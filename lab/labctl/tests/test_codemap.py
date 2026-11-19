"""The application's map from its code (labctl.codemap), the tokeniser it reads code with
(labctl.jstokens) and the opt-in fetch of the code a recording never loaded (labctl.codefetch).

Units first (no browser): the tokeniser's hard cases, every route, navigation and API shape,
the called / not-called marking, the masking of interface texts, the secret check and the
fetch's safety rules. Then the Angular reference application's minified build, checked against
what its source says (written down by hand below). Then the hand-written parcel depot
(fixtures/codespa) recorded with the real recorder and taken to a build package, before and
after the opt-in fetch of its lazy chunk.
"""

from __future__ import annotations

import json
import shutil
import sys
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from labctl import codefetch, codemap, lookfeel
from labctl import jstokens as js

HERE = Path(__file__).parent
REPO = HERE.parents[2]
ANGULAR = REPO / "reference" / "web" / "caseboard-angular"
HOST = "app.example.test"
sys.path.insert(0, str(HERE / "fixtures" / "codespa"))


def _res(path: str, text: str, mime: str | None = None, host: str = HOST) -> codemap.Res:
    mime = mime or {"js": "text/javascript", "json": "application/json", "css": "text/css"}.get(
        path.rsplit(".", 1)[-1], "text/html"
    )
    return codemap.Res(f"https://{host}{path}", host, path, "", mime, codemap.kind_of(path, mime),
                       text, len(text), host == HOST)  # fmt: skip


def _an(*files: tuple[str, str], checker=None) -> codemap.Analysis:
    return codemap.Analysis([_res(p, t) for p, t in files], {HOST}, checker)


def _doc(*files, screens=(), endpoints=(), checker=None) -> dict:
    return codemap.document(_an(*files, checker=checker), "demo", screens=list(screens),
                            endpoints=list(endpoints))  # fmt: skip


# =================================================================================================
# The tokeniser
# =================================================================================================


def _kinds(src: str) -> list[tuple[str, object]]:
    return [(t.kind, t.value) for t in js.tokens(src).toks]


def test_regex_or_division_is_decided_by_what_comes_before():
    assert ("regex", "a/b") not in _kinds("x = y / a / b;")
    assert _kinds("x = y / 2 / z")[3] == ("punct", "/")
    assert ("regex", "ab[/]c") in _kinds("r = /ab[/]c/g.test(s)")
    assert ("regex", "\\d+") in _kinds("return /\\d+/.exec(s)")
    assert ("regex", "x") in _kinds("if (a) {} /x/.test(b)")  # after a block
    assert ("regex", "2") not in _kinds("a = {}/2/1")  # an object divided: not a pattern after
    assert _kinds("a = (b) / 2")[-2] == ("punct", "/")
    assert ("regex", "q") in _kinds("case /q/: x")
    # a quote inside a regular expression does not start a string
    assert _kinds("s.replace(/'/g, '\"')")[4] == ("regex", "'")


def test_strings_comments_and_identifier_escapes():
    toks = _kinds("a = 'it\\'s' + \"\\u0041\\x42\\n\" // comment 'x\n/* block \" */ b = `t`")
    assert ("str", "it's") in toks and ("str", "AB\n") in toks
    assert toks[-1][0] == "tmpl" and toks[-1][1].strings == ["t"]
    assert not any(v == "comment 'x" for _, v in toks)
    assert ("name", "ɵfac") in _kinds("static \\u0275fac = 1")
    # an unterminated string ends at its line, not at the end of the file
    toks = _kinds("a = 'broken\nb = 'fine'")
    assert ("str", "fine") in toks and ("name", "b") in toks
    banners = js.tokens("/*! jQuery v3.7.1 | (c) OpenJS */\nvar a;").banners
    assert banners and "3.7.1" in banners[0]


def test_template_literals_nest_and_keep_their_expressions():
    t = js.tokens("x = `a${ {k: `b${1}c`}.k }d${y}e`; z = 1")
    tpl = t.toks[2].value
    assert isinstance(tpl, js.Template) and tpl.strings == ["a", "d", "e"]
    inner = tpl.exprs[0]
    assert inner[0].is_p("{") and inner[3].kind == "tmpl" and inner[3].value.strings == ["b", "c"]
    assert t.toks[-1].value == "1"  # the rest of the file is read again after the template


def test_literals_are_read_and_anything_else_is_an_expression():
    t = js.tokens("o = {a: 'x', b: !0, c: !1, d: -2, e: [1, 'y'], f() { return 1 }, g, ...h,"
                  " 'k-k': {n: null}, [m]: 3, get v() { return 2 }, w: fn(1)}")  # fmt: skip
    o = js.obj(t, 2)
    assert o["a"] == "x" and o["b"] is True and o["c"] is False and o["d"] == -2
    assert o["e"] == [1, "y"] and o["k-k"] == {"n": None}
    assert isinstance(o["f"], js.Expr) and isinstance(o["g"], js.Expr)
    assert isinstance(o["w"], js.Expr) and "m" not in o and isinstance(o["v"], js.Expr)
    close = t.pairs[2]
    assert t.toks[close].is_p("}") and t.parent[3] == 2


def test_a_large_minified_bundle_is_read_quickly_and_balanced():
    src = (ANGULAR / "site" / "chunk-JWL3ULEW.js").read_text("utf-8")
    import time

    start = time.monotonic()
    t = js.tokens(src)
    assert time.monotonic() - start < 10
    opens = [i for i, x in enumerate(t.toks) if x.kind == "punct" and x.value in "([{"]
    assert len(t) > 100_000 and all(i in t.pairs for i in opens)


# =================================================================================================
# Routes
# =================================================================================================

ANGULAR_SRC = """
const routes = [
  { path: 'login', component: LoginComponent, title: 'Sign in' },
  { path: 'admin', canActivate: [AdminGuard], loadChildren: () => import('./admin/admin.module.js').then(m => m.AdminModule) },
  { path: 'orders', component: OrdersComponent, canActivate: [authGuard], children: [
      { path: '', component: OrderListComponent },
      { path: ':orderId/edit', component: OrderEditComponent, canDeactivate: [unsavedGuard] },
  ]},
  { path: '', redirectTo: 'orders', pathMatch: 'full' },
  { path: '**', component: NotFoundComponent },
];
const authGuard = () => inject(Router).createUrlTree(['/login']);
RouterModule.forRoot(routes);
"""


