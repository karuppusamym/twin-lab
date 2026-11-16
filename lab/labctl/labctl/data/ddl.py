"""Table definitions from DDL: what `labctl brief` writes and real schema dumps.

Reads `CREATE TABLE`, `ALTER TABLE ... ADD CONSTRAINT` and `CREATE INDEX` from Postgres (pg_dump),
SQL Server (SSMS scripts with `GO`), Oracle, MySQL and SQLite. Types are normalised to the names
`labctl.source.sqlalchemy_models` uses. Statements that do not describe tables are skipped; any
that cannot be read are skipped too and reported as problems. Nothing here raises on bad input.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from labctl.appmodel import AppModel, Evidence
from labctl.appmodel import Column as ModelColumn
from labctl.appmodel import Table as ModelTable
from labctl.source.sqlalchemy_models import Column, Table

__all__ = ["parse", "parse_with_problems", "to_model", "normalise_type"]


# --- lexer -------------------------------------------------------------------------------------


@dataclass
class Tok:
    kind: str  # word, ident (quoted), string, number, punct, group, sep
    value: Any  # text; for a group, the list of tokens inside the parentheses
    line: int
    first: bool = False  # first token on its line

    @property
    def up(self) -> str | None:
        return self.value.upper() if self.kind == "word" else None

    @property
    def name(self) -> str | None:
        """The identifier this token spells, quoted or not."""
        return self.value if self.kind in ("word", "ident") else None


_WORD = re.compile(r"[A-Za-z_À-￿@#][\w$#@À-￿]*")
_NUMBER = re.compile(r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?")
_DOLLAR = re.compile(r"\$([A-Za-z_]\w*)?\$")
_SEP_LINE = re.compile(r"(?:GO(?:[ \t]+\d+)?|/)[ \t]*(?=\r?\n|$)", re.IGNORECASE)
_COPY_DATA = re.compile(
    r"^(COPY\b[^;]*\bFROM\s+stdin\s*;)[^\n]*\n(.*?)^\\\.[ \t]*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _drop_copy_data(text: str) -> str:
    """pg_dump `COPY ... FROM stdin;` blocks carry rows; keep the statement, blank the rows."""
    return _COPY_DATA.sub(lambda m: m.group(1) + "\n" * (m.group(0).count("\n")), text)


def _lex(text: str, mysql: bool) -> list[Tok]:
    out: list[Tok] = []
    i, n, line = 0, len(text), 1
    bol = True  # only whitespace since the last newline
    last_line = 0

    def add(kind: str, value: Any, at: int) -> None:
        nonlocal last_line
        out.append(Tok(kind, value, at, first=at != last_line))
        last_line = at

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            bol = True
            i += 1
            continue
        if c in " \t\r\f\v\ufeff":
            i += 1
            continue
        if bol:
            m = _SEP_LINE.match(text, i)
            if m:
                add("sep", m.group(0).strip(), line)
                i = m.end()
                continue
            if c == "\\":  # psql meta-command such as \connect
                while i < n and text[i] != "\n":
                    i += 1
                continue
        bol = False
        if text.startswith("--", i) or (mysql and c == "#"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            i = end
            continue
        if c in "NnEeXxBbUu" and i + 1 < n and text[i + 1] == "'":
            i += 1
            c = "'"
        if c == "'":
            start_line = line
            j, buf = i + 1, []
            while j < n:
                ch = text[j]
                if ch == "\\" and mysql and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if ch == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                if ch == "\n":
                    line += 1
                buf.append(ch)
                j += 1
            add("string", "".join(buf), start_line)
            i = j + 1
            continue
        if c in '"`[':
            close = {'"': '"', "`": "`", "[": "]"}[c]
            j, buf = i + 1, []
            while j < n:
                if text[j] == close:
                    if j + 1 < n and text[j + 1] == close:
                        buf.append(close)
                        j += 2
                        continue
                    break
                if text[j] == "\n":
                    line += 1
                buf.append(text[j])
                j += 1
            add("ident", "".join(buf), line)
            i = j + 1
            continue
        if c == "$":
            m = _DOLLAR.match(text, i)
            if m:
                end = text.find(m.group(0), m.end())
                end = n if end < 0 else end
                add("string", text[m.end() : end], line)
                line += text.count("\n", i, end)
                i = min(n, end + len(m.group(0)))
                continue
        if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUMBER.match(text, i)
            add("number", m.group(0), line)
            i = m.end()
            continue
        m = _WORD.match(text, i)
        if m:
            add("word", m.group(0), line)
            i = m.end()
            continue
        add("punct", c, line)
        i += 1
    return out


_BLOCK_START = {"TRIGGER", "PROCEDURE", "PROC", "FUNCTION", "PACKAGE", "TYPE"}
_LINE_STARTS = {"CREATE", "ALTER"}


def _is_block_start(toks: list[Tok], i: int) -> bool:
    """CREATE [OR REPLACE] [EDITIONABLE] TRIGGER|PROCEDURE|...: a body with its own semicolons."""
    if toks[i].up != "CREATE":
        return False
    for t in toks[i + 1 : i + 5]:
        if t.up in _BLOCK_START:
            return True
        if t.up not in ("OR", "REPLACE", "EDITIONABLE", "NONEDITIONABLE", "AND", "COMPILE"):
            return False
    return False


def _statements(toks: list[Tok]) -> list[list[Tok]]:
    """Split on `;`, `GO` and `/` lines, and before CREATE/ALTER starting a line at depth 0."""
    has_seps = any(t.kind == "sep" for t in toks)
    out: list[list[Tok]] = []
    cur: list[Tok] = []
    depth = 0
    block = False
    for i, t in enumerate(toks):
        if not cur and has_seps and _is_block_start(toks, i):
            block = True
        if t.kind == "sep":
            if cur:
                out.append(cur)
            cur, depth, block = [], 0, False
            continue
        if block:
            cur.append(t)
            continue
        if t.kind == "punct" and t.value == ";":  # even inside unbalanced brackets
            if cur:
                out.append(cur)
            cur, depth = [], 0
            continue
        if depth == 0 and cur and t.first and t.up in _LINE_STARTS:
            out.append(cur)
            cur = []
            if has_seps and _is_block_start(toks, i):
                block = True
        if t.kind == "punct" and t.value == "(":
            depth += 1
        elif t.kind == "punct" and t.value == ")":
            depth -= 1
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def _nest(toks: list[Tok]) -> list[Tok] | None:
    """Turn `( ... )` into group tokens. None when the parentheses do not balance."""
    stack: list[list[Tok]] = [[]]
    opened: list[Tok] = []
    for t in toks:
        if t.kind == "punct" and t.value == "(":
            opened.append(t)
            stack.append([])
        elif t.kind == "punct" and t.value == ")":
            if len(stack) == 1:
                return None
            inner = stack.pop()
            start = opened.pop()
            stack[-1].append(Tok("group", inner, start.line, start.first))
        else:
            stack[-1].append(t)
    return stack[0] if len(stack) == 1 else None


def _split(items: list[Tok]) -> list[list[Tok]]:
    """Split a token list at top-level commas."""
    parts: list[list[Tok]] = [[]]
    for t in items:
        if t.kind == "punct" and t.value == ",":
            parts.append([])
        else:
            parts[-1].append(t)
    return [p for p in parts if p]


def _up(toks: list[Tok], i: int) -> str | None:
    return toks[i].up if 0 <= i < len(toks) else None


def _qualified(toks: list[Tok], i: int) -> tuple[str | None, int]:
    """`a.b."c"` at i -> ("c", index after it)."""
    if i >= len(toks) or toks[i].name is None:
        return None, i
    name = toks[i].name
    i += 1
    while (
        i + 1 < len(toks)
        and toks[i].kind == "punct"
        and toks[i].value == "."
        and toks[i + 1].name is not None
    ):
        name = toks[i + 1].name
        i += 2
    return name, i


def _first_group(toks: list[Tok], i: int) -> tuple[list[Tok] | None, int]:
    while i < len(toks):
        if toks[i].kind == "group":
            return toks[i].value, i + 1
        i += 1
    return None, i


def _column_list(group: list[Tok]) -> list[str | None]:
    """`(a ASC, "b"(10), lower(c))` -> ["a", "b", None]; None for an expression."""
    names: list[str | None] = []
    for part in _split(group):
        first = part[0]
        if first.name is None or (len(part) > 1 and part[1].kind == "group" and first.kind == "word"
                                  and first.up not in ("ASC", "DESC") and _is_function(part)):  # fmt: skip
            names.append(None)
        else:
            names.append(first.name)
    return names


def _is_function(part: list[Tok]) -> bool:
    """`lower(c)` is an expression; MySQL `name(10)` is a prefix length on a column."""
    inner = part[1].value
    return not (len(inner) == 1 and inner[0].kind == "number")


def _contains(toks: list[Tok], word: str) -> bool:
    for t in toks:
        if t.kind == "group":
            if _contains(t.value, word):
                return True
        elif t.name is not None and t.name.upper() == word:
            return True
    return False


# --- types -------------------------------------------------------------------------------------

_INTEGER = {"INT", "INTEGER", "INT4", "MEDIUMINT", "PLS_INTEGER", "BINARY_INTEGER", "YEAR"}
_BIGINT = {"BIGINT", "INT8"}
_SMALLINT = {"SMALLINT", "INT2"}
_SERIAL = {"SERIAL": "INTEGER", "SERIAL4": "INTEGER", "BIGSERIAL": "BIGINT", "SERIAL8": "BIGINT",
           "SMALLSERIAL": "SMALLINT", "SERIAL2": "SMALLINT"}  # fmt: skip
_CHAR = {"CHAR", "NCHAR", "CHARACTER", "BPCHAR", "NATIONAL"}
_VARCHAR = {"VARCHAR", "NVARCHAR", "VARCHAR2", "NVARCHAR2", "STRING", "VARYING"}
_TEXT = {"TEXT", "NTEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT", "CLOB", "NCLOB", "DBCLOB",
         "CITEXT", "LONG", "XML", "XMLTYPE", "SYSNAME", "SET", "NAME"}  # fmt: skip
_NUMERIC = {"NUMERIC", "DECIMAL", "DEC", "NUMBER", "FIXED"}
_DOUBLE = {"FLOAT", "FLOAT4", "FLOAT8", "REAL", "DOUBLE", "BINARY_FLOAT", "BINARY_DOUBLE"}
_TIMESTAMP = {"DATETIME", "DATETIME2", "SMALLDATETIME"}
_TSTZ = {"TIMESTAMPTZ", "DATETIMEOFFSET"}
_TIME = {"TIME", "TIMETZ"}
_BLOB = {"BYTEA", "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB", "BINARY", "VARBINARY", "IMAGE",
         "RAW", "BFILE", "GRAPHIC", "VARGRAPHIC"}  # fmt: skip
_JSON = {"JSON", "JSONB"}
_UUID = {"UUID", "UNIQUEIDENTIFIER"}
_BOOL = {"BOOLEAN", "BOOL"}
_OTHER = {"BIT", "TINYINT", "MONEY", "SMALLMONEY", "DATE", "TIMESTAMP", "ENUM", "INTERVAL",
          "ROWVERSION"}  # fmt: skip
TYPE_WORDS = (
    _INTEGER | _BIGINT | _SMALLINT | set(_SERIAL) | _CHAR | _VARCHAR | _TEXT | _NUMERIC | _DOUBLE
    | _TIMESTAMP | _TSTZ | _TIME | _BLOB | _JSON | _UUID | _BOOL | _OTHER
)  # fmt: skip
_TYPE_CONT = {"VARYING", "PRECISION", "UNSIGNED", "SIGNED", "ZEROFILL", "RAW", "LARGE", "OBJECT",
              "CHARACTER", "CHAR", "VARCHAR", "WITHOUT", "TIME", "ZONE", "LOCAL"}  # fmt: skip


@dataclass
class _Type:
    type: str
    identity: bool = False  # serial
    unreadable: bool = False  # not insertable (rowversion)
    note: str | None = None  # problem to report


def _args(group: list[Tok] | None) -> tuple[list[int], bool, list[str]]:
    """(numbers, MAX given, strings) inside a type's parentheses."""
    if not group:
        return [], False, []
    nums, has_max, strings = [], False, []
    for t in group:
        if t.kind == "number":
            nums.append(int(float(t.value)))
        elif t.up == "MAX":
            has_max = True
        elif t.kind == "punct" and t.value == "*":
            nums.append(38)
        elif t.kind == "string":
            strings.append(t.value)
    return nums, has_max, strings


