# Multilingual course content contract 2.0.0

This contract describes a resolved, private teaching package containing the
English source, translation units, rendered assets and grading bindings. The
entry point is `package.v2.json`. Consumers select this interface explicitly;
the existing root `catalog.yaml` remains the English v1 entry point.

The contract, admission tests and [offline producer](../../locales/README.md)
are implemented. Website ingestion, workshop installation and learner language
switching require their respective consumer implementations. The
[example](examples/README.md) is synthetic and cannot establish course readiness.

## Pinned interfaces

- [schema.json](schema.json) is a self-contained JSON Schema Draft 7 document.
  Its default reference validates the complete package. `definitions` also
  exposes `catalog`, `course`, `module`, `authoring_quiz`, `unit`, `translation`,
  `asset`, `quiz`, `profile`, `snapshot`, `source_map`, `locale_overlay`,
  `ui_messages` and `public_quiz` for independent producers and consumers.
- The canonical English `catalog`, `course`, `module` and `authoring_quiz`
  definitions are exact copies of the immutable v1 definitions. They describe
  English source documents. Localized text belongs in units and assets.
- [locales.json](locales.json), version `1.0.0`, is the locale registry.
  [registry.schema.json](registry.schema.json) defines its structure.
- [english-baseline.json](english-baseline.json) freezes the development base
  SHA, v1 schema and manifest hashes, stable identities, English routes/source
  paths, the legacy quiz digest algorithm's output and a synthetic v1 progress
  record. It contains no production database IDs or observed learner records.

All object fields are required unless the schema explicitly says otherwise.
Unknown fields are rejected, including hooks, executable templates, custom CSS
and private grading fields in public quizzes. Empty arrays and nullable fields
have explicit meanings; omitted fields do not mean English fallback.

## Package objects

| Object | Meaning and invariant |
| --- | --- |
| `catalog` | Path to the original v1 `catalog.yaml`; course order and program IDs/requirements remain canonical. |
| `courses` | One binding per catalog course: `slug`, original `manifest` path and opaque `policy_revision`. Certificate codes and completion requirements stay in the original manifest. |
| English module | Original manifest object with permanent `id`, source-directory `slug`, level/order, status, requirements and primary/optional asset paths. Translation never creates a new module. |
| `source_units` | Stable `id`, course/module scope, `source_path`, locator, exact `source_text` and its UTF-8 SHA-256. IDs survive source edits and movement. |
| `translations` | One overlay per non-English canonical locale; each entry names an existing unit and binds its source hash, target text/hash, state, translator and review evidence. |
| `assets` | Stable asset ID, kind, locale or `shared`, exact file record and nullable `source_asset_hash`. A replacement binds an English/shared asset of the same kind. Code/data cannot be localized. |
| `quizzes` | Stable quiz/course/module IDs, canonical source path, opaque semantic revision, exact threshold/question/option/answer mapping and locale presentation bindings. This object is private. |
| `profiles` | Explicit module selection and required unit, source-asset and quiz IDs. Primary lesson/Lab/quiz assets and the primary quiz binding cannot be omitted. The producer must derive the full closure, including selected optional work, instructions and UI. |
| `snapshot` | Full source SHA, pinned schema/registry digests and versions, builder version, sorted exact file inventory, translation/teaching/package digests. |

The source files, units and rendered assets are included in one frozen tree.
Every referenced source file, canonical Lab setup file, primary/optional module
asset and asset record must occur in the inventory. Consumers copy only the
files named by the projection they implement. They never serve this whole tree.

`purpose: contract-fixture` requires the validator's explicit fixture opt-in.
Teaching packages use `purpose: teaching`. A fixture's example `source_ref`
records its development base, not a production content release.

### Source locators and text integrity

`json-pointer` selects an allowed display leaf in catalog/course/quiz YAML or
JSON, or a message in a `doris-ui-messages` dictionary. Identity, answer,
threshold, code, URL/configuration and completion fields are excluded. Array
positions are current locators, never persistent unit identities. Update the
locator when a source question moves and retain its unit ID.

`notebook-cell` selects a Markdown cell by its existing, unique cell ID.
`markdown-block` uses an opaque stable locator label and the exact registered
`source_text` as a unique substring of the Markdown file. Missing or ambiguous
blocks require author intervention. Source edits update the text/hash and mark
old translations stale; they do not generate replacement unit IDs. The
producer must register the complete translatable structure, rather than using
this locator check as evidence that it extracted every block.

