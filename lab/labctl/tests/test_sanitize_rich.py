"""Page snapshots with frames, shadow roots and canvases stay readable after sanitizing, and keep
no values."""

from __future__ import annotations

from labctl import sanitize

PAGE = """<!DOCTYPE html><html><body>
<iframe id="inner" src="/inner.html"></iframe><tl-frame data-tl-frame="iframe#inner">
<label for="note">Note</label><input id="note" value="Customer called on Monday"></tl-frame>
<open-field id="amountField"><tl-shadow data-tl-mode="open"><input id="amount" value="1250"></tl-shadow></open-field>
<canvas id="chart" data-tl-canvas="c1"></canvas><tl-canvas-text data-tl-for="c1" data-tl-ocr="windows">Total due 1234 for Avery Holt</tl-canvas-text>
<embed id="contract" type="application/pdf" src="/contract.pdf?id=88412" data-tl-document="pdf">
</body></html>"""


def test_markers_kept_values_masked():
    info = sanitize.page(PAGE, known={"avery", "holt"})
    out = info.html
    assert 'data-tl-frame="iframe#inner"' in out and 'data-tl-mode="open"' in out
    assert 'data-tl-canvas="c1"' in out and 'data-tl-document="pdf"' in out
    assert {"note", "amount", "chart", "contract"} <= info.ids
    assert "Customer called" not in out and "1250" not in out
    assert "Avery" not in out and "88412" not in out
