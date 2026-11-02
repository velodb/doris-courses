# Cleanup runner image scan

## Current runtime-only candidate (2026-11-02)

Source `744734d16cf7f9b8adfdc62a470c56fe5ffb66df`, local image
`sha256:a9c98f12308e3081b16f20ca56f8082d2a84df18bc1a905199b7a0d67a7dae44`,
passed isolated image acceptance and a complete OS/Python/Terraform/expanded-provider
scan. **Deployment review remains open.** The image is local and Scheduler stays
disabled.

Against the same fresh official DB (`2026-11-02T01:14:05Z`), the earlier hardened
baseline has 187 package occurrences / 86 advisory IDs; the new candidate has
37 / 31. High decreases from 45 to zero, Critical stays zero, and OS payloads
fall from 87 to 14. The candidate retains 27 Medium, eight Low and two Go Unknown
occurrences, with no Python findings or new advisory ID relative to that baseline.
No finding filter or automatic exemption was added; scan exit remains `2`.

See the [runtime contract, exact bindings and current scan](security-minimization.md)
for the native-module exclusions, file checks, same-DB comparison and adoption
requirements. The [earlier Go applicability limitation](security-applicability.md)
still applies to the unchanged stripped executables. The historical counts below
use an older DB and must not replace the current comparison.

## Historical hardened image (2026-11-01)

The hardened runner built from source
`1a3e4127339774e721f556004964e8f6a4c5a494` was scanned on 2026-11-01.
Its local image ID is
`sha256:e5cb4e89ec4035b11ba9ce78637e2a320e7e34efe45237945551df619ec41ae9`.
**The scan is complete; deployment review remains open.** The image has not
been pushed or deployed, and Scheduler remains disabled.

The [2026-11-02 applicability review](security-applicability.md) maps the High
and Unknown findings to installed files and checks the Go analyzer's symbol
coverage. Three advisory IDs concern absent components; six OS advisories and
both Go occurrences remain open. That review does not change the raw counts
below or constitute a fresh scan.

### Coverage and results

Trivy 0.76.1 came from its immutable
[official release](https://github.com/aquasecurity/trivy/releases/tag/v0.76.1),
with the archive checked against release asset hashes and the checksums file.
Both scans below used the same official DB snapshot, updated at
`2026-10-31T13:09:00Z`; it was under 12 hours old for the hardened scan.
Analysis used local files with telemetry, version checks and DB/dependency
network updates disabled. No image or SBOM was uploaded to a scanning service.

The original baseline used source
`593fb436295601a51b79b32658cbcf99c4b1f657`, image
`sha256:abb613303f1456747be0c4b1c6c7cdf51c2bcc81b9193fe91133d0f0bf906466`.
The hardened image uses Python 3.12.16 on Debian 13.7 (Trixie), with the
publisher's Linux amd64 manifest pinned in [images.json](images.json).
Terraform 1.17.1, AWS provider 6.71.0 and the 16 locked Python runtime
dependencies retain their versions and artifact hashes.

| Severity | Original baseline | Hardened image |
| --- | ---: | ---: |
| Critical | 2 | 0 |
| High | 59 | 44 |
| Medium | 110 | 60 |
| Low | 106 | 61 |
| Unknown | 4 | 3 |
| Total package finding occurrences | 281 | 168 |
| Unique advisory IDs | 126 | 73 |

Coverage is 87 Debian packages and 16 installed Python packages, compared
with 105 and 17 in the baseline. Both scans cover 260 Terraform Go module
entries and 387 AWS-provider Go module entries. The provider ZIP is verified
against the image lock, expanded and scanned as an executable. The hardened
image has no reported Python findings in this DB snapshot.

Counts include unfixed issues and source-package matches. An advisory can
affect several packages or both binaries; occurrences do not establish
reachability. No ignore file, unfixed filter or automatic waiver was applied.

### Changes verified

- **SQLite / CVE-2025-7458:** the hardened image contains
  `libsqlite3-0 3.46.1-7+deb13u2`, which Debian lists as fixed. The original
  Bookworm package remains vulnerable; its crafted-SQL prerequisite was not
  used as a waiver. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2025-7458)
- **zlib / CVE-2023-45853:** this finding is absent on Trixie's
  `1:1.3.dfsg+really1.3.1-1+b1`. Preserve the separate vendor explanation for
  the old Bookworm finding: it concerned MiniZip, which that source did not
  build into its binary packages. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2023-45853)
