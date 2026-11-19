"""The visual draft leaves out regions that change by themselves (found replaying Demoblaze: its
home page's Bootstrap carousel rides, so the pictures after steps 6, 8 and 38 showed another
slide than the replica's and failed by 19 to 21 %).

Detected from the recorded snapshots: a carousel with `data-ride="carousel"` or
`data-bs-ride`, a video element, a CSS animation written on an element, and an element whose
content changed between two snapshots of the same screen with no action between. Each is
excluded in every drafted state, with the reason; nothing else is.
"""

from __future__ import annotations

import json

import yaml

from labctl.behaviour import drafts, visual
from labctl.botlog.recording import load

HOME = """<html><body>
<div id="nav"><a id="home" href="/">Home</a><a id="login" href="#">Log in</a>
<h5 id="dup">Log in</h5><h5 id="dup">About</h5></div>
<div id="hero" class="carousel slide" data-ride="carousel">
  <div class="carousel-inner"><div class="carousel-item {a}">1</div><div class="carousel-item {b}">2</div></div>
</div>
<div class="carousel" data-bs-ride="true"><div class="carousel-item active">x</div></div>
<video id="clip"></video>
<span class="pulse" style="color: red; animation: pulse 2s infinite">!</span>
<p id="clock">{clock}</p>
<p id="still">Welcome</p>
</body></html>"""


def _recording(tmp_path):
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    (rec / "screens").mkdir()
    pages = {2: HOME.format(a="active", b="", clock="10:00"),
             3: HOME.format(a="", b="active", clock="10:01"),
             4: HOME.format(a="active", b="", clock="10:02")}  # fmt: skip
    steps = [
        {"seq": 1, "ts": "2026-01-01T00:00:00.000Z", "url": "about:blank", "action": "goto"},
        {"seq": 2, "ts": "2026-01-01T00:00:01.000Z", "url": "http://x/", "action": "goto",
         "nav": "navigate", "dom": "dom/0002.html", "screenshot": "screens/0002.png"},
        # an alert the page showed by itself: no action of the person's between 2 and 3
        {"seq": 3, "ts": "2026-01-01T00:00:03.000Z", "url": "http://x/", "action": "dialog",
         "dialog": {"kind": "alert", "choice": "accept"}, "dom": "dom/0003.html",
         "screenshot": "screens/0003.png"},
        {"seq": 4, "ts": "2026-01-01T00:00:05.000Z", "url": "http://x/", "action": "click",
         "selector": "a#login", "dom": "dom/0004.html", "screenshot": "screens/0004.png"},
    ]  # fmt: skip
    for seq, html in pages.items():
        (rec / f"dom/{seq:04d}.html").write_text(html, "utf-8")
        (rec / f"screens/{seq:04d}.png").write_bytes(b"")
    (rec / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), "utf-8")
    return load(rec)


def test_regions_that_change_by_themselves_are_found_with_the_reason(tmp_path):
    found = {m["css"]: m["reason"] for m in visual.self_changing(_recording(tmp_path))}
    assert "data-ride=carousel" in found["#hero .carousel-inner"]
    assert "data-bs-ride" in found[".carousel"]
    assert "video" in found["video"]
    assert "CSS animation" in found["span.pulse"]
    assert "after step 2 and step 3" in found["#clock"]
    # the carousel's own slide change is the carousel's; a page-wide id used twice pairs nothing;
    # what did not change, and the click's step 4, are not excluded
    assert set(found) == {"#hero .carousel-inner", ".carousel", "video", "span.pulse", "#clock"}


def test_the_visual_draft_excludes_them_in_every_state_and_says_why(tmp_path):
    rec = _recording(tmp_path)
    text = drafts.visual_draft(rec, fonts_carried=False)
    assert "#   #hero .carousel-inner: a carousel that advances by itself" in text
    states = yaml.safe_load(text)["states"]
    assert [s["id"] for s in states] == ["after-step-2", "after-step-3", "after-step-4"]
    for st in states:
        css = [e["css"] for e in st["exclude"]]
        assert css[0] == drafts.TEXT_REGIONS["css"]  # the text regions, as before
        assert set(css[1:]) == {"#hero .carousel-inner", ".carousel", "video", "span.pulse",
                                "#clock"}  # fmt: skip
        assert all(e.get("reason") for e in st["exclude"][1:])
    # with the fonts carried the text is compared; the self-changing regions are still left out
    carried = yaml.safe_load(drafts.visual_draft(rec, fonts_carried=True))["states"]
    assert len(carried[0]["exclude"]) == 5


def test_a_page_with_nothing_moving_gets_no_exclusion(tmp_path):
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    (rec / "screens").mkdir()
    (rec / "dom/0002.html").write_text("<html><body><p id='a'>x</p></body></html>", "utf-8")
    (rec / "screens/0002.png").write_bytes(b"")
    (rec / "steps.jsonl").write_text(json.dumps({"seq": 2, "url": "http://x/", "action": "goto",
                                                 "dom": "dom/0002.html",
                                                 "screenshot": "screens/0002.png"}) + "\n",
                                     "utf-8")  # fmt: skip
    loaded = load(rec)
    assert visual.self_changing(loaded) == []
    assert yaml.safe_load(visual.template(loaded))["states"][0]["exclude"] == []
