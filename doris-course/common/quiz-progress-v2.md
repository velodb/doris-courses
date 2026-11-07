# Quiz sessions and portable progress v2

Display language does not identify a learner, module or credit. The reader and
Quiz retain SQLite's `progress(course,module)` primary key. Lesson, Lab and
passed Quiz each count once. Historical passes survive failed retries and
content updates. Scores use exact fractions; passing requires
`correct * 100 >= total * threshold`. Preferences never authorize grades.

## Independent bindings

| Binding | Meaning |
| --- | --- |
| `source_ref`, `content_version` | Pinned source commit and existing runtime module hash. Preserve historical values and algorithms. |
| `semantic_revision` | Producer-assigned opaque meaning/answer/threshold revision. Reuse with different meaning is an admission error. |
| `locale`, `presentation_revision` | Complete admitted display, using the content-v2 stable-unit digest. |
| `policy_revision` | Immutable completion requirements. Translation does not reset old credit or certificates. |

`quizsession.Presentation.admitted(bank, private_quiz_descriptor, locale=...,
policy_revision=..., source_digest=...)` is an installation/server adapter API.
Its caller must first admit the content-v2 package, verify hashes, revision
history and coverage, and supply its descriptor and policy. The API also checks
question IDs, option ID sets, answers and threshold. It cannot infer semantic
equivalence or translation quality from text.

Canonical English loading retains its title validation and shuffled order.
`load_quiz(path, localized=True)` relaxes only the English title prefix check for
admitted banks. New legacy English attempts retain their exact file digest and
null semantic/presentation/policy revisions. Never synthesize a website
`questionDigest`, release revision, missing selection or historical attempt.

## Presentation changes

```python
quiz = CourseQuiz.from_yaml(english_path, presentation=english_presentation,
                            messages=english_context.messages)
quiz.show()
quiz.switch_presentation(target_presentation, messages=target_context.messages,
                         source_path=target_path)
```

Switching preserves attempt ID, question index, question and option order,
confirmed answers and unsubmitted selections by stable IDs. Quiz ID, admitted
semantic revision, policy revision and grading map must match. Missing
equivalence raises `QuizConflict`. Legacy banks cannot acquire translations by
relabeling. Controls and content must share a locale. Stale widget callbacks
are ignored; switches and result rendering serialize.

`show()` and **Try again** start new attempts. Completing every question freezes
the submitted presentation, answers and attempt ID. Equivalent results can
change display language without writing again. Submitted questions are
read-only. **Retry saving** resends the original frozen submission.

## Fixed submissions

Private `Store.register_quiz(module, presentation)` installs a grading snapshot
and returns a random opaque `release_id`, independent of answer hashes. Never
call it with browser-supplied snapshots. `Store.submit_quiz(module, request)`
accepts exactly this shape:

```json
{
  "schema_version": 2,
  "kind": "doris-quiz-submission",
  "attempt_id": "0123456789abcdef0123456789abcdef",
  "release_id": "fedcba9876543210fedcba9876543210",
  "binding": {
    "quiz_id": "q.example",
    "locale": "kk-Cyrl",
    "semantic_revision": "quiz.example.1",
    "presentation_revision": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "policy_revision": "policy.example.1"
  },
  "answers": {"stable_question_id": "stable_option_id"}
}
```

The server resolves the release, checks bindings and exact question/option
coverage, and grades using its fixed answers. New meaning never regrades old
requests. Unavailable releases conflict. The same attempt ID and canonical
request return the original receipt/timestamp; different answers or display
under that ID conflict. Receipt and merged credit commit together. Requests
cannot supply scores, answer keys, source digests or identity selectors.

The trusted caller supplies the module. This instance store represents one
assigned learner and remains editable self-study data. A website adapter must
scope release/session and idempotency lookup to authenticated learner/activity,
check ownership, retain old releases or return HTTP 409 with an explicit reload
action, and map `QuizConflict` accordingly. Use its existing authenticated
transaction and certificate policy. Preserve native old attempts,
`questionDigest`, revisions and certificates. Actual website DB/HTTP integration
is separate consumer work; portable records cannot enter trusted grading.

