"""Screens as templates (labctl.templates): addresses of one structure become one screen, the
layout every screen shares is listed once, and each template says how many instances were seen,
what varies, its data fields with one synthetic seed row per instance, and its parts
(breadcrumbs, gallery, variant picker, facets, recommendations, lists that load more).

Unit tests first, on pages written here. Then the fixture shop (fixtures/shop: 30 products from
one template, a mega-menu, facets, infinite scroll, GraphQL, multipart, server-sent events, a
third party), recorded and taken to a package: one template per kind of page, the network facts
in the endpoint files and `api/network.json`, and no recorded value anywhere. A long session
(100+ steps, 20+ products) runs with `TWINLAB_SCALE_TEST=1`: what it measures is in
docs/06-evidence.md."""

from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from labctl import sanitize
from labctl import templates as tp

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "fixtures" / "shop"))

import shop_flow  # noqa: E402
import shop_server  # noqa: E402

LAYOUT = ('<header id="top"><nav id="menu"><ul><li><a class="dept" href="/c/women">Women</a><ul>'
          '<li><a class="cat" href="/c/women-dresses">Dresses</a></li>'
          '<li><a class="cat" href="/c/women-shoes">Shoes</a></li></ul></li>'
          '<li><a class="dept" href="/c/men">Men</a><ul><li><a class="cat" href="/c/men-shirts">'
          'Shirts</a></li><li><a class="cat" href="/c/men-boots">Boots</a></li></ul></li></ul>'
          '</nav><a id="cartLink" href="/cart">Cart</a></header>')  # fmt: skip
FOOT = '<footer id="foot"><a href="/help">Help</a></footer>'


def product(title: str, price: str, n: int) -> str:
    return (f"<html><body>{LAYOUT}<main>"
            '<nav aria-label="Breadcrumb"><ol class="breadcrumb"><li><a href="/">Home</a></li>'
            '<li><a href="/c/women">Women</a></li><li aria-current="page">This</li></ol></nav>'
            f'<div id="gallery"><img id="mainImage" src="/img/{n}/a.jpg">'
            f'<button class="thumb"><img src="/img/{n}/a.jpg"></button>'
            f'<button class="thumb"><img src="/img/{n}/b.jpg"></button></div>'
            f'<h1 id="productTitle">{title}</h1><span id="price">{price}</span>'
            '<p id="note">Free returns</p>'
            '<div id="variants"><fieldset id="colourPicker"><legend>Colour</legend>'
            '<button class="swatch">red</button><button class="swatch">blue</button></fieldset>'
            '<select id="sizeSelect" name="size"><option>S</option><option>M</option></select></div>'
            '<section id="alsoViewed" aria-label="Customers also viewed"><h2>Also viewed</h2>'
            '<a class="rec" href="/p/x">A</a><a class="rec" href="/p/y">B</a></section>'
            f"</main>{FOOT}</body></html>")  # fmt: skip


def search() -> str:
    return (f"<html><body>{LAYOUT}<main><aside id=\"facets\"><fieldset class=\"facet\">"
            '<legend>Colour</legend><input type="checkbox" name="color" value="red">'
            '<input type="checkbox" name="color" value="blue"></fieldset></aside>'
            '<select id="sortBy" name="sort"><option value="relevance">Relevance</option>'
            '<option value="price-asc">Price</option></select><table id="results"></table>'
            f'<nav class="pager"><a href="/s?k=a&page=2">2</a></nav></main>{FOOT}</body></html>')  # fmt: skip


def cart() -> str:
    return (f"<html><body>{LAYOUT}<main><form id=\"cartForm\"><table><tr><td>x</td></tr>"
            f"</table><button id=\"checkout\">Pay</button></form></main>{FOOT}</body></html>")  # fmt: skip


