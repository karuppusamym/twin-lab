"""Schema inventory of a replica (Prod Echo I-10), through the SQLAlchemy inspector.

Read-only: the connection is opened read-only where the dialect allows it and a guard refuses any
statement that is not a read. The URL is never written anywhere; messages show it masked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy import table as sa_table
from sqlalchemy.engine import Connection

from labctl.realdata._db import connection
from labctl.source.sqlalchemy_models import Column, Table

AUDIT_PATTERN = re.compile(
    r"^(updated?_?(at|on|ts|time|date|dt)|last_?mod(ified)?(_?(at|on|ts|time|date|dt|by))?|"
    r"modified(_?(at|on|ts|time|date|dt))?|mod_?(ts|dt|date)|changed_?(at|on|ts)|row_?version|"
    r"rowversion|version_?(no|num)?|ts_?updated|upd_?(ts|dt|date)|chg_?(ts|dt))$",
    re.I,
)
AUTO_TYPES = ("INT", "SERIAL")


@dataclass
class ForeignKey:
    columns: list[str]
    parent: str
    parent_columns: list[str]
    name: str | None = None


@dataclass
class DbTable(Table):
    """A `Table` read from a live database, with what the inspector adds."""

    schema: str | None = None
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    unique_constraints: list[list[str]] = field(default_factory=list)
    audit_columns: list[str] = field(default_factory=list)
    row_count: int | None = None

    @property
    def primary_key(self) -> list[str]:
        return [c.name for c in self.columns if c.primary_key]


def is_audit_column(name: str) -> bool:
    return bool(AUDIT_PATTERN.match(name))


def _type(col: dict, dialect) -> str:
    try:
        return col["type"].compile(dialect=dialect)
    except Exception:  # noqa: BLE001  (some reflected types cannot compile; the name is enough)
        return type(col["type"]).__name__.upper()


def _sqlite_uniques(conn: Connection, name: str) -> list[list[str]]:
    quoted = '"' + name.replace('"', '""') + '"'
    out = []
    for idx in conn.exec_driver_sql(f"PRAGMA index_list({quoted})").mappings():
        if idx["origin"] == "u" and idx["unique"]:
            iq = '"' + idx["name"].replace('"', '""') + '"'
            out.append(
                [r["name"] for r in conn.exec_driver_sql(f"PRAGMA index_info({iq})").mappings()]
            )
    return out


def read(conn: Connection, schema: str | None = None, counts: bool = False) -> list[DbTable]:
    """The inventory over an open connection."""
    insp = inspect(conn)
    tables: list[DbTable] = []
    for name in sorted(insp.get_table_names(schema=schema)):
        pk = insp.get_pk_constraint(name, schema=schema) or {}
        pk_cols = list(pk.get("constrained_columns") or [])
        fks = [
            ForeignKey(
                columns=list(fk["constrained_columns"]),
                parent=fk["referred_table"],
                parent_columns=list(fk["referred_columns"]),
                name=fk.get("name"),
            )
            for fk in insp.get_foreign_keys(name, schema=schema)
            if fk.get("referred_table") and fk.get("constrained_columns")
        ]
        uniques = [
            list(u["column_names"]) for u in insp.get_unique_constraints(name, schema=schema)
        ]
        uniques += [
            list(i["column_names"])
            for i in insp.get_indexes(name, schema=schema)
            if i.get("unique") and all(i.get("column_names") or [None])
        ]
        if conn.dialect.name == "sqlite":  # inline UNIQUE only shows as an automatic index
            uniques += _sqlite_uniques(conn, name)
        uniques = [u for i, u in enumerate(uniques) if u not in uniques[:i] and u != pk_cols]
        single_fk = {fk.columns[0]: fk for fk in fks if len(fk.columns) == 1}
        columns: list[Column] = []
        for col in insp.get_columns(name, schema=schema):
            col_type = _type(col, conn.dialect)
            fk = single_fk.get(col["name"])
            auto = col.get("autoincrement")
            columns.append(
                Column(
                    name=col["name"],
                    type=col_type,
                    nullable=bool(col.get("nullable", True)),
                    primary_key=col["name"] in pk_cols,
                    unique=[col["name"]] in uniques,
                    references=(fk.parent, fk.parent_columns[0]) if fk else None,
                    autoincrement=(
                        len(pk_cols) == 1
                        and col["name"] in pk_cols
                        and auto is not False
                        and any(t in col_type.upper() for t in AUTO_TYPES)
                    ),
                )
            )
        table = DbTable(
            name=name,
            model="",
            file=None,
            line=0,
            columns=columns,
            schema=schema,
            foreign_keys=fks,
            unique_constraints=uniques,
            audit_columns=[c.name for c in columns if is_audit_column(c.name)],
        )
        if counts:
            query = select(func.count()).select_from(sa_table(name, schema=schema))
            table.row_count = int(conn.execute(query).scalar_one())
        tables.append(table)
    return tables


def pull(url: str, counts: bool = False, schema: str | None = None) -> list[Table]:
    """Tables of the database at `url` (any SQLAlchemy URL), read-only. Counts only on request."""
    with connection(url) as conn:
        return list(read(conn, schema=schema, counts=counts))


# -- JSON form (for `labctl realdata inventory` output and as input to later commands) ---------


def to_json(tables: list[Table]) -> list[dict[str, Any]]:
    out = []
    for t in tables:
        item: dict[str, Any] = {
            "name": t.name,
            "columns": [
                {
                    "name": c.name,
                    "type": c.type,
                    "nullable": c.nullable,
                    "primary_key": c.primary_key,
                    "unique": c.unique,
                    "references": list(c.references) if c.references else None,
                    "autoincrement": c.autoincrement,
                }
                for c in t.columns
            ],
        }
        if isinstance(t, DbTable):
            item.update(
                schema=t.schema,
                foreign_keys=[fk.__dict__ for fk in t.foreign_keys],
                unique_constraints=t.unique_constraints,
                audit_columns=t.audit_columns,
                row_count=t.row_count,
            )
        out.append(item)
    return out


def from_json(data: list[dict[str, Any]]) -> list[DbTable]:
    tables = []
    for item in data:
        columns = [
            Column(
                name=c["name"],
                type=c["type"],
                nullable=c.get("nullable", True),
                primary_key=c.get("primary_key", False),
                unique=c.get("unique", False),
                references=tuple(c["references"]) if c.get("references") else None,
                autoincrement=c.get("autoincrement", False),
            )
            for c in item["columns"]
        ]
        fks = [ForeignKey(**fk) for fk in item.get("foreign_keys") or []]
        if not fks:
            fks = [
                ForeignKey([c.name], c.references[0], [c.references[1]])
                for c in columns
                if c.references
            ]
        tables.append(
            DbTable(
                name=item["name"],
                model="",
                file=None,
                line=0,
                columns=columns,
                schema=item.get("schema"),
                foreign_keys=fks,
                unique_constraints=item.get("unique_constraints") or [],
                audit_columns=item.get("audit_columns")
                or [c.name for c in columns if is_audit_column(c.name)],
                row_count=item.get("row_count"),
            )
        )
    return tables


def foreign_keys(table: Table) -> list[ForeignKey]:
    """Declared foreign keys of any Table (composite ones only on a DbTable)."""
    if isinstance(table, DbTable):
        return table.foreign_keys
    return [
        ForeignKey([c.name], c.references[0], [c.references[1]])
        for c in table.columns
        if c.references
    ]
