"""Optional, instance-local self-study records shared by the reader and quizzes.

This module uses only the standard library. Its JSON format is independent of
the immutable course content contract. Records are not examination credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 2000
FLAGS = ("course_read", "lab_opened", "lab_declared_complete", "quiz_passed")
FIELDS = {"course_slug", "module_id", *FLAGS, "best_correct", "best_total",
          "content_version", "source_ref", "updated_at"}
IDENTITY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class ProgressError(ValueError):
    """A portable record or requested operation is invalid."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def empty(module):
    return {"course_slug": module["course_slug"], "module_id": module["id"],
            **dict.fromkeys(FLAGS, False), "best_correct": 0, "best_total": 0,
            "content_version": module["content_version"], "source_ref": module["source_ref"],
            "updated_at": now()}


def registry(plan):
    """Use only the selected published modules and their permanent manifest IDs."""
    result = {}
    for course in plan["courses"]:
        if course.get("status") not in {"published", "preview"}:
            continue
        for module in course["modules"]:
            if module.get("status") != "published":
                continue
            entry = {**module, "course_slug": course["slug"], "course_dir": course["course_dir"],
                     "source_ref": plan["course_ref"]}
            key = course["slug"], module["id"]
            if key in result:
                raise ProgressError("Duplicate module identity in the runtime plan.")
            result[key] = entry
    return result


