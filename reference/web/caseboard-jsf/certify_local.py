"""The three certification gates for JSF, run locally on Caseboard.

    python reference/web/caseboard-jsf/certify_local.py [--keep FOLDER]

1. DETECT: the recording is analysed (labctl.model_build.from_recording) and must name JSF as
   the page framework and the server framework, with the implementation and the specification
   version where the pages show them.
2. CAPTURE: every action of expected-actions.yaml is in the recording, in order.
3. SELF-REPLAY: the recording replays against a FRESH instance of the same application with
   no failed step and no difference.

Prints what each gate found and exits 0 only when all three pass. `labctl` is imported from
this checkout (lab/labctl), whatever is installed.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "lab" / "labctl"))

import yaml  # noqa: E402


def _sibling(name: str):
    """A module of this folder, loaded by its path under a name of its own: `flow`, `run` and
    `build` are names other folders of the repository use too."""
    import importlib.util

    full = f"caseboard_jsf_{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


flow_mod = _sibling("flow")
run_mod = _sibling("run")
APP = "caseboard-jsf"
# What Caseboard's pages show of JSF: the implementation by its script's own namespace, and the
# specification it implements. The implementation's release (2.3.9) is in no page or header.
EXPECT_IMPLEMENTATION, EXPECT_VERSION = "Mojarra", "2.3"


def expected_actions() -> list[str]:
    doc = yaml.safe_load((HERE / "expected-actions.yaml").read_text("utf-8"))
    return [str(a) for a in doc["actions"]]


def steps_of(rec: Path) -> list[dict]:
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def record(base: str, folder: Path) -> Path:
    """Record flow.py with the lab's recorder, then give each step that types or chooses from a
    list its synthetic value (bindings.yaml), as a person does before a replay: the recorder
    never writes those values."""
    from labctl import recorder

    recorder.record(base + flow_mod.START, folder, headless=True,
                    script=lambda page: flow_mod.flow(page, base))  # fmt: skip
    typed = [s["seq"] for s in steps_of(folder) if s["action"] in ("type", "select")]
    (folder / "bindings.yaml").write_text(
        yaml.safe_dump(flow_mod.bindings_for(typed), sort_keys=False), "utf-8"
    )
    (folder / "acceptance.yaml").write_text(
        yaml.safe_dump(flow_mod.acceptance_for(steps_of(folder)), sort_keys=False), "utf-8"
    )
    return folder


def detect(rec: Path, work: Path) -> dict:
    """Gate 1. What the lab says the recording's technology is."""
    from labctl import model_build

    stack = model_build.from_recording(APP, rec, work, None).stack
    front, server = stack.frontend or {}, stack.server or {}
    libraries = {x["name"]: x for x in stack.libraries}
    found = {
        "framework": front.get("framework"),
        "rendering": front.get("rendering"),
        "marker": front.get("marker"),
        "version": server.get("framework_version"),
        "implementation": server.get("implementation"),
        "implementation_marker": server.get("implementation_marker"),
        "server_technology": server.get("technology"),
        "server_framework": server.get("framework"),
        "server_confidence": server.get("confidence"),
        "server_marker": server.get("framework_marker") or server.get("marker"),
        "web_server": (server.get("web_server") or {}).get("name"),
        "web_server_version": (server.get("web_server") or {}).get("version"),
        "libraries": sorted(libraries),
        "markers": sorted(e["marker"] for e in stack.detected),
    }
    found["ok"] = (
        found["framework"] == "JSF"
        and found["server_technology"] == "Java"
        and found["server_framework"] == "JSF"
        and found["server_confidence"] == "certain"
        and found["implementation"] == EXPECT_IMPLEMENTATION
        and found["version"] == EXPECT_VERSION
    )
    return found


def capture(rec: Path) -> dict:
    """Gate 2. Which expected actions the recording holds."""
    from labctl.behaviour import fidelity
    from labctl.botlog.recording import load

    expected = expected_actions()
    captured = fidelity.captured_actions(load(rec))
    missing = fidelity.missing_actions(expected, captured)
    return {"expected": len(expected), "captured": len(captured), "missing": missing,
            "actions": captured, "ok": not missing}  # fmt: skip


def replay(rec: Path, source_base: str, replica_base: str):
    """Gate 3. The recording against another instance; the report is labctl's own."""
    from labctl.behaviour import fidelity

    return fidelity.replay(rec, origins={source_base: replica_base}, expected=expected_actions(),
                           resolve_timeout_ms=5000)  # fmt: skip