def test_angular_route_table_with_children_guards_lazy_and_redirects():
    doc = _doc(("/main.js", ANGULAR_SRC), screens=["/login", "/orders"])
    by: dict[str, dict] = {}
    for r in doc["routes"]:  # the parent first; its default child (`''`) has the same path
        by.setdefault(r["path"], r)
    default = [r for r in doc["routes"] if r["path"] == "/orders"][1]
    assert default["written"] == "" and default["component"] == {"name": "OrderListComponent"}
    assert set(by) == {"/login", "/admin", "/orders", "/orders/{orderId}/edit", "/", "/**"}
    assert by["/login"]["title"] == "Sign in" and by["/login"]["status"] == "recorded"
    assert by["/login"]["component"] == {"name": "LoginComponent"}
    assert by["/admin"]["lazy_chunk"] == "/admin/admin.module.js"
    assert by["/admin"]["chunk_export"] == "AdminModule" and not by["/admin"]["chunk_downloaded"]
    assert by["/admin"]["guards"][0]["name"] == "AdminGuard"
    guard = by["/orders"]["guards"][0]
    assert guard == {
        "kind": "canActivate",
        "name": "authGuard",
        "sends_refused_visitor_to": "/login",
    }
    assert by["/orders"]["children"] == ["/orders", "/orders/{orderId}/edit"]
    edit = by["/orders/{orderId}/edit"]
    assert edit["parent"] == "/orders" and edit["status"] == "only in code"
    assert edit["guards"][0]["kind"] == "canDeactivate"
    assert by["/"]["redirect_to"] == "/orders" and by["/"]["status"] == "redirect"
    assert all(r["framework"] == "angular" for r in doc["routes"])
    assert doc["missing_code"] == [{"file": "/admin/admin.module.js", "routes": ["/admin"]}]


def test_react_router_objects_and_compiled_route_elements():
    objects = """
    const router = createBrowserRouter([
      { path: "/", element: jsx(Layout, {}), errorElement: jsx(Oops, {}), children: [
        { index: true, element: jsx(Home, {}) },
        { path: "reports/:year", lazy: () => import("./reports-4f3a.js") },
      ]},
    ]);"""
    compiled = """
    function App() { return jsxs(Routes, { children: [
      jsx(Route, { path: "/inbox", element: jsx(Inbox, {}) }),
      jsxs(Route, { path: "/settings", element: jsx(Settings, {}), children: [
        jsx(Route, { path: "profile", element: jsx(Profile, {}) })] })] }); }"""
    doc = _doc(("/a.js", objects), ("/b.js", compiled))
    by = {r["path"]: r for r in doc["routes"]}
    assert {"/", "/reports/{year}", "/inbox", "/settings", "/settings/profile"} <= set(by)
    assert by["/reports/{year}"]["lazy_chunk"] == "/reports-4f3a.js"
    assert by["/reports/{year}"]["parent"] == "/"
    assert by["/settings/profile"]["parent"] == "/settings"
    assert by["/inbox"]["component"] == {"name": "Inbox"}
    assert {r["framework"] for r in doc["routes"]} == {"react router"}


def test_vue_router_with_meta_title_guard_redirect_and_catch_all():
    src = """
    const router = createRouter({ history: createWebHistory(), routes: [
      { path: '/', redirect: '/home' },
      { path: '/home', name: 'home', component: Home, meta: { title: 'Home' } },
      { path: '/users/:id', component: () => import('./UserView.js'),
        beforeEnter: (to) => { if (!auth.ok) return router.push('/login') } },
      { path: '/:pathMatch(.*)*', component: NotFound },
    ]});"""
    by = {r["path"]: r for r in _doc(("/main.js", src))["routes"]}
    assert by["/"]["redirect_to"] == "/home" and by["/home"]["title"] == "Home"
    assert by["/home"]["name"] == "home" and by["/users/{id}"]["lazy_chunk"] == "/UserView.js"
    assert by["/users/{id}"]["guards"][0]["sends_refused_visitor_to"] == "/login"
    assert "/**" in by and by["/home"]["framework"] == "vue router"


def test_an_object_with_a_path_that_is_not_a_route_is_left_alone():
    src = "a = {path: '/x', width: 3, height: 4}; b = {path: 'M0 0L1 1', component: c};"
    assert _doc(("/x.js", src))["routes"] == []


def test_webpack_chunk_ids_are_named_through_the_runtime_map():
    runtime = 'r.u = e => "static/js/" + e + "." + {12: "ab12cd", 7: "ffee00"}[e] + ".chunk.js";'
    main = "const routes = [{ path: '/x', lazy: () => r.e(12).then(r.bind(r, 99)) }];"
    doc = _doc(("/static/js/runtime.js", runtime), ("/static/js/main.js", main))
    assert doc["routes"][0]["lazy_chunk"] == "/static/js/12.ab12cd.chunk.js"


# =================================================================================================
# Navigation elements and dialogs
# =================================================================================================


