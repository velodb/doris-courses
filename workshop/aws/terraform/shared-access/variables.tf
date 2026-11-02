variable "aws_profile" { type = string }
variable "expected_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Supply the expected AWS account ID."
  }
}
variable "region" { type = string }
variable "owner_id" { type = string }
variable "access_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.access_id))
    error_message = "Use a stable access ID, at most 32 characters."
  }
}
variable "domain" {
  type = string
  validation {
    condition     = length(var.domain) <= 190 && can(regex("^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.domain))
    error_message = "Use a lowercase DNS suffix without a wildcard."
  }
}
variable "hosted_zone_id" { type = string }
variable "dns" {
  description = "Optional separate DNS identity. Use one explicit profile or a role assumed from the workload profile."
  type        = object({ account_id = string, profile = optional(string), role_arn = optional(string) })
  default     = null
  validation {
    condition = var.dns == null ? true : (
      can(regex("^[0-9]{12}$", var.dns.account_id)) &&
      ((var.dns.profile != null) != (var.dns.role_arn != null)) &&
      (var.dns.profile == null ? true : length(trimspace(var.dns.profile)) > 0) &&
      (var.dns.role_arn == null ? true : can(regex("^arn:aws:iam::${var.dns.account_id}:role/[A-Za-z0-9_+=,.@/-]+$", var.dns.role_arn)))
    )
    error_message = "Supply a DNS account ID and exactly one nonempty profile or a role ARN in that account."
  }
}
variable "portal_instance_id" {
  type    = string
  default = null
  validation {
    condition     = var.portal_instance_id == null ? true : can(regex("^i-[0-9a-f]{17}$", var.portal_instance_id))
    error_message = "Supply a separately managed operator portal instance, or leave the entry closed."
  }
}
variable "portal_priority" {
  type    = number
  default = 100
  validation {
    condition     = var.portal_priority >= 1 && var.portal_priority <= 50000 && floor(var.portal_priority) == var.portal_priority
    error_message = "Reserve a unique integer listener priority for the shared entry."
  }
}
variable "availability_zones" {
  type = list(string)
  validation {
    condition     = length(var.availability_zones) >= 2 && length(var.availability_zones) <= 3 && length(distinct(var.availability_zones)) == length(var.availability_zones) && alltrue([for zone in var.availability_zones : can(regex("^${var.region}[a-z]$", zone))])
    error_message = "Select two or three distinct standard Availability Zones in this Region."
  }
}
variable "vpc_cidr" {
  type    = string
  default = "10.194.0.0/20"
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && try(tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 23, false)
    error_message = "Use an IPv4 VPC CIDR from /16 to /23; each ALB subnet must be at least /27."
  }
}
