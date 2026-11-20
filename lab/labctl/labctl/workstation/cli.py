"""`labctl workstation`: record a session across applications on this PC."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml

app = typer.Typer(no_args_is_help=True,
                  help="Record one session across web and desktop applications.")  # fmt: skip


def _targets(file: Path | None, exe: list[str], origin: list[str], steps: list[str],
             tn3270: list[str] | None = None):  # fmt: skip
    from labctl.workstation.levels import Targets

    items: list[dict] = []
    deny: list[str] = []
    if file is not None:
        doc = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        items = list(doc.get("targets") or [])
        deny = list(doc.get("deny_keys") or [])
    items += [{"exe": e} for e in exe] + [{"origin": o} for o in origin]
    items += [{"exe": e, "level": "steps"} for e in steps]
    items += [{"tn3270": m} for m in tn3270 or []]
    try:
        return Targets.from_list(items, deny)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@app.command("record")
def record(
    out: Path = typer.Option(Path("recordings"), help="Folder that holds the session folders."),
    targets: Path | None = typer.Option(
        None, help="YAML: targets: [{key|exe|origin, level, start}]"
    ),
    exe: list[str] = typer.Option([], help="A desktop target by executable name (full level)."),
    origin: list[str] = typer.Option([], help="A web target by origin (full level)."),
    steps_exe: list[str] = typer.Option(
        [], "--steps-exe", help="A desktop application at steps level."
    ),
    name: str = typer.Option("", help="A name for the session."),
    minutes: float = typer.Option(240, help="Stop after this many minutes."),
    tn3270: list[str] = typer.Option([], help="A mainframe target: host:port (TN3270)."),
    browser_profile: Path | None = typer.Option(
        None, help="Keep the session browser's profile here (for an extension such as UiPath's)."
    ),
    cdp: str | None = typer.Option(
        None, help="Attach to a running browser's DevTools address instead of starting one."
    ),
) -> None:
    """Record until Ctrl+C, or until a file named `stop` appears in the session folder."""
    from labctl.workstation.session import Session, recover_all

    t = _targets(targets, exe, origin, steps_exe, tn3270)
    for done in recover_all(out):
        typer.echo(f"recovered an unfinished session: {done}")
    s = Session(out, t, name=name, max_seconds=minutes * 60, browser_profile=browser_profile,
                cdp=cdp)  # fmt: skip
    typer.echo(f"recording session {s.id} in {s.folder} (Ctrl+C or a 'stop' file ends it)")
    for i, m in enumerate(t.mainframes()):
        port = m.listen if m.listen is not None else 23270 + i
        typer.echo(f"mainframe {m.tn3270}: point the terminal at 127.0.0.1:{port}")
    try:
        folder = s.run()
    except KeyboardInterrupt:
        s.stop()
        folder = s.folder
    box = json.loads((folder / "portal-outbox.json").read_text(encoding="utf-8"))
    typer.echo(f"applications recorded: {len(box['apps'])}")
    for a in box["apps"]:
        typer.echo(f"  {a['level']:8} {a['key']}  {len(box['flows'].get(a['key'], []))} step(s)")


@app.command("finalize")
def finalize(folder: Path) -> None:
    """Write session.jsonl and portal-outbox.json again (or recover an unfinished session)."""
    from labctl.workstation import session, timeline

    if not session.recover(folder):
        timeline.build(folder)
    typer.echo(f"wrote {folder / 'session.jsonl'} and {folder / 'portal-outbox.json'}")


@app.command("recover")
def recover_cmd(
    folder: Path | None = typer.Argument(None, help="One session folder (default: every one)."),
    out: Path = typer.Option(Path("recordings"), help="Folder that holds the session folders."),
) -> None:
    """Rebuild sessions the recorder left unfinished (it was killed) from their capture journal."""
    from labctl.workstation import session, timeline

    done = (
        ([folder.name] if session.recover(folder) else []) if folder else session.recover_all(out)
    )
    for name in done:
        path = (folder if folder else out / name) / "portal-outbox.json"
        c = timeline.read_references(path).get("completeness") or {}
        typer.echo(f"recovered {name}: {c.get('overall', 'unknown')}")
        for ch, st in (c.get("channels") or {}).items():
            if st["status"] == "not_requested":
                continue
            typer.echo(f"  {ch:9} {st['status']}")
            for g in st["gaps"]:
                typer.echo(f"    gap {g['from']} - {g['to']}: {g['reason']}")
    if not done:
        typer.echo("nothing to recover")


@app.command("propose")
def propose_cmd(
    project: Path,
    out: Path | None = typer.Option(None, help="Write the proposal here (YAML) for --targets."),
) -> None:
    """Propose a session's targets from a UiPath project's XAML (nothing is run)."""
    from labctl.workstation.propose import propose

    try:
        got = propose(project)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    doc = {"targets": [{k: v for k, v in t.items() if k != "uses"} for t in got["targets"]]}
    text = yaml.safe_dump(doc, sort_keys=False)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)
    if got["browser"]:
        typer.echo(f"browser: {got['browser']}")
    for note in got["notes"]:
        typer.echo(f"note: {note}")
