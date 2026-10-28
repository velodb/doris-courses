#!/usr/bin/env python3
"""Validate contract v1 and preserve published identities across pull requests."""
import argparse
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from jsonschema import Draft7Validator
from markdown_it import MarkdownIt

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "contracts/v1/schema.json"


class UniqueKeyLoader(yaml.SafeLoader):
    """Do not silently replace a manifest field or answer with a duplicate key."""


def mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def load(text):
    return yaml.load(text, Loader=UniqueKeyLoader)


def safe_file(root, relative):
    path = root / relative
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
        raise ValueError(f"symlinks are not content assets: {relative}")
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f"missing file or path outside content root: {relative}")
    return resolved


def check_markdown(root, lesson):
    """Validate the same file/protocol/media boundary used by the publisher."""
    problems = []
    parser = MarkdownIt()
    parser.validateLink = lambda value: True  # Inspect, rather than silently discard, unsupported protocols.
    blocked = {"script", "style", "iframe", "object", "embed", "form", "input", "link", "meta", "base"}

    def url(value, image=False):
        if value.startswith("#"):
            return
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") or (parsed.scheme == "mailto" and not image):
            return
        if parsed.scheme or (image and value.startswith("/")):
            problems.append(f"{lesson}: unsupported content URL: {value}")
            return
        if value.startswith("/"):
            return
        try:
            target = safe_file(root, (lesson.parent / unquote(parsed.path)).relative_to(root).as_posix())
            if image:
                if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
                    raise ValueError(f"unsupported image format: {value}")
                if target.suffix.lower() == ".svg" and re.search(r"<script|\bon\w+\s*=|<foreignObject|javascript:", target.read_text(), re.I):
                    raise ValueError(f"unsafe SVG: {value}")
        except (OSError, ValueError) as error:
            problems.append(f"{lesson}: {error}")

    class Html(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag.lower() in blocked:
                problems.append(f"{lesson}: unsupported raw HTML element: {tag}")
            if tag == "img" and not any(name == "src" and value for name, value in attrs):
                problems.append(f"{lesson}: an image requires a non-empty src")
            for name, value in attrs:
                if name.lower().startswith("on") or name.lower() in {"style", "srcdoc"}:
                    problems.append(f"{lesson}: unsupported raw HTML attribute: {name}")
                if value is not None and ((tag == "img" and name == "src") or (tag == "a" and name == "href")):
                    url(value, tag == "img")

    def tokens(items):
        for token in items:
            if token.type == "image":
                url(token.attrGet("src"), True)
            elif token.type == "link_open":
                url(token.attrGet("href"))
            elif token.type in {"html_block", "html_inline"}:
                Html().feed(token.content)
            if token.children:
                tokens(token.children)

    tokens(parser.parse(lesson.read_text()))
    return problems


def validate(root, base_ref=None):
    root = Path(root).resolve()
    schema = json.loads(SCHEMA.read_text())
    Draft7Validator.check_schema(schema)
    problems = []

    def document(relative, kind):
        try:
            value = load(safe_file(root, relative).read_text())
            validator = Draft7Validator({"$ref": f"#/definitions/{kind}", "definitions": schema["definitions"]})
            errors = sorted(validator.iter_errors(value), key=lambda error: str(list(error.path)))
            for error in errors:
                problems.append(f"{relative}:{'.'.join(map(str, error.path))}: {error.message}")
            return value if not errors else None
        except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: {error}")
            return None

    catalog = document("catalog.yaml", "catalog")
    if catalog is None:
        return problems
    discovered = {path.relative_to(root).as_posix() for path in (root / "doris-course").glob("*/course.yaml")}
    if discovered != set(catalog["courses"]):
        problems.append(f"catalog courses must list every course.yaml exactly once: {sorted(discovered ^ set(catalog['courses']))}")
    courses = {}
    certificate_codes = set()
    requirements = {"course_read": "course", "lab_opened": "lab", "lab_declared_complete": "lab", "quiz_passed": "quiz"}
    for relative in catalog["courses"]:
        course = document(relative, "course")
        if course is None:
            continue
        course_id = course["slug"]
        if course_id in courses:
            problems.append(f"duplicate course identity: {course_id}")
        courses[course_id] = course
        code = course["certificate"]["code"]
        if code in certificate_codes:
            problems.append(f"duplicate certificate code: {code}")
        certificate_codes.add(code)
        if course["completion"]["enabled"] and course["status"] != "published":
            problems.append(f"{relative}: only published courses can enable completion")
        if course["certificate"]["enabled"] and not course["completion"]["enabled"]:
            problems.append(f"{relative}: certificates require completion to be enabled")
        course_root = Path(relative).parent
        for field in ("requirements", "guide"):
            try:
                safe_file(root, (course_root / course["lab_setup"][field]).as_posix())
            except ValueError as error:
                problems.append(f"{relative}: {error}")
        ids, orders = set(), set()
        for module in course["modules"]:
            at = f"{course_id}/{module['id']}"
            if module["id"] in ids or module["order"] in orders:
                problems.append(f"{at}: duplicate module ID or order")
            ids.add(module["id"])
            orders.add(module["order"])
            if str(module["level"]) not in course["levels"]:
                problems.append(f"{at}: unknown level {module['level']}")
            if module["status"] != "published" and (module["required"] or module["requirements"]):
                problems.append(f"{at}: planned/retired modules cannot be required or grant completion")
            if module["status"] == "published" and not module.get("course"):
                problems.append(f"{at}: a published module requires a lesson")
            if module["required"] and not module["requirements"]:
                problems.append(f"{at}: a required module needs explicit completion requirements")
            for requirement in module["requirements"]:
                if not module.get(requirements[requirement]):
                    problems.append(f"{at}: {requirement} requires {requirements[requirement]}")
            assets = [(module.get(field), field) for field in ("course", "lab", "quiz")]
            for part in module.get("optional", []):
                assets.extend([(part["path"], "optional"), (part.get("quiz"), "quiz")])
            for asset, kind in assets:
                if not asset:
                    continue
                asset_path = (course_root / asset).as_posix()
                try:
                    asset_file = safe_file(root, asset_path)
                except ValueError as error:
                    problems.append(f"{at}: {error}")
                    continue
                if kind == "course":
                    problems.extend(check_markdown(root, asset_file))
                if kind == "quiz":
                    quiz = document(asset_path, "quiz")
                    if quiz:
                        question_ids = set()
                        for question in quiz["questions"]:
                            options = [option["id"] for option in question["options"]]
                            if question["id"] in question_ids or len(set(options)) != len(options) or question["answer"] not in options:
                                problems.append(f"{asset_path}: duplicate question/option ID or invalid answer")
                            question_ids.add(question["id"])
        if course["completion"]["enabled"] and not any(module["required"] for module in course["modules"]):
            problems.append(f"{relative}: completion requires at least one required module")
    program_ids = set()
    for program in catalog["programs"]:
        if program["id"] in program_ids:
            problems.append(f"duplicate program identity: {program['id']}")
        program_ids.add(program["id"])
        for course_id in program["required_courses"]:
            if course_id not in courses:
                problems.append(f"{program['id']}: unknown course {course_id}")
    if base_ref and not problems:
        problems.extend(check_identities(root, base_ref, courses, catalog))
    return problems


def check_identities(root, base_ref, courses, catalog):
    def previous(path):
        result = subprocess.run(["git", "show", f"{base_ref}:{path}"], cwd=root, capture_output=True, text=True)
        if result.returncode:
            return None
        return load(result.stdout)

    old = previous("catalog.yaml")
    if old is None:  # The initial migration establishes the stable identities.
        return []
    problems = []
    for path in old["courses"]:
        before = previous(path)
        if not before:
            problems.append(f"base catalog has no readable manifest: {path}")
            continue
        after = courses.get(before["slug"])
        if after is None:
            problems.append(f"course identity removed: {before['slug']}; retain it with status retired")
            continue
        if before["certificate"]["code"] != after["certificate"]["code"]:
            problems.append(f"certificate code is immutable: {before['slug']}")
        after_ids = {module["id"] for module in after["modules"]}
        for module in before["modules"]:
            if module["id"] not in after_ids:
                problems.append(f"module identity removed: {before['slug']}/{module['id']}; retire it instead")
    old_programs = {program["id"] for program in old["programs"]}
    if old_programs - {program["id"] for program in catalog["programs"]}:
        problems.append("program identities cannot be removed")
    schema = subprocess.run(["git", "show", f"{base_ref}:contracts/v1/schema.json"], cwd=root, capture_output=True, text=True)
    if schema.returncode == 0 and schema.stdout != (root / "contracts/v1/schema.json").read_text():
        problems.append("contract v1 schema is immutable; introduce a new version for schema changes")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--base-ref", help="compare IDs with this fetched Git commit")
    args = parser.parse_args()
    if args.base_ref:
        subprocess.run(["git", "rev-parse", "--verify", args.base_ref + "^{commit}"], cwd=args.root, check=True, stdout=subprocess.DEVNULL)
    problems = validate(args.root, args.base_ref)
    for problem in problems:
        print(f"PROBLEM {problem}")
    print(f"Content contract v1: {len(problems)} problem(s)")
    return bool(problems)


if __name__ == "__main__":
    sys.exit(main())
