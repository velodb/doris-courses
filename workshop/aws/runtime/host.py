"""Target-host provisioning helpers. Only the install command mutates the host."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

PAYLOAD = {"install_host.sh", "host.py", "kernel.py", "check.py", "serve.py",
           "doris-workshop-jupyter.service", "packages.json", "source.tar"}
COURSES = {
    "doris-rta": ("doris-course/01-real-time-analytics", "doris_course"),
    "doris-dw": ("doris-course/02-data-warehousing", "dw_course"),
    "doris-obs": ("doris-course/03-observability", "doris_course"),
    "doris-ai": ("doris-course/04-ai-integrations", "doris_course"),
}
DOCKER_PACKAGES = {"docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"}


class RuntimeErrorDetail(ValueError):
    pass


class Layout:
    """Production paths; tests can place the same layout under a temporary root."""

    def __init__(self, root=Path("/")):
        root = Path(root)
        self.source = root / "opt/doris-courses"
        self.server = root / "opt/doris-workshop/server-venv"
        self.helpers = root / "usr/local/lib/doris-workshop"
        self.kernels = root / "usr/local/share/doris-workshop/jupyter/kernels"
        self.state = root / "var/lib/doris-workshop"
        self.workspace = self.state / "workspace"
        self.home = self.state / "home"
        self.config = root / "etc/doris-workshop"
        self.units = root / "etc/systemd/system"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def relative_path(value):
    if not isinstance(value, str) or value != value.strip():
        raise RuntimeErrorDetail("bundle: invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeErrorDetail("bundle: unsafe relative path")
    return path


def verify_bundle(bundle):
    bundle = Path(bundle)
    plan = json.loads((bundle / "runtime.json").read_text())
    if not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan.get("schema_version") != 1 or plan.get("kind") != "doris-workshop-runtime":
        raise RuntimeErrorDetail("bundle: unsupported runtime format")
    if not re.fullmatch(r"[0-9a-f]{40}", plan.get("course_ref", "")):
        raise RuntimeErrorDetail("bundle: invalid course commit")
    if plan.get("runtime") != {"os": "linux", "architecture": "x86_64", "python_version": "3.12", "repository_root": "/opt/doris-courses"}:
        raise RuntimeErrorDetail("bundle: incompatible runtime")
    if not isinstance(plan.get("sha256"), dict) or set(plan["sha256"]) != PAYLOAD:
        raise RuntimeErrorDetail("bundle: incomplete payload hashes")
    for name, checksum in plan["sha256"].items():
        if (bundle / name).is_symlink() or digest(bundle / name) != checksum:
            raise RuntimeErrorDetail("bundle: payload checksum mismatch")
    courses = plan.get("courses")
    if not isinstance(courses, list) or not courses:
        raise RuntimeErrorDetail("bundle: no selected courses")
    seen = set()
    for course in courses:
        if not isinstance(course, dict):
            raise RuntimeErrorDetail("bundle: invalid course record")
        kernel = course.get("kernel")
        if kernel not in COURSES or kernel in seen:
            raise RuntimeErrorDetail("bundle: invalid or duplicate kernel")
        seen.add(kernel)
        if (course.get("course_dir"), course.get("package")) != COURSES[kernel]:
            raise RuntimeErrorDetail("bundle: course/kernel mismatch")
        if type(course.get("extensions")) is not bool:
            raise RuntimeErrorDetail("bundle: invalid extensions flag")
        if not isinstance(course.get("notebooks"), list) or not course["notebooks"]:
            raise RuntimeErrorDetail("bundle: course has no selected notebooks")
        for path in course["notebooks"]:
            if relative_path(path).suffix != ".ipynb":
                raise RuntimeErrorDetail("bundle: expected a Notebook path")
    packages = json.loads((bundle / "packages.json").read_text())
    if set(packages.get("docker_apt", {})) != DOCKER_PACKAGES:
        raise RuntimeErrorDetail("bundle: expected all five Docker packages")
    if any(not re.fullmatch(r"[0-9A-Za-z.+:~_-]+", version) for version in packages["docker_apt"].values()):
        raise RuntimeErrorDetail("bundle: invalid package version")
    if packages.get("server_pip") != ["jupyterlab==4.4.10", "ipykernel==6.29.5"]:
        raise RuntimeErrorDetail("bundle: unexpected Jupyter dependencies")
    return plan, packages


def archive_files(archive):
    """Git exports should contain only directories and regular files."""
    with tarfile.open(archive) as stream:
        names = []
        for member in stream.getmembers():
            relative_path(member.name)
            if not (member.isdir() or member.isfile()):
                raise RuntimeErrorDetail("source: archive links and special files are not allowed")
            if member.isfile():
                names.append(member.name)
        return names


def install_source(bundle, plan, layout):
    files = archive_files(Path(bundle) / "source.tar")
    expected = {"course_ref": plan["course_ref"], "archive_sha256": plan["sha256"]["source.tar"]}
    marker = layout.source / ".doris-workshop-source.json"
    if layout.source.exists():
        if layout.source.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise RuntimeErrorDetail("source: existing checkout is unmanaged or has another revision; use a fresh instance")
        return files
    layout.source.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".doris-source-", dir=layout.source.parent))
    try:
        with tarfile.open(Path(bundle) / "source.tar") as stream:
            stream.extractall(temporary, filter="data")
        atomic_json(temporary / marker.name, expected, 0o644)
        os.rename(temporary, layout.source)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return files


def materialize(plan, files, layout):
    """Preserve source Notebooks and any existing learner working copies."""
    for directory in (layout.kernels.parent.parent, layout.kernels.parent, layout.kernels):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
    selected = {
        course["course_dir"] + "/" + path: course
        for course in plan["courses"] for path in course["notebooks"]
    }
    if not set(selected).issubset(files):
        raise RuntimeErrorDetail("source: a selected Notebook is missing from the archive")
    for name in files:
        relative_path(name)
        source = layout.source / name
        target = layout.workspace / name
        if target.exists():
            continue
        if name.endswith(".ipynb"):
            if name not in selected:
                continue
            notebook = json.loads(source.read_text())
            notebook.setdefault("metadata", {})["kernelspec"] = {
                "name": selected[name]["kernel"], "display_name": selected[name]["kernel"], "language": "python",
            }
            for cell in notebook.get("cells", []):
                if cell.get("cell_type") == "code":
                    cell["outputs"] = []
                    cell["execution_count"] = None
            atomic_json(target, notebook, 0o600)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for course in plan["courses"]:
        root = layout.source / course["course_dir"]
        spec_dir = layout.kernels / course["kernel"]
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_dir.chmod(0o755)
        spec = {
            "argv": [str(root / ".venv/bin/python"), "-I", str(layout.helpers / "kernel.py"),
                     "--course-dir", str(root), "-f", "{connection_file}"],
            "display_name": course["kernel"], "language": "python",
            "env": {"PYTHONNOUSERSITE": "1", "VIRTUAL_ENV": str(root / ".venv"),
                    "PATH": str(root / ".venv/bin") + ":/usr/local/bin:/usr/bin:/bin"},
        }
        atomic_json(spec_dir / "kernel.json", spec, 0o644)


def learner_user(layout):
    user = pwd.getpwnam("workshop")
    if user.pw_uid == 0 or Path(user.pw_dir) != layout.home or user.pw_shell != "/bin/bash":
        raise RuntimeErrorDetail("user: workshop must be non-root with the managed home and Bash shell")
    return user


def chown_tree(path, user):
    for directory, directories, files in os.walk(path):
        os.chown(directory, user.pw_uid, user.pw_gid)
        for name in directories + files:
            os.chown(Path(directory) / name, user.pw_uid, user.pw_gid, follow_symlinks=False)


def as_learner(command, layout, *, cwd=None, capture=False):
    # Never inherit deployer credentials, pip indexes, Python paths or login state.
    result = subprocess.run(
        ["runuser", "--user", "workshop", "--", "env", "-i",
         f"HOME={layout.home}", "PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8",
         "PYTHONNOUSERSITE=1", "PIP_CONFIG_FILE=/dev/null", *map(str, command)],
        cwd=cwd, check=True, timeout=1200, capture_output=capture, text=True,
    )
    return result


def require_target():
    if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeErrorDetail("install: requires root on Ubuntu 24.04 x86_64")
    release = platform.freedesktop_os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04" or sys.version_info[:2] != (3, 12):
        raise RuntimeErrorDetail("install: Ubuntu 24.04 and Python 3.12 are required")


def install(bundle):
    require_target()
    plan, packages = verify_bundle(bundle)
    layout = Layout()
    user = learner_user(layout)
    layout.state.mkdir(parents=True, exist_ok=True)
    fingerprint = digest(Path(bundle) / "runtime.json")
    receipt_path = layout.state / "install.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {"bundle_sha256": fingerprint, "environments": []}
    if receipt["bundle_sha256"] != fingerprint:
        raise RuntimeErrorDetail("install: runtime bundle differs; rebuild on a fresh instance")
    files = install_source(bundle, plan, layout)
    chown_tree(layout.source, user)
    user_config = layout.home / "jupyter-config"
    user_config.mkdir(parents=True, exist_ok=True)
    user_config.chmod(0o700)
    os.chown(user_config, user.pw_uid, user.pw_gid)
    layout.server.parent.mkdir(parents=True, exist_ok=True)
    os.chown(layout.server.parent, user.pw_uid, user.pw_gid)
    for name, directory, requirements, cwd in [
        ("server", layout.server, packages["server_pip"], layout.source),
        *((course["kernel"], layout.source / course["course_dir"] / ".venv",
           ["-r", "requirements.txt", "-e", ".[extensions]" if course["extensions"] else "."],
           layout.source / course["course_dir"]) for course in plan["courses"]),
    ]:
        if name in receipt["environments"] and (directory / "bin/python").is_file():
            continue
        as_learner(["/usr/bin/python3.12", "-m", "venv", directory], layout)
        as_learner([directory / "bin/python", "-m", "pip", "install", "--disable-pip-version-check",
                    "--index-url", "https://pypi.org/simple", *requirements], layout, cwd=cwd)
        as_learner([directory / "bin/python", "-m", "pip", "check"], layout, cwd=cwd)
        frozen = as_learner([directory / "bin/python", "-m", "pip", "freeze"], layout, cwd=cwd, capture=True)
        freeze_path = layout.state / "installed-packages" / (name + ".txt")
        freeze_path.parent.mkdir(parents=True, exist_ok=True)
        freeze_path.write_text(frozen.stdout)
        receipt["environments"] = sorted(set(receipt["environments"]) | {name})
        atomic_json(receipt_path, receipt)
    layout.helpers.mkdir(parents=True, exist_ok=True)
    layout.helpers.chmod(0o755)
    for name in ("host.py", "kernel.py", "check.py", "serve.py"):
        shutil.copyfile(Path(bundle) / name, layout.helpers / name)
        (layout.helpers / name).chmod(0o644)
    materialize(plan, files, layout)
    chown_tree(layout.workspace, user)
    layout.config.mkdir(parents=True, exist_ok=True)
    layout.config.chmod(0o755)
    atomic_json(layout.config / "runtime.json", plan, 0o644)
    shutil.copyfile(Path(bundle) / "doris-workshop-jupyter.service", layout.units / "doris-workshop-jupyter.service")
    (layout.units / "doris-workshop-jupyter.service").chmod(0o644)
    installed = subprocess.run(["dpkg-query", "-W", "-f=${Package}=${Version}\n", *sorted(DOCKER_PACKAGES)],
                               capture_output=True, text=True, check=True, timeout=30)
    (layout.state / "installed-packages/docker.txt").write_text(installed.stdout)
    print("Runtime files installed; service and kernel readiness checks follow.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "packages", "install", "validate-user"))
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-user":
            learner_user(Layout())
        elif args.bundle is None:
            raise RuntimeErrorDetail("bundle: --bundle is required")
        elif args.command == "install":
            install(args.bundle)
        else:
            _, packages = verify_bundle(args.bundle)
            if args.command == "packages":
                for name, version in sorted(packages["docker_apt"].items()):
                    print(name + "=" + version)
            else:
                archive_files(args.bundle / "source.tar")
                print("Runtime bundle checks passed.")
        return 0
    except (RuntimeErrorDetail, OSError, ValueError, KeyError, TypeError, tarfile.TarError,
            subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, RuntimeErrorDetail) else "runtime: operation failed; see the bootstrap stage log", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
