#!/usr/bin/env python3
"""Read-only live checks and a saved-plan gate for the first AWS integration run."""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import workshop_config as wc

SPEC = wc.SPEC
MODULES = SPEC / 'terraform'
CANONICAL = '099720109477'
# Deliberately excludes burstable T instances and special/GPU quota families.
STANDARD = re.compile(r'^[acdhimrz][0-9][a-z0-9-]*\.[a-z0-9]+$')


class PreflightError(wc.ConfigError):
    """A failed check or an unknown condition blocks planning and apply."""


def require(condition, message):
    if not condition:
        raise PreflightError(message)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def explicit_environment():
    # Credentials must come from the selected profile, including its role/SSO chain.
    forbidden = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
                 'AWS_SECURITY_TOKEN', 'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_ROLE_ARN',
                 'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI', 'AWS_CONTAINER_CREDENTIALS_FULL_URI')
    require(not any(os.environ.get(key) for key in forbidden),
            'identity: remove ambient AWS credential overrides and use the selected profile')
    require(not any(key.startswith('AWS_ENDPOINT_URL') and val for key, val in os.environ.items()),
            'identity: remove custom AWS endpoint overrides')
    env = dict(os.environ)
    env.update(AWS_EC2_METADATA_DISABLED='true', AWS_IGNORE_CONFIGURED_ENDPOINT_URLS='true',
               AWS_PAGER='', TF_INPUT='0')
    require(not any(key.startswith(('TF_CLI_ARGS', 'TF_VAR_')) for key in env),
            'terraform: remove implicit TF_CLI_ARGS/TF_VAR overrides')
    require(not env.get('TF_WORKSPACE'), 'terraform: remove implicit TF_WORKSPACE override')
    return env


class Aws:
    """No API mutations: only reads, simulations and EC2 DryRun are exposed."""

    def __init__(self, config):
        explicit_environment()
        try:
            import boto3
            from botocore.config import Config
            self.session = boto3.Session(profile_name=config['aws_profile'], region_name=config['region'])
            profiles = self.session._session.full_config.get('profiles', {})
            name = config['aws_profile']
            visited = set()
            while name:
                require(name not in visited, 'identity: cyclic credential profile chain')
                visited.add(name)
                selected = profiles.get(name, {})
                require(not selected.get('credential_source'),
                        'identity: use SSO, credential_process or an explicit source_profile chain')
                name = selected.get('source_profile')
            resolver = self.session._session.get_component('credential_provider')
            resolver.providers = [p for p in resolver.providers if p.METHOD not in {'env', 'iam-role', 'container-role'}]
            self.options = Config(connect_timeout=5, read_timeout=20,
                                  retries={'max_attempts': 2}, ignore_configured_endpoint_urls=True)
            self.clients = {}
        except ImportError as exc:
            raise PreflightError('tools: install workshop/aws/preflight-requirements.txt') from exc
        except PreflightError:
            raise
        except Exception as exc:
            raise PreflightError('identity: selected profile cannot be loaded') from exc

    def client(self, service):
        if service not in self.clients:
            options = {'region_name': 'us-east-1'} if service == 'pricing' else {}
            self.clients[service] = self.session.client(service, config=self.options, **options)
        return self.clients[service]

    def call(self, service, operation, **kwargs):
        require(operation.startswith(('get_', 'describe_', 'list_', 'simulate_')) or kwargs.get('DryRun') is True,
                'internal: non-read-only AWS operation rejected')
        try:
            return getattr(self.client(service), operation)(**kwargs)
        except Exception as exc:
            response = getattr(exc, 'response', None) or {}
            code = (response.get('Error') or {}).get('Code', '')
            if code == 'DryRunOperation' and kwargs.get('DryRun') is True:
                return {'dry_run': True}
            if code in {'404', 'NoSuchBucket', 'NotFound'} and operation == 'head_bucket':
                return {'absent': True}
            # Never echo SDK messages, request bodies or credentials.
            safe = code if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', code) else 'QueryFailed'
            raise PreflightError(f'query: {service}.{operation} failed ({safe})') from exc

    def items(self, service, operation, key, **kwargs):
        client = self.client(service)
        if not client.can_paginate(operation):
            return self.call(service, operation, **kwargs)[key]
        try:
            return [item for page in client.get_paginator(operation).paginate(**kwargs) for item in page[key]]
        except Exception as exc:
            raise PreflightError(f'query: {service}.{operation} pagination failed') from exc