def test_route_pattern_follows_learned_rules_only_inside_the_block():
    assert sanitize.route_pattern("/p/amber-linen-shirt") == "/p/amber-linen-shirt"
    rules = [("", "p", "*"), ("", "*", "dp", "{}")]
    with sanitize.template_rules(rules):
        assert sanitize.route_pattern("/p/amber-linen-shirt") == "/p/{slug}"
        # a name with digits and one without: the same template
        assert sanitize.route_pattern("/Echo-Dot-5th/dp/B09B8V1LZ3") == "/{slug}/dp/{id}"
        assert sanitize.route_pattern("/Kindle-Paperwhite/dp/B07CXG6C9W") == "/{slug}/dp/{id}"
        assert sanitize.route_pattern("/p") == "/p" and sanitize.route_pattern("/q/x") == "/q/x"
        with sanitize.template_rules([]):
            assert sanitize.route_pattern("/p/amber-linen-shirt") == "/p/amber-linen-shirt"
    assert sanitize.route_pattern("/p/amber-linen-shirt") == "/p/amber-linen-shirt"


def test_a_list_link_named_like_a_record_is_not_a_replay_locator():
    from labctl import stepfacts

    step = {"selector": "a.product-link", "locators": [
        {"kind": "role", "role": "link", "name": "Cedar Cotton Coat", "count": 1},
        {"kind": "structure", "css": "#grid > div > a", "index": 0, "count": 8}]}  # fmt: skip
    assert "Cedar" not in (stepfacts._target(step) or "")
    step["locators"][0]["name"] = "Next page"
    assert "Next page" in stepfacts._target(step)


def test_addresses_of_one_structure_become_a_template_and_different_pages_do_not():
    pages = {
        "/p/amber-linen-dress": product("Amber Linen Dress", "19.00", 1),
        "/p/birch-wool-flats": product("Birch Wool Flats", "26.00", 2),
        "/p/cedar-cotton-coat": product("Cedar Cotton Coat", "33.00", 3),
        "/s": search(), "/cart": cart(),
        # three help pages of different structure: never one template
        "/help/returns": f"<html><body>{LAYOUT}<main><table id='t'><tr><td>a</td></tr></table>"
                         f"</main></body></html>",
        "/help/contact": f"<html><body>{LAYOUT}<main><form id='f'><input name='m'></form></main>"
                         "</body></html>",
        "/help/terms": f"<html><body>{LAYOUT}<main><ol id='o'><li>x</li></ol><dl id='d'></dl>"
                       "</main></body></html>",
    }  # fmt: skip
    found = {tuple(r["rule"]): r for r in tp.learn(pages)}
    assert ("", "p", "*") in found and found[("", "p", "*")]["pattern"] == "/p/{slug}"
    assert found[("", "p", "*")]["similarity"] >= tp.SIMILAR
    assert ("", "help", "*") not in found
    # four category links from one repeated element, none visited: not enough to tell
    assert ("", "c", "*") not in found
    # one segment alone is never a template: `/s` and `/cart` stay what they are
    assert not any(r == ("", "*") for r in found)


def test_links_alone_need_many_from_one_repeated_element():
    few = {"/": f"<html><body>{LAYOUT}<a class='go' href='/go/a'>a</a><a class='go' href='/go/b'>"
                "b</a><a class='go' href='/go/c'>c</a></body></html>", "/cart": cart()}  # fmt: skip
    assert not any(r["rule"] == ("", "go", "*") for r in tp.learn(few))
    many = {"/": few["/"].replace("</body>", "".join(
        f"<a class='go' href='/go/{x}'>{x}</a>" for x in "defgh") + "</body>"), "/cart": cart()}  # fmt: skip
    rule = next(r for r in tp.learn(many) if r["rule"] == ("", "go", "*"))
    assert rule["visited"] == 0 and "8 addresses linked from `a.go`" == rule["links"]


