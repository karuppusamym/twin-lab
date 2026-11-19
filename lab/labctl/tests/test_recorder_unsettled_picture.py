"""A recorder picture taken after the next action began (found replaying Demoblaze: the picture
after typing the message was taken once the click on Close had started the dialog's fade).

The picture is kept; the step says `picture_settled: false` with the reason, and the visual
draft makes that state optional and says why. An older recording, which does not say, is read
the same way when its next action began within the recorder's quiet time.
"""

from __future__ import annotations

import json

import yaml

from labctl.behaviour import visual
from labctl.recorder import mark_unsettled_pictures, next_action


def _steps():
    return [
        {"seq": 1, "ts": "2026-11-19T02:53:24.622Z", "action": "type", "tab": "t1",
         "screenshot": "screens/0001.png", "_shot_at": "2026-11-19T02:53:24.700Z"},
        {"seq": 2, "ts": "2026-11-19T02:53:25.149Z", "action": "type", "tab": "t1",
         "screenshot": "screens/0002.png", "_shot_at": "2026-11-19T02:53:25.190Z"},
        {"seq": 3, "ts": "2026-11-19T02:53:25.155Z", "action": "click", "tab": "t1",
         "screenshot": "screens/0003.png", "_shot_at": "2026-11-19T02:53:25.600Z"},
        {"seq": 4, "ts": "2026-11-19T02:53:25.400Z", "action": "dialog", "tab": "t1"},
        {"seq": 5, "ts": "2026-11-19T02:53:26.483Z", "action": "click", "tab": "t1"},
    ]  # fmt: skip


def test_a_picture_taken_after_the_next_action_began_is_kept_and_marked():
    steps = _steps()
    assert mark_unsettled_pictures(steps) == 1
    first, second, third = steps[:3]
    assert "picture_settled" not in first  # taken before the next action began
    assert second["picture_settled"] is False
    assert second["screenshot"] == "screens/0002.png"  # kept
    assert "step 3 (click)" in second["picture_note"] and "35 ms" in second["picture_note"]
    # a dialog holds the page while it is open: it is not the next action
    assert next_action(steps, 2)["seq"] == 5
    assert "picture_settled" not in third


def _recording(tmp_path, steps, quiet=400):
    rec = tmp_path / "rec"
    (rec / "screens").mkdir(parents=True)
    for s in steps:
        s.pop("_shot_at", None)
        if s.get("screenshot"):
            (rec / s["screenshot"]).write_bytes(b"")
    (rec / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), "utf-8")
    (rec / "recording.json").write_text(json.dumps({"settle": {"quiet_ms": quiet}}), "utf-8")
    return rec


def test_the_visual_draft_makes_an_unsettled_state_optional_with_the_reason(tmp_path):
    steps = _steps()
    mark_unsettled_pictures(steps)
    rec = _recording(tmp_path, steps)
    from labctl.botlog.recording import load

    states = {s["id"]: s for s in yaml.safe_load(visual.template(load(rec)))["states"]}
    assert states["after-step-1"]["required"] is True
    assert states["after-step-2"]["required"] is False
    assert "not settled" in states["after-step-2"]["note"]
    assert "step 3 (click)" in states["after-step-2"]["note"]
    assert states["after-step-3"]["required"] is True
    # the draft's note is no field the loader needs: the file still loads, the state optional
    (rec / "visual.yaml").write_text(visual.template(load(rec)), "utf-8")
    loaded = {s.id: s for s in visual.load(rec / "visual.yaml").states}
    assert loaded["after-step-2"].required is False and loaded["after-step-2"].problem is None


def test_an_older_recording_is_read_by_its_recorders_quiet_time(tmp_path):
    steps = _steps()  # not marked: an older recorder wrote no picture_settled
    rec = _recording(tmp_path, steps)
    found = visual.unsettled_pictures(rec)
    # step 2's next action came 6 ms later, inside the 400 ms quiet time; step 1's 527 ms later
    assert list(found) == [2]
    assert "6 ms" in found[2] and "400 ms" in found[2]