def validate_inputs(config, pilot, now=None):
    config, _, _ = wc.validate_config(config, now=now)
    pilot = dict(pilot)
    wc.schema_check(pilot, 'pilot.schema.json')
    wc.check_plain_strings(pilot)
    require(all(not isinstance(v, float) or math.isfinite(v) for v in pilot.values()),
            'pilot: all numbers must be finite')
    require(config['aws_partition'] == 'aws' and config['network_mode'] == 'public-egress',
            'scope: first run requires commercial AWS and a new VPC with public egress')
    require(1 <= len(config['learner_ids']) <= 5 and config['spare_count'] == 0,
            'scope: integration pilot supports one to five learners and no spares')
    if config['image']['mode'] == 'base':
        require(config['image']['owner_account_id'] == CANONICAL, 'image: base mode requires an official Canonical AMI')
    else:
        import workshop_release
        require(bool(pilot.get('image_release_file')), 'image: prebuilt mode requires an immutable validated release file')
        pilot['image_release_file'] = str(Path(pilot['image_release_file']).resolve())
        release = workshop_release.load_release(pilot['image_release_file'], config)
    require(bool(STANDARD.fullmatch(config['instance_type'])),
            'scope: select a non-burstable Standard On-Demand instance family')
    require(config['disk_gib'] >= 20, 'disk_gib: pilot requires at least 20 GiB')
    bundle = Path(pilot['runtime_bundle_dir']).resolve()
    runtime = wc.load_document(bundle / 'runtime.json')
    require(isinstance(runtime, dict) and runtime.get('kind') == 'doris-workshop-runtime',
            'runtime: build a real host runtime bundle')
    require(runtime.get('course_ref') == config['course_ref'], 'runtime: course SHA must match config')
    require(sorted(p for c in runtime['courses'] for p in c['profiles']) == config['course_profiles'],
            'runtime: profiles must match config')
    require(runtime.get('runtime') == json.loads((SPEC / 'profiles.json').read_text())['runtime'],
            'runtime: platform contract must match')
    for name, checksum in runtime['sha256'].items():
        path = bundle / name
        require(path.resolve().is_relative_to(bundle) and path.is_file() and file_hash(path) == checksum,
                'runtime: bundle file missing, unsafe or changed')
    locks = runtime.get('python_locks', {})
    require(isinstance(locks, dict) and (not locks or
            (set(locks) == {'server', *(c['kernel'] for c in runtime['courses'])} and
             all(path == name + '.requirements.lock' for name, path in locks.items()))),
            'runtime: invalid Python lock mapping')
    # Validated pre-overlay images retain their exact old runtime fingerprint.
    operations = {'operations.py'} if 'operations.py' in runtime['sha256'] else set()
    ui = {'ui.tar'} if 'ui.tar' in runtime['sha256'] else set()
    require({'install_host.sh', 'source.tar', 'host.py', 'check.py', 'kernel.py', 'serve.py', 'portal.py',
             'packages.json', 'doris-workshop-jupyter.service'} | operations | ui | set(locks.values()) == runtime['sha256'].keys(),
            'runtime: incomplete production bundle')
    if config['image']['mode'] == 'prebuilt':
        require(file_hash(bundle / 'runtime.json') == release['runtime_sha256'], 'image: local runtime does not match the released image')
    if pilot.get('browser_routes_file'):
        import workshop_urls
        pilot['browser_routes_file'] = str(Path(pilot['browser_routes_file']).resolve())
        routes = wc.load_document(pilot['browser_routes_file'])
        require(set(routes) == set(config['learner_ids']), 'browser: routes must match the assigned learners')
        for learner, ports in routes.items(): workshop_urls.build_routes(config, learner, ports)
    if pilot.get('shared_access_file'):
        pilot['shared_access_file'] = str(Path(pilot['shared_access_file']).resolve())
        shared = wc.load_document(pilot['shared_access_file'])
        require(isinstance(shared, dict) and shared.get('schema_version') == 1 and
                shared.get('account_id') == config['aws_account_id'] and shared.get('region') == config['region'] and
                shared.get('domain') == config['domain'], 'shared network: access output must match activity identity')
    pilot['runtime_bundle_dir'] = str(bundle)
    return config, pilot


def load_inputs(config_path, pilot_path, now=None):
    return validate_inputs(wc.load_document(config_path), wc.load_document(pilot_path), now)


def principal_arn(aws, identity):
    arn = identity['Arn']
    if ':assumed-role/' in arn:
        name = arn.split(':assumed-role/', 1)[1].split('/')[0]
        return aws.call('iam', 'get_role', RoleName=name)['Role']['Arn']
    require(':user/' in arn, 'identity: use an IAM user or assumed role, never root/federated-user')
    return arn


# Conservative simulation at wildcard scope. Scoped/conditional roles may need
# a more precise future simulator; ambiguous/missing context never counts as pass.
ACTIONS = {
    'ec2': '''RunInstances TerminateInstances CreateTags DeleteTags CreateVolume DeleteVolume
        CreateVpc DeleteVpc ModifyVpcAttribute CreateSubnet DeleteSubnet
        CreateInternetGateway DeleteInternetGateway AttachInternetGateway DetachInternetGateway
        CreateRouteTable DeleteRouteTable CreateRoute DeleteRoute AssociateRouteTable
        DisassociateRouteTable CreateSecurityGroup DeleteSecurityGroup
        AuthorizeSecurityGroupEgress RevokeSecurityGroupEgress ModifyInstanceAttribute'''.split(),
    'iam': '''CreateRole DeleteRole GetRole TagRole UntagRole PutRolePolicy GetRolePolicy DeleteRolePolicy
        CreateInstanceProfile DeleteInstanceProfile GetInstanceProfile TagInstanceProfile
        UntagInstanceProfile AddRoleToInstanceProfile RemoveRoleFromInstanceProfile PassRole
        ListRolePolicies ListAttachedRolePolicies ListInstanceProfilesForRole'''.split(),
    's3': '''CreateBucket DeleteBucket ListBucket GetBucketLocation GetBucketTagging PutBucketTagging
        GetBucketPolicy GetBucketPolicyStatus PutBucketPolicy DeleteBucketPolicy
        GetBucketPublicAccessBlock PutBucketPublicAccessBlock GetEncryptionConfiguration
        PutEncryptionConfiguration GetBucketVersioning PutBucketVersioning GetBucketAcl
        GetObject PutObject DeleteObject ListBucketVersions GetObjectVersion DeleteObjectVersion
        AbortMultipartUpload ListMultipartUploadParts'''.split(),
    'ssm': '''CreateDocument DescribeDocument GetDocument UpdateDocument DeleteDocument
        AddTagsToResource RemoveTagsFromResource ListTagsForResource SendCommand
        GetCommandInvocation ListCommands ListCommandInvocations DescribeInstanceInformation
        StartSession ResumeSession TerminateSession'''.split(),
    'ssmmessages': ['OpenDataChannel'],
}


