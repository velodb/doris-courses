# Kazakh course sources

Edit these course files directly. Each course has its own Markdown lessons, Jupyter
Notebooks, Quiz YAML, course manifest, helper code, requirements, environments,
data, tests and images. The three course directories mirror the English course
layout. `catalog.yaml` selects these three courses using repository-relative paths.
`ui.yaml` is a plain message-key to text mapping.

There is no translated AI course here. The catalog declares no program completion
qualification; the complete program still requires the English catalog's fourth
course. Links to untranslated AI material explicitly lead to its English source.

Keep the repository together. Course requirements install the single shared
`doris-course/common` package through `-e ../../common`; each course has its own
virtual environment. The one-time migration preserved all Notebook code cells
and Quiz question, option and answer IDs. Future examples and code can be edited
independently; shared grading identities remain equivalent across languages.
Course helper paths continue to discover their own course root.

Historical `unit-*` anchors and English heading aliases are ordinary HTML anchors
saved in Markdown and Notebook Markdown cells. Preserve these IDs when editing a
section so existing links keep working. New authoring and rendering need no
English paragraph matching, heading-count matching, or translation JSON overlay.

Existing English PNG pixels are retained. Kazakh alt text, captions and complete
adjacent label mappings explain the diagrams; the image pixels were not translated.
The text was translated and self-checked by the session LLM, with technical checks,
and has not received native human language review.

The one-time migration tool is `tools/migrate_course_translations.py`. It consumes
the historical admitted package, writes a **new** output directory, and refuses to
overwrite an existing source tree. `--verify-only` reads and audits a migration
without writing. The tool and the historical package are not build dependencies
and are not an export/import editing workflow.

For normal maintenance, edit the files and run `python tools/check_course_files.py`
from the repository root. Build committed files using `tools/build_course_files.py`
as described in `locales/README.md`.

To replay the historical migration into a new directory (optional):

```bash
python tools/migrate_course_translations.py \
  --package /path/to/historical-package --output /tmp/new-kk-Cyrl-source
python tools/migrate_course_translations.py \
  --package /path/to/historical-package --output /tmp/new-kk-Cyrl-source --verify-only
python -m unittest discover -s tools/tests -p test_migrate_course_translations.py -v
```

Use an environment with `tools/requirements.txt`. Historical package replay tests
run when that package is available, or when `DORIS_MIGRATION_PACKAGE` points to it.
The native source checks and normal course authoring need no historical package.
