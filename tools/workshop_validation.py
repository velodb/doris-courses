#!/usr/bin/env python3
"""Plan and audit an isolated fresh launch of a private candidate image."""
import argparse
from datetime import datetime, timezone
import base64
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import workshop_config as wc
import workshop_preflight as pf
import workshop_image as images
import workshop as ws
import workshop_course

MODULE = wc.SPEC / 'terraform/image-validation'


def named_browser_routes(config, routes):
    """Assign a stable browser origin even when Flink chooses a new host port."""
    if not set(config['course_profiles']) & {'dw-main', 'obs-main'}:
        return {}
    ports = ({'obs-fe': '28030', 'obs-grafana': '23000', 'obs-demo': '28082',
              'obs-mailpit': '28025', 'obs-litefuse': '23100'}
             if 'obs-main' in config['course_profiles'] else {'dw-fe': '51030'})
    if 'dw-bi' in config['course_profiles']:
        ports['dw-superset'] = '51888'
    if 'dw-cdc' in config['course_profiles']:
        ports['dw-flink'] = '51881'
    pf.require(all(set(ports.values()) <= row.keys() for row in routes.values()),
               'validation: supply routes for every selected course browser service')
    return {learner: {service: row[port] for service, port in ports.items()}
            for learner, row in routes.items()}


def environment(directory):
    env = pf.explicit_environment()
    env['TF_DATA_DIR'] = str(directory / 'terraform-data')
    return env


def tf(directory, *args, capture=False):
    return pf.terraform(['-chdir=' + str(MODULE), *map(str, args)], environment(directory), capture=capture)


def candidate_files(candidate_run):
    c = wc.load_document(candidate_run / 'candidate.json')
    m = wc.load_document(candidate_run / 'image-manifest.json')
    pf.require(c['status']=='candidate' and images.checksum(candidate_run/'image-manifest.json')==c['manifest_sha256'],
               'validation: candidate manifest mismatch')
    return c,m


def plan(config_path, settings_path, candidate_run, directory, routes_path):
    config, settings = pf.load_inputs(config_path, settings_path)
    pf.require(len(config['learner_ids']) == 1, 'validation: use one independent validation learner')
    c,m = candidate_files(candidate_run)
    pf.require(c['course_ref']==config['course_ref'] and m['profiles']==config['course_profiles'] and c['region']==config['region'],
               'validation: candidate selection does not match the event')
    aws = pf.Aws(config)
    images.record_candidate(candidate_run, aws)
    report = pf.live_check(config, settings, aws)
    routes = wc.load_document(routes_path)
    pf.require(set(routes)==set(config['learner_ids']), 'validation: supply browser routes for each selected learner')
    import workshop_urls
    for learner,ports in routes.items():
        workshop_urls.build_routes(config,learner,ports)
        if 'rta-main' in config['course_profiles']:
            pf.require({'18030','18130','18131','18132'} <= ports.keys(),
                       'validation: RTA requires routes for the host FE ports 18030 and 18130-18132')
    named_routes = named_browser_routes(config, routes)
    pf.require(not directory.exists(), 'validation: choose a new run directory')
    directory.mkdir(parents=True, mode=0o700)
    directory=directory.resolve()
    variables = {k:config[k] for k in ('aws_profile','region','owner_id','event_id','learner_ids','instance_type','disk_gib','expires_at','course_ref')}
    variables.update(expected_account_id=config['aws_account_id'],state_bucket_name=settings['state_bucket_name'],
        availability_zone=report['availability_zones'][0],ami_id=c['ami_id'],release_ref='candidate-validation',
        image_manifest_sha256=c['manifest_sha256'],runtime_sha256=m['runtime_sha256'],browser_routes=routes,
        browser_services=named_routes)
    images.write(directory/'inputs.tfvars.json',variables)
    images.write(directory/'preflight.json', {**report,'validation_ami_id':c['ami_id']})
    tf(directory,'init','-input=false','-lockfile=readonly','-backend-config=path='+str(directory/'terraform.tfstate'))
    tf(directory,'plan','-input=false','-lock-timeout=60s','-var-file='+str(directory/'inputs.tfvars.json'),'-out='+str(directory/'saved.tfplan'))
    images.write(directory/'binding.json',{'config':config,'settings':settings,'candidate':c,'manifest':m,
        'candidate_run':str(candidate_run.resolve()),'plan_sha256':images.checksum(directory/'saved.tfplan'),
        'vars_sha256':images.checksum(directory/'inputs.tfvars.json'),'module_sha256':pf.module_hash('image-validation'),
        'initializer_sha256':images.checksum(wc.SPEC/'runtime/start_image.py')})
    print('Validation plan saved. Review before apply; this is a new instance, not the Packer host.')