def replay_summary(report) -> dict:
    cov = report.coverage
    failing = next((s for s in report.steps if s.status in ("fail", "blocked")), None)
    differences = [d.text() for s in report.steps for d in s.differences]
    return {
        "status": report.status,
        "verdict": report.verdict,
        "cases": {c.id: c.status for c in report.cases},
        "steps": cov.steps,
        "replayed": cov.replayed,
        "compared": cov.compared,
        "failed": cov.failed,
        "skipped": cov.skipped,
        "blocked": cov.unresolved + cov.missing_value + cov.unsupported + cov.unmapped,
        "differences": differences,
        "first_failing_step": None if failing is None else {
            "seq": failing.seq, "action": failing.action, "selector": failing.selector,
            "status": failing.status, "outcome": failing.outcome, "note": failing.note,
            "differences": [d.text() for d in failing.differences][:5],
        },
        "problems": list(report.problems),
        "blocked_requests": list(report.blocked_requests),
        "ok": report.status == "pass" and cov.failed == 0 and cov.skipped == 0
        and not differences and cov.replayed == cov.steps
        and bool(report.cases) and all(c.status == "PASS" for c in report.cases),
    }  # fmt: skip


def run_gates(work: Path) -> dict:
    """Record on one instance, analyse, and replay on a fresh one. Returns the three results
    (`detect`, `capture`, `replay`, each with `ok`) and the recording folder."""
    work = Path(work)
    rec = work / "recording"
    source = run_mod.serve()
    try:
        record(source.base, rec)
    finally:
        source.stop()
    result = {"recording": str(rec), "detect": detect(rec, work), "capture": capture(rec)}
    fresh = run_mod.serve()  # nothing of the recording session is in it
    try:
        report = replay(rec, source.base, fresh.base)
    finally:
        fresh.stop()
    (work / "replay-report.md").write_text(report.markdown(), "utf-8")
    result["replay"] = replay_summary(report)
    result["ok"] = all(result[g]["ok"] for g in ("detect", "capture", "replay"))
    return result


def print_result(result: dict) -> None:
    d, c, r = result["detect"], result["capture"], result["replay"]
    mark = {True: "PASS", False: "FAIL"}
    print(f"DETECT       {mark[d['ok']]}")
    print(f"  framework        {d['framework']} ({d['rendering']} rendering)")
    print(f"  implementation   {d['implementation']} [{d['implementation_marker']}]")
    print(f"  version          {d['version']}")
    print(f"  marker           {d['marker']}")
    print(f"  server           {d['server_technology']} / {d['server_framework']}"
          f" ({d['server_confidence']}: {d['server_marker']})")  # fmt: skip
    print(f"  web server       {d['web_server']} {d['web_server_version'] or ''}")
    print(f"  libraries        {', '.join(d['libraries']) or '-'}")
    for m in d["markers"]:
        print(f"    seen: {m}")
    print(f"CAPTURE      {mark[c['ok']]}")
    print(f"  expected {c['expected']}, captured {c['captured']} semantic actions, "
          f"missing {len(c['missing'])}{': ' + ', '.join(c['missing']) if c['missing'] else ''}")  # fmt: skip
    print(f"SELF-REPLAY  {mark[r['ok']]}")
    print(f"  status {r['status']}: steps {r['steps']}, replayed {r['replayed']}, compared "
          f"{r['compared']}, failed {r['failed']}, skipped {r['skipped']}, blocked {r['blocked']}, "
          f"differences {len(r['differences'])}")  # fmt: skip
    passed = sum(1 for v in r["cases"].values() if v == "PASS")
    print(f"  acceptance cases {passed} of {len(r['cases'])} passed; verdict: {r['verdict']}")
    for case, status in r["cases"].items():
        if status != "PASS":
            print(f"  case {case}: {status}")
    if r["first_failing_step"]:
        print(f"  first failing step: {json.dumps(r['first_failing_step'], indent=2)}")
    for p in r["problems"][:10]:
        print(f"  problem: {p}")
    for b in r["blocked_requests"][:10]:
        print(f"  refused request: {b}")
    print(f"recording: {result['recording']}")
    print("ALL GATES PASS" if result["ok"] else "NOT CERTIFIED")


def main(argv: list[str]) -> int:
    reason = run_mod.build_mod.unavailable()
    if reason:
        print(f"cannot run: {reason}")
        return 2
    if "--keep" in argv:
        work = Path(argv[argv.index("--keep") + 1]).resolve()
        work.mkdir(parents=True, exist_ok=True)
        result = run_gates(work)
    else:
        with tempfile.TemporaryDirectory(prefix="caseboard-jsf-") as tmp:
            result = run_gates(Path(tmp))
        result["recording"] = "removed with its temporary folder (keep it with --keep FOLDER)"
    print_result(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
