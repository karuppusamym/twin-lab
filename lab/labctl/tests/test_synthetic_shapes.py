"""Synthetic API examples keep the shape and meaning of what they replace (Demoblaze trial): a
UUID stays a UUID, a cookie field keeps its cookie name, an address stays an address, a short
code list stays as observed, a carried photo keeps its file name, and one record has the same
values in every endpoint. Real data next to them is still replaced."""

from __future__ import annotations

import json
from types import SimpleNamespace

from labctl import apicatalog, seedplan
from labctl.data import observed

GUID = "3f0c2b1e-8a1d-4c55-9f0e-5a7b6c1d2e3f"


def _synth(images=(), seed=None):
    kept = apicatalog.Kept(images=set(images))
    kept.codes["cat"] = {"phone", "notebook", "monitor"}
    world = apicatalog.World(seed or {}, kept, set(), {})
    part = SimpleNamespace(owner={}, raw=[], facts=SimpleNamespace(steps=[]))
    ctx = SimpleNamespace(part=part, known=set(), shapes=set(), values={})
    return apicatalog.Synth(ctx, world, set())


def _ex(route="/view"):
    return seedplan.Ex(None, "POST", route, route, {}, {}, 200, None)


def test_shapes_are_kept_and_values_replaced():
    s = _synth(images={"/imgs/galaxy_s6.jpg"})
    e = _ex()
    made = s.leaf("id", GUID, e, None)
    assert made != GUID and apicatalog.UUID.fullmatch(made)
    cookie = s.leaf("cookie", f"user={GUID}", e, None)
    assert cookie.startswith("user=") and GUID not in cookie
    assert apicatalog.UUID.fullmatch(cookie.split("=", 1)[1])
    url = s.leaf("API_URL", "https://api.shop-real.com", e, None)
    assert url == "https://api.example.test"
    assert s.leaf("img", "imgs/galaxy_s6.jpg", e, None) == "imgs/galaxy_s6.jpg"  # carried photo
    assert s.leaf("img", "imgs/private_scan.jpg", e, None) != "imgs/private_scan.jpg"
    assert s.leaf("cat", "phone", e, None) == "phone"  # a code of the interface
    assert s.leaf("title", "Samsung galaxy s6", e, None) != "Samsung galaxy s6"  # data
    assert s.leaf("owner", "Ann Hartley", e, None) != "Ann Hartley"


def test_cat_is_a_code_list():
    vals = ["phone"] * 7 + ["notebook"] * 6 + ["monitor"] * 2
    assert observed.classify_column("cat", vals)["kind"] == "code"
    names = ["Ann Hartley", "Bo Lind", "Cy Moss", "Di Park"]
    assert observed.classify_column("cat", names)["kind"] != "code"


def test_one_record_has_the_same_values_everywhere():
    s = _synth()
    row = {"id": 1, "title": "Samsung galaxy s6", "price": 360.0, "cat": "phone"}
    listed = s.obj({"Items": [row]}, _ex("/entries"), "entries", False, top=True)
    viewed = s.obj(dict(row), _ex("/view"), "view", False, top=True)
    assert listed["Items"][0] == viewed
    assert viewed["price"] != 360.0 and viewed["title"] != "Samsung galaxy s6"


def test_an_empty_answer_is_said_to_be_empty():
    entry = {"request": {"method": "POST", "url": "https://shop.example/addtocart", "headers": []},
             "response": {"status": 200, "headers": [{"name": "Content-Length", "value": "0"}],
                          "content": {"size": 0, "mimeType": "text/html", "text": ""}}}  # fmt: skip
    got = seedplan._extras(entry, {}, entry["response"]["content"])
    assert got["resp_empty"] and got["resp_length"] == 0
    entry["response"]["headers"] = [{"name": "Content-Length", "value": "16"}]
    got = seedplan._extras(entry, {}, {"size": -1, "mimeType": "application/json"})
    assert not got["resp_empty"] and got["resp_length"] == 16
    assert json.dumps(got)
