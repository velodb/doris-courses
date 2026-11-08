"""Stable presentations, fixed graders, concurrent receipts and reversible migration."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import uuid

from doris_labkit import progress as p
from doris_labkit.messages import Messages
from doris_labkit.quiz import CourseQuiz
from doris_labkit.quizbank import parse_quiz, display_order
from doris_labkit.quizsession import Presentation, QuizConflict, digest
from test_quiz import QUIZ_YAML
from test_progress import module
import yaml


def presentation(locale='en', *, revision='meaning.1', bank=None, threshold=80):
    bank = bank or parse_quiz(yaml.safe_load(QUIZ_YAML))
    bank = replace(bank, passing_score_percent=threshold)
    descriptor = {'id': 'quiz.example', 'semantic_revision': revision, 'passing_score_percent': threshold,
        'questions': [{'id': q.question_id, 'option_ids': [o.option_id for o in q.options], 'answer': q.answer}
                      for q in bank.questions],
        'presentations': [{'locale': locale, 'presentation_revision': digest({'text': repr(bank), 'locale': locale})}]}
    return Presentation.admitted(bank, descriptor, locale=locale, policy_revision='policy.1', source_digest=digest(repr(bank)))


def messages(locale):
    source = Messages()
    return source if locale == 'en' else Messages(locale, messages={k: 'Ә ' + v for k, v in source.source.items()})


def widget(presentation, source_path=None):
    bank = presentation.quiz
    return CourseQuiz(title=bank.title, description=bank.description, questions=bank.questions,
        passing_score_percent=bank.passing_score_percent, presentation=presentation,
        messages=messages(presentation.locale), source_path=source_path)


class BoundQuizTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private/progress.sqlite3'
        self.store = p.Store(self.path)
        self.module = module()
        self.key = self.module['course_slug'], self.module['id']
        self.modules = {self.key: self.module}
        self.en = presentation()
        translated = replace(self.en.quiz, title='Синтетикалық тақырып',
            questions=tuple(replace(q, prompt='Ә ' + q.prompt,
                options=tuple(replace(o, text='Ә ' + o.text) for o in reversed(q.options))) for q in reversed(self.en.quiz.questions)))
        self.kk = presentation('kk-Cyrl', bank=translated)

    def request(self, view=None, *, attempt_id=None):
        view = view or self.en
        return {'schema_version': 2, 'kind': 'doris-quiz-submission', 'attempt_id': attempt_id or uuid.uuid4().hex,
            'release_id': self.store.register_quiz(self.module, view), 'binding': view.binding(),
            'answers': {q.question_id: q.answer for q in view.quiz.questions}}

    def test_switch_keeps_drafts_confirmed_answers_position_order_and_attempt(self):
        quiz = widget(self.en)
        quiz._render_question()
        first = quiz.questions[0]
        old_choices = quiz._root.children[0].children[1]
        old_choices.value = first.answer
        quiz._root.children[0].children[3].children[1].click()
        quiz._root.children[0].children[3].children[2].click()
        second = quiz.questions[1]
        stale_choices = quiz._root.children[0].children[1]
        stale_choices.value = second.answer
        before = (quiz._current, dict(quiz._answers), dict(quiz._drafts), quiz._attempt_id)
        order = [(q.question_id, [o.option_id for o in q.options]) for q in quiz.questions]
        quiz.switch_presentation(self.kk, messages=messages('kk-Cyrl'))
        self.assertEqual(before, (quiz._current, quiz._answers, quiz._drafts, quiz._attempt_id))
        self.assertEqual(order, [(q.question_id, [o.option_id for o in q.options]) for q in quiz.questions])
        self.assertEqual(quiz._root.children[0].children[1].value, second.answer)
        self.assertIn('Ә ', quiz._root.children[0].children[0].value)
        stale_choices.value = next(o.option_id for o in second.options if o.option_id != second.answer)
        self.assertEqual(quiz._drafts[second.question_id], second.answer)
        self.assertFalse(self.path.exists())

    def test_no_equivalence_no_silent_switch_or_legacy_translation(self):
        quiz = widget(self.en)
        quiz._render_question()
        for target in (replace(self.kk, semantic_revision='meaning.2'), replace(self.kk, policy_revision='policy.2'),
                       presentation('kk-Cyrl', threshold=50)):
            with self.assertRaises(QuizConflict):
                quiz.switch_presentation(target, messages=messages('kk-Cyrl'))
            self.assertEqual(quiz.locale, 'en')
        legacy = widget(Presentation.legacy(self.en.quiz, 'a' * 64))
        with self.assertRaises(QuizConflict):
            legacy.switch_presentation(self.kk, messages=messages('kk-Cyrl'))

    def test_submission_retry_and_many_tabs_are_one_immutable_attempt(self):
        request = self.request(self.kk)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: p.Store(self.path).submit_quiz(self.module, request), range(20)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(len(self.store.attempts()), 1)
        self.assertEqual(results[0]['locale'], 'kk-Cyrl')
        self.assertEqual(results[0]['semantic_revision'], 'meaning.1')
        self.assertEqual(results[0]['correct'], 2)
        before = self.store.records()
        # Locale cannot be used as another score namespace or to rewrite history.
        for changed in ({**request, 'binding': self.en.binding()},
                        {**request, 'answers': {q.question_id: q.options[0].option_id for q in self.en.quiz.questions}}):
            if changed == request:
                continue
            with self.assertRaises(QuizConflict):
                self.store.submit_quiz(self.module, changed)
        self.assertEqual(before, self.store.records())
        with sqlite3.connect(self.path) as db:
            self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(progress)')], ['course', 'module', 'record'])
            self.assertEqual(db.execute('SELECT count(*) FROM progress').fetchone()[0], 1)

    def test_old_release_grades_its_original_answers_after_new_meaning_installed(self):
        old_request = self.request()
        changed = replace(self.en.quiz, questions=tuple(replace(q, answer=next(o.option_id for o in q.options if o.option_id != q.answer))
            for q in self.en.quiz.questions))
        bad = replace(self.en, quiz=changed)
        with self.assertRaises(QuizConflict):
            self.store.register_quiz(self.module, bad)
        new = presentation(bank=changed, revision='meaning.2')
        new_request = self.request(new)
        old = self.store.submit_quiz(self.module, old_request)
        self.assertTrue(old['passed'])
        new_request['answers'] = old_request['answers']
        failed = self.store.submit_quiz(self.module, new_request)
        self.assertFalse(failed['passed'])
        self.assertEqual(failed['correct'], 0)
        self.assertTrue(self.store.records()[self.key]['quiz_passed'])
        restarted = p.Store(self.path)
        self.assertEqual(restarted.submit_quiz(self.module, old_request), old)
        self.assertEqual(len(restarted.attempts()), 2)

    def test_forged_binding_missing_release_and_supplied_scores_rejected(self):
        request = self.request()
        for bad in ({**request, 'release_id': 'f' * 32}, {**request, 'binding': {**request['binding'], 'locale': 'xx'}},
                    {**request, 'correct': 999}, {**request, 'answers': {}},
                    {**request, 'answers': {**request['answers'], 'fake': 'fe'}}):
            with self.assertRaises(QuizConflict):
                self.store.submit_quiz(self.module, bad)
        self.assertEqual(self.store.attempts(), [])
        self.assertEqual(self.store.records(), {})
        self.assertNotIn('source_digest', request['binding'])

    def test_exact_threshold_is_server_side_and_pass_survives_failed_retry(self):
        questions = self.en.quiz.questions
        bank = replace(self.en.quiz, questions=questions + (replace(questions[0], question_id='third'),))
        view = presentation(bank=bank, threshold=67)
        request = self.request(view)
        request['answers']['third'] = next(o.option_id for o in bank.questions[-1].options if o.option_id != bank.questions[-1].answer)
        attempt = self.store.submit_quiz(self.module, request)
        self.assertEqual(attempt['correct'], 2)
        self.assertFalse(attempt['passed'])
        self.store.submit_quiz(self.module, self.request(view))
        self.store.submit_quiz(self.module, {**request, 'attempt_id': uuid.uuid4().hex})
        self.assertTrue(self.store.records()[self.key]['quiz_passed'])

    def test_results_switch_and_retry_saving_keep_original_submission_and_no_side_effect(self):
        quiz = widget(self.en, '/fixture/en.yaml')
        quiz._answers = {q.question_id: q.answer for q in quiz.questions}
        with mock.patch.object(p, 'save_submission_from_environment', side_effect=OSError('private')) as save:
            quiz._render_results()
            original = save.call_args
            self.assertIn('Retry saving', str(quiz._root.children))
            quiz.switch_presentation(self.kk, messages=messages('kk-Cyrl'), source_path='/fixture/kk.yaml')
            self.assertEqual(save.call_count, 1)
            save.side_effect = None
            save.return_value = {'quiz_passed': True}
            quiz._root.children[0].children[1].children[2].click()
            self.assertEqual(save.call_args, original)
            quiz._render_results()
            self.assertEqual(save.call_count, 2)
            self.assertEqual(quiz._submission[1].locale, 'en')
            quiz._root.children[0].children[1].children[0].click()
            self.assertTrue(quiz._root.children[0].children[1].disabled)

    def test_failed_submission_transaction_has_no_attempt_or_credit(self):
        request = self.request()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER fail_attempt BEFORE INSERT ON quiz_attempts BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.Error):
            self.store.submit_quiz(self.module, request)
        self.assertEqual(self.store.records(), {})
        self.assertEqual(self.store.attempts(), [])

    def test_v1_database_upgrade_preserves_exact_record_and_unknown_history(self):
        original = p.empty(self.module)
        original.update(quiz_passed=True, best_correct=9, best_total=10)
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE progress (course TEXT, module TEXT, record TEXT NOT NULL, PRIMARY KEY(course,module))')
            db.execute('INSERT INTO progress VALUES (?,?,?)', (*self.key, json.dumps(original)))
        self.assertEqual(self.store.records()[self.key], original)
        exported = p.validate_document(self.store.export())
        self.assertEqual(exported['legacy_records'], [original])
        self.assertEqual(exported['attempts'], [])
        self.assertNotIn('locale', exported['legacy_records'][0])
        backup = next(self.path.parent.glob('*.before-upgrade-*.backup'))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.store.submit_quiz(self.module, self.request())
        self.store.restore_backup(backup)
        self.assertEqual(self.store.records()[self.key], original)
        self.assertEqual(self.store.attempts(), [])

    def test_v1_v2_preview_import_projection_idempotence_and_rollback(self):
        self.store.submit_quiz(self.module, self.request(self.kk))
        self.store.mark(self.module, 'course_read')
        doc = p.validate_document(self.store.export())
        other = p.Store(Path(self.temp.name) / 'other/progress.sqlite3')
        before = other.records()
        rows = other.preview(doc, self.modules)
        self.assertEqual(rows[0]['attempt_count'], 1)
        self.assertEqual(other.records(), before)
        other.import_document(doc, self.modules)
        backup = other.last_backup
        records, attempts = other.records(), other.attempts()
        other.import_document(doc, self.modules)
        self.assertEqual(other.records(), records)
        self.assertEqual(other.attempts(), attempts)
        with self.assertWarnsRegex(UserWarning, 'omits attempt'):
            v1 = p.validate_document(other.export(version=1))
        self.assertEqual(v1['schema_version'], 1)
        self.assertEqual(set(v1['records'][0]), p.FIELDS)
        other.restore_backup(backup)
        self.assertEqual(other.records(), before)
        self.assertEqual(other.attempts(), [])

    def test_conflicting_or_corrupt_import_rolls_back_records_and_all_history(self):
        self.store.submit_quiz(self.module, self.request())
        doc = p.validate_document(self.store.export())
        broken = copy.deepcopy(doc)
        broken['attempts'][0]['locale'] = 'kk-Cyrl'
        with self.assertRaises(p.ProgressError):
            self.store.import_document(broken, self.modules)
        for field, value in [('passed', False), ('answers', {}), ('trust', 'website-certified'), ('semantic_revision', None)]:
            invalid = copy.deepcopy(doc)
            invalid['attempts'][0][field] = value
            with self.subTest(field=field), self.assertRaises(p.ProgressError):
                self.store.import_document(invalid, self.modules)
        current = p.validate_document(self.store.export())
        self.assertEqual(current['records'], doc['records'])
        self.assertEqual(current['attempts'], doc['attempts'])
        other = p.Store(Path(self.temp.name) / 'empty/progress.sqlite3')
        self.assertEqual(other.import_document(doc, {}), 0)
        self.assertEqual(other.attempts(), [])

    def test_same_presentation_can_register_different_session_orders(self):
        first = self.request(self.en)
        ordered = self.en.in_order_of(self.kk)
        second = self.request(ordered)
        self.assertEqual(first['release_id'], second['release_id'])

    def test_concurrent_switch_and_results_freeze_one_submission(self):
        quiz = widget(self.en, '/fixture/quiz.yaml')
        quiz._answers = {q.question_id: q.answer for q in quiz.questions}
        def save(path, view, identity, answers):
            request = self.request(view, attempt_id=identity)
            request['answers'] = answers
            attempt = self.store.submit_quiz(self.module, request)
            return {**self.store.records()[self.key], 'attempt': attempt}
        def operate(index):
            if index % 3 == 0:
                quiz._render_results()
            else:
                target = self.en if index % 2 else self.kk
                quiz.switch_presentation(target, messages=messages(target.locale))
        with mock.patch.object(p, 'save_submission_from_environment', side_effect=save):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(operate, range(30)))
        attempts = self.store.attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]['locale'], quiz._submission[1].locale)

    def test_portable_fixtures_keep_unknown_legacy_and_never_become_receipts(self):
        fixtures = Path(__file__).with_name('fixtures')
        v1 = p.validate_document((fixtures / 'progress-v1.json').read_bytes())
        self.store.import_document(v1, self.modules)
        self.assertEqual(self.store.attempts(), [])
        v2 = p.validate_document((fixtures / 'progress-v2.json').read_bytes())
        self.assertEqual(self.store.import_document(v2, self.modules), 1)
        self.assertTrue(self.store.records()[self.key]['quiz_passed'])
        request = self.request(attempt_id=v2['attempts'][0]['attempt_id'])
        with self.assertRaises(QuizConflict):
            self.store.submit_quiz(self.module, request)
        self.assertEqual(self.store.attempts(), v2['attempts'])
        doc = p.validate_document(self.store.export())
        self.assertEqual(doc['legacy_records'], v1['records'])

    def test_import_storage_failure_rolls_back_history_before_progress_write(self):
        self.store.submit_quiz(self.module, self.request())
        document = p.validate_document(self.store.export())
        other = p.Store(Path(self.temp.name) / 'broken/progress.sqlite3')
        other.records()
        with sqlite3.connect(other.path) as db:
            db.execute("CREATE TRIGGER fail_import BEFORE INSERT ON progress BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.Error):
            other.import_document(document, self.modules)
        self.assertEqual(other.records(), {})
        self.assertEqual(other.attempts(), [])

    def test_v2_installed_adapter_resolves_only_pinned_selected_paths(self):
        source = Path(self.temp.name) / 'source'
        source.mkdir()
        path = source / 'quiz.yaml'
        path.write_text(QUIZ_YAML)
        bank = parse_quiz(yaml.safe_load(QUIZ_YAML))
        view = presentation('kk-Cyrl', bank=bank)
        view = replace(view, source_digest=hashlib.sha256(path.read_bytes()).hexdigest())
        descriptor = {'id': view.quiz_id, 'semantic_revision': view.semantic_revision,
            'passing_score_percent': bank.passing_score_percent,
            'questions': [{'id': q.question_id, 'option_ids': [o.option_id for o in q.options], 'answer': q.answer} for q in bank.questions],
            'presentations': [{'locale': view.locale, 'presentation_revision': view.presentation_revision}]}
        selected = {**self.module, 'quiz': 'canonical.yaml', 'status': 'published', 'quiz_binding': descriptor,
            'policy_revision': 'policy.1', 'quiz_presentations': [{'path': 'quiz.yaml', 'locale': view.locale, 'source_digest': view.source_digest}]}
        plan = {'course_ref': 'b' * 40, 'courses': [{'slug': self.module['course_slug'], 'course_dir': '.',
            'status': 'published', 'modules': [selected]}]}
        plan_path = source / 'plan.json'
        plan_path.write_text(json.dumps(plan))
        environment = {'DORIS_WORKSHOP_PROGRESS_DB': str(self.path), 'DORIS_WORKSHOP_PROGRESS_PLAN': str(plan_path),
            'DORIS_WORKSHOP_PROGRESS_SOURCE': str(source)}
        answers = {q.question_id: q.answer for q in bank.questions}
        with mock.patch.dict(os.environ, environment):
            result = p.save_submission_from_environment(path, view, uuid.uuid4().hex, answers)
            self.assertEqual(result['attempt']['locale'], 'kk-Cyrl')
            path.write_text(QUIZ_YAML.replace('Which node', 'What node'))
            with self.assertRaises(QuizConflict):
                p.save_submission_from_environment(path, view, uuid.uuid4().hex, answers)

    def test_localized_optional_quiz_keeps_main_credit_and_checks_its_frozen_bytes(self):
        self.store.submit_quiz(self.module, self.request())
        before_records, before_attempts = self.store.records(), self.store.attempts()
        source = Path(self.temp.name) / 'optional-source'; source.mkdir()
        path = source / 'translated.yaml'; path.write_text(QUIZ_YAML)
        bank = parse_quiz(yaml.safe_load(QUIZ_YAML))
        view = replace(presentation('kk-Cyrl', bank=bank), source_digest=hashlib.sha256(path.read_bytes()).hexdigest())
        descriptor = {'id': view.quiz_id, 'semantic_revision': view.semantic_revision, 'passing_score_percent': bank.passing_score_percent,
            'questions': [{'id': q.question_id, 'option_ids': [o.option_id for o in q.options], 'answer': q.answer} for q in bank.questions],
            'presentations': [{'locale': view.locale, 'presentation_revision': view.presentation_revision}]}
        part = {'path': 'optional.ipynb', 'quiz': 'canonical.yaml', 'quiz_binding': descriptor, 'policy_revision': 'policy.1',
                'quiz_presentations': [{'path': path.name, 'locale': view.locale, 'source_digest': view.source_digest}]}
        plan = {'course_ref': 'b'*40, 'courses': [{'slug': self.module['course_slug'], 'course_dir': '.', 'status': 'published', 'modules': [], 'optional': [part]}]}
        plan_path = source / 'plan.json'; plan_path.write_text(json.dumps(plan))
        env = {'DORIS_WORKSHOP_PROGRESS_DB': str(self.path), 'DORIS_WORKSHOP_PROGRESS_PLAN': str(plan_path), 'DORIS_WORKSHOP_PROGRESS_SOURCE': str(source)}
        with mock.patch.dict(os.environ, env):
            answers = {q.question_id: q.answer for q in bank.questions}
            self.assertEqual(p.save_submission_from_environment(path, view, uuid.uuid4().hex, answers), {'optional': True})
            path.write_text(QUIZ_YAML.replace('Which node', 'What node'))
            with self.assertRaises(QuizConflict):
                p.save_submission_from_environment(path, view, uuid.uuid4().hex, answers)
        self.assertEqual(self.store.records(), before_records)
        self.assertEqual(self.store.attempts(), before_attempts)


if __name__ == '__main__':
    unittest.main()
