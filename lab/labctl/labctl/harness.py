"""The whole harness chain in one command (`labctl harness run`), for demos and regression.

For one automation against its running lab:

1. reset the data to the baseline (unless told not to) and apply a condition (or normal);
2. start a lab session;
3. run the harness bot (`tools/harness_bot.py`): it drives the automation's web steps the way
   the robot would, and writes a UiPath-shaped robot log and a recording per run;
4. stop the session;
5. import the robot log as a harness upload (Robot runs), building its bot traces;
6. judge the session (expected row changes) and, when a production run is named, compare it
   with the first harness run and the session (parity);
7. write `report.md` and `result.json` in the output folder.

The result is PASS only when every bot run succeeded, the session verdict is not FAIL and parity
(when asked for) is not FAIL.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labctl import bindings as bindings_mod
from labctl import lab as lab_mod
from labctl import outcome as outcome_mod
from labctl import provenance, repo
from labctl import verdict as verdict_mod
from labctl.botlog import runs as botruns
from labctl.models import Automation, Condition


class HarnessError(Exception):
    pass


@dataclass
class ChainResult:
    status: str
    automation: str
    app: str
    out: Path
    runs: list[dict] = field(default_factory=list)
    session_id: str | None = None
    verdict: dict | None = None
    outcome: dict | None = None  # business result against the declared case; not the verdict
    upload: str | None = None
    parity: str | None = None
    notes: list[str] = field(default_factory=list)
    wall_s: float | None = None
    change_kinds: list[str] = field(default_factory=list)  # what kind of rows changed, per table
    provenance: dict | None = None  # written columns: typed / selected / derived counts
    lab: str | None = None  # the copy it ran on, when it ran on one

    def to_dict(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in self.__dict__.items()}


def consistency(results: list[ChainResult]) -> tuple[str, str]:
    """(status, Markdown) for repeated runs of one chain: PASS when every run passed and every
    session changed the same kinds of rows. Times are shown, never judged."""
    kinds = {tuple(r.change_kinds) for r in results}
    ok = all(r.status == "PASS" for r in results) and len(kinds) == 1
    lines = [f"# Repeated runs: {results[0].automation} — {'PASS' if ok else 'FAIL'}", "",
             "| Run | Result | Took | Rows changed | Session |", "|---|---|---|---|---|"]  # fmt: skip
    for n, r in enumerate(results, 1):
        lines.append(f"| {n} | {r.status} | {r.wall_s} s | {'; '.join(r.change_kinds) or 'none'} "
                     f"| `{r.session_id}` |")  # fmt: skip
    if len(kinds) > 1:
        lines += ["", "The runs changed different kinds of rows: the automation is not consistent."]
    return ("PASS" if ok else "FAIL"), "\n".join(lines) + "\n"


def harness_bot(root: Path):
    path = root / "tools" / "harness_bot.py"
    if not path.exists():
        raise HarnessError(f"{path} is missing")
    spec = importlib.util.spec_from_file_location("harness_bot", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def condition_body(root: Path, condition_id: str, app_id: str) -> dict:
    path = root / "conditions" / f"{condition_id}.yaml"
    model, problems = repo.load(path, "condition") if path.exists() else (None, ["not found"])
    if not isinstance(model, Condition):
        raise HarnessError(f"condition {condition_id}: " + "; ".join(map(str, problems)))
    target = next((t for t in model.targets if t.app == app_id), None)
    target = target or next((t for t in model.targets if t.app == "any"), None)
    if target is None:
        raise HarnessError(f"condition {condition_id} has nothing for {app_id}")
    return {
        "id": model.id,
        "seed": model.seed,
        "http": [r.model_dump() for r in target.http],
        "db": target.db.model_dump() if target.db else None,
        "faults": [f.model_dump() for f in target.faults],
    }


def _web_app(automation: Automation) -> str:
    apps = [t.app for t in automation.touches if t.via == "web"]
    if not apps:
        raise HarnessError(f"{automation.id} has no web steps: the harness bot drives screens")
    return apps[0]


def run_chain(
    root: Path,
    automation_id: str,
    inputs: list[dict] | None = None,
    condition: str | None = None,
    reset: bool = True,
    golden: str | None = None,
    out: Path | None = None,
    headless: bool = True,
    copy: bool = False,
    keep_copy: bool = False,
    on: str | None = None,
    case: str | None = None,
) -> ChainResult:
    """With `copy`, the chain runs on a copy of the app's lab made for this run (labctl lab copy),
    started from the app lab's baseline and dropped afterwards unless `keep_copy`: nobody else's
    run can disturb it, and it disturbs nobody."""
    path = root / "automations" / automation_id / "automation.yaml"
    automation, problems = repo.load(path, "automation")
    if not isinstance(automation, Automation):
        raise HarnessError(f"automation {automation_id}: " + "; ".join(map(str, problems)))
    app_id = _web_app(automation)
    try:
        outcome_mod.select_case(automation.outcome, case)  # an unknown case stops before any run
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(out or lab_mod.state_dir(root) / "harness" / automation_id / stamp)
    out.mkdir(parents=True, exist_ok=True)
    result = ChainResult("FAIL", automation_id, app_id, out)
    lab_id = app_id
    if on:  # a copy made beforehand (labctl lab copy), kept afterwards
        if bindings_mod.split_lab_id(on)[0] != app_id:
            raise HarnessError(f"{on} is not a copy of {app_id}")
        lab_id = result.lab = on
    elif copy:
        from labctl import copies

        try:
            rec = copies.create(root, app_id, build=False)
        except copies.CopyError as exc:
            raise HarnessError(str(exc)) from exc
        lab_id = rec["lab"]
        result.lab = lab_id
        result.notes.append(f"ran on its own copy {lab_id} (data: {rec['data']})")
    try:
        return _run(root, automation, automation_id, app_id, lab_id, inputs, condition, reset,
                    golden, out, stamp, result, headless, case)  # fmt: skip
    finally:
        if copy and not keep_copy:
            from labctl import copies

            try:
                copies.drop(root, lab_id)
            except Exception as exc:  # noqa: BLE001 - the run's result still stands
                result.notes.append(f"the copy {lab_id} could not be dropped: {exc}")
            _rewrite(out, result)


def _rewrite(out: Path, result: ChainResult) -> None:
    path = out / "result.json"
    if path.exists():
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


def _run(root, automation, automation_id, app_id, lab_id, inputs, condition, reset, golden, out,
         stamp, result, headless, case=None) -> ChainResult:  # fmt: skip
    agent = lab_mod.agent(root, lab_id)
    agent.health()  # unreachable lab -> LabError before anything changes

    # 1. data and condition; the condition the lab had is put back at the end
    if reset:
        agent.reset()
    before = agent.condition() or {}
    before = {k: v for k, v in before.items() if k != "applied_at"} if before.get("id") else None
    agent.set_condition(condition_body(root, condition, app_id) if condition else None)
    try:
        return _chain(root, automation, automation_id, app_id, agent, inputs, condition, golden,
                      out, stamp, result, headless, lab_id, case)  # fmt: skip
    finally:
        try:
            agent.set_condition(before)
        except lab_mod.LabError as exc:
            result.notes.append(f"the earlier condition could not be put back: {exc}")


def _chain(root, automation, automation_id, app_id, agent, inputs, condition, golden, out, stamp,
           result, headless, lab_id=None, case=None) -> ChainResult:  # fmt: skip
    # 2-4. session around the bot
    lab_id = lab_id or app_id
    labs = {app_id: lab_id} if lab_id != app_id else None
    b = bindings_mod.for_automation(root, automation, host="127.0.0.1", labs=labs)
    bindings = json.loads(b.model_dump_json(exclude_none=True))
    runs = inputs or [dict(automation.test_inputs)]
    log = out / "robot.log.jsonl"
    agent.start_session(automation_id, f"harness {stamp}" + (f", {condition}" if condition else ""))
    try:
        done = harness_bot(root).run(
            root / "automations" / automation_id, bindings, runs, log, out / "recordings",
            headless=headless,
        )  # fmt: skip
        result.runs = [
            {
                "job_id": d.get("job_id"),
                "state": d.get("state"),
                "inputs": i,
                "recording": d.get("recording"),
            }  # fmt: skip
            for d, i in zip(done, runs, strict=False)
        ]
    except BaseException:
        agent.stop_session()  # keep what was recorded, then report the failure
        raise
    session = agent.stop_session()
    result.session_id = session["session_id"]
    result.wall_s = (session.get("summary") or {}).get("wall_s")
    result.change_kinds = [
        f"{e.table}: {', '.join(e.ops)}" + (f" ({', '.join(e.columns)})" if e.columns else "")
        for e in verdict_mod.learn(session.get("changes") or [])
    ]

    # 5. robot log -> bot traces
    try:
        meta = botruns.import_log(
            root, lab_mod.state_dir(root), automation_id, log.read_bytes(), log.name,
            source="harness",
        )  # fmt: skip
        result.upload = meta["upload"]
    except botruns.RunsError as exc:
        result.notes.append(f"the robot log could not be imported: {exc}")

    # 6. verdict and parity
    v, _, _ = verdict_mod.for_session(root, lab_id, result.session_id, automation_id)
    result.verdict = v.to_dict()
    parity_md = None
    if golden:
        if not result.upload or not result.runs:
            result.notes.append("parity skipped: no harness run to compare")
        else:
            try:
                traces = botruns.traces(lab_mod.state_dir(root), automation_id, result.upload)
                first = f"{result.upload}/{traces[0].run_id}"
                result.parity, parity_md = botruns.parity(
                    lab_mod.state_dir(root), automation_id, golden, first, session
                )
            except botruns.RunsError as exc:
                result.notes.append(f"parity skipped: {exc}")
    provenance_md = None
    if result.upload:
        try:
            traces = botruns.traces(lab_mod.state_dir(root), automation_id, result.upload)
            detail = agent.session_detail(result.session_id) or {}
            prov = provenance.bind(provenance.combined(traces), detail.get("changes") or [],
                                   agent.data())  # fmt: skip
            result.provenance = prov.counts()
            provenance_md = prov.markdown()
        except (botruns.RunsError, lab_mod.LabError) as exc:
            result.notes.append(f"provenance skipped: {exc}")
    ok_runs = bool(result.runs) and all(r.get("state") == "Successful" for r in result.runs)
    try:
        outcome = outcome_mod.for_session(
            root, lab_id, result.session_id, automation_id, case, ok_runs
        )[0]
    except (lab_mod.LabError, ValueError) as exc:
        outcome = outcome_mod.Outcome(
            outcome_mod.CANNOT_CHECK, notes=[f"the outcome could not be judged: {exc}"]
        )
    result.outcome = outcome.to_dict()
    # a run declared to fail (`rejected`, `failed_run`) is expected to end unsuccessfully
    expect_failure = outcome.kind in ("rejected", "failed_run")
    if not ok_runs and not expect_failure:
        result.notes.append("a bot run did not succeed")
    failed = (
        (not ok_runs and not expect_failure)
        or v.status == verdict_mod.FAIL
        or outcome.status == outcome_mod.NOT_MET
        or (result.parity or "").lower() == "fail"
    )
    result.status = "FAIL" if failed else "PASS"
    (out / "report.md").write_text(
        render(result, session, parity_md, provenance_md), encoding="utf-8", newline="\n"
    )
    (out / "result.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


def _outcome_line(o: dict) -> str:
    if o.get("status") == outcome_mod.NOT_DECLARED:
        return "no outcome declared"
    return f"{str(o['status']).replace('_', ' ')} (case {o.get('case')}, {o.get('kind')})"


def render(
    result: ChainResult, session: dict, parity_md: str | None, provenance_md: str | None = None
) -> str:
    sm = session.get("summary") or {}
    v = result.verdict or {}
    lines = [
        f"# Harness run: {result.automation} — {result.status}",
        "",
        f"App `{result.app}`, session `{result.session_id}`, condition "
        f"{(session.get('apps') or [{}])[0].get('condition') or 'normal'}.",
        "",
        "| Bot run | Inputs | Result |",
        "|---|---|---|",
    ]
    for r in result.runs:
        shown = ", ".join(f"{k}={v}" for k, v in (r.get("inputs") or {}).items())
        lines.append(f"| {(r.get('job_id') or '')[:8]} | {shown} | {r.get('state')} |")
    lines += [
        "",
        f"Session: {sm.get('requests', 0)} requests, {sm.get('wall_s', 0)} s, "
        f"{sm.get('waiting_on_app_s', 0)} s waiting on the app, {sm.get('errors', 0)} errors, "
        f"{len(session.get('changes') or [])} row change(s).",
        "",
        f"**Verdict on row changes: {v.get('status')}**"
        + (f" ({'; '.join(v.get('reasons') or [])})" if v.get("reasons") else ""),
    ]
    for c in v.get("unexpected") or []:
        lines.append(f"- `{c['table']}` {c['op']} `{c['key']}`: {c['why']}")
    if result.outcome is not None:
        o = result.outcome
        text = _outcome_line(o)
        lines += ["", f"**Business outcome: {text}**"]
        lines += [f"- {u}" for u in (o.get("unmet") or []) + (o.get("unchecked") or [])]
    if result.upload:
        lines += ["", f"Robot log imported as harness upload `{result.upload}` (Robot runs)."]
    if provenance_md:
        lines += ["", provenance_md.strip()]
    if parity_md:
        lines += [
            "",
            "## Parity with the production run",
            "",
            ("\n" + parity_md).replace("\n# ", "\n### ").strip(),
        ]
    if result.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in result.notes]
    return "\n".join(lines) + "\n"
