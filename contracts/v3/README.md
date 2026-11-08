# Independently authored course files

Format `3.0.0` uses `package.v3.json` and Workshop ABI
`doris-workshop-content-3.0`. Its capabilities are `course-files-v1`,
`notebook-copies-v1` and `quiz-presentations-v2`. An old v2 consumer cannot
claim support by accepting a changed package filename or capability list.

Authors maintain ordinary Markdown, Notebook, Quiz YAML, course metadata,
images, code and data under their language's physical course root. The
one-time migrated Kazakh root is `doris-course/kk-Cyrl/`; English roots retain
their original paths. No source-unit map, translation overlay, review state,
English paragraph alignment or target source-text hash is an authoring input.

The generated manifest contains:

- Source SHA, exact raw-input and artifact inventories, deterministic builder
  identity and package/locale digests.
- `course_sources[locale][course_slug]`, mapping stable course identities to
  physical authoring roots.
- Course/profile selections and private Quiz grading/presentation bindings.
- Assets with `source_path` (logical canonical identity), `source_file`
  (physical language source), locale, kind and generated file hash.

Normalized projections keep consumer routes and working-copy identities:
`rendered/<locale>/doris-course/<canonical-course>/…`. They preserve source
bytes except the named legacy Notebook Markdown style-attribute normalization
and YAML-to-JSON metadata/UI projections. The raw sources remain inventoried.
Relative references resolve from the physical source, then map to their logical
course identity. New language-only assets are part of that language's closure.

`tools/content_files_contract.py` independently checks the complete package,
raw inputs/projections, safe content, Notebook validity, references, exact
profile closure, UI placeholders and stable grading identities. With an
independently fetched source directory, it compares every raw input byte to
Git. It never imports a producer or code supplied by the package. Package
scripts, dependency installers and Labs are not admission hooks.

Language text, headings, Notebook cells, examples, helpers and data may change
independently. Module IDs, selected paths/order/completion policy and ordered
Quiz question/option IDs, answers and thresholds retain shared grade identity.
Display changes create a presentation revision; machine grading changes create
a semantic revision. Existing legacy identities are reused when their machine
meaning is unchanged, preserving old progress and database bindings.

The teaching archive and raw answer banks remain private. Website projection
may publish only selected public documents, images and allowlisted downloads;
registered and unregistered Quiz banks never become ordinary data attachments.
Native, unbound Quiz attempts remain self-study. Trusted receipts require the
installed/server-owned grading and actor boundary.

Historical overlay-v2 artifacts remain supported through their original
contract and ABI. New course-file releases use their own validator and builder,
with no dependency on historical translation JSON or the migration tool.
