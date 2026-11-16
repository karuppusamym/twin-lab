"""Which tables and columns a SQL statement reads or writes. Pattern-based, not a SQL parser."""

from __future__ import annotations

import re

NAME = r'((?:[\w$#]+|\[[^\]]+\]|"[^"]+")(?:\.(?:[\w$#]+|\[[^\]]+\]|"[^"]+"))*)'
KEYWORDS = {
    "where", "on", "join", "inner", "left", "right", "full", "outer", "cross", "group", "order",
    "having", "union", "limit", "set", "values", "select", "using", "natural", "fetch", "offset",
}  # fmt: skip
READ = re.compile(rf"\b(?:FROM|JOIN)\s+{NAME}(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", re.I)
WRITES = (
    re.compile(rf"\bINSERT\s+INTO\s+{NAME}", re.I),
    re.compile(rf"\bUPDATE\s+{NAME}", re.I),
    re.compile(rf"\bDELETE\s+FROM\s+{NAME}", re.I),
    re.compile(rf"\bMERGE\s+INTO\s+{NAME}", re.I),
)
DELETE_FROM = re.compile(rf"\bDELETE\s+FROM\s+{NAME}", re.I)
SELECT_LIST = re.compile(r"^\s*SELECT\s+(?:DISTINCT\s+)?(.*?)\s+FROM\b", re.I | re.S)
QUALIFIED = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
PROCEDURE = re.compile(r"^\s*(?:EXEC(?:UTE)?|CALL)\b", re.I)


def _bare(name: str) -> str:
    """`dbo.[Case Audit]` -> `Case Audit`: last segment, without quoting."""
    last = re.findall(r'\[[^\]]+\]|"[^"]+"|[\w$#]+', name)[-1]
    return last.strip('[]"')


def tables(sql: str) -> tuple[list[dict], str | None]:
    """Returns ([{table, columns, access}], problem). `problem` explains why nothing was found."""
    if PROCEDURE.match(sql):
        return [], "stored procedure call; the tables behind it cannot be read from the statement"
    text = re.sub(r"'(?:[^']|'')*'", "''", sql)  # literals must not look like table names
    deleted = {_bare(m.group(1)) for m in DELETE_FROM.finditer(text)}
    writes = {_bare(m.group(1)) for rx in WRITES for m in rx.finditer(text)}
    reads: dict[str, None] = {}
    alias: dict[str, str] = {}
    for m in READ.finditer(text):
        table = _bare(m.group(1))
        alias[table.lower()] = table
        if m.group(2) and m.group(2).lower() not in KEYWORDS:
            alias[m.group(2).lower()] = table
        reads[table] = None
    for table in deleted:
        reads.pop(table, None)
    order = list(dict.fromkeys([*reads, *sorted(writes)]))
    if not order:
        return [], "no table name could be read from the statement"

    columns: dict[str, list[str]] = {t: [] for t in order}
    for owner, column in QUALIFIED.findall(text):
        table = alias.get(owner.lower())
        if table in columns and column not in columns[table]:
            columns[table].append(column)
    select = SELECT_LIST.match(text)
    if select and len(order) == 1:
        for item in select.group(1).split(","):
            name = item.strip().split()[0] if item.strip() else ""
            if re.fullmatch(r"[A-Za-z_]\w*", name) and name not in columns[order[0]]:
                columns[order[0]].append(name)

    def access(table: str) -> str:
        if table in writes and table in reads:
            return "read_write"
        return "write" if table in writes else "read"

    return [{"table": t, "columns": columns[t], "access": access(t)} for t in order], None
