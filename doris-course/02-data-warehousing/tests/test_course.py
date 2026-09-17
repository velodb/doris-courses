"""Offline course checks. These tests do not connect to Doris."""

import ast
import json
import re
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import nbformat
import yaml

from dw_course.runtime import COURSE_ROOT, WarehouseLab, expect, fixture, identifier
from dw_course.schema import order_ddl


class FixturesTest(unittest.TestCase):
    def test_initial_contract(self):
        orders = fixture("orders.json")
        expected = fixture("expected_summary.json")
        self.assertEqual(len(orders), expected["initial_rows"])
        self.assertEqual(sum(Decimal(x["order_amount"]) for x in orders), Decimal("1400.00"))
        self.assertEqual(len({x["order_id"] for x in orders}), 10)
        raw = fixture("raw_orders.json")
        self.assertEqual(len(raw), 12)
        self.assertEqual([r["input_id"] for r in raw if r["order_id"] is None], [12])

    def test_replay_independent_oracle(self):
        initial = fixture("orders.json")
        deliveries = fixture("deliveries.json")
        current = {x["order_id"]: x for x in initial}
        history = {x["event_id"]: x for x in initial}
        for attempt in range(2):
            for delivered in deliveries:
                record = {key: value for key, value in delivered.items() if key != "delivery_id"}
                if record["event_id"] in history:
                    self.assertEqual(history[record["event_id"]], record)
                history[record["event_id"]] = record
                old = current.get(record["order_id"])
                if old is None or record["event_version"] > old["event_version"]:
                    current[record["order_id"]] = record
            self.assertEqual([current[key] for key in sorted(current)], fixture("expected_current.json"))
            self.assertEqual(len(history), 16)
        summary = fixture("expected_summary.json")
        self.assertEqual(Counter(row["status"] for row in current.values()), summary["statuses"])
        for column, expected in [("order_amount", "1510.00"), ("paid_amount", "250.00"), ("refund_amount", "150.00")]:
            self.assertEqual(sum(Decimal(row[column]) for row in current.values()), Decimal(expected))

    def test_csv_matches_json(self):
        import csv
        from dw_course.schema import ORDER_COLUMNS
        with (COURSE_ROOT / "datasets/orders.csv").open() as stream:
            rows = list(csv.reader(stream))
        expected = [[str(record[col]) for col in ORDER_COLUMNS] for record in fixture("orders.json")]
        self.assertEqual(rows, expected)


class RuntimeTest(unittest.TestCase):
    def test_scoped_database_and_opt_in(self):
        with patch.dict("os.environ", {}, clear=True), patch("pymysql.connect") as connect:
            with self.assertRaises(RuntimeError):
                WarehouseLab()
            connect.assert_not_called()
        with patch.dict("os.environ", {"DW_ALLOW_WRITES": "yes", "DW_DATABASE": "production"}, clear=True), patch("pymysql.connect") as connect:
            with self.assertRaises(ValueError):
                WarehouseLab()
            connect.assert_not_called()

    def test_identifiers_and_fixture_scope(self):
        for value in ["a; DROP TABLE t", "catalog.table", "../orders"]:
            with self.assertRaises(ValueError):
                identifier(value)
        with self.assertRaises(ValueError):
            fixture("../orders.json")

    def test_expect_catches_mismatch(self):
        with self.assertRaises(AssertionError):
            expect([(11, Decimal("1400.00"))], [(10, "1400.00")])

    def test_model_contract(self):
        self.assertIn('UNIQUE KEY(order_id)', order_ddl("d06_current", current=True))
        self.assertIn('"function_column.sequence_col"="event_version"', order_ddl("d06_current", current=True))
        self.assertIn("UNIQUE KEY(event_id)", order_ddl("d06_history", history=True))
        with self.assertRaises(ValueError):
            order_ddl("invalid", current=True, history=True)

    def test_stream_load_does_not_follow_redirect(self):
        lab = WarehouseLab.__new__(WarehouseLab)
        lab.database, lab.user, lab.password = "dw_course_l1_test", "student", "not-a-real-password"
        response = Mock(status_code=307)
        with patch("requests.put", return_value=response) as put:
            with self.assertRaises(RuntimeError):
                lab.stream_load("orders", COURSE_ROOT / "datasets/orders.csv", "test", "order_id")
        self.assertFalse(put.call_args.kwargs["allow_redirects"])