def normalise_type(
    parts: list[str],
    group: list[Tok] | None = None,
    dialect: str = "postgres",
    aliases: dict[str, str] | None = None,
) -> _Type:
    """Words of a declared type (and its parentheses) -> the sqlalchemy_models type name."""
    words = [p.upper() for p in parts if p.upper() not in ("UNSIGNED", "SIGNED", "ZEROFILL")]
    spelled = " ".join(parts) + (f"({','.join(str(t.value) for t in group)})" if group else "")
    if not words:
        return _Type("TEXT", note="no type given; treated as TEXT")
    nums, has_max, strings = _args(group)
    first = words[0]
    joined = " ".join(words)
    tz = "WITH TIME ZONE" in joined or "WITH LOCAL TIME ZONE" in joined
    if aliases and parts[0].lower() in aliases and len(words) == 1:
        return _Type(aliases[parts[0].lower()])
    if first in _SERIAL:
        return _Type(_SERIAL[first], identity=True)
    if first in _INTEGER:
        return _Type("INTEGER")
    if first in _BIGINT:
        return _Type("BIGINT")
    if first in _SMALLINT:
        return _Type("SMALLINT")
    if first == "TINYINT":
        return _Type("BOOLEAN" if dialect == "mysql" and nums[:1] == [1] else "SMALLINT")
    if first in _BOOL:
        return _Type("BOOLEAN")
    if first == "BIT":
        if "VARYING" in words or (nums and nums[0] > 1):
            return _Type("TEXT", note=f"bit string type {spelled} treated as TEXT")
        return _Type("BOOLEAN")
    if first in _UUID:
        return _Type("UUID")
    if first in _JSON:
        return _Type("JSON")
    if "LARGE" in words and "OBJECT" in words:
        return _Type("BLOB" if first in ("BINARY", "BLOB") else "TEXT")
    if first in _BLOB or joined == "LONG RAW" or "VARBINARY" in words:
        return _Type("BLOB")
    if first in _TEXT and not (first == "LONG" and "RAW" in words):
        return _Type("TEXT")
    if first in _CHAR or first in _VARCHAR:
        varying = any(w in _VARCHAR or w == "VARYING" for w in words)
        if has_max:
            return _Type("TEXT")
        if nums:
            return _Type(f"VARCHAR({nums[0]})")
        return _Type("VARCHAR" if varying else "VARCHAR(1)")
    if first == "ENUM":
        longest = max((len(s) for s in strings), default=1)
        return _Type(f"VARCHAR({longest})")
    if first in ("MONEY", "SMALLMONEY"):
        return _Type("NUMERIC(19,4)" if first == "MONEY" else "NUMERIC(10,4)")
    if first in _NUMERIC:
        precision = nums[0] if nums else None
        scale = nums[1] if len(nums) > 1 else 0
        if precision is None:
            return _Type("NUMERIC")
        if scale == 0 and first == "NUMBER":  # Oracle spells every integer as NUMBER(p)
            if precision == 1:
                return _Type("BOOLEAN")
            if precision <= 10:
                return _Type("INTEGER")
            if precision <= 19:
                return _Type("BIGINT")
        return _Type(f"NUMERIC({precision},{scale})")
    if first in _DOUBLE:
        return _Type("DOUBLE PRECISION")
    if first == "DATE":
        return _Type("DATE")
    if first in _TSTZ:
        return _Type("TIMESTAMP WITH TIME ZONE")
    if first in _TIMESTAMP:
        return _Type("TIMESTAMP")
    if first == "ROWVERSION" or (first == "TIMESTAMP" and dialect == "mssql"):
        return _Type("BLOB", unreadable=True)
    if first == "TIMESTAMP":
        return _Type("TIMESTAMP WITH TIME ZONE" if tz else "TIMESTAMP")
    if first in _TIME:
        return _Type("TIME")
    return _Type("TEXT", note=f"type {spelled} not recognised; treated as TEXT")


