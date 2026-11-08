"""One-time migration safety plus portable checks for the editable source tree."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate_course_translations as migration


ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / 'doris-course/kk-Cyrl'
PACKAGE = Path(os.environ.get('DORIS_MIGRATION_PACKAGE',
    ROOT / 'plan-doc/multilingual/evidence/2026-11-08-ml09/package-final'))


class MigrationSafetyTests(unittest.TestCase):
    def test_existing_source_is_never_overwritten_even_without_a_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'existing'
            out.mkdir()
            (out / 'lesson.md').write_text('A maintainer edit.\n')
            with self.assertRaises(FileExistsError):
                migration.migrate(Path(tmp) / 'absent-package', out)
            self.assertEqual((out / 'lesson.md').read_text(), 'A maintainer edit.\n')
            self.assertEqual([p.name for p in out.iterdir()], ['lesson.md'])

    def test_saved_alias_survives_later_independent_heading_edits(self):
        text = migration.anchored('## Environment\n\nText.\n', '## Орта\n\nМәтін.\n',
                                  'unit-u.example', migration.Counter())
        # A normal author can change this heading without loading any English
        # source, source map, translation unit, overlay or historical package.
        edited = text.replace('## Орта', '## Өзгертілген тақырып')
        self.assertIn('environment', migration.fragment_ids(edited))
        self.assertIn('unit-u.example', migration.fragment_ids(edited))

    def test_duplicate_heading_aliases_keep_the_original_file_order(self):
        counts = migration.Counter()
        first = migration.anchored('# Quiz\n', '# Тест\n', 'unit-u.one', counts)
        second = migration.anchored('# Quiz\n', '# Тест\n', 'unit-u.two', counts)
        self.assertIn('<a id="quiz"></a>', first)
        self.assertIn('<a id="quiz-1"></a>', second)

    def test_relocated_targets_keep_selected_languages_and_explicit_english_ai(self):
        source = 'doris-course/02-data-warehousing/level2/module11-bi-and-ai/course.md'
        self.assertEqual(migration.relocated_url('../../../01-real-time-analytics/README.md#environment', source),
                         '../../../01-real-time-analytics/README.md#environment')
        self.assertEqual(migration.relocated_url('../../../04-ai-integrations/level1/module01-doris-mcp/course.md', source),
                         '../../../../04-ai-integrations/level1/module01-doris-mcp/course.md')
        readme = 'doris-course/01-real-time-analytics/README.md'
        self.assertEqual(migration.relocated_url('../common/README.md', readme), '../../common/README.md')
        remote = 'https://example.test/doris-course/events.csv?x=1#part'
        self.assertEqual(migration.relocated_url(remote, readme), remote)

    def test_only_real_links_are_relocated_not_fenced_or_inline_examples(self):
        source = 'doris-course/02-data-warehousing/level2/module11-bi-and-ai/course.md'
        url = '../../../04-ai-integrations/README.md'
        value = f'[AI]({url})\n\n`[example]({url})`\n\n```md\n[example]({url})\n```\n'
        result = migration.relocate_links(value, source)
        self.assertIn('[AI](../../../../04-ai-integrations/README.md)', result)
        self.assertIn(f'`[example]({url})`', result)
        self.assertIn(f'```md\n[example]({url})\n```', result)
        self.assertEqual(migration.links(result), ['../../../../04-ai-integrations/README.md'])

    def test_html_image_paths_remain_native_course_relative_paths(self):
        source = 'doris-course/03-observability/level1/module03-data-visualization/lab03.ipynb'
        text = '<img src="images/failed-checkout-trace.png" alt="Қате" style="max-width:100%">'
        self.assertEqual(migration.relocate_links(text, source), text)
        self.assertEqual(migration.links(text), ['images/failed-checkout-trace.png'])


class NativeCourseSourceTests(unittest.TestCase):
    """These require no old translation package and allow normal author edits."""

    def test_catalog_and_course_setup_are_independent_native_yaml(self):
        catalog = yaml.safe_load((NATIVE / 'catalog.yaml').read_text())
        self.assertEqual(catalog['courses'], [f'{migration.PREFIX}/{c}/course.yaml' for c in migration.COURSES])
        self.assertEqual(catalog['programs'], [])
        self.assertFalse((NATIVE / '04-ai-integrations').exists())
        self.assertFalse((NATIVE / 'common').exists())
        for course in migration.COURSES:
            root = NATIVE / course
            metadata = yaml.safe_load((root / 'course.yaml').read_text())
            self.assertEqual(metadata['language'], 'kk-Cyrl')
            self.assertTrue((root / '../../common/pyproject.toml').is_file())
            self.assertIn('-e ../../common\n', (root / 'requirements.txt').read_text())
            for module in metadata['modules']:
                for key in ('course', 'lab', 'quiz'):
                    self.assertTrue((root / module[key]).is_file(), module[key])
                for optional in module.get('optional', []):
                    self.assertTrue((root / optional['path']).is_file(), optional['path'])

    def test_ui_is_a_plain_editable_message_map(self):
        messages = yaml.safe_load((NATIVE / 'ui.yaml').read_text())
        self.assertTrue(messages)
        self.assertTrue(all(isinstance(key, str) and isinstance(value, str) for key, value in messages.items()))
        self.assertNotIn('units', messages)
        self.assertNotIn('schema_version', messages)
        self.assertNotIn('messages', messages)
        self.assertTrue(any('{correct}' in value for value in messages.values()))
        self.assertTrue(any('{total}' in value for value in messages.values()))

    def test_native_quiz_banks_load_without_any_translation_overlay(self):
        sys.path.insert(0, str(ROOT / 'doris-course/common'))
        from doris_labkit.quizbank import load_quiz
        banks = sorted(NATIVE.rglob('quiz*.yaml'))
        self.assertEqual(len(banks), 33)
        for path in banks:
            with self.subTest(path=path.relative_to(NATIVE)):
                quiz = load_quiz(path, localized=True)
                self.assertEqual(len(quiz.questions), 6)
                for question in quiz.questions:
                    ids = [option.option_id for option in question.options]
                    self.assertEqual(len(ids), 4)
                    self.assertEqual(len(set(ids)), 4)
                    self.assertIn(question.answer, ids)


class CompleteHistoricalMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PACKAGE / 'tree/package.v2.json').is_file():
            raise unittest.SkipTest('Set DORIS_MIGRATION_PACKAGE to replay the historical one-time migration')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temporary.name) / 'first'
        cls.report = migration.migrate(PACKAGE, cls.output)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temporary'):
            cls.temporary.cleanup()

    def test_all_tracked_sources_and_every_course_activity_are_migrated(self):
        expected = {'tracked_course_files': 394, 'markdown_readers': 37, 'notebooks': 72,
                    'unchanged_code_cells': 547, 'quiz_banks': 33, 'unchanged_images': 127,
                    'primary_modules': 32, 'optional_assets': 9, 'historical_unit_anchors': 1782,
                    'ui_messages': 231}
        for key, count in expected.items():
            self.assertEqual(self.report[key], count, key)
        self.assertTrue(self.report['passed'])
        self.assertGreater(self.report['relative_links_and_fragments'], 600)
        self.assertGreater(self.report['saved_english_heading_aliases'], 1000)
        self.assertTrue((self.output / '01-real-time-analytics/.gitignore').is_file())

    def test_same_package_migrates_deterministically_to_a_second_new_directory(self):
        other = Path(self.temporary.name) / 'second'
        migration.migrate(PACKAGE, other)
        def files(root):
            return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob('*') if p.is_file()}
        self.assertEqual(files(self.output), files(other))

    def test_read_only_audit_detects_changed_code_and_quiz_answers(self):
        clone = Path(self.temporary.name) / 'tampered'
        shutil.copytree(self.output, clone)
        notebook = next(clone.rglob('lab01*.ipynb'))
        original = notebook.read_bytes()
        document = json.loads(original)
        next(c for c in document['cells'] if c['cell_type'] == 'code')['source'] = ['raise RuntimeError("changed")']
        notebook.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'parity'):
            migration.audit_source_tree(clone, PACKAGE)
        notebook.write_bytes(original)
        quiz = next(clone.rglob('quiz*.yaml'))
        document = yaml.safe_load(quiz.read_text())
        question = document['questions'][0]
        question['answer'] = next(o['id'] for o in question['options'] if o['id'] != question['answer'])
        quiz.write_text(yaml.safe_dump(document, allow_unicode=True))
        with self.assertRaisesRegex(ValueError, 'parity'):
            migration.audit_source_tree(clone, PACKAGE)

    def test_failed_migration_never_publishes_a_partial_source_tree(self):
        output = Path(self.temporary.name) / 'failed'
        with patch.object(migration, 'audit_source_tree', side_effect=ValueError('broken relative image')):
            with self.assertRaisesRegex(ValueError, 'broken relative image'):
                migration.migrate(PACKAGE, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(output.parent.glob('.course-migration-*')), [])


if __name__ == '__main__':
    unittest.main()
