#!/usr/bin/env bash
# Run through operator SSM on an already verified host, after uploading this overlay.
set -euo pipefail
test "$(id -u)" -eq 0
test "$#" -eq 2
bundle_dir=$(realpath "$1")
gateway_config=$(realpath "$2")
test -f /etc/doris-workshop/activity.json
python3.12 - "$bundle_dir" "$gateway_config" <<'PY'
import hashlib, json, pathlib, sys
bundle, config_path = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((bundle/'overlay.json').read_text())
expected = {'gateway.py', 'jupyter_gateway.py', 'requirements.txt', 'doris-workshop-gateway.service', 'install_gateway.sh'}
assert manifest['schema_version'] == 1 and set(manifest['sha256']) == expected
for name, checksum in manifest['sha256'].items():
    assert not (bundle/name).is_symlink()
    assert hashlib.sha256((bundle/name).read_bytes()).hexdigest() == checksum
config = json.loads(config_path.read_text())
activity = json.loads(pathlib.Path('/etc/doris-workshop/activity.json').read_text())
assert all(config[k] == activity[k] for k in ('event_id', 'learner_id', 'expires_at'))
PY
python3.12 -m venv /opt/doris-workshop/gateway-venv
/opt/doris-workshop/gateway-venv/bin/python -m pip --isolated install --disable-pip-version-check --index-url https://pypi.org/simple -r "$bundle_dir/requirements.txt"
/opt/doris-workshop/gateway-venv/bin/python - "$bundle_dir" "$gateway_config" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from gateway import validate_config
validate_config(json.loads(pathlib.Path(sys.argv[2]).read_text()))
PY
install -d -m 0755 /opt/doris-workshop/access
for overlay_file in gateway.py jupyter_gateway.py requirements.txt overlay.json; do
  install -m 0644 "$bundle_dir/$overlay_file" "/opt/doris-workshop/access/$overlay_file"
done
install -m 0644 "$gateway_config" /etc/doris-workshop/gateway.json
install -m 0644 "$bundle_dir/doris-workshop-gateway.service" /etc/systemd/system/doris-workshop-gateway.service
install -d -m 0755 /etc/systemd/system/doris-workshop-jupyter.service.d
cat > /etc/systemd/system/doris-workshop-jupyter.service.d/gateway.conf <<'UNIT'
[Service]
ExecStart=
ExecStart=/opt/doris-workshop/server-venv/bin/python -I /opt/doris-workshop/access/jupyter_gateway.py
UNIT
systemctl daemon-reload
systemctl restart doris-workshop-jupyter.service
systemctl enable --now doris-workshop-gateway.service
systemctl restart doris-workshop-gateway.service
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8080/healthz
# Jupyter readiness, course execution and formal-domain browser tests follow.
