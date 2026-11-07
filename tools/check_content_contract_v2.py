#!/usr/bin/env python3
"""Validate a resolved v2 package against this checkout's pinned contract."""
import argparse
import json
from pathlib import Path

from content_contract_v2 import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="frozen content tree, never a URL")
    parser.add_argument("--entry", default="package.v2.json", help="root-relative package manifest")
    parser.add_argument("--allow-fixture", action="store_true", help="tests only: admit synthetic contract fixtures")
    args = parser.parse_args()
    report, problems = validate(args.root, args.entry, allow_fixture=args.allow_fixture)
    for problem in problems:
        print(f"PROBLEM {problem}")
    if report:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print(f"Content contract v2: {len(problems)} problem(s)")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
