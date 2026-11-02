mock_provider "aws" {
  mock_resource "aws_lb_listener" { defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/example/0123456789abcdef/0123456789abcdef" } }
  mock_resource "aws_lb_target_group" { defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/portal/0123456789abcdef" } }
  mock_resource "aws_vpc" { defaults = { id = "vpc-00000000000000000" } }
  mock_resource "aws_security_group" { defaults = { id = "sg-00000000000000000" } }
  mock_resource "aws_lb" { defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/example/0123456789abcdef" } }
  mock_resource "aws_acm_certificate_validation" { defaults = { certificate_arn = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-4000-8000-000000000000" } }
  mock_resource "aws_acm_certificate" {
    defaults = { arn = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-4000-8000-000000000000", domain_validation_options = [
      { domain_name = "*.workshops.example.com", resource_record_name = "_test.workshops.example.com", resource_record_type = "CNAME", resource_record_value = "_test.acm-validations.aws" },
      { domain_name = "workshops.example.com", resource_record_name = "_test.workshops.example.com", resource_record_type = "CNAME", resource_record_value = "_test.acm-validations.aws" }
    ] }
  }
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_data "aws_route53_zone" { defaults = { name = "workload.invalid.", private_zone = false } }
  mock_data "aws_subnet" { defaults = { vpc_id = "vpc-00000000000000000" } }
  mock_data "aws_instance" { defaults = {
    subnet_id = "subnet-00000000000000000", vpc_security_group_ids = ["sg-00000000000000000"],
    tags      = { Project = "doris-workshop", Scope = "shared-portal", Owner = "test-owner", Access = "test-access" }
  } }
}
mock_provider "aws" {
  alias = "dns"
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_data "aws_route53_zone" { defaults = { name = "example.com.", private_zone = false } }
  mock_resource "aws_route53_record" { defaults = { fqdn = "dns-provider.example.com" } }
}
variables {
  aws_profile         = "offline-test"
  expected_account_id = "000000000000"
  region              = "us-east-1"
  owner_id            = "test-owner"
  access_id           = "test-access"
  domain              = "workshops.example.com"
  hosted_zone_id      = "ZEXAMPLE"
  availability_zones  = ["us-east-1a", "us-east-1b"]
}
run "separate_dns_account_keeps_certificate_and_alb_in_workload" {
  command   = apply
  state_key = "separate-dns"
  variables { dns = { account_id = "111111111111", role_arn = "arn:aws:iam::111111111111:role/workshop-dns" } }
  override_data {
    target = data.aws_caller_identity.dns
    values = { account_id = "111111111111" }
  }
  assert {
    condition     = output.access.account_id == "000000000000" && output.access.dns_account_id == "111111111111" && aws_route53_record.root.fqdn == "dns-provider.example.com" && aws_route53_record.validation.fqdn == "dns-provider.example.com" && startswith(aws_acm_certificate.access.arn, "arn:aws:acm:us-east-1:000000000000:")
    error_message = "Only DNS records and lookup use the separate DNS provider."
  }
}
run "reject_wrong_dns_identity" {
  command = plan
  variables { dns = { account_id = "111111111111", profile = "dns-operator" } }
  expect_failures = [data.aws_caller_identity.dns]
}
run "reject_dns_role_in_other_account" {
  command = plan
  variables { dns = { account_id = "111111111111", role_arn = "arn:aws:iam::222222222222:role/dns" } }
  expect_failures = [var.dns]
}
run "shared_tls_defaults_closed" {
  command = apply
  assert {
    condition     = length(aws_subnet.access) == 2 && aws_lb.access.preserve_host_header && aws_lb.access.idle_timeout == 3600
    error_message = "ALB needs two zones, preserved Host and long-lived connections."
  }
  assert {
    condition     = aws_lb_listener.https.default_action[0].fixed_response[0].status_code == "403" && aws_lb_listener.http.default_action[0].redirect[0].protocol == "HTTPS"
    error_message = "Unassigned HTTPS hosts must be denied and HTTP must redirect."
  }
  assert {
    condition     = aws_acm_certificate.access.domain_name == "*.workshops.example.com" && length(aws_security_group.alb.ingress) == 2
    error_message = "TLS terminates at ACM/ALB; no private key is distributed to learners."
  }
  assert {
    condition     = aws_acm_certificate.access.subject_alternative_names == toset([var.domain]) && aws_route53_record.validation.name == "_test.workshops.example.com" && aws_route53_record.root.name == var.domain && length(aws_lb_listener_rule.portal) == 0
    error_message = "Cover apex and wildcard with one validation CNAME; keep the root closed without an operator portal."
  }
}
run "reject_one_zone" {
  command = plan
  variables { availability_zones = ["us-east-1a"] }
  expect_failures = [var.availability_zones]
}
run "reject_foreign_dns_zone" {
  command = plan
  variables { domain = "workshops.other.example" }
  expect_failures = [aws_acm_certificate.access]
}
run "operator_portal_routes_only_course_path" {
  command = apply
  variables { portal_instance_id = "i-00000000000000009" }
  assert {
    condition     = anytrue([for condition in aws_lb_listener_rule.portal[0].condition : try(condition.host_header[0].values == toset([var.domain]), false)]) && anytrue([for condition in aws_lb_listener_rule.portal[0].condition : try(condition.path_pattern[0].values == toset(["/doris-courses", "/doris-courses/"]), false)]) && length(aws_lb_listener_rule.portal[0].condition) == 2 && aws_lb_target_group_attachment.portal[0].target_id == var.portal_instance_id && output.access.portal.instance_id == var.portal_instance_id
    error_message = "The root course path uses an operator target, while other root paths retain default denial."
  }
}
run "partner_namespace_has_own_apex_and_wildcard" {
  command   = apply
  state_key = "partner"
  variables { domain = "partner-a.workshops.example.com" }
  override_resource {
    target = aws_acm_certificate.access
    values = { arn = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-4000-8000-000000000001", domain_validation_options = [
      { domain_name = "*.partner-a.workshops.example.com", resource_record_name = "_partner.partner-a.workshops.example.com", resource_record_type = "CNAME", resource_record_value = "_partner.acm-validations.aws" },
      { domain_name = "partner-a.workshops.example.com", resource_record_name = "_partner.partner-a.workshops.example.com", resource_record_type = "CNAME", resource_record_value = "_partner.acm-validations.aws" }
    ] }
  }
  assert {
    condition     = aws_acm_certificate.access.domain_name == "*.partner-a.workshops.example.com" && aws_route53_record.validation.name == "_partner.partner-a.workshops.example.com" && aws_route53_record.root.name == var.domain
    error_message = "A partner requires its own apex SAN, wildcard and root alias."
  }
}
run "reject_learner_as_operator_portal" {
  command = plan
  variables { portal_instance_id = "i-00000000000000001" }
  override_data {
    target = data.aws_instance.portal[0]
    values = { subnet_id = "subnet-00000000000000000", vpc_security_group_ids = ["sg-00000000000000000"], tags = { Project = "doris-workshop", Scope = "shared-portal", Owner = "test-owner", Access = "test-access", Event = "some-event" } }
  }
  expect_failures = [aws_lb_target_group.portal]
}
