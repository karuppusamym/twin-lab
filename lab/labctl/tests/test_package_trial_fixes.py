"""The package defects a public-shop trial (static pages, an API origin, a media origin) found.

Synthetic data only: hosts under `example.test`, products and texts made up here.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from labctl import apicatalog, har, lookfeel, netfacts, sanitize
from tests.test_netfacts import entry, fake_ctx

WWW = "https://www.shop.example.test"
API = "https://api.shop.example.test"
MEDIA = "https://media.shop.example.test"

SERVED = ('<html><body><div class="row" id="cards">\n  </div>'
          '<div id="footer"><p>About us</p></div></body></html>')  # fmt: skip
FILLED = ('<html><body><div class="row" id="cards"><div class="card"><a class="title">'
          "Amber linen lamp</a><p>A tall lamp of amber linen.</p></div><div class=\"card\">"
          '<a class="title">Cobalt reading chair</a></div></div><div id="footer"><p>About us</p>'
          "</div></body></html>")  # fmt: skip
ANSWER = {"Items": [{"id": 1, "title": "Amber linen lamp", "desc": "A tall lamp of amber linen."},
                    {"id": 2, "title": "Cobalt reading chair", "desc": "A chair to read in."}]}  # fmt: skip


# --- 1: static pages filled in the browser from the API ----------------------------------------


def test_static_pages_filled_from_api_answers_are_told_from_server_rendered():
    found = har.client_rendering([("/", SERVED)], [("/", FILLED)], [("GET /entries", ANSWER)])
    assert found is not None
    assert found["containers"] == ["cards"] and found["calls"] == ["GET /entries"]
    assert found["values"] >= 2 and "`#cards`" in found["marker"]
    assert "Amber" not in found["marker"]  # names and counts only, never a value


def test_pages_served_with_their_content_stay_server_rendered():
    assert (
        har.client_rendering([("/", FILLED)], [("/", FILLED)], [("GET /entries", ANSWER)]) is None
    )
    assert (
        har.client_rendering([("/", SERVED)], [("/", SERVED)], [("GET /entries", ANSWER)]) is None
    )


# --- 2: state-changing requests counted per origin -----------------------------------------------


def _ex(method, host, path, kind="api"):
    return har.Exchange(started=datetime(2026, 11, 19, 9), method=method, host=host, path=path,
                        query=[], request={}, req_mime="", req_fields=[], status=200,
                        resp_mime="application/json", resp_json=None, resp_text=None, ms=5,
                        kind=kind)  # fmt: skip


def test_writes_to_the_api_origin_are_not_said_to_go_to_the_pages_origin():
    entries = [_ex("POST", "api.shop.example.test", "/addtocart"),
               _ex("POST", "api.shop.example.test", "/view"),
               _ex("POST", "www.shop.example.test", "/contact", kind="form")]  # fmt: skip
    out = har.data_location(entries, [], [], api_origins={"api.shop.example.test"})
    ev = " | ".join(out["evidence"])
    assert "2 state-changing request(s) to the application's API origin" in ev
    assert "1 state-changing request(s) to the app's own origin: POST /contact" in ev


# --- 3: a media origin the application's configuration names -----------------------------------


def _media_entries():
    return [
        entry(0, "GET", f"{WWW}/", mime="text/html", body="<html><body></body></html>"),
        entry(
            0.1, "GET", f"{WWW}/config.json", body={"API_URL": API, "HLS_URL": MEDIA}
        ),  # fmt: skip
        entry(
            0.2,
            "GET",
            f"{MEDIA}/index.m3u8",
            status=206,
            mime="binary/octet-stream",
            body="#EXTM3U",
        ),  # fmt: skip
        entry(0.3, "GET", f"{MEDIA}/clip00000.ts", status=206, mime="text/plain", body="x"),
        entry(
            0.4,
            "GET",
            "https://tags.tracker.example.net/t.js",
            mime="application/javascript",
            body="var t=1",
        ),  # fmt: skip
    ]


def _media_ctx(tmp_path):
    ctx = fake_ctx(tmp_path, _media_entries())
    for s in ctx.part.raw:
        s["url"] = f"{WWW}/"
    return ctx


def test_a_host_the_configuration_names_is_the_applications_media_origin(tmp_path):
    net = netfacts.build([_media_ctx(tmp_path)], {})["network"]
    media = net["media_origins"]
    assert len(media) == 1 and media[0]["origin"] == "media-origin-1"
    assert media[0]["serves"] == ["an HLS video playlist", "HLS video segments"]
    assert media[0]["named_by"] == ["`HLS_URL` in `/config.json`"]
    assert "stand-in" in media[0]["note"] and "Never call it" not in media[0]["note"]
    # a real third party stays one
    assert [t["origin"] for t in net["third_parties"]] == ["third-party-1"]
    assert "Never call it" in net["third_parties"][0]["build"]
    text = json.dumps(net) + "\n".join(netfacts.architecture_lines(net, "apps/shop"))
    assert "media.shop" not in text and "example.test" not in text
    assert "Media origin `media-origin-1`" in "\n".join(netfacts.brief_lines(net))


def test_a_host_of_another_site_is_never_a_media_origin(tmp_path):
    entries = _media_entries()
    entries[1] = entry(0.1, "GET", f"{WWW}/config.json",
                       body={"HLS_URL": "https://cdn.other.example.org"})  # fmt: skip
    entries[2] = entry(0.2, "GET", "https://cdn.other.example.org/index.m3u8", status=206,
                       mime="binary/octet-stream", body="#EXTM3U")  # fmt: skip
    ctx = fake_ctx(tmp_path, entries)
    for s in ctx.part.raw:
        s["url"] = f"{WWW}/"
    net = netfacts.build([ctx], {})["network"]
    assert net["media_origins"] == []


# --- 4: a static configuration file is a file, not a table ---------------------------------------


def test_static_json_configuration_is_a_file_with_local_addresses(tmp_path):
    assert har.static_json("GET", "/config.json", {"API_URL": API, "HLS_URL": MEDIA})
    assert not har.static_json("GET", "/entries.json", {"Items": [{"id": 1}]})
    assert not har.static_json("POST", "/config.json", {"API_URL": API})
    exs = [SimpleNamespace(status=200, data={"API_URL": API, "HLS_URL": MEDIA})]
    st = apicatalog.static_file("GET", "/config.json", exs)
    assert st["keys"] == ["API_URL", "HLS_URL"] and st["file"] == "static/config.json"
    doc = {"static_file": st, "responses": {"200": {"example": {
        "API_URL": "https://api.example.test", "HLS_URL": "https://hls.example.test"}}}}  # fmt: skip
    (written,) = apicatalog.write_static(tmp_path, "shop", {"get-config-json.json": doc})
    body = json.loads(written.read_text(encoding="utf-8"))
    assert body == {"API_URL": apicatalog.LOCAL_ORIGIN, "HLS_URL": f"{apicatalog.LOCAL_ORIGIN}/hls"}
    assert written == tmp_path / "apps" / "shop" / "static" / "config.json"


# --- 9 and G13: the look ---------------------------------------------------------------------------


def test_a_video_poster_is_an_interface_image_of_the_page():
    read = lookfeel.read_page('<div id="v" poster="imgs/front.jpg"><video poster="imgs/front.jpg">'
                              "</video></div>")  # fmt: skip
    assert [i[0] for i in read.images] == ["imgs/front.jpg", "imgs/front.jpg"]
    assert "video poster" in read.images[0][2]


def test_style_text_stays_raw_in_snapshots_and_inline_css():
    page = sanitize.page("<html><head><style>.nav>.c a{color:red}.x:after{content:''}</style>"
                         "</head><body><p>Hello</p></body></html>")  # fmt: skip
    assert ".nav>.c a{color:red}" in page.html and "&gt;" not in page.html
