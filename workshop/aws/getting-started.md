# Operator quick start

Start with the [support matrix](support.md). The first live path is the
SSM-only pilot. Shared HTTPS/Cognito and TTL have separate acceptance gates.
Every live operation needs a current identity, resource ownership, quota,
price/budget and expiry check, followed by a reviewed fresh Terraform plan.

## 1. Obtain and check the source

Clone the repository supplied by the organizer and check out the reviewed
deployment commit. Keep the complete Git history needed by `course_ref`.

```bash
git clone REPOSITORY_URL doris-courses
cd doris-courses
git checkout --detach "$WORKSHOP_SOURCE_SHA"
python3.12 -m venv .venv-workshop
.venv-workshop/bin/python -m pip install -r workshop/aws/preflight-requirements.txt
.venv-workshop/bin/python tools/workshop_config.py validate \
  --config workshop/aws/examples/event.example.yaml
.venv-workshop/bin/python tools/check_workshop_release.py
```

`REPOSITORY_URL` and the source SHA are release inputs, not literal working
credentials. Examples use fictional accounts, AMIs and domains. Offline
validation checks their structure; it does not authorize a deployment or
prove access to those resources.

For the full local regression suite, also install
`workshop/aws/runtime/test-requirements.txt`. Run the repository checks and
tests described in [CONTRIBUTING.md](../../CONTRIBUTING.md). A learner using
the normal local Docker course follows that course's README and needs no
workshop configuration or AWS identity.

## 2. Prepare private activity inputs

Copy the appropriate configuration and pilot examples to a private operator
directory. Fill the expected account, explicit credential profile, Region,
event/owner, learner IDs, course SHA/profile, source/image mode, disk size,
expiry and budget. Keep credential material outside these files.

For a prebuilt image, validate the operator-owned release manifest against
the exact account, Region, AMI, source and profiles. For another account, use
the [partner self-build path](partner-guide.md): build, validate and publish the
image in that account. Image sharing is not required for this delivery. See
[image release](packer/README.md) and [validation](image-validation.md).

For formal shared access, establish DNS ownership/delegation, certificate,
network, independent operator portal and Cognito prerequisites before using
the [shared-access](shared-access.md) and [authentication](cognito-access.md)
flows. Route 53 delegation does not create workload resources or transfer
access to another account's private AMI/state.

## 3. Preflight, review and create

Follow [the first-run runbook](first-run.md) for the explicit-profile online
checks, retained foundation and pilot saved-plan receipt. Use
[the lifecycle CLI](lifecycle.md) to plan, inspect the private plan, approve its
exact hash and run `up`. A changed roster uses
[batch preparation](batch-operations.md) with the previous ready run.

Preserve the run directory and state. Reusing an old plan after source,
configuration, runtime, state or expiry changes is rejected. An enrolled TTL
event additionally requires its current `--ttl-binding`; cancel and reseal a
new revision before roster, replacement or expiry changes.

## 4. Access and verify readiness

`running` is not application readiness. Wait for per-learner status and runtime
checks. The pilot uses operator SSM port forwarding as documented in
[browser access](browser-urls.md); it is not a public shared student entry.
Verify the reader, Notebook/kernel, terminal and the selected course services.
Shared deployments must additionally validate TLS, login/ownership, API and
WebSocket access through the formal entry.

## 5. Diagnose or replace one learner

Use [controlled SSM diagnostics](operations.md). Keep private diagnostics and
saved plans away from student hosts and public repositories. A partial failure
does not make the whole event ready. Single-learner replacement follows the
reviewed-plan procedure in [prebuilt startup](prebuilt.md); rebuilt hosts do
not restore student files or learning progress.

## 6. Clean up and reconcile

Follow [cleanup ownership and reconciliation](cleanup.md) and the lifecycle
destroy procedure using the original activity state and ownership records.
Check residual resources and explicitly retained infrastructure. An empty
state or a failed API query does not prove cleanup. Do not delete shared,
foundation, other-event or published-image resources by name prefix.

External TTL uses the separate [runner control protocol](ttl/README.md).
Its local implementation and image checks do not replace natural-expiry,
least-privilege, concurrent-lock and self-retirement acceptance. Preserve an
unknown operation's fence until reconciliation; there is no automatic
force-unlock or lease stealing.

Retain state, releases and necessary private operational evidence under the
operator's policy. These records manage infrastructure; they are separate
from the [personal progress record](runtime/progress.md) stored on a new learner
runtime. Download that record before expiry to import it manually into another
workshop. Certificates and automatic learner-data backups remain outside this scope.
