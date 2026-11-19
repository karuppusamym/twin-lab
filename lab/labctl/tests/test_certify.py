"""The certification matrix (`labctl certify`, docs/10-certification.md): the matrix file is
parsed and refused when wrong, a result never counts a gate that did not run as passed, the table
is rendered from the kept results, and the server-rendered fixture goes through the real recorder
and a real replay once, and so does the Angular entry on its committed build. The React entry
runs only where its vendor files were built (not in CI), the JSF entry only where there is a JDK
and its jars were fetched. Desktop entries are never started here: without TWINLAB_DESKTOP_GUI=1
they are NOT RUN, and their gate is checked on reports made by hand.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
import yaml

from labctl import certify
from labctl.certify import CAPTURE_VERIFIED, CERTIFIED, FAIL, NOT_RUN, PARTIAL, PASS, Gate

ROOT = Path(__file__).resolve().parents[3]
MATRIX = ROOT / "certification" / "matrix.yaml"
NO_APP = "no reference application has been built for it yet"
DECLARED_ONLY = ("vue", "aspnet-webforms")


def entry(**over) -> dict:
    base = {"id": "x", "technology": "X", "surface": "web", "gates": {"detect": {"framework": "x"}}}
    return {**base, **over}


# --- the matrix file -------------------------------------------------------------------------------


def test_the_repository_matrix_loads_and_declares_every_technology():
    mx = certify.load_matrix(MATRIX)
    ids = [e.id for e in mx.entries]
    assert ids == ["html-forms", "react-16", "angular", "vue", "jsf-mojarra", "aspnet-webforms",
                   "winforms", "wpf", "java-swing"]  # fmt: skip
    for e in mx.entries:
        assert e.gates["detect"], e.id  # each says what must be detected
        if e.id not in DECLARED_ONLY:
            assert e.app is not None and e.not_run is None, e.id
    for name in DECLARED_ONLY:
        e = mx.entry(name)
        assert e.app is None and e.flow is None and e.not_run == NO_APP
    assert "not approved" not in MATRIX.read_text("utf-8")  # nothing waits for an approval
    for name, kind in (("angular", "caseboard_angular"), ("jsf-mojarra", "caseboard_jsf")):
        e = mx.entry(name)  # started by the application's own module, under its own names
        assert (e.app.kind, e.app.namespace, e.flow.base_url) == ("module", kind, True)
        assert (ROOT / e.app.file).is_file() and (ROOT / e.flow.file).is_file()
        assert (ROOT / e.gates["capture"]["expected_file"]).is_file()
    assert mx.entry("jsf-mojarra").gates["detect"]["server"] == {
        "technology": "Java", "framework": "JSF", "framework_version": "2.3",
        "implementation": "Mojarra"}  # fmt: skip
    assert mx.entry("react-16").gates["detect"] == {
        "framework": "react", "version": "16.7.0", "rendering": "client"}  # fmt: skip
    assert {mx.entry(i).surface for i in ("winforms", "wpf", "java-swing")} == {"desktop"}


def test_an_unknown_gate_is_refused():
    bad = entry(gates={"detect": {"framework": "x"}, "replay": {}})
    with pytest.raises(certify.MatrixError, match=r"entry x: gates: unknown gate 'replay'"):
        certify.parse_matrix({"entries": [bad]})


def test_a_duplicate_id_is_refused():
    with pytest.raises(certify.MatrixError, match="duplicate id 'x'"):
        certify.parse_matrix({"entries": [entry(), entry(technology="Y")]})


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"gates": {}}, "gates.detect is missing"),
        ({"surface": "mainframe"}, "surface"),
        ({"app": {"kind": "static"}}, "needs `folder`"),
        ({"app": {"kind": "python", "file": "a.py"}}, "needs `file` and `origins`"),
        ({"app": {"kind": "winforms", "folder": "f"}}, "does not fit surface 'web'"),
        ({"app": {"kind": "static", "folder": "f"}}, "needs a flow"),
        ({"app": {"kind": "module"}}, "a module app needs `file`"),
        ({"app": {"kind": "module", "file": "a.py", "unavailable": "b.py"}}, "file.py:function"),
        ({"colour": "blue"}, "colour"),
    ],
)
def test_a_wrong_entry_is_refused_with_the_place(change, says):
    with pytest.raises(certify.MatrixError) as err:
        certify.parse_matrix({"entries": [entry(**change)]}, "m.yaml")
    assert says in str(err.value) and "m.yaml: entry x:" in str(err.value)


def test_a_file_that_is_no_matrix_is_refused(tmp_path):
    with pytest.raises(certify.MatrixError, match="needs `entries`"):
        certify.parse_matrix(["a"])
    with pytest.raises(certify.MatrixError, match="no such file"):
        certify.load_matrix(tmp_path / "none.yaml")
    with pytest.raises(certify.MatrixError, match="no entry 'nope'"):
        certify.load_matrix(MATRIX).entry("nope")


# --- the result ------------------------------------------------------------------------------------


def expected_result(statuses: tuple[str, ...]) -> str:
    """The rule, written out again apart from the code under test."""
    if FAIL in statuses:
        return PARTIAL
    if statuses == (PASS, PASS, PASS, PASS):
        return CERTIFIED
    if statuses == (PASS, PASS, PASS, NOT_RUN):
        return CAPTURE_VERIFIED
    if statuses[:3] == (NOT_RUN, NOT_RUN, NOT_RUN) and statuses[3] != PASS:
        return NOT_RUN
    return PARTIAL


def test_every_combination_of_gate_outcomes_gives_the_right_result():
    seen = set()
    for statuses in itertools.product((PASS, FAIL, NOT_RUN), repeat=4):
        gates = {g: Gate(status=s, detail=f"{g} detail")
                 for g, s in zip(certify.GATES, statuses, strict=True)}  # fmt: skip
        result, reason = certify.decide(gates)
        assert result == expected_result(statuses), statuses
        seen.add(result)
        # NOT RUN never counts as a pass
        if NOT_RUN in statuses:
            assert result != CERTIFIED
        if NOT_RUN in statuses[:3]:
            assert result not in (CERTIFIED, CAPTURE_VERIFIED)
        # a failed gate is named with its reason; so is each gate missing from a PARTIAL
        for g, s in zip(certify.GATES, statuses, strict=True):
            if s == FAIL:
                assert f"{g} failed: {g} detail" in reason
            elif s == NOT_RUN and result == PARTIAL and FAIL not in statuses:
                assert f"{g} not run: {g} detail" in reason
    assert seen == {CERTIFIED, CAPTURE_VERIFIED, PARTIAL, NOT_RUN}
    assert len(seen) == 4 and 3**4 == 81


def test_a_gate_with_no_result_is_not_run():
    gates = {g: Gate(status=PASS) for g in certify.GATES[:3]}  # rebuild is absent
    assert certify.decide(gates)[0] == CAPTURE_VERIFIED
    assert certify.decide({})[0] == NOT_RUN
    assert Gate().status == NOT_RUN  # what a gate is until it has run


def test_detect_compares_technology_and_version():
    react = {"framework": "react", "version": "16.7.0", "rendering": "client"}
    found = {**react, "marker": "react-dom bundle, rendererPackageName"}
    gate = certify.detect_gate(react, found)
    assert gate.status == PASS and "version 16.7.0" in gate.detail
    assert gate.facts["marker"] == "react-dom bundle, rendererPackageName"
    wrong = certify.detect_gate(react, {**found, "version": "18.3.1"})
    assert wrong.status == FAIL
    assert wrong.detail == "version: declared 16.7.0, detected 18.3.1"
    assert certify.detect_gate(react, {"framework": "vue"}).status == FAIL
    assert certify.detect_gate(react, None).status == FAIL  # nothing detected
    # an entry that declares no version passes only when none was detected
    plain = {"framework": "server-rendered"}
    assert certify.detect_gate(plain, {"framework": "server-rendered"}).status == PASS
    odd = certify.detect_gate(plain, {"framework": "server-rendered", "version": "2"})
    assert odd.status == FAIL and "declared none, detected 2" in odd.detail
    # a declared version may be coarser than the detected one, never different
    net = {"toolkit": "WinForms", "runtime": {"name": ".NET", "version": "6"}}
    seen = {"toolkit": "WinForms", "runtime": {"name": ".NET", "version": "6.0.36"}}
    assert certify.detect_gate(net, seen).status == PASS
    seen["runtime"]["version"] = "60.1"
    assert certify.detect_gate(net, seen).status == FAIL
    assert certify.detect_gate(net, {"toolkit": "WinForms"}).status == FAIL
    assert certify.detect_gate(net, {"toolkit": "WinForms", "runtime": {
        "name": ".NET", "version": "6.0.3", "marker": "m"}}).detail == (
        "detected toolkit WinForms, runtime.name .NET, runtime.version 6.0.3")  # fmt: skip
    jsf = {"framework": "JSF", "server": {"technology": "Java"}}
    assert certify.detect_gate(jsf, {"framework": "JSF", "server": {"technology": "PHP"}}).detail == (
        "server.technology: declared Java, detected PHP")  # fmt: skip


def test_capture_needs_every_expected_action():
    got = ["goto", "type", "keypress:Enter", "click", "goto:reload"]
    ok = certify.capture_gate(["type", "keypress:Enter", "click", "goto:reload"], got)
    assert ok.status == PASS and ok.facts == {"expected": 4, "captured": 5, "missing": []}
    lost = certify.capture_gate(["type", "keypress:Escape", "dblclick"], got)
    assert lost.status == FAIL and lost.facts["missing"] == ["keypress:Escape", "dblclick"]
    assert "2 of 3 expected action(s) not in the recording" in lost.detail
    assert certify.capture_gate(["click", "type"], got).status == FAIL  # the order counts
    assert certify.capture_gate([], got).status == NOT_RUN  # nothing declared: nothing checked
    assert certify.desktop_actions([{"action": "key", "key": "Enter"}, {"action": "menu"}]) == [
        "key:Enter", "menu"]  # fmt: skip
    assert certify.expected_actions({"expected": ["type", "click"]}, None) == ["type", "click"]

    class Flow:
        EXPECTED = [("type", "t1", "#q"), ("click", "t1", "#go")]

    assert certify.expected_actions({"expected_from": "EXPECTED"}, Flow) == ["type", "click"]


def test_expected_actions_from_a_file_beside_the_application(tmp_path):
    (tmp_path / "expected-actions.yaml").write_text(
        "version: 1\nactions:\n  - type   # a name\n  - keypress:Escape\n", "utf-8"
    )
    got = certify.expected_actions({"expected_file": "expected-actions.yaml"}, None, tmp_path)
    assert got == ["type", "keypress:Escape"]
    # the two reference applications' own lists, as the entries name them
    mx = certify.load_matrix(MATRIX)
    for name, count in (("angular", 34), ("jsf-mojarra", 24)):
        assert len(certify.expected_actions(mx.entry(name).gates["capture"], None, ROOT)) == count


def test_self_replay_passes_only_on_a_replay_that_ran_and_passed():
    from labctl.behaviour.fidelity import Coverage, Difference, FidelityReport, StepReport

    def report(status, rows=(), **cov):
        return FidelityReport(recording="r", status=status, steps=list(rows),
                              coverage=Coverage(**cov))  # fmt: skip

    ok = certify.replay_gate(report("pass", steps=5, replayed=5, compared=4))
    assert ok.status == PASS and ok.facts["failed"] == 0
    assert certify.replay_gate(report("pass", replayed=0)).status == FAIL  # nothing ran
    bad_step = StepReport(seq=7, action="click", status="fail", differences=[
        Difference(step=7, element="button#save", check="missing", expected=1, actual=0)])  # fmt: skip
    bad = certify.replay_gate(report("fail", [bad_step], steps=9, replayed=9, failed=1))
    assert bad.status == FAIL and "1 failed" in bad.detail and "step 7 (click)" in bad.detail
    blocked = certify.replay_gate(report("blocked", steps=9, replayed=3, missing_value=1), ["#q"])
    assert blocked.status == FAIL and blocked.facts["no_value_for"] == ["#q"]


def test_desktop_self_replay_never_counts_a_step_that_was_not_performed():
    """Desktop verify marks two kinds of step `skipped`: one the steps around it cover, and one
    it could not perform. Only the first is no obstacle to a pass."""
    from labctl.behaviour.fidelity import Difference, FidelityReport, StepReport

    def report(*rows, status="pass"):
        return FidelityReport(recording="r", status=status, steps=list(rows))

    done = StepReport(seq=1, action="click", status="pass")
    implied = StepReport(seq=2, action="menu_open", status="skipped", note=certify.DESKTOP_IMPLIED)
    ok = certify.desktop_replay_gate(report(done, implied))
    assert ok.status == PASS and "1 of 2 step(s) verified" in ok.detail
    assert ok.facts == {"status": "pass", "steps": 2, "replayed": 1, "implied": 1, "failed": 0,
                        "skipped": 0}  # fmt: skip
    left = StepReport(seq=3, action="select", status="skipped", note="the item holds data")
    not_all = certify.desktop_replay_gate(report(done, implied, left))  # verify itself says pass
    assert not_all.status == FAIL and not_all.facts["skipped"] == 1
    assert "1 not performed" in not_all.detail
    assert "step 3 (select) the item holds data" in not_all.detail
    bad = StepReport(seq=4, action="click", status="fail", differences=[
        Difference(step=4, element="btnOpen", check="missing", expected=1, actual=0)])  # fmt: skip
    failed = certify.desktop_replay_gate(report(done, bad, status="fail"))
    assert (
        failed.status == FAIL and "1 failed" in failed.detail and "step 4 (click)" in failed.detail
    )
    assert certify.desktop_replay_gate(report(implied)).status == FAIL  # nothing was performed


def test_rebuild_reads_a_kept_report_and_is_never_run(tmp_path):
    assert certify.rebuild_gate({}, tmp_path).status == NOT_RUN
    gone = certify.rebuild_gate({"report": "reports/x.json"}, tmp_path)
    assert gone.status == NOT_RUN and "is not there" in gone.detail
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"status": "pass", "verdict": "PASS WITHIN DECLARED SCOPE",
                                "assurance": "VERIFIED COMPLETE"}), "utf-8")  # fmt: skip
    assert certify.rebuild_gate({"report": "r.json"}, tmp_path).status == PASS
    path.write_text(json.dumps({"status": "pass", "assurance": "DECLARED CASES ONLY"}), "utf-8")
    weak = certify.rebuild_gate({"report": "r.json"}, tmp_path)
    assert weak.status == FAIL and "DECLARED CASES ONLY" in weak.detail
    path.write_text(json.dumps({"status": "fail", "assurance": None}), "utf-8")
    assert certify.rebuild_gate({"report": "r.json"}, tmp_path).status == FAIL
    path.write_text("[1]", "utf-8")
    assert certify.rebuild_gate({"report": "r.json"}, tmp_path).status == FAIL


# --- entries that do not run here ------------------------------------------------------------------


def test_declared_entries_are_not_run_with_the_reason(tmp_path):
    mx = certify.load_matrix(MATRIX)
    for name in DECLARED_ONLY:
        got = certify.run_entry(mx.entry(name), ROOT, tmp_path)
        assert got.result == NOT_RUN and got.reason == NO_APP
        assert {g.status for g in got.gates.values()} == {NOT_RUN}


STARTER = """
class Running:
    base = "http://127.0.0.1:1/"

    def stop(self):
        pass


