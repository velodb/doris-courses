"""SSM diagnostics with strict output projection and bounded private evidence."""

import base64
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import zlib

import workshop_config as wc
import workshop_preflight as pf

NODE_PATH = wc.SPEC / "operations/node.py"
spec = importlib.util.spec_from_file_location("workshop_operations_node", NODE_PATH)
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)
TERMINAL = {"Success", "Failed", "Cancelled", "TimedOut", "Delivery Timed Out", "Execution Timed Out"}
PENDING = {"Pending", "InProgress", "Delayed", "Cancelling"}


def invocation_summary(value):
    """Preserve fault/command identity without persisting any SSM response body."""
    safe = {"Status": node.enum(value.get("Status"), TERMINAL | PENDING), "raw_output": "suppressed"}
    for key, pattern in (("CommandId", r"[0-9a-f-]{36}"), ("InstanceId", r"i-[0-9a-f]{8,17}")):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(pattern, item):
            safe[key] = item
    if type(value.get("ResponseCode")) is int:
        safe["ResponseCode"] = value["ResponseCode"]
    for key in ("RequestedDateTime", "ExecutionStartDateTime", "ExecutionEndDateTime"):
        item = value.get(key)
        if isinstance(item, datetime):
            safe[key] = wc.utc_text(item)
    return safe


def safe_terraform_log(returncode):
    # Terraform errors can contain provider response bodies, user-data or state.
    # Do not persist arbitrary text, even in private diagnostic files.
    return json.dumps({"component": "terraform", "returncode": returncode,
                       "detail": "raw-output-suppressed"}) + "\n"


def prune_local(directory, days=None, max_files=20):
    policy = Path(directory) / "operations-policy.json"
    if days is None:
        days = wc.load_document(policy).get("retention_days", 7) if policy.exists() else 7
    pf.require(type(days) is int and 1 <= days <= 30, "diagnose: invalid stored retention policy")
    paths = [p for pattern in ("terraform-*.log", "diagnostic-*.json") for p in Path(directory).glob(pattern)
             if p.is_file() and not p.is_symlink()]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    total = 0
    for index, path in enumerate(paths):
        total += path.stat().st_size
        if index >= max_files or path.stat().st_mtime < cutoff or total > 10 * 1048576:
            path.unlink()


