"""E-02: app models from browser recordings (HAR plus page snapshots), and their privacy."""

import json
import shutil
import socket
from pathlib import Path

import pytest
import yaml

from labctl import brief as brief_mod
from labctl import har, sanitize
from labctl import model_build as mb
from labctl.models import App, Automation
from labctl.source import same_shape

ROOT = Path(__file__).resolve().parents[3]
REF = ROOT / "reference" / "case-workbench"
SAMPLE = ROOT / "automations" / "uipath-case-update" / "automation.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "showcase-rec"


def _sample() -> Automation:
    return Automation.model_validate(yaml.safe_load(SAMPLE.read_text(encoding="utf-8")))


def _auto(touches: list[dict], auto_id: str = "bot") -> Automation:
    return Automation.model_validate(
        {"id": auto_id, "name": auto_id, "type": "uipath", "surface": "combo",
         "source": {"path": "."}, "touches": touches}
    )  # fmt: skip


def _dump(model) -> str:
    return yaml.safe_dump(model.model_dump(mode="json", by_alias=True, exclude_none=True))


@pytest.fixture(scope="module")
def recorded():
    return mb.from_recording("showcase", FIXTURE, ROOT, None)


# --- route patterns ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        ("/reviews/R-1001", "/reviews/{id}"),
        ("/reviews/R-1002", "/reviews/{id}"),
        ("/api/cases/C-1001/notes", "/api/cases/{id}/notes"),
        ("/api/orders/42/lines/7", "/api/orders/{id}/lines/{id2}"),
        ("/api/items/3f2b8c1e-9d4a-4b7e-8c2f-1a2b3c4d5e6f", "/api/items/{id}"),
        ("/api/v2/cases/search", "/api/v2/cases/search"),
        ("/assets/index-HYuJWd3h.js", "/assets/index-HYuJWd3h.js"),
        ("/cases", "/cases"),
    ],
)
def test_ids_numbers_and_uuids_in_paths_become_parameters(path, pattern):
    assert sanitize.route_pattern(path) == pattern


# --- what a recording shows ----------------------------------------------------------------------


def test_screens_come_from_the_recorded_addresses_with_their_dom(recorded):
    paths = {s.path: s for s in recorded.screens}
    assert set(paths) == {"/login", "/cases", "/cases/{id}"}
    detail = paths["/cases/{id}"]
    assert detail.name == "cases-item" and detail.basis == "observed" and detail.snapshots > 0
    assert {"category", "subcategory", "caseStatus", "btnSave", "message"} <= set(detail.ids)
    status = next(e for e in detail.elements if e.selector == "select#caseStatus")
    assert status.label == "Status" and status.actions == ["select"]
    assert status.options == ["OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED"]
    assert {"status", "case-link"} <= set(paths["/cases"].classes)
    assert next(e for e in paths["/login"].elements if e.selector == "button#btnLogin").label == (
        "Sign in"
    )
    assert any(
        e.selector == "a.case-link" and "click" in e.actions for e in paths["/cases"].elements
    )


def test_routes_have_shapes_statuses_timings_and_the_screen_that_called_them(recorded):
    put = recorded.route("PUT /api/cases/{id}")
    assert put.basis == "observed"
    assert put.body == ["status: string", "category_id: integer", "subcategory_id: integer",
                        "escalation_reason: null|string"]  # fmt: skip
    assert put.responses == {"422": {"detail": {"code": "string", "message": "string"}}}
    assert put.timings.count == 2 and put.timings.max_ms >= put.timings.p50_ms
    search = recorded.route("GET /api/cases/search")
    assert search.query == ["caseNo", "customer", "status"]
    assert set(search.responses["200"]["items"]["[]"]) >= {"case_no", "status", "priority"}
    case = recorded.route("GET /api/cases/{id}")
    assert case.responses["200"]["customer"]["email"] == "string(email)"
    calls = {s.path: [(c.method, c.path) for c in s.calls] for s in recorded.screens}
    assert calls["/login"] == [("POST", "/api/login")]
    assert ("PUT", "/api/cases/{id}") in calls["/cases/{id}"]
    assert ("GET", "/api/categories/{id}/subcategories") in calls["/cases/{id}"]