def permissions(aws, config, arn):
    actions = sorted(f'{service}:{action}' for service, names in ACTIONS.items() for action in names)
    context = [{'ContextKeyName': 'aws:RequestedRegion', 'ContextKeyValues': [config['region']],
                'ContextKeyType': 'string'},
               {'ContextKeyName': 'iam:PassedToService', 'ContextKeyValues': ['ec2.amazonaws.com'],
                'ContextKeyType': 'string'}]
    for offset in range(0, len(actions), 50):
        batch = actions[offset:offset + 50]
        results = aws.items('iam', 'simulate_principal_policy', 'EvaluationResults',
                            PolicySourceArn=arn, ActionNames=batch, ResourceArns=['*'], ContextEntries=context)
        require({r['EvalActionName'].lower() for r in results} == {a.lower() for a in batch},
                'permissions: incomplete simulation results')
        require(all(r['EvalDecision'] == 'allowed' and not r.get('MissingContextValues') and
                    r.get('PermissionsBoundaryDecisionDetail', {}).get('AllowedByPermissionsBoundary', True) and
                    r.get('OrganizationsDecisionDetail', {}).get('AllowedByOrganizations', True) for r in results),
                'permissions: required deployment/SSM/destroy action denied or context unknown')


def quota(aws, service, code, used, extra, name):
    limit = aws.call('service-quotas', 'get_service_quota', ServiceCode=service, QuotaCode=code)['Quota']['Value']
    require(math.isfinite(limit) and used + extra <= limit, f'quota: insufficient {name} headroom')
    return {'used': used, 'requested': extra, 'limit': limit, 'quota_code': code}


