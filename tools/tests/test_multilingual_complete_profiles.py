"""Complete-course selection, navigation and Quiz isolation, without services.

The manifests and Quiz banks come from the pinned local Git revision. Package
indexes and kk-Cyrl display text below are SYNTHETIC unit fixtures, not a Kazakh
translation or bilingual release admission. Only verify_tree is mocked: these
tests exercise the runtime's independent profile/path/Quiz checks and real local
file hashes. No Labs, Docker, Jupyter process or AWS operation is run.
"""
import copy
from html import unescape
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tools/tests'),
               str(ROOT / 'ui/workshop'), str(ROOT / 'doris-course/common')]
import workshop_config as wc
import workshop_content
import workshop_runtime as builder
from doris_labkit import progress
from doris_labkit.quizbank import load_quiz
from doris_labkit.quizsession import Presentation, QuizConflict
from multilingual_fixture import installed

consumer = workshop_content.consumer
_spec = importlib.util.spec_from_file_location('complete_profiles_portal', builder.RUNTIME / 'portal.py')
portal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(portal)

PROFILES = ['real-time-analytics-complete', 'data-warehousing-complete', 'observability-complete']
COUNTS = {'real-time-analytics': 10, 'data-warehousing': 14, 'observability': 8}
OPTIONALS = {
    PROFILES[0]: {'level1/module01-introduction/optional01_metabase_dashboard.ipynb'},
    PROFILES[1]: {
        'level1/extensions/extension23_physical_design.ipynb',
        'level1/module05-ingestion/optional05_continuous_ingestion.md',
        'level1/module05-ingestion/optional05_invoice_and_receipts.md',
        'level1/module05-ingestion/optional05_kafka_routine_load.ipynb',
        'level1/module05-ingestion/optional05_flink_mysql_cdc.ipynb',
        'level1/extensions/extension45_files_and_group_commit.ipynb',
        'level1/extensions/extension67_schema_and_delete.ipynb',
        'level1/module05-ingestion/quiz05_continuous_ingestion.ipynb',
    },
    PROFILES[2]: {'level2/module07-data-analysis-litefuse/optional07_trace_a_real_model.ipynb'},
}


def binding(ref, profiles=PROFILES):
    """Valid binding shape with deliberately synthetic content fingerprints."""
    return {'schema_version': 1, 'abi': consumer.ABI, 'capabilities': consumer.CAPABILITIES,
            'source_ref': ref, 'package_digest': '1' * 64, 'code_digest': '2' * 64,
            'dependency_digest': '3' * 64, 'available_locales': ['en', 'kk-Cyrl'],
            'default_locale': 'en', 'locale_digests': {'en': '4' * 64, 'kk-Cyrl': '5' * 64},
            'profiles': list(profiles)}


def config(ref, profiles=PROFILES, version=2):
    result = wc.load_document(wc.SPEC / 'examples/event.example.yaml')
    result.update(schema_version=version, course_ref=ref, course_profiles=list(profiles),
                  expires_at='2099-01-01T00:00:00Z')
    if version == 2:
        result['content'] = binding(ref, profiles)
    return result


