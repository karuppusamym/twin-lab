"""The pull-request guard (tools/guard_ai_changes.py): what an AI builder may not do."""

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("guard", ROOT / "tools/guard_ai_changes.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

APP = {
    "id": "qtt",
    "run": {"web": {"port": 8000}},
    "rules": [{"id": "R1", "rule": "x", "verification": "hypothesis"}],
}


def run(changes: dict[str, tuple[str | None, str | None]], labels=(), ai=True) -> list[str]:
    return guard.check(
        list(changes), lambda p: changes[p][0], lambda p: changes[p][1], set(labels), ai
    )


def dump(data) -> str:
    return yaml.safe_dump(data)


def raised(level="owner_approved") -> dict:
    return {**APP, "rules": [{"id": "R1", "rule": "x", "verification": level}]}


def test_nobody_approves_a_rule_without_the_owner_label():
    change = {"apps/qtt/app.yaml": (dump(APP), dump(raised()))}
    assert any("raised to owner_approved" in p for p in run(change, ai=False))
    assert run(change, labels=["owner-approved"], ai=False) == []


def test_the_builder_may_change_run_but_nothing_else_in_app_yaml():
    ok = {**APP, "run": {"web": {"port": 9000}}}
    assert run({"apps/qtt/app.yaml": (dump(APP), dump(ok))}) == []
    bad = {**APP, "lab": {"port": 1}}
    assert any("outside `run:`" in p for p in run({"apps/qtt/app.yaml": (dump(APP), dump(bad))}))


def test_the_builder_may_not_edit_generated_inputs():
    for path in ("apps/qtt/BRIEF.md", "apps/qtt/db/schema.sql", "apps/qtt/pages/queue.html",
                 "apps/qtt/look/theme.json"):  # fmt: skip
        assert any("generated from the evidence" in p for p in run({path: ("a", "b")})), path
    assert run({"apps/qtt/app/main.py": ("a", "b")}) == []


def test_app_and_its_checks_do_not_change_together():
    change = {"apps/qtt/app/main.py": ("a", "b"), "automations/x/automation.yaml": ("a", "b")}
    assert any("oracle-reviewed" in p for p in run(change))
    assert run(change, labels=["oracle-reviewed"]) == []
    assert run(change, ai=False) == []  # a person's pull request may do both


def test_the_repository_as_it_is_passes():
    apps = sorted(p for p in (ROOT / "apps").glob("*/app.yaml"))
    for path in apps:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        assert run({rel: (text, text)}, ai=False) == []
