#!/usr/bin/env python3
"""Plan, bring up, inspect and destroy an SSM-only AWS workshop activity."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import workshop_config as wc
import workshop_preflight as pf
import workshop_operations as ops
import workshop_batch as batch
import workshop_cleanup as cleanup
import workshop_ttl as ttl

IN_FLIGHT = {"Pending", "InProgress", "Delayed", "Cancelling", "DispatchUnknown"}

_PROBE_PREFIX = ("runuser --user workshop -- env -i HOME=/var/lib/doris-workshop/home "
         "PATH=/opt/doris-workshop/server-venv/bin:/usr/local/bin:/usr/bin:/bin "
         "LANG=C.UTF-8 PYTHONNOUSERSITE=1 /opt/doris-workshop/server-venv/bin/python "
         "-I ")
PROBE = ("if test -f /usr/local/lib/doris-workshop/operations.py; then " + _PROBE_PREFIX +
         "/usr/local/lib/doris-workshop/operations.py probe; else " + _PROBE_PREFIX +
         "/usr/local/lib/doris-workshop/check.py; fi")
IMAGE_STARTUP = ["set -eu", "timeout 300 cloud-init status --wait >/dev/null",
    "deadline=$(($(date +%s)+120)); until timeout 20 runuser --user workshop -- docker info >/dev/null 2>&1; do "
    "test $(date +%s) -lt $deadline || exit 1; sleep 2; done"]
RESOURCE_NAMES = {
    "aws_vpc": "pilot", "aws_internet_gateway": "pilot", "aws_subnet": "pilot",
    "aws_route_table": "pilot", "aws_route_table_association": "pilot", "aws_security_group": "pilot",
    "aws_s3_bucket": "runtime", "aws_s3_bucket_public_access_block": "runtime",
    "aws_s3_bucket_server_side_encryption_configuration": "runtime", "aws_s3_bucket_policy": "runtime",
    "aws_s3_object": "runtime", "aws_iam_role": "learner", "aws_iam_role_policy": "learner",
    "aws_iam_instance_profile": "learner", "aws_instance": "learner", "aws_ssm_document": "install",
}


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def run_lock(directory):
    with (Path(directory) / "operation.lock").open("a") as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise wc.ConfigError("run: another operation holds this run directory") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Terraform:
    def __init__(self, directory, timeout=1800):
        self.directory, self.timeout = Path(directory).resolve(), timeout

    def execute(self, args, env, *, capture=False):
        # Successful machine-readable output (including state) stays in memory.
        # Human diagnostics stay private and never enter the ordinary CLI output.
        try:
            result = subprocess.run(["terraform", *map(str, args)], env=env, text=True,
                                    capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise wc.ConfigError("terraform: unavailable or timed out; resources may remain, run status/destroy") from exc
        if not capture or result.returncode:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            log = self.directory / f"terraform-{stamp}.log"
            descriptor = os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(ops.safe_terraform_log(result.returncode))
            ops.prune_local(self.directory)
        pf.require(result.returncode == 0, "terraform: failed; inspect private terraform logs and run status/destroy")
        return result.stdout if capture else None

    def run(self, args, *, capture=False):
        env = pf.explicit_environment()
        env["TF_DATA_DIR"] = str(self.directory / "terraform-data")
        return self.execute([f"-chdir={pf.MODULES / 'pilot'}", *args], env, capture=capture)

    def state(self):
        state = json.loads(self.run(["show", "-json"], capture=True))
        values = state.get("values", {})
        root = values.get("root_module", {})
        pf.require(not root.get("child_modules"), "ownership: unexpected nested Terraform module")
        return [r for r in root.get("resources", []) if r.get("mode") == "managed"]


class Cloud(pf.Aws):
    """Explicit-profile reads and bounded SSM startup, verification and health checks."""

    def control_store(self, bucket, account):
        return ttl.S3ControlStore(self.client("s3"), bucket, account)

    def dispatch_client(self):
        # SendCommand has no idempotency token. Retrying a lost HTTP response
        # could run an installer twice; only an operator may resolve that case.
        if not hasattr(self, "_ssm_dispatch_client"):
            from botocore.config import Config
            self._ssm_dispatch_client = self.session.client("ssm", config=self.options.merge(
                Config(retries={"mode": "standard", "total_max_attempts": 1})))
        return self._ssm_dispatch_client

    def send(self, instance, document, *, probe=False, startup=False):
        options = {"Parameters": {"commands": [PROBE], "executionTimeout": ["300"]}} if probe else (
            {"Parameters": {"commands": IMAGE_STARTUP, "executionTimeout": ["600"]}} if startup else {})
        return self.dispatch_client().send_command(InstanceIds=[instance], DocumentName=document,
                                                TimeoutSeconds=600, **options)["Command"]["CommandId"]

    def command(self, command_id, instance, *, probe=False):
        client = self.client("ssm")
        if probe:
            try:
                return client.get_command_invocation(CommandId=command_id, InstanceId=instance)
            except client.exceptions.InvocationDoesNotExist:
                return {"Status": "Pending"}
        # Installation has download and install steps. Inspect the aggregate
        # invocation, so a download failure cannot masquerade as a pending install.
        rows = client.list_command_invocations(CommandId=command_id, InstanceId=instance, Details=False)["CommandInvocations"]
        return rows[0] if rows else {"Status": "Pending"}


class Controller:
    def __init__(self, directory, *, cloud=None, terraform=None, clock=time.monotonic, sleep=time.sleep, ttl_binding=None):
        self.directory = Path(directory).resolve()
        self.ttl_binding = ttl_binding
        self.binding = wc.load_document(self.directory / "binding.json")
        b = self.binding
        pf.require(b.get("stage") == "pilot", "run: only activity runs are supported; foundation is retained")
        self.config, self.pilot = b["config"], b["pilot"]
        wc.schema_check(self.config, "config.schema.json")
        self.allocations = {key: {"role": "learner", "active": True} for key in self.config["learner_ids"]}
        if b.get("allocations_sha256"):
            pf.require(pf.file_hash(self.directory / "allocations.json") == b["allocations_sha256"], "ownership: allocation manifest changed")
            manifest = wc.load_document(self.directory / "allocations.json")
            batch.validate_manifest(manifest, self.config)
            self.allocations = manifest["allocations"]
        # Expiry and live prices deliberately do not block status or cleanup.
        for name, key in (("inputs.tfvars.json", "vars_sha256"), ("backend.tfbackend.json", "backend_sha256")):
            pf.require(pf.file_hash(self.directory / name) == b[key], "ownership: bound variables or backend changed")
        backend = wc.load_document(self.directory / "backend.tfbackend.json")
        expected_key = f"aws-workshop/aws/{self.config['aws_account_id']}/{self.config['region']}/{self.config['event_id']}/terraform.tfstate"
        pf.require(backend["bucket"] == self.pilot["state_bucket_name"] and backend["key"] == expected_key
                   and backend["region"] == self.config["region"] and backend["profile"] == self.config["aws_profile"]
                   and backend["allowed_account_ids"] == [self.config["aws_account_id"]]
                   and backend.get("encrypt") is True and backend.get("use_lockfile") is True,
                   "ownership: activity backend does not match the explicit target")
        variables = wc.load_document(self.directory / "inputs.tfvars.json")
        expected = pf.inputs_for_terraform(self.config, self.pilot, "pilot", variables.get("availability_zone"),
            release=b.get("image_release"), browser_routes=b.get("browser_routes"), shared_access=b.get("shared_access"))
        pf.require(variables == expected and pf.module_hash("pilot") == b["module_sha256"],
                   "ownership: module or activity inputs changed; use the original deployment checkout")
        if self.config['image']['mode'] == 'prebuilt':
            pf.require(pf.file_hash(wc.SPEC/'runtime/start_image.py') == b['image_initializer_sha256'],
                       'ownership: image initializer changed; use the original deployment checkout')
        self.cloud = cloud or Cloud(self.config)
        identity = self.cloud.call("sts", "get_caller_identity")
        pf.require(identity["Account"] == self.config["aws_account_id"] and
                   identity["Arn"].startswith(f"arn:{self.config['aws_partition']}:"), "identity: unexpected account or partition")
        self.tf = terraform or Terraform(self.directory)
        self.clock, self.sleep = clock, sleep
        self.path = self.directory / "lifecycle.json"
        self.record = wc.load_document(self.path) if self.path.exists() else {
            "schema_version": 1, "event_id": self.config["event_id"], "phase": "planned", "nodes": {},
            "known_instances": [], "known_volumes": [],
        }
        pf.require(self.record.get("event_id") == self.config["event_id"], "ownership: lifecycle event mismatch")
        if b.get("resume_sha256"):
            pf.require(pf.file_hash(self.directory / "resume.json") == b["resume_sha256"] and
                pf.file_hash(self.directory / "roster-review.json") == b["roster_review_sha256"], "ownership: roster review or resume snapshot changed")
            if not self.path.exists():
                previous = wc.load_document(self.directory / "resume.json")
                pf.require(previous.get("event_id") == self.config["event_id"] and previous.get("phase") == "ready",
                    "roster: resume only a ready activity")
                for key in ("nodes", "known_instances", "known_volumes"):
                    self.record[key] = previous[key]
                self.record["known_network_interfaces"] = previous.get("known_network_interfaces", {})
        self.concurrency = self.record.get("batch_concurrency", 2)

    def save(self):
        self.record["updated_at"] = wc.utc_text(datetime.now(timezone.utc))
        atomic_json(self.path, self.record)

    def owned_resources(self):
        resources = self.tf.state()
        cleanup.project_state({**self.config, "cleanup_learner_ids": [key for key, row in self.allocations.items() if row["role"] == "learner"]},
            resources, state_bucket=self.pilot["state_bucket_name"])
        return resources

    def tags(self):
        return {"Project": "doris-workshop", "Event": self.config["event_id"], "Owner": self.config["owner_id"], "Scope": "activity"}

    def inventory(self, *, resources=None):
        resources = self.owned_resources() if resources is None else resources
        instances, document = {}, None
        for row in resources:
            values = row["values"]
            if row["type"] == "aws_instance":
                learner = row.get("index")
                pf.require(learner in self.allocations and self.allocations[learner]["role"] == "learner" and values.get("tags", {}).get("Learner") == learner,
                           "ownership: unknown learner instance")
                instances[learner] = values["id"]
                self.record["known_instances"] = sorted(set(self.record["known_instances"]) | {values["id"]})
                volumes = [v["volume_id"] for v in values.get("root_block_device", []) if v.get("volume_id")]
                self.record["known_volumes"] = sorted(set(self.record["known_volumes"]) | set(volumes))
            elif row["type"] == "aws_ssm_document":
                document = values["name"]
        self.record["managed_resources"] = len(resources)
        self.save()
        return instances, document

    def actual_instances(self, instance_ids):
        if not instance_ids:
            return {}
        reservations = self.cloud.items("ec2", "describe_instances", "Reservations", Filters=[{"Name": "instance-id", "Values": sorted(instance_ids)}])
        instances = {i["InstanceId"]: i for r in reservations for i in r["Instances"]}
        for instance in instances.values():
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            pf.require(all(tags.get(k) == v for k, v in self.tags().items()), "ownership: actual EC2 tags do not match")
            pf.require(tags.get("Learner") in self.allocations and self.allocations[tags["Learner"]]["role"] == "learner", "ownership: actual EC2 learner mismatch")
            for interface in instance.get("NetworkInterfaces", []):
                self.record.setdefault("known_network_interfaces", {})[interface["NetworkInterfaceId"]] = instance["InstanceId"]
        return instances

    def poll_command(self, node, kind, document, deadline, *, retry_failed=False):
        entry = node.setdefault(kind, {"attempts": 0})
        pf.require(entry.get("status") != "DispatchUnknown",
                   "ssm: command dispatch outcome unknown; inspect SSM command history before retrying; no command resent")
        if entry.get("status") == "Success":
            return True
        if entry.get("status") in {"Failed", "Cancelled", "TimedOut"}:
            if not retry_failed or entry["attempts"] >= 3:
                raise wc.ConfigError(f"ssm: {kind} failed; inspect target logs, then use --retry-failed (at most 3 attempts)")
            entry.pop("command_id", None)
        if not entry.get("command_id"):
            if self.clock() >= deadline:
                return False
            if self.inflight() >= self.concurrency or any(row.get("status") == "DispatchUnknown"
                    for current in self.record["nodes"].values() for row in self.commands(current)):
                entry["status"] = "Queued"
                node["waiting_for_slot"] = True
                return False
            node.pop("waiting_for_slot", None)
            entry["attempts"] += 1
            entry["status"] = "DispatchUnknown"
            self.save()
            options = {"probe": kind == "probe"}
            if kind == "startup": options["startup"] = True
            entry["command_id"] = self.cloud.send(node["instance_id"], document, **options)
            entry["status"] = "Pending"
            self.save()
        result = self.cloud.command(entry["command_id"], node["instance_id"], probe=kind == "probe")
        status = result["Status"]
        pf.require(status in {"Pending", "InProgress", "Delayed", "Success", "Failed", "Cancelled", "Cancelling", "TimedOut"},
                   "ssm: unknown command status")
        entry["status"] = status
        if status in {"Success", "Failed"} and kind == "probe":
            try:
                report = json.loads(result.get("StandardOutputContent", ""))
                checks = report["checks"]
                required = {"target-platform", "learner-identity", "source-version", "jupyter-systemd",
                            "jupyter-authenticated-loopback", "course-entry", "docker-daemon-and-permission", "compose"}
                for course in wc.load_document(Path(self.pilot["runtime_bundle_dir"]) / "runtime.json")["courses"]:
                    required.update({course["kernel"] + ":interpreter-imports-cwd-restart-docker",
                                     course["kernel"] + ":notebook-bindings"})
                valid = (status == "Success" and report.get("scope") == "host-runtime" and report.get("ready") is True
                         and report.get("course_ref") == self.config["course_ref"]
                         and report.get("runtime_sha256") == self.binding["runtime_sha256"]
                         and all(type(r.get("passed")) is bool and r["passed"] for r in checks)
                         and required.issubset(r["check"] for r in checks))
                entry["ready"] = valid
                entry["checks"] = [{"check": r["check"], "passed": r["passed"]} for r in checks
                                   if r["check"] in ops.node.CHECKS and type(r["passed"]) is bool]
                entry["services"] = [{"name": family, "state": r["state"]} for r in report.get("services", [])
                                     for family in ("doris-rta", "doris-warehousing-course", "doris-obs", "doris-ai")
                                     if isinstance(r.get("name"), str) and r["name"].startswith(family)
                                     and r.get("state") in {"running", "created", "restarting", "removing", "paused", "exited", "dead"}]
                if not valid:
                    entry["status"] = "Failed"
            except (ValueError, KeyError, TypeError, AttributeError):
                entry["status"], entry["ready"] = "Failed", False
        self.save()
        if entry["status"] in {"Failed", "Cancelled", "TimedOut"}:
            raise wc.ConfigError(f"ssm: {kind} failed; target logs: /var/log/doris-workshop; resources remain")
        return status == "Success"

    @staticmethod
    def commands(node):
        return [node[kind] for kind in ("startup", "install", "probe") if kind in node]

    def inflight(self):
        return sum(row.get("status") in IN_FLIGHT for node in self.record["nodes"].values() for row in self.commands(node))

    def inspect(self, *, install=False, timeout=900, retry_failed=False, concurrency=2):
        pf.require(type(concurrency) is int and 1 <= concurrency <= 16, "batch: concurrency must be an integer from 1 to 16")
        self.concurrency = concurrency
        self.record["batch_concurrency"] = concurrency
        began = self.clock()
        deadline = self.clock() + timeout
        instances, document = self.inventory()
        if install:
            pf.require(set(instances) == set(self.config["learner_ids"]) and document,
                       "apply: incomplete activity state; resources may remain, create a fresh reviewed plan or destroy")
        for learner, instance in instances.items():
            if self.record["nodes"].get(learner, {}).get("instance_id") != instance:
                self.record["nodes"][learner] = {"instance_id": instance, "ready": False}
        # Every status command starts a fresh remote probe, never reports an old
        # successful probe as current health. Up preserves pending command IDs.
        if not install:
            for node in self.record["nodes"].values():
                if node.get("probe", {}).get("status") in {"Success", "Failed", "Cancelled", "TimedOut"}:
                    node.pop("probe", None)
        for learner, node in self.record["nodes"].items():
            node["ready"] = False
            node["managed"] = instances.get(learner) == node["instance_id"]
        while True:
            actual = self.actual_instances(set(instances.values()) | set(self.record["known_instances"]))
            for node in self.record["nodes"].values():
                if not node["managed"]:
                    node["ec2"] = actual.get(node["instance_id"], {}).get("State", {}).get("Name", "missing")
            self.record["untracked_instances"] = sorted(i for i, row in actual.items()
                                                        if i not in instances.values() and row.get("State", {}).get("Name") != "terminated")
            all_ready = bool(instances) and set(instances) == set(self.config["learner_ids"]) and not self.record["untracked_instances"]
            failed = bool(self.record["untracked_instances"])
            for learner, instance in sorted(instances.items()):
                old = self.record["nodes"].get(learner, {})
                node = old if old.get("instance_id") == instance else {"instance_id": instance}
                self.record["nodes"][learner] = node
                node["managed"] = True
                node["ec2"] = actual.get(instance, {}).get("State", {}).get("Name", "missing")
                if instance in actual:
                    tags = {t["Key"]: t["Value"] for t in actual[instance].get("Tags", [])}
                    pf.require(tags.get("Learner") == learner, "ownership: actual EC2 assignment differs from state")
                node["ready"] = False
                node.pop("waiting_for_slot", None)
                node.pop("error", None)
                if node["ec2"] != "running":
                    all_ready = False
                    continue
                try:
                    rows = self.cloud.call("ssm", "describe_instance_information",
                                           Filters=[{"Key": "InstanceIds", "Values": [instance]}])["InstanceInformationList"]
                    node["ssm"] = rows[0].get("PingStatus", "Offline") if rows else "Offline"
                    if node["ssm"] != "Online":
                        all_ready = False
                        continue
                    if not install:
                        # Status may finish polling an earlier dispatch, but
                        # cannot resend an installer or leave its occupied slot
                        # stuck forever after the remote command has completed.
                        outstanding = False
                        for kind in ("startup", "install"):
                            entry = node.get(kind, {})
                            if entry.get("status") in IN_FLIGHT:
                                outstanding = not self.poll_command(node, kind,
                                    "AWS-RunShellScript" if kind == "startup" else document, deadline)
                                if outstanding:
                                    break
                        if outstanding:
                            all_ready = False
                            continue
                    if install and self.config['image']['mode'] == 'prebuilt' and not self.poll_command(
                            node, 'startup', 'AWS-RunShellScript', deadline, retry_failed=retry_failed):
                        all_ready = False
                        continue
                    if install and not self.poll_command(node, "install", document, deadline, retry_failed=retry_failed):
                        all_ready = False
                        continue
                    if self.poll_command(node, "probe", "AWS-RunShellScript", deadline, retry_failed=retry_failed):
                        node["ready"] = node["probe"].get("ready", False)
                    node.pop("error", None)
                except Exception as exc:
                    node["error"] = str(exc) if isinstance(exc, wc.ConfigError) else "batch: query or dispatch failed; reconcile this node before retrying"
                    failed = True
                all_ready &= node["ready"]
            self.save()
            if all_ready or failed or not instances or not install and all(n.get("ec2") in {"missing", "terminated", "stopped"}
                                                                 for n in self.record["nodes"].values()):
                self.record["last_health_check"] = {"seconds": round(self.clock()-began,2), "ready": bool(all_ready),
                    "checked_at": wc.utc_text(datetime.now(timezone.utc)), "boundary": "ready" if all_ready else "failed-or-unavailable"}
                self.save()
                return self.summary(all_ready)
            if self.clock() >= deadline:
                self.record["last_error"] = "wait-timeout; resources may remain; rerun status/up or destroy"
                self.record["last_health_check"] = {"seconds": round(self.clock()-began,2), "ready": False,
                    "checked_at": wc.utc_text(datetime.now(timezone.utc)), "boundary": "timeout"}
                self.save()
                raise wc.ConfigError("wait: timed out; command IDs and resource ownership retained for status/up/destroy")
            self.sleep(min(5, max(0, deadline - self.clock())))

    def summary(self, ready=False):
        current = {key: self.record["nodes"].get(key, {}) for key in self.config["learner_ids"]}
        ready_ids = sorted(key for key, node in current.items() if node.get("ready") and node.get("managed"))
        failed_ids = sorted(key for key, node in current.items() if node.get("error"))
        shared = self.binding.get("shared_access")
        entry = {"transport": "operator-ssm-forward", "remote_port": 8888, "path": "/workshop/"}
        if shared:
            routes = self.binding.get("browser_routes", {})
            entry = {"transport": "shared-authenticated-https", "access_readiness": "not-checked",
                     "learner_urls": {learner: routes.get(learner, {}) for learner in sorted(current)}}
            if shared.get("portal") is not None:
                entry["activity_url"] = f'https://{self.config["event_id"]}-entry.{shared["domain"]}/doris-courses/'
        return {"event_id": self.config["event_id"], "region": self.config["region"], "phase": self.record["phase"],
                "ready": bool(ready), "readiness_scope": "host-runtime-and-course-entry", "course_labs_validated": False,
                "managed_resources": self.record.get("managed_resources", 0),
                "resources_may_remain": bool(self.record.get("managed_resources", 0) or self.record.get("untracked_instances")
                                              or (self.record["known_volumes"] or self.record["known_instances"] or self.record.get("known_network_interfaces")) and self.record["phase"] != "destroyed"),
                "batch": {"concurrency": self.concurrency, "in_flight": self.inflight(), "ready": ready_ids,
                    "failed": failed_ids, "pending": sorted(set(current) - set(ready_ids) - set(failed_ids))},
                "allocations": {key: {"role": row["role"], "active": row["active"]} for key, row in self.allocations.items()},
                "untracked_instances": self.record.get("untracked_instances", []),
                "known_network_interfaces": self.record.get("known_network_interfaces", {}),
                "cleanup_boundary": self.record.get("cleanup_boundary", "not-yet-reconciled"),
                "retained_foundation": self.pilot["state_bucket_name"],
                "target_logs": "/var/log/doris-workshop", "nodes": self.record["nodes"],
                "last_health_check": self.record.get("last_health_check"),
                "entry": entry}

    def up(self, approved_plan_hash, *, timeout=7500, retry_failed=False, concurrency=2):
        with ttl.activity_operation(self.cloud, self.config, self.pilot, self.ttl_binding, scope_change=self.record["phase"] == "planned"):
            return self._up(approved_plan_hash, timeout=timeout, retry_failed=retry_failed, concurrency=concurrency)

    def _up(self, approved_plan_hash, *, timeout=7500, retry_failed=False, concurrency=2):
        pf.require(type(concurrency) is int and 1 <= concurrency <= 16, "batch: concurrency must be an integer from 1 to 16")
        began = self.clock()
        pf.require(self.record["phase"] not in {"destroying", "destroyed"}, "up: use a fresh reviewed plan after destroy")
        if self.record["phase"] == "planned":
            pf.require(approved_plan_hash and pf.file_hash(self.directory / "saved.tfplan") == approved_plan_hash,
                       "plan: pass the reviewed plan SHA256; no apply started")
            self.inventory()
            self.record["phase"] = "applying"
            self.record["apply_started_at"] = wc.utc_text(datetime.now(timezone.utc))
            self.save()
            try:
                pf.apply_saved(self.directory, aws_factory=lambda config: self.cloud, terraform_runner=self.tf.execute)
            except Exception:
                self.record["phase"] = "apply-failed"
                self.save()
                raise
            self.record["phase"] = "provisioned"
            self.record["apply_finished_at"] = wc.utc_text(datetime.now(timezone.utc))
            self.save()
        pf.require(self.record["phase"] != "apply-failed" and self.record["phase"] != "applying",
                   "apply: previous attempt incomplete; inspect status, create a fresh reviewed plan or destroy")
        # A repeated up reuses installation commands and performs a new health probe.
        for node in self.record["nodes"].values():
            if node.get("probe", {}).get("status") == "Success":
                node.pop("probe", None)
        report = self.inspect(install=True, timeout=timeout, retry_failed=retry_failed, concurrency=concurrency)
        pf.require(report["ready"], "runtime: one or more nodes failed readiness; see lifecycle.json and status, resources remain")
        self.record["phase"] = "ready"
        self.record.setdefault("readiness_history", []).append({"operation": "up", "seconds": round(self.clock() - began, 2),
            "ready_at": wc.utc_text(datetime.now(timezone.utc)), "instances": {k: n["instance_id"] for k,n in report["nodes"].items()}})
        self.save()
        report["phase"] = "ready"
        return report

    def replacement_plan(self, learner):
        pf.require(self.record["phase"] == "ready" and learner in self.config["learner_ids"],
                   "replace: select an existing learner in a ready activity")
        instances, _ = self.inventory()
        actual = self.actual_instances(instances.values())
        pf.require(set(instances) == set(self.config["learner_ids"]) and all(
            actual.get(i, {}).get("State", {}).get("Name") == "running" for i in instances.values()),
            "replace: all learners must be running before planning a single replacement")
        self.check_shared_access_binding()
        pf.live_check(self.config, self.pilot, self.cloud)
        self.check_shared_access_binding()
        address = 'aws_instance.learner[' + json.dumps(learner) + ']'
        plan_path = self.directory / "replace.tfplan"
        self.tf.run(["plan", "-input=false", "-lock-timeout=60s", "-replace=" + address,
            f"-var-file={self.directory / 'inputs.tfvars.json'}", f"-out={plan_path}"])
        plan = json.loads(self.tf.run(["show", "-json", str(plan_path)], capture=True))
        self.validate_replacement(plan, address)
        receipt = {"learner_id": learner, "address": address, "before": instances,
            "plan_sha256": pf.file_hash(plan_path), "vars_sha256": pf.file_hash(self.directory / "inputs.tfvars.json"),
            "valid_until": wc.utc_text(datetime.now(timezone.utc) + timedelta(minutes=15)),
            "warning": "The selected learner's instance and temporary root-disk data will be deleted. Other learners are preserved."}
        atomic_json(self.directory / "replacement.json", receipt)
        return receipt

    @staticmethod
    def validate_replacement(plan, address):
        pf.require(isinstance(plan.get("resource_changes"), list) and not plan.get("errored") and
                   plan.get("complete", True) is True and not plan.get("deferred_changes"),
                   "replace: use a complete, non-deferred Terraform plan export")
        managed = [r for r in plan["resource_changes"] if r.get("mode") == "managed"]
        addresses = [r["address"] for r in managed]
        pf.require(len(addresses) == len(set(addresses)) and all(not r.get("module_address") for r in managed),
                   "replace: duplicate or nested resource addresses are not supported")
        changed = [r for r in managed if r["change"]["actions"] != ["no-op"]]
        pf.require(len(changed) == 1 and changed[0]["address"] == address and
                   changed[0]["change"]["actions"] in (["delete", "create"], ["create", "delete"]),
                   "replace: plan must replace only the selected instance; shared or other-learner changes rejected")

    def replace(self, approved_plan_hash, *, timeout=900, concurrency=2):
        with ttl.activity_operation(self.cloud, self.config, self.pilot, self.ttl_binding, scope_change=True):
            return self._replace(approved_plan_hash, timeout=timeout, concurrency=concurrency)

    def _replace(self, approved_plan_hash, *, timeout=900, concurrency=2):
        pf.require(type(concurrency) is int and 1 <= concurrency <= 16, "batch: concurrency must be an integer from 1 to 16")
        receipt = wc.load_document(self.directory / "replacement.json")
        path = self.directory / "replace.tfplan"
        pf.require(approved_plan_hash and approved_plan_hash == receipt["plan_sha256"] == pf.file_hash(path)
            and receipt["vars_sha256"] == pf.file_hash(self.directory / "inputs.tfvars.json")
            and wc.timestamp(receipt["valid_until"], "valid_until") > datetime.now(timezone.utc),
            "replace: reviewed replacement plan expired or changed; create a new replacement plan")
        instances, _ = self.inventory()
        pf.require(instances == receipt["before"] and self.record["phase"] == "ready", "replace: activity changed since planning")
        self.actual_instances(instances.values())
        self.validate_replacement(json.loads(self.tf.run(["show", "-json", str(path)], capture=True)), receipt["address"])
        self.check_shared_access_binding()
        pf.live_check(self.config, self.pilot, self.cloud)
        self.check_shared_access_binding()
        began = self.clock()
        self.record["phase"] = "replacing"
        self.save()
        try:
            self.tf.run(["apply", "-input=false", "-lock-timeout=60s", str(path)])
            after, _ = self.inventory()
            learner = receipt["learner_id"]
            pf.require(after[learner] != instances[learner] and all(after[k] == v for k,v in instances.items() if k != learner),
                       "replace: instance assignments differ from the reviewed replacement")
            for node in self.record['nodes'].values():
                if node.get('probe',{}).get('status') == 'Success': node.pop('probe',None)
            report = self.inspect(install=True, timeout=timeout, concurrency=concurrency)
            pf.require(report["ready"], "replace: readiness failed; inspect status or destroy")
            self.record["phase"] = "ready"
            self.record.setdefault("readiness_history", []).append({"operation": "replace", "learner_id": learner,
                "seconds": round(self.clock()-began,2), "ready_at": wc.utc_text(datetime.now(timezone.utc)), "before": instances, "after": after})
            self.save()
            report["phase"] = "ready"
            return report
        except Exception:
            self.record["phase"] = "replace-failed"
            self.save()
            raise

    def check_shared_access_binding(self):
        if self.pilot.get("shared_access_file"):
            pf.require(pf.file_hash(self.pilot["shared_access_file"]) == self.binding.get("shared_access_sha256"),
                       "replace: shared access metadata changed; reconcile the topology before planning")

    def destroy(self):
        with ttl.activity_operation(self.cloud, self.config, self.pilot, self.ttl_binding, cleanup_operation=True):
            return self._destroy()

    def _destroy(self):
        resources = self.tf.state()
        claims = cleanup.project_state({**self.config, "cleanup_learner_ids": [key for key, row in self.allocations.items() if row["role"] == "learner"]},
            resources, state_bucket=self.pilot["state_bucket_name"], defer_missing_parents=True)
        self.inventory(resources=resources)  # Save IDs before Terraform removes them.
        self.actual_instances(self.record["known_instances"])
        self.record["phase"] = "destroying"
        self.save()
        plan_path = self.directory / "destroy.tfplan"
        if resources:
            self.tf.run(["plan", "-destroy", "-input=false", "-lock-timeout=60s",
                         f"-var-file={self.directory / 'inputs.tfvars.json'}", f"-out={plan_path}"])
            plan = json.loads(self.tf.run(["show", "-json", str(plan_path)], capture=True))
            absent = cleanup.validate_destroy_plan(resources, plan)
            pf.require(all(row["address"] in absent for row in claims if row.get("requires_refresh_absence")),
                "destroy: a tagless object without an owned parent may only be forgotten after explicit refresh deletion proof")
            self.tf.run(["apply", "-input=false", "-lock-timeout=60s", str(plan_path)])
        remaining = self.owned_resources()
        actual = self.actual_instances(self.record["known_instances"])
        active = [i for i in actual.values() if i.get("State", {}).get("Name") != "terminated"]
        volumes = self.cloud.items("ec2", "describe_volumes", "Volumes", Filters=[{"Name": "volume-id", "Values": self.record["known_volumes"]}]) if self.record["known_volumes"] else []
        interfaces = self.cloud.items("ec2", "describe_network_interfaces", "NetworkInterfaces",
            Filters=[{"Name": "network-interface-id", "Values": sorted(self.record.get("known_network_interfaces", {}))}]) if self.record.get("known_network_interfaces") else []
        self.record["managed_resources"] = len(remaining)
        self.record["reconciliation"] = {"managed_resources": len(remaining), "active_known_instances": len(active),
            "remaining_known_volumes": len(volumes), "remaining_known_network_interfaces": len(interfaces)}
        self.save()
        pf.require(not remaining and not active and not volumes and not interfaces,
                   "destroy: residual state, EC2 or EBS detected; retry destroy and inspect reconciliation")
        self.record["phase"], self.record["nodes"] = "destroyed", {}
        self.record["untracked_instances"] = []
        self.record["cleanup_boundary"] = "pilot-state-and-known-ec2-ebs-eni-only; use cleanup report for complete configured coverage"
        self.save()
        return self.summary()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--config", type=Path, required=True)
    plan.add_argument("--pilot", type=Path, required=True)
    plan.add_argument("--run-dir", type=Path, required=True)
    plan.add_argument("--previous-run", type=Path, help="Ready run on the same backend, for a roster-only reviewed plan")
    plan.add_argument("--ttl-binding", type=Path)
    for name in ("up", "status", "destroy", "replace-plan", "replace", "diagnose", "configure-logs"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--run-dir", type=Path, required=True)
        if name in {"up", "replace", "destroy", "replace-plan"}:
            cmd.add_argument("--ttl-binding", type=Path)
        if name not in {"destroy", "replace-plan"}:
            cmd.add_argument("--timeout", type=int, default=7500 if name == "up" else 180)
            cmd.add_argument("--retry-failed", action="store_true")
        if name in {"up", "replace"}:
            cmd.add_argument("--approved-plan-sha256")
        if name in {"up", "status", "replace"}:
            cmd.add_argument("--concurrency", type=int, default=2)
        if name == "replace-plan":
            cmd.add_argument("--learner", required=True)
        if name in {"diagnose", "configure-logs"}:
            cmd.add_argument("--learner")
            cmd.add_argument("--retention-days", type=int, default=7)
        if name == "destroy":
            cmd.add_argument("--approve-destroy", action="store_true", required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    controller = None
    try:
        if args.command == "plan":
            config, pilot = pf.load_inputs(args.config, args.pilot)
            runner = Terraform(args.run_dir)
            cloud = Cloud(config)
            with ttl.activity_operation(cloud, config, pilot, args.ttl_binding, scope_change=True):
                return plan_activity(args, config, pilot, cloud, runner)
        if args.command not in {"destroy", "replace-plan"}:
            pf.require(1 <= args.timeout <= 14400, "wait: timeout must be between 1 and 14400 seconds")
        if args.command in {"up", "status", "replace"}:
            pf.require(1 <= args.concurrency <= 16, "batch: concurrency must be between 1 and 16")
        with run_lock(args.run_dir):
            controller = Controller(args.run_dir, ttl_binding=getattr(args, "ttl_binding", None))
            if args.command == "destroy":
                report = controller.destroy()
            elif args.command == "up":
                report = controller.up(args.approved_plan_sha256, timeout=args.timeout, retry_failed=args.retry_failed, concurrency=args.concurrency)
            elif args.command == "replace-plan":
                with ttl.activity_operation(controller.cloud, controller.config, controller.pilot, controller.ttl_binding, scope_change=True):
                    report = controller.replacement_plan(args.learner)
            elif args.command == "replace":
                report = controller.replace(args.approved_plan_sha256, timeout=args.timeout, concurrency=args.concurrency)
            elif args.command in {"diagnose", "configure-logs"}:
                report = ops.diagnose(controller, learner=args.learner, configure=args.command == "configure-logs",
                                      days=args.retention_days, timeout=args.timeout)
            else:
                report = controller.inspect(timeout=args.timeout, retry_failed=args.retry_failed, concurrency=args.concurrency)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if args.command in {"destroy", "replace-plan"} or report.get("ok", report.get("ready", False)) else 1
    except Exception as exc:
        safe = str(exc) if isinstance(exc, wc.ConfigError) else "operation: query or input failed; inspect private diagnostics; resources may remain"
        failure = {"error": safe, "run_dir": str(args.run_dir), "resources_may_remain": args.command != "plan",
                   "next": "status/destroy with the same run directory; target logs /var/log/doris-workshop"}
        if safe.startswith("ttl:"):
            failure["next"] = "inspect the current TTL control record and reconcile outstanding tasks/locks before retrying"
        if controller is not None:
            try:
                failure["batch"] = controller.summary()["batch"]
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        print(json.dumps(failure), file=sys.stderr)
        return 2


def plan_activity(args, config, pilot, cloud, runner):
    if args.previous_run:
        with run_lock(args.previous_run):
            previous = Controller(args.previous_run, cloud=cloud)
            pf.require(previous.record["phase"] == "ready" and previous.binding.get("allocations_sha256") and
                all(previous.record["nodes"].get(key, {}).get("ready") for key in previous.config["learner_ids"]) and
                previous.inflight() == 0 and
                previous.binding["runtime_sha256"] == pf.file_hash(Path(pilot["runtime_bundle_dir"]) / "runtime.json") and
                previous.pilot["state_bucket_name"] == pilot["state_bucket_name"],
                "roster: use a ready run with bound allocations, the same runtime and state bucket")
            instances, _ = previous.inventory()
            pf.require(set(instances) == set(previous.config["learner_ids"]) and all(
                previous.record["nodes"][key]["instance_id"] == value for key, value in instances.items()),
                "roster: previous state changed since readiness; reconcile it before planning")
            pf.plan(config, pilot, cloud, "pilot", args.run_dir, terraform_runner=runner.execute,
                previous_manifest=wc.load_document(args.previous_run / "allocations.json"), previous_record=previous.record)
    else:
        pf.plan(config, pilot, cloud, "pilot", args.run_dir, terraform_runner=runner.execute)
    print(json.dumps({"phase": "planned", "plan_sha256": pf.file_hash(args.run_dir / "saved.tfplan"),
                      "message": "Review the private saved plan before up; foundation is reused."}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
