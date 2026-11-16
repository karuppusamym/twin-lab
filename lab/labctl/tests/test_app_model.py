"""E-01: the app model, its provenance, conflicts, and scope from requested flows."""

from pathlib import Path

import yaml
from typer.testing import CliRunner

from labctl import brief as brief_mod
from labctl import model_build as mb
from labctl.appmodel import AppModel, Flow
from labctl.cli import app
from labctl.models import Automation

ROOT = Path(__file__).resolve().parents[3]
REF = ROOT / "reference" / "case-workbench"
SAMPLE = ROOT / "automations" / "uipath-case-update" / "automation.yaml"
FLOW = ROOT / "specs" / "flow.example.yaml"
runner = CliRunner()


def _sample() -> Automation:
    return Automation.model_validate(yaml.safe_load(SAMPLE.read_text(encoding="utf-8")))


def _flow(**changes) -> Flow:
    return Flow.model_validate(yaml.safe_load(FLOW.read_text(encoding="utf-8")) | changes)


def _auto(touches: list[dict], auto_id: str = "bot") -> Automation:
    return Automation.model_validate(
        {
            "id": auto_id,
            "name": auto_id,
            "type": "uipath",
            "surface": "combo",
            "source": {"path": "."},
            "touches": touches,
        }
    )


# --- provenance ------------------------------------------------------------------------------------


def test_every_item_records_its_evidence_and_the_strongest_basis():
    model = brief_mod.make_model("showcase", [_sample()], ROOT, source=REF)
    login = next(s for s in model.screens if s.path == "/login")
    assert login.basis == "observed"
    assert login.from_ == ["source:reference/case-workbench", "automation:uipath-case-update"]
    user = next(e for e in login.elements if e.selector == "input#username")
    assert user.basis == "observed"  # the bot declared it, the source shows it
    assert set(user.from_) == {"automation:uipath-case-update", "source:reference/case-workbench"}
    notes = model.route("POST /api/cases/{case_no}/notes")
    assert notes.from_ == ["source:reference/case-workbench", "automation:uipath-case-update"]
    assert model.route("GET /api/responses").from_ == ["source:reference/case-workbench"]
    assert {r.basis for r in model.rules} == {"observed"}


def test_routes_that_only_look_alike_are_not_merged():
    model = mb.from_source("showcase", REF, ROOT)
    keys = [r.key for r in model.routes]
    assert "GET /api/cases/search" in keys and "GET /api/cases/{case_no}" in keys


def test_model_round_trips_through_yaml_and_validates():
    model = brief_mod.make_model("showcase", [_sample()], ROOT, source=REF, flows=[_flow()])
    text = yaml.safe_dump(model.model_dump(mode="json", by_alias=True, exclude_none=True))
    assert AppModel.model_validate(yaml.safe_load(text)) == model


def test_no_absolute_path_reaches_the_model_when_the_source_is_elsewhere(tmp_path):
    model = mb.from_source(
        "showcase", REF, tmp_path
    )  # repository root that does not hold the source
    text = yaml.safe_dump(model.model_dump(mode="json", by_alias=True, exclude_none=True))
    assert str(REF.parent.as_posix()) not in text and ":/" not in text
    assert model.screens[0].files[0].startswith("case-workbench/")


# --- conflicts -------------------------------------------------------------------------------------


def test_evidence_that_disagrees_on_a_selector_is_an_open_question_not_a_choice():
    bot = _auto([{"app": "showcase", "via": "web", "screens": [
        {"name": "search", "path": "/cases", "uses": [{"action": "click", "selector": "#searchButton"}]}]}])  # fmt: skip
    model = brief_mod.make_model("showcase", [bot], ROOT, source=REF)
    element = next(e for s in model.screens if s.path == "/cases" for e in s.elements)
    assert element.basis == "declared"  # not upgraded, not dropped
    assert [c.what.split(":")[0] for c in model.conflicts] == ["selector #searchButton on /cases"]
    assert model.conflicts[0].evidence == ["automation:bot", "source:reference/case-workbench"]
    assert any("#searchButton" in g for g in brief_mod.scope("showcase", "x", model).gaps)


