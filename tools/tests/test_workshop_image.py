"""Image input tampering, fresh-host sealing order and candidate release boundaries."""
import importlib.util
import itertools
import json
import subprocess
from datetime import datetime, timedelta, timezone
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
        self.config['course_profiles'] = ['obs-main', 'obs-openai']
        self.path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(wc.ConfigError, 'rta-main'):
            self.prepare()
        self.assertFalse((self.root / 'bundle').exists())

    def test_dw_image_contains_the_selected_locks_and_custom_build_contexts(self):
        self.config['course_profiles'] = sorted(image.DW_PROFILES)
        self.path.write_text(json.dumps(self.config))
        bundle = self.prepare()
        plan, _ = host.verify_bundle(bundle)
        self.assertEqual(set(plan['python_locks']), {'server', 'doris-dw'})
        self.assertEqual(len(plan['courses'][0]['notebooks']), 34)
        inputs = json.loads((bundle / 'image-input.json').read_text())
        self.assertEqual(inputs['images']['profiles'], sorted(image.DW_PROFILES))
        self.assertEqual(len(inputs['images']['images']), 7)
        self.assertEqual({b['base'] for b in inputs['images']['builds']},
                         {'flink:1.20.1-scala_2.12-java11', 'apache/superset:4.1.2'})

    def test_dw_main_excludes_unselected_streaming_and_bi_builds(self):
        self.config['course_profiles'] = ['dw-main']
        self.path.write_text(json.dumps(self.config))
        inputs = json.loads((self.prepare() / 'image-input.json').read_text())
        self.assertEqual(len(inputs['images']['images']), 3)
        self.assertEqual(inputs['images']['builds'], [])

    def test_obs_bundle_pins_every_selected_container_and_course_dependency(self):
        for profiles, count, notebooks in [(['obs-main'], 33, 16),
                                           (['obs-local-model', 'obs-main'], 34, 17)]:
            with self.subTest(profiles=profiles):
                self.config['course_profiles'] = profiles
                self.path.write_text(json.dumps(self.config))
                bundle = self.root / ('bundle-' + str(count))
                inputs = image.prepare(self.path, bundle)
                plan, _ = host.verify_bundle(bundle)
                self.assertEqual(set(plan['python_locks']), {'server', 'doris-obs'})
                self.assertEqual(len(plan['courses'][0]['notebooks']), notebooks)
                self.assertEqual(len(inputs['images']['images']), count)
                self.assertEqual(inputs['images']['builds'], [])
                self.assertEqual(any('llama.cpp' in row['tag'] for row in inputs['images']['images']), count == 34)
                self.assertEqual(inputs['images']['profiles'], profiles)

    def test_obs_external_model_or_mixed_course_selection_is_refused(self):
        for profiles in [['obs-main', 'obs-openai'], ['obs-main', 'dw-main'], ['obs-local-model']]:
            with self.subTest(profiles=profiles), self.assertRaises(wc.ConfigError):
                image.image_selection({'course_profiles': profiles})

    def test_output_cannot_overwrite_an_existing_bundle(self):
        self.prepare()
        with self.assertRaises(wc.ConfigError):
            self.prepare()


