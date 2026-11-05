# Three-learner scale rehearsal

## Current private-image acceptance

On 2026-11-05, private release `rta-db2620c-ui-v1`, pinned to course commit
`db2620cb51c29a5fb6577e242dadeb03c15c368e`, passed a new three-learner
`rta-main` round in `us-east-1`. Each learner used Linux x86_64,
`m7i.2xlarge` and a 200 GiB encrypted gp3 root disk. The portal used a separate
`t3.small` with a 20 GiB root disk. There were no spares.

| Exercise | Result |
| --- | --- |
| Image and clone identity | Fresh-image audit passed; all three Docker engine IDs, machine IDs and token identities were distinct |
| Concurrent Lab 1 | Three Notebooks, 12 code cells each, zero execution errors; 184.96 seconds of common execution overlap |
| Authenticated browser access | Three isolated sessions saw exactly their own five origins, executed Notebook SQL returning 42, ran terminal Docker commands and logged in to their own FE |
| Cross-learner HTTP/API | All six directed pairs returned 403 for five page, status, contents, kernel and terminal paths: 30 denials |
| Cross-learner WebSockets | All 12 kernel/terminal handshakes failed; all six owner controls opened successfully from the same document type; failed-handshake HTTP status was not exposed |
| Entry logout and reauthentication | All three reached the branded signed-out page, required Cognito login and returned to the correct assignment; other service origins retained their own sessions |
| Single-service fault and recovery | Only the third learner's Jupyter was stopped: its API returned 503 while the other two stayed at 200 with their original kernels, processes and terminals. Restart restored SQL and Docker access without replacing an EC2 instance |

The same image passed all ten Labs and ten quizzes (189 code cells) and six
actual browser checks on a separate fresh validation instance before private
publication. The three-host round did not run every Lab concurrently, repeat
CPU pressure or replace a host. Its disposable accounts had administratively
prepared passwords; first-login password changes and hosted-login branding
were not part of this round. Remote GitHub Actions is a separate release gate.

## Historical pressure and replacement rehearsal

On 2026-11-04, a three-learner `rta-main` event was exercised in `us-east-1`.
Each learner had an independent Linux x86_64 `m7i.2xlarge` instance and a
200 GiB encrypted gp3 root disk. A separate `t3.small`/20 GiB portal and
shared ALB served the authenticated entry. No spares were provisioned.
The course image pinned commit `180aca72c993a784a5d9b25e903341a834f14458`;
operator code was `6c82de1ae7b107b13810150cf67bb75133472a31`.

## Observed results

| Exercise | Result |
| --- | --- |
| Guarded `up --concurrency 3` | 198.744 seconds including live preflight; all three startup/probe commands succeeded on their first attempt |
| EC2 launch to successful readiness probe | 93.184, 95.337 and 105.493 seconds; 11 checks per host |
| Concurrent Lab 1 | Three Notebooks, 12 code cells each, no execution errors; 158.369–162.234 seconds per Notebook |
| Authenticated access | Three independent Chromium contexts completed Cognito sign-in and password change; each executed SQL returning 42 in its assigned kernel, used a terminal to run Docker, and reached its own FE |
| Portal assignments | Each account saw exactly its own five service origins |
| Cross-learner access | All six directed pairs returned 403 for five page/API paths; none established a terminal WebSocket; repeated successfully after replacement |
| Two-minute CPU pressure | Eight workers on one learner host; sampled CPU mean 99.98%, peak 100%. Other hosts peaked at 2.22% and 1.67% |
| Operator access | The SSM diagnostic dispatched during pressure completed successfully in 18.087 seconds |
| Service fault | Stopping one learner's Jupyter produced API 503 and a failed readiness result for that learner; the other APIs stayed at 200 |
| Single-host replacement | Only the selected EC2 changed. Guarded replacement took 203.623 seconds including preflight; the new host reran Lab 1 successfully |
| Retained learners | Instance, root-volume and ENI IDs and marker-file hashes stayed unchanged; their original kernel and terminal IDs remained usable |

The retained-session monitor ran for 1049.157 seconds, completing 234 rounds
and 468 successful SQL executions. It checked authenticated API access and
reconnected closed sockets 16 times while retaining the same kernel and
terminal IDs. Query response polling had a 303 ms median and a 1211 ms maximum;
these times exclude reconnection setup. This exercise does not establish a
maximum uninterrupted WebSocket lifetime.

During pressure, sampled available memory stayed above 27.6 GiB on every host.
The observed network counters and disk headroom are specific to this short
exercise. These measurements cover cached-image startup, concurrent Lab 1,
bounded CPU pressure and replacement. Full-course execution on the pinned image
has separate historical evidence; simultaneous execution of every Lab on all
three hosts was not part of this rehearsal.