# --- statements --------------------------------------------------------------------------------

_SILENT = {
    "SET", "SELECT", "USE", "BEGIN", "COMMIT", "END", "START", "ROLLBACK", "GRANT", "REVOKE",
    "COMMENT", "LOCK", "UNLOCK", "DROP", "PRAGMA", "PRINT", "EXEC", "EXECUTE", "DECLARE",
    "ANALYZE", "VACUUM", "REFRESH", "TRUNCATE", "SAVEPOINT", "RELEASE", "WHENEVER", "SPOOL",
    "CONNECT", "DELIMITER", "SHOW", "FLUSH", "CHECKPOINT", "DBCC", "RAISERROR", "PROMPT",
    "REM", "REMARK", "EXIT", "QUIT",
}  # fmt: skip
_ROWS = {"INSERT", "COPY", "MERGE", "UPDATE", "DELETE", "REPLACE", "UPSERT"}
_CREATE_SILENT = {"SEQUENCE", "SCHEMA", "EXTENSION", "DATABASE", "ROLE", "USER", "LOGIN",
                  "TABLESPACE", "STATISTICS", "COLLATION", "PUBLICATION", "SUBSCRIPTION",
                  "SERVER", "LANGUAGE", "DIRECTORY", "PROFILE", "CONTEXT"}  # fmt: skip
_CREATE_OTHER = {"VIEW", "MATERIALIZED", "FUNCTION", "PROCEDURE", "PROC", "TRIGGER", "PACKAGE",
                 "SYNONYM", "RULE", "POLICY", "EVENT", "AGGREGATE", "OPERATOR", "CAST",
                 "TEXT", "FOREIGN", "ASSEMBLY", "FULLTEXT", "XML"}  # fmt: skip
