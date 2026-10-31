# Shared HTTPS access

The `terraform/shared-access` module owns a shared VPC, two or three public
subnets in distinct Availability Zones, ALB, an apex plus wildcard ACM certificate,
one deduplicated DNS validation CNAME and a root A Alias. Supply a delegated
public Route 53 zone or an existing public zone you
control. The module does not register a domain or change its delegation.

The HTTPS listener defaults to 403. An optional operator portal opens only
the root Host's `/doris-courses` and `/doris-courses/` paths. Activity bindings
add authenticated routes separately. HTTP redirects to HTTPS. ACM retains the TLS private key;
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
The separately provisioned operator portal host has its own lifecycle and must
also be retired when the shared entry is retired; activity destroy does not own it.

## Operator-owned course entry

The [portal runtime](access/portal.py) serves a public activity list at
`https://workshop.example.com/doris-courses/`. Selecting `class-a` opens
`https://class-a-entry.workshop.example.com/doris-courses/`, which authenticates
with that activity's Cognito pool. The portal verifies ALB signatures, issuer,
client, signer, expiry and immutable subject before showing only that learner's
service links. The Jupyter link opens `/workshop/` on the learner's own origin.
No request parameter can select another learner or a redirect destination.

Each activity entry has its own origin and `/oauth2/idpresponse` callback.
Multiple activity pools therefore do not compete for one root-host callback.
The root list contains activity IDs, with no accounts or service assignments.
Other root paths retain the listener's 403 response. Portal pages are not cached
and do not record progress, grades or course completion.

Deploy this explicit interface using separately reviewed Terraform plans:

1. Create shared access with `portal_instance_id = null`. The root Alias exists
   but its course path remains closed. Export `access` as before.
2. Separately provision an operator-controlled Ubuntu 24.04 EC2 in the shared
   VPC, with Python 3.12 and `python3.12-venv`, SSM access and outbound HTTPS.
   The host needs Internet egress for package installation and ALB public keys.
   Give it the exported `portal_security_group_id`; expose no public inbound
   ports. Remove any other security-group ingress that would bypass the ALB.
   Tag it `Project=doris-workshop`, `Scope=shared-portal`, matching `Owner` and
   `Access`, and no `Event` tag. It must have no learner runtime, Docker socket,
   learner login, state access or deployment credentials. Its encrypted disk,
   IAM/SSM access, patches, capacity, cost and cleanup belong to the operator.
   This interface does not provision that host automatically.
3. Set `portal_instance_id` to that separate host and reserve `portal_priority`
   (default 100) across the listener. Review/apply a fresh shared plan and export
   `access` again. Terraform checks VPC, portal SG and ownership tags; activity
   bindings also reject assigning this host to a learner. These checks do not
   inspect the host's installed software or all other security groups.
4. Generate each activity's routes and deploy its bindings using
   [Cognito access](cognito-access.md). With a configured shared portal, `routes`
   adds the activity entry, three rule priorities and its exact callback/logout
   URLs. Reserve priorities across all activities. Create accounts, install
   learner gateways and verify their independent readiness.
5. Prepare the operator-only assignment configuration and a distinct bundle:

   ```bash
   python tools/workshop_access.py portal-config \
     --plan workshop/aws/.local/access-plan.json \
     --identity workshop/aws/.local/identity.json \
     --subjects workshop/aws/.local/account-delivery/subjects.json \
     --output workshop/aws/.local/portal.json
   python tools/workshop_access.py portal-bundle \
     --output-dir workshop/aws/.local/portal-overlay
   ```

   Repeat `--plan`, `--identity` and `--subjects` in the same order for additional
   activities on the same ALB and namespace. All activities must be present in
   each generated portal configuration. It contains immutable subject mappings,
   not client secrets, passwords or tokens. Keep it private and never upload it
   to a learner host. The runtime uses only this file and the public bundle.
6. Upload both through operator SSM to the portal host. As root, run
   `bash install_portal.sh /path/to/portal-overlay /path/to/portal.json`.
   The installer checks bundle hashes, rejects a learner activity file or Docker
   socket, and runs a dedicated unprivileged `doris-portal` systemd service.
   Application and install output are suppressed; `/healthz` checks the process.
   Verify public TLS, Cognito first login/change-password, assigned links,
   origin isolation, restart and cleanup separately.

Removing an activity from the portal file and restarting the portal removes its
entry immediately. Also remove its activity bindings and learner resources;
removing an entry or reaching its expiry does not destroy AWS resources. Portal
logout clears only the activity-entry origin's cookies. Other service sessions
follow the existing [logout boundary](cognito-access.md#trust-and-session-boundary).

For a delegated partner namespace, set `domain` consistently in the event and
shared module, for example `partner-a.workshop.example.com`, and supply the
partner's public zone in its own account. The same `/doris-courses/` path and
single-label service origins apply. Its certificate covers that partner apex
and `*.partner-a.workshop.example.com`; a company wildcard cannot cover the
deeper service names. Examples do not create or authorize a partner. This
single-provider interface does not automate cross-account company DNS writes.

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
operator portal host installer has local fixtures and shell syntax coverage;
it has not been installed or reboot-tested on EC2. The
published AMI remains unchanged; access code must be installed as a separately
versioned overlay or included in a newly validated image.

References: [ALB subnet requirements](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html),
[ALB authentication](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html),
[ALB quotas](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html),
[subdomain delegation](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/CreatingNewSubdomain.html).
The apex/wildcard CNAME selection follows
[ACM DNS validation](https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html);
the root Alias follows [Route 53 ELB routing](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-to-elb-load-balancer.html).
