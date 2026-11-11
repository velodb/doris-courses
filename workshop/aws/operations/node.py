"""Bounded, content-free infrastructure diagnostics for dedicated workshop hosts.

Only fixed enums, booleans and counts leave the node. Never return journal text,
container output, environment, user-data, credentials or learner file contents.
This stdlib-only overlay can also be used with older validated course images.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

UNITS = {
    "cloud-init": "cloud-final.service", "docker": "docker.service",
    "jupyter": "doris-workshop-jupyter.service", "gateway": "doris-workshop-gateway.service",
    "expiry": "doris-workshop-expire.timer",
}
STATES = {
    "LoadState": {"loaded", "not-found", "masked", "error", "bad-setting"},
    "ActiveState": {"active", "inactive", "failed", "activating", "deactivating", "reloading"},
    "Result": {"success", "exit-code", "signal", "core-dump", "timeout", "resources", "start-limit-hit", "dependency", "oom-kill"},
}
CHECKS = {"target-platform", "learner-identity", "source-version", "docker-daemon-and-permission",
          "compose", "jupyter-systemd", "jupyter-authenticated-loopback", "course-entry",
          "jupyter-widget-frontend", "course-service-inventory"}
CHECKS |= {kernel + ":" + suffix for kernel in ("doris-rta", "doris-dw", "doris-obs", "doris-ai")
           for suffix in ("notebook-bindings", "interpreter-imports-cwd-restart-docker")}
CHECKS |= {'content:en', 'content:kk-Cyrl'}
CHECKS |= {kernel + ':' + locale + ':interpreter-imports-cwd-restart-docker'
           for kernel in ('doris-rta', 'doris-dw', 'doris-obs', 'doris-ai')
           for locale in ('en', 'kk-Cyrl')}
STAGES = {"preflight", "system-packages", "user-and-docker", "course-environments", "jupyter-service", "readiness"}
REASONS = {"stage", "disk-full", "permission", "network", "dependency", "error", "completed", "suppressed"}
ROOT = Path("/")


def enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def run(command, timeout=15):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def unit_state(unit):
    result = run(["systemctl", "show", unit, "--property=LoadState,ActiveState,Result"])
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line) if result else {}
    return {key: enum(fields.get(key), values) for key, values in STATES.items()}


def required_checks(kernels, locales=(), *, course_files=False):
    base = CHECKS - {"course-service-inventory"} - {c for c in CHECKS if ":" in c}
    bindings = {kernel + ':notebook-bindings' for kernel in kernels}
    execution = ({kernel + ':' + locale + ':interpreter-imports-cwd-restart-docker'
                  for kernel in kernels for locale in locales} if course_files else
                 {kernel + ':interpreter-imports-cwd-restart-docker' for kernel in kernels})
    return base | bindings | execution | {'content:' + locale for locale in locales}


def probe_projection(value, course_ref, runtime_sha256, kernels=(), locales=(), *, course_files=False):
    """Validate even well-formed JSON as untrusted; strings must be fixed enums."""
    if not isinstance(value, dict):
        value = {}
    checks = value.get("checks", [])
    if not isinstance(checks, list):
        checks = []
    checks = [{"check": row["check"], "passed": row["passed"]} for row in checks
              if isinstance(row, dict) and isinstance(row.get("check"), str) and row["check"] in CHECKS and type(row.get("passed")) is bool]
    version = value.get("course_ref") == course_ref and value.get("runtime_sha256") == runtime_sha256
    return {"version_matches": version, "checks": checks,
            "ready": bool(version and value.get("scope") == "host-runtime" and checks and value.get("ready") is True
                          and required_checks(kernels, locales, course_files=course_files).issubset(r["check"] for r in checks)
                          and len({r["check"] for r in checks}) == len(checks) and all(r["passed"] for r in checks))}


def classify(line):
    # Re-project our own records too: never copy unexpected JSON fields.
    try:
        record = json.loads(line)
        if isinstance(record, dict) and isinstance(record.get("reason"), str) and record["reason"] in REASONS:
            safe = {"reason": record["reason"]}
            if record.get("reason") == "stage" and isinstance(record.get("stage"), str) and record["stage"] in STAGES:
                safe["stage"] = record["stage"]
            return safe
    except (ValueError, TypeError):
        pass
    stage = re.fullmatch(r"WORKSHOP_STAGE=([a-z-]+)\s*", line)
    if stage and stage[1] in STAGES:
        return {"reason": "stage", "stage": stage[1]}
    lowered = line.lower()
    for text, reason in (("no space left", "disk-full"), ("permission denied", "permission"),
                         ("could not resolve", "network"), ("connection timed out", "network"),
                         ("no matching distribution", "dependency"), ("error", "error"),
                         ("failed", "error"), ("host runtime checks passed", "completed")):
        if text in lowered:
            return {"reason": reason}
    return {"reason": "suppressed"}


def filter_stream(stream, output, log=None):
    # Bound input lines and output cardinality even for verbose or hostile tools.
    seen = set()
    while True:
        line = stream.readline(4096)
        if not line:
            break
        event = classify(line)
        key = json.dumps(event, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        output.write(key + "\n")
        output.flush()
        if log:
            append_log(log, key + "\n")


def append_log(path, line, max_bytes=1048576):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size + len(line.encode()) > max_bytes:
        Path(str(path) + ".2").unlink(missing_ok=True)
        first = Path(str(path) + ".1")
        if first.exists():
            first.replace(str(path) + ".2")
        path.replace(first)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(line)


def write_private(path, content, mode=0o600):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(mode)


def configure(days):
    if not 1 <= days <= 30:
        raise ValueError("retention")
    write_private("etc/doris-workshop/operations.json", json.dumps({"retention_days": days, "max_file_bytes": 1048576}))
    write_private("usr/local/lib/doris-workshop/operations.py", Path(__file__).read_text(), 0o644)
    logs = ROOT / "var/log/doris-workshop"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs.chmod(0o700)
    for name in ("bootstrap", "cloud-init", "image-start", "gateway-install"):
        path = logs / (name + ".log")
        if path.exists() and not path.is_symlink():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                records = []
                for line in stream.read(65536).decode(errors="replace").splitlines():
                    event = json.dumps(classify(line), sort_keys=True)
                    if not records or records[-1] != event:
                        records.append(event)
            path.write_text("\n".join(records) + "\n")
            path.chmod(0o600)
        for suffix in (".1", ".2", ".1.gz", ".2.gz"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
    # No HTTP/access/kernel/terminal output in the service journal. systemd's
    # fixed unit properties remain available for failure diagnosis.
    for service in ("jupyter", "gateway"):
        write_private(f"etc/systemd/system/{UNITS[service]}.d/operations.conf",
                      "[Service]\nStandardOutput=null\nStandardError=null\n")
    write_private("etc/systemd/journald.conf.d/doris-workshop.conf", f"[Journal]\nSystemMaxUse=32M\nRuntimeMaxUse=32M\nMaxRetentionSec={days}day\nMaxFileSec=1day\n")
    write_private("etc/logrotate.d/doris-workshop", "/var/log/doris-workshop/*.log {\n daily\n maxsize 1M\n rotate 2\n maxage " + str(days) + "\n missingok\n notifempty\n copytruncate\n create 0600 root root\n}\n")
    # The timer also expires summaries on quiet hosts, without touching state,
    # credentials, package receipts or learner data.
    write_private("etc/systemd/system/doris-workshop-log-prune.service",
                  "[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /usr/local/lib/doris-workshop/operations.py prune\n")
    write_private("etc/systemd/system/doris-workshop-log-prune.timer",
                  "[Timer]\nOnBootSec=5min\nOnUnitActiveSec=1h\n[Install]\nWantedBy=timers.target\n")
    for command in (["systemctl", "daemon-reload"], ["systemctl", "restart", "systemd-journald"],
                    ["systemctl", "enable", "--now", "doris-workshop-log-prune.timer"]):
        result = run(command)
        if result is None or result.returncode:
            raise ValueError("policy install")
    # systemd needs a service restart for StandardOutput changes, so configure
    # before admitting learners; this is an explicit maintenance operation.
    for key in ("jupyter", "gateway"):
        if unit_state(UNITS[key])["ActiveState"] == "active":
            result = run(["systemctl", "restart", UNITS[key]], timeout=60)
            if result is None or result.returncode:
                raise ValueError("service restart")


def prune():
    policy = json.loads((ROOT / "etc/doris-workshop/operations.json").read_text())
    cutoff = time.time() - int(policy["retention_days"]) * 86400
    for path in (ROOT / "var/log/doris-workshop").glob("*.log*"):
        if path.is_file() and not path.is_symlink():
            if path.stat().st_mtime < cutoff:
                path.unlink()
            elif path.stat().st_size > 1048576:
                # Old image logs may be raw. Discard them, never export them.
                path.write_text('{"reason":"suppressed"}\n')
                path.chmod(0o600)


def verify_activity(event, learner, prebuilt):
    activity_path = ROOT / "etc/doris-workshop/activity.json"
    activity = json.loads(activity_path.read_text()) if activity_path.exists() else {}
    if (prebuilt or activity) and (activity.get("event_id") != event or activity.get("learner_id") != learner):
        raise ValueError("binding")


def safe_probe():
    """Guard stdout of the original pinned checker on pre-overlay AMIs too."""
    path = ROOT / "etc/doris-workshop/runtime.json"
    plan = json.loads(path.read_text())
    course_ref = plan.get("course_ref", "")
    if not isinstance(course_ref, str) or not re.fullmatch(r"[0-9a-f]{40}", course_ref):
        raise ValueError("version")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    result = run([sys.executable, "-I", "/usr/local/lib/doris-workshop/check.py"], timeout=240)
    try:
        value = json.loads(result.stdout) if result else {}
    except ValueError:
        value = {}
    kernels = [c["kernel"] for c in plan["courses"]]
    projection = probe_projection(value, course_ref, checksum, kernels,
        plan.get('content', {}).get('available_locales', []),
        course_files=plan.get('content', {}).get('abi') == 'doris-workshop-content-3.0')
    services = []
    for row in value.get("services", []) if isinstance(value, dict) and isinstance(value.get("services"), list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        for family in ("doris-rta", "doris-warehousing-course", "doris-obs", "doris-ai"):
            if row["name"].startswith(family) and enum(row.get("state"), {"running", "created", "restarting", "removing", "paused", "exited", "dead"}) != "unknown":
                services.append({"name": family, "state": row["state"]})
    return {"scope": "host-runtime", "ready": projection["ready"], "checks": projection["checks"],
            "course_ref": course_ref, "runtime_sha256": checksum, "services": services, "course_labs_validated": False}


def collect(event, learner, course_ref, runtime_sha256, prebuilt):
    verify_activity(event, learner, prebuilt)
    units = {key: unit_state(name) for key, name in UNITS.items()}
    probe = run(["runuser", "--user", "workshop", "--", "env", "-i",
                 "HOME=/var/lib/doris-workshop/home", "PATH=/opt/doris-workshop/server-venv/bin:/usr/local/bin:/usr/bin:/bin",
                 "LANG=C.UTF-8", "PYTHONNOUSERSITE=1", "/opt/doris-workshop/server-venv/bin/python", "-I",
                 "/usr/local/lib/doris-workshop/check.py"], timeout=180)
    try:
        value = json.loads(probe.stdout) if probe else {}
    except ValueError:
        value = {}
    try:
        plan = json.loads((ROOT / "etc/doris-workshop/runtime.json").read_text())
        kernels = [c["kernel"] for c in plan["courses"]]
        locales = plan.get('content', {}).get('available_locales', [])
        course_files = plan.get('content', {}).get('abi') == 'doris-workshop-content-3.0'
    except (OSError, ValueError, KeyError, TypeError):
        kernels = []
        locales = []
        course_files = False
    logs = {}
    last_stage = "unknown"
    for name in ("bootstrap", "cloud-init", "image-start"):
        counts = {}
        path = ROOT / f"var/log/doris-workshop/{name}.log"
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                for line in stream.read(65536).decode(errors="replace").splitlines():
                    event = classify(line)
                    reason = event["reason"]
                    if name == "bootstrap" and "stage" in event:
                        last_stage = event["stage"]
                    counts[reason] = counts.get(reason, 0) + 1
        logs[name] = counts
    report = {"schema_version": 1, "units": units, "logs": logs, "last_install_stage": last_stage,
              "runtime": probe_projection(value, course_ref, runtime_sha256, kernels, locales, course_files=course_files),
              "probe_outcome": "timeout-or-unavailable" if probe is None else "completed",
              "log_policy_installed": (ROOT / "etc/doris-workshop/operations.json").exists(),
              "gateway": "not-installed" if units["gateway"]["LoadState"] == "not-found" else "installed-unverified",
              "shared_access_validated": False, "course_labs_validated": False}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("diagnose", "configure", "filter", "prune", "probe"))
    parser.add_argument("--event")
    parser.add_argument("--learner")
    parser.add_argument("--course-ref")
    parser.add_argument("--runtime-sha256")
    parser.add_argument("--prebuilt", action="store_true")
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "filter":
            filter_stream(sys.stdin, sys.stdout, args.log)
            return 0
        if args.action == "probe":
            report = safe_probe()
            print(json.dumps(report))
            return 0 if report["ready"] else 1
        if os.geteuid() != 0:
            raise ValueError("root")
        if args.action == "configure":
            if args.event:
                verify_activity(args.event, args.learner, args.prebuilt)
            configure(args.retention_days)
            prune()
            print('{"configured":true}')
        elif args.action == "prune":
            prune()
        else:
            print(json.dumps(collect(args.event, args.learner, args.course_ref, args.runtime_sha256, args.prebuilt)))
        return 0
    except Exception:
        # No exception text, traceback or raw subprocess body enters SSM.
        print('{"error":"node-operation-failed"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
