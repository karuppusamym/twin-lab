"""The replay waits after a step as the recorder did before its snapshot (found replaying
Demoblaze: a Bootstrap dialog closes with a 300 ms fade).

The recorder waits for a page quiet for 400 ms (`recording.json`, settle.quiet_ms); the replay
waited 150 ms and compared the dialog mid-fade: still shown, where the recording has it hidden.
The replay now waits at least the recorder's quiet time, and until the page's CSS transitions
and animations that end by themselves have ended (read-only, with an upper bound).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from labctl.behaviour import fidelity

# As Bootstrap closes a dialog: the class that shows it goes, the opacity fades for 300 ms, and
# only when the transition has ended is it hidden (display: none). The page makes no request.
PAGE = b"""<!DOCTYPE html><html><head><style>
#dlg { transition: opacity 300ms linear; opacity: 1; }
#dlg.closing { opacity: 0; }
.spinner { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style></head><body>
<div class="spinner">*</div>
<div id="dlg"><p>Thanks</p><button id="close" type="button">Close</button></div>
<script>
const dlg = document.getElementById("dlg");
document.getElementById("close").addEventListener("click", () => {
  dlg.classList.add("closing");
  dlg.addEventListener("transitionend", () => { dlg.style.display = "none"; }, {once: true});
});
</script></body></html>"""


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from labctl import recorder

    def flow(page):
        page.wait_for_timeout(500)
        page.click("#close")
        page.wait_for_timeout(1200)

    server, base = serve()
    out = tmp_path_factory.mktemp("settle") / "rec"
    try:
        recorder.record(f"{base}/", out, headless=True, script=flow)
    finally:
        server.shutdown()
    return out, base


def test_the_recording_says_how_long_its_recorder_waited(recorded):
    rec, _ = recorded
    assert json.loads((rec / "recording.json").read_text("utf-8"))["settle"]["quiet_ms"] == 400
    from labctl.botlog.recording import load

    assert fidelity.recorder_quiet_ms(load(rec)) == 400


def test_the_recorded_dialog_is_hidden_after_the_click(recorded):
    rec, _ = recorded
    steps = [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]
    click = next(s for s in steps if s["action"] == "click")
    html = (rec / click["dom"]).read_text("utf-8")
    assert "display: none" in html


def test_a_dialog_fading_out_is_compared_once_it_has_closed(recorded):
    rec, base = recorded
    server, fresh = serve()
    try:
        started = time.monotonic()
        report = fidelity.replay(rec, origins={base: fresh}, resolve_timeout_ms=3000)
        took = time.monotonic() - started
    finally:
        server.shutdown()
    diffs = [d.text() for s in report.steps for d in s.differences]
    assert report.status == "pass" and diffs == [], report.markdown()
    # the spinner repeats forever: it is never waited for (the bound is not reached either)
    assert took < 20, took


def test_the_replay_waits_for_a_transition_and_never_for_an_endless_animation():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    server, base = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(base)
            assert fidelity._animating(page) is False  # only the endless spinner runs
            page.click("#close")
            assert fidelity._animating(page) is True  # the fade
            fidelity._track(page)
            fidelity._settle(page, 150)
            assert page.evaluate("getComputedStyle(document.getElementById('dlg')).display") == (
                "none"
            )
            browser.close()
    finally:
        server.shutdown()
