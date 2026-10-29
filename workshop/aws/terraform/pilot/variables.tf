variable "aws_profile" {
  type = string
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Select an explicit local AWS CLI profile."
  }
}
variable "expected_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Specify the expected AWS account ID."
  }
}
variable "region" { type = string }
variable "owner_id" { type = string }
variable "state_bucket_name" { type = string }
variable "event_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.event_id))
    error_message = "Use a stable event ID of at most 32 lowercase letters, digits or hyphens."
  }
}
variable "learner_id" {
  type    = string
  default = "s001"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.learner_id))
    error_message = "Use a stable learner ID."
  }
}
variable "ami_id" { type = string }
variable "instance_type" {
  type    = string
  default = "r7i.xlarge"
}
variable "disk_gib" {
  type    = number
  default = 100
  validation {
    condition     = var.disk_gib >= 20 && floor(var.disk_gib) == var.disk_gib
    error_message = "Use an integer disk capacity of at least 20 GiB; actual course sizing is checked before apply."
  }
}
variable "expires_at" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at)) && can(formatdate("YYYY-MM-DD hh:mm:ss", var.expires_at))
    error_message = "Specify a valid UTC expiry with seconds, ending in Z."
  }
}
variable "runtime_bundle_dir" { type = string }
variable "vpc_id" {
  type     = string
  default  = null
  nullable = true
}
variable "subnet_id" {
  type     = string
  default  = null
  nullable = true
  validation {
    condition     = (var.vpc_id == null) == (var.subnet_id == null)
    error_message = "Provide both VPC and subnet IDs, or neither to create an activity-owned network."
  }
}
