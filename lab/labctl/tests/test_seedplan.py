"""Seed rows and replay values made together (labctl.seedplan): the parts that need no browser.
The whole chain, on a recording of the two fixture applications, is in test_multi_app_flow.py."""

from __future__ import annotations

import sqlite3

import yaml

from labctl import multiapp, seedplan
from labctl.behaviour import bindings as bnd
from labctl.behaviour import dom as domlib
from labctl.source.sqlalchemy_models import Column, Table


def _node(html: str):
    return next(n for n in domlib._walk(domlib.parse_html(html)) if n.tag in ("input", "textarea"))


def test_a_value_a_person_changes_after_it_was_generated_is_theirs(tmp_path):
    path = tmp_path / "bindings.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "steps": {
        3: {"action": "type", "value": "Kestrel", "generated": "Kestrel"},
        5: {"action": "type", "value": "mine", "generated": "Kestrel"},
        7: {"action": "type", "value": "typed by hand"}}}), "utf-8")  # fmt: skip
    got = bnd.load(path)
    assert got.values == {3: "Kestrel", 5: "mine", 7: "typed by hand"}
    assert got.generated == {3}
    merged = bnd.Bindings({7: "x"}).merged(got)
    assert merged.generated == {3} and merged.values[7] == "x"


def test_generated_values_do_not_ask_for_the_recording_to_be_split_again(tmp_path):
    path = tmp_path / "bindings.yaml"
    path.write_text(yaml.safe_dump({"steps": {3: {"field": "#q", "value": None}}}), "utf-8")
    empty = multiapp._given(path)
    path.write_text(yaml.safe_dump({"steps": {3: {"field": "#q", "value": "Kestrel",
                                                  "generated": "Kestrel"}}}), "utf-8")  # fmt: skip
    assert multiapp._given(path) == empty == {}
    path.write_text(yaml.safe_dump({"steps": {3: {"field": "#q", "value": "Lindqvist",
                                                  "generated": "Kestrel"}}}), "utf-8")  # fmt: skip
    assert multiapp._given(path) == {"steps": {"3": {"field": "#q", "value": "Lindqvist",
                                                     "generated": "Kestrel"}}}  # fmt: skip


def test_values_follow_the_shape_and_the_field_attributes():
    words = seedplan.Words(set())
    qty = _node('<input id="quantity" type="number" min="1" max="99">')
    assert seedplan._from_shape("9", qty, words, 0, refused=True) == "0"
    assert seedplan._from_shape("9", qty, words, 0) == "2"
    mail = _node('<input id="customerEmail" type="email">')
    ok = seedplan._from_shape("A.A@A.A", mail, words, 1)
    bad = seedplan._from_shape("A.A.A", mail, words, 2, refused=True)
    assert ok.endswith("@example.test") and "@" not in bad
    amount = _node('<input name="amount" inputmode="decimal">')
    assert seedplan.TWO_DEC.match(seedplan._from_shape("9.9", amount, words, 3))
    date = _node('<input id="deliveryDate" type="date">')
    assert seedplan.ISO_DATE.match(seedplan._from_shape(None, date, words, 4))
    name = seedplan._from_shape("A A-A", _node('<input id="customerName">'), words, 5)
    assert len(name.split()) == 2 and "-" in name.split()[1]
    # typed words never come from the seed's word lists: a typed name adds no search hit
    assert not set(seedplan.TYPED) & set(seedplan.LAST) and not set(seedplan.TYPED) & set(
        seedplan.FIRST)  # fmt: skip


def test_generated_words_avoid_every_word_of_the_evidence():
    words = seedplan.Words({w.lower() for w in seedplan.TYPED[:3]})
    assert words.take() == seedplan.TYPED[3]
    assert seedplan._pool(seedplan.LAST, {"kestrel"})[0] == "Larkspur"