def test_without_complete_evidence_a_declared_item_is_not_a_conflict():
    bot = _auto([
        {"app": "legacy", "via": "web", "screens": [{"name": "home", "path": "/home", "uses": [{"action": "click", "selector": "#go"}]}]},
        {"app": "legacy", "via": "api", "routes": [{"method": "POST", "path": "/api/orders"}]},
        {"app": "legacy", "via": "db", "tables": [{"table": "ORDERS", "columns": ["ID", "STATUS"], "access": "read"}]},
    ])  # fmt: skip
    model = brief_mod.make_model("legacy", [bot], ROOT)
    assert model.conflicts == []
    assert {s.basis for s in model.screens} == {"declared"}


# --- scope from requested flows ----------------------------------------------------------------------


def test_a_requested_flow_brings_its_screen_and_what_it_needs_into_scope():
    flow = _flow(
        id="responses",
        steps=[{"screen": "Canned responses", "path": "/admin/responses", "do": "add one"}],
    )
    plain = brief_mod.build("showcase", "x", automations=[_sample()], source=REF, repo_root=ROOT)
    asked = brief_mod.build(
        "showcase", "x", automations=[_sample()], flows=[flow], source=REF, repo_root=ROOT
    )
    assert "canned_response" not in plain.tables
    assert asked.tables["canned_response"] == [
        "used by GET /api/responses",
        "used by POST /api/responses",
    ]
    assert set(asked.routes) - set(plain.routes) == {"GET /api/responses", "POST /api/responses"}
    screen = next(s for s in asked.screens if s.screen.path == "/admin/responses")
    assert screen.reasons == ["requested in flow responses"]
    assert asked.out_screens == [] and asked.out_tables == []
    assert "responses" in asked.needed_by


def test_flow_steps_merge_with_observed_screens_and_keep_their_rules():
    built = brief_mod.build(
        "showcase", "x", automations=[], flows=[_flow()], source=REF, repo_root=ROOT
    )
    case = next(s.screen for s in built.screens if s.screen.path == "/cases/:caseNo")
    assert case.name == "cases-item" and "Case" in case.aliases
    assert case.basis == "observed"
    described = {r["rule"]: r["basis"] for r in brief_mod.rules(built)}
    assert described["Resolving needs at least one note"] == "described"
    labels = [e.label for e in case.elements if e.label]
    assert labels == ["Category", "Sub-category", "Status"]
    assert "canned_response" not in built.tables


def test_a_flow_alone_builds_a_described_skeleton():
    flow = _flow(
        app="newapp", steps=[{"screen": "Orders", "do": "list orders", "fields": ["Order no"]}]
    )
    built = brief_mod.build("newapp", "New app", automations=[], flows=[flow], repo_root=ROOT)
    assert [s.screen.basis for s in built.screens] == ["described"]
    assert built.screens[0].elements[0].status == "unchecked"
    assert any("no evidence shows the database engine" in g for g in built.gaps)
    rendered = brief_mod.render(built, ROOT)
    assert "No evidence shows the stack" in rendered and "*Order no*" in rendered


def test_automation_only_gives_declared_tables_with_assumed_types():
    bot = _auto([{"app": "legacy", "via": "db", "tables": [{"table": "ORDERS", "columns": ["ID", "STATUS"], "access": "read"}]}])  # fmt: skip
    built = brief_mod.build("legacy", "Legacy", automations=[bot], repo_root=ROOT)
    assert built.tables == {"ORDERS": ["queried directly by bot"]}
    ddl = brief_mod.schema(built, ROOT)
    assert "CREATE TABLE ORDERS" in ddl and "declared only; column types assumed" in ddl


# --- command line ----------------------------------------------------------------------------------


def test_brief_command_writes_a_model_and_reads_flows(tmp_path, monkeypatch):
    for folder in ("automations", "apps", "lab", "flows"):
        (tmp_path / folder).mkdir()
    (tmp_path / "flows/newapp.yaml").write_text(
        yaml.safe_dump({"id": "orders", "app": "newapp", "requested_by": "demo team",
                        "steps": [{"screen": "Orders", "path": "/orders", "do": "list"}]}),
        encoding="utf-8",
    )  # fmt: skip
    monkeypatch.setenv("LABCTL_ROOT", str(tmp_path))
    result = runner.invoke(app, ["brief", "newapp"])
    assert result.exit_code == 0, result.output
    model = AppModel.model_validate(
        yaml.safe_load((tmp_path / "apps/newapp/model.yaml").read_text(encoding="utf-8"))
    )
    assert model.flows == ["orders"]
    assert runner.invoke(app, ["validate"]).exit_code == 0