def serve():
    return Running()


def unavailable():
    return {why!r}
"""


def module_entry(root: Path, why: str | None) -> certify.Entry:
    """An entry whose application is started by its own module (as Caseboard is), in a folder
    made here, under names of its own."""
    app = root / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "run.py").write_text(STARTER.format(why=why), "utf-8")
    (app / "flow.py").write_text("def flow(page, base_url):\n    pass\n", "utf-8")
    spec = entry(app={"kind": "module", "file": "app/run.py", "start": "/login",
                      "namespace": f"certify_test_{root.name}",
                      "unavailable": "app/run.py:unavailable"},
                 flow={"file": "app/flow.py", "run": "flow", "base_url": True})  # fmt: skip
    return certify.parse_matrix({"entries": [spec]}).entries[0]


def test_an_application_started_by_its_own_module(tmp_path):
    """What the JSF entry needs: where the application says it cannot start (no JDK, no jars),
    the entry is NOT RUN with that reason; where it can, it is started by its own function and
    recorded from its own address."""
    import sys

    no = module_entry(tmp_path / "no", "no JDK: `java` and `javac` are not on PATH")
    assert certify.missing_requirements(no.app, tmp_path / "no") == (
        "no JDK: `java` and `javac` are not on PATH")  # fmt: skip
    got = certify.run_entry(no, tmp_path / "no", tmp_path)
    assert got.result == NOT_RUN and got.reason == "no JDK: `java` and `javac` are not on PATH"
    assert {g.status for g in got.gates.values()} == {NOT_RUN}
    yes = module_entry(tmp_path / "yes", None)
    assert certify.missing_requirements(yes.app, tmp_path / "yes") is None
    run = certify.start_app(yes.app, tmp_path / "yes")
    assert run.origins == {"app": "http://127.0.0.1:1"} and run.start == "http://127.0.0.1:1/login"
    run.stop()
    assert "certify_test_yes_run" in sys.modules  # loaded under the application's own names
    (tmp_path / "yes" / "app" / "run.py").unlink()
    assert certify.missing_requirements(yes.app, tmp_path / "yes") == "app/run.py is not there"
    # the real entry: its own build.py says why (or None, where a JDK and the jars are)
    jsf = certify.load_matrix(MATRIX).entry("jsf-mojarra")
    why = certify.missing_requirements(jsf.app, ROOT)
    assert why is None or "JDK" in why or "jar(s) missing" in why
    if why:
        assert certify.run_entry(jsf, ROOT, tmp_path).reason == why


def test_bindings_given_by_a_function_of_the_flow_file(tmp_path):
    """What the JSF flow gives (`bindings_for`): a value for every type and select step, by the
    step's number, an emptied field included."""
    from labctl.behaviour import bindings

    steps = [{"seq": 1, "action": "goto"}, {"seq": 2, "action": "type"},
             {"seq": 3, "action": "click"}, {"seq": 4, "action": "select"},
             {"seq": 5, "action": "type"}]  # fmt: skip
    (tmp_path / "steps.jsonl").write_text("\n".join(json.dumps(s) for s in steps), "utf-8")
    asked = []

    def make(valued):
        asked.append(valued)
        return {
            "version": 1,
            "steps": {n: {"value": v} for n, v in zip(valued, "abc", strict=True)},
        }

    with pytest.raises(ValueError, match="zip"):
        certify.bindings_from_flow(tmp_path, lambda valued: dict(zip(valued, "ab", strict=True)))
    certify.bindings_from_flow(tmp_path, make)
    assert asked == [[2, 4, 5]]
    assert bindings.load(tmp_path / bindings.FILE).values == {2: "a", 4: "b", 5: "c"}


