"""T-07 conformance, against a small app served in-process (no Docker needed)."""

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from labctl import conformance
from labctl import lab as lab_mod
from labctl.models import Automation

pytest.importorskip("playwright.sync_api")

PORT = 18611
LOGIN = """<html><body><form action="/home" method="get">
<input id="username" name="u"><input id="password" type="password" name="p">
<button id="btnLogin" type="submit">Sign in</button></form></body></html>"""
HOME = """<html><body><h1>Home</h1>
<select id="category"><option value="">Select</option><option value="1">A</option><option value="2">B</option></select>
<select id="sub" disabled><option value="">Select</option></select>
<button id="{button}">Save</button><div id="message">Saved</div>
<script>
document.getElementById('category').addEventListener('change', e => {{
  setTimeout(() => {{
    const s = document.getElementById('sub');
    s.innerHTML = '<option value="">Select</option><option value="x">X-' + e.target.value + '</option>';
    s.disabled = false;
  }}, 300);
}});
</script></body></html>"""


def make_app(button_id: str) -> FastAPI:
    api = FastAPI()

    @api.get("/login", response_class=HTMLResponse)
    def login():
        return LOGIN

    @api.get("/home", response_class=HTMLResponse)
    def home():
        return HOME.format(button=button_id)

    @api.post("/api/things/{thing_id}/notes")
    def notes(thing_id: str):
        return {"detail": "need a sign-in"}

    return api


@pytest.fixture
def served():
    started = []

    def start(button_id: str):
        server = uvicorn.Server(uvicorn.Config(make_app(button_id), port=PORT, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", PORT)) == 0:
                    break
            time.sleep(0.05)
        started.append((server, thread))

    yield start
    for server, thread in started:
        server.should_exit = True
        thread.join(5)


def make_repo(tmp_path: Path, uses_home: list[dict], routes: list[dict]) -> tuple[Path, Automation]:
    for folder in ("automations/bot", "apps/demo", "lab"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "apps/demo/app.yaml").write_text(
        yaml.safe_dump({
            "id": "demo", "name": "Demo", "copies": {"system": "Demo"}, "stack": {},
            "run": {"web": {"port": 8000}}, "lab": {"port": PORT, "db_port": 18612,
                                                    "credentials": {"username": "u1", "password": "p1"}},
        }),
        encoding="utf-8",
    )  # fmt: skip
    auto = Automation.model_validate({
        "id": "bot", "name": "Bot", "type": "uipath", "surface": "combo", "source": {"path": "."},
        "bindings": {"in_User": "{{demo.credentials.username}}", "in_Password": "{{demo.credentials.password}}"},
        "test_inputs": {"in_Id": "T-1", "in_Cat": "B"},
        "touches": [
            {"app": "demo", "via": "web", "screens": [
                {"name": "login", "path": "/login", "uses": [
                    {"action": "type", "selector": "input#username", "field": "in_User"},
                    {"action": "type", "selector": "input#password", "field": "in_Password"},
                    {"action": "click", "selector": "button#btnLogin"}]},
                {"name": "home", "path": "/home", "uses": uses_home}]},
        ],
    })  # fmt: skip
    if routes:
        auto.touches.append(Automation.model_validate({
            "id": "x", "name": "x", "type": "uipath", "surface": "combo", "source": {"path": "."},
            "touches": [{"app": "demo", "via": "api", "routes": routes}]}).touches[0])  # fmt: skip
    return tmp_path, auto


HOME_USES = [
    {"action": "select", "selector": "select#category", "field": "in_Cat"},
    {"action": "select", "selector": "select#sub"},
    {"action": "click", "selector": "button#btnSave"},
    {"action": "read", "selector": "#message"},
]


def test_ready_when_every_step_resolves_including_a_late_cascade(tmp_path, served):
    served("btnSave")
    root, auto = make_repo(
        tmp_path, HOME_USES, [{"method": "POST", "path": "/api/things/{in_Id}/notes"}]
    )
    report = conformance.check(root, auto, host="127.0.0.1")
    assert report.verdict == "READY", conformance.render(report)
    assert report.counts() == {"ok": 8}


def test_blocked_names_the_missing_selector_and_the_missing_route(tmp_path, served):
    served("saveButton")  # the app renamed the button
    routes = [
        {"method": "POST", "path": "/api/things/{in_Id}/notes"},
        {"method": "GET", "path": "/api/gone"},
    ]
    root, auto = make_repo(tmp_path, HOME_USES, routes)
    report = conformance.check(root, auto, host="127.0.0.1")
    assert report.verdict == "BLOCKED"
    failed = [i.what for i in report.items if i.status == "failed"]
    assert failed == ["button#btnSave on /home (click)", "GET /api/gone"]
    assert "button#btnSave" in conformance.render(report)


def test_a_selector_css_cannot_express_is_listed_not_checked(tmp_path, served):
    served("btnSave")
    uses = [*HOME_USES, {"action": "click", "selector": "uipath:<webctrl aaname='Save' />"}]
    root, auto = make_repo(tmp_path, uses, [])
    report = conformance.check(root, auto, host="127.0.0.1")
    assert report.verdict == "READY"
    assert report.counts()["unchecked"] == 1


def test_missing_lab_settings_block_with_a_clear_reason(tmp_path):
    root, auto = make_repo(tmp_path, HOME_USES, [])
    data = yaml.safe_load((root / "apps/demo/app.yaml").read_text(encoding="utf-8"))
    del data["lab"]
    (root / "apps/demo/app.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    report = conformance.check(root, auto)
    assert report.verdict == "BLOCKED"
    assert "no `lab:` section" in report.errors[0]


def test_compose_wires_the_app_through_the_agent_and_its_db_proxy(tmp_path):
    root, _ = make_repo(tmp_path, HOME_USES, [])
    (root / "apps/demo/db").mkdir()
    (root / "apps/demo/db/schema.sql").write_text("CREATE TABLE t (id INTEGER);", encoding="utf-8")
    doc = lab_mod.compose(root, "demo")
    services = doc["services"]
    assert services["app"]["environment"] == {
        "DATABASE_URL": "postgresql://lab:lab@agent:15432/demo"
    }
    assert services["agent"]["ports"] == [f"{PORT}:9000", "18612:15432"]
    assert services["agent"]["environment"]["APP_UPSTREAM"] == "http://app:8000"
    assert (
        services["agent"]["environment"]["ADMIN_DATABASE_URL"]
        == "postgresql://lab:lab@db:5432/demo"
    )
    assert doc["name"] == "twinlab-demo"


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs Docker")
def test_status_of_an_app_that_is_not_running(tmp_path):
    root, _ = make_repo(tmp_path, HOME_USES, [])
    (root / "apps/demo/db").mkdir()
    (root / "apps/demo/db/schema.sql").write_text("CREATE TABLE t (id INTEGER);", encoding="utf-8")
    state = lab_mod.status(root, "demo")
    assert state.running is False
