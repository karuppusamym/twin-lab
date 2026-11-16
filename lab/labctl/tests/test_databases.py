"""Demo databases on SQL Server, Oracle or Postgres: configuration, Compose, bindings, tables,
OpenShift, the Devin task. Live SQL Server checks run when TWINLAB_TEST_MSSQL_URL is set."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
import yaml
from sqlalchemy.schema import CreateTable

from labctl import bindings, dbservers, dbsetup, lab, ocp
from labctl.devin import package as pkg

ROOT = Path(__file__).resolve().parents[3]

LOCAL = """
databases:
  qtt-sql:
    engine: SQL Server
    host: sqldemo01.office.example
    database: TWINLAB_QTT
    owner_user: tl_owner
    owner_password_env: TL_OWNER_PW
    bot_user: tl_bot
    bot_password: "b0t;pw"
  loans-ora:
    engine: oracle
    host: oradb01.office.example
    port: 1521
    service: LOANSDEMO
    owner_user: TL_OWNER
    owner_password: x
"""

SCHEMA = """-- Dialect: mssql
CREATE TABLE customers (customer_id INT IDENTITY(1,1) PRIMARY KEY, code NVARCHAR(10) NOT NULL UNIQUE, note NVARCHAR(MAX));
CREATE TABLE orders (order_id INT IDENTITY(1,1) PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(customer_id),
  total DECIMAL(10,2), placed DATETIME2, paid BIT);
GO
CREATE VIEW v_orders AS SELECT o.order_id, c.code AS customer_code FROM orders o JOIN customers c ON c.customer_id = o.customer_id;
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for folder in ("lab", "automations", "specs"):
        shutil.copytree(
            ROOT / folder,
            tmp_path / folder,
            ignore=shutil.ignore_patterns("node_modules", "__pycache__"),
        )
    app = tmp_path / "apps" / "demo"
    (app / "db").mkdir(parents=True)
    (app / "db" / "schema.sql").write_text(SCHEMA, encoding="utf-8")
    (app / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (app / "app.yaml").write_text(yaml.safe_dump({
        "id": "demo", "name": "Demo", "copies": {"system": "Demo"}, "stack": {"db": {"engine": "mssql"}},
        "run": {"web": {"port": 8000}, "api": {"port": 8000, "health": "/api/health"}},
        "lab": {"port": 8750, "db_port": 15750, "database": "qtt-sql"},
    }), encoding="utf-8")  # fmt: skip
    (tmp_path / "twinlab.local.yaml").write_text(LOCAL, encoding="utf-8")
    monkeypatch.setenv("TL_OWNER_PW", "own:er/pw")
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / ".lab-state"))
    return tmp_path


def test_servers_from_the_local_file_and_the_environment(repo, monkeypatch):
    servers = dbservers.load(repo)
    sql, ora = servers["qtt-sql"], servers["loans-ora"]
    assert sql.engine == "mssql" and sql.port == 1433 and sql.owner_password == "own:er/pw"
    assert (
        sql.url()
        == "mssql+pymssql://tl_owner:own%3Aer%2Fpw@sqldemo01.office.example:1433/TWINLAB_QTT"
    )
    assert sql.url(bot=True, host="agent", port=15432).startswith(
        "mssql+pymssql://tl_bot:b0t%3Bpw@agent:15432/"
    )
    assert sql.connection_string(host="h", port=1).startswith(
        "Server=h,1;Database=TWINLAB_QTT;User Id=tl_bot"
    )
    assert (
        ora.url()
        == "oracle+oracledb://TL_OWNER:x@oradb01.office.example:1521/?service_name=LOANSDEMO"
    )
    assert ora.connection_string(bot=False).startswith(
        "Data Source=oradb01.office.example:1521/LOANSDEMO"
    )
    shown = str(sql.described())
    assert "own:er" not in shown and "b0t" not in shown and "'owner_password': 'set'" in shown
    monkeypatch.delenv("TL_OWNER_PW")
    assert "the owner's password is not set" in "; ".join(
        dbservers.load(repo)["qtt-sql"].problems()
    )
    monkeypatch.setenv(
        "TWINLAB_DATABASES",
        "pg: {engine: postgres, host: pg1, database: d, owner_user: u, owner_password: p}",
    )
    assert dbservers.load(repo)["pg"].url() == "postgresql://u:p@pg1:5432/d"


