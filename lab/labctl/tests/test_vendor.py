"""The open-source client libraries and web fonts are carried in the package (labctl.vendor).

From a recording of fixtures/vendorapp (a made-up MIT library with an icon font, a library of
unknown licence, a library with a planted key, the application's own script, a web font whose
name table says OFL and one that says nothing):

- the MIT library is carried byte for byte under `vendor/<library>/<version>/`, with its icon
  font at the same relative place; the unknown one and the leaky one are named with the reason,
  not carried; the application's own script is never carried by this rule;
- the OFL font is carried in `look/fonts/`, the carried style sheet points at it, and the
  pictures are rendered with it; the font without a licence is not, unless
  `TWINLAB_LOOK_FONTS=bundle` (with a warning in the package);
- `THIRD-PARTY-NOTICES.md` names each with its version, licence, address and sha256 and holds
  the licence texts; `MANIFEST.json` and `INDEX.md` say what each file is for; the brief, the
  architecture, the page file and the task tell the builder to use these exact files.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from labctl import fontname, lookfeel, vendor

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE / "fixtures" / "vendorapp"))

import vendorapp  # noqa: E402

# =================================================================================================
# Licences, fonts and the check, without a browser
# =================================================================================================


def test_a_licence_is_read_from_the_banner_or_known_for_a_well_known_library():
    lic = vendor.library_licence("TinyLib", [vendorapp.TINYLIB_CSS])
    assert (lic.ok, lic.id, lic.source) == (True, "MIT", "the licence banner of the file")
    assert lic.copyright == "Copyright 2026 The TinyLib Authors"
    jq = vendor.library_licence(
        "jQuery", ["/*! jQuery v3.2.1 | (c) JS Foundation | jquery.org/license */"]
    )
    assert jq.ok and jq.id == "MIT" and jq.source.startswith("the known licence of jQuery")
    vjs = vendor.library_licence("video.js", ["/**\n * @license\n * Video.js 7.6.5\n * Available under "
                                              "Apache License Version 2.0\n */"])  # fmt: skip
    assert vjs.id == "Apache-2.0" and vjs.source == "the licence banner of the file"
    unknown = vendor.library_licence("Mystery", [vendorapp.MYSTERY_JS])
    assert not unknown.ok and "not identified" in unknown.why_not
    gpl = vendor.library_licence("Mystery", ["/*! Mystery v1 | Licensed under the GPL v3 */"])
    assert not gpl.ok and "not on the list" in gpl.why_not
    dual = vendor.library_licence("Old", ["/*! Old v1 | Dual licensed under the MIT or GPL */"])
    assert dual.ok and dual.id == "MIT"


def test_a_font_states_its_licence_in_its_name_table_ttf_and_woff():
    for body in (
        vendorapp.ttf(vendorapp.FAMILY, vendorapp.OFL),
        vendorapp.woff(vendorapp.FAMILY, vendorapp.OFL),
    ):
        names = fontname.read(body)
        assert names.why is None and names.family == vendorapp.FAMILY
        assert names.licence == vendorapp.OFL and names.licence_url == "https://openfontlicense.org"
        lic = vendor.font_licence(body)
        assert lic.ok and lic.id == "OFL-1.1" and lic.source.startswith("the font's own name table")
        assert lic.copyright == f"Copyright 2026 The {vendorapp.FAMILY} Project Authors"
    plain = vendor.font_licence(vendorapp.ttf(vendorapp.PLAIN), vendorapp.PLAIN)
    assert not plain.ok and "could not be identified" in plain.why_not
    # a library's icon font that states nothing: the library's licence
    icons = vendor.font_licence(vendorapp.woff("TinyIcons"), "TinyIcons", "tinyicons.woff",
                                vendor.library_licence("TinyLib", [vendorapp.TINYLIB_JS]), "TinyLib")  # fmt: skip
    assert icons.ok and icons.id == "MIT" and "TinyLib" in icons.source
    # a font whose own name table names other terms is not carried, whatever its family
    own = vendor.font_licence(vendorapp.ttf("Lato", "Proprietary: may not be redistributed."))
    assert not own.ok and "not on the list" in own.why_not


def test_a_woff2_that_cannot_be_read_falls_back_to_a_known_family(monkeypatch):
    monkeypatch.setitem(sys.modules, "brotli", None)  # as on a machine without Brotli
    body = b"wOF2" + b"\x00" * 60
    names = fontname.read(body)
    assert names.records == {} and "Brotli" in names.why
    lato = vendor.font_licence(body, "LatoWeb", "Lato-Regular.woff2")
    assert lato.ok and lato.id == "OFL-1.1" and "`LatoWeb` family" in lato.source
    assert "Brotli" in lato.source
    assert (
        vendor.family_key("LatoWebBlack") == "lato"
        and vendor.family_key("fontawesome-webfont") == "fontawesome"
    )
    assert vendor.family_key("Interstate-Bold") == "interstate"
    unknown = vendor.font_licence(body, "Lab Grotesk", "labgrotesk-regular.woff2")
    assert not unknown.ok and "Brotli" in unknown.why_not


def test_the_check_refuses_keys_and_internal_hosts_and_not_a_library_code():
    assert any("cloud access key" in x for x in vendor.findings(vendorapp.LEAKY_JS))
    assert any("internal host" in x for x in vendor.findings('var api="https://db01.corp/x";'))
    assert vendor.findings(vendorapp.TINYLIB_JS) == []
    # an embedded font, a public home page, `r.LOCAL` in code: not findings
    font = base64.b64encode(vendorapp.woff("TinyIcons")).decode()
    css = f"/*! x v1 | MIT | https://github.com/x/x */@font-face{{src:url(data:application/font-woff;base64,{font})}}"
    assert vendor.findings(css) == []
    assert vendor.findings("var a=r.MPEGTS/9e4-r.LOCAL+t.mapping;") == []


def _recording(folder: Path, base: str = "http://shelf.test", extra: dict | None = None) -> Path:
    """A recording of vendorapp's one page, written as the recorder would (HAR and steps)."""
    entries = []
    for path, (mime, body) in [("/shelf", ("text/html", vendorapp.PAGE.encode())),
                               *vendorapp.STATIC.items(), *(extra or {}).items()]:  # fmt: skip
        text = body.decode() if mime.startswith(("text/", "application/javascript")) else None
        content = ({"mimeType": mime, "text": text} if text is not None else
                   {"mimeType": mime, "text": base64.b64encode(body).decode(), "encoding": "base64"})  # fmt: skip
        entries.append({"request": {"method": "GET", "url": base + path, "headers": []},
                        "response": {"status": 200, "headers": [], "content": content}})  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "recording.har").write_text(json.dumps({"log": {"entries": entries}}), "utf-8")
    (folder / "steps.jsonl").write_text(json.dumps({"seq": 1, "url": base + "/shelf",
                                                    "action": "goto"}) + "\n", "utf-8")  # fmt: skip
    return folder


