"""Parity (Prod Echo I-24): does another run do what the bot does?

Compares a bot trace (production or harness, the golden run) with another run: an agent's
recording (as a BotTrace, see `trace.from_recording`) or a lab Session's request log. Checks:

| Check | Fail | Warn |
|---|---|---|
| screens in order | a screen of the bot never visited | an extra screen; screens in another order |
| fields touched | a field the bot types, selects or clicks is never touched | an extra field |
| values typed | a typed or selected value differs (after `mask`), both shown | |
| requests | a non-asset request pattern the bot causes is missing | an extra one |
| outcome | a row change of the bot's session is missing (Session `changes`) | an extra change |

A check without data on both sides is `skipped`, never passed. The run is `unverified` when the
fields could not be compared at all.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from labctl.botlog.models import BotTrace, TraceStep
from labctl.botlog.recording import is_asset, request_key
from labctl.models import Session

Severity = Literal["fail", "warn", "info"]
TOUCHING = ("type", "select", "click", "submit")
VALUED = ("type", "select")


class Finding(BaseModel):
    check: str
    severity: Severity
    what: str
    field: str | None = None
    screen: str | None = None
    expected: Any = None
    actual: Any = None


class CheckResult(BaseModel):
    check: str
    status: Literal["pass", "warn", "fail", "skipped"]
    note: str | None = None


class ParityReport(BaseModel):
    base: str
    other: str
    status: Literal["pass", "warn", "fail", "unverified"]
    checks: list[CheckResult] = []
    findings: list[Finding] = []

    def markdown(self) -> str:
        lines = [f"# Parity: {self.base} vs {self.other}", "", f"**Result: {self.status}**", ""]
        lines += ["| Check | Result | Note |", "|---|---|---|"]
        for c in self.checks:
            lines.append(f"| {c.check} | {c.status} | {c.note or ''} |")
        if self.findings:
            lines += ["", "## Differences", ""]
            for f in self.findings:
                where = f" on {f.screen}" if f.screen else ""
                both = ""
                if f.expected is not None or f.actual is not None:
                    both = f" (bot: `{f.expected}`, other: `{f.actual}`)"
                lines.append(f"- **{f.severity}** [{f.check}] {f.what}{where}{both}")
        return "\n".join(lines) + "\n"


def _target(s: TraceStep) -> str:
    return s.field or s.selector or "?"


def _screens(trace: BotTrace) -> list[str]:
    out: list[str] = []
    for s in trace.steps:
        if s.screen and (not out or out[-1] != s.screen):
            out.append(s.screen)
    return out


def _norm(key: str) -> str:
    """Drop the query marker (`GET /search*` -> `GET /search`): lab request logs keep no query."""
    return key[:-1] if key.endswith("*") and not key.endswith("/*") else key


def _requests(trace: BotTrace) -> set[str]:
    return {_norm(r) for s in trace.steps for r in s.requests}


def _session_requests(session: Session) -> set[str]:
    return {_norm(request_key(r.method, r.path)) for r in session.requests if not is_asset(r.path)}


def _groups(trace: BotTrace) -> dict[tuple[str | None, str], list[TraceStep]]:
    out: dict[tuple[str | None, str], list[TraceStep]] = {}
    for s in trace.steps:
        if s.action in TOUCHING:
            out.setdefault((s.screen, _target(s)), []).append(s)
    return out


def _same(a: TraceStep, b: TraceStep) -> bool:
    if a.field and b.field:
        return a.field == b.field
    return bool(a.selector and a.selector == b.selector)


def _find(step: TraceStep, screen: str | None, groups: dict) -> list[TraceStep]:
    """The other run's steps on the same field: same screen first, then any screen."""
    for (scr, _), steps in groups.items():
        if scr == screen and _same(step, steps[0]):
            return steps
    for steps in groups.values():
        if _same(step, steps[0]):
            return steps
    return []