def test_html_in_scripts_gives_links_tabs_menus_dialog_openers_and_dialogs():
    src = """
    const Page = { template: `<nav class="navbar"><a routerLink="/orders">Orders</a>
      <a [routerLink]="['/orders', o.id]">Open</a></nav>
      <ul class="nav nav-tabs" role="tablist"><li><a class="nav-link" data-bs-toggle="tab"
      href="#lines" aria-controls="lines">Lines</a></li></ul>
      <div role="menu"><button role="menuitem" mat-menu-item>Export</button></div>
      <button data-bs-toggle="modal" data-bs-target="#confirm">Remove</button>
      <div class="modal" id="confirm" aria-labelledby="ct"><h5 id="ct">Remove it?</h5></div>
      <mat-tab-group><mat-tab label="A"></mat-tab></mat-tab-group>` };
    const routes = [{ path: '/orders', component: Page }];"""
    doc = _doc(("/m.js", src))
    nav = {(n["kind"], n["selector"]): n for n in doc["navigation"]}
    assert nav[("link", "a")]["target"] in ("/orders", "/orders/{id}")
    targets = {n.get("target") for n in doc["navigation"] if n["kind"] == "link"}
    assert {"/orders", "/orders/{id}"} <= targets
    assert nav[("navigation bar", "nav.navbar")]["routes"] == ["/orders"]
    assert (
        nav[("tab", "a.nav-link")]["text"] == "Lines"
        and nav[("tab", "a.nav-link")]["target"] == "#lines"
    )
    assert ("tab set", "ul.nav") in nav and ("menu", "div") in nav
    assert nav[("menu entry", "button")]["text"] == "Export"
    assert nav[("dialog opener", "button")]["opens"] == "#confirm"
    assert ("tab set", "mat-tab-group") in nav and ("tab", "mat-tab") in nav
    dialog = next(d for d in doc["dialogs"] if d["selector"] == "div#confirm")
    assert dialog["title"] == "Remove it?" and dialog["component"] == "Page"


def test_compiled_jsx_links_and_dialogs_opened_by_code():
    src = """
    function Menu() { return jsxs("nav", { className: "sidebar", children: [
      jsx(Link, { to: "/invoices", children: "Invoices" }),
      jsx("a", { href: "/help", role: "menuitem", children: "Help" })] }); }
    function wire() {
      document.getElementById("delete").addEventListener("click", () => {
        $("#confirmDelete").modal("show"); });
      const d = document.getElementById("notes");
      document.querySelector("#addNote").addEventListener("click", () => d.showModal());
      this.dialog.open(NoteDialog, { width: "400px" });
      if (confirm("Discard the changes?")) { location.href = "/drafts"; }
    }"""
    doc = _doc(("/m.js", src))
    nav = {n["selector"]: n for n in doc["navigation"]}
    assert nav["a"]["target"] in ("/invoices", "/help")
    assert {n.get("target") for n in doc["navigation"]} >= {"/invoices", "/help"}
    assert nav["nav.sidebar"]["kind"] == "navigation bar"
    dialogs = {d.get("selector") or d["kind"]: d for d in doc["dialogs"]}
    assert dialogs["#confirmDelete"]["opened_by"] == ["click on `#delete`"]
    assert dialogs["#notes"]["opened_by"] == ["click on `#addNote`"]
    assert dialogs["#notes"]["how"] == "showModal()"
    opened = next(d for d in doc["dialogs"] if d.get("how") == "dialog.open()")
    assert opened["component"] == "NoteDialog"
    confirm = next(d for d in doc["dialogs"] if d["kind"] == "browser confirm")
    assert confirm["text"] == "Discard the changes?"
    assert any(m["target"] == "/drafts" and m["how"] == "location.href"
               for m in doc["navigation_by_code"])  # fmt: skip


# =================================================================================================
# API calls
# =================================================================================================

API_SRC = """
const API = '/api/v2';
class OrderService {
  list(page) { return this.http.get(API + '/orders?page=' + page + '&sort=' + s); }
  one(id) { return this.http.get(`${API}/orders/${id}`); }
  save(o) { return this.http.put(API + '/orders/' + o.id, o); }
  remove(id) { return fetch(`/api/v2/orders/${id}`, { method: 'DELETE' }); }
  notes(id) { return axios.post('/api/v2/orders/' + id + '/notes', {}); }
  legacy() { return $.ajax({ url: '/legacy/report.do', type: 'post' }); }
  raw() { const x = new XMLHttpRequest(); x.open('PATCH', '/api/v2/flags'); }
  cached(k) { return this.cache.get(k); }
  external() { return fetch('https://metrics.example.test/collect'); }
  other() { return fetch(CONFIG.reportsUrl + '/export'); }
}"""


def test_api_calls_with_method_and_address_template_and_called_or_not():
    endpoints = [{"method": "GET", "path": "/api/v2/orders"},
                 {"method": "GET", "path": "/api/v2/orders/{id}"}]  # fmt: skip
    config = json.dumps({"reportsUrl": "https://reports.example.test/api"})
    doc = _doc(("/main.js", API_SRC), ("/assets/config.json", config), endpoints=endpoints)
    calls = {(c["method"], c["path"]): c for c in doc["api_calls"]}
    assert calls[("GET", "/api/v2/orders")]["query"] == ["page", "sort"]
    assert calls[("GET", "/api/v2/orders")]["status"] == "called in the recording"
    assert calls[("GET", "/api/v2/orders/{id}")]["endpoint"] == "GET /api/v2/orders/{id}"
    for key in (("PUT", "/api/v2/orders/{id}"), ("DELETE", "/api/v2/orders/{id}"),
                ("POST", "/api/v2/orders/{id}/notes"), ("POST", "/legacy/report.do"),
                ("PATCH", "/api/v2/flags")):  # fmt: skip
        assert calls[key]["status"] == "in code, not called", key
    assert calls[("GET", "/api/v2/orders")]["in"] == "OrderService"
    assert not any("cache" in c["path"] or c["path"] == "{k}" for c in doc["api_calls"])
    # another origin's address is never kept, not even through the configuration
    others = [c for c in doc["api_calls"] if c["path"].startswith("(another origin")]
    assert others and not any("reportsUrl" in c["path"] for c in doc["api_calls"])
    assert "reports" not in codemap.dump(doc) and "metrics" not in codemap.dump(doc)


def test_html_forms_in_documents_and_templates_are_calls_too():
    page = (
        '<html><body><form action="/search.do" method="post"><input name="q"></form></body></html>'
    )
    tpl = 'x = \'<form action="/upload" method="post"><input type=file></form>\';'
    doc = _doc(("/index.html", page), ("/m.js", tpl))
    paths = {(c["method"], c["path"]) for c in doc["api_calls"]}
    assert {("POST", "/search.do"), ("POST", "/upload")} <= paths


# =================================================================================================
# Interface texts, lists of values, the data checks
# =================================================================================================


