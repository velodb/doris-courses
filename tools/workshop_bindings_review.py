#!/usr/bin/env python3
"""Validate the exact Terraform changes for one learner's access binding update."""
import copy
import json

import workshop_config as wc


def require(value, message):
    if not value:
        raise wc.ConfigError("bindings review: " + message)


def address(kind, key):
    return "aws_" + kind + ".learner[" + json.dumps(key) + "]"


def difference(old, new):
    """Classify one roster or target change without permitting unrelated inputs."""
    require(set(old) == set(new), "input fields changed")
    require(all(old[key] == new[key] for key in old if key not in {"learners", "routes"}),
            "shared entry or activity inputs changed")
    before, after = old["learners"], new["learners"]
    br, ar = old["routes"], new["routes"]
    require(all(br[key] == ar[key] for key in set(br) & set(ar)), "retained route changed")
    added, removed = set(after) - set(before), set(before) - set(after)
    changed = {key for key in set(before) & set(after) if before[key] != after[key]}
    route_added, route_removed = set(ar) - set(br), set(br) - set(ar)
    require(len(added) + len(removed) + len(changed) == 1, "change exactly one learner")
    if added:
        operation, learner = "add", next(iter(added))
        require(not route_removed and route_added and all(ar[k]["learner_id"] == learner for k in route_added),
                "added routes differ from the new learner")
    elif removed:
        operation, learner = "remove", next(iter(removed))
        require(not route_added and route_removed and all(br[k]["learner_id"] == learner for k in route_removed),
                "removed routes differ from the withdrawn learner")
    else:
        operation, learner = "replace", next(iter(changed))
        require(not route_added and not route_removed, "replacement changed service routes")
    for routes, learners in ((br, before), (ar, after)):
        require(len(set(learners.values())) == len(learners) and all(isinstance(v, str) and v for v in learners.values()),
                "learner targets must be distinct")
        require(all(isinstance(k, str) and isinstance(v, dict) and v.get("learner_id") in learners and
                    k.startswith(v["learner_id"] + ":") for k, v in routes.items()), "route assignment is invalid")
        require(len({row.get("hostname") for row in routes.values()}) == len(routes), "service hostnames must be distinct")
        require(all(any(row["learner_id"] == learner_id for row in routes.values()) for learner_id in learners),
                "every learner must have routes")
    expected = {}
    if operation in {"add", "remove"}:
        action = ["create"] if added else ["delete"]
        for typ in ("lb_target_group", "lb_target_group_attachment"):
            expected[address(typ, learner)] = action
        for route in route_added | route_removed:
            expected[address("route53_record", route)] = action
            for suffix in ("logout", "api", "page"):
                expected[address("lb_listener_rule", route + "-" + suffix)] = action
        expected["aws_cognito_user_pool_client.alb"] = ["update"]
    else:
        expected[address("lb_target_group_attachment", learner)] = None
    return {"operation": operation, "learner": learner, "expected": expected}


def _normalization(drift, old):
    """Recognize provider normalization only when every other value is equal."""
    typ, key = drift["type"], drift.get("index")
    require(drift["change"]["actions"] == ["update"] and
            isinstance(drift["change"].get("before"), dict) and isinstance(drift["change"].get("after"), dict),
            "unexpected refresh drift")
    b, a = copy.deepcopy(drift["change"]["before"]), copy.deepcopy(drift["change"]["after"])
    if typ == "aws_lb_listener_rule":
        keys = set(old["routes"]) | ({"portal"} if old.get("portal") else set())
        require(key in {k + "-" + suffix for k in keys for suffix in ("logout", "api", "page")}, "drift belongs to another rule")
        if b.get("tags") is None and a.get("tags") == {}:
            b["tags"] = {}
        ba, aa = b.get("action", []), a.get("action", [])
        require(len(ba) == len(aa), "listener actions changed")
        for x, y in zip(ba, aa):
            bx, ay = x.get("authenticate_cognito", []), y.get("authenticate_cognito", [])
            require(len(bx) == len(ay), "listener authentication changed")
            for old_auth, new_auth in zip(bx, ay):
                if old_auth.get("authentication_request_extra_params") is None and new_auth.get("authentication_request_extra_params") == {}:
                    old_auth["authentication_request_extra_params"] = {}
    elif typ == "aws_lb_target_group":
        require(key in old["learners"], "drift belongs to another target group")
        if b.get("tags") is None and a.get("tags") == {}:
            b["tags"] = {}
        if b.get("load_balancer_arns") == [] and a.get("load_balancer_arns") == [old["access"]["alb_arn"]]:
            b["load_balancer_arns"] = [old["access"]["alb_arn"]]
    elif typ == "aws_route53_record":
        require(key in set(old["routes"]) | ({"portal"} if old.get("portal") else set()), "drift belongs to another DNS record")
        if b.get("records") is None and a.get("records") == []:
            b["records"] = []
    elif typ == "aws_cognito_user_pool":
        require(drift.get("address") == "aws_cognito_user_pool.activity", "drift belongs to another user pool")
        for field in ("auto_verified_attributes", "username_attributes"):
            if b.get(field) is None and a.get(field) == []:
                b[field] = []
        if b.get("tags") is None and a.get("tags") == {}:
            b["tags"] = {}
        domain = "dws-" + old["event_id"] + "-" + old["expected_account_id"]
        if b.get("domain") == "" and a.get("domain") == domain:
            b["domain"] = domain
        if all(type(v) is int and v >= 0 for v in (b.get("estimated_number_of_users"), a.get("estimated_number_of_users"))):
            b["estimated_number_of_users"] = a["estimated_number_of_users"]
    else:
        raise wc.ConfigError("bindings review: unsupported refresh drift")
    require(b == a, "refresh changed more than provider normalization")


