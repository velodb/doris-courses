mock_provider "aws" {
  mock_resource "aws_cognito_user_pool" {
    defaults = { id = "us-east-1_test", arn = "arn:aws:cognito-idp:us-east-1:000000000000:userpool/us-east-1_test" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/test/0123456789abcdef" }
  }
}
mock_provider "aws" {
  alias = "dns"
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_resource "aws_route53_record" { defaults = { fqdn = "dns-provider.example.com" } }
}
variables {
  aws_profile         = "offline-test"
  expected_account_id = "000000000000"
  region              = "us-east-1"
  owner_id            = "test-owner"
  expires_at          = "2099-01-01T00:00:00Z"
  event_id            = "test-event"
  access = {
    vpc_id             = "vpc-00000000000000000"
    domain             = "workshops.example.com"
    hosted_zone_id     = "ZEXAMPLE"
    alb_dns_name       = "test.us-east-1.elb.amazonaws.com"
    alb_zone_id        = "ZEXAMPLEALB"
    https_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/test/0123456789abcdef/0123456789abcdef"
    portal = {
      instance_id       = "i-00000000000000009"
      target_group_arn  = "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/portal/0123456789abcdef"
      listener_priority = 100
    }
  }
  learners = { alice = "i-00000000000000001", bob = "i-00000000000000002" }
  routes = {
    alice = { learner_id = "alice", hostname = "test-event-alice-jupyter.workshops.example.com", priority = 1000 }
    bob   = { learner_id = "bob", hostname = "test-event-bob-jupyter.workshops.example.com", priority = 1003 }
  }
}
run "separate_dns_account_owns_only_activity_records" {
  command   = apply
  state_key = "separate-dns"
  variables {
    dns = { account_id = "111111111111", profile = "dns-operator" }
    access = {
      vpc_id             = "vpc-00000000000000000", domain = "workshops.example.com", hosted_zone_id = "ZEXAMPLE", dns_account_id = "111111111111",
      alb_dns_name       = "test.us-east-1.elb.amazonaws.com", alb_zone_id = "ZEXAMPLEALB",
      https_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/test/0123456789abcdef/0123456789abcdef"
    }
  }
  override_data {
    target = data.aws_caller_identity.dns
    values = { account_id = "111111111111" }
  }
  assert {
    condition     = aws_route53_record.learner["alice"].fqdn == "dns-provider.example.com" && startswith(aws_cognito_user_pool.activity.arn, "arn:aws:cognito-idp:us-east-1:000000000000:")
    error_message = "Activity DNS uses the DNS account while Cognito remains in the workload account."
  }
}
run "reject_unexpected_dns_identity" {
  command = plan
  override_data {
    target = data.aws_caller_identity.dns
    values = { account_id = "222222222222" }
  }
  expect_failures = [data.aws_caller_identity.dns]
}
run "reject_cross_account_access_without_dns_credentials" {
  command = plan
  variables {
    access = {
      vpc_id             = "vpc-00000000000000000", domain = "workshops.example.com", hosted_zone_id = "ZEXAMPLE", dns_account_id = "111111111111",
      alb_dns_name       = "test.us-east-1.elb.amazonaws.com", alb_zone_id = "ZEXAMPLEALB",
      https_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/test/0123456789abcdef/0123456789abcdef"
    }
  }
  expect_failures = [var.dns]
}
run "reject_separate_dns_in_task_role_runner" {
  command = plan
  variables {
    aws_profile     = null
    credential_mode = "task-role"
    dns             = { account_id = "000000000000", profile = "dns-operator" }
  }
  expect_failures = [var.dns]
}
run "owned_cognito_and_authenticated_routes" {
  command = apply
  assert {
    condition     = aws_cognito_user_pool.activity.admin_create_user_config[0].allow_admin_create_user_only && aws_cognito_user_pool_client.alb.generate_secret && aws_cognito_user_pool_client.alb.enable_token_revocation
    error_message = "Only admins create users; the ALB confidential client requires a secret and token revocation."
  }
  assert {
    condition     = length(aws_lb_target_group.learner) == 2 && aws_lb_target_group_attachment.learner["bob"].target_id == "i-00000000000000002" && length(aws_lb_listener_rule.learner) == 6
    error_message = "Each learner has a separate target and each service has three explicit rules."
  }
  assert {
    condition     = length([for action in aws_lb_listener_rule.learner["alice-page"].action : action if action.type == "authenticate-cognito" && action.order == 1]) == 1 && length([for action in aws_lb_listener_rule.learner["alice-api"].action : action if try(action.authenticate_cognito[0].on_unauthenticated_request, "") == "deny"]) == 1
    error_message = "Both pages and APIs authenticate before forwarding; anonymous APIs must not return a login page."
  }
  assert {
    condition     = aws_lb_listener_rule.learner["alice-logout"].action[0].type == "fixed-response" && length(aws_cognito_user_pool_client.alb.callback_urls) == 2
    error_message = "Logout must not immediately log in again; only exact HTTPS callbacks are registered."
  }
}
run "reject_cross_certificate_hostname" {
  command = plan
  variables { routes = { alice = { learner_id = "alice", hostname = "nested.alice.workshops.example.com", priority = 1000 } } }
  expect_failures = [aws_lb_listener_rule.learner]
}
run "reject_unassigned_learner" {
  command = plan
  variables { routes = { charlie = { learner_id = "charlie", hostname = "charlie.workshops.example.com", priority = 1000 } } }
  expect_failures = [aws_lb_listener_rule.learner]
}
run "reject_overlapping_rule_priorities" {
  command = plan
  variables {
    routes = {
      alice = { learner_id = "alice", hostname = "alice.workshops.example.com", priority = 1000 }
      bob   = { learner_id = "bob", hostname = "bob.workshops.example.com", priority = 1002 }
    }
  }
  expect_failures = [var.routes]
}
run "reject_wrong_listener_account" {
  command = plan
  variables { expected_account_id = "111111111111" }
  expect_failures = [var.access]
}
run "task_role_has_no_operator_profile" {
  command = plan
  variables {
    aws_profile     = null
    credential_mode = "task-role"
  }
}
run "reject_accidental_role_fallback" {
  command = plan
  variables { aws_profile = null }
  expect_failures = [var.aws_profile]
}
run "activity_entry_authenticates_on_operator_target" {
  command = apply
  variables { portal = { hostname = "test-event-entry.workshops.example.com", priority = 1006 } }
  assert {
    condition     = length(aws_lb_listener_rule.learner) == 9 && length(aws_lb_target_group.learner) == 2 && length(aws_cognito_user_pool_client.alb.callback_urls) == 3 && aws_route53_record.learner["portal"].name == var.portal.hostname
    error_message = "The activity entry has its own callback and three rules, and adds no learner target."
  }
  assert {
    condition     = anytrue([for action in aws_lb_listener_rule.learner["portal-page"].action : try(action.target_group_arn == var.access.portal.target_group_arn && action.order == 2, false)]) && anytrue([for action in aws_lb_listener_rule.learner["portal-page"].action : action.type == "authenticate-cognito" && action.order == 1])
    error_message = "Authenticate the entry with this activity's pool before sending it to the operator portal."
  }
  assert {
    condition     = length(aws_lb_listener_rule.learner["portal-logout"].action) == 1 && aws_lb_listener_rule.learner["portal-logout"].action[0].type == "forward" && aws_lb_listener_rule.learner["portal-logout"].action[0].target_group_arn == var.access.portal.target_group_arn
    error_message = "Only the exact portal logout landing page forwards without authentication; learner service logout remains a fixed response."
  }
}
run "reject_portal_priority_overlap" {
  command = plan
  variables { portal = { hostname = "test-event-entry.workshops.example.com", priority = 1004 } }
  expect_failures = [var.portal]
}
run "reject_portal_from_other_namespace" {
  command = plan
  variables { portal = { hostname = "test-event-entry.other.example.com", priority = 1006 } }
  expect_failures = [var.portal]
}
run "reject_assigning_portal_host_to_learner" {
  command = plan
  variables { learners = { alice = "i-00000000000000009", bob = "i-00000000000000002" } }
  expect_failures = [aws_lb_listener_rule.learner]
}
run "reject_using_shared_entry_priority" {
  command = plan
  variables { routes = { alice = { learner_id = "alice", hostname = "alice.workshops.example.com", priority = 100 } } }
  expect_failures = [aws_lb_listener_rule.learner]
}
