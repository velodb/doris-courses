"""No-credential checks of live-query failures and the Terraform saved-plan gate."""

import copy
from datetime import datetime, timedelta, timezone
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import workshop_preflight as pf
import workshop_runtime as builder

NOW = datetime.now(timezone.utc).replace(microsecond=0)


class AwsFailure(Exception):
    response = {'Error': {'Code': '404'}}


class FakeAws:
    def __init__(self):
        self.calls = []
        self.overrides = {}
        self.session = SimpleNamespace(get_available_regions=lambda service: ['ap-southeast-1'])

    def client(self, service):
        return SimpleNamespace(head_bucket=lambda **kwargs: self.head_bucket(**kwargs))

    def head_bucket(self, **kwargs):
        self.calls.append(('s3', 'head_bucket', kwargs))
        value = self.overrides.get('head_bucket', AwsFailure())
        if isinstance(value, Exception):
            raise value
        return value

    def call(self, service, operation, **kwargs):
        self.calls.append((service, operation, kwargs))
        if operation in self.overrides:
            value = self.overrides[operation]
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        if operation == 'get_caller_identity':
            return {'Account': '000000000000', 'Arn': 'arn:aws:sts::000000000000:assumed-role/Pilot/session'}
        if operation == 'get_role':
            return {'Role': {'Arn': 'arn:aws:iam::000000000000:role/path/Pilot'}}
        if operation == 'describe_regions':
            return {'Regions': [{'OptInStatus': 'opt-in-not-required'}]}
        if operation == 'describe_images':
            return {'Images': [{'ImageId': 'ami-00000000000000000', 'OwnerId': pf.CANONICAL,
                'State': 'available', 'Architecture': 'x86_64', 'RootDeviceType': 'ebs',
                'VirtualizationType': 'hvm', 'Name': 'ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-20261001',
                'RootDeviceName': '/dev/sda1', 'BlockDeviceMappings': [{'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': 8}}]}]}
        if operation == 'describe_instance_types':
            return {'InstanceTypes': [{'InstanceType': name, 'ProcessorInfo': {'SupportedArchitectures': ['x86_64']},
                'SupportedUsageClasses': ['on-demand'], 'SupportedVirtualizationTypes': ['hvm'],
                'VCpuInfo': {'DefaultVCpus': 4}} for name in kwargs['InstanceTypes']]}
        if operation == 'get_service_quota':
            return {'Quota': {'Value': 100}}
        if operation == 'create_vpc':
            assert kwargs['DryRun'] is True
            return {'dry_run': True}
        raise AssertionError((service, operation, kwargs))

    def items(self, service, operation, key, **kwargs):
        self.calls.append((service, operation, kwargs))
        if operation in self.overrides:
            value = self.overrides[operation]
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        if operation == 'describe_instance_type_offerings':
            return [{'Location': 'ap-southeast-1a'}]
        if operation == 'simulate_principal_policy':
            return [{'EvalActionName': action, 'EvalDecision': 'allowed'} for action in kwargs['ActionNames']]
        if operation in {'describe_instances', 'describe_volumes', 'describe_vpcs', 'describe_internet_gateways'}:
            return []
        if operation == 'get_products':
            unit = {'AmazonEC2': 'Hrs', 'AmazonVPC': 'Hrs', 'AmazonS3': 'Requests', 'AWSDataTransfer': 'GB'}[kwargs['ServiceCode']]
            filters = {f['Field']: f['Value'] for f in kwargs['Filters']}
            if filters.get('volumeApiName') == 'gp3' or filters.get('volumeType') == 'Standard':
                unit = 'GB-Mo'
            return [json.dumps({'product': {'sku': 'TEST-SKU'}, 'terms': {'OnDemand': {'offer': {
                'effectiveDate': '2026-01-01T00:00:00Z', 'priceDimensions': {'rate': {
                    'unit': unit, 'pricePerUnit': {'USD': '0.001'}, 'rateCode': 'TEST-RATE'}}}}}})]
        raise AssertionError((service, operation, key, kwargs))


class PreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.base_config = pf.wc.load_document(pf.SPEC / 'examples/event.example.yaml')
        cls.base_config['course_ref'] = pf.wc.git(pf.wc.REPO, 'rev-parse', 'HEAD').strip()
        cls.base_config['image']['owner_account_id'] = pf.CANONICAL
        cls.base_config['expires_at'] = pf.wc.utc_text(NOW + timedelta(hours=3))
        cls.config_path = cls.root / 'event.json'
        cls.config_path.write_text(json.dumps(cls.base_config))
        cls.bundle = cls.root / 'bundle'
        builder.create_bundle(cls.config_path, cls.bundle)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.config = copy.deepcopy(self.base_config)
        self.pilot = pf.wc.load_document(pf.SPEC / 'examples/pilot.example.yaml')
        self.pilot['runtime_bundle_dir'] = str(self.bundle)
        self.config, self.pilot = pf.validate_inputs(self.config, self.pilot, NOW)
        self.aws = FakeAws()
        self.environment = mock.patch.dict(os.environ, {'PATH': os.environ['PATH']}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def check(self):
        return pf.live_check(self.config, self.pilot, self.aws, 'bootstrap', NOW)

    def test_all_checks_pass_only_with_explicit_read_results(self):
        result = self.check()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['identity']['principal_arn'], 'arn:aws:iam::000000000000:role/path/Pilot')
        self.assertEqual(result['quotas']['standard_vcpus']['requested'], 4)
        self.assertEqual(set(result['cost']['lines']), {'compute', 'root_gp3', 'public_ipv4', 's3_storage',
                                                      's3_writes', 's3_reads', 'internet_egress'})
        self.assertFalse(result['cost']['billing_cap'])
        self.assertTrue(all(op.startswith(('get_', 'describe_', 'simulate_', 'head_')) or kw.get('DryRun')
                            for _, op, kw in self.aws.calls))
        self.assertNotIn('assumed-role/Pilot/session', json.dumps(result))

    def test_wrong_account_stops_before_any_other_query(self):
        self.aws.overrides['get_caller_identity'] = {'Account': '111111111111', 'Arn': 'arn:aws:iam::111111111111:root'}
        with self.assertRaisesRegex(pf.PreflightError, 'actual account'):
            self.check()
        self.assertEqual(len(self.aws.calls), 1)

    def test_root_is_rejected(self):
        self.aws.overrides['get_caller_identity'] = {'Account': '000000000000', 'Arn': 'arn:aws:iam::000000000000:root'}
        with self.assertRaisesRegex(pf.PreflightError, 'never root'):
            self.check()

    def test_permission_denial_missing_context_and_incomplete_result_fail(self):
        for result in [[], [{'EvalActionName': 'ec2:RunInstances', 'EvalDecision': 'explicitDeny'}]]:
            self.aws.overrides['simulate_principal_policy'] = result
            with self.assertRaisesRegex(pf.PreflightError, 'permissions'):
                self.check()
        actions = sorted(f'{s}:{a}' for s, values in pf.ACTIONS.items() for a in values)[:50]
        for changes in [{'EvalDecision': 'implicitDeny'}, {'MissingContextValues': ['aws:RequestTag/Owner']},
                        {'OrganizationsDecisionDetail': {'AllowedByOrganizations': False}},
                        {'PermissionsBoundaryDecisionDetail': {'AllowedByPermissionsBoundary': False}}]:
            results = [{'EvalActionName': a, 'EvalDecision': 'allowed'} for a in actions]
            results[0].update(changes)
            self.aws.overrides['simulate_principal_policy'] = results
            with self.subTest(changes=changes), self.assertRaisesRegex(pf.PreflightError, 'denied or context'):
                self.check()

    def test_query_failure_never_becomes_a_pass(self):
        self.aws.overrides['describe_volumes'] = pf.PreflightError('query: AccessDenied')
        with self.assertRaisesRegex(pf.PreflightError, 'AccessDenied'):
            self.check()

    def test_quota_uses_existing_standard_and_t_instances_and_gp3_volumes(self):
        self.aws.overrides['describe_instances'] = [{'Instances': [
            {'InstanceType': 'r7i.xlarge'}, {'InstanceType': 't3.xlarge'},
            {'InstanceType': 'r7i.xlarge', 'InstanceLifecycle': 'spot'}, {'InstanceType': 'g5.xlarge'}]}]
        self.aws.overrides['describe_volumes'] = [{'Size': 1024}]
        result = self.check()['quotas']
        self.assertEqual(result['standard_vcpus']['used'], 8)
        self.assertEqual(result['gp3_tib']['used'], 1)
        self.aws.overrides['get_service_quota'] = {'Quota': {'Value': 8}}
        with self.assertRaisesRegex(pf.PreflightError, 'insufficient vCPU'):
            self.check()

    def test_expired_and_unbounded_test_duration_fail(self):
        for expiry in [NOW, NOW - timedelta(seconds=1), NOW + timedelta(hours=5)]:
            self.config['expires_at'] = pf.wc.utc_text(expiry)
            with self.subTest(expiry=expiry), self.assertRaisesRegex(pf.PreflightError, 'expiry'):
                self.check()

    def test_budget_and_unknown_price_fail(self):
        self.config['budget_usd'] = 1
        with self.assertRaisesRegex(pf.PreflightError, 'budget'):
            self.check()
        self.config['budget_usd'] = 100
        self.aws.overrides['get_products'] = []
        with self.assertRaisesRegex(pf.PreflightError, 'pricing'):
            self.check()

    def test_price_uses_highest_paid_tier_and_records_provenance(self):
        def product(value, date='2026-01-01T00:00:00Z'):
            return json.dumps({'product': {'sku': 'test'}, 'terms': {'OnDemand': {'offer': {
                'effectiveDate': date, 'priceDimensions': {'rate': {'unit': 'GB', 'pricePerUnit': {'USD': value},
                                                                  'rateCode': 'test-rate'}}}}}})
        self.aws.overrides['get_products'] = [product('0'), product('0.12'), product('0.08'),
                                             product('1', '2099-01-01T00:00:00Z')]
        quote = pf.price(self.aws, 'AWSDataTransfer', {}, 'GB', NOW)
        self.assertEqual(quote['usd_per_unit'], '0.12')
        self.assertEqual(quote['queried_at'], pf.wc.utc_text(NOW))
        for value in ['NaN', '-1', 'Infinity']:
            self.aws.overrides['get_products'] = [product(value)]
            with self.assertRaises(pf.PreflightError):
                pf.price(self.aws, 'AWSDataTransfer', {}, 'GB', NOW)

    def test_image_region_and_offering_checks(self):
        for operation, result in [('describe_regions', {'Regions': [{'OptInStatus': 'not-opted-in'}]}),
                                  ('describe_images', {'Images': []}), ('describe_instance_type_offerings', [])]:
            with self.subTest(operation=operation):
                self.aws.overrides = {operation: result}
                with self.assertRaises(pf.PreflightError):
                    self.check()
        image = FakeAws().call('ec2', 'describe_images')['Images'][0]
        for key, value in [('Architecture', 'arm64'), ('OwnerId', '111111111111'), ('ProductCodes', [{}])]:
            bad = {**image, key: value}
            self.aws.overrides = {'describe_images': {'Images': [bad]}}
            with self.subTest(key=key), self.assertRaises(pf.PreflightError):
                self.check()

    def test_unsupported_topologies_and_numbers_fail_before_aws(self):
        for key, value in [('network_mode', 'private-egress'), ('learner_ids', [f's{i:03d}' for i in range(6)]),
                           ('spare_count', 1), ('instance_type', 't3.xlarge'), ('instance_type', 'g5.xlarge')]:
            bad = {**self.config, key: value}
            with self.subTest(key=key), self.assertRaises(pf.PreflightError):
                pf.validate_inputs(bad, self.pilot, NOW)
        for key, value in [('max_run_hours', 25), ('max_run_hours', float('nan')), ('contingency_usd', 0)]:
            with self.subTest(key=key), self.assertRaises(pf.wc.ConfigError):
                pf.validate_inputs(self.config, {**self.pilot, key: value}, NOW)

    def test_two_learner_cost_quota_and_terraform_inputs_include_both(self):
        single = self.check()
        self.config['learner_ids'] = ['s002', 's001']
        self.config, self.pilot = pf.validate_inputs(self.config, self.pilot, NOW)
        pair = self.check()
        self.assertEqual(pair['quotas']['standard_vcpus']['requested'], 8)
        self.assertEqual(pair['quotas']['gp3_tib']['requested'], 200 / 1024)
        from decimal import Decimal
        for line in ['compute', 'public_ipv4', 'root_gp3']:
            self.assertEqual(Decimal(pair['cost']['lines'][line]['quantity']),
                             2 * Decimal(single['cost']['lines'][line]['quantity']))
        variables = pf.inputs_for_terraform(self.config, self.pilot, 'pilot')
        self.assertEqual(variables['learner_ids'], ['s001', 's002'])
        self.assertNotIn('learner_id', variables)
        self.aws.overrides['get_service_quota'] = {'Quota': {'Value': 7}}
        with self.assertRaisesRegex(pf.PreflightError, 'quota'):
            self.check()

    def test_quote_without_approved_budget_cannot_pass_creation_check(self):
        self.config['budget_usd'] = None
        quote = pf.costs(self.aws, self.config, self.pilot, NOW, enforce_budget=False)
        self.assertIsNone(quote['budget_usd'])
        self.assertNotIn('status', quote)
        with self.assertRaisesRegex(pf.PreflightError, 'approved budget is missing'):
            self.check()

    def test_bundle_mutation_and_mismatched_profiles_fail(self):
        bad = {**self.config, 'course_profiles': ['dw-main']}
        with self.assertRaisesRegex(pf.PreflightError, 'profiles'):
            pf.validate_inputs(bad, self.pilot, NOW)
        with tempfile.TemporaryDirectory() as temp:
            import shutil
            bundle = Path(temp) / 'bundle'
            shutil.copytree(self.bundle, bundle)
            (bundle / 'host.py').write_text('changed')
            with self.assertRaisesRegex(pf.PreflightError, 'changed'):
                pf.validate_inputs(self.config, {**self.pilot, 'runtime_bundle_dir': str(bundle)}, NOW)

    def test_ambient_credentials_and_terraform_overrides_rejected_without_echo(self):
        for key in ['AWS_ACCESS_KEY_ID', 'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_ENDPOINT_URL_STS',
                    'TF_VAR_region', 'TF_CLI_ARGS_apply', 'TF_WORKSPACE']:
            with self.subTest(key=key), mock.patch.dict(os.environ, {key: 'DO-NOT-ECHO'}):
                with self.assertRaises(pf.PreflightError) as exc:
                    pf.explicit_environment()
                self.assertNotIn('DO-NOT-ECHO', str(exc.exception))

    def make_plan(self, root):
        directory = (Path(root) / 'run').resolve()
        def tf(args, env, **kwargs):
            if 'plan' in args:
                (directory / 'saved.tfplan').write_bytes(b'saved-plan')
            self.assertEqual(env['TF_DATA_DIR'], str(directory / 'terraform-data'))
        with mock.patch.object(pf, 'terraform', side_effect=tf):
            pf.plan(self.config, self.pilot, self.aws, 'bootstrap', directory)
        return directory

    def test_failed_check_never_invokes_terraform(self):
        self.aws.overrides['get_products'] = []
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(pf, 'terraform') as tf:
            with self.assertRaises(pf.PreflightError):
                pf.plan(self.config, self.pilot, self.aws, 'bootstrap', Path(temp) / 'run')
            tf.assert_not_called()
            self.assertFalse((Path(temp) / 'run').exists())

    def test_apply_reruns_live_checks_and_uses_only_saved_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = self.make_plan(temp)
            self.assertEqual((directory / 'binding.json').stat().st_mode & 0o777, 0o600)
            self.aws.calls.clear()
            with mock.patch.object(pf, 'terraform') as tf:
                pf.apply_saved(directory, lambda config: self.aws)
                args = tf.call_args.args[0]
                self.assertEqual(args[-1], str(directory / 'saved.tfplan'))
                self.assertIn('apply', args)
                self.assertTrue(any(op == 'get_products' for _, op, _ in self.aws.calls))
            self.aws.overrides['get_service_quota'] = {'Quota': {'Value': 0}}
            with mock.patch.object(pf, 'terraform') as tf, self.assertRaisesRegex(pf.PreflightError, 'quota'):
                pf.apply_saved(directory, lambda config: self.aws)
            tf.assert_not_called()

    def test_changed_plan_expired_report_and_modified_inputs_block_apply(self):
        for change in ['saved.tfplan', 'inputs.tfvars.json', 'expiry', 'config']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                directory = self.make_plan(temp)
                if change == 'expiry':
                    report = json.loads((directory / 'preflight.json').read_text())
                    report['valid_until'] = pf.wc.utc_text(NOW - timedelta(seconds=1))
                    (directory / 'preflight.json').write_text(json.dumps(report))
                elif change == 'config':
                    binding = json.loads((directory / 'binding.json').read_text())
                    binding['config']['budget_usd'] = 999
                    (directory / 'binding.json').write_text(json.dumps(binding))
                else:
                    (directory / change).write_text('changed')
                with mock.patch.object(pf, 'terraform') as tf, self.assertRaises(pf.PreflightError):
                    pf.apply_saved(directory, lambda config: self.aws)
                tf.assert_not_called()

    def test_pilot_cannot_plan_without_verified_foundation(self):
        with self.assertRaisesRegex(pf.PreflightError, 'foundation'):
            pf.live_check(self.config, self.pilot, self.aws, 'pilot', NOW)

    def verified_foundation(self):
        bucket = self.pilot['state_bucket_name']
        self.aws.overrides.update({
            'head_bucket': {}, 'get_bucket_location': {'LocationConstraint': self.config['region']},
            'get_bucket_tagging': {'TagSet': [{'Key': k, 'Value': v} for k, v in {
                'Project': 'doris-workshop', 'Owner': self.config['owner_id'], 'Scope': 'foundation'}.items()]},
            'get_bucket_versioning': {'Status': 'Enabled'},
            'get_public_access_block': {'PublicAccessBlockConfiguration': dict.fromkeys(
                ['BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets'], True)},
            'get_bucket_encryption': {'ServerSideEncryptionConfiguration': {'Rules': [
                {'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}},
            'get_bucket_policy': {'Policy': json.dumps({'Statement': [
                {'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
                 'Resource': [f'arn:aws:s3:::{bucket}', f'arn:aws:s3:::{bucket}/*'], 'Condition': condition}
                for condition in [{'Bool': {'aws:SecureTransport': 'false'}},
                                  {'ArnLike': {'aws:PrincipalArn': 'arn:aws:iam::000000000000:role/doris-workshop-learner-*'}}]]})}})

    def test_pilot_verifies_state_bucket_security_and_preserves_its_backend(self):
        self.verified_foundation()
        report = pf.live_check(self.config, self.pilot, self.aws, 'pilot', NOW)
        self.assertEqual(report['foundation'], 'verified')
        with tempfile.TemporaryDirectory() as temp:
            directory = (Path(temp) / 'run').resolve()
            def tf(args, env):
                if 'plan' in args:
                    (directory / 'saved.tfplan').write_bytes(b'plan')
            with mock.patch.object(pf, 'terraform', side_effect=tf):
                pf.plan(self.config, self.pilot, self.aws, 'pilot', directory)
            backend = json.loads((directory / 'backend.tfbackend.json').read_text())
            variables = json.loads((directory / 'inputs.tfvars.json').read_text())
            self.assertEqual(variables['availability_zone'], 'ap-southeast-1a')
            self.assertTrue(backend['use_lockfile'])
            self.assertEqual(backend['allowed_account_ids'], ['000000000000'])
            self.assertIn('/example-rta/terraform.tfstate', backend['key'])
        for operation, value in [('get_bucket_versioning', {'Status': 'Suspended'}),
                                 ('get_bucket_location', {'LocationConstraint': 'us-east-1'}),
                                 ('get_bucket_policy', {'Policy': '{"Statement": []}'})]:
            self.verified_foundation()
            self.aws.overrides[operation] = value
            with self.subTest(operation=operation), self.assertRaisesRegex(pf.PreflightError, 'foundation'):
                pf.live_check(self.config, self.pilot, self.aws, 'pilot', NOW)

    def test_cli_sanitizes_malformed_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'invalid.yaml'
            path.write_text('password: [DO-NOT-ECHO')
            with mock.patch('sys.stderr', new_callable=io.StringIO) as output:
                code = pf.main(['check', '--config', str(path), '--pilot', str(path), '--output', str(Path(temp) / 'report')])
            self.assertEqual(code, 2)
            self.assertNotIn('DO-NOT-ECHO', output.getvalue())


@unittest.skipUnless(importlib.util.find_spec('boto3'), 'install preflight requirements for SDK contract tests')
class SdkContractTests(unittest.TestCase):
    def setUp(self):
        import boto3
        from botocore.config import Config
        from botocore.stub import Stubber
        self.Stubber = Stubber
        self.aws = pf.Aws.__new__(pf.Aws)
        self.aws.session = boto3.Session(aws_access_key_id='fixture', aws_secret_access_key='fixture',
                                        region_name='ap-southeast-1')
        self.aws.options = Config(ignore_configured_endpoint_urls=True)
        self.aws.clients = {}

    def test_dryrun_permission_errors_and_redaction(self):
        client = self.aws.client('ec2')
        with self.Stubber(client) as stub:
            params = {'CidrBlock': '10.193.0.0/24', 'DryRun': True}
            stub.add_client_error('create_vpc', service_error_code='DryRunOperation', expected_params=params)
            self.assertEqual(self.aws.call('ec2', 'create_vpc', **params), {'dry_run': True})
            stub.add_client_error('create_vpc', service_error_code='UnauthorizedOperation',
                                  service_message='DO-NOT-ECHO', expected_params=params)
            with self.assertRaises(pf.PreflightError) as exc:
                self.aws.call('ec2', 'create_vpc', **params)
            self.assertNotIn('DO-NOT-ECHO', str(exc.exception))
            self.assertIn('UnauthorizedOperation', str(exc.exception))
        with self.assertRaisesRegex(pf.PreflightError, 'non-read-only'):
            self.aws.call('ec2', 'run_instances', MinCount=1, MaxCount=1)

    def test_pagination_reads_all_inventory_pages(self):
        client = self.aws.client('ec2')
        with self.Stubber(client) as stub:
            stub.add_response('describe_volumes', {'Volumes': [{'Size': 100}], 'NextToken': 'page2'}, {})
            stub.add_response('describe_volumes', {'Volumes': [{'Size': 200}]}, {'NextToken': 'page2'})
            self.assertEqual(sum(v['Size'] for v in self.aws.items('ec2', 'describe_volumes', 'Volumes')), 300)

    def test_transport_error_with_null_response_is_reported_safely(self):
        from botocore.exceptions import ReadTimeoutError
        error = ReadTimeoutError(endpoint_url='https://do-not-echo.example')
        error.response = None
        client = self.aws.client('ec2')
        with mock.patch.object(client, 'describe_instances', side_effect=error):
            with self.assertRaisesRegex(pf.PreflightError, 'QueryFailed') as caught:
                self.aws.call('ec2', 'describe_instances')
        self.assertNotIn('do-not-echo', str(caught.exception))

    def test_selected_profile_never_falls_back_to_host_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config'
            config.write_text('[profile pilot]\nregion = ap-southeast-1\n')
            env = {'AWS_CONFIG_FILE': str(config), 'AWS_SHARED_CREDENTIALS_FILE': str(Path(temp) / 'missing')}
            with mock.patch.dict(os.environ, env, clear=True):
                aws = pf.Aws({'aws_profile': 'pilot', 'region': 'ap-southeast-1'})
                methods = {p.METHOD for p in aws.session._session.get_component('credential_provider').providers}
                self.assertFalse(methods & {'env', 'iam-role', 'container-role'})
                self.assertIsNone(aws.session.get_credentials())


if __name__ == '__main__':
    unittest.main()
