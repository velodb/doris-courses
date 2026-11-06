#!/usr/bin/env bash
# Run through operator SSM on an already verified host, after uploading this overlay.
set -euo pipefail
test "$(id -u)" -eq 0
umask 077
test "$#" -eq 4
bundle_dir=$(realpath "$1")
gateway_config=$(realpath "$2")
browser_routes=$(realpath "$3")
browser_environment=$(realpath "$4")
test -f /etc/doris-workshop/activity.json
python3.12 - "$bundle_dir" "$gateway_config" "$browser_routes" "$browser_environment" <<'PY'
import hashlib, json, pathlib, sys
import re
bundle, config_path, browser_path, environment_path = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((bundle/'overlay.json').read_text())
expected = {'gateway.py', 'jupyter_gateway.py', 'requirements.txt', 'doris-workshop-gateway.service', 'install_gateway.sh', 'operations.py', 'service_guidance.py', 'session_recovery.js', 'session_recovery.css'}
assert manifest['schema_version'] == 1 and set(manifest['sha256']) == expected
for name, checksum in manifest['sha256'].items():
    assert not (bundle/name).is_symlink()
    assert hashlib.sha256((bundle/name).read_bytes()).hexdigest() == checksum
config = json.loads(config_path.read_text())
activity = json.loads(pathlib.Path('/etc/doris-workshop/activity.json').read_text())
assert all(config[k] == activity[k] for k in ('event_id', 'learner_id', 'expires_at'))
routes = json.loads(browser_path.read_text())
assert all(routes[k] == config[k] for k in ('event_id', 'learner_id'))
assert routes['ports'] == {str(s['port']): 'https://'+host for host, s in config['services'].items()}
assert set(routes['services'].values()) == {'https://'+host for host in config['services']}
for line in environment_path.read_text().splitlines():
    key, value = line.split('=', 1)
    assert re.fullmatch(r'(?:DORIS_|DW_)[A-Z0-9_]+', key)
    assert re.fullmatch(r'[a-zA-Z0-9:/._-]+', value)
PY
install -d -m 0700 /var/log/doris-workshop
exec > >(python3.12 "$bundle_dir/operations.py" filter --log /var/log/doris-workshop/gateway-install.log) 2>&1
python3.12 -m venv /opt/doris-workshop/gateway-venv
/opt/doris-workshop/gateway-venv/bin/python -m pip --isolated install --disable-pip-version-check --index-url https://pypi.org/simple -r "$bundle_dir/requirements.txt"
chmod -R a+rX /opt/doris-workshop/gateway-venv
/opt/doris-workshop/gateway-venv/bin/python - "$bundle_dir" "$gateway_config" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from gateway import validate_config
validate_config(json.loads(pathlib.Path(sys.argv[2]).read_text()))
PY
install -d -m 0755 /opt/doris-workshop/access
for overlay_file in gateway.py jupyter_gateway.py requirements.txt overlay.json service_guidance.py session_recovery.js session_recovery.css; do
  install -m 0644 "$bundle_dir/$overlay_file" "/opt/doris-workshop/access/$overlay_file"
done
/opt/doris-workshop/gateway-venv/bin/python -m pip freeze > /opt/doris-workshop/access/installed-packages.txt
install -m 0644 "$gateway_config" /etc/doris-workshop/gateway.json
install -m 0644 "$browser_routes" /etc/doris-workshop/browser-urls.json
install -m 0644 "$browser_environment" /etc/doris-workshop/browser.env
install -m 0644 "$bundle_dir/doris-workshop-gateway.service" /etc/systemd/system/doris-workshop-gateway.service
install -d -m 0755 /etc/systemd/system/doris-workshop-jupyter.service.d
cat > /etc/systemd/system/doris-workshop-jupyter.service.d/gateway.conf <<'UNIT'
[Service]
EnvironmentFile=/etc/doris-workshop/browser.env
ExecStart=
ExecStart=/opt/doris-workshop/server-venv/bin/python -I /opt/doris-workshop/access/jupyter_gateway.py
UNIT
systemctl daemon-reload
systemctl restart doris-workshop-jupyter.service
systemctl enable --now doris-workshop-gateway.service
systemctl restart doris-workshop-gateway.service
curl --fail --silent --show-error --max-time 5 --retry 10 --retry-connrefused --retry-delay 1 --retry-max-time 30 http://127.0.0.1:8080/healthz
# Jupyter readiness, course execution and formal-domain browser tests follow.
