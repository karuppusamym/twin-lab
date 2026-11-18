"""One recording across two applications and two tabs, replayed as recorded (the gaps found by
docs/multi-app-recording-trial.md, reproduced on local fixtures, without the internet).

The fixture applications `orders` and `billing` (fixtures/multiapp/apps.py, with an exchange-rate
widget on a third origin) are driven through the 108-step scripted flow (fixtures/multiapp/flow.py)
while Twin Lab records. Then, as the trial's reviewer would:

- the split keeps every step reference right: renumbered within a part, a reference to a step
  that went to the other application kept as a cross-app reference (trial P0-1);
- setup events (the blank page a browser starts on) are counted the same everywhere (P1);
- the UNSPLIT recording replays against the two applications in one browser context through an
  origin map: tabs opened, switched and closed as recorded, Back and Forward as recorded, each
  replica given its own steps, no request to a source or an unmapped origin (P0-3); against the
  same source code (fresh instances) it shows zero differences, three times (P0-2), and with the
  acceptance cases and the declared scope below it is PASS WITHIN DECLARED SCOPE;
- against a replica saving a wrong value, and against one missing a page, it FAILS, and the first
  divergence names the application, the step and the locator.

The values a replay types are synthetic, per field, from fixtures/multiapp/flow.py (`SYNTHETIC`);
the order number is the one a fresh `orders` gives its first new order.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "fixtures" / "multiapp"))

import apps as fixture_apps  # noqa: E402
import flow as fixture_flow  # noqa: E402

from labctl import split as sp  # noqa: E402
from labctl.behaviour import bindings as bnd  # noqa: E402
from labctl.botlog.recording import load as load_rec  # noqa: E402

NUMBER = "ORD-1026"  # what a fresh orders numbers its first new order (25 seeded)
SYN = {
    **fixture_flow.SYNTHETIC["orders"],
    "#q": ["Lindqvist", NUMBER, "QQQ-0001"],
    **fixture_flow.SYNTHETIC["billing"],
    "orderRef": ["BAD9", NUMBER, NUMBER],
}


def steps_of(folder: Path) -> list[dict]:
    return [json.loads(x) for x in (folder / "steps.jsonl").read_text("utf-8").splitlines() if x]


def fill_bindings(rec_dir: Path, values: dict[str, list[str]]) -> Path:
    """What a person does: the bindings template, each value given field by field, in order."""
    rec = load_rec(rec_dir)
    path = bnd.write_template(rec)
    doc = yaml.safe_load(path.read_text("utf-8"))
    used: dict[str, int] = {}
    for entry in doc["steps"].values():
        if "value" not in entry:
            continue
        key = max((k for k in values if k in (entry.get("field") or "")), key=len)
        n = used[key] = used.get(key, -1) + 1
        entry["value"] = values[key][n]
    path.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    return path


def seq_of(rec_dir: Path, action: str, selector_part: str, nth: int = 0, **match) -> int:
    hits = [s["seq"] for s in steps_of(rec_dir) if s["action"] == action
            and selector_part in (s.get("selector") or s.get("url") or "")
            and all(s.get(k) == v for k, v in match.items())]  # fmt: skip
    return hits[nth]


# =================================================================================================
# Recorded once
# =================================================================================================

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def source():
    run = fixture_apps.serve()
    yield run
    run.stop()


@pytest.fixture(scope="module")
def recorded(source, tmp_path_factory):
    from labctl import recorder

    rec = tmp_path_factory.mktemp("across") / "order-to-invoice"
    recorder.record(f"{source.orders}/login", rec, headless=True, script=fixture_flow.run)
    fill_bindings(rec, SYN)
    return rec


def declare(rec: Path, source) -> None:
    """The acceptance cases and the scope a reviewer declares before the run, from the scripted
    values (never from the replica): the saved order shows its customer and an order number,
    the search finds it, the invoice shows the amount typed, the bad amount is refused."""
    name = seq_of(rec, "type", "#customerName")
    saved = seq_of(rec, "goto", "/orders/saved", nav="navigate")
    found = seq_of(rec, "submit", "#searchForm", 2)  # the search by order number (Enter)
    bad_amount = seq_of(rec, "goto", "/billing/account", 1)
    amount = seq_of(rec, "type", 'name="amount"', 1)
    invoice = seq_of(rec, "goto", "/billing/invoice")
    cases = [
        {"id": "saved-customer", "after": saved, "check": "text", "locator": "#savedCustomer",
         "equals": {"binding": name}},
        {"id": "saved-number", "after": saved, "check": "text", "locator": "#savedNumber",
         "matches": r"^ORD-\d{4}$"},
        {"id": "search-finds-it", "after": found, "check": "texts", "locator": "#results td.order-no",
         "equals": [NUMBER]},
        {"id": "invoice-amount", "after": invoice, "check": "text",
         "locator": "[data-field=amount]", "equals": {"binding": amount}},
        {"id": "bad-amount-refused", "after": bad_amount, "check": "text",
         "locator": "p.notice--error", "contains": "The amount is a number"},
    ]  # fmt: skip
    (rec / "acceptance.yaml").write_text(yaml.safe_dump(
        {"version": 1, "scope": ["order to invoice across orders and billing"], "cases": cases}),
        "utf-8")  # fmt: skip
    (rec / "scope.yaml").write_text(yaml.safe_dump({
        "version": 1, "approved_by": "the trial reviewer", "approved_on": "2026-11-18",
        "exclusions": [{"id": "fx-widget-rates", "element": "tbody#fxRows",
                        "reason": "the exchange-rate widget's live rates are not replicated"}]}),
        "utf-8")  # fmt: skip


def origin_map(source, replica) -> dict[str, str]:
    return {source.orders: replica.orders, source.billing: replica.billing,
            source.widget: replica.widget}  # fmt: skip


def copy_of(rec: Path, dest: Path, keep: int | None = None) -> Path:
    shutil.copytree(rec, dest)
    if keep is not None:
        lines = (dest / "steps.jsonl").read_text("utf-8").splitlines()[:keep]
        (dest / "steps.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    return dest


# =================================================================================================
# P0-1: split references
# =================================================================================================


def test_split_remaps_every_step_reference(recorded, source, tmp_path):
    rec = copy_of(recorded, tmp_path / "order-to-invoice")
    declare(rec, source)
    prop = sp.propose(rec)
    prop.apps[0].id, prop.apps[1].id = "orders", "billing"
    orders, billing = sp.apply(rec, prop, tmp_path / "repo")
    whole = {s["seq"]: s for s in steps_of(rec)}
    for part in (orders, billing):
        local = {s["global_seq"]: s["seq"] for s in steps_of(part)}
        for s in steps_of(part):
            g = whole[s["global_seq"]]
            if isinstance(g.get("caused_by"), int):  # the cause is in this part: its number here
                assert s["caused_by"] == local[g["caused_by"]], s
        acc = yaml.safe_load((part / "acceptance.yaml").read_text("utf-8"))
        for case in acc["cases"]:
            assert case["after"] in local.values()
            want = next(c for c in yaml.safe_load((rec / "acceptance.yaml").read_text("utf-8"))[
                "cases"] if c["id"] == case["id"])  # fmt: skip
            assert local[want["after"]] == case["after"]
            if isinstance(want.get("equals"), dict):
                assert case["equals"] == {"binding": local[want["equals"]["binding"]]}
        b = yaml.safe_load((part / "bindings.yaml").read_text("utf-8"))
        for key, entry in b["steps"].items():
            gs = next(g for g, n in local.items() if n == key)
            assert entry["field"] == whole[gs]["selector"]
    ids = {
        c["id"] for c in yaml.safe_load((orders / "acceptance.yaml").read_text("utf-8"))["cases"]
    }
    assert ids == {"saved-customer", "saved-number", "search-finds-it"}
    assert (orders / "scope.yaml").is_file() and (billing / "scope.yaml").is_file()


def test_a_reference_to_the_other_application_is_kept_as_cross_app(tmp_path):
    """A step references a step that went to the other application: a page load in billing that
    a click in orders caused (the same tab), a record titled with a value typed in orders, a case
    in billing expecting that value. Each is kept as a cross-app reference, and the value the
    whole recording's bindings gave travels with it ("supplied from outside")."""
    rec = tmp_path / "in" / "cross"
    (rec / "dom").mkdir(parents=True)
    a, b = "http://a.example.test", "http://b.example.test"
    rows = [(a + "/", "goto", None, {}), (a + "/", "type", "input#ref", {"typed_shape": "A-9"}),
            (a + "/", "click", "a#toB", {}),
            (b + "/in", "goto", None, {"caused_by": 3, "nav": "navigate"}),
            (b + "/in", "click", "li button", {"locators": [
                {"kind": "record", "rows": "ul li", "control": "button", "count": 1, "text_from": 2}]})]  # fmt: skip
    lines = []
    for n, (url, action, sel, extra) in enumerate(rows, 1):
        step = {"seq": n, "ts": f"2026-11-17T10:00:{n:02d}.000Z", "url": url, "action": action,
                "selector": sel, "dom": f"dom/{n:04d}.html", "tab": "t1", **extra}  # fmt: skip
        (rec / step["dom"]).write_text("<html><body><p>x</p></body></html>", "utf-8")
        lines.append(json.dumps(step))
    (rec / "steps.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": []}}), "utf-8")
    (rec / "bindings.yaml").write_text(yaml.safe_dump(
        {"version": 1, "steps": {2: {"action": "type", "value": "SYN-1"}}}), "utf-8")  # fmt: skip
    (rec / "acceptance.yaml").write_text(yaml.safe_dump({"cases": [
        {"id": "ref-shown", "after": 5, "check": "text", "locator": "#ref", "equals": {"binding": 2}}]}),
        "utf-8")  # fmt: skip
    prop = sp.propose(rec)
    prop.apps[0].id, prop.apps[1].id = "a", "b"
    pa, pb = sp.apply(rec, prop, tmp_path / "repo")
    sb = steps_of(pb)
    assert "caused_by" not in sb[0] and sb[0]["caused_by_outside"] == {"app": "a", "step": 3}
    cand = sb[1]["locators"][0]
    assert cand["text_from"] is None and cand["text_from_outside"] == {"app": "a", "step": 2}
    case = yaml.safe_load((pb / "acceptance.yaml").read_text("utf-8"))["cases"][0]
    assert case["after"] == 2 and case["equals"] == {"binding": {"app": "a", "step": 2}}
    outside = yaml.safe_load((pb / "bindings.yaml").read_text("utf-8"))["outside"]
    assert outside == {"a#2": "SYN-1"}
    # the part reads: its record title and expected value come from outside
    part = load_rec(pb)
    given = bnd.load(pb / "bindings.yaml")
    from labctl.behaviour import fidelity

    assert fidelity._record_title(part.steps[1], {}, given) == "SYN-1"
    assert bnd.needs(part) == []  # nothing is left for a person to give
    # the navigation the other application caused is the replay's own to make
    assert part.steps[0].caused_by is None and part.steps[0].caused_by_outside == {"app": "a",
                                                                                   "step": 3}  # fmt: skip


