"""Review gates for TTL control-plane policies and retirement evidence."""
import re

import workshop_config as wc
import workshop_ttl as ttl

ROLE_NAMES = {"scheduler", "workflow", "execution", "runner"}
GLOBAL_READS = {"sts:GetCallerIdentity", "ecr:GetAuthorizationToken", "s3:ListAllMyBuckets", "iam:ListRoles", "iam:ListInstanceProfiles",
    "s3:GetBucketLocation", "s3:GetBucketTagging", "iam:GetRole", "iam:GetInstanceProfile", "ssm:ListTagsForResource",
    "ssm:ListDocuments", "cognito-idp:ListUserPools", "cognito-idp:ListTagsForResource", "cognito-idp:DescribeUserPoolDomain", "ecs:DescribeTasks",
    *["ec2:" + name for name in ("DescribeInstances", "DescribeInstanceTypes", "DescribeVpcs", "DescribeSubnets", "DescribeVolumes",
        "DescribeNetworkInterfaces", "DescribeRouteTables", "DescribeSecurityGroups", "DescribeSecurityGroupRules", "DescribeInstanceCreditSpecifications", "DescribeInternetGateways", "DescribeNatGateways",
        "DescribeAddresses", "DescribeImages", "DescribeSnapshots", "DescribeKeyPairs", "DescribeTags")],
    *["elasticloadbalancing:" + name for name in ("DescribeLoadBalancers", "DescribeTargetGroups", "DescribeTargetGroupAttributes", "DescribeListeners", "DescribeRules", "DescribeTags", "DescribeTargetHealth")],
    "tag:GetResources"}
# This is a verifier for reviewed Autopilot output, not a policy generator.
MUTATIONS = {
    "scheduler": {"states:StartExecution", "sqs:SendMessage"},
    "workflow": {"ecs:RunTask", "ecs:StopTask", "iam:PassRole", "events:PutRule", "events:PutTargets"},
    "execution": {"ecr:BatchCheckLayerAvailability"},
    "runner": {"ec2:TerminateInstances", "ec2:DeleteVpc", "ec2:DeleteSubnet", "ec2:DeleteRouteTable", "ec2:DeleteSecurityGroup",
        "ec2:DeleteInternetGateway", "ec2:DetachInternetGateway", "ec2:DisassociateRouteTable", "ec2:DeleteRoute", "ec2:DeleteVolume",
        "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:DeleteBucket", "iam:DeleteRole", "iam:DeleteRolePolicy",
        "s3:DeleteBucketPolicy", "s3:PutBucketPublicAccessBlock", "s3:PutEncryptionConfiguration",
        "iam:DeleteInstanceProfile", "iam:RemoveRoleFromInstanceProfile", "ssm:DeleteDocument", "elasticloadbalancing:DeleteRule",
        "elasticloadbalancing:DeleteTargetGroup", "elasticloadbalancing:DeregisterTargets", "cognito-idp:DeleteUserPool",
        "cognito-idp:DeleteUserPoolClient", "cognito-idp:DeleteUserPoolDomain", "route53:ChangeResourceRecordSets"},
}