def test_translation_texts_and_lists_of_values_lose_whatever_may_be_data():
    data = lookfeel.Data(names={"harbour", "north"}, tokens={"P-1001"})
    checker = codemap.Checker(data, observed={"Mira Holt"}, hosts={HOST})
    bundle = json.dumps({"nav": {"orders": "Orders", "help": "Help"},
                         "msg": {"saved": "Order saved.", "phone": "Call 0800 555 0199",
                                 "mail": "Write to ops@corp-mail.com", "who": "Mira Holt",
                                 "depot": "Harbour North", "key": "AKIAQX7ZPLANTED0KEY9",
                                 "example": "name@example.test"}})  # fmt: skip
    src = """
    export const STATUSES = [{value: 'open', label: 'Open'}, {value: 'held', label: 'On hold'}];
    const PEOPLE = [{id: 'a', name: 'Mira Holt'}, {id: 'b', name: 'Ivo Marsh'}];
    var Kind; (function (Kind) { Kind.Parcel = "parcel"; Kind.Letter = "letter"; })(Kind || (Kind = {}));
    var Flags; (function (e) { e[e.None = 0] = "None"; e[e.Dirty = 1] = "Dirty"; })(Flags || (Flags = {}));
    const ROWS = [['P-1001', 'Harbour North', 'Mira Holt'], ['P-1002', 'Quarry', 'Ivo']];
    document.title = 'Orders - Depot';
    """
    doc = _doc(("/i18n/en.json", bundle), ("/m.js", src), checker=checker)
    table = doc["text_tables"][0]
    assert table["language"] == "en"
    assert table["entries"] == {"nav.orders": "Orders", "nav.help": "Help",
                                "msg.saved": "Order saved.", "msg.example": "name@example.test"}  # fmt: skip
    assert table["dropped"] == 5
    enums = {e["name"]: e for e in doc["enumerations"]}
    assert enums["STATUSES"]["values"] == [{"value": "open", "label": "Open"},
                                           {"value": "held", "label": "On hold"}]  # fmt: skip
    assert enums["PEOPLE"]["values"] == []  # names of people: none of it is kept
    assert any(e["values"] == [{"value": "parcel", "label": "Parcel"},
                               {"value": "letter", "label": "Letter"}] for e in doc["enumerations"])  # fmt: skip
    assert not any(v["label"] == "Dirty" for e in doc["enumerations"] for v in e["values"])
    text = codemap.dump(doc)
    for value in ("Harbour", "Mira", "0199", "corp-mail", "AKIA", "P-1001", "Quarry"):
        assert value not in text, value
    assert {"text": "Orders - Depot", "where": "/m.js"} in doc["titles"]


# =================================================================================================
# The secret check
# =================================================================================================


@pytest.mark.parametrize("planted", [
    "-----BEGIN RSA PRIVATE KEY-----", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
    "AKIAQX7ZPLANTED0KEY9", "Server=db01;Database=cases;User Id=sa;Password=Pw12345;",
    "postgres://app:pw@db.internal:5432/x", "https://admin:Secret99@intra.example.test/",
    "reports.depot.corp.internal", "10.20.30.40", "https://billing.acme-bank.com/api",
    '"apiKey": "k3y-9f8e7d6c5b4a"', "xoxb-123456789012-abcdefghij",
    "Bearer abcdefghijklmnopqrstuvwxyz0123", "Zx81Kq0LmN3pR7sT9vW2yA4bC6dE8fG1hJ",
])  # fmt: skip
def test_the_check_finds_each_kind_of_secret_and_foreign_host(planted):
    found = codemap.check_text(json.dumps({"x": planted}), {HOST})
    assert found, planted
    assert all(planted not in f for f in found)  # named by kind and shape, never the value


def test_the_check_passes_what_a_map_normally_holds():
    doc = _doc(("/main.js", ANGULAR_SRC), ("/x.js", API_SRC))
    text = codemap.dump(doc) + json.dumps({"login.password": "Password", "token": "Token",
                                           "url": f"https://{HOST}/x", "f": "/chunk-CSK2V5PL.js",
                                           "cdn": "https://cdnjs.cloudflare.com/x.js"})  # fmt: skip
    assert codemap.check_text(text, {HOST}) == []