def apply(directory):
    b=wc.load_document(directory/'binding.json');p=wc.load_document(directory/'preflight.json')
    pf.require(wc.timestamp(p['valid_until'],'valid_until')>datetime.now(timezone.utc),'validation: plan expired')
    pf.require(images.checksum(directory/'saved.tfplan')==b['plan_sha256'] and
       images.checksum(directory/'inputs.tfvars.json')==b['vars_sha256'] and
       pf.module_hash('image-validation')==b['module_sha256'] and
       images.checksum(wc.SPEC/'runtime/start_image.py')==b['initializer_sha256'],'validation: plan inputs or initializer changed')
    aws=pf.Aws(b['config'])
    images.record_candidate(Path(b['candidate_run']),aws)
    images.write(directory/'apply-preflight.json',pf.live_check(b['config'],b['settings'],aws))
    tf(directory,'apply','-input=false','-lock-timeout=60s',directory/'saved.tfplan')
    outputs=json.loads(tf(directory,'output','-json',capture=True))
    images.write(directory/'outputs.json',{k:v['value'] for k,v in outputs.items()})
    print('Validation instance created. Run audit before executing Labs; destroy when verification finishes.')


def send_file_command(aws, instance, code, expected, timeout=600):
    # Keep each SSM StringList item short; only reviewed code and public metadata
    # are encoded. No operator credentials or browser tokens are transmitted.
    encoded=base64.b64encode(gzip.compress(code.encode(),mtime=0)).decode()
    commands=['set -eu','umask 077','printf "" > /run/workshop-audit.b64']
    commands += ["printf '%s' '"+encoded[i:i+1000]+"' >> /run/workshop-audit.b64" for i in range(0,len(encoded),1000)]
    commands += ['base64 -d /run/workshop-audit.b64 > /run/workshop-audit.py.gz',
       "python3.12 -c \"import gzip,pathlib; p=pathlib.Path('/run/workshop-audit.py'); p.write_bytes(gzip.decompress(p.with_suffix('.py.gz').read_bytes()))\"",
       "printf '%s' '"+base64.b64encode(json.dumps(expected).encode()).decode()+"' | base64 -d > /run/workshop-audit-input.json",
       'cloud-init status --wait >/dev/null', 'python3.12 /run/workshop-audit.py /run/workshop-audit-input.json']
    pf.require(len(json.dumps(commands).encode()) < 24000, 'validation: SSM command payload exceeds its bound')
    return aws.client('ssm').send_command(InstanceIds=[instance],DocumentName='AWS-RunShellScript',
         Parameters={'commands':commands,'executionTimeout':[str(timeout)]},TimeoutSeconds=600)['Command']['CommandId']


