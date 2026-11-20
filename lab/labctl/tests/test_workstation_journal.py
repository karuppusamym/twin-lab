"""The capture journal of a workstation session: permanent event ids, the loss window, recovery
after the recorder is killed mid-capture (each channel, the web HAR included), and the
completeness manifest of recorder contract 1.1.0."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import labctl
from labctl.workstation import completeness, recovery, timeline
from labctl.workstation import journal as capture
from labctl.workstation.session import recover, recover_all

ORIGIN = "http://127.0.0.1:8080"
DESK = "desktop:Twin Lab reference/CaseDesk"
ROOT = Path(__file__).resolve().parents[3]
UUID7 = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def _env() -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(labctl.__file__).resolve().parents[1]), env.get("PYTHONPATH", "")]
    )
    return env


def _meta(folder: Path, channels: list[str], state: str = "recording", **extra) -> dict:
    targets = []
    if "web" in channels:
        targets.append({"origin": ORIGIN, "level": "full"})
    if "desktop" in channels:
        targets.append({"exe": "casedesk.exe", "level": "full"})
    if "mainframe" in channels:
        targets.append({"tn3270": "127.0.0.1:2323", "key": "mainframe:127.0.0.1:2323"})
    meta = {"format": timeline.FORMAT, "id": folder.name, "name": "t", "state": state,
            "started": "2026-11-20T09:00:00.000Z", "ended": None, "targets": targets,
            "channels": channels, "apps": {}, "notes": [], **extra}  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    rows = [{"ts": "2026-11-20T09:00:00.100Z", "kind": "app", "pid": 11, "key": DESK,
             "surface": "desktop", "exe": "CaseDesk.exe", "level": "full", "display": "CaseDesk"}]  # fmt: skip
    (folder / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    return meta


# --- ids and the loss window -------------------------------------------------------------------


def test_uuid7_ids_are_version_7_and_sort_in_capture_order():
    import re

    ids = [capture.uuid7() for _ in range(200)]
    assert all(re.match(UUID7, i) for i in ids)
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    step = capture.stamp({"action": "click"}, "web")
    assert step["channel"] == "web" and step["captured_at"].endswith("Z")
    assert capture.stamp(dict(step), "web")["event_id"] == step["event_id"]  # never replaced


def test_every_line_is_fsynced_within_the_loss_window(tmp_path):
    synced: list[float] = []
    j = capture.Journal(
        tmp_path / "web.jsonl", "web", fsync=lambda fd: synced.append(time.monotonic())
    )
    try:
        for _ in range(3):
            written = time.monotonic()
            j.event({"action": "click", "seq": 1})
            end = written + capture.LOSS_WINDOW_S + 1
            while time.monotonic() < end and not any(t >= written for t in synced):
                time.sleep(0.02)
            durable = min(t for t in synced if t >= written)
            assert durable - written <= capture.LOSS_WINDOW_S
            assert durable - written <= capture.FSYNC_INTERVAL_S + 0.5
    finally:
        j.close()
    assert capture.FSYNC_INTERVAL_S < capture.LOSS_WINDOW_S
    # idle: the journal still says the collector was alive (ticks), at least once a TICK_S
    j = capture.Journal(tmp_path / "idle.jsonl", "web", fsync_interval=0.05)
    time.sleep(capture.TICK_S * 2.5)
    j.close()
    ticks = [r for r in capture.read(tmp_path / "idle.jsonl") if r["type"] == "tick"]
    assert len(ticks) >= 2


def test_a_torn_line_is_skipped_when_reading_a_journal(tmp_path):
    path = tmp_path / "j.jsonl"
    path.write_text('{"type": "open"}\n{"type": "ev\n{"type": "close", "clean": true}\n', "utf-8")
    assert [r["type"] for r in capture.read(path)] == ["open", "close"]


# --- event ids: stable across finalise, recovery and reorder ----------------------------------


def _web_recording(out: Path, j: capture.Journal | None):
    from labctl.recorder import _Steps

    (out / "dom").mkdir(parents=True, exist_ok=True)
    steps = _Steps(out, screenshots=False, origins={ORIGIN}, journal=j)
    url = ORIGIN + "/cases"
    for eid, action, sel in (("e1", "click", "a#c1"), ("e2", "click", "a#c1")):
        steps.on_event({}, {"action": action, "url": url, "selector": sel, "eid": eid,
                            "at": "2026-11-20T09:00:01.000Z"})  # fmt: skip
    steps.on_event({}, {"action": "dblclick", "url": url, "selector": "a#c1", "eid": "e3",
                        "replaces": ["e1", "e2"], "at": "2026-11-20T09:00:01.300Z"})  # fmt: skip
    steps.on_event({}, {"action": "type", "url": url, "selector": "input#q", "eid": "e4",
                        "at": "2026-11-20T09:00:02.000Z"})  # fmt: skip
    steps.on_event({}, {"action": "keypress", "url": url, "selector": "input#q", "eid": "e5",
                        "key": "Enter", "at": "2026-11-20T09:00:02.500Z"})  # fmt: skip
    steps.on_event({}, {"action": "goto", "url": ORIGIN + "/cases/1", "nav": "navigate",
                        "eid": "e6", "at": "2026-11-20T09:00:03.000Z"})  # fmt: skip
    return steps


def test_event_ids_survive_finalise_recovery_and_reorder(tmp_path):
    folder = tmp_path / "s"
    _meta(folder, ["web", "network"], state="stopped")
    j = capture.Journal(capture.path_of(folder, "web"), "web")
    j.open()
    steps = _web_recording(folder / "web", j)
    ids = {eid: steps.eid_event[eid] for eid in ("e1", "e2", "e3", "e4", "e5", "e6")}
    steps._close_open()
    steps.drain(pictures=False)
    steps.close()  # finalise: the double click takes the clicks' place, all is renumbered
    j.close()
    final = timeline.read_jsonl(folder / "web" / "steps.jsonl")
    assert [s["seq"] for s in final] == [1, 2, 3, 4]
    assert [s["event_id"] for s in final] == [ids["e3"], ids["e4"], ids["e5"], ids["e6"]]
    assert all(s["channel"] == "web" and s["captured_at"] for s in final)
    nav = final[-1]
    assert nav["caused_by"] == 3 and nav["caused_by_event"] == ids["e5"]  # by id, not by number
    # the journal alone gives the same events (the clicks the double click replaced dropped)
    rebuilt = recovery.steps_of(capture.read(capture.path_of(folder, "web")), "web")["web"]
    assert [s["event_id"] for s in rebuilt] == [s["event_id"] for s in final]
    # the timeline numbers again when another channel's step comes before: ids stay
    box1 = timeline.build(folder)
    rows1 = {
        r["event_id"]: r for r in timeline.read_jsonl(folder / "session.jsonl") if "event_id" in r
    }
    (folder / "desktop").mkdir()
    (folder / "desktop" / "steps.jsonl").write_text(json.dumps(
        {"seq": 1, "ts": "2026-11-20T09:00:00.500Z", "surface": "desktop", "action": "click",
         "pid": 11, "event_id": capture.uuid7(), "channel": "desktop",
         "captured_at": "2026-11-20T09:00:00.500Z"}) + "\n", "utf-8")  # fmt: skip
    box2 = timeline.build(folder)
    rows2 = {
        r["event_id"]: r for r in timeline.read_jsonl(folder / "session.jsonl") if "event_id" in r
    }
    for eid, row in rows1.items():
        assert rows2[eid]["action"] == row["action"] and rows2[eid]["seq"] == row["seq"]
        assert rows2[eid]["global_seq"] != row["global_seq"]  # display order only
    web = f"web:{ORIGIN}"
    assert [s["event_id"] for s in box1["flows"][web]] == [
        s["event_id"] for s in box2["flows"][web]
    ]
    assert box2["contract_version"] == "1.1.0"


# --- kill mid-capture, per channel --------------------------------------------------------------

CHILD = r"""
import sys, time
from pathlib import Path
from types import SimpleNamespace
from labctl.workstation import journal as capture

