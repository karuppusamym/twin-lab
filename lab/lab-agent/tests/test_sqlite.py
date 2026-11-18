"""The lab agent on a SQLite file (`ADMIN_DATABASE_URL=sqlite:///...`): the file and tables made
from the schema, load, reset, baselines, sessions while the "app" (a second connection, as a demo
app would hold) writes to the same file, and database conditions reported as not available.
Real SQLite, no Docker."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")

import httpx  # noqa: E402

from lab_agent.app import LabAgent  # noqa: E402
from lab_agent.config import Settings  # noqa: E402
from lab_agent.data import DataProblems  # noqa: E402
from lab_agent.sqldb import SqlDatabase, engine_of  # noqa: E402
from lab_agent.sqlite_init import prepare  # noqa: E402

SCHEMA = """-- Dialect: postgres
CREATE TABLE categories (id SERIAL PRIMARY KEY, name VARCHAR(40) NOT NULL UNIQUE);
CREATE TABLE cases (case_id SERIAL PRIMARY KEY, case_no VARCHAR(12) NOT NULL UNIQUE,
  category_id INT NOT NULL REFERENCES categories(id), amount NUMERIC(10,2), opened TIMESTAMP,
  closed BOOLEAN NOT NULL DEFAULT false, notes TEXT);
CREATE TABLE notes (id SERIAL PRIMARY KEY, case_id INT NOT NULL REFERENCES cases(case_id), text TEXT);
CREATE VIEW open_cases AS SELECT c.case_no, k.name AS category FROM cases c JOIN categories k ON k.id = c.category_id;
"""

ROWS = {
    "categories": [{"id": 1, "name": "Billing"}, {"id": 2, "name": "Claims"}],
    "cases": [
        {"case_id": 10, "case_no": "C-10", "category_id": 1, "amount": "12.50",
         "opened": "2026-11-01T09:30:00", "closed": False, "notes": "first"},
        {"case_id": 11, "case_no": "C-11", "category_id": 2, "amount": 3, "closed": True},
    ],
    "notes": [{"id": 100, "case_id": 10, "text": "hello"}],
    "open_cases": [{"case_no": "C-10", "category": "Billing"}],
}  # fmt: skip


@pytest.fixture
def db_file(tmp_path):
    schema = tmp_path / "schema.sql"
    schema.write_text(SCHEMA, encoding="utf-8")
    path = tmp_path / "data" / "demo.db"
    url = f"sqlite:///{path.as_posix()}"
    made = prepare(url, schema)
    assert sorted(made) == ["cases", "categories", "notes", "open_cases"]
    return url, path, schema


def test_engine_and_file_preparation(db_file):
    url, path, schema = db_file
    assert engine_of(url) == "sqlite" and engine_of("sqlite://") == "sqlite"
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)  # kept in the file
    finally:
        conn.close()
    assert prepare(url, schema) == []  # again: nothing to do


def test_load_snapshot_and_reset_on_sqlite(db_file):
    url, path, _ = db_file
    db = SqlDatabase(url, None, {"categories", "cases", "notes", "open_cases"})
    res = asyncio.run(db.replace(ROWS))
    assert res.counts == {"categories": 2, "cases": 2, "notes": 1, "open_cases": 1}
    schema, snap = asyncio.run(db.snapshot())
    first = snap["cases"][0]
    assert first["amount"] == "12.50" and first["closed"] is False  # numeric as text, booleans
    assert first["opened"].startswith("2026-11-01T09:30:00")
    assert snap["cases"][1]["amount"] == "3.00" and snap["cases"][1]["closed"] is True
    assert schema.tables["cases"].column("case_id").identity == "BY DEFAULT"  # the rowid
    assert schema.tables["open_cases"].primary_key == []
    # validation before anything changes: a missing parent, a missing required value
    with pytest.raises(DataProblems) as bad:
        asyncio.run(db.replace({"notes": [{"id": 1, "case_id": 999}]}))
    assert "= 999, not found in the database" in bad.value.problems[0].message
    with pytest.raises(DataProblems) as bad:
        asyncio.run(db.replace({"cases": [{"case_no": "x", "category_id": 1}]}))
    assert (bad.value.problems[0].column, bad.value.problems[0].message) == (
        "closed", "required (NOT NULL) and missing")  # the key (case_id) is not: SQLite gives one
    assert asyncio.run(db.snapshot())[1]["notes"][0]["id"] == 100  # nothing changed
    # an app adds a row; replacing the parent table clears the children that point at it
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO notes (case_id, text) VALUES (10, 'added')")
    conn.commit()
    conn.close()
    res = asyncio.run(db.replace({"categories": ROWS["categories"], "cases": ROWS["cases"]}))
    assert res.cleared == ["notes"] and res.counts["notes"] == 0
    # ids continue after the loaded rows, the same way after every reset
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO cases (case_no, category_id, closed) VALUES ('C-new', 1, 0)")
    assert conn.execute("SELECT MAX(case_id) FROM cases").fetchone() == (12,)
    conn.rollback()
    conn.close()


def test_rows_without_a_key_value_get_one(db_file):
    url, _, _ = db_file
    db = SqlDatabase(url, None, None)
    asyncio.run(db.replace({"categories": [{"name": "A"}, {"name": "B"}]}))
    _, snap = asyncio.run(db.snapshot(["categories"]))
    assert [r["id"] for r in snap["categories"]] == [1, 2]


def test_autoincrement_counters_are_put_back(tmp_path):
    path = tmp_path / "a.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.close()
    db = SqlDatabase(f"sqlite:///{path.as_posix()}", None, None)
    asyncio.run(db.replace({"t": [{"id": 1, "v": "a"}]}))
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO t (v) VALUES ('next')")
    assert conn.execute("SELECT MAX(id) FROM t").fetchone() == (2,)  # not 4
    conn.close()


def test_circular_foreign_keys_load(tmp_path):
    path = tmp_path / "c.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE a (id INTEGER PRIMARY KEY, b_id INTEGER REFERENCES b(id));
        CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES a(id));""")
    conn.close()
    db = SqlDatabase(f"sqlite:///{path.as_posix()}", None, None)
    res = asyncio.run(db.replace({"a": [{"id": 1, "b_id": 1}], "b": [{"id": 1, "a_id": 1}]}))
    assert res.counts == {"a": 1, "b": 1}