- **Runtime installer removed:** the build installs only hash-locked wheels,
  runs `pip check`, then uninstalls pip and its bundled dependencies. Upgrading
  to pip 26.2.2 removed the original six pip findings but introduced five High
  vendored-dependency findings in a tested intermediate image. The final image
  needs no installer. Runtime verification requires the exact 16-package
  distribution inventory and rejects an importable pip package.

The candidate is built from a clean Git archive. Local verification uses
Linux amd64, UID/GID 10001, no network, read-only root, all capabilities dropped,
a 1 GiB memory limit and 0.5 CPU. It checks source seals, writable scratch,
provider schema loading, both original modules' initialization/validation,
the installed SDK's container-role path and ambient credential rejection.
This is local image acceptance; Fargate permissions and natural TTL remain
unverified.

### High and Unknown findings

The 44 OS High occurrences represent eight advisory IDs. The complete report
retains every affected package, including source-package repetitions:

| Source package | High advisory IDs |
| --- | --- |
| util-linux | CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410 |
| acl | CVE-2026-54369 |
| ncurses | CVE-2025-69720 |
| systemd | CVE-2026-16742 |
| perl | CVE-2026-9538 |

The DB lists no fixed Trixie version for those entries. Debian still marks
the installed source versions vulnerable. The follow-up review confirms that
mount/nsenter, the affected libacl symbols and infocmp are installed, so those
six advisories remain open. It finds the affected systemd-homed component
absent from this exact image; only libraries from that source are installed.
The non-root/read-only container configuration does not close the remaining
findings.
[util-linux tracker](https://security-tracker.debian.org/tracker/CVE-2026-76642),
[systemd tracker](https://security-tracker.debian.org/tracker/CVE-2026-16742)

The three Unknown occurrences are:

- **CVE-2026-82560:** a Perl Pod::Text resource-exhaustion issue. Debian marks
  Trixie's source package vulnerable. The follow-up file and Perl load checks
  find the affected Pod::Text module absent, as they do Archive::Tar for
  CVE-2026-9538. Both conclusions apply only to this exact image.
  [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-82560)
- **GO-2026-5932, in both Go binaries:** golang.org/x/crypto v0.58.0 is detected.
  The advisory concerns deprecated OpenPGP packages and has no fixed version.
  Decoding the actual binaries' Go function tables found x/crypto functions
  but no `golang.org/x/crypto/openpgp` function entries. Official govulncheck
  v1.9.0 cannot extract package symbols from either stripped artifact and
  falls back to module matching; positive controls confirm the limitation.
  Complete inline/package applicability remains unproved. Keep both raw
  findings open; a compiler upgrade alone does not resolve them.
  [Go advisory](https://pkg.go.dev/vuln/GO-2026-5932),
  [Go symbol table documentation](https://pkg.go.dev/debug/gosym)

Resolve the installed-component findings and Go applicability before release.
The follow-up assessment leaves the original 168 raw occurrences intact and
does not assess the Medium/Low findings. This document grants no blanket
exemption for an unfixed package, container isolation or a source-package match.
Enrolling this candidate requires
a new reviewed source/image binding; do not retarget an existing bound request.
Rebuild, verify and rescan
against a current DB after further changes.

### Repeat locally

Obtain and verify Trivy 0.76.1 and a current official Trivy DB. The helper does
not install a scanner or update a database:

```bash
.venv-workshop/bin/python tools/scan_workshop_image.py \
  --image doris-workshop-ttl:aws20-hardened-final \
  --trivy "$TRIVY_BINARY" --trivy-sha256 "$TRIVY_BINARY_SHA256" \
  --cache-dir "$TRIVY_CACHE" \
  --output workshop/aws/.local/runner-scan-new
```

It verifies the scanner hash, DB age, local image and OS/Python/both-Go-binary
coverage. It exports fixed paths through a container that is never started,
removes its own container/anonymous volume, and deletes temporary exports.
Reports stay local. Empty provider output is incomplete coverage.

Exit `0`: complete scan with no High/Critical/Unknown findings. Exit `2`:
complete scan requiring review. Exit `1`: failed execution or incomplete
coverage. Both reviewed images return `2`. None of these exits certify IAM,
Fargate, natural TTL or final release acceptance.
