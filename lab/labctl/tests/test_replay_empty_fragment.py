"""An empty fragment is no fragment (found replaying Demoblaze, whose links are `href="#"`).

The browser's address after such a link ends in `#`. The replay rebuilt each recorded address on
its replica with `urlunsplit`, which drops an empty fragment, and compared it exactly with the
browser's: every step after such a click failed with "expected .../, the page is at .../#".
An older recorder also wrote the address without the `#`. Both now compare as the same address;
a fragment that is not empty is still part of it.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from labctl.behaviour import fidelity
from labctl.behaviour.fidelity import OriginMap, _rebase, same_address


def test_an_empty_fragment_is_no_fragment_and_a_real_one_still_counts():
    assert same_address("http://x/#") == same_address("http://x/")
    assert same_address("http://x/cart.html?id=1#") == same_address("http://x/cart.html?id=1")
    assert same_address("http://x/#/orders") != same_address("http://x/")
    assert same_address("http://x/#top") != same_address("http://x/#")
    assert same_address("http://x/a;jsessionid=AB12#") == same_address("http://x/a;jsessionid=CD")


def test_the_replayed_address_keeps_the_recorded_empty_fragment():
    omap = OriginMap({"https://shop.example": "http://127.0.0.1:1"}, strict=True)
    assert omap.rebase("https://shop.example/#") == "http://127.0.0.1:1/#"
    assert omap.rebase("https://shop.example/") == "http://127.0.0.1:1/"
    assert omap.rebase("https://shop.example/p?x=1#a") == "http://127.0.0.1:1/p?x=1#a"
    assert _rebase("https://shop.example/cart.html#", "http://127.0.0.1:1") == (
        "http://127.0.0.1:1/cart.html#"
    )


PAGE = b"""<!DOCTYPE html><html><body>
<a id="cat" href="#" onclick="document.getElementById('out').className = 'shown'">Phones</a>
<p id="out" class="hidden">list</p>
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


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from labctl import recorder

    def flow(page):
        page.wait_for_timeout(500)
        page.click("#cat")
        page.wait_for_timeout(700)

    server, base = serve()
    out = tmp_path_factory.mktemp("fragment") / "rec"
    try:
        recorder.record(f"{base}/", out, headless=True, script=flow)
    finally:
        server.shutdown()
    return out, base


def _steps(rec):
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def test_the_recorder_writes_the_address_the_browser_showed(recorded):
    rec, base = recorded
    pops = [s for s in _steps(rec) if s["action"] == "goto" and s.get("nav") == "pop"]
    assert [s["url"] for s in pops] == [f"{base}/#"]


@pytest.mark.parametrize("older", [False, True], ids=["as-recorded", "older-recorder-no-hash"])
def test_a_link_to_hash_replays_with_no_wrong_address(recorded, tmp_path, older):
    import shutil

    rec, base = recorded
    copy = tmp_path / "rec"
    shutil.copytree(rec, copy)
    if older:  # an older recorder wrote the address without its empty fragment
        lines = [dict(s, url=s["url"][:-1] if s["url"].endswith("#") else s["url"])
                 for s in _steps(copy)]  # fmt: skip
        (copy / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in lines), "utf-8")
    server, fresh = serve()
    try:
        report = fidelity.replay(copy, origins={base: fresh}, resolve_timeout_ms=3000)
    finally:
        server.shutdown()
    diffs = [d.text() for s in report.steps for d in s.differences]
    assert report.status == "pass" and diffs == [], report.markdown()
    assert report.coverage.failed == 0
