"""Fields a bot's own code sends to and reads from each route reach the brief (round 2 of QTT)."""

import shutil
import textwrap
from pathlib import Path

import yaml

from labctl import repo
from labctl.analyse import python as pyscan
from labctl.analyse import run as analysis
from labctl.analyse import uipath
from labctl.appmodel import AppModel, Route
from labctl.brief import has_field, render, scope

ROOT = Path(__file__).resolve().parents[3]


def scan(tmp_path: Path, code: str) -> dict:
    f = tmp_path / "bot.py"
    f.write_text(textwrap.dedent(code), encoding="utf-8")
    return {k: (v.sends, v.reads) for k, v in pyscan.analyse([f]).items()}


def test_the_sla_report_bot_needs_token_and_items():
    found = pyscan.analyse([ROOT / "automations/python-qtt-sla-report/report.py"])
    login, listing = found[("POST", "/api/login")], found[("GET", "/api/reviews")]
    assert login.sends == ["password", "username"] and login.reads == ["token"]
    assert listing.sends == ["status"]
    assert listing.reads == [
        "items[].assignee", "items[].due_date", "items[].loan_no", "items[].review_no",
        "items[].submitted_at",
    ]  # fmt: skip


def test_values_are_followed_through_variables_loops_and_functions(tmp_path):
    found = scan(
        tmp_path,
        """
        import httpx

        def fetch(c, case_no):
            r = c.get(f"/api/cases/{case_no}", params={"full": 1})
            body = r.json()
            return body["case"]

        def notes(case):
            return [n["text"] for n in case["notes"]]

        def main():
            with httpx.Client(base_url="http://x") as c:
                case = fetch(c, "C-1")
                print(case.get("status"), notes(case))
                for i, row in enumerate(c.get("/api/cases").json()["items"]):
                    print(row["case_no"])
                c.put(f"{BASE}/api/cases/x", json={"status": "OPEN", "note": ""})
        """,
    )
    assert found[("GET", "/api/cases/{case_no}")] == (
        ["full"],
        ["case.notes[].text", "case.status"],
    )
    assert found[("GET", "/api/cases")] == ([], ["items[].case_no"])
    assert found[("PUT", "/api/cases/x")] == (["note", "status"], [])


def test_unreadable_code_gives_nothing(tmp_path):
    assert scan(tmp_path, "def broken(:\n") == {}


def test_uipath_bots_read_the_token_and_the_status():
    folder = ROOT / "automations/uipath-qtt-qc-review"
    bindings = yaml.safe_load((folder / "automation.yaml").read_text("utf-8"))["bindings"]
    result = uipath.analyse(folder / "project", "Main.xaml", bindings)
    api = next(t for t in result.touches if t["via"] == "api")["routes"]
    login = next(r for r in api if r["path"] == "/api/login")
    review = next(r for r in api if r["path"].startswith("/api/reviews/"))
    assert login["sends"] == ["username", "password"] and login["reads"] == ["token"]
    assert review["reads"] == ["status"]


def test_python_analysis_fills_declared_routes_and_keeps_a_persons_notes(tmp_path):
    folder = tmp_path / "python-qtt-sla-report"
    shutil.copytree(ROOT / "automations/python-qtt-sla-report", folder)
    extra = folder / "extra.py"
    extra.write_text('def x(c):\n    return c.delete("/api/other")\n', encoding="utf-8")
    model, problems = repo.load(folder / "automation.yaml", "automation")
    assert model is not None, problems
    out = analysis.analyse(folder, model)
    routes = next(t for t in out.data["touches"] if t["via"] == "api")["routes"]
    assert routes[0]["reads"] == ["token"]
    assert out.data["analysis"]["by"] == analysis.PYTHON_VERSION
    whats = [n.what for n in out.notes]
    assert any("status=SUBMITTED" in w for w in whats)  # the person's note stays
    assert any("DELETE /api/other" in w for w in whats)  # a call nobody declared


def test_brief_lists_the_fields_and_flags_one_the_recording_lacks():
    model = AppModel(app="a")
    model.routes.append(
        Route(
            method="POST",
            path="/api/login",
            basis="observed",
            responses={"200": {"username": "string"}},
            reads=["token"],
            sends=["username"],
            **{"from": ["automation:bot"]},
        )
    )
    brief = scope("a", "A", model)
    assert any("`token`" in g and "recorded answer" in g for g in brief.gaps)
    text = render(brief, ROOT)
    assert "automations read: `token`" in text and "automations send: `username`" in text


def test_has_field_walks_lists():
    shape = {"items": {"[]": {"review_no": "string"}}, "total": "integer"}
    assert has_field(shape, "items[].review_no") and has_field(shape, "total")
    assert not has_field(shape, "items[].loan_no") and not has_field(shape, "token")


def test_the_registered_automations_carry_their_fields():
    for name in ("python-qtt-sla-report", "uipath-qtt-qc-review", "uipath-case-update"):
        data = yaml.safe_load((ROOT / "automations" / name / "automation.yaml").read_text("utf-8"))
        login = [
            r for t in data["touches"] if t["via"] == "api" for r in t["routes"]
            if r["path"] == "/api/login"
        ]  # fmt: skip
        assert login and login[0]["reads"] == ["token"], name
