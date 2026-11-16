"""The recorder: a page before each action, a PNG after each step, and attaching to a browser
that is already running (as a robot's would be) over the DevTools protocol."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.sync_api")

from labctl import har, recorder  # noqa: E402

PAGE = b"""<!DOCTYPE html><html><body>
<form id="f" onsubmit="event.preventDefault(); fetch('/api/find?q=' + q.value).then(r => r.json())
  .then(d => { out.textContent = d.found; })">
<input id="q" name="q"><button id="go" type="submit">Find</button></form>
<p id="out">nothing yet</p></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/find"):
            body, ctype = json.dumps({"found": 3}).encode(), "application/json"
        else:
            body, ctype = PAGE, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def drive(page):
    page.fill("#q", "C-1")
    page.click("#go")
    page.wait_for_function("document.querySelector('#out').textContent === '3'")


def steps_of(out):
    return [json.loads(line) for line in (out / "steps.jsonl").read_text().splitlines()]


def test_record_keeps_the_page_before_each_action(site, tmp_path):
    out = recorder.record(f"{site}/", tmp_path / "rec", script=drive, headless=True)
    steps = steps_of(out)
    click = next(s for s in steps if s["action"] == "click")
    assert click["dom_before"] and (out / click["dom_before"]).exists()
    before = (out / click["dom_before"]).read_text(encoding="utf-8")
    assert "nothing yet" in before  # the page as the click met it
    assert not any(s["screenshot"] for s in steps)  # a script's run has no honest pictures
    assert har.load(out).steps  # the analyser still reads the recording


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_attach_records_a_browser_someone_else_started(site, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        exe = pw.chromium.executable_path
    port = _free_port()
    args = [exe, "--headless=new", f"--remote-debugging-port={port}", "--no-first-run",
            f"--user-data-dir={tmp_path / 'profile'}"]  # fmt: skip
    if sys.platform.startswith("linux"):
        args.append("--no-sandbox")  # CI containers have no user namespaces for Chrome's sandbox
    log = (tmp_path / "chrome.log").open("w")
    robot = subprocess.Popen([*args, f"{site}/"], stdout=log, stderr=subprocess.STDOUT)
    try:
        cdp = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while True:  # the DevTools endpoint answers once the browser is up
            try:
                with urllib.request.urlopen(f"{cdp}/json/version", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            if robot.poll() is not None or time.monotonic() > deadline:
                log.flush()
                pytest.fail(
                    "the browser did not start:\n" + (tmp_path / "chrome.log").read_text()[-2000:]
                )
            time.sleep(0.2)
        done = threading.Event()
        result = {}

        def record():
            try:
                result["out"] = recorder.attach(
                    cdp, tmp_path / "att", stop=done.is_set, settle_ms=200
                )
            except Exception as exc:  # noqa: BLE001 - reported by the test below
                result["error"] = exc

        t = threading.Thread(target=record)
        t.start()
        time.sleep(2.5)  # the recorder connects and installs its listener
        with sync_playwright() as pw:  # the "robot" drives its own browser
            browser = pw.chromium.connect_over_cdp(cdp)
            page = browser.contexts[0].pages[0]
            drive(page)
            page.wait_for_timeout(800)
        done.set()
        t.join(30)
        assert not t.is_alive()
    finally:
        robot.terminate()
        robot.wait(10)
        log.close()
    assert "error" not in result, f"the recorder failed: {result.get('error')!r}"
    out = result["out"]
    steps = steps_of(out)
    assert "click" in [s["action"] for s in steps]
    shots = [s["screenshot"] for s in steps if s.get("screenshot")]
    assert shots and all((out / s).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for s in shots)
    entries = json.loads((out / "recording.har").read_text())["log"]["entries"]
    api = [e for e in entries if "/api/find" in e["request"]["url"]]
    assert api and json.loads(api[0]["response"]["content"]["text"]) == {"found": 3}
    assert any(x.path == "/api/find" for x in har.exchanges(har.load(out).har))
