"""Review and failure boundaries for fenced multi-state activity cleanup."""
import copy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_config as wc
import workshop_shared_cleanup as shared_cleanup
import workshop_portal_update as portal_update
from test_workshop_cleanup import config, shared, snapshot, state_row, tags


class FakeController:
    def __init__(self, root):
        self.directory = root / "pilot-run"
        self.directory.mkdir()
        self.config = config()
        self.config["aws_profile"] = "operator"
        self.config["expires_at"] = "2099-01-01T00:00:00Z"
        self.pilot = {"state_bucket_name": "foundation-state-example"}
        self.binding = {"shared_access": shared()}
        self.cloud = object()
        self.record = {"event_id": "class-a", "known_instances": [], "known_volumes": [], "nodes": {}}
        self.reconciled = False
        for name in ("binding.json", "inputs.tfvars.json", "backend.tfbackend.json"):
            wc.write_manifest(self.directory / name, {"fixture": name})

    def inventory(self, *, resources):
        self.record["known_instances"] = sorted(r["values"]["id"] for r in resources if r["type"] == "aws_instance")
        self.record["known_volumes"] = sorted(v["volume_id"] for r in resources if r["type"] == "aws_instance"
                                                   for v in r["values"].get("root_block_device", []))
        return {}, None

    def actual_instances(self, instances):
        return {}

    def save(self):
        pass

    def _destroy(self):
        self.reconciled = True
        return {"cleanup_boundary": "pilot-state-and-known-ec2-ebs-eni-only"}


class FakeTerraform:
    store = {}
    plans = {}
    actions = []
    fail_module = None

    def __init__(self, module, path):
        self.module, self.path = module, Path(path)

    def init(self, backend):
        data = self.path / "terraform-data"
        data.mkdir()
        (data / "terraform.tfstate").write_text("backend:" + self.module)

    def state(self):
        return {"module": self.module, "resources": copy.deepcopy(self.store[self.module]), "build_id": None}

    def plan(self, variables, path):
        Path(path).write_bytes(("plan:" + self.module).encode())
        rows = [{"mode": "managed", "address": r["address"],
                 "change": {"actions": ["delete"], "before": copy.deepcopy(r["values"])}}
                for r in self.store[self.module]]
        self.plans[self.module] = {"resource_changes": rows}
        return copy.deepcopy(self.plans[self.module])

    def call(self, args, *, capture=False):
        return json.dumps(self.plans[self.module])

    def apply(self, path):
        self.actions.append(self.module)
        if self.module == self.fail_module:
            raise RuntimeError("private provider error")
        self.store[self.module] = []


class FakeScanner:
    fail = False

    def __init__(self, cloud, manifest, *, states):
        self.result = snapshot(manifest)
        self.result["states"] = states
        if self.fail:
            self.result["checks"]["buckets"]["status"] = "failed"

    def collect(self):
        return self.result


class SharedCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.controller = FakeController(self.root)
        self.bindings = self.root / "bindings-run"
        self.bindings.mkdir()
        c = self.controller.config
        backend = {"bucket": self.controller.pilot["state_bucket_name"], "region": c["region"],
                   "profile": c["aws_profile"], "allowed_account_ids": [c["aws_account_id"]],
                   "encrypt": True, "use_lockfile": True,
                   "key": f"aws-workshop/aws/{c['aws_account_id']}/{c['region']}/{c['event_id']}/access.tfstate"}
        host = "class-a-alice-jupyter." + c["domain"]
        self.variables = {"aws_profile": c["aws_profile"], "expected_account_id": c["aws_account_id"],
            "region": c["region"], "owner_id": c["owner_id"], "event_id": c["event_id"],
            "expires_at": c["expires_at"], "access": shared(), "learners": {"alice": "i-00000000000000001"},
            "routes": {"alice:jupyter": {"hostname": host}}, "portal": None}
        wc.write_manifest(self.bindings / "backend.tfbackend.json", backend)
        wc.write_manifest(self.bindings / "inputs.tfvars.json", self.variables)
        self.routes = self.root / "routes.json"
        wc.write_manifest(self.routes, {"terraform": self.variables})
        self.output = self.root / "review"
        pilot = state_row(values={"root_block_device": [{"volume_id": "vol-00000000000000001"}]})
        pool = state_row("aws_cognito_user_pool", "activity", "us-east-1_pool",
                         {"tags_all": tags("activity-access")}, index=None)
        client = state_row("aws_cognito_user_pool_client", "alb", "client",
                           {"user_pool_id": "us-east-1_pool", "client_secret": "PRIVATE-CLIENT"}, index=None)
        FakeTerraform.store = {"pilot": [pilot], "access-bindings": [pool, client]}
        FakeTerraform.plans, FakeTerraform.actions, FakeTerraform.fail_module = {}, [], None
        FakeScanner.fail = False

    def prepare(self):
        return shared_cleanup.prepare(self.controller, self.bindings, self.routes, self.output, tf_factory=FakeTerraform)

    def test_review_then_apply_orders_states_and_preserves_private_values(self):
        review = self.prepare()
        self.assertNotIn("PRIVATE-CLIENT", (self.output / "manifest.json").read_text())
        result = shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                                      review["review_sha256"], tf_factory=FakeTerraform, scan_factory=FakeScanner)
        self.assertEqual(FakeTerraform.actions, ["access-bindings", "pilot"])
        self.assertTrue(self.controller.reconciled)
        self.assertTrue(result["shared_resources_retained"])
        self.assertTrue(result["full_shared_scan_complete"])
        self.assertTrue(wc.load_document(Path(result["report"]))["ok"])

    def test_incomplete_scan_requires_read_only_retry_without_reapplying(self):
        review = self.prepare()
        FakeScanner.fail = True
        with self.assertRaisesRegex(wc.ConfigError, "reconciliation incomplete"):
            shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                review["review_sha256"], tf_factory=FakeTerraform, scan_factory=FakeScanner)
        self.assertEqual(FakeTerraform.actions, ["access-bindings", "pilot"])
        self.assertEqual(wc.load_document(self.output / "progress.json")["phase"], "reconciliation-incomplete")
        self.assertFalse(wc.load_document(self.output / "reconciliation-001" / "report.json")["ok"])
        FakeScanner.fail = False
        result = shared_cleanup.retry_reconciliation(self.controller, self.bindings, self.routes, self.output,
            review["review_sha256"], tf_factory=FakeTerraform, scan_factory=FakeScanner)
        self.assertTrue(result["full_shared_scan_complete"])
        self.assertEqual(FakeTerraform.actions, ["access-bindings", "pilot"])
        self.assertTrue(wc.load_document(self.output / "reconciliation-002" / "report.json")["ok"])

    def test_cannot_retry_scan_after_unknown_apply_outcome(self):
        review = self.prepare()
        wc.write_manifest(self.output / "progress.json", {"phase": "applying-pilot"})
        with self.assertRaisesRegex(wc.ConfigError, "apply outcome is not known"):
            shared_cleanup.retry_reconciliation(self.controller, self.bindings, self.routes, self.output,
                review["review_sha256"], tf_factory=FakeTerraform, scan_factory=FakeScanner)
        self.assertEqual(FakeTerraform.actions, [])

    def test_reviewed_portal_removal_precedes_destroy_and_is_bound_to_receipt(self):
        paths = {}
        for key in ("identity", "subjects", "registry"):
            paths[key] = self.root / (key + ".json")
            wc.write_manifest(paths[key], {"fixture": key})
        expected = {"inputs": {key: shared_cleanup.pf.file_hash(path) for key, path in paths.items()},
                    "next_registry_sha256": "next-registry"}
        with mock.patch.object(shared_cleanup, "portal_review", return_value=expected), \
                mock.patch.object(portal_update, "update", side_effect=lambda *a, **kw: FakeTerraform.actions.append("portal")):
            review = shared_cleanup.prepare(self.controller, self.bindings, self.routes, self.output,
                tf_factory=FakeTerraform, portal_files=paths)
            with self.assertRaisesRegex(wc.ConfigError, "portal inputs"):
                shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                    review["review_sha256"], tf_factory=FakeTerraform, scan_factory=FakeScanner)
            shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                review["review_sha256"], tf_factory=FakeTerraform, scan_factory=FakeScanner, portal_files=paths)
        self.assertEqual(FakeTerraform.actions, ["portal", "access-bindings", "pilot"])

    def test_portal_failure_blocks_destroy_and_requires_investigation(self):
        paths = {}
        for key in ("identity", "subjects", "registry"):
            paths[key] = self.root / (key + ".json")
            wc.write_manifest(paths[key], {"fixture": key})
        expected = {"inputs": {key: shared_cleanup.pf.file_hash(path) for key, path in paths.items()},
                    "next_registry_sha256": "next-registry"}
        with mock.patch.object(shared_cleanup, "portal_review", return_value=expected), \
                mock.patch.object(portal_update, "update", side_effect=RuntimeError("dispatch unknown")):
            review = shared_cleanup.prepare(self.controller, self.bindings, self.routes, self.output,
                tf_factory=FakeTerraform, portal_files=paths)
            with self.assertRaises(RuntimeError):
                shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                    review["review_sha256"], tf_factory=FakeTerraform, scan_factory=FakeScanner, portal_files=paths)
        self.assertEqual(FakeTerraform.actions, [])
        self.assertEqual(wc.load_document(self.output / "progress.json")["phase"], "unregistering-portal")

    def test_changed_plan_or_backend_never_applies(self):
        review = self.prepare()
        (self.output / "pilot" / "destroy.tfplan").write_bytes(b"changed")
        with self.assertRaisesRegex(wc.ConfigError, "saved destroy plan changed"):
            shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                                 review["review_sha256"], tf_factory=FakeTerraform)
        self.assertEqual(FakeTerraform.actions, [])

    def test_changed_initialized_backend_never_applies(self):
        review = self.prepare()
        (self.output / "access-bindings" / "terraform-data" / "terraform.tfstate").write_text("other-backend")
        with self.assertRaisesRegex(wc.ConfigError, "initialized Terraform backend changed"):
            shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                                 review["review_sha256"], tf_factory=FakeTerraform)
        self.assertEqual(FakeTerraform.actions, [])

    def test_changed_inputs_during_validation_never_apply(self):
        receipt = self.prepare()
        original = receipt["review_sha256"]
        artifacts = wc.load_document(self.output / "review.json")["artifacts"]
        with mock.patch.object(shared_cleanup, "artifact_hashes", side_effect=[artifacts, {"changed": "yes"}]):
            with self.assertRaisesRegex(wc.ConfigError, "changed before apply"):
                shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output, original, tf_factory=FakeTerraform)
        self.assertEqual(FakeTerraform.actions, [])

    def test_changed_state_or_partial_failure_is_never_blindly_replayed(self):
        review = self.prepare()
        FakeTerraform.store["pilot"][0]["values"]["tags_all"]["Owner"] = "other"
        with self.assertRaises(wc.ConfigError):
            shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                                 review["review_sha256"], tf_factory=FakeTerraform)
        self.assertEqual(FakeTerraform.actions, [])
        FakeTerraform.store["pilot"][0]["values"]["tags_all"]["Owner"] = "operator"
        FakeTerraform.fail_module = "pilot"
        with self.assertRaises(RuntimeError):
            shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                                 review["review_sha256"], tf_factory=FakeTerraform)
        self.assertEqual(FakeTerraform.actions, ["access-bindings", "pilot"])
        self.assertEqual(json.loads((self.output / "progress.json").read_text())["phase"], "applying-pilot")
        with self.assertRaises(wc.ConfigError):
            shared_cleanup.apply(self.controller, self.bindings, self.routes, self.output,
                                 review["review_sha256"], tf_factory=FakeTerraform)
        self.assertEqual(FakeTerraform.actions, ["access-bindings", "pilot"])

    def test_wrong_backend_and_cross_account_dns_are_rejected(self):
        path = self.bindings / "backend.tfbackend.json"
        backend = json.loads(path.read_text())
        path.unlink()
        wc.write_manifest(path, {**backend, "key": "unrelated/terraform.tfstate"})
        with self.assertRaisesRegex(wc.ConfigError, "backend differs"):
            self.prepare()
        self.assertFalse(self.output.exists())
        path.unlink()
        wc.write_manifest(path, backend)
        self.variables["access"]["dns_account_id"] = "111111111111"
        self.controller.binding["shared_access"] = self.variables["access"]
        (self.bindings / "inputs.tfvars.json").unlink()
        self.routes.unlink()
        wc.write_manifest(self.bindings / "inputs.tfvars.json", self.variables)
        wc.write_manifest(self.routes, {"terraform": self.variables})
        with self.assertRaisesRegex(wc.ConfigError, "separate DNS"):
            self.prepare()

    def test_cli_holds_activity_fence_and_requires_cancelled_ttl_revision(self):
        self.controller.cloud = object()
        with mock.patch.object(shared_cleanup.lifecycle, "run_lock", return_value=nullcontext()), \
                mock.patch.object(shared_cleanup.lifecycle, "Controller", return_value=self.controller), \
                mock.patch.object(shared_cleanup.ttl, "activity_operation", return_value=nullcontext()) as fence, \
                mock.patch.object(shared_cleanup, "prepare", return_value={"review_sha256": "fixture"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            result = shared_cleanup.main(["plan", "--pilot-run", str(self.controller.directory),
                "--bindings-run", str(self.bindings), "--routes", str(self.routes), "--output", str(self.output)])
        self.assertEqual(result, 0)
        self.assertTrue(fence.call_args.kwargs["cleanup_operation"])
        self.assertTrue(fence.call_args.kwargs["scope_change"])


if __name__ == "__main__":
    unittest.main()
