"""Value binding and provenance (Prod Echo I-11, I-12): which column got which value from where.

A bot run is joined with the lab session it ran in. Each value the bot typed or selected (from
its bot trace, never from a Private field) is looked for among the values the session wrote.
Every written column is then one of:

- `typed`: equals what the bot typed into a field (the field and selector are named);
- `selected`: equals what the bot chose in a list;
- `derived`: the app set it (ids, timestamps, a code behind a chosen label, a computed status).

Values the bot entered that no written column holds are listed too: the app may have dropped,
reformatted or coded them. All of this runs on the lab's data, which is synthetic or masked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from labctl.botlog.models import BotTrace

ENTERED = {"type": "typed", "select": "selected"}


@dataclass
class Entered:
    kind: str  # typed | selected
    value: str
    field: str | None
    selector: str | None
    seq: int


@dataclass
class Binding:
    table: str
    key: str
    column: str
    kind: str  # typed | selected | derived
    source: str | None = None  # the field or selector it came from


@dataclass
class Provenance:
    bindings: list[Binding] = field(default_factory=list)
    not_stored: list[Entered] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"typed": 0, "selected": 0, "derived": 0}
        for b in self.bindings:
            out[b.kind] += 1
        return out

    def markdown(self) -> str:
        c = self.counts()
        lines = [
            "## Where the written values came from",
            "",
            f"{c['typed']} typed by the bot, {c['selected']} chosen by the bot from a list, "
            f"{c['derived']} set by the app.",
            "",
            "| Table | Row | Column | Came from |",
            "|---|---|---|---|",
        ]
        for b in self.bindings:
            src = {"typed": f"typed into {b.source}", "selected": f"chosen in {b.source}",
                   "derived": "the app"}[b.kind]  # fmt: skip
            lines.append(f"| `{b.table}` | `{b.key}` | `{b.column}` | {src} |")
        if self.not_stored:
            lines += ["", "Entered by the bot but found in no written column (the app may code, "
                      "reformat or ignore them):", ""]  # fmt: skip
            for e in self.not_stored:
                lines.append(f"- step {e.seq}: {e.kind} into {e.field or e.selector}")
        return "\n".join(lines) + "\n"


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def entered(trace: BotTrace) -> list[Entered]:
    """What the bot typed or selected, as logged; redacted values are left out."""
    out = []
    for s in trace.steps:
        if s.action in ENTERED and not s.redacted and _norm(s.value) is not None:
            out.append(Entered(ENTERED[s.action], str(s.value), s.field, s.selector, s.seq))
    return out


def combined(traces: list[BotTrace]) -> BotTrace:
    """Several runs in one session, as one trace (steps renumbered in order)."""
    steps = []
    for t in traces:
        for s in t.steps:
            steps.append(s.model_copy(update={"seq": len(steps) + 1}))
    first = traces[0] if traces else BotTrace(run_id="none")
    return first.model_copy(update={"steps": steps})


def _written(change: dict) -> dict[str, Any]:
    """Column -> value the change left in the row (inserts: every column; updates: the changed)."""
    after = change.get("after") or {}
    if change.get("op") == "insert":
        return dict(after)
    if change.get("op") == "update":
        return {c: after.get(c) for c in change.get("columns") or []}
    return {}


def _parent_rows(column: str, rows: dict[str, list[dict]]) -> list[dict]:
    """Rows of the table a `<name>_id` / `<name>_code` column points at, by naming convention."""
    lower = column.lower()
    for suffix in ("_id", "_code", "_no"):
        if lower.endswith(suffix) and len(lower) > len(suffix):
            stem = lower[: -len(suffix)]
            for name, table_rows in rows.items():
                if name.lower() in (stem, stem + "s", stem + "es", stem.rstrip("y") + "ies"):
                    return table_rows
    return []


def _coded(column: str, value: Any, candidates: list[Entered], rows) -> Entered | None:
    """The entered label whose row in the parent table has this code: `Payments` -> 3."""
    want = _norm(value)
    for row in _parent_rows(column, rows or {}):
        if want not in {
            _norm(v) for k, v in row.items() if k.lower() in ("id", "code", column.lower())
        }:
            continue
        texts = {_norm(v) for v in row.values() if isinstance(v, str)}
        hit = next((e for e in candidates if _norm(e.value) in texts), None)
        if hit:
            return hit
    return None


def bind(trace: BotTrace, changes: list[dict], rows: dict | None = None) -> Provenance:
    """`changes` are a session's detail changes (with `after` values); `rows`, the lab's data
    after the run, lets a chosen label be found behind the code the app stored."""
    values = entered(trace)
    by_value: dict[str, list[Entered]] = {}
    for e in values:
        by_value.setdefault(_norm(e.value), []).append(e)
    used: set[int] = set()
    out = Provenance()
    for change in changes:
        for column, value in _written(change).items():
            match = by_value.get(_norm(value) or "\0")
            if match:
                e = match[0]
                used.add(e.seq)
                out.bindings.append(Binding(change["table"], str(change.get("key")), column,
                                            e.kind, e.field or e.selector))  # fmt: skip
            elif (hit := _coded(column, value, values, rows)) is not None:
                used.add(hit.seq)
                out.bindings.append(Binding(
                    change["table"], str(change.get("key")), column, hit.kind,
                    f"{hit.field or hit.selector} (stored as its code {value})",
                ))  # fmt: skip
            else:
                out.bindings.append(
                    Binding(change["table"], str(change.get("key")), column, "derived")
                )
    out.not_stored = [e for e in values if e.seq not in used]
    return out