class MaterialsTest(unittest.TestCase):
    def test_numbered_material_names(self):
        for module in (COURSE_ROOT / "level1").glob("module*"):
            match = re.match(r"module(\d+)([a-z]?)", module.name)
            number = str(int(match[1])) + match[2]
            self.assertEqual(len(list(module.glob(f"course{number}_*.md"))), 1)
            self.assertEqual(len(list(module.glob(f"quiz{number}_*.yaml"))), 1)
            self.assertEqual(len(list(module.glob(f"quiz{number}_*.ipynb"))), 1)
            self.assertFalse((module / "course.md").exists())
            self.assertFalse((module / "quiz.ipynb").exists())

    def test_notebooks_are_valid_clean_and_compilable(self):
        paths = list((COURSE_ROOT / "level1").glob("*/*.ipynb"))
        self.assertEqual(len(paths), 14)
        for path in paths:
            notebook = nbformat.read(path, as_version=4)
            nbformat.validate(notebook)
            for cell in notebook.cells:
                if cell.cell_type == "code":
                    self.assertEqual(cell.outputs, [], path)
                    self.assertIsNone(cell.execution_count, path)
                    ast.parse(cell.source, filename=str(path))

    def test_quiz_contract_and_shared_renderer(self):
        from dw_course.quiz import CourseQuiz
        paths = list((COURSE_ROOT / "level1").glob("*/quiz*.yaml"))
        self.assertEqual(len(paths), 7)
        for path in paths:
            data = yaml.safe_load(path.read_text())
            self.assertEqual(len(data["questions"]), 5, path)
            ids = [q["id"] for q in data["questions"]]
            self.assertEqual(len(ids), len(set(ids)), path)
            for question in data["questions"]:
                options = [option["id"] for option in question["options"]]
                self.assertEqual(len(options), len(set(options)))
                self.assertIn(question["answer"], options)
                self.assertTrue(question["explanation"])
            self.assertIsInstance(CourseQuiz.from_yaml(path), CourseQuiz)

    def test_local_markdown_links(self):
        for path in COURSE_ROOT.rglob("*.md"):
            if any(part in {".venv", ".runtime", ".ipynb_checkpoints"} for part in path.parts):
                continue
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if target.startswith(("https://", "http://", "#")):
                    continue
                self.assertTrue((path.parent / target.split("#")[0]).exists(), f"{path}: {target}")


    def test_notebook_markdown_links_and_covers(self):
        for path in (COURSE_ROOT / "level1").glob("*/*.ipynb"):
            notebook = nbformat.read(path, as_version=4)
            self.assertIn("DATA WAREHOUSING WITH APACHE DORIS", notebook.cells[0].source)
            self.assertIn("border-top:4px solid #0f766e", notebook.cells[0].source)
            for cell in notebook.cells:
                if cell.cell_type != "markdown":
                    continue
                for target in re.findall(r"\]\(([^)]+)\)", cell.source):
                    if target.startswith(("https://", "http://", "#")):
                        continue
                    self.assertTrue((path.parent / target.split("#")[0]).exists(), f"{path}: {target}")


class AlignmentTest(unittest.TestCase):
    def test_display_components_are_reused(self):
        from dw_course import ui
        from dw_course._shared import load_component
        shared = load_component("doris_client")
        self.assertIs(ui.show_frame, shared.show_frame)
        self.assertIs(ui.card, shared.card)
        self.assertIs(ui.install_styles, shared.install_styles)

    def test_sql_keeps_column_names(self):
        lab = WarehouseLab.__new__(WarehouseLab)
        cursor = Mock()
        cursor.description = [("order_id",), ("amount",)]
        cursor.fetchall.return_value = [(1001, Decimal("100.00"))]
        manager = Mock()
        manager.__enter__ = Mock(return_value=cursor)
        manager.__exit__ = Mock(return_value=False)
        lab.connection = Mock()
        lab.connection.cursor.return_value = manager
        with patch("dw_course.runtime.in_notebook", return_value=True), patch("dw_course.runtime.show_sql"), patch("dw_course.runtime.show_frame") as show:
            frame = lab.sql("SELECT order_id, amount FROM example")
        self.assertEqual(list(frame.columns), ["order_id", "amount"])
        self.assertEqual(frame.iloc[0]["amount"], Decimal("100.00"))
        show.assert_called_once()

    def test_compose_scope_and_persistence(self):
        from dw_course.docker_runtime import COMPOSE_FILE, CONNECTION, PROJECT
        config = yaml.safe_load(COMPOSE_FILE.read_text())
        self.assertEqual(config["name"], PROJECT)
        service = config["services"]["doris"]
        self.assertEqual(service["image"], "apache/doris:all-in-one-4.1.3")
        self.assertEqual(service["ports"], ["127.0.0.1:52030:9030", "127.0.0.1:51030:8030", "127.0.0.1:51040:8040"])
        self.assertEqual(CONNECTION["DW_PORT"], "52030")
        self.assertEqual(set(config["volumes"]), {"fe-meta", "be-storage"})

    def test_docker_start_requires_explicit_opt_in(self):
        from dw_course.docker_runtime import prepare_environment
        with patch.dict("os.environ", {}, clear=True), patch("subprocess.run") as run:
            with self.assertRaises(RuntimeError):
                prepare_environment()
            run.assert_not_called()

    def test_docker_start_checks_health_before_connection(self):
        from dw_course.docker_runtime import prepare_environment, CONNECTION, compose_command
        cursor = Mock()
        cursor.fetchone.return_value = (1,)
        manager = Mock()
        manager.__enter__ = Mock(return_value=cursor)
        manager.__exit__ = Mock(return_value=False)
        connection = Mock()
        connection.cursor.return_value = manager
        with patch.dict("os.environ", {"DW_START_SANDBOX": "yes"}, clear=True), patch("subprocess.run") as run, patch("pymysql.connect", return_value=connection):
            self.assertEqual(prepare_environment(), CONNECTION)
            self.assertEqual(run.call_args_list[0].args[0], compose_command("config", "--quiet"))
            self.assertEqual(run.call_args_list[1].args[0], compose_command("up", "-d", "--wait", "--wait-timeout", "300"))
            connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