_OBJECTS = {"TABLE", "INDEX", "TYPE", "DOMAIN"} | _CREATE_SILENT | _CREATE_OTHER
_VIEW_KEYWORDS = {"ON", "WHERE", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS",
                  "GROUP", "ORDER", "HAVING", "UNION", "WITH", "LIMIT", "AS", "FROM", "USING"}  # fmt: skip
_NOT_TYPES = {"NOT", "NULL", "PRIMARY", "UNIQUE", "REFERENCES", "DEFAULT", "CHECK", "CONSTRAINT",
              "GENERATED", "COLLATE"}  # fmt: skip
_TABLE_CONSTRAINT = {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "EXCLUDE", "LIKE",
                     "PERIOD", "FULLTEXT", "SPATIAL"}  # fmt: skip


@dataclass
class _Col:
    name: str
    type: str | None
    notnull: bool = False
    nullable_said: bool = False
    primary: bool = False
    unique: bool = False
    identity: bool = False
    references: tuple[str, str | None] | None = None
    computed: bool = False
    unreadable: bool = False


class _Parser:
    def __init__(self, text: str):
        head = text[:4000]
        said = re.search(r"Dialect:\s*([A-Za-z0-9]+)", head)
        self.dialect = said.group(1).lower() if said else _guess_dialect(text)
        self.tables: dict[str, Table] = {}
        self.refs: dict[tuple[str, str], tuple[str, str | None]] = {}
        self.aliases: dict[str, str] = {}  # enum and domain types -> our type
        self.problems: list[str] = []
        self.views: list[tuple[list[Tok], int, int]] = []  # read after every table is known
        self.row_statements = 0
        self.text = _drop_copy_data(text)

    # -- driver --

    def run(self) -> tuple[list[Table], list[str]]:
        for stmt in _statements(_lex(self.text, self.dialect == "mysql")):
            line = stmt[0].line
            nested = _nest(stmt)
            if nested is None:
                self.problem(line, f"skipped: brackets do not balance in {_preview(stmt)}")
                continue
            try:
                self.statement(nested, line)
            except Exception as exc:  # never let one odd statement stop the import
                self.problem(line, f"skipped: could not read {_preview(stmt)} ({exc})")
        for toks, i, line in self.views:
            try:
                self.create_view(toks, i, line)
            except Exception as exc:  # noqa: BLE001
                self.problem(line, f"skipped: could not read view {_preview(toks)} ({exc})")
        if self.row_statements:
            self.problems.append(
                f"{self.row_statements} statement(s) with rows (INSERT, COPY) skipped: "
                "only table definitions are read from a schema file"
            )
        return self.finish(), self.problems

    def problem(self, line: int, message: str) -> None:
        self.problems.append(f"line {line}: {message}")

    def statement(self, toks: list[Tok], line: int) -> None:
        first = toks[0].up
        if first == "CREATE":
            self.create(toks, line)
        elif first == "ALTER":
            if _up(toks, 1) == "TABLE":
                self.alter_table(toks, line)
            elif _up(toks, 1) not in ("SEQUENCE", "SCHEMA", "DATABASE", "TYPE", "FUNCTION",
                                      "PROCEDURE", "VIEW", "INDEX", "ROLE", "USER", "DEFAULT",
                                      "EXTENSION", "SESSION", "SYSTEM", "TABLESPACE",
                                      "AUTHORIZATION", "LOGIN", "TRIGGER", "PACKAGE",
                                      "MATERIALIZED", "DOMAIN", "PUBLICATION"):  # fmt: skip
                self.problem(line, f"skipped: {_preview(toks)} not understood")
        elif first in _ROWS:
            self.row_statements += 1
        elif first in _SILENT:
            pass
        else:
            self.problem(line, f"skipped: {_preview(toks)} not understood")

    # -- CREATE --

    def create(self, toks: list[Tok], line: int) -> None:
        i = next((k for k in range(1, min(len(toks), 12)) if toks[k].up in _OBJECTS), len(toks))
        flags = {t.up for t in toks[1:i] if t.up}
        what = toks[i].up if i < len(toks) else None
        if what == "TABLE":
            if flags & {"TEMPORARY", "TEMP", "PRIVATE"}:
                self.problem(line, "skipped: temporary table")
                return
            self.create_table(toks, i + 1, line)
        elif what == "INDEX":
            self.create_index(toks, i + 1, "UNIQUE" in flags, line)
        elif what == "TYPE":
            self.create_type(toks, i + 1, line)
        elif what == "DOMAIN":
            self.create_domain(toks, i + 1, line)
        elif what in _CREATE_SILENT:
            pass
        elif what == "VIEW" or (what == "MATERIALIZED" and _up(toks, i + 1) == "VIEW"):
            self.views.append((toks, i + 1 if what == "VIEW" else i + 2, line))
        elif what in _CREATE_OTHER:
            name, _ = _qualified(toks, i + 1 if what != "MATERIALIZED" else i + 2)
            if what in ("PROCEDURE", "PROC", "FUNCTION", "TRIGGER", "PACKAGE"):
                self.problem(line, f"{what.lower()} {name or ''} is not created in the demo "
                             "database: its logic is not copied. A bot that calls it directly "
                             "needs it written by hand, or the app's API instead")  # fmt: skip
            else:
                self.problem(line, f"skipped: CREATE {what} {name or ''}".rstrip() + " (only "
                             "tables and views are read)")  # fmt: skip
        else:
            self.problem(line, f"skipped: {_preview(toks)} not understood")

    def create_table(self, toks: list[Tok], i: int, line: int) -> None:
        if _up(toks, i) == "IF":
            i += 3  # IF NOT EXISTS
        name, i = _qualified(toks, i)
        if name is None:
            self.problem(line, f"skipped: no table name in {_preview(toks)}")
            return
        if i >= len(toks) or toks[i].kind != "group":
            self.problem(line, f"skipped: table {name} has no column list (CREATE TABLE ... AS, "
                         "PARTITION OF or OF type)")  # fmt: skip
            return
        if name in self.tables:
            self.problem(line, f"table {name} is defined twice; the last definition is used")
            self.refs = {k: v for k, v in self.refs.items() if k[0] != name}
        table = Table(name=name, model=name, file=None, line=line)
        self.tables[name] = table
        for item in _split(toks[i].value):
            at = item[0].line
            if self.is_table_constraint(item):
                self.table_constraint(table, item, at)
            else:
                col = self.column_def(table.name, item, at)
                if col:
                    self.add_column(table, col, at)

    def create_view(self, toks: list[Tok], i: int, line: int) -> None:
        """A view becomes a table of the same name and columns, filled with sample data like any
        other table: a bot that reads the view gets rows, and nothing needs the tables behind it.
        A column is typed like the table column it selects, when that can be seen; otherwise TEXT.
        """
        if _up(toks, i) == "IF":
            i += 3
        name, i = _qualified(toks, i)
        if name is None:
            self.problem(line, f"skipped: no view name in {_preview(toks)}")
            return
        declared: list[str] = []
        if i < len(toks) and toks[i].kind == "group":  # CREATE VIEW v (a, b) AS ...
            declared = [n for n in _column_list(toks[i].value) if n]
            i += 1
        start = next((k for k in range(i, len(toks)) if toks[k].up == "SELECT"), None)
        if start is None:
            self.problem(line, f"view {name}: no SELECT found; left out")
            return
        end = next((k for k in range(start + 1, len(toks)) if toks[k].up == "FROM"), len(toks))
        sources = self._view_sources(toks[end + 1 :])
        items = [p for p in _split(toks[start + 1 : end]) if p]
        if items and items[0][0].up in ("DISTINCT", "ALL", "TOP"):
            items[0] = items[0][1:] if items[0][0].up != "TOP" else items[0][2:]
        cols: list[tuple[str, str]] = []
        for item in items:
            cols += self._view_item(item, sources)
        if declared:
            cols = [(d, cols[k][1] if k < len(cols) else "TEXT") for k, d in enumerate(declared)]
        if not cols:
            self.problem(line, f"view {name}: no columns could be read; left out")
            return
        if self.find_table(name) is not None:
            self.problem(line, f"view {name} has the name of a table; the view is left out")
            return
        table = Table(name=name, model=name, file=None, line=line,
                      note=f"stands in for the view {name}: a table with its columns, filled "
                           "with sample data")  # fmt: skip
        seen: set[str] = set()
        for cname, ctype in cols:
            if cname.lower() in seen:
                continue
            seen.add(cname.lower())
            table.columns.append(Column(name=cname, type=ctype, nullable=True))
        self.tables[name] = table
        self.problems.append(f"line {line}: view {name} becomes a table with its "
                             f"{len(table.columns)} column(s); load sample rows into it")  # fmt: skip

    def _view_sources(self, toks: list[Tok]) -> dict[str, Table]:
        """FROM / JOIN tables with their aliases: alias (or name) -> table."""
        out: dict[str, Table] = {}
        k = 0
        while k < len(toks):
            if toks[k].up in ("FROM", "JOIN") or (k == 0 and toks[k].name):
                j = k + 1 if toks[k].up in ("FROM", "JOIN") else k
                tname, j = _qualified(toks, j)
                table = self.find_table(tname)
                if table is not None:
                    out[tname.lower()] = table
                    if _up(toks, j) == "AS":
                        j += 1
                    alias = (
                        toks[j].name
                        if j < len(toks) and toks[j].kind in ("word", "ident")
                        else None
                    )
                    if alias and (toks[j].up or "") not in _VIEW_KEYWORDS:
                        out[alias.lower()] = table
                k = j
                continue
            k += 1
        return out

    def _view_item(self, item: list[Tok], sources: dict[str, Table]) -> list[tuple[str, str]]:
        """Columns one select-list item gives: (name, type)."""
        if item[-1].kind == "punct" and item[-1].value == "*":  # * or t.*
            owner = item[0].name.lower() if len(item) >= 3 and item[0].name else None
            tables = [sources[owner]] if owner in sources else list(dict.fromkeys(sources.values()))
            return [(c.name, c.type) for t in tables for c in t.columns]
        alias = None
        if len(item) >= 2 and item[-1].name and (item[-2].up == "AS" or (
                item[-2].kind in ("word", "ident", "group") and item[-2].value != "."
                and item[-1].up not in _VIEW_KEYWORDS)):  # fmt: skip
            alias = item[-1].name
            item = item[:-2] if item[-2].up == "AS" else item[:-1]
        if len(item) == 1 and item[0].name:  # col
            col = next((c for t in sources.values() for c in t.columns
                        if c.name.lower() == item[0].name.lower()), None)  # fmt: skip
            return [(alias or item[0].name, col.type if col else "TEXT")]
        if len(item) == 3 and item[1].kind == "punct" and item[1].value == "." and item[2].name:
            table = sources.get((item[0].name or "").lower())
            col = self.find_column(table, item[2].name) if table else None
            return [(alias or item[2].name, col.type if col else "TEXT")]
        if alias:  # an expression: its type cannot be told from here
            return [(alias, "TEXT")]
        return []

    def create_index(self, toks: list[Tok], i: int, unique: bool, line: int) -> None:
        on = next((k for k in range(i, len(toks)) if toks[k].up == "ON"), None)
        if on is None:
            self.problem(line, f"skipped: {_preview(toks)} not understood")
            return
        k = on + 1
        if _up(toks, k) == "ONLY":
            k += 1
        name, k = _qualified(toks, k)
        group, _ = _first_group(toks, k)
        table = self.find_table(name)
        if table is None or group is None:
            self.problem(line, f"index on {name}: table not defined before it; index ignored")
            return
        cols = _column_list(group)
        if len(cols) != 1 or cols[0] is None:
            if unique:
                shown = ", ".join(c or "expression" for c in cols)
                self.problem(line, f"{table.name}: unique index on ({shown}) is not checked "
                             "(only single-column unique keys are)")  # fmt: skip
            return
        col = self.find_column(table, cols[0])
        if col is None:
            self.problem(line, f"index on {table.name}.{cols[0]}: no such column; ignored")
            return
        if unique:
            col.unique = True
        else:
            col.index = True

    def create_type(self, toks: list[Tok], i: int, line: int) -> None:
        name, k = _qualified(toks, i)
        if name and _up(toks, k) == "AS" and _up(toks, k + 1) == "ENUM":
            group, _ = _first_group(toks, k + 2)
            _, _, labels = _args(group)
            self.aliases[name.lower()] = f"VARCHAR({max((len(s) for s in labels), default=1)})"
        else:
            self.problem(line, f"skipped: CREATE TYPE {name or ''} (only enum types are read)")

    def create_domain(self, toks: list[Tok], i: int, line: int) -> None:
        name, k = _qualified(toks, i)
        if _up(toks, k) == "AS":
            k += 1
        parts, group, _ = self.type_words(toks, k)
        if name:
            self.aliases[name.lower()] = normalise_type(
                parts, group, self.dialect, self.aliases
            ).type

    # -- ALTER TABLE --

    def alter_table(self, toks: list[Tok], line: int) -> None:
        i = 2
        while _up(toks, i) in ("ONLY", "IF", "EXISTS"):
            i += 1
        name, i = _qualified(toks, i)
        if _up(toks, i) == "ONLY":
            i += 1
        table = self.find_table(name)
        actions = _split(toks[i:])
        if table is None:
            if any(_up(a, 0) in ("ADD", "WITH", "ALTER", "MODIFY") for a in actions):
                self.problem(line, f"ALTER TABLE {name}: table not defined before it; ignored")
            return
        for action in actions:
            self.alter_action(table, action, line)

    def alter_action(self, table: Table, a: list[Tok], line: int) -> None:
        k = 0
        if _up(a, 0) == "WITH" and _up(a, 1) in ("CHECK", "NOCHECK"):
            k = 2
        verb = _up(a, k)
        rest = a[k + 1 :]
        if verb == "ADD":
            if rest and rest[0].kind == "group":  # Oracle ADD ( col type, ... )
                for item in _split(rest[0].value):
                    self.add_or_constraint(table, item, line)
                return
            if _up(rest, 0) == "COLUMN":
                rest = rest[1:]
                if _up(rest, 0) == "IF":
                    rest = rest[3:]
            self.add_or_constraint(table, rest, line)
        elif verb == "ALTER":
            if _up(rest, 0) == "COLUMN":
                rest = rest[1:]
            col = self.find_column(table, rest[0].name) if rest else None
            if col is None:
                self.problem(line, f"ALTER TABLE {table.name}: {_preview(a)} not applied")
                return
            tail = rest[1:]
            if _up(tail, 0) == "ADD" and _contains(tail, "IDENTITY"):
                col.autoincrement = True
            elif _up(tail, 0) == "SET" and _up(tail, 1) == "DEFAULT":
                if _contains(tail, "NEXTVAL"):
                    col.autoincrement = True
            elif _up(tail, 0) == "SET" and _up(tail, 1) == "NOT":
                col.nullable = False
            elif _up(tail, 0) == "DROP" and _up(tail, 1) == "NOT":
                col.nullable = col.primary_key
            elif _up(tail, 0) == "TYPE" or (_up(tail, 0) == "SET" and _up(tail, 2) == "TYPE"):
                start = 1 if _up(tail, 0) == "TYPE" else 3
                parts, group, _ = self.type_words(tail, start)
                col.type = self.make_type(table.name, col.name, parts, group, line).type
            elif tail and (tail[0].name or "").upper() in TYPE_WORDS:  # SQL Server
                new = self.column_def(table.name, rest, line)
                if new and new.type:
                    col.type = new.type
                    col.nullable = not (new.notnull or col.primary_key)
        elif verb in ("MODIFY", "CHANGE"):
            if rest and rest[0].kind == "group":
                for item in _split(rest[0].value):
                    self.modify(table, item, item[0].name if item else None, line)
                return
            if _up(rest, 0) == "COLUMN":
                rest = rest[1:]
            old = rest[0].name if rest else None
            if verb == "CHANGE":
                rest = rest[1:]
            self.modify(table, rest, old, line)
        elif verb in ("DROP", "RENAME"):
            self.problem(line, f"ALTER TABLE {table.name}: {_preview(a)} not applied")
        elif verb is None or verb in ("OWNER", "CHECK", "NOCHECK", "ENABLE", "DISABLE", "SET",
                                      "CLUSTER", "REPLICA", "INHERIT", "NO", "RESET", "OF",
                                      "NOT", "VALIDATE", "ATTACH", "DETACH", "FORCE",
                                      "AUTO_INCREMENT", "ENGINE", "LOGGING", "NOLOGGING",
                                      "MOVE", "SHRINK", "ROW", "PARALLEL", "NOPARALLEL",
                                      "CONVERT", "DEFAULT", "COMMENT"):  # fmt: skip
            pass
        else:
            self.problem(line, f"ALTER TABLE {table.name}: {_preview(a)} not understood")

    def add_or_constraint(self, table: Table, item: list[Tok], line: int) -> None:
        if not item:
            return
        if self.is_table_constraint(item) or _up(item, 0) in ("DEFAULT", "KEY", "INDEX"):
            self.table_constraint(table, item, line)
            return
        col = self.column_def(table.name, item, line)
        if col:
            self.add_column(table, col, line)

    def modify(self, table: Table, item: list[Tok], old: str | None, line: int) -> None:
        col = self.find_column(table, old) if old else None
        new = self.column_def(table.name, item, line) if item else None
        if col is None or new is None:
            self.problem(line, f"ALTER TABLE {table.name}: change to column {old} not applied")
            return
        col.name = new.name
        if new.type:
            col.type = new.type
        if new.notnull:
            col.nullable = False
        elif new.nullable_said and not col.primary_key:
            col.nullable = True
        if new.identity:
            col.autoincrement = True
        if new.primary:
            col.primary_key, col.nullable = True, False
        if new.unique:
            col.unique = True
        if new.references:
            self.refs[(table.name, col.name)] = new.references

    # -- columns and constraints --

    def is_table_constraint(self, item: list[Tok]) -> bool:
        u = item[0].up
        if u in _TABLE_CONSTRAINT:
            return u != "PRIMARY" or _up(item, 1) == "KEY"
        if u in ("KEY", "INDEX"):
            if len(item) > 1 and item[1].kind == "group":
                return True
            second = (item[1].name or "").upper() if len(item) > 1 else ""
            return len(item) > 2 and item[2].kind == "group" and second not in TYPE_WORDS
        return False

    def type_words(self, toks: list[Tok], j: int) -> tuple[list[str], list[Tok] | None, int]:
        parts: list[str] = []
        group: list[Tok] | None = None
        array = False
        while j < len(toks):
            t = toks[j]
            if t.kind == "ident" and not parts:
                if t.value == "":
                    break
                name, j = _qualified(toks, j)
                parts.append(name)
                continue
            if t.kind == "ident" and t.value == "":  # Postgres integer[]
                array = True
                j += 1
                continue
            if t.kind == "word":
                u = t.up
                if not parts:
                    name, j = _qualified(toks, j)
                    parts.append(name)
                    continue
                if u == "WITH" and _up(toks, j + 1) in ("TIME", "LOCAL"):
                    parts.append(t.value)
                    j += 1
                    continue
                if u == "CHARACTER" and _up(toks, j + 1) == "SET":
                    break
                if u in _TYPE_CONT:
                    parts.append(t.value)
                    j += 1
                    continue
                break
            if t.kind == "group" and parts and group is None:
                group = t.value
                j += 1
                continue
            break
        if array:
            parts.append("[]")
        return parts, group, j

    def make_type(
        self, table: str, column: str, parts: list[str], group: list[Tok] | None, line: int
    ) -> _Type:
        if parts and parts[-1] == "[]":
            self.problem(line, f"{table}.{column}: array type treated as TEXT")
            return _Type("TEXT")
        found = normalise_type(parts, group, self.dialect, self.aliases)
        if found.note:
            self.problem(line, f"{table}.{column}: {found.note}")
        return found

    def column_def(self, table: str, item: list[Tok], line: int) -> _Col | None:
        name = item[0].name
        if name is None:
            self.problem(line, f"{table}: {_preview(item)} is not a column; ignored")
            return None
        if item[0].kind == "word" and item[0].up in ("CHECK", "EXCLUDE"):
            return None
        col = _Col(name=name, type=None)
        if _up(item, 1) == "AS":  # SQL Server computed column
            col.computed = True
            return col
        parts, group, j = [], None, 1
        if _up(item, 1) not in _NOT_TYPES:
            parts, group, j = self.type_words(item, 1)
        if parts:
            found = self.make_type(table, name, parts, group, line)
            col.type, col.identity, col.unreadable = found.type, found.identity, found.unreadable
        rest = item[j:]
        k = 0
        while k < len(rest):
            u = rest[k].up
            nxt = _up(rest, k + 1)
            if u == "NOT" and nxt == "NULL":
                col.notnull = True
                k += 2
            elif u == "NULL":
                col.nullable_said = True
                k += 1
            elif u == "PRIMARY" and nxt == "KEY":
                col.primary = True
                k += 2
            elif u == "UNIQUE":
                col.unique = True
                k += 1
            elif u == "REFERENCES":
                target, k = _qualified(rest, k + 1)
                ref_col = None
                if k < len(rest) and rest[k].kind == "group":
                    cols = _column_list(rest[k].value)
                    ref_col = cols[0] if cols else None
                    k += 1
                if target:
                    col.references = (target, ref_col)
            elif u == "ON" and nxt in ("DELETE", "UPDATE"):
                k += 2
                k += 2 if _up(rest, k) in ("SET", "NO") else 1
            elif u == "ON" and nxt == "NULL":
                k += 2
            elif u == "GENERATED":
                at = next((m for m in range(k + 1, len(rest)) if rest[m].up == "AS"), None)
                if at is None:
                    k += 1
                elif _up(rest, at + 1) == "IDENTITY":
                    col.identity = True
                    k = at + 2
                else:
                    col.computed = True
                    k = at + 1
            elif u == "AS" and k + 1 < len(rest) and rest[k + 1].kind == "group":
                col.computed = True
                k += 2
            elif u in ("IDENTITY", "AUTO_INCREMENT", "AUTOINCREMENT", "SERIAL"):
                col.identity = True
                k += 1
            elif u == "DEFAULT":
                tail = rest[k + 1 :]
                if _contains(tail, "NEXTVAL") or (
                    _up(tail, 0) == "NEXT" and _up(tail, 1) == "VALUE"
                ):
                    col.identity = True
                k += 1
            elif u in ("CONSTRAINT", "COLLATE"):
                _, k = _qualified(rest, k + 1)
            elif u == "COMMENT":
                k += 2
            else:
                k += 1
        return col

    def add_column(self, table: Table, col: _Col, line: int) -> None:
        if col.computed or col.unreadable:
            what = "computed column" if col.computed else "row version column"
            self.problem(line, f"{table.name}.{col.name}: {what} left out; the database fills it")
            return
        if self.find_column(table, col.name):
            self.problem(line, f"{table.name}.{col.name}: column defined twice; first one kept")
            return
        if col.type is None:
            self.problem(line, f"{table.name}.{col.name}: no type given; treated as TEXT")
        table.columns.append(
            Column(
                name=col.name,
                type=col.type or "TEXT",
                nullable=not (col.notnull or col.primary),
                primary_key=col.primary,
                unique=col.unique and not col.primary,
                autoincrement=col.identity,
            )
        )
        if col.references:
            self.refs[(table.name, col.name)] = col.references

    def table_constraint(self, table: Table, item: list[Tok], line: int) -> None:
        k = 0
        if item[0].up == "CONSTRAINT":
            _, k = _qualified(item, 1)
        kind = _up(item, k)
        if kind in ("CHECK", "EXCLUDE", "DEFAULT", "LIKE", "PERIOD", None):
            return
        group, after = _first_group(item, k + 1)
        if group is None:
            self.problem(line, f"{table.name}: {_preview(item)} has no column list; ignored")
            return
        names = _column_list(group)
        cols = []
        for n in names:
            col = self.find_column(table, n) if n else None
            if col is None:
                self.problem(line, f"{table.name}: {kind} names column {n or 'expression'}, "
                             "which is not defined; ignored")  # fmt: skip
                return
            cols.append(col)
        if kind == "PRIMARY":
            for col in cols:
                col.primary_key, col.nullable, col.unique = True, False, False
        elif kind == "UNIQUE":
            if len(cols) == 1:
                if not cols[0].primary_key:
                    cols[0].unique = True
            else:
                shown = ", ".join(c.name for c in cols)
                self.problem(line, f"{table.name}: unique key on ({shown}) is not checked "
                             "(only single-column unique keys are)")  # fmt: skip
        elif kind == "FOREIGN":
            ref = next((m for m in range(after, len(item)) if item[m].up == "REFERENCES"), None)
            if ref is None:
                self.problem(line, f"{table.name}: foreign key without REFERENCES; ignored")
                return
            target, m = _qualified(item, ref + 1)
            ref_cols: list[str | None] = [None] * len(cols)
            if m < len(item) and item[m].kind == "group":
                ref_cols = _column_list(item[m].value)
            if len(cols) > 1:
                self.problem(line, f"{table.name}: foreign key ({', '.join(c.name for c in cols)})"
                             " has several columns; each is checked on its own")  # fmt: skip
            for col, rc in zip(cols, ref_cols, strict=False):
                if target:
                    self.refs[(table.name, col.name)] = (target, rc)
        elif kind in ("KEY", "INDEX", "FULLTEXT", "SPATIAL"):
            if len(cols) == 1:
                cols[0].index = True

    # -- lookups and the end --

    def find_table(self, name: str | None) -> Table | None:
        if name is None:
            return None
        if name in self.tables:
            return self.tables[name]
        low = name.lower()
        return next((t for n, t in self.tables.items() if n.lower() == low), None)

    def find_column(self, table: Table, name: str | None) -> Column | None:
        if name is None:
            return None
        exact = next((c for c in table.columns if c.name == name), None)
        return exact or next((c for c in table.columns if c.name.lower() == name.lower()), None)

    def finish(self) -> list[Table]:
        tables = []
        for table in self.tables.values():
            if not table.columns:
                self.problem(table.line, f"table {table.name} has no columns; left out")
                continue
            tables.append(table)
        if self.dialect == "sqlite":  # an INTEGER primary key is the row id: SQLite numbers it
            for table in tables:
                keys = [c for c in table.columns if c.primary_key]
                if len(keys) == 1 and keys[0].type == "INTEGER":
                    keys[0].autoincrement = True
        for (tname, cname), (target, tcol) in self.refs.items():
            table = self.tables[tname]
            col = self.find_column(table, cname)
            parent = self.find_table(target)
            if col is None or table not in tables:
                continue
            if parent is None or parent not in tables:
                self.problem(table.line, f"{tname}.{cname} refers to table {target}, which is "
                             "not in this file; the reference is kept as written")  # fmt: skip
                col.references = (target, tcol or "id")
                continue
            if tcol is None:
                keys = [c.name for c in parent.columns if c.primary_key]
                if len(keys) != 1:
                    self.problem(table.line, f"{tname}.{cname} refers to {parent.name} without "
                                 "naming a column, and it has no single primary key; ignored")  # fmt: skip
                    continue
                tcol = keys[0]
            target_col = self.find_column(parent, tcol)
            if target_col is None:
                self.problem(table.line, f"{tname}.{cname} refers to {parent.name}.{tcol}, which "
                             "is not defined; ignored")  # fmt: skip
                continue
            col.references = (parent.name, target_col.name)
        return tables


