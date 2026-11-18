"""Background jobs for the portal: anything that takes longer than a page load.

A job runs a function in a worker thread, captures what it prints, and keeps its status, log and
result in SQLite so the portal can show progress and history. A restart marks jobs that were
running as `interrupted`, never as done.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

INTERACTIVE = {"record"}  # job kinds that wait on a person

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, subject TEXT,
  status TEXT NOT NULL, log TEXT NOT NULL DEFAULT '', result TEXT,
  created TEXT NOT NULL, updated TEXT NOT NULL
)
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _ThreadRouter(io.TextIOBase):
    """sys.stdout replacement installed once: a job thread's prints go to that job's log, every
    other thread's to the real console. (Swapping sys.stdout per job mixes parallel jobs' logs.)"""

    def __init__(self, console):
        self.console = console
        self.local = threading.local()

    def write(self, text: str) -> int:
        target = getattr(self.local, "log", None)
        return (target or self.console).write(text)

    def flush(self) -> None:
        target = getattr(self.local, "log", None)
        (target or self.console).flush()


_router_lock = threading.Lock()


def _router() -> _ThreadRouter:
    with _router_lock:
        if not isinstance(sys.stdout, _ThreadRouter):
            sys.stdout = _ThreadRouter(sys.stdout)
        return sys.stdout


class Log(io.TextIOBase):
    """A file-like object a job writes to; flushed into the job's row as it goes."""

    def __init__(self, runner: JobRunner, job_id: str):
        self.runner, self.job_id = runner, job_id

    def write(self, text: str) -> int:
        if text:
            self.runner._append(self.job_id, text)
        return len(text)


class JobRunner:
    def __init__(self, path: Path, workers: int = 4):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        # Called with (kind, subject) after a job ends, however it ended: the portal uses it to
        # forget what it had read from a lab that the job may have changed.
        self.on_done: Callable[[str, str | None], None] | None = None
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="twinlab-job")
        # Jobs that wait on a person (a recording window) get their own worker, so they can
        # never fill the pool and leave Start, Brief and Conformance queued.
        self.interactive = ThreadPoolExecutor(max_workers=1, thread_name_prefix="twinlab-person")
        with self._db() as db:
            db.execute(SCHEMA)
            db.execute(
                "UPDATE jobs SET status='interrupted', updated=? WHERE status IN ('queued','running')",
                (now(),),
            )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _append(self, job_id: str, text: str) -> None:
        with self.lock, self._db() as db:
            db.execute(
                "UPDATE jobs SET log = log || ?, updated=? WHERE id=?", (text, now(), job_id)
            )

    def _set(self, job_id: str, **fields) -> None:
        fields["updated"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock, self._db() as db:
            db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def submit(
        self, kind: str, title: str, fn: Callable[[Log], object], subject: str | None = None
    ) -> str:
        job_id = uuid.uuid4().hex[:10]
        with self.lock, self._db() as db:
            db.execute(
                "INSERT INTO jobs (id, kind, title, subject, status, created, updated) VALUES (?,?,?,?,?,?,?)",
                (job_id, kind, title, subject, "queued", now(), now()),
            )

        def run() -> None:
            self._set(job_id, status="running")
            log = Log(self, job_id)
            try:
                router = _router()
                router.local.log = log
                try:
                    result = fn(log)
                finally:
                    router.local.log = None
                self._set(job_id, status="done", result=json.dumps(result, default=str))
            except Exception as exc:  # noqa: BLE001 - a failed job records why, it never crashes the portal
                log.write(f"\nFAILED: {exc}\n")
                log.write(traceback.format_exc()[-4000:])
                self._set(job_id, status="failed", result=json.dumps({"error": str(exc)}))
            if self.on_done is not None:
                try:
                    self.on_done(kind, subject)
                except Exception:  # noqa: BLE001 - a listener never fails a finished job
                    pass

        (self.interactive if kind in INTERACTIVE else self.pool).submit(run)
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(
        self, kind: str | None = None, subject: str | None = None, limit: int = 50
    ) -> list[dict]:
        query, args = "SELECT * FROM jobs WHERE 1=1", []
        if kind:
            query += " AND kind=?"
            args.append(kind)
        if subject:
            query += " AND subject=?"
            args.append(subject)
        query += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self._db() as db:
            return [self._row(r) for r in db.execute(query, args).fetchall()]

    def latest(self, kind: str, subject: str) -> dict | None:
        found = self.list(kind, subject, 1)
        return found[0] if found else None

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        out = dict(row)
        out["result"] = json.loads(out["result"]) if out.get("result") else None
        return out
