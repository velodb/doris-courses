#!/usr/bin/env python3
"""Plan and audit an isolated fresh launch of a private candidate image."""
import argparse
from datetime import datetime, timezone
import base64
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

MODULE = wc.SPEC / 'terraform/image-validation'


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
    pf.require(not directory.exists(), 'validation: choose a new run directory')
    directory.mkdir(parents=True, mode=0o700)
    directory=directory.resolve()
    variables = {k:config[k] for k in ('aws_profile','region','owner_id','event_id','learner_ids','instance_type','disk_gib','expires_at','course_ref')}
    variables.update(expected_account_id=config['aws_account_id'],state_bucket_name=settings['state_bucket_name'],
        availability_zone=report['availability_zones'][0],ami_id=c['ami_id'],release_ref='candidate-validation',
        image_manifest_sha256=c['manifest_sha256'],runtime_sha256=m['runtime_sha256'],browser_routes=routes)
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


def send_file_command(aws, instance, code, expected):
    # Keep each SSM StringList item short; only reviewed code and public metadata
    # are encoded. No operator credentials or browser tokens are transmitted.
    encoded=base64.b64encode(code.encode()).decode()
    commands=['set -eu','umask 077','printf "" > /run/workshop-audit.b64']
    commands += ["printf '%s' '"+encoded[i:i+1000]+"' >> /run/workshop-audit.b64" for i in range(0,len(encoded),1000)]
    commands += ['base64 -d /run/workshop-audit.b64 > /run/workshop-audit.py',
       "printf '%s' '"+base64.b64encode(json.dumps(expected).encode()).decode()+"' | base64 -d > /run/workshop-audit-input.json",
       'cloud-init status --wait >/dev/null', 'python3.12 /run/workshop-audit.py /run/workshop-audit-input.json']
    return aws.client('ssm').send_command(InstanceIds=[instance],DocumentName='AWS-RunShellScript',
         Parameters={'commands':commands,'executionTimeout':['600']},TimeoutSeconds=600)['Command']['CommandId']


def audit(directory):
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
        'event_id':config['event_id'],'learner_id':learner,'ports':v['browser_routes'][learner]}
    images.write(directory/'audit-input.json',expected)
    command=send_file_command(aws,instance,(wc.SPEC/'runtime/audit_image.py').read_text(),expected)
    images.write(directory/'audit-command.json',{'instance_id':instance,'command_id':command})
    while time.monotonic()<deadline:
        try:r=aws.client('ssm').get_command_invocation(CommandId=command,InstanceId=instance)
        except aws.client('ssm').exceptions.InvocationDoesNotExist:
            time.sleep(5);continue
        if r['Status'] in ('Pending','InProgress','Delayed'):
            time.sleep(5);continue
        images.write(directory/'audit-invocation.json',r)
        report=next((line[len('IMAGE_AUDIT='):] for line in r['StandardOutputContent'].splitlines() if line.startswith('IMAGE_AUDIT=')),None)
        if report:
            data=json.loads(report);images.write(directory/'initial-audit.json',data)
            pf.require(r['Status']=='Success' and data['passed'],'validation: initial image audit failed; inspect private report')
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
    result={'checked_at':wc.utc_text(datetime.now(timezone.utc)),'managed_resources':sum(r['mode']=='managed' for r in after),
        'known_instances':known,'active_instances':sum(i['State']['Name']!='terminated' for i in instances),
        'remaining_volumes':len(aws.call('ec2','describe_volumes',Filters=f)['Volumes'])}
    ws.atomic_json(directory/'reconciliation.json',result)
    pf.require(not any(result[k] for k in ('managed_resources','active_instances','remaining_volumes')),'validation: residual resources remain')
    print('Validation environment destroyed and EC2/EBS reconciled; candidate AMI retained.')


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    planp=s.add_parser('plan')
    for name in ('config','settings','candidate-run','run-dir','routes'):planp.add_argument('--'+name,type=Path,required=True)
    for name in ('apply','audit','destroy'):
        q=s.add_parser(name);q.add_argument('--run-dir',type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    try:
        if a.command=='plan':plan(a.config,a.settings,a.candidate_run.resolve(),a.run_dir,a.routes)
        else:
            with ws.run_lock(a.run_dir):globals()[a.command](a.run_dir.resolve())
        return 0
    except Exception as error:
        print(str(error) if isinstance(error,wc.ConfigError) else 'validation: operation failed; inspect private records; resources may remain',file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
