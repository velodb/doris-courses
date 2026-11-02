import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_ttl_image as image


class ImageTests(unittest.TestCase):
    def test_native_payload_selection_rejects_missing_changed_or_duplicate_packages(self):
        spec = importlib.util.spec_from_file_location("rootfs_fixture", image.ROOT / "workshop/aws/ttl/runtime_rootfs.py")
        rootfs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rootfs)
        status = "Package: libc6\nStatus: install ok installed\nVersion: 1.0\nSource: glibc\n"
        selected = rootfs.select_packages(status + "\nPackage: mount\nVersion: 1.0\n", {"libc6": "1.0"})
        self.assertEqual(set(selected), {"libc6"})
        self.assertIn("Source: glibc", selected["libc6"][1])
        for value, expected in ((status, {"libc6": "2.0"}), (status, {"libc6": "1.0", "libssl3t64": "1.0"}),
                (status + "\n" + status, {"libc6": "1.0"}),
                (status.replace("install ok installed", "deinstall ok config-files"), {"libc6": "1.0"})):
            with self.assertRaises(ValueError):
                rootfs.select_packages(value, expected)

    def test_build_context_uses_only_the_selected_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = root / "workshop/aws/ttl/images.json"
            spec.parent.mkdir(parents=True)
            spec.write_bytes((image.ROOT / "workshop/aws/ttl/images.json").read_bytes())
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            original = json.loads(spec.read_text())
            spec.write_text("uncommitted PRIVATE-SENTINEL")
            (root / "private.tfstate").write_text("PRIVATE-SENTINEL")
            real_run = subprocess.run
            seen = []
            def process(args, **kwargs):
                if args[:3] == ["docker", "buildx", "build"]:
                    with tarfile.open(fileobj=kwargs["stdin"], mode="r|") as archive:
                        names = []
                        for member in archive:
                            names.append(member.name)
                            if member.name.endswith("images.json"):
                                self.assertEqual(json.load(archive.extractfile(member)), original)
                        self.assertNotIn("private.tfstate", names)
                    seen.append(args)
                    return subprocess.CompletedProcess(args, 0)
                if args[:3] == ["docker", "image", "inspect"]:
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps([{"Id": "sha256:" + "a" * 64}]))
                return real_run(args, **kwargs)
            with mock.patch.object(image, "ROOT", root), mock.patch("subprocess.run", side_effect=process):
                report = image.build(sha, "doris-workshop-ttl:fixture", root / "result.json")
            self.assertEqual(report["source_sha"], sha)
            self.assertFalse(report["pushed"])
            self.assertEqual(seen[0][-1], "-")
            self.assertIn("linux/amd64", seen[0])
            self.assertNotIn("--secret", seen[0])

    def test_mutable_or_foreign_base_and_noncommit_source_are_rejected(self):
        with self.assertRaises(ValueError):
            image.source_images("HEAD")
        source = json.loads((image.ROOT / "workshop/aws/ttl/images.json").read_text())
        for name, value in (("python", "docker.io/library/python:latest"), ("terraform", "example.invalid/terraform@sha256:" + "a" * 64)):
            changed = {**source, name: {"image": value}}
            with mock.patch.object(image, "run", return_value=json.dumps(changed)), self.assertRaises(ValueError):
                image.source_images("a" * 40)

    def test_installed_container_provider_uses_loopback_fixture_and_rejects_ambient_paths(self):
        spec = importlib.util.spec_from_file_location("image_contract_fixture", image.ROOT / "workshop/aws/ttl/image_check.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        with tempfile.TemporaryDirectory() as home, mock.patch.dict("os.environ", {"HOME": home, "AWS_EC2_METADATA_DISABLED": "true"}, clear=True):
            report = checker.check_credentials()
        self.assertEqual(report["method"], "container-role")
        self.assertEqual(report["metadata_calls"], 1)
        self.assertEqual(report["ambient_rejections"], 4)

    def test_cached_provider_cannot_bypass_either_committed_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "registry.terraform.io/hashicorp/aws/terraform-provider-aws_6.71.0_linux_amd64.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"unreviewed-provider")
            lock = 'version = "6.71.0"\nhashes = ["zh:' + "a" * 64 + '"]'
            with mock.patch.object(image, "run", return_value=lock), self.assertRaises(ValueError):
                image.provider_context("a" * 40, root, root / "build")
            self.assertFalse((root / "build").exists())

    def test_wheel_hash_and_direct_dependency_pins_are_bound_to_source(self):
        import re
        base = image.ROOT / "workshop/aws/ttl"
        wheels = json.loads((base / "wheels.json").read_text())["wheels"]
        pins = {row["package"]: row["version"] for row in wheels}
        lock = (base / "requirements.lock").read_text()
        for row in wheels:
            self.assertIn(f'{row["package"]}=={row["version"]} --hash=sha256:{row["sha256"]}', lock)
            self.assertTrue(row["url"].startswith("https://files.pythonhosted.org/"))
        for path in (image.ROOT / "tools/requirements.txt", base / "requirements.txt"):
            for package, version in re.findall(r"^([A-Za-z0-9_-]+)==([^\s]+)$", path.read_text(), re.MULTILINE):
                self.assertEqual(pins[package.lower().replace("_", "-")], version)
        spec = {"schema_version": 1, "platform": "linux/amd64", "wheels": [{"name": "bad.whl", "size": 3, "sha256": "a" * 64}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.whl").write_bytes(b"bad")
            with mock.patch.object(image, "run", return_value=json.dumps(spec)), self.assertRaises(ValueError):
                image.wheel_context("a" * 40, root, root / "context")


if __name__ == "__main__":
    unittest.main()
