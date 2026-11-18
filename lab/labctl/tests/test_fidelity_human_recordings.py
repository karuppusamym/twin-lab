"""Fidelity replay on a recording a person made: record ids are masked and typed values are not
kept, and the demo app's data is never the real app's."""

from types import SimpleNamespace

from labctl.behaviour import fidelity


def page(rows: int, value: str = "") -> str:
    links = "".join(
        f'<a id="loan-L-2000{i:02d}" class="loan-link" href="#">x</a>' for i in range(rows)
    )
    return f'<html><body><input id="loanQuery" value="{value}"><div id="list">{links}</div></body></html>'


def checks(diffs):
    return sorted((d.check, d.element) for d in diffs)


def test_a_different_number_of_records_is_not_a_difference():
    assert fidelity.compare_dom(page(4), page(50)) == []
    assert fidelity.compare_dom(page(50), page(1)) == []


def test_records_on_one_side_only_is_a_difference():
    assert [d.check for d in fidelity.compare_dom(page(0), page(3))] == ["extra"]
    assert [d.check for d in fidelity.compare_dom(page(3), page(0))] == ["missing"]


def test_a_supplied_value_is_not_compared_by_format():
    recorded, live = page(1, "Smith"), page(1, "L-2000")
    assert ("value_format", "#loanQuery") in checks(fidelity.compare_dom(recorded, live))
    assert fidelity.compare_dom(recorded, live, supplied={"#loanQuery"}) == []


def test_a_value_can_be_given_for_one_step():
    rec = SimpleNamespace(dom=lambda step: None)
    values = {"6:#loanQuery": "first", "#loanQuery": "any"}
    step = lambda seq: SimpleNamespace(seq=seq, selector="#loanQuery", value=None)  # noqa: E731
    assert fidelity._value_for(rec, step(6), values) == "first"
    assert fidelity._value_for(rec, step(10), values) == "any"
    assert fidelity._value_for(rec, step(10), {}) is None
    assert fidelity._selector_of("6:#loanQuery") == "#loanQuery"
    assert fidelity._selector_of("#a:hover") == "#a:hover"
