"""Read-only, paginated resource discovery with explicit failed coverage."""
from datetime import datetime, timezone

import workshop_cleanup as cleanup
import workshop_config as wc

# Normalized fields contain identifiers and associations, never configuration,
# user data, IAM policy documents, Cognito secrets/users or object contents.
EC2 = {
    "instances": ("describe_instances", "Reservations", "InstanceId", {"state": ("State", "Name"), "subnet_id": ("SubnetId",), "vpc_id": ("VpcId",)}),
    "volumes": ("describe_volumes", "Volumes", "VolumeId", {}),
    "network_interfaces": ("describe_network_interfaces", "NetworkInterfaces", "NetworkInterfaceId", {"instance_id": ("Attachment", "InstanceId"), "vpc_id": ("VpcId",), "subnet_id": ("SubnetId",)}),
    "vpcs": ("describe_vpcs", "Vpcs", "VpcId", {}),
    "subnets": ("describe_subnets", "Subnets", "SubnetId", {"vpc_id": ("VpcId",)}),
    "route_tables": ("describe_route_tables", "RouteTables", "RouteTableId", {"vpc_id": ("VpcId",)}),
    "security_groups": ("describe_security_groups", "SecurityGroups", "GroupId", {"vpc_id": ("VpcId",)}),
    "internet_gateways": ("describe_internet_gateways", "InternetGateways", "InternetGatewayId", {}),
    "nat_gateways": ("describe_nat_gateways", "NatGateways", "NatGatewayId", {"state": ("State",), "vpc_id": ("VpcId",)}),
    "addresses": ("describe_addresses", "Addresses", "AllocationId", {"instance_id": ("InstanceId",), "network_interface_id": ("NetworkInterfaceId",)}),
    "images": ("describe_images", "Images", "ImageId", {"state": ("State",)}),
    "snapshots": ("describe_snapshots", "Snapshots", "SnapshotId", {"volume_id": ("VolumeId",)}),
    "key_pairs": ("describe_key_pairs", "KeyPairs", "KeyPairId", {}),
}


def tags(value):
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k in {"Project", "Owner", "Event", "Scope", "ImageBuild", "ImageStatus", "ReleaseRef"}}
    return tags({row["Key"]: row["Value"] for row in value or []})


def field(value, path):
    for part in path:
        value = value.get(part, {})
    return value if isinstance(value, str) else None


