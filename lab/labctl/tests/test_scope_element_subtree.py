"""An element exclusion covers the element and what is inside it (found replaying Demoblaze: the
approved `element: div.video-js` excluded 0 differences, because a video.js player's parts are
named by their own ids, `#example-video_html5_api`, and the matcher looked only at the element
named).
"""

from __future__ import annotations

from labctl.behaviour import scope
from labctl.behaviour.fidelity import compare_dom

RECORDED = """<html><body>
<a id="home" href="/">Home</a>
<div id="videoModal" class="modal">
  <div id="example-video" class="video-js vjs-paused">
    <video id="example-video_html5_api" class="vjs-tech"></video>
    <div class="vjs-control-bar">
      <button id="example-video_play" class="vjs-play-control" type="button">Play</button>
      <a class="nav-link" href="#">Help</a>
      <select id="captions-example-video_component_999"><option>White</option></select>
    </div>
  </div>
  <button id="close" type="button">Close</button>
</div>
<a class="nav-link" href="#">About</a>
</body></html>"""

# The replica's stand-in player: none of the player's parts, the link outside it gone too.
LIVE = """<html><body>
<a id="home" href="/">Home</a>
<div id="videoModal" class="modal">
  <div id="example-video" class="video-js"><video id="stand-in"></video></div>
  <button id="close" type="button">Close</button>
</div>
</body></html>"""


def _scope():
    sc = scope.Scope(approved_by="judge")
    sc.exclusions = [scope.Exclusion(id="player", element="div.video-js", reason="stand-in")]
    return sc


def test_the_widget_and_everything_inside_it_is_named():
    names = scope.subtree_names(RECORDED, "div.video-js")
    assert {"#example-video", "#example-video_html5_api", "#example-video_play",
            "#captions-example-video_component_999",
            "button#example-video_play.vjs-play-control"} <= names  # fmt: skip
    assert "#home" not in names and "#close" not in names
    # a signature an element outside the widget has too is not the widget's alone
    assert "a.nav-link" not in names


def test_differences_inside_the_widget_are_excluded_and_the_rest_still_fail():
    sc = _scope()
    diffs = compare_dom(RECORDED, LIVE, step=3)
    names = scope.resolve(sc, RECORDED, LIVE)
    excluded = [d for d in diffs if scope.which(sc, d.element, "http://x/", names)]
    kept = [d for d in diffs if not scope.which(sc, d.element, "http://x/", names)]
    assert {d.element for d in excluded} >= {"button#example-video_play.vjs-play-control",
                                             "select#captions-example-video_component_999"}  # fmt: skip
    # the link outside the widget is missing in the replica: still a difference
    assert [d.element for d in kept] == ["a.nav-link"], [d.text() for d in kept]
    # without the recorded snapshot, only the element's own name matches (as before)
    assert scope.which(sc, "#example-video_play", "http://x/") is None
    assert scope.which(sc, "div#example-video.video-js", "http://x/").id == "player"
