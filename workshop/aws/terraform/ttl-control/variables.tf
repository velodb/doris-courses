variable "aws_profile" { type = string }
variable "expected_account_id" { type = string }
variable "region" { type = string }
variable "owner_id" { type = string }
variable "event_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.event_id))
    error_message = "Use the bound stable activity ID."
  }
}
variable "binding_sha256" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.binding_sha256))
    error_message = "Pin the reviewed TTL binding hash."
  }
}
variable "revision" {
  type = number
  validation {
    condition     = var.revision >= 1 && floor(var.revision) == var.revision
    error_message = "Use a positive integer binding revision."
  }
}
variable "expires_at" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at)) && can(formatdate("YYYY-MM-DD hh:mm:ss", var.expires_at))
    error_message = "Supply canonical UTC expiry, including seconds."
  }
}
variable "runner_image" {
  type = string
  validation {
    condition     = can(regex("^${var.expected_account_id}\\.dkr\\.ecr\\.${var.region}\\.amazonaws\\.com/[a-z0-9/_-]+@sha256:[0-9a-f]{64}$", var.runner_image))
    error_message = "Use an immutable private ECR image digest in this account/Region."
  }
}
variable "control_vpc_id" { type = string }
variable "control_subnet_ids" {
  type = list(string)
  validation {
    condition     = length(var.control_subnet_ids) > 0 && length(distinct(var.control_subnet_ids)) == length(var.control_subnet_ids)
    error_message = "Supply dedicated operator subnet IDs with HTTPS egress."
  }
}
variable "activity_vpc_ids" { type = set(string) }
variable "task_timeout_seconds" {
  type    = number
  default = 3600
  validation {
    condition     = var.task_timeout_seconds >= 60 && var.task_timeout_seconds <= 7200 && floor(var.task_timeout_seconds) == var.task_timeout_seconds
    error_message = "Bound each task to 60..7200 seconds."
  }
}
variable "enabled" {
  type    = bool
  default = false
}
variable "acceptance" {
  type    = object({ shared_cleanup = bool, runner_image = bool, operator_fencing = bool, policies = bool })
  default = { shared_cleanup = false, runner_image = false, operator_fencing = false, policies = false }
}
variable "policy_documents" {
  description = "Four independently generated/reviewed identity policies; never attach unreviewed Autopilot output."
  type        = map(object({ json = string, sha256 = string }))
  validation {
    condition = toset(keys(var.policy_documents)) == toset(["scheduler", "workflow", "execution", "runner"]) && alltrue([
      for policy in var.policy_documents : sha256(policy.json) == policy.sha256 && can(jsondecode(policy.json).Statement) && try(jsondecode(policy.json).Version == "2012-10-17", false)
    ])
    error_message = "Supply four reviewed, hash-bound identity policies."
  }
}
variable "state_bucket_name" { type = string }
variable "binding_object_version" {
  type = string
  validation {
    condition     = length(var.binding_object_version) > 0 && var.binding_object_version != "null"
    error_message = "Pin a versioned private binding object."
  }
}
variable "approved_resource_arns" {
  type = map(set(string))
  validation {
    condition = toset(keys(var.approved_resource_arns)) == toset(["scheduler", "workflow", "execution", "runner"]) && alltrue(flatten([
      for role, resources in var.approved_resource_arns : [for arn in resources : startswith(arn, "arn:aws:") &&
        (!strcontains(arn, "*") || role == "workflow" && arn == "arn:aws:ecs:${var.region}:${var.expected_account_id}:task/doris-ttl-${var.event_id}/*") && !strcontains(arn, "?") &&
      try(contains(["", var.region], split(":", arn)[3]) && contains(["", var.expected_account_id], split(":", arn)[4]), false)]
    ]))
    error_message = "Approve exact resource ARNs for each role."
  }
}
variable "approved_dns_names" {
  type    = set(string)
  default = []
  validation {
    condition     = alltrue([for name in var.approved_dns_names : startswith(name, "${var.event_id}-") && !strcontains(name, "*") && !strcontains(name, "?")])
    error_message = "Approve exact activity service/entry DNS names."
  }
}
