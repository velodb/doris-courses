"""Bound the complete course driver before making any SSM request."""
import ast
import base64
import gzip
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_config as wc
import workshop_validation as validation


class ValidationTransport(unittest.TestCase):
    def test_actual_nested_course_driver_fits_ssm_and_round_trips_exactly(self):
        fn = next(n for n in ast.parse(Path(validation.__file__).read_text()).body
                  if isinstance(n, ast.FunctionDef) and n.name == 'course')
        template = next(n.value.left.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'driver' for t in n.targets))
        source = (wc.SPEC / 'runtime/validate_course.py').read_bytes()
        driver = template % base64.b64encode(source).decode()
        aws = MagicMock()
        validation.send_file_command(aws, 'i-' + '0' * 17, driver, {'course_ref': 'a' * 40})
        commands = aws.client.return_value.send_command.call_args.kwargs['Parameters']['commands']
        self.assertLess(len(json.dumps(commands).encode()), 24000)
        packed = ''.join(line.split("'")[3] for line in commands if line.startswith("printf '%s' ")
                         and ' >> /run/workshop-audit.b64' in line)
        self.assertEqual(gzip.decompress(base64.b64decode(packed)).decode(), driver)

    def test_oversized_metadata_is_rejected_before_dispatch(self):
        aws = MagicMock()
        with self.assertRaises(wc.ConfigError):
            validation.send_file_command(aws, 'i-' + '0' * 17, 'pass', {'unbounded': 'x' * 24000})
        aws.client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
