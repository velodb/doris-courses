"""Fixed-command Terraform adapter for the TTL control protocol.

This adapter is independently testable. The packaged image and task-role
path still require real acceptance before deployment.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import workshop_cleanup as cleanup
import workshop_config as wc
import workshop_ttl as ttl


def task_session(binding):
    """A separate task-role path; never relax the operator profile resolver."""
    import boto3
    ttl.validate_binding(binding)
    forbidden = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
        "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_CONTAINER_CREDENTIALS_FULL_URI"}
    ttl.require(not any(os.environ.get(key) for key in forbidden) and not any(key.startswith("AWS_ENDPOINT_URL") for key in os.environ),
        "runner accepts only its ECS task role, without ambient credentials or endpoints")
    ttl.require(os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "").startswith("/v2/credentials/"), "ECS task credentials are required")
    session = boto3.Session(region_name=binding["target"]["region"])
    ttl.require(session.get_credentials().method == "container-role", "unexpected runner credential provider")
    return session


def read_binding(client, *, bucket, key, version, expected_hash, account):
    ttl.require(version and version != "null", "pin an immutable binding object version")
    value = client.get_object(Bucket=bucket, Key=key, VersionId=version, ExpectedBucketOwner=account)
    with value["Body"] as body:
        raw = body.read(65537)
    ttl.require(len(raw) <= 65536, "binding object too large")
    binding = json.loads(raw)
    ttl.require(ttl.validate_binding(binding) == expected_hash and binding["control"]["bucket"] == bucket and
        binding["target"]["aws_account_id"] == account and key == binding["control"]["key"].removesuffix("control.json") + f'bindings/{binding["revision"]}.json',
        "binding object differs from the fixed task definition")
    return binding


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hash(root):
    root = Path(root)
    paths = [*root.glob("tools/workshop*.py"), *root.glob("workshop/aws/terraform/pilot/*.tf"),
        *root.glob("workshop/aws/terraform/pilot/*.tftpl"), *root.glob("workshop/aws/terraform/access-bindings/*.tf")]
    paths.extend(root.glob("workshop/aws/terraform/*/.terraform.lock.hcl"))
    paths.extend(root.glob("workshop/aws/ttl/*.json"))
    paths.extend(root.glob("workshop/aws/ttl/*.py"))
    paths.extend(root.glob("workshop/aws/ttl/*.tfrc"))
    paths.extend(root.glob("workshop/aws/ttl/requirements.txt"))
    paths.extend(root.glob("workshop/aws/ttl/requirements.lock"))
    paths.extend(root.glob("workshop/aws/runtime/start_image.py"))
    return wc.canonical_hash({str(path.relative_to(root)): file_hash(path) for path in sorted(paths)})


class TerraformAdapter:
    def __init__(self, root, work, manifest, inputs, *, source_sha, identity, expected_role_arn, reconciliation,
                 environment, runner=subprocess.run, apply_authorized=False):
        self.root, self.work = Path(root).resolve(), Path(work).resolve()
        self.manifest, self.inputs = manifest, inputs
        self.source_sha, self.identity, self.role_arn = source_sha, identity, expected_role_arn
        self.reconciliation, self.environment, self.runner = reconciliation, environment, runner
        self.apply_authorized = apply_authorized
        self.plans, self.binding = {}, None

    def verify(self, binding):
        ttl.validate_binding(binding)
        cleanup.validate_manifest(self.manifest)
        ttl.require(self.apply_authorized and binding["cleanup_manifest_sha256"] == wc.canonical_hash(self.manifest),
            "explicit scoped cleanup authorization is required; a scan report is not a grant")
        ttl.require(self.manifest["target"] == binding["target"] and self.source_sha == binding["source_sha"] and
            tree_hash(self.root) == binding["source_tree_sha256"], "source or cleanup ownership changed")
        t = binding["target"]
        role_prefix = f'arn:aws:iam::{t["aws_account_id"]}:role/'
        ttl.require(self.role_arn.startswith(role_prefix), "runner role belongs to another account")
        role_name = self.role_arn.removeprefix(role_prefix)
        ttl.require(self.identity["Account"] == t["aws_account_id"] and
            self.identity["Arn"].startswith(f'arn:aws:sts::{t["aws_account_id"]}:assumed-role/{role_name}/'), "unexpected runner role identity")
        ttl.require(set(self.inputs) == {row["module"] for row in binding["modules"]}, "unexpected state inputs")
        forbidden = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
            "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_CONTAINER_CREDENTIALS_FULL_URI"}
        ttl.require(not any(self.environment.get(key) for key in forbidden) and not any(key.startswith("AWS_ENDPOINT_URL") for key in self.environment),
            "Terraform must use the same verified task-role credential path")
        self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
        for row in binding["modules"]:
            module = row["module"]
            directory = self.root / "workshop/aws/terraform" / module
            ttl.require(all(path.is_file() and not path.is_symlink() for path in directory.glob("*.tf")), "unsafe module source")
            files = {path.name: file_hash(path) for path in directory.iterdir() if path.suffix in {".tf", ".tftpl"} or path.name == ".terraform.lock.hcl"}
            ttl.require(wc.canonical_hash(files) == row["module_sha256"] and wc.canonical_hash(self.inputs[module]) == row["vars_sha256"], "module or variables changed")
            values = self.inputs[module]
            ttl.require(values.get("aws_profile") is None and values.get("credential_mode") == "task-role", "cloud Terraform inputs must use the verified task role")
            ttl.require(values["expected_account_id"] == t["aws_account_id"] and values["region"] == t["region"] and
                values["event_id"] == t["event_id"] and values["owner_id"] == t["owner_id"] and
                ttl.utc(values["expires_at"]) == binding["expires_at"], "variables do not belong to this activity")
            private = self.work / module
            private.mkdir(exist_ok=True, mode=0o700)
            wc.write_manifest(private / "inputs.tfvars.json", values)
            wc.write_manifest(private / "backend.json", row["backend"])
        self.binding = binding

    def command(self, module, args, *, timeout):
        ttl.require(self.binding is not None and module in self.inputs, "verify the runner before using Terraform")
        env = {**self.environment, "TF_DATA_DIR": str(self.work / module / "terraform-data"), "TF_INPUT": "0"}
        ttl.require(not any(key.startswith(("TF_VAR_", "TF_CLI_ARGS")) or key == "TF_WORKSPACE" for key in env), "implicit Terraform overrides are forbidden")
        result = self.runner(["terraform", "-chdir=" + str(self.root / "workshop/aws/terraform" / module), *args],
            env=env, capture_output=True, text=True, timeout=timeout)
        ttl.require(result.returncode == 0, "Terraform failed; retain the activity fence and private evidence")
        return result.stdout

    def plan(self, module_binding, *, lock_wait_seconds, timeout=None):
        module = module_binding["module"]
        private = self.work / module
        self.command(module, ["init", "-input=false", "-lockfile=readonly", "-backend-config=" + str(private / "backend.json")], timeout=180)
        state = json.loads(self.command(module, ["show", "-json"], timeout=60))
        exported = cleanup.state_export(module, state)
        resources = [row for row in exported["resources"] if row.get("mode", "managed") == "managed"]
        path = private / "destroy.tfplan"
        self.command(module, ["plan", "-destroy", "-input=false", "-lock=true", f"-lock-timeout={lock_wait_seconds}s",
            "-var-file=" + str(private / "inputs.tfvars.json"), "-out=" + str(path)], timeout=timeout or self.binding["limits"]["task_timeout_seconds"])
        plan = json.loads(self.command(module, ["show", "-json", str(path)], timeout=60))
        self.plans[module] = {"path": path, "sha256": file_hash(path), "resources": resources}
        return plan

    def validate(self, module_binding, plan):
        module = module_binding["module"]
        resources = self.plans[module]["resources"]
        approved = {(row["address"], row["id"]) for row in self.manifest["state"] if row["module"] == module}
        # Project again, using only the already reviewed scope. Fresh additions
        # and replacements require a new binding and schedule revision.
        for row in resources:
            identity = row["values"].get("arn") if row["type"] in {"aws_lb_target_group", "aws_lb_listener_rule"} else row["values"].get("id")
            if row["type"] == "aws_route53_record":
                identity = row["values"]["zone_id"] + "/" + row["values"]["name"].rstrip(".") + "/" + row["values"]["type"]
            if row["type"] in cleanup.DERIVED:
                identity = row["address"]
            ttl.require((row["address"], identity) in approved, "state contains an unreviewed resource or replacement")
        config = {**self.binding["target"], "aws_partition": "aws", "learner_ids": self.inputs[module].get("learner_ids", list(self.inputs[module].get("learners", {}))),
            "cleanup_instance_ids": [row["id"] for row in self.manifest["known"] if row["kind"] == "instances"]}
        hosts = [row["id"].split("/")[1] for row in self.manifest["known"] if row["kind"] == "dns_records"]
        projected = cleanup.project_state(config, resources, module=module, state_bucket=self.binding["control"]["bucket"],
            shared=self.inputs[module].get("access"), hosts=hosts, defer_missing_parents=True)
        absent = cleanup.validate_destroy_plan(resources, plan)
        ttl.require(all(row["address"] in absent for row in projected if row.get("requires_refresh_absence")), "unproved dependent resource cannot be deleted")

    def apply(self, module_binding, plan, *, timeout):
        module = module_binding["module"]
        approved = self.plans[module]
        ttl.require(file_hash(approved["path"]) == approved["sha256"], "fresh destroy plan changed after validation")
        self.command(module, ["apply", "-input=false", "-lock=true", f'-lock-timeout={self.binding["limits"]["lock_wait_seconds"]}s', str(approved["path"])], timeout=timeout)

    def reconcile(self):
        report = self.reconciliation()
        ttl.require(report.get("manifest_sha256") == self.binding["cleanup_manifest_sha256"] and report.get("event_id") == self.binding["target"]["event_id"],
            "reconciliation used a different activity manifest")
        return report
