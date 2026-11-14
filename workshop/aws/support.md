# Workshop support and validation

The validated RTA profile is **`rta-main` on Linux x86_64**, using
Python 3.12, `us-east-1`, `m7i.2xlarge` and a 200 GiB root disk. The published
private course image release `rta-db2620c-ui-v1` was validated at course commit
`db2620cb51c29a5fb6577e242dadeb03c15c368e`. Its fresh three-learner shared
HTTPS/Cognito round passed concurrent Lab 1, browser isolation, logout and
single-service recovery. The [scale rehearsal](scale-rehearsal.md) records this
round and the older CPU-pressure and host-replacement exercise separately.
The pinned image's full main-course Lab execution was validated on a separate
fresh instance; the three-host round did not execute all Labs concurrently.

Browser validation includes the historical operator SSM/token entry and the
formal-domain ALB/Cognito entry, learner ownership and RTA service routes.
Configured-range cleanup and natural TTL have separate acceptance evidence;
the TTL image's production security review remains open. A local check, mock
or kernel probe does not certify a course Lab, browser flow or model conversation.

The complete six-profile Data Warehousing selection also passed independent
EC2 course/browser validation and private publication. Its three-learner shared
acceptance and temporary-resource cleanup also passed; see
[the recorded scope](data-warehousing.md#current-acceptance).

The combined English `obs-main` and `obs-local-model` selection also passed
independent validation, private publication and three complete shared course
runs. Its browser, resource and external-client delivery limits are recorded in
[observability acceptance](observability.md#current-acceptance).

The combined RTA and Data Warehousing round is
[partially validated](#dual-course-validation); its remaining checks are separate
from the single-course results above.

## Dual-course validation

The 2026-11-13 combination remains **partially validated**. Course and operator
source were fixed at `0ade736bc8596386658d5d9a891b1e6c3e6358b7`, using the English
schema-v1 selection: `rta-main` and all six Data Warehousing profiles. Three
learners each had two separate `m7i.2xlarge` instances with 200 GiB root disks in
`us-east-1`, behind one shared HTTPS/Cognito portal and ALB.

Both temporary private images passed fresh-instance course, browser and cleanup
gates before activity creation. All six activity instances completed their full
selected courses: 162 Notebooks and 1,179 code cells. Six native Notebook
executions, saves, downloads and Docker terminal operations passed. SQL and file
checks on the original kernels overlapped for 89.82 seconds, with 18 checks per
instance and terminal commands during that interval. FE, BI, Flink and
save/download operations were not all validated in that same interval.

Twelve directed cross-learner cases rejected 48 HTTP/API requests and 24 kernel
or terminal WebSocket connections. Six cross-activity cookie replay cases were
also rejected. Manual progress import into a new activity passed for each
course, without restoring Lab data.

Shared service GUI coverage was partial. RTA FE passed for one learner; two
learners had loading or authentication failures. DW FE navigation, three
Superset dashboards and Flink overview counters were observed. Some FE system
tables were empty and Task Managers navigation was not established. Both course
containers were stopped and restarted, but complete browser coverage of the
other course during both faults was not obtained. RTA entry logout with an
independently valid DW session still needs a complete repeat. DW entry-only
logout and reauthentication passed separately.

Initial probes incorrectly ran API and WebSocket requests from the reader page,
whose CSP blocks those requests. Corrected probes on native JupyterLab pages
verified the original DW kernels, files, SQL markers, APIs and terminal sockets.
After RTA resource deletion, all three DW learners retained those original
kernels and data, and reconnected their terminals; the portal listed only DW.

Both activities, the dedicated shared entry and the two temporary image/snapshot
pairs were retired. Exact AWS checks confirmed twelve known instances retired
and their root volumes absent; six remote and five local state files were empty.
The existing OBS image/snapshot, long-lived DNS zone, foundation and original
dataset versions were preserved. Test credentials were deleted and the browser
was closed.

This round does not qualify multilingual schema-v2 bundles, cold external-client
loading, first-password changes, MFA, larger classrooms, performance guarantees
or remote GitHub Actions.

## Validation levels

| Profile | Configuration | EC2 course execution | Shared HTTPS/Cognito |
| --- | --- | --- | --- |
| `rta-main` | Implemented | Verified within the limits above | Verified; three-learner scope above |
| `rta-metabase` | Implemented | Unverified | Pending |
| `dw-main`, `dw-kafka`, `dw-cdc`, `dw-bi`, `dw-backup`, `dw-extensions` | Implemented | Verified together; [exact scope](data-warehousing.md#current-acceptance) | Verified; three learners, full selection and scoped cleanup |
| `obs-main`, `obs-local-model` | Implemented | Verified together; [exact scope](observability.md#current-acceptance) | Verified for three learners using AWS-hosted Chromium; external-client delivery remains unqualified |
| `obs-openai` | Declared; image path refused pending model/credential/spending contract | Unverified | Pending |
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

The current partner delivery path is [an image built and qualified in the
partner's own account](partner-guide.md). The [organizer](organizer-guide.md),
[learner](learner-guide.md) and [cost](costs.md) runbooks cover this path. Image
sharing is not a delivery prerequisite.

The [source image readiness workflow](image-sharing.md) has offline tool and
Terraform mock coverage. Publisher qualification covers an enabled CMK-encrypted
source copy, private permissions and a recipient handoff; actual source cloud
readiness is pending. A [recipient operates their own copy and workshop](image-sharing-recipient.md)
using their own credentials and the existing publication gates. Those external
results are unverified until reported and are separate from publisher acceptance.
The same-account results above do not certify cross-account delivery.

- EC2 runtime and AMI validation cover Linux x86_64 and the stated Region and
  instance type. Arm64 EC2, other Regions/types and optional course combinations
  need their own validation.
- The lifecycle CLI executes a one-to-five-host pilot, including explicitly
  bound shared networks. Three learner hosts were tested live. Shared route,
  gateway and portal operations remain separate commands; no spare promotion
  is implemented. Larger events require their own capacity acceptance.
- Shared access, stable allocation, Cognito and service routing have local tests
  and real integration evidence for the declared RTA, complete Data Warehousing
  and combined Observability scopes. The older RTA AMI shares a Docker engine ID
  between clones. The newer RTA release includes
  the sealing change, passed the fresh-instance audit, full RTA run and browser
  gate, and produced three distinct Docker engine, machine and token identities.
  Its new three-learner authenticated browser round passed all six directed
  cross-learner HTTP/API and kernel/terminal WebSocket isolation checks,
  entry logout/reauthentication and single-service failure/recovery.
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
[content contract](../../contracts/v1/schema.json). New source bundles add
[instance-local progress](runtime/progress.md) and manual JSON transfer without
changing the content contract or normal website records. Fresh English schema-v1
RTA/DW images exercised progress and manual transfer in the partial dual-course
round above. This result does not qualify multilingual bundles or older images.
Certificates and a long-term learning service remain outside the workshop.
Notebook edits and experiment data remain temporary, and manual Notebook
download is available while the activity exists.

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

A later private image at course/deployment commit
`db2620cb51c29a5fb6577e242dadeb03c15c368e` completed all ten RTA Labs and ten
quizzes (189 code cells) after a Module 9 BE disk-report readiness fix. Its
native Linux x86_64 checks covered 389 tools/runtime tests, five UI tests and
591 shared/course tests (two DOG-specific skips). On a separate fresh instance,
the same image passed the full course again and all six actual browser checks:
reader/images, Notebook create/run/save/download, kernel restart/reconnect,
quiz navigation/feedback, terminal Docker access and an FE query-profile detail.
That validation environment was destroyed and reconciled before private release.

The new shared portal passed HTTPS and fixed-asset/CORS checks. Three clones
passed identity checks and concurrent Lab 1 with 184.96 seconds of common
execution overlap. Each account saw only its own five origins and passed
Notebook SQL, terminal Docker and FE login. All six directed cross-learner pairs
returned HTTP 403 for five page/API paths and failed both WebSocket handshakes,
with successful owner controls. The browser test did not expose failed-handshake
HTTP status. Three entry logout/reauthentication flows passed; other service
origins retained their own sessions. Stopping and restarting one learner's
Jupyter preserved the other two original kernels, processes and terminals.
First-login password changes and hosted-login branding were not tested in this
round. An operator-only signed-out-route review fix at `ab02729` passed
391 local tools tests and seven focused native Linux x86_64 tests; it does not
change the image payload or its source identity. Track remote GitHub Actions
separately from these local and cloud results.

The new activity and its dedicated portal/shared entry were fully retired.
The activity's 26-category scan found no residuals or ownership conflicts;
a final scoped API audit confirmed five empty states, five terminated instances,
five missing root volumes and 80 exact resource-absence checks. Published images,
encrypted snapshots, partner DNS and foundation state were retained. See the
[current acceptance summary](scale-rehearsal.md#current-private-image-acceptance)
for the cleanup sequence and scope.
