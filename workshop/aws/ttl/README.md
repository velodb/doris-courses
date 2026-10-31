# External activity TTL cleanup

The local implementation uses EventBridge Scheduler → Step Functions Standard
→ one ECS Fargate cleanup task. It runs outside learner hosts. The
[control Terraform module](../terraform/ttl-control/main.tf) defaults to a
disabled schedule. Its provider mocks and the Python contracts run offline.
The image recipe has **not** been built or run on Fargate, and natural expiry,
live IAM permissions, shared cleanup and control-resource retirement remain
unverified. Do not enable it until those acceptance gates pass.

The worker currently supports the canonical `access-bindings` and `pilot`
remote states in one workload account and Region. It cleans bindings first,
then learner hosts. It does not adopt shared-access, foundation, image-build,
local validation states or cross-account DNS. A cleanup manifest containing
unsupported states is rejected. Residual candidates outside these states must
be handled through their separately reviewed lifecycle.

## Binding and private inputs

[workshop_ttl.py](../../../tools/workshop_ttl.py) seals one revision against
the account, Region, event, owner, domain, UTC expiry, source commit, executable
source hash, [cleanup manifest](../cleanup.md), module hashes, exact variables,
backend keys and runtime artifacts. Inputs and state can contain sensitive
deployment information; keep them in private operator storage.

The activity backend must use S3 encryption, native lock files and the exact
key `aws-workshop/aws/ACCOUNT/REGION/EVENT/terraform.tfstate` for `pilot`, or
`access.tfstate` for bindings. Backend configuration carries
`allowed_account_ids`, without an operator profile. Runner variables require
`credential_mode: task-role` and `aws_profile: null`; the operator CLI still
requires an explicit named profile. Preserve the original deployment inputs and source snapshot for audit;
review the pinned cleanup source separately. An old saved creation plan is
not a cleanup grant.

Prepare a private `modules.json` array. Each item has `module`, `backend`,
`vars_sha256` and `module_sha256`. Variables hashes use
`workshop_config.canonical_hash`. The module hash uses that same function on a
map from each immediate `.tf`, `.tftpl` and `.terraform.lock.hcl` filename to
its SHA-256 file hash. `workshop_ttl_runner.tree_hash(repository_root)` supplies
the executable source hash. These functions are the canonical serialization
contract, including the provider lock files.

For `pilot`, preserve the original runtime bundle as fixed, hash-bound inputs;
change only its variable path to `/work/runtime`. Include `runtime.json` and
every installer file it references. The worker downloads the approved filenames
to scratch storage, verifies sizes/hashes and never extracts or executes the
course archive. Only the installer artifact names accepted by
`workshop_ttl.RUNTIME_FILES` are allowed. Private runtime data and state are
excluded from the runner image.

From the repository root, this command only writes private local files:

```bash
.venv-workshop/bin/python tools/workshop_ttl.py prepare \
  --config workshop/aws/.local/event.yaml \
  --cleanup-manifest workshop/aws/.local/cleanup.json \
  --modules workshop/aws/.local/modules.json \
  --runtime-bundle workshop/aws/.local/runtime \
  --bucket foundation-state-example --revision 1 \
  --source-sha "$CLEANUP_SOURCE_SHA" \
  --source-tree-sha256 "$CLEANUP_TREE_SHA256" \
  --output workshop/aws/.local/ttl.json
```

Use actual reviewed paths and bucket names. `prepare` emits the binding and
`ttl.schedule.json`; it does not upload objects or configure a timer. It rejects
an already expired schedule. A separately authorized execution of the same
fixed worker can handle a due activity; it must pass every ownership and fence
check. Expiry offsets normalize to UTC, and scheduling rounds **up** to a minute
so second precision never causes early deletion. Scheduler itself has
[60-second precision](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html),
so expiry is not a promise of immediate or deadline-guaranteed reclamation.

The task definition pins the binding's S3 VersionId and canonical hash. The
fixed object layout in the existing versioned state bucket is:

