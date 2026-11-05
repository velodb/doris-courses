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
    def test_all_selected_dw_services_need_their_own_browser_origin(self):
        config = {'course_profiles': ['dw-main', 'dw-bi', 'dw-cdc']}
        ports = {'51030': 'http://127.0.0.1:49030', '51888': 'http://127.0.0.1:49888',
                 '51881': 'http://127.0.0.1:49381'}
        named = validation.named_browser_routes(config, {'s001': ports})
        self.assertEqual(named['s001']['dw-flink'], ports['51881'])
        self.assertEqual(set(named['s001']), {'dw-fe', 'dw-superset', 'dw-flink'})
        for missing in ports:
            with self.subTest(missing=missing), self.assertRaises(wc.ConfigError):
                validation.named_browser_routes(config, {'s001': {p: u for p, u in ports.items() if p != missing}})

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
