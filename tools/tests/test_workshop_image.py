"""Image input tampering, fresh-host sealing order and candidate release boundaries."""
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_config as wc
import workshop_image as image

spec = importlib.util.spec_from_file_location('image_test_host', wc.SPEC / 'runtime/host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class ImageInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = wc.load_document(wc.SPEC / 'examples/event.example.yaml')
        self.config['course_ref'] = wc.git(wc.REPO, 'rev-parse', 'HEAD').strip()
        self.config['image']['owner_account_id'] = '099720109477'
        self.path = self.root / 'event.yaml'
        self.path.write_text(json.dumps(self.config))

    def prepare(self):
        target = self.root / 'bundle'
        image.prepare(self.path, target)
        return target

    def test_fixed_bundle_contains_locks_but_no_activity_identity(self):
        bundle = self.prepare()
        plan, _ = host.verify_bundle(bundle)
        self.assertEqual(set(plan['python_locks']), {'server', 'doris-rta'})
        data = (bundle / 'image-input.json').read_text() + (bundle / 'runtime.json').read_text()
        for field in ('aws_profile', 'learner_ids', 'event_id', 'auth', 'domain'):
            self.assertNotIn('"' + field + '"', data)
        self.assertEqual(len(json.loads((bundle / 'image-input.json').read_text())['images']['images']), 3)
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)

    def test_changed_lock_is_rejected_before_installing(self):
        bundle = self.prepare()
        (bundle / 'server.requirements.lock').write_text('unreviewed-package==1\n')
        with self.assertRaisesRegex(host.RuntimeErrorDetail, 'checksum'):
            host.verify_bundle(bundle)

    def test_partial_or_traversing_lock_mapping_is_rejected(self):
        bundle = self.prepare()
        plan = json.loads((bundle / 'runtime.json').read_text())
        for locks in ({'server': 'server.requirements.lock'}, {'server': '../secret', 'doris-rta': 'doris-rta.requirements.lock'}, []):
            changed = {**plan, 'python_locks': locks}
            (bundle / 'runtime.json').write_text(json.dumps(changed))
            with self.subTest(locks=locks), self.assertRaisesRegex(host.RuntimeErrorDetail, 'lock'):
                host.verify_bundle(bundle)

    def test_unvalidated_profile_is_rejected(self):
        self.config['course_profiles'] = ['dw-main']
        self.path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(wc.ConfigError, 'rta-main'):
            self.prepare()
        self.assertFalse((self.root / 'bundle').exists())

    def test_output_cannot_overwrite_an_existing_bundle(self):
        self.prepare()
        with self.assertRaises(wc.ConfigError):
            self.prepare()


