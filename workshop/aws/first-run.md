# First real Terraform integration run

Use this runbook after the offline Terraform and runtime checks pass. It covers
one to five new-network, SSM-only learner hosts; it does not certify full course/browser access.
The [architecture](architecture.md) defines resource ownership and later stages.

## Supply the first-run inputs

Confirm an explicit local AWS profile (SSO or a short-lived AssumeRole chain),
expected 12-digit account ID, commercial AWS Region, owner/event ID, explicit learner
IDs, course SHA/profiles, instance/disk sizing, expiry and USD budget. Confirm that
new VPC creation and public egress are allowed. Do not put credentials in YAML.
Formal domain/IdP values may remain deferred placeholders for this SSM-only run.

Start with [event.example.yaml](examples/event.example.yaml) and
[pilot.example.yaml](examples/pilot.example.yaml), placing your copies in ignored
`workshop/aws/events/`. The examples are not approved activity parameters.
Use a globally unique state bucket name and a UTC expiry within the configured
`max_run_hours` (at most 24). Include installation time in the run duration.

The integration gate supports one to five non-burstable Standard On-Demand x86_64 instances,
an official Canonical Ubuntu 24.04 EBS base AMI and no spares. It rejects private
networks, reused networks, prebuilt images, spares and rosters above five learners. Discover a current
AMI in the selected Region with the
[Canonical instructions](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/);
set its owner to `099720109477`. Availability listings do not reserve capacity.

Install Python 3.12 and Terraform 1.10 or newer (below 2.0), then:

```bash
python3.12 -m venv .venv-workshop
.venv-workshop/bin/python -m pip install -r workshop/aws/preflight-requirements.txt
.venv-workshop/bin/python tools/workshop_config.py validate \
  --config workshop/aws/events/pilot.yaml
.venv-workshop/bin/python tools/workshop_runtime.py \
  --config workshop/aws/events/pilot.yaml \
  --output workshop/aws/.local/runtime-bundle
```

