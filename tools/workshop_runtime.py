#!/usr/bin/env python3
"""Build a credential-free runtime bundle from a locally available course commit."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import workshop_config as config_tool

RUNTIME = config_tool.SPEC / "runtime"
PAYLOAD = ("install_host.sh", "host.py", "kernel.py", "check.py", "serve.py",
           "doris-workshop-jupyter.service", "packages.json")


def create_bundle(config_path, output, *, repo=config_tool.REPO):
    config, resolved, runtime = config_tool.validate_config(config_tool.load_document(config_path), repo=repo)
    courses = {}
    files = set(config_tool.git(repo, "ls-tree", "-r", "--name-only", config["course_ref"]).splitlines())
    for profile_id, profile in resolved.items():
        course = courses.setdefault(profile["kernel"], {
            "course_dir": profile["course_dir"], "kernel": profile["kernel"],
            "package": "dw_course" if profile["kernel"] == "doris-dw" else "doris_course",
            "profiles": [], "notebooks": [], "extensions": False,
        })
        course["profiles"].append(profile_id)
        course["extensions"] |= profile_id == "dw-extensions"
        for module in profile["modules"]:
            course["notebooks"].append(module["lab"])
            quiz = str(Path(module["quiz"]).with_suffix(".ipynb"))
            if profile["course_dir"] + "/" + quiz in files:
                course["notebooks"].append(quiz)
        course["notebooks"].extend(path for path in profile["optional_paths"] if path.endswith(".ipynb"))
    for course in courses.values():
        course["notebooks"] = sorted(set(course["notebooks"]))
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise config_tool.ConfigError("output: already exists; choose a new bundle directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".runtime-bundle-", dir=output.parent))
    try:
        for name in PAYLOAD:
            shutil.copyfile(RUNTIME / name, temporary / name)
        with (temporary / "source.tar").open("wb") as archive:
            result = subprocess.run(
                ["git", "-C", str(repo), "archive", "--format=tar", config["course_ref"]],
                stdout=archive, stderr=subprocess.PIPE,
            )
        if result.returncode:
            raise config_tool.ConfigError("course_ref: cannot export pinned source")
        plan = {
            "schema_version": 1, "kind": "doris-workshop-runtime",
            "course_ref": config["course_ref"], "runtime": runtime,
            "courses": list(courses.values()),
            "sha256": {},
        }
        for name in (*PAYLOAD, "source.tar"):
            with (temporary / name).open("rb") as stream:
                plan["sha256"][name] = hashlib.file_digest(stream, "sha256").hexdigest()
        (temporary / "runtime.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        # The whole directory is private, even though the bundle contains no credentials.
        os.rename(temporary, output)
        return plan
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = create_bundle(args.config, args.output)
        print(f"Runtime bundle written for {len(plan['courses'])} courses; nothing installed or deployed.")
        return 0
    except (config_tool.ConfigError, OSError) as exc:
        print(str(exc) if isinstance(exc, config_tool.ConfigError) else "bundle: cannot write output", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
