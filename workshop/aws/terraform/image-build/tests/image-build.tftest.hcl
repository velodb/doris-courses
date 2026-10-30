mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws", dns_suffix = "amazonaws.com" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }
}
variables {
  aws_profile         = "offline-test"
  expected_account_id = "000000000000"
  region              = "us-east-1"
  owner_id            = "test-owner"
  state_bucket_name   = "example-terraform-state"
  build_id            = "test-image-build"
  expires_at          = "2099-01-01T00:00:00Z"
  availability_zone   = "us-east-1a"
  runtime_bundle_dir  = "tests/fixtures/runtime"
}
run "private_build_resources" {
  command = apply
  assert {
    condition     = length(aws_security_group.pilot.ingress) == 0
    error_message = "Image builders must have no inbound port."
  }
  assert {
    condition     = startswith(aws_iam_role.builder.name, "doris-workshop-builder-")
    error_message = "The image role must be separate from learner roles."
  }
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_role_policy.builder.policy).Statement : statement.Effect != "Allow" || !contains(statement.Action, "s3:PutObject")])
    error_message = "The builder must not upload credentials or state."
  }
  assert {
    condition     = length([for statement in jsondecode(aws_iam_role_policy.builder.policy).Statement : statement if statement.Sid == "NoTerraformState" && statement.Effect == "Deny"]) == 1
    error_message = "State access must be explicitly denied."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.runtime.block_public_policy && aws_s3_bucket_public_access_block.runtime.block_public_acls
    error_message = "Build inputs must stay private."
  }
}
run "reject_unsafe_build_name" {
  command = plan
  variables { build_id = "../another-run" }
  expect_failures = [var.build_id]
}
