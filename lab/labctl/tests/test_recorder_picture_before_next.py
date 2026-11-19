"""A step's picture is taken before the next action's events come (Demoblaze, rounds 2 and 3:
the picture after typing the message was taken 6 ms after the click on Close had begun, so it
showed the dialog fading and was only marked `picture_settled: false`).

A recording script's `fill` followed at once by a `click` (as Playwright scripts do): before the
click, the page writes and snapshots the typing (`__tlBeforeAction`) and the recorder takes its
picture, so the picture shows the page before the click and is settled. Served from 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.sync_api")

from labctl import recorder  # noqa: E402

# The click turns the panel red at once (no transition): a picture taken after the click began
# shows red; one taken before it, green.
PAGE = b"""<!DOCTYPE html><html><body style="margin:0">
<div id="panel" style="width:400px;height:300px;background:rgb(0,160,0)">
  <textarea id="message"></textarea>
  <button id="close" type="button">Close</button>
</div>
<script>
document.getElementById("close").addEventListener("click", () => {
  document.getElementById("panel").style.background = "rgb(220,0,0)";
});
</script></body></html>"""


def _serve():
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


def _flow(page):
    page.wait_for_timeout(600)
    page.fill("#message", "hello there")
    page.click("#close")  # a few ms later: well inside the 400 ms settle window
    page.wait_for_timeout(600)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    server, base = _serve()
    out = tmp_path_factory.mktemp("rec") / "r"
    try:
        recorder.record(f"{base}/", out, script=_flow, headless=True)
    finally:
        server.shutdown()
    steps = [json.loads(x) for x in (out / "steps.jsonl").read_text("utf-8").splitlines()]
    return out, steps


def _colour(path):
    from PIL import Image

    return Image.open(path).convert("RGB").getpixel((390, 290))


def test_the_typing_steps_picture_is_taken_before_the_click(recorded):
    out, steps = recorded
    (typed,) = [s for s in steps if s["action"] == "type"]
    (click,) = [s for s in steps if s["action"] == "click"]
    assert typed["seq"] < click["seq"] and typed.get("screenshot"), typed
    assert "picture_settled" not in typed, typed.get("picture_note")
    r, g, b = _colour(out / typed["screenshot"])
    assert g > 120 and r < 60, (r, g, b)  # green: the page before the click
    # the click's own effect is in the snapshot after it, as before
    after = [s for s in steps if s["seq"] >= click["seq"] and s.get("dom")]
    assert after and "rgb(220, 0, 0)" in (out / after[-1]["dom"]).read_text("utf-8")


def test_the_typed_value_stays_out_of_the_recording(recorded):
    out, steps = recorded
    text = (out / "steps.jsonl").read_text("utf-8")
    text += "".join(p.read_text("utf-8") for p in (out / "dom").glob("*.html"))
    assert "hello there" not in text


def test_a_remaining_late_picture_is_still_marked():
    steps = [
        {"seq": 1, "ts": "2026-11-19T02:53:25.149Z", "action": "type", "tab": "t1",
         "screenshot": "screens/0001.png", "_shot_at": "2026-11-19T02:53:25.190Z"},
        {"seq": 2, "ts": "2026-11-19T02:53:25.184Z", "action": "click", "tab": "t1"},
    ]  # fmt: skip
    assert recorder.mark_unsettled_pictures(steps) == 1
    assert steps[0]["picture_settled"] is False and "6 ms" in steps[0]["picture_note"]
