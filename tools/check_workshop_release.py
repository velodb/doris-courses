#!/usr/bin/env python3
"""Check workshop support claims and public checkout boundaries, offline."""
import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def validate(matrix, profiles, *, root=ROOT, tracked=None):
    problems = []
    if matrix.get("schema_version") != 1 or set(matrix.get("profiles", {})) != set(profiles["profiles"]):
        return ["support matrix must cover every declared profile exactly once"]
    if matrix.get("runtime") != profiles["runtime"]:
        problems.append("support matrix runtime differs from the profile contract")
    for name, row in matrix["profiles"].items():
        expected = profiles["profiles"][name]
        if row.get("course_dir") != expected["course_dir"] or row.get("kernel") != expected["kernel"]:
            problems.append(f"{name}: course or kernel differs from the profile contract")
        if row.get("ec2_course") not in {"verified", "unverified"} or row.get("shared_browser") not in {"verified", "pending"}:
            problems.append(f"{name}: unsupported validation state")
        if not isinstance(row.get("limitations"), str) or not row["limitations"].strip():
            problems.append(f"{name}: describe the unverified scope")
        proof = row.get("evidence")
        if row.get("ec2_course") == "verified":
            if not isinstance(proof, dict) or not re.fullmatch(r"[0-9a-f]{40}", proof.get("course_sha", "")):
                problems.append(f"{name}: verified course needs pinned evidence")
            elif proof.get("transport") not in {"operator-ssm-forward", "shared-https"} or type(proof.get("hosts")) is not int or proof["hosts"] < 1:
                problems.append(f"{name}: verified course needs an explicit transport and tested host count")
            elif row["shared_browser"] == "verified" and proof["transport"] != "shared-https":
                problems.append(f"{name}: SSM evidence cannot certify shared browser access")
        elif proof is not None or row.get("shared_browser") == "verified":
            problems.append(f"{name}: unverified profile cannot claim integration evidence")
        if expected["requires_browser_host"] and row.get("browser_host") != "pending":
            problems.append(f"{name}: the required browser Host still needs acceptance")
        summary = row.get("summary", "")
        relative = PurePosixPath("workshop/aws") / summary.split("#", 1)[0]
        if not summary or ":" in summary or relative.is_absolute() or ".." in relative.parts or "plan-doc" in relative.parts or not (root / relative).is_file():
            problems.append(f"{name}: use an existing public validation summary")
        elif tracked is not None and relative.as_posix() not in tracked:
            problems.append(f"{name}: validation summary is not tracked")
    return problems


def public_boundaries(root, tracked):
    problems = [f"{name}: private operator files must not be tracked" for name in sorted(tracked)
        if set(PurePosixPath(name).parts) & {"plan-doc", ".local", ".venv"}]
    for relative in sorted(tracked):
        path = root / relative
        if relative.startswith("workshop/aws/") and path.suffix == ".md":
            for raw in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                link = urlsplit(raw.strip("<>"))
                if link.scheme or link.netloc or not link.path:
                    continue
                target = (path.parent / unquote(link.path)).resolve()
                inside = target.is_relative_to(root.resolve())
                name = target.relative_to(root.resolve()).as_posix() if inside else ""
                public = name in tracked or target.is_dir() and any(item.startswith(name + "/") for item in tracked)
                if not inside or not public:
                    problems.append(f"{relative}: public documentation links outside tracked source")
        if relative.startswith("doris-course/") and path.suffix == ".py":
            tree = ast.parse(path.read_text(), filename=relative)
            for node in ast.walk(tree):
                names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                if any(name.split(".")[0] in {"workshop_config", "workshop_preflight", "workshop_ttl", "workshop_ttl_runner"} for name in names):
                    problems.append(f"{relative}: local courses import an AWS operator tool")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        tracked = set(subprocess.check_output(["git", "-C", str(ROOT), "ls-files"], text=True).splitlines())
        matrix = json.loads((ROOT / "workshop/aws/support-matrix.json").read_text())
        profiles = json.loads((ROOT / "workshop/aws/profiles.json").read_text())
        problems = validate(matrix, profiles, tracked=tracked) + public_boundaries(ROOT, tracked)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        problems = ["cannot read the tracked workshop release contract"]
    for problem in problems:
        print(problem)
    print(f"Workshop release: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
