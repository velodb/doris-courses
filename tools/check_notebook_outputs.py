#!/usr/bin/env python3
"""Report committed notebook outputs, error outputs, and local paths.

Usage (from the repository root):
    python3 tools/check_notebook_outputs.py [--strict] [PATH ...]

PATH defaults to doris-course/. Error outputs and local paths always fail
the check. Notebooks are committed without outputs; with --strict, any
remaining output also fails.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL_PATH = re.compile(
    r"(/Users/[^/\s\"']+|/home/[^/\s\"']+|/mnt/[^/\s\"']+|/private/var/folders/|/var/folders/"
    r"|[A-Za-z]:\\\\Users|~/[^/\s\"'\\]+)"
)


def display(path):
    try:
        return path.relative_to(REPO)
    except ValueError:
        return path


def iter_notebooks(root):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*.ipynb")):
        if ".ipynb_checkpoints" not in path.parts and ".venv" not in path.parts:
            yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--strict", action="store_true", help="fail when any output remains")
    args = parser.parse_args()
    roots = [Path(path).resolve() for path in args.paths] or [REPO / "doris-course"]

    failures = 0
    for root in roots:
        for path in iter_notebooks(root):
            notebook = json.loads(path.read_text(encoding="utf-8"))
            outputs = 0
            for index, cell in enumerate(notebook.get("cells", [])):
                for output in cell.get("outputs", []) or []:
                    outputs += 1
                    if output.get("output_type") == "error":
                        failures += 1
                        print(f"ERROR   {display(path)} cell {index}: {output.get('ename')}")
                    for local in sorted(set(LOCAL_PATH.findall(json.dumps(output, ensure_ascii=False)))):
                        failures += 1
                        print(f"PATH    {display(path)} cell {index}: {local}")
            if outputs:
                if args.strict:
                    failures += 1
                print(f"OUTPUT  {display(path)}: {outputs} output(s)")
    print(f"{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
