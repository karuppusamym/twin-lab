"""Fidelity replay (Prod Echo I-22a): replay a recording against a running demo app and compare,
after each step, the live DOM with the recorded after-snapshot.

Compared: the set of interactive elements (tag, id and classes), visibility, enabled state,
option lists (texts) and value format (shape only, so data values and masked values compare by
shape). Text content is not compared: it is data. A step fails with the element named.

The replay is fail-closed. Each step's target is found from its ranked locator candidates and
must match exactly one element (labctl.locate.resolve); values come from the bindings
(labctl.behaviour.bindings). A step whose target is missing or ambiguous, that needs a value no
binding gives, or whose action this replay cannot perform is BLOCKED, and the replay stops there.
The report counts what happened to every step (replayed, implied by the step that caused it,
failed, skipped, unresolved, missing value, unsupported) and, given the actions a flow is
expected to have, which of them the recording lacks. Its status is:

- `pass`: every step ran, none failed or was skipped, every target resolved, every value was
  bound, and every expected action was captured (never from zero steps run);
- `fail`: a step's action failed or its page differs, or an expected action was not captured;
- `blocked`: a step could not be run as recorded (and the steps after it were not run).

`compare_dom` works on two snapshots alone, so a replay result can be checked without a live app.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from labctl.behaviour import bindings as bindings_mod
from labctl.behaviour import dom as domlib
from labctl.botlog.recording import Recording, RecStep
from labctl.botlog.recording import load as load_recording

Check = Literal[
    "missing", "extra", "visibility", "enabled", "options", "value_format", "action", "target",
    "value", "url", "unsupported",
]  # fmt: skip
SUPPORTED = {
    "goto",
    "click",
    "dblclick",
    "type",
    "select",
    "submit",
    "keypress",
    "check",
    "uncheck",
}


class Difference(BaseModel):
    step: int
    element: str
    check: Check
    expected: Any = None
    actual: Any = None

    def text(self) -> str:
        return (
            f"step {self.step}: {self.element} {self.check}"
            f" (recorded: {self.expected!r}, live: {self.actual!r})"
        )


class StepReport(BaseModel):
    seq: int
    action: str
    selector: str | None = None
    url: str | None = None
    status: Literal["pass", "fail", "skipped", "blocked"]
    differences: list[Difference] = []
    note: str | None = None
    target: str | None = None  # the candidate that found the target
    # replayed | implied | failed | not_run | unresolved | missing_value | unsupported
    outcome: str | None = None
    compared: bool = False


class Coverage(BaseModel):
    """What happened to the recording's steps, and which expected actions it lacks."""

    steps: int = 0
    replayed: int = 0  # ran (implied ones included)
    implied: int = 0  # a submit or navigation the step before caused: checked, not repeated
    compared: int = 0  # with a recorded snapshot to compare
    failed: int = 0
    skipped: int = 0  # not run (after a blocked step, or after a failure with stop_on_fail)
    unresolved: int = 0
    missing_value: int = 0
    unsupported: int = 0
    expected: int | None = None  # actions the flow is declared to have (None: not declared)
    captured: int = 0  # semantic actions in the recording
    missing_expected: list[str] = []


