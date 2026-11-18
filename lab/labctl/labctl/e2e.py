"""End to end in one command (`labctl e2e <automation>`): everything between a built app and a
judged bot run, on a lab copy made for it, with a report saying which stage passed.

1. **contracts**: every contract file in the repository is valid (`labctl validate`);
2. **lab**: the app's lab runs (started, and its images built, when it does not);
3. **copy**: a copy of that lab for this run, from the lab's baseline (`labctl lab copy`);
4. **readiness**: the automation is READY against the copy (`labctl conformance --on`);
5. **bot run**: the harness chain on the copy: reset, condition, session around the harness bot,
   robot log, verdict on row changes, provenance, parity with a production run when named;
6. **fidelity**: a recording replayed against the copy, kept for the readiness gate. The newest
   recording of the real application in `evidence/<app>/` when there is one, else the harness
   bot's own recording from stage 5 (which shows the replay works, not that the app is faithful);
7. **cleanup**: the copy is dropped (unless kept).

Overall PASS needs every stage to pass; a stage that cannot run is SKIP and says why.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Stage:
    name: str
    status: str = "SKIP"  # PASS | FAIL | SKIP
    detail: str = ""
    seconds: float = 0.0
    link: str | None = None


@dataclass
class E2EResult:
    automation: str
    app: str | None = None
    lab: str | None = None
    status: str = "FAIL"
    stages: list[Stage] = field(default_factory=list)
    out: str = ""

    def stage(self, name: str) -> Stage:
        s = Stage(name)
        self.stages.append(s)
        return s

    def markdown(self) -> str:
        lines = [f"# End to end: {self.automation} — {self.status}", "",
                 f"App `{self.app}`" + (f", run on the copy `{self.lab}`" if self.lab else "") + ".",
                 "", "| Stage | Result | Took | Detail |", "|---|---|---|---|"]  # fmt: skip
        for s in self.stages:
            detail = s.detail.replace("\n", " ").replace("|", "/")
            lines.append(f"| {s.name} | {s.status} | {s.seconds:.0f} s | {detail} |")
        links = [f"- {s.name}: `{s.link}`" for s in self.stages if s.link]
        if links:
            lines += ["", "Reports:", "", *links]
        return "\n".join(lines) + "\n"


class _Timer:
    def __init__(self, stage: Stage):
        self.stage = stage

    def __enter__(self):
        self.t = time.monotonic()
        return self.stage

    def __exit__(self, exc_type, exc, tb):
        self.stage.seconds = time.monotonic() - self.t
        if exc is not None and not isinstance(exc, KeyboardInterrupt):
            self.stage.status = "FAIL"
            self.stage.detail = (self.stage.detail + " " if self.stage.detail else "") + str(exc)[
                :600
            ]
            return True  # recorded; the next stages decide whether they can run
        return False


def _newest_recording(root: Path, app_id: str) -> Path | None:
    from labctl import factory

    folder = factory.evidence_dir(root, app_id)
    found = [d for d in folder.iterdir() if (d / "steps.jsonl").exists()] if folder.is_dir() else []
    return max(found, key=lambda d: (d / "steps.jsonl").stat().st_mtime) if found else None


def run(
    root: Path,
    automation_id: str,
    condition: str | None = None,
    golden: str | None = None,
    build: bool = True,
    keep: bool = False,
    out: Path | None = None,
    headless: bool = True,
    say=print,
    inputs: list[dict] | None = None,
    case: str | None = None,
) -> E2EResult:
    """`inputs`: the bot's runs (one dict each); default `automations/<id>/e2e-inputs.json` when
    it exists, else the automation's test_inputs."""
    from labctl import bindings as bnd
    from labctl import conformance, copies, harness, repo
    from labctl import lab as lab_mod
    from labctl.models import Automation

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(out or lab_mod.state_dir(root) / "e2e" / automation_id / stamp)
    out.mkdir(parents=True, exist_ok=True)
    result = E2EResult(automation_id, out=str(out))

    def ok(stage: str) -> bool:
        return any(s.name == stage and s.status == "PASS" for s in result.stages)

    with _Timer(result.stage("contracts")) as s:
        say("1/7 contracts")
        files = repo.contract_files(root)
        problems = [p for f in files for p in repo.load(f)[1]]
        s.status = "FAIL" if problems else "PASS"
        s.detail = f"{len(files)} file(s), {len(problems)} problem(s)" + (
            ": " + "; ".join(map(str, problems[:3])) if problems else ""
        )
    automation, problems = repo.load(root / "automations" / automation_id / "automation.yaml")
    if not isinstance(automation, Automation):
        result.stages[-1].status = "FAIL"
        result.stages[-1].detail = f"automation {automation_id}: " + "; ".join(map(str, problems))
        return _finish(result, out)
    web = [t.app for t in automation.touches if t.via == "web"]
    app_id = web[0] if web else (automation.apps() or [None])[0]
    result.app = app_id
    if app_id is None:
        result.stages[-1].detail += "; the automation touches no app"
        result.stages[-1].status = "FAIL"
        return _finish(result, out)

    with _Timer(result.stage("lab")) as s:
        say(f"2/7 lab {app_id}")
        state = lab_mod.status(root, app_id)
        if state.running and not build:
            s.detail = "running"
        else:
            lab_mod.up(root, app_id, build=build)
            state = lab_mod.status(root, app_id)
            s.detail = "started" + (" (images built)" if build else "")
        s.status = "PASS" if state.running else "FAIL"
        if not state.running:
            s.detail += f"; not running: {state.error or state.services}"

    lab_id = None
    if ok("lab"):
        with _Timer(result.stage("copy")) as s:
            say("3/7 copy")
            rec = copies.create(root, app_id, build=False)
            lab_id = result.lab = rec["lab"]
            s.status, s.detail = "PASS", f"{lab_id} on port {rec['port']}, data: {rec['data']}"
    labs = {app_id: lab_id} if lab_id else {}

    if lab_id:
        with _Timer(result.stage("readiness")) as s:
            say("4/7 readiness")
            report = conformance.check(root, automation, labs=labs)
            (out / "readiness.txt").write_text(conformance.render(report) + "\n", encoding="utf-8")
            s.status = "PASS" if report.verdict == "READY" else "FAIL"
            c = report.counts()
            s.detail = f"{report.verdict}: " + ", ".join(f"{v} {k}" for k, v in c.items())
            s.link = str(out / "readiness.txt")

    chain = None
    if lab_id and web:
        with _Timer(result.stage("bot run")) as s:
            say("5/7 bot run" + (f" under {condition}" if condition else ""))
            if (
                inputs is None
                and (root / "automations" / automation_id / "e2e-inputs.json").exists()
            ):
                given = json.loads((root / "automations" / automation_id / "e2e-inputs.json")
                                   .read_text(encoding="utf-8-sig"))  # fmt: skip
                inputs = given if isinstance(given, list) else [given]
            chain = harness.run_chain(root, automation_id, inputs, condition=condition, golden=golden,
                                      out=out / "harness", headless=headless, on=lab_id, case=case)  # fmt: skip
            v = chain.verdict or {}
            s.status = chain.status
            s.detail = (f"{len(chain.runs)} run(s), verdict {v.get('status')}"
                        + f", {outcome_detail(chain.outcome)}"
                        + (f", parity {chain.parity}" if chain.parity else "")
                        + (f", session {chain.session_id}" if chain.session_id else ""))  # fmt: skip
            s.link = str(out / "harness" / "report.md")
    elif lab_id:
        result.stage("bot run").detail = "no web steps: the harness bot drives screens"

    if lab_id and web:
        with _Timer(result.stage("fidelity")) as s:
            say("6/7 fidelity")
            from labctl.behaviour import fidelity

            recording = _newest_recording(root, app_id)
            own = recording is None
            if own and chain and chain.runs and chain.runs[0].get("recording"):
                recording = Path(chain.runs[0]["recording"])
            if recording is None:
                s.detail = "no recording to replay"
            else:
                app = bnd.load_app(root, lab_id)
                creds = app.lab.credentials if app.lab else {}
                given = {sel: creds["password"] for sel in
                         ("input[type=password]", "input#password", 'input[name="password"]')
                         if creds.get("password")}  # fmt: skip
                lab_mod.agent(root, lab_id).reset()
                rep = fidelity.replay(recording, f"http://127.0.0.1:{app.lab.port}", given,
                                      headless=headless)  # fmt: skip
                name = fidelity.save(root, app_id, rep)
                failed = sum(1 for st in rep.steps if st.status == "fail")
                s.status = "PASS" if rep.status == "pass" else "FAIL"
                s.detail = (f"{'the harness bot' if own else 'real'} recording {recording.name}: "
                            f"{len(rep.steps)} step(s), {failed} differ")  # fmt: skip
                s.link = f"/apps/{app_id}/fidelity/{name}"

    if lab_id:
        with _Timer(result.stage("cleanup")) as s:
            say("7/7 cleanup")
            if keep:
                s.status, s.detail = "PASS", f"kept {lab_id} (labctl lab drop {lab_id})"
            else:
                copies.drop(root, lab_id)
                s.status, s.detail = "PASS", f"dropped {lab_id}"
    return _finish(result, out)


def outcome_detail(outcome: dict | None) -> str:
    """How a stage names the business outcome next to the verdict."""
    if not outcome or outcome.get("status") == "NOT_DECLARED":
        return "no outcome declared"
    return f"outcome {outcome['status']} (case {outcome.get('case')})"


def _finish(result: E2EResult, out: Path) -> E2EResult:
    needed = {"contracts", "lab", "copy", "readiness", "bot run", "fidelity", "cleanup"}
    passed = {s.name for s in result.stages if s.status == "PASS"}
    result.status = "PASS" if needed <= passed else "FAIL"
    (out / "report.md").write_text(result.markdown(), encoding="utf-8", newline="\n")
    (out / "result.json").write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return result
