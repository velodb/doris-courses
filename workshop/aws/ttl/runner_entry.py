"""Immutable-image entry point. Only fixed object paths and JSON triggers enter."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import workshop_cleanup as cleanup
import workshop_config as wc
import workshop_scan
import workshop_ttl as ttl
import workshop_ttl_runner as runtime


class TaskCloud:
    def __init__(self, session):
        from botocore.config import Config
        self.session, self.clients = session, {}
        self.options = Config(connect_timeout=5, read_timeout=20, retries={"mode": "standard", "total_max_attempts": 3},
            ignore_configured_endpoint_urls=True)

    def client(self, service):
        if service not in self.clients:
            self.clients[service] = self.session.client(service, config=self.options)
        return self.clients[service]


def read_json(client, bucket, key, account, expected_hash):
    response = client.get_object(Bucket=bucket, Key=key, ExpectedBucketOwner=account)
    with response["Body"] as body:
        value = body.read(4 * 1024 * 1024 + 1)
    ttl.require(len(value) <= 4 * 1024 * 1024, "private input exceeds its bound")
    result = json.loads(value)
    ttl.require(wc.canonical_hash(result) == expected_hash, "private object hash changed")
    return result


def materialize_runtime(client, binding, prefix, directory):
    directory = Path(directory)
    ttl.require(not directory.is_symlink(), "runtime directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for artifact in binding["runtime_artifacts"]:
        path = directory / artifact["name"]
        response = client.get_object(Bucket=binding["control"]["bucket"], Key=prefix + "runtime/" + artifact["name"],
            ExpectedBucketOwner=binding["target"]["aws_account_id"])
        digest, total = hashlib.sha256(), 0
        with response["Body"] as body, path.open("xb") as output:
            while chunk := body.read(1024 * 1024):
                total += len(chunk)
                ttl.require(total <= artifact["size"], "runtime object exceeds its declared size")
                digest.update(chunk)
                output.write(chunk)
        ttl.require(total == artifact["size"] and digest.hexdigest() == artifact["sha256"], "runtime object changed")


def main():
    import boto3
    from botocore.config import Config
    os.umask(0o077)
    try:
        # Bootstrap only from task-definition constants, never from event fields.
        account, region = os.environ["TTL_ACCOUNT_ID"], os.environ["TTL_REGION"]
        forbidden = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
            "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_CONTAINER_CREDENTIALS_FULL_URI"}
        ttl.require(not any(os.environ.get(key) for key in forbidden) and not any(key.startswith("AWS_ENDPOINT_URL") for key in os.environ) and
            os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "").startswith("/v2/credentials/"), "ECS credentials required")
        session = boto3.Session(region_name=region)
        ttl.require(session.get_credentials().method == "container-role", "unexpected credential source")
        cloud = TaskCloud(session)
        s3 = session.client("s3", config=cloud.options.merge(Config(retries={"mode": "standard", "total_max_attempts": 1})))
        binding = runtime.read_binding(s3, bucket=os.environ["TTL_BINDING_BUCKET"], key=os.environ["TTL_BINDING_KEY"],
            version=os.environ["TTL_BINDING_VERSION"], expected_hash=os.environ["TTL_BINDING_SHA256"], account=account)
        ttl.require(binding["target"]["region"] == region, "task Region differs from binding")
        identity = cloud.client("sts").get_caller_identity()
        source = json.loads(Path("/opt/doris-workshop/source.json").read_text())
        ttl.require(source["source_tree_sha256"] == binding["source_tree_sha256"], "image source seal differs from binding")
        prefix = binding["control"]["key"].removesuffix("control.json") + f'bindings/{binding["revision"]}/'
        manifest = read_json(s3, binding["control"]["bucket"], prefix + "cleanup.json", account, binding["cleanup_manifest_sha256"])
        inputs = {row["module"]: read_json(s3, binding["control"]["bucket"], prefix + row["module"] + ".tfvars.json", account, row["vars_sha256"])
            for row in binding["modules"]}
        if "pilot" in inputs:
            ttl.require(inputs["pilot"].get("runtime_bundle_dir") == "/work/runtime" and
                any(row["name"] == "runtime.json" for row in binding["runtime_artifacts"]), "sealed runtime metadata is required")
        materialize_runtime(s3, binding, prefix, "/work/runtime")
        with tempfile.TemporaryDirectory(prefix="ttl-", dir="/work") as workspace:
            def reconcile():
                states = {}
                for row in binding["modules"]:
                    exported = cleanup.state_export(row["module"], json.loads(adapter.command(row["module"], ["show", "-json"], timeout=60)))
                    states[row["module"]] = {"status": "complete", "managed_addresses": [r["address"] for r in exported["resources"] if r.get("mode", "managed") == "managed"]}
                snapshot = workshop_scan.Scanner(cloud, manifest, states=states).collect()
                report = cleanup.reconcile(manifest, snapshot)
                s3.put_object(Bucket=binding["control"]["bucket"], Key=prefix + "result.json", ExpectedBucketOwner=account,
                    Body=json.dumps({"snapshot": snapshot, "report": report}, sort_keys=True).encode(), ContentType="application/json", ServerSideEncryption="AES256")
                return report
            adapter = runtime.TerraformAdapter(ROOT, workspace, manifest, inputs, source_sha=source["source_sha"], identity=identity,
                expected_role_arn=os.environ["TTL_EXPECTED_ROLE_ARN"], reconciliation=reconcile, environment=dict(os.environ), apply_authorized=True)
            ttl.execute(binding, json.loads(os.environ["TTL_TRIGGER_JSON"]), ttl.S3ControlStore(s3, binding["control"]["bucket"], account), adapter)
        print("TTL request handled; private control/evidence records contain the outcome.")
        return 0
    except ttl.Conflict:
        print("TTL activity busy; no unbound deletion attempted.", file=sys.stderr)
        return 75
    except Exception:
        print("TTL request failed; inspect private control/evidence and reconcile unknown outcomes.", file=sys.stderr)
        return 78


if __name__ == "__main__":
    sys.exit(main())
