"""Why a replay can fail with every step passed and no difference (seen once in
test_reference_primefaces.py, not reproduced): a request the replica made to the source or to
an origin with no replica is refused, and with an origin map that fails the replay. Such a
request can come at any moment (a timer, a late answer), so the report now says during which
step it was refused, or that it came before the first step or after the last.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from labctl.behaviour import fidelity

# A timer the page sets on load asks an origin the replay has no replica for.
PAGE = b"""<!DOCTYPE html><html><body><p id="msg">hello</p>
<script>setTimeout(() => fetch("http://127.0.0.1:9/late").catch(() => {}), 100);</script>
</body></html>"""


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


def test_a_refused_request_fails_the_replay_and_names_the_step_it_came_during(tmp_path):
    pytest.importorskip("playwright.sync_api")
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    (rec / "dom/0001.html").write_text(PAGE.decode(), "utf-8")
    step = {"seq": 1, "ts": "2026-01-01T00:00:00.000Z", "url": "https://shop.example/",
            "action": "goto", "nav": "navigate", "dom": "dom/0001.html"}  # fmt: skip
    (rec / "steps.jsonl").write_text(json.dumps(step) + "\n", "utf-8")
    server, base = serve()
    try:
        report = fidelity.replay(rec, origins={"https://shop.example": base})
    finally:
        server.shutdown()
    assert [s.status for s in report.steps] == ["pass"]
    assert all(not s.differences for s in report.steps)
    assert report.status == "fail"  # the replica depends on something outside the lab
    (refused,) = [r for r in report.blocked_requests if "/late" in r]
    assert "an origin with no replica; during step 1" in refused
    assert any("were refused (http://127.0.0.1:9)" in p for p in report.problems), report.problems