def test_a_step_on_a_record_gets_its_title(tmp_path, monkeypatch):
    """What the Angular flow needs: a click on a row that was there before the recording
    started is bound to the row's title (`record:`), beside the typed values."""
    from labctl.behaviour import bindings
    from labctl.botlog import recording

    template = {"version": 1, "steps": {
        2: {"action": "type", "field": "input#search", "shape": "aaaaa", "value": None},
        3: {"action": "click", "field": "a", "record_shape": "AA-9999 Aaaaa", "record": None},
        6: {"action": "click", "field": "a", "record_shape": "AA-9999 Aaaaa", "record": None},
    }}  # fmt: skip
    path = tmp_path / bindings.FILE
    monkeypatch.setattr(recording, "load", lambda rec_dir: None)

    def write(rec):
        path.write_text(yaml.safe_dump(template), "utf-8")
        return path

    monkeypatch.setattr(bindings, "write_template", write)
    unbound = certify.fill_bindings(tmp_path, {"input#search": ["valve"]}, ["CB-1007 Valve"])
    got = bindings.load(path)
    assert got.values == {2: "valve"} and got.records == {3: "CB-1007 Valve"}
    assert unbound == ["a (a record)"]  # the second such step has no title: the replay blocks


def test_the_angular_entry_gives_the_values_its_flow_types():
    """The entry's synthetic values are the flow's own (`INPUTS`, `RECORDS`), in the same order."""
    e = certify.load_matrix(MATRIX).entry("angular")
    flow = certify._module(ROOT / e.flow.file, e.app.namespace)
    assert flow.__name__ == "caseboard_angular_flow" and e.app.start == flow.START
    values: dict[str, list[str]] = {}
    for _action, selector, value in flow.INPUTS:
        values.setdefault(selector, []).append(value)
    assert e.gates["self-replay"]["values"] == values
    assert e.gates["self-replay"]["records"] == [title for _action, title in flow.RECORDS]
    jsf = certify.load_matrix(MATRIX).entry("jsf-mojarra")
    jsf_flow = certify._module(ROOT / jsf.flow.file, jsf.app.namespace)
    assert jsf.app.start == jsf_flow.START
    assert callable(getattr(jsf_flow, jsf.gates["self-replay"]["bindings_from"]))


