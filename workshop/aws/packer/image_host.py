"""Prepare caches and a compact audit manifest on a dedicated Packer host."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    bundle = Path(sys.argv[1]).resolve()
    spec = json.loads((bundle / 'image-input.json').read_text())
    runtime = json.loads((bundle / 'runtime.json').read_text())
    assert digest(bundle / 'runtime.json') == spec['runtime_sha256']
    for name, expected in spec['payload_sha256'].items():
        assert digest(bundle / name) == expected
    # Both Docker's image data and containerd's content store stay on the one
    # encrypted root volume. There is no disposable second cache disk to omit.
    assert call('docker', 'info', '--format', '{{.DockerRootDir}}') == '/var/lib/docker'
    root_device = call('findmnt', '-n', '-o', 'SOURCE', '-T', '/')
    for cache in ('/var/lib/docker', '/var/lib/containerd'):
        assert call('findmnt', '-n', '-o', 'SOURCE', '-T', cache) == root_device
    assert not call('docker', 'ps', '-aq')
    assert not call('docker', 'volume', 'ls', '-q')
    images = []
    for image in spec['images']['images']:
        repository = image['tag'].rsplit(':', 1)[0]
        pinned = repository + '@' + image['digest']
        subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', pinned], check=True, stdout=sys.stderr)
        subprocess.run(['docker', 'tag', pinned, image['tag']], check=True)
        info = json.loads(call('docker', 'image', 'inspect', image['tag']))[0]
        assert pinned in info['RepoDigests'] and info['Architecture'] == 'amd64'
        images.append({**image, 'id': info['Id']})
    # Do not run course Labs here: the image must have no completed exercises.
    freezes = {}
    for name in runtime['python_locks']:
        venv = Path('/opt/doris-workshop/server-venv') if name == 'server' else Path('/opt/doris-courses/doris-course/01-real-time-analytics/.venv')
        subprocess.run([str(venv / 'bin/python'), '-m', 'pip', 'check'], check=True, stdout=sys.stderr)
        freezes[name] = digest(Path('/var/lib/doris-workshop/installed-packages') / (name + '.txt'))
    manifest = {
        'schema_version': 1, 'kind': 'doris-workshop-private-image', 'status': 'candidate',
        'image_input_sha256': digest(bundle / 'image-input.json'),
        'course_ref': spec['course_ref'], 'deployment_ref': spec['deployment_ref'],
        'deployment_dirty': spec['deployment_dirty'], 'runtime_sha256': spec['runtime_sha256'],
        'base_ami': spec['base_ami'], 'profiles': spec['images']['profiles'],
        'python': call('/usr/bin/python3.12', '--version'),
        'python_locks': {n: runtime['sha256'][p] for n, p in runtime['python_locks'].items()},
        'freeze_sha256': freezes, 'docker_packages_sha256': digest('/var/lib/doris-workshop/installed-packages/docker.txt'),
        'images': images, 'root_volume': {'device': '/dev/sda1', 'size_gib': 200, 'encrypted': True,
                                       'cache_paths': ['/var/lib/docker', '/var/lib/containerd']},
        'online_dependencies': spec['online_dependencies'],
        'validation': 'Build-time runtime checks only. Independent launch and course/browser certification are required before release.'
    }
    installed = Path('/var/lib/doris-workshop/installed-packages/system.txt')
    installed.write_text(call('dpkg-query', '-W', '-f=${Package}=${Version}\n') + '\n')
    manifest['system_packages_sha256'] = digest(installed)
    target = Path('/etc/doris-workshop/image-manifest.json')
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    target.chmod(0o644)
    Path('/etc/doris-workshop/image-input.json').write_bytes((bundle / 'image-input.json').read_bytes())
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
