#!/usr/bin/env python3
"""Safely update one activity's assignment in an installed shared portal."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import zlib
from urllib.parse import urlsplit

import workshop as lifecycle
import workshop_access as access
import workshop_bindings_update as bindings
import workshop_bindings_review as review
import workshop_config as wc
import workshop_preflight as pf
import workshop_shared_cleanup as shared_cleanup
import workshop_ttl as ttl


def require(value, message):
    if not value:
        raise wc.ConfigError("portal update: " + message)


def verify(controller, bindings_run, current_route, route, identity, subjects, previous, *, remove_activity=False):
    require(controller.binding.get("tool_sha256") == pf.tool_hash(), "portal source changed since the reviewed pilot plan")
    variables = bindings.verify_route(controller, route)
    current = bindings.verify_route(controller, current_route)
    c, shared = controller.config, controller.binding["shared_access"]
    require(wc.load_document(Path(bindings_run) / "inputs.tfvars.json") == current, "current route differs from active binding inputs")
    if current != variables:
        review.difference(current, variables)
    backend = wc.load_document(Path(bindings_run) / "backend.tfbackend.json")
    require(all(backend.get(k) == v for k, v in {"bucket": controller.pilot["state_bucket_name"], "region": c["region"],
        "profile": c["aws_profile"], "key": f"aws-workshop/aws/{c['aws_account_id']}/{c['region']}/{c['event_id']}/access.tfstate",
        "allowed_account_ids": [c["aws_account_id"]], "encrypt": True, "use_lockfile": True}.items()), "binding backend differs")
    with tempfile.TemporaryDirectory(prefix="workshop-portal-state-") as temp:
        tf = shared_cleanup.Terraform("access-bindings", temp)
        tf.init(Path(bindings_run).resolve() / "backend.tfbackend.json")
        state = tf.state()
    bindings.verify_state(controller, state, current)
    pools = [r["values"] for r in state["resources"] if r["type"] == "aws_cognito_user_pool"]
    clients = [r["values"] for r in state["resources"] if r["type"] == "aws_cognito_user_pool_client"]
    require(len(pools) == len(clients) == 1 and pools[0]["id"] == identity.get("user_pool_id") and
            clients[0]["id"] == identity.get("client_id") and clients[0]["user_pool_id"] == pools[0]["id"],
            "identity does not match the activity binding state")
    portal = shared.get("portal")
    require(isinstance(portal, dict) and portal.get("instance_id") and portal.get("target_group_arn"), "shared portal is not bound")
    instances, _ = controller.inventory()
    require(all(instances.get(k) == v for k, v in variables["learners"].items()), "portal targets do not match current pilot instances")
    actual = controller.actual_instances(instances.values())
    require(set(actual) == set(instances.values()) and all(v.get("State", {}).get("Name") == "running" for v in actual.values()),
            "pilot instances are not running")
    for learner, instance_id in instances.items():
        tags = {tag["Key"]: tag["Value"] for tag in actual[instance_id].get("Tags", [])}
        require(tags.get("Learner") == learner, "live instance learner tag differs")
    rows = controller.cloud.call("ec2", "describe_instances", InstanceIds=[portal["instance_id"]])["Reservations"]
    hosts = [i for r in rows for i in r["Instances"]]
    require(len(hosts) == 1 and hosts[0]["InstanceId"] == portal["instance_id"] and
            hosts[0].get("State", {}).get("Name") == "running" and hosts[0].get("VpcId") == shared["vpc_id"] and
            {g["GroupId"] for g in hosts[0].get("SecurityGroups", [])} == {shared["portal_security_group_id"]},
            "portal host or network differs from the shared entry")
    tags = {t["Key"]: t["Value"] for t in hosts[0].get("Tags", [])}
    require(all(tags.get(k) == v for k, v in {"Project": "doris-workshop", "Scope": "shared-portal",
            "Owner": c["owner_id"], "Access": shared["access_id"]}.items()) and "Event" not in tags,
            "portal host is not operator-owned")
    pool = identity.get("user_pool_id", "")
    require(isinstance(pool, str) and re.fullmatch(re.escape(c["region"]) + r"_[A-Za-z0-9]+", pool) and
            identity.get("event_id") == c["event_id"] and
            identity.get("issuer") == f'https://cognito-idp.{c["region"]}.amazonaws.com/{pool}', "identity belongs to another activity")
    client = controller.cloud.client("cognito-idp")
    arn = f'arn:aws:cognito-idp:{c["region"]}:{c["aws_account_id"]}:userpool/{pool}'
    pool_tags = client.list_tags_for_resource(ResourceArn=arn)["Tags"]
    require(all(pool_tags.get(k) == v for k, v in {"Project": "doris-workshop", "Scope": "activity-access",
            "Event": c["event_id"], "Owner": c["owner_id"]}.items()), "Cognito pool ownership differs")
    actual_client = client.describe_user_pool_client(UserPoolId=pool, ClientId=identity["client_id"])["UserPoolClient"]
    require(actual_client["ClientId"] == identity["client_id"] and actual_client["UserPoolId"] == pool, "Cognito client differs")
    login = urlsplit(identity["login_origin"])
    suffix = f'.auth.{c["region"]}.amazoncognito.com'
    require(login.scheme == "https" and login.hostname and login.hostname.endswith(suffix) and
            login.netloc == login.hostname and not login.path and not login.query and not login.fragment,
            "invalid Cognito login origin")
    require(client.describe_user_pool_domain(Domain=login.hostname.removesuffix(suffix)).get("DomainDescription", {}).get("UserPoolId") == pool,
            "Cognito domain differs")
    for learner in sorted(variables["learners"]):
        user = client.admin_get_user(UserPoolId=pool, Username=learner)
        actual_subject = [a["Value"] for a in user.get("UserAttributes", []) if a.get("Name") == "sub"]
        require(user.get("Username") == learner and user.get("Enabled") is True and actual_subject == subjects.get(learner),
                "Cognito subject differs from the assigned learner")
    sys.path.insert(0, str(access.OVERLAY))
    from portal import validate_config
    validate_config(previous)
    require(previous["domain"] == c["domain"] and previous["alb_arn"] == shared["alb_arn"] and
            previous["region"] == c["region"], "previous portal registry belongs to another entry")
    activity = access.portal_config([route], [identity], [subjects])["activities"][c["event_id"]]
    if remove_activity:
        require(current == variables and previous["activities"].get(c["event_id"]) == activity,
                "remove only the exact previously registered activity assignment")
        activities = {k: v for k, v in previous["activities"].items() if k != c["event_id"]}
    else:
        activities = {**previous["activities"], c["event_id"]: activity}
    next_registry = {**previous, "activities": activities}
    if any('ui_snapshot' in a for a in activities.values()):
        next_registry['schema_version'] = 2
    validate_config(next_registry)
    controller.save()
    return portal["instance_id"], next_registry


def remote_commands(old_bytes, new_bytes):
    old_hash = hashlib.sha256(old_bytes).hexdigest()
    new_hash = hashlib.sha256(new_bytes).hexdigest()
    try:
        compressed = json.loads(new_bytes).get('schema_version') == 2
    except (ValueError, AttributeError):
        compressed = False
    require(len(new_bytes) <= 1024 * 1024, 'registry exceeds its decoded size bound')
    encoded = base64.b64encode(zlib.compress(new_bytes) if compressed else new_bytes).decode()
    portal_hash = pf.file_hash(access.OVERLAY / "portal.py")
    gateway_hash = pf.file_hash(access.OVERLAY / "gateway.py")
    code = """import base64,fcntl,grp,hashlib,os,pathlib,subprocess,tempfile,time,urllib.request,zlib
