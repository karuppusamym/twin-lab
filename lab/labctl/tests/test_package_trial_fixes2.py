"""More package defects the Demoblaze trial, its blind builder and its judge found: what the
sanitiser keeps, the seed and the record bindings, drafts to judge with, aliases, replay
details, library versions, code-only endpoints, list order and the package-only checks.

Synthetic data only.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from labctl import apicatalog, har, publicdata, sanitize
from labctl.behaviour import dom as domlib
from tests.test_package_trial_fixes import _ex

# --- 5, G1, G16: what the sanitiser keeps -----------------------------------------------------


def _public():
    pub = publicdata.Public([publicdata.Declaration(
        approved_by="tester", endpoints=[publicdata.Endpoint("GET", "/entries")])])  # fmt: skip
    pub.phrases |= {sanitize.phrase("A tall lamp of amber linen."), "amber linen lamp"}
    pub.numbers |= {"1", "360"}
    return pub


def _with_public(fn):
    token = publicdata._CURRENT.set(_public())
    try:
        return fn()
    finally:
        publicdata._CURRENT.reset(token)


def test_a_declared_value_shows_in_an_element_with_an_id_but_a_sum_and_a_cart_row_stay_masked():
    html = ('<html><body><div class="card"><p id="article" class="card-text">A tall lamp of\n'
            ' amber linen. </p></div><h3 id="totalp">360</h3><table><tr><td>Amber linen lamp'
            "</td></tr></table></body></html>")  # fmt: skip
    page = _with_public(lambda: sanitize.page(html, {"amber", "linen", "lamp"}))
    assert "A tall lamp of" in page.html  # declared, as the API answered it
    assert ">999<" in page.html  # the cart's sum: a number in an element of its own, data
    assert "Amber linen lamp</td>" not in page.html  # a cart row stays masked


def test_behaviour_attributes_classes_sizes_and_declared_query_values_are_kept():
    html = ('<html><head><link rel="icon" sizes="32x32" href="/favicon-32x32.png"></head><body>'
            '<a class="nav-link" data-toggle="modal" data-target="#videoModal" href="#">About</a>'
            '<li data-target="#carouselExampleIndicators" data-slide-to="0"></li>'
            '<div class="card h-100"><a href="prod.html?idp_=1">x</a></div>'
            '<div id="videoModal"></div></body></html>')  # fmt: skip
    plain = sanitize.page(html).html
    assert 'data-target="#videoModal"' in plain and 'data-slide-to="0"' in plain
    assert 'class="card h-100"' in plain and 'sizes="32x32"' in plain
    assert 'href="prod.html?idp_="' in plain  # without a declaration the id is left out
    declared = _with_public(lambda: sanitize.page(html).html)
    assert 'href="prod.html?idp_=1"' in declared


def test_a_row_control_keeps_its_label_and_a_data_link_does_not():
    html = ("<html><body><table><tr><td><a href='#'>Delete</a></td><td><a href='#'>Amber</a>"
            "</td></tr></table></body></html>")  # fmt: skip
    page = sanitize.page(html, {"amber"})
    assert ">Delete</a>" in page.html and ">Amber</a>" not in page.html


# --- G3, 6: the seed and the record bindings -----------------------------------------------------


def test_every_record_any_answer_returned_reaches_the_seed_once():
    from labctl import seedplan

    d = seedplan.ListDesign("/entries", "entries")
    d.first_items = [{"id": 1, "title": "Amber"}, {"id": 2, "title": "Birch"}]
    seedplan._union(d, [("POST", "/bycat", [{"id": 2, "title": "Birch"}, {"id": 3, "title": "Cedar"}]),
                        ("POST", "/viewcart", [{"id": "x", "cookie": "c"}])])  # fmt: skip
    assert [(m, r, i["id"]) for m, r, i in d.all_items] == [
        ("GET", "/entries", 1), ("GET", "/entries", 2), ("POST", "/bycat", 3)]  # fmt: skip


CARDS = ('<div id="cards" class="row"><div class="col"><div class="card"><a class="t">Amber lamp s6'
         '</a><h5>$360</h5><p>A lamp</p></div></div><div class="col"><div class="card"><a '
         'class="t">Birch stool 1520</a><h5>$820</h5><p>A stool</p></div></div></div>')  # fmt: skip


def _part(raw, befores, doms=None):
    return SimpleNamespace(raw=raw, befores=befores, doms=doms or {}, html_at=lambda seq: None)


def test_a_click_on_one_of_many_cards_is_bound_to_its_seed_row():
    from labctl import seedplan

    rows = {
        "entries": [{"id": 1, "title": "Amber lamp s6"}, {"id": 2, "title": "Birch stool 1520"}]
    }
    loc = [{"kind": "record", "rows": "#cards > div.col", "control": "a.t", "text_from": None,
            "title_shape": domlib.shape("Birch stool 1520 $820 A stool")}]  # fmt: skip
    part = _part([{"seq": 5, "action": "click", "selector": "a.t", "locators": loc}],
                 {5: f"<html><body>{CARDS}</body></html>"})  # fmt: skip
    (got,) = seedplan.record_bindings(part, rows)
    assert got["record"] == "Birch stool 1520"
    # two cards of one shape: the page the click opened decides; else TO DO with candidates
    same = [{**loc[0], "title_shape": domlib.shape("Amber lamp s6 $360 A lamp")}]
    twins = CARDS.replace("Birch stool 1520", "Cobalt chair z5").replace("$820", "$360")
    twins = twins.replace("A stool", "A chair")
    rows2 = {"entries": [*rows["entries"], {"id": 3, "title": "Cobalt chair z5"}]}
    raw = [{"seq": 5, "action": "click", "selector": "a.t", "locators": same},
           {"seq": 6, "action": "goto", "caused_by": 5, "dom": "x"}]  # fmt: skip
    opened = _part(raw, {5: twins}, {6: "<h2 class='name'>Amber lamp s6</h2>"})
    assert seedplan.record_bindings(opened, rows2)[0]["record"] == "Amber lamp s6"
    (todo,) = seedplan.record_bindings(_part(raw[:1], {5: twins}), rows2)
    assert todo["record"] is None
    assert sorted(todo["candidates"]) == ["Amber lamp s6", "Cobalt chair z5"]


# --- 7: drafts to judge with ---------------------------------------------------------------------


def test_drafts_count_rows_name_the_opened_record_and_relax_visual_mode(monkeypatch, tmp_path):
    from labctl.behaviour import drafts, visual

    assert len(drafts.select(CARDS, "#cards > div.col")) == 2
    assert len(drafts.select(CARDS, "div.row div.card a.t")) == 2
    raw = [{"seq": 4, "action": "goto", "dom": "d4"},
           {"seq": 5, "action": "click",
            "locators": [{"kind": "record", "rows": "#cards > div.col"}]},
           {"seq": 6, "action": "goto", "caused_by": 5, "dom": "d6"}]  # fmt: skip
    part = _part(raw, {5: CARDS}, {6: "<h2 class='name'>Birch stool 1520</h2>"})
    by = {
        c["id"]: c for c in drafts.acceptance_cases(part, [{"n": 5, "record": "Birch stool 1520"}])
    }
    assert (by["rows-before-5"]["after"], by["rows-before-5"]["check"]) == (4, "count")
    assert by["rows-before-5"]["equals"] == 2
    assert by["record-shown-5"]["locator"] == "h2.name"
    assert by["record-shown-5"]["equals"] == {"record": 5}
    monkeypatch.setattr(visual, "pictures_of", lambda p: {3: "screens/0003.png"})
    monkeypatch.setattr(visual, "picture_size", lambda p: (1280, 720))
    text = drafts.visual_draft(SimpleNamespace(id="r", path=tmp_path), fonts_carried=False)
    (state,) = yaml.safe_load(text)["states"]
    assert state["mode"] == "threshold" and state["max_ratio"] == drafts.VISUAL_RATIO
    assert state["exclude"] == [drafts.TEXT_REGIONS] and "no font" in text
    net = {"media_origins": [{"origin": "media-origin-1", "serves": ["an HLS video playlist"]}]}
    sc = yaml.safe_load(drafts.scope_draft("r", net, ['<div class="video-js">']))
    assert sc["approved_by"] is None and sc["exclusions"][0]["element"] == "div.video-js"
    assert drafts.scope_draft("r", {"media_origins": []}, []) is None


# --- 8, G4: one screen at two addresses; relative links -------------------------------------------


def test_the_directory_index_is_an_alias_of_its_screen(monkeypatch):
    page = "<html><head><title>STORE</title></head><body><p>x</p></body></html>"
    docs = [_ex("GET", "www.shop.example.test", p, kind="document")
            for p in ("/", "/index.html", "/cart.html", "/orders")]  # fmt: skip
    for e, text in zip(docs, [page, page, page.replace("STORE", "CART"), page], strict=True):
        e.resp_mime, e.resp_text = "text/html", text
    monkeypatch.setattr(har, "load", lambda p: SimpleNamespace(har={}))
    monkeypatch.setattr(har, "exchanges", lambda h: docs)
    # `/orders` serves the same HTML (a single-page shell, say) but is no directory index
    assert har.screen_aliases(["rec"]) == {"/index.html": "/"}
    with sanitize.screen_aliases({"/index.html": "/"}):
        assert sanitize.route_pattern("/index.html") == "/"
    assert sanitize.route_pattern("/index.html") == "/index.html"


def test_a_relative_link_is_the_screen_it_resolves_to():
    from labctl import navmap

    assert navmap.resolve_target("index.html", "/cart.html") == "/index.html"
    assert navmap.resolve_target("prod.html?idp_=1", "/") == "/prod.html"
    assert navmap.resolve_target("/x/y", "/a") == "/x/y"


# --- judge findings: bindings by title, dialogs after a hash change, snapshots on load ------------


def test_a_binding_names_the_row_whose_text_holds_the_title_once():
    from labctl.locate import title_hits

    rows = ["Amber lamp s6 360 Delete", "Birch stool 1520 820 Delete"]
    assert title_hits(rows, "Birch stool 1520") == [1]
    assert title_hits(rows, "Amber lamp s6 360 Delete") == [0]  # the whole text still works
    assert title_hits(rows, "Delete") == [0, 1]  # not one: the replay blocks


def test_a_dialog_after_a_hash_navigation_belongs_to_the_click():
    from labctl.behaviour.fidelity import dialog_after

    def st(seq, action, caused_by=None):
        return SimpleNamespace(seq=seq, action=action, caused_by=caused_by)

    steps = [st(15, "click"), st(16, "goto", 15), st(17, "dialog"), st(18, "click")]
    assert dialog_after(steps, steps[0]).seq == 17
    assert dialog_after(steps, steps[3]) is None
    assert dialog_after([st(1, "click"), st(2, "click"), st(3, "dialog")], st(1, "click")) is None


def test_a_snapshot_with_dialogs_closed_by_script_is_not_the_screen():
    fresh = '<div class="modal fade" id="m" role="dialog"></div>'
    after = '<div class="modal fade" id="m" role="dialog" style="display: none;"></div>'
    assert har.dialog_residue(fresh) == 0 and har.dialog_residue(after) == 1


# --- G2, G9, G11, G7: versions, code-only endpoints, order, template values ----------------------


def test_a_library_banner_quoted_in_another_bundle_does_not_set_its_version():
    rule = next(r for r in har.RULES if r.marker == "jQuery banner in a bundle")
    texts = {"x=1;/*! jQuery v1.9.1 */", "/*! jQuery v3.2.1 | (c) JS Foundation */ !function(){}"}
    (hit,) = [h for h in har._hits({rule.source: texts}) if h["rule"] is rule]
    assert hit["version"] == "3.2.1" and hit["versions"] == ["1.9.1", "3.2.1"]


def test_an_endpoint_only_the_code_calls_gets_its_fields_from_the_code():
    js = ("function logIn(u,p){ $.ajax({type:'POST', url: API_URL + '/login', data: "
          "JSON.stringify({username: u, password: p}), contentType: 'application/json'}) }")  # fmt: skip
    assert apicatalog.code_fields([js], "/login") == ["username", "password"]


def test_the_observed_order_of_list_answers_is_stated():
    lists = [[{"id": 1, "t": "b"}, {"id": 2, "t": "a"}], [{"id": 3, "t": "c"}, {"id": 5, "t": "d"}]]
    assert apicatalog.observed_order(lists) == (
        "rows ordered by `id` ascending in every recorded answer")  # fmt: skip
    other = apicatalog.observed_order([[{"id": 2}, {"id": 1}], [{"id": 1}, {"id": 2}]])
    assert other.startswith("no field orders")


def test_template_values_say_how_many_are_left_out():
    from labctl import templates

    got = templates._query_params(["/prod.html?idp_=1", "/prod.html?idp_=2"], {"1"})
    assert got["idp_"]["values"] == ["1"] and "1 of the 2" in got["idp_"]["values_note"]


# --- 10: checks a builder can run with the package alone -----------------------------------------


def test_the_task_gives_package_only_checks_and_keeps_repository_checks_apart():
    from labctl.builder import package as pkg

    text = pkg._package_only_text("shop", "apps/shop", "sqlite", True, "DATABASE_URL", nav=True,
                                  api=True, screens=["home.html"], pictures=["home.png"],
                                  static=["/config.json"])  # fmt: skip
    for want in (
        "Start the app",
        "Open each screen",
        "Compare with the pictures",
        "Run the replay values",
        "`/config.json`",
        "`home.png`",
        "without labctl",
    ):
        assert want in text, want
    assert "pytest" in text.split("3. Instead")[0]  # named only as what to skip
