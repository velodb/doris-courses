# Personal workshop progress

New source bundles support a personal learning record in the authenticated
course outline. Lesson, Lab and Quiz each contribute one third of a module:

| Item | How to earn credit |
| --- | --- |
| Lesson | Read the lesson, then select **Mark as read**. You can undo it. |
| Lab | Select **Open Lab** from the outline or lesson, complete your work, then return to the outline and select **Mark Lab complete**. You can undo completion. |
| Quiz | Select **Open Quiz**, run its initialization cell and submit every answer. The exact number correct must reach the YAML bank's `passing_score_percent`. |

Opening a page or running some Lab cells does not grant completion. Refresh the
outline after working in a Lab or Quiz tab. The Quiz retains its best score and
any earlier pass, including after an unsuccessful retry. A rounded displayed
percentage cannot grant a pass below the actual threshold.

The main total counts only selected, published, required modules. For example,
two complete modules plus a third with Lesson and Lab credit in a ten-module
course give 8 / 30 completion items, or 26.7%. A module is complete only when
all three items are done. Optional modules show their credit separately and do
not affect the main total. Planned, retired and unselected modules are excluded;
an empty main course shows **Not applicable**.

Selected optional Labs attached to a module appear in a separate list. These
assets have no independent permanent module ID, so they do not create extra
completion items or replace the module's main Lab/Quiz credit. Optional Quiz
results explain this rather than reporting a failed save.

## Save and move your record

Records save automatically after each explicit action or completed Quiz. A
**Progress was not saved** message means the operation failed; retry before
closing the page. A Quiz retains its answers and offers **Retry saving** after
a save failure. The outline reports unavailable storage instead of showing an
empty record. Logging out, using another browser, or restarting the kernel or
Jupyter keeps committed records on this instance. People sharing one assigned
account/instance share one record; use separate allocations for personal work.

Before the environment expires, select **Download progress JSON**. Keep this
file on your computer. In the next workshop:

1. Sign in to your newly assigned environment and select **Import progress**.
2. Choose your JSON file and select **Preview import**. Check matching module
   identities, content changes, the resulting credit/best score and skipped
   unknown or unselected modules. Previewing writes nothing.
3. Select **Confirm import**, then return to the outline.

Import merges completed items, preserves earlier Quiz passes and keeps the
best exact score fraction. Repeated imports are idempotent and an old file
cannot lower current progress. Importing a completed item restores it even if
you previously undid that mark. The same permanent module identity keeps its
credit after a content update, with a visible reminder to review the updated
material. New module identities start empty. Module display order and temporary
workshop usernames do not determine identity.

An import restores learning records. Prepare Lab services, prerequisite data
and database state again. Notebook and database backups require separate work.
There is no automatic cloud synchronization or recovery after instance
replacement/deletion. This portable self-study record is not a transcript,
examination result, certificate or a transfer from the separate course website.

## Storage, activation and image boundary

The reader and course Quiz widget share one standard-library SQLite component
in `doris_labkit.progress`. Production stores records in
`/var/lib/doris-workshop/progress/progress.sqlite3`, outside the Notebook root
and Docker volumes. The directory and file use modes `0700` and `0600`.
Transactions serialize concurrent tabs, Quiz saves and imports. Reinstalling
the same runtime preserves the file; image sealing removes it, and fresh-image
audits reject nonempty learning records.

Bundle generation sets `learning_progress: true` only when the pinned source
contains the progress component. The runtime carries the manifest course slug,
permanent module ID, publication/required status and a SHA256 version derived
from its pinned Lesson, Lab and Quiz assets. The v1 content contract is unchanged.
Older bundles/images do not acquire this feature. A new source/image release
and fresh-instance acceptance are still required before claiming AWS support.

`serve.py --no-progress` disables the reader controls and Quiz persistence.
`--progress-db` can select another private database outside the Notebook root.
The server supplies `DORIS_WORKSHOP_PROGRESS_DB`,
`DORIS_WORKSHOP_PROGRESS_PLAN` and `DORIS_WORKSHOP_PROGRESS_SOURCE` to its
kernels. A normal local course with no progress environment retains its
in-memory Quiz behavior and creates no database. Quiz association uses the
selected manifest's exact YAML path, never a filename number or supplied user.

Progress routes reuse authenticated Jupyter access and the gateway's existing
assignment checks. Writes require same-origin form XSRF even when the gateway
supplies Jupyter token authentication. File names and uploaded fields cannot
select an account, database path or other instance. The UI remains server
rendered under the existing CSP, without inline scripts or browser storage.

## Portable JSON v1

The independent format has exactly four top-level keys: `schema_version: 1`,
`kind: "doris-workshop-progress"`, timezone-aware `exported_at`, and `records`.
Each record contains exactly these fields:

| Field | Value |
| --- | --- |
| `course_slug`, `module_id` | Permanent lowercase hyphen-separated manifest identities |
| `course_read`, `lab_opened`, `lab_declared_complete`, `quiz_passed` | JSON booleans; Lab completion requires opening |
| `best_correct`, `best_total` | Integers, `0 <= correct <= total <= 10000`; a passed Quiz has a nonzero total |
| `content_version` | 64 lowercase hexadecimal characters; historical content binding |
| `source_ref` | 40 lowercase hexadecimal characters; source commit |
| `updated_at` | Timezone-aware timestamp |

Files are limited to 1 MiB and 2000 records. Unknown fields, duplicate fields or
identities, unsupported versions and invalid types/ranges are rejected before
writing. Imports are atomic. Files include no passwords, authentication tokens,
Notebook content, database data or kernel state. They are editable personal
records and do not establish a trusted examination grade.

## Local verification

The new feature has local acceptance only until a new cloud release is verified.
The disposable browser fixture runs real Jupyter servers and the actual course
Quiz widget, with lightweight Lab cells. It does not qualify course Labs or AWS.

```bash
python -m unittest discover -s tools/tests -p test_workshop_progress.py -v
# Use a course environment with doris_labkit and its widget dependencies installed:
python -m unittest discover -s doris-course/common/tests -p test_progress.py -v
npm ci --prefix ui/workshop --ignore-scripts
python tools/tests/run_progress_browser.py --output /path/to/private/browser-evidence
```

The browser runner verifies desktop/mobile controls, actual Quiz pass and retry,
kernel restart, logout/new browser login, JSON download, a second empty
environment, preview/confirmed/repeated import, invalid-file feedback and
preservation after both Jupyter processes restart. It removes its temporary
processes, credentials and data. Evidence is stored only in the selected output.
