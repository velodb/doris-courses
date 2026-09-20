"""Offline regression checks for optional streaming orchestration and notebooks."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import nbformat
import yaml

ROOT = Path(__file__).resolve().parents[3] / "doris-course/02-data-warehousing"
sys.path.insert(0, str(ROOT))
from dw_course import streaming


class StreamingTest(unittest.TestCase):
    def test_explicit_opt_in_and_profiles(self):
        with patch.object(streaming, "compose") as command:
            for args in [("kafka", False), ("other", True)]:
                with self.assertRaises(ValueError):
                    streaming.prepare_streaming(args[0], start=args[1])
            command.assert_not_called()

    def test_wait_returns_only_matching_observation(self):
        read = Mock(side_effect=[[], [1]])
        with patch.object(streaming.time, "sleep"):
            self.assertEqual(streaming.wait_for(read, lambda x: x == [1], description="test"), [1])
        self.assertEqual(read.call_count, 2)

    def test_timeout_reports_last_observation(self):
        with patch.object(streaming.time, "monotonic", side_effect=[0, 0, 2]), patch.object(streaming.time, "sleep"):
            with self.assertRaisesRegex(TimeoutError, "last observation = 'lagging'"):
                streaming.wait_for(lambda: "lagging", bool_false, description="test", timeout=1)

    def test_sql_client_errors_are_not_success(self):
        with patch.object(streaming, "compose", return_value="[ERROR] connector missing"):
            with self.assertRaisesRegex(RuntimeError, "successful INSERT"):
                streaming.submit_sql("INSERT INTO sink SELECT * FROM src")

    def test_submit_extracts_job_and_waits_for_running(self):
        job = "a" * 32
        with patch.object(streaming, "compose", return_value="Job ID: " + job), patch.object(streaming, "flink_api", return_value={"state": "RUNNING"}):
            self.assertEqual(streaming.submit_sql("SQL"), job)

    def test_terminal_failure_is_reported_without_waiting(self):
        with patch.object(streaming, "flink_api", return_value={"state": "FAILED"}):
            with self.assertRaisesRegex(RuntimeError, "FAILED; see"):
                streaming.job_state("a" * 32)

    def test_mysql_timezone_matches_cdc_notebook(self):
        config = yaml.safe_load(streaming.COMPOSE.read_text())
        self.assertIn("--default-time-zone=+08:00", config["services"]["mysql"]["command"])
        notebook = nbformat.read(ROOT / "level1/module05-ingestion/optional5_flink_mysql_cdc.ipynb", 4)
        self.assertIn("'server-time-zone'='Asia/Shanghai'", "\n".join(c.source for c in notebook.cells))

    def test_savepoint_requires_job_id_and_completed_path(self):
        with self.assertRaises(ValueError):
            streaming.stop_with_savepoint("not-a-job; echo bad")
        with patch.object(streaming, "compose", return_value="savepoint failed"):
            with self.assertRaisesRegex(RuntimeError, "No completed savepoint"):
                streaming.stop_with_savepoint("a" * 32)

    def test_compose_failures_include_output(self):
        with patch.object(streaming.subprocess, "run", return_value=Mock(returncode=1, stdout="fixture failed")):
            with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                streaming.compose("ps")

    def test_local_services_pinned_and_private(self):
        config = yaml.safe_load(streaming.COMPOSE.read_text())
        self.assertEqual(config["name"], "doris-warehousing-streaming")
        self.assertTrue(config["networks"]["doris"]["external"])
        for service in config["services"].values():
            self.assertNotIn(":latest", service["image"])
            for port in service.get("ports", []):
                self.assertTrue(port.startswith("127.0.0.1:"))
        self.assertNotIn("ports", config["services"]["mysql"])
        self.assertNotIn("ports", config["services"]["kafka"])

    def test_notebooks_are_clean_and_teaching_sql_visible(self):
        paths = sorted((ROOT / "level1/module05-ingestion").glob("optional5_*.ipynb"))
        self.assertEqual(len(paths), 2)
        sources = []
        for path in paths:
            notebook = nbformat.read(path, 4)
            nbformat.validate(notebook)
            for cell in notebook.cells:
                if cell.cell_type == "code":
                    ast.parse(cell.source)
                    self.assertEqual(cell.outputs, [])
                    self.assertIsNone(cell.execution_count)
            text = "\n".join(c.source for c in notebook.cells)
            self.assertIn('dw_course_l1_streaming', text)
            self.assertNotIn("DROP DATABASE", text)
            self.assertNotIn("down -v", text)
            self.assertIn("lab.close()", notebook.cells[-1].source)
            sources.append(text)
        self.assertIn("CREATE ROUTINE LOAD", "\n".join(sources))
        self.assertIn("'connector'='mysql-cdc'", "\n".join(sources))
        self.assertIn('restored["external_path"]', "\n".join(sources))


def bool_false(value):
    return False
