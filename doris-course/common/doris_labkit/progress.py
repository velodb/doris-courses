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
import hashlib
import uuid
import warnings

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 2000
FLAGS = ("course_read", "lab_opened", "lab_declared_complete", "quiz_passed")
FIELDS = {"course_slug", "module_id", *FLAGS, "best_correct", "best_total",
          "content_version", "source_ref", "updated_at"}
TABLES = {'progress': ('course', 'module', 'record'),
          'quiz_releases': ('id', 'snapshot'), 'quiz_attempts': ('id', 'request', 'record'),
          'portable_attempts': ('id', 'record'), 'legacy_records': ('id', 'record')}
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
    if not isinstance(document, dict):
        raise ProgressError("Unexpected progress file fields.")
    version = document.get('schema_version')
    if type(version) is not int or version not in {1, 2}:
        raise ProgressError("Unsupported progress schema_version; expected 1 or 2.")
    fields = {"schema_version", "kind", "exported_at", "records"}
    if version == 2:
        fields |= {'attempts', 'legacy_records'}
    if set(document) != fields:
        raise ProgressError("Unexpected progress file fields.")
    if document["kind"] != "doris-workshop-progress":
        raise ProgressError("This is not a workshop progress file.")
    timestamp(document["exported_at"])
    records = document["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ProgressError("A progress file may contain at most 2000 module records.")
    validate_records(records)
    if version == 2:
        legacy = document['legacy_records']
        if not isinstance(legacy, list) or len(legacy) > MAX_RECORDS:
            raise ProgressError('Invalid legacy progress history.')
        for record in legacy:
            validate_records([record])
        attempts = document['attempts']
        if not isinstance(attempts, list) or len(attempts) > MAX_RECORDS:
            raise ProgressError('Invalid Quiz attempt history.')
        seen = set()
        for attempt in attempts:
            validate_attempt(attempt)
            if attempt['attempt_id'] in seen:
                raise ProgressError('Duplicate Quiz attempt identity.')
            seen.add(attempt['attempt_id'])
        identities = {(r['course_slug'], r['module_id']) for r in records}
        if any((r['course_slug'], r['module_id']) not in identities for r in [*legacy, *attempts]):
            raise ProgressError('Quiz history must belong to an included module record.')
    return document


def validate_records(records):
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


def validate_attempt(record):
    fields = {'attempt_id', 'course_slug', 'module_id', 'quiz_id', 'locale', 'semantic_revision',
              'presentation_revision', 'policy_revision', 'source_digest', 'content_version', 'source_ref',
              'answers', 'correct', 'total', 'threshold', 'passed', 'submitted_at', 'trust'}
    if not isinstance(record, dict) or set(record) != fields or record['trust'] != 'self-study':
        raise ProgressError('Invalid Quiz attempt history.')
    if not isinstance(record['attempt_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', record['attempt_id']):
        raise ProgressError('Invalid Quiz attempt identity.')
    for field in ('course_slug', 'module_id'):
        if not isinstance(record[field], str) or len(record[field]) > 100 or not IDENTITY.fullmatch(record[field]):
            raise ProgressError('Invalid permanent course or module identity.')
    for field in ('quiz_id', 'semantic_revision', 'policy_revision'):
        v = record[field]
        if v is None and field != 'quiz_id':
            continue
        if not isinstance(v, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,199}', v):
            raise ProgressError('Invalid Quiz release binding.')
    if not isinstance(record['locale'], str) or not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,8}', record['locale']):
        raise ProgressError('Invalid Quiz release binding.')
    for field, size in (('source_digest', 64), ('content_version', 64), ('source_ref', 40), ('presentation_revision', 64)):
        v = record[field]
        if field == 'presentation_revision' and v is None:
            continue
        if not isinstance(v, str) or not re.fullmatch('[0-9a-f]{' + str(size) + '}', v):
            raise ProgressError('Invalid Quiz release binding.')
    if (len({record[f] is None for f in ('semantic_revision', 'policy_revision', 'presentation_revision')}) != 1 or
            (record['semantic_revision'] is None and record['locale'] != 'en')):
        raise ProgressError('Invalid Quiz release binding.')
    a, b, t = record['correct'], record['total'], record['threshold']
    if (any(type(v) is not int for v in (a, b, t)) or not 0 <= a <= b <= 10000 or not b or
            not 1 <= t <= 100 or type(record['passed']) is not bool or record['passed'] != (a * 100 >= b * t)):
        raise ProgressError('Invalid completed quiz score or passing threshold.')
    answers = record['answers']
    if (not isinstance(answers, dict) or len(answers) != b or any(
            not isinstance(v, str) or not re.fullmatch(r'[a-z0-9]+(?:_[a-z0-9]+)*', v)
            for pair in answers.items() for v in pair)):
        raise ProgressError('Invalid Quiz attempt answers.')
    timestamp(record['submitted_at'])


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
            first_upgrade = connection.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_records'").fetchone() is None
            connection.execute('CREATE TABLE IF NOT EXISTS quiz_releases (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS quiz_attempts (id TEXT PRIMARY KEY, request TEXT NOT NULL, record TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS portable_attempts (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS legacy_records (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
            if first_upgrade:
                self._backup(connection, label='before-upgrade')
                for record in self._read(connection).values():
                    self._legacy(connection, record)
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

    @staticmethod
    def _legacy(connection, record):
        raw = json.dumps(record, sort_keys=True, separators=(',', ':'))
        connection.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)', (hashlib.sha256(raw.encode()).hexdigest(), raw))

    def _backup(self, connection, label='before-import'):
        """Back up committed data while our transaction prevents other writers.

        A separate read connection also works on Python 3.9, without serialize().
        Uncommitted additive schema is intentionally absent from upgrade backups.
        """
        path = self.path.with_name(self.path.name + '.' + label + '-' + uuid.uuid4().hex + '.backup')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
        self.last_backup = path
        return path

    def backup(self):
        with self.connection() as connection:
            return self._backup(connection, 'manual')

    def restore_backup(self, path):
        """Operator rollback: atomically restore our tables, backing up current data.

        Stop learner writers before rollback. The normal downgrade keeps these
        additive tables and uses a v1 projection instead of deleting history.
        """
        source = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        try:
            if source.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ProgressError('Invalid progress backup.')
            available = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'progress' not in available:
                raise ProgressError('Invalid progress backup.')
            rows = {table: source.execute('SELECT ' + ','.join(columns) + ' FROM ' + table).fetchall()
                    if table in available else [] for table, columns in TABLES.items()}
        finally:
            source.close()
        with self.connection() as connection:
            self._backup(connection, 'before-restore')
            for table, columns in TABLES.items():
                connection.execute('DELETE FROM ' + table)
                connection.executemany('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', rows[table])
            # A legacy image has no attempt metadata to recover or fabricate.
            if 'legacy_records' not in available:
                for record in self._read(connection).values():
                    self._legacy(connection, record)

    @staticmethod
    def _attempts(connection):
        rows = {}
        for table in ('portable_attempts', 'quiz_attempts'):
            for identity, raw in connection.execute('SELECT id,record FROM ' + table):
                record = json.loads(raw)
                if identity in rows and rows[identity] != record:
                    raise ProgressError('Conflicting Quiz attempt identity.')
                rows[identity] = record
        return rows

    def attempts(self):
        with self.connection() as connection:
            rows = self._attempts(connection)
            return [rows[k] for k in sorted(rows)]

    def register_quiz(self, module, presentation):
        """Server/installation-only API. Never register browser-supplied answers."""
        from .quizsession import canonical, QuizConflict
        snapshot = presentation.snapshot()
        snapshot['quiz']['questions'] = sorted(snapshot['quiz']['questions'], key=lambda q: q['question_id'])
        for question in snapshot['quiz']['questions']:
            question['options'] = sorted(question['options'], key=lambda o: o['option_id'])
        snapshot['module'] = {k: module[k] for k in ('course_slug', 'id', 'content_version', 'source_ref')}
        raw = canonical(snapshot)
        with self.connection() as connection:
            for old_id, old_raw in connection.execute('SELECT id,snapshot FROM quiz_releases'):
                old = json.loads(old_raw)
                if old_raw == raw:
                    return old_id
                b = old['binding']
                if (presentation.semantic_revision is not None and b['quiz_id'] == presentation.quiz_id and
                        b['semantic_revision'] == presentation.semantic_revision):
                    # Producer admission also pins meaning/text to the semantic ID.
                    def meaning(value):
                        return {'threshold': value['passing_score_percent'], 'questions': sorted(
                            [(q['question_id'], sorted(o['option_id'] for o in q['options']), q['answer'])
                             for q in value['questions']])}
                    if meaning(old['quiz']) != meaning(snapshot['quiz']):
                        raise QuizConflict('An immutable Quiz semantic revision was reused.')
                if (b == snapshot['binding'] and old['source_digest'] == snapshot['source_digest'] and
                        old['module'] == snapshot['module']):
                    raise QuizConflict('An immutable Quiz presentation binding was reused.')
            # Public release handles must not disclose an answer-derived hash.
            identity = uuid.uuid4().hex
            connection.execute('INSERT OR IGNORE INTO quiz_releases VALUES (?,?)', (identity, raw))
        return identity

    def submit_quiz(self, module, request):
        """Grade a fixed request once. A duplicate ID with different bytes conflicts."""
        from .quizsession import canonical, QuizConflict, score
        fields = {'schema_version', 'kind', 'attempt_id', 'release_id', 'binding', 'answers'}
        if (not isinstance(request, dict) or set(request) != fields or type(request['schema_version']) is not int or
                request['schema_version'] != 2 or request['kind'] != 'doris-quiz-submission' or
                not isinstance(request['attempt_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', request['attempt_id']) or
                not isinstance(request['release_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', request['release_id'])):
            raise QuizConflict('Invalid Quiz submission binding.')
        raw = canonical(request)
        # Keep the canonical bytes, grading and receipt tied to one copy even
        # when a caller reuses/mutates its request dictionary in another thread.
        request = json.loads(raw)
        module = dict(module)
        with self.connection() as connection:
            row = connection.execute('SELECT snapshot FROM quiz_releases WHERE id=?', (request['release_id'],)).fetchone()
            if not row:
                raise QuizConflict('Quiz release is unavailable; reload before submitting.')
            snapshot = json.loads(row[0])
            bound = snapshot['module']
            if ((bound['course_slug'], bound['id']) != (module['course_slug'], module['id']) or
                    request['binding'] != snapshot['binding']):
                raise QuizConflict('Quiz submission does not match its pinned release.')
            old = connection.execute('SELECT request,record FROM quiz_attempts WHERE id=?', (request['attempt_id'],)).fetchone()
            if old:
                if old[0] != raw:
                    raise QuizConflict('Quiz attempt was already submitted with different answers or presentation.')
                return json.loads(old[1])
            if connection.execute('SELECT 1 FROM portable_attempts WHERE id=?', (request['attempt_id'],)).fetchone():
                raise QuizConflict('A portable Quiz attempt cannot be used as a local submission receipt.')
            correct, total, threshold, passed = score(snapshot, request['answers'])
            attempt = {**snapshot['binding'], 'attempt_id': request['attempt_id'], 'course_slug': bound['course_slug'],
                       'source_digest': snapshot['source_digest'],
                       'module_id': bound['id'], 'content_version': bound['content_version'], 'source_ref': bound['source_ref'],
                       'answers': request['answers'], 'correct': correct, 'total': total, 'threshold': threshold,
                       'passed': passed, 'submitted_at': now(), 'trust': 'self-study'}
            validate_attempt(attempt)
            incoming = empty(bound)
            incoming.update(best_correct=correct, best_total=total, quiz_passed=passed)
            current = self._read(connection).get((module['course_slug'], module['id']), empty(module))
            self._write(connection, merge(current, incoming, module))
            connection.execute('INSERT INTO quiz_attempts VALUES (?,?,?)', (request['attempt_id'], raw, canonical(attempt)))
            return attempt

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
            self._legacy(connection, incoming)
        return result

    def export(self, version=2):
        if type(version) is not int or version not in {1, 2}:
            raise ProgressError('Unsupported progress schema_version; expected 1 or 2.')
        with self.connection() as connection:
            records = self._read(connection)
            document = {"schema_version": version, "kind": "doris-workshop-progress", "exported_at": now(),
                        "records": [records[k] for k in sorted(records)]}
            if version == 2:
                attempts = self._attempts(connection)
                document.update(attempts=[attempts[k] for k in sorted(attempts)], legacy_records=[json.loads(r) for r, in
                                connection.execute('SELECT record FROM legacy_records ORDER BY id')])
            else:
                warnings.warn('Progress v1 projection omits attempt, language and version history; keep the v2 file and database backup.',
                              UserWarning, stacklevel=2)
        data = json.dumps(document, indent=2).encode()
        validate_document(data)
        return data

    def preview(self, document, modules):
        document = validate_document(json.dumps(document).encode())
        current = self.records()
        result = []
        for record in document["records"]:
            key = record["course_slug"], record["module_id"]
            module = modules.get(key)
            result.append({"record": record, "matched": module is not None,
                           "attempt_count": sum((a['course_slug'], a['module_id']) == key for a in document.get('attempts', [])),
                           "legacy_count": (1 if document['schema_version'] == 1 else sum(
                               (a['course_slug'], a['module_id']) == key for a in document['legacy_records'])),
                           "content_updated": bool(module and record["content_version"] != module["content_version"]),
                           "result": merge(current.get(key, empty(module)), record, module) if module else None})
        return result

    def import_document(self, document, modules):
        # Validate again even for non-HTTP callers; nothing writes before validation.
        document = validate_document(json.dumps(document).encode())
        count = 0
        with self.connection() as connection:
            self._backup(connection)
            current = self._read(connection)
            known = self._attempts(connection)
            for attempt in document.get('attempts', []):
                key = attempt['course_slug'], attempt['module_id']
                if key not in modules:
                    continue
                identity = attempt['attempt_id']
                if identity in known and known[identity] != attempt:
                    raise ProgressError('Conflicting Quiz attempt identity.')
                connection.execute('INSERT OR IGNORE INTO portable_attempts VALUES (?,?)', (identity, json.dumps(attempt)))
            for record in document.get('legacy_records', document['records'] if document['schema_version'] == 1 else []):
                if (record['course_slug'], record['module_id']) in modules:
                    self._legacy(connection, record)
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


def save_submission_from_environment(path, presentation, attempt_id, answers):
    """Resolve trusted installed bytes, then submit stable IDs to the pinned grader.

    The optional v2 plan fields are an installation-adapter interface, not an
    implicit upgrade of the runtime v1 schema. See quiz-progress-v2.md.
    """
    db = os.environ.get('DORIS_WORKSHOP_PROGRESS_DB')
    if not db:
        return None
    from .quizbank import load_quiz
    from .quizsession import Presentation, QuizConflict
    plan = json.loads(Path(os.environ['DORIS_WORKSHOP_PROGRESS_PLAN']).read_text())
    source = Path(os.environ['DORIS_WORKSHOP_PROGRESS_SOURCE']).resolve()
    path = Path(path).resolve()
    def selected_path(relative):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise QuizConflict('Invalid installed Quiz path.')
        selected = (source / relative).resolve()
        if not selected.is_relative_to(source):
            raise QuizConflict('Invalid installed Quiz path.')
        return selected
    for module in registry(plan).values():
        canonical_path = selected_path(str(Path(module['course_dir']) / module['quiz']))
        installed = None
        if presentation.semantic_revision is None and canonical_path == path:
            bank = load_quiz(path)
            installed = Presentation.legacy(bank, hashlib.sha256(path.read_bytes()).hexdigest(), presentation.quiz_id)
        elif presentation.semantic_revision is not None:
            for entry in module.get('quiz_presentations', []):
                if selected_path(entry['path']) == path and entry['locale'] == presentation.locale:
                    raw_digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if raw_digest != entry['source_digest']:
                        raise QuizConflict('Installed Quiz bytes changed; reload before submitting.')
                    installed = Presentation.admitted(load_quiz(path, localized=True), module['quiz_binding'],
                        locale=entry['locale'], policy_revision=module['policy_revision'], source_digest=raw_digest)
        if installed is not None:
            installed = installed.in_order_of(presentation)
            if installed.snapshot() != presentation.snapshot():
                raise QuizConflict('Installed Quiz bytes changed; reload before submitting.')
            store = Store(db)
            release_id = store.register_quiz(module, installed)
            attempt = store.submit_quiz(module, {'schema_version': 2, 'kind': 'doris-quiz-submission',
                'attempt_id': attempt_id, 'release_id': release_id, 'binding': presentation.binding(), 'answers': answers})
            return {**store.records()[(module['course_slug'], module['id'])], 'attempt': attempt}
    for course in plan['courses']:
        for part in course.get('optional', []):
            if part.get('quiz') and (source / course['course_dir'] / part['quiz']).resolve() == path:
                return {'optional': True}
    raise ProgressError('This quiz is not selected in the current workshop; progress was not saved.')