def test_error_responses_become_observed_rules_with_code_and_message(recorded):
    texts = {r.text: r for r in recorded.rules}
    note = texts["422 NOTE_REQUIRED: Add a note before resolving the case"]
    assert note.basis == "observed" and note.where == ["PUT /api/cases/{id}"]
    assert note.status == 422 and note.code == "NOTE_REQUIRED"
    assert (
        "422 ESCALATION_REASON_REQUIRED: Escalation needs a reason of at least 10 characters"
        in (texts)
    )
    assert "401 INVALID_CREDENTIALS: Invalid user ID or password" in texts


def test_framework_tables_codes_and_evidence(recorded):
    assert recorded.stack.frontend == {
        "framework": "react",
        "version": "18.3.1",
        "rendering": "client",
        "marker": "react-dom bundle, rendererPackageName",
    }
    tables = {t.name: t for t in recorded.tables}
    assert {"cases", "customer", "categories", "subcategories"} <= set(tables)
    assert {t.basis for t in tables.values()} == {"inferred"}
    cases = {c.name: c.type for c in tables["cases"].columns}
    assert cases["id"] == "INTEGER" and cases["opened_at"] == "TIMESTAMP"
    assert "login" not in tables  # a POST's reply is not a resource
    assert recorded.route("GET /api/cases/{id}").tables == ["cases", "customer"]
    assert recorded.codes["cases.status"] == ["IN_PROGRESS"]
    assert "PAYMENTS" in recorded.codes["categories.code"]
    assert not any(k.endswith(("full_name", "email", "city")) for k in recorded.codes)
    (ev,) = recorded.evidence
    assert ev.id == "har:showcase-rec" and ev.kind == "har" and ev.complete == []
    assert ev.ref.startswith("sha256:")


def test_a_bare_har_of_a_single_page_app_gives_addresses_only(tmp_path):
    bare = tmp_path / "devtools-export.har"
    shutil.copy(FIXTURE / "recording.har", bare)
    model = mb.from_recording("showcase", bare, ROOT, tmp_path / "pages")
    assert [(s.path, s.snapshots, s.elements, s.ids) for s in model.screens] == [
        ("/login", 0, [], [])
    ]
    assert any(
        "known only by address" in p and "single-page-app shells" in p for p in model.problems
    )
    assert model.route("PUT /api/cases/{id}").basis == "observed"  # the API is still all there
    assert not (tmp_path / "pages").exists()
    built = brief_mod.scope("showcase", "x", mb.merge("showcase", [mb.from_automations(
        "showcase", [_sample()]), model]))  # fmt: skip
    login = next(s for s in built.screens if s.screen.path == "/login")
    assert {e.status for e in login.elements} == {"unchecked"}
    assert login.elements[0].detail == "recorded by address only; no page content"


def test_page_snapshots_are_written_value_free(tmp_path):
    pages = tmp_path / "apps" / "showcase" / "pages"
    model = mb.from_recording("showcase", FIXTURE, tmp_path, pages)
    assert sorted(p.name for p in pages.iterdir()) == [
        "cases-item.html",
        "cases.html",
        "login.html",
    ]
    html = (pages / "cases-item.html").read_text(encoding="utf-8")
    assert '<select id="caseStatus" name="caseStatus">' in html and "<script" not in html
    assert ">Save</button>" in html and '<label for="category">Category</label>' in html
    assert 'id="fCaseNo">X-9999<' in html  # a value inside a heading is still a value
    case = next(s for s in model.screens if s.path == "/cases/{id}")
    assert case.files == ["apps/showcase/pages/cases-item.html"]


# --- server-rendered pages and framework markers -------------------------------------------------


def _entry(method, url, status=200, mime="text/html", text="", headers=(), post=None, t=0, ms=12):
    entry = {
        "startedDateTime": f"2026-11-15T10:00:{t:02d}.000Z",
        "time": ms,
        "request": {"method": method, "url": url, "headers": list(headers)},
        "response": {"status": status, "headers": [], "content": {"mimeType": mime, "text": text}},
    }
    if post is not None:
        entry["request"]["postData"] = post
    return entry