# =================================================================================================
# P1: setup events
# =================================================================================================


def test_setup_events_are_classified_and_counted_the_same_everywhere(recorded, tmp_path):
    steps = steps_of(recorded)
    from labctl.botlog.recording import setup_reason

    assert steps[0]["url"] == "about:blank" and setup_reason(steps[0]) == "blank page"
    assert sum(1 for s in steps if setup_reason(s)) == 1
    prop = sp.propose(recorded)
    assert prop.setup == 1
    rec = copy_of(recorded, tmp_path / "order-to-invoice")
    parts = sp.apply(rec, prop, tmp_path / "repo")
    seq = yaml.safe_load(sp.sequence_path(rec, tmp_path / "repo").read_text("utf-8"))
    counts = seq["counts"]
    tab_steps = sum(1 for s in steps if s["action"] in sp.TAB_ACTIONS)
    assert counts == {"total": len(steps), "setup": 1, "tab": tab_steps,
                      "apps": {e.id: e.steps for e in prop.apps}}  # fmt: skip
    assert counts["total"] == counts["setup"] + counts["tab"] + sum(counts["apps"].values())
    for e, part in zip(prop.apps, parts, strict=True):
        assert len(steps_of(part)) == e.steps  # proposal, part and sequence agree
    assert seq["setup"] == [{"n": 1, "do": "open a blank page", "reason": "blank page"}]
    assert not any(r["n"] == 1 for r in seq["steps"])  # provenance kept, in no application