def test_desktop_entries_run_only_when_asked_for(tmp_path, monkeypatch):
    """No desktop application is started here: without the variable, and for an application
    folder that is not there, the entry is NOT RUN with that reason."""
    monkeypatch.delenv(certify.DESKTOP_ENV, raising=False)
    mx = certify.load_matrix(MATRIX)
    for name in ("winforms", "java-swing"):
        got = certify.run_entry(mx.entry(name), ROOT, tmp_path)
        live = {k: g.status for k, g in got.gates.items() if k != "rebuild"}
        assert set(live.values()) == {NOT_RUN}
        assert all(g.detail == "set TWINLAB_DESKTOP_GUI=1" for k, g in got.gates.items()
                   if k != "rebuild")  # fmt: skip
        if name == "winforms":  # its rebuild is a kept report: read without starting anything
            assert got.gates["rebuild"].status == "PASS" and got.result == "PARTIAL"
        else:
            assert got.result == NOT_RUN and got.reason == "set TWINLAB_DESKTOP_GUI=1"
    monkeypatch.setenv(certify.DESKTOP_ENV, "1")  # asked for, but the folder is not there
    empty = tmp_path / "repo"
    empty.mkdir()
    for name in ("winforms", "wpf", "java-swing"):
        got = certify.run_entry(mx.entry(name), empty, tmp_path)
        assert got.result == NOT_RUN and got.reason == "reference app not present"