def _har(tmp_path: Path, entries: list[dict], name: str = "legacy.har") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"log": {"version": "1.2", "entries": entries}}), encoding="utf-8")
    return path


WEBFORMS = """<!DOCTYPE html><html><head><title>Account search</title></head><body>
<form method="post" action="/Accounts/Search.aspx" id="form1">
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="dDwtMTA4MzE0MjEwNTs7Pg==" />
<h1>Find an account</h1>
<label for="txtAccount">Account number</label>
<input name="txtAccount" type="text" id="txtAccount" value="GB29NWBK60161331926819" />
<select name="ddlBranch" id="ddlBranch"><option value="12">North</option><option value="13">South</option></select>
<input type="submit" name="btnFind" value="Find" id="btnFind" />
<table id="gvResults"><tr><th>Holder</th><th>Balance</th></tr>
<tr><td class="holder">Ottilie Vanderbrook</td><td>1,234.56</td></tr></table>
<!-- served by node 7 for Ottilie -->
<a href="/Accounts/Detail.aspx?acct=GB29NWBK60161331926819" onclick="track('Ottilie')">Open</a>
<script>var token = "eyJhbGciOiJIUzI1NiJ9.secret";</script>
</form></body></html>"""


def test_server_rendered_pages_give_screens_elements_and_form_routes(tmp_path):
    path = _har(tmp_path, [
        _entry("GET", "https://legacy.example.test/Accounts/Search.aspx", text=WEBFORMS),
        _entry("POST", "https://legacy.example.test/Accounts/Search.aspx", text=WEBFORMS, t=5,
               post={"mimeType": "application/x-www-form-urlencoded",
                     "params": [{"name": "__VIEWSTATE", "value": "dDw"},
                                {"name": "txtAccount", "value": "GB29NWBK60161331926819"},
                                {"name": "btnFind", "value": "Find"}]}),
    ])  # fmt: skip
    model = mb.from_recording("legacy", path, tmp_path, tmp_path / "pages")
    assert model.stack.frontend["framework"] == "ASP.NET Web Forms"
    assert model.stack.frontend["marker"] == "__VIEWSTATE"
    (screen,) = model.screens
    assert screen.path == "/Accounts/Search.aspx" and screen.snapshots == 2
    els = {e.selector: e for e in screen.elements}
    assert els["input#txtAccount"].label == "Account number"
    assert els["input#btnFind"].label == "Find"
    assert els["select#ddlBranch"].options == ["North", "South"]
    post = model.route("POST /Accounts/Search.aspx")
    assert post.body == ["__VIEWSTATE: string", "txtAccount: string", "btnFind: string"]
    assert model.route("GET /Accounts/Search.aspx").responses == {"200": {"(content)": "text/html"}}


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<html><body><app-root ng-version="14.2.0"><input id="q"></app-root></body></html>',
         {"framework": "angular", "version": "14.2.0", "marker": 'ng-version="14.2.0"'}),
        ('<html><body><div id="app" data-v-app=""><input id="q"></div></body></html>',
         {"framework": "vue", "marker": "data-v- attribute"}),
        ('<html><body><form><input name="javax.faces.ViewState" id="j_id1:javax.faces.ViewState:0" '
         'type="hidden" value="x"><input id="q"></form></body></html>',
         {"framework": "JSF", "marker": "javax.faces.ViewState"}),
        ("<html><body><form><label for='q'>Find</label><input id='q'></form></body></html>",
         {"framework": "server-rendered"}),
    ],
)  # fmt: skip
def test_framework_markers(tmp_path, html, expected):
    model = mb.from_recording("x", _har(tmp_path, [_entry("GET", "http://a.test/p", text=html)]),
                              tmp_path, None)  # fmt: skip
    front = model.stack.frontend
    assert {k: front[k] for k in expected} == expected


