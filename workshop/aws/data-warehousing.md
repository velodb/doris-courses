# Data Warehousing on AWS

The complete six-profile selection has passed fresh-instance course and browser
validation, and private image release `dw-341f7de-full-v1` is available in the
operator account. Shared HTTPS/Cognito final acceptance is pending activity
cleanup and reconciliation. See [current acceptance](#current-acceptance) and
the [support matrix](support.md#validation-levels) for the exact scope.

Select all six profiles to include the main course and every optional Lab:

```yaml
course_profiles:
  - dw-main
  - dw-kafka
  - dw-cdc
  - dw-bi
  - dw-backup
  - dw-extensions
```

The complete selection contains 34 Notebooks and 204 code cells at the initial
implementation baseline: 14 main Labs, 14 main quizzes, Kafka and CDC Labs,
three extension Labs and the continuous-ingestion quiz. Readings stay in the
course source. The execution and publication checks derive the selected files,
cell counts and source hashes from the pinned course commit, so this count is
descriptive rather than a substitute for checking that commit.

The validator runs main modules in order. It runs the physical-design extension
after Module 3, the files/group-commit extension and streaming Labs after
Module 5, and the schema/delete extension after Module 7. After streaming, it
uses the course's documented return to Lab 1 startup to remove the streaming
memory overlay and restore existing course network attachments. Main-path
dependencies and exercise data stay on that disposable host.

With `dw-bi`, it enables the documented `BUILD_DASHBOARD` boolean in a private
execution copy of Lab 11. With `dw-backup`, it similarly enables
`RUN_BACKUP_DRILL` in Lab 13. Both choices and every executed cell are recorded;
missing or ambiguous booleans fail validation. Repository source and learner
working copies keep their original opt-in defaults. The backup drill uses the
course's temporary local object storage and creates no platform learning-record
or backup service.

Use the [private-image build](packer/README.md) and
[fresh-instance validation](image-validation.md) commands with a new event,
expiry and run directory. The operator must refresh identity, prices, quotas
and the budget, review the saved plan and reconcile all resources after testing.
The initial build hardware remains one Linux x86_64 `m7i.2xlarge` with a 200 GiB
encrypted root volume. The measured whole-selection run is described below.
An old RTA AMI cannot be relabeled as a Data Warehousing image.

Shared access declares separate protected origins for Jupyter, Doris FE,
Superset and the dynamically assigned Flink REST port. Internal SQL and API
clients keep their loopback/container addresses. Browser checks must verify
actual dashboards, Flink links, cookies, redirects, Notebook/kernel/terminal
use and learner isolation before shared-browser support is claimed. A successful
Notebook run alone does not establish those browser results.

Failed execution stops at the first failing file and preserves a bounded public
summary. Executed Notebooks and cell timings remain private because their
outputs can include course credentials and connection strings. Publication
requires the complete selected execution, real browser checks on the same
audited fresh instance, and its completed cleanup reconciliation.

The disposable-host validator samples CPU, available memory, free disk and
non-loopback network counters every two seconds. Its report summarizes the
observed minimum available capacity and maximum interval CPU use; the private
sample file preserves the time series. These measurements describe the selected
Lab run and its cache state, and do not certify larger datasets or classrooms.
The report also records the activity-owned dynamic Flink port when CDC is selected.


## Current acceptance

On 2026-11-05, the private image at course/deployment commit
`341f7de639bc1b4807015de2db08712d12a09871` passed 28 fresh-instance checks,
all 34 selected Notebooks and 204 code cells, and eight actual browser checks
on the same new Linux x86_64 instance. The browser checks covered the reader
and images, Notebook creation/execution/save/download, kernel restart and
reconnection, quiz navigation and feedback, terminal Docker access, a real FE
query profile, the Superset dashboard, and the dynamic Flink dashboard.
The builder and independent validation environment were destroyed and reconciled
before the private release was published. The AMI is private; partners must
build and validate in their own accounts.

The 646.94-second course run used an `m7i.2xlarge` with a 200 GiB encrypted
root disk in `us-east-1`. Two-second samples observed at least 23.752 GiB
available memory and 176.987 GiB free disk, with a maximum interval CPU use of
64.81%. The separate ordinary → 6 GiB streaming → ordinary transition drill
preserved both data volumes, lake/BI/backup network attachments and live
Superset metrics. These results describe the pinned teaching data and cached
images; they do not establish performance for larger data or classrooms.

A subsequent three-host shared exercise completed the full selection on each
host (102 Notebooks and 612 code cells). Its original reports were read through
authenticated kernels after the operator AWS session expired. Three-account
assignment, owner SQL/terminal WebSockets, all six directed cross-account
HTTP/API and WebSocket checks, 18 further cross-account FE/BI/Flink denials,
and entry logout/reauthentication passed. Shared FE profile, Superset metrics
and Flink TaskManager pages were observed. Some first frontend loads timed
out; final checks used the real loaded applications and normal reloads, with
no asset or API substitution. First-login password changes, hosted-login
branding and a shared Notebook's first-save confirmation were not verified.
The original SSM terminal statuses and complete activity/shared-resource
cleanup still require operator authentication. Shared support remains pending
until those final checks and reconciliation finish.

Each learner has one EC2 instance containing that learner's course services.
Jupyter, FE, Superset and Flink are four protected origins for one learner.
Three learners therefore use three learner instances plus a separate shared
Portal instance. Sharing one learner instance among several people is not
implemented. The lifecycle pilot currently permits one to five learners and
has been exercised with three; a 12-person event needs expansion and its own
capacity acceptance.
