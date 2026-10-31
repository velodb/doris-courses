#!/usr/bin/env python3
"""Prepare shared-access routing and per-host overlays without calling AWS."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

import workshop_config as wc

SERVICES = {
    "jupyter": (8888, None),
    "rta-fe": (18030, "rta-main"),
    "rta-fe1": (18130, "rta-main"),
    "rta-fe2": (18131, "rta-main"),
    "rta-fe3": (18132, "rta-main"),
}
OVERLAY = wc.REPO / "workshop/aws/access"
OVERLAY_FILES = {"gateway.py", "jupyter_gateway.py", "requirements.txt", "doris-workshop-gateway.service", "install_gateway.sh"}


def require(condition, message):
    if not condition:
        raise wc.ConfigError("access: " + message)


def routes(config, access, instances, *, overrides=None, priority_base=1000):
    require(access["account_id"] == config["aws_account_id"] and access["region"] == config["region"] and
            access["domain"] == config["domain"], "shared access must match the event account, Region and domain")
    require(set(instances) == set(config["learner_ids"]) and len(set(instances.values())) == len(instances), "supply one distinct instance per assigned learner")
    require(type(priority_base) is int and 1 <= priority_base < 50000, "reserve an explicit listener priority range")
    selected = {name: port for name, (port, profile) in SERVICES.items() if profile is None or profile in config["course_profiles"]}
    overrides = overrides or {}
    require(set(overrides) <= set(instances), "port overrides include an unassigned learner")
    result, browser, services = {}, {}, {}
    for learner in sorted(instances):
        require(set(overrides.get(learner, {})) <= set(selected), "port overrides include an unselected service")
        browser[learner], services[learner] = {}, {}
        for name, default_port in selected.items():
            port = overrides.get(learner, {}).get(name, default_port)
            require(type(port) is int and 1024 <= port <= 65535 and port != 8080, "invalid loopback web port")
            host = f'{config["event_id"]}-{learner}-{name}.{config["domain"]}'
            require(len(host.split(".")[0]) <= 63 and len(host) <= 253, "shorten event/learner IDs to fit the certificate-covered DNS label")
            key = learner + ":" + name
            priority = priority_base + 3 * len(result)
            result[key] = {"learner_id": learner, "hostname": host, "priority": priority}
            require(str(port) not in browser[learner], "published services cannot share a loopback port")
            browser[learner][str(port)] = "https://" + host
            services[learner][host] = {"port": port, "jupyter": name == "jupyter", "redirect_origins": []}
    require(len(result) <= 33 and priority_base + 3 * len(result) <= 50001, "three rules per service exceed the reserved ALB rule/priority limit")
    return {"schema_version": 1, "event_id": config["event_id"], "expires_at": config["expires_at"],
            "alb_arn": access["alb_arn"], "region": config["region"], "services": services,
            "browser_routes": browser,
            "terraform": {"aws_profile": config["aws_profile"], "expected_account_id": config["aws_account_id"],
                "region": config["region"], "owner_id": config["owner_id"], "event_id": config["event_id"],
                "access": access, "learners": instances, "routes": result}}


def gateway_configs(plan, identity, subjects):
    require(identity["event_id"] == plan["event_id"], "identity belongs to another activity")
    require(set(subjects) == set(plan["services"]), "supply subjects for exactly the assigned learners")
    assigned = [s for values in subjects.values() for s in values]
    require(len(assigned) == len(set(assigned)), "one identity cannot belong to multiple learners")
    sys.path.insert(0, str(OVERLAY))
    from gateway import validate_config
    result = {}
    for learner, services in plan["services"].items():
        config = {"schema_version": 1, "event_id": plan["event_id"], "learner_id": learner,
            "expires_at": plan["expires_at"], "region": plan["region"], "alb_arn": plan["alb_arn"],
            "issuer": identity["issuer"], "client_id": identity["client_id"], "login_origin": identity["login_origin"],
            "subjects": subjects[learner], "services": services}
        validate_config(config)
        result[learner] = config
    return result


def bundle(directory):
    require(not directory.exists(), "select a new overlay output directory")
    directory.mkdir(parents=True, mode=0o700)
    for name in sorted(OVERLAY_FILES):
        shutil.copyfile(OVERLAY / name, directory / name)
    wc.write_manifest(directory / "overlay.json", {"schema_version": 1,
        "sha256": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in sorted(OVERLAY_FILES)}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    planp = commands.add_parser("routes")
    for name in ("config", "access", "instances", "output"):
        planp.add_argument("--" + name, type=Path, required=True)
    planp.add_argument("--ports", type=Path)
    planp.add_argument("--priority-base", type=int, required=True)
    hostp = commands.add_parser("gateways")
    for name in ("plan", "identity", "subjects", "output-dir"):
        hostp.add_argument("--" + name, type=Path, required=True)
    bundlep = commands.add_parser("bundle")
    bundlep.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "bundle":
            bundle(args.output_dir)
        elif args.command == "routes":
            config, _, _ = wc.validate_config(wc.load_document(args.config))
            value = routes(config, wc.load_document(args.access), wc.load_document(args.instances),
                overrides=wc.load_document(args.ports) if args.ports else None, priority_base=args.priority_base)
            wc.write_manifest(args.output, value)
            wc.write_manifest(args.output.with_suffix(".tfvars.json"), value["terraform"])
        else:
            require(not args.output_dir.exists(), "select a new gateway configuration directory")
            plan = wc.load_document(args.plan)
            configs = gateway_configs(plan, wc.load_document(args.identity), wc.load_document(args.subjects))
            args.output_dir.mkdir(parents=True, mode=0o700)
            for learner, config in configs.items():
                wc.write_manifest(args.output_dir / (learner + ".json"), config)
                wc.write_manifest(args.output_dir / (learner + "-browser.json"), {
                    "schema_version": 1, "event_id": plan["event_id"], "learner_id": learner,
                    "ports": plan["browser_routes"][learner]})
        print("Access files prepared; no AWS resources or accounts were created.")
    except (ValueError, KeyError, TypeError, OSError):
        print("access: invalid inputs or output path; inspect private configuration", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
