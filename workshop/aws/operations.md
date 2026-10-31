# Infrastructure diagnostics and log handling

Use the activity's original checkout, bundle and private run directory with the
explicit AWS profile. No SSH ingress or key pair is required. The CLI checks
the account, bound Terraform state, activity ownership tags and each live EC2
learner assignment before sending a command to an exact instance ID.

```bash
umask 077
.venv-workshop/bin/python tools/workshop.py diagnose \
  --run-dir workshop/aws/.local/event-run-03 --learner s001 --timeout 300
```

Omit `--learner` to inspect all managed nodes in the pilot. Each invocation gets
fresh results. A pending command resumes by its saved ID after a local timeout;
an uncertain dispatch is never resent automatically. Resolve that command in
SSM history first. The wait limit applies to each node, and does not cancel an
already dispatched command. Node execution is bounded to 300 seconds. SDK
reads also have finite retries/timeouts.

The report contains fixed unit states for cloud-final, Docker, Jupyter, gateway
and the expiry timer; pinned version checks; authenticated loopback, course
entry and synthetic kernel checks; and category counts from bounded installer
logs. It reports an absent gateway as `not-installed`. An installed gateway is
`installed-unverified`: ALB/TLS/Cognito and real browser checks are separate.
The command exits zero when collection succeeds, even when runtime checks fail.
Inspect each node's `runtime.ready` and `checks`. It never reports class-wide
shared-access readiness. Labs are not run by this command.

For initialization problems, inspect `units.cloud-init` and `logs.cloud-init`
or `logs.image-start`. Installation categories appear under `logs.bootstrap`.
Use `units.docker`, `units.jupyter` and individual runtime/kernel checks to
separate service failures from interpreter or authentication failures. For an
offline node, check its EC2 state, instance profile, SSM Agent and HTTPS egress
using the AWS console; no command output can be collected while SSM is offline.
Keep the original activity run for `status`, recovery or `destroy`.

## Install the policy on an existing validated image

New source-based runtime installs include the log policy. Older validated AMIs
keep their original course/runtime fingerprints. Install the versioned operations
overlay on a fresh activity host before learners connect:

```bash
.venv-workshop/bin/python tools/workshop.py configure-logs \
  --run-dir workshop/aws/.local/event-run-03 --learner s001 \
  --retention-days 7 --timeout 300
```

This is a maintenance operation: it restarts active Jupyter/gateway services,
interrupting kernels and connections. Repeating it is supported. It checks the
prebuilt host's event/learner marker before changing files, records the overlay
SHA256, installs a root-owned policy/prune timer and bounds system journal
storage. It does not rebuild or relabel an AMI. Install it before the access
gateway overlay, whose installer requires the safe logging helper.

## Content, permissions and retention

Operational output uses an allowlist. Raw tool text is discarded before installer
stdout or log persistence; only known stages and categories (network, permission,
disk, dependency, error, completed or suppressed) remain. Terraform human output
is replaced by a return code and `raw-output-suppressed`. Structured Terraform
state is processed in memory; private saved plans/state remain operational inputs.
SSM reports are projected again on the operator side, discarding unexpected
fields and strings, even in valid JSON. No command accepts arbitrary shell input.
Image-build and validation invocation/fault records also discard SSM output bodies.
Course-validation failure summaries discard exception messages; executed validation
Notebooks remain private artifacts on their disposable validation host.

Jupyter and gateway stdout/stderr are disabled in systemd, including HTTP request
paths and error tracebacks. Fixed unit failure state remains available. Diagnostics
do not read student Notebooks, terminal input, container logs, database queries,
Cookies, environment, user-data or state into evidence. Kernel checks execute
fixed synthetic probes with history disabled. This creates no learning progress,
quiz score, certificate or student behavior event stream. Course-owned application
files and manually enabled logging remain the course/operator's responsibility;
do not upload them as infrastructure evidence.

| Storage | Access and bound | Retention and cleanup |
| --- | --- | --- |
| Node operational logs | root directory `0700`, files `0600`; safe categories only | Default 7 days; 1 MiB per file plus two rotations; hourly prune and logrotate; deleted with root EBS |
| System journal | OS root/journal access; Jupyter/gateway output suppressed | 32 MiB cap for persistent and runtime journal, 7-day maximum age; configured with the same retention days |
| Operator `diagnostic-*.json` and `terraform-*.log` | private run, new files `0600`; fixed summaries only | Default 7 days, at most 20 files and 10 MiB total per run; pruned on CLI writes, not by a background local daemon |
| SSM command metadata/output | AWS operator IAM; diagnostic command contains only code and non-secret bindings; no S3/CloudWatch output destination | AWS retains execution history for up to 30 days; local run retention does not change this service policy |
| Plans, state, lifecycle/command IDs, release manifests | private operator storage/backend; inaccessible to the learner role | Preserved for ownership, recovery and reconciliation; summary pruning never deletes these inputs |

The 7-day default provides a short incident-review window after a workshop of
up to 24 hours. It is an implementation default, not an organization-approved
retention policy. Choose 1–30 days with `--retention-days`; use `configure-logs`
to apply it on nodes and `diagnose` for local summaries. Quiet local directories
need explicit cleanup after incident review; retention is enforced on the next
write. Node logrotate's age/size checks are periodic, while the installer stream
writer rotates at the file limit. General OS/SSM Agent logs have their own OS/agent
rotation and are never exported by this tool. Existing application journal entries
are not retroactively redacted by installing the policy.

Only the operator may collect diagnostics. Use the deployment identity's bounded
SSM command and read permissions; do not give the learner role `SendCommand`,
session start, state access or log-reader privileges. Learner Docker access is
effectively node root access, so root file modes are not an isolation boundary
against that learner. The operator's external state and AWS permissions remain
the ownership boundary. A node administrator can tamper with node diagnostics;
the projection prevents content export but is not remote attestation.

No CloudWatch log group, agent, S3 log bucket or interactive session recorder is
created. Summaries use the existing encrypted root disk, private operator disk
and SSM transport. There is no new hourly logging resource; EC2/EBS/IP, API and
existing backend storage/transfer charges still apply. Activity destroy removes
node logs with its root disk. It preserves operator evidence, foundation state
and released AMI/snapshot; those have separate owners/retention/storage costs.

## Verified scope

On 2026-10-31, a fresh single-node `m7i.2xlarge`/200 GiB activity in `us-east-1`
used a pinned validated course AMI plus this independent operations overlay.
Real SSM checks located stopped Jupyter, a broken course interpreter and stopped
Docker, then verified recovery. Dummy password/Cookie/token/model-key input was
discarded from operational logs and reports. File modes, journal output suppression,
logrotate configuration, expiry pruning, repeated policy installation and final
CLI status/diagnostics passed. The 15 activity resources were destroyed; repeated
destroy and exact-resource API reconciliation passed, retaining foundation and
the previously released AMI/snapshot. The absent gateway stayed `not-installed`.
Source-install integration and image fault-record filtering have offline coverage;
this run did not rebuild an AMI, rerun all course Labs or accept shared TLS/Cognito.

AWS references: [Run Command history and sensitive parameters](https://docs.aws.amazon.com/systems-manager/latest/userguide/running-commands.html),
[command output destinations and limits](https://docs.aws.amazon.com/systems-manager/latest/userguide/sysman-rc-setting-up-cwlogs.html),
[SendCommand](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_SendCommand.html).
