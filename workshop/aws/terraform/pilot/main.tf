terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
  }
  backend "s3" {}
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = {
      Project   = "doris-workshop", Event = var.event_id, Owner = var.owner_id
      ExpiresAt = var.expires_at, CourseRef = local.runtime.course_ref, Scope = "activity"
    }
  }
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_ec2_instance_type" "selected" {
  instance_type = var.instance_type
}
data "aws_ami" "ubuntu" {
  owners = [var.ami_owner_account_id]
  filter {
    name   = "image-id"
    values = [var.ami_id]
  }
  dynamic "filter" {
    for_each = var.image_mode == "base" ? [1] : []
    content {
      name   = "name"
      values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
    }
  }
}

locals {
  runtime      = jsondecode(file("${var.runtime_bundle_dir}/runtime.json"))
  runtime_hash = filesha256("${var.runtime_bundle_dir}/runtime.json")
  artifact_key = "runtime/${local.runtime_hash}"
  files        = var.image_mode == "prebuilt" ? toset([]) : setunion(toset(keys(local.runtime.sha256)), toset(["runtime.json"]))
  name         = "doris-workshop-${var.event_id}"
  create_vpc   = var.vpc_id == null
  vpc_id       = local.create_vpc ? aws_vpc.pilot[0].id : var.vpc_id
  subnet_id    = local.create_vpc ? aws_subnet.pilot[0].id : var.subnet_id
}

resource "aws_vpc" "pilot" {
  count                = local.create_vpc ? 1 : 0
  cidr_block           = "10.193.0.0/24"
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
  cidr_block        = "10.193.0.0/26"
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
  bucket_prefix = "doris-ws-${substr(var.event_id, 0, 16)}-"
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

resource "aws_iam_role" "learner" {
  name = "doris-workshop-learner-${var.event_id}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy" "learner" {
  role = aws_iam_role.learner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
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
      ], jsondecode(var.image_mode == "prebuilt" ? "[]" : jsonencode([
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
      ])), [
      {
        Sid      = "NoDeploymentPrivileges", Effect = "Deny"
        Action   = ["iam:*", "ec2:RunInstances", "ec2:TerminateInstances", "ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "secretsmanager:GetSecretValue"]
        Resource = "*"
      },
      {
        Sid      = "NoTerraformState", Effect = "Deny", Action = ["s3:*"]
        Resource = ["arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}", "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket_name}/*"]
      }
    ])
  })
}
resource "aws_iam_instance_profile" "learner" {
  name = "${local.name}-learner"
  role = aws_iam_role.learner.name
}

resource "aws_instance" "learner" {
  for_each                             = toset(var.learner_ids)
  ami                                  = data.aws_ami.ubuntu.id
  instance_type                        = var.instance_type
  subnet_id                            = local.subnet_id
  vpc_security_group_ids               = [aws_security_group.pilot.id]
  associate_public_ip_address          = true
  iam_instance_profile                 = aws_iam_instance_profile.learner.name
  instance_initiated_shutdown_behavior = "terminate"
  user_data_replace_on_change          = true
  user_data = var.image_mode == "base" ? templatefile("${path.module}/user-data.sh.tftpl", {
    expires_at = var.expires_at, runtime_hash = local.runtime_hash
    }) : templatefile("${path.module}/prebuilt-user-data.sh.tftpl", {
    expires_at = var.expires_at,
    activity = base64encode(jsonencode({ schema_version = 1, event_id = var.event_id, learner_id = each.key,
    release_ref = var.image_release_ref, expires_at = var.expires_at, ports = try(var.browser_routes[each.key], {}) })),
    init_script     = file("${path.module}/../../runtime/start_image.py"),
    manifest_sha256 = var.image_manifest_sha256, runtime_sha256 = var.image_runtime_sha256, course_ref = local.runtime.course_ref
  })
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.disk_gib
    encrypted             = true
    delete_on_termination = true
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  tags       = { Name = "${local.name}-${each.key}", Learner = each.key }
  depends_on = [aws_iam_role_policy.learner, aws_route_table_association.pilot]
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.expected_account_id
      error_message = "The effective account must match the explicitly configured target."
    }
    precondition {
      condition     = data.aws_ami.ubuntu.architecture == "x86_64" && data.aws_ami.ubuntu.root_device_type == "ebs"
      error_message = "The selected Canonical Ubuntu AMI must be x86_64 with an EBS root."
    }
    precondition {
      condition     = contains(data.aws_ec2_instance_type.selected.supported_architectures, "x86_64")
      error_message = "The instance type must support the x86_64 runtime."
    }
    precondition {
      condition = var.image_mode == "base" || (
        var.ami_owner_account_id == var.expected_account_id &&
        data.aws_ami.ubuntu.owner_id == var.expected_account_id && !data.aws_ami.ubuntu.public &&
        try(data.aws_ami.ubuntu.tags["ImageStatus"], "") == "validated" &&
        try(data.aws_ami.ubuntu.tags["ReleaseRef"], "") == var.image_release_ref && var.image_release_ref != "" &&
        try(data.aws_ami.ubuntu.tags["ReleaseSHA256"], "") == var.image_release_sha256 &&
        try(data.aws_ami.ubuntu.tags["ManifestSHA256"], "") == var.image_manifest_sha256 &&
      local.runtime_hash == var.image_runtime_sha256)
      error_message = "Prebuilt images must be private validated versions with matching release and runtime hashes."
    }
    precondition {
      condition     = alltrue([for name, checksum in local.runtime.sha256 : filesha256("${var.runtime_bundle_dir}/${name}") == checksum])
      error_message = "Runtime bundle hashes must match before creating an instance."
    }
  }
}

resource "aws_ssm_document" "install" {
  name          = "${local.name}-install"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Download this activity's runtime code and install it on the dedicated host."
    mainSteps = jsondecode(var.image_mode == "prebuilt" ? jsonencode([
      {
        action = "aws:runShellScript", name = "verifyPrebuilt"
        inputs = { timeoutSeconds = "600", runCommand = [
          "set -eu", "cloud-init status --wait >/dev/null",
          "python3.12 /usr/local/lib/doris-workshop/start_image.py --check --activity /etc/doris-workshop/activity.json --manifest-sha256 ${var.image_manifest_sha256} --runtime-sha256 ${var.image_runtime_sha256} --course-ref ${local.runtime.course_ref}",
          "runuser --user workshop -- env -i HOME=/var/lib/doris-workshop/home PATH=/opt/doris-workshop/server-venv/bin:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 PYTHONNOUSERSITE=1 /opt/doris-workshop/server-venv/bin/python -I /usr/local/lib/doris-workshop/check.py --wait 120"
        ] }
      }
      ]) : jsonencode([
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
      }
    ]))
  })
  depends_on = [aws_s3_object.runtime]
}

output "instance_ids" { value = { for id, instance in aws_instance.learner : id => instance.id } }
output "install_document" { value = aws_ssm_document.install.name }
output "runtime_bucket" { value = aws_s3_bucket.runtime.id }
output "learner_role_arn" { value = aws_iam_role.learner.arn }
