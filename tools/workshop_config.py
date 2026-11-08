#!/usr/bin/env python3
"""Validate an AWS workshop configuration and write a local planning manifest.

This tool does not contact AWS, invoke Terraform, or install course dependencies.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import yaml
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "workshop/aws"
TOOL_VERSION = "0.1.0"


class ConfigError(ValueError):
    """Invalid non-secret configuration or local planning manifest."""


class StrictLoader(yaml.SafeLoader):
    """Reject duplicate keys and leave timestamps as strings for schema validation."""


StrictLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in rules if tag != "tag:yaml.org,2002:timestamp"]
    for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ConfigError("input: mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


StrictLoader.add_constructor("tag:yaml.org,2002:map", unique_mapping)


def load_document(path):
    try:
        return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=StrictLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        # Parser messages can contain the supplied password or token. Do not echo them.
        raise ConfigError("input: cannot read document or invalid YAML/JSON") from exc


def schema_check(value, name):
    if isinstance(value, dict) and 'schema_version' in value and type(value['schema_version']) is not int:
        raise ConfigError('schema_version: expected an integer without a decimal point')
    if name in {"config.schema.json", "manifest.schema.json", "image-release.schema.json"} and isinstance(value, dict) and value.get("schema_version") == 2:
        name = name.replace(".schema.json", ".v2.schema.json")
    schema = json.loads((SPEC / name).read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        messages = []
        for error in errors[:20]:
            path = ".".join(map(str, error.absolute_path)) or "input"
            detail = error.validator
            if error.validator == "required" and isinstance(error.instance, dict):
                detail += ": " + ", ".join(
                    key for key in error.validator_value if key not in error.instance
                )
            messages.append(f"{path}: failed {detail} constraint")
        raise ConfigError("\n".join(messages))


def timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"{field}: expected a valid timestamp with timezone") from exc


def utc_text(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def check_plain_strings(value, path="input"):
    # A regex ending in $ can also match before a final newline. None of the
    # configuration's string fields allows surrounding whitespace.
    if isinstance(value, str) and value != value.strip():
        raise ConfigError(f"{path}: surrounding whitespace is not allowed")
    if isinstance(value, dict):
        for key, item in value.items():
            check_plain_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_plain_strings(item, f"{path}.{index}")


def git(repo, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, check=True
        )
        return result.stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        raise ConfigError("source: required Git commit or file is unavailable locally") from exc


def resolve_profiles(config, repo=REPO):
    """Read course metadata at the pinned commit, never the working-tree course files."""
    registry = json.loads((SPEC / ("profiles.v2.json" if config.get('schema_version') == 2 else "profiles.json")).read_text(encoding="utf-8"))
    profiles = registry["profiles"]
    chosen = set(config["course_profiles"])
    if "rta-module-1" in chosen and config.get("schema_version") != 2:
        raise ConfigError("course_profiles: rta-module-1 requires the explicit v2 content binding")
    ref = config["course_ref"]
    if git(repo, "rev-parse", "--verify", ref + "^{commit}").strip() != ref:
        raise ConfigError("course_ref: must identify the exact local commit")
    files = set(git(repo, "ls-tree", "-r", "--name-only", ref).splitlines())
    courses = {}
    resolved = {}
    ordered_profiles = (config['course_profiles'] if config.get('schema_version') == 2
                        else sorted(chosen))
    for profile_id in ordered_profiles:
        profile = profiles[profile_id]
        missing = set(profile["requires"]) - chosen
        if missing:
            raise ConfigError(f"course_profiles: {profile_id} requires {', '.join(sorted(missing))}")
        if chosen.intersection(profile["conflicts"]):
            raise ConfigError(f"course_profiles: {profile_id} conflicts with another selected profile")
        directory = profile["course_dir"]
        if directory not in courses:
            try:
                course = yaml.load(git(repo, "show", f"{ref}:{directory}/course.yaml"), Loader=StrictLoader)
                if not isinstance(course, dict) or not isinstance(course.get("modules"), list):
                    raise ConfigError("course_ref: invalid course manifest")
                modules = course["modules"]
                if any(not isinstance(m, dict) or not isinstance(m.get("id"), str) for m in modules):
                    raise ConfigError("course_ref: modules must have stable string IDs")
                if len({m["id"] for m in modules}) != len(modules):
                    raise ConfigError("course_ref: module IDs must be unique")
                for module in modules:
                    optional = module.get("optional", [])
                    if not isinstance(optional, list) or any(
                        not isinstance(part, dict) or not isinstance(part.get("path"), str)
                        for part in optional
                    ):
                        raise ConfigError("course_ref: invalid optional paths")
                courses[directory] = course
            except yaml.YAMLError as exc:
                raise ConfigError("course_ref: invalid course manifest YAML") from exc
        course = courses[directory]
        if course.get("status") != "published":
            if course.get("status") not in {"draft", "preview"} or not config["allow_draft_courses"]:
                raise ConfigError(f"course_profiles: {profile_id} needs an available course and explicit draft opt-in")
        available = {module["id"]: module for module in course["modules"]}
        ids = profile["module_ids"]
        if ids is None:
            ids = [m["id"] for m in course["modules"] if m.get("required")]
            if not ids:
                raise ConfigError(f"course_profiles: {profile_id} has no required modules")
        selected = []
        for module_id in ids:
            module = available.get(module_id)
            if not module or module.get("status") != "published":
                raise ConfigError(f"course_profiles: {profile_id} contains an unavailable or planned module")
            paths = {kind: module.get(kind) for kind in ("course", "lab", "quiz")}
            if any(not path or f"{directory}/{path}" not in files for path in paths.values()):
                raise ConfigError(f"course_profiles: {profile_id} has missing module files at course_ref")
            selected.append({"id": module_id, **paths})
        optional = {
            part["path"] for m in course["modules"] for part in m.get("optional", [])
        }
        optional.update(str(Path(part['quiz']).with_suffix('.ipynb'))
                        for m in course['modules'] for part in m.get('optional', []) if part.get('quiz'))
        for path in profile["optional_paths"]:
            if path not in optional or f"{directory}/{path}" not in files:
                raise ConfigError(f"course_profiles: {profile_id} has an unavailable optional path")
        resolved[profile_id] = {
            "course_dir": directory,
            "course_status": course["status"],
            "kernel": profile["kernel"],
            "working_directory": registry["runtime"]["repository_root"] + "/" + directory,
            "modules": selected,
            "optional_paths": profile["optional_paths"],
            "requires_browser_host": profile["requires_browser_host"],
        }
    return resolved, registry["runtime"]


def validate_config(value, *, now=None, repo=REPO):
    schema_check(value, "config.schema.json")
    check_plain_strings(value)
    if value["schema_version"] == 2:
        import workshop_content
        try:
            workshop_content.consumer.validate_binding(value['content'])
        except ValueError as error:
            raise ConfigError(str(error)) from error
        if value['content']['source_ref'] != value['course_ref'] or value['content']['profiles'] != value['course_profiles']:
            raise ConfigError('content: revision and profiles must match config')
    # JSON Schema accepts 1.0 as an integer; Python's range() does not.
    for key in ("schema_version", "disk_gib", "spare_count"):
        if type(value[key]) is not int:
            raise ConfigError(f"{key}: expected an integer without a decimal point")
    now = now or datetime.now(timezone.utc)
    expiry = timestamp(value["expires_at"], "expires_at")
    if expiry <= now:
        raise ConfigError("expires_at: must be in the future")
    if isinstance(value["budget_usd"], float) and not math.isfinite(value["budget_usd"]):
        raise ConfigError("budget_usd: must be finite")
    if any(learner.startswith("spare-") for learner in value["learner_ids"]):
        raise ConfigError("learner_ids: spare- is reserved for unassigned instances")
    region = value["region"]
    partition = "aws-cn" if region.startswith("cn-") else "aws-us-gov" if region.startswith("us-gov-") else "aws"
    if value["aws_partition"] != partition:
        raise ConfigError("aws_partition: does not match region")
    image = value["image"]
    if image["region"] != region:
        raise ConfigError("image.region: must match event region")
    if image["mode"] == "prebuilt":
        if image["course_ref"] != value["course_ref"]:
            raise ConfigError("image.course_ref: must match event course_ref")
        if set(image["course_profiles"]) != set(value["course_profiles"]):
            raise ConfigError("image.course_profiles: must match event profiles")
    resolved, runtime = resolve_profiles(value, repo)
    # Copy before normalizing, so callers retain their original input.
    normalized = json.loads(json.dumps(value))
    normalized["learner_ids"] = sorted(value["learner_ids"])
    # V2 preserves the immutable producer binding's ordered profile set.
    # Reordering only this field would make a valid multi-course binding stale.
    normalized["course_profiles"] = (list(value["course_profiles"]) if value['schema_version'] == 2
                                      else sorted(value["course_profiles"]))
    normalized["expires_at"] = utc_text(expiry)
    if image["mode"] == "prebuilt":
        normalized["image"]["course_profiles"] = normalized["course_profiles"]
    return normalized, resolved, runtime


def tooling_version(repo):
    paths = [Path(__file__).resolve(), SPEC / "profiles.json", SPEC / "config.schema.json", SPEC / "manifest.schema.json",
             SPEC / 'profiles.v2.json', SPEC / 'config.v2.schema.json', SPEC / 'manifest.v2.schema.json',
             SPEC / 'content-binding.schema.json', Path(__file__).with_name('workshop_content.py'), SPEC / 'runtime/multilingual.py']
    hashes = {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return {
        "git_sha": git(repo, "rev-parse", "HEAD").strip(),
        "tool_version": TOOL_VERSION,
        "tooling_sha256": canonical_hash(hashes),
        "working_tree_dirty": bool(git(repo, "status", "--porcelain", "--", "tools/workshop_config.py", "workshop/aws").strip()),
    }


def build_manifest(config, resolved, runtime, *, previous=None, now=None, repo=REPO):
    """Retain withdrawn identities so shrinking a roster cannot erase cleanup context."""
    now = now or datetime.now(timezone.utc)
    event = {key: config[key] for key in ("event_id", "owner_id", "aws_partition", "aws_account_id", "region")}
    state = {"scope": "event", "key": "/".join([
        "aws-workshop", event["aws_partition"], event["aws_account_id"],
        event["region"], event["event_id"], "terraform.tfstate",
    ])}
    allocations = {}
    if previous is not None:
        schema_check(previous, "manifest.schema.json")
        check_plain_strings(previous)
        if previous['schema_version'] != config['schema_version'] or previous['desired'].get('content') != config.get('content'):
            raise ConfigError('previous: content binding changed; use an explicit update on a fresh installation')
        if previous["event"] != event or previous["state"] != state:
            raise ConfigError("previous: account, partition, region, event, owner and state must match")
        # Check ownership as well as JSON types before reusing local operational data.
        for key, allocation in previous["allocations"].items():
            if allocation["terraform_key"] != key or allocation["owner_key"] != event["event_id"] + "/" + key:
                raise ConfigError("previous.allocations: inconsistent ownership")
            expected_role = "spare" if key.startswith("spare-") else "learner"
            if allocation["role"] != expected_role:
                raise ConfigError("previous.allocations: inconsistent role")
            allocations[key] = {**allocation, "active": False}
    desired = {key: "learner" for key in config["learner_ids"]}
    desired.update({f"spare-{number:03d}": "spare" for number in range(1, config["spare_count"] + 1)})
    for key, role in desired.items():
        allocations[key] = {
            "terraform_key": key,
            "owner_key": event["event_id"] + "/" + key,
            "role": role,
            "active": True,
        }
    manifest = {
        "schema_version": config['schema_version'],
        "kind": "aws-workshop-plan",
        "status": "planned",
        "generated_at": utc_text(now),
        "config_sha256": canonical_hash(config),
        "event": event,
        "state": state,
        "versions": {"course_ref": config["course_ref"], "deployment": tooling_version(repo)},
        "runtime": runtime,
        "desired": {
            "expires_at": config["expires_at"],
            "image": config["image"],
            "instance_type": config["instance_type"],
            "disk_gib": config["disk_gib"],
            "budget_usd": config["budget_usd"],
            "network_mode": config["network_mode"],
            "domain": config["domain"],
            "auth": config["auth"],
            "profiles": resolved,
        },
        "allocations": dict(sorted(allocations.items())),
    }
    if config['schema_version'] == 2:
        manifest['desired']['content'] = config['content']
    schema_check(manifest, "manifest.schema.json")
    return manifest


def write_manifest(path, manifest):
    """Atomically create a new private file; never overwrite an operational record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise ConfigError("output: already exists; choose a new path and pass the old file as --previous") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "manifest"):
        cmd = commands.add_parser(command)
        cmd.add_argument("--config", required=True, type=Path)
        if command == "manifest":
            cmd.add_argument("--output", required=True, type=Path)
            cmd.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    try:
        config, resolved, runtime = validate_config(load_document(args.config))
        if args.command == "manifest":
            previous = load_document(args.previous) if args.previous else None
            manifest = build_manifest(config, resolved, runtime, previous=previous)
            write_manifest(args.output, manifest)
            print("Planning manifest written; no cloud resources have been created or verified.")
        else:
            print(f"Valid offline configuration: {len(config['learner_ids'])} learners, {len(resolved)} profiles.")
        return 0
    except (ConfigError, OSError) as exc:
        message = str(exc) if isinstance(exc, ConfigError) else "output: cannot write planning manifest"
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
