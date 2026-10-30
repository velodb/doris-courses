variable "aws_profile" { type = string }
variable "expected_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "An explicit target account is required."
  }
}
variable "region" { type = string }
variable "build_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.build_id))
    error_message = "Use a unique lowercase build ID of 3 to 32 characters."
  }
}
variable "owner_id" { type = string }
variable "expires_at" { type = string }
variable "state_bucket_name" { type = string }
variable "runtime_bundle_dir" { type = string }
variable "availability_zone" { type = string }
