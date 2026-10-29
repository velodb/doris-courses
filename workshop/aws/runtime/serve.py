"""Start loopback-only JupyterLab with credentials generated on this host."""

import argparse
import json
import os
from pathlib import Path
import secrets


def private_bytes(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(value)
    finally:
        os.close(descriptor)


def make_config(plan, workspace, runtime_dir, kernel_dir, port=8888):
    from traitlets.config import Config

    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_dir.chmod(0o700)
    token = secrets.token_urlsafe(48)
    private_bytes(runtime_dir / "token", token.encode())
    private_bytes(runtime_dir / "cookie-secret", secrets.token_bytes(32))
    config = Config()
    config.ServerApp.ip = "127.0.0.1"
    config.ServerApp.port = port
    config.ServerApp.port_retries = 0
    config.ServerApp.root_dir = str(workspace)
    config.ServerApp.open_browser = False
    config.ServerApp.allow_root = False
    config.ServerApp.allow_remote_access = False
    config.ServerApp.trust_xheaders = False
    config.ServerApp.disable_check_xsrf = False
    config.ServerApp.cookie_secret_file = str(runtime_dir / "cookie-secret")
    config.ServerApp.log_level = "WARN"
    config.ServerApp.no_browser_open_file = True
    config.ServerApp.runtime_dir = str(runtime_dir)
    config.IdentityProvider.token = token
    config.KernelSpecManager.ensure_native_kernel = False
    config.KernelSpecManager.allowed_kernelspecs = {course["kernel"] for course in plan["courses"]}
    config.MultiKernelManager.default_kernel_name = plan["courses"][0]["kernel"]
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-json", type=Path, default=Path("/etc/doris-workshop/runtime.json"))
    parser.add_argument("--workspace", type=Path, default=Path("/var/lib/doris-workshop/workspace"))
    parser.add_argument("--runtime-dir", type=Path, default=Path("/run/doris-workshop"))
    parser.add_argument("--kernel-dir", type=Path, default=Path("/usr/local/share/doris-workshop/jupyter/kernels"))
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args(argv)
    from jupyterlab.labapp import LabApp

    plan = json.loads(args.runtime_json.read_text())
    config = make_config(plan, args.workspace, args.runtime_dir, args.kernel_dir, args.port)
    # LabApp.launch_instance drops its kwargs when creating ServerApp. Pass
    # configuration through initialize_server instead, before any startup.
    server = LabApp.initialize_server(argv=[], config=config)
    # kernel_dirs is an instance trait, not a configurable trait in
    # jupyter_client. Setting it through Config is silently ineffective.
    server.kernel_spec_manager.kernel_dirs = [str(args.kernel_dir)]
    server.start()


if __name__ == "__main__":
    main()