class ImageProvenance(unittest.TestCase):
    def test_ui_and_packaging_changes_cannot_claim_a_clean_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            paths = ['ui/workshop/src/templates/page.html', 'ui/workshop/assets/manrope.ttf',
                     'ui/workshop/package.py', 'tools/workshop_ui.py',
                     'workshop/aws/runtime/host.py']
            for name in paths:
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('committed input\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.com', '-c', 'commit.gpgsign=false',
                            'commit', '-qm', 'Image inputs'], check=True)
            self.assertFalse(image.deployment_dirty(repo=repo))
            for name in paths:
                with self.subTest(path=name):
                    path = repo / name
                    path.write_text('uncommitted input\n')
                    self.assertTrue(image.deployment_dirty(repo=repo))
                    path.unlink()
                    self.assertTrue(image.deployment_dirty(repo=repo))
                    path.write_text('committed input\n')
            extra = repo / 'ui/workshop/src/untracked.html'
            extra.write_text('untracked input\n')
            self.assertTrue(image.deployment_dirty(repo=repo))
            extra.unlink()
            (repo / 'plan-doc').mkdir()
            (repo / 'plan-doc/private.md').write_text('local planning\n')
            self.assertFalse(image.deployment_dirty(repo=repo))


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
        self.ssm.exceptions.InvocationDoesNotExist = type('InvocationDoesNotExist', (Exception,), {})
        self.ssm.exceptions.InvalidPluginName = type('InvalidPluginName', (Exception,), {})
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

    def test_preserves_the_builder_root_volume_before_snapshot_cleanup(self):
        self.ec2.describe_instances.return_value['Reservations'][0]['Instances'][0]['BlockDeviceMappings'] = [
            {'DeviceName': '/dev/sda1', 'Ebs': {'VolumeId': 'vol-00000000000000000'}}]
        image.provision(self.root)
        receipt = json.loads((self.root / 'build-instance.json').read_text())
        self.assertEqual(receipt['root_volume_id'], 'vol-00000000000000000')

    def test_failed_install_never_schedules_sealing(self):
        self.ssm.list_commands.return_value = {'Commands': [{'Status': 'Failed',
            'StandardErrorContent': 'fixture-password-do-not-persist',
            'Parameters': {'commands': ['fixture-password-do-not-persist']},
            'RequestedDateTime': datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)}]}
        with self.assertRaisesRegex(wc.ConfigError, 'earlier SSM'):
            image.provision(self.root)
        self.assertEqual(self.ssm.send_command.call_count, 1)
        self.assertFalse((self.root / 'seal-command.json').exists())
        self.assertEqual(json.loads((self.root / 'failed-command.json').read_text())['RequestedDateTime'],
                         '2026-10-30T12:00:00Z')
        self.assertNotIn('fixture-password-do-not-persist', (self.root / 'failed-command.json').read_text())

    def test_later_step_not_registered_while_installing_is_retried(self):
        result = self.ssm.get_command_invocation.return_value
        self.ssm.get_command_invocation.side_effect = [self.ssm.exceptions.InvalidPluginName(), result]
        self.ssm.list_commands.side_effect = [{'Commands': [{'Status': 'InProgress'}]},
                                              {'Commands': [{'Status': 'Success'}]}]
        image.provision(self.root)
        self.assertEqual(self.ssm.get_command_invocation.call_count, 2)
        self.assertTrue((self.root / 'seal-command.json').exists())

    def test_completed_document_with_missing_step_fails_after_grace(self):
        self.ssm.get_command_invocation.side_effect = self.ssm.exceptions.InvalidPluginName()
        with self.assertRaisesRegex(wc.ConfigError, 'no preparation step'):
            image.provision(self.root)
        self.assertEqual(self.ssm.get_command_invocation.call_count, 7)
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
        (self.root / 'binding.json').write_text(json.dumps({'build_id': 'test-image', 'config': {
            'region': 'us-east-1', 'aws_account_id': '000000000000', 'course_ref': 'a' * 40, 'course_profiles': ['rta-main']}}))
        (self.root / 'packer-manifest.json').write_text(json.dumps({'builds': [{'artifact_id': 'us-east-1:ami-00000000000000000'}]}))
        (self.root / 'image-manifest.json').write_text(json.dumps({'course_ref': 'a' * 40, 'profiles': ['rta-main']}))
        self.image = {'State': 'available', 'Public': False, 'Architecture': 'x86_64', 'BlockDeviceMappings': [
            {'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': 200, 'DeleteOnTermination': True, 'SnapshotId': 'snap-00000000000000000'}}],
            'Tags': [{'Key': 'ImageBuild', 'Value': 'test-image'}, {'Key': 'CourseRef', 'Value': 'a' * 40},
                     {'Key': 'Scope', 'Value': 'image'}]}
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

    def test_inherited_virtual_devices_do_not_add_ebs_snapshots(self):
        self.image['BlockDeviceMappings'].extend([
            {'DeviceName': '/dev/sdb', 'VirtualName': 'ephemeral0'},
            {'DeviceName': '/dev/sdc', 'VirtualName': 'ephemeral1'}])
        image.record_candidate(self.root, self.aws)
        self.assertEqual(len(json.loads((self.root / 'candidate.json').read_text())['snapshots']), 1)

    def test_wrong_build_tags_are_rejected(self):
        self.image['Tags'][0]['Value'] = 'another-image'
        with self.assertRaisesRegex(wc.ConfigError, 'provenance'):
            image.record_candidate(self.root, self.aws)

    def test_candidate_cannot_relabel_rta_as_dw(self):
        (self.root / 'image-manifest.json').write_text(json.dumps({'course_ref': 'a' * 40, 'profiles': ['dw-main']}))
        with self.assertRaisesRegex(wc.ConfigError, 'provenance'):
            image.record_candidate(self.root, self.aws)

    def test_verify_can_repeat_but_rechecks_encryption(self):
        image.record_candidate(self.root, self.aws)
        before = (self.root / 'candidate.json').stat().st_mtime_ns
        image.record_candidate(self.root, self.aws)
        self.assertEqual((self.root / 'candidate.json').stat().st_mtime_ns, before)
        self.snapshot['Encrypted'] = False
        with self.assertRaises(wc.ConfigError):
            image.record_candidate(self.root, self.aws)

    def test_unknown_extra_device_is_rejected(self):
        self.image['BlockDeviceMappings'].append({'DeviceName': '/dev/sdf'})
        with self.assertRaises(wc.ConfigError):
            image.record_candidate(self.root, self.aws)

    def test_missing_cache_volume_mapping_is_rejected(self):
        self.image['BlockDeviceMappings'].append({'DeviceName': '/dev/sdf', 'Ebs': {'VolumeSize': 100}})
        with self.assertRaises(wc.ConfigError):
            image.record_candidate(self.root, self.aws)


