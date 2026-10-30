"""Immutable, evidence-bound private-image release metadata and live checks."""
import json
from pathlib import Path

import workshop_config as wc

BROWSER_CHECKS = {'reader-images', 'notebook-create-run-save-download', 'kernel-restart-reconnect',
                  'quiz-navigation-feedback', 'terminal-docker', 'fe-profile'}


def require(condition,message):
    if not condition:raise wc.ConfigError(message)


def load_release(path,config=None):
    value=wc.load_document(path);wc.schema_check(value,'image-release.schema.json')
    if config:
        image=config['image']
        require(image['mode']=='prebuilt' and value['status']=='validated' and
          value['aws_account_id']==image['owner_account_id']==config['aws_account_id'] and
          value['ami_id']==image['ami_id'] and value['region']==image['region']==config['region'] and
          value['architecture']==image['architecture']=='x86_64' and value['course_ref']==config['course_ref']==image['course_ref'] and
          set(value['course_profiles'])==set(config['course_profiles'])==set(image['course_profiles']) and
          value['release_ref']==image['release_ref'], 'image: release does not match the selected account/region/version/profiles')
        require(config['disk_gib']>=value['root_gib'],'image: disk is smaller than the released root snapshot')
    return value


def verify_live(aws,config,release):
    rows=aws.call('ec2','describe_images',Owners=[config['aws_account_id']],ImageIds=[release['ami_id']])['Images']
    require(len(rows)==1,'image: released AMI is unavailable')
    image=rows[0];tags={t['Key']:t['Value'] for t in image.get('Tags',[])}
    require(image['OwnerId']==config['aws_account_id'] and image['State']=='available' and not image['Public'] and
      image['Architecture']=='x86_64' and image['RootDeviceType']=='ebs' and image['VirtualizationType']=='hvm' and
      not image.get('ProductCodes'),'image: released AMI owner/state/architecture/privacy mismatch')
    require(tags.get('ImageStatus')=='validated' and tags.get('ReleaseRef')==release['release_ref'] and
      tags.get('ReleaseSHA256')==wc.canonical_hash(release) and tags.get('ManifestSHA256')==release['image_manifest_sha256'] and
      tags.get('CourseRef')==release['course_ref'],'image: AMI is unpublished, retired or has different version tags')
    ebs=[m['Ebs'] for m in image['BlockDeviceMappings'] if 'Ebs' in m]
    require(len(ebs)==1 and ebs[0]['SnapshotId'] in release['snapshot_ids'] and ebs[0]['VolumeSize']==release['root_gib'] and
      ebs[0]['Encrypted'] and ebs[0]['DeleteOnTermination'],'image: released EBS mapping mismatch')
    permissions=aws.call('ec2','describe_image_attribute',ImageId=release['ami_id'],Attribute='launchPermission')
    require(not permissions.get('LaunchPermissions'),'image: published workshop AMI must remain private')
    return image


def publish(validation_run,course_path,browser_path,release_ref,output,aws):
    import workshop_image as wi
    directory=Path(validation_run);binding=wc.load_document(directory/'binding.json')
    config=binding['config'];candidate=binding['candidate'];manifest=binding['manifest']
    audit=wc.load_document(directory/'initial-audit.json');course=wc.load_document(course_path);browser=wc.load_document(browser_path)
    cleanup=wc.load_document(directory/'reconciliation.json')
    wi.record_candidate(Path(binding['candidate_run']),aws)
    instance=audit['instance_id']
    require(audit['passed'] is True and all(v is True for v in audit['checks'].values()) and audit['image_id']==candidate['ami_id'] and
        audit['course_ref']==config['course_ref'] and audit['image_manifest_sha256']==candidate['manifest_sha256'],
        'release: fresh-image audit failed or belongs to another image')
    require(course['kind']=='doris-workshop-course-validation' and course['done'] is True and course['passed'] is True and
        course['image_id']==candidate['ami_id'] and course['instance_id']==instance and course['course_ref']==config['course_ref'] and
        course['notebook_count']==20 and len(course['modules'])==10 and course['code_cells']>=189 and all(m['passed'] is True for m in course['modules']),
        'release: the complete selected course must pass on the audited instance')
    require(browser['kind']=='doris-workshop-browser-validation' and browser['passed'] is True and browser['instance_id']==instance and
        browser['image_id']==candidate['ami_id'] and browser['course_ref']==config['course_ref'] and
        BROWSER_CHECKS <= browser['checks'].keys() and all(v is True for v in browser['checks'].values()),
        'release: real browser validation must pass on the audited instance')
    require(instance in cleanup['known_instances'] and not any(cleanup[k] for k in
        ('managed_resources','active_instances','remaining_volumes','remaining_network_resources','role_exists','profile_exists','remaining_enis')),
        'release: validation resources must be destroyed and reconciled before publication')
    value={'schema_version':1,'kind':'doris-workshop-image-release','status':'validated','release_ref':release_ref,
        'aws_account_id':config['aws_account_id'],'region':config['region'],'ami_id':candidate['ami_id'],'architecture':'x86_64',
        'course_ref':config['course_ref'],'deployment_ref':manifest['deployment_ref'],'course_profiles':manifest['profiles'],
        'runtime_sha256':manifest['runtime_sha256'],'image_manifest_sha256':candidate['manifest_sha256'],
        'root_gib':manifest['root_volume']['size_gib'],'snapshot_ids':candidate['snapshots'],
        'validation':{'instance_id':instance,'checked_at':cleanup['checked_at'],'initial_audit_sha256':wi.checksum(directory/'initial-audit.json'),
         'course_report_sha256':wi.checksum(course_path),'browser_report_sha256':wi.checksum(browser_path),'cleanup_sha256':wi.checksum(directory/'reconciliation.json')}}
    wc.schema_check(value,'image-release.schema.json')
    old=aws.call('ec2','describe_images',ImageIds=[candidate['ami_id']],Owners=[config['aws_account_id']])['Images'][0]
    tags={t['Key']:t['Value'] for t in old.get('Tags',[])}
    require(tags.get('ImageStatus')=='candidate' or tags.get('ImageStatus')=='validated' and
        tags.get('ReleaseRef')==release_ref and tags.get('ReleaseSHA256')==wc.canonical_hash(value),
        'release: never overwrite a different published image version')
    output=Path(output)
    if output.exists():require(wc.load_document(output)==value,'release: preserve conflicting release records')
    else:wi.write(output,value)
    aws.client('ec2').create_tags(Resources=[candidate['ami_id']],Tags=[{'Key':k,'Value':v} for k,v in {
        'ImageStatus':'validated','ReleaseRef':release_ref,'ReleaseSHA256':wc.canonical_hash(value),
        'ManifestSHA256':value['image_manifest_sha256']}.items()])
    return value
