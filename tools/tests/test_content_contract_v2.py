"""Cross-version, publication and hostile-input tests for the pinned contract."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft7Validator
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import content_contract_v2 as c


class V2AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "content"
        shutil.copytree(c.CONTRACT / "examples", self.root)
        self.package = json.loads((self.root / "package.v2.json").read_text())

    def write(self, seal=True):
        value = c.seal(self.package) if seal else self.package
        (self.root / "package.v2.json").write_text(json.dumps(value, ensure_ascii=False))

    def problems(self, seal=True):
        self.write(seal)
        return "\n".join(c.validate(self.root, allow_fixture=True)[1])

    def change_file(self, relative, value):
        target = self.root / relative
        if isinstance(value, str):
            target.write_text(value)
        elif relative.endswith(".yaml"):
            target.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True))
        else:
            target.write_text(json.dumps(value, ensure_ascii=False))
        record = {"path": relative, "size": len(target.read_bytes()), "sha256": c.sha256(target.read_bytes())}
        for item in self.package["snapshot"]["files"]:
            if item["path"] == relative:
                item.update(record)
        for asset in self.package["assets"]:
            if asset["file"]["path"] == relative:
                asset["file"].update(record)

    def test_pinned_schemas_and_complete_bilingual_example(self):
        Draft7Validator.check_schema(c.SCHEMA)
        c.validate_registry()
        report, errors = c.validate(self.root, allow_fixture=True)
        self.assertEqual(errors, [])
        self.assertEqual(report["available_locales"], ["en", "kk-Cyrl"])

    def test_fixture_requires_explicit_opt_in(self):
        self.assertIn("contract fixture", c.validate(self.root)[1][0])

    def test_cli_exit_status(self):
        script = Path(c.__file__).with_name("check_content_contract_v2.py")
        for flags, status in [([], 1), (["--allow-fixture"], 0)]:
            result = subprocess.run([sys.executable, str(script), "--root", str(self.root), *flags],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, status, result.stdout + result.stderr)

    def test_unknown_fields_at_every_object_boundary(self):
        locations = [(), ("snapshot",), ("courses", 0), ("variants", 0), ("profiles", 0),
                     ("source_units", 0), ("source_units", 0, "locator"), ("translations", 0),
                     ("translations", 0, "units", 0), ("translations", 0, "units", 0, "checks"),
                     ("assets", 0), ("quizzes", 0), ("quizzes", 0, "questions", 0)]
        for location in locations:
            with self.subTest(location=location):
                before = copy.deepcopy(self.package)
                target = self.package
                for key in location:
                    target = target[key]
                target["hook"] = "run.py"
                self.assertIn("Additional properties", self.problems())
                self.package = before

    def test_wrong_contract_versions(self):
        for key, value in [("schema_version", 1), ("schema_version", True), ("contract_version", "3.0.0")]:
            with self.subTest(key=key, value=value):
                before = self.package[key]
                self.package[key] = value
                self.assertTrue(self.problems())
                self.package[key] = before

    def test_trailing_newlines_cannot_bypass_identity_or_digest_patterns(self):
        for field in ["id", "source_hash"]:
            before = self.package["source_units"][0][field]
            self.package["source_units"][0][field] += "\n"
            self.assertTrue(self.problems())
            self.package["source_units"][0][field] = before
        self.package["snapshot"]["source_ref"] += "\n"
        self.assertTrue(self.problems())

    def test_duplicate_locator_cannot_create_a_second_learning_unit(self):
        duplicate = copy.deepcopy(self.package["source_units"][0])
        duplicate["id"] = "other.id"
        self.package["source_units"].append(duplicate)
        self.assertIn("duplicate source locator", self.problems())

    def test_registry_aliases_are_only_for_adapters(self):
        self.assertEqual(c.normalize_locale("KK-kz"), "kk-Cyrl")
        self.assertEqual(c.normalize_locale("EN-us"), "en")
        for tag in ["kk-KZ", "fr", "KK-cYRL"]:
            self.package["default_locale"] = tag
            self.assertTrue(self.problems())

    def test_registry_alias_collisions_rejected(self):
        for alias in ["en", "en-us", "kk-cyrl"]:
            registry = copy.deepcopy(c.REGISTRY)
            registry["locales"][1]["aliases"].append(alias)
            with self.assertRaises(c.AdmissionError):
                c.validate_registry(registry)

    def test_duplicate_stable_ids(self):
        for key in ["courses", "source_units", "assets", "quizzes", "profiles", "variants", "translations"]:
            with self.subTest(key=key):
                before = copy.deepcopy(self.package)
                self.package[key].append(copy.deepcopy(self.package[key][0]))
                self.assertTrue(self.problems())
                self.package = before

    def test_duplicate_translation_and_quiz_presentation(self):
        self.package["translations"][0]["units"].append(copy.deepcopy(self.package["translations"][0]["units"][0]))
        self.assertIn("duplicate translation unit", self.problems())
        self.package["translations"][0]["units"].pop()
        self.package["quizzes"][0]["presentations"].append(copy.deepcopy(self.package["quizzes"][0]["presentations"][0]))
        self.assertIn("duplicate quiz presentation", self.problems())

    def test_source_text_and_hash_are_checked_against_real_files(self):
        unit = self.package["source_units"][0]
        unit["source_text"] = "Forged source"
        unit["source_hash"] = c.sha256(b"Forged source")
        self.assertIn("wrong source hash/text", self.problems())

    def test_stale_source_target_hash_and_unknown_unit_rejected(self):
        unit = self.package["translations"][0]["units"][0]
        for key, value, expected in [("source_hash", "a" * 64, "stale/wrong"),
                                     ("target_hash", "b" * 64, "wrong target"),
                                     ("unit_id", "unknown", "unknown translation unit")]:
            before = unit[key]
            unit[key] = value
            self.assertIn(expected, self.problems())
            unit[key] = before

    def test_json_pointer_cannot_translate_identity_or_machine_policy(self):
        unit = next(unit for unit in self.package["source_units"] if unit["id"] == "course.title")
        unit.update(locator={"kind": "json-pointer", "value": "/certificate/code"}, source_text="EXAMPLE",
                    source_hash=c.sha256(b"EXAMPLE"))
        self.assertIn("non-translatable", self.problems())

    def test_notebook_code_is_not_a_translation_unit(self):
        unit = next(unit for unit in self.package["source_units"] if unit["id"] == "notebook.intro")
        unit["locator"]["value"] = "probe"
        self.assertIn("Markdown cell ID", self.problems())

    def test_ambiguous_markdown_selector_rejected(self):
        unit = next(unit for unit in self.package["source_units"] if unit["id"] == "lesson.introduction")
        self.change_file(unit["source_path"], unit["source_text"] * 2)
        self.assertIn("ambiguous Markdown block", self.problems())

    def test_default_locale_must_be_available(self):
        self.package["variants"][1]["publication_state"] = "draft"
        self.package["package_state"] = "preview"
        self.package["available_locales"] = ["en"]
        self.package["default_locale"] = "kk-Cyrl"
        self.assertIn("default_locale", self.problems())

    def test_missing_translation_prevents_publication(self):
        self.package["translations"][0]["units"] = []
        self.assertTrue(self.problems())

    def test_unchecked_and_withdrawn_units_prevent_publication(self):
        # This metadata unit is outside the quiz, so publication is the gate.
        unit = self.package["translations"][0]["units"][0]
        for state in ["translated", "withdrawn"]:
            unit["state"] = state
            self.assertIn("unchecked or withdrawn", self.problems())

    def test_draft_preview_exposes_only_admitted_locales(self):
        self.package["package_state"] = "preview"
        self.package["variants"][1]["publication_state"] = "draft"
        self.package["translations"][0]["units"][0]["state"] = "translated"
        self.package["available_locales"] = ["en"]
        self.assertEqual(self.problems(), "")
        self.package["package_state"] = "release"
        self.assertIn("release cannot include draft", self.problems())

    def test_llm_self_check_cannot_claim_native_human_review(self):
        unit = self.package["translations"][0]["units"][0]
        unit["state"] = "reviewed"
        self.assertIn("native human review", self.problems())
        unit["checks"]["native_review"] = copy.deepcopy(unit["checks"]["llm_self_check"])
        self.assertIn("review method/hash binding", self.problems())

    def test_review_evidence_is_bound_to_exact_source_and_target(self):
        unit = self.package["translations"][0]["units"][0]
        unit["checks"]["llm_self_check"]["target_hash"] = "c" * 64
        self.assertIn("review method/hash binding", self.problems())
        unit["checks"]["llm_self_check"] = None
        self.assertIn("requires self-check", self.problems())

    def test_native_review_is_optional_for_llm_checked(self):
        unit = self.package["translations"][0]["units"][0]
        self.assertIsNone(unit["checks"]["native_review"])
        self.assertEqual(self.problems(), "")
        unit["state"] = "reviewed"
        unit["checks"]["native_review"] = {**unit["checks"]["llm_self_check"], "method": "native-human",
                                             "actor": "example-native-reviewer"}
        self.assertEqual(self.problems(), "")

    def test_profile_rejects_draft_course_and_planned_module(self):
        path = self.package["courses"][0]["manifest"]
        original = yaml.safe_load((self.root / path).read_text())
        for kind in ["course", "module"]:
            course = copy.deepcopy(original)
            course["completion"]["enabled"] = course["certificate"]["enabled"] = False
            if kind == "course":
                course["status"] = "draft"
            else:
                course["modules"][0].update(status="planned", required=False, requirements=[])
            self.change_file(path, course)
            self.assertIn("draft/planned/retired", self.problems())

    def test_profile_intersection_includes_every_selected_profile(self):
        # A second profile requires a unit not required by the first one.
        uid = self.package["profiles"][0]["unit_ids"].pop(0)
        profile = copy.deepcopy(self.package["profiles"][0])
        profile.update(id="second-profile", unit_ids=[uid])
        self.package["profiles"].append(profile)
        self.package["translations"][0]["units"][0]["state"] = "translated"
        self.assertEqual(self.problems(), "")
        self.package["selected_profiles"].append("second-profile")
        self.assertIn("unchecked or withdrawn", self.problems())

    def test_missing_localized_assets_prevent_publication(self):
        self.package["assets"] = [asset for asset in self.package["assets"] if asset["id"] != "lesson.kk"]
        self.assertIn("unchecked or withdrawn", self.problems())

    def test_unknown_profile_unit_asset_and_quiz_references(self):
        profile = self.package["profiles"][0]
        for field in ["unit_ids", "asset_ids", "quiz_ids"]:
            before = profile[field]
            profile[field] = ["unknown"]
            self.assertIn("profile references unknown", self.problems())
            profile[field] = before

    def test_quiz_identity_order_answer_and_threshold_are_protected(self):
        quiz = self.package["quizzes"][0]
        for field, value in [("passing_score_percent", 50), ("questions", [])]:
            before = quiz[field]
            quiz[field] = value
            self.assertTrue(self.problems())
            quiz[field] = before
        quiz["questions"][0]["option_ids"].reverse()
        self.assertIn("differ from English", self.problems())
        quiz["questions"][0]["option_ids"].reverse()
        quiz["questions"][0]["answer"] = "locale"
        self.assertIn("differ from English", self.problems())

    def test_localized_notebook_cannot_modify_code_or_cell_ids(self):
        relative = "kk-Cyrl/lab.ipynb"
        nb = json.loads((self.root / relative).read_text())
        nb["cells"][1]["source"] = ["print(999)\n"]
        self.change_file(relative, nb)
        self.assertIn("code/structure", self.problems())

    def test_notebook_structure_comparison_preserves_json_scalar_types(self):
        source_path = "doris-course/example/lab.ipynb"
        target_path = "kk-Cyrl/lab.ipynb"
        source = json.loads((self.root / source_path).read_text())
        source["metadata"]["example_flag"] = 0
        self.change_file(source_path, source)
        target = json.loads((self.root / target_path).read_text())
        target["metadata"]["example_flag"] = False
        self.change_file(target_path, target)
        source_asset = next(a for a in self.package["assets"] if a["id"] == "notebook.en")
        target_asset = next(a for a in self.package["assets"] if a["id"] == "notebook.kk")
        target_asset["source_asset_hash"] = source_asset["file"]["sha256"]
        self.assertIn("code/structure", self.problems())

    def test_localized_quiz_cannot_change_grading(self):
        relative = "kk-Cyrl/quiz.yaml"
        quiz = yaml.safe_load((self.root / relative).read_text())
        quiz["questions"][0]["answer"] = "locale"
        self.change_file(relative, quiz)
        self.assertIn("quiz grading differs", self.problems())

    def test_localized_asset_cannot_disagree_with_overlay(self):
        relative = "kk-Cyrl/quiz.yaml"
        quiz = yaml.safe_load((self.root / relative).read_text())
        quiz["questions"][0]["prompt"] = "Unregistered display text"
        self.change_file(relative, quiz)
        self.assertIn("text differs from its translation units", self.problems())

    def test_code_urls_and_placeholders_are_protected_in_text_units(self):
        pairs = [('Run `SELECT 1`.', '`SELECT 2` орындаңыз.'),
                 ('[Docs](https://example.com/a)', '[Құжат](https://example.com/b)'),
                 ('<a href="guide.md">Guide</a>', '<a href="other.md">Нұсқаулық</a>'),
                 ('Welcome, {learner_name}.', 'Қош келдіңіз, {name}.')]
        for source, target in pairs:
            self.assertNotEqual(c.protected_markup(source), c.protected_markup(target))
        self.assertEqual(c.protected_markup('Run `SELECT 1` for {learner_name}.'),
                         c.protected_markup('{learner_name} үшін `SELECT 1` орындаңыз.'))

    def test_modified_code_in_a_rehashed_translation_is_rejected(self):
        catalog = yaml.safe_load((self.root / "catalog.yaml").read_text())
        catalog["title"] = "Run `SELECT 1` for {name}."
        self.change_file("catalog.yaml", catalog)
        source = self.package["source_units"][0]
        source.update(source_text=catalog["title"], source_hash=c.sha256(catalog["title"].encode()))
        target = self.package["translations"][0]["units"][0]
        target.update(source_hash=source["source_hash"], target_text="{name} үшін `SELECT 2` орындаңыз.")
        target["target_hash"] = c.sha256(target["target_text"].encode())
        for record in target["checks"].values():
            if record:
                record.update(source_hash=target["source_hash"], target_hash=target["target_hash"])
        self.assertIn("translation changed code", self.problems())

    def test_profile_cannot_omit_primary_teaching_assets_or_quiz(self):
        self.package["profiles"][0]["asset_ids"].remove("lesson.en")
        self.assertIn("omits a primary English teaching asset", self.problems())
        self.package["profiles"][0]["asset_ids"].append("lesson.en")
        self.package["profiles"][0]["quiz_ids"] = []
        self.assertIn("omits its primary quiz", self.problems())

    def test_public_presentation_revision_excludes_explanations(self):
        self.package["quizzes"][0]["presentations"][0]["unit_ids"].append("quiz.question.explanation")
        self.assertIn("cannot depend on explanations", self.problems())

    def test_public_quiz_rejects_answers_and_explanations(self):
        quiz = json.loads((c.CONTRACT / "public-quiz.example.json").read_text())
        c.shape(quiz, "public_quiz")
        for key in ["answer", "explanation", "questionDigest", "grading_reference"]:
            quiz["questions"][0][key] = "private"
            with self.assertRaises(c.AdmissionError):
                c.shape(quiz, "public_quiz")
            del quiz["questions"][0][key]

    def test_path_traversal_encoding_and_absolute_paths(self):
        for value in ["../outside.json", "/etc/passwd", "a/../../b", "a/%2e%2e/b", "a/./b", "a\\b", "a//b"]:
            with self.subTest(value=value), self.assertRaises(c.AdmissionError):
                c.safe_path(self.root, value)

    def test_file_and_parent_directory_symlinks_rejected(self):
        path = self.root / "shared/diagram.svg"
        data = path.read_bytes()
        path.unlink()
        outside = self.root.parent / "image.svg"
        outside.write_bytes(data)
        path.symlink_to(outside)
        self.assertIn("symlinks", self.problems())
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(self.root.parent)
        self.assertIn("symlinks", self.problems())

    def test_file_hash_and_inventory_integrity(self):
        (self.root / "shared/diagram.svg").write_text("changed")
        self.assertIn("hash/size mismatch", self.problems())

    def test_file_inventory_must_be_sorted_and_unique(self):
        files = self.package["snapshot"]["files"]
        files.reverse()
        self.assertIn("sorted", self.problems())
        files.reverse()
        files.append(copy.deepcopy(files[0]))
        self.assertIn("duplicate file path", self.problems())

    def test_manifest_cannot_be_in_its_own_file_inventory(self):
        self.package["snapshot"]["files"].append({"path": "package.v2.json", "size": 1, "sha256": "a" * 64})
        self.package["snapshot"]["files"].sort(key=lambda item: item["path"])
        self.assertIn("exclude the package manifest", self.problems())

    def test_unsafe_svg_is_rejected_after_hash_is_recomputed(self):
        for text in ['<svg onload="run()"/>', '<svg><script>run()</script></svg>',
                     '<svg><foreignObject/></svg>', '<svg><use href="https://example.com/a.svg#x"/></svg>',
                     '<!DOCTYPE svg [<!ENTITY x "unsafe">]><svg/>']:
            self.change_file("shared/diagram.svg", text)
            self.assertIn("unsafe SVG", self.problems())

    def test_unsafe_translated_text_is_rejected_after_hash_is_recomputed(self):
        unit = self.package["translations"][0]["units"][0]
        for text in ['<script>run()</script>', '<img src="x" onerror="run()"/>',
                     '[run](javascript:run)', '<iframe src="https://example.com"/>',
                     '[outside](../../%2e%2e/secret)', '<a href="file:///etc/passwd">x</a>']:
            unit["target_text"] = text
            unit["target_hash"] = c.sha256(text.encode())
            self.assertTrue(self.problems())

    def test_fenced_examples_are_inert_text(self):
        c.safe_markdown('```html\n<script>example()</script>\n```\n', "lesson.md", {})

    def test_external_images_cannot_escape_frozen_asset_inventory(self):
        with self.assertRaisesRegex(c.AdmissionError, "frozen local files"):
            c.safe_markdown('![image](https://example.com/mutable.svg)', "lesson.md", {})

    def test_standalone_source_map_and_overlay_shapes(self):
        common = {"schema_version": 2, "contract_version": "2.0.0"}
        c.shape({**common, "kind": "doris-source-map", "units": self.package["source_units"]}, "source_map")
        c.shape({**common, "kind": "doris-locale-overlay", **self.package["translations"][0]}, "locale_overlay")

    def test_duplicate_json_and_yaml_keys_are_rejected(self):
        (self.root / "package.v2.json").write_text('{"schema_version":2,"schema_version":2}')
        self.assertIn("duplicate JSON key", c.validate(self.root, allow_fixture=True)[1][0])
        self.change_file("kk-Cyrl/quiz.yaml", "title: first\ntitle: second\n")
        self.assertIn("duplicate YAML key", self.problems())

    def test_yaml_aliases_and_deep_documents_are_rejected(self):
        self.change_file("kk-Cyrl/quiz.yaml", "title: &x [*x]\n")
        self.assertIn("YAML aliases", self.problems())
        value = []
        for _ in range(70):
            value = [value]
        with self.assertRaises(c.AdmissionError):
            c.read_json(json.dumps(value).encode())

    def test_package_size_limit_is_enforced_before_loading_files(self):
        for item in self.package["snapshot"]["files"]:
            item["size"] = c.MAX_FILE_BYTES
        self.assertIn("128 MiB", self.problems())

    def test_pinned_schema_registry_and_all_digests_are_verified(self):
        for key in ["source_schema_digest", "contract_digest", "registry_digest", "translation_digest",
                    "teaching_digest", "package_digest"]:
            before = copy.deepcopy(self.package)
            self.package["snapshot"][key] = "f" * 64
            self.assertIn("wrong", self.problems(seal=False))
            self.package = before
        self.package["locale_digests"]["en"] = "f" * 64
        self.assertIn("per-locale digests", self.problems(seal=False))

    def test_canonical_hashes_preserve_unicode_and_newline_bytes(self):
        self.assertNotEqual(c.sha256("Қазақша".encode()), c.sha256("Қазақша\n".encode()))
        self.assertNotEqual(c.digest({"text": "é"}), c.digest({"text": "e\u0301"}))
        self.assertEqual(c.canonical({"b": [2, 1], "a": "Қ"}), '{"a":"Қ","b":[2,1]}'.encode())
        self.assertEqual(c.seal(self.package), c.seal(c.seal(self.package)))

    def test_manifest_rejects_float_syntax_for_cross_consumer_digest_consistency(self):
        for value in [1.0, 1e20]:
            before = self.package["snapshot"]["files"][0]["size"]
            self.package["snapshot"]["files"][0]["size"] = value
            self.assertIn("integer JSON syntax", self.problems())
            self.package["snapshot"]["files"][0]["size"] = before


class EnglishProtectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads((c.CONTRACT / "english-baseline.json").read_text())

    def test_v1_bytes_and_embedded_schemas_are_unchanged(self):
        self.assertEqual(c.sha256(c.v1.SCHEMA.read_bytes()), self.baseline["v1_schema_sha256"])
        original = json.loads(c.v1.SCHEMA.read_text())["definitions"]
        for key, schema in original.items():
            self.assertEqual(c.SCHEMA["definitions"]["authoring_quiz" if key == "quiz" else key], schema)

    def test_all_historical_learning_ids_codes_and_english_paths_remain(self):
        root = c.CONTRACT.parents[1]
        catalog = yaml.safe_load((root / "catalog.yaml").read_text())
        self.assertLessEqual({p["id"] for p in self.baseline["programs"]}, {p["id"] for p in catalog["programs"]})
        for frozen in self.baseline["courses"]:
            course = yaml.safe_load((root / frozen["manifest"]).read_text())
            self.assertEqual(course["slug"], frozen["slug"])
            self.assertEqual(course["certificate"]["code"], frozen["certificate_code"])
            current = {module["id"]: module for module in course["modules"]}
            for old in frozen["modules"]:
                module = current[old["id"]]
                self.assertEqual(module["slug"], old["slug"])
                self.assertEqual(f"/courses/{course['slug']}/level-{module['level']}/{module['id']}", old["english_route"])
                for key in ["course", "lab", "quiz"]:
                    if key in module:
                        self.assertEqual(str(Path(frozen["manifest"]).parent / module[key]), old["source_paths"][key])
                for bank in old["quizzes"]:
                    quiz = yaml.safe_load((root / bank["source_path"]).read_text())
                    questions = {q["id"]: q for q in quiz["questions"]}
                    for identity, options in bank["option_ids"].items():
                        self.assertEqual([o["id"] for o in questions[identity]["options"]], options)

    def test_frozen_legacy_quiz_uses_original_algorithm_without_locale(self):
        sample = self.baseline["legacy_quiz_sample"]
        quiz = sample["authoring_quiz"]
        grading = {"passingScorePercent": quiz["passing_score_percent"],
                   "questions": [{k: q[k] for k in ("id", "prompt", "options", "answer")} for q in quiz["questions"]]}
        self.assertEqual(c.digest(grading), sample["question_digest"])
        self.assertEqual(len(quiz["questions"]), 6)

    def test_frozen_progress_is_still_valid_and_has_no_locale_identity(self):
        sys.path.insert(0, str(c.CONTRACT.parents[1] / "doris-course/common"))
        from doris_labkit.progress import validate_document
        sample = self.baseline["legacy_progress_sample"]
        self.assertEqual(validate_document(json.dumps(sample)), sample)
        self.assertTrue(sample["records"][0]["quiz_passed"])
        self.assertNotIn("locale", sample["records"][0])

    def test_v1_still_rejects_v2_fields(self):
        schema = json.loads(c.v1.SCHEMA.read_text())
        catalog = yaml.safe_load((c.CONTRACT / "examples/catalog.yaml").read_text())
        catalog["available_locales"] = ["en", "kk-Cyrl"]
        errors = list(Draft7Validator({"$ref": "#/definitions/catalog", "definitions": schema["definitions"]}).iter_errors(catalog))
        self.assertTrue(any("Additional properties" in error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
