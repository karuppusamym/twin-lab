"""The API catalogue states a list's order as a rule only when the answers show it.

Demoblaze round 3: `POST /viewcart` was stated as "rows ordered by `id` ascending in every
recorded answer" from one two-row answer whose ids are random UUIDs, and its synthetic example
listed `...0005` before `...0003`, against that rule.
"""

from __future__ import annotations

from types import SimpleNamespace

from labctl import apicatalog

U1, U2, U3 = ("86792c5e-f198-6c3e-f81a-792961572956", "d55d88fb-cc3c-b09c-8411-91d7f6d45319",
              "f0e1d2c3-b4a5-9687-7869-5a4b3c2d1e0f")  # fmt: skip
COOKIE = "user=0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"


def test_one_answer_is_not_a_rule():
    cart = [
        [{"cookie": COOKIE, "id": U1, "prod_id": 2}, {"cookie": COOKIE, "id": U2, "prod_id": 1}]
    ]
    said = apicatalog.observed_order(cart)
    assert said.startswith("not stable (one answer)"), said
    assert "`id` ascending (random values)" in said and "must not pin" in said
    assert "every recorded answer" not in said.split(":")[0]


def test_random_keys_are_not_a_rule_even_in_several_answers():
    said = apicatalog.observed_order([[{"id": U1}, {"id": U2}], [{"id": U1}, {"id": U3}]])
    assert said.startswith("not stable (random keys)"), said


def test_several_answers_on_a_plain_key_stay_a_rule():
    lists = [[{"id": 1, "t": "b"}, {"id": 2, "t": "a"}], [{"id": 3, "t": "c"}, {"id": 5, "t": "d"}]]
    assert (
        apicatalog.observed_order(lists)
        == "rows ordered by `id` ascending in every recorded answer"
    )


def test_the_endpoint_says_its_examples_order_is_not_a_rule():
    def ex(rows):
        return SimpleNamespace(status=200, data={"Items": rows})

    cart = ex([{"id": U1, "prod_id": 2}, {"id": U2, "prod_id": 1}])
    note = apicatalog.order_note([cart, ex([{"id": U2, "prod_id": 1}])])
    assert note and "not stable (one answer)" in note and "one possible order" in note
    assert U1 not in note and U2 not in note
    entries = [ex([{"id": i} for i in (1, 2, 3)]), ex([{"id": i} for i in (4, 5)])]
    assert apicatalog.order_note(entries) is None
    assert apicatalog.order_note([ex([{"id": 1}])]) is None


def test_the_brief_names_an_unstable_order_as_an_order_not_every_answer():
    index = {"endpoints": [], "lists": [{"endpoint": "POST /viewcart", "answers": 1,
                                          "parameters": {},
                                          "order": apicatalog.observed_order(
                                              [[{"id": U1}, {"id": U2}]])}]}  # fmt: skip
    text = apicatalog.brief_section("shop", {"index": index, "endpoints": {}})
    assert "  - order: not stable" in text and "every answer: not stable" not in text
