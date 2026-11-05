"""Bound the complete course driver before making any SSM request."""
import ast
import base64
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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

    def test_obs_all_five_browser_services_require_routes(self):
        config = {'course_profiles': ['obs-main']}
        ports = {p: 'http://127.0.0.1:' + p for p in ('28030','23000','28082','28025','23100')}
        named = validation.named_browser_routes(config, {'s001': ports})
        self.assertEqual(set(named['s001']), {'obs-fe','obs-grafana','obs-demo','obs-mailpit','obs-litefuse'})
        for missing in ports:
            with self.subTest(missing=missing), self.assertRaises(wc.ConfigError):
                validation.named_browser_routes(config, {'s001': {p:u for p,u in ports.items() if p!=missing}})

    def test_oversized_metadata_is_rejected_before_dispatch(self):
        aws = MagicMock()
        with self.assertRaises(wc.ConfigError):
            validation.send_file_command(aws, 'i-' + '0' * 17, 'pass', {'unbounded': 'x' * 24000})
        aws.client.assert_not_called()


class BrowserCheckpointDispatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.attempt = self.directory / 'courses' / 'attempt'
        self.attempt.mkdir(parents=True)
        self.gate = 'obs-payment-failure-on'
        expected = dict(ami_id='ami-a', instance_id='i-a', course_ref='a'*40, event_id='obs-event',
                        learner_id='s001', course_output_dir='/var/lib/doris-workshop/home/attempt')
        binding = dict(config=dict(aws_account_id='000000000000', course_profiles=['obs-main'],
                                   course_ref='a'*40, event_id='obs-event', learner_ids=['s001']),
                       candidate=dict(ami_id='ami-a'))
        self.write(self.directory/'binding.json', binding)
        self.write(self.directory/'initial-audit.json', dict(instance_id='i-a'))
        self.write(self.attempt/'command.json', dict(command_id='original', expected=expected))
        self.cloud = MagicMock()
        self.cloud.call.return_value = {'Account':'000000000000'}
        self.cloud.client.return_value.get_command_invocation.side_effect = [
            {'Status':'InProgress'}, {'Status':'Success'}]

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def test_acknowledgement_preserves_the_original_course_and_its_own_dispatch(self):
        original = (self.attempt/'command.json').read_bytes()
        with patch.object(validation.pf, 'Aws', return_value=self.cloud), \
             patch.object(validation, 'send_file_command', return_value='acknowledgement') as send:
            validation.checkpoint(self.directory, self.attempt, self.gate)
        self.assertEqual((self.attempt/'command.json').read_bytes(), original)
        self.assertEqual(send.call_args.args[3]['gate'], self.gate)
        receipt = json.loads((self.attempt/(self.gate+'-command.json')).read_text())
        self.assertEqual(receipt['command_id'], 'acknowledgement')
        self.assertEqual(receipt['phase'], 'dispatched')

    def test_uncertain_dispatch_cannot_be_replayed(self):
        self.cloud.client.return_value.get_command_invocation.side_effect = None
        self.cloud.client.return_value.get_command_invocation.return_value = {'Status':'InProgress'}
        with patch.object(validation.pf, 'Aws', return_value=self.cloud), \
             patch.object(validation, 'send_file_command', side_effect=OSError('connection lost')) as send:
            with self.assertRaises(OSError): validation.checkpoint(self.directory, self.attempt, self.gate)
            with self.assertRaises(wc.ConfigError): validation.checkpoint(self.directory, self.attempt, self.gate)
        self.assertEqual(send.call_count, 1)
        receipt = json.loads((self.attempt/(self.gate+'-command.json')).read_text())
        self.assertEqual(receipt['phase'], 'dispatching')

    def test_wrong_account_or_attempt_never_dispatches(self):
        self.cloud.call.return_value = {'Account':'111111111111'}
        with patch.object(validation.pf, 'Aws', return_value=self.cloud), \
             patch.object(validation, 'send_file_command') as send:
            with self.assertRaises(wc.ConfigError): validation.checkpoint(self.directory, self.attempt, self.gate)
            with self.assertRaises(wc.ConfigError): validation.checkpoint(self.directory, self.directory, self.gate)
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