# =================================================================================================
# P0-2 and P0-3: the unsplit recording, one context, as recorded
# =================================================================================================


def replay(rec: Path, origins, **kw):
    from labctl.behaviour import fidelity

    return fidelity.replay(rec, origins=origins, resolve_timeout_ms=4000, **kw)


def test_the_unsplit_recording_passes_within_declared_scope(recorded, source, tmp_path):
    """The recorded source against the same source code (fresh instances, three times): zero
    differences, every step on its own application, tabs and history as recorded."""
    rec = copy_of(recorded, tmp_path / "order-to-invoice")
    declare(rec, source)
    whole = steps_of(rec)
    expected = [a for a, _, _ in fixture_flow.EXPECTED]
    for _ in range(3):
        fresh = fixture_apps.serve()
        try:
            omap = origin_map(source, fresh)
            report = replay(rec, omap, expected=expected)
            diffs = [d.text() for s in report.steps for d in s.differences]
            assert diffs == [] and report.status == "pass", report.markdown()
            assert report.verdict == "PASS WITHIN DECLARED SCOPE", report.markdown()
            c = report.coverage
            assert (c.setup, c.steps, c.failed, c.skipped, c.unresolved) == (
                1,
                len(whole) - 1,
                0,
                0,
                0,
            )
            # each replica was given exactly its own steps
            for s, r in zip(whole, report.steps, strict=True):
                if s["url"] == "about:blank" or s["action"] in sp.TAB_ACTIONS:
                    continue
                assert r.replica_origin == omap[s["origin"]], (s, r)
            # tabs as recorded; Back and Forward performed, not reloaded
            by = {r.seq: r for r in report.steps}
            opened = next(s for s in whole if s["action"] == "tab-open")
            assert by[opened["seq"]].target == "popup opened by step %d" % (opened["seq"] - 1)
            backs = [by[s["seq"]].target for s in whole if s.get("nav") == "back_forward"]
            assert backs == ["history back", "history forward"]
            assert report.problems == [] and report.baseline["source_requests_blocked"] == 0
            assert (
                report.excluded is not None and report.excluded.approved_by == "the trial reviewer"
            )
            assert {c.id: c.status for c in report.cases} == dict.fromkeys(
                ["saved-customer", "saved-number", "search-finds-it", "invoice-amount",
                 "bad-amount-refused"], "PASS")  # fmt: skip
            # the replicas did what the session did, with the synthetic values
            assert fresh.apps["orders"].orders[NUMBER].customer == "Syn Customer"
            assert fresh.apps["billing"].invoices[NUMBER].amount == 88.20
        finally:
            fresh.stop()