def test_a_snapshot_waits_for_nothing_while_the_app_writes(db_file):
    """WAL: the app holds a write transaction; the agent still reads (the committed rows)."""
    url, path, _ = db_file
    db = SqlDatabase(url, None, None)
    asyncio.run(db.replace(ROWS))
    app = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
    app.execute("BEGIN IMMEDIATE")
    app.execute("UPDATE cases SET notes = 'uncommitted' WHERE case_id = 10")
    try:
        _, snap = asyncio.run(asyncio.wait_for(db.snapshot(["cases"]), 5))
        assert snap["cases"][0]["notes"] == "first"
        # and a load waits for the app's commit (busy timeout) instead of failing at once
        release = threading.Timer(0.5, lambda: app.execute("COMMIT"))
        release.start()
        res = asyncio.run(db.replace({"categories": ROWS["categories"], "cases": ROWS["cases"]}))
        assert res.counts["cases"] == 2
        release.join()
    finally:
        app.close()


# --- the whole agent over HTTP ---------------------------------------------------------------


def make_agent(tmp_path, db_file) -> LabAgent:
    url, _, schema = db_file
    settings = Settings(app_id="demo", app_upstream="http://upstream", state_dir=tmp_path / "state",
                        admin_database_url=url, schema_path=schema)  # fmt: skip
    return LabAgent(settings, start_db_proxy=False)


def run(agent: LabAgent, scenario):
    async def main():
        await agent.startup()
        try:
            transport = httpx.ASGITransport(app=agent)
            async with httpx.AsyncClient(transport=transport, base_url="http://lab") as client:
                return await scenario(client)
        finally:
            await agent.shutdown()

    return asyncio.run(main())


