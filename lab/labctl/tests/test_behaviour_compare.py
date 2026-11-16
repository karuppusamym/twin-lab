"""UAT against production (I-22b): behaviours compared rule by rule."""

from __future__ import annotations

from pathlib import Path

from labctl.behaviour import compare
from labctl.behaviour.catalog import Catalog

FIX = Path(__file__).parent / "fixtures" / "botlog" / "recordings" / "showcase"


def rule(kind, element, target, detail=None, event="click", screen="/cases"):
    return {"id": f"{kind}-{element}-{target}", "kind": kind, "screen": screen,
            "trigger": {"element": element, "event": event}, "target": target,
            "detail": detail or {}, "observations": 2, "single_observation": False,
            "evidence": [{"recording": "r", "step": 2, "before": "dom/1.html", "after": "dom/2.html"}]}  # fmt: skip


def cat(*rules):
    return Catalog.model_validate({"rules": list(rules)})


def test_same_behaviour_and_each_kind_of_difference():
    a = cat(
        rule("show", "#caseStatus", "#escalationReason", event="change"),
        rule("set_options", "#category", "#subcategory", {"options": ["Refund", "Delayed"]}, "change"),
        rule("format", "#caseNo", "#caseNo", {"shape": "A-9"}, "input"),
        rule("delay", "#btnSearch", "#results", {"ms": 100}),
        rule("disable", "#btnSave", "#btnSave"),
    )  # fmt: skip
    b = cat(
        rule("show", "#caseStatus", "#escalationReason", event="change"),
        rule("set_options", "#category", "#subcategory", {"options": ["Refund"]}, "change"),
        rule("delay", "#btnSearch", "#results", {"ms": 180}),  # within a factor of two
        rule("hide", "#caseNo", "#hint", event="input"),  # the trigger exists in both
    )  # fmt: skip
    result = compare.compare(a, b, "UAT", "production")
    kinds = {(d.kind, d.rule.split(",")[0]) for d in result.differences}
    assert result.same == 2  # the escalation field and the delay agree
    assert ("differs", "When #category changes") in kinds
    assert ("only_a", "When #caseNo is typed into") in kinds  # the mask: B typed there, no mask
    assert ("only_b", "When #caseNo is typed into") in kinds
    assert ("not_exercised", "When #btnSave is clicked") in kinds  # B never clicked Save
    assert result.status == "different"
    text = result.markdown()
    assert "**DIFFERENT**" in text and "Seen only in UAT" in text


def test_delays_far_apart_differ_and_identical_sets_are_the_same():
    a = cat(rule("delay", "#btnSearch", "#results", {"ms": 100}))
    b = cat(rule("delay", "#btnSearch", "#results", {"ms": 900}))
    assert compare.compare(a, b).differences[0].kind == "differs"
    recs = sorted(p for p in FIX.iterdir() if p.is_dir())
    same = compare.compare_recordings(recs[:2], recs[:2], "A", "B")
    assert same.status == "same" and same.same > 0
