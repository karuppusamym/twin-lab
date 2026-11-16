"""Subset spec and closure engine (Prod Echo I-13, I-14, I-15, I-16).

From seed rows, breadth-first over declared foreign keys plus hand-added associations:
- parents are always pulled (an insert without its parent fails);
- children are pulled only below seed rows and rows reached as children (not below a parent
  that was pulled for integrity, or one branch would pull every account of the bank), at most
  `closure.limits[child]` rows per parent row, and every cut is reported;
- cycles end because each row is visited once, keyed on (table, primary key).
"""

from __future__ import annotations

import base64
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import Field, field_validator, model_validator
from sqlalchemy import MetaData, and_, or_, select, text
from sqlalchemy import Table as SaTable
from sqlalchemy.engine import Connection

from labctl.models import Contract
from labctl.realdata import inventory
from labctl.realdata._db import RealDataError, connection

Rows = dict[str, list[dict[str, Any]]]
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CHUNK = 500


class SpecError(RealDataError):
    pass


# -- the subset spec (Prod Echo docs/04, one database) -------------------------------------------


class Seed(Contract):
    """Where the subset starts: key values of one table, or a WHERE with bound parameters."""

    table: str
    key: str | list[str] | None = Field(None, description="Default: the primary key.")
    values: list[Any] | None = None
    where: str | None = Field(None, description="SQL condition; values only as :params.")
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = Field(None, ge=1, description="Cap on rows a WHERE seed selects.")

    @model_validator(mode="after")
    def _one_kind(self) -> Seed:
        if (self.values is None) == (self.where is None):
            raise ValueError("a seed has either `values` or `where`, not both and not neither")
        if self.where is not None and re.search(r"'[^']*'", self.where):
            raise ValueError("literal strings in `where` are not allowed; use :params")
        if self.where is not None and re.search(r";|--|/\*", self.where):
            raise ValueError("`where` is one condition: no `;` and no comments")
        return self


class Association(Contract):
    """A relation the database does not declare: child.column holds parent.column_parent."""

    child: str
    column: str
    parent: str
    column_parent: str
    inferred_from: str | None = Field(None, description="load_trace, name, person ...")


class Entity(Contract):
    name: str
    table: str
    key: str
    side: Literal["read", "write"] = "read"
    preload: Literal["never", "starting_state"] | None = None


class Closure(Contract):
    associations: list[Association] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict, description="Max children per parent.")
    default_limit: int | None = Field(None, ge=0, description="For children not in `limits`.")
    exclude: list[str] = Field(default_factory=list, description="Child tables never followed.")
    max_rows: int = Field(100_000, ge=1, description="Stop (and report) past this many rows.")


class SubsetSpec(Contract):
    process: str | None = None
    version: str | date | None = None
    app: str | None = None
    db: str | None = Field(None, description="NAME of the env var holding the URL, never a URL.")
    entities: list[Entity] = Field(default_factory=list)
    seeds: list[Seed] = Field(min_length=1)
    closure: Closure = Field(default_factory=Closure)

    @field_validator("db")
    @classmethod
    def _env_name(cls, value: str | None) -> str | None:
        if value is not None and not ENV_NAME.match(value):
            raise ValueError("`db` names an environment variable; a URL never goes in a file")
        return value


# -- plan and result -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Relation:
    child: str
    child_columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]
    via: str  # "fk" or "association"


@dataclass
class Truncation:
    table: str
    parent: str
    parent_key: dict[str, Any]
    limit: int
    via: str

    def __str__(self) -> str:
        key = ", ".join(f"{k}={_json_safe(v)}" for k, v in self.parent_key.items())
        return f"{self.table}: limited to {self.limit} per {self.parent} ({key}) via {self.via}"


@dataclass
class ExtractPlan:
    spec: SubsetSpec
    tables: dict[str, inventory.DbTable]
    relations: list[Relation]
    queries: list[str] = field(default_factory=list)  # what will run, for review or another tool


@dataclass
class Extraction:
    rows: Rows
    truncated: list[Truncation] = field(default_factory=list)
    stopped_at_max_rows: bool = False

    @property
    def counts(self) -> dict[str, int]:
        return {t: len(r) for t, r in self.rows.items()}

    def report(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "truncated": [str(t) for t in self.truncated],
            "stopped_at_max_rows": self.stopped_at_max_rows,
        }