def _ctx(pages: dict[str, str]):
    raw, doms = [], {}
    for n, (path, html) in enumerate(pages.items(), 1):
        raw.append({"seq": n, "url": f"https://shop.example.test{path}", "action": "goto"})
        doms[n] = html
    part = SimpleNamespace(raw=raw, doms=doms, words=set(), exchanges=[],
                           path=Path("no-recording"))  # fmt: skip
    return SimpleNamespace(part=part, known={"amber", "birch", "cedar"}, vocab={"free", "returns"},
                           name="r")  # fmt: skip


PAGES = {"/p/amber-linen-dress": product("Amber Linen Dress", "19.00", 1),
         "/p/birch-wool-flats": product("Birch Wool Flats", "26.00", 2),
         "/p/cedar-cotton-coat": product("Cedar Cotton Coat", "33.00", 3),
         "/s?k=red&sort=price-asc&color=blue": search(), "/cart": cart()}  # fmt: skip


def _nav(ctxs):
    with sanitize.template_rules([("", "p", "*")]):
        screens = sorted({sanitize.route_pattern(p.split("?")[0]) for p in PAGES})
        doc = {"screens": [{"path": s, "in_scope": True, "elements": [
            {"selector": "a.dept"}, {"selector": "button.swatch", "used": [
                {"step": 2, "requests": ["GET /api/products/{id}/variant 200"]}]},
            {"selector": "select#sizeSelect"}, {"selector": "button#checkout"}]}
            for s in screens]}  # fmt: skip
        return tp.extend_navigation(doc, ctxs)


def test_the_shared_layout_is_listed_once_and_marked_on_each_screen():
    ctxs = [_ctx(PAGES)]
    nav = _nav(ctxs)
    layout = nav["shared_layout"]
    regions = {r["selector"]: r for r in layout["regions"]}
    assert set(regions) == {"header#top", "footer#foot"}
    assert regions["header#top"]["on_screens"] == "3 of 3"
    mega = regions["header#top"]["mega_menu"]
    assert mega["selector"] == "nav#menu" and mega["top_items"] == 2 and mega["depth"] == 2
    assert [c["label"] for c in mega["tree"][0]["children"]] == ["Dresses", "Shoes"]
    assert "a.dept" in layout["elements"] and "a#cartLink" in layout["elements"]
    for screen in nav["screens"]:
        els = {e["selector"]: e for e in screen["elements"]}
        assert els["a.dept"]["layout"] == "header#top" and "layout" not in els["button#checkout"]


