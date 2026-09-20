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


def wait_for(read, accepts, *, description, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = read()
        if accepts(last):
            return last
        time.sleep(2)
    raise TimeoutError(f"{description}: last observation = {last!r}")


def prepare_streaming(profile, *, start=False):
    if not start:
        raise ValueError("Read environments/streaming/README.md, then pass start=True")
    if profile not in ("kafka", "cdc"):
        raise ValueError("Choose kafka or cdc")
    print(compose("--profile", profile, "up", "-d", timeout=1800))
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
                    description="completed checkpoint")


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


def wait_rows(lab, table, expected):
    table = identifier(table)
    return wait_for(
        lambda: lab.query(f"SELECT * FROM {table} ORDER BY order_id"),
        lambda rows: normalized(rows) == normalized(expected),
        description=f"{table} matches expected rows",
    )
