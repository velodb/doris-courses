# Host runtime for AWS workshops

The authenticated [course reader and terminal entry](access.md) share the
Jupyter login at `/workshop/`. The page contains no learning-record controls.

The runtime bundle installs the selected courses on a dedicated Ubuntu 24.04
x86_64 host. Each course gets a Python 3.12 virtual environment, a distinct
Jupyter kernel and a working copy of its selected Notebooks. JupyterLab runs
as the `workshop` user under systemd and listens on `127.0.0.1:8888`. Its writable
Jupyter configuration directory is `/var/lib/doris-workshop/home/jupyter-config`;
`/etc/doris-workshop` holds the root-managed runtime plan. This separation lets
Jupyter migrate IPython configuration after kernels have run and after restarts.
The server environment also installs the widget frontend used by course
quizzes. Installing `ipywidgets` only in a separate course kernel does not make
its browser extension available to the server. Readiness checks verify that
Lab advertises and serves the widget manager's JavaScript assets.

The server negotiates gzip for text responses, including JupyterLab's
JavaScript chunks. This reduces cold-load traffic through SSM port forwarding;
slow tunnels can otherwise exceed the browser's script-loading timeout.

The host runtime was validated on two real Ubuntu 24.04 x86_64
`m7i.2xlarge` instances with `rta-main` on 2026-10-30. Checks covered Notebook
kernel and terminal Docker access, real container execution, authenticated
loopback access through SSM port forwarding, service/host reboot, credential
rotation, saved-file persistence and fault detection/recovery. A same-name
package fixture also verified kernel isolation on Linux; this does not certify
the other courses' Labs.

The real restart test found a Jupyter configuration-migration permission error;
the writable user configuration directory above fixes it. A regression test
reproduces the previous failure and verifies repeated authenticated startup.
The complete local tool suite passes with Python 3.12, including live kernel
and Jupyter fixtures.

The `rta-main` default Labs 1–10 and ten quiz entry Notebooks were also
executed in separate managed kernels on a fresh Ubuntu 24.04 host: 20 Notebooks,
189 code cells, with no final error outputs. Module 9 requires stopping the
single-node sandbox first; stop its cluster before Lab 10, as the course guide
requires. Optional Metabase and the opt-in real-clock expiry path were excluded.
Authenticated lesson/image/download APIs, kernel/terminal WebSockets, and a
temporary loopback HTTPS service with certificate verification and Secure,
HttpOnly login cookies were checked separately. These API results do not
certify manual browser rendering or interaction.

Terraform creates and destroys the hosts; this installer handles the runtime.
The shared browser gateway, trusted domain/OIDC routing, manual browser steps
and other course profiles remain separate implementation and validation work. The instance expiry timer is a host cost
guard; activity-wide scheduled cleanup still requires a separate executor.

## Build a bundle locally

