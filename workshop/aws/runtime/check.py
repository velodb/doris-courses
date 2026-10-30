"""Report host-runtime readiness without creating Lab containers or printing secrets."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import queue
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def command_ok(command):
    try:
        result = subprocess.run(command, capture_output=True, timeout=15)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def execute_json(manager, client, code, timeout=30):
    message_id = client.execute(code, store_history=False)
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        try:
            message = client.get_iopub_msg(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            break
        if message.get("parent_header", {}).get("msg_id") != message_id:
            continue
        kind = message["header"]["msg_type"]
        if kind == "error":
            raise RuntimeError("kernel execution failed")
        if kind == "stream":
            output += message["content"]["text"]
        if kind == "status" and message["content"].get("execution_state") == "idle":
            for line in output.splitlines():
                if line.startswith("WORKSHOP_PROBE="):
                    return json.loads(line[len("WORKSHOP_PROBE="):])
            raise RuntimeError("kernel did not return a probe result")
    raise RuntimeError("kernel execution timed out")


def probe_kernel(course, source_root, kernel_dir, *, check_docker=True):
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager

    root = Path(source_root) / course["course_dir"]
    # Start from a nested Notebook directory, as the browser will. The launcher
    # must override that cwd and retain its own interpreter after restart.
    nested = root / Path(course["notebooks"][0]).parent
    specs = KernelSpecManager(kernel_dirs=[str(kernel_dir)], ensure_native_kernel=False)
    manager = KernelManager(kernel_name=course["kernel"], kernel_spec_manager=specs)
    client = None
    probe_code = f"""
import importlib, json, pathlib, socket, subprocess, sys, tempfile
course_package = importlib.import_module({course['package']!r})
shared_package = importlib.import_module('doris_labkit')
with tempfile.NamedTemporaryFile(dir=pathlib.Path.cwd(), prefix='.workshop-write-') as writable:
    writable.write(b'probe')
result = {{'executable': sys.executable, 'prefix': sys.prefix, 'cwd': str(pathlib.Path.cwd()),
          'package_file': course_package.__file__, 'shared_file': shared_package.__file__,
          'python': list(sys.version_info[:2]), 'loopback': socket.gethostbyname('localhost')}}
if {check_docker!r}:
    result['docker'] = subprocess.run(['docker', 'info'], capture_output=True, timeout=15).returncode == 0
    result['compose'] = subprocess.run(['docker', 'compose', 'version'], capture_output=True, timeout=15).returncode == 0
print('WORKSHOP_PROBE=' + json.dumps(result))
"""
    results = []
    try:
        manager.start_kernel(cwd=str(nested), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = manager.client()
        client.start_channels()
        client.wait_for_ready(timeout=30)
        for restart in (False, True):
            if restart:
                manager.restart_kernel(now=True)
                client.wait_for_ready(timeout=30)
            result = execute_json(manager, client, probe_code)
            expected_interpreter = root / ".venv/bin/python"
            if (Path(result["executable"]).parent.resolve() != expected_interpreter.parent.resolve()
                    or Path(result["executable"]).name != expected_interpreter.name
                    or Path(result["prefix"]).resolve() != (root / ".venv").resolve()
                    or Path(result["cwd"]).resolve() != root.resolve()
                    or not Path(result["package_file"]).resolve().is_relative_to(root.resolve())
                    or not Path(result["shared_file"]).resolve().is_relative_to((Path(source_root) / "doris-course/common").resolve())
                    or result["python"] != [3, 12] or result["loopback"] != "127.0.0.1"
                    or (check_docker and not (result.get("docker") and result.get("compose")))):
                raise RuntimeError("kernel interpreter, imports, cwd, loopback or Docker access is incorrect")
            results.append(result)
        return results
    finally:
        if client is not None:
            client.stop_channels()
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
        manager.cleanup_resources()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def jupyter_ready(token_file, url="http://127.0.0.1:8888/api/status", *, expected_kernels=None):
    """Require both rejection without a token and success with the local token."""
    try:
        token_file = Path(token_file)
        if token_file.stat().st_mode & 0o777 != 0o600:
            return False
        token = token_file.read_text().strip()
        if len(token) < 32:
            return False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            opener.open(url, timeout=5).close()
            return False
        except urllib.error.HTTPError as error:
            if error.code not in (401, 403):
                return False
        request = urllib.request.Request(url, headers={"Authorization": "token " + token})
        with opener.open(request, timeout=5) as response:
            if response.status != 200 or not isinstance(json.load(response), dict):
                return False
        if expected_kernels is not None:
            specs_url = url.removesuffix("/api/status") + "/api/kernelspecs"
            request = urllib.request.Request(specs_url, headers={"Authorization": "token " + token})
            with opener.open(request, timeout=5) as response:
                if set(json.load(response)["kernelspecs"]) != set(expected_kernels):
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        return False


def portal_ready(token_file, url="http://127.0.0.1:8888/workshop/"):
    try:
        token = Path(token_file).read_text().strip()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            opener.open(url, timeout=5).close()
            return False
        except urllib.error.HTTPError as error:
            if error.code not in (302, 401, 403):
                return False
        with opener.open(urllib.request.Request(url, headers={"Authorization": "token " + token}), timeout=5) as response:
            return response.status == 200 and b"Apache Doris workshop" in response.read(262144)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def widget_frontend_ready(token_file, url="http://127.0.0.1:8888/lab"):
    """Require the widget manager in Lab's actual page config and served assets."""
    try:
        headers = {"Authorization": "token " + Path(token_file).read_text().strip()}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(urllib.request.Request(url, headers=headers), timeout=5) as response:
            page = response.read().decode()
        match = re.search(r'<script[^>]*id="jupyter-config-data"[^>]*>(.*?)</script>', page, re.S)
        config = json.loads(match.group(1))
        manager = next(item for item in config["federated_extensions"]
                       if item["name"] == "@jupyter-widgets/jupyterlab-manager")
        path = config["fullLabextensionsUrl"].rstrip("/") + "/" + manager["name"] + "/" + manager["load"]
        asset_url = urllib.parse.urljoin(url, path)
        with opener.open(urllib.request.Request(asset_url, headers=headers), timeout=5) as response:
            return response.status == 200 and bool(response.read(1024))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, urllib.error.URLError):
        return False