def test_writing_and_packaging_refuse_a_map_that_holds_a_secret(tmp_path):
    from labctl.builder import package as pkg

    app = tmp_path / "apps" / "demo"
    (app / "db").mkdir(parents=True)
    (app / "BRIEF.md").write_text("# brief\n", "utf-8")
    (app / "db" / "schema.sql").write_text("CREATE TABLE t (id TEXT);\n", "utf-8")
    (tmp_path / "docs").mkdir()
    shutil.copy(REPO / "AGENTS.md", tmp_path / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", tmp_path / "docs" / "factory-playbook.md")
    (app / codemap.FILE).write_text(json.dumps({"routes": [{"path": "/x",
                                    "title": "Server=db01;Password=Pw12345;"}]}), "utf-8")  # fmt: skip
    assert codemap.package_check(tmp_path, "demo")
    with pytest.raises(pkg.PackageError, match="codemap.json failed the privacy check"):
        pkg.build(tmp_path, "demo", tmp_path / "out")
    (app / codemap.FILE).write_text(json.dumps({"routes": [{"path": "/x"}]}), "utf-8")
    assert codemap.package_check(tmp_path, "demo") == []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        with zipfile.ZipFile(pkg.build(tmp_path, "demo", tmp_path / "out")) as zf:
            assert "apps/demo/codemap.json" in zf.namelist()
            assert "apps/demo/codemap.json" in zf.read("BUILD_TASK.md").decode()


# =================================================================================================
# The opt-in fetch: its rules
# =================================================================================================

OWN = {"https://app.example.test"}


@pytest.mark.parametrize("url, why", [
    ("https://app.example.test/static/js/admin.js", None),
    ("https://app.example.test/chunk-AB12.mjs?v=3", None),
    ("https://app.example.test/i18n/de.json", None),
    ("https://app.example.test/styles.css", None),
    ("https://other.example.test/admin.js", "not the application's own origin"),
    ("http://app.example.test/admin.js", "not the application's own origin"),
    ("https://app.example.test/orders", "not a static script"),
    ("https://app.example.test/report.xhtml", "not a static script"),
    ("https://app.example.test/api/orders.json", "under an API path"),
    ("https://app.example.test/static/logout.js", "could change state"),
    ("https://app.example.test/x.js?id=1001", "a query that is not a version"),
    ("https://u:p@app.example.test/x.js", "a user name or password"),
    ("ftp://app.example.test/x.js", "not an http(s) address"),
])  # fmt: skip
def test_only_static_code_of_the_own_origin_may_be_fetched(url, why):
    got = codefetch.refusal(url, OWN)
    assert (got is None) if why is None else (why in got), (url, got)


def test_the_answer_must_be_of_the_kind_of_file_asked_for():
    assert codefetch.type_ok("/a.js", "text/javascript; charset=utf-8")
    assert codefetch.type_ok("/a.mjs", "application/javascript")
    assert codefetch.type_ok("/a.json", "application/json")
    assert codefetch.type_ok("/a.css", "text/css")
    assert not codefetch.type_ok("/a.js", "text/html")  # a login page in place of the chunk
    assert not codefetch.type_ok("/a.json", "text/plain")


def _rec(tmp_path: Path, lazy: list[str]) -> Path:
    """A minimal recording: one page of the own origin whose script imports `lazy` chunks."""
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "steps.jsonl").write_text(json.dumps({"seq": 1, "action": "goto",
                                     "url": "https://app.example.test/"}) + "\n", "utf-8")  # fmt: skip
    main = "const routes = [" + ",".join(
        f"{{path: '/p{i}', loadComponent: () => import('{p}').then(m => m.X)}}"
        for i, p in enumerate(lazy)) + "];"  # fmt: skip
    entry = {"request": {"method": "GET", "url": "https://app.example.test/main.js"},
             "response": {"status": 200, "content": {"mimeType": "text/javascript", "text": main}}}  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": [entry]}}), "utf-8")
    return rec


def test_nothing_is_asked_without_yes_and_only_the_planned_files_with_it(tmp_path):
    rec = _rec(tmp_path, ["./chunk-a.js", "./chunk-b.js", "/api/x.json", "./logout.js"])
    asked: list[tuple[str, dict]] = []
    waits: list[float] = []

    def get(url, headers):
        asked.append((url, headers))
        return 200, "text/javascript", b"export const X = {};"

    plan = codefetch.fetch(rec, get=get, sleep=waits.append)
    assert asked == [] and len(plan["planned"]) == 4 and not (rec / "code").exists()
    done = codefetch.fetch(rec, yes=True, get=get, delay=0.5, sleep=waits.append)
    assert [u for u, _ in asked] == ["https://app.example.test/chunk-a.js",
                                     "https://app.example.test/chunk-b.js"]  # fmt: skip
    assert {r["reason"] for r in done["refused"]} == {"under an API path",
                                                      "a word in its address that could change state"}  # fmt: skip
    assert all("Cookie" not in h for _, h in asked)  # no session
    assert waits and all(w <= 0.5 for w in waits)  # waited between the two, never longer
    manifest = json.loads((rec / "code" / "manifest.json").read_text("utf-8"))
    assert [f["file"] for f in manifest["files"]] == ["chunk-a.js", "chunk-b.js"]
    # the code map reads what was fetched; a second run asks for nothing again
    res = codemap.resources([rec])
    assert {r.path for r in res if r.source == "fetched"} == {"/chunk-a.js", "/chunk-b.js"}
    asked.clear()
    codefetch.fetch(rec, yes=True, get=get, sleep=waits.append)
    assert asked == []


def test_a_wrong_answer_is_refused_and_a_busy_server_stops_the_fetch(tmp_path):
    rec = _rec(tmp_path, ["./a.js", "./b.js", "./c.js"])
    answers = iter([(200, "text/html", b"<html>sign in</html>"), (429, "", b""), (200, "x", b"")])
    done = codefetch.fetch(rec, yes=True, get=lambda u, h: next(answers), sleep=lambda s: None)
    assert done["refused"][0]["reason"].startswith("the answer's content type")
    assert "429" in done["stopped"] and done["fetched"] == []
    assert not list((rec / "code").glob("*.js"))


def test_the_saved_browser_state_is_the_only_session_sent(tmp_path):
    rec = _rec(tmp_path, ["./a.js"])
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"cookies": [
        {"name": "sid", "value": "s1", "domain": "app.example.test"},
        {"name": "other", "value": "o1", "domain": "elsewhere.example.test"},
        {"name": "old", "value": "x", "domain": ".example.test", "expires": 1}]}), "utf-8")  # fmt: skip
    seen = []
    codefetch.fetch(rec, yes=True, storage_state=state, sleep=lambda s: None,
                    get=lambda u, h: (seen.append(h), (200, "text/javascript", b"1"))[1])  # fmt: skip
    assert seen[0]["Cookie"] == "sid=s1"


def test_the_real_getter_uses_get_and_does_not_follow_a_redirect():
    import http.server
    import threading

    log = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN002
            pass

        def do_GET(self):  # noqa: N802
            log.append(("GET", self.path))
            if self.path == "/moved.js":
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.end_headers()
            self.wfile.write(b"1")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert codefetch.http_get(base + "/a.js", {})[0] == 200
        assert codefetch.http_get(base + "/moved.js", {})[0] == 302
    finally:
        server.shutdown()
        server.server_close()
    assert log == [("GET", "/a.js"), ("GET", "/moved.js")]  # /login was never asked


# =================================================================================================
# The Angular reference application, from its minified build alone
# =================================================================================================

