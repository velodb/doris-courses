"""Learner-facing checks, including expected errors and independent exercises."""

import ast
import json
import re
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / "doris-course/02-data-warehousing"
sys.path.insert(0, str(ROOT))
from dw_course import runtime
from dw_course import wwi


class LearningFlowTest(unittest.TestCase):
    def test_every_module_has_blank_exercise_and_folded_executable_solution(self):
        paths = list((ROOT / "level1").glob("*/lab*.ipynb"))
        self.assertEqual(len(paths), 7)
        for path in paths:
            cells = json.loads(path.read_text())["cells"]
            work = [c for c in cells if "course_exercise" in c.get("metadata", {}).get("tags", [])]
            solutions = [c for c in cells if "course_solution" in c.get("metadata", {}).get("tags", [])]
            self.assertEqual(len(work), 1, path)
            self.assertEqual(ast.parse("".join(work[0]["source"])).body, [], path)
            self.assertEqual(len(solutions), 1, path)
            source = "".join(solutions[0]["source"])
            self.assertIn("<details>", source)
            self.assertNotIn("<details open", source)
            blocks = re.findall(r"```python\n(.*?)```", source, re.DOTALL)
            self.assertEqual(len(blocks), 1, path)
            tree = ast.parse(blocks[0])
            self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                                and n.func.id == "expect" for n in ast.walk(tree)), path)
            self.assertLess(cells.index(work[0]), cells.index(solutions[0]))

    def test_ingestion_progresses_from_one_file_to_history(self):
        path = next((ROOT / "level1/module05-ingestion").glob("lab*.ipynb"))
        ids = [c["id"] for c in json.loads(path.read_text())["cells"]]
        self.assertLess(ids.index("cell-8"), ids.index("historical-load"))

    def test_lake_preparation_requires_explicit_start(self):
        from dw_course import lakehouse
        with patch.object(lakehouse, "_run") as run:
            with self.assertRaises(ValueError):
                lakehouse.prepare_lakehouse(None)
            run.assert_not_called()

    def test_notebooks_keep_connections_for_independent_work(self):
        for path in (ROOT / "level1").glob("*/lab*.ipynb"):
            for cell in json.loads(path.read_text())["cells"]:
                if cell["cell_type"] != "code":
                    continue
                tree = ast.parse("".join(cell["source"]))
                closes = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Attribute)
                          and isinstance(node.func.value, ast.Name)
                          and node.func.value.id == "lab" and node.func.attr == "close"]
                self.assertEqual(closes, [], path)

    def test_expected_quality_error_has_no_failure_card(self):
        with patch.object(runtime, "in_notebook", return_value=True), patch.object(runtime, "card") as card:
            with runtime.expected_failure("重复订单检查", "已识别重复订单，质量规则生效"):
                runtime.expect([(11,)], [(10,)])
            self.assertEqual([call.args[1] for call in card.call_args_list], ["ok"])
            self.assertIn("已识别重复订单", card.call_args.args[0])

    def test_missing_expected_error_is_a_real_failure(self):
        with patch.object(runtime, "in_notebook", return_value=False):
            with self.assertRaises(AssertionError):
                with runtime.expected_failure("重复检查", "识别成功"):
                    runtime.expect([(10,)], [(10,)])

    def test_unrelated_exception_is_not_treated_as_learning_success(self):
        with patch.object(runtime, "in_notebook", return_value=True), patch.object(runtime, "card") as card:
            with self.assertRaises(RuntimeError):
                with runtime.expected_failure("重复检查", "识别成功"):
                    raise RuntimeError("connection failed")
            card.assert_not_called()

    def test_successful_internal_checks_do_not_flood_notebook(self):
        with patch.object(runtime, "in_notebook", return_value=True), patch.object(runtime, "card") as card:
            runtime.expect([(10,)], [(10,)])
            card.assert_not_called()
            runtime.expect([(10,)], [(10,)], title="订单数符合预期")
            self.assertEqual(card.call_args.args[2], "订单数符合预期")


class LocalBundleTest(unittest.TestCase):
    def test_default_bundle_prepares_and_reuses_verified_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "datasets").symlink_to(ROOT / "datasets", target_is_directory=True)
            with patch.object(wwi, "COURSE_ROOT", root), patch.dict("os.environ", {}, clear=True):
                paths = wwi.parquet_paths()
                self.assertEqual(len(paths), 10)
                modified = {k: p.stat().st_mtime_ns for k, p in paths.items()}
                self.assertEqual(wwi.parquet_paths(), paths)
                self.assertEqual({k: p.stat().st_mtime_ns for k, p in paths.items()}, modified)

    def test_explicit_missing_directory_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(wwi, "_unpack_bundle") as unpack:
                with self.assertRaises(FileNotFoundError):
                    wwi.parquet_paths(Path(directory) / "missing")
                unpack.assert_not_called()

    def test_archive_members_must_be_exact_regular_files(self):
        entries = {"orders": {"bytes": 1, "sha256": "unused"}}
        for name, kind in [("../orders.parquet", tarfile.REGTYPE),
                           ("orders.parquet", tarfile.SYMTYPE)]:
            member = tarfile.TarInfo(name)
            member.type, member.size = kind, 1
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "wwi"
                with patch.object(wwi, "manifest", return_value={"tables": entries}), patch("tarfile.open") as opened:
                    opened.return_value.__enter__.return_value.getmembers.return_value = [member]
                    with self.assertRaises(ValueError):
                        wwi._unpack_bundle(target)
                    self.assertFalse(target.exists())

    def test_bad_checksum_does_not_publish_partial_bundle(self):
        bad = wwi.manifest()
        next(iter(bad["tables"].values()))["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "wwi"
            with patch.object(wwi, "manifest", return_value=bad):
                with self.assertRaises(ValueError):
                    wwi._unpack_bundle(target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
