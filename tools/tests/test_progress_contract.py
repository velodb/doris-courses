"""Portable schemas are independent of course-content/runtime versions."""
import copy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'doris-course/common'))
from doris_labkit import progress


class PortableContractTests(unittest.TestCase):
    def test_self_contained_schemas_and_fixtures_dispatch(self):
        for version in (1, 2):
            schema = json.loads((ROOT / f'contracts/progress/v{version}.schema.json').read_text())
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            document = json.loads((ROOT / f'doris-course/common/tests/fixtures/progress-v{version}.json').read_text())
            validator.validate(document)
            progress.validate_document(json.dumps(document).encode())
            bad = {**document, 'schema_version': 3 - version}
            self.assertTrue(list(validator.iter_errors(bad)))
            for field, value in [('best_correct', True), ('source_ref', 'b' * 40 + '\n'), ('account', 'other')]:
                invalid = copy.deepcopy(document)
                invalid['records'][0][field] = value
                self.assertTrue(list(validator.iter_errors(invalid)))
                with self.assertRaises(progress.ProgressError):
                    progress.validate_document(json.dumps(invalid).encode())


if __name__ == '__main__':
    unittest.main()
