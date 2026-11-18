"""Prove the three certification gates for Angular on the committed build of Caseboard.

    python reference/web/caseboard-angular/certify_local.py [--keep FOLDER] [--headed]

1. DETECT: the lab's technology detection, run on a recording of `flow.py`, names Angular, the
   version in `package.json`, and the marker that proved it.
2. CAPTURE: every action in `expected-actions.yaml` (written from the flow) is in the recording.
3. SELF-REPLAY: the recording replays against a fresh instance of the same build with no failed,
   blocked or skipped step.

Needs Python, the `labctl` package (this repository's `lab/labctl`), Playwright and Chromium. No
npm: `site/` is the production build, served by `serve.py`. Exit code 0 when all three pass.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
APP = "caseboard-angular"
FRAMEWORK = "angular"

if importlib.util.find_spec("labctl") is None:  # not installed and not on PYTHONPATH
    sys.path.insert(0, str(REPO / "lab" / "labctl"))


def _sibling(name: str):
    """A module beside this file, loaded under a name of its own (`flow` and `serve` are names
    other reference applications use too)."""
    full = f"caseboard_angular_{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


flow_mod = _sibling("flow")
serve_mod = _sibling("serve")


def angular_version() -> str:
    """The version the application was built with, as package.json pins it."""
    deps = json.loads((HERE / "package.json").read_text("utf-8"))["dependencies"]
    return deps["@angular/core"].lstrip("^~")


def expected_actions() -> list[str]:
    data = yaml.safe_load((HERE / "expected-actions.yaml").read_text("utf-8"))
    return [str(a) for a in data["actions"]]


def steps_of(rec: Path) -> list[dict]:
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def record(base_url: str, out: Path, headless: bool = True) -> Path:
    """Record `flow.py` with the lab's recorder and write the bindings a replay needs."""
    from labctl import recorder

    recorder.record(
        base_url + flow_mod.START,
        out,
        headless=headless,
        script=lambda page: flow_mod.flow(page, base_url),
    )
    bind(out)
    return out


def bind(rec: Path) -> None:
    """bindings.yaml: the value of every step that entered one, from `flow.INPUTS`, in order.
    The steps are the ones the lab says need a value; a mismatch with the flow is an error."""
    from labctl.behaviour import bindings
    from labctl.botlog.recording import load

    every = bindings.needs(load(rec))
    needs = [n for n in every if n.kind == "value"]
    records = [n for n in every if n.kind == "record"]
    by_seq = {s["seq"]: s for s in steps_of(rec)}
    got = [(n.action, by_seq[n.seq].get("selector")) for n in needs]
    want = [(action, selector) for action, selector, _ in flow_mod.INPUTS]
    if got != want:
        raise AssertionError(f"steps that need a value: recorded {got}, the flow enters {want}")
    if [n.action for n in records] != [action for action, _ in flow_mod.RECORDS]:
        raise AssertionError(
            f"steps on a record: recorded {records}, the flow has {flow_mod.RECORDS}"
        )
    steps: dict[int, dict] = {
        n.seq: {"value": value} for n, (_, _, value) in zip(needs, flow_mod.INPUTS, strict=True)
    }
    for n, (_, title) in zip(records, flow_mod.RECORDS, strict=True):
        steps.setdefault(n.seq, {})["record"] = title
    (rec / "bindings.yaml").write_text(yaml.safe_dump({"version": 1, "steps": steps}), "utf-8")


def detect(rec: Path) -> dict:
    """What the lab's detection says of the front end (framework, version, rendering, marker)."""
    from labctl import har

    return dict(har.analyse(APP, rec, REPO).stack.frontend or {})


def replay(rec: Path, source_base: str, replica_base: str, headless: bool = True):
    from labctl.behaviour import fidelity

    return fidelity.replay(
        rec,
        origins={source_base: replica_base},
        expected=expected_actions(),
        headless=headless,
        resolve_timeout_ms=5000,
    )


@dataclass
class Result:
    recording: Path
    frontend: dict
    expected: int
    captured: int
    missing: list[str]
    report: object  # labctl.behaviour.fidelity.FidelityReport

    @property
    def detect_ok(self) -> bool:
        return (
            self.frontend.get("framework") == FRAMEWORK
            and self.frontend.get("version") == angular_version()
            and bool(self.frontend.get("marker"))
        )

    @property
    def capture_ok(self) -> bool:
        return self.expected > 0 and not self.missing

    @property
    def not_passed(self) -> list:
        return [s for s in self.report.steps if s.status != "pass"]

    @property
    def replay_ok(self) -> bool:
        cov = self.report.coverage
        return (
            self.capture_ok
            and self.report.status == "pass"
            and not self.not_passed
            and cov.replayed == cov.steps > 0
            and not self.report.blocked_requests
        )

    @property
    def ok(self) -> bool:
        return self.detect_ok and self.capture_ok and self.replay_ok

    def lines(self) -> list[str]:
        f, cov = self.frontend, self.report.coverage
        mark = {True: "PASS", False: "FAIL"}
        out = [
            f"DETECT       {mark[self.detect_ok]}  framework={f.get('framework')} "
            f"version={f.get('version')} (built with {angular_version()}) "
            f"rendering={f.get('rendering')} marker={f.get('marker')}",
            f"CAPTURE      {mark[self.capture_ok]}  expected={self.expected} "
            f"captured={self.expected - len(self.missing)} missing={self.missing} "
            f"(actions in the recording: {self.captured})",
            f"SELF-REPLAY  {mark[self.replay_ok]}  status={self.report.status} steps={cov.steps} "
            f"replayed={cov.replayed} (implied {cov.implied}) compared={cov.compared} "
            f"failed={cov.failed} blocked={cov.unresolved + cov.missing_value + cov.unsupported} "
            f"skipped={cov.skipped}",
        ]
        first = next(iter(self.not_passed), None)
        if first is not None:
            out.append(
                f"  first step not passed: {first.seq} {first.action} {first.selector or first.url}"
                f" [{first.status}/{first.outcome}] {first.note or ''}"
            )
            out += [f"    - {d.text()}" for d in first.differences[:12]]
        out += [f"  problem: {p}" for p in self.report.problems]
        return out


def certify(work: Path, headless: bool = True) -> Result:
    """Run the three gates; the recording is written to `work/recording`."""
    from labctl.behaviour import fidelity
    from labctl.botlog.recording import load

    source = serve_mod.serve()
    try:
        rec = record(source.base, Path(work) / "recording", headless)
    finally:
        source.stop()
    expected = expected_actions()
    captured = fidelity.captured_actions(load(rec))
    replica = serve_mod.serve()  # a fresh instance: nothing of the recording run is in it
    try:
        report = replay(rec, source.base, replica.base, headless)
    finally:
        replica.stop()
    return Result(
        recording=rec,
        frontend=detect(rec),
        expected=len(expected),
        captured=len(captured),
        missing=list(report.coverage.missing_expected),
        report=report,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", type=Path, help="keep the recording and the report here")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="caseboard-angular-") as tmp:
        work = args.keep or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        result = certify(work, headless=not args.headed)
        if args.keep:
            (work / "replay.md").write_text(result.report.markdown(), "utf-8")
        print(f"Caseboard (Angular {angular_version()}), build in {serve_mod.SITE}")
        print("\n".join(result.lines()))
        print("CERTIFIED" if result.ok else "NOT CERTIFIED")
        return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
