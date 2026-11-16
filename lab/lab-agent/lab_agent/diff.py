"""Row changes between two snapshots (`Rows`: table -> list of JSON-safe rows).

Tables with a primary key are compared by key: insert, update (with the changed columns), delete.
Tables without one are compared as multisets of whole rows: a changed row is a delete plus an
insert, and its key is `row:<hash of the row>`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

Rows = dict[str, list[dict[str, Any]]]
OPS = ("insert", "update", "delete")


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(row: dict[str, Any]) -> str:
    return "row:" + hashlib.sha256(_canonical(row).encode()).hexdigest()[:16]


def key_of(row: dict[str, Any], pk: list[str]) -> str:
    return ",".join("" if row.get(c) is None else str(row.get(c)) for c in pk)


def _sort_key(key: str) -> tuple:
    parts = []
    for p in key.split(","):
        try:
            parts.append((0, int(p), ""))
        except ValueError:
            parts.append((1, 0, p))
    return tuple(parts)


def diff_table(
    table: str, before: list[dict[str, Any]], after: list[dict[str, Any]], pk: list[str]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if pk:
        old = {key_of(r, pk): r for r in before}
        new = {key_of(r, pk): r for r in after}
        for key in sorted(new.keys() - old.keys(), key=_sort_key):
            changes.append({"table": table, "op": "insert", "key": key, "after": new[key]})
        for key in sorted(new.keys() & old.keys(), key=_sort_key):
            a, b = old[key], new[key]
            cols = [c for c in dict.fromkeys([*a, *b]) if a.get(c) != b.get(c)]
            if cols:
                changes.append(
                    {
                        "table": table,
                        "op": "update",
                        "key": key,
                        "columns": cols,
                        "before": {c: a.get(c) for c in cols},
                        "after": {c: b.get(c) for c in cols},
                    }
                )
        for key in sorted(old.keys() - new.keys(), key=_sort_key):
            changes.append({"table": table, "op": "delete", "key": key, "before": old[key]})
        return changes
    old_c = Counter(_canonical(r) for r in before)
    new_c = Counter(_canonical(r) for r in after)
    for text, n in sorted((new_c - old_c).items()):
        row = json.loads(text)
        changes += [
            {"table": table, "op": "insert", "key": row_hash(row), "after": row} for _ in range(n)
        ]
    for text, n in sorted((old_c - new_c).items()):
        row = json.loads(text)
        changes += [
            {"table": table, "op": "delete", "key": row_hash(row), "before": row} for _ in range(n)
        ]
    return changes


def diff(before: Rows, after: Rows, pks: dict[str, list[str]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for table in sorted(before.keys() | after.keys()):
        changes += diff_table(
            table, before.get(table, []), after.get(table, []), pks.get(table, [])
        )
    return changes