All paths in the pilot settings are relative to the repository root. Install the
[AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
and [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
for the later operator session. The preflight tool uses the pinned Python AWS
SDK and does not require the AWS CLI. Authenticate the chosen profile through
your normal SSO/role process. Remove unintended credential environment variables,
custom AWS endpoint overrides, `TF_VAR_*`, `TF_CLI_ARGS*` and `TF_WORKSPACE`.
The gate rejects them rather than choosing an implicit identity or workspace.

## Live preflight and saved plans

Run from the repository root with Terraform on `PATH`:

```bash
.venv-workshop/bin/python tools/workshop_preflight.py plan \
  --stage bootstrap --config workshop/aws/events/pilot.yaml \
  --pilot workshop/aws/events/pilot-settings.yaml \
  --output workshop/aws/.local/foundation-run-01
```

This performs read-only AWS queries, policy simulation and EC2 DryRun. It checks
STS account/partition, enabled Region, official AMI metadata/root size, instance
compatibility/offerings, deployment/SSM/destroy permissions, existing vCPU/gp3/VPC/
IGW usage and applied quotas, bucket ownership, live prices and the budget.
Bootstrap requires a new state bucket name. The pilot stage requires an existing
foundation bucket with matching owner/Region/tags, versioning, encryption, public
access blocks and explicit learner/TLS deny policies.

The IAM simulation deliberately evaluates required actions at wildcard scope.
A scoped or conditional deployment role may be rejected even when a more precise
request would succeed. Missing context or denied simulation is a failure; do not
relax the account's policies just to pass. Extend the simulator for the approved
role's actual resource/context constraints instead. Simulation does not establish
session-policy, resource-policy or endpoint-policy authorization, nor launch
capacity. Actual AWS operations can still fail and require cleanup.

The estimate includes compute and public IPv4 for the full maximum run plus
cleanup buffer, encrypted root gp3, S3 Standard storage including retained state,
S3 write/read requests, Internet egress and an explicit USD contingency. Quote
SKU/rate, effective date, query time, unit and quantity are saved. Highest paid
tiers are used conservatively, with no free-tier or discount assumption. There
is no NAT, ALB, model service, snapshot or separate teaching archive in this
pilot. Selected course services run on the host. Usage quantities are assumptions,
not enforced billing limits; review them against the chosen courses and reserve
for taxes/account-specific charges in the contingency. Retained state continues
to incur storage charges after the event. Any unknown required price fails.

A standalone read-only report is also available:

```bash
.venv-workshop/bin/python tools/workshop_preflight.py check \
  --stage bootstrap --config workshop/aws/events/pilot.yaml \
  --pilot workshop/aws/events/pilot-settings.yaml \
  --output workshop/aws/.local/preflight-01.json
```

A failed check exits `2` and never starts Terraform. A query error is reported as
a query failure, not as a configuration success. Reports and run files are
private operational records (new files use `0600`, run directories `0700`).
Do not commit plans, state, backend files, event settings or reports.

Review the bootstrap plan, then apply the exact saved plan:

```bash
.venv-workshop/bin/python tools/workshop_preflight.py apply \
  --run-dir workshop/aws/.local/foundation-run-01
```

Apply verifies plan/variables/module hashes, rechecks all bundle files and reruns
live preflight. Changed inputs, a stale report (15 minutes maximum), query
failures and budget/quota failures block apply. Recreate and review a new plan
when the report expires. Do not bypass this gate with raw `terraform apply`.
Preserve `foundation-run-01/foundation.tfstate` and its backup as the separate
bootstrap state; do not create the same bucket again from a new empty state.

Once the foundation exists, plan and review the activity:

```bash
.venv-workshop/bin/python tools/workshop_preflight.py plan \
  --stage pilot --config workshop/aws/events/pilot.yaml \
  --pilot workshop/aws/events/pilot-settings.yaml \
  --output workshop/aws/.local/pilot-run-01
.venv-workshop/bin/python tools/workshop_preflight.py apply \
  --run-dir workshop/aws/.local/pilot-run-01
```

The Terraform input is `learner_ids`, a list of stable unique IDs (default
`["s001"]`). Reordering that list leaves existing map keys unchanged. Cost and
vCPU/gp3 headroom calculations include every learner. The gate pins the new
subnet to a zone that offers the instance type and rechecks that zone before
apply. This extends the operator
integration pilot; formal learner login, cross-user browser isolation and the
full batch lifecycle remain separate work.

Every run has a separate `terraform-data` directory and an explicit backend;
switching events cannot silently reuse a previous event's backend. Review one
instance per stable learner ID, encrypted/deleted-on-termination root disks, zero inbound rules,
activity-only resources, tags, owner, account and expiry in the saved plan.
Partial apply failures leave the run directory and state available for cleanup.

## Install, inspect and destroy

For commands that operate on a run, select the same Terraform data directory:

```bash
export TF_DATA_DIR="$PWD/workshop/aws/.local/pilot-run-01/terraform-data"
terraform -chdir=workshop/aws/terraform/pilot output -json
```

Use the `instance_ids` and `install_document` outputs with the explicit profile
and Region. Wait until `ssm describe-instance-information` reports the instance
online, then invoke `ssm send-command` with that installation document and
instance. Poll `ssm get-command-invocation` with a finite timeout (the document
allows at most two hours). Inspect private installation logs and run the runtime
[readiness command](runtime/README.md) through SSM. EC2 running or a successful
SSM dispatch alone does not mean runtime ready.

For example, with the confirmed profile/Region and actual Terraform outputs:

```bash
# Repeat this read for at most 10 minutes; proceed only when PingStatus is Online.
aws --profile CONFIRMED_PROFILE --region CONFIRMED_REGION ssm describe-instance-information \
  --filters 'Key=InstanceIds,Values=ACTUAL_INSTANCE_ID' \
  --query 'InstanceInformationList[0].PingStatus' --output text
aws --profile CONFIRMED_PROFILE --region CONFIRMED_REGION ssm send-command \
  --document-name ACTUAL_INSTALL_DOCUMENT --instance-ids ACTUAL_INSTANCE_ID \
  --query 'Command.CommandId' --output text
aws --profile CONFIRMED_PROFILE --region CONFIRMED_REGION ssm get-command-invocation \
  --command-id ACTUAL_COMMAND_ID --instance-id ACTUAL_INSTANCE_ID
aws --profile CONFIRMED_PROFILE --region CONFIRMED_REGION ssm start-session \
  --target ACTUAL_INSTANCE_ID --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8888"],"localPortNumber":["18888"]}'
```

For a local Jupyter connection, use `AWS-StartPortForwardingSession` with the
runtime's loopback port 8888 and a free local port. Retrieve the runtime token
through the authenticated operator session without including it in evidence.
Verify real kernels/cwd/package isolation, Notebook/terminal Docker access,
service/host restart, fault detection, and instance-role denials for state,
secrets and deployment actions. These are real integration checks, not inferred
from the mocked tests. Full course and HTTPS/browser checks follow separately.

Always destroy the activity, including after failure or expiry:

```bash
terraform -chdir=workshop/aws/terraform/pilot plan -destroy \
  -var-file="$PWD/workshop/aws/.local/pilot-run-01/inputs.tfvars.json" \
  -out="$PWD/workshop/aws/.local/pilot-run-01/destroy.tfplan"
terraform -chdir=workshop/aws/terraform/pilot apply \
  "$PWD/workshop/aws/.local/pilot-run-01/destroy.tfplan"
```

Review the destroy plan before applying. Creation preflight intentionally does
not guard destroy: expiry or a price-query outage must not prevent cleanup.
Reconcile EC2, EBS, ENIs, public addresses, VPC/routes/IGW/SG, runtime bucket,
installation document and learner role/profile. Preserve the foundation bucket
and its separate state, and record retained storage costs. The host expiry timer
terminates EC2/root disk only; it does not replace activity Terraform destroy.