Source and target hashes use the exact UTF-8 text, with no implicit Unicode or
newline normalization. Notebook source arrays are concatenated in order before
hashing Markdown cells. JSON pointers resolve parsed scalar strings. Code
fences, inline code, link/image URLs, HTML anchors and named placeholders
(`{name}`, `${name}`, `{{name}}`) must survive translation. Consumers perform
route rewriting separately from translation. Localized Notebook cell identity,
order, metadata and code stay equal to their source; teaching Notebooks have
cleared outputs and execution counts. Localized quizzes retain their grading
structure. Registered rendered text must agree with its overlay.

### Locales, profiles and partial publication

Registry tags are `en` (Latn, ltr, format `en-US`) and `kk-Cyrl` (Cyrl, ltr,
format `kk-KZ`). `en-US`/`en-GB` and `kk`/`kk-KZ` are input aliases. Tags, routes
and aliases cannot collide ignoring case. UI/route adapters may normalize an
alias; content manifests use the canonical tag exactly. Registry extensions
need a new pinned registry version and consumer support.

`variants` declares intended locales and their `draft`, `published` or
`withdrawn` state. `available_locales` is derived in variant order: only published
locales that satisfy every selected profile can be included. All required units
must be English source or have `llm_checked`/`reviewed` translations; required
assets need a same-kind localized replacement or a shared asset; required
quizzes need a bound presentation. `default_locale` must belong to this set.

A `release` requires every declared variant to pass. Missing or stale content
cannot silently remove an intended locale from a release. A `preview` can keep
draft variants outside `available_locales`, with missing/unchecked units reported
by the validator. Invalid hashes still fail preview admission. Partial releases
select profiles explicitly: a Module 1 package does not claim whole-course
translation. Draft courses, planned modules and retired content cannot be
exposed through a profile. An English source catalog can retain those identities.

Workshop deployment binds the entire admitted locale set, its per-locale
digests and a default. A learner's later choice refers to installed content;
it does not change the deployment binding or the progress identity.

### Translation quality evidence

Every translation attributes `translator: current-session-llm`, a session and
batch ID, and glossary version. Review records include method, actor, a
traceable record reference/description and both exact text hashes.
A non-null check records a completed successful check. Failed or pending checks
remain null; retain their reports in the batch evidence.

| State | Meaning |
| --- | --- |
| `translated` | Text exists; required checks are incomplete. It cannot be published. |
| `llm_checked` | Current-session LLM self-check and technical admission records exist for these exact hashes. Native review is optional and must not be implied. |
| `reviewed` | The above checks plus an attributed `native-human` review of these exact texts. An LLM or technical validator cannot supply that review method. |
| `withdrawn` | Retained for traceability, excluded from publication. |

The validator verifies evidence structure/bindings and independently performs
technical admission. Metadata does not prove that a person performed a review;
maintainers must retain and inspect the referenced evidence. Synthetic examples
are labeled as such and claim no native human review.

### Independent revisions and public quizzes

- `policy_revision` identifies immutable completion requirements. Changing
  presentation or locale never resets passed credit, attempts or certificates.
- `semantic_revision` is an opaque, assigned ID for an immutable quiz meaning,
  answer mapping and threshold. A consumer must reject reuse with a different
  semantic payload. Never derive its public value from answers.
- `presentation_revision` hashes canonical JSON containing `quiz_id`, `locale`
  and a `texts` map from the declared presentation unit IDs to their displayed
  text. Explanation units are excluded. A display-only revision does not imply
  a new semantic/policy revision.

The [public quiz example](public-quiz.example.json) validates against
`public_quiz`. It includes displayed questions/options and opaque semantic /
presentation bindings, without answers, explanations, private grading
references or legacy answer-derived digests. Keep the complete source package,
grading inventory, `teaching_digest` and answer-derived hashes server-side.
After submission, return only the feedback authorized for that attempt.

## Canonical bytes and digests

File records contain root-relative `path`, exact byte `size` and SHA-256.
Canonical JSON uses UTF-8, lexicographically sorted object keys, original array
order, no extra spaces/newline and no non-finite numbers. The admitted model
contains integer numeric fields and rejects decimal/exponent syntax such as
`100.0` or `1e2`; it does not depend on float serialization.
No Unicode normalization occurs.

| Digest | Canonical input |
| --- | --- |
| Schema/registry digest | Exact bytes of the pinned schema/registry file. `source_schema_digest` is the original v1 schema; `contract_digest` is this v2 schema. |
| `translation_digest` | The ordered `translations` array. |
| `teaching_digest` | Object containing `files` (the entire frozen file inventory), `courses`, `profiles`, `selected_profiles`, `source_units`, `translations`, `assets`, `quizzes`. |
| `locale_digests[tag]` | Object with `units` (English source units or this locale's overlay units), `assets` (this locale plus shared assets), `quizzes` (each quiz's ID, semantic revision and presentations filtered to the locale). Preserve manifest array order. |
| `package_digest` | The whole manifest after removing only `snapshot.package_digest`. File hashes transitively bind the inventory bytes. |