channel, folder = sys.argv[1], Path(sys.argv[2])
ORIGIN = "http://127.0.0.1:8080"
j = capture.Journal(capture.path_of(folder, channel), channel)
j.open()
if channel == "web":
    from labctl.recorder import _Har, _Steps

    n = capture.Journal(capture.path_of(folder, "network"), "network")
    n.open()
    out = folder / "web"
    (out / "dom").mkdir(parents=True)
    steps = _Steps(out, screenshots=False, origins={ORIGIN}, journal=j)
    har = _Har({ORIGIN}, journal=n)
    har.steps = steps
    steps.on_secret({}, {"value": "Hunter-42", "tid": "t1"})
    url = ORIGIN + "/cases"
    steps.on_event({}, {"action": "click", "url": url, "selector": "button#open", "eid": "e1",
                        "at": "2026-11-20T09:00:01.000Z"})
    steps.on_event({}, {"action": "type", "url": url, "selector": "input#q", "eid": "e2",
                        "tid": "t1", "at": "2026-11-20T09:00:02.000Z"})
    steps._close_open()
    steps.drain(pictures=False)

    def request(path, body):
        response = SimpleNamespace(status=200, status_text="OK",
                                   headers={"content-type": "application/json"},
                                   body=lambda: body)
        return SimpleNamespace(frame=SimpleNamespace(page=SimpleNamespace(url=url)),
                               url=ORIGIN + path, method="POST",
                               headers={"content-type": "application/json", "cookie": "sid=s3cr3t"},
                               post_data='{"q": "Hunter-42"}', resource_type="fetch",
                               timing={"startTime": time.time() * 1000, "responseEnd": 12},
                               response=lambda: response)

    har.on_finished(request("/api/search", b'{"hits": 1}'))
    har.on_finished(request("/api/cases/1", b'{"id": 1}'))
    # captured, not settled yet: the page is still busy when the process dies
    steps.on_event({}, {"action": "click", "url": ORIGIN + "/cases/1", "selector": "a#save",
                        "eid": "e3", "at": "2026-11-20T09:00:03.000Z"})
