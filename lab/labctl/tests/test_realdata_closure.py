"""Schema inventory, read-only access, closure and the app-load trace diff, on synthetic data."""

import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from labctl.realdata import closure, demo, inventory
from labctl.realdata._db import RealDataError, WriteRefused, connection, safe_url, scrub

ASSOC = {
    "child": "addresses",
    "column": "customer_ref",
    "parent": "customers",
    "column_parent": "id",
}


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    path = demo.build(tmp_path_factory.mktemp("replica") / "replica.db")
    return "sqlite:///" + path.as_posix()


def spec(**closure_kw):
    return closure.SubsetSpec(
        seeds=[{"table": "customers", "values": [1]}],
        closure={"limits": {"accounts": 2}, **closure_kw},
    )


# -- inventory -------------------------------------------------------------------------------


def test_inventory_reads_keys_foreign_keys_uniques_and_audit_columns(url):
    tables = {t.name: t for t in inventory.pull(url)}
    assert set(tables) == {
        "branches", "customers", "accounts", "transactions", "addresses", "crm_contacts"
    }  # fmt: skip
    acc = tables["accounts"]
    assert acc.primary_key == ["id"]
    assert {(tuple(f.columns), f.parent) for f in acc.foreign_keys} == {
        (("customer_id",), "customers"),
        (("branch_id",), "branches"),
    }
    assert ["account_no"] in acc.unique_constraints
    assert next(c for c in acc.columns if c.name == "account_no").unique
    assert next(c for c in acc.columns if c.name == "customer_id").references == ("customers", "id")
    assert acc.audit_columns == ["LAST_MOD_TS"]
    assert tables["customers"].audit_columns == ["updated_at"]
    assert tables["addresses"].audit_columns == ["MODIFIED_DATE"]
    assert tables["addresses"].foreign_keys == []  # the relation the trace diff must find
    assert all(t.row_count is None for t in tables.values())


def test_row_counts_only_on_request(url):
    counts = {t.name: t.row_count for t in inventory.pull(url, counts=True)}
    assert counts["customers"] == 6 and counts["transactions"] == 45


def test_inventory_json_round_trip(url):
    tables = inventory.pull(url)
    again = inventory.from_json(inventory.to_json(tables))
    assert inventory.to_json(again) == inventory.to_json(tables)


def test_connection_is_read_only(url):
    with connection(url) as conn, pytest.raises(WriteRefused):
        conn.exec_driver_sql("DELETE FROM customers")
    with connection(url) as conn:  # below the guard, SQLite itself refuses (mode=ro)
        raw = conn.connection.dbapi_connection
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            raw.execute("DELETE FROM customers")


def test_password_never_shown():
    url = "postgresql+psycopg://lab:s3cr3t-synthetic@127.0.0.1:1/none"
    assert "s3cr3t" not in safe_url(url)
    assert "s3cr3t" not in scrub(f"failed for {url} with password s3cr3t-synthetic", url)
    with pytest.raises(RealDataError) as err:  # an unknown driver fails fast, with no network
        inventory.pull(url.replace("+psycopg", "+nosuchdriver"))
    assert "s3cr3t" not in str(err.value) and "***" in str(err.value)


# -- spec --------------------------------------------------------------------------------------


def test_spec_refuses_a_url_in_db_and_ambiguous_seeds():
    with pytest.raises(ValidationError):
        closure.SubsetSpec(db="sqlite:///x.db", seeds=[{"table": "t", "values": [1]}])
    with pytest.raises(ValidationError):
        closure.SubsetSpec(seeds=[{"table": "t", "values": [1], "where": "a = :a"}])
    with pytest.raises(ValidationError):
        closure.SubsetSpec(seeds=[{"table": "t", "where": "name = 'x'"}])


def test_example_spec_validates_and_runs(url):
    path = Path(closure.__file__).parent / "specs" / "subset.example.yaml"
    example = closure.SubsetSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    rows = closure.extract(url, example)
    assert {r["id"] for r in rows["customers"]} >= {1, 3}
    assert "addresses" in rows


# -- closure -----------------------------------------------------------------------------------


def test_closure_pulls_parents_bounds_children_and_reports_the_cut(url):
    result = closure.extract_with_report(url, spec())
    rows = result.rows
    by_id = {t: {r["id"] for r in rs} for t, rs in rows.items()}
    # customer 1 has 5 accounts; the limit keeps 2 (lowest ids) and says so
    assert {r["id"] for r in rows["accounts"] if r["customer_id"] == 1} == {100, 101}
    assert any(t.table == "accounts" and t.parent_key == {"id": 1} for t in result.truncated)
    # parents always come: every branch an account needs, every referenced customer and account
    for acc in rows["accounts"]:
        assert acc["branch_id"] in by_id["branches"] and acc["customer_id"] in by_id["customers"]
    for cust in rows["customers"]:
        assert cust["primary_account_id"] in by_id["accounts"]
        assert cust["referred_by"] is None or cust["referred_by"] in by_id["customers"]
    # children of a parent pulled only for integrity are not followed (branches -> accounts)
    assert by_id["customers"] == {1, 2}
    # transactions are children of pulled accounts, unbounded here
    assert {r["account_id"] for r in rows["transactions"]} == by_id["accounts"]
    assert "addresses" not in rows  # no foreign key says how to reach them
    assert "crm_contacts" not in rows


def test_closure_terminates_on_cycles_and_rows_are_unique(url):
    # customers <-> accounts and customers.referred_by (1 <-> 2) are both cycles
    rows = closure.extract(url, spec())
    for table, items in rows.items():
        ids = [r["id"] for r in items]
        assert len(ids) == len(set(ids)), table


