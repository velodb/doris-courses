#!/usr/bin/env python3
"""Write per-learner browser routes; no proxy, AWS calls or internal URL changes."""

import argparse
import importlib.util
from pathlib import Path
import sys

import workshop_config as wc

spec = importlib.util.spec_from_file_location("workshop_browser", wc.REPO / "doris-course/common/doris_labkit/browser.py")
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)


def build_routes(config, learner_id, ports):
    if learner_id not in config["learner_ids"]:
        raise wc.ConfigError("browser routes: learner is not assigned to this event")
    routes = {"schema_version": 1, "event_id": config["event_id"], "learner_id": learner_id, "ports": ports}
    try:
        return browser.validate_routes(routes, config["event_id"], learner_id)
    except ValueError as exc:
        raise wc.ConfigError("browser routes: invalid port or browser endpoint") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--learner", required=True)
    parser.add_argument("--ports", required=True, type=Path, help="JSON/YAML mapping from host ports to browser base URLs")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config, _, _ = wc.validate_config(wc.load_document(args.config))
        wc.write_manifest(args.output, build_routes(config, args.learner, wc.load_document(args.ports)))
        print("Browser routes written; endpoints must be provisioned separately.")
        return 0
    except (wc.ConfigError, OSError) as exc:
        print(str(exc) if isinstance(exc, wc.ConfigError) else "browser routes: cannot write output", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
