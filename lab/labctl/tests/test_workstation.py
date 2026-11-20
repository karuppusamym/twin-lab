"""labctl.workstation: identity and levels, portal labels, the merged timeline, recovery, and
the web recorder's origin filter. The live session (real Edge, real CaseDesk) is in
test_workstation_gui.py."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from labctl.workstation import flow, levels, timeline
from labctl.workstation.levels import FULL, PRESENCE, STEPS, Targets
from labctl.workstation.session import recover

CASEDESK_EXE = {"file": "CaseDesk.exe", "company": "Twin Lab reference", "product": "CaseDesk",
                "product_version": "1.0.0"}  # fmt: skip


# --- identity and levels ---------------------------------------------------------------------


def test_identity_is_company_and_product_not_path_or_version():
    a = levels.desktop_identity(r"C:\apps\v1\CaseDesk.exe", CASEDESK_EXE)
    b = levels.desktop_identity(
        r"D:\other\CaseDesk_v2.exe", dict(CASEDESK_EXE, product_version="2")
    )
    assert a["key"] == b["key"] == "desktop:Twin Lab reference/CaseDesk"
    assert a["exe"] == "CaseDesk.exe" and "path" not in a
    bare = levels.desktop_identity(r"C:\tools\Thing.exe", {})
    assert bare["key"] == "desktop:exe/thing.exe"


def test_origin_is_normalised_and_drops_user_info():
    assert levels.origin_of("HTTPS://User:pw@Example.com:443/a?b") == "https://example.com"
    assert levels.origin_of("http://127.0.0.1:8080/x") == "http://127.0.0.1:8080"
    assert levels.origin_of("about:blank") == ""


def test_levels_targets_steps_presence_and_deny_list():
    t = Targets.from_list([{"exe": "CaseDesk.exe"}, {"exe": "Notes.exe", "level": "steps"},
                           {"origin": "http://127.0.0.1:8080"}])  # fmt: skip
    case = levels.desktop_identity(r"C:\a\CaseDesk.exe", CASEDESK_EXE)
    assert t.level(case, r"C:\a\CaseDesk.exe") == FULL
    assert t.level(levels.desktop_identity(r"C:\a\Notes.exe", {}), r"C:\a\Notes.exe") == STEPS
    other = levels.desktop_identity(r"C:\a\Other.exe", {})
    assert t.level(other, r"C:\a\Other.exe") == PRESENCE
    assert t.level(levels.web_identity("http://127.0.0.1:8080/cases")) == FULL
    assert t.level(levels.web_identity("http://127.0.0.1:9999/")) == PRESENCE


@pytest.mark.parametrize("path", [
    r"C:\Windows\explorer.exe", r"C:\Windows\System32\notepad.exe",
    r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe",
    r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
    r"C:\Users\u\AppData\Local\Microsoft\Teams\ms-teams.exe",
    r"C:\Program Files\KeePass\KeePass.exe",
])  # fmt: skip
def test_deny_list_is_presence_even_as_a_target(path):
    name = path.rsplit("\\", 1)[-1]
    t = Targets.from_list([{"exe": name}])
    assert t.level(levels.desktop_identity(path, {}), path) == PRESENCE
    allowed = Targets.from_list([{"exe": name, "allow_denied": True}])
    assert allowed.level(levels.desktop_identity(path, {}), path) == FULL


def test_hosts_and_browsers_are_never_applications_by_themselves():
    t = Targets.from_list([{"exe": "CaseDesk.exe"}])
    for path in (r"C:\jdk\bin\javaw.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        assert t.level(levels.desktop_identity(path, {}), path) == PRESENCE
    assert Targets.from_list([{"origin": "https://outlook.office.com"}]).level(
        levels.web_identity("https://outlook.office.com/mail")) == PRESENCE  # fmt: skip


def test_marked_not_replicated_is_presence():
    case = levels.desktop_identity(r"C:\a\CaseDesk.exe", CASEDESK_EXE)
    t = Targets.from_list([{"exe": "CaseDesk.exe"}], deny_keys=[case["key"]])
    assert t.level(case, r"C:\a\CaseDesk.exe") == PRESENCE


def test_target_validation():
    with pytest.raises(ValueError):
        Targets.from_list([{"level": "full"}])
    with pytest.raises(ValueError):
        Targets.from_list([{"exe": "a.exe", "level": "presence"}])
    t = Targets.from_list(
        [{"key": "web:http://127.0.0.1:8080", "start": "http://127.0.0.1:8080/login"}]
    )
    assert t.origins() == {"http://127.0.0.1:8080"}
    assert t.web_start() == "http://127.0.0.1:8080/login"


# --- labels -------------------------------------------------------------------------------------


def _desk(selector, props=None, title="CaseDesk - Cases", wid="MainForm", action="click"):
    return {"surface": "desktop", "action": action,
            "window": {"selector": f"uia:AutomationId={wid};ControlType=Window" if wid else None,
                       "title": title, "props": {"AutomationId": wid} if wid else {}},
            "target": {"selector": selector, "props": props or {}}}  # fmt: skip


def test_desktop_labels_keep_ids_and_structure_names_only():
    s = flow.label_step(_desk("uia:AutomationId=btnSave;Name=Save;ControlType=Button"), 3)
    assert s == {
        "n": 3,
        "action": "click",
        "control": "Button",
        "label": "btnSave",
        "screen": "MainForm",
    }
    named = flow.label_step(_desk("uia:Name=Open;ControlType=Button"), 1)
    assert named["label"] == "Open"
    # a list item's or a text's name is data: never a label
    for sel in ("uia:Name=Jane Doe;ControlType=ListItem", "uia:Name=Jane Doe;ControlType=Text",
                "uia:Name=Xxxx Xxx;ControlType=Button", "uia:Name=Case 10023;ControlType=Button",
                "uia:Name=jane@example.com;ControlType=Button"):  # fmt: skip
        assert "label" not in flow.label_step(_desk(sel), 1), sel
    # a title with a record in it: only a plain application part, else nothing
    no_id = _desk("uia:AutomationId=btnSave;ControlType=Button", title="Case X-9999", wid=None)
    assert "screen" not in flow.label_step(no_id, 1)
    doc = _desk(
        "uia:AutomationId=btnSave;ControlType=Button", title="Smith letter - Writer", wid=None
    )
    assert "screen" not in flow.label_step(doc, 1)  # the title names the document
    assert flow.label_step(doc, 1, {"display": "Writer"})["screen"] == "Writer"
    record = _desk(
        "uia:AutomationId=btnSave;ControlType=Button", title="Writer - Jane Doe", wid=None
    )
    assert flow.label_step(record, 1, {"display": "Writer"})["screen"] == "Writer"
    for sel in ("uia:Name=Jane Doe;ControlType=TabItem", "uia:Name=Jane Doe;ControlType=Window",
                "uia:Name=Delete the record of Jane;ControlType=Button"):  # fmt: skip
        assert "label" not in flow.label_step(_desk(sel), 1), sel


def test_web_labels_and_route_templates():
    s = flow.label_step({"action": "click", "selector": "button#btnSave",
                         "url": "https://app.example.test/cases/C-100/edit?x=1#f"}, 2)  # fmt: skip
    assert s == {"n": 2, "action": "click", "control": "button", "label": "btnSave",
                 "screen": "https://app.example.test/cases/{id}/edit"}  # fmt: skip
    t = flow.label_step(
        {"action": "click", "selector": 'a:has-text("Jane Doe")', "url": "http://h/"}, 1
    )
    assert "label" not in t and t["control"] == "a"
    n = flow.label_step(
        {"action": "type", "selector": 'input[name="custName"]', "url": "http://h/"}, 1
    )
    assert n["label"] == "custName"
    assert flow.route_template("/a/" + "x" * 30 + "/b") == "/a/{id}/{id}"
    assert flow.route_template("/customers/jane-doe/edit") == "/customers/{id}/edit"
    aria = flow.label_step({"action": "click", "selector": '[aria-label="Delete Jane Doe"]',
                            "url": "http://h/"}, 1)  # fmt: skip
    assert "label" not in aria


# --- timeline -----------------------------------------------------------------------------------


def _write(folder: Path, rel: str, rows: list[dict]) -> None:
    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
    (folder / rel).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _session(tmp_path: Path, state="stopped") -> Path:
    folder = tmp_path / "s1"
    folder.mkdir()
    meta = {"format": timeline.FORMAT, "id": "s1", "name": "trial", "state": state,
            "started": "2026-11-19T10:00:00.000Z", "ended": "2026-11-19T10:05:00.000Z",
            "targets": [{"exe": "casedesk.exe", "level": "full"},
                        {"origin": "http://127.0.0.1:8080", "level": "full", "app_id": 7}],
            "apps": {}, "notes": []}  # fmt: skip
    (folder / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    key = "desktop:Twin Lab reference/CaseDesk"
    _write(folder, "journal.jsonl", [
        {"ts": "2026-11-19T10:00:01.000Z", "kind": "app", "pid": 11, "key": key, "surface": "desktop",
         "exe": "CaseDesk.exe", "level": "full", "display": "CaseDesk"},
        {"ts": "2026-11-19T10:00:05.000Z", "kind": "presence", "exe": "notepad.exe"},
        {"ts": "2026-11-19T10:00:05.500Z", "kind": "presence", "exe": "explorer.exe"},
        {"ts": "2026-11-19T10:00:09.000Z", "kind": "outside", "denied": False},
    ])  # fmt: skip
    _write(folder, "desktop/steps.jsonl", [
        {"seq": 1, "ts": "2026-11-19T10:00:02.000Z", "surface": "desktop", "action": "type", "pid": 11,
         "window": {"selector": "uia:AutomationId=MainForm;ControlType=Window", "title": "CaseDesk - Cases"},
         "target": {"selector": "uia:AutomationId=txtSearch;ControlType=Edit"}, "typed_shape": "X-999"},
        {"seq": 2, "ts": "2026-11-19T10:00:03.000Z", "surface": "desktop", "action": "click", "pid": 11,
         "window": {"selector": "uia:AutomationId=MainForm;ControlType=Window", "title": "Case X-9999"},
         "target": {"selector": "uia:Name=Xxxx Xxx;ControlType=ListItem"}},
    ])  # fmt: skip
    _write(folder, "web/steps.jsonl", [
        {"seq": 1, "ts": "2026-11-19T10:00:00.500Z", "url": "about:blank", "action": "goto"},
        {"seq": 2, "ts": "2026-11-19T10:00:06.000Z", "url": "http://127.0.0.1:8080/login",
         "action": "type", "selector": "input#username"},
        {"seq": 3, "ts": "2026-11-19T10:00:07.000Z", "url": "http://127.0.0.1:8080/cases/C-100",
         "action": "click", "selector": "button#btnSave"},
    ])  # fmt: skip
    return folder


def test_timeline_merges_by_time_and_writes_app_switches(tmp_path):
    folder = _session(tmp_path)
    box = timeline.build(folder)
    rows = timeline.read_jsonl(folder / "session.jsonl")
    apps = [r.get("app") if r["action"] != "app-switch" else "->" for r in rows]
    desk, web = "desktop:Twin Lab reference/CaseDesk", "web:http://127.0.0.1:8080"
    assert apps == [desk, desk, "->", "presence", "->", web, web, "->", "presence"]
    assert [r["global_seq"] for r in rows] == list(range(1, len(rows) + 1))
    # the references: target applications and their labels only
    assert {a["key"] for a in box["apps"]} == {desk, web}
    assert next(a for a in box["apps"] if a["key"] == web)["app_id"] == 7
    assert box["flows"][desk][0] == {"n": 1, "action": "type", "control": "Edit",
                                     "label": "txtSearch", "screen": "MainForm"}  # fmt: skip
    assert "label" not in box["flows"][desk][1]
    assert box["flows"][web][1]["screen"] == "http://127.0.0.1:8080/cases/{id}"
    assert box["timeline"] == [{"app": desk, "steps": 2}, {"app": "presence", "steps": 1},
                               {"app": web, "steps": 2}, {"app": "presence", "steps": 1}]  # fmt: skip
    text = json.dumps(box)
    for leak in ("notepad", "explorer", "X-999", "Xxxx", "Case X", "C-100", "10:00:05"):
        assert leak not in text, leak


def test_recover_finalises_an_unfinished_session(tmp_path):
    folder = _session(tmp_path, state="recording")
    with (folder / "desktop" / "steps.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "ts": "2026-11-19T10:0')  # the process died mid-line
    assert recover(folder) is True
    meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert meta["state"] == "recovered" and meta["ended"] == "2026-11-19T10:00:09.000Z"
    assert any("no HAR" in n for n in meta["notes"])
    assert (folder / "portal-outbox.json").is_file()
    assert recover(folder) is False  # once


# --- the web recorder's origin filter -----------------------------------------------------------


def test_web_steps_outside_target_origins_are_not_numbered(tmp_path):
    from labctl.recorder import _Steps

    heard = []
    steps = _Steps(tmp_path, screenshots=False, origins={"http://127.0.0.1:8080"},
                   outside=lambda o, ts: heard.append(o))  # fmt: skip
    steps.on_event({}, {"action": "click", "url": "https://mail.example.test/inbox",
                        "selector": "a#m", "before": "<html>secret</html>", "eid": "e1"})  # fmt: skip
    assert steps.seq == 0 and not list(tmp_path.glob("dom/*"))
    assert heard == ["https://mail.example.test"]
    (tmp_path / "dom").mkdir()
    steps.on_event({}, {"action": "click", "url": "http://127.0.0.1:8080/x", "selector": "a#b",
                        "eid": "e2"})  # fmt: skip
    assert steps.seq == 1
    steps.close()


def test_har_keeps_only_requests_of_target_pages():
    from labctl.recorder import _Har

    har = _Har({"http://127.0.0.1:8080"})

    def req(page_url, url):
        return SimpleNamespace(frame=SimpleNamespace(page=SimpleNamespace(url=page_url)), url=url)

    assert har._wanted(req("http://127.0.0.1:8080/a", "https://cdn.example.test/x.js"))
    assert not har._wanted(req("https://mail.example.test/", "https://mail.example.test/api"))
    assert _Har()._wanted(req("https://mail.example.test/", "https://mail.example.test/api"))


def test_a_step_without_a_process_belongs_to_the_application_before_it(tmp_path):
    folder = _session(tmp_path)
    with (folder / "desktop" / "steps.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "seq": 3,
                    "ts": "2026-11-19T10:00:04.000Z",
                    "surface": "desktop",
                    "action": "window_close",
                    "pid": None,
                    "process": None,
                    "window": {"selector": "uia:Name=Sign in;ControlType=Window;ClassName=#32770"},
                }
            )
            + "\n"
        )
    box = timeline.build(folder)
    desk = "desktop:Twin Lab reference/CaseDesk"
    assert [r["app"] for r in box["timeline"]][:2] == [desk, "presence"]
    assert box["timeline"][0]["steps"] == 3
    keys = {a["key"] for a in box["apps"]}
    assert all(r["app"] in keys | {"presence"} for r in box["timeline"])
