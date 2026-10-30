terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
  }
  backend "local" {}
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = {
      Project   = "doris-workshop", ImageBuild = var.build_id, Owner = var.owner_id
      ExpiresAt = var.expires_at, CourseRef = local.runtime.course_ref, Scope = "image-build"
    }
  }
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

locals {
  runtime      = jsondecode(file("${var.runtime_bundle_dir}/runtime.json"))
  runtime_hash = filesha256("${var.runtime_bundle_dir}/runtime.json")
  artifact_key = "runtime/${local.runtime_hash}"
  files        = setunion(toset(keys(local.runtime.sha256)), toset(["runtime.json", "image-input.json", "image_host.py", "seal.sh"]))
  name         = "doris-image-${var.build_id}"
  create_vpc   = true
  vpc_id       = aws_vpc.pilot[0].id
  subnet_id    = aws_subnet.pilot[0].id
}

resource "aws_vpc" "pilot" {
  count                = local.create_vpc ? 1 : 0
  cidr_block           = "10.194.0.0/24"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}
resource "aws_internet_gateway" "pilot" {
  count  = local.create_vpc ? 1 : 0
  vpc_id = aws_vpc.pilot[0].id
}
resource "aws_subnet" "pilot" {
  count             = local.create_vpc ? 1 : 0
  vpc_id            = aws_vpc.pilot[0].id
  cidr_block        = "10.194.0.0/26"
  availability_zone = var.availability_zone
}
resource "aws_route_table" "pilot" {
  count  = local.create_vpc ? 1 : 0
  vpc_id = aws_vpc.pilot[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.pilot[0].id
  }
}
resource "aws_route_table_association" "pilot" {
  count          = local.create_vpc ? 1 : 0
  subnet_id      = aws_subnet.pilot[0].id
  route_table_id = aws_route_table.pilot[0].id
}

resource "aws_security_group" "pilot" {
  name_prefix = "${local.name}-"
  description = "SSM-managed workshop host with no inbound rules"
  vpc_id      = local.vpc_id
  # No SSH, Jupyter, SQL or Lab port is exposed directly.
  ingress = []
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_s3_bucket" "runtime" {
  bucket_prefix = "doris-ws-${substr(var.build_id, 0, 16)}-"
  force_destroy = true # This activity bucket contains installer code only.
}
resource "aws_s3_bucket_public_access_block" "runtime" {
  bucket                  = aws_s3_bucket.runtime.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "runtime" {
  bucket = aws_s3_bucket.runtime.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "runtime" {
  bucket = aws_s3_bucket.runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.runtime.arn, "${aws_s3_bucket.runtime.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
resource "aws_s3_object" "runtime" {
  for_each               = local.files
  bucket                 = aws_s3_bucket.runtime.id
  key                    = "${local.artifact_key}/${each.value}"
  source                 = "${var.runtime_bundle_dir}/${each.value}"
  source_hash            = filesha256("${var.runtime_bundle_dir}/${each.value}")
  server_side_encryption = "AES256"
  depends_on             = [aws_s3_bucket_public_access_block.runtime, aws_s3_bucket_policy.runtime]
}

resource "aws_iam_role" "builder" {
  name = "doris-workshop-builder-${var.build_id}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy" "builder" {
  role = aws_iam_role.builder.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SSMControl", Effect = "Allow"
        Action   = ["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
        Resource = "*"
      },
      {
        Sid      = "LegacyAgentMessages", Effect = "Allow"
        Action   = ["ec2messages:AcknowledgeMessage", "ec2messages:DeleteMessage", "ec2messages:FailMessage", "ec2messages:GetEndpoint", "ec2messages:GetMessages", "ec2messages:SendReply"]
        Resource = "*"
      },
      {
        Sid      = "InstallerObjects", Effect = "Allow", Action = ["s3:GetObject"]
        Resource = [for name in local.files : "${aws_s3_bucket.runtime.arn}/${local.artifact_key}/${name}"]
      },
      {
        Sid       = "InstallerPrefix", Effect = "Allow", Action = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.runtime.arn
        Condition = { StringLike = { "s3:prefix" = [local.artifact_key, "${local.artifact_key}/", "${local.artifact_key}/*"] } }
      },
      {
        Sid      = "InstallerRegion", Effect = "Allow", Action = ["s3:GetBucketLocation"]
        Resource = aws_s3_bucket.runtime.arn
      },
      {
        Sid      = "NoDeploymentPrivileges", Effect = "Deny"
        Action   = ["iam:*", "ec2:RunInstances", "ec2:TerminateInstances", "ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "secretsmanager:GetSecretValue"]
        Resource = "*"
      },
      {
        Sid      = "NoTerraformState", Effect = "Deny", Action = ["s3:*"]
        Resource = ["arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}", "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}/*"]
      }
    ]
  })
}
resource "aws_iam_instance_profile" "builder" {
  name = "${local.name}-builder"
  role = aws_iam_role.builder.name
}

resource "aws_ssm_document" "install" {
  name          = "${local.name}-install"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Download this activity's runtime code and install it on the dedicated host."
    mainSteps = [
      {
        action = "aws:downloadContent", name = "downloadRuntime"
        inputs = {
          sourceType      = "S3"
          sourceInfo      = jsonencode({ path = "https://${aws_s3_bucket.runtime.bucket}.s3.${var.region}.${data.aws_partition.current.dns_suffix}/${local.artifact_key}/" })
          destinationPath = "/var/lib/doris-workshop/incoming"
        }
      },
      {
        action = "aws:runShellScript", name = "installRuntime"
        inputs = { timeoutSeconds = "7200", runCommand = ["set -eu", "cd /var/lib/doris-workshop/incoming", "bash install_host.sh"] }
      },
      {
        action = "aws:runShellScript", name = "prepareImage"
        inputs = { timeoutSeconds = "3600", runCommand = ["set -eu", "python3.12 /var/lib/doris-workshop/incoming/image_host.py /var/lib/doris-workshop/incoming"] }
      }
    ]
  })
  depends_on = [aws_s3_object.runtime]
}

output "subnet_id" { value = local.subnet_id }
output "security_group_id" { value = aws_security_group.pilot.id }
output "instance_profile" { value = aws_iam_instance_profile.builder.name }
output "install_document" { value = aws_ssm_document.install.name }
output "runtime_bucket" { value = aws_s3_bucket.runtime.id }
output "builder_role_arn" { value = aws_iam_role.builder.arn }
