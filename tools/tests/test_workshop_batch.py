import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_access as access
import workshop_batch as batch
import workshop_config as wc
import workshop_routing as routing

ARN = "arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/test/0123456789abcdef"


def routing_inputs():
    config = {"event_id": "event", "aws_account_id": "000000000000", "region": "us-east-1", "domain": "workshops.example.com",
        "learner_ids": ["alice", "bob"], "course_profiles": ["rta-main"], "expires_at": "2099-01-01T00:00:00Z",
        "owner_id": "operator", "aws_profile": "offline-test"}
    shared = {"account_id": config["aws_account_id"], "region": config["region"], "domain": config["domain"], "alb_arn": ARN,
        "portal": {"instance_id": "i-00000000000000009", "listener_priority": 100}}
    return config, shared, {"alice": "i-00000000000000001", "bob": "i-00000000000000002"}


class RoutingAllocationTests(unittest.TestCase):
    def test_reorder_add_remove_and_rejoin_preserve_other_priorities_and_portal(self):
        c, a, instances = routing_inputs()
        first = access.routes(c, a, instances)
        c["learner_ids"].reverse()
        self.assertEqual(first, access.routes(c, a, instances, previous=first["routing_allocations"]))
        c["learner_ids"].append("aaron")
        instances["aaron"] = "i-00000000000000003"
        added = access.routes(c, a, instances, previous=first["routing_allocations"])
        for key, value in first["terraform"]["routes"].items():
            self.assertEqual(value, added["terraform"]["routes"][key])
        self.assertEqual(first["terraform"]["portal"], added["terraform"]["portal"])
        c["learner_ids"].remove("alice")
        saved = instances.pop("alice")
        removed = access.routes(c, a, instances, previous=added["routing_allocations"])
        self.assertFalse(removed["routing_allocations"]["events"]["event"]["hosts"]["alice:jupyter"]["active"])
        c["learner_ids"].append("alice")
        instances["alice"] = saved
        restored = access.routes(c, a, instances, previous=removed["routing_allocations"])
        self.assertEqual(restored["terraform"], added["terraform"])

    def test_service_removal_and_instance_replacement_do_not_reassign_priorities(self):
        c, a, instances = routing_inputs()
        c["course_profiles"].append("rta-metabase")
        first = access.routes(c, a, instances)
        c["course_profiles"].remove("rta-metabase")
        instances["alice"] = "i-00000000000000004"
        changed = access.routes(c, a, instances, previous=first["routing_allocations"])
        self.assertEqual(first["terraform"]["routes"]["bob:jupyter"], changed["terraform"]["routes"]["bob:jupyter"])
        self.assertFalse(changed["routing_allocations"]["events"]["event"]["hosts"]["alice:rta-metabase"]["active"])

    def test_different_events_reserve_exclusive_ranges_and_keep_tombstones(self):
        c, a, instances = routing_inputs()
        first = access.routes(c, a, instances)
        c["event_id"] = "other"
        with self.assertRaises(ValueError):
            access.routes(c, a, instances, previous=first["routing_allocations"])
        second = access.routes(c, a, instances, previous=first["routing_allocations"], priority_base=2000, priority_limit=2999)
        self.assertEqual(set(second["routing_allocations"]["events"]), {"event", "other"})
        self.assertEqual(first["routing_allocations"]["events"]["event"], second["routing_allocations"]["events"]["event"])

    def test_foreign_namespace_owner_changed_range_and_corruption_are_rejected(self):
        c, a, instances = routing_inputs()
        first = access.routes(c, a, instances)
        for mutation in (lambda l: l["scope"].update(domain="other.example.com"),
                lambda l: l["events"]["event"].update(owner_id="other"),
                lambda l: l["events"]["event"]["hosts"]["alice:jupyter"].update(priority=1003),
                lambda l: l["events"]["event"]["hosts"]["alice:jupyter"].update(hostname="bob.example.com")):
            corrupted = copy.deepcopy(first["routing_allocations"])
            mutation(corrupted)
            with self.assertRaises(ValueError):
                access.routes(c, a, instances, previous=corrupted)
        with self.assertRaises(ValueError):
            access.routes(c, a, instances, previous=first["routing_allocations"], priority_limit=2500)

    def test_rule_ceiling_counts_all_active_events_in_the_ledger(self):
        c, a, instances = routing_inputs()
        previous = None
        for index in range(1, 4):
            c["event_id"] = "event-" + str(index)
            plan = access.routes(c, a, instances, previous=previous, priority_base=index * 1000,
                priority_limit=index * 1000 + 999)
            previous = plan["routing_allocations"]
        c["event_id"] = "event-4"
        with self.assertRaisesRegex(ValueError, "100-rule ceiling"):
            access.routes(c, a, instances, previous=previous, priority_base=4000, priority_limit=4999)

    def test_retired_slots_and_root_priority_cannot_be_reused(self):
        c, a, instances = routing_inputs()
        first = access.routes(c, a, instances, priority_limit=1032)
        c["learner_ids"] = ["alice", "charlie"]
        instances["charlie"] = instances.pop("bob")
        with self.assertRaisesRegex(ValueError, "exhausted"):
            access.routes(c, a, instances, previous=first["routing_allocations"], priority_limit=1032)
        with self.assertRaisesRegex(ValueError, "root priority"):
            access.routes(c, a, instances, priority_base=100, priority_limit=199)

    def test_ambiguous_hyphenated_cross_event_hostnames_are_rejected(self):
        c, a, _ = routing_inputs()
        first, _ = routing.reserve(c, a, {"a-bob:jupyter": "event-a-bob-jupyter." + c["domain"]})
        c["event_id"] = "event-a"
        with self.assertRaisesRegex(ValueError, "hostname is reserved"):
            routing.reserve(c, a, {"bob:jupyter": "event-a-bob-jupyter." + c["domain"]}, previous=first,
                priority_base=2000, priority_limit=2999)

    def test_public_cli_uses_one_locked_private_ledger_and_requires_explicit_initialization(self):
        c, a, instances = routing_inputs()
        full = wc.load_document(wc.SPEC / "examples/event.example.yaml")
        full.update(c, course_ref=wc.git(wc.REPO, "rev-parse", "HEAD").strip())
        full["image"]["region"] = full["region"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = []
            for name, value in (("config", full), ("access", a), ("instances", instances)):
                path = root / (name + ".json")
                path.write_text(json.dumps(value))
                args.extend(["--" + name, str(path)])
            ledger = root / "allocations.json"
            command = [sys.executable, str(wc.REPO / "tools/workshop_access.py"), "routes", *args,
                "--allocations", str(ledger), "--priority-base", "1000", "--priority-limit", "1999"]
            run = lambda output, extra=(): subprocess.run(command + ["--output", str(root / output), *extra], capture_output=True, text=True)
            self.assertEqual(run("missing.json").returncode, 2)
            self.assertFalse(ledger.exists())
            self.assertEqual(run("first.json", ["--init-allocations"]).returncode, 0)
            before = ledger.read_bytes()
            self.assertEqual(run("reset.json", ["--init-allocations"]).returncode, 2)
            with routing.lock(ledger):
                self.assertEqual(run("locked.json").returncode, 2)
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual(run("next.json").returncode, 0)
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)


class RosterReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = wc.load_document(wc.SPEC / "examples/event.example.yaml")
        cls.config.update(course_ref=wc.git(wc.REPO, "rev-parse", "HEAD").strip(), learner_ids=["alice", "bob"],
            expires_at=wc.utc_text(datetime.now(timezone.utc) + timedelta(hours=3)))
        cls.config, cls.resolved, cls.runtime = wc.validate_config(cls.config)

    def manifests(self):
        old = wc.build_manifest(self.config, self.resolved, self.runtime)
        config = {**self.config, "learner_ids": ["alice", "charlie"]}
        return old, wc.build_manifest(config, self.resolved, self.runtime, previous=old)

    def row(self, learner, action):
        tags = {"Project": "doris-workshop", "Scope": "activity", "Event": self.config["event_id"],
            "Owner": self.config["owner_id"], "Learner": learner}
        values = {"id": "i-" + learner, "tags_all": tags}
        return {"address": 'aws_instance.learner[' + json.dumps(learner) + ']', "mode": "managed", "type": "aws_instance",
            "name": "learner", "index": learner, "change": {"actions": [action],
                "before": values if action != "create" else None, "after": values if action != "delete" else None}}

    def test_receipt_lists_removed_id_and_preserves_other_learners(self):
        old, desired = self.manifests()
        receipt = batch.review_roster(old, desired, {"resource_changes": [self.row("alice", "no-op"), self.row("bob", "delete"), self.row("charlie", "create")]})
        self.assertEqual(receipt["added"], ["charlie"])
        self.assertEqual(receipt["removed"], ["bob"])
        self.assertEqual(receipt["retained"], ["alice"])
        self.assertIn("bob", receipt["withdrawn"])
        self.assertTrue(receipt["requires_reviewed_saved_plan"])

    def test_incomplete_plan_collateral_replacement_and_wrong_ownership_are_rejected(self):
        old, desired = self.manifests()
        valid = [self.row("bob", "delete"), self.row("charlie", "create")]
        for rows in (valid[:1], valid + [self.row("alice", "update")], valid + [
                {"address": "aws_vpc.pilot[0]", "mode": "managed", "change": {"actions": ["update"]}}]):
            with self.assertRaises(ValueError):
                batch.review_roster(old, desired, {"resource_changes": rows})
        bad = copy.deepcopy(valid)
        bad[0]["change"]["before"]["tags_all"]["Owner"] = "other"
        with self.assertRaises(ValueError):
            batch.review_roster(old, desired, {"resource_changes": bad})
        with self.assertRaises(ValueError):
            batch.review_roster(old, desired, {"complete": False, "resource_changes": valid})

    def test_manifest_target_runtime_or_withdrawn_ownership_cannot_change(self):
        old, desired = self.manifests()
        for mutate in (lambda m: m["event"].update(owner_id="other"),
                lambda m: m["desired"].update(disk_gib=300),
                lambda m: m["allocations"].pop("bob"),
                lambda m: m["allocations"]["alice"].update(role="spare")):
            broken = copy.deepcopy(desired)
            mutate(broken)
            with self.assertRaises(ValueError):
                batch.review_roster(old, broken, {"resource_changes": []})

    def test_spares_remain_unassigned_and_outside_the_live_pilot(self):
        old, _ = self.manifests()
        config = {**self.config, "spare_count": 1}
        desired = wc.build_manifest(config, self.resolved, self.runtime, previous=old)
        self.assertEqual(desired["allocations"]["spare-001"]["role"], "spare")
        with self.assertRaisesRegex(ValueError, "excludes unassigned spares"):
            batch.review_roster(old, desired, {"resource_changes": []})


if __name__ == "__main__":
    unittest.main()
