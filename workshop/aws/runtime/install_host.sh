#!/usr/bin/env bash
# Run only on a dedicated Ubuntu 24.04 x86_64 host with a reviewed runtime bundle.
set -Eeuo pipefail
umask 077

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    echo 'host: Ubuntu 24.04 x86_64 is required' >&2
    exit 2
fi
if [[ $EUID -ne 0 ]]; then
    echo 'host: run as root on the target workshop instance' >&2
    exit 2
fi
# shellcheck source=/dev/null
source /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then
    echo 'host: Ubuntu 24.04 is required' >&2
    exit 2
fi
bundle=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec 9>/run/lock/doris-workshop-install.lock
flock -n 9 || { echo 'host: another bootstrap is running' >&2; exit 2; }
install -d -m 0700 /var/log/doris-workshop
exec > >(python3 "$bundle/operations.py" filter --log /var/log/doris-workshop/bootstrap.log) 2>&1
python3 "$bundle/host.py" verify --bundle "$bundle"
python3 "$bundle/operations.py" configure
stage=preflight
trap 'echo "bootstrap failed at stage $stage, line $LINENO; see /var/log/doris-workshop/bootstrap.log" >&2' ERR

for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) == 'install ok installed' ]]; then
        echo "host: conflicting package $package; use a fresh dedicated instance" >&2
        exit 2
    fi
done

stage=system-packages
echo "WORKSHOP_STAGE=$stage"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git python3.12 python3.12-venv
install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location --max-time 60 \
    https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/doris-workshop-docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
mapfile -t docker_packages < <(python3.12 "$bundle/host.py" packages --bundle "$bundle")
if [[ ${#docker_packages[@]} -ne 5 ]]; then
    echo 'host: invalid Docker package plan' >&2
    exit 2
fi
apt-get install -y --no-install-recommends "${docker_packages[@]}"

stage=user-and-docker
echo "WORKSHOP_STAGE=$stage"
install -d -m 0755 /var/lib/doris-workshop
if ! id workshop >/dev/null 2>&1; then
    useradd --create-home --user-group --home-dir /var/lib/doris-workshop/home --shell /bin/bash workshop
fi
python3.12 "$bundle/host.py" validate-user
chmod 0700 /var/lib/doris-workshop/home
usermod --append --groups docker workshop
systemctl enable --now docker.service

stage=course-environments
echo "WORKSHOP_STAGE=$stage"
python3.12 "$bundle/host.py" install --bundle "$bundle"
stage=jupyter-service
echo "WORKSHOP_STAGE=$stage"
systemctl daemon-reload
systemctl enable doris-workshop-jupyter.service
systemctl restart doris-workshop-jupyter.service
stage=readiness
echo "WORKSHOP_STAGE=$stage"
runuser --user workshop -- env -i \
    HOME=/var/lib/doris-workshop/home PATH=/opt/doris-workshop/server-venv/bin:/usr/local/bin:/usr/bin:/bin \
    LANG=C.UTF-8 PYTHONNOUSERSITE=1 \
    /opt/doris-workshop/server-venv/bin/python -I /usr/local/lib/doris-workshop/check.py --wait 60
echo 'Host runtime checks passed; course Labs and browser gateway still require validation.'
