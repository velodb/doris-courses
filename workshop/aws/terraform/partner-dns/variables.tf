variable "aws_profile" {
  type = string
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Use an explicit partner operator profile."
  }
}
variable "expected_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Supply the partner's twelve-digit AWS account ID."
  }
}
variable "region" { type = string }
variable "owner_id" { type = string }
variable "partner_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.partner_id))
    error_message = "Use a single lowercase partner label, at most 32 characters."
  }
}
variable "parent_domain" {
  type = string
  validation {
    condition     = length(var.parent_domain) <= 190 && can(regex("^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.parent_domain))
    error_message = "Use the approved lowercase parent domain, without a wildcard or trailing dot."
  }
}
