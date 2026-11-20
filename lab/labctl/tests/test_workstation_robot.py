"""The robot's own log beside a session (labctl.workstation.robot): activities in the session's
time, and which UI activities left no recorded step."""

from __future__ import annotations

import json
from pathlib import Path

from labctl.workstation import robot, timeline


def _log_line(ts: str, message: str, activity: dict | None = None) -> str:
    rec = {"message": message, "level": "Verbose", "timeStamp": ts, "processName": "OrderToInvoice",
           "jobId": "job-1", "robotName": "r"}  # fmt: skip
    if activity:
        rec["activityInfo"] = activity
    return f"{ts[11:24]} Verbose {json.dumps(rec)}"


def _activity(ts, end, kind, display, selector=None, args=None):
    base = {"Activity": f"UiPath.Core.Activities.{kind}", "DisplayName": display}
    if selector:
        base["Arguments"] = {"Target": {"Selector": selector}, **(args or {})}
    return [_log_line(ts, f"{display} Executing", dict(base, State="Executing")),
            _log_line(end, f"{display} Closed", dict(base, State="Closed"))]  # fmt: skip


def _session(tmp_path: Path) -> Path:
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "session.json").write_text(
        json.dumps(
            {
                "format": timeline.FORMAT,
                "id": "s",
                "name": "t",
                "state": "stopped",
                "started": "2026-11-20T10:00:00.000Z",
                "ended": "2026-11-20T10:05:00.000Z",
                "targets": [{"exe": "casedesk.exe"}],
                "apps": {},
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    (folder / "journal.jsonl").write_text(json.dumps({
        "ts": "2026-11-20T10:00:01.000Z", "kind": "app", "pid": 7, "surface": "desktop",
        "key": "desktop:Twin Lab reference/CaseDesk", "exe": "CaseDesk.exe", "level": "full"}) + "\n",
        encoding="utf-8")  # fmt: skip
    (folder / "desktop").mkdir()
    (folder / "desktop" / "steps.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "ts": "2026-11-20T10:01:00.300Z",
                "surface": "desktop",
                "action": "type",
                "pid": 7,
                "target": {"selector": "uia:AutomationId=txtSearch;ControlType=Edit"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    timeline.build(folder)
    return folder


def test_activities_in_the_session_and_the_gaps(tmp_path):
    folder = _session(tmp_path)
    lines = [_log_line("2026-11-20T09:59:00.0000000Z", "OrderToInvoice execution started")]
    lines += _activity("2026-11-20T10:01:00.0000000Z", "2026-11-20T10:01:00.4000000Z", "TypeInto",
                       "Type account 1000001", "<wnd app='casedesk.exe' /><ctrl automationid='txtSearch' />",
                       {"Text": "1000001"})  # fmt: skip
    lines += _activity("2026-11-20T10:02:00.0000000Z", "2026-11-20T10:02:00.2000000Z", "Click",
                       "Click Save", "<wnd app='casedesk.exe' /><ctrl automationid='btnSave' />")  # fmt: skip
    lines += _activity("2026-11-20T11:00:00.0000000Z", "2026-11-20T11:00:01.0000000Z", "Click",
                       "After the session", "<wnd app='casedesk.exe' />")  # fmt: skip
    log = tmp_path / "2026-11-20_Execution.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cov = robot.attach(folder, [log])
    rows = [
        json.loads(x)
        for x in (folder / "robot" / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [r["activity"] for r in rows] == ["TypeInto", "Click"]
    assert rows[0]["app"] == "casedesk.exe" and rows[0]["surface"] == "desktop"
    assert rows[0]["display"] == "Type account 9999999"
    text = (folder / "robot" / "steps.jsonl").read_text(encoding="utf-8")
    assert "1000001" not in text
    assert cov["ui_activities"] == 2 and cov["covered"] == 1
    assert [g["activity"] for g in cov["gaps"]] == ["Click"]
    # the outbox carries the counts only
    box = timeline.build(folder)
    assert box["robot"] == {"activities": 2, "ui_activities": 2, "covered": 1, "gaps": 1}


def test_no_robot_log_is_nothing(tmp_path):
    folder = _session(tmp_path)
    assert robot.attach(folder, []) is None
    assert not (folder / "robot").exists()
