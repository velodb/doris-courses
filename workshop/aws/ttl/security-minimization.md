# Runtime-only cleanup image

The 2026-11-02 candidate is built from source
`744734d16cf7f9b8adfdc62a470c56fe5ffb66df`, with local image ID
`sha256:a9c98f12308e3081b16f20ca56f8082d2a84df18bc1a905199b7a0d67a7dae44`.
Its executable source-tree hash is
`2f48241f761f6c98b109c5bbb260f72a811e9649a0108585d59a4a237b772d2b`.
**Local acceptance passed; deployment review remains open.** This image is
the fixed cleanup worker, separate from the course AMI and learner environment.

## Runtime contract

The [Dockerfile](Dockerfile) prepares dependencies using the same pinned Python
3.12.16/Trixie and Terraform images, then copies a curated filesystem into a
`scratch` stage. It does not install a different distribution or mix Debian
release repositories. Terraform 1.17.1, AWS provider 6.71.0 and all 16 locked
Python distributions retain their versions and artifact hashes.

[runtime-packages.json](runtime-packages.json) pins 14 native package payloads.
[runtime_rootfs.py](runtime_rootfs.py) rejects changed, missing or duplicate
package records, copies retained payload files and licensing data, and preserves
their original dpkg status/file-list provenance for scanning. The generated
`/opt/doris-workshop/native-runtime.json` records file hashes, symlink targets,
dynamic-library dependencies, source-package versions and paths already absent
from the slim base. The retained dpkg records describe runtime payloads; this
filesystem is not an apt-managed installation. Package configuration tools and
their configuration-time dependencies are intentionally absent.

The runtime contains Python, Terraform, the packed provider mirror, the sealed
source and writable scratch. It keeps the pre-generated CA store, SSL data,
OS identity and resolver policy. Shells, package managers, Perl, mount/nsenter,
infocmp, coreutils, libacl and libmount are absent. The build checks the ELF
dependencies of every retained executable/extension under `/usr/local` against
the assembled filesystem, failing on missing libraries.

Five optional native extensions are explicitly outside this worker's contract:
`_curses`, `_curses_panel`, `readline`, `_sqlite3` and `_uuid`. Their corresponding
ncurses/readline, SQLite and libuuid payloads are absent, rather than hidden from
the scanner. The cleanup code uses `uuid.uuid4()`, whose Python path is verified
without `_uuid`. Interactive terminal and SQLite APIs are unsupported here.
The pinned upstream slim image also contains an unusable `_tkinter` extension
without Tcl/Tk libraries. The assembler verifies that exact inherited failure
and omits the orphan extension; it does not accept arbitrary missing dependencies.

Use this image through its fixed Python entry point. It has no shell for
interactive troubleshooting. Diagnostic overrides can invoke Python directly;
they remain separate from the production entry point and its credential checks.

## Verification

The candidate was built from a clean Git archive, using checksum-verified
provider/wheel caches. No operator environment, credentials, state, `.git` or
private planning files enter the build context.

Actual Linux amd64 containers ran on an arm64 Docker Desktop host with UID/GID
10001, read-only root, no network, all capabilities dropped, no-new-privileges,
256 PIDs, 1 GiB memory and 0.5 CPU. Checks passed for:

- 767 retained payload file hashes/symlinks, exact native/Python inventories,
  absent tools/libraries/extensions, all 72 retained standard-library native
  extension imports, UUID generation, localhost resolution and 148 loaded CAs.
- Source seals, writable `/work`, no runtime pip, unchanged provider locks,
  provider schema loading in an independent local fixture, and both original
  modules' backend-disabled initialization and validation.
- The installed SDK's container-role provider against loopback, STS Stubber,
  four ambient credential/endpoint rejections and two production-entry rejections.

All 307 host tool tests passed. These results do not establish native amd64 CI,
live AWS TLS/routing, IAM permissions, Fargate execution or natural TTL behavior.

The first assembly stopped on upstream's missing Tcl/Tk dependencies. An
intermediate 21-package candidate passed local acceptance but retained optional
terminal/SQLite/UUID libraries. Its full scan found eight High and nine Unknown
occurrences. It was superseded by the explicit optional-extension contract above.

## Full scan with one current database

Trivy 0.76.1 used a newly downloaded official
`ghcr.io/aquasecurity/trivy-db:2` snapshot, updated
`2026-11-02T01:14:05Z`, SHA-256
`72ddbfd4f4fb1b6458b1cceb050e3c31733fd832615deab4be241ded106fece3`.
The scanner executable hash remains
`5c03401caf9d3cc04d9e0c7f3abd102e97518e8237465cce702162b605535e8d`.
Analysis used local image exports and expanded provider executables, with
telemetry and database/dependency updates disabled. No image or SBOM was uploaded.

The earlier hardened image (`1a3e412`, image `e5cb4e89…`) was rescanned using
**the same current DB** as this candidate:

| Result | Hardened baseline, current DB | Runtime-only candidate |
| --- | ---: | ---: |
| Debian package payloads | 87 | 14 |
| Python distributions | 16 | 16 |
| Terraform Go module entries | 260 | 260 |
| Expanded provider Go module entries | 387 | 387 |
| Critical | 0 | 0 |
| High | 45 | 0 |
| Medium | 71 | 27 |
| Low | 61 | 8 |
| Unknown | 10 | 2 |
| Total package finding occurrences | 187 | 37 |
| Unique advisory IDs | 86 | 31 |

The candidate introduces no advisory ID relative to that baseline. Python has
no reported findings. The old 168/73 baseline used the 2026-10-31 DB; it is a
historical result, not the denominator for this comparison. The current DB adds
SQLite findings to the unchanged baseline, including
[CVE-2026-119373](https://security-tracker.debian.org/tracker/CVE-2026-119373).
The final worker contains neither SQLite's library nor its Python extension.

The two Unknown occurrences remain GO-2026-5932, one per unchanged Go binary.
Their hashes match the [earlier applicability review](security-applicability.md).
Its stripped-symbol analysis limitation still applies; removing OS tools does
not resolve OpenPGP package applicability. The 27 Medium and eight Low
occurrences also remain in the raw reports and require review. No ignore file,
unfixed filter or automatic exemption was added. The complete scan exits `2`
and reports `review_required=true`, rather than granting release approval.

Local Docker image size decreased from 476,104,087 to 278,897,802 bytes. These
are the engine's image-size values, not compressed registry transfer sizes.

## Adoption boundary

Repeat builds and isolated verification using the [image commands](README.md#image-permissions-and-control-resource-retirement),
then run the [full scan helper](../../../tools/scan_workshop_image.py) with a
current official DB. Changing the native package/extension contract requires
review, a new clean-source build and the same checks. Keep raw reports and
artifact/source bindings for each candidate.

The final image remains local and Scheduler stays disabled. Enrollment requires
a new reviewed source/image binding; do not retarget the previous image's
binding. Shared cleanup, Fargate/network/IAM acceptance, natural expiry,
state-lock concurrency and control-resource retirement retain their original
prerequisites.