def course(directory):
    b=wc.load_document(directory/'binding.json');config=b['config'];aws=pf.Aws(config)
    workshop_course.selection(config)
    audit=wc.load_document(directory/'initial-audit.json')
    pf.require(audit['passed'], 'validation: initial audit must pass before Labs')
    attempt=directory/'courses'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    attempt.mkdir(parents=True,mode=0o700)
    expected={'ami_id':b['candidate']['ami_id'],'instance_id':audit['instance_id'],'course_ref':config['course_ref'],
        'event_id':config['event_id'],'learner_id':config['learner_ids'][0], 'course_profiles':config['course_profiles'],
        'course_output_dir':'/var/lib/doris-workshop/home/'+attempt.name}
    encoded=base64.b64encode((wc.SPEC/'runtime/validate_course.py').read_bytes()).decode()
    driver="""import base64,json,subprocess,sys
from pathlib import Path
root=Path('/var/lib/doris-workshop/home')
code=root/'validation-course.py';inputs=root/'validation-course-input.json'
code.write_bytes(base64.b64decode(%r));inputs.write_text(Path(sys.argv[1]).read_text())
subprocess.run(['chown','workshop:workshop',str(code),str(inputs)],check=True)
subprocess.run(['runuser','--user','workshop','--','env','-i','HOME='+str(root),
 'PATH=/opt/doris-workshop/server-venv/bin:/usr/local/bin:/usr/bin:/bin','LANG=C.UTF-8','PYTHONNOUSERSITE=1',
 '/opt/doris-workshop/server-venv/bin/python','-I',str(code),str(inputs)],check=True)
""" % encoded
    execution_timeout = 14400 if 'obs-main' in config['course_profiles'] else 7200
    command=send_file_command(aws,audit['instance_id'],driver,expected,timeout=execution_timeout)
    images.write(attempt/'command.json',{'command_id':command,'instance_id':audit['instance_id'],'expected':expected})
    deadline=time.monotonic()+execution_timeout+300
    while time.monotonic()<deadline:
        try:r=aws.client('ssm').get_command_invocation(CommandId=command,InstanceId=audit['instance_id'])
        except aws.client('ssm').exceptions.InvocationDoesNotExist:time.sleep(5);continue
        if r['Status'] in ('Pending','InProgress','Delayed'):time.sleep(10);continue
        images.write(attempt/'invocation.json',ws.ops.invocation_summary(r))
        report=next((line[len('COURSE_REPORT='):] for line in r['StandardOutputContent'].splitlines() if line.startswith('COURSE_REPORT=')),None)
        pf.require(report is not None,'validation: course returned no report; inspect the private invocation')
        value=json.loads(report);images.write(attempt/'report.json',value)
        pf.require(r['Status']=='Success' and value['passed'] and workshop_course.verify_report(config,value),
                   'validation: complete selected course failed; preserve the report and destroy resources')
        pf.require(not (directory/'course-report.json').exists(),'validation: preserve the successful course report')
        images.write(directory/'course-report.json',value)
        print(json.dumps({'passed':True,'notebook_count':value['notebook_count'],'code_cells':value['code_cells']}));return
    raise wc.ConfigError('validation: course timed out; inspect the remote execution and destroy resources')


