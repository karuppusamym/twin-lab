"""SQLite as a built-in database choice (`lab.database: sqlite`): configuration, Compose, the
start-up order, bindings, copies, readiness and the tables made from a schema file. No Docker
needed: what Docker would be told is checked, not run. The lab agent's side is tested in
lab/lab-agent/tests/test_sqlite.py."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from labctl import bindings, conformance, copies, dbservers, dbsetup, lab
from labctl.models import Automation

PG_SCHEMA = """-- Dialect: postgres
CREATE TABLE categories (id SERIAL PRIMARY KEY, name VARCHAR(40) NOT NULL UNIQUE);
CREATE TABLE cases (case_id BIGSERIAL PRIMARY KEY, case_no VARCHAR(12) NOT NULL UNIQUE,
  category_id INT REFERENCES categories(id), amount NUMERIC(10,2), opened TIMESTAMPTZ,
  closed BOOLEAN DEFAULT false, notes TEXT);
CREATE VIEW open_cases AS SELECT c.case_no, k.name AS category FROM cases c JOIN categories k ON k.id = c.category_id;
CREATE FUNCTION touch() RETURNS trigger AS $$ BEGIN RETURN NEW; END $$ LANGUAGE plpgsql;
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    app = tmp_path / "apps" / "demo"
    (app / "db").mkdir(parents=True)
    (app / "db" / "schema.sql").write_text(PG_SCHEMA, encoding="utf-8")
    (app / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (app / "app.yaml").write_text(yaml.safe_dump({
        "id": "demo", "name": "Demo", "copies": {"system": "Demo"}, "stack": {},
        "run": {"web": {"port": 8000}, "api": {"port": 8000, "health": "/api/health"},
                "env": {"database_url": "DATABASE_URL"}},
        "lab": {"port": 8750, "database": "sqlite", "credentials": {"username": "u", "password": "p"}},
    }), encoding="utf-8")  # fmt: skip
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / ".lab-state"))
    return tmp_path


def _set_lab(root: Path, **changes) -> None:
    path = root / "apps/demo/app.yaml"
    data = yaml.safe_load(path.read_text())
    data["lab"].update(changes)
    path.write_text(yaml.safe_dump(data))


def test_sqlite_is_a_built_in_choice(root):
    assert dbservers.choice("sqlite") == "sqlite"
    assert dbservers.engine_for(root, "sqlite") == "sqlite"  # no twinlab.local.yaml needed
    assert dbservers.LABEL["sqlite"] == "SQLite"
    assert "sqlite" not in dbservers.ENGINES  # a DBA's server is never a SQLite file


def test_compose_has_no_database_container_and_no_database_port(root):
    doc = lab.compose(root, "demo")
    assert set(doc["services"]) == {"agent", "app"}
    agent, app = doc["services"]["agent"], doc["services"]["app"]
    env = agent["environment"]
    assert env["ADMIN_DATABASE_URL"] == "sqlite:////var/lib/twinlab-db/demo.db"
    assert "DB_UPSTREAM" not in env and "DB_PROXY_PORT" not in env  # no proxy: nothing to proxy
    assert env["SCHEMA_PATH"] == "/lab/schema.sql" and env["LAB_TOKEN"]
    assert agent["ports"] == ["127.0.0.1:8750:9000"]
    assert "sqlite:/var/lib/twinlab-db" in agent["volumes"]
    assert app["volumes"] == ["sqlite:/var/lib/twinlab-db"]  # the same file in both containers
    assert app["environment"] == {
        "DATABASE_URL": "sqlite:////var/lib/twinlab-db/demo.db", "DB_ENGINE": "sqlite",
        "DB_PATH": "/var/lib/twinlab-db/demo.db",
        "DB_CONNECTION_STRING": "Data Source=/var/lib/twinlab-db/demo.db",
    }  # fmt: skip
    assert set(doc["volumes"]) == {"state", "sqlite"}  # not `db`: a Postgres volume stays apart
    assert "password" not in yaml.safe_dump(doc).lower()


def test_a_database_port_is_still_needed_by_the_other_engines(root):
    _set_lab(root, database="postgres")
    with pytest.raises(lab.LabError, match="db_port"):
        lab.compose(root, "demo")
    _set_lab(root, db_port=15750)
    assert "db" in lab.compose(root, "demo")["services"]


def test_up_prepares_the_file_and_tables_before_the_app_starts(root, monkeypatch):
    calls = []

    def docker(args, timeout=900):
        calls.append(args)
        out = "sqlite: created categories, cases, open_cases\n" if "run" in args else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(lab, "_docker", docker)
    monkeypatch.setattr(lab, "agent", lambda *a, **k: (_ for _ in ()).throw(lab.LabError("x")))
    lab.up(root, "demo", build=False)
    run, up = calls
    assert run[3:] == ["run", "--rm", "--no-deps", "-T", "agent", "python", "-m",
                       "lab_agent.sqlite_init"]  # fmt: skip
    assert up[3:5] == ["up", "-d"] and "--wait" in up
    assert not any("psql" in a or "db" == a for c in calls for a in c)  # no database container


