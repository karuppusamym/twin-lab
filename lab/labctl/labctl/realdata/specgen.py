"""A first subset spec from the replica's inventory (Prod Echo I-13, generator).

Given the table the bot works on and the records it needs, draft `subset.yaml`:

- the seed: those key values (or a `where` with `:params`);
- per child table, a limit on rows per parent (default 20), so one busy parent cannot pull the
  whole table;
- child tables that look like history or logs (audit, log, history, archive, journal, event,
  _hist, _bak) are excluded: they are big and rarely needed to open a record;
- associations are left empty: `trace-diff` proposes them from what a screen reads.

A person reads the draft and changes it; nothing is extracted by drafting.
"""

from __future__ import annotations

import re
from typing import Any

from labctl.realdata import inventory
from labctl.realdata.closure import SpecError, SubsetSpec

HISTORY = re.compile(r"(audit|(^|_)log(s)?($|_)|history|archive|journal|event|_hist$|_bak$)", re.I)
DEFAULT_LIMIT = 20


def draft(
    tables: list[inventory.DbTable],
    seed_table: str,
    values: list[Any] | None = None,
    where: str | None = None,
    params: dict[str, Any] | None = None,
    app: str | None = None,
    db_env: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    by_lower = {t.name.lower(): t for t in tables}
    seed = by_lower.get(seed_table.lower())
    if seed is None:
        raise SpecError(f"no table {seed_table} in the inventory")
    children: set[str] = set()
    for t in tables:
        for fk in inventory.foreign_keys(t):
            if fk.parent.lower() in by_lower:
                children.add(t.name)
    excluded = sorted(c for c in children if HISTORY.search(c) and c.lower() != seed.name.lower())
    limits = {c: limit for c in sorted(children) if c not in excluded and c != seed.name}
    seed_doc: dict[str, Any] = {"table": seed.name}
    if where:
        seed_doc.update(where=where, params=params or {})
    else:
        seed_doc["values"] = list(values or [])
    spec = {
        "app": app,
        "db": db_env,
        "seeds": [seed_doc],
        "closure": {"associations": [], "limits": limits, "exclude": excluded},
    }
    spec = {k: v for k, v in spec.items() if v is not None}
    SubsetSpec.model_validate(spec)  # the draft is a valid spec
    return spec