elif channel == "desktop":
    from labctl.desktop.recorder import Recorder

    rec = Recorder.__new__(Recorder)  # its writing half: no Windows needed
    rec.out = folder / "desktop"
    rec.out.mkdir(parents=True)
    rec.file = (rec.out / "steps.jsonl").open("w", encoding="utf-8")
    rec.pending, rec._marked, rec.last_activity, rec.journal = [], set(), 0.0, j
    a = {"kind": "click", "t": time.time(), "desc": {}}
    rec._add(a)
    rec._add({"kind": "key", "key": "Enter", "t": time.time()})
    rec.pending.remove(a)
    rec._emit({"seq": 1, "ts": capture.now_iso(), "surface": "desktop", "action": "click",
               "process": "CaseDesk.exe", "pid": 11,
               "window": {"selector": "uia:AutomationId=MainForm;ControlType=Window"},
               "target": {"selector": "uia:AutomationId=btnSave;ControlType=Button"},
               "event_id": a["event_id"], "channel": "desktop", "captured_at": a["captured_at"]})
else:
    from labctl.workstation import tn3270

    rec = tn3270.Recorder(folder / "mainframe", "mainframe:127.0.0.1:2323", journal=j)
    screen = {"id": "scr:1", "title": "SIGN ON", "rows": 24, "cols": 80}
    rec._write({"action": "screen", "connection": 1, "screen": screen}, {"fields": []})
    rec._write({"action": "aid", "connection": 1, "key": "ENTER", "screen": screen})
