"""Record numbers built into ids, links, titles and recorded selectors never leave the evidence."""

from labctl import sanitize
from labctl.behaviour import dom

PAGE = """<html><head><title>Loan L-200001 - Quality Tracking Tool</title></head><body>
<h1>Review <span id="reviewNo">R-1003</span></h1>
<a id="open-R-1001" href="/reviews/R-1001" class="row-R-1001 review-link">Open</a>
<tr id="loan-L-200040" data-loan="L-200040"><td>Avery Holt</td></tr>
<select id="result-CRD-01"><option>Pass</option></select>
<input id="comment-CRD-01" value="call 555-0142 re acct 4471000012">
</body></html>"""


def test_record_numbers_are_masked_everywhere_in_a_page():
    info = sanitize.page(PAGE)
    for leaked in ("L-200001", "R-1003", "R-1001", "L-200040", "4471000012", "Avery"):
        assert leaked not in info.html, leaked
        assert not any(leaked in i for i in info.ids | info.classes), leaked
    assert "open-R-9999" in info.ids and "loan-L-999999" in info.ids
    assert "<h1>Review " in info.html  # the application's own words stay
    # A run of Title-case words in a title is treated as a possible name: masked, deliberately.


def test_short_application_codes_stay():
    info = sanitize.page(PAGE)
    assert {"result-CRD-01", "comment-CRD-01"} <= info.ids


def test_identifier_masking_rules():
    assert sanitize.mask_identifiers("loan-L-200040") == "loan-L-999999"
    assert sanitize.mask_identifiers("/reviews/R-1001?x=1") == "/reviews/R-9999?x=1"
    assert sanitize.mask_identifiers("save-CRD-01") == "save-CRD-01"
    assert sanitize.mask_identifiers("col-md-12") == "col-md-12"


def test_behaviour_catalog_sees_masked_ids():
    snap = dom.snapshot(PAGE)
    html = repr(snap)
    assert "R-1001" not in html and "L-200040" not in html


REVIEWER_PAGE = """<html><head><title>Jane Doe - Customer 360</title>
<meta name="csrf-token" content="kQ7fLm2ZpXa9RtYbNc3VwEoHs8UiGd1JxAz5TqMn">
<meta name="viewport" content="width=device-width"></head><body>
<h1>Customer: Jane Doe</h1>
<table><tr><th scope="row">Jane Doe</th><td title="Jane Doe, 12 Elm Street">Jane Doe</td></tr></table>
<a href="mailto:jane.doe@realbank.com">mail</a>
<select id="advisor"><option>Robert Paulson</option><option>Marla Singer</option></select>
<input id="q" placeholder="Jane Doe" value="secret">
<button>Delete Jane Doe</button><button>Sign in</button>
<div style="background:url(/photo?token=abcSECRETdef)">x</div>
</body></html>"""


def test_the_reviewers_page_leaks_nothing_and_keeps_the_app_words():
    info = sanitize.page(REVIEWER_PAGE)
    text = info.html + repr(info.elements) + info.title
    for leaked in ("Jane", "Doe", "kQ7fLm2Z", "jane.doe@", "Robert Paulson", "Marla", "Elm Street",
                   "abcSECRETdef", "realbank"):  # fmt: skip
        assert leaked not in text, leaked
    assert "Sign in" in info.html and "width=device-width" in info.html


def test_paths_never_keep_session_ids_emails_or_keys():
    assert sanitize.route_pattern("/app/home.jsf;jsessionid=0A1B2C3D") == "/app/home.jsf"
    assert (
        sanitize.route_pattern("/customers/jane.doe@realbank.com/orders")
        == "/customers/{id}/orders"
    )
    assert sanitize.route_pattern("/api/accounts/12345678:close") == "/api/accounts/{id}"
    assert sanitize.route_pattern("/customers/doe,jane") == "/customers/{id}"
