"""Capture and replay without manual repair (replication gaps R01-R04).

An independent source fixture (fixtures/todo-app, a to-do list written for these tests) is driven
through the replication gate's flow while Twin Lab records; the emitted log is then replayed on a
replica page with synthetic bindings only, no handwritten actions. Expected actions and final
states are declared here, not taken from the recorder or the replica. Everything is served from
127.0.0.1.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytest.importorskip("playwright.sync_api")

import todo_flow as tf  # noqa: E402

from labctl import recorder  # noqa: E402
from labctl.behaviour import bindings as bnd  # noqa: E402
from labctl.behaviour import fidelity  # noqa: E402
from labctl.botlog.recording import load  # noqa: E402

# The values the replica is given, in the order the flow types them (different from the source's).
SYNTHETIC = ["Syn alpha", "Syn beta", "Syn gamma", "Syn discard", "Syn beta edited", "Syn delta",
             "Syn temp", "Syn epsilon"]  # fmt: skip
# The gate flow's end state, declared from the flow: two active tasks (the third and last added).
FINAL_TITLES_SOURCE = [tf.TYPED[2], tf.TYPED[7]]
FINAL_TITLES_REPLICA = [SYNTHETIC[2], SYNTHETIC[7]]


@pytest.fixture(scope="module")
def servers():
    source, replica = tf.serve("source.html"), tf.serve("replica.html")
    yield SimpleNamespace(source=tf.base(source), replica=tf.base(replica))
    source.shutdown()
    replica.shutdown()


@pytest.fixture(scope="module")
def gate(servers, tmp_path_factory):
    """The gate flow recorded on the source, with the source's own end state."""
    out = tmp_path_factory.mktemp("gate") / "rec"
    seen = {}

    def flow(page):
        tf.gate_flow(page)
        seen.update(tf.state(page))

    recorder.record(f"{servers.source}/index.html", out, script=flow, headless=True)
    return SimpleNamespace(path=out, source_state=seen)


def fill_bindings(rec_dir: Path, values: list[str]) -> Path:
    """What a person does: write the template, fill each value in order."""
    from typer.testing import CliRunner

    from labctl.behaviour.cli import app

    result = CliRunner().invoke(app, ["bindings", str(rec_dir)])
    assert result.exit_code == 0, result.output
    path = rec_dir / bnd.FILE
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    given = iter(values)
    for entry in doc["steps"].values():
        if "value" in entry:
            entry["value"] = next(given)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def replay_gate(rec_dir: Path, base: str, **kw):
    seen = {}
    report = fidelity.replay(rec_dir, base, expected=tf.EXPECTED,
                             after=lambda page: seen.update(tf.state(page)), **kw)  # fmt: skip
    return report, seen


def steps_of(rec_dir: Path) -> list[dict]:
    return [json.loads(x) for x in (rec_dir / "steps.jsonl").read_text("utf-8").splitlines()]


# --- (1) capture completeness -------------------------------------------------------------------


def test_the_scripted_flow_captures_every_expected_action_in_order(gate):
    rec = load(gate.path)
    captured = fidelity.captured_actions(rec)
    assert fidelity.missing_actions(tf.EXPECTED, captured) == [], captured
    # nothing else of substance: the extra steps are the load and caused navigations
    extra = [s for s in rec.steps if s.action not in ("goto",)]
    assert len(extra) == len([a for a in tf.EXPECTED if a != "goto" and a != "goto:reload"])
    # "type, then Enter" is two ordered steps, on the same field
    for i, s in enumerate(rec.steps):
        if s.action == "keypress" and s.key == "Enter":
            prev = rec.steps[i - 1]
            assert prev.action == "type" and prev.selector == s.selector, (prev, s)
    # a double click is one step, never two clicks on the label; boxes are check/uncheck
    assert sum(s.action == "dblclick" for s in rec.steps) == 3
    assert not any(s.action == "click" and s.selector == "label" for s in rec.steps)
    assert not any(s.action == "type" and "toggle" in (s.selector or "") for s in rec.steps)
    assert [s.action for s in rec.steps if s.action in ("check", "uncheck")] == [
        "check", "check", "uncheck"]  # fmt: skip
    # key presses carry the key and modifiers, never characters of the typed text
    keys = {(s.key, tuple(s.modifiers)) for s in rec.steps if s.action == "keypress"}
    assert keys == {("Enter", ()), ("Escape", ())}
    # blank values are kept as they are (no data), the rest only as their shape
    blanks = [s.blank for s in rec.steps if s.blank is not None]
    assert blanks == ["   ", ""]
    # a navigation a click caused says so; the reload is a reload
    assert [s.caused_by is not None for s in rec.steps if s.nav == "pop"] == [True] * 3
    assert sum(s.nav == "reload" for s in rec.steps) == 1