def test_libraries_are_carried_named_or_refused_and_listed_with_their_licences(tmp_path):
    rec = _recording(tmp_path / "rec")
    doc = vendor.write(tmp_path, "shelf", [rec])
    app = tmp_path / "apps" / "shelf"
    (lib,) = doc["libraries"]
    assert (lib["name"], lib["version"], lib["licence"], lib["folder"]) == (
        "tinylib",
        "1.2.3",
        "MIT",
        "vendor/tinylib/1.2.3",
    )
    files = {f["file"]: f for f in lib["files"]}
    assert set(files) == {"vendor/tinylib/1.2.3/dist/css/tinylib.css", "vendor/tinylib/1.2.3/dist/js/tinylib.min.js",
                          "vendor/tinylib/1.2.3/dist/fonts/tinyicons.woff"}  # fmt: skip
    # byte for byte, at the same relative places: the library's own url(../fonts/...) works
    css = files["vendor/tinylib/1.2.3/dist/css/tinylib.css"]
    assert (app / css["file"]).read_bytes() == vendorapp.TINYLIB_CSS.encode()
    assert css["address"] == "/node_modules/tinylib/dist/css/tinylib.css"
    assert css["sha256"] == hashlib.sha256(vendorapp.TINYLIB_CSS.encode()).hexdigest()
    assert files["vendor/tinylib/1.2.3/dist/fonts/tinyicons.woff"]["licence"] == "MIT"
    assert lib["not_in_recording"] == ["/node_modules/tinylib/dist/img/never-loaded.png"]
    assert lib["pages"] == ["/shelf"]
    refused = {x["name"]: x["reason"] for x in doc["not_carried"]}
    assert set(refused) == {"mystery", "leaky"}
    assert "licence is not identified" in refused["mystery"]
    assert "secret and host check" in refused["leaky"] and "cloud access key" in refused["leaky"]
    assert vendorapp.SECRET not in json.dumps(doc)
    assert doc["own_scripts_not_carried"] == ["/static/js/shelf.js"]
    assert doc["pages"] == {"/shelf": ["/node_modules/tinylib/dist/css/tinylib.css",
                                       "/node_modules/tinylib/dist/js/tinylib.min.js"]}  # fmt: skip
    carried = sorted(
        p.relative_to(app / "vendor").as_posix() for p in (app / "vendor").rglob("*") if p.is_file()
    )
    assert not any("mystery" in p or "leaky" in p or "shelf.js" in p for p in carried)
    notices = (app / vendor.NOTICES).read_text("utf-8")
    assert (
        "### tinylib 1.2.3" in notices and "MIT License (`MIT`), from the licence banner" in notices
    )
    assert css["sha256"] in notices and "`/node_modules/tinylib/dist/css/tinylib.css`" in notices
    assert "Permission is hereby granted, free of charge" in notices  # the licence text
    assert "## Named, not carried" in notices and "mystery" in notices
    assert vendor.package_check(tmp_path, "shelf") == []
    # a file put there by hand, or changed, is refused when packaging
    (app / "vendor" / "tinylib" / "1.2.3" / "dist" / "js" / "extra.js").write_text(
        "var x=1;", "utf-8"
    )
    (app / css["file"]).write_text("body{}", "utf-8")
    problems = vendor.package_check(tmp_path, "shelf")
    assert any("extra.js is not a file" in p for p in problems)
    assert any("tinylib.css is not the file" in p for p in problems)