# Written by hand from reference/web/caseboard-angular/src/app (app.routes.ts, auth.guard.ts,
# app.ts, case-list.ts, case-edit.ts, sign-in.ts). The application calls no API: its state is in
# memory (case-store.ts), with the lists of values STATUSES and PRIORITIES.
ANGULAR_EXPECTED = {
    "routes": {
        "/sign-in": {"selector": "app-sign-in", "title": "Sign in - Caseboard", "guard": None},
        "/cases": {"selector": "app-case-list", "title": "Cases - Caseboard", "guard": "/sign-in"},
        "/cases/{id}": {"selector": "app-case-edit", "title": "Edit case - Caseboard",
                        "guard": "/sign-in", "lazy": "/chunk-CSK2V5PL.js"},
        "/": {"redirect": "/cases"},
        "/**": {"redirect": "/cases"},
    },
    "links": {("a#navCases", "/cases", "app-root"), ('a[id^="open-"]', "/cases/{param}", "app-case-list"),
              ("a#btnCancel", "/cases", "app-case-edit")},
    "dialog": {"selector": "div#signOutDialog", "title": "Sign out?",
               "opened_by": ["click on `button#btnSignOut`"],
               "closed_by": ["click on `button#btnSignOutCancel`", "click on `button#btnSignOutConfirm`",
                             "keydown.escape (anywhere on the page)"]},
    "by_code": {("/sign-in", "router.navigate()"), ("/cases", "router.navigate()")},
    "enums": [["open", "in-progress", "waiting", "closed"], ["low", "normal", "high"]],
}  # fmt: skip


def _angular(files: list[str], screens: list[str]) -> dict:
    out = []
    for name in files:
        text = (ANGULAR / "site" / name).read_text("utf-8")
        out.append(("/" if name == "index.html" else "/" + name, text))
    return _doc(*out, screens=screens)


def test_angular_reference_routes_guards_lazy_route_dialog_and_links_from_the_bundle():
    doc = _angular(["index.html", "main-JIGO3NH3.js", "chunk-JWL3ULEW.js", "chunk-CSK2V5PL.js",
                    "styles-HJXEHUCQ.css"], ["/sign-in", "/cases", "/cases/{id}"])  # fmt: skip
    by = {r["path"]: r for r in doc["routes"]}
    assert set(by) == set(ANGULAR_EXPECTED["routes"])
    for path, want in ANGULAR_EXPECTED["routes"].items():
        r = by[path]
        if "redirect" in want:
            assert r["redirect_to"] == want["redirect"] and r["status"] == "redirect"
            continue
        assert r["component"]["selector"] == want["selector"] and r["title"] == want["title"]
        assert r["status"] == "recorded"
        guards = [g.get("sends_refused_visitor_to") for g in r.get("guards") or []]
        assert guards == ([want["guard"]] if want["guard"] else [])
        assert r.get("lazy_chunk") == want.get("lazy")
    links = {(n["selector"], n.get("target"), n["component"]) for n in doc["navigation"]
             if n["kind"] == "link"}  # fmt: skip
    assert links == ANGULAR_EXPECTED["links"]
    shell = next(n for n in doc["navigation"] if n["selector"] == "a#navCases")
    assert shell["text"] == "Cases" and shell["routes"][0].startswith("(every screen")
    (dialog,) = doc["dialogs"]
    for key, want in ANGULAR_EXPECTED["dialog"].items():
        assert dialog.get(key if key != "title" else "text") == want or dialog.get(key) == want, key
    assert dialog["shown_when"] == "`asking` is true"
    assert {(m["target"], m["how"]) for m in doc["navigation_by_code"]} == ANGULAR_EXPECTED[
        "by_code"
    ]
    assert doc["api_calls"] == []  # none in the source; none found in 260 KB of framework code
    assert [[v["value"] for v in e["values"]] for e in doc["enumerations"]] == ANGULAR_EXPECTED[
        "enums"
    ]
    assert {"name": "@angular/core", "version": "21.2.26"}.items() <= doc["third_party"][0].items()
    assert doc["missing_code"] == []
    assert codemap.check_text(codemap.dump(doc), {HOST}) == []


def test_angular_reference_without_the_lazy_chunk_names_it_and_its_route():
    doc = _angular(["index.html", "main-JIGO3NH3.js", "chunk-JWL3ULEW.js"], ["/sign-in", "/cases"])
    edit = next(r for r in doc["routes"] if r["path"] == "/cases/{id}")
    assert edit["status"] == "only in code" and edit["chunk_downloaded"] is False
    assert edit["chunk_export"] == "CaseEdit" and "component" not in edit
    assert doc["missing_code"] == [{"file": "/chunk-CSK2V5PL.js", "routes": ["/cases/{id}"]}]
    nav = codemap.extend_navigation({"screens": [{"path": "/sign-in"}, {"path": "/cases"}],
                                     "transitions": []}, doc)  # fmt: skip
    added = next(s for s in nav["screens"] if s["path"] == "/cases/{id}")
    assert added["source"] == "code" and added["kind"] == "found in code, not recorded"
    assert any(m["from"] == "/cases" and m["to"] == "/cases/{id}" and m["source"] == "code"
               for m in nav["transitions"])  # fmt: skip


# =================================================================================================
# The JSF reference application: server pages, recorded (needs a JDK and its jars)
# =================================================================================================

JSF = REPO / "reference" / "web" / "caseboard-jsf"

# Written by hand from reference/web/caseboard-jsf/webapp (*.xhtml, WEB-INF/template.xhtml): four
# pages; command links that post their form (sign out, a case's title, Previous, Next, Cancel);
# an f:ajax status menu; one h:link (Back to the list -> /cases.xhtml); a form per page. Where a
# command link leads is decided in Java (the beans' outcomes), which the browser never gets.
JSF_EXPECTED = {
    "pages": {"/login.xhtml", "/cases.xhtml", "/edit.xhtml", "/saved.xhtml"},
    "command links": {"a#j_idt10:signOut", "a#list:table:{row}:open", "a#list:next",
                      "a#list:previous", "a#edit:cancel", "a#j_idt12:signOut"},
    "ajax": {"select#edit:status"},
    "links": {("a#backToList", "/cases.xhtml", "Back to the list")},
}  # fmt: skip


def _jsf_module(name: str):
    import importlib.util

    full = f"caseboard_jsf_{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, JSF / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
    return sys.modules[full]


