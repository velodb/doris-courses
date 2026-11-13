#!/usr/bin/env python3
"""Distribute encrypted AMI copies without changing published source images."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import sys

import workshop_config as wc
import workshop_image as wi
import workshop_preflight as pf
import workshop_release as releases
import workshop as ws

MODULE = wc.SPEC / 'terraform/image-distribution'
FILES = {'release.json', 'image-manifest.json', 'builder.json'}
RUNTIME_FILES = {'install_host.sh', 'source.tar', 'host.py', 'check.py', 'kernel.py', 'serve.py', 'portal.py',
                 'packages.json', 'doris-workshop-jupyter.service', 'operations.py', 'ui.tar',
                 'multilingual.py', 'teaching.tar.gz', 'profiles.v2.json'}
RESOURCE_TYPES = {'aws_kms_key', 'aws_ami_copy', 'aws_ami_launch_permission',
                  'aws_snapshot_create_volume_permission'}
require = pf.require


def immutable(path, value):
    if path.exists():
        require(wc.load_document(path) == value, 'transfer: preserve conflicting operational records')
    else:
        wi.write(path, value)


def package(directory, trusted=None):
    """The digest must arrive through a trusted channel, independently of files."""
    directory = Path(directory)
    index = wc.load_document(directory / 'package.json')
    digest = wi.checksum(directory / 'package.json')
    require(trusted is None or trusted == digest, 'transfer: trusted package digest mismatch')
    require(index.get('kind') == 'doris-workshop-image-transfer' and type(index.get('schema_version')) is int and index.get('schema_version') == 1 and
            set(index) == {'kind', 'schema_version', 'transfer_id', 'recipient_account_id',
                           'recipient_principal_arn', 'sha256'}, 'transfer: invalid package index')
    names = set(index['sha256'])
    require(FILES | {'runtime/runtime.json'} <= names and
            all(name in FILES | {'distribution.json','runtime/runtime.json'} or
                name.startswith('runtime/') and (name[8:] in RUNTIME_FILES or
                re.fullmatch(r'[a-zA-Z0-9_.-]+\.requirements\.lock', name[8:])) for name in names),
            'transfer: unexpected package files')
    require(all(not (directory / name).is_symlink() and (directory/name).resolve().is_relative_to(directory.resolve()) and
                wi.checksum(directory / name) == digest
                for name, digest in index['sha256'].items()), 'transfer: package file checksum mismatch')
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,31}', index['transfer_id']) and
            re.fullmatch(r'[0-9]{12}', index['recipient_account_id']), 'transfer: invalid transfer or recipient ID')
    require(re.fullmatch(r'arn:aws:iam::' + index['recipient_account_id'] + r':(?:role|user)/[A-Za-z0-9+=,.@_/-]+',
                         index['recipient_principal_arn']), 'transfer: select an exact recipient user or role')
    release = releases.load_release(directory / 'release.json')
    runtime = wc.load_document(directory / 'runtime/runtime.json')
    require(index['sha256']['runtime/runtime.json'] == release['runtime_sha256'] and
            runtime['kind'] == 'doris-workshop-runtime' and runtime['schema_version'] == release['schema_version'] and
            runtime['course_ref'] == release['course_ref'] and runtime.get('content') == release.get('content') and
            sorted(p for course in runtime['courses'] for p in course['profiles']) == sorted(release['course_profiles']) and
            {name[8:] for name in names if name.startswith('runtime/') and name != 'runtime/runtime.json'} == set(runtime['sha256']) and
            all(index['sha256']['runtime/'+name] == digest for name,digest in runtime['sha256'].items()),
            'transfer: matching runtime bundle is incomplete or changed')
    manifest = wc.load_document(directory / 'image-manifest.json')
    require(re.fullmatch(r'(us|eu|ap|ca|sa|me|af|il|mx)-[a-z]+-[0-9]+', release['region']) and
            release['aws_account_id'] != index['recipient_account_id'], 'transfer: use two commercial accounts in one Region')
    require(index['sha256']['image-manifest.json'] == release['image_manifest_sha256'] and
            all(manifest[k] == release[k] for k in ('course_ref', 'deployment_ref', 'runtime_sha256')) and
            manifest['profiles'] == release['course_profiles'] and
            manifest.get('schema_version', 1) == release['schema_version'] and
            manifest.get('content') == release.get('content') and
            manifest['root_volume']['size_gib'] == release['root_gib'] == 200,
            'transfer: source release and sealed manifest differ')
    builder = wc.load_document(directory / 'builder.json')
    require(set(builder) == {'instance_id'} and re.fullmatch(r'i-[0-9a-f]{17}', builder['instance_id']),
            'transfer: retain the original builder identity for fresh-instance audits')
    if 'distribution.json' in index['sha256']:
        distribution = wc.load_document(directory / 'distribution.json')
        require(set(distribution) == {'ami_id', 'snapshot_ids', 'kms_key_arn', 'account_id', 'region',
                                      'transfer_id', 'shared', 'source_ami_id'} and
                distribution['account_id'] == release['aws_account_id'] and distribution['region'] == release['region'] and
                distribution['transfer_id'] == index['transfer_id'] and distribution['shared'] is True and
                distribution['source_ami_id'] == release['ami_id'] and distribution['ami_id'] != release['ami_id'] and
                re.fullmatch(r'ami-[0-9a-f]{17}', distribution['ami_id']) and
                len(distribution['snapshot_ids']) == 1 and
                re.fullmatch(r'snap-[0-9a-f]{17}', distribution['snapshot_ids'][0]) and
                re.fullmatch(r'arn:aws:kms:' + release['region'] + ':' + release['aws_account_id'] +
                             r':key/[0-9a-f-]{36}', distribution['kms_key_arn']), 'transfer: invalid distribution identity')
    return index, release, manifest


def prepare(release_path, manifest_path, builder_path, transfer_id, recipient, principal, output, runtime_bundle):
    require(not output.exists(), 'transfer: choose a new package directory')
    output.mkdir(parents=True, mode=0o700)
    # Only explicitly selected provenance and checksummed runtime payload leave
    # the source operator's run. Never copy bindings, plans, state or credentials.
    shutil.copyfile(release_path, output / 'release.json')
    shutil.copyfile(manifest_path, output / 'image-manifest.json')
    wi.write(output / 'builder.json', {'instance_id': wc.load_document(builder_path)['instance_id']})
    runtime = wc.load_document(runtime_bundle / 'runtime.json')
    require(all(name in RUNTIME_FILES or re.fullmatch(r'[a-zA-Z0-9_.-]+\.requirements\.lock', name)
                for name in runtime['sha256']), 'transfer: unsafe runtime file name')
    (output / 'runtime').mkdir(mode=0o700)
    runtime_names = {'runtime.json', *runtime['sha256']}
    for name in runtime_names:
        require(not (runtime_bundle / name).is_symlink(), 'transfer: runtime payload must be a regular file')
        shutil.copyfile(runtime_bundle / name, output / 'runtime' / name)
    wi.write(output / 'package.json', dict(schema_version=1, kind='doris-workshop-image-transfer',
        transfer_id=transfer_id, recipient_account_id=recipient, recipient_principal_arn=principal,
        sha256={name: wi.checksum(output / name) for name in sorted(FILES | {'runtime/'+name for name in runtime_names})}))
    package(output)
    return wi.checksum(output / 'package.json')


def account(aws, expected):
    identity = aws.call('sts', 'get_caller_identity')
    require(identity['Account'] == expected, 'transfer: wrong AWS account')
    return identity


def source_live(aws, release):
    releases.verify_live(aws, {'aws_account_id': release['aws_account_id']}, release)
    snapshots = aws.call('ec2', 'describe_snapshots', SnapshotIds=release['snapshot_ids'])['Snapshots']
    require(len(snapshots) == 1 and snapshots[0]['SnapshotId'] == release['snapshot_ids'][0] and
            snapshots[0]['OwnerId'] == release['aws_account_id'] and snapshots[0]['Encrypted'] is True and
            snapshots[0]['State'] == 'completed' and snapshots[0]['VolumeSize'] == release['root_gib'],
            'transfer: source snapshot is not owned, encrypted and complete')
    attrs = aws.call('ec2', 'describe_snapshot_attribute', SnapshotId=release['snapshot_ids'][0], Attribute='createVolumePermission')
    require(not attrs.get('CreateVolumePermissions'), 'transfer: the published source snapshot must remain unshared')


def key_policy_live(aws, artifact, recipient_principal=None):
    policy = json.loads(aws.call('kms', 'get_key_policy', KeyId=artifact['kms_key_arn'], PolicyName='default')['Policy'])
    statements = {s['Sid']: s for s in policy['Statement']}
    expected = {'OwnerAdministration': ({'AWS':'arn:aws:iam::'+artifact['account_id']+':root'}, {'kms:*'}, None)}
    if recipient_principal:
        principal = {'AWS': recipient_principal}
        via = {'StringEquals': {'kms:ViaService':'ec2.'+artifact['region']+'.amazonaws.com'}}
        expected.update({
            'RecipientDescribe': (principal, {'kms:DescribeKey'}, None),
            'RecipientCopy': (principal, {'kms:Decrypt','kms:ReEncryptFrom','kms:ReEncryptTo',
                                         'kms:GenerateDataKey','kms:GenerateDataKeyWithoutPlaintext'}, via),
            'RecipientAWSGrant': (principal, {'kms:CreateGrant'}, {**via, 'Bool':{'kms:GrantIsForAWSResource':'true'}})})
    require(len(policy['Statement']) == len(expected) and set(statements) == set(expected),
            'transfer: unexpected key policy statements')
    for sid, (principal, actions, condition) in expected.items():
        statement = statements[sid]
        actual = statement['Action']
        require(set(statement) == ({'Sid','Effect','Principal','Action','Resource'} | ({'Condition'} if condition else set())) and
                statement['Effect'] == 'Allow' and statement['Principal'] == principal and statement['Resource'] == '*' and
                set(actual if isinstance(actual, list) else [actual]) == actions and statement.get('Condition') == condition,
                'transfer: key policy permissions differ from the reviewed transfer')


def artifact_live(aws, artifact, release, *, owned, sharing=None, key_enabled=True):
    rows = aws.call('ec2', 'describe_images', ImageIds=[artifact['ami_id']], Owners=[artifact['account_id']])['Images']
    require(len(rows) == 1, 'transfer: copy unavailable')
    image = rows[0]
    mappings = [m for m in image['BlockDeviceMappings'] if 'Ebs' in m]
    require(image['ImageId'] == artifact['ami_id'] and image['OwnerId'] == artifact['account_id'] and
            image['State'] == 'available' and image['Public'] is False and image['Architecture'] == 'x86_64' and
            image['RootDeviceType'] == 'ebs' and image['VirtualizationType'] == 'hvm' and
            not image.get('ProductCodes') and len(mappings) == 1 and mappings[0]['DeviceName'] == '/dev/sda1' and
            mappings[0]['Ebs']['SnapshotId'] == artifact['snapshot_ids'][0] and
            mappings[0]['Ebs']['VolumeSize'] == release['root_gib'] and
            mappings[0]['Ebs']['Encrypted'] is True and mappings[0]['Ebs']['DeleteOnTermination'] is True and
            image.get('SourceImageId') == artifact['source_ami_id'] and
            image.get('SourceImageRegion') == artifact['region'], 'transfer: copy identity or root mapping mismatch')
    rows = aws.call('ec2', 'describe_snapshots', SnapshotIds=artifact['snapshot_ids'])['Snapshots']
    require(len(rows) == 1 and rows[0]['SnapshotId'] == artifact['snapshot_ids'][0] and
            rows[0]['OwnerId'] == artifact['account_id'] and rows[0]['State'] == 'completed' and
            rows[0]['Encrypted'] is True and rows[0]['KmsKeyId'] == artifact['kms_key_arn'] and
            rows[0]['VolumeSize'] == release['root_gib'], 'transfer: snapshot owner, encryption or key mismatch')
    key = aws.call('kms', 'describe_key', KeyId=artifact['kms_key_arn'])['KeyMetadata']
    require(key['Arn'] == artifact['kms_key_arn'] and key['AWSAccountId'] == artifact['account_id'] and
            key['KeyManager'] == 'CUSTOMER' and key['KeyState'] == ('Enabled' if key_enabled else 'Disabled') and
            key['KeyUsage'] == 'ENCRYPT_DECRYPT' and key['KeySpec'] == 'SYMMETRIC_DEFAULT',
            'transfer: use an enabled customer-managed symmetric key owned by this side')
    if owned:
        grants = aws.call('ec2', 'describe_image_attribute', ImageId=artifact['ami_id'], Attribute='launchPermission')
        snapshot_grants = aws.call('ec2', 'describe_snapshot_attribute', SnapshotId=artifact['snapshot_ids'][0],
                                  Attribute='createVolumePermission')
        expected = [{'UserId': sharing}] if sharing else []
        require(grants.get('LaunchPermissions', []) == expected and
                snapshot_grants.get('CreateVolumePermissions', []) == expected,
                'transfer: unexpected AMI or snapshot sharing permission')
        tags = {t['Key']: t['Value'] for t in image.get('Tags', [])}
        require(tags.get('Transfer') == artifact['transfer_id'] and tags.get('ImageBuild') == artifact['transfer_id'] and
                tags.get('ManifestSHA256') == release['image_manifest_sha256'] and
                tags.get('CourseRef') == release['course_ref'], 'transfer: copy provenance tags differ')
    return image


def environment(root):
    env = pf.explicit_environment()
    env['TF_DATA_DIR'] = str(root / 'terraform-data')
    return env


def tf(root, *args, capture=False):
    return pf.terraform(['-chdir=' + str(MODULE), *map(str, args)], environment(root), capture=capture)


def state_hash(root):
    path = root / 'terraform.tfstate'
    return wi.checksum(path) if path.exists() else None


def lock_directory(root):
    lock = root.resolve().parent / (root.name + '-lock')
    lock.mkdir(parents=True, mode=0o700, exist_ok=True)
    return lock


def cloud_check(aws, index, release, directory, mode, action):
    expected = release['aws_account_id'] if mode == 'export' else index['recipient_account_id']
    identity = account(aws, expected)
    # Cleanup must still work after the original release was retired or its
    # sharing window ended. It operates only on this run's recorded state.
    if action == 'create':
        if mode == 'export':
            source_live(aws, release)
        else:
            require(pf.principal_arn(aws, identity) == index['recipient_principal_arn'],
                    'transfer: import requires the exact named recipient principal')
            artifact_live(aws, wc.load_document(directory / 'distribution.json'), release, owned=False)


def check_plan(value, action):
    require(value.get('errored') is not True and value.get('complete', True) is True and
            not value.get('deferred_changes'), 'transfer: incomplete or errored Terraform plan')
    addresses = set()
    for row in value.get('resource_changes', []):
        if row['mode'] != 'managed':
            continue
        expected_addresses = {'aws_kms_key.copy', 'aws_ami_copy.copy',
                              'aws_ami_launch_permission.recipient[0]', 'aws_snapshot_create_volume_permission.recipient[0]'}
        require(row['address'] not in addresses and row['address'] in expected_addresses and row['type'] in RESOURCE_TYPES and
                not row.get('module_address'), 'transfer: unexpected or duplicate managed resource')
        addresses.add(row['address'])
        actions = row['change']['actions']
        allowed = {'create', 'no-op'} if action == 'create' else {'update', 'delete', 'no-op'} if action == 'revoke' else {'delete', 'no-op'}
        require(set(actions) <= allowed, 'transfer: replacement or unexpected plan action rejected')
        if action == 'revoke':
            require((row['type'] == 'aws_kms_key' and actions in (['update'], ['no-op'])) or
                    (row['type'] == 'aws_ami_copy' and actions == ['no-op']) or
                    row['type'] in {'aws_ami_launch_permission', 'aws_snapshot_create_volume_permission'},
                    'transfer: revocation must preserve the CMK and copy')


def plan(directory, trusted, mode, profile, root, action, budget=None, retain=None):
    directory, root = directory.resolve(), root.resolve()
    index, release, _ = package(directory, trusted)
    require(mode != 'import' or 'distribution.json' in index['sha256'], 'transfer: import needs an exported package')
    expected = release['aws_account_id'] if mode == 'export' else index['recipient_account_id']
    aws = pf.Aws({'aws_profile': profile, 'region': release['region']})
    cloud_check(aws, index, release, directory, mode, action)
    config = dict(aws_profile=profile, aws_account_id=expected, region=release['region'])
    if (root / 'run.json').exists():
        run = wc.load_document(root / 'run.json')
        require(run['config'] == config and run['mode'] == mode and run['package_sha256'] == trusted,
                'transfer: this run belongs to different inputs; do not reuse its state')
    else:
        require(action == 'create' and not root.exists(), 'transfer: use a new run directory for a new copy')
        require(budget is not None and Decimal(budget).is_finite() and Decimal(budget) > 0 and retain and
                wc.timestamp(retain, 'retain-until') > datetime.now(timezone.utc),
                'transfer: provide a positive budget and future retention review time')
        root.mkdir(parents=True, mode=0o700)
        run = dict(config=config, mode=mode, package_sha256=trusted, package_dir=str(directory),
                   budget_usd=str(budget), retain_until=retain)
        wi.write(root / 'run.json', run)
    require(action != 'create' or wc.timestamp(run['retain_until'], 'retain-until') > datetime.now(timezone.utc),
            'transfer: retention window expired; only revocation and cleanup remain available')
    require(action != 'revoke' or mode == 'export', 'transfer: only the exporter owns sharing grants')
    # Locking is handled by main; attempts are immutable and share one stable state.
    attempt = root / 'plans' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    attempt.mkdir(parents=True, mode=0o700)
    source = release if mode == 'export' else wc.load_document(directory / 'distribution.json')
    variables = dict(aws_profile=profile, expected_account_id=expected, region=release['region'], mode=mode,
        source_account_id=release['aws_account_id'], source_ami_id=source['ami_id'],
        recipient_account_id=index['recipient_account_id'], recipient_principal_arn=index['recipient_principal_arn'],
        transfer_id=index['transfer_id'], course_ref=release['course_ref'],
        manifest_sha256=release['image_manifest_sha256'], source_release_sha256=wc.canonical_hash(release),
        retain_until=run['retain_until'], share_enabled=(mode == 'export' and action == 'create'))
    if action == 'destroy' and (root / 'applied-inputs.json').exists():
        variables = wc.load_document(root / 'applied-inputs.json')
    wi.write(attempt / 'inputs.json', variables)
    tf(root, 'init', '-input=false', '-lockfile=readonly', '-backend-config=path=' + str(root / 'terraform.tfstate'))
    args = ['plan', '-input=false', '-lock-timeout=60s', '-var-file=' + str(attempt / 'inputs.json'),
            '-out=' + str(attempt / 'saved.tfplan')]
    if action == 'destroy': args.append('-destroy')
    tf(root, *args)
    rendered = json.loads(tf(root, 'show', '-json', attempt / 'saved.tfplan', capture=True))
    wi.write(attempt / 'review.json', rendered)
    check_plan(rendered, action)
    wi.write(attempt / 'binding.json', dict(action=action, plan_sha256=wi.checksum(attempt / 'saved.tfplan'),
        vars_sha256=wi.checksum(attempt / 'inputs.json'), run_sha256=wi.checksum(root / 'run.json'),
        module_sha256=pf.module_hash('image-distribution'), tool_sha256=tool_hash(), state_sha256=state_hash(root),
        valid_until=wc.utc_text(datetime.now(timezone.utc) + timedelta(minutes=15))))
    return str(attempt)


def tool_hash():
    return wc.canonical_hash({p.name: wi.checksum(p) for p in
        (Path(__file__), Path(wi.__file__), Path(pf.__file__), Path(wc.__file__), Path(releases.__file__))})


def apply(root, attempt, approved):
    require(attempt.resolve().parent == (root / 'plans').resolve(), 'transfer: choose this run\'s plan attempt')
    b = wc.load_document(attempt / 'binding.json')
    require(approved == b['plan_sha256'] == wi.checksum(attempt / 'saved.tfplan') and
            b['vars_sha256'] == wi.checksum(attempt / 'inputs.json') and
            b['run_sha256'] == wi.checksum(root / 'run.json') and
            b['module_sha256'] == pf.module_hash('image-distribution') and b['tool_sha256'] == tool_hash() and
            b['state_sha256'] == state_hash(root) and
            wc.timestamp(b['valid_until'], 'plan.valid_until') > datetime.now(timezone.utc),
            'transfer: stale or changed plan, state or tool; create and review a new plan')
    require(not (attempt / 'apply-started.json').exists(),
            'transfer: previous apply was dispatched; inspect state and make a fresh recovery plan')
    run = wc.load_document(root / 'run.json')
    require(b['action'] != 'create' or wc.timestamp(run['retain_until'], 'retain-until') > datetime.now(timezone.utc),
            'transfer: retention window expired before apply')
    directory = Path(run['package_dir'])
    index, release, _ = package(directory, run['package_sha256'])
    aws = pf.Aws(run['config'])
    cloud_check(aws, index, release, directory, run['mode'], b['action'])
    check_plan(json.loads(tf(root, 'show', '-json', attempt / 'saved.tfplan', capture=True)), b['action'])
    if b['action'] == 'destroy':
        state = json.loads(tf(root, 'show', '-json', capture=True))
        targets = cleanup_targets(state, run, index)
        wi.write(attempt / 'cleanup-targets.json', targets)
    wi.write(attempt / 'apply-started.json', {'checked_at': wc.utc_text(datetime.now(timezone.utc))})
    tf(root, 'apply', '-input=false', '-lock-timeout=60s', attempt / 'saved.tfplan')
    ws.atomic_json(root / 'applied-inputs.json', wc.load_document(attempt / 'inputs.json'))
    if b['action'] == 'destroy':
        state = json.loads(tf(root, 'show', '-json', capture=True))
        require(not state.get('values', {}).get('root_module', {}).get('resources'), 'transfer: state is not empty')
        wi.write(attempt / 'result.json', {'state_empty': True, 'kms_deletion': 'Pending deletion; reconcile API and grants separately.'})
    else:
        result = status(root, aws=aws)
        wi.write(attempt / 'result.json', result)
    return str(attempt / 'result.json')


def cleanup_targets(state, run, index):
    targets = dict(account_id=run['config']['aws_account_id'], region=run['config']['region'],
                   transfer_id=index['transfer_id'], ami_ids=[], snapshot_ids=[], kms_key_arns=[])
    for row in state.get('values', {}).get('root_module', {}).get('resources', []):
        if row['mode'] != 'managed': continue
        require(row['type'] in RESOURCE_TYPES, 'transfer: unexpected resource in cleanup state')
        values = row['values']
        if row['type'] in {'aws_ami_copy', 'aws_kms_key'}:
            tags = values.get('tags_all') or values.get('tags', {})
            require(tags.get('Transfer') == index['transfer_id'] and tags.get('Scope') == 'image-distribution',
                    'transfer: cleanup state contains an unowned resource')
        if row['type'] == 'aws_ami_copy':
            targets['ami_ids'].append(values['id'])
            if values.get('root_snapshot_id'): targets['snapshot_ids'].append(values['root_snapshot_id'])
        elif row['type'] == 'aws_kms_key': targets['kms_key_arns'].append(values['arn'])
    return targets


def reconcile(root, attempt, aws=None):
    require(attempt.resolve().parent == (root / 'plans').resolve(), 'transfer: choose this run\'s cleanup attempt')
    run = wc.load_document(root / 'run.json')
    targets = wc.load_document(attempt / 'cleanup-targets.json')
    require(wc.load_document(attempt / 'binding.json')['action'] == 'destroy' and
            targets['account_id'] == run['config']['aws_account_id'] and targets['region'] == run['config']['region'],
            'transfer: wrong cleanup account or attempt')
    aws = aws or pf.Aws(run['config'])
    account(aws, targets['account_id'])
    state = json.loads(tf(root, 'show', '-json', capture=True))
    images = aws.call('ec2', 'describe_images', Owners=[targets['account_id']],
                      Filters=[{'Name':'image-id', 'Values':targets['ami_ids']}])['Images'] if targets['ami_ids'] else []
    snapshots = aws.call('ec2', 'describe_snapshots', OwnerIds=[targets['account_id']],
                         Filters=[{'Name':'snapshot-id', 'Values':targets['snapshot_ids']}])['Snapshots'] if targets['snapshot_ids'] else []
    keys = []
    for arn in targets['kms_key_arns']:
        client = aws.client('kms')
        try:
            metadata = client.describe_key(KeyId=arn)['KeyMetadata']
        except client.exceptions.NotFoundException:
            keys.append(dict(arn=arn, state='NotFound', grants=[]))
            continue
        require(metadata['AWSAccountId'] == targets['account_id'], 'transfer: wrong cleanup key owner')
        grants = [g['GrantId'] for page in client.get_paginator('list_grants').paginate(KeyId=arn)
                  for g in page['Grants']]
        keys.append(dict(arn=arn, state=metadata['KeyState'], grants=grants))
    # Query ownership tags too: copies interrupted before their snapshot ID was
    # written to state must not disappear from the reconciliation boundary.
    filters = [{'Name':'tag:Transfer', 'Values':[targets['transfer_id']]},
               {'Name':'tag:Scope', 'Values':['image-distribution']}]
    tagged_images = aws.call('ec2', 'describe_images', Owners=[targets['account_id']], Filters=filters)['Images']
    tagged_snapshots = [s for page in aws.client('ec2').get_paginator('describe_snapshots').paginate(
        OwnerIds=[targets['account_id']], Filters=filters) for s in page['Snapshots']]
    # The pinned aws_ami_copy provider tags the AMI, not necessarily its copied
    # snapshots. Scan by this transfer's dedicated key as well as known IDs.
    key_snapshots = [s for page in aws.client('ec2').get_paginator('describe_snapshots').paginate(
        OwnerIds=[targets['account_id']]) for s in page['Snapshots'] if s.get('KmsKeyId') in targets['kms_key_arns']]
    passed = not state.get('values', {}).get('root_module', {}).get('resources') and not any(
        (images, snapshots, tagged_images, tagged_snapshots, key_snapshots)) and all(k['state'] in {'PendingDeletion', 'NotFound'} for k in keys)
    report = dict(checked_at=wc.utc_text(datetime.now(timezone.utc)), passed=passed, targets=targets,
        remaining_images=[i['ImageId'] for i in images], remaining_snapshots=[s['SnapshotId'] for s in snapshots],
        tagged_images=[i['ImageId'] for i in tagged_images], tagged_snapshots=[s['SnapshotId'] for s in tagged_snapshots],
        key_snapshots=[s['SnapshotId'] for s in key_snapshots],
        keys=keys, key_boundary='PendingDeletion keys are retained disabled resources, not deleted keys. Grants are reported, not assumed absent.')
    wi.write(attempt / ('reconciliation-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json'), report)
    require(passed, 'transfer: cloud cleanup is incomplete; preserve state and reconcile again')
    return report


def status(root, *, aws=None):
    run = wc.load_document(root / 'run.json')
    index, release, _ = package(Path(run['package_dir']), run['package_sha256'])
    aws = aws or pf.Aws(run['config'])
    account(aws, run['config']['aws_account_id'])
    artifact = json.loads(tf(root, 'output', '-json', capture=True))['artifact']['value']
    expected_source = release['ami_id'] if run['mode'] == 'export' else wc.load_document(
        Path(run['package_dir']) / 'distribution.json')['ami_id']
    require(artifact['account_id'] == run['config']['aws_account_id'] and artifact['region'] == release['region'] and
            artifact['transfer_id'] == index['transfer_id'] and artifact['source_ami_id'] == expected_source and
            artifact['ami_id'] != expected_source and (run['mode'] == 'export' or artifact['shared'] is False),
            'transfer: unexpected state output identity')
    artifact_live(aws, artifact, release, owned=True,
                  sharing=index['recipient_account_id'] if artifact['shared'] else None,
                  key_enabled=run['mode'] == 'import' or artifact['shared'])
    key_policy_live(aws, artifact, index['recipient_principal_arn'] if artifact['shared'] else None)
    if run['mode'] == 'export' and artifact['shared']:
        source_live(aws, release)
    return artifact


def export(root, output):
    artifact = status(root)
    run = wc.load_document(root / 'run.json')
    require(run['mode'] == 'export' and artifact['shared'] is True and not output.exists(),
            'transfer: export a live shared copy to a new package directory')
    source = Path(run['package_dir'])
    index, _, _ = package(source, run['package_sha256'])
    output.mkdir(parents=True, mode=0o700)
    for name in index['sha256']:
        (output / name).parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copyfile(source / name, output / name)
    wi.write(output / 'distribution.json', artifact)
    wi.write(output / 'package.json', {**index, 'sha256': {name: wi.checksum(output / name)
                                                       for name in sorted(set(index['sha256']) | {'distribution.json'})}})
    package(output)
    return wi.checksum(output / 'package.json')


def candidate(root, output):
    artifact = status(root)
    run = wc.load_document(root / 'run.json')
    require(run['mode'] == 'import' and artifact['shared'] is False, 'transfer: only import produces a local candidate')
    source = Path(run['package_dir'])
    index, release, manifest = package(source, run['package_sha256'])
    require(not output.exists(), 'transfer: preserve existing candidate records')
    output.mkdir(parents=True, mode=0o700)
    shutil.copyfile(source / 'image-manifest.json', output / 'image-manifest.json')
    shutil.copyfile(source / 'builder.json', output / 'build-instance.json')
    wi.write(output / 'binding.json', {'config': {**run['config'], 'course_ref': release['course_ref'],
             'course_profiles': release['course_profiles']}, 'build_id': index['transfer_id']})
    wi.write(output / 'imported-artifact.json', dict(kind='doris-workshop-imported-image', schema_version=1,
        artifact=artifact, package_sha256=run['package_sha256'], source_release=release,
        binding_sha256=wi.checksum(output / 'binding.json'), image_manifest_sha256=wi.checksum(output / 'image-manifest.json')))
    wi.record_candidate(output, pf.Aws(run['config']))
    return str(output)


def imported_candidate(directory, aws):
    """Used by the existing fresh-image validation and publication gates."""
    receipt = wc.load_document(directory / 'imported-artifact.json')
    binding = wc.load_document(directory / 'binding.json')
    release = receipt['source_release']
    wc.schema_check(release, 'image-release.schema.json')
    artifact = receipt['artifact']
    require(receipt['kind'] == 'doris-workshop-imported-image' and receipt['schema_version'] == 1 and
            wi.checksum(directory / 'binding.json') == receipt['binding_sha256'] and
            wi.checksum(directory / 'image-manifest.json') == receipt['image_manifest_sha256'] == release['image_manifest_sha256'] and
            binding['config']['aws_account_id'] == artifact['account_id'] != release['aws_account_id'] and
            binding['config']['region'] == artifact['region'] == release['region'] and
            binding['build_id'] == artifact['transfer_id'] and artifact['shared'] is False,
            'transfer: imported artifact binding changed')
    manifest = wc.load_document(directory / 'image-manifest.json')
    require(manifest['course_ref'] == binding['config']['course_ref'] == release['course_ref'] and
            manifest['profiles'] == binding['config']['course_profiles'] == release['course_profiles'] and
            manifest['runtime_sha256'] == release['runtime_sha256'], 'transfer: imported provenance changed')
    account(aws, artifact['account_id'])
    image = artifact_live(aws, artifact, release, owned=True)
    key_policy_live(aws, artifact)
    tags = {t['Key']: t['Value'] for t in image.get('Tags', [])}
    require(tags.get('ImageStatus') in {'candidate', 'validated'}, 'transfer: imported AMI is retired')
    return dict(ami_id=artifact['ami_id'], region=artifact['region'], status='candidate', course_ref=release['course_ref'],
        snapshots=artifact['snapshot_ids'], manifest_sha256=release['image_manifest_sha256'],
        release_requirement='Independent new-instance verification remains required.',
        retention='Recipient-owned copy and CMK; explicit retention review and separate cleanup required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    for name in ('release', 'manifest', 'builder', 'output', 'runtime-bundle'): prep.add_argument('--' + name, type=Path, required=True)
    for name in ('transfer-id', 'recipient-account', 'recipient-principal'): prep.add_argument('--' + name, required=True)
    pp = sub.add_parser('plan')
    pp.add_argument('--package', type=Path, required=True)
    pp.add_argument('--trusted-package-sha256', required=True)
    pp.add_argument('--mode', choices=['export', 'import'], required=True)
    pp.add_argument('--action', choices=['create', 'revoke', 'destroy'], default='create')
    pp.add_argument('--profile', required=True)
    pp.add_argument('--budget-usd')
    pp.add_argument('--retain-until')
    ap = sub.add_parser('apply')
    ap.add_argument('--attempt', type=Path, required=True)
    ap.add_argument('--approved-plan-sha256', required=True)
    rc = sub.add_parser('reconcile')
    rc.add_argument('--attempt', type=Path, required=True)
    for name in ('status', 'export', 'candidate'):
        item = sub.add_parser(name)
        if name != 'status': item.add_argument('--output', type=Path, required=True)
    for name in ('plan', 'apply', 'status', 'export', 'candidate', 'reconcile'):
        sub.choices[name].add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == 'prepare':
            result = prepare(args.release, args.manifest, args.builder, args.transfer_id,
                             args.recipient_account, args.recipient_principal, args.output, args.runtime_bundle)
        elif args.command == 'plan':
            # Lock outside the state directory so a new run remains distinguishable.
            with ws.run_lock(lock_directory(args.run_dir)):
                result = plan(args.package, args.trusted_package_sha256, args.mode, args.profile,
                              args.run_dir, args.action, args.budget_usd, args.retain_until)
        else:
            root = args.run_dir.resolve()
            with ws.run_lock(lock_directory(root)):
                if args.command == 'apply': result = apply(root, args.attempt.resolve(), args.approved_plan_sha256)
                elif args.command == 'reconcile': result = reconcile(root, args.attempt.resolve())
                elif args.command == 'status': result = status(root)
                else: result = globals()[args.command](root, args.output)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(str(error) if isinstance(error, wc.ConfigError) else
              'transfer: operation failed; preserve the run and inspect state before retrying', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
