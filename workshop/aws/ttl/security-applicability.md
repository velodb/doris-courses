# Cleanup runner finding applicability

Reviewed on 2026-11-02 for source
`1a3e4127339774e721f556004964e8f6a4c5a494` and local image
`sha256:e5cb4e89ec4035b11ba9ce78637e2a320e7e34efe45237945551df619ec41ae9`.
**Deployment review remains open.** This review covers the 44 High and three
Unknown occurrences in the [2026-11-01 scan](security-review.md). It does not
rescan the image or assess the remaining Medium/Low findings.

Three advisory IDs concern components absent from this exact image. Six OS
advisories concern installed components and remain open. The Go advisory also
remains open in both executables because the official analyzer falls back to
module-level matching on these stripped binaries. Raw scan counts and reports
remain unchanged; these conclusions do not grant deployment approval.

## OS package and file review

The image's dpkg database, package file lists and installed files were inspected
with UID/GID 10001, no network, read-only root, all capabilities dropped,
no-new-privileges, 1 GiB memory and 0.5 CPU. The image ID and source seal match
the scanned candidate. The review maps each source-package finding to its
affected component, rather than treating all binaries from one source package
as independent flaws.

| Advisory | Raw High/Unknown occurrences | Installed component and conclusion |
| --- | ---: | --- |
| [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642) | 9 High | **Open.** `mount` and `libmount1` 2.41.5-0+deb13u1 are installed. The helper/post-mount-hook issue involves libmount, which `/usr/bin/mount` loads. Its mode is 4755. |
| [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408) | 9 High | **Open.** `/usr/bin/nsenter` is installed and its help advertises `--join-cgroup`, the affected option. |
| [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409) | 9 High | **Open.** The affected mount stack is installed. The advisory's `X-mount.subdir` and kernel prerequisites require separate review; version numbering alone does not establish absence. |
| [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) | 9 High | **Open.** The installed SUID mount tool uses libmount. The restricted bind-mount source race is not excluded by a source-package match. |
| [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369) | 1 High | **Open.** `libacl1` 2.3.2-2+b1 exports all four affected pathname functions: `acl_get_file`, `acl_set_file`, `acl_extended_file` and `acl_delete_def_file`. `cp`, `mv` and GNU `tar` load this library. |
| [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) | 4 High | **Open.** `ncurses-bin` 6.5+20250216-2 contains `/usr/bin/infocmp`, where the reported `analyze_string` overflow occurs. Library-only reasoning cannot dismiss this image's finding. |
| [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) | 2 High | **Affected component absent.** Only `libsystemd0` and `libudev1` 257.13-1~deb13u1 are installed from this source. Their file lists and the image contain no `systemd-homed`, `systemd-homework`, homed service or `homectl`. The [upstream advisory](https://github.com/systemd/systemd/security/advisories/GHSA-jm29-p7hh-vjhv) locates the flaw in homed's home-record authentication handler. |
| [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538) | 1 High | **Affected component absent.** The image contains `perl-base` 5.40.1-6+deb13u1, without `Archive/Tar.pm`. Loading `Archive::Tar` fails with “Can't locate”; the affected tar parser is absent from the package manifest and filesystem. |
| [CVE-2026-82560](https://security-tracker.debian.org/tracker/CVE-2026-82560) | 1 Unknown | **Affected component absent.** `Pod/Text.pm` is absent from the package manifest and filesystem; loading `Pod::Text` fails with “Can't locate”. The advisory concerns POD formatting in this module, rather than the base Perl interpreter. |

Debian still lists the corresponding Trixie source versions as vulnerable on
the review date. Component absence is an image-specific applicability finding,
not a claim that Debian fixed those versions. It accounts for three High and
one Unknown occurrences. The other 41 High and two Go Unknown occurrences
remain unresolved. No ignore file or scanner filter consumes this assessment.

The fixed cleanup adapter invokes `terraform init`, `show`, `plan -destroy`
and `apply` with argument lists. The two bound modules have no provisioner or
external command blocks. Their checked-in provider mirror also avoids runtime
registry downloads. This source inspection found no intended call to the OS
tools above; it does not prove their vulnerable code unreachable. An empty
fstab, task isolation and lack of intended callers are supporting context,
not reasons to close the six installed-component findings. No exploit was run.

## Go package and symbol review

[GO-2026-5932](https://vuln.go.dev/ID/GO-2026-5932.json) covers seven deprecated
`golang.org/x/crypto/openpgp` package paths across all versions. Both executables
contain module metadata for `golang.org/x/crypto v0.58.0`.

Both artifacts were exported again from the exact image without starting the
export container. The provider ZIP matches its embedded Terraform lock before
expansion. Their SHA-256 values match the earlier function-table review:

| Executable | SHA-256 | Embedded compiler |
| --- | --- | --- |
| Terraform 1.17.1 | `1eaecd79a92a0114d70017b5d5e274c12e5b4561d971ff0b5e24073cddd287dc` | go1.26.10 |
| AWS provider 6.71.0 | `287b8b0f7c29980010149dc577389bfafbcd9b4f5b65cf2c8cad32ec98f008ea` | go1.27.3 |

The official [govulncheck v1.9.0](https://pkg.go.dev/golang.org/x/vuln@v1.9.0/cmd/govulncheck)
was built with Go 1.27.1 from the pinned module, with checksum-database
verification enabled. Analysis used a local DB containing only the unmodified
official GO-2026-5932 record. This targeted check is not a current full Go scan.

Neither executable has an ELF `.symtab`; the tool's extract mode returns module
metadata and zero package symbols. Its [binary analysis implementation](https://go.googlesource.com/vuln/+/refs/tags/v1.9.0/internal/vulncheck/binary.go)
then falls back to all vulnerable packages/symbols known for the module. The
result contains seven wildcard symbol frames for each executable. Those frames
are generated from the advisory; they do not demonstrate linked OpenPGP code.
The documented fallback also prevents a negative applicability conclusion.

A local positive control calls `openpgp.ReadArmoredKeyRing` with the same
x/crypto version. Its ordinary build yields 4,349 extracted symbols, including
93 OpenPGP symbols, and concrete vulnerable-function findings. Building the
same source with `-s -w` yields zero extracted symbols and the same seven
wildcard frames as the release artifacts. The fixtures were analyzed without
execution. This confirms the extraction limitation, not the candidates' safety.

The previous `.gopclntab` review found no OpenPGP function entries, but did not
cover all inline metadata. Keep both raw Unknown findings open until matching
build inputs with the actual release configuration, usable publisher symbol
artifacts, or a validated stripped-binary inline decoder establishes package
applicability. A compiler update or wildcard finding alone cannot settle it.

For a repeat targeted review, export the exact artifacts, obtain and checksum
the pinned tool source and official advisory, then use a local advisory directory:

```bash
govulncheck -mode=extract /path/to/executable > extracted.json
govulncheck -mode=binary -json -db=file:///absolute/path/to/advisory-directory \
  /path/to/executable > findings.json
```

Check extraction coverage and fallback behavior before interpreting findings.
JSON mode exits zero even when findings exist, as the tool's documentation
explains. It is not a release gate by exit status alone.

## Follow-up

Review removal of unused mount/namespace tools, ncurses utilities and their
dependencies, or wait for applicable stable-branch fixes. Any changed image
requires a clean-source build, the existing isolated acceptance checks, a full
current-DB scan and a new reviewed source/image binding. Do not mix unstable
Debian packages into this candidate or retarget an old binding.

The image was not rebuilt, pushed or deployed for this review. Scheduler stays
disabled. Live shared cleanup, Fargate/IAM acceptance, state-lock concurrency,
natural expiry and control-resource retirement retain their existing prerequisites.
