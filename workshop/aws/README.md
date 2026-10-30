# AWS workshop configuration

This directory defines the configuration and planning manifest for temporary
AWS course environments. The current tool validates local files and pinned
course metadata. A [host runtime bundle and installer](runtime/README.md) and
[Terraform integration scripts](terraform/README.md) are available.
The [temporary course reader](runtime/access.md) and
[browser URL mapping](browser-urls.md) reuse the host runtime.
Use the [activity lifecycle CLI](lifecycle.md) for reviewed-plan up, status,
runtime waiting and basic activity destroy.
For a validated private AMI, use [prebuilt activity startup](prebuilt.md),
including pinned release metadata and guarded single-learner replacement.
Real EC2 infrastructure, the two-host CLI lifecycle, and default `rta-main`
Notebook execution have been verified. Browser checks through operator SSM
forwards also covered the reader, Notebook editing/save/download, kernel
restart and reconnection, quiz widgets, terminal Docker access and the Doris
QueryProfile page, including a temporary single-instance HTTPS/token entry.
Slow SSM cold loads required a reload after frontend assets arrived. Shared
HTTPS/OIDC routing remains a separate step. The
[deployment architecture](architecture.md) and
[first real Terraform runbook](first-run.md) describe the live preflight and
saved-plan gate for the first SSM-only host.

## Try the offline tool

From the repository root, use Python 3.12 or later and the repository's check
dependencies:

```bash
python3 -m venv .venv-workshop
.venv-workshop/bin/python -m pip install -r tools/requirements.txt
.venv-workshop/bin/python tools/workshop_config.py validate \
  --config workshop/aws/examples/event.example.yaml
.venv-workshop/bin/python tools/workshop_config.py manifest \
  --config workshop/aws/examples/event.example.yaml \
  --output workshop/aws/.local/example-plan-01.json
```

The [base-image example](examples/event.example.yaml) and
[prebuilt-image example](examples/prebuilt.example.yaml) use fictional account
and AMI IDs, a reserved example domain and an illustrative expiry. Replace
them with confirmed activity parameters before using them for an event. The
configured course commit must exist in the local Git object database; fetch
that revision explicitly when using a shallow clone. The tool never fetches
code or calls AWS. Exit code `0` means offline validation passed; `2` means
invalid input or an unreadable/unwritable file.

## Configuration contract

[config.schema.json](config.schema.json) defines the required fields and rejects
unknown fields at every level. The CLI also checks real timestamps, profile
dependencies and consistency across fields. YAML and JSON are accepted;
duplicate keys, booleans used as numbers and decimal counts are rejected.
Errors identify the failing field without printing its value.

| Fields | Meaning |
| --- | --- |
| `event_id`, `owner_id` | Stable activity and operator/team IDs, up to 32 lowercase letters, digits and hyphens, starting with a letter |
| `aws_partition`, `aws_account_id`, `aws_profile`, `region` | Expected deployment target and explicit local credential-profile name; account IDs are quoted 12-digit strings |
| `network_mode` | `public-egress` or `private-egress`; records the intended network choice |
| `domain`, `auth.mode`, `auth.config_ref` | DNS name, `oidc` or `temporary` login, and an opaque reference to separately managed authentication configuration |
| `course_ref`, `course_profiles`, `allow_draft_courses` | Full 40-character Git SHA, explicit profile selection and explicit opt-in for unpublished courses |
| `image` | Declared AMI ID, Region, owner and `x86_64` architecture; `base` installs the runtime later, `prebuilt` additionally binds a release reference to the exact course SHA and profile set |
| `instance_type`, `disk_gib` | Intended EC2 type and positive integer root-disk capacity |
| `learner_ids`, `spare_count` | Explicit stable learner IDs and 0–999 unassigned spare slots |
| `expires_at`, `budget_usd` | Future RFC 3339 timestamp with seconds and timezone, and a finite positive USD budget |

All fields are required. `image.course_ref`, `image.course_profiles` and
`image.release_ref` are required only in `prebuilt` mode. Image Region must
match event Region; prebuilt course SHA and profiles must match the event.
The supported partition declarations are `aws`, `aws-cn` and `aws-us-gov`.

Offline validation checks Region and instance-type syntax, declared architecture
and metadata consistency. Actual account identity, Region/service availability,
AMI existence, ownership and architecture, EC2 compatibility, capacity, pricing,
permissions, DNS control and authentication references require AWS preflight.
No budget enforcement, expiry scheduler or login service runs in this tool.

The runtime contract is Linux x86_64, Python 3.12 and a complete checkout at
`/opt/doris-courses`. Course virtual environments will live in their respective
course directories. This records installation targets; it does not install
environments or certify that the courses pass on Linux.

## Course profiles

[profiles.json](profiles.json) defines the IDs and their dependencies. Each
selected profile resolves its lesson, Lab and quiz paths from `course.yaml`
**at `course_ref`**, regardless of working-tree edits. Main profiles select
required modules and reject unavailable or planned content. Optional paths
must be registered in that pinned course manifest and exist in the commit.

