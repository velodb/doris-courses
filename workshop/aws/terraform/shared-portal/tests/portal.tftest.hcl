mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_data "aws_subnet" { defaults = { vpc_id = "vpc-00000000000000000" } }
  mock_data "aws_vpc" { defaults = { tags = { Project = "doris-workshop", Owner = "operator", Access = "test" } } }
  mock_data "aws_security_group" { defaults = { vpc_id = "vpc-00000000000000000" } }
  mock_data "aws_vpc_security_group_rules" { defaults = { ids = ["sgr-00000000000000001"] } }
  mock_data "aws_vpc_security_group_rule" { defaults = {
    is_egress                    = false, ip_protocol = "tcp", from_port = 8080, to_port = 8080,
    referenced_security_group_id = "sg-00000000000000001", cidr_ipv4 = "", cidr_ipv6 = "", prefix_list_id = ""
  } }
  mock_data "aws_ami" { defaults = {
    id                  = "ami-00000000000000000", owner_id = "099720109477", architecture = "x86_64", root_device_type = "ebs",
    virtualization_type = "hvm", name = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-20260101"
  } }
}
variables {
  aws_profile              = "offline-test"
  expected_account_id      = "000000000000"
  region                   = "us-east-1"
  owner_id                 = "operator"
  access_id                = "test"
  vpc_id                   = "vpc-00000000000000000"
  subnet_id                = "subnet-00000000000000000"
  portal_security_group_id = "sg-00000000000000000"
  alb_security_group_id    = "sg-00000000000000001"
  ami_id                   = "ami-00000000000000000"
}
run "separate_operator_host_is_hardened_and_has_only_ssm" {
  command = apply
  assert {
    condition     = aws_instance.portal.metadata_options[0].http_tokens == "required" && aws_instance.portal.metadata_options[0].http_put_response_hop_limit == 1 && aws_instance.portal.root_block_device[0].encrypted && aws_instance.portal.credit_specification[0].cpu_credits == "standard"
    error_message = "The portal needs IMDSv2, encrypted root and bounded CPU credits."
  }
  assert {
    condition     = aws_instance.portal.vpc_security_group_ids == toset([var.portal_security_group_id]) && aws_iam_role_policy_attachment.ssm.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" && !contains(keys(aws_instance.portal.tags), "Event")
    error_message = "Use only the selected portal group and SSM role, with no activity Event."
  }
}
run "reject_foreign_network" {
  command = plan
  variables { vpc_id = "vpc-11111111111111111" }
  expect_failures = [aws_instance.portal]
}
run "reject_direct_public_ingress" {
  command = plan
  override_data {
    target = data.aws_vpc_security_group_rule.portal["sgr-00000000000000001"]
    values = { referenced_security_group_id = "", cidr_ipv4 = "0.0.0.0/0" }
  }
  expect_failures = [aws_instance.portal]
}
run "reject_unverified_ami" {
  command = plan
  override_data {
    target = data.aws_ami.base
    values = { owner_id = "111111111111" }
  }
  expect_failures = [aws_instance.portal]
}