def test_react_without_its_vendor_files_is_not_run(tmp_path):
    (tmp_path / "apps" / "web-todo" / "static").mkdir(parents=True)
    got = certify.run_entry(certify.load_matrix(MATRIX).entry("react-16"), tmp_path, tmp_path)
    assert got.result == NOT_RUN
    assert got.reason == ("vendor/react.js, vendor/react-dom.js not in apps/web-todo/static "
                          "(built from node_modules by build.cjs, not in Git)")  # fmt: skip


# --- the table -------------------------------------------------------------------------------------


def test_markdown_is_rendered_from_the_kept_results(tmp_path):
    mx = certify.parse_matrix({"entries": [
        entry(id="a", technology="Alpha", version="1.2", not_covered=["uploads", "a | b"]),
        entry(id="b", technology="Beta", not_run="needs a download"),
        entry(id="c", technology="Gamma"),
    ]})  # fmt: skip
    gates = {g: Gate(status=PASS, detail=f"{g} ok") for g in certify.GATES}
    gates["self-replay"] = Gate(status=FAIL, detail="replay fail: 1 failed of 9 step(s)")
    gates["rebuild"] = Gate(detail="no report")
    first = certify._result(mx.entries[0], gates)
    first.date = "2026-11-18"
    certify.write_result(first, tmp_path)
    second = certify.run_entry(mx.entries[1], tmp_path, tmp_path)
    second.date = "2026-11-17"
    certify.write_result(second, tmp_path)
    results = certify.read_results(mx, tmp_path)  # "c" has no result file
    assert [(r.id, r.result) for r in results] == [("a", PARTIAL), ("b", NOT_RUN), ("c", NOT_RUN)]
    text = certify.markdown(results)
    lines = text.splitlines()
    head = lines.index("| Technology | Version | Surface | Detect | Capture | Self-replay | Rebuild "
                       "| Result | Date | Not covered |")  # fmt: skip
    assert lines[head + 2] == ("| Alpha | 1.2 | web | PASS | PASS | FAIL | NOT RUN | **PARTIAL** "
                               "| 2026-11-18 | uploads; a \\| b |")  # fmt: skip
    assert lines[head + 3] == ("| Beta | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN "
                               "| **NOT RUN** | 2026-11-17 | - |")  # fmt: skip
    assert lines[head + 4].startswith("| Gamma | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN |")
    assert "| - | - |" in lines[head + 4]  # never run: no date
    assert "Result: **PARTIAL**. self-replay failed: replay fail: 1 failed of 9 step(s)" in text
    assert "- self-replay: FAIL. replay fail: 1 failed of 9 step(s)" in text
    assert "Result: **NOT RUN**. needs a download" in text
    assert "Result: **NOT RUN**. never run" in text
    assert text.endswith("\n") and "\n\n\n" not in text


