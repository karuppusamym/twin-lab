"""One small local fixture per interaction surface Twin Lab advertises (replication gap R12): each
is recorded and replayed and either PASSES, or is reported as an unsupported surface with an
actionable message, never silently skipped. labctl.behaviour.capabilities lists each surface
with the test here that proves it; the last test checks every row names a test that exists.

Pages: fixtures/capabilities/capability_pages.py (two local origins). Every replay runs against a fresh
instance of the same pages, through an origin map, so nothing outside 127.0.0.1 is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "fixtures" / "capabilities"))

import capability_pages as cap  # noqa: E402

from labctl.behaviour import capabilities  # noqa: E402


def test_every_advertised_capability_names_a_test_that_exists():
    import ast

    repo = HERE.parents[2]
    for path, name in capabilities.test_names():
        tree = ast.parse((repo / path).read_text(encoding="utf-8"))
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert name in names, (path, name)
    statuses = {r["status"] for r in capabilities.rows()}
    assert statuses <= {"supported", "partial", "unsupported"}
    assert "| Closed shadow DOM | unsupported |" in capabilities.markdown()


pytest.importorskip("playwright.sync_api")

from labctl import recorder  # noqa: E402
from labctl.behaviour import fidelity  # noqa: E402
from labctl.botlog.recording import load  # noqa: E402


@pytest.fixture(scope="module")
def site():
    run = cap.serve()
    yield run
    run.stop()


def steps_of(rec: Path) -> list[dict]:
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def record(site, tmp_path, path: str, script, name="rec", **kw) -> Path:
    out = tmp_path / name
    recorder.record(f"{site.main}{path}", out, headless=True, script=script, **kw)
    return out


def replay(rec: Path, site, **kw):
    fresh = cap.serve()
    try:
        kw.setdefault("resolve_timeout_ms", 3000)
        report = fidelity.replay(rec, origins={site.main: fresh.main, site.other: fresh.other},
                                 **kw)  # fmt: skip
        return report, fresh
    finally:
        fresh.stop()


def bind(rec: Path, steps: dict) -> None:
    (rec / "bindings.yaml").write_text(yaml.safe_dump({"version": 1, "steps": steps}), "utf-8")


def actions(rec: Path) -> list[str]:
    return [s["action"] for s in steps_of(rec) if s["url"] != "about:blank"]


def assert_clean_pass(report):
    diffs = [d.text() for s in report.steps for d in s.differences]
    assert report.status == "pass" and diffs == [], report.markdown()


# --- frames -------------------------------------------------------------------------------------


def test_same_origin_iframe(site, tmp_path):
    def flow(page):
        page.frame_locator("#inner").locator("#ping").click()
        page.wait_for_timeout(500)

    rec = record(site, tmp_path, "/iframe-same", flow)
    click = next(s for s in steps_of(rec) if s["action"] == "click")
    assert click["frame"] == "iframe#inner" and click["selector"].endswith(">>> button#ping")
    report, _ = replay(rec, site)
    assert_clean_pass(report)


def test_cross_origin_iframe(site, tmp_path):
    def flow(page):
        page.frame_locator("#inner").locator("#ping").click()
        page.wait_for_timeout(500)

    rec = record(site, tmp_path, "/iframe-cross", flow)
    click = next(s for s in steps_of(rec) if s["action"] == "click")
    assert click["frame"] == "iframe#inner"
    report, fresh = replay(rec, site)
    assert_clean_pass(report)
    assert report.blocked_requests == []
    # the frame's origin unmapped: its requests are refused and reported, never sent to the source
    alone = cap.serve()
    try:
        r = fidelity.replay(rec, origins={site.main: alone.main}, resolve_timeout_ms=1500)
    finally:
        alone.stop()
    assert r.status != "pass" and any("no replica" in p for p in r.problems), r.markdown()


# --- shadow DOM ---------------------------------------------------------------------------------


def test_open_shadow_dom(site, tmp_path):
    def flow(page):
        page.locator("x-card .title").fill("Shadow title")
        page.locator("x-card .add").click()
        page.wait_for_timeout(500)

    rec = record(site, tmp_path, "/shadow-open", flow)
    st = steps_of(rec)
    click = next(s for s in st if s["action"] == "click")
    kinds = [c["kind"] for c in click["locators"]]
    assert click["shadow"] == "open" and kinds[0] == "role" and "structure" in kinds
    assert click["locators"][0] == {"kind": "role", "role": "button", "name": "Add", "count": 1}
    typed = next(s for s in st if s["action"] == "type")
    assert any(c["kind"] == "placeholder" for c in typed["locators"])
    bind(rec, {typed["seq"]: {"value": "Syn title"}})
    report, _ = replay(rec, site)
    assert_clean_pass(report)
    assert next(r for r in report.steps if r.action == "click").target == 'role button "Add"'


def test_closed_shadow_dom_is_reported(site, tmp_path):
    def flow(page):
        page.locator("x-card").click(position={"x": 5, "y": 5})  # into the closed root
        page.wait_for_timeout(300)
        page.evaluate("() => window.__tlShadows.get(document.querySelector('x-card'))"
                      ".querySelector('.add').click()")  # fmt: skip
        page.wait_for_timeout(500)

    rec = record(site, tmp_path, "/shadow-closed", flow)
    clicks = [s for s in steps_of(rec) if s["action"] == "click" and s.get("shadow") == "closed"]
    assert clicks, steps_of(rec)
    report, _ = replay(rec, site)
    assert report.status == "blocked"
    blocked = next(r for r in report.steps if r.status == "blocked")
    assert blocked.outcome == "unsupported" and "closed shadow root" in blocked.note
    assert any("closed shadow root" in u for u in report.requirements.unsupported)


# --- files --------------------------------------------------------------------------------------


def test_file_upload(site, tmp_path):
    secret = tmp_path / "Quarterly-Accounts-2026.txt"
    secret.write_text("confidential numbers 12345\n" * 40, "utf-8")

    def flow(page):
        page.set_input_files("#doc", str(secret))
        page.wait_for_timeout(500)

    rec = record(site, tmp_path, "/upload", flow)
    up = next(s for s in steps_of(rec) if s["action"] == "upload")
    assert up["files"] == [{"name_shape": "A-A-9.txt", "type": "text/plain", "size": "1KB-1MB"}]
    texts = "".join(p.read_text("utf-8", "replace") for p in rec.rglob("*") if p.is_file()
                    and p.suffix != ".png")  # fmt: skip
    assert "Quarterly" not in texts and "confidential" not in texts  # never the file or its name
    report, _ = replay(rec, site, resolve_timeout_ms=1500)
    assert report.status == "blocked"
    first = next(r for r in report.steps if r.status == "blocked")
    assert first.outcome == "missing_value" and "file:" in first.note
    synthetic = tmp_path / "synthetic.txt"
    synthetic.write_text("synthetic\n", "utf-8")
    bind(rec, {up["seq"]: {"file": str(synthetic)}})
    report, _ = replay(rec, site)
    assert report.status == "pass", report.markdown()


def test_file_download(site, tmp_path):
    def flow(page):
        with page.expect_download():
            page.click("#get")
        page.wait_for_timeout(400)

    rec = record(site, tmp_path, "/download", flow)
    st = steps_of(rec)
    down = next(s for s in st if s["action"] == "download")
    click = next(s for s in st if s["action"] == "click")
    assert down["download"] == {"name_shape": "A-9.csv", "ext": "csv"}
    assert down["caused_by"] == click["seq"] and down["seq"] == click["seq"] + 1
    report, _ = replay(rec, site)
    assert_clean_pass(report)
    assert next(r for r in report.steps if r.action == "download").target.startswith("a download")


# --- windows and dialogs ------------------------------------------------------------------------


def test_popup_window(site, tmp_path):
    def flow(page):
        with page.context.expect_page() as opened:
            page.click("#open")
        child = page.wrap(opened.value)
        child.wait_for_load_state()
        child.wait_for_timeout(400)
        child.click("#done")
        child.wait_for_timeout(400)
        child.close()
        page.bring_to_front()
        page.wait_for_timeout(300)
        page.click("#open")
        page.wait_for_timeout(600)

    rec = record(site, tmp_path, "/popup", flow)
    assert [a for a in actions(rec) if a.startswith("tab-") or a == "click"] == [
        "click", "tab-open", "click", "tab-close", "tab-switch", "click", "tab-open"]  # fmt: skip
    report, _ = replay(rec, site)
    assert_clean_pass(report)
    # the replica that opens no window fails at the tab the source opened
    broken = rec.parent / "broken"
    import shutil

    shutil.copytree(rec, broken)
    fresh = cap.serve()
    fresh_pages = dict(cap.PAGES)
    try:
        cap.PAGES["/popup"] = cap.PAGES["/popup"].replace("window.open('/child', '_blank');", "")
        r = fidelity.replay(broken, origins={site.main: fresh.main, site.other: fresh.other},
                            resolve_timeout_ms=1500)  # fmt: skip
    finally:
        cap.PAGES.clear()
        cap.PAGES.update(fresh_pages)
        fresh.stop()
    assert r.status == "fail"
    bad = next(x for x in r.steps if x.status == "fail")
    assert bad.action == "tab-open" and "opened no tab" in bad.differences[0].actual


def test_dialogs(site, tmp_path):
    def flow(page):
        answers = iter(["accept", "dismiss", "alert", "prompt"])

        def handle(d):
            how = next(answers)
            if how == "prompt":
                d.accept("Rosalind Okonkwo")
            elif how == "dismiss":
                d.dismiss()
            else:
                d.accept()

        page.on("dialog", handle)
        for button in ("#ask", "#ask", "#tell", "#name"):
            page.click(button)
            page.wait_for_timeout(600)

    rec = record(site, tmp_path, "/dialogs", flow)
    st = steps_of(rec)
    dialogs = [s for s in st if s["action"] == "dialog"]
    assert [(d["dialog"]["kind"], d["dialog"]["choice"]) for d in dialogs] == [
        ("confirm", "accept"), ("confirm", "dismiss"), ("alert", "accept"), ("prompt", "accept")]  # fmt: skip
    assert all(isinstance(d["caused_by"], int) for d in dialogs)
    # the message is the application's text, recorded with each dialog (Demoblaze trial: the
    # "Product added." alert never reached the package)
    assert [d["dialog"].get("message") for d in dialogs] == [
        "Delete it?", "Delete it?", "Saved", "Your name?"]  # fmt: skip
    assert dialogs[3]["typed_shape"] == "A A"
    texts = "".join(p.read_text("utf-8", "replace") for p in rec.rglob("*") if p.is_file()
                    and p.suffix != ".png")  # fmt: skip
    assert "Rosalind" not in texts and "Okonkwo" not in texts  # prompt text: a typed value
    report, _ = replay(rec, site, resolve_timeout_ms=1500)
    assert report.status == "blocked" and "dialog" in report.problems[0]  # prompt text unbound
    bind(rec, {dialogs[3]["seq"]: {"value": "Syn Name"}})
    report, _ = replay(rec, site)
    assert_clean_pass(report)


# --- timing and values --------------------------------------------------------------------------


def test_async_content(site, tmp_path):
    def flow(page):
        page.click("#load")
        page.wait_for_selector("#pick-beta")
        page.wait_for_timeout(300)
        page.click("#pick-beta")
        page.wait_for_timeout(400)

    rec = record(site, tmp_path, "/async", flow)
    load_step = next(s for s in steps_of(rec) if s["action"] == "click")
    html = (rec / load_step["dom"]).read_text("utf-8")
    assert "pick-beta" in html  # the after-snapshot waited for the delayed answer
    import time

    started = time.monotonic()
    report, _ = replay(rec, site, settle_ms=100)
    assert_clean_pass(report)
    assert time.monotonic() - started >= cap.SLOW_MS / 1000  # it waited on the network, not a sleep


def test_select_by_index(site, tmp_path):
    def flow(page):
        page.select_option("#size", index=2)
        page.wait_for_timeout(400)

    rec = record(site, tmp_path, "/select", flow)
    sel = next(s for s in steps_of(rec) if s["action"] == "select")
    bind(rec, {sel["seq"]: {"index": 2}})
    report, _ = replay(rec, site)
    assert_clean_pass(report)
    bind(rec, {sel["seq"]: {"index": 1}})  # another option: the page shows another value
    report, _ = replay(rec, site)
    assert report.status == "pass"  # structure is the same; the value is a binding's
    assert report.steps[-1].target


def test_slow_post_redirect_and_history(site, tmp_path):
    """A POST answered after longer than the recorder's settle time, then a redirect (POST,
    redirect, GET), then Back and Forward: the submit is observed at the page it led to, and the
    replay of the source against the same source shows no difference (three times)."""

    def flow(page):
        page.fill("#who", "Slow Sender")
        page.click("#send")
        page.wait_for_url("**/slow-done", timeout=10000)
        page.wait_for_timeout(500)
        page.go_back()
        page.wait_for_timeout(600)
        page.go_forward()
        page.wait_for_timeout(600)

    rec = record(site, tmp_path, "/slow-form", flow)
    st = steps_of(rec)
    click = next(s for s in st if s["action"] == "click")
    done = next(s for s in st if s["action"] == "goto" and s["url"].endswith("/slow-done"))
    # the submit was observed at the page it loaded: no snapshot of the form it left
    assert click["dom"] is None and done["caused_by"] == click["seq"]
    assert all(s["dom"] is None for s in st if click["seq"] <= s["seq"] < done["seq"])
    assert [s["nav"] for s in st if s.get("nav") == "back_forward"] == ["back_forward"] * 2
    typed = next(s for s in st if s["action"] == "type")
    bind(rec, {typed["seq"]: {"value": "Syn Sender"}})
    for _ in range(3):
        report, fresh = replay(rec, site)
        assert_clean_pass(report)
        assert [r.target for r in report.steps if r.action == "goto" and r.target and "history"
                in r.target] == ["history back", "history forward"]  # fmt: skip
        assert fresh.hits.count(("POST", "/slow-form")) == 1  # posted once, not again on Back


# --- surfaces a DOM replay does not cover: reported, never claimed --------------------------------


def test_drag_and_drop_is_reported(site, tmp_path):
    def flow(page):
        page.drag_and_drop("#item", "#zone")
        page.wait_for_timeout(500)

    rec = record(site, tmp_path, "/drag", flow)
    assert "drag" in actions(rec)
    report, _ = replay(rec, site)
    assert report.status == "blocked"
    blocked = next(r for r in report.steps if r.status == "blocked")
    assert blocked.outcome == "unsupported" and "drag and drop" in blocked.note


def test_canvas_interaction_is_reported(site, tmp_path):
    def flow(page):
        page.click("#pad", position={"x": 40, "y": 30})
        page.wait_for_timeout(400)

    rec = record(site, tmp_path, "/canvas", flow)
    click = next(s for s in steps_of(rec) if s["action"] == "click")
    assert click["surface"] == "canvas"
    report, _ = replay(rec, site)
    assert report.status == "blocked"
    blocked = next(r for r in report.steps if r.status == "blocked")
    assert blocked.outcome == "unsupported" and "canvas" in blocked.note


def test_service_worker_is_classified(site, tmp_path):
    def flow(page):
        page.wait_for_function("document.getElementById('sw').textContent === 'registered'")
        page.click("#hello")
        page.wait_for_timeout(400)

    rec = record(site, tmp_path, "/sw", flow)
    assert any(s.get("service_worker") for s in steps_of(rec))
    report, _ = replay(rec, site)
    assert report.status == "pass", report.markdown()
    assert any("service worker" in u for u in report.requirements.unsupported)


# --- a second browser engine ----------------------------------------------------------------------


def _engines() -> dict[str, str | None]:
    """Each engine: None when it launches here, else why not (never downloaded)."""
    from playwright.sync_api import sync_playwright

    out = {}
    with sync_playwright() as pw:
        for name in ("firefox", "webkit"):
            try:
                getattr(pw, name).launch().close()
                out[name] = None
            except Exception as exc:  # noqa: BLE001 - not installed: reported
                out[name] = str(exc).splitlines()[0][:160]
    return out


def test_other_browser_engines(site, tmp_path, record_property):
    engines = _engines()
    ran = []
    for name, why in engines.items():
        if why is not None:
            record_property(f"engine-{name}", f"not run: {why}")
            continue

        def flow(page):
            page.select_option("#size", index=1)
            page.wait_for_timeout(400)

        rec = record(site, tmp_path, "/select", flow, name=f"rec-{name}", browser=name)
        sel = next(s for s in steps_of(rec) if s["action"] == "select")
        bind(rec, {sel["seq"]: {"index": 1}})
        report, _ = replay(rec, site, browser=name)
        assert_clean_pass(report)
        assert report.fingerprint["browser"]["name"] == name
        ran.append(name)
        record_property(f"engine-{name}", "ran")
    print("other engines:", {n: ("ran" if n in ran else w) for n, w in engines.items()})
    if not ran:
        pytest.xfail("no second engine is installed here: " + json.dumps(engines))


def test_a_recording_loads_with_the_new_fields(site, tmp_path):
    def flow(page):
        page.select_option("#size", index=1)
        page.wait_for_timeout(300)

    rec = load(record(site, tmp_path, "/select", flow))
    assert rec.steps[0].setup == "blank page"
    assert next(s for s in rec.steps if s.action == "select").option_index is None  # a binding
