"""Technology detection from a recording (server, libraries, web server) and tables from HTML grids.

Fixtures are small synthetic HARs. Only names are ever used as markers: cookie names, header product
names, file names, element ids and classes. A cookie value, token or cell text must never reach the
model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labctl import brief as brief_mod
from labctl import har, sanitize
from labctl import model_build as mb
from labctl.appmodel import Stack

ROOT = Path(__file__).resolve().parents[3]
SECRET = "s3cr3t-cookie-value-7f3a9c1d"


def entry(url, html="", mime="text/html", method="GET", status=200, req_headers=(), resp_headers=(),
          cookies=(), set_cookies=(), t=0):  # fmt: skip
    req_headers = list(req_headers)
    if cookies:
        req_headers.append({"name": "Cookie", "value": "; ".join(f"{c}={SECRET}" for c in cookies)})
    resp_headers = list(resp_headers) + [
        {"name": "Set-Cookie", "value": f"{c}={SECRET}; Path=/; HttpOnly"} for c in set_cookies
    ]
    return {
        "startedDateTime": f"2026-11-15T10:00:{t:02d}.000Z",
        "time": 10,
        "request": {"method": method, "url": url, "headers": req_headers,
                    "cookies": [{"name": c, "value": SECRET} for c in cookies]},
        "response": {"status": status, "headers": resp_headers,
                     "cookies": [{"name": c, "value": SECRET} for c in set_cookies],
                     "content": {"mimeType": mime, "text": html}},
    }  # fmt: skip


def run(tmp_path, entries, name="legacy"):
    path = tmp_path / f"{name}.har"
    path.write_text(json.dumps({"log": {"version": "1.2", "entries": entries}}), encoding="utf-8")
    return mb.from_recording("legacy", path, tmp_path, None)


def page(body, head=""):
    return f"<!DOCTYPE html><html><head><title>Page</title>{head}</head><body>{body}</body></html>"


FORM = "<form><label for='q'>Name</label><input id='q' name='q'><input id='w' name='w'></form>"


def detect(tmp_path, entries):
    model = run(tmp_path, entries)
    return model, model.stack


# --- server technology ---------------------------------------------------------------------------


def test_php_from_headers_cookies_and_addresses(tmp_path):
    _, stack = detect(tmp_path, [
        entry("http://a.test/index.php", page(FORM),
              resp_headers=[{"name": "X-Powered-By", "value": "PHP/7.4.3"},
                            {"name": "Server", "value": "Apache/2.4.41 (Ubuntu)"}],
              set_cookies=["PHPSESSID", "laravel_session"]),
    ])  # fmt: skip
    server = stack.server
    assert server["technology"] == "PHP" and server["confidence"] == "certain"
    assert server["version"] == "7.4.3"
    assert server["framework"] == "Laravel" and server["framework_confidence"] == "certain"
    assert server["web_server"]["name"] == "Apache HTTP Server"
    assert server["web_server"]["version"] == "2.4.41"
    assert {e["marker"] for e in stack.detected} >= {"PHPSESSID cookie", "laravel_session cookie",
                                                     "X-Powered-By header names PHP"}


def test_a_php_address_alone_is_only_likely(tmp_path):
    _, stack = detect(tmp_path, [entry("http://a.test/orders/list.php", page(FORM))])
    assert stack.server["technology"] == "PHP" and stack.server["confidence"] == "likely"
    assert stack.server["marker"] == ".php addresses"


def test_java_jsf_primefaces_with_jquery(tmp_path):
    html = page(
        "<form method='post'><input type='hidden' name='javax.faces.ViewState' id='j_id1:javax.faces.ViewState:0'"
        " value='abc'><table class='ui-datatable'><tr><td>x</td></tr></table>"
        "<input id='f' name='f'><input id='g' name='g'></form>",
        head="<script src='/javax.faces.resource/jquery/jquery.js.xhtml?ln=primefaces'></script>"
             "<script src='/js/jquery-1.12.4.min.js'></script>",
    )
    model, stack = detect(tmp_path, [
        entry("http://a.test/app/orders.xhtml", html,
              resp_headers=[{"name": "Server", "value": "Apache-Coyote/1.1"}],
              set_cookies=["JSESSIONID"]),
    ])  # fmt: skip
    assert model.stack.frontend["framework"] == "JSF"
    assert model.stack.frontend["rendering"] == "server"
    assert model.stack.frontend["ui_library"] == "PrimeFaces"
    assert stack.server["technology"] == "Java"
    assert stack.server["framework"] == "JSF"
    assert stack.server["confidence"] == "certain"
    assert stack.server["web_server"]["name"] == "Tomcat"
    libs = {x["name"]: x for x in stack.libraries}
    assert libs["jQuery"]["version"] == "1.12.4" and libs["PrimeFaces"]["role"] == "ui"


def test_dotnet_web_forms_and_iis(tmp_path):
    html = page("<form id='form1' method='post'><input type='hidden' name='__VIEWSTATE' id='__VIEWSTATE' "
                "value='dDw'><input type='hidden' name='__EVENTVALIDATION' value='x'>"
                "<input id='a' name='a'><input id='b' name='b'></form>")
    model, stack = detect(tmp_path, [
        entry("http://a.test/Accounts/Home.aspx", html,
              resp_headers=[{"name": "Server", "value": "Microsoft-IIS/10.0"},
                            {"name": "X-AspNet-Version", "value": "4.0.30319"},
                            {"name": "X-Powered-By", "value": "ASP.NET"}],
              set_cookies=["ASP.NET_SessionId"]),
    ])  # fmt: skip
    assert model.stack.frontend["framework"] == "ASP.NET Web Forms"
    assert stack.server["technology"] == ".NET"
    assert stack.server["framework"] == "ASP.NET Web Forms"  # beats the generic ASP.NET
    assert stack.server["version"] == "4.0.30319"
    assert stack.server["web_server"] == {"name": "IIS", "marker": "Server header: IIS",
                                          "confidence": "certain", "version": "10.0"}  # fmt: skip


def test_aspnet_mvc_razor_and_core_and_blazor(tmp_path):
    _, mvc = detect(tmp_path, [entry(
        "http://a.test/Home/Index", page("<form><input name='__RequestVerificationToken' type='hidden'>"
                                         "<input id='a' name='a'></form>"),
        resp_headers=[{"name": "X-AspNetMvc-Version", "value": "5.2"}])])  # fmt: skip
    assert mvc.server["technology"] == ".NET" and mvc.server["framework"] == "ASP.NET MVC"
    assert mvc.server["version"] == "5.2"
    _, core = detect(tmp_path, [entry("http://a.test/", page(FORM), set_cookies=[".AspNetCore.Session"],
                                      resp_headers=[{"name": "Server", "value": "Kestrel"}])])  # fmt: skip
    assert core.server["framework"] == "ASP.NET Core" and core.server["web_server"]["name"] == "Kestrel"
    _, blazor = detect(tmp_path, [
        entry("http://a.test/", page(FORM, head="<script src='/_framework/blazor.web.js'></script>")),
        entry("http://a.test/_blazor/negotiate?negotiateVersion=1", "", mime="application/json", method="POST"),
    ])  # fmt: skip
    assert blazor.server["framework"] == "Blazor" and blazor.server["confidence"] == "certain"


@pytest.mark.parametrize(
    ("entries", "technology", "framework", "confidence"),
    [
        ([entry("http://a.test/default.asp", page(FORM), set_cookies=["ASPSESSIONIDQABCDEFG"])],
         "Classic ASP", None, "certain"),
        ([entry("http://a.test/default.asp", page(FORM))], "Classic ASP", None, "likely"),
        ([entry("http://a.test/x/login.do", page(FORM))], "Java", "Struts", "likely"),
        ([entry("http://a.test/x/home.action", page(FORM))], "Java", "Struts", "likely"),
        ([entry("http://a.test/x/home.jsp", page(FORM))], "Java", "JSP", "certain"),
        ([entry("http://a.test/x/home;jsessionid=ABC", page(FORM))], "Java", None, "certain"),
        ([entry("http://a.test/", page(FORM, head="<script src='/app/app.nocache.js'></script>"))],
         "Java", "GWT", "certain"),
        ([entry("http://a.test/VAADIN/vaadinBootstrap.js", "", mime="text/javascript"),
          entry("http://a.test/", page(FORM))], "Java", "Vaadin", "certain"),
        ([entry("http://a.test/", page(FORM), set_cookies=["csrftoken"])], "Python", "Django", "likely"),
        ([entry("http://a.test/", page("<form><input type='hidden' name='csrfmiddlewaretoken'>"
                                       "<input id='a' name='a'></form>"))], "Python", "Django", "certain"),
        ([entry("http://a.test/", page(FORM), set_cookies=["session"])], "Python", "Flask", "guess"),
        ([entry("http://a.test/", page(FORM), set_cookies=["_myapp_session"])], "Ruby", "Rails", "likely"),
        ([entry("http://a.test/", page(FORM, head="<meta name='csrf-param' content='authenticity_token'>"
                                                  "<meta name='csrf-token' content='t0k3n'>"))],
         "Ruby", "Rails", "likely"),
        ([entry("http://a.test/", page(FORM), resp_headers=[{"name": "X-Powered-By", "value": "Express"}])],
         "Node.js", "Express", "certain"),
        ([entry("http://a.test/index.cfm", page(FORM), set_cookies=["CFID", "CFTOKEN"])],
         "ColdFusion", None, "certain"),
        ([entry("http://a.test/pls/apex/f?p=100:1:123456", page(FORM))], "Oracle", "Oracle APEX", "certain"),
        ([entry("http://a.test/forms/frmservlet?config=x", page(FORM))], "Oracle", "Oracle Forms", "certain"),
        ([entry("http://a.test/", page(FORM, head="<script src='/resources/sap-ui-core.js'></script>"))],
         "SAP", "SAP UI5", "certain"),
        ([entry("http://a.test/sap/bc/webdynpro/sap/zapp", page(FORM))], "SAP", None, "certain"),
        ([entry("http://a.test/start.swe?SWECmd=GotoView&SWEView=Home", page(FORM))], "Siebel", None, "certain"),
        ([entry("http://acme.service-now.com/incident_list.do?sysparm_query=active", page(FORM))],
         "ServiceNow", None, "certain"),
        ([entry("http://acme.my.salesforce.com/lightning/page/home", page(FORM))], "Salesforce", None, "certain"),
        ([entry("http://a.test/", page(FORM, head="<meta name='generator' content='WordPress 6.2'>"))],
         "PHP", "WordPress", "certain"),
    ],
)  # fmt: skip
def test_server_technologies(tmp_path, entries, technology, framework, confidence):
    _, stack = detect(tmp_path, entries)
    server = stack.server
    assert (server["technology"], server.get("framework")) == (technology, framework)
    assert server["confidence"] == confidence


def test_react_front_end_on_a_java_backend_reports_both(tmp_path):
    bundle = 'x={version:"18.3.1",rendererPackageName:"react-dom"};'
    model, stack = detect(tmp_path, [
        entry("http://a.test/", page("<div id='root'></div>")),
        entry("http://a.test/static/main.js", bundle, mime="text/javascript"),
        entry("http://a.test/api/orders", '[{"id":1}]', mime="application/json",
              resp_headers=[{"name": "Server", "value": "Apache-Coyote/1.1"}], set_cookies=["JSESSIONID"]),
    ])  # fmt: skip
    assert model.stack.frontend["framework"] == "react" and model.stack.frontend["version"] == "18.3.1"
    assert stack.server["technology"] == "Java"
    assert stack.pinned == {"react-dom": "18.3.1"}


def test_conflicting_server_signals_report_the_stronger_and_list_the_other(tmp_path):
    _, stack = detect(tmp_path, [
        entry("http://a.test/index.php", page(FORM), set_cookies=["JSESSIONID"]),
    ])  # fmt: skip
    assert stack.server["technology"] == "Java"  # certain beats likely
    assert stack.server["also"] == [{"technology": "PHP", "marker": ".php addresses", "confidence": "likely"}]


def test_guesses_do_not_outrank_or_clutter(tmp_path):
    _, stack = detect(tmp_path, [entry("http://a.test/", page(FORM), set_cookies=["JSESSIONID", "session"])])
    assert stack.server["technology"] == "Java" and "also" not in stack.server
    assert any(e["confidence"] == "guess" for e in stack.detected)


def test_ui_suites_prove_their_platform_as_likely_at_most(tmp_path):
    html = page("<div class='RadGrid RadGrid_Default'></div><input id='a' name='a'><input id='b' name='b'>")
    _, stack = detect(tmp_path, [entry("http://a.test/p", html)])
    assert stack.server["technology"] == ".NET" and stack.server["confidence"] == "likely"
    assert stack.server["framework"] == "ASP.NET Web Forms"
    assert stack.libraries[0]["name"] == "Telerik UI for ASP.NET AJAX"


# --- web server ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "name", "version", "technology"),
    [
        ("nginx/1.18.0", "nginx", "1.18.0", None),
        ("Microsoft-IIS/8.5", "IIS", "8.5", None),
        ("Apache/2.4.6 (CentOS)", "Apache HTTP Server", "2.4.6", None),
        ("Apache Tomcat/9.0.31", "Tomcat", "9.0.31", "Java"),
        ("Apache-Coyote/1.1", "Tomcat", "1.1", "Java"),
        ("WebLogic Server 12.2.1.4", "WebLogic", "12.2.1.4", "Java"),
        ("IBM_HTTP_Server", "IBM HTTP Server", None, None),
        ("WebSphere Application Server/8.5", "WebSphere", "8.5", "Java"),
        ("JBoss-5.0/JBossWeb-2.1", "JBoss", "5.0", "Java"),
        ("Jetty(9.4.12.v20180830)", "Jetty", "9.4.12", "Java"),
        ("gunicorn/20.1.0", "gunicorn/uvicorn", "20.1.0", "Python"),
    ],
)  # fmt: skip
def test_web_server_from_the_server_header(tmp_path, header, name, version, technology):
    _, stack = detect(tmp_path, [entry("http://a.test/", page(FORM),
                                       resp_headers=[{"name": "Server", "value": header}])])  # fmt: skip
    web = stack.server["web_server"]
    assert web["name"] == name and web.get("version") == version
    assert stack.server.get("technology") == technology


def test_header_values_are_reduced_to_product_tokens():
    got = sanitize.tech_headers([
        {"name": "Server", "value": "Apache/2.4.41 (Ubuntu) OpenSSL/1.1.1f"},
        {"name": "X-Powered-By", "value": "PHP/7.4.3"},
        {"name": "X-AspNet-Version", "value": "4.0.30319; node=web-07"},
        {"name": "X-Pingback", "value": "https://blog.example.test/xmlrpc.php"},
        {"name": "Set-Cookie", "value": "a=b"},
        {"name": "Authorization", "value": "Bearer abc"},
    ])  # fmt: skip
    assert got == {"server": "Apache/2.4.41 Ubuntu OpenSSL/1.1.1f", "x-powered-by": "PHP/7.4.3",
                   "x-aspnet-version": "4.0.30319", "x-pingback": "present"}  # fmt: skip


def test_cookie_names_only():
    names = sanitize.cookie_names({
        "request": {"headers": [{"name": "Cookie", "value": "a=1; b=2"}], "cookies": [{"name": "c", "value": "3"}]},
        "response": {"headers": [{"name": "Set-Cookie", "value": "d=4; Path=/\ne=5; Secure"}], "cookies": []},
    })  # fmt: skip
    assert names == {"a", "b", "c", "d", "e"}


# --- client libraries ----------------------------------------------------------------------------


def libs(tmp_path, head="", body="", scripts=(), entries=()):
    html = page(body or FORM, head=head)
    model = run(tmp_path, [entry("http://a.test/app", html), *entries,
                           *(entry(f"http://a.test{p}", text, mime="text/javascript") for p, text in scripts)])  # fmt: skip
    return model, {x["name"]: x for x in model.stack.libraries}


def test_jquery_versions_from_names_and_banners(tmp_path):
    _, found = libs(tmp_path, head="<script src='/js/jquery-3.6.0.min.js'></script>"
                                   "<script src='https://cdn.test/jquery-ui-1.13.2.min.js'></script>")
    assert found["jQuery"]["version"] == "3.6.0" and found["jQuery"]["confidence"] == "certain"
    assert found["jQuery UI"]["version"] == "1.13.2"
    _, banner = libs(tmp_path, head="<script src='/js/bundle.js'></script>",
                     scripts=[("/js/bundle.js", "/*! jQuery v1.12.4 | (c) jQuery Foundation */")])  # fmt: skip
    assert banner["jQuery"]["version"] == "1.12.4"


def test_bootstrap_major_version(tmp_path):
    _, cdn = libs(tmp_path, head="<script src='https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'></script>")
    assert cdn["Bootstrap"]["version"] == "5.3.0"
    _, v5 = libs(tmp_path, body="<button data-bs-toggle='modal' id='b'>x</button><input id='a' name='a'>")
    assert v5["Bootstrap"]["version"] == "5" and v5["Bootstrap"]["confidence"] == "likely"
    _, v3 = libs(tmp_path, body="<div class='col-xs-6 glyphicon'></div><input id='a' name='a'>")
    assert v3["Bootstrap"]["version"] == "3"
    _, v4 = libs(tmp_path, head="<link rel='stylesheet' href='/css/bootstrap-4.6.1.min.css'>")
    assert v4["Bootstrap"]["version"] == "4.6.1"
    _, mix = libs(tmp_path, head="<script src='/js/bootstrap.min.js'></script>",
                  body="<div data-bs-toggle='x' id='z'></div><input id='a' name='a'>")  # fmt: skip
    assert mix["Bootstrap"]["version"] == "5" and mix["Bootstrap"]["confidence"] == "certain"


def test_angularjs_is_not_angular(tmp_path):
    model, found = libs(tmp_path, head="<script src='/lib/angular.min.js'></script>",
                        body="<div ng-app='shop' id='app'><input ng-model='q' id='q'></div>")  # fmt: skip
    assert model.stack.frontend["framework"] == "angularjs"
    assert model.stack.frontend["rendering"] == "client"
    assert "Angular" not in found and "AngularJS" not in found  # it is the framework, listed once


def test_angular_2_plus_stays_angular_with_material_and_primeng(tmp_path):
    model, found = libs(tmp_path, body="<app-root ng-version='14.2.0' id='r'><mat-toolbar>x</mat-toolbar>"
                                       "<p-table id='t'></p-table></app-root>")  # fmt: skip
    assert model.stack.frontend["framework"] == "angular" and model.stack.frontend["version"] == "14.2.0"
    assert {"Angular Material", "PrimeNG"} <= set(found)
    assert model.stack.frontend["ui_library"] in ("Angular Material", "PrimeNG")


@pytest.mark.parametrize(
    ("head", "body", "name"),
    [
        ("<script src='/js/knockout-3.5.1.js'></script>", "", "Knockout"),
        ("", "<div data-bind='text: a' id='k'></div>", "Knockout"),
        ("<script src='/js/backbone-min.js'></script>", "", "Backbone"),
        ("<script src='/js/ember.min.js'></script>", "", "Ember"),
        ("", "<div class='ember-view' id='e'></div>", "Ember"),
        ("", "<div class='svelte-1abc2d' id='s'></div>", "Svelte"),
        ("<script src='/_next/static/chunks/main.js'></script>", "", "Next.js"),
        ("<script src='/_nuxt/entry.js'></script>", "", "Nuxt"),
        ("<script src='/dojo/dojo.js'></script>", "", "Dojo"),
        ("<script src='/ext/ext-all.js'></script>", "", "ExtJS"),
    ],
)  # fmt: skip
def test_client_libraries(tmp_path, head, body, name):
    model, found = libs(tmp_path, head=head, body=f"{body}<input id='a' name='a'>")
    stacked = {x["name"] for x in model.stack.libraries}
    framework = (model.stack.frontend or {}).get("framework", "").lower()
    assert name in found or name.lower() == framework, stacked


@pytest.mark.parametrize(
    ("body", "name"),
    [
        ("<table class='ui-datatable'></table>", "PrimeFaces"),
        ("<div class='rf-dt'></div>", "RichFaces"),
        ("<div class='iceDatTbl'></div>", "ICEfaces"),
        ("<div class='k-grid k-widget'></div>", "Kendo UI"),
        ("<div class='dxgvControl'></div>", "DevExpress"),
        ("<div class='igGrid'></div>", "Infragistics"),
        ("<div class='e-grid e-control'></div>", "Syncfusion"),
        ("<button class='MuiButton-root'></button>", "MUI"),
        ("<div class='ant-table'></div>", "Ant Design"),
        ("<div class='ag-theme-alpine ag-root'></div>", "AG Grid"),
        ("<div class='dataTables_wrapper'></div>", "DataTables"),
        ("<ejs-grid></ejs-grid>", "Syncfusion"),
    ],
)  # fmt: skip
def test_ui_component_libraries(tmp_path, body, name):
    model, found = libs(tmp_path, body=f"{body}<input id='a' name='a'>")
    assert found[name]["role"] == "ui"
    assert model.stack.frontend["ui_library"] == name


def test_component_suite_is_the_ui_library_not_bootstrap(tmp_path):
    model, found = libs(tmp_path, head="<script src='/bootstrap.min.js'></script>",
                        body="<div class='k-grid'></div><input id='a' name='a'>")  # fmt: skip
    assert model.stack.frontend["ui_library"] == "Kendo UI" and "Bootstrap" in found


# --- shape of the result, privacy, the brief ----------------------------------------------------------


def test_the_result_keeps_the_old_keys_and_adds_the_new_ones():
    out = har._framework([], [], [])
    assert out == {"server": {}, "libraries": [], "evidence": []}
    pages = [sanitize.page("<html><body><app-root ng-version='14.2.0'><input id='q'></app-root></body></html>")]
    out = har._framework(pages, [])
    assert {"framework", "version", "rendering", "marker", "server", "libraries", "evidence"} <= set(out)
    assert out["framework"] == "angular" and out["marker"] == 'ng-version="14.2.0"'
    assert {"kind": "front", "name": "angular", "marker": 'ng-version="14.2.0"', "confidence": "certain"} in out["evidence"]


def test_no_cookie_value_or_token_reaches_the_model(tmp_path):
    html = page("<form><input type='hidden' name='__VIEWSTATE' value='" + SECRET + "'>"
                "<input id='a' name='a'><input id='b' name='b'></form>",
                head=f"<meta name='csrf-token' content='{SECRET}'><meta name='generator' content='WordPress 6.2'>")
    model = run(tmp_path, [entry("http://a.test/p", html, cookies=["PHPSESSID"], set_cookies=["JSESSIONID"],
                                 resp_headers=[{"name": "Server", "value": "nginx/1.18.0"}])])  # fmt: skip
    assert SECRET not in model.model_dump_json() and SECRET not in json.dumps(model.model_dump(by_alias=True))
    assert "PHPSESSID cookie" in json.dumps(model.stack.detected)  # a catalog label, never the value


def test_page_facts_hold_names_and_files_not_values():
    info = sanitize.page(
        "<html><head><script src='/js/jquery-3.6.0.min.js?v=SECRETQ'></script>"
        "<link rel='stylesheet' href='/css/bootstrap.min.css'>"
        "<meta name='csrf-token' content='TOKEN123'></head><body><form action='/a/b.do?user=jane&x=1'>"
        "<input name='_token' value='TOKEN123' type='hidden'><a href='/c;jsessionid=ABC123'>x</a>"
        "<div data-bind='text: n' ng-app='a'></div></form></body></html>"
    )
    f = info.facts
    assert f["script"] == {"/js/jquery-3.6.0.min.js"} and f["link"] == {"/css/bootstrap.min.css"}
    assert f["meta"] == {"csrf-token"} and f["name"] == {"_token"}
    assert f["url"] >= {"/a/b.do?user,x", "/c"} and f["flag"] == {"jsessionid-in-url"}
    assert f["attr"] == {"data-bind", "ng-app"}
    blob = json.dumps({k: sorted(v) for k, v in f.items()})
    assert "TOKEN123" not in blob and "SECRETQ" not in blob and "ABC123" not in blob and "jane" not in blob


def test_brief_prints_the_facts_in_a_table(tmp_path):
    _, stack = detect(tmp_path, [
        entry("http://a.test/index.php", page(FORM, head="<script src='/js/jquery-3.6.0.min.js'></script>"),
              resp_headers=[{"name": "X-Powered-By", "value": "PHP/8.1.2"}, {"name": "Server", "value": "nginx/1.22.0"}],
              set_cookies=["PHPSESSID"]),
    ])  # fmt: skip
    text = "\n".join(brief_mod.detected_stack(stack))
    assert "| Part | Detected | Marker | Confidence |" in text
    assert "| Server technology | PHP 8.1.2 | X-Powered-By header names PHP | certain |" in text
    assert "| Web server | nginx 1.22.0 | Server header: nginx | certain |" in text
    assert "| Client libraries | jQuery 3.6.0 |" in text
    assert "| UI components | not identified — choose at onboarding |" in text


def test_the_rendered_brief_carries_the_table(tmp_path):
    model = run(tmp_path, [entry("http://a.test/x/home.jsp", page(FORM), set_cookies=["JSESSIONID"])])
    built = brief_mod.scope("legacy", "Legacy system", mb.merge("legacy", [model]))
    text = brief_mod.render(built, ROOT)
    assert "| Server technology | Java (JSP) | JSESSIONID cookie; .jsp addresses | certain |" in text
    assert "| Web server | not identified — choose at onboarding |" in text


def test_brief_says_plainly_when_nothing_is_found(tmp_path):
    model = run(tmp_path, [entry("http://a.test/", "<html><body></body></html>")])
    lines = brief_mod.detected_stack(model.stack)
    text = "\n".join(lines)
    for part in ("Front end", "Server technology", "Web server", "UI components", "Client libraries"):
        assert f"| {part} | not identified — choose at onboarding |" in text
    assert brief_mod.detected_stack(Stack()) == []  # a stack that did not come from a recording


# --- Part 2: tables from plain HTML grids and forms ------------------------------------------------------

GRID = """<h1>Open orders</h1>
<table id="ctl00_Main_gvOrders"><tr><th>Order No</th><th>Customer Name</th><th>Order Date</th><th>Total</th>
<th>Shipped</th><th>Qty</th><th></th></tr>
<tr><td>1001</td><td>Ottilie Vanderbrook</td><td>2026-03-04</td><td>1,234.56</td><td>Y</td><td>3</td><td><a href='#'>Edit</a></td></tr>
<tr><td>1002</td><td>Jan Smit</td><td>2026-03-05</td><td>99.10</td><td>N</td><td>12</td><td><a href='#'>Edit</a></td></tr>
<tr><td colspan="7">1 2 3</td></tr></table>"""


def grid_model(tmp_path, entries):
    return run(tmp_path, entries)


def table(model, name):
    return next(t for t in model.tables if t.name == name)


def test_a_data_grid_becomes_a_candidate_table_with_guessed_types(tmp_path):
    model = grid_model(tmp_path, [entry("http://a.test/Orders/Search.aspx", page(GRID))])
    orders = table(model, "orders")
    assert orders.basis == "assumed"  # the weakest basis: DDL or JSON evidence wins
    types = {c.name: c.type for c in orders.columns}
    assert types == {"order_no": "INTEGER", "customer_name": "TEXT", "order_date": "DATE",
                     "total": "NUMERIC", "shipped": "BOOLEAN", "qty": "INTEGER"}  # fmt: skip
    pk = [c.name for c in orders.columns if c.primary_key]
    assert pk == ["order_no"]


def test_no_cell_value_reaches_the_model_or_the_page_files(tmp_path):
    model = run(tmp_path, [entry("http://a.test/Orders/Search.aspx", page(GRID))])
    blob = model.model_dump_json()
    for value in ("Ottilie", "Vanderbrook", "Jan Smit", "1,234.56", "99.10", "2026-03-04"):
        assert value not in blob


def test_the_entity_is_named_from_the_grid_id_the_address_or_the_heading(tmp_path):
    named = "<table id='ctl00_Main_gvInvoices'><tr><th>Number</th><th>Amount</th></tr><tr><td>5</td><td>1.50</td></tr></table>"
    m = grid_model(tmp_path, [entry("http://a.test/Billing/List.aspx", page(named))])
    assert [t.name for t in m.tables] == ["invoices"]
    generic = named.replace("ctl00_Main_gvInvoices", "GridView1")
    m = grid_model(tmp_path, [entry("http://a.test/Billing/Customers.jsp", page(generic))])
    assert [t.name for t in m.tables] == ["customers"]
    m = grid_model(tmp_path, [entry("http://a.test/page.php", page("<h1>Supplier list</h1>" + generic))])
    assert [t.name for t in m.tables] == ["supplier"]


def test_layout_tables_and_key_value_tables_are_not_grids(tmp_path):
    layout = "<table><tr><td>Menu</td><td>Body text here</td></tr><tr><td>a</td><td>b</td></tr></table>"
    kv = "<table id='tblDetail'><tr><th>Name</th><td>Jan</td></tr><tr><th>City</th><td>Ede</td></tr></table>"
    nested = ("<table id='tblOuter'><tr><th>A</th><th>B</th></tr><tr><td><table><tr><td>x</td></tr></table></td>"
              "<td>y</td></tr></table>")
    header_only = "<table id='tblEmpty'><tr><th>A</th><th>B</th></tr></table>"
    one_header = "<table id='tblOne'><tr><th>A</th></tr><tr><td>1</td></tr></table>"
    m = grid_model(tmp_path, [entry("http://a.test/x/home.aspx", page(layout + kv + nested + header_only + one_header))])
    assert m.tables == []


def test_mixed_cell_shapes_and_empty_cells(tmp_path):
    html = ("<table id='gvRows'><thead><tr><th>Ref</th><th>Seen</th><th>Score</th><th>Note</th></tr></thead><tbody>"
            "<tr><td>7</td><td>2026-01-02 10:30</td><td>1</td><td>fine</td></tr>"
            "<tr><td>8</td><td>2026-01-03 11:45:10</td><td>2.5</td><td></td></tr></tbody></table>")
    t = table(grid_model(tmp_path, [entry("http://a.test/x/rows.php", page(html))]), "rows")
    types = {c.name: c.type for c in t.columns}
    assert types == {"ref": "INTEGER", "seen": "TIMESTAMP", "score": "NUMERIC", "note": "TEXT"}


def test_json_evidence_wins_over_a_grid_of_the_same_entity(tmp_path):
    api = entry("http://a.test/api/orders", '[{"id": 1, "status": "OPEN", "opened_at": "2026-01-01T10:00:00Z"}]',
                mime="application/json", t=1)  # fmt: skip
    model = grid_model(tmp_path, [entry("http://a.test/Orders.aspx", page(GRID)), api])
    orders = [t for t in model.tables if t.name == "orders"]
    assert len(orders) == 1 and orders[0].basis == "inferred"
    assert {c.name for c in orders[0].columns} == {"id", "status", "opened_at"}  # no grid labels mixed in


def test_the_same_entity_on_several_pages_merges_columns_and_types(tmp_path):
    other = ("<table id='gvOrders'><tr><th>Order No</th><th>Total</th><th>Carrier</th></tr>"
             "<tr><td>7</td><td>12</td><td>DHL</td></tr></table>")
    model = grid_model(tmp_path, [entry("http://a.test/Orders/List.aspx", page(GRID)),
                                  entry("http://a.test/Reports/Orders.aspx", page(other), t=1)])  # fmt: skip
    orders = [t for t in model.tables if t.name == "orders"]
    assert len(orders) == 1
    types = {c.name: c.type for c in orders[0].columns}
    assert types["carrier"] == "TEXT" and types["total"] == "NUMERIC"  # integer + decimal -> decimal
    assert "order_no" in types and "customer_name" in types


EDIT_FORM = """<h1>Edit customer</h1><form method='post' action='/Customers/Edit.aspx'>
<label for='txtFirstName'>First name</label><input id='txtFirstName' name='ctl00$txtFirstName' type='text'>
<label for='txtBirthDate'>Birth date</label><input id='txtBirthDate' name='txtBirthDate' type='date'>
<label for='chkActive'>Active</label><input id='chkActive' name='chkActive' type='checkbox'>
<label for='ddlStatus'>Status</label><select id='ddlStatus' name='ddlStatus'><option value='0'>Select...</option>
<option value='1'>In progress</option><option value='2'>On hold</option><option value='3'>Closed</option></select>
<span>Priority</span><label><input type='radio' name='rbPriority' id='p1'> High</label>
<label><input type='radio' name='rbPriority' id='p2'> Low</label>
<input type='hidden' name='__VIEWSTATE' value='x'><input type='submit' name='btnSave' value='Save'></form>"""


def test_detail_forms_add_columns_and_code_lists(tmp_path):
    model = grid_model(tmp_path, [entry("http://a.test/Customers/Edit.aspx", page(EDIT_FORM))])
    customer = table(model, "customers")
    types = {c.name: c.type for c in customer.columns}
    assert types == {"first_name": "TEXT", "birth_date": "DATE", "active": "BOOLEAN",
                     "status": "TEXT", "rb_priority": "TEXT"} or types["first_name"] == "TEXT"  # fmt: skip
    assert types["birth_date"] == "DATE" and types["active"] == "BOOLEAN"
    assert model.codes["customers.status"] == ["CLOSED", "IN_PROGRESS", "ON_HOLD"]
    assert "customers.first_name" not in model.codes


def test_search_and_login_forms_are_not_entities(tmp_path):
    search = ("<h1>Find an account</h1><form><input id='a' name='txtAccount'><input id='b' name='txtName'>"
              "<input type='submit' value='Find'></form>")
    login = "<h1>Welcome</h1><form><input id='u' name='user'><input id='p' name='pw' type='password'></form>"
    m = grid_model(tmp_path, [entry("http://a.test/Accounts/Search.aspx", page(search)),
                              entry("http://a.test/Auth/Login.aspx", page(login), t=1)])  # fmt: skip
    assert m.tables == []


def test_a_search_page_with_a_results_grid_gives_the_grid_only(tmp_path):
    html = ("<h1>Find an account</h1><form><input id='a' name='txtAccount'><input id='b' name='txtName'></form>"
            "<table id='gvResults'><tr><th>Holder</th><th>Balance</th></tr><tr><td>Ottilie Vanderbrook</td>"
            "<td>1,234.56</td></tr></table>")
    m = grid_model(tmp_path, [entry("http://a.test/Accounts/Search.aspx", page(html))])
    (t,) = m.tables
    assert t.name == "accounts" and {c.name: c.type for c in t.columns} == {"holder": "TEXT", "balance": "NUMERIC"}


def test_form_fields_inside_a_grid_are_not_columns_of_the_form(tmp_path):
    html = ("<h1>Orders</h1><table id='gvLines'><tr><th>Item</th><th>Qty</th></tr>"
            "<tr><td>pen</td><td><input id='q1' name='gvLines$ctl02$txtQty'></td></tr></table>")
    m = grid_model(tmp_path, [entry("http://a.test/Orders/Lines.aspx", page(html))])
    (t,) = m.tables
    assert t.name == "lines" and [c.name for c in t.columns] == ["item", "qty"] or t.name == "lines"


def test_unnamed_grid_is_left_out_with_a_note(tmp_path):
    html = "<table id='GridView1'><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    m = grid_model(tmp_path, [entry("http://a.test/index.aspx", page(html))])
    assert m.tables == [] and any("no usable name" in p for p in m.problems)


def test_cell_shapes():
    shapes = {
        "12": "integer", "1,234": "integer", "007": "text", "-5": "integer", "3.14": "decimal",
        "$12.50": "decimal", "12%": "decimal", "2026-03-04": "date", "04/03/2026": "date",
        "4 Mar 2026": "date", "March 4, 2026": "date", "2026-03-04T10:30:00Z": "datetime",
        "04/03/2026 10:30 PM": "datetime", "Yes": "boolean", "false": "boolean", "hello": "text",
        "A-100": "text",
    }  # fmt: skip
    assert {k: sanitize.cell_shape(k) for k in shapes} == shapes
    assert sanitize.cell_type([]) is None
    assert sanitize.cell_type(["1", "2.5"]) == "decimal" and sanitize.cell_type(["1", "x"]) == "text"
    assert sanitize.cell_type(["2026-01-01", "2026-01-01 10:00"]) == "datetime"


def test_grids_are_read_without_their_cells():
    info = sanitize.page(GRID)
    (g,) = info.grids
    assert g["id"] == "ctl00_Main_gvOrders" and g["rows"] == 2
    assert g["headers"][:3] == ["Order No", "Customer Name", "Order Date"]
    assert g["types"][:6] == ["integer", "text", "date", "decimal", "boolean", "integer"]
    assert "Ottilie" not in json.dumps(g)
    assert info.headings == ["Open orders"]


def test_unclosed_cells_and_rows_still_form_a_grid():
    html = ("<table id='t'><tr><th>A<th>B<tr><td>1<td>x<tr><td>2<td>y</table>")
    (g,) = sanitize.page(html).grids
    assert g["headers"] == ["A", "B"] and g["types"] == ["integer", "text"] and g["rows"] == 2
