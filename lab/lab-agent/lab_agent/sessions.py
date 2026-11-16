"""Sessions: a before snapshot, the requests in between, an after snapshot, and the diff.

State under STATE_DIR:

    active/session.json     the running session (survives a restart of the agent)
    active/before.json      its before snapshot
    active/requests.jsonl   requests recorded so far, one JSON object per line
    sessions/<id>.json      a finished session: a `labctl.models.Session` document
    sessions/<id>.detail.json  label, changed columns with before/after values, faults
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lab_agent.diff import diff
from lab_agent.store import read_json, remove, write_json
from labctl.models import Session

SESSION_ID = re.compile(r"^ses_\d{8}T\d{6}Z_[0-9a-f]{6}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_session_id(now: datetime) -> str:
    return f"ses_{now.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}"


@dataclass
class Active:
    session_id: str
    started: datetime
    automation: str | None
    label: str | None
    condition: str | None
    data_pack: str | None
    requests: list[dict[str, Any]] = field(default_factory=list)
    # table -> {"read": bool, "write": bool}, from statements seen on the database link. Kept in
    # memory only: a session resumed after an agent restart lists what it saw since.
    tables: dict[str, dict[str, bool]] = field(default_factory=dict)


class SessionStore:
    def __init__(self, state_dir: Path, app_id: str, version: str) -> None:
        self.dir = state_dir / "sessions"
        self.active_dir = state_dir / "active"
        self.app_id = app_id
        self.version = version
        self.active: Active | None = None
        self._log = None
        self.dir.mkdir(parents=True, exist_ok=True)
        self._resume()

    # --- active session ----------------------------------------------------------------------

    def _resume(self) -> None:
        meta = read_json(self.active_dir / "session.json")
        if not meta:
            return
        requests: list[dict[str, Any]] = []
        log_path = self.active_dir / "requests.jsonl"
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                try:
                    requests.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a line cut short by a crash
        self.active = Active(
            session_id=meta["session_id"],
            started=datetime.fromisoformat(meta["started"].replace("Z", "+00:00")),
            automation=meta.get("automation"),
            label=meta.get("label"),
            condition=meta.get("condition"),
            data_pack=meta.get("data_pack"),
            requests=requests,
        )
        self._log = log_path.open("a", encoding="utf-8")

    def start(
        self,
        before: dict[str, list[dict[str, Any]]],
        automation: str | None,
        label: str | None,
        condition: str | None,
        data_pack: str | None,
    ) -> Active:
        if self.active is not None:
            raise RuntimeError(f"session {self.active.session_id} is already running")
        now = utcnow()
        active = Active(new_session_id(now), now, automation, label, condition, data_pack)
        shutil.rmtree(self.active_dir, ignore_errors=True)
        write_json(self.active_dir / "before.json", before)
        write_json(
            self.active_dir / "session.json",
            {
                "session_id": active.session_id,
                "started": iso(now),
                "automation": automation,
                "label": label,
                "condition": condition,
                "data_pack": data_pack,
            },
        )
        self._log = (self.active_dir / "requests.jsonl").open("a", encoding="utf-8")
        self.active = active
        return active

    def note_tables(self, found: list[dict[str, Any]]) -> None:
        """Tables the database link saw statements on (from `analyse.sql.tables`)."""
        if self.active is None:
            return
        for t in found:
            entry = self.active.tables.setdefault(t["table"], {"read": False, "write": False})
            if t["access"] in ("read", "read_write"):
                entry["read"] = True
            if t["access"] in ("write", "read_write"):
                entry["write"] = True

    def record(self, request: dict[str, Any]) -> None:
        if self.active is None:
            return
        self.active.requests.append(request)
        if self._log is not None:
            self._log.write(json.dumps(request, separators=(",", ":")) + "\n")
            self._log.flush()

    def stop(
        self, after: dict[str, list[dict[str, Any]]], pks: dict[str, list[str]]
    ) -> dict[str, Any]:
        active = self.active
        if active is None:
            raise RuntimeError("no session is running")
        before = read_json(self.active_dir / "before.json", {})
        ended = utcnow()
        changes = diff(before, after, pks)
        reqs = active.requests
        errors = sum(1 for r in reqs if r["status"] >= 500 or r.get("fault"))
        waiting_ms = sum(r["app_ms"] + r["injected_ms"] for r in reqs)
        doc = Session(
            session_id=active.session_id,
            automation=active.automation,
            apps=[
                {
                    "app": self.app_id,
                    "version": self.version,
                    "data_pack": active.data_pack,
                    "condition": active.condition,
                }
            ],
            started=active.started,
            ended=ended,
            requests=[
                {
                    k: r[k]
                    for k in ("ts", "app", "method", "path", "status", "app_ms", "injected_ms")
                }
                for r in reqs
            ],
            changes=[
                {
                    "app": self.app_id,
                    "table": c["table"],
                    "op": c["op"],
                    "key": c["key"],
                    "columns": c.get("columns", []),
                }
                for c in changes
            ],
            summary={
                "requests": len(reqs),
                "wall_s": round((ended - active.started).total_seconds(), 3),
                "waiting_on_app_s": round(waiting_ms / 1000, 3),
                "errors": errors,
            },
        )
        data = doc.model_dump(mode="json")
        write_json(
            self.dir / f"{active.session_id}.detail.json",
            {
                "session_id": active.session_id,
                "label": active.label,
                "changes": [{"app": self.app_id, **c} for c in changes],
                "faults": [r for r in reqs if r.get("fault")],
                "tables_used": [{"table": t, **use} for t, use in sorted(active.tables.items())],
            },
        )
        write_json(self.dir / f"{active.session_id}.json", data)
        if self._log is not None:
            self._log.close()
            self._log = None
        shutil.rmtree(self.active_dir, ignore_errors=True)
        self.active = None
        return data

    def discard(self) -> None:
        """Drop a running session without storing it."""
        if self._log is not None:
            self._log.close()
            self._log = None
        shutil.rmtree(self.active_dir, ignore_errors=True)
        self.active = None

    # --- stored sessions ---------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        out = []
        for path in self.dir.glob("ses_*.json"):
            if path.name.endswith(".detail.json"):
                continue
            doc = read_json(path)
            if not doc:
                continue
            detail = read_json(self.dir / f"{doc['session_id']}.detail.json", {}) or {}
            out.append(
                {
                    "session_id": doc["session_id"],
                    "automation": doc.get("automation"),
                    "label": detail.get("label"),
                    "started": doc.get("started"),
                    "ended": doc.get("ended"),
                    "summary": doc.get("summary"),
                }
            )
        return sorted(out, key=lambda d: d["started"] or "", reverse=True)

    def get(self, session_id: str) -> dict[str, Any] | None:
        if not SESSION_ID.match(session_id):
            return None
        return read_json(self.dir / f"{session_id}.json")

    def detail(self, session_id: str) -> dict[str, Any] | None:
        if not SESSION_ID.match(session_id):
            return None
        return read_json(self.dir / f"{session_id}.detail.json")

    def delete(self, session_id: str) -> bool:
        if not SESSION_ID.match(session_id) or not (self.dir / f"{session_id}.json").exists():
            return False
        remove(self.dir / f"{session_id}.json")
        remove(self.dir / f"{session_id}.detail.json")
        return True