# --- (2), (4), (5), (9) replay on a fresh replica -----------------------------------------------


@pytest.fixture(scope="module")
def replayed(gate, servers):
    fill_bindings(gate.path, SYNTHETIC)
    return replay_gate(gate.path, servers.replica)


def test_the_log_replays_on_a_fresh_replica_to_the_same_state(gate, replayed):
    report, state = replayed
    assert report.status == "pass", report.markdown()
    c = report.coverage
    assert (c.skipped, c.unresolved, c.missing_value, c.unsupported, c.failed) == (0, 0, 0, 0, 0)
    assert c.replayed == c.steps == len(load(gate.path).steps) and c.missing_expected == []
    # the end state, declared independently of any DOM comparison
    assert state["titles"] == FINAL_TITLES_REPLICA
    assert state["completed"] == [False, False]
    assert state["left"] == "2 items left" and state["clear_hidden"] is True
    stored = json.loads(state["stored"])
    assert [(t["title"], t["completed"]) for t in stored] == [
        (t, False) for t in FINAL_TITLES_REPLICA
    ]
    # and it is the source's end state, with the synthetic values in place of what was typed
    src = gate.source_state
    assert src["titles"] == FINAL_TITLES_SOURCE
    mapping = dict(zip(tf.TYPED, SYNTHETIC, strict=True))
    assert [mapping[t] for t in src["titles"]] == state["titles"]
    assert (src["completed"], src["left"], src["clear_hidden"]) == (
        state["completed"], state["left"], state["clear_hidden"])  # fmt: skip
    src_stored = [(mapping[t["title"]], t["completed"]) for t in json.loads(src["stored"])]
    assert src_stored == [(t["title"], t["completed"]) for t in stored]


def test_two_titles_typed_into_the_same_input_replay_as_two_titles(servers, tmp_path):
    def flow(page):
        new = page.locator(".new-todo")
        for title in ("First thing", "Second thing"):
            new.fill(title)
            new.press("Enter")
            tf.pause(page)

    out = recorder.record(f"{servers.source}/index.html", tmp_path / "rec", script=flow,
                          headless=True)  # fmt: skip
    typed = [s for s in load(out).steps if s.action == "type"]
    assert len(typed) == 2 and typed[0].selector == typed[1].selector == "input.new-todo"
    fill_bindings(out, ["Synthetic one", "Synthetic two"])
    seen = {}
    report = fidelity.replay(out, servers.replica, after=lambda p: seen.update(tf.state(p)))
    assert report.status == "pass", report.markdown()
    assert seen["titles"] == ["Synthetic one", "Synthetic two"]


def test_the_gate_passes_its_declared_acceptance_cases_within_scope(gate, replayed, servers,
                                                                   tmp_path):  # fmt: skip
    """R05/R11 on the gate: the end state declared from the flow, values from the bindings."""
    copy = tmp_path / "rec"
    shutil.copytree(gate.path, copy)
    typed = [s.seq for s in load(copy).steps if s.action == "type" and s.blank is None]
    titles = [{"binding": typed[2]}, {"binding": typed[7]}]
    (copy / "acceptance.yaml").write_text(yaml.safe_dump({
        "scope": ["the first completion gate"], "cases": [
            {"id": "titles", "check": "texts", "locator": ".todo-list li label", "equals": titles},
            {"id": "none-completed", "check": "count", "locator": ".todo-list li.completed",
             "equals": 0},
            {"id": "left", "check": "text", "locator": ".todo-count", "equals": "2 items left"},
            {"id": "stored", "check": "storage", "area": "localStorage", "key": tf.KEY,
             "path": "$[1].title", "equals": titles[1]},
            {"id": "after-reload", "check": "texts", "reload": True, "category": "data",
             "locator": ".todo-list li label", "equals": titles},
            {"id": "isolated", "check": "count", "fresh_context": True, "category": "data",
             "locator": ".todo-list li", "equals": 0},
        ]}), "utf-8")  # fmt: skip
    report, _ = replay_gate(copy, servers.replica, artifacts=tmp_path / "art")
    assert report.verdict == "PASS WITHIN DECLARED SCOPE", report.markdown()
    cats = report.categories
    assert (cats["capture"].status, cats["replay"].status) == ("PASS", "PASS")
    assert cats["capture"].required_passed == cats["capture"].required_total == len(tf.EXPECTED)
    assert (cats["behaviour"].required_passed, cats["data"].required_passed) == (3, 3)
    fp = report.fingerprint
    assert fp["recording"]["digest"] and fp["files"]["acceptance"]["digest"]
    assert fp["files"]["bindings"]["digest"] and fp["browser"]["name"] == "chromium"


