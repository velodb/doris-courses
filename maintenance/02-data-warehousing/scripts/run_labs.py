"""Execute the committed notebook code cells; never rewrite notebook outputs."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "doris-course/02-data-warehousing"
sys.path.insert(0, str(ROOT))
CORE = [
    "module01-introduction",
    "module02-architecture",
    "module03-table-design",
    "module05-ingestion",
    "module06-data-quality",
    "module07-state-changes",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iceberg", action="store_true", help="Also prepare the local lakehouse fixture and execute D04")
    parser.add_argument("--solutions", action="store_true", help="Validate folded reference solutions after each independent exercise")
    args = parser.parse_args()
    if os.environ.get("DW_ALLOW_WRITES") != "yes":
        parser.error("Set DW_ALLOW_WRITES=yes after reading environments/single-node/README.md")
    modules = list(CORE)
    if args.iceberg:
        modules.insert(3, "module04-external-access")
    for module in modules:
        paths = list((ROOT / "level1" / module).glob("lab*.ipynb"))
        if len(paths) != 1:
            raise RuntimeError(f"Expected exactly one lab in {module}")
        notebook = json.loads(paths[0].read_text())
        namespace = {"__name__": "__main__"}
        previous_directory = Path.cwd()
        print(f"RUN {module}", flush=True)
        try:
            os.chdir(paths[0].parent)
            for index, cell in enumerate(notebook["cells"]):
                if args.solutions and "course_solution" in cell.get("metadata", {}).get("tags", []):
                    source = "".join(cell["source"])
                    solutions = re.findall(r"```python\n(.*?)```", source, re.DOTALL)
                    if len(solutions) != 1:
                        raise ValueError("Expected one reference solution: " + cell["id"])
                    exec(compile(solutions[0], f"{paths[0].name}:solution-{index}", "exec"), namespace)
                    print("PASS reference solution: " + module, flush=True)
                if cell["cell_type"] == "code":
                    source = cell["source"]
                    source = "".join(source) if isinstance(source, list) else source
                    exec(compile(source, f"{paths[0].name}:cell-{index}", "exec"), namespace)
        finally:
            os.chdir(previous_directory)
            if "lab" in namespace and namespace["lab"].connection.open:
                namespace["lab"].close()
        print(f"PASS {module}", flush=True)
    print("PASS: selected notebook code cells. This does not validate the browser UI.")
    if not args.iceberg:
        print("NOT RUN: D04 Iceberg integration. See maintenance/02-data-warehousing/integration-backlog.md.")


if __name__ == "__main__":
    main()
