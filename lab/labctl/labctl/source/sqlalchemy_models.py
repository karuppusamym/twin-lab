"""SQLAlchemy declarative models -> tables, read with `ast`. The models are never imported."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

PY_TYPES = {
    "int": "INTEGER", "str": "TEXT", "bool": "BOOLEAN", "float": "DOUBLE PRECISION",
    "datetime": "TIMESTAMP", "date": "DATE", "time": "TIME", "Decimal": "NUMERIC",
    "bytes": "BLOB", "dict": "JSON", "list": "JSON",
}  # fmt: skip
SA_TYPES = {
    "Integer": "INTEGER", "BigInteger": "BIGINT", "SmallInteger": "SMALLINT", "Text": "TEXT",
    "Boolean": "BOOLEAN", "Date": "DATE", "Time": "TIME", "Float": "DOUBLE PRECISION",
    "LargeBinary": "BLOB", "JSON": "JSON", "Uuid": "UUID", "UUID": "UUID",
}  # fmt: skip


@dataclass
class Column:
    name: str
    type: str
    nullable: bool
    primary_key: bool = False
    unique: bool = False
    index: bool = False
    references: tuple[str, str] | None = None  # (table, column)
    autoincrement: bool = False  # the database numbers it when an insert leaves it out


@dataclass
class Table:
    name: str
    model: str
    file: Path | None
    line: int
    columns: list[Column] = field(default_factory=list)
    note: str | None = None  # where the table came from, when not from a source file

    @property
    def parents(self) -> list[str]:
        return [
            c.references[0] for c in self.columns if c.references and c.references[0] != self.name
        ]


def _const(node: ast.AST | None):
    return node.value if isinstance(node, ast.Constant) else None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _sql_type(node: ast.AST) -> str | None:
    name = _call_name(node)
    args = node.args if isinstance(node, ast.Call) else []
    kwargs = {k.arg: _const(k.value) for k in node.keywords} if isinstance(node, ast.Call) else {}
    if name in ("String", "Unicode", "VARCHAR", "NVARCHAR"):
        length = _const(args[0]) if args else kwargs.get("length")
        return f"VARCHAR({length})" if length else "VARCHAR"
    if name == "DateTime":
        return "TIMESTAMP WITH TIME ZONE" if kwargs.get("timezone") else "TIMESTAMP"
    if name == "Numeric":
        p = _const(args[0]) if args else kwargs.get("precision")
        s = _const(args[1]) if len(args) > 1 else kwargs.get("scale")
        return f"NUMERIC({p},{s})" if p and s is not None else "NUMERIC"
    return SA_TYPES.get(name or "")


def _mapped(annotation: ast.AST | None) -> tuple[str | None, bool]:
    """`Mapped[int | None]` -> ("int", True). Returns (python type name, nullable)."""
    if not (isinstance(annotation, ast.Subscript) and _call_name(annotation.value) == "Mapped"):
        return None, False
    inner = annotation.slice
    nullable = False
    if isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.BitOr):
        parts = [inner.left, inner.right]
        nullable = any(_const(p) is None and isinstance(p, ast.Constant) for p in parts)
        inner = next(p for p in parts if not (isinstance(p, ast.Constant) and p.value is None))
    elif isinstance(inner, ast.Subscript) and _call_name(inner.value) == "Optional":
        nullable, inner = True, inner.slice
    return _call_name(inner), nullable


def parse(path: Path, source: str | None = None) -> tuple[list[Table], list[str]]:
    """Tables declared in one Python file, and problems found reading them."""
    text = source if source is not None else path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [], [f"{path.name}: cannot parse ({exc.msg}, line {exc.lineno})"]
    tables: list[Table] = []
    problems: list[str] = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        name = next(
            (
                _const(s.value)
                for s in cls.body
                if isinstance(s, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets)
            ),
            None,
        )
        if not isinstance(name, str):
            continue
        table = Table(name=name, model=cls.name, file=path, line=cls.lineno)
        for stmt in cls.body:
            column = _column(stmt, table, problems)
            if column:
                table.columns.append(column)
        keys = [c for c in table.columns if c.primary_key]
        if len(keys) != 1:  # SQLAlchemy numbers only a single-column integer key
            for c in keys:
                c.autoincrement = False
        tables.append(table)
    return tables, problems


def _column(stmt: ast.stmt, table: Table, problems: list[str]) -> Column | None:
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        name, call, (py_type, nullable) = stmt.target.id, stmt.value, _mapped(stmt.annotation)
        if py_type is None:
            return None
    elif (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        name, call, py_type, nullable = stmt.targets[0].id, stmt.value, None, True
        if _call_name(call) != "Column":
            return None
    else:
        return None
    if isinstance(call, ast.Call) and _call_name(call) in ("relationship", "association_proxy"):
        return None
    if call is not None and not (
        isinstance(call, ast.Call) and _call_name(call) in ("mapped_column", "Column")
    ):
        return None
    args = call.args if isinstance(call, ast.Call) else []
    kwargs = {k.arg: k.value for k in call.keywords} if isinstance(call, ast.Call) else {}
    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        name, args = args[0].value, args[1:]  # explicit column name
    sql_type, references = None, None
    for arg in args:
        if _call_name(arg) == "ForeignKey" and arg.args:
            target = _const(arg.args[0])
            if isinstance(target, str) and "." in target:
                references = tuple(target.rsplit(".", 1))
        elif sql_type is None:
            sql_type = _sql_type(arg)
    sql_type = sql_type or PY_TYPES.get(py_type or "")
    if sql_type is None:
        problems.append(f"{table.name}.{name}: type not recognised; written as TEXT")
        sql_type = "TEXT"
    primary = _const(kwargs.get("primary_key")) is True
    numbered = (
        primary
        and sql_type in ("INTEGER", "BIGINT", "SMALLINT")
        and references is None
        and _const(kwargs.get("autoincrement")) is not False
    )
    if "nullable" in kwargs and isinstance(kwargs["nullable"], ast.Constant):
        nullable = bool(kwargs["nullable"].value)
    return Column(
        name=name,
        type=sql_type,
        nullable=nullable and not primary,
        primary_key=primary,
        unique=_const(kwargs.get("unique")) is True,
        index=_const(kwargs.get("index")) is True,
        references=references,
        autoincrement=numbered,
    )


def ordered(tables: list[Table]) -> list[Table]:
    """Parents before children; tables in a cycle keep their given order."""
    by_name = {t.name: t for t in tables}
    out: list[Table] = []
    seen: set[str] = set()

    def visit(table: Table, stack: set[str]) -> None:
        if table.name in seen or table.name in stack:
            return
        stack.add(table.name)
        for parent in table.parents:
            if parent in by_name:
                visit(by_name[parent], stack)
        seen.add(table.name)
        out.append(table)

    for table in tables:
        visit(table, set())
    return out


ENGINES = ("postgres", "sqlite", "mysql", "mssql", "oracle", "db2")
IDENTITY = {
    "postgres": " GENERATED BY DEFAULT AS IDENTITY",
    "oracle": " GENERATED BY DEFAULT AS IDENTITY",
    "db2": " GENERATED BY DEFAULT AS IDENTITY",
    "mssql": " IDENTITY(1,1)",
    "mysql": " AUTO_INCREMENT",
    "sqlite": "",  # an INTEGER primary key is numbered by SQLite itself
}
TYPES = {
    "postgres": {"BLOB": "BYTEA", "JSON": "JSONB"},
    "mssql": {"BOOLEAN": "BIT", "TEXT": "NVARCHAR(MAX)", "TIMESTAMP WITH TIME ZONE": "DATETIMEOFFSET",
              "TIMESTAMP": "DATETIME2", "DOUBLE PRECISION": "FLOAT", "BLOB": "VARBINARY(MAX)"},
    "oracle": {"TEXT": "CLOB", "BOOLEAN": "NUMBER(1)", "INTEGER": "NUMBER(10)",
               "BIGINT": "NUMBER(19)"},
    "mysql": {"TIMESTAMP WITH TIME ZONE": "DATETIME(6)", "TIMESTAMP": "DATETIME(6)"},
}  # fmt: skip


ALIASES = {"postgresql": "postgres", "sqlserver": "mssql", "sql server": "mssql"}

# Words that cannot be a bare table or column name in at least one engine the lab writes for
# (SQL:2016 reserved words and the ones PostgreSQL, SQL Server, SQLite, MySQL and Oracle add).
# A name in this list is always quoted, so one schema reads the same on every engine.
RESERVED = frozenset(
    """
