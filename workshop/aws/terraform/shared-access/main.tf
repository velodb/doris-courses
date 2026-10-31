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
    tags = { Project = "doris-workshop", Owner = var.owner_id, Scope = "shared-access", Access = var.access_id }
  }
}

data "aws_caller_identity" "current" {}
data "aws_route53_zone" "public" {
  zone_id      = var.hosted_zone_id
  private_zone = false
}

locals {
  name  = "doris-access-${var.access_id}"
  zones = { for index, zone in var.availability_zones : zone => index }
}

resource "aws_vpc" "access" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}
resource "aws_internet_gateway" "access" { vpc_id = aws_vpc.access.id }
resource "aws_subnet" "access" {
  for_each          = local.zones
  vpc_id            = aws_vpc.access.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, each.value)
}
resource "aws_route_table" "access" {
  vpc_id = aws_vpc.access.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.access.id
  }
}
resource "aws_route_table_association" "access" {
  for_each       = local.zones
  subnet_id      = aws_subnet.access[each.key].id
  route_table_id = aws_route_table.access.id
}
resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-"
  vpc_id      = aws_vpc.access.id
  description = "Public HTTPS and HTTP redirect; backends remain closed"
  ingress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = ["0.0.0.0/0"]
  }
  # HTTPS reaches the IdP token/userinfo endpoints. No learner services use 443.
  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    protocol    = "tcp"
    from_port   = 8080
    to_port     = 8080
    cidr_blocks = [var.vpc_cidr]
  }
}
resource "aws_security_group" "gateway" {
  name_prefix = "${local.name}-gateway-"
  vpc_id      = aws_vpc.access.id
  description = "Only the shared ALB can reach learner gateways"
  ingress {
    protocol        = "tcp"
    from_port       = 8080
    to_port         = 8080
    security_groups = [aws_security_group.alb.id]
  }
  egress = []
}
resource "aws_acm_certificate" "access" {
  domain_name       = "*.${var.domain}"
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
    precondition {
      condition = !data.aws_route53_zone.public.private_zone && (
        var.domain == trimsuffix(data.aws_route53_zone.public.name, ".") ||
        endswith(var.domain, ".${trimsuffix(data.aws_route53_zone.public.name, ".")}")
      )
      error_message = "The access domain must be inside the selected public DNS zone."
    }
  }
}
resource "aws_route53_record" "validation" {
  zone_id = var.hosted_zone_id
  name    = one(aws_acm_certificate.access.domain_validation_options).resource_record_name
  type    = one(aws_acm_certificate.access.domain_validation_options).resource_record_type
  records = [one(aws_acm_certificate.access.domain_validation_options).resource_record_value]
  ttl     = 60
}
resource "aws_acm_certificate_validation" "access" {
  certificate_arn         = aws_acm_certificate.access.arn
  validation_record_fqdns = [aws_route53_record.validation.fqdn]
}
resource "aws_lb" "access" {
  name_prefix                = "dws-"
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = [for subnet in aws_subnet.access : subnet.id]
  security_groups            = [aws_security_group.alb.id]
  idle_timeout               = 3600
  preserve_host_header       = true
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  depends_on                 = [aws_route_table_association.access]
}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.access.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.access.certificate_arn
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "403"
      message_body = "No authorized workshop route."
    }
  }
}
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.access.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

output "access" {
  value = {
    schema_version            = 1, access_id = var.access_id, account_id = data.aws_caller_identity.current.account_id,
    region                    = var.region, domain = var.domain, hosted_zone_id = var.hosted_zone_id,
    vpc_id                    = aws_vpc.access.id, subnet_ids = { for zone, subnet in aws_subnet.access : zone => subnet.id },
    alb_arn                   = aws_lb.access.arn, alb_security_group_id = aws_security_group.alb.id,
    alb_dns_name              = aws_lb.access.dns_name, alb_zone_id = aws_lb.access.zone_id,
    https_listener_arn        = aws_lb_listener.https.arn, gateway_port = 8080,
    gateway_security_group_id = aws_security_group.gateway.id
  }
}