class Extractor(Protocol):
    """Prod Echo ADR-004: the pipeline does not care which extractor produced the rows."""

    def plan(self, spec: SubsetSpec) -> ExtractPlan: ...

    def run(self, plan: ExtractPlan) -> Extraction: ...


# -- helpers -------------------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def _resolver(names: list[str]):
    exact = {n: n for n in names}
    lower = {n.lower(): n for n in names}

    def resolve(name: str, what: str) -> str:
        found = exact.get(name) or lower.get(name.lower())
        if found is None:
            raise SpecError(f"unknown {what}: {name}")
        return found

    return resolve


def relations_of(tables: dict[str, inventory.DbTable], spec: SubsetSpec) -> list[Relation]:
    """Declared foreign keys plus the spec's associations, with names resolved."""
    resolve = _resolver(list(tables))
    rels = [
        Relation(
            t.name, tuple(fk.columns), resolve(fk.parent, "table"), tuple(fk.parent_columns), "fk"
        )
        for t in tables.values()
        for fk in inventory.foreign_keys(t)
        if fk.parent.lower() in {n.lower() for n in tables}
    ]
    for a in spec.closure.associations:
        child, parent = resolve(a.child, "table"), resolve(a.parent, "table")
        ccol = _resolver([c.name for c in tables[child].columns])(a.column, f"column of {child}")
        pcol = _resolver([c.name for c in tables[parent].columns])(
            a.column_parent, f"column of {parent}"
        )
        rel = Relation(child, (ccol,), parent, (pcol,), "association")
        if rel not in rels:
            rels.append(rel)
    return rels


# -- the own extractor ---------------------------------------------------------------------------


class OwnExtractor:
    """The closure engine over SQLAlchemy, read-only."""

    def __init__(self, url: str) -> None:
        self._url = url

    def __repr__(self) -> str:  # never show the URL
        return "OwnExtractor(<url hidden>)"

    def plan(self, spec: SubsetSpec) -> ExtractPlan:
        with connection(self._url) as conn:
            return self._plan(conn, spec)

    def run(self, plan: ExtractPlan) -> Extraction:
        with connection(self._url) as conn:
            return _Walk(conn, plan).run()

    def _plan(self, conn: Connection, spec: SubsetSpec) -> ExtractPlan:
        tables = {t.name: t for t in inventory.read(conn)}
        rels = relations_of(tables, spec)
        resolve = _resolver(list(tables))
        queries = []
        for seed in spec.seeds:
            name = resolve(seed.table, "table")
            if seed.where is not None:
                queries.append(f"seed: SELECT * FROM {name} WHERE {seed.where}")
            else:
                keys = _seed_key(tables[name], seed)
                queries.append(f"seed: SELECT * FROM {name} WHERE ({', '.join(keys)}) IN (:values)")
        for rel in rels:
            pc, cc = ", ".join(rel.parent_columns), ", ".join(rel.child_columns)
            queries.append(
                f"parent: SELECT * FROM {rel.parent} WHERE ({pc}) IN (keys from {rel.child}.{cc})"
            )
            if rel.child.lower() in {e.lower() for e in spec.closure.exclude}:
                continue
            limit = _limit(spec, rel.child)
            per = f" LIMIT {limit} per parent" if limit is not None else ""
            queries.append(
                f"child: SELECT * FROM {rel.child} WHERE ({cc}) IN (keys of {rel.parent}){per}"
                f" [{rel.via}]"
            )
        for name in spec.closure.limits:
            resolve(name, "table in closure.limits")
        return ExtractPlan(spec=spec, tables=tables, relations=rels, queries=queries)


def _limit(spec: SubsetSpec, table: str) -> int | None:
    for name, value in spec.closure.limits.items():
        if name.lower() == table.lower():
            return value
    return spec.closure.default_limit


def _seed_key(table: inventory.DbTable, seed: Seed) -> list[str]:
    cols = _resolver([c.name for c in table.columns])
    if seed.key is None:
        if not table.primary_key:
            raise SpecError(f"seed on {table.name}: no primary key, name `key`")
        return table.primary_key
    keys = [seed.key] if isinstance(seed.key, str) else seed.key
    return [cols(k, f"column of {table.name}") for k in keys]


