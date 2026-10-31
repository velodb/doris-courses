mock_provider "aws" {
  mock_resource "aws_cognito_user_pool" {
    defaults = { id = "us-east-1_test", arn = "arn:aws:cognito-idp:us-east-1:000000000000:userpool/us-east-1_test" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/test/0123456789abcdef" }
  }
}
variables {
  aws_profile         = "offline-test"
  expected_account_id = "000000000000"
  region              = "us-east-1"
  owner_id            = "test-owner"
  event_id            = "test-event"
  access = {
    vpc_id             = "vpc-00000000000000000"
    domain             = "workshops.example.com"
    hosted_zone_id     = "ZEXAMPLE"
    alb_dns_name       = "test.us-east-1.elb.amazonaws.com"
    alb_zone_id        = "ZEXAMPLEALB"
    https_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/test/0123456789abcdef/0123456789abcdef"
  }
  learners = { alice = "i-00000000000000001", bob = "i-00000000000000002" }
  routes = {
    alice = { learner_id = "alice", hostname = "test-event-alice-jupyter.workshops.example.com", priority = 1000 }
    bob   = { learner_id = "bob", hostname = "test-event-bob-jupyter.workshops.example.com", priority = 1003 }
  }
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