```text
aws-workshop-control/ACCOUNT/REGION/EVENT/control.json
aws-workshop-control/ACCOUNT/REGION/EVENT/bindings/REVISION.json
aws-workshop-control/ACCOUNT/REGION/EVENT/bindings/REVISION/cleanup.json
aws-workshop-control/ACCOUNT/REGION/EVENT/bindings/REVISION/MODULE.tfvars.json
aws-workshop-control/ACCOUNT/REGION/EVENT/bindings/REVISION/runtime/FILENAME
aws-workshop-control/ACCOUNT/REGION/EVENT/bindings/REVISION/result.json
```

The manifest, variables and artifacts are read only from these fixed paths and
must match the binding hashes. Revision inputs must not be overwritten. Keep
bucket versioning, encryption and public access blocking; there is no uploader
or automated deployment command in this phase.

## Enrollment, renewal and operator coordination

Initial enrollment requires a quiescent activity, a current owned state
manifest and all operators using the fenced CLI. Retire older binaries and
direct Terraform mutation paths before enabling TTL. The first registration
cannot retrospectively fence an already running legacy command. A missing
control key permits an unenrolled legacy activity; permission failures and
unreadable objects always fail closed.

After reviewing the exact binding hash, `register --config … --binding …
--approve-binding-sha256 …` writes the active control record using explicit
profile credentials and verified STS account identity. It is a real S3 write;
it does not deploy or enable Scheduler. Registration is separate from uploading
the immutable binding and reviewing the control Terraform plan. The task must
not be enabled until all three agree.

`plan`, `up`, `replace-plan`, `replace` and `destroy` in the
[lifecycle CLI](../lifecycle.md) read the deterministic activity control key.
An enrolled activity requires `--ttl-binding PATH`, even from an older run
directory. A conditional S3 write fences the whole operation; Terraform's
normal state lock additionally guards each state. No path disables locking or
uses automatic force-unlock. Status and fixed SSM diagnostics do not mutate
Terraform and do not acquire this fence.

To change a roster, replace a learner or extend expiry, first cancel the old
revision with `cancel --config … --binding … --approve-binding-sha256 …`.
Cancel before editing the configuration. Cancelled activities permit reviewed
operator maintenance while retaining the fence. After successful changes,
rebuild the manifest, variables, policies and binding, advance exactly one
revision using `register --expected-revision OLD …`, and separately update the
disabled control plan and timer. Do not leave a cancelled activity running
without an agreed reclamation owner. Old events become harmless stale requests.
Cancellation does not stop a running task or delete a schedule. A running,
unknown or completed record cannot be renewed or reset through this CLI.

## Fixed runner and failure handling

[runner_entry.py](runner_entry.py) accepts only a JSON trigger containing the
schema, event, revision and binding hash. The task definition supplies all
other constants. No trigger field chooses commands, source, buckets, state
keys, IAM roles or endpoints. The runner verifies ECS task-role credentials,
STS identity, the immutable image's source seal, input hashes and state
ownership, then creates fresh saved destroy plans. Every module plan must
validate before any apply. Newly added or replaced resource IDs require a new
reviewed binding. Final success requires fresh empty-state evidence and the
full configuration-scoped AWS resource reconciliation, including coverage
failures. The result never asserts that the whole account is empty.

