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
}

data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_account_id
      error_message = "The DNS zone must belong to the explicitly selected partner account."
    }
  }
}

locals {
  domain = "${var.partner_id}.${var.parent_domain}"
}

# Route 53 creates the zone's authoritative NS and SOA. Parent delegation is a
# separate company operation; no parent-zone credentials or records are owned.
resource "aws_route53_zone" "partner" {
  name          = local.domain
  comment       = "Partner namespace retained independently of workshop activities"
  force_destroy = false
  tags = {
    Project = "doris-workshop", Owner = var.owner_id, Scope = "partner-dns",
    Partner = var.partner_id, ParentDomain = var.parent_domain
  }
  lifecycle { prevent_destroy = true }
}

output "delegation" {
  value = {
    schema_version = 1
    account_id     = data.aws_caller_identity.current.account_id
    partner_id     = var.partner_id
    domain         = local.domain
    parent_domain  = var.parent_domain
    hosted_zone_id = aws_route53_zone.partner.zone_id
    name_servers   = sort(aws_route53_zone.partner.name_servers)
    parent_record = {
      name    = local.domain
      type    = "NS"
      ttl     = 300
      records = sort(aws_route53_zone.partner.name_servers)
    }
  }
}
