"""Audit a freshly launched image before any course Lab is executed. Prints no secrets."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import urllib.request


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    expected = json.loads(Path(sys.argv[1]).read_text())
    config = Path('/etc/doris-workshop')
    manifest = json.loads((config / 'image-manifest.json').read_text())
    runtime = json.loads((config / 'runtime.json').read_text())
    activity = json.loads((config / 'activity.json').read_text())
    receipt = json.loads(Path('/var/lib/doris-workshop/image-start.json').read_text())
    checks = {}
    checks['sealed-marker'] = (config / 'image-sealed').is_file()
    checks['manifest'] = digest(config / 'image-manifest.json') == expected['manifest_sha256']
    checks['runtime'] = digest(config / 'runtime.json') == manifest['runtime_sha256'] == expected['runtime_sha256']
    checks['course'] = runtime['course_ref'] == manifest['course_ref'] == expected['course_ref']
    checks['activity-injected'] = (receipt['source_had_no_activity'] and receipt['activity'] == activity and
                                   activity['event_id'] == expected['event_id'] and activity['learner_id'] == expected['learner_id'])
    env = Path('/etc/systemd/system/doris-workshop-jupyter.service.d/activity.conf').read_text()
    checks['browser-identity'] = all(k + '=' + v in env for k, v in {
        'DORIS_WORKSHOP_EVENT_ID': expected['event_id'], 'DORIS_WORKSHOP_LEARNER_ID': expected['learner_id']}.items())
    routes = json.loads((config / 'browser-urls.json').read_text())
    checks['browser-routes'] = (routes['event_id'] == expected['event_id'] and routes['learner_id'] == expected['learner_id'] and
                                routes['ports'] == expected['ports'] and routes.get('services', {}) == expected.get('services', {}))
    if 'obs-main' in manifest['profiles']:
        public = {'obs-grafana': 'DORIS_OBS_GRAFANA_PUBLIC_URL', 'obs-demo': 'DORIS_OBS_DEMO_PUBLIC_URL',
                  'obs-litefuse': 'DORIS_OBS_LITEFUSE_PUBLIC_URL'}
        environment = dict(line.split('=', 1) for line in (config / 'browser.env').read_text().splitlines())
        checks['obs-public-application-origins'] = all(
            environment.get(key) == expected.get('services', {}).get(service) and environment.get(key)
            for service, key in public.items())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('http://169.254.169.254/latest/api/token', method='PUT', headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'})
    with opener.open(request,timeout=5) as response: token = response.read().decode()
    request = urllib.request.Request('http://169.254.169.254/latest/meta-data/instance-id',headers={'X-aws-ec2-metadata-token':token})
    with opener.open(request,timeout=5) as response: instance = response.read().decode()
    checks['new-instance'] = instance == expected['instance_id'] and instance not in expected['builder_instance_ids']
    checks['cloud-init-identity'] = Path('/var/lib/cloud/data/instance-id').read_text().strip() == instance
    machine_id = Path('/etc/machine-id').read_text().strip()
    checks['machine-id'] = len(machine_id) == 32 and all(c in '0123456789abcdef' for c in machine_id)
    old_refs = [p for i in expected['builder_instance_ids'] for p in Path('/var/lib/amazon/ssm').glob('**/' + i)]
    checks['no-build-ssm-state'] = not old_refs
    checks['no-build-expiry'] = not Path('/etc/systemd/system/doris-image-expiry.timer').exists()
    checks['cloud-init-finished'] = subprocess.run(['cloud-init','status','--wait'],capture_output=True,timeout=120).returncode == 0
    checks['no-credentials'] = all(not (Path(home)/name).exists() for home in ('/root','/home/ubuntu','/var/lib/doris-workshop/home') for name in ('.aws','.docker/config.json','course_secrets.env'))
    ssh = list(Path('/etc/ssh').glob('ssh_host_*_key.pub'))
    launched = datetime.fromisoformat(expected['launched_at']).timestamp()
    checks['new-ssh-keys'] = bool(ssh) and all(p.stat().st_mtime >= launched-60 for p in ssh)
    server_token = Path('/run/doris-workshop/token')
    checks['no-containers'] = not call('docker','ps','-aq')
    checks['no-volumes'] = not call('docker','volume','ls','-q')
    workspace = Path('/var/lib/doris-workshop/workspace')
    notebooks = []
    for course in runtime['courses']:
        for name in course['notebooks']:
            nb = json.loads((workspace/course['course_dir']/name).read_text())
            notebooks.append(nb)
    checks['pristine-notebooks'] = all(not cell.get('outputs') and cell.get('execution_count') is None for nb in notebooks for cell in nb['cells'] if cell['cell_type']=='code')
    checks['python-version'] = call('/usr/bin/python3.12','--version') == manifest['python']
    checks['installed-freezes'] = all(digest(Path('/var/lib/doris-workshop/installed-packages')/(name+'.txt')) == sha for name,sha in manifest['freeze_sha256'].items())
    actual_freezes = {}
    for name in manifest['freeze_sha256']:
        venv = (Path('/opt/doris-workshop/server-venv') if name=='server' else
                Path('/opt/doris-courses') / next(c['course_dir'] for c in runtime['courses'] if c['kernel']==name) / '.venv')
        freeze = subprocess.check_output([str(venv/'bin/python'),'-m','pip','freeze'])
        actual_freezes[name] = hashlib.sha256(freeze).hexdigest()
    checks['actual-freezes'] = actual_freezes == manifest['freeze_sha256']
    system = call('dpkg-query','-W','-f=${Package}=${Version}\n')+'\n'
    checks['system-packages'] = hashlib.sha256(system.encode()).hexdigest() == manifest['system_packages_sha256']
    checks['docker-packages'] = digest('/var/lib/doris-workshop/installed-packages/docker.txt') == manifest['docker_packages_sha256']
    checks['cached-images'] = all(json.loads(call('docker','image','inspect',i['tag']))[0]['Id'] == i['id'] for i in manifest['images'])
    if manifest.get('custom_builds'):
        checks['custom-image-inventories'] = all(digest(Path('/var/lib/doris-workshop/installed-packages') / b['inventory_file']) == b['inventory_sha256'] for b in manifest['custom_builds'])
    device = call('findmnt','-n','-o','SOURCE','-T','/')
    checks['cache-on-root'] = all(call('findmnt','-n','-o','SOURCE','-T',p)==device for p in ('/var/lib/docker','/var/lib/containerd'))
    probe = subprocess.run(['runuser','--user','workshop','--','env','-i','HOME=/var/lib/doris-workshop/home',
      'PATH=/opt/doris-workshop/server-venv/bin:/usr/local/bin:/usr/bin:/bin','LANG=C.UTF-8','PYTHONNOUSERSITE=1',
      '/opt/doris-workshop/server-venv/bin/python','-I','/usr/local/lib/doris-workshop/check.py','--wait','120'],capture_output=True,text=True,timeout=300)
    checks['runtime-ready'] = probe.returncode == 0
    checks['fresh-jupyter-token'] = server_token.is_file() and server_token.stat().st_mtime >= launched-60 and server_token.stat().st_mode & 0o777 == 0o600
    report = {'schema_version':1,'kind':'doris-workshop-image-audit','checked_at':datetime.now(timezone.utc).isoformat(),
      'image_id':expected['ami_id'],'instance_id':instance,'event_id':activity['event_id'],'learner_id':activity['learner_id'],
      'course_ref':runtime['course_ref'],'image_manifest_sha256':digest(config/'image-manifest.json'),
      'machine_id':machine_id,'token_sha256':digest(server_token) if server_token.is_file() else None,'notebook_count':len(notebooks),
      'checks':checks,'passed':all(checks.values()),'runtime_probe': json.loads(probe.stdout) if probe.returncode==0 else {'error':'readiness failed'}}
    Path('/var/lib/doris-workshop/initial-image-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print('IMAGE_AUDIT='+json.dumps(report,sort_keys=True))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    sys.exit(main())
