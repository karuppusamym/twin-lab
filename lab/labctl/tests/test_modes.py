"""Build modes: copy, reconstruction, skeleton, backend, sample (docs/06-evidence.md)."""

from pathlib import Path

import yaml

from labctl import brief as brief_mod
from labctl.appmodel import Flow
from labctl.models import App, Automation

ROOT = Path(__file__).resolve().parents[3]
REF = ROOT / "reference" / "case-workbench"
SAMPLE = ROOT / "automations" / "uipath-case-update" / "automation.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "showcase-rec"


def _sample() -> Automation:
    return Automation.model_validate(yaml.safe_load(SAMPLE.read_text(encoding="utf-8")))


def _auto(touches: list[dict]) -> Automation:
    return Automation.model_validate({"id": "bot", "name": "bot", "type": "uipath", "surface": "combo",
                                      "source": {"path": "."}, "touches": touches})  # fmt: skip


def _modes(built) -> dict[str, str]:
    return {s.screen.path or s.screen.name: s.mode for s in built.screens}


def _check_app_yaml(built) -> dict:
    data = brief_mod.app_yaml(built, ROOT)
    App.model_validate(data)
    assert list(data)[:3] == ["id", "name", "mode"]
    return data


def test_source_gives_a_copy():
    built = brief_mod.build("showcase", "x", automations=[_sample()], source=REF, repo_root=ROOT)
    assert set(_modes(built).values()) == {"copy"} and built.mode == "copy"
    assert _check_app_yaml(built)["mode"] == "copy"
    assert "| Mode | **copy**" in brief_mod.render(built, ROOT)


def test_a_recording_gives_a_reconstruction_and_says_what_that_means():
    built = brief_mod.build("showcase", "x", automations=[_sample()], recordings=[FIXTURE],
                            repo_root=ROOT)  # fmt: skip
    assert _modes(built) == {"/login": "reconstruction", "/cases": "reconstruction",
                             "/cases/{id}": "reconstruction"}  # fmt: skip
    text = brief_mod.render(built, ROOT)
    assert "## Build mode" in text and "This app is a **reconstruction**" in text
    assert "(cases-item, observed, reconstruction; used by uipath-case-update)" in text
    assert _check_app_yaml(built)["mode"] == "reconstruction"


def test_automation_only_gives_a_skeleton_and_mixed_apps_take_the_weakest_mode():
    bot = _auto([{"app": "showcase", "via": "web", "screens": [
        {"name": "reports", "path": "/reports", "uses": [{"action": "click", "selector": "#run"}]}]}])  # fmt: skip
    alone = brief_mod.build("showcase", "x", automations=[bot], repo_root=ROOT)
    assert _modes(alone) == {"/reports": "skeleton"} and alone.mode == "skeleton"
    mixed = brief_mod.build("showcase", "x", automations=[_sample(), bot], recordings=[FIXTURE],
                            repo_root=ROOT)  # fmt: skip
    assert _modes(mixed)["/reports"] == "skeleton" and _modes(mixed)["/cases"] == "reconstruction"
    assert mixed.mode == "skeleton"
    text = brief_mod.render(mixed, ROOT)
    assert (
        "| `/reports` | skeleton |" in text
        and "- **reconstruction**: built from a recording" in text
    )


def test_no_screens_gives_backend_and_a_description_gives_a_sample():
    api = _auto([{"app": "svc", "via": "api", "routes": [{"method": "GET", "path": "/api/orders"}]},
                 {"app": "svc", "via": "db", "tables": [{"table": "ORDERS", "columns": ["ID"], "access": "read"}]}])  # fmt: skip
    backend = brief_mod.build("svc", "Service", automations=[api], repo_root=ROOT)
    assert backend.screens == [] and backend.mode == "backend"
    assert "This app is a **backend**" in brief_mod.render(backend, ROOT)
    flow = Flow.model_validate({"id": "orders", "app": "newapp", "requested_by": "demo team",
                                "steps": [{"screen": "Orders", "do": "list"}]})  # fmt: skip
    sample = brief_mod.build("newapp", "New", automations=[], flows=[flow], repo_root=ROOT)
    assert _modes(sample) == {"Orders": "sample"} and sample.mode == "sample"
    assert _check_app_yaml(sample)["mode"] == "sample"
