"""Batch admission preserves provenance and cannot repair unsafe translations."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from merge_locale_batches import merge


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def entry(identity='u.'+'1'*32, target='Оқыңыз `table` [нұсқаулық](guide.md).', checked=True):
    source = 'Read `table` [guide](guide.md).'
    value = {'unit_id': identity, 'source_hash': digest(source), 'target_text': target, 'target_hash': digest(target),
             'state': 'translated', 'provenance': {'translator': 'current-session-llm', 'session_id': 'synthetic-test', 'batch_id': 'synthetic-test', 'glossary_version': 'test-1'},
             'checks': {'llm_self_check': None, 'technical_admission': None, 'native_review': None}}
    if checked:
        value['checks']['llm_self_check'] = {'method': 'current-session-llm', 'actor': 'synthetic unit-test fixture', 'record': 'Synthetic test data, not a teaching translation.', 'source_hash': value['source_hash'], 'target_hash': value['target_hash']}
    unit = {'id': identity, 'source_hash': digest(source), 'source_text': source, 'source_path': 'lesson.md'}
    return unit, value


def overlay(entries):
    return {'schema_version': 2, 'contract_version': '2.0.0', 'kind': 'doris-locale-overlay', 'locale': 'kk-Cyrl', 'units': entries}


class BatchTests(unittest.TestCase):
    def run_merge(self, rows, existing=()):
        source = {'units': [u for u, _ in rows]}
        return merge(source, overlay(list(existing)), [('test.json', overlay([v for _, v in rows]))], {'lesson.md': None, 'guide.md': None}, source_ref='a'*40)

    def test_checked_translation_gets_real_technical_binding_without_rewriting_input(self):
        row = entry(); before = copy.deepcopy(row[1])
        result, accepted, errors, pending = self.run_merge([row])
        self.assertEqual(errors, []); self.assertEqual(pending, []); self.assertEqual(accepted, [row[0]['id']])
        translated = result['units'][0]
        self.assertEqual(translated['state'], 'llm_checked')
        self.assertEqual(translated['checks']['technical_admission']['target_hash'], before['target_hash'])
        self.assertEqual(row[1], before)

    def test_code_or_link_change_is_rejected_instead_of_silently_repaired(self):
        for text in ['Оқыңыз `different` [нұсқаулық](guide.md).', 'Оқыңыз `table` [нұсқаулық](other.md).']:
            with self.subTest(target=text):
                result, accepted, errors, pending = self.run_merge([entry(target=text)])
                self.assertFalse(accepted); self.assertEqual(result['units'], []); self.assertEqual(len(errors), 1)

    def test_draft_does_not_gain_checked_state_and_duplicates_cannot_replace_history(self):
        row = entry(checked=False)
        result, accepted, errors, pending = self.run_merge([row])
        self.assertFalse(accepted); self.assertFalse(errors); self.assertEqual(pending, [row[0]['id']]); self.assertFalse(result['units'])
        with self.assertRaisesRegex(ValueError, 'replace or duplicate'):
            self.run_merge([row], existing=[row[1]])
        with self.assertRaisesRegex(ValueError, 'replace or duplicate'):
            merge({'units': [row[0]]}, overlay([]), [('one.json', overlay([row[1]])), ('two.json', overlay([row[1]]))],
                  {'lesson.md': None, 'guide.md': None}, source_ref='a'*40)

    def test_existing_records_remain_exactly_unchanged(self):
        old = entry(identity='u.'+'2'*32)[1]
        result, _, errors, _ = self.run_merge([entry()], existing=[old])
        self.assertEqual(errors, [])
        self.assertEqual(next(v for v in result['units'] if v['unit_id'] == old['unit_id']), old)


if __name__ == '__main__':
    unittest.main()
