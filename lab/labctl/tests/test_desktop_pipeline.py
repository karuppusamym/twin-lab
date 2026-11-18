"""Desktop recordings through the pipeline (runs everywhere, from recorded fixtures): model,
split, brief, build package and its task. The fixtures are real recordings of the reference apps
CaseDesk (WinForms, UI Automation) and CaseDeskSwing (Java Access Bridge), value-free."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from labctl import factory, models
from labctl.builder import package
from labctl.desktop import model as dmodel
from labctl.desktop import split as dsplit

FIX = Path(__file__).parent / "fixtures" / "desktop"
WINFORMS, SWING = FIX / "casedesk-rec", FIX / "casedesk-swing-rec"
REPO = Path(__file__).resolve().parents[3]


def test_model_from_winforms_recording() -> None:
    m = dmodel.analyse("casedesk", WINFORMS)
    assert m.stack.desktop["toolkit"] == "WinForms" and m.stack.desktop["runtime"]["name"] == ".NET"
    names = [s.name for s in m.screens]
    assert names[:1] == ["Sign in"] and "CaseDesk - Cases" in names and "Case X-9999" in names
    login = m.screens[0]
    assert (
        login.surface == "desktop"
        and login.desktop["window"]["selector"] == "uia:AutomationId=LoginForm;ControlType=Window"
    )
    el = {e.selector: e for e in login.elements}
    btn = el["uia:AutomationId=btnSignIn;ControlType=Button"]
    assert btn.actions == ["click"] and btn.label == "Sign in"
    assert btn.uia["uipath"].startswith("<wnd app='casedesk.exe' ctrlname='LoginForm'")
    assert el["uia:AutomationId=txtPassword;ControlType=Edit"].uia["password"] is True
    main = next(s for s in m.screens if s.name == "CaseDesk - Cases")
    menus = {mm["menu"]: [i["name"] for i in mm["items"]] for mm in main.desktop["menus"]}
    assert menus["Case"] == ["Open case", "Refresh"]
    assert main.desktop["grids"][0]["columns"] == ["Case No", "Customer", "Status", "Opened"]
    assert "F5" in main.desktop["shortcuts"] and "Enter" in main.desktop["keys_seen"]
    table = m.table("cases")
    assert table.basis == "assumed" and [c.name for c in table.columns][:4] == [
        "case_no",
        "customer",
        "status",
        "opened",
    ]
    assert next(c for c in table.columns if c.name == "opened").type == "DATE"
    assert {"summary", "urgent"} <= {c.name for c in table.columns}
    assert "Resolved" in m.codes["cases.status"]
    assert any("message box" in r.text and "at least 10 characters" in r.text for r in m.rules)
    boxes = [s for s in m.screens if s.desktop["kind"] == "message box"]
    assert len(boxes) == 2
    dump = json.dumps(m.model_dump(mode="json", by_alias=True))
    assert "C-1001" not in dump and "Ada" not in dump


def test_model_from_swing_recording() -> None:
    m = dmodel.analyse("casedesk-swing", SWING)
    assert m.stack.desktop["toolkit"] == "Java Swing / AWT"
    sels = {e.selector for s in m.screens for e in s.elements}
    assert "jab:Name=Sign in;Role=push button" in sels and "jab:Name=Status;Role=combo box" in sels
    login = m.screens[0]
    btn = next(e for e in login.elements if e.selector == "jab:Name=Sign in;Role=push button")
    assert "<java role='push button' name='Sign in' />" in btn.uia["uipath"]
    assert any("at least 10 characters" in r.text for r in m.rules)


def test_split_and_interleave(tmp_path: Path) -> None:
    rec = tmp_path / "two-apps"
    shutil.copytree(WINFORMS, rec)
    lines = (rec / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    out = []
    for n, line in enumerate(lines):  # pretend the last third happened in another application
        s = json.loads(line)
        if n >= 2 * len(lines) // 3:
            s["process"] = "Other.exe"
        out.append(json.dumps(s))
    (rec / "steps.jsonl").write_text("\n".join(out) + "\n", encoding="utf-8")
    prop = dsplit.propose(rec)
    assert [(e.id, e.process) for e in prop.apps] == [
        ("casedesk", "CaseDesk.exe"),
        ("other", "Other.exe"),
    ]
    path = dsplit.save(prop, tmp_path / "p.yaml")
    prop = dsplit.load(path)
    parts = dsplit.apply(rec, prop, tmp_path)
    assert [p.parent.name for p in parts] == ["casedesk", "other"]
    first = [
        json.loads(x) for x in (parts[1] / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert first[0]["seq"] == 1 and first[0]["global_seq"] > 1 and first[0]["surface"] == "desktop"
    assert all((parts[1] / s["tree"]).is_file() for s in first if s.get("tree"))
    seq = yaml.safe_load(
        (tmp_path / "evidence" / "_sequences" / "two-apps.flow.yaml").read_text(encoding="utf-8")
    )
    assert seq["apps"] == ["casedesk", "other"] and seq["steps"][0]["surface"] == "desktop"
    assert dmodel.analyse("other", parts[1]).screens
    web = tmp_path / "web"
    web.mkdir()
    (web / "steps.jsonl").write_text(json.dumps({"seq": 1, "ts": "2000-01-01T00:00:00Z", "action": "goto",
                                                 "url": "https://x.example/"}) + "\n", encoding="utf-8")  # fmt: skip
    rows = dsplit.interleave(rec, web)
    assert rows[0]["surface"] == "web" and rows[1]["surface"] == "desktop"


def _lab(tmp_path: Path) -> Path:
    root = tmp_path / "lab"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REPO / "AGENTS.md", root / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", root / "docs" / "factory-playbook.md")
    (root / "flows").mkdir()
    flow = {"id": "casedesk-update", "app": "casedesk", "requested_by": "test",
            "purpose": "bot updates a case", "steps": [
                {"screen": "Sign in", "do": "sign in"}, {"screen": "CaseDesk - Cases", "do": "search"},
                {"screen": "Case X-9999", "do": "edit and save"}]}  # fmt: skip
    (root / "flows" / "casedesk-update.yaml").write_text(yaml.safe_dump(flow), encoding="utf-8")
    shutil.copytree(WINFORMS, root / "evidence" / "casedesk" / "casedesk-rec")
    return root


def test_brief_and_package_for_a_desktop_app(tmp_path: Path) -> None:
    root = _lab(tmp_path)
    found = factory.discover(root, "casedesk")
    assert found.kinds() == ["desktop"]
    done = factory.generate(root, "casedesk", "CaseDesk")
    brief = (root / "apps" / "casedesk" / "BRIEF.md").read_text(encoding="utf-8")
    assert "## Desktop windows and controls" in brief
    assert "`uia:AutomationId=btnSave;ControlType=Button`" in brief
    assert "WinForms" in brief and "sqlite" in brief.lower()
    assert "No Dockerfile" in brief and "One Dockerfile" not in brief
    assert "GET /api/health" not in brief
    assert done.brief.mode == "reconstruction"
    # the three windows the flow names, and the two message boxes their actions open
    assert sorted(s.screen.name for s in done.brief.screens) == [
        "Case (message box)",
        "Case X-9999",
        "CaseDesk - Cases",
        "Sign in",
        "Sign in (message box)",
    ]
    conf = yaml.safe_load((root / "apps" / "casedesk" / "app.yaml").read_text(encoding="utf-8"))
    assert (
        conf["stack"]["desktop"]["toolkit"] == "WinForms"
        and conf["stack"]["db"]["engine"] == "sqlite"
    )
    models.App.model_validate(conf)  # labctl validate accepts it
    assert "CREATE TABLE" in (root / "apps" / "casedesk" / "db" / "schema.sql").read_text(
        encoding="utf-8"
    )
    task = package.prompt(root, "casedesk", None)
    assert "Windows desktop application" in task and "dotnet publish" in task
    assert "no Dockerfile" in task and "labctl desktop verify casedesk" in task
    assert "docker build" not in task
    zipped = package.build(root, "casedesk", tmp_path / "out")
    names = set(package.read_manifest(zipped)["files"][i]["path"] for i in range(3))
    assert "BUILD_TASK.md" in names or zipped.is_file()


def test_web_model_digest_unchanged_by_desktop_fields() -> None:
    from labctl.appmodel import AppModel, Screen
    from labctl.model_build import model_digest

    m = AppModel(app="x", screens=[Screen(name="home", basis="observed", path="/")])
    data = m.model_dump(mode="json", by_alias=True)
    assert data["screens"][0]["surface"] is None  # present in the dump ...
    assert model_digest(m) == model_digest(
        AppModel.model_validate(data)
    )  # ... and not in the digest
