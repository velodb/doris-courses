"""Fenced bindings-update review and failure handling."""
from contextlib import nullcontext
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_bindings_update as update
import workshop_access as access
import workshop_config as wc
from test_workshop_bindings_review import removal_fixture
from test_workshop_access import ARN


class FakeTerraform:
    plan_value = None
    apply_count = 0
    fail = False

    def __init__(self, module, directory):
        self.directory = Path(directory)

    def init(self, backend):
        (self.directory / "terraform-data").mkdir()
        (self.directory / "terraform-data/terraform.tfstate").write_text("bound backend")

    def state(self):
        return {"resources": []}

    def call(self, args, *, capture=False):
        if args[0] == "plan":
            Path(next(x[5:] for x in args if x.startswith("-out="))).write_bytes(b"reviewed-plan")
            return ""
        return json.dumps(self.plan_value)

    def apply(self, path):
        type(self).apply_count += 1
        if self.fail:
            raise RuntimeError("partial failure")


class BindingUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bindings = self.root / "bindings"
        self.bindings.mkdir()
        self.old, self.new, plan = removal_fixture()
        FakeTerraform.plan_value, FakeTerraform.apply_count, FakeTerraform.fail = plan, 0, False
        self.old_path, self.new_path, self.output = self.root / "old.json", self.root / "new.json", self.root / "review"
        wc.write_manifest(self.old_path, {"terraform": self.old})
        wc.write_manifest(self.new_path, {"terraform": self.new})
        wc.write_manifest(self.bindings / "inputs.tfvars.json", self.old)
        wc.write_manifest(self.bindings / "backend.tfbackend.json", {"backend": "fixture"})
        self.controller = mock.Mock()
        self.controller.config = {"event_id": "class"}
        self.controller.pilot = {}
        self.controller.cloud = object()
        patches = [mock.patch.object(update, "verify_context", return_value=(self.old, self.new, {"operation": "remove"})),
                   mock.patch.object(update, "hashes", return_value={"bound": "fixture"}),
                   mock.patch.object(update, "verify_state", return_value="same-state"),
                   mock.patch.object(update, "verify_plan_state")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def prepare(self):
        return update.prepare(self.controller, self.bindings, self.old_path, self.new_path, self.output, tf_factory=FakeTerraform)

    def apply(self, receipt):
        return update.apply(self.controller, self.bindings, self.old_path, self.new_path, self.output,
                            receipt["review_sha256"], tf_factory=FakeTerraform)

    def test_review_apply_and_progress_are_bound(self):
        receipt = self.prepare()
        result = self.apply(receipt)
        self.assertEqual(result["phase"], "complete")
        self.assertFalse(result["portal_assignment_updated"])
        self.assertEqual(json.loads((self.bindings / "inputs.tfvars.json").read_text()), self.new)
        self.assertEqual(FakeTerraform.apply_count, 1)
        self.assertEqual(json.loads((self.output / "progress.json").read_text())["phase"], "complete")
        with self.assertRaises(ValueError):
            self.apply(receipt)
        self.assertEqual(FakeTerraform.apply_count, 1)

    def test_changed_plan_and_backend_are_rejected(self):
        receipt = self.prepare()
        (self.output / "update.tfplan").write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.apply(receipt)
        self.assertEqual(FakeTerraform.apply_count, 0)

    def test_inputs_changed_during_validation_are_rejected_before_apply(self):
        receipt = self.prepare()
        with mock.patch.object(update, "hashes", side_effect=[{"bound": "fixture"}, {"bound": "changed"}]):
            with self.assertRaisesRegex(ValueError, "changed before apply"):
                self.apply(receipt)
        self.assertEqual(FakeTerraform.apply_count, 0)

    def test_partial_failure_is_not_blindly_replayed(self):
        receipt = self.prepare()
        FakeTerraform.fail = True
        with self.assertRaises(RuntimeError):
            self.apply(receipt)
        self.assertEqual(json.loads((self.output / "progress.json").read_text())["phase"], "applying")
        with self.assertRaisesRegex(ValueError, "previous apply"):
            self.apply(receipt)
        self.assertEqual(FakeTerraform.apply_count, 1)

    def test_cli_holds_scope_changing_activity_fence(self):
        with mock.patch.object(update.lifecycle, "run_lock", return_value=nullcontext()), \
                mock.patch.object(update.lifecycle, "Controller", return_value=self.controller), \
                mock.patch.object(update.ttl, "activity_operation", return_value=nullcontext()) as fence, \
                mock.patch.object(update, "prepare", return_value={"review_sha256": "fixture"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            args = ["plan", "--pilot-run", str(self.root), "--bindings-run", str(self.bindings),
                    "--old-route", str(self.old_path), "--new-route", str(self.new_path), "--output", str(self.output)]
            self.assertEqual(update.main(args), 0)
        self.assertTrue(fence.call_args.kwargs["scope_change"])

    def test_regeneration_rejects_tampered_routes_or_ledger(self):
        config = {"event_id": "event", "aws_account_id": "000000000000", "region": "us-east-1",
            "domain": "workshops.example.com", "learner_ids": ["alice"], "course_profiles": ["rta-main"],
            "expires_at": "2099-01-01T00:00:00Z", "owner_id": "operator", "aws_profile": "offline"}
        shared = {"account_id": config["aws_account_id"], "region": config["region"],
                  "domain": config["domain"], "alb_arn": ARN}
        controller = mock.Mock(config=config, binding={"shared_access": shared})
        route = access.routes(config, shared, {"alice": "i-00000000000000001"})
        self.assertEqual(update.verify_route(controller, route), route["terraform"])
        tampered = copy.deepcopy(route)
        tampered["terraform"]["routes"]["alice:jupyter"]["hostname"] = "foreign.example.com"
        with self.assertRaises(ValueError):
            update.verify_route(controller, tampered)


if __name__ == "__main__":
    unittest.main()
