"""The visual draft leaves out data the package masks and the page itself carries, text included
or not, and its text-region exclusion is an explicit option.

Demoblaze round 3: with the fonts carried, every text compared pixel for pixel except the three
footer contact lines (address, phone, e-mail), which the package masks by design and the build
fills with its own synthetic values. The cart's rows and total, filled in by the page's scripts
from the API (the seed), stay compared.
"""

from __future__ import annotations

import json

import yaml

from labctl.behaviour import drafts
from labctl.botlog.recording import load

FOOTER = ('<div id="fotcont"><div class="col"><div class="thumbnail"><div class="caption">'
          "<h4><b>Get in Touch</b></h4><p>Address: 2390 El Camino Real</p>"
          "<p>Phone: +440 123456</p><p>Email: demo@example.com </p></div></div></div></div>")  # fmt: skip
SERVED = ("<html><body><nav><a id=\"home\" href=\"/\">Home</a></nav>"
          '<table><tbody id="tbodyid"></tbody></table><h3 id="totalp"></h3>'
          f"{FOOTER}</body></html>")  # fmt: skip
SHOWN = ('<html><body><nav><a id="home" href="/">Home</a></nav><table><tbody id="tbodyid">'
         '<tr class="success"><td>Samsung galaxy s6</td><td>360</td></tr></tbody></table>'
         f'<h3 id="totalp">360</h3>{FOOTER}</body></html>')  # fmt: skip
TEXTS = {"screens": {"cart.html": [
    {"where": "a#home", "kind": "text", "text": "Home", "is": "literal"},
    {"where": "td", "kind": "text", "text": "Xxxxxxx xxxxxx x9", "is": "data"},
    {"where": "td", "kind": "text", "text": "999", "is": "data"},
    {"where": "h3#totalp", "kind": "text", "text": "999", "is": "data"},
    {"where": "h4", "kind": "text", "text": "Get in Touch", "is": "literal"},
    {"where": "p", "kind": "text", "text": "Address: 9999 Xx Xxxxxx Xxxx", "is": "mixed"},
    {"where": "p", "kind": "text", "text": "Phone: +999 999999", "is": "mixed"},
    {"where": "p", "kind": "text", "text": "Email: xxxx@xxxxxxx.xxx", "is": "mixed"},
]}}  # fmt: skip
FOOTER_CSS = {f"#fotcont > div:nth-child(1) > div:nth-child(1) > div:nth-child(1) > p:nth-child({n})"
              for n in (2, 3, 4)}  # fmt: skip


def _setup(tmp_path, fonts: bool):
    root = tmp_path / "root"
    look = root / "apps" / "shop" / "look"
    look.mkdir(parents=True)
    (look / "texts.json").write_text(json.dumps(TEXTS), "utf-8")
    if fonts:
        (look / "fonts").mkdir()
        (look / "fonts" / "Lato-Regular.woff").write_bytes(b"wOFF")
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    (rec / "screens").mkdir()
    (rec / "dom/0002.html").write_text(SHOWN, "utf-8")
    (rec / "screens/0002.png").write_bytes(b"")
    steps = [{"seq": 1, "ts": "2026-01-01T00:00:00.000Z", "url": "about:blank", "action": "goto"},
             {"seq": 2, "ts": "2026-01-01T00:00:01.000Z", "url": "http://x/cart.html",
              "action": "goto", "nav": "navigate", "dom": "dom/0002.html",
              "screenshot": "screens/0002.png"}]  # fmt: skip
    (rec / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), "utf-8")
    har = {"log": {"entries": [{"request": {"url": "http://x/cart.html"}, "response": {
        "status": 200, "content": {"mimeType": "text/html", "text": SERVED}}}]}}  # fmt: skip
    (rec / "recording.har").write_text(json.dumps(har), "utf-8")
    return root, rec


def _states(rec):
    return yaml.safe_load((rec / drafts.VISUAL).read_text("utf-8"))["states"]


def test_footer_data_is_excluded_even_with_text_compared(tmp_path, monkeypatch):
    monkeypatch.delenv(drafts.TEXT_OPTION, raising=False)
    root, rec = _setup(tmp_path, fonts=True)
    drafts.write_all(root, rec, ["shop"])
    (st,) = _states(rec)
    css = {e["css"]: e.get("reason", "") for e in st["exclude"]}
    assert drafts.TEXT_REGIONS["css"] not in css  # fonts carried: text compared by default
    assert set(css) == FOOTER_CSS, css  # the cart's rows and total, and the heading: compared
    assert all("masks" in r and "own synthetic values" in r for r in css.values())
    text = (rec / drafts.VISUAL).read_text("utf-8")
    assert "Text regions: compared (the package carries the fonts" in text
    assert "2390" not in text and "example.com" not in text  # no recorded value in the draft


def test_without_fonts_text_regions_are_excluded_and_the_footer_still_named(tmp_path, monkeypatch):
    monkeypatch.delenv(drafts.TEXT_OPTION, raising=False)
    root, rec = _setup(tmp_path, fonts=False)
    drafts.write_all(root, rec, ["shop"])
    (st,) = _states(rec)
    css = [e["css"] for e in st["exclude"]]
    assert css[0] == drafts.TEXT_REGIONS["css"] and set(css[1:]) == FOOTER_CSS
    assert "Text regions: excluded (the package carries no font" in (rec / drafts.VISUAL).read_text(
        "utf-8"
    )


def test_the_text_region_option_is_explicit(tmp_path, monkeypatch):
    root, rec = _setup(tmp_path, fonts=True)
    monkeypatch.setenv(drafts.TEXT_OPTION, "exclude")
    assert drafts.text_regions_default(fonts_carried=True) is True
    monkeypatch.setenv(drafts.TEXT_OPTION, "compare")
    assert drafts.text_regions_default(fonts_carried=False) is False
    monkeypatch.delenv(drafts.TEXT_OPTION)
    assert drafts.text_regions_default(True) is False and drafts.text_regions_default(False)
    drafts.write_all(root, rec, ["shop"], text_regions=True)
    (st,) = _states(rec)
    assert st["exclude"][0] == drafts.TEXT_REGIONS


def test_masked_shapes_fit_their_values():
    assert drafts._fits_masked("Address: 9999 Xx Xxxxxx Xxxx", "Address: 2390 El Camino Real")
    assert drafts._fits_masked("Email: xxxx@xxxxxxx.xxx", "Email: demo@example.com")
    assert drafts._fits_masked("Total: 999", "Total: 1180")
    assert not drafts._fits_masked("Phone: +999 999999", "Address: +440 123456")
    assert not drafts._fits_masked("Total: 999", "Total: due")


def test_without_a_har_nothing_is_excluded_as_masked(tmp_path):
    root, rec = _setup(tmp_path, fonts=True)
    (rec / "recording.har").unlink()
    masked = drafts.masked_texts(root, ["shop"])
    assert drafts.masked_regions(load(rec), masked) == {}