def test_compose_for_a_dba_server_has_no_database_container(repo):
    doc = lab.compose(repo, "demo")
    assert "db" not in doc["services"]
    env = doc["services"]["agent"]["environment"]
    assert env["DB_UPSTREAM"] == "sqldemo01.office.example:1433"
    assert env["ADMIN_DATABASE_URL"].startswith("mssql+pymssql://tl_owner:")
    app_env = doc["services"]["app"]["environment"]
    assert (
        app_env["DATABASE_URL"].startswith("mssql+pymssql://tl_bot:")
        and "@agent:15432/" in app_env["DATABASE_URL"]
    )
    assert (
        app_env["DB_ENGINE"] == "mssql"
        and app_env["DB_HOST"] == "agent"
        and app_env["DB_USER"] == "tl_bot"
    )


def test_compose_for_the_sql_server_container(repo):
    path = repo / "apps/demo/app.yaml"
    data = yaml.safe_load(path.read_text())
    data["lab"]["database"] = "sqlserver"
    path.write_text(yaml.safe_dump(data))
    doc = lab.compose(repo, "demo")
    db = doc["services"]["db"]
    assert (
        db["image"].startswith("mcr.microsoft.com/mssql/server")
        and db["environment"]["ACCEPT_EULA"] == "Y"
    )
    assert db["environment"]["MSSQL_SA_PASSWORD"].startswith("Tl9!")
    assert doc["services"]["agent"]["environment"]["DB_UPSTREAM"] == "db:1433"
    assert "@agent:15432/demo" in doc["services"]["app"]["environment"]["DATABASE_URL"]
    batches = lab._mssql_setup_sql("demo", "pw'1")
    assert len(batches) == 2 and "CREATE DATABASE [demo]" in batches[0] and "pw''1" in batches[0]
    assert batches[1].startswith("USE [demo];") and "db_datawriter" in batches[1]


def test_bots_get_the_engine_and_a_connection_string(repo):
    from labctl.models import Automation

    auto = Automation.model_validate({"id": "b", "name": "b", "type": "python", "surface": "backend",
                                      "source": {"path": "."}, "touches": [{"app": "demo", "via": "db",
                                      "tables": [{"table": "orders", "access": "read"}]}]})  # fmt: skip
    b = bindings.for_automation(repo, auto, host="127.0.0.1").apps["demo"]
    assert b.db_engine == "mssql" and b.db.startswith("mssql+pymssql://tl_bot:")
    assert "@127.0.0.1:15750/" in b.db and b.db_connection_string.startswith(
        "Server=127.0.0.1,15750;"
    )


@pytest.mark.parametrize("dialect", ["mssql", "oracle", "postgresql"])
def test_tables_compile_for_each_engine(dialect):
    from importlib import import_module

    meta = dbsetup.metadata(SCHEMA, dialect)
    assert {t.name for t in meta.sorted_tables} == {"customers", "orders", "v_orders"}
    d = import_module(f"sqlalchemy.dialects.{dialect}").dialect()
    orders = str(CreateTable(meta.tables["orders"]).compile(dialect=d))
    assert "REFERENCES customers" in orders and "IDENTITY" in orders.upper()
    customers = str(CreateTable(meta.tables["customers"]).compile(dialect=d))
    assert "UNIQUE" in customers.upper()


