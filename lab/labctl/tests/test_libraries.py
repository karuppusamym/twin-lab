"""Third-party libraries and dialogs in the code map (Demoblaze trial):

1. Bootstrap was reported as 5.0.0 from a guess, while its own banner says v4.0.0-alpha.6: the
   version is read from the file's content first, the address only as a marked guess.
2. Bootstrap was listed twice (its style sheet and its script): one entry per library and
   version, listing its files.
3. `dialogs` held the same modal once per page and a video player's own dialogs: each dialog is
   listed once with its routes, and a third-party widget's dialogs are left out.

The page rules name libraries with the same function (`labctl.libraries.identify`).
"""

from __future__ import annotations

import pytest

from labctl import codemap, libraries
from labctl import pagerules as pr

HOST = "shop.example.test"

BOOTSTRAP_CSS = (
    "/*!\n * Bootstrap v4.0.0-alpha.6 (https://getbootstrap.com)\n * Copyright 2011-2017 The "
    "Bootstrap Authors\n * Licensed under MIT\n */html{font-family:sans-serif}"
    "/*! normalize.css v5.0.0 | MIT License */"
)
BOOTSTRAP_JS = (
    "/*!\n * Bootstrap v4.0.0-alpha.6 (https://getbootstrap.com)\n */\n"
    "!function(){var VERSION='4.0.0-alpha.6';}();"
)


def _res(path: str, text: str, host: str = HOST, query: str = "") -> codemap.Res:
    mime = {"js": "text/javascript", "css": "text/css"}.get(path.rsplit(".", 1)[-1], "text/html")
    return codemap.Res(f"https://{host}{path}", host, path, query, mime,
                       codemap.kind_of(path, mime), text, len(text), host == HOST)  # fmt: skip


def _doc(*res: codemap.Res) -> dict:
    return codemap.document(codemap.Analysis(list(res), {HOST}), "demo", screens=[])


# --- 1. the version the file states ---------------------------------------------------------------


@pytest.mark.parametrize(("text", "name", "version"), [
    (BOOTSTRAP_CSS, "Bootstrap", "4.0.0-alpha.6"),  # not normalize.css's 5.0.0 further down
    ("/*! jQuery v3.2.1 | (c) JS Foundation */\n!function(){}", "jQuery", "3.2.1"),
    ("/**\n * @license\n * Video.js 7.6.5 <http://videojs.com/>\n */", "video.js", "7.6.5"),
    ("/**\n * A widget\n * @version 2.3.4\n */\nvar a;", "widget", "2.3.4"),
    ("/*! tiny v1.2.3 */\nvar a;", "tiny", "1.2.3"),
    ("!function(){var VERSION=\"4.6.2\"}()", "Bootstrap", "4.6.2"),
    ("!function(){var a=1}()", "Bootstrap", None),
])  # fmt: skip
def test_the_version_is_read_from_the_files_own_content(text, name, version):
    assert libraries.version_in_text(text, name) == version


def test_the_content_wins_over_the_address_and_the_address_is_marked_as_a_guess():
    lib = libraries.identify("/lib/bootstrap-5.0.0.min.css", BOOTSTRAP_CSS)
    assert (lib.name, lib.version, lib.version_from) == (
        "Bootstrap", "4.0.0-alpha.6", libraries.CONTENT)  # fmt: skip
    guess = libraries.identify("/js/jquery-3.7.1.min.js", "!function(){}")
    assert (guess.name, guess.version, guess.version_from) == (
        "jQuery", "3.7.1", libraries.ADDRESS)  # fmt: skip
    none = libraries.identify("/node_modules/tether/dist/js/tether.min.js", "!function(){}")
    assert (none.name, none.version, none.version_from) == ("tether", None, None)


def test_the_code_map_reports_the_stated_version():
    doc = _doc(_res("/node_modules/bootstrap/dist/css/bootstrap.min.css", BOOTSTRAP_CSS))
    (lib,) = doc["third_party"]
    assert lib["name"] == "Bootstrap" and lib["version"] == "4.0.0-alpha.6"
    assert lib["version_from"] == libraries.CONTENT
    (guess,) = _doc(_res("/js/jquery-3.7.1.min.js", "!function(){}"))["third_party"]
    assert guess["version"] == "3.7.1" and guess["version_from"] == libraries.ADDRESS


# --- 2. one entry per library and version ----------------------------------------------------------


