#!/usr/bin/env python3
"""Install the fixed gateway overlay on exact activity-owned learner hosts."""

import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import time
from urllib.parse import urlsplit

import workshop as lifecycle
import workshop_access as access
import workshop_config as wc
import workshop_preflight as pf
import workshop_ttl as ttl


def require(condition, message):
    if not condition:
        raise wc.ConfigError("gateway deployment: " + message)


def payload(plan, config, learner):
    """Pack only checked-in installer files and the validated learner config."""
    files = {}
    for name in access.OVERLAY_FILES:
        source = wc.SPEC / "operations/node.py" if name == "operations.py" else access.OVERLAY / name
        require(source.is_file() and not source.is_symlink(), "fixed overlay file is missing or unsafe")
        files[name] = source.read_bytes()
    manifest = {"schema_version": 1, "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files["overlay.json"] = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    files["gateway.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    files["browser-urls.json"] = (json.dumps(access.browser_routes(plan, learner), sort_keys=True) + "\n").encode()
    values = access.host_environment(plan, learner)
    require(all(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and re.fullmatch(r"[a-zA-Z0-9:/._-]+", value)
                for key, value in values.items()), "unsafe environment value")
    files["browser.env"] = "".join(f"{k}={v}\n" for k, v in sorted(values.items())).encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o600, 0
            tar.addfile(entry, io.BytesIO(data))
    packed = gzip.compress(archive.getvalue(), mtime=0)
    encoded = base64.b64encode(packed).decode()
    digest = hashlib.sha256(packed).hexdigest()
    commands = ["set -eu", "umask 077", "install -d -m 0700 /var/lib/doris-workshop-gateway-install",
                ": > /var/lib/doris-workshop-gateway-install/bundle.b64"]
    commands.extend("printf '%s' '" + encoded[i:i + 1200] + "' >> /var/lib/doris-workshop-gateway-install/bundle.b64"
                    for i in range(0, len(encoded), 1200))
    commands.append("python3.12 - <<'PY'\nimport base64,hashlib,io,pathlib,tarfile\n"
        "p=pathlib.Path('/var/lib/doris-workshop-gateway-install')\n"
        "b=base64.b64decode((p/'bundle.b64').read_text(),validate=True)\n"
        + "assert hashlib.sha256(b).hexdigest()==" + repr(digest) + "\n"
        "with tarfile.open(fileobj=io.BytesIO(b),mode='r:gz') as t:\n"
        + " assert {m.name for m in t.getmembers()}==" + repr(set(files)) + "\n"
        " for m in t.getmembers():\n"
        "  assert m.isfile() and m.size<100000\n"
        "  (p/m.name).write_bytes(t.extractfile(m).read())\nPY")
    commands.extend([
        "bash /var/lib/doris-workshop-gateway-install/install_gateway.sh /var/lib/doris-workshop-gateway-install "
        "/var/lib/doris-workshop-gateway-install/gateway.json /var/lib/doris-workshop-gateway-install/browser-urls.json "
        "/var/lib/doris-workshop-gateway-install/browser.env",
        "systemctl is-active --quiet doris-workshop-gateway.service",
        "systemctl is-active --quiet doris-workshop-jupyter.service", "printf 'gateway-ready\\n'",
    ])
    require(len(json.dumps(commands)) < 24000, "fixed SSM payload exceeds its bound")
    return commands, digest


def verify_assignments(controller, plan, identity, subjects):
    shared = controller.binding.get("shared_access")
    config = controller.config
    require(controller.binding.get("tool_sha256") == pf.tool_hash(),
            "gateway source changed since the reviewed pilot plan")
    require(isinstance(shared, dict) and plan.get("event_id") == config["event_id"] and
            plan.get("expires_at") == config["expires_at"] and plan.get("region") == config["region"] and
            plan.get("alb_arn") == shared["alb_arn"] and plan.get("terraform", {}).get("access") == shared,
            "route plan is not bound to this activity and shared entry")
    require(all(plan["terraform"].get(k) == v for k, v in {
        "aws_profile": config["aws_profile"], "expected_account_id": config["aws_account_id"],
        "region": config["region"], "owner_id": config["owner_id"], "event_id": config["event_id"],
        "expires_at": config["expires_at"]}.items()), "route variables belong to another activity")
    instances, _ = controller.inventory()
    require(instances == plan["terraform"]["learners"] and set(instances) == set(config["learner_ids"]),
            "route assignments differ from the current pilot state")
    require(plan.get("browser_routes") == controller.binding.get("browser_routes"),
            "browser routes differ from the bound pilot inputs")
    actual = controller.actual_instances(instances.values())
    require(set(actual) == set(instances.values()) and all(row.get("State", {}).get("Name") == "running" for row in actual.values()),
            "every assigned instance must exist and be running")
    pool = identity.get("user_pool_id", "")
    require(isinstance(pool, str) and re.fullmatch(re.escape(config["region"]) + r"_[A-Za-z0-9]+", pool) and
            identity.get("event_id") == config["event_id"] and
            identity.get("issuer") == f'https://cognito-idp.{config["region"]}.amazonaws.com/{pool}',
            "identity does not belong to this activity")
    client = controller.cloud.client("cognito-idp")
    arn = f'arn:aws:cognito-idp:{config["region"]}:{config["aws_account_id"]}:userpool/{pool}'
    tags = client.list_tags_for_resource(ResourceArn=arn)["Tags"]
    require(all(tags.get(k) == v for k, v in {"Project": "doris-workshop", "Scope": "activity-access",
            "Event": config["event_id"], "Owner": config["owner_id"]}.items()), "Cognito pool is not activity-owned")
    actual_client = client.describe_user_pool_client(UserPoolId=pool, ClientId=identity["client_id"])["UserPoolClient"]
    require(actual_client["ClientId"] == identity["client_id"] and actual_client["UserPoolId"] == pool,
            "Cognito client differs from the selected pool")
    login = urlsplit(identity["login_origin"])
    suffix = f'.auth.{config["region"]}.amazoncognito.com'
    require(login.scheme == "https" and login.hostname and login.hostname.endswith(suffix) and
            not login.path and not login.query and not login.fragment and login.netloc == login.hostname,
            "invalid Cognito login origin")
    domain = login.hostname.removesuffix(suffix)
    require(client.describe_user_pool_domain(Domain=domain).get("DomainDescription", {}).get("UserPoolId") == pool,
            "Cognito domain differs from the selected pool")
    configs = access.gateway_configs(plan, identity, subjects)
    for learner in sorted(instances):
        user = client.admin_get_user(UserPoolId=pool, Username=learner)
        actual_subjects = [row["Value"] for row in user.get("UserAttributes", []) if row.get("Name") == "sub"]
        require(user.get("Username") == learner and user.get("Enabled") is True and actual_subjects == subjects[learner],
                "Cognito subject or account status differs from the learner assignment")
        expected_hosts = {row["hostname"] for row in plan["terraform"]["routes"].values() if row["learner_id"] == learner}
        require(set(configs[learner]["services"]) == expected_hosts, "gateway hosts differ from the assigned routes")
        expected_ports = {str(row["port"]): "https://" + host for host, row in configs[learner]["services"].items()}
        require(len(expected_ports) == len(expected_hosts) and expected_ports == plan["browser_routes"][learner],
                "gateway ports differ from the bound browser routes")
    return instances, configs


def install(controller, plan, identity, subjects, *, learner=None, timeout=900, retry_failed=False,
            clock=time.monotonic, sleep=time.sleep):
    instances, configs = verify_assignments(controller, plan, identity, subjects)
    require(learner is None or learner in instances, "selected learner is not assigned")
    selected = sorted(instances if learner is None else [learner])
    deadline = clock() + timeout
    results = {}
    installs = controller.record.setdefault("gateway_installations", {})
    for who in selected:
        instance = instances[who]
        commands, digest = payload(plan, configs[who], who)
        require(controller.binding.get("tool_sha256") == pf.tool_hash(),
                "gateway source changed while preparing the installation")
        previous = installs.get(instance)
        if previous:
            require(previous.get("learner_id") == who and previous.get("payload_sha256") == digest,
                    "existing installation is bound to different inputs; reconcile it first")
            require(previous.get("status") != "dispatching" and previous.get("command_id"),
                    "dispatch outcome unknown; reconcile SSM history before retrying")
            if previous.get("status") == "Success":
                results[who] = {"instance_id": instance, "status": "Success", "reused": True}
                continue
            if previous.get("status") in {"Failed", "Cancelled", "TimedOut"}:
                require(retry_failed and previous.get("attempts", 0) < 3, "failed installation requires explicit bounded retry")
                previous = None
        if previous is None:
            attempts = installs.get(instance, {}).get("attempts", 0) + 1
            installs[instance] = {"learner_id": who, "payload_sha256": digest, "attempts": attempts, "status": "dispatching"}
            controller.save()
            result = controller.cloud.dispatch_client().send_command(InstanceIds=[instance], DocumentName="AWS-RunShellScript",
                Parameters={"commands": commands, "executionTimeout": ["600"]}, TimeoutSeconds=600)
            installs[instance].update(status="Pending", command_id=result["Command"]["CommandId"])
            controller.save()
        row = installs[instance]
        while True:
            try:
                result = controller.cloud.client("ssm").get_command_invocation(CommandId=row["command_id"], InstanceId=instance)
            except controller.cloud.client("ssm").exceptions.InvocationDoesNotExist:
                result = {"Status": "Pending"}
            status = result["Status"]
            require(status in {"Pending", "InProgress", "Delayed", "Success", "Failed", "Cancelled", "Cancelling", "TimedOut"},
                    "unknown SSM status; reconcile before retrying")
            row["status"] = ("Failed" if status == "Success" and
                             "gateway-ready" not in result.get("StandardOutputContent", "") else status)
            controller.save()
            if status in {"Success", "Failed", "Cancelled", "TimedOut"}:
                break
            require(clock() < deadline, "installation is still pending; reconcile before retrying")
            sleep(min(5, max(0, deadline - clock())))
        require(row["status"] == "Success",
                "gateway installation failed or returned an invalid result; inspect private target logs")
        results[who] = {"instance_id": instance, "status": "Success", "reused": False}
    return {"event_id": controller.config["event_id"], "gateways": results,
            "readiness_scope": "gateway-installation-only", "authenticated_access_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pilot-run", "plan", "identity", "subjects"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ttl-binding", type=Path)
    parser.add_argument("--learner")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(1 <= args.timeout <= 14400, "timeout must be between 1 and 14400 seconds")
        with lifecycle.run_lock(args.pilot_run):
            controller = lifecycle.Controller(args.pilot_run, ttl_binding=args.ttl_binding)
            with ttl.activity_operation(controller.cloud, controller.config, controller.pilot, args.ttl_binding, scope_change=True):
                result = install(controller, wc.load_document(args.plan), wc.load_document(args.identity),
                                 wc.load_document(args.subjects), learner=args.learner, timeout=args.timeout,
                                 retry_failed=args.retry_failed)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        safe = str(exc) if isinstance(exc, wc.ConfigError) else "operation failed; inspect private target logs and reconcile before retrying"
        print(json.dumps({"error": safe, "resources_may_remain": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
