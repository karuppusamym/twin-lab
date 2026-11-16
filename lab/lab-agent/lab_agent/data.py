"""Direct database work over ADMIN_DATABASE_URL: snapshots, loads and resets.

Never goes through the DB link proxy, so conditions never slow the lab's own work.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from lab_agent.schema import Schema, Table, read_schema
from lab_agent.values import JsonValue, ValueProblem, from_json, row_to_json

log = logging.getLogger("lab_agent.data")

Rows = dict[str, list[dict[str, Any]]]
MAX_PROBLEMS = 200
LOCK_TIMEOUT = "5s"


@dataclass
class Problem:
    table: str | None
    row: int | None  # 1-based position in the table's list of rows
    column: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "row": self.row,
            "column": self.column,
            "message": self.message,
        }


class DataProblems(Exception):
    def __init__(self, problems: list[Problem]) -> None:
        super().__init__(f"{len(problems)} problem(s)")
        self.problems = problems


@dataclass
class LoadResult:
    counts: dict[str, int]
    replaced: list[str]
    cleared: list[str]
    snapshot: Rows
    triggers: str  # "disabled" (session_replication_role=replica) or "enabled"


def _q(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _adapt(value: Any) -> Any:
    if isinstance(value, JsonValue):
        return Jsonb(value.obj)
    if isinstance(value, list):
        return [_adapt(v) for v in value]
    return value


class Database:
    def __init__(self, url: str, schema: str = "public", only: set[str] | None = None) -> None:
        self.url = url
        self.schema_name = schema
        self.only = only

    async def connect(self, timeout: float = 5.0) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(
            self.url,
            connect_timeout=max(1, int(timeout)),
            options="-c TimeZone=UTC",
            application_name="twinlab-lab-agent",
        )

    async def ping(self, timeout: float = 3.0) -> bool:
        try:
            async with asyncio.timeout(timeout):
                conn = await self.connect(timeout)
                async with conn:
                    await conn.execute("SELECT 1")
            return True
        except Exception as exc:  # noqa: BLE001 - any failure means "down"
            log.debug("database ping failed: %s", exc)
            return False

    async def read_schema(self, conn: psycopg.AsyncConnection | None = None) -> Schema:
        if conn is not None:
            return await read_schema(conn, self.schema_name, self.only)
        async with await self.connect() as c:
            return await read_schema(c, self.schema_name, self.only)

    # --- reading -----------------------------------------------------------------------------

    async def _rows(self, conn: psycopg.AsyncConnection, table: Table) -> list[dict[str, Any]]:
        query = sql.SQL("SELECT * FROM {}").format(_q(self.schema_name, table.name))
        if table.primary_key:
            query += sql.SQL(" ORDER BY {}").format(
                sql.SQL(", ").join(sql.Identifier(c) for c in table.primary_key)
            )
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query)
            return [row_to_json(r) for r in await cur.fetchall()]

    async def _snapshot_in(
        self, conn: psycopg.AsyncConnection, schema: Schema, tables: list[str] | None = None
    ) -> Rows:
        names = tables if tables is not None else sorted(schema.tables)
        return {n: await self._rows(conn, schema.tables[n]) for n in names if n in schema.tables}

    async def snapshot(self, tables: list[str] | None = None) -> tuple[Schema, Rows]:
        """A consistent read of every table (one REPEATABLE READ, read-only transaction)."""
        async with await self.connect() as conn:
            await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
            await conn.set_read_only(True)
            async with conn.transaction():
                schema = await read_schema(conn, self.schema_name, self.only)
                return schema, await self._snapshot_in(conn, schema, tables)

    # --- validation --------------------------------------------------------------------------

    async def validate(
        self,
        conn: psycopg.AsyncConnection,
        schema: Schema,
        rows: Rows,
        cleared: set[str],
    ) -> tuple[dict[str, list[dict[str, Any]]], list[Problem]]:
        converted, problems = convert_rows(schema, rows)
        if problems:
            return converted, problems
        db_keys: dict[tuple[str, tuple[str, ...]], set[tuple]] = {}
        for key in needed_keys(schema, converted, cleared):
            ref_table, ref_cols = key
            q = sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(sql.Identifier(c) for c in ref_cols),
                _q(self.schema_name, ref_table),
            )
            cur = await conn.execute(q)
            db_keys[key] = {tuple(r) for r in await cur.fetchall()}
        return converted, fk_problems(schema, converted, cleared, db_keys)

    # --- writing -----------------------------------------------------------------------------

    async def _clear(self, conn: psycopg.AsyncConnection, names: list[str]) -> None:
        if not names:
            return
        targets = sql.SQL(", ").join(_q(self.schema_name, n) for n in names)
        try:
            async with conn.transaction():
                await conn.execute(sql.SQL("TRUNCATE {}").format(targets))
        except psycopg.errors.LockNotAvailable:
            # Something holds a lock that TRUNCATE (ACCESS EXCLUSIVE) waits on, usually an app
            # connection idle in a transaction. DELETE needs a weaker lock; children first.
            log.warning("TRUNCATE timed out on a lock; deleting rows instead")
            for n in reversed(names):
                await conn.execute(sql.SQL("DELETE FROM {}").format(_q(self.schema_name, n)))

    _self_ordered = staticmethod(lambda table, rows: self_ordered(table, rows))

    async def _insert(self, conn: psycopg.AsyncConnection, table: Table, rows: list[dict]) -> None:
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for r in self_ordered(table, rows):
            groups.setdefault(tuple(r), []).append(r)
        # Keep the original order across groups when there is only one (the usual case).
        for cols, grows in groups.items():
            if not cols:
                q = sql.SQL("INSERT INTO {} DEFAULT VALUES").format(
                    _q(self.schema_name, table.name)
                )
                for _ in grows:
                    await conn.execute(q)
                continue
            overriding = any(
                (c := table.column(n)) is not None and c.identity == "ALWAYS" for n in cols
            )
            q = sql.SQL("INSERT INTO {} ({}){} VALUES ({})").format(
                _q(self.schema_name, table.name),
                sql.SQL(", ").join(sql.Identifier(c) for c in cols),
                sql.SQL(" OVERRIDING SYSTEM VALUE") if overriding else sql.SQL(""),
                sql.SQL(", ").join(sql.Placeholder() for _ in cols),
            )
            async with conn.cursor() as cur:
                await cur.executemany(q, [[_adapt(r[c]) for c in cols] for r in grows])

    async def _sync_sequences(self, conn: psycopg.AsyncConnection, tables: list[Table]) -> None:
        for table in tables:
            for col in table.columns:
                if not col.is_sequence:
                    continue
                qualified = f'"{self.schema_name}"."{table.name}"'
                cur = await conn.execute(
                    "SELECT pg_get_serial_sequence(%s, %s)", (qualified, col.name)
                )
                seq = (await cur.fetchone())[0]
                if not seq:
                    continue
                q = sql.SQL(
                    "SELECT setval(%(seq)s::regclass, GREATEST("
                    "COALESCE((SELECT MAX({col}) FROM {tbl}), 0) + 1, "
                    "(SELECT seqmin FROM pg_sequence WHERE seqrelid = %(seq)s::regclass)), false)"
                ).format(col=sql.Identifier(col.name), tbl=_q(self.schema_name, table.name))
                await conn.execute(q, {"seq": seq})

    async def replace(self, rows: Rows) -> LoadResult:
        """Replace the tables in `rows` (and clear their children) in one transaction.

        Raises DataProblems (nothing changed) on any validation or database error.
        """
        async with await self.connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(LOCK_TIMEOUT))
                )
                schema = await read_schema(conn, self.schema_name, self.only)
                given = set(rows)
                cleared = schema.children_of(given & set(schema.tables))
                converted, problems = await self.validate(conn, schema, rows, cleared)
                if problems:
                    raise DataProblems(problems)

                triggers = "enabled"
                try:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL session_replication_role = replica")
                    triggers = "disabled"
                except psycopg.errors.InsufficientPrivilege:
                    pass
                order, cycle = schema.insert_order(given)
                if cycle and triggers == "enabled":
                    await conn.execute("SET CONSTRAINTS ALL DEFERRED")
                clear_order, _ = schema.insert_order(given | cleared)
                try:
                    await self._clear(conn, clear_order)
                    for name in order:
                        await self._insert(conn, schema.tables[name], converted[name])
                    await self._sync_sequences(
                        conn, [schema.tables[n] for n in sorted(given | cleared)]
                    )
                    if triggers == "disabled":
                        await conn.execute("SET LOCAL session_replication_role = origin")
                    else:
                        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    snapshot = await self._snapshot_in(conn, schema)
                except psycopg.errors.LockNotAvailable as exc:
                    raise DataProblems(
                        [Problem(None, None, None, f"database busy, try again: {exc}")]
                    ) from exc
                except psycopg.DatabaseError as exc:
                    diag = exc.diag
                    message = diag.message_primary or str(exc)
                    if diag.message_detail:
                        message += f" ({diag.message_detail})"
                    raise DataProblems(
                        [Problem(diag.table_name, None, diag.column_name, message)]
                    ) from exc
                counts = {n: len(snapshot.get(n, [])) for n in sorted(given | cleared)}
                return LoadResult(
                    counts=counts,
                    replaced=sorted(given),
                    cleared=sorted(cleared),
                    snapshot=snapshot,
                    triggers=triggers,
                )


# --- shared by every engine ------------------------------------------------------------------


def convert_rows(
    schema: Schema, rows: Rows
) -> tuple[dict[str, list[dict[str, Any]]], list[Problem]]:
    """Each row checked against its table and converted for the database: unknown tables and
    columns, required values, types, duplicate primary keys."""
    problems: list[Problem] = []
    converted: dict[str, list[dict[str, Any]]] = {}

    def add(table, row, column, message) -> None:
        if len(problems) < MAX_PROBLEMS:
            problems.append(Problem(table, row, column, message))

    for tname, trows in rows.items():
        table = schema.tables.get(tname)
        if table is None:
            add(tname, None, None, f"unknown table {tname!r}")
            continue
        if not isinstance(trows, list):
            add(tname, None, None, "rows must be a list of objects")
            continue
        out: list[dict[str, Any]] = []
        seen_pk: dict[tuple, int] = {}
        for i, raw in enumerate(trows, start=1):
            if not isinstance(raw, dict):
                add(tname, i, None, "row must be an object")
                continue
            row: dict[str, Any] = {}
            for cname, value in raw.items():
                col = table.column(cname)
                if col is None:
                    add(tname, i, cname, f"unknown column {cname!r}")
                    continue
                if col.generated:
                    continue  # computed by the database; a snapshot carries it, ignore
                if value is None and not col.nullable:
                    add(tname, i, cname, "required (NOT NULL), got null")
                    continue
                try:
                    row[cname] = from_json(value, col)
                except ValueProblem as exc:
                    add(tname, i, cname, str(exc))
            for col in table.columns:
                if (
                    col.name not in raw
                    and not col.nullable
                    and not col.has_default
                    and not col.generated
                ):
                    add(tname, i, col.name, "required (NOT NULL) and missing")
            if table.primary_key and all(c in row for c in table.primary_key):
                pk = tuple(row[c] for c in table.primary_key)
                if pk in seen_pk:
                    add(tname, i, ",".join(table.primary_key),
                        f"duplicate primary key {pk!r} (also row {seen_pk[pk]})")  # fmt: skip
                else:
                    seen_pk[pk] = i
            out.append(row)
        converted[tname] = out
    return converted, problems


def needed_keys(
    schema: Schema, converted: dict, cleared: set[str]
) -> set[tuple[str, tuple[str, ...]]]:
    """(table, columns) whose existing keys the foreign key check must read from the database."""
    out = set()
    for tname in converted:
        for fk in schema.tables[tname].foreign_keys:
            if fk.ref_table not in converted and fk.ref_table not in cleared:
                out.add((fk.ref_table, tuple(fk.ref_columns)))
    return out


def fk_problems(schema: Schema, converted: dict, cleared: set[str], db_keys: dict) -> list[Problem]:
    """Foreign keys: against the rows given, against nothing for tables being cleared, and
    against the database (`db_keys`) for tables left alone."""
    problems: list[Problem] = []
    for tname, trows in converted.items():
        table = schema.tables[tname]
        for fk in table.foreign_keys:
            values = {
                tuple(r.get(c) for c in fk.columns): i
                for i, r in reversed(list(enumerate(trows, start=1)))
                if all(r.get(c) is not None for c in fk.columns)
            }
            if not values:
                continue
            ref_cols = tuple(fk.ref_columns)
            if fk.ref_table in converted:
                valid = {tuple(r.get(c) for c in ref_cols) for r in converted[fk.ref_table]}
                where = "in the rows given"
            elif fk.ref_table in cleared:
                valid = set()
                where = "(that table is cleared by this load)"
            else:
                valid = db_keys.get((fk.ref_table, ref_cols), set())
                where = "in the database"
            for value, i in sorted(values.items(), key=lambda kv: kv[1]):
                if value not in valid and len(problems) < MAX_PROBLEMS:
                    shown = value[0] if len(value) == 1 else value
                    problems.append(Problem(
                        tname, i, ",".join(fk.columns),
                        f"references {fk.ref_table}({', '.join(ref_cols)}) = {shown!r}, "
                        f"not found {where}",
                    ))  # fmt: skip
    return problems


def self_ordered(table: Table, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows of a self-referencing table, parents first where possible."""
    self_fks = [fk for fk in table.foreign_keys if fk.ref_table == table.name]
    if not self_fks or len(rows) < 2:
        return rows
    fk = self_fks[0]
    keyed = {tuple(r.get(c) for c in fk.ref_columns): r for r in rows}
    placed: set[tuple] = set()
    ordered: list[dict[str, Any]] = []
    pending = list(rows)
    while pending:
        progress = []
        for r in pending:
            parent = tuple(r.get(c) for c in fk.columns)
            if any(v is None for v in parent) or parent not in keyed or parent in placed:
                progress.append(r)
        if not progress:  # a cycle among rows: insert the rest as they are
            ordered += pending
            break
        for r in progress:
            placed.add(tuple(r.get(c) for c in fk.ref_columns))
        ordered += progress
        ids = {id(r) for r in progress}
        pending = [r for r in pending if id(r) not in ids]
    return ordered
