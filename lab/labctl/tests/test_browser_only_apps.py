"""R07: browser storage, SQLite files and server databases are told apart.

A recording decides where an application's business data lives: `server` (state-changing requests
or records in responses), `browser` (the page changes with no request) or `unknown`. A browser-only
app has no tables, no database claim and no data controls; unknown stays unknown.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from test_devin_package import make_repo
from test_har import _entry, _har, _sample
from test_portal import repo  # noqa: F401 - the fixture
from test_portal_round4 import setup as portal_setup
from typer.testing import CliRunner

from labctl import brief as brief_mod
from labctl import conformance, dbservers, factory, har, lab
from labctl import model_build as mb
from labctl import repo as repo_mod
from labctl.bindings import for_automation
from labctl.builder import package as bpkg
from labctl.cli import app as cli_app
from labctl.models import App, Automation
from labctl.portal.app import create_app

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures" / "showcase-rec"
SHELL = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
APP_JS = (
    "function save(t){localStorage.setItem('todo-list', JSON.stringify(t));}"
    "function load(){return JSON.parse(localStorage.getItem('todo-list'));}"
)
PAGE_A = '<html><body><input id="new" class="new"><ul id="list"></ul></body></html>'
PAGE_B = (
    '<html><body><input id="new" class="new"><ul id="list"><li class="item">Buy milk</li></ul>'
    "</body></html>"
)


def _recording(tmp_path: Path, entries: list[dict], steps: list[dict] | None = None,
               pages: dict[str, str] | None = None, name: str = "rec") -> Path:  # fmt: skip
    """A recording folder: recording.har, steps.jsonl and dom/<n>.html."""
    folder = tmp_path / name
    folder.mkdir(parents=True)
    (folder / "recording.har").write_text(
        json.dumps({"log": {"version": "1.2", "entries": entries}}), encoding="utf-8"
    )
    if steps is not None:
        (folder / "dom").mkdir()
        lines = []
        for n, step in enumerate(steps, 1):
            body = (pages or {}).get(step.get("page", ""), "")
            if body:
                (folder / "dom" / f"{n:04d}.html").write_text(body, encoding="utf-8")
            line = {"seq": n, "ts": f"2026-11-17T10:00:{n:02d}.000Z", "url": "https://todo.example.test/",
                    "action": step["action"], "selector": step.get("selector"),
                    "dom": f"dom/{n:04d}.html" if body else None}  # fmt: skip
            if "storage" in step:
                line["storage"] = step["storage"]
            lines.append(json.dumps(line))
        (folder / "steps.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def _shell_entries() -> list[dict]:
    return [
        _entry("GET", "https://todo.example.test/", text=SHELL),
        _entry("GET", "https://todo.example.test/app.js", mime="application/javascript",
               text=APP_JS, t=1),
    ]  # fmt: skip


STEPS = [
    {"action": "goto", "page": "a"},
    {"action": "type", "selector": "input.new", "page": "a"},
    {"action": "click", "selector": "button.add", "page": "b"},
]
PAGES = {"a": PAGE_A, "b": PAGE_B}


def _data(path: Path) -> dict:
    return har.analyse("todo", path, path.parent).stack.data


# --- detection -----------------------------------------------------------------------------------


def test_server_when_a_state_changing_request_reaches_the_apps_origin(tmp_path):
    entries = _shell_entries() + [
        _entry("POST", "https://todo.example.test/api/tasks", mime="application/json",
               text='{"id": 1}', post={"mimeType": "application/json", "text": '{"title": "x"}'},
               t=2),
    ]  # fmt: skip
    data = _data(_recording(tmp_path, entries, STEPS, PAGES))
    assert data["kind"] == "server"
    assert any("POST /api/tasks" in line for line in data["evidence"])


def test_server_when_responses_carry_records_even_without_a_write(tmp_path):
    entries = _shell_entries() + [
        _entry("GET", "https://todo.example.test/api/tasks", mime="application/json",
               text='[{"id": 1, "title": "x", "done": false}]', t=2),
    ]  # fmt: skip
    entries[-1]["_resourceType"] = "fetch"
    data = _data(_recording(tmp_path, entries, STEPS, PAGES))
    assert data["kind"] == "server"
    assert any("responses carry records" in line for line in data["evidence"])


def test_browser_when_the_page_changes_with_no_request_and_storage_is_not_observed(tmp_path):
    data = _data(_recording(tmp_path, _shell_entries(), STEPS, PAGES))
    assert data["kind"] == "browser"
    assert data["storage"] == [] and data["keys"] == []  # nothing the recorder captured
    assert data["script_keys"] == ["localStorage:todo-list"]
    assert data["referenced"] == ["localStorage"]  # but a script mentions it
    text = " ".join(data["evidence"])
    assert "not observed" in text and "no state-changing request" in text
    assert "a script mention is not proof" in text


def test_a_telemetry_beacon_or_health_probe_does_not_make_an_app_server_backed(tmp_path):
    entries = _shell_entries() + [
        _entry("POST", "https://todo.example.test/analytics/collect", mime="application/json",
               text="{}", post={"mimeType": "application/json", "text": '{"e": 1}'}, t=2),
        _entry("GET", "https://todo.example.test/api/health", mime="application/json",
               text='{"ok": true}', t=3),
    ]  # fmt: skip
    for e in entries[2:]:
        e["_resourceType"] = "fetch"
    assert _data(_recording(tmp_path, entries, STEPS, PAGES))["kind"] == "browser"


def test_unknown_when_neither_can_be_shown(tmp_path):
    # Only pages were fetched and no step changed anything: nothing shows where data lives.
    bare = _har(tmp_path, [_entry("GET", "https://todo.example.test/", text=SHELL)])
    assert _data(bare)["kind"] == "unknown"
    still = _recording(tmp_path, _shell_entries(),
                       [{"action": "goto", "page": "a"}, {"action": "click", "page": "a"}],
                       PAGES, name="still")  # fmt: skip
    data = _data(still)
    assert data["kind"] == "unknown" and "not shown" in data["evidence"][0]


def test_storage_the_recorder_captures_keeps_keys_and_shapes_but_never_values(tmp_path):
    steps = [
        {"action": "goto", "page": "a"},
        {"action": "click", "selector": "button.add", "page": "b",
         "storage": {"localStorage": {
             "todo-list": json.dumps([{"title": "Buy milk", "done": False}]),
             "auth_token": "eyJhbGciOiJIUzI1NiJ9.SECRETVALUE"}}},
    ]  # fmt: skip
    folder = _recording(tmp_path, _shell_entries(), steps, PAGES)
    data = _data(folder)
    assert data["kind"] == "browser" and data["storage"] == ["localStorage"]
    assert data["keys"] == ["localStorage:todo-list", "localStorage:auth_token"]
    assert data["shape"]["localStorage:todo-list"] == {"[]": {"title": "string", "done": "boolean"}}
    assert data["shape"]["localStorage:auth_token"] == "(credential-like key: not described)"
    blob = json.dumps(data)
    assert "Buy milk" not in blob and "SECRETVALUE" not in blob


def test_a_declared_database_outranks_a_recording_without_writes(tmp_path):
    folder = _recording(tmp_path, _shell_entries(), STEPS, PAGES)
    recorded = mb.from_recording("todo", folder, tmp_path, None)
    assert recorded.stack.data["kind"] == "browser"
    from labctl.data import ddl as ddl_mod

    declared = ddl_mod.to_model("todo", "CREATE TABLE tasks (id integer primary key);", "ddl:t.sql")
    merged = mb.merge("todo", [declared, recorded])
    assert merged.stack.data["kind"] == "server"
    assert "declared by ddl:t.sql" in " ".join(merged.stack.data["evidence"])


def test_showcase_recording_is_server_backed():
    data = mb.from_recording("showcase", FIXTURE, ROOT, None).stack.data
    assert data["kind"] == "server" and data["storage"] == []


def test_no_table_is_invented_from_a_grid_when_the_data_is_in_the_browser(tmp_path):
    grid = (
        '<html><body><table id="gvTasks"><tr><th>Title</th><th>Done</th></tr>'
        "<tr><td>Buy milk</td><td>no</td></tr><tr><td>Walk dog</td><td>yes</td></tr></table>"
        '<input id="new"></body></html>'
    )
    pages = {"a": PAGE_A, "b": grid}
    model = mb.from_recording("todo", _recording(tmp_path, _shell_entries(), STEPS, pages),
                              tmp_path, None)  # fmt: skip
    assert model.stack.data["kind"] == "browser" and model.tables == []
    assert [r.key for r in model.routes] == ["GET /"]  # the page itself, nothing for data


# --- the brief -----------------------------------------------------------------------------------


@pytest.fixture
def browser_model(tmp_path):
    folder = _recording(tmp_path, _shell_entries(), STEPS, PAGES)
    return mb.merge("todo", [mb.from_recording("todo", folder, tmp_path, None)])


def _scoped(model, **kw):
    return brief_mod.scope("todo", "Todo list", model, **kw)


def test_a_browser_only_brief_has_no_tables_and_a_storage_section(browser_model):
    brief = _scoped(browser_model)
    text = brief_mod.render(brief, ROOT)
    assert brief.browser_only and brief.tables == {}
    assert "### Browser storage" in text and "**not observed**" in text
    assert "scripts reference `localStorage`" in text
    assert "What resets it" in text and "clearing the site's data" in text
    assert "### Tables" not in text and "Create them from" not in text
    assert "server outside the container" not in text and "proxy" not in text
    assert "There is no database" in text and "hosting health, not business-data health" in text
    assert "no database engine" not in text and "postgres assumed" not in " ".join(brief.gaps)
    assert any("keys were not observed" in g for g in brief.gaps)


def test_the_schema_is_written_without_a_table_and_app_yaml_says_where_data_lives(browser_model):
    brief = _scoped(browser_model)
    sql = brief_mod.schema(brief, ROOT)
    assert sql.startswith("--") and "CREATE" not in sql.upper() and "browser storage" in sql
    conf = brief_mod.app_yaml(brief, ROOT)
    assert conf["stack"]["data"]["kind"] == "browser"
    assert "db" not in conf["stack"]  # no engine is claimed
    assert conf["stack"]["data"]["referenced"] == ["localStorage"]
    assert App.model_validate({**conf, "run": {"web": {"port": 8000}}}).browser_only


def test_a_server_backed_brief_is_unchanged(tmp_path):
    model = mb.from_recording("showcase", FIXTURE, ROOT, None)
    brief = brief_mod.scope("showcase", "x", model)
    text = brief_mod.render(brief, ROOT)
    assert not brief.browser_only and "### Tables" in text and "Browser storage" not in text
    # nothing shows the engine and none was chosen: SQLite, said to be a default (item 4)
    assert "SQLite (a default, not evidence" in text and "`DATABASE_URL`" in text
    assert "data" not in brief_mod.app_yaml(brief, ROOT)["stack"]  # not written for a server app
    assert brief_mod.app_yaml(brief, ROOT)["stack"]["db"]["engine"] == "sqlite"
    named = brief_mod.render(brief_mod.scope("showcase", "x", model, "postgres"), ROOT)
    assert "server outside the container" in named and "a default" not in named


def test_unknown_stays_unknown_in_the_brief(tmp_path):
    bare = _har(tmp_path, [_entry("GET", "https://todo.example.test/", text=SHELL)])
    model = mb.merge("todo", [mb.from_recording("todo", bare, tmp_path, None)])
    brief = _scoped(model)
    assert brief.data_kind == "unknown" and brief.tables == {}
    assert any("not shown by the evidence" in g and "no table or database" in g for g in brief.gaps)
    assert brief_mod.app_yaml(brief, ROOT)["stack"]["data"]["kind"] == "unknown"


def test_override_to_browser_drops_server_tables_and_says_so(tmp_path):
    model = mb.merge("showcase", [mb.from_automations("showcase", [_sample()]),
                                  mb.from_recording("showcase", FIXTURE, ROOT, None)])  # fmt: skip
    assert brief_mod.scope("showcase", "x", model).tables  # the evidence does show tables
    brief = brief_mod.scope("showcase", "x", model, data="browser")
    text = brief_mod.render(brief, ROOT)
    assert brief.browser_only and brief.tables == {}
    assert "Overridden by a person" in text and "--data browser" in text
    assert any("overrides the evidence" in g for g in brief.gaps)
    assert brief_mod.app_yaml(brief, ROOT)["stack"]["data"]["overridden"] is True


def test_override_to_server_over_a_browser_finding(browser_model):
    brief = _scoped(browser_model, data="server")
    assert brief.data_kind == "server" and not brief.browser_only
    assert brief.data["overridden"] and "the recording showed: browser" in brief.data["evidence"][0]
    assert any("--data server overrides" in g for g in brief.gaps)


def test_the_cli_refuses_an_unknown_data_kind():
    result = CliRunner().invoke(cli_app, ["brief", "todo", "--data", "cloud"])
    assert result.exit_code == 2 and "browser or server" in result.output


# --- factory, package, validate and the lab without a schema table ---------------------------------


FLOW = {
    "id": "todo-basics", "app": "todo", "requested_by": "test",
    "steps": [{"screen": "Tasks", "path": "/", "do": "add a task"}],
}  # fmt: skip


@pytest.fixture
def todo_repo(tmp_path):
    root = make_repo(tmp_path / "repo", app="seed")
    shutil.rmtree(root / "apps" / "seed")
    (root / "flows").mkdir()
    (root / "flows" / "todo-basics.yaml").write_text(yaml.safe_dump(FLOW), encoding="utf-8")
    _recording(root / "evidence" / "todo", _shell_entries(), STEPS, PAGES, name="rec")
    return root


def test_factory_package_validate_and_lab_work_without_a_table(todo_repo):
    done = factory.generate(todo_repo, "todo", "Todo list")
    assert done.brief.browser_only
    folder = todo_repo / "apps" / "todo"
    assert (folder / "db" / "schema.sql").read_text(encoding="utf-8").startswith("--")
    conf = yaml.safe_load((folder / "app.yaml").read_text(encoding="utf-8"))
    conf["lab"] = {"port": 8730}
    conf["run"] = {"web": {"port": 8000}}
    (folder / "app.yaml").write_text(yaml.safe_dump(conf), encoding="utf-8")
    # validate: every contract file, the new app.yaml and model.yaml included
    files = [folder / "app.yaml", folder / "model.yaml"]
    assert [p for f in files for p in repo_mod.load(f)[1]] == []
    # the lab runs it with no database container
    app = repo_mod.load(folder / "app.yaml", "app")[0]
    assert app.browser_only and dbservers.app_choice(app) == "sqlite"
    services = lab.compose(todo_repo, "todo")["services"]
    assert set(services) == {"agent", "app"}  # no db service
    # the build package carries the schema and the task says there is no database
    package = bpkg.build(todo_repo, "todo", todo_repo / "out")
    text = bpkg.prompt(todo_repo, "todo", None)
    assert package.is_file()
    assert "There is no server database" in text
    assert "`localStorage` is referenced by the page's scripts but not observed" in text
    assert "the absence of server writes is not evidence of browser storage" in text
    assert "hosting health, not business-data health" in text
    assert "Database objects: none" in text
    assert "docker run -d --name twinlab-todo-app" in text and "POSTGRES" not in text
    assert "reads its database address" not in text and "Only the **tables and data**" not in text


def test_package_task_for_a_database_app_keeps_its_database_wording(tmp_path):
    root = make_repo(tmp_path / "repo")
    text = bpkg.prompt(root, "demo", None)
    assert "Only the **tables and data**" in text and "Database objects: create tables" in text
    assert "no server database" not in text.lower()


def test_package_task_names_the_keys_when_they_were_observed(tmp_path):
    root = make_repo(tmp_path / "repo")
    (root / "apps" / "demo" / "app.yaml").write_text(
        "id: demo\ncopies:\n  ref: sha256:0123456789abcdef\nstack:\n  data:\n    kind: browser\n"
        "    storage: [localStorage]\n    keys: ['localStorage:todo-list']\n"
        "run:\n  web:\n    port: 8000\n",
        encoding="utf-8",
    )
    text = bpkg.prompt(root, "demo", None)
    assert "`localStorage` (observed)" in text and "`localStorage:todo-list`" in text


# --- the portal ----------------------------------------------------------------------------------


def _browser_app(repo_root: Path, app_id: str = "showcase") -> None:
    path = repo_root / "apps" / app_id / "app.yaml"
    conf = yaml.safe_load(path.read_text(encoding="utf-8"))
    conf["stack"] = {"data": {"kind": "browser", "referenced": ["localStorage"]}}
    conf["lab"] = {"port": 8720, "credentials": {"password": "pw"}}
    path.write_text(yaml.safe_dump(conf), encoding="utf-8")


def test_portal_says_not_applicable_for_a_browser_only_app(repo, monkeypatch):  # noqa: F811
    fake = portal_setup(repo, monkeypatch)
    _browser_app(repo)
    monkeypatch.setattr(
        lab, "status",
        lambda root, app_id: lab.Status(app_id, True, {"app": "running"}, {"app": "ok", "db": "ok"}),
    )  # fmt: skip
    client = TestClient(create_app(repo))
    page = client.get("/apps/showcase").text
    assert 'id="data-na"' in page and "not applicable" in page.lower()
    assert "browser (transient or unknown persistence)" in page or "no browser storage" in page
    assert "Business data:" in page and "not evidence of localStorage" in page
    assert "hosting: ok" in page and "business data: no server database" in page
    assert "db ok" not in page
    for offered in ("Generate and load", "Reset to baseline", "Named data packs", "Your own data",
                    "Use this database", "reset data first"):  # fmt: skip
        assert offered not in page, offered
    assert 'id="conditions"' in page and "Web conditions" in page
    # the controls are refused too, even if somebody posts to them
    for url in ("/apps/showcase/data/reset", "/apps/showcase/data/baseline"):
        r = client.post(url)
        assert "Not applicable" in r.text, url
    r = client.post("/apps/showcase/packs/save", data={"name": "x"})
    assert "Not applicable" in r.text and fake.loaded == []
    assert "Not applicable" in client.get("/apps/showcase/data/template").text


def test_portal_session_page_has_no_row_change_verdict_for_a_browser_only_app(repo, monkeypatch):  # noqa: F811
    from test_portal_round4 import SESSION

    portal_setup(repo, monkeypatch)
    _browser_app(repo)
    page = TestClient(create_app(repo)).get(f"/apps/showcase/sessions/{SESSION}").text
    assert "not applicable" in page and "Rows changed" not in page and "Tables used" not in page


def test_portal_keeps_the_controls_of_a_database_backed_app(repo, monkeypatch):  # noqa: F811
    portal_setup(repo, monkeypatch)
    monkeypatch.setattr(
        lab, "status",
        lambda root, app_id: lab.Status(app_id, True, {"app": "running"}, {"app": "ok", "db": "ok"}),
    )  # fmt: skip
    page = TestClient(create_app(repo)).get("/apps/showcase").text
    for offered in ("Generate and load", "Reset to baseline", "Named data packs", "Your own data",
                    "Use this database", "reset data first"):  # fmt: skip
        assert offered in page, offered
    assert 'id="data-na"' not in page and "app ok, db ok" in page


def test_portal_hides_database_only_conditions_for_a_browser_app(repo, monkeypatch):  # noqa: F811
    portal_setup(repo, monkeypatch)
    cond = repo / "conditions" / "slow-db-only.yaml"
    cond.write_text(yaml.safe_dump({
        "id": "slow-db-only", "description": "slow database", "calibration": "estimate",
        "targets": [{"app": "showcase", "db": {"latency_ms": 500}}],
    }), encoding="utf-8")  # fmt: skip
    client = TestClient(create_app(repo))
    assert 'value="slow-db-only"' in client.get("/apps/showcase").text  # a database app: offered
    _browser_app(repo)
    page = client.get("/apps/showcase").text
    assert 'value="slow-db-only"' not in page and "slow-db-only" in page.split("Not offered:")[1]
    assert 'value="peak-hour"' in page  # it slows requests too: its web part still applies
    r = client.post("/apps/showcase/condition", data={"condition_id": "slow-db-only"})
    assert "Not applicable" in r.text


# --- conformance ---------------------------------------------------------------------------------


def _auto(touches: list[dict]) -> Automation:
    return Automation.model_validate(
        {"id": "bot", "name": "bot", "type": "uipath", "surface": "combo",
         "source": {"path": "."}, "touches": touches}
    )  # fmt: skip


def test_conformance_claims_no_database_coverage_for_a_browser_only_app(repo, monkeypatch):  # noqa: F811
    portal_setup(repo, monkeypatch)
    _browser_app(repo)
    bot = _auto([{"app": "showcase", "via": "db",
                  "tables": [{"table": "cases", "columns": ["id"], "access": "read"}]}])  # fmt: skip
    bindings = for_automation(repo, bot)
    assert bindings.apps["showcase"].db_engine == "browser" and bindings.apps["showcase"].db is None
    report = conformance.Report("bot")
    conformance._check_tables(bot, bindings, report, {}, repo)
    (item,) = report.items
    assert item.kind == "table" and item.status == "failed"
    assert "keeps its business data in the browser" in item.detail
    assert report.counts() == {"failed": 1}  # nothing is reported "ok": no coverage is claimed


def test_a_bot_that_only_uses_the_web_is_not_failed_for_lacking_tables(repo, monkeypatch):  # noqa: F811
    portal_setup(repo, monkeypatch)
    _browser_app(repo)
    web = _auto([{"app": "showcase", "via": "web", "screens": [{"name": "Tasks", "path": "/"}]}])
    report = conformance.Report("bot")
    conformance._check_tables(web, for_automation(repo, web), report, {}, repo)
    assert report.items == []


def test_the_fingerprint_names_the_browser_not_a_database(repo, monkeypatch):  # noqa: F811
    portal_setup(repo, monkeypatch)
    before = conformance.app_facts(repo, "showcase")["database"]
    _browser_app(repo)
    after = conformance.app_facts(repo, "showcase")["database"]
    assert before == "postgres" and after == "browser storage (no database)"


# --- where the data lives: the five findings (trial P1) --------------------------------------------


def _persistence(data: dict) -> str:
    return data["persistence"]


def test_five_data_location_findings_are_told_apart(tmp_path):
    # 1. transient DOM state: the page changed, nothing shows it persists.
    transient = _data(_recording(tmp_path, _shell_entries(), STEPS, PAGES, name="t"))
    assert transient["kind"] == "browser" and _persistence(transient) == "transient"
    assert transient["label"] == "browser (transient or unknown persistence)"
    assert "not evidence of localStorage" in " ".join(transient["evidence"])
    # 2. navigation / form state: values travel in the address.
    nav_entries = [_entry("GET", "https://todo.example.test/form", text=SHELL),
                   _entry("GET", "https://todo.example.test/result?q=1&name=x", text=SHELL, t=1)]  # fmt: skip
    nav = _data(_recording(tmp_path, nav_entries,
                           [{"action": "goto", "page": "a"}, {"action": "click", "page": "a"}],
                           PAGES, name="n"))  # fmt: skip
    assert nav["kind"] == "browser" and _persistence(nav) == "navigation"
    assert "query fields: q, name" in " ".join(nav["evidence"])
    assert "navigation" in nav["label"]
    # 3. observed browser storage: keys seen changing.
    stored = _data(_recording(tmp_path, _shell_entries(), [
        {"action": "goto", "page": "a"},
        {"action": "click", "page": "b", "storage": {"localStorage": {"todo-list": "[]"}}},
    ], PAGES, name="s"))  # fmt: skip
    assert _persistence(stored) == "storage" and stored["label"].startswith("browser storage")
    # 4. observed server writes.
    entries = _shell_entries() + [
        _entry("POST", "https://todo.example.test/api/tasks", mime="application/json",
               text='{"id": 1}', post={"mimeType": "application/json", "text": '{"t": "x"}'}, t=2)]  # fmt: skip
    server = _data(_recording(tmp_path, entries, STEPS, PAGES, name="w"))
    assert server["kind"] == "server" and _persistence(server) == "server"
    # 5. unknown persistence.
    bare = _data(_har(tmp_path, [_entry("GET", "https://todo.example.test/", text=SHELL)]))
    assert bare["kind"] == "unknown" and _persistence(bare) == "unknown"
    assert bare["label"] == "unknown persistence"


def test_a_script_mention_is_never_storage_evidence(tmp_path):
    data = _data(_recording(tmp_path, _shell_entries(), STEPS, PAGES))
    assert data["referenced"] == ["localStorage"] and data["storage"] == []
    assert _persistence(data) == "transient"  # not "storage"


def test_the_brief_and_task_do_not_claim_local_storage_without_observation(browser_model):
    brief = _scoped(browser_model)
    text = brief_mod.render(brief, ROOT)
    assert "does not show that the data persists" in text
    assert "absence of server writes is not evidence of browser storage" in text
    assert "keeps its business data **in browser storage**" not in text
    assert any("persistence is not shown" in g for g in brief.gaps)
    conf = brief_mod.app_yaml(brief, ROOT)
    assert conf["stack"]["data"]["persistence"] == "transient"
    task = bpkg._browser_database_text(conf, "apps/todo")
    assert "nothing shows that the data persists" in task and "not observed" in task


def test_the_brief_says_storage_was_observed_only_when_it_was(tmp_path):
    steps = [{"action": "goto", "page": "a"},
             {"action": "click", "page": "b", "storage": {"localStorage": {"todo-list": "[]"}}}]  # fmt: skip
    model = mb.merge("todo", [mb.from_recording(
        "todo", _recording(tmp_path, _shell_entries(), steps, PAGES), tmp_path, None)])  # fmt: skip
    text = brief_mod.render(_scoped(model), ROOT)
    assert "keeps its business data **in browser storage**, observed in the recording" in text
