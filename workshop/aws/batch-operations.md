# Stable batch preparation

The local batch implementation preserves learner keys, withdrawn allocations,
ALB priorities and retained installation command IDs. Fixtures cover roster
changes, bounded remote SSM scheduling and partial failures. Real shared
TLS/Cognito/service acceptance, full activity cleanup, external TTL and target
scale remain separate acceptance work. This code does not extend the guarded
CLI beyond the existing one-to-five-host SSM pilot.

## Persistent listener assignments

Use [Cognito route generation](cognito-access.md#prepare-and-deploy) with one
private `--allocations` file for the shared listener. Explicitly create it with
`--init-allocations` once; later calls require the existing file and omit that
flag. Keep it alongside the operator's shared state and retain versioned copies.
It contains infrastructure ownership and addresses, with no passwords, subjects,
session tokens or login grants. Never copy it to learner hosts.

Each event reserves an inclusive `--priority-base`/`--priority-limit` interval,
for example 1000–1999; another event might reserve 2000–2999. Intervals cannot
overlap, and the shared root priority cannot fall inside any event interval.
Every service or activity entry uses three priorities. Existing learner/service
keys and the activity entry keep their priorities when a roster is reordered,
extended, reduced or an instance is replaced. Removed hosts become inactive
tombstones, keep their slots and regain those slots if reactivated. New hosts
allocate after all previous slots. Exhaustion fails without recycling withdrawn
assignments or silently extending a reservation.
All active events recorded in the ledger count toward the local 100-rule
ceiling, including activity entries and the shared root rule. Tombstones reserve
priorities without adding active rules. Live quota and actual-rule checks still
apply.

The allocation file is bound to the ALB, account, Region and DNS namespace;
event owner/range changes and ambiguous hostnames are rejected. One local file
lock protects generation and atomic ledger replacement. Reservations are saved
before output files: if writing an output fails, retain the ledger and retry
with fresh output paths. Existing outputs are never overwritten. Generated
route plans also contain a ledger snapshot; the `terraform` object contains
only active routes, not withdrawn rules or other events' bindings.

These local reservations do not discover rules created by other tools or
operators. All operators must coordinate through the same authoritative ledger;
independent copies are not a distributed lock. Check real listener rules, quotas
and the saved Terraform plan before deployment. Keep the ledger until the shared
listener and all activity rules have been retired and reconciled. Never reset a
deployed listener's ledger to work around an allocation error. Route plans from
older versions without a ledger need an explicitly verified migration; this
command does not infer their live assignments.

## Roster-only pilot plans

New pilot plans write a private, hash-bound `allocations.json`. It preserves
inactive learner IDs rather than losing their resource ownership. Starting from
a ready run produced by this version, edit only `learner_ids` in the event
configuration and generate a fresh run:

```bash
python tools/workshop.py plan \
  --config workshop/aws/events/event.local.yaml \
  --pilot workshop/aws/events/pilot.local.yaml \
  --previous-run workshop/aws/.local/event-run-03 \
  --run-dir workshop/aws/.local/event-run-04
```

The previous run must have matching ownership/backend/runtime, bound allocations,
ready nodes, no outstanding commands and unchanged instance assignments. Its
local operation lock is held while the new plan is prepared. The exported plan
is checked for exactly the added/removed learner instances. Changes to retained
instances or shared resources, replacements, deferred/incomplete plans, altered
ownership, runtime/image/expiry changes and missing removals are rejected. A
rejected plan has no usable binding and cannot be applied through this CLI.

Review both the actual saved plan and `roster-review.json`; the receipt is not
an apply grant. Apply the saved plan with its freshly reviewed SHA256 as in the
[lifecycle guide](lifecycle.md). The new run binds a private `resume.json`, keeps
known instance/volume IDs including withdrawals, reuses retained installations
and runs fresh health probes. Newly assigned instance IDs get new installation
records. A removed learner's root disk and temporary work are deleted by the
reviewed Terraform change. No cross-instance learning-state recovery is added.

After applying a roster update, use the newest run for status and cleanup and
retain its predecessors as evidence. Do not concurrently operate older run
directories against the same backend. Terraform still holds its backend lock
for mutations; local run locks do not serialize different operator machines.
Old runs lacking `allocations.json` cannot be adopted through `--previous-run`.
Single-learner replacement retains its separate reviewed-plan flow.

Spare identities in planning manifests remain explicitly unassigned. The live
pilot continues to require `spare_count: 0`; spare creation and promotion are not
implemented or claimed here. Shared activity bindings/gateways/portal are still
separate interfaces. Remove an exiting learner's bindings before deleting that
host, and add new authenticated bindings only with verified assignments. This
pilot CLI does not orchestrate those shared topology operations.

For a shared entry, set `shared_access_file` in the pilot inputs to the private
JSON object exported by shared access. Use the same access output and listener
allocation ledger for each update. The pilot CLI verifies and binds the shared
network; its ready status covers host runtime only.

For a one-learner access update, retain the previous and newly generated route
plans and the bindings backend directory. Review the private saved plan and its
receipt, then apply the exact receipt hash:

```bash
python tools/workshop_bindings_update.py plan \
  --pilot-run workshop/aws/.local/event-run \
  --bindings-run workshop/aws/.local/access-bindings \
  --old-route workshop/aws/.local/routes-old.json \
  --new-route workshop/aws/.local/routes-new.json \
  --output workshop/aws/.local/bindings-review-01
python tools/workshop_bindings_update.py apply \
  --pilot-run workshop/aws/.local/event-run \
  --bindings-run workshop/aws/.local/access-bindings \
  --old-route workshop/aws/.local/routes-old.json \
  --new-route workshop/aws/.local/routes-new.json \
  --output workshop/aws/.local/bindings-review-01 \
  --approved-review-sha256 REVIEWED_RECEIPT_SHA256
```

The command permits one add, removal or target replacement, checks exact route
and listener allocations, state ownership, refreshed identities and OAuth URLs,
and uses the activity fence and Terraform state lock. It updates the bindings
input file only after a successful apply and state check. A partial or unknown
apply requires reconciliation and a fresh review; it is never blindly replayed.
For an enrolled TTL revision supply `--ttl-binding` and cancel that revision
before changing scope. This command currently supports DNS in the workload
account only. Portal assignments, account operations, gateway installation and
authenticated access checks remain separate steps.

To add a learner, create that learner's instance using a roster-only pilot plan,
then generate and review new access bindings for the exact instance IDs. Create
only the new Cognito account with `workshop_accounts.py --learner ID` and a new
private delivery directory. Merge its subject receipt with the retained prior
subjects, regenerate the gateway and portal configuration, install the new
gateway, then verify authenticated access. Do not reset retained accounts.

To remove a learner, first remove its portal assignment and authenticated
bindings with a separately reviewed access plan. Use the prior roster and
subject mapping with `workshop_accounts.py --disable --learner ID --subjects
PATH` to disable only the verified Cognito subject. Existing ALB sessions can
outlive disabling an account, so remove routes and assignments independently.
Then apply the roster-only pilot plan that deletes the learner's instance and
temporary disk. If the disable call fails or its result is unknown, retain the
private operation directory and reconcile the exact user before retrying.

For a replacement, a pilot replacement does not update the access state or
install the new gateway. Reconcile the one changed target attachment through a
fresh reviewed access plan, reinstall the same learner's gateway configuration
on the new host and verify authentication and readiness. Other learner instance
IDs, routes and assignments must remain unchanged.

## Bounded remote work and failure reports

`up`, `status` and `replace` accept `--concurrency 1..16`, default 2. This counts
outstanding startup/install/probe commands in one run, including commands left
by an interrupted invocation. It uses remote SSM concurrency with serialized
state writes. Terraform has its own scheduling and backend lock. A lower limit
does not cancel existing commands; no new command is dispatched until there is
a free slot. Already running work remains visible if it exceeds the new limit.

Pending command IDs are polled and reused. A status query may finish polling an
older installer but never sends an installer. Failed commands require explicit
`--retry-failed` and retain the three-attempt limit. SendCommand uses a separate
SDK client with automatic retries disabled. A lost dispatch response stays
`DispatchUnknown`, blocks new dispatches and requires SSM-history reconciliation;
neither `up` nor `status` silently resends it. Polling and SDK reads retain finite
timeouts/retries. A local timeout does not cancel remote work.

The `batch` report lists ready, failed and pending learner IDs, the configured
limit and in-flight count. Per-node failures are filtered before persistence.
For a shared topology, the report also lists the bound non-secret learner URLs
and marks shared access readiness as not checked. A ready host does not prove
that its target group, gateway or authenticated browser session is ready.
Partial failures leave pending IDs and known billable resources visible and
never produce a ready class. `resources_may_remain` stays true until the owned
resources are reconciled. Runtime readiness continues to report
`course_labs_validated: false`; it does not certify Cognito, browser service
behavior, complete cleanup or a target-size exercise.

References: [ALB rule priorities](https://docs.aws.amazon.com/cli/latest/reference/elbv2/set-rule-priorities.html),
[SSM SendCommand](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_SendCommand.html),
[Run Command consistency](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html).
