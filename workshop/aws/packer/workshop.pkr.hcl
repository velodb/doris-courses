packer {
  required_version = "= 1.16.2"
  required_plugins {
    amazon = {
      version = "= 1.8.3"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "aws_profile" { type = string }
variable "expected_account_id" { type = string }
variable "region" { type = string }
variable "base_ami_id" { type = string }
variable "build_id" { type = string }
variable "course_ref" { type = string }
variable "owner_id" { type = string }
variable "expires_at" { type = string }
variable "retain_until" { type = string }
variable "subnet_id" { type = string }
variable "security_group_id" { type = string }
variable "instance_profile" { type = string }
variable "run_dir" { type = string }
variable "python" { type = string }

locals {
  tags = {
    Project = "doris-workshop", Scope = "image-build", ImageBuild = var.build_id
    Owner   = var.owner_id, CourseRef = var.course_ref, ExpiresAt = var.expires_at
  }
}

source "amazon-ebs" "workshop" {
  profile                     = var.aws_profile
  region                      = var.region
  source_ami                  = var.base_ami_id
  ami_name                    = "doris-workshop-${var.build_id}"
  ami_description             = "rta-main candidate; independent launch validation required"
  instance_type               = "m7i.2xlarge"
  subnet_id                   = var.subnet_id
  security_group_id           = var.security_group_id
  associate_public_ip_address = true
  iam_instance_profile        = var.instance_profile
  communicator                = "none"
  user_data                   = templatefile("${abspath(path.root)}/build-expiry.sh.tftpl", { expires_at = var.expires_at })
  # No inbound SSH rule, SSH session or provisioner upload is needed.
  # The Amazon plugin may still create a tagged temporary EC2 key pair.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_type           = "gp3"
    volume_size           = 200
    encrypted             = true
    delete_on_termination = true
  }
  ami_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_type           = "gp3"
    volume_size           = 200
    delete_on_termination = true
  }
  run_tags        = local.tags
  run_volume_tags = local.tags
  tags            = merge(local.tags, { Scope = "image", ImageStatus = "candidate", ExpiresAt = var.retain_until })
  snapshot_tags   = merge(local.tags, { Scope = "image", ImageStatus = "candidate", ExpiresAt = var.retain_until })
  # No sharing permissions and no force-deregister of an existing image.
  aws_polling {
    delay_seconds = 10
    max_attempts  = 360
  }
}

build {
  sources = ["source.amazon-ebs.workshop"]
  provisioner "shell-local" {
    environment_vars = ["IMAGE_RUN_DIR=${var.run_dir}", "IMAGE_PYTHON=${var.python}", "IMAGE_TOOL=${abspath("${path.root}/../../../tools/workshop_image.py")}"]
    inline           = ["\"$IMAGE_PYTHON\" \"$IMAGE_TOOL\" provision --run-dir \"$IMAGE_RUN_DIR\""]
    timeout          = "150m"
  }
  post-processor "manifest" {
    output      = "${var.run_dir}/packer-manifest.json"
    strip_path  = true
    custom_data = { course_ref = var.course_ref, build_id = var.build_id, image_status = "candidate" }
  }
}
