"""Background jobs for the portal: anything that takes longer than a page load.

A job runs a function in a worker thread, captures what it prints, and keeps its status, log and
result in SQLite so the portal can show progress and history. A restart marks jobs that were
running as `interrupted`, never as done.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, subject TEXT,
  status TEXT NOT NULL, log TEXT NOT NULL DEFAULT '', result TEXT,
  created TEXT NOT NULL, updated TEXT NOT NULL
)
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="twinlab-job")
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
                with contextlib.redirect_stdout(log):
                    result = fn(log)
                self._set(job_id, status="done", result=json.dumps(result, default=str))
            except Exception as exc:  # noqa: BLE001 - a failed job records why, it never crashes the portal
                log.write(f"\nFAILED: {exc}\n")
                log.write(traceback.format_exc()[-4000:])
                self._set(job_id, status="failed", result=json.dumps({"error": str(exc)}))

        self.pool.submit(run)
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
