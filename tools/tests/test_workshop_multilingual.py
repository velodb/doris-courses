"""Production v2 admission and copy preservation using a real local teaching release."""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS.parent / 'workshop/aws/runtime'), str(TOOLS.parent / 'ui/workshop')]
import content_producer as producer
import workshop_config as wc
import workshop_runtime as builder
import workshop_content as content
import workshop_release as release
import host
import workshop_preflight as preflight
import workshop_image as image
import workshop_course
from i18n import LocaleUnavailable
import importlib.util
_portal_spec = importlib.util.spec_from_file_location('multilingual_runtime_portal', builder.RUNTIME / 'portal.py')
_portal = importlib.util.module_from_spec(_portal_spec)
_portal_spec.loader.exec_module(_portal)
render_markdown, rewrite_link = _portal.render_markdown, _portal.rewrite_link


class MultilingualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ml05-tests-')
        cls.base = Path(cls.temp.name)
        # Historical v2 remains testable after native language files replace
        # overlays on HEAD. An explicit caller override always takes priority.
        legacy = 'dfa31100d936cf1262648df44542a8a2e0c35dee'
        default_ref = (wc.git(wc.REPO, 'rev-parse', 'HEAD').strip()
                       if (wc.REPO / 'locales/kk-Cyrl/overlay.json').is_file() else legacy)
        cls.ref = os.environ.get('ML_CONTENT_SOURCE_REF', default_ref)
        cls.package = cls.base / 'package'
        producer.build(producer.GitSource(wc.REPO, cls.ref), cls.package, ['rta-module-1'], ['en', 'kk-Cyrl'])
        cls.binding = content.admit(cls.package)
        cls.config = wc.load_document(wc.SPEC / 'examples/event.example.yaml')
        cls.config.update(schema_version=2, course_ref=cls.ref, course_profiles=['rta-module-1'], content=cls.binding)
        cls.path = cls.base / 'config.json'
        cls.path.write_text(json.dumps(cls.config))
        cls.bundle = cls.base / 'bundle'
        cls.plan = builder.create_bundle(cls.path, cls.bundle, content_package=cls.package)

    @classmethod
    def tearDownClass(cls):
        for path in cls.base.rglob('*'):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        cls.temp.cleanup()

    def setUp(self):
        self.directory = self.base / self.id().rsplit('.', 1)[-1]
        self.layout = host.Layout(self.directory)
        files = host.install_source(self.bundle, self.plan, self.layout)
        host.install_content(self.bundle, self.plan, self.layout)
        host.materialize(self.plan, files, self.layout)
        self.adapter = content.consumer.Content(self.plan, self.layout.content, self.layout.workspace)
        self.lab = next(p for p in self.adapter.notebooks if '/lab' in p)

    def test_explicit_versions_and_untranslated_optional(self):
        config, profiles, runtime = wc.validate_config(self.config)
        manifest = wc.build_manifest(config, profiles, runtime)
        self.assertEqual(manifest['schema_version'], 2)
        self.assertEqual([m['id'] for m in profiles['rta-module-1']['modules']], ['module-1'])
        self.assertEqual(manifest['desired']['content'], self.binding)
        old = copy.deepcopy(config); old.pop('content'); old['schema_version'] = 1; old['course_profiles'] = ['rta-main']
        self.assertEqual(len(wc.resolve_profiles(old)[0]['rta-main']['modules']), 10)
        for field, bad in [('available_locales', ['en']), ('abi', 'unknown'), ('profiles', ['rta-main'])]:
            changed = copy.deepcopy(config); changed['content'][field] = bad
            with self.assertRaises(wc.ConfigError): wc.validate_config(changed)
        optional = self.lab.rsplit('/', 1)[0] + '/optional01_metabase_dashboard.ipynb'
        with self.assertRaises(LocaleUnavailable): self.adapter.resolve(optional, 'notebook', 'kk-Cyrl')
        with self.assertRaises(ValueError):
            wc.build_manifest(*wc.validate_config(config), previous={**manifest, 'schema_version': 1})

    def test_complete_bundle_and_exact_retry(self):
        self.assertEqual(host.verify_bundle(self.bundle)[0], self.plan)
        self.assertFalse(list(self.layout.workspace.rglob('*.ipynb')))
        host.install_content(self.bundle, self.plan, self.layout)
        bad = copy.deepcopy(self.plan); bad['schema_version'] = 1
        with self.assertRaises(ValueError): content.consumer.validate_plan(bad)
        bad = copy.deepcopy(self.plan); bad['content']['locale_digests']['kk-Cyrl'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'binding differs'):
            content.consumer.verify_tree(self.layout.content, bad['content'])
        with self.assertRaisesRegex(wc.ConfigError, 'already exists'):
            builder.create_bundle(self.path, self.bundle, content_package=self.package)
        with self.assertRaisesRegex(wc.ConfigError, 'requires --content-package'):
            builder.create_bundle(self.path, self.directory / 'missing')

    def test_two_languages_concurrent_open_and_edited_outputs_survive(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            copies = list(pool.map(lambda _: self.adapter.copy(self.lab, 'kk-Cyrl'), range(4)))
        self.assertEqual(len(set(copies)), 1)
        kk = self.layout.workspace / copies[0]
        english = self.layout.workspace / self.adapter.copy(self.lab, 'en')
        self.assertNotEqual(kk, english)
        notebook = json.loads(kk.read_text()); codes = [c for c in notebook['cells'] if c['cell_type'] == 'code']
        template = json.loads((self.layout.content / self.adapter.resolve(self.lab, 'notebook', 'kk-Cyrl')[0]).read_text())
        self.assertEqual(codes, [c for c in template['cells'] if c['cell_type'] == 'code'])
        self.assertEqual([c['id'] for c in notebook['cells']], [c['id'] for c in template['cells']])
        codes[0]['outputs'] = [{'output_type': 'stream', 'name': 'stdout', 'text': ['preserved']}]
        codes[0]['execution_count'] = 123
        codes[0]['source'] = ['edited by learner']
        kk.write_text(json.dumps(notebook)); before = kk.read_bytes()
        self.adapter.copy(self.lab, 'en'); self.adapter.copy(self.lab, 'kk-Cyrl')
        host.materialize(self.plan, host.archive_files(self.bundle / 'source.tar'), self.layout)
        host.install_content(self.bundle, self.plan, self.layout)
        self.assertEqual(kk.read_bytes(), before)
        self.assertEqual((self.layout.content / self.adapter.resolve(self.lab, 'notebook', 'kk-Cyrl')[0]).stat().st_mode & 0o222, 0)
        self.assertEqual(self.adapter.ui().context(explicit='kk-Cyrl').locale, 'kk-Cyrl')

    def test_source_fragments_images_and_immutable_downloads(self):
        context = self.adapter.ui().context(explicit='kk-Cyrl')
        path = self.plan['courses'][0]['course_dir'] + '/' + self.plan['courses'][0]['modules'][0]['course']
        resolved, actual = self.adapter.resolve(path, 'lesson', 'kk-Cyrl')
        text = (self.layout.content / resolved).read_text().split('\n', 1)[1]
        html = render_markdown(text, path, '/', context, aliases=self.adapter.aliases(path), adapter=self.adapter)
        self.assertIn('id="module-goal"', html)
        self.assertIn('id="unit-u.', html)
        self.assertIn('locale=kk-Cyrl', html)
        guide = self.plan['courses'][0]['course_dir'] + '/README.md'
        asset = rewrite_link('datasets/events.yaml', guide, '/', context, self.adapter)
        self.assertIn('/workshop/artifact/', asset)
        mirror = self.lab.replace('module01-introduction/lab01_start_doris_and_build_baseline.ipynb', 'module02-architecture/course.md')
        self.assertEqual(self.adapter.resolve(mirror, 'lesson', 'kk-Cyrl')[1], 'en')
        with self.assertRaises(ValueError): content.consumer.safe(self.layout.content, '../escape')

    def test_tampered_hash_symlink_and_quiz_binding(self):
        enriched = self.adapter.enrich()['courses'][0]['modules'][0]
        self.assertEqual({p['locale'] for p in enriched['quiz_presentations']}, {'en', 'kk-Cyrl'})
        path = self.layout.content / enriched['quiz_presentations'][1]['path']
        path.chmod(0o644); path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            content.consumer.Content(self.plan, self.layout.content, self.layout.workspace)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            host.install_content(self.bundle, self.plan, self.layout)

    def test_legacy_release_cannot_advertise_v2(self):
        from test_workshop_release import fixture
        # Use the public checked example shape, not any live image or account.
        example = fixture()
        example['schema_version'] = 1
        p = self.directory / 'legacy-release.json'; p.write_text(json.dumps(example))
        with self.assertRaises(wc.ConfigError): release.load_release(p, self.config)

    def test_offline_preflight_binds_the_complete_release(self):
        config = copy.deepcopy(self.config)
        config['image']['owner_account_id'] = preflight.CANONICAL
        pilot = wc.load_document(wc.SPEC / 'examples/pilot.example.yaml')
        pilot['runtime_bundle_dir'] = str(self.bundle)
        self.assertEqual(preflight.validate_inputs(config, pilot)[0]['content'], self.binding)
        config['content']['locale_digests']['kk-Cyrl'] = 'f' * 64
        with self.assertRaisesRegex(wc.ConfigError, 'binding changed'):
            preflight.validate_inputs(config, pilot)

    def test_v2_image_inputs_and_release_examples_are_explicit(self):
        config = copy.deepcopy(self.config); config['image']['owner_account_id'] = preflight.CANONICAL
        p = self.directory / 'image-config.json'; p.write_text(json.dumps(config))
        output = self.directory / 'image-bundle'
        inputs = image.prepare(p, output, content_package=self.package)
        self.assertEqual(inputs['schema_version'], 2)
        self.assertEqual(inputs['content'], self.binding)
        self.assertEqual([r['tag'] for r in inputs['images']['images']], ['apache/doris:all-in-one-4.1.3'])
        host.verify_bundle(output)
        wc.schema_check(wc.load_document(wc.SPEC / 'examples/multilingual.example.json'), 'config.schema.json')
        example = wc.load_document(wc.SPEC / 'examples/image-release.v2.example.json')
        wc.schema_check(example, 'image-release.schema.json')
        example['schema_version'] = 2.0
        with self.assertRaises(wc.ConfigError): wc.schema_check(example, 'image-release.schema.json')

    def test_profile_paths_and_installed_symlinks_fail_closed(self):
        changed = copy.deepcopy(self.plan)
        changed['courses'][0]['modules'][0]['lab'] = 'unselected.ipynb'
        with self.assertRaisesRegex(ValueError, 'runtime paths differ'):
            content.consumer.Content(changed, self.layout.content, self.layout.workspace)
        self.layout.content.chmod(0o755)
        (self.layout.content / 'extra').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            content.consumer.verify_tree(self.layout.content, self.binding)
        (self.layout.content / 'extra').unlink()

    def test_image_course_report_requires_every_installed_language(self):
        course, steps = workshop_course.selection(self.config)
        rows = [{**s, 'passed': True, 'code_cells': sum(c['cell_type'] == 'code' for c in json.loads(wc.git(wc.REPO, 'show',
                 f"{self.ref}:{course['course_dir']}/{s['notebook']}"))['cells']), 'errors': 0, 'enabled_opt_ins': [],
                 'source_sha256': content.consumer.digest(wc.git(wc.REPO, 'show', f"{self.ref}:{course['course_dir']}/{s['notebook']}").encode())} for s in steps]
        count = sum(r['code_cells'] for r in rows)
        report = {'schema_version': 3, 'content': self.binding, 'notebook_count': 4, 'code_cells': count * 2,
                  'language_reports': {tag: {'locale': tag, 'course_profiles': ['rta-module-1'], 'notebooks': rows,
                  'modules': [{'module': 'module-1', 'passed': True}], 'notebook_count': 2, 'code_cells': count,
                  'passed': True} for tag in ['en', 'kk-Cyrl']}}
        self.assertTrue(workshop_course.verify_report(self.config, report))
        del report['language_reports']['kk-Cyrl']
        self.assertFalse(workshop_course.verify_report(self.config, report))

    def test_entry_and_gateway_project_only_bound_messages_with_bounded_uploads(self):
        import asyncio
        import workshop_access as access
        import workshop_gateway_deploy as deploy
        import workshop_portal_update as update
        import test_workshop_portal as fixture
        from aiohttp.test_utils import TestClient, TestServer
        from i18n import InstalledUI
        snapshot = content.ui_from_bundle(self.bundle, self.binding)
        self.assertEqual(InstalledUI.from_snapshot(snapshot).default, 'en')
        plan, identity, subjects = fixture.inputs()
        config = {**self.config, 'event_id': 'class-a', 'region': 'us-east-1', 'domain': fixture.DOMAIN,
                  'learner_ids': ['alice', 'bob'], 'expires_at': wc.utc_text(wc.timestamp(self.config['expires_at'], 'expires_at'))}
        plan = access.routes(config, plan['terraform']['access'], plan['terraform']['learners'], runtime_bundle=self.bundle)
        self.assertEqual(plan['ui_snapshot'], snapshot)
        self.assertEqual({key.split(':')[1] for key in plan['terraform']['routes']}, {'jupyter', 'rta-fe'})
        registry = access.portal_config([plan], [identity], [subjects])
        self.assertEqual(registry['schema_version'], 2)
        gateways = access.gateway_configs(plan, identity, subjects)
        self.assertEqual(gateways['alice']['schema_version'], 2)
        for assets, languages in [(True, False), (False, False), (False, True)]:
            commands, _ = deploy.payload(plan, gateways['alice'], 'alice', assets=assets, languages=languages)
            self.assertLess(len(json.dumps(commands)), 24000)
        before = fixture.config()
        after = copy.deepcopy(before); after['schema_version'] = 2
        after['activities']['class-a']['ui_snapshot'] = snapshot
        commands, _, _ = update.remote_commands(json.dumps(before).encode(), json.dumps(after).encode())
        self.assertLess(len(json.dumps(commands)), 24000)
        async def requests():
            verifiers = {event: fixture.LocalVerifier({**a, 'region': after['region'], 'alb_arn': after['alb_arn']}, None)
                         for event, a in after['activities'].items()}
            client = TestClient(TestServer(fixture.portal.application(after, verifiers=verifiers)))
            await client.start_server()
            try:
                response = await client.get('/doris-courses/?locale=kk-Cyrl', headers={
                    'Host': 'class-a-entry.' + after['domain'], 'X-Amzn-Oidc-Data': fixture.token()})
                self.assertEqual(response.status, 200)
                self.assertIn('lang="kk-Cyrl"', await response.text())
                response = await client.get('/doris-courses/?locale=kk-Cyrl', headers={
                    'Host': 'class-b-entry.' + after['domain'], 'X-Amzn-Oidc-Data': fixture.token(event='class-b')})
                self.assertEqual(response.status, 406)
            finally:
                await client.close()
        asyncio.run(requests())


if __name__ == '__main__':
    unittest.main()
