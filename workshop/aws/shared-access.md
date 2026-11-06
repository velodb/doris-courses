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

For a partner-owned namespace, first create or reconcile its public zone using
[partner DNS](terraform/partner-dns/README.md), and have the company administrator
delegate that subdomain. Use the partner's own zone ID and domain here.

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

Service links are assigned before the corresponding Lab starts its containers.
For RTA, the single-FE sandbox starts in Lab 1; FE 1–3 belong to the separate
three-FE, three-BE cluster started in Lab 9. The workspace explains this distinction
and links to Lab 9. If an authenticated browser cannot reach a service, its error
page explains the startup step and offers the assigned Lab, course and retry links.
This guidance does not start containers or report a live cluster inventory.
API and WebSocket failures keep their existing status and non-HTML responses.

The guarded pilot CLI can also consume the private exported `access` object via
`shared_access_file` in the pilot inputs. Its read-only preflight checks the
account, resource ownership tags, ALB/listener/VPC/subnet relationships,
gateway security-group ingress, a route to the owned Internet gateway, address
headroom and the instance offering. The access file is bound to the saved plan;
changing it requires a fresh plan. The estimate includes the shared ALB,
10 LCUs, its IPv4 addresses and the current portal host, if present, for the
entire test window. DNS, snapshots and model APIs remain outside that estimate.
This topology check does not provision the shared entry, review its state or
prove that DNS, authentication, gateway software or course services work.
Create and review those resources separately before the pilot plan.

Delete activity listener rules, DNS aliases, target attachments and target
groups before destroying the learner instances. Shared ALB/network/certificate
state and existing DNS zones survive activity cleanup. Retire the shared state
only after all activities have detached. The module owns its ACM validation
record; do not reuse that exact record outside its managed certificate lifecycle.
The separately provisioned operator portal host has its own lifecycle and must
also be retired when the shared entry is retired; activity destroy does not own it.

When retiring an entry with a portal, first remove all activity assignments and
bindings. While the portal host still exists, set `portal_instance_id = null`
and review a fresh shared-access plan that detaches only its portal listener
rule, target attachment and target group. Apply that plan under the shared
state lock, then destroy the separate portal state. Finally review and destroy
the remaining shared-access state. This order avoids evaluating the portal
instance and subnet data sources after EC2 has discarded the terminated host's
network details. Preserve the hosted zone and foundation state unless they are
independently owned and explicitly selected for retirement.

If the portal was already terminated, do not replay an old plan or blindly
remove state. Reconcile the exact prior instance, target group and attachment
against AWS, prepare fresh inputs with the portal reference cleared, and review
the resulting plan and any refresh drift before proceeding. A missing attachment
is not evidence that unrelated state objects can be forgotten.

## Separate DNS account

The workload and DNS accounts can differ. The default AWS provider owns the
ALB, ACM certificate, network and portal attachments in `expected_account_id`.
Only Route 53 zone reads and records use the `aws.dns` provider. Set the optional
`dns` variable in the shared module to exactly one explicit credential path:

```json
{
  "dns": {
    "account_id": "111111111111",
    "role_arn": "arn:aws:iam::111111111111:role/workshop-dns"
  }
}
```

Alternatively, replace `role_arn` with `"profile": "workshop-dns"` for an
operator profile using temporary credentials. The role path assumes the DNS
role from the existing workload profile; it does not change the S3 backend
identity. Both provider configurations restrict the allowed account IDs, and
the DNS caller is checked independently. Omitting `dns` preserves the original
same-account behavior. The exported `access.dns_account_id` binds later
activity records to that same DNS account.

Before any apply, verify STS for both identities, `GetHostedZone` for the exact
public zone, its delegated NS set, record inventory and the DNS role's scoped
permissions. The role needs zone-scoped reads and record changes, plus
`GetChange` to observe propagation. Restrict changes to approved A aliases and
ACM validation CNAMEs with Route 53 record-name/type/action conditions. Exclude
NS/SOA changes, zone creation/deletion and parent-zone access. The DNS account
administrator must establish trust and the caller must be allowed to assume
that exact role. This module does not create a role or establish trust. See
[Route 53 record permissions](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resource-record-sets-permissions.html)
and [cross-account roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/tutorial_cross-account-with-roles.html).

Pass the same private DNS configuration to activity route generation with
`--dns`; see [Cognito access](cognito-access.md). Retain both identities and
their reviewed inputs until the shared and activity records have been removed.
The activity state owns only its exact aliases. The shared state owns the apex
alias and certificate CNAME; neither state owns the hosted zone or delegation.
DNS cannot be switched to another provider just by editing an old saved plan.

This interface has offline provider-mock coverage. Cross-account API scans,
full cleanup reconciliation and task-role TTL execution still require separate
implementation and cloud acceptance. Separate DNS credentials are rejected in
the current task-role bindings mode. Do not enable automatic TTL on the basis
of these mocks or treat a workload-only scan as proof that DNS is clean.
Changes to the bindings module also change the TTL runtime tree: the previously
validated local cleanup image and its source binding cannot be relabeled or
reused for this new source without rebuilding and repeating its required checks.

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
2. Separately provision an operator-controlled Ubuntu 24.04 EC2 using
   [shared-portal](terraform/shared-portal/README.md) in the shared
   VPC, with Python 3.12 and `python3.12-venv`, SSM access and outbound HTTPS.
   The host needs Internet egress for package installation and ALB public keys.
   Give it the exported `portal_security_group_id`; expose no public inbound
   ports. Remove any other security-group ingress that would bypass the ALB.
   Tag it `Project=doris-workshop`, `Scope=shared-portal`, matching `Owner` and
   `Access`, and no `Event` tag. It must have no learner runtime, Docker socket,
   learner login, state access or deployment credentials. Its encrypted disk,
   IAM/SSM access, patches, capacity, cost and cleanup belong to the operator.
   The separate module provisions this host; it is not part of activity state.