## Prepare a rehearsal

Confirm learner count, selected profiles, instance/disk size, Region, maximum
duration, cleanup buffer and budget. Use a fresh event ID, future expiry and new
run directories. Reuse a validated image release and the authoritative shared
listener ledger. Preserve the deployment checkout and every reviewed artifact.
Follow the [shared-access](shared-access.md), [prebuilt-image](prebuilt.md) and
[batch](batch-operations.md) procedures; host readiness and shared authenticated
access have separate verification steps.

Check current vCPU, gp3, subnet-address, ALB-rule and target-group headroom, as
well as current prices. Three RTA learners require 24 learner vCPUs and 600 GiB
of learner root storage, plus the portal. Five service origins per learner,
an activity entry and a shared root entry require 49 non-default listener rules
with the current three-rules-per-origin layout. The
[AWS ALB quota reference](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-limits.html)
explains the account limits; query the actual account before deployment and
include other events sharing the ALB.

Apply the reviewed pilot plan with explicit remote concurrency:

```bash
python tools/workshop.py up \
  --run-dir workshop/aws/.local/rehearsal-run \
  --approved-plan-sha256 REVIEWED_SAVED_PLAN_SHA256 \
  --concurrency 3 --timeout 1800
```

This CLI limit controls outstanding per-host SSM work in the run. Terraform
scheduling is separate. AWS also documents the service's
[Run Command concurrency controls](https://docs.aws.amazon.com/systems-manager/latest/userguide/send-commands-multiple.html).
Record per-host readiness, attempts and timestamps. A running EC2 or healthy
ALB target alone does not prove authenticated course access.

Use isolated browser contexts for every learner. Verify the assigned host using
its kernel's event/learner identity, execute a query and a terminal command,
then exercise all directed cross-learner pairs. Include page, contents, kernel,
terminal and WebSocket paths. Preserve status codes and failed handshakes
without saving passwords, Cookies or identity tokens in ordinary evidence.

Sample aggregate host CPU, available memory, disk headroom and network counters
before and during a bounded workload. Keep the workload and sample timestamps
so another operator can identify their overlap. Retain file checksums and
kernel/terminal IDs for unaffected learners. These are infrastructure test
records; the workshop does not add learning-progress or grading storage.

For a failure, use [SSM diagnostics](operations.md), record accurate failure
status, and [review a single-learner replacement](prebuilt.md).
After replacement, reconcile its target attachment and install its gateway as
[the batch guide](batch-operations.md) describes. Recheck portal assignments,
authenticated execution and other learners' identities, disks and files.
The replaced learner's temporary work is lost.

## Cleanup and cost boundary

Use [reviewed activity cleanup](cleanup.md) to unregister the activity from the
portal, destroy its access bindings and pilot resources, then collect fresh
state and resource-scan evidence. A dedicated test portal and shared entry need
their own retirement: detach the portal while its host still exists, destroy
the portal, then retire the remaining shared entry. Retained partner DNS,
foundation state and published image artifacts have separate ownership.

In the historical 2026-11-04 round, 73 access-binding and 12 pilot resources were deleted through
the reviewed cleanup command. Its fresh scan completed all 26 configured
resource categories with zero residuals and ownership conflicts; nine stale
Tagging observations were retained with exact native-API confirmations.
The dedicated portal attachments, four portal resources and remaining 17
shared-entry resources were then retired separately. Fresh pulls of all four
states contained no managed resources. The final API audit confirmed five
historical instances terminated and 85 exact resource-absence checks, including
five root volumes and seven ENIs. The partner zone retained only NS/SOA;
foundation state, the published AMI and its snapshot were retained. This
reconciliation covers this event and its dedicated entry.

The shared-resource window lasted 1.404252 hours. Scaling the quoted capacity
and usage allowances to that window, with an additional replacement disk/address
allowance, gives 3.511958 USD. Including the original 15.50 USD fixed contingency
and retained DNS/snapshot assumptions gives 19.011958 USD. These are planning
models; consumption and invoice amounts were not measured by Cost Explorer.

The 25-hour planning model was 52.871738 USD against a 100 USD budget. It
included a 24-hour maximum plus one cleanup hour, shared entry, conservative
usage allowances, contingency and retained DNS/snapshot assumptions. It is an
estimate and does not cap AWS charges. Cost Explorer denied the billing-tag
query, so a settled activity bill was unavailable.

The older image used in the historical rehearsal duplicated Docker engine IDs across clones.
This rehearsal used EC2 IDs, disks, event/learner bindings and authenticated
routes for ownership checks. The newer private image passed its rebuild,
fresh-instance audit and three-clone identity checks described above.
Use the [support matrix](support.md) for the remaining release boundaries.
