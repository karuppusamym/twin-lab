"""The recorder on what JSF and PrimeFaces pages do after an action: a request PrimeFaces still
holds in its ajax queue, a dialog-framework page loading in a new iframe, and a click on a row of
a PrimeFaces data table (whose `<tbody>` carries the table's id). The settle rule waits for the
first two (read-only looks at the page: nothing of its code is run), and the row is named by a
record locator that finds it again."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.sync_api")

from labctl import recorder  # noqa: E402

PAGE = b"""<!DOCTYPE html><html><body>
<h1>Queue</h1>
<button id="queued" onclick="queue()">Queued request</button>
<button id="framed" onclick="frame()">Open picker</button>
<table id="grid"><tbody id="grid_data">
<tr data-ri="0" class="ui-widget-content ui-datatable-even"><td>Alder Row</td><td>Open</td></tr>
<tr data-ri="1" class="ui-widget-content ui-datatable-odd"><td>Birch Row</td><td>Open</td></tr>
<tr data-ri="2" class="ui-widget-content ui-datatable-even"><td>Cedar Row</td><td>Open</td></tr>
</tbody></table>
<p id="picked"></p>
<script>
// A stand-in for PrimeFaces: a data table's filter waits out its delay (`filterTimeout`, 1.1 s
// here) before it sends its request, then a request is queued: no XHR in flight meanwhile.
function queue() {
  const pf = window.PrimeFaces = {ajax: {Queue: {requests: [], xhrs: []}},
                                  widgets: {casesTable: {filterTimeout: 7}}};
  setTimeout(() => { pf.widgets.casesTable.filterTimeout = null; pf.ajax.Queue.requests.push({}); }, 700);
  setTimeout(() => { const p = document.createElement("p"); p.id = "late";
    p.textContent = "answered"; document.body.appendChild(p); pf.ajax.Queue.requests = []; }, 1100);
}
function frame() {
  const f = document.createElement("iframe");
  f.id = "dlgFrame"; f.src = "/slow.html"; document.body.appendChild(f);
}
document.getElementById("grid_data").addEventListener("click", (e) => {
  const tr = e.target.closest("tr"); document.getElementById("picked").textContent = "picked";
  tr.setAttribute("aria-selected", "true");
});
</script></body></html>"""
SLOW = b"""<!DOCTYPE html><html><body><h1 id="pickerTitle">Pick an owner</h1></body></html>"""


@pytest.fixture
def site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/slow.html"):
                time.sleep(1.0)  # a slow server: longer than the settle time
                body = SLOW
            else:
                body = PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_the_snapshot_waits_for_the_ajax_queue_and_a_loading_frame(site, tmp_path):
    def flow(page):
        page.wait_for_timeout(600)
        page.click("#queued")
        page.wait_for_selector("#late")
        page.wait_for_timeout(900)
        page.click("#framed")
        page.frame_locator("#dlgFrame").locator("#pickerTitle").wait_for()
        page.wait_for_timeout(900)
        page.click("#grid_data tr[data-ri='1'] td:nth-child(2)")
        page.wait_for_timeout(900)

    recorder.record(site + "/", tmp_path, headless=True, script=flow)
    steps = [json.loads(x) for x in (tmp_path / "steps.jsonl").read_text("utf-8").splitlines()]
    clicks = [s for s in steps if s["action"] == "click"]
    assert [s["selector"] for s in clicks][:2] == ["button#queued", "button#framed"]
    queued, framed, row = clicks
    # PrimeFaces still held the request back: the snapshot was taken once it was answered
    assert 'id="late"' in (tmp_path / queued["dom"]).read_text("utf-8")
    # the dialog-framework page loaded after the click's own work was done: it is in the snapshot
    html = (tmp_path / framed["dom"]).read_text("utf-8")
    assert "<tl-frame" in html and 'id="pickerTitle"' in html
    # a PrimeFaces row: the record's list is the identified <tbody> itself, the striping classes
    # (even, odd) do not narrow it to every other row, and the cell is named by its place
    rec = row["locators"][0]
    assert rec["kind"] == "record", row["locators"]
    assert rec["rows"] == "#grid_data > tr.ui-widget-content", rec
    assert rec["control"] == "td:nth-child(2)" and rec["count"] == 1, rec
