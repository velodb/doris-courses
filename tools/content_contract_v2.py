"""Pinned, data-only admission for the multilingual content contract.

This module validates resolved packages. Extraction, rendering and packaging
belong to the producer; neither consumers nor this module execute package code.
"""
from __future__ import annotations

import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from jsonschema import Draft7Validator
from markdown_it import MarkdownIt
import yaml

import check_content_contract as v1

CONTRACT = Path(__file__).resolve().parents[1] / "contracts/v2"
SCHEMA = json.loads((CONTRACT / "schema.json").read_text())
REGISTRY = json.loads((CONTRACT / "locales.json").read_text())
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
PATH = re.compile(r"[A-Za-z0-9_-]+(?:[./][A-Za-z0-9_-]+)*\Z")


class AdmissionError(ValueError):
    """The supplied package violates a pinned contract or integrity boundary."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    """UTF-8 JSON, sorted keys, ordered arrays, no spaces or trailing newline."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return sha256(canonical(value))


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdmissionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def bounded(value):
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 64 or count > 1000000:
            raise AdmissionError("document nesting or item limit exceeded")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def read_json(data, *, integers_only=False):
    if len(data) > MAX_FILE_BYTES:
        raise AdmissionError("document exceeds 16 MiB")
    options = {"object_pairs_hook": unique_pairs,
               "parse_constant": lambda _: fail("non-finite JSON number")}
    if integers_only:
        options["parse_float"] = lambda _: fail("package numbers must use integer JSON syntax")
    return bounded(json.loads(data, **options))


def fail(message):
    raise AdmissionError(message)


def shape(value, kind="package"):
    validator = Draft7Validator({"$ref": f"#/definitions/{kind}",
                                 "definitions": SCHEMA["definitions"]})
    errors = sorted(validator.iter_errors(value), key=lambda error: str(list(error.path)))
    if errors:
        error = errors[0]
        fail(f"{kind}:{'.'.join(map(str, error.path))}: {error.message}")


def validate_registry(registry=REGISTRY):
    schema = json.loads((CONTRACT / "registry.schema.json").read_text())
    errors = list(Draft7Validator(schema).iter_errors(registry))
    if errors:
        fail(f"invalid locale registry: {errors[0].message}")
    names = set()
    for locale in registry["locales"]:
        # route_key repeats its own tag, but may not name any other locale.
        own = {locale["tag"].casefold(), locale["route_key"].casefold()}
        aliases = [name.casefold() for name in locale["aliases"]]
        if len(aliases) != len(set(aliases)) or own.intersection(aliases):
            fail("duplicate locale alias")
        own.update(aliases)
        if own.intersection(names):
            fail("locale tags, routes and aliases must be unique ignoring case")
        names.update(own)
    if registry["source_locale"] not in {item["tag"] for item in registry["locales"]}:
        fail("source locale is not registered")


def normalize_locale(value):
    """For UI/route adapters only. Manifest admission requires canonical tags."""
    for locale in REGISTRY["locales"]:
        if value.casefold() in {name.casefold() for name in
                               [locale["tag"], locale["route_key"], *locale["aliases"]]}:
            return locale["tag"]
    fail(f"unregistered locale: {value}")


def safe_path(root, relative):
    parts = PurePosixPath(relative).parts
    if (not PATH.fullmatch(relative) or any(part in {".", ".."} for part in parts)
            or PurePosixPath(relative).as_posix() != relative):
        fail(f"non-canonical or escaping path: {relative}")
    current = Path(root)
    for part in parts:
        current = current / part
        if current.is_symlink():
            fail(f"symlinks are not content files: {relative}")
    if not current.is_file() or not current.resolve().is_relative_to(Path(root).resolve()):
        fail(f"missing file or path outside content root: {relative}")
    if current.stat().st_size > MAX_FILE_BYTES:
        fail(f"file exceeds 16 MiB: {relative}")
    return current


