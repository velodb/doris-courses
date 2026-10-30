"""Runtime bundles, preservation, real kernel isolation and loopback authentication."""

import copy
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.request
import venv

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import workshop_runtime as builder  # noqa: E402


def load_helper(name):
    spec = importlib.util.spec_from_file_location("workshop_" + name, builder.RUNTIME / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load_helper("host")
check = load_helper("check")
serve = load_helper("serve")
LIVE = (sys.version_info[:2] == (3, 12) and importlib.util.find_spec("jupyterlab") is not None
        and importlib.util.find_spec("ipykernel") is not None)


def commit(repo, message):
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                    "-c", "commit.gpgsign=false", "commit", "-qm", message], check=True)
    return builder.config_tool.git(repo, "rev-parse", "HEAD").strip()


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.repo = cls.base / "repo"
        cls.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(cls.repo)], check=True)
        cls.notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {
            "name": "python3", "display_name": "Original Python", "language": "python"}},
            "cells": [{"cell_type": "code", "id": "fixture", "metadata": {}, "source": ["1 + 1"],
                       "outputs": [], "execution_count": None}]}
        cls.lab = "level1/module01-fixture/lab01_fixture.ipynb"
        for kernel in ("doris-rta", "doris-obs"):
            directory, package = host.COURSES[kernel]
            root = cls.repo / directory
            (root / package).mkdir(parents=True)
            (root / package / "__init__.py").write_text(f"COURSE = {kernel!r}\n")
            (root / cls.lab).parent.mkdir(parents=True)
            (root / cls.lab).write_text(json.dumps(cls.notebook))
            (root / "unselected.ipynb").write_text(json.dumps(cls.notebook))
            (root / "course.md").write_text("Fixture lesson\n")
            (root / "quiz.yaml").write_text("title: Fixture\n")
            (root / "quiz.ipynb").write_text(json.dumps(cls.notebook))
            (root / "course.yaml").write_text(yaml.safe_dump({
                "status": "published", "modules": [{"id": "module-1", "required": True,
                "status": "published", "course": "course.md", "lab": cls.lab, "quiz": "quiz.yaml"}]}))
        common = cls.repo / "doris-course/common/doris_labkit"
        common.mkdir(parents=True)
        (common / "__init__.py").write_text("SHARED = True\n")
        ref = commit(cls.repo, "Pinned runtime fixture")
        cls.config = builder.config_tool.load_document(builder.config_tool.SPEC / "examples/event.example.yaml")
        cls.config.update(course_ref=ref, course_profiles=["rta-main", "obs-main"])
        cls.config_path = cls.base / "event.yaml"
        cls.config_path.write_text(yaml.safe_dump(cls.config))
        cls.bundle = cls.base / "bundle"
        cls.plan = builder.create_bundle(cls.config_path, cls.bundle, repo=cls.repo)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.case_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.case_dir.cleanup)
        self.layout = host.Layout(Path(self.case_dir.name))

    def materialize(self):
        files = host.install_source(self.bundle, self.plan, self.layout)
        self.layout.helpers.mkdir(parents=True)
        shutil.copyfile(builder.RUNTIME / "kernel.py", self.layout.helpers / "kernel.py")
        host.materialize(self.plan, files, self.layout)
        return files

    def test_bundle_is_pinned_verified_and_runtime_only(self):
        plan, packages = host.verify_bundle(self.bundle)
        self.assertEqual(plan["course_ref"], self.config["course_ref"])
        self.assertEqual(len(plan["courses"]), 2)
        self.assertEqual(set(packages["docker_apt"]), host.DOCKER_PACKAGES)
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o700)
        for key in ("aws_account_id", "aws_profile", "event_id", "learner_ids", "auth"):
            self.assertNotIn(key, json.dumps(plan))
        with tarfile.open(self.bundle / "source.tar") as stream:
            self.assertFalse(any(".git/" in member.name for member in stream.getmembers()))
        with self.assertRaisesRegex(builder.config_tool.ConfigError, "already exists"):
            builder.create_bundle(self.config_path, self.bundle, repo=self.repo)

    def test_bundle_detects_tampering_and_unsafe_selection(self):
        modified = Path(self.case_dir.name) / "modified-bundle"
        shutil.copytree(self.bundle, modified)
        (modified / "kernel.py").write_text("changed\n")
        with self.assertRaisesRegex(host.RuntimeErrorDetail, "checksum"):
            host.verify_bundle(modified)
        shutil.copyfile(self.bundle / "kernel.py", modified / "kernel.py")
        plan = copy.deepcopy(self.plan)
        plan["courses"][0]["notebooks"] = ["../../outside.ipynb"]
        (modified / "runtime.json").write_text(json.dumps(plan))
        with self.assertRaisesRegex(host.RuntimeErrorDetail, "unsafe"):
            host.verify_bundle(modified)

    def test_archive_rejects_traversal_symlinks_and_devices(self):
        archive = Path(self.case_dir.name) / "unsafe.tar"
        for name, kind in (("../outside", tarfile.REGTYPE), ("link", tarfile.SYMTYPE), ("device", tarfile.CHRTYPE)):
            with tarfile.open(archive, "w") as stream:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                stream.addfile(info, io.BytesIO())
            with self.subTest(name=name), self.assertRaises(host.RuntimeErrorDetail):
                host.archive_files(archive)

    def test_working_copies_preserve_originals_and_learner_changes(self):
        files = self.materialize()
        for course in self.plan["courses"]:
            source = self.layout.source / course["course_dir"] / self.lab
            target = self.layout.workspace / course["course_dir"] / self.lab
            self.assertEqual(json.loads(source.read_text())["metadata"]["kernelspec"]["name"], "python3")
            self.assertEqual(json.loads(target.read_text())["metadata"]["kernelspec"]["name"], course["kernel"])
            self.assertFalse((self.layout.workspace / course["course_dir"] / "unselected.ipynb").exists())
            target.write_text("learner's unsaved exercise\n")
            host.materialize(self.plan, files, self.layout)
            self.assertEqual(target.read_text(), "learner's unsaved exercise\n")
        host.install_source(self.bundle, self.plan, self.layout)
        other = copy.deepcopy(self.plan)
        other["course_ref"] = "f" * 40
        with self.assertRaisesRegex(host.RuntimeErrorDetail, "another revision"):
            host.install_source(self.bundle, other, self.layout)

    def test_unmanaged_source_and_missing_notebook_fail(self):
        self.layout.source.mkdir(parents=True)
        with self.assertRaisesRegex(host.RuntimeErrorDetail, "unmanaged"):
            host.install_source(self.bundle, self.plan, self.layout)
        shutil.rmtree(self.layout.source)
        files = host.install_source(self.bundle, self.plan, self.layout)
        with self.assertRaisesRegex(host.RuntimeErrorDetail, "missing"):
            host.materialize(self.plan, [name for name in files if not name.endswith(self.lab)], self.layout)

    def test_student_commands_drop_deployer_environment(self):
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "NOT-FOR-THE-HOST", "PIP_EXTRA_INDEX_URL": "PRIVATE-INDEX"}), \
                mock.patch.object(host.subprocess, "run") as run:
            host.as_learner(["python", "-m", "pip", "check"], self.layout)
        command = run.call_args.args[0]
        self.assertIn("-i", command)
        self.assertNotIn("NOT-FOR-THE-HOST", " ".join(command))
        self.assertNotIn("PRIVATE-INDEX", " ".join(command))
        self.assertTrue(any(item.startswith("HOME=") for item in command))

    def test_install_resumes_after_dependency_failure_without_resetting_notebooks(self):
        self.layout.units.mkdir(parents=True)
        user = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        failed = False
        calls = []

        def provision(command, layout, *, cwd=None, capture=False):
            nonlocal failed
            command = list(map(str, command))
            calls.append((command, cwd))
            if command[1:3] == ["-m", "venv"]:
                binary = Path(command[-1]) / "bin/python"
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.touch()
            elif "install" in command and cwd != self.layout.source and not failed:
                failed = True
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout="fixture==1.0\n")

        with mock.patch.object(host, "require_target"), mock.patch.object(host, "Layout", return_value=self.layout), \
                mock.patch.object(host, "learner_user", return_value=user), mock.patch.object(host.os, "chown"), \
                mock.patch.object(host, "as_learner", side_effect=provision), \
                mock.patch.object(host.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="docker=fixture\n")):
            with self.assertRaises(subprocess.CalledProcessError):
                host.install(self.bundle)
            receipt = json.loads((self.layout.state / "install.json").read_text())
            self.assertEqual(receipt["environments"], ["server"])
            host.install(self.bundle)
            user_config = self.layout.home / "jupyter-config"
            self.assertTrue(user_config.is_dir())
            self.assertEqual(user_config.stat().st_mode & 0o777, 0o700)
            course = self.plan["courses"][0]
            notebook = self.layout.workspace / course["course_dir"] / self.lab
            notebook.write_text("learner exercise\n")
            calls.clear()
            host.install(self.bundle)
            self.assertEqual(calls, [])
            self.assertEqual(notebook.read_text(), "learner exercise\n")

    def test_missing_docker_permissions_and_dependencies_are_reported(self):
        self.materialize()
        marker = self.layout.source / ".doris-workshop-source.json"
        self.assertTrue(marker.is_file())
        with mock.patch.object(check, "command_ok", return_value=False), \
                mock.patch.object(check, "jupyter_ready", return_value=False), \
                mock.patch.object(check, "probe_kernel", side_effect=PermissionError):
            checks = check.collect_checks(self.plan, source=self.layout.source,
                                          workspace=self.layout.workspace, kernels=self.layout.kernels)
        statuses = {row["check"]: row["passed"] for row in checks}
        self.assertTrue(statuses["source-version"])
        self.assertFalse(statuses["docker-daemon-and-permission"])
        self.assertFalse(statuses["compose"])
        self.assertFalse(statuses["doris-rta:interpreter-imports-cwd-restart-docker"])
        self.assertFalse(all(statuses.values()))

    def test_readiness_refuses_other_identity_before_launching_learner_code(self):
        config = Path(self.case_dir.name) / "runtime.json"
        config.write_text(json.dumps(self.plan))
        with mock.patch.object(check.os, "geteuid", return_value=0), \
                mock.patch.object(check, "collect_checks") as collect, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            result = check.main(["--runtime-json", str(config)])
        self.assertEqual(result, 1)
        self.assertFalse(json.loads(output.getvalue())["ready"])
        collect.assert_not_called()

    def test_service_inventory_reports_running_and_stopped_course_containers(self):
        output = "doris-rta\trunning\ndoris-warehousing-course\texited\nunrelated\trunning\n"
        with mock.patch.object(check.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)):
            self.assertEqual(check.course_services(), [{"name": "doris-rta", "state": "running"},
                                                     {"name": "doris-warehousing-course", "state": "exited"}])

    @unittest.skipUnless(LIVE, "Install runtime/test-requirements.txt with Python 3.12 for live kernel tests")
    def test_real_isolated_kernels_nested_cwd_and_restart(self):
        import ipykernel

        self.materialize()
        dependency_site = Path(ipykernel.__file__).resolve().parents[1]
        for course in self.plan["courses"]:
            env = self.layout.source / course["course_dir"] / ".venv"
            venv.EnvBuilder(with_pip=False).create(env)
            site = next((env / "lib").glob("python*/site-packages"))
            # Test-only shared Jupyter dependencies; the course packages and
            # interpreters remain distinct. Production installs normal venvs.
            (site / "00-fixture-common.pth").write_text(str(self.layout.source / "doris-course/common") + "\n")
            (site / "99-test-dependencies.pth").write_text(str(dependency_site) + "\n")
            results = check.probe_kernel(course, self.layout.source, self.layout.kernels, check_docker=False)
            self.assertEqual(len(results), 2)
            self.assertEqual(Path(results[0]["prefix"]).resolve(), Path(results[1]["prefix"]).resolve())
            self.assertIn(course["course_dir"], results[0]["package_file"])

    @unittest.skipUnless(LIVE, "Install runtime/test-requirements.txt with Python 3.12 for live server tests")
    def test_server_restart_migrates_config_in_the_service_user_directory(self):
        self.materialize()
        self.layout.config.mkdir(parents=True)
        self.layout.config.chmod(0o555)
        unit = (builder.RUNTIME / "doris-workshop-jupyter.service").read_text()
        setting = next(line.split("=", 2)[2] for line in unit.splitlines()
                       if line.startswith("Environment=JUPYTER_CONFIG_DIR="))
        config_dir = Path(self.case_dir.name) / setting.lstrip("/")
        config_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir = Path(self.case_dir.name) / "restart-runtime"
        legacy = self.layout.home / ".ipython"
        runtime_json = Path(self.case_dir.name) / "restart-runtime.json"
        runtime_json.write_text(json.dumps(self.plan))
        server_env = {**os.environ, "BROWSER": "/usr/bin/true", "HOME": str(self.layout.home),
                      "IPYTHONDIR": str(legacy), "JUPYTER_CONFIG_DIR": str(config_dir),
                      "JUPYTER_DATA_DIR": str(self.layout.home / "jupyter-data")}
        server_env.pop("JUPYTER_RUNTIME_DIR", None)  # Exercise the CLI's runtime-dir override.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        credentials = []
        try:
            for generation in range(2):
                if generation:
                    # Starting a course kernel creates this directory after the
                    # first server launch. The next launch must be able to mark
                    # migration without writing to root-owned /etc configuration.
                    legacy.mkdir(parents=True, exist_ok=True)
                    (config_dir / "migrated").unlink(missing_ok=True)
                log = Path(self.case_dir.name) / f"restart-{generation}.log"
                with log.open("w") as stream:
                    process = subprocess.Popen([sys.executable, "-I", str(builder.RUNTIME / "serve.py"),
                        "--runtime-json", str(runtime_json), "--workspace", str(self.layout.workspace),
                        "--runtime-dir", str(runtime_dir), "--kernel-dir", str(self.layout.kernels),
                        "--port", str(port)], stdout=stream, stderr=stream, env=server_env)
                    try:
                        url = f"http://127.0.0.1:{port}/api/status"
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline and process.poll() is None:
                            if check.jupyter_ready(runtime_dir / "token", url):
                                break
                            time.sleep(0.2)
                        self.assertTrue(check.jupyter_ready(runtime_dir / "token", url), log.read_text())
                        credentials.append(((runtime_dir / "token").read_bytes(),
                                            (runtime_dir / "cookie-secret").read_bytes()))
                        if generation:
                            self.assertTrue((config_dir / "migrated").is_file())
                        self.assertFalse((self.layout.config / "migrated").exists())
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
            self.assertNotEqual(credentials[0][0], credentials[1][0])
            self.assertNotEqual(credentials[0][1], credentials[1][1])
        finally:
            self.layout.config.chmod(0o755)

    @unittest.skipUnless(LIVE, "Install runtime/test-requirements.txt with Python 3.12 for live server tests")
    def test_real_loopback_server_requires_token_and_rotates_credentials(self):
        self.materialize()
        runtime_dir = Path(self.case_dir.name) / "sessions"
        first = serve.make_config(self.plan, self.layout.workspace, runtime_dir, self.layout.kernels)
        old_token = (runtime_dir / "token").read_text()
        second = serve.make_config(self.plan, self.layout.workspace, runtime_dir, self.layout.kernels)
        self.assertNotEqual(old_token, (runtime_dir / "token").read_text())
        self.assertEqual(first.ServerApp.ip, "127.0.0.1")
        self.assertFalse(second.ServerApp.disable_check_xsrf)
        self.assertFalse(second.ServerApp.use_redirect_file)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        runtime_json = Path(self.case_dir.name) / "runtime.json"
        runtime_json.write_text(json.dumps(self.plan))
        log = Path(self.case_dir.name) / "server.log"
        server_env = {**os.environ, "BROWSER": "/usr/bin/true",
                      "JUPYTER_CONFIG_DIR": str(Path(self.case_dir.name) / "jupyter-config"),
                      "JUPYTER_DATA_DIR": str(Path(self.case_dir.name) / "jupyter-data"),
                      "JUPYTER_RUNTIME_DIR": str(runtime_dir)}
        with log.open("w") as stream:
            process = subprocess.Popen([sys.executable, "-I", str(builder.RUNTIME / "serve.py"),
                "--runtime-json", str(runtime_json), "--workspace", str(self.layout.workspace),
                "--source", str(self.layout.source),
                "--runtime-dir", str(runtime_dir), "--kernel-dir", str(self.layout.kernels), "--port", str(port)],
                stdout=stream, stderr=stream, env=server_env)
            try:
                url = f"http://127.0.0.1:{port}/api/status"
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and process.poll() is None:
                    if check.jupyter_ready(runtime_dir / "token", url):
                        break
                    time.sleep(0.2)
                self.assertTrue(check.jupyter_ready(runtime_dir / "token", url), log.read_text())
                token = (runtime_dir / "token").read_text()
                request = urllib.request.Request(f"http://127.0.0.1:{port}/api/kernelspecs",
                                                 headers={"Authorization": "token " + token})
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
                    self.assertEqual(set(json.load(response)["kernelspecs"]), {"doris-rta", "doris-obs"})
                self.assertNotIn(token, log.read_text())
                self.assertNotIn("not recognized by", log.read_text())
                try:
                    self.verify_portal_and_protected_endpoints(port, token, runtime_dir / "token")
                except Exception as exc:
                    self.fail(f"{type(exc).__name__}: {log.read_text()}")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def verify_portal_and_protected_endpoints(self, port, token, token_file):
        from tornado.httpclient import HTTPRequest, HTTPClientError
        from tornado.websocket import websocket_connect

        base = f"http://127.0.0.1:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), check.NoRedirect())

        def request(path, *, auth=True, method="GET", body=None):
            headers = {"Authorization": "token " + token} if auth else {}
            req = urllib.request.Request(base + path, headers=headers, method=method, data=body)
            return opener.open(req, timeout=10)

        for path in ("/api/contents", "/api/kernels", "/api/sessions", "/api/terminals", "/workshop/",
                     "/workshop/content/doris-course/01-real-time-analytics/course.md", "/workshop/terminal"):
            with self.subTest(unauthenticated=path), self.assertRaises(urllib.error.HTTPError) as failure:
                request(path, auth=False)
            self.assertIn(failure.exception.code, (302, 401, 403))
        for path in ("/api/kernels", "/api/terminals", "/workshop/terminal"):
            with self.subTest(unauthenticated_post=path), self.assertRaises(urllib.error.HTTPError) as failure:
                request(path, auth=False, method="POST", body=b"{}")
            self.assertIn(failure.exception.code, (401, 403))
        with request("/workshop/") as response:
            page = response.read().decode()
            self.assertIn("doris-rta", page)
            self.assertIn("/lab/tree/doris-course/01-real-time-analytics/" + self.lab, page)
            self.assertNotIn("certificate", page)
            self.assertNotIn("fetch(", page)
        self.assertTrue(check.portal_ready(token_file, base + "/workshop/"))
        lesson = self.layout.source / "doris-course/01-real-time-analytics/course.md"
        (lesson.parent / "image.png").write_bytes(b"fixture-image")
        lesson.write_text(f'# Lesson\n[Lab]({self.lab}?x=1#cell)\n![Image](image.png)\n<script>alert(1)</script>')
        with request("/workshop/content/doris-course/01-real-time-analytics/course.md") as response:
            page = response.read().decode()
            self.assertIn("?x=1#cell", page)
            self.assertIn("/workshop/content/doris-course/01-real-time-analytics/image.png", page)
            self.assertNotIn("<script>", page)
        with request("/workshop/content/doris-course/01-real-time-analytics/image.png") as response:
            self.assertEqual(response.read(), b"fixture-image")
        with request("/api/contents/doris-course/01-real-time-analytics/" + self.lab) as response:
            self.assertEqual(json.load(response)["content"]["metadata"]["kernelspec"]["name"], "doris-rta")
        with request("/workshop/terminal") as response:
            self.assertIn('href="/lab"', response.read().decode())
        with request("/api/terminals", method="POST", body=b"{}") as response:
            terminal = json.load(response)["name"]
        # A real kernel and terminal ensure rejection is authentication, not a
        # missing resource or an invalid websocket handshake.
        course = self.plan["courses"][0]
        import ipykernel
        env = self.layout.source / course["course_dir"] / ".venv"
        venv.EnvBuilder(with_pip=False).create(env)
        site = next((env / "lib").glob("python*/site-packages"))
        (site / "99-test-dependencies.pth").write_text(str(Path(ipykernel.__file__).resolve().parents[1]) + "\n")
        with request("/api/kernels", method="POST", body=json.dumps({"name": course["kernel"]}).encode()) as response:
            kernel = json.load(response)["id"]

        async def sockets():
            for path in (f"/api/kernels/{kernel}/channels", f"/terminals/websocket/{terminal}"):
                url = base.replace("http:", "ws:") + path
                with self.subTest(websocket=path), self.assertRaises(HTTPClientError) as failure:
                    await websocket_connect(HTTPRequest(url, connect_timeout=5))
                self.assertIn(failure.exception.code, (401, 403))
                connection = await websocket_connect(HTTPRequest(url, headers={"Authorization": "token " + token}, connect_timeout=5))
                connection.close()
        asyncio.run(sockets())
        with request("/files/doris-course/01-real-time-analytics/" + self.lab + "?download=1") as response:
            self.assertEqual(json.load(response)["metadata"]["kernelspec"]["name"], "doris-rta")
        for path in (f"/api/kernels/{kernel}", f"/api/terminals/{terminal}"):
            request(path, method="DELETE").close()
        for path in ("/workshop/content/../../etc/passwd", "/workshop/content/doris-course/01-real-time-analytics/.env"):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                request(path)
            self.assertEqual(failure.exception.code, 404)



if __name__ == "__main__":
    unittest.main()