def validate_document(data):
    """Bound size, shape, scalar types and values before any database transaction."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ProgressError("Progress files must be at most 1 MiB.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProgressError("Duplicate JSON fields are not supported.")
            result[key] = value
        return result

    try:
        document = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProgressError("The progress file is not valid JSON.") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "kind", "exported_at", "records"}:
        raise ProgressError("Unexpected progress file fields.")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ProgressError("Unsupported progress schema_version; expected 1.")
    if document["kind"] != "doris-workshop-progress":
        raise ProgressError("This is not a workshop progress file.")
    timestamp(document["exported_at"])
    records = document["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ProgressError("A progress file may contain at most 2000 module records.")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != FIELDS:
            raise ProgressError("Unexpected module record fields.")
        for field in ("course_slug", "module_id"):
            value = record[field]
            if not isinstance(value, str) or len(value) > 100 or not IDENTITY.fullmatch(value):
                raise ProgressError("Invalid permanent course or module identity.")
        key = record["course_slug"], record["module_id"]
        if key in seen:
            raise ProgressError("A module identity occurs more than once.")
        seen.add(key)
        if any(type(record[field]) is not bool for field in FLAGS):
            raise ProgressError("Completion flags must be JSON booleans.")
        correct, total = record["best_correct"], record["best_total"]
        if type(correct) is not int or type(total) is not int or not 0 <= correct <= total <= 10000:
            raise ProgressError("Invalid quiz score; expected 0 <= correct <= total <= 10000.")
        if record["quiz_passed"] and not total:
            raise ProgressError("A passed quiz must include a completed score.")
        if record["lab_declared_complete"] and not record["lab_opened"]:
            raise ProgressError("A completed Lab must have been opened.")
        for field, length in (("content_version", 64), ("source_ref", 40)):
            if not isinstance(record[field], str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", record[field]):
                raise ProgressError("Invalid content source version.")
        timestamp(record["updated_at"])
    return document


def timestamp(value):
    try:
        if not isinstance(value, str) or len(value) > 40 or datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError()
    except ValueError as exc:
        raise ProgressError("Expected a timestamp with a timezone.") from exc


def merge(current, incoming, module):
    """Union credit and retain the best exact fraction, independent of quiz size."""
    result = dict(current)
    for flag in FLAGS:
        result[flag] |= incoming[flag]
    a, b = incoming["best_correct"], incoming["best_total"]
    c, d = current["best_correct"], current["best_total"]
    if b and (not d or a * d > c * b or (a * d == c * b and b > d)):
        result.update(best_correct=a, best_total=b)
    # Keep a historical content binding visible if either source grants old credit.
    if incoming["content_version"] != module["content_version"]:
        result.update(content_version=incoming["content_version"], source_ref=incoming["source_ref"])
    # Reimporting the same record is a true no-op, including its timestamp.
    if any(result[field] != current[field] for field in FIELDS - {"updated_at"}):
        result["updated_at"] = now()
    return result


def view(module, record=None):
    record = record or empty(module)
    parts = sum(record[k] for k in ("course_read", "lab_declared_complete", "quiz_passed"))
    return {**record, "parts": parts, "complete": parts == 3,
            "content_updated": record["content_version"] != module["content_version"]}


def summary(modules, records):
    states = [view(m, records.get((m["course_slug"], m["id"]))) for m in modules if m.get("required")]
    parts, total = sum(s["parts"] for s in states), len(states) * 3
    return {"parts": parts, "total": total, "percent": round(parts / total * 100, 1) if total else None,
            "complete": sum(s["complete"] for s in states), "modules": len(states)}


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS progress (course TEXT, module TEXT, record TEXT NOT NULL, PRIMARY KEY(course,module))")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def _read(connection):
        return {(c, m): json.loads(r) for c, m, r in connection.execute("SELECT course,module,record FROM progress")}

    @staticmethod
    def _write(connection, record):
        connection.execute("INSERT INTO progress VALUES (?,?,?) ON CONFLICT(course,module) DO UPDATE SET record=excluded.record",
                           (record["course_slug"], record["module_id"], json.dumps(record)))

    def records(self):
        with self.connection() as connection:
            return self._read(connection)

    def mark(self, module, field, value=True):
        if field not in {"course_read", "lab_opened", "lab_declared_complete"} or type(value) is not bool:
            raise ProgressError("Invalid completion operation.")
        with self.connection() as connection:
            record = self._read(connection).get((module["course_slug"], module["id"]), empty(module))
            if field == "lab_declared_complete" and value and not record["lab_opened"]:
                raise ProgressError("Open this module's Lab before marking it complete.")
            if field == "lab_opened" and not value:
                raise ProgressError("Lab opening cannot be cancelled.")
            record.update({field: value, "updated_at": now()})
            self._write(connection, record)

    def quiz(self, module, correct, total, passing_score_percent):
        if (type(correct) is not int or type(total) is not int or not 0 <= correct <= total <= 10000 or not total or
                type(passing_score_percent) is not int or not 1 <= passing_score_percent <= 100):
            raise ProgressError("Invalid completed quiz score or passing threshold.")
        incoming = empty(module)
        incoming.update(best_correct=correct, best_total=total, quiz_passed=correct * 100 >= passing_score_percent * total)
        with self.connection() as connection:
            current = self._read(connection).get((module["course_slug"], module["id"]), empty(module))
            result = merge(current, incoming, module)
            self._write(connection, result)
        return result

    def export(self):
        records = self.records()
        data = json.dumps({"schema_version": 1, "kind": "doris-workshop-progress", "exported_at": now(),
                           "records": [records[k] for k in sorted(records)]}, indent=2).encode()
        validate_document(data)
        return data

    def preview(self, document, modules):
        current = self.records()
        result = []
        for record in document["records"]:
            key = record["course_slug"], record["module_id"]
            module = modules.get(key)
            result.append({"record": record, "matched": module is not None,
                           "content_updated": bool(module and record["content_version"] != module["content_version"]),
                           "result": merge(current.get(key, empty(module)), record, module) if module else None})
        return result

    def import_document(self, document, modules):
        # Validate again even for non-HTTP callers; nothing writes before validation.
        document = validate_document(json.dumps(document).encode())
        count = 0
        with self.connection() as connection:
            current = self._read(connection)
            for incoming in document["records"]:
                key = incoming["course_slug"], incoming["module_id"]
                if key not in modules:
                    continue
                module = modules[key]
                result = merge(current.get(key, empty(module)), incoming, module)
                self._write(connection, result)
                count += 1
        return count


def save_quiz_from_environment(path, correct, total, threshold):
    """Return None when disabled, or save for the exact selected quiz source path."""
    db = os.environ.get("DORIS_WORKSHOP_PROGRESS_DB")
    if not db:
        return None
    plan = json.loads(Path(os.environ["DORIS_WORKSHOP_PROGRESS_PLAN"]).read_text())
    source = Path(os.environ["DORIS_WORKSHOP_PROGRESS_SOURCE"]).resolve()
    quiz_path = Path(path).resolve()
    for module in registry(plan).values():
        if (source / module["course_dir"] / module["quiz"]).resolve() == quiz_path:
            return Store(db).quiz(module, correct, total, threshold)
    for course in plan['courses']:
        for part in course.get('optional', []):
            if part.get('quiz') and (source / course['course_dir'] / part['quiz']).resolve() == quiz_path:
                return {'optional': True}
    raise ProgressError("This quiz is not selected in the current workshop; progress was not saved.")
