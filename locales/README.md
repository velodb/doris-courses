# Course languages

Teaching content is maintained as ordinary course files. English remains in
`doris-course/01-real-time-analytics`, `02-data-warehousing`, `03-observability`
and `04-ai-integrations`. Kazakh Cyrillic mirrors the first three courses in
`doris-course/kk-Cyrl/`, including their original filenames and subdirectories.

Edit a language's `README.md`, `course.yaml`, lessons, Notebook Markdown/code,
Quiz YAML, images, helper code, data and environments directly. No translation
overlay, source-unit map, hash editing, review-state update or export/import
step is required. English prose and Kazakh prose can evolve independently.
The single shared `doris-course/common` helper remains shared.

`course-files.yaml` registers language roots, native catalogs/UI dictionaries
and release selections. Its historical grading identities preserve existing
Quiz and completion records; the builder automatically versions new grading
meaning. They are not translation records and need no change for prose edits.
English UI messages and their typed placeholder contract remain application
configuration. Kazakh UI messages are the plain `doris-course/kk-Cyrl/ui.yaml`.

## Build from committed files

Check edits in the working tree before committing:

```bash
python tools/check_course_files.py
```

This validates files without publishing an artifact. Then commit and build:

```bash
python -m pip install -r tools/requirements.txt
SOURCE_REF=$(git rev-parse HEAD)
python tools/build_course_files.py --source-ref "$SOURCE_REF" \
  --profile real-time-analytics-complete \
  --profile data-warehousing-complete --profile observability-complete \
  --locale en --locale kk-Cyrl --output /tmp/doris-course-files
python tools/workshop_content.py --package /tmp/doris-course-files \
  --output /tmp/doris-course-files-binding.json
```

Use a fresh output directory. The builder reads the exact Git commit, validates
in staging and publishes atomically. Uncommitted edits are not included. It
never calls a translator, executes a Lab or runs source-provided build hooks.

The generated private `tree/package.v3.json` and indexes contain file inventories,
release identities and hashes for installation integrity. They are build
artifacts, not files that a course author edits. The teaching archive contains
Quiz answers and helper code and must remain private. Website compilation
projects only allowed public pages, images and downloads.

## Editing and identity

A direct edit changes that language's next package. English-only prose edits do
not stale or block Kazakh. Kazakh-only headings, sections, Notebook cells,
examples, helper code, data or images need no English paragraph counterpart.
References are resolved relative to each physical language source file; a
new language-only image needs no English image. Existing PNGs currently retain
English pixels with complete adjacent Kazakh labels.

Course/module IDs, module paths/order, completion policy and Quiz question/option
IDs, order, answers and thresholds identify shared grades. Keep that machine
structure equivalent across installed languages. UI message keys/placeholders
also retain their application contract. A deliberate shared grading change
needs the corresponding language changes and creates a new grading revision;
ordinary wording changes only change the presentation. This check protects
shared progress without comparing English and target paragraphs or code.

Old `unit-*` anchors and English heading aliases were saved once into the
Kazakh Markdown files. Retain an anchor when editing its section to preserve
existing links. New sections can use normal Markdown headings.

Standalone Kazakh Notebooks load native Quiz YAML and UI text directly. These
unbound attempts are self-study; they do not generate trusted Workshop receipts
or website credit. Installed Workshop kernels use each language's own physical
course root, helpers and data with the shared common library. A course
interpreter may be reused when dependency sets are compatible.
Managed Workshop installs compare requirements, includes and project dependencies
before reusing it. Different dependencies require that language's own environment
and reviewed lock; a missing one produces an explicit error instead of silently
using English dependencies. Standalone course setup uses its normal local venv.

## Historical v2 packages

Previously generated overlay-v2 packages remain immutable and supported by the
legacy consumer. Their source map, translation batches and checks remain in
Git history, including commit `dfa31100d936cf1262648df44542a8a2e0c35dee`.
They are not authoring inputs for new course-file releases. The one-time
`tools/migrate_course_translations.py` created the native Kazakh tree from that
admitted package and refuses to overwrite an existing tree. It is not part of
subsequent builds.

See [the Kazakh tree](../doris-course/kk-Cyrl/README.md),
[Workshop installation](../workshop/aws/runtime/multilingual.md) and
[the website consumer](../contracts/v2/website-consumer.md).
