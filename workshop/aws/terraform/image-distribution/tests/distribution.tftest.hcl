mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000" }
  }
  mock_resource "aws_ami_copy" {
    defaults = { id = "ami-11111111111111111", root_snapshot_id = "snap-11111111111111111" }
  }
}
variables {
  aws_profile             = "offline-source"
  expected_account_id     = "000000000000"
  source_account_id       = "000000000000"
  recipient_account_id    = "111111111111"
  recipient_principal_arn = "arn:aws:iam::111111111111:role/WorkshopImporter"
  region                  = "us-east-1"
  mode                    = "export"
  share_enabled           = true
  source_ami_id           = "ami-00000000000000000"
  transfer_id             = "rta-partner-v1"
  course_ref              = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  manifest_sha256         = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  source_release_sha256   = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  retain_until            = "2099-01-01T00:00:00Z"
}
run "separate_encrypted_distribution_copy_and_exact_grants" {
  command = apply
  assert {
    condition     = aws_ami_copy.copy.source_ami_id == var.source_ami_id && aws_ami_copy.copy.encrypted && aws_ami_copy.copy.kms_key_id == aws_kms_key.copy.arn
    error_message = "Own and encrypt a new copy, with separately managed snapshot cleanup."
  }
  assert {
    condition     = aws_ami_launch_permission.recipient[0].account_id == var.recipient_account_id && aws_snapshot_create_volume_permission.recipient[0].account_id == var.recipient_account_id && aws_ami_launch_permission.recipient[0].image_id != var.source_ami_id
    error_message = "Only the named recipient gets AMI and snapshot access to the copy."
  }
  assert {
    condition     = length(jsondecode(aws_kms_key.copy.policy).Statement) == 4 && jsondecode(aws_kms_key.copy.policy).Statement[3].Condition.Bool["kms:GrantIsForAWSResource"] == "true" && jsondecode(aws_kms_key.copy.policy).Statement[1].Principal.AWS == var.recipient_principal_arn
    error_message = "Restrict cross-account key use to the selected principal and AWS-created grants."
  }
}
run "revoke_preserves_copy_and_removes_policy_and_ec2_permissions" {
  command = apply
  variables {
    share_enabled = false
    revoked       = true
  }
  assert {
    condition     = length(aws_ami_launch_permission.recipient) == 0 && length(aws_snapshot_create_volume_permission.recipient) == 0 && length(jsondecode(aws_kms_key.copy.policy).Statement) == 1 && output.artifact.shared == false && !aws_kms_key.copy.is_enabled
    error_message = "Revocation must remove future sharing, not claim to recall recipient copies."
  }
}
run "recipient_owns_separate_unshared_copy" {
  command = plan
  variables {
    mode                    = "import"
    source_account_id       = "111111111111"
    recipient_account_id    = "000000000000"
    recipient_principal_arn = "arn:aws:iam::000000000000:role/WorkshopImporter"
    share_enabled           = false
  }
  assert {
    condition     = length(aws_ami_launch_permission.recipient) == 0 && length(jsondecode(aws_kms_key.copy.policy).Statement) == 1
    error_message = "The recipient copy must have no external key or image grants."
  }
}
run "reject_same_account_export" {
  command = plan
  variables {
    recipient_account_id    = "000000000000"
    recipient_principal_arn = "arn:aws:iam::000000000000:role/WorkshopImporter"
  }
  expect_failures = [aws_ami_copy.copy]
}
run "reject_root_recipient" {
  command = plan
  variables { recipient_principal_arn = "arn:aws:iam::111111111111:root" }
  expect_failures = [var.recipient_principal_arn]
}
run "reject_foreign_recipient_principal" {
  command = plan
  variables { recipient_principal_arn = "arn:aws:iam::222222222222:role/WorkshopImporter" }
  expect_failures = [var.recipient_principal_arn]
}
run "reject_wrong_effective_account" {
  command = plan
  variables { expected_account_id = "222222222222" }
  expect_failures = [data.aws_caller_identity.current, aws_ami_copy.copy]
}
run "stage_without_any_recipient_keeps_key_enabled_and_grants_absent" {
  command = apply
  variables {
    recipient_account_id    = ""
    recipient_principal_arn = ""
    share_enabled           = false
    revoked                 = false
  }
  assert {
    condition     = aws_kms_key.copy.is_enabled && length(jsondecode(aws_kms_key.copy.policy).Statement) == 1 && length(aws_ami_launch_permission.recipient) == 0 && length(aws_snapshot_create_volume_permission.recipient) == 0 && !output.artifact.shared
    error_message = "Source preparation requires no recipient identity and must not grant external access."
  }
}
run "sharing_cannot_omit_recipient" {
  command = plan
  variables {
    recipient_account_id    = ""
    recipient_principal_arn = ""
    share_enabled           = true
  }
  expect_failures = [var.recipient_account_id]
}
run "sharing_requires_exact_recipient_principal" {
  command = plan
  variables { recipient_principal_arn = "" }
  expect_failures = [var.recipient_principal_arn]
}
run "reject_shared_revoked_key" {
  command = plan
  variables { revoked = true }
  expect_failures = [aws_ami_copy.copy]
}
