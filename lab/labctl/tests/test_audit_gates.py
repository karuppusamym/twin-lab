"""The verdict gates the audit of 18 November 2026 found open
(docs/extensive-replication-audit-2026-11-18.md, A01 to A04), as regression tests.

A01  a required case with a misspelled category still decides the result
A02  two visual states with one id are both kept: a pass cannot erase a failure
A03  a PASS without an expected action list is not VERIFIED COMPLETE
A04  a PASS resting on an assumed required case is not VERIFIED COMPLETE
and  a report never says status blocked and verdict PASS together
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from labctl.behaviour import acceptance as acc
from labctl.behaviour import fidelity, judging, visual
from labctl.behaviour.fidelity import Coverage

COV = Coverage(steps=1, replayed=1, compared=1, expected=1, captured=1)


def good(**kw) -> acc.CaseResult:
    return acc.CaseResult(id="good", check="text", category="behaviour", status="PASS", **kw)


def judge(cases, images=(), coverage=COV):
    cats = judging.categories(coverage, "pass", cases, list(images))
    verdict, reasons = judging.scoped(cats, cases, list(images))
    tier, tier_reasons = judging.assurance(verdict, cats, cases, list(images))
    return cats, verdict, reasons, tier, tier_reasons


def test_a01_a_required_case_with_a_misspelled_category_blocks() -> None:
    case = acc.parse_case(
        {"id": "required-check", "check": "text", "category": "behavor",
         "locator": "#saved-value", "equals": "correct"}, 1)  # fmt: skip
    assert case.problem and "unknown category" in case.problem
    bad = acc.CaseResult(id=case.id, check=case.check, category=case.category,
                         status="BLOCKED", note=case.problem)  # fmt: skip
    cats, verdict, reasons, tier, _ = judge([good(), bad])
    assert verdict == judging.SCOPED_BLOCKED and tier is None
    # the case stays in the count and is named
    assert cats["behaviour"].required_total == 2 and cats["behaviour"].required_passed == 1
    assert "required-check" in " ".join(reasons) and "behavor" in " ".join(reasons)
    # a failed one decides too
    failed = bad.model_copy(update={"status": "FAIL"})
    assert judge([good(), failed])[1] == judging.SCOPED_FAIL


def test_a_required_result_decides_even_outside_every_category() -> None:
    # filed where no category looks (a visual result with a foreign status holder)
    stray = SimpleNamespace(id="stray", required=True, status="BLOCKED", category="nowhere")
    cats = {n: judging.Category(status="PASS") for n in judging.CATEGORY_NAMES}
    verdict, reasons = judging.scoped(cats, [good(), stray], [])
    assert verdict == judging.SCOPED_BLOCKED and "stray" in " ".join(reasons)


def test_a02_duplicate_visual_ids_keep_both_results(tmp_path) -> None:
    path = tmp_path / "visual.yaml"
    path.write_text(
        "states:\n- {id: repeated, after: 1}\n- {id: repeated, after: 2}\n", encoding="utf-8"
    )
    spec = visual.load(path)
    ids = [s.id for s in spec.states]
    assert len(set(ids)) == 2 and ids[0] == "repeated"
    assert spec.states[1].problem and "used twice" in spec.states[1].problem

    j = judging.Judging(SimpleNamespace(path=tmp_path, steps=[]), None, spec,
                        binding=lambda _n: None, record=lambda _n: None)  # fmt: skip
    samples = [
        visual.VisualResult(id="repeated", after="1", viewport="desktop", status="FAIL"),
        visual.VisualResult(id="repeated", after="2", viewport="desktop", status="PASS"),
    ]
    with patch.object(visual, "check_state", side_effect=samples):
        j.at(None, 1)
        j.at(None, 2)
    results = list(j.visual.values())
    assert [r.status for r in results] == ["FAIL", "PASS"]  # the failure is still there
    cats, verdict, _, _, _ = judge([], results)
    assert verdict == judging.SCOPED_FAIL and cats["appearance"].required_total == 2


def test_two_results_under_one_key_are_never_merged(tmp_path) -> None:
    # even when a spec was built without the loader's check
    spec = visual.Spec(path=tmp_path / "v.yaml", states=[
        visual.State(id="same", after=1), visual.State(id="same", after=1)])  # fmt: skip
    j = judging.Judging(SimpleNamespace(path=tmp_path, steps=[]), None, spec,
                        binding=lambda _n: None, record=lambda _n: None)  # fmt: skip
    samples = [
        visual.VisualResult(id="same", after="1", viewport="desktop", status="FAIL"),
        visual.VisualResult(id="same", after="1", viewport="desktop", status="PASS"),
    ]
    with patch.object(visual, "check_state", side_effect=samples):
        j.at(None, 1)
    assert sorted(j.visual) == ["same", "same#2"]


def test_a03_a_pass_without_an_expected_action_list_is_not_verified() -> None:
    _, verdict, _, tier, why = judge([good()], coverage=COV.model_copy(update={"expected": None}))
    assert verdict == judging.SCOPED_PASS  # the declared cases did pass
    assert tier == judging.DECLARED_ONLY and "expected action list" in why[0]
    # with the list declared and nothing missing, and evidence behind the case: verified
    _, verdict, _, tier, why = judge([good()])
    assert (verdict, tier, why) == (judging.SCOPED_PASS, judging.VERIFIED, [])


def test_a04_a_pass_resting_on_an_assumption_is_not_verified() -> None:
    _, verdict, _, tier, why = judge([good(basis="assumed")])
    assert verdict == judging.SCOPED_PASS
    assert tier == judging.DECLARED_ONLY and "assumption" in why[0] and "good" in why[0]
    # an optional assumed case does not hold the tier back
    extra = acc.CaseResult(id="hint", check="text", category="behaviour", status="PASS",
                           required=False, basis="assumed")  # fmt: skip
    assert judge([good(), extra])[3] == judging.VERIFIED


def test_no_tier_without_a_pass() -> None:
    failed = acc.CaseResult(id="bad", check="text", category="behaviour", status="FAIL")
    assert judge([failed])[3] is None


def test_a_report_never_says_blocked_and_pass_together() -> None:
    report = fidelity.FidelityReport(recording="r", base_url="http://x", status="blocked",
                                     steps=[], coverage=COV)  # fmt: skip
    judging_run = SimpleNamespace(cases={"good": good()}, visual={})
    rec = SimpleNamespace(steps=[], path=None, id="r")
    with patch.object(fidelity, "_structural", return_value="pass"), \
         patch("labctl.behaviour.judging.first_divergence", return_value=None):  # fmt: skip
        fidelity._scope(report, judging_run, None, rec, {})
    assert report.status == "blocked"
    assert report.verdict == judging.SCOPED_BLOCKED and report.assurance is None
    assert "the replay's status is blocked" in report.verdict_reasons
    text = report.markdown()
    assert "PASS WITHIN DECLARED SCOPE" not in text


def test_the_report_names_the_assurance_tier() -> None:
    report = fidelity.FidelityReport(recording="r", base_url="http://x", status="pass",
                                     steps=[], coverage=COV.model_copy(update={"expected": None}),
                                     verdict=judging.SCOPED_PASS,
                                     assurance=judging.DECLARED_ONLY,
                                     assurance_reasons=["capture completeness is not verified"])  # fmt: skip
    text = report.markdown()
    assert "**Assurance: DECLARED CASES ONLY**" in text and "capture completeness" in text