3. Set `portal_instance_id` to that separate host and reserve `portal_priority`
   (default 100) across the listener. Review/apply a fresh shared plan and export
   `access` again. Terraform checks VPC, portal SG and ownership tags; activity
   bindings also reject assigning this host to a learner. These checks do not
   inspect the host's installed software or all other security groups.
4. Install the empty portal on the verified shared host through SSM:

   ```bash
   python tools/workshop_portal_install.py \
     --config workshop/aws/events/event.local.yaml \
     --access workshop/aws/.local/access.json \
     --portal-run workshop/aws/.local/portal-run \
     --output workshop/aws/.local/portal-install
   ```

   The run directory contains the shared-portal backend and inputs. The command
   checks the owned state, live host and target group, then installs the fixed
   overlay under a host lock. It refuses to overwrite an existing registry and
   saves the exact empty registry as `portal-install/portal-empty.json`.
   Unknown or failed dispatches require reconciliation before a new operation.
5. Generate each activity's routes and deploy its bindings using
   [Cognito access](cognito-access.md). With a configured shared portal, `routes`
   adds the activity entry, three rule priorities and its exact callback/logout
   URLs. Reserve priorities across all activities. Create accounts, install
   learner gateways and verify their independent readiness.
6. Register the activity with the guarded [portal update](batch-operations.md),
   using the same route file for `--current-route` and `--plan` and the empty
   registry as `--previous-registry` for the first registration. Later updates
   use the previous operation's `next-portal.json` and preserve other activities.
   Verify public TLS, Cognito first login/change-password, assigned links,
   origin isolation, restart and cleanup separately.

Removing an activity from the portal file and restarting the portal removes its
entry immediately. Also remove its activity bindings and learner resources;
removing an entry or reaching its expiry does not destroy AWS resources. Portal
logout clears only the activity-entry origin's cookies. Other service sessions
follow the existing [logout boundary](cognito-access.md#trust-and-session-boundary).

Before registering the first activity, install the same runtime with an empty
registry containing `schema_version: 1`, `domain`, `region`, the actual shared
`alb_arn`, and `activities: {}`. The root index reports that no activities are
available. Activity hosts remain denied, and no placeholder Cognito identities
or learner assignments are needed. Replace this private configuration with the
generated registry when an activity is ready.

For a delegated partner namespace, set `domain` consistently in the event and
shared module, for example `partner-a.workshop.example.com`, and supply the
partner's public zone in its own account. The same `/doris-courses/` path and
single-label service origins apply. Its certificate covers that partner apex
and `*.partner-a.workshop.example.com`; a company wildcard cannot cover the
deeper service names. Examples do not create or authorize a partner. A
separate DNS identity is optional; a partner with its own zone and workloads in
one account does not need company credentials or a cross-account DNS role.

## Hostnames and connections

Use one DNS label per event, learner and service, such as
`class-a-s001-jupyter.workshop.example.com`. A certificate for
`*.workshop.example.com` covers this name; it does not cover another nested
label. Each learner has one target group and a local gateway dispatches its
service hostnames. Exact Host matching prevents unassigned hostnames from
selecting a backend. Separate service origins retain root-relative resources
and keep application cookies separate.

The ALB preserves Host, drops invalid headers and uses strict desync mitigation.
Its idle timeout is 3600 seconds. Gateways support WebSocket upgrades. A
real two-learner `rta-main` event validated public TLS, shared login, notebook
and terminal connections, reconnection and a signed-identity expiry while idle.
Increasing the ALB idle timeout does not make a connection immortal.

The operator portal was validated on a separate host, including restart and
reboot in an earlier event. These results cover the tested events and their
selected course profile, not every deployment or target class size. The published AMI remains
unchanged; install access code as a separately versioned overlay or include it
in a newly validated image.

References: [ALB subnet requirements](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html),
[ALB authentication](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html),
[ALB quotas](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html),
[subdomain delegation](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/CreatingNewSubdomain.html).
The apex/wildcard CNAME selection follows
[ACM DNS validation](https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html);
the root Alias follows [Route 53 ELB routing](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-to-elb-load-balancer.html).


## Independently maintained UI

The activity entry and course handlers render the English A interface from [`ui/workshop`](../../ui/workshop/README.md). Templates, CSS, labels and assets stay there; the handlers retain authentication, assignment and route checks. Course Markdown and Notebooks remain in their original directories.

The public apex route covers `/doris-courses/` and its owned UI paths. Only four fixed public static resources allow cross-origin loading, so an entry signed-out page can load its styles without starting a new login. Personal data does not allow CORS. Only the exact portal signed-out path forwards without authentication; other learner service logout responses keep their existing behavior.

New portal installs stage a hash-bound UI archive before installing the main bundle. Each SSM step keeps the 24KB bound and its own receipt; unknown dispatches or changed inputs require reconciliation. The portal downloads the font and Jupyter logo from pinned public sources with SHA256 checks during installation. Runtime bundles carry those assets offline. Existing AMIs and installed portals are not updated by changing source code.

This integration has local handler/browser and Terraform mock coverage. A new deployment still needs its own identity, budget, expiry and reviewed plans, followed by real Cognito, service, isolation, logout and cleanup verification.
