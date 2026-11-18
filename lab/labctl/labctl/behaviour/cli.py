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
        [],
        "--value",
        help="selector=value for unrecorded typed values; 6:selector=value for step 6 only "
        "(turned into bindings for the steps they match)",
    ),
    bindings: Path | None = typer.Option(
        None, "--bindings", help="bindings file (default: bindings.yaml beside the recording)"
    ),
    expect: Path | None = typer.Option(
        None, "--expect", help="YAML/JSON list of the semantic actions the flow must have"
    ),
    storage_state: Path | None = typer.Option(
        None, "--storage-state", help="browser data to start from (the replica's baseline)"
    ),
    headed: bool = typer.Option(False, "--headed"),
    json_out: Path | None = typer.Option(None, "--json", help="write the report as JSON"),
    md_out: Path | None = typer.Option(None, "--md", help="write the report as Markdown"),
    app_id: str | None = typer.Option(
        None, "--app", help="keep the report for this app (portal, and the readiness gate)"
    ),
) -> None:
    """Replay a recording against a running app and compare each step's DOM. Exit 1 unless the
    replay passed (a failed or BLOCKED replay exits 1)."""
    if recording.is_file():
        if actual is None:
            raise typer.BadParameter("comparing DOM files needs two files")
        diffs = fidelity_mod.compare_files(recording, actual)
        for d in diffs:
            typer.echo(d.text())
        typer.echo("pass" if not diffs else f"fail: {len(diffs)} difference(s)")
        raise typer.Exit(1 if diffs else 0)
    values = dict(v.split("=", 1) for v in value if "=" in v)
    expected = None
    if expect is not None:
        import yaml

        expected = [str(a) for a in yaml.safe_load(expect.read_text(encoding="utf-8")) or []]
    report = fidelity_mod.replay(recording, base_url, values, headless=not headed,
                                 bindings=bindings, expected=expected,
                                 storage_state=storage_state)  # fmt: skip
    if json_out:
        json_out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if md_out:
        md_out.write_text(report.markdown(), encoding="utf-8")
    if app_id:
        from labctl.cli import _root

        typer.echo(f"kept as {fidelity_mod.save(_root(), app_id, report)}")
    typer.echo(report.markdown())
    if report.status != "pass":
        raise typer.Exit(1)


@app.command("bindings")
def bindings_cmd(
    recording: Path = typer.Argument(..., exists=True, file_okay=False, help="recording folder"),
    out: Path | None = typer.Option(
        None, "--out", help="bindings file to write (default: bindings.yaml beside the recording)"
    ),
) -> None:
    """Write the bindings template: each step that needs a synthetic value (with the shape of
    what was recorded) or the title of the record it acts on. Values already filled are kept."""
    from labctl.behaviour import bindings as bindings_mod
    from labctl.botlog.recording import load as load_recording

    rec = load_recording(recording)
    path = bindings_mod.write_template(rec, out)
    needs = bindings_mod.needs(rec)
    filled = bindings_mod.load(path)
    for n in needs:
        have = (filled.values if n.kind == "value" else filled.records).get(n.seq) is not None
        shape = f" shape {n.shape!r}" if n.shape else ""
        typer.echo(f"step {n.seq:>4} {n.action:<8} {n.field or '':<32} {n.kind}{shape}"
                   f"{'' if have else '  (to fill)'}")  # fmt: skip
    typer.echo(f"wrote {path}: {len(needs)} binding(s) needed")


@app.command()
def compare(
    a: list[Path] = typer.Option(..., "--a", exists=True, help="recordings of place A (repeat)"),
    b: list[Path] = typer.Option(..., "--b", exists=True, help="recordings of place B (repeat)"),
    name_a: str = typer.Option("UAT", "--name-a"),
    name_b: str = typer.Option("production", "--name-b"),
    md_out: Path | None = typer.Option(None, "--md", help="write the report as Markdown"),
) -> None:
    """Does the app behave the same in two places (UAT and production)? Exit 1 when not."""
    from labctl.behaviour import compare as compare_mod

    result = compare_mod.compare_recordings(_recordings(a), _recordings(b), name_a, name_b)
    text = result.markdown()
    if md_out:
        md_out.write_text(text, encoding="utf-8")
    typer.echo(text)
    if result.status != "same":
        raise typer.Exit(1)
