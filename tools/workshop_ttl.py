#!/usr/bin/env python3
"""Versioned TTL contracts and fenced activity control; offline preparation CLI."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import time
import uuid

import workshop_cleanup as cleanup
import workshop_config as wc

HASH = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
MODULE_KEYS = {"pilot": "terraform.tfstate", "access-bindings": "access.tfstate"}
RUNTIME_FILES = {"runtime.json", "source.tar", "install_host.sh", "host.py", "kernel.py", "check.py", "serve.py", "portal.py",
    "doris-workshop-jupyter.service", "packages.json", "operations.py", "server.requirements.lock",
    "doris-rta.requirements.lock", "doris-dw.requirements.lock", "doris-obs.requirements.lock", "doris-ai.requirements.lock"}
BUSY = {"operating", "cleaning", "unknown"}


class Conflict(wc.ConfigError):
    pass


def require(condition, message):
    if not condition:
        raise wc.ConfigError("ttl: " + message)


def utc(value):
    return wc.utc_text(wc.timestamp(value, "expires_at"))


def control_key(target):
    return "/".join(["aws-workshop-control", target["aws_account_id"], target["region"], target["event_id"], "control.json"])


def validate_binding(binding):
    require(set(binding) == {"schema_version", "kind", "target", "revision", "expires_at", "source_sha", "source_tree_sha256",
        "control", "modules", "cleanup_manifest_sha256", "limits", "runtime_artifacts"} and binding["schema_version"] == 1 and
        type(binding["schema_version"]) is int and binding["kind"] == "aws-workshop-ttl-binding", "invalid binding fields")
    t = binding["target"]
    require(set(t) == {"aws_account_id", "region", "event_id", "owner_id", "domain"} and
        re.fullmatch(r"[0-9]{12}", t["aws_account_id"]) and re.fullmatch(r"[a-z]{2}-[a-z-]+-\d", t["region"]) and
        ID.fullmatch(t["event_id"]) and ID.fullmatch(t["owner_id"]) and isinstance(t["domain"], str) and
        re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", t["domain"]), "invalid activity target")
    require(type(binding["revision"]) is int and binding["revision"] >= 1 and binding["expires_at"] == utc(binding["expires_at"]),
        "revision must be positive and expiry must use canonical UTC")
    require(re.fullmatch(r"[0-9a-f]{40}", binding["source_sha"]) and HASH.fullmatch(binding["source_tree_sha256"]) and
        HASH.fullmatch(binding["cleanup_manifest_sha256"]), "pin source and cleanup manifest hashes")
    control = binding["control"]
    require(set(control) == {"bucket", "key"} and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", control["bucket"]) and
        control["key"] == control_key(t), "invalid fixed control object")
    prefix = f'aws-workshop/aws/{t["aws_account_id"]}/{t["region"]}/{t["event_id"]}/'
    modules = binding["modules"]
    require(isinstance(modules, list) and 0 < len(modules) <= 2 and len({row["module"] for row in modules}) == len(modules),
        "use one binding per supported remote activity state")
    require([row["module"] for row in modules] == [name for name in ("access-bindings", "pilot") if any(row["module"] == name for row in modules)],
        "access bindings must be cleaned before learner hosts")
    for row in modules:
        require(set(row) == {"module", "backend", "vars_sha256", "module_sha256"} and row["module"] in MODULE_KEYS and
            HASH.fullmatch(row["vars_sha256"]) and HASH.fullmatch(row["module_sha256"]), "unsupported module or unbound inputs")
        backend = row["backend"]
        require(backend == {"bucket": control["bucket"], "key": prefix + MODULE_KEYS[row["module"]], "region": t["region"],
            "encrypt": True, "use_lockfile": True, "allowed_account_ids": [t["aws_account_id"]]}, "backend is not this activity's canonical locked state")
    limits = binding["limits"]
    require(set(limits) == {"max_attempts", "task_timeout_seconds", "lock_wait_seconds"} and
        all(type(value) is int for value in limits.values()) and 1 <= limits["max_attempts"] <= 3 and
        60 <= limits["task_timeout_seconds"] <= 7200 and 1 <= limits["lock_wait_seconds"] <= 120, "invalid finite execution limits")
    artifacts = binding["runtime_artifacts"]
    require(isinstance(artifacts, list) and len({row["name"] for row in artifacts}) == len(artifacts) and all(
        set(row) == {"name", "sha256", "size"} and row["name"] in RUNTIME_FILES and HASH.fullmatch(row["sha256"]) and
        type(row["size"]) is int and 0 <= row["size"] <= (1024 ** 3 if row["name"] == "source.tar" else 4 * 1024 ** 2) for row in artifacts),
        "only bounded installer artifacts may enter the cleanup workspace")
    return wc.canonical_hash(binding)


def make_binding(config, manifest, modules, *, bucket, revision, source_sha, tree_sha256, runtime_artifacts=()):
    cleanup.validate_manifest(manifest)
    require(cleanup.target(config) == manifest["target"] and {row["module"] for row in modules} == set(manifest["modules"]),
        "cleanup scope differs from the activity modules")
    value = {"schema_version": 1, "kind": "aws-workshop-ttl-binding", "target": manifest["target"], "revision": revision,
        "expires_at": utc(config["expires_at"]), "source_sha": source_sha, "source_tree_sha256": tree_sha256,
        "control": {"bucket": bucket, "key": control_key(manifest["target"])},
        "modules": sorted(modules, key=lambda row: ("access-bindings", "pilot").index(row["module"]) if row["module"] in MODULE_KEYS else 99),
        "cleanup_manifest_sha256": wc.canonical_hash(manifest), "limits": {"max_attempts": 3, "task_timeout_seconds": 3600, "lock_wait_seconds": 60},
        "runtime_artifacts": list(runtime_artifacts)}
    validate_binding(value)
    return value


def trigger(binding):
    return {"schema_version": 1, "event_id": binding["target"]["event_id"], "revision": binding["revision"], "binding_sha256": validate_binding(binding)}


def schedule(binding, *, now=None):
    validate_binding(binding)
    now = now or datetime.now(timezone.utc)
    expiry = wc.timestamp(binding["expires_at"], "expires_at")
    require(expiry > now, "already expired; use the separately authorized due runner instead of creating a past schedule")
    scheduled = expiry.replace(second=0, microsecond=0) + (timedelta(minutes=1) if expiry.second else timedelta())
    return {"schedule_expression": "at(" + wc.utc_text(scheduled)[:-1] + ")", "schedule_expression_timezone": "UTC",
        "flexible_time_window": {"mode": "OFF"}, "action_after_completion": "DELETE", "payload": trigger(binding),
        "maximum_event_age_in_seconds": 3600, "maximum_retry_attempts": 2, "trigger_at": wc.utc_text(scheduled)}


def initial_record(binding):
    return {"schema_version": 1, "target": binding["target"], "revision": binding["revision"], "binding_sha256": validate_binding(binding),
        "expires_at": binding["expires_at"], "state": "active", "holder": None, "attempts": 0, "result": None}


def check_record(record, binding):
    require(set(record) == {"schema_version", "target", "revision", "binding_sha256", "expires_at", "state", "holder", "attempts", "result"} and
        type(record["schema_version"]) is int and record["schema_version"] == 1 and record["target"] == binding["target"] and type(record["revision"]) is int and
        HASH.fullmatch(record["binding_sha256"]) and record["expires_at"] == utc(record["expires_at"]) and
        record["state"] in {"active", "cancelled", "operating", "cleaning", "unknown", "failed", "complete"} and
        type(record["attempts"]) is int and record["attempts"] >= 0, "invalid or foreign control record")
    if record["binding_sha256"] == validate_binding(binding) and record["revision"] == binding["revision"]:
        require(record["expires_at"] == binding["expires_at"], "control expiry differs from the reviewed binding")


class S3ControlStore:
    """One CAS record, no expiring lease or automatic lock stealing."""
    def __init__(self, client, bucket, expected_account_id):
        self.client, self.bucket, self.expected_account_id = client, bucket, expected_account_id
        require(re.fullmatch(r"[0-9]{12}", expected_account_id), "explicit bucket-owner account is required")

    def read(self, key):
        try:
            value = self.client.get_object(Bucket=self.bucket, Key=key, ExpectedBucketOwner=self.account)
        except self.client.exceptions.NoSuchKey:
            return None, None
        with value["Body"] as body:
            raw = body.read(65537)
        require(len(raw) <= 65536 and value.get("ETag"), "invalid control object")
        return json.loads(raw), value["ETag"]

    def compare_set(self, key, expected, value):
        from botocore.exceptions import ClientError
        parameters = {"Bucket": self.bucket, "Key": key, "Body": json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(), "ContentType": "application/json",
            "ServerSideEncryption": "AES256", "ExpectedBucketOwner": self.account}
        parameters.update({"IfNoneMatch": "*"} if expected is None else {"IfMatch": expected})
        try:
            return self.client.put_object(**parameters)["ETag"]
        except ClientError as exc:
            # These S3 conditional errors have no generated typed SDK exception.
            if exc.response.get("Error", {}).get("Code") not in {"PreconditionFailed", "ConditionalRequestConflict"}:
                raise
            raise Conflict("ttl: control changed or another operation holds the activity") from exc

    @property
    def account(self):
        return self.expected_account_id


def register(store, binding, *, expected_revision=None):
    validate_binding(binding)
    key = binding["control"]["key"]
    current, etag = store.read(key)
    if current is None:
        require(expected_revision is None and binding["revision"] == 1, "first registration requires revision 1")
    else:
        check_record(current, binding)
        require(current["state"] not in BUSY and current["state"] != "complete", "cannot renew while operating, unknown or already cleaned")
        require(type(expected_revision) is int and current["revision"] == expected_revision and binding["revision"] == expected_revision + 1, "renewal must advance exactly one reviewed revision")
    return store.compare_set(key, etag, initial_record(binding))


def cancel(store, binding):
    digest = validate_binding(binding)
    key = binding["control"]["key"]
    current, etag = store.read(key)
    require(current is not None, "control record is missing")
    check_record(current, binding)
    require(current["revision"] == binding["revision"] and current["binding_sha256"] == digest and current["state"] in {"active", "failed", "cancelled"},
        "cannot cancel a different revision or running operation")
    return store.compare_set(key, etag, {**current, "state": "cancelled", "holder": None, "result": "cancelled"})


@contextmanager
def operation(store, binding, *, holder, now=None, cleanup_operation=False):
    """Operator up/destroy/replace use the same fence as TTL work."""
    digest = validate_binding(binding)
    key = binding["control"]["key"]
    current, etag = store.read(key)
    require(current is not None, "registered activity control is missing")
    check_record(current, binding)
    require(current["state"] in {"active", "cancelled"} and current["binding_sha256"] == digest and current["revision"] == binding["revision"], "activity busy, stale or already cleaned")
    require(cleanup_operation or current["state"] == "cancelled" or wc.timestamp(current["expires_at"], "expires_at") >
        (now or datetime.now(timezone.utc)), "activity expired; only cleanup is permitted")
    claimed = {**current, "state": "operating", "holder": holder}
    owned_etag = store.compare_set(key, etag, claimed)
    try:
        yield
    except BaseException:
        # The caller must reconcile effects before an operator releases this.
        store.compare_set(key, owned_etag, {**claimed, "state": "unknown", "result": "operator-outcome-unknown"})
        raise
    else:
        store.compare_set(key, owned_etag, current)


@contextmanager
def activity_operation(cloud, config, pilot, binding_file=None, *, cleanup_operation=False, scope_change=False):
    """An explicit missing object permits legacy runs; read failures do not."""
    caller = cloud.call("sts", "get_caller_identity")
    require(caller["Account"] == config["aws_account_id"] and caller["Arn"].startswith("arn:aws:"), "operator identity differs from activity target")
    store = cloud.control_store(pilot["state_bucket_name"], config["aws_account_id"])
    identity = cleanup.target(config)
    current, _ = store.read(control_key(identity))
    if current is None:
        require(binding_file is None, "TTL binding supplied without a registered control record")
        yield
        return
    require(binding_file is not None, "registered activity requires --ttl-binding; an older run cannot bypass the fence")
    binding = wc.load_document(binding_file)
    require(binding["target"] == identity and binding["control"]["bucket"] == pilot["state_bucket_name"], "operator binding target/backend differs")
    if scope_change:
        require(current["state"] == "cancelled", "cancel TTL before changing the activity, then reseal a new revision")
    if current["state"] == "active" and not cleanup_operation:
        require(utc(config["expires_at"]) == binding["expires_at"], "cancel TTL before changing expiry")
    with operation(store, binding, holder="operator-" + uuid.uuid4().hex, cleanup_operation=cleanup_operation):
        yield


def execute(binding, event, store, adapter, *, now=None, holder=None, clock=time.monotonic):
    """Adapter owns fixed Terraform commands and independent final reconciliation."""
    digest = validate_binding(binding)
    require(set(event) == {"schema_version", "event_id", "revision", "binding_sha256"} and type(event["schema_version"]) is int and event["schema_version"] == 1 and
        type(event["revision"]) is int and HASH.fullmatch(event["binding_sha256"]), "trigger contains unsupported commands or selectors")
    require(event["event_id"] == binding["target"]["event_id"], "trigger belongs to another activity")
    key = binding["control"]["key"]
    current, etag = store.read(key)
    require(current is not None, "control record missing; no destructive work started")
    check_record(current, binding)
    if event != trigger(binding) or current["revision"] != binding["revision"] or current["binding_sha256"] != digest:
        return {"outcome": "stale", "deleted": False}
    if current["state"] in {"cancelled", "complete"}:
        return {"outcome": current["state"], "deleted": False}
    if wc.timestamp(current["expires_at"], "expires_at") > (now or datetime.now(timezone.utc)):
        return {"outcome": "not-due", "deleted": False}
    if current["state"] in {"operating", "cleaning"}:
        raise Conflict("ttl: activity is fenced by outstanding work")
    require(current["state"] != "unknown", "activity outcome unknown; manual reconciliation is required")
    require(current["state"] in {"active", "failed"} and current["attempts"] < binding["limits"]["max_attempts"], "retry budget exhausted")
    adapter.verify(binding)  # Identity, pinned source, inputs and explicit grant.
    claimed = {**current, "state": "cleaning", "holder": holder or uuid.uuid4().hex, "attempts": current["attempts"] + 1, "result": None}
    owned_etag = store.compare_set(key, etag, claimed)
    mutation_started = False
    deadline = clock() + binding["limits"]["task_timeout_seconds"]
    try:
        plans = []
        for module in binding["modules"]:
            require(clock() < deadline, "task deadline reached before planning")
            plan = adapter.plan(module, lock_wait_seconds=binding["limits"]["lock_wait_seconds"], timeout=max(1, int(deadline - clock())))
            adapter.validate(module, plan)
            plans.append((module, plan))
        # Validate every module before deleting even the first route or host.
        for module, plan in plans:
            require(clock() < deadline, "task deadline reached before apply")
            mutation_started = True
            adapter.apply(module, plan, timeout=max(1, int(deadline - clock())))
        require(clock() < deadline, "task deadline reached before reconciliation")
        report = adapter.reconcile()
        require(clock() < deadline, "task deadline reached during reconciliation")
        require(report.get("ok") is True and report.get("account_globally_empty") is False, "residual resources or incomplete cleanup evidence")
        store.compare_set(key, owned_etag, {**claimed, "state": "complete", "holder": None, "result": "reconciled"})
        return {"outcome": "complete", "deleted": mutation_started, "reconciled": True}
    except BaseException:
        state = "unknown" if mutation_started else "failed"
        store.compare_set(key, owned_etag, {**claimed, "state": state, "result": "apply-or-reconciliation-unknown" if mutation_started else "failed-before-apply"})
        raise


def runtime_bundle_artifacts(directory):
    """Bind exactly the files required by the runtime manifest, including lockfiles."""
    import hashlib
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "runtime bundle must be a directory")
    paths = sorted(directory.iterdir())
    require(all(path.name in RUNTIME_FILES and path.is_file() and not path.is_symlink() for path in paths),
            "runtime bundle contains unapproved data or symlinks")
    names = {path.name for path in paths}
    require("runtime.json" in names, "runtime manifest is required")
    runtime = wc.load_document(directory / "runtime.json")
    require(isinstance(runtime, dict) and isinstance(runtime.get("sha256"), dict), "invalid runtime manifest hashes")
    expected = runtime["sha256"]
    require(all(isinstance(name, str) and name in RUNTIME_FILES and name != "runtime.json" and
                isinstance(digest, str) and HASH.fullmatch(digest) for name, digest in expected.items()) and
            names == set(expected) | {"runtime.json"}, "runtime bundle must contain exactly its declared files")
    artifacts = []
    for path in paths:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        require(path.name == "runtime.json" or digest == expected[path.name], "runtime artifact hash differs from manifest")
        artifacts.append({"name": path.name, "sha256": digest, "size": path.stat().st_size})
    return artifacts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("prepare")
    make.add_argument("--config", type=Path, required=True)
    make.add_argument("--cleanup-manifest", type=Path, required=True)
    make.add_argument("--modules", type=Path, required=True)
    make.add_argument("--bucket", required=True)
    make.add_argument("--revision", type=int, required=True)
    make.add_argument("--source-sha", required=True)
    make.add_argument("--source-tree-sha256", required=True)
    make.add_argument("--runtime-bundle", type=Path)
    make.add_argument("--output", type=Path, required=True)
    for name in ("register", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--binding", type=Path, required=True)
        command.add_argument("--approve-binding-sha256", required=True)
        if name == "register":
            command.add_argument("--expected-revision", type=int)
    args = parser.parse_args(argv)
    try:
        config = wc.load_document(args.config)
        wc.schema_check(config, "config.schema.json")
        if args.command == "prepare":
            artifacts = []
            if args.runtime_bundle:
                artifacts = runtime_bundle_artifacts(args.runtime_bundle)
            binding = make_binding(config, wc.load_document(args.cleanup_manifest), wc.load_document(args.modules), bucket=args.bucket,
                revision=args.revision, source_sha=args.source_sha, tree_sha256=args.source_tree_sha256, runtime_artifacts=artifacts)
            scheduled = schedule(binding)
            wc.write_manifest(args.output, binding)
            wc.write_manifest(args.output.with_suffix(".schedule.json"), scheduled)
            print("TTL binding prepared; no schedule, policy or executor was deployed.")
        else:
            import workshop_preflight as pf
            binding = wc.load_document(args.binding)
            require(binding["target"] == cleanup.target(config) and validate_binding(binding) == args.approve_binding_sha256,
                "review the exact binding for this explicit target")
            cloud = pf.Aws(config)
            who = cloud.call("sts", "get_caller_identity")
            require(who["Account"] == config["aws_account_id"] and who["Arn"].startswith("arn:aws:"), "operator identity differs from binding")
            store = S3ControlStore(cloud.client("s3"), binding["control"]["bucket"], config["aws_account_id"])
            if args.command == "register":
                register(store, binding, expected_revision=args.expected_revision)
            else:
                cancel(store, binding)
            print("Control record updated; no schedule or activity resource was changed.")
        return 0
    except Exception:
        print("ttl: operation failed; inspect private inputs/control state before retrying; no unbound deletion was attempted", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
