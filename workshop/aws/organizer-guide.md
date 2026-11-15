# Organizer runbook

Use a reviewed source revision and the exact qualified private release for each
course. Partners first follow [self-built image delivery](partner-guide.md).
Give learners the short [learner guide](learner-guide.md). The operator keeps
AWS credentials, private state, saved plans and account-delivery files.

## 1. Record the event and resource owners

Before creating anything, record the expected account/profile/Region, owner,
event ID, selected course SHA/profiles/release, learner IDs, instance/disk size,
budget, expiry, cleanup buffer and retained-resource policy. Use new run
directories and unique event IDs. Check the current [support matrix](support.md)
for the tested scope. Three learners are the tested target; a larger configured
pilot is not a capacity qualification.

| Lifetime | Resources and private records |
| --- | --- |
| Retained foundation | State bucket, version history and local bootstrap state |
| Retained namespace | Hosted zone and approved parent NS delegation |
| Qualified release | Image, encrypted snapshot, runtime bundle, manifests and validation evidence; retire on its recorded date |
| Shared entry | VPC/subnets/SGs, ALB/listeners/ACM/root aliases and an independent operator portal |
| Each activity | Learner hosts/disks, IAM/SSM resources, Cognito pool/accounts/client/domain, target groups/rules and exact activity DNS records |
| Each learner | Temporary Notebook copies, Lab data, session and local progress file |

Two simultaneous courses use separate activities and per-course instances,
Cognito pools, states, hostnames and origins. Reserve disjoint listener-priority
ranges on the shared ALB and retain the common allocation ledger. Identical
learner names do not create a common account or single sign-on.

## 2. Check identity, costs and the shared entry

Follow [first-run preflight](first-run.md) for identity, AMI, permissions, quotas,
price and budget checks. A budget estimate does not enforce a hard AWS spending
cap. The host's expiry timer terminates the host; full binding/network cleanup
requires the operator or the separately configured external TTL runner.

Build the [shared entry](shared-access.md) in this order:

1. Reconcile the existing delegated zone and retained foundation.
2. Initialize a separate encrypted S3 backend for `terraform/shared-access`;
   review and apply the plan with `portal_instance_id = null`.
3. Export `terraform output -json access`. Use its real VPC, subnet and security
   groups to review and create `terraform/shared-portal` in its own state.
4. Supply that exact operator instance ID to a fresh shared-access plan. Review
   only the expected portal target-group, attachment and listener-rule additions.
