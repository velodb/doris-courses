variable "aws_profile" {
  type     = string
  nullable = false
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Use an explicit operator profile for the portal host."
  }
}
variable "expected_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Supply the expected operator account ID."
  }
}
variable "region" { type = string }
variable "owner_id" { type = string }
variable "access_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.access_id))
    error_message = "Use the shared access ID, at most 32 characters."
  }
}
variable "vpc_id" { type = string }
variable "subnet_id" { type = string }
variable "portal_security_group_id" { type = string }
variable "alb_security_group_id" { type = string }
variable "ami_id" { type = string }
variable "instance_type" {
  type    = string
  default = "t3.small"
  validation {
    condition     = contains(["t3.small", "t3.medium"], var.instance_type)
    error_message = "Use an x86 t3.small or t3.medium portal; Standard credits avoid surplus credit billing."
  }
}