def test_filter_links_are_targeted_although_their_selector_is_a_bare_a(gate, replayed):
    rec = load(gate.path)
    links = [s for s in rec.steps if s.action == "click" and s.selector in ("a", "a.selected")]
    assert [s.locators[0]["name"] for s in links] == ["Active", "Completed", "All"]
    assert all(s.locators[0]["kind"] == "role" and s.locators[0]["count"] == 1 for s in links)
    report, _ = replayed
    by_seq = {r.seq: r for r in report.steps}
    assert [by_seq[s.seq].target for s in links] == [
        'role link "Active"', 'role link "Completed"', 'role link "All"']  # fmt: skip
    # each caused navigation arrived at the filter's own address on the replica
    for s in links:
        nav = next(x for x in rec.steps if x.caused_by == s.seq)
        assert by_seq[nav.seq].status == "pass" and by_seq[nav.seq].outcome == "implied"


def test_three_fresh_context_repetitions_pass(gate, servers):
    for _ in range(3):
        report, state = replay_gate(gate.path, servers.replica)
        assert report.status == "pass", report.markdown()
        assert state["titles"] == FINAL_TITLES_REPLICA


# --- (3) fail-closed ----------------------------------------------------------------------------


def _without(rec_dir: Path, dest: Path, drop) -> Path:
    shutil.copytree(rec_dir, dest)
    lines = (dest / "steps.jsonl").read_text("utf-8").splitlines()
    kept = [x for x in lines if not drop(json.loads(x))]
    assert len(kept) == len(lines) - 1
    (dest / "steps.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    return dest


def test_removing_one_required_event_blocks_the_replay(gate, servers, replayed, tmp_path):
    steps = steps_of(gate.path)
    third_add = [s["seq"] for s in steps if s["action"] == "type" and s["selector"] == "input.new-todo"
                 and not s.get("blank")][2]  # fmt: skip
    copy = _without(gate.path, tmp_path / "rec",
                    lambda s: s["seq"] == third_add + 1 and s["action"] == "keypress")  # fmt: skip
    report, _ = replay_gate(copy, servers.replica, resolve_timeout_ms=1500)
    assert report.status == "blocked", report.markdown()
    assert report.coverage.unresolved == 1 and report.coverage.skipped > 0
    assert "keypress:Enter" in report.coverage.missing_expected
    blocked = next(r for r in report.steps if r.status == "blocked")
    assert "matched 0" in blocked.note or "0 of" in blocked.note, blocked.note


def test_a_missing_binding_blocks_and_nothing_passes_from_zero_steps(gate, servers, tmp_path):
    copy = tmp_path / "rec"
    shutil.copytree(gate.path, copy)
    (copy / bnd.FILE).unlink()
    report, _ = replay_gate(copy, servers.replica, resolve_timeout_ms=1000)
    assert report.status == "blocked"
    first = next(r for r in report.steps if r.status == "blocked")
    assert first.outcome == "missing_value" and "bindings.yaml" in first.note
    assert report.coverage.missing_value == 1
    empty = tmp_path / "empty"
    (empty / "dom").mkdir(parents=True)
    (empty / "steps.jsonl").write_text("", encoding="utf-8")
    nothing = fidelity.replay(empty, servers.replica)
    assert nothing.status == "blocked" and "no step was replayed" in nothing.problems[-1]


def test_a_blocked_report_reads_back_for_the_readiness_gate(tmp_path):
    report = fidelity.FidelityReport(recording="r", status="blocked", problems=["step 3 BLOCKED"])
    fidelity.save(tmp_path, "demo", report)
    name, back = fidelity.latest(tmp_path, "demo")
    assert back.status == "blocked" and back.problems == ["step 3 BLOCKED"]
    assert "**Result: blocked**" in back.markdown()


# --- (6) per-record controls after reordering ----------------------------------------------------


def baseline(origin: str, titles: list[str], done: tuple[str, ...] = ()) -> dict:
    todos = [{"id": i + 1, "title": t, "completed": t in done} for i, t in enumerate(titles)]
    return {"cookies": [], "origins": [{"origin": origin, "localStorage": [
        {"name": tf.KEY, "value": json.dumps(todos)}]}]}  # fmt: skip


@pytest.fixture(scope="module")
def on_records(servers, tmp_path_factory):
    """Recorded on a source whose list was already there: complete one record, delete another."""

    def flow(page):
        tf.row(page, "Beta task").locator(".toggle").check()
        tf.pause(page)
        target = tf.row(page, "Gamma task")
        target.hover()
        target.locator(".destroy").click()
        tf.pause(page, 700)

    out = tmp_path_factory.mktemp("records") / "rec"
    state = baseline(servers.source, ["Alpha task", "Beta task", "Gamma task"])
    recorder.record(f"{servers.source}/index.html", out, script=flow, headless=True,
                    storage_state=state)  # fmt: skip
    return out


def test_actions_on_existing_records_need_the_record_named(on_records):
    rec = load(on_records)
    needs = bnd.needs(rec)
    assert [(n.action, n.kind) for n in needs] == [("check", "record"), ("click", "record")]
    for step in (s for s in rec.steps if s.action in ("check", "click")):
        assert step.locators[0]["kind"] == "record"  # no candidate picks the row by position
        assert not any(c["kind"] in ("structure", "role", "css") for c in step.locators)


def test_reordered_rows_still_hit_the_intended_record_or_block(on_records, servers):
    rec = load(on_records)
    check_seq, click_seq = (n.seq for n in bnd.needs(rec))
    reordered = baseline(servers.replica, ["Gamma task", "Beta task", "Alpha task"])

    def run(bindings, state):
        seen = {}
        report = fidelity.replay(rec, servers.replica, bindings=bindings, storage_state=state,
                                 resolve_timeout_ms=1000,
                                 after=lambda p: seen.update(tf.state(p)))  # fmt: skip
        return report, seen

    # no record named: blocked, and nothing was changed (no other row was taken instead)
    report, seen = run(bnd.Bindings(), reordered)
    assert report.status == "blocked" and report.coverage.missing_value == 1
    assert seen["titles"] == ["Gamma task", "Beta task", "Alpha task"]
    assert seen["completed"] == [False, False, False]
    # named: the intended records, wherever they are now
    named = bnd.Bindings(records={check_seq: "Beta task", click_seq: "Gamma task"})
    report, seen = run(named, reordered)
    assert report.status == "pass", report.markdown()
    assert seen["titles"] == ["Beta task", "Alpha task"]
    assert seen["completed"] == [True, False]
    # two records with that title: ambiguous, blocked, nothing changed
    twice = baseline(servers.replica, ["Beta task", "Gamma task", "Beta task", "Alpha task"])
    report, seen = run(named, twice)
    assert report.status == "blocked" and report.coverage.unresolved == 1
    blocked = next(r for r in report.steps if r.status == "blocked")
    assert "2 of 4 record(s) have that title" in blocked.note
    assert seen["completed"] == [False, False, False, False]


MASKED_PAGE = """<ul>
<li><a id="loan-L-200040" href="#a">open</a> Loan for Ann</li>
<li><a id="loan-L-200041" href="#b">open</a> Loan for Bob</li>
</ul>"""


def test_a_masked_record_id_is_taken_only_when_one_record_fits():
    from playwright.sync_api import sync_playwright

    from labctl.locate import Unresolved, resolve

    step = SimpleNamespace(selector="#loan-L-999999", locators=[], frame=None)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(MASKED_PAGE)
        with pytest.raises(Unresolved) as exc:  # two have that shape: never the first one
            resolve(page, step, timeout_ms=300)
        assert "matched 2" in str(exc.value) and "name the record" in str(exc.value)
        loc, how = resolve(page, step, title="open Loan for Bob", timeout_ms=300)
        assert loc.get_attribute("id") == "loan-L-200041" and how == "css #loan-L-999999"
        page.set_content(MASKED_PAGE.split("</li>")[0] + "</li></ul>")
        loc, _ = resolve(page, step, timeout_ms=300)  # the only one of that shape
        assert loc.get_attribute("id") == "loan-L-200040"
        browser.close()


# --- (7) older recordings ------------------------------------------------------------------------

SHOWCASE = Path(__file__).parent / "fixtures" / "showcase-rec"


def test_an_old_format_recording_still_loads_and_replays():
    rec = load(SHOWCASE)
    assert len(rec.steps) == 24 and not bnd.new_format(rec)
    assert not any(s.locators or s.key or s.nav for s in rec.steps)
    # older per-selector values become bindings for the steps they match
    values = {"4:input#password": "not-the-password", "7:input#password": "workbench-demo",
              "16:select#category": "Cards", "17:select#subcategory": "Declined payment",
              "18:select#caseStatus": "RESOLVED", "21:select#caseStatus": "ESCALATED",
              "22:input#escalationReason": "short"}  # fmt: skip
    translated = bnd.from_values(rec, values)
    assert translated.values[4] == "not-the-password" and translated.values[7] == "workbench-demo"
    # the rest (user name, case number, reason) the old snapshots hold as their value
    assert {n.seq for n in bnd.needs(rec)} == {4, 7, 16, 17, 18, 21}
    # served from its own HAR: no application and no network needed
    report = fidelity.replay(
        rec, None, values, resolve_timeout_ms=3000,
        setup=lambda ctx: ctx.route_from_har(str(SHOWCASE / "recording.har"), not_found="abort"),
    )  # fmt: skip
    c = report.coverage
    assert (c.replayed, c.skipped, c.unresolved, c.missing_value) == (24, 0, 0, 0), (
        report.markdown()
    )
    # the submits the clicks caused are not made a second time
    assert {r.seq for r in report.steps if r.outcome == "implied"} >= {6, 9, 13, 20, 24}
    # what differs is only what this recording snapshotted early (before the page rendered)
    kinds = {d.check for r in report.steps for d in r.differences}
    assert kinds <= {"extra", "missing", "value_format", "enabled"}, report.markdown()


# --- (8) privacy --------------------------------------------------------------------------------


def _forms(value: str) -> set[str]:
    from urllib.parse import quote, quote_plus

    return {value.lower(), quote(value).lower(), quote_plus(value).lower()}


def test_no_typed_value_is_in_any_written_artifact(gate, replayed, tmp_path):
    report, _ = replayed
    files = [p for p in gate.path.rglob("*") if p.is_file() and p.suffix != ".png"]
    assert {p.name for p in files} >= {"steps.jsonl", "recording.har", "bindings.yaml"}
    template = tmp_path / "template.yaml"
    template.write_text(bnd.template(load(gate.path)), encoding="utf-8")
    texts = {p: p.read_text(encoding="utf-8").lower() for p in [*files, template]}
    texts[Path("report.json")] = report.model_dump_json().lower()
    leaks = [(p.name, v) for p, text in texts.items() for v in tf.TYPED
             for form in _forms(v) if form in text]  # fmt: skip
    # the bindings file the person filled holds only synthetic values
    assert leaks == []
    # every step that showed a typed value has a picture: masked, or not kept
    assert any((gate.path / "screens").glob("*.png"))


def test_secrets_are_taken_out_of_markup_and_har_strings():
    s = recorder._Secrets()
    s.add("Water plants", "http://x", "t1")
    s.add("ab", "http://x", "t2")
    s.add("   ", "http://x", "t3")  # blank: not a secret
    html = '<li title="Water plants"><label>Water plants</label><b>ab</b><i>about</i></li>'
    out = s.redact_html(html)
    assert "Water plants" not in out and "<label>A A</label>" in out
    assert "<b>A</b>" in out and "<i>about</i>" in out  # a short value only where it is all
    har = {"q": "title=Water%20plants&x=ab", "body": '{"t":"ab","u":"Water+plants"}'}
    assert "Water" not in json.dumps(s.scrub(har)) and '"t":"A"' in s.scrub(har)["body"]
    step = {"locators": [{"kind": "role", "name": "Water plants"}, {"kind": "id", "value": "x"}]}
    assert s.clean_step(step)["locators"] == [{"kind": "id", "value": "x"}]


def test_missing_actions_matches_in_order():
    assert fidelity.missing_actions(["type", "keypress:Enter"], ["type", "keypress:Enter"]) == []
    assert fidelity.missing_actions(["keypress", "type"], ["type", "keypress:Escape"]) == ["type"]
    assert fidelity.missing_actions(["check"], []) == ["check"]
