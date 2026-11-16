"""Snapshots, loads and resets on SQL Server and Oracle, with the same behaviour as `data.Database`
(Postgres): one transaction, validation before anything changes, children cleared with their
parents, identity counters kept in step, every row read back for the baseline.

SQLAlchemy does the dialect work: `mssql+pymssql://user:pw@host:1433/db` and
`oracle+oracledb://user:pw@host:1521/?service_name=FREEPDB1` (thin mode, no Oracle client). The
calls are synchronous and run in a worker thread so the agent's event loop never waits on them.

Differences from Postgres, by necessity:
- Triggers stay enabled (disabling them needs ALTER rights a service account rarely has).
- Circular foreign keys are handled by switching those constraints off for the load and back on
  (SQL Server NOCHECK/CHECK, Oracle DISABLE/ENABLE); that needs ALTER on the tables.
- Identity counters are moved past the loaded rows (SQL Server DBCC CHECKIDENT, Oracle
  START WITH LIMIT VALUE); without the right to, the load still succeeds and says so.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import types as T
from sqlalchemy.engine import Connection, Engine

from lab_agent.data import (
    DataProblems,
    LoadResult,
    Problem,
    Rows,
    convert_rows,
    fk_problems,
    needed_keys,
    self_ordered,
)
from lab_agent.schema import Column, ForeignKey, Schema, Table
from lab_agent.values import JsonValue, row_to_json

log = logging.getLogger("lab_agent.sqldb")


def engine_of(url: str) -> str:
    """'postgresql', 'mssql', 'oracle' or 'other', from a database URL."""
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    return {"postgres": "postgresql", "postgresql": "postgresql", "mssql": "mssql",
            "sqlserver": "mssql", "oracle": "oracle"}.get(scheme, "other")  # fmt: skip


def kind_of(t: T.TypeEngine) -> tuple[str, str, int | None]:
    """(data_type, udt, max_length) in the Postgres-style names `values.convert` understands."""
    name = type(t).__name__.upper()
    length = getattr(t, "length", None)
    if isinstance(t, T.Boolean) or name == "BIT":
        return "boolean", "bool", None
    if name == "TINYINT" or isinstance(t, T.SmallInteger):
        return "smallint", "int2", None
    if isinstance(t, T.BigInteger):
        return "bigint", "int8", None
    if isinstance(t, T.Integer):
        return "integer", "int4", None
    if isinstance(t, T.Numeric) and not isinstance(t, T.Float):
        precision, scale = getattr(t, "precision", None), getattr(t, "scale", None)
        if name == "NUMBER" and scale == 0 and precision:  # Oracle NUMBER(p): an integer
            return ("integer", "int4", None) if precision <= 9 else ("bigint", "int8", None)
        return "numeric", "numeric", None
    if isinstance(t, T.Float) or name in ("REAL", "BINARY_DOUBLE", "BINARY_FLOAT"):
        return "double precision", "float8", None
    if name in ("DATETIMEOFFSET",) or getattr(t, "timezone", False):
        return "timestamp with time zone", "timestamptz", None
    if isinstance(t, T.DateTime) or name in ("DATETIME2", "SMALLDATETIME"):
        return "timestamp", "timestamp", None  # Oracle DATE carries a time too
    if isinstance(t, T.Date):
        return "date", "date", None
    if isinstance(t, T.Time):
        return "time", "time", None
    if isinstance(t, T.LargeBinary) or name in ("VARBINARY", "BINARY", "IMAGE", "RAW", "BLOB"):
        return "bytea", "bytea", None
    if name in ("UNIQUEIDENTIFIER", "UUID") or isinstance(t, T.Uuid):
        return "uuid", "uuid", None
    if isinstance(t, T.String) or name in ("CLOB", "NCLOB", "NTEXT", "XML"):
        return "character varying", "varchar", length if isinstance(length, int) else None
    return name.lower(), "text", None


def _adapt(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, JsonValue):
        import json

        return json.dumps(value.obj)
    return value


class SqlDatabase:
    """The `data.Database` interface over SQLAlchemy, for SQL Server and Oracle."""

    def __init__(self, url: str, schema: str | None = None, only: set[str] | None = None) -> None:
        self.url = url
        self.engine_name = engine_of(url)
        self.schema_name = schema
        self.only = {n.lower() for n in only} if only else None
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            args: dict[str, Any] = {"pool_pre_ping": True}
            if self.engine_name == "mssql":
                args["connect_args"] = {"login_timeout": 10, "timeout": 120}
            self._engine = sa.create_engine(self.url, **args)
        return self._engine

    # --- schema --------------------------------------------------------------------------------

    def _read_schema(self, conn: Connection) -> tuple[Schema, sa.MetaData]:
        insp = sa.inspect(conn)
        names = [n for n in insp.get_table_names(schema=self.schema_name)
                 if self.only is None or n.lower() in self.only]  # fmt: skip
        meta = sa.MetaData(schema=self.schema_name)
        meta.reflect(bind=conn, only=names)
        tables: dict[str, Table] = {}
        for name in names:
            cols = []
            for pos, c in enumerate(insp.get_columns(name, schema=self.schema_name), start=1):
                dtype, udt, length = kind_of(c["type"])
                ident = c.get("identity")
                identity = None
                if ident:
                    identity = "ALWAYS" if ident.get("always") else "BY DEFAULT"
                elif self.engine_name == "mssql" and c.get("autoincrement") is True:
                    identity = "BY DEFAULT"
                cols.append(Column(
                    name=c["name"], data_type=dtype, udt_name=udt, nullable=bool(c["nullable"]),
                    default=str(c["default"]) if c.get("default") is not None else None,
                    identity=identity, max_length=length, position=pos,
                    generated=bool(c.get("computed")),
                ))  # fmt: skip
            pk = insp.get_pk_constraint(name, schema=self.schema_name) or {}
            fks = [
                ForeignKey(
                    fk.get("name") or f"fk_{name}_{i}",
                    name,
                    list(fk["constrained_columns"]),
                    fk["referred_table"],
                    list(fk["referred_columns"]),
                )  # fmt: skip
                for i, fk in enumerate(insp.get_foreign_keys(name, schema=self.schema_name))
                if fk.get("referred_table") in names
            ]
            tables[name] = Table(name=name, columns=cols,
                                 primary_key=list(pk.get("constrained_columns") or []),
                                 foreign_keys=fks)  # fmt: skip
        return Schema(tables=tables), meta

    def _sa_table(self, meta: sa.MetaData, name: str) -> sa.Table:
        key = f"{self.schema_name}.{name}" if self.schema_name else name
        return meta.tables[key]

    # --- reading -----------------------------------------------------------------------------

    def _rows(self, conn: Connection, meta: sa.MetaData, table: Table) -> list[dict[str, Any]]:
        t = self._sa_table(meta, table.name)
        q = sa.select(t)
        if table.primary_key:
            q = q.order_by(*[t.c[c] for c in table.primary_key])
        return [row_to_json(dict(r._mapping)) for r in conn.execute(q)]

    def _snapshot_in(self, conn, schema: Schema, meta, tables: list[str] | None = None) -> Rows:
        names = tables if tables is not None else sorted(schema.tables)
        return {n: self._rows(conn, meta, schema.tables[n]) for n in names if n in schema.tables}

    def _snapshot(self, tables: list[str] | None) -> tuple[Schema, Rows]:
        with self.engine.connect() as conn:
            schema, meta = self._read_schema(conn)
            rows = self._snapshot_in(conn, schema, meta, tables)
            conn.rollback()
            return schema, rows

    async def snapshot(self, tables: list[str] | None = None) -> tuple[Schema, Rows]:
        return await asyncio.to_thread(self._snapshot, tables)

    async def read_schema(self, conn: Any = None) -> Schema:
        def read() -> Schema:
            with self.engine.connect() as c:
                return self._read_schema(c)[0]

        return await asyncio.to_thread(read)

    async def ping(self, timeout: float = 3.0) -> bool:
        def check() -> bool:
            with self.engine.connect() as c:
                c.execute(
                    sa.text("SELECT 1 FROM DUAL" if self.engine_name == "oracle" else "SELECT 1")
                )
            return True

        try:
            return await asyncio.wait_for(asyncio.to_thread(check), timeout)
        except Exception as exc:  # noqa: BLE001 - any failure means "down"
            log.debug("database ping failed: %s", exc)
            return False

    # --- writing -----------------------------------------------------------------------------

    def _constraints(self, conn: Connection, names: list[str], on: bool) -> None:
        """Foreign keys of `names` off (for a load with circular references) or back on."""
        for name in names:
            if self.engine_name == "mssql":
                q = self._quoted(name)
                conn.execute(
                    sa.text(
                        f"ALTER TABLE {q} {'WITH CHECK CHECK' if on else 'NOCHECK'} CONSTRAINT ALL"
                    )
                )
            elif self.engine_name == "oracle":
                found = conn.execute(sa.text(
                    "SELECT constraint_name FROM user_constraints "
                    "WHERE table_name = :t AND constraint_type = 'R'"), {"t": name.upper()}).all()  # fmt: skip
                for (cname,) in found:
                    conn.execute(sa.text(f'ALTER TABLE {self._quoted(name)} '
                                         f'{"ENABLE" if on else "DISABLE"} CONSTRAINT "{cname}"'))  # fmt: skip

    def _quoted(self, name: str) -> str:
        prep = self.engine.dialect.identifier_preparer
        table = prep.quote(name if self.engine_name != "oracle" else name)
        return f"{prep.quote_schema(self.schema_name)}.{table}" if self.schema_name else table

    def _reseed(self, conn: Connection, schema: Schema, names: list[str]) -> list[str]:
        """Identity counters past the highest loaded value. Returns notes when not allowed."""
        notes = []
        for name in names:
            for col in schema.tables[name].columns:
                if not col.identity:
                    continue
                high = conn.execute(sa.text(
                    f"SELECT MAX({self.engine.dialect.identifier_preparer.quote(col.name)}) "
                    f"FROM {self._quoted(name)}")).scalar()  # fmt: skip
                try:
                    with conn.begin_nested():
                        if self.engine_name == "mssql" and high is not None:
                            plain = f"{self.schema_name}.{name}" if self.schema_name else name
                            conn.execute(
                                sa.text(f"DBCC CHECKIDENT ('{plain}', RESEED, {int(high)})")
                            )
                        elif self.engine_name == "oracle":
                            ident = self.engine.dialect.identifier_preparer.quote(col.name)
                            conn.execute(sa.text(
                                f"ALTER TABLE {self._quoted(name)} MODIFY ({ident} GENERATED BY DEFAULT "
                                "AS IDENTITY (START WITH LIMIT VALUE))"))  # fmt: skip
                except Exception as exc:  # noqa: BLE001 - the load stands; the counter may lag
                    notes.append(
                        f"{name}.{col.name}: identity counter not moved ({exc.__class__.__name__})"
                    )
        return notes

    def _replace(self, rows: Rows) -> LoadResult:
        with self.engine.connect() as conn:
            schema, meta = self._read_schema(conn)
            given = set(rows)
            cleared = schema.children_of(given & set(schema.tables))
            converted, problems = convert_rows(schema, rows)
            if problems:
                raise DataProblems(problems)
            db_keys = {}
            for ref_table, ref_cols in needed_keys(schema, converted, cleared):
                t = self._sa_table(meta, ref_table)
                db_keys[(ref_table, ref_cols)] = {
                    tuple(r) for r in conn.execute(sa.select(*[t.c[c] for c in ref_cols]))
                }
            problems = fk_problems(schema, converted, cleared, db_keys)
            if problems:
                raise DataProblems(problems)
            always = [(n, c.name) for n in given for c in schema.tables[n].columns
                      if c.identity == "ALWAYS" and any(c.name in r for r in converted[n])]  # fmt: skip
            if always:
                raise DataProblems([Problem(n, None, c, "this identity column is GENERATED ALWAYS: "
                                            "values cannot be loaded into it") for n, c in always])  # fmt: skip
            order, cycle = schema.insert_order(given)
            clear_order, _ = schema.insert_order(given | cleared)
            conn.rollback()  # end the reading transaction; the load is one transaction of its own
            try:
                with conn.begin():
                    if cycle:
                        self._constraints(conn, sorted(given | cleared), on=False)
                    for name in reversed(clear_order):  # children first
                        conn.execute(self._sa_table(meta, name).delete())
                    for name in order:
                        trows = self_ordered(schema.tables[name], converted[name])
                        if trows:
                            groups: dict[tuple, list[dict]] = {}
                            for r in trows:
                                groups.setdefault(tuple(r), []).append(
                                    {k: _adapt(v) for k, v in r.items()}
                                )
                            for grows in groups.values():
                                conn.execute(self._sa_table(meta, name).insert(), grows)
                    if cycle:
                        self._constraints(conn, sorted(given | cleared), on=True)
                    notes = self._reseed(conn, schema, sorted(given | cleared))
                    snapshot = self._snapshot_in(conn, schema, meta)
            except sa.exc.DBAPIError as exc:
                message = str(exc.orig) if exc.orig is not None else str(exc)
                raise DataProblems([Problem(None, None, None, message[:500])]) from exc
            for note in notes:
                log.warning(note)
            counts = {n: len(snapshot.get(n, [])) for n in sorted(given | cleared)}
            return LoadResult(counts=counts, replaced=sorted(given), cleared=sorted(cleared),
                              snapshot=snapshot, triggers="enabled")  # fmt: skip

    async def replace(self, rows: Rows) -> LoadResult:
        return await asyncio.to_thread(self._replace, rows)