def test_a_step_on_an_origin_with_no_replica_blocks(recorded, source, tmp_path):
    rec = copy_of(recorded, tmp_path / "rec", keep=70)
    fresh = fixture_apps.serve()
    try:
        report = replay(rec, {source.orders: fresh.orders})
    finally:
        fresh.stop()
    assert report.status == "blocked"
    first = next(r for r in report.steps if r.status == "blocked")
    assert first.outcome == "unmapped" and source.billing in first.note and "--map" in first.note
    assert first.seq == seq_of(rec, "goto", "/billing/start")


def test_no_request_goes_to_a_source_or_to_an_origin_without_a_replica(recorded, source, tmp_path):
    rec = copy_of(recorded, tmp_path / "rec", keep=68)
    fresh = fixture_apps.serve()
    try:  # the widget's origin is neither mapped nor declared excluded
        report = replay(rec, {source.orders: fresh.orders, source.billing: fresh.billing})
        assert report.status == "fail"
        assert any("no replica" in p and fresh.widget in p for p in report.problems), (
            report.problems
        )
        assert report.blocked_requests and all(fresh.widget in r for r in report.blocked_requests)
        # declared excluded before the run: refused and counted as excluded, not failed
        (rec / "scope.yaml").write_text(yaml.safe_dump({"approved_by": "reviewer", "exclusions": [
            {"id": "fx", "origin": fresh.widget, "reason": "third-party widget"},
            {"id": "fx-rows", "element": "tbody#fxRows", "reason": "its rates"}]}), "utf-8")  # fmt: skip
        again = replay(rec, {source.orders: fresh.orders, source.billing: fresh.billing})
        assert again.status == "pass", again.markdown()
        assert again.excluded.requests >= 1 and again.excluded.by_id.get("fx", 0) >= 1
        assert "excluded by declared scope" in again.markdown().lower()
    finally:
        fresh.stop()