def validate_plan(old, new, plan):
    """Prove the saved plan changes only the specified learner and OAuth URLs."""
    change = difference(old, new)
    operation, learner, expected = change["operation"], change["learner"], change["expected"]
    require(not plan.get("errored") and plan.get("complete", True) is True and plan.get("applyable", True) is True and not plan.get("deferred_changes"),
            "plan is incomplete, deferred or errored")
    require(all(plan.get("variables", {}).get(k, {}).get("value") == v for k, v in new.items()),
            "saved plan variables differ from the proposed inputs")
    old_attachment_drift = False
    group = None
    if operation == "replace":
        groups = [r for r in plan.get("resource_changes", []) if r.get("address") == address("lb_target_group", learner)]
        require(len(groups) == 1 and groups[0]["change"]["actions"] == ["no-op"], "replacement changed the target group")
        group = groups[0]["change"]["before"]["arn"]
    for drift in plan.get("resource_drift", []):
        if drift.get("mode") != "managed" or drift["change"]["actions"] == ["no-op"]:
            continue
        if operation == "replace" and drift.get("type") == "aws_lb_target_group_attachment":
            value = drift["change"].get("before") or {}
            require(not old_attachment_drift and drift.get("index") == learner and
                    drift["change"]["actions"] == ["delete"] and drift["change"].get("after") is None and
                    value.get("target_id") == old["learners"][learner] and value.get("target_group_arn") == group and
                    value.get("port") == 8080, "unexpected removed target drift")
            old_attachment_drift = True
        else:
            _normalization(drift, old)
    rows = [r for r in plan.get("resource_changes", []) if r.get("mode") == "managed" and r["change"]["actions"] != ["no-op"]]
    require(len(rows) == len(expected) and {r["address"] for r in rows} == set(expected), "unexpected or duplicate changes")
    for row in rows:
        actions, want, detail = row["change"]["actions"], expected[row["address"]], row["change"]
        require(row["address"] == (row["type"] + ".alb" if row["type"] == "aws_cognito_user_pool_client" else
                row["type"] + ".learner[" + json.dumps(row.get("index")) + "]"), "change address is ambiguous")
        require(actions == want if want is not None else actions in (["delete", "create"], ["create", "delete"]) or
                actions == ["create"] and old_attachment_drift, "unexpected action")
        if row["type"] == "aws_lb_target_group_attachment":
            key = row["index"]
            if "delete" in actions:
                require(detail["before"]["target_id"] == old["learners"][key] and detail["before"]["port"] == 8080,
                        "old target differs")
            if "create" in actions:
                require(detail["after"]["target_id"] == new["learners"][key] and detail["after"]["port"] == 8080,
                        "new target differs")
                if operation == "replace":
                    require(detail["after"]["target_group_arn"] == group, "new target belongs to another group")
        if row["type"] == "aws_route53_record":
            value = detail["after" if actions == ["create"] else "before"]
            route = (new if actions == ["create"] else old)["routes"][row["index"]]
            require(value["zone_id"] == new["access"]["hosted_zone_id"] and value["type"] == "A" and
                    value["name"].rstrip(".") == route["hostname"], "DNS record differs from assigned route")
        if row["address"] == "aws_cognito_user_pool_client.alb":
            b, a = detail["before"], detail["after"]
            for field, suffix in (("callback_urls", "/oauth2/idpresponse"), ("logout_urls", "/_workshop/logged-out")):
                for inputs, value in ((old, b), (new, a)):
                    hosts = {r["hostname"] for r in inputs["routes"].values()}
                    if inputs.get("portal"):
                        hosts.add(inputs["portal"]["hostname"])
                    require(set(value[field]) == {"https://" + h + suffix for h in hosts}, "OAuth hosts differ from routes")
            for key in set(b) & set(a) - {"callback_urls", "logout_urls"}:
                if not detail.get("after_unknown", {}).get(key):
                    require(b[key] == a[key], "unexpected Cognito client change")
    return {"operation": operation, "learner": learner, "changes": [{"address": r["address"], "actions": r["change"]["actions"]} for r in rows],
            "verified_old_attachment_drift": old_attachment_drift}
