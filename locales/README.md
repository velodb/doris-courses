# Building multilingual teaching packages

The producer reads a full Git commit and creates a private teaching tree plus
an answer-free public quiz projection. It never reads uncommitted course or
translation changes. The existing English catalog, source paths and v1 consumer
remain available. The [v2 contract](../contracts/v2/README.md) defines admission.

Install the pinned tools dependencies, commit the inputs, then build from the
repository root with the matching installed builder:

```bash
python -m pip install -r tools/requirements.txt
SOURCE_REF=$(git rev-parse HEAD)
python tools/build_content_package.py --source-ref "$SOURCE_REF" check-map
python tools/build_content_package.py --source-ref "$SOURCE_REF" build \
  --profile rta-module-1 --output /tmp/doris-content-one
python tools/build_content_package.py --source-ref "$SOURCE_REF" build \
  --profile rta-module-1 --output /tmp/doris-content-two
cmp /tmp/doris-content-one/teaching.tar.gz /tmp/doris-content-two/teaching.tar.gz
cmp /tmp/doris-content-one/public.tar.gz /tmp/doris-content-two/public.tar.gz
python tools/check_content_contract_v2.py --root /tmp/doris-content-one/tree
```

Use new output directories for each attempt. An existing directory is never
overwritten. The producer validates a temporary tree before publishing the
complete output directory. A failed admission leaves no output artifact.

## Inputs and stable identities

| Input | Purpose |
| --- | --- |
| [source-map.json](source-map.json) | Committed, assigned unit IDs, exact canonical English text/hashes and current locators. |
| [producer.json](producer.json) | Explicit profiles, optional selections, immutable policy/quiz revision bindings, glossary version and shared-image treatment. |
| [ui.en.json](ui.en.json) | Canonical English messages for entry, reader, progress, service guidance, Quiz controls and recovery. See the [consumer interface](../ui/workshop/i18n.md). |
| [ui.contract.json](ui.contract.json) | Typed placeholders and explicit count forms, validated by producer and consumers. |
| [ui.inventory.json](ui.inventory.json) | Auditable message-key/consumer inventory; check with `tools/check_ui_messages.py`. |
| [glossary.json](glossary.json) | Versioned product/identifier rules and the Kazakh RTA Module 1 terminology. |
| `locales/kk-Cyrl/overlay.json` | A formal `locale_overlay`, supplied by the current-session LLM with attributed checks. The RTA Module 1 pilot is included; see [revision and quality scope](kk-Cyrl/README.md). |
| `locales/kk-Cyrl/assets.json` | Optional JSON object mapping English image paths to committed localized image files. |

Source discovery covers catalog/course display leaves, each course guide,
Markdown and Notebooks under `level*/`, primary/optional manifest assets, all
question/option/explanation leaves and the UI dictionary. Markdown is partitioned
into exact sections, including the bytes between headings. Repeated selectors
are merged until unique. Notebook Markdown uses its existing cell ID. Identity,
policy, answers, code, URLs and machine configuration are never translation
leaves. The producer independently discovers the complete set on every build;
omitting a unit from the map cannot increase reported coverage.

Unit IDs are UUIDs assigned once and committed. Text, hashes and array positions
never determine replacement IDs. To inspect changes at a newly committed SHA:

```bash
python tools/build_content_package.py --source-ref "$SOURCE_REF" inspect > /tmp/candidates.json
python tools/build_content_package.py --source-ref "$SOURCE_REF" register \
  --previous locales/source-map.json --bindings /tmp/unit-bindings.json \
  --output /tmp/source-map.next.json
```

The binding file maps an existing unit ID to an inspected candidate handle.
Structured handles name permanent module/question/option IDs; a Markdown
candidate's block number is temporary and must never become its identity.
Unchanged locators and exact texts can match automatically. Edits, movement and
reordering that change that match require explicit bindings. Notebook cell IDs
stay intact. Review splits/merges explicitly: retain the appropriate existing
ID, use `--retire <id>` for retired units, and `--accept-new` for newly assigned
units. Never restart an existing map without `--previous`. Review the resulting
diff, replace the committed map and commit again before building.

Old overlays retain their old source hash. A source-hash mismatch is reported
as stale and rejects both preview and release; the tool never rewrites check
records to make a stale translation current. New text needs a new target hash
and fresh checks for the exact source/target pair.

English Notebook quiz banners contain legacy inline CSS. The canonical v2
Notebook removes only HTML style attributes in Markdown, preserving code
examples, code cells, metadata, order and cell IDs. The original Git byte hash
and named transformation are recorded in `indexes/source-inputs.json`. Original
repository files and historical v1 bytes are unchanged. Source-map Notebook
text refers to this canonical v2 representation. The producer rejects uncleared
source outputs instead of silently discarding them.

Policy and quiz meaning bindings have an opaque assigned revision and a private
payload digest. A meaning change requires a new revision and updated digest;
reusing the old digest fails. Translation changes neither binding. The builder also checks the selected Git
history to reject rebinding an existing revision to another payload digest. These private
payload digests must never become public quiz revisions or download URLs.

