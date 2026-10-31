# AWS deployment architecture

The first integration run uses the existing `terraform/bootstrap` and
`terraform/pilot` modules. It validates the host runtime through an operator's
SSM session. Full course navigation, shared HTTPS access, AMI publication and
activity-wide expiry cleanup have separate acceptance checks.

## First integration run

Use one On-Demand Ubuntu 24.04 amd64 instance per learner, up to five stable
learner IDs and no spares in the integration pilot.
`rta-main`, `r7i.xlarge` and a 100 GiB gp3 root disk are initial sizing
recommendations. The operator must supply the actual account, explicit profile,
Region, course SHA, runtime bundle, budget and expiry before planning.

The supported preflight path creates an activity-owned VPC, public subnet,
Internet Gateway and route. The instance receives a public IPv4 address for
egress. Its security group has no inbound rules; SSH, Jupyter, SQL and Lab ports
are closed to the Internet. Jupyter binds to loopback and requires its runtime
token. Operators use SSM Run Command for installation and Session Manager for
maintenance and local port forwarding. AWS deployment credentials and Terraform
state stay on the operator's machine or control plane.

A private subnet, reused network, prebuilt AMI, burstable instance or a roster
above five learners requires additional preflight support. The first-run gate rejects these
choices rather than estimating resources it cannot verify. Formal DNS and login
fields in the event configuration are deferred for this SSM-only run.

## Resource ownership

| Scope | State and owner | Destruction boundary |
| --- | --- | --- |
| Foundation | Separate bootstrap state, private versioned S3 state bucket, operator | Retained across activities; bucket has `prevent_destroy` |
| Activity | `aws-workshop/<partition>/<account>/<region>/<event_id>/terraform.tfstate` | EC2/root EBS, activity VPC/routes/IGW/SG, installer-only S3 bucket, learner IAM role/profile and installation document |
| Image release (planned) | Separate Packer release inventory and private AMIs/snapshots | Removed by explicit image-retirement work, never activity destroy |
| Shared access (planned) | Separate state for ALB/ACM, DNS and IdP integration | Activity cleanup removes its bindings only; existing IdP users and shared infrastructure survive |
| Expiry control (planned) | External control-plane state and execution role | Outlives the activity until cleanup has succeeded and been reconciled |

Use stable learner map keys and Project/Event/Owner/Scope tags. Tags help
reconciliation; they do not replace state or grant authority to delete a
resource. Keep bootstrap state, backend configuration and operational records
outside learner instances. No LMS, progress database, persistent grades,
completion certificates or automatic learner-data archive is required.

The learner can control Docker, so treat the instance role as learner-accessible.
It receives SSM agent channels and read access to its exact runtime-code prefix,
with explicit denial of Terraform state, deployment management and secret reads.
Actual negative permission tests remain part of the EC2 integration run.

## Shared browser access

The [shared-access module](shared-access.md) now defines the ALB/ACM and two-zone
network with a default-deny listener. Activity authentication, gateway and browser
acceptance are separate from the SSM pilot. Real-domain cloud validation is pending.

The implementation baseline is a shared ALB with ACM TLS and an OIDC identity
provider. Temporary workshop accounts can use Cognito behind the same identity
contract. The real domain, IdP and account lifecycle require activity input.

The trusted identity key is `(issuer, subject)`. An operator-owned assignment
maps that key to an event and learner ID; a hostname or URL alone never selects
an authorized machine. Validate token expiry, audience/client, issuer and signer
before using identity claims, and check the learner assignment for every page,
API, terminal and WebSocket connection. When consuming ALB identity headers,
verify the signed claims and expected ALB ARN, strip client-supplied identity
headers, and accept backend traffic only from the gateway. OIDC callbacks belong
to registered HTTPS hosts. A logged-in learner must still be denied access to
another learner's instance.

## Expiry, budget and failure behavior

The first pilot has an instance-local persistent systemd expiry timer that
terminates EC2 and deletes its root EBS. It does not delete IAM, S3, SSM or network
resources. The operator must run activity Terraform destroy and reconcile the
resources, including after partial installation or an expired host. Destroy
must remain usable after expiry and without a passing creation preflight.

For full activities, the selected implementation baseline is an external
EventBridge Scheduler trigger and a control-plane Terraform cleanup runner
(for example ECS Fargate, with Step Functions coordinating retries). The runner
uses the original backend and normal state lock, checks account/event ownership,
retries lock contention and transient failures, and records residual resources.
It never runs on the learner host. This executor is planned work, not deployed
by the pilot modules.

Live preflight must fail on unknown identity, permissions, availability, quota
or price. Cost includes On-Demand compute, root gp3, public IPv4, installer/state
storage and requests, transfer assumptions and an explicit cleanup contingency.
ALB, NAT and model charges are absent from this pilot; selecting a topology that
requires them is rejected. A cost estimate is not a billing cap. Reserve time
for installation and cleanup, run destroy even on failure, and retain foundation
storage charges in the operational inventory.

References: [SSM port forwarding](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html),
[ALB claim verification](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html),
[S3 backend locking](https://developer.hashicorp.com/terraform/language/backend/s3).
