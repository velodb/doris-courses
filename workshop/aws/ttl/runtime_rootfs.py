"""Assemble an immutable runtime payload from the pinned Python build stage.

This is a build-only script. Package records describe retained payloads, not
an apt-managed installation with package configuration tools.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def select_packages(status, expected):
    selected = {}
    for block in status.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
        name = fields.get("Package")
        if name in expected:
            if fields.get("Status") != "install ok installed" or fields.get("Version") != expected[name] or name in selected:
                raise ValueError("runtime package differs from the pinned payload: " + name)
            selected[name] = (fields, block)
    if set(selected) != set(expected):
        raise ValueError("missing runtime package payload")
    return selected


def assemble(destination=Path("/rootfs")):
    spec = json.loads(Path("workshop/aws/ttl/runtime-packages.json").read_text())
    assert spec["schema_version"] == 1 and destination == Path("/rootfs") and not destination.exists()
    selected = select_packages(Path("/var/lib/dpkg/status").read_text(), spec["packages"])
    destination.mkdir()
    for name in ("usr/local", "opt", "work", "etc/ssl"):
        shutil.copytree("/" + name, destination / name, symlinks=True)
    for name in ("bin", "sbin", "lib", "lib64"):
        (destination / "usr" / name).mkdir(exist_ok=True)
        (destination / name).symlink_to("usr/" + name)

    copied, missing = {}, []
    def copy_file(source):
        source = Path(source)
        # Resolve parent aliases in the source filesystem, preserving leaf links.
        canonical = source.parent.resolve() / source.name
        target = destination / canonical.relative_to("/")
        if source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_symlink():
                target.symlink_to(os.readlink(source))
            assert os.readlink(target) == os.readlink(source)
            copied[str(canonical)] = {"symlink": os.readlink(source)}
        elif source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            with source.open("rb") as stream:
                copied[str(canonical)] = {"sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
        else:
            # Debian slim already omits some documentation listed by dpkg.
            missing.append(str(source))

    info = destination / "var/lib/dpkg/info"
    info.mkdir(parents=True)
    payloads = {}
    for name, (fields, block) in sorted(selected.items()):
        manifests = [p for p in Path("/var/lib/dpkg/info").glob(name + "*.list")
            if p.name == name + ".list" or p.name.startswith(name + ":")]
        assert len(manifests) == 1
        for filename in manifests[0].read_text().splitlines():
            assert filename.startswith("/") and ".." not in Path(filename).parts
            if filename not in {"/", "/."}:
                copy_file(filename)
        for suffix in (".list", ".md5sums"):
            record = manifests[0].with_suffix(suffix)
            if record.is_file():
                shutil.copy2(record, info / record.name)
        payloads[name] = {"version": fields["Version"], "source": fields.get("Source", name)}
    (destination / "var/lib/dpkg/status").write_text("\n\n".join(row[1] for _, row in sorted(selected.items())) + "\n\n")

    # Keep OS identity, resolver policy and pre-generated trust/configuration
    # data. Configuration helpers and their dependencies are not runtime inputs.
    metadata = ["/etc/os-release", "/usr/lib/os-release", "/etc/debian_version", "/etc/nsswitch.conf"]
    for source in metadata:
        copy_file(source)
    for root in (Path("/etc/ssl"),):
        for source in root.rglob("*"):
            if not source.is_dir() or source.is_symlink():
                copy_file(source)
    (destination / "etc/passwd").write_text("root:x:0:0:root:/root:/nonexistent\nrunner:x:10001:10001:runner:/work:/nonexistent\n")
    (destination / "etc/group").write_text("root:x:0:\nrunner:x:10001:\n")

    dependencies, excluded, optional = {}, {}, {}
    for source in sorted(Path("/usr/local").rglob("*")):
        if source.is_symlink() or not source.is_file():
            continue
        with source.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        extension = source.name.split(".", 1)[0]
        if source.parent.name == "lib-dynload" and extension in spec["excluded_optional_extensions"]:
            (destination / source.relative_to("/")).unlink()
            optional[extension] = {"source_path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "reason": "optional native extension outside the fixed cleanup runtime"}
            continue
        result = subprocess.run(["ldd", str(source)], capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        missing_libraries = sorted(re.findall(r"^\s*(\S+) => not found$", output, re.MULTILINE))
        if missing_libraries:
            # The pinned upstream slim image leaves an already unusable Tk GUI
            # extension. Do not quietly accept a newly broken native module.
            assert source.parent.name == "lib-dynload" and missing_libraries == spec["excluded_unusable_extensions"].get(extension), str(source)
            (destination / source.relative_to("/")).unlink()
            excluded[extension] = {"source_path": str(source), "missing_in_pinned_base": missing_libraries,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            continue
        if result.returncode:
            assert "not a dynamic executable" in output or "statically linked" in output, output
        libraries = sorted(set(re.findall(r"(?:=>\s+|^\s*)(/[^\s()]+)", output, re.MULTILINE)))
        for library in libraries:
            assert (destination / Path(library).resolve().relative_to("/")).is_file(), library
        dependencies[str(source)] = libraries
    assert set(excluded) == set(spec["excluded_unusable_extensions"])
    assert set(optional) == set(spec["excluded_optional_extensions"])
    for path in (destination / "work", destination / "work/tmp"):
        os.chown(path, 10001, 10001)
        path.chmod(0o700)
    manifest = {"schema_version": 1, "packages": payloads, "files": copied, "dynamic_dependencies": dependencies,
        "excluded_unusable_extensions": excluded,
        "excluded_optional_extensions": optional,
        "base_manifest_paths_already_absent": sorted(set(missing)), "metadata_files": metadata,
        "scope": "retained runtime payloads; package configuration dependencies and tools intentionally absent"}
    (destination / "opt/doris-workshop/native-runtime.json").write_text(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    assemble()