def course_services():
    """Report current containers, including stopped Labs; do not start services."""
    result = subprocess.run(["docker", "ps", "--all", "--format", "{{.Names}}\t{{.State}}"],
                            capture_output=True, text=True, timeout=15, check=True)
    services = []
    for line in result.stdout.splitlines():
        name, state = line.split("\t", 1)
        if name.startswith(("doris-rta", "doris-warehousing-course", "doris-obs", "doris-ai")):
            services.append({"name": name, "state": state})
    return services


def collect_checks(plan, *, source=Path("/opt/doris-courses"),
                   workspace=Path("/var/lib/doris-workshop/workspace"),
                   kernels=Path("/usr/local/share/doris-workshop/jupyter/kernels")):
    checks = []

    def check(name, function):
        try:
            passed = bool(function())
        except Exception:
            passed = False
        checks.append({"check": name, "passed": passed})

    check("target-platform", lambda: platform.system() == "Linux" and platform.machine() == "x86_64"
          and platform.freedesktop_os_release().get("ID") == "ubuntu"
          and platform.freedesktop_os_release().get("VERSION_ID") == "24.04" and sys.version_info[:2] == (3, 12))
    check("learner-identity", lambda: os.geteuid() != 0 and pwd.getpwuid(os.geteuid()).pw_name == "workshop")
    check("source-version", lambda: json.loads((source / ".doris-workshop-source.json").read_text())["course_ref"] == plan["course_ref"])
    check("docker-daemon-and-permission", lambda: command_ok(["docker", "info"]))
    check("compose", lambda: command_ok(["docker", "compose", "version"]))
    check("jupyter-systemd", lambda: command_ok(["systemctl", "is-active", "--quiet", "doris-workshop-jupyter.service"]))
    check("jupyter-authenticated-loopback", lambda: jupyter_ready(Path("/run/doris-workshop/token"),
          expected_kernels={course["kernel"] for course in plan["courses"]}))
    check("course-entry", lambda: portal_ready(Path("/run/doris-workshop/token")))
    check("jupyter-widget-frontend", lambda: widget_frontend_ready(Path("/run/doris-workshop/token")))
    for course in plan["courses"]:
        def copies_valid():
            return all(json.loads((workspace / course["course_dir"] / path).read_text())["metadata"]["kernelspec"]["name"] == course["kernel"]
                       for path in course["notebooks"])
        check(course["kernel"] + ":notebook-bindings", copies_valid)
        check(course["kernel"] + ":interpreter-imports-cwd-restart-docker",
              lambda: len(probe_kernel(course, source, kernels)) == 2)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-json", type=Path, default=Path("/etc/doris-workshop/runtime.json"))
    parser.add_argument("--wait", type=int, default=0, choices=range(0, 301), metavar="SECONDS")
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.runtime_json.read_text())
        if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "workshop":
            print(json.dumps({"scope": "host-runtime", "ready": False, "error": "run as the workshop user; no kernel probes started"}))
            return 1
        deadline = time.monotonic() + args.wait
        # Wait only for startup prerequisites. Kernel probes have their own bounds.
        while args.wait and time.monotonic() < deadline:
            if jupyter_ready(Path("/run/doris-workshop/token")):
                break
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        checks = collect_checks(plan)
        passed = all(check["passed"] for check in checks)
        try:
            services = course_services()
        except (OSError, ValueError, subprocess.SubprocessError):
            services = []
            passed = False
            checks.append({"check": "course-service-inventory", "passed": False})
        print(json.dumps({"scope": "host-runtime", "ready": passed, "course_labs_validated": False,
                          "course_ref": plan["course_ref"],
                          "runtime_sha256": hashlib.sha256(args.runtime_json.read_bytes()).hexdigest(),
                          "checks": checks, "services": services}, indent=2))
        return 0 if passed else 1
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"scope": "host-runtime", "ready": False, "error": "invalid or unreadable runtime configuration"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
