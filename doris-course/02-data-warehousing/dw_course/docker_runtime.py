"""Explicit opt-in lifecycle for course 02's own single-node Compose project."""

import os
import subprocess

import pymysql

from .runtime import COURSE_ROOT, WarehouseLab

COMPOSE_FILE = COURSE_ROOT / "environments/single-node/compose.yml"
PROJECT = "doris-warehousing-course"
CONNECTION = {
    "DW_HOST": "127.0.0.1",
    "DW_PORT": "52030",
    "DW_BE_HTTP_URL": "http://127.0.0.1:51040",
    "DW_USER": "root",
    "DW_PASSWORD": "",
}


def compose_command(*arguments):
    return [
        "docker", "compose", "--project-name", PROJECT,
        "--file", str(COMPOSE_FILE), *arguments,
    ]


def prepare_environment(*, start=False):
    """Start only on explicit opt-in; apply connection settings after health succeeds."""
    if not start and os.environ.get("DW_START_SANDBOX") != "yes":
        raise RuntimeError("Set DW_START_SANDBOX=yes only to start course 02's Docker sandbox")
    subprocess.run(compose_command("config", "--quiet"), check=True, timeout=30)
    # The pinned image supplies the container healthcheck.
    subprocess.run(
        compose_command("up", "-d", "--wait", "--wait-timeout", "300"),
        check=True, timeout=1800,
    )
    connection = pymysql.connect(
        host=CONNECTION["DW_HOST"], port=int(CONNECTION["DW_PORT"]),
        user=CONNECTION["DW_USER"], password=CONNECTION["DW_PASSWORD"],
        connect_timeout=5, read_timeout=10, autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("Sandbox SQL readiness check failed")
            cursor.execute('SELECT SUM(number) FROM numbers("number"="10")')
            if cursor.fetchone() != (45,):
                raise RuntimeError("Sandbox BE execution check failed")
    finally:
        connection.close()
    os.environ.update(CONNECTION)
    print("Sandbox ready on FE 52030 / BE HTTP 51040; named volumes retained.")
    return dict(CONNECTION)


def connect_sandbox():
    """Connect this notebook to the course container without starting Docker."""
    os.environ.update(CONNECTION)
    return WarehouseLab(allow_writes=True)
