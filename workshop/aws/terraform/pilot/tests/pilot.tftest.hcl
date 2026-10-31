# Provider mocks never contact AWS or create real resources.
mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws", dns_suffix = "amazonaws.com" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }
  mock_data "aws_ec2_instance_type" {
    defaults = { supported_architectures = ["x86_64"] }
  }
  mock_data "aws_ami" {
    defaults = {
      id               = "ami-00000000000000000"
      architecture     = "x86_64"
      root_device_type = "ebs"
    }
  }
}

variables {
  aws_profile         = "offline-test"
  expected_account_id = "000000000000"
  region              = "us-east-1"
  owner_id            = "test-owner"
  state_bucket_name   = "example-terraform-state"
  event_id            = "test-event"
  ami_id              = "ami-00000000000000000"
  expires_at          = "2099-01-01T00:00:00Z"
  runtime_bundle_dir  = "tests/fixtures/runtime"
}

run "protected_single_instance" {
  command = apply

  assert {
    condition     = length(aws_instance.learner) == 1 && contains(keys(aws_instance.learner), "s001")
    error_message = "The pilot must use a stable learner key."
  }
  assert {
    condition     = length(aws_security_group.pilot.ingress) == 0
    error_message = "The pilot must have no direct inbound access."
  }
  assert {
    condition     = aws_instance.learner["s001"].root_block_device[0].encrypted && aws_instance.learner["s001"].root_block_device[0].delete_on_termination
    error_message = "Root disks must be encrypted and deleted when the instance terminates."
  }
  assert {
    condition     = aws_instance.learner["s001"].metadata_options[0].http_tokens == "required"
    error_message = "Instance metadata must require IMDSv2."
  }
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_role_policy.learner.policy).Statement : statement.Effect != "Allow" || !contains(statement.Action, "ssm:GetParameter")])
    error_message = "The instance role must not receive unrestricted parameter access."
  }
  assert {
    condition     = length([for statement in jsondecode(aws_iam_role_policy.learner.policy).Statement : statement if statement.Sid == "NoTerraformState" && statement.Effect == "Deny"]) == 1
    error_message = "State access must be explicitly denied for the learner role."
  }
}

run "reject_incomplete_existing_network" {
  command = plan
  variables { vpc_id = "vpc-00000000000000000" }
  expect_failures = [var.subnet_id]
}

run "reject_wrong_account" {
  command = plan
  variables { expected_account_id = "111111111111" }
  expect_failures = [aws_instance.learner]
}

run "reject_arm_instance" {
  command = plan
  override_data {
    target = data.aws_ec2_instance_type.selected
    values = { supported_architectures = ["arm64"] }
  }
  expect_failures = [aws_instance.learner]
}

run "two_stable_learner_keys" {
  command = apply
  variables {
    learner_ids       = ["s002", "s001"]
    availability_zone = "us-east-1a"
  }
  assert {
    condition     = toset(keys(aws_instance.learner)) == toset(["s001", "s002"])
    error_message = "Both learners must have their own instance and stable keys."
  }
  assert {
    condition     = alltrue([for instance in aws_instance.learner : instance.root_block_device[0].delete_on_termination && instance.root_block_device[0].encrypted])
    error_message = "Every learner root disk must be encrypted and deleted on termination."
  }
  assert {
    condition     = aws_subnet.pilot[0].availability_zone == "us-east-1a"
    error_message = "The integration subnet must use the verified instance offering's zone."
  }
}

run "reject_duplicate_learner_ids" {
  command = plan
  variables { learner_ids = ["s001", "s001"] }
  expect_failures = [var.learner_ids]
}

run "reject_reserved_spare_id" {
  command = plan
  variables { learner_ids = ["spare-001"] }
  expect_failures = [var.learner_ids]
}

run "validated_prebuilt_injects_only_activity" {
  command = plan
  variables {
    image_mode            = "prebuilt"
    ami_owner_account_id  = "000000000000"
    image_release_ref     = "rta-v1"
    image_release_sha256  = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    image_manifest_sha256 = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    image_runtime_sha256  = filesha256("tests/fixtures/runtime/runtime.json")
  }
  override_data {
    target = data.aws_ami.ubuntu
    values = {
      owner_id         = "000000000000"
      public           = false
      architecture     = "x86_64"
      root_device_type = "ebs"
      tags = {
        ImageStatus    = "validated"
        ReleaseRef     = "rta-v1"
        ReleaseSHA256  = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        ManifestSHA256 = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      }
    }
  }
  assert {
    condition     = length(aws_s3_object.runtime) == 0 && length(regexall("install_host.sh|pip install|apt-get", aws_instance.learner["s001"].user_data)) == 0
    error_message = "Prebuilt startup must not upload or execute the dependency installer."
  }
  assert {
    condition     = jsondecode(aws_ssm_document.install.content).mainSteps[0].name == "verifyPrebuilt" && length(jsondecode(aws_ssm_document.install.content).mainSteps) == 1
    error_message = "The prebuilt SSM operation must only check the injected activity and runtime."
  }
  assert {
    condition     = length([for statement in jsondecode(aws_iam_role_policy.learner.policy).Statement : statement if statement.Effect == "Allow" && contains(statement.Action, "s3:GetObject")]) == 0
    error_message = "Prebuilt learner roles must not receive runtime S3 download permissions."
  }
}

run "reject_unpublished_prebuilt" {
  command = plan
  variables {
    image_mode           = "prebuilt"
    ami_owner_account_id = "000000000000"
  }
  expect_failures = [aws_instance.learner]
}
run "shared_network_keeps_service_ports_closed" {
  command = apply
  variables {
    vpc_id                    = "vpc-00000000000000000"
    subnet_id                 = "subnet-00000000000000000"
    gateway_security_group_id = "sg-00000000000000000"
  }
  assert {
    condition     = length(aws_vpc.pilot) == 0 && contains(aws_instance.learner["s001"].vpc_security_group_ids, "sg-00000000000000000") && length(aws_security_group.pilot.ingress) == 0
    error_message = "Shared access reuses its VPC without exposing backend service ports."
  }
}
run "reject_gateway_in_an_unrelated_vpc" {
  command = plan
  variables { gateway_security_group_id = "sg-00000000000000000" }
  expect_failures = [var.gateway_security_group_id]
}
