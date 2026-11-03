#!/usr/bin/env python3
"""Review and execute an activity's bindings-then-pilot cleanup under one fence."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

import workshop as lifecycle
import workshop_cleanup as cleanup
import workshop_config as wc
import workshop_preflight as pf
import workshop_ttl as ttl


def require(condition, message):
    if not condition:
        raise wc.ConfigError("shared cleanup: " + message)


def bound_inputs(controller, bindings_run, routes_file):
    """Validate explicit state targets before any Terraform or AWS mutation."""
    directory = Path(bindings_run).resolve()
    variables = wc.load_document(directory / "inputs.tfvars.json")
    backend = wc.load_document(directory / "backend.tfbackend.json")
    routes = wc.load_document(routes_file)
    config, pilot = controller.config, controller.pilot
    shared = controller.binding.get("shared_access")
    require(isinstance(shared, dict) and routes.get("terraform") == variables and
            variables.get("access") == shared, "use the bound shared access and exact route variables")
    require(all(variables.get(key) == value for key, value in {
        "aws_profile": config["aws_profile"], "expected_account_id": config["aws_account_id"],
        "region": config["region"], "owner_id": config["owner_id"], "event_id": config["event_id"],
        "expires_at": config["expires_at"]}.items()), "bindings variables belong to another activity")
    expected_key = f"aws-workshop/aws/{config['aws_account_id']}/{config['region']}/{config['event_id']}/access.tfstate"
    require(all(backend.get(key) == value for key, value in {
        "bucket": pilot["state_bucket_name"], "key": expected_key, "region": config["region"],
        "profile": config["aws_profile"], "allowed_account_ids": [config["aws_account_id"]],
        "encrypt": True, "use_lockfile": True}.items()), "bindings backend differs from the activity")
    require(shared.get("dns_account_id", config["aws_account_id"]) == config["aws_account_id"] and
            not variables.get("dns"), "separate DNS credentials are not supported by this cleanup")
    require(set(variables.get("learners", {})) == set(config["learner_ids"]), "bindings roster differs from the pilot")
    hosts = [row["hostname"] for row in variables["routes"].values()]
    if variables.get("portal"):
        hosts.append(variables["portal"]["hostname"])
    require(len(hosts) == len(set(hosts)) and hosts, "route hosts must be unique")
    return variables, backend, shared, hosts


class Terraform:
    def __init__(self, module, data_dir, *, runner=None):
        require(module in {"pilot", "access-bindings"}, "unsupported Terraform module")
        self.module = module
        self.data_dir = Path(data_dir).resolve()
        self.runner = runner or lifecycle.Terraform(self.data_dir)

    def call(self, args, *, capture=False):
        env = pf.explicit_environment()
        env["TF_DATA_DIR"] = str(self.data_dir / "terraform-data")
        return self.runner.execute([f"-chdir={pf.MODULES / self.module}", *map(str, args)], env, capture=capture)

    def init(self, backend):
        self.call(["init", "-input=false", "-lockfile=readonly", f"-backend-config={backend}"])

    def state(self):
        return cleanup.state_export(self.module, json.loads(self.call(["show", "-json"], capture=True)))

    def plan(self, variables, path):
        self.call(["plan", "-destroy", "-input=false", "-lock-timeout=60s", f"-var-file={variables}", f"-out={path}"])
        os.chmod(path, 0o600)
        return json.loads(self.call(["show", "-json", str(path)], capture=True))

    def apply(self, path):
        self.call(["apply", "-input=false", "-lock-timeout=60s", str(path)])


def state_manifest(controller, bindings, pilot, shared, hosts):
    return cleanup.prepare(controller.config, [bindings, pilot], state_bucket=controller.pilot["state_bucket_name"],
                           history=controller.record, shared=shared, hosts=hosts)


def validate_plans(manifest, states, plans):
    for module in ("access-bindings", "pilot"):
        resources = [r for r in states[module]["resources"] if r.get("mode", "managed") == "managed"]
        absent = cleanup.validate_destroy_plan(resources, plans[module])
        claims = [row for row in manifest["state"] if row["module"] == module]
        require(all(row["address"] in absent for row in claims if row.get("requires_refresh_absence")),
                "missing owned parents require explicit refresh deletion evidence")


def artifact_hashes(controller, bindings_run, routes_file):
    return {"pilot_binding": pf.file_hash(controller.directory / "binding.json"),
            "pilot_vars": pf.file_hash(controller.directory / "inputs.tfvars.json"),
            "pilot_backend": pf.file_hash(controller.directory / "backend.tfbackend.json"),
            "bindings_vars": pf.file_hash(Path(bindings_run) / "inputs.tfvars.json"),
            "bindings_backend": pf.file_hash(Path(bindings_run) / "backend.tfbackend.json"),
            "routes": pf.file_hash(routes_file), "tools": pf.tool_hash(),
            "pilot_module": pf.module_hash("pilot"), "bindings_module": pf.module_hash("access-bindings")}


def prepare(controller, bindings_run, routes_file, output, *, tf_factory=Terraform, now=None):
    bindings_run, routes_file = Path(bindings_run).resolve(), Path(routes_file).resolve()
    output = Path(output).resolve()
    require(not output.exists(), "use a fresh private cleanup directory")
    variables, backend, shared, hosts = bound_inputs(controller, bindings_run, routes_file)
    del variables, backend
    input_hashes = artifact_hashes(controller, bindings_run, routes_file)
    output.mkdir(mode=0o700, parents=True)
    modules = {}
    for name in ("access-bindings", "pilot"):
        path = output / name
        path.mkdir(mode=0o700)
        modules[name] = tf_factory(name, path)
        backend_path = Path(bindings_run) / "backend.tfbackend.json" if name == "access-bindings" else controller.directory / "backend.tfbackend.json"
        modules[name].init(backend_path)
    states = {name: modules[name].state() for name in modules}
    controller.inventory(resources=states["pilot"]["resources"])
    controller.actual_instances(controller.record["known_instances"])
    controller.save()
    manifest = state_manifest(controller, states["access-bindings"], states["pilot"], shared, hosts)
    plans = {}
    for name in ("access-bindings", "pilot"):
        var_path = Path(bindings_run) / "inputs.tfvars.json" if name == "access-bindings" else controller.directory / "inputs.tfvars.json"
        plans[name] = modules[name].plan(var_path, output / name / "destroy.tfplan")
    validate_plans(manifest, states, plans)
    require(input_hashes == artifact_hashes(controller, bindings_run, routes_file), "inputs changed while planning")
    now = now or datetime.now(timezone.utc)
    receipt = {"schema_version": 1, "kind": "aws-workshop-shared-cleanup-review", "event_id": controller.config["event_id"],
        "valid_until": wc.utc_text(now + timedelta(minutes=15)), "artifacts": input_hashes,
        "manifest_sha256": wc.canonical_hash(manifest),
        "plans": {name: pf.file_hash(output / name / "destroy.tfplan") for name in modules},
        "initialized_backends": {name: pf.file_hash(output / name / "terraform-data" / "terraform.tfstate") for name in modules},
        "order": ["access-bindings", "pilot"], "shared_resources_retained": True}
    wc.write_manifest(output / "manifest.json", manifest)
    wc.write_manifest(output / "review.json", receipt)
    return {"review_sha256": pf.file_hash(output / "review.json"), "order": receipt["order"],
            "shared_resources_retained": True, "valid_until": receipt["valid_until"]}


def apply(controller, bindings_run, routes_file, output, approved_hash, *, tf_factory=Terraform, now=None):
    bindings_run, routes_file = Path(bindings_run).resolve(), Path(routes_file).resolve()
    output = Path(output).resolve()
    review_path = output / "review.json"
    require(approved_hash and pf.file_hash(review_path) == approved_hash, "supply the reviewed receipt SHA256")
    receipt, manifest = wc.load_document(review_path), wc.load_document(output / "manifest.json")
    require(receipt.get("kind") == "aws-workshop-shared-cleanup-review" and receipt.get("schema_version") == 1 and
            receipt.get("event_id") == controller.config["event_id"] and receipt.get("order") == ["access-bindings", "pilot"] and
            wc.timestamp(receipt["valid_until"], "valid_until") > (now or datetime.now(timezone.utc)), "receipt is invalid or expired")
    require(receipt["artifacts"] == artifact_hashes(controller, bindings_run, routes_file) and
            receipt["manifest_sha256"] == wc.canonical_hash(manifest), "inputs, source or manifest changed")
    for name in receipt["order"]:
        require(receipt["plans"][name] == pf.file_hash(output / name / "destroy.tfplan"), "saved destroy plan changed")
        require(receipt["initialized_backends"][name] == pf.file_hash(output / name / "terraform-data" / "terraform.tfstate"),
                "initialized Terraform backend changed")
    _, _, shared, hosts = bound_inputs(controller, bindings_run, routes_file)
    modules = {name: tf_factory(name, output / name) for name in receipt["order"]}
    states = {name: modules[name].state() for name in modules}
    controller.actual_instances(controller.record["known_instances"])
    controller.save()
    current = state_manifest(controller, states["access-bindings"], states["pilot"], shared, hosts)
    require(wc.canonical_hash(current) == receipt["manifest_sha256"], "state or resource ownership changed since review")
    plans = {name: json.loads(modules[name].call(["show", "-json", str(output / name / "destroy.tfplan")], capture=True)) for name in modules}
    validate_plans(manifest, states, plans)
    progress = output / "progress.json"
    require(not progress.exists(), "previous apply attempted; reconcile and create new plans")
    lifecycle.atomic_json(progress, {"phase": "applying-bindings", "resources_may_remain": True})
    modules["access-bindings"].apply(output / "access-bindings" / "destroy.tfplan")
    require(not modules["access-bindings"].state()["resources"], "bindings state is not empty")
    lifecycle.atomic_json(progress, {"phase": "applying-pilot", "resources_may_remain": True})
    modules["pilot"].apply(output / "pilot" / "destroy.tfplan")
    # The pilot reconciler checks known EC2, EBS and ENIs; the already empty
    # state causes no further Terraform mutation or nested fence acquisition.
    require(not modules["pilot"].state()["resources"], "pilot state is not empty")
    controller.tf = lifecycle.Terraform(output / "pilot")
    result = controller._destroy()
    lifecycle.atomic_json(progress, {"phase": "complete", "pilot_resources_reconciled": True,
                                    "other_activity_resources_may_remain": True, "shared_resources_retained": True})
    return {"phase": "complete", "shared_resources_retained": True,
            "cleanup_boundary": result["cleanup_boundary"], "full_shared_scan_complete": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply"))
    for name in ("pilot-run", "bindings-run", "routes", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ttl-binding", type=Path)
    parser.add_argument("--approved-review-sha256")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        with lifecycle.run_lock(args.pilot_run):
            controller = lifecycle.Controller(args.pilot_run, ttl_binding=args.ttl_binding)
            with ttl.activity_operation(controller.cloud, controller.config, controller.pilot, args.ttl_binding,
                                        cleanup_operation=True, scope_change=True):
                result = (prepare(controller, args.bindings_run, args.routes, args.output) if args.command == "plan" else
                          apply(controller, args.bindings_run, args.routes, args.output, args.approved_review_sha256))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        safe = str(exc) if isinstance(exc, wc.ConfigError) else "operation failed; inspect private state and reconcile before retrying"
        print(json.dumps({"error": safe, "resources_may_remain": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
