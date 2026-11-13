variable "aws_profile" {
  type = string
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Select an explicit local AWS profile."
  }
}
variable "expected_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Use an exact account ID."
  }
}
variable "source_account_id" { type = string }
variable "recipient_account_id" {
  type = string
  validation {
    condition     = var.recipient_account_id == "" ? !var.share_enabled && var.mode == "export" : can(regex("^[0-9]{12}$", var.recipient_account_id))
    error_message = "Use an exact recipient account ID."
  }
}
variable "recipient_principal_arn" {
  type = string
  validation {
    condition     = var.recipient_principal_arn == "" ? !var.share_enabled && var.recipient_account_id == "" && var.mode == "export" : can(regex("^arn:aws:iam::[0-9]{12}:(role|user)/[A-Za-z0-9+=,.@_/-]+$", var.recipient_principal_arn)) && try(split(":", var.recipient_principal_arn)[4], "") == var.recipient_account_id
    error_message = "Select one IAM user or role in the recipient account; no wildcard or root principal."
  }
}
variable "region" {
  type = string
  validation {
    condition     = can(regex("^(us|eu|ap|ca|sa|me|af|il|mx)-[a-z]+-[0-9]+$", var.region))
    error_message = "Use a commercial AWS Region; both sides use the same Region."
  }
}
variable "mode" {
  type = string
  validation {
    condition     = contains(["export", "import"], var.mode)
    error_message = "Choose export or import."
  }
}
variable "share_enabled" { type = bool }
variable "revoked" {
  type    = bool
  default = false
}
variable "source_ami_id" {
  type = string
  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.source_ami_id))
    error_message = "Pin the exact source AMI ID."
  }
}
variable "transfer_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.transfer_id))
    error_message = "Use a unique lowercase transfer ID."
  }
}
variable "course_ref" { type = string }
variable "manifest_sha256" { type = string }
variable "source_release_sha256" { type = string }
variable "retain_until" {
  type = string
  validation {
    condition     = can(timecmp(var.retain_until, var.retain_until))
    error_message = "Record an explicit RFC3339 retention review time; tags do not delete resources."
  }
}
