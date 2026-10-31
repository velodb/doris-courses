#!/usr/bin/env python3
"""Prepare and reconcile private cleanup evidence; never delete AWS resources."""
import argparse
import json
from pathlib import Path
import re
import sys

import workshop_config as wc

PILOT_NAMES = {
    "aws_vpc": "pilot", "aws_internet_gateway": "pilot", "aws_subnet": "pilot",
    "aws_route_table": "pilot", "aws_route_table_association": "pilot", "aws_security_group": "pilot",
    "aws_s3_bucket": "runtime", "aws_s3_bucket_public_access_block": "runtime",
    "aws_s3_bucket_server_side_encryption_configuration": "runtime", "aws_s3_bucket_policy": "runtime",
    "aws_s3_object": "runtime", "aws_iam_role": "learner", "aws_iam_role_policy": "learner",
    "aws_iam_instance_profile": "learner", "aws_instance": "learner", "aws_ssm_document": "install",
}
ACCESS_NAMES = {
    "aws_cognito_user_pool": "activity", "aws_cognito_user_pool_client": "alb", "aws_cognito_user_pool_domain": "activity",
    "aws_lb_target_group": "learner", "aws_lb_target_group_attachment": "learner",
    "aws_lb_listener_rule": "learner", "aws_route53_record": "learner",
}
KINDS = {
    "aws_instance": "instances", "aws_vpc": "vpcs", "aws_internet_gateway": "internet_gateways",
    "aws_subnet": "subnets", "aws_route_table": "route_tables", "aws_security_group": "security_groups",
    "aws_s3_bucket": "buckets", "aws_iam_role": "roles", "aws_iam_instance_profile": "instance_profiles",
    "aws_ssm_document": "ssm_documents", "aws_cognito_user_pool": "user_pools",
    "aws_lb_target_group": "target_groups", "aws_lb_listener_rule": "listener_rules", "aws_route53_record": "dns_records",
}
# Absence of these parents proves absence of their subordinate state objects.
DERIVED = {
    "aws_s3_bucket_public_access_block": ("buckets", "bucket"),
    "aws_s3_bucket_server_side_encryption_configuration": ("buckets", "bucket"),
    "aws_s3_bucket_policy": ("buckets", "bucket"), "aws_s3_object": ("buckets", "bucket"),
    "aws_iam_role_policy": ("roles", "role"), "aws_route_table_association": ("route_tables", "route_table_id"),
    "aws_cognito_user_pool_client": ("user_pools", "user_pool_id"),
    "aws_cognito_user_pool_domain": ("user_pools", "user_pool_id"),
    "aws_lb_target_group_attachment": ("target_groups", "target_group_arn"),
}
BASE_KINDS = {"instances", "volumes", "network_interfaces", "vpcs", "subnets", "route_tables", "security_groups",
    "internet_gateways", "nat_gateways", "addresses", "images", "snapshots", "key_pairs", "roles", "instance_profiles", "buckets", "ssm_documents", "tagged_resources"}
ACCESS_KINDS = {"load_balancers", "target_groups", "listener_rules", "target_attachments", "dns_records", "user_pools", "user_pool_clients", "user_pool_domains"}
ALL_KINDS = BASE_KINDS | ACCESS_KINDS
PROTECTED_SCOPES = {"foundation", "shared-access", "shared-portal", "image-release"}
TEXT = re.compile(r"[A-Za-z0-9_./:*+=@,\[\]-]{1,1024}")


def require(condition, message):
    if not condition:
        raise wc.ConfigError("cleanup: " + message)


def ref(kind, identity):
    require(kind in ALL_KINDS and isinstance(identity, str) and TEXT.fullmatch(identity), "invalid resource reference")
    return {"kind": kind, "id": identity}


def key(value):
    return value["kind"], value["id"]


def target(config):
    require(config["aws_partition"] == "aws", "only the commercial partition is implemented")
    return {name: config[name] for name in ("aws_account_id", "region", "event_id", "owner_id", "domain")}


