"""Shared topology checks use only explicit, read-only AWS responses."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_shared_preflight as shared


class FakeAws:
    def __init__(self, access):
        tags = [{"Key": k, "Value": v} for k, v in {"Project": "doris-workshop", "Owner": "owner",
                "Scope": "shared-access", "Access": "training"}.items()]
        common = {"OwnerId": "123456789012", "VpcId": access["vpc_id"], "Tags": tags}
        self.results = {
            "describe_vpcs": {"Vpcs": [{**common, "State": "available"}]},
            "describe_load_balancers": {"LoadBalancers": [{"VpcId": access["vpc_id"], "Type": "application",
                "Scheme": "internet-facing", "State": {"Code": "active"},
                "SecurityGroups": [access["alb_security_group_id"]],
                "AvailabilityZones": [{"ZoneName": z, "SubnetId": s} for z, s in access["subnet_ids"].items()]}]},
            "describe_tags": {"TagDescriptions": [{"ResourceArn": access["alb_arn"], "Tags": tags}]},
            "describe_listeners": {"Listeners": [{"LoadBalancerArn": access["alb_arn"], "Protocol": "HTTPS", "Port": 443}]},
            "describe_security_groups": {"SecurityGroups": [
                {**common, "GroupId": access["alb_security_group_id"]},
                {**common, "GroupId": access["gateway_security_group_id"], "IpPermissions": [{"IpProtocol": "tcp",
                 "FromPort": 8080, "ToPort": 8080, "UserIdGroupPairs": [{"GroupId": access["alb_security_group_id"],
                 "UserId": "123456789012"}]}]}]},
            "describe_subnets": {"Subnets": [{**common, "SubnetId": s, "AvailabilityZone": z,
                "State": "available", "AvailableIpAddressCount": 20} for z, s in access["subnet_ids"].items()]},
            "describe_internet_gateways": {"InternetGateways": [{"InternetGatewayId": "igw-owned", "Tags": tags,
                "OwnerId": "123456789012", "Attachments": [{"VpcId": access["vpc_id"], "State": "available"}]}]},
            "describe_route_tables": {"RouteTables": [{"Tags": tags, "Associations": [{"Main": True}],
                "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-owned", "State": "active"}]}]},
        }

    def call(self, service, operation, **kwargs):
        return copy.deepcopy(self.results[operation])

    def items(self, service, operation, key, **kwargs):
        return self.call(service, operation, **kwargs)[key]


class SharedPreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = {"aws_account_id": "123456789012", "region": "us-east-1", "domain": "example.com",
                       "owner_id": "owner", "learner_ids": ["alice", "bob"]}
        arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
        self.access = {"schema_version": 1, "account_id": "123456789012", "region": "us-east-1",
            "domain": "example.com", "access_id": "training", "vpc_id": "vpc-" + "1" * 17,
            "alb_security_group_id": "sg-" + "2" * 17, "gateway_security_group_id": "sg-" + "3" * 17,
            "gateway_port": 8080, "alb_arn": arn + "loadbalancer/app/test/123",
            "https_listener_arn": arn + "listener/app/test/123/456",
            "subnet_ids": {"us-east-1a": "subnet-" + "4" * 17, "us-east-1b": "subnet-" + "5" * 17}}
        self.aws = FakeAws(self.access)

    def verify(self):
        return shared.verify(self.config, self.access, self.aws, ["us-east-1a", "us-east-1b"])

    def test_matching_topology_selects_both_zones(self):
        self.assertEqual(self.verify()["availability_zones"], ["us-east-1a", "us-east-1b"])

    def test_foreign_or_public_gateway_is_rejected(self):
        for field, value in (("IpRanges", [{"CidrIp": "0.0.0.0/0"}]),
                             ("UserIdGroupPairs", [{"GroupId": "sg-" + "9" * 17}])):
            with self.subTest(field=field):
                aws = FakeAws(self.access)
                aws.results["describe_security_groups"]["SecurityGroups"][1]["IpPermissions"][0][field] = value
                with self.assertRaisesRegex(ValueError, "gateway ingress"):
                    shared.verify(self.config, self.access, aws, ["us-east-1a"])

    def test_wrong_ownership_listener_and_subnet_are_rejected(self):
        for operation, mutate in (
            ("describe_vpcs", lambda r: r["Vpcs"][0].update(OwnerId="000000000000")),
            ("describe_tags", lambda r: r["TagDescriptions"][0].update(Tags=[])),
            ("describe_listeners", lambda r: r["Listeners"][0].update(Port=80)),
            ("describe_subnets", lambda r: r["Subnets"][0].update(VpcId="vpc-other"))):
            with self.subTest(operation=operation):
                aws = FakeAws(self.access)
                mutate(aws.results[operation])
                with self.assertRaises(ValueError):
                    shared.verify(self.config, self.access, aws, ["us-east-1a"])

    def test_missing_route_and_insufficient_addresses_are_rejected(self):
        self.aws.results["describe_route_tables"]["RouteTables"][0]["Routes"] = []
        with self.assertRaisesRegex(ValueError, "Internet route"):
            self.verify()
        self.aws = FakeAws(self.access)
        for row in self.aws.results["describe_subnets"]["Subnets"]:
            row["AvailableIpAddressCount"] = 1
        with self.assertRaisesRegex(ValueError, "free IPs"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
