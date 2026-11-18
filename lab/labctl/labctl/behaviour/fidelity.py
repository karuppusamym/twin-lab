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
from labctl.behaviour.acceptance import CaseResult
from labctl.behaviour.judging import Category, Divergence, Requirements
from labctl.behaviour.visual import VisualResult
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
    # The replay's result, as current readers (portal, conformance, e2e) know it. With acceptance
    # cases declared it is also `fail` when a required case failed, `blocked` when one was blocked.
    status: Literal["pass", "fail", "blocked"]
    steps: list[StepReport] = []
    coverage: Coverage | None = None
    problems: list[str] = []  # why the result is not pass, first one first
    # Added for R05/R06/R08/R11; all default, so older reports still load.
    verdict: str | None = None  # PASS WITHIN DECLARED SCOPE | FAIL | BLOCKED (None: older report)
    verdict_reasons: list[str] = []
    categories: dict[str, Category] = {}  # capture, replay, behaviour, data, appearance
    cases: list[CaseResult] = []
    visual: list[VisualResult] = []
    visual_setup: dict | None = None
    baseline: dict | None = None  # what was declared and applied (names only, no values)
    requirements: Requirements | None = None
    first_divergence: Divergence | None = None
    fingerprint: dict | None = None  # what was compared (labctl.behaviour.durable)
    artifacts: str | None = None  # the folder with the replica's pictures and diff images

    def first_failure(self) -> Difference | None:
        for s in self.steps:
            if s.differences:
                return s.differences[0]
        return None

    def markdown(self) -> str:
        lines = [f"# Fidelity: {self.recording}", "", f"**Result: {self.status}**", ""]
        if self.verdict:
            lines += [f"**Status: {self.verdict}**", ""]
            lines += [f"- {r}" for r in self.verdict_reasons] + (
                [""] if self.verdict_reasons else []
            )
        if self.categories:
            lines += ["| Category | Result | Required passed / total | Optional passed / total "
                      "| Note |", "|---|---|---|---|---|"]  # fmt: skip
            for name, c in self.categories.items():
                lines.append(f"| {name} | {c.status} | {c.required_passed} / {c.required_total} "
                             f"| {c.optional_passed} / {c.optional_total} | {c.note or ''} |")  # fmt: skip
            lines.append("")
        if self.fingerprint:
            from labctl.behaviour.durable import describe

            lines += [describe(self.fingerprint), ""]
        if self.first_divergence:
            d = self.first_divergence
            lines += ["## First divergence", "", f"- {d.kind} {d.what} at step {d.step}"
                      + (f", locator {d.locator}" if d.locator else ""), f"- {d.state}"]  # fmt: skip
            for label, p in (("source", d.before), ("replica", d.after), ("diff", d.diff)):
                if p:
                    lines.append(f"- {label} picture: {p}")
            if d.requests:
                lines.append("- source requests: " + ", ".join(d.requests))
            lines.append("")
        if self.cases:
            lines += ["## Acceptance cases", "", "| Case | Category | Required | When | Check "
                      "| Result | Note |", "|---|---|---|---|---|---|---|"]  # fmt: skip
            for c in self.cases:
                note = (c.note or "").replace("|", "/")
                lines.append(f"| {c.id} | {c.category} | {'yes' if c.required else 'no'} "
                             f"| {c.after} | {c.check} | {c.status} | {note} |")  # fmt: skip
            lines.append("")
        if self.visual:
            lines += ["## Visual states", "", "| State | Viewport | Mode | Compared | Differing "
                      "| Ratio | Excluded | Masked | Result |",
                      "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
            for v in self.visual:
                lines.append(f"| {v.id} | {v.viewport} | {v.mode} | {v.compared} | {v.differing} "
                             f"| {v.ratio:.4%} | {v.excluded} | {v.masked} | {v.status} |")  # fmt: skip
            lines.append("")
        if self.requirements:
            r = self.requirements
            for label, items in (("Scope", r.scope), ("Excluded", r.excluded),
                                 ("Evidence-backed", r.evidence_backed), ("Assumed", r.assumed),
                                 ("Unobserved", r.unobserved), ("Unsupported", r.unsupported)):  # fmt: skip
                if items:
                    lines += [f"**{label}:**", ""] + [f"- {i}" for i in items] + [""]
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
    if isinstance(report.fingerprint, dict) and "app" not in report.fingerprint:
        from labctl import conformance
        from labctl.behaviour import durable

        # the replica it ran against: what freshness compares later (R11)
        report.fingerprint = durable.add_app(report.fingerprint, root, app_id,
                                             conformance.image_id(app_id))  # fmt: skip
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
    if u.scheme not in ("http", "https"):  # about:blank (a new tab) is no address of the app
        return url
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


def _declared(mod, given, rec: Recording):
    """(the declared object, its file) for an acceptance, baseline or visual declaration: given
    as an object, a file, False (none), or None (the file beside the recording, if any)."""
    if given is False:
        return None, None
    if given is None:
        path = rec.path / mod.FILE
        return (mod.load(path), path) if path.exists() else (None, None)
    if isinstance(given, (str, Path)):
        path = Path(given)
        if not path.exists():
            raise FileNotFoundError(f"{path}: no such file")
        return mod.load(path), path
    return given, getattr(given, "path", None)


def _viewport(viewport, vspec) -> tuple[dict, str]:
    from labctl.behaviour.visual import VIEWPORTS

    if isinstance(viewport, str):
        if viewport not in VIEWPORTS:
            raise ValueError(f"viewport {viewport!r}: one of {', '.join(VIEWPORTS)}")
        return dict(VIEWPORTS[viewport]), viewport
    if isinstance(viewport, dict):
        name = next((k for k, v in VIEWPORTS.items() if v == viewport), "custom")
        return dict(viewport), name
    if vspec is not None:
        return vspec.size(), vspec.viewport
    return {"width": 1280, "height": 900}, "replay-default"


def _no_cookies(state, notes: dict):
    """The given browser data without its cookies: a replay sends only the cookies its declared
    baseline makes up, never one taken from the source (R06)."""
    if state is None:
        return None
    if isinstance(state, (str, Path)):
        import json

        state = json.loads(Path(state).read_text(encoding="utf-8"))
    state = dict(state)
    dropped = state.get("cookies") or []
    if dropped:
        notes["cookies_not_sent"] = len(dropped)
    state["cookies"] = []
    return state


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
    acceptance=None,
    baseline=None,
    visual=None,
    artifacts: Path | None = None,
    outcome_judge: Callable | None = None,
    viewport: str | dict | None = None,
) -> FidelityReport:
    """Replay the recording's steps with Playwright and compare each step's after-snapshot.

    Values come from `bindings` (a Bindings, or a bindings file; by default `bindings.yaml`
    beside the recording). `values` are the older per-selector values (`"#loanQuery"`, or
    `"6:#loanQuery"` for step 6 only), used for steps the bindings do not bind. `expected` lists
    the semantic actions the flow should have (see `action_label`): one the recording lacks fails
    the replay. `storage_state` is browser data to start from (its cookies are never sent);
    `setup(context)` runs before the first step and `after(page)` after the last.

    `acceptance`, `baseline` and `visual` are the declarations beside the recording by default
    (`acceptance.yaml`, `baseline.yaml`, `visual.yaml`; False for none): acceptance cases and
    visual states are evaluated where they are declared, the baseline is applied to the fresh
    context before the first step. `outcome_judge(automation, case)` judges `outcome` cases on
    the lab database. Pictures (the replica's, diff images) go to `artifacts`. `viewport` is
    desktop, mobile or a size (default: the visual file's, else 1280x900).
    """
    from playwright.sync_api import sync_playwright

    from labctl.behaviour import acceptance as acc_mod
    from labctl.behaviour import baseline as base_mod
    from labctl.behaviour import durable
    from labctl.behaviour import judging as judge_mod
    from labctl.behaviour import visual as vis_mod
    from labctl.locate import Unresolved

    rec = recording if isinstance(recording, Recording) else load_recording(Path(recording))
    _infer_causes(rec)
    bnd = _bindings(rec, bindings, values)
    vals = _values(rec, bnd)
    supplied = {k for k in (domlib.key_of(_selector_of(name)) for name in values or {}) if k}
    supplied |= bindings_mod.supplied_keys(rec, bnd)
    spec, spec_path = _declared(acc_mod, acceptance, rec)
    base, base_path = _declared(base_mod, baseline, rec)
    vspec, vspec_path = _declared(vis_mod, visual, rec)
    if isinstance(bindings, (str, Path)):
        bnd_path = Path(bindings)
    elif bindings is None and (rec.path / bindings_mod.FILE).exists():
        bnd_path = rec.path / bindings_mod.FILE
    else:
        bnd_path = None
    size, run_viewport = _viewport(viewport, vspec)
    reports: list[StepReport] = []
    problems: list[str] = []
    stopped = False
    notes: dict = {}
    state = _no_cookies(storage_state, notes)
    source_origin = next((o for o in (base_mod.origin_of(s.url) for s in rec.steps) if o), None)
    replica_origin = base_mod.origin_of(base_url) or source_origin
    if base is not None and base.problems:
        problems += [f"baseline: {p}" for p in base.problems]
        stopped = True  # nothing runs on a baseline that cannot be applied
    step_by_seq = {s.seq: s for s in rec.steps}

    def record_title(seq: int) -> str | None:
        step = step_by_seq.get(seq)
        return _record_title(step, vals, bnd) if step is not None else None

    judging = None
    if (spec and spec.cases) or (vspec and vspec.states):
        if artifacts is None:
            import tempfile

            artifacts = Path(tempfile.mkdtemp(prefix="twinlab-replay-"))
    if artifacts is not None:
        artifacts = Path(artifacts)
        artifacts.mkdir(parents=True, exist_ok=True)
    blocked_requests: list[str] = []
    step_shots: dict[int, str] = {}
    browser_info = visual_setup = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        browser_info = {"name": browser.browser_type.name, "version": browser.version}
        options: dict = {"viewport": size}
        if vspec is not None:
            options.update(vis_mod.context_options(vspec))
            options["viewport"] = size

        def new_context():
            ctx = browser.new_context(storage_state=state, **options)
            ctx.add_init_script(domlib.SHADOW_HOOK_JS)  # closed shadow roots, as recorded
            if source_origin and replica_origin and source_origin != replica_origin:
                # Nothing of the replay goes to the source: no cookie, no request.
                def refuse(route):
                    blocked_requests.append(route.request.method + " " + route.request.url[:200])
                    route.abort()

                ctx.route(source_origin + "/**", refuse)
            return ctx

        def fresh(url: str):
            ctx = new_context()
            p = ctx.new_page()
            if vspec is not None:
                vis_mod.prepare(p, vspec)
            base_mod.apply(ctx, p, base, replica_origin)
            p.goto(url)
            _settle(p, settle_ms)
            return ctx, p

        try:
            context = new_context()
            if setup is not None:
                setup(context)
            page = context.new_page()
            _track(page)
            if vspec is not None:
                vis_mod.prepare(page, vspec)
            if spec is not None or vspec is not None or base is not None:
                judging = judge_mod.Judging(
                    rec, spec, vspec, binding=vals.get, record=record_title, artifacts=artifacts,
                    outcome_judge=outcome_judge, fresh=fresh, run_viewport=run_viewport,
                    settle=lambda p: _settle(p, settle_ms))  # fmt: skip
            if not stopped and base is not None:
                try:
                    notes.update(base_mod.apply(context, page, base, replica_origin))
                except Exception as exc:  # noqa: BLE001 - reported: nothing runs without it
                    problems.append(f"baseline: could not be applied: {exc}"[:300])
                    stopped = True
            reached: set[int] = set()
            stopped_at = None
            for step in rec.steps:
                report = StepReport(seq=step.seq, action=step.action, selector=step.selector,
                                    url=step.url, status="pass")  # fmt: skip
                reports.append(report)
                if stopped:
                    report.status, report.outcome = "skipped", "not_run"
                    report.note = "not run: the replay stopped at an earlier step"
                    if stopped_at is None:
                        stopped_at = step.seq
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
                    _shoot(page, artifacts, step.seq, step_shots)
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
                    _shoot(page, artifacts, step.seq, step_shots)
                    continue
                except Unsupported as exc:
                    report.status, report.outcome, report.note = "blocked", "unsupported", str(exc)
                    report.differences.append(Difference(step=step.seq, element=where,
                                                         check="unsupported", expected=step.action,
                                                         actual=str(exc)))  # fmt: skip
                    problems.append(f"step {step.seq} ({step.action}) BLOCKED: {exc}")
                    stopped = True
                    _shoot(page, artifacts, step.seq, step_shots)
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
                    _shoot(page, artifacts, step.seq, step_shots)
                    if stop_on_fail:
                        stopped = True
                    continue
                report.outcome = "implied" if implied else "replayed"
                _settle(page, settle_ms)
                reached.add(step.seq)
                if expected_html is None:
                    report.note = "ran; no snapshot was recorded after this step to compare"
                else:
                    report.compared = True
                    report.differences = compare_dom(
                        expected_html, domlib.page_html(page), step.seq, supplied
                    )
                    if report.differences:  # ran, and the page differs
                        report.status = "fail"
                        _shoot(page, artifacts, step.seq, step_shots)
                        if stop_on_fail:
                            stopped = True
                if judging is not None:
                    judging.at(page, step.seq, step.seq)
            if after is not None:
                after(page)
            if judging is not None:
                if not stopped:
                    judging.at(page, "end", rec.steps[-1].seq if rec.steps else None)
                judging.finish(reached, stopped_at)
            if vspec is not None:
                visual_setup = vis_mod.setup_report(browser, page, vspec)
        finally:
            browser.close()
    report = _result(rec, base_url, reports, problems, expected)
    used = {"bindings": bnd_path, "acceptance": spec_path, "baseline": base_path,
            "visual": vspec_path}  # fmt: skip
    report.fingerprint = durable.for_run(rec, used, browser_info, size)
    report.visual_setup = visual_setup
    report.artifacts = str(artifacts) if artifacts is not None else None
    report.baseline = {**(base.summary() if base is not None else {"file": None, "empty": True}),
                       **notes, "source_requests_blocked": len(blocked_requests)}  # fmt: skip
    if blocked_requests:
        report.problems.append(f"{len(blocked_requests)} request(s) to the source "
                               f"({source_origin}) were refused")  # fmt: skip
    _scope(report, judging, spec, rec, step_shots)
    return report


