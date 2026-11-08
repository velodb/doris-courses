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
import workshop_ui

RUNTIME = config_tool.SPEC / "runtime"
PAYLOAD = ("install_host.sh", "host.py", "kernel.py", "check.py", "serve.py", "portal.py",
           "doris-workshop-jupyter.service", "packages.json", "operations.py", "ui.tar")


def course_selection(config, *, repo=config_tool.REPO):
    """Resolve only pinned teaching files, also for post-expiry evidence checks."""
    resolved, _ = config_tool.resolve_profiles(config, repo)
    courses = {}
    files = set(config_tool.git(repo, "ls-tree", "-r", "--name-only", config["course_ref"]).splitlines())
    for profile_id, profile in resolved.items():
        metadata = config_tool.yaml.load(config_tool.git(repo, "show", f"{config['course_ref']}:{profile['course_dir']}/course.yaml"), Loader=config_tool.StrictLoader)
        titles = {m["id"]: m.get("title", m["id"]) for m in metadata["modules"]}
        levels = {m["id"]: m.get("level") for m in metadata["modules"]}
        identities = {m["id"]: m for m in metadata["modules"]}
        course = courses.setdefault(profile["kernel"], {
            "course_dir": profile["course_dir"], "kernel": profile["kernel"],
            "package": "dw_course" if profile["kernel"] == "doris-dw" else "doris_course",
            "profiles": [], "notebooks": [], "modules": [], "optional": [], "extensions": False,
            "title": metadata.get("title", profile["course_dir"].rsplit("/", 1)[-1]),
            "levels": metadata.get("levels", {}),
            "slug": metadata.get("slug"), "status": metadata["status"],
        })
        course["profiles"].append(profile_id)
        course["extensions"] |= profile_id == "dw-extensions"
        for module in profile["modules"]:
            if not any(m["id"] == module["id"] for m in course["modules"]):
                identity = identities[module["id"]]
                version = hashlib.sha256()
                for kind in ("course", "lab", "quiz"):
                    version.update(config_tool.git(repo, "show", f"{config['course_ref']}:{profile['course_dir']}/{module[kind]}").encode())
                    version.update(b"\0")
                course["modules"].append({**module, "title": titles[module["id"]], "level": levels[module["id"]],
                    "required": identity.get("required", False), "status": identity["status"],
                    "content_version": version.hexdigest()})
            course["notebooks"].append(module["lab"])
            quiz = str(Path(module["quiz"]).with_suffix(".ipynb"))
            if profile["course_dir"] + "/" + quiz in files:
                course["notebooks"].append(quiz)
        course["notebooks"].extend(path for path in profile["optional_paths"] if path.endswith(".ipynb"))
        for module in metadata['modules']:
            for part in module.get('optional', []):
                if part['path'] in profile['optional_paths'] and part not in course['optional']:
                    course['optional'].append(part)
    for course in courses.values():
        course["notebooks"] = sorted(set(course["notebooks"]))
    return list(courses.values())


def create_bundle(config_path, output, *, repo=config_tool.REPO, content_package=None):
    config, _, runtime = config_tool.validate_config(config_tool.load_document(config_path), repo=repo)
    courses = course_selection(config, repo=repo)
    content = None
    if config['schema_version'] == 2:
        import workshop_content
        if content_package is None:
            raise config_tool.ConfigError('content: v2 requires --content-package with the complete admitted release')
        content = workshop_content.admit(content_package, config)
    elif content_package is not None:
        raise config_tool.ConfigError('content: v1 bundles cannot acquire multilingual capabilities')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise config_tool.ConfigError("output: already exists; choose a new bundle directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".runtime-bundle-", dir=output.parent))
    try:
        for name in PAYLOAD:
            if name == "ui.tar":
                (temporary / name).write_bytes(workshop_ui.archive(offline=True))
                continue
            source = config_tool.SPEC / "operations/node.py" if name == "operations.py" else RUNTIME / name
            shutil.copyfile(source, temporary / name)
        with (temporary / "source.tar").open("wb") as archive:
            result = subprocess.run(
                ["git", "-C", str(repo), "archive", "--format=tar", config["course_ref"]],
                stdout=archive, stderr=subprocess.PIPE,
            )
        if result.returncode:
            raise config_tool.ConfigError("course_ref: cannot export pinned source")
        plan = {
            "schema_version": config['schema_version'], "kind": "doris-workshop-runtime",
            "course_ref": config["course_ref"], "runtime": runtime,
            "courses": courses,
            "sha256": {},
            "learning_progress": "doris-course/common/doris_labkit/progress.py" in
                config_tool.git(repo, "ls-tree", "-r", "--name-only", config["course_ref"]).splitlines(),
        }
        payload = list(PAYLOAD) + ['source.tar']
        if content is not None:
            shutil.copyfile(Path(content_package) / 'teaching.tar.gz', temporary / 'teaching.tar.gz')
            shutil.copyfile(RUNTIME / 'multilingual.py', temporary / 'multilingual.py')
            plan.update(content=content, capabilities=workshop_content.consumer.CAPABILITIES)
            payload.extend(['teaching.tar.gz', 'multilingual.py'])
        for name in payload:
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
    parser.add_argument("--content-package", type=Path)
    args = parser.parse_args(argv)
    try:
        plan = create_bundle(args.config, args.output, content_package=args.content_package)
        print(f"Runtime bundle written for {len(plan['courses'])} courses; nothing installed or deployed.")
        return 0
    except (config_tool.ConfigError, OSError) as exc:
        print(str(exc) if isinstance(exc, config_tool.ConfigError) else "bundle: cannot write output", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
