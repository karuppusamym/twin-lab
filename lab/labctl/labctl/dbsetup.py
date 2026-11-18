"""Create a demo app's tables on any database engine, from its `db/schema.sql`.

The schema file is parsed (labctl.data.ddl: any dialect, views become tables) and the tables are
created through SQLAlchemy, which writes the right SQL for SQL Server, Oracle, Postgres or SQLite:
one schema file serves every engine. Existing tables are left alone (`checkfirst`), so this is safe
to run on every start.
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlalchemy as sa

from labctl.data import ddl


def sa_type(spelled: str, engine: str):
    """A generic type from the DDL reader as a SQLAlchemy type."""
    t = (spelled or "TEXT").upper()
    m = re.match(r"^(\w+(?: \w+)*?)\s*(?:\((\d+)(?:\s*,\s*(\d+))?\))?$", t)
    base, a, b = (m.group(1), m.group(2), m.group(3)) if m else (t, None, None)
    if base in ("INTEGER", "INT"):
        return sa.Integer()
    if base == "BIGINT":
        return sa.BigInteger()
    if base == "SMALLINT":
        return sa.SmallInteger()
    if base in ("NUMERIC", "DECIMAL"):
        return sa.Numeric(int(a) if a else 18, int(b) if b else (0 if a else 2))
    if base in ("REAL", "FLOAT", "DOUBLE", "DOUBLE PRECISION"):
        return sa.Float()
    if base == "BOOLEAN":
        return sa.Boolean()
    if base == "DATE":
        return sa.Date()
    if base.startswith("TIMESTAMP"):
        return sa.DateTime(timezone="TIME ZONE" in base)
    if base == "TIME":
        return sa.Time()
    if base in ("BLOB", "BYTEA", "BINARY", "VARBINARY"):
        return sa.LargeBinary()
    if base == "UUID":
        return sa.Uuid()
    if base in ("VARCHAR", "CHAR", "CHARACTER VARYING", "NVARCHAR") and a:
        return sa.Unicode(int(a))
    # TEXT and anything unknown: long text (NVARCHAR(MAX) on SQL Server, CLOB on Oracle).
    # Oracle cannot index or key a CLOB, so a key column gets a bounded string instead.
    return sa.UnicodeText()


def metadata(schema_sql: str, engine: str, schema: str | None = None) -> sa.MetaData:
    tables, _ = ddl.parse_with_problems(schema_sql)
    meta = sa.MetaData(schema=schema)
    names = {t.name for t in tables}
    for t in tables:
        cols = []
        keyed = {c.name for c in t.columns if c.primary_key or c.unique or c.references}
        for c in t.columns:
            kind = sa_type(c.type, engine)
            if isinstance(kind, sa.UnicodeText) and c.name in keyed:
                kind = sa.Unicode(255)  # keys cannot be unbounded text on SQL Server or Oracle
            args = []
            if c.references and c.references[0] in names:
                target = f"{schema}.{c.references[0]}" if schema else c.references[0]
                args.append(sa.ForeignKey(f"{target}.{c.references[1]}"))
            if c.autoincrement and c.primary_key:
                args.append(sa.Identity(always=False))  # SQLite ignores it: see below
                if engine == "sqlite" and isinstance(kind, sa.Integer):
                    # Only a column declared exactly INTEGER PRIMARY KEY is SQLite's rowid and
                    # numbers itself (MAX + 1, so a reset gives the same ids again); BIGINT
                    # would not. SQLite integers are 64-bit either way.
                    kind = sa.Integer()
            cols.append(sa.Column(c.name, kind, *args, primary_key=c.primary_key,
                                  nullable=c.nullable and not c.primary_key,
                                  unique=c.unique and not c.primary_key))  # fmt: skip
        sa.Table(t.name, meta, *cols)
    return meta


def create_tables(url: str, schema_file: Path, engine: str, schema: str | None = None) -> list[str]:
    """Create the tables that do not exist yet. Returns the names it created."""
    meta = metadata(Path(schema_file).read_text(encoding="utf-8"), engine, schema)
    db = sa.create_engine(url)
    try:
        with db.connect() as conn:
            have = {n.lower() for n in sa.inspect(conn).get_table_names(schema=schema)}
        missing = [t for t in meta.sorted_tables if t.name.lower() not in have]
        if missing:
            meta.create_all(db, tables=missing, checkfirst=True)
        return [t.name for t in missing]
    finally:
        db.dispose()