def test_jsf_reference_pages_command_links_and_forms_from_its_recording(tmp_path):
    reason = _jsf_module("build").unavailable()
    if reason:
        pytest.skip(f"the JSF reference application cannot run here: {reason}")
    pytest.importorskip("playwright.sync_api")
    run = _jsf_module("run").serve()
    try:
        rec = _jsf_module("certify_local").record(run.base, tmp_path / "rec")
    finally:
        run.stop()
    from labctl import stepfacts

    screens = []
    for line in (rec / "steps.jsonl").read_text("utf-8").splitlines():
        p = stepfacts._pattern(json.loads(line).get("url"))
        if p and p not in screens:
            screens.append(p)
    an = codemap.Analysis(codemap.resources([rec]), codemap.own_hosts([rec]))
    doc = codemap.document(an, "jsf", screens=screens)
    assert {r["path"] for r in doc["routes"]} == JSF_EXPECTED["pages"]
    assert all(
        r["status"] == "recorded" and r["framework"] == "server pages" for r in doc["routes"]
    )
    kinds: dict[str, set] = {}
    for n in doc["navigation"]:
        kinds.setdefault(n["kind"].split(" (")[0], set()).add(n["selector"])
    assert kinds["command link"] == JSF_EXPECTED["command links"]
    assert kinds["ajax update"] == JSF_EXPECTED["ajax"]
    links = {(n["selector"], n.get("target"), n.get("text")) for n in doc["navigation"]
             if n["kind"] == "link"}  # fmt: skip
    assert links == JSF_EXPECTED["links"]
    forms = {(c["method"], c["path"]) for c in doc["api_calls"]}
    assert forms == {("POST", p) for p in JSF_EXPECTED["pages"]}
    assert doc["third_party"][0]["name"] == "javax.faces"
    assert doc["third_party"][0]["version"] == "specification 2.3"
    text = codemap.dump(doc)
    assert "jsessionid" not in text.lower() and codemap.check_text(text, an.hosts) == []
    assert "C-1003" not in text and "Signed in as" not in text  # data, and a sentence holding it


# =================================================================================================
# The parcel depot, recorded, packaged, then completed with the opt-in fetch
# =================================================================================================


def _root(path: Path) -> Path:
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (path / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", path / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", path / "docs" / "factory-playbook.md")
    return path


