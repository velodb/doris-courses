# Multilingual Workshop runtime

New releases read independently maintained course files from each language tree.
Use `tools/build_course_files.py` and the private `package.v3.json` release
(format 3.0.0, ABI `doris-workshop-content-3.0`). English files stay in their
original roots; Kazakh files mirror them in `doris-course/kk-Cyrl/`.
Edit those files directly; there are no source-unit overlays or stale-English
translation gates. The existing v2 runtime/config envelope also supports the
new ABI with its exact capability set. Historical overlay-v2 packages retain
their original ABI and remain readable.

`course_sources` selects the physical language course root for kernel cwd,
imports, helper code, data and environments. The common library stays shared.
Notebook copies remain separate by language, persistent and never overwritten
on reopen. Unbound native-language Quiz attempts remain self-study; installed
Quiz bindings use the existing trusted grading/progress boundary.


Runtime v2 installs a complete English and Kazakh (`kk-Cyrl`) release before
learners enter. The `real-time-analytics-complete`, `data-warehousing-complete`
and `observability-complete` profiles cover all 32 primary modules, nine optional
or extension materials and the optional Data Warehousing Quiz. They may be
selected together, in the binding's declared course order. The retained
`rta-module-1` pilot selects only its primary lesson, Lab and Quiz and cannot be
combined with the complete RTA profile. Runtime v1 selections retain their
original English behavior. Existing English AMIs do not acquire these profiles
or multilingual capability by changing a configuration.

## Prepare one complete release

Commit the teaching inputs and use the matching builder from that commit:

```bash
SOURCE_REF=$(git rev-parse HEAD)
python tools/build_course_files.py --source-ref "$SOURCE_REF" \
  --profile rta-module-1 --locale en --locale kk-Cyrl --output /tmp/rta-content
python tools/workshop_content.py --package /tmp/rta-content \
  --output /tmp/rta-content-binding.json
```

For all three complete courses, replace the profile option with
`--profile real-time-analytics-complete --profile data-warehousing-complete
--profile observability-complete`. Use that same ordered list for
`course_profiles` and the generated content binding. The runtime bundles its
reviewed v2 profile registry, validates every course and exact Notebook
selection, and keeps identical module IDs in different courses separate.

Optional Markdown is available through the reader; optional Notebooks have the
same persistent per-language working copies as primary Labs. The optional
Data Warehousing Quiz uses its own exact source-path and presentation binding.
It never writes a primary module's receipt or completion credit.

Copy [the v2 configuration example](../examples/multilingual.example.json) to a
private operational directory. Replace its example account/image/expiry values,
set `course_ref` to `SOURCE_REF`, and replace the entire `content` object with
`rta-content-binding.json`. The example's repeated digest values and `f` revision
are placeholders. They do not identify an installed release or an AWS image.

```bash
python tools/workshop_config.py validate --config /private/event.json
python tools/workshop_runtime.py --config /private/event.json \
  --content-package /tmp/rta-content --output /tmp/rta-runtime
python /tmp/rta-runtime/host.py verify --bundle /tmp/rta-runtime
```

For a future private pilot AMI build, prepare fixed inputs with
`tools/workshop_image.py prepare --config /private/event.json
--content-package /tmp/rta-content --output /tmp/rta-image-inputs`.
These commands only prepare local artifacts. Complete-course profiles have
local content/runtime support; multi-course image preparation and cloud
qualification are separate work. The existing image-build,
independent-instance, course, browser and cleanup gates still apply. The v2 image
cache selects only Module 1's pinned Doris image. Neither local tests nor the
[release-shape example](../examples/image-release.v2.example.json) qualify an AMI.

Config, saved plan, runtime and image release dispatch on their own integer
versions. V2 binds the complete language set, default, profile, package and locale
digests, the content ABI, capabilities, and shared code/dependency fingerprints.
The bundle also hashes every payload, including its private teaching archive and
adapter. Admission checks all file bytes, paths, coverage, Quiz bindings and the
consumer message contract. It rejects preview/fixture content, missing languages,
untranslated optional selections, changed hashes and incompatible ABI. Nothing
calls an online translation service.

For shared entry routing, pass `--runtime-bundle /tmp/rta-runtime` to
`tools/workshop_access.py routes` with the existing route/identity arguments.
V2 route plans project only the bound message dictionaries into v2 gateway and
portal configuration. The shared portal keeps a separate language set for each
activity, so v1 activities remain English. These snapshots contain no grading
data. Gateway upload stages and compressed portal updates retain the 24 KB SSM
command bound and existing assignment checks.

## Installed files and learner copies

| Location | Purpose |
| --- | --- |
| `/opt/doris-courses/` | Pinned shared course code, environments and writable Lab files. |
| `/var/lib/doris-workshop/content/tree/` | Root-owned read-only admitted content, including per-language Notebook templates. Kept outside the Jupyter file root. |
| `/var/lib/doris-workshop/workspace/work/<locale>/<source-path>` | One persistent learner working Notebook per language and canonical path. |
| `/var/lib/doris-workshop/progress/` | Private SQLite progress, grading snapshots, backups and enriched runtime plan. |