def test_a_file_named_like_a_library_and_nothing_more_is_not_carried(tmp_path):
    # the application's own stand-in, named like jQuery Validation: no banner, no version, not
    # from a package folder or a public library host
    shim = (
        b"(function($){ $.fn.validate = function(o){ window.opts = o; return this; }; })(jQuery);"
    )
    rec = _recording(tmp_path / "rec", extra={
        "/static/lib/jquery.validate-shim.js": ("application/javascript", shim)})  # fmt: skip
    doc = vendor.write(tmp_path, "shelf", [rec])
    refused = {x["name"]: x["reason"] for x in doc["not_carried"]}
    assert "named only by its file name" in refused["jQuery Validation"]
    assert not list((tmp_path / "apps" / "shelf" / "vendor").rglob("*shim*"))


def test_libraries_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv(vendor.SWITCH_ENV, "off")
    doc = vendor.write(tmp_path, "shelf", [_recording(tmp_path / "rec")])
    assert doc["libraries"] == [] and all("switched off" in x["reason"] for x in doc["not_carried"])
    assert not (tmp_path / "apps" / "shelf" / vendor.NOTICES).exists()


# =================================================================================================
# A recording of the fixture, through the brief, the look and the package
# =================================================================================================

APP = "shelf"


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from labctl import recorder

    rec = tmp_path_factory.mktemp("vendorapp-rec") / "shelf"
    run = vendorapp.serve()
    try:
        recorder.record(run.base + "/shelf", rec, headless=True, script=vendorapp.flow)
    finally:
        run.stop()
    return rec


