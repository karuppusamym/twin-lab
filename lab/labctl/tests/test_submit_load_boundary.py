"""A submit whose page load the recorder did not attribute (docs/multi-app-recording-trial.md,
the source-against-source failure at the two submit steps): the load right after a submit is its
result, so the submit is compared at that page and not by the form snapshot taken before it."""

from __future__ import annotations

import json

from labctl.behaviour import fidelity
from labctl.botlog.recording import load

FORM = "https://app.test/form.html"
DONE = "https://app.test/submitted.html?name=x"


def _recording(tmp_path, steps):
    folder = tmp_path / "rec"
    (folder / "dom").mkdir(parents=True)
    for s in steps:
        for key in ("dom", "dom_before"):
            if s.get(key):
                (folder / s[key]).write_text("<html><body></body></html>", encoding="utf-8")
    (folder / "steps.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8"
    )
    return load(folder)


def _steps(load_gap_ms: int, nav: str = "navigate"):
    return [
        {"seq": 1, "ts": "2026-11-18T04:04:11.000Z", "url": FORM, "action": "goto",
         "dom": "dom/0001.html", "nav": "navigate"},
        {"seq": 2, "ts": "2026-11-18T04:04:27.664Z", "url": FORM, "action": "click",
         "selector": "button", "dom": None, "dom_before": "dom/0002.before.html",
         "locators": [{"kind": "css", "css": "button"}]},
        {"seq": 3, "ts": "2026-11-18T04:04:27.665Z", "url": FORM, "action": "submit",
         "selector": "form", "dom": "dom/0003.html", "caused_by": 2},
        {"seq": 4, "ts": f"2026-11-18T04:04:{27.665 + load_gap_ms / 1000:06.3f}Z", "url": DONE,
         "action": "goto", "dom": "dom/0004.html", "nav": nav},
    ]  # fmt: skip


def test_a_load_right_after_a_submit_is_its_result(tmp_path):
    rec = _recording(tmp_path, _steps(66))
    assert rec.steps[3].caused_by is None  # as the trial's recorder wrote it
    fidelity._infer_causes(rec)
    assert rec.steps[3].caused_by == 2  # the click that submitted
    # the click and the submit are compared at the loaded page, not by the form snapshot
    assert fidelity._observed_at_navigation(rec) == {2: 4, 3: 4}


def test_a_later_load_or_a_history_move_is_not_attributed(tmp_path):
    late = _recording(tmp_path / "late", _steps(2300))
    fidelity._infer_causes(late)
    assert late.steps[3].caused_by is None and not fidelity._observed_at_navigation(late)
    back = _recording(tmp_path / "back", _steps(66, nav="back_forward"))
    fidelity._infer_causes(back)
    assert back.steps[3].caused_by is None
