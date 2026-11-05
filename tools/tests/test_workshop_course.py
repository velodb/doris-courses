"""Selected course evidence must cover opt-ins, every source file and every cell."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_config as wc
import workshop_course as course


class CourseContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = wc.load_document(wc.SPEC / 'examples/event.example.yaml')
        cls.config.update(course_ref=wc.git(wc.REPO, 'rev-parse', 'HEAD').strip(),
                          course_profiles=sorted(course._contract['DW_PROFILES']))
        cls.selected, cls.steps = course.selection(cls.config)
        cls.sources = {s['notebook']: wc.git(wc.REPO, 'show',
                       f"{cls.config['course_ref']}:{cls.selected['course_dir']}/{s['notebook']}").encode()
                       for s in cls.steps}

    def report(self):
        rows = [{**s, 'enabled_opt_ins': s['opt_ins'], 'passed': True, 'errors': 0,
                 'source_sha256': hashlib.sha256(self.sources[s['notebook']]).hexdigest(),
                 'code_cells': sum(c['cell_type'] == 'code' for c in json.loads(self.sources[s['notebook']])['cells'])}
                for s in self.steps]
        return {'course_profiles': self.config['course_profiles'], 'notebooks': rows,
                'modules': [{'module': s['module'], 'passed': True} for s in self.steps if s['kind'] == 'lab'],
                'notebook_count': len(rows), 'code_cells': sum(r['code_cells'] for r in rows)}

    def verify(self, report):
        return course.valid_selection_report(report, self.steps, self.config['course_profiles'], self.sources)

    def test_all_selected_files_have_one_execution_and_documented_opt_ins(self):
        self.assertEqual(len(self.steps), 34)
        self.assertEqual({s['module'] for s in self.steps}, {f'module-{i}' for i in range(1, 15)})
        self.assertEqual([f for s in self.steps for f in s['opt_ins']], ['BUILD_DASHBOARD', 'RUN_BACKUP_DRILL'])
        self.assertEqual(sum(s['kind'] == 'optional' for s in self.steps), 5)
        self.assertTrue(self.verify(self.report()))

    def test_incomplete_reordered_changed_or_skipped_execution_cannot_pass(self):
        for field, value in [('notebook_count', 28), ('code_cells', 0), ('course_profiles', ['dw-main'])]:
            report = self.report(); report[field] = value
            with self.subTest(field=field):
                self.assertFalse(self.verify(report))
        for key, value in [('passed', False), ('passed', 'true'), ('errors', 1), ('source_sha256', '0' * 64),
                           ('code_cells', 0), ('notebook', 'another.ipynb'), ('enabled_opt_ins', [])]:
            report = self.report()
            next(row for row in report['notebooks'] if row['opt_ins'])[key] = value
            with self.subTest(key=key):
                self.assertFalse(self.verify(report))
        report = self.report(); report['notebooks'].reverse()
        self.assertFalse(self.verify(report))

    def test_opt_ins_only_modify_the_private_execution_copy(self):
        import nbformat
        enable = course._contract['enable_opt_ins']
        for step in self.steps:
            if not step['opt_ins']: continue
            original = nbformat.reads(self.sources[step['notebook']].decode(), as_version=4)
            nb = copy.deepcopy(original)
            self.assertEqual(enable(nb, step['opt_ins']), step['opt_ins'])
            for flag in step['opt_ins']:
                self.assertTrue(any(flag + ' = True' in c.source for c in nb.cells))
                self.assertTrue(any(flag + ' = False' in c.source for c in original.cells))
            with self.assertRaises(ValueError): enable(nb, step['opt_ins'])

    def test_missing_optional_or_unsupported_course_is_refused(self):
        selected = copy.deepcopy(self.selected)
        selected['notebooks'].pop()
        with self.assertRaises(ValueError): course.selection_plan(selected)
        config = {**self.config, 'course_profiles': ['ai-functions']}
        with self.assertRaises(wc.ConfigError): course.selection(config)

    def test_resource_summary_uses_interval_cpu_and_minimum_available_capacity(self):
        first = dict(at=10, cpu_total=100, cpu_idle=50, memory_total_gib=32,
                     memory_available_gib=24, disk_total_gib=190, disk_available_gib=140,
                     network_rx_bytes=1000, network_tx_bytes=2000, load1=1)
        middle = {**first, 'at':12, 'cpu_total':200, 'cpu_idle':75,
                  'memory_available_gib':16, 'disk_available_gib':130, 'load1':4}
        last = {**first, 'at':14, 'cpu_total':300, 'cpu_idle':125,
                'network_rx_bytes':3000, 'network_tx_bytes':5000}
        summary = course._contract['summarize_resources']([first, middle, last])
        self.assertEqual(summary['maximum_cpu_busy_percent'], 75)
        self.assertEqual(summary['minimum_memory_available_gib'], 16)
        self.assertEqual(summary['minimum_disk_available_gib'], 130)
        self.assertEqual(summary['network_rx_bytes'], 2000)
        self.assertEqual(summary['network_tx_bytes'], 3000)
        self.assertEqual(summary['duration_seconds'], 4)


class ObservabilityContract(unittest.TestCase):
    def test_main_and_local_model_cover_exact_files_cells_and_browser_checkpoints(self):
        for profiles, count in [(['obs-main'], 16), (['obs-local-model', 'obs-main'], 17)]:
            config = wc.load_document(wc.SPEC / 'examples/event.example.yaml')
            config.update(course_ref=wc.git(wc.REPO, 'rev-parse', 'HEAD').strip(), course_profiles=profiles)
            selected, steps = course.selection(config)
            self.assertEqual(len(steps), count)
            if count == 17:
                optional = next(i for i, step in enumerate(steps) if step['kind'] == 'optional')
                self.assertEqual(steps[optional]['module'], 'module-7')
                self.assertEqual(steps[optional + 1]['module'], 'module-8')
            sources = {s['notebook']: wc.git(wc.REPO, 'show',
                       f"{config['course_ref']}:{selected['course_dir']}/{s['notebook']}").encode() for s in steps}
            rows = [{**s, 'enabled_opt_ins': [], 'passed': True, 'errors': 0,
                     'source_sha256': hashlib.sha256(sources[s['notebook']]).hexdigest(),
                     'code_cells': sum(c['cell_type'] == 'code' for c in json.loads(sources[s['notebook']])['cells'])}
                    for s in steps]
            gates = [{'gate': course._contract['OBS_BROWSER_GATES'][c['id']], 'notebook': s['notebook'], 'cell': c['id']}
                     for s in steps for c in json.loads(sources[s['notebook']])['cells']
                     if c.get('id') in course._contract['OBS_BROWSER_GATES']]
            self.assertEqual(len(gates), 5)
            report = dict(course_profiles=profiles, notebooks=rows, notebook_count=count,
                          code_cells=sum(r['code_cells'] for r in rows), browser_checkpoints=gates,
                          modules=[{'module': s['module'], 'passed': True} for s in steps if s['kind'] == 'lab'])
            verify = lambda value: course.valid_selection_report(value, steps, profiles, sources)
            self.assertTrue(verify(report))
            for change in [dict(browser_checkpoints=[]), dict(browser_checkpoints=gates[::-1]),
                           dict(waiting_browser=gates[0]), dict(notebook_count=count-1), dict(course_profiles=['rta-main'])]:
                self.assertFalse(verify({**report, **change}))
            bad = copy.deepcopy(report); bad['notebooks'][0]['code_cells'] -= 1
            self.assertFalse(verify(bad))
            bad = copy.deepcopy(selected); bad['modules'].reverse()
            with self.assertRaises(ValueError): course.selection_plan(bad)
            bad = copy.deepcopy(selected); bad['notebooks'].pop()
            with self.assertRaises(ValueError): course.selection_plan(bad)

    def test_browser_acknowledgement_is_bound_to_the_exact_environment_and_step(self):
        expected = dict(ami_id='ami-a', instance_id='i-a', course_ref='a'*40, event_id='event-a', learner_id='s001')
        value = {**expected, 'kind': 'doris-workshop-browser-checkpoint', 'passed': True,
                 'gate': 'obs-payment-failure-on', 'notebook': 'lab.ipynb'}
        verify = lambda v: course._contract['valid_browser_checkpoint'](v, expected, value['gate'], value['notebook'])
        self.assertTrue(verify(value))
        for key in list(expected) + ['kind', 'passed', 'gate', 'notebook']:
            self.assertFalse(verify({**value, key: 'wrong'}))
        self.assertFalse(verify([]))


if __name__ == '__main__':
    unittest.main()
