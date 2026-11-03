"""Read-only verification of an existing shared workshop network.

This is a topology check, not a deployment, identity, pricing or readiness gate.
"""

import re

import workshop_config as wc


def require(condition, message):
    if not condition:
        raise wc.ConfigError("shared network: " + message)


def verify(config, access, aws, offered_zones):
    require(isinstance(access, dict) and access.get("schema_version") == 1 and
            access.get("account_id") == config["aws_account_id"] and
            access.get("region") == config["region"] and access.get("domain") == config["domain"],
            "access output must match the activity account, Region and domain")
    access_id = access.get("access_id")
    require(isinstance(access_id, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,31}", access_id),
            "invalid shared access ID")
    for key, prefix in (("vpc_id", "vpc"), ("gateway_security_group_id", "sg"),
                        ("alb_security_group_id", "sg")):
        require(isinstance(access.get(key), str) and re.fullmatch(prefix + r"-[0-9a-f]{17}", access[key]),
                "invalid " + key)
    require(access["gateway_security_group_id"] != access["alb_security_group_id"] and
            access.get("gateway_port") == 8080, "invalid gateway security group or port")
    alb = access.get("alb_arn")
    listener = access.get("https_listener_arn")
    arn_prefix = f'arn:aws:elasticloadbalancing:{config["region"]}:{config["aws_account_id"]}:'
    require(isinstance(alb, str) and alb.startswith(arn_prefix + "loadbalancer/app/") and
            isinstance(listener, str) and listener.startswith(arn_prefix + "listener/app/"),
            "ALB or listener ARN differs from the selected account and Region")
    subnets = access.get("subnet_ids")
    require(isinstance(subnets, dict) and len(subnets) >= 2 and
            all(isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"subnet-[0-9a-f]{17}", v)
                for k, v in subnets.items()) and len(set(subnets.values())) == len(subnets), "invalid shared subnets")
    expected_tags = {"Project": "doris-workshop", "Owner": config["owner_id"],
                     "Scope": "shared-access", "Access": access_id}

    def owned(row):
        tags = {entry["Key"]: entry["Value"] for entry in row.get("Tags", [])}
        return all(tags.get(k) == v for k, v in expected_tags.items())

    vpcs = aws.call("ec2", "describe_vpcs", VpcIds=[access["vpc_id"]])["Vpcs"]
    require(len(vpcs) == 1 and vpcs[0].get("OwnerId") == config["aws_account_id"] and
            vpcs[0].get("State") == "available" and owned(vpcs[0]), "VPC ownership or state mismatch")
    lbs = aws.call("elbv2", "describe_load_balancers", LoadBalancerArns=[alb])["LoadBalancers"]
    require(len(lbs) == 1 and lbs[0].get("VpcId") == access["vpc_id"] and
            lbs[0].get("Type") == "application" and lbs[0].get("Scheme") == "internet-facing" and
            lbs[0].get("State", {}).get("Code") == "active" and
            lbs[0].get("SecurityGroups") == [access["alb_security_group_id"]] and
            {r["ZoneName"]: r["SubnetId"] for r in lbs[0].get("AvailabilityZones", [])} == subnets,
            "ALB topology or state differs from the access output")
    tags = aws.call("elbv2", "describe_tags", ResourceArns=[alb])["TagDescriptions"]
    require(len(tags) == 1 and tags[0].get("ResourceArn") == alb and owned(tags[0]), "ALB ownership tags mismatch")
    listeners = aws.call("elbv2", "describe_listeners", ListenerArns=[listener])["Listeners"]
    require(len(listeners) == 1 and listeners[0].get("LoadBalancerArn") == alb and
            listeners[0].get("Protocol") == "HTTPS" and listeners[0].get("Port") == 443,
            "HTTPS listener does not belong to the selected ALB")
    groups = aws.call("ec2", "describe_security_groups", GroupIds=[access["gateway_security_group_id"],
                      access["alb_security_group_id"]])["SecurityGroups"]
    group_map = {group["GroupId"]: group for group in groups}
    require(set(group_map) == {access["gateway_security_group_id"], access["alb_security_group_id"]} and
            all(g.get("VpcId") == access["vpc_id"] and g.get("OwnerId") == config["aws_account_id"] and owned(g)
                for g in groups), "security group ownership or VPC mismatch")
    ingress = group_map[access["gateway_security_group_id"]].get("IpPermissions", [])
    require(len(ingress) == 1 and ingress[0].get("IpProtocol") == "tcp" and
            ingress[0].get("FromPort") == ingress[0].get("ToPort") == 8080 and
            not any(ingress[0].get(key) for key in ("IpRanges", "Ipv6Ranges", "PrefixListIds")) and
            len(ingress[0].get("UserIdGroupPairs", [])) == 1 and
            ingress[0]["UserIdGroupPairs"][0].get("GroupId") == access["alb_security_group_id"] and
            ingress[0]["UserIdGroupPairs"][0].get("UserId") in (None, config["aws_account_id"]),
            "gateway ingress must allow only this ALB security group on TCP 8080")
    rows = aws.call("ec2", "describe_subnets", SubnetIds=sorted(subnets.values()))["Subnets"]
    require(len(rows) == len(subnets) and all(row.get("OwnerId") == config["aws_account_id"] and
            row.get("VpcId") == access["vpc_id"] and row.get("State") == "available" and owned(row) and
            subnets.get(row.get("AvailabilityZone")) == row.get("SubnetId") for row in rows),
            "subnet ownership, zone or VPC mismatch")
    tables = aws.items("ec2", "describe_route_tables", "RouteTables",
                       Filters=[{"Name": "vpc-id", "Values": [access["vpc_id"]]}])
    gateways = aws.items("ec2", "describe_internet_gateways", "InternetGateways",
                         Filters=[{"Name": "attachment.vpc-id", "Values": [access["vpc_id"]]}])
    gateway_ids = {g["InternetGatewayId"] for g in gateways if g.get("OwnerId") == config["aws_account_id"] and
                   owned(g) and any(a.get("VpcId") == access["vpc_id"] and a.get("State") == "available"
                                    for a in g.get("Attachments", []))}
    require(gateway_ids, "no owned Internet gateway is attached to the shared VPC")

    def has_egress(subnet_id):
        selected = [t for t in tables if any(a.get("SubnetId") == subnet_id for a in t.get("Associations", []))]
        if not selected:
            selected = [t for t in tables if any(a.get("Main") is True for a in t.get("Associations", []))]
        return len(selected) == 1 and owned(selected[0]) and any(
            route.get("DestinationCidrBlock") == "0.0.0.0/0" and route.get("GatewayId") in gateway_ids and
            route.get("State") == "active" for route in selected[0].get("Routes", []))

    candidates = sorted(row["AvailabilityZone"] for row in rows if row["AvailabilityZone"] in offered_zones and
                        row.get("AvailableIpAddressCount", 0) >= len(config["learner_ids"]) and
                        has_egress(row["SubnetId"]))
    require(candidates, "no shared subnet has an offered type, enough free IPs and an owned Internet route")
    return {"access_id": access_id, "vpc_id": access["vpc_id"], "availability_zones": candidates,
            "subnet_ids": {zone: subnets[zone] for zone in candidates},
            "gateway_security_group_id": access["gateway_security_group_id"]}
