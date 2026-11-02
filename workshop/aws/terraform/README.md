# Terraform integration-pilot implementation

These scripts have completed a real operator integration run with two
`m7i.2xlarge` hosts in `us-east-1`, each with a 200 GiB encrypted gp3 root disk,
on 2026-10-30. Terraform created the foundation and activity, the host runtime
was installed through SSM, and activity destroy was verified through AWS APIs.
The separate private state bucket was retained. Eight provider-mock runs also
pass; mocks remain distinct from the real run.

This validates infrastructure and host-runtime operations. Full course Lab
execution, a learner-facing browser gateway, HTTPS/login and automated
activity-wide TTL cleanup have separate acceptance work.

## Modules

| Directory | Scope |
| --- | --- |
| `bootstrap/` | Separate private, encrypted, versioned state bucket; blocks learner-role access and prevents accidental bucket destruction |
| `partner-dns/` | Persistent public partner zone and parent NS handoff, independently retained across activities |
| `pilot/` | One EC2 per learner (up to five), with stable map keys, encrypted gp3 root disk, IMDSv2, SSM, a private runtime-code bucket and an installation document |
| `image-validation/` | Independent temporary launch and audit of a fixed candidate AMI before publication |

The initial pilot targets the commercial AWS partition and an official
Canonical Ubuntu 24.04 amd64 server AMI. The exact AMI must be resolved and
verified in the selected Region before planning. A new activity-owned public
subnet/Internet Gateway is created by default, with no NAT Gateway. An explicit
existing VPC and subnet may be supplied together; their ownership, public
egress route and available capacity need preflight verification.

The same pilot also accepts an exact private, validated release through the
[prebuilt activity workflow](../prebuilt.md). It verifies owner/version hashes,
injects activity differences, uploads no installer objects and checks existing
services. Base mode retains its original runtime installation path.

The instance has a public IPv4 address for outbound access and **no inbound
security-group rules**. SSM provides operator access and local port forwarding.
Jupyter remains on loopback. Formal workshop HTTPS, login and per-user routing
are separate implementation work.

The private activity bucket contains runtime installer code only. Terraform
uploads the files declared by `runtime.json`; the SSM document downloads the
bundle using the instance role and invokes `install_host.sh`. The role can read
only that installer prefix. It cannot read Terraform state, parameter-store
secrets or Secrets Manager values, or create/terminate instances and manage IAM.
SSM channel permissions use wildcard resources where the service requires them.
These boundaries still require real-instance negative tests.

## Static checks first

With Terraform 1.10 or newer (less than 2.0), run from the repository root:

```bash
terraform fmt -check -recursive workshop/aws/terraform
terraform -chdir=workshop/aws/terraform/bootstrap init -backend=false
terraform -chdir=workshop/aws/terraform/bootstrap validate
terraform -chdir=workshop/aws/terraform/bootstrap test
terraform -chdir=workshop/aws/terraform/pilot init -backend=false
terraform -chdir=workshop/aws/terraform/pilot validate
terraform -chdir=workshop/aws/terraform/pilot test
```

The tests use provider mocks, including mock apply operations, so they do not
contact an AWS account or create resources. The tiny bundle under
`pilot/tests/fixtures/` is only a test fixture. Real planning requires a bundle
created with [the runtime builder](../runtime/README.md).

## Real deployment sequence

Use the [first-run runbook](../first-run.md) and `tools/workshop_preflight.py`
for live checks and saved-plan application. That gate generates explicit vars,
isolates each run's Terraform data/backend, verifies immutable inputs and reruns
live checks before apply. Failed checks never invoke Terraform. Its first-run
scope is one to five learners, a new VPC, public egress and an official base AMI.

The following is the intended operator sequence. It is not an executed result.
Account/profile/Region, owner, budget, expiry and course selection must first be
provided and checked. Clear unintended environment credentials, use an explicit
profile, and verify its STS account against the expected account. Resolve the
Canonical AMI, check x86_64 instance compatibility/quotas and current total cost.
Unknown or failed checks must block apply.

1. Create a private local activity directory for vars, plans, operational
   results and the bootstrap state. Use restrictive filesystem permissions.
2. Plan and apply `bootstrap/` using a distinct foundation state path. Preserve
   that state and bucket beyond the activity; the learner never receives them.
3. Configure the pilot S3 backend with `bucket`, `region`, explicit `profile`,
   `allowed_account_ids`, `encrypt = true`, `use_lockfile = true` and an event key
   of `aws-workshop/<partition>/<account>/<region>/<event_id>/terraform.tfstate`.
   Reinitialize the backend for this activity before planning.
4. Supply the required pilot variables, including a verified `ami_id`, a future
   UTC `expires_at`, `state_bucket_name` and a real `runtime_bundle_dir`. Review
   the saved plan against the target account, resource ownership and budget.
5. Apply the saved Terraform plan, wait for the instance to become SSM-managed,
   and invoke the `install_document` output through SSM Run Command. Runtime
   readiness must pass before reporting the environment ready.
6. Run the real kernel/Notebook/Terminal/Docker, service/host restart, permissions
   and fault checks. Save redacted evidence, then destroy the activity through
   the same Terraform backend and vars and reconcile the remaining resources.

No deployment identity or learning-record backend is sent to the instance.
The full create/install/readiness wrapper remains separate work. The live
preflight and saved-plan gate were exercised in the real operator run described
above. Full course/browser validation is still pending.
Do not use examples or mock results as evidence of a real account check.

## Expiry and cleanup boundaries

User data configures a persistent systemd timer for the declared expiry. An
instance-initiated halt terminates this temporary EC2, with its root volume
deleted. A normal reboot remains a reboot. This is a cost guard for the pilot;
it is **not** an activity-wide Terraform cleanup executor. It cannot clean the
activity bucket, IAM, SSM document or network after agent/control-plane failure.
The TTL executor and state-lock coordination remain separate work.

The operator must run Terraform destroy for the entire activity. Existing
VPC/subnet resources and the separate foundation state bucket are retained;
activity-owned network, code bucket, document, role and EC2 are removed. Record
any retained or failed-to-delete resource and its next cleanup action.

Local state, vars, backend files, plans and `.terraform/` are ignored. The
provider lock files include the verified native content hash and official
release ZIP checksums for the published platforms. Terraform validation and provider-mock tests do not establish EC2
availability, installation success or actual cleanup.

References: [S3 backend and lock files](https://developer.hashicorp.com/terraform/language/backend/s3),
[SSM scripts from S3](https://docs.aws.amazon.com/systems-manager/latest/userguide/integration-s3.html),
[Canonical Ubuntu AMI discovery](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/).
## Shared access

See [shared HTTPS access](../shared-access.md) for `shared-access`, its independent
state, two-zone VPC, default-deny ALB and ACM/DNS ownership. The pilot module can
reuse that network and attach its ALB-only gateway security group through explicit
Terraform variables. The guarded SSM pilot CLI does not yet plan this topology.

## TTL control module

`ttl-control` defines an external Scheduler → Standard workflow → Fargate
cleanup task, with four reviewed policy inputs and a schedule disabled by
default. See [TTL preparation and limits](../ttl/README.md) before using it.
It belongs to a separate `cleanup-control` state and operator network. Do not
include it in an activity destroy plan. Its provider mocks are offline checks;
the image, permissions, real expiry and retirement still need acceptance.

`pilot` and `access-bindings` keep named-profile authentication by default.
Only the explicit `credential_mode = "task-role"`, `aws_profile = null` path
allows the verified Fargate runner to use ECS credentials. This does not
change the operator CLI's explicit-profile requirement.
