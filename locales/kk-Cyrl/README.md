# Kazakh Cyrillic teaching packages

ML-09 extends `kk-Cyrl` to all three published courses: 32 primary modules and
all nine declared optional or extension assets. The combined overlay contains
3,561 units: 3,200 new translations and the 361 retained pilot records. The
expansion translates frozen English content at
`c84c646c70306d693e8cc7d0d516b7bcee88e60e` and keeps glossary version
`kk-rta-m1-1`.

| Course | Complete producer profile | Primary modules | Optional or extension assets | Inspected images |
| --- | --- | ---: | ---: | ---: |
| Real-time Analytics | `real-time-analytics-complete` | 10 | 1 | 58 |
| Data Warehousing | `data-warehousing-complete` | 14 | 7 | 42 |
| Observability | `observability-complete` | 8 | 1 | 27 |
| Total | All three profiles together | 32 | 9 | 127 |

Each complete profile includes its primary lessons, Labs and quizzes, declared
optional work, full course README, display metadata and required shared UI.
The producer independently discovers the required content and checks complete
coverage for the selected profiles. The existing `*-primary` profiles keep
their primary-only scope. Optional work grants no primary-module completion
credit. The AI Integrations course has no Kazakh translation in this expansion.

The current-session LLM directly translated the new units without an external
translation API. After saving each batch, the LLM made a separate source/target
comparison. The parent integration process then passed mechanical admission
for exact source/target hashes, protected code and markup, links, placeholders,
Quiz identities and image-legend bindings. These are separate checks:

| Quality step | ML-09 status |
| --- | --- |
| Current-session LLM translation | Complete |
| Second source/target comparison after saving | Complete, with hash-bound evidence |
| Independent mechanical admission | Passed |
| Native human language review | Not performed |
| Website and Workshop rendering acceptance | Recorded separately for each immutable consumer binding |
| Complete execution of all translated Labs | Not performed as part of translation admission |

[overlay.json](overlay.json) carries the admitted records and their provenance.
The recoverable ML-09 batch fragments are in
[ml09-rta](batches/ml09-rta/), [ml09-dw](batches/ml09-dw/),
[ml09-dw-tail](batches/ml09-dw-tail/) and [ml09-obs](batches/ml09-obs/).
The incremental entry in [revision.json](revision.json) records the batch
counts and hashes, retained units and completed technical checks.

All 127 images were visually inspected. Their original English PNGs remain
unchanged, with Kazakh alt text, captions and complete adjacent label mappings.
This is the producer's explicit `shared_original` treatment; the pixel text
remains English. [image-register.json](image-register.json) binds all source
image hashes and translated legend hashes, with treatment evidence.

Canonical English content, executable code, Notebook code-cell contents and
cell IDs, course/module identities, Quiz question/option IDs, answer mappings
and grading policy remain unchanged. Lab code output strings retain their
authored English text. Translation admission does not certify full Lab
execution or final consumer rendering.

Build all three complete profiles locally from committed inputs with the
matching producer, as described in [locales/README.md](../README.md). Use a new
output directory for each build:

```bash
SOURCE_REF="$(git rev-parse HEAD)"
python tools/build_content_package.py --source-ref "$SOURCE_REF" check-map
python tools/build_content_package.py --source-ref "$SOURCE_REF" build \
  --profile real-time-analytics-complete \
  --profile data-warehousing-complete \
  --profile observability-complete \
  --locale en --locale kk-Cyrl --state release \
  --output /tmp/doris-courses-ml09-complete
python tools/check_content_contract_v2.py \
  --root /tmp/doris-courses-ml09-complete/tree
```

The producer reads the full committed SHA and rejects missing, stale or
unchecked required units. Its private teaching tree includes source courses
and answers; public Quiz delivery uses the answer-free public projection.
Website and Workshop acceptance follow their separate consumer workflows.

## Historical pilot: RTA Module 1

The following paragraphs record the pilot's original scope and verification.
ML-09 expands that scope as described above.

Revision `kk-rta-m1-1` translates the `rta-module-1` profile from English commit
`7e4ef9f5e9752f2812385fd555ff1cf0acea443e`. The current-session LLM translated
and separately checked all 356 required units: 226 UI messages, 44 Quiz leaves,
32 lesson sections, 25 Lab Markdown cells, 13 course-guide sections, 12 metadata
leaves and four Quiz Notebook cells.

[overlay.json](overlay.json) binds every translation and check to the permanent
unit ID and exact source/target hashes. [revision.json](revision.json) records
batches, model information, corrections and the scope of the second comparison.
The translations have state `llm_checked`. Native human review is unperformed.
The shared [glossary](../glossary.json) pins product names and Kazakh terminology.

The six diagrams retain their original English PNGs. Each translated lesson
image has a Kazakh alt, caption and complete adjacent label mapping.
[image-register.json](image-register.json) records each frozen source hash,
legend unit, treatment rationale, available source and repository license.
The producer explicitly admits these as `shared_original`; their raster text
remains English. The editable diagram sources are absent from the frozen tree.

The full course README is translated, including navigation to other modules
and optional work. The selected profile covers Module 1's primary lesson, Lab
and Quiz. The producer marks English navigation mirrors explicitly. Metabase's
optional Notebook and the other modules require their own translation scope.
Local previews passed at 1440, 390 and 320 pixels, with a real six-question
Jupyter submission. The UI keeps its `Manrope,sans-serif` stack: Manrope omits
some Kazakh letters, and the local browser used Helvetica fallback for them.
Preserve the system fallback.

Code cells, machine values, identities, grading policy and answer IDs retain
their canonical source. Lab code output strings remain as authored in English.

Build a private bilingual teaching package using the producer documented in
[locales/README.md](../README.md), selecting `--profile rta-module-1 --locale en
--locale kk-Cyrl`. Commit the inputs first and pass the full resulting SHA.
The teaching tree contains source courses and answers; use its answer-free
public projection for public Quiz delivery. Workshop installation and website
integration follow their separate consumer workflows.

ML-05 adds five checked Notebook working-copy UI messages. Their incremental provenance is in `revision.json`; the original 356 translation records remain unchanged. The pilot now requires 361 units and the UI corpus has 231 messages. Native human review remains unperformed.
