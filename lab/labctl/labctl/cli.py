"""labctl command line. Commands are thin: parse arguments, call one function, print."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
import yaml
from pydantic import ValidationError

from labctl import bindings as bindings_mod
from labctl import conformance as conformance_mod
from labctl import demand as demand_report
from labctl import evidence_cli, factory, ocp_cli, repo
from labctl import lab as lab_mod
from labctl.analyse import run as analysis
from labctl.behaviour import cli as behaviour_cli
from labctl.bindings import BindingError
from labctl.botlog import cli as botlog_cli
from labctl.builder import cli as builder_cli
from labctl.data import cli as data_cli
from labctl.devin import cli as devin_cli
from labctl.models import KINDS, Automation
from labctl.realdata import cli as realdata_cli
from labctl.source import UnsupportedStack
from labctl.source import sqlalchemy_models as sam

app = typer.Typer(no_args_is_help=True, help="Twin Lab: register, build, check and control.")
automation_app = typer.Typer(no_args_is_help=True, help="Registered automations.")
app_app = typer.Typer(no_args_is_help=True, help="Demo apps.")
data_app = typer.Typer(no_args_is_help=True, help="Data packs.")
condition_app = typer.Typer(no_args_is_help=True, help="Condition profiles (slowness, faults).")
app.add_typer(automation_app, name="automation")
app.add_typer(app_app, name="app")
app.add_typer(data_app, name="data")
app.add_typer(condition_app, name="condition")


def _root() -> Path:
    try:
        return repo.find_root()
    except repo.NotARepo as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@app.command()
def validate(
    paths: list[Path] = typer.Argument(None, help="Files to check. Default: every contract file."),
    kind: str = typer.Option(None, help=f"Contract kind, one of: {', '.join(KINDS)}."),
) -> None:
    """Check contract files. Prints file, field and reason for each problem."""
    files = list(paths) if paths else repo.contract_files(_root())
    problems = [p for f in files for p in repo.load(f, kind)[1]]
    for problem in problems:
        typer.echo(str(problem))
    typer.echo(f"{len(files)} file(s) checked, {len(problems)} problem(s)")
    if problems:
        raise typer.Exit(1)


def _list(kind: str, describe) -> None:
    for path in repo.files_of(_root(), kind):
        model, problems = repo.load(path)
        typer.echo(describe(model) if model else f"INVALID  {path} ({len(problems)} problem(s))")


@automation_app.command("list")
def automation_list() -> None:
    """List registered automations."""
    _list(
        "automation",
        lambda a: f"{a.id}  {a.type.value}  {a.surface.value}  apps={','.join(a.apps()) or '-'}",
    )


@automation_app.command("analyse")
def automation_analyse(
    automation_id: str = typer.Argument(..., help="Folder name under automations/."),
    write: bool = typer.Option(False, "--write", help="Save the result into automation.yaml."),
    force: bool = typer.Option(False, "--force", help="Overwrite a file a person has reviewed."),
) -> None:
    """Read an automation's source and report what it touches. Nothing is executed."""
    path = _root() / "automations" / automation_id / "automation.yaml"
    model, problems = repo.load(path)
    if model is None:
        for problem in problems:
            typer.echo(str(problem), err=True)
        raise typer.Exit(1)
    try:
        result = analysis.analyse(path.parent, model)
    except analysis.AnalysisError as exc:
        typer.echo(f"REJECTED  {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        updated = Automation.model_validate(result.data)
    except ValidationError as exc:
        typer.echo("analysis does not fit what automation.yaml declares:", err=True)
        for e in exc.errors():
            typer.echo(f"  {'.'.join(str(p) for p in e['loc']) or '-'}: {e['msg']}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"{automation_id}: read {', '.join(result.files)}")
    for touch in updated.touches:
        count = len(getattr(touch, {"web": "screens", "api": "routes", "db": "tables"}[touch.via]))
        typer.echo(f"  {touch.app}  {touch.via}  {count} item(s)")
        for route in getattr(touch, "routes", []):
            if route.sends or route.reads:
                typer.echo(f"    {route.method} {route.path}  sends {', '.join(route.sends) or '-'}"
                           f"  reads {', '.join(route.reads) or '-'}")  # fmt: skip
    for gap in result.gaps:
        typer.echo(f"  GAP  {gap.where}: {gap.what}")
    for note in result.notes:
        typer.echo(f"  note {note.where}: {note.what}")
    typer.echo(f"{len(result.gaps)} gap(s), {len(result.notes)} note(s)")
    if not write:
        typer.echo("not saved (use --write)")
        return
    if model.analysis.reviewed_by and not force:
        typer.echo(
            f"not saved: reviewed by {model.analysis.reviewed_by}; --force discards that review",
            err=True,
        )
        raise typer.Exit(1)
    path.write_text(
        yaml.safe_dump(
            updated.model_dump(mode="json", exclude_none=True), sort_keys=False, width=100
        ),
        encoding="utf-8",
        newline="\n",
    )
    typer.echo(f"saved {path}")


@app.command()
def demand(
    app_id: str = typer.Argument(..., help="Application id, as used in automation.yaml touches."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of text."),
) -> None:
    """What registered automations need from one application: the scope of its demo app."""
    report = demand_report.demand(app_id, _automations())
    typer.echo(json.dumps(report, indent=2) if as_json else demand_report.render(report))


def _automations() -> list[Automation]:
    out = []
    for path in repo.files_of(_root(), "automation"):
        model, problems = repo.load(path)
        if model is None:
            typer.echo(f"skipped invalid {path} ({len(problems)} problem(s))", err=True)
            continue
        out.append(model)
    return out


def _flows(app_id: str) -> list:
    out = []
    for path in repo.files_of(_root(), "flow"):
        model, problems = repo.load(path)
        if model is None:
            typer.echo(f"skipped invalid {path} ({len(problems)} problem(s))", err=True)
            continue
        if model.app == app_id:
            out.append(model)
    return out


@app.command()
def brief(
    app_id: str = typer.Argument(..., help="Application id, as used in automation.yaml and flows."),
    source: Path = typer.Option(None, help="Folder holding the real application's source, if any."),
    system: str = typer.Option(None, help="Name of the real system. Default: the app id."),
    force: bool = typer.Option(
        False, "--force", help="Replace an existing brief, model and schema."
    ),
    db_engine: str = typer.Option(
        None, help="Database the real system runs: postgres, mssql, oracle, db2, mysql, sqlite."
    ),
    data: str = typer.Option(
        None,
        "--data",
        help="Where the business data lives, if the recording cannot show it: browser or server.",
    ),
    draft_flow: bool = typer.Option(
        False,
        "--draft-flow",
        help="No automation or flow says what the app needs: first write a draft requested "
        "flow from each of its recordings (flows/<app>-<recording>.yaml), to review.",
    ),
) -> None:
    """Write apps/<app>/model.yaml, BRIEF.md, db/schema.sql and a first app.yaml from the evidence."""
    root = _root()
    if draft_flow:
        from labctl import multiapp

        for rec in factory.discover(root, app_id).recordings:
            if rec.is_dir() and (rec / "steps.jsonl").is_file():
                path, note = multiapp.write_draft_flow(root, app_id, rec, rec.name)
                typer.echo(f"flow: {path.relative_to(root) if path else '-'} ({note})")
    if source is not None and not source.is_dir():
        typer.echo(f"source folder not found: {source}", err=True)
        raise typer.Exit(2)
    if db_engine is not None and db_engine not in sam.ENGINES:
        typer.echo(f"unknown --db-engine; one of {', '.join(sam.ENGINES)}", err=True)
        raise typer.Exit(2)
    if data is not None and data not in ("browser", "server"):
        typer.echo("--data is browser or server", err=True)
        raise typer.Exit(2)
    found = factory.discover(root, app_id)
    if found.kinds():
        typer.echo("evidence: " + ", ".join(f"{len(getattr(found, k))} {k}" for k in found.kinds()))
    try:
        done = factory.generate(
            root,
            app_id,
            system or (source.resolve().name if source else app_id),
            source,
            db_engine,
            force,
            data,
        )
    except factory.FactoryError as exc:
        typer.echo(str(exc), err=True)
        if "nothing to build" in str(exc) and found.recordings:
            typer.echo(f"  the evidence has {len(found.recordings)} recording(s): run again with "
                       "--draft-flow to draft the requested flow from them, or write "
                       f"flows/{app_id}-....yaml yourself", err=True)  # fmt: skip
        raise typer.Exit(1) from exc
    except UnsupportedStack as exc:
        typer.echo(f"UNSUPPORTED  {exc}", err=True)
        raise typer.Exit(2) from exc
    result = done.brief
    if done.kept_app_yaml:
        typer.echo(
            f"kept existing {root / 'apps' / app_id / 'app.yaml'}; rules in the brief may differ from it"
        )
    typer.echo(
        f"{app_id}: {len(result.screens)} screen(s), {len(result.routes)} route(s), "
        f"{len(result.tables)} table(s) in scope; "
        f"left out {len(result.out_screens)} screen(s), {len(result.out_routes)} route(s), "
        f"{len(result.out_tables)} table(s); mode {result.mode}"
    )
    for gap in result.gaps:
        typer.echo(f"  OPEN  {gap}")
    typer.echo("wrote " + ", ".join(str(p.relative_to(root)) for p in done.files))


@app_app.command("list")
def app_list() -> None:
    """List demo apps."""
    _list("app", lambda a: f"{a.id}  copies={a.copies.system}  built_for={len(a.built_for)}")


@data_app.command("list")
def data_list() -> None:
    """List data packs."""
    _list("pack", lambda p: f"{p.id}  {p.kind}  apps={','.join(p.apps)}")


@condition_app.command("list")
def condition_list() -> None:
    """List condition profiles."""
    _list("condition", lambda c: f"{c.id}  calibration={c.calibration}  targets={len(c.targets)}")


if __name__ == "__main__":
    app()


# --- lab, bindings, conformance ------------------------------------------------------------------

lab_app = typer.Typer(no_args_is_help=True, help="Run demo apps in the lab (Docker Compose).")
app.add_typer(lab_app, name="lab")
app.add_typer(evidence_cli.app, name="evidence", help="Analyse and record evidence.")
app.add_typer(realdata_cli.app, name="realdata", help="Slice, mask and load real data (read-only).")
app.add_typer(
    botlog_cli.app, name="botlog", help="Robot logs, NetLog, bot traces, calibration, parity."
)
app.add_typer(behaviour_cli.app, name="behaviour", help="Behaviour catalog and fidelity replay.")
data_app.registered_commands.extend(
    data_cli.app.registered_commands
)  # template, validate, generate
app.add_typer(devin_cli.app, name="devin", help="Build apps with Devin: package, start, status.")
app.add_typer(
    builder_cli.app, name="build", help="Build apps with a coding agent: Claude, Devin, by hand."
)
app.add_typer(ocp_cli.app, name="ocp")


def _automation(automation_id: str) -> Automation:
    path = _root() / "automations" / automation_id / "automation.yaml"
    model, problems = repo.load(path, "automation")
    if model is None:
        for problem in problems:
            typer.echo(str(problem), err=True)
        raise typer.Exit(1)
    return model


@lab_app.command("up")
def lab_up(
    app_id: str,
    no_build: bool = typer.Option(False, "--no-build", help="Use the images already built."),
) -> None:
    """Start an app with its database and lab agent; waits until healthy."""
    try:
        lab_mod.up(_root(), app_id, build=not no_build)
        state = lab_mod.status(_root(), app_id)
    except (lab_mod.LabError, BindingError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{app_id}: {'running' if state.running else 'NOT running'} {state.services}")
    if state.health is not None:
        typer.echo(f"  health {state.health}")


@lab_app.command("down")
def lab_down(
    app_id: str, wipe: bool = typer.Option(False, "--wipe", help="Delete its database too.")
) -> None:
    """Stop an app's lab."""
    try:
        lab_mod.down(_root(), app_id, wipe)
    except (lab_mod.LabError, BindingError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{app_id}: stopped" + (" and wiped" if wipe else ""))


@lab_app.command("status")
def lab_status(app_id: str) -> None:
    """Containers and health of an app's lab."""
    state = lab_mod.status(_root(), app_id)
    typer.echo(f"{app_id}: {'running' if state.running else 'not running'} {state.services or ''}")
    if state.health is not None:
        typer.echo(f"  health {state.health}")
    if state.error:
        typer.echo(f"  {state.error}")


@lab_app.command("copy")
def lab_copy(
    app_id: str,
    name: str = typer.Option(None, "--name", help="Copy name (default run-1, run-2, ...)."),
    data: str = typer.Option(
        "baseline", "--data", help="baseline (the app lab's), current, app, or a data pack name."
    ),
    no_build: bool = typer.Option(False, "--no-build", help="Use the images already built."),
) -> None:
    """Start a copy of an app's lab for one run, on free ports, with its own database."""
    from labctl import copies

    try:
        rec = copies.create(_root(), app_id, name, data, build=not no_build)
    except (copies.CopyError, lab_mod.LabError, BindingError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{rec['lab']}: running on http://127.0.0.1:{rec['port']} "
               f"(database port {rec['db_port']}), data: {rec['data']}")  # fmt: skip
    typer.echo(f"  bindings: labctl bindings <automation> --on {rec['lab']}")
    typer.echo(f"  drop it:  labctl lab drop {rec['lab']}")


@lab_app.command("copies")
def lab_copies(app_id: str = typer.Argument(None)) -> None:
    """Copies running now (of one app, or all)."""
    from labctl import copies

    rows = copies.listing(_root(), app_id)
    for r in rows:
        typer.echo(f"{r['lab']}  port {r['port']}  db {r['db_port']}  data {r['data']}  "
                   f"since {r['created']}")  # fmt: skip
    if not rows:
        typer.echo("no copies")


@lab_app.command("drop")
def lab_drop(lab_id: str) -> None:
    """Stop a copy and delete it with its data."""
    from labctl import copies

    try:
        copies.drop(_root(), lab_id)
    except (copies.CopyError, lab_mod.LabError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{lab_id}: dropped")


def _labs(on: list[str]) -> dict[str, str]:
    """`--on showcase.run-1` -> {"showcase": "showcase.run-1"}."""
    out = {}
    for lab_id in on:
        base, copy = bindings_mod.split_lab_id(lab_id)
        if not copy:
            raise typer.BadParameter(f"{lab_id} is not a copy (app.copy, see labctl lab copies)")
        out[base] = lab_id
    return out


@app.command()
def coverage(
    app_id: str = typer.Argument(..., help="Application id."),
    as_json: bool = typer.Option(False, "--json", help="Print the matrix as JSON."),
    no_save: bool = typer.Option(False, "--no-save", help="Do not keep the result for the portal."),
) -> None:
    """The coverage matrix: every requirement, what shows it, what checks it, and what to ask for.

    Prints the claim "100% of the agreed replication acceptance cases passed" only when every
    required row is VERIFIED or EXCLUDED; otherwise the exact shortfall. Exit 1 when it cannot be
    claimed."""
    from labctl import coverage as coverage_mod

    root = _root()
    try:
        report = coverage_mod.build(root, app_id)
    except coverage_mod.CoverageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if not no_save:
        coverage_mod.save(root, report)
    typer.echo(report.model_dump_json(indent=2) if as_json else report.markdown())
    if not report.claim:
        raise typer.Exit(1)


ON_HELP = "Use this copy of an app's lab (app.copy); repeat for several apps."


@app.command()
def bindings(
    automation_id: str,
    form: str = typer.Option(
        "json", help="json (ATS_BINDINGS), env, uipath (input arguments) or yaml."
    ),
    on: list[str] = typer.Option([], "--on", help=ON_HELP),
) -> None:
    """What an automation needs to run against the lab: addresses, test sign-in, inputs."""
    auto = _automation(automation_id)
    try:
        b = bindings_mod.for_automation(_root(), auto, labs=_labs(on))
        typer.echo(bindings_mod.render(auto, b, form))
    except BindingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def conformance(
    automation_id: str,
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
    on: list[str] = typer.Option([], "--on", help=ON_HELP),
    require_fidelity: bool = typer.Option(
        False,
        "--require-fidelity",
        help="BLOCKED unless a fidelity replay of every app the bot uses on screen has passed.",
    ),  # fmt: skip
) -> None:
    """Check an automation against the running demo apps: READY or BLOCKED."""
    report = conformance_mod.check(_root(), _automation(automation_id), labs=_labs(on),
                                   require_fidelity=require_fidelity)  # fmt: skip
    typer.echo(
        json.dumps(report.to_dict(), indent=2) if as_json else conformance_mod.render(report)
    )
    if report.verdict != "READY":
        raise typer.Exit(1)


@app.command()
def e2e(
    automation_id: str,
    condition: str = typer.Option(None, "--condition", help="Condition for the bot run."),
    golden: str = typer.Option(None, "--golden", help="Production run for parity: <upload>/<run>."),
    no_build: bool = typer.Option(False, "--no-build", help="Use the images already built."),
    keep: bool = typer.Option(False, "--keep", help="Keep the lab copy afterwards."),
    out: Path = typer.Option(None, "--out", help="Folder for the report."),
    headed: bool = typer.Option(False, "--headed", help="Show the browser."),
    inputs: Path = typer.Option(
        None, "--inputs", help="Bot runs, JSON object or list (default: e2e-inputs.json)."
    ),
    case: str = typer.Option(None, "--case", help="Outcome case to judge (default: the first)."),
) -> None:
    """End to end on a lab copy: contracts, lab, copy, readiness, bot run (session, verdict,
    parity), fidelity replay, cleanup. Writes report.md; exit 1 unless every stage passed."""
    from labctl import e2e as e2e_mod

    runs = None
    if inputs is not None:
        data = json.loads(inputs.read_text(encoding="utf-8-sig"))
        runs = data if isinstance(data, list) else [data]
    result = e2e_mod.run(_root(), automation_id, condition, golden, not no_build, keep, out,
                         not headed, say=lambda m: typer.echo(m, err=True), inputs=runs, case=case)  # fmt: skip
    typer.echo(Path(result.out, "report.md").read_text(encoding="utf-8"))
    typer.echo(f"report: {Path(result.out, 'report.md')}")
    if result.status != "PASS":
        raise typer.Exit(1)


# --- sessions: verdict on unexpected changes ------------------------------------------------------

session_app = typer.Typer(no_args_is_help=True, help="Lab sessions and their verdict.")
app.add_typer(session_app, name="session")


def _lab_call(fn):
    from labctl import packs as packs_mod

    try:
        return fn()
    except (lab_mod.LabError, BindingError, packs_mod.PackError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@session_app.command("list")
def session_list(app_id: str) -> None:
    """Stored sessions of an app's lab, newest first."""
    for s in _lab_call(lambda: lab_mod.agent(_root(), app_id).sessions()):
        sm = s.get("summary") or {}
        typer.echo(
            f"{s['session_id']}  {s.get('automation') or '-':<28} {s.get('label') or '':<24} "
            f"{sm.get('requests', 0)} requests, {sm.get('errors', 0)} errors"
        )


@session_app.command("verdict")
def session_verdict(
    app_id: str,
    session_id: str,
    automation: str = typer.Option(None, "--automation", help="Judge against this automation."),
    case: str = typer.Option(None, "--case", help="Outcome case to judge (default: the first)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """PASS when every row change is one the automation expects; FAIL otherwise (exit 1). Also
    reports whether the declared business outcome was met (NOT_MET exits 1, too)."""
    from labctl import outcome as outcome_mod
    from labctl import verdict as verdict_mod

    v, _, auto = _lab_call(lambda: verdict_mod.for_session(_root(), app_id, session_id, automation))
    oc, _ = _lab_call(
        lambda: outcome_mod.for_session(_root(), app_id, session_id, automation, case)
    )
    if as_json:
        typer.echo(
            json.dumps({"automation": auto, **v.to_dict(), "outcome": oc.to_dict()}, indent=2)
        )
    else:
        typer.echo(f"{v.status}  {session_id}  automation {auto or '-'}  {v.checked} change(s)")
        for reason in v.reasons:
            typer.echo(f"  {reason}")
        for c in v.unexpected[: verdict_mod.SHOWN]:
            typer.echo(f"  {c['table']} {c['op']} {c['key']}: {c['why']}")
        typer.echo(f"Outcome: {oc.summary()}")
    if v.status == verdict_mod.FAIL or oc.status == outcome_mod.NOT_MET:
        raise typer.Exit(1)


@session_app.command("provenance")
def session_provenance(
    app_id: str,
    session_id: str,
    run: str = typer.Option(
        ...,
        "--run",
        help="The bot's run in that session: <upload> or <upload>/<run id> (Robot runs).",
    ),
    automation: str = typer.Option(None, "--automation", help="Default: the session's own."),
) -> None:
    """Which written column got its value from the bot (typed or chosen) and which from the app."""
    from labctl import provenance
    from labctl.botlog import runs as botruns

    root = _root()
    agent = lab_mod.agent(root, app_id)
    session = _lab_call(lambda: agent.session(session_id))
    auto = automation or session.get("automation")
    if not auto:
        typer.echo("the session names no automation; give --automation", err=True)
        raise typer.Exit(2)
    upload, _, run_id = run.partition("/")
    try:
        traces = botruns.traces(lab_mod.state_dir(root), auto, upload)
    except botruns.RunsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if run_id:
        traces = [t for t in traces if t.run_id == run_id]
    if not traces:
        typer.echo(f"no run {run_id} in {upload}", err=True)
        raise typer.Exit(1)
    detail = _lab_call(lambda: agent.session_detail(session_id)) or {}
    prov = provenance.bind(provenance.combined(traces), detail.get("changes") or [], agent.data())
    typer.echo(prov.markdown())


@session_app.command("accept")
def session_accept(
    app_id: str,
    session_id: str,
    automation: str = typer.Option(None, "--automation", help="Default: the session's own."),
    replace: bool = typer.Option(
        False, "--replace", help="Replace `expects:` instead of widening."
    ),
) -> None:
    """Accept a checked session's row changes as what the automation is expected to do."""
    from labctl import verdict as verdict_mod

    root = _root()
    session, changes = _lab_call(
        lambda: verdict_mod.session_changes(lab_mod.agent(root, app_id), session_id)
    )
    auto = automation or session.get("automation")
    if not auto:
        typer.echo("the session names no automation; give --automation", err=True)
        raise typer.Exit(2)
    expects = _lab_call(lambda: verdict_mod.accept(root, auto, changes, replace))
    typer.echo(f"automations/{auto}/automation.yaml expects:")
    for e in expects:
        cols = f" columns {', '.join(e.columns)}" if e.columns else ""
        typer.echo(f"  {e.table}: {', '.join(e.ops)}{cols}")
    if not expects:
        typer.echo("  nothing (the automation changes no rows)")


# --- the harness chain ---------------------------------------------------------------------------

harness_app = typer.Typer(no_args_is_help=True, help="The harness chain in one command.")
app.add_typer(harness_app, name="harness")


@harness_app.command("run")
def harness_run(
    automation_id: str,
    inputs: Path = typer.Option(None, "--inputs", help="JSON object or list (one run each)."),
    condition: str = typer.Option(None, "--condition", help="Condition id; default normal."),
    no_reset: bool = typer.Option(False, "--no-reset", help="Keep the data as it is."),
    golden: str = typer.Option(
        None, "--golden", help="A production run to compare with: <upload>/<run id>."
    ),
    out: Path = typer.Option(None, "--out", help="Folder for the log, recordings and report."),
    headed: bool = typer.Option(False, "--headed", help="Show the browser."),
    repeat: int = typer.Option(1, "--repeat", min=1, max=20, help="Run the chain N times."),
    copy: bool = typer.Option(
        False, "--copy", help="Run on a copy of the lab made for the run, dropped afterwards."
    ),
    keep_copy: bool = typer.Option(False, "--keep-copy", help="With --copy: keep the copy."),
    case: str = typer.Option(None, "--case", help="Outcome case to judge (default: the first)."),
) -> None:
    """Reset, apply a condition, record a session around the harness bot, import its robot log,
    judge the row changes and (with --golden) check parity. With --repeat, also check that
    every run changed the same kinds of rows. Exit 1 unless PASS."""
    from labctl import harness

    runs = None
    if inputs is not None:
        data = json.loads(inputs.read_text(encoding="utf-8-sig"))
        runs = data if isinstance(data, list) else [data]
    results = []
    try:
        for n in range(repeat):
            where = (out / f"run-{n + 1}") if (out and repeat > 1) else out
            results.append(harness.run_chain(_root(), automation_id, runs, condition,
                                             not no_reset, golden, where, not headed,
                                             copy, keep_copy, case=case))  # fmt: skip
    except (harness.HarnessError, lab_mod.LabError, BindingError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if repeat == 1:
        typer.echo((results[0].out / "report.md").read_text(encoding="utf-8"))
        typer.echo(f"report: {results[0].out / 'report.md'}")
        status = results[0].status
    else:
        status, text = harness.consistency(results)
        typer.echo(text)
        for r in results:
            typer.echo(f"report: {r.out / 'report.md'}")
    if status != "PASS":
        raise typer.Exit(1)


# --- data packs and baselines --------------------------------------------------------------------


@data_app.command("save")
def data_save(
    app_id: str,
    name: str,
    label: str = typer.Option(None, "--label"),
    replace: bool = typer.Option(False, "--replace", help="Overwrite a pack of that name."),
) -> None:
    """Save the lab's current data as a named pack (kept on this machine)."""
    from labctl import packs as packs_mod

    info = _lab_call(lambda: packs_mod.save_from_lab(_root(), app_id, name, label, replace))
    typer.echo(f"saved pack {info.name}: " + ", ".join(f"{t} {n}" for t, n in info.counts.items()))


@data_app.command("packs")
def data_packs(app_id: str) -> None:
    """Named packs saved for an app."""
    from labctl import packs as packs_mod

    for p in packs_mod.listing(_root(), app_id):
        typer.echo(f"{p.name:<24} {p.saved_at}  {sum(p.counts.values())} rows  {p.label or ''}")


@data_app.command("use")
def data_use(app_id: str, name: str) -> None:
    """Load a named pack into the lab; it becomes the baseline."""
    from labctl import packs as packs_mod

    reply = _lab_call(lambda: packs_mod.load_into_lab(_root(), app_id, name))
    typer.echo(f"loaded pack {name}: " + ", ".join(f"{t} {n}" for t, n in reply["counts"].items()))


@data_app.command("baselines")
def data_baselines(app_id: str) -> None:
    """Every kept baseline, newest first. * marks the current one, P a pinned one."""
    for b in _lab_call(lambda: lab_mod.agent(_root(), app_id).baselines()):
        mark = ("*" if b.get("current") else " ") + ("P" if b.get("pinned") else " ")
        rows = sum((b.get("counts") or {}).values())
        typer.echo(
            f"{mark} {b['id']}  {b.get('loaded_at')}  {b.get('kind'):<8} {rows:>6} rows  "
            f"{b.get('pack') or b.get('label') or ''}"
        )


@data_app.command("restore")
def data_restore(app_id: str, baseline_id: str) -> None:
    """Put an earlier baseline back; it becomes the current baseline."""
    reply = _lab_call(lambda: lab_mod.agent(_root(), app_id).restore_baseline(baseline_id))
    typer.echo(
        f"restored {baseline_id}: " + ", ".join(f"{t} {n}" for t, n in reply["counts"].items())
    )


@data_app.command("load-across")
def data_load_across(
    pack: Path = typer.Argument(..., help='JSON: {"apps": {"<app>": {"<table>": [rows]}}}'),
    label: str = typer.Option(None, "--label"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only check the links."),
) -> None:
    """Load one data pack into several apps' labs. The same customer (or any linked record)
    must exist in every app, as each app.yaml's `links` say; otherwise nothing is loaded."""
    from labctl import crossapp
    from labctl.bindings import load_app

    root = _root()
    try:
        parts = crossapp.read_pack(pack)
        problems = crossapp.check(parts, {a: load_app(root, a) for a in parts})
        if problems:
            for p in problems[:50]:
                typer.echo(f"refused: {p}")
            typer.echo(f"{len(problems)} key(s) do not match across apps; nothing was loaded")
            raise typer.Exit(1)
        if dry_run:
            typer.echo(f"links match across {', '.join(parts)}; nothing loaded (--dry-run)")
            return
        done = crossapp.load(root, parts, label or pack.stem)
    except (crossapp.CrossAppError, BindingError, lab_mod.LabError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    for app_id, reply in done.items():
        counts = ", ".join(f"{t} {n}" for t, n in (reply.get("counts") or {}).items())
        typer.echo(f"{app_id}: loaded {counts}")


@data_app.command("quarantine")
def data_quarantine(
    app_id: str,
    baseline_id: str,
    reason: str = typer.Option(None, "--reason", help="Why it must not be used again."),
    release: bool = typer.Option(False, "--release", help="Take it out of quarantine."),
) -> None:
    """Mark a baseline unusable (it can no longer be restored or reset to), or release it."""
    if not release and not reason:
        typer.echo("give --reason, or --release", err=True)
        raise typer.Exit(2)
    _lab_call(
        lambda: lab_mod.agent(_root(), app_id).quarantine_baseline(baseline_id, reason, release)
    )
    typer.echo(f"{baseline_id}: {'released' if release else 'quarantined: ' + reason}")


db_app = typer.Typer(no_args_is_help=True, help="Database servers for demo apps.")
app.add_typer(db_app, name="db")


@db_app.command("list")
def db_list() -> None:
    """Database servers an app can use: Twin Lab's own, and those in twinlab.local.yaml."""
    from labctl import dbservers

    typer.echo("postgres     PostgreSQL in a container Twin Lab runs (the default)")
    typer.echo("sqlserver    SQL Server 2022 in a container Twin Lab runs (Developer edition)")
    try:
        servers = dbservers.load(_root())
    except dbservers.DbServerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    for s in servers.values():
        d = s.described()
        problems = s.problems()
        typer.echo(f"{s.name:<12} {d['engine']} at {s.host}:{s.port} {s.database or s.service or ''} "
                   f"owner {d['owner_user']} (password {d['owner_password']}), bot {d['bot_user']}"
                   + (f"  PROBLEM: {'; '.join(problems)}" if problems else ""))  # fmt: skip


@db_app.command("check")
def db_check(name: str) -> None:
    """Connect to a configured server as the owner and as the bot."""
    from labctl import dbservers

    try:
        typer.echo(dbservers.check(dbservers.server(_root(), name)))
    except dbservers.DbServerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@db_app.command("use")
def db_use(app_id: str, name: str) -> None:
    """Choose the database an app's lab uses: postgres, sqlserver, or a configured server. Run
    `labctl lab up <app>` afterwards; the app's tables are created there."""
    from labctl import dbservers

    root = _root()
    path = root / "apps" / app_id / "app.yaml"
    model, problems = repo.load(path, "app")
    if model is None or model.lab is None:
        typer.echo(f"apps/{app_id}/app.yaml: no lab section yet" if model else
                   "; ".join(map(str, problems)), err=True)  # fmt: skip
        raise typer.Exit(1)
    if name not in dbservers.BUILT_IN:
        try:
            dbservers.server(root, name)
        except dbservers.DbServerError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["lab"]["database"] = None if name == "postgres" else name
    if data["lab"]["database"] is None:
        data["lab"].pop("database")
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, width=100), encoding="utf-8", newline="\n"
    )
    typer.echo(f"{app_id} uses {name}; run `labctl lab up {app_id}`")


@lab_app.command("reserve")
def lab_reserve(
    app_id: str,
    minutes: int = typer.Option(60, "--minutes", min=1, max=480),
    purpose: str = typer.Option(None, "--purpose", help="What for, shown to others."),
) -> None:
    """Reserve an app's lab: others can look, but their changes are refused until it ends.
    You are TWINLAB_HOLDER, else your login name."""
    who = lab_mod.holder()
    if not who:
        typer.echo("set TWINLAB_HOLDER to your name", err=True)
        raise typer.Exit(2)
    lease = _lab_call(lambda: lab_mod.agent(_root(), app_id).take_lease(who, minutes, purpose))
    typer.echo(f"{app_id}: reserved by {lease['holder']} until {lease['until']}")


@lab_app.command("release")
def lab_release(
    app_id: str, force: bool = typer.Option(False, "--force", help="Release someone else's.")
) -> None:
    """Release a reservation (your own, or anyone's with --force)."""
    reply = _lab_call(lambda: lab_mod.agent(_root(), app_id).release_lease(force))
    was = (reply or {}).get("released")
    typer.echo(f"{app_id}: released" + (f" ({was['holder']}'s)" if was else " (it was free)"))


@data_app.command("pin")
def data_pin(app_id: str, baseline_id: str, unpin: bool = typer.Option(False, "--unpin")) -> None:
    """Keep a baseline however many newer ones are taken (or --unpin)."""
    _lab_call(lambda: lab_mod.agent(_root(), app_id).pin_baseline(baseline_id, not unpin))
    typer.echo(f"{baseline_id}: {'unpinned' if unpin else 'pinned'}")


@app.command()
def portal(
    host: str = typer.Option(
        "127.0.0.1", help="0.0.0.0 to let colleagues reach it (set a password)."
    ),
    port: int = typer.Option(8790),
) -> None:
    """Start the Twin Lab portal (web page) for this repository."""
    import uvicorn

    from labctl.portal.app import create_app

    if host not in ("127.0.0.1", "localhost") and not os.environ.get("TWINLAB_PORTAL_PASSWORD"):
        typer.echo(
            "Refusing to listen beyond this machine without TWINLAB_PORTAL_PASSWORD set.", err=True
        )
        raise typer.Exit(2)
    typer.echo(f"Twin Lab portal on http://{host}:{port}  (repository {_root()})")
    uvicorn.run(create_app(_root()), host=host, port=port, log_level="warning")
