"""Behaviour catalog from the recorder's own recordings: an input mask seen from the shape of
what was typed (the value is never recorded), and a click+submit step compared with the page
the click met."""

from __future__ import annotations

import json

from labctl.behaviour import catalog

PAGE = """<!DOCTYPE html><html><body><form id="searchForm">
<input id="caseNo" name="caseNo" value="{value}"><button id="btnSearch" {disabled}>Search</button>
</form>{extra}</body></html>"""


def write(folder, steps, pages):
    (folder / "dom").mkdir(parents=True)
    for name, html in pages.items():
        (folder / "dom" / name).write_text(html, encoding="utf-8")
    (folder / "steps.jsonl").write_text("\n".join(json.dumps(s) for s in steps) + "\n")
    (folder / "recording.har").write_text(json.dumps({"log": {"entries": []}}))


def step(seq, action, selector, dom, ts, **extra):
    return {"seq": seq, "ts": f"2026-11-16T10:00:0{ts}.000Z", "url": "http://app/cases",
            "action": action, "selector": selector, "dom": dom, **extra}  # fmt: skip


def test_mask_found_from_the_typed_shape(tmp_path):
    rec = tmp_path / "rec1"
    write(
        rec,
        [
            step(1, "goto", None, "dom/1.html", 1),
            step(2, "type", "input#caseNo", "dom/2.html", 2, typed_shape="A9"),
        ],  # fmt: skip
        {
            "1.html": PAGE.format(value="", disabled="", extra=""),
            "2.html": PAGE.format(value="C-1003", disabled="", extra=""),
        },  # fmt: skip
    )
    lines = catalog.brief_lines(catalog.build([rec], "demo"))
    assert any("#caseNo is typed into, the value is formatted as A-9" in line for line in lines)


def test_same_shape_is_no_mask(tmp_path):
    rec = tmp_path / "rec2"
    write(
        rec,
        [
            step(1, "goto", None, "dom/1.html", 1),
            step(2, "type", "input#caseNo", "dom/2.html", 2, typed_shape="A-9"),
        ],  # fmt: skip
        {
            "1.html": PAGE.format(value="", disabled="", extra=""),
            "2.html": PAGE.format(value="C-1003", disabled="", extra=""),
        },  # fmt: skip
    )
    assert not any("formatted" in line for line in catalog.brief_lines(catalog.build([rec], "d")))


def test_click_and_submit_use_the_page_the_click_met(tmp_path):
    rec = tmp_path / "rec3"
    write(
        rec,
        [
            step(1, "goto", None, "dom/1.html", 1),
            step(2, "click", "button#btnSearch", None, 2, dom_before="dom/2.before.html"),
            step(3, "submit", "form#searchForm", "dom/3.html", 2, dom_before="dom/3.before.html"),
        ],  # fmt: skip
        {
            "1.html": PAGE.format(value="", disabled="", extra=""),
            "2.before.html": PAGE.format(value="C-1003", disabled="", extra=""),
            "3.before.html": PAGE.format(value="C-1003", disabled="", extra=""),
            "3.html": PAGE.format(
                value="C-1003", disabled="disabled", extra='<div id="loading">Searching</div>'
            ),
        },  # fmt: skip
    )
    lines = catalog.brief_lines(catalog.build([rec], "demo"))
    assert any("#loading appears" in line for line in lines)
    assert any("#btnSearch is disabled" in line for line in lines)
