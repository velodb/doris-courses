"""Shared portal update isolation and uncertain SSM dispatch handling."""
from contextlib import nullcontext
import copy
import grp
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_access as access
import workshop_config as wc
import workshop_portal_update as portal
import workshop_preflight as pf
from test_workshop_access import ARN, config as gateway_fixture
from test_workshop_gateway_deploy import Cloud


class Controller:
    def __init__(self):
        self.config = {"event_id": "event", "aws_account_id": "000000000000", "region": "us-east-1",
            "domain": "workshops.example.com", "learner_ids": ["alice"], "course_profiles": ["rta-main"],
            "expires_at": "2099-01-01T00:00:00Z", "owner_id": "operator", "aws_profile": "offline"}
        self.shared = {"account_id": "000000000000", "region": "us-east-1", "domain": "workshops.example.com",
            "alb_arn": ARN, "access_id": "shared", "vpc_id": "vpc-1", "portal_security_group_id": "sg-1",
            "portal": {"instance_id": "i-portal", "target_group_arn": "tg-portal", "listener_priority": 100}}
        self.binding, self.pilot, self.cloud = {"shared_access": self.shared, "tool_sha256": pf.tool_hash()}, {"state_bucket_name": "state"}, Cloud()
        self.cloud.call = self.call
        self.instances = {"alice": "i-alice"}
        self.record = {"known_instances": ["i-alice"], "nodes": {"alice": {"instance_id": "i-alice"}}}

    def call(self, service, operation, **kwargs):
        return {"Reservations": [{"Instances": [{"InstanceId": "i-portal", "State": {"Name": "running"}, "VpcId": "vpc-1",
            "SecurityGroups": [{"GroupId": "sg-1"}], "Tags": [{"Key": k, "Value": v} for k, v in {
                "Project": "doris-workshop", "Scope": "shared-portal", "Owner": "operator", "Access": "shared"}.items()]}]}]}

    def inventory(self):
        return self.instances, "document"

    def actual_instances(self, values):
        return {i: {"State": {"Name": "running"}, "Tags": [{"Key": "Learner", "Value": "alice"}]} for i in values}

    def save(self):
        pass


class StateTerraform:
    def __init__(self, *args):
        pass

    def init(self, *args):
        pass

    def state(self):
        return {"resources": [{"type": "aws_cognito_user_pool", "values": {"id": "us-east-1_test"}},
            {"type": "aws_cognito_user_pool_client", "values": {"id": "client", "user_pool_id": "us-east-1_test"}}]}


class PortalUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bindings = self.root / "bindings"
        self.bindings.mkdir()
        self.controller = Controller()
        c = self.controller.config
        self.route = access.routes(c, self.controller.shared, self.controller.instances)
        self.identity = {k: gateway_fixture()[k] for k in ("event_id", "issuer", "login_origin")}
        self.identity.update(user_pool_id="us-east-1_test", client_id="client")
        self.subjects = {"alice": ["subject-alice"]}
        self.previous = {"schema_version": 1, "domain": c["domain"], "alb_arn": ARN, "region": c["region"], "activities": {}}
        self.previous_file = self.root / "previous.json"
        wc.write_manifest(self.previous_file, self.previous)
        wc.write_manifest(self.bindings / "inputs.tfvars.json", self.route["terraform"])
        wc.write_manifest(self.bindings / "backend.tfbackend.json", {"bucket": "state", "region": c["region"],
            "profile": c["aws_profile"], "key": "aws-workshop/aws/000000000000/us-east-1/event/access.tfstate",
            "allowed_account_ids": [c["aws_account_id"]], "encrypt": True, "use_lockfile": True})
        self.output = self.root / "operation"
        self.controller.cloud.output = "portal-update-ready\n"

    def test_registry_update_preserves_other_activities_and_validates_ownership(self):
        # Another activity is a valid opaque entry and must survive byte-for-byte as data.
        other_config = {**self.controller.config, "event_id": "another"}
        other_route = access.routes(other_config, self.controller.shared, self.controller.instances)
        other_identity = {**self.identity, "event_id": "another"}
        other = access.portal_config([other_route], [other_identity], [self.subjects])["activities"]["another"]
        previous = {**self.previous, "activities": {"another": other}}
        with mock.patch.object(portal.shared_cleanup, "Terraform", StateTerraform), \
                mock.patch.object(portal.bindings, "verify_state"):
            instance, result = portal.verify(self.controller, self.bindings, self.route, self.route, self.identity, self.subjects, previous)
            self.assertEqual(instance, "i-portal")
            self.assertEqual(result["activities"]["another"], other)
            self.assertIn("event", result["activities"])
            _, removed = portal.verify(self.controller, self.bindings, self.route, self.route,
                                       self.identity, self.subjects, result, remove_activity=True)
            self.assertEqual(removed["activities"], {"another": other})
            broken = copy.deepcopy(result)
            broken["activities"]["event"]["client_id"] = "different"
            with self.assertRaises(ValueError):
                portal.verify(self.controller, self.bindings, self.route, self.route,
                              self.identity, self.subjects, broken, remove_activity=True)
            with self.assertRaisesRegex(ValueError, "subject differs"):
                portal.verify(self.controller, self.bindings, self.route, self.route, self.identity, {"alice": ["wrong"]}, previous)

    def registered(self):
        return access.portal_config([self.route], [self.identity], [self.subjects])

    def test_removal_accepts_nonrunning_or_missing_learners_but_registration_does_not(self):
        for state in ("stopping", "stopped", "shutting-down", "terminated", None):
            with self.subTest(state=state):
                actual = {} if state is None else {"i-alice": {"State": {"Name": state},
                    "Tags": [{"Key": "Learner", "Value": "alice"}]}}
                with mock.patch.object(portal.shared_cleanup, "Terraform", StateTerraform), \
                        mock.patch.object(portal.bindings, "verify_state"), \
                        mock.patch.object(self.controller, "actual_instances", return_value=actual):
                    _, result = portal.verify(self.controller, self.bindings, self.route, self.route,
                        self.identity, self.subjects, self.registered(), remove_activity=True)
                    self.assertEqual(result["activities"], {})
                    with self.assertRaisesRegex(ValueError, "not running"):
                        portal.verify(self.controller, self.bindings, self.route, self.route,
                            self.identity, self.subjects, self.previous)

    def test_removal_after_refresh_requires_the_saved_instance_assignment(self):
        self.controller.instances = {}
        with mock.patch.object(portal.shared_cleanup, "Terraform", StateTerraform), \
                mock.patch.object(portal.bindings, "verify_state"), \
                mock.patch.object(self.controller, "actual_instances", return_value={}) as actual:
            _, result = portal.verify(self.controller, self.bindings, self.route, self.route,
                self.identity, self.subjects, self.registered(), remove_activity=True)
            self.assertEqual(result["activities"], {})
            self.assertEqual(set(actual.call_args.args[0]), {"i-alice"})
            for history in ({}, {"known_instances": [], "nodes": {"alice": {"instance_id": "i-alice"}}},
                            {"known_instances": ["i-alice"], "nodes": {"alice": {"instance_id": "i-other"}}}):
                with self.subTest(history=history):
                    self.controller.record = history
                    with self.assertRaisesRegex(ValueError, "saved pilot assignment"):
                        portal.verify(self.controller, self.bindings, self.route, self.route,
                            self.identity, self.subjects, self.registered(), remove_activity=True)

    def test_removal_rejects_reassigned_instances_and_live_ownership_changes(self):
        with mock.patch.object(portal.shared_cleanup, "Terraform", StateTerraform), \
                mock.patch.object(portal.bindings, "verify_state"):
            self.controller.instances = {"alice": "i-replacement"}
            with self.assertRaisesRegex(ValueError, "current pilot instances"):
                portal.verify(self.controller, self.bindings, self.route, self.route,
                    self.identity, self.subjects, self.registered(), remove_activity=True)
            self.controller.instances = {"alice": "i-alice"}
            with mock.patch.object(self.controller, "actual_instances", side_effect=wc.ConfigError("ownership differs")):
                with self.assertRaisesRegex(ValueError, "ownership differs"):
                    portal.verify(self.controller, self.bindings, self.route, self.route,
                        self.identity, self.subjects, self.registered(), remove_activity=True)
            with mock.patch.object(self.controller, "actual_instances", return_value={"i-alice": {
                    "State": {"Name": "terminated"}, "Tags": [{"Key": "Learner", "Value": "bob"}]}}):
                with self.assertRaisesRegex(ValueError, "learner tag differs"):
                    portal.verify(self.controller, self.bindings, self.route, self.route,
                        self.identity, self.subjects, self.registered(), remove_activity=True)

    def test_removal_requires_a_running_operator_portal(self):
        response = self.controller.call("ec2", "describe_instances")
        response["Reservations"][0]["Instances"][0]["State"]["Name"] = "stopped"
        with mock.patch.object(portal.shared_cleanup, "Terraform", StateTerraform), \
                mock.patch.object(portal.bindings, "verify_state"), \
                mock.patch.object(self.controller.cloud, "call", return_value=response):
            with self.assertRaisesRegex(ValueError, "portal host or network differs"):
                portal.verify(self.controller, self.bindings, self.route, self.route,
                    self.identity, self.subjects, self.registered(), remove_activity=True)

    def test_shared_cleanup_review_and_dispatch_remove_only_the_expired_assignment(self):
        other_config = {**self.controller.config, "event_id": "another"}
        other_route = access.routes(other_config, self.controller.shared, self.controller.instances)
        other_identity = {**self.identity, "event_id": "another"}
        previous = access.portal_config([self.route, other_route], [self.identity, other_identity],
            [self.subjects, self.subjects])
        self.previous_file = self.root / "registered.json"
        wc.write_manifest(self.previous_file, previous)
        files = {"registry": self.previous_file}
        for key, value in (("identity", self.identity), ("subjects", self.subjects)):
            files[key] = self.root / (key + ".json")
            wc.write_manifest(files[key], value)
        route_file = self.root / "route.json"
        wc.write_manifest(route_file, self.route)
        self.controller.instances = {}
        with mock.patch.object(portal.shared_cleanup, "Terraform", StateTerraform), \
                mock.patch.object(portal.bindings, "verify_state"), \
                mock.patch.object(self.controller, "actual_instances", return_value={}), \
                mock.patch.object(self.controller.cloud, "send_command", wraps=self.controller.cloud.send_command) as send:
            receipt = portal.shared_cleanup.portal_review(self.controller, self.bindings, route_file, files)
            result = portal.update(self.controller, self.bindings, self.route, self.route,
                self.identity, self.subjects, self.previous_file, self.output, remove_activity=True)
        self.assertEqual(result["status"], "Success")
        next_registry = wc.load_document(self.output / "next-portal.json")
        self.assertEqual(receipt["next_registry_sha256"], wc.canonical_hash(next_registry))
        self.assertEqual(next_registry["activities"], {"another": previous["activities"]["another"]})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs["InstanceIds"], ["i-portal"])

    def test_remote_code_compiles_and_binds_old_and_new_hash(self):
        commands, old_hash, new_hash = portal.remote_commands(b"old", b"new")
        self.assertIn(old_hash, commands[2])
        self.assertIn(new_hash, commands[2])
        self.assertIn("fcntl.flock", commands[2])
        script = commands[2].split("\n", 1)[1].rsplit("\nPY", 1)[0]
        compile(script, "remote", "exec")
        with self.assertRaises(ValueError):
            portal.remote_commands(b"old", b"x" * 30000)

    def test_remote_update_compares_previous_content_under_lock(self):
        target = self.root / "portal.json"
        target.write_bytes(b"old")
        lock = self.root / "portal.lock"
        portal_source = self.root / "portal.py"
        gateway_source = self.root / "gateway.py"
        portal_source.write_bytes((access.OVERLAY / "portal.py").read_bytes())
        gateway_source.write_bytes((access.OVERLAY / "gateway.py").read_bytes())
        commands, _, _ = portal.remote_commands(b"old", b"new")
        script = commands[2].split("\n", 1)[1].rsplit("\nPY", 1)[0]
        script = script.replace("/etc/doris-workshop-portal/portal.json", str(target)).replace(
            "/run/lock/doris-workshop-portal-registry.lock", str(lock)).replace(
            "/opt/doris-workshop-portal/portal.py", str(portal_source)).replace(
            "/opt/doris-workshop-portal/gateway.py", str(gateway_source))
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.object(os, "chown"), mock.patch.object(grp, "getgrnam", return_value=SimpleNamespace(gr_gid=1)), \
                mock.patch.object(subprocess, "run"), mock.patch.object(urllib.request, "urlopen", return_value=response), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            exec(compile(script, "remote", "exec"), {})
            self.assertEqual(target.read_bytes(), b"new")
            with self.assertRaisesRegex(AssertionError, "registry changed"):
                exec(compile(script, "remote", "exec"), {})
            self.assertEqual(target.read_bytes(), b"new")

    def test_success_reuses_receipt_and_unknown_dispatch_is_not_resent(self):
        with mock.patch.object(portal, "verify", return_value=("i-portal", self.previous)):
            args = (self.controller, self.bindings, self.route, self.route, self.identity, self.subjects, self.previous_file, self.output)
            result = portal.update(*args)
            self.assertFalse(result["reused_receipt"])
            self.assertTrue(portal.update(*args)["reused_receipt"])
            self.assertEqual(self.controller.cloud.sent, 1)
            record = json.loads((self.output / "dispatch.json").read_text())
            record["status"] = "dispatching"
            record.pop("command_id")
            (self.output / "dispatch.json").write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "dispatch outcome unknown"):
                portal.update(*args)
            self.assertEqual(self.controller.cloud.sent, 1)

    def test_lost_dispatch_response_and_duplicate_json_do_not_retry(self):
        with mock.patch.object(portal, "verify", return_value=("i-portal", self.previous)), \
                mock.patch.object(self.controller.cloud, "send_command", side_effect=TimeoutError("unknown")) as send:
            args = (self.controller, self.bindings, self.route, self.route, self.identity, self.subjects, self.previous_file, self.output)
            with self.assertRaises(TimeoutError):
                portal.update(*args)
            with self.assertRaisesRegex(ValueError, "dispatch outcome unknown"):
                portal.update(*args)
            self.assertEqual(send.call_count, 1)
        self.previous_file.write_text('{"activities":{},"activities":{}}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            portal.update(self.controller, self.bindings, self.route, self.route, self.identity, self.subjects,
                          self.previous_file, self.root / "another")

    def test_cli_requires_activity_fence(self):
        c = self.controller
        c.directory = self.root
        with mock.patch.object(portal.lifecycle, "run_lock", return_value=nullcontext()), \
                mock.patch.object(portal.lifecycle, "Controller", return_value=c), \
                mock.patch.object(portal.ttl, "activity_operation", return_value=nullcontext()) as fence, \
                mock.patch.object(portal.wc, "load_document", return_value={}), \
                mock.patch.object(portal, "update", return_value={"status": "Success"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            args = []
            for name in ("pilot-run", "bindings-run", "current-route", "plan", "identity", "subjects", "previous-registry", "output"):
                args.extend(["--" + name, str(self.root)])
            self.assertEqual(portal.main(args), 0)
        self.assertTrue(fence.call_args.kwargs["scope_change"])


if __name__ == "__main__":
    unittest.main()