Use Python 3.12 and the [configuration tool's dependencies](../../../tools/requirements.txt).
From the repository root:

```bash
python tools/workshop_runtime.py \
  --config workshop/aws/examples/event.example.yaml \
  --output workshop/aws/.local/runtime-bundle
python workshop/aws/.local/runtime-bundle/host.py verify \
  --bundle workshop/aws/.local/runtime-bundle
```

Use a real event configuration when preparing an activity. The example only
demonstrates local generation. The selected Git commit must already exist
locally. The builder exports tracked files at that exact commit with `git
archive`; it excludes `.git`, untracked files, local virtual environments and
deployer login state. Runtime helper code is copied separately from this
checkout and its SHA256 hashes are recorded in `runtime.json`.

The runtime plan contains course selections, relative Notebook paths, kernel
names, runtime targets and artifact hashes. It contains no event account,
learner roster, authentication configuration or AWS credential-profile name.
The output directory has mode `0700`; an existing destination is refused.
Payload hashes detect corruption against the plan. Distribute the reviewed
bundle through a trusted channel; these hashes are not a signature or a
substitute for trusting the course commit and installer.

## Install on the target host

After a separate provisioning step supplies a dedicated Ubuntu 24.04 x86_64
instance, copy the complete bundle there and run:

```bash
sudo bash ./runtime-bundle/install_host.sh
```

The installer checks platform and root identity before changing the host,
verifies bundle hashes, takes an installation lock and refuses conflicting
Docker packages. It installs Python 3.12 and Docker from the official signed
APT repository. Exact Docker package versions and Jupyter entry-point versions
are listed in [packages.json](packages.json); unavailable versions fail rather
than silently selecting another version. The currently listed versions were
found in the official Ubuntu Noble amd64 package index on 2026-10-29 and
validated together on the Ubuntu EC2 hosts on 2026-10-30.

Course dependencies come from the pinned course's `requirements.txt` and
editable local package, installed from that course's directory so `../common`
resolves correctly. `dw-extensions` additionally installs `.[extensions]`.
Each environment runs `pip check`. The installer uses a clean learner process
environment and public PyPI; it does not pass deployer AWS credentials, custom
package indexes, Python paths or shell login state into installation commands.

Python and Ubuntu transitive dependencies are not yet fully locked. Actual
Python `pip freeze` output and installed Docker versions are saved under
`/var/lib/doris-workshop/installed-packages/` for review and later AMI work.
This does not claim a reproducible image build or pre-cache Lab images,
models and datasets. Course containers start when the learner runs the Labs.

## Paths and ownership

| Path | Purpose |
| --- | --- |
| `/opt/doris-courses` | Complete pinned course source export; course runtime data and `.venv` directories live under their respective course roots |
| `/opt/doris-workshop/server-venv` | Separate JupyterLab server environment |
| `/var/lib/doris-workshop/workspace` | Repository-shaped course materials and selected Notebook working copies |
| `/var/lib/doris-workshop/home` | Fresh local home for `workshop` |
| `/usr/local/share/doris-workshop/jupyter/kernels` | Managed `doris-rta`, `doris-dw`, `doris-obs` and/or `doris-ai` kernelspecs |
| `/usr/local/lib/doris-workshop` | Runtime helper programs |
| `/etc/doris-workshop/runtime.json` | Non-secret installed runtime plan |
| `/var/lib/doris-workshop/install.json` | Installation fingerprint and completed environment stages |
| `/run/doris-workshop` | Private, temporary Jupyter token, cookie secret and connection files |
| `/var/log/doris-workshop/bootstrap.log` | Target-host bootstrap stages, dependency installation output and readiness result |

Course source/runtime directories and working copies are writable by `workshop`.
Operational configuration and helpers are root-owned. The learner belongs to
the Docker group, which grants control of this dedicated instance. Keep
deployment credentials, Terraform state and other learners' secrets off it.

Jupyter uses only the selected managed kernels. Each kernelspec names its
course's `.venv/bin/python` explicitly and sets a course-specific executable
search path. The launcher changes cwd to that course root before starting
IPKernelApp, including when the browser opened a nested Notebook. This keeps
same-named `doris_course` packages in separate processes and preserves the
courses' localhost and Docker CLI behavior.

Selected Lab, quiz and optional Notebooks are copied with the correct kernel;
source Notebook metadata stays unchanged. Other repository files retain their
relative layout for lesson images and links. Unselected Notebooks are omitted
from the workspace. This is preparation of files, not the course navigation UI.

## Retry and restart behavior

Rerunning the same bundle resumes completed environment stages and preserves
existing learner Notebooks, downloaded assets and database volumes. It does
not replace working copies or reset the source tree. An unmanaged source
directory, a different course archive or a different runtime bundle is refused;
use a fresh instance for those changes. A damaged working copy or environment
can therefore make readiness fail and needs explicit repair.

The installed `doris-workshop-jupyter.service` requires Docker, starts after
network readiness and is enabled for host startup. It restarts on failure.
Restarting Jupyter keeps disk files and generates a fresh token and cookie
secret in `/run`; clients need a fresh authenticated session. Runtime secrets
are generated on the target, never copied from the bundle or developer machine.
Host reboot and this recovery behavior still need real EC2 verification.

Jupyter remains on loopback, requires its private local token and keeps XSRF
checks enabled. Startup does not print the token or open a browser. A later
gateway must add HTTPS, authenticated user ownership and protected proxy routes
before browser access is made public. These runtime controls alone do not
implement workshop access isolation.

## Readiness and troubleshooting

Run the read-only host probe as the learner user:

```bash
sudo runuser --user workshop -- env -i \
  HOME=/var/lib/doris-workshop/home \
  PATH=/opt/doris-workshop/server-venv/bin:/usr/local/bin:/usr/bin:/bin \
  /opt/doris-workshop/server-venv/bin/python -I \
  /usr/local/lib/doris-workshop/check.py --wait 60
```

The probe reports separate results for platform, identity, source version,
Docker daemon/permission, Compose, systemd, authenticated loopback Jupyter,
Notebook kernel bindings and each course kernel. It starts each kernel from
a nested Notebook directory, checks interpreter prefix, course/shared imports,
cwd, write access, loopback resolution and Docker/Compose commands, then
restarts it and repeats. Kernels shut down after the probe. Missing dependencies,
permission failures and a stopped Docker daemon produce a failed result.
The probe does not start or stop Lab containers.

Exit codes are `0` for host-runtime checks passed, `1` for failed readiness and
`2` for unreadable configuration or provisioning errors. Even successful output
sets `course_labs_validated: false`; it does not assert that teaching SQL,
browser pages, AI Host integration or whole-course execution passed.

For bootstrap failures, start with the stage and line in
`/var/log/doris-workshop/bootstrap.log`. Inspect service failures with
`systemctl status doris-workshop-jupyter.service` and
`journalctl -u doris-workshop-jupyter.service`. Keep raw operational logs local
and redact them before saving evidence. The bootstrap does not install a log
retention policy yet.

## Local tests

```bash
python -m pip install -r workshop/aws/runtime/test-requirements.txt
python -m unittest discover -s tools/tests -p test_workshop_runtime.py -v
shellcheck workshop/aws/runtime/install_host.sh
```

The suite tests pinned exports, integrity checks, archive path rejection,
preservation on retries, dropped deployment environment variables and failed
readiness stages. With Python 3.12 and Jupyter installed it also starts real
fixture kernels in distinct temporary venvs, restarts them, and starts a
temporary Jupyter server to check token rejection, credential rotation and
kernel allowlisting. Fixture kernels share test dependencies through `.pth`
files; production environments use separate dependency installs. No test needs
AWS or a running Docker daemon. The dedicated CI job installs the dependencies
needed for these live local tests.

Installation details follow the [official Docker Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
Kernel registration follows the [Jupyter client kernelspec contract](https://jupyter-client.readthedocs.io/en/stable/kernels.html),
and server controls use the [Jupyter Server configuration API](https://jupyter-server.readthedocs.io/en/stable/other/full-config.html).
