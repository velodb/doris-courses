"""Extraction completeness, profile closure and reproducible producer tests.

Target-language fixtures are synthetic; they establish no real course quality.
"""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import content_producer as p
import content_contract_v2 as c

REPO = Path(__file__).resolve().parents[2]
BASE = 'doris-course/example/'


class Source:
    ref = 'a' * 40

    def __init__(self):
        self.files = {path.relative_to(c.CONTRACT / 'examples').as_posix(): path.read_bytes()
                      for path in (c.CONTRACT / 'examples').rglob('*') if path.is_file()
                      and path.relative_to(c.CONTRACT / 'examples').as_posix().startswith(('doris-course/', 'shared/'))}
        self.files['catalog.yaml'] = (c.CONTRACT / 'examples/catalog.yaml').read_bytes()
        for path in ('tools/content_producer.py', 'tools/build_content_package.py',
                     'tools/content_contract_v2.py', 'tools/check_content_contract.py',
                     'tools/requirements.txt', 'contracts/v1/schema.json', 'contracts/v2/schema.json',
                     'contracts/v2/locales.json', 'contracts/v2/registry.schema.json'):
            self.files[path] = (REPO / path).read_bytes()
        self.put(p.UI, {'kind': 'doris-ui-messages', 'messages': {'quiz.next': 'Next question', 'greeting': 'Hello {name}'}})
        self.files[BASE + 'lesson.md'] += b'\n![Diagram](../../shared/diagram.svg)\n'
        shell = self.doc(BASE + 'lab.ipynb')
        shell['cells'][1]['source'] = ['CourseQuiz.from_yaml(\"quiz.yaml\")\n']
        self.put(BASE + 'level1/quiz-shell.ipynb', shell)
        self.files[BASE + 'optional.md'] = b'# Extra practice\n\nTry this optional exercise.\n'
        course = self.doc(BASE + 'course.yaml')
        course['modules'][0]['optional'] = [{'title': 'Extra practice', 'path': 'optional.md'}]
        self.put(BASE + 'course.yaml', course)
        self.map = p.reconcile(self)
        self.put(p.MAP, self.map)
        self.config = {'version': 1, 'glossary_version': 'synthetic-1', 'shared_images': {},
            'policy_revisions': {'contract-example': {'revision': 'policy.synthetic-1', 'payload_digest': c.digest(p.policy_payload(course))}},
            'quiz_revisions': {BASE + 'quiz.yaml': {'revision': 'quiz.synthetic-1', 'payload_digest': c.digest(p.quiz_payload(self.doc(BASE + 'quiz.yaml')))}},
            'profiles': [{'id': 'primary', 'modules': [{'course_slug': 'contract-example', 'module_id': 'module-1', 'optional_paths': []}]},
                         {'id': 'optional', 'modules': [{'course_slug': 'contract-example', 'module_id': 'module-1', 'optional_paths': ['optional.md']}]}]}
        self.save_config()
        self.put(p.GLOSSARY, {'version': 'synthetic-1', 'source_locale': 'en', 'target_locale': 'kk-Cyrl', 'entries': []})

    def paths(self):
        return sorted(self.files)

    def read(self, path):
        p.checked_path(path)
        if path not in self.files:
            c.fail('missing test source: ' + path)
        return self.files[path]

    def put(self, path, value):
        self.files[path] = (p.yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode()
                            if path.endswith(('.yaml', '.yml')) else p.json_bytes(value))

    def doc(self, path):
        return p.load(self.files[path], path)

    def save_config(self):
        self.put(p.CONFIG, self.config)

    def refresh(self, bindings=None, allow_new=False):
        self.map = p.reconcile(self, self.map, bindings, allow_new=allow_new)
        self.put(p.MAP, self.map)

    def bilingual(self):
        entries = []
        for unit in self.map['units']:
            text = 'Синтетикалық мәтін: ' + unit['source_text']
            entry = {'unit_id': unit['id'], 'source_hash': unit['source_hash'], 'target_text': text,
                'target_hash': c.sha256(text.encode()), 'state': 'llm_checked',
                'provenance': {'translator': 'current-session-llm', 'session_id': 'synthetic-test',
                    'batch_id': 'synthetic-only', 'glossary_version': 'synthetic-1'},
                'checks': {'native_review': None}}
            for key, method in [('llm_self_check', 'current-session-llm'), ('technical_admission', 'technical-validator')]:
                entry['checks'][key] = {'method': method, 'actor': 'synthetic-fixture',
                    'record': 'Synthetic test data only. No native review.', 'source_hash': unit['source_hash'],
                    'target_hash': entry['target_hash']}
            entries.append(entry)
        self.put('locales/kk-Cyrl/overlay.json', {'schema_version': 2, 'contract_version': '2.0.0',
            'kind': 'doris-locale-overlay', 'locale': 'kk-Cyrl', 'units': entries})
        self.files['locales/kk-Cyrl/diagram.svg'] = self.files['shared/diagram.svg'].replace(b'FE', 'Түйін'.encode())
        self.put('locales/kk-Cyrl/assets.json', {'shared/diagram.svg': 'locales/kk-Cyrl/diagram.svg'})


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.source = Source()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, **kwargs):
        return p.build(self.source, self.root / 'result', ['primary'], **kwargs)

    def test_real_english_shape_full_closure_and_public_projection(self):
        report = self.build()
        self.assertEqual(report['available_locales'], ['en'])
        package = self.source.doc(p.MAP)
        tree = self.root / 'result/tree'
        package = c.read_json((tree / 'package.v2.json').read_bytes())
        required = set(package['profiles'][0]['unit_ids'])
        for path in [BASE + 'README.md', BASE + 'level1/quiz-shell.ipynb', p.UI]:
            self.assertTrue(any(u['source_path'] == path and u['id'] in required for u in package['source_units']))
        self.assertFalse(any(u['source_path'] == BASE + 'optional.md' and u['id'] in required for u in package['source_units']))
        index = self.root / 'result/public/index.json'
        public_text = '\n'.join(p.read_text() for p in index.parent.rglob('*.json'))
        for private in ['"answer"', '"explanation"', 'teaching_digest', 'payload_digest', 'source_hash']:
            self.assertNotIn(private, public_text)
        public_quiz = next((index.parent / 'quizzes/en').glob('*.json'))
        c.shape(json.loads(public_quiz.read_text()), 'public_quiz')

    def test_synthetic_bilingual_notebook_and_grading_invariants(self):
        self.source.bilingual()
        report = self.build(locales=['en', 'kk-Cyrl'])
        self.assertEqual(report['available_locales'], ['en', 'kk-Cyrl'])
        tree = self.root / 'result/tree'
        before = json.loads((tree / (BASE + 'lab.ipynb')).read_text())
        after = json.loads((tree / ('rendered/kk-Cyrl/' + BASE + 'lab.ipynb')).read_text())
        for left, right in zip(before['cells'], after['cells']):
            self.assertEqual(left['id'], right['id'])
            if left['cell_type'] == 'code':
                self.assertEqual(left, right)
        quiz = p.load((tree / ('rendered/kk-Cyrl/' + BASE + 'quiz.yaml')).read_bytes(), 'quiz.yaml')
        self.assertEqual(quiz['questions'][0]['answer'], self.source.doc(BASE + 'quiz.yaml')['questions'][0]['answer'])
        self.assertTrue((tree / 'rendered/kk-Cyrl/shared/diagram.svg').exists())

    def test_deterministic_tree_and_both_archives(self):
        self.source.bilingual()
        p.build(self.source, self.root / 'one', ['primary'], ['en', 'kk-Cyrl'])
        p.build(self.source, self.root / 'two', ['primary'], ['en', 'kk-Cyrl'])
        def contents(root):
            return {f.relative_to(root).as_posix(): f.read_bytes() for f in root.rglob('*') if f.is_file()}
        self.assertEqual(contents(self.root / 'one'), contents(self.root / 'two'))
        with tarfile.open(fileobj=io.BytesIO((self.root / 'one/teaching.tar.gz').read_bytes()), mode='r:gz') as archive:
            self.assertTrue(all(m.mtime == m.uid == m.gid == 0 and m.mode == 0o644 for m in archive.getmembers()))

    def test_missing_translation_release_fails_without_output(self):
        with self.assertRaisesRegex(c.AdmissionError, 'missing/unchecked'):
            self.build(locales=['en', 'kk-Cyrl'])
        self.assertFalse((self.root / 'result').exists())

    def test_preview_reports_missing_and_keeps_declared_locale(self):
        report = self.build(locales=['en', 'kk-Cyrl'], state='preview')
        self.assertEqual(report['available_locales'], ['en'])
        self.assertTrue(report['coverage']['kk-Cyrl']['missing_units'])
        package = json.loads((self.root / 'result/tree/package.v2.json').read_text())
        self.assertEqual([v['locale'] for v in package['variants']], ['en', 'kk-Cyrl'])

    def test_stale_overlay_rejected_even_in_preview(self):
        self.source.bilingual()
        overlay = self.source.doc('locales/kk-Cyrl/overlay.json')
        overlay['units'][0]['source_hash'] = '0' * 64
        self.source.put('locales/kk-Cyrl/overlay.json', overlay)
        with self.assertRaisesRegex(c.AdmissionError, 'stale overlay'):
            self.build(locales=['en', 'kk-Cyrl'], state='preview')

    def test_changed_source_requires_explicit_id_binding(self):
        unit = next(u for u in self.source.map['units'] if u['source_path'] == BASE + 'README.md')
        self.source.files[BASE + 'README.md'] = b'# A revised introduction\n'
        with self.assertRaisesRegex(c.AdmissionError, 'explicit rebind'):
            self.source.refresh()
        self.source.refresh({unit['id']: BASE + 'README.md::block-1'})
        updated = next(u for u in self.source.map['units'] if u['id'] == unit['id'])
        self.assertEqual(updated['locator'], unit['locator'])
        self.assertNotEqual(updated['source_hash'], unit['source_hash'])

    def test_markdown_move_keeps_id_and_opaque_anchor(self):
        old_path = BASE + 'optional.md'
        unit = next(u for u in self.source.map['units'] if u['source_path'] == old_path)
        new_path = BASE + 'level1/moved.md'
        self.source.files[new_path] = self.source.files.pop(old_path)
        course = self.source.doc(BASE + 'course.yaml')
        course['modules'][0]['optional'][0]['path'] = 'level1/moved.md'
        self.source.put(BASE + 'course.yaml', course)
        bindings = {unit['id']: new_path + '::block-1'}
        title_unit = next(u for u in self.source.map['units'] if u['source_path'] == BASE + 'course.yaml'
                          and '/optional/' in u['locator']['value'])
        bindings[title_unit['id']] = BASE + 'course.yaml::module.module-1.optional.level1/moved.md.title'
        self.source.refresh(bindings)
        updated = next(u for u in self.source.map['units'] if u['id'] == unit['id'])
        self.assertEqual(updated['locator'], unit['locator'])
        self.assertEqual(updated['source_path'], new_path)

    def test_omitted_or_extra_units_fail_independent_extraction(self):
        for mutation in ['omit', 'duplicate', 'text', 'scope']:
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(self.source.map)
                if mutation == 'omit': value['units'].pop()
                elif mutation == 'duplicate': value['units'].append({**value['units'][0], 'id': 'another.id'})
                elif mutation == 'text': value['units'][0]['source_text'] += ' changed'
                else: value['units'][0]['scope']['module_id'] = 'module-2'
                self.source.put(p.MAP, value)
                with self.assertRaises(c.AdmissionError): self.build()

    def test_new_units_require_explicit_acceptance(self):
        self.source.files[BASE + 'level1/new.md'] = b'## New instructions\n\nDo this next.\n'
        with self.assertRaisesRegex(c.AdmissionError, 'unregistered'):
            self.source.refresh()
        old = {u['id'] for u in self.source.map['units']}
        self.source.refresh(allow_new=True)
        self.assertTrue(old.issubset({u['id'] for u in self.source.map['units']}))

    def test_optional_closure_is_selected_explicitly(self):
        files, primary = p.build_tree(self.source, ['primary'])
        _, optional = p.build_tree(self.source, ['optional'])
        unit = next(u for u in primary['source_units'] if u['source_path'] == BASE + 'optional.md')
        self.assertNotIn(unit['id'], primary['profiles'][0]['unit_ids'])
        self.assertIn(unit['id'], optional['profiles'][0]['unit_ids'])

    def test_optional_quiz_shell_follows_its_bank_selection(self):
        bank_path = BASE + 'optional-quiz.yaml'
        bank = self.source.doc(BASE + 'quiz.yaml')
        self.source.put(bank_path, bank)
        course = self.source.doc(BASE + 'course.yaml')
        course['modules'][0]['optional'][0]['quiz'] = 'optional-quiz.yaml'
        self.source.put(BASE + 'course.yaml', course)
        shell = self.source.doc(BASE + 'level1/quiz-shell.ipynb')
        shell['cells'][1]['source'] = ['CourseQuiz.from_yaml("optional-quiz.yaml")\n']
        shell_path = BASE + 'level1/optional-shell.ipynb'
        self.source.put(shell_path, shell)
        self.source.refresh(allow_new=True)
        self.source.config['quiz_revisions'][bank_path] = {'revision': 'quiz.synthetic-optional',
                                                         'payload_digest': c.digest(p.quiz_payload(bank))}
        self.source.save_config()
        _, primary = p.build_tree(self.source, ['primary'])
        _, optional = p.build_tree(self.source, ['optional'])
        uid = next(u['id'] for u in primary['source_units'] if u['source_path'] == shell_path)
        self.assertNotIn(uid, primary['profiles'][0]['unit_ids'])
        self.assertIn(uid, optional['profiles'][0]['unit_ids'])

    def test_source_code_change_cannot_hide_in_a_translation(self):
        self.source.bilingual()
        overlay = self.source.doc('locales/kk-Cyrl/overlay.json')
        entry = next(t for t in overlay['units'] if t['unit_id'] == next(
            u['id'] for u in self.source.map['units'] if '{name}' in u['source_text']))
        entry['target_text'] = entry['target_text'].replace('{name}', '{other}')
        entry['target_hash'] = c.sha256(entry['target_text'].encode())
        for check in entry['checks'].values():
            if check: check['target_hash'] = entry['target_hash']
        self.source.put('locales/kk-Cyrl/overlay.json', overlay)
        with self.assertRaisesRegex(c.AdmissionError, 'placeholder'):
            self.build(locales=['en', 'kk-Cyrl'])

    def test_unknown_duplicate_overlay_units_and_wrong_hashes(self):
        for mutation in ['unknown', 'duplicate', 'hash', 'unchecked', 'glossary']:
            with self.subTest(mutation=mutation):
                self.source.bilingual()
                overlay = self.source.doc('locales/kk-Cyrl/overlay.json')
                required_id = next(u['id'] for u in self.source.map['units'] if u['source_path'] == p.UI)
                entry = next(t for t in overlay['units'] if t['unit_id'] == required_id)
                if mutation == 'unknown': entry['unit_id'] = 'unknown.id'
                elif mutation == 'duplicate': overlay['units'].append(copy.deepcopy(entry))
                elif mutation == 'hash': entry['target_hash'] = 'f' * 64
                elif mutation == 'unchecked': entry['state'] = 'translated'
                else: entry['provenance']['glossary_version'] = 'other'
                self.source.put('locales/kk-Cyrl/overlay.json', overlay)
                with self.assertRaises(c.AdmissionError): self.build(locales=['en', 'kk-Cyrl'])
                self.assertFalse((self.root / 'result').exists())

    def test_source_map_cannot_select_code_or_omit_quiz_option(self):
        code = self.source.doc(BASE + 'lab.ipynb')['cells'][1]
        value = copy.deepcopy(self.source.map)
        unit = next(u for u in value['units'] if u['locator']['kind'] == 'notebook-cell')
        unit['locator']['value'] = code['id']
        self.source.put(p.MAP, value)
        with self.assertRaisesRegex(c.AdmissionError, 'incomplete or stale'): self.build()
        value = copy.deepcopy(self.source.map)
        value['units'] = [u for u in value['units'] if '/options/0/' not in u['locator']['value']]
        self.source.put(p.MAP, value)
        with self.assertRaisesRegex(c.AdmissionError, 'incomplete or stale'): self.build()

    def test_unknown_locale_alias_profile_or_optional_work_is_rejected(self):
        for tag in ['fr', 'kk-KZ', 'EN']:
            with self.subTest(tag=tag), self.assertRaisesRegex(c.AdmissionError, 'canonical locale'):
                self.build(locales=['en', tag])
        with self.assertRaisesRegex(c.AdmissionError, 'known producer profiles'):
            p.build_tree(self.source, ['unknown'])
        self.source.config['profiles'][0]['modules'][0]['optional_paths'] = ['unknown.md']
        self.source.save_config()
        with self.assertRaisesRegex(c.AdmissionError, 'unknown optional'): self.build()

    def test_malicious_paths_and_image_bindings_are_rejected(self):
        for path in ['../secret', '/etc/passwd', 'a/../b', 'a/%2e%2e/b', 'a\\b']:
            with self.subTest(path=path), self.assertRaises(c.AdmissionError): p.checked_path(path)
        self.source.bilingual()
        self.source.put('locales/kk-Cyrl/assets.json', {BASE + 'lab.ipynb': 'locales/kk-Cyrl/diagram.svg'})
        with self.assertRaisesRegex(c.AdmissionError, 'English image'): self.build(locales=['en', 'kk-Cyrl'])

    def test_missing_image_treatment_prevents_publication(self):
        self.source.bilingual()
        del self.source.files['locales/kk-Cyrl/assets.json']
        with self.assertRaisesRegex(c.AdmissionError, 'missing, unchecked'):
            self.build(locales=['en', 'kk-Cyrl'])

    def test_unsafe_image_replacement_rejected_after_correct_hashing(self):
        self.source.bilingual()
        self.source.files['locales/kk-Cyrl/diagram.svg'] = b'<svg><script>alert(1)</script></svg>'
        with self.assertRaisesRegex(c.AdmissionError, 'unsafe SVG'):
            self.build(locales=['en', 'kk-Cyrl'])

    def test_shared_image_requires_explicit_evidence(self):
        self.source.bilingual()
        del self.source.files['locales/kk-Cyrl/assets.json']
        self.source.config['shared_images'] = {'shared/diagram.svg': {'record': '', 'rationale': ''}}
        self.source.save_config()
        with self.assertRaisesRegex(c.AdmissionError, 'evidence'): self.build(locales=['en', 'kk-Cyrl'])
        self.source.config['shared_images']['shared/diagram.svg'] = {'record': 'synthetic-test', 'rationale': 'Synthetic geometric image.'}
        self.source.save_config()
        self.assertEqual(self.build(locales=['en', 'kk-Cyrl'])['available_locales'], ['en', 'kk-Cyrl'])

    def test_semantic_and_policy_revision_reuse_rejected(self):
        quiz = self.source.doc(BASE + 'quiz.yaml')
        quiz['questions'][0]['answer'] = quiz['questions'][0]['options'][1]['id']
        self.source.put(BASE + 'quiz.yaml', quiz)
        with self.assertRaisesRegex(c.AdmissionError, 'immutable revision'): self.build()
        self.source = Source()
        course = self.source.doc(BASE + 'course.yaml')
        course['certificate']['code'] = 'OTHER'
        self.source.put(BASE + 'course.yaml', course)
        with self.assertRaisesRegex(c.AdmissionError, 'immutable revision'): self.build()

    def test_draft_course_and_module_cannot_be_exposed(self):
        course = self.source.doc(BASE + 'course.yaml')
        course['status'] = 'draft'
        self.source.put(BASE + 'course.yaml', course)
        with self.assertRaisesRegex(c.AdmissionError, 'draft/planned'): self.build()

    def test_opaque_compose_aliases_preserve_download_bytes(self):
        path = BASE + 'compose.yml'
        self.source.files[path] = b'x: &x {image: example}\nservices: {a: *x}\n'
        self.build()
        self.assertEqual((self.root / 'result/tree' / path).read_bytes(), self.source.files[path])

    def test_source_notebook_normalization_preserves_code_and_metadata(self):
        path = BASE + 'lab.ipynb'
        notebook = self.source.doc(path)
        notebook['cells'][0]['source'] = ['<div style="color:red">Text</div>\n']
        data = p.json_bytes(notebook)
        result = json.loads(p.normalized_notebook(data))
        self.assertEqual(result['cells'][0]['source'], ['<div>Text</div>\n'])
        self.assertEqual(result['cells'][1], notebook['cells'][1])
        self.assertEqual(result['metadata'], notebook['metadata'])

    def test_optional_translations_are_not_required_by_primary_metadata(self):
        self.source.bilingual()
        excluded = {u['id'] for u in self.source.map['units'] if u['source_path'] == BASE + 'optional.md'
                    or '/optional/' in u['locator']['value']}
        overlay = self.source.doc('locales/kk-Cyrl/overlay.json')
        overlay['units'] = [t for t in overlay['units'] if t['unit_id'] not in excluded]
        self.source.put('locales/kk-Cyrl/overlay.json', overlay)
        self.assertEqual(self.build(locales=['en', 'kk-Cyrl'])['available_locales'], ['en', 'kk-Cyrl'])

    def test_legacy_style_transform_keeps_code_examples_and_prose(self):
        text = '<div style="color:red">Text</div>\n\n```html\n<div style="color:red">Text</div>\n```\n'
        text += '\n`<div style="color:red">` and the setting style="color:red".\n'
        result = p.strip_legacy_styles(text)
        self.assertTrue(result.startswith('<div>Text</div>'))
        self.assertIn('```html\n<div style="color:red">Text</div>\n```', result)
        self.assertIn('`<div style="color:red">`', result)
        self.assertIn('the setting style="color:red"', result)

    def test_builder_mismatch_is_rejected(self):
        self.source.files['tools/content_producer.py'] += b'\n# another version\n'
        with self.assertRaisesRegex(c.AdmissionError, 'installed pinned builder'): self.build()

    def test_revision_cannot_be_rebound_by_updating_its_digest(self):
        repo = self.root / 'history'; repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-q'); git('config', 'user.email', 'test@example.invalid'); git('config', 'user.name', 'Fixture')
        (repo / 'locales').mkdir()
        (repo / p.CONFIG).write_bytes(p.json_bytes(self.source.config))
        git('add', p.CONFIG); git('commit', '-qm', 'first binding')
        self.source.config['policy_revisions']['contract-example']['payload_digest'] = 'b' * 64
        (repo / p.CONFIG).write_bytes(p.json_bytes(self.source.config))
        git('add', p.CONFIG); git('commit', '-qm', 'incorrectly reuse revision')
        with self.assertRaisesRegex(c.AdmissionError, 'reused with different meaning'):
            p.verify_revision_history(p.GitSource(repo, git('rev-parse', 'HEAD')))

    def test_existing_output_is_never_overwritten(self):
        self.build()
        with self.assertRaisesRegex(c.AdmissionError, 'already exists'): self.build()

    def test_git_source_requires_commit_and_rejects_symlink(self):
        repo = self.root / 'git'; repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
        git('init', '-q'); git('config', 'user.email', 'test@example.invalid'); git('config', 'user.name', 'Fixture')
        (repo / 'file').write_bytes(b'committed')
        (repo / 'link').symlink_to('file')
        git('add', 'file', 'link'); git('commit', '-qm', 'fixture')
        sha = git('rev-parse', 'HEAD')
        source = p.GitSource(repo, sha)
        (repo / 'file').write_bytes(b'dirty')
        self.assertEqual(source.read('file'), b'committed')
        with self.assertRaisesRegex(c.AdmissionError, 'non-regular'): source.read('link')
        with self.assertRaisesRegex(c.AdmissionError, 'full 40-character'): p.GitSource(repo, 'HEAD')


if __name__ == '__main__':
    unittest.main()
