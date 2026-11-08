#!/usr/bin/env python3
"""Review and apply one activity's learner access update under the activity fence."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

import workshop as lifecycle
import workshop_access as access
import workshop_bindings_review as review
import workshop_cleanup as cleanup
import workshop_config as wc
import workshop_preflight as pf
import workshop_shared_cleanup as shared_cleanup
import workshop_ttl as ttl


def require(value, message):
    if not value:
        raise wc.ConfigError("bindings update: " + message)


def verify_route(controller, route):
    """Regenerate Terraform inputs from fixed service names and the saved ledger."""
    config = dict(controller.config)
    shared = controller.binding.get("shared_access")
    variables = route["terraform"]
    require(isinstance(shared, dict) and variables.get("access") == shared and
            shared.get("dns_account_id", config["aws_account_id"]) == config["aws_account_id"] and not variables.get("dns"),
            "use the bound shared entry in the same DNS account")
    require(route.get("event_id") == config["event_id"] and route.get("expires_at") == config["expires_at"] and
            route.get("alb_arn") == shared["alb_arn"] and route.get("region") == config["region"], "route target differs")
    config["learner_ids"] = sorted(variables["learners"])
    ledger = route["routing_allocations"]
    allocation = ledger["events"][config["event_id"]]
    regenerated = access.routes(config, shared, variables["learners"], priority_base=allocation["priority_base"],
                                priority_limit=allocation["priority_limit"], previous=ledger,
                                runtime_bundle=controller.pilot.get('runtime_bundle_dir'))
    if config.get('schema_version', 1) == 2:
        require(route.get('schema_version') == 2 and route.get('content') == regenerated['content'] and
                route.get('ui_snapshot') == regenerated['ui_snapshot'], 'entry content differs from the bound runtime')
    require(regenerated["terraform"] == variables and regenerated["routing_allocations"] == ledger,
            "routes differ from fixed service generation and ledger")
    return variables


def verify_context(controller, bindings_run, old_route, new_route):
    directory = Path(bindings_run).resolve()
    old, new = verify_route(controller, old_route), verify_route(controller, new_route)
    require(wc.load_document(directory / "inputs.tfvars.json") == old, "old routes do not match the active binding inputs")
    backend = wc.load_document(directory / "backend.tfbackend.json")
    c = controller.config
    require(all(backend.get(k) == v for k, v in {
        "bucket": controller.pilot["state_bucket_name"], "region": c["region"], "profile": c["aws_profile"],
        "key": f"aws-workshop/aws/{c['aws_account_id']}/{c['region']}/{c['event_id']}/access.tfstate",
        "allowed_account_ids": [c["aws_account_id"]], "encrypt": True, "use_lockfile": True}.items()),
        "bindings backend differs from this activity")
    change = review.difference(old, new)
    instances, _ = controller.inventory()
    intended = old["learners"] if change["operation"] == "remove" else new["learners"]
    require(instances == intended and set(instances) == set(c["learner_ids"]), "current pilot instances differ from the required roster")
    actual = controller.actual_instances(instances.values())
    require(set(actual) == set(instances.values()) and all(v.get("State", {}).get("Name") == "running" for v in actual.values()),
            "current pilot targets are not running and activity-owned")
    for learner, instance_id in instances.items():
        tags = {tag["Key"]: tag["Value"] for tag in actual[instance_id].get("Tags", [])}
        require(tags.get("Learner") == learner, "live learner tag differs from the target assignment")
    controller.save()
    return old, new, change


def verify_state(controller, state, variables, *, missing_target=None):
    hosts = [r["hostname"] for r in variables["routes"].values()]
    if variables.get("portal"):
        hosts.append(variables["portal"]["hostname"])
    history = {"event_id": controller.config["event_id"], "known_instances": list(variables["learners"].values()),
               "nodes": {k: {} for k in variables["learners"]}}
    cleanup.prepare(controller.config, [state], state_bucket=controller.pilot["state_bucket_name"],
                    history=history, shared=variables["access"], hosts=hosts)
    resources = state["resources"]
    attachments = {r["index"]: r["values"]["target_id"] for r in resources if r["type"] == "aws_lb_target_group_attachment"}
    groups = {r["index"] for r in resources if r["type"] == "aws_lb_target_group"}
    expected_targets = {k: v for k, v in variables["learners"].items() if k != missing_target}
    require(attachments == expected_targets and groups == set(variables["learners"]),
            "state targets differ from the previous roster")
    return wc.canonical_hash(state)


def verify_plan_state(controller, state, old, plan, result):
    """Check refreshed ownership as well as the unrefreshed backend snapshot."""
    missing = result["learner"] if result["verified_old_attachment_drift"] else None
    prior = cleanup.state_export("access-bindings", plan["prior_state"])
    verify_state(controller, prior, old, missing_target=missing)
    before = {r["address"]: r for r in state["resources"] if r.get("mode", "managed") == "managed"}
    refreshed = {r["address"]: r for r in prior["resources"] if r.get("mode", "managed") == "managed"}
    absent = {review.address("lb_target_group_attachment", missing)} if missing else set()
    require(set(before) - absent == set(refreshed) and absent <= set(before), "refresh changed managed addresses")
    for name, row in refreshed.items():
        require(row["values"].get("id") == before[name]["values"].get("id"), "refresh changed resource identity")


def hashes(controller, bindings_run, old_path, new_path):
    directory = Path(bindings_run)
    return {"pilot_binding": pf.file_hash(controller.directory / "binding.json"),
            "bindings_vars": pf.file_hash(directory / "inputs.tfvars.json"),
            "bindings_backend": pf.file_hash(directory / "backend.tfbackend.json"),
            "old_route": pf.file_hash(old_path), "new_route": pf.file_hash(new_path),
            "module": pf.module_hash("access-bindings"), "tools": pf.tool_hash(),
            "reviewer": pf.file_hash(Path(review.__file__)), "updater": pf.file_hash(Path(__file__))}


def prepare(controller, bindings_run, old_path, new_path, output, *, tf_factory=shared_cleanup.Terraform, now=None):
    output = Path(output).resolve()
    require(not output.exists(), "use a fresh private review directory")
    old_path, new_path = Path(old_path).resolve(), Path(new_path).resolve()
    old, new, _ = verify_context(controller, bindings_run, wc.load_document(old_path), wc.load_document(new_path))
    before_hashes = hashes(controller, bindings_run, old_path, new_path)
    output.mkdir(parents=True, mode=0o700)
    wc.write_manifest(output / "next.tfvars.json", new)
    tf = tf_factory("access-bindings", output)
    tf.init(Path(bindings_run).resolve() / "backend.tfbackend.json")
    state = tf.state()
    state_hash = verify_state(controller, state, old)
    plan_path = output / "update.tfplan"
    tf.call(["plan", "-input=false", "-lock-timeout=60s", f"-var-file={output / 'next.tfvars.json'}", f"-out={plan_path}"])
    plan_path.chmod(0o600)
    plan = json.loads(tf.call(["show", "-json", plan_path], capture=True))
    result = review.validate_plan(old, new, plan)
    verify_plan_state(controller, state, old, plan, result)
    require(before_hashes == hashes(controller, bindings_run, old_path, new_path), "inputs changed while planning")
    now = now or datetime.now(timezone.utc)
    receipt = {"schema_version": 1, "kind": "aws-workshop-bindings-update-review", "event_id": controller.config["event_id"],
               "valid_until": wc.utc_text(now + timedelta(minutes=15)), "artifacts": before_hashes,
               "state_sha256": state_hash, "plan_sha256": pf.file_hash(plan_path),
               "next_vars_sha256": pf.file_hash(output / "next.tfvars.json"),
               "initialized_backend_sha256": pf.file_hash(output / "terraform-data/terraform.tfstate"), **result}
    wc.write_manifest(output / "review.json", receipt)
    return {"review_sha256": pf.file_hash(output / "review.json"), "operation": result["operation"],
            "learner": result["learner"], "changes": result["changes"], "valid_until": receipt["valid_until"]}


def apply(controller, bindings_run, old_path, new_path, output, approved_hash, *, tf_factory=shared_cleanup.Terraform, now=None):
    output, old_path, new_path = Path(output).resolve(), Path(old_path).resolve(), Path(new_path).resolve()
    receipt_path = output / "review.json"
    require(approved_hash and pf.file_hash(receipt_path) == approved_hash, "supply the reviewed receipt hash")
    receipt = wc.load_document(receipt_path)
    now = now or datetime.now(timezone.utc)
    require(receipt.get("kind") == "aws-workshop-bindings-update-review" and receipt.get("schema_version") == 1 and
            receipt.get("event_id") == controller.config["event_id"] and wc.timestamp(receipt["valid_until"], "valid_until") > now,
            "receipt is invalid or expired")
    require(receipt["artifacts"] == hashes(controller, bindings_run, old_path, new_path) and
            receipt["plan_sha256"] == pf.file_hash(output / "update.tfplan") and
            receipt["next_vars_sha256"] == pf.file_hash(output / "next.tfvars.json") and
            receipt["initialized_backend_sha256"] == pf.file_hash(output / "terraform-data/terraform.tfstate"),
            "inputs, initialized backend, source or saved plan changed")
    old, new, _ = verify_context(controller, bindings_run, wc.load_document(old_path), wc.load_document(new_path))
    require(wc.load_document(output / "next.tfvars.json") == new, "proposed variables changed")
    tf = tf_factory("access-bindings", output)
    state = tf.state()
    require(verify_state(controller, state, old) == receipt["state_sha256"], "binding state changed since review")
    plan = json.loads(tf.call(["show", "-json", output / "update.tfplan"], capture=True))
    result = review.validate_plan(old, new, plan)
    verify_plan_state(controller, state, old, plan, result)
    require(all(receipt.get(key) == result[key] for key in ("operation", "learner", "changes", "verified_old_attachment_drift")),
            "saved plan no longer matches the review")
    progress = output / "progress.json"
    require(not progress.exists(), "previous apply attempted; reconcile before creating another plan")
    require(wc.timestamp(receipt["valid_until"], "valid_until") > (now or datetime.now(timezone.utc)) and
            receipt["artifacts"] == hashes(controller, bindings_run, old_path, new_path) and
            receipt["plan_sha256"] == pf.file_hash(output / "update.tfplan"), "review expired or inputs changed before apply")
    lifecycle.atomic_json(progress, {"phase": "applying", "resources_may_remain": True})
    tf.apply(output / "update.tfplan")
    verify_state(controller, tf.state(), new)
    lifecycle.atomic_json(Path(bindings_run).resolve() / "inputs.tfvars.json", new)
    lifecycle.atomic_json(progress, {"phase": "complete", "resources_may_remain": True})
    return {"phase": "complete", "operation": result["operation"], "learner": result["learner"],
            "portal_assignment_updated": False, "authenticated_access_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply"))
    for name in ("pilot-run", "bindings-run", "old-route", "new-route", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ttl-binding", type=Path)
    parser.add_argument("--approved-review-sha256")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        with lifecycle.run_lock(args.pilot_run):
            controller = lifecycle.Controller(args.pilot_run, ttl_binding=args.ttl_binding)
            with ttl.activity_operation(controller.cloud, controller.config, controller.pilot, args.ttl_binding, scope_change=True):
                result = (prepare(controller, args.bindings_run, args.old_route, args.new_route, args.output) if args.command == "plan" else
                          apply(controller, args.bindings_run, args.old_route, args.new_route, args.output, args.approved_review_sha256))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        safe = str(exc) if isinstance(exc, wc.ConfigError) else "operation failed; inspect private state and reconcile before retrying"
        print(json.dumps({"error": safe, "resources_may_remain": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
