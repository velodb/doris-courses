"""Independent file-contract failure cases; all target text is synthetic.

Mutations are rehashed deliberately, so these test semantic admission rather
than merely detecting an obsolete file digest. No historical overlays or LLM
checks are present, and no course Lab or external service is executed.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import content_files_contract as contract
import course_files_producer as producer

EN = 'doris-course/example'
KK = 'doris-course/kk-Cyrl/example'


class Source:
    ref = 'a' * 40

    def __init__(self):
        examples = ROOT / 'contracts/v2/examples'
        self.files = {EN + '/' + p.name: p.read_bytes() for p in (examples / EN).iterdir() if p.is_file()}
        course = yaml.safe_load(self.files[EN + '/course.yaml'])
        course['modules'][0]['optional'] = [{'title': 'Optional', 'path': 'optional.md', 'quiz': 'optional.yaml'}]
        self.put(EN + '/course.yaml', course)
        self.files[EN + '/optional.md'] = b'# Optional\n'
        self.files[EN + '/optional.yaml'] = self.files[EN + '/quiz.yaml']
        self.files[EN + '/quiz.ipynb'] = self.files[EN + '/lab.ipynb']
        self.files[EN + '/optional.ipynb'] = self.files[EN + '/lab.ipynb']
        self.files[EN + '/probe.py'] = b'LANGUAGE = "en"\n'
        self.files[EN + '/environments/compose.yaml'] = b'services: {}\n'
        for path, raw in list(self.files.items()):
            self.files[path.replace(EN, KK, 1)] = raw
        localized = copy.deepcopy(course); localized['language'] = 'kk-Cyrl'; localized['title'] = 'Synthetic kk'
        self.put(KK + '/course.yaml', localized)
        self.files[KK + '/probe.py'] = b'LANGUAGE = "kk-Cyrl"\n'
        self.files[KK + '/lesson.md'] = '# Synthetic kk\n\n![Independent](images/kk-only.svg)\n'.encode()
        self.files[KK + '/images/kk-only.svg'] = b'<svg xmlns="http://www.w3.org/2000/svg"><text>unit fixture</text></svg>'
        catalog = yaml.safe_load((examples / 'catalog.yaml').read_text())
        self.put('catalog.yaml', catalog)
        catalog['courses'] = [KK + '/course.yaml']
        self.put('doris-course/kk-Cyrl/catalog.yaml', catalog)
        self.put('locales/ui.en.json', {'schema_version': 1, 'kind': 'doris-ui-messages', 'locale': 'en',
            'messages': {'quiz.next': 'Next', 'greeting': 'Hello {name}'}})
        self.put('doris-course/kk-Cyrl/ui.yaml', {'quiz.next': 'Synthetic kk', 'greeting': 'Synthetic kk {name}'})
        self.put('locales/ui.contract.json', {'version': 1, 'parameters': {'quiz.next': {}, 'greeting': {'name': 'text'}}, 'plurals': {}})
        self.put('locales/course-files.yaml', {'schema_version': 1, 'locales': {
            'en': {'root': 'doris-course', 'catalog': 'catalog.yaml', 'ui': 'locales/ui.en.json'},
            'kk-Cyrl': {'root': 'doris-course/kk-Cyrl', 'catalog': 'doris-course/kk-Cyrl/catalog.yaml', 'ui': 'doris-course/kk-Cyrl/ui.yaml'}},
            'profiles': [{'id': 'example-complete', 'modules': [{'course_slug': 'contract-example',
                'module_id': 'module-1', 'optional_paths': ['optional.md']}]}],
            'legacy_revisions': {'quizzes': {}, 'policies': {}}})
        for path in ('tools/course_files_producer.py', 'tools/content_files_contract.py',
                     'contracts/v2/schema.json', 'contracts/v2/locales.json', 'contracts/v1/schema.json'):
            self.files[path] = (ROOT / path).read_bytes()
        self.files['doris-course/common/probe.py'] = b'SHARED = True\n'
        self.files['doris-course/common/empty.txt'] = b''

    def put(self, path, value):
        self.files[path] = (yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode()
                            if path.endswith('.yaml') else producer.data(value))

    def read(self, path):
        return self.files[path]

    def paths(self):
        return sorted(self.files)


class CourseFileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Source()
        cls.original_files, cls.original_package = producer.build_tree(cls.source,
            ['example-complete'], ['en', 'kk-Cyrl'])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='course-files-contract-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'tree'
        self.reset()

    def reset(self):
        self.files = copy.deepcopy(self.original_files)
        self.package = copy.deepcopy(self.original_package)

    def publish(self):
        index = contract.read_json(self.files['indexes/assets.json'])
        index['assets'] = self.package['assets']
        by_id = {a['id']: a for a in self.package['assets']}
        # Keep existing download rows self-consistent after source edits, while
        # deliberately preserving omissions/duplicates being tested.
        for row in index['downloads']:
            asset = by_id[row['asset_id']]
            row.update(path=asset['file']['path'], sha256=asset['file']['sha256'])
        self.files['indexes/assets.json'] = producer.data(index)
        self.package['snapshot']['files'] = [producer.record(path, raw) for path, raw in sorted(self.files.items())
                                               if path != 'package.v3.json']
        self.package['locale_digests'] = {tag: contract.locale_digest(self.package, tag)
                                          for tag in self.package['available_locales']}
        self.package['snapshot']['package_digest'] = contract.package_digest(self.package)
        self.files['package.v3.json'] = producer.data(self.package)
        for path, raw in self.files.items():
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)

    def edit_source(self, path, value):
        raw = (yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode()
               if path.endswith('.yaml') and not isinstance(value, bytes) else
               producer.data(value) if not isinstance(value, bytes) else value)
        self.files[path] = raw
        inputs = contract.read_json(self.files['indexes/source-inputs.json'])
        inputs['inputs'] = [producer.record(path, raw) if row['path'] == path else row for row in inputs['inputs']]
        for projection in inputs['projections']:
            if projection['source'] == path:
                self.files[projection['path']] = contract.project(raw, projection['transformation'])
        for asset in self.package['assets']:
            if asset['source_file'] == path:
                target = asset['file']['path']
                asset['file'] = producer.record(target, self.files[target])
        self.files['indexes/source-inputs.json'] = producer.data(inputs)

    def reject(self):
        self.publish()
        with self.assertRaises((ValueError, OSError)):
            contract.validate_package(self.root)

    def test_complete_direct_source_package_and_independent_cells_are_admitted(self):
        notebook = contract.read_json(self.files[KK + '/lab.ipynb'])
        notebook['cells'].append({'cell_type': 'markdown', 'id': 'kk-independent', 'metadata': {},
                                  'source': '# A new independent section\n'})
        next(c for c in notebook['cells'] if c['cell_type'] == 'code')['source'] = ['own_language_code = 42\n']
        self.edit_source(KK + '/lab.ipynb', notebook)
        self.publish()
        self.assertNotIn('source_units', self.package); self.assertNotIn('translations', self.package)
        self.assertEqual(contract.validate_package(self.root)['available_locales'], ['en', 'kk-Cyrl'])
        image = next(a for a in self.package['assets'] if a['source_file'] == KK + '/images/kk-only.svg')
        self.assertIn(image['id'], self.package['profiles'][0]['asset_ids'])

    def test_unknown_top_level_snapshot_asset_and_quiz_fields_are_rejected(self):
        for section in ('package', 'snapshot', 'asset', 'quiz', 'course', 'presentation'):
            with self.subTest(section=section):
                self.reset()
                target = {'package': self.package, 'snapshot': self.package['snapshot'],
                          'asset': self.package['assets'][0], 'quiz': self.package['quizzes'][0],
                          'course': self.package['courses'][0],
                          'presentation': self.package['quizzes'][0]['presentations'][0]}[section]
                target['unknown_contract_field'] = True
                self.reject()

    def test_source_input_records_reject_unknown_fields(self):
        for change in ('unknown', 'boolean_size'):
            with self.subTest(change=change):
                self.reset()
                inputs = contract.read_json(self.files['indexes/source-inputs.json'])
                if change == 'unknown':
                    inputs['inputs'][0]['unknown_contract_field'] = True
                else:
                    next(r for r in inputs['inputs'] if r['path'] == 'doris-course/common/empty.txt')['size'] = False
                self.files['indexes/source-inputs.json'] = producer.data(inputs)
                self.reject()

    def test_independent_authoring_source_check_rejects_a_different_helper(self):
        self.publish()
        source_root = Path(self.temporary.name) / 'independent-source'
        for path, raw in self.source.files.items():
            target = source_root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        contract.validate_package(self.root, source=source_root)
        (source_root / KK / 'probe.py').write_bytes(b'LANGUAGE = "different"\n')
        with self.assertRaisesRegex(ValueError, 'independent Git source differs'):
            contract.validate_package(self.root, source=source_root)

    def test_missing_primary_optional_and_quiz_shell_assets_are_rejected(self):
        for relative in ('lab.ipynb', 'optional.md', 'optional.ipynb', 'quiz.ipynb'):
            with self.subTest(path=relative):
                self.reset()
                removed = {a['id'] for a in self.package['assets'] if a['source_path'] == EN + '/' + relative}
                self.package['profiles'][0]['asset_ids'] = [a for a in self.package['profiles'][0]['asset_ids'] if a not in removed]
                self.reject()

    def test_missing_locale_quiz_and_rehashed_changed_answers_are_rejected(self):
        for change in ('optional_locale_omitted', 'answers'):
            with self.subTest(change=change):
                self.reset()
                if change == 'optional_locale_omitted':
                    self.package['quizzes'][0]['presentations'] = [p for p in self.package['quizzes'][0]['presentations'] if p['locale'] == 'en']
                else:
                    path = KK + '/quiz.yaml'; bank = yaml.safe_load(self.files[path]); q = bank['questions'][0]
                    q['answer'] = next(o['id'] for o in q['options'] if o['id'] != q['answer'])
                    self.edit_source(path, bank)
                    descriptor = next(q for q in self.package['quizzes'] if q['source_path'] == EN + '/quiz.yaml')
                    next(p for p in descriptor['presentations'] if p['locale'] == 'kk-Cyrl')['presentation_revision'] = contract.presentation(bank)
                self.reject()

    def test_machine_course_identity_and_ui_placeholders_are_independently_checked(self):
        for change in ('level', 'placeholder'):
            with self.subTest(change=change):
                self.reset()
                if change == 'level':
                    path = KK + '/course.yaml'; course = yaml.safe_load(self.files[path]); course['modules'][0]['level'] = 2
                    self.edit_source(path, course)
                else:
                    path = 'doris-course/kk-Cyrl/ui.yaml'; ui = yaml.safe_load(self.files[path]); ui['greeting'] = 'Missing the parameter'
                    self.edit_source(path, ui)
                self.reject()

    def test_missing_download_entries_and_false_source_file_claims_are_rejected(self):
        for change in ('downloads', 'source_file'):
            with self.subTest(change=change):
                self.reset()
                index = contract.read_json(self.files['indexes/assets.json'])
                if change == 'downloads':
                    index['downloads'] = []
                else:
                    asset = next(a for a in self.package['assets'] if a['source_file'] == KK + '/lesson.md')
                    asset['source_file'] = EN + '/lesson.md'
                    next(r for r in index['links'] if r['path'] == asset['file']['path'])['source_file'] = EN + '/lesson.md'
                self.files['indexes/assets.json'] = producer.data(index)
                self.reject()

    def test_unsafe_images_outputs_and_symlinks_are_rejected(self):
        self.edit_source(KK + '/images/kk-only.svg', b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
        self.reject()
        self.reset()
        notebook = contract.read_json(self.files[KK + '/lab.ipynb'])
        next(c for c in notebook['cells'] if c['cell_type'] == 'code')['outputs'] = [{'output_type': 'stream', 'text': 'not cleared'}]
        self.edit_source(KK + '/lab.ipynb', notebook)
        self.reject()
        self.reset(); self.publish(); (self.root / 'outside').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            contract.validate_package(self.root)

    def test_v2_manifest_and_wrong_ref_digest_are_never_admitted_as_v3(self):
        self.publish()
        with self.assertRaises(ValueError):
            contract.validate_package(self.root, expected_ref='b' * 40)
        with self.assertRaises(ValueError):
            contract.validate_package(self.root, expected_digest='b' * 64)
        (self.root / 'package.v3.json').rename(self.root / 'package.v2.json')
        with self.assertRaises((ValueError, OSError)):
            contract.validate_package(self.root)


if __name__ == '__main__':
    unittest.main()