def validate_policy(role, document, approved_arns, *, account_id, region, state_bucket, event_id=None, approved_dns_names=()):
    task_scope = f"arn:aws:ecs:{region}:{account_id}:task/doris-ttl-{event_id}/*" if event_id else None
    cluster_arn = f"arn:aws:ecs:{region}:{account_id}:cluster/doris-ttl-{event_id}" if event_id else None
    ttl.require(role in ROLE_NAMES and document.get("Version") == "2012-10-17" and isinstance(document.get("Statement"), list), "invalid reviewed role policy")
    ttl.require(approved_arns and all(isinstance(arn, str) and arn.startswith("arn:aws:") and
        ("*" not in arn or role == "workflow" and arn == task_scope or
         role == "runner" and arn == "arn:aws:route53:::change/*") and "?" not in arn for arn in approved_arns),
        "approve exact resource ARNs; wildcard mutation scopes are forbidden")
    for arn in approved_arns:
        parts = arn.split(":", 5)
        ttl.require(len(parts) == 6 and parts[3] in {"", region} and parts[4] in {"", account_id}, "policy resource belongs to another target")
    for row in document["Statement"]:
        ttl.require(set(row) <= {"Sid", "Effect", "Action", "Resource", "Condition"} and row["Effect"] in {"Allow", "Deny"}, "unsupported IAM policy statement")
        actions = [row["Action"]] if isinstance(row["Action"], str) else row["Action"]
        resources = [row["Resource"]] if isinstance(row["Resource"], str) else row["Resource"]
        ttl.require(actions and resources and all(re.fullmatch(r"[a-z0-9-]+:[A-Za-z0-9]+", action) for action in actions), "enumerate IAM actions without wildcards")
        for action in actions:
            readonly = bool(re.fullmatch(r"[a-z0-9-]+:(?:Get|Describe|List|BatchGet)[A-Za-z0-9]*", action))
            ttl.require(readonly or action in MUTATIONS[role], "role contains an unsupported mutation or escalation action")
            ttl.require(all(resource in approved_arns or action in GLOBAL_READS and resource == "*" for resource in resources), "policy access escapes reviewed exact resources")
            if action in {"s3:DeleteBucket", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:DeleteBucketPolicy", "s3:PutBucketPublicAccessBlock", "s3:PutEncryptionConfiguration"}:
                ttl.require(all(resource != "arn:aws:s3:::" + state_bucket and
                    (not resource.startswith("arn:aws:s3:::" + state_bucket + "/") or resource.endswith(".tflock")) for resource in resources),
                    "runner cannot delete control objects, state or retained evidence")
            if action == "iam:PassRole":
                ttl.require(event_id and all(resource in {f"arn:aws:iam::{account_id}:role/doris-ttl-{event_id}-{name}"
                    for name in ("runner", "execution")} for resource in resources) and
                    row.get("Condition", {}).get("StringEquals", {}).get("iam:PassedToService") == "ecs-tasks.amazonaws.com", "PassRole must be restricted to the exact cleanup task roles")
            if any("*" in resource and resource != "*" for resource in resources):
                ttl.require((action in {"ecs:StopTask", "ecs:DescribeTasks"} and resources == [task_scope] and
                    row.get("Condition", {}).get("ArnEquals", {}).get("ecs:cluster") == cluster_arn) or
                    (role == "runner" and action == "route53:GetChange" and resources == ["arn:aws:route53:::change/*"]),
                    "only this cleanup cluster's dynamic task IDs may use a scoped wildcard")
            if action == "route53:ChangeResourceRecordSets":
                conditions = row.get("Condition", {})
                selected = conditions.get("ForAllValues:StringEquals", {})
                ttl.require(selected.get("route53:ChangeResourceRecordSetsActions") == ["DELETE"] and
                    selected.get("route53:ChangeResourceRecordSetsRecordTypes") == ["A"] and
                    selected.get("route53:ChangeResourceRecordSetsNormalizedRecordNames") and
                    event_id and all(name in approved_dns_names and name.startswith(event_id + "-") and "*" not in name and "?" not in name
                        for name in selected["route53:ChangeResourceRecordSetsNormalizedRecordNames"]) and
                    conditions.get("Null", {}).get("route53:ChangeResourceRecordSetsNormalizedRecordNames") == "false" and
                    conditions.get("Null", {}).get("route53:ChangeResourceRecordSetsActions") == "false" and
                    conditions.get("Null", {}).get("route53:ChangeResourceRecordSetsRecordTypes") == "false", "DNS changes must be limited to explicit DELETE A record names")
    return wc.canonical_hash(document)


def retirement(record, *, schedules_disabled, executions_terminal, tasks_stopped, dlq_reviewed):
    safe = record["state"] in {"cancelled", "complete"} and all((schedules_disabled, executions_terminal, tasks_stopped, dlq_reviewed))
    return {"may_plan_retirement": bool(safe), "mutations_performed": False,
        "order": ["schedule", "workflow", "task-definition", "roles", "queue-and-logs", "cluster"],
        "retain": ["foundation-state-bucket", "versioned-control-and-cleanup-evidence"],
        "blocked": [] if safe else ["reconcile active, unknown, running or unreviewed control resources first"]}


def state_resources(resources, target):
    """Separate ownership model; these resources never enter activity destroy."""
    taggable = {"aws_scheduler_schedule_group", "aws_sfn_state_machine", "aws_ecs_cluster", "aws_ecs_task_definition",
        "aws_iam_role", "aws_sqs_queue", "aws_cloudwatch_log_group", "aws_security_group"}
    children = {"aws_scheduler_schedule", "aws_iam_role_policy"}
    roots, result = set(), []
    for row in resources:
        if row.get("mode", "managed") != "managed":
            continue
        ttl.require(row["type"] in taggable | children and not row.get("module_address"), "unexpected TTL control resource")
        if row["type"] in taggable:
            tags = row["values"].get("tags_all") or row["values"].get("tags") or {}
            ttl.require(all(tags.get(key) == value for key, value in {"Project": "doris-workshop", "Scope": "cleanup-control",
                "Event": target["event_id"], "Owner": target["owner_id"]}.items()), "TTL resource ownership differs")
            roots.add((row["type"], row["values"].get("name", row["values"]["id"])))
        result.append({"address": row["address"], "type": row["type"], "id": row["values"]["id"], "retained_from_activity_cleanup": True})
    for row in resources:
        if row.get("type") == "aws_iam_role_policy":
            ttl.require(("aws_iam_role", row["values"]["role"]) in roots, "TTL policy has no owned control role")
        if row.get("type") == "aws_scheduler_schedule":
            ttl.require(("aws_scheduler_schedule_group", row["values"]["group_name"]) in roots, "schedule has no owned control group")
    return result
