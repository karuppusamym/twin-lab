"""A recording whose recorder crashed, timed out or was restarted: what could be read is used and
the rest is said in a message, never a traceback and never a result that looks complete.

Each case is checked at the three places a recording is read: the replay's loader
(`labctl.botlog.recording.load`), the split (`labctl.split`) and the brief's loader
(`labctl.har.load`). All data is synthetic."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from labctl import har
from labctl import split as sp
from labctl.behaviour import fidelity
from labctl.botlog import recording as rec_mod
from labctl.evidence_cli import app as cli_app

T0 = datetime(2026, 11, 18, 9, 0, 0, tzinfo=timezone.utc)
A = "https://a.example.com"
B = "https://b.example.org"


def _t(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _step(seq: int, url: str, action: str = "goto", selector: str | None = None, **extra) -> dict:
    return {"seq": seq, "ts": _t(seq * 10), "url": url, "action": action, "selector": selector,
            "dom": f"dom/{seq:04d}.html", "dom_before": f"dom/{seq:04d}.before.html",
            "screenshot": f"screens/{seq:04d}.png", **extra}  # fmt: skip


def _entry(url: str, at: float) -> dict:
    return {"pageref": "page_1", "startedDateTime": _t(at), "time": 5,
            "request": {"method": "GET", "url": url,
                        "headers": [{"name": "Sec-Fetch-Dest", "value": "document"}]},
            "response": {"status": 200, "content": {"mimeType": "text/html", "text": "<html/>"}}}  # fmt: skip


STEPS = [
    _step(1, f"{A}/home"),
    _step(2, f"{A}/cases", "click", "a#cases", caused_by=1),
    _step(3, f"{B}/dash"),
    _step(4, f"{B}/dash/items", "click", "a#items", caused_by=3),
]


def write_files(rec: Path, steps: list[dict]) -> None:
    """The snapshots and pictures the steps name."""
    (rec / "dom").mkdir(parents=True, exist_ok=True)
    (rec / "screens").mkdir(exist_ok=True)
    for s in steps:
        page = f'<html><body><h1 id="h">step {s["seq"]}</h1><a id="cases">x</a></body></html>'
        (rec / s["dom"]).write_text(page, encoding="utf-8")
        (rec / s["dom_before"]).write_text(page, encoding="utf-8")
        (rec / s["screenshot"]).write_bytes(b"\x89PNG-" + str(s["seq"]).encode())


def write_har(rec: Path, steps: list[dict]) -> None:
    entries = [_entry(s["url"], s["seq"] * 10 - 0.5) for s in steps if s["action"] == "goto"]
    log = {"version": "1.2", "creator": {"name": "t", "version": "1"},
           "pages": [{"id": "page_1", "title": "x", "startedDateTime": _t(0)}],
           "entries": entries}  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": log}), encoding="utf-8")


def make(tmp_path: Path, steps: list[dict] | None = None, lines: list[str] | None = None) -> Path:
    """A whole recording of `steps`; `lines` replaces the text of steps.jsonl (to damage it)."""
    rec = tmp_path / "in" / "rec-1"
    steps = STEPS if steps is None else steps
    write_files(rec, steps)
    write_har(rec, steps)
    text = lines if lines is not None else [json.dumps(s) for s in steps]
    (rec / "steps.jsonl").write_text("".join(x + "\n" for x in text), encoding="utf-8")
    return rec


def has(problems: list[str], *words: str) -> bool:
    return any(all(w in p for w in words) for p in problems)


def part_steps(folder: Path) -> list[dict]:
    return [json.loads(x) for x in (folder / "steps.jsonl").read_text("utf-8").splitlines()]


# --- a whole recording: nothing is reported -----------------------------------------------------------


def test_whole_recording_has_no_problems(tmp_path):
    rec = make(tmp_path)
    loaded = rec_mod.load(rec)
    assert [s.seq for s in loaded.steps] == [1, 2, 3, 4]
    assert loaded.problems == [] and loaded.bad_lines == 0
    assert sp.propose(rec).problems == [] and sp.problems(rec) == []
    assert har.load(rec).problems == []
    assert fidelity.recording_damage(loaded) == []
    paths = sp.apply(rec, sp.propose(rec), tmp_path / "root")
    assert "problems" not in yaml.safe_load(sp.sequence_path(rec, tmp_path / "root").read_text())
    assert all(rec_mod.load(p).problems == [] for p in paths)


# --- 1. the last line is cut off (the recorder was killed while writing) ------------------------------


def cut(tmp_path: Path) -> Path:
    rec = make(tmp_path)
    whole = [json.dumps(s) for s in STEPS]
    text = "\n".join(whole[:3]) + "\n" + whole[3][: len(whole[3]) // 2]  # no closing brace, no \n
    (rec / "steps.jsonl").write_text(text, encoding="utf-8")
    return rec


def test_cut_last_line_is_dropped_and_said(tmp_path):
    rec = cut(tmp_path)
    loaded = rec_mod.load(rec)
    assert [s.seq for s in loaded.steps] == [1, 2, 3]
    assert loaded.bad_lines == 1
    assert has(loaded.problems, "steps.jsonl line 4 (the last) is cut off", "dropped")
    # the replay's report carries it: three steps replayed is not the whole recording
    assert has(fidelity.recording_damage(loaded), "recording: steps.jsonl line 4", "cut off")


def test_cut_last_line_in_split_and_brief_loader(tmp_path):
    rec = cut(tmp_path)
    prop = sp.propose(rec)
    assert sum(e.steps for e in prop.apps) == 3
    assert has(prop.problems, "line 4 (the last) is cut off")
    paths = sp.apply(rec, prop, tmp_path / "root")
    assert sum(len(part_steps(p)) for p in paths) == 3
    seq = yaml.safe_load(sp.sequence_path(rec, tmp_path / "root").read_text("utf-8"))
    assert has(seq["problems"], "line 4 (the last) is cut off")
    assert seq["counts"]["total"] == 3
    brief = har.load(rec)
    assert [s.seq for s in brief.steps] == [1, 2, 3]
    assert has(brief.problems, "steps.jsonl line 4")


def test_cut_in_the_middle_of_a_character(tmp_path):
    """The file ends inside a multi-byte character: still only the last line is lost."""
    rec = make(tmp_path)
    whole = "\n".join(json.dumps(s) for s in STEPS[:3]).encode()
    tail = b'\n{"seq": 4, "action": "type", "value": "caf\xc3'  # half of an e-acute
    (rec / "steps.jsonl").write_bytes(whole + tail)
    loaded = rec_mod.load(rec)
    assert [s.seq for s in loaded.steps] == [1, 2, 3]
    assert has(loaded.problems, "line 4 (the last) is cut off")
    assert has(sp.propose(rec).problems, "line 4 (the last) is cut off")
    assert [s.seq for s in har.load(rec).steps] == [1, 2, 3]


# --- 2. a garbage or empty line in the middle ---------------------------------------------------------


@pytest.mark.parametrize("junk", ["\x00\x00\x00\x00", "not json at all", "42", "null", "[1, 2]",
                                  '{"seq": "three", "action": "click"}'])  # fmt: skip
def test_garbage_line_in_the_middle_is_skipped_and_said(tmp_path, junk):
    whole = [json.dumps(s) for s in STEPS]
    rec = make(tmp_path, lines=[whole[0], junk, "", "   ", *whole[1:]])
    loaded = rec_mod.load(rec)
    assert [s.seq for s in loaded.steps] == [1, 2, 3, 4]
    assert loaded.bad_lines == 1
    assert loaded.problems == ["steps.jsonl line 2 is not a step; skipped"]  # blank lines: nothing
    prop = sp.propose(rec)
    assert sum(e.steps for e in prop.apps) == 4
    assert prop.problems == ["steps.jsonl line 2 is not a step; skipped"]
    brief = har.load(rec)
    assert [s.seq for s in brief.steps] == [1, 2, 3, 4]
    assert brief.problems == ["steps.jsonl line 2 is not a step; skipped"]


# --- 3. a step names a snapshot or picture that was never written -------------------------------------


def lost_snapshots(tmp_path: Path) -> Path:
    rec = make(tmp_path)
    (rec / "dom" / "0002.html").unlink()
    (rec / "screens" / "0002.png").unlink()
    return rec


def test_missing_snapshot_loads_and_names_the_step(tmp_path):
    rec = lost_snapshots(tmp_path)
    loaded = rec_mod.load(rec)
    assert [s.seq for s in loaded.steps] == [1, 2, 3, 4]
    step = loaded.steps[1]
    assert loaded.dom(step) is None  # nothing to compare with
    assert loaded.dom(step, before=True) is not None  # the before-snapshot was written
    assert has(loaded.problems, "step 2:", "dom/0002.html not found")
    assert has(loaded.problems, "step 2:", "screens/0002.png not found")
    assert len(loaded.problems) == 2
    assert has(har.load(rec).problems, "snapshot dom/0002.html not found")


def test_missing_snapshot_in_split(tmp_path):
    rec = lost_snapshots(tmp_path)
    prop = sp.propose(rec)
    assert [(e.origin, e.steps) for e in prop.apps] == [(A, 2), (B, 2)]
    assert has(prop.problems, "step 2:", "dom/0002.html not found")
    assert has(prop.problems, "step 2:", "screens/0002.png not found")
    paths = sp.apply(rec, prop, tmp_path / "root")
    first = part_steps(paths[0])
    assert first[1]["global_seq"] == 2
    assert first[1]["dom"] is None and first[1]["screenshot"] is None  # not a dangling name
    assert first[1]["dom_before"] == "dom/0002.before.html"
    assert first[0]["dom"] == "dom/0001.html" and first[0]["screenshot"] == "screens/0001.png"
    seq = yaml.safe_load(sp.sequence_path(rec, tmp_path / "root").read_text("utf-8"))
    assert has(seq["problems"], "step 2:", "dom/0002.html not found")
    assert rec_mod.load(paths[0]).problems == []  # the part itself is consistent


def test_step_without_its_snapshot_is_not_compared_and_is_a_problem(tmp_path):
    loaded = rec_mod.load(lost_snapshots(tmp_path))
    step = loaded.steps[1]
    note, problem = fidelity.uncompared(step)
    assert "not compared" in note and "dom/0002.html" in note
    assert problem == (
        "step 2 (click) ran but was not compared: its snapshot dom/0002.html is not in the "
        "recording"
    )
    # a step that names no snapshot at all (a recorder that writes none there) is no problem
    step.dom = None
    assert loaded.dom(step) is None
    assert fidelity.uncompared(step) == (
        "ran; no snapshot was recorded after this step to compare",
        None,
    )


def test_uncompared_step_is_counted_as_not_compared_in_the_report(tmp_path):
    """What the replay loop reports for the step (its outcome without a browser): it ran, it is
    not in `compared`, and the report says why."""
    loaded = rec_mod.load(lost_snapshots(tmp_path))
    reports = []
    problems = fidelity.recording_damage(loaded)
    for step in loaded.steps:
        r = fidelity.StepReport(seq=step.seq, action=step.action, status="pass", outcome="replayed")
        if loaded.dom(step) is None:
            r.note, lost = fidelity.uncompared(step)
            problems += [lost] if lost else []
        else:
            r.compared = True
        reports.append(r)
    report = fidelity._result(loaded, None, reports, problems, None)
    assert (report.coverage.replayed, report.coverage.compared) == (4, 3)
    assert [r.compared for r in report.steps] == [True, False, True, True]
    assert has(report.problems, "step 2 (click) ran but was not compared")
    assert "step 2 (click) ran but was not compared" in report.markdown()


# --- 4. recording.har missing, empty or cut off -------------------------------------------------------

HAR_DAMAGE = {
    "empty": "",
    "cut": '{"log": {"version": "1.2", "entries": [{"request": {"method": "GET", "url": "https://a.ex',
    "not-a-har": "[]",
    "no-entries": '{"log": {"version": "1.2"}}',
}


@pytest.mark.parametrize("kind", [*HAR_DAMAGE, "missing"])
def test_damaged_har_loads_the_steps_and_says_no_requests(tmp_path, kind):
    rec = make(tmp_path)
    if kind == "missing":
        (rec / "recording.har").unlink()
    else:
        (rec / "recording.har").write_text(HAR_DAMAGE[kind], encoding="utf-8")
    loaded = rec_mod.load(rec)
    assert [s.seq for s in loaded.steps] == [1, 2, 3, 4]
    assert loaded.entries == []
    assert len(loaded.problems) == 1
    assert has(loaded.problems, "recording.har", "no requests were read")
    assert fidelity.recording_damage(loaded) == []  # the replay does not use the HAR
    assert rec_mod.read_har(rec / "recording.har") == []  # as before, without a list to fill


@pytest.mark.parametrize("kind", [*HAR_DAMAGE, "missing"])
def test_damaged_har_refuses_the_split_and_the_brief_with_the_reason(tmp_path, kind):
    rec = make(tmp_path)
    if kind == "missing":
        (rec / "recording.har").unlink()
    else:
        (rec / "recording.har").write_text(HAR_DAMAGE[kind], encoding="utf-8")
    with pytest.raises(sp.ProposalError, match="recording.har") as exc:
        sp.propose(rec)
    assert "rec-1" in str(exc.value)
    assert "Traceback" not in str(exc.value)
    whole = make(tmp_path / "whole")
    with pytest.raises(sp.ProposalError, match="recording.har"):
        sp.apply(rec, sp.propose(whole), tmp_path / "root")
    assert not (tmp_path / "root" / "evidence").exists()  # nothing half-written
    if kind == "no-entries":  # a HAR log without requests: the brief's loader reads it as none
        assert len(har.load(rec).steps) == 4
        return
    with pytest.raises((FileNotFoundError, ValueError), match=r"recording\.har"):
        har.load(rec)


def test_har_entries_that_are_not_objects_are_left_out(tmp_path):
    rec = make(tmp_path)
    doc = json.loads((rec / "recording.har").read_text("utf-8"))
    doc["log"]["entries"] += [None, "x", 7]
    (rec / "recording.har").write_text(json.dumps(doc), encoding="utf-8")
    assert len(rec_mod.load(rec).entries) == 2
    assert [e.steps for e in sp.propose(rec).apps] == [2, 2]


# --- 5. step numbers repeated or out of order (the recorder was restarted and appended) ---------------


def restarted(tmp_path: Path) -> Path:
    """Four steps, then the recorder starts again at 1 and appends two more."""
    again = [_step(1, f"{A}/home"), _step(2, f"{A}/cases", "click", "a#again")]
    rec = make(tmp_path)
    lines = [json.dumps(s) for s in [*STEPS, *again]]
    (rec / "steps.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rec


def test_repeated_step_numbers_keep_the_written_order_and_are_said(tmp_path):
    loaded = rec_mod.load(restarted(tmp_path))
    # not 1, 1, 2, 2, 3, 4: sorting would interleave the two runs
    assert [s.seq for s in loaded.steps] == [1, 2, 3, 4, 1, 2]
    assert [s.selector for s in loaded.steps][-1] == "a#again"
    assert has(loaded.problems, "step number(s) 1, 2 used more than once", "restarted")
    assert has(fidelity.recording_damage(loaded), "recording: steps.jsonl", "more than once")


def test_repeated_step_numbers_are_proposed_but_not_split(tmp_path):
    rec = restarted(tmp_path)
    prop = sp.propose(rec)
    assert [(e.origin, e.steps) for e in prop.apps] == [(A, 4), (B, 2)]
    assert has(prop.problems, "step number(s) 1, 2 used more than once")
    with pytest.raises(sp.ProposalError, match=r"step number\(s\) 1, 2 are used more than once"):
        sp.apply(rec, prop, tmp_path / "root")
    assert not (tmp_path / "root" / "evidence").exists()


def test_out_of_order_step_numbers_are_read_by_number_and_said(tmp_path):
    whole = [json.dumps(s) for s in STEPS]
    rec = make(tmp_path, lines=[whole[0], whole[2], whole[1], whole[3]])
    loaded = rec_mod.load(rec)
    assert [s.seq for s in loaded.steps] == [1, 2, 3, 4]  # the loader reads them by number
    assert loaded.problems == [
        "steps.jsonl: step numbers are not in rising order; check the order of the steps"
    ]
    prop = sp.propose(rec)
    assert has(prop.problems, "not in rising order")
    paths = sp.apply(rec, prop, tmp_path / "root")
    first, second = part_steps(paths[0]), part_steps(paths[1])
    assert [s["global_seq"] for s in first] == [1, 2] and [s["global_seq"] for s in second] == [
        3,
        4,
    ]
    assert first[1]["caused_by"] == 1 and second[1]["caused_by"] == 1  # still the right steps


# --- 6. an empty recording ----------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "\n\n", None])
def test_empty_recording(tmp_path, text):
    rec = make(tmp_path, steps=[])
    if text is None:
        (rec / "steps.jsonl").unlink()
    else:
        (rec / "steps.jsonl").write_text(text, encoding="utf-8")
    loaded = rec_mod.load(rec)
    assert loaded.steps == [] and loaded.bad_lines == 0
    assert has(loaded.problems, "steps.jsonl", "no step")
    with pytest.raises(sp.ProposalError, match="rec-1: (steps.jsonl has no step|no steps.jsonl)"):
        sp.propose(rec)
    assert har.load(rec).steps == []
    # a replay of nothing never passes
    report = fidelity._result(loaded, None, [], fidelity.recording_damage(loaded), None)
    assert report.status == "blocked"
    assert has(report.problems, "no step was replayed")


def test_recording_of_one_cut_line_says_what_was_lost(tmp_path):
    rec = make(tmp_path, steps=[], lines=['{"seq": 1, "action": "go'])
    loaded = rec_mod.load(rec)
    assert loaded.steps == []
    assert has(loaded.problems, "line 1 (the last) is cut off")
    assert has(loaded.problems, "steps.jsonl has no step")
    with pytest.raises(sp.ProposalError) as exc:
        sp.propose(rec)
    assert "has no step" in str(exc.value) and "line 1 (the last) is cut off" in str(exc.value)


# --- the command says it ------------------------------------------------------------------------------


def test_split_command_reports_the_damage(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from labctl import repo

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(repo, "find_root", lambda: root)
    rec = cut(tmp_path)
    (rec / "dom" / "0002.html").unlink()
    runner = CliRunner()
    prop = tmp_path / "p.yaml"
    r = runner.invoke(cli_app, ["split", str(rec), "--proposal", str(prop)])
    assert r.exit_code == 0, r.output
    assert "recording: steps.jsonl line 4 (the last) is cut off" in r.output
    assert "recording: step 2: dom/0002.html not found" in r.output
    r = runner.invoke(cli_app, ["split", str(rec), "--proposal", str(prop), "--apply"])
    assert r.exit_code == 0, r.output
    assert "recording: steps.jsonl line 4 (the last) is cut off" in r.output
    (rec / "recording.har").write_text("", encoding="utf-8")
    r = runner.invoke(cli_app, ["split", str(rec), "--proposal", str(prop), "--apply", "--force"])
    assert r.exit_code == 1
    assert "recording.har is not a complete HAR file" in r.output and "Traceback" not in r.output
