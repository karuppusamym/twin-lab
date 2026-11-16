"""QTT SLA report: which submitted QC reviews have waited too long for a reviewer decision.

Backend only. Signs in to the QTT API, lists SUBMITTED reviews, reads each one's submit and return
history from review_audit by SQL, and reports how long each has waited against the SLA.

Bindings contract (same as Application Twin Studio's ATS_BINDINGS, read self-contained here):
    env ATS_BINDINGS = path to a JSON file, or the JSON itself:
    {"apps": {"qtt": {"web": "...", "api": "http://...", "db": "sqlite:///... | postgresql://...",
                      "credentials": {"username": "...", "password": "..."}}},
     "inputs": {"sla_hours": "48", "as_of": "2026-11-15T12:00:00Z"},   # optional
     "output_path": "result.json"}                                       # optional; else stdout

Run:  ATS_BINDINGS=bindings.json python report.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

APP = "qtt"
DEFAULT_SLA_HOURS = 48

AUDIT_SQL = """
SELECT r.review_no, a.event, a.actor, a.created_at
FROM review_audit a
JOIN qc_reviews r ON r.review_id = a.review_id
WHERE r.status = 'SUBMITTED' AND a.event IN ('SUBMITTED', 'RETURNED')
ORDER BY r.review_no, a.created_at, a.audit_id
"""


def load_bindings() -> dict:
    raw = os.environ.get("ATS_BINDINGS")
    if not raw:
        raise SystemExit("ATS_BINDINGS is not set (path to the bindings JSON, or the JSON itself)")
    if raw.lstrip().startswith("{"):
        return json.loads(raw)
    with open(raw, encoding="utf-8") as fh:
        return json.load(fh)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


# --- database ----------------------------------------------------------------------------------


def query(db_url: str, sql: str) -> list[dict]:
    """Run a read-only query against the bound database. SQLite natively; Postgres via psycopg."""
    parts = urlsplit(db_url)
    if parts.scheme in ("sqlite", "file", ""):
        path = unquote(parts.path if parts.scheme else db_url)
        if parts.scheme == "sqlite" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]  # sqlite:///C:/... on Windows
        if path.startswith("//"):
            path = "/" + path.lstrip("/")  # sqlite:////tmp/x gives //tmp/x; POSIX keeps the //
        # as_uri() gives file:///... on every OS (a bare "file://tmp/x" would name a host "tmp").
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()
    if parts.scheme.startswith("postgres"):
        try:
            import psycopg  # noqa: PLC0415  (optional; only needed for a Postgres binding)
        except ImportError as exc:
            raise SystemExit("a postgresql binding needs the psycopg package") from exc
        with psycopg.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute(sql)
            names = [c.name for c in cur.description]
            return [
                {
                    k: (v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in zip(names, row, strict=True)
                }
                for row in cur.fetchall()
            ]
    raise SystemExit(f"database binding scheme '{parts.scheme}' is not supported")


# --- API ---------------------------------------------------------------------------------------


def submitted_reviews(api: str, credentials: dict) -> list[dict]:
    with httpx.Client(base_url=api.rstrip("/"), timeout=15) as client:
        login = client.post(
            "/api/login",
            json={"username": credentials.get("username"), "password": credentials.get("password")},
        )
        if login.status_code != 200:
            raise SystemExit(f"sign-in refused ({login.status_code}): {login.text[:200]}")
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        listing = client.get("/api/reviews", params={"status": "SUBMITTED"}, headers=headers)
        listing.raise_for_status()
        return listing.json()["items"]


# --- report ------------------------------------------------------------------------------------


def build_report(reviews: list[dict], audit: list[dict], as_of: datetime, sla_hours: int) -> dict:
    history: dict[str, list[dict]] = {}
    for row in audit:
        history.setdefault(row["review_no"], []).append(row)
    rows = []
    for review in sorted(reviews, key=lambda r: r["review_no"]):
        events = history.get(review["review_no"], [])
        submits = [e for e in events if e["event"] == "SUBMITTED"]
        last = submits[-1]["created_at"] if submits else review.get("submitted_at")
        waited = None
        if last:
            waited = max(0.0, round((as_of - parse_ts(last)).total_seconds() / 3600, 1))
        rows.append(
            {
                "review_no": review["review_no"],
                "loan_no": review["loan_no"],
                "assignee": review["assignee"],
                "due_date": review["due_date"],
                "submitted_at": last,
                "hours_waiting": waited,
                "times_returned": sum(1 for e in events if e["event"] == "RETURNED"),
                "breached": waited is not None and waited > sla_hours,
                "overdue": review["due_date"] < as_of.date().isoformat(),
            }
        )
    return {
        "status": "DONE",
        "as_of": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sla_hours": sla_hours,
        "submitted": len(rows),
        "breached": sum(1 for r in rows if r["breached"]),
        "overdue": sum(1 for r in rows if r["overdue"]),
        "reviews": rows,
    }


def main() -> int:
    bindings = load_bindings()
    app = (bindings.get("apps") or {}).get(APP)
    if not app or not app.get("api") or not app.get("db"):
        raise SystemExit(f"bindings need apps.{APP}.api and apps.{APP}.db")
    inputs = bindings.get("inputs") or {}
    sla_hours = int(inputs.get("sla_hours") or DEFAULT_SLA_HOURS)
    as_of = parse_ts(inputs["as_of"]) if inputs.get("as_of") else datetime.now(UTC)

    reviews = submitted_reviews(app["api"], app.get("credentials") or {})
    audit = query(app["db"], AUDIT_SQL)
    report = build_report(reviews, audit, as_of, sla_hours)

    text = json.dumps(report, indent=2)
    if bindings.get("output_path"):
        Path(bindings["output_path"]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    print(
        f"{report['submitted']} submitted review(s), {report['breached']} over {sla_hours}h",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