# --- privacy ---------------------------------------------------------------------------------------


SECRETS = {
    "customer name": "Ottilie Vanderbrook",
    "account number": "GB29NWBK60161331926819",
    "token": "eyJhbGciOiJIUzI1NiJ9.secret",
    "cookie": "sessionid=7f3a9c1d2e",
    "bearer": "Bearer abc123token",
    "csrf": "csrf-55aa66bb",
    "viewstate": "dDwtMTA4MzE0MjEwNTs7Pg==",
    "balance": "1,234.56",
}


def _private_har(tmp_path: Path) -> Path:
    creds = [{"name": "Cookie", "value": SECRETS["cookie"]},
             {"name": "Authorization", "value": SECRETS["bearer"]},
             {"name": "X-CSRF-Token", "value": SECRETS["csrf"]},
             {"name": "Referer", "value": "https://legacy.example.test/Accounts/Search.aspx"}]  # fmt: skip
    account = {"holder": SECRETS["customer name"], "iban": SECRETS["account number"],
               "status": "ACTIVE", "balance": 1234.56,
               "links": {SECRETS["account number"]: {"href": "/x"}}}  # fmt: skip
    entry = _entry("GET", f"https://legacy.example.test/api/accounts/{SECRETS['account number']}"
                   f"?holder={SECRETS['customer name']}", mime="application/json",
                   text=json.dumps(account), headers=creds, t=3)  # fmt: skip
    entry["response"]["headers"] = [{"name": "Set-Cookie", "value": SECRETS["cookie"]}]
    error = _entry("POST", "https://legacy.example.test/api/accounts/block", status=409,
                   mime="application/json", headers=creds, t=4,
                   text=json.dumps({"code": "BLOCKED", "message":
                                    f"Account {SECRETS['account number']} of Ms Vanderbrook is blocked"}),
                   post={"mimeType": "application/json",
                         "text": json.dumps({"iban": SECRETS["account number"], "token": SECRETS["token"]})})  # fmt: skip
    return _har(tmp_path, [
        _entry("GET", "https://legacy.example.test/Accounts/Search.aspx", text=WEBFORMS, headers=creds),
        entry, error,
    ], name="private.har")  # fmt: skip


def _fixture_values() -> dict[str, str]:
    """Values the showcase recording holds: sign-in, token, customer and case details."""
    entries = json.loads((FIXTURE / "recording.har").read_text(encoding="utf-8"))["log"]["entries"]
    values = {"password": "workbench-demo"}
    for e in entries:
        text = e["response"]["content"].get("text") or ""
        if e["request"]["url"].endswith("/api/login") and e["response"]["status"] == 200:
            login = json.loads(text)
            values |= {"token": login["token"], "display name": login["display_name"]}
        if e["request"]["url"].endswith("/api/cases/C-1001") and e["request"]["method"] == "GET":
            case = json.loads(text)
            cust = case["customer"]
            values |= {"case no": case["case_no"], "case title": case["title"],
                       "customer name": cust["full_name"], "customer no": cust["cust_no"],
                       "email": cust["email"], "phone": cust["phone"]}  # fmt: skip
    assert len(values) == 9, values
    return values


