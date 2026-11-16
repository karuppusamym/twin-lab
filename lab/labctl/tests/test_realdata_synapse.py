"""The Synapse adapter round-trips: job SQL run by "the platform" (here SQLite itself) gives the
same rows as the closure engine. And a drafted subset spec is valid and sensible."""

from __future__ import annotations

import json
import sqlite3

import pytest

from labctl.realdata import closure, demo, inventory, specgen, synapse


@pytest.fixture(scope="module")
def replica(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("replica") / "replica.db")


def spec():
    return closure.SubsetSpec(
        seeds=[{"table": "customers", "values": [1]}], closure={"limits": {"accounts": 2}}
    )


def run_platform(db, job_dir, out_dir):
    """What the data platform does: run each table's SQL, write <table>.json."""
    out_dir.mkdir()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    for sql_file in job_dir.glob("*.sql"):
        rows = []
        for stmt in sql_file.read_text(encoding="utf-8").split(";\n"):
            if stmt.strip():
                rows += [dict(r) for r in con.execute(stmt)]
        (out_dir / f"{sql_file.stem}.json").write_text(json.dumps(rows), encoding="utf-8")
    con.close()


def test_round_trip_matches_the_closure_engine(replica, tmp_path):
    url = "sqlite:///" + replica.as_posix()
    job = synapse.write_job(url, spec(), tmp_path / "job")
    assert job["format"] == synapse.FORMAT and (tmp_path / "job" / "accounts.sql").exists()
    run_platform(replica, tmp_path / "job", tmp_path / "out")
    got = synapse.read_output(tmp_path / "job", tmp_path / "out")
    want = closure.extract(url, spec())

    def ids(rows):
        return {t: sorted(r["id"] for r in rs) for t, rs in rows.items()}

    assert ids(got.rows) == ids(want)


def test_output_with_extra_or_missing_rows_is_refused(replica, tmp_path):
    url = "sqlite:///" + replica.as_posix()
    synapse.write_job(url, spec(), tmp_path / "job")
    run_platform(replica, tmp_path / "job", tmp_path / "out")
    accounts = json.loads((tmp_path / "out" / "accounts.json").read_text())
    (tmp_path / "out" / "accounts.json").write_text(json.dumps(accounts[1:] + [{"id": 999}]))
    (tmp_path / "out" / "branches.json").unlink()
    with pytest.raises(synapse.SynapseError) as err:
        synapse.read_output(tmp_path / "job", tmp_path / "out")
    text = str(err.value)
    assert "branches: no output file" in text
    assert "1 row(s) the job did not ask for" in text and "1 asked-for row(s) missing" in text


def test_job_sql_is_portable():
    sql = synapse.job_sql("t", ["a", "b"], [(1, "x'y"), (2, None)])
    assert sql == (
        'SELECT * FROM "t" WHERE ("a" = 1 AND "b" = \'x\'\'y\') OR ("a" = 2 AND "b" IS NULL);\n'
    )
    many = synapse.job_sql("t", ["id"], [(n,) for n in range(1201)])
    assert many.count("SELECT") == 3  # IN lists of at most 500
    assert synapse.job_sql("t", ["id"], []) == 'SELECT * FROM "t" WHERE 1 = 0;\n'


def test_drafted_spec_is_valid_and_runs(replica):
    url = "sqlite:///" + replica.as_posix()
    tables = inventory.pull(url)
    drafted = specgen.draft(tables, "CUSTOMERS", [1, 3], app="bank", db_env="BANK_REPLICA", limit=5)
    assert drafted["seeds"] == [{"table": "customers", "values": [1, 3]}]
    assert drafted["closure"]["limits"].get("accounts") == 5
    assert "transactions" in drafted["closure"]["limits"]
    rows = closure.extract(url, closure.SubsetSpec.model_validate(drafted))
    assert {r["id"] for r in rows["customers"]} >= {1, 3}


def test_history_tables_are_left_out():
    t = inventory.from_json([
        {"name": "cases", "columns": [{"name": "id", "type": "INTEGER", "primary_key": True}]},
        {"name": "case_audit", "columns": [{"name": "id", "type": "INTEGER", "primary_key": True},
                                           {"name": "case_id", "type": "INTEGER"}],
         "foreign_keys": [{"columns": ["case_id"], "parent": "cases", "parent_columns": ["id"]}]},
        {"name": "notes", "columns": [{"name": "id", "type": "INTEGER", "primary_key": True},
                                      {"name": "case_id", "type": "INTEGER"}],
         "foreign_keys": [{"columns": ["case_id"], "parent": "cases", "parent_columns": ["id"]}]},
    ])  # fmt: skip
    drafted = specgen.draft(t, "cases", where="status = :s", params={"s": "OPEN"})
    assert drafted["closure"]["exclude"] == ["case_audit"]
    assert drafted["closure"]["limits"] == {"notes": 20}
    with pytest.raises(closure.SpecError):
        specgen.draft(t, "nope", [1])
