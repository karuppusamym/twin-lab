"""Live workstation session: a real Edge, the real CaseDesk (WinForms) and Notepad, on this PC.

One session records CaseDesk (a desktop target), one web origin (a web target), a second web
origin that is not a target, and Notepad (not a target). It checks that the targets are
recorded in full, the others only as presence, and that nothing of them reaches the portal's
references. Opt in like the other GUI tests:

    TWINLAB_DESKTOP_GUI=1 python -m pytest -q lab/labctl/tests/test_workstation_gui.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CASEDESK = ROOT / "reference" / "desktop" / "CaseDesk"

pytestmark = [
    pytest.mark.desktop_gui,
    pytest.mark.skipif(sys.platform != "win32", reason="needs Windows"),
    pytest.mark.skipif(importlib.util.find_spec("comtypes") is None, reason="desktop extra missing"),
    pytest.mark.skipif(os.environ.get("TWINLAB_DESKTOP_GUI") != "1",
                       reason="starts GUI apps and clicks: set TWINLAB_DESKTOP_GUI=1 to run"),
]  # fmt: skip

PAGE = b"""<!DOCTYPE html><html><head><title>Orders</title></head><body>
<form id="f" onsubmit="event.preventDefault(); fetch('/api/find?q=' + q.value).then(r => r.json())
  .then(d => { out.textContent = d.found; })">
<input id="q" name="q"><button id="go" type="submit">Find</button></form>
<p id="out">nothing yet</p><a id="away" href="OTHER/">other site</a></body></html>"""
OTHER = b"""<!DOCTYPE html><html><head><title>Private mail</title></head><body>
<p>Dear Jane Doe, your account 4111-2222 is ready.</p><button id="read">Read</button></body></html>"""


def _server(body: bytes, other: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/api/find"):
                data, ctype = b'{"found": 3}', "application/json"
            else:
                data, ctype = body.replace(b"OTHER", other.encode()), "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _flow():
    spec = importlib.util.spec_from_file_location("casedesk_flow", CASEDESK / "flow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_one_session_across_casedesk_edge_and_presence_apps(tmp_path):
    if shutil.which("dotnet") is None:
        pytest.skip("the .NET SDK is not installed")
    from labctl.desktop import win
    from labctl.workstation.levels import Targets
    from labctl.workstation.session import Session, find_browser

    if find_browser() is None:
        pytest.skip("no Edge or Chrome on this machine")
    flow = _flow()
    exe = flow.build()
    other_srv, other = _server(OTHER)
    site_srv, site = _server(PAGE, other)
    targets = Targets.from_list([{"exe": "CaseDesk.exe", "start": str(exe)},
                                 {"origin": site, "start": site + "/"}])  # fmt: skip

    def script(session: Session) -> None:
        from playwright.sync_api import sync_playwright

        end = time.monotonic() + 30
        while session.cdp is None and time.monotonic() < end:
            time.sleep(0.2)
        assert session.cdp, "the session browser did not start"
        time.sleep(2.0)  # the web recorder has attached
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(session.cdp)
            page = browser.contexts[0].pages[0]
            page.bring_to_front()
            page.wait_for_selector("#q")
            page.fill("#q", "C-1")
            page.click("#go")
            page.wait_for_function("document.querySelector('#out').textContent === '3'")
            page.click("#away")  # to the site that is not a target
            page.wait_for_selector("#read")
            page.click("#read")
            page.goto(site + "/")
            page.wait_for_selector("#q")
            page.fill("#q", "C-2")
            page.click("#go")
            page.wait_for_timeout(800)
        # an application that is not a target, in front for a moment
        notepad = subprocess.Popen(["notepad.exe"])
        time.sleep(2.5)
        subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
        notepad.wait(5)
        # the desktop target: CaseDesk was started by the session
        end = time.monotonic() + 20
        pids: set[int] = set()
        while not pids and time.monotonic() < end:
            pids = {p for p in win.pids_by_name("CaseDesk.exe") if win.top_windows({p})}
            time.sleep(0.3)
        assert pids, "CaseDesk did not start"
        time.sleep(1.0)
        flow.flow(pids)

    try:
        session = Session(tmp_path / "recordings", targets, name="gui trial", max_seconds=300)
        folder = session.run(script=script)
    finally:
        site_srv.shutdown()
        other_srv.shutdown()

    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert meta["state"] == "stopped", meta["notes"]
    assert not [n for n in meta["notes"] if "failed" in n], meta["notes"]
    assert not (folder / "web-profile").exists()
    rows = [
        json.loads(x) for x in (folder / "session.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    apps = {r.get("app") for r in rows if r.get("action") != "app-switch"}
    desk = "desktop:Twin Lab reference/CaseDesk"
    web = f"web:{site}"
    assert {desk, web, "presence"} <= apps, apps
    assert any(r["action"] == "app-switch" for r in rows)

    # the web recording holds the target site only
    web_steps = (folder / "web" / "steps.jsonl").read_text(encoding="utf-8")
    assert other not in web_steps and site in web_steps
    har = json.loads((folder / "web" / "recording.har").read_text(encoding="utf-8"))
    assert har["log"]["entries"] and not [e for e in har["log"]["entries"]
                                          if e["request"]["url"].startswith(other)]  # fmt: skip
    for f in (folder / "web" / "dom").glob("*.html"):
        assert "Jane Doe" not in f.read_text(encoding="utf-8")

    # the desktop recording holds CaseDesk only
    desktop = json.loads((folder / "desktop" / "desktop.json").read_text(encoding="utf-8"))
    assert {p["process"] for p in desktop["processes"]} == {"CaseDesk.exe"}

    # the references: two applications, labels only, nothing of the others
    box = json.loads((folder / "portal-outbox.json").read_text(encoding="utf-8"))
    assert {a["key"] for a in box["apps"]} == {desk, web}
    assert len(box["flows"][desk]) >= 8 and len(box["flows"][web]) >= 3
    text = json.dumps(box)
    for leak in (other, "notepad", "Notepad", "Jane", "4111", "C-1", "C-2", "tester", "demo"):
        assert leak not in text, leak