class Scanner:
    def __init__(self, aws, manifest, *, max_pages=200, max_calls=5000, states=None):
        cleanup.validate_manifest(manifest)
        self.aws, self.manifest = aws, manifest
        self.max_pages, self.max_calls, self.calls = max_pages, max_calls, 0
        self.known = {cleanup.key(row) for row in manifest["known"]}
        self.owned_references = {row["id"] for row in manifest["known"]}
        self.references = {row["id"] for row in manifest["known"] + manifest["retained"]}
        self.snapshot = {"schema_version": 1, "target": manifest["target"], "checks": {},
            "states": states or {}, "manifest_sha256": wc.canonical_hash(manifest),
            "published_snapshots": [], "collected_at": wc.utc_text(datetime.now(timezone.utc))}

    def call(self, service, operation, **kwargs):
        cleanup.require(operation.startswith(("get_", "list_", "describe_")), "scanner mutations are forbidden")
        self.calls += 1
        cleanup.require(self.calls <= self.max_calls, "scan API-call bound reached; coverage is incomplete")
        return getattr(self.aws.client(service), operation)(**kwargs)

    def items(self, service, operation, result, **kwargs):
        cleanup.require(self.calls < self.max_calls, "scan API-call bound reached; coverage is incomplete")
        client = self.aws.client(service)
        cleanup.require(operation.startswith(("get_", "list_", "describe_")), "scanner mutations are forbidden")
        if client.can_paginate(operation):
            for number, page in enumerate(client.get_paginator(operation).paginate(**kwargs), 1):
                self.calls += 1
                cleanup.require(number <= self.max_pages and self.calls <= self.max_calls, "scan pagination bound reached; coverage is incomplete")
                yield from page.get(result, [])
                cleanup.require(number < self.max_pages and self.calls < self.max_calls, "scan bound reached; coverage is incomplete")
        else:
            response = self.call(service, operation, **kwargs)
            cleanup.require(not any(response.get(k) for k in ("NextToken", "NextMarker", "Marker", "PaginationToken", "IsTruncated")),
                "SDK lacks pagination support for this response; coverage is incomplete")
            yield from response.get(result, [])

    def add(self, kind, identity, *, resource_tags=None, fields=None, related=False):
        cleanup.ref(kind, identity)
        resource_tags, fields = tags(resource_tags or {}), fields or {}
        related = related or any(value in self.owned_references for value in fields.values() if isinstance(value, str))
        # Keep relevant project resources and exact/related references. Never
        # export a regional account's unrelated inventory or arbitrary metadata.
        if resource_tags.get("Project") == "doris-workshop" or (kind, identity) in self.known or identity in self.references or related:
            row = {"id": identity, "tags": resource_tags, "fields": fields, "related": bool(related)}
            self.snapshot["checks"][kind]["resources"].append(row)

    def run(self, kind, operation):
        self.snapshot["checks"][kind] = {"status": "failed", "resources": []}
        try:
            operation()
            self.snapshot["checks"][kind]["status"] = "complete"
        except Exception:
            # A failed family is a failed proof, even after some pages succeeded.
            # Raw AWS messages/parameters must never be copied into evidence.
            self.snapshot["checks"][kind]["error"] = "query-or-pagination-failed"

    def ec2(self, kind):
        operation, result, identity, paths = EC2[kind]
        options = {"Owners": ["self"]} if kind == "images" else {"OwnerIds": ["self"]} if kind == "snapshots" else {}
        for container in self.items("ec2", operation, result, **options):
            for value in container.get("Instances", []) if kind == "instances" else [container]:
                if kind == "instances" and value.get("State", {}).get("Name") == "terminated" or kind == "nat_gateways" and value.get("State") == "deleted":
                    continue
                selected = {name: field(value, path) for name, path in paths.items() if field(value, path) is not None}
                if kind == "volumes":
                    selected["instance_id"] = next((row["InstanceId"] for row in value.get("Attachments", []) if row["InstanceId"] in self.owned_references), "")
                if kind == "images" and (tags(value.get("Tags")).get("ImageStatus") == "validated" or
                        ("images", value["ImageId"]) in {cleanup.key(row) for row in self.manifest["retained"]}):
                    self.snapshot["published_snapshots"].extend(row["Ebs"]["SnapshotId"] for row in value.get("BlockDeviceMappings", []) if row.get("Ebs", {}).get("SnapshotId"))
                self.add(kind, value[identity], resource_tags=value.get("Tags"), fields=selected)

    def iam(self, kind):
        role = kind == "roles"
        operation, result, name, get = ("list_roles", "Roles", "RoleName", "get_role") if role else (
            "list_instance_profiles", "InstanceProfiles", "InstanceProfileName", "get_instance_profile")
        for value in self.items("iam", operation, result):
            try:
                detailed = self.call("iam", get, **{name: value[name]})["Role" if role else "InstanceProfile"]
            except self.aws.client("iam").exceptions.NoSuchEntityException:
                continue
            self.add(kind, value[name], resource_tags=detailed.get("Tags"))

    def buckets(self):
        t = self.manifest["target"]
        client = self.aws.client("s3")
        incomplete = False
        for bucket in self.items("s3", "list_buckets", "Buckets", BucketRegion=t["region"]):
            name = bucket["Name"]
            try:
                cleanup.require(bucket.get("BucketRegion") == t["region"], "bucket region missing or mismatched")
                try:
                    resource_tags = self.call("s3", "get_bucket_tagging", Bucket=name)["TagSet"]
                except client.exceptions.ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") != "NoSuchTagSet":
                        raise
                    resource_tags = []
            except client.exceptions.NoSuchBucket:
                continue
            except client.exceptions.ClientError:
                # One inaccessible account bucket must not hide later buckets.
                # Its unknown ownership still makes the whole catalog incomplete.
                incomplete = True
                continue
            self.add("buckets", name, resource_tags=resource_tags)
        cleanup.require(not incomplete, "one or more buckets could not be inspected")

    def ssm(self):
        for value in self.items("ssm", "list_documents", "DocumentIdentifiers", Filters=[{"Key": "Owner", "Values": ["Self"]}]):
            name = value["Name"]
            result = self.call("ssm", "list_tags_for_resource", ResourceType="Document", ResourceId=name)
            self.add("ssm_documents", name, resource_tags=result.get("TagList"))

    def tagged(self):
        for value in self.items("resourcegroupstaggingapi", "get_resources", "ResourceTagMappingList",
                TagFilters=[{"Key": "Project", "Values": ["doris-workshop"]}]):
            self.add("tagged_resources", value["ResourceARN"], resource_tags=value.get("Tags"))

    def elb_catalog(self, kind):
        operation, result, identity = ("describe_load_balancers", "LoadBalancers", "LoadBalancerArn") if kind == "load_balancers" else (
            "describe_target_groups", "TargetGroups", "TargetGroupArn")
        for value in self.items("elbv2", operation, result):
            arn = value[identity]
            tagged = self.call("elbv2", "describe_tags", ResourceArns=[arn])["TagDescriptions"]
            self.add(kind, arn, resource_tags=tagged[0].get("Tags"), fields={"vpc_id": value.get("VpcId", "")})

    def rules(self):
        shared = self.manifest["shared"]
        cleanup.require(shared is not None, "shared ALB reference is required to scan rules")
        for listener in self.items("elbv2", "describe_listeners", "Listeners", LoadBalancerArn=shared["alb_arn"]):
            for rule in self.items("elbv2", "describe_rules", "Rules", ListenerArn=listener["ListenerArn"]):
                if rule.get("IsDefault"):
                    continue
                arn = rule["RuleArn"]
                result = self.call("elbv2", "describe_tags", ResourceArns=[arn])["TagDescriptions"]
                target_groups = [action["TargetGroupArn"] for action in rule.get("Actions", []) if action.get("TargetGroupArn")]
                target_groups += [target["TargetGroupArn"] for action in rule.get("Actions", [])
                    for target in action.get("ForwardConfig", {}).get("TargetGroups", [])]
                hosts = [host for condition in rule.get("Conditions", []) if condition["Field"] == "host-header"
                    for host in condition.get("HostHeaderConfig", {}).get("Values", condition.get("Values", []))]
                known_hosts = {row["id"].split("/")[1] for row in self.manifest["known"] if row["kind"] == "dns_records"}
                self.add("listener_rules", arn, resource_tags=result[0].get("Tags"), fields={"alb_arn": shared["alb_arn"]},
                    related=bool(set(target_groups) & self.owned_references or set(hosts) & known_hosts))

    def targets(self):
        # Query every target group: a foreign group can still point at one of
        # our instances, and must be reported rather than silently deregistered.
        for group in self.items("elbv2", "describe_target_groups", "TargetGroups"):
            arn = group["TargetGroupArn"]
            result = self.call("elbv2", "describe_tags", ResourceArns=[arn])["TagDescriptions"]
            for target in self.call("elbv2", "describe_target_health", TargetGroupArn=arn)["TargetHealthDescriptions"]:
                target_id, port = target["Target"]["Id"], target["Target"].get("Port", 8080)
                self.add("target_attachments", arn + "/" + target_id + "/" + str(port), resource_tags=result[0].get("Tags"),
                    fields={"target_group_arn": arn, "instance_id": target_id})

    def dns(self):
        shared = self.manifest["shared"]
        cleanup.require(shared is not None, "DNS reference is required")
        zone = self.call("route53", "get_hosted_zone", Id=shared["hosted_zone_id"])["HostedZone"]
        domain = self.manifest["target"]["domain"]
        cleanup.require(not zone.get("Config", {}).get("PrivateZone") and (domain == zone["Name"].rstrip(".") or
            domain.endswith("." + zone["Name"].rstrip("."))), "unexpected public DNS zone")
        for record in self.items("route53", "list_resource_record_sets", "ResourceRecordSets", HostedZoneId=shared["hosted_zone_id"]):
            name = record["Name"].rstrip(".")
            identity = shared["hosted_zone_id"] + "/" + name + "/" + record["Type"]
            # Exact names from the ownership manifest; a prefix is no proof.
            known_hosts = {row["id"].split("/")[1] for row in self.manifest["known"] if row["kind"] == "dns_records"}
            retained_ids = {row["id"] for row in self.manifest["retained"] if row["kind"] == "dns_records"}
            if name in known_hosts or identity in retained_ids:
                alias = record.get("AliasTarget", {})
                self.add("dns_records", identity, fields={"alias_dns_name": alias.get("DNSName", "").rstrip("."), "alias_zone_id": alias.get("HostedZoneId", "")},
                    related=("dns_records", identity) not in self.known and identity not in retained_ids)

    def pools(self, kind):
        t = self.manifest["target"]
        checked_domains = set()
        for pool in self.items("cognito-idp", "list_user_pools", "UserPools", MaxResults=60):
            identity = pool["Id"]
            arn = f'arn:aws:cognito-idp:{t["region"]}:{t["aws_account_id"]}:userpool/{identity}'
            resource_tags = self.call("cognito-idp", "list_tags_for_resource", ResourceArn=arn)["Tags"]
            if kind == "user_pools":
                self.add(kind, identity, resource_tags=resource_tags)
            elif resource_tags.get("Project") == "doris-workshop" or identity in self.references:
                if kind == "user_pool_clients":
                    for client in self.items("cognito-idp", "list_user_pool_clients", "UserPoolClients", UserPoolId=identity, MaxResults=60):
                        self.add(kind, identity + "/" + client["ClientId"], resource_tags=resource_tags, fields={"pool_id": identity})
                else:
                    value = self.call("cognito-idp", "describe_user_pool", UserPoolId=identity)["UserPool"]
                    for field_name in ("Domain", "CustomDomain"):
                        if value.get(field_name):
                            self.add(kind, value[field_name], resource_tags=resource_tags, fields={"pool_id": identity})
                            checked_domains.add(value[field_name])
        if kind == "user_pool_domains":
            # A domain can still be deleting after its pool vanished from a list.
            # Check the exact historical domain without reading any users.
            for resource in self.manifest["known"]:
                if resource["kind"] == kind and resource["id"] not in checked_domains:
                    value = self.call("cognito-idp", "describe_user_pool_domain", Domain=resource["id"])["DomainDescription"]
                    if value.get("UserPoolId"):
                        self.add(kind, resource["id"], fields={"pool_id": value["UserPoolId"]})

    def collect(self):
        identity = self.call("sts", "get_caller_identity")
        cleanup.require(identity["Account"] == self.manifest["target"]["aws_account_id"] and
            identity["Arn"].startswith("arn:aws:"), "scan identity does not match the explicit account")
        for kind in self.manifest["required_kinds"]:
            if kind in EC2:
                self.run(kind, lambda kind=kind: self.ec2(kind))
            elif kind in {"roles", "instance_profiles"}:
                self.run(kind, lambda kind=kind: self.iam(kind))
            elif kind in {"load_balancers", "target_groups"}:
                self.run(kind, lambda kind=kind: self.elb_catalog(kind))
            elif kind in {"user_pools", "user_pool_clients", "user_pool_domains"}:
                self.run(kind, lambda kind=kind: self.pools(kind))
            else:
                method = {"buckets": self.buckets, "ssm_documents": self.ssm, "tagged_resources": self.tagged,
                    "listener_rules": self.rules, "target_attachments": self.targets, "dns_records": self.dns}.get(kind)
                if method:
                    self.run(kind, method)
                else:
                    self.snapshot["checks"][kind] = {"status": "unsupported", "resources": []}
        self.snapshot["published_snapshots"] = sorted(set(self.snapshot["published_snapshots"]))
        return self.snapshot
