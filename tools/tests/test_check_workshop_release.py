import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_workshop_release as release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.matrix = json.loads((release.ROOT / "workshop/aws/support-matrix.json").read_text())
        self.profiles = json.loads((release.ROOT / "workshop/aws/profiles.json").read_text())

    def test_profile_coverage_and_actual_transport_bound_claims(self):
        self.assertEqual(release.validate(self.matrix, self.profiles), [])
        self.matrix["profiles"]["rta-main"]["shared_browser"] = "verified"
        self.assertTrue(any("SSM evidence" in error for error in release.validate(self.matrix, self.profiles)))
        del self.matrix["profiles"]["dw-main"]
        self.assertTrue(release.validate(self.matrix, self.profiles))

    def test_unverified_profiles_cannot_acquire_evidence_or_private_summaries(self):
        self.matrix["profiles"]["dw-main"]["evidence"] = copy.deepcopy(self.matrix["profiles"]["rta-main"]["evidence"])
        self.assertTrue(any("unverified profile" in error for error in release.validate(self.matrix, self.profiles)))
        self.matrix["profiles"]["dw-main"].update(evidence=None, summary="../../plan-doc/private.md")
        self.assertTrue(any("public validation summary" in error for error in release.validate(self.matrix, self.profiles)))

    def test_existing_untracked_document_is_not_a_public_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs = root / "workshop/aws"
            docs.mkdir(parents=True)
            (docs / "README.md").write_text("[private](.local/result.md)\n")
            (docs / ".local").mkdir()
            (docs / ".local/result.md").write_text("private")
            course = root / "doris-course/example"
            course.mkdir(parents=True)
            (course / "local.py").write_text("import boto3\n")
            tracked = {"workshop/aws/README.md", "doris-course/example/local.py"}
            errors = release.public_boundaries(root, tracked)
            self.assertEqual(len(errors), 1)
            self.assertIn("outside tracked source", errors[0])
            (docs / "README.md").write_text("public")
            (course / "local.py").write_text("from workshop_preflight import Aws\n")
            self.assertIn("AWS operator tool", release.public_boundaries(root, tracked)[0])


if __name__ == "__main__":
    unittest.main()
