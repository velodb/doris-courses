"""Start loopback-only JupyterLab with credentials generated on this host."""

import argparse
import json
import os
from pathlib import Path
import secrets
import sys

# Python -I excludes this directory. Load only the deployed helper directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def private_bytes(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(value)
    finally:
        os.close(descriptor)


def make_config(plan, workspace, runtime_dir, kernel_dir, port=8888, *, base_url="/", certfile=None, keyfile=None):
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
    config.ServerApp.root_dir = str(Path(workspace).resolve())
    config.ServerApp.base_url = base_url
    config.ServerApp.default_url = "/workshop/"
    if bool(certfile) != bool(keyfile):
        raise ValueError("TLS requires both certificate and key")
    if certfile:
        config.ServerApp.certfile = str(certfile)
        config.ServerApp.keyfile = str(keyfile)
        config.IdentityProvider.secure_cookie = True
    config.ServerApp.open_browser = False
    config.ServerApp.allow_root = False
    config.ServerApp.allow_remote_access = False
    config.ServerApp.trust_xheaders = False
    config.ServerApp.disable_check_xsrf = False
    config.ServerApp.cookie_secret_file = str(runtime_dir / "cookie-secret")
    config.ServerApp.log_level = "WARN"
    config.ServerApp.use_redirect_file = False
    # JupyterLab loads many JavaScript chunks over the SSM tunnel. Negotiate
    # text compression with browsers so cold loads do not exhaust chunk timeouts.
    config.ServerApp.tornado_settings = {"compress_response": True}
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
    parser.add_argument("--source", type=Path, default=Path("/opt/doris-courses"))
    parser.add_argument("--base-url", default="/")
    parser.add_argument("--certfile", type=Path)
    parser.add_argument("--keyfile", type=Path)
    parser.add_argument("--progress-db", type=Path)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--content-root", type=Path, default=Path('/var/lib/doris-workshop/content/tree'))
    args = parser.parse_args(argv)
    # runtime_dir is selected by jupyter_core through this environment variable;
    # it is not a configurable ServerApp trait.
    os.environ["JUPYTER_RUNTIME_DIR"] = str(args.runtime_dir)
    from jupyterlab.labapp import LabApp

    plan = json.loads(args.runtime_json.read_text())
    content = None
    if plan.get('schema_version') == 2:
        from multilingual import Content, verify_tree
        verify_tree(args.content_root, plan['content'], full=True, source=args.source)
        content = Content(plan, args.content_root, args.workspace)
    elif plan.get('schema_version', 1) != 1 or 'content' in plan or 'capabilities' in plan:
        raise ValueError('runtime: unsupported version or undeclared capability')
    config = make_config(plan, args.workspace, args.runtime_dir, args.kernel_dir, args.port,
                         base_url=args.base_url, certfile=args.certfile, keyfile=args.keyfile)
    # LabApp.launch_instance drops its kwargs when creating ServerApp. Pass
    # configuration through initialize_server instead, before any startup.
    server = LabApp.initialize_server(argv=[], config=config)
    # kernel_dirs is an instance trait, not a configurable trait in
    # jupyter_client. Setting it through Config is silently ineffective.
    server.kernel_spec_manager.kernel_dirs = [str(args.kernel_dir)]
    from portal import register
    progress_db = None
    if not args.no_progress and plan.get('learning_progress') is True and all(c.get('slug') for c in plan['courses']):
        progress_db = args.progress_db or args.workspace.parent / 'progress/progress.sqlite3'
        if progress_db.resolve().is_relative_to(args.workspace.resolve()):
            raise ValueError('Progress storage must be outside the Notebook root.')
        # Managed kernels and the reader share the exact same store and plan.
        os.environ.update(DORIS_WORKSHOP_PROGRESS_DB=str(progress_db.resolve()),
                          DORIS_WORKSHOP_PROGRESS_PLAN=str(args.runtime_json.resolve()),
                          DORIS_WORKSHOP_PROGRESS_SOURCE=str(args.source.resolve()))
    else:
        for name in ('DORIS_WORKSHOP_PROGRESS_DB', 'DORIS_WORKSHOP_PROGRESS_PLAN', 'DORIS_WORKSHOP_PROGRESS_SOURCE'):
            os.environ.pop(name, None)
    if content:
        from host import atomic_json
        private_plan = (progress_db.parent if progress_db else args.workspace.parent / 'progress') / 'runtime-v2.json'
        atomic_json(private_plan, content.enrich(), 0o600)
        os.environ.update(DORIS_WORKSHOP_PROGRESS_PLAN=str(private_plan.resolve()),
                          DORIS_WORKSHOP_PROGRESS_SOURCE=str(args.content_root.resolve()))
    register(server, plan, args.source, progress_db=progress_db,
             **({'installed_ui': content.ui(), 'content_adapter': content} if content else {}))
    server.start()


if __name__ == "__main__":
    main()
