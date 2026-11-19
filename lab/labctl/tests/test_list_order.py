"""A list's order is a rule only when the evidence shows it (`labctl.listorder`), and the drafts
compare a list in any order (`unordered: true`) unless it does.

Demoblaze round 3: the cart lists its rows by random ids (UUIDs the page makes) fetched in
parallel, and the one recorded view of two rows pinned an order the original does not keep.
"""

from __future__ import annotations

from types import SimpleNamespace

from labctl import listorder
from labctl.behaviour import drafts

U1, U2, U3 = ("86792c5e-f198-6c3e-f81a-792961572956", "d55d88fb-cc3c-b09c-8411-91d7f6d45319",
              "f0e1d2c3-b4a5-9687-7869-5a4b3c2d1e0f")  # fmt: skip


def test_random_values_are_recognised():
    assert listorder.is_random(U1) and listorder.is_random(f"deleteItem('{U1}')")
    assert listorder.is_random("5f2b7c9e1a3d4b6c8e0f1a2b")  # an object id / hash
    assert listorder.is_random("aZ3kP9qR7tX2mN5vB8")  # a random token
    assert not any(listorder.is_random(v) for v in (7, "42", "Nokia lumia 1520", "2026-11-19"))


def test_one_view_or_random_keys_is_not_stable():
    one = listorder.judge([[{"id": U1, "n": 2}, {"id": U2, "n": 1}]])
    assert not one.stable and one.label == "not stable (one answer)"
    assert "`id` ascending (random values)" in one.reason
    rnd = listorder.judge([[{"id": U1}, {"id": U2}], [{"id": U1}, {"id": U3}]])
    assert not rnd.stable and rnd.label == "not stable (random keys)"
    same_two = listorder.judge([[{"id": 1}, {"id": 2}], [{"id": 1}, {"id": 2}]])
    assert not same_two.stable and same_two.label == "not stable (too few rows)"


def test_agreeing_views_on_a_plain_key_or_a_sort_control_are_stable():
    ok = listorder.judge([[{"id": 1}, {"id": 2}, {"id": 5}], [{"id": 3}, {"id": 4}]])
    assert ok.stable and (ok.key, ok.direction) == ("id", "ascending")
    assert ok.label == "rows ordered by `id` ascending in every recorded answer"
    sorted_once = listorder.judge([[{"t": "b"}, {"t": "a"}]], sort_used=True)
    assert sorted_once.stable and "sort control" in sorted_once.label


def _cart(*rows: tuple[str, str]) -> str:
    trs = "".join(f'<tr class="success"><td><img src="i.jpg"></td><td>{t}</td><td>9</td>'
                  f"<td><a href=\"#\" onclick=\"deleteItem('{u}')\">Delete</a></td></tr>"
                  for t, u in rows)  # fmt: skip
    return f'<html><body><table><tbody id="tbodyid">{trs}</tbody></table></body></html>'


def _part(views: dict[int, str], extra: list[dict] = ()) -> SimpleNamespace:
    raw = [{"seq": 2, "action": "click", "selector": "a.hrefch"},
           {"seq": 4, "action": "click", "selector": "a.hrefch"}, *extra]  # fmt: skip
    raw += [{"seq": n, "action": "goto", "dom": f"dom/{n:04}.html"} for n in views]
    return SimpleNamespace(raw=sorted(raw, key=lambda s: s["seq"]), doms=views, befores={})


RECORDS = [{"n": 2, "record": "Samsung galaxy s6"}, {"n": 4, "record": "Nokia lumia 1520"}]


def test_a_cart_shown_once_with_random_row_ids_is_drafted_unordered():
    part = _part({6: _cart(("Nokia lumia 1520", U1), ("Samsung galaxy s6", U2)),
                  8: _cart(("Samsung galaxy s6", U2))})  # fmt: skip
    cases = drafts.acceptance_cases(part, RECORDS)
    lists = [c for c in cases if c["check"] == "texts"]
    assert [c["after"] for c in lists] == [6, 8]
    two, one = lists
    assert two["locator"] == "#tbodyid > tr.success > td:nth-child(2)"
    assert two["equals"] == [{"record": 4}, {"record": 2}] and two["unordered"] is True
    assert "not stable (one page view)" in two["note"] and two["required"] is False
    assert one["equals"] == [{"record": 2}] and "unordered" not in one
    # no recorded value (a row id) reaches the draft
    assert U1 not in str(cases) and U2 not in str(cases)


def test_a_list_shown_in_one_order_by_a_plain_key_twice_keeps_its_order():
    def rows(*ids):
        items = "".join(f'<li data-id="{i}"><span>{t}</span></li>' for i, t in ids)
        return f'<html><body><ul id="items">{items}</ul></body></html>'

    records = [*RECORDS, {"n": 5, "record": "Sony xperia z5"}]
    part = _part({6: rows((1, "Samsung galaxy s6"), (2, "Nokia lumia 1520")),
                  9: rows((1, "Samsung galaxy s6"), (2, "Nokia lumia 1520"), (3, "Sony xperia z5"))},
                 [{"seq": 5, "action": "click", "selector": "a.hrefch"}])  # fmt: skip
    lists = [c for c in drafts.acceptance_cases(part, records) if c["check"] == "texts"]
    assert len(lists) == 2 and not any(c.get("unordered") for c in lists)
    assert "order stable" in lists[0]["note"]
    assert lists[1]["equals"] == [{"record": 2}, {"record": 4}, {"record": 5}]


def test_a_sort_control_the_recording_used_makes_the_order_stable():
    sort = [{"seq": 5, "action": "click", "selector": "th.sort-title"}]
    part = _part({6: _cart(("Nokia lumia 1520", U1), ("Samsung galaxy s6", U2))}, sort)
    (case,) = [c for c in drafts.acceptance_cases(part, RECORDS) if c["check"] == "texts"]
    assert "unordered" not in case and "sort control" in case["note"]


def test_a_list_with_rows_the_flow_did_not_act_on_or_a_single_title_is_not_drafted():
    part = _part({6: _cart(("Nokia lumia 1520", U1), ("Sony xperia z5", U3)),
                  8: _cart(("Samsung galaxy s6", U2))})  # fmt: skip
    assert not [c for c in drafts.acceptance_cases(part, RECORDS) if c["check"] == "texts"]


def test_the_whole_recording_maps_each_record_of_a_list():
    from labctl import seedplan

    glob = {2: 13, 4: 20, 6: 26}
    got = seedplan.global_refs([{"record": 4}, {"record": 2}], glob)
    assert got == [{"record": 20}, {"record": 13}]
    assert seedplan.global_refs({"binding": 6}, glob) == {"binding": 26}
    assert seedplan.global_refs("1180", glob) == "1180"
    assert seedplan.global_refs([{"record": 4}, {"record": 9}], glob) is None
