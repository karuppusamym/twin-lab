"""A record locator finds its rows in the page it was recorded on, also when the identified
element is the list itself (`<div id="cards" class="row">` holding the cards, `<tbody id="t">`
holding the rows), as a public shop's product grid and cart are built."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.sync_api")

from labctl import recorder  # noqa: E402

PAGE = b"""<!DOCTYPE html><html><body><div class="container"><div class="col-lg-9">
<div class="row" id="cards">
 <div class="col-lg-4 col-md-6"><div class="card"><h4><a href="#a" class="title">Amber lamp s6</a></h4>
  <h5>$10</h5></div></div>
 <div class="col-lg-4 col-md-6"><div class="card"><h4><a href="#b" class="title">Birch stool 1520</a></h4>
  <h5>$20</h5></div></div>
</div></div></div>
<table><tbody id="lines">
 <tr class="success"><td>Amber lamp</td><td>10</td><td><a href="#x" onclick="return false">Delete</a></td></tr>
 <tr class="success"><td>Birch stool</td><td>20</td><td><a href="#y" onclick="return false">Delete</a></td></tr>
</tbody></table></body></html>"""


@pytest.fixture
def site():
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
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_every_record_locator_matches_rows_of_its_own_page(site, tmp_path):
    def flow(page):
        page.wait_for_timeout(400)
        page.click("#cards .col-lg-4:nth-child(2) a.title")
        page.wait_for_timeout(500)
        page.click("#lines tr:nth-child(1) a")
        page.wait_for_timeout(500)

    recorder.record(site + "/", tmp_path, headless=True, script=flow)
    steps = [json.loads(x) for x in (tmp_path / "steps.jsonl").read_text("utf-8").splitlines()]
    records = [(s, c) for s in steps for c in s.get("locators") or [] if c["kind"] == "record"]
    assert len(records) == 2, steps
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            for step, rec in records:
                html = (tmp_path / (step.get("dom_before") or step["dom"])).read_text("utf-8")
                page.set_content(html)
                found = page.evaluate("s => document.querySelectorAll(s).length", rec["rows"])
                assert found >= 2, rec
                assert rec["count"] == 1, rec
        finally:
            browser.close()
