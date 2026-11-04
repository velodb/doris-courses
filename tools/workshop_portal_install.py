#!/usr/bin/env python3
"""Install an empty operator portal on the exact shared-portal host via SSM."""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import time

import workshop as lifecycle
import workshop_access as access
import workshop_config as wc
import workshop_preflight as pf


def require(value, message):
    if not value:
        raise wc.ConfigError("portal install: " + message)


def verify(config, shared, portal_run, cloud):
    require(shared.get("schema_version") == 1 and shared.get("account_id") == config["aws_account_id"] and
            shared.get("region") == config["region"] and shared.get("domain") == config["domain"] and
            isinstance(shared.get("portal"), dict), "shared entry differs from the configured target")
    portal = shared["portal"]
    backend = wc.load_document(Path(portal_run) / "backend.tfbackend.json")
    variables = wc.load_document(Path(portal_run) / "inputs.tfvars.json")
    require(all(backend.get(k) == v for k, v in {"profile": config["aws_profile"], "region": config["region"],
            "allowed_account_ids": [config["aws_account_id"]], "encrypt": True, "use_lockfile": True}.items()) and
            isinstance(backend.get("bucket"), str) and isinstance(backend.get("key"), str) and
            backend["key"].startswith("aws-workshop/shared-portal/" + config["region"] + "/") and
            ".." not in backend["key"].split("/"), "shared portal backend differs")
    require(all(variables.get(k) == v for k, v in {"aws_profile": config["aws_profile"],
            "expected_account_id": config["aws_account_id"], "region": config["region"], "owner_id": config["owner_id"],
            "access_id": shared["access_id"], "vpc_id": shared["vpc_id"],
            "portal_security_group_id": shared["portal_security_group_id"],
            "alb_security_group_id": shared["alb_security_group_id"]}.items()), "portal variables differ from the shared entry")
    with tempfile.TemporaryDirectory(prefix="workshop-portal-install-state-") as temp:
        runner = lifecycle.Terraform(Path(temp))
        env = pf.explicit_environment()
        env["TF_DATA_DIR"] = str(Path(temp) / "terraform-data")
        module = f"-chdir={pf.MODULES / 'shared-portal'}"
        runner.execute([module, "init", "-input=false", "-lockfile=readonly",
                        f"-backend-config={Path(portal_run).resolve() / 'backend.tfbackend.json'}"], env)
        data = json.loads(runner.execute([module, "show", "-json"], env, capture=True))
    root = data.get("values", {}).get("root_module", {})
    require(not root.get("child_modules"), "portal state contains nested modules")
    resources = [r for r in root.get("resources", []) if r.get("mode") == "managed"]
    expected = {"aws_iam_role.portal", "aws_iam_role_policy_attachment.ssm", "aws_iam_instance_profile.portal", "aws_instance.portal"}
    require(len(resources) == len(expected) and {r["address"] for r in resources} == expected, "portal state resources differ")
    instance = next(r["values"] for r in resources if r["address"] == "aws_instance.portal")
    require(instance["id"] == portal["instance_id"] and instance["ami"] == variables["ami_id"] and instance["vpc_security_group_ids"] == [shared["portal_security_group_id"]] and
            instance["subnet_id"] == variables["subnet_id"], "portal state points to another host")
    expected_tags = {"Project": "doris-workshop", "Scope": "shared-portal", "Owner": config["owner_id"], "Access": shared["access_id"]}
    for row in resources:
        if row["address"] != "aws_iam_role_policy_attachment.ssm":
            tags = row["values"].get("tags_all") or row["values"].get("tags") or {}
            require(all(tags.get(k) == v for k, v in expected_tags.items()) and "Event" not in tags, "portal state ownership differs")
    role = next(r["values"] for r in resources if r["address"] == "aws_iam_role.portal")
    profile = next(r["values"] for r in resources if r["address"] == "aws_iam_instance_profile.portal")
    attachment = next(r["values"] for r in resources if r["address"] == "aws_iam_role_policy_attachment.ssm")
    require(attachment["role"] == role["name"] and profile["role"] == role["name"] and
            attachment["policy_arn"] == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore", "portal role relationships differ")
    reservations = cloud.call("ec2", "describe_instances", InstanceIds=[portal["instance_id"]])["Reservations"]
    rows = [i for r in reservations for i in r["Instances"]]
    require(len(rows) == 1 and rows[0]["InstanceId"] == portal["instance_id"] and rows[0]["State"]["Name"] == "running" and
            rows[0]["VpcId"] == shared["vpc_id"] and rows[0]["SubnetId"] == variables["subnet_id"] and rows[0]["ImageId"] == variables["ami_id"] and
            {g["GroupId"] for g in rows[0]["SecurityGroups"]} == {shared["portal_security_group_id"]} and
            not rows[0].get("KeyName") and rows[0]["MetadataOptions"]["HttpTokens"] == "required" and
            rows[0]["MetadataOptions"]["HttpPutResponseHopLimit"] == 1, "live portal host or network differs")
    tags = {t["Key"]: t["Value"] for t in rows[0].get("Tags", [])}
    require(all(tags.get(k) == v for k, v in expected_tags.items()) and "Event" not in tags, "live portal ownership differs")
    balancers = cloud.call("elbv2", "describe_load_balancers", LoadBalancerArns=[shared["alb_arn"]])["LoadBalancers"]
    require(len(balancers) == 1 and balancers[0]["VpcId"] == shared["vpc_id"] and balancers[0]["State"]["Code"] == "active",
            "shared ALB differs")
    tag_rows = cloud.call("elbv2", "describe_tags", ResourceArns=[shared["alb_arn"]])["TagDescriptions"]
    tags = {t["Key"]: t["Value"] for r in tag_rows if r.get("ResourceArn") == shared["alb_arn"] for t in r.get("Tags", [])}
    require(all(tags.get(k) == v for k, v in {**expected_tags, "Scope": "shared-access"}.items()), "shared ALB ownership differs")
    target = cloud.call("elbv2", "describe_target_groups", TargetGroupArns=[portal["target_group_arn"]])["TargetGroups"]
    require(len(target) == 1 and target[0]["VpcId"] == shared["vpc_id"] and shared["alb_arn"] in target[0]["LoadBalancerArns"],
            "portal target group differs from the shared ALB")
    attached = cloud.call("elbv2", "describe_target_health", TargetGroupArn=portal["target_group_arn"])["TargetHealthDescriptions"]
    require(len(attached) == 1 and attached[0]["Target"]["Id"] == portal["instance_id"] and attached[0]["Target"]["Port"] == 8080,
            "portal target attachment differs")
    return portal["instance_id"]


def payload(shared, *, sources=None):
    files = dict(sources if sources is not None else access.portal_sources())
    ui_digest = hashlib.sha256(files["ui.tar"]).hexdigest()
    files["overlay.json"] = (json.dumps({"schema_version": 1, "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}, sort_keys=True) + "\n").encode()
    registry = {"schema_version": 1, "domain": shared["domain"], "region": shared["region"], "alb_arn": shared["alb_arn"], "activities": {}}
    registry_bytes = (json.dumps(registry, indent=2, sort_keys=True) + "\n").encode()
    files["portal.json"] = registry_bytes
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, data in sorted(files.items()):
            if name == "ui.tar":
                continue
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o600, 0
            tar.addfile(entry, io.BytesIO(data))
    packed = gzip.compress(archive.getvalue(), mtime=0)
    encoded, digest = base64.b64encode(packed).decode(), hashlib.sha256(packed).hexdigest()
    commands = ["set -eu", "umask 077", "exec 9>/run/lock/doris-workshop-portal-install.lock", "flock -n 9",
        "test ! -e /etc/doris-workshop-portal/portal.json",
        "install -d -m 0700 /var/lib/doris-workshop-portal-install",
        "portal_boot_rc=0; timeout 300 cloud-init status --wait --format=json > /var/lib/doris-workshop-portal-install/cloud-init-status.json 2>/dev/null || portal_boot_rc=$?; test \"$portal_boot_rc\" -eq 0 || test \"$portal_boot_rc\" -eq 2",
        "python3.12 - <<'PY'\nimport json,pathlib\ns=json.loads(pathlib.Path('/var/lib/doris-workshop-portal-install/cloud-init-status.json').read_text())\nassert s['status']=='done' and not s.get('errors',[])\nPY",
        "test -f /var/lib/doris-workshop-portal-install/bootstrap-ready", ": > /var/lib/doris-workshop-portal-install/bundle.b64"]
    commands.extend("printf '%s' '" + encoded[i:i + 1200] + "' >> /var/lib/doris-workshop-portal-install/bundle.b64" for i in range(0, len(encoded), 1200))
    commands.append("python3.12 - <<'PY'\nimport base64,hashlib,io,pathlib,tarfile\np=pathlib.Path('/var/lib/doris-workshop-portal-install')\nb=base64.b64decode((p/'bundle.b64').read_text(),validate=True)\n"
        + "assert hashlib.sha256(b).hexdigest()==" + repr(digest) + "\n"
        "with tarfile.open(fileobj=io.BytesIO(b),mode='r:gz') as t:\n"
        + " assert {m.name for m in t.getmembers()}==" + repr(set(files) - {"ui.tar"}) + "\n"
        " for m in t.getmembers():\n  assert m.isfile() and m.size<100000\n  (p/m.name).write_bytes(t.extractfile(m).read())\nPY")
    commands.append("cp /var/lib/doris-workshop-portal-install/ui-" + ui_digest + ".tar /var/lib/doris-workshop-portal-install/ui.tar")
    commands.extend(["bash /var/lib/doris-workshop-portal-install/install_portal.sh /var/lib/doris-workshop-portal-install /var/lib/doris-workshop-portal-install/portal.json",
                     "systemctl is-active --quiet doris-workshop-portal.service", "printf 'portal-ready\\n'"])
    require(len(json.dumps(commands)) < 24000, "portal installer exceeds its SSM payload bound")
    return commands, digest, registry_bytes


def ui_payload(data):
    digest = hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(gzip.compress(data, mtime=0)).decode()
    commands = ["set -eu", "umask 077", "install -d -m 0700 /var/lib/doris-workshop-portal-install",
        "python3 - <<'PY'\nimport base64,gzip,hashlib,os,pathlib,tempfile\n"
        + "data=gzip.decompress(base64.b64decode(" + repr(encoded) + ",validate=True))\n"
        + "assert len(data)<=1024*1024 and hashlib.sha256(data).hexdigest()==" + repr(digest) + "\n"
        + "target=pathlib.Path('/var/lib/doris-workshop-portal-install/ui-" + digest + ".tar')\n"
        + "assert not target.is_symlink()\n"
        + "with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as f:\n f.write(data)\n temp=pathlib.Path(f.name)\n"
        + "try:\n temp.chmod(0o600)\n os.replace(temp,target)\nfinally:\n temp.unlink(missing_ok=True)\nprint('ui-ready')\nPY"]
    require(len(json.dumps(commands)) < 24000, "UI stage exceeds its SSM payload bound")
    return commands, digest


def dispatch_step(instance, commands, digest, record_path, marker, cloud, *, timeout, clock, sleep):
    require(timeout > 0, "portal installation is pending; reconcile before retrying")
    ssm = cloud.client("ssm")
    require(len(json.dumps(commands)) < 24000, "installer step exceeds its SSM payload bound")
    if record_path.exists():
        record = wc.load_document(record_path)
        require(record.get("instance_id") == instance and record.get("payload_sha256") == digest, "operation inputs differ")
        require(record.get("status") != "dispatching" and record.get("command_id"), "dispatch outcome unknown; reconcile SSM history")
        if record.get("status") == "Success":
            return True
        require(record.get("status") in {"Pending", "InProgress", "Delayed", "Cancelling"}, "failed installation requires reconciliation")
    else:
        deadline = clock() + min(timeout, 300)
        while True:
            rows = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance]}])["InstanceInformationList"]
            if len(rows) == 1 and rows[0].get("PingStatus") == "Online":
                break
            require(clock() < deadline, "portal SSM agent is not online")
            sleep(min(5, max(0, deadline - clock())))
        record = {"instance_id": instance, "payload_sha256": digest, "status": "dispatching"}
        lifecycle.atomic_json(record_path, record)
        sent = cloud.dispatch_client().send_command(InstanceIds=[instance], DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands, "executionTimeout": ["600"]}, TimeoutSeconds=600)
        record.update(status="Pending", command_id=sent["Command"]["CommandId"])
        lifecycle.atomic_json(record_path, record)
    deadline = clock() + timeout
    while True:
        try:
            result = ssm.get_command_invocation(CommandId=record["command_id"], InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            result = {"Status": "Pending"}
        status = result["Status"]
        require(status in {"Pending", "InProgress", "Delayed", "Cancelling", "Success", "Failed", "Cancelled", "TimedOut"},
                "unknown SSM status; reconcile before retrying")
        record["status"] = "Failed" if status == "Success" and marker not in result.get("StandardOutputContent", "") else status
        lifecycle.atomic_json(record_path, record)
        if status in {"Success", "Failed", "Cancelled", "TimedOut"}:
            break
        require(clock() < deadline, "portal installation is pending; reconcile before retrying")
        sleep(min(5, max(0, deadline - clock())))
    require(record["status"] == "Success", "installation failed or returned an invalid result; inspect private host logs")
    return False


def install(config, shared, portal_run, output, cloud, *, timeout=900, clock=time.monotonic, sleep=time.sleep):
    output = Path(output).resolve()
    instance = verify(config, shared, portal_run, cloud)
    sources = access.portal_sources()
    commands, digest, registry_bytes = payload(shared, sources=sources)
    stage_commands, stage_digest = ui_payload(sources["ui.tar"])
    operation = {"schema_version": 1, "instance_id": instance, "payload_sha256": digest, "ui_sha256": stage_digest}
    operation_path = output / "operation.json"
    if output.exists():
        require(operation_path.is_file(), "incomplete operation directory; reconcile before retrying")
        require(wc.load_document(operation_path) == operation, "operation inputs differ")
    else:
        output.mkdir(parents=True, mode=0o700)
        lifecycle.atomic_json(operation_path, operation)
        (output / "portal-empty.json").write_bytes(registry_bytes)
        os.chmod(output / "portal-empty.json", 0o600)
    deadline = clock() + timeout
    dispatch_step(instance, stage_commands, stage_digest, output / "ui-dispatch.json", "ui-ready", cloud,
        timeout=max(0, deadline-clock()), clock=clock, sleep=sleep)
    require(clock() < deadline, "portal installation is pending; reconcile before retrying")
    reused = dispatch_step(instance, commands, digest, output / "dispatch.json", "portal-ready", cloud,
        timeout=max(0, deadline-clock()), clock=clock, sleep=sleep)
    return {"status": "Success", "reused_receipt": reused, "portal_instance_id": instance}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "access", "portal-run", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        require(1 <= args.timeout <= 3600, "timeout must be between 1 and 3600 seconds")
        config, _, _ = wc.validate_config(wc.load_document(args.config))
        cloud = lifecycle.Cloud(config)
        identity = cloud.call("sts", "get_caller_identity")
        require(identity["Account"] == config["aws_account_id"] and identity["Arn"].startswith("arn:aws:"), "AWS identity differs")
        # The portal is shared, not an activity-owned TTL resource. All operators
        # use the same portal run directory; the remote host also holds a lock.
        with lifecycle.run_lock(args.portal_run):
            result = install(config, wc.load_document(args.access), args.portal_run, args.output, cloud, timeout=args.timeout)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        safe = str(exc) if isinstance(exc, wc.ConfigError) else "operation failed; inspect private state and reconcile before retrying"
        print(json.dumps({"error": safe, "resources_may_remain": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
