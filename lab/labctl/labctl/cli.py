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
from labctl import evidence_cli, factory, repo
from labctl import lab as lab_mod
from labctl.analyse import run as analysis
from labctl.behaviour import cli as behaviour_cli
from labctl.bindings import BindingError
from labctl.botlog import cli as botlog_cli
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
) -> None:
    """Write apps/<app>/model.yaml, BRIEF.md, db/schema.sql and a first app.yaml from the evidence."""
    root = _root()
    if source is not None and not source.is_dir():
        typer.echo(f"source folder not found: {source}", err=True)
        raise typer.Exit(2)
    if db_engine is not None and db_engine not in sam.ENGINES:
        typer.echo(f"unknown --db-engine; one of {', '.join(sam.ENGINES)}", err=True)
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
        )
    except factory.FactoryError as exc:
        typer.echo(str(exc), err=True)
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


@app.command()
def bindings(
    automation_id: str,
    form: str = typer.Option(
        "json", help="json (ATS_BINDINGS), env, uipath (input arguments) or yaml."
    ),
) -> None:
    """What an automation needs to run against the lab: addresses, test sign-in, inputs."""
    auto = _automation(automation_id)
    try:
        b = bindings_mod.for_automation(_root(), auto)
        typer.echo(bindings_mod.render(auto, b, form))
    except BindingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def conformance(
    automation_id: str,
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """Check an automation against the running demo apps: READY or BLOCKED."""
    report = conformance_mod.check(_root(), _automation(automation_id))
    typer.echo(
        json.dumps(report.to_dict(), indent=2) if as_json else conformance_mod.render(report)
    )
    if report.verdict != "READY":
        raise typer.Exit(1)


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