def test_openshift_for_a_dba_server(repo):
    docs = ocp.render(
        repo, "demo", registry="ghcr.io/o/r", tag="t1", namespace="tl", expose_db=True
    )
    kinds = [(d["kind"], d["metadata"]["name"]) for d in docs]
    assert not any(n == "twinlab-demo-db" for _, n in kinds)  # no database deployed
    job = next(d for d in docs if d["kind"] == "Job")
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert (
        container["image"] == "ghcr.io/o/r/lab-agent:t1"
        and "dbsetup.create_tables" in container["command"][2]
    )
    agent = next(
        d
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "twinlab-demo-agent"
    )
    env = {e["name"]: e for e in agent["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["DB_UPSTREAM"]["value"] == "sqldemo01.office.example:1433"
    assert env["ADMIN_DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "twinlab-demo-dbconn"
    text = ocp.to_yaml(docs)
    assert "own:er" not in text and "b0t" not in text  # no password in any manifest
    nodeport = next(d for d in docs if d["metadata"]["name"] == "twinlab-demo-db-external")
    assert nodeport["spec"]["type"] == "NodePort"
    secret = ocp.conn_secret_manifest("demo", "tl", dbservers.server(repo, "qtt-sql"))
    assert "@twinlab-demo-agent:15432/" in secret["stringData"]["app-url"]


def test_openshift_refuses_the_local_sql_server_container(repo):
    path = repo / "apps/demo/app.yaml"
    data = yaml.safe_load(path.read_text())
    data["lab"]["database"] = "sqlserver"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ocp.OcpError, match="local lab only"):
        ocp.render(repo, "demo", registry="r", tag="t", namespace="tl")


def test_devin_task_names_the_engine_and_the_enhancement():
    assert "mcr.microsoft.com/mssql/server" in pkg._db_example(
        "mssql", "qtt", "DATABASE_URL", "8000"
    )
    assert "service_name=FREEPDB1" in pkg._db_example("oracle", "qtt", "DATABASE_URL", "8000")
    text = pkg.prompt(ROOT, "showcase", "acme/twin-lab")
    assert "this is an enhancement" in text and "never replace a brief yourself" in text
    assert "A view in the schema is a **table**" in text


MSSQL = os.environ.get(
    "TWINLAB_TEST_MSSQL_URL"
)  # e.g. mssql+pymssql://sa:pw@127.0.0.1:14330/master


@pytest.mark.skipif(not MSSQL, reason="set TWINLAB_TEST_MSSQL_URL to a SQL Server to test against")
def test_live_sql_server_tables_load_reset(tmp_path):
    import asyncio
    import sys

    sys.path.insert(0, str(ROOT / "lab" / "lab-agent"))
    from lab_agent.data import DataProblems
    from lab_agent.sqldb import SqlDatabase

    master = sa.create_engine(MSSQL, isolation_level="AUTOCOMMIT")
    with master.connect() as c:
        c.execute(sa.text("IF DB_ID('tltest_dbs') IS NOT NULL DROP DATABASE tltest_dbs"))
        c.execute(sa.text("CREATE DATABASE tltest_dbs"))
    url = MSSQL.rsplit("/", 1)[0] + "/tltest_dbs"
    schema = tmp_path / "schema.sql"
    schema.write_text(SCHEMA, encoding="utf-8")
    assert sorted(dbsetup.create_tables(url, schema, "mssql")) == [
        "customers",
        "orders",
        "v_orders",
    ]
    assert dbsetup.create_tables(url, schema, "mssql") == []
    db = SqlDatabase(url, None, {"customers", "orders", "v_orders"})
    rows = {"customers": [{"customer_id": 7, "code": "C7"}],
            "orders": [{"order_id": 70, "customer_id": 7, "total": "1.50", "paid": True}],
            "v_orders": [{"order_id": 70, "customer_code": "C7"}]}  # fmt: skip
    res = asyncio.run(db.replace(rows))
    assert res.counts == {"customers": 1, "orders": 1, "v_orders": 1}
    with pytest.raises(DataProblems, match="problem"):
        asyncio.run(db.replace({"orders": [{"order_id": 71, "customer_id": 99}]}))
    _, snap = asyncio.run(db.snapshot())
    assert snap["orders"][0]["total"] == "1.50" and snap["orders"][0]["paid"] is True
    with sa.create_engine(url).begin() as c:  # the identity counter moved past the loaded rows
        c.execute(sa.text("INSERT INTO customers (code) VALUES ('new')"))
        assert c.execute(sa.text("SELECT MAX(customer_id) FROM customers")).scalar() == 8
