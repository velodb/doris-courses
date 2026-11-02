mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_resource "aws_route53_zone" {
    defaults = {
      zone_id      = "ZPARTNEREXAMPLE"
      name_servers = ["ns-4.example.net", "ns-2.example.net", "ns-1.example.net", "ns-3.example.net"]
    }
  }
}
variables {
  aws_profile         = "offline-partner"
  expected_account_id = "000000000000"
  region              = "us-east-1"
  owner_id            = "test-owner"
  partner_id          = "partner-test"
  parent_domain       = "workshop.example.com"
}
run "partner_owned_public_zone_and_parent_handoff" {
  command = apply
  assert {
    condition     = aws_route53_zone.partner.name == "partner-test.workshop.example.com" && !aws_route53_zone.partner.force_destroy && length(aws_route53_zone.partner.vpc) == 0
    error_message = "Create only a non-force-destroy public partner zone."
  }
  assert {
    condition     = aws_route53_zone.partner.tags["Scope"] == "partner-dns" && aws_route53_zone.partner.tags["Partner"] == var.partner_id && aws_route53_zone.partner.tags["Owner"] == var.owner_id && !contains(keys(aws_route53_zone.partner.tags), "Event")
    error_message = "The DNS namespace must have persistent partner ownership, without an activity tag."
  }
  assert {
    condition     = output.delegation.account_id == var.expected_account_id && output.delegation.hosted_zone_id == "ZPARTNEREXAMPLE" && output.delegation.parent_record.name == aws_route53_zone.partner.name && output.delegation.parent_record.type == "NS" && output.delegation.parent_record.ttl == 300 && output.delegation.parent_record.records == sort(aws_route53_zone.partner.name_servers) && length(output.delegation.name_servers) == 4
    error_message = "Export this zone's actual sorted NS and ID for the separate parent operator."
  }
}
run "reject_wrong_partner_account" {
  command = plan
  variables { expected_account_id = "111111111111" }
  expect_failures = [data.aws_caller_identity.current]
}
run "reject_nested_partner_label" {
  command = plan
  variables { partner_id = "partner.nested" }
  expect_failures = [var.partner_id]
}
run "reject_parent_wildcard" {
  command = plan
  variables { parent_domain = "*.workshop.example.com" }
  expect_failures = [var.parent_domain]
}
run "reject_ambient_profile" {
  command = plan
  variables { aws_profile = " " }
  expect_failures = [var.aws_profile]
}
