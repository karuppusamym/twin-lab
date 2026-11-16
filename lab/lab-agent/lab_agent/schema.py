"""Tables, columns, primary and foreign keys, read from information_schema."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Column:
    name: str
    data_type: str  # information_schema data_type, e.g. "integer", "timestamp with time zone"
    udt_name: str  # e.g. "int4", "_text" for arrays
    nullable: bool
    default: str | None
    identity: str | None  # "ALWAYS" | "BY DEFAULT" | None
    max_length: int | None
    position: int
    generated: bool = False  # GENERATED ALWAYS AS (...) STORED: read, never written

    @property
    def has_default(self) -> bool:
        return self.default is not None or self.identity is not None

    @property
    def is_sequence(self) -> bool:
        return self.identity is not None or (self.default or "").startswith("nextval(")

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.data_type
            if self.data_type != "ARRAY"
            else self.udt_name.lstrip("_") + "[]",
            "nullable": self.nullable,
            "max_length": self.max_length,
            "default": self.default,
            "identity": self.identity,
        }


@dataclass
class ForeignKey:
    name: str
    table: str
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    name: str
    columns: list[Column]
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "primary_key": self.primary_key,
            "columns": [
                {**c.info(), "primary_key": c.name in self.primary_key, "references": self._ref(c)}
                for c in self.columns
            ],
        }

    def _ref(self, col: Column) -> dict[str, str] | None:
        for fk in self.foreign_keys:
            if col.name in fk.columns and len(fk.columns) == 1:
                return {"table": fk.ref_table, "column": fk.ref_columns[0]}
        return None


@dataclass
class Schema:
    tables: dict[str, Table]

    def children_of(self, names: set[str]) -> set[str]:
        """Every table that references one of `names`, directly or through other tables."""
        found: set[str] = set()
        frontier = set(names)
        while frontier:
            nxt: set[str] = set()
            for t in self.tables.values():
                if t.name in found or t.name in names:
                    continue
                if any(fk.ref_table in frontier for fk in t.foreign_keys):
                    nxt.add(t.name)
            found |= nxt
            frontier = nxt
        return found

    def insert_order(self, names: set[str]) -> tuple[list[str], bool]:
        """Parents before children, among `names`. Returns (order, had_cycle)."""
        remaining = set(names)
        order: list[str] = []
        while remaining:
            ready = sorted(
                n
                for n in remaining
                if not any(
                    fk.ref_table in remaining and fk.ref_table != n
                    for fk in self.tables[n].foreign_keys
                )
            )
            if not ready:  # a cycle between tables: alphabetical, and the caller defers checks
                return order + sorted(remaining), True
            order.extend(ready)
            remaining -= set(ready)
        return order, False


SQL_TABLES = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = %(schema)s AND table_type = 'BASE TABLE'
ORDER BY table_name
"""

SQL_COLUMNS = """
SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default,
       is_identity, identity_generation, character_maximum_length, ordinal_position,
       is_generated
FROM information_schema.columns
WHERE table_schema = %(schema)s
ORDER BY table_name, ordinal_position
"""

SQL_PRIMARY_KEYS = """
SELECT tc.table_name, kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_schema = tc.constraint_schema AND kcu.constraint_name = tc.constraint_name
 AND kcu.table_name = tc.table_name
WHERE tc.table_schema = %(schema)s AND tc.constraint_type = 'PRIMARY KEY'
ORDER BY tc.table_name, kcu.ordinal_position
"""

SQL_FOREIGN_KEYS = """
SELECT kcu.constraint_name, kcu.table_name, kcu.column_name,
       ref.table_name AS ref_table, ref.column_name AS ref_column
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_schema = rc.constraint_schema AND kcu.constraint_name = rc.constraint_name
JOIN information_schema.key_column_usage ref
  ON ref.constraint_schema = rc.unique_constraint_schema
 AND ref.constraint_name = rc.unique_constraint_name
 AND ref.ordinal_position = kcu.position_in_unique_constraint
WHERE kcu.table_schema = %(schema)s
ORDER BY kcu.table_name, kcu.constraint_name, kcu.ordinal_position
"""


# A view in the schema file is a table in a demo database (labctl.dbsetup), so it counts too.
_CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?(?:MATERIALIZED\s+)?(?:TABLE|VIEW)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(?:[\"\[]?\w+[\"\]]?\.)?[\"\[]?(\w+)[\"\]]?",
    re.IGNORECASE,
)


def tables_in_ddl(path: Path) -> set[str]:
    """Table names created by a schema file (used to limit the agent to the app's own tables);
    views count, since a demo database holds a table for each."""
    text = path.read_text(encoding="utf-8")
    text = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    return {m.group(1) for m in _CREATE_TABLE.finditer(text)}


async def read_schema(conn: Any, schema: str = "public", only: set[str] | None = None) -> Schema:
    params = {"schema": schema}

    async def rows(sql: str) -> list[tuple]:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()

    names = [r[0] for r in await rows(SQL_TABLES)]
    if only:
        names = [n for n in names if n in only]
    tables = {n: Table(name=n, columns=[]) for n in names}
    for t, col, dtype, udt, nullable, default, is_id, id_gen, maxlen, pos, gen in await rows(
        SQL_COLUMNS
    ):
        if t in tables:
            tables[t].columns.append(
                Column(
                    name=col,
                    data_type=dtype,
                    udt_name=udt,
                    nullable=nullable == "YES",
                    default=default,
                    identity=id_gen if is_id == "YES" else None,
                    max_length=maxlen,
                    position=pos,
                    generated=gen == "ALWAYS",
                )
            )
    for t, col in await rows(SQL_PRIMARY_KEYS):
        if t in tables:
            tables[t].primary_key.append(col)
    fks: dict[tuple[str, str], ForeignKey] = {}
    for name, t, col, ref_t, ref_col in await rows(SQL_FOREIGN_KEYS):
        if t not in tables:
            continue
        fk = fks.setdefault((t, name), ForeignKey(name, t, [], ref_t, []))
        fk.columns.append(col)
        fk.ref_columns.append(ref_col)
    for (t, _), fk in fks.items():
        tables[t].foreign_keys.append(fk)
    return Schema(tables=tables)