The protocol uses S3
[conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
with ETags, not an expiring lease. It never steals an outstanding operation:

| Control state | Meaning and next step |
| --- | --- |
| `active` | Due cleanup or fenced operator work may claim the revision |
| `cancelled` | TTL is inert; reviewed operator maintenance may proceed |
| `operating` / `cleaning` | Another operation owns the fence; wait or investigate |
| `failed` | No apply started; at most three explicit cleanup attempts per revision |
| `unknown` | Apply, operator work or reconciliation may have changed resources; investigate before any further mutation |
| `complete` | Fresh reconciliation passed; duplicate triggers do no work |

Lock wait defaults to 60 seconds and is capped at 120. A task defaults to one
hour and is capped at two hours. The workflow launches at most three tasks,
waiting 30 seconds only after a definite busy exit (`75`). It does not blindly
retry ECS errors, timeout, nonzero exits or uncertain apply outcomes. ECS
`.sync` API failures and container exits are
[separate checks](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html).
Even a timeout or lost S3 reply leaves the fence intact. The workflow attempts
task cancellation using its scoped StopTask permission; verify that the task
actually stopped before recovery.

Inspect the Standard execution history, ECS stopped reason/exit and private
control/result objects. The 14-day encrypted SQS queue records **Scheduler
delivery failures**, not asynchronous worker failures. No CloudWatch log
group, alarm or paging integration is provisioned here. Raw Terraform output,
student files and credentials are not copied to execution output. A failed
coverage check cannot be treated as resource absence. Recovery from an
outstanding/unknown fence requires evidence of terminal execution/task state,
fresh state and resource reconciliation, then a separately reviewed exact
conditional control update. There is deliberately no automatic lease release,
force-unlock, generic delete command or blind retry tool.

## Image, permissions and control-resource retirement

Build [Dockerfile](Dockerfile) only from a clean source archive, supplying the
40-character `SOURCE_SHA` and reviewed digest-pinned `TERRAFORM_IMAGE` and
`PYTHON_IMAGE` build arguments. The recipe installs pinned Python dependencies
and caches locked Terraform providers. It sets an unprivileged UID, a read-only
root and a single writable `/work` volume; ECS uses the matching Dockerfile
[volume ownership contract](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html).
The actual image must be built for Linux x86_64, checked for writable scratch,
provider execution and task-role operation, scanned, then referenced by its
same-account/Region ECR digest. Building or publishing that image was not part
of local acceptance.

Four distinct reviewed identity policies are required: Scheduler delivery,
workflow orchestration, ECR image execution and activity cleanup. Generate
baselines with IAM Policy Autopilot from the current source/plan and verify
them against the actual APIs and resources. Its output is only a starting
point. The checked-in fixture is genuine Autopilot-generated
`sts:GetCallerIdentity` permission for mocks; it cannot run cleanup. The action
catalogs are validators, not deployable policies or proof of sufficient
permissions. Terraform binds each policy to the SHA-256 of its exact JSON
bytes. The Python verifier returns a canonical document hash instead; do not
substitute that for the Terraform byte hash.

Both verifiers reject wildcard actions, escalation actions and unrestricted
mutation resources. Read-only regional discovery is explicitly catalogued;
it may inspect account metadata but not arbitrary object bodies or secrets.
Mutations require reviewed exact ARNs. The sole dynamic-task exception is
StopTask/DescribeTasks under this cleanup cluster, with an exact cluster
condition. PassRole is limited to the exact cleanup execution/task roles for
ECS. DNS changes are DELETE-only for approved event record names, with null
guards. The runner cannot delete or weaken the state bucket or delete retained
control/state/evidence objects; only exact Terraform `.tflock` objects may be
deleted there. Review control-record/state PutObject scopes separately from
immutable input objects. No account administrator policy is supplied.

The control module uses a dedicated operator VPC/subnets that survive activity
deletion, no incoming network access, and HTTPS egress. Provide working private
egress or the necessary service endpoints and DNS. The module does not create
a VPC, NAT gateway, ECR repository or new state bucket. These prerequisites,
live IAM policy size/behavior and cross-account DNS must be resolved before
enabling. The acceptance booleans are explicit operator attestations, not
automated cloud verification.

All control resources carry `Scope=cleanup-control`; activity cleanup protects
that scope. `workshop_ttl_control.state_resources` models their ownership
separately, and `retirement` permits **planning** only when the record is
cancelled/complete, schedules are disabled, workflows terminal, tasks stopped
and delivery failures reviewed. Review a separate control-state destroy plan,
remove the schedule/workflow/task definition and roles/queue/cluster/SG, and
reconcile it independently. Do not import them into activity deletion state.
Retain versioned control and cleanup evidence under the operator's retention
policy; it has no automatic purge here. One-time schedules request automatic
deletion after delivery, which still requires reconciliation before retiring
their roles.

Future costs include Scheduler delivery, Standard state transitions, Fargate
CPU/memory/time, ECR storage, SQS and S3 requests/storage. The separate operator
network can add ongoing NAT/endpoint costs. No such resources were deployed
for this local phase; do not interpret that as a price or future budget check.
Refresh regional prices, quotas and the full activity/control budget before
reviewing a real deployment plan.
