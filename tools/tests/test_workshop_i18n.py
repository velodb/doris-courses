"""Locale selection, message safety and authenticated consumer boundaries."""
import asyncio
from html import unescape
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import subprocess
import unittest

from multilingual_fixture import installed, ROOT
from i18n import InstalledUI, LocaleUnavailable, Messages, with_locale
from doris_workshop_ui import Renderer


class MessageTests(unittest.TestCase):
    def test_complete_dictionaries_and_exact_typed_parameters(self):
        en = Messages()
        self.assertIn('1 matching module.', en.plural('import.saved', 1))
        self.assertIn('2 matching modules.', en.plural('import.saved', 2))
        for kwargs in ({}, {'count': True}, {'count': -1}, {'count': '2'}, {'count': 2, 'token': 'private'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                en('import.saved.other', **kwargs)
        for change in ({'quiz.text-1': ''}, {'quiz.text-15': '{number.__class__}'}, {'quiz.text-15': '{number!r}'}, {'quiz.text-15': '{number:100}'}):
            with self.assertRaises(ValueError):
                Messages('kk-Cyrl', messages={**en.source, **change})
        with self.assertRaises(ValueError):
            Messages('kk-Cyrl', messages={})
        for contract in ({}, {'version': True, 'parameters': {}, 'plurals': {}}, {'version': 1, 'parameters': [], 'plurals': {}}):
            with self.assertRaises(ValueError):
                Messages(contract=contract)

    def test_only_known_static_diagnostics_reach_the_page(self):
        target = installed().context(explicit='kk').messages
        self.assertIn('ӘҒҚҢӨҰҮҺІ', target.error(ValueError('Unexpected progress file fields.')))
        self.assertEqual(target.error(OSError('/private/token=secret')), target('import.invalid'))

    def test_html_parameters_and_translations_are_inert(self):
        messages = Messages('kk-Cyrl', messages={**Messages().source, 'workspace.assigned': '<script>{learner}</script>'})
        value = messages.html('workspace.assigned', learner='<img src=x onerror=alert(1)>')
        self.assertNotIn('<', value)
        r = Renderer(asset_base='/workshop/static', home='/workshop/', context=installed().context(explicit='kk'))
        page = r.workspace(event='<script>', learner='a"<img>', course='/workshop/', tools=[], extra=[], logout='/logout')
        self.assertNotIn('<img>', page)
        self.assertIn('a&quot;&lt;img&gt;', page)
        self.assertIn('lang="kk-Cyrl"', page)
        self.assertIn('aria-label="ӘҒҚҢӨҰҮҺІ', page)

    def test_state_pages_and_inventory_use_canonical_keys(self):
        context = installed().context(explicit='kk')
        r = Renderer(asset_base='/workshop/static', home='/workshop/', context=context)
        for key, row in r.copy.items():
            if 'destination' not in row:
                continue  # The offline login-error preview is not a runtime state.
            page = r.state(key)
            self.assertIn(context.messages.html(f'state.{key}.title'), page)
            self.assertNotIn(f'>state.{key}.title<', page)
        spec = importlib.util.spec_from_file_location('check_ui_messages', ROOT / 'tools/check_ui_messages.py')
        checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
        self.assertEqual(json.loads(checker.INVENTORY.read_text()), checker.inventory())

    def test_ui_archive_contains_exact_canonical_inputs_and_runs_alone(self):
        spec = importlib.util.spec_from_file_location('ui_package', ROOT / 'ui/workshop/package.py')
        package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)
        with tarfile.open(fileobj=io.BytesIO(package.archive(offline=True))) as archive:
            self.assertEqual(archive.extractfile('ui.en.json').read(), (ROOT / 'locales/ui.en.json').read_bytes())
            self.assertEqual(archive.extractfile('ui.contract.json').read(), (ROOT / 'locales/ui.contract.json').read_bytes())
            self.assertEqual(archive.extractfile('workshop_messages.py').read(), (ROOT / 'doris-course/common/doris_labkit/messages.py').read_bytes())
        self.assertEqual(package.archive(), package.archive())
        with tempfile.TemporaryDirectory() as temporary:
            with tarfile.open(fileobj=io.BytesIO(package.archive(offline=True))) as archive:
                archive.extractall(temporary, filter='data')
            run = subprocess.run([sys.executable, '-I', '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);from doris_workshop_ui import Renderer;'
                'print(len(Renderer(asset_base="/static",home="/workshop/").activities([])))', temporary],
                cwd=temporary, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertGreater(int(run.stdout.strip()), 100)



class LocaleTests(unittest.TestCase):
    def test_priority_aliases_and_weighted_browser_matching(self):
        ui = installed()
        self.assertEqual(ui.context(explicit='en-US', preference='kk').locale, 'en')
        self.assertEqual(ui.context(preference='kk-KZ', browser='en').locale, 'kk-Cyrl')
        self.assertEqual(ui.context(browser='fr;q=1,en;q=0.2,kk-KZ;q=0.9').locale, 'kk-Cyrl')
        self.assertEqual(ui.context(browser='kk;q=0,en;q=0.5').locale, 'en')
        self.assertEqual(ui.context(preference='retired', browser='kk').locale, 'kk-Cyrl')
        self.assertEqual(ui.context(browser='fr').locale, 'en')
        self.assertEqual(ui.context(browser='kk-Cyrl-KZ').locale, 'kk-Cyrl')
        self.assertEqual(ui.context(browser='en-AU').locale, 'en')
        for value in ('', 'fr', 'kk-Arab', '../../en'):
            with self.assertRaises(LocaleUnavailable):
                ui.context(explicit=value)
        with self.assertRaises(LocaleUnavailable):
            InstalledUI().context(explicit='kk')

    def test_preference_keys_are_stable_and_identity_scoped(self):
        ui = installed()
        a = ui.context(activity='a', learner='alice', scope='origin')
        self.assertEqual(a.preference_key, ui.context(activity='a', learner='alice', scope='origin').preference_key)
        for change in ({'activity': 'b'}, {'learner': 'bob'}, {'scope': 'other-origin'}):
            self.assertNotEqual(a.preference_key, ui.context(**({'activity': 'a', 'learner': 'alice', 'scope': 'origin'} | change)).preference_key)
        self.assertNotIn('alice', json.dumps(a.client()))

    def test_route_preserves_module_section_queries_and_fragments(self):
        self.assertEqual(with_locale('/workshop/lesson?module=m1&section=two&locale=en#u.fixed', 'kk-Cyrl'),
                         '/workshop/lesson?module=m1&section=two&locale=kk-Cyrl#u.fixed')
        self.assertEqual(with_locale('https://assigned.example/lab/tree/a.ipynb?x=1#cell', 'en'),
                         'https://assigned.example/lab/tree/a.ipynb?x=1&locale=en#cell')

    def test_content_adapter_reports_actual_locale_and_keeps_identity(self):
        calls = []
        def content(path, kind, locale):
            calls.append((path, kind, locale))
            return ('english.md', 'en') if path == 'mirror.md' else (f'installed/{locale}/{path}', locale)
        ctx = installed(content=content).context(explicit='kk')
        for kind in ('lesson', 'asset', 'quiz', 'notebook', 'progress'):
            self.assertEqual(ctx.resolve_content('unit', kind), ('installed/kk-Cyrl/unit', 'kk-Cyrl'))
        self.assertEqual(ctx.resolve_content('mirror.md', 'lesson'), ('english.md', 'en'))
        self.assertEqual(len(calls), 6)
        with self.assertRaises(LocaleUnavailable):
            installed().context(explicit='kk').resolve_content('lesson.md', 'lesson')

    def test_translated_heading_uses_the_same_stable_section_anchor(self):
        spec = importlib.util.spec_from_file_location('runtime_locale_renderer', ROOT / 'workshop/aws/runtime/portal.py')
        runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)
        ui = installed(anchors=lambda path, locale: ['u.fixed-section'])
        english = runtime.render_markdown('## Different English heading', 'lesson.md', '/', ui.context(explicit='en'))
        target = runtime.render_markdown('## Өзгерген тақырып', 'lesson.md', '/', ui.context(explicit='kk'))
        self.assertIn('id="u.fixed-section"', english)
        self.assertIn('id="u.fixed-section"', target)
        with self.assertRaises(LocaleUnavailable):
            runtime.render_markdown('## Target', 'lesson.md', '/', installed().context(explicit='kk'))
        with self.assertRaises(ValueError):
            runtime.render_markdown('## First\n\n## Second', 'lesson.md', '/', ui.context(explicit='kk'))

    def test_quiz_accepts_context_without_changing_answers_or_module_identity(self):
        sys.path.insert(0, str(ROOT / 'doris-course/common'))
        from doris_labkit.quiz import CourseQuiz
        path = next((ROOT / 'doris-course/01-real-time-analytics/level1').glob('*/quiz*.yaml'))
        en = CourseQuiz.from_yaml(path)
        target = CourseQuiz.from_yaml(path, messages=installed().context(explicit='kk').messages)
        self.assertEqual(en.questions, target.questions)
        self.assertEqual(en.source_path, target.source_path)
        target._render_question()
        self.assertIn('ӘҒҚҢӨҰҮҺІ', target._root.children[0].children[0].value)
        self.assertEqual(target._root.children[0].children[3].children[1].description, target.t('quiz.text-2'))
        target._render_results()
        self.assertIn('lang="kk-Cyrl"', target._root.children[0].children[0].value)


# Reuse the real signed-identity fixture, never test-supplied learner URL params.
import test_workshop_portal as portal_fixture
from aiohttp.test_utils import TestClient, TestServer


class PortalLocaleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        c = portal_fixture.config()
        self.client = TestClient(TestServer(portal_fixture.portal.application(c, installed_ui=installed(),
            verifiers={event: portal_fixture.LocalVerifier({**activity, 'region': c['region'], 'alb_arn': c['alb_arn']}, None)
                       for event, activity in c['activities'].items()})))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def fetch(self, query='', who='subject-alice', **headers):
        return await self.client.get('/doris-courses/' + query, headers={'Host': 'class-a-entry.workshops.example.com',
            'X-Amzn-Oidc-Data': portal_fixture.token(who), **headers})

    async def test_request_locale_is_not_shared_mutable_state(self):
        target, english = await asyncio.gather(self.fetch('?locale=kk'), self.fetch('?locale=en', who='subject-bob'))
        a, b = await target.text(), await english.text()
        self.assertIn('lang="kk-Cyrl"', a); self.assertIn('lang="en"', b)
        self.assertIn('alice', a); self.assertNotIn('alice', b)
        self.assertIn('/workshop/?locale=kk-Cyrl', a)
        self.assertNotIn('locale=kk-Cyrl', b)
        self.assertIn("script-src 'self'", target.headers['Content-Security-Policy'])
        self.assertIn('src="https://workshops.example.com/doris-courses/static/jupyter.svg"', a)

    async def test_unknown_language_is_explicitly_unavailable_and_queries_stay_closed(self):
        response = await self.fetch('?locale=fr')
        self.assertEqual(response.status, 406)
        self.assertIn('not available', await response.text())
        self.assertEqual((await self.fetch('?locale=en&locale=kk')).status, 400)
        self.assertEqual((await self.fetch('?learner=bob')).status, 400)
        self.assertEqual((await self.fetch('?locale=en', who='unassigned')).status, 403)

    async def test_browser_match_and_public_script_do_not_expose_assignments(self):
        response = await self.fetch('', **{'Accept-Language': 'kk-KZ'})
        self.assertIn('lang="kk-Cyrl"', await response.text())
        script = await self.client.get('/doris-courses/static/locale.js', headers={'Host': 'workshops.example.com'})
        self.assertEqual(script.status, 200)
        self.assertNotIn('subject-alice', await script.text())


class RecoveryLocaleTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_script_and_service_error_use_installed_messages(self):
        import test_workshop_access as fixture
        from aiohttp import web
        upstream_app = web.Application()
        async def upstream(request):
            return web.Response(text='<!doctype html><html><head></head><body>Lab</body></html>', content_type='text/html')
        upstream_app.router.add_get('/lab', upstream)
        server = TestServer(upstream_app)
        await server.start_server(); self.addAsyncCleanup(server.close)
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / 'token'; token.write_text('fixture-private-token')
            config = fixture.config(server.port)
            client = TestClient(TestServer(fixture.gateway.application(config, verifier=fixture.LocalVerifier(config, None),
                token_file=token, installed_ui=installed())))
            await client.start_server(); self.addAsyncCleanup(client.close)
            headers = {'Host': fixture.HOST, 'X-Amzn-Oidc-Data': fixture.token(), 'Accept': 'text/html'}
            response = await client.get('/_workshop/session_recovery.js?locale=kk-Cyrl', headers=headers)
            script = await response.text()
            self.assertEqual(response.status, 200)
            self.assertIn('workshopLocale="kk-Cyrl"', script)
            self.assertIn('\\u04', script)
            self.assertNotIn('fixture-private-token', script)
            self.assertIn('no-store', response.headers['Cache-Control'])
            response = await client.get('/lab?locale=kk-Cyrl', headers=headers)
            self.assertIn('session_recovery.js?locale=kk-Cyrl', await response.text())
            self.assertEqual((await client.get('/lab?locale=fr', headers=headers)).status, 406)
            self.assertEqual((await client.get('/_workshop/session?locale=kk&locale=en', headers=headers)).status, 400)
            await server.close()
            response = await client.get('/?locale=kk-Cyrl', headers=headers)
            self.assertEqual(response.status, 503)
            self.assertIn('lang="kk-Cyrl"', await response.text())
            self.assertNotIn('fixture-private-token', await response.text())


if __name__ == '__main__':
    unittest.main()
