"""The build notes' windows: one opened across applications (a split recording's part), and one
a script opened and closed (recorded, not guessed)."""

from __future__ import annotations

from types import SimpleNamespace

from labctl import navmap


def ctx(steps: list[dict], raw: list[dict]):
    return SimpleNamespace(steps=lambda: steps, part=SimpleNamespace(raw=raw), name="rec")


def test_the_part_that_opened_another_applications_window_says_so():
    to = {"app": "billing", "step": 5, "tab": "t2", "url": "http://b.example.test/start"}
    steps = [{"n": 1, "action": "goto", "tab": "t1", "screen": "/home"},
             {"n": 2, "action": "click", "tab": "t1", "screen": "/home", "selector": "a#open"}]  # fmt: skip
    (row,) = navmap.windows([ctx(steps, [{"seq": 1}, {"seq": 2, "opens_outside": to}])])
    assert row["application"] == "billing" and row["shows"] == to["url"]
    assert row["opened_from"] == "/home" and row["opened_by"] == {
        "step": 2, "action": "click", "selector": "a#open"}  # fmt: skip


def test_the_part_another_application_opened_in_a_window_says_who():
    by = {"app": "orders", "step": 3, "tab": "t1", "element": "a#open"}
    steps = [{"n": 1, "action": "goto", "tab": "t2", "screen": "/start"},
             {"n": 2, "action": "click", "tab": "t2", "screen": "/start", "selector": "button"}]  # fmt: skip
    (row,) = navmap.windows([ctx(steps, [{"seq": 1, "opened_by_outside": by}, {"seq": 2}])])
    assert row["shows"] == "/start" and row["opened_by_outside"] == by and "opened_by" not in row
    # with its tab-open step in the part (the application used two tabs): one row, not two
    steps = [{"n": 1, "action": "goto", "tab": "t1", "screen": "/in"},
             {"n": 2, "action": "tab-open", "tab": "t2", "screen": "/in"},
             {"n": 3, "action": "goto", "tab": "t2", "screen": "/start"}]  # fmt: skip
    raw = [{"seq": 1}, {"seq": 2, "opened_by_outside": by}, {"seq": 3, "opened_by_outside": by}]
    (row,) = navmap.windows([ctx(steps, raw)])
    assert row["opened_at_step"] == 2 and row["opened_by_outside"] == by


def test_a_window_a_script_opened_and_closed_is_described_as_recorded():
    window = {
        "how": "window.open",
        "name": "picker",
        "features": {"width": 420, "resizable": False},
    }
    steps = [{"n": 1, "action": "click", "tab": "t1", "screen": "/lookup", "selector": "#pick"},
             {"n": 2, "action": "tab-open", "tab": "t2", "screen": "/lookup"},
             {"n": 3, "action": "goto", "tab": "t2", "screen": "/picker"},
             {"n": 4, "action": "click", "tab": "t2", "screen": "/picker", "selector": "#choose"},
             {"n": 5, "action": "tab-close", "tab": "t2", "screen": "/picker"}]  # fmt: skip
    raw = [{"seq": 1}, {"seq": 2, "opener": "t1", "window": window}, {"seq": 3}, {"seq": 4},
           {"seq": 5, "closed_by": "script"}]  # fmt: skip
    (row,) = navmap.windows([ctx(steps, raw)])
    assert row["window"] == window and row["opened_by"]["selector"] == "#pick"
    assert row["closed"]["note"] == (
        "a script closed the window (`window.close()`) right after this action"
    )
    assert navmap._window_text(window) == (
        "; recorded as a script's `window.open`, window name `picker`, "
        "features `width=420,resizable=no`"
    )
