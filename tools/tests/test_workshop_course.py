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
        config = {**self.config, 'course_profiles': ['obs-main']}
        with self.assertRaises(wc.ConfigError): course.selection(config)


if __name__ == '__main__':
    unittest.main()
