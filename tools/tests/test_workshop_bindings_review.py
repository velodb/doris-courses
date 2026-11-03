"""Exact learner-binding change review, including provider refresh drift."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_bindings_review as review


def inputs():
    return {"event_id": "class", "expected_account_id": "123456789012", "access": {
        "hosted_zone_id": "zone", "alb_arn": "alb"}, "portal": None,
        "learners": {"alice": "i-alice", "bob": "i-bob"},
        "routes": {name + ":jupyter": {"learner_id": name, "hostname": name + ".example.com", "priority": n}
                   for name, n in (("alice", 100), ("bob", 103))}}


def removal_fixture():
    old = inputs()
    new = copy.deepcopy(old)
    del new["learners"]["bob"]
    route = new["routes"].pop("bob:jupyter")
    rows = []
    def row(address, typ, index, before, action):
        rows.append({"mode": "managed", "address": address, "type": typ, "index": index,
                     "change": {"before": before, "after": None, "actions": action}})
    row(review.address("lb_target_group", "bob"), "aws_lb_target_group", "bob", {}, ["delete"])
    row(review.address("lb_target_group_attachment", "bob"), "aws_lb_target_group_attachment", "bob",
        {"target_id": "i-bob", "port": 8080}, ["delete"])
    row(review.address("route53_record", "bob:jupyter"), "aws_route53_record", "bob:jupyter",
        {"zone_id": "zone", "type": "A", "name": route["hostname"]}, ["delete"])
    for suffix in ("logout", "api", "page"):
        key = "bob:jupyter-" + suffix
        row(review.address("lb_listener_rule", key), "aws_lb_listener_rule", key, {}, ["delete"])
    def client(value):
        hosts = [r["hostname"] for r in value["routes"].values()]
        return {"callback_urls": ["https://" + h + "/oauth2/idpresponse" for h in hosts],
                "logout_urls": ["https://" + h + "/_workshop/logged-out" for h in hosts], "name": "stable"}
    rows.append({"mode": "managed", "address": "aws_cognito_user_pool_client.alb", "type": "aws_cognito_user_pool_client",
                 "change": {"actions": ["update"], "before": client(old), "after": client(new)}})
    plan = {"complete": True, "errored": False, "variables": {k: {"value": v} for k, v in new.items()}, "resource_changes": rows}
    return old, new, plan


class BindingReviewTests(unittest.TestCase):
    def test_removal_is_exactly_one_learner(self):
        old, new, plan = removal_fixture()
        result = review.validate_plan(old, new, plan)
        self.assertEqual((result["operation"], result["learner"], len(result["changes"])), ("remove", "bob", 7))

    def test_addition_and_replacement_bind_the_exact_target(self):
        old, new, plan = removal_fixture()
        addition = copy.deepcopy(plan)
        addition["variables"] = {k: {"value": v} for k, v in old.items()}
        for row in addition["resource_changes"]:
            c = row["change"]
            c["before"], c["after"] = c["after"], c["before"]
            if c["actions"] == ["delete"]:
                c["actions"] = ["create"]
        self.assertEqual(review.validate_plan(new, old, addition)["operation"], "add")
        replacement = copy.deepcopy(old)
        replacement["learners"]["bob"] = "i-bob-new"
        attachment = {"mode": "managed", "address": review.address("lb_target_group_attachment", "bob"),
                      "type": "aws_lb_target_group_attachment", "index": "bob", "change": {
                          "actions": ["delete", "create"], "before": {"target_id": "i-bob", "port": 8080},
                          "after": {"target_id": "i-bob-new", "port": 8080, "target_group_arn": "group"}}}
        group = {"mode": "managed", "address": review.address("lb_target_group", "bob"),
                 "type": "aws_lb_target_group", "index": "bob", "change": {"actions": ["no-op"], "before": {"arn": "group"}}}
        p = {"complete": True, "variables": {k: {"value": v} for k, v in replacement.items()},
             "resource_changes": [attachment, group]}
        self.assertEqual(review.validate_plan(old, replacement, p)["operation"], "replace")
        attachment["change"]["after"]["target_group_arn"] = "foreign-group"
        with self.assertRaises(ValueError):
            review.validate_plan(old, replacement, p)

    def test_unrelated_change_missing_or_duplicate_action_rejected(self):
        old, new, plan = removal_fixture()
        for edit in (lambda p: p["resource_changes"].append(copy.deepcopy(p["resource_changes"][0])),
                     lambda p: p["resource_changes"].pop(),
                     lambda p: p["resource_changes"][0]["change"].update(actions=["update"])):
            broken = copy.deepcopy(plan)
            edit(broken)
            with self.assertRaises(ValueError):
                review.validate_plan(old, new, broken)
        changed = copy.deepcopy(new)
        changed["routes"]["alice:jupyter"]["hostname"] = "other.example.com"
        with self.assertRaises(ValueError):
            review.difference(old, changed)

    def test_target_dns_and_oauth_cannot_be_redirected(self):
        old, new, plan = removal_fixture()
        for index, field, value in ((1, "target_id", "i-foreign"), (2, "zone_id", "other")):
            broken = copy.deepcopy(plan)
            broken["resource_changes"][index]["change"]["before"][field] = value
            with self.assertRaises(ValueError):
                review.validate_plan(old, new, broken)
        broken = copy.deepcopy(plan)
        broken["resource_changes"][-1]["change"]["after"]["callback_urls"].append("https://attacker.example/cb")
        with self.assertRaises(ValueError):
            review.validate_plan(old, new, broken)

    def test_normalization_is_bounded_and_other_drift_rejected(self):
        old, new, plan = removal_fixture()
        base = {"mode": "managed", "type": "aws_cognito_user_pool", "address": "aws_cognito_user_pool.activity",
                "change": {"actions": ["update"], "before": {"estimated_number_of_users": 1, "domain": ""},
                           "after": {"estimated_number_of_users": 2, "domain": "dws-class-123456789012"}}}
        plan["resource_drift"] = [base]
        review.validate_plan(old, new, plan)
        base["change"]["after"]["domain"] = "other"
        with self.assertRaises(ValueError):
            review.validate_plan(old, new, plan)

    def test_incomplete_and_deferred_plans_rejected(self):
        old, new, plan = removal_fixture()
        for edit in ({"complete": False}, {"errored": True}, {"applyable": False}, {"deferred_changes": [{"reason": "unknown"}]}):
            with self.assertRaises(ValueError):
                review.validate_plan(old, new, {**plan, **edit})


if __name__ == "__main__":
    unittest.main()
