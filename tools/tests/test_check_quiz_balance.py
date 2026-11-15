"""Run tools/check_quiz_balance.py on small quiz trees and check its verdicts."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOL = Path(__file__).resolve().parents[1] / "check_quiz_balance.py"


def quiz(questions=6, letter_ids=False):
    items = []
    for number in range(1, questions + 1):
        ids = ["a", "b", "c", "d"] if letter_ids else [f"choice_{number}_{i}" for i in range(4)]
        items.append({
            "id": f"q{number}",
            "objective": f"Objective {number}",
            "prompt": f"Question {number}?",
            # The answer is the shortest option, so the longest-option share stays at zero.
            "options": [{"id": ids[0], "text": "Short"}] + [{"id": i, "text": f"A longer option {i}"} for i in ids[1:]],
            "answer": ids[0],
            "explanation": "Because it is right.",
        })
    return {"title": "Module 1 Quiz: Example", "description": "Example questions.", "questions": items}


def run(tree, *args):
    with tempfile.TemporaryDirectory() as directory:
        for relative, document in tree.items():
            path = Path(directory) / relative
            path.parent.mkdir(parents=True)
            path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        return subprocess.run([sys.executable, str(TOOL), *args, directory], capture_output=True, text=True)


class QuizCheckTests(unittest.TestCase):
    def test_a_balanced_quiz_passes_strict(self):
        result = run({"09-example/level1/module01-x/quiz01_x.yaml": quiz()}, "--strict")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_positions_are_counted_as_learners_see_them(self):
        # Every answer is written first, but the shuffle spreads them over the positions.
        result = run({"09-example/level1/module01-x/quiz01_x.yaml": quiz()})
        row = next(line for line in result.stdout.splitlines() if line.startswith("09-example level1"))
        self.assertEqual(sorted(row.split()[-4:]), ["1", "1", "2", "2"], row)

    def test_structure_problems_always_fail(self):
        result = run({"09-example/level1/module01-x/quiz01_x.yaml": quiz(letter_ids=True)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("single letter", result.stdout)

    def test_balance_and_question_count_fail_only_with_strict(self):
        tree = {"09-example/level1/module01-x/quiz01_x.yaml": quiz(questions=5)}
        self.assertEqual(run(tree).returncode, 0)
        result = run(tree, "--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("without 6 questions", result.stdout)

    def test_native_titles_use_manifest_language_and_separate_statistics(self):
        translated = quiz()
        translated["title"] = "1-модуль тесті: Мысал"
        tree = {
            "09-example/level1/module01-x/quiz01_x.yaml": quiz(),
            "kk-Cyrl/09-example/course.yaml": {"language": "kk-Cyrl"},
            "kk-Cyrl/09-example/level1/module01-x/quiz01_x.yaml": translated,
        }
        result = run(tree)
        self.assertEqual(result.returncode, 0, result.stdout)
        english = next(line for line in result.stdout.splitlines() if line.startswith("09-example level1"))
        native = next(line for line in result.stdout.splitlines() if line.startswith("kk-Cyrl 09-example level1"))
        self.assertEqual(english.split()[2], "6")
        self.assertEqual(native.split()[3], "6")

    def test_native_quizzes_still_reject_invalid_answers_and_ids(self):
        translated = quiz(letter_ids=True)
        translated["title"] = "1-модуль тесті: Мысал"
        result = run({
            "kk-Cyrl/09-example/course.yaml": {"language": "kk-Cyrl"},
            "kk-Cyrl/09-example/level1/module01-x/quiz01_x.yaml": translated,
        })
        self.assertEqual(result.returncode, 1)
        self.assertIn("single letter", result.stdout)

    def test_english_title_policy_and_native_root_binding_remain_enforced(self):
        document = quiz()
        document["title"] = "1-модуль тесті: Мысал"
        result = run({"09-example/level1/module01-x/quiz01_x.yaml": document})
        self.assertEqual(result.returncode, 1)
        self.assertIn("Module N Quiz", result.stdout)
        result = run({
            "09-example/course.yaml": {"language": "kk-Cyrl"},
            "09-example/level1/module01-x/quiz01_x.yaml": document,
        })
        self.assertEqual(result.returncode, 1)
        self.assertIn("native source root", result.stdout)


if __name__ == "__main__":
    unittest.main()