def _guess_dialect(text: str) -> str:
    if re.search(r"^\s*GO\s*$", text, re.IGNORECASE | re.MULTILINE) or re.search(
        r"\[dbo\]|IDENTITY\s*\(\s*\d|NVARCHAR\s*\(\s*MAX|DATETIME2|UNIQUEIDENTIFIER", text, re.I
    ):
        return "mssql"
    if "`" in text or re.search(r"AUTO_INCREMENT|ENGINE\s*=", text, re.IGNORECASE):
        return "mysql"
    if re.search(r"VARCHAR2|NUMBER\s*\(|\bCLOB\b|SEGMENT CREATION", text, re.IGNORECASE):
        return "oracle"
    if re.search(r"\bAUTOINCREMENT\b|WITHOUT ROWID", text, re.IGNORECASE):
        return "sqlite"
    return "postgres"


def _preview(toks: list[Tok], width: int = 60) -> str:
    words: list[str] = []
    for t in toks:
        if t.kind == "group":
            words.append("(...)")
        elif t.kind == "string":
            words.append("'...'")
        else:
            words.append(str(t.value))
        if sum(len(w) + 1 for w in words) > width:
            return " ".join(words)[:width] + "..."
    return " ".join(words)


# --- public ------------------------------------------------------------------------------------


