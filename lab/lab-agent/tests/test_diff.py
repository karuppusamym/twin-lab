from lab_agent.diff import diff, diff_table, row_hash


def test_insert_update_delete_by_primary_key():
    before = [{"id": 1, "s": "OPEN", "t": "a"}, {"id": 2, "s": "OPEN", "t": "b"}]
    after = [{"id": 1, "s": "RESOLVED", "t": "a"}, {"id": 3, "s": "OPEN", "t": "c"}]
    changes = diff_table("cases", before, after, ["id"])
    assert [(c["op"], c["key"]) for c in changes] == [
        ("insert", "3"),
        ("update", "1"),
        ("delete", "2"),
    ]
    upd = changes[1]
    assert upd["columns"] == ["s"]
    assert upd["before"] == {"s": "OPEN"} and upd["after"] == {"s": "RESOLVED"}


def test_keys_sort_numerically_and_composite():
    after = [{"a": 10, "b": "x"}, {"a": 9, "b": "y"}, {"a": 9, "b": "x"}]
    changes = diff_table("t", [], after, ["a", "b"])
    assert [c["key"] for c in changes] == ["9,x", "9,y", "10,x"]


def test_no_primary_key_is_a_multiset():
    row = {"x": 1}
    before = [row, row, {"x": 2}]
    after = [row, {"x": 2}, {"x": 3}, {"x": 3}]
    changes = diff_table("log", before, after, [])
    ops = sorted((c["op"], c["key"]) for c in changes)
    assert ops == sorted([("insert", row_hash({"x": 3}))] * 2 + [("delete", row_hash({"x": 1}))])


def test_unchanged_gives_nothing_and_new_tables_appear():
    rows = {"a": [{"id": 1}]}
    assert diff(rows, rows, {"a": ["id"]}) == []
    changes = diff({}, {"b": [{"id": 5}]}, {"b": ["id"]})
    assert changes == [{"table": "b", "op": "insert", "key": "5", "after": {"id": 5}}]