def quotas(aws, config, selected, *, shared=False):
    reservations = aws.items('ec2', 'describe_instances', 'Reservations', Filters=[
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping']}])
    instances = [i for r in reservations for i in r['Instances'] if not i.get('InstanceLifecycle') and
                 (STANDARD.fullmatch(i['InstanceType']) or re.match(r'^t[0-9]', i['InstanceType']))]
    types = sorted({i['InstanceType'] for i in instances})
    descriptions = {}
    for start in range(0, len(types), 100):
        descriptions.update({t['InstanceType']: t for t in aws.call(
            'ec2', 'describe_instance_types', InstanceTypes=types[start:start + 100])['InstanceTypes']})
    used = sum(descriptions[i['InstanceType']]['VCpuInfo']['DefaultVCpus'] for i in instances)
    volumes = aws.items('ec2', 'describe_volumes', 'Volumes', Filters=[{'Name': 'volume-type', 'Values': ['gp3']}])
    vpcs = aws.items('ec2', 'describe_vpcs', 'Vpcs')
    igws = aws.items('ec2', 'describe_internet_gateways', 'InternetGateways')
    return {
        'standard_vcpus': quota(aws, 'ec2', 'L-1216C47A', used,
                                selected['VCpuInfo']['DefaultVCpus'] * len(config['learner_ids']), 'vCPU'),
        'gp3_tib': quota(aws, 'ebs', 'L-7A658B76', sum(v['Size'] for v in volumes) / 1024,
                         config['disk_gib'] * len(config['learner_ids']) / 1024, 'gp3 storage'),
        'vpcs': quota(aws, 'vpc', 'L-F678F1CE', len(vpcs), 0 if shared else 1, 'VPC'),
        'internet_gateways': quota(aws, 'vpc', 'L-A4707A72', len(igws), 0 if shared else 1, 'Internet Gateway'),
    }


def price(aws, service, filters, unit, now):
    products = aws.items('pricing', 'get_products', 'PriceList', ServiceCode=service, Filters=[
        {'Type': 'TERM_MATCH', 'Field': k, 'Value': v} for k, v in filters.items()])
    candidates = []
    for raw in products:
        product = json.loads(raw)
        for term in product.get('terms', {}).get('OnDemand', {}).values():
            effective = wc.timestamp(term['effectiveDate'], 'pricing.effectiveDate')
            if effective > now:
                continue
            for dimension in term['priceDimensions'].values():
                if dimension['unit'] != unit:
                    continue
                rate = Decimal(dimension['pricePerUnit']['USD'])
                require(rate.is_finite() and rate >= 0, 'pricing: invalid USD rate')
                candidates.append((rate, product['product']['sku'], term['effectiveDate'], dimension['rateCode']))
    require(bool(candidates), 'pricing: required On-Demand USD product/unit is unknown')
    # Upper bound across published tiers, ignoring free-tier/volume discounts.
    rate, sku, effective, code = max(candidates)
    require(rate > 0, 'pricing: only zero/free rates found; paid rate is unknown')
    return {'usd_per_unit': str(rate), 'unit': unit, 'sku': sku, 'rate_code': code,
            'effective_at': effective, 'queried_at': wc.utc_text(now), 'service': service, 'filters': filters}


def costs(aws, config, pilot, now, *, enforce_budget=True):
    region = config['region']
    specifications = {
        'compute': ('AmazonEC2', {'regionCode': region, 'instanceType': config['instance_type'],
                     'operatingSystem': 'Linux', 'tenancy': 'Shared', 'preInstalledSw': 'NA',
                     'capacitystatus': 'Used'}, 'Hrs'),
        'root_gp3': ('AmazonEC2', {'regionCode': region, 'volumeApiName': 'gp3', 'productFamily': 'Storage'}, 'GB-Mo'),
        'public_ipv4': ('AmazonVPC', {'regionCode': region, 'group': 'VPCPublicIPv4Address'}, 'Hrs'),
        's3_storage': ('AmazonS3', {'regionCode': region, 'storageClass': 'General Purpose',
                      'volumeType': 'Standard'}, 'GB-Mo'),
        's3_writes': ('AmazonS3', {'regionCode': region, 'group': 'S3-API-Tier1'}, 'Requests'),
        's3_reads': ('AmazonS3', {'regionCode': region, 'group': 'S3-API-Tier2'}, 'Requests'),
        'internet_egress': ('AWSDataTransfer', {'fromRegionCode': region, 'toLocation': 'External',
                           'transferType': 'AWS Outbound'}, 'GB'),
    }
    hours = Decimal(str(pilot['max_run_hours'])) + Decimal(str(pilot['cleanup_buffer_hours']))
    count = len(config['learner_ids'])
    quantities = {'compute': hours * count, 'public_ipv4': hours * count,
                  # 28-day denominator conservatively covers the shortest month.
                  'root_gp3': hours * config['disk_gib'] * count / Decimal(28 * 24),
                  's3_storage': Decimal(str(pilot['s3_storage_gb_months'])),
                  's3_writes': Decimal(pilot['s3_write_requests']), 's3_reads': Decimal(pilot['s3_read_requests']),
                  'internet_egress': Decimal(str(pilot['internet_egress_gb']))}
    lines = {}
    total = Decimal(str(pilot['contingency_usd']))
    for name, (service, filters, unit) in specifications.items():
        quote = price(aws, service, filters, unit, now)
        amount = quantities[name] * Decimal(quote['usd_per_unit'])
        total += amount
        lines[name] = {**quote, 'quantity': str(quantities[name]), 'estimated_usd': str(amount)}
    if enforce_budget:
        require(config.get('budget_usd') is not None and
                total <= Decimal(str(config['budget_usd'])),
                'budget: approved budget is missing or estimated cost plus contingency exceeds budget_usd')
    return {'lines': lines, 'total_usd': str(total), 'budget_usd': config['budget_usd'],
            'contingency_usd': pilot['contingency_usd'], 'assumptions': pilot,
            'excluded_resources': ['ALB', 'NAT Gateway', 'model API', 'snapshots'],
            'billing_cap': False}


def shared_costs(aws, config, pilot, access, now, estimate):
    """Include the already-running entry for the entire approved test window."""
    hours = Decimal(str(pilot['max_run_hours'])) + Decimal(str(pilot['cleanup_buffer_hours']))
    region = config['region']
    specs = {
        'shared_alb': ('AWSELB', {'regionCode': region, 'productFamily': 'Load Balancer-Application'}, 'Hrs', hours),
        'shared_alb_lcu': ('AWSELB', {'regionCode': region, 'productFamily': 'Load Balancer-Application'}, 'LCU-Hrs', hours * 10),
        'shared_alb_ipv4': ('AmazonVPC', {'regionCode': region, 'group': 'VPCPublicIPv4Address'}, 'Hrs', hours * len(access['subnet_ids'])),
    }
    portal = access.get('portal')
    if portal is not None:
        require(isinstance(portal, dict) and re.fullmatch(r'i-[0-9a-f]{17}', portal.get('instance_id', '')),
                'shared network: invalid portal instance')
        reservations = aws.call('ec2', 'describe_instances', InstanceIds=[portal['instance_id']])['Reservations']
        instances = [i for r in reservations for i in r['Instances']]
        require(len(instances) == 1 and instances[0].get('InstanceId') == portal['instance_id'] and
                instances[0].get('VpcId') == access['vpc_id'] and instances[0].get('State', {}).get('Name') == 'running',
                'shared network: portal is not running in the selected VPC')
        instance = instances[0]
        tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
        require(all(tags.get(k) == v for k, v in {'Project': 'doris-workshop', 'Owner': config['owner_id'],
                'Scope': 'shared-portal', 'Access': access['access_id']}.items()) and 'Event' not in tags,
                'shared network: portal ownership mismatch')
        roots = [v['Ebs']['VolumeId'] for v in instance.get('BlockDeviceMappings', [])
                 if v.get('DeviceName') == instance.get('RootDeviceName') and v.get('Ebs', {}).get('VolumeId')]
        require(len(roots) == 1, 'shared network: portal root volume is unknown')
        volumes = aws.call('ec2', 'describe_volumes', VolumeIds=roots)['Volumes']
        require(len(volumes) == 1 and volumes[0].get('VolumeType') == 'gp3' and volumes[0].get('Size', 0) > 0,
                'shared network: portal root must be a known gp3 volume')
        specs.update({
            'portal_compute': ('AmazonEC2', {'regionCode': region, 'instanceType': instance['InstanceType'],
                'operatingSystem': 'Linux', 'tenancy': 'Shared', 'preInstalledSw': 'NA', 'capacitystatus': 'Used'}, 'Hrs', hours),
            'portal_root_gp3': ('AmazonEC2', {'regionCode': region, 'volumeApiName': 'gp3', 'productFamily': 'Storage'},
                'GB-Mo', hours * volumes[0]['Size'] / Decimal(28 * 24)),
            'portal_ipv4': ('AmazonVPC', {'regionCode': region, 'group': 'VPCPublicIPv4Address'}, 'Hrs', hours),
        })
    total = Decimal(estimate['total_usd'])
    for name, (service, filters, unit, quantity) in specs.items():
        quote = price(aws, service, filters, unit, now)
        amount = quantity * Decimal(quote['usd_per_unit'])
        total += amount
        estimate['lines'][name] = {**quote, 'quantity': str(quantity), 'estimated_usd': str(amount)}
    require(config.get('budget_usd') is not None and total <= Decimal(str(config['budget_usd'])),
            'budget: shared entry and activity exceed budget_usd or approved budget is missing')
    estimate['total_usd'] = str(total)
    estimate['excluded_resources'] = ['model API', 'snapshots', 'DNS hosted zone and queries']
    return estimate


def foundation(aws, config, pilot, stage):
    bucket = pilot['state_bucket_name']
    # ExpectedBucketOwner prevents an unrelated global name from being adopted.
    try:
        aws.client('s3').head_bucket(Bucket=bucket, ExpectedBucketOwner=config['aws_account_id'])
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
        require(stage == 'bootstrap' and code in {'404', 'NoSuchBucket', 'NotFound'},
                'foundation: state bucket is inaccessible, missing or owned by another account')
        return 'new'
    location = aws.call('s3', 'get_bucket_location', Bucket=bucket)['LocationConstraint'] or 'us-east-1'
    require(location == config['region'], 'foundation: state bucket Region mismatch')
    tags = {t['Key']: t['Value'] for t in aws.call('s3', 'get_bucket_tagging', Bucket=bucket)['TagSet']}
    require(all(tags.get(k) == v for k, v in {'Project': 'doris-workshop', 'Owner': config['owner_id'], 'Scope': 'foundation'}.items()),
            'foundation: state bucket ownership tags mismatch')
    require(aws.call('s3', 'get_bucket_versioning', Bucket=bucket).get('Status') == 'Enabled',
            'foundation: state bucket versioning is required')
    block = aws.call('s3', 'get_public_access_block', Bucket=bucket)['PublicAccessBlockConfiguration']
    require(all(block.get(k) is True for k in ['BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets']),
            'foundation: state bucket must block all public access')
    rules = aws.call('s3', 'get_bucket_encryption', Bucket=bucket)['ServerSideEncryptionConfiguration']['Rules']
    require(any(r.get('ApplyServerSideEncryptionByDefault', {}).get('SSEAlgorithm') == 'AES256' for r in rules),
            'foundation: state bucket requires the pilot AES256 encryption configuration')
    policy = json.loads(aws.call('s3', 'get_bucket_policy', Bucket=bucket)['Policy'])
    expected = f"arn:aws:iam::{config['aws_account_id']}:role/doris-workshop-learner-*"
    statements = policy['Statement']
    resources = {f'arn:aws:s3:::{bucket}', f'arn:aws:s3:::{bucket}/*'}
    def deny(condition):
        return any(s.get('Effect') == 'Deny' and s.get('Principal') == '*' and s.get('Action') == 's3:*'
                   and set(s.get('Resource', [])) == resources and s.get('Condition') == condition for s in statements)
    require(deny({'Bool': {'aws:SecureTransport': 'false'}}) and
            deny({'ArnLike': {'aws:PrincipalArn': expected}}), 'foundation: required state-deny policies are missing')
    return 'verified'


def live_check(config, pilot, aws, stage='pilot', now=None):
    now = now or datetime.now(timezone.utc)
    hours = (wc.timestamp(config['expires_at'], 'expires_at') - now).total_seconds() / 3600
    require(0 < hours <= pilot['max_run_hours'], 'expiry: set a future expiry within max_run_hours (at most 24h)')
    identity = aws.call('sts', 'get_caller_identity')
    require(identity['Account'] == config['aws_account_id'] and identity['Arn'].startswith('arn:aws:'),
            'identity: actual account/partition does not match the explicit target')
    arn = principal_arn(aws, identity)
    regions = aws.call('ec2', 'describe_regions', RegionNames=[config['region']], AllRegions=True)['Regions']
    require(len(regions) == 1 and regions[0]['OptInStatus'] in {'opt-in-not-required', 'opted-in'},
            'region: target Region is unavailable or not enabled')
    if config['image']['mode'] == 'base':
        images = aws.call('ec2', 'describe_images', ImageIds=[config['image']['ami_id']], Owners=[CANONICAL])['Images']
        require(len(images) == 1, 'image: selected AMI is unavailable to this identity')
        image = images[0]
        require(image['OwnerId'] == CANONICAL and image['State'] == 'available' and image['Architecture'] == 'x86_64'
                and image['RootDeviceType'] == 'ebs' and image['VirtualizationType'] == 'hvm'
                and image['Name'].startswith('ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-')
                and not image.get('ProductCodes'), 'image: require an official available Ubuntu 24.04 amd64 EBS AMI')
    else:
        import workshop_release
        release = workshop_release.load_release(pilot['image_release_file'], config)
        image = workshop_release.verify_live(aws, config, release)
    root = next(b['Ebs']['VolumeSize'] for b in image['BlockDeviceMappings'] if b['DeviceName'] == image['RootDeviceName'])
    require(config['disk_gib'] >= root, 'disk_gib: smaller than the AMI root volume')
    types = aws.call('ec2', 'describe_instance_types', InstanceTypes=[config['instance_type']])['InstanceTypes']
    require(len(types) == 1, 'instance: unknown instance type')
    selected = types[0]
    require('x86_64' in selected['ProcessorInfo']['SupportedArchitectures'] and
            'on-demand' in selected['SupportedUsageClasses'] and 'hvm' in selected['SupportedVirtualizationTypes'],
            'instance: incompatible architecture, purchase class or virtualization')
    offerings = aws.items('ec2', 'describe_instance_type_offerings', 'InstanceTypeOfferings',
                          LocationType='availability-zone', Filters=[{'Name': 'instance-type', 'Values': [config['instance_type']]}])
    require(bool(offerings), 'instance: no offering in the target Region')
    # Actual capacity is determined at launch, not by this offering inventory.
    for service in ('ssm', 's3', 'ec2', 'iam'):
        require(config['region'] in aws.session.get_available_regions(service) or service == 'iam',
                'services: required service endpoint unavailable')
    permissions(aws, config, arn)
    access = wc.load_document(pilot['shared_access_file']) if pilot.get('shared_access_file') else None
    require(access is None or stage == 'pilot', 'shared network: use an existing foundation and pilot stage')
    topology = None
    if access is not None:
        import workshop_shared_preflight
        topology = workshop_shared_preflight.verify(config, access, aws, {o['Location'] for o in offerings})
    quota_results = quotas(aws, config, selected, shared=access is not None)
    require(aws.call('ec2', 'create_vpc', CidrBlock='10.193.0.0/24', DryRun=True).get('dry_run') is True,
            'permissions: EC2 DryRun did not explicitly succeed')
    state = foundation(aws, config, pilot, stage)
    require(stage != 'bootstrap' or state == 'new',
            'foundation: bucket already exists; preserve original bootstrap state and use stage pilot')
    estimate = costs(aws, config, pilot, now, enforce_budget=access is None)
    if access is not None:
        estimate = shared_costs(aws, config, pilot, access, now, estimate)
    return {'schema_version': 1, 'kind': 'aws-workshop-preflight', 'status': 'passed', 'stage': stage,
            'checked_at': wc.utc_text(now), 'valid_until': wc.utc_text(min(now + timedelta(minutes=15),
                        wc.timestamp(config['expires_at'], 'expires_at'))),
            'config_sha256': wc.canonical_hash(config), 'pilot_sha256': wc.canonical_hash(pilot),
            'identity': {'account': identity['Account'], 'principal_arn': arn},
            'region': config['region'], 'ami_id': image['ImageId'],
            'availability_zones': topology['availability_zones'] if topology else sorted(o['Location'] for o in offerings),
            **({'shared_network': topology} if topology else {}),
            'quotas': quota_results, 'foundation': state, 'cost': estimate,
            'limits': ['IAM simulation does not prove session-policy/endpoint-policy permission or launch capacity',
                       'Usage estimates and contingency are not billing caps; operator must destroy and reconcile']}


def terraform(args, env, *, capture=False):
    require(shutil.which('terraform') is not None, 'tools: put Terraform 1.10+ on PATH')
    # No shell interpolation; explicit args cannot be replaced by TF_CLI_ARGS.
    result = subprocess.run(['terraform', *args], env=env, capture_output=capture, text=True)
    require(result.returncode == 0, 'terraform: command failed; inspect private plan/runtime diagnostics')
    return result.stdout if capture else None


def inputs_for_terraform(config, pilot, stage, availability_zone=None, *, release=None, browser_routes=None, shared_access=None):
    variables = {'aws_profile': config['aws_profile'], 'expected_account_id': config['aws_account_id'],
                 'region': config['region'], 'owner_id': config['owner_id'], 'state_bucket_name': pilot['state_bucket_name']}
    if stage == 'pilot':
        variables.update(event_id=config['event_id'], learner_ids=config['learner_ids'],
                         availability_zone=availability_zone,
                         ami_id=config['image']['ami_id'], instance_type=config['instance_type'],
                         disk_gib=config['disk_gib'], expires_at=config['expires_at'],
                         runtime_bundle_dir=pilot['runtime_bundle_dir'])
        if config['image']['mode'] == 'prebuilt':
            import workshop_release
            release = release or workshop_release.load_release(pilot['image_release_file'], config)
            variables.update(image_mode='prebuilt', ami_owner_account_id=config['aws_account_id'],
                image_release_ref=release['release_ref'], image_release_sha256=wc.canonical_hash(release),
                image_manifest_sha256=release['image_manifest_sha256'], image_runtime_sha256=release['runtime_sha256'])
        if browser_routes is not None or pilot.get('browser_routes_file'):
            variables['browser_routes'] = browser_routes if browser_routes is not None else wc.load_document(pilot['browser_routes_file'])
        if shared_access is not None or pilot.get('shared_access_file'):
            shared_access = shared_access if shared_access is not None else wc.load_document(pilot['shared_access_file'])
            require(availability_zone in shared_access['subnet_ids'], 'shared network: selected zone is not in the access output')
            variables.update(vpc_id=shared_access['vpc_id'], subnet_id=shared_access['subnet_ids'][availability_zone],
                             gateway_security_group_id=shared_access['gateway_security_group_id'])
    return variables


def plan(config, pilot, aws, stage, directory, *, terraform_runner=None, previous_manifest=None, previous_record=None):
    run = terraform_runner or terraform
    env = explicit_environment()
    shared_path = pilot.get('shared_access_file')
    shared_hash = file_hash(shared_path) if shared_path else None
    report = live_check(config, pilot, aws, stage)
    require(shared_path is None or file_hash(shared_path) == shared_hash,
            'shared network: access output changed during preflight')
    shared_access = wc.load_document(shared_path) if shared_path else None
    if previous_manifest is not None:
        require(stage == 'pilot' and previous_record is not None, 'roster: previous run is required for activity changes')
    require(not directory.exists(), 'output: run directory exists; select a new directory')
    directory.mkdir(parents=True, mode=0o700)
    directory = directory.resolve()
    # Each run owns its Terraform data directory. Switching events cannot reuse a backend.
    env['TF_DATA_DIR'] = str(directory / 'terraform-data')
    variables = inputs_for_terraform(config, pilot, stage, report['availability_zones'][0], shared_access=shared_access)
    wc.write_manifest(directory / 'inputs.tfvars.json', variables)
    wc.write_manifest(directory / 'preflight.json', report)
    if stage == 'pilot':
        resolved, runtime = wc.resolve_profiles(config, wc.REPO)
        allocations = wc.build_manifest(config, resolved, runtime, previous=previous_manifest)
        wc.write_manifest(directory / 'allocations.json', allocations)
    module = f'-chdir={MODULES / stage}'
    init = [module, 'init', '-input=false', '-lockfile=readonly']
    if stage == 'pilot':
        backend = {'bucket': pilot['state_bucket_name'], 'region': config['region'], 'profile': config['aws_profile'],
                   'allowed_account_ids': [config['aws_account_id']], 'encrypt': True, 'use_lockfile': True,
                   'key': f"aws-workshop/aws/{config['aws_account_id']}/{config['region']}/{config['event_id']}/terraform.tfstate"}
        wc.write_manifest(directory / 'backend.tfbackend.json', backend)
        init.append(f'-backend-config={directory / "backend.tfbackend.json"}')
    else:
        init.append(f'-backend-config=path={directory / "foundation.tfstate"}')
    run(init, env)
    args = [module, 'plan', '-input=false', '-lock-timeout=60s', f'-var-file={directory / "inputs.tfvars.json"}',
            f'-out={directory / "saved.tfplan"}']
    run(args, env)
    if previous_manifest is not None:
        import workshop_batch
        exported = json.loads(run([module, 'show', '-json', str(directory / 'saved.tfplan')], env, capture=True))
        receipt = workshop_batch.review_roster(previous_manifest, allocations, exported)
        wc.write_manifest(directory / 'roster-review.json', receipt)
        wc.write_manifest(directory / 'resume.json', previous_record)
    require(shared_path is None or file_hash(shared_path) == shared_hash,
            'shared network: access output changed while planning')
    wc.write_manifest(directory / 'binding.json', {'stage': stage, 'config': config, 'pilot': pilot,
                       'plan_sha256': file_hash(directory / 'saved.tfplan'),
                       'vars_sha256': file_hash(directory / 'inputs.tfvars.json'),
                       'backend_sha256': file_hash(directory / 'backend.tfbackend.json') if stage == 'pilot' else None,
                       'runtime_sha256': file_hash(Path(pilot['runtime_bundle_dir']) / 'runtime.json'),
                       'tool_sha256': tool_hash(),
                       'module_sha256': module_hash(stage),
                       **({'allocations_sha256': file_hash(directory / 'allocations.json')} if stage == 'pilot' else {}),
                       **({'resume_sha256': file_hash(directory / 'resume.json'),
                           'roster_review_sha256': file_hash(directory / 'roster-review.json')} if previous_manifest is not None else {}),
                       **({'image_release': wc.load_document(pilot['image_release_file']),
                           'image_release_sha256': file_hash(pilot['image_release_file']),
                           'image_initializer_sha256': file_hash(SPEC/'runtime/start_image.py')} if config['image']['mode'] == 'prebuilt' else {}),
                       **({'browser_routes': wc.load_document(pilot['browser_routes_file']),
                           'browser_routes_sha256': file_hash(pilot['browser_routes_file'])} if pilot.get('browser_routes_file') else {}),
                       **({'shared_access': shared_access, 'shared_access_sha256': shared_hash} if shared_path else {})})
    return directory


def module_hash(stage):
    return wc.canonical_hash({p.name: file_hash(p) for p in (MODULES / stage).iterdir()
                              if p.suffix in {'.tf', '.tftpl'} or p.name == '.terraform.lock.hcl'})


def tool_hash():
    paths = [Path(__file__), Path(wc.__file__), SPEC / 'config.schema.json',
             SPEC / 'pilot.schema.json', SPEC / 'profiles.json', SPEC / 'image-release.schema.json',
             Path(__file__).with_name('workshop_release.py'), SPEC / 'runtime/start_image.py']
    paths.extend(Path(__file__).with_name(name) for name in ('workshop_access.py', 'workshop_routing.py', 'workshop_batch.py', 'workshop_cleanup.py', 'workshop_scan.py', 'workshop_ttl.py', 'workshop_shared_preflight.py', 'workshop_shared_cleanup.py', 'workshop_gateway_deploy.py', 'workshop_bindings_create.py', 'workshop_bindings_review.py', 'workshop_bindings_update.py', 'workshop_portal_install.py', 'workshop_portal_update.py', 'workshop.py'))
    paths.extend(SPEC / 'access' / name for name in ('gateway.py', 'portal.py', 'jupyter_gateway.py', 'requirements.txt',
                 'doris-workshop-gateway.service', 'install_gateway.sh', 'doris-workshop-portal.service', 'install_portal.sh'))
    paths.append(SPEC / 'operations/node.py')
    paths.append(wc.REPO / 'ui/workshop/service_guidance.py')
    return wc.canonical_hash({str(p.relative_to(wc.REPO)): file_hash(p) for p in paths})


def apply_saved(directory, aws_factory=Aws, *, terraform_runner=None):
    env = explicit_environment()
    directory = directory.resolve()
    binding = wc.load_document(directory / 'binding.json')
    config, pilot = validate_inputs(binding['config'], binding['pilot'])
    previous = wc.load_document(directory / 'preflight.json')
    require(previous.get('status') == 'passed' and
            previous['config_sha256'] == wc.canonical_hash(config) and
            previous['pilot_sha256'] == wc.canonical_hash(pilot) and
            wc.timestamp(previous['valid_until'], 'preflight.valid_until') > datetime.now(timezone.utc),
            'plan: preflight expired or inputs changed; create and review a new plan')
    require(binding['stage'] in {'bootstrap', 'pilot'}, 'plan: invalid stage')
    for filename, key in (('allocations.json', 'allocations_sha256'), ('resume.json', 'resume_sha256'),
                          ('roster-review.json', 'roster_review_sha256')):
        if key in binding:
            require(file_hash(directory / filename) == binding[key], 'plan: bound allocation or roster artifacts changed')
    require(file_hash(directory / 'saved.tfplan') == binding['plan_sha256'] and
            file_hash(directory / 'inputs.tfvars.json') == binding['vars_sha256'] and
            file_hash(Path(pilot['runtime_bundle_dir']) / 'runtime.json') == binding['runtime_sha256'] and
            tool_hash() == binding['tool_sha256'] and
            module_hash(binding['stage']) == binding['module_sha256'], 'plan: saved plan, variables or module changed')
    if binding['stage'] == 'pilot':
        require(file_hash(directory / 'backend.tfbackend.json') == binding['backend_sha256'], 'plan: backend changed')
    if config['image']['mode'] == 'prebuilt':
        require(file_hash(pilot['image_release_file']) == binding['image_release_sha256'], 'plan: release metadata changed')
    if pilot.get('browser_routes_file'):
        require(file_hash(pilot['browser_routes_file']) == binding['browser_routes_sha256'], 'plan: browser route metadata changed')
    if pilot.get('shared_access_file'):
        require(file_hash(pilot['shared_access_file']) == binding['shared_access_sha256'], 'plan: shared access metadata changed')
    report = live_check(config, pilot, aws_factory(config), binding['stage'])
    if pilot.get('shared_access_file'):
        require(file_hash(pilot['shared_access_file']) == binding['shared_access_sha256'],
                'plan: shared access metadata changed during apply preflight')
    if binding['stage'] == 'pilot':
        variables = wc.load_document(directory / 'inputs.tfvars.json')
        require(variables['availability_zone'] in report['availability_zones'],
                'instance: saved plan availability zone no longer offers this instance type')
    wc.write_manifest(directory / f'apply-preflight-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")}.json', report)
    env['TF_DATA_DIR'] = str(directory / 'terraform-data')
    (terraform_runner or terraform)([f'-chdir={MODULES / binding["stage"]}', 'apply', '-input=false', '-lock-timeout=60s',
               str(directory / 'saved.tfplan')], env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('check', 'plan'):
        cmd = commands.add_parser(command)
        cmd.add_argument('--config', required=True, type=Path)
        cmd.add_argument('--pilot', required=True, type=Path)
        cmd.add_argument('--stage', choices=['bootstrap', 'pilot'], default='pilot')
        cmd.add_argument('--output', required=True, type=Path)
    cmd = commands.add_parser('apply')
    cmd.add_argument('--run-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    # Plans, local bootstrap state and SDK diagnostics must stay private.
    os.umask(0o077)
    try:
        if args.command == 'apply':
            apply_saved(args.run_dir)
            return 0
        config, pilot = load_inputs(args.config, args.pilot)
        aws = Aws(config)
        if args.command == 'check':
            report = live_check(config, pilot, aws, args.stage)
            wc.write_manifest(args.output, report)
            print('Live preflight passed; no resources created. Report valid for 15 minutes at most.')
        else:
            directory = plan(config, pilot, aws, args.stage, args.output)
            print('Plan saved. Review it before using the apply command; apply reruns live preflight.')
        return 0
    except (wc.ConfigError, OSError, KeyError, TypeError, ValueError, StopIteration, InvalidOperation) as exc:
        message = str(exc) if isinstance(exc, wc.ConfigError) else 'input/query: malformed or unavailable data; apply blocked'
        print(message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
