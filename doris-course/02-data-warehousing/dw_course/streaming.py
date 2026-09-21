"""Opt-in local streaming fixtures. Teaching SQL remains in the notebooks."""

import json
import re
import subprocess
import time
from uuid import uuid4

import requests

from .runtime import COURSE_ROOT, identifier, normalized

COMPOSE = COURSE_ROOT / "environments/streaming/compose.yml"
REST = "http://127.0.0.1:51881"


def compose(*arguments, input=None, timeout=120):
    result = subprocess.run(
        ["docker", "compose", "--file", str(COMPOSE), *arguments],
        input=input, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stdout)
    return result.stdout


def configure_streaming_port():
    """Discover Docker's dynamically assigned Flink host port."""
    output = compose("port", "jobmanager", "8081")
    match = re.search(r":(\d+)\s*$", output.strip())
    if match is None:
        raise RuntimeError(f"Could not determine Flink Web UI port: {output!r}")
    port = int(match.group(1))
    global REST
    REST = f"http://127.0.0.1:{port}"
    return port


def wait_for(read, accepts, *, description, timeout=180, check_health=None):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        if check_health is not None:
            check_health()
        last = read()
        if accepts(last):
            return last
        time.sleep(2)
    raise TimeoutError(f"{description}: last observation = {last!r}")


def check_resources():
    """Check Docker daemon capacity, not available RAM or a resource reservation."""
    result = subprocess.run(
        ["docker", "info", "--format", "{{json .}}"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=30, check=True,
    )
    info = json.loads(result.stdout)
    memory_gib = info["MemTotal"] / (1024 ** 3)
    cpus = info["NCPU"]
    print(f"Docker capacity: {memory_gib:.1f} GiB RAM, {cpus} CPUs", flush=True)
    if memory_gib < 18 or cpus < 4:
        raise RuntimeError(
            "This streaming lab profile requires Docker capacity of at least "
            "18 GiB RAM and 4 CPUs (12 GiB Doris cap plus 6 GiB dependency budget). "
            "Increase Docker Desktop resources or use a suitable local host. "
            "See environments/streaming/README.md. No containers were started."
        )
    print("Capacity is not free memory: check host/VM load and disk space before continuing.", flush=True)


def prepare_streaming(profile, *, start=False):
    if not start:
        raise ValueError("Read environments/streaming/README.md, then pass start=True")
    if profile not in ("kafka", "cdc"):
        raise ValueError("Choose kafka or cdc")
    print(compose("--profile", profile, "up", "-d", timeout=1800))
    if profile == "cdc":
        print(f"Flink Web UI: {configure_streaming_port()}")
    # Startup failures are retried only here; operational SQL failures are not hidden.
    def ready():
        try:
            if profile == "kafka":
                kafka("kafka-topics.sh", "--list")
            else:
                mysql("SELECT COUNT(*) FROM course_cdc.orders")
                response = requests.get(REST + "/overview", timeout=5)
                response.raise_for_status()
                return response.json()["slots-total"] >= 1
            return True
        except (RuntimeError, requests.RequestException) as error:
            return str(error)
    wait_for(ready, lambda value: value is True, description=f"{profile} startup", timeout=300)


def kafka(script, *arguments, input=None):
    return compose("exec", "-T", "kafka", "/opt/kafka/bin/" + script,
                   "--bootstrap-server", "course-stream-kafka:9092", *arguments,
                   input=input)


def produce(topic, rows):
    return kafka("kafka-console-producer.sh", "--topic", topic,
                 input="".join(json.dumps(row) + "\n" for row in rows))


def mysql(sql):
    return compose("exec", "-T", "mysql", "env", "MYSQL_PWD=course_stream_local_only",
                   "mysql", "--protocol=TCP", "-h127.0.0.1", "-uroot", "--batch", "--raw", "-e", sql)


def flink_api(path):
    response = requests.get(REST + path, timeout=10)
    response.raise_for_status()
    return response.json()


def job_state(job):
    state = flink_api(f"/jobs/{job}")["state"]
    if state in {"FAILED", "CANCELED"}:
        raise RuntimeError(f"Flink job {job} is {state}; see {REST}/#/job/{job}/exceptions")
    return state


def check_flink_job(job):
    state = job_state(job)
    if state in {"FINISHED", "SUSPENDED"}:
        raise RuntimeError(f"Flink job {job} unexpectedly {state}; see {REST}/#/job/{job}/exceptions")


def check_routine_load(lab, job):
    job = identifier(job)
    statement = f"SHOW ALL ROUTINE LOAD FOR {job}"
    with lab.connection.cursor() as cursor:
        cursor.execute(statement)
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    if len(rows) != 1:
        raise RuntimeError(f"Expected one Routine Load job {job}; run {statement}: {rows!r}")
    status = rows[0]
    if status["State"] in {"PAUSED", "STOPPED", "CANCELLED"}:
        raise RuntimeError(
            f"Routine Load {job}: {status['State']}; "
            f"ReasonOfStateChanged={status['ReasonOfStateChanged']}; "
            f"ErrorLogUrls={status['ErrorLogUrls']}; run {statement}"
        )


def submit_sql(sql):
    # Separate files prevent overwriting the SQL of a previous session.
    path = "/tmp/course-" + uuid4().hex + ".sql"
    output = compose("exec", "-T", "jobmanager", "sh", "-c",
                     f"cat > {path} && /opt/flink/bin/sql-client.sh -f {path}",
                     input=sql, timeout=180)
    print(output)
    jobs = re.findall(r"Job ID:\s*([0-9a-f]{32})", output)
    if len(jobs) != 1 or "[ERROR]" in output:
        raise RuntimeError("Expected one successful INSERT job; inspect SQL client output")
    job = jobs[0]
    wait_for(lambda: job_state(job),
             lambda state: state == "RUNNING", description="Flink RUNNING")
    return job


def wait_checkpoint(job):
    return wait_for(lambda: flink_api(f"/jobs/{job}/checkpoints"),
                    lambda result: result["counts"]["completed"] > 0,
                    description=f"completed checkpoint for {job}",
                    check_health=lambda: check_flink_job(job))


def stop_with_savepoint(job):
    if re.fullmatch(r"[0-9a-f]{32}", job) is None:
        raise ValueError("Invalid Flink job ID")
    output = compose("exec", "-T", "jobmanager", "/opt/flink/bin/flink", "stop",
                     "--savepointPath", "file:///opt/flink/state/savepoints", job,
                     timeout=180)
    print(output)
    match = re.search(r"Savepoint completed\. Path: (\S+)", output)
    if match is None:
        raise RuntimeError("No completed savepoint in CLI output")
    wait_for(lambda: job_state(job),
             lambda state: state == "FINISHED", description="stopped Flink job")
    return match.group(1)


def wait_rows(lab, table, expected, *, check_health):
    table = identifier(table)
    return wait_for(
        lambda: lab.query(f"SELECT * FROM {table} ORDER BY order_id"),
        lambda rows: normalized(rows) == normalized(expected),
        description=f"{table} matches expected rows",
        check_health=check_health,
    )