def test_the_seed_is_sql_of_the_schema_dialect_with_every_name_quoted():
    table = Table(name="invoices", model="invoices", file=None, line=0)
    table.columns = [Column(n, t, True) for n, t in (("number", "TEXT"), ("order", "TEXT"),
                     ("vat", "BOOLEAN"), ("amount", "NUMERIC"), ("memo", "TEXT"))]  # fmt: skip
    rows = {"invoices": [{"number": "INV-5001", "order": "ORD-1024", "vat": True,
                          "amount": "64.50", "memo": "it's"}]}  # fmt: skip
    sql = seedplan.seed_sql("billing", rows, [table], "sqlite", ["`invoices`: 1 row(s)"])
    assert 'INSERT INTO "invoices" ("number", "order", "vat", "amount", "memo")' in sql
    assert "VALUES ('INV-5001', 'ORD-1024', 1, 64.50, 'it''s');" in sql
    db = sqlite3.connect(":memory:")
    db.executescript('CREATE TABLE invoices ("number" TEXT, "order" TEXT, vat BOOLEAN, '
                     "amount NUMERIC, memo TEXT);" + sql)  # fmt: skip
    assert db.execute('select "order", amount from invoices').fetchall() == [("ORD-1024", 64.5)]
    assert "[order]" in seedplan.seed_sql("billing", rows, [table], "mssql", [])
    assert "TRUE" in seedplan.seed_sql("billing", rows, [table], "postgres", [])


def test_draft_acceptance_requires_only_what_the_package_states(tmp_path):
    steps = tmp_path / "apps" / "billing" / "steps"
    steps.mkdir(parents=True)
    (steps / f"rec{seedplan.VALUES}").write_text(yaml.safe_dump({
        "steps": [{"n": 18, "value": "64.50"}, {"n": 14, "value": "abc"}],
        "validations": [
            {"message": "The amount is a number greater than 0, such as 999.99.", "step": 15,
             "shown_at": 17, "element": "p.notice--error",
             "refused": [{"value": "abc", "step": 14}]},
            {"message": "An order reference looks like ORD-0000.", "step": 5, "shown_at": 7,
             "element": "p.notice--error", "refused": [{"value": "abc9xyz", "step": 4}]}],
        "display": [
            {"element": "[data-field=amount]", "screen": "/billing/invoice", "seen_at": 23,
             "echo": 18, "shows": "what step 18 typed", "format": "two decimals"},
            {"element": "[data-field=total]", "screen": "/billing/invoice", "seen_at": 23,
             "computed": True}]}), "utf-8")  # fmt: skip
    draft = seedplan.acceptance(tmp_path, "billing", "rec")
    ids = {c["id"]: c for c in draft["cases"]}
    assert ids["shows-data-field-amount"]["equals"] == {"binding": 18}
    assert ids["shows-data-field-amount"]["after"] == 23
    # a message holding masked data (999.99) is not a literal to expect; one without is
    assert "refused-15" not in ids and ids["refused-5"]["contains"].startswith("An order")
    assert not any("total" in c["locator"] for c in draft["cases"])
    assert draft["excluded"] and "rule unknown" in draft["excluded"][0]


