"""Inject one activity into a sealed image without installing packages or resetting Labs."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

PUBLIC_URL_ENV = {
    'rta-metabase': 'DORIS_RTA_METABASE_PUBLIC_URL', 'dw-superset': 'DW_BI_PUBLIC_URL',
    'obs-grafana': 'DORIS_OBS_GRAFANA_PUBLIC_URL', 'obs-demo': 'DORIS_OBS_DEMO_PUBLIC_URL',
    'obs-litefuse': 'DORIS_OBS_LITEFUSE_PUBLIC_URL',
}


def browser_environment(routes):
    return ''.join(key + '=' + routes['services'][service] + '\n'
                   for service, key in PUBLIC_URL_ENV.items() if service in routes.get('services', {}))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def initialize(activity, manifest_sha, runtime_sha, course_ref, *, root=Path('/'), check=False):
    root = Path(root)
    config = root / 'etc/doris-workshop'
    state = root / 'var/lib/doris-workshop'
    require((config / 'image-sealed').is_file(), 'image: missing sealing marker')
    require(digest(config / 'image-manifest.json') == manifest_sha, 'image: manifest checksum mismatch')
    require(digest(config / 'runtime.json') == runtime_sha, 'image: runtime checksum mismatch')
    manifest = json.loads((config / 'image-manifest.json').read_text())
    runtime = json.loads((config / 'runtime.json').read_text())
    require(manifest['course_ref'] == runtime['course_ref'] == course_ref, 'image: course revision mismatch')
    require(manifest['runtime_sha256'] == runtime_sha, 'image: unexpected runtime revision')
    require(activity.get('schema_version') == 1 and
            all(re.fullmatch(r'[a-z][a-z0-9-]{0,31}', activity.get(k, '')) for k in ('event_id', 'learner_id', 'release_ref')),
            'activity: invalid identity')
    module_path = root / 'opt/doris-courses/doris-course/common/doris_labkit/browser.py'
    spec = importlib.util.spec_from_file_location('image_browser', module_path)
    browser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browser)
    routes = {'schema_version': 1, 'event_id': activity['event_id'], 'learner_id': activity['learner_id'],
              'ports': activity.get('ports', {})}
    if activity.get('services'):
        routes['services'] = activity['services']
    browser.validate_routes(routes, activity['event_id'], activity['learner_id'])
    receipt_path = state / 'image-start.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        require(receipt['activity'] == activity and receipt['image_manifest_sha256'] == manifest_sha,
                'activity: an initialized instance cannot change ownership or image')
    else:
        require(not check, 'activity: first initialization has not completed')
        require(not (config / 'activity.json').exists() and not (config / 'browser-urls.json').exists(),
                'image: activity identity was baked into the image')
        receipt = {'schema_version': 1, 'kind': 'doris-workshop-image-start', 'activity': activity,
                   'course_ref': course_ref, 'image_manifest_sha256': manifest_sha, 'runtime_sha256': runtime_sha,
                   'source_had_no_activity': True, 'initialized_at': datetime.now(timezone.utc).isoformat()}
    if check:
        require(json.loads((config / 'activity.json').read_text()) == activity, 'activity: metadata changed')
        browser.validate_routes(json.loads((config / 'browser-urls.json').read_text()), activity['event_id'], activity['learner_id'])
        require((config / 'browser.env').read_text() == browser_environment(routes), 'activity: browser environment changed')
        return receipt
    config.mkdir(parents=True, exist_ok=True)
    for name, data in [('activity.json', activity), ('browser-urls.json', routes)]:
        path = config / name
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
        path.chmod(0o644)
    environment = config / 'browser.env'
    environment.write_text(browser_environment(routes))
    environment.chmod(0o644)
    dropin = root / 'etc/systemd/system/doris-workshop-jupyter.service.d'
    dropin.mkdir(parents=True, exist_ok=True)
    (dropin / 'activity.conf').write_text('[Service]\n' + '\n'.join([
        'Environment=DORIS_WORKSHOP_EVENT_ID=' + activity['event_id'],
        'Environment=DORIS_WORKSHOP_LEARNER_ID=' + activity['learner_id'],
        'EnvironmentFile=/etc/doris-workshop/browser.env',
        'Environment=DORIS_BROWSER_ROUTES_FILE=/etc/doris-workshop/browser-urls.json']) + '\n')
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    receipt_path.chmod(0o600)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--activity', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--runtime-sha256', required=True)
    p.add_argument('--course-ref', required=True)
    p.add_argument('--check', action='store_true')
    a = p.parse_args()
    try:
        initialize(json.loads(a.activity.read_text()), a.manifest_sha256, a.runtime_sha256, a.course_ref, check=a.check)
        print('Image identity, versions and activity initialization verified.')
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print('Image initialization failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
