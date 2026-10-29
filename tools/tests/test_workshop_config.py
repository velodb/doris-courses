"""Offline configuration, pinned content and ownership checks for AWS workshops."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

import yaml
from jsonschema import Draft202012Validator

TOOL = Path(__file__).resolve().parents[1] / "workshop_config.py"
spec = importlib.util.spec_from_file_location("workshop_config", TOOL)
wc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wc)
NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


class WorkshopConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use HEAD so this suite also runs in a shallow CI checkout. Operators
        # must have the exact configured commit locally; the CLI never fetches it.
        cls.ref = wc.git(wc.REPO, "rev-parse", "HEAD").strip()

    def setUp(self):
        self.config = wc.load_document(wc.SPEC / "examples/event.example.yaml")
        self.config["course_ref"] = self.ref

    def validate(self, config=None):
        return wc.validate_config(self.config if config is None else config, now=NOW)

    def manifest(self, config=None, **kwargs):
        return wc.build_manifest(*self.validate(config), now=NOW, **kwargs)

    def test_schemas_and_profile_enums_are_consistent(self):
        for name in ("config.schema.json", "manifest.schema.json"):
            schema = json.loads((wc.SPEC / name).read_text())
            Draft202012Validator.check_schema(schema)
        profiles = json.loads((wc.SPEC / "profiles.json").read_text())["profiles"]
        config = json.loads((wc.SPEC / "config.schema.json").read_text())["properties"]
        self.assertEqual(set(profiles), set(config["course_profiles"]["items"]["enum"]))
        for profile in profiles.values():
            self.assertLessEqual(set(profile["requires"] + profile["conflicts"]), set(profiles))

    def test_base_example_resolves_pinned_files_and_runtime(self):
        config, profiles, runtime = self.validate()
        self.assertEqual(config["expires_at"], "2030-01-01T04:00:00Z")
        self.assertEqual(len(profiles["rta-main"]["modules"]), 10)
        self.assertEqual(profiles["rta-main"]["kernel"], "doris-rta")
        self.assertEqual(runtime["architecture"], "x86_64")
        self.assertTrue(profiles["rta-main"]["working_directory"].endswith("/01-real-time-analytics"))

    def test_prebuilt_requires_matching_release_metadata(self):
        config = wc.load_document(wc.SPEC / "examples/prebuilt.example.yaml")
        config["course_ref"] = self.ref
        config["image"]["course_ref"] = self.ref
        self.validate(config)
        for key, value in (("course_ref", "f" * 40), ("course_profiles", ["dw-main"])):
            bad = copy.deepcopy(config)
            bad["image"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(wc.ConfigError, "image\\."):
                self.validate(bad)
        del config["image"]["release_ref"]
        with self.assertRaisesRegex(wc.ConfigError, "required"):
            self.validate(config)

    def test_bad_shapes_types_and_secret_fields_are_rejected(self):
        cases = [
            ("disk_gib", True), ("disk_gib", 1.0), ("schema_version", 1.0),
            ("spare_count", 1.0), ("spare_count", -1), ("learner_ids", ["s001", "s001"]),
            ("learner_ids", []), ("learner_ids", ["spare-001"]),
            ("learner_ids", ["s001\n"]), ("event_id", "example-rta\n"),
            ("domain", "workshops.example.com\n"),
            ("learner_ids", ["../someone"]), ("aws_account_id", 123456789012),
            ("region", "not-a-region"), ("course_ref", "main"),
            ("course_profiles", ["rta-main", "rta-main"]),
            ("course_profiles", ["hybrid-search-lakehouse"]),
            ("allow_draft_courses", "false"), ("budget_usd", 0),
            ("budget_usd", float("nan")), ("budget_usd", float("inf")),
            ("domain", "https://workshops.example.com"),
            ("progress", True), ("grades", {}), ("certificate", {}),
            ("archive_bucket", "bucket"), ("password", "DO-NOT-PRINT-THIS"),
        ]
        for key, value in cases:
            config = copy.deepcopy(self.config)
            config[key] = value
            with self.subTest(key=key, value_type=type(value).__name__):
                with self.assertRaises(wc.ConfigError) as error:
                    self.validate(config)
                self.assertNotIn("DO-NOT-PRINT-THIS", str(error.exception))
        for config in (None, [], "example", {}):
            with self.subTest(shape=type(config).__name__), self.assertRaises(wc.ConfigError):
                wc.validate_config(config, now=NOW)
        del self.config["auth"]
        with self.assertRaisesRegex(wc.ConfigError, "auth"):
            self.validate()

    def test_expiry_is_a_real_future_instant(self):
        for value in ("2026-10-29T12:00:00Z", "2026-10-29T19:59:59+08:00",
                      "2026-10-29T12:00:00", "2030-02-30T00:00:00Z",
                      "2030-01-01T00:00:00+25:00"):
            self.config["expires_at"] = value
            with self.subTest(value=value), self.assertRaisesRegex(wc.ConfigError, "expires_at"):
                self.validate()

    def test_image_architecture_region_and_mode(self):
        for changes in ({"architecture": "arm64"}, {"region": "us-east-1"},
                        {"mode": "prebuilt"}, {"ami_id": "latest"},
                        {"course_ref": self.ref}):
            config = copy.deepcopy(self.config)
            config["image"].update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(wc.ConfigError, "image"):
                self.validate(config)

    def test_partition_region_agreement(self):
        self.config["aws_partition"] = "aws-cn"
        with self.assertRaisesRegex(wc.ConfigError, "aws_partition"):
            self.validate()
        for partition, region in (("aws-cn", "cn-north-1"), ("aws-us-gov", "us-gov-west-1")):
            self.config.update(aws_partition=partition, region=region)
            self.config["image"]["region"] = region
            self.validate()

    def test_optional_profiles_need_parent_and_models_are_exclusive(self):
        for profiles, message in ((["rta-metabase"], "requires rta-main"),
                                  (["dw-cdc"], "requires dw-main"),
                                  (["obs-openai"], "requires obs-main"),
                                  (["obs-main", "obs-openai", "obs-local-model"], "conflicts")):
            self.config["course_profiles"] = profiles
            with self.subTest(profiles=profiles), self.assertRaisesRegex(wc.ConfigError, message):
                self.validate()

    def test_every_available_profile_and_ai_scope(self):
        # Both model paths are resolved separately because they are exclusive.
        registry = json.loads((wc.SPEC / "profiles.json").read_text())["profiles"]
        self.config["allow_draft_courses"] = True
        self.config["course_profiles"] = sorted(set(registry) - {"obs-openai"})
        _, profiles, _ = self.validate()
        self.assertEqual(len(profiles["dw-main"]["modules"]), 14)
        self.assertEqual(len(profiles["obs-main"]["modules"]), 8)
        self.assertEqual(profiles["ai-mcp"]["course_status"], "draft")
        self.assertTrue(profiles["ai-mcp"]["requires_browser_host"])
        ai_ids = {m["id"] for key in ("ai-mcp", "ai-functions") for m in profiles[key]["modules"]}
        self.assertEqual(ai_ids, {"module-1", "module-2"})
        self.config["course_profiles"] = ["obs-main", "obs-openai"]
        self.validate()

    def test_ai_requires_explicit_draft_opt_in(self):
        self.config["course_profiles"] = ["ai-mcp"]
        with self.assertRaisesRegex(wc.ConfigError, "draft opt-in"):
            self.validate()

    def test_pinned_commit_must_exist_locally(self):
        self.config["course_ref"] = "f" * 40
        with self.assertRaisesRegex(wc.ConfigError, "unavailable locally"):
            self.validate()

    def test_metadata_is_read_from_commit_not_working_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            course = {"status": "published", "modules": [{
                "id": "module-1", "required": True, "status": "published",
                "course": "course.md", "lab": "lab.ipynb", "quiz": "quiz.yaml",
            }]}
            directory = repo / "doris-course/01-real-time-analytics"
            directory.mkdir(parents=True)
            (directory / "course.yaml").write_text(yaml.safe_dump(course))
            for name in ("course.md", "lab.ipynb", "quiz.yaml"):
                (directory / name).write_text("fixture\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.com", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "Fixture"], check=True)
            self.config["course_ref"] = wc.git(repo, "rev-parse", "HEAD").strip()
            (directory / "course.yaml").write_text("invalid working tree\n")
            wc.validate_config(self.config, now=NOW, repo=repo)
            course["modules"][0]["status"] = "planned"
            (directory / "course.yaml").write_text(yaml.safe_dump(course))
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.com", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "Planned"], check=True)
            self.config["course_ref"] = wc.git(repo, "rev-parse", "HEAD").strip()
            with self.assertRaisesRegex(wc.ConfigError, "planned"):
                wc.validate_config(self.config, now=NOW, repo=repo)
            del course["modules"][0]["id"]
            (directory / "course.yaml").write_text(yaml.safe_dump(course))
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.com", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "Missing ID"], check=True)
            self.config["course_ref"] = wc.git(repo, "rev-parse", "HEAD").strip()
            with self.assertRaisesRegex(wc.ConfigError, "stable string IDs"):
                wc.validate_config(self.config, now=NOW, repo=repo)

    def test_roster_reorder_add_remove_and_spare_identity(self):
        self.config.update(learner_ids=["s002", "s001"], spare_count=2)
        first = self.manifest()
        self.config["learner_ids"].reverse()
        reordered = self.manifest()
        self.assertEqual(first, reordered)
        self.config.update(learner_ids=["s002", "s003"], spare_count=1)
        changed = self.manifest(previous=first)
        for key in ("s002", "spare-001"):
            self.assertEqual(first["allocations"][key], changed["allocations"][key])
        for key in ("s001", "spare-002"):
            self.assertFalse(changed["allocations"][key]["active"])
            self.assertEqual(first["allocations"][key]["owner_key"], changed["allocations"][key]["owner_key"])
        self.assertTrue(changed["allocations"]["s003"]["active"])
        self.assertEqual(first["state"], changed["state"])

    def test_manifest_versions_and_no_claim_of_cloud_readiness(self):
        result = self.manifest()
        self.assertEqual(result["versions"]["course_ref"], self.ref)
        self.assertEqual(result["versions"]["deployment"]["git_sha"], self.ref)
        self.assertEqual(result["desired"]["image"], self.config["image"])
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["allocations"]["s001"]["owner_key"], "example-rta/s001")
        self.assertNotIn("aws_profile", json.dumps(result))
        self.assertNotIn("instance_id", json.dumps(result))

    def test_previous_event_and_ownership_cannot_be_reassigned(self):
        original = self.manifest()
        for field, replacement in (("aws_account_id", "111111111111"), ("aws_partition", "aws-cn"),
                                   ("region", "us-east-1"), ("event_id", "another"), ("owner_id", "other-team")):
            previous = copy.deepcopy(original)
            previous["event"][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(wc.ConfigError, "must match"):
                self.manifest(previous=previous)
        previous = copy.deepcopy(original)
        previous["allocations"]["s001"]["owner_key"] = "someone/s001"
        with self.assertRaisesRegex(wc.ConfigError, "ownership"):
            self.manifest(previous=previous)

    def test_input_duplicates_invalid_yaml_and_dates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.yaml"
            for content in ("event_id: a\nevent_id: b\n", '{"event_id":"a","event_id":"b"}',
                            "a: [\nDO-NOT-PRINT-THIS", "[1]: value\n", "password: !unsafe value\n"):
                path.write_text(content)
                with self.subTest(content_type=content[:1]), self.assertRaises(wc.ConfigError) as error:
                    wc.load_document(path)
                self.assertNotIn("DO-NOT-PRINT-THIS", str(error.exception))
            path.write_text("expires_at: 2030-01-01T04:00:00Z\n")
            self.assertIsInstance(wc.load_document(path)["expires_at"], str)

    def test_cli_private_output_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "event.yaml"
            output = Path(temp) / "manifest.json"
            path.write_text(yaml.safe_dump(self.config))
            command = [sys.executable, str(TOOL), "manifest", "--config", str(path), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no cloud resources", result.stdout)
            original = output.read_bytes()
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("already exists", result.stderr)
            self.assertEqual(output.read_bytes(), original)
            self.config["password"] = "DO-NOT-PRINT-THIS"
            path.write_text(yaml.safe_dump(self.config))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("DO-NOT-PRINT-THIS", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