def index_by(items, key, name):
    result = {}
    for item in items:
        identity = item[key]
        if identity in result:
            fail(f"duplicate {name}: {identity}")
        result[identity] = item
    return result


def safe_svg(data):
    if re.search(br"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", data, re.I):
        fail("unsafe SVG declaration")
    tree = ET.fromstring(data)
    allowed = {"svg", "g", "defs", "path", "rect", "circle", "ellipse", "line", "polyline",
               "polygon", "text", "tspan", "title", "desc", "clipPath", "linearGradient",
               "radialGradient", "stop", "use", "symbol", "marker"}
    for node in tree.iter():
        if node.tag.rsplit("}", 1)[-1] not in allowed:
            fail("unsafe SVG element")
        for key, value in node.attrib.items():
            key = key.rsplit("}", 1)[-1].lower()
            if key.startswith("on") or key in {"style", "base"}:
                fail("unsafe SVG attribute")
            if key in {"href", "src"} and not value.startswith("#"):
                fail("unsafe SVG external reference")
            if re.search(r"url\(\s*['\"]?(?!#)|javascript:|data:", value, re.I):
                fail("unsafe SVG URL")


def safe_markdown(text, source_path, files):
    """Conservative authoring boundary; consumers must also sanitize output."""
    parser = MarkdownIt()
    parser.validateLink = lambda _: True

    def link(value, image=False):
        value = value.strip()
        for _ in range(4):
            decoded = unquote(value)
            if decoded == value:
                break
            value = decoded
        if "%" in value or "\\" in value or any(ord(c) < 32 for c in value):
            fail("unsafe content URL encoding")
        parsed = urlsplit(value)
        if image and (parsed.scheme or parsed.netloc):
            fail("images must use frozen local files")
        if parsed.scheme in {"http", "https"} or (parsed.scheme == "mailto" and not image):
            return
        if parsed.scheme or parsed.netloc:
            fail("unsafe content URL protocol")
        if not parsed.path:
            return
        if parsed.path.startswith("/"):
            if image:
                fail("image must use a frozen local file")
            return  # Website-owned routes, never filesystem paths.
        parts = list(PurePosixPath(source_path).parent.parts)
        for part in parsed.path.split("/"):
            if part == "..":
                if not parts:
                    fail("content URL escapes package")
                parts.pop()
            elif part not in {"", "."}:
                parts.append(part)
        target = "/".join(parts)
        if target not in files:
            fail(f"content URL is not in frozen file inventory: {target}")
        if image and Path(target).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            fail("unsupported image format")

    class Html(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in {"details", "summary", "br", "p", "a", "img", "em", "strong", "code",
                           "pre", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
                           "blockquote", "hr", "sub", "sup", "kbd", "samp", "del", "h1", "h2",
                           "h3", "h4", "h5", "h6", "div", "span"}:
                fail(f"unsafe raw HTML element: {tag}")
            for key, value in attrs:
                if key not in {"id", "title", "alt", "href", "src", "open", "width", "height", "align"}:
                    fail(f"unsafe raw HTML attribute: {key}")
                if key in {"href", "src"}:
                    link(value or "", key == "src")

    def tokens(items):
        for token in items:
            if token.type in {"html_block", "html_inline"}:
                Html().feed(token.content)
            elif token.type == "image":
                link(token.attrGet("src"), True)
            elif token.type == "link_open":
                link(token.attrGet("href"))
            if token.children:
                tokens(token.children)
    # Legacy quiz display leaves contain the literal SQL notation <db>.
    # It has no attributes or executable meaning; consumers render display
    # leaves as escaped text. Do not admit arbitrary custom HTML elements.
    tokens(parser.parse(text.replace('<db>', '&lt;db&gt;')))


def protected_markup(text):
    """Code bytes, URLs, anchors and named placeholders survive translation."""
    parser = MarkdownIt()
    parser.validateLink = lambda _: True
    protected = []

    class Html(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {"href", "src", "id"}:
                    protected.append(("html-" + key, "", value or ""))

    def walk(items):
        for token in items:
            if token.type in {"fence", "code_block", "code_inline"}:
                protected.append((token.type, token.info, token.content))
            if token.type in {"link_open", "image"}:
                url = token.attrGet("href" if token.type == "link_open" else "src")
                protected.append(("url", "", url))
            if token.type in {"html_block", "html_inline"}:
                Html().feed(token.content)
            if token.children:
                walk(token.children)
    walk(parser.parse(text))
    protected.extend(("placeholder", "", match) for match in re.findall(
        r"\$\{[A-Za-z_][A-Za-z0-9_.]*\}|\{\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}|\{[A-Za-z_][A-Za-z0-9_.]*\}", text))
    return sorted(protected)


def source_text(unit, documents, files):
    data = files[unit["source_path"]]
    kind, value = unit["locator"]["kind"], unit["locator"]["value"]
    if kind == "markdown-block":
        if Path(unit["source_path"]).suffix != ".md":
            fail("Markdown block must reference a Markdown file")
        # value is an opaque stable author label; source_text selects an exact,
        # unique block. Text edits update the selector, never the unit identity.
        text = data.decode("utf-8")
        if text.count(unit["source_text"]) != 1:
            fail(f"missing or ambiguous Markdown block: {unit['id']}")
        return unit["source_text"]
    document = documents.get(unit["source_path"])
    if document is None:
        fail("structured unit must reference YAML, JSON or Notebook data")
    if kind == "notebook-cell":
        if Path(unit["source_path"]).suffix != ".ipynb":
            fail("Notebook unit must reference a Notebook file")
        cells = [cell for cell in document.get("cells", []) if cell.get("id") == value]
        if len(cells) != 1 or cells[0].get("cell_type") != "markdown":
            fail("Notebook unit must reference a unique Markdown cell ID")
        content = cells[0]["source"]
        return "".join(content) if isinstance(content, list) else content
    if not value.startswith("/"):
        fail("JSON pointer must be absolute")
    current = document
    parts = value[1:].split("/")
    for part in parts:
        if re.search(r"~(?![01])", part):
            fail("invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                fail("invalid JSON pointer array index")
            current = current[int(key)]
        else:
            current = current[key]
    # Only display leaves can be translated. IDs, URLs, code and policies are
    # deliberately absent from this allowlist.
    allowed = []
    if isinstance(document, dict) and document.get("schema_version") == 1 and "programs" in document:
        allowed = [r"/(title|description)", r"/programs/[0-9]+/title"]
    elif isinstance(document, dict) and "modules" in document and "slug" in document:
        allowed = [r"/(title|short_title|description)", r"/presentation/badge_label", r"/levels/[1-9][0-9]*",
                   r"/modules/[0-9]+/(title|summary)", r"/modules/[0-9]+/optional/[0-9]+/title"]
    elif isinstance(document, dict) and "passing_score_percent" in document and "questions" in document:
        allowed = [r"/(title|description)", r"/questions/[0-9]+/(objective|prompt|explanation)",
                   r"/questions/[0-9]+/options/[0-9]+/text"]
    elif isinstance(document, dict) and document.get("kind") == "doris-ui-messages":
        allowed = [r"/messages/[a-z][a-z0-9_.-]*"]
    if not any(re.fullmatch(pattern, value) for pattern in allowed):
        fail("unit targets a non-translatable field")
    if not isinstance(current, str):
        fail("unit must select a string")
    return current


def teaching_value(package):
    return {"files": package["snapshot"]["files"], **{
        key: package[key] for key in ("courses", "profiles", "selected_profiles", "source_units",
                                      "translations", "assets", "quizzes")}}


def locale_value(package, locale):
    return {"units": package["source_units"] if locale == "en" else next(
                (overlay["units"] for overlay in package["translations"] if overlay["locale"] == locale), []),
            "assets": [asset for asset in package["assets"] if asset["locale"] in {locale, "shared"}],
            "quizzes": [{"id": quiz["id"], "semantic_revision": quiz["semantic_revision"],
                         "presentations": [p for p in quiz["presentations"] if p["locale"] == locale]}
                        for quiz in package["quizzes"]]}


def presentation_digest(quiz, locale, units, overlays):
    presentation = next(p for p in quiz["presentations"] if p["locale"] == locale)
    texts = {uid: units[uid]["source_text"] if locale == "en" else overlays[locale][uid]["target_text"]
             for uid in presentation["unit_ids"]}
    return digest({"quiz_id": quiz["id"], "locale": locale, "texts": texts})


def seal(package):
    """Fill deterministic digests for a producer. This does not grant admission."""
    result = copy.deepcopy(package)
    result["locale_digests"] = {locale: digest(locale_value(result, locale))
                                for locale in result["available_locales"]}
    snap = result["snapshot"]
    snap["translation_digest"] = digest(result["translations"])
    snap["teaching_digest"] = digest(teaching_value(result))
    snap.pop("package_digest", None)
    snap["package_digest"] = digest(result)
    return result


def validate_package(root, entry="package.v2.json", *, allow_fixture=False):
    """Return a validation report, or raise AdmissionError. No writes or network."""
    root = Path(root).resolve()
    package = read_json(safe_path(root, entry).read_bytes(), integers_only=True)
    shape(package)
    if package["purpose"] == "contract-fixture" and not allow_fixture:
        fail("contract fixture cannot be used as teaching content; opt in only for tests")
    validate_registry()
    registered = {item["tag"] for item in REGISTRY["locales"]}

    def locale(tag):
        if tag not in registered:
            fail(f"unregistered or non-canonical locale: {tag}")

    snap = package["snapshot"]
    if snap["source_schema_digest"] != sha256(v1.SCHEMA.read_bytes()):
        fail("wrong source schema digest")
    if snap["contract_digest"] != sha256((CONTRACT / "schema.json").read_bytes()):
        fail("wrong contract digest")
    if snap["registry_digest"] != sha256((CONTRACT / "locales.json").read_bytes()):
        fail("wrong registry digest")
    inventory = index_by(snap["files"], "path", "file path")
    if list(inventory) != sorted(inventory) or entry in inventory:
        fail("file inventory must be sorted and exclude the package manifest")
    if sum(item["size"] for item in inventory.values()) > MAX_TOTAL_BYTES:
        fail("package exceeds 128 MiB")
    # Shared code/data are opaque learner downloads. In particular, Docker
    # Compose anchors are not content-authoring YAML and must not be expanded
    # during ingestion. Any structured reference still forces strict parsing.
    structured_paths = {package['catalog'], *(b['manifest'] for b in package['courses']),
                        *(q['source_path'] for q in package['quizzes']),
                        *(u['source_path'] for u in package['source_units']
                          if u['locator']['kind'] != 'markdown-block')}
    opaque_paths = {a['file']['path'] for a in package['assets']
                    if a['kind'] in {'code', 'data'} and a['locale'] == 'shared'} - structured_paths
    files, documents = {}, {}
    for relative, record in inventory.items():
        data = safe_path(root, relative).read_bytes()
        if len(data) != record["size"] or sha256(data) != record["sha256"]:
            fail(f"file hash/size mismatch: {relative}")
        files[relative] = data
        suffix = Path(relative).suffix.lower()
        if suffix in {".yaml", ".yml"} and relative not in opaque_paths:
            if any(isinstance(token, (yaml.AliasToken, yaml.AnchorToken)) for token in yaml.scan(data)):
                fail("YAML aliases and anchors are not contract data")
            documents[relative] = bounded(v1.load(data.decode("utf-8")))
        elif suffix in {".json", ".ipynb"} and relative not in opaque_paths:
            documents[relative] = read_json(data)
        elif suffix == ".svg":
            safe_svg(data)
        if suffix == ".md":
            safe_markdown(data.decode("utf-8"), relative, inventory)
        if suffix == ".ipynb":
            notebook = documents[relative]
            if (not isinstance(notebook, dict) or notebook.get("nbformat") != 4
                    or not isinstance(notebook.get("cells"), list)):
                fail("invalid Notebook document")
            ids = set()
            for cell in notebook.get("cells", []):
                if not isinstance(cell, dict) or cell.get("cell_type") not in {"code", "markdown", "raw"}:
                    fail("invalid Notebook cell")
                identity = cell.get("id")
                if not identity or identity in ids:
                    fail("Notebook cells require unique stable IDs")
                ids.add(identity)
                if cell.get("cell_type") == "markdown":
                    safe_markdown("".join(cell["source"]), relative, inventory)
                if cell["cell_type"] == "code" and (cell.get("outputs") or cell.get("execution_count") is not None):
                    fail("teaching Notebooks must have cleared outputs and execution counts")
            # Code is a downloadable learning asset. Never execute it on import.

    def document(path):
        if path not in documents:
            fail(f"document is not in frozen inventory: {path}")
        return documents[path]

    catalog = document(package["catalog"])
    shape(catalog, "catalog")
    for program in catalog["programs"]:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", program["id"]):
            fail("invalid permanent program ID")
    courses = index_by(package["courses"], "slug", "course binding")
    if {course["manifest"] for course in courses.values()} != set(catalog["courses"]):
        fail("course bindings must match the English catalog")
    modules = {}
    for binding in courses.values():
        course = document(binding["manifest"])
        shape(course, "course")
        if course["slug"] != binding["slug"]:
            fail("course identity differs from English manifest")
        if not re.fullmatch(r"[A-Z0-9]{2,24}", course["certificate"]["code"]):
            fail("invalid certificate code")
        course_root = PurePosixPath(binding["manifest"]).parent
        for field in ("requirements", "guide"):
            if str(course_root / course["lab_setup"][field]) not in inventory:
                fail("Lab setup file is absent from inventory")
        for module in course["modules"]:
            if any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", module[field]) for field in ("id", "slug")):
                fail("invalid permanent module ID/slug")
            key = course["slug"], module["id"]
            if key in modules:
                fail("duplicate stable module ID")
            modules[key] = (course, module)
            for path in [module.get(field) for field in ("course", "lab", "quiz")] + [
                    part.get(field) for part in module.get("optional", []) for field in ("path", "quiz")]:
                if path and str(course_root / path) not in inventory:
                    fail(f"module asset is absent from inventory: {path}")
    # Retain every original v1 access, completion, ID and Markdown rule.
    problems = v1.validate(root)
    if problems:
        fail("English v1 admission: " + problems[0])

    variants = index_by(package["variants"], "locale", "locale variant")
    for tag in [*variants, package["default_locale"], *package["available_locales"], *package["locale_digests"]]:
        locale(tag)
    if "en" not in variants or variants["en"]["publication_state"] != "published":
        fail("English source variant must be published")
    units = index_by(package["source_units"], "id", "stable unit ID")
    locators = set()
    for unit in units.values():
        locator = unit["source_path"], unit["locator"]["kind"], unit["locator"]["value"]
        if locator in locators:
            fail("duplicate source locator under different unit IDs")
        locators.add(locator)
        scope = unit["scope"]
        if scope["module_id"] and (scope["course_slug"], scope["module_id"]) not in modules:
            fail("unit references an unknown module")
        if scope["course_slug"] and scope["course_slug"] not in courses:
            fail("unit references an unknown course")
        if unit["source_path"] not in files:
            fail("unit source file is absent from inventory")
        resolved = source_text(unit, documents, files)
        if resolved != unit["source_text"] or sha256(resolved.encode("utf-8")) != unit["source_hash"]:
            fail(f"wrong source hash/text: {unit['id']}")
        safe_markdown(resolved, unit["source_path"], inventory)
    overlays = {}
    for overlay in package["translations"]:
        tag = overlay["locale"]
        locale(tag)
        if tag == "en" or tag not in variants or tag in overlays:
            fail("duplicate, English or undeclared translation overlay")
        overlays[tag] = index_by(overlay["units"], "unit_id", "translation unit ID")
        for uid, translation in overlays[tag].items():
            if uid not in units:
                fail(f"unknown translation unit: {uid}")
            if translation["source_hash"] != units[uid]["source_hash"]:
                fail(f"stale/wrong source hash: {uid}")
            if translation["target_hash"] != sha256(translation["target_text"].encode("utf-8")):
                fail(f"wrong target hash: {uid}")
            safe_markdown(translation["target_text"], units[uid]["source_path"], inventory)
            if protected_markup(translation["target_text"]) != protected_markup(units[uid]["source_text"]):
                fail("translation changed code, a URL, an anchor or a named placeholder")
            checks = translation["checks"]
            for key, method in (("llm_self_check", "current-session-llm"),
                                ("technical_admission", "technical-validator"), ("native_review", "native-human")):
                check = checks[key]
                if check and (check["method"] != method or check["source_hash"] != translation["source_hash"]
                              or check["target_hash"] != translation["target_hash"]):
                    fail("review method/hash binding is invalid")
            if translation["state"] in {"llm_checked", "reviewed"} and not (
                    checks["llm_self_check"] and checks["technical_admission"]):
                fail("llm_checked requires self-check and technical evidence")
            if translation["state"] == "reviewed" and not checks["native_review"]:
                fail("reviewed requires an attributed native human review")

    assets = index_by(package["assets"], "id", "asset ID")
    for asset in assets.values():
        if asset["locale"] != "shared":
            locale(asset["locale"])
            if asset["locale"] not in variants:
                fail("asset uses an undeclared locale variant")
        if asset["locale"] in {"en", "shared"} and asset["source_asset_hash"] is not None:
            fail("source/shared asset cannot replace another source asset")
        if asset["locale"] not in {"shared", "en"} and asset["source_asset_hash"] is None:
            fail("localized asset requires source_asset_hash")
        if asset["locale"] not in {"shared", "en"} and asset["kind"] in {"code", "data"}:
            fail("code and data assets cannot be localized")
        record = asset["file"]
        if inventory.get(record["path"]) != record:
            fail("asset differs from frozen file inventory")
        extensions = {"lesson": {".md"}, "notebook": {".ipynb"}, "quiz": {".yaml", ".yml", ".json"},
                      "ui": {".json"}, "metadata": {".json"},
                      "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}}
        if asset["kind"] in extensions and Path(record["path"]).suffix.lower() not in extensions[asset["kind"]]:
            fail("asset kind has an unsupported file format")
        if asset["kind"] == "ui":
            messages = document(record["path"])
            shape(messages, "ui_messages")
            for text in messages["messages"].values():
                safe_markdown(text, record["path"], inventory)
        if asset["kind"] == "quiz":
            bank = document(record["path"])
            shape(bank, "authoring_quiz")
            texts = [bank["title"], bank["description"]]
            for question in bank["questions"]:
                texts.extend(question[key] for key in ("objective", "prompt", "explanation"))
                texts.extend(option["text"] for option in question["options"])
            for text in texts:
                safe_markdown(text, record["path"], inventory)
        if asset["source_asset_hash"] and not any(
                source["file"]["sha256"] == asset["source_asset_hash"] and source["locale"] in {"en", "shared"}
                and source["kind"] == asset["kind"] for source in assets.values()):
            fail("localized asset has no matching source asset")
        if asset["source_asset_hash"] and asset["locale"] not in {"en", "shared"}:
            source = next(source for source in assets.values()
                          if source["file"]["sha256"] == asset["source_asset_hash"]
                          and source["locale"] in {"en", "shared"} and source["kind"] == asset["kind"])
            for uid, unit in units.items():
                if unit["source_path"] != source["file"]["path"]:
                    continue
                translation = overlays.get(asset["locale"], {}).get(uid)
                if not translation:
                    fail("localized asset is missing a registered translation unit")
                target_unit = {**unit, "source_path": record["path"], "source_text": translation["target_text"]}
                if source_text(target_unit, documents, files) != translation["target_text"]:
                    fail("localized asset text differs from its translation units")
        if asset["source_asset_hash"] and asset["kind"] in {"notebook", "quiz"}:
            sources = [source for source in assets.values() if source["file"]["sha256"] == asset["source_asset_hash"]
                       and source["locale"] in {"en", "shared"} and source["kind"] == asset["kind"]]
            original = copy.deepcopy(document(sources[0]["file"]["path"]))
            translated = copy.deepcopy(document(record["path"]))
            if asset["kind"] == "notebook":
                for notebook in (original, translated):
                    for cell in notebook["cells"]:
                        if cell["cell_type"] == "markdown":
                            cell.pop("source", None)
            else:
                for quiz in (original, translated):
                    shape(quiz, "authoring_quiz")
                    for key in ("title", "description"):
                        quiz.pop(key)
                    for question in quiz["questions"]:
                        for key in ("objective", "prompt", "explanation"):
                            question.pop(key)
                        for option in question["options"]:
                            option.pop("text")
            if canonical(original) != canonical(translated):
                fail("localized Notebook code/structure or quiz grading differs from source")
    quizzes = index_by(package["quizzes"], "id", "quiz ID")
    quiz_sources = set()
    for quiz in quizzes.values():
        key = quiz["course_slug"], quiz["module_id"]
        if key not in modules or quiz["source_path"] in quiz_sources:
            fail("unknown quiz module or duplicate quiz source")
        quiz_sources.add(quiz["source_path"])
        course, module = modules[key]
        course_root = PurePosixPath(courses[key[0]]["manifest"]).parent
        allowed = [module.get("quiz"), *(part.get("quiz") for part in module.get("optional", []))]
        if quiz["source_path"] not in {str(course_root / path) for path in allowed if path}:
            fail("quiz source is not owned by its module")
        source = document(quiz["source_path"])
        shape(source, "authoring_quiz")
        for question in source["questions"]:
            if any(not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", identity) for identity in
                   [question["id"], question["answer"], *(option["id"] for option in question["options"])]):
                fail("invalid permanent question/option ID")
        expected = [{"id": q["id"], "type": q["type"], "option_ids": [o["id"] for o in q["options"]],
                     "answer": q["answer"]} for q in source["questions"]]
        if quiz["questions"] != expected or quiz["passing_score_percent"] != source["passing_score_percent"]:
            fail("quiz IDs, option order, answer or passing score differ from English")
        presentations = index_by(quiz["presentations"], "locale", "quiz presentation locale")
        for tag, presentation in presentations.items():
            locale(tag)
            if tag not in variants or any(uid not in units or units[uid]["source_path"] != quiz["source_path"]
                                          for uid in presentation["unit_ids"]):
                fail("quiz presentation uses unknown or unrelated units")
            if any(units[uid]["locator"]["value"].endswith("/explanation")
                   for uid in presentation["unit_ids"]):
                fail("public presentation revision cannot depend on explanations")
            if tag != "en" and any(uid not in overlays.get(tag, {}) for uid in presentation["unit_ids"]):
                fail("quiz presentation has missing translations")
            if presentation["presentation_revision"] != presentation_digest(quiz, tag, units, overlays):
                fail("wrong quiz presentation revision")

    profiles = index_by(package["profiles"], "id", "profile ID")
    for profile in profiles.values():
        selected_modules = {(module["course_slug"], module["module_id"]) for module in profile["modules"]}
        for module in profile["modules"]:
            key = module["course_slug"], module["module_id"]
            if key not in modules:
                fail("profile references unknown module")
            course, canonical_module = modules[key]
            if course["status"] not in {"published", "preview"} or canonical_module["status"] != "published":
                fail("profile cannot expose draft/planned/retired content")
        for field, index in (("unit_ids", units), ("asset_ids", assets), ("quiz_ids", quizzes)):
            if any(identity not in index for identity in profile[field]):
                fail(f"profile references unknown {field}")
        if any(assets[aid]["locale"] not in {"en", "shared"} for aid in profile["asset_ids"]):
            fail("profile must name English/shared source assets")
        if any((quizzes[qid]["course_slug"], quizzes[qid]["module_id"]) not in selected_modules
               for qid in profile["quiz_ids"]):
            fail("profile quiz belongs to an unselected module")
        for key in selected_modules:
            module = modules[key][1]
            base = PurePosixPath(courses[key[0]]["manifest"]).parent
            for field, kind in (("course", "lesson"), ("lab", "notebook"), ("quiz", "quiz")):
                if module.get(field) and not any(
                        assets[aid]["file"]["path"] == str(base / module[field])
                        and assets[aid]["kind"] == kind and assets[aid]["locale"] == "en"
                        for aid in profile["asset_ids"]):
                    fail("profile omits a primary English teaching asset")
            if module.get("quiz") and not any(quizzes[qid]["source_path"] == str(base / module["quiz"])
                                               for qid in profile["quiz_ids"]):
                fail("profile omits its primary quiz binding")
    if any(pid not in profiles for pid in package["selected_profiles"]):
        fail("unknown selected profile")
    required_units = set().union(*(set(profiles[pid]["unit_ids"]) for pid in package["selected_profiles"]))
    required_assets = set().union(*(set(profiles[pid]["asset_ids"]) for pid in package["selected_profiles"]))
    required_quizzes = set().union(*(set(profiles[pid]["quiz_ids"]) for pid in package["selected_profiles"]))
    availability = {}
    for tag, variant in variants.items():
        missing = [] if tag == "en" else sorted(uid for uid in required_units if
            overlays.get(tag, {}).get(uid, {}).get("state") not in {"llm_checked", "reviewed"})
        missing_quizzes = sorted(qid for qid in required_quizzes if tag not in
                                  {p["locale"] for p in quizzes[qid]["presentations"]})
        missing_assets = sorted(aid for aid in required_assets if assets[aid]["locale"] not in {tag, "shared"}
            and not any(asset["locale"] == tag and asset["source_asset_hash"] == assets[aid]["file"]["sha256"]
                        and asset["kind"] == assets[aid]["kind"] for asset in assets.values()))
        availability[tag] = {"missing_units": missing, "missing_quizzes": missing_quizzes,
                             "missing_assets": missing_assets}
        if variant["publication_state"] == "published" and (missing or missing_quizzes or missing_assets):
            fail(f"published locale has missing, unchecked or withdrawn content: {tag}")
    available = [tag for tag, variant in variants.items() if variant["publication_state"] == "published"]
    if package["available_locales"] != available or package["default_locale"] not in available:
        fail("available_locales/default_locale differs from admitted profile intersection")
    if package["package_state"] == "release" and len(available) != len(variants):
        fail("release cannot include draft or withdrawn variants; select partial scope explicitly")
    expected = seal(package)
    if expected["locale_digests"] != package["locale_digests"]:
        fail("wrong per-locale digests")
    for field in ("translation_digest", "teaching_digest", "package_digest"):
        if expected["snapshot"][field] != snap[field]:
            fail(f"wrong {field}")
    return {"contract_version": package["contract_version"], "available_locales": available,
            "selected_profiles": package["selected_profiles"], "coverage": availability,
            "package_digest": snap["package_digest"]}


def validate(root, entry="package.v2.json", *, allow_fixture=False):
    """CLI-friendly error collection, including malformed untrusted documents."""
    try:
        return validate_package(root, entry, allow_fixture=allow_fixture), []
    except (ValueError, TypeError, KeyError, IndexError, OSError, UnicodeError,
            RecursionError, yaml.YAMLError, ET.ParseError) as error:
        return None, [str(error)]
