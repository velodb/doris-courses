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
variable "availability_zone" {
  type     = string
  default  = null
  nullable = true
  validation {
    condition     = var.availability_zone == null || can(regex("^${var.region}[a-z]$", var.availability_zone))
    error_message = "The availability zone must belong to the selected Region."
  }
}
variable "owner_id" { type = string }
variable "state_bucket_name" { type = string }
variable "event_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.event_id))
    error_message = "Use a stable event ID of at most 32 lowercase letters, digits or hyphens."
  }
}
variable "learner_ids" {
  type    = list(string)
  default = ["s001"]
  validation {
    condition = (
      length(var.learner_ids) >= 1 && length(var.learner_ids) <= 5 &&
      length(distinct(var.learner_ids)) == length(var.learner_ids) &&
      alltrue([for id in var.learner_ids : can(regex("^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", id)) && length(id) <= 32 && !startswith(id, "spare-")])
    )
    error_message = "Use one to five unique stable learner IDs, without the reserved spare- prefix."
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

variable "image_mode" {
  type    = string
  default = "base"
  validation {
    condition     = contains(["base", "prebuilt"], var.image_mode)
    error_message = "Choose an explicit base or prebuilt image mode."
  }
}
variable "ami_owner_account_id" {
  type    = string
  default = "099720109477"
}
variable "image_release_ref" {
  type    = string
  default = ""
}
variable "image_release_sha256" {
  type    = string
  default = ""
}
variable "image_manifest_sha256" {
  type    = string
  default = ""
}
variable "image_runtime_sha256" {
  type    = string
  default = ""
}
variable "browser_routes" {
  type    = map(map(string))
  default = {}
}
variable "gateway_security_group_id" {
  type     = string
  default  = null
  nullable = true
  validation {
    condition     = var.gateway_security_group_id == null || var.vpc_id != null
    error_message = "A shared gateway security group requires the shared access VPC and subnet."
  }
}
