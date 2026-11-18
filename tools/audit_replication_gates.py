"""Probe strict replication assurance gates; nonzero exit means an OPEN gap was reproduced.

Run: python tools/audit_replication_gates.py
Written for the audit of 18 November 2026 (docs/extensive-replication-audit-2026-11-18.md) and
kept as a probe: it exits 0 since the gates were fixed, and 1 if one opens again. The same cases
are regression tests in lab/labctl/tests/test_audit_gates.py.
Visual comparison results are injected to isolate aggregation/ID handling, not pixel comparison.
A01 and A02 are judged by the scoped verdict; A03 and A04 by the assurance tier beside it: a
scoped PASS stays possible there, VERIFIED COMPLETE does not.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lab/labctl"))

from labctl.behaviour import acceptance as acc  # noqa: E402
from labctl.behaviour import judging, visual  # noqa: E402
from labctl.behaviour.fidelity import Coverage  # noqa: E402


def audit():
    cov = Coverage(steps=1, replayed=1, compared=1, expected=1, captured=1)
    good = acc.CaseResult(id="good", check="text", category="behaviour", status="PASS")
    results = []

    def check(gap_id, title, cases, images=(), coverage=cov, kind="correctness"):
        categories = judging.categories(coverage, "pass", cases, list(images))
        verdict, reasons = judging.scoped(categories, cases, list(images))
        tier, tier_reasons = judging.assurance(verdict, categories, cases, list(images))
        if kind == "assurance-policy":
            expected = "not VERIFIED COMPLETE"
            open_gap = tier == judging.VERIFIED
        else:
            expected = "FAIL or BLOCKED"
            open_gap = verdict == judging.SCOPED_PASS
        results.append(
            {
                "id": gap_id,
                "kind": kind,
                "title": title,
                "expected": expected,
                "actual": verdict,
                "assurance": tier,
                "reasons": reasons + tier_reasons,
                "gap_reproduced": open_gap,
            }
        )

    case = acc.parse_case(
        {
            "id": "required-check",
            "check": "text",
            "category": "behavor",
            "locator": "#saved-value",
            "equals": "correct",
        },
        1,
    )
    bad = acc.CaseResult(
        id=case.id, check=case.check, category=case.category, status="BLOCKED", note=case.problem
    )
    check("A01", "Unknown category hides a required blocked case", [good, bad])

    with tempfile.TemporaryDirectory(prefix="twinlab-gate-audit-") as folder:
        root = Path(folder)
        path = root / "visual.yaml"
        path.write_text(
            "states:\n- {id: repeated, after: 1}\n- {id: repeated, after: 2}\n", encoding="utf-8"
        )
        spec = visual.load(path)
        judge = judging.Judging(
            SimpleNamespace(path=root, steps=[]),
            None,
            spec,
            binding=lambda _n: None,
            record=lambda _n: None,
        )
        samples = [
            visual.VisualResult(id="repeated", after="1", viewport="desktop", status="FAIL"),
            visual.VisualResult(id="repeated", after="2", viewport="desktop", status="PASS"),
        ]
        with patch.object(visual, "check_state", side_effect=samples):
            judge.at(None, 1)
            judge.at(None, 2)
        check(
            "A02",
            "Duplicate visual IDs overwrite a required failure",
            [],
            list(judge.visual.values()),
        )

    check(
        "A03",
        "Capture completeness is undeclared",
        [good],
        coverage=cov.model_copy(update={"expected": None}),
        kind="assurance-policy",
    )
    check(
        "A04",
        "Only required case is based on an assumption",
        [good.model_copy(update={"basis": "assumed"})],
        kind="assurance-policy",
    )
    return results


if __name__ == "__main__":
    results = audit()
    target = ROOT / ".lab-state/audit-2026-11-18/gate-probes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    raise SystemExit(1 if any(r["gap_reproduced"] for r in results) else 0)
