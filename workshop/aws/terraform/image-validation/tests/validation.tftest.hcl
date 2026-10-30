mock_provider "aws" {
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_data "aws_ec2_instance_type" { defaults = { supported_architectures = ["x86_64"] } }
  mock_data "aws_ami" {
    defaults = { id = "ami-00000000000000000", architecture = "x86_64", root_device_type = "ebs" }
  }
}
variables {
  aws_profile           = "offline"
  expected_account_id   = "000000000000"
  region                = "us-east-1"
  event_id              = "image-validation-test"
  owner_id              = "test-owner"
  state_bucket_name     = "example-terraform-state"
  ami_id                = "ami-00000000000000000"
  instance_type         = "m7i.2xlarge"
  disk_gib              = 200
  expires_at            = "2030-01-01T00:00:00Z"
  course_ref            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  release_ref           = "candidate-validation"
  image_manifest_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  runtime_sha256        = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  browser_routes        = { s001 = { "8030" = "http://127.0.0.1:49000" } }
}
run "encrypted_fresh_instance_without_inbound_ports" {
  command = apply
  assert {
    condition     = length(aws_security_group.pilot.ingress) == 0 && length(aws_instance.learner) == 1
    error_message = "The validation instance must be isolated with zero inbound access."
  }
  assert {
    condition     = aws_instance.learner["s001"].root_block_device[0].encrypted && aws_instance.learner["s001"].root_block_device[0].delete_on_termination && aws_instance.learner["s001"].metadata_options[0].http_tokens == "required"
    error_message = "Validation needs an encrypted disposable root and IMDSv2."
  }
  assert {
    condition     = length([for statement in jsondecode(aws_iam_role_policy.learner.policy).Statement : statement if statement.Sid == "NoTerraformState" && statement.Effect == "Deny"]) == 1
    error_message = "The validation role must deny deployment state access."
  }
}
run "reject_wrong_account" {
  command = plan
  variables { expected_account_id = "111111111111" }
  expect_failures = [aws_instance.learner]
}
run "reject_wrong_architecture" {
  command = plan
  override_data {
    target = data.aws_ami.ubuntu
    values = { architecture = "arm64" }
  }
  expect_failures = [aws_instance.learner]
}
