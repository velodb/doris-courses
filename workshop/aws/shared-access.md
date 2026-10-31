# Shared HTTPS access

The `terraform/shared-access` module owns a shared VPC, two or three public
subnets in distinct Availability Zones, ALB, ACM certificate and its DNS validation
record. Supply a delegated public Route 53 zone or an existing public zone you
control. The module does not register a domain or change its delegation.

The HTTPS listener initially returns 403. Activity bindings add authenticated
routes separately. HTTP redirects to HTTPS. ACM retains the TLS private key;
learner hosts receive no certificate private keys. The gateway security group
accepts only ALB traffic on TCP 8080. No SQL, Jupyter, container or SSH port is
opened to the Internet.

## States and deployment order

Use a separate S3 backend key, for example
`aws-workshop/shared-access/us-east-1/training/terraform.tfstate`. Keep the existing
foundation state. Shared infrastructure has its own owner and remains billable
until explicitly destroyed; an activity's expiry does not remove it.

1. Supply `aws_profile`, `expected_account_id`, `region`, `owner_id`, `access_id`,
   `domain`, `hosted_zone_id` and at least two `availability_zones` as private
   Terraform variables. Inspect account identity, DNS ownership, free subnet
   addresses, ALB/target/rule quotas and prices before planning real resources.
2. Initialize the shared module with its explicit backend, save a plan, review
   that plan and apply the saved plan. Export `terraform output -json access`
   to an operator-only file. This file contains infrastructure identifiers,
   not credentials.
3. For learner instances, pass the shared output's `vpc_id`, a `subnet_id` in
   a verified instance-offering zone and `gateway_security_group_id` to the
   existing pilot Terraform module. Retain the existing fixed AMI/release,
   hash checks, instance expiry, private backend and operator-only state.
4. Follow [Cognito access](cognito-access.md) to add authenticated activity
   bindings and install the learner gateways. ALB `/healthz` checks only the gateway process, not the course,
   Docker, kernel or optional services.

The current guarded pilot CLI still plans its SSM topology. Shared topology is
an explicit Terraform interface; do not edit a saved pilot plan or claim the
pilot preflight has checked ALB, DNS or authentication costs. Full shared-access
cloud deployment needs refreshed identity, prices, quotas and a reviewed plan.

Delete activity listener rules, DNS aliases, target attachments and target
groups before destroying the learner instances. Shared ALB/network/certificate
state and existing DNS zones survive activity cleanup. Retire the shared state
only after all activities have detached. The module owns its ACM validation
record; do not reuse that exact record outside its managed certificate lifecycle.

## Hostnames and connections

Use one DNS label per event, learner and service, such as
`class-a-s001-jupyter.workshop.example.com`. A certificate for
`*.workshop.example.com` covers this name; it does not cover another nested
label. Each learner has one target group and a local gateway dispatches its
service hostnames. Exact Host matching prevents unassigned hostnames from
selecting a backend. Separate service origins retain root-relative resources
and keep application cookies separate.

The ALB preserves Host, drops invalid headers and uses strict desync mitigation.
Its idle timeout is 3600 seconds. Gateways must support WebSocket upgrades;
reconnect, kernel/terminal sessions and browser TLS remain cloud acceptance
checks. Increasing an idle timeout does not make a connection immortal.

The deployment currently has local format/schema/provider-mock coverage. No
formal-domain TLS or shared-login browser validation is claimed yet. The
published AMI remains unchanged; access code must be installed as a separately
versioned overlay or included in a newly validated image.

References: [ALB subnet requirements](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html),
[ALB authentication](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html),
[ALB quotas](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html),
[subdomain delegation](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/CreatingNewSubdomain.html).