def _shoot(page, artifacts: Path | None, seq: int, shots: dict[int, str]) -> None:
    """The replica at the first step that diverged."""
    if artifacts is None or shots:
        return
    try:
        path = Path(artifacts) / f"step-{seq:04d}.after.png"
        page.screenshot(path=str(path), animations="disabled", caret="hide")
        shots[seq] = str(path)
    except Exception:  # noqa: BLE001 - a missing picture never loses the result
        pass


def _scope(report: FidelityReport, judging, spec, rec: Recording, step_shots) -> None:
    """The scoped result beside the replay's: categories, overall status, requirements, first
    divergence. A failed or blocked required case also makes the replay's own status fail or
    blocked, so current readers of `status` see it."""
    from labctl.behaviour import judging as judge_mod

    cases = list(judging.cases.values()) if judging else []
    visual = list(judging.visual.values()) if judging else []
    report.cases, report.visual = cases, visual
    required = [r for r in [*cases, *visual] if r.required]
    failed = [r.id for r in required if r.status == "FAIL"]
    blocked = [r.id for r in required if r.status == "BLOCKED"]
    if failed:
        report.problems.append("required acceptance case(s) failed: " + ", ".join(failed[:10]))
        if report.status == "pass":
            report.status = "fail"
    if blocked:
        report.problems.append("required acceptance case(s) blocked: " + ", ".join(blocked[:10]))
        if report.status == "pass":
            report.status = "blocked"
    report.categories = judge_mod.categories(report.coverage, report.status, cases, visual)
    # the replay category is the structure alone: not the cases folded into `status`
    structural = "pass"
    if report.coverage is not None:
        c = report.coverage
        if c.unresolved or c.missing_value or c.unsupported or c.replayed == 0:
            structural = "blocked"
        elif c.failed or c.skipped:
            structural = "fail"
    report.categories["replay"].status = {"pass": "PASS", "fail": "FAIL"}.get(structural,
                                                                              "BLOCKED")  # fmt: skip
    report.verdict, report.verdict_reasons = judge_mod.scoped(report.categories, cases, visual)
    report.requirements = judge_mod.requirements(spec, cases, visual, report.steps,
                                                 report.coverage)  # fmt: skip
    report.first_divergence = judge_mod.first_divergence(judging, report.steps, rec, step_shots)


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