def compare(
    base: BotTrace,
    other: BotTrace | Session,
    mask: Callable[[Any], Any] | None = None,
    base_session: Session | None = None,
    other_session: Session | None = None,
) -> ParityReport:
    """Compare `other` with the golden `base`. `mask` is applied to both values before comparing."""
    mask = mask or (lambda v: v)
    findings: list[Finding] = []
    checks: list[CheckResult] = []
    if isinstance(other, Session):
        other_session = other_session or other
        other_trace = None
        other_name = other.session_id
    else:
        other_trace = other
        other_name = other.run_id

    def result(check: str, note: str | None = None) -> None:
        sev = {f.severity for f in findings if f.check == check}
        status = "fail" if "fail" in sev else "warn" if "warn" in sev else "pass"
        checks.append(CheckResult(check=check, status=status, note=note))

    def skipped(check: str, note: str) -> None:
        checks.append(CheckResult(check=check, status="skipped", note=note))

    # screens in order
    if other_trace is not None:
        a, b = _screens(base), _screens(other_trace)
        for s in a:
            if s not in b:
                findings.append(
                    Finding(
                        check="screens", severity="fail", what=f"screen {s} never visited", screen=s
                    )
                )
        for s in b:
            if s not in a:
                findings.append(
                    Finding(check="screens", severity="warn", what=f"extra screen {s}", screen=s)
                )
        common_a = [s for s in a if s in b]
        common_b = [s for s in b if s in a]
        if common_a != common_b:
            findings.append(
                Finding(
                    check="screens",
                    severity="warn",
                    what="screens visited in another order",
                    expected=a,
                    actual=b,
                )
            )
        result("screens", " > ".join(a))
    else:
        skipped("screens", "the other run is a request log; no screens")

    # fields touched and values typed
    if other_trace is not None:
        mine = _groups(base)
        theirs = _groups(other_trace)
        for (screen, target), steps in mine.items():
            if not _find(steps[0], screen, theirs):
                findings.append(
                    Finding(
                        check="fields",
                        severity="fail",
                        what=f"field {target} never touched",
                        field=target,
                        screen=screen,
                    )
                )
        for (screen, target), steps in theirs.items():
            if not _find(steps[0], screen, mine):
                findings.append(
                    Finding(
                        check="fields",
                        severity="warn",
                        what=f"extra field {target}",
                        field=target,
                        screen=screen,
                    )
                )
        result("fields", f"{len(mine)} field(s) the bot touches")

        compared = not_compared = 0
        for (screen, target), steps in mine.items():
            ours = [s for s in steps if s.action in VALUED]
            other_steps = _find(steps[0], screen, theirs)
            others = [s for s in other_steps if s.action in VALUED]
            for s, o in zip(ours, others, strict=False):
                if s.redacted or o.redacted or s.value is None or o.value is None:
                    not_compared += 1
                    continue
                compared += 1
                if mask(s.value) != mask(o.value):
                    findings.append(
                        Finding(
                            check="values",
                            severity="fail",
                            what=f"field {target} has a different value",
                            field=target,
                            screen=screen,
                            expected=mask(s.value),
                            actual=mask(o.value),
                        )
                    )
        note = f"{compared} value(s) compared"
        if not_compared:
            note += f", {not_compared} not compared (redacted or not logged)"
        if compared:
            result("values", note)
        else:
            skipped("values", note)
    else:
        skipped("fields", "the other run is a request log; no fields")
        skipped("values", "the other run is a request log; no values")

    # requests
    ra = _requests(base) | (_session_requests(base_session) if base_session else set())
    if other_trace is not None:
        rb = _requests(other_trace)
    else:
        rb = set()
    if other_session is not None:
        rb |= _session_requests(other_session)
    if ra and rb:
        for r in sorted(ra - rb):
            findings.append(
                Finding(check="requests", severity="fail", what=f"request {r} never made")
            )
        for r in sorted(rb - ra):
            findings.append(Finding(check="requests", severity="warn", what=f"extra request {r}"))
        result("requests", f"{len(ra)} request pattern(s)")
    else:
        skipped("requests", "requests are not known for both runs")

    # outcome
    if base_session is not None and other_session is not None:
        ca = Counter((c.app, c.table, c.op) for c in base_session.changes)
        cb = Counter((c.app, c.table, c.op) for c in other_session.changes)
        for (app, table, op), n in (ca - cb).items():
            findings.append(
                Finding(
                    check="outcome",
                    severity="fail",
                    what=f"{n} {op} on {app}.{table} missing",
                    expected=ca[(app, table, op)],
                    actual=cb[(app, table, op)],
                )
            )
        for (app, table, op), n in (cb - ca).items():
            findings.append(
                Finding(
                    check="outcome",
                    severity="warn",
                    what=f"{n} extra {op} on {app}.{table}",
                    expected=ca[(app, table, op)],
                    actual=cb[(app, table, op)],
                )
            )
        result("outcome", f"{sum(ca.values())} row change(s) by the bot")
    else:
        skipped("outcome", "row changes need a lab Session for both runs")

    statuses = {c.check: c.status for c in checks}
    if any(c.status == "fail" for c in checks):
        status = "fail"
    elif statuses.get("fields") == "skipped":
        status = "unverified"
    elif any(c.status == "warn" for c in checks):
        status = "warn"
    else:
        status = "pass"
    return ParityReport(
        base=base.run_id, other=other_name, status=status, checks=checks, findings=findings
    )
