"""Where a typed value is taken out of a recording, and where it is kept as structure
(labctl.typedvalues).

Seen on a PrimeFaces application: typing "report" turned the id `main:navReports` into
`main:navAs` in every snapshot, which broke that element's selector in the rebuild and in the
replay. A typed value is now taken out only where it is a value (text, form values, content
attributes, the query of an address, JSON strings), as a whole token; ids, classes and names
keep it, and one that IS a typed value becomes a placeholder the rebuild and the replay share.

The first tests need no browser; the last records a small page, checks everything written and
replays it on a fresh copy. Everything is served from 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import pytest
import yaml

from labctl import locate, recorder, typedvalues

NAV = '<a id="main:navReports" class="ui-menuitem-link" href="#">Reports</a>'


def _secrets(*values: str) -> recorder._Secrets:
    s = recorder._Secrets()
    for n, v in enumerate(values, 1):
        s.add(v, "http://x", f"t{n}")
        s.place(f"t{n}", n)
    return s


# --- no browser ------------------------------------------------------------------------------


def test_typing_report_next_to_main_nav_reports_keeps_the_id():
    s = _secrets("report")
    html = (f"<nav>{NAV}</nav><label for='main:filter'>Filter</label>"
            '<input id="main:filter" name="main:filter" value="report">'
            '<script>PrimeFaces.cw("Menu","widget_main_navReports",{id:"main:navReports"});</script>'
            '<style>#main\\:navReports{color:red}</style>')  # fmt: skip
    out = s.redact_html(html)
    assert out.count("main:navReports") == 2 and "#main\\:navReports{color:red}" in out
    assert 'href="#"' in out  # an address with nothing typed in it: as it was
    assert "main:navAs" not in out and ">Reports<" in out  # a longer word: kept
    assert 'value="A"' in out  # the field's value: its shape
    assert "report" not in out.replace("Report", "").replace("main:filter", "")
    step = {
        "selector": "a#main\\:navReports",
        "locators": [{"kind": "id", "value": "main:navReports", "count": 1},
                     {"kind": "role", "role": "link", "name": "Reports", "count": 1},
                     {"kind": "structure", "css": "nav > a#main\\:navReports", "index": 0,
                      "count": 1}],
    }  # fmt: skip
    clean = s.clean_step(step)
    assert clean["selector"] == step["selector"] and clean["locators"] == step["locators"]
    assert "typed_placeholders" not in clean
    # the HAR: a JSF partial response and the form post keep the ids
    har = {"request": {"postData": {"text": "javax.faces.source=main%3AnavReports&q=report",
                                    "params": [{"name": "main:navReports", "value": "report"}]}},
           "response": {"content": {"text": '<partial-response><update id="main:content">'
                                             f"<![CDATA[{NAV}]]></update></partial-response>"}}}  # fmt: skip
    scrubbed = json.dumps(s.scrub(har))
    assert scrubbed.count("navReports") == 3 and "main:navAs" not in scrubbed
    assert "report" not in scrubbed.replace("Report", "")


def test_typing_a_name_that_shows_in_a_text_node_removes_it():
    s = _secrets("Rosalind Avery")
    html = ("<ul><li class='greeting'>Hello Rosalind  Avery, welcome</li>"
            "<li title='Rosalind Avery'>Rosalind Avery</li><li>Rosalind Averyson</li></ul>")  # fmt: skip
    out = s.redact_html(html)
    assert "Hello A  A, welcome" in out and "<li title='A A'>A A</li>" in out
    assert "Rosalind Avery<" not in out and "Averyson" in out  # part of a longer word: kept
    # a locator whose text shows the name is dropped; dialog messages and storage keys too
    step = {"locators": [{"kind": "text", "tag": "li", "text": "Hello Rosalind Avery"},
                         {"kind": "structure", "css": "ul > li", "index": 0, "count": 2}],
            "storage": {"localStorage": {"draft-Rosalind Avery": "A"}}}  # fmt: skip
    clean = s.clean_step(step)
    assert [c["kind"] for c in clean["locators"]] == ["structure"]
    assert list(clean["storage"]["localStorage"]) == ["draft-A A"]
    assert recorder.dialog_message("Saved Rosalind Avery", s) == "Saved A A"


def test_a_typed_value_inside_a_class_name_is_kept_as_structure():
    s = _secrets("panel")
    html = (
        '<div class="report-panel panel-body ui-panel" data-widget="widget_panelTop">'
        '<span class="panel">Open the panel</span></div><style>.report-panel{}</style>'
    )
    out = s.redact_html(html)
    assert 'class="report-panel panel-body ui-panel"' in out
    assert 'data-widget="widget_panelTop"' in out and ".report-panel{}" in out
    assert "Open the A</span>" in out  # in text, a whole word: its shape
    assert 'class="tl-typed-a"' in out  # a class that IS the value: the placeholder
    clean, shown = s.redact_css("div.report-panel > span.panel")
    assert clean == "div.report-panel > span.tl-typed-a" and not shown


def test_a_typed_value_equal_to_a_whole_id_gets_the_placeholder_and_the_step_says_so():
    s = _secrets("ledger")
    html = (
        '<label for="ledger">Book</label><button id="ledger" type="button">Open ledger'
        '</button><a href="#ledger">Jump</a><script>$("#ledger").show()</script>'
    )
    used: set[str] = set()
    out = s.redact_html(html, used)
    assert used == {"tl-typed-a"}
    assert 'for="tl-typed-a"' in out and 'id="tl-typed-a"' in out and 'href="#tl-typed-a"' in out
    assert '$("#tl-typed-a")' in out and 'type="button"' in out
    assert "ledger" not in out.lower()
    step = {"selector": "button#ledger",
            "locators": [{"kind": "id", "value": "ledger", "count": 1},
                         {"kind": "role", "role": "button", "name": "Open ledger", "count": 1},
                         {"kind": "structure", "css": "body > button#ledger", "index": 0,
                          "count": 1}]}  # fmt: skip
    clean = s.clean_step(step)
    assert clean["selector"] == "button#tl-typed-a"
    assert clean["locators"] == [
        {"kind": "id", "value": "tl-typed-a", "count": 1},
        {"kind": "structure", "css": "body > button#tl-typed-a", "index": 0, "count": 1},
    ]
    assert clean["typed_placeholders"] == [{"placeholder": "tl-typed-a", "typed_at": 1}]
    assert "ledger" not in json.dumps(clean)
    # each typed value has its own placeholder, letters only (no digit masking changes it)
    assert typedvalues.placeholder_name(0) == "tl-typed-a"
    assert typedvalues.placeholder_name(26) == "tl-typed-aa"
    # the replay tries the placeholder (the rebuilt page) and then the value it typed (the
    # application itself)
    rec_step = SimpleNamespace(selector=clean["selector"], locators=clean["locators"],
                               typed_placeholders=clean["typed_placeholders"])  # fmt: skip
    typed = locate.typed_values(rec_step, {1: "ledger"})
    assert typed == {"tl-typed-a": "ledger"}
    cands = locate.candidates(rec_step, typed)
    assert [locate.describe(c) for c in cands] == [
        "#tl-typed-a", "#ledger", "structure body > button#tl-typed-a [0 of 1]",
        'structure body > button[id="ledger"] [0 of 1]', "css button#tl-typed-a",
        'css button[id="ledger"]',
    ]  # fmt: skip


def test_a_typed_value_in_an_address_query_is_removed():
    s = _secrets("report", "Water plants")
    url = "http://x/reports/report?q=report&t=Water%20plants&u=Water+plants&r=reports#report"
    assert s.redact_url(url) == "http://x/reports/A?q=A&t=A%9A&u=A+A&r=reports#A"
    html = '<a href="/search?q=report&amp;page=2" class="nav">Search</a>'
    assert s.redact_html(html) == '<a href="/search?q=A&amp;page=2" class="nav">Search</a>'
    har = {"url": url, "queryString": [{"name": "q", "value": "report"}],
           "headers": [{"name": "Referer", "value": "http://x/app?q=Water+plants"}]}  # fmt: skip
    text = json.dumps(s.scrub(har)).lower()
    for v in ("report", "water plants"):
        for form in (v, quote(v), quote_plus(v)):
            assert form not in text.replace("reports", "")
    step = s.clean_step({"url": url})
    assert step["url"].startswith("http://x/reports/A?q=A")


def test_a_typed_number_the_application_echoes_as_a_json_number_is_its_shape():
    s = _secrets("149.85", "ab")
    body = '{"amount": 149.85, "ref": "ab", "items": [{"note": "paid 149.85 today"}]}'
    out = s.scrub({"text": body})["text"]
    assert "149.85" not in out and json.loads(out)["amount"] == 9.9
    assert json.loads(out)["ref"] == "A"  # a short value where it is the whole string


# --- a recording of a small page, replayed ----------------------------------------------------

PAGE = b"""<!DOCTYPE html><html><body>
<nav><a id="main:navReports" class="ui-menuitem-link" href="#">Reports</a></nav>
<input id="main:search" aria-label="Search">
<button id="main:go" type="button">Find</button>
<div id="results" class="report-panel"></div>
<input id="who" aria-label="Name"><button id="greet" type="button">Greet</button>
<p id="greeting"></p>
<input id="code" aria-label="Code"><button id="ledger" type="button">Open ledger</button>
<p id="opened"></p>
<script>
const $ = (id) => document.getElementById(id);
$("main:go").addEventListener("click", () => {
  fetch("/api/find?q=" + encodeURIComponent($("main:search").value))
    .then((r) => r.json()).then((d) => { $("results").textContent = d.rows + " results"; });
});
$("main:navReports").addEventListener("click", (e) => {
  e.preventDefault();
  $("results").className = "report-panel shown";
});
$("greet").addEventListener("click", () => {
  $("greeting").textContent = "Hello " + $("who").value + ", welcome";
});
$("ledger").addEventListener("click", () => { $("opened").textContent = "Opened"; });
</script></body></html>"""

TYPED = ["report", "Rosalind Avery", "ledger"]


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the page, and a search that echoes what it was given
            body, kind = PAGE, "text/html"
            if self.path.startswith("/api/find"):
                q = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                body, kind = json.dumps({"query": q, "rows": 2}).encode(), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _flow(page):
    page.wait_for_timeout(600)
    page.fill('[id="main:search"]', TYPED[0])
    page.click('[id="main:go"]')
    page.wait_for_timeout(600)
    page.click('[id="main:navReports"]')
    page.wait_for_timeout(600)
    page.fill("#who", TYPED[1])
    page.click("#greet")
    page.wait_for_timeout(600)
    page.fill("#code", TYPED[2])
    page.click("#ledger")
    page.wait_for_timeout(600)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    server, base = _serve()
    out = tmp_path_factory.mktemp("typed-values") / "rec"
    try:
        recorder.record(f"{base}/", out, headless=True, script=_flow)
    finally:
        server.shutdown()
    steps = [json.loads(x) for x in (out / "steps.jsonl").read_text("utf-8").splitlines() if x]
    typed = [s["seq"] for s in steps if s["action"] == "type"]
    (out / "bindings.yaml").write_text(
        yaml.safe_dump({"version": 1, "steps": {k: {"value": v}
                                                for k, v in zip(typed, TYPED, strict=True)}}),
        "utf-8",
    )  # fmt: skip
    return out, base, steps


def test_a_recording_keeps_ids_takes_out_values_and_replays(recorded):
    rec, base, steps = recorded
    files = [p for p in rec.rglob("*") if p.is_file() and p.suffix not in (".png", ".yaml")]
    texts = {p.name: p.read_text("utf-8").lower() for p in files}
    # no typed value anywhere (a longer word or identifier holding one is not the value)
    for name, text in texts.items():
        bare = text.replace("navreports", "").replace("report-panel", "").replace("reports", "")
        for v in TYPED:
            for form in {v.lower(), quote(v).lower(), quote_plus(v).lower()}:
                assert form not in bare, (name, v)
    last = (rec / steps[-1]["dom"]).read_text("utf-8")
    assert 'id="main:navReports"' in last and ">Reports<" in last  # the defect: main:navAs
    assert 'class="report-panel shown"' in last  # structure that holds the value: kept
    assert "Hello A A, welcome" in last  # the name in a text node: its shape
    assert 'id="tl-typed-c"' in last  # an id that IS the value: its placeholder
    by_target = {s.get("selector"): s for s in steps if s["action"] == "click"}
    nav = by_target["a#main\\:navReports"]
    assert {"kind": "id", "value": "main:navReports", "count": 1} in nav["locators"]
    assert any(c.get("name") == "Reports" for c in nav["locators"])
    ledger = by_target["button#tl-typed-c"]
    typed_at = [s["seq"] for s in steps if s["action"] == "type"][2]
    assert ledger["typed_placeholders"] == [{"placeholder": "tl-typed-c", "typed_at": typed_at}]
    assert ledger["locators"][0] == {"kind": "id", "value": "tl-typed-c", "count": 1}
    har = json.loads((rec / "recording.har").read_text("utf-8"))
    find = next(e for e in har["log"]["entries"] if "/api/find" in e["request"]["url"])
    assert find["request"]["url"].endswith("/api/find?q=A")  # the query: the value's shape
    assert json.loads(find["response"]["content"]["text"]) == {"query": "A", "rows": 2}

    # the rebuilt page (the snapshot as recorded) has the placeholder: the step finds it there
    from playwright.sync_api import sync_playwright

    from labctl.botlog.recording import load

    step = next(s for s in load(rec).steps if s.seq == ledger["seq"])
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(last)
            loc, found = locate.resolve(page, step, timeout_ms=2000)
            assert found == "#tl-typed-c" and loc.inner_text() == "Open A"
        finally:
            browser.close()

    # and the application itself: the replay types "ledger" and finds the button by it
    from labctl.behaviour import fidelity

    server, fresh = _serve()
    try:
        report = fidelity.replay(rec, origins={base: fresh}, resolve_timeout_ms=3000)
    finally:
        server.shutdown()
    diffs = [d.text() for s in report.steps for d in s.differences]
    assert report.status == "pass" and diffs == [], report.markdown()
    assert report.coverage.failed == 0, report.coverage


def test_the_rule_is_documented_where_the_recorder_and_sanitiser_say_it():
    assert "labctl.typedvalues" in (recorder._Secrets.__doc__ or "")
    doc = Path(typedvalues.__file__).read_text("utf-8")
    assert "tl-typed-a" in doc and "whole token" in doc
