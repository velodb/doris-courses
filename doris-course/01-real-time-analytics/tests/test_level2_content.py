from __future__ import annotations

import ast
from pathlib import Path
import unittest

import nbformat


ROOT = Path(__file__).resolve().parents[1]
LEVEL2 = ROOT / "level2"


class Level2ContentTest(unittest.TestCase):
    def test_all_level2_modules_have_a_guide_and_lab(self) -> None:
        expected = {
            "module04-modeling": "lab4_model_data.ipynb",
            "module05-analyzing": "lab5_analyze_data.ipynb",
            "module06-joining": "lab6_join_data.ipynb",
            "module07-updating-deleting": "lab7_update_delete_data.ipynb",
        }
        for module, lab_name in expected.items():
            module_dir = LEVEL2 / module
            self.assertTrue((module_dir / "course.md").is_file(), module)
            self.assertTrue((module_dir / lab_name).is_file(), module)

    def test_level2_notebooks_are_valid_source_notebooks(self) -> None:
        notebooks = sorted(LEVEL2.glob("module*/*.ipynb"))
        self.assertEqual(len(notebooks), 4)
        for path in notebooks:
            notebook = nbformat.read(path, as_version=4)
            nbformat.validate(notebook)
            ids = [cell.get("id") for cell in notebook.cells]
            self.assertEqual(len(ids), len(set(ids)), path)
            for cell in notebook.cells:
                if cell.cell_type == "code":
                    ast.parse(cell.source, filename=str(path))
                    self.assertFalse(cell.get("outputs"), path)
                    self.assertIsNone(cell.get("execution_count"), path)

    def test_labs_keep_the_level1_baseline_immutable(self) -> None:
        for path in sorted(LEVEL2.glob("module*/*.ipynb")):
            source = nbformat.read(path, as_version=4).cells
            code = "\n".join(cell.source for cell in source if cell.cell_type == "code")
            self.assertNotIn("TRUNCATE TABLE events", code, path)
            self.assertNotIn("DROP TABLE events", code, path)

    def test_teaching_contract_terms_are_present(self) -> None:
        expected_terms = {
            "module05-analyzing": ("LAG", "ROW_NUMBER", "cumulative"),
            "module06-joining": ("EXPLAIN", "semi", "anti"),
            "module07-updating-deleting": ("sequence", "partial", "soft deletion"),
        }
        for module, terms in expected_terms.items():
            text = "\n".join(p.read_text() for p in (LEVEL2 / module).glob("*.md"))
            notebook_text = "\n".join(
                cell.source
                for p in (LEVEL2 / module).glob("*.ipynb")
                for cell in nbformat.read(p, as_version=4).cells
            )
            combined = f"{text}\n{notebook_text}".lower()
            for term in terms:
                self.assertIn(term.lower(), combined, (module, term))


if __name__ == "__main__":
    unittest.main()