old_hash=OLD_HASH
new_hash=NEW_HASH
payload=base64.b64decode(PAYLOAD,validate=True)
if COMPRESSED: payload=zlib.decompress(payload)
assert hashlib.sha256(payload).hexdigest()==new_hash
path=pathlib.Path('/etc/doris-workshop-portal/portal.json')
with open('/run/lock/doris-workshop-portal-registry.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 assert hashlib.sha256(pathlib.Path('/opt/doris-workshop-portal/portal.py').read_bytes()).hexdigest()==PORTAL_HASH, 'portal source changed'
 assert hashlib.sha256(pathlib.Path('/opt/doris-workshop-portal/gateway.py').read_bytes()).hexdigest()==GATEWAY_HASH, 'gateway source changed'
 assert hashlib.sha256(path.read_bytes()).hexdigest()==old_hash, 'portal registry changed; reconcile'
 fd,name=tempfile.mkstemp(prefix='.portal-next-',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as out:
   out.write(payload);out.flush();os.fsync(out.fileno())
  subprocess.run(['/opt/doris-workshop-portal/venv/bin/python','-I','-c',"import json,sys;sys.path.insert(0,'/opt/doris-workshop-portal');from portal import validate_config;validate_config(json.load(open(sys.argv[1])))",name],check=True)
  os.chown(name,0,grp.getgrnam('doris-portal').gr_gid);os.chmod(name,0o640);os.replace(name,path)
  subprocess.run(['systemctl','restart','doris-workshop-portal.service'],check=True)
  subprocess.run(['systemctl','is-active','--quiet','doris-workshop-portal.service'],check=True)
  for attempt in range(10):
   try:
    with urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=5) as response: assert response.status==200
    break
   except Exception:
    if attempt==9: raise
    time.sleep(1)
  print('portal-update-ready')
 finally:
  if os.path.exists(name): os.unlink(name)
""".replace("OLD_HASH", repr(old_hash)).replace("NEW_HASH", repr(new_hash)).replace("PORTAL_HASH", repr(portal_hash)).replace("GATEWAY_HASH", repr(gateway_hash)).replace("COMPRESSED", repr(compressed)).replace("PAYLOAD", repr(encoded))
    commands = ["set -eu", "umask 077", "python3.12 - <<'PY'\n" + code + "\nPY"]
    require(len(json.dumps(commands)) < 24000, "registry exceeds the bounded SSM payload")
    return commands, old_hash, new_hash


def update(controller, bindings_run, current_route, route, identity, subjects, previous_file, output, *, remove_activity=False, timeout=300, clock=time.monotonic, sleep=time.sleep):
    output, previous_file = Path(output).resolve(), Path(previous_file).resolve()
    previous_bytes = previous_file.read_bytes()
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "previous registry contains duplicate JSON keys")
            value[key] = item
        return value
    previous = json.loads(previous_bytes, object_pairs_hook=unique_object)
    instance, registry = verify(controller, bindings_run, current_route, route, identity, subjects, previous, remove_activity=remove_activity)
    # Stable bytes are also retained locally so an interrupted update can be reconciled.
    data = (json.dumps(registry, indent=2, sort_keys=True) + "\n").encode()
    commands, old_hash, new_hash = remote_commands(previous_bytes, data)
    record_file = output / "dispatch.json"
    if output.exists():
        require(record_file.is_file(), "incomplete prior operation; reconcile before retrying")
        record = wc.load_document(record_file)
        require(record.get("instance_id") == instance and record.get("old_sha256") == old_hash and
                record.get("new_sha256") == new_hash, "operation directory belongs to different inputs")
        require(record.get("status") != "dispatching" and record.get("command_id"), "dispatch outcome unknown; reconcile SSM history")
        if record.get("status") == "Success":
            return {"status": "Success", "reused_receipt": True, "portal_instance_id": instance,
                    "authenticated_access_verified": False}
        require(record.get("status") in {"Pending", "InProgress", "Delayed", "Cancelling"},
                "failed portal update requires reconciliation and a new operation")
    else:
        output.mkdir(parents=True, mode=0o700)
        (output / "next-portal.json").write_bytes(data)
        os.chmod(output / "next-portal.json", 0o600)
        record = {"instance_id": instance, "old_sha256": old_hash, "new_sha256": new_hash, "status": "dispatching"}
        lifecycle.atomic_json(record_file, record)
        result = controller.cloud.dispatch_client().send_command(InstanceIds=[instance], DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands, "executionTimeout": ["180"]}, TimeoutSeconds=180)
        record.update(status="Pending", command_id=result["Command"]["CommandId"])
        lifecycle.atomic_json(record_file, record)
    deadline = clock() + timeout
    while True:
        try:
            result = controller.cloud.client("ssm").get_command_invocation(CommandId=record["command_id"], InstanceId=instance)
        except controller.cloud.client("ssm").exceptions.InvocationDoesNotExist:
            result = {"Status": "Pending"}
        status = result["Status"]
        require(status in {"Pending", "InProgress", "Delayed", "Cancelling", "Success", "Failed", "Cancelled", "TimedOut"},
                "unknown SSM status; reconcile before retrying")
        record["status"] = "Failed" if status == "Success" and "portal-update-ready" not in result.get("StandardOutputContent", "") else status
        lifecycle.atomic_json(record_file, record)
        if status in {"Success", "Failed", "Cancelled", "TimedOut"}:
            break
        require(clock() < deadline, "portal operation is pending; reconcile before retrying")
        sleep(min(5, max(0, deadline - clock())))
    require(record["status"] == "Success", "portal update failed or returned an invalid result; reconcile the remote registry")
    return {"status": "Success", "reused_receipt": False, "portal_instance_id": instance,
            "authenticated_access_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pilot-run", "bindings-run", "current-route", "plan", "identity", "subjects", "previous-registry", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ttl-binding", type=Path)
    parser.add_argument("--remove-activity", action="store_true", help="Remove only this exact registered activity before deleting its bindings")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        require(1 <= args.timeout <= 3600, "timeout must be between 1 and 3600 seconds")
        with lifecycle.run_lock(args.pilot_run):
            controller = lifecycle.Controller(args.pilot_run, ttl_binding=args.ttl_binding)
            with ttl.activity_operation(controller.cloud, controller.config, controller.pilot, args.ttl_binding, scope_change=True):
                result = update(controller, args.bindings_run, wc.load_document(args.current_route), wc.load_document(args.plan),
                    wc.load_document(args.identity), wc.load_document(args.subjects), args.previous_registry, args.output,
                    remove_activity=args.remove_activity, timeout=args.timeout)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        safe = str(exc) if isinstance(exc, wc.ConfigError) else "operation failed; inspect private state and reconcile before retrying"
        print(json.dumps({"error": safe, "resources_may_remain": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