class ArtifactRecovery(unittest.TestCase):
    def setUp(self):
        CandidateChecks.setUp(self)
        (self.root / 'packer-manifest.json').unlink()
        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'runtime.json').write_text('{"courses": []}\n')
        (bundle / 'image-input.json').write_text(json.dumps({
            'deployment_ref': 'a' * 40, 'deployment_dirty': False}))
        manifest = {'course_ref': 'a' * 40, 'profiles': ['rta-main'], 'deployment_ref': 'a' * 40,
                    'image_input_sha256': image.checksum(bundle / 'image-input.json'),
                    'runtime_sha256': image.checksum(bundle / 'runtime.json')}
        (self.root / 'image-manifest.json').write_text(json.dumps(manifest))
        (self.root / 'inputs.tfvars.json').write_text('{"build_id":"test-image"}\n')
        binding = wc.load_document(self.root / 'binding.json')
        binding['config']['owner_id'] = 'owner'
        binding.update(settings={'runtime_bundle_dir': str(bundle)}, payload_sha256=image.payload_hash(bundle),
                       vars_sha256=image.checksum(self.root / 'inputs.tfvars.json'))
        (self.root / 'binding.json').write_text(json.dumps(binding))
        (self.root / 'terraform.tfstate').write_text('{"resources": []}\n')
        self.instance = 'i-00000000000000000'
        self.volume = 'vol-00000000000000000'
        self.ami = 'ami-00000000000000000'
        for name, command in [('build-instance', None), ('build-command', 'prepare-command'),
                              ('seal-command', 'seal-command')]:
            value = {'instance_id': self.instance}
            if command:
                value['command_id'] = command
            (self.root / (name + '.json')).write_text(json.dumps(value))
        (self.root / 'build.pkrvars.json').write_text(json.dumps({
            'retain_until': wc.utc_text(datetime.now(timezone.utc) + timedelta(days=7))}))
        self.image.update(Name='doris-workshop-test-image', OwnerId='000000000000')
        self.image['Tags'] = [{'Key': k, 'Value': v} for k, v in {
            'ImageBuild': 'test-image', 'CourseRef': 'a' * 40, 'Scope': 'image-build',
            'Project': 'doris-workshop', 'Owner': 'owner'}.items()]
        self.snapshot.update(VolumeId=self.volume, VolumeSize=200, Tags=[],
                             Description=f'Created by CreateImage({self.instance}) for {self.ami}')
        self.preparation = {'Status': 'Success', 'CommandPlugins': [
            {'Name': name, 'Status': 'Success', 'ResponseCode': 0}
            for name in ['downloadRuntime', 'installRuntime', 'prepareImage']]}
        self.hosts = []
        self.aws.call.side_effect = lambda service, operation, **kwargs: {
            'get_caller_identity': {'Account': '000000000000'},
            'describe_instances': {'Reservations': self.hosts},
            'list_command_invocations': {'CommandInvocations': [self.preparation]},
            'describe_images': {'Images': [self.image]}, 'describe_snapshots': {'Snapshots': [self.snapshot]},
            'describe_image_attribute': self.attrs}[operation]
        self.ec2 = MagicMock()
        self.aws.client.return_value = self.ec2
        def tag(**kwargs):
            self.assertEqual(kwargs['Resources'], [self.ami, self.snapshot['SnapshotId']])
            for item in [self.image, self.snapshot]:
                current = {t['Key']: t['Value'] for t in item['Tags']}
                current.update({t['Key']: t['Value'] for t in kwargs['Tags']})
                item['Tags'] = [{'Key': k, 'Value': v} for k, v in current.items()]
        self.ec2.create_tags.side_effect = tag
        clean = patch.object(image, 'deployment_dirty', return_value=False)
        clean.start()
        self.addCleanup(clean.stop)

    def recover(self):
        image.recover_artifact(self.root, self.ami, self.volume, self.aws)

    def assert_no_recovery(self):
        self.ec2.create_tags.assert_not_called()
        self.assertFalse((self.root / 'candidate.json').exists())
        self.assertFalse((self.root / 'recovered-artifact.json').exists())

    def test_recovers_partial_tags_without_fabricating_packer_success(self):
        self.recover()
        self.assertFalse((self.root / 'packer-manifest.json').exists())
        proof = wc.load_document(self.root / 'recovered-artifact.json')
        self.assertEqual(proof['kind'], 'doris-workshop-image-artifact-recovery')
        self.assertEqual(proof['source_instance_id'], self.instance)
        self.assertEqual(proof['source_volume_id'], self.volume)
        self.assertEqual(wc.load_document(self.root / 'candidate.json')['status'], 'candidate')
        self.ec2.create_tags.assert_called_once()
        self.ec2.create_image.assert_not_called()
        self.ec2.run_instances.assert_not_called()
        self.ec2.send_command.assert_not_called()

    def test_repeat_and_normal_verify_preserve_the_recovery(self):
        self.recover()
        paths = [self.root / 'candidate.json', self.root / 'recovered-artifact.json']
        before = [p.stat().st_mtime_ns for p in paths]
        self.recover()
        image.record_candidate(self.root, self.aws)
        self.assertEqual(before, [p.stat().st_mtime_ns for p in paths])
        self.ec2.create_tags.assert_called_once()

    def test_rejects_wrong_source_volume_before_tagging(self):
        self.snapshot['VolumeId'] = 'vol-11111111111111111'
        with self.assertRaisesRegex(wc.ConfigError, 'captured builder root'):
            self.recover()
        self.assert_no_recovery()

    def test_rejects_another_builder_description_before_tagging(self):
        self.snapshot['Description'] = 'Created by CreateImage(i-11111111111111111) for ' + self.ami
        with self.assertRaisesRegex(wc.ConfigError, 'captured builder root'):
            self.recover()
        self.assert_no_recovery()

    def test_requires_finished_cleanup_and_original_preparation(self):
        self.hosts = [{'Instances': [{'State': {'Name': 'running'}}]}]
        with self.assertRaisesRegex(wc.ConfigError, 'still active'):
            self.recover()
        self.hosts = []
        self.preparation['CommandPlugins'][-1]['Status'] = 'Failed'
        with self.assertRaisesRegex(wc.ConfigError, 'preparation'):
            self.recover()
        self.assert_no_recovery()

    def test_never_retags_a_published_image(self):
        self.image['Tags'].append({'Key': 'ImageStatus', 'Value': 'validated'})
        with self.assertRaisesRegex(wc.ConfigError, 'published image'):
            self.recover()
        self.assert_no_recovery()

    def test_changed_payload_and_public_image_remain_rejected(self):
        runtime = self.root / 'bundle/runtime.json'
        original = runtime.read_bytes()
        runtime.write_text('changed')
        with self.assertRaisesRegex(wc.ConfigError, 'inputs changed'):
            self.recover()
        runtime.write_bytes(original)
        self.image['Public'] = True
        with self.assertRaisesRegex(wc.ConfigError, 'privacy'):
            self.recover()
        self.assert_no_recovery()

    def test_verify_rechecks_recovered_volume_and_proof_hashes(self):
        self.recover()
        self.snapshot['VolumeId'] = 'vol-11111111111111111'
        with self.assertRaisesRegex(wc.ConfigError, 'source volume changed'):
            image.record_candidate(self.root, self.aws)
        self.snapshot['VolumeId'] = self.volume
        (self.root / 'image-manifest.json').write_text('{}')
        with self.assertRaisesRegex(wc.ConfigError, 'provenance changed'):
            image.record_candidate(self.root, self.aws)


if __name__ == '__main__':
    unittest.main()