def test_the_command_writes_results_and_the_table(tmp_path):
    from typer.testing import CliRunner

    from labctl.cli import app

    args = ["certify", "--only", "aspnet-webforms", "--only", "vue", "--out", str(tmp_path / "res"),
            "--doc", str(tmp_path / "matrix.md"), "--matrix", str(MATRIX)]  # fmt: skip
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 0, r.output
    assert f"aspnet-webforms: NOT RUN ({NO_APP})" in r.output
    assert sorted(p.name for p in (tmp_path / "res").iterdir()) == [
        "aspnet-webforms.json", "vue.json"]  # fmt: skip
    kept = json.loads((tmp_path / "res" / "vue.json").read_text("utf-8"))
    assert kept["result"] == NOT_RUN and kept["gates"]["detect"]["status"] == NOT_RUN
    table = (tmp_path / "matrix.md").read_text("utf-8")
    assert "| Vue | - | web | NOT RUN |" in table and "| React | 16.7.0 | web | NOT RUN |" in table
    r = CliRunner().invoke(app, [*args[:1], "--only", "nope", *args[5:]])
    assert r.exit_code == 2 and "no entry 'nope'" in r.output


def test_the_committed_table_matches_the_committed_results():
    """docs/certification-matrix.md is what `labctl certify` writes from certification/results."""
    results = certify.read_results(certify.load_matrix(MATRIX), ROOT / certify.RESULTS)
    doc = (ROOT / certify.DOC).read_text("utf-8")
    assert doc == certify.markdown(results)
    for r in results:  # a kept result agrees with its own gates
        assert r.result == certify.decide(r.gates)[0], r.id
        assert r.result != CERTIFIED or all(g.status == PASS for g in r.gates.values())