def test_a_librarys_style_sheet_and_script_are_one_entry():
    doc = _doc(
        _res("/node_modules/bootstrap/dist/css/bootstrap.min.css", BOOTSTRAP_CSS),
        _res("/node_modules/bootstrap/dist/js/bootstrap.min.js", BOOTSTRAP_JS),
        _res("/node_modules/video.js/dist/video-js.min.css", ".video-js{}"),
        _res("/node_modules/video.js/dist/video.min.js", "/**\n * Video.js 7.6.5\n */var a;"),
    )
    by = {x["name"]: x for x in doc["third_party"]}
    assert list(by) == ["Bootstrap", "video.js"]
    assert by["Bootstrap"]["files"] == ["/node_modules/bootstrap/dist/css/bootstrap.min.css",
                                        "/node_modules/bootstrap/dist/js/bootstrap.min.js"]  # fmt: skip
    # the style sheet states no version: it joins the one version its library has
    assert by["video.js"]["version"] == "7.6.5" and len(by["video.js"]["files"]) == 2


def test_two_versions_of_a_library_stay_two_entries():
    rows = libraries.group([
        {"name": "jQuery", "version": "3.2.1", "files": ["a.js"]},
        {"name": "jQuery", "version": "1.12.4", "files": ["b.js"]},
        {"name": "jQuery", "version": None, "files": ["c.js"]},
    ])  # fmt: skip
    assert [(r["version"], r["files"]) for r in rows] == [
        ("3.2.1", ["a.js"]), ("1.12.4", ["b.js"]), (None, ["c.js"])]  # fmt: skip


def test_the_page_rules_name_a_library_as_the_code_map_does():
    for path, text in [
        ("/node_modules/bootstrap/dist/js/bootstrap.min.js", BOOTSTRAP_JS),
        ("/node_modules/bootstrap-sweetalert/dist/sweetalert.min.js", "!function(){}"),
        ("/node_modules/video.js/dist/video.min.js", "/**\n * Video.js 7.6.5\n */"),
        ("/js/index.js", "var a = 1;"),
    ]:
        lib = codemap.library_of(_res(path, text))
        assert pr.library_name(path, text) == (lib[0] if lib else None), path
    assert pr.library_name("/x.js", "/*! Select2 4.1.0 */") == "Select2"
    assert libraries.identify("/x.js", "/*! Select2 4.1.0 */").version == "4.1.0"


# --- 3. dialogs -------------------------------------------------------------------------------------

PAGE = """<html><head><title>Shop</title></head><body>
<div class="modal fade" id="logInModal" role="dialog" aria-labelledby="logInLabel">
  <h5 id="logInLabel">Log in</h5></div>
<div class="modal fade" id="videoModal" role="dialog"><video class="video-js" id="example">
  <div class="vjs-modal-dialog vjs-hidden" role="dialog" aria-modal="true">
    <div class="vjs-modal-dialog-content" role="document"></div></div>
  <div class="vjs-text-track-settings vjs-modal-dialog" role="dialog"></div>
</video></div>
<div class="modal" id="after" role="dialog">After the player</div>
</body></html>"""


def test_a_page_dialog_is_listed_once_with_every_route_and_a_players_dialogs_are_left_out():
    doc = _doc(_res("/index.html", PAGE), _res("/prod.html", PAGE),
               _res("/cart.html", PAGE.replace("</body>", '<div class="modal" id="orderModal" '
                                               'role="dialog">Place order</div></body>')))  # fmt: skip
    sel = [d["selector"] for d in doc["dialogs"]]
    assert sel == ["div#logInModal", "div#videoModal", "div#after", "div#orderModal"]
    login = doc["dialogs"][0]
    assert login["routes"] == ["/index.html", "/prod.html", "/cart.html"]
    assert login["files"] == ["/index.html", "/prod.html", "/cart.html"]
    assert doc["dialogs"][3]["routes"] == ["/cart.html"]
    assert "vjs" not in codemap.dump(doc["dialogs"])


def test_a_browser_alert_in_several_scripts_is_one_dialog():
    src = "function go(){ alert('Wrong password.'); }"
    doc = _doc(_res("/js/index.js", src), _res("/js/cart.js", src))
    (alert,) = doc["dialogs"]
    assert alert["kind"] == "browser alert" and alert["files"] == ["/js/index.js", "/js/cart.js"]


def test_a_widget_librarys_script_is_not_read_for_dialogs():
    player = "var d=document.createElement('div');d.innerHTML='<div class=\"x\" role=\"dialog\">" \
             "Caption settings</div>';alert('Playback failed');"  # fmt: skip
    doc = _doc(_res("/node_modules/video.js/dist/video.min.js", "/*! Video.js 7.6.5 */" + player))
    assert doc["dialogs"] == []
    assert doc["third_party"][0]["name"] == "video.js"