def _package(root: Path) -> dict[str, str]:
    from labctl import factory
    from labctl.builder import package as pkg

    factory.generate(root, "depot", force=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        path = pkg.build(root, "depot", root / "packages")
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


@pytest.fixture(scope="module")
def depot(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    import spa_flow
    import spa_server

    from labctl import recorder

    root = _root(tmp_path_factory.mktemp("codespa") / "repo")
    rec = root / "evidence" / "depot" / "run"
    run = spa_server.serve()
    try:
        recorder.record(f"{run.url}/parcels", rec, headless=True, script=spa_flow.run)
        before = _package(root)
        recorded = list(run.requests)
        run.requests.clear()
        fetched = codefetch.fetch(rec, yes=True, delay=0.2)
        asked = list(run.requests)
        after = _package(root)
    finally:
        run.stop()
    return SimpleNamespace(root=root, rec=rec, before=before, after=after, recorded=recorded,
                           asked=asked, fetched=fetched,
                           code=json.loads(before["apps/depot/codemap.json"]),
                           code2=json.loads(after["apps/depot/codemap.json"]),
                           nav=json.loads(before["apps/depot/navigation.json"]),
                           brief=before["apps/depot/BRIEF.md"])  # fmt: skip


EXPECTED_ROUTES = {  # from static/js/main.js, by hand: path -> recorded by spa_flow.py?
    "/sign-in": True, "/": "redirect", "/parcels": True, "/parcels/{id}": False,
    "/parcels/{id}/overview": True, "/parcels/{id}/history": True, "/depots": False,
    "/admin": False, "/admin/users": False, "/admin/settings": False, "/help": False, "/**": False,
}  # fmt: skip
EXPECTED_CALLS = {  # method, address -> made in the recording?
    ("GET", "/api/session"): True, ("POST", "/api/session"): True, ("DELETE", "/api/session"): False,
    ("GET", "/api/parcels"): True, ("POST", "/api/parcels"): False,
    ("GET", "/api/parcels/{id}"): True, ("DELETE", "/api/parcels/{id}"): False,
    ("GET", "/api/parcels/{id}/history"): True, ("GET", "/api/depots"): False,
    ("GET", "(another origin: not kept)"): False, ("GET", "/static/i18n/en.json"): True,
}  # fmt: skip


def test_the_depot_map_has_every_route_and_says_which_were_recorded(depot):
    got = {r["path"]: r["status"] for r in depot.code["routes"]}
    want = {p: ("recorded" if v is True else "redirect" if v == "redirect" else "only in code")
            for p, v in EXPECTED_ROUTES.items()}  # fmt: skip
    assert got == want
    by = {r["path"]: r for r in depot.code["routes"]}
    assert by["/parcels"]["guards"][0] == {"kind": "beforeEnter", "name": "requireSession",
                                           "sends_refused_visitor_to": "/sign-in"}  # fmt: skip
    assert by["/admin"]["guards"][0]["sends_refused_visitor_to"] == "/parcels"
    assert by["/admin"]["lazy_chunk"] == "/static/js/admin.js"
    assert by["/admin"]["children"] == ["/admin/users", "/admin/settings"]
    assert by["/depots"]["title"] == "Depots - Parcel depot"
    assert depot.code["missing_code"] == [{"file": "/static/js/admin.js",
                                           "routes": ["/admin", "/admin/settings", "/admin/users"]}]  # fmt: skip


def test_the_depot_map_has_its_menu_tabs_dialog_and_calls(depot):
    nav = {(n["kind"], n["selector"]): n for n in depot.code["navigation"]}
    shell = "(every screen: the shell around the routed screens)"
    assert nav[("link", "a#navDepots")]["target"] == "/depots"
    assert nav[("link", "a#navDepots")]["routes"] == [shell]
    assert nav[("menu", "div#userMenu")]["routes"] == [shell]
    assert nav[("menu entry", "a#menuHelp")]["target"] == "/help"
    assert nav[("tab", "a#tabHistory")]["target"] == "/parcels/{id}/history"
    assert nav[("tab", "a#tabHistory")]["routes"] == ["/parcels/{id}"]
    assert ("tab set", "div#parcelTabs") in nav and ("tab panel", "section#parcelPanel") in nav
    dialog = next(d for d in depot.code["dialogs"] if d["kind"] == "dialog")
    assert dialog["selector"] == "dialog#newParcelDialog" and dialog["title"] == "New parcel"
    assert dialog["opened_by"] == ["click on `#newParcel`"] and dialog["routes"] == ["/parcels"]
    assert "click on `#cancelParcel`" in dialog["closed_by"]
    calls = {(c["method"], c["path"]): c["status"] == "called in the recording"
             for c in depot.code["api_calls"]}  # fmt: skip
    assert calls == EXPECTED_CALLS
    enum = depot.code["enumerations"][0]
    assert enum["name"] == "PARCEL_STATUSES" and len(enum["values"]) == 4
    texts = depot.code["text_tables"][0]["entries"]
    assert texts["parcel.new"] == "New parcel" and "depot.example" not in texts
    assert "support.phone" not in texts and depot.code["text_tables"][0]["dropped"] == 2


def test_the_depot_package_carries_the_map_and_no_script_value_or_secret(depot):
    import spa_flow

    names = set(depot.before)
    assert "apps/depot/codemap.json" in names
    assert not [n for n in names if n.endswith((".js", ".mjs"))]  # no script is carried
    text = depot.before["apps/depot/codemap.json"]
    for value in (*spa_flow.TYPED.values(), *spa_flow.DATA, "AKIA", "corp.internal", "0199",
                  "127.0.0.1"):  # fmt: skip
        assert value not in text, value
    assert codemap.check_text(text, set()) == []
    task = depot.before["BUILD_TASK.md"]
    assert (
        "`apps/depot/codemap.json`" in task.split("## Read in this order", 1)[1].split("\n## ")[0]
    )
    index = depot.before["INDEX.md"]
    assert "| `apps/depot/codemap.json` | What the application's code defines" in index
    gaps = json.loads(depot.before["MANIFEST.json"])["completeness"]["gaps"]
    assert any("`/static/js/admin.js`" in g and "fetch-code" in g for g in gaps)


def test_the_depot_brief_says_what_the_code_defines_and_what_to_build(depot):
    part = depot.brief.split("## What the application's code defines", 1)[1].split("\n## ", 1)[0]
    assert "Build every route found in code at least as a reachable page" in part
    assert (
        "| `/depots` | **no: only in code** | `DepotList` | requireSession -> `/sign-in` |" in part
    )
    assert "| `/parcels` | yes (`/parcels`) |" in part
    assert "| GET | `/api/depots` | **no: in code, not called** | `/depots` |" in part
    assert "`/static/js/admin.js` (route `/admin`" in part and "fetch-code" in part
    assert '`received` "Received"' in part
    scope = depot.brief.split("## Scope", 1)[1].split("\n## ", 1)[0]
    assert "built too, as reconstructions" in scope


def test_the_depot_navigation_map_has_every_route_with_the_source_of_each_way(depot):
    nav = depot.nav
    screens = {s["path"]: s for s in nav["screens"]}
    for path, rec in EXPECTED_ROUTES.items():
        if rec is True:
            assert screens[path].get("source") is None and screens[path]["in_scope"]
        elif rec is False and path != "/**":
            assert screens[path]["source"] == "code" and screens[path]["in_scope"], path
    assert screens["/depots"]["api_calls"] == ["GET /api/depots"]
    assert any(w["source"] == "code" for w in screens["/depots"]["ways_in"])
    sources = {m["source"] for m in nav["transitions"]}
    assert sources == {"recording", "page", "code"}
    rec = [m for m in nav["transitions"] if m["source"] == "recording"]
    assert ("/sign-in", "/parcels") in {(m["from"], m["to"]) for m in rec}
    assert any(m["source"] == "page" and m["to"] == "/depots" for m in nav["transitions"])
    assert any(m["source"] == "code" and m["by"].startswith("guard requireSession")
               for m in nav["transitions"])  # fmt: skip
    loose = [x for s in nav["screens"] for x in s.get("links_seen_not_visited") or []
             if x["target"] == "/depots"]  # fmt: skip
    assert loose and all(x["status"] == "found in code" for x in loose)
    assert nav["undefined_targets"] == []


def test_fetch_code_asks_only_for_the_lazy_chunk_then_the_map_is_complete(depot):
    assert [(m, p.split("?")[0]) for m, p, _ in depot.asked] == [("GET", "/static/js/admin.js")]
    assert all(cookie == "" for _, _, cookie in depot.asked)  # no session was sent
    assert [f["file"] for f in depot.fetched["fetched"]] == ["static_js_admin.js"]
    assert (depot.rec / "code" / "static_js_admin.js").is_file()
    assert not any("admin.js" in n for n in depot.after)  # stored beside the recording only
    code = depot.code2
    assert code["missing_code"] == []
    by = {r["path"]: r for r in code["routes"]}
    assert by["/admin/users"]["component"] == {"name": "AdminUsers"}
    assert by["/admin"]["chunk_downloaded"] is True
    calls = {(c["method"], c["path"]): c for c in code["api_calls"]}
    assert calls[("GET", "/api/admin/users")]["routes"] == ["/admin/users"]
    assert calls[("PUT", "/api/admin/settings")]["status"] == "in code, not called"
    tabs = {n["selector"]: n for n in code["navigation"] if n["kind"] == "tab"}
    assert tabs["a#tabSettings"]["target"] == "/admin/settings"
    assert tabs["a#tabSettings"]["routes"] == ["/admin"]
    gaps = json.loads(depot.after["MANIFEST.json"])["completeness"]["gaps"]
    assert not any("admin.js" in g for g in gaps)
    assert any(f["source"] == "fetched" for f in code["files"])
