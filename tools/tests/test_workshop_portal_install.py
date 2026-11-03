"""Shared portal bootstrap scope, deterministic payload and dispatch safety."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_config as wc
import workshop_portal_install as install


class Cloud:
    def __init__(self):
        self.sent = 0
        self.output = "portal-ready\n"
    def client(self, service): return self
    def dispatch_client(self): return self
    def describe_instance_information(self, **kwargs): return {"InstanceInformationList": [{"PingStatus": "Online"}]}
    def send_command(self, **kwargs):
        self.sent += 1
        return {"Command": {"CommandId": "command-" + str(self.sent)}}
    def get_command_invocation(self, **kwargs): return {"Status": "Success", "StandardOutputContent": self.output}
    class exceptions:
        class InvocationDoesNotExist(Exception): pass


class PortalInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = {"schema_version": 1, "account_id": "123456789012", "region": "us-east-1", "domain": "example.com",
            "alb_arn": "alb", "access_id": "access", "vpc_id": "vpc", "portal_security_group_id": "sg-portal",
            "alb_security_group_id": "sg-alb", "portal": {"instance_id": "i-portal", "target_group_arn": "tg"}}
        self.config = {"aws_account_id": "123456789012", "region": "us-east-1", "domain": "example.com", "owner_id": "owner", "aws_profile": "operator"}
        self.cloud = Cloud()

    def test_fixed_payload_is_deterministic_bounded_and_empty(self):
        commands, digest, raw = install.payload(self.shared)
        self.assertEqual(digest, install.payload(self.shared)[1])
        self.assertEqual(json.loads(raw)["activities"], {})
        self.assertTrue(any("flock -n 9" in c for c in commands))
        self.assertTrue(any("test ! -e /etc/doris-workshop-portal/portal.json" in c for c in commands))
        self.assertLess(len(json.dumps(commands)), 24000)

    def test_success_receipt_is_reused_and_unknown_dispatch_not_resent(self):
        with mock.patch.object(install, "verify", return_value="i-portal"):
            args = (self.config, self.shared, self.root, self.root / "install", self.cloud)
            self.assertFalse(install.install(*args)["reused_receipt"])
            self.assertTrue(install.install(*args)["reused_receipt"])
            self.assertEqual(self.cloud.sent, 1)
            record = json.loads((self.root / "install/dispatch.json").read_text())
            record["status"] = "dispatching"
            record.pop("command_id")
            (self.root / "install/dispatch.json").write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "dispatch outcome unknown"): install.install(*args)
            self.assertEqual(self.cloud.sent, 1)

    def test_lost_dispatch_and_invalid_success_are_not_replayed(self):
        with mock.patch.object(install, "verify", return_value="i-portal"), \
                mock.patch.object(self.cloud, "send_command", side_effect=TimeoutError("unknown")) as send:
            args = (self.config, self.shared, self.root, self.root / "lost", self.cloud)
            with self.assertRaises(TimeoutError): install.install(*args)
            with self.assertRaisesRegex(ValueError, "dispatch outcome unknown"): install.install(*args)
            self.assertEqual(send.call_count, 1)
        with mock.patch.object(install, "verify", return_value="i-portal"):
            self.cloud.output = "wrong marker"
            args = (self.config, self.shared, self.root, self.root / "bad", self.cloud)
            with self.assertRaisesRegex(ValueError, "invalid result"): install.install(*args)
            with self.assertRaisesRegex(ValueError, "requires reconciliation"): install.install(*args)

    def test_scope_rejects_wrong_entry_before_terraform(self):
        bad = {**self.shared, "account_id": "000000000000"}
        with mock.patch.object(install.lifecycle, "Terraform") as tf:
            with self.assertRaisesRegex(ValueError, "shared entry differs"): install.verify(self.config, bad, self.root, self.cloud)
        tf.assert_not_called()

    def test_owned_state_and_live_host_are_bound_to_exact_portal(self):
        wc.write_manifest(self.root / "backend.tfbackend.json", {"profile": "operator", "region": "us-east-1",
            "allowed_account_ids": ["123456789012"], "encrypt": True, "use_lockfile": True,
            "bucket": "state", "key": "aws-workshop/shared-portal/us-east-1/access/terraform.tfstate"})
        variables = {"aws_profile": "operator", "expected_account_id": "123456789012", "region": "us-east-1",
            "owner_id": "owner", "access_id": "access", "vpc_id": "vpc", "subnet_id": "subnet", "ami_id": "ami",
            "portal_security_group_id": "sg-portal", "alb_security_group_id": "sg-alb"}
        wc.write_manifest(self.root / "inputs.tfvars.json", variables)
        tags = {"Project": "doris-workshop", "Scope": "shared-portal", "Owner": "owner", "Access": "access"}
        def resource(address, values): return {"address": address, "mode": "managed", "values": values}
        resources = [resource("aws_instance.portal", {"id": "i-portal", "ami": "ami", "subnet_id": "subnet", "vpc_security_group_ids": ["sg-portal"], "tags_all": tags}),
            resource("aws_iam_role.portal", {"name": "role", "tags_all": tags}),
            resource("aws_iam_instance_profile.portal", {"role": "role", "tags_all": tags}),
            resource("aws_iam_role_policy_attachment.ssm", {"role": "role", "policy_arn": "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"})]
        data = {"values": {"root_module": {"resources": resources}}}
        class Runner:
            def __init__(self, *args): pass
            def execute(self, args, env, *, capture=False): return json.dumps(data) if capture else None
        class ReadCloud:
            def __init__(self): self.target = "i-portal"
            def call(self, service, op, **kwargs):
                if op == "describe_instances": return {"Reservations": [{"Instances": [{"InstanceId": "i-portal", "ImageId": "ami", "State": {"Name": "running"},
                    "VpcId": "vpc", "SubnetId": "subnet", "SecurityGroups": [{"GroupId": "sg-portal"}],
                    "MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 1},
                    "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]}]}]}
                if op == "describe_load_balancers": return {"LoadBalancers": [{"VpcId": "vpc", "State": {"Code": "active"}}]}
                if op == "describe_tags": return {"TagDescriptions": [{"ResourceArn": "alb", "Tags": [{"Key": k, "Value": v} for k, v in {**tags, "Scope": "shared-access"}.items()]}]}
                if op == "describe_target_groups": return {"TargetGroups": [{"VpcId": "vpc", "LoadBalancerArns": ["alb"]}]}
                if op == "describe_target_health": return {"TargetHealthDescriptions": [{"Target": {"Id": self.target, "Port": 8080}}]}
                raise AssertionError(op)
        cloud = ReadCloud()
        with mock.patch.object(install.lifecycle, "Terraform", Runner):
            self.assertEqual(install.verify(self.config, self.shared, self.root, cloud), "i-portal")
            cloud.target = "foreign"
            with self.assertRaisesRegex(ValueError, "target attachment differs"):
                install.verify(self.config, self.shared, self.root, cloud)
            cloud.target = "i-portal"
            resources[0]["values"]["tags_all"] = {**tags, "Owner": "other"}
            with self.assertRaisesRegex(ValueError, "ownership differs"):
                install.verify(self.config, self.shared, self.root, cloud)


if __name__ == "__main__":
    unittest.main()