| Profile | Scope and requirements | Kernel |
| --- | --- | --- |
| `rta-main` | Real-time Analytics required modules, including Kafka and the multi-node Lab | `doris-rta` |
| `rta-metabase` | Metabase optional Lab; requires `rta-main` | `doris-rta` |
| `dw-main` | Data Warehousing required modules, including the lake environment | `doris-dw` |
| `dw-kafka`, `dw-cdc` | Corresponding optional ingestion Labs; require `dw-main` | `doris-dw` |
| `dw-bi`, `dw-backup` | Enable the optional BI or teaching-backup services used within main Labs; require `dw-main` | `doris-dw` |
| `dw-extensions` | Three extension Notebooks; requires `dw-main` | `doris-dw` |
| `obs-main` | Observability required modules | `doris-obs` |
| `obs-local-model`, `obs-openai` | Two mutually exclusive routes for optional Lab 7A; require `obs-main` | `doris-obs` |
| `ai-mcp` | Only AI module 1; requires a separately implemented browser Host | `doris-ai` |
| `ai-functions` | Only AI module 2 | `doris-ai` |

AI is currently a draft course: selecting either implemented AI profile also
requires `allow_draft_courses: true`. No profile includes planned AI module 3.
Selecting profiles records the requested scope; it does not imply deployment
support or successful Lab execution. Model credentials stay outside these files.

Each kernel's working directory is its course root. Runtime installation must
register these distinct kernels and create Notebook working copies that select
the correct kernel. Source Notebook metadata and the existing website content
contract remain unchanged.

## Stable identities and planning manifests

Learner IDs are explicit names such as `s001`. Reordering the list, adding a
learner or removing another learner leaves existing keys unchanged. Renaming
an ID changes its identity. Spare IDs use the reserved `spare-001` through
`spare-999` namespace and have no learner assignment.

The [manifest schema](manifest.schema.json) records:

- Activity ownership, expected account/partition/Region, and an event state key
  of `aws-workshop/<partition>/<account>/<region>/<event_id>/terraform.tfstate`.
- Course commit, deployment checkout commit, tool version, tool-input content
  hash and whether the tool's source has local changes.
- Desired AMI metadata, profile/module scope, runtime, access configuration,
  budget and expiry.
- An allocation map keyed by learner/spare ID, with a stable `terraform_key`,
  an `event_id/id` ownership key, role and active flag.

The allocation keys are intended for Terraform `for_each`, which identifies
instances by map key. See the
[Terraform reference](https://developer.hashicorp.com/terraform/language/meta-arguments/for_each).
Image architecture is an independent AMI property that later preflight must
query; see the [EC2 Image API](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_Image.html).

When revising the roster, supply the prior manifest and write a new file:

```bash
.venv-workshop/bin/python tools/workshop_config.py manifest \
  --config workshop/aws/events/my-event.yaml \
  --previous workshop/aws/.local/example-plan-01.json \
  --output workshop/aws/.local/example-plan-02.json
```

The prior activity, account, partition, Region, owner and state key must match.
Withdrawn learner/spare allocations remain with `active: false` so a roster
change retains cleanup context. Reinstating the same ID restores its original
key. Only active allocations belong in the next Terraform `for_each` input.
Omitting `--previous` starts a fresh planning history.

The tool creates output files with mode `0600` and refuses to overwrite any
existing file. Preserve these operational records outside the learner instance.
They are marked `status: planned` and contain no invented EC2 instance IDs or
readiness claims. The state key is a logical locator, not a configured backend.
Future deployment/destroy commands must reconcile actual Terraform state and
resource ownership, using the original target even after expiry or roster
changes. A plan alone cannot locate or prove deletion of actual cloud resources.

## Files and secrets

Schemas, profile definitions and sanitized examples can be committed. Put local
event configurations in ignored `events/` and generated records in ignored
`.local/`; `*.local.yaml` and `*.local.json` are also ignored in this directory.
Other output paths are allowed, so check the destination before sharing files.

Provide AWS credentials through the selected local profile/SSO, and provide
IdP secrets, learner passwords, TLS keys and model keys through a separate
controlled process. `auth.config_ref` identifies that configuration and never
contains its secret values. Keep Terraform state and deployment credentials
off learner instances. `aws_profile` is local input and is omitted from manifests.

This configuration adds no learning progress, persistent grades, completion
certificates, automatic learner backups or required learning-record backend.
Lab files and teaching data can still persist on the instance during an event;
destroying or rebuilding an instance does not preserve them.

## Verification

```bash
.venv-workshop/bin/python -m unittest discover -s tools/tests -p test_workshop_config.py -v
```

These tests exercise schema and semantic rejection, pinned Git metadata, profile
scope, draft/planned boundaries, roster stability, ownership checks, private
output and overwrite refusal. The repository check job discovers them through
the existing `tools/tests` suite. They require no AWS identity or Docker.

Fixed private-image construction is described in the [Packer build guide](packer/README.md). Images remain candidates until independently validated on a new instance.

The [fresh-image validation guide](image-validation.md) describes isolated candidate validation and cleanup before activity publication.