def test_up_says_plainly_when_the_database_cannot_be_prepared(root, monkeypatch):
    monkeypatch.setattr(lab, "_docker", lambda args, timeout=900: subprocess.CompletedProcess(
        args, 1, "", "no such image"))  # fmt: skip
    with pytest.raises(lab.LabError, match="preparing the SQLite database of demo failed"):
        lab.up(root, "demo", build=False)


def test_bots_get_web_addresses_and_the_engine_but_no_database_address(root):
    auto = Automation.model_validate({"id": "b", "name": "b", "type": "python", "surface": "frontend",
                                      "source": {"path": "."}, "bindings": {"url": "{{demo.web}}"},
                                      "touches": [{"app": "demo", "via": "web", "screens": [{"name": "home", "path": "/"}]}]})  # fmt: skip
    b = bindings.for_automation(root, auto, host="127.0.0.1").apps["demo"]
    assert b.web == b.api == b.http == "http://127.0.0.1:8750"
    assert b.db is None and b.db_engine == "sqlite" and b.db_connection_string is None
    assert b.credentials == {"username": "u", "password": "p"}
    assert bindings.arguments(auto, bindings.for_automation(root, auto)) == {
        "url": "http://127.0.0.1:8750"
    }


def test_readiness_of_a_web_bot_needs_no_database_port(root, monkeypatch):
    auto = Automation.model_validate({"id": "b", "name": "b", "type": "python", "surface": "frontend",
                                      "source": {"path": "."},
                                      "touches": [{"app": "demo", "via": "web", "screens": [{"name": "home", "path": "/"}]}]})  # fmt: skip
    b = bindings.for_automation(root, auto)
    report = conformance.Report("b")
    conformance._check_tables(auto, b, report, {}, root)
    assert report.items == [] and report.errors == []


def test_readiness_of_a_database_bot_checks_tables_through_the_agent(root, monkeypatch):
    auto = Automation.model_validate({"id": "b", "name": "b", "type": "python", "surface": "backend",
                                      "source": {"path": "."}, "touches": [{"app": "demo", "via": "db",
                                      "tables": [{"table": "cases", "access": "read", "columns": ["case_no"]},
                                                 {"table": "nope", "access": "read"}]}]})  # fmt: skip
    b = bindings.for_automation(root, auto)
    seen = []

    class FakeAgent:
        def info(self):
            return {"tables": [{"name": "cases", "columns": [{"name": "case_no"}]}]}

    monkeypatch.setattr(lab, "agent", lambda r, lab_id, **k: seen.append(lab_id) or FakeAgent())
    report = conformance.Report("b")
    conformance._check_tables(auto, b, report, {}, root, {"demo": "demo.run-1"})
    got = {(i.what, i.status) for i in report.items}
    assert ("database", "failed") in got  # the bot cannot reach a SQLite file: said, and blocking
    assert ("cases", "ok") in got and ("nope", "failed") in got
    assert seen == ["demo.run-1"]
    assert "SQLite" in next(i.detail for i in report.items if i.what == "database")


def test_copies_run_on_sqlite(root, monkeypatch):
    started = []
    monkeypatch.setattr(lab, "up", lambda r, lab_id, build=True: started.append(lab_id))
    monkeypatch.setattr(lab, "agent", lambda *a, **k: (_ for _ in ()).throw(lab.LabError("down")))
    rec = copies.create(root, "demo", build=False)
    assert started == ["demo.run-1"] and rec["data"] == "app"
    doc = lab.compose(root, "demo.run-1")
    assert doc["name"] == "twinlab-demo_run-1" and "sqlite" in doc["volumes"]  # its own file
    assert doc["services"]["agent"]["ports"] == [f"127.0.0.1:{rec['port']}:9000"]


def test_tables_from_a_postgres_schema_on_a_sqlite_file(tmp_path, root):
    url = f"sqlite:///{(tmp_path / 'demo.db').as_posix()}"
    schema = root / "apps/demo/db/schema.sql"
    made = dbsetup.create_tables(url, schema, "sqlite")
    assert sorted(made) == ["cases", "categories", "open_cases"]  # the view is a table; no function
    assert dbsetup.create_tables(url, schema, "sqlite") == []  # safe on every start
    conn = sqlite3.connect(tmp_path / "demo.db")
    try:
        # BIGSERIAL became the rowid (INTEGER PRIMARY KEY), so it numbers itself
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(cases)")}
        assert cols["case_id"] == "INTEGER" and cols["amount"].startswith("NUMERIC")
        conn.execute("INSERT INTO categories (name) VALUES ('a')")
        conn.execute("INSERT INTO cases (case_no, category_id) VALUES ('C1', 1)")
        assert conn.execute("SELECT case_id FROM cases").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO categories (name) VALUES ('a')")  # UNIQUE kept
    finally:
        conn.close()