def expected_tags(config, module, build_id=None):
    tags = {"Project": "doris-workshop", "Owner": config["owner_id"]}
    if module == "image-build":
        require(isinstance(build_id, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,31}", build_id), "supply the exact temporary image build ID")
        return {**tags, "ImageBuild": build_id, "Scope": "image-build"}
    return {**tags, "Event": config["event_id"], "Scope": {"pilot": "activity", "access-bindings": "activity-access", "image-validation": "image-validation"}[module]}


def project_state(config, resources, *, module="pilot", state_bucket, shared=None, hosts=(), build_id=None, defer_missing_parents=False):
    """Check taggable roots and exact parent references, without exporting secrets."""
    require(module in {"pilot", "access-bindings", "image-build", "image-validation"}, "shared/foundation states are retained")
    names = ACCESS_NAMES if module == "access-bindings" else dict(PILOT_NAMES)
    if module == "image-build":
        names.update(aws_iam_role="builder", aws_iam_role_policy="builder", aws_iam_instance_profile="builder")
        names.pop("aws_instance", None)  # Packer's host is not in this Terraform state.
    if module == "image-validation":
        for name in list(names):
            if name.startswith("aws_s3_"):
                names.pop(name)
        names.pop("aws_ssm_document", None)
    required = expected_tags(config, module, build_id)
    direct, result, addresses = {}, [], set()
    for row in resources:
        if row.get("mode", "managed") != "managed":
            continue
        typ, name, values = row["type"], row["name"], row["values"]
        require(names.get(typ) == name and not row.get("module_address"), "unexpected resource in the selected activity state")
        address = typ + "." + name + ("[" + json.dumps(row["index"]) + "]" if "index" in row else "")
        require(row["address"] == address and address not in addresses, "duplicate or inconsistent state address")
        addresses.add(address)
        if typ not in KINDS:
            require(typ in DERIVED, "unsupported state resource")
            continue
        kind = KINDS[typ]
        if values.get("arn"):
            arn = values["arn"].split(":", 5)
            require(len(arn) == 6 and arn[:2] == ["arn", "aws"] and arn[3] in {"", config["region"]} and
                arn[4] in {"", config["aws_account_id"]}, "state ARN target differs from the explicit account/Region")
        if kind in {"instances", "target_groups"}:
            require(row.get("index") in set(config["learner_ids"]) | set(config.get("cleanup_learner_ids", [])),
                "state includes an unassigned learner")
        identity = values.get("arn") if kind in {"target_groups", "listener_rules"} else values.get("id")
        if kind == "dns_records":
            require(shared is not None and values["zone_id"] == shared["hosted_zone_id"] and
                values["name"].rstrip(".") in hosts and values["type"] == "A", "DNS state must name an exact assigned service/entry record")
            aliases = values.get("alias", [])
            require(len(aliases) == 1 and aliases[0]["name"].rstrip(".") == shared["alb_dns_name"].rstrip(".") and
                aliases[0]["zone_id"] == shared["alb_zone_id"], "DNS alias differs from the selected shared ALB")
            identity = values["zone_id"] + "/" + values["name"].rstrip(".") + "/A"
        else:
            tags = values.get("tags_all") or values.get("tags") or {}
            require(all(tags.get(k) == v for k, v in required.items()), "missing or conflicting state ownership tags")
            require(tags.get("Scope") not in PROTECTED_SCOPES, "shared or retained resource found in activity state")
        if kind in {"listener_rules", "target_groups"}:
            require(identity.startswith(f'arn:aws:elasticloadbalancing:{config["region"]}:{config["aws_account_id"]}:'), "state ARN belongs to another account or Region")
        if kind == "listener_rules":
            rule_hosts = [host for condition in values.get("condition", []) for block in condition.get("host_header", []) for host in block["values"]]
            require(shared and values.get("listener_arn") == shared["https_listener_arn"] and len(rule_hosts) == 1 and
                set(rule_hosts) <= set(hosts), "listener rule must use this shared listener and an exact assigned host")
        item = {**ref(kind, identity), "address": address, "module": module, "parent": None}
        if kind == "dns_records":
            item["fields"] = {"alias_dns_name": aliases[0]["name"].rstrip("."), "alias_zone_id": aliases[0]["zone_id"]}
        require(not (kind == "buckets" and identity == state_bucket), "foundation state bucket found in activity state")
        if kind == "buckets" and "bucket" in values:
            require(values["bucket"] == identity, "bucket identity differs from its state ID")
        if shared:
            protected = {shared.get(name) for name in ("vpc_id", "alb_arn", "https_listener_arn", "hosted_zone_id", "gateway_security_group_id", "portal_security_group_id")}
            protected |= {shared.get("portal", {}).get("instance_id"), shared.get("portal", {}).get("target_group_arn")} if shared.get("portal") else set()
            require(identity not in protected, "shared resource found in activity state")
        direct[(kind, identity)] = item
        result.append(item)
    # A tagless dependent cannot grant ownership of a foreign parent.
    for row in resources:
        if row.get("mode", "managed") != "managed" or row["type"] not in DERIVED:
            continue
        kind, field = DERIVED[row["type"]]
        parent = ref(kind, row["values"][field])
        require(not (kind == "buckets" and parent["id"] == state_bucket), "foundation parent is never activity-owned")
        unproven = key(parent) not in direct
        require(not unproven or defer_missing_parents, "tagless state object has no owned parent in this state")
        if row["type"] == "aws_route_table_association":
            unproven |= ("subnets", row["values"]["subnet_id"]) not in direct
            require(not unproven or defer_missing_parents, "route association points to an unowned subnet")
        if row["type"] == "aws_lb_target_group_attachment":
            require(row["values"]["target_id"] in set(config.get("cleanup_instance_ids", [])) and row["values"]["port"] == 8080,
                "target attachment needs an exact verified learner instance")
        item = {"kind": "dependent", "id": row["address"], "address": row["address"], "module": module, "parent": parent}
        if unproven:
            item["requires_refresh_absence"] = True
        if row["type"] == "aws_cognito_user_pool_domain":
            item["observation"] = ref("user_pool_domains", row["values"]["domain"])
        if row["type"] == "aws_cognito_user_pool_client":
            item["observation"] = ref("user_pool_clients", row["values"]["user_pool_id"] + "/" + row["values"]["id"])
        result.append(item)
    return result


def validate_destroy_plan(resources, plan):
    require(not plan.get("errored") and plan.get("complete", True) is True and not plan.get("deferred_changes"), "destroy plan is incomplete or deferred")
    expected = {r["address"]: r for r in resources if r.get("mode", "managed") == "managed"}
    seen, absent = set(), set()
    rows = [(row, False) for row in plan.get("resource_changes", [])]
    rows += [(row, True) for row in plan.get("resource_drift", []) if row.get("mode") == "managed" and row["change"]["actions"] == ["delete"]]
    for row, drift in rows:
        if row.get("mode") != "managed":
            continue
        address = row["address"]
        require(address in expected and address not in seen and row["change"]["actions"] == ["delete"],
            "destroy plan contains an unowned, duplicate or non-delete action")
        seen.add(address)
        if drift:
            require(row["change"].get("after") is None, "refresh did not prove resource absence")
            absent.add(address)
        # The refresh must not swap an object between ownership check and delete.
        before = row["change"].get("before")
        require(isinstance(before, dict) and before.get("id") == expected[address]["values"].get("id"), "destroy plan refreshed a different object")
        for field in ("tags", "tags_all"):
            if expected[address]["values"].get(field):
                require(before.get(field) == expected[address]["values"][field], "destroy plan ownership changed during refresh")
        for field in ("bucket", "key", "role", "subnet_id", "route_table_id", "user_pool_id", "target_group_arn", "target_id",
                "port", "zone_id", "name", "type", "alias", "condition", "listener_arn", "unique_id", "arn"):
            if field in expected[address]["values"]:
                require(before.get(field) == expected[address]["values"][field], "destroy plan resource relationship changed during refresh")
    require(seen == set(expected), "destroy plan omits state resources")
    return absent


def prepare(config, states, *, state_bucket, history=None, shared=None, hosts=(), retained=()):
    identity = target(config)
    config = {**config, "cleanup_instance_ids": list(history.get("known_instances", [])) if history else [],
        "cleanup_learner_ids": list(history.get("nodes", {})) if history else []}
    config["cleanup_instance_ids"].extend(row["values"]["id"] for state in states for row in state["resources"] if row.get("type") == "aws_instance")
    if shared:
        require(shared["account_id"] == identity["aws_account_id"] and shared["region"] == identity["region"] and
            shared["domain"] == identity["domain"], "shared access target/namespace differs from the activity")
        require(all(host.endswith("." + identity["domain"]) and len(host.removesuffix("." + identity["domain"]).split(".")) == 1 and
            host.startswith(identity["event_id"] + "-") for host in hosts), "activity host claims cannot include the shared root or another namespace")
    retained = [ref(row["kind"], row["id"]) for row in retained]
    retained.append(ref("buckets", state_bucket))
    if shared:
        retained.append(ref("dns_records", shared["hosted_zone_id"] + "/" + identity["domain"] + "/A"))
        for field, kind in (("vpc_id", "vpcs"), ("alb_arn", "load_balancers"), ("gateway_security_group_id", "security_groups"), ("portal_security_group_id", "security_groups")):
            if shared.get(field):
                retained.append(ref(kind, shared[field]))
        if shared.get("portal"):
            retained.extend([ref("instances", shared["portal"]["instance_id"]), ref("target_groups", shared["portal"]["target_group_arn"])])
    projected, modules, build_ids = [], set(), set()
    for state in states:
        module = state["module"]
        require(module not in modules, "supply one exact state export per module")
        modules.add(module)
        projected.extend(project_state(config, state["resources"], module=module, state_bucket=state_bucket,
            shared=shared, hosts=hosts, build_id=state.get("build_id")))
        if module == "image-build":
            build_ids.add(state["build_id"])
    require(modules, "supply activity state exports, including empty states for the selected modules")
    known = [ref(row["kind"], row["id"]) for row in projected if row["kind"] != "dependent"]
    known.extend(row["observation"] for row in projected if "observation" in row)
    if shared:
        known.extend(ref("dns_records", shared["hosted_zone_id"] + "/" + host + "/A") for host in hosts)
    known.extend(ref("volumes", volume["volume_id"]) for state in states for row in state["resources"] if row.get("type") == "aws_instance"
        for volume in row["values"].get("root_block_device", []) if volume.get("volume_id"))
    if history:
        require(history["event_id"] == config["event_id"], "lifecycle history belongs to another activity")
        known.extend(ref(kind, value) for field, kind in (("known_instances", "instances"), ("known_volumes", "volumes")) for value in history.get(field, []))
        known.extend(ref("network_interfaces", value) for value in history.get("known_network_interfaces", {}))
        known.extend(ref(kind, value) for field, kind in (("known_images", "images"), ("known_snapshots", "snapshots"),
            ("known_key_pairs", "key_pairs")) for value in history.get(field, []))
        for value in history.get("image_build_ids", []):
            require(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,31}", value), "invalid historical image build ID")
            build_ids.add(value)
    retained_keys = {key(row) for row in retained}
    require(not any(key(row) in retained_keys for row in known), "a retained resource is claimed by activity state/history")
    required = BASE_KINDS | (ACCESS_KINDS if "access-bindings" in modules else set())
    value = {"schema_version": 1, "kind": "aws-workshop-cleanup-manifest", "target": identity,
        "modules": sorted(modules), "build_ids": sorted(build_ids), "state": projected,
        "known": sorted({key(row): row for row in known}.values(), key=key),
        "retained": sorted({key(row): row for row in retained}.values(), key=key), "required_kinds": sorted(required),
        "shared": {field: shared[field] for field in ("alb_arn", "hosted_zone_id", "alb_dns_name", "alb_zone_id")} if shared else None,
        "cleanup_order": [module for module in ("access-bindings", "pilot", "image-validation", "image-build") if module in modules]}
    validate_manifest(value)
    return value


def validate_manifest(manifest):
    require(manifest.get("kind") == "aws-workshop-cleanup-manifest" and manifest.get("schema_version") == 1,
        "invalid cleanup manifest")
    require(set(manifest["modules"]) <= {"pilot", "access-bindings", "image-build", "image-validation"} and manifest["modules"], "invalid cleanup modules")
    required = BASE_KINDS | (ACCESS_KINDS if "access-bindings" in manifest["modules"] else set())
    require(set(manifest["required_kinds"]) == required, "manifest cannot omit required scan families")
    require("access-bindings" not in manifest["modules"] or manifest["shared"] is not None, "shared DNS/ALB context is required")
    known = {key(ref(row["kind"], row["id"])) for row in manifest["known"]}
    retained = {key(ref(row["kind"], row["id"])) for row in manifest["retained"]}
    require(not known & retained, "retained and activity claims overlap")
    require(all(kind in required for kind, identity in known), "known resources require explicit family coverage")
    require(all(row["module"] in manifest["modules"] for row in manifest["state"]), "state scope differs from cleanup modules")
    require(manifest["cleanup_order"] == [module for module in ("access-bindings", "pilot", "image-validation", "image-build") if module in manifest["modules"]], "cleanup order changed")
    return required


def state_export(module, exported, build_id=None):
    require(isinstance(exported.get("format_version"), str) and re.fullmatch(r"1\.\d+", exported["format_version"]), "supply supported Terraform show -json, not an arbitrary JSON object")
    tree = exported.get("values", {}).get("root_module", {})
    require(not tree.get("child_modules"), "nested modules are not supported")
    return {"module": module, "resources": tree.get("resources", []), "build_id": build_id}


def reconcile(manifest, snapshot):
    validate_manifest(manifest)
    require(snapshot.get("schema_version") == 1 and snapshot.get("target") == manifest["target"], "scan target does not match manifest")
    require(snapshot.get("manifest_sha256") == wc.canonical_hash(manifest), "snapshot was collected for a different ownership manifest")
    require(set(manifest["required_kinds"]) <= ALL_KINDS, "unsupported scan coverage requested")
    known = {key(row) for row in manifest["known"]}
    retained = {key(row) for row in manifest["retained"]}
    checks = snapshot.get("checks", {})
    incomplete = sorted(kind for kind in manifest["required_kinds"] if checks.get(kind, {}).get("status") != "complete")
    incomplete_modules = sorted(module for module in manifest["modules"] if snapshot.get("states", {}).get(module, {}).get("status") != "complete")
    for module, state in snapshot.get("states", {}).items():
        require(module in manifest["modules"] and state.get("status") in {"complete", "failed"} and
            isinstance(state.get("managed_addresses"), list) and all(isinstance(address, str) for address in state["managed_addresses"]), "invalid current state evidence")
    managed = [{"module": module, "address": address} for module in manifest["modules"]
        for address in snapshot.get("states", {}).get(module, {}).get("managed_addresses", [])]
    observed, residual, conflicts, protected = {}, [], [], []
    t = manifest["target"]
    for kind, check in checks.items():
        require(kind in ALL_KINDS and check.get("status") in {"complete", "failed", "unsupported"}, "invalid scan coverage result")
        for row in check.get("resources", []):
            identity = ref(kind, row["id"])
            require(key(identity) not in observed, "duplicate resource in scan")
            observed[key(identity)] = row
            tags = row.get("tags", {})
            protected_tag = tags.get("Scope") in PROTECTED_SCOPES or kind == "images" and tags.get("ImageStatus") == "validated"
            published_parent = kind == "snapshots" and row["id"] in set(snapshot.get("published_snapshots", []))
            if key(identity) in retained or protected_tag or published_parent:
                protected.append({**identity, "reason": "explicit-or-shared-or-published", "cost_review_required": True})
                if key(identity) in known or row.get("related"):
                    conflicts.append({**identity, "reason": "activity-claim-overlaps-retained-resource"})
                continue
            owns = tags.get("Project") == "doris-workshop" and tags.get("Owner") == t["owner_id"] and (
                tags.get("Event") == t["event_id"] or
                tags.get("ImageBuild") in manifest["build_ids"] and tags.get("Scope") in {"image-build", "image"})
            if kind == "dns_records" and key(identity) in known:
                proof = next((item for item in manifest["state"] if item["kind"] == kind and item["id"] == row["id"]), {})
                owns = bool(proof) and proof.get("fields") == row.get("fields")
            if owns:
                residual.append({**identity, "state_tracked": key(identity) in known, "reason": "owned-resource-still-present"})
            elif key(identity) in known:
                # Exact history identifies what to inspect, not a new delete grant.
                conflicts.append({**identity, "reason": "known-resource-missing-or-conflicting-ownership-tags"})
            elif row.get("related"):
                conflicts.append({**identity, "reason": "untagged-or-foreign-related-resource"})
            elif tags.get("Project") == "doris-workshop":
                protected.append({**identity, "reason": "other-activity-or-owner", "cost_review_required": True})
    missing = sorted((kind, value) for kind, value in known if (kind, value) not in observed and kind not in incomplete)
    # DNS and other tagless children are exact claims. Their disappearance needs
    # successful family coverage; tagless presence never becomes an auto-delete.
    state_remaining = []
    for row in manifest["state"]:
        identity = row["parent"] if row["kind"] == "dependent" else row
        if key(identity) in observed:
            state_remaining.append({"address": row["address"], "module": row["module"]})
    return {"schema_version": 1, "event_id": t["event_id"], "manifest_sha256": wc.canonical_hash(manifest),
        "modules": manifest["modules"], "cleanup_order": manifest["cleanup_order"],
        "snapshot_sha256": wc.canonical_hash(snapshot), "ok": not (incomplete or incomplete_modules or managed or residual or conflicts or state_remaining),
        "boundary": "configured-modules-known-resources-and-tagged-discovery", "account_globally_empty": False,
        "incomplete_kinds": incomplete, "incomplete_modules": incomplete_modules, "managed_state_remaining": managed,
        "residual": sorted(residual, key=key), "conflicts": sorted(conflicts, key=key),
        "state_objects_still_present": state_remaining, "known_absent": [ref(kind, value) for kind, value in missing],
        "retained": sorted(protected, key=key), "mutations_performed": False,
        "retained_expected": [{**ref(kind, identity), "presence": "present" if (kind, identity) in observed else
            "unknown" if kind in incomplete else "absent-or-outside-required-coverage"} for kind, identity in sorted(retained)],
        "shared_dns_zone_retained": manifest["shared"]["hosted_zone_id"] if manifest["shared"] else None,
        "limitations": ["No arbitrary untagged account-wide ownership inference", "No TTL or cross-account DNS acceptance", "No deletion authorization from a scan report"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("prepare")
    make.add_argument("--config", type=Path, required=True)
    make.add_argument("--state", action="append", required=True, help="module=private terraform show -json export")
    make.add_argument("--state-bucket", required=True)
    for name in ("history", "shared", "routes", "retained"):
        make.add_argument("--" + name, type=Path)
    make.add_argument("--build-id")
    make.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("report")
    for name in ("manifest", "snapshot", "output"):
        report.add_argument("--" + name, type=Path, required=True)
    collect = commands.add_parser("collect")
    for name in ("config", "manifest", "output"):
        collect.add_argument("--" + name, type=Path, required=True)
    collect.add_argument("--state", action="append", default=[], help="module=fresh post-cleanup Terraform show -json export")
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            value = reconcile(wc.load_document(args.manifest), wc.load_document(args.snapshot))
        elif args.command == "collect":
            import workshop_preflight as pf
            from workshop_scan import Scanner
            config = wc.load_document(args.config)
            wc.schema_check(config, "config.schema.json")  # Expiry must never block inspection.
            manifest = wc.load_document(args.manifest)
            require(target(config) == manifest["target"], "explicit scan configuration differs from manifest")
            current_states = {}
            for specification in args.state:
                module, filename = specification.split("=", 1)
                state = state_export(module, wc.load_document(Path(filename)))
                require(module in manifest["modules"] and module not in current_states, "unexpected or duplicate current state export")
                current_states[module] = {"status": "complete", "managed_addresses": [row["address"] for row in state["resources"] if row.get("mode", "managed") == "managed"]}
            value = Scanner(pf.Aws(config), manifest, states=current_states).collect()
        else:
            config = wc.load_document(args.config)
            wc.schema_check(config, "config.schema.json")
            states = []
            for specification in args.state:
                module, filename = specification.split("=", 1)
                exported = wc.load_document(Path(filename))
                states.append(state_export(module, exported, args.build_id))
            routes = wc.load_document(args.routes) if args.routes else None
            if routes:
                require(routes["event_id"] == config["event_id"] and routes["terraform"]["owner_id"] == config["owner_id"] and
                    routes["terraform"]["expected_account_id"] == config["aws_account_id"] and routes["region"] == config["region"] and
                    routes["terraform"]["access"]["domain"] == config["domain"], "route ownership differs from cleanup target")
            hosts = [row["hostname"] for row in routes["terraform"]["routes"].values()] if routes else []
            if routes and routes["terraform"].get("portal"):
                hosts.append(routes["terraform"]["portal"]["hostname"])
            if routes and routes.get("routing_allocations"):
                import workshop_routing
                ledger = routes["routing_allocations"]
                reservation = ledger["events"][config["event_id"]]
                # Reuse the strict listener/owner/range/host validator without
                # changing the operator's ledger or releasing any reservation.
                workshop_routing.reserve(config, routes["terraform"]["access"], {}, previous=ledger,
                    priority_base=reservation["priority_base"], priority_limit=reservation["priority_limit"])
                hosts = sorted(set(hosts) | {row["hostname"] for row in reservation["hosts"].values()})
            value = prepare(config, states, state_bucket=args.state_bucket,
                history=wc.load_document(args.history) if args.history else None,
                shared=wc.load_document(args.shared) if args.shared else None, hosts=hosts,
                retained=wc.load_document(args.retained) if args.retained else [])
        wc.write_manifest(args.output, value)
        print("Private cleanup evidence written; no resources were deleted.")
        return 0 if args.command == "prepare" or value.get("ok", all(row["status"] == "complete" for row in value.get("checks", {}).values())) else 1
    except Exception:
        print("cleanup: inputs, ownership or scan failed; resources may remain; no delete was attempted", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
