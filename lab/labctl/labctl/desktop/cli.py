"""`labctl desktop`: record, analyse, split and verify Windows desktop applications."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Windows desktop applications (UI Automation).")


def _root() -> Path:
    from labctl import repo

    try:
        return repo.find_root()
    except repo.NotARepo as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _out(out: Path | None, app_id: str | None, name: str | None) -> Path:
    if out is not None:
        return out
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return _root() / "evidence" / (app_id or "desktop") / (name or f"desktop-{stamp}")


def _fail(exc: Exception) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(2) from exc


def _done(path: Path) -> None:
    steps = (path / "steps.jsonl").read_text(encoding="utf-8").count("\n")
    session = json.loads((path / "desktop.json").read_text(encoding="utf-8"))
    typer.echo(f"recorded {steps} step(s) in {path}")
    for p in session.get("processes") or []:
        t = p.get("technology") or {}
        typer.echo(f"  {p.get('process')}: {t.get('toolkit') or 'toolkit not identified'} "
                   f"({t.get('confidence') or '-'}; {t.get('marker')})")  # fmt: skip
        if t.get("problem"):
            typer.echo(f"  PROBLEM  {t['problem']}")
    for note in session.get("notes") or []:
        typer.echo(f"  note: {note}")


@app.command()
def record(
    exe: Path = typer.Argument(..., help="The application's executable."),
    app_id: str = typer.Option(None, "--app", help="Application id: evidence/<app>/<name>/."),
    name: str = typer.Option(None, help="Recording name. Default: desktop-<UTC time>."),
    out: Path = typer.Option(None, help="Recording folder (instead of evidence/<app>/<name>/)."),
    arg: list[str] = typer.Option([], "--arg", help="An argument for the executable (repeat)."),
    screenshots: bool = typer.Option(False, help="Keep a picture of the window per step."),
    keep_open: bool = typer.Option(False, help="Leave the application running at the end."),
    max_minutes: float = typer.Option(30.0, help="Stop after this many minutes."),
) -> None:
    """Start an application and record a person using it; stop with Ctrl+C or by closing it."""
    from labctl.desktop import DesktopUnavailable, recorder

    path = _out(out, app_id, name)
    typer.echo(f"recording {exe.name}; use the application, then close it or press Ctrl+C")
    try:
        recorder.record(str(exe), path, args=list(arg), close=not keep_open,
                        screenshots=screenshots, max_seconds=max_minutes * 60)  # fmt: skip
    except (DesktopUnavailable, RuntimeError) as exc:
        _fail(exc)
    _done(path)


@app.command()
def attach(
    process: str = typer.Option(None, help="Executable name of a running application."),
    pid: int = typer.Option(None, help="Process id of a running application."),
    title: str = typer.Option(None, help="Regular expression on a window title."),
    app_id: str = typer.Option(None, "--app", help="Application id: evidence/<app>/<name>/."),
    name: str = typer.Option(None, help="Recording name."),
    out: Path = typer.Option(None, help="Recording folder."),
    screenshots: bool = typer.Option(False, help="Keep a picture of the window per step."),
    max_minutes: float = typer.Option(30.0, help="Stop after this many minutes."),
) -> None:
    """Record an application that is already running (it keeps running afterwards)."""
    from labctl.desktop import DesktopUnavailable, recorder

    path = _out(out, app_id, name)
    typer.echo("recording; press Ctrl+C to stop")
    try:
        recorder.attach(path, pid=pid, process=process, title=title, screenshots=screenshots,
                        max_seconds=max_minutes * 60)  # fmt: skip
    except (DesktopUnavailable, RuntimeError) as exc:
        _fail(exc)
    _done(path)


@app.command()
def analyse(
    recording: Path = typer.Argument(..., help="A desktop recording folder."),
    app_id: str = typer.Option(..., "--app", help="Application id for the model."),
    out: Path = typer.Option(None, help="Write the model here (YAML); default: print a summary."),
) -> None:
    """The app model a desktop recording shows: windows, controls, menus, grids, codes."""
    import yaml

    from labctl.desktop import model as dmodel

    if not dmodel.is_recording(recording):
        _fail(ValueError(f"{recording}: not a desktop recording (desktop.json and steps.jsonl)"))
    m = dmodel.analyse(app_id, recording)
    if out is not None:
        out.write_text(yaml.safe_dump(m.model_dump(mode="json", by_alias=True, exclude_none=True),
                                      sort_keys=False, width=100), encoding="utf-8")  # fmt: skip
        typer.echo(f"wrote {out}")
    tk = (m.stack.desktop or {}).get("toolkit") or "not identified"
    typer.echo(f"{app_id}: toolkit {tk}; {len(m.screens)} window(s), "
               f"{sum(len(s.elements) for s in m.screens)} control(s), {len(m.tables)} candidate "
               f"table(s), {len(m.codes)} code list(s), {len(m.rules)} rule(s)")  # fmt: skip
    for s in m.screens:
        typer.echo(f"  {s.desktop.get('kind')}: {s.name}  {s.desktop['window']['selector']}  "
                   f"({len(s.elements)} controls)")  # fmt: skip
    for p in m.problems:
        typer.echo(f"  OPEN  {p}")


@app.command()
def split(
    recording: Path = typer.Argument(..., help="A desktop recording folder."),
    proposal: Path = typer.Option(None, help="An edited proposal to apply."),
    apply: bool = typer.Option(False, "--apply", help="Write evidence/<app>/<recording>/."),
    overwrite: bool = typer.Option(False, help="Replace parts written before."),
) -> None:
    """Propose one evidence set per application in the recording; `--apply` writes them."""
    from labctl.desktop import split as dsplit

    try:
        prop = dsplit.load(proposal) if proposal else dsplit.propose(recording)
        if not apply:
            path = proposal or recording.parent / f"{recording.name}.desktop-split.yaml"
            if proposal is None:
                dsplit.save(prop, path)
            for e in prop.apps:
                typer.echo(f"  {e.id}: {e.process}, {e.steps} step(s), include={e.include}")
            typer.echo(f"proposal: {path}; edit it, then run again with --proposal {path} --apply")
            return
        paths = dsplit.apply(recording, prop, _root(), overwrite=overwrite)
    except dsplit.ProposalError as exc:
        _fail(exc)
    for p in paths:
        typer.echo(f"wrote {p}")


@app.command()
def verify(
    app_id: str = typer.Argument(..., help="The demo app's id."),
    exe: Path = typer.Option(..., "--exe", help="The built demo app's executable."),
    recording: list[Path] = typer.Option(
        [],
        "--recording",
        help="Recording(s); default: all desktop recordings under evidence/<app>/.",
    ),  # fmt: skip
    value: list[str] = typer.Option(
        [],
        "--value",
        help="AutomationId=text for a field whose "
        "value matters (a password, a search key). Repeat; several for one id are typed in order.",
    ),  # fmt: skip
    save: bool = typer.Option(True, help="Keep the report where readiness reads it."),
) -> None:
    """Start the built app, replay each recording, compare every window's controls."""
    from labctl.behaviour import fidelity
    from labctl.desktop import DesktopUnavailable
    from labctl.desktop import model as dmodel
    from labctl.desktop import verify as dverify

    root = _root()
    recs = list(recording) or sorted(p for p in (root / "evidence" / app_id).glob("*")
                                     if dmodel.is_recording(p))  # fmt: skip
    if not recs:
        _fail(ValueError(f"no desktop recording for {app_id}: give --recording"))
    values = {}
    for v in value:
        k, sep, text = v.partition("=")
        if not sep:
            _fail(ValueError(f"--value {v!r}: expected AutomationId=text"))
        values.setdefault(k, []).append(text)
    worst = 0
    for rec in recs:
        try:
            report = dverify.verify(str(exe), rec, values=values)
        except DesktopUnavailable as exc:
            _fail(exc)
        typer.echo(report.markdown())
        if save:
            typer.echo(f"saved fidelity report {fidelity.save(root, app_id, report)}")
        worst = max(worst, 1 if report.status == "fail" else 0)
    raise typer.Exit(worst)