# --- with a real browser ---------------------------------------------------------------------------

SHORT = {
    "id": "html-forms-short",
    "technology": "Server-rendered HTML forms",
    "surface": "web",
    "app": {"kind": "python", "file": "lab/labctl/tests/fixtures/multiapp/apps.py",
            "origins": ["orders", "billing", "widget"], "start": "{orders}/login"},
    "flow": {"file": "lab/labctl/tests/fixtures/multiapp/flow.py", "run": "run_second"},
    "gates": {
        "detect": {"framework": "server-rendered", "rendering": "server"},
        # written from flow.run_second: sign in, the customer list, a new order for a chair
        "capture": {"expected": ["type", "type", "click", "click", "click", "type", "type",
                                 "click", "select", "type", "type", "click"]},
        "self-replay": {"values": {
            "#username": ["clerk.syn"], "#password": ["Syn-pass-1"],
            "#customerName": ["Syn Customer"], "#customerEmail": ["syn@example.test"],
            "#product": ["Office chair"], "#quantity": ["1"], "#deliveryDate": ["2027-01-15"]}},
    },
}  # fmt: skip


def test_the_server_rendered_entry_end_to_end(tmp_path):
    """The same application and gates as the `html-forms` entry, on the fixture's short flow:
    recorded with the real recorder, detected, compared with the expected actions, replayed
    against a fresh instance. Then the same recording fails detect and capture for an entry that
    declares another technology and actions the flow does not have."""
    pytest.importorskip("playwright.sync_api")
    matrix = tmp_path / "matrix.yaml"
    wrong = {**SHORT, "id": "wrong", "not_run": None,
             "gates": {**SHORT["gates"], "detect": {"framework": "react", "version": "16.7.0"},
                       "capture": {"expected": ["type", "dblclick", "click"]}}}  # fmt: skip
    matrix.write_text(yaml.safe_dump({"entries": [SHORT, wrong]}), "utf-8")
    out, doc = tmp_path / "results", tmp_path / "matrix.md"
    (good,) = certify.run(
        ROOT, only=["html-forms-short"], out=out, doc=doc, matrix=matrix, keep=tmp_path / "kept"
    )
    g = good.gates
    assert good.result == CAPTURE_VERIFIED, good.model_dump_json(indent=1)
    assert g["detect"].facts["detected"] == {"version": None, "framework": "server-rendered",
                                             "rendering": "server"}  # fmt: skip
    assert g["capture"].facts["expected"] == 12 and g["capture"].facts["missing"] == []
    replayed = g["self-replay"].facts
    assert replayed["failed"] == 0 and replayed["replayed"] == replayed["steps"] > 12
    assert g["rebuild"].status == NOT_RUN
    kept = json.loads((out / "html-forms-short.json").read_text("utf-8"))
    assert kept["result"] == CAPTURE_VERIFIED
    text = json.dumps(kept)
    for typed in ("clerk.marsh", "Tide-Pool-77", "Second Flow Person", "Syn-pass-1"):
        assert typed not in text  # no typed value in a result
    table = doc.read_text("utf-8")
    assert "| PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** |" in table
    assert (
        "| NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | - |" in table
    )  # "wrong": never run

    # the same recording, judged as an entry declaring another technology and another flow
    from labctl.behaviour import fidelity
    from labctl.botlog.recording import load

    rec = tmp_path / "kept" / "html-forms-short"
    mx = certify.load_matrix(matrix)
    declared = mx.entry("wrong").gates
    found = certify.detect_web("wrong", rec, tmp_path)
    detect = certify.detect_gate(declared["detect"], found)
    assert detect.status == FAIL and "declared react, detected server-rendered" in detect.detail
    capture = certify.capture_gate(declared["capture"]["expected"],
                                   fidelity.captured_actions(load(rec)))  # fmt: skip
    assert capture.status == FAIL and capture.facts["missing"] == ["dblclick"]
    assert certify.decide({"detect": detect, "capture": capture})[0] == PARTIAL