The authenticated outline opens a working-copy page with the language selector,
an **Open saved working copy** link, a saved-file download and a link refresh.
The first visit creates that language's copy with an atomic no-replace operation;
concurrent opens and later visits reuse it. Use JupyterLab **File → Save Notebook**
before downloading or reloading the Notebook. Jupyter's File menu also provides
its normal save, reload and download actions. Switching languages or refreshing
the copy links never overwrites an existing file, clears output, executes a Lab,
restarts a kernel or changes Terraform bindings. Return to the original language
to resume its saved copy and existing Jupyter session. Unsaved edits remain in
the original open tab and still need saving there.

Working-copy generation retains cell IDs, order, all code and cell metadata.
It sets the managed kernelspec, inserts stable unit and original-English heading
aliases into Markdown, and routes Markdown links through authenticated content
and Notebook entry points. Frozen source-relative package bytes remain unchanged.
Reader headings retain stable unit IDs and original source fragments such as
`#environment`. Navigation mirrors explicitly redirect to English; unselected
Notebooks report unavailable. Generated machine downloads use the installed
artifact path and hash, including YAML with Compose anchors. No private grading
manifest is served through the reader or artifact endpoint.

All language kernels use the same course interpreter, shared course cwd, imports,
relative files and Docker services. Each Notebook session has separate Python
memory. The kernel adapter binds unchanged `CourseQuiz.from_yaml` calls to the
installed language's admitted presentation and messages. It passes the frozen
semantic/policy/presentation bindings to the shared progress helper. Both
languages credit the same module identity. The working-copy selector does not
transfer an in-flight Quiz attempt between kernels; returning to the original
open Notebook retains that kernel's attempt.

Readiness validates each declared language, the complete UI, templates, assets,
Quiz presentations and interpreter/import/cwd/restart behavior. It creates no
learner Notebook and starts no Lab containers. Failed content checks prevent
readiness and identify their language in operator diagnostics.

## Retry, updates and old installations

An exact bundle can be retried after a partial installation. Published content is
verified before reuse; a changed or corrupted tree is rejected without rewriting
it or learner copies. A changed bundle fingerprint, package, language set, default
or profile needs a new reviewed plan and fresh installation. There is no in-place
language-addition or template overwrite command in this release. Export progress,
retain saved Notebooks and the private store, and follow the existing replacement
workflow. A previous saved plan cannot silently acquire another content binding.

V1 configuration, runtime bundles and releases retain their formats and original
English behavior. Existing saved plans continue to diagnose and clean up through
their pinned tools. Do not replace the bundle or tooling recorded by an active
AWS binding. The lock metadata accepts exactly the reviewed ML-03 package-data
change to the common helper's pyproject; its dependencies and wheel lock hashes
are unchanged. This does not alter any existing AMI's declared capabilities.

## Local verification

```bash
python -m unittest discover -s tools/tests -p 'test_workshop_multilingual.py' -v
PLAYWRIGHT_MODULE="$PWD/ui/workshop/node_modules/playwright" \
  python tools/tests/run_workshop_multilingual_browser.py \
  --package /tmp/rta-content --output /tmp/rta-browser
python tools/tests/run_workshop_multilingual_labs.py --package /tmp/rta-content \
  --directory /tmp/rta-lab-fixture --output /tmp/rta-lab-evidence
```

The browser runner uses two isolated local learners, real installed templates,
Jupyter editing/execution/save/download and a real admitted Quiz. The Lab runner
executes unchanged code for both languages on one shared sandbox and checks the
10,158,080-row baseline. It requires unused `doris-rta` resource names and removes
only its created sandbox/network/volumes. It uses Docker and the public S3 dataset.
It does not establish Linux AMI, gateway, TTL, website or native-language review.

For a local website/Workshop comparison, give both consumers the same admitted
package and record its full source SHA, package digest, locale digests, profile,
contract and registry versions. Website compilation rewrites navigation for its
own routes, so its final bundle digest differs from the Workshop bundle. Compare
the shared teaching inputs, stable anchors, Notebook cell IDs/code/metadata and
Quiz question/option mappings instead. Website Notebook downloads must match
the corresponding package artifact byte for byte.

Keep the website database separate from production. Test English and Kazakh in
two tabs, then test two learners. Save a Notebook before comparing its download
or restarting services. Language switching preserves the original live kernel;
an explicit kernel or service restart clears Python memory and requires running
the initialization cell again. Saved files, outputs and recorded progress must
survive. Website and Workshop progress remain separate stores; portable imports
do not grant website credit.

The browser check inserts a fresh code cell through Jupyter's toolbar and checks
that every existing cell keeps its ID, type and source. Jupyter mounts only part
of a large Notebook, so the last visible editor cannot identify its last cell.
These local checks do not qualify cloud ingress, operating-system user isolation
or a production rollback. Retain the installed content, private progress store
and saved learner copies when stopping a review environment.
