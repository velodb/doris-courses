#!/usr/bin/env bash
# Operator SSM only, on a separate Ubuntu 24.04 portal host with python3.12-venv.
set -euo pipefail
test "$(id -u)" -eq 0
test "$#" -eq 2
umask 077
bundle_dir=$(realpath "$1")
portal_config=$(realpath "$2")
# A learner runtime or Docker socket is never a portal installation target.
test ! -e /etc/doris-workshop/activity.json
test ! -S /var/run/docker.sock
exec >/dev/null 2>&1
python3.12 - "$bundle_dir" <<'PY'
import hashlib, json, pathlib, sys
bundle = pathlib.Path(sys.argv[1])
manifest = json.loads((bundle/'overlay.json').read_text())
files = {'portal.py', 'gateway.py', 'requirements.txt', 'install_portal.sh', 'doris-workshop-portal.service'}
assert manifest['schema_version'] == 1 and set(manifest['sha256']) == files
for name, checksum in manifest['sha256'].items():
    assert not (bundle/name).is_symlink()
    assert hashlib.sha256((bundle/name).read_bytes()).hexdigest() == checksum
PY
id doris-portal >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin doris-portal
install -d -m 0755 /opt/doris-workshop-portal
python3.12 -m venv /opt/doris-workshop-portal/venv
/opt/doris-workshop-portal/venv/bin/python -m pip --isolated install --disable-pip-version-check --index-url https://pypi.org/simple -r "$bundle_dir/requirements.txt"
/opt/doris-workshop-portal/venv/bin/python - "$bundle_dir" "$portal_config" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from portal import validate_config
validate_config(json.loads(pathlib.Path(sys.argv[2]).read_text()))
PY
for portal_file in portal.py gateway.py requirements.txt overlay.json; do
  install -m 0644 "$bundle_dir/$portal_file" "/opt/doris-workshop-portal/$portal_file"
done
install -d -m 0750 -g doris-portal /etc/doris-workshop-portal
install -m 0640 -g doris-portal "$portal_config" /etc/doris-workshop-portal/portal.json
install -m 0644 "$bundle_dir/doris-workshop-portal.service" /etc/systemd/system/doris-workshop-portal.service
systemctl daemon-reload
systemctl enable --now doris-workshop-portal.service
systemctl restart doris-workshop-portal.service
curl --fail --silent --show-error --retry 5 --retry-connrefused --max-time 5 http://127.0.0.1:8080/healthz
