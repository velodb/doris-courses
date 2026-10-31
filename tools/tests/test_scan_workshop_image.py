from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scan_workshop_image as scan


class ScanTests(unittest.TestCase):
    def reports(self):
        return {"Results": [{"Class": "os-pkgs", "Type": "debian", "Packages": [{"Name": "libc6"}]},
            {"Type": "python-pkg", "Packages": [{"Name": "boto3"}]},
            {"Type": "gobinary", "Packages": [{"Name": "github.com/hashicorp/terraform"}]}]}, {
            "Results": [{"Type": "gobinary", "Packages": [{"Name": "github.com/hashicorp/terraform-provider-aws"}]}]}

    def test_empty_or_unexpanded_provider_cannot_look_clean(self):
        image, provider = self.reports()
        self.assertFalse(scan.summarize(image, provider, ["boto3"])["review_required"])
        with self.assertRaises(ValueError):
            scan.summarize(image, {"Results": []}, ["boto3"])
        with self.assertRaises(ValueError):
            scan.summarize(image, provider, ["boto3", "botocore"])
        with self.assertRaises(ValueError):
            scan.summarize(image, provider, [])

    def test_unfixed_critical_and_unknown_remain_in_review_gate(self):
        image, provider = self.reports()
        image["Results"][0]["Vulnerabilities"] = [{"VulnerabilityID": "CVE-fixture", "Severity": "CRITICAL", "Status": "affected"}]
        provider["Results"][0]["Vulnerabilities"] = [{"VulnerabilityID": "GO-fixture", "Severity": "UNKNOWN"}]
        report = scan.summarize(image, provider, ["boto3"])
        self.assertTrue(report["review_required"])
        self.assertEqual(report["package_findings"], {"CRITICAL": 1, "UNKNOWN": 1})
        self.assertFalse(report["cloud_accepted"])

    def test_stale_or_future_database_fails_closed(self):
        now = datetime(2030, 1, 2, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "db").mkdir()
            (cache / "db/trivy.db").write_bytes(b"fixture")
            for timestamp in ("2029-12-30T00:00:00Z", "2030-01-03T00:00:00Z"):
                (cache / "db/metadata.json").write_text(json.dumps({"Version": 2, "UpdatedAt": timestamp}))
                with self.assertRaises(ValueError):
                    scan.database_metadata(cache, now=now)


if __name__ == "__main__":
    unittest.main()
