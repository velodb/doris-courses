variable "aws_profile" {
  type = string
  validation {
    condition     = var.credential_mode == "task-role" ? var.aws_profile == null : (var.aws_profile == null ? false : length(trimspace(var.aws_profile)) > 0)
    error_message = "Select an operator profile, or null only with explicit task-role mode."
  }
}
variable "credential_mode" {
  type    = string
  default = "profile"
  validation {
    condition     = contains(["profile", "task-role"], var.credential_mode)
    error_message = "Use explicit profile or task-role credentials."
  }
}
variable "dns" {
  description = "Optional DNS identity, matching the shared access DNS account. Cross-account task-role cleanup is not yet supported."
  type        = object({ account_id = string, profile = optional(string), role_arn = optional(string) })
  default     = null
  validation {
    condition = var.dns == null ? coalesce(var.access.dns_account_id, var.expected_account_id) == var.expected_account_id : (
      var.credential_mode == "profile" &&
      can(regex("^[0-9]{12}$", var.dns.account_id)) &&
      var.dns.account_id == coalesce(var.access.dns_account_id, var.expected_account_id) &&
      ((var.dns.profile != null) != (var.dns.role_arn != null)) &&
      (var.dns.profile == null ? true : length(trimspace(var.dns.profile)) > 0) &&
      (var.dns.role_arn == null ? true : can(regex("^arn:aws:iam::${var.dns.account_id}:role/[A-Za-z0-9_+=,.@/-]+$", var.dns.role_arn)))
    )
    error_message = "Supply exactly one DNS profile or role in the shared DNS account; separate DNS credentials are operator-only."
  }
}
variable "expected_account_id" { type = string }
variable "region" { type = string }
variable "owner_id" { type = string }
variable "expires_at" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at)) && can(formatdate("YYYY-MM-DD hh:mm:ss", var.expires_at))
    error_message = "Supply the activity's UTC expiry, including seconds. Tags do not perform cleanup."
  }
}
variable "event_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.event_id))
    error_message = "Use a stable event ID of at most 32 characters."
  }
}
variable "access" {
  type = object({ vpc_id = string, domain = string, hosted_zone_id = string, dns_account_id = optional(string), alb_dns_name = string, alb_zone_id = string, https_listener_arn = string, portal = optional(object({ instance_id = string, target_group_arn = string, listener_priority = number })) })
  validation {
    condition     = startswith(var.access.https_listener_arn, "arn:aws:elasticloadbalancing:${var.region}:${var.expected_account_id}:listener/app/")
    error_message = "The shared HTTPS listener must belong to this account and Region."
  }
  validation {
    condition     = var.access.dns_account_id == null ? true : can(regex("^[0-9]{12}$", var.access.dns_account_id))
    error_message = "The shared DNS account must be a twelve-digit account ID."
  }
}
variable "portal" {
  type    = object({ hostname = string, priority = number })
  default = null
  validation {
    condition = var.portal == null ? true : (
      var.access.portal != null && var.portal.hostname == "${var.event_id}-entry.${var.access.domain}" &&
      var.portal.priority >= 1 && var.portal.priority <= 49998 && floor(var.portal.priority) == var.portal.priority &&
      !contains(flatten([for route in var.routes : [route.priority, route.priority + 1, route.priority + 2]]), var.portal.priority) &&
      !contains(flatten([for route in var.routes : [route.priority, route.priority + 1, route.priority + 2]]), var.portal.priority + 1) &&
      !contains(flatten([for route in var.routes : [route.priority, route.priority + 1, route.priority + 2]]), var.portal.priority + 2)
    )
    error_message = "The activity entry needs a shared operator portal and three non-overlapping priorities."
  }
}
variable "learners" {
  type = map(string)
  validation {
    condition     = length(var.learners) > 0 && length(var.learners) <= 100 && alltrue([for id, instance in var.learners : can(regex("^[a-z][a-z0-9-]{0,31}$", id)) && can(regex("^i-[0-9a-f]{17}$", instance))]) && length(distinct(values(var.learners))) == length(var.learners)
    error_message = "Use one distinct EC2 per stable learner, up to the ALB target-group limit."
  }
}
variable "routes" {
  type = map(object({ learner_id = string, hostname = string, priority = number }))
  validation {
    condition     = !contains(keys(var.routes), "portal") && length(var.routes) > 0 && length(var.routes) <= 33 && length(distinct([for route in var.routes : route.hostname])) == length(var.routes) && length(distinct(flatten([for route in var.routes : [route.priority, route.priority + 1, route.priority + 2]]))) == length(var.routes) * 3 && alltrue([for route in var.routes : can(regex("^[a-z0-9][a-z0-9.-]+$", route.hostname)) && length(split(".", route.hostname)[0]) <= 63 && route.priority >= 1 && route.priority <= 49998 && floor(route.priority) == route.priority])
    error_message = "Use unique certificate-covered hosts and three reserved integer priorities per host; reserve the portal key and stay within 100 total rules."
  }
}
