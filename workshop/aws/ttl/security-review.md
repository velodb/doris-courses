# Cleanup runner image scan

The runner built from source `593fb436295601a51b79b32658cbcf99c4b1f657`
was scanned on 2026-10-31. Its local image ID is
`sha256:abb613303f1456747be0c4b1c6c7cdf51c2bcc81b9193fe91133d0f0bf906466`.
**The scan is complete; deployment review remains open.** The image has not
been pushed or deployed, and Scheduler remains disabled.

## Coverage and results

Trivy 0.76.1 came from its immutable
[official release](https://github.com/aquasecurity/trivy/releases/tag/v0.76.1),
with the archive checked against release asset hashes and the checksums file.
The official DB snapshot was updated at `2026-10-31T13:09:00Z`. Analysis used
local files; telemetry, version checks and DB/dependency network updates were
disabled. No image or SBOM was uploaded to a scanning service.

Coverage: 105 Debian packages, 17 installed Python packages, 260 Terraform Go
module entries and 387 AWS-provider Go module entries. The provider ZIP was
verified against the image lock, expanded and scanned as an executable.

| Severity | Package finding occurrences |
| --- | ---: |
| Critical | 2 |
| High | 59 |
| Medium | 110 |
| Low | 106 |
| Unknown | 4 |

These 281 occurrences represent 126 unique advisory IDs. An advisory can
affect several packages or both binaries. Counts include unfixed issues and
source-package matches; they do not prove that every finding is reachable.
No ignore file, unfixed filter or automatic waiver was applied.

## Review findings

- **SQLite / CVE-2025-7458:** Debian lists the installed Bookworm version as
  vulnerable and the update as a minor/no-DSA issue. The advisory requires
  crafted arbitrary SQL. The fixed cleanup interface exposes no SQL input,
  but the installed-library finding remains open. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2025-7458)
- **zlib / CVE-2023-45853:** this affects MiniZip. Debian explicitly notes that
  Bookworm's zlib source does not build it and its binary packages are
  unaffected. Keep this vendor explanation alongside the raw finding.
  [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2023-45853)
- **OS High entries:** many occurrences share source-package advisories.
  CVE-2026-53613 concerns authorized non-root mounts and writable paths;
  CVE-2026-16742 concerns systemd-homed, while this image contains libraries
  from that source package. Capability dropping/read-only root and the absence
  of systemd-homed inform reachability review, but do not resolve every OS
  finding. Review a maintained patched base and the complete report before
  release. [util-linux tracker](https://security-tracker.debian.org/tracker/CVE-2026-53613),
  [systemd tracker](https://security-tracker.debian.org/tracker/CVE-2026-16742)
- **pip 25.0.1:** five Medium and one Low findings have fixes. Upgrade or
  remove the runtime installer in the next hardening change, then rebuild,
  validate and scan that exact image. The 16 locked runtime dependencies have
  no reported Python findings in this DB snapshot.
- **GO-2026-5932:** both binaries contain golang.org/x/crypto v0.58.0. The
  advisory concerns deprecated OpenPGP packages and has no fixed version.
  Module detection alone does not prove vulnerable-symbol reachability. Keep
  the Unknown finding open for package/symbol review; a compiler upgrade alone
  does not resolve it. [Go advisory](https://pkg.go.dev/vuln/GO-2026-5932)

The complete operator report retains the remaining OS findings. This document
grants no blanket exemption for an unfixed package, container isolation or a
source-package match. Rerun against the reviewed candidate and current DB
after changes; this snapshot is not permanent release approval.

## Repeat locally

Obtain and verify Trivy 0.76.1 and a current official Trivy DB. The helper does
not install a scanner or update a database:

```bash
.venv-workshop/bin/python tools/scan_workshop_image.py \
  --image doris-workshop-ttl:aws20-final \
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
coverage. The baseline above returns `2`. None of these exits certify IAM,
Fargate, natural TTL or final release acceptance.
