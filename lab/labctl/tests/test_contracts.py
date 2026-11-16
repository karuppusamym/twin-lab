from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl import repo
from labctl.cli import app
from labctl.models import KINDS, Automation, Condition, DataPack

ROOT = Path(__file__).resolve().parents[3]
SPECS = sorted((ROOT / "specs").glob("*.example.*"))
runner = CliRunner()


def _example(name: str) -> dict:
    return yaml.safe_load((ROOT / "specs" / name).read_text(encoding="utf-8"))


def test_every_contract_has_an_example():
    assert {repo.kind_of(p) for p in SPECS} == set(KINDS)


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_example_validates(path):
    model, problems = repo.load(path)
    assert problems == []
    assert model is not None


def test_unknown_key_is_rejected_with_its_name(tmp_path):
    data = _example("automation.example.yaml")
    data["tigger"] = "manual"
    bad = tmp_path / "automation.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    _, problems = repo.load(bad)
    assert [p.field for p in problems] == ["tigger"]


def test_validate_command_reports_file_and_field_for_a_broken_file(tmp_path):
    data = _example("automation.example.yaml")
    data["touches"][0]["screens"][0]["uses"][0]["action"] = "shout"
    bad = tmp_path / "automation.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1
    assert str(bad) in result.output
    assert "touches.0.web.screens.0.uses.0.action" in result.output


def test_validate_command_passes_on_the_repository(monkeypatch):
    monkeypatch.setenv("LABCTL_ROOT", str(ROOT))
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert "0 problem(s)" in result.output


def test_help_lists_the_command_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in ("automation", "app", "data", "condition", "validate"):
        assert group in result.output


def test_backend_automation_cannot_touch_a_screen():
    data = _example("automation.example.yaml")
    data["surface"] = "backend"
    with pytest.raises(ValueError, match="backend"):
        Automation.model_validate(data)


def test_automation_lists_the_apps_it_touches():
    assert Automation.model_validate(_example("automation.example.yaml")).apps() == ["showcase"]


def test_real_derived_pack_must_be_masked():
    data = _example("pack.example.yaml") | {
        "kind": "byo",
        "generator": None,
        "derived_from_real_data": True,
        "masked": False,
    }
    with pytest.raises(ValueError, match="masked"):
        DataPack.model_validate(data)


def test_synthetic_pack_needs_a_generator():
    data = _example("pack.example.yaml") | {"generator": None}
    with pytest.raises(ValueError, match="generator"):
        DataPack.model_validate(data)


def test_p95_below_p50_is_rejected():
    data = _example("condition.example.yaml")
    data["targets"][0]["http"][0]["latency_ms"] = {"p50": 900, "p95": 100}
    with pytest.raises(ValueError, match="p95"):
        Condition.model_validate(data)


def test_unreadable_yaml_is_a_problem_not_a_crash(tmp_path):
    bad = tmp_path / "app.yaml"
    bad.write_text("id: [unclosed", encoding="utf-8")
    model, problems = repo.load(bad)
    assert model is None
    assert problems[0].message.startswith("cannot read")