def build(recording: Path, root: Path) -> SimpleNamespace:
    from labctl import multiapp

    for d in ("apps", "automations", "lab", "flows", "docs"):
        (root / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", root / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", root / "docs" / "factory-playbook.md")
    rec = multiapp.recordings_dir(root) / "shelf"
    shutil.copytree(recording, rec)
    multiapp.build_all(root, rec, say=lambda m: None)
    proposal = multiapp.proposal_file(rec)
    data = yaml.safe_load(proposal.read_text("utf-8"))
    data["apps"][0]["id"] = APP
    proposal.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    summary = multiapp.build_all(root, rec, proposal=proposal, say=lambda m: None,
                                 package_dir=root / "packages")  # fmt: skip
    (result,) = summary.apps
    assert result.errors == [] and result.package, result.errors
    with zipfile.ZipFile(root / result.package) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    return SimpleNamespace(root=root, files=files)


@pytest.fixture(scope="module")
def built(recording, tmp_path_factory):
    patch = pytest.MonkeyPatch()
    for var in ("TWINLAB_PAGE_TEXT", lookfeel.FONTS_ENV, lookfeel.SWITCH_ENV, vendor.SWITCH_ENV):
        patch.delenv(var, raising=False)
    try:
        yield build(recording, tmp_path_factory.mktemp("vendor") / "repo")
    finally:
        patch.undo()


