"""Public bind mounts work with strict umasks without exposing private files."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from doris_course import DorisLab, environment
from doris_course.doris_client import CHECKOUT_STACK
from doris_course.public_stack import prepare_public_checkout


@unittest.skipUnless(os.name == "posix", "POSIX bind-mount permissions")
class PublicCheckoutTests(unittest.TestCase):
    def test_real_checkout_from_a_restrictive_shell_restores_the_private_umask(self):
        with tempfile.TemporaryDirectory() as temporary:
            origin = Path(temporary) / "public-origin"
            origin.mkdir()
            subprocess.run(["git", "init", "-q", str(origin)], check=True)
            (origin / "config.yml").write_text("receivers: {}\n")
            subprocess.run(["git", "-C", str(origin), "add", "config.yml"], check=True)
            subprocess.run(["git", "-C", str(origin), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "Public source"], check=True)
            revision = subprocess.check_output(["git", "-C", str(origin), "rev-parse", "HEAD"], text=True).strip()
            course = Path(temporary) / "course"
            course.mkdir()
            with patch.object(environment, "STACK_REPOSITORY", str(origin)), patch.object(environment, "STACK_COMMIT", revision):
                script = DorisLab(lab_dir=course)._compose_script(
                    "mkdir -p .runtime/doris-logs/fe\n" + CHECKOUT_STACK + "\nprintf private > private-after.env\n")
            result = subprocess.run(["bash", "-c", "umask 077\n" + script], cwd=course, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            checkout = course / ".runtime/ai-observe-stack"
            self.assertEqual(stat.S_IMODE((checkout / "config.yml").stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(checkout.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((course / "private-after.env").stat().st_mode), 0o600)

    def test_repair_tracked_configs_and_scripts_preserves_private_files_and_umask(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "stack"
            root.mkdir(mode=0o700)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            config = root / "grafana" / "grafana.ini"
            config.parent.mkdir(mode=0o700)
            config.write_text("[server]\nhttp_port=3000\n")
            config.chmod(0o600)
            script = root / "start.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o700)
            private = root / "private-notes.env"
            private.write_text("private fixture\n")
            private.chmod(0o600)
            external = Path(temporary) / "external.env"
            external.write_text("external fixture\n")
            external.chmod(0o600)
            (root / "external-link").symlink_to(external)
            subprocess.run(["git", "-C", str(root), "add", "grafana/grafana.ini", "start.sh", "external-link"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "Public checkout"], check=True)
            # A locally staged private file is absent from the pinned HEAD.
            subprocess.run(["git", "-C", str(root), "add", "private-notes.env"], check=True)
            (root / ".git").chmod(0o700)
            previous = os.umask(0o077)
            try:
                prepare_public_checkout(root)
                # A container can replace an already-readable tracked flag
                # file with its own UID. A rerun must not require chmod then.
                with patch.object(Path, "chmod", side_effect=PermissionError("foreign UID")) as chmod:
                    prepare_public_checkout(root)
                    chmod.assert_not_called()
                self.assertEqual(os.umask(0o077), 0o077)
            finally:
                os.umask(previous)
            for path in (root, config.parent, script):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o644)
            for path in (private, external):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(config.read_text(), "[server]\nhttp_port=3000\n")
            self.assertEqual(script.read_text(), "#!/bin/sh\nexit 0\n")
            self.assertTrue((root / "external-link").is_symlink())
            self.assertEqual(stat.S_IMODE((root / ".git").stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