def test_a_request_sent_just_before_its_step_was_logged_still_belongs_to_it(tmp_path):
    """The recorder logs a typing step and the key press it causes a few ms after the search
    request has started: the request is still that step's (a flaky trial run showed it)."""
    import json

    part = tmp_path / "evidence" / "orders" / "rec"
    part.mkdir(parents=True)
    url = "http://127.0.0.1:1/orders/search"
    steps = [{"seq": 1, "ts": "2026-11-18T10:50:04.000Z", "url": url, "action": "goto"},
             {"seq": 2, "ts": "2026-11-18T10:50:05.512Z", "url": url, "action": "type",
              "selector": "input#q", "typed_shape": "A-9"},
             {"seq": 3, "ts": "2026-11-18T10:50:05.512Z", "url": url, "action": "keypress",
              "selector": "input#q", "key": "Enter"}]  # fmt: skip
    (part / "steps.jsonl").write_text("\n".join(json.dumps(s) for s in steps), "utf-8")
    answer = {"items": [{"number": "A-9", "status": "NEW"}], "page": 1, "pages": 1, "total": 1}
    har = {"log": {"entries": [{
        "startedDateTime": "2026-11-18T10:50:05.507Z", "time": 3,
        "request": {"method": "GET", "url": "http://127.0.0.1:1/api/orders?by=number&q=A-9",
                    "headers": []},
        "response": {"status": 200, "headers": [], "content": {
            "mimeType": "application/json", "text": json.dumps(answer)}}}]}}  # fmt: skip
    (part / "recording.har").write_text(json.dumps(har), "utf-8")
    p = seedplan.load_part("orders", part)
    assert [e.route for e in p.owner.get(3, [])] == ["/api/orders"]
    (s,) = seedplan._searches_of(p)
    assert (s.step, s.param, s.rule, s.key, s.others) == (2, "q", "equals", "number",
                                                          {"by": "number"})  # fmt: skip


def test_display_formats_from_what_the_pages_showed():
    assert seedplan._format(["378.00", "99.90"])[1] == "2"
    assert seedplan._format(["2026-10-02"])[0] == "a date `YYYY-MM-DD`"
    assert seedplan._format(["NEW", "PAID"])[0].startswith("one of `NEW`, `PAID`")
    assert seedplan._format(["ORD-1001", "ORD-1023"])[0] == "an identifier `ORD-` + 4 digits"
    assert seedplan._format(["3"])[1] == "0"


def test_the_pages_api_on_another_origin_of_their_site_is_read_with_no_page_from_its_referer(
    tmp_path,
):
    """Demoblaze trial: the pages on www. call api. of the same site. Its calls were dropped as
    another host's, and their origin-only Referer (`/`) named the wrong page."""
    import json

    part = tmp_path / "evidence" / "shop" / "rec"
    part.mkdir(parents=True)
    steps = [{"seq": 1, "ts": "2026-11-18T10:50:04.000Z", "url": "https://www.shop.example/",
              "action": "goto"},
             {"seq": 2, "ts": "2026-11-18T10:50:05.000Z",
              "url": "https://www.shop.example/prod.html?idp_=1", "action": "goto"}]  # fmt: skip
    (part / "steps.jsonl").write_text("\n".join(json.dumps(s) for s in steps), "utf-8")

    def call(host: str, t: str) -> dict:
        return {"startedDateTime": f"2026-11-18T10:50:{t}Z", "time": 3, "_resourceType": "fetch",
                "request": {"method": "POST", "url": f"https://{host}/view",
                            "headers": [{"name": "Referer", "value": "https://www.shop.example/"}],
                            "postData": {"mimeType": "application/json", "text": '{"id": "1"}'}},
                "response": {"status": 200, "headers": [], "content": {
                    "mimeType": "application/json", "text": '{"id": 1, "title": "Lamp"}'}}}  # fmt: skip

    har = {"log": {"entries": [call("api.shop.example", "05.300"),
                               call("api.other.example", "05.400")]}}  # fmt: skip
    (part / "recording.har").write_text(json.dumps(har), "utf-8")
    p = seedplan.load_part("shop", part)
    (e,) = p.exchanges  # the API of another site stays out
    assert (e.method, e.route, e.referer) == ("POST", "/view", "")
    assert [x.route for x in p.owner.get(2, [])] == ["/view"]


def test_an_id_the_page_gives_several_elements_is_found_by_the_next_locator():
    from labctl import stepfacts

    step = {"selector": "a#itemc", "locators": [
        {"kind": "id", "value": "itemc", "count": 3},
        {"kind": "role", "role": "link", "name": "Laptops", "count": 1}]}  # fmt: skip
    assert stepfacts._target(step) == 'role link "Laptops"'
    step["locators"][0]["count"] = 1
    assert stepfacts._target(step) is None  # an id of one element names it
