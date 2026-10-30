# Temporary course access

The host runtime serves `/workshop/` inside the same authenticated Jupyter
server as `/lab`, the Notebook APIs and terminal WebSockets. It reads the
selected modules from `runtime.json`, built from the pinned course manifests.
Lessons and images come from the pinned source; Lab links open the learner's
working copies with the selected course kernel. JupyterLab retains its normal
Save and Download actions. This reader has no learning-record API, grade store,
completion controls, certificate service or external frontend dependency.

## Start and verify locally

Install the test dependencies in a separate Python 3.12 environment:

```bash
python3.12 -m venv .venv-workshop
.venv-workshop/bin/python -m pip install -r workshop/aws/runtime/test-requirements.txt
.venv-workshop/bin/python -m unittest discover -s tools/tests -p test_workshop_runtime.py -v
```

The tests start a real loopback server, render a fixture lesson and image, open
the working Notebook and terminal, and connect authenticated kernel and terminal
WebSockets. They also check anonymous GET/POST and WebSocket rejection and native
Notebook download. They need neither AWS nor Docker.

To serve an installed or locally materialized runtime, supply its paths:

```bash
.venv-workshop/bin/python -I workshop/aws/runtime/serve.py \
  --runtime-json /path/to/bundle/runtime.json \
  --source /path/to/pinned/source \
  --workspace /path/to/learner/workspace \
  --kernel-dir /path/to/managed/jupyter/kernels \
  --runtime-dir /path/to/private/session --port 8888
```

Open `http://127.0.0.1:8888/workshop/` and enter the generated `session/token`
in Jupyter's login form. Keep the token private. It rotates whenever the server
restarts, as does the cookie secret. The server uses the managed kernel
allowlist; it never installs or executes commands from the lesson reader.

## Host, TLS and authentication boundary

The installed service listens on `127.0.0.1:8888`. Use the existing SSM port
forward from the [first-run guide](../first-run.md), then open `/workshop/`.
The token is a temporary single-instance credential. It does not bind an OIDC
subject to a learner or provide shared multi-learner access.

For a local TLS test, pass both `--certfile /path/to/certificate.pem` and
`--keyfile /path/to/private-key.pem`. The certificate must match the browser
hostname and be trusted there. HTTPS enables secure authentication cookies.
`--base-url /prefix/` places all reader and Jupyter routes under one prefix.
Do not disable XSRF or token checking. Forwarded headers are not trusted and
remote access remains disabled. A future trusted reverse proxy needs an
explicit reviewed configuration, TLS and per-request ownership checks.

The authentication model follows [Jupyter Server security](https://jupyter-server.readthedocs.io/en/latest/operators/security.html);
Notebook navigation follows [JupyterLab file URLs](https://jupyterlab.readthedocs.io/en/stable/user/urls.html).
Actual EC2 browser use, TLS, login-cookie behavior, refresh and reconnection
remain AWS-11 second-round checks. Shared HTTPS/OIDC and cross-learner rejection
remain AWS-15/16 work.