def command(action, config, learner, runtime_sha256, days):
    pf.require(action in {"diagnose", "configure"} and 1 <= days <= 30, "diagnose: invalid action or retention")
    # These values have already passed the event schema; validate again before
    # constructing shell input. No arbitrary operator command option is exposed.
    for text in (config["event_id"], learner):
        pf.require(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", text), "diagnose: invalid target binding")
    pf.require(re.fullmatch(r"[0-9a-f]{40}", config["course_ref"]) and re.fullmatch(r"[0-9a-f]{64}", runtime_sha256),
               "diagnose: invalid version binding")
    payload = base64.b64encode(zlib.compress(NODE_PATH.read_bytes())).decode()
    arguments = [action, "--event", config["event_id"], "--learner", learner,
                 "--course-ref", config["course_ref"], "--runtime-sha256", runtime_sha256,
                 "--retention-days", str(days)]
    if config["image"]["mode"] == "prebuilt":
        arguments.append("--prebuilt")
    script = ("import base64,os,pathlib,runpy,sys,zlib;os.umask(0o077);"
              "p=pathlib.Path('/run/doris-workshop-operations.py');"
              f"p.write_bytes(zlib.decompress(base64.b64decode({payload!r})));p.chmod(0o600);"
              f"sys.argv=[str(p)]+{arguments!r};runpy.run_path(str(p),run_name='__main__')")
    return "python3.12 -c " + shlex.quote(script)


def project(value, config, binding, kernels):
    pf.require(isinstance(value, dict) and value.get("schema_version") == 1, "diagnose: invalid node report; raw output discarded")
    units = value.get("units", {})
    pf.require(isinstance(units, dict), "diagnose: invalid unit report")
    safe_units = {}
    for name in node.UNITS:
        row = units.get(name, {})
        if not isinstance(row, dict):
            row = {}
        safe_units[name] = {key: node.enum(row.get(key), values) for key, values in node.STATES.items()}
    runtime = value.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    # The remote projection has no original version strings. Revalidate every
    # field and require all expected checks, rather than trusting ready alone.
    checks = runtime.get("checks", [])
    locales = config.get('content', {}).get('available_locales', [])
    required = node.required_checks(kernels, locales)
    clean = node.probe_projection({"scope": "host-runtime", "course_ref": config["course_ref"],
        "runtime_sha256": binding["runtime_sha256"], "checks": checks, "ready": runtime.get("ready")},
        config["course_ref"], binding["runtime_sha256"], kernels, locales)
    clean["version_matches"] = runtime.get("version_matches") is True
    clean["ready"] = bool(clean["ready"] and clean["version_matches"] and required.issubset(r["check"] for r in clean["checks"]))
    logs = value.get("logs", {})
    safe_logs = {}
    for name in ("bootstrap", "cloud-init", "image-start"):
        row = logs.get(name, {}) if isinstance(logs, dict) else {}
        if not isinstance(row, dict):
            row = {}
        safe_logs[name] = {reason: min(65536, max(0, count)) for reason, count in row.items()
                           if isinstance(reason, str) and reason in node.REASONS and type(count) is int}
    return {"units": safe_units, "runtime": clean, "logs": safe_logs,
            "last_install_stage": node.enum(value.get("last_install_stage"), node.STAGES),
            "probe_outcome": node.enum(value.get("probe_outcome"), {"completed", "timeout-or-unavailable"}),
            "log_policy_installed": value.get("log_policy_installed") is True,
            "gateway": "not-installed" if safe_units["gateway"]["LoadState"] == "not-found" else "installed-unverified",
            "shared_access_validated": False, "course_labs_validated": False}


def diagnose(controller, *, learner=None, configure=False, days=7, timeout=300):
    pf.require(1 <= days <= 30, "diagnose: retention must be between 1 and 30 days")
    pf.require(learner is None or learner in controller.config["learner_ids"], "diagnose: unknown learner")
    instances, _ = controller.inventory()
    selected = {k: v for k, v in instances.items() if learner is None or k == learner}
    pf.require(bool(selected), "diagnose: no managed target")
    actual = controller.actual_instances(selected.values())
    # Validate ALL assignments before dispatching to ANY target.
    for who, instance in selected.items():
        row = actual.get(instance, {})
        tags = {r["Key"]: r["Value"] for r in row.get("Tags", [])}
        pf.require(tags.get("Learner") == who, "diagnose: actual learner assignment differs from state")
    state_path = controller.directory / "operations-state.json"
    state = wc.load_document(state_path) if state_path.exists() else {}
    from workshop import atomic_json
    atomic_json(controller.directory / "operations-policy.json", {"retention_days": days})
    report = {"schema_version": 1, "event_id": controller.config["event_id"], "region": controller.config["region"],
              "course_ref": controller.config["course_ref"], "runtime_sha256": controller.binding["runtime_sha256"],
              "overlay_sha256": pf.file_hash(NODE_PATH), "retention_days": days, "nodes": {},
              "scope": "infrastructure-diagnostics", "shared_access_validated": False, "course_labs_validated": False}
    kernels = [c["kernel"] for c in wc.load_document(Path(controller.pilot["runtime_bundle_dir"]) / "runtime.json")["courses"]]
    action = "configure" if configure else "diagnose"
    client = controller.cloud.client("ssm")
    for who, instance in selected.items():
        target = {"instance_id": instance, "ec2": actual[instance].get("State", {}).get("Name", "unknown")}
        report["nodes"][who] = target
        if target["ec2"] != "running":
            target["outcome"] = "instance-not-running"
            continue
        info = controller.cloud.call("ssm", "describe_instance_information", Filters=[{"Key": "InstanceIds", "Values": [instance]}])["InstanceInformationList"]
        if not info or info[0].get("PingStatus") != "Online":
            target["outcome"] = "ssm-offline"
            continue
        key = who + ":" + action
        entry = state.get(key, {})
        if entry.get("status") in TERMINAL:
            entry = {}
        if entry:
            pf.require(entry.get("instance_id") == instance and entry.get("overlay_sha256") == report["overlay_sha256"]
                       and entry.get("retention_days") == days, "diagnose: pending command binding changed; resolve original SSM command")
        else:
            entry = {"instance_id": instance, "overlay_sha256": report["overlay_sha256"], "retention_days": days,
                     "status": "DispatchUnknown"}
            state[key] = entry
            atomic_json(state_path, state)
            result = client.send_command(InstanceIds=[instance], DocumentName="AWS-RunShellScript", TimeoutSeconds=60,
                Comment="workshop controlled infrastructure " + action,
                CloudWatchOutputConfig={"CloudWatchOutputEnabled": False},
                Parameters={"commands": [command(action, controller.config, who, controller.binding["runtime_sha256"], days)],
                            "executionTimeout": ["300"]})
            entry["command_id"] = result["Command"]["CommandId"]
            entry["status"] = "Pending"
            atomic_json(state_path, state)
        pf.require(entry["status"] != "DispatchUnknown", "diagnose: dispatch outcome unknown; inspect SSM history; no command resent")
        target["command_id"] = entry["command_id"]
        deadline = controller.clock() + timeout
        while True:
            result = controller.cloud.command(entry["command_id"], instance, probe=True)
            status = result.get("Status")
            pf.require(status in TERMINAL | PENDING, "diagnose: unknown SSM status")
            entry["status"] = status
            atomic_json(state_path, state)
            if status in TERMINAL:
                target["outcome"] = status
                if status == "Success":
                    try:
                        value = json.loads(result.get("StandardOutputContent", ""))
                    except (ValueError, TypeError):
                        value = None
                    if configure:
                        target["configured"] = isinstance(value, dict) and value.get("configured") is True
                    else:
                        target.update(project(value, controller.config, controller.binding, kernels))
                break
            if controller.clock() >= deadline:
                target["outcome"] = "wait-timeout"
                break
            controller.sleep(min(3, max(0, deadline - controller.clock())))
    report["ok"] = all(r.get("outcome") == "Success" and (r.get("configured") is True if configure else True)
                       for r in report["nodes"].values())
    report["action"] = action
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(controller.directory / f"diagnostic-{stamp}.json", report)
    prune_local(controller.directory, days)
    return report
