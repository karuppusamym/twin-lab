"""Data packs that span apps (T-19): the same real-world thing must exist in every app.

A bot that works across two apps (look a customer up in one, change their case in the other)
needs both labs to hold the same customers. Each app's `app.yaml` says how its records match
another app's in `links`:

    links:
      - {entity: customer, column: cases.cust_no, to: crm.customer.cust_no, via: equal}
      - {entity: customer, column: cases.cust_id, to: crm.customer.id, via: crosswalk,
         table: cust_xref}          # rows of cust_xref hold both cust_id and id

A cross-app pack is `{"apps": {"<app>": {"<table>": [rows]}, ...}}`. Before anything loads, every
link is checked both ways; a key present on one side only refuses the pack, naming the key. Then
each app's lab loads its part; if one fails, the apps already loaded get their previous
baseline back, so the labs never hold half a pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from labctl import lab as lab_mod
from labctl.bindings import load_app
from labctl.models import App, Link


class CrossAppError(Exception):
    pass


@dataclass
class Problem:
    entity: str
    key: str
    present: str  # where it is
    missing: str  # where it is not

    def __str__(self) -> str:
        return f"{self.entity} {self.key!r}: in {self.present} but not in {self.missing}"


def _split(ref: str, parts: int) -> list[str]:
    bits = ref.split(".")
    if len(bits) != parts or not all(bits):
        want = "table.column" if parts == 2 else "app.table.column"
        raise CrossAppError(f"link reference {ref!r} must be {want}")
    return bits


def _rows(pack: dict, app_id: str, table: str) -> list[dict] | None:
    tables = pack.get(app_id)
    if tables is None:
        return None
    for name, rows in tables.items():
        if name.lower() == table.lower():
            return rows
    return []


def _col(row: dict, column: str) -> Any:
    for k, v in row.items():
        if k.lower() == column.lower():
            return v
    return None


def _keys(rows: list[dict], column: str) -> set[str]:
    return {str(v) for r in rows if (v := _col(r, column)) is not None and str(v) != ""}


def check_link(pack: dict, app_id: str, link: Link) -> list[Problem]:
    table, column = _split(link.column, 2)
    other_app, other_table, other_column = _split(link.to, 3)
    here, there = _rows(pack, app_id, table), _rows(pack, other_app, other_table)
    if here is None or there is None:
        return []  # the pack does not cover both apps: nothing to compare
    mine, theirs = _keys(here, column), _keys(there, other_column)
    if link.via == "crosswalk":
        if not link.table:
            raise CrossAppError(f"{app_id}: a crosswalk link needs `table`")
        xref = _rows(pack, app_id, link.table) or []
        pairs = {(str(_col(r, column)), str(_col(r, other_column))) for r in xref}
        here_ref = f"{app_id}.{table}.{column}"
        there_ref = f"{other_app}.{other_table}.{other_column}"
        xref_ref = f"the crosswalk {app_id}.{link.table}"
        not_mapped = mine - {a for a, _ in pairs}  # our key has no partner
        dangling = {b for a, b in pairs if a in mine} - theirs  # its partner is not there
        orphans = theirs - {b for _, b in pairs}  # their key has no partner here
        return (
            [Problem(link.entity, k, here_ref, xref_ref) for k in sorted(not_mapped)]
            + [Problem(link.entity, k, xref_ref, there_ref) for k in sorted(dangling)]
            + [Problem(link.entity, k, there_ref, xref_ref) for k in sorted(orphans)]
        )
    return [
        Problem(link.entity, k, f"{app_id}.{table}.{column}", f"{other_app}.{other_table}.{other_column}")
        for k in sorted(mine - theirs)
    ] + [
        Problem(link.entity, k, f"{other_app}.{other_table}.{other_column}", f"{app_id}.{table}.{column}")
        for k in sorted(theirs - mine)
    ]  # fmt: skip


def check(pack: dict, apps: dict[str, App]) -> list[Problem]:
    """Every link of every app in the pack, both ways."""
    problems: list[Problem] = []
    for app_id in pack:
        app = apps.get(app_id)
        if app is None:
            raise CrossAppError(f"the pack names an unknown app {app_id!r}")
        for link in app.links:
            problems += check_link(pack, app_id, link)
    return problems


def read_pack(path: Path) -> dict:
    import json

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CrossAppError(f"cannot read {path}: {exc}") from exc
    apps = doc.get("apps") if isinstance(doc, dict) else None
    if (
        not isinstance(apps, dict)
        or not apps
        or not all(
            isinstance(t, dict) and all(isinstance(r, list) for r in t.values())
            for t in apps.values()
        )
    ):
        raise CrossAppError(f'{path}: expected {{"apps": {{"<app>": {{"<table>": [rows]}}}}}}')
    return apps


def load(root: Path, pack: dict, label: str) -> dict[str, dict]:
    """Check the links, then load each app's part. All apps or none."""
    apps = {a: load_app(root, a) for a in pack}
    problems = check(pack, apps)
    if problems:
        raise CrossAppError(
            f"{len(problems)} key(s) do not match across apps; nothing was loaded:\n"
            + "\n".join(str(p) for p in problems[:50])
        )
    agents = {a: lab_mod.agent(root, a) for a in pack}
    before = {
        a: ((agent.info() or {}).get("baseline") or {}).get("id") for a, agent in agents.items()
    }
    done: dict[str, dict] = {}
    try:
        for app_id, rows in pack.items():
            done[app_id] = agents[app_id].load(rows, label)
    except lab_mod.LabError as exc:
        undo = []
        for app_id in done:
            if before.get(app_id):
                try:
                    agents[app_id].restore_baseline(before[app_id])
                    undo.append(app_id)
                except lab_mod.LabError:
                    pass
        raise CrossAppError(
            f"loading stopped: {exc}. Put back the previous data of: {', '.join(undo) or 'none'}"
        ) from exc
    return done
