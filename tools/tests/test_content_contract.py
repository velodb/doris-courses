"""Admission tests for valid content and changes that would break the website."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_content_contract import REPO, SCHEMA, validate


class ContentContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = "doris-course/01-real-time-analytics/course.yaml"
        self.course = yaml.safe_load((REPO / source).read_text())
        self.course["modules"] = self.course["modules"][:1]
        self.course["modules"][0].pop("optional", None)
        self.course["estimated_minutes"] = self.course["modules"][0]["estimated_minutes"]
        self.catalog = {"schema_version": 1, "title": "Courses", "description": "Example catalog",
                        "courses": [source], "programs": [{"id": "program", "title": "Program", "required_courses": [self.course["slug"]]}]}
        self.manifest = self.root / source
        self.manifest.parent.mkdir(parents=True)
        module = self.course["modules"][0]
        for field in ("course", "lab"):
            path = self.manifest.parent / module[field]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Lesson\n" if field == "course" else "{}")
        self.quiz_path = self.manifest.parent / module["quiz"]
        self.quiz = yaml.safe_load((REPO / source).parent.joinpath(module["quiz"]).read_text())
        for field in ("guide", "requirements"):
            (self.manifest.parent / self.course["lab_setup"][field]).write_text("Example\n")
        schema = self.root / "contracts/v1/schema.json"
        schema.parent.mkdir(parents=True)
        schema.write_text(SCHEMA.read_text())
        self.write()

    def write(self):
        for path, value in [(self.manifest, self.course), (self.root / "catalog.yaml", self.catalog), (self.quiz_path, self.quiz)]:
            path.write_text(yaml.safe_dump(value, sort_keys=False))

    def problems(self):
        self.write()
        return "\n".join(validate(self.root))

    def baseline(self):
        for args in [["init", "-q"], ["config", "user.name", "Contract Test"], ["config", "user.email", "contract@example.com"], ["add", "."], ["commit", "-qm", "baseline"]]:
            subprocess.run(["git", *args], cwd=self.root, check=True)

    def test_complete_package(self):
        self.assertEqual(validate(self.root), [])

    def test_unknown_version_and_field(self):
        self.catalog["schema_version"] = 2
        self.assertIn("1 was expected", self.problems())
        self.catalog["schema_version"] = 1
        self.course["executable"] = "run.sh"
        self.assertIn("Additional properties", self.problems())

    def test_catalog_must_list_every_course(self):
        path = self.root / "doris-course/99-forgotten/course.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        self.assertIn("list every course.yaml", self.problems())

    def test_missing_asset_or_symlink(self):
        path = self.manifest.parent / self.course["modules"][0]["lab"]
        path.unlink()
        self.assertIn("missing file", self.problems())
        path.symlink_to(self.manifest)
        self.assertIn("symlinks", self.problems())

    def test_escaping_path_and_duplicate_yaml(self):
        self.course["modules"][0]["course"] = "../../../../outside.md"
        self.assertIn("does not match", self.problems())
        self.write()
        self.manifest.write_text(self.manifest.read_text() + "title: Duplicate\n")
        self.assertIn("duplicate YAML key", "\n".join(validate(self.root)))

    def test_unknown_program_and_level(self):
        self.catalog["programs"][0]["required_courses"] = ["missing"]
        self.course["modules"][0]["level"] = 99
        self.assertIn("unknown course missing", self.problems())
        self.assertIn("unknown level 99", self.problems())

    def test_preview_cannot_issue_certificates(self):
        self.course["completion"]["enabled"] = False
        self.assertIn("certificates require completion", self.problems())
        self.course["certificate"]["enabled"] = False
        self.course["status"] = "preview"
        self.assertEqual(self.problems(), "")

    def test_planned_module_cannot_grant_credit(self):
        self.course["modules"][0]["status"] = "planned"
        self.assertIn("planned/retired modules", self.problems())

    def test_invalid_answer_and_duplicate_ids(self):
        self.quiz["questions"][0]["answer"] = "missing"
        self.assertIn("invalid answer", self.problems())
        self.quiz["questions"][0]["answer"] = self.quiz["questions"][0]["options"][0]["id"]
        self.quiz["questions"].append(copy.deepcopy(self.quiz["questions"][0]))
        self.assertIn("duplicate question", self.problems())

    def test_unsupported_question_type_and_passing_score(self):
        self.quiz["questions"][0]["type"] = "javascript"
        self.quiz["passing_score_percent"] = 101
        self.assertIn("single_choice", self.problems())
        self.assertIn("greater than the maximum", self.problems())

    def test_stable_module_identity(self):
        self.baseline()
        self.course["modules"][0]["id"] = "renamed"
        self.write()
        self.assertIn("module identity removed", "\n".join(validate(self.root, "HEAD")))

    def test_titles_can_change_but_schema_cannot(self):
        self.baseline()
        self.course["modules"][0]["title"] = "New title"
        self.write()
        self.assertEqual(validate(self.root, "HEAD"), [])
        path = self.root / "contracts/v1/schema.json"
        path.write_text(json.dumps(json.loads(path.read_text())))
        self.assertIn("schema is immutable", "\n".join(validate(self.root, "HEAD")))


if __name__ == "__main__":
    unittest.main()
