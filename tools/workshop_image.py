#!/usr/bin/env python3
"""Prepare fixed private-image inputs and provision Packer hosts through SSM only."""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import workshop_config as wc
import workshop_preflight as preflight
import workshop_runtime as runtime
import workshop_operations as operations

SPEC = wc.SPEC / 'packer'


def deployment_dirty():
    paths = [Path(__file__), Path(preflight.__file__), Path(runtime.__file__), Path(wc.__file__),
             *runtime.RUNTIME.iterdir(), operations.NODE_PATH, *(wc.SPEC / 'terraform/image-build').glob('*.tf'),
             wc.SPEC / 'terraform/image-build/.terraform.lock.hcl',
             *(p for p in SPEC.rglob('*') if p.is_file() and '__pycache__' not in p.parts)]
    paths = [str(p.relative_to(wc.REPO)) for p in paths if p.is_file()]
    tracked = set(wc.git(wc.REPO, 'ls-files', '--', *paths).splitlines())
    return set(paths) != tracked or bool(wc.git(wc.REPO, 'diff', 'HEAD', '--', *paths))


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    def timestamp_value(item):
        if isinstance(item, datetime):
            return wc.utc_text(item)
        raise TypeError('image: unsupported operational record value')
    wc.write_manifest(path, json.loads(json.dumps(value, default=timestamp_value)))


def prepare(config_path, output):
    config, _, _ = wc.validate_config(wc.load_document(config_path))
    preflight.require(config['course_profiles'] == ['rta-main'], 'image: only the validated rta-main profile is supported')
    preflight.require(config['image']['mode'] == 'base' and config['image']['owner_account_id'] == preflight.CANONICAL,
                      'image: require a pinned Canonical base AMI')
    preflight.require(not output.exists(), 'image: choose a new output directory')
    metadata = wc.load_document(SPEC / 'locks/metadata.json')
    for name, expected in metadata['source_sha256'].items():
        actual = hashlib.sha256(subprocess.check_output(['git', 'show', f"{config['course_ref']}:{name}"])).hexdigest()
        preflight.require(actual == expected, 'image: course dependency inputs changed; regenerate and review locks')
    preflight.require(checksum(runtime.RUNTIME / 'packages.json') == metadata['packages_sha256'],
                      'image: runtime package inputs changed; regenerate locks')
    plan = runtime.create_bundle(config_path, output)
    plan['python_locks'] = {}
    for name in ('server', 'doris-rta'):
        filename = name + '.requirements.lock'
        preflight.require(checksum(SPEC / 'locks' / filename) == metadata['locks_sha256'][filename], 'image: lock checksum mismatch')
        shutil.copyfile(SPEC / 'locks' / filename, output / filename)
        plan['python_locks'][name] = filename
        plan['sha256'][filename] = checksum(output / filename)
    (output / 'runtime.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    images = wc.load_document(SPEC / 'images.json')
    preflight.require(images['profiles'] == ['rta-main'], 'image: cache selection mismatch')
    # Digest pins must agree with the selected course commit's reviewed image table.
    table = wc.git(wc.REPO, 'show', f"{config['course_ref']}:maintenance/images.md")
    for image in images['images']:
        preflight.require(re.fullmatch(r'[a-z0-9./:_-]+', image['tag']) and
                          re.fullmatch(r'sha256:[0-9a-f]{64}', image['digest']), 'image: invalid container pin')
        preflight.require(any(line.startswith('|') and f"`{image['tag']}`" in line and
                              f"`{image['digest']}`" in line for line in table.splitlines()),
                          'image: container pin differs from selected course source')
    for name in ('image_host.py', 'seal.sh'):
        shutil.copyfile(SPEC / name, output / name)
    image_input = {
        'schema_version': 1, 'kind': 'doris-workshop-image-input',
        'base_ami': config['image'], 'course_ref': config['course_ref'],
        'deployment_ref': wc.git(wc.REPO, 'rev-parse', 'HEAD').strip(),
        'deployment_dirty': deployment_dirty(),
        'runtime_sha256': checksum(output / 'runtime.json'),
        'images': images, 'payload_sha256': {name: checksum(output / name) for name in ('image_host.py', 'seal.sh')},
        'online_dependencies': images['online_dependencies'],
    }
    (output / 'image-input.json').write_text(json.dumps(image_input, indent=2, sort_keys=True) + '\n')
    return image_input


def payload_hash(bundle):
    return wc.canonical_hash({p.name: checksum(p) for p in bundle.iterdir() if p.is_file()})


def plan_build(config_path, settings_path, run_dir, build_id):
    preflight.require(bool(re.fullmatch(r'[a-z][a-z0-9-]{2,31}', build_id)), 'image: invalid build ID')
    config, settings = preflight.load_inputs(config_path, settings_path)
    preflight.require(config['instance_type'] == 'm7i.2xlarge' and config['disk_gib'] == 200 and
                      config['course_profiles'] == ['rta-main'], 'image: require validated rta-main hardware')
    bundle = Path(settings['runtime_bundle_dir'])
    image_input = wc.load_document(bundle / 'image-input.json')
    preflight.require(image_input['base_ami'] == config['image'] and not image_input['deployment_dirty'] and
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
        for command in (['packer', 'init', str(SPEC)],
                        ['packer', 'validate', '-var-file=' + str(run_dir / 'build.pkrvars.json'), str(SPEC)],
                        ['packer', 'build', '-color=false', '-on-error=cleanup', '-var-file=' + str(run_dir / 'build.pkrvars.json'), str(SPEC)]):
            subprocess.run(command, env=env, check=True)
        record_candidate(run_dir, preflight.Aws(config))
    finally:
        # Packer normally terminates its own instance before this destroy. Do not
        # remove the builder's networking/role while its instance is still live.
        cleanup_build(run_dir)


def record_candidate(run_dir, aws):
    binding = wc.load_document(run_dir / 'binding.json')
    config = binding['config']
    packer = wc.load_document(run_dir / 'packer-manifest.json')
    region, ami = packer['builds'][-1]['artifact_id'].split(':')
    preflight.require(region == config['region'] and bool(re.fullmatch(r'ami-[0-9a-f]{17}', ami)),
                      'image: invalid Packer artifact')
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
                      tags.get('Scope') == 'image' and manifest['course_ref'] == config['course_ref'],
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
    path = run_dir / 'candidate.json'
    if path.exists():
        preflight.require(wc.load_document(path) == record, 'image: existing candidate record differs; preserve it for review')
    else:
        write(path, record)


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
    write(run_dir / 'build-instance.json', {'instance_id': instance, 'build_id': settings['build_id']})
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
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.config, args.output.absolute())
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
        else:
            provision(args.run_dir.resolve())
        return 0
    except (wc.ConfigError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, wc.ConfigError) else 'image: operation failed; inspect private diagnostics', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
