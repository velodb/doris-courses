"""Failure, privacy, ownership and bounded-retention tests for SSM diagnostics."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop as ws
import workshop_operations as ops
import test_workshop as lifecycle_fixture


SECRET = "fixture-sensitive-value-never-export"


def remote_report(config, binding):
    checks = [{"check": name, "passed": True} for name in ops.node.required_checks(["doris-rta"])]
    return {"schema_version": 1,
            "units": {name: {"LoadState": "not-found" if name == "gateway" else "loaded", "ActiveState": "active", "Result": "success"}
                      for name in ops.node.UNITS},
            "runtime": {"version_matches": True, "ready": True, "checks": checks},
            "probe_outcome": "completed", "log_policy_installed": True}


class ProjectionTests(unittest.TestCase):
    def test_ssm_fault_records_keep_only_identity_status_and_typed_times(self):
        from datetime import datetime, timezone
        value = {"Status": "Failed", "CommandId": "11111111-1111-1111-1111-111111111111",
                 "InstanceId": "i-00000000000000000", "ResponseCode": 1,
                 "RequestedDateTime": datetime(2026, 10, 31, tzinfo=timezone.utc),
                 "StandardOutputContent": SECRET, "StandardErrorContent": SECRET,
                 "Parameters": {"commands": [SECRET]}, "Comment": SECRET}
        summary = ops.invocation_summary(value)
        self.assertNotIn(SECRET, json.dumps(summary))
        self.assertEqual(summary["RequestedDateTime"], "2026-10-31T00:00:00Z")
        self.assertEqual(summary["ResponseCode"], 1)

    def test_raw_logs_headers_state_and_json_fields_cannot_escape(self):
        stream = io.StringIO("password=" + SECRET + "\nCookie: " + SECRET + "\nToken=" + SECRET +
            "\nmodel_key=" + SECRET + "\n-----BEGIN PRIVATE KEY-----\n" + SECRET + "\n" +
            json.dumps({"reason": "error", "user_data": SECRET, "state": SECRET}) + "\n" +
            "WORKSHOP_STAGE=course-environments\nconnection timed out " + SECRET + "\n")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bootstrap.log"
            ops.node.filter_stream(stream, output, path)
            self.assertNotIn(SECRET, output.getvalue() + path.read_text())
            self.assertIn('"reason": "network"', output.getvalue())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertLess(len(output.getvalue()), 512)

    def test_untrusted_remote_values_are_reprojected_at_cli_boundary(self):
        config = {"course_ref": "a" * 40}
        binding = {"runtime_sha256": "b" * 64}
        value = remote_report(config, binding)
        value.update(cookie=SECRET, user_data=SECRET, state={"password": SECRET})
        value["runtime"]["checks"].append({"check": SECRET, "passed": True})
        value["units"]["jupyter"]["Result"] = SECRET
        value["units"]["docker"]["Result"] = [SECRET]
        value["logs"] = {"bootstrap": {SECRET: 5, "error": 2}}
        projected = ops.project(value, config, binding, ["doris-rta"])
        self.assertNotIn(SECRET, json.dumps(projected))
        self.assertEqual(projected["gateway"], "not-installed")
        self.assertEqual(projected["units"]["docker"]["Result"], "unknown")
        self.assertFalse(projected["shared_access_validated"])
        value["runtime"]["checks"] = []
        self.assertFalse(ops.project(value, config, binding, ["doris-rta"])["runtime"]["ready"])

    def test_full_version_and_required_checks_are_needed(self):
        value = {"scope": "host-runtime", "course_ref": "a" * 40, "runtime_sha256": "b" * 64,
                 "ready": True, "checks": [{"check": c, "passed": True} for c in ops.node.required_checks(["doris-rta"])]}
        self.assertTrue(ops.node.probe_projection(value, "a" * 40, "b" * 64, ["doris-rta"])["ready"])
        for altered in ({**value, "course_ref": "c" * 40}, {**value, "checks": value["checks"][:-1]},
                        {**value, "checks": value["checks"] * 2}):
            self.assertFalse(ops.node.probe_projection(altered, "a" * 40, "b" * 64, ["doris-rta"])["ready"])

    def test_native_file_readiness_requires_each_installed_language_at_both_boundaries(self):
        legacy = ops.node.required_checks(['doris-rta'])
        names = legacy - {'doris-rta:interpreter-imports-cwd-restart-docker'} | {
            'content:en', 'content:kk-Cyrl',
            'doris-rta:en:interpreter-imports-cwd-restart-docker',
            'doris-rta:kk-Cyrl:interpreter-imports-cwd-restart-docker'}
        config = {'course_ref': 'a' * 40, 'content': {'abi': 'doris-workshop-content-3.0',
                  'available_locales': ['en', 'kk-Cyrl']}}
        binding = {'runtime_sha256': 'b' * 64}
        for absent in (None, 'doris-rta:kk-Cyrl:interpreter-imports-cwd-restart-docker'):
            checks = [{'check': name, 'passed': True} for name in names if name != absent]
            raw = {'scope': 'host-runtime', 'ready': True, 'course_ref': 'a' * 40,
                   'runtime_sha256': 'b' * 64, 'checks': checks}
            node = ops.node.probe_projection(raw, 'a' * 40, 'b' * 64, ['doris-rta'],
                                            ['en', 'kk-Cyrl'], course_files=True)
            self.assertEqual(node['ready'], absent is None)
            report = remote_report(config, binding)
            report['runtime']['checks'] = checks
            self.assertEqual(ops.project(report, config, binding, ['doris-rta'])['runtime']['ready'], absent is None)
        self.assertFalse(ops.node.probe_projection(raw, 'a' * 40, 'b' * 64,
                                                   ['doris-rta'], ['en', 'kk-Cyrl'])['ready'])

    def test_local_expiry_count_size_and_protected_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i in range(25):
                (root / f"diagnostic-{i:02d}.json").write_text("{}")
            old = root / "terraform-old.log"
            old.write_text(SECRET)
            os.utime(old, (time.time() - 8 * 86400,) * 2)
            state = root / "terraform.tfstate"
            state.write_text("must preserve")
            ops.prune_local(root)
            self.assertFalse(old.exists())
            self.assertEqual(len(list(root.glob("diagnostic-*"))), 20)
            self.assertEqual(state.read_text(), "must preserve")

    def test_node_log_rotation_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "var/log/doris-workshop/bootstrap.log"
            for _ in range(10):
                ops.node.append_log(path, '{"reason":"error"}\n', max_bytes=30)
            self.assertEqual(len(list(path.parent.glob("bootstrap.log*"))), 3)
            policy = root / "etc/doris-workshop/operations.json"
            policy.parent.mkdir(parents=True)
            policy.write_text('{"retention_days":7}')
            os.utime(path, (time.time() - 8 * 86400,) * 2)
            with mock.patch.object(ops.node, "ROOT", root):
                ops.node.prune()
            self.assertFalse(path.exists())

    def test_node_mismatched_activity_stops_before_probing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "etc/doris-workshop/activity.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"event_id":"other","learner_id":"s001"}')
            with mock.patch.object(ops.node, "ROOT", root), mock.patch.object(ops.node, "run") as run:
                with self.assertRaises(ValueError):
                    ops.node.collect("event", "s001", "a" * 40, "b" * 64, True)
                run.assert_not_called()

    def test_policy_is_private_bounded_and_replaces_existing_raw_installer_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "var/log/doris-workshop/bootstrap.log"
            path.parent.mkdir(parents=True)
            path.write_text("WORKSHOP_STAGE=system-packages\npassword=" + SECRET + "\nERROR " + SECRET +
                            "\nWORKSHOP_STAGE=readiness\n")
            completed = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.object(ops.node, "ROOT", root), mock.patch.object(ops.node, "run", return_value=completed):
                ops.node.configure(3)
                ops.node.configure(3)
            self.assertNotIn(SECRET, path.read_text())
            self.assertLess(path.read_text().index("system-packages"), path.read_text().index("readiness"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("MaxRetentionSec=3day", (root / "etc/systemd/journald.conf.d/doris-workshop.conf").read_text())
            self.assertIn("StandardOutput=null", (root / "etc/systemd/system/doris-workshop-jupyter.service.d/operations.conf").read_text())
            self.assertIn("maxsize 1M", (root / "etc/logrotate.d/doris-workshop").read_text())

    def test_tf_raw_diagnostics_are_never_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ws.Terraform(directory)
            result = subprocess.CompletedProcess([], 1, SECRET, SECRET)
            with mock.patch.object(ws.subprocess, "run", return_value=result), self.assertRaises(ws.wc.ConfigError):
                runner.execute(["plan"], {})
            self.assertNotIn(SECRET, "".join(p.read_text() for p in Path(directory).glob("*.log")))

    def test_old_image_probe_filters_before_ssm_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "etc/doris-workshop/runtime.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"course_ref": "a" * 40, "courses": [{"kernel": "doris-rta"}]}))
            import hashlib
            value = {"scope": "host-runtime", "course_ref": "a" * 40, "runtime_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "ready": True, "checks": [{"check": c, "passed": True} for c in ops.node.required_checks(["doris-rta"])],
                     "services": [{"name": "doris-rta-" + SECRET, "state": "running"}], "password": SECRET}
            result = subprocess.CompletedProcess([], 0, json.dumps(value), SECRET)
            with mock.patch.object(ops.node, "ROOT", root), mock.patch.object(ops.node, "run", return_value=result):
                report = ops.node.safe_probe()
            self.assertTrue(report["ready"])
            self.assertNotIn(SECRET, json.dumps(report))
            self.assertEqual(report["services"], [{"name": "doris-rta", "state": "running"}])


# Reuse the production binding fixture, without inheriting/rerunning its tests.
class DiagnosticTests(unittest.TestCase):
    setUpClass = classmethod(lifecycle_fixture.LifecycleTests.setUpClass.__func__)
    tearDownClass = classmethod(lifecycle_fixture.LifecycleTests.tearDownClass.__func__)
    setUp = lifecycle_fixture.LifecycleTests.setUp
    controller = lifecycle_fixture.LifecycleTests.controller

    def prepare(self):
        self.tf.create()
        self.client = mock.Mock()
        self.client.send_command.return_value = {"Command": {"CommandId": "11111111-1111-1111-1111-111111111111"}}
        self.cloud.client = lambda service: self.client
        self.remote = remote_report(self.config, self.binding)
        self.cloud.command = mock.Mock(return_value={"Status": "Success", "StandardOutputContent": json.dumps(self.remote), "StandardErrorContent": SECRET})
        return self.controller()

    def test_targeted_fresh_diagnostics_and_safe_evidence(self):
        controller = self.prepare()
        for _ in range(2):
            report = ops.diagnose(controller, learner="alice", timeout=2)
            self.assertEqual(set(report["nodes"]), {"alice"})
            self.assertTrue(report["ok"])
            self.assertTrue(report["nodes"]["alice"]["runtime"]["ready"])
        self.assertEqual(self.client.send_command.call_count, 2)
        args = self.client.send_command.call_args.kwargs
        self.assertEqual(args["InstanceIds"], ["i-alice"])
        self.assertEqual(args["CloudWatchOutputConfig"], {"CloudWatchOutputEnabled": False})
        for path in self.directory.glob("diagnostic-*.json"):
            self.assertNotIn(SECRET, path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_timeout_resumes_without_redispatch(self):
        controller = self.prepare()
        self.cloud.command.return_value = {"Status": "InProgress", "StandardOutputContent": SECRET}
        report = ops.diagnose(controller, learner="alice", timeout=2)
        self.assertFalse(report["ok"])
        self.cloud.command.return_value = {"Status": "Success", "StandardOutputContent": json.dumps(self.remote)}
        self.assertTrue(ops.diagnose(controller, learner="alice", timeout=2)["ok"])
        self.assertEqual(self.client.send_command.call_count, 1)

    def test_lost_dispatch_is_not_resent(self):
        controller = self.prepare()
        self.client.send_command.side_effect = RuntimeError(SECRET)
        with self.assertRaises(RuntimeError):
            ops.diagnose(controller, learner="alice")
        with self.assertRaisesRegex(ws.wc.ConfigError, "dispatch outcome unknown"):
            ops.diagnose(controller, learner="alice")
        self.assertEqual(self.client.send_command.call_count, 1)

    def test_unknown_learner_and_live_assignment_mismatch_do_not_dispatch(self):
        controller = self.prepare()
        with self.assertRaisesRegex(ws.wc.ConfigError, "unknown learner"):
            ops.diagnose(controller, learner="unknown")
        original = controller.actual_instances
        def swapped(ids):
            actual = original(ids)
            actual["i-alice"]["Tags"][-1]["Value"] = "bob"
            return actual
        controller.actual_instances = swapped
        with self.assertRaisesRegex(ws.wc.ConfigError, "assignment differs"):
            ops.diagnose(controller)
        self.client.send_command.assert_not_called()

    def test_ssm_failure_body_is_not_evidence(self):
        controller = self.prepare()
        self.cloud.command.return_value = {"Status": "Failed", "StandardOutputContent": SECRET, "StandardErrorContent": SECRET}
        report = ops.diagnose(controller, learner="alice", timeout=2)
        self.assertFalse(report["ok"])
        self.assertNotIn(SECRET, json.dumps(report))

    def test_no_dispatch_when_ssm_is_offline(self):
        controller = self.prepare()
        self.cloud.online = False
        report = ops.diagnose(controller, learner="alice")
        self.assertEqual(report["nodes"]["alice"]["outcome"], "ssm-offline")
        self.client.send_command.assert_not_called()

    def test_configure_only_accepts_controlled_success(self):
        controller = self.prepare()
        self.cloud.command.return_value = {"Status": "Success", "StandardOutputContent": json.dumps({"configured": True, "password": SECRET})}
        report = ops.diagnose(controller, learner="alice", configure=True, days=3)
        self.assertTrue(report["ok"])
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(json.loads((self.directory / "operations-policy.json").read_text())["retention_days"], 3)

    def test_sdk_validates_actual_diagnostic_request(self):
        import boto3
        from botocore.stub import Stubber
        controller = self.prepare()
        client = boto3.client("ssm", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture")
        self.cloud.client = lambda service: client
        self.cloud.command = lambda command_id, instance, **kwargs: ws.Cloud.command(self.cloud, command_id, instance, **kwargs)
        command_id = "11111111-1111-1111-1111-111111111111"
        with Stubber(client) as stub:
            stub.add_response("send_command", {"Command": {"CommandId": command_id}}, {
                "InstanceIds": ["i-alice"], "DocumentName": "AWS-RunShellScript", "TimeoutSeconds": 60,
                "Comment": "workshop controlled infrastructure diagnose", "CloudWatchOutputConfig": {"CloudWatchOutputEnabled": False},
                "Parameters": {"commands": [ops.command("diagnose", self.config, "alice", self.binding["runtime_sha256"], 7)], "executionTimeout": ["300"]}})
            stub.add_response("get_command_invocation", {"Status": "Success", "StandardOutputContent": json.dumps(self.remote)},
                              {"CommandId": command_id, "InstanceId": "i-alice"})
            self.assertTrue(ops.diagnose(controller, learner="alice")["ok"])
            stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
