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
      Project   = "doris-workshop", Event = var.event_id, Owner = var.owner_id
      ExpiresAt = var.expires_at, CourseRef = var.course_ref, Scope = "image-validation"
    }
  }
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_ec2_instance_type" "selected" {
  instance_type = var.instance_type
}
data "aws_ami" "ubuntu" {
  owners = [var.expected_account_id]
  filter {
    name   = "image-id"
    values = [var.ami_id]
  }

}

locals {
  name       = "doris-workshop-${var.event_id}"
  create_vpc = true
  vpc_id     = aws_vpc.pilot[0].id
  subnet_id  = aws_subnet.pilot[0].id
}

resource "aws_vpc" "pilot" {
  count                = local.create_vpc ? 1 : 0
  cidr_block           = "10.195.0.0/24"
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
  cidr_block        = "10.195.0.0/26"
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
  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    expires_at = var.expires_at,
    activity = base64encode(jsonencode({ schema_version = 1, event_id = var.event_id, learner_id = each.key,
      release_ref = var.release_ref, expires_at = var.expires_at, ports = var.browser_routes[each.key],
    services = lookup(var.browser_services, each.key, {}) })),
    init_script     = file("${path.module}/../../runtime/start_image.py"),
    manifest_sha256 = var.image_manifest_sha256, runtime_sha256 = var.runtime_sha256, course_ref = var.course_ref
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

  }
}

output "instance_ids" { value = { for id, instance in aws_instance.learner : id => instance.id } }
output "security_group_id" { value = aws_security_group.pilot.id }
output "vpc_id" { value = local.vpc_id }
output "learner_role_arn" { value = aws_iam_role.learner.arn }