## Profiles, coverage and images

The initial profiles are `rta-module-1` and the primary modules of each published
course: `real-time-analytics-primary`, `data-warehousing-primary` and
`observability-primary`. The draft AI course remains registered as English
source and cannot be selected. Repeat `--profile` to combine selections.

Each profile requires catalog/course display copy for its selected modules, the
complete course README (including setup/cleanup), primary lessons/Labs/Quiz,
Quiz Notebook shells statically bound to the selected YAML banks, selected optional work, the message corpus, shared course
and common code/data/dependencies, and linked images/downloads. Links to other
lessons are navigation, not an implicit selection of those modules. Unselected
optional text does not add required translations. Course metadata JSON keeps
unselected English fields for identity/navigation; consumers must filter by the
selected profiles and use registered units for localized presentation.

Coverage counts required independently extracted units, not entries supplied by
an overlay. Publication also requires complete asset and quiz presentation
bindings for every selected profile. English images require localized
replacements, or an explicit `shared_images` record with treatment rationale
and evidence. No real image has been declared language-neutral by default.
Source hashes bind replacements; code/data replacements are prohibited.

To inspect an intended locale before its translations exist:

```bash
python tools/build_content_package.py --source-ref "$SOURCE_REF" build \
  --profile rta-module-1 --locale en --locale kk-Cyrl --state preview \
  --output /tmp/doris-content-preview
```

A preview keeps the declared draft locale and reports missing units/assets/Quiz
bindings. It exposes English only. A release publishes every requested locale
and fails if any required content is missing, stale, unchecked or withdrawn.
The committed Kazakh pilot contains current-session LLM translations and a
separate source/target self-check. Native human review is unperformed. Automated
producer regression tests still use synthetic overlays. The workshop UI consumes the canonical message contract. The [Workshop v2 installer](../workshop/aws/runtime/multilingual.md) and separately pinned [website consumer](../contracts/v2/website-consumer.md) consume this private release. Combined release qualification and cloud publication remain separate checks.

## Artifact layout and consumers

| Output | Use |
| --- | --- |
| `tree/package.v2.json` | Private admitted manifest: profiles, units, overlays, assets, grading bindings and frozen inventory/digests. |
| `tree/doris-course/` | Original shared learning layout; canonical v2 Notebooks retain the original paths. |
| `tree/rendered/en/` | Data-only JSON projections of original catalog/course metadata. |
| `tree/rendered/<locale>/` | Localized assets with source-relative layout and required link targets. |
| `tree/indexes/anchors.json` | Permanent unit anchors for Markdown sections and Notebook Markdown cells. Consumers insert/use these anchors during rendering. |
| `tree/indexes/assets.json` | Asset records, immutable-artifact downloads, closure and explicit locale of generated link targets. |
| `tree/indexes/source-inputs.json` | Raw Git file hashes, full SHA and canonical Notebook transformation. |
| `tree/indexes/coverage.json` | Unit denominator and missing/stale/unchecked IDs per locale. The admission report also covers assets/quizzes. |
| `tree/indexes/projections.json` | Answer-free public inventory and digest, bound by the private manifest. |
| `public/` | Independently validated public quizzes and index. No answer/explanation, full source map, grading payload digest or teaching digest. |
| `teaching.tar.gz` / `public.tar.gz` | Separate deterministic private/public archives. |
| `build-report.json` | Admission result, source/builder binding, package/public and exact archive digests. |

Never serve the private teaching tree. It includes all original source courses
and answers, even when profiles select only one module. Website consumers must
construct their own lesson/download allowlist and sanitize rendered content;
the public directory currently supplies quizzes only. Workshop installation may
consume the private package under its existing learner access policy.

Route rewriting is separate from translation. Relative asset links retain their
protected bytes. Missing navigation targets can be mirrored as English source
resources inside the frozen tree; `indexes/assets.json` marks their actual
locale. Consumers must route those links explicitly to English or an unavailable
response. They must never serve the mirror as a target-language lesson. Use the
asset path/hash through immutable artifact storage for generated downloads;
these files do not claim to exist at a GitHub raw path.

The installed builder, admission modules, requirements and schemas must match
the selected Git commit. File hashes pin the actual source map, glossary,
configuration and overlay inputs. Canonical JSON and inventories bind the
package, teaching and locale digests. The public digest binds its own sorted
inventory without including private grading hashes. Archives fix entry order,
mtime, owner, names and modes; the manifest excludes itself from its inventory.

Shared machine YAML/JSON is an opaque learner download, never parsed or executed
by admission. Compose anchors therefore retain their original bytes. Referencing
such a file as a structured content document forces strict parsing; authoring
YAML still rejects aliases/anchors and duplicate keys. Literal SQL `<db>` in
legacy quiz display strings is inert notation and must be escaped by consumers.
Active HTML, arbitrary custom elements and inline CSS remain rejected.

Run the regression checks with:

```bash
python -m unittest discover -s tools/tests -p 'test_content*.py' -v
```