def test_no_recorded_value_reaches_the_model_the_brief_or_the_page_files(tmp_path):
    pages = tmp_path / "apps" / "x" / "pages"
    legacy = _auto([{"app": "showcase", "via": "web", "screens": [
        {"name": "search", "path": "/Accounts/Search.aspx",
         "uses": [{"action": "type", "selector": "#txtAccount"}]}]}], "legacy-bot")  # fmt: skip
    built = brief_mod.build(
        "showcase", "Mixed", automations=[_sample(), legacy], repo_root=tmp_path,
        recordings=[FIXTURE, _private_har(tmp_path)], pages_dir=pages,
    )  # fmt: skip
    outputs = {
        "model.yaml": _dump(built.model),
        "BRIEF.md": brief_mod.render(built, tmp_path),
        "app.yaml": yaml.safe_dump(brief_mod.app_yaml(built, tmp_path)),
        "schema.sql": brief_mod.schema(built, tmp_path),
    }
    outputs |= {f"pages/{p.name}": p.read_text(encoding="utf-8") for p in pages.iterdir()}
    assert len(outputs) == 4 + 4  # three showcase screens and the legacy one
    leaks = [
        f"{what} ({value!r}) in {name}"
        for what, value in (SECRETS | _fixture_values()).items()
        for name, text in outputs.items()
        if value in text
    ]
    assert leaks == []
    for word in ("Vanderbrook", "sessionid", "abc123token", "Ottilie"):
        assert not any(word in text for text in outputs.values()), word
    # The application's own words survive, and so do the shapes.
    assert "Account number" in outputs["pages/Accounts-Search.aspx.html"]
    assert (
        "409 BLOCKED: Account XX99XXXX99999999999999 of Ms Vanderbrook" not in outputs["model.yaml"]
    )
    assert (
        "409 BLOCKED: Account XX99XXXX99999999999999 of Ms Xxxxxxxxxxx is blocked"
        in outputs["BRIEF.md"]
    )


def test_credential_headers_are_dropped_when_read():
    headers = [{"name": n, "value": "v"} for n in (
        "Cookie", "Set-Cookie", "Authorization", "X-CSRF-Token", "X-Api-Key", "X-Auth-Token",
        "X-Amz-Security-Token", "Proxy-Authorization", "X-Session-Id", "Referer", "Accept")]  # fmt: skip
    assert set(sanitize.safe_headers(headers)) == {"referer", "accept"}
    assert sanitize.mask("Smith 1001, Flat 2b") == "Xxxxx 9999, Xxxx 9x"


# --- E-02 Definition of Done ------------------------------------------------------------------------


def _compare(source: brief_mod.Brief, recorded: brief_mod.Brief) -> list[tuple[str, str, str, str]]:
    """Every screen, selector and route the source-based brief finds on the recorded paths."""
    seen = [s.path for s in recorded.model.screens if any(f.startswith("har:") for f in s.from_)]
    rows = []
    on_path = []
    for sc in source.screens:
        if not any(same_shape(sc.screen.path, p) for p in seen):
            rows.append(("screen", sc.screen.path, "in scope", "not recorded (excluded)"))
            continue
        on_path.append(sc.screen.path)
        mine = next(
            (r for r in recorded.screens if same_shape(r.screen.path, sc.screen.path)), None
        )
        rows.append(("screen", sc.screen.path, "in scope",
                     f"in scope as {mine.screen.path} ({mine.mode})" if mine else "MISSING"))  # fmt: skip
        for el in sc.elements:
            got = (
                next((e for e in mine.elements if e.selector == el.selector), None)
                if mine
                else None
            )
            rows.append(("selector", f"{sc.screen.path} {el.selector}", el.status,
                         f"{got.status} ({got.basis})" if got else "MISSING"))  # fmt: skip
    for key, reasons in source.routes.items():
        method, path = key.split(" ", 1)
        needed = any("directly" in r for r in reasons) or any(
            r.startswith("called by screen ") and r.split()[3] in on_path for r in reasons
        )
        if not needed:
            rows.append(("route", key, "; ".join(reasons), "not on a recorded path (excluded)"))
            continue
        got = next((k for k in recorded.routes
                    if k.split(" ", 1)[0] == method and same_shape(k.split(" ", 1)[1], path)), None)  # fmt: skip
        basis = recorded.route(got).basis if got else ""
        rows.append(("route", key, "in scope", f"{got} ({basis})" if got else "MISSING"))
    return rows


