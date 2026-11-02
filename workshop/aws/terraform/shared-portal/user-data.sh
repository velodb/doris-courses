#!/usr/bin/env bash
set -euo pipefail
umask 077
exec >/var/log/doris-portal-bootstrap.log 2>&1
# The dedicated portal group allows outbound HTTPS, including signed APT repos.
python3 - <<'PY'
from pathlib import Path
import re
paths = [Path('/etc/apt/sources.list'), *Path('/etc/apt/sources.list.d').glob('*.sources')]
for path in paths:
    if path.is_file():
        value = re.sub(r'http://(?:[a-z0-9.-]+\.)?archive\.ubuntu\.com/ubuntu/?', 'https://archive.ubuntu.com/ubuntu/', path.read_text())
        value = value.replace('http://security.ubuntu.com/ubuntu', 'https://security.ubuntu.com/ubuntu')
        path.write_text(value)
PY
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3.12-venv ca-certificates curl
install -d -m 0700 /var/lib/doris-workshop-portal-install
touch /var/lib/doris-workshop-portal-install/bootstrap-ready
