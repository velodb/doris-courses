"""Reuse the existing repository quiz renderer without importing its cluster helper."""

import importlib.util
import sys
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "01-real-time-analytics/doris_course/quiz.py"
)
SPEC = importlib.util.spec_from_file_location("dw_course._shared_quiz", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
CourseQuiz = MODULE.CourseQuiz