def checkpoint(directory, attempt, gate):
    """Acknowledge a completed browser action without restarting the course run."""
    attempt = attempt.resolve()
    pf.require(attempt.parent == (directory / 'courses').resolve(), 'validation: select this run\'s exact course attempt')
    b = wc.load_document(directory / 'binding.json')
    command = wc.load_document(attempt / 'command.json')
    expected = command['expected']
    pf.require('obs-main' in b['config']['course_profiles'] and
               gate in workshop_course._contract['OBS_BROWSER_GATES'].values() and
               expected['instance_id'] == wc.load_document(directory / 'initial-audit.json')['instance_id'] and
               expected['ami_id'] == b['candidate']['ami_id'] and
               expected['course_ref'] == b['config']['course_ref'] and
               expected['event_id'] == b['config']['event_id'] and
               expected['learner_id'] == b['config']['learner_ids'][0] and
               expected['course_output_dir'] == '/var/lib/doris-workshop/home/' + attempt.name,
               'validation: browser checkpoint does not match the audited course attempt')
    code = """import json,os,sys
from pathlib import Path
value=json.loads(Path(sys.argv[1]).read_text());root=Path(value['course_output_dir'])
progress=json.loads((root/'progress.json').read_text())
waiting=progress.get('waiting_browser',{})
assert progress.get('done') is False and waiting.get('gate')==value['gate']
assert progress['instance_id']==value['instance_id'] and progress['image_id']==value['ami_id']
assert progress['course_ref']==value['course_ref']
target=root/'browser-actions';target.mkdir(mode=0o755,exist_ok=True)
os.chmod(target,0o755)
record={k:value[k] for k in ('ami_id','instance_id','course_ref','event_id','learner_id','gate')}
record.update(kind='doris-workshop-browser-checkpoint',passed=True,notebook=waiting['notebook'])
path=target/(value['gate']+'.json')
with path.open('x') as out: json.dump(record,out)
os.chmod(path,0o644)
print('Browser checkpoint acknowledged; independent browser evidence is still required.')
"""
    aws = pf.Aws(b['config'])
    ssm = aws.client('ssm')
    pf.require(aws.call('sts', 'get_caller_identity')['Account'] == b['config']['aws_account_id'],
               'validation: wrong checkpoint account')
    invocation = ssm.get_command_invocation(CommandId=command['command_id'], InstanceId=expected['instance_id'])
    pf.require(invocation['Status'] in {'InProgress', 'Delayed'}, 'validation: course attempt is not running')
    marker = attempt / (gate + '-command.json')
    images.write(marker, {'phase': 'dispatching', 'instance_id': expected['instance_id'], 'gate': gate})
    dispatch = send_file_command(aws, expected['instance_id'], code, {**expected, 'gate': gate})
    ws.atomic_json(marker, {'phase': 'dispatched', 'command_id': dispatch, 'instance_id': expected['instance_id'], 'gate': gate})
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            result = ssm.get_command_invocation(CommandId=dispatch, InstanceId=expected['instance_id'])
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(2)
            continue
        if result['Status'] in {'Pending', 'InProgress', 'Delayed'}:
            time.sleep(2)
            continue
        images.write(attempt / (gate + '-invocation.json'), ws.ops.invocation_summary(result))
        pf.require(result['Status'] == 'Success', 'validation: checkpoint failed; inspect the exact dispatched command before retrying')
        print('Browser action acknowledged; this does not certify browser acceptance.')
        return
    raise wc.ConfigError('validation: checkpoint outcome unknown; inspect the dispatched command before retrying')


def audit(directory):
    pf.require(not (directory/'initial-audit.json').exists(), 'validation: preserve the successful initial audit')
    attempt=directory/'audits'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    attempt.mkdir(parents=True,mode=0o700)
    b=wc.load_document(directory/'binding.json');config=b['config'];aws=pf.Aws(config)
    outputs=wc.load_document(directory/'outputs.json');instance=next(iter(outputs['instance_ids'].values()))
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        rows=aws.call('ssm','describe_instance_information',Filters=[{'Key':'InstanceIds','Values':[instance]}])['InstanceInformationList']
        if rows and rows[0]['PingStatus']=='Online':break
        time.sleep(5)
    else:raise wc.ConfigError('validation: SSM did not become available; destroy resources')
    row=aws.call('ec2','describe_instances',InstanceIds=[instance])['Reservations'][0]['Instances'][0]
    tags={t['Key']:t['Value'] for t in row['Tags']}
    pf.require(row['ImageId']==b['candidate']['ami_id'] and tags.get('Scope')=='image-validation' and
               tags.get('Event')==config['event_id'],'validation: wrong instance ownership or image')
    v=wc.load_document(directory/'inputs.tfvars.json');learner=config['learner_ids'][0]
    builder=wc.load_document(Path(b['candidate_run'])/'build-instance.json')['instance_id']
    expected={'ami_id':b['candidate']['ami_id'],'instance_id':instance,'builder_instance_ids':[builder],
        'launched_at':wc.utc_text(row['LaunchTime']),'manifest_sha256':b['candidate']['manifest_sha256'],
        'runtime_sha256':b['manifest']['runtime_sha256'],'course_ref':config['course_ref'],
        'event_id':config['event_id'],'learner_id':learner,'ports':v['browser_routes'][learner],
        'services':v.get('browser_services',{}).get(learner,{})}
    images.write(attempt/'audit-input.json',expected)
    command=send_file_command(aws,instance,(wc.SPEC/'runtime/audit_image.py').read_text(),expected)
    images.write(attempt/'audit-command.json',{'instance_id':instance,'command_id':command})
    while time.monotonic()<deadline:
        try:r=aws.client('ssm').get_command_invocation(CommandId=command,InstanceId=instance)
        except aws.client('ssm').exceptions.InvocationDoesNotExist:
            time.sleep(5);continue
        if r['Status'] in ('Pending','InProgress','Delayed'):
            time.sleep(5);continue
        images.write(attempt/'audit-invocation.json',ws.ops.invocation_summary(r))
        report=next((line[len('IMAGE_AUDIT='):] for line in r['StandardOutputContent'].splitlines() if line.startswith('IMAGE_AUDIT=')),None)
        if report:
            data=json.loads(report);images.write(attempt/'audit-report.json',data)
            pf.require(r['Status']=='Success' and data['passed'],'validation: initial image audit failed; inspect private report')
            images.write(directory/'initial-audit.json',data)
            print(json.dumps({'passed':True,'instance_id':instance,'checks':data['checks']},indent=2));return
        raise wc.ConfigError('validation: audit failed without a report; inspect private audit invocation')
    raise wc.ConfigError('validation: audit timed out; destroy resources')


