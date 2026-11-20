"""The collector supervisor (labctl.workstation.supervisor) and the capture preflight.

The collectors here are stand-ins run in real child processes (spawn): one that keeps working,
one that hangs as a UI Automation call into a hung application would, one that crashes, one that
ignores the request to stop. Windows-only checks are marked and skipped elsewhere.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from labctl.workstation import journal as capture
from labctl.workstation import preflight as pf
from labctl.workstation import supervisor as sv
from labctl.workstation.levels import Targets
from labctl.workstation.session import Session, _now

ME = __name__
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


# -- collectors run in the child processes ---------------------------------------------------------


def healthy(ctx, spec):
    ctx.ready()
    n = 0
    while not ctx.stopping():
        ctx.progress()
        n += 1
        ctx.send({"t": "row", "row": {"kind": "probe", "n": n, "ts": _now()}})
        time.sleep(0.1)


def hangs(ctx, spec):
    ctx.ready()
    time.sleep(3600)  # a call that never returns: no progress, heartbeats go on


def crashes_first(ctx, spec):
    if not spec.get("resume"):
        ctx.ready()
        time.sleep(0.3)
        os._exit(3)
    healthy(ctx, spec)


def raises(ctx, spec):
    raise RuntimeError("the recorder could not start")


def ends_early(ctx, spec):
    ctx.note("no browser answers: web targets not recorded")


def ignores_stop(ctx, spec):
    ctx.ready()
    while True:
        ctx.progress()
        time.sleep(0.1)


# -- helpers --------------------------------------------------------------------------------------


def _session(tmp_path: Path, cdp: str | None = None) -> Session:
    s = Session(tmp_path / "rec", Targets.from_list([]), name="sup", cdp=cdp)
    s.folder.mkdir(parents=True)
    s.started = _now()
    s._journal = capture.Journal(s.folder / "journal.jsonl", ticks=False)
    return s


def _rows(s: Session) -> list[dict]:
    text = (s.folder / "journal.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _probes(s: Session, channel_rows=None) -> int:
    return sum(1 for r in _rows(s) if r.get("kind") == "probe")


def _wait(cond, seconds: float = 30.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _sup(s, channels, **kw) -> sv.Supervisor:
    kw.setdefault("heartbeat_timeout", 2.0)
    kw.setdefault("shutdown_s", 5.0)
    kw.setdefault("ready_wait", 30.0)
    return sv.Supervisor(s, channels={k: f"{ME}:{v}" for k, v in channels.items()}, **kw)


# -- the supervisor -------------------------------------------------------------------------------


def test_a_hung_collector_does_not_stop_the_others_and_is_marked_failed(tmp_path):
    s = _session(tmp_path)
    sup = _sup(s, {"web": "healthy", "desktop": "hangs"})
    sup.start()
    try:
        assert _wait(lambda: len(sup.channels["desktop"].failures) >= 2, 40), "not failed twice"
        before = _probes(s)
        time.sleep(1.0)
        assert _probes(s) > before, "the web collector stopped while the desktop one hung"
    finally:
        status = sup.shutdown()
    desk = status["desktop"]
    assert desk["status"] == "interrupted"
    assert desk["details"]["restarts"] == 1  # restarted once, not twice
    assert "made no progress" in desk["details"]["failures"][0]["reason"]
    assert desk["gaps"] and all(g["from"] and g["to"] and g["reason"] for g in desk["gaps"])
    assert "not restarted" in desk["gaps"][-1]["reason"]
    assert status["web"]["status"] == "complete"
    assert status["mainframe"]["status"] == "not_requested"
    events = [r["event"] for r in _rows(s) if r.get("kind") == "channel"
              and r["channel"] == "desktop"]  # fmt: skip
    assert events.count("failed") == 2 and "restarted" in events
    assert any("desktop channel failed" in n for n in s.notes)
    saved = json.loads((s.folder / "channels.json").read_text(encoding="utf-8"))
    assert saved["channels"]["desktop"]["status"] == "interrupted"


def test_a_crashed_collector_is_restarted_once_and_the_gap_closes(tmp_path):
    s = _session(tmp_path)
    sup = _sup(s, {"web": "crashes_first"})
    sup.start()
    try:
        assert _wait(lambda: sup.channels["web"].restarts == 1
                     and sup.channels["web"].state == "running"
                     and sup.channels["web"].open_gap() is None, 40)  # fmt: skip
    finally:
        status = sup.shutdown()
    web = status["web"]
    assert web["status"] == "interrupted"
    assert "ended unexpectedly (exit code 3)" in web["details"]["failures"][0]["reason"]
    assert len(web["gaps"]) == 1 and "restarted" in web["gaps"][0]["reason"]
    assert web["gaps"][0]["to"] >= web["gaps"][0]["from"]


def test_an_error_or_an_early_end_is_explicit_and_a_never_ready_channel_is_missing(tmp_path):
    s = _session(tmp_path)
    sup = _sup(s, {"web": "ends_early", "mainframe": "raises"})
    sup.start()
    try:
        assert _wait(lambda: sup.channels["web"].state == "missing"
                     and sup.channels["mainframe"].state == "failed", 30)  # fmt: skip
    finally:
        status = sup.shutdown()
    assert status["web"]["status"] == "missing"
    assert "no browser answers" in status["web"]["gaps"][0]["reason"]
    mf = status["mainframe"]
    assert mf["status"] == "missing"
    assert "RuntimeError: the recorder could not start" in mf["gaps"][0]["reason"]
    assert "not restarted" in mf["gaps"][0]["reason"]  # the TN3270 proxy is not restarted
    assert status["desktop"]["status"] == "not_requested"


def test_shutdown_is_bounded_and_records_the_channel_cut_off(tmp_path):
    s = _session(tmp_path)
    sup = _sup(s, {"web": "healthy", "desktop": "ignores_stop"}, shutdown_s=1.5)
    sup.start()
    time.sleep(0.5)
    t0 = time.monotonic()
    status = sup.shutdown()
    assert time.monotonic() - t0 < 15
    assert not sup.channels["desktop"].alive()
    desk = status["desktop"]
    assert desk["status"] == "interrupted" and desk["details"]["state"] == "cut_off"
    assert "did not stop within 2 s" in desk["gaps"][0]["reason"]
    assert status["web"]["status"] == "complete"
    assert any(r.get("event") == "cut_off" for r in _rows(s))


def test_attach_mode_warns_at_the_start_and_keeps_it_in_the_notes(tmp_path):
    s = _session(tmp_path, cdp="http://127.0.0.1:9")
    sup = _sup(s, {"web": "healthy"})
    sup.start()
    assert sv.CDP_WARNING in s.notes  # shown before anything is recorded
    status = sup.shutdown()
    web = status["web"]
    assert web["details"]["mode"] == "attach"
    assert any("Chromium-based browsers only" in n for n in web["notes"])
    assert any(r.get("event") == "warning" for r in _rows(s))


def test_the_recorder_controlled_browser_is_the_default_mode(tmp_path):
    s = _session(tmp_path)
    sup = _sup(s, {"web": "healthy"})
    sup.start()
    status = sup.shutdown()
    assert status["web"]["details"]["mode"] == "controlled"
    assert sv.CDP_WARNING not in s.notes


def test_requested_channels_follow_the_targets():
    t = Targets.from_list([{"origin": "https://a.example"}, {"tn3270": "h.example:23"}])
    assert sv.requested(t) == {"web", "mainframe"}
    assert sv.requested(Targets.from_list([{"exe": "casedesk.exe"}])) == {"desktop"}


def test_inaccessible_desktop_steps_are_reported():
    rows = [
        {
            "seq": 1,
            "action": "click",
            "target": {"type": "Button", "props": {"AutomationId": "ok"}},
        },  # fmt: skip
        {"seq": 2, "action": "click", "target": {"type": "Pane", "props": {"ControlType": "Pane"}}},
        {"seq": 3, "action": "type", "target": None},
        {"seq": 4, "action": "window_open", "target": None},
        {"seq": 5, "action": "click", "target": {"type": "Edit", "props": {}}},  # a data name
    ]
    found = sv.inaccessible_steps(rows)
    assert [f["seq"] for f in found] == [2, 3]
    assert sv.inaccessible_steps(rows, after_seq=2)[0]["seq"] == 3


def test_a_locked_desktop_is_a_gap(tmp_path):
    states = iter([False, True, True, False])

    class Ctx:
        sent: list = []

        def gap(self, start, end, reason):
            self.sent.append({"t": "gap", "from": start, "to": end, "reason": reason})

        def send(self, msg):
            self.sent.append(msg)

    ctx, stop = Ctx(), threading.Event()
    t = threading.Thread(target=sv._watch_lock,
                         args=(ctx, stop, lambda: next(states, False)))  # fmt: skip
    t.start()
    time.sleep(4.5)
    stop.set()
    t.join(5)
    assert [m["t"] for m in ctx.sent] == ["gap", "gap_end"]
    assert ctx.sent[0]["reason"] == sv.LOCKED_REASON


def test_a_session_runs_its_collectors_in_processes_of_their_own(tmp_path):
    t = Targets.from_list([{"tn3270": f"127.0.0.1:{_free_port()}", "listen": 0}])
    seen = {}

    def script(session):
        seen["listen"] = session.mainframe[0].listen
        seen["pid"] = session.supervisor.channels["mainframe"].proc.pid

    s = Session(tmp_path / "rec", t, max_seconds=60)
    folder = s.run(script=script)
    assert seen["listen"] > 0 and seen["pid"] != os.getpid()
    status = s.channel_status()
    assert status["mainframe"]["status"] == "complete", status
    assert status["web"]["status"] == status["desktop"]["status"] == "not_requested"
    assert sv.read_status(folder)["mainframe"]["status"] == "complete"
    assert json.loads((folder / "session.json").read_text(encoding="utf-8"))["apps"]


@windows
def test_the_desktop_lock_check_reads_this_desktop():
    assert sv.desktop_locked() in (False, True)


@windows
def test_the_real_desktop_collector_starts_and_stops_in_its_own_process(tmp_path):
    s = _session(tmp_path)
    s.targets = Targets.from_list([{"exe": "twinlab-no-such-app.exe"}])
    sup = sv.Supervisor(s, heartbeat_timeout=30, shutdown_s=60)
    sup.start()
    try:
        assert sup.channels["desktop"].ever_ready
        assert sup.channels["desktop"].proc.pid != os.getpid()
        time.sleep(1.0)
    finally:
        status = sup.shutdown()
    assert status["desktop"]["status"] == "complete", status["desktop"]
    assert (s.folder / "desktop" / "desktop.json").is_file()


# -- preflight ------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


class _Http:
    def __init__(self, body: dict | None = None):
        doc = json.dumps(body or {}).encode()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.end_headers()
                self.wfile.write(doc)

            do_HEAD = do_GET  # noqa: N815

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _names(got):
    return {c["name"]: c for c in got["checks"]}


def test_preflight_ready_when_every_check_passes(tmp_path, monkeypatch):
    site = _Http()
    monkeypatch.setattr(pf, "check_browser", lambda *a: pf._check("browser", True, "stub"))
    try:
        got = pf.preflight(Targets.from_list([{"origin": site.url}]), out=tmp_path, min_free_mb=1)
    finally:
        site.close()
    assert got["ready"], got
    assert {"recorder", "disk", "browser", f"reach {site.url}"} <= set(_names(got))
    assert all({"name", "ok", "detail"} == set(c) for c in got["checks"])


def test_preflight_fails_when_the_disk_is_short(tmp_path):
    got = pf.preflight(Targets.from_list([]), out=tmp_path / "x" / "y", min_free_mb=1e12)
    assert not got["ready"] and not _names(got)["disk"]["ok"]


def test_preflight_fails_when_a_target_does_not_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "check_browser", lambda *a: pf._check("browser", True, "stub"))
    url = f"http://127.0.0.1:{_free_port()}"
    got = pf.preflight(Targets.from_list([{"origin": url}]), out=tmp_path, min_free_mb=1,
                       timeout=2)  # fmt: skip
    assert not got["ready"] and not _names(got)[f"reach {url}"]["ok"]


def test_preflight_fails_without_a_browser(tmp_path, monkeypatch):
    import labctl.workstation.session as ses

    monkeypatch.setattr(ses, "find_browser", lambda *a: None)
    monkeypatch.setattr(sv, "_playwright_chromium", lambda: None)
    c = pf.check_browser()
    assert not c["ok"] and "no Edge, Chrome or Playwright Chromium" in c["detail"]


def test_preflight_attach_mode_needs_a_chromium_browser_that_answers():
    assert not pf.check_browser(cdp=f"http://127.0.0.1:{_free_port()}", timeout=2)["ok"]
    firefox = _Http({"Browser": "Firefox/140.0"})
    chrome = _Http({"Browser": "Chrome/141.0.7390.0"})
    try:
        bad = pf.check_browser(cdp=firefox.url)
        good = pf.check_browser(cdp=chrome.url)
    finally:
        firefox.close()
        chrome.close()
    assert not bad["ok"] and "Chromium-only" in bad["detail"]
    assert good["ok"] and "attach mode" in good["detail"]


def test_preflight_fails_on_a_contract_the_portal_does_not_take():
    assert pf.check_recorder(None)["ok"]
    assert not pf.check_recorder("1.0.0", contract="")["ok"]  # a recorder stating none
    assert not pf.check_recorder("2.0.0", contract="1.1.0")["ok"]
    assert not pf.check_recorder("1.2.0", contract="1.1.0")["ok"]
    assert pf.check_recorder("1.0.0", contract="1.1.0")["ok"]


def test_preflight_fails_when_the_mainframe_or_its_proxy_port_is_not_usable(tmp_path):
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    port = taken.getsockname()[1]
    try:
        t = Targets.from_list([{"tn3270": f"127.0.0.1:{_free_port()}", "listen": port}])
        got = _names(pf.preflight(t, out=tmp_path, min_free_mb=1, timeout=2))
    finally:
        taken.close()
    assert not got[f"proxy port {port}"]["ok"]
    assert not next(c for n, c in got.items() if n.startswith("mainframe "))["ok"]


def test_preflight_desktop_check(monkeypatch):
    if sys.platform != "win32":
        c = pf.check_desktop()
        assert not c["ok"] and "needs Windows" in c["detail"]
        return
    c = pf.check_desktop()
    assert c["ok"] or "locked" in c["detail"], c
