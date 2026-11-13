terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.71.0" }
  }
  backend "local" {}
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = {
      Project   = "doris-workshop", Scope = "image-distribution", Transfer = var.transfer_id,
      CourseRef = var.course_ref, RetainUntil = var.retain_until
    }
  }
}

data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_account_id
      error_message = "Use the explicitly selected distribution account."
    }
  }
}

# A dedicated key for each side of each transfer makes revocation independent
# of every published source image and every other partner.
resource "aws_kms_key" "copy" {
  description             = "Workshop image transfer ${var.transfer_id}"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  # Disabling this dedicated export key also blocks outstanding KMS grants.
  # Recipient copies already re-encrypted under their own key remain usable.
  is_enabled = var.mode == "import" || var.share_enabled
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([{
      Sid       = "OwnerAdministration", Effect = "Allow",
      Principal = { AWS = "arn:aws:iam::${var.expected_account_id}:root" },
      Action    = "kms:*", Resource = "*"
      }], [for statement in [
      {
        Sid    = "RecipientDescribe", Effect = "Allow", Principal = { AWS = var.recipient_principal_arn },
        Action = ["kms:DescribeKey"], Resource = "*"
      },
      {
        Sid      = "RecipientCopy", Effect = "Allow", Principal = { AWS = var.recipient_principal_arn },
        Action   = ["kms:Decrypt", "kms:ReEncryptFrom", "kms:ReEncryptTo", "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext"],
        Resource = "*", Condition = { StringEquals = { "kms:ViaService" = "ec2.${var.region}.amazonaws.com" } }
      },
      {
        Sid    = "RecipientAWSGrant", Effect = "Allow", Principal = { AWS = var.recipient_principal_arn },
        Action = ["kms:CreateGrant"], Resource = "*",
        Condition = {
          Bool         = { "kms:GrantIsForAWSResource" = "true" },
          StringEquals = { "kms:ViaService" = "ec2.${var.region}.amazonaws.com" }
        }
      }
    ] : statement if var.share_enabled])
  })
}

resource "aws_ami_copy" "copy" {
  name              = "doris-${var.transfer_id}-${var.mode}"
  source_ami_id     = var.source_ami_id
  source_ami_region = var.region
  encrypted         = true
  kms_key_id        = aws_kms_key.copy.arn
  tags = {
    ImageStatus    = "candidate", ImageBuild = var.transfer_id,
    ManifestSHA256 = var.manifest_sha256, SourceReleaseSHA256 = var.source_release_sha256
  }
  timeouts { create = "120m" }
  lifecycle {
    precondition {
      condition     = var.mode == "export" ? var.source_account_id == var.expected_account_id && var.recipient_account_id != var.expected_account_id : var.source_account_id != var.expected_account_id && var.recipient_account_id == var.expected_account_id && !var.share_enabled
      error_message = "Export owns the source; import owns an unshared recipient copy in a different account."
    }
  }
}

resource "aws_ami_launch_permission" "recipient" {
  count      = var.share_enabled ? 1 : 0
  image_id   = aws_ami_copy.copy.id
  account_id = var.recipient_account_id
}

# Unlike launching a shared AMI, copying it requires snapshot read permission.
resource "aws_snapshot_create_volume_permission" "recipient" {
  count       = var.share_enabled ? 1 : 0
  snapshot_id = aws_ami_copy.copy.root_snapshot_id
  account_id  = var.recipient_account_id
}

output "artifact" {
  value = {
    ami_id        = aws_ami_copy.copy.id, snapshot_ids = [aws_ami_copy.copy.root_snapshot_id],
    kms_key_arn   = aws_kms_key.copy.arn, account_id = data.aws_caller_identity.current.account_id,
    region        = var.region, transfer_id = var.transfer_id, shared = var.share_enabled,
    source_ami_id = var.source_ami_id
  }
}
