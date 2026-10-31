"""Offline ownership checks for roster-only Terraform changes."""
import json

import workshop_config as wc


def require(condition, message):
    if not condition:
        raise wc.ConfigError("roster: " + message)


def validate_manifest(manifest, config=None):
    wc.schema_check(manifest, "manifest.schema.json")
    event = manifest["event"]
    require(manifest["state"]["key"] == "/".join(["aws-workshop", event["aws_partition"],
        event["aws_account_id"], event["region"], event["event_id"], "terraform.tfstate"]), "state ownership mismatch")
    for key, entry in manifest["allocations"].items():
        require(entry["terraform_key"] == key and entry["owner_key"] == event["event_id"] + "/" + key and
            entry["role"] == ("spare" if key.startswith("spare-") else "learner"), "allocation ownership mismatch")
    if config is not None:
        require(manifest["config_sha256"] == wc.canonical_hash(config) and
            all(event[key] == config[key] for key in event), "allocation manifest does not match the bound configuration")
        require({key for key, row in manifest["allocations"].items() if row["active"] and row["role"] == "learner"} ==
            set(config["learner_ids"]), "active learner allocations differ from configuration")


def review_roster(previous, desired, plan):
    """Reject collateral mutations; this receipt never authorizes an apply."""
    validate_manifest(previous)
    validate_manifest(desired)
    require(previous["event"] == desired["event"] and previous["state"] == desired["state"], "target/owner/state changed")
    require(previous["desired"] == desired["desired"] and
        previous["versions"]["course_ref"] == desired["versions"]["course_ref"],
        "roster-only plans cannot change image, runtime, expiry or other deployment settings")
    for key, old in previous["allocations"].items():
        require(key in desired["allocations"] and all(desired["allocations"][key][field] == old[field]
            for field in ("terraform_key", "owner_key", "role")), "withdrawn allocations must remain in the manifest")
    require(not any(row["active"] and row["role"] == "spare" for m in (previous, desired)
        for row in m["allocations"].values()), "the live pilot still excludes unassigned spares")
    active = lambda m: {key for key, row in m["allocations"].items() if row["active"]}
    before, after = active(previous), active(desired)
    added, removed = after - before, before - after
    seen = {"create": set(), "delete": set()}
    require(isinstance(plan.get("resource_changes"), list) and not plan.get("errored") and
        plan.get("complete", True) is True and not plan.get("deferred_changes"), "use a complete Terraform plan export")
    addresses = set()
    for row in plan["resource_changes"]:
        if row.get("mode") != "managed":
            continue
        require(row["address"] not in addresses and not row.get("module_address"), "duplicate or nested resource address")
        addresses.add(row["address"])
        actions = row["change"]["actions"]
        if actions == ["no-op"]:
            continue
        learner = row.get("index")
        require(row.get("type") == "aws_instance" and row.get("name") == "learner" and isinstance(learner, str) and
            row["address"] == 'aws_instance.learner[' + json.dumps(learner) + ']' and actions in (["create"], ["delete"]),
            "only added/removed learner instances may change; use the separate replacement flow for replacements")
        action = actions[0]
        require(learner in (added if action == "create" else removed), "plan changes a retained or unassigned learner")
        values = row["change"]["after" if action == "create" else "before"]
        tags = values.get("tags_all") or values.get("tags") or {}
        require(all(tags.get(key) == value for key, value in {
            "Project": "doris-workshop", "Scope": "activity", "Event": desired["event"]["event_id"],
            "Owner": desired["event"]["owner_id"], "Learner": learner}.items()), "instance ownership tags differ from this roster")
        seen[action].add(learner)
    require(seen["create"] == added and seen["delete"] == removed, "plan does not implement the complete roster change")
    return {"kind": "aws-workshop-roster-review", "schema_version": 1, "event_id": desired["event"]["event_id"],
        "previous_manifest_sha256": wc.canonical_hash(previous), "manifest_sha256": wc.canonical_hash(desired),
        "plan_json_sha256": wc.canonical_hash(plan), "added": sorted(added), "removed": sorted(removed),
        "retained": sorted(before & after), "withdrawn": sorted(set(desired["allocations"]) - after),
        "requires_reviewed_saved_plan": True, "course_labs_validated": False}