def test_a_template_has_instances_parameters_data_fields_seed_rows_and_parts(tmp_path):
    ctxs = [_ctx(PAGES)]
    nav = _nav(ctxs)
    endpoints = {"x": {"method": "GET", "path": "/api/products/{id}/variant",
                       "network": {"role": "variant of a product"},
                       "responses": {"200": {"schema": {"properties": {"price": {}, "image": {},
                                                                       "sku": {}}}}}}}  # fmt: skip
    with sanitize.template_rules([("", "p", "*")]):
        doc = tp.build(tmp_path, "shop", ctxs, nav, {"endpoints": endpoints})
    by = {t["screen"]: t for t in doc["templates"]}
    p = by["/p/{slug}"]
    assert p["instances"] == 3 and p["path_parameters"]["slug"]["shapes"] == ["A-A-A"]
    fields = {f["selector"]: f for f in p["data_fields"]}
    assert set(fields) == {"img#mainImage", "h1#productTitle", "span#price"}  # not `Free returns`
    assert fields["span#price"]["kind"] == "amount" and fields["h1#productTitle"]["kind"] == "text"
    rows = p["seed"]["rows"]
    assert len(rows) == 3 and "every value is masked" in p["seed"]["basis"]
    text = json.dumps(doc)
    for value in ("Amber Linen Dress", "Birch", "19.00", "/img/1/a.jpg", "amber-linen"):
        assert value not in text, value
    assert all(r["price"].count(".") == 1 for r in rows)
    kinds = {w["kind"]: w for w in p["widgets"]}
    assert kinds["breadcrumbs"]["levels"] == ["/", "/c/women", "(this page)"]
    assert kinds["image gallery"]["main_image"] == "img#mainImage"
    assert kinds["image gallery"]["thumbnails"] == 2
    picker = kinds["variant picker"]
    assert [d["name"] for d in picker["dimensions"]] == ["Colour", "size"]
    assert picker["api"] == ["GET /api/products/{id}/variant"]
    assert picker["changes"] == ["image", "price", "sku"]
    assert kinds["recommendations or personalisation"]["selector"] == "section#alsoViewed"
    s = by["/s"]
    assert s["query_parameters"]["color"] == {"role": "filter or option", "distinct": 1,
                                              "sent_in": "1 of 1 visit(s)", "values": ["blue"]}  # fmt: skip
    assert (
        s["query_parameters"]["k"]["role"] == "search"
        and "values" not in s["query_parameters"]["k"]
    )
    facets = next(w for w in s["widgets"] if w["kind"] == "search facets")["facets"]
    assert facets == [{"name": "Colour", "param": "color", "control": "check box",
                       "values": ["red", "blue"]}]  # fmt: skip
    assert next(w for w in s["widgets"] if w["kind"] == "sort")["values"] == ["relevance",
                                                                             "price-asc"]  # fmt: skip
    assert doc["navigation_hierarchy"]["categories"]["tree"][1]["label"] == "Men"
    lines = "\n".join(tp.page_lines(doc, "/p/{slug}", "apps/shop"))
    assert "## Template and instances" in lines and "ONE template" in lines
    assert "| `span#price` | `price` | amount |" in lines
    arch = "\n".join(tp.architecture_lines(doc, nav, "apps/shop"))
    assert "## Shared layout" in arch and "## Screen templates" in arch
    assert "Mega-menu `nav#menu`" in arch


def test_a_declared_public_field_is_the_only_way_a_recorded_value_is_carried(tmp_path):
    from labctl import publicdata

    ctxs = [_ctx(PAGES)]
    # nothing declared: every value is synthetic
    with sanitize.template_rules([("", "p", "*")]):
        doc = tp.build(tmp_path, "shop", ctxs, _nav(ctxs), None)
    p = next(t for t in doc["templates"] if t["screen"] == "/p/{slug}")
    assert (
        "Amber Linen Dress" not in json.dumps(p) and "every value is masked" in p["seed"]["basis"]
    )
    # an approved declaration in force: the title (a declared value as a whole) and the pictures
    # under a declared prefix are carried; the price, in no region and not declared, is not
    decl = publicdata.Declaration(approved_by="A. Owner", regions=["h1#productTitle"],
                                  images=["/img/"])  # fmt: skip
    token = publicdata._CURRENT.set(publicdata.Public([decl]))
    try:
        with sanitize.template_rules([("", "p", "*")]):
            doc = tp.build(tmp_path, "shop", ctxs, _nav(ctxs), None)
    finally:
        publicdata._CURRENT.reset(token)
    p = next(t for t in doc["templates"] if t["screen"] == "/p/{slug}")
    rows = p["seed"]["rows"]
    assert [r["product_title"] for r in rows] == [
        "Amber Linen Dress", "Birch Wool Flats", "Cedar Cotton Coat"]  # fmt: skip
    assert [r["main_image"] for r in rows] == ["/img/1/a.jpg", "/img/2/a.jpg", "/img/3/a.jpg"]
    assert "19.00" not in json.dumps(rows)
    assert "main_image, product_title are declared public data" in p["seed"]["basis"]
    fields = {f["column"]: f for f in p["data_fields"]}
    assert "public" in fields["product_title"] and "public" not in fields["price"]
    # a caller's own rule, with the same signature
    with sanitize.template_rules([("", "p", "*")]):
        doc = tp.build(tmp_path, "shop", ctxs, _nav(ctxs), None,
                       reveal=lambda screen, field, value: field["column"] == "price")  # fmt: skip
    p = next(t for t in doc["templates"] if t["screen"] == "/p/{slug}")
    assert [r["price"] for r in p["seed"]["rows"]] == ["19.00", "26.00", "33.00"]
    assert "Amber Linen Dress" not in json.dumps(p["seed"]["rows"])


