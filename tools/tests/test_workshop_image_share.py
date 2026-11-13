"""Transfer trust, account isolation, immutable plans and recipient publication gates."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_config as wc
import workshop_image as wi
import workshop_image_share as share


SOURCE = '000000000000'
TARGET = '111111111111'


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.runtime = self.root/'runtime'
        self.runtime.mkdir()
        (self.runtime/'source.tar').write_bytes(b'fixed public source fixture')
        wi.write(self.runtime/'runtime.json', dict(kind='doris-workshop-runtime', schema_version=1,
            course_ref='a'*40, courses=[dict(profiles=['rta-main'])],
            sha256={'source.tar':wi.checksum(self.runtime/'source.tar')}))
        runtime_sha = wi.checksum(self.runtime/'runtime.json')
        self.manifest = dict(schema_version=1, course_ref='a'*40, deployment_ref='a'*40,
                             runtime_sha256=runtime_sha, profiles=['rta-main'], root_volume=dict(size_gib=200))
        wi.write(self.root/'manifest.json', self.manifest)
        self.release = dict(schema_version=1, kind='doris-workshop-image-release', status='validated',
            release_ref='rta-v1', aws_account_id=SOURCE, region='us-east-1', ami_id='ami-'+'0'*17,
            architecture='x86_64', course_ref='a'*40, deployment_ref='a'*40, course_profiles=['rta-main'],
            runtime_sha256=runtime_sha, image_manifest_sha256=wi.checksum(self.root/'manifest.json'), root_gib=200,
            snapshot_ids=['snap-'+'0'*17], validation=dict(instance_id='i-'+'0'*17, checked_at='2026-01-01T00:00:00Z',
            **{k:'d'*64 for k in ('initial_audit_sha256','course_report_sha256','browser_report_sha256','cleanup_sha256')}))
        wi.write(self.root/'release.json', self.release)
        wi.write(self.root/'builder.json', dict(instance_id='i-'+'9'*17, ignored_secret='do not copy'))
        self.package = self.root/'package'
        self.principal = 'arn:aws:iam::'+TARGET+':role/WorkshopImporter'
        self.digest = share.prepare(self.root/'release.json', self.root/'manifest.json', self.root/'builder.json',
                                    'rta-partner-v1', TARGET, self.principal, self.package, self.runtime)
        self.artifact = dict(ami_id='ami-'+'1'*17, snapshot_ids=['snap-'+'1'*17],
            kms_key_arn='arn:aws:kms:us-east-1:'+SOURCE+':key/'+'1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12,
            account_id=SOURCE, region='us-east-1', transfer_id='rta-partner-v1', shared=True,
            source_ami_id=self.release['ami_id'])

    def replace(self, path, value):
        path.write_text(json.dumps(value))

    def cloud(self, owned=True):
        a = self.artifact
        image = dict(ImageId=a['ami_id'], OwnerId=a['account_id'], State='available', Public=False,
            Architecture='x86_64', RootDeviceType='ebs', VirtualizationType='hvm',
            SourceImageId=a['source_ami_id'], SourceImageRegion=a['region'],
            BlockDeviceMappings=[dict(DeviceName='/dev/sda1', Ebs=dict(SnapshotId=a['snapshot_ids'][0],
                VolumeSize=200, Encrypted=True, DeleteOnTermination=True))],
            Tags=[dict(Key=k, Value=v) for k,v in dict(Transfer=a['transfer_id'], ImageBuild=a['transfer_id'],
                ManifestSHA256=self.release['image_manifest_sha256'], CourseRef=self.release['course_ref'],
                ImageStatus='candidate').items()])
        snapshot = dict(SnapshotId=a['snapshot_ids'][0], OwnerId=a['account_id'], State='completed',
                        Encrypted=True, KmsKeyId=a['kms_key_arn'], VolumeSize=200)
        key = dict(Arn=a['kms_key_arn'], AWSAccountId=a['account_id'], KeyManager='CUSTOMER',
                   KeyState='Enabled', KeyUsage='ENCRYPT_DECRYPT', KeySpec='SYMMETRIC_DEFAULT')
        permissions = [dict(UserId=TARGET)] if a['shared'] else []
        policy = dict(Version='2012-10-17', Statement=[dict(Sid='OwnerAdministration', Effect='Allow',
            Principal={'AWS':'arn:aws:iam::'+a['account_id']+':root'}, Action='kms:*', Resource='*')])
        def call(service, operation, **kwargs):
            return {'describe_images': dict(Images=[image]), 'describe_snapshots':dict(Snapshots=[snapshot]),
                'describe_key':dict(KeyMetadata=key), 'describe_image_attribute':dict(LaunchPermissions=permissions),
                'describe_snapshot_attribute':dict(CreateVolumePermissions=permissions),
                'get_key_policy':dict(Policy=json.dumps(policy)),
                'get_caller_identity':dict(Account=a['account_id'])}[operation]
        aws = mock.Mock()
        aws.call.side_effect = call
        return aws, image, snapshot, key, permissions

    def test_package_contains_only_explicit_provenance_and_exact_manifest_bytes(self):
        share.package(self.package, self.digest)
        self.assertEqual(set(p.name for p in self.package.iterdir()), share.FILES | {'package.json','runtime'})
        self.assertEqual((self.package/'image-manifest.json').read_bytes(), (self.root/'manifest.json').read_bytes())
        self.assertEqual(wc.load_document(self.package/'builder.json'), dict(instance_id='i-'+'9'*17))

    def test_checksum_and_trusted_channel_reject_changed_bytes(self):
        with self.assertRaisesRegex(wc.ConfigError, 'trusted'): share.package(self.package, '0'*64)
        with (self.package/'image-manifest.json').open('a') as stream: stream.write('\n')
        with self.assertRaisesRegex(wc.ConfigError, 'checksum'): share.package(self.package, self.digest)

    def test_reject_path_injection_symlink_and_unknown_file(self):
        original = wc.load_document(self.package/'package.json')
        for name in ('../release.json', '/etc/passwd', 'credential.json'):
            bad = copy.deepcopy(original)
            bad['sha256'][name] = '0'*64
            self.replace(self.package/'package.json', bad)
            with self.subTest(name=name), self.assertRaisesRegex(wc.ConfigError, 'unexpected'): share.package(self.package)
        self.replace(self.package/'package.json', original)
        (self.package/'builder.json').unlink()
        (self.package/'builder.json').symlink_to(self.root/'builder.json')
        with self.assertRaises(wc.ConfigError): share.package(self.package)

    def test_recipient_root_wildcard_other_account_and_same_account_are_rejected(self):
        original = wc.load_document(self.package/'package.json')
        for principal in ('*', 'arn:aws:iam::'+TARGET+':root', 'arn:aws:iam::'+SOURCE+':role/Importer'):
            self.replace(self.package/'package.json', {**original, 'recipient_principal_arn':principal})
            with self.subTest(principal=principal), self.assertRaises(wc.ConfigError): share.package(self.package)
        self.replace(self.package/'package.json', {**original, 'recipient_account_id':SOURCE,
                     'recipient_principal_arn':'arn:aws:iam::'+SOURCE+':role/Importer'})
        with self.assertRaisesRegex(wc.ConfigError, 'two commercial'): share.package(self.package)

    def test_schema_v2_capability_binding_is_preserved(self):
        v2 = wc.load_document(wc.SPEC/'examples/image-release.v2.example.json')
        runtime = wc.load_document(self.package/'runtime/runtime.json')
        runtime.update(schema_version=2, content=v2['content'], course_ref=v2['course_ref'],
                       courses=[dict(profiles=v2['course_profiles'])])
        self.replace(self.package/'runtime/runtime.json',runtime)
        v2['runtime_sha256'] = wi.checksum(self.package/'runtime/runtime.json')
        manifest = dict(self.manifest, schema_version=2, content=v2['content'])
        for key in ('course_ref','deployment_ref','runtime_sha256'): manifest[key] = v2[key]
        manifest['profiles'] = v2['course_profiles']
        self.replace(self.package/'image-manifest.json', manifest)
        v2['image_manifest_sha256'] = wi.checksum(self.package/'image-manifest.json')
        v2['aws_account_id'] = SOURCE
        self.replace(self.package/'release.json', v2)
        index = wc.load_document(self.package/'package.json')
        index['sha256'] = {name:wi.checksum(self.package/name) for name in index['sha256']}
        self.replace(self.package/'package.json', index)
        _, release, _ = share.package(self.package)
        self.assertEqual(release['content'], v2['content'])
        manifest.pop('content')
        self.replace(self.package/'image-manifest.json', manifest)
        with self.assertRaises(wc.ConfigError): share.package(self.package)

    def test_owned_copy_checks_permissions_and_does_not_mutate(self):
        aws,*_ = self.cloud()
        share.artifact_live(aws, self.artifact, self.release, owned=True, sharing=TARGET)
        aws.client.assert_not_called()

    def test_wrong_owner_public_unencrypted_foreign_key_and_lineage_rejected(self):
        for location, field, bad in [('image','OwnerId',TARGET), ('image','Public',True),
            ('image','SourceImageId',self.artifact['ami_id']), ('snapshot','Encrypted',False),
            ('snapshot','OwnerId',TARGET), ('snapshot','KmsKeyId','another-key'),
            ('key','KeyManager','AWS'), ('key','KeyState','PendingDeletion'), ('key','AWSAccountId',TARGET)]:
            aws,image,snapshot,key,_ = self.cloud()
            dict(image=image,snapshot=snapshot,key=key)[location][field] = bad
            with self.subTest(field=field), self.assertRaises(wc.ConfigError):
                share.artifact_live(aws, self.artifact, self.release, owned=True, sharing=TARGET)
            aws.client.assert_not_called()

    def test_unexpected_and_public_permissions_are_rejected(self):
        for grant in [dict(Group='all'), dict(UserId='222222222222'), dict(OrganizationArn='arn:org:example')]:
            aws,*_,permissions = self.cloud()
            permissions.append(grant)
            with self.subTest(grant=grant), self.assertRaises(wc.ConfigError):
                share.artifact_live(aws, self.artifact, self.release, owned=True, sharing=TARGET)

    def test_foreign_discovery_does_not_depend_on_invisible_owner_tags(self):
        aws,image,*_ = self.cloud()
        image['Tags'] = []
        share.artifact_live(aws, self.artifact, self.release, owned=False)
        self.assertNotIn('describe_image_attribute', [call.args[1] for call in aws.call.call_args_list])

    def test_private_key_policy_rejects_another_recipient_or_extra_permissions(self):
        aws,*_ = self.cloud()
        share.key_policy_live(aws, self.artifact)
        for bad in [dict(Version='2012-10-17', Statement=[]), dict(Statement=[dict(Sid='OwnerAdministration',
            Effect='Allow', Principal={'AWS':'*'}, Action='kms:*', Resource='*')])]:
            aws.call.side_effect = None
            aws.call.return_value = dict(Policy=json.dumps(bad))
            with self.subTest(bad=bad), self.assertRaises(wc.ConfigError): share.key_policy_live(aws, self.artifact)

    def test_revoked_distribution_key_is_disabled_but_import_key_must_be_enabled(self):
        self.artifact['shared'] = False
        aws,_,_,key,_ = self.cloud()
        key['KeyState'] = 'Disabled'
        share.artifact_live(aws, self.artifact, self.release, owned=True, key_enabled=False)
        with self.assertRaises(wc.ConfigError): share.artifact_live(aws, self.artifact, self.release, owned=True)

    def test_export_never_copies_state_bindings_or_credentials(self):
        root = self.root/'export-run'
        root.mkdir()
        wi.write(root/'run.json', dict(mode='export', package_dir=str(self.package), package_sha256=self.digest))
        wi.write(root/'terraform.tfstate', dict(secret='do not copy'))
        output = self.root/'delivery'
        with mock.patch.object(share, 'status', return_value=self.artifact): digest = share.export(root, output)
        share.package(output, digest)
        self.assertEqual(set(p.name for p in output.iterdir()), share.FILES | {'package.json','distribution.json','runtime'})

    def test_imported_candidate_remains_private_and_needs_original_publication(self):
        self.artifact.update(account_id=TARGET, shared=False, source_ami_id='ami-'+'2'*17,
                             kms_key_arn=self.artifact['kms_key_arn'].replace(SOURCE,TARGET))
        aws,*_ = self.cloud()
        root = self.root/'import-run'
        root.mkdir()
        wi.write(root/'run.json', dict(mode='import', package_dir=str(self.package), package_sha256=self.digest,
            config=dict(aws_profile='partner', aws_account_id=TARGET, region='us-east-1')))
        out = self.root/'candidate'
        with mock.patch.object(share, 'status', return_value=self.artifact), mock.patch.object(share.pf, 'Aws', return_value=aws):
            share.candidate(root, out)
        wi.record_candidate(out, aws)
        self.assertEqual(wc.load_document(out/'candidate.json')['status'], 'candidate')
        self.assertFalse((out/'release.json').exists())
        self.assertEqual(wc.load_document(out/'build-instance.json'), dict(instance_id='i-'+'9'*17))
        aws.client.assert_not_called()

    def test_imported_receipt_cannot_change_baked_manifest(self):
        self.test_imported_candidate_remains_private_and_needs_original_publication()
        out = self.root/'candidate'
        with (out/'image-manifest.json').open('a') as stream: stream.write('\n')
        with self.assertRaisesRegex(wc.ConfigError, 'binding changed'): wi.record_candidate(out, mock.Mock())

    def test_plan_rejects_unexpected_types_replacements_deferred_and_duplicate_addresses(self):
        row = dict(mode='managed', address='aws_ami_copy.copy', type='aws_ami_copy', change=dict(actions=['create']))
        share.check_plan(dict(resource_changes=[row]), 'create')
        for bad in [dict(deferred_changes=[{}]), dict(complete=False), dict(errored=True),
                    dict(resource_changes=[row,row]), dict(resource_changes=[dict(row,type='aws_instance')]),
                    dict(resource_changes=[dict(row,change=dict(actions=['delete','create']))])]:
            with self.subTest(bad=bad), self.assertRaises(wc.ConfigError): share.check_plan(bad,'create')

    def test_revoke_preserves_copy_and_key(self):
        for resource, actions in [('aws_ami_copy',['delete']), ('aws_kms_key',['delete']), ('aws_ami_copy',['update'])]:
            with self.subTest(resource=resource), self.assertRaises(wc.ConfigError):
                share.check_plan(dict(resource_changes=[dict(mode='managed', address=resource+'.copy', type=resource,
                                                           change=dict(actions=actions))]), 'revoke')

    def test_apply_rejects_expired_changed_state_and_unknown_prior_dispatch(self):
        root = self.root/'run'
        attempt = root/'plans'/'one'
        attempt.mkdir(parents=True)
        wi.write(root/'run.json', {})
        wi.write(attempt/'inputs.json', {})
        (attempt/'saved.tfplan').write_bytes(b'plan')
        binding = dict(action='create',plan_sha256=wi.checksum(attempt/'saved.tfplan'),
            vars_sha256=wi.checksum(attempt/'inputs.json'), run_sha256=wi.checksum(root/'run.json'),
            module_sha256=pf_hash(), tool_sha256=share.tool_hash(),state_sha256=None,
            valid_until=wc.utc_text(datetime.now(timezone.utc)+timedelta(minutes=15)))
        wi.write(attempt/'binding.json', binding)
        with mock.patch.object(share.pf,'Aws') as aws:
            for field,bad in [('valid_until','2000-01-01T00:00:00Z'), ('state_sha256','changed'),
                              ('module_sha256','changed'),('tool_sha256','changed')]:
                self.replace(attempt/'binding.json',dict(binding,**{field:bad}))
                with self.subTest(field=field), self.assertRaisesRegex(wc.ConfigError,'stale or changed'):
                    share.apply(root,attempt,binding['plan_sha256'])
            self.replace(attempt/'binding.json',binding)
            wi.write(attempt/'apply-started.json', {})
            with self.assertRaisesRegex(wc.ConfigError,'previous apply'): share.apply(root,attempt,binding['plan_sha256'])
            aws.assert_not_called()

    def test_lock_for_new_run_does_not_create_state_directory(self):
        root = self.root/'new-run'
        with share.ws.run_lock(share.lock_directory(root)):
            self.assertFalse(root.exists())

    def test_cleanup_reconciles_api_residuals_and_reports_pending_key_grants(self):
        root = self.root/'cleanup-run'
        attempt = root/'plans'/'cleanup'
        attempt.mkdir(parents=True)
        config = dict(aws_profile='publisher', aws_account_id=SOURCE, region='us-east-1')
        wi.write(root/'run.json', dict(config=config))
        wi.write(attempt/'binding.json', dict(action='destroy'))
        targets = dict(account_id=SOURCE, region='us-east-1', transfer_id='rta-partner-v1',
                       ami_ids=[self.artifact['ami_id']], snapshot_ids=self.artifact['snapshot_ids'],
                       kms_key_arns=[self.artifact['kms_key_arn']])
        wi.write(attempt/'cleanup-targets.json', targets)
        aws = mock.Mock()
        aws.call.side_effect = lambda s,o,**kw: dict(Account=SOURCE) if o == 'get_caller_identity' else (
            dict(Images=[]) if o == 'describe_images' else dict(Snapshots=[]))
        kms, ec2 = mock.Mock(), mock.Mock()
        aws.client.side_effect = lambda name: kms if name == 'kms' else ec2
        kms.describe_key.return_value = dict(KeyMetadata=dict(AWSAccountId=SOURCE, KeyState='PendingDeletion'))
        kms.get_paginator.return_value.paginate.return_value = [dict(Grants=[dict(GrantId='existing-disabled-grant')])]
        ec2.get_paginator.return_value.paginate.return_value = [dict(Snapshots=[])]
        with mock.patch.object(share,'tf',return_value='{}'):
            report = share.reconcile(root,attempt,aws)
            self.assertTrue(report['passed'])
            self.assertEqual(report['keys'][0]['grants'], ['existing-disabled-grant'])
            kms.describe_key.return_value['KeyMetadata']['KeyState'] = 'Enabled'
            with self.assertRaisesRegex(wc.ConfigError,'cleanup is incomplete'): share.reconcile(root,attempt,aws)
            kms.describe_key.return_value['KeyMetadata']['KeyState'] = 'PendingDeletion'
            ec2.get_paginator.return_value.paginate.return_value = [dict(Snapshots=[dict(SnapshotId='snap-'+'9'*17,
                KmsKeyId=self.artifact['kms_key_arn'])])]
            with self.assertRaisesRegex(wc.ConfigError,'cleanup is incomplete'): share.reconcile(root,attempt,aws)
        self.assertEqual(len(list(attempt.glob('reconciliation-*.json'))),3)

    def test_cleanup_refuses_unowned_state(self):
        row = dict(mode='managed', type='aws_ami_copy', values=dict(id=self.artifact['ami_id'],
                   root_snapshot_id=self.artifact['snapshot_ids'][0], tags=dict(Transfer='another-transfer')))
        with self.assertRaisesRegex(wc.ConfigError,'unowned'):
            share.cleanup_targets(dict(values=dict(root_module=dict(resources=[row]))),
                dict(config=dict(aws_account_id=SOURCE,region='us-east-1')),dict(transfer_id='rta-partner-v1'))

    def test_plan_apply_hash_gate_and_new_attempt_recovery_share_one_state(self):
        root = self.root/'guarded-run'
        aws = mock.Mock()
        aws.call.return_value = dict(Account=SOURCE)
        rendered = dict(complete=True, resource_changes=[dict(mode='managed', address='aws_ami_copy.copy',
                        type='aws_ami_copy', change=dict(actions=['create']))])
        def terraform(directory, *args, capture=False):
            self.assertEqual(directory, root)
            if args[0] == 'plan':
                output = next(arg.removeprefix('-out=') for arg in args if arg.startswith('-out='))
                Path(output).write_bytes(b'fixed plan fixture')
            if args[0] == 'show': return json.dumps(rendered)
            if args[0] == 'apply': (root/'terraform.tfstate').write_text('{"serial":1}')
        with mock.patch.object(share.pf,'Aws',return_value=aws), mock.patch.object(share,'source_live'), \
             mock.patch.object(share,'tf',side_effect=terraform), mock.patch.object(share,'status',return_value=self.artifact):
            attempt = Path(share.plan(self.package,self.digest,'export','publisher',root,'create','10','2099-01-01T00:00:00Z'))
            b = wc.load_document(attempt/'binding.json')
            share.apply(root,attempt,b['plan_sha256'])
            self.assertTrue((attempt/'apply-started.json').is_file())
            self.assertTrue((attempt/'result.json').is_file())
            with self.assertRaises(wc.ConfigError): share.apply(root,attempt,b['plan_sha256'])
            second = Path(share.plan(self.package,self.digest,'export','publisher',root,'create'))
            self.assertNotEqual(second,attempt)
            self.assertEqual(wc.load_document(second/'binding.json')['state_sha256'], wi.checksum(root/'terraform.tfstate'))

    def test_wrong_profile_account_blocks_plan_before_state_or_copy(self):
        aws = mock.Mock()
        aws.call.return_value = dict(Account=TARGET)
        root = self.root/'wrong-account'
        with mock.patch.object(share.pf,'Aws',return_value=aws), mock.patch.object(share,'tf') as tf:
            with self.assertRaisesRegex(wc.ConfigError,'wrong AWS account'):
                share.plan(self.package,self.digest,'export','wrong',root,'create','10','2099-01-01T00:00:00Z')
            self.assertFalse(root.exists())
            tf.assert_not_called()

    def unbound_package(self):
        output = self.root/'unbound'
        digest = share.prepare(self.root/'release.json', self.root/'manifest.json', self.root/'builder.json',
            'rta-partner-v1', None, None, output, self.runtime)
        return output, digest

    def test_source_package_needs_no_recipient_or_recipient_credentials(self):
        output, digest = self.unbound_package()
        index, _, _ = share.package(output, digest)
        self.assertIsNone(index['recipient_account_id'])
        self.assertIsNone(index['recipient_principal_arn'])
        with mock.patch.object(share.pf,'Aws') as aws, self.assertRaisesRegex(wc.ConfigError,'bind a recipient'):
            share.plan(output,digest,'export','publisher',self.root/'run','create','10','2099-01-01T00:00:00Z')
        aws.assert_not_called()

    def test_half_bound_package_and_shared_package_without_recipient_are_rejected(self):
        output, _ = self.unbound_package()
        original = wc.load_document(output/'package.json')
        for account, principal in [(TARGET,None),(None,self.principal)]:
            self.replace(output/'package.json',dict(original,recipient_account_id=account,recipient_principal_arn=principal))
            with self.subTest(account=account), self.assertRaises(wc.ConfigError): share.package(output)
        self.replace(output/'package.json',original)
        wi.write(output/'distribution.json', self.artifact)
        original['sha256']['distribution.json'] = wi.checksum(output/'distribution.json')
        self.replace(output/'package.json',original)
        with self.assertRaisesRegex(wc.ConfigError,'exact recipient'): share.package(output)

    def staged_run(self):
        directory, digest = self.unbound_package()
        root = self.root/'staged-run'
        root.mkdir()
        wi.write(root/'run.json',dict(config=dict(aws_profile='publisher',aws_account_id=SOURCE,region='us-east-1'),
            mode='export',package_sha256=digest,package_dir=str(directory),retain_until='2099-01-01T00:00:00Z'))
        wi.write(root/'applied-inputs.json',dict(share_enabled=False,revoked=False))
        self.artifact['shared'] = False
        return root, directory, digest

    def test_staged_status_checks_enabled_key_and_owner_policy_with_only_source_profile(self):
        root, _, _ = self.staged_run()
        aws,*_ = self.cloud()
        with mock.patch.object(share,'tf',return_value=json.dumps(dict(artifact=dict(value=self.artifact)))), \
             mock.patch.object(share,'source_live') as source:
            self.assertEqual(share.status(root,aws=aws),self.artifact)
            source.assert_called_once_with(aws,self.release)
        aws.client.assert_not_called()

    def test_readiness_is_source_only_and_does_not_claim_cross_account_acceptance(self):
        root,_,_ = self.staged_run()
        with mock.patch.object(share,'status',return_value=self.artifact):
            report = share.readiness(root)
        self.assertTrue(report['share_ready'])
        self.assertFalse(report['recipient_credentials_required'])
        self.assertFalse(report['cross_account_copy_verified'])
        self.assertEqual(len(list((root/'reports').glob('*.json'))),1)

    def test_bind_requires_only_public_identifiers_and_preserves_state_and_payload(self):
        root, original, digest = self.staged_run()
        before = wi.checksum(root/'run.json')
        (root/'terraform.tfstate').write_bytes(b'unchanged source state')
        bound = self.root/'bound'
        with mock.patch.object(share.pf,'Aws') as aws:
            new_digest = share.bind(root,bound,TARGET,self.principal)
            aws.assert_not_called()
        index,_,_ = share.package(bound,new_digest)
        self.assertEqual(index['recipient_account_id'],TARGET)
        self.assertEqual(index['sha256'],wc.load_document(original/'package.json')['sha256'])
        self.assertEqual((root/'terraform.tfstate').read_bytes(),b'unchanged source state')
        self.assertEqual(wi.checksum(original/'package.json'),digest)
        self.assertNotEqual(wi.checksum(root/'run.json'),before)
        self.assertEqual(wc.load_document(root/'run.json')['package_sha256'],new_digest)
        with self.assertRaises(wc.ConfigError): share.bind(root,self.root/'another-binding','222222222222',
                                                         'arn:aws:iam::222222222222:role/Another')

    def test_source_stage_plan_creates_no_grants_and_keeps_key_enabled(self):
        directory,digest = self.unbound_package()
        root = self.root/'stage-plan'
        aws = mock.Mock()
        aws.call.return_value = dict(Account=SOURCE)
        def terraform(directory,*args,capture=False):
            if args[0] == 'plan':
                output = next(arg.removeprefix('-out=') for arg in args if arg.startswith('-out='))
                Path(output).write_bytes(b'fixed stage plan')
            if args[0] == 'show': return json.dumps(dict(complete=True,resource_changes=[]))
        with mock.patch.object(share.pf,'Aws',return_value=aws) as factory, \
             mock.patch.object(share,'source_live'),mock.patch.object(share,'tf',side_effect=terraform):
            attempt = Path(share.plan(directory,digest,'export','publisher',root,'stage','10','2099-01-01T00:00:00Z'))
        factory.assert_called_once_with(dict(aws_profile='publisher',region='us-east-1'))
        variables = wc.load_document(attempt/'inputs.json')
        self.assertEqual(variables['recipient_account_id'],'')
        self.assertEqual(variables['recipient_principal_arn'],'')
        self.assertFalse(variables['share_enabled'])
        self.assertFalse(variables['revoked'])

    def test_share_plan_cannot_create_or_replace_staged_key_or_copy(self):
        for resource in ['aws_ami_copy','aws_kms_key']:
            for actions in [['create'],['delete','create'],['delete']]:
                with self.subTest(resource=resource,actions=actions),self.assertRaises(wc.ConfigError):
                    share.check_plan(dict(resource_changes=[dict(mode='managed',address=resource+'.copy',type=resource,
                                                                change=dict(actions=actions))]),'share')
        with self.assertRaises(wc.ConfigError):
            share.check_plan(dict(resource_changes=[dict(mode='managed',address='aws_ami_launch_permission.recipient[0]',
                type='aws_ami_launch_permission',change=dict(actions=['create']))]),'stage')

    def test_readiness_and_binding_reject_revoked_copies(self):
        root,_,_ = self.staged_run()
        self.replace(root/'applied-inputs.json',dict(share_enabled=False,revoked=True))
        with self.assertRaises(wc.ConfigError): share.readiness(root)
        with self.assertRaises(wc.ConfigError): share.bind(root,self.root/'forbidden',TARGET,self.principal)


def pf_hash():
    return share.pf.module_hash('image-distribution')


if __name__ == '__main__':
    unittest.main()