def test_a_wrong_value_fails_at_the_right_app_step_and_locator(recorded, source, tmp_path):
    rec = copy_of(recorded, tmp_path / "order-to-invoice")
    declare(rec, source)
    fresh = fixture_apps.serve()
    orders = fresh.apps["orders"]
    real = orders.items_post

    def wrong(req, mode):  # saves the customer's name wrongly
        draft = orders.session(req).get("draft") or {}
        draft["customer"] = (draft.get("customer") or "").upper()
        return real(req, mode)

    orders.routes = [(m, p, wrong if fn == real else fn) for m, p, fn in orders.routes]
    try:
        report = replay(rec, origin_map(source, fresh))
    finally:
        fresh.stop()
    assert report.verdict == "FAIL" and report.status == "fail", report.markdown()
    d = report.first_divergence
    assert (d.app, d.kind, d.what, d.locator) == (source.orders, "case", "saved-customer",
                                                  "#savedCustomer")  # fmt: skip
    assert d.step == seq_of(rec, "goto", "/orders/saved", nav="navigate")


def test_a_missing_page_fails_at_the_right_app_step_and_locator(recorded, source, tmp_path):
    rec = copy_of(recorded, tmp_path / "order-to-invoice")
    declare(rec, source)
    (rec / "replicas.yaml").write_text(yaml.safe_dump({"apps": [
        {"id": "orders", "source": source.orders}, {"id": "billing", "source": source.billing},
        {"id": "widget", "source": source.widget}]}), "utf-8")  # fmt: skip
    fresh = fixture_apps.serve()
    billing = fresh.apps["billing"]
    billing.routes = [r for r in billing.routes if r[1] != "/billing/invoice"]  # the page is gone
    try:  # stopped at the first failure: what follows a missing page cannot run as recorded
        report = replay(rec, origin_map(source, fresh), stop_on_fail=True)
    finally:
        fresh.stop()
    assert report.status == "fail" and report.verdict == "FAIL", report.markdown()
    d = report.first_divergence
    assert d.app == "billing" and d.kind == "step"
    assert d.step == seq_of(rec, "goto", "/billing/invoice")
    assert d.locator and d.locator.startswith("a"), d  # the invoice page's link is missing
    assert "billing" in report.markdown().split("## First divergence", 1)[1][:200]


# =================================================================================================
# The parts still replay on their own, also a part that used two tabs on one application
# =================================================================================================


def two_tabs_on_orders(page) -> dict:
    t = fixture_flow.TYPED
    page.fill("#username", t["user"])
    page.fill("#password", t["password"])
    page.click("#signIn")
    page.wait_for_timeout(700)
    other = page.context.new_page()  # a second tab on the same application, opened by hand
    wrap = getattr(page, "wrap", lambda p: p)
    other = wrap(other)
    other.goto(page.url.rsplit("/", 2)[0] + "/customers")
    other.wait_for_timeout(800)
    other.click("#navSearch")
    other.wait_for_timeout(700)
    other.close()
    page.bring_to_front()
    page.wait_for_timeout(300)
    page.click("#newOrder")
    page.wait_for_timeout(700)
    page.click("#signOut")
    page.wait_for_timeout(700)
    return {}


