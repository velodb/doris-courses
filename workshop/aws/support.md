# Workshop support and validation

The validated AWS course profile is **`rta-main` on Linux x86_64**, using
Python 3.12, `us-east-1`, `m7i.2xlarge` and a 200 GiB root disk. The published
private course image was validated at course commit
`180aca72c993a784a5d9b25e903341a834f14458`. Three-learner shared HTTPS/Cognito startup, concurrent Lab 1, CPU pressure,
single-host replacement and cleanup now have real-AWS evidence in the
[scale rehearsal](scale-rehearsal.md). The pinned image's full main-course Lab
execution was validated separately; the three-host round did not execute all
Labs concurrently.

Browser validation includes the historical operator SSM/token entry and the
formal-domain ALB/Cognito entry, learner ownership and RTA service routes.
Configured-range cleanup and natural TTL have separate acceptance evidence;
the TTL image's production security review remains open. A local check, mock
or kernel probe does not certify a course Lab, browser flow or model conversation.

## Validation levels

| Profile | Configuration | EC2 course execution | Shared HTTPS/Cognito |
| --- | --- | --- | --- |
| `rta-main` | Implemented | Verified within the limits above | Verified; three-learner scope above |
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
- The lifecycle CLI executes a one-to-five-host pilot, including explicitly
  bound shared networks. Three learner hosts were tested live. Shared route,
  gateway and portal operations remain separate commands; no spare promotion
  is implemented. Larger events require their own capacity acceptance.
- Shared access, stable allocation, Cognito and service routing have local tests
  and real integration evidence for the declared RTA scope. The older validated
  AMI still shares a Docker engine ID between clones. A new candidate includes
  the sealing change and passed the fresh-instance audit and full RTA run;
  GUI certification, repeated clone launches and release are still pending.
- The cleanup runner is a separate Linux amd64 image with its own
  [build, verification and scan requirements](ttl/README.md). Its isolated
  Fargate/TTL acceptance does not close its production security review or
  certify another course/image variant.
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

A later private candidate at course/deployment commit
`db2620cb51c29a5fb6577e242dadeb03c15c368e` completed all ten RTA Labs and ten
quizzes (189 code cells) after a Module 9 BE disk-report readiness fix. Its
native Linux x86_64 checks covered 389 tools/runtime tests, five UI tests and
591 shared/course tests (two DOG-specific skips). The shared portal passed
HTTPS and fixed-asset/CORS checks. Window-control failures prevented the new
candidate's GUI certification and release, so it is not an activity-ready
replacement for the older published image. The new three-learner UI round and
remote GitHub Actions run remain pending.
