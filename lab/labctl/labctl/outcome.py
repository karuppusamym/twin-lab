"""Did a session reach the business result its automation exists for?

`verdict.py` answers a different question: did the run change only what it may change. A run
that changes nothing passes that check. An automation can therefore also declare `outcome:`, a
list of named acceptance cases, and a session is judged against one of them:

- `changes`: the run must make at least the listed changes (table, op, row counts, columns an
  update must touch, final values to `set`).
- `read_only`: the run must change no rows.
- `rejected` / `failed_run`: the bot run is expected to end unsuccessfully and leave data unchanged.

Statuses: MET, NOT_MET, CANNOT_CHECK (something needed was not recorded, never reported as met)
and NOT_DECLARED. The outcome never alters the allowed-change verdict; both are reported side by
side.

What a session records: the session document lists table, op, key and, for updates, the changed
columns. Final values ("after": the new row for an insert, the changed columns for an update)
are only in the session detail the lab agent keeps; `with_values` merges them in. An update's
`after` holds the changed columns only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from labctl.models import OutcomeCase, OutcomeRequire
from labctl.verdict import _norm

MET, NOT_MET, CANNOT_CHECK, NOT_DECLARED = "MET", "NOT_MET", "CANNOT_CHECK", "NOT_DECLARED"


@dataclass
class Outcome:
    status: str
    case: str | None = None
    kind: str | None = None
    when: str | None = None
    unmet: list[str] = field(default_factory=list)  # requirements the session did not meet
    unchecked: list[str] = field(default_factory=list)  # requirements that could not be checked
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "case": self.case,
            "kind": self.kind,
            "when": self.when,
            "unmet": self.unmet,
            "unchecked": self.unchecked,
            "notes": self.notes,
        }

    def summary(self) -> str:
        """One line for reports."""
        if self.status == NOT_DECLARED:
            return "no outcome declared"
        head = f"{self.status.replace('_', ' ')} (case {self.case})"
        why = self.unmet + self.unchecked + self.notes
        return head + (": " + "; ".join(why) if why else "")


def select_case(cases: list[OutcomeCase] | None, name: str | None = None) -> OutcomeCase | None:
    """The named case; with no name, the case called `default`, else the first. None when the
    automation declares no outcome. An unknown name is an error, not a silent fallback."""
    if not cases:
        if name:
            raise ValueError(f"no outcome case {name!r}: the automation declares no outcome")
        return None
    if name:
        for c in cases:
            if c.name == name:
                return c
        raise ValueError(f"no outcome case {name!r}; cases: {', '.join(c.name for c in cases)}")
    return next((c for c in cases if c.name == "default"), cases[0])


def with_values(
    changes: list[dict[str, Any]], detail_changes: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """`changes` (session document) with `after` taken from the session detail where it has it."""
    pool: dict[tuple, list[dict[str, Any]]] = {}
    for d in detail_changes or []:
        pool.setdefault((_norm(d.get("table", "")), d.get("op"), str(d.get("key"))), []).append(d)
    out = []
    for c in changes:
        mate = (pool.get((_norm(c.get("table", "")), c.get("op"), str(c.get("key")))) or [None])[0]
        merged = dict(c)
        if mate is not None:
            if "after" in mate and "after" not in merged:
                merged["after"] = mate["after"]
            if not merged.get("columns") and mate.get("columns"):
                merged["columns"] = mate["columns"]
        out.append(merged)
    return out


def _text(value: Any) -> str:
    return "NULL" if value is None else str(value)


def _describe(req: OutcomeRequire) -> str:
    what = f"{req.op} of {req.table}"
    if req.columns:
        what += f" touching {', '.join(req.columns)}"
    if req.set:
        what += " setting " + ", ".join(f"{k}={v}" for k, v in req.set.items())
    return what


def _check(req: OutcomeRequire, changes: list[dict[str, Any]], out: Outcome) -> None:
    mine = [
        c
        for c in changes
        if _norm(c.get("table", "")) == _norm(req.table) and c.get("op") == req.op
    ]
    bound = f"at least {req.min_rows}" + (
        f" and at most {req.max_rows}" if req.max_rows is not None else ""
    )
    if req.max_rows == req.min_rows:
        bound = f"exactly {req.min_rows}"

    other = ""
    cols = {x.lower() for x in req.columns}
    if cols:
        recorded = [c for c in mine if c.get("columns")]
        if mine and not recorded:
            out.unchecked.append(
                f"expected {_describe(req)}; changed columns of {req.table} were not recorded"
            )
            return
        seen = {x.lower() for c in recorded for x in c["columns"]}
        if mine and not cols <= seen:
            missing = ", ".join(sorted(cols - seen))
            out.unmet.append(
                f"expected {_describe(req)}; columns {missing} were not among those changed"
            )
            return

    if req.set:
        with_after = [c for c in mine if isinstance(c.get("after"), dict)]
        if mine and not with_after:
            out.unchecked.append(
                f"expected {_describe(req)}; cannot check values, the session recorded no "
                f"new values"
            )
            return
        wanted = {k.lower(): v for k, v in req.set.items()}

        def holds(c: dict[str, Any]) -> bool:
            after = {k.lower(): v for k, v in c["after"].items()}
            return all(k in after and _text(after[k]) == v for k, v in wanted.items())

        before = len(mine)
        mine = [c for c in with_after if holds(c)]
        if before and not mine and req.min_rows >= 1:
            other = f" ({before} {req.op}(s) of {req.table} had other values)"

    n = len(mine)
    if n < req.min_rows:
        out.unmet.append(f"expected {bound} {_describe(req)}; saw {n}{other}")
    elif req.max_rows is not None and n > req.max_rows:
        out.unmet.append(f"expected {bound} {_describe(req)}; saw {n}")


def judge(
    changes: list[dict[str, Any]],
    session: dict[str, Any] | None,
    case: OutcomeCase | None,
    run_ok: bool | None = None,
) -> Outcome:
    """The session's recorded `changes` against one outcome case. `run_ok`: whether the bot runs
    ended successfully, when the caller knows (needed for `rejected` and `failed_run`)."""
    if case is None:
        return Outcome(NOT_DECLARED, notes=["the automation declares no `outcome:`"])
    out = Outcome(MET, case=case.name, kind=case.kind, when=case.when)
    if case.kind == "changes":
        for req in case.requires:
            _check(req, changes, out)
    else:
        if changes:
            kinds = sorted({f"{c.get('op')} {c.get('table')}" for c in changes})
            what = {
                "read_only": "expected no row changes",
                "rejected": "expected the run to be rejected and leave data unchanged",
                "failed_run": "expected the failed run to leave data unchanged",
            }[case.kind]
            out.unmet.append(f"{what}; saw {len(changes)} ({', '.join(kinds[:5])})")
        if case.kind in ("rejected", "failed_run"):
            if run_ok is True:
                out.unmet.append("expected the bot run to end unsuccessfully; it succeeded")
            elif run_ok is None:
                out.unchecked.append(
                    "expected the bot run to end unsuccessfully; this check does not know how "
                    "the run ended"
                )
    if out.unmet:
        out.status = NOT_MET
    elif out.unchecked:
        out.status = CANNOT_CHECK
    return out


def for_session(
    root,
    app_id: str,
    session_id: str,
    automation_id: str | None = None,
    case_name: str | None = None,
    run_ok: bool | None = None,
) -> tuple[Outcome, str | None]:
    """(outcome, automation id) for a stored session, judged against the automation's case."""
    from labctl import lab as lab_mod
    from labctl import repo
    from labctl import verdict as verdict_mod

    agent = lab_mod.agent(root, app_id)
    session, changes = verdict_mod.session_changes(agent, session_id)
    automation_id = automation_id or session.get("automation")
    if not automation_id:
        return Outcome(NOT_DECLARED, notes=["the session names no automation"]), None
    model, problems = repo.load(
        root / "automations" / automation_id / "automation.yaml", "automation"
    )
    if model is None:
        note = f"automation {automation_id} cannot be read: " + "; ".join(map(str, problems))
        return Outcome(NOT_DECLARED, notes=[note]), automation_id
    case = select_case(model.outcome, case_name)
    base = app_id.partition(".")[0]
    mine = [c for c in changes if c.get("app") in (None, base)]
    if case is not None and case.kind == "changes" and any(r.set for r in case.requires):
        detail = agent.session_detail(session_id) or {}
        mine = with_values(mine, detail.get("changes"))
    return judge(mine, session, case, run_ok), automation_id