5. Export the updated shared output and install the empty portal using
   `tools/workshop_portal_install.py`. Follow the complete command and input
   shapes in [operator-owned course entry](shared-access.md#operator-owned-course-entry).

Verify the exact namespace's apex/wildcard certificate is issued, the ALB is
active, HTTPS serves the empty portal, and direct learner/SQL/SSH ports are not
publicly exposed. ACM retains the TLS private key. Do not use a learner host
as the shared portal or distribute certificate/app-client private keys to it.
Keep shared and portal backends separate from activity states.

## 3. Create learner hosts from the qualified release

Prepare private event and pilot files as described in [prebuilt startup](prebuilt.md).
The event names the exact AMI owner, ID, Region, course SHA, profiles and release.
Pilot settings name the private release file, its original matching runtime
bundle, foundation bucket, shared-access output and browser-route map. Plan
browser origins for the stable roster before startup; never apply placeholder
instance IDs as target attachments.

```bash
.venv-workshop/bin/python tools/workshop.py plan \
  --config "$EVENT_FILE" --pilot "$PILOT_SETTINGS" --run-dir "$PILOT_RUN"
```

Inspect the saved plan and its printed hash: account, event/owner tags, retained
foundation/shared references, roster, AMI, deleted root disks, expiry, zero
public inbound rules and exact resource changes. Then apply the approved file:

```bash
.venv-workshop/bin/python tools/workshop.py up \
  --run-dir "$PILOT_RUN" --approved-plan-sha256 "$PLAN_SHA256" \
  --timeout 1800 --concurrency 3
.venv-workshop/bin/python tools/workshop.py status --run-dir "$PILOT_RUN" --timeout 180
```

`ready` covers runtime and course entry, not complete Lab execution or public
HTTPS/Cognito. Save the real learner-instance assignment from the ready state.
A failed or uncertain apply needs reconciliation and a fresh plan, not replay.
The [lifecycle](lifecycle.md) and [batch guide](batch-operations.md) cover command
resumption, replacement and stable roster changes.

## 4. Bind authentication, gateways and the portal

Follow [Cognito access](cognito-access.md) in order. Generate routes from the
real assignments and the existing shared allocation ledger, prepare the exact
activity `access.tfstate` backend and create bindings with the public gate:

```bash
.venv-workshop/bin/python tools/workshop_bindings_create.py plan \
  --pilot-run "$PILOT_RUN" --bindings-run "$BINDINGS_RUN" \
  --route "$ROUTE_PLAN" --output "$BINDINGS_REVIEW"
# Inspect the complete plan and private review receipt.
.venv-workshop/bin/python tools/workshop_bindings_create.py apply \
  --pilot-run "$PILOT_RUN" --bindings-run "$BINDINGS_RUN" \
  --route "$ROUTE_PLAN" --output "$BINDINGS_REVIEW" \
  --approved-review-sha256 "$BINDINGS_REVIEW_SHA256"
.venv-workshop/bin/python tools/workshop_accounts.py \
  --config "$EVENT_FILE" --identity "$IDENTITY_FILE" --output-dir "$ACCOUNT_DELIVERY"
.venv-workshop/bin/python tools/workshop_gateway_deploy.py \
  --pilot-run "$PILOT_RUN" --plan "$ROUTE_PLAN" \
  --identity "$IDENTITY_FILE" --subjects "$SUBJECTS_FILE"
```

`IDENTITY_FILE` is the binding command's private `identity.json`;
`SUBJECTS_FILE` is the account command's `subjects.json`. Password files are
private and messages are suppressed. Give each account only to its intended
learner through the agreed channel. First-login password changes belong to the
real learner flow; setting permanent passwords through an API is a test fixture,
not proof that the first-login UI works.

Register the exact assignment through `tools/workshop_portal_update.py` using
its current route plan, identity, subjects and the latest common registry.
Serialize portal writes across activities. Keep the returned `next-portal.json`
as the next operation's input; an older single-activity registry cannot replace
a newer multi-activity registry. [Shared access](shared-access.md) documents the
installation and registration commands.

## 5. Accept the entry before handing it out

Use an ordinary browser through the actual HTTPS hosts. Check TLS, sign-in,
learner/course identity, lesson images, selected Notebook/kernel, save/download,
terminal and all selected service pages. Verify actual HTTP/API and kernel/
terminal WebSocket rejection for another learner and another activity. Record
logout scope, reauthentication and recovery after session expiry or idle.
Run API/WebSocket probes on native JupyterLab pages; the reader's CSP prevents
arbitrary fetch/WebSocket probes and must remain intact.

For simultaneous courses, collect a real common execution interval containing
Notebook execution/save/download, terminal commands and native FE/BI/Flink
operations for both activities. Test both directions of service failure and
entry logout while the other activity retains its own valid session and data.
Sequential screenshots and a successful isolated course run do not establish
that combination. Record CPU/memory and response samples as observations,
without a classroom performance guarantee.

Only after acceptance, hand over the activity URL, username, private temporary
password, expiry, selected course/profile, support contact and the learner
guide. Do not advertise an internal port, an old retired URL or a course image
that was never qualified for this account.

## 6. Diagnose, recover and update

Use [controlled diagnostics](operations.md) to distinguish transport/authentication,
gateway, runtime and course-service failures. Preserve the original failed
command and resource IDs. Poll an existing SSM command; never resend an unknown
SendCommand outcome. Do not disable TLS, ownership checks or authentication to
make a diagnostic pass.

A reviewed [single-learner replacement](prebuilt.md) deletes that learner's
Notebook/data/progress disk. Update its separate binding targets and gateway
using new reviewed plans, then recheck authenticated access. Other learners and
other activities retain their own state. Roster changes use the previous stable
allocation ledger; never rename an ID to impersonate an existing learner.

## 7. Clean up, prove absence and review retained costs

Ask learners to download files and progress before expiry. Cancel an enrolled
external TTL revision before manual scope changes. Use the current assignment
and most recent common portal registry with the public cleanup gate:

```bash
.venv-workshop/bin/python tools/workshop_shared_cleanup.py plan \
  --pilot-run "$PILOT_RUN" --bindings-run "$BINDINGS_RUN" --routes "$ROUTE_PLAN" \
  --portal-identity "$IDENTITY_FILE" --portal-subjects "$SUBJECTS_FILE" \
  --portal-registry "$CURRENT_REGISTRY" --output "$CLEANUP_REVIEW"
# Review both exact delete-only plans and the printed receipt.
.venv-workshop/bin/python tools/workshop_shared_cleanup.py apply \
  --pilot-run "$PILOT_RUN" --bindings-run "$BINDINGS_RUN" --routes "$ROUTE_PLAN" \
  --portal-identity "$IDENTITY_FILE" --portal-subjects "$SUBJECTS_FILE" \
  --portal-registry "$CURRENT_REGISTRY" --output "$CLEANUP_REVIEW" \
  --approved-review-sha256 "$CLEANUP_REVIEW_SHA256"
```

It removes only this registered assignment, then activity bindings and hosts,
and performs ownership-scoped reconciliation. Use its returned registry for
the next activity. If the assignment was already successfully removed and
reconciled, omit all three portal arguments. Stopped or missing learners do
not permit deleting another assignment or bypassing the separate destroy proof.
A stale plan or interrupted apply requires new evidence and fresh plans.

After the final activity, follow [shared retirement](shared-access.md#states-and-deployment-order):
clear the portal reference in a fresh shared plan, delete the owned portal
bindings, retire the separate portal host, then destroy the remaining shared
entry. Do not delete the long-lived zone, foundation or a retained release as
part of activity cleanup.

An empty state is one proof, not the final absence check. Reconcile exact known
EC2/root-volume/ENI, IAM, SSM, runtime buckets, Cognito, target groups/rules and
DNS references with native APIs. Record what was deleted, what remains and its
owner/retention date. Explicitly retire unneeded exact AMIs and their unreferenced
snapshots. Close operator SSM/browser sessions and remove temporary credential
files after the identity and runtime resources are gone.

See [cost and validation observations](costs.md). Keep foundation state versions,
DNS and image storage costs in the retained-resource report. `ExpiresAt` tags do
not delete images, snapshots or the entire shared entry.

## Account for the shared-resource deadline

An activity's instance expiry does not retire its Cognito/DNS/ALB bindings,
shared portal and ALB, or image snapshots. Assign an explicit retirement time
and operator for those resources, allow time for reconciliation, and record the
actual completion timestamp. The 2026-11-14/15 validation required overdue
shared-resource cleanup; its instance timers did not prove an eight-hour global
retirement. After an authorization expires, perform cleanup and obtain a new
explicit budget/window before creating validation resources again.
