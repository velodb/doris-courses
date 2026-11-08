"""Workshop file-v3 routing with synthetic assets; not translation admission."""
import copy
import io
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import tarfile
import sys
import unittest
from unittest import mock
from types import SimpleNamespace
import test_multilingual_complete_profiles as profiles_fixture
from test_multilingual_complete_profiles import (SyntheticPackage,
                                                 PROFILES, binding, config, consumer, portal, wc)
import workshop_runtime as runtime


class CourseFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the pinned English manifest fixture, not the v2 admission path.
        profiles_fixture.CompleteProfileTests.setUpClass()
        cls.base = profiles_fixture.CompleteProfileTests

    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='course-files-runtime-')
        self.addCleanup(temporary.cleanup)
        plan = copy.deepcopy(self.base.plan)
        plan['content']['abi'] = consumer.FILES_ABI
        plan['content']['capabilities'] = consumer.FILES_CAPABILITIES
        plan['capabilities'] = consumer.FILES_CAPABILITIES
        fixture = SyntheticPackage(temporary.name, plan, self.base.manifests,
                                  lambda p: wc.git(wc.REPO, 'show', f'{self.base.ref}:{p}'))
        fixture.package.update(schema_version=3, kind='doris-course-files',
            course_sources={locale: {c['slug']: c['course_dir'] if locale == 'en' else
                            'doris-course/kk-Cyrl/' + Path(c['course_dir']).name for c in plan['courses']}
                            for locale in ('en', 'kk-Cyrl')})
        fixture.package.pop('source_units')
        return fixture

    def test_abi_and_capabilities_are_strict_pairs_in_consumer_and_all_config_schemas(self):
        value = binding(self.base.ref)
        for abi, capabilities in ((consumer.ABI, consumer.CAPABILITIES),
                                  (consumer.FILES_ABI, consumer.FILES_CAPABILITIES)):
            good = {**value, 'abi': abi, 'capabilities': capabilities}
            consumer.validate_binding(good)
            wc.schema_check(good, 'content-binding.schema.json')
            cfg = config(self.base.ref); cfg['content'] = good
            normalized, resolved, runtime = wc.validate_config(cfg)
            manifest = wc.build_manifest(normalized, resolved, runtime)
            wc.schema_check(manifest, 'manifest.schema.json')
            release = wc.load_document(wc.SPEC / 'examples/image-release.v2.example.json')
            release['content'] = good
            wc.schema_check(release, 'image-release.schema.json')
            bad = {**good, 'capabilities': (consumer.FILES_CAPABILITIES if abi == consumer.ABI else consumer.CAPABILITIES)}
            with self.assertRaises(ValueError):
                consumer.validate_binding(bad)
            with self.assertRaises(wc.ConfigError):
                wc.schema_check(bad, 'content-binding.schema.json')
            cfg['content'] = bad
            with self.assertRaises(wc.ConfigError):
                wc.schema_check(cfg, 'config.schema.json')
            release['content'] = bad
            with self.assertRaises(wc.ConfigError):
                wc.schema_check(release, 'image-release.schema.json')

    def test_v3_fingerprints_include_independent_language_code_and_dependencies(self):
        package = {'schema_version': 3, 'assets': [
            {'locale': locale, 'kind': 'code', 'file': {'path': prefix + '/pyproject.toml', 'sha256': checksum}}
            for locale, prefix, checksum in [('en', 'doris-course/example', '1' * 64),
                ('kk-Cyrl', 'rendered/kk-Cyrl/doris-course/example', '2' * 64)]]}
        before = consumer.fingerprint(package)
        package['assets'][1]['file']['sha256'] = '3' * 64
        after = consumer.fingerprint(package)
        self.assertNotEqual(before[0], after[0]); self.assertNotEqual(before[1], after[1])
        package['schema_version'] = 2
        unchanged = consumer.fingerprint(package)
        package['assets'][1]['file']['sha256'] = '4' * 64
        self.assertEqual(unchanged, consumer.fingerprint(package))

    def test_v3_copy_uses_its_own_cell_ids_order_metadata_and_heading_count(self):
        fixture = self.fixture()
        path = fixture.plan['courses'][0]['course_dir'] + '/' + fixture.plan['courses'][0]['modules'][0]['lab']
        target = 'rendered/kk-Cyrl/' + path
        notebook = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {'independent': True}, 'cells': [
            {'cell_type': 'markdown', 'id': 'kk-only-cell', 'metadata': {'own': True},
             'source': '<a id="old-english-heading"></a>\n\n# Қазақша\n\n## Жаңа бөлім\n\n```md\n[literal](course.md)\n```\n\n`[literal](course.md)`\n'},
            {'cell_type': 'code', 'id': 'kk-own-code', 'metadata': {}, 'source': ['value = 42\n'],
             'outputs': [], 'execution_count': None}]}
        fixture.write(target, json.dumps(notebook).encode())
        index = json.loads((fixture.root / 'indexes/assets.json').read_text())
        next(r for r in index['links'] if r['source_path'] == path and r['locale'] == 'kk-Cyrl')['path'] = target
        fixture.write('indexes/assets.json', json.dumps(index).encode())
        fixture.package['snapshot']['files'] = list(fixture.files.values())
        adapter = fixture.adapter()
        copied = adapter.copy(path, 'kk-Cyrl')
        document = json.loads((adapter.workspace / copied).read_text())
        self.assertEqual([c['id'] for c in document['cells']], ['kk-only-cell', 'kk-own-code'])
        self.assertEqual(document['cells'], notebook['cells'])
        self.assertTrue(document['metadata']['independent'])
        self.assertEqual(document['metadata']['kernelspec']['name'], fixture.plan['courses'][0]['kernel'])
        self.assertIsNone(adapter.aliases(path))
        # A later reopen preserves a learner's own edits.
        (adapter.workspace / copied).write_text('learner edits')
        self.assertEqual(adapter.copy(path, 'kk-Cyrl'), copied)
        self.assertEqual((adapter.workspace / copied).read_text(), 'learner edits')

    def test_physical_language_paths_map_to_the_same_stable_quiz_identity(self):
        fixture = self.fixture(); adapter = fixture.adapter()
        for course in fixture.plan['courses']:
            canonical = course['course_dir'] + '/' + course['modules'][0]['quiz']
            self.assertEqual(adapter.canonical_source(canonical, 'en'), canonical)
            physical = adapter.source_course(course, 'kk-Cyrl') + '/' + course['modules'][0]['quiz']
            self.assertEqual(adapter.canonical_source(physical, 'kk-Cyrl'), canonical)
            with self.assertRaises(ValueError):
                adapter.canonical_source(canonical, 'kk-Cyrl')
        changed = fixture.package['course_sources']['kk-Cyrl']
        changed[fixture.plan['courses'][0]['slug']] = '../outside'
        with self.assertRaisesRegex(ValueError, 'source root'):
            fixture.adapter()

    def test_v3_markdown_renders_its_explicit_anchors_without_english_aliases(self):
        fixture = self.fixture(); adapter = fixture.adapter()
        rendered = portal.render_markdown('<a id="prior-alias"></a>\n\n## Жаңа бөлім\n',
                                          'doris-course/example/lesson.md', '/', adapter=adapter)
        self.assertIn('<a id="prior-alias"></a>', rendered)
        self.assertNotIn('&lt;a', rendered)
        title, body = portal.lesson_title('<a id="prior-title"></a>\n\n# Қазақша атау\n\n## Жаңа бөлім\n',
                                          'course', course_files=True)
        self.assertEqual(title, 'Қазақша атау')
        self.assertIn('<a id="prior-title"></a>', body)
        self.assertNotIn('# Қазақша атау', body)
        self.assertIn('&lt;db&gt;', portal.render_markdown('Use <db> here.\n',
            'doris-course/example/lesson.md', '/', adapter=adapter))
        with self.assertRaisesRegex(ValueError, 'unsafe authored HTML attribute'):
            portal.render_markdown('<a id="x" onclick="alert(1)">link</a>\n',
                                   'doris-course/example/lesson.md', '/', adapter=adapter)

    def test_links_use_physical_kk_depth_and_explicit_english_or_shared_routes(self):
        fixture = self.fixture(); adapter = fixture.adapter()
        course = fixture.plan['courses'][0]
        current = course['course_dir'] + '/' + course['modules'][0]['course']
        # course.md is four directories below doris-course/kk-Cyrl; crossing
        # that extra level is essential when linking back to English sources.
        import posixpath
        physical = adapter.source_course(course, 'kk-Cyrl') + '/' + course['modules'][0]['course']
        english = course['course_dir'] + '/' + course['modules'][0]['course']
        relative = posixpath.relpath(english, posixpath.dirname(physical))
        self.assertEqual(adapter.link_target(current, relative, 'kk-Cyrl'), (english, 'en'))
        common = 'doris-course/common/doris_labkit/quiz.py'
        relative_common = posixpath.relpath(common, posixpath.dirname(physical))
        self.assertEqual(adapter.link_target(current, relative_common, 'kk-Cyrl'), (common, 'kk-Cyrl'))
        self.assertEqual(adapter.link_target(current, 'images/new-kk-only.png', 'kk-Cyrl'),
                         (str(Path(current).parent / 'images/new-kk-only.png'), 'kk-Cyrl'))

    def test_kk_only_assets_are_selected_by_logical_source_path(self):
        fixture = self.fixture()
        course = fixture.plan['courses'][0]
        logical = str(Path(course['course_dir']) / Path(course['modules'][0]['course']).parent / 'images/kk-only.png')
        target = 'rendered/kk-Cyrl/' + logical
        physical = logical.replace('doris-course/', 'doris-course/kk-Cyrl/', 1)
        fixture.write(target, b'unit-fixture-image')
        fixture.package['assets'].append({'id': 'a.only-kk', 'kind': 'image', 'locale': 'kk-Cyrl',
            'file': fixture.files[target], 'source_path': logical, 'source_file': physical})
        fixture.package['profiles'][0]['asset_ids'].append('a.only-kk')
        index = json.loads((fixture.root / 'indexes/assets.json').read_text())
        index['links'].append({'locale': 'kk-Cyrl', 'source_path': logical, 'source_file': physical, 'path': target})
        fixture.write('indexes/assets.json', json.dumps(index).encode())
        fixture.package['snapshot']['files'] = list(fixture.files.values())
        adapter = fixture.adapter()
        self.assertEqual(adapter.resolve(logical, 'asset', 'kk-Cyrl'), (target, 'kk-Cyrl'))

    def test_manifest_filename_and_abi_cannot_be_crossed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = {'schema_version': 3, 'kind': 'doris-course-files', 'package_state': 'release',
                'assets': [], 'snapshot': {'source_ref': self.base.ref, 'package_digest': '1' * 64, 'files': []},
                'default_locale': 'en', 'available_locales': ['en', 'kk-Cyrl'],
                'locale_digests': {'en': '2' * 64, 'kk-Cyrl': '3' * 64}, 'selected_profiles': PROFILES}
            payload = copy.deepcopy(package); payload['snapshot'].pop('package_digest')
            package['snapshot']['package_digest'] = consumer.digest(consumer.canonical(payload))
            (root / 'package.v3.json').write_text(json.dumps(package))
            expected = consumer.binding(package)
            self.assertEqual(consumer.verify_tree(root, expected), package)
            tampered = {**package, 'course_sources': {'en': {}}}
            (root / 'package.v3.json').write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, 'manifest digest'):
                consumer.verify_tree(root, expected)
            (root / 'package.v3.json').write_text(json.dumps(package))
            (root / 'package.v3.json').rename(root / 'package.v2.json')
            legacy = {**expected, 'abi': consumer.ABI, 'capabilities': consumer.CAPABILITIES}
            with self.assertRaisesRegex(ValueError, 'format differs'):
                consumer.verify_tree(root, legacy)
            with self.assertRaisesRegex(ValueError, 'format or kind'):
                consumer.binding({**package, 'schema_version': 2})

    def test_executed_source_archive_must_match_the_package_raw_authoring_inputs(self):
        path, raw = 'doris-course/kk-Cyrl/example/helper.py', b'VALUE = 42\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def archive(name, files):
                with tarfile.open(root / name, 'w') as stream:
                    for path, data in files.items():
                        info = tarfile.TarInfo(path); info.size = len(data)
                        stream.addfile(info, io.BytesIO(data))
                return root / name
            teaching = archive('teaching.tar', {'indexes/source-inputs.json': json.dumps({'inputs': [
                {'path': path, 'size': len(raw), 'sha256': consumer.digest(raw)}]}).encode()})
            source = archive('source.tar', {path: raw})
            runtime.verify_course_sources(source, teaching)
            archive('source.tar', {path: b'VALUE = 99\n'})
            with self.assertRaisesRegex(wc.ConfigError, 'differs from frozen'):
                runtime.verify_course_sources(source, teaching)

    def test_language_dependency_changes_need_a_locale_lock_and_an_own_environment(self):
        fixture = self.fixture(); adapter = fixture.adapter(); course = fixture.plan['courses'][0]
        source = fixture.root / 'installed-source'; common = source / 'doris-course/common'
        en, kk = source / course['course_dir'], source / adapter.source_course(course, 'kk-Cyrl')
        project = ('[build-system]\nrequires=["setuptools>=68"]\n'
                   '[project]\nname="test-course"\nversion="0.1"\ndependencies=["base-package==1"]\n'
                   '[project.optional-dependencies]\nextra=["extra-package==1"]\n')
        for root, common_path in [(en, '../common'), (kk, '../../common')]:
            root.mkdir(parents=True)
            (root / 'requirements.txt').write_text('-e ' + common_path + '\n-e .\n-r extras.txt\n')
            (root / 'extras.txt').write_text('included-package==1\n')
            (root / 'pyproject.toml').write_text(project)
        self.assertEqual(consumer.dependency_signature(en, common), consumer.dependency_signature(kk, common))
        self.assertEqual(adapter.environment(source, course, 'kk-Cyrl'), en / '.venv')
        for root, reference in [(en, '../common'), (kk, '../../common')]:
            (root / 'pyproject.toml').write_text(project.replace('base-package==1', 'shared-kit @ file:' + reference))
        self.assertEqual(consumer.dependency_signature(en, common), consumer.dependency_signature(kk, common))
        for root in (en, kk):
            (root / 'pyproject.toml').write_text(project)
        # Neither a changed included requirement nor project dependencies,
        # extras or build requirements may silently use the English lock/env.
        for change in ('include', 'dependency', 'extra', 'build'):
            with self.subTest(change=change):
                (kk / 'extras.txt').write_text('included-package==2\n' if change == 'include' else 'included-package==1\n')
                target = project.replace({'dependency': 'base-package==1', 'extra': 'extra-package==1',
                    'build': 'setuptools>=68'}.get(change, 'not-present'), 'changed-package==2')
                (kk / 'pyproject.toml').write_text(target)
                adapter.plan['python_locks'] = {course['kernel']: course['kernel'] + '.requirements.lock'}
                with self.assertRaisesRegex(ValueError, 'reviewed locale lock'):
                    adapter.environment(source, course, 'kk-Cyrl')
        key = course['kernel'] + '.kk-Cyrl'
        adapter.plan['python_locks'][key] = key + '.requirements.lock'
        self.assertEqual(adapter.environment(source, course, 'kk-Cyrl', require_installed=False), kk / '.venv')
        with self.assertRaisesRegex(ValueError, 'install the locked language environment'):
            adapter.environment(source, course, 'kk-Cyrl')
        (kk / '.venv/bin').mkdir(parents=True); (kk / '.venv/bin/python').touch()
        self.assertEqual(adapter.environment(source, course, 'kk-Cyrl'), kk / '.venv')
        (kk / '.venv/bin/python').unlink(); (kk / '.venv/bin').rmdir(); (kk / '.venv').rmdir()
        (en / '.venv').mkdir(); (kk / '.venv').symlink_to(en / '.venv', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'must be its own directory'):
            adapter.environment(source, course, 'kk-Cyrl', require_installed=False)

    def test_kernel_handoff_keeps_the_connection_arguments_for_the_locale_interpreter(self):
        spec = importlib.util.spec_from_file_location('language_kernel_handoff', runtime.RUNTIME / 'kernel.py')
        kernel = importlib.util.module_from_spec(spec); spec.loader.exec_module(kernel)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); shared = root / 'source'; workspace = root / 'workspace'
            course = {'course_dir': 'doris-course/example', 'slug': 'example'}
            en = shared / course['course_dir']; en.mkdir(parents=True)
            kk = shared / 'doris-course/kk-Cyrl/example'; kk.mkdir(parents=True)
            environment = kk / '.venv'; (environment / 'bin').mkdir(parents=True)
            (environment / 'bin/python').touch()
            plan = root / 'runtime.json'; plan.write_text(json.dumps({'courses': [course]}))
            content = SimpleNamespace(course_files=True, links={'en': {}, 'kk-Cyrl': {}}, workspace=workspace,
                source_course=lambda *_: 'doris-course/kk-Cyrl/example', environment=lambda *_: environment)
            api = SimpleNamespace(Content=lambda *_: content, configure_quiz=lambda *_: None)
            argv = [str(runtime.RUNTIME / 'kernel.py'), '--course-dir', str(en), '-f', str(root / 'connection.json')]
            env = {'DORIS_WORKSHOP_CONTENT_ROOT': str(root), 'DORIS_WORKSHOP_RUNTIME_PLAN': str(plan),
                'DORIS_WORKSHOP_WORKSPACE': str(workspace), 'DORIS_WORKSHOP_SHARED_SOURCE': str(shared),
                'PATH': str(en / '.venv/bin') + ':/usr/bin'}
            with mock.patch.dict(sys.modules, {'multilingual': api}), mock.patch.object(sys, 'argv', argv), \
                    mock.patch.object(sys, 'path', list(sys.path)), mock.patch.dict(os.environ, env), \
                    mock.patch.object(Path, 'cwd', return_value=workspace / 'work/kk-Cyrl/doris-course/example/level1'), \
                    mock.patch.object(os, 'execv', side_effect=RuntimeError('exec sentinel')) as handoff:
                with self.assertRaisesRegex(RuntimeError, 'exec sentinel'):
                    kernel.main()
                handoff.assert_called_once_with(str(environment / 'bin/python'),
                    [str(environment / 'bin/python'), '-I', str((runtime.RUNTIME / 'kernel.py').resolve()), *argv[1:]])
                self.assertEqual(os.environ['VIRTUAL_ENV'], str(environment))
                self.assertNotIn(str(en / '.venv/bin'), os.environ['PATH'])


if __name__ == '__main__':
    unittest.main()