def parse_with_problems(text: str) -> tuple[list[Table], list[str]]:
    """Tables defined in a DDL text (in file order), and what could not be read."""
    try:
        return _Parser(text).run()
    except Exception as exc:  # a bug here must not stop an import; say so instead
        return [], [f"the schema could not be read ({type(exc).__name__}: {exc})"]


def parse(text: str) -> list[Table]:
    """Tables defined in a DDL text, in file order. Use `parse_with_problems` to see what was
    skipped."""
    return parse_with_problems(text)[0]


def to_model(app: str, text: str, evidence_id: str) -> AppModel:
    """An app model holding the tables of one DDL file: declared, complete for tables."""
    tables, problems = parse_with_problems(text)
    ref = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return AppModel(
        app=app,
        evidence=[Evidence(id=evidence_id, kind="ddl", ref=ref, complete=["tables"])],
        tables=[
            ModelTable(
                name=t.name,
                basis="declared",
                **{"from": [evidence_id]},
                columns=[
                    ModelColumn(
                        name=c.name,
                        type=c.type,
                        nullable=c.nullable,
                        primary_key=c.primary_key,
                        unique=c.unique,
                        index=c.index,
                        references=list(c.references) if c.references else None,
                        autoincrement=c.autoincrement,
                    )
                    for c in t.columns
                ],
            )
            for t in tables
        ],
        problems=[f"ddl: {p}" for p in problems],
    )