class SyntheticPackage:
    """Real course identities/banks with minimal, explicitly synthetic assets."""
    def __init__(self, root, plan, manifests, source):
        self.root, self.plan = Path(root), copy.deepcopy(plan)
        self.files = {}
        self.package = {'profiles': [], 'assets': [], 'source_units': [], 'quizzes': [],
                        'courses': [], 'snapshot': {'files': []}}
        links = []
        for course in self.plan['courses']:
            manifest = manifests[course['slug']]
            profile = {'id': course['profiles'][0], 'modules': [], 'asset_ids': [], 'quiz_ids': []}
            self.package['profiles'].append(profile)
            self.package['courses'].append({'slug': course['slug'], 'policy_revision': 'synthetic-policy.1'})
            for locale in ('en', 'kk-Cyrl'):
                display = copy.deepcopy(manifest)
                if locale != 'en':
                    display['title'] = 'SYNTHETIC kk · ' + display['title']
                    for module in display['modules']:
                        module['title'] = 'SYNTHETIC kk · ' + module['title']
                        for part in module.get('optional', []):
                            part['title'] = 'SYNTHETIC kk · ' + part['title']
                self.write(f'rendered/{locale}/{course["course_dir"]}/course.json',
                           json.dumps(display).encode())
            for module in course['modules']:
                profile['modules'].append({'course_slug': course['slug'], 'module_id': module['id']})
                original = next(m for m in manifest['modules'] if m['id'] == module['id'])
                # Put the optional bank first: selecting only by module_id would
                # silently attach this bank to DW module-5's main Quiz.
                banks = [p['quiz'] for p in original.get('optional', []) if p.get('quiz')]
                for relative in [*banks, module['quiz']]:
                    path = course['course_dir'] + '/' + relative
                    document = wc.yaml.safe_load(source(path))
                    descriptor = {'id': 'q.' + consumer.digest(path.encode())[:32],
                        'source_path': path, 'course_slug': course['slug'], 'module_id': module['id'],
                        'semantic_revision': 'synthetic-meaning.1',
                        'passing_score_percent': document.get('passing_score_percent', 80),
                        'questions': [{'id': q['id'], 'option_ids': [o['id'] for o in q['options']],
                                       'answer': q['answer']} for q in document['questions']],
                        'presentations': []}
                    self.package['quizzes'].append(descriptor)
                    profile['quiz_ids'].append(descriptor['id'])
                    for locale in ('en', 'kk-Cyrl'):
                        text = copy.deepcopy(document)
                        if locale != 'en':
                            text['title'] = 'SYNTHETIC kk · ' + text['title']
                        raw = wc.yaml.safe_dump(text, allow_unicode=True).encode()
                        target = f'rendered/{locale}/{path}'
                        self.write(target, raw)
                        descriptor['presentations'].append({'locale': locale,
                            'presentation_revision': consumer.digest(raw)})
                        links.append({'locale': locale, 'source_path': path, 'path': target})
            for relative in course['notebooks'] + [p['path'] for p in course['optional']]:
                path = course['course_dir'] + '/' + relative
                if path in self.files:
                    continue
                # Minimal non-executable templates are sufficient for selection.
                self.write(path, b'{}' if path.endswith('.ipynb') else b'# Synthetic optional reading\n')
                asset_id = 'a.' + consumer.digest(path.encode())[:32]
                self.package['assets'].append({'id': asset_id, 'locale': 'en', 'file': {'path': path}})
                profile['asset_ids'].append(asset_id)
                for locale in ('en', 'kk-Cyrl'):
                    links.append({'locale': locale, 'source_path': path, 'path': path})
        self.write('indexes/assets.json', json.dumps({'links': links}).encode())
        self.write('indexes/anchors.json', b'[]')
        self.package['snapshot']['files'] = list(self.files.values())

    def write(self, name, raw):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.files[name] = {'path': name, 'size': len(raw), 'sha256': consumer.digest(raw)}

    def adapter(self, plan=None):
        # This bypasses package admission only, never the selection checks under
        # test. It must not be used as evidence of a real bilingual release.
        with mock.patch.object(consumer, 'verify_tree', return_value=self.package):
            return consumer.Content(plan or self.plan, self.root, self.root / 'workspace')


class CompleteProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = wc.git(wc.REPO, 'rev-parse', 'HEAD').strip()
        cls.config = config(cls.ref)
        cls.courses = builder.course_selection(cls.config)
        cls.manifests = {c['slug']: wc.yaml.safe_load(wc.git(wc.REPO, 'show',
                         f'{cls.ref}:{c["course_dir"]}/course.yaml')) for c in cls.courses}
        cls.plan = {'schema_version': 2, 'course_ref': cls.ref, 'content': cls.config['content'],
                    'capabilities': consumer.CAPABILITIES,
                    'courses': [next(c for c in cls.courses if c['profiles'] == [p]) for p in PROFILES]}

    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-complete-profiles-')
        self.addCleanup(temporary.cleanup)
        return SyntheticPackage(temporary.name, self.plan, self.manifests,
                                lambda path: wc.git(wc.REPO, 'show', f'{self.ref}:{path}'))

    def test_complete_registry_and_manifest_closure_contains_all_32_modules_and_optionals(self):
        registry = consumer.profile_registry()
        self.assertEqual(set(registry), {*PROFILES, 'rta-module-1'})
        total = 0
        for course in self.courses:
            slug, pid = course['slug'], course['profiles'][0]
            with self.subTest(course=slug):
                expected_ids = [f'module-{n}' for n in range(1, COUNTS[slug] + 1)]
                self.assertEqual(registry[pid]['module_ids'], expected_ids)
                self.assertEqual([m['id'] for m in course['modules']], expected_ids)
                self.assertEqual([m['id'] for m in self.manifests[slug]['modules']], expected_ids)
                self.assertEqual(set(registry[pid]['optional_paths']), OPTIONALS[pid])
                parts = [p for m in self.manifests[slug]['modules'] for p in m.get('optional', [])]
                self.assertEqual(course['optional'], parts)
                expected_paths = {p['path'] for p in parts}
                expected_paths.update(str(Path(p['quiz']).with_suffix('.ipynb')) for p in parts if p.get('quiz'))
                self.assertEqual(expected_paths, OPTIONALS[pid])
                total += len(course['modules'])
        self.assertEqual(total, 32)

    def test_notebooks_include_main_shells_all_electives_and_extensions_but_no_markdown(self):
        counts = {}
        for course in self.courses:
            pid = course['profiles'][0]
            expected = {m['lab'] for m in course['modules']}
            expected.update(str(Path(m['quiz']).with_suffix('.ipynb')) for m in course['modules'])
            expected.update(p for p in OPTIONALS[pid] if p.endswith('.ipynb'))
            self.assertEqual(set(course['notebooks']), expected)
            self.assertEqual(len(course['notebooks']), len(expected))
            self.assertEqual(course['extensions'], course['slug'] == 'data-warehousing')
            counts[course['slug']] = len(course['notebooks'])
        self.assertEqual(counts, {'real-time-analytics': 21, 'data-warehousing': 34, 'observability': 17})

    def test_producer_complete_profiles_select_every_manifest_optional_at_its_own_module(self):
        legacy = ROOT / 'locales/producer.json'
        definitions = (json.loads(legacy.read_text()) if legacy.is_file() else
                       wc.load_document(ROOT / 'locales/course-files.yaml'))
        declared = {p['id']: p for p in definitions['profiles']}
        for course in self.courses:
            profile = declared[course['profiles'][0]]
            manifest = self.manifests[course['slug']]
            self.assertEqual([(m['course_slug'], m['module_id']) for m in profile['modules']],
                             [(course['slug'], m['id']) for m in manifest['modules']])
            for selected, module in zip(profile['modules'], manifest['modules']):
                with self.subTest(course=course['slug'], module=module['id']):
                    self.assertEqual(selected['optional_paths'],
                                     [p['path'] for p in module.get('optional', [])])

    def test_v2_course_order_follows_the_bound_profile_order(self):
        for order in (PROFILES, list(reversed(PROFILES))):
            with self.subTest(order=order):
                selected = builder.course_selection(config(self.ref, order))
                self.assertEqual([c['profiles'][0] for c in selected], order)

    def test_v2_normalization_keeps_binding_order_and_input_unchanged(self):
        before = copy.deepcopy(self.config)
        result, _, _ = wc.validate_config(self.config)
        self.assertEqual(self.config, before)
        self.assertEqual(result['course_profiles'], PROFILES)
        self.assertEqual(result['content']['profiles'], PROFILES)
        changed = copy.deepcopy(self.config)
        changed['course_profiles'].reverse()
        with self.assertRaisesRegex(wc.ConfigError, 'profiles must match'):
            wc.validate_config(changed)

    def test_binding_rejects_duplicate_unknown_overlap_and_english_only_profiles(self):
        for profiles in ([], [PROFILES[0], PROFILES[0]], ['unknown'], ['rta-main'],
                         ['rta-module-1', PROFILES[0]]):
            with self.subTest(profiles=profiles), self.assertRaises(ValueError):
                consumer.validate_binding(binding(self.ref, profiles))
        value = binding(self.ref)
        value['available_locales'] = ['en']; value['locale_digests'].pop('kk-Cyrl')
        with self.assertRaises(ValueError):
            consumer.validate_binding(value)
        with self.assertRaises(wc.ConfigError):
            wc.validate_config(config(self.ref, ['rta-module-1', PROFILES[0]]))

    def test_runtime_rejects_missing_duplicate_wrong_course_or_module_selection(self):
        mutations = [lambda p: p['courses'].pop(),
                     lambda p: p['courses'].__setitem__(1, copy.deepcopy(p['courses'][0])),
                     lambda p: p['courses'][0].update(kernel='doris-ai'),
                     lambda p: p['courses'][0]['modules'].pop(),
                     lambda p: p['courses'][0]['modules'].reverse(),
                     lambda p: p['courses'][1].update(extensions=False),
                     lambda p: p['courses'][0]['optional'].append({'path': 'extra.ipynb'})]
        consumer.validate_plan(self.plan)
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index), self.assertRaises(ValueError):
                value = copy.deepcopy(self.plan); mutate(value); consumer.validate_plan(value)

    def test_runtime_rejects_course_order_different_from_its_binding(self):
        value = copy.deepcopy(self.plan); value['courses'].reverse()
        with self.assertRaises(ValueError):
            consumer.validate_plan(value)

    def test_content_rejects_extra_missing_notebook_and_missing_or_changed_optional(self):
        fixture = self.fixture()
        self.assertEqual(len(fixture.adapter().notebooks), 72)
        mutations = [lambda p: p['courses'][0]['notebooks'].append('extra.ipynb'),
                     lambda p: p['courses'][1]['notebooks'].remove('level1/module05-ingestion/quiz05_continuous_ingestion.ipynb'),
                     lambda p: p['courses'][1]['optional'].pop(),
                     lambda p: p['courses'][1]['optional'][0].update(title='Changed contract'),
                     lambda p: p['courses'][0]['modules'][0].update(lab='other.ipynb'),
                     lambda p: p['courses'][0].update(slug='different-course')]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index), self.assertRaises(ValueError):
                value = copy.deepcopy(self.plan); mutate(value); fixture.adapter(value)

    def test_content_rejects_profile_missing_from_the_package(self):
        fixture = self.fixture(); fixture.package['profiles'].pop()
        with self.assertRaisesRegex(ValueError, 'selected profile is absent'):
            fixture.adapter()

    def test_main_and_optional_quiz_bindings_use_exact_path_with_the_same_module_id(self):
        fixture = self.fixture(); enriched = fixture.adapter().enrich()
        dw = next(c for c in enriched['courses'] if c['slug'] == 'data-warehousing')
        main = next(m for m in dw['modules'] if m['id'] == 'module-5')
        optional = next(p for p in dw['optional'] if p.get('quiz'))
        self.assertNotEqual(main['quiz_binding']['id'], optional['quiz_binding']['id'])
        self.assertEqual(main['quiz_binding']['module_id'], optional['quiz_binding']['module_id'])
        for course in enriched['courses']:
            for part in [*course['modules'], *course['optional']]:
                if not part.get('quiz'):
                    continue
                self.assertEqual(part['quiz_binding']['source_path'], course['course_dir'] + '/' + part['quiz'])
                self.assertEqual([p['locale'] for p in part['quiz_presentations']], ['en', 'kk-Cyrl'])
                self.assertEqual(len(part['quiz_binding']['questions']), 6)

    def test_cross_course_progress_ids_are_separate_and_optional_cannot_grant_main_credit(self):
        fixture = self.fixture(); enriched = fixture.adapter().enrich()
        modules = progress.registry(enriched)
        self.assertEqual(len(modules), 32)
        self.assertEqual({slug: sum(k[0] == slug for k in modules) for slug in COUNTS}, COUNTS)
        plan_path, db = fixture.root / 'runtime.json', fixture.root / 'progress.sqlite3'
        plan_path.write_text(json.dumps(enriched))
        env = {'DORIS_WORKSHOP_PROGRESS_DB': str(db), 'DORIS_WORKSHOP_PROGRESS_PLAN': str(plan_path),
               'DORIS_WORKSHOP_PROGRESS_SOURCE': str(fixture.root)}

        def submit(part):
            entry = next(p for p in part['quiz_presentations'] if p['locale'] == 'kk-Cyrl')
            path = fixture.root / entry['path']
            view = Presentation.admitted(load_quiz(path, localized=True), part['quiz_binding'],
                locale=entry['locale'], policy_revision=part['policy_revision'], source_digest=entry['source_digest'])
            answers = {q.question_id: q.answer for q in view.quiz.questions}
            return progress.save_submission_from_environment(path, view, uuid.uuid4().hex, answers)

        with mock.patch.dict(os.environ, env):
            for course in enriched['courses']:
                self.assertEqual(submit(course['modules'][0])['attempt']['locale'], 'kk-Cyrl')
            store = progress.Store(db)
            before = store.records(), store.attempts()
            self.assertEqual(set(before[0]), {(s, 'module-1') for s in COUNTS})
            optional = next(p for c in enriched['courses'] for p in c['optional'] if p.get('quiz'))
            self.assertEqual(submit(optional), {'optional': True})
            self.assertEqual((store.records(), store.attempts()), before)
            entry = next(p for p in optional['quiz_presentations'] if p['locale'] == 'kk-Cyrl')
            # Leave the loaded binding untouched, change only installed bytes.
            path = fixture.root / entry['path']
            original_loader = progress.save_submission_from_environment
            view = Presentation.admitted(load_quiz(path, localized=True), optional['quiz_binding'],
                locale='kk-Cyrl', policy_revision=optional['policy_revision'], source_digest=entry['source_digest'])
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaises(QuizConflict):
                original_loader(path, view, uuid.uuid4().hex, {q.question_id: q.answer for q in view.quiz.questions})
            self.assertEqual((store.records(), store.attempts()), before)

    def test_outline_keeps_all_courses_and_markdown_elective_links_in_the_selected_locale(self):
        fixture = self.fixture(); adapter = fixture.adapter()
        ui = installed(content=adapter.resolve, labels=adapter.labels)
        handler = SimpleNamespace(plan=self.plan, progress=None, content_adapter=adapter,
                                  installed_ui=ui, base_url='/', body=None)
        handler.ui = portal.Renderer(context=ui.context(explicit='kk-Cyrl'),
                                     asset_base='/workshop/static', home='/workshop/')
        for name in ('notebook', 'module_view', 'course_links'):
            setattr(handler, name, MethodType(getattr(portal.PortalHandler, name), handler))
        handler.page = lambda body: setattr(handler, 'body', body)
        portal.IndexHandler.get.__wrapped__(handler)
        self.assertEqual(handler.body.count('<article class="module">'), 32)
        self.assertEqual(handler.body.count('<section class="course-outline">'), 3)
        positions = [handler.body.index('SYNTHETIC kk · ' + self.manifests[s]['title']) for s in COUNTS]
        self.assertEqual(positions, sorted(positions))

        class Links(HTMLParser):
            def __init__(self):
                super().__init__(); self.urls = []
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.urls.extend(v for k, v in attrs if k == 'href')

        links = Links(); links.feed(handler.body)
        for course in self.plan['courses']:
            for part in course['optional']:
                route = '/workshop/notebook/' if part['path'].endswith('.ipynb') else '/workshop/content/'
                target = route + course['course_dir'] + '/' + part['path'] + '?locale=kk-Cyrl'
                self.assertIn(target, [unescape(url) for url in links.urls])

    def test_v1_and_original_pilot_keep_their_existing_scope(self):
        legacy = config(self.ref, ['rta-main', 'dw-main', 'obs-main'], version=1)
        courses = builder.course_selection(legacy)
        self.assertEqual(sum(len(c['modules']) for c in courses), 32)
        consumer.validate_plan({'schema_version': 1, 'courses': courses})
        for field, value in [('content', binding(self.ref)), ('capabilities', consumer.CAPABILITIES)]:
            with self.assertRaises(ValueError):
                consumer.validate_plan({'schema_version': 1, field: value})
        pilot = config(self.ref, ['rta-module-1'])
        courses = builder.course_selection(pilot)
        plan = {'schema_version': 2, 'course_ref': self.ref, 'courses': courses,
                'content': pilot['content'], 'capabilities': consumer.CAPABILITIES}
        consumer.validate_plan(plan)
        self.assertEqual([m['id'] for m in courses[0]['modules']], ['module-1'])
        self.assertEqual(len(courses[0]['notebooks']), 2)
        self.assertEqual(courses[0]['optional'], [])
        self.assertFalse(courses[0]['extensions'])
        with self.assertRaises(wc.ConfigError):
            wc.resolve_profiles(config(self.ref, ['rta-module-1'], version=1))

    def test_pilot_can_coexist_with_other_courses_without_expanding_its_rta_scope(self):
        profiles = ['rta-module-1', PROFILES[1], PROFILES[2]]
        value = config(self.ref, profiles)
        normalized, _, _ = wc.validate_config(value)
        courses = builder.course_selection(normalized)
        consumer.validate_plan({'schema_version': 2, 'course_ref': self.ref, 'courses': courses,
                                'content': value['content'], 'capabilities': consumer.CAPABILITIES})
        self.assertEqual([c['profiles'][0] for c in courses], profiles)
        self.assertEqual([len(c['modules']) for c in courses], [1, 14, 8])
        self.assertEqual(courses[0]['optional'], [])
        self.assertEqual(len(courses[0]['notebooks']), 2)


if __name__ == '__main__':
    unittest.main()