def test_association_adds_the_address_table(url):
    rows = closure.extract(url, spec(associations=[ASSOC]))
    customers = {r["id"] for r in rows["customers"]}
    assert {r["customer_ref"] for r in rows["addresses"]} == customers
    assert len(rows["addresses"]) == 2 * len(customers)


def test_where_seed_with_bound_parameters(url):
    s = closure.SubsetSpec(
        seeds=[
            {"table": "accounts", "where": "status = :s", "params": {"s": "primary"}, "limit": 2}
        ]
    )
    rows = closure.extract(url, s)
    assert [r["status"] for r in rows["accounts"] if r["id"] in (100, 105)] == ["primary"] * 2


def test_exclude_and_max_rows(url):
    rows = closure.extract(url, spec(exclude=["transactions"]))
    assert "transactions" not in rows
    result = closure.extract_with_report(url, spec(max_rows=3))
    assert result.stopped_at_max_rows and sum(result.counts.values()) == 3


def test_unknown_names_are_spec_errors(url):
    with pytest.raises(closure.SpecError, match="nope"):
        closure.extract(url, closure.SubsetSpec(seeds=[{"table": "nope", "values": [1]}]))
    bad = dict(ASSOC, column="nope")
    with pytest.raises(closure.SpecError, match="nope"):
        closure.extract(url, spec(associations=[bad]))


def test_own_extractor_follows_the_protocol(url):
    ex: closure.Extractor = closure.OwnExtractor(url)
    plan = ex.plan(spec(associations=[ASSOC]))
    assert any("association" in q and "addresses" in q for q in plan.queries)
    assert "sqlite" not in repr(ex)
    assert set(ex.run(plan).rows) >= {"customers", "accounts", "addresses"}


# -- app-load trace diff -----------------------------------------------------------------------


def test_trace_diff_finds_the_address_table_and_is_empty_after_the_association(url):
    observed = ["customers", "accounts", "addresses", "branches"]
    missing = closure.missing_relations(url, spec(), observed)
    assert [m.table for m in missing] == ["addresses"]
    (cand,) = missing[0].candidates
    assert (cand.child, cand.column, cand.parent, cand.column_parent) == (
        "addresses", "customer_ref", "customers", "id"
    )  # fmt: skip
    assert closure.missing_relations(url, spec(associations=[cand]), observed) == []


# -- Postgres in Docker ------------------------------------------------------------------------

PG_PORT = 18850
PG_NAME = "tl-realdata-pg-test"
PG_PASSWORD = "lab-test-only"
PG_URL = f"postgresql+psycopg://postgres:{PG_PASSWORD}@127.0.0.1:{PG_PORT}/postgres"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="module")
def pg_url(url):
    if not _docker_ok():
        pytest.skip("Docker is not available")
    psycopg = pytest.importorskip("psycopg")
    subprocess.run(["docker", "rm", "-f", PG_NAME], capture_output=True)
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", PG_NAME, "-p", f"127.0.0.1:{PG_PORT}:5432",
         "-e", f"POSTGRES_PASSWORD={PG_PASSWORD}", "postgres:17-alpine"],
        capture_output=True, text=True,
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"cannot start postgres:17-alpine: {started.stderr.strip()[:200]}")
    dsn = f"host=127.0.0.1 port={PG_PORT} user=postgres password={PG_PASSWORD} dbname=postgres"
    try:
        deadline = time.time() + 90
        while True:
            try:
                db = psycopg.connect(dsn, autocommit=True)
                break
            except psycopg.OperationalError:
                if time.time() > deadline:
                    pytest.fail("Postgres did not start in time")
                time.sleep(1)
        with db:
            ddl = demo.DDL.replace("INTEGER REFERENCES accounts(id)", "INTEGER")
            db.execute(ddl)
            db.execute(
                "ALTER TABLE customers ADD FOREIGN KEY (primary_account_id) REFERENCES accounts(id)"
            )
            db.execute("SET session_replication_role = replica")
            src = sqlite3.connect(url.removeprefix("sqlite:///"))
            for table in ("branches", "customers", "accounts", "transactions", "addresses"):
                cur = src.execute(f"SELECT * FROM {table}")
                marks = ", ".join(["%s"] * len(cur.description))
                with db.cursor() as c:
                    c.executemany(f"INSERT INTO {table} VALUES ({marks})", cur.fetchall())
            src.close()
        yield PG_URL
    finally:
        subprocess.run(["docker", "rm", "-f", PG_NAME], capture_output=True)


def test_postgres_inventory_and_closure(pg_url):
    tables = {t.name: t for t in inventory.pull(pg_url, counts=True)}
    assert tables["customers"].row_count == 6
    assert {f.parent for f in tables["customers"].foreign_keys} == {"accounts", "customers"}
    assert ["ssn"] in tables["customers"].unique_constraints
    assert tables["accounts"].audit_columns == ["last_mod_ts"]  # unquoted names fold to lower
    rows = closure.extract(pg_url, spec(associations=[ASSOC]))
    assert {r["id"] for r in rows["customers"]} == {1, 2}
    assert rows["transactions"][0]["amount"] == "12.50"  # NUMERIC stays exact, as text
    assert closure.missing_relations(pg_url, spec(), ["customers", "addresses"])[0].candidates


def test_postgres_session_is_read_only(pg_url):
    with connection(pg_url) as conn:
        assert conn.exec_driver_sql("SHOW default_transaction_read_only").scalar() == "on"
        with pytest.raises(WriteRefused):
            conn.exec_driver_sql("UPDATE customers SET email = NULL")
        raw = conn.connection.dbapi_connection
        with pytest.raises(Exception, match="read-only"):
            raw.execute("UPDATE customers SET email = NULL")
