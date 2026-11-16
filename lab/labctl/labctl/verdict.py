"""Did a session change only what its automation is expected to change?

An automation declares `expects:` in its `automation.yaml`: the tables it may change, how
(insert, update, delete), which columns an update may touch, and optionally how many rows. A
session then gets a verdict:

- PASS: every recorded row change matches an expectation.
- FAIL: at least one change matches none, or a table changed more rows than allowed.
- NOT_DECLARED: the automation has no `expects:` yet, so nothing can be judged.

`learn()` turns the changes of a session someone checked by hand into expectations, so the
usual way to declare them is: run once, read the changes, accept them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from labctl.models import ExpectedChange

PASS, FAIL, NOT_DECLARED = "PASS", "FAIL", "NOT_DECLARED"
SHOWN = 50


@dataclass
class Verdict:
    status: str
    checked: int = 0
    unexpected: list[dict[str, Any]] = field(default_factory=list)  # changes no rule allows
    over: list[str] = field(default_factory=list)  # "table insert: 12 rows, at most 10"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked": self.checked,
            "unexpected": self.unexpected[:SHOWN],
            "unexpected_count": len(self.unexpected),
            "over": self.over,
            "reasons": self.reasons,
        }


def _norm(name: str) -> str:
    return name.split(".")[-1].strip('"[]`').lower()


def _why_not(change: dict[str, Any], rules: list[ExpectedChange]) -> str | None:
    """None when a rule allows the change, else why not."""
    table = _norm(change.get("table", ""))
    same_table = [r for r in rules if _norm(r.table) == table]
    if not same_table:
        return f"table {change.get('table')} is not expected to change"
    same_op = [r for r in same_table if change.get("op") in r.ops]
    if not same_op:
        allowed = sorted({op for r in same_table for op in r.ops})
        return f"{change.get('op')} is not expected on {change.get('table')} (only {', '.join(allowed)})"
    if change.get("op") != "update":
        return None
    changed = {c.lower() for c in change.get("columns") or []}
    for rule in same_op:
        allowed_cols = {c.lower() for c in rule.columns}
        if not allowed_cols or changed <= allowed_cols:
            return None
    extra = sorted(changed - {c.lower() for r in same_op for c in r.columns})
    return f"columns {', '.join(extra)} of {change.get('table')} are not expected to change"


def judge(changes: list[dict[str, Any]], expects: list[ExpectedChange] | None) -> Verdict:
    if expects is None:
        return Verdict(
            NOT_DECLARED,
            checked=len(changes),
            reasons=["the automation has no `expects:` yet; accept a checked session to set it"],
        )
    v = Verdict(PASS, checked=len(changes))
    for change in changes:
        why = _why_not(change, expects)
        if why:
            v.unexpected.append({**change, "why": why})
    counts = Counter((_norm(c.get("table", "")), c.get("op")) for c in changes)
    for rule in expects:
        if rule.max_rows is None:
            continue
        for op in rule.ops:
            n = counts.get((_norm(rule.table), op), 0)
            if n > rule.max_rows:
                v.over.append(f"{rule.table} {op}: {n} rows, at most {rule.max_rows} expected")
    if v.unexpected or v.over:
        v.status = FAIL
        if v.unexpected:
            v.reasons.append(f"{len(v.unexpected)} unexpected change(s)")
        v.reasons += v.over
    return v


def session_changes(agent, session_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A stored session and its row changes with the columns each update touched (the detail
    has them for sessions recorded before the session document carried them)."""
    session = agent.session(session_id)
    changes = session.get("changes") or []
    if any(c.get("op") == "update" and not c.get("columns") for c in changes):
        detail = agent.session_detail(session_id) or {}
        changes = [
            {k: c.get(k) for k in ("app", "table", "op", "key", "columns")}
            for c in detail.get("changes") or changes
        ]
        session = {**session, "changes": changes}
    return session, changes


def for_session(root, app_id: str, session_id: str, automation_id: str | None = None):
    """(verdict, session, automation id) for a stored session. The automation is the one the
    session was recorded for, unless another is named."""
    from labctl import lab as lab_mod
    from labctl import repo

    session, changes = session_changes(lab_mod.agent(root, app_id), session_id)
    automation_id = automation_id or session.get("automation")
    if not automation_id:
        return (
            Verdict(NOT_DECLARED, len(changes), reasons=["the session names no automation"]),
            session,
            None,
        )
    model, problems = repo.load(
        root / "automations" / automation_id / "automation.yaml", "automation"
    )
    if model is None:
        reason = f"automation {automation_id} cannot be read: " + "; ".join(map(str, problems))
        return Verdict(NOT_DECLARED, len(changes), reasons=[reason]), session, automation_id
    expects = model.expects
    mine = [c for c in changes if c.get("app") in (None, app_id)]
    return judge(mine, expects), session, automation_id


def accept(root, automation_id: str, changes: list[dict[str, Any]], replace: bool = False):
    """Write the changes' kinds into the automation's `expects:` (widening what is there,
    unless `replace`). Returns the new expectations."""
    import yaml

    from labctl import repo

    path = root / "automations" / automation_id / "automation.yaml"
    model, problems = repo.load(path, "automation")
    if model is None:
        raise ValueError(f"automation {automation_id}: " + "; ".join(map(str, problems)))
    learned = learn(changes)
    model.expects = learned if replace else merge(model.expects, learned)
    path.write_text(
        yaml.safe_dump(
            model.model_dump(mode="json", exclude_none=True), sort_keys=False, width=100
        ),
        encoding="utf-8",
        newline="\n",
    )
    return model.expects


def learn(changes: list[dict[str, Any]]) -> list[ExpectedChange]:
    """Expectations that allow exactly the kinds of change seen: per table, the ops seen and,
    for updates, the columns seen. Row counts are left open."""
    ops: dict[str, set[str]] = defaultdict(set)
    cols: dict[str, set[str]] = defaultdict(set)
    for c in changes:
        table = c.get("table")
        if not table:
            continue
        ops[table].add(c.get("op"))
        if c.get("op") == "update":
            cols[table] |= set(c.get("columns") or [])
    order = ("insert", "update", "delete")
    return [
        ExpectedChange(
            table=t,
            ops=[o for o in order if o in ops[t]],
            columns=sorted(cols[t]) if "update" in ops[t] else [],
        )
        for t in sorted(ops)
    ]


def merge(old: list[ExpectedChange] | None, new: list[ExpectedChange]) -> list[ExpectedChange]:
    """Widen `old` by `new`: union of ops and columns per table. An update rule with no
    columns (any column) stays open."""
    by_table: dict[str, ExpectedChange] = {}
    for rule in old or []:
        by_table[_norm(rule.table)] = rule.model_copy(deep=True)
    for rule in new:
        key = _norm(rule.table)
        have = by_table.get(key)
        if have is None:
            by_table[key] = rule
            continue
        had_update = "update" in have.ops
        have.ops = [o for o in ("insert", "update", "delete") if o in {*have.ops, *rule.ops}]
        if "update" in rule.ops:
            if not had_update:
                have.columns = sorted(rule.columns)
            elif have.columns and rule.columns:
                have.columns = sorted({*have.columns, *rule.columns})
            else:
                have.columns = []  # one side already allowed any column
    return [by_table[k] for k in sorted(by_table)]
