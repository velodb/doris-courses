# Workshop support and validation

The validated AWS course profile is **`rta-main` on Linux x86_64**, using
Python 3.12, `us-east-1`, `m7i.2xlarge` and a 200 GiB root disk. The published
private course image was validated at course commit
`180aca72c993a784a5d9b25e903341a834f14458`. Two-host startup, repeated startup,
single-host replacement and manual cleanup have historical real-EC2 evidence.
See [prebuilt startup](prebuilt.md) and [the pilot runbook](first-run.md).

Browser validation used operator SSM forwards, including a temporary
single-host HTTPS/token entry. Formal-domain ALB/Cognito access, shared service
routing, complete activity cleanup, natural TTL and target-scale acceptance
remain pending. A local check, mock or kernel probe does not certify a course
Lab, a real browser flow, a model conversation or cloud permissions.

## Validation levels

| Profile | Configuration | EC2 course execution | Shared HTTPS/Cognito |
| --- | --- | --- | --- |
| `rta-main` | Implemented | Verified within the limits above | Pending |
| `rta-metabase` | Implemented | Unverified | Pending |
| `dw-main`, `dw-kafka`, `dw-cdc`, `dw-bi`, `dw-backup`, `dw-extensions` | Implemented | Unverified; delivery scope not selected | Pending |
| `obs-main`, `obs-local-model`, `obs-openai` | Implemented | Unverified; delivery scope not selected | Pending |
| `ai-mcp`, `ai-functions` | Draft configuration | Unverified; scope, model access and quotas pending | Pending |

`ai-mcp` requires a separately implemented browser/cloud Host. A stdio MCP
probe is not a complete conversational Host. The AI course remains a preview;
the workshop configuration does not change its publication status.

[support-matrix.json](support-matrix.json) records each declared profile,
kernel, validation state, course source and public summary. Run
`python tools/check_workshop_release.py` from the repository root to check its
coverage and public dependency boundaries. Changing a validation claim requires
new evidence for that scope; the checker validates consistency and cannot
perform cloud or browser acceptance.

## Platforms and deployment modes

- EC2 runtime and AMI validation cover Linux x86_64 and the stated Region and
  instance type. Arm64 EC2, other Regions/types and optional course combinations
  need their own validation.
- The lifecycle CLI currently executes a one-to-five-host SSM-only pilot with
  no spare promotion. Two hosts were tested live. The upper configuration limit
  is not evidence that five hosts, a larger event or shared authentication passed.
- Shared access, stable allocation, Cognito and service routing have local
  implementations and tests. Follow their explicit pending acceptance limits.
- The cleanup runner is a separate Linux amd64 image, with its own
  [build, verification and scan requirements](ttl/README.md). Its local success
  does not certify ECS/Fargate, natural expiry or the course AMI.
- A partner uses its own account, credentials, state and image/installation
  source. The current private AMI is not a public cross-account dependency.

## Local courses and content identity

The [normal course setup](../../README.md#before-you-start) remains available
without AWS credentials, a workshop domain or the operator CLI. Each course
has its own virtual environment and kernel. Keep the shared runtime installed
through the course's documented requirements; do not combine the courses'
same-named Python packages in one environment.

Published course IDs, completion rules and certificate metadata remain in the
[content contract](../../contracts/v1/schema.json). The temporary workshop
reader independently hides progress, persistent grading and course-certificate
actions. It does not add a learning-record API/database or rewrite the normal
website's metadata. Notebook edits and experiment data remain temporary, and
manual Notebook download is available while the activity exists.

## Release checks

Use the repository checks in [CONTRIBUTING.md](../../CONTRIBUTING.md), plus the
workshop release checker. Run offline tests in separate clean environments for
the tools/runtime, shared package and every course. A full clone contains the
pinned course history; an archive or shallow clone may need that history
fetched explicitly before bundle generation.

Record source SHA, dependency versions, platform, test counts/skips and scope.
Fresh environment validation must install dependencies into new environments,
without copying a developer's virtual environment, private configuration,
state, credentials or local planning files. Internet downloads during setup
are distinct from offline tests. Unit tests do not run the Docker Labs.

GitHub Actions checks the support matrix, public links, private-file exclusion,
content identities against the event's base commit, Notebook outputs and
offline tests. A local run does not mean remote CI ran or a release was
published. Cloud, browser, optional-course and external-partner acceptance
remain explicit release gates.

The 2026-10-31 clean-install check ran 589 shared/course tests on Linux amd64
(two conditional DOG-checkout tests skipped) and 306 tools/runtime tests on
native Linux arm64. An amd64-emulated run on an arm64 Mac reproduced three
kernel/WebSocket timeouts, including with a vanilla kernel; the same runtime
tests passed natively. This resembles a
[reported Docker Desktop/Rosetta issue](https://github.com/docker/desktop-feedback/issues/592),
but does not establish its exact cause. Preserve that failed run and confirm
the current runtime suite on native amd64 CI. This arm64 test result does not
add arm64 EC2/AMI support to the matrix above.
