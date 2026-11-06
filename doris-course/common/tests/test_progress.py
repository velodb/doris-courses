"""Learning-credit policy, durable concurrency and untrusted migration files."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from doris_labkit import progress as p
from doris_labkit.quiz import CourseQuiz
from test_quiz import QUIZ_YAML


def module(course='real-time-analytics', identity='module-1', **kwargs):
    return dict(course_slug=course, id=identity, required=True, content_version='a' * 64, source_ref='b' * 40, **kwargs)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'progress/progress.sqlite3'
        self.store = p.Store(self.path)
        self.module = module()
        self.key = ('real-time-analytics', 'module-1')
        self.modules = {self.key: self.module}

    def record(self):
        return self.store.records()[self.key]

    def test_explicit_marks_and_open_lab_precondition(self):
        self.assertEqual(self.store.records(), {})
        with self.assertRaises(p.ProgressError):
            self.store.mark(self.module, 'lab_declared_complete')
        self.store.mark(self.module, 'course_read')
        self.assertEqual(p.view(self.module, self.record())['parts'], 1)
        self.store.mark(self.module, 'lab_opened')
        self.assertEqual(p.view(self.module, self.record())['parts'], 1)
        self.store.mark(self.module, 'lab_declared_complete')
        self.store.mark(self.module, 'course_read', False)
        self.assertEqual(p.view(self.module, self.record())['parts'], 1)
        with self.assertRaises(p.ProgressError):
            self.store.mark(self.module, 'quiz_passed')

    def test_exact_threshold_and_preserved_pass_and_best_fraction(self):
        self.store.quiz(self.module, 2, 3, 67)  # display rounds to 67%, but not passed
        self.assertFalse(self.record()['quiz_passed'])
        self.store.quiz(self.module, 7, 10, 70)
        self.assertTrue(self.record()['quiz_passed'])
        self.store.quiz(self.module, 1, 10, 80)
        self.assertEqual((self.record()['best_correct'], self.record()['best_total']), (7, 10))
        self.assertTrue(self.record()['quiz_passed'])
        self.store.quiz(self.module, 3, 3, 100)
        self.store.quiz(self.module, 9, 10, 80)
        self.assertEqual((self.record()['best_correct'], self.record()['best_total']), (3, 3))

    def test_course_summary_uses_eight_of_thirty_and_excludes_optional(self):
        modules, records = [], {}
        for index in range(10):
            m = module(identity=f'module-{index + 1}')
            modules.append(m)
            r = p.empty(m)
            if index < 2:
                r.update(course_read=True, lab_opened=True, lab_declared_complete=True, quiz_passed=True)
            elif index == 2:
                r.update(course_read=True, lab_opened=True, lab_declared_complete=True)
            records[(m['course_slug'], m['id'])] = r
        optional = module(identity='extension-1')
        optional['required'] = False
        modules.append(optional)
        self.assertEqual(p.summary(modules, records), {'parts': 8, 'total': 30, 'percent': 26.7, 'complete': 2, 'modules': 10})
        self.assertIsNone(p.summary([optional], {})['percent'])
        self.assertFalse(p.view(module())['complete'])

    def test_two_store_instances_preserve_independent_concurrent_writes(self):
        def operation(index):
            store = p.Store(self.path)
            if index % 2:
                store.mark(self.module, 'course_read')
            else:
                store.quiz(self.module, index % 11, 10, 80)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(operation, range(40)))
        r = p.Store(self.path).records()[self.key]
        self.assertTrue(r['course_read'])
        self.assertTrue(r['quiz_passed'])
        self.assertEqual(r['best_correct'], 10)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_new_environment_export_preview_union_and_idempotent_import(self):
        self.store.mark(self.module, 'course_read')
        self.store.mark(self.module, 'lab_opened')
        self.store.quiz(self.module, 9, 10, 80)
        document = p.validate_document(self.store.export())
        other = p.Store(Path(self.temp.name) / 'second/progress.sqlite3')
        self.assertEqual(other.records(), {})
        preview = other.preview(document, self.modules)
        self.assertTrue(preview[0]['matched'])
        self.assertEqual(other.records(), {})
        other.import_document(document, self.modules)
        before = other.records()
        other.import_document(document, self.modules)
        self.assertEqual(before, other.records())
        other.mark(self.module, 'lab_declared_complete')
        other.quiz(self.module, 10, 10, 80)
        other.import_document(document, self.modules)
        self.assertTrue(other.records()[self.key]['lab_declared_complete'])
        self.assertEqual(other.records()[self.key]['best_correct'], 10)
        other.mark(self.module, 'course_read', False)
        self.assertFalse(other.records()[self.key]['course_read'])
        other.import_document(document, self.modules)
        self.assertTrue(other.records()[self.key]['course_read'])

    def test_content_update_new_and_unknown_identity_do_not_reinterpret_credit(self):
        self.store.quiz(self.module, 10, 10, 80)
        document = p.validate_document(self.store.export())
        updated = {**self.module, 'content_version': 'c' * 64, 'source_ref': 'd' * 40}
        unknown = {**document['records'][0], 'course_slug': 'other-course'}
        document['records'].append(unknown)
        registry = {self.key: updated, ('real-time-analytics', 'module-2'): module(identity='module-2')}
        preview = self.store.preview(document, registry)
        self.assertTrue(preview[0]['content_updated'])
        self.assertFalse(preview[1]['matched'])
        self.assertEqual(self.store.import_document(document, registry), 1)
        self.assertTrue(p.view(updated, self.record())['content_updated'])
        self.assertTrue(self.record()['quiz_passed'])
        self.assertEqual(len(self.store.records()), 1)
        self.assertEqual(p.view(registry[('real-time-analytics', 'module-2')])['parts'], 0)
        self.assertFalse(self.store.preview(document, {})[0]['matched'])

    def test_registry_counts_only_selected_published_modules_using_manifest_identity(self):
        plan = {'course_ref': 'a' * 40, 'courses': [{'slug': 'permanent', 'course_dir': 'directory', 'status': 'published',
                'modules': [{'id': 'stable-id', 'status': 'published'}, {'id': 'old', 'status': 'retired'}, {'id': 'future', 'status': 'planned'}]}]}
        self.assertEqual(set(p.registry(plan)), {('permanent', 'stable-id')})
        plan['courses'][0]['status'] = 'draft'
        self.assertEqual(p.registry(plan), {})

    def test_invalid_files_rejected_before_any_partial_import(self):
        self.store.mark(self.module, 'course_read')
        valid = p.validate_document(self.store.export())
        before = self.store.records()
        changes = [('course_slug', '../other'), ('module_id', 1), ('course_read', 1), ('best_correct', True),
                   ('best_total', -1), ('best_total', 10001), ('content_version', 'oops'), ('updated_at', '2026-01-01'),
                   ('lab_declared_complete', True), ('quiz_passed', True), ('account', 'somebody')]
        for key, value in changes:
            doc = copy.deepcopy(valid)
            doc['records'].append({**doc['records'][0], 'module_id': 'module-2', key: value})
            with self.subTest(key=key), self.assertRaises(p.ProgressError):
                self.store.import_document(doc, self.modules)
            self.assertEqual(before, self.store.records())
        for field, value in [('schema_version', 2), ('schema_version', True), ('records', {}), ('exported_at', None), ('identity', 'x')]:
            doc = {**valid, field: value}
            with self.subTest(field=field), self.assertRaises(p.ProgressError):
                p.validate_document(json.dumps(doc).encode())
        for raw in (b'[]', b'{"schema_version":1,"schema_version":1}', b'\xff', b' ' * (p.MAX_BYTES + 1), b'[' * 2000):
            with self.assertRaises(p.ProgressError):
                p.validate_document(raw)
        duplicate = copy.deepcopy(valid)
        duplicate['records'] *= 2
        with self.assertRaises(p.ProgressError):
            self.store.import_document(duplicate, self.modules)

    def test_transaction_failure_rolls_back_whole_import(self):
        self.store.mark(self.module, 'course_read')
        document = p.validate_document(self.store.export())
        document['records'].append({**document['records'][0], 'module_id': 'module-2'})
        modules = {**self.modules, ('real-time-analytics', 'module-2'): module(identity='module-2')}
        other = p.Store(Path(self.temp.name) / 'rollback/progress.sqlite3')
        write = other._write
        calls = []
        def fail_second(connection, record):
            calls.append(record)
            if len(calls) == 2:
                raise sqlite3.OperationalError('injected disk failure')
            return write(connection, record)
        with mock.patch.object(other, '_write', side_effect=fail_second), self.assertRaises(sqlite3.OperationalError):
            other.import_document(document, modules)
        self.assertEqual(other.records(), {})

    def test_widget_threshold_full_submission_and_unsaved_retry(self):
        path = Path(self.temp.name) / 'quiz.yaml'
        path.write_text(QUIZ_YAML + '\npassing_score_percent: 50\n')
        quiz = CourseQuiz.from_yaml(path)
        self.assertEqual(quiz.passing_score_percent, 50)
        with mock.patch.object(p, 'save_quiz_from_environment') as save:
            quiz._render_results()
            save.assert_not_called()
            quiz._answers = {q.question_id: q.answer for q in quiz.questions}
            save.return_value = {**p.empty(self.module), 'quiz_passed': True}
            quiz._render_results()
            save.assert_called_once_with(path.resolve(), 2, 2, 50)
            self.assertIn('Progress saved', quiz._root.children[0].children[0].value)
            save.side_effect = OSError('private path should not appear')
            quiz._render_results()
            self.assertIn('Progress was not saved', quiz._root.children[0].children[0].value)
            self.assertNotIn('private path', quiz._root.children[0].children[0].value)
            save.side_effect = None
            quiz._root.children[0].children[1].children[2].click()
            self.assertIn('Progress saved', quiz._root.children[0].children[0].value)

    def test_local_quiz_does_not_create_progress_when_disabled(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(p.save_quiz_from_environment('/does/not/exist', 1, 1, 80))
        self.assertFalse(self.path.exists())

    def test_quiz_path_binding_and_optional_quiz_do_not_cross_module_identities(self):
        source = Path(self.temp.name) / 'source'
        plan = {'course_ref': 'b' * 40, 'courses': [{'slug': 'real-time-analytics', 'course_dir': 'rta', 'status': 'published',
                'modules': [{'id': 'module-1', 'quiz': 'quiz.yaml', 'status': 'published', 'content_version': 'a' * 64}],
                'optional': [{'path': 'optional.ipynb', 'quiz': 'optional.yaml'}]}]}
        plan_path = Path(self.temp.name) / 'plan.json'
        plan_path.write_text(json.dumps(plan))
        environment = {'DORIS_WORKSHOP_PROGRESS_DB': str(self.path), 'DORIS_WORKSHOP_PROGRESS_PLAN': str(plan_path),
                       'DORIS_WORKSHOP_PROGRESS_SOURCE': str(source)}
        with mock.patch.dict(os.environ, environment):
            result = p.save_quiz_from_environment(source / 'rta/quiz.yaml', 10, 10, 80)
            self.assertTrue(result['quiz_passed'])
            self.assertEqual(p.save_quiz_from_environment(source / 'rta/optional.yaml', 10, 10, 80), {'optional': True})
            with self.assertRaises(p.ProgressError):
                p.save_quiz_from_environment(source / 'other/quiz.yaml', 10, 10, 80)
        self.assertEqual(set(self.store.records()), {self.key})


if __name__ == '__main__':
    unittest.main()