def test_e02_recording_and_automation_find_what_the_source_finds_on_the_recorded_paths(capsys):
    source = brief_mod.build("showcase", "Case Workbench", automations=[_sample()], source=REF,
                             repo_root=ROOT)  # fmt: skip
    recorded = brief_mod.build("showcase", "Case Workbench", automations=[_sample()],
                               recordings=[FIXTURE], repo_root=ROOT)  # fmt: skip
    rows = _compare(source, recorded)
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    with capsys.disabled():
        print("\n\nE-02: source brief vs recording + automation brief (showcase)\n")
        head = ("kind", "item", "source brief", "recording + automation brief")
        print(" | ".join(h.ljust(w) for h, w in zip(head, widths, strict=True)))
        print("-+-".join("-" * w for w in widths))
        for row in rows:
            print(" | ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)))
        print()
    missing = [
        r for r in rows if "MISSING" in r[3] or (r[0] == "selector" and not r[3].startswith(r[2]))
    ]
    assert missing == []
    counted = [r for r in rows if "excluded" not in r[3]]
    assert {r[0] for r in counted} == {"screen", "selector", "route"}
    assert len([r for r in counted if r[0] == "screen"]) == 3
    assert len([r for r in counted if r[0] == "selector"]) == 12
    assert [r[1] for r in rows if "excluded" in r[3]] == ["GET /api/health"]
    assert recorded.mode == "reconstruction" and source.mode == "copy"
    assert {r.code for r in recorded.model.rules} >= {"NOTE_REQUIRED", "ESCALATION_REASON_REQUIRED"}
    App.model_validate(brief_mod.app_yaml(recorded, ROOT) | {"run": {}})


# --- live recording ----------------------------------------------------------------------------------


def _up(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _up(8720), reason="showcase demo copy not running on 8720")
def test_live_recording_of_the_showcase_gives_the_same_screens_and_routes(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from recording_flow import BASE, showcase_flow

    from labctl.recorder import record

    out = record(f"{BASE}/login", tmp_path / "live", script=showcase_flow, headless=True)
    steps = [json.loads(line) for line in (out / "steps.jsonl").read_text().splitlines()]
    assert {s["action"] for s in steps} >= {"goto", "type", "click", "select", "submit"}
    base = {"seq", "ts", "url", "action", "selector", "dom"}
    extra = {"dom_before", "screenshot", "typed_shape"}  # never "value": typed text is not kept
    assert all(base <= set(s) <= base | extra for s in steps)
    assert not any(
        "workbench-demo" in p.read_text(encoding="utf-8") for p in (out / "dom").iterdir()
    )
    live = mb.from_recording("showcase", out, ROOT, None)
    fixed = mb.from_recording("showcase", FIXTURE, ROOT, None)
    assert {s.path for s in live.screens} == {s.path for s in fixed.screens}
    assert {r.key for r in live.routes} == {r.key for r in fixed.routes}
    assert har.load(out).id == "live"


def test_names_an_api_returned_are_masked_on_every_page(tmp_path):
    """Review finding 1: API answers keep no text, so their values were not known as data, and a
    single title-case name in a heading, option or button survived into pages/ and the model."""
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    answer = {"items": [{"id": 1, "name": "Okonkwo"}, {"id": 2, "name": "Vandermeer"}]}
    entries = [{
        "startedDateTime": "2026-01-01T00:00:00Z", "time": 5, "_resourceType": "fetch",
        "request": {"method": "GET", "url": "https://app.example/api/customers", "headers": []},
        "response": {"status": 200, "content": {"mimeType": "application/json",
                                                "text": json.dumps(answer)}},
    }]  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    (rec / "dom" / "0001.html").write_text(
        "<html><body><h2>Okonkwo</h2><select id='cust'><option>Vandermeer</option></select>"
        "<button>Okonkwo</button><form><input id='a'></form></body></html>",
        encoding="utf-8",
    )
    step = {"seq": 1, "ts": "2026-01-01T00:00:01Z", "url": "https://app.example/customers",
            "action": "load", "dom": "dom/0001.html"}  # fmt: skip
    (rec / "steps.jsonl").write_text(json.dumps(step) + "\n", encoding="utf-8")
    pages = tmp_path / "pages"
    model = har.analyse("demo", rec, tmp_path, pages)
    written = "".join(p.read_text(encoding="utf-8") for p in pages.glob("*.html"))
    dumped = model.model_dump_json()
    for name in ("Okonkwo", "Vandermeer"):
        assert name not in written and name not in dumped, name