@pytest.fixture(scope="module")
def bundled(recording, tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setenv(lookfeel.FONTS_ENV, "bundle")
    try:
        yield build(recording, tmp_path_factory.mktemp("vendor-bundle") / "repo")
    finally:
        patch.undo()


def test_the_package_carries_the_library_and_the_licensed_font(built):
    files = built.files
    app = f"apps/{APP}/"
    assert (
        files[app + "vendor/tinylib/1.2.3/dist/css/tinylib.css"] == vendorapp.TINYLIB_CSS.encode()
    )
    assert (
        files[app + "vendor/tinylib/1.2.3/dist/js/tinylib.min.js"] == vendorapp.TINYLIB_JS.encode()
    )
    assert app + "vendor/tinylib/1.2.3/dist/fonts/tinyicons.woff" in files
    assert not any(
        n.startswith(app + "vendor/") and ("mystery" in n or "leaky" in n) for n in files
    )
    assert not any(n.startswith(app + "scripts/") or n.endswith("shelf.js") for n in files)
    assert all(vendorapp.SECRET.encode() not in data for data in files.values())
    # the licensed font is carried, the one without a licence is not
    assert (
        files[app + "look/fonts/shelfsans.woff"]
        == vendorapp.STATIC["/static/fonts/shelfsans.woff"][1]
    )
    assert not any(n.endswith("plaingrotesk.ttf") for n in files)
    css = files[app + "look/css/shelf.css"].decode()
    assert 'url("../fonts/shelfsans.woff")' in css and "url(#font-not-carried)" in css
    manifest = json.loads(files[app + "look/manifest.json"])
    assert manifest["font_mode"] == "licensed" and manifest["fonts_carried"] == [
        "fonts/shelfsans.woff",
        "fonts/tinyicons.woff",
    ]
    fonts = {f["family"]: f for f in manifest["fonts"]}
    shelf, plain = fonts[vendorapp.FAMILY], fonts[vendorapp.PLAIN]
    assert shelf["bundled"] and shelf["files"][0]["licence"] == "OFL-1.1"
    assert not plain["bundled"] and "could not be identified" in plain["files"][0]["not_carried"]
    assert plain["to_do"].startswith("Not copied (fonts are licensed). Its licence could not")
    # the pictures were rendered with the carried font, and without the other one
    assert shelf["loaded_in_pictures"] is True and plain["loaded_in_pictures"] is False


def test_the_notices_the_manifest_and_the_index_name_every_carried_file(built):
    files = built.files
    app = f"apps/{APP}/"
    notices = files[app + vendor.NOTICES].decode()
    assert "### tinylib 1.2.3" in notices and "## Web fonts" in notices
    assert f"| {vendorapp.FAMILY} | `{app}look/fonts/shelfsans.woff`" in notices
    assert "SIL Open Font License 1.1 (`OFL-1.1`), from the font's own name table" in notices
    assert hashlib.sha256(files[app + "look/fonts/shelfsans.woff"]).hexdigest() in notices
    assert "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007" in notices
    assert "Permission is hereby granted, free of charge" in notices
    assert "Warning" not in notices
    listing = {f["path"]: f for f in json.loads(files["MANIFEST.json"])["files"]}
    for rel in (app + vendor.NOTICES, app + "vendor/vendor.json", app + "vendor/tinylib/1.2.3/dist/css/tinylib.css",
                app + "look/fonts/shelfsans.woff"):  # fmt: skip
        assert listing[rel]["sha256"] == hashlib.sha256(files[rel]).hexdigest()
    assert listing[app + "vendor/tinylib/1.2.3/dist/css/tinylib.css"]["purpose"].startswith(
        "A file of the open-source library tinylib 1.2.3"
    )
    assert listing[app + vendor.NOTICES]["purpose"].startswith("The licences of the open-source")
    index = files["INDEX.md"].decode()
    assert f"| `{app}{vendor.NOTICES}` | The licences" in index
    assert f"| `{app}look/fonts/shelfsans.woff` | A web font file" in index


def test_the_brief_the_documents_and_the_task_say_to_use_these_files(built):
    files = built.files
    app = f"apps/{APP}"
    brief = files[f"{app}/BRIEF.md"].decode()
    assert f"## {vendor.HEADING}" in brief and "| tinylib | 1.2.3 | MIT |" in brief
    assert "Use these exact library files and versions" in brief
    assert "mystery" in brief and "leaky" in brief and "`/static/js/shelf.js`" in brief
    arch = files[f"{app}/spec/ARCHITECTURE.md"].decode()
    assert "### Client libraries and fonts carried" in arch and "| tinylib | 1.2.3 | MIT |" in arch
    constitution = files[f"{app}/spec/CONSTITUTION.md"].decode()
    assert (
        f"The exact third-party library files and versions are available in `{app}/vendor/`"
        in constitution
    )
    assert f"The web fonts are in `{app}/look/fonts/`." in constitution
    page = next(v.decode() for k, v in files.items() if k.startswith(f"{app}/spec/PAGES/"))
    assert "Third-party files this page loads" in page
    assert (
        f"`/node_modules/tinylib/dist/js/tinylib.min.js`: tinylib 1.2.3 (`{app}/vendor/tinylib/1.2.3/dist/js/tinylib.min.js`)"
        in page
    )
    assert f"- Web fonts: `{app}/look/fonts/`" in page
    task = files["BUILD_TASK.md"].decode()
    assert "## Same libraries and fonts" in task and f"`{app}/vendor/tinylib/1.2.3/`" in task
    assert f"The exact library files are available in `{app}/vendor/` (tinylib 1.2.3" in task
    assert f"`{app}/{vendor.DIR}/**`, `{app}/{vendor.NOTICES}`" in task


def test_bundle_carries_a_font_without_a_licence_with_a_warning(built, bundled):
    files = bundled.files
    app = f"apps/{APP}/"
    assert (
        files[app + "look/fonts/plaingrotesk.ttf"]
        == vendorapp.STATIC["/static/fonts/plaingrotesk.ttf"][1]
    )
    manifest = json.loads(files[app + "look/manifest.json"])
    plain = next(f for f in manifest["fonts"] if f["family"] == vendorapp.PLAIN)
    assert plain["bundled"] and plain["loaded_in_pictures"] is True
    assert "carried on the owner's request" in plain["files"][0]["warning"]
    notices = files[app + vendor.NOTICES].decode()
    assert "**not identified**" in notices and "**Warning:**" in notices
    # the picture differs from the one drawn without that font: the carried font is what it shows
    shots = [n for n in files if n.startswith(app + "look/screens/") and n.endswith(".png")]
    assert shots and any(files[n] != built.files.get(n) for n in shots)