class _Walk:
    def __init__(self, conn: Connection, plan: ExtractPlan) -> None:
        self.conn = conn
        self.plan = plan
        self.spec = plan.spec
        self.meta = MetaData()
        self.sa: dict[str, SaTable] = {}
        self.rows: dict[str, dict[tuple, dict[str, Any]]] = defaultdict(dict)
        self.down: set[tuple[str, tuple]] = set()
        self.truncated: list[Truncation] = []
        self.total = 0
        self.full = False
        self.excluded = {e.lower() for e in self.spec.closure.exclude}
        self.parents_of: dict[str, list[Relation]] = defaultdict(list)
        self.children_of: dict[str, list[Relation]] = defaultdict(list)
        for rel in plan.relations:
            self.parents_of[rel.child].append(rel)
            if rel.child.lower() not in self.excluded:
                self.children_of[rel.parent].append(rel)

    def table(self, name: str) -> SaTable:
        if name not in self.sa:
            schema = self.plan.tables[name].schema
            self.sa[name] = SaTable(name, self.meta, autoload_with=self.conn, schema=schema)
        return self.sa[name]

    def ident(self, name: str, row: dict[str, Any]) -> tuple:
        pk = self.plan.tables[name].primary_key or list(row)
        return tuple(row[c] for c in pk)

    def order(self, name: str):
        t = self.table(name)
        pk = self.plan.tables[name].primary_key or [c.name for c in t.columns]
        return [t.c[c] for c in pk]

    def fetch(self, name: str, columns: tuple[str, ...], keys: list[tuple], limit=None):
        t = self.table(name)
        for i in range(0, len(keys), CHUNK):
            chunk = keys[i : i + CHUNK]
            if len(columns) == 1:
                cond = t.c[columns[0]].in_([k[0] for k in chunk])
            else:
                cond = or_(
                    *[and_(*[t.c[c] == v for c, v in zip(columns, k, strict=True)]) for k in chunk]
                )
            stmt = select(t).where(cond).order_by(*self.order(name))
            if limit is not None:
                stmt = stmt.limit(limit)
            yield from (dict(r._mapping) for r in self.conn.execute(stmt))

    def add(self, name: str, row: dict[str, Any], down: bool, queue: list) -> None:
        key = self.ident(name, row)
        if key in self.rows[name]:
            if down and (name, key) not in self.down:
                self.down.add((name, key))
                queue.append((name, key))
            return
        if self.total >= self.spec.closure.max_rows:
            self.full = True
            return
        self.rows[name][key] = row
        self.total += 1
        if down:
            self.down.add((name, key))
        queue.append((name, key))

    def seeds(self, queue: list) -> None:
        resolve = _resolver(list(self.plan.tables))
        for seed in self.spec.seeds:
            name = resolve(seed.table, "table")
            t = self.table(name)
            if seed.where is not None:
                stmt = select(t).where(text(seed.where)).order_by(*self.order(name))
                if seed.limit:
                    stmt = stmt.limit(seed.limit)
                found = [dict(r._mapping) for r in self.conn.execute(stmt, seed.params)]
            else:
                cols = tuple(_seed_key(self.plan.tables[name], seed))
                keys = [
                    tuple(v) if isinstance(v, list | tuple) else (v,) for v in seed.values or []
                ]
                found = list(self.fetch(name, cols, keys))
            for row in found:
                self.add(name, row, True, queue)

    def run(self) -> Extraction:
        queue: list[tuple[str, tuple]] = []
        self.seeds(queue)
        while queue and not self.full:
            layer, queue = queue, []
            by_table: dict[str, list[tuple]] = defaultdict(list)
            for name, key in layer:
                by_table[name].append(key)
            for name, keys in by_table.items():
                rows = [self.rows[name][k] for k in keys]
                for rel in self.parents_of[name]:
                    wanted = {tuple(r[c] for c in rel.child_columns) for r in rows}
                    wanted = sorted(
                        (w for w in wanted if None not in w), key=lambda w: tuple(map(str, w))
                    )
                    for parent in self.fetch(rel.parent, rel.parent_columns, wanted):
                        self.add(rel.parent, parent, False, queue)
                down_rows = [r for k, r in zip(keys, rows, strict=True) if (name, k) in self.down]
                for rel in self.children_of[name]:
                    self.children(rel, down_rows, queue)
        rows: Rows = {}
        for name in self.plan.tables:
            if self.rows.get(name):
                rows[name] = [
                    {k: _json_safe(v) for k, v in r.items()} for r in self.rows[name].values()
                ]
        return Extraction(rows=rows, truncated=self.truncated, stopped_at_max_rows=self.full)

    def children(self, rel: Relation, parents: list[dict[str, Any]], queue: list) -> None:
        values = [tuple(p[c] for c in rel.parent_columns) for p in parents]
        values = [v for v in dict.fromkeys(values) if None not in v]
        if not values:
            return
        limit = _limit(self.spec, rel.child)
        if limit is None:
            for child in self.fetch(rel.child, rel.child_columns, values):
                self.add(rel.child, child, True, queue)
            return
        for value in values:
            found = list(self.fetch(rel.child, rel.child_columns, [value], limit + 1))
            if len(found) > limit:
                self.truncated.append(
                    Truncation(
                        table=rel.child,
                        parent=rel.parent,
                        parent_key=dict(zip(rel.parent_columns, value, strict=True)),
                        limit=limit,
                        via=f"{rel.via} {rel.child}.{','.join(rel.child_columns)}",
                    )
                )
            for child in found[:limit]:
                self.add(rel.child, child, True, queue)


