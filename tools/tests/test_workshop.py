"""Controlled lifecycle integration: real saved-plan gate, fake Terraform and SSM."""

import copy
from datetime import datetime, timedelta, timezone
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop as ws
import workshop_runtime as builder


class Clock:
    now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeCloud:
    def __init__(self, config, kernels):
        self.config, self.kernels = config, kernels
        self.sent, self.commands = [], {}
        self.online, self.pending, self.fail_install = True, False, set()
        self.deleted, self.residual_volume = False, False
        self.account = config["aws_account_id"]
        self.runtime_sha256 = None

    def call(self, service, operation, **kwargs):
        if operation == "get_caller_identity":
            return {"Account": self.account, "Arn": f"arn:aws:iam::{self.account}:user/operator"}
        if operation == "describe_instances":
            rows = []
            for instance in kwargs["Filters"][0]["Values"]:
                learner = instance.split("-", 1)[1]
                rows.append({"InstanceId": instance, "State": {"Name": "terminated" if self.deleted else "running"},
                             "Tags": [{"Key": k, "Value": v} for k, v in {
                                 "Project": "doris-workshop", "Event": self.config["event_id"], "Owner": self.config["owner_id"],
                                 "Scope": "activity", "Learner": learner}.items()]})
            return {"Reservations": [{"Instances": rows}]}
        if operation == "describe_instance_information":
            return {"InstanceInformationList": [{"PingStatus": "Online" if self.online else "Offline"}]}
        if operation == "describe_volumes":
            return {"Volumes": [{"VolumeId": "vol-residual"}] if self.residual_volume else []}
        raise AssertionError(operation)

    def send(self, instance, document, *, probe=False):
        command = f"command-{len(self.sent)}"
        self.sent.append((instance, document, probe))
        self.commands[command] = (instance, probe)
        return command

    def command(self, command_id, instance, *, probe=False):
        if self.pending:
            return {"Status": "InProgress"}
        if not probe:
            return {"Status": "Failed" if instance in self.fail_install else "Success", "StandardErrorContent": "PRIVATE-TOKEN"}
        names = ["target-platform", "learner-identity", "source-version", "jupyter-systemd",
                 "jupyter-authenticated-loopback", "course-entry", "docker-daemon-and-permission", "compose"]
        names += [k + ":interpreter-imports-cwd-restart-docker" for k in self.kernels]
        names += [k + ":notebook-bindings" for k in self.kernels]
        return {"Status": "Success", "StandardOutputContent": json.dumps({"scope": "host-runtime", "ready": True,
                "course_ref": self.config["course_ref"], "runtime_sha256": self.runtime_sha256,
                "checks": [{"check": n, "passed": True} for n in names],
                "services": [{"name": "doris-rta", "state": "running"}], "token": "PRIVATE-TOKEN"})}


