# Activity lifecycle CLI

`tools/workshop.py` wraps the existing live preflight and saved-plan gate,
Terraform activity resources, SSM installation and runtime probes. It reuses an
existing foundation bucket. It never creates or destroys foundation resources.
The current scope is the one-to-five-host SSM-only pilot described in the
[first-run guide](first-run.md). The two-host up/repeated-up, fault status,
service recovery and basic destroy path have been exercised on real EC2.
Stable roster updates, listener allocations and bounded SSM scheduling now have
[local batch coverage](batch-operations.md); those additions have not been
exercised in a real shared activity.
The default `rta-main` Labs 1–10 were executed separately after readiness.
Shared HTTPS/OIDC and manual browser interaction remain separate checks.

## Plan, review and bring up

Install [preflight-requirements.txt](preflight-requirements.txt), put Terraform
on PATH, authenticate the explicit profile, and prepare the event/pilot inputs
and a fresh [runtime bundle](runtime/README.md). Use a new run directory each
time you create a saved plan. The bundle must include the current reader and
runtime probes; rebuild older bundles rather than patching an installed host.

```bash
.venv-workshop/bin/python tools/workshop.py plan \
  --config workshop/aws/events/event.local.yaml \
  --pilot workshop/aws/events/pilot.local.yaml \
  --run-dir workshop/aws/.local/event-run-03
```

This performs live identity, budget, price, permission and quota checks before
Terraform init/plan. It writes a private saved plan and prints its SHA256.
Inspect the saved plan with Terraform, using that run's `TF_DATA_DIR`:

```bash
TF_DATA_DIR="$PWD/workshop/aws/.local/event-run-03/terraform-data" \
  terraform -chdir=workshop/aws/terraform/pilot show \
  "$PWD/workshop/aws/.local/event-run-03/saved.tfplan"
```

Review ownership, learner IDs, zero inbound rules, deleted root disks, expiry,
instance count and resource changes. Then one command applies that exact plan,
waits for SSM, installs the runtime and checks every learner host:

```bash
.venv-workshop/bin/python tools/workshop.py up \
  --run-dir workshop/aws/.local/event-run-03 \
  --approved-plan-sha256 REVIEWED_SHA256 --timeout 7500 --concurrency 2
```

Apply reruns live preflight and rejects stale, changed or unreviewed plans.
`--timeout` bounds installation/readiness waiting (1–14400 seconds), separately
from the 1800-second Terraform subprocess limit and the SSM document's execution
limit. SDK reads have finite retries/timeouts. A deadline leaves resource and
command IDs in `lifecycle.json`; it does not imply cancellation or cleanup.
`--concurrency` limits outstanding remote SSM commands in this run, from 1 to 16
(default 2). It applies to `up`, `status` and `replace`, independently of
Terraform's own scheduling. It does not increase the pilot's one-to-five-host
limit. Lowering the limit polls already running work without cancelling it.

`ready: true` means host runtime and authenticated course entry are ready:
correct pinned course/runtime, Jupyter authentication, course reader, Docker,
Notebook bindings and course kernels. It always includes
`course_labs_validated: false`. Lab containers are started by the Labs, so the
service list can initially be empty. Later probes report running and stopped
course containers. EC2 `running` and an SSM dispatch are insufficient for ready.

The reported entry is `operator-ssm-forward`, remote port 8888, `/workshop/`.
Use the [temporary-access instructions](runtime/access.md) to forward the port
and log in. A configured DNS placeholder is never printed as a working URL.
The CLI does not print tokens or retrieve them into its ordinary output.

## Status and recovery

```bash
.venv-workshop/bin/python tools/workshop.py status \
  --run-dir workshop/aws/.local/event-run-03 --timeout 180
.venv-workshop/bin/python tools/workshop.py up \
  --run-dir workshop/aws/.local/event-run-03 --retry-failed
```