# =================================================================================================
# The fixture shop, recorded
# =================================================================================================


def make_root(path: Path) -> Path:
    import shutil

    repo = HERE.parents[2]
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (path / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(repo / "AGENTS.md", path / "AGENTS.md")
    shutil.copy(repo / "docs" / "factory-playbook.md", path / "docs" / "factory-playbook.md")
    return path


def record_and_package(root: Path, name: str, script) -> SimpleNamespace:
    import warnings

    from labctl import multiapp, recorder
    from labctl.builder import package as pkg

    rec = multiapp.recordings_dir(root) / name
    run = shop_server.serve()
    started = time.time()
    try:
        recorder.record(f"{run.url}/", rec, headless=True, script=script)
    finally:
        run.stop()
    recorded = time.time()
    multiapp.build_all(root, rec, say=lambda m: None)
    path = multiapp.proposal_file(rec)
    data = yaml.safe_load(path.read_text("utf-8"))
    data["apps"][0]["id"] = "shop"
    path.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        summary = multiapp.build_all(root, rec, proposal=path, say=lambda m: None,
                                     package_dir=root / "packages")  # fmt: skip
    built = time.time()
    zpath = root / summary.apps[0].package
    with zipfile.ZipFile(zpath) as zf:
        texts = {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}
    steps = sum(1 for line in (rec / "steps.jsonl").read_text("utf-8").splitlines() if line)
    return SimpleNamespace(root=root, rec=rec, texts=texts, zip=zpath, steps=steps,
                           record_s=recorded - started, build_s=built - recorded,
                           templates=json.loads(texts["apps/shop/templates.json"]),
                           network=json.loads(texts["apps/shop/api/network.json"]),
                           nav=json.loads(texts["apps/shop/navigation.json"]))  # fmt: skip


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    return record_and_package(make_root(tmp_path_factory.mktemp("shop") / "repo"), "shop-run",
                              shop_flow.run)  # fmt: skip


def test_the_shop_is_described_per_template_not_per_product(shop):
    t = {x["screen"]: x for x in shop.templates["templates"]}
    assert set(t) == {"/", "/c/{slug}", "/p/{slug}", "/s", "/cart"}
    assert t["/p/{slug}"]["instances"] == 3 and len(t["/p/{slug}"]["seed"]["rows"]) == 3
    pages = sorted(n for n in shop.texts if n.startswith("apps/shop/pages/"))
    assert pages == [f"apps/shop/pages/{x}.html" for x in ("c-item", "cart", "home", "p-item", "s")]
    docs = [n for n in shop.texts if "/spec/PAGES/" in n]
    assert not [n for n in docs if any(w in n for w in ("amber", "birch", "cedar", "women-"))]
    kinds = {w["kind"] for w in t["/p/{slug}"]["widgets"]}
    assert {"breadcrumbs", "image gallery", "variant picker",
            "recommendations or personalisation"} <= kinds  # fmt: skip
    picker = next(w for w in t["/p/{slug}"]["widgets"] if w["kind"] == "variant picker")
    assert picker["api"] == ["GET /api/products/{id}/variant"] and "price" in picker["changes"]
    facets = next(w for w in t["/s"]["widgets"] if w["kind"] == "search facets")["facets"]
    assert [f["param"] for f in facets] == ["color", "size", "brand"]
    assert facets[0]["values"] == list(shop_server.COLOURS)
    layout = shop.nav["shared_layout"]
    assert [r["selector"] for r in layout["regions"]] == ["header#siteHeader", "footer#siteFooter"]
    assert layout["regions"][0]["mega_menu"]["top_items"] == 3
    state = {s["kind"]: s for s in shop.templates["state"]}
    assert (
        state["cart"]["badge"] == "a#cartLink" and "cookie `shop_sid`" in state["cart"]["kept_by"]
    )
    page = shop.texts["apps/shop/spec/PAGES/03-p-item.md"]
    assert "## Template and instances" in page and "of the shared layout" in page
    arch = shop.texts["apps/shop/spec/ARCHITECTURE.md"]
    for heading in ("## Shared layout", "## Screen templates", "## Network behaviour"):
        assert heading in arch, heading
    assert "## Screen templates and shared layout" in shop.texts["apps/shop/BRIEF.md"]


def test_the_shop_network_facts(shop):
    api = {Path(n).name: json.loads(v) for n, v in shop.texts.items() if "/api/" in n}
    products = api["get-api-products.json"]["network"]
    assert products["paging"]["next"] == ["next_cursor"]
    assert "header X-Total-Count" in products["paging"]["total"]
    assert products["answers"]["200"]["compression"] == ["gzip"]
    assert any(d["field"] == "query.cursor" for d in products["depends_on"])
    assert any("infinite scroll" in a for a in products["paging"]["asked_by"])
    assert any("button#loadMore" in a for a in products["paging"]["asked_by"])
    cart = api["post-api-cart-items.json"]["network"]
    assert cart["csrf"][0]["name"] == "X-CSRF-Token"
    assert '<meta name="csrf-token">' in cart["csrf"][0]["comes_from"]
    assert cart["retries"][0]["after"] == 503 and cart["retries"][0]["then"] == 201
    wish = api["post-api-wishlist.json"]["network"]
    assert wish["body"]["format"] == "application/x-www-form-urlencoded"
    assert wish["csrf"][0]["in"] == "form"
    review = api["post-api-reviews.json"]["network"]["body"]
    assert review["format"] == "multipart/form-data"
    assert review["fields"]["photo"]["accept"] == "image/png,image/jpeg"
    props = api["post-api-reviews.json"]["request"]["schema"]["properties"]
    assert set(props) == {"rating", "title", "body", "photo", "sku"}  # the parts, by name
    gql = api["post-graphql.json"]
    op = gql["network"]["graphql"]["operations"]["ProductReviews"]
    assert op["variables"]["after"]["type"] == "String" and "selection" in op
    assert gql["calls"][0]["request"]["query"].startswith("query ProductReviews(")
    net = shop.network
    sse = net["streams"][0]
    assert sse["type"] == "server-sent events" and "price" in sse["events"]
    assert net["redirect_chains"][0]["start"] == "GET /go/women"
    assert [h["status"] for h in net["redirect_chains"][0]["hops"]] == [301, 302]
    third = net["third_parties"][0]
    assert third["origin"] == "third-party-1" and third["purpose"] == "analytics or tracking"
    assert set(net["static_files"]) >= {"style", "script", "image", "font"}
    assert net["static_files"]["style"]["from_browser_cache"] > 0
    assert net["page_loads"]["/p/{slug}"][:2] == ["GET /api/cart", "POST /graphql"]


def test_no_recorded_value_reaches_the_shop_package(shop):
    from labctl import apicatalog as ac

    whole = "\n".join(shop.texts.values())
    for value in [*shop_flow.TYPED.values(), *shop_flow.DATA, "localhost"]:
        assert value not in whole, value
    har = json.loads((shop.rec / "recording.har").read_text("utf-8"))
    secrets = set()
    for e in har["log"]["entries"]:
        for h in e["request"]["headers"]:
            if h["name"].lower() in ("x-csrf-token", "cookie"):
                secrets.add(h["value"].split("=")[-1])
    assert secrets and not [s for s in secrets if len(s) >= 8 and s in whole]
    assert ac.leaks(shop.root, "shop") == []


@pytest.mark.skipif(not os.environ.get("TWINLAB_SCALE_TEST"), reason="long: TWINLAB_SCALE_TEST=1")
def test_a_long_session_grows_per_template_not_per_instance(tmp_path, shop):
    pytest.importorskip("playwright.sync_api")
    long = record_and_package(make_root(tmp_path / "repo"), "shop-long",
                              lambda page: shop_flow.long(page, 24))  # fmt: skip
    t = {x["screen"]: x for x in long.templates["templates"]}
    print(
        f"\nscale: {long.steps} steps, {t['/p/{slug}']['instances']} product pages, "
        f"{len(t)} templates, zip {long.zip.stat().st_size} bytes, record {long.record_s:.0f} s, "
        f"build {long.build_s:.0f} s; short: {shop.steps} steps, zip {shop.zip.stat().st_size}"
    )
    assert long.steps >= 100 and t["/p/{slug}"]["instances"] >= 20
    assert len(t["/p/{slug}"]["seed"]["rows"]) == t["/p/{slug}"]["instances"]  # per instance
    assert set(t) == {x["screen"] for x in shop.templates["templates"]}
    # per template, never per instance (the long session calls fewer endpoints: no cart edits)
    for prefix in ("apps/shop/pages/", "apps/shop/spec/PAGES/", "apps/shop/api/",
                   "apps/shop/look/screens/"):  # fmt: skip
        assert len([n for n in long.texts if n.startswith(prefix)]) <= len(
            [n for n in shop.texts if n.startswith(prefix)]), prefix  # fmt: skip
    assert long.zip.stat().st_size < 2 * shop.zip.stat().st_size


def test_a_jsf_view_is_one_screen_never_a_template_instance(tmp_path):
    def view(name: str, body: str) -> str:
        return (f'<html><body>{LAYOUT}<form id="main" method="post" action="/{name}.xhtml">'
                f'<input type="hidden" name="javax.faces.ViewState" value="1:2">{body}</form>'
                f"{FOOT}</body></html>")  # fmt: skip

    same = "<table id='main:cases'><tr><td>x</td></tr></table>"
    pages = {f"/admin/{n}.xhtml": view(f"admin/{n}", same) for n in ("users", "roles", "groups")}
    assert tp.learn(pages)  # three views of one structure would be a template...
    rec = tmp_path / "rec"
    rec.mkdir()
    entries = [{"startedDateTime": "2026-11-18T09:00:00Z", "time": 5,
                "request": {"method": "GET", "url": f"https://jsf.example.test{p}", "headers": []},
                "response": {"status": 200, "headers": [],
                             "content": {"mimeType": "text/html", "text": h}}}
               for p, h in pages.items()]  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": entries}}), "utf-8")
    assert tp._visited([rec]) == {} and tp.rules_for(tmp_path, "jsf", [rec]) == []  # ...not JSF
    # the address bar keeps /login.xhtml after the sign-in forward: the screen is the view
    raw = [{"seq": 1, "url": "https://jsf.example.test/login.xhtml", "action": "goto"},
           {"seq": 2, "url": "https://jsf.example.test/login.xhtml", "action": "click"}]  # fmt: skip
    doms = {1: view("login", "<input id='main:user'>"), 2: view("workspace", same)}
    part = SimpleNamespace(raw=raw, doms=doms, words=set(), exchanges=[],
                           path=Path("no-recording"))  # fmt: skip
    ctx = SimpleNamespace(part=part, known=set(), vocab=set(), name="r")
    doc = tp.build(tmp_path, "jsf", [ctx], {"screens": []}, None)
    by = {t["screen"]: t for t in doc["templates"]}
    assert set(by) == {"/login.xhtml", "/workspace.xhtml"}
    assert by["/workspace.xhtml"]["instances"] == 1 and "path_parameters" not in by[
        "/workspace.xhtml"]  # fmt: skip
