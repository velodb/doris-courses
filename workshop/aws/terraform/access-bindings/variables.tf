variable "aws_profile" { type = string }
variable "expected_account_id" { type = string }
variable "region" { type = string }
variable "owner_id" { type = string }
variable "event_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.event_id))
    error_message = "Use a stable event ID of at most 32 characters."
  }
}
variable "access" {
  type = object({ vpc_id = string, domain = string, hosted_zone_id = string, alb_dns_name = string, alb_zone_id = string, https_listener_arn = string })
  validation {
    condition     = startswith(var.access.https_listener_arn, "arn:aws:elasticloadbalancing:${var.region}:${var.expected_account_id}:listener/app/")
    error_message = "The shared HTTPS listener must belong to this account and Region."
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
    condition     = length(var.routes) > 0 && length(var.routes) <= 33 && length(distinct([for route in var.routes : route.hostname])) == length(var.routes) && length(distinct(flatten([for route in var.routes : [route.priority, route.priority + 1, route.priority + 2]]))) == length(var.routes) * 3 && alltrue([for route in var.routes : can(regex("^[a-z0-9][a-z0-9.-]+$", route.hostname)) && length(split(".", route.hostname)[0]) <= 63 && route.priority >= 1 && route.priority <= 49998 && floor(route.priority) == route.priority])
    error_message = "Use unique certificate-covered hosts and three reserved integer priorities per host; no more than 100 total rules."
  }
}
