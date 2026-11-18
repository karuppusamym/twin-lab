"""Fidelity replay (Prod Echo I-22a): replay a recording against a running demo app and compare,
after each step, the live DOM with the recorded after-snapshot.

Compared: the set of interactive elements (tag, id and classes), visibility, enabled state,
option lists (texts) and value format (shape only, so data values and masked values compare by
shape). Text content is not compared: it is data. A step fails with the element named.

`compare_dom` works on two snapshots alone, so a replay result can be checked without a live app.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from labctl.behaviour import dom as domlib
from labctl.botlog.recording import Recording
from labctl.botlog.recording import load as load_recording

Check = Literal["missing", "extra", "visibility", "enabled", "options", "value_format", "action"]


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
    status: Literal["pass", "fail", "skipped"]
    differences: list[Difference] = []
    note: str | None = None


class FidelityReport(BaseModel):
    recording: str
    base_url: str | None = None
    status: Literal["pass", "fail"]
    steps: list[StepReport] = []

    def first_failure(self) -> Difference | None:
        for s in self.steps:
            if s.differences:
                return s.differences[0]
        return None

    def markdown(self) -> str:
        lines = [f"# Fidelity: {self.recording}", "", f"**Result: {self.status}**", ""]
        lines += ["| Step | Action | Selector | Result |", "|---|---|---|---|"]
        for s in self.steps:
            lines.append(f"| {s.seq} | {s.action} | {s.selector or ''} | {s.status} |")
        diffs = [d for s in self.steps for d in s.differences]
        if diffs:
            lines += ["", "## Differences", ""] + [f"- {d.text()}" for d in diffs]
        return "\n".join(lines) + "\n"


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


def replay(
    recording: Recording | Path,
    base_url: str | None = None,
    values: dict[str, str] | None = None,
    headless: bool = True,
    settle_ms: int = 150,
    stop_on_fail: bool = False,
) -> FidelityReport:
    """Replay the recording's steps with Playwright and compare each step's after-snapshot.

    `values` supplies typed values by selector, for fields recorded without one (a recording
    keeps no typed value). `"6:#loanQuery"` gives the value for step 6 only.
    """
    from playwright.sync_api import sync_playwright

    rec = recording if isinstance(recording, Recording) else load_recording(Path(recording))
    values = values or {}
    supplied = {k for k in (domlib.key_of(_selector_of(name)) for name in values) if k is not None}
    reports: list[StepReport] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_init_script(domlib.SHADOW_HOOK_JS)  # closed shadow roots, as recorded
            page = context.new_page()
            _track(page)
            for step in rec.steps:
                report = StepReport(
                    seq=step.seq,
                    action=step.action,
                    selector=step.selector,
                    url=step.url,
                    status="pass",
                )
                reports.append(report)
                expected = rec.dom(step)
                try:
                    _act(page, rec, step, base_url, values)
                except Exception as exc:  # noqa: BLE001 - reported as the step's failure
                    report.status = "fail"
                    report.differences.append(
                        Difference(
                            step=step.seq,
                            element=step.selector or step.url or "?",
                            check="action",
                            expected=step.action,
                            actual=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}",
                        )
                    )
                    if stop_on_fail:
                        break
                    continue
                _settle(page, settle_ms)
                if expected is None:
                    report.status = "skipped"
                    report.note = "no recorded snapshot for this step"
                    continue
                report.differences = compare_dom(
                    expected, domlib.page_html(page), step.seq, supplied
                )
                if report.differences:
                    report.status = "fail"
                    if stop_on_fail:
                        break
        finally:
            browser.close()
    status = "fail" if any(r.status == "fail" for r in reports) else "pass"
    return FidelityReport(recording=rec.id, base_url=base_url, status=status, steps=reports)


def _recorded_value(rec: Recording, step) -> str | None:
    if step.value is not None:
        return step.value
    html = rec.dom(step)
    key = domlib.key_of(step.selector)
    if html is None or key is None:
        return None
    el = domlib.snapshot(html).elements.get(key)
    if el is None:
        return None
    return el.selected if el.tag == "select" else el.value


def _selector_of(name: str) -> str:
    """The selector of a `values` key: `"6:#loanQuery"` -> `"#loanQuery"`."""
    head, sep, rest = name.partition(":")
    return rest if sep and head.isdigit() else name


def _value_for(rec: Recording, step, values: dict[str, str]) -> str | None:
    for key in (f"{step.seq}:{step.selector}", step.selector):
        if key in values:
            return values[key]
    return _recorded_value(rec, step)


_UNMASK_JS = """masked => {
  const el = [...document.querySelectorAll('[id]')].find(
    e => e.id.replace(/[0-9]/g, '9') === masked);
  return el ? el.id : null;
}"""


def _unmasked(page, selector: str | None) -> str | None:
    """A recording masks record ids (`#loan-L-200040` is kept as `#loan-L-999999`). When no
    element has the masked id, the first element whose id has that shape stands for it."""
    if not selector or not selector.startswith("#") or "9" not in selector:
        return None
    if any(c in selector for c in " >+~[.:,"):
        return None
    try:
        if page.locator(selector).count():
            return None
        real = page.evaluate(_UNMASK_JS, selector[1:])
    except Exception:  # noqa: BLE001 - the plain selector is tried next and reports the failure
        return None
    return f'[id="{real}"]' if real else None


def _act(page, rec: Recording, step, base_url: str | None, values: dict[str, str]) -> None:
    if step.action == "goto":
        page.goto(_rebase(step.url, base_url))
        return
    from labctl.locate import locate

    selector = _unmasked(page, step.selector) or step.selector
    loc = locate(page, selector).first  # into frames; open shadow roots need nothing
    if step.action == "type":
        value = _value_for(rec, step, values)
        if value is None:
            raise ValueError(f"no value for {step.selector}; pass it in `values`")
        loc.fill(value, timeout=10000)
    elif step.action == "select":
        value = _value_for(rec, step, values)
        try:
            loc.select_option(label=value, timeout=10000)
        except Exception:  # noqa: BLE001 - recorded as text; fall back to the option value
            loc.select_option(value=value, timeout=10000)
    elif step.action == "click":
        loc.click(timeout=10000)
    elif step.action == "submit":
        loc.evaluate(
            "el => (el.form || el).requestSubmit ? (el.form || el).requestSubmit() : el.click()"
        )
    else:
        raise ValueError(f"unknown action {step.action}")
