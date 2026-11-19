"""Acceptance cases beyond structure (replication gap R05).

A short flow is recorded on the independent to-do fixture; acceptance cases are declared here,
from the flow, with synthetic expected values from the bindings. Deliberately wrong replicas
(made in tmp_path from the fixture) keep the DOM structure the replay compares, and each fails
the case that covers its fault. Everything is served from 127.0.0.1.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

pytest.importorskip("playwright.sync_api")

import replica_kit as kit  # noqa: E402

from labctl import recorder  # noqa: E402
from labctl.behaviour import acceptance as acc  # noqa: E402
from labctl.behaviour import fidelity  # noqa: E402
from labctl.botlog.recording import load  # noqa: E402

KEY = "tl-todos"


def cases(first: int, second: int) -> dict:
    """Declared from the flow: two tasks added, the first completed, the page reloaded."""
    b1, b2 = {"binding": first}, {"binding": second}
    return {"version": 1, "scope": ["add two tasks", "complete one", "reload"],
            "excluded": ["page styling"], "unobserved": ["what a blank title shows"],
            "cases": [
        {"id": "titles", "check": "texts", "locator": ".todo-list li label", "equals": [b1, b2]},
        {"id": "completed-count", "check": "count", "locator": ".todo-list li.completed",
         "equals": 1},
        {"id": "first-flag", "check": "checked", "locator": ".todo-list li:first-child .toggle",
         "equals": True},
        {"id": "left-count", "check": "text", "locator": ".todo-count strong", "equals": "1"},
        {"id": "left-message", "check": "text", "locator": ".todo-count", "equals": "1 item left"},
        {"id": "saved-title", "check": "storage", "area": "localStorage", "key": KEY,
         "path": "$[0].title", "equals": b1},
        {"id": "saved-flag", "check": "storage", "area": "localStorage", "key": KEY,
         "path": "$[0].completed", "equals": True},
        {"id": "saved-count", "check": "storage", "area": "localStorage", "key": KEY,
         "path": "$.length", "equals": 2},
        {"id": "address", "check": "url", "matches": r"^/index\.html"},
        {"id": "kept-after-reload", "check": "texts", "reload": True, "category": "data",
         "locator": ".todo-list li label", "equals": [b1, b2]},
        {"id": "fresh-context-isolated", "check": "count", "fresh_context": True,
         "category": "data", "locator": ".todo-list li", "equals": 0},
        {"id": "clear-shown", "check": "visible", "locator": ".clear-completed",
         "required": False},
    ]}  # fmt: skip


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    src_dir = kit.make_app(tmp_path_factory.mktemp("src"), "source.html")
    source = kit.serve_dir(src_dir)
    out = tmp_path_factory.mktemp("acc") / "rec"
    recorder.record(f"{tf_base(source)}/index.html", out, script=kit.short_flow, headless=True)
    source.shutdown()
    kit.write_bindings(out, kit.SYNTHETIC)
    first, second = kit.typed_steps(out)
    (out / acc.FILE).write_text(yaml.safe_dump(cases(first, second), sort_keys=False), "utf-8")
    return out


def tf_base(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def run(rec_dir, tmp_path, js=(), **kw):
    app = kit.make_app(tmp_path / "replica", "replica.html", js=js)
    server = kit.serve_dir(app)
    try:
        seen = {}
        report = fidelity.replay(rec_dir, tf_base(server), artifacts=tmp_path / "art",
                                 after=lambda p: seen.update(stored=p.evaluate(
                                     "k => localStorage.getItem(k)", KEY)), **kw)  # fmt: skip
        return SimpleNamespace(report=report, by={c.id: c for c in report.cases}, seen=seen)
    finally:
        server.shutdown()


def structure_passed(report) -> bool:
    return report.categories["replay"].status == "PASS" and not any(
        d for s in report.steps for d in s.differences
    )


def test_a_correct_replica_passes_every_required_case(recorded, tmp_path):
    r = run(recorded, tmp_path)
    failed = {c.id: (c.status, c.expected, c.actual, c.note) for c in r.report.cases
              if c.status != "PASS"}  # fmt: skip
    assert failed == {}, r.report.markdown()
    assert r.report.status == "pass" and r.report.verdict == "PASS WITHIN DECLARED SCOPE"
    cats = r.report.categories
    assert (cats["behaviour"].required_passed, cats["behaviour"].required_total) == (5, 5)
    assert (cats["data"].required_passed, cats["data"].required_total) == (6, 6)
    assert cats["behaviour"].optional_total == 1 and cats["appearance"].status == "NOT_DECLARED"
    # the expected values were the synthetic ones, never the source's
    assert r.by["titles"].expected == kit.SYNTHETIC
    assert [t["title"] for t in json.loads(r.seen["stored"])] == kit.SYNTHETIC
    req = r.report.requirements
    assert req.scope == ["add two tasks", "complete one", "reload"]
    assert req.excluded == ["page styling"] and req.unobserved == ["what a blank title shows"]
    assert len(req.evidence_backed) == 12 and any("no expected action" in a for a in req.assumed)
    assert r.report.first_divergence is None
    md = r.report.markdown()
    assert "PASS WITHIN DECLARED SCOPE" in md and "| titles | behaviour | yes" in md


WRONG = {
    # the counter counts every task, not those left
    "count": ([('"<strong>" + left + "</strong> "', '"<strong>" + todos.length + "</strong> "')],
              {"left-count"}),
    # a saved title is shown changed once read back (a title shown wrong from the start would not
    # name its record, and the replay would block on the step that completes it)
    "title": ([("return Array.isArray(data) ? data : [];",
                "return Array.isArray(data) ? data.map(function (t) { t.title += '.'; return t; })"
                " : [];")],
              {"titles", "kept-after-reload"}),
    # the completion is saved but never shown
    "flag": ([("box.checked = !!todo.completed;", "box.checked = false;"),
              ('if (todo.completed) li.className = "completed";', "")],
             {"completed-count", "first-flag"}),
    # kept only for the tab's session: a reload shows it, the browser does not keep it
    "persistence": ([("function save() { localStorage.setItem(",
                      "function save() { sessionStorage.setItem("),
                     ('JSON.parse(localStorage.getItem(KEY) || "[]")',
                      'JSON.parse(sessionStorage.getItem(KEY) || "[]")')],
                    {"saved-title", "saved-flag", "saved-count"}),
    # the message is wrong for one task
    "message": ([('(left === 1 ? "item" : "items")', '"items"')], {"left-message"}),
}  # fmt: skip


@pytest.mark.parametrize("fault", sorted(WRONG))
def test_a_wrong_replica_fails_its_case_although_the_structure_matches(recorded, tmp_path, fault):
    js, expected_failures = WRONG[fault]
    r = run(recorded, tmp_path, js=js)
    assert structure_passed(r.report), r.report.markdown()
    failed = {c.id for c in r.report.cases if c.status == "FAIL"}
    assert expected_failures <= failed, r.report.markdown()
    assert r.report.verdict == "FAIL" and r.report.status == "fail"
    assert any("required acceptance case(s) failed" in p for p in r.report.problems)
    # the first divergence names the case, with the replica's picture
    div = r.report.first_divergence
    assert div.kind == "case" and div.what in expected_failures, div
    assert div.after and div.after.endswith(".png")
    # no average hides a failed category: the others may pass, the status is still FAIL
    failing = {c.category for c in r.report.cases if c.status == "FAIL"}
    assert all(r.report.categories[n].status == "FAIL" for n in failing)


def test_zero_cases_executed_is_blocked_never_pass(recorded, tmp_path):
    r = run(recorded, tmp_path, acceptance=False)
    assert r.report.status == "pass"  # the structure alone, as current readers know it
    assert r.report.verdict == "BLOCKED" and r.report.cases == []
    assert "no required acceptance case ran" in r.report.verdict_reasons[-1]
    only_optional = tmp_path / "optional.yaml"
    only_optional.write_text(yaml.safe_dump({"cases": [
        {"id": "o", "check": "count", "locator": ".todo-list li", "equals": 2,
         "required": False}]}), "utf-8")  # fmt: skip
    r = run(recorded, tmp_path / "2", acceptance=only_optional)
    assert r.by["o"].status == "PASS" and r.report.verdict == "BLOCKED"


def test_unfinished_ambiguous_or_unreached_cases_block(recorded, tmp_path):
    spec = tmp_path / "cases.yaml"
    spec.write_text(yaml.safe_dump({"cases": [
        {"id": "todo", "check": "text", "locator": ".todo-count", "equals": "TODO"},
        {"id": "ambiguous", "check": "text", "locator": ".todo-list li label", "equals": "x"},
        {"id": "unbound", "check": "text", "locator": ".todo-count", "equals": {"binding": 99}},
        {"id": "nowhere", "after": 999, "check": "count", "locator": "li", "equals": 1},
        {"id": "nowhere-optional", "after": 999, "check": "count", "locator": "li",
         "equals": 1, "required": False},
        {"id": "db", "check": "outcome", "automation": "some-bot"},
        {"id": "typo", "check": "txet", "locator": "li", "equals": 1},
    ]}), "utf-8")  # fmt: skip
    r = run(recorded, tmp_path, acceptance=spec)
    status = {c.id: c.status for c in r.report.cases}
    assert status == {"todo": "BLOCKED", "ambiguous": "BLOCKED", "unbound": "BLOCKED",
                      "nowhere": "BLOCKED", "nowhere-optional": "SKIPPED", "db": "BLOCKED",
                      "typo": "BLOCKED"}, r.report.markdown()  # fmt: skip
    assert "matched 2 elements" in r.by["ambiguous"].note
    assert "TODO" in r.by["todo"].note and "outcome.py" in r.by["db"].note
    assert r.report.verdict == "BLOCKED" and r.report.status == "blocked"


def test_outcome_cases_reuse_the_outcome_judge(recorded, tmp_path):
    from labctl.outcome import MET, NOT_MET, Outcome

    spec = tmp_path / "cases.yaml"
    spec.write_text(
        yaml.safe_dump(
            {"cases": [{"id": "db", "check": "outcome", "automation": "bot", "case": "default"}]}
        ),
        "utf-8",
    )
    seen = []

    def judge(automation, case):
        seen.append((automation, case))
        return Outcome(MET, case=case)

    r = run(recorded, tmp_path, acceptance=spec, outcome_judge=judge)
    assert seen == [("bot", "default")] and r.by["db"].status == "PASS"
    assert r.by["db"].category == "data" and r.report.verdict == "PASS WITHIN DECLARED SCOPE"
    r = run(recorded, tmp_path / "2", acceptance=spec,
            outcome_judge=lambda a, c: Outcome(NOT_MET, case=c, unmet=["no insert"]))  # fmt: skip
    assert r.by["db"].status == "FAIL" and "no insert" in r.by["db"].note


def test_the_template_lists_candidate_cases_with_todo_values(recorded, tmp_path):
    from typer.testing import CliRunner

    from labctl.behaviour.cli import app

    out = tmp_path / "acceptance.yaml"
    result = CliRunner().invoke(app, ["acceptance", str(recorded), "--out", str(out)])
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(out.read_text("utf-8"))
    checks = {(c["check"], c.get("locator")) for c in doc["cases"]}
    assert ("count", "ul.todo-list > li") in checks and ("texts", "ul.todo-list > li") in checks
    assert ("text", "span.todo-count") in checks and ("checked", "#toggle-all") in checks
    assert any(c.get("fresh_context") for c in doc["cases"])
    assert all(c.get("equals", acc.TODO) == acc.TODO or c["check"] == "storage"
               for c in doc["cases"])  # fmt: skip
    text = out.read_text("utf-8").lower()
    assert not any(v.lower() in text for v in kit.SOURCE_TYPED + kit.SYNTHETIC)
    # unfinished, every case blocks: a template never passes anything
    spec = acc.load(out)
    assert all(c.problem for c in spec.cases if c.check != "storage")
    again = CliRunner().invoke(app, ["acceptance", str(recorded), "--out", str(out)])
    assert again.exit_code == 1 and "not overwritten" in again.output


def test_json_path_and_matching():
    data = [{"title": "a", "tags": ["x", "y"]}, {"title": "b"}]
    assert acc.json_path(data, "$[1].title") == "b"
    assert acc.json_path(data, "$[0].tags.length") == 2 and acc.json_path(data, "$") == data
    with pytest.raises(KeyError):
        acc.json_path(data, "$[5].title")
    assert acc._compare("contains", "item", "1 item left")
    assert acc._compare("matches", r"^\d+ items?", "2 items left")
    assert not acc._compare("equals", True, 1) or acc._compare("equals", True, True)
    assert acc._same(["a  b"], ["a b"]) and not acc._same([1, 2], [2, 1])


def test_unordered_texts_compare_as_a_multiset():
    assert acc.same_multiset(["a", "b"], ["b", "a"]) and acc.same_multiset(["a  b"], ["a b"])
    assert not acc.same_multiset(["a", "a"], ["a", "b"])  # each item as often
    assert not acc.same_multiset(["a"], ["a", "a"]) and not acc.same_multiset("a", ["a"])
    assert acc._compare("equals", ["a", "b"], ["b", "a"], unordered=True)
    assert not acc._compare("equals", ["a", "b"], ["b", "a"])
    bad = {
        "on-count": {"check": "count", "locator": "li", "unordered": True, "equals": 2},
        "contains": {"check": "texts", "locator": "li", "unordered": True, "contains": "a"},
        "not-a-list": {"check": "texts", "locator": "li", "unordered": True, "equals": "a"},
    }
    for cid, entry in bad.items():
        assert acc.parse_case({"id": cid, **entry}, 1).problem, cid
    ok = acc.parse_case({"id": "t", "check": "texts", "locator": "li", "unordered": True,
                         "equals": ["a", "b"]}, 1)  # fmt: skip
    assert ok.problem is None and ok.unordered


def test_an_unordered_texts_case_passes_in_any_order(recorded, tmp_path):
    first, second = kit.typed_steps(recorded)
    b1, b2 = {"binding": first}, {"binding": second}
    spec = tmp_path / "cases.yaml"
    spec.write_text(yaml.safe_dump({"cases": [
        {"id": "any-order", "check": "texts", "locator": ".todo-list li label", "unordered": True,
         "equals": [b2, b1]},
        {"id": "pinned-order", "check": "texts", "locator": ".todo-list li label",
         "equals": [b2, b1]},
        {"id": "missing-item", "check": "texts", "locator": ".todo-list li label",
         "unordered": True, "equals": [b1, b1]},
    ]}), "utf-8")  # fmt: skip
    r = run(recorded, tmp_path, acceptance=spec)
    status = {c.id: c.status for c in r.report.cases}
    assert status == {"any-order": "PASS", "pinned-order": "FAIL", "missing-item": "FAIL"}
    assert "any order" in r.by["any-order"].note


def test_no_typed_value_in_the_report(recorded, tmp_path):
    r = run(recorded, tmp_path)
    text = r.report.model_dump_json().lower() + r.report.markdown().lower()
    assert not any(v.lower() in text for v in kit.SOURCE_TYPED)
    assert load(recorded).steps  # the recording itself is unchanged