# -- entry points --------------------------------------------------------------------------------


def extract_with_report(
    url: str, spec: SubsetSpec, extractor: Extractor | None = None
) -> Extraction:
    ex = extractor or OwnExtractor(url)
    return ex.run(ex.plan(spec))


def extract(url: str, spec: SubsetSpec) -> Rows:
    """The closure of the spec's seeds as `Rows` (JSON-safe values)."""
    return extract_with_report(url, spec).rows


@dataclass
class MissingTable:
    """A table a screen reads that the closure did not reach, with guessed associations."""

    table: str
    candidates: list[Association] = field(default_factory=list)


def _stem(name: str) -> str:
    n = name.lower()
    for suffix in ("ies", "es", "s"):
        if n.endswith(suffix) and len(n) > len(suffix) + 2:
            return n[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return n


def missing_relations(url: str, spec: SubsetSpec, observed_tables: list[str]) -> list[MissingTable]:
    """App-load trace diff (I-15, simplified): tables read but not in the closure.

    For each, candidate associations: a column whose name starts with a reached table's stem
    (`customer_ref` -> `customers`) and whose values hit keys the closure pulled.
    """
    with connection(url) as conn:
        plan = OwnExtractor(url)._plan(conn, spec)
        result = _Walk(conn, plan).run()
        reached = {t.lower() for t in result.rows}
        resolve = _resolver(list(plan.tables))
        out: list[MissingTable] = []
        for observed in dict.fromkeys(observed_tables):
            try:
                name = resolve(observed, "table")
            except SpecError:
                out.append(MissingTable(table=observed))
                continue
            if name.lower() in reached:
                continue
            out.append(MissingTable(table=name, candidates=_guess(conn, plan, result, name)))
        return out


def _guess(
    conn: Connection, plan: ExtractPlan, result: Extraction, child: str
) -> list[Association]:
    walk = _Walk(conn, plan)
    found = []
    for parent, rows in result.rows.items():
        pk = plan.tables[parent].primary_key
        if len(pk) != 1 or parent == child:
            continue
        stem = _stem(parent)
        keys = [(r[pk[0]],) for r in rows]
        pk_type = next(c.type for c in plan.tables[parent].columns if c.name == pk[0])
        for col in plan.tables[child].columns:
            low = col.name.lower()
            if col.primary_key or not (low.startswith(stem) or low.startswith(stem[:4] + "_")):
                continue
            if _numeric(col.type) != _numeric(pk_type):
                continue  # comparing text with numbers fails on strict databases
            if list(walk.fetch(child, (col.name,), keys, limit=1)):
                found.append(
                    Association(
                        child=child,
                        column=col.name,
                        parent=parent,
                        column_parent=pk[0],
                        inferred_from="load_trace",
                    )
                )
    return found


def _numeric(sql_type: str) -> bool:
    return any(t in sql_type.upper() for t in ("INT", "NUM", "DEC", "SERIAL", "NUMBER"))
