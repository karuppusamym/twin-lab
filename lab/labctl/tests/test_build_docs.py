"""The build documents of a package (labctl.builder.docs) and the cookie facts.

Units first (no browser): the cookie attributes read from a `Set-Cookie` header never carry the
value; the session facts never carry a credential; the documents of a small hand-made package
(an unrecorded page, a screen known only from the code map) and the completeness check of the
documents. Then the help desk (fixtures/apidesk) and the permit form (fixtures/formdesk) are
recorded with the real recorder, packaged, and every new file is read as a builder would.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

HERE = Path(__file__).parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE / "fixtures" / "apidesk"))
sys.path.insert(0, str(HERE / "fixtures" / "formdesk"))

import desk_app  # noqa: E402
import desk_flow  # noqa: E402
import form_app  # noqa: E402
import form_flow  # noqa: E402

from labctl import apicatalog as ac  # noqa: E402
from labctl import sanitize, signin  # noqa: E402
from labctl.builder import complete, docs  # noqa: E402
from labctl.builder import package as pkg  # noqa: E402

SECRET = "f00dbabe5ec7e7c0ffee1234"

# =================================================================================================
# Cookies: names and attributes, never a value
# =================================================================================================


def _entry(*set_cookies: str, url="https://app.corp.example:8443/api/session", cookies=None):
    return {
        "startedDateTime": "2026-11-19T02:52:20.000Z",
        "request": {"method": "POST", "url": url, "headers": [], "cookies": []},
        "response": {
            "status": 200,
            "headers": [{"name": "Date", "value": "Thu, 19 Nov 2026 02:52:20 GMT"},
                        *[{"name": "Set-Cookie", "value": v} for v in set_cookies]],
            "cookies": cookies or [],
        },
    }  # fmt: skip


def test_cookie_attributes_keep_name_and_attributes_never_the_value():
    rows = sanitize.cookie_attributes(_entry(
        f"sid={SECRET}; Path=/app/orders/10234; HttpOnly; Secure; SameSite=Strict; Max-Age=3600",
        f"pref={SECRET}x; Domain=.corp.example; Expires=Fri, 20 Nov 2026 02:52:20 GMT",
        f"csrf={SECRET}y; Domain=app.corp.example; SameSite=None; Secure; Partitioned",
        "gone=; Path=/; Max-Age=0"))  # fmt: skip
    by = {r["name"]: r for r in rows}
    assert set(by) == {"sid", "pref", "csrf", "gone"}
    assert by["sid"] == {"name": "sid", "path": "/app/orders/{id}", "http_only": True,
                         "secure": True, "partitioned": False, "same_site": "Strict",
                         "domain": "not set: the cookie belongs to the application's own host only",
                         "lifetime": "persistent: 1 hour (3600 s)", "max_age_seconds": 3600}  # fmt: skip
    assert by["pref"]["domain"].startswith("a parent domain of the application's host")
    assert by["pref"]["lifetime"] == "persistent: 1 day (86400 s)"
    assert by["csrf"]["domain"] == "the application's own host" and by["csrf"]["partitioned"]
    assert by["csrf"]["same_site"] == "None" and "session:" in by["csrf"]["lifetime"]
    assert by["gone"]["clears"] is True and by["gone"]["lifetime"].startswith("cleared")
    text = json.dumps(rows)
    assert SECRET not in text and "corp.example" not in text  # no value, no host name
    # a HAR that holds only the parsed list (Playwright turns Max-Age=0 into a past expiry)
    only = sanitize.cookie_attributes(_entry(cookies=[
        {"name": "sid", "value": SECRET, "path": "/", "httpOnly": True, "sameSite": "Lax",
         "expires": "2026-11-19T02:52:19.000Z"}]))  # fmt: skip
    assert only[0]["clears"] and only[0]["same_site"] == "Lax" and SECRET not in json.dumps(only)


def test_the_session_facts_carry_no_credential():
    found = {"screen": "/login", "fields": [{"selector": "input#u", "password": False}],
             "call": {"method": "POST", "path": "/api/session", "body": "JSON",
                      "fields": ["username", "password"]},
             "attempts": [{"recording": "r", "step": 3, "status": 200, "accepted": True,
                           "by": "click button#go", "lands_on": "/home"}],
             "users": [{"username": "saffron.cobalt", "password": "Synthetic-pass-2"}],
             "cookies": ["sid"], "schemes": [], "sign_out": [], "unauthenticated": [],
             "whoami": [], "cookie_sent_back": True,
             "cookie_facts": [{"name": "sid", "set_by": [{"request": "POST /api/session",
                                                          "status": 200, "step": 3,
                                                          "screen": "/login"}],
                               "cleared_by": [], "attributes": {"path": "/", "http_only": True},
                               "sent_back": True, "session_cookie": True}]}  # fmt: skip
    doc = signin.facts_doc("demo", found)
    text = json.dumps(doc)
    assert "Synthetic-pass-2" not in text and "saffron" not in text and "users" not in doc
    assert doc["cookies"][0]["name"] == "sid" and "never captured" in doc["about"]
    assert doc["session"] == {"mechanism": "cookie", "cookies": ["sid"], "schemes": [],
                              "sent_back": True}  # fmt: skip
    lines = signin.cookie_lines(found)
    assert lines[0].startswith("- Cookie `sid` (the session): set by `POST /api/session 200`")
    assert "never cleared in the recording" in lines[0] and "value is never kept" in lines[0]
    assert signin.facts_doc("demo", None) is None


# =================================================================================================
# The documents of a small hand-made package
# =================================================================================================


def _files(**extra) -> dict[str, bytes]:
    app = "apps/demo"
    nav = {"entry": {"screen": "/a", "step": 1},
           "screens": [{"path": "/a", "in_scope": True, "name": "a", "page": f"{app}/pages/a.html",
                        "elements": [{"selector": "button#go", "label": "Go", "type": "button",
                                      "used": [{"step": 2, "action": "click",
                                                "requests": ["GET /api/x 200"],
                                                "goes_to": "/b"}]}],
                        "ways_out": [{"to": "/b", "by": "button", "selector": "button#go",
                                      "steps": [2]}]},
                       {"path": "/b", "in_scope": True, "name": "b", "elements": []}],
           "transitions": [{"from": "/a", "to": "/b", "selector": "button#go"}],
           "unrecorded_pages": [{"path": "/reports", "title": "Reports", "linked_from": ["/a"],
                                 "in_scope": False}]}  # fmt: skip
    cat = {"endpoints": [{"method": "GET", "path": "/api/x", "file": "api/get-api-x.json",
                          "auth": "none", "statuses": ["200"], "table": "items"}]}  # fmt: skip
    endpoint = {"method": "GET", "path": "/api/x",
                "triggered_by": [{"screen": "/a", "action": "click", "selector": "button#go",
                                  "steps": [2]}],
                "responses": {"200": {"content_type": "application/json"}}}  # fmt: skip
    files = {
        f"{app}/navigation.json": json.dumps(nav),
        f"{app}/api/catalogue.json": json.dumps(cat),
        f"{app}/api/get-api-x.json": json.dumps(endpoint),
        f"{app}/pages/a.html": "<html><head><title>Demo - A</title></head><body><h1>A</h1>"
                               '<button id="go" type="button">Go</button>'
                               '<span id="total">9999</span></body></html>',
        f"{app}/db/schema.sql": "CREATE TABLE items (id TEXT NOT NULL, name TEXT, "
                                "PRIMARY KEY (id));\n",
        f"{app}/db/seed.json": json.dumps({"items": [{"id": "IT-1", "name": "x"}]}),
        f"{app}/db/seed.sql": "-- seed\n",
        f"{app}/app.yaml": yaml.safe_dump({"id": "demo", "stack": {"db": {"engine": "sqlite"}}}),
        f"{app}/steps/r.json": json.dumps({"steps": [
            {"n": 1, "action": "goto", "screen": "/a"},
            {"n": 2, "action": "click", "screen": "/a", "selector": "button#go"}]}),
    }  # fmt: skip
    files.update(extra)
    return {k: v.encode() if isinstance(v, str) else v for k, v in files.items()}


def test_a_page_file_per_screen_marked_by_kind_and_the_code_map_read_when_present():
    code = {"routes": [{"path": "/admin/settings", "file": "app.routes.ts"}, {"path": "/a"}],
            "api_calls": [{"method": "POST", "path": "/api/settings"}]}  # fmt: skip
    out = docs.generate("demo", _files(**{"apps/demo/codemap.json": json.dumps(code)}))
    text = {k: v.decode() for k, v in out.items()}
    pages = sorted(k for k in text if "/spec/PAGES/" in k)
    assert pages == ["apps/demo/spec/PAGES/01-a.md", "apps/demo/spec/PAGES/02-b.md",
                     "apps/demo/spec/PAGES/03-reports.md", "apps/demo/spec/PAGES/04-admin-settings.md"]  # fmt: skip
    a = text[pages[0]]
    assert "Kind: **recorded screen**" in a and "`button#go`" in a and "`span#total`" in a
    assert (
        'page title: "Demo - A"' in a and "`GET /api/x`" in a and "(../../api/get-api-x.json)" in a
    )
    assert "Step 2: click `button#go`: sent `GET /api/x 200`; went to `/b`." in a
    assert "`items`: 1 row(s)" in a and "The recording started here (step 1)." in a
    assert "Picture: unknown" in a  # no look/ in this package: said, with where it comes from
    assert "NOT a recorded screen" in text[pages[2]] and "Do not build this page." in text[pages[2]]
    assert "RECONSTRUCTED" in text[pages[3]] and "`POST /api/settings`" in text[pages[3]]
    arch = text["apps/demo/spec/ARCHITECTURE.md"]
    for route in ("/a", "/b", "/reports", "/admin/settings"):
        assert f"| `{route}` |" in arch, route
    assert "| `items` | `id` TEXT, `name` TEXT | `id` | 1 |" in arch
    assert "Unknown: the package has no `session.json`" in arch
    plan = text["apps/demo/spec/BUILD-PLAN.md"]
    assert plan.index("Screen `/a`") < plan.index("Screen `/b`") and "## Not built" in plan
    # without the code map: no code-only screen, and nothing depends on it
    assert len([k for k in docs.generate("demo", _files()) if "/PAGES/" in k]) == 3


def test_the_completeness_check_finds_a_screen_without_a_page_file(tmp_path):
    files = _files()
    made = docs.generate("demo", files)
    assert docs.gaps("demo", files, made) == []
    lost = {k: v for k, v in made.items() if not k.endswith("02-b.md")}
    lost.pop("apps/demo/spec/BUILD-PLAN.md")
    gaps = docs.gaps("demo", files, lost)
    assert "screen `/b` has no page file under `apps/demo/spec/PAGES/`" in gaps
    assert "`apps/demo/spec/BUILD-PLAN.md` is missing" in gaps
    # a desktop application has its own task and no documents
    desk = _files(**{"apps/demo/app.yaml": "stack: {desktop: {toolkit: WinForms}}\n"})
    assert docs.generate("demo", desk) == {} and docs.gaps("demo", desk) == []
    # the repository check reads the same files and says nothing about a complete tree
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    assert not [g for g in complete.problems(tmp_path, "demo") if "page file" in g]


def test_the_rules_text_is_one_function_for_the_task_and_the_constitution():
    have = {"apps/d/navigation.json", "apps/d/rules.json", "apps/d/look/screens/a.png"}
    text = docs.rules_text("apps/d", have, browser=False, seed_loaded=True)
    assert pkg._rules_text("apps/d", sorted(have), False, True) == text
    assert text.startswith("1. **Same technology and versions.**")
    for words in ("**Same look.**", "match each screen to its picture",
                  "**Same field rules and page behaviour.**", "**Assumptions are written down.**",
                  "**Done means every check holds.**"):  # fmt: skip
        assert words in text, words
    assert pkg._done_text("apps/d", sorted(have), False, True) == docs.done_text(
        "apps/d", have, False, True
    )


# =================================================================================================
# Two recorded applications, packaged
# =================================================================================================


def make_root(path: Path) -> Path:
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (path / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", path / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", path / "docs" / "factory-playbook.md")
    return path


def _zip(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


def _cookie_values(har_path: Path) -> set[str]:
    har = json.loads(har_path.read_text("utf-8"))
    out = set()
    for e in har["log"]["entries"]:
        for side in ("request", "response"):
            out |= {str(c.get("value") or "") for c in e[side].get("cookies") or []}
            for h in e[side].get("headers") or []:
                name = h["name"].lower()
                if name == "set-cookie":
                    out |= {v.split("=", 1)[1].split(";")[0] for v in h["value"].splitlines()
                            if "=" in v}  # fmt: skip
                elif name == "cookie":
                    out |= {p.split("=", 1)[1] for p in h["value"].split(";") if "=" in p}
    return {v.strip() for v in out if len(v.strip()) >= 8}


@pytest.fixture(scope="module", params=["desk", "permit"])
def built(request, tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from labctl import factory, recorder

    app = request.param
    root = make_root(tmp_path_factory.mktemp(app) / "repo")
    rec = root / "evidence" / app / f"{app}-run"
    run = desk_app.serve() if app == "desk" else form_app.serve()
    try:
        if app == "desk":
            recorder.record(f"{run.url}/tickets", rec, headless=True, script=desk_flow.run)
        else:
            recorder.record(f"{run.url}/apply", rec, headless=True, script=form_flow.run)
    finally:
        run.stop()
    factory.generate(root, app, force=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        path = pkg.build(root, app, root / "packages")
    texts = _zip(path)
    base = f"apps/{app}/spec/"
    new = {n: t for n, t in texts.items() if n.startswith(base) or n == f"apps/{app}/session.json"}
    typed = list(desk_flow.TYPED.values()) + list(desk_flow.DATA) if app == "desk" else list(
        form_flow.TYPED.values())  # fmt: skip
    return SimpleNamespace(app=app, root=root, rec=rec, texts=texts, new=new, base=base,
                           nav=json.loads(texts[f"apps/{app}/navigation.json"]),
                           rules=json.loads(texts.get(f"apps/{app}/rules.json") or "{}"),
                           typed=typed)  # fmt: skip


def test_one_page_file_per_screen_naming_its_selectors_rules_and_api_calls(built):
    pages = {n: t for n, t in built.new.items() if "/PAGES/" in n}
    screens = built.nav["screens"]
    assert len(pages) == len(screens) + len(built.nav.get("unrecorded_pages") or [])
    by_path = {}
    for text in pages.values():
        path = re.match(r"# \d\d\. `([^`]+)`", text).group(1)
        by_path[path] = text
    assert set(by_path) >= {s["path"] for s in screens}
    # recorded screens first, in recorded order
    order = [re.match(r"# \d\d\. `([^`]+)`", pages[n]).group(1) for n in sorted(pages)]
    assert order[0] == built.nav["entry"]["screen"]
    api = {n: json.loads(t) for n, t in built.texts.items() if f"apps/{built.app}/api/" in n}
    for s in screens:
        text = by_path[s["path"]]
        for e in s.get("elements") or []:
            assert f"`{e['selector']}`" in text, (s["path"], e["selector"])
        for v in s.get("subviews") or []:
            if v.get("selector"):
                assert f"`{v['selector']}`" in text
        rules = next((r for r in built.rules.get("screens") or [] if r["path"] == s["path"]), {})
        for f in rules.get("fields") or []:
            for r in f.get("rules") or []:
                assert f"| `{f['selector']}` ({f['label']}) | {r['rule']}" in text
                if r.get("message"):
                    assert r["message"] in text
        for b in rules.get("behaviour") or []:
            assert b["what"] in text
        for doc in api.values():
            for t in doc.get("triggered_by") or []:
                if isinstance(t, dict) and t.get("screen") == s["path"]:
                    assert f"`{doc['method']} {doc['path']}`" in text
                    assert f"(../../api/{doc['id']}.json)" in text
        for heading in ("## Purpose", "## Address", "## How you get here", "## Layout",
                        "## Elements", "## What each action did", "## Field rules",
                        "## API calls of this screen", "## Data shown", "## Seed rows",
                        "## Acceptance checks", "## Replay steps", "## Open questions"):  # fmt: skip
            assert heading in text, (s["path"], heading)
        if s.get("page"):
            assert f"`{s['page']}`" in text


def test_architecture_lists_every_route_and_table_and_the_plan_every_screen(built):
    arch = built.new[built.base + "ARCHITECTURE.md"]
    for s in built.nav["screens"]:
        assert f"| `{s['path']}` |" in arch, s["path"]
    from labctl.builder.package import _create_tables

    tables = [n for n, _ in _create_tables(built.texts[f"apps/{built.app}/db/schema.sql"])]
    assert tables
    for t in tables:
        assert f"| `{t}` |" in arch, t
    cat = json.loads(built.texts.get(f"apps/{built.app}/api/catalogue.json") or "{}")
    for e in cat.get("endpoints") or []:
        assert f"| `{e['method']} {e['path']}` |" in arch
    for heading in ("## Stack", "## Layers", "## Route table", "## API surface", "## Data model",
                    "## Sign-in, session and cookies", "## How it runs", "## Diagram"):  # fmt: skip
        assert heading in arch, heading
    plan = built.new[built.base + "BUILD-PLAN.md"]
    for s in built.nav["screens"]:
        assert f"Screen `{s['path']}`" in plan
    assert plan.count("Done when:") >= len(built.nav["screens"]) + 2


def test_constitution_and_task_share_their_rules_and_the_index_lists_every_new_file(built):
    task = built.texts["BUILD_TASK.md"]
    con = built.new[built.base + "CONSTITUTION.md"]

    def block(text: str, heading: str) -> str:
        return text.split(heading + "\n\n", 1)[1].split("\n\n", 1)[0]

    rules = block(task, "## Rules that are not negotiable")
    assert rules.startswith("1. **Same technology and versions.**")
    assert block(con, "## Rules") == rules
    done = task.split("The build is done when all of this holds:\n\n", 1)[1].split("\n\n", 1)[0]
    assert block(con, '## What "done" means') == done
    index = built.texts["INDEX.md"]
    manifest = json.loads(built.texts["MANIFEST.json"])
    listed = {f["path"]: f["purpose"] for f in manifest["files"]}
    for name in built.new:
        assert f"| `{name}` |" in index, name
        assert listed[name] != "Part of the package.", name
    # the reading order: task, constitution, architecture, plan, pages; brief as a reference
    reading = task.split("## Read in this order", 1)[1].split("\n## ", 1)[0]
    spots = [reading.index(x) for x in ("`BUILD_TASK.md`", "spec/CONSTITUTION.md`",
                                        "spec/ARCHITECTURE.md`", "spec/BUILD-PLAN.md`",
                                        "spec/PAGES/*.md`", f"`apps/{built.app}/BRIEF.md`")]  # fmt: skip
    assert spots == sorted(spots)
    assert f"`apps/{built.app}/spec/**`" in task
    assert not [g for g in manifest["completeness"]["gaps"] if "page file" in g]


def test_no_recorded_value_and_no_cookie_value_in_any_new_file(built):
    ctxs = ac.load(built.root, built.app)
    found = ac.leaks(built.root, built.app, ctxs, built.new)
    assert found == [], found
    new = "\n".join(built.new.values())
    for value in built.typed:
        assert value not in new, value
    observed = ac.observed_values(ctxs, ac.kept_values(built.root, built.app, ctxs))
    observed -= ac._seed_dates(built.root, built.app)
    assert not [v for v in observed if len(v) >= 6 and v in new]
    whole = "\n".join(built.texts.values())
    secrets = _cookie_values(built.rec / "recording.har")
    if built.app == "desk":
        assert secrets  # the session cookie was set and sent back
    assert not [s for s in secrets if s in whole]


def test_the_cookie_facts_reach_the_session_file_the_brief_and_the_architecture(built):
    if built.app != "desk":
        assert f"apps/{built.app}/session.json" not in built.texts  # nobody signed in
        assert "No response of the recording set a cookie" not in built.new[
            built.base + "ARCHITECTURE.md"]  # fmt: skip
        return
    ses = json.loads(built.texts["apps/desk/session.json"])
    (cookie,) = ses["cookies"]
    assert cookie["name"] == "desk_session" and cookie["session_cookie"]
    step = next(a["step"] for a in ses["sign_in"]["attempts"] if a["accepted"])
    assert cookie["set_by"] == [{"request": "POST /api/session", "status": 200, "step": step,
                                 "screen": "/login"}]  # fmt: skip
    assert cookie["cleared_by"][0]["request"] == "DELETE /api/session"
    a = cookie["attributes"]
    assert a["path"] == "/" and a["http_only"] and not a["secure"] and a["same_site"] == "Lax"
    assert a["lifetime"].startswith("session:") and a["domain"].startswith("not set")
    assert "value" not in json.dumps(cookie).replace("values", "")
    brief = built.texts["apps/desk/BRIEF.md"]
    assert "- Cookie `desk_session` (the session): set by `POST /api/session 200`" in brief
    assert "cleared by `DELETE /api/session 204`" in brief and "SameSite Lax" in brief
    assert "- Cookies: set each cookie listed above under the same name" in brief
    arch = built.new["apps/desk/spec/ARCHITECTURE.md"]
    assert (f"| `desk_session` (session) | `POST /api/session 200` (step {step}, `/login`) | / |"
            in arch)  # fmt: skip
    assert "Cookie values are never captured into the package and are not needed" in arch
    login = next(t for n, t in built.new.items() if n.endswith("01-login.md"))
    assert "This is the sign-in screen" in login and "`app_users`: 1 row(s)" in login
