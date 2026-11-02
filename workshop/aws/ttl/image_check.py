"""Local, network-isolated image acceptance. Never a production entry point."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import workshop_ttl_runner as runtime
import workshop_ttl as ttl


def command(arguments, **kwargs):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=180, **kwargs)
    if result.returncode:
        raise RuntimeError("offline image command failed: " + arguments[0])
    return result.stdout


@contextmanager
def metadata_fixture():
    """Exercise the installed SDK's real container provider against loopback."""
    from botocore.utils import ContainerMetadataFetcher
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if self.path != "/v2/credentials/image-fixture":
                self.send_error(404)
                return
            body = json.dumps({"AccessKeyId": "fixture-access", "SecretAccessKey": "fixture-secret", "Token": "fixture-token",
                "Expiration": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "AccountId": "000000000000"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # This redirect exists only in this explicitly invoked offline fixture.
        # The production task continues to require the ECS relative-URI path.
        with mock.patch.object(ContainerMetadataFetcher, "IP_ADDRESS", f"127.0.0.1:{server.server_port}"), mock.patch.dict(
                os.environ, {"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/image-fixture"}):
            yield calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def check_credentials():
    from botocore.stub import Stubber
    sys.path.insert(0, str(ROOT / "tools/tests"))
    from test_workshop_ttl import binding
    with metadata_fixture() as calls:
        session = runtime.task_session(binding())
        credentials = session.get_credentials()
        assert credentials.method == "container-role"
        assert credentials.get_frozen_credentials().access_key == "fixture-access"
        sts = session.client("sts")
        with Stubber(sts) as stub:
            stub.add_response("get_caller_identity", {"Account": "000000000000", "Arn": "arn:aws:sts::000000000000:assumed-role/image-fixture/check", "UserId": "fixture"}, {})
            assert sts.get_caller_identity()["Account"] == "000000000000"
            stub.assert_no_pending_responses()
        for key in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_ENDPOINT_URL_STS"):
            before = len(calls)
            with mock.patch.dict(os.environ, {key: "fixture"}):
                try:
                    runtime.task_session(binding())
                except ttl.wc.ConfigError:
                    pass
                else:
                    raise AssertionError("ambient credential or endpoint accepted")
            assert len(calls) == before
        assert calls == ["/v2/credentials/image-fixture"]
    return {"method": "container-role", "metadata_calls": len(calls), "aws_calls": "SDK Stubber only", "ambient_rejections": 4}


def check_native_runtime():
    import importlib
    import socket
    import ssl
    import sysconfig
    manifest = json.loads(Path("/opt/doris-workshop/native-runtime.json").read_text())
    expected = json.loads((ROOT / "workshop/aws/ttl/runtime-packages.json").read_text())["packages"]
    assert {name: row["version"] for name, row in manifest["packages"].items()} == expected
    for filename, row in manifest["files"].items():
        path = Path(filename)
        if "symlink" in row:
            assert path.is_symlink() and os.readlink(path) == row["symlink"], filename
        else:
            assert runtime.file_hash(path) == row["sha256"], filename
    for name in ("sh", "bash", "mount", "umount", "nsenter", "infocmp", "cp", "mv", "tar", "perl", "apt", "apt-get", "dpkg"):
        assert not any((Path(directory) / name).exists() for directory in ("/bin", "/usr/bin", "/sbin", "/usr/sbin")), name
    assert not list(Path("/usr/lib").rglob("libacl.so*")) and not list(Path("/usr/lib").rglob("libmount.so*"))
    extensions = sorted(path.name.split(".", 1)[0] for path in Path(sysconfig.get_config_var("DESTSHARED")).glob("*.so"))
    for name in extensions:
        importlib.import_module(name)
    assert socket.getaddrinfo("localhost", 443)
    authorities = ssl.create_default_context().cert_store_stats()["x509_ca"]
    assert authorities > 0
    return {"packages": expected, "payload_files_verified": len(manifest["files"]),
        "stdlib_extensions_imported": len(extensions), "certificate_authorities": authorities, "os_tools_absent": True}


def check_runtime(source_sha):
    assert platform.system() == "Linux" and platform.machine() == "x86_64"
    assert os.getuid() == os.getgid() == 10001
    assert os.statvfs("/").f_flag & os.ST_RDONLY
    assert "CapEff:\t0000000000000000" in Path("/proc/self/status").read_text()
    assert os.environ["TMPDIR"] == "/work/tmp" and tempfile.gettempdir() == "/work/tmp"
    assert not any((ROOT / name).exists() for name in (".git", "plan-doc", "workshop/aws/.local"))
    source = json.loads(Path("/opt/doris-workshop/source.json").read_text())
    assert source["source_sha"] == source_sha and source["source_tree_sha256"] == runtime.tree_hash(ROOT)
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "scratch").write_text("local-only")
    wheels = json.loads((ROOT / "workshop/aws/ttl/wheels.json").read_text())["wheels"]
    # pip check runs before the installer is removed during the build. Require
    # the final installed distribution inventory to match the reviewed closure.
    installed = {dist.metadata["Name"].lower().replace("_", "-"): dist.version
        for dist in importlib.metadata.distributions()}
    assert installed == {row["package"]: row["version"] for row in wheels}
    assert importlib.util.find_spec("pip") is None
    native = check_native_runtime()
    version = json.loads(command(["terraform", "version", "-json"]))
    assert version["terraform_version"] == "1.17.1" and version["platform"] == "linux_amd64"
    # The schema command reads backend state. Use an independent local-only
    # fixture, then validate the original S3 modules without initializing S3.
    with tempfile.TemporaryDirectory(prefix="schema-") as directory:
        fixture = Path(directory)
        (fixture / "main.tf.json").write_text(json.dumps({"terraform": {"required_providers": {
            "aws": {"source": "hashicorp/aws", "version": "= 6.71.0"}}}}))
        (fixture / ".terraform.lock.hcl").write_bytes((ROOT / "workshop/aws/terraform/pilot/.terraform.lock.hcl").read_bytes())
        env = {**os.environ, "TF_DATA_DIR": str(fixture / "data"), "TF_INPUT": "0"}
        command(["terraform", "-chdir=" + directory, "init", "-input=false", "-lockfile=readonly"], env=env)
        schema = json.loads(command(["terraform", "-chdir=" + directory, "providers", "schema", "-json"], env=env))
        provider = schema["provider_schemas"]["registry.terraform.io/hashicorp/aws"]
        assert {"aws_instance", "aws_lb_listener_rule"} <= set(provider["resource_schemas"])
        del schema, provider
    checks = {}
    for module in ("pilot", "access-bindings"):
        directory = ROOT / "workshop/aws/terraform" / module
        lock = directory / ".terraform.lock.hcl"
        lock_hash = runtime.file_hash(lock)
        with tempfile.TemporaryDirectory(prefix="provider-") as data:
            env = {**os.environ, "TF_DATA_DIR": data, "TF_INPUT": "0"}
            command(["terraform", "-chdir=" + str(directory), "init", "-backend=false", "-input=false", "-lockfile=readonly"], env=env)
            assert json.loads(command(["terraform", "-chdir=" + str(directory), "validate", "-json"], env=env))["valid"]
            checks[module] = {"provider_schema_loaded": True, "valid": True, "lock_sha256": lock_hash}
        assert runtime.file_hash(lock) == lock_hash
    archives = list(Path("/opt/terraform-providers").rglob("*.zip"))
    assert len(archives) == 1
    with archives[0].open("rb") as stream:
        provider_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    assert all('"zh:' + provider_hash + '"' in (ROOT / "workshop/aws/terraform" / module / ".terraform.lock.hcl").read_text()
        for module in checks)
    return {"platform": "linux/amd64", "uid": os.getuid(), "readonly_root": True, "scratch": "/work", "source": source,
        "python": platform.python_version(), "installed_packages": installed, "runtime_installer": False, "native_runtime": native, "dependencies": {name: importlib.metadata.version(name) for name in ("boto3", "botocore", "jsonschema", "PyYAML")},
        "terraform": version["terraform_version"], "provider_zip_sha256": provider_hash, "schema_fixture_backend": "local", "modules": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    report = check_runtime(args.source_sha)
    report["credentials"] = check_credentials()
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