The manifest itself is excluded from `snapshot.files` to avoid self-reference.
File records must be unique and sorted by path. `seal()` in the producer-side
helper computes these digests; it does not grant admission. A deterministic
archive must also fix timestamps, owners and file modes before being shipped.

## Consumer admission and security

Consumers pin their own reviewed schema, registry and validator/compiler. They
resolve an independently selected full source SHA, verify source files against
that checkout, then validate/build into a new directory. The local validator
checks package data and digests; it does not fetch Git or establish release
provenance. Never execute a validator, hook, JS, Python, SQL, shell command or
dependency installer obtained from a content package during ingestion. Lab code
is a learner-run download only.

Canonical declared paths use ASCII letters, digits, `_`, `-`, dots and slashes;
absolute paths, empty/dot/parent components, encodings, backslashes and symlinks
are rejected. Declared files are limited to 16 MiB each, 10,000 files and 128 MiB
in total. Documents allow at most 64 levels and 1,000,000 values. Content-authoring YAML aliases, anchors, duplicate YAML/JSON keys and
non-finite JSON values are rejected. Shared code/data downloads are opaque and
are never parsed or executed; a structured content reference forces strict
parsing even if the file also has a shared asset record.

Markdown permits inert code examples and a restricted HTML subset. Active HTML,
event handlers, inline CSS and unsafe URL protocols are rejected. The literal
SQL notation `<db>` in legacy display leaves is treated as inert text; consumers
must escape those display strings. Relative
links/images must remain inside the frozen file inventory. Remote images are
rejected; HTTP(S) text links, mail links and website-owned absolute routes have
explicit handling. SVG admits a
conservative shape/text subset, rejecting scripts, handlers, external references,
foreignObject, CSS and entity/DOCTYPE declarations. Image formats are PNG, JPEG,
GIF, WebP and SVG. Consumers also sanitize rendered HTML/SVG and enforce safe
download MIME types; producer admission does not replace browser boundaries.

## Compatibility, deployment and rollback

| Input | Existing consumers | Upgraded consumers |
| --- | --- | --- |
| Original v1 English tree | Continue using `catalog.yaml` and their pinned v1 schema. | Explicit v1 adapter exposes locale `en` while preserving original bytes, paths and release algorithms. |
| v2 teaching package | Reject as an unsupported interface. | Admit only through pinned v2 schema, complete profile coverage and consumer capability checks. |
| Generated v1 English projection | Must independently pass the old schema and full English-tree checks. | Record it as a distinct artifact; never overwrite an old release binding. |
| v1 event config / runtime / image release | Keep their existing tools and cleanup ownership. | Dispatch by that interface's own version and kind before adapting to English. Content v2 does not implicitly upgrade these formats. |
| Portable progress v1 | Existing self-study import remains unchanged. | Explicit legacy adapter preserves IDs, passed flags, best score and historical content binding. Locale is display metadata only. |
| Future portable progress/runtime format | Reject unsupported versions. | Requires its own schema, version dispatch, migration and downgrade behavior. This content schema does not define those formats. |

The original English adapter reads the original files at the recorded SHA and
uses the original website `questionDigest` algorithm. It does not translate or
re-render them to regenerate historical releases. Preserve old attempt bindings;
an obsolete browser submission must use its original release or receive an
explicit conflict/reload response. Workshop progress is untrusted self-study
data and cannot grant website credentials. Progress keys never include locale.

Upgrade producer admission first, then consumers with explicit v1/v2 dispatch,
additive database/presentation bindings and new runtime capabilities. Enable a
language only after its actual content and both consumers pass integration.
Activate complete snapshots atomically. On failure retain the current snapshot.
Rollback selects an earlier complete snapshot/runtime and keeps historical
records and additive database structures. An old binary requires a compatible
v1 snapshot; unavailable languages must give an explicit response with an
English entry, without silently serving English under another locale's URL.

## Local validation

```bash
python -m pip install -r tools/requirements.txt
python tools/check_content_contract.py
python tools/check_content_contract.py --root contracts/v2/examples
python tools/check_content_contract_v2.py --root contracts/v2/examples --allow-fixture
python -m unittest discover -s tools/tests -p 'test_content_contract*.py'
```

For a real producer output, use `--root <frozen-tree>` without `--allow-fixture`.
The checker validates declared coverage. The producer separately verifies
extraction completeness and optional/profile closure against the selected Git
commit, binds asset treatment and admits the completed tree before emitting
deterministic artifacts. See the [build instructions](../../locales/README.md).
