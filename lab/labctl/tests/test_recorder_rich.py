"""Recording pages the plain outerHTML misses: iframes (same and cross origin), open and closed
shadow DOM, a canvas with drawn text, and an embedded PDF with a form field."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.sync_api")

from labctl import har, ocr, recorder  # noqa: E402
from labctl.behaviour import catalog, dom  # noqa: E402
from labctl.locate import locate  # noqa: E402


def pdf_bytes() -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Loan agreement summary", fontsize=14)
    widget = fitz.Widget()
    widget.field_name = "borrower_name"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(72, 100, 300, 120)
    page.add_widget(widget)
    return doc.tobytes()


def top_page(other: str) -> bytes:
    return f"""<!DOCTYPE html><html><body>
<h1 id="title">Review</h1>
<iframe id="inner" src="/inner.html" width="300" height="120"></iframe>
<iframe id="partner" src="{other}/partner.html" width="300" height="120"></iframe>
<open-field id="amountField"></open-field>
<closed-button id="approveBox"></closed-button>
<canvas id="chart" width="360" height="70"></canvas>
<embed id="contract" type="application/pdf" src="/contract.pdf" width="300" height="200">
<p id="status">waiting</p>
<script>
customElements.define("open-field", class extends HTMLElement {{
  constructor() {{ super(); const r = this.attachShadow({{mode: "open"}});
    r.innerHTML = '<label for="amount">Amount</label><input id="amount" name="amount">'; }}
}});
customElements.define("closed-button", class extends HTMLElement {{
  constructor() {{ super(); const r = this.attachShadow({{mode: "closed"}});
    r.innerHTML = '<button id="approve" style="width:120px;height:30px">Approve</button>';
    r.querySelector("button").addEventListener("click", () => {{
      document.getElementById("status").textContent = "approved"; }}); }}
}});
const c = document.getElementById("chart").getContext("2d");
c.fillStyle = "white"; c.fillRect(0, 0, 360, 70);
c.fillStyle = "black"; c.font = "32px Arial"; c.fillText("Total due 1234", 10, 45);
</script></body></html>""".encode()


INNER = b"""<!DOCTYPE html><html><body>
<input id="note" name="note"><button id="saveNote" onclick="document.getElementById('saved').hidden=false">Save note</button>
<p id="saved" hidden>Note saved</p></body></html>"""
PARTNER = b"""<!DOCTYPE html><html><body>
<button id="lookup" onclick="this.nextElementSibling.textContent='Partner found'">Look up</button><span id="result"></span>
</body></html>"""


def serve(routes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body, ctype = routes.get(self.path.split("?")[0], (b"", "text/plain"))
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def site():
    # Two origins: 127.0.0.1 and localhost on another port.
    partner = serve({"/partner.html": (PARTNER, "text/html")})
    other = f"http://localhost:{partner.server_address[1]}"
    main = serve({
        "/": (top_page(other), "text/html"),
        "/inner.html": (INNER, "text/html"),
        "/contract.pdf": (pdf_bytes(), "application/pdf"),
    })  # fmt: skip
    yield f"http://127.0.0.1:{main.server_address[1]}"
    main.shutdown()
    partner.shutdown()


def flow(page):
    page.wait_for_timeout(500)
    locate(page, "iframe#inner >>> input#note").fill("Checked")
    locate(page, "iframe#inner >>> button#saveNote").click()
    page.wait_for_timeout(600)
    page.locator("input#amount").fill("1250")  # inside an open shadow root
    page.wait_for_timeout(600)
    locate(page, "iframe#partner >>> button#lookup").click()  # another origin
    page.wait_for_timeout(600)
    box = page.locator("#approveBox").bounding_box()  # a closed root: only by position
    page.mouse.click(box["x"] + 20, box["y"] + 10)
    page.wait_for_timeout(600)


def steps_of(out):
    return [json.loads(line) for line in (out / "steps.jsonl").read_text().splitlines()]


def test_frames_shadow_canvas_and_pdf_are_recorded(site, tmp_path):
    out = recorder.record(f"{site}/", tmp_path / "rec", script=flow, headless=True)
    steps = steps_of(out)
    selectors = [s["selector"] for s in steps if s["action"] != "goto"]

    # actions inside frames carry the frame, across origins too
    assert "iframe#inner >>> button#saveNote" in selectors
    assert "iframe#inner >>> input#note" in selectors
    assert "iframe#partner >>> button#lookup" in selectors
    inner = next(s for s in steps if s["selector"] == "iframe#inner >>> button#saveNote")
    assert inner["frame"] == "iframe#inner" and inner["before_scope"] == "frame"
    # a change inside an open shadow root, and a click inside a closed one, are recorded
    assert "input#amount" in selectors and "button#approve" in selectors

    last = (out / steps[-1]["dom"]).read_text(encoding="utf-8")
    snap = dom.snapshot(last)
    # frame documents are in the snapshot, the cross-origin one filled in from outside
    assert '<tl-frame data-tl-frame="iframe#inner">' in last and "#saveNote" in snap.elements
    assert '<tl-frame data-tl-frame="iframe#partner">' in last and "#lookup" in snap.elements
    assert "Partner found" in last and "data-tl-pending" not in last
    assert not any(s.get("frames_unread") for s in steps)
    # open and closed shadow roots are in the snapshot
    assert '<tl-shadow data-tl-mode="open">' in last and "#amount" in snap.elements
    assert '<tl-shadow data-tl-mode="closed">' in last and "#approve" in snap.elements
    assert "approved" in last
    # the canvas has its picture, and its text when an OCR engine is there
    assert 'data-tl-canvas="c1"' in last and "<tl-canvas-text" in last
    if ocr.available():
        assert "Total due" in last
    # the PDF: kept, with its text and form field names (never field values)
    docs = json.loads((out / "docs" / "documents.json").read_text())
    assert docs[0]["fields"] == [{"name": "borrower_name", "type": "Tx"}]
    assert "Loan agreement" in (out / docs[0]["file"].replace(".pdf", ".txt")).read_text()
    assert 'data-tl-document="pdf"' in last
    # every step has its own picture, also in a scripted run
    assert all((out / s["screenshot"]).exists() for s in steps if s["dom"])
    # the readers of a recording still work, and the model has the document with its field
    assert har.load(out).steps
    assert catalog.build([out], "rich") is not None
    from labctl import model_build

    model = model_build.from_recording("rich", out, tmp_path, None)
    doc = next(s for s in model.screens if s.name.startswith("document /contract"))
    assert [e.label for e in doc.elements] == ["borrower_name"]
    assert any("PDF document" in p for p in model.problems)


def test_fidelity_replay_goes_into_frames_and_shadow(site, tmp_path):
    from labctl.behaviour import fidelity

    out = recorder.record(f"{site}/", tmp_path / "rec", script=flow, headless=True)
    report = fidelity.replay(
        out, site, {"iframe#inner >>> input#note": "Checked", "input#amount": "1250"}
    )
    acted = {s.selector: s.status for s in report.steps}
    assert acted["iframe#partner >>> button#lookup"] != "fail", report.markdown()
    assert acted["iframe#inner >>> button#saveNote"] != "fail", report.markdown()
