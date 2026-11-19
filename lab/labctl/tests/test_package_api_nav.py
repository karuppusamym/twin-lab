"""What a build package carries beyond the screens: the API catalogue with synthetic examples,
the navigation map, the actions of each screen, sign-in and session, the scope without a flow,
the task text, the index and the completeness check.

Unit tests of the new modules first (no browser). Then the help-desk fixture
(fixtures/apidesk: pages filled by a JSON API, a modal dialog, in-page tabs, an iframe, a popup
window) is recorded twice with the real recorder: a working session, taken through
`labctl evidence build-all`, and a session that only looks around, taken through `labctl brief`
with no flow and no automation at all.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

HERE = Path(__file__).parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE / "fixtures" / "apidesk"))

import desk_app  # noqa: E402
import desk_flow  # noqa: E402

from labctl import apicatalog as ac  # noqa: E402
from labctl import contract, navmap, signin  # noqa: E402
from labctl.builder import complete  # noqa: E402
from labctl.builder import package as pkg  # noqa: E402

# =================================================================================================
# Units
# =================================================================================================


def _kept(**codes) -> ac.Kept:
    return ac.Kept(
        options={"OPEN", "CLOSED", "newest"}, codes={k: set(v) for k, v in codes.items()}
    )


def test_schema_keeps_keys_types_codes_and_identifier_patterns_never_a_value():
    kept = _kept(status=["OPEN", "CLOSED"])
    ids = {("TCK", "-", 4)}
    samples = [
        {"id": "TCK-1001", "title": "Broken lamp in hall", "status": "OPEN", "n": 3, "at": "2026-01-02"},
        {"id": "TCK-1002", "title": "A A", "status": "CLOSED", "n": 12, "at": "2026-02-03",
         "mail": "x.y@fixture.test"},
    ]  # fmt: skip
    s = ac.schema_of(samples, kept, ids, shapes={"A A"})
    props = s["properties"]
    assert s["required"] == ["id", "title", "status", "n", "at"]
    assert props["id"]["pattern"] == r"^TCK\-\d{4}$"
    assert props["status"] == {"type": "string", "enum": ["CLOSED", "OPEN"]}
    assert props["at"]["format"] == "date" and props["mail"]["format"] == "email"
    assert "x-source" in props["title"] and "x-length-seen" not in props["title"]
    assert props["n"]["type"] == "integer" and "minimum" not in props["n"]
    assert "Broken" not in json.dumps(s) and "fixture.test" not in json.dumps(s)
    # an error answer is the application's own message
    err = ac.schema_of([{"error": "Check the fields."}], kept, ids, set(), error=True)
    assert err["properties"]["error"]["x-kind"].startswith("a message of the application")
    # a key that is itself a value is not a property name
    dyn = ac.schema_of([{"EUR9": 1.5, "USD7": 2.5}], kept, ids, set())
    assert "additionalProperties" in dyn and not dyn["properties"]


def test_synthetic_text_is_stable_and_keeps_only_codes():
    world = ac.World({}, _kept(status=["OPEN", "CLOSED"]), avoid=set(), id_keys={})
    a = world.text("Jane Example")
    assert a == world.text("Jane Example") and "Jane" not in a and len(a.split()) == 2
    line = world.text("Status OPEN to CLOSED")
    assert line.split()[1] == "OPEN" and line.split()[3] == "CLOSED"
    assert "Status" not in line and " to " not in line
    assert world.text("2026-03-04") != "2026-03-04"
    assert world.text("jane.x@corp.example").endswith("@example.test")
    assert world.number(48213) != 48213 and len(str(world.number(48213))) == 5


def test_a_seeded_record_gives_its_values_to_every_example():
    seed = {"tickets": [{"id": "TCK-1001", "title": "Avery Kestrel", "status": "OPEN"}]}
    world = ac.World(seed, _kept(), set(), {"tickets": "id"})
    rec = world.record("tickets", "TCK-1001")
    assert rec == {"id": "TCK-1001", "title": "Avery Kestrel", "status": "OPEN"}
    assert world.record("tickets", "TCK-1001") is rec  # one record, whoever asks
    assert world.record("tickets", "TCK-1099") == {}  # made in the recording: filled by the calls


def test_password_hash_verifies_and_is_the_same_on_every_run():
    h = signin.password_hash("desk", "saffron.cobalt", "Synthetic-pass-2")
    assert h == signin.password_hash("desk", "saffron.cobalt", "Synthetic-pass-2")
    assert h.startswith("pbkdf2_sha256$100000$") and "Synthetic" not in h
    assert signin.verify(h, "Synthetic-pass-2") and not signin.verify(h, "synthetic-pass-2")
    assert not signin.verify("nonsense", "x")


def test_users_table_takes_the_profile_fields_and_never_a_credential():
    found = {"whoami": [{"request": "GET /api/session", "fields": [
        "user.username", "user.displayName", "user.role", "token", "user.password"]}]}  # fmt: skip
    cols = signin.profile_columns(found, {})
    assert cols == ["display_name", "role"]
    sql = signin.schema_sql(cols)
    assert "CREATE TABLE app_users" in sql and "CREATE TABLE app_sessions" in sql
    assert "display_name VARCHAR(200)" in sql and "password_hash VARCHAR(200) NOT NULL" in sql
    import sqlite3

    sqlite3.connect(":memory:").executescript(sql)


def test_selectors_match_elements_and_the_step_locators_pick_among_several():
    from labctl.behaviour import dom as domlib

    tree = domlib.parse_html(
        '<nav><a class="nav__item" data-testid="nav-start" href="/s">Overview</a>'
        '<a class="nav__item" data-testid="nav-find" href="/f">Find</a></nav>'
        '<form id="f"><input name="q"><button id="go" type="submit">Go</button></form>'
        '<dialog id="d" open><button class="x y">Close</button></dialog>'
    )
    nodes = {navmap.canon(n): n for n in domlib._walk(tree)}
    assert {'a[data-testid="nav-start"]', 'input[name="q"]', "button#go", "button.x"} <= set(nodes)
    assert navmap._matches("a.nav__item", nodes['a[data-testid="nav-find"]'])
    assert navmap._matches("form#f button#go", nodes["button#go"])
    assert not navmap._matches("button#go", nodes["button.x"])
    assert navmap._matches('input[name="q"]', nodes['input[name="q"]'])
    cands = [{"_node": nodes['a[data-testid="nav-start"]'], "label": "Overview"},
             {"_node": nodes['a[data-testid="nav-find"]'], "label": "Find"}]  # fmt: skip
    hit = navmap._pick(cands, [{"kind": "testid", "attr": "data-testid", "value": "nav-find"}])
    assert hit["label"] == "Find"
    assert (
        navmap._pick(cands, [{"kind": "role", "role": "link", "name": "Find"}])["label"] == "Find"
    )
    assert navmap._pick(cands, [])["label"] == "Overview"
    assert navmap._inside(nodes["button.x"]) == ["dialog `dialog#d`"]
    assert navmap._kind(nodes["button#go"]) == "button (submit)"
    assert navmap._href(nodes['a[data-testid="nav-find"]']) == "/f"
    states = navmap._subview_states(tree)
    assert states == {"dialog#d": ("dialog", True)}


def test_open_questions_agree_with_the_actions_list():
    text = ("- button#help on /a was never pressed in any recording: what it does is unknown. "
            "Record it, or build it to show it is not available in the demo\n"
            "- button#cancel on /a was never pressed in any recording: what it does is unknown. "
            "Record it, or build it to show it is not available in the demo\n- other")  # fmt: skip
    nav = {"screens": [{"elements": [
        {"selector": "iframe#x >>> button#help", "used": [{"step": 3}]},
        {"selector": "button#cancel", "used": "seen, not used"}]}]}  # fmt: skip
    out = contract._never_pressed(text, nav)
    assert "button#help" not in out and "- other" in out
    assert "button#cancel on /a" in out and "rendered and does nothing" in out
    assert "build it to show" not in out


def _tree(root: Path, nav=None, cat=None, endpoint=None, seed=None, steps=None, values=None):
    app = root / "apps" / "demo"
    (app / "db").mkdir(parents=True)
    (app / "db" / "schema.sql").write_text(
        "CREATE TABLE cases (id TEXT);\nCREATE TABLE app_sessions (token TEXT);\n", "utf-8")  # fmt: skip
    if seed is not None:
        (app / "db" / "seed.sql").write_text("-- seed\n", "utf-8")
        (app / "db" / "seed.json").write_text(json.dumps(seed), "utf-8")
    if nav is not None:
        (app / "navigation.json").write_text(json.dumps(nav), "utf-8")
    if cat is not None:
        (app / "api").mkdir()
        (app / "api" / "catalogue.json").write_text(json.dumps(cat), "utf-8")
        if endpoint is not None:
            (app / "api" / "get-x.json").write_text(json.dumps(endpoint), "utf-8")
    if steps is not None:
        (app / "steps").mkdir()
        (app / "steps" / "rec.json").write_text(json.dumps({"steps": steps}), "utf-8")
        if values is not None:
            (app / "steps" / "rec.values.yaml").write_text(
                yaml.safe_dump({"steps": values}), "utf-8"
            )
    return root


def test_completeness_lists_every_kind_of_gap(tmp_path):
    nav = {"screens": [
        {"path": "/a", "in_scope": True, "page": "apps/demo/pages/a.html", "elements": []},
        {"path": "/b", "in_scope": True, "elements": [{"selector": "a#x"}]},
        {"path": "/f", "in_scope": True, "page": "p", "kind": "shown in a frame", "elements": []},
        {"path": "/out", "in_scope": False, "elements": []}],
        "undefined_targets": ["/a: a#gone -> /nowhere"]}  # fmt: skip
    cat = {"endpoints": [{"method": "GET", "path": "/x", "file": "api/get-x.json"},
                         {"method": "GET", "path": "/y", "file": "api/get-y.json"}]}  # fmt: skip
    endpoint = {"request": {"content_type": "application/json"},
                "responses": {"200": {"content_type": "application/json"},
                              "204": {"body": "none"}}}  # fmt: skip
    steps = [{"n": 1, "action": "goto"}, {"n": 2, "action": "type"}, {"n": 3, "action": "type"}]
    root = _tree(tmp_path, nav, cat, endpoint, seed={"cases": []}, steps=steps,
                 values=[{"n": 2, "value": "x"}])  # fmt: skip
    gaps = complete.problems(root, "demo")
    text = "\n".join(gaps)
    assert "screen `/a` has no actions list" in text and "screen `/b` has no page snapshot" in text
    assert "`/f`" not in text and "`/out`" not in text
    assert "navigation target undefined: /a: a#gone -> /nowhere" in text
    assert "endpoint `GET /x`: the request has no schema" in text
    assert "endpoint `GET /x` answer 200: no schema" in text and "answer 204" not in text
    assert "endpoint `GET /y`: its file api/get-y.json is missing" in text
    assert "table `cases` has no seed rows" in text and "app_sessions" not in text
    assert "typing step(s) 3 have no value" in text
    # nothing to say about a complete one
    good_nav = {"screens": [{"path": "/a", "in_scope": True, "page": "p",
                             "elements": [{"selector": "a#x"}]}], "undefined_targets": []}  # fmt: skip
    root2 = _tree(tmp_path / "ok", good_nav, seed={"cases": [{"id": "1"}]}, steps=steps,
                  values=[{"n": 2, "value": "x"}, {"n": 3, "value": "y"}])  # fmt: skip
    assert complete.problems(root2, "demo") == []
    # steps without a navigation map: said
    root3 = _tree(
        tmp_path / "nonav", seed={"cases": [{"id": "1"}]}, steps=[{"n": 1, "action": "goto"}]
    )
    assert any("navigation.json is missing" in g for g in complete.problems(root3, "demo"))


def test_every_packaged_file_has_a_purpose_and_the_index_says_the_gaps():
    doc = json.dumps({"method": "GET", "path": "/api/x"}).encode()
    assert "`GET /api/x`" in complete.purpose("apps/demo/api/get-api-x.json", "demo", doc)
    for rel in ("BUILD_TASK.md", "apps/demo/BRIEF.md", "apps/demo/navigation.json",
                "apps/demo/pages/a.html", "apps/demo/steps/r.values.yaml", "apps/demo/look/theme.json",
                "apps/demo/db/seed.sql", "flows/demo-r.yaml", "MANIFEST.json"):  # fmt: skip
        assert complete.purpose(rel, "demo") != "Part of the package.", rel
    text = complete.index_md(
        "demo", [("BUILD_TASK.md", "The task.")], ["table `x` has no seed rows"]
    )
    assert "| `BUILD_TASK.md` | The task. |" in text and "- table `x` has no seed rows" in text
    assert "No gap found" in complete.index_md("demo", [], [])


def test_a_strict_package_refuses_gaps_and_a_normal_one_warns(tmp_path):
    root = _tree(tmp_path, steps=[{"n": 1, "action": "type"}])
    (root / "apps" / "demo" / "BRIEF.md").write_text("# brief\n", "utf-8")
    (root / "docs").mkdir()
    shutil.copy(REPO / "AGENTS.md", root / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", root / "docs" / "factory-playbook.md")
    with pytest.raises(pkg.PackageError, match="not complete"):
        pkg.build(root, "demo", tmp_path / "out", strict=True)
    with pytest.warns(pkg.PackageWarning, match="gap"):
        path = pkg.build(root, "demo", tmp_path / "out")
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("MANIFEST.json"))
        task = zf.read("BUILD_TASK.md").decode()
        index = zf.read("INDEX.md").decode()
    assert manifest["completeness"]["complete"] is False and manifest["completeness"]["gaps"]
    assert all(f["purpose"] for f in manifest["files"])
    assert "## Known gaps in this package" in task and "navigation.json is missing" in task
    assert "## Completeness check" in index and "navigation.json is missing" in index


# =================================================================================================
# The help desk, recorded: a working session through build-all
# =================================================================================================


def make_root(path: Path) -> Path:
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (path / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", path / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", path / "docs" / "factory-playbook.md")
    return path


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    import warnings

    from labctl import multiapp, recorder

    root = make_root(tmp_path_factory.mktemp("apidesk") / "repo")
    rec = multiapp.recordings_dir(root) / "desk-run"
    run = desk_app.serve()
    seen = {}
    try:
        recorder.record(f"{run.url}/tickets", rec, headless=True,
                        script=lambda page: seen.update(desk_flow.run(page)))  # fmt: skip
    finally:
        run.stop()
    multiapp.build_all(root, rec, say=lambda m: None)
    path = multiapp.proposal_file(rec)
    data = yaml.safe_load(path.read_text("utf-8"))
    data["apps"][0]["id"] = "desk"
    path.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        summary = multiapp.build_all(root, rec, proposal=path, say=lambda m: None,
                                     package_dir=root / "packages")  # fmt: skip
    with zipfile.ZipFile(root / summary.apps[0].package) as zf:
        texts = {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}
    api = {Path(n).name: json.loads(t) for n, t in texts.items() if "/api/" in n}
    return SimpleNamespace(
        root=root, rec=rec, texts=texts, api=api, number=seen["number"],
        brief=texts["apps/desk/BRIEF.md"], task=texts["BUILD_TASK.md"],
        nav=json.loads(texts["apps/desk/navigation.json"]),
        seed=json.loads(texts["apps/desk/db/seed.json"]),
        values={r["n"]: r for r in yaml.safe_load(
            texts["apps/desk/steps/desk-run.values.yaml"])["steps"]})  # fmt: skip


def _value(desk, field: str, nth: int = -1) -> str:
    rows = [r["value"] for r in desk.values.values() if r.get("field") == field]
    return rows[nth]


def test_the_catalogue_lists_every_endpoint_the_pages_called(desk):
    cat = desk.api["catalogue.json"]
    assert {(e["method"], e["path"]) for e in cat["endpoints"]} == {
        ("GET", "/api/session"), ("POST", "/api/session"), ("DELETE", "/api/session"),
        ("GET", "/api/tickets"), ("POST", "/api/tickets"), ("GET", "/api/tickets/{id}"),
        ("PATCH", "/api/tickets/{id}"), ("DELETE", "/api/tickets/{id}"),
        ("GET", "/api/tickets/{id}/history")}  # fmt: skip
    for e in cat["endpoints"]:
        doc = desk.api[Path(e["file"]).name]
        assert f"apps/desk/{e['file']}" in desk.texts
        assert doc["method"] == e["method"] and doc["path"] == e["path"]
        assert doc["triggered_by"] and doc["calls"] and doc["responses"]
        assert all(isinstance(t["steps"], list) and t["steps"] for t in doc["triggered_by"])
    by = {(e["method"], e["path"]): e for e in cat["endpoints"]}
    # authorised by the cookie's NAME; the sign-in call by nothing
    assert by[("GET", "/api/tickets")]["auth"] == "cookie `desk_session`"
    assert by[("POST", "/api/session")]["auth"] == "none"
    assert by[("GET", "/api/tickets")]["query"] == ["page", "q", "sort", "status"]
    assert by[("POST", "/api/tickets")]["statuses"] == ["201", "422"]
    assert "click button#saveTicket on /tickets" in by[("POST", "/api/tickets")]["triggered_by"]
    assert any(
        t.startswith("page load of /tickets") for t in by[("GET", "/api/tickets")]["triggered_by"]
    )
    assert by[("PATCH", "/api/tickets/{id}")]["table"] == "tickets"


def test_request_and_answer_schemas_by_status_with_parameters_and_controls(desk):
    post = desk.api["post-api-tickets.json"]
    req = post["request"]
    assert req["content_type"] == "application/json"
    assert list(req["schema"]["properties"]) == ["title", "requester", "priority", "description"]
    assert req["schema"]["properties"]["title"]["control"] == "input#title"
    assert set(req["schema"]["properties"]["priority"]["enum"]) == {"HIGH", "NORMAL"}
    ok, bad = post["responses"]["201"], post["responses"]["422"]
    assert ok["schema"]["properties"]["id"]["pattern"] == r"^TCK\-\d{4}$"
    assert ok["schema"]["properties"]["status"]["enum"] == ["OPEN"]
    assert bad["count"] == 2 and set(bad["schema"]["properties"]) == {"error", "fields"}
    # the application's own messages, as the page shows them
    assert bad["example"]["error"] == "Check the marked fields."
    assert bad["example"]["fields"]["title"] == "Give the ticket a title."
    assert "such as name@example.test" in bad["example"]["fields"]["requester"]
    lst = desk.api["get-api-tickets.json"]
    q = lst["query"]
    assert q["q"]["control"] == "input#q" and "x-source" in q["q"]
    assert q["status"]["control"] == "select#statusFilter" and "OPEN" in q["status"]["enum"]
    assert set(q["sort"]["enum"]) == {"newest", "oldest", "title"}
    assert q["page"]["x-kind"] == "paging" and q["page"]["x-values-seen"] == [1, 2, 3]
    one = desk.api["get-api-tickets-id.json"]
    assert one["path_params"]["id"]["pattern"] == r"^TCK\-\d{4}$"
    # where the page showed the answer
    assert one["shown"]["title"] == ["h1#ticketTitle", "h1#printTitle"]
    assert lst["shown"]["items[].title"] == ['table#tickets column "Title"']
    # an answer the page never read has no schema, and the package says so
    session = desk.api["post-api-session.json"]["responses"]["200"]
    assert "schema" not in session and session["body"].startswith("not recorded")
    assert session["sets_cookie"] == ["desk_session"]
    # a nested list says which record it belongs to
    assert any("belongs to one `tickets` record" in n
               for n in desk.api["get-api-tickets-id-history.json"]["notes"])  # fmt: skip


def test_examples_are_one_consistent_synthetic_world(desk):
    title, mail = _value(desk, "input#title"), _value(desk, "input#requester")
    assignee = _value(desk, "input#assignee")
    post = desk.api["post-api-tickets.json"]
    assert [c["status"] for c in post["calls"]] == [422, 422, 201]
    made = post["calls"][2]
    assert made["request"] == {"title": title, "requester": mail, "priority": "HIGH",
                               "description": _value(desk, "textarea#description")}  # fmt: skip
    assert (
        post["calls"][0]["request"]["title"] == "" and post["request"]["example"] == made["request"]
    )
    new_id = made["response"]["id"]
    assert new_id == "TCK-1013" == desk.number and made["response"]["title"] == title
    # the seed stops before the record the recording made, and numbers on from there
    ids = [r["id"] for r in desk.seed["tickets"]]
    assert ids == [f"TCK-{n}" for n in range(1001, 1013)] and new_id not in ids
    # the later calls read, change and print the same record
    one = desk.api["get-api-tickets-id.json"]["calls"]
    assert one[0]["path"] == f"/api/tickets/{new_id}" and one[0]["response"]["title"] == title
    assert one[0]["response"]["status"] == "OPEN" and one[0]["response"]["assignee"] == ""
    patch = desk.api["patch-api-tickets-id.json"]["calls"]
    assert [c["status"] for c in patch] == [422, 200]
    assert patch[1]["request"] == {"status": "CLOSED", "assignee": assignee}
    assert patch[1]["response"]["status"] == "CLOSED" and patch[1]["response"]["title"] == title
    assert one[1]["response"]["status"] == "CLOSED" and one[1]["response"]["assignee"] == assignee
    history = desk.api["get-api-tickets-id-history.json"]["calls"]
    assert history[0]["response"] == {"items": []} and len(history[1]["response"]["items"]) == 1
    assert {"OPEN", "CLOSED"} <= set(history[1]["response"]["items"][0]["change"].split())
    # listed rows are the seed's rows, value for value
    seed = {r["id"]: r for r in desk.seed["tickets"]}
    lst = desk.api["get-api-tickets.json"]["calls"]
    for call in lst[:4]:
        for item in call["response"]["items"]:
            assert item["title"] == seed[item["id"]]["title"], item
            assert item["status"] == seed[item["id"]]["status"]
    search = next(c for c in lst if c["query"]["q"])
    assert search["query"]["q"] == _value(desk, "input#q", 0)
    assert all(search["query"]["q"] in i["title"] for i in search["response"]["items"])
    assert search["query"]["page"] == "1" and search["response"]["total"] == 4
    opened = next(c for c in lst if c["query"]["status"] == "OPEN")
    assert {i["status"] for i in opened["response"]["items"]} == {"OPEN"}
    # the deleted record is one of the seed, and the session shows the seeded user
    gone = desk.api["delete-api-tickets-id.json"]["calls"][0]
    assert gone["path"] == "/api/tickets/TCK-1002" and gone["status"] == 204
    user = desk.api["get-api-session.json"]["responses"]["200"]["example"]["user"]
    row = desk.seed["app_users"][0]
    assert user["username"] == row["username"] == _value(desk, "input#username")
    assert user["display_name"] == row["display_name"]
    sign_in = desk.api["post-api-session.json"]["calls"]
    assert [c["status"] for c in sign_in] == [401, 200] and sign_in[0]["request"]["password"] == ""


def test_no_observed_value_is_in_any_carried_file(desk):
    """Nothing typed, nothing the application held, no cookie value: in no file of the package."""
    ctxs = ac.load(desk.root, "desk")
    assert ac.leaks(desk.root, "desk", ctxs) == []
    observed = ac.observed_values(ctxs, ac.kept_values(desk.root, "desk", ctxs))
    observed -= ac._seed_dates(desk.root, "desk")  # a generated date may fall on a recorded day
    assert len(observed) > 20 and any("@fixture-mail.test" in v for v in observed)
    api_text = "\n".join(t for n, t in desk.texts.items() if "/api/" in n)
    assert not [v for v in observed if len(v) >= 8 and v in api_text]
    whole = "\n".join(desk.texts.values())
    for value in [*desk_flow.TYPED.values(), *desk_flow.DATA, "Dana"]:
        assert value not in whole, value
    har = json.loads((desk.rec / "recording.har").read_text("utf-8"))
    secrets = {c.get("value") for e in har["log"]["entries"] for side in ("request", "response")
               for c in e[side].get("cookies") or []}  # fmt: skip
    for e in har["log"]["entries"]:
        for h in e["response"].get("headers") or []:
            if h["name"].lower() == "set-cookie":
                secrets.add(h["value"].split("=", 1)[1].split(";")[0])
    secrets = {s for s in secrets if s and len(s) >= 8}
    assert secrets and not [s for s in secrets if s in whole]
    # the check itself finds a planted value, and names its shape only
    planted = {"x.json": json.dumps({"mail": "ines.calvo@fixture-mail.test"})}
    found = ac.leaks(desk.root, "desk", ctxs, planted)
    assert found == ["x.json: an observed value of shape `A.A@A-A.A`"]


def test_search_filter_sort_and_paging_are_described_from_the_answers(desk):
    facts = next(
        x for x in desk.api["catalogue.json"]["lists"] if x["endpoint"] == "GET /api/tickets"
    )
    assert facts["page_size"] == 5 and facts["answer_fields_beside_the_rows"] == [
        "page",
        "pages",
        "total",
    ]
    p = facts["parameters"]
    assert p["page"]["role"] == "paging" and p["q"]["role"] == "search text a person typed"
    assert "every row's `title` held it as a word" in p["q"]["observed"][0]
    assert p["status"]["observed"] == ['`OPEN` ("Open"): every row had `status` = `OPEN` '
                                       "(2 answer(s)): a filter"]  # fmt: skip
    sort = "\n".join(p["sort"]["observed"])
    assert '`title` ("Title"): rows ordered by `title`, lowest first' in sort
    assert '`newest` ("Newest first"): rows ordered by' in sort and "highest first" in sort
    assert "**Search, filter, sort and paging.**" in desk.brief
    assert "| `tickets.title` | `title` |" in desk.brief  # the field map


def test_the_navigation_map_has_every_way_and_every_sub_view(desk):
    nav = desk.nav
    assert nav["undefined_targets"] == [] and nav["entry"]["screen"] == "/login"
    screens = {s["path"]: s for s in nav["screens"]}
    assert set(screens) == {
        "/login",
        "/tickets",
        "/tickets/{id}",
        "/tickets/{id}/print",
        "/help/panel",
    }
    assert all(s["in_scope"] for s in screens.values())
    # the recorded moves; links on the pages and the code's own are there too, with their source
    assert {m["source"] for m in nav["transitions"]} >= {"recording", "page"}
    moves = {(m["from"], m["selector"], m["to"]) for m in nav["transitions"]
             if m["source"] == "recording"}  # fmt: skip
    assert moves == {
        ("/login", "input#password", "/tickets"),
        ("/tickets", "a.ticket-link", "/tickets/{id}"),
        ("/tickets/{id}", "a#backToList", "/tickets"),
        ("/tickets/{id}", "button#confirmDeleteYes", "/tickets"),
        ("/tickets", "button#signOut", "/login")}  # fmt: skip
    back = next(m for m in nav["transitions"] if m["selector"] == "a#backToList")
    assert back["by"] == "link" and back["text"] == "Back to the list"
    assert (
        nav["redirects"][0]["request"] == "GET /tickets" and nav["redirects"][0]["to"] == "/login"
    )
    assert "before signing in" in nav["redirects"][0]["when"]
    subs = {v.get("selector") or v["kind"]: v for v in screens["/tickets"]["subviews"]}
    dialog = subs["dialog#ticketDialog"]
    assert dialog["kind"] == "dialog" and dialog["title"] == "New ticket"
    assert dialog["first_seen"] == "closed" and "button#saveTicket" in dialog["contains"]
    assert [w["selector"] for w in dialog["opened_by"]] == ["button#newTicket"]
    assert [w["selector"] for w in dialog["closed_by"]] == ["button#saveTicket"]
    frame = subs["iframe#helpFrame"]
    assert frame["shows"] == "/help/panel" and frame["origin"] == "this application"
    assert frame["in_scope"] and screens["/help/panel"]["kind"] == "shown in a frame"
    item = {v.get("selector") or v["kind"]: v for v in screens["/tickets/{id}"]["subviews"]}
    panel = item["section#panelHistory"]
    assert panel["kind"] == "tab panel" and panel["tab"] == "button#tabHistory"
    assert panel["tab_text"] == "History" and panel["first_seen"] == "closed"
    assert [w["selector"] for w in panel["opened_by"]] == ["button#tabHistory"]
    assert [w["selector"] for w in item["dialog#confirmDelete"]["closed_by"]] == [
        "button#confirmDeleteNo"
    ]
    popup = item["popup window or new tab"]
    assert (
        popup["shows"] == "/tickets/{id}/print"
        and popup["opened_by"]["selector"] == "button#printTicket"
    )
    assert popup["closed"]["after"] == "click button#closePrint"
    assert screens["/tickets/{id}/print"]["kind"] == "shown in a popup window or new tab"
    loose = {(x["selector"], x["target"], x["status"]) for s in nav["screens"]
             for x in s.get("links_seen_not_visited", [])}  # fmt: skip
    assert ("a#navReports", "/reports", "out of scope") in loose
    assert ("a#navTickets", "/tickets", "in scope") in loose
    assert ("iframe#helpFrame >>> a#helpGuide", "/help/guide", "out of scope") in loose
    for words in ("## Navigation map", "**From screen to screen**", "**Sub-views: dialogs",
                  "**Links seen, not visited.**", "opens `/tickets/{id}/print` in a popup window",
                  "must work the same way"):  # fmt: skip
        assert words in desk.brief, words


def test_every_interactive_element_is_listed_with_what_it_did(desk):
    screens = {s["path"]: s for s in desk.nav["screens"]}
    els = {e["selector"]: e for e in screens["/tickets"]["elements"]}
    assert list(els)[:3] == ["a#navTickets", "a#navReports", "button#signOut"]  # page order
    assert {"input#q", "select#statusFilter", "select#sortBy", "button#searchBtn", "button#newTicket",
            "a.ticket-link", "button#prevPage", "button#nextPage", "input#title", "input#requester",
            "select#priority", "textarea#description", "button#saveTicket", "button#cancelTicket",
            "iframe#helpFrame >>> button#helpToggle", "iframe#helpFrame >>> a#helpGuide"} <= set(els)  # fmt: skip
    assert els["button#cancelTicket"]["used"] == "seen, not used"
    assert els["button#cancelTicket"]["label"] == "Cancel"
    assert els["button#cancelTicket"]["inside"] == [
        "form `form#ticketForm`",
        "dialog `dialog#ticketDialog`",
    ]
    assert els["select#sortBy"]["type"] == "select" and els["select#sortBy"]["label"] == "Sort by"
    assert {"value": "title", "label": "Title"} in els["select#sortBy"]["options"]
    assert els["input#title"]["accepts"] == {"maxlength": "80"}
    assert (
        els["a.ticket-link"]["repeated"] == 5 and els["a.ticket-link"]["target"] == "/tickets/{id}"
    )
    saves = els["button#saveTicket"]["used"]
    assert [u["requests"][0] for u in saves] == ["POST /api/tickets 422", "POST /api/tickets 422",
                                                 "POST /api/tickets 201"]  # fmt: skip
    assert any("Give the ticket a title." in m for m in saves[0]["messages"])
    assert saves[2]["closes"] == ["dialog dialog#ticketDialog"]
    assert els["button#newTicket"]["used"][0]["opens"] == ["dialog dialog#ticketDialog"]
    typed = els["input#title"]["used"][0]
    assert typed["action"] == "type" and typed["replay_value"] == _value(desk, "input#title")
    rows = els["a.ticket-link"]["used"]
    assert [u["record"] for u in rows] == ["TCK-1013", "TCK-1002"]
    assert all(u["goes_to"] == "/tickets/{id}" for u in rows)
    assert els["iframe#helpFrame >>> button#helpToggle"]["used"][0]["action"] == "click"
    item = {e["selector"]: e for e in screens["/tickets/{id}"]["elements"]}
    assert item["button#tabHistory"]["type"] == "tab"
    assert item["button#printTicket"]["used"][0]["opens"] == [
        "popup window or new tab showing /tickets/{id}/print"]  # fmt: skip
    # the shared menu: one control, the same on every screen
    assert item["button#signOut"]["used"].startswith(
        "seen, not used on this screen; the same element"
    )
    assert item["a#navReports"]["used"] == "seen, not used"
    closer = screens["/tickets/{id}/print"]["elements"][0]
    assert closer["selector"] == "button#closePrint" and closer["used"][0]["closes"] == [
        "this window"
    ]
    assert "## Actions on each screen" in desk.brief
    assert "| `button#cancelTicket` | Cancel | button |" in desk.brief
    assert "button#helpToggle on /tickets was never pressed" not in desk.brief
    assert "build it to show it is not available" not in desk.brief


def test_sign_in_and_session_observed_and_stored_in_the_database(desk):
    import sqlite3

    part = desk.brief.split("## Sign-in and session", 1)[1].split("\n## ", 1)[0]
    for words in (
        "Sign-in screen: `/login`, fields `input#username`, `input#password` (the password)",
        "`POST /api/session` with JSON `username`, `password`",
        "with `password` empty: status `401`",
        'div#loginError: "Give your user name and your password."',
        "the person landed on `/tickets`",
        "set the cookie `desk_session` (its value is never kept)",
        "`GET /api/session` answered `user.username`, `user.display_name`, `user.role`",
        "sent `DELETE /api/session 204`, then `/login`",
        "(before signing in): `GET /tickets` answered `302`, redirect to `/login`",
        "(before signing in): `GET /api/session` answered `401`",
        "rows of `app_users` (`username`, `password_hash`, `display_name`, `role`)",
        "there is no separate `user` table",
        "A session survives a restart",
        "**Everything is stored in the backend.**",
    ):  # fmt: skip
        assert words in part, words
    assert (
        "accept any non-empty user name" not in desk.brief and "**Signing in.**" not in desk.brief
    )
    schema = desk.texts["apps/desk/db/schema.sql"]
    assert "CREATE TABLE app_users" in schema and "CREATE TABLE app_sessions" in schema
    assert 'CREATE TABLE "user"' not in schema and 'CREATE TABLE "session"' not in schema
    db = sqlite3.connect(":memory:")
    db.executescript(schema)
    db.executescript(desk.texts["apps/desk/db/seed.sql"])
    name, stored = db.execute("SELECT username, password_hash FROM app_users").fetchone()
    assert name == _value(desk, "input#username")
    assert signin.verify(stored, _value(desk, "input#password"))
    assert db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 12
    assert _value(desk, "input#password") not in desk.texts["apps/desk/db/seed.sql"]


def test_the_task_reads_as_one_instruction_and_the_index_lists_every_file(desk):
    task = desk.task
    order = ['## What to build, and what "done" means', "## Read in this order",
             "## Technology and versions", "## Rules that are not negotiable", "## Database",
             "## How to report assumptions", "## Known gaps in this package",
             "## Working from this package alone", "## 3. Build", "## 4. Check"]  # fmt: skip
    places = [task.index(h) for h in order]
    assert places == sorted(places)
    reading = task.split("## Read in this order", 1)[1].split("\n## ", 1)[0]
    names = ["BUILD_TASK.md", "AGENTS.md", "apps/desk/BRIEF.md", "apps/desk/pages/*.html",
             "apps/desk/look/", "apps/desk/navigation.json", "apps/desk/api/catalogue.json",
             "apps/desk/db/schema.sql", "apps/desk/steps/<recording>.json", "flows/*.yaml",
             "apps/desk/behaviours.json"]  # fmt: skip
    spots = [reading.index(f"`{n}`") for n in names]
    assert spots == sorted(spots)
    for words in ("**Same selectors and ids.**", "**Same literal texts.**",
                  "**Same addresses and API contract.**", "**Every navigation and every action works.**",
                  "**Data lives in the backend database.**", "**The seed is loaded at start.**",
                  "**Synthetic data only.**", "This is the acceptance run.",
                  "under **Assumptions**", "There is nobody to ask."):  # fmt: skip
        assert words in task, words
    assert "the answer's body was not recorded" in desk.brief  # said where the endpoint is
    manifest = json.loads(desk.texts["MANIFEST.json"])
    listed = {f["path"] for f in manifest["files"]}
    assert listed == set(desk.texts) - {"MANIFEST.json"}
    assert all(f["purpose"] and f["purpose"] != "Part of the package." for f in manifest["files"])
    gaps = manifest["completeness"]["gaps"]
    assert any("`POST /api/session` answer 200: no schema" in g for g in gaps)
    assert all(g in task for g in gaps)
    index = desk.texts["INDEX.md"]
    assert all(f"| `{n}` |" in index for n in desk.texts)
    scope = desk.brief.split("## Scope", 1)[1].split("\n## ", 1)[0]
    assert "**Every screen and every action recorded.**" in scope
    assert "1. `/login`: type `input#username`; click `button#signIn`" in scope


# =================================================================================================
# No flow, no automation: a session that only looks, through `labctl brief`
# =================================================================================================


@pytest.fixture(scope="module")
def looked(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    import warnings

    from labctl import factory, recorder

    root = make_root(tmp_path_factory.mktemp("looked") / "repo")
    rec = root / "evidence" / "look" / "browse"
    run = desk_app.serve()
    try:
        recorder.record(f"{run.url}/tickets", rec, headless=True, script=desk_flow.browse)
    finally:
        run.stop()
    done = factory.generate(root, "look", force=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        path = pkg.build(root, "look", root / "packages")
    with zipfile.ZipFile(path) as zf:
        texts = {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}
    return SimpleNamespace(root=root, done=done, texts=texts, brief=texts["apps/look/BRIEF.md"],
                           nav=json.loads(texts["apps/look/navigation.json"]))  # fmt: skip


def test_without_any_flow_the_scope_is_everything_recorded(looked):
    assert not list((looked.root / "flows").iterdir())  # nothing was written for it
    assert not [n for n in looked.texts if n.startswith("flows/")]
    assert looked.done.brief.needed_by == ["recorded-look-browse"]
    scope = looked.brief.split("## Scope", 1)[1].split("\n## ", 1)[0]
    assert "**Every screen and every action recorded.**" in scope
    assert "Recording `browse`" in scope and "In recorded order" in scope
    assert "click `button#newTicket`; click `button#cancelTicket`" in scope
    assert "Recorded but not in scope" not in scope
    paths = {s["path"] for s in looked.nav["screens"] if s["in_scope"]}
    assert paths == {"/login", "/tickets", "/tickets/{id}", "/tickets/{id}/print", "/help/panel"}
    for name in ("login", "tickets", "tickets-item", "tickets-print", "help-panel"):
        assert f"apps/look/pages/{name}.html" in looked.texts
    assert "apps/look/steps/browse.json" in looked.texts
    assert "apps/look/api/catalogue.json" in looked.texts
    assert "## Out of scope" in looked.brief
    out = looked.brief.split("## Out of scope", 1)[1].split("\n## ", 1)[0]
    assert "/help/panel" not in out and "Screens:" not in out


def test_without_any_flow_the_brief_is_still_complete(looked):
    brief, nav = looked.brief, looked.nav
    for heading in ("## Stack to pin", "### Screens", "### API routes", "### Tables",
                    "## What each recorded action did", "## API contract", "## Sign-in and session",
                    "## Navigation map", "## Actions on each screen", "## Acceptance checks"):  # fmt: skip
        assert heading in brief, heading
    screens = {s["path"]: s for s in nav["screens"]}
    dialog = next(
        v for v in screens["/tickets"]["subviews"] if v.get("selector") == "dialog#ticketDialog"
    )
    assert [w["selector"] for w in dialog["opened_by"]] == ["button#newTicket"]
    assert [w["selector"] for w in dialog["closed_by"]] == ["button#cancelTicket"]
    els = {e["selector"]: e for e in screens["/tickets"]["elements"]}
    assert els["button#saveTicket"]["used"] == "seen, not used"  # nothing was created
    assert isinstance(els["button#cancelTicket"]["used"], list)
    assert any(v["kind"].startswith("popup") for v in screens["/tickets/{id}"]["subviews"])
    cat = json.loads(looked.texts["apps/look/api/catalogue.json"])
    assert {(e["method"], e["path"]) for e in cat["endpoints"]} == {
        ("GET", "/api/session"), ("POST", "/api/session"), ("DELETE", "/api/session"),
        ("GET", "/api/tickets"), ("GET", "/api/tickets/{id}"), ("GET", "/api/tickets/{id}/history")}  # fmt: skip
    seed = json.loads(looked.texts["apps/look/db/seed.json"])
    assert len(seed["tickets"]) == 12 and len(seed["app_users"]) == 1
    task = looked.texts["BUILD_TASK.md"]
    assert "## Read in this order" in task and "apps/look/navigation.json" in task
    whole = "\n".join(looked.texts.values())
    for value in [desk_flow.TYPED["user"], desk_flow.TYPED["password"], *desk_flow.DATA]:
        assert value not in whole, value
    assert ac.leaks(looked.root, "look") == []


def test_no_recording_and_no_flow_is_still_refused(tmp_path):
    from labctl import factory

    root = make_root(tmp_path / "repo")
    with pytest.raises(factory.FactoryError, match="nothing to build"):
        factory.generate(root, "nothing")
    assert contract.implied_flows(root, "nothing") == []
    assert re.match(r"^labctl ", contract.RECORDED_BY)
