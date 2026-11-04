mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::000000000000:role/mock-control" } }
  mock_resource "aws_ecs_cluster" { defaults = { arn = "arn:aws:ecs:us-east-1:000000000000:cluster/mock-control" } }
  mock_resource "aws_ecs_task_definition" { defaults = { arn = "arn:aws:ecs:us-east-1:000000000000:task-definition/mock-control:1" } }
  mock_resource "aws_sfn_state_machine" { defaults = { arn = "arn:aws:states:us-east-1:000000000000:stateMachine:mock-control" } }
  mock_resource "aws_sqs_queue" { defaults = { arn = "arn:aws:sqs:us-east-1:000000000000:mock-control" } }
  mock_data "aws_subnet" { defaults = { vpc_id = "vpc-00000000000000001" } }
}
variables {
  aws_profile            = "offline-test"
  expected_account_id    = "000000000000"
  region                 = "us-east-1"
  event_id               = "class-a"
  owner_id               = "operator"
  binding_sha256         = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  revision               = 1
  expires_at             = "2099-01-01T12:00:31Z"
  runner_image           = "000000000000.dkr.ecr.us-east-1.amazonaws.com/cleanup@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  control_vpc_id         = "vpc-00000000000000001"
  control_subnet_ids     = ["subnet-00000000000000001"]
  activity_vpc_ids       = ["vpc-00000000000000002"]
  state_bucket_name      = "foundation-state-example"
  binding_object_version = "immutable-version"
  policy_documents = {
    scheduler = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
    workflow  = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
    execution = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
    runner    = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
  }
  approved_resource_arns = {
    scheduler = ["arn:aws:states:us-east-1:000000000000:stateMachine:mock-control"]
    workflow  = ["arn:aws:ecs:us-east-1:000000000000:task-definition/mock-control:1"]
    execution = ["arn:aws:ecr:us-east-1:000000000000:repository/cleanup"]
    runner    = ["arn:aws:s3:::foundation-state-example/activity.tflock"]
  }
}
run "disabled_control_plane_has_bounded_nonprivileged_workflow" {
  command = apply
  assert {
    condition     = aws_scheduler_schedule.control.state == "DISABLED" && aws_scheduler_schedule.control.action_after_completion == "DELETE" && aws_scheduler_schedule.control.schedule_expression == "at(2099-01-01T12:01:00)"
    error_message = "Do not deploy an active timer before real acceptance; round up minute precision."
  }
  assert {
    condition     = aws_sfn_state_machine.control.type == "STANDARD" && !can(jsondecode(aws_sfn_state_machine.control.definition).States.Run.Retry) && jsondecode(aws_sfn_state_machine.control.definition).States.CheckExit.Default == "Failed"
    error_message = "Unknown ECS outcomes and nonzero exits cannot silently succeed or blindly reapply."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.control.container_definitions)[0].readonlyRootFilesystem && jsondecode(aws_ecs_task_definition.control.container_definitions)[0].user == "10001:10001" && length(aws_security_group.control.ingress) == 0
    error_message = "The control worker is unprivileged and has no incoming network access."
  }
  assert {
    condition     = length(aws_iam_role.control) == 4 && aws_sqs_queue.delivery_failures.sqs_managed_sse_enabled
    error_message = "Separate scheduler, workflow, image-execution and runner roles; encrypt delivery failures."
  }
  assert {
    condition = alltrue([for item in jsondecode(aws_ecs_task_definition.control.container_definitions)[0].environment :
      item.name != "TTL_EXPECTED_ROLE_ARN" || item.value == aws_iam_role.control["runner"].arn
    ]) && contains([for item in jsondecode(aws_ecs_task_definition.control.container_definitions)[0].environment : item.name], "TTL_ACCOUNT_ID")
    error_message = "The image entry point must receive the fixed account and runner identity."
  }
}
run "reject_unaccepted_enable" {
  command = plan
  variables { enabled = true }
  expect_failures = [aws_scheduler_schedule.control]
}
run "reject_activity_network" {
  command = plan
  variables { activity_vpc_ids = ["vpc-00000000000000001"] }
  expect_failures = [aws_security_group.control]
}
run "reject_floating_image" {
  command = plan
  variables { runner_image = "000000000000.dkr.ecr.us-east-1.amazonaws.com/cleanup:latest" }
  expect_failures = [var.runner_image]
}
run "reject_unversioned_binding" {
  command = plan
  variables { binding_object_version = "null" }
  expect_failures = [var.binding_object_version]
}
run "reject_cross_account_image" {
  command = plan
  variables { expected_account_id = "111111111111" }
  expect_failures = [var.runner_image, var.approved_resource_arns]
}
run "reject_wildcard_mutation_policy" {
  command = plan
  variables {
    policy_documents = {
      for name in ["scheduler", "workflow", "execution", "runner"] : name => {
        json   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "ec2:TerminateInstances", Resource = "*" }] })
        sha256 = sha256(jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "ec2:TerminateInstances", Resource = "*" }] }))
      }
    }
  }
  expect_failures = [aws_iam_role_policy.control]
}
run "reject_foundation_bucket_configuration_mutation" {
  command = plan
  variables {
    approved_resource_arns = { for name in ["scheduler", "workflow", "execution", "runner"] : name => ["arn:aws:s3:::foundation-state-example"] }
    policy_documents = {
      for name in ["scheduler", "workflow", "execution", "runner"] : name => {
        json   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "s3:PutBucketPublicAccessBlock", Resource = "arn:aws:s3:::foundation-state-example" }] })
        sha256 = sha256(jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "s3:PutBucketPublicAccessBlock", Resource = "arn:aws:s3:::foundation-state-example" }] }))
      }
    }
  }
  expect_failures = [aws_iam_role_policy.control]
}
run "accept_exact_dns_a_deletion" {
  command = plan
  variables {
    approved_dns_names = ["class-a-s001.workshops.example.com"]
    approved_resource_arns = {
      scheduler = ["arn:aws:states:us-east-1:000000000000:stateMachine:mock-control"]
      workflow  = ["arn:aws:ecs:us-east-1:000000000000:task-definition/mock-control:1"]
      execution = ["arn:aws:ecr:us-east-1:000000000000:repository/cleanup"]
      runner    = ["arn:aws:route53:::hostedzone/ZEXAMPLE"]
    }
    policy_documents = {
      scheduler = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      workflow  = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      execution = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      runner    = { json = file("tests/fixtures/dns-a-only.json"), sha256 = filesha256("tests/fixtures/dns-a-only.json") }
    }
  }
}
run "reject_dns_deletion_without_record_type_guard" {
  command = plan
  variables {
    approved_dns_names = ["class-a-s001.workshops.example.com"]
    approved_resource_arns = {
      scheduler = ["arn:aws:states:us-east-1:000000000000:stateMachine:mock-control"]
      workflow  = ["arn:aws:ecs:us-east-1:000000000000:task-definition/mock-control:1"]
      execution = ["arn:aws:ecr:us-east-1:000000000000:repository/cleanup"]
      runner    = ["arn:aws:route53:::hostedzone/ZEXAMPLE"]
    }
    policy_documents = {
      scheduler = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      workflow  = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      execution = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      runner    = { json = file("tests/fixtures/dns-unrestricted-type.json"), sha256 = filesha256("tests/fixtures/dns-unrestricted-type.json") }
    }
  }
  expect_failures = [aws_iam_role_policy.control]
}
run "accept_dynamic_dns_change_status_only" {
  command = plan
  variables {
    approved_resource_arns = {
      scheduler = ["arn:aws:states:us-east-1:000000000000:stateMachine:mock-control"]
      workflow  = ["arn:aws:ecs:us-east-1:000000000000:task-definition/mock-control:1"]
      execution = ["arn:aws:ecr:us-east-1:000000000000:repository/cleanup"]
      runner    = ["arn:aws:route53:::change/*"]
    }
    policy_documents = {
      scheduler = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      workflow  = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      execution = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      runner    = { json = file("tests/fixtures/dns-change-status.json"), sha256 = filesha256("tests/fixtures/dns-change-status.json") }
    }
  }
}
run "reject_dynamic_dns_change_other_action" {
  command = plan
  variables {
    approved_resource_arns = {
      scheduler = ["arn:aws:states:us-east-1:000000000000:stateMachine:mock-control"]
      workflow  = ["arn:aws:ecs:us-east-1:000000000000:task-definition/mock-control:1"]
      execution = ["arn:aws:ecr:us-east-1:000000000000:repository/cleanup"]
      runner    = ["arn:aws:route53:::change/*"]
    }
    policy_documents = {
      scheduler = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      workflow  = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      execution = { json = file("tests/fixtures/read-only-policy.json"), sha256 = filesha256("tests/fixtures/read-only-policy.json") }
      runner    = { json = file("tests/fixtures/dns-change-wrong-action.json"), sha256 = filesha256("tests/fixtures/dns-change-wrong-action.json") }
    }
  }
  expect_failures = [aws_iam_role_policy.control]
}
