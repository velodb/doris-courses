from __future__ import annotations

import ast
from pathlib import Path
import unittest

import nbformat


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "level3/module10-managing-data"


class Level3ContentTest(unittest.TestCase):
    def test_module10_has_guide_and_lab(self) -> None:
        self.assertTrue((MODULE / "course.md").is_file())
        self.assertTrue((MODULE / "lab10_manage_data.ipynb").is_file())

    def test_lab_is_valid_source_notebook(self) -> None:
        path = MODULE / "lab10_manage_data.ipynb"
        notebook = nbformat.read(path, as_version=4)
        nbformat.validate(notebook)
        ids = [cell["id"] for cell in notebook.cells]
        self.assertEqual(len(ids), len(set(ids)))
        for cell in notebook.cells:
            if cell.cell_type == "code":
                ast.parse(cell.source, filename=str(path))
                self.assertFalse(cell.get("outputs"))
                self.assertIsNone(cell.get("execution_count"))

    def test_lab_has_an_explicit_management_boundary(self) -> None:
        notebook = nbformat.read(MODULE / "lab10_manage_data.ipynb", as_version=4)
        code = "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "code")
        self.assertIn("management_events_lab10", code)
        self.assertIn("TRUNCATE TABLE management_events_lab10 PARTITION", code)
        self.assertNotIn("TRUNCATE TABLE events", code)
        self.assertNotIn("DROP TABLE events", code)


if __name__ == "__main__":
    unittest.main()
