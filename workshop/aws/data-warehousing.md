# Data Warehousing on AWS

The build and full-course validation tools accept `dw-main` and its optional
profiles. EC2 course, private-image and shared-browser acceptance for this
selection is pending. See the [support matrix](support.md#validation-levels)
for the current accepted versions. Declaring a profile does not certify it.

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
encrypted root volume; suitability for the whole selection still requires the
actual course run. An old RTA AMI cannot be relabeled as a Data Warehousing image.

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
