mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws", dns_suffix = "amazonaws.com" }
  }
}
variables {
  aws_profile         = "offline-test"
  region              = "us-east-1"
  expected_account_id = "000000000000"
  owner_id            = "test-owner"
  state_bucket_name   = "example-workshop-state"
}

run "encrypted_private_versioned_state" {
  command = apply
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls && aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.ignore_public_acls && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "State storage must block all public access."
  }
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State storage must retain versions."
  }
  assert {
    condition     = !aws_s3_bucket.state.force_destroy
    error_message = "State storage must not be force-deleted."
  }
  assert {
    condition     = length([for statement in jsondecode(aws_s3_bucket_policy.state.policy).Statement : statement if statement.Sid == "DenyLearnerRoles" && statement.Effect == "Deny"]) == 1
    error_message = "Learner roles must be excluded from foundation state."
  }
}
