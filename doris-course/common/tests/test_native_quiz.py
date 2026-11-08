"""Native language sources work in plain Python/IPython without a runtime adapter."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

from doris_labkit.messages import Messages
from doris_labkit.progress import Store
from doris_labkit.quiz import CourseQuiz
from doris_labkit.quizbank import QuizFormatError, load_quiz
from doris_labkit.quizsession import Presentation, QuizConflict
from test_quiz import QUIZ_YAML


ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / 'doris-course/kk-Cyrl'
ENGLISH = ROOT / 'doris-course/01-real-time-analytics'


def complete(quiz):
    quiz._render_question()
    for question in quiz.questions:
        content = quiz._root.children[0]
        content.children[1].value = question.answer
        content.children[3].children[1].click()
        content.children[3].children[2].click()
    return quiz._root.children[0].children[0].value


class NativeQuizTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.course = self.root / 'kk-Cyrl/course'
        self.course.mkdir(parents=True)
        self.path = self.course / 'level1/module1/quiz.yaml'
        self.path.parent.mkdir(parents=True)
        self.path.write_text(QUIZ_YAML.replace('Module 1 Quiz: Example', '1-модуль тесті: Мысал'))
        (self.course / 'course.yaml').write_text('language: kk-Cyrl\n')
        self.ui = self.course.parent / 'ui.yaml'
        self.ui.write_bytes((NATIVE / 'ui.yaml').read_bytes())

    def test_implicit_native_locale_loads_matching_ui_and_unbound_results(self):
        quiz = CourseQuiz.from_yaml(self.path)
        self.assertEqual(quiz.locale, 'kk-Cyrl')
        self.assertIsNone(quiz.presentation)
        self.assertEqual(quiz.source_path, self.path.resolve())
        self.assertEqual(quiz.t('quiz.text-2'), 'Жауапты жіберу')
        result = complete(quiz)
        self.assertIn('lang="kk-Cyrl"', result)
        self.assertIn('100%', result)
        self.assertIsNone(quiz._receipt)
        self.assertIsNone(quiz._submission[1])

    def test_explicit_english_ui_does_not_relabel_native_source_as_legacy(self):
        native = CourseQuiz.from_yaml(self.path, locale='en')
        self.assertEqual(native.locale, 'en')
        self.assertEqual(native.source_locale, 'kk-Cyrl')
        self.assertIsNone(native.presentation)
        self.assertEqual(native.t('quiz.text-2'), 'Submit answer')
        self.path.write_text(QUIZ_YAML)
        self.assertIsNone(CourseQuiz.from_yaml(self.path, locale='en').presentation)
        (self.course / 'course.yaml').write_text('language: en\n')
        self.path.write_text(QUIZ_YAML.replace('Module 1 Quiz: Example', '1-модуль тесті: Мысал'))
        with self.assertRaises(QuizFormatError):
            CourseQuiz.from_yaml(self.path, locale='en')
        self.path.write_text(QUIZ_YAML)
        self.ui.write_text('not a message dictionary')
        quiz = CourseQuiz.from_yaml(self.path, locale='en')
        self.assertEqual(quiz.locale, 'en')
        self.assertEqual(quiz.presentation.locale, 'en')
        self.assertIsNone(quiz.presentation.semantic_revision)

    def test_explicit_messages_take_priority_and_require_no_source_dictionary(self):
        self.ui.unlink()
        source = Messages()
        supplied = Messages('kk-Cyrl', messages={k: 'Ә ' + v for k, v in source.source.items()})
        quiz = CourseQuiz.from_yaml(self.path, locale='en', messages=supplied)
        self.assertIs(quiz.t, supplied)
        self.assertEqual(quiz.locale, 'kk-Cyrl')
        self.assertIsNone(quiz.presentation)
        english_ui = CourseQuiz.from_yaml(self.path, locale='kk-Cyrl', messages=Messages())
        self.assertEqual(english_ui.locale, 'en')
        self.assertEqual(english_ui.source_locale, 'kk-Cyrl')
        self.assertIsNone(english_ui.presentation)

    def test_unknown_explicit_locale_is_rejected_before_reading_a_quiz(self):
        absent = self.root / 'absent.yaml'
        for locale in ('../../secret', 'fr', '', 'kk', ['kk-Cyrl']):
            with self.subTest(locale=locale), self.assertRaisesRegex(ValueError, 'Unsupported Quiz locale'):
                CourseQuiz.from_yaml(absent, locale=locale)
        source = Messages()
        unknown = Messages('unknown', messages=source.source)
        with self.assertRaisesRegex(ValueError, 'Unsupported Quiz locale'):
            CourseQuiz.from_yaml(absent, messages=unknown)

    def test_unknown_source_locale_cannot_select_an_arbitrary_dictionary_path(self):
        (self.course / 'course.yaml').write_text('language: ../../secret\n')
        self.ui.write_text('!!python/object:never.imported {}')
        with self.assertRaisesRegex(ValueError, 'Unsupported Quiz locale'):
            CourseQuiz.from_yaml(self.path)

    def test_dictionary_is_fixed_local_yaml_and_has_no_search_fallback(self):
        self.ui.unlink()
        unrelated = self.root / 'ui.yaml'
        unrelated.write_bytes((NATIVE / 'ui.yaml').read_bytes())
        with self.assertRaisesRegex(ValueError, 'regular local YAML'):
            CourseQuiz.from_yaml(self.path)
        self.ui.symlink_to(unrelated)
        with self.assertRaisesRegex(ValueError, 'regular local YAML'):
            CourseQuiz.from_yaml(self.path)
        self.ui.unlink()
        self.ui.write_text('quiz.text-2: Ә\n')
        with self.assertRaisesRegex(ValueError, 'Incomplete message dictionary'):
            CourseQuiz.from_yaml(self.path)
        self.ui.write_text('x' * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, 'regular local YAML'):
            CourseQuiz.from_yaml(self.path)

    def test_manifest_symlink_and_non_language_parent_are_rejected(self):
        manifest = self.course / 'course.yaml'
        manifest.unlink()
        target = self.root / 'metadata.yaml'
        target.write_text('language: kk-Cyrl\n')
        manifest.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'regular local YAML'):
            CourseQuiz.from_yaml(self.path)
        manifest.unlink()
        manifest.write_text('language: kk-Cyrl\n')
        moved = self.root / 'arbitrary'
        self.course.parent.rename(moved)
        with self.assertRaisesRegex(ValueError, 'matching language source root'):
            CourseQuiz.from_yaml(moved / 'course/level1/module1/quiz.yaml')

    def test_quiz_symlink_uses_actual_source_course_instead_of_the_alias_parent(self):
        english = self.root / 'english'
        english.mkdir()
        (english / 'course.yaml').write_text('language: en\n')
        real = english / 'quiz.yaml'
        real.write_text(QUIZ_YAML)
        self.path.unlink()
        self.path.symlink_to(real)
        quiz = CourseQuiz.from_yaml(self.path)
        self.assertEqual(quiz.locale, 'en')
        self.assertEqual(quiz.source_path, real.resolve())

    def test_english_legacy_identity_and_file_digest_are_unchanged(self):
        path = next(ENGLISH.rglob('quiz01*.yaml'))
        quiz = CourseQuiz.from_yaml(path)
        expected = Presentation.legacy(load_quiz(path), hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(quiz.presentation, expected)
        self.assertEqual(quiz.locale, 'en')

    def test_unbound_self_study_cannot_switch_to_an_admitted_release(self):
        quiz = CourseQuiz.from_yaml(self.path)
        bank = load_quiz(self.path, localized=True)
        descriptor = {'id': 'q.example', 'semantic_revision': 'meaning.1', 'passing_score_percent': 80,
            'questions': [{'id': q.question_id, 'option_ids': [o.option_id for o in q.options], 'answer': q.answer}
                          for q in bank.questions],
            'presentations': [{'locale': 'kk-Cyrl', 'presentation_revision': 'c' * 64}]}
        bound = Presentation.admitted(bank, descriptor, locale='kk-Cyrl', policy_revision='policy.1',
                                     source_digest=hashlib.sha256(self.path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(QuizConflict, 'not equivalent'):
            quiz.switch_presentation(bound, messages=quiz.t)
        self.assertIsNone(quiz.presentation)

        # An installation adapter that supplies an admitted release and its
        # context still follows the bound path, with no native metadata lookup.
        self.ui.unlink()
        (self.course / 'course.yaml').write_text('not valid: [')
        installed = CourseQuiz.from_yaml(self.path, presentation=bound, messages=quiz.t)
        self.assertEqual(installed.presentation, bound)
        self.assertEqual(installed.locale, 'kk-Cyrl')

    def test_all_33_native_banks_answer_in_kazakh_without_saving_workshop_credit(self):
        database = self.root / 'progress.sqlite3'
        store = Store(database)
        paths = sorted(NATIVE.rglob('quiz*.yaml'))
        self.assertEqual(len(paths), 33)
        environment = {'DORIS_WORKSHOP_PROGRESS_DB': str(database),
                       'DORIS_WORKSHOP_PROGRESS_PLAN': str(self.root / 'must-not-read-plan.json'),
                       'DORIS_WORKSHOP_PROGRESS_SOURCE': str(NATIVE)}
        with patch.dict(os.environ, environment):
            for path in paths:
                with self.subTest(path=path.relative_to(NATIVE)):
                    quiz = CourseQuiz.from_yaml(path)
                    self.assertEqual(quiz.locale, 'kk-Cyrl')
                    self.assertEqual(quiz.source_locale, 'kk-Cyrl')
                    self.assertIsNone(quiz.presentation)
                    with contextlib.redirect_stdout(io.StringIO()):
                        quiz.show()
                    result = complete(quiz)
                    self.assertIn(quiz.t('quiz.text-16', correct_count=6, total=6, percent=100), result)
                    self.assertIn('lang="kk-Cyrl"', result)
                    self.assertNotIn(quiz.t('quiz.text-23'), result)
                    self.assertNotIn(quiz.t('quiz.text-21'), result)
                    self.assertTrue(quiz._save_complete)
                    self.assertIsNone(quiz._receipt)
                    self.assertEqual(len(quiz._answers), 6)
            for explicit in ({'locale': 'en'}, {'messages': Messages()}):
                quiz = CourseQuiz.from_yaml(paths[0], **explicit)
                self.assertIsNone(quiz.presentation)
                self.assertEqual(quiz.source_locale, 'kk-Cyrl')
                self.assertIn('100%', complete(quiz))
                self.assertTrue(quiz._save_complete)
                self.assertIsNone(quiz._receipt)
        self.assertEqual(store.records(), {})
        self.assertEqual(store.attempts(), [])


class OrdinaryKernelNativeQuizTests(unittest.TestCase):
    def test_plain_ipython_kernel_loads_and_answers_33_quizzes_without_runtime_adapter(self):
        from jupyter_client import KernelManager
        with tempfile.TemporaryDirectory() as tmp:
            manager = KernelManager(kernel_name='python3')
            manager.kernel_spec.argv = [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}']
            env = {k: v for k, v in os.environ.items() if not k.startswith('DORIS_WORKSHOP_')}
            env.update(PYTHONPATH=str(ROOT / 'doris-course/common'), IPYTHONDIR=str(Path(tmp) / 'ipython'))
            client = None
            try:
                manager.start_kernel(cwd=tmp, env=env)
                client = manager.blocking_client()
                client.start_channels()
                client.wait_for_ready(timeout=40)
                code = f'''
import json, sys
from pathlib import Path
from doris_labkit.quiz import CourseQuiz
paths = sorted(Path({str(NATIVE)!r}).rglob('quiz*.yaml'))
assert len(paths) == 33
for path in paths:
    quiz = CourseQuiz.from_yaml(path)
    assert quiz.locale == 'kk-Cyrl' and quiz.presentation is None
    quiz.show()
    for question in quiz.questions:
        content = quiz._root.children[0]
        content.children[1].value = question.answer
        content.children[3].children[1].click()
        content.children[3].children[2].click()
    html = quiz._root.children[0].children[0].value
    assert quiz.t('quiz.text-16', correct_count=6, total=6, percent=100) in html
    assert 'lang="kk-Cyrl"' in html and quiz._receipt is None
assert 'multilingual' not in sys.modules
print('NATIVE_QUIZ_RESULT ' + json.dumps({{'quizzes':33,'locale':'kk-Cyrl','correct':198,'admitted':False,'runtime_adapter_imported':False}}))
'''
                message_id = client.execute(code)
                errors, output, widget_views = [], '', 0
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        message = client.get_iopub_msg(timeout=5)
                    except queue.Empty:
                        continue
                    if message['parent_header'].get('msg_id') != message_id:
                        continue
                    kind, content = message['msg_type'], message['content']
                    if kind == 'error':
                        errors.append(content['ename'] + ': ' + content['evalue'])
                    if kind == 'stream':
                        output += content['text']
                    if kind == 'display_data' and 'application/vnd.jupyter.widget-view+json' in content['data']:
                        widget_views += 1
                    if kind == 'status' and content['execution_state'] == 'idle':
                        break
                else:
                    self.fail('Ordinary native Quiz kernel did not finish')
                self.assertEqual(errors, [])
                self.assertGreaterEqual(widget_views, 33)
                line = next(line for line in output.splitlines() if line.startswith('NATIVE_QUIZ_RESULT '))
                result = json.loads(line.removeprefix('NATIVE_QUIZ_RESULT '))
                self.assertEqual(result, {'quizzes': 33, 'locale': 'kk-Cyrl', 'correct': 198,
                                          'admitted': False, 'runtime_adapter_imported': False})
            finally:
                if client is not None:
                    client.stop_channels()
                manager.shutdown_kernel(now=True)


if __name__ == '__main__':
    unittest.main()