abort absolute action add all allocate alter analyse analyze and any are array as asc assertion
asymmetric at authorization autoincrement backup begin between bigint binary bit blob boolean both
breadth browse bulk by call cascade cascaded case cast catalog char character check checkpoint
close clustered coalesce collate collation column comment commit compute concurrently condition
conflict date
connect connection constraint constraints contains containstable continue convert corresponding
create cross current current_date current_role current_time current_timestamp current_user cursor
database dbcc deallocate dec decimal declare default deferrable deferred delete deny depth desc
describe detach diagnostics disconnect disk distinct distributed do domain double drop dump each
else end end-exec errlvl escape except exception exclusive exec execute exists exit explain
external fail false fetch file fillfactor filter first float for force foreign found freetext
freetexttable freeze from full function get glob global go goto grant group having holdlock
identity identity_insert identitycol if ignore ilike immediate in index indexed initially inner
inout input insensitive insert instead int integer intersect interval into is isnull isolation
join key kill language last lateral leading left level like limit lineno load local localtime
localtimestamp lock long match merge minus mode modify module national natural nchar next no
nocheck nonclustered not notnull nowait null nullif number numeric of off offset offsets on only
open opendatasource openquery openrowset openxml option or order outer output over overlaps
partition percent pivot placing plan pragma precision prepare preserve primary print prior
privileges proc procedure public raise raiserror range read readtext reconfigure recursive
references regexp reindex relative release rename replace replication restore restrict return
returning revert revoke right rollback row rowcount rowguidcol rownum rows rule save savepoint
schema scroll second section securityaudit select semantickeyphrasetable session session_user
set setuser shutdown similar size smallint some space sql sqlcode sqlerror sqlstate statistics
symmetric system_user table tablesample temp temporary textsize then time timestamp to top
trailing tran transaction translate trigger true truncate try_convert tsequal union unique
unknown unpivot update updatetext usage use user using vacuum value values varchar variadic
varying verbose view virtual waitfor when whenever where while window with within without work
write writetext zone
""".split()
)
BARE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote(name: str, engine: str) -> str:
    """A table or column name as `engine` must write it: bare when it is a plain name (letters,
    digits, `_`) that no engine reserves, as the source wrote it, else quoted ("order" in
    PostgreSQL, SQLite and Oracle, [order] in SQL Server, `order` in MySQL), with any quote
    character inside doubled."""
    engine = ALIASES.get(engine, engine)
    if BARE.match(name) and name.lower() not in RESERVED:
        return name
    if engine == "mssql":
        return "[" + name.replace("]", "]]") + "]"
    if engine == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def ddl(tables: list[Table], header: str, engine: str = "sqlite") -> str:
    """CREATE TABLE statements for one engine. Names never change (quoted where the engine needs
    it); types and identity do."""
    engine = ALIASES.get(engine, engine)
    if engine not in ENGINES:
        raise ValueError(f"unknown database engine {engine!r}; one of {', '.join(ENGINES)}")
    types = TYPES.get(engine, {})

    def q(name: str) -> str:
        return quote(name, engine)

    lines = [f"-- {line}" if line else "--" for line in header.splitlines()]
    for table in ordered(tables):
        body = []
        for c in table.columns:
            kind = types.get(c.type, c.type)
            identity = IDENTITY[engine] if c.autoincrement else ""
            body.append(f"  {q(c.name)} {kind}{identity}{'' if c.nullable else ' NOT NULL'}")
        pk = [q(c.name) for c in table.columns if c.primary_key]
        if pk:
            body.append(f"  PRIMARY KEY ({', '.join(pk)})")
        for c in table.columns:
            if c.unique:
                body.append(f"  UNIQUE ({q(c.name)})")
        for c in table.columns:
            if c.references:
                body.append(f"  FOREIGN KEY ({q(c.name)}) REFERENCES {q(c.references[0])} "
                            f"({q(c.references[1])})")  # fmt: skip
        where = (
            f"{table.file.name}:{table.line}"
            if table.file
            else table.note or "declared only; column types assumed"
        )
        lines.append(f"\n-- {table.model}, {where}")
        lines.append(f"CREATE TABLE {q(table.name)} (\n" + ",\n".join(body) + "\n);")
        for c in table.columns:
            if c.index and not c.unique and not c.primary_key:
                index = q(f"ix_{table.name}_{c.name}")
                lines.append(f"CREATE INDEX {index} ON {q(table.name)} ({q(c.name)});")
    return "\n".join(lines) + "\n"