print("ready", flush=True)
time.sleep(120)
"""


def _kill_mid_capture(tmp_path: Path, channel: str) -> Path:
    folder = tmp_path / "rec" / "20261120T090000Z-0000abcd"
    _meta(folder, [channel, "network"] if channel == "web" else [channel])
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), channel, str(folder)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(), text=True)  # fmt: skip
    try:
        line = proc.stdout.readline()
        assert line.strip() == "ready", proc.stderr.read()
    finally:
        proc.kill()  # abrupt: no finally block, no close, no HAR written by the recorder
        proc.wait(30)
    return folder


@pytest.mark.parametrize("channel", ["web", "desktop", "mainframe"])
def test_a_recording_killed_mid_capture_is_rebuilt_from_its_journal(tmp_path, channel):
    folder = _kill_mid_capture(tmp_path, channel)
    journaled = [r for r in capture.read(capture.path_of(folder, channel)) if r["type"] == "event"]
    assert journaled and not any(
        r["type"] == "close" for r in capture.read(capture.path_of(folder, channel))
    )
    assert recover_all(folder.parent) == [folder.name]
    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert meta["state"] == "recovered" and meta["recovered"] is True
    box = json.loads((folder / "portal-outbox.json").read_text(encoding="utf-8"))
    c = box["completeness"]
    assert c["recovered"] is True and c["overall"] == "partial"
    assert c["loss_window_seconds"] == capture.LOSS_WINDOW_S
    ch = c["channels"][channel]
    assert ch["status"] == "interrupted"
    assert any("stopped unexpectedly" in g["reason"] for g in ch["gaps"])
    gap = ch["gaps"][-1]
    assert timeline._t(gap["to"]) - timeline._t(gap["from"]) == pytest.approx(capture.LOSS_WINDOW_S)
    others = [k for k in capture.CHANNELS if k not in (channel, "network")]
    assert all(c["channels"][k]["status"] == "not_requested" for k in others)
    name = {"web": "web", "desktop": "desktop", "mainframe": "mainframe"}[channel]
    steps = timeline.read_jsonl(folder / name / "steps.jsonl")
    dropped = {e for r in capture.read(capture.path_of(folder, channel)) if r["type"] == "drop"
               for e in r["event_ids"]}  # fmt: skip
    assert [s["event_id"] for s in steps] == [r["event_id"] for r in journaled
                                              if r["event_id"] not in dropped]  # fmt: skip
    if channel == "web":
        assert [s["action"] for s in steps] == ["click", "type", "click"]
        assert steps[-1]["incomplete"] is True and steps[-1]["origin"] == ORIGIN
        assert "incomplete" not in steps[0] and steps[1]["selector"] == "input#q"
        assert any("without their detail" in n for n in ch["notes"])
        assert len(box["flows"][f"web:{ORIGIN}"]) == 3
    if channel == "desktop":
        assert [s["action"] for s in steps] == ["click", "key"]
        assert steps[1]["incomplete"] is True
    if channel == "mainframe":
        assert [s["action"] for s in steps] == ["screen", "aid"]
        assert not any(s.get("incomplete") for s in steps)
    flows = [s for f in box["flows"].values() for s in f]
    assert {s["event_id"] for s in flows} <= {s["event_id"] for s in steps}
    assert any("rebuilt from the capture journal" in n for n in meta["notes"])


def test_a_web_recording_killed_mid_capture_keeps_its_har(tmp_path):
    folder = _kill_mid_capture(tmp_path, "web")
    assert not (folder / "web" / "recording.har").exists()  # the recorder writes it at the end
    assert recover(folder) is True
    har = json.loads((folder / "web" / "recording.har").read_text(encoding="utf-8"))
    entries = har["log"]["entries"]
    assert [e["request"]["url"] for e in entries] == [
        ORIGIN + "/api/search",
        ORIGIN + "/api/cases/1",
    ]
    assert json.loads(entries[0]["response"]["content"]["text"]) == {"hits": 1}
    text = json.dumps(har)
    assert "Hunter-42" not in text and "s3cr3t" not in text  # typed values, credential headers
    typed = next(
        s for s in timeline.read_jsonl(folder / "web" / "steps.jsonl") if s["action"] == "type"
    )
    assert entries[0]["_event_id"] == typed["event_id"]  # the network evidence links by event id
    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert any("HAR was rebuilt" in n for n in meta["notes"])
    assert not any("has no HAR" in n for n in meta["notes"])
    box = json.loads((folder / "portal-outbox.json").read_text(encoding="utf-8"))
    assert box["completeness"]["network_evidence"] == "incomplete"
    assert box["completeness"]["channels"]["network"]["status"] == "interrupted"


# --- a whole session, killed ------------------------------------------------------------------

SESSION_CHILD = r"""
import importlib.util, sys, time
from pathlib import Path
from labctl.workstation.levels import Targets
from labctl.workstation.session import Session

root, port, demo_path = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("demo_host", demo_path)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

def script(session):
    demo.flow(session.mainframe[0].listen)
    print("flowed", flush=True)
    time.sleep(120)

Session(root, Targets.from_list([{"tn3270": f"127.0.0.1:{port}", "listen": 0}]),
        max_seconds=300).run(script=script)
