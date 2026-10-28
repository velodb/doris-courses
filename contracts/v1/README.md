# Course content contract v1

`catalog.yaml` is the package entry point. It lists every course manifest and
the Programs that combine courses. `schema.json` defines the complete JSON
Schema Draft 7 contract for the catalog, courses, modules and quiz banks.
The website consumes data described by this contract, never repository code.

## Authoring

Update the course's existing `course.yaml` when adding or changing content.
The catalog owns catalog text and course order. Course manifests own titles,
summaries, level names, publication status, Lab setup, completion requirements
and certificate metadata. Modules point to lesson Markdown, Lab notebooks,
quiz YAML and optional readings/Labs. Keep the complete repository when
running a Lab: requirements can install the shared runtime from `../common`.

- A course `slug` and module `id` are permanent learner-data identities. The
  initial IDs `module-1`, etc. preserve existing website progress. The module
  `slug` names its source directory; `order` controls display numbering. Never
  regenerate IDs from order. Keep removed identities with `status: retired`,
  `required: false` and an empty `requirements` list.
- `published` modules have a lesson. Their requirements explicitly select
  `course_read`, `lab_opened`, `lab_declared_complete` and/or `quiz_passed`;
  each selected requirement needs its corresponding asset. Planned modules
  do not grant credit and do not enter the sitemap.
- Only a published course can enable completion. Certificates require that
  completion is enabled. The V1 completion strategy is all required modules;
  previously passed quizzes keep their credit for the same module identity.
- Each quiz declares an integer `passing_score_percent` from 1 to 100. Every
  question declares `type: single_choice`, unique semantic IDs, at least two
  options, one answer naming an option, and an explanation. Authors do not
  maintain website revision counters: consumers derive versions from content.
- Course-relative asset paths must resolve to files inside the repository.
  Manifest asset paths cannot be absolute, traverse parents or use symlinks.
  Ordinary lesson links may navigate to another course within the repository;
  the website resolves these through the complete route index.
- Use Markdown headings, lists, tables, fenced code and local images. Native
  `details`/`summary` disclosure blocks are supported. No JSX, executable
  expressions, scripts or author-provided CSS is part of the contract. The
  website must sanitize HTML and treat code blocks as text.
- Lab `python_version`, `requirements`, `guide`, platforms and dependencies
  describe the learner's environment. Deployment must not execute Lab code,
  install Python requirements or run commands supplied by the content package.

The website uses the full content commit SHA for every Notebook, README and
source link, and for the generated clone/checkout instructions. It rewrites
relative lesson/quiz/Lab links to website routes where available and copies
local images into versioned assets. Quiz answers belong only in server-side
grading artifacts, although the authoring repository itself is public.

## Validation and compatibility

```bash
python -m pip install -r tools/requirements.txt
python tools/check_content_contract.py
python tools/check_content_contract.py --base-ref origin/main
python tools/check_course_manifests.py
python -m unittest discover -s tools/tests -p 'test_content_contract.py'
```

The dedicated **Content contract** PR check validates every manifest, asset,
quiz and Program reference, rejects unknown fields/types, and compares stable
IDs and certificate codes with the PR base. The existing CI also validates the
package. Maintainers must require the Content contract check in main's branch
protection for it to be a merge gate.

Schema v1 is immutable after its initial introduction. A change that requires
a new field, interaction, question type or completion strategy needs a new
contract version and a consumer upgrade. Updating ordinary content, adding
courses/modules, changing quiz answers or publishing existing assets stays
within v1 and requires no website source edit.

Consumers must validate against their own pinned copy of this schema, rather
than executing a validator or trusting a replacement schema from a downloaded
content commit. A deployment records website SHA, content SHA, package digest
and immutable policy/quiz version bindings. It must not reinterpret historical
quiz attempts or certificates when a newer package is deployed.
