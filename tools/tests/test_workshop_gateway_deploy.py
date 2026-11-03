"""Gateway rollout ownership and resumable dispatch boundaries."""
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_access as access
import workshop_gateway_deploy as deploy
import workshop_preflight as pf
from test_workshop_access import ARN, config as gateway_fixture


class Cognito:
    def list_tags_for_resource(self, **kwargs):
        return {"Tags": {"Project": "doris-workshop", "Scope": "activity-access", "Event": "event", "Owner": "operator"}}

    def describe_user_pool_client(self, **kwargs):
        return {"UserPoolClient": {"ClientId": kwargs["ClientId"], "UserPoolId": kwargs["UserPoolId"]}}

    def describe_user_pool_domain(self, **kwargs):
        return {"DomainDescription": {"UserPoolId": "us-east-1_test"}}

    def admin_get_user(self, **kwargs):
        return {"Username": kwargs["Username"], "Enabled": True,
                "UserAttributes": [{"Name": "sub", "Value": "subject-" + kwargs["Username"]}]}


class Cloud:
    def __init__(self):
        self.cognito = Cognito()
        self.sent = 0
        self.output = "gateway-ready\n"
        self.status = "Success"

    def client(self, name):
        return self.cognito if name == "cognito-idp" else self

    def dispatch_client(self):
        return self

    def send_command(self, **kwargs):
        self.sent += 1
        return {"Command": {"CommandId": "command-" + str(self.sent)}}

    def get_command_invocation(self, **kwargs):
        return {"Status": self.status, "StandardOutputContent": self.output}

    class exceptions:
        class InvocationDoesNotExist(Exception):
            pass


class Controller:
    def __init__(self, config, shared, instances, routes):
        self.config = config
        self.binding = {"shared_access": shared, "browser_routes": routes, "tool_sha256": pf.tool_hash()}
        self.instances = instances
        self.cloud = Cloud()
        self.record = {}
        self.saves = 0

    def inventory(self):
        return self.instances, "document"

    def actual_instances(self, values):
        return {v: {"State": {"Name": "running"}} for v in values}

    def save(self):
        self.saves += 1


class GatewayDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.config = {"event_id": "event", "aws_account_id": "000000000000", "region": "us-east-1",
            "domain": "workshops.example.com", "learner_ids": ["alice", "bob"], "course_profiles": ["rta-main"],
            "expires_at": "2099-01-01T00:00:00Z", "owner_id": "operator", "aws_profile": "offline-test"}
        self.shared = {"account_id": self.config["aws_account_id"], "region": self.config["region"],
                       "domain": self.config["domain"], "alb_arn": ARN}
        self.instances = {"alice": "i-00000000000000001", "bob": "i-00000000000000002"}
        self.plan = access.routes(self.config, self.shared, self.instances)
        self.identity = {k: gateway_fixture()[k] for k in ("event_id", "issuer", "client_id", "login_origin")}
        self.identity["user_pool_id"] = "us-east-1_test"
        self.subjects = {"alice": ["subject-alice"], "bob": ["subject-bob"]}
        self.controller = Controller(self.config, self.shared, self.instances, self.plan["browser_routes"])

    def test_validated_inputs_bind_exact_pool_subjects_hosts_and_pilot(self):
        instances, configs = deploy.verify_assignments(self.controller, self.plan, self.identity, self.subjects)
        self.assertEqual(instances, self.instances)
        self.assertEqual(configs["alice"]["subjects"], ["subject-alice"])
        self.assertNotIn("subject-bob", json.dumps(configs["alice"]))
        for mutate in (lambda p: p.update(alb_arn="other"),
                       lambda p: p["terraform"]["learners"].update(alice="i-other"),
                       lambda p: p["browser_routes"]["alice"].update({"8888": "https://other.example.com"})):
            broken = copy.deepcopy(self.plan)
            mutate(broken)
            with self.assertRaises(ValueError):
                deploy.verify_assignments(self.controller, broken, self.identity, self.subjects)
        with self.assertRaisesRegex(ValueError, "subject or account"):
            deploy.verify_assignments(self.controller, self.plan, self.identity, {"alice": ["wrong"], "bob": ["subject-bob"]})
        self.controller.binding["tool_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "gateway source changed"):
            deploy.verify_assignments(self.controller, self.plan, self.identity, self.subjects)

    def test_payload_is_deterministic_and_never_contains_another_subject(self):
        _, configs = deploy.verify_assignments(self.controller, self.plan, self.identity, self.subjects)
        commands, digest = deploy.payload(self.plan, configs["alice"], "alice")
        self.assertEqual(digest, deploy.payload(self.plan, configs["alice"], "alice")[1])
        self.assertLess(len(json.dumps(commands)), 24000)
        self.assertNotIn("subject-bob", json.dumps(commands))

    def test_success_is_reused_and_unknown_dispatch_is_not_replayed(self):
        first = deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice")
        second = deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice")
        self.assertFalse(first["gateways"]["alice"]["reused"])
        self.assertTrue(second["gateways"]["alice"]["reused"])
        self.assertEqual(self.controller.cloud.sent, 1)
        row = self.controller.record["gateway_installations"][self.instances["alice"]]
        row["status"] = "dispatching"
        row.pop("command_id")
        with self.assertRaisesRegex(ValueError, "dispatch outcome unknown"):
            deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice")
        self.assertEqual(self.controller.cloud.sent, 1)

    def test_success_without_expected_marker_is_failed_and_requires_explicit_retry(self):
        self.controller.cloud.output = "unexpected output"
        with self.assertRaisesRegex(ValueError, "invalid result"):
            deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice")
        row = self.controller.record["gateway_installations"][self.instances["alice"]]
        self.assertEqual(row["status"], "Failed")
        with self.assertRaisesRegex(ValueError, "explicit bounded retry"):
            deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice")
        self.controller.cloud.output = "gateway-ready\n"
        result = deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice", retry_failed=True)
        self.assertEqual(result["gateways"]["alice"]["status"], "Success")
        self.assertEqual(self.controller.cloud.sent, 2)

    def test_lost_dispatch_response_is_persisted_and_never_resent(self):
        with mock.patch.object(self.controller.cloud, "send_command", side_effect=TimeoutError("unknown outcome")) as send:
            with self.assertRaises(TimeoutError):
                deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice")
            row = self.controller.record["gateway_installations"][self.instances["alice"]]
            self.assertEqual(row["status"], "dispatching")
            self.assertGreater(self.controller.saves, 0)
            with self.assertRaisesRegex(ValueError, "dispatch outcome unknown"):
                deploy.install(self.controller, self.plan, self.identity, self.subjects, learner="alice", retry_failed=True)
            self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
