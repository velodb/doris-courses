#!/usr/bin/env python3
"""Prepare fixed private-image inputs and provision Packer hosts through SSM only."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import workshop_config as wc
import workshop_preflight as preflight
import workshop_runtime as runtime
import workshop_operations as operations

SPEC = wc.SPEC / 'packer'
DW_PROFILES = {'dw-main', 'dw-kafka', 'dw-cdc', 'dw-bi', 'dw-backup', 'dw-extensions'}
OBS_PROFILES = {'obs-main', 'obs-local-model'}


def image_selection(config):
    profiles = set(config['course_profiles'])
    if config.get('schema_version', 1) == 2 and profiles == {'rta-module-1'}:
        catalog = wc.load_document(SPEC / 'images.json')
        return 'doris-rta', {'schema_version': 2, 'profiles': ['rta-module-1'],
            'images': [i for i in catalog['images'] if i['tag'].startswith('apache/doris:')],
            'online_dependencies': [
                'Module 1 reads its public events Parquet dataset from S3.',
                'SSM and administration need AWS endpoints; a new image build needs APT and PyPI.',
                'Both admitted languages are installed before readiness; no online translation is used.']}
    if profiles == {'rta-main'}:
        return 'doris-rta', wc.load_document(SPEC / 'images.json')
    dw = 'dw-main' in profiles and profiles <= DW_PROFILES
    obs = 'obs-main' in profiles and profiles <= OBS_PROFILES
    preflight.require(dw or obs,
                      'image: select rta-main, dw-main with its options, or obs-main with optional obs-local-model')
    catalog = wc.load_document(SPEC / ('images-dw.json' if dw else 'images-obs.json'))
    selected = {'schema_version': 1, 'profiles': sorted(profiles),
                'online_dependencies': catalog['online_dependencies']}
    for key in ('images', 'builds'):
        selected[key] = [{k: v for k, v in row.items() if k != 'required_by'}
                         for row in catalog[key] if profiles.intersection(row['required_by'])]
    return ('doris-dw' if dw else 'doris-obs'), selected


def deployment_dirty(*, repo=None):
    # Include the independent UI and its packaging adapter in image provenance.
    # Git status also detects removed and untracked inputs; filtering by is_file
    # would silently omit deleted tracked files.
    paths = ['tools/workshop_image.py', 'tools/workshop_preflight.py',
             'tools/workshop_runtime.py', 'tools/workshop_config.py', 'tools/workshop_ui.py',
             'workshop/aws/runtime', 'workshop/aws/profiles.json', 'workshop/aws/operations/node.py',
             'workshop/aws/terraform/image-build', 'workshop/aws/packer',
             'ui/workshop']
    return bool(wc.git(repo or wc.REPO, 'status', '--porcelain=v1',
                       '--untracked-files=all', '--', *paths).strip())


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    def timestamp_value(item):
        if isinstance(item, datetime):
            return wc.utc_text(item)
        raise TypeError('image: unsupported operational record value')
    wc.write_manifest(path, json.loads(json.dumps(value, default=timestamp_value)))


@contextmanager
def packer_environment(config, env):
    """Give the pinned plugin process credentials for native AWS login profiles."""
    aws = preflight.Aws(config)
    profile = aws.session._session.full_config.get('profiles', {}).get(config['aws_profile'], {})
    if not profile.get('login_session'):
        yield env
        return
    executable = shutil.which('aws', path=env.get('PATH'))
    preflight.require(executable is not None, 'image: AWS CLI is required for login profile credentials')
    # The plugin's pinned SDK predates login_session. The CLI remains the
    # refreshable provider; never write its credential output to disk or logs.
    original = {name: env.get(name) for name in ('AWS_CONFIG_FILE', 'AWS_SHARED_CREDENTIALS_FILE')}
    with tempfile.TemporaryDirectory(prefix='doris-packer-login-') as temporary:
        directory = Path(temporary)
        helper = directory / 'credentials.py'
        helper.write_text(
            'import os,subprocess,sys\n'
            'env = dict(os.environ)\n'
            f'original = {original!r}\n'
            'for key, value in original.items():\n'
            '    if value is None:\n'
            '        env.pop(key, None)\n'
            '    else:\n'
            '        env[key] = value\n'
            f'sys.exit(subprocess.call({[executable, "configure", "export-credentials", "--profile", config["aws_profile"], "--format", "process"]!r}, env=env))\n'
        )
        helper.chmod(0o600)
        credentials = directory / 'empty-credentials'
        credentials.touch(mode=0o600)
        settings = directory / 'config'
        command = shlex.join((sys.executable, str(helper)))
        settings.write_text(f'[profile {config["aws_profile"]}]\n'
                            f'region = {config["region"]}\ncredential_process = {command}\n')
        settings.chmod(0o600)
        yield {**env, 'AWS_CONFIG_FILE': str(settings),
               'AWS_SHARED_CREDENTIALS_FILE': str(credentials), 'AWS_SDK_LOAD_CONFIG': '1'}


def prepare(config_path, output, *, content_package=None):
    config, _, _ = wc.validate_config(wc.load_document(config_path))
    kernel, images = image_selection(config)
    preflight.require(config['image']['mode'] == 'base' and config['image']['owner_account_id'] == preflight.CANONICAL,
                      'image: require a pinned Canonical base AMI')
    preflight.require(not output.exists(), 'image: choose a new output directory')
    metadata_name = {'doris-rta': 'metadata.json', 'doris-dw': 'metadata-dw.json', 'doris-obs': 'metadata-obs.json'}[kernel]
    metadata = wc.load_document(SPEC / 'locks' / metadata_name)
    for name, expected in metadata['source_sha256'].items():
        actual = hashlib.sha256(subprocess.check_output(['git', 'show', f"{config['course_ref']}:{name}"])).hexdigest()
        preflight.require(actual == expected or actual in metadata.get('compatible_source_sha256', {}).get(name, []),
                          'image: course dependency inputs changed; regenerate and review locks')
    preflight.require(checksum(runtime.RUNTIME / 'packages.json') == metadata['packages_sha256'],
                      'image: runtime package inputs changed; regenerate locks')
    plan = runtime.create_bundle(config_path, output, content_package=content_package)
    plan['python_locks'] = {}
    for name in ('server', kernel):
        filename = name + '.requirements.lock'
        preflight.require(checksum(SPEC / 'locks' / filename) == metadata['locks_sha256'][filename], 'image: lock checksum mismatch')
        shutil.copyfile(SPEC / 'locks' / filename, output / filename)
        plan['python_locks'][name] = filename
        plan['sha256'][filename] = checksum(output / filename)
    (output / 'runtime.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    preflight.require(images['profiles'] == config['course_profiles'], 'image: cache selection mismatch')
    # Digest pins must agree with the selected course commit's reviewed image table.
    table = wc.git(wc.REPO, 'show', f"{config['course_ref']}:maintenance/images.md")
    for image in images['images']:
        preflight.require(re.fullmatch(r'[a-z0-9./_-]+:[A-Za-z0-9_][A-Za-z0-9_.-]*', image['tag']) and
                          re.fullmatch(r'sha256:[0-9a-f]{64}', image['digest']), 'image: invalid container pin')
        preflight.require(any(line.startswith('|') and f"`{image['tag']}`" in line and
                              f"`{image['digest']}`" in line for line in table.splitlines()),
                          'image: container pin differs from selected course source')
    for build in images.get('builds', []):
        preflight.require(build['context'] in {
            'doris-course/02-data-warehousing/environments/streaming',
            'doris-course/02-data-warehousing/environments/bi'} and
            build['base'] in {i['tag'] for i in images['images']} and
            re.fullmatch(r'doris-course-[a-z]+:[A-Za-z0-9_.-]+', build['tag']),
            'image: invalid custom cache selection')
        dockerfile = wc.git(wc.REPO, 'show', f"{config['course_ref']}:{build['context']}/Dockerfile")
        preflight.require(dockerfile.startswith('FROM ' + build['base'] + '\n'),
                          'image: custom build base differs from pinned source')
    for name in ('image_host.py', 'seal.sh'):
        shutil.copyfile(SPEC / name, output / name)
    image_input = {
        'schema_version': config.get('schema_version', 1), 'kind': 'doris-workshop-image-input',
        'base_ami': config['image'], 'course_ref': config['course_ref'],
        'deployment_ref': wc.git(wc.REPO, 'rev-parse', 'HEAD').strip(),
        'deployment_dirty': deployment_dirty(),
        'runtime_sha256': checksum(output / 'runtime.json'),
        'images': images, 'payload_sha256': {name: checksum(output / name) for name in ('image_host.py', 'seal.sh')},
        'online_dependencies': images['online_dependencies'],
    }
    if config.get('schema_version', 1) == 2:
        image_input['content'] = config['content']
    (output / 'image-input.json').write_text(json.dumps(image_input, indent=2, sort_keys=True) + '\n')
    return image_input


def payload_hash(bundle):
    return wc.canonical_hash({p.name: checksum(p) for p in bundle.iterdir() if p.is_file()})


def plan_build(config_path, settings_path, run_dir, build_id):
    preflight.require(bool(re.fullmatch(r'[a-z][a-z0-9-]{2,31}', build_id)), 'image: invalid build ID')
    config, settings = preflight.load_inputs(config_path, settings_path)
    image_selection(config)
    preflight.require(config['instance_type'] == 'm7i.2xlarge' and config['disk_gib'] == 200,
                      'image: require the selected m7i.2xlarge/200 GiB build hardware')
    bundle = Path(settings['runtime_bundle_dir'])
    image_input = wc.load_document(bundle / 'image-input.json')
    preflight.require(image_input['base_ami'] == config['image'] and image_input['course_ref'] == config['course_ref'] and
                      image_input['images']['profiles'] == config['course_profiles'] and not image_input['deployment_dirty'] and
                      not deployment_dirty() and image_input['deployment_ref'] == wc.git(wc.REPO, 'rev-parse', 'HEAD').strip(),
                      'image: use matching base AMI and a clean committed deployment checkout')
    aws = preflight.Aws(config)
    report = preflight.live_check(config, settings, aws)
    # Additional image operations beyond the existing activity permission checks.
    actions = ['ec2:CreateImage', 'ec2:CreateSnapshot', 'ec2:DescribeSnapshots', 'ec2:DescribeImageAttribute',
               'ec2:ModifyImageAttribute', 'ec2:StopInstances', 'ec2:DeregisterImage', 'ec2:DeleteSnapshot',
               'ec2:CreateKeyPair', 'ec2:DeleteKeyPair', 'ec2:DescribeKeyPairs']
    results = aws.items('iam', 'simulate_principal_policy', 'EvaluationResults',
                        PolicySourceArn=report['identity']['principal_arn'], ActionNames=actions, ResourceArns=['*'])
    preflight.require(len(results) == len(actions) and all(r['EvalDecision'] == 'allowed' and not r.get('MissingContextValues')
                      for r in results), 'image: image build/cleanup permission simulation did not pass')
    # The existing two-learner 24h estimate is conservative for one image builder.
    # Add a full 200 GiB snapshot, retained for seven days, before checking the budget.
    snapshot = preflight.price(aws, 'AmazonEC2', {'productFamily': 'Storage Snapshot',
                              'regionCode': config['region']}, 'GB-Mo', datetime.now(timezone.utc))
    snapshot_cost = Decimal(snapshot['usd_per_unit']) * 200 * 7 / 28
    report['image_snapshot'] = {'gib': 200, 'retention_days': 7, 'price': snapshot,
                                'estimated_usd': str(snapshot_cost)}
    total = Decimal(report['cost']['total_usd']) + snapshot_cost
    preflight.require(total <= Decimal(str(config['budget_usd'])), 'image: snapshot plus build estimate exceeds budget')
    report['image_total_usd'] = str(total)
    preflight.require(not run_dir.exists(), 'image: choose a new run directory')
    run_dir.mkdir(parents=True, mode=0o700)
    run_dir = run_dir.resolve()
    variables = {key: config[key] for key in ('aws_profile', 'region', 'owner_id', 'expires_at')}
    variables.update(expected_account_id=config['aws_account_id'], build_id=build_id,
                     runtime_bundle_dir=str(bundle), availability_zone=report['availability_zones'][0],
                     state_bucket_name=settings['state_bucket_name'])
    write(run_dir / 'inputs.tfvars.json', variables)
    write(run_dir / 'preflight.json', report)
    env = preflight.explicit_environment()
    env['TF_DATA_DIR'] = str(run_dir / 'terraform-data')
    module = '-chdir=' + str(wc.SPEC / 'terraform/image-build')
    preflight.terraform([module, 'init', '-input=false', '-lockfile=readonly',
                         '-backend-config=path=' + str(run_dir / 'terraform.tfstate')], env)
    preflight.terraform([module, 'plan', '-input=false', '-lock-timeout=60s',
                         '-var-file=' + str(run_dir / 'inputs.tfvars.json'), '-out=' + str(run_dir / 'saved.tfplan')], env)
    write(run_dir / 'binding.json', {'config': config, 'settings': settings, 'build_id': build_id,
          'payload_sha256': payload_hash(bundle), 'module_sha256': preflight.module_hash('image-build'),
          'plan_sha256': checksum(run_dir / 'saved.tfplan'), 'vars_sha256': checksum(run_dir / 'inputs.tfvars.json'),
          'tool_sha256': checksum(__file__), 'packer_sha256': wc.canonical_hash({p.name: checksum(p) for p in SPEC.iterdir() if p.is_file()})})
    return run_dir


def build_image(run_dir):
    run_dir = run_dir.resolve()
    binding = wc.load_document(run_dir / 'binding.json')
    config, settings = preflight.validate_inputs(binding['config'], binding['settings'])
    bundle = Path(settings['runtime_bundle_dir'])
    previous = wc.load_document(run_dir / 'preflight.json')
    preflight.require(wc.timestamp(previous['valid_until'], 'valid_until') > datetime.now(timezone.utc),
                      'image: saved plan expired; prepare and review a fresh run')
    preflight.require(binding['payload_sha256'] == payload_hash(bundle) and
                      binding['module_sha256'] == preflight.module_hash('image-build') and
                      binding['plan_sha256'] == checksum(run_dir / 'saved.tfplan') and
                      binding['vars_sha256'] == checksum(run_dir / 'inputs.tfvars.json') and
                      binding['tool_sha256'] == checksum(__file__) and
                      binding['packer_sha256'] == wc.canonical_hash({p.name: checksum(p) for p in SPEC.iterdir() if p.is_file()}),
                      'image: plan or build payload changed; create a new run')
    write(run_dir / 'apply-preflight.json', preflight.live_check(config, settings, preflight.Aws(config)))
    env = preflight.explicit_environment()
    env['TF_DATA_DIR'] = str(run_dir / 'terraform-data')
    module = '-chdir=' + str(wc.SPEC / 'terraform/image-build')
    try:
        preflight.terraform([module, 'apply', '-input=false', '-lock-timeout=60s', str(run_dir / 'saved.tfplan')], env)
        outputs = json.loads(preflight.terraform([module, 'output', '-json'], env, capture=True))
        outputs = {k: v['value'] for k, v in outputs.items()}
        write(run_dir / 'outputs.json', outputs)
        write(run_dir / 'provision.json', {'aws_profile': config['aws_profile'], 'region': config['region'],
              'expected_account_id': config['aws_account_id'], 'build_id': binding['build_id'],
              'install_document': outputs['install_document'], 'runtime_bundle_dir': str(bundle)})
        variables = {key: config[key] for key in ('aws_profile', 'region', 'owner_id', 'expires_at', 'course_ref')}
        variables.update(expected_account_id=config['aws_account_id'], base_ami_id=config['image']['ami_id'],
                         build_id=binding['build_id'], run_dir=str(run_dir), python=sys.executable,
                         retain_until=wc.utc_text(datetime.now(timezone.utc) + timedelta(days=7)),
                         **{k: outputs[k] for k in ('subnet_id', 'security_group_id', 'instance_profile')})
        write(run_dir / 'build.pkrvars.json', variables)
        with packer_environment(config, env) as packer_env:
            for command in (['packer', 'init', str(SPEC)],
                            ['packer', 'validate', '-var-file=' + str(run_dir / 'build.pkrvars.json'), str(SPEC)],
                            ['packer', 'build', '-color=false', '-on-error=cleanup', '-var-file=' + str(run_dir / 'build.pkrvars.json'), str(SPEC)]):
                subprocess.run(command, env=packer_env, check=True)
        record_candidate(run_dir, preflight.Aws(config))
    finally:
        # Packer normally terminates its own instance before this destroy. Do not
        # remove the builder's networking/role while its instance is still live.
        cleanup_build(run_dir)


def checked_candidate(run_dir, aws, region, ami, *, unfinished_tags=False):
    binding = wc.load_document(run_dir / 'binding.json')
    config = binding['config']
    preflight.require(region == config['region'] and bool(re.fullmatch(r'ami-[0-9a-f]{17}', ami)),
                      'image: invalid image artifact')
    images = aws.call('ec2', 'describe_images', ImageIds=[ami], Owners=[config['aws_account_id']])['Images']
    preflight.require(len(images) == 1, 'image: built AMI missing')
    image = images[0]
    mappings = image['BlockDeviceMappings']
    mapping = [m for m in mappings if 'Ebs' in m]
    virtual_only = all('Ebs' in m or 'NoDevice' in m or
                       re.fullmatch(r'ephemeral[0-9]+', m.get('VirtualName', '')) for m in mappings)
    preflight.require(image['State'] == 'available' and not image['Public'] and image['Architecture'] == 'x86_64' and
                      virtual_only and len(mapping) == 1 and mapping[0]['DeviceName'] == '/dev/sda1' and
                      mapping[0]['Ebs']['VolumeSize'] == 200 and mapping[0]['Ebs']['DeleteOnTermination'],
                      'image: AMI privacy, architecture or root volume mismatch')
    tags = {t['Key']: t['Value'] for t in image.get('Tags', [])}
    manifest = wc.load_document(run_dir / 'image-manifest.json')
    preflight.require(tags.get('ImageBuild') == binding['build_id'] and tags.get('CourseRef') == config['course_ref'] and
                      tags.get('Scope') in ({'image', 'image-build'} if unfinished_tags else {'image'}) and
                      manifest['course_ref'] == config['course_ref'] and
                      manifest.get('profiles') == config['course_profiles'],
                      'image: AMI provenance does not match this build manifest')
    attrs = aws.call('ec2', 'describe_image_attribute', ImageId=ami, Attribute='launchPermission')
    preflight.require(not attrs.get('LaunchPermissions'), 'image: candidate must have no sharing grants')
    snapshots = aws.call('ec2', 'describe_snapshots', SnapshotIds=[mapping[0]['Ebs']['SnapshotId']])['Snapshots']
    preflight.require(len(snapshots) == 1 and snapshots[0]['Encrypted'] and snapshots[0]['State'] == 'completed' and
                      snapshots[0]['OwnerId'] == config['aws_account_id'], 'image: snapshot encryption/owner mismatch')
    record = {'ami_id': ami, 'region': region, 'status': 'candidate', 'course_ref': config['course_ref'],
          'snapshots': [s['SnapshotId'] for s in snapshots], 'manifest_sha256': checksum(run_dir / 'image-manifest.json'),
          'release_requirement': 'Independent new-instance verification remains required.',
          'retention': 'Keep for the next validation task; review after seven days. Tags do not delete snapshots.'}
    return record, image, snapshots[0]


def recovery_reference(run_dir):
    recovery = wc.load_document(run_dir / 'recovered-artifact.json')
    binding = wc.load_document(run_dir / 'binding.json')
    preflight.require(recovery.get('kind') == 'doris-workshop-image-artifact-recovery' and
                      recovery.get('schema_version') == 1 and recovery.get('build_id') == binding['build_id'] and
                      recovery.get('binding_sha256') == checksum(run_dir / 'binding.json') and
                      recovery.get('image_manifest_sha256') == checksum(run_dir / 'image-manifest.json') and
                      recovery.get('region') == binding['config']['region'] and
                      bool(re.fullmatch(r'vol-[0-9a-f]{17}', recovery.get('source_volume_id', ''))),
                      'image: recovered artifact provenance changed; preserve it for review')
    return recovery


def record_candidate(run_dir, aws):
    recovery = None
    if (run_dir / 'packer-manifest.json').exists():
        packer = wc.load_document(run_dir / 'packer-manifest.json')
        region, ami = packer['builds'][-1]['artifact_id'].split(':')
    else:
        recovery = recovery_reference(run_dir)
        region, ami = recovery['region'], recovery['ami_id']
    record, _, snapshot = checked_candidate(run_dir, aws, region, ami)
    if recovery:
        preflight.require(snapshot.get('VolumeId') == recovery['source_volume_id'],
                          'image: recovered snapshot source volume changed')
    path = run_dir / 'candidate.json'
    if path.exists():
        preflight.require(wc.load_document(path) == record, 'image: existing candidate record differs; preserve it for review')
    else:
        write(path, record)


def recover_artifact(run_dir, ami, source_volume_id, aws):
    """Finish candidate metadata after CreateImage succeeded but Packer polling failed."""
    preflight.require(not deployment_dirty(), 'image: use a clean committed recovery tool')
    preflight.require(not (run_dir / 'packer-manifest.json').exists(),
                      'image: a Packer manifest exists; use verify instead of recovery')
    preflight.require(bool(re.fullmatch(r'ami-[0-9a-f]{17}', ami)) and
                      bool(re.fullmatch(r'vol-[0-9a-f]{17}', source_volume_id)),
                      'image: recovery requires exact AMI and captured source volume IDs')
    binding = wc.load_document(run_dir / 'binding.json')
    config = binding['config']
    if (run_dir / 'recovered-artifact.json').exists():
        recovery = recovery_reference(run_dir)
        preflight.require(recovery['ami_id'] == ami and recovery['source_volume_id'] == source_volume_id,
                          'image: preserve the existing recovery; another artifact was requested')
        record_candidate(run_dir, aws)
        return
    state = wc.load_document(run_dir / 'terraform.tfstate')
    preflight.require(not any(r.get('mode') == 'managed' and r.get('instances') for r in state.get('resources', [])),
                      'image: finish build cleanup before recovering an artifact')
    bundle = Path(binding['settings']['runtime_bundle_dir'])
    preflight.require(payload_hash(bundle) == binding['payload_sha256'] and
                      checksum(run_dir / 'inputs.tfvars.json') == binding['vars_sha256'],
                      'image: recovery inputs changed')
    manifest = wc.load_document(run_dir / 'image-manifest.json')
    inputs = wc.load_document(bundle / 'image-input.json')
    preflight.require(manifest.get('image_input_sha256') == checksum(bundle / 'image-input.json') and
                      manifest.get('runtime_sha256') == checksum(bundle / 'runtime.json') and
                      manifest.get('deployment_ref') == inputs['deployment_ref'] and not inputs['deployment_dirty'],
                      'image: recovery host manifest differs from the saved clean inputs')
    instance = wc.load_document(run_dir / 'build-instance.json')['instance_id']
    prepare_command = wc.load_document(run_dir / 'build-command.json')
    seal_command = wc.load_document(run_dir / 'seal-command.json')
    preflight.require(prepare_command['instance_id'] == seal_command['instance_id'] == instance,
                      'image: preparation and sealing receipts belong to another host')
    preflight.require(aws.call('sts', 'get_caller_identity')['Account'] == config['aws_account_id'],
                      'image: recovery account changed')
    hosts = aws.call('ec2', 'describe_instances', Filters=[{'Name': 'instance-id', 'Values': [instance]}])['Reservations']
    preflight.require(all(i['State']['Name'] == 'terminated' for r in hosts for i in r['Instances']),
                      'image: the original builder is still active')
    invocations = aws.call('ssm', 'list_command_invocations', CommandId=prepare_command['command_id'],
                           InstanceId=instance, Details=True)['CommandInvocations']
    preflight.require(len(invocations) == 1 and invocations[0]['Status'] == 'Success' and
                      {p['Name'] for p in invocations[0].get('CommandPlugins', [])} ==
                      {'downloadRuntime', 'installRuntime', 'prepareImage'} and
                      all(p['Status'] == 'Success' and p['ResponseCode'] == 0 for p in invocations[0]['CommandPlugins']),
                      'image: the original native preparation did not complete successfully')
    record, image, snapshot = checked_candidate(run_dir, aws, config['region'], ami, unfinished_tags=True)
    tags = {t['Key']: t['Value'] for t in image.get('Tags', [])}
    preflight.require(image.get('Name') == 'doris-workshop-' + binding['build_id'] and
                      image.get('OwnerId') == config['aws_account_id'] and
                      tags.get('Project') == 'doris-workshop' and tags.get('Owner') == config['owner_id'] and
                      tags.get('ImageStatus') in (None, 'candidate') and not tags.get('ReleaseRef'),
                      'image: recovery cannot retag another or published image')
    preflight.require(snapshot.get('VolumeId') == source_volume_id and snapshot.get('VolumeSize') == 200 and
                      'CreateImage(' + instance + ')' in snapshot.get('Description', '') and
                      ami in snapshot.get('Description', ''),
                      'image: snapshot does not come from the captured builder root')
    root = wc.load_document(run_dir / 'build-instance.json').get('root_volume_id')
    preflight.require(root is None or root == source_volume_id, 'image: captured builder root differs')
    original_tags = {t['Key']: t['Value'] for t in snapshot.get('Tags', [])}
    preflight.require(all(k not in original_tags or original_tags[k] == v for k, v in {
        'Project': 'doris-workshop', 'ImageBuild': binding['build_id'], 'Owner': config['owner_id'],
        'CourseRef': config['course_ref']}.items()) and original_tags.get('Scope') in (None, 'image-build', 'image'),
        'image: snapshot ownership tags conflict with this recovery')
    retain = wc.load_document(run_dir / 'build.pkrvars.json')['retain_until']
    preflight.require(wc.timestamp(retain, 'retain_until') > datetime.now(timezone.utc),
                      'image: original candidate retention expired; review before recovery')
    expected = {'Project': 'doris-workshop', 'Scope': 'image', 'ImageBuild': binding['build_id'],
                'Owner': config['owner_id'], 'CourseRef': config['course_ref'], 'ExpiresAt': retain,
                'ImageStatus': 'candidate'}
    if any(any(current.get(k) != v for k, v in expected.items()) for current in (tags, original_tags)):
        aws.client('ec2').create_tags(Resources=[ami, snapshot['SnapshotId']],
            Tags=[{'Key': k, 'Value': v} for k, v in expected.items()])
    _, current_image, current_snapshot = checked_candidate(run_dir, aws, config['region'], ami)
    preflight.require(all(all({t['Key']: t['Value'] for t in item.get('Tags', [])}.get(k) == v
                              for k, v in expected.items()) for item in (current_image, current_snapshot)),
                      'image: candidate tags not yet reconciled; verify current metadata before retrying')
    write(run_dir / 'recovered-artifact.json', {
        'schema_version': 1, 'kind': 'doris-workshop-image-artifact-recovery',
        'checked_at': wc.utc_text(datetime.now(timezone.utc)), 'build_id': binding['build_id'],
        'region': config['region'], 'ami_id': ami, 'source_instance_id': instance,
        'source_volume_id': source_volume_id, 'binding_sha256': checksum(run_dir / 'binding.json'),
        'image_manifest_sha256': record['manifest_sha256'], 'prepare_command_id': prepare_command['command_id'],
        'seal_command_id': seal_command['command_id'], 'operator_ref': wc.git(wc.REPO, 'rev-parse', 'HEAD').strip(),
        'operator_tool_sha256': checksum(__file__),
        'scope': 'Recovered candidate metadata; original Packer failure is preserved. Independent validation remains required.'})
    record_candidate(run_dir, aws)


def cleanup_build(run_dir):
    import boto3
    from botocore.config import Config
    env = preflight.explicit_environment()
    binding = wc.load_document(run_dir / 'binding.json')
    config = binding['config']
    session = boto3.Session(profile_name=config['aws_profile'], region_name=config['region'])
    options = Config(connect_timeout=5, read_timeout=20, retries={'total_max_attempts': 3})
    ec2, sts = [session.client(n, config=options) for n in ('ec2', 'sts')]
    preflight.require(sts.get_caller_identity()['Account'] == config['aws_account_id'], 'cleanup: account changed')
    found = ec2.describe_instances(Filters=[{'Name': 'tag:ImageBuild', 'Values': [binding['build_id']]},
                                            {'Name': 'tag:Scope', 'Values': ['image-build']},
                                            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}])
    ids = [i['InstanceId'] for r in found['Reservations'] for i in r['Instances']]
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
        ec2.get_waiter('instance_terminated').wait(InstanceIds=ids, WaiterConfig={'Delay': 10, 'MaxAttempts': 60})
    # The Amazon plugin can create a temporary key pair even with no communicator.
    # Its step tags the key with run_tags. Never enumerate/delete unrelated keys.
    keys = ec2.describe_key_pairs(Filters=[{'Name': 'tag:ImageBuild', 'Values': [binding['build_id']]},
                                         {'Name': 'tag:Scope', 'Values': ['image-build']}])['KeyPairs']
    for key in keys:
        ec2.delete_key_pair(KeyPairId=key['KeyPairId'])
    env['TF_DATA_DIR'] = str(run_dir / 'terraform-data')
    preflight.terraform(['-chdir=' + str(wc.SPEC / 'terraform/image-build'), 'destroy', '-auto-approve',
                         '-input=false', '-lock-timeout=60s', '-var-file=' + str(run_dir / 'inputs.tfvars.json')], env)


def provision(run_dir):
    """Packer shell-local entry. No inbound SSH or deployer credential upload."""
    import boto3
    from botocore.config import Config
    preflight.explicit_environment()
    settings = wc.load_document(run_dir / 'provision.json')
    session = boto3.Session(profile_name=settings['aws_profile'], region_name=settings['region'])
    options = Config(connect_timeout=5, read_timeout=20, retries={'total_max_attempts': 3, 'mode': 'standard'})
    ec2, ssm, sts = [session.client(name, config=options) for name in ('ec2', 'ssm', 'sts')]
    preflight.require(sts.get_caller_identity()['Account'] == settings['expected_account_id'], 'image: account changed')
    started = time.monotonic()
    instance = None
    while time.monotonic() - started < 900:
        found = ec2.describe_instances(Filters=[{'Name': 'tag:ImageBuild', 'Values': [settings['build_id']]},
                                                {'Name': 'instance-state-name', 'Values': ['running']}])
        instances = [i for r in found['Reservations'] for i in r['Instances']]
        preflight.require(len(instances) <= 1, 'image: ambiguous build instance tag')
        if instances:
            instance = instances[0]['InstanceId']
            info = ssm.describe_instance_information(Filters=[{'Key': 'InstanceIds', 'Values': [instance]}])
            if any(i['PingStatus'] == 'Online' for i in info['InstanceInformationList']):
                break
        time.sleep(10)
    else:
        raise preflight.PreflightError('image: build host did not register with SSM')
    receipt = {'instance_id': instance, 'build_id': settings['build_id']}
    roots = [m['Ebs']['VolumeId'] for m in instances[0].get('BlockDeviceMappings', [])
             if m.get('DeviceName') == '/dev/sda1' and 'Ebs' in m]
    if len(roots) == 1:
        receipt['root_volume_id'] = roots[0]
    write(run_dir / 'build-instance.json', receipt)
    command = ssm.send_command(InstanceIds=[instance], DocumentName=settings['install_document'], TimeoutSeconds=7200)['Command']['CommandId']
    write(run_dir / 'build-command.json', {'instance_id': instance, 'command_id': command})
    deadline = time.monotonic() + 7200
    completed_without_step = 0
    while time.monotonic() < deadline:
        aggregate = ssm.list_commands(CommandId=command)['Commands']
        if aggregate and aggregate[0]['Status'] not in ('Pending', 'InProgress', 'Delayed', 'Success'):
            write(run_dir / 'failed-command.json', operations.invocation_summary(aggregate[0]))
            raise preflight.PreflightError('image: an earlier SSM build step failed; inspect private command record')
        try:
            result = ssm.get_command_invocation(CommandId=command, InstanceId=instance, PluginName='prepareImage')
        except (ssm.exceptions.InvocationDoesNotExist, ssm.exceptions.InvalidPluginName):
            # SSM only exposes later document steps once they start. The command
            # can still be installing packages when prepareImage is not visible.
            if aggregate and aggregate[0]['Status'] == 'Success':
                completed_without_step += 1
                preflight.require(completed_without_step <= 6,
                                  'image: completed SSM document has no preparation step')
            time.sleep(5)
            continue
        if result['Status'] == 'Success':
            # The manifest is the only stdout of this document step, below SSM's output limit.
            report = json.loads(result['StandardOutputContent'])
            expected = wc.load_document(Path(settings['runtime_bundle_dir']) / 'image-input.json')
            preflight.require(report['image_input_sha256'] == checksum(Path(settings['runtime_bundle_dir']) / 'image-input.json') and
                              report['course_ref'] == expected['course_ref'], 'image: host manifest/input mismatch')
            write(run_dir / 'image-manifest.json', report)
            break
        if result['Status'] not in ('Pending', 'InProgress', 'Delayed'):
            write(run_dir / 'failed-command.json', operations.invocation_summary(result))
            raise preflight.PreflightError('image: SSM preparation failed; inspect private failed-command.json')
        time.sleep(10)
    else:
        raise preflight.PreflightError('image: preparation timed out; reconcile Packer resources')
    # Sealing runs in /run after SSM acknowledges this command. Wait for the host to
    # stop itself before allowing Packer to snapshot; an arbitrary pause is unsafe.
    seal = ssm.send_command(InstanceIds=[instance], DocumentName='AWS-RunShellScript',
                           Parameters={'commands': ['systemd-run --unit=doris-image-seal --on-active=15s /bin/bash /var/lib/doris-workshop/incoming/seal.sh'],
                                       'executionTimeout': ['60']})['Command']['CommandId']
    write(run_dir / 'seal-command.json', {'command_id': seal, 'instance_id': instance})
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        state = ec2.describe_instances(InstanceIds=[instance])['Reservations'][0]['Instances'][0]['State']['Name']
        if state == 'stopped':
            return
        preflight.require(state not in ('terminated', 'shutting-down'), 'image: builder terminated before snapshot')
        time.sleep(5)
    raise preflight.PreflightError('image: sealing did not stop the build host; no image should be published')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    make = subs.add_parser('prepare')
    make.add_argument('--config', type=Path, required=True)
    make.add_argument('--output', type=Path, required=True)
    make.add_argument('--content-package', type=Path)
    build = subs.add_parser('provision')
    build.add_argument('--run-dir', type=Path, required=True)
    planned = subs.add_parser('plan')
    planned.add_argument('--config', type=Path, required=True)
    planned.add_argument('--settings', type=Path, required=True)
    planned.add_argument('--run-dir', type=Path, required=True)
    planned.add_argument('--build-id', required=True)
    for command in ('build', 'cleanup', 'verify'):
        command_parser = subs.add_parser(command)
        command_parser.add_argument('--run-dir', type=Path, required=True)
    recovery = subs.add_parser('recover')
    recovery.add_argument('--run-dir', type=Path, required=True)
    recovery.add_argument('--ami-id', required=True)
    recovery.add_argument('--source-volume-id', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.config, args.output.absolute(), content_package=args.content_package)
            print('Fixed image inputs prepared locally; no AWS resources created.')
        elif args.command == 'plan':
            plan_build(args.config, args.settings, args.run_dir, args.build_id)
        elif args.command == 'build':
            build_image(args.run_dir)
        elif args.command == 'cleanup':
            cleanup_build(args.run_dir.resolve())
        elif args.command == 'verify':
            run_dir = args.run_dir.resolve()
            config = wc.load_document(run_dir / 'binding.json')['config']
            record_candidate(run_dir, preflight.Aws(config))
            print('Private candidate image verified; independent new-instance validation remains required.')
        elif args.command == 'recover':
            run_dir = args.run_dir.resolve()
            config = wc.load_document(run_dir / 'binding.json')['config']
            recover_artifact(run_dir, args.ami_id, args.source_volume_id, preflight.Aws(config))
            print('Created image recovered as a candidate; original Packer failure and independent validation gate retained.')
        else:
            provision(args.run_dir.resolve())
        return 0
    except (wc.ConfigError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, wc.ConfigError) else 'image: operation failed; inspect private diagnostics', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