"""


def _demo_host():
    spec = importlib.util.spec_from_file_location(
        "demo_host", ROOT / "reference" / "mainframe" / "demo_host.py"
    )
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    stop = threading.Event()
    server = demo.serve(0, stop)
    return demo, server, stop, server.getsockname()[1]


def test_a_session_process_killed_mid_capture_is_recovered_on_the_next_start(tmp_path):
    _demo, server, stop, port = _demo_host()
    script = tmp_path / "session_child.py"
    script.write_text(SESSION_CHILD, encoding="utf-8")
    root = tmp_path / "recordings"
    demo_path = ROOT / "reference" / "mainframe" / "demo_host.py"
    proc = subprocess.Popen([sys.executable, str(script), str(root), str(port), str(demo_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(), text=True)  # fmt: skip
    try:
        line = proc.stdout.readline()
        assert line.strip() == "flowed", proc.stderr.read()
    finally:
        proc.kill()
        proc.wait(30)
        stop.set()
        server.close()
    (folder,) = list(root.iterdir())
    assert json.loads((folder / "session.json").read_text(encoding="utf-8"))["state"] == "recording"
    assert recover_all(root) == [folder.name]
    box = json.loads((folder / "portal-outbox.json").read_text(encoding="utf-8"))
    c = box["completeness"]
    assert c["recovered"] is True and c["overall"] == "partial"
    assert c["channels"]["mainframe"]["status"] == "interrupted"
    (flow,) = box["flows"].values()
    labels = [s.get("label") for s in flow if s["action"] == "type"]
    assert labels[:2] == ["USERID", "PASSWORD"] and all("event_id" in s for s in flow)
    for value in ("TESTER", "DEMO123"):
        assert value not in json.dumps(box)


def test_a_session_that_stops_cleanly_is_complete_and_says_what_it_requested(tmp_path):
    from labctl.workstation.levels import Targets
    from labctl.workstation.session import Session

    demo, server, stop, port = _demo_host()
    targets = Targets.from_list([{"tn3270": f"127.0.0.1:{port}", "listen": 0}])
    pre = {"checks": [{"name": "host reachable", "ok": True, "detail": f"127.0.0.1:{port}"}]}
    try:
        folder = Session(tmp_path / "rec", targets, max_seconds=60, preflight=pre).run(
            script=lambda s: demo.flow(s.mainframe[0].listen)
        )
    finally:
        stop.set()
        server.close()
    box = json.loads((folder / "portal-outbox.json").read_text(encoding="utf-8"))
    c = box["completeness"]
    assert c == {"overall": "complete", "channels": {
        "web": {"status": "not_requested", "gaps": [], "notes": []},
        "desktop": {"status": "not_requested", "gaps": [], "notes": []},
        "mainframe": {"status": "complete", "gaps": [], "notes": []},
        "network": {"status": "not_requested", "gaps": [], "notes": []}},
        "network_evidence": "none", "recovered": False, "loss_window_seconds": 2.0}  # fmt: skip
    assert box["preflight"] == {"checks": pre["checks"], "ready": True}
    assert box["contract_version"] == "1.1.0"
    steps = timeline.read_jsonl(folder / "mainframe" / "steps.jsonl")
    (flow,) = box["flows"].values()
    assert [s["event_id"] for s in flow] == [s["event_id"] for s in steps]
    assert all(s["channel"] == "mainframe" and s["captured_at"] for s in flow)


# --- the manifest -------------------------------------------------------------------------------


def _journal(folder: Path, channel: str, lines: list[dict]) -> None:
    path = capture.path_of(folder, channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")


OPEN = {"type": "open", "at": "2026-11-20T09:00:00.000Z"}
CLOSE = {"type": "close", "at": "2026-11-20T09:05:00.000Z", "clean": True}


def test_manifest_of_a_clean_run(tmp_path):
    meta = _meta(tmp_path / "s", ["web", "network"], state="stopped")
    _journal(tmp_path / "s", "web", [OPEN, CLOSE])
    _journal(tmp_path / "s", "network", [OPEN, {"type": "har", "entry": {}}, CLOSE])
    m = completeness.manifest(tmp_path / "s", meta)
    assert m["overall"] == "complete" and m["network_evidence"] == "complete"
    assert m["recovered"] is False and m["loss_window_seconds"] == 2.0
    assert m["channels"]["web"] == {"status": "complete", "gaps": [], "notes": []}
    assert m["channels"]["desktop"]["status"] == "not_requested"


def test_a_stopped_recording_is_not_complete_by_itself(tmp_path):
    folder = tmp_path / "s"
    meta = _meta(folder, ["web", "network", "desktop"], state="stopped")
    gap = {"type": "gap", "from": "2026-11-20T09:01:00.000Z", "to": "2026-11-20T09:01:04.000Z",
           "reason": "the browser was closed during the session and opened again"}  # fmt: skip
    _journal(folder, "web", [OPEN, gap, CLOSE])
    _journal(folder, "network", [OPEN, {"type": "close", "at": CLOSE["at"], "clean": False}])
    _journal(folder, "desktop", [])  # never started (not Windows, no hook)
    m = completeness.manifest(folder, meta)
    assert m["overall"] == "partial"
    assert m["channels"]["web"]["status"] == "interrupted"
    assert m["channels"]["web"]["gaps"] == [{k: gap[k] for k in ("from", "to", "reason")}]
    assert m["channels"]["network"]["status"] == "interrupted"
    assert m["network_evidence"] == "incomplete"
    assert m["channels"]["desktop"]["status"] == "missing"
    for ch in ("web", "network"):
        _journal(folder, ch, [])
    assert completeness.manifest(folder, meta)["overall"] == "failed"


def test_what_the_supervisor_knows_is_merged_worst_first(tmp_path):
    folder = tmp_path / "s"
    hb = {"from": "2026-11-20T09:02:00.000Z", "to": "2026-11-20T09:02:09.000Z",
          "reason": "no heartbeat from the desktop collector"}  # fmt: skip
    meta = _meta(folder, ["desktop"], state="stopped",
                 channel_status={"desktop": {"status": "complete", "gaps": [hb], "notes": ["n"]}})  # fmt: skip
    _journal(folder, "desktop", [OPEN, CLOSE])
    d = completeness.manifest(folder, meta)["channels"]["desktop"]
    assert d == {"status": "interrupted", "gaps": [hb], "notes": ["n"]}
    meta["channel_status"] = {"desktop": {"status": "missing"}}
    assert completeness.manifest(folder, meta)["channels"]["desktop"]["status"] == "missing"


def test_a_session_recorded_before_the_journal_stays_readable(tmp_path):
    folder = tmp_path / "s"
    meta = _meta(folder, ["web", "network"], state="stopped")
    del meta["channels"]  # contract 1.0: no channels, no capture journal, no event ids
    (folder / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    (folder / "web").mkdir()
    (folder / "web" / "steps.jsonl").write_text(json.dumps(
        {"seq": 1, "ts": "2026-11-20T09:00:01.000Z", "url": ORIGIN + "/x", "action": "click",
         "selector": "button#b"}) + "\n", "utf-8")  # fmt: skip
    box = timeline.build(folder)
    (step,) = box["flows"][f"web:{ORIGIN}"]
    assert "event_id" not in step and step["n"] == 1
    c = box["completeness"]
    assert c["loss_window_seconds"] is None and c["channels"]["web"]["status"] == "complete"
    assert c["channels"]["network"]["status"] == "missing" and c["overall"] == "partial"
    old = {k: v for k, v in box.items() if k not in ("contract_version", "completeness")}
    (folder / "portal-outbox.json").write_text(json.dumps(old), encoding="utf-8")
    read = timeline.read_references(folder / "portal-outbox.json")
    assert read["contract_version"] == "1.0.0" and read["completeness"] is None


def test_the_session_journal_rows_get_event_ids_and_are_durable(tmp_path):
    from labctl.workstation.levels import Targets
    from labctl.workstation.session import Session

    s = Session(tmp_path, Targets.from_list([{"origin": ORIGIN}]), max_seconds=1)
    s.folder.mkdir()
    s._journal = capture.Journal(s.folder / "journal.jsonl", ticks=False)
    s.journal({"kind": "presence", "exe": "notepad.exe"})
    s.journal({"kind": "outside", "denied": False})
    s.journal({"kind": "app", "key": DESK})
    s._journal.close()
    rows = timeline.read_jsonl(s.folder / "journal.jsonl")
    assert rows[0]["channel"] == "desktop" and rows[1]["channel"] == "web"
    assert all("event_id" in r for r in rows[:2]) and "event_id" not in rows[2]
    assert s.requested_channels() == ["web", "network"]


def test_robot_coverage_links_activities_to_events_by_id():
    from labctl.workstation import robot

    eid = capture.uuid7()
    acts = [{"seq": 1, "ts": "2026-11-20T09:00:01.000Z", "end_ts": "2026-11-20T09:00:01.200Z",
             "activity": "Click"}]  # fmt: skip
    rec = [{"ts": "2026-11-20T09:00:01.100Z", "event_id": eid},
           {"ts": "2026-11-20T09:00:01.150Z"}]  # a 1.0 step: covered, no id to link  # fmt: skip
    cov = robot.coverage(acts, rec)
    assert cov["covered"] == 1 and cov["links"] == [{"seq": 1, "event_ids": [eid]}]
