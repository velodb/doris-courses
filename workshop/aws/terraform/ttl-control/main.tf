terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers { aws = { source = "hashicorp/aws", version = "= 6.71.0" } }
  backend "s3" {}
}
provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = { Project = "doris-workshop", Scope = "cleanup-control", Event = var.event_id, Owner = var.owner_id }
  }
}
locals {
  name                = "doris-ttl-${var.event_id}"
  state_machine_arn   = "arn:aws:states:${var.region}:${var.expected_account_id}:stateMachine:${local.name}"
  group_arn           = "arn:aws:scheduler:${var.region}:${var.expected_account_id}:schedule-group/${local.name}"
  roles               = { scheduler = "scheduler.amazonaws.com", workflow = "states.amazonaws.com", execution = "ecs-tasks.amazonaws.com", runner = "ecs-tasks.amazonaws.com" }
  payload             = { schema_version = 1, event_id = var.event_id, revision = var.revision, binding_sha256 = var.binding_sha256 }
  policy_actions      = jsondecode(file("${path.module}/../../ttl/policy-actions.json"))
  global_read_actions = jsondecode(file("${path.module}/../../ttl/global-read-actions.json"))
  trigger_at          = timeadd(var.expires_at, "${formatdate("ss", var.expires_at) == "00" ? 0 : 60 - tonumber(formatdate("ss", var.expires_at))}s")
}
data "aws_subnet" "control" {
  for_each = toset(var.control_subnet_ids)
  id       = each.value
}
resource "aws_iam_role" "control" {
  for_each = local.roles
  name     = "${local.name}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = each.value },
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.expected_account_id },
        ArnLike      = { "aws:SourceArn" = each.key == "scheduler" ? local.group_arn : each.key == "workflow" ? local.state_machine_arn : "arn:aws:ecs:${var.region}:${var.expected_account_id}:*" }
      }
    }]
  })
}
resource "aws_iam_role_policy" "control" {
  for_each = var.policy_documents
  name     = "reviewed-${each.key}"
  role     = aws_iam_role.control[each.key].id
  policy   = each.value.json
  lifecycle {
    precondition {
      condition = alltrue([for statement in jsondecode(each.value.json).Statement :
        length(setsubtract(toset(keys(statement)), toset(["Sid", "Effect", "Action", "Resource", "Condition"]))) == 0 && contains(["Allow", "Deny"], statement.Effect) &&
        alltrue([for action in flatten([statement.Action]) : can(regex("^[a-z0-9-]+:[A-Za-z0-9]+$", action))]) &&
        alltrue([for action in flatten([statement.Action]) : can(regex("^[a-z0-9-]+:(Get|Describe|List|BatchGet)[A-Za-z0-9]*$", action)) || contains(local.policy_actions[each.key], action)]) &&
        alltrue([for resource in flatten([statement.Resource]) : contains(var.approved_resource_arns[each.key], resource) ||
        (resource == "*" && alltrue([for action in flatten([statement.Action]) : contains(local.global_read_actions, action)]))]) &&
        alltrue([for resource in flatten([statement.Resource]) : !strcontains(resource, "*") ||
          (each.key == "workflow" && resource == "arn:aws:ecs:${var.region}:${var.expected_account_id}:task/${local.name}/*" &&
            alltrue([for action in flatten([statement.Action]) : contains(["ecs:StopTask", "ecs:DescribeTasks"], action)]) &&
          try(statement.Condition.ArnEquals["ecs:cluster"] == "arn:aws:ecs:${var.region}:${var.expected_account_id}:cluster/${local.name}", false)) ||
          (each.key == "runner" && resource == "arn:aws:route53:::change/*" &&
          alltrue([for action in flatten([statement.Action]) : action == "route53:GetChange"])) ||
          (each.key == "runner" && resource == "arn:aws:s3:::${var.state_bucket_name}/aws-workshop-control/${var.expected_account_id}/${var.region}/${var.event_id}/bindings/${var.revision}/*" &&
          alltrue([for action in flatten([statement.Action]) : contains(["s3:GetObject", "s3:GetObjectVersion"], action)])) ||
        (resource == "*" && alltrue([for action in flatten([statement.Action]) : contains(local.global_read_actions, action)]))])
      ])
      error_message = "Policies must enumerate actions; wildcard resources are allowed only for read APIs. Run the policy scope verifier before deployment."
    }
    precondition {
      condition = alltrue([for statement in jsondecode(each.value.json).Statement :
        !anytrue([for action in flatten([statement.Action]) : contains(["s3:DeleteBucket", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:DeleteBucketPolicy", "s3:PutBucketPublicAccessBlock", "s3:PutEncryptionConfiguration"], action)]) ||
        alltrue([for resource in flatten([statement.Resource]) : resource != "arn:aws:s3:::${var.state_bucket_name}" &&
        (!startswith(resource, "arn:aws:s3:::${var.state_bucket_name}/") || endswith(resource, ".tflock"))])
      ])
      error_message = "Never delete foundation state, control objects or retained evidence."
    }
    precondition {
      condition = alltrue([for statement in jsondecode(each.value.json).Statement :
        !contains(flatten([statement.Action]), "iam:PassRole") || try(
          statement.Condition.StringEquals["iam:PassedToService"] == "ecs-tasks.amazonaws.com" &&
          alltrue([for resource in flatten([statement.Resource]) : contains([
            "arn:aws:iam::${var.expected_account_id}:role/${local.name}-runner", "arn:aws:iam::${var.expected_account_id}:role/${local.name}-execution"
        ], resource)]), false)
      ])
      error_message = "Only the exact task/execution roles may be passed to ECS."
    }
    precondition {
      condition = alltrue([for statement in jsondecode(each.value.json).Statement :
        !contains(flatten([statement.Action]), "route53:ChangeResourceRecordSets") || try(
          statement.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsActions"] == ["DELETE"] &&
          statement.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsRecordTypes"] == ["A"] &&
          length(statement.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsNormalizedRecordNames"]) > 0 &&
          alltrue([for name in statement.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsNormalizedRecordNames"] : contains(var.approved_dns_names, name)]) &&
          statement.Condition.Null["route53:ChangeResourceRecordSetsNormalizedRecordNames"] == "false" &&
          statement.Condition.Null["route53:ChangeResourceRecordSetsActions"] == "false" &&
        statement.Condition.Null["route53:ChangeResourceRecordSetsRecordTypes"] == "false", false)
      ])
      error_message = "DNS mutation must be DELETE-only for reviewed exact names with null guards."
    }
  }
}
resource "aws_scheduler_schedule_group" "control" { name = local.name }
resource "aws_sqs_queue" "delivery_failures" {
  name                      = "${local.name}-delivery-failures"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600
}
resource "aws_ecs_cluster" "control" { name = local.name }
resource "aws_security_group" "control" {
  name_prefix = "${local.name}-"
  vpc_id      = var.control_vpc_id
  ingress     = []
  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
  lifecycle {
    precondition {
      condition     = !contains(var.activity_vpc_ids, var.control_vpc_id) && alltrue([for subnet in data.aws_subnet.control : subnet.vpc_id == var.control_vpc_id])
      error_message = "Cleanup compute must use a separate operator VPC that survives activity cleanup."
    }
  }
}
resource "aws_ecs_task_definition" "control" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.control["execution"].arn
  task_role_arn            = aws_iam_role.control["runner"].arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  volume { name = "work" }
  container_definitions = jsonencode([{
    name                   = "cleanup", image = var.runner_image, essential = true, user = "10001:10001",
    readonlyRootFilesystem = true, privileged = false,
    linuxParameters        = { capabilities = { drop = ["ALL"] } },
    mountPoints            = [{ sourceVolume = "work", containerPath = "/work", readOnly = false }],
    environment = [
      { name = "TTL_ACCOUNT_ID", value = var.expected_account_id },
      { name = "TTL_REGION", value = var.region },
      { name = "TTL_EXPECTED_ROLE_ARN", value = aws_iam_role.control["runner"].arn },
      { name = "TTL_BINDING_SHA256", value = var.binding_sha256 },
      { name = "TTL_BINDING_BUCKET", value = var.state_bucket_name },
      { name = "TTL_BINDING_KEY", value = "aws-workshop-control/${var.expected_account_id}/${var.region}/${var.event_id}/bindings/${var.revision}.json" },
      { name = "TTL_BINDING_VERSION", value = var.binding_object_version }
    ],
    stopTimeout = 120
  }])
  depends_on = [aws_iam_role_policy.control]
}
resource "aws_sfn_state_machine" "control" {
  name     = local.name
  role_arn = aws_iam_role.control["workflow"].arn
  type     = "STANDARD"
  definition = jsonencode({
    StartAt = "Initialize", TimeoutSeconds = var.task_timeout_seconds * 3 + 300,
    States = {
      Initialize = { Type = "Pass", Parameters = { "trigger.$" = "$", attempts = 0 }, Next = "Run" },
      Run = {
        Type = "Task", Resource = "arn:aws:states:::ecs:runTask.sync", TimeoutSeconds = var.task_timeout_seconds,
        Parameters = {
          Cluster              = aws_ecs_cluster.control.arn, TaskDefinition = aws_ecs_task_definition.control.arn,
          LaunchType           = "FARGATE", PlatformVersion = "1.4.0",
          "ClientToken.$"      = "States.Hash(States.Format('{}:{}', $$.Execution.Id, $.attempts), 'SHA-256')",
          NetworkConfiguration = { AwsvpcConfiguration = { Subnets = var.control_subnet_ids, SecurityGroups = [aws_security_group.control.id], AssignPublicIp = "DISABLED" } },
          Overrides            = { ContainerOverrides = [{ Name = "cleanup", Environment = [{ Name = "TTL_TRIGGER_JSON", "Value.$" = "States.JsonToString($.trigger)" }] }] },
          PropagateTags        = "TASK_DEFINITION"
        },
        ResultPath = "$.task", Next = "CheckExit",
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.failure", Next = "Failed" }]
      },
      CheckExit = {
        Type = "Choice", Choices = [
          { And = [{ Variable = "$.task.Containers[0].ExitCode", IsPresent = true }, { Variable = "$.task.Containers[0].ExitCode", NumericEquals = 0 }], Next = "Succeeded" },
          { And = [{ Variable = "$.task.Containers[0].ExitCode", IsPresent = true }, { Variable = "$.task.Containers[0].ExitCode", NumericEquals = 75 }, { Variable = "$.attempts", NumericLessThan = 2 }], Next = "Backoff" }
        ], Default = "Failed"
      },
      Backoff   = { Type = "Wait", Seconds = 30, Next = "Increment" },
      Increment = { Type = "Pass", Parameters = { "trigger.$" = "$.trigger", "attempts.$" = "States.MathAdd($.attempts, 1)" }, Next = "Run" },
      Succeeded = { Type = "Succeed" }, Failed = { Type = "Fail", Error = "CleanupNotReconciled", Cause = "Inspect private control record and execution; unknown tasks are never automatically re-applied." }
    }
  })
}
resource "aws_scheduler_schedule" "control" {
  name                         = local.name
  group_name                   = aws_scheduler_schedule_group.control.name
  schedule_expression          = "at(${trimsuffix(local.trigger_at, "Z")})"
  schedule_expression_timezone = "UTC"
  action_after_completion      = "DELETE"
  state                        = var.enabled ? "ENABLED" : "DISABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_sfn_state_machine.control.arn
    role_arn = aws_iam_role.control["scheduler"].arn
    input    = jsonencode(local.payload)
    dead_letter_config { arn = aws_sqs_queue.delivery_failures.arn }
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }
  lifecycle {
    precondition {
      condition     = !var.enabled || alltrue(values(var.acceptance))
      error_message = "Enabling requires real shared cleanup, runner image, operator fencing and reviewed policies acceptance."
    }
    precondition {
      condition     = !var.enabled || timecmp(local.trigger_at, plantimestamp()) > 0
      error_message = "Do not enable a schedule already in the past."
    }
  }
  depends_on = [aws_iam_role_policy.control]
}
output "control" {
  value = { schedule_arn = aws_scheduler_schedule.control.arn, state_machine_arn = aws_sfn_state_machine.control.arn,
    cluster_arn = aws_ecs_cluster.control.arn, task_definition_arn = aws_ecs_task_definition.control.arn,
  dead_letter_queue_arn = aws_sqs_queue.delivery_failures.arn, enabled = var.enabled, binding_sha256 = var.binding_sha256, trigger_at = local.trigger_at }
}
