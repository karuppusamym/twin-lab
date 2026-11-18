"""The page-text rule: the application's own interface text is kept, data is masked, a strict
mode masks everything; and identifiers in a schema are quoted for each engine."""

from __future__ import annotations

import sqlite3

import pytest

from labctl import sanitize
from labctl.source import sqlalchemy_models as sam

PAGE = """<!doctype html><html><head><title>Orders - Order saved</title>
<style>body { font-family: Arial, sans-serif; } .x { background: url(/img?token=SECRET9) }</style></head>
<body><nav id="mainNav"><a id="navSearch" href="/orders/search">Search</a></nav>
<h1>Order saved</h1>
<p id="savedMessage">Order <b id="savedNumber">ORD-1026</b> was saved for Ann Hartley.</p>
<p class="notice notice--error" role="alert">The amount is a number greater than 0, such as 120.50.</p>
<p class="hint">An order reference looks like ORD-0000; mail name@example.test for help.</p>
<label for="email">E-mail</label><input id="email" name="email" value="ann@realbank.com">
<label for="q">Search for</label><input id="q" placeholder="Order number">
<select id="terms" name="terms"><option value="NET30">30 days</option></select>
<table id="results"><thead><tr><th>Number</th><th>Customer</th></tr></thead>
<tbody><tr class="order-row"><td class="order-no">ORD-1001</td><td>Ann Hartley</td></tr>
<tr class="order-row"><td class="order-no">ORD-1002</td><td>Ben Okafor</td></tr></tbody></table>
<span class="summary__value" data-field="amount">149.85</span>
<span id="userName">Hartley</span>
<button>Save invoice</button><button>Delete Ann Hartley</button>
</body></html>"""


def test_interface_text_is_kept_and_data_masked():
    with sanitize.page_text("interface"):
        info = sanitize.page(PAGE)
    html = info.html
    for kept in ("<title>Orders - Order saved</title>", "<h1>Order saved</h1>", ">Search<",
                 "The amount is a number greater than 0, such as 999.99.",  # its number is data
                 "ORD-0000", "name@example.test",  # format examples
                 ">E-mail<", ">Search for<", 'placeholder="Order number"', ">30 days<",
                 'value="NET30"', "<th>Number</th>", ">Save invoice<", 'data-field="amount"',
                 "font-family: Arial, sans-serif;", "Order <b", "was saved for"):  # fmt: skip
        assert kept in html, kept
    for leaked in ("ORD-1026", "ORD-1001", "Hartley", "Okafor", "ann@realbank", "120.50",
                   "149.85", "SECRET9"):  # fmt: skip
        assert leaked not in html, leaked
    assert "XXX-9999" in html  # a message's identifier: masked, prefix and all
    assert {
        "selector": "p.notice.notice--error",
        "hidden": False,
        "text": "The amount is a number greater than 0, such as 999.99.",
    } in info.messages


def test_strict_mode_masks_every_text():
    with sanitize.page_text("strict"):
        info = sanitize.page(PAGE)
    for gone in ("Order saved", "Save invoice", "30 days", "E-mail", "The amount", "Order number"):
        assert gone not in info.html, gone
    assert 'id="savedNumber"' in info.html and "Arial" not in info.html


def test_the_mode_comes_from_the_environment_and_unknown_is_strict(monkeypatch):
    monkeypatch.delenv("TWINLAB_PAGE_TEXT", raising=False)
    assert sanitize.page_text_mode() == "interface"  # the documented default
    monkeypatch.setenv("TWINLAB_PAGE_TEXT", "strict")
    assert sanitize.page_text_mode() == "strict"
    monkeypatch.setenv("TWINLAB_PAGE_TEXT", "everything")
    assert sanitize.page_text_mode() == "strict"
    with pytest.raises(ValueError):
        with sanitize.page_text("loose"):
            pass


def test_text_that_changes_between_snapshots_of_a_screen_is_data():
    a = "<html><body><h2 class='t'>Invoice for Acme</h2><p class='lede'>Look it up.</p></body></html>"
    b = "<html><body><h2 class='t'>Invoice for Globex</h2><p class='lede'>Look it up.</p></body></html>"
    first = [sanitize.page(a), sanitize.page(b)]
    vary = sanitize.varying(first)
    html = sanitize.page(a, vary=vary).html
    assert "Acme" not in html and "Invoice for" in html and "Look it up." in html


def test_a_typed_value_never_shows_even_as_interface_text():
    """The recorder puts the shape of a typed value in its place before anything is written; the
    page rule keeps that shape, never the value (which it never sees)."""
    typed = "Mirela Vasquez-Ode"
    recorded = PAGE.replace("Ann Hartley", "A A-A")  # what the recorder wrote in its place
    info = sanitize.page(recorded)
    assert typed not in info.html and "Mirela" not in repr(info.elements)


def test_identifiers_are_quoted_where_an_engine_reserves_them():
    assert sam.quote("order", "sqlite") == '"order"' and sam.quote("order", "postgres") == '"order"'
    assert sam.quote("order", "mssql") == "[order]" and sam.quote("order", "sqlserver") == "[order]"
    assert sam.quote("order", "mysql") == "`order`"
    assert sam.quote("number", "postgres") == '"number"'  # Oracle reserves it: quoted everywhere
    assert (
        sam.quote("customer", "postgres") == "customer" and sam.quote("ORDERS", "mssql") == "ORDERS"
    )
    assert sam.quote('we"ird name', "postgres") == '"we""ird name"'
    assert sam.quote("a]b", "mssql") == "[a]]b]"


@pytest.mark.parametrize("engine", ["sqlite", "postgres", "mssql"])
def test_a_schema_with_reserved_words_is_written_quoted(engine):
    table = sam.Table("invoices", "invoices", None, 0, [
        sam.Column("number", "TEXT", False, primary_key=True), sam.Column("order", "TEXT", True),
        sam.Column("user", "TEXT", True, index=True), sam.Column("total", "NUMERIC", True)])  # fmt: skip
    text = sam.ddl([table], "test", engine)
    q = {"mssql": "[{}]"}.get(engine, '"{}"')
    for name in ("number", "order", "user"):
        assert q.format(name) in text, name
    assert " total NUMERIC" in text  # a plain name stays bare
    if engine == "sqlite":
        db = sqlite3.connect(":memory:")
        db.executescript(text)  # it loads
        db.execute('INSERT INTO invoices ("number", "order", "user", total) VALUES (?, ?, ?, 1)',
                   ("INV-1", "ORD-1", "u"))  # fmt: skip
        assert db.execute('SELECT "order" FROM invoices').fetchone() == ("ORD-1",)