def test_the_react_entry_where_its_vendor_files_are_built(tmp_path):
    """Local only: apps/web-todo/static/vendor is built from node_modules and is not in Git."""
    pytest.importorskip("playwright.sync_api")
    entry_ = certify.load_matrix(MATRIX).entry("react-16")
    if certify.missing_requirements(entry_.app, ROOT):
        pytest.skip("apps/web-todo/static/vendor is not built on this machine")
    got = certify.run_entry(entry_, ROOT, tmp_path)
    assert got.result == CAPTURE_VERIFIED, got.model_dump_json(indent=1)
    assert got.gates["detect"].facts["detected"]["version"] == "16.7.0"
    assert got.gates["capture"].facts["expected"] == 20
    assert got.gates["self-replay"].facts["failed"] == 0


def test_the_angular_entry_end_to_end(tmp_path):
    """The `angular` entry as the matrix declares it, on the committed build: started by the
    application's own serve.py (a route's address answers with the shell), recorded, detected,
    and replayed against a fresh instance with the entry's values and the record's title."""
    pytest.importorskip("playwright.sync_api")
    entry_ = certify.load_matrix(MATRIX).entry("angular")
    assert certify.missing_requirements(entry_.app, ROOT) is None  # site/ is in Git
    got = certify.run_entry(entry_, ROOT, tmp_path)
    assert got.result == CAPTURE_VERIFIED, got.model_dump_json(indent=1)
    assert got.gates["detect"].facts["detected"] == {
        "version": "21.2.26", "framework": "angular", "rendering": "client"}  # fmt: skip
    assert got.gates["capture"].facts["expected"] == 34
    replayed = got.gates["self-replay"].facts
    assert replayed["failed"] == 0 and replayed["replayed"] == replayed["steps"] >= 34
    bound = yaml.safe_load((tmp_path / "angular" / "bindings.yaml").read_text("utf-8"))["steps"]
    assert [b["record"] for b in bound.values() if "record" in b] == entry_.gates["self-replay"][
        "records"]  # fmt: skip
    text = got.model_dump_json()
    assert "not-the-password" not in text and "Position sensor" not in text
