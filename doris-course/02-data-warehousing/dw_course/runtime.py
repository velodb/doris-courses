"""Explicit connection and assertion helpers; never start or stop a cluster."""

import json
import os
import re
from decimal import Decimal
from pathlib import Path

import pymysql
import requests

COURSE_ROOT = Path(__file__).resolve().parents[1]


def identifier(value):
    if re.fullmatch(r"[a-z][a-z0-9_]*", value) is None:
        raise ValueError("Expected a lowercase SQL identifier")
    return value


def fixture(name):
    """Read only committed fixtures by basename."""
    if Path(name).name != name:
        raise ValueError("Fixture must be a basename")
    return json.loads((COURSE_ROOT / "datasets" / name).read_text())


def normalized(value):
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, (tuple, list)):
        return [normalized(item) for item in value]
    return value


def expect(actual, expected):
    """Raise on mismatch even when Python runs with optimization enabled."""
    if normalized(actual) != normalized(expected):
        raise AssertionError(f"Expected {expected!r}, got {actual!r}")
    print("PASS", normalized(expected))


class WarehouseLab:
    def __init__(self):
        if os.environ.get("DW_ALLOW_WRITES") != "yes":
            raise RuntimeError("Read the reset scope, then set DW_ALLOW_WRITES=yes")
        self.database = identifier(os.environ.get("DW_DATABASE", "dw_course_l1_demo"))
        if not self.database.startswith("dw_course_l1_"):
            raise ValueError("Use a dedicated database with prefix dw_course_l1_")
        self.user = os.environ.get("DW_USER", "root")
        self.password = os.environ.get("DW_PASSWORD", "")
        self.connection = pymysql.connect(
            host=os.environ.get("DW_HOST", "127.0.0.1"),
            port=int(os.environ.get("DW_PORT", "9030")),
            user=self.user,
            password=self.password,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
            read_timeout=120,
            write_timeout=120,
        )
        self.execute(f"CREATE DATABASE IF NOT EXISTS {self.database}")
        self.execute(f"USE {self.database}")
        self.execute("SET time_zone = '+08:00'")
        self.execute("SET group_commit = 'off_mode'")

    def query(self, sql, params=None):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def execute(self, sql, params=None):
        with self.connection.cursor() as cursor:
            return cursor.execute(sql, params)

    def insert(self, table, columns, rows):
        table = identifier(table)
        columns = [identifier(col) for col in columns]
        statement = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))})"
        with self.connection.cursor() as cursor:
            return cursor.executemany(statement, rows)

    def stream_load(self, table, path, label, columns):
        """Use an explicitly configured BE HTTP endpoint; do not forward secrets on redirects."""
        table = identifier(table)
        endpoint = os.environ.get("DW_BE_HTTP_URL", "http://127.0.0.1:8040").rstrip("/")
        with Path(path).open("rb") as payload:
            response = requests.put(
                f"{endpoint}/api/{self.database}/{table}/_stream_load",
                auth=(self.user, self.password),
                headers={
                    "label": label,
                    "format": "csv",
                    "column_separator": ",",
                    "columns": columns,
                    "strict_mode": "true",
                    "max_filter_ratio": "0",
                    "group_commit": "off_mode",
                },
                data=payload,
                allow_redirects=False,
                timeout=120,
            )
        if 300 <= response.status_code < 400:
            raise RuntimeError("Set DW_BE_HTTP_URL to the trusted BE HTTP endpoint, not FE")
        response.raise_for_status()
        return response.json()

    def close(self):
        self.connection.close()