class FidelityReport(BaseModel):
    recording: str
    base_url: str | None = None
    status: Literal["pass", "fail", "blocked"]
    steps: list[StepReport] = []
    coverage: Coverage | None = None
    problems: list[str] = []  # why the result is not pass, first one first

    def first_failure(self) -> Difference | None:
        for s in self.steps:
            if s.differences:
                return s.differences[0]
        return None

    def markdown(self) -> str:
        lines = [f"# Fidelity: {self.recording}", "", f"**Result: {self.status}**", ""]
        c = self.coverage
        if c is not None:
            expected = "not declared" if c.expected is None else str(c.expected)
            lines += [
                "| Steps | Replayed | Implied | Compared | Failed | Skipped | Unresolved "
                "| Missing value | Unsupported | Expected actions | Captured | Not captured |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|",
                f"| {c.steps} | {c.replayed} | {c.implied} | {c.compared} | {c.failed} "
                f"| {c.skipped} | {c.unresolved} | {c.missing_value} | {c.unsupported} "
                f"| {expected} | {c.captured} | {len(c.missing_expected)} |",
                "",
            ]
        if self.problems:
            lines += ["## Why not pass", ""] + [f"- {p}" for p in self.problems] + [""]
        lines += ["| Step | Action | Selector | Target | Result |", "|---|---|---|---|---|"]
        for s in self.steps:
            target = (s.target or "").replace("|", "/")
            lines.append(f"| {s.seq} | {s.action} | {s.selector or ''} | {target} | {s.status} |")
        diffs = [d for s in self.steps for d in s.differences]
        if diffs:
            lines += ["", "## Differences", ""] + [f"- {d.text()}" for d in diffs]
        return "\n".join(lines) + "\n"


def action_label(step: RecStep) -> str:
    """A step as a semantic action: `keypress:Enter`, `keypress:Control+s`, `goto:reload`,
    `check`, `dblclick`..."""
    if step.action == "keypress":
        return "keypress:" + "+".join([*step.modifiers, step.key or "?"])
    if step.action == "goto" and step.nav == "reload":
        return "goto:reload"
    return step.action


def captured_actions(rec: Recording) -> list[str]:
    return [action_label(s) for s in rec.steps]


def missing_actions(expected: list[str], captured: list[str]) -> list[str]:
    """The expected actions the captured ones lack, matched in order. `keypress` matches any key
    press; `keypress:Enter` only that one."""
    out, i = [], 0
    for want in expected:
        j = i
        while j < len(captured) and not (
            captured[j] == want or (":" not in want and captured[j].split(":")[0] == want)
        ):
            j += 1
        if j < len(captured):
            i = j + 1
        else:
            out.append(want)
    return out


def reports_dir(root: Path, app_id: str) -> Path:
    from labctl import lab

    return lab.state_dir(root) / "fidelity" / app_id