def test_a_part_that_used_two_tabs_on_one_app_replays_on_its_own(source, tmp_path):
    from labctl import recorder
    from labctl.behaviour import fidelity

    rec = tmp_path / "in" / "two-tabs-orders"
    recorder.record(f"{source.orders}/login", rec, headless=True, script=two_tabs_on_orders)
    actions = [s["action"] for s in steps_of(rec)]
    assert "tab-open" in actions and "tab-close" in actions
    prop = sp.propose(rec)
    prop.apps[0].id = "orders"
    (part,) = sp.apply(rec, prop, tmp_path / "repo")
    part_steps = steps_of(part)
    assert [s["action"] for s in part_steps if s["action"] in sp.TAB_ACTIONS] == [
        "tab-open", "tab-close", "tab-switch"]  # fmt: skip
    fill_bindings(part, {"#username": ["clerk.syn"], "#password": ["Syn-pass-1"]})
    fresh = fixture_apps.serve()
    try:
        report = fidelity.replay(part, fresh.orders, resolve_timeout_ms=4000)
    finally:
        fresh.stop()
    assert report.status == "pass", report.markdown()
    assert report.coverage.replayed == report.coverage.steps == len(part_steps)


# =================================================================================================
# P1: reviewed scope
# =================================================================================================


def test_scope_is_drafted_from_the_brief_for_review(tmp_path):
    from typer.testing import CliRunner

    from labctl.behaviour.cli import app

    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    (rec / "dom" / "0001.html").write_text(
        '<html><body><a id="homeLink" href="/">Home</a><a id="go" href="/orders">Go</a></body></html>',
        "utf-8")  # fmt: skip
    (rec / "steps.jsonl").write_text(json.dumps(
        {"seq": 1, "url": "http://a.test/orders", "action": "goto", "dom": "dom/0001.html"}) + "\n",
        "utf-8")  # fmt: skip
    brief = tmp_path / "BRIEF.md"
    brief.write_text("# x\n\n## Out of scope\n\nIn the evidence...\n\n- Screens: `/` (Index), "
                     "`/help`\n- Routes: `GET /api/help`\n\n## Running in the lab\n", "utf-8")  # fmt: skip
    r = CliRunner().invoke(app, ["scope", str(rec), "--from-brief", str(brief)])
    assert r.exit_code == 0, r.output
    data = yaml.safe_load((rec / "scope.yaml").read_text("utf-8"))
    assert data["approved_by"] is None  # nothing is used until a person approves it
    got = {(e.get("element"), e.get("screen")) for e in data["exclusions"]}
    assert got == {("a#homeLink", None), (None, "/"), (None, "/help")}
    from labctl.behaviour import scope

    sc = scope.load(rec / "scope.yaml")
    assert sc.usable == [] and "approved_by" in sc.problems[0]
    r = CliRunner().invoke(app, ["scope", str(rec), "--from-brief", str(brief)])
    assert r.exit_code == 1  # an existing declaration is never overwritten


def test_scope_matching_is_by_element_or_screen_and_never_silent():
    from labctl.behaviour import scope

    sc = scope.Scope(approved_by="r", exclusions=[
        scope.Exclusion("home", "out of scope", element="a#homeLink"),
        scope.Exclusion("help", "not built", screen="/help*"),
        scope.Exclusion("bad", "")])  # fmt: skip
    sc.exclusions[2].problem = "no reason given"
    assert scope.which(sc, "a#homeLink.nav", "http://x/orders").id == "home"
    assert scope.which(sc, "#homeLink", "http://x/orders").id == "home"
    assert scope.which(sc, "a#other", "http://x/orders") is None
    assert scope.which(sc, "button#save", "http://x/help/topics").id == "help"
    assert (
        scope.which(scope.Scope(exclusions=sc.exclusions), "a#homeLink", "/") is None
    )  # unapproved