Repeated `up` after a successful apply resumes installation without replaying
the saved plan. It reuses pending SSM command IDs and successful installations,
then runs fresh health probes. `status` performs fresh remote probes and never
installs packages. `--retry-failed` permits at most three attempts per failed
installation/probe in that run. A lost send-command response is recorded as
`DispatchUnknown`; inspect SSM command history and resolve the outstanding
command before retrying. It is never silently resent.
The separate dispatch SDK client disables automatic retries. `status` can poll
an already dispatched installer to completion, but never dispatches a new one.

Failed or interrupted Terraform apply requires a new reviewed plan in a fresh
run directory using the same event backend, or cleanup through the existing
run. An old saved plan cannot be replayed. Preserve every run directory until
cleanup and reconciliation finish. Keep the original deployment checkout and
bundle available, because module, variables and backend fingerprints are bound.

The non-secret `lifecycle.json` reports each learner's EC2/SSM state, bounded
command attempts, health checks, current services, known EC2/EBS IDs, errors and
retained foundation. SDK/SSM bodies are filtered rather than copied to it.
Terraform diagnostics are private `terraform-*.log` files; raw machine-readable
state stays in memory. Human Terraform output is discarded; these log files
contain only a return code. Installer logs under `/var/log/doris-workshop` use
fixed categories. Jupyter/gateway application output is disabled by the log
policy. Use [controlled diagnostics](operations.md) for per-node service/kernel
failures, bounded private evidence and older-image policy installation.
Neither CLI failure nor local interruption deletes resources.

Exit codes are `0` for ready/successfully destroyed/planned, `1` for a completed
status query that is not ready, and `2` for invalid input, query/command failure,
timeout or incomplete cleanup. Per-run file locks prevent concurrent local
operations; Terraform retains its normal backend lock.

## Destroy the activity

```bash
.venv-workshop/bin/python tools/workshop.py destroy \
  --run-dir workshop/aws/.local/event-run-03 --approve-destroy
```

This validates explicit identity, bound activity backend/variables/module,
state resource ownership and actual EC2 assignment. It saves EC2/EBS IDs,
creates a destroy plan, rejects unowned or non-delete changes, and applies the
plan under the normal Terraform lock. It then requires an empty managed state,
no active known instance and no remaining known root volume. Retry the same
command after a failed destroy or eventual-consistency residual. Known ENIs must
also disappear. The [cleanup guide](cleanup.md) describes stricter ownership and
delete-plan checks plus broader standalone scan/report interfaces. Retry the same
command after a failed destroy or eventual-consistency residual. Expired event
inputs and unavailable price queries do not block cleanup.

Foundation storage remains and is reported explicitly. This basic reconciliation
does not replace the later full account/API residual scan, external TTL runner,
or inspection of resources removed manually from state. Known running instances
missing from state are reported as untracked billing resources; do not claim
the account is empty based on an empty activity state.

The SSM integration uses [SendCommand](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_SendCommand.html)
and [GetCommandInvocation](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_GetCommandInvocation.html).
Controlled offline tests cover orchestration and AWS SDK request shapes.
Real cloud checks covered two fresh hosts, unchanged installation command IDs
on repeated up, detection of a stopped Jupyter service while EC2 remained
running, recovery, and activity destroy with retained foundation. Separate fresh-image acceptance covered the complete default RTA Labs and
quizzes plus real browser interactions. Optional parts and shared user routing
remain outside that acceptance scope.

For exact private-image selection, dependency-free startup, timing records and
guarded single-learner replacement, see [prebuilt activities](prebuilt.md).

## TTL enrollment

The [external TTL contract](ttl/README.md) adds a distributed activity fence
around `plan`, `up`, `replace-plan`, `replace` and `destroy`. Enrolled events
require `--ttl-binding PATH`; an old run directory cannot skip that check.
Cancel the current revision before roster/replacement/expiry changes, then
reseal the resulting resource IDs and inputs as a new revision. An unknown
operator/cleanup outcome retains the fence until reconciliation. Missing-key
legacy behavior does not coordinate an already running command with first
enrollment, so quiesce operators before registering the first revision. The
control module is disabled by default and has no live TTL acceptance yet.