class FakeTerraform:
    def __init__(self, directory, config, cloud):
        self.directory, self.config, self.cloud = directory, config, cloud
        self.resources, self.apply_count = [], 0
        self.fail_apply, self.fail_destroy, self.bad_destroy = False, False, False
        self.calls = []

    def create(self):
        tags = {"Project": "doris-workshop", "Event": self.config["event_id"], "Owner": self.config["owner_id"], "Scope": "activity"}
        self.resources = [{"mode": "managed", "address": f'aws_instance.learner["{learner}"]', "type": "aws_instance",
                           "name": "learner", "index": learner, "values": {"id": "i-" + learner,
                           "tags": {"Learner": learner}, "tags_all": {**tags, "Learner": learner},
                           "root_block_device": [{"volume_id": "vol-" + learner}]}} for learner in self.config["learner_ids"]]
        self.resources.append({"mode": "managed", "address": "aws_ssm_document.install", "type": "aws_ssm_document",
                               "name": "install", "values": {"id": "install", "name": "install", "tags_all": tags}})

    def execute(self, args, env, **kwargs):
        self.calls.append(list(args))
        if "apply" in args:
            self.apply_count += 1
            self.create()
            if self.fail_apply:
                raise ws.wc.ConfigError("terraform: partial failure; resources remain")

    def state(self):
        return copy.deepcopy(self.resources)

    def run(self, args, *, capture=False):
        self.calls.append(list(args))
        if args[0] == "show":
            changes = [{"mode": "managed", "address": r["address"], "change": {"actions": ["delete"]}} for r in self.resources]
            if self.bad_destroy:
                changes.append({"mode": "managed", "address": "aws_s3_bucket.foundation", "change": {"actions": ["delete"]}})
            return json.dumps({"resource_changes": changes})
        if args[0] == "apply":
            if self.fail_destroy:
                raise ws.wc.ConfigError("terraform: destroy failed")
            self.resources, self.cloud.deleted = [], True


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        config = ws.wc.load_document(ws.wc.SPEC / "examples/event.example.yaml")
        config["course_ref"] = ws.wc.git(ws.wc.REPO, "rev-parse", "HEAD").strip()
        config["learner_ids"] = ["alice", "bob"]
        config["image"]["owner_account_id"] = ws.pf.CANONICAL
        config["expires_at"] = ws.wc.utc_text(datetime.now(timezone.utc) + timedelta(hours=3))
        path = cls.root / "config.json"
        path.write_text(json.dumps(config))
        cls.bundle = cls.root / "bundle"
        cls.runtime = builder.create_bundle(path, cls.bundle)
        pilot = ws.wc.load_document(ws.wc.SPEC / "examples/pilot.example.yaml")
        pilot["runtime_bundle_dir"] = str(cls.bundle)
        cls.config, cls.pilot = ws.pf.validate_inputs(config, pilot)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory()
        self.addCleanup(self.case.cleanup)
        self.directory = Path(self.case.name)
        self.config = copy.deepcopy(type(self).config)
        self.pilot = copy.deepcopy(type(self).pilot)
        self.cloud = FakeCloud(self.config, [c["kernel"] for c in self.runtime["courses"]])
        self.cloud.runtime_sha256 = ws.pf.file_hash(self.bundle / "runtime.json")
        self.tf = FakeTerraform(self.directory, self.config, self.cloud)
        self.clock = Clock()
        variables = ws.pf.inputs_for_terraform(self.config, self.pilot, "pilot", self.config["region"] + "a")
        backend = {"bucket": self.pilot["state_bucket_name"], "region": self.config["region"], "profile": self.config["aws_profile"],
                   "allowed_account_ids": [self.config["aws_account_id"]], "encrypt": True, "use_lockfile": True,
                   "key": f"aws-workshop/aws/{self.config['aws_account_id']}/{self.config['region']}/{self.config['event_id']}/terraform.tfstate"}
        ws.atomic_json(self.directory / "inputs.tfvars.json", variables)
        ws.atomic_json(self.directory / "backend.tfbackend.json", backend)
        (self.directory / "saved.tfplan").write_bytes(b"reviewed-plan")
        self.binding = {"stage": "pilot", "config": self.config, "pilot": self.pilot,
                        "vars_sha256": ws.pf.file_hash(self.directory / "inputs.tfvars.json"),
                        "backend_sha256": ws.pf.file_hash(self.directory / "backend.tfbackend.json"),
                        "plan_sha256": ws.pf.file_hash(self.directory / "saved.tfplan"),
                        "runtime_sha256": ws.pf.file_hash(self.bundle / "runtime.json"),
                        "module_sha256": ws.pf.module_hash("pilot"), "tool_sha256": ws.pf.tool_hash()}
        ws.atomic_json(self.directory / "binding.json", self.binding)
        self.report = {"status": "passed", "config_sha256": ws.wc.canonical_hash(self.config),
                       "pilot_sha256": ws.wc.canonical_hash(self.pilot),
                       "valid_until": ws.wc.utc_text(datetime.now(timezone.utc) + timedelta(minutes=10)),
                       "availability_zones": [variables["availability_zone"]]}
        ws.atomic_json(self.directory / "preflight.json", self.report)
        patcher = mock.patch.object(ws.pf, "live_check", return_value=self.report)
        self.live = patcher.start()
        self.addCleanup(patcher.stop)

    def controller(self):
        return ws.Controller(self.directory, cloud=self.cloud, terraform=self.tf, clock=self.clock, sleep=self.clock.sleep)

    def up(self, **kwargs):
        return self.controller().up(self.binding["plan_sha256"], timeout=10, **kwargs)

    def test_saved_plan_to_ready_and_repeated_up_do_not_reapply_or_reinstall(self):
        result = self.up()
        self.assertTrue(result["ready"])
        self.assertFalse(result["course_labs_validated"])
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["nodes"]["alice"]["probe"]["services"], [{"name": "doris-rta", "state": "running"}])
        self.assertEqual(self.tf.apply_count, 1)
        self.assertTrue(self.live.called)
        self.assertTrue(self.up()["ready"])
        self.assertEqual(self.tf.apply_count, 1)
        self.assertEqual(len([c for c in self.cloud.sent if not c[2]]), 2)
        self.assertEqual(len([c for c in self.cloud.sent if c[2]]), 4)
        text = (self.directory / "lifecycle.json").read_text()
        self.assertNotIn("PRIVATE-TOKEN", text)
        self.assertEqual((self.directory / "lifecycle.json").stat().st_mode & 0o777, 0o600)

    def test_ec2_running_without_ssm_times_out_and_can_resume(self):
        self.cloud.online = False
        with self.assertRaisesRegex(ws.wc.ConfigError, "timed out"):
            self.up()
        self.assertEqual(self.cloud.sent, [])
        record = self.controller().record
        self.assertEqual(record["phase"], "provisioned")
        self.assertEqual(record["nodes"]["alice"]["ec2"], "running")
        self.assertFalse(record["nodes"]["alice"]["ready"])
        self.cloud.online = True
        self.assertTrue(self.up()["ready"])
        self.assertEqual(self.tf.apply_count, 1)

    def test_pending_install_resumes_same_ssm_command_after_timeout(self):
        self.cloud.pending = True
        with self.assertRaises(ws.wc.ConfigError):
            self.up()
        commands = copy.deepcopy(self.controller().record["nodes"])
        self.cloud.pending = False
        self.assertTrue(self.up()["ready"])
        for learner, node in commands.items():
            self.assertEqual(self.controller().record["nodes"][learner]["install"]["command_id"], node["install"]["command_id"])

    def test_unknown_ssm_dispatch_is_not_silently_resent(self):
        original = self.cloud.send
        def lost_response(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError("response lost")
        self.cloud.send = lost_response
        with self.assertRaises(TimeoutError):
            self.up()
        self.cloud.send = original
        with self.assertRaises(ws.wc.ConfigError):
            self.up(retry_failed=True)
        self.assertEqual(len([c for c in self.cloud.sent if c[0] == "i-alice" and not c[2]]), 1)

    def test_partial_failure_records_both_nodes_and_retry_is_bounded(self):
        self.cloud.fail_install = {"i-bob"}
        with self.assertRaisesRegex(ws.wc.ConfigError, "one or more"):
            self.up()
        record = self.controller().record
        self.assertTrue(record["nodes"]["alice"]["ready"])
        self.assertFalse(record["nodes"]["bob"]["ready"])
        for _ in range(4):
            with self.assertRaises(ws.wc.ConfigError):
                self.up(retry_failed=True)
        self.assertEqual(self.controller().record["nodes"]["bob"]["install"]["attempts"], 3)
        self.assertEqual(len([c for c in self.cloud.sent if not c[2] and c[0] == "i-alice"]), 1)

    def test_failed_apply_cannot_replay_stale_plan_and_destroy_remains_available(self):
        self.tf.fail_apply = True
        with self.assertRaises(ws.wc.ConfigError):
            self.up()
        self.assertEqual(self.controller().record["phase"], "apply-failed")
        with self.assertRaisesRegex(ws.wc.ConfigError, "previous attempt"):
            self.up()
        self.assertEqual(self.tf.apply_count, 1)
        result = self.controller().destroy()
        self.assertEqual(result["phase"], "destroyed")
        self.assertFalse(result["resources_may_remain"])
        self.assertEqual(self.controller().destroy()["phase"], "destroyed")

    def test_destroy_failure_and_volume_residual_are_reported_then_retried(self):
        self.up()
        self.tf.fail_destroy = True
        with self.assertRaisesRegex(ws.wc.ConfigError, "destroy failed"):
            self.controller().destroy()
        self.tf.fail_destroy = False
        self.cloud.residual_volume = True
        with self.assertRaisesRegex(ws.wc.ConfigError, "residual"):
            self.controller().destroy()
        self.assertEqual(self.controller().record["reconciliation"]["remaining_known_volumes"], 1)
        self.cloud.residual_volume = False
        self.assertEqual(self.controller().destroy()["phase"], "destroyed")

    def test_wrong_account_changed_backend_unknown_resource_and_destroy_plan_fail_closed(self):
        self.cloud.account = "111111111111"
        with self.assertRaisesRegex(ws.wc.ConfigError, "identity"):
            self.controller()
        self.cloud.account = self.config["aws_account_id"]
        self.up()
        self.tf.bad_destroy = True
        with self.assertRaisesRegex(ws.wc.ConfigError, "unowned"):
            self.controller().destroy()
        self.tf.bad_destroy = False
        self.tf.resources[0]["values"]["tags_all"]["Event"] = "other"
        with self.assertRaisesRegex(ws.wc.ConfigError, "tags"):
            self.controller().destroy()
        (self.directory / "backend.tfbackend.json").write_text("{}")
        with self.assertRaisesRegex(ws.wc.ConfigError, "backend changed"):
            self.controller()

    def test_unapproved_or_changed_plan_never_applies(self):
        with self.assertRaisesRegex(ws.wc.ConfigError, "reviewed"):
            self.controller().up("wrong-hash")
        self.assertEqual(self.tf.apply_count, 0)
        (self.directory / "saved.tfplan").write_bytes(b"changed")
        with self.assertRaises(ws.wc.ConfigError):
            self.up()
        self.assertEqual(self.tf.apply_count, 0)

    def test_status_uses_a_fresh_probe_and_does_not_install(self):
        self.up()
        before = len(self.cloud.sent)
        self.assertTrue(self.controller().inspect(timeout=10)["ready"])
        self.assertTrue(all(c[2] for c in self.cloud.sent[before:]))
        self.cloud.command = lambda *a, **k: {"Status": "Success", "StandardOutputContent": '{"ready": true}'}
        result = self.controller().inspect(timeout=10)
        self.assertFalse(result["ready"])

    def test_status_detects_wrong_runtime_and_untracked_billing_resources(self):
        self.up()
        self.cloud.runtime_sha256 = "wrong-version"
        self.assertFalse(self.controller().inspect(timeout=10)["ready"])
        self.tf.resources = []
        result = self.controller().inspect(timeout=10)
        self.assertFalse(result["ready"])
        self.assertTrue(result["resources_may_remain"])
        self.assertEqual(result["untracked_instances"], ["i-alice", "i-bob"])

    def test_cleanup_does_not_require_unexpired_creation_preflight(self):
        self.up()
        self.binding["config"]["expires_at"] = "2020-01-01T00:00:00Z"
        variables = ws.wc.load_document(self.directory / "inputs.tfvars.json")
        variables["expires_at"] = self.binding["config"]["expires_at"]
        ws.atomic_json(self.directory / "inputs.tfvars.json", variables)
        self.binding["vars_sha256"] = ws.pf.file_hash(self.directory / "inputs.tfvars.json")
        ws.atomic_json(self.directory / "binding.json", self.binding)
        self.live.side_effect = AssertionError("cleanup must not query prices")
        self.assertEqual(self.controller().destroy()["phase"], "destroyed")

    def test_local_lock_rejects_concurrent_operations(self):
        with ws.run_lock(self.directory), self.assertRaisesRegex(ws.wc.ConfigError, "another operation"):
            with ws.run_lock(self.directory):
                self.fail("second lock acquired")

    def test_ordinary_error_output_redacts_sdk_or_target_messages(self):
        with mock.patch.object(ws, "Controller", side_effect=RuntimeError("PRIVATE-TOKEN")), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stream:
            result = ws.main(["status", "--run-dir", str(self.directory), "--timeout", "1"])
        self.assertEqual(result, 2)
        self.assertNotIn("PRIVATE-TOKEN", stream.getvalue())
        self.assertTrue(json.loads(stream.getvalue())["resources_may_remain"])


@unittest.skipUnless(importlib.util.find_spec("boto3"), "Install preflight-requirements.txt for SDK contract tests")
class SsmSdkTests(unittest.TestCase):
    def test_real_sdk_shapes_and_eventual_consistency(self):
        import boto3
        from botocore.stub import Stubber

        client = boto3.client("ssm", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture")
        cloud = object.__new__(ws.Cloud)
        cloud.client = lambda service: client
        command = "11111111-1111-1111-1111-111111111111"
        instance = "i-00000000000000000"
        with Stubber(client) as stub:
            stub.add_response("send_command", {"Command": {"CommandId": command}},
                              {"InstanceIds": [instance], "DocumentName": "AWS-RunShellScript", "TimeoutSeconds": 600,
                               "Parameters": {"commands": [ws.PROBE], "executionTimeout": ["300"]}})
            stub.add_client_error("get_command_invocation", service_error_code="InvocationDoesNotExist",
                                  expected_params={"CommandId": command, "InstanceId": instance})
            stub.add_response("list_command_invocations", {"CommandInvocations": [{"Status": "Failed"}]},
                              {"CommandId": command, "InstanceId": instance, "Details": False})
            self.assertEqual(cloud.send(instance, "AWS-RunShellScript", probe=True), command)
            self.assertEqual(cloud.command(command, instance, probe=True)["Status"], "Pending")
            self.assertEqual(cloud.command(command, instance)["Status"], "Failed")
            stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