@unittest.skipUnless(importlib.util.find_spec('boto3'), 'Install preflight-requirements.txt for SSM contract tests')
class SsmSealing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bundle = self.root / 'bundle'
        bundle.mkdir()
        self.image_input = {'course_ref': 'a' * 40}
        (bundle / 'image-input.json').write_text(json.dumps(self.image_input))
        self.settings = {'aws_profile': 'offline', 'region': 'us-east-1', 'expected_account_id': '000000000000',
                         'build_id': 'test-image', 'install_document': 'test-install', 'runtime_bundle_dir': str(bundle)}
        (self.root / 'provision.json').write_text(json.dumps(self.settings))
        self.ec2, self.ssm, self.sts = MagicMock(), MagicMock(), MagicMock()
        self.sts.get_caller_identity.return_value = {'Account': '000000000000'}
        instance = {'InstanceId': 'i-00000000000000000', 'State': {'Name': 'stopped'}}
        self.ec2.describe_instances.return_value = {'Reservations': [{'Instances': [instance]}]}
        self.ssm.describe_instance_information.return_value = {'InstanceInformationList': [{'PingStatus': 'Online'}]}
        self.ssm.send_command.return_value = {'Command': {'CommandId': 'offline-command'}}
        self.ssm.list_commands.return_value = {'Commands': [{'Status': 'Success'}]}
        self.report = {'image_input_sha256': image.checksum(bundle / 'image-input.json'), 'course_ref': 'a' * 40}
        self.ssm.get_command_invocation.return_value = {'Status': 'Success', 'StandardOutputContent': json.dumps(self.report)}
        session = MagicMock()
        session.client.side_effect = lambda name, **kwargs: {'ec2': self.ec2, 'ssm': self.ssm, 'sts': self.sts}[name]
        self.session_patch = patch('boto3.Session', return_value=session)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        clock = patch.object(image.time, 'monotonic', side_effect=itertools.count())
        clock.start()
        self.addCleanup(clock.stop)
        sleep = patch.object(image.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_waits_for_stopped_host_after_scheduling_seal(self):
        image.provision(self.root)
        self.assertEqual(self.ssm.send_command.call_count, 2)
        self.assertIn('systemd-run', self.ssm.send_command.call_args.kwargs['Parameters']['commands'][0])
        self.assertEqual(json.loads((self.root / 'image-manifest.json').read_text()), self.report)
        self.ec2.create_image.assert_not_called()  # Snapshot is Packer's responsibility after this function returns.

    def test_failed_install_never_schedules_sealing(self):
        self.ssm.list_commands.return_value = {'Commands': [{'Status': 'Failed'}]}
        with self.assertRaisesRegex(wc.ConfigError, 'earlier SSM'):
            image.provision(self.root)
        self.assertEqual(self.ssm.send_command.call_count, 1)
        self.assertFalse((self.root / 'seal-command.json').exists())

    def test_ambiguous_build_tag_never_runs_a_command(self):
        self.ec2.describe_instances.return_value['Reservations'][0]['Instances'] *= 2
        with self.assertRaisesRegex(wc.ConfigError, 'ambiguous'):
            image.provision(self.root)
        self.ssm.send_command.assert_not_called()

    def test_input_mismatch_never_schedules_sealing(self):
        self.ssm.get_command_invocation.return_value['StandardOutputContent'] = json.dumps({**self.report, 'image_input_sha256': '0' * 64})
        with self.assertRaisesRegex(wc.ConfigError, 'mismatch'):
            image.provision(self.root)
        self.assertEqual(self.ssm.send_command.call_count, 1)

    def test_shutdown_timeout_cannot_pass_as_a_sealed_image(self):
        self.ec2.describe_instances.return_value['Reservations'][0]['Instances'][0]['State']['Name'] = 'running'
        with self.assertRaisesRegex(wc.ConfigError, 'did not stop'):
            image.provision(self.root)


class CandidateChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'binding.json').write_text(json.dumps({'config': {'region': 'us-east-1', 'aws_account_id': '000000000000'}}))
        (self.root / 'packer-manifest.json').write_text(json.dumps({'builds': [{'artifact_id': 'us-east-1:ami-00000000000000000'}]}))
        (self.root / 'image-manifest.json').write_text('{}')
        self.image = {'State': 'available', 'Public': False, 'Architecture': 'x86_64', 'BlockDeviceMappings': [
            {'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': 200, 'DeleteOnTermination': True, 'SnapshotId': 'snap-00000000000000000'}}]}
        self.snapshot = {'Encrypted': True, 'State': 'completed', 'OwnerId': '000000000000', 'SnapshotId': 'snap-00000000000000000'}
        self.attrs = {'LaunchPermissions': []}
        self.aws = MagicMock()
        self.aws.call.side_effect = lambda service, operation, **kwargs: {
            'describe_images': {'Images': [self.image]}, 'describe_snapshots': {'Snapshots': [self.snapshot]},
            'describe_image_attribute': self.attrs}[operation]

    def test_success_is_still_a_candidate(self):
        image.record_candidate(self.root, self.aws)
        self.assertEqual(json.loads((self.root / 'candidate.json').read_text())['status'], 'candidate')

    def test_public_or_shared_image_is_never_recorded_as_candidate(self):
        self.image['Public'] = True
        with self.assertRaises(wc.ConfigError):
            image.record_candidate(self.root, self.aws)
        self.image['Public'] = False
        self.attrs['LaunchPermissions'] = [{'Group': 'all'}]
        with self.assertRaises(wc.ConfigError):
            image.record_candidate(self.root, self.aws)
        self.assertFalse((self.root / 'candidate.json').exists())

    def test_unencrypted_snapshot_is_rejected(self):
        self.snapshot['Encrypted'] = False
        with self.assertRaises(wc.ConfigError):
            image.record_candidate(self.root, self.aws)
        self.assertFalse((self.root / 'candidate.json').exists())

    def test_missing_cache_volume_mapping_is_rejected(self):
        self.image['BlockDeviceMappings'].append({'DeviceName': '/dev/sdf', 'Ebs': {'VolumeSize': 100}})
        with self.assertRaises(wc.ConfigError):
            image.record_candidate(self.root, self.aws)


if __name__ == '__main__':
    unittest.main()
