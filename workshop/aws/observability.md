# Observability workshop validation

The image pipeline accepts `obs-main`, optionally with `obs-local-model`.
The combined English selection has passed the independent and three-learner
acceptance described below. Other selections need their own evidence.

## Current acceptance

Private release `obs-4f401d1-full-v1`, at course and deployment commit
`4f401d1c91220fc736a395a510e7660ae88836fe`, passed on Linux x86_64,
Python 3.12, `us-east-1`, `m7i.2xlarge` and a 200 GiB root disk on
2026-11-13 (Asia/Shanghai). This covers English legacy schema-v1
`obs-main` **together with** `obs-local-model`.

One fresh independent instance passed 29 image/runtime checks, all 17 Notebooks
and 148 code cells, and 14 actual browser checks before private publication.
Three fresh shared clones then each passed the same complete selection:
51 Notebooks and 444 code cells in total. Their Docker engine, machine and token
identities were distinct. Three isolated Chromium contexts passed normal
Cognito login, native Notebook save/download/restart/reconnect, quiz feedback,
terminal execution, FE profiles, all six browser checkpoints, Grafana Live,
Litefuse simulated and local-model traces, and the seven-email alert cycle.
Browser telemetry returned actual HTTP 200 responses; all 46 captured trace IDs
were found in the respective Doris instances, with 102 spans.

Shared ownership checks covered all six directed learner pairs, including
HTTP/API and kernel/terminal WebSocket denial, the five additional application
origins, and entry logout/reauthentication. Each original CPU load measured
about 99.8% busy while its entry returned HTTP 200 and its existing browser
kernel returned SQL 42. Stopping the five applications left each reader and
terminal available; restarting the same containers retained dashboards, flags,
model traces and data volumes. Course teardown removed all course containers,
networks and Docker volumes and verified log ownership. All temporary cloud
resources were retired and reconciled; the private release remains retained.

The shared runs took about 34–35 minutes. They sampled at least 17.5 GiB of
available memory and 164.6 GiB of free disk. These are observations for the
stated configuration, not minimum sizing or a performance guarantee. A temporary
browser on one learner host added two CPU and a 3 GiB memory limit, raised to
5 GiB after browser OOM kills. Transient Jupyter save conflicts, stale Grafana
pages and crashed browser pages required recorded recovery. Original course
commands were not replayed, and failed attempts remain separate evidence.

Browser acceptance used Chromium 156 hosted in AWS. An earlier macOS access
attempt received a truncated Grafana JavaScript asset; its cause remains
unresolved, so external-client delivery is not qualified by this round.
First-password-change and MFA UI, natural session expiry, long idle periods,
translated/schema-v2 content, other selections, browsers, Regions, instance
types and larger classrooms remain outside this acceptance. Litefuse also sets
a PostHog analytics cookie on the parent site domain; analytics privacy and
isolation were not certified. Mailpit's temporary inbox can reset on restart;
the full alert cycle was captured before the restart.

A partner builds and validates an image in its own account. The retained private
AMI is not a public dependency: deployment currently requires matching account
ownership and no launch grants. An AMI encrypted with the default AWS-managed
EBS key cannot be shared directly across accounts; a distribution workflow
would need a customer-managed KMS key and recipient-owned validation.
See [AWS AMI sharing rules](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sharingamis-explicit.html).

## Additional external-client attempt: 2026-11-15

