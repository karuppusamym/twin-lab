"""What the network shows beyond the endpoint contract (labctl.netfacts), on HAR files written
here: headers by name, the CSRF token and where it came from, body formats (form, multipart,
GraphQL, JSON-RPC, SOAP), caching, CORS, paging envelopes, dependencies between calls, timing,
retries, streams (server-sent events, WebSocket, long polling), static files, redirect chains
and third parties. No browser; every value written into the HAR must stay out of the facts."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from labctl import netfacts as nf

START = datetime(2026, 11, 18, 9, 0, 0)
ORIGIN = "https://shop.example.test"
CSRF = "tok3n-Value-9f8e7d6c5b4a"
CURSOR = "eyJvIjogOH0"
SESSION = "c00kie-value-123456"


def _h(d: dict) -> list[dict]:
    return [{"name": k, "value": v} for k, v in d.items()]


def entry(n: float, method: str, url: str, status: int = 200, *, mime: str = "application/json",
          body=None, req: dict | None = None, resp: dict | None = None, post=None,
          ms: float = 20, wait: float = 15, transfer: int | None = 500, ws=None) -> dict:  # fmt: skip
    text = body if isinstance(body, str) or body is None else json.dumps(body)
    e = {
        "startedDateTime": (START + timedelta(seconds=n)).isoformat() + "Z",
        "time": ms,
        "request": {"method": method, "url": url, "headers": _h(req or {}), "cookies": []},
        "response": {"status": status, "headers": _h(resp or {}), "cookies": [],
                     "content": {"mimeType": mime, "size": len(text or ""),
                                 **({"text": text} if text is not None else {})},
                     "_transferSize": transfer},
        "timings": {"wait": wait},
    }  # fmt: skip
    if post is not None:
        e["request"]["postData"] = post
    if ws is not None:
        e["_webSocketMessages"] = ws
    return e


PAGE = (f'<html><head><meta name="csrf-token" content="{CSRF}"></head><body>'
        f'<div id="product" data-sku="SKU-4001"></div><div id="more" data-cursor="{CURSOR}">'
        "</div></body></html>")  # fmt: skip
GQL = ('query ProductReviews($sku: ID!, $first: Int = 2) { product(sku: $sku, tag: "Sunny") '
       "{ reviews(first: $first) { edges { node { id author } } pageInfo { endCursor } } } }")  # fmt: skip
BOUNDARY = "----WebKitFormBoundaryX1"
MULTI = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"rating\"\r\n\r\n4\r\n"
         f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"Ines-Calvo.png\""
         f"\r\nContent-Type: image/png\r\n\r\nPNGDATA123\r\n--{BOUNDARY}--\r\n")  # fmt: skip
SOAP = ('<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body><GetQuote><Symbol>Margarethe Holm</Symbol><Count>5</Count></GetQuote>"
        "</soap:Body></soap:Envelope>")  # fmt: skip
API = {"Accept": "application/json", "X-Shop-Client": "web/1.4", "Cookie": f"sid={SESSION}",
       "User-Agent": "Mozilla/5.0"}  # fmt: skip


def har_entries() -> list[dict]:
    return [
        entry(0, "GET", f"{ORIGIN}/p/amber-linen-dress", mime="text/html", body=PAGE,
              resp={"Cache-Control": "private, no-cache", "Set-Cookie": f"sid={SESSION}; Path=/"},
              req={"Sec-Fetch-Dest": "document"}),
        entry(0.5, "GET", f"{ORIGIN}/api/products?category=all&cursor={CURSOR}&limit=8",
              body={"items": [{"sku": "SKU-4002", "title": "Cobalt Wool Scarf"}],
                    "next_cursor": "eyJvIjogMTZ9", "total": 30},
              req=API, resp={"Link": '</api/products?cursor=eyJvIjogMTZ9>; rel="next"',
                             "X-Total-Count": "30", "Content-Encoding": "gzip",
                             "Cache-Control": "no-store", "Vary": "Accept-Encoding"}, ms=40),
        entry(1.5, "GET", f"{ORIGIN}/api/products?category=all&cursor=eyJvIjogMTZ9&limit=8",
              body={"items": [], "next_cursor": None, "total": 30}, req=API, ms=60, wait=50),
        entry(2, "GET", f"{ORIGIN}/api/products/SKU-4001/variant?colour=red&size=M",
              body={"sku": "SKU-4001", "price": 41.5}, req=API),
        entry(3, "POST", f"{ORIGIN}/api/cart/items", 503, body={"error": "Busy."},
              req={**API, "X-CSRF-Token": CSRF, "Content-Type": "application/json"},
              resp={"Retry-After": "1"},
              post={"mimeType": "application/json", "text": '{"sku": "SKU-4001", "qty": 1}'}),
        entry(3.4, "POST", f"{ORIGIN}/api/cart/items", 201, body={"count": 1},
              req={**API, "X-CSRF-Token": CSRF, "Content-Type": "application/json"},
              post={"mimeType": "application/json", "text": '{"sku": "SKU-4001", "qty": 1}'}),
        entry(4, "POST", f"{ORIGIN}/api/wishlist", body={"count": 1},
              post={"mimeType": "application/x-www-form-urlencoded",
                    "text": f"sku=SKU-4001&_csrf={CSRF}"}),
        entry(5, "POST", f"{ORIGIN}/graphql",
              body={"data": {"product": {"reviews": {"edges": [{"node": {"id": "REV-7001",
                    "author": "Ilse Varga"}}], "pageInfo": {"endCursor": "Y3Vyc29yOjI"}}}}},
              req={**API, "Content-Type": "application/json"},
              post={"mimeType": "application/json", "text": json.dumps(
                  {"operationName": "ProductReviews", "query": GQL,
                   "variables": {"sku": "SKU-4001", "first": 2}})}),
        entry(6, "POST", f"{ORIGIN}/api/reviews", 201, body={"id": "REV-8001"},
              req={**API, "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
              post={"mimeType": f"multipart/form-data; boundary={BOUNDARY}", "text": MULTI}),
        entry(7, "POST", f"{ORIGIN}/rpc", body={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
              post={"mimeType": "application/json", "text": json.dumps(
                  {"jsonrpc": "2.0", "id": 1, "method": "cart.recalculate",
                   "params": {"basket": "Holm Margarethe"}})}),
        entry(8, "POST", f"{ORIGIN}/soap/quote", mime="text/xml", body="<ok/>",
              req={"SOAPAction": '"http://intranet.corp.example/Quote#GetQuote"'},
              post={"mimeType": "text/xml", "text": SOAP}),
        entry(9, "GET", f"{ORIGIN}/api/cart", 304, mime="application/json", body=None,
              req={**API, "If-None-Match": '"abc123"'}, resp={"ETag": 'W/"abc123"'}),
        entry(10, "GET", f"{ORIGIN}/api/price-stream?sku=SKU-4001", mime="text/event-stream",
              body='retry: 5000\n\nid: 1\nevent: price\ndata: {"sku": "SKU-4001", "price": 41.5}\n\n'
                   'event: price\ndata: {"sku": "SKU-4001", "price": 42.0}\n\n'),
        entry(11, "GET", "wss://shop.example.test/live", 101, mime="", ws=[
            {"type": "send", "opcode": 1, "data": json.dumps({"type": "subscribe", "sku": "SKU-4001"})},
            {"type": "receive", "opcode": 1, "data": json.dumps({"type": "tick", "price": 43.0})},
            {"type": "receive", "opcode": 1, "data": json.dumps({"type": "tick", "price": 44.0})}]),
        *[entry(12 + k * 3, "GET", f"{ORIGIN}/api/inbox/poll", body={"messages": []}, req=API,
                ms=2500, wait=2400) for k in range(3)],
        entry(22, "GET", f"{ORIGIN}/static/app.css?v=3", mime="text/css", body="body{}",
              resp={"Cache-Control": "public, max-age=86400", "ETag": '"e1"',
                    "Last-Modified": "Tue, 17 Nov 2026 10:00:00 GMT"}),
        entry(23, "GET", f"{ORIGIN}/static/app.css?v=3", mime="text/css", body="body{}",
              transfer=0),
        entry(24, "GET", f"{ORIGIN}/img/p/SKU-4001/red.jpg", mime="image/jpeg", body="x" * 300),
        entry(25, "GET", "https://www.google-analytics.com/g/collect?v=2&tid=G-12345", mime="",
              body="", status=204),
        entry(26, "GET", "https://tags.private-vendor.example.com/t.js", mime="application/javascript",
              body="window.t=1"),
        entry(27, "POST", "https://tags.private-vendor.example.com/track", 204, mime="", body="",
              resp={"Access-Control-Allow-Origin": ORIGIN, "Access-Control-Allow-Credentials": "true"},
              post={"mimeType": "text/plain", "text": '{"e": "Margarethe"}'}),
        entry(28, "GET", f"{ORIGIN}/go/women", 301, mime="text/html", body="",
              resp={"Location": "/category/women"}, req={"Sec-Fetch-Dest": "document"}),
        entry(28.1, "GET", f"{ORIGIN}/category/women", 302, mime="text/html", body="",
              resp={"Location": "/c/women"}, req={"Sec-Fetch-Dest": "document"}),
        entry(28.2, "GET", f"{ORIGIN}/c/women", mime="text/html", body="<html></html>",
              req={"Sec-Fetch-Dest": "document"}),
    ]  # fmt: skip


def fake_ctx(tmp_path: Path, entries: list[dict]):
    rec = tmp_path / "rec"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": entries}}), "utf-8")
    raw = [{"seq": 1, "ts": START.isoformat() + "Z", "url": f"{ORIGIN}/p/amber-linen-dress",
            "action": "goto"},
           {"seq": 2, "ts": (START + timedelta(seconds=1.4)).isoformat() + "Z",
            "url": f"{ORIGIN}/p/amber-linen-dress", "action": "click", "selector": "button#more"},
           {"seq": 3, "ts": (START + timedelta(seconds=5.9)).isoformat() + "Z",
            "url": f"{ORIGIN}/p/amber-linen-dress", "action": "upload", "selector": "input#photo",
            "files": [{"name_shape": "A-A.png", "type": "image/png", "size": "<1KB"}]}]  # fmt: skip
    page = '<form><input id="photo" name="photo" type="file" accept="image/png,image/jpeg"></form>'
    part = SimpleNamespace(path=rec, raw=raw, html_at=lambda seq: page)
    steps = [{"n": 1, "action": "goto"}, {"n": 2, "action": "click", "selector": "button#more"},
             {"n": 3, "action": "upload"}]  # fmt: skip
    return SimpleNamespace(name="rec", part=part, steps=lambda: steps)


def endpoint_docs() -> dict[str, dict]:
    routes = [("GET", "/api/products"), ("GET", "/api/products/{id}/variant"),
              ("POST", "/api/cart/items"), ("POST", "/api/wishlist"), ("POST", "/graphql"),
              ("POST", "/api/reviews"), ("POST", "/rpc"), ("POST", "/soap/quote"),
              ("GET", "/api/cart"), ("GET", "/api/inbox/poll")]  # fmt: skip
    out = {}
    for method, path in routes:
        doc = {"method": method, "path": path, "calls": [], "responses": {}}
        if path == "/api/products":
            doc["calls"] = [{"recording": "rec", "step": 1, "query": {"cursor": "x1"}},
                            {"recording": "rec", "step": 2, "query": {"cursor": "x2"}}]  # fmt: skip
        if path == "/graphql":
            doc["calls"] = [{"request": {"operationName": "Avery", "query": "Kestrel Larkspur",
                                         "variables": {"sku": "SKU-4001"}}}]  # fmt: skip
            doc["request"] = {"example": dict(doc["calls"][0]["request"])}
        out[nf._field_name(f"{method.lower()}{path.replace('/', '-')}") + ".json"] = doc
    return out


def built(tmp_path):
    ctx = fake_ctx(tmp_path, har_entries())
    eps = endpoint_docs()
    return nf.build([ctx], eps), eps, ctx


def by_path(found: dict, eps: dict, method: str, path: str) -> dict:
    name = next(n for n, d in eps.items() if d["method"] == method and d["path"] == path)
    return found["endpoints"][name]


def test_headers_are_named_and_classified_never_given(tmp_path):
    found, eps, _ = built(tmp_path)
    cart = by_path(found, eps, "POST", "/api/cart/items")
    heads = cart["request_headers"]
    assert heads["Content-Type"] == ["application/json"]
    assert heads["X-CSRF-Token"] == "the CSRF token (see `csrf`)"
    assert heads["X-Shop-Client"].startswith("the same on every call (a value of shape `A/9.9`)")
    assert "Cookie" not in heads and "User-Agent" not in heads
    csrf = cart["csrf"][0]
    assert csrf["name"] == "X-CSRF-Token" and csrf["in"] == "header"
    assert csrf["comes_from"] == '`<meta name="csrf-token">` of the page `/p/amber-linen-dress`'
    wish = by_path(found, eps, "POST", "/api/wishlist")
    assert wish["csrf"][0]["name"] == "_csrf" and wish["csrf"][0]["in"] == "form"
    assert wish["body"] == {
        "format": "application/x-www-form-urlencoded",
        "fields": ["sku", "_csrf"],
    }


def test_body_formats_multipart_graphql_jsonrpc_soap(tmp_path):
    found, eps, _ = built(tmp_path)
    multi = by_path(found, eps, "POST", "/api/reviews")["body"]
    assert multi["format"] == "multipart/form-data"
    photo = multi["fields"]["photo"]
    assert photo["kind"] == "file" and photo["content_type"] == "image/png"
    assert photo["extension"] == "png" and photo["bytes"] == 10
    assert photo["accept"] == "image/png,image/jpeg" and photo["size_class"] == "<1KB"
    assert multi["fields"]["rating"] == {"kind": "text"}
    gql = by_path(found, eps, "POST", "/graphql")
    op = gql["graphql"]["operations"]["ProductReviews"]
    assert op["type"] == "query" and op["calls"] == 1
    assert op["variables"]["sku"] == {"type": "ID!", "json": "string"}
    assert op["variables"]["first"]["type"] == "Int"
    sel = op["selection"]['product(sku: $sku, tag: "...")']
    assert sel["reviews(first: $first)"]["edges"]["node"] == {"id": None, "author": None}
    assert op["data"]["product"]["reviews"]["pageInfo"] == {"endCursor": "string"}
    rpc = by_path(found, eps, "POST", "/rpc")
    assert rpc["body"]["format"] == "JSON-RPC"
    assert rpc["json_rpc"]["methods"]["cart.recalculate"]["params"] == {"basket": "string"}
    soap = by_path(found, eps, "POST", "/soap/quote")["body"]
    assert soap["format"] == "SOAP" and soap["operation"] == "GetQuote"
    assert soap["elements"] == ["Symbol", "Count"] and soap["soap_action"] == "GetQuote"
    text = nf.graphql_text(GQL)
    assert '"..."' in text and "Sunny" not in text and "$first: Int = 0" in text


def test_caching_compression_cors_paging_and_query_roles(tmp_path):
    found, eps, _ = built(tmp_path)
    lst = by_path(found, eps, "GET", "/api/products")
    ok = lst["answers"]["200"]
    assert ok["compression"] == ["gzip"] and ok["caching"]["vary"] == ["Accept-Encoding"]
    assert ok["headers"]["X-Total-Count"].startswith("a number: the count of all rows")
    assert ok["headers"]["Link"] == "paging links, rel next"
    paging = lst["paging"]
    assert paging["next"] == ["next_cursor"] and paging["total"] == ["header X-Total-Count",
                                                                       "total"]  # fmt: skip
    assert paging["last_page_seen"].startswith("yes") and paging["link_header"] == ["next"]
    assert any("a click on `button#more`" in a for a in paging["asked_by"])
    roles = lst["query_roles"]
    assert roles["cursor"].startswith("cursor: the value of") and roles["limit"] == "page size"
    assert roles["category"].startswith("filter or option")
    variant = by_path(found, eps, "GET", "/api/products/{id}/variant")
    assert variant["query_roles"]["size"].startswith("filter or option")  # size=M: a choice
    cart = by_path(found, eps, "GET", "/api/cart")["answers"]["304"]
    assert cart["caching"]["etag"] == "weak" and cart["body"] == "empty"
    assert cart["conditional_requests"].startswith("1 of 1")
    static = found["network"]["static_files"]["style"]
    assert static["from_browser_cache"] == 1 and static["requests"] == 2
    css = static["files"]["/static/app.css?v="]
    assert css["caching"] == {"cache_control": "public, max-age=86400", "etag": "strong",
                              "last_modified": "present"}  # fmt: skip
    assert "/img/p/{id}/{file}.jpg" in found["network"]["static_files"]["image"]["files"]


def test_dependencies_timing_and_retries(tmp_path):
    found, eps, _ = built(tmp_path)
    lst = by_path(found, eps, "GET", "/api/products")
    deps = {(d["field"], d["from"]) for d in lst["depends_on"]}
    assert (
        "query.cursor",
        "`data-cursor` of `div#more` on the page `/p/amber-linen-dress`",
    ) in deps
    assert ("query.cursor", "GET /api/products answer `next_cursor`") in deps
    variant = by_path(found, eps, "GET", "/api/products/{id}/variant")
    assert variant["depends_on"][0]["from"].startswith("`data-sku` of `div#product`")
    assert lst["time_ms"] == {"median": 50, "max": 60, "calls": 2, "server_wait_median": 32}
    cart = by_path(found, eps, "POST", "/api/cart/items")
    assert cart["retries"] == [{"after": 503, "delay_ms": 400, "then": 201,
                                "retry_after_header": True}]  # fmt: skip
    assert cart["role"] == "cart state"
    net = found["network"]
    assert net["retries"][0]["endpoint"] == "POST /api/cart/items"
    assert {(e["endpoint"], e["status"]) for e in net["errors"]} == {("POST /api/cart/items", 503)}


def test_streams_redirects_and_third_parties(tmp_path):
    found, _, _ = built(tmp_path)
    net = found["network"]
    streams = {s["endpoint"]: s for s in net["streams"]}
    sse = streams["GET /api/price-stream"]
    assert sse["type"] == "server-sent events" and sse["retry_ms"] == 5000
    assert sse["events"]["price"] == {"count": 2, "data": {"sku": "string", "price": "number"},
                                      "id": True}  # fmt: skip
    ws = streams["GET /live"]
    assert ws["type"] == "WebSocket"
    assert ws["events"]["sent type=subscribe"]["data"] == {"type": "string", "sku": "string"}
    assert ws["events"]["received type=tick"]["count"] == 2
    poll = streams["GET /api/inbox/poll"]
    assert poll["type"] == "long polling" and poll["server_wait_median_ms"] == 2400
    chain = net["redirect_chains"][0]
    assert chain["start"] == "GET /go/women" and chain["ends"] == "200 /c/women"
    assert [h["status"] for h in chain["hops"]] == [301, 302]
    third = {t["origin"]: t for t in net["third_parties"]}
    ga = third["google-analytics.com"]
    assert ga["vendor"] == "Google Analytics" and ga["purpose"] == "analytics"
    assert ga["calls"] == ["GET /g/collect?tid=&v="]
    other = third["third-party-1"]
    assert other["purpose"] == "analytics or tracking" and "vendor" not in other
    assert other["cors"] == {"allow_origin": "the page's origin", "allow_credentials": True}
    assert "Never call it" in other["build"]
    assert set(net["_vendors"]) == {"google-analytics.com"}


def test_no_value_of_the_recording_is_in_the_facts(tmp_path):
    found, _, _ = built(tmp_path)
    text = json.dumps(found["endpoints"]) + json.dumps(found["network"])
    for value in (CSRF, SESSION, CURSOR, "eyJvIjogMTZ9", "Cobalt Wool Scarf", "Ilse Varga",
                  "Margarethe", "Holm", "Sunny", "PNGDATA123", "Ines-Calvo", "G-12345",
                  "private-vendor", "intranet.corp", "41.5", "abc123", "SKU-4002"):  # fmt: skip
        assert value not in text, value


def test_extend_writes_into_the_endpoint_files_and_fixes_graphql_examples(tmp_path):
    ctx = fake_ctx(tmp_path, har_entries())
    eps = endpoint_docs()
    built_doc = {"endpoints": eps}
    problems = nf.extend(tmp_path, "shop", [ctx], built_doc)
    assert problems == [] and "network" in built_doc
    gql = next(d for d in eps.values() if d["path"] == "/graphql")
    req = gql["calls"][0]["request"]
    assert req["operationName"] == "ProductReviews" and req["query"].startswith(
        "query ProductReviews($sku: ID!")  # fmt: skip
    assert gql["request"]["example"]["query"] == req["query"]
    assert all("network" in d for d in eps.values())
    lines = nf.endpoint_lines(
        next(d for d in eps.values() if d["path"] == "/api/products")["network"]
    )
    assert any(x.startswith("answers in 50 ms") for x in lines)
    assert any("takes `query.cursor` from" in x for x in lines)
    arch = "\n".join(nf.architecture_lines(built_doc["network"], "apps/shop"))
    assert "## Network behaviour" in arch and "Redirect chain `GET /go/women`" in arch
    assert "| style | 2 | 1 |" in arch


def test_a_host_of_the_recording_that_is_not_a_known_vendor_is_never_written(tmp_path):
    entries = [entry(0, "GET", "https://api.internal-bank.example.org/v1/rates", body={"a": 1})]
    ctx = fake_ctx(tmp_path, entries)
    found = nf.build([ctx], {})
    assert found["network"]["third_parties"][0]["origin"] == "third-party-1"
    assert "internal-bank" not in json.dumps(found)


def test_websocket_binary_and_base64_bodies_are_read_safely():
    raw = nf.read({"log": {"entries": [entry(0, "GET", f"{ORIGIN}/a", body=None)]}}, set())
    assert raw[0].own and raw[0].text is None
    content = {"text": base64.b64encode(b'{"x": 1}').decode(), "encoding": "base64"}
    assert nf._decoded(content) == '{"x": 1}'
    assert nf.value_kind("8c1f0f3e-1d2b-4c5d-9e8f-0a1b2c3d4e5f") == "a UUID"
    assert nf.value_kind("123456") == "a number"


JSF_PAGE = ('<html><body><form id="main" method="post" action="/workspace.xhtml">'
            '<input type="hidden" name="javax.faces.ViewState" value="-1234567890:987654321">'
            "</form></body></html>")  # fmt: skip
PARTIAL_ANSWER = ('<?xml version="1.0" encoding="UTF-8"?><partial-response><changes>'
                  '<update id="main:cases"><![CDATA[<div id="main:cases"><h2>Cases</h2></div>]]>'
                  '</update><update id="j_id1:javax.faces.ViewState:0"><![CDATA[-555:777888999]]>'
                  "</update><eval><![CDATA[PF('casesDlg').show();]]></eval>"
                  '<extension ln="primefaces" type="args">{"validationFailed":true}</extension>'
                  "</changes></partial-response>")  # fmt: skip


def jsf_post(n: float, state: str) -> dict:
    form = (f"javax.faces.partial.ajax=true&javax.faces.source=main%3AopenCase&"
            f"javax.faces.partial.execute=main%3AopenCase&javax.faces.partial.render=main%3Acases"
            f"&main=main&main%3Aq=Margarethe&javax.faces.ViewState={state}")  # fmt: skip
    return entry(n, "POST", f"{ORIGIN}/workspace.xhtml", mime="text/xml", body=PARTIAL_ANSWER,
                 req={"Faces-Request": "partial/ajax"},
                 post={"mimeType": "application/x-www-form-urlencoded; charset=UTF-8",
                       "text": form})  # fmt: skip


def test_jsf_partial_requests_and_answers_are_described_by_client_id(tmp_path):
    entries = [entry(0, "GET", f"{ORIGIN}/login.xhtml", mime="text/html", body=JSF_PAGE,
                     req={"Sec-Fetch-Dest": "document"}),
               jsf_post(1, "-1234567890%3A987654321"), jsf_post(2, "-555%3A777888999")]  # fmt: skip
    ctx = fake_ctx(tmp_path, entries)
    eps = {"post-workspace-xhtml.json": {"method": "POST", "path": "/workspace.xhtml",
                                         "calls": [], "responses": {}}}  # fmt: skip
    found = nf.build([ctx], eps)
    facts = found["endpoints"]["post-workspace-xhtml.json"]
    body = facts["body"]
    assert body["format"] == "JSF partial request (ajax)"
    assert body["source"] == "main:openCase" and body["render"] == ["main:cases"]
    assert body["fields"] == ["main", "main:q"] and "javax.faces.ViewState" in body["faces_fields"]
    answer = facts["answers"]["200"]["jsf_partial_response"]
    assert answer["updates"] == ["main:cases"] and answer["ignored"] == [
        "j_id1:javax.faces.ViewState:0"]  # fmt: skip
    assert answer["scripts_run"] == ["PF('...').show"] and answer["callback_args"] == [
        "validationFailed"]  # fmt: skip
    assert answer["validation_failed"] == "2 of 2 answer(s)"
    csrf = {c["comes_from"] for c in facts["csrf"]}
    assert "the hidden field `javax.faces.ViewState` of the page `/login.xhtml`" in csrf
    assert "POST /workspace.xhtml partial answer `<update id=...ViewState>`" in csrf
    assert facts["request_headers"]["Faces-Request"].startswith("the same on every call")
    text = json.dumps(found["endpoints"]) + json.dumps(found["network"])
    assert "987654321" not in text and "777888999" not in text and "Margarethe" not in text
    lines = " | ".join(nf.endpoint_lines(facts))
    assert "is a JSF partial request (ajax): source `main:openCase`" in lines
    assert "JSF partial response updating `main:cases`" in lines
    assert nf.extend(tmp_path, "jsf", [ctx], {"endpoints": eps}) == []
