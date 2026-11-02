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

provider "aws" {
  alias               = "dns"
  region              = var.region
  profile             = var.dns == null ? var.aws_profile : (var.dns.profile == null ? var.aws_profile : var.dns.profile)
  allowed_account_ids = [local.dns_account_id]
  dynamic "assume_role" {
    for_each = var.dns == null ? [] : (var.dns.role_arn == null ? [] : [var.dns.role_arn])
    content { role_arn = assume_role.value }
  }
}

data "aws_caller_identity" "current" {}
data "aws_caller_identity" "dns" {
  provider = aws.dns
  lifecycle {
    postcondition {
      condition     = self.account_id == local.dns_account_id
      error_message = "The DNS identity must match the explicitly selected DNS account."
    }
  }
}
data "aws_route53_zone" "public" {
  provider     = aws.dns
  zone_id      = var.hosted_zone_id
  private_zone = false
}

locals {
  name           = "doris-access-${var.access_id}"
  zones          = { for index, zone in var.availability_zones : zone => index }
  dns_account_id = var.dns == null ? var.expected_account_id : var.dns.account_id
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
  domain_name               = "*.${var.domain}"
  subject_alternative_names = [var.domain]
  validation_method         = "DNS"
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
  provider = aws.dns
  zone_id  = var.hosted_zone_id
  # ACM returns the same validation CNAME for an apex and its wildcard. Select
  # the apex explicitly so keys remain known in the first (unapplied) plan.
  name    = one([for option in aws_acm_certificate.access.domain_validation_options : option.resource_record_name if option.domain_name == var.domain])
  type    = "CNAME"
  records = [one([for option in aws_acm_certificate.access.domain_validation_options : option.resource_record_value if option.domain_name == var.domain])]
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

resource "aws_route53_record" "root" {
  provider = aws.dns
  zone_id  = var.hosted_zone_id
  name     = var.domain
  type     = "A"
  alias {
    name                   = aws_lb.access.dns_name
    zone_id                = aws_lb.access.zone_id
    evaluate_target_health = true
  }
}

# Provision the operator's host separately, then attach this dedicated group.
# No learner code, Docker socket, deployment credentials or TLS keys run here.
resource "aws_security_group" "portal" {
  name_prefix = "${local.name}-portal-"
  vpc_id      = aws_vpc.access.id
  ingress {
    protocol        = "tcp"
    from_port       = 8080
    to_port         = 8080
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
}
data "aws_instance" "portal" {
  count       = var.portal_instance_id == null ? 0 : 1
  instance_id = var.portal_instance_id
}
data "aws_subnet" "portal" {
  count = var.portal_instance_id == null ? 0 : 1
  id    = data.aws_instance.portal[0].subnet_id
}
resource "aws_lb_target_group" "portal" {
  count                = var.portal_instance_id == null ? 0 : 1
  name_prefix          = "dwsp-"
  vpc_id               = aws_vpc.access.id
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "instance"
  deregistration_delay = 10
  health_check {
    path    = "/healthz"
    matcher = "200"
  }
  lifecycle {
    precondition {
      condition     = data.aws_subnet.portal[0].vpc_id == aws_vpc.access.id && contains(data.aws_instance.portal[0].vpc_security_group_ids, aws_security_group.portal.id) && try(data.aws_instance.portal[0].tags["Project"], "") == "doris-workshop" && try(data.aws_instance.portal[0].tags["Scope"], "") == "shared-portal" && try(data.aws_instance.portal[0].tags["Owner"], "") == var.owner_id && try(data.aws_instance.portal[0].tags["Access"], "") == var.access_id && !contains(keys(data.aws_instance.portal[0].tags), "Event")
      error_message = "Use a separately owned shared-portal host in this VPC with the portal SG, matching Owner/Access and no activity Event tag."
    }
  }
}
resource "aws_lb_target_group_attachment" "portal" {
  count            = var.portal_instance_id == null ? 0 : 1
  target_group_arn = aws_lb_target_group.portal[0].arn
  target_id        = var.portal_instance_id
  port             = 8080
}
resource "aws_lb_listener_rule" "portal" {
  count        = var.portal_instance_id == null ? 0 : 1
  listener_arn = aws_lb_listener.https.arn
  priority     = var.portal_priority
  condition {
    host_header { values = [var.domain] }
  }
  condition {
    path_pattern { values = ["/doris-courses", "/doris-courses/"] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.portal[0].arn
  }
}

output "access" {
  value = {
    schema_version            = 1, access_id = var.access_id, account_id = data.aws_caller_identity.current.account_id,
    region                    = var.region, domain = var.domain, hosted_zone_id = var.hosted_zone_id,
    dns_account_id            = data.aws_caller_identity.dns.account_id,
    vpc_id                    = aws_vpc.access.id, subnet_ids = { for zone, subnet in aws_subnet.access : zone => subnet.id },
    alb_arn                   = aws_lb.access.arn, alb_security_group_id = aws_security_group.alb.id,
    alb_dns_name              = aws_lb.access.dns_name, alb_zone_id = aws_lb.access.zone_id,
    https_listener_arn        = aws_lb_listener.https.arn, gateway_port = 8080,
    gateway_security_group_id = aws_security_group.gateway.id,
    portal_security_group_id  = aws_security_group.portal.id,
    portal = var.portal_instance_id == null ? null : {
      instance_id       = var.portal_instance_id, target_group_arn = aws_lb_target_group.portal[0].arn,
      listener_priority = var.portal_priority
    }
  }
}
