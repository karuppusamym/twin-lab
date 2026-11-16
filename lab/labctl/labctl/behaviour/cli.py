"""`labctl behaviour ...`: the behaviour catalog and fidelity replay.

Wired into labctl by the integrator: `app.add_typer(behaviour_cli.app, name="behaviour")`.
"""

from __future__ import annotations

from pathlib import Path

import typer

from labctl.behaviour import catalog as catalog_mod
from labctl.behaviour import fidelity as fidelity_mod

app = typer.Typer(no_args_is_help=True, help="Client behaviour: catalog from recordings, fidelity.")


def _recordings(paths: list[Path]) -> list[Path]:
    """Recording folders; a folder holding recordings (evidence/<app>) is expanded."""
    out: list[Path] = []
    for p in paths:
        if (p / "steps.jsonl").exists():
            out.append(p)
        else:
            out += sorted(d for d in p.iterdir() if (d / "steps.jsonl").exists())
    return out


@app.command()
def catalog(
    recordings: list[Path] = typer.Argument(..., exists=True, help="recording folder(s)"),
    app_id: str | None = typer.Option(None, "--app", help="app id (default: the evidence folder)"),
    out: Path | None = typer.Option(None, "--out", help="behaviours.json to write"),
) -> None:
    """Infer behaviour rules from recordings; print the brief lines."""
    cat = catalog_mod.build(_recordings(recordings), app_id)
    if out:
        catalog_mod.write(cat, out)
        typer.echo(f"wrote {len(cat.rules)} rule(s) to {out}")
    for line in catalog_mod.brief_lines(cat):
        typer.echo(line)


@app.command()
def fidelity(
    recording: Path = typer.Argument(
        ..., exists=True, help="recording folder, or a recorded DOM file"
    ),
    actual: Path | None = typer.Argument(None, help="with a DOM file: the DOM to compare it with"),
    base_url: str | None = typer.Option(
        None, "--base-url", help="the demo app (default: as recorded)"
    ),
    value: list[str] = typer.Option(
        [], "--value", help="selector=value for unrecorded typed values"
    ),
    headed: bool = typer.Option(False, "--headed"),
    json_out: Path | None = typer.Option(None, "--json", help="write the report as JSON"),
    md_out: Path | None = typer.Option(None, "--md", help="write the report as Markdown"),
) -> None:
    """Replay a recording against a running app and compare each step's DOM. Exit 1 on fail."""
    if recording.is_file():
        if actual is None:
            raise typer.BadParameter("comparing DOM files needs two files")
        diffs = fidelity_mod.compare_files(recording, actual)
        for d in diffs:
            typer.echo(d.text())
        typer.echo("pass" if not diffs else f"fail: {len(diffs)} difference(s)")
        raise typer.Exit(1 if diffs else 0)
    values = dict(v.split("=", 1) for v in value if "=" in v)
    report = fidelity_mod.replay(recording, base_url, values, headless=not headed)
    if json_out:
        json_out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if md_out:
        md_out.write_text(report.markdown(), encoding="utf-8")
    typer.echo(report.markdown())
    if report.status == "fail":
        raise typer.Exit(1)
