mock_provider "aws" {
  mock_resource "aws_lb" { defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/example/0123456789abcdef" } }
  mock_resource "aws_acm_certificate_validation" { defaults = { certificate_arn = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-4000-8000-000000000000" } }
  mock_resource "aws_acm_certificate" {
    defaults = { arn = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-4000-8000-000000000000", domain_validation_options = [{ domain_name = "*.workshops.example.com", resource_record_name = "_test.workshops.example.com", resource_record_type = "CNAME", resource_record_value = "_test.acm-validations.aws" }] }
  }
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_data "aws_route53_zone" { defaults = { name = "example.com.", private_zone = false } }
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