Public Quiz responses exclude full grading snapshots, explanations,
source/teaching digests and answer-derived hashes. Return only feedback allowed
for the attempt. Private self-study exports are a separate interface.

## Runtime adapter

Legacy Notebook initialization is unchanged. On completion,
`save_submission_from_environment` resolves the exact selected canonical YAML
path and checks its bytes against the widget. Without a progress environment it
creates no database. Optional banks retain their no-main-credit behavior.
Changed source bytes before first save produce an explicit conflict.

A future installer can supply these private per-module fields:

- `quiz_binding`: admitted content-v2 private Quiz descriptor;
- `policy_revision`: admitted course policy revision;
- `quiz_presentations`: entries with source-root-relative `path`, `locale` and
  the installed file's `source_digest`.

The helper checks selection, containment, hash and presentation before saving.
These are adapter fields, not an implicit extension of runtime v1. An installer
must explicitly version its plan/capabilities. The browser fixture supplies them
only in a disposable kernel; real installation is separate consumer work.

## Portable formats and migration

Separate [v1](../../contracts/progress/v1.schema.json) and
[v2](../../contracts/progress/v2.schema.json) schemas describe
`kind: doris-workshop-progress`. Dispatch on integer `schema_version`. Content,
runtime and image versions do not select this format. The standard-library
validator also checks exact fractions, strict booleans, timezone timestamps,
uniqueness, history membership and flag dependencies. Limits are 1 MiB and 2000
entries per array; oversized exports fail explicitly.

V1 retains its four top-level fields and original record shape. V2 adds
`attempts` and `legacy_records`; summaries retain the v1 record shape.

| History | Retained data |
| --- | --- |
| `attempts` | Attempt/course/module/Quiz IDs, submitted locale/revisions, original source bindings, selected IDs, score/threshold/pass, timestamp, constant `trust: self-study`. |
| `legacy_records` | Exact v1 summary snapshots with no invented locale, selections, semantic revision or attempt ID. Missing information stays unknown. |

`Store.export()` and the authenticated download default to v2. Explicit
`export(version=1)` warns about history loss; `?version=1` also returns a `Warning`
header. Keep the v2 file and database. Unknown versions are rejected.

Import validates the whole file, previews matches/history counts and merges in
one transaction. Unknown, retired and unselected identities and their histories
are skipped. Flags union, passes survive, best fractions do not decrease and
reimports retain timestamps/history. Conflicting attempt IDs reject the
transaction. Imported attempts live separately and cannot become local
submission receipts. Old scores remain as recorded without regrading. Imports
never establish trusted transcripts or website certificates.

Opening an old database adds tables and archives exact legacy snapshots without
changing progress rows or their primary key. Upgrade and each import create a
private `0600` SQLite backup under the database directory while holding the
writer lock. Backups may include grading data; keep them outside web/Notebook
roots. Paths are not exposed in learner HTML.

`Store.backup()` creates an operator backup. After stopping learner writers,
`Store.restore_backup(path)` restores owned tables atomically and first backs
up current data. V1 images restore without invented history. Normal binary
rollback retains additive tables; old code reads its original progress table
and a v1 projection. Full restoration returns to an earlier point in time; keep
the pre-restore image to recover later work.

Synthetic examples: [v1 fixture](tests/fixtures/progress-v1.json),
[v2 fixture](tests/fixtures/progress-v2.json). The browser runner uses synthetic
Cyrillic copy and real local widgets, without native-language, cloud or Lab
qualification.

```bash
PYTHONPATH=doris-course/common <course-python> -m unittest discover \
  -s doris-course/common/tests -v
PYTHONPATH=tools/tests:doris-course/common .venv-workshop/bin/python -m unittest \
  test_progress_contract test_workshop_progress test_workshop_i18n -v
PLAYWRIGHT_MODULE="$PWD/ui/workshop/node_modules/playwright" \
  .venv-workshop/bin/python tools/tests/run_progress_browser.py --bilingual \
  --output /tmp/quiz-progress-review
```
