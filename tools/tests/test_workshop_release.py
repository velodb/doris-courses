"""Evidence gates and immutable, private release selection; no AWS access."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workshop_release as release
import workshop_config as wc


def fixture():
    return dict(schema_version=1,kind='doris-workshop-image-release',status='validated',release_ref='rta-v1',
        aws_account_id='000000000000',region='us-east-1',ami_id='ami-'+'0'*17,architecture='x86_64',
        course_ref='a'*40,deployment_ref='a'*40,course_profiles=['rta-main'],runtime_sha256='b'*64,
        image_manifest_sha256='c'*64,root_gib=200,snapshot_ids=['snap-'+'0'*17],validation=dict(
            instance_id='i-'+'0'*17,checked_at='2026-01-01T00:00:00Z',**{k:'d'*64 for k in
                ('initial_audit_sha256','course_report_sha256','browser_report_sha256','cleanup_sha256')}))


class ReleaseTests(unittest.TestCase):
    def test_dw_browser_checks_include_selected_actual_services(self):
        self.assertEqual(release.browser_checks({'course_profiles':['dw-main']}), release.BROWSER_CHECKS)
        checks = release.browser_checks({'course_profiles':['dw-main','dw-bi','dw-cdc']})
        self.assertEqual(checks - release.BROWSER_CHECKS, {'dw-superset-dashboard','dw-flink-rest'})

    def test_obs_requires_browser_telemetry_live_alerts_load_and_selected_model(self):
        checks = release.browser_checks({'course_profiles':['obs-main']})
        self.assertEqual(len(checks - release.BROWSER_CHECKS), 7)
        self.assertTrue({'obs-demo-browser-telemetry', 'obs-mailpit-alert-cycle', 'obs-cpu-load-entry',
                         'obs-grafana-live', 'obs-service-restart'} <= checks)
        self.assertEqual(release.browser_checks({'course_profiles':['obs-main','obs-local-model']}) - checks,
                         {'obs-local-model-trace'})

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'release.json';self.value=fixture()
        self.config=dict(aws_account_id=self.value['aws_account_id'],region='us-east-1',course_ref='a'*40,
            course_profiles=['rta-main'],disk_gib=200,image=dict(mode='prebuilt',owner_account_id='000000000000',
                region='us-east-1',ami_id=self.value['ami_id'],architecture='x86_64',course_ref='a'*40,
                course_profiles=['rta-main'],release_ref='rta-v1'))
        self.write(self.path,self.value)

    def write(self,path,value):path.write_text(json.dumps(value))

    def test_exact_selection_and_bad_account_region_architecture_status(self):
        self.assertEqual(release.load_release(self.path,self.config),self.value)
        for field,bad in [('aws_account_id','111111111111'),('region','us-west-2'),('architecture','arm64'),
                          ('status','candidate'),('course_ref','e'*40),('release_ref','new-release')]:
            value=copy.deepcopy(self.value);value[field]=bad;self.write(self.path,value)
            with self.subTest(field=field),self.assertRaises(wc.ConfigError):release.load_release(self.path,self.config)
        self.write(self.path,self.value);self.config['disk_gib']=100
        with self.assertRaisesRegex(wc.ConfigError,'smaller'):release.load_release(self.path,self.config)

    def cloud(self):
        image=dict(ImageId=self.value['ami_id'],OwnerId='000000000000',State='available',Public=False,
            Architecture='x86_64',RootDeviceType='ebs',VirtualizationType='hvm',BlockDeviceMappings=[dict(Ebs=dict(
                SnapshotId=self.value['snapshot_ids'][0],VolumeSize=200,Encrypted=True,DeleteOnTermination=True))],
            Tags=[dict(Key=k,Value=v) for k,v in dict(ImageStatus='validated',ReleaseRef='rta-v1',
                ReleaseSHA256=wc.canonical_hash(self.value),ManifestSHA256='c'*64,CourseRef='a'*40).items()])
        cloud=mock.Mock();cloud.call.side_effect=lambda s,o,**kw:dict(Images=[image]) if o=='describe_images' else dict(LaunchPermissions=[])
        return cloud,image

    def test_live_rejects_unpublished_tampered_public_shared_and_extra_ebs(self):
        cloud,image=self.cloud();release.verify_live(cloud,self.config,self.value)
        for change in [dict(State='deregistered'),dict(Public=True),dict(Architecture='arm64'),dict(OwnerId='111111111111'),
                       dict(Tags=[]),dict(BlockDeviceMappings=image['BlockDeviceMappings']*2)]:
            cloud,image=self.cloud();image.update(change)
            with self.subTest(change=change),self.assertRaises(wc.ConfigError):release.verify_live(cloud,self.config,self.value)
        cloud,image=self.cloud();cloud.call.side_effect=lambda s,o,**kw:dict(Images=[image]) if o=='describe_images' else dict(LaunchPermissions=[dict(Group='all')])
        with self.assertRaises(wc.ConfigError):release.verify_live(cloud,self.config,self.value)

    def publish_inputs(self):
        v=self.value;instance=v['validation']['instance_id']
        binding=dict(config=self.config,candidate=dict(ami_id=v['ami_id'],manifest_sha256='c'*64,snapshots=v['snapshot_ids']),
            manifest=dict(deployment_ref='a'*40,profiles=['rta-main'],runtime_sha256='b'*64,root_volume=dict(size_gib=200)),candidate_run=str(self.root))
        audit=dict(passed=True,checks=dict(clean=True),instance_id=instance,image_id=v['ami_id'],course_ref='a'*40,image_manifest_sha256='c'*64)
        course=dict(kind='doris-workshop-course-validation',done=True,passed=True,image_id=v['ami_id'],instance_id=instance,course_ref='a'*40,
            notebook_count=20,code_cells=189,modules=[dict(passed=True) for _ in range(10)])
        browser=dict(kind='doris-workshop-browser-validation',passed=True,image_id=v['ami_id'],instance_id=instance,course_ref='a'*40,
            checks={k:True for k in release.BROWSER_CHECKS})
        cleanup=dict(known_instances=[instance],checked_at='2026-01-01T00:00:00Z',**{k:0 for k in ('managed_resources','active_instances',
            'remaining_volumes','remaining_network_resources','role_exists','profile_exists','remaining_enis')})
        for name,data in [('binding',binding),('initial-audit',audit),('course',course),('browser',browser),('reconciliation',cleanup)]:self.write(self.root/(name+'.json'),data)
        cloud=mock.Mock();cloud.call.return_value=dict(Images=[dict(Tags=[dict(Key='ImageStatus',Value='candidate')])])
        return cloud,course,browser,cleanup

    def test_publication_needs_full_course_ui_and_complete_cleanup(self):
        for name,key,bad in [('course','passed',False),('course','notebook_count',19),('browser','passed',False),
                             ('browser','instance_id','another'),('browser','checks',{'ui':True}),
                             ('browser','passed','true'),('reconciliation','remaining_enis',1)]:
            cloud,course,browser,cleanup=self.publish_inputs();data={'course':course,'browser':browser,'reconciliation':cleanup}[name]
            data[key]=bad;self.write(self.root/(name+'.json'),data)
            with self.subTest(key=key),mock.patch('workshop_image.record_candidate'),self.assertRaises(wc.ConfigError):
                release.publish(self.root,self.root/'course.json',self.root/'browser.json','rta-v1',self.root/'out.json',cloud)
            cloud.client.assert_not_called();self.assertFalse((self.root/'out.json').exists())

    def test_obs_publication_refuses_missing_browser_checks_and_wrong_selection(self):
        for bad in ('missing-check', 'wrong-profiles'):
            cloud, course, browser, _ = self.publish_inputs()
            binding = wc.load_document(self.root/'binding.json')
            binding['config']['course_profiles'] = ['obs-local-model', 'obs-main']
            binding['manifest']['profiles'] = ['obs-local-model', 'obs-main']
            self.write(self.root/'binding.json', binding)
            browser.update(course_profiles=['obs-local-model','obs-main'],
                           checks={k:True for k in release.browser_checks(binding['config'])})
            if bad == 'missing-check': del browser['checks']['obs-local-model-trace']
            else: browser['course_profiles'] = ['obs-main']
            self.write(self.root/'browser.json', browser)
            with self.subTest(bad=bad), mock.patch('workshop_image.record_candidate'), \
                 mock.patch.object(release.workshop_course, 'verify_report', return_value=True), \
                 self.assertRaisesRegex(wc.ConfigError, 'real browser validation'):
                release.publish(self.root,self.root/'course.json',self.root/'browser.json','obs-v1',self.root/'out.json',cloud)
            cloud.client.assert_not_called()

    def test_release_is_written_after_evidence_and_cannot_overwrite_another_version(self):
        cloud,*_=self.publish_inputs()
        with mock.patch('workshop_image.record_candidate'):
            value=release.publish(self.root,self.root/'course.json',self.root/'browser.json','rta-v1',self.root/'out.json',cloud)
        self.assertEqual(value['validation']['course_report_sha256'],__import__('workshop_image').checksum(self.root/'course.json'))
        cloud.client.return_value.create_tags.assert_called_once()
        cloud.call.return_value=dict(Images=[dict(Tags=[dict(Key='ImageStatus',Value='validated'),dict(Key='ReleaseRef',Value='rta-v2')])])
        with mock.patch('workshop_image.record_candidate'),self.assertRaisesRegex(wc.ConfigError,'overwrite'):
            release.publish(self.root,self.root/'course.json',self.root/'browser.json','rta-v2',self.root/'out2.json',cloud)
