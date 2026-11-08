"""Independent authoring is effective without overlays, source units or reviews."""
import copy
import tempfile
from pathlib import Path
import unittest

from test_course_files_contract import Source, EN, KK
import course_files_producer as producer
import content_files_contract as contract
import content_producer as legacy


class CourseFileProducerTests(unittest.TestCase):
    def build(self, source):
        files, package = producer.build_tree(source, ['example-complete'], ['en', 'kk-Cyrl'])
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary) / 'tree'
            legacy.write_tree(files, tree)
            contract.validate_package(tree)
        return files, package

    def test_course_source_has_no_overlay_or_source_map_dependency(self):
        source = Source()
        self.assertFalse(any('overlay' in p or 'source-map' in p for p in source.paths()))
        files, package = self.build(source)
        self.assertNotIn('translations', package)
        self.assertNotIn('source_units', package)
        self.assertEqual(package['course_sources']['kk-Cyrl']['contract-example'], KK)
        self.assertEqual(files['rendered/kk-Cyrl/' + EN + '/lesson.md'], source.files[KK + '/lesson.md'])

    def test_english_only_prose_edit_does_not_stale_or_change_kazakh_payload(self):
        source = Source(); before_files, before = self.build(source)
        source.files[EN + '/lesson.md'] += b'\n## Added English section\n\nIndependent English edit.\n'
        after_files, after = self.build(source)
        self.assertNotEqual(before['locale_digests']['en'], after['locale_digests']['en'])
        self.assertEqual(before['locale_digests']['kk-Cyrl'], after['locale_digests']['kk-Cyrl'])
        self.assertEqual(before_files['rendered/kk-Cyrl/' + EN + '/lesson.md'], after_files['rendered/kk-Cyrl/' + EN + '/lesson.md'])

    def test_direct_kazakh_edits_and_new_cells_assets_helpers_are_used(self):
        source = Source(); _, before = self.build(source)
        source.files[KK + '/lesson.md'] += b'\n## Own section\n\nEdited directly.\n![More](images/new.svg)\n'
        source.files[KK + '/images/new.svg'] = b'<svg xmlns="http://www.w3.org/2000/svg"><text>new</text></svg>'
        notebook = contract.read_json(source.files[KK + '/lab.ipynb'])
        notebook['cells'].append({'id': 'native-extra-cell', 'cell_type': 'markdown', 'metadata': {}, 'source': ['## Extra native section\n']})
        source.put(KK + '/lab.ipynb', notebook)
        source.files[KK + '/probe.py'] = b'LANGUAGE = "independently-maintained"\n'
        files, after = self.build(source)
        self.assertEqual(files['rendered/kk-Cyrl/' + EN + '/lesson.md'], source.files[KK + '/lesson.md'])
        self.assertEqual(files['rendered/kk-Cyrl/' + EN + '/probe.py'], source.files[KK + '/probe.py'])
        self.assertEqual(contract.read_json(files['rendered/kk-Cyrl/' + EN + '/lab.ipynb'])['cells'][-1]['id'], 'native-extra-cell')
        image = next(a for a in after['assets'] if a['source_path'] == EN + '/images/new.svg')
        self.assertIn(image['id'], after['profiles'][0]['asset_ids'])
        self.assertEqual(before['locale_digests']['en'], after['locale_digests']['en'])
        self.assertNotEqual(before['locale_digests']['kk-Cyrl'], after['locale_digests']['kk-Cyrl'])

    def test_quiz_wording_changes_only_that_presentation_without_regrading_identity(self):
        source = Source(); _, before = self.build(source)
        bank = contract.load(source.files[KK + '/quiz.yaml'])
        bank['questions'][0]['prompt'] += ' Independent edited wording.'
        source.put(KK + '/quiz.yaml', bank)
        _, after = self.build(source)
        one = next(q for q in before['quizzes'] if q['source_path'] == EN + '/quiz.yaml')
        two = next(q for q in after['quizzes'] if q['source_path'] == EN + '/quiz.yaml')
        self.assertEqual(one['semantic_revision'], two['semantic_revision'])
        self.assertEqual(one['questions'], two['questions'])
        self.assertEqual(one['presentations'][0], two['presentations'][0])
        self.assertNotEqual(one['presentations'][1], two['presentations'][1])

    def test_quiz_answer_changes_cannot_silently_share_one_cross_language_grade(self):
        source = Source(); bank = contract.load(source.files[KK + '/quiz.yaml'])
        question = bank['questions'][0]
        question['answer'] = next(o['id'] for o in question['options'] if o['id'] != question['answer'])
        source.put(KK + '/quiz.yaml', bank)
        with self.assertRaisesRegex(ValueError, 'grading identity'):
            self.build(source)

    def test_new_semantic_identity_uses_stable_git_origin_not_private_answers(self):
        source = Source()
        bank = contract.load(source.files[EN + '/quiz.yaml'])
        meaning = producer.ordered_grading(bank)
        before = copy.deepcopy(bank); before['questions'][0]['answer'] = 'previous-answer'
        wording = copy.deepcopy(bank); wording['description'] += ' Earlier display wording.'
        commits = ['b' * 40, 'c' * 40, 'd' * 40]

        def git(*args):
            if args[0] == 'log':
                return ('\n'.join(commits) + '\n').encode()
            revision = args[1].split(':', 1)[0]
            return producer.data({commits[0]: bank, commits[1]: wording, commits[2]: before}[revision])

        source.git = git
        origin = producer.grading_origin(source, EN + '/quiz.yaml', meaning)
        self.assertEqual(origin, commits[1])
        token = contract.semantic_token(EN + '/quiz.yaml', origin)
        self.assertNotEqual(token, 'quiz.' + contract.digest(meaning)[:32])
        source.ref = 'e' * 40
        self.assertEqual(producer.grading_origin(source, EN + '/quiz.yaml', meaning), origin)
        self.assertEqual(contract.semantic_token(EN + '/quiz.yaml', origin), token)

    def test_public_presentation_revision_cannot_be_an_answer_or_explanation_oracle(self):
        bank = contract.load(Source().files[EN + '/quiz.yaml'])
        before = contract.presentation(bank)
        private_change = copy.deepcopy(bank)
        private_change['questions'][0]['answer'] = 'another-private-answer'
        private_change['questions'][0]['explanation'] = 'Updated private explanation.'
        self.assertEqual(contract.presentation(private_change), before)
        private_change['questions'][0]['prompt'] += ' Updated public wording.'
        self.assertNotEqual(contract.presentation(private_change), before)

    def test_build_is_deterministic_and_refuses_existing_output(self):
        source = Source()
        with tempfile.TemporaryDirectory() as temporary:
            one, two = Path(temporary) / 'one', Path(temporary) / 'two'
            producer.build(source, one, ['example-complete'], ['en', 'kk-Cyrl'])
            producer.build(source, two, ['example-complete'], ['en', 'kk-Cyrl'])
            self.assertEqual((one / 'teaching.tar.gz').read_bytes(), (two / 'teaching.tar.gz').read_bytes())
            with self.assertRaisesRegex(ValueError, 'output exists'):
                producer.build(source, one, ['example-complete'], ['en', 'kk-Cyrl'])


if __name__ == '__main__':
    unittest.main()
