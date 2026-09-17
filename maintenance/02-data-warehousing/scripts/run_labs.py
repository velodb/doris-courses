"""Execute the committed notebook code cells; never rewrite notebook outputs."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "doris-course/02-data-warehousing"
sys.path.insert(0, str(ROOT))
CORE = [
    "module01-introduction",
    "module02-architecture",
    "module03-table-design",
    "module05-ingestion",
    "module09a-data-quality",
    "module06-state-changes",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iceberg", action="store_true", help="Also execute D04 against a preconfigured Catalog")
    args = parser.parse_args()
    if os.environ.get("DW_ALLOW_WRITES") != "yes":
        parser.error("Set DW_ALLOW_WRITES=yes after reading environments/single-node/README.md")
    modules = list(CORE)
    if args.iceberg:
        if "DW_ICEBERG_ORDERS" not in os.environ:
            parser.error("--iceberg requires DW_ICEBERG_ORDERS")
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