A fresh clone of the retained release passed its 29 initial checks, including
exact system packages with the prebuilt automatic-APT freeze. An external macOS
Chromium 153 client passed hosted first-password change, assigned entry, reader
images/read state, native Notebook save/download, terminal/sockets, kernel
restart/reload and quiz feedback. The first browser checkpoint also passed.
Grafana and the remaining checkpoints did not finish; the original full-course
command ended Failed at 10:02 UTC. This does not extend external-client
qualification. See the [current support record](support.md#dual-course-validation)
for the network/sign-in failures and overdue cleanup. The earlier three-learner
AWS-hosted acceptance above retains its original scope.

## Selection and dependencies

`obs-main` contains all eight main modules: eight Labs and eight quiz Notebooks.
`obs-local-model` adds Lab 7A after Module 7 and before Module 8, for 17 Notebooks
and 148 code cells in the current course source. It runs Qwen2.5 0.5B Instruct
through the CPU llama.cpp server, without an external model account or key.
Main-path Litefuse examples use synthetic model responses. The independent AI
Integrations course is a separate selection.

`packer/images-obs.json` pins 33 main-path container images and the optional
llama.cpp image against the course source's image table. The image contains
pristine course Notebooks and container caches, with no running services,
telemetry or completed exercises. Python dependencies use the reviewed
`packer/locks/doris-obs.requirements.lock` for Python 3.12/Linux amd64.

First setup still needs the pinned DOG/demo Git checkout and Grafana plugin
download. Lab 7A downloads its model weights from the course's pinned Hugging
Face revision and verifies their SHA256 before starting the model server.
Weights live in the temporary course volume and are not baked into the AMI.
Cached containers do not establish offline operation. The `obs-openai` image
path is refused pending an explicit model, credential and spending contract.

The pinned public DOG/demo checkout is made readable by its non-root container
users even under the workshop's restrictive umask. Only paths in that public
commit receive read/traverse permissions; private files, Git metadata and
uncommitted additions keep their existing permissions.

## Independent launch

Follow [fresh-image validation](image-validation.md). Supply browser routes for
all five service ports, in addition to Jupyter:

| Service | Host port | Purpose |
| --- | --- | --- |
| `obs-fe` | `28030` | Doris profiles |
| `obs-grafana` | `23000` | Discover, traces, dashboards and alerting |
| `obs-demo` | `28082` | Storefront, feature flags and browser telemetry |
| `obs-mailpit` | `28025` | Local test, firing, resolved and no-data emails |
| `obs-litefuse` | `23100` | LLM trace UI and login callback |

Named origins enter both browser links and the application settings for
Grafana, demo telemetry and Litefuse authentication. Internal SDK, SQL, SMTP
and Collector addresses remain on loopback or the Docker network.
An operator SSM forward must target each actual host port and remain open
through its browser acceptance. Shared access uses authenticated service
origins with the learner's ownership checks.

## Coordinating the course and browser

After the fresh-image audit, start the normal `course` command. It executes
the pinned Notebooks without replacing their code. Private copies retain
execution output, source hashes, order, cell timings and host resource samples.
Observability has a four-hour command limit and a 30-minute limit per browser
checkpoint. Inspect the exact attempt's remote `progress.json` from its host
terminal. Its location is `course_output_dir` in the local attempt's
`command.json`; the `waiting_browser` object identifies the next step.

Complete the course's preceding browser instructions before acknowledging:

| Checkpoint | Required browser action |
| --- | --- |
| `obs-payment-failure-on` | Set and save `paymentFailure` to `50%` in the demo flag UI |
| `obs-grafana-discover-trace` | Complete Lab 3's Discover searches and open a failed checkout trace at least one minute old, including the payment error details |
| `obs-grafana-dashboard` | Create and save the two-panel `Payment incident` dashboard from Lab 3 |
| `obs-grafana-contact-test` | Create the Lab 8 Mailpit contact point, send its test notification and configure its matching notification policy |
| `obs-grafana-cpu-rule` | Create the documented `Host CPU busy` rule and verify its initial state |
| `obs-payment-failure-off` | Set and save `paymentFailure` to `off` after the failed-order alert fires |

Use a second operator terminal while the original course command runs:

```bash
python tools/workshop_validation.py checkpoint \
  --run-dir workshop/aws/.local/image-validation-run \
  --attempt workshop/aws/.local/image-validation-run/courses/ATTEMPT \
  --gate obs-payment-failure-on
```

The acknowledgement is bound to the exact image, instance, course, event,
learner, Notebook and pending step. It neither performs the browser action nor
certifies it. The original Notebook checks the resulting application state.
The trace step needs an access log at least one minute old because services
export spans in batches. Keep the course paused while completing the documented
Discover investigation before acknowledging that step.
Dispatch records are preserved: if dispatch fails or its outcome is unknown,
inspect the original command before retrying. Never restart the whole course
to resume a checkpoint.

## Acceptance and cleanup

Keep independent browser evidence for the normal reader, Notebook, quiz,
terminal and FE checks, plus `obs-demo-browser-telemetry`,
`obs-grafana-discover-traces-dashboard`, `obs-grafana-live`,
`obs-litefuse-login-trace`, `obs-mailpit-alert-cycle`, `obs-cpu-load-entry`
and `obs-service-restart`. Add `obs-local-model-trace` when selected.
Browser reports name the exact sorted `course_profiles`. API responses and
checkpoint acknowledgements cannot replace these browser interactions.

Verify that browser telemetry reaches Doris, the CPU load leaves Jupyter and
the entry available, and the alert sequence includes firing, resolution and
Collector no-data/recovery. Check service stop/start separately. Record Linux
host metrics, available memory/disk, log ownership and actual waits.
Use the course's teardown to check its containers, named/anonymous volumes,
model cache and logs; then destroy and reconcile the independent environment.
Publish only after the same instance passes the full selected course, real
browser checks and resource cleanup. Repeat the selected profile's checks on
the three-learner shared environment before claiming shared access support.
