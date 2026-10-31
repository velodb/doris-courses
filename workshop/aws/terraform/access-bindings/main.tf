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
    tags = { Project = "doris-workshop", Event = var.event_id, Owner = var.owner_id, Scope = "activity-access", ExpiresAt = var.expires_at }
  }
}

resource "aws_cognito_user_pool" "activity" {
  name = "doris-workshop-${var.event_id}"
  admin_create_user_config { allow_admin_create_user_only = true }
  username_configuration { case_sensitive = false }
  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 1
  }
  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration { enabled = true }
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }
}
resource "aws_cognito_user_pool_client" "alb" {
  name                                 = "workshop-alb"
  user_pool_id                         = aws_cognito_user_pool.activity.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = sort([for route in var.routes : "https://${route.hostname}/oauth2/idpresponse"])
  logout_urls                          = sort([for route in var.routes : "https://${route.hostname}/_workshop/logged-out"])
  access_token_validity                = 15
  id_token_validity                    = 15
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
  enable_token_revocation       = true
  prevent_user_existence_errors = "ENABLED"
}
resource "aws_cognito_user_pool_domain" "activity" {
  domain       = "dws-${var.event_id}-${var.expected_account_id}"
  user_pool_id = aws_cognito_user_pool.activity.id
}

resource "aws_lb_target_group" "learner" {
  for_each             = var.learners
  name_prefix          = "dws-"
  vpc_id               = var.access.vpc_id
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "instance"
  deregistration_delay = 10
  health_check {
    path                = "/healthz"
    port                = "traffic-port"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
  lifecycle { create_before_destroy = true }
}
resource "aws_lb_target_group_attachment" "learner" {
  for_each         = var.learners
  target_group_arn = aws_lb_target_group.learner[each.key].arn
  target_id        = each.value
  port             = 8080
}
resource "aws_route53_record" "learner" {
  for_each = var.routes
  zone_id  = var.access.hosted_zone_id
  name     = each.value.hostname
  type     = "A"
  alias {
    name                   = var.access.alb_dns_name
    zone_id                = var.access.alb_zone_id
    evaluate_target_health = true
  }
}

locals {
  rules = merge(
    { for key, route in var.routes : "${key}-logout" => merge(route, { kind = "logout", priority = route.priority }) },
    { for key, route in var.routes : "${key}-api" => merge(route, { kind = "api", priority = route.priority + 1 }) },
    { for key, route in var.routes : "${key}-page" => merge(route, { kind = "page", priority = route.priority + 2 }) }
  )
}
resource "aws_lb_listener_rule" "learner" {
  for_each     = local.rules
  listener_arn = var.access.https_listener_arn
  priority     = each.value.priority
  condition {
    host_header { values = [each.value.hostname] }
  }
  dynamic "condition" {
    for_each = each.value.kind == "page" ? [] : [1]
    content {
      path_pattern { values = each.value.kind == "logout" ? ["/_workshop/logged-out"] : ["/api/*", "/terminals/*"] }
    }
  }
  dynamic "action" {
    for_each = each.value.kind == "logout" ? [] : [1]
    content {
      type  = "authenticate-cognito"
      order = 1
      authenticate_cognito {
        user_pool_arn              = aws_cognito_user_pool.activity.arn
        user_pool_client_id        = aws_cognito_user_pool_client.alb.id
        user_pool_domain           = aws_cognito_user_pool_domain.activity.domain
        scope                      = "openid"
        session_timeout            = 3600
        session_cookie_name        = "DorisAuth${substr(sha256(each.value.hostname), 0, 20)}"
        on_unauthenticated_request = each.value.kind == "api" ? "deny" : "authenticate"
      }
    }
  }
  dynamic "action" {
    for_each = each.value.kind == "logout" ? [] : [1]
    content {
      type             = "forward"
      order            = 2
      target_group_arn = aws_lb_target_group.learner[each.value.learner_id].arn
    }
  }
  dynamic "action" {
    for_each = each.value.kind == "logout" ? [1] : []
    content {
      type = "fixed-response"
      fixed_response {
        content_type = "text/plain"
        status_code  = "200"
        message_body = "Signed out of this service. Close other course tabs."
      }
    }
  }
  lifecycle {
    precondition {
      condition     = endswith(each.value.hostname, ".${var.access.domain}") && length(split(".", trimsuffix(each.value.hostname, ".${var.access.domain}"))) == 1 && contains(keys(var.learners), each.value.learner_id)
      error_message = "Every hostname must be one certificate-covered label and assigned to an existing learner."
    }
  }
}

output "identity" {
  value = {
    event_id     = var.event_id, user_pool_id = aws_cognito_user_pool.activity.id,
    issuer       = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.activity.id}",
    client_id    = aws_cognito_user_pool_client.alb.id,
    login_origin = "https://${aws_cognito_user_pool_domain.activity.domain}.auth.${var.region}.amazoncognito.com"
  }
}
