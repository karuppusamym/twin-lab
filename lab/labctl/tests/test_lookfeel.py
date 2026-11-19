"""The look of an application in the build package (labctl.lookfeel), and the interface texts
the privacy pass keeps for it (labctl.sanitize).

Unit tests for each rule, then one recording of a small styled application
(fixtures/lookapp: two style sheets, a web font declaration, a logo, a sprite, user avatars,
every kind of interface text, data in cells and fields) taken through the brief and the package:

- the package holds the style sheets, `theme.json` with the application's colours and fonts, a
  rendered picture per screen, the interface's images and the literal texts;
- no data value of the recording is in any carried file or shown by any picture, and the
  packaging step fails when one is put there, or when a picture is not one Twin Lab rendered;
- fonts are named, not copied, unless `TWINLAB_LOOK_FONTS=bundle`;
- `TWINLAB_PAGE_TEXT=strict` still masks every text and carries no style sheet.
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

HERE = Path(__file__).parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE / "fixtures" / "lookapp"))

import lookapp  # noqa: E402

from labctl import lookfeel, sanitize  # noqa: E402
from labctl.builder import package as pkg  # noqa: E402

# =================================================================================================
# Interface text the privacy pass keeps (sanitize): one test per case found on real recordings
# =================================================================================================

KNOWN = {"open", "closed", "low", "high", "hartley", "smith"}  # words the data shows


def _page(body: str, title: str = "Cases - Caseboard", **kw) -> str:
    return sanitize.page(f"<html><head><title>{title}</title></head><body>{body}</body></html>",
                         KNOWN, **kw).html  # fmt: skip


def test_an_option_that_repeats_its_own_code_is_kept_and_a_record_is_not():
    html = _page(
        '<select id="s"><option value="open">Open</option>'
        '<option value="in-progress">In progress</option>'
        '<option value="17">Smith</option><option value="c2">Hartley</option></select>'
    )
    assert ">Open</option>" in html and ">In progress</option>" in html
    assert "Smith" not in html and "Hartley" not in html  # a key, not a code: still data
    # without the code, a word the data shows is masked as before
    assert ">Xxxx</option>" in _page("<select><option>Open</option></select>")


def test_a_radio_label_that_repeats_the_id_it_is_for_is_kept():
    html = _page(
        '<label for="priority-low"><input type="radio" id="priority-low" name="p"> Low</label>'
        '<label><input type="radio" name="p" value="high"> High</label>'
        '<label for="owner-1"><input type="radio" id="owner-1" name="o"> Hartley</label>'
    )
    assert "> Low</label>" in html and "> High</label>" in html
    assert "Hartley" not in html and "> Xxxxxxx</label>" in html


def test_the_application_name_in_the_title_is_kept_where_an_element_with_an_id_shows_it():
    html = _page('<span id="brand">Caseboard</span><span id="customerName">Hartley</span>')
    assert '<span id="brand">Caseboard</span>' in html
    assert "Hartley" not in html
    # the title must say it: an id alone keeps nothing
    assert '<span id="brand">Xxxxxxxxx</span>' in _page(
        '<span id="brand">Caseboard</span>', "Cases"
    )


def test_joining_words_stay_in_a_value_holder_that_says_interface_words():
    vocab = {"signed", "in"}
    html = _page('<span id="who">Signed in as Margit</span><p id="t">Lost for good as said</p>',
                 vocab=vocab)  # fmt: skip
    assert "Signed in as Xxxxxx" in html
    assert "Xxxx xxx xxxx xx xxxx" in html  # no interface word in it: nothing is kept


def test_a_tooltip_on_a_control_is_read_as_its_label():
    html = _page(
        '<button title="Open the help pages" aria-label="Open">?</button>'
        '<input placeholder="Open or closed" title="Margit Hartley">'
        '<td title="Open the door">x</td>'
    )
    assert 'title="Open the help pages"' in html  # a label's single word the data also shows
    assert 'aria-label="Xxxx"' in html  # the whole text is a data word: masked
    assert 'placeholder="Open or closed"' in html
    assert "Hartley" not in html and "Margit" not in html  # names are masked everywhere
    assert 'title="Xxxx the door"' in html  # not a control: as before


def test_a_glyph_in_a_style_sheet_string_stays_and_words_are_masked():
    css = sanitize._style('.i::before{content:"\\f101"} .r::after{content:" (required)"} '
                          '.s::after{content:"*"} .n::after{content:"\\2713 123"}')  # fmt: skip
    assert 'content:"\\f101"' in css and 'content:"*"' in css
    assert "required" not in css and "(xxxxxxxx)" in css
    assert sanitize.css_symbol('"\\2713"') and not sanitize.css_symbol('"New"')
    assert not sanitize.css_symbol('"1234"')


def test_strict_mode_masks_every_one_of_these_texts():
    html = sanitize.page(
        '<html><head><title>Cases - Caseboard</title><style>a{color:#123456}</style></head><body>'
        '<span id="brand">Caseboard</span><select><option value="open">Open</option></select>'
        '<button title="Open the help pages">Save</button></body></html>', KNOWN, mode="strict",
    ).html  # fmt: skip
    for word in ("Caseboard", "Open", "Save", "help", "color"):
        assert word not in html, word


def test_code_words_are_names_only():
    assert sanitize.code_words("in-progress", "priorityLow") == {
        "in",
        "progress",
        "priority",
        "low",
    }
    assert sanitize.code_words("17", "c2", "") == set()


# =================================================================================================
# Style sheets
# =================================================================================================


def _res(url: str, mime: str, body: bytes = b"x") -> lookfeel.Resource:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return lookfeel.Resource(url, parts.netloc, parts.path, parts.query, mime, body)


def test_clean_css_keeps_the_code_and_drops_addresses_and_data():
    found = {r.url: r for r in (
        _res("http://app.test/static/css/base.css", "text/css"),
        _res("http://app.test/static/img/logo.png", "image/png"),
        _res("http://app.test/static/fonts/a.woff2", "font/woff2"),
    )}  # fmt: skip
    place = {"http://app.test/static/css/base.css": "base.css",
             "http://app.test/static/img/logo.png": "../assets/logo.png"}  # fmt: skip
    css = (
        '@import "base.css"; @import url(//cdn.example/x.css) screen;\n'
        "/* by jane.doe@corp.example, for Hartley */\n"
        '@font-face{font-family:"Open Sans";src:url(../fonts/a.woff2) format("woff2"),'
        "url(data:font/woff2;base64,AAAA)}\n"
        ".logo{background:#b4126e url('../img/logo.png') no-repeat;width:1040px}\n"
        ".x{background:url(http://other.test/t.gif?user=20417)}"
        ".d{background:url(data:image/png;base64,iVBOR)}\n"
        'tr[data-ref="PRT-20417"]{color:rgb(1,2,3)} .n::after{content:"For Hartley"}'
        ' .g::before{content:"\\f101"}\n/*# sourceMappingURL=app.css.map */'
    )
    out, refs = lookfeel.clean_css(css, "http://app.test/static/css/app.css", found.get,
                                   lambda r: place.get(r.url), {"hartley", "open"})  # fmt: skip
    assert '@import url("base.css");' in out and "@import not carried: /x.css" in out
    assert 'url("../assets/logo.png")' in out and "#b4126e" in out and "width:1040px" in out
    assert "rgb(1,2,3)" in out and ".logo{" in out  # selectors, colours and sizes as written
    assert 'font-family:"Open Sans"' in out  # a font's name is not a person's
    assert out.count("url(#font-not-carried)") == 2  # the file and the embedded one
    assert "other.test" not in out and "20417" not in out and "cdn.example" not in out
    assert "jane.doe" not in out and "Hartley" not in out and "sourceMappingURL" not in out
    assert 'content:"\\f101"' in out and "data:image/png;base64,iVBOR" in out
    assert refs.imports == ["base.css"] and refs.embedded_fonts == 1
    assert refs.fonts == ["http://app.test/static/fonts/a.woff2"]
    assert "/t.gif" in refs.dropped
    # the opt-in keeps the font files
    kept, _ = lookfeel.clean_css(css, "http://app.test/static/css/app.css", found.get,
                                 lambda r: "../fonts/a.woff2" if r.kind == "font" else None,
                                 fonts=True)  # fmt: skip
    assert 'url("../fonts/a.woff2")' in kept and "data:font/woff2;base64,AAAA" in kept
    # a cleaned sheet has nothing left to find
    data = lookfeel.Data({"hartley", "open"}, {"PRT-20417"})
    assert lookfeel.css_findings(out, data) == []
    assert lookfeel.css_findings('a::after{content:"For Hartley"}', data) == ["Hartley"]
    assert lookfeel.css_findings('a[x="PRT-20417"]{}', data) == ["PRT-20417"]
    assert lookfeel.css_findings("/* mail a@b.com */", data) == ["an e-mail address"]
    assert lookfeel.css_findings('a{font-family:"Open Sans"}', data) == []


def test_font_faces_name_each_file_and_generic_fallbacks():
    css = ('@font-face{font-family:"Lab Grotesk";font-weight:700;src:local("Lab Grotesk"),'
           'url(/f/lab.woff2?v=2) format("woff2"),url("lab.ttf")}')  # fmt: skip
    res = _res("http://app.test/f/lab.woff2?v=2", "font/woff2", b"1234")
    (face,) = lookfeel.font_faces(css, "http://app.test/css/app.css",
                                  {res.url: res}.get)  # fmt: skip
    assert face["family"] == "Lab Grotesk" and face["weight"] == "700"
    assert face["local"] == ["Lab Grotesk"]
    first, second = face["files"]
    assert (first["path"], first["format"], first["bytes"], first["in_har"]) == (
        "/f/lab.woff2", "woff2", 4, True)  # fmt: skip
    assert (second["path"], second["format"], second["in_har"]) == ("/css/lab.ttf", "truetype",
                                                                    False)  # fmt: skip
    stacks = ['"Lab Grotesk", "Trebuchet MS", sans-serif', "Georgia, serif"]
    assert lookfeel.generic_fallback("Lab Grotesk", stacks) == "sans-serif"
    assert lookfeel.generic_fallback("Georgia", []) == "serif"
    assert lookfeel.generic_fallback("JetBrains Mono") == "monospace"
    assert lookfeel.generic_fallback("Unheard Of") == "sans-serif"
    assert lookfeel.families_of(stacks) == ["Lab Grotesk", "Trebuchet MS", "Georgia"]


# =================================================================================================
# Images, texts, tokens
# =================================================================================================


def test_image_sizes_are_read_from_the_file():
    assert lookfeel.image_size(lookapp.png(48, 16, (1, 2, 3))) == (48, 16)
    assert lookfeel.image_size(lookapp.LOGO, ".svg") == (120, 32)
    assert lookfeel.image_size(b"GIF89a" + (7).to_bytes(2, "little") + (9).to_bytes(2, "little")) == (
        7, 9)  # fmt: skip
    assert lookfeel.image_size(b'<svg viewBox="0 0 24 12"></svg>', ".svg") == (24, 12)
    assert lookfeel.image_size(b"nothing") is None


def test_only_static_images_of_the_interface_are_carried():
    own = {"app.test"}

    def why(url: str, in_data: bool = False, body: bytes = b"x"):
        return lookfeel.interface_image(_res(url, "image/png", body), own, in_data)

    assert why("http://app.test/static/img/logo.png") is None
    assert why("http://app.test/static/img/logo.png?v=3") is None  # only a version
    assert "query" in why("http://app.test/static/img/photo.png?user=7")
    assert "another host" in why("http://cdn.test/logo.png")
    assert "user content" in why("http://app.test/uploads/avatars/me.png")
    assert "user content" in why("http://app.test/api/attachments/scan.png")
    assert "record number" in why("http://app.test/img/1042/photo.png")
    assert "data cell" in why("http://app.test/static/img/flag.png", in_data=True)
    from labctl import har

    assert "larger" in why("http://app.test/static/img/big.png", body=b"x" * (har.MAX_BODY + 1))


def test_texts_are_literal_placeholders_or_mixed():
    kind = lookfeel.text_kind
    assert kind("Save part") == "literal" and kind("A title is required.") == "literal"
    assert kind("Xxxxxx Xxxxxxx") == "data" and kind("XXX-99999") == "data"
    assert kind("x.xxxxx@xxxxxxxx.xxx") == "data" and kind("9,999.99") == "data"
    assert kind("XXX99") == "data"
    assert kind("Part XXX-99999 was saved.") == "mixed"
    assert kind("A-9") == "data" and kind("A A, A A A") == "data"  # the recorder's typed shapes
    assert kind("x") == "literal" and kind("?") == "literal"  # a close button


def test_a_page_is_read_for_its_texts_images_sheets_and_codes():
    read = lookfeel.read_page(
        "<!-- /parts/{id}: value-free snapshot from har:x. -->\n<html><head><title>Edit - Desk</title>"
        '<link rel="stylesheet" href="/static/app.css"><link rel="icon" href="/fav.png">'
        "<style>p{color:red}</style><script>var x = 'never';</script></head><body>"
        '<img id="logo" src="/static/logo.svg" alt="Desk"><h1 id="h">Edit <span id="no">XXX-9999'
        '</span></h1><input id="q" placeholder="Part number" title="Search the list">'
        '<input type="submit" value="Save part"><select><option value="on-hold">On hold</option>'
        '</select><table><tr><td><img src="/uploads/1.png"> Xxxx</td></tr></table></body></html>'
    )
    got = {(t["where"], t["kind"], t["text"], t["is"]) for t in read.texts}
    assert ("title", "title", "Edit - Desk", "literal") in got
    assert ("h1#h", "text", "Edit", "literal") in got
    assert ("span#no", "text", "XXX-9999", "data") in got
    assert ("input#q", "placeholder", "Part number", "literal") in got
    assert ("input#q", "title", "Search the list", "literal") in got
    assert ("input", "value", "Save part", "literal") in got
    assert ("img#logo", "alt", "Desk", "literal") in got
    assert ("td", "text", "Xxxx", "data") in got
    assert not any("never" in t["text"] or "color" in t["text"] for t in read.texts)
    assert read.links == ["/static/app.css"] and read.inline == ["p{color:red}"]
    assert ("/fav.png", False, "the page's icon") in read.images
    assert ("/static/logo.svg", False, "img#logo") in read.images
    assert ("/uploads/1.png", True, "img") in read.images  # inside a data cell
    assert read.codes == {"on", "hold"}
    assert lookfeel.page_pattern("<!-- /parts/{id}: value-free snapshot from har:x. -->") == (
        "/parts/{id}")  # fmt: skip


def test_known_data_is_found_in_a_text_and_shapes_are_not():
    data = lookfeel.Data({"hartley", "open"}, {"PRT-20417", "2026-03-14"})
    assert data.findings("Part PRT-20417, due 2026-03-14.") == ["PRT-20417", "2026-03-14"]
    assert data.findings("Ask Hartley") == ["Hartley"]
    assert data.findings("Open") == ["Open"] and data.findings("Open", codes={"open"}) == []
    # a label keeps a single word the data also shows; not a name beside another, not alone
    assert data.findings("Open the help pages") == ["Open"]
    assert data.findings("Open the help pages", label=True) == []
    assert data.findings("Delete Margit Hartley", label=True) == ["Hartley"]
    assert data.findings("Hartley", label=True) == ["Hartley"]
    assert data.findings("write to m.hartley@corp-mail.com now") == ["an e-mail address"]
    assert data.findings("name@example.test, A@A.A, x.xxx@xxxx.xxx") == []  # examples, shapes
    assert data.findings("Part XXX-99999 was saved. A-9 open hartley") == []
    assert lookfeel._data_tokens(["Order ORD-1026 of 2026-01-02 for 120.50", "A-9 9.9 ORD-0000"]) == {
        "ORD-1026", "2026-01-02", "120.50"}  # fmt: skip


def test_colours_and_the_theme_from_measured_styles():
    assert lookfeel.colour("rgb(180, 18, 110)") == "#b4126e"
    assert lookfeel.colour("rgba(10, 20, 30, 0.45)") == "rgba(10, 20, 30, 0.45)"
    assert lookfeel.colour("rgba(255, 255, 255, 1)") == "#ffffff"

    def group(sample, count=1, classes=(), kind="", **style):
        base = {"color": "rgb(42, 33, 64)", "background": "rgba(0, 0, 0, 0)",
                "font_family": '"Lab Grotesk", sans-serif', "font_size": "16px",
                "font_weight": "400", "padding": "0px 0px 0px 0px", "border": "0px none rgb(0, 0, 0)",
                "border_bottom": "0px none rgb(0, 0, 0)", "radius": "0px"}  # fmt: skip
        return {"count": count, "sample": sample, "classes": list(classes), "type": kind,
                "width": 100, "height": 40, "style": {**base, **style}}  # fmt: skip

    look = {
        "roles": {
            "body": [group("body", background="rgb(251, 246, 236)")],
            "h1": [group("h1#heading", font_family="Georgia, serif", font_size="28px")],
            "button": [
                group("button.btn", 3, ["btn"], "button", background="rgb(255, 255, 255)",
                      border="2px solid rgb(107, 91, 149)", radius="11px"),
                group("button#save", 1, ["btn", "btn--primary"], "submit", color="rgb(255, 255, 255)",
                      background="rgb(180, 18, 110)", border="2px solid rgb(180, 18, 110)",
                      radius="11px", padding="9px 22px 9px 22px"),
            ],
            "input": [group("input#q", border="2px solid rgb(107, 91, 149)", radius="7px")],
            "error": [group("p.msg--error", color="rgb(194, 65, 12)")],
        },
        "palette": {"rgb(180, 18, 110)": {"uses": 2, "as": {"background": 1, "border": 1}}},
        "families": {'"Lab Grotesk", sans-serif': 9, "Georgia, serif": 1},
        "regions": [{"selector": "main", "x": 120, "y": 64, "width": 1040, "height": 400}],
        "page": {"width": 1280, "height": 720, "content_width": 1040, "content_max_width": "1040px"},
    }  # fmt: skip
    theme = lookfeel.theme({"parts": look}, {"width": 1280, "height": 720})
    c = theme["colors"]
    assert c["page_background"] == "#fbf6ec" and c["text"] == "#2a2140"
    assert c["primary_button_background"] == "#b4126e" and c["primary_button_text"] == "#ffffff"
    assert c["secondary_button_background"] == "#ffffff" and c["border"] == "#6b5b95"
    assert c["error_text"] == "#c2410c"
    assert theme["fonts"]["h1"] == {"family": "Georgia, serif", "size": "28px", "weight": "400",
                                    "color": "#2a2140"}  # fmt: skip
    assert theme["controls"]["button_primary"]["radius"] == "11px"
    assert theme["controls"]["button_primary"]["padding"] == "9px 22px 9px 22px"
    assert theme["controls"]["input"]["border"] == "2px solid #6b5b95"
    assert theme["font_stacks"][0] == '"Lab Grotesk", sans-serif'
    assert theme["palette"][0] == {"color": "#b4126e", "uses": 2, "as": ["background", "border"]}
    assert theme["layout"]["pages"][0]["content_width"] == 1040
    assert theme["layout"]["regions"][0]["screen"] == "parts"


def test_resources_come_from_the_har_as_delivered():
    import base64

    def entry(url, mime, text, status=200, method="GET", encoding=None):
        content = {"mimeType": mime, "text": text}
        if encoding:
            content["encoding"] = encoding
        return {"request": {"method": method, "url": url},
                "response": {"status": status, "content": content}}  # fmt: skip

    har = {"log": {"entries": [
        entry("http://a.test/s/app.css?v=1", "text/css; charset=utf-8", "a{}"),
        entry("http://a.test/s/logo.png", "image/png", base64.b64encode(b"\x89PNG").decode(),
              encoding="base64"),
        entry("http://a.test/s/f.woff2", "application/octet-stream", "AAAA", encoding="base64"),
        entry("http://a.test/api/list", "application/json", "[]"),
        entry("http://a.test/s/gone.css", "text/css", "x", status=404),
        entry("http://a.test/s/post.css", "text/css", "x", method="POST"),
        entry("http://a.test/s/app.css?v=1", "text/css", "again{}"),
    ]}}  # fmt: skip
    got = lookfeel.resources(har)
    assert [(r.path, r.kind, r.query) for r in got] == [
        ("/s/app.css", "css", "v=1"), ("/s/logo.png", "image", ""), ("/s/f.woff2", "font", "")]  # fmt: skip
    assert got[0].body == b"a{}" and got[1].body == b"\x89PNG" and got[0].mime == "text/css"


# =================================================================================================
# One recording of the styled fixture, through the brief and the package
# =================================================================================================

pytest.importorskip("playwright.sync_api")

APP = "partsdesk"


def make_root(path: Path) -> Path:
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (path / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", path / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", path / "docs" / "factory-playbook.md")
    return path


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    from labctl import recorder

    rec = tmp_path_factory.mktemp("lookapp-rec") / "parts"
    run = lookapp.serve()
    try:
        recorder.record(run.base + "/parts", rec, headless=True, script=lookapp.flow)
    finally:
        run.stop()
    return rec


def build(recording: Path, root: Path) -> SimpleNamespace:
    """What `labctl evidence build-all` does with the recording, the proposal reviewed."""
    from labctl import multiapp

    make_root(root)
    rec = multiapp.recordings_dir(root) / "parts"
    shutil.copytree(recording, rec)
    multiapp.build_all(root, rec, say=lambda m: None)
    proposal = multiapp.proposal_file(rec)
    data = yaml.safe_load(proposal.read_text("utf-8"))
    data["apps"][0]["id"] = APP
    proposal.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    summary = multiapp.build_all(root, rec, proposal=proposal, say=lambda m: None,
                                 package_dir=root / "packages")  # fmt: skip
    (result,) = summary.apps
    return SimpleNamespace(root=root, app=root / "apps" / APP, result=result,
                           package=root / result.package if result.package else None)  # fmt: skip


@pytest.fixture(scope="module")
def built(recording, tmp_path_factory, monkeypatch_module):
    monkeypatch_module.delenv("TWINLAB_PAGE_TEXT", raising=False)
    monkeypatch_module.delenv(lookfeel.FONTS_ENV, raising=False)
    monkeypatch_module.delenv(lookfeel.SWITCH_ENV, raising=False)
    out = build(recording, tmp_path_factory.mktemp("lookapp") / "repo")
    assert out.result.errors == [] and out.package is not None, out.result.errors
    return out


@pytest.fixture(scope="module")
def monkeypatch_module():
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()


def _zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_the_package_holds_the_style_sheets_as_delivered(built):
    files = _zip(built.package)
    look = f"apps/{APP}/look/"
    app_css = files[look + "css/app.css"].decode()
    base_css = files[look + "css/base.css"].decode()
    for colour in (lookapp.PRIMARY, lookapp.HEADER, lookapp.ERROR, lookapp.SUCCESS_BG):
        assert colour in app_css, colour
    assert lookapp.PAGE in base_css and f'"{lookapp.FONT}", "Trebuchet MS", sans-serif' in base_css
    assert ".btn--primary {" in app_css and "border-radius: 11px" in app_css  # as written
    assert '@import url("base.css");' in app_css
    assert 'url("../assets/sprite.png")' in app_css and 'content: "\\2713"' in app_css
    assert 'content: " (required)"' in app_css  # the interface's words, kept
    assert "url(#font-not-carried)" in app_css and "sourceMappingURL" not in app_css
    assert "PRT-20417" not in app_css and "example.invalid" not in app_css
    assert "northwind" not in base_css  # the e-mail address in the comment
    assert files[look + "css/inline/parts.css"].decode().strip() == (
        ".muted { color: #7c6f99; font-size: 13px; }")  # fmt: skip
    manifest = json.loads(files[look + "manifest.json"])
    assert [c["file"] for c in manifest["css"]] == ["css/app.css", "css/base.css"]
    assert manifest["css"][0]["imports"] == ["css/base.css"]
    assert manifest["css"][0]["not_carried"] == ["/pixel.gif"]  # a path, never the host
    assert all(c["from"] == "the application's own origin" for c in manifest["css"])


def test_the_theme_has_the_colours_fonts_and_sizes_of_the_application(built):
    theme = json.loads((built.app / "look" / "theme.json").read_text("utf-8"))
    c = theme["colors"]
    assert c["page_background"] == lookapp.PAGE and c["text"] == lookapp.TEXT
    assert c["header_background"] == lookapp.HEADER and c["header_text"] == "#ffffff"
    assert c["primary_button_background"] == lookapp.PRIMARY
    assert c["primary_button_text"] == "#ffffff"
    assert c["secondary_button_background"] == "#ffffff" and c["border"] == "#6b5b95"
    assert c["error_text"] == lookapp.ERROR and c["success_background"] == lookapp.SUCCESS_BG
    assert c["table_header_background"] == "#e7def7" and c["table_row_border"] == "#d8cfe8"
    assert c["link"] == "#1d5fd1"
    fonts = theme["fonts"]
    assert fonts["body"]["family"].startswith(f'"{lookapp.FONT}", "Trebuchet MS"')
    assert (fonts["body"]["size"], fonts["body"]["weight"]) == ("16px", "400")
    assert fonts["h1"]["family"].startswith("Georgia") and fonts["h1"]["size"] == "28px"
    assert (fonts["label"]["size"], fonts["label"]["weight"]) == ("13px", "700")
    assert fonts["label"]["text_transform"] == "uppercase"
    assert fonts["table_header"]["size"] == "14px" and fonts["table_cell"]["size"] == "16px"
    assert fonts["input"]["size"] == "16px" and fonts["button"]["family"] == fonts["body"]["family"]
    controls = theme["controls"]
    assert controls["button_primary"]["radius"] == "11px"
    assert controls["button_primary"]["padding"] == "9px 22px 9px 22px"
    assert (
        controls["input"]["radius"] == "7px" and controls["input"]["padding"] == "8px 12px 8px 12px"
    )
    assert controls["input"]["border"] == "2px solid #6b5b95"
    assert theme["spacing"]["main_padding"] == "26px 26px 26px 26px"
    assert theme["viewport"] == {"width": 1280, "height": 720}
    pages = {p["screen"]: p for p in theme["layout"]["pages"]}
    assert (
        pages["parts"]["content_width"] == 1040 and pages["parts"]["content_max_width"] == "1040px"
    )
    assert any(r["selector"] == "header#topbar" and r["width"] == 1280
               for r in theme["layout"]["regions"])  # fmt: skip
    assert lookapp.PRIMARY in {p["color"] for p in theme["palette"]}
    # no text of any page is in the tokens
    text = json.dumps(theme)
    assert "Parts on order" not in text and "Amounts are in euro" not in text


def test_fonts_are_named_with_their_source_and_not_copied(built):
    files = _zip(built.package)
    assert not any("/look/fonts/" in n or n.endswith((".woff2", ".woff", ".ttf")) for n in files)
    manifest = json.loads(files[f"apps/{APP}/look/manifest.json"])
    fonts = {f["family"]: f for f in manifest["fonts"]}
    lab = fonts[lookapp.FONT]
    assert lab["source"] == "web font (declared with @font-face in css/app.css)"
    assert lab["generic_fallback"] == "sans-serif" and lab["bundled"] is False
    assert "body" in lab["used_for"] and "Not copied (fonts are licensed)" in lab["to_do"]
    seen, unseen = lab["files"]
    assert (seen["path"], seen["format"], seen["in_har"], seen["bytes"]) == (
        "/static/fonts/labgrotesk-regular.woff2", "woff2", True, len(lookapp.FONT_FILE))  # fmt: skip
    assert (unseen["path"], unseen["format"], unseen["in_har"]) == (
        "/static/fonts/labgrotesk-regular.woff", "woff", False)  # fmt: skip
    assert fonts["Georgia"]["source"].startswith("system font")
    assert fonts["Georgia"]["generic_fallback"] == "serif" and fonts["Georgia"]["used_for"] == [
        "h1"
    ]
    assert fonts["Trebuchet MS"]["to_do"].startswith("Nothing to obtain")
    assert manifest["fonts_bundled"] is False


def test_interface_images_are_carried_and_user_content_is_not(built):
    files = _zip(built.package)
    look = f"apps/{APP}/look/"
    assert files[look + "assets/logo.svg"] == lookapp.LOGO
    assert lookfeel.image_size(files[look + "assets/sprite.png"]) == (48, 16)
    assert sorted(n for n in files if n.startswith(look + "assets/")) == [
        look + "assets/logo.svg", look + "assets/sprite.png"]  # fmt: skip
    images = {i["path"]: i for i in json.loads(files[look + "manifest.json"])["images"]}
    logo = images["/static/img/logo.svg"]
    assert logo["bundled"] and (logo["width"], logo["height"]) == (120, 32)
    assert logo["where"] == ["img#logo on parts", "img#logo on parts-item"]
    assert images["/static/img/sprite.png"]["where"] == ["a style sheet"]
    me = images["/uploads/avatars/me.png"]
    assert not me["bundled"] and me["file"] is None and "user content" in me["reason"]
    assert (me["width"], me["height"]) == (28, 28)
    assert not any(i["bundled"] for p, i in images.items() if p.startswith("/uploads/"))
    assert images["/static/img/favicon.png"]["reason"].startswith("not in the recording") or (
        images["/static/img/favicon.png"]["bundled"])  # fmt: skip


def test_every_screen_has_a_rendered_picture_and_never_a_recorded_one(built, recording):
    files = _zip(built.package)
    look = f"apps/{APP}/look/"
    manifest = json.loads(files[look + "manifest.json"])
    screens = {s["screen"]: s for s in manifest["screens"]}
    assert sorted(screens) == ["parts", "parts-item"]
    pages = sorted(Path(n).stem for n in files if n.startswith(f"apps/{APP}/pages/"))
    assert pages == sorted(screens)
    recorded = {p.read_bytes() for p in (recording / "screens").glob("*.png")}
    assert recorded
    import hashlib

    for name, s in screens.items():
        png = files[look + s["picture"]]
        assert s["picture"] == f"screens/{name}.png" and png[:8] == b"\x89PNG\r\n\x1a\n"
        assert lookfeel.image_size(png)[0] == 1280 and lookfeel.image_size(png)[1] >= 720
        assert hashlib.sha256(png).hexdigest() == s["sha256"]
        assert png not in recorded
        # the page asked for nothing outside the package but what the manifest says is not carried
        assert all(a.startswith("/uploads/") or "favicon" in a or "fonts" in a
                   for a in s["not_loaded"]), s["not_loaded"]  # fmt: skip
    assert not any(n.startswith("evidence/") or "/screens/00" in n for n in files)
    assert manifest["check"]["result"] == "pass" and manifest["check"]["pictures_checked"] == 2
    assert manifest["check"]["known_names"] > 0 and manifest["check"]["known_tokens"] > 0
    assert manifest["pictures"].startswith("rendered from the sanitised page snapshots")


def test_the_picture_shows_the_interface_text_and_no_data(built, recording):
    """Rendered again from what the package carries: the text the picture shows."""
    manifest = json.loads((built.app / "look" / "manifest.json").read_text("utf-8"))
    served = {}
    for c in manifest["css"]:
        served[c["source"]] = ((built.app / "look" / c["file"]).read_bytes(), "text/css")
    pages = {p.stem: (lookfeel.page_pattern(p.read_text("utf-8")), p.read_text("utf-8"))
             for p in (built.app / "pages").glob("*.html")}  # fmt: skip
    shots, why = lookfeel.render(pages, served)
    assert why is None and sorted(shots) == ["parts", "parts-item"]
    text = shots["parts"].text + "\n" + shots["parts-item"].text
    for literal in ("Parts on order", "Search", "Newest part", "Amounts are in euro.", "E-mail",
                    "A description is required.", "Description", "On hold", "Save part",
                    "Back to the list", "Urgency"):  # fmt: skip
        assert literal in text, literal
    for value in lookapp.DATA_VALUES:
        assert value not in text, value
    assert "@northwind" not in text and "XXX-99999" in text
    assert lookfeel.known_data([recording]).findings(text, {"open", "closed", "on", "hold", "low",
                                                            "high", "urgency"}) == []  # fmt: skip


def test_literal_interface_texts_are_in_the_package_and_marked(built):
    files = _zip(built.package)
    texts = json.loads(files[f"apps/{APP}/look/texts.json"])["screens"]
    literal = {s: {t["text"] for t in items if t["is"] == "literal"} for s, items in texts.items()}
    # labels, placeholder, button texts, heading, menu entries, table headers, tooltip, aria-label
    assert {"Search", "Part number or owner", "Newest part", "Parts on order", "Parts", "Reports",
            "Part", "Description", "Owner", "E-mail", "Due", "Amount", "Status", "Help",
            "Search the parts list", "Open the help pages", "Amounts are in euro.",
            "Parts desk"} <= literal["parts"]  # fmt: skip
    # option texts, radio labels, legend, validation message, tooltip, placeholder
    assert {"Open", "On hold", "Closed", "Low", "High", "Urgency", "A description is required.",
            "Save part", "Save this part", "First and last name", "Due date", "Notes",
            "Back to the list", "Edit part"} <= literal["parts-item"]  # fmt: skip
    kinds = {(t["kind"], t["text"]) for t in texts["parts"]}
    assert ("placeholder", "Part number or owner") in kinds and ("aria-label", "Help") in kinds
    assert ("title", "Open the help pages") in kinds and ("alt", "Parts desk") in kinds
    mixed = [t["text"] for t in texts["parts"] if t["is"] == "mixed"]
    assert mixed == ["Part XXX-99999 was saved."]  # the status message: words kept, data masked
    data = [t for t in texts["parts"] if t["is"] == "data"]
    assert {t["where"] for t in data} == {"a#open-PRT-99999", "td"}
    assert [t["where"] for t in texts["parts-item"] if t["is"] == "data"] == ["span#partNo"]
    # the same texts are in the page snapshot the picture was rendered from
    page = files[f"apps/{APP}/pages/parts-item.html"].decode()
    for text in ("Open</option>", "On hold</option>", "> Low</label>", 'title="Save this part"',
                 "A description is required.", 'placeholder="First and last name"'):  # fmt: skip
        assert text in page, text


def test_no_known_data_value_is_in_any_carried_file(built, recording):
    files = _zip(built.package)
    data = lookfeel.known_data([recording])
    assert {"margit", "lindqvist", "okonkwo"} <= data.names  # (`Hartley` was typed: never recorded)
    assert {"PRT-20417", "2026-03-14", "1,284.50"} <= data.tokens
    carried = {n: b for n, b in files.items()
               if n.startswith((f"apps/{APP}/look/", f"apps/{APP}/pages/"))}  # fmt: skip
    assert len(carried) >= 12
    for name, body in carried.items():
        if name.endswith(".png"):
            continue
        text = body.decode("utf-8")
        for value in lookapp.DATA_VALUES:
            assert value not in text, (name, value)
        assert "northwind" not in text, name
    assert lookfeel.package_check(built.root, APP) == []


def test_the_brief_and_the_task_tell_the_builder_how_to_use_the_look(built):
    files = _zip(built.package)
    brief = files[f"apps/{APP}/BRIEF.md"].decode()
    section = pkg._section(brief, lookfeel.HEADING)
    for line in ("Start from the carried style sheets.", "`theme.json` is the summary",
                 "Compare each screen with its picture.", "Literal texts must be identical",
                 "| `look/css/app.css` | `/static/css/app.css` | the application's own origin |",
                 f"| primary button background | `{lookapp.PRIMARY}` |",
                 "| button primary | 9px 22px 9px 22px | 2px solid #b4126e | 11px |",
                 "Main content width: 1040px (max-width 1040px)",
                 f"| `{lookapp.FONT}` | web font (declared with @font-face in css/app.css) |",
                 "fonts are licensed, so a font file is copied only when its licence allows",
                 "| `/static/img/logo.svg` | 120 x 32 | img#logo on parts; img#logo on parts-item | "
                 "`look/assets/logo.svg` |",
                 "no: under a folder for user content (avatars, uploads)",
                 "**parts-item** (`/parts/{id}`): picture `look/screens/parts-item.png`, snapshot "
                 "`pages/parts-item.html`.",
                 "- Placeholders for data (show your data there): `span#partNo`.",
                 "- Literal words around a placeholder: `Part XXX-99999 was saved.`."):  # fmt: skip
        assert line in section, line
    assert "`A description is required.`" in section and "`Save part`" in section
    assert brief.index(f"## {lookfeel.HEADING}") < brief.index("## Running in the lab")
    # the brief's own inventory (what a later enhancement compares) is not disturbed
    assert pkg.brief_inventory(brief)["screens"] == {"/parts", "/parts/{id}"}
    task = files["BUILD_TASK.md"].decode()
    assert "## Same look" in task and task.index("## Same look") < task.index("## Database")
    for line in (f"`apps/{APP}/look/css/`: the original style sheets",
                 f"`apps/{APP}/look/theme.json`", f"`apps/{APP}/look/screens/<screen>.png`",
                 f"`apps/{APP}/look/texts.json`", f"`apps/{APP}/look/manifest.json`",
                 f"`apps/{APP}/pages/*.html`, `apps/{APP}/look/**` and",
                 "Look: open each screen at 1280 x 720 pixels next to",
                 f"`apps/{APP}/look/screens/*.png` are pictures of the screens"):  # fmt: skip
        assert line in task, line
    assert "The package has no pictures of the screens" not in task
    assert task.count("Look: open each screen") == 2  # the lab's checks and the package-only ones
    manifest = json.loads(files["MANIFEST.json"])
    listed = {f["path"] for f in manifest["files"]}
    assert (
        f"apps/{APP}/look/screens/parts.png" in listed and f"apps/{APP}/look/css/app.css" in listed
    )


def test_packaging_fails_when_the_look_holds_data_or_a_foreign_picture(built, tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(built.root, root)
    look = root / "apps" / APP / "look"
    assert pkg.build(root, APP, tmp_path / "ok").is_file()
    # a recorded screenshot put where the rendered picture belongs
    picture = look / "screens" / "parts.png"
    rendered = picture.read_bytes()
    picture.write_bytes(lookapp.png(4, 4, (9, 9, 9)))
    with pytest.raises(pkg.PackageError, match="not a picture Twin Lab rendered"):
        pkg.build(root, APP, tmp_path / "out")
    picture.write_bytes(rendered)
    (look / "screens" / "0007.png").write_bytes(lookapp.png(4, 4, (9, 9, 9)))
    with pytest.raises(pkg.PackageError, match="0007.png is not a picture Twin Lab rendered"):
        pkg.build(root, APP, tmp_path / "out")
    (look / "screens" / "0007.png").unlink()
    # a data value in a carried style sheet
    sheet = look / "css" / "app.css"
    clean = sheet.read_text("utf-8")
    sheet.write_text(clean + '\n.x::after { content: "Ask Okonkwo"; }\n', "utf-8")
    with pytest.raises(pkg.PackageError, match="css/app.css holds `Okonkwo`"):
        pkg.build(root, APP, tmp_path / "out")
    sheet.write_text(clean, "utf-8")
    # a data value in the page a picture shows
    page = root / "apps" / APP / "pages" / "parts.html"
    html = page.read_text("utf-8")
    page.write_text(html.replace("Amounts are in euro.", "Amounts for PRT-20417 are in euro."),
                    "utf-8")  # fmt: skip
    with pytest.raises(pkg.PackageError, match=r"the picture screens/parts.png shows `PRT-20417`"):
        pkg.build(root, APP, tmp_path / "out")
    page.write_text(html, "utf-8")
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.zip"))
    assert pkg.build(root, APP, tmp_path / "again").is_file()


def test_writing_the_look_refuses_data_and_leaves_nothing(built, recording, tmp_path, monkeypatch):
    """A rendered picture that would show a known value: nothing is written, and it is said."""
    root = tmp_path / "repo"
    shutil.copytree(built.root, root)
    page = root / "apps" / APP / "pages" / "parts.html"
    page.write_text(page.read_text("utf-8").replace("Amounts are in euro.",
                                                    "Ask Okonkwo about 2026-03-14."), "utf-8")  # fmt: skip
    with pytest.raises(lookfeel.LookError) as failure:
        lookfeel.write(root, APP)
    assert "the picture screens/parts.png shows `Okonkwo`" in str(failure.value)
    assert "the picture screens/parts.png" in failure.value.places
    assert not any("Okonkwo" in p for p in failure.value.places)  # places name no value
    assert not (root / "apps" / APP / "look").exists()
    section = lookfeel.brief_section(root, APP, refused=failure.value.places)
    assert "No look is carried" in section and "privacy check" in section
    assert "Okonkwo" not in section and lookfeel.task_text(root, APP) == ""


def test_font_files_are_bundled_only_on_request(built, tmp_path, monkeypatch):
    root = tmp_path / "repo"
    shutil.copytree(built.root, root)
    monkeypatch.setenv(lookfeel.FONTS_ENV, "bundle")
    manifest = lookfeel.write(root, APP)
    look = root / "apps" / APP / "look"
    assert (look / "fonts" / "labgrotesk-regular.woff2").read_bytes() == lookapp.FONT_FILE
    css = (look / "css" / "app.css").read_text("utf-8")
    assert 'url("../fonts/labgrotesk-regular.woff2") format("woff2")' in css
    assert 'url(#font-not-carried) format("woff")' in css  # the one the browser never asked for
    lab = next(f for f in manifest["fonts"] if f["family"] == lookapp.FONT)
    assert lab["bundled"] is True and manifest["fonts_bundled"] is True
    assert lab["files"][0]["bundled"] == "fonts/labgrotesk-regular.woff2"
    assert "look/fonts/" in lab["to_do"]
    assert "on the owner's request (`TWINLAB_LOOK_FONTS=bundle`)" in lookfeel.brief_section(
        root, APP
    )
    assert "carried on the owner's request" in lab["files"][0]["warning"]  # no licence identified
    assert "font files are in `look/fonts/`" in lookfeel.task_text(root, APP)
    assert any(rel.endswith("look/fonts/labgrotesk-regular.woff2")
               for rel, _ in lookfeel.package_files(root, APP))  # fmt: skip
    monkeypatch.setenv(lookfeel.SWITCH_ENV, "off")
    assert lookfeel.write(root, APP) is None and not look.exists()
    assert "switched off" in lookfeel.brief_section(root, APP)


def test_strict_mode_masks_every_text_and_carries_no_style_sheet(recording, tmp_path, monkeypatch):
    monkeypatch.setenv("TWINLAB_PAGE_TEXT", "strict")
    out = build(recording, tmp_path / "repo")
    assert out.result.errors == [] and out.package is not None
    files = _zip(out.package)
    assert not any("/look/" in n or n.endswith((".css", ".png", ".svg")) for n in files)
    pages = [body.decode() for n, body in files.items() if n.startswith(f"apps/{APP}/pages/")]
    assert len(pages) == 2
    for html in pages:
        for text in ("Parts on order", ">Search<", "Save part", "Parts desk", "Edit part", "On hold",
                     "A description is required.", lookapp.PRIMARY, lookapp.HEADER, "color",
                     "First and last name", "Hartley", "PRT-20417"):  # fmt: skip
            assert text not in html, text
        assert "strict page-text mode" in html and 'id="heading"' in html  # structure stays
    brief = files[f"apps/{APP}/BRIEF.md"].decode()
    assert "No look is carried for this application: strict page-text mode" in brief
    task = files["BUILD_TASK.md"].decode()
    assert "## Same look" not in task and "/look/" not in task
    assert "The package has no pictures of the screens" in task
