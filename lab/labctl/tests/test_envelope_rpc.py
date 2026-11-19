"""Envelope answers in any case (`{"Items": [...]}`) are lists of records, and a remote-procedure
API (`POST /viewcart`, `/addtocart`, `/deleteitem`) shows its tables through POST answers."""

from __future__ import annotations

import json
from pathlib import Path

from labctl import har, seedplan

PRODUCTS = {"Items": [{"id": 1, "title": "Okonkwo lamp", "cat": "lamp", "price": 360.0},
                      {"id": 2, "title": "Brannigan desk", "cat": "desk", "price": 820.0}],
            "LastEvaluatedKey": {"id": "2"}}  # fmt: skip
CART = {"Items": [{"id": "3f0c2b1e-8a1d-4c55-9f0e-5a7b6c1d2e3f", "cookie": "user=x", "prod_id": 1}]}


def test_envelope_keys_match_in_any_case():
    assert har.list_key(PRODUCTS) == "Items"
    assert har.list_key({"DATA": [{"a": 1}]}) == "DATA"
    assert har.list_key({"items": "not a list"}) is None
    assert [i["id"] for i in har._items(PRODUCTS)] == [1, 2]
    assert har.is_list_answer(PRODUCTS) and not har.is_list_answer({"id": 1})
    assert seedplan._items(PRODUCTS) is not None


def test_rpc_nouns():
    assert har.rpc_noun("/addtocart") == "cart"
    assert har.rpc_noun("/viewcart") == "cart"
    assert har.rpc_noun("/deleteitem") == "item"
    assert har.rpc_noun("/get_orders") == "orders"
    assert har.rpc_noun("/entries") is None and har.rpc_noun("/bycat") is None


def _rec(tmp_path: Path) -> Path:
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    page = "<html><body><h1>Shop</h1><div id='tbodyid'></div></body></html>"

    def call(t, method, url, answer, body=None, ref="https://www.shop.example/"):
        entry = {"startedDateTime": f"2026-01-01T00:00:0{t}Z", "time": 5,
                 "_resourceType": "fetch",
                 "request": {"method": method, "url": url,
                             "headers": [{"name": "Referer", "value": ref}]},
                 "response": {"status": 200, "content": {
                     "mimeType": "application/json" if answer is not None else "text/html",
                     "text": json.dumps(answer) if answer is not None else ""}}}  # fmt: skip
        if body is not None:
            entry["request"]["postData"] = {"mimeType": "application/json",
                                            "text": json.dumps(body)}  # fmt: skip
        return entry

    entries = [
        {"startedDateTime": "2026-01-01T00:00:00Z", "time": 5, "_resourceType": "document",
         "request": {"method": "GET", "url": "https://www.shop.example/cart.html", "headers": []},
         "response": {"status": 200, "content": {"mimeType": "text/html", "text": page}}},
        call(1, "GET", "https://api.shop.example/entries", PRODUCTS),
        call(2, "POST", "https://api.shop.example/bycat", PRODUCTS, {"cat": "lamp"}),
        call(3, "POST", "https://api.shop.example/addtocart", None, {"id": "x", "prod_id": 1}),
        call(4, "POST", "https://api.shop.example/viewcart", CART, {"cookie": "user=x"}),
        call(5, "POST", "https://api.shop.example/deleteitem", None, {"id": "x"}),
    ]  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    (rec / "dom" / "0001.html").write_text(page, encoding="utf-8")
    step = {"seq": 1, "ts": "2026-01-01T00:00:00.500Z", "url": "https://www.shop.example/cart.html",
            "action": "goto", "dom": "dom/0001.html"}  # fmt: skip
    (rec / "steps.jsonl").write_text(json.dumps(step) + "\n", encoding="utf-8")
    return rec


def test_items_become_the_table_and_a_cart_is_inferred(tmp_path):
    model = har.analyse("shop", _rec(tmp_path), tmp_path)
    tables = {t.name: sorted(c.name for c in t.columns) for t in model.tables}
    assert tables["entries"] == ["cat", "id", "price", "title"]
    assert tables["cart"] == ["cookie", "id", "prod_id"]
    assert "lastevaluatedkey" not in tables and "items" not in tables
    by = {r.key: r.tables for r in model.routes}
    assert by["POST /bycat"] == ["entries"]
    assert by["POST /addtocart"] == ["cart"] and by["POST /viewcart"] == ["cart"]
    assert by["POST /deleteitem"] == ["cart"]
