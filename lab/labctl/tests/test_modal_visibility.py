"""Bootstrap keeps every modal in the page: a modal is open only when its markup says so (`show`,
`display: block`, `aria-modal`), its title is its own, the controls of a carousel inside a
`<nav>` are the carousel's, and a closed Log in modal makes no screen a sign-in screen."""

from __future__ import annotations

from labctl import linkcheck, navmap
from labctl.behaviour import dom as domlib

PAGE = """<html><body class="{body}">
<nav id="narvbarx"><a class="nav-link" href="index.html">Home</a>
<div id="contcar"><div id="carouselExampleIndicators" class="carousel slide" data-ride="carousel">
<a class="carousel-control-next" href="#carouselExampleIndicators" role="button">Next</a></div></div>
</nav>
<div class="modal fade{log}" id="logInModal" role="dialog" aria-labelledby="logInModalLabel"{logstyle}>
<h5 class="modal-title" id="logInModalLabel">Log in</h5>
<input type="password" id="loginpassword"></div>
<div class="modal fade" id="videoModal" role="dialog" aria-labelledby="logInModalLabel" aria-hidden="true">
<h5 class="modal-title" id="videoModalLabel">About us</h5>
<div class="video-js"><div class="vjs-modal-dialog vjs-hidden" role="dialog">x</div></div></div>
</body></html>"""


def _tree(open_login: bool):
    return domlib.parse_html(PAGE.format(
        body="modal-open" if open_login else "", log=" show" if open_login else "",
        logstyle=' style="display: block;" aria-modal="true"' if open_login else ' aria-hidden="true"'))  # fmt: skip


def test_a_modal_is_open_only_when_shown():
    closed = navmap._subview_states(_tree(False))
    assert closed == {"div#logInModal": ("dialog", False), "div#videoModal": ("dialog", False)}
    opened = navmap._subview_states(_tree(True))
    assert opened["div#logInModal"] == ("dialog", True)
    assert opened["div#videoModal"] == ("dialog", False)


def test_a_dialog_has_its_own_title():
    tree = _tree(False)
    video = next(n for n in domlib._walk(tree) if n.id == "videoModal")
    assert navmap._view_title(video, tree) == "About us"


def test_carousel_controls_are_not_in_the_menu():
    tree = _tree(False)
    nxt = next(n for n in domlib._walk(tree) if "carousel-control-next" in
               (n.attrs.get("class") or ""))  # fmt: skip
    home = next(
        n for n in domlib._walk(tree) if n.tag == "a" and n.attrs.get("href") == "index.html"
    )
    assert navmap._inside(nxt) == ["carousel `div#carouselExampleIndicators`"]
    assert navmap._inside(home) == ["menu `nav#narvbarx`"]


def test_sign_in_screen_needs_a_password_field_shown():
    assert not linkcheck._has_password(_tree(False))
    assert linkcheck._has_password(_tree(True))
