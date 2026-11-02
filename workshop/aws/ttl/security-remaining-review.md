# Remaining Go and OS finding review

Reviewed on 2026-11-02 for runtime source
`744734d16cf7f9b8adfdc62a470c56fe5ffb66df` and image
`sha256:a9c98f12308e3081b16f20ca56f8082d2a84df18bc1a905199b7a0d67a7dae44`.
Both requested review areas are covered: the two Go occurrences and every
one of the 27 Medium/eight Low OS occurrences in the [full scan](security-minimization.md).

The Go advisory is assessed **not affected for these executable hashes**:
the vulnerable package's concrete and inline function names are absent from
the complete bounded runtime name table. For the OS findings, 14 Medium and
two Low occurrences have absent components, architecture or image-specific
prerequisites. **13 Medium and six Low occurrences remain open.** Their library
code or vendor dispute requires further resolution.

The raw scan still contains all 37 occurrences and 31 advisory IDs. No ignore
file, severity downgrade or automatic release exception was added. Deployment
review remains open. The image, package versions and runtime code are unchanged;
this review is not a new scan or cloud acceptance.

## Go: complete runtime names, including inlining

The earlier review decoded concrete functions and found that govulncheck could
not recover inline symbols from the stripped executables. This review uses a
different source-checked method: the Go linker's `generateFuncnametab` calls
`walkFuncs` over both ordinary functions and inline-tree functions. Their names
are stored as null-terminated entries in the runtime name table even when the
ELF symbol table is stripped. This was checked in the exact embedded compiler
versions: [Go 1.26.10 linker](https://raw.githubusercontent.com/golang/go/go1.26.10/src/cmd/link/internal/ld/pcln.go)
and [Go 1.27.3 linker](https://raw.githubusercontent.com/golang/go/go1.27.3/src/cmd/link/internal/ld/pcln.go).

[inspect_go_names.go](../../../tools/inspect_go_names.go) reads only the
`pcHeader.funcnameOffset` through `pcHeader.cuOffset` region. It validates the
header, offsets, encoding and concrete-function count, then independently
checks that every function decoded by `debug/gosym` occurs in that bounded
region. It records the executable and metadata hashes and requires an expected
artifact SHA-256. It never executes the inspected binary. The supported scope
is standard static Linux amd64 Go 1.26/1.27 executables; other build settings or
unreviewed layouts fail rather than returning an empty result.

| Executable | Concrete function entries | Unique runtime names | Names without a concrete entry | x/crypto names | OpenPGP names |
| --- | ---: | ---: | ---: | ---: | ---: |
| Terraform 1.17.1 / Go 1.26.10 | 161,306 | 175,670 | 14,396 | 921 | 0 |
| AWS provider 6.71.0 / Go 1.27.3 | 853,291 | 935,323 | 83,416 | 366 | 0 |

Concrete entries can share names; “Names without a concrete entry” counts
distinct names.
The full region covers names used by inlined functions, rather than just
functions with their own code entry. There is no match for any
`golang.org/x/crypto/openpgp` package path, including the subpackages covered
by [GO-2026-5932](https://vuln.go.dev/ID/GO-2026-5932.json).

The executable hashes remain:

- Terraform: `1eaecd79a92a0114d70017b5d5e274c12e5b4561d971ff0b5e24073cddd287dc`.
- AWS provider: `287b8b0f7c29980010149dc577389bfafbcd9b4f5b65cf2c8cad32ec98f008ea`.

Validation includes an OpenPGP-positive executable built with x/crypto v0.58.0
and `-s -w`: the reader finds 98 OpenPGP names, 12 without a concrete function
entry. A separate tiny function is fully inlined into its caller: the reader
finds it both before and after stripping while the concrete function table
does not. Hash mismatch and damaged metadata fail closed. These controls
address the earlier inline-coverage gap.

This is an artifact-specific package-code assessment, not a call graph or a
general claim that x/crypto v0.58.0 is safe. A new executable, compiler layout,
renaming/packing scheme or dynamic/shared build requires a new assessment.
Module-based scanner findings remain in the raw report.

Repeat with a locally built reader, without a vulnerability DB or network access:

```bash
go test tools/inspect_go_names.go tools/inspect_go_names_test.go
go build -o /tmp/inspect-go-names tools/inspect_go_names.go
/tmp/inspect-go-names -binary /path/to/executable -sha256 REVIEWED_ARTIFACT_SHA256
```

## OS evidence and assessment meanings

Read-only inspection of the exact image confirmed no libstdc++ runtime/headers,
bzip2recover, nscd or ldd. The two installed GCC-source payloads contain base
metadata and libgcc support code; they do not supply the affected C++ library
and template implementations. No set-id regular file or file capability was
found in `/usr`, `/opt`, `/etc` or `/work`. Inspected libc and related ELFs are
EM_X86_64, which excludes the Power8-specific implementation.

The retained libc, gconv, libresolv and zlib interfaces were checked through
actual files and exported symbols. Deprecated DNS-printer implementation names
in this ABI include `ns_sprintrrf`, `ns_sprintrr` and `__fp_nquery`; their presence
was verified rather than inferred from advisory spelling. The current message
locale is C, with no extra locale files, LANGUAGE or LOCPATH setting. That
observation does not certify the future task environment or network resolver.

“Component absent” and “Architecture absent” apply to this exact image.
“Prerequisite absent” records the absence of the set-id execution path required
by two loader advisories. It does not declare the retained loader fixed;
adding executables, capabilities or a different security context requires
re-review. Non-root/read-only settings alone were not used to dismiss other
library findings.

“Open” retains the issue for further work. Vendor comments that a report is
minor, disputed or outside its security policy do not remove it from the raw
scan. Each row below links the vendor record used for that advisory.

| Advisory | Raw occurrences | Assessment and evidence |
| --- | ---: | --- |
| [CVE-2010-4756](https://security-tracker.debian.org/tracker/CVE-2010-4756) | 1 Low | **Open.** glob is exported. Debian describes resource limits as the caller responsibility under POSIX semantics; hostile pattern handling is not certified. |
| [CVE-2018-20796](https://security-tracker.debian.org/tracker/CVE-2018-20796) | 1 Low | **Open.** regexec is exported. Debian records upstream security-exception treatment; the implementation remains present. |
| [CVE-2019-1010022](https://security-tracker.debian.org/tracker/CVE-2019-1010022) | 1 Low | **Open.** The retained libc includes threading/stack-guard behavior. Upstream treats the report as non-security; no scanner suppression or patch is claimed. |
| [CVE-2019-1010023](https://security-tracker.debian.org/tracker/CVE-2019-1010023) | 1 Low | **Component absent.** ldd is absent. The advisory requires running that tool on an untrusted executable; upstream also disputes security classification. |
| [CVE-2019-1010024](https://security-tracker.debian.org/tracker/CVE-2019-1010024) | 1 Low | **Open.** Thread-stack/heap reuse behavior remains. Upstream disputes the ASLR-bypass classification; this is vendor context, not an automatic exemption. |
| [CVE-2019-1010025](https://security-tracker.debian.org/tracker/CVE-2019-1010025) | 1 Low | **Open.** Thread/heap address behavior remains. Upstream says ASLR disclosure alone is not a vulnerability; the raw finding is preserved. |
| [CVE-2019-9192](https://security-tracker.debian.org/tracker/CVE-2019-9192) | 1 Low | **Open.** regexec is exported. The maintainer disputes vulnerability classification for the crafted-pattern case; raw finding and code remain. |
| [CVE-2026-102010](https://security-tracker.debian.org/tracker/CVE-2026-102010) | 2 Medium | **Component absent.** libstdc++ binary-heap erase_if code/headers are absent. Only GCC base metadata and libgcc support/unwind payloads remain; no libstdc++ runtime or headers. |
| [CVE-2026-114690](https://security-tracker.debian.org/tracker/CVE-2026-114690) | 2 Medium | **Component absent.** libstdc++ parallel search code/headers are absent. Only GCC base metadata and libgcc support/unwind payloads remain; no libstdc++ runtime or headers. |
| [CVE-2026-114691](https://security-tracker.debian.org/tracker/CVE-2026-114691) | 2 Medium | **Component absent.** libstdc++ vector<bool> code/headers are absent. Only GCC base metadata and libgcc support/unwind payloads remain; no libstdc++ runtime or headers. |
| [CVE-2026-115455](https://security-tracker.debian.org/tracker/CVE-2026-115455) | 2 Medium | **Component absent.** libstdc++ wide-character bitset extraction code/headers are absent. Only GCC base metadata and libgcc support/unwind payloads remain; no libstdc++ runtime or headers. |
| [CVE-2026-18374](https://security-tracker.debian.org/tracker/CVE-2026-18374) | 1 Medium | **Open.** fopen remains exported. A caller-controlled ccs mode is required; no such intended caller was found, but the libc code is present and complete caller exclusion is unproved. |
| [CVE-2026-19499](https://security-tracker.debian.org/tracker/CVE-2026-19499) | 1 Medium | **Open.** strfmon/strfmon_l remain exported. Susceptible format/width handling has not been excluded across native callers. |
| [CVE-2026-19542](https://security-tracker.debian.org/tracker/CVE-2026-19542) | 1 Medium | **Open.** tdelete remains exported. A sufficiently deep search tree is required; the implementation was not removed or patched. |
| [CVE-2026-27171](https://security-tracker.debian.org/tracker/CVE-2026-27171) | 1 Medium | **Open.** crc32_combine64 and crc32_combine_gen64 are exported by the retained zlib. No complete exclusion of native callers was established. |
| [CVE-2026-42250](https://security-tracker.debian.org/tracker/CVE-2026-42250) | 1 Medium | **Component absent.** The affected bzip2recover executable is absent. The retained libbz2 decompression library is a different component. |
| [CVE-2026-5435](https://security-tracker.debian.org/tracker/CVE-2026-5435) | 1 Medium | **Open.** libresolv retains the deprecated DNS-printing implementation (actual exports/aliases ns_sprintrrf, ns_sprintrr and __fp_nquery). No intended printer caller is identified; code remains present. |
| [CVE-2026-6238](https://security-tracker.debian.org/tracker/CVE-2026-6238) | 1 Medium | **Open.** The same DNS-printing interfaces remain exported. The advisory distinguishes them from ordinary resolver operation; that distinction does not remove the retained interfaces. |
| [CVE-2026-6368](https://security-tracker.debian.org/tracker/CVE-2026-6368) | 1 Medium | **Open.** wordexp remains exported. The WRDE_APPEND/wordfree path is not excluded by the absence of a shell. |
| [CVE-2026-6791](https://security-tracker.debian.org/tracker/CVE-2026-6791) | 1 Medium | **Open.** wordexp remains exported. Long tilde expansion is a separate libc path; shell removal does not establish absence. |
| [CVE-2026-77117](https://security-tracker.debian.org/tracker/CVE-2026-77117) | 1 Medium | **Open.** The SHIFT_JISX0213 gconv module is retained. Its conversion-state implementation is present even though Python codecs need not use it. |
| [CVE-2026-80489](https://security-tracker.debian.org/tracker/CVE-2026-80489) | 1 Medium | **Open.** The EUC-JISX0213 gconv module is retained. The affected conversion implementation is present. |
| [CVE-2026-84243](https://security-tracker.debian.org/tracker/CVE-2026-84243) | 1 Medium | **Open.** gettext is exported. Current LC_MESSAGES is C with no extra locale files or LANGUAGE/LOCPATH; future task environment and input boundaries have not been certified. |
| [CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091) | 1 Medium | **Open.** Runtime zlib reports 1.3.1 while the description names 1.3.1.2-1.3.2. Debian still lists the installed source as vulnerable and upstream discussion questions earlier writer paths; do not close by version-string comparison. |
| [CVE-2026-8674](https://security-tracker.debian.org/tracker/CVE-2026-8674) | 1 Medium | **Open.** Python can use libc name resolution. The deployed resolv.conf/LOCALDOMAIN search-list boundary remains a live-network prerequisite, not established by localhost testing. |
| [CVE-2026-86805](https://security-tracker.debian.org/tracker/CVE-2026-86805) | 1 Medium | **Prerequisite absent.** Requires a set-id executable with susceptible $ORIGIN RPATH and privileged loader context. No set-id regular files or file capabilities exist in this image; security-context/filesystem changes require re-review. |
| [CVE-2026-89092](https://security-tracker.debian.org/tracker/CVE-2026-89092) | 1 Medium | **Component absent.** The nscd daemon/package is absent. Resolver client libraries do not supply the affected daemon worker. |
| [CVE-2026-95619](https://security-tracker.debian.org/tracker/CVE-2026-95619) | 2 Medium | **Component absent.** libstdc++ aligned allocation code/headers are absent. Only GCC base metadata and libgcc support/unwind payloads remain; no libstdc++ runtime or headers. |
| [CVE-2026-95818](https://security-tracker.debian.org/tracker/CVE-2026-95818) | 1 Medium | **Prerequisite absent.** Requires AT_SECURE execution of a set-id executable with susceptible $ORIGIN RPATH/RUNPATH. No set-id regular files or file capabilities exist; this is an image-scoped prerequisite finding. |
| [CVE-2026-97399](https://security-tracker.debian.org/tracker/CVE-2026-97399) | 1 Low | **Architecture absent.** The flaw concerns Power8 strncasecmp. The image and inspected libc ELF are x86-64 (EM_X86_64), not Power8. |

## Remaining work

The open 13 Medium occurrences concern retained libc APIs/converters, resolver
or locale/environment boundaries, and zlib. The six Low occurrences concern
retained glob/regex or threading/address behavior with upstream policy context.
No complete native-caller exclusion or applicable stable-package correction is
claimed for them.

In particular, do not dismiss CVE-2026-85091 from the runtime's `zlibVersion()`
value of 1.3.1 alone. Debian marks the installed source affected, while the
description names a later range and the [upstream discussion](https://github.com/madler/zlib/issues/1310)
also questions older writer paths. That classification conflict remains open.

Future remediation should use an applicable stable fix or a separately reviewed
native-library change, preserving package provenance and the Python/provider
contract. Any changed runtime requires a clean build, isolated acceptance,
current full scan and new source/image binding. This review performs none of
the live shared-cleanup, Fargate/IAM, natural-TTL, concurrency or control-resource
retirement checks.
