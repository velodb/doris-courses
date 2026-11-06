#!/usr/bin/env bash
# Last operation on the build machine. SSM has acknowledged the scheduling command.
set -Eeuo pipefail
umask 077
systemctl stop doris-workshop-jupyter.service docker.service docker.socket containerd.service
# Keep cached images, but let Docker create a unique engine identity on each clone.
rm -f /var/lib/docker/engine-id
# Nothing may write identity/session state while it is being removed.
systemctl stop snap.amazon-ssm-agent.amazon-ssm-agent.service
systemctl stop rsyslog.service systemd-journald.service systemd-journald.socket systemd-journald-dev-log.socket systemd-journald-audit.socket || true
systemctl disable --now doris-image-expiry.timer
rm -f /etc/systemd/system/doris-image-expiry.{timer,service}
rm -rf /var/lib/amazon/ssm /var/log/amazon/ssm /run/doris-workshop
rm -rf /var/lib/doris-workshop/home /var/lib/doris-workshop/workspace /var/lib/doris-workshop/progress
install -d -m 0700 -o workshop -g workshop /var/lib/doris-workshop/progress
install -d -m 0700 -o workshop -g workshop /var/lib/doris-workshop/home
# Recreate only pristine selected notebooks and helpers, never verification output.
python3.12 - <<'PY'
import json, sys
sys.path.insert(0, '/usr/local/lib/doris-workshop')
import host
layout = host.Layout()
plan = json.loads((layout.config / 'runtime.json').read_text())
files = [str(p.relative_to(layout.source)) for p in layout.source.rglob('*')
         if p.is_file() and '.venv' not in p.parts and '__pycache__' not in p.parts and
         not any(x.endswith('.egg-info') for x in p.parts) and p.name != '.doris-workshop-source.json']
host.materialize(plan, files, layout)
user = host.learner_user(layout)
host.chown_tree(layout.workspace, user)
config = layout.home / 'jupyter-config'
config.mkdir(mode=0o700)
host.chown_tree(layout.home, user)
PY
for task_home in /root /home/ubuntu /home/ssm-user /var/lib/doris-workshop/home; do
  rm -rf "$task_home/.aws" "$task_home/.ssh" "$task_home/.docker" "$task_home/.cache" "$task_home/.ipython" "$task_home/.local/share/jupyter" "$task_home/.bash_history" "$task_home/.python_history"
done
rm -f /etc/ssh/ssh_host_* /var/lib/dbus/machine-id
cloud-init clean --logs --machine-id --seed --configs all
rm -rf /var/lib/cloud /var/log/doris-workshop /var/log/journal /var/lib/systemd/random-seed
rm -rf /var/lib/doris-workshop/incoming /tmp/* /var/tmp/*
find /var/log -type f -exec truncate -s 0 {} +
# A completion marker is necessary: the local provisioner also verifies shutdown.
install -m 0644 /dev/null /etc/doris-workshop/image-sealed
sync
shutdown -h now
