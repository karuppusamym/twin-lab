"""Public catalogue data, declared by the owner: off by default, used only when approved, only
for what is declared, and never for a secret, a cookie, an e-mail address or a typed value."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from labctl import apicatalog, publicdata, sanitize, seedplan

TOKEN = "sk9f3k2j1h4g5d6s7a8q9w0e1r2t3y4u"
ITEMS = [
    {"id": 1, "title": "Samsung galaxy s6", "price": 360.0, "cat": "phone",
     "img": "imgs/galaxy_s6.jpg", "seller": "Ann Hartley", "note": "ann.hartley@bank.example",
     "code": TOKEN},
    {"id": 2, "title": "Nokia lumia 1520", "price": 820.0, "cat": "phone",
     "img": "imgs/Lumia_1520.jpg", "seller": "Bo Lind", "note": "x", "code": "y"},
]  # fmt: skip
PAGE = ("<html><body><div id='tbodyid'><h4 class='card-title'><a class='hrefch'>Samsung galaxy s6"
        "</a></h4><h5>$360</h5><p id='seller'>Ann Hartley</p></div><table><tr><td>"
        "Samsung galaxy s6</td><td>360</td></tr></table></body></html>")  # fmt: skip


def _rec(tmp_path: Path, decl: dict | None) -> Path:
    rec = tmp_path / "rec"
    rec.mkdir()
    entry = {"startedDateTime": "2026-01-01T00:00:01Z", "time": 5, "_resourceType": "fetch",
             "request": {"method": "GET", "url": "https://api.shop.example/entries", "headers": []},
             "response": {"status": 200, "content": {"mimeType": "application/json",
                                                     "text": json.dumps({"Items": ITEMS})}}}  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": [entry]}}), encoding="utf-8")
    (rec / "steps.jsonl").write_text("", encoding="utf-8")
    if decl is not None:
        (rec / publicdata.FILE).write_text(yaml.safe_dump(decl), encoding="utf-8")
    return rec


DECL = {"approved_by": "J. Owner", "approved_on": "2026-11-19", "reason": "public catalogue",
        "public": {"endpoints": [{"method": "GET", "path": "/entries",
                                  "fields": ["title", "price", "cat", "img", "note", "code"]}],
                   "images": ["/imgs/"]}}  # fmt: skip
KNOWN = sanitize.names_in({"Items": ITEMS}, prose=False) | sanitize.phrases_in({"Items": ITEMS})


def _synth():
    world = apicatalog.World({}, apicatalog.Kept(), set(), {})
    part = SimpleNamespace(owner={}, raw=[], facts=SimpleNamespace(steps=[]))
    ctx = SimpleNamespace(part=part, known=set(), shapes=set(), values={})
    return apicatalog.Synth(ctx, world, set())


def _outputs():
    html = sanitize.page(PAGE, KNOWN).html
    s = _synth()
    e = seedplan.Ex(None, "GET", "/entries", "/entries", {}, {}, 200, None)
    got = s.obj({"Items": [dict(i) for i in ITEMS]}, e, "entries", False, top=True)["Items"][0]
    return html, got


def test_without_a_declaration_nothing_changes(tmp_path):
    with publicdata.active([_rec(tmp_path, None)]) as pub:
        assert pub is None
        html, got = _outputs()
    assert "galaxy" not in html and got["title"] != "Samsung galaxy s6"


def test_an_unapproved_declaration_changes_nothing(tmp_path):
    decl = {**DECL, "approved_by": None}
    rec = _rec(tmp_path, decl)
    assert "not used" in " ".join(publicdata.load(rec / publicdata.FILE).problems)
    with publicdata.active([rec]) as pub:
        assert pub is None
        html, got = _outputs()
    assert "galaxy" not in html and got["price"] != 360.0


def test_declared_fields_come_through_and_the_rest_stays_masked(tmp_path):
    with publicdata.active([_rec(tmp_path, DECL)]) as pub:
        assert pub is not None and pub.image("/imgs/galaxy_s6.jpg")
        html, got = _outputs()
        assert "J. Owner" in pub.note() and "`GET /entries`" in pub.note()
    assert ">Samsung galaxy s6<" in html and ">$360<" in html  # the card: declared values
    assert "Ann" not in html and "Hartley" not in html  # an undeclared field next to them
    assert "<td>Samsung" not in html and "<td>360" not in html  # a data cell (a cart row)
    assert got["title"] == "Samsung galaxy s6" and got["price"] == 360.0 and got["cat"] == "phone"
    assert got["img"] == "imgs/galaxy_s6.jpg"
    assert got["seller"] != "Ann Hartley"  # not declared


def test_a_secret_inside_a_declared_field_is_still_refused(tmp_path):
    with publicdata.active([_rec(tmp_path, DECL)]):
        _, got = _outputs()
    assert got["note"] != "ann.hartley@bank.example"  # an e-mail address in a declared field
    assert got["code"] != TOKEN  # a token in a declared field
    assert publicdata.refused("cookie", "user=abc") and publicdata.refused("x", "A9-AAA")
    assert publicdata.refused("password", "Samsung") and not publicdata.refused("title", "Nexus 6")


def test_the_draft_marks_nothing_public(tmp_path):
    rec = _rec(tmp_path, None)
    doc = yaml.safe_load(publicdata.draft(rec))
    assert doc["approved_by"] is None and doc["public"] == {"endpoints": [], "regions": [],
                                                             "images": []}  # fmt: skip
    assert doc["seen"]["endpoints"][0]["path"] == "/entries"
    assert "title" in doc["seen"]["endpoints"][0]["fields"]