def destroy(directory):
    b=wc.load_document(directory/'binding.json');config=b['config'];aws=pf.Aws(config)
    pf.require(aws.call('sts','get_caller_identity')['Account']==config['aws_account_id'],'validation: wrong cleanup account')
    pf.require(images.checksum(directory/'inputs.tfvars.json')==b['vars_sha256'],'validation: cleanup variables changed')
    state=json.loads(tf(directory,'show','-json',capture=True)).get('values',{}).get('root_module',{}).get('resources',[])
    managed=[r for r in state if r.get('mode')=='managed']
    for r in managed:
        tags=r['values'].get('tags_all') or r['values'].get('tags')
        if tags:pf.require(tags.get('Scope')=='image-validation' and tags.get('Event')==config['event_id'],'validation: unowned resource in state')
    known=[r['values']['id'] for r in managed if r['type']=='aws_instance']
    volumes=[v['volume_id'] for r in managed if r['type']=='aws_instance' for v in r['values'].get('root_block_device',[]) if v.get('volume_id')]
    if not volumes and (directory/'reconciliation.json').exists():
        volumes=wc.load_document(directory/'reconciliation.json').get('known_volumes',[])
    if not known and (directory/'outputs.json').exists():known=list(wc.load_document(directory/'outputs.json')['instance_ids'].values())
    if managed:
        path=directory/'destroy.tfplan'
        tf(directory,'plan','-destroy','-input=false','-lock-timeout=60s','-var-file='+str(directory/'inputs.tfvars.json'),'-out='+str(path))
        planned=json.loads(tf(directory,'show','-json',path,capture=True))
        addresses={r['address'] for r in managed}
        pf.require(all(r['mode']!='managed' or r['address'] in addresses and r['change']['actions'] in (['delete'],['no-op']) for r in planned.get('resource_changes',[])),
                   'validation: destroy plan contains an unexpected action')
        tf(directory,'apply','-input=false','-lock-timeout=60s',path)
    after=json.loads(tf(directory,'show','-json',capture=True)).get('values',{}).get('root_module',{}).get('resources',[])
    f=[{'Name':'tag:Event','Values':[config['event_id']]},{'Name':'tag:Scope','Values':['image-validation']}]
    instances=[i for r in aws.call('ec2','describe_instances',Filters=f)['Reservations'] for i in r['Instances']]
    if known:
        rows=aws.call('ec2','describe_instances',Filters=[{'Name':'instance-id','Values':known}])['Reservations']
        instances+= [i for r in rows for i in r['Instances'] if i['InstanceId'] not in {j['InstanceId'] for j in instances}]
    remaining_volumes={v['VolumeId'] for v in aws.call('ec2','describe_volumes',Filters=f)['Volumes']}
    if volumes:
        remaining_volumes.update(v['VolumeId'] for v in aws.call('ec2','describe_volumes',Filters=[{'Name':'volume-id','Values':volumes}])['Volumes'])
    network=sum(len(aws.call('ec2',op,Filters=f)[key]) for op,key in (
        ('describe_vpcs','Vpcs'),('describe_subnets','Subnets'),('describe_security_groups','SecurityGroups'),
        ('describe_internet_gateways','InternetGateways'),('describe_route_tables','RouteTables')))
    iam=aws.client('iam')
    role_exists=profile_exists=False
    try:iam.get_role(RoleName='doris-workshop-learner-'+config['event_id']);role_exists=True
    except iam.exceptions.NoSuchEntityException:pass
    try:iam.get_instance_profile(InstanceProfileName='doris-workshop-'+config['event_id']+'-learner');profile_exists=True
    except iam.exceptions.NoSuchEntityException:pass
    groups=[r['values']['id'] for r in managed if r['type']=='aws_security_group']
    if not groups and (directory/'outputs.json').exists():groups=[wc.load_document(directory/'outputs.json')['security_group_id']]
    enis=sum(len(aws.call('ec2','describe_network_interfaces',Filters=[{'Name':'group-id','Values':[group]}])['NetworkInterfaces']) for group in groups)
    result={'checked_at':wc.utc_text(datetime.now(timezone.utc)),'managed_resources':sum(r['mode']=='managed' for r in after),
        'known_instances':known,'known_volumes':volumes,'active_instances':sum(i['State']['Name']!='terminated' for i in instances),
        'remaining_volumes':len(remaining_volumes),
        'remaining_network_resources':network,'role_exists':role_exists,'profile_exists':profile_exists,'remaining_enis':enis}
    keys=('managed_resources','active_instances','remaining_volumes','remaining_network_resources','role_exists','profile_exists','remaining_enis')
    record=directory/'reconciliation.json'
    if record.exists() and not any(wc.load_document(record).get(k,1) for k in keys):
        images.write(directory/('cleanup-recheck-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'),result)
    else:ws.atomic_json(record,result)
    pf.require(not any(result[k] for k in keys),'validation: residual resources remain')
    print('Validation environment destroyed and EC2/EBS reconciled; candidate AMI retained.')


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    planp=s.add_parser('plan')
    for name in ('config','settings','candidate-run','run-dir','routes'):planp.add_argument('--'+name,type=Path,required=True)
    for name in ('apply','audit','course','destroy'):
        q=s.add_parser(name);q.add_argument('--run-dir',type=Path,required=True)
    browser=s.add_parser('checkpoint',help='Acknowledge an actually completed documented browser step')
    browser.add_argument('--run-dir',type=Path,required=True)
    browser.add_argument('--attempt',type=Path,required=True)
    browser.add_argument('--gate',choices=sorted(workshop_course._contract['OBS_BROWSER_GATES'].values()),required=True)
    release=s.add_parser('publish')
    for name in ('run-dir','course-report','browser-report','output'):release.add_argument('--'+name,type=Path,required=True)
    release.add_argument('--release-ref',required=True)
    a=p.parse_args();os.umask(0o077)
    try:
        if a.command=='plan':plan(a.config,a.settings,a.candidate_run.resolve(),a.run_dir,a.routes)
        elif a.command=='checkpoint':
            with ws.run_lock(a.attempt):checkpoint(a.run_dir.resolve(),a.attempt,a.gate)
        elif a.command=='publish':
            import workshop_release
            b=wc.load_document(a.run_dir/'binding.json')
            workshop_release.publish(a.run_dir,a.course_report,a.browser_report,a.release_ref,a.output,pf.Aws(b['config']))
            print('Evidence-bound image release published privately; no validation instances retained.')
        else:
            with ws.run_lock(a.run_dir):globals()[a.command](a.run_dir.resolve())
        return 0
    except Exception as error:
        print(str(error) if isinstance(error,wc.ConfigError) else 'validation: operation failed; inspect private records; resources may remain',file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
