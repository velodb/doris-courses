#!/usr/bin/env python3
"""Review and create one activity's initial authenticated bindings under a fence."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

import workshop as lifecycle
import workshop_bindings_update as update
import workshop_config as wc
import workshop_preflight as pf
import workshop_shared_cleanup as shared_cleanup
import workshop_ttl as ttl


def require(value, message):
    if not value:
        raise wc.ConfigError("bindings create: " + message)


def address(typ, key):
    return "aws_" + typ + ".learner[" + json.dumps(key) + "]"


def context(controller, bindings_run, route):
    c = controller.config
    require(controller.binding.get("tool_sha256") == pf.tool_hash(), "source differs from the reviewed pilot")
    for key, filename in (("shared_access_sha256", controller.pilot.get("shared_access_file")),
                          ("browser_routes_sha256", controller.pilot.get("browser_routes_file")),
                          ("image_release_sha256", controller.pilot.get("image_release_file"))):
        if key in controller.binding:
            require(filename and pf.file_hash(Path(filename)) == controller.binding[key], "bound pilot input changed")
    if "runtime_sha256" in controller.binding:
        require(pf.file_hash(Path(controller.pilot["runtime_bundle_dir"]) / "runtime.json") == controller.binding["runtime_sha256"],
                "bound runtime manifest changed")
    variables = update.verify_route(controller, route)
    directory = Path(bindings_run).resolve()
    require(wc.load_document(directory / "inputs.tfvars.json") == variables and
            route.get("browser_routes") == controller.binding.get("browser_routes"), "route inputs differ from the pilot binding")
    backend = wc.load_document(directory / "backend.tfbackend.json")
    require(all(backend.get(k) == v for k, v in {"bucket": controller.pilot["state_bucket_name"], "region": c["region"],
        "profile": c["aws_profile"], "key": f"aws-workshop/aws/{c['aws_account_id']}/{c['region']}/{c['event_id']}/access.tfstate",
        "allowed_account_ids": [c["aws_account_id"]], "encrypt": True, "use_lockfile": True}.items()), "binding backend differs")
    instances, _ = controller.inventory()
    require(instances == variables["learners"] and set(instances) == set(c["learner_ids"]) and
            controller.record.get("phase") == "ready", "pilot roster is not ready or differs from the routes")
    actual = controller.actual_instances(instances.values())
    require(set(actual) == set(instances.values()) and all(i.get("State", {}).get("Name") == "running" for i in actual.values()),
            "assigned pilot instances are not running")
    for learner, instance_id in instances.items():
        tags = {t["Key"]: t["Value"] for t in actual[instance_id].get("Tags", [])}
        require(tags.get("Learner") == learner, "live learner tag differs")
    controller.save()
    return variables


def headroom(controller, variables):
    cloud, shared = controller.cloud, variables["access"]
    lbs = cloud.call("elbv2", "describe_load_balancers", LoadBalancerArns=[shared["alb_arn"]])["LoadBalancers"]
    require(len(lbs) == 1 and lbs[0].get("VpcId") == shared["vpc_id"] and lbs[0].get("State", {}).get("Code") == "active" and
            lbs[0].get("DNSName") == shared["alb_dns_name"] and lbs[0].get("CanonicalHostedZoneId") == shared["alb_zone_id"],
            "live ALB differs from the bound shared entry")
    tag_rows = cloud.call("elbv2", "describe_tags", ResourceArns=[shared["alb_arn"]])["TagDescriptions"]
    tags = {t["Key"]: t["Value"] for row in tag_rows if row.get("ResourceArn") == shared["alb_arn"] for t in row.get("Tags", [])}
    require(all(tags.get(k) == v for k, v in {"Project": "doris-workshop", "Scope": "shared-access",
            "Owner": controller.config["owner_id"], "Access": shared["access_id"]}.items()), "ALB ownership differs")
    listeners = cloud.items("elbv2", "describe_listeners", "Listeners", LoadBalancerArn=shared["alb_arn"])
    selected = [row for row in listeners if row["ListenerArn"] == shared["https_listener_arn"]]
    require(len(selected) == 1 and selected[0].get("Protocol") == "HTTPS" and selected[0].get("Port") == 443,
            "HTTPS listener differs from the shared entry")
    hosts = {v["hostname"] for v in variables["routes"].values()}
    routes = dict(variables["routes"])
    if variables.get("portal"):
        hosts.add(variables["portal"]["hostname"])
        routes["portal"] = variables["portal"]
    priorities = {v["priority"] + offset for v in routes.values() for offset in (0, 1, 2)}
    require(len(priorities) == 3 * len(routes) and len(hosts) == len(routes) and len(hosts) <= 100,
            "host or listener priorities overlap")
    rules = cloud.items("elbv2", "describe_rules", "Rules", ListenerArn=shared["https_listener_arn"])
    used = {int(r["Priority"]) for r in rules if r.get("Priority") != "default"}
    require(not priorities & used, "listener priorities are already in use")
    limits = {r["Name"]: int(r["Max"]) for r in cloud.items("elbv2", "describe_account_limits", "Limits")}
    groups = cloud.items("elbv2", "describe_target_groups", "TargetGroups")
    count = sum(len([r for r in (rules if listener["ListenerArn"] == shared["https_listener_arn"] else
                cloud.items("elbv2", "describe_rules", "Rules", ListenerArn=listener["ListenerArn"]))
                if r.get("Priority") != "default"]) for listener in listeners)
    require(count + len(priorities) <= limits["rules-per-application-load-balancer"] and
            len(groups) + len(variables["learners"]) <= limits["target-groups"], "ALB rule or target-group limit exceeded")
    zone = cloud.call("route53", "get_hosted_zone", Id=shared["hosted_zone_id"])["HostedZone"]
    require(zone["Name"].rstrip(".") == controller.config["domain"] and not zone.get("Config", {}).get("PrivateZone"),
            "hosted zone differs from the activity domain")
    records = cloud.items("route53", "list_resource_record_sets", "ResourceRecordSets", HostedZoneId=shared["hosted_zone_id"])
    require(not hosts & {r["Name"].rstrip(".") for r in records}, "an assigned DNS name already exists")


def validate_plan(variables, plan):
    require(not plan.get("errored") and plan.get("complete", True) is True and plan.get("applyable", True) is True and
            not plan.get("deferred_changes") and not [d for d in plan.get("resource_drift", []) if d.get("mode") == "managed"],
            "plan is incomplete, deferred or has managed drift")
    require(all(plan.get("variables", {}).get(k, {}).get("value") == v for k, v in variables.items()), "plan variables differ")
    hosts = dict(variables["routes"])
    if variables.get("portal"):
        hosts["portal"] = variables["portal"]
    expected = {"aws_cognito_user_pool.activity", "aws_cognito_user_pool_client.alb", "aws_cognito_user_pool_domain.activity"}
    for learner in variables["learners"]:
        expected.update((address("lb_target_group", learner), address("lb_target_group_attachment", learner)))
    for key in hosts:
        expected.add(address("route53_record", key))
        expected.update(address("lb_listener_rule", key + "-" + kind) for kind in ("logout", "api", "page"))
    rows = [r for r in plan.get("resource_changes", []) if r.get("mode") == "managed"]
    require(len(rows) == len(expected) and {r["address"] for r in rows} == expected and
            all(r["change"]["actions"] == ["create"] for r in rows), "plan must create only the exact activity bindings")
    for row in rows:
        typ, key, after = row["type"], row.get("index"), row["change"]["after"]
        fixed = {"aws_cognito_user_pool": "aws_cognito_user_pool.activity", "aws_cognito_user_pool_client": "aws_cognito_user_pool_client.alb",
                 "aws_cognito_user_pool_domain": "aws_cognito_user_pool_domain.activity"}
        require(row["address"] == (fixed[typ] if typ in fixed else typ + ".learner[" + json.dumps(key) + "]"), "ambiguous resource address")
        if typ == "aws_cognito_user_pool":
            require(after["name"] == "doris-workshop-" + variables["event_id"] and
                    after["admin_create_user_config"][0]["allow_admin_create_user_only"] is True, "user pool allows unowned registration")
        elif typ == "aws_cognito_user_pool_client":
            require(after["generate_secret"] is True and after["allowed_oauth_flows"] == ["code"], "invalid ALB OAuth client")
            for field, suffix in (("callback_urls", "/oauth2/idpresponse"), ("logout_urls", "/_workshop/logged-out")):
                require(set(after[field]) == {"https://" + r["hostname"] + suffix for r in hosts.values()}, "OAuth hosts differ")
        elif typ == "aws_cognito_user_pool_domain":
            require(after["domain"] == "dws-" + variables["event_id"] + "-" + variables["expected_account_id"], "user-pool domain differs")
        elif typ == "aws_lb_target_group_attachment":
            require(after["target_id"] == variables["learners"][key] and after["port"] == 8080, "attachment targets another instance")
        elif typ == "aws_lb_target_group":
            require(after["vpc_id"] == variables["access"]["vpc_id"] and after["port"] == 8080 and
                    after["protocol"] == "HTTP" and after["target_type"] == "instance", "target group differs from the shared network")
        elif typ == "aws_route53_record":
            alias = after["alias"]
            require(after["zone_id"] == variables["access"]["hosted_zone_id"] and after["type"] == "A" and
                after["name"].rstrip(".") == hosts[key]["hostname"] and len(alias) == 1 and
                alias[0]["name"].rstrip(".") == variables["access"]["alb_dns_name"].rstrip(".") and
                alias[0]["zone_id"] == variables["access"]["alb_zone_id"], "DNS target differs")
        elif typ == "aws_lb_listener_rule":
            key, kind = key.rsplit("-", 1)
            require(kind in {"logout", "api", "page"} and key in hosts and
                after["listener_arn"] == variables["access"]["https_listener_arn"] and
                after["priority"] == hosts[key]["priority"] + {"logout": 0, "api": 1, "page": 2}[kind], "listener assignment differs")
            host_values = [h for condition in after["condition"] for block in condition.get("host_header", []) for h in block["values"]]
            require(host_values == [hosts[key]["hostname"]], "listener host differs")
            actions = after["action"]
            if kind == "logout":
                require(len(actions) == 1 and actions[0]["type"] == "fixed-response", "logout rule differs")
            else:
                require(len(actions) == 2 and [a["type"] for a in actions] == ["authenticate-cognito", "forward"] and
                    actions[0]["authenticate_cognito"][0]["on_unauthenticated_request"] == ("deny" if kind == "api" else "authenticate"),
                    "listener must authenticate before forwarding")
    return [{"address": r["address"], "actions": r["change"]["actions"]} for r in rows]


def hashes(controller, bindings_run, route_path):
    directory = Path(bindings_run)
    return {"pilot_binding": pf.file_hash(controller.directory / "binding.json"),
            "bindings_vars": pf.file_hash(directory / "inputs.tfvars.json"),
            "bindings_backend": pf.file_hash(directory / "backend.tfbackend.json"), "route": pf.file_hash(route_path),
            "module": pf.module_hash("access-bindings"), "tools": pf.tool_hash()}


def refresh_preflight(controller):
    report = pf.live_check(controller.config, controller.pilot, controller.cloud, "pilot")
    variables = wc.load_document(controller.directory / "inputs.tfvars.json")
    require(report.get("status") == "passed" and variables["availability_zone"] in report["availability_zones"],
            "live cost, permissions or placement preflight did not pass")
    return report


def prepare(controller, bindings_run, route_path, output, *, tf_factory=shared_cleanup.Terraform, now=None):
    output, route_path = Path(output).resolve(), Path(route_path).resolve()
    require(not output.exists(), "use a fresh private review directory")
    variables = context(controller, bindings_run, wc.load_document(route_path))
    preflight = refresh_preflight(controller)
    headroom(controller, variables)
    files = hashes(controller, bindings_run, route_path)
    output.mkdir(parents=True, mode=0o700)
    wc.write_manifest(output / "preflight.json", preflight)
    tf = tf_factory("access-bindings", output)
    tf.init(Path(bindings_run).resolve() / "backend.tfbackend.json")
    require(not tf.state()["resources"], "binding state is not empty")
    path = output / "create.tfplan"
    tf.call(["plan", "-input=false", "-lock-timeout=60s", f"-var-file={Path(bindings_run).resolve() / 'inputs.tfvars.json'}", f"-out={path}"])
    path.chmod(0o600)
    plan = json.loads(tf.call(["show", "-json", path], capture=True))
    changes = validate_plan(variables, plan)
    require(files == hashes(controller, bindings_run, route_path), "inputs changed while planning")
    now = now or datetime.now(timezone.utc)
    receipt = {"schema_version": 1, "kind": "aws-workshop-bindings-create-review", "event_id": controller.config["event_id"],
        "valid_until": wc.utc_text(min(now + timedelta(minutes=15), wc.timestamp(preflight["valid_until"], "preflight.valid_until"))),
        "artifacts": files, "changes": changes, "preflight_sha256": pf.file_hash(output / "preflight.json"),
        "plan_sha256": pf.file_hash(path), "initialized_backend_sha256": pf.file_hash(output / "terraform-data/terraform.tfstate")}
    wc.write_manifest(output / "review.json", receipt)
    return {"review_sha256": pf.file_hash(output / "review.json"), "changes": changes, "valid_until": receipt["valid_until"]}


def apply(controller, bindings_run, route_path, output, approved_hash, *, tf_factory=shared_cleanup.Terraform, now=None):
    output, route_path = Path(output).resolve(), Path(route_path).resolve()
    receipt_path = output / "review.json"
    require(approved_hash and pf.file_hash(receipt_path) == approved_hash, "supply the reviewed receipt hash")
    receipt = wc.load_document(receipt_path)
    require(receipt.get("kind") == "aws-workshop-bindings-create-review" and receipt.get("schema_version") == 1 and
        receipt.get("event_id") == controller.config["event_id"] and
        wc.timestamp(receipt["valid_until"], "valid_until") > (now or datetime.now(timezone.utc)), "receipt is invalid or expired")
    require(receipt["artifacts"] == hashes(controller, bindings_run, route_path) and
        receipt["plan_sha256"] == pf.file_hash(output / "create.tfplan") and
        receipt["preflight_sha256"] == pf.file_hash(output / "preflight.json") and
        receipt["initialized_backend_sha256"] == pf.file_hash(output / "terraform-data/terraform.tfstate"), "bound inputs changed")
    variables = context(controller, bindings_run, wc.load_document(route_path))
    current_preflight = refresh_preflight(controller)
    headroom(controller, variables)
    tf = tf_factory("access-bindings", output)
    require(not tf.state()["resources"], "binding state changed since review")
    plan = json.loads(tf.call(["show", "-json", output / "create.tfplan"], capture=True))
    require(validate_plan(variables, plan) == receipt["changes"], "saved plan differs from the review")
    progress = output / "progress.json"
    require(not progress.exists(), "previous apply attempted; reconcile before another plan")
    checked_at = now or datetime.now(timezone.utc)
    require(wc.timestamp(receipt["valid_until"], "valid_until") > checked_at and
            wc.timestamp(current_preflight["valid_until"], "preflight.valid_until") > checked_at and
            receipt["artifacts"] == hashes(controller, bindings_run, route_path) and
            receipt["plan_sha256"] == pf.file_hash(output / "create.tfplan"), "review or preflight expired or inputs changed")
    wc.write_manifest(output / "apply-preflight.json", current_preflight)
    lifecycle.atomic_json(progress, {"phase": "applying", "resources_may_remain": True})
    tf.apply(output / "create.tfplan")
    update.verify_state(controller, tf.state(), variables)
    identity = json.loads(tf.call(["output", "-json", "identity"], capture=True))
    require(identity.get("event_id") == controller.config["event_id"], "identity output differs")
    wc.write_manifest(output / "identity.json", identity)
    lifecycle.atomic_json(progress, {"phase": "complete", "resources_may_remain": True})
    return {"phase": "complete", "identity_file": str(output / "identity.json"), "authenticated_access_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply"))
    for name in ("pilot-run", "bindings-run", "route", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ttl-binding", type=Path)
    parser.add_argument("--approved-review-sha256")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        with lifecycle.run_lock(args.pilot_run):
            controller = lifecycle.Controller(args.pilot_run, ttl_binding=args.ttl_binding)
            with ttl.activity_operation(controller.cloud, controller.config, controller.pilot, args.ttl_binding, scope_change=True):
                result = (prepare(controller, args.bindings_run, args.route, args.output) if args.command == "plan" else
                          apply(controller, args.bindings_run, args.route, args.output, args.approved_review_sha256))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        safe = str(exc) if isinstance(exc, wc.ConfigError) else "operation failed; inspect private state and reconcile before retrying"
        print(json.dumps({"error": safe, "resources_may_remain": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