def test_load_session_and_reset_through_the_admin_api(tmp_path, db_file):
    _, path, _ = db_file
    agent = make_agent(tmp_path, db_file)
    assert agent.db_proxy is None and agent.settings.db_upstream is None

    async def scenario(c: httpx.AsyncClient):
        r = await c.post("/_lab/data/load", json={"rows": ROWS, "label": "start"})
        assert r.status_code == 200, r.text
        info = (await c.get("/_lab/info")).json()
        assert info["db_engine"] == "sqlite" and info["db_proxy"] is None
        assert {t["name"] for t in info["tables"]} == {"categories", "cases", "notes", "open_cases"}
        sid = (await c.post("/_lab/session/start", json={"automation": "bot"})).json()["session_id"]

        def app_writes():  # what the demo app does through its own connection
            conn = sqlite3.connect(path, timeout=5)
            conn.execute("UPDATE cases SET closed = 1, notes = 'done' WHERE case_id = 10")
            conn.execute("INSERT INTO notes (case_id, text) VALUES (10, 'closing')")
            conn.execute("DELETE FROM notes WHERE id = 100")
            conn.commit()
            conn.close()

        await asyncio.to_thread(app_writes)
        doc = (await c.post("/_lab/session/stop")).json()
        detail = (await c.get(f"/_lab/sessions/{sid}/detail")).json()
        changes = {(ch["table"], ch["op"], ch["key"]) for ch in detail["changes"]}
        assert changes == {("cases", "update", "10"), ("notes", "insert", "101"),
                           ("notes", "delete", "100")}  # fmt: skip
        update = next(ch for ch in detail["changes"] if ch["op"] == "update")
        assert set(update["columns"]) == {"closed", "notes"}
        r = await c.post("/_lab/reset")
        assert r.status_code == 200, r.text
        rows = (await c.get("/_lab/data", params={"tables": "cases,notes"})).json()
        assert rows["cases"][0]["notes"] == "first" and [n["id"] for n in rows["notes"]] == [100]
        health = (await c.get("/_lab/health")).json()
        assert health["db"] == "ok"
        return doc

    doc = run(agent, scenario)
    assert doc["session_id"]


def test_database_conditions_are_reported_not_available(tmp_path, db_file):
    agent = make_agent(tmp_path, db_file)

    async def scenario(c: httpx.AsyncClient):
        r = await c.put("/_lab/condition", json={"id": "slow-database", "db": {"latency_ms": 250}})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "LAB_CONDITION_UNAVAILABLE"
        assert "not available on sqlite" in detail["message"]
        assert (await c.get("/_lab/condition")).json()["id"] is None  # nothing was applied
        mixed = {"id": "peak-hour", "db": {"latency_ms": 80},
                 "http": [{"match": "GET /api/*", "latency_ms": {"p50": 10, "p95": 10}}]}  # fmt: skip
        r = await c.put("/_lab/condition", json=mixed)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "peak-hour" and body["http"]  # the web part applies
        assert body["unavailable"][0]["part"] == "db"
        assert "not available on sqlite" in body["unavailable"][0]["message"]
        assert (await c.get("/_lab/condition")).json()["unavailable"]  # and says so later too
        r = await c.put("/_lab/condition", json={"id": "errors", "faults": [
            {"match": "POST /api/*", "status": 503, "rate": 0.5}]})  # fmt: skip
        assert r.status_code == 200 and "unavailable" not in r.json()

    run(agent, scenario)


def test_other_engines_report_no_unavailable_part(tmp_path):
    settings = Settings(app_id="demo", app_upstream="http://upstream", state_dir=tmp_path,
                        admin_database_url="postgresql://u:p@db:5432/demo")  # fmt: skip
    agent = LabAgent(settings, start_db_proxy=False)

    async def scenario(c: httpx.AsyncClient):
        r = await c.put("/_lab/condition", json={"id": "slow-database", "db": {"latency_ms": 250}})
        assert r.status_code == 200 and "unavailable" not in r.json()

    run(agent, scenario)
