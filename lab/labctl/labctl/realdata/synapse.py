"""The Synapse adapter behind the extractor protocol (Prod Echo I-16).

Some teams may not let a tool read the replica row by row; a data platform (Azure Synapse, or
any service that runs SQL and drops files) does the extraction instead. The adapter splits the
work:

1. `write_job(url, spec, job_dir)` works out the closure (which rows of which tables) with the
   read-only closure engine and writes, per table, the SQL that selects exactly those rows by
   key, plus `job.json`. Portable SQL: standard single-quoted literals, IN lists of at most 500,
   composite keys as OR-ed AND groups.
2. The platform runs each `<table>.sql` and writes `<output>/<table>.json`: a JSON list of row
   objects (column name -> value).
3. `read_output(job_dir, output_dir)` reads those files back as an `Extraction` and checks
   that every table is there and that the rows are exactly the keys the job asked for.

The job SQL holds key values of real rows: keep the job folder on this machine, like the
evidence. The wire format is a placeholder until it is agreed with the platform's owner.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from labctl.realdata._db import RealDataError
from labctl.realdata.closure import Extraction, OwnExtractor, SubsetSpec, _json_safe

BATCH = 500
FORMAT = "twinlab-synapse-job/1"


class SynapseError(RealDataError):
    pass


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return "'" + value.isoformat() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def job_sql(table: str, key: list[str], keys: list[tuple]) -> str:
    if not keys:
        return f"SELECT * FROM {_ident(table)} WHERE 1 = 0;\n"
    parts = []
    for i in range(0, len(keys), BATCH):
        chunk = keys[i : i + BATCH]
        if len(key) == 1:
            values = ", ".join(sql_literal(k[0]) for k in chunk)
            parts.append(f"SELECT * FROM {_ident(table)} WHERE {_ident(key[0])} IN ({values});")
        else:

            def cond(c, v):
                return f"{_ident(c)} IS NULL" if v is None else f"{_ident(c)} = {sql_literal(v)}"

            groups = [
                "(" + " AND ".join(cond(c, v) for c, v in zip(key, k, strict=True)) + ")"
                for k in chunk
            ]
            parts.append(f"SELECT * FROM {_ident(table)} WHERE " + " OR ".join(groups) + ";")
    return "\n".join(parts) + "\n"


def write_job(url: str, spec: SubsetSpec, job_dir: Path) -> dict:
    """Plan the closure and write the job. Returns job.json's content."""
    extractor = OwnExtractor(url)
    plan = extractor.plan(spec)
    found = extractor.run(plan)  # held in memory only; only the keys leave this function
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    tables = []
    for name, rows in sorted(found.rows.items()):
        key = plan.tables[name].primary_key or [c.name for c in plan.tables[name].columns]
        keys = [tuple(r.get(c) for c in key) for r in rows]
        (job_dir / f"{name}.sql").write_text(job_sql(name, key, keys), encoding="utf-8")
        tables.append({"table": name, "key": key, "rows": len(rows),
                       "keys": [[_json_safe(v) for v in k] for k in keys]})  # fmt: skip
    job = {
        "format": FORMAT,
        "made": datetime.now(UTC).isoformat(timespec="seconds"),
        "process": spec.process,
        "app": spec.app,
        "output": "<output folder>/<table>.json: a JSON list of row objects",
        "tables": tables,
        "truncated": [str(t) for t in found.truncated],
    }
    (job_dir / "job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")
    return job


def _key_text(values) -> tuple[str, ...]:
    return tuple("" if v is None else str(v) for v in values)


def read_output(job_dir: Path, output_dir: Path) -> Extraction:
    """The platform's output as an Extraction; refuses a missing table or rows the job did not
    ask for (or asked for and did not get)."""
    try:
        job = json.loads((Path(job_dir) / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SynapseError(f"cannot read the job in {job_dir}: {exc}") from exc
    if job.get("format") != FORMAT:
        raise SynapseError(f"{job_dir}/job.json is not a {FORMAT} job")
    rows: dict[str, list[dict]] = {}
    problems = []
    for t in job["tables"]:
        path = Path(output_dir) / f"{t['table']}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            problems.append(f"{t['table']}: no output file {path.name}")
            continue
        except (OSError, ValueError) as exc:
            problems.append(f"{t['table']}: {path.name} is not JSON ({exc})")
            continue
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            problems.append(f"{t['table']}: {path.name} must be a list of row objects")
            continue
        want = {_key_text(k) for k in t["keys"]}
        got = {_key_text(r.get(c) for c in t["key"]) for r in data}
        if got - want:
            problems.append(f"{t['table']}: {len(got - want)} row(s) the job did not ask for")
        if want - got:
            problems.append(f"{t['table']}: {len(want - got)} asked-for row(s) missing")
        rows[t["table"]] = data
    if problems:
        raise SynapseError("the output does not match the job:\n" + "\n".join(problems))
    return Extraction(rows=rows)