def save(root: Path, app_id: str, report: FidelityReport) -> str:
    """Keep a replay's report where the portal shows it and readiness reads it. Returns its name
    (`<recording>--<UTC stamp>`)."""
    from datetime import UTC, datetime

    out = reports_dir(root, app_id)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{Path(report.recording).name}--{stamp}"
    (out / f"{name}.md").write_text(report.markdown(), encoding="utf-8", newline="\n")
    (out / f"{name}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return name


def latest(root: Path, app_id: str) -> tuple[str, FidelityReport] | None:
    """The newest replay report kept for the app, or None."""
    folder = reports_dir(root, app_id)
    found = sorted(folder.glob("*--*.json"), key=lambda p: p.stem.rsplit("--", 1)[-1])
    for path in reversed(found):
        try:
            return path.stem, FidelityReport.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
    return None


def _data_row(sig: str) -> bool:
    """An element repeated once per record: its id holds a masked value (`loan-L-999999`). How
    many there are depends on the data, which a demo app never shares with the real one."""
    return "#" in sig and "9" in sig.split("#", 1)[1].split(".")[0]


def compare_dom(
    expected_html: str,
    actual_html: str,
    step: int = 0,
    supplied: frozenset[str] | set[str] = frozenset(),
) -> list[Difference]:
    """Differences between a recorded after-snapshot and a live one, each naming the element.

    A per-record element counts as the same when both pages have at least one. `supplied` names
    the fields (element keys) whose value a person typed for the replay: their value format says
    nothing about the app, so it is not compared.
    """
    exp, act = domlib.snapshot(expected_html), domlib.snapshot(actual_html)
    diffs: list[Difference] = []
    ce, ca = Counter(exp.signatures), Counter(act.signatures)
    missing = ce - ca
    for sig in list(missing):
        if _data_row(sig) and ca[sig]:
            del missing[sig]
            continue
        diffs.append(
            Difference(step=step, element=sig, check="missing", expected=ce[sig], actual=ca[sig])
        )
    for sig in ca - ce:
        if _data_row(sig) and ce[sig]:
            continue
        diffs.append(
            Difference(step=step, element=sig, check="extra", expected=ce[sig], actual=ca[sig])
        )
    missing_ids = {s.split(".")[0].split("#", 1)[-1] for s in missing if "#" in s}
    for key, e in exp.elements.items():
        a = act.elements.get(key)
        if e.interactive and e.id in missing_ids:
            continue
        ev, av = e.visible, bool(a and a.visible)
        if ev != av:
            diffs.append(
                Difference(step=step, element=key, check="visibility", expected=ev, actual=av)
            )
            continue
        if a is None or not ev:
            continue
        if e.interactive and e.enabled != a.enabled:
            diffs.append(
                Difference(
                    step=step, element=key, check="enabled", expected=e.enabled, actual=a.enabled
                )
            )
        if e.options is not None and e.options != a.options:
            diffs.append(
                Difference(
                    step=step, element=key, check="options", expected=e.options, actual=a.options
                )
            )
        if e.tag in domlib.FORM_TAGS and e.value is not None and key not in supplied:
            es, as_ = domlib.shape(e.value), domlib.shape(a.value)
            if es != as_:
                diffs.append(
                    Difference(
                        step=step, element=key, check="value_format", expected=es, actual=as_
                    )
                )
    return diffs


def compare_files(expected: Path, actual: Path, step: int = 0) -> list[Difference]:
    return compare_dom(
        Path(expected).read_text(encoding="utf-8"), Path(actual).read_text(encoding="utf-8"), step
    )


def _rebase(url: str, base_url: str | None) -> str:
    if not base_url:
        return url
    b, u = urlsplit(base_url), urlsplit(url)
    return urlunsplit((b.scheme, b.netloc, u.path, u.query, u.fragment))


SETTLE_MAX_MS = 30000


def _track(page) -> None:
    """Count requests in flight: `networkidle` alone returns at once on a page that was idle
    before, so a slow login's POST would be outrun by the next step."""
    page._tl_inflight = 0

    def start(_r):
        page._tl_inflight += 1

    def end(_r):
        page._tl_inflight = max(0, page._tl_inflight - 1)

    page.on("request", start)
    page.on("requestfinished", end)
    page.on("requestfailed", end)


def _settle(page, settle_ms: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001 - a page that never goes idle is still compared
        pass
    if not hasattr(page, "_tl_inflight"):
        page.wait_for_timeout(settle_ms)
        return
    quiet = waited = 0
    while quiet < settle_ms and waited < SETTLE_MAX_MS:
        page.wait_for_timeout(50)
        waited += 50
        quiet = quiet + 50 if page._tl_inflight == 0 else 0


class MissingValue(Exception):  # noqa: N818 - reported as BLOCKED
    pass


class Unsupported(Exception):  # noqa: N818 - reported as BLOCKED
    pass


def _bindings(rec: Recording, given, values: dict[str, str] | None) -> bindings_mod.Bindings:
    """The bindings file (given, or beside the recording), with older per-selector values for
    the steps it does not bind."""
    if isinstance(given, bindings_mod.Bindings):
        bnd = given
    elif given is not None:
        bnd = bindings_mod.load(Path(given))
    else:
        bnd = bindings_mod.load(rec.path / bindings_mod.FILE)
    return bnd.merged(bindings_mod.from_values(rec, values))


def _values(rec: Recording, bnd: bindings_mod.Bindings) -> dict[int, str]:
    """The value each step types or selects: bound, or held by the recording itself."""
    out: dict[int, str] = {}
    for step in rec.steps:
        if step.action not in bindings_mod.VALUED:
            continue
        value = bnd.values.get(step.seq)
        if value is None:
            value = bindings_mod.recorded_value(rec, step)
        if value is not None:
            out[step.seq] = value
    return out


def replay(
    recording: Recording | Path,
    base_url: str | None = None,
    values: dict[str, str] | None = None,
    headless: bool = True,
    settle_ms: int = 150,
    stop_on_fail: bool = False,
    *,
    bindings: bindings_mod.Bindings | Path | None = None,
    expected: list[str] | None = None,
    storage_state: dict | str | Path | None = None,
    setup: Callable | None = None,
    after: Callable | None = None,
    resolve_timeout_ms: int = 5000,
) -> FidelityReport:
    """Replay the recording's steps with Playwright and compare each step's after-snapshot.

    Values come from `bindings` (a Bindings, or a bindings file; by default `bindings.yaml`
    beside the recording). `values` are the older per-selector values (`"#loanQuery"`, or
    `"6:#loanQuery"` for step 6 only), used for steps the bindings do not bind. `expected` lists
    the semantic actions the flow should have (see `action_label`): one the recording lacks fails
    the replay. `storage_state` is the browser data the replay starts from (the replica's declared
    baseline); `setup(context)` runs before the first step and `after(page)` after the last.
    """
    from playwright.sync_api import sync_playwright

    from labctl.locate import Unresolved

    rec = recording if isinstance(recording, Recording) else load_recording(Path(recording))
    _infer_causes(rec)
    bnd = _bindings(rec, bindings, values)
    vals = _values(rec, bnd)
    supplied = {k for k in (domlib.key_of(_selector_of(name)) for name in values or {}) if k}
    supplied |= bindings_mod.supplied_keys(rec, bnd)
    reports: list[StepReport] = []
    problems: list[str] = []
    stopped = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            state = str(storage_state) if isinstance(storage_state, Path) else storage_state
            context = browser.new_context(viewport={"width": 1280, "height": 900},
                                          storage_state=state)  # fmt: skip
            context.add_init_script(domlib.SHADOW_HOOK_JS)  # closed shadow roots, as recorded
            if setup is not None:
                setup(context)
            page = context.new_page()
            _track(page)
            for step in rec.steps:
                report = StepReport(seq=step.seq, action=step.action, selector=step.selector,
                                    url=step.url, status="pass")  # fmt: skip
                reports.append(report)
                if stopped:
                    report.status, report.outcome = "skipped", "not_run"
                    report.note = "not run: the replay stopped at an earlier step"
                    continue
                expected_html = rec.dom(step)
                where = step.selector or step.url or "?"
                try:
                    report.target, implied = _act(page, step, base_url, vals, bnd,
                                                  resolve_timeout_ms)  # fmt: skip
                except Unresolved as exc:
                    report.status = "blocked"
                    report.outcome = "missing_value" if exc.missing_binding else "unresolved"
                    report.note = str(exc)
                    report.differences.append(Difference(step=step.seq, element=where,
                                                         check="target", expected=step.action,
                                                         actual=str(exc)[:500]))  # fmt: skip
                    problems.append(f"step {step.seq} ({step.action}) BLOCKED: {exc}")
                    stopped = True
                    continue
                except MissingValue as exc:
                    report.status, report.outcome, report.note = (
                        "blocked",
                        "missing_value",
                        str(exc),
                    )
                    report.differences.append(Difference(step=step.seq, element=where,
                                                         check="value", expected=step.action,
                                                         actual=str(exc)))  # fmt: skip
                    problems.append(f"step {step.seq} ({step.action}) BLOCKED: {exc}")
                    stopped = True
                    continue
                except Unsupported as exc:
                    report.status, report.outcome, report.note = "blocked", "unsupported", str(exc)
                    report.differences.append(Difference(step=step.seq, element=where,
                                                         check="unsupported", expected=step.action,
                                                         actual=str(exc)))  # fmt: skip
                    problems.append(f"step {step.seq} ({step.action}) BLOCKED: {exc}")
                    stopped = True
                    continue
                except Exception as exc:  # noqa: BLE001 - reported as the step's failure
                    report.status, report.outcome = "fail", "failed"
                    report.differences.append(
                        Difference(
                            step=step.seq,
                            element=where,
                            check="url" if isinstance(exc, _WrongAddress) else "action",
                            expected=step.action,
                            actual=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}",
                        )
                    )
                    if stop_on_fail:
                        stopped = True
                    continue
                report.outcome = "implied" if implied else "replayed"
                _settle(page, settle_ms)
                if expected_html is None:
                    report.note = "ran; no snapshot was recorded after this step to compare"
                    continue
                report.compared = True
                report.differences = compare_dom(
                    expected_html, domlib.page_html(page), step.seq, supplied
                )
                if report.differences:  # ran, and the page differs
                    report.status = "fail"
                    if stop_on_fail:
                        stopped = True
            if after is not None:
                after(page)
        finally:
            browser.close()
    return _result(rec, base_url, reports, problems, expected)


def _result(rec, base_url, reports, problems, expected) -> FidelityReport:
    count = Counter(r.outcome for r in reports)
    captured = captured_actions(rec)
    cov = Coverage(
        steps=len(reports),
        replayed=count["replayed"] + count["implied"],
        implied=count["implied"],
        compared=sum(1 for r in reports if r.compared),
        failed=sum(1 for r in reports if r.status == "fail"),
        skipped=count["not_run"],
        unresolved=count["unresolved"],
        missing_value=count["missing_value"],
        unsupported=count["unsupported"],
        expected=None if expected is None else len(expected),
        captured=len(captured),
        missing_expected=missing_actions(list(expected), captured) if expected else [],
    )
    problems = list(problems)
    if cov.missing_expected:
        problems.append("expected actions not in the recording: " + ", ".join(cov.missing_expected))
    if cov.failed:
        first = next(r for r in reports if r.status == "fail")
        problems.append(f"{cov.failed} step(s) failed, first step {first.seq} ({first.action})")
    if cov.replayed == 0:
        problems.append("no step was replayed: a replay of nothing never passes")
    if cov.unresolved or cov.missing_value or cov.unsupported or cov.replayed == 0:
        status = "blocked"
    elif cov.failed or cov.skipped or cov.missing_expected:
        status = "fail"
    else:
        status = "pass"
    return FidelityReport(recording=rec.id, base_url=base_url, status=status, steps=reports,
                          coverage=cov, problems=problems)  # fmt: skip


def _recorded_value(rec: Recording, step) -> str | None:
    return bindings_mod.recorded_value(rec, step)


def _selector_of(name: str) -> str:
    """The selector of a `values` key: `"6:#loanQuery"` -> `"#loanQuery"`."""
    return bindings_mod.selector_of(name)


def _value_for(rec: Recording, step, values: dict[str, str]) -> str | None:
    """The older per-selector lookup: step-specific, then for the field, then as recorded."""
    for key in (f"{step.seq}:{step.selector}", step.selector):
        if key in values:
            return values[key]
    if getattr(step, "value", None) is not None:
        return step.value
    html = rec.dom(step)
    key = domlib.key_of(step.selector)
    if html is None or key is None:
        return None
    el = domlib.snapshot(html).elements.get(key)
    if el is None:
        return None
    return el.selected if el.tag == "select" else el.value


def _infer_causes(rec: Recording) -> None:
    """An older recording has no `caused_by`. Its recorder kept a click and the submit (or
    navigation) it caused in one batch, the snapshot after the last of them only: such a submit
    or navigation is implied by the click, as a newer recording says, not performed again."""
    if bindings_mod.new_format(rec):
        return
    for prev, step in zip(rec.steps, rec.steps[1:], strict=False):
        if step.caused_by is not None or prev.dom is not None or not (prev.ts and step.ts):
            continue
        gap = (step.ts - prev.ts).total_seconds()
        if step.action == "submit" and prev.action == "click" and 0 <= gap < 0.15:
            step.caused_by = prev.caused_by or prev.seq
        elif step.action == "goto" and prev.action in ("click", "submit") and 0 <= gap < 1.5:
            step.caused_by = prev.caused_by or prev.seq


class _WrongAddress(AssertionError):
    pass


def _wait_address(page, url: str, timeout_ms: int = 5000) -> None:
    """A navigation the step before caused (or the app made by itself) is not made again: the
    replica must arrive at the same address on its own."""
    import time

    deadline = time.monotonic() + timeout_ms / 1000
    while page.url != url:
        if time.monotonic() >= deadline:
            raise _WrongAddress(f"expected {url}, the page is at {page.url}")
        page.wait_for_timeout(50)


def _record_title(step: RecStep, values: dict[int, str], bnd: bindings_mod.Bindings) -> str | None:
    """The title of the record a step acts on: named in the bindings, or the value bound to the
    step that typed it."""
    from labctl.locate import record_of

    if step.seq in bnd.records:
        return bnd.records[step.seq]
    cand = record_of(step)
    if cand is not None and cand.get("text_from") is not None:
        return values.get(int(cand["text_from"]))
    return None


def _act(page, step: RecStep, base_url: str | None, values: dict[int, str],
         bnd: bindings_mod.Bindings, timeout_ms: int = 5000) -> tuple[str | None, bool]:  # fmt: skip
    """Perform one step. Returns (what found the target, whether the step was implied by the one
    before it rather than performed)."""
    from labctl.locate import Unresolved, resolve

    if step.action == "goto":
        url = _rebase(step.url, base_url)
        if step.caused_by is not None or step.nav in ("push", "replace"):
            _wait_address(page, url)
            why = f"step {step.caused_by}" if step.caused_by is not None else "the page itself"
            return f"navigation caused by {why}", True
        if step.nav == "reload" and page.url not in ("", "about:blank"):
            page.reload()
            _wait_address(page, url)
        else:
            page.goto(url)
        return None, False
    if step.action == "submit" and step.caused_by is not None:
        return f"submitted by step {step.caused_by}", True
    if step.action not in SUPPORTED:
        raise Unsupported(f"this replay cannot perform {step.action!r}")
    value = None
    if step.action in bindings_mod.VALUED:
        value = values.get(step.seq)
        if value is None:
            shape = f" (recorded shape {step.typed_shape!r})" if step.typed_shape else ""
            raise MissingValue(f"no binding gives the value for step {step.seq} "
                               f"({step.selector}){shape}: add it to bindings.yaml")  # fmt: skip
    loc, found = resolve(page, step, _record_title(step, values, bnd), timeout_ms)
    if loc is None and step.action != "keypress":
        raise Unresolved("the step names no element", [])
    if step.action in ("click", "dblclick") and not loc.is_visible():
        # A control shown only while its record is under the pointer (a list item's delete
        # button): a person points at the record first; so does the replay.
        row = loc.locator("xpath=ancestor::*[self::li or self::tr or @role='row'][1]")
        if row.count() == 1:
            row.hover(timeout=10000)
    if step.action == "type":
        loc.fill(value, timeout=10000)
    elif step.action == "select":
        try:
            loc.select_option(label=value, timeout=10000)
        except Exception:  # noqa: BLE001 - recorded as text; fall back to the option value
            loc.select_option(value=value, timeout=10000)
    elif step.action == "click":
        loc.click(timeout=10000)
    elif step.action == "dblclick":
        loc.dblclick(timeout=10000)
    elif step.action in ("check", "uncheck"):
        if loc.is_checked(timeout=10000) != (step.action == "check"):
            loc.click(timeout=10000)
    elif step.action == "keypress":
        combo = "+".join([*step.modifiers, step.key or ""])
        if not step.key:
            raise Unsupported("a key press without its key")
        if loc is None:
            page.keyboard.press(combo)
        else:
            loc.press(combo, timeout=10000)
    elif step.action == "submit":
        loc.evaluate(
            "el => (el.form || el).requestSubmit ? (el.form || el).requestSubmit() : el.click()"
        )
    return found, False
