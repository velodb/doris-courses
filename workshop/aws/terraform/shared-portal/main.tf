terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.71.0" }
  }
  backend "s3" {}
}
provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = { Project = "doris-workshop", Scope = "shared-portal", Owner = var.owner_id, Access = var.access_id }
  }
}
data "aws_caller_identity" "current" {}
data "aws_subnet" "selected" { id = var.subnet_id }
data "aws_vpc" "selected" { id = var.vpc_id }
data "aws_security_group" "portal" { id = var.portal_security_group_id }
data "aws_vpc_security_group_rules" "portal" {
  filter {
    name   = "group-id"
    values = [var.portal_security_group_id]
  }
}
data "aws_vpc_security_group_rule" "portal" {
  for_each               = toset(data.aws_vpc_security_group_rules.portal.ids)
  security_group_rule_id = each.value
}
locals {
  portal_ingress = [for rule in data.aws_vpc_security_group_rule.portal : rule if !rule.is_egress]
}
data "aws_ami" "base" {
  owners = ["099720109477"]
  filter {
    name   = "image-id"
    values = [var.ami_id]
  }
}

# EC2 instance-profile trust uses the EC2 service principal. The operator's
# scoped PassRole permission controls attachment; no deployment role runs here.
resource "aws_iam_role" "portal" {
  name = "doris-workshop-portal-${var.access_id}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.portal.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "portal" {
  name = aws_iam_role.portal.name
  role = aws_iam_role.portal.name
}
resource "aws_instance" "portal" {
  ami                         = data.aws_ami.base.id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [var.portal_security_group_id]
  iam_instance_profile        = aws_iam_instance_profile.portal.name
  user_data                   = file("${path.module}/user-data.sh")
  user_data_replace_on_change = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }
  credit_specification { cpu_credits = "standard" }
  root_block_device {
    encrypted             = true
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
  }
  tags       = { Name = "doris-portal-${var.access_id}" }
  depends_on = [aws_iam_role_policy_attachment.ssm]
  lifecycle {
    precondition {
      condition = (data.aws_subnet.selected.vpc_id == var.vpc_id && data.aws_security_group.portal.vpc_id == var.vpc_id &&
        try(data.aws_vpc.selected.tags["Project"], "") == "doris-workshop" &&
        try(data.aws_vpc.selected.tags["Owner"], "") == var.owner_id &&
      try(data.aws_vpc.selected.tags["Access"], "") == var.access_id)
      error_message = "The subnet and portal group must belong to the operator-owned shared-access VPC."
    }
    precondition {
      condition = length(local.portal_ingress) == 1 && alltrue([
        for rule in local.portal_ingress : rule.ip_protocol == "tcp" && rule.from_port == 8080 && rule.to_port == 8080 &&
        rule.referenced_security_group_id == var.alb_security_group_id &&
        (rule.cidr_ipv4 == null || rule.cidr_ipv4 == "") && (rule.cidr_ipv6 == null || rule.cidr_ipv6 == "") &&
        (rule.prefix_list_id == null || rule.prefix_list_id == "")
      ])
      error_message = "Use only the portal group with ALB-only TCP 8080 ingress."
    }
    precondition {
      condition = (data.aws_ami.base.owner_id == "099720109477" && data.aws_ami.base.architecture == "x86_64" &&
        data.aws_ami.base.root_device_type == "ebs" && data.aws_ami.base.virtualization_type == "hvm" &&
      startswith(data.aws_ami.base.name, "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-"))
      error_message = "Use a verified official Ubuntu 24.04 amd64 EBS AMI for this separate portal host."
    }
  }
}
output "portal" {
  value = {
    instance_id       = aws_instance.portal.id
    account_id        = data.aws_caller_identity.current.account_id
    access_id         = var.access_id
    vpc_id            = var.vpc_id
    subnet_id         = var.subnet_id
    security_group_id = var.portal_security_group_id
    role_arn          = aws_iam_role.portal.arn
    root_volume_id    = aws_instance.portal.root_block_device[0].volume_id
  }
}
