"""`labctl evidence ...`: analyse and record evidence (recordings, API descriptions).

Wired into the main command line by the integrator: `app.add_typer(evidence_cli.app, name="evidence")`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer
import yaml

from labctl import model_build as mb
from labctl import repo
from labctl.appmodel import AppModel

app = typer.Typer(no_args_is_help=True, help="Evidence: recordings and API descriptions.")


@app.callback()
def _group() -> None:
    """Evidence: recordings and API descriptions."""


def kind_of(path: Path) -> str:
    """`recording` (a folder or a .har file) or `openapi` (OpenAPI or Postman)."""
    if path.is_dir() or path.suffix.lower() == ".har":
        return "recording"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return "openapi"
    return "recording" if isinstance(data, dict) and "log" in data else "openapi"


def summary(model: AppModel) -> str:
    out = []
    w = out.append
    for ev in model.evidence:
        w(
            f"evidence {ev.id} ({ev.kind}, {ev.ref}); complete for: {', '.join(ev.complete) or 'nothing'}"
        )
    front = model.stack.frontend or {}
    if front:
        name = " ".join(str(front[k]) for k in ("framework", "version") if front.get(k))
        w(f"front end: {name} ({front.get('marker', '')})")
    w(f"screens: {len(model.screens)}")
    for s in model.screens:
        seen = "address only" if s.snapshots == 0 else f"{s.snapshots or 0} snapshot(s)"
        w(f"  {s.path or s.name:<32} {seen}, {len(s.ids)} ids, {len(s.elements)} elements, "
          f"{len(s.calls)} call(s)")  # fmt: skip
    w(f"routes: {len(model.routes)}")
    for r in model.routes:
        statuses = ",".join(r.responses) or "-"
        timing = f", p95 {r.timings.p95_ms} ms over {r.timings.count}" if r.timings else ""
        w(f"  {r.key:<44} {r.basis}, status {statuses}{timing}")
    if model.rules:
        w(f"rules: {len(model.rules)}")
        for rule in model.rules:
            w(f"  {', '.join(rule.where)}: {rule.text}")
    if model.tables:
        w(f"tables: {len(model.tables)}")
        for t in model.tables:
            w(f"  {t.name} ({t.basis}): {', '.join(c.name for c in t.columns)}")
    if model.codes:
        w("code lists:")
        for key, values in model.codes.items():
            w(f"  {key}: {', '.join(values)}")
    if model.problems:
        w("problems:")
        for p in model.problems:
            w(f"  - {p}")
    return "\n".join(out)


@app.command()
def analyse(
    app_id: str = typer.Argument(..., metavar="APP", help="The app the evidence is for."),
    path: Path = typer.Argument(..., help="Recording folder, .har file, OpenAPI or Postman file."),
    pages: bool = typer.Option(
        False, "--pages", help="Write value-free page snapshots to apps/<app>/pages/."
    ),
    model_out: Path | None = typer.Option(None, "--model", help="Also write the model as YAML."),
) -> None:
    """Analyse one piece of evidence and print what it shows. Nothing recorded is kept as a value."""
    if not path.exists():
        typer.echo(f"{path} not found", err=True)
        raise typer.Exit(2)
    try:
        root = repo.find_root()
    except repo.NotARepo:
        root = Path.cwd()
    try:
        if kind_of(path) == "recording":
            pages_dir = root / "apps" / app_id / "pages" if pages else None
            model = mb.from_recording(app_id, path, root, pages_dir)
        else:
            model = mb.from_openapi(app_id, path)
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(summary(model))
    if model_out is not None:
        data = model.model_dump(mode="json", by_alias=True, exclude_none=True)
        model_out.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8")
        typer.echo(f"wrote {model_out}")


@app.command()
def record(
    app_id: str = typer.Argument(..., metavar="APP"),
    url: str = typer.Argument(..., help="Where to start, e.g. http://localhost:8720/login."),
    out: Path | None = typer.Option(
        None, "--out", help="Recording folder (default evidence/<app>/<timestamp>/)."
    ),
    no_screenshots: bool = typer.Option(False, "--no-screenshots", help="Skip the PNG per step."),
    storage_state: Path | None = typer.Option(
        None,
        "--storage-state",
        help="Browser data to start from (Playwright storage state: a declared baseline).",
    ),
) -> None:
    """Open a browser; click through the flow, then close the window to finish the recording.
    Then `labctl behaviour bindings <folder>` lists the steps a replay needs synthetic values for."""
    from labctl import recorder

    out = out or _default_out(app_id)
    typer.echo(f"recording into {out}; close the browser window to finish")
    recorder.record(url, out, screenshots=not no_screenshots, storage_state=storage_state)
    typer.echo(f"done: analyse it with `labctl evidence analyse {app_id} {out}`")
    typer.echo(f"replay values: `labctl behaviour bindings {out}` writes the bindings to fill")


def _default_out(app_id: str) -> Path:
    try:
        root = repo.find_root()
    except repo.NotARepo:
        root = Path.cwd()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return root / "evidence" / app_id / stamp


@app.command()
def attach(
    app_id: str = typer.Argument(..., metavar="APP"),
    cdp: str = typer.Option(
        "http://127.0.0.1:9222",
        "--cdp",
        help="The running browser's debugging address. Start Chrome or Edge with "
        "--remote-debugging-port=9222 (and its own --user-data-dir).",
    ),
    out: Path | None = typer.Option(None, "--out", help="Recording folder."),
    minutes: float = typer.Option(30, "--minutes", help="Stop after this long."),
    no_screenshots: bool = typer.Option(False, "--no-screenshots"),
) -> None:
    """Record a browser that is already running, such as the one a robot drives. Stop with
    Ctrl+C, by closing the browser, or after --minutes. The browser is left running."""
    from labctl import recorder

    out = out or _default_out(app_id)
    typer.echo(f"recording {cdp} into {out}; press Ctrl+C to finish")
    try:
        recorder.attach(cdp, out, max_seconds=minutes * 60, screenshots=not no_screenshots)
    except Exception as exc:  # noqa: BLE001 - Playwright raises its own error types
        typer.echo(f"could not attach to {cdp}: {str(exc).splitlines()[0]}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"done: analyse it with `labctl evidence analyse {app_id} {out}`")


@app.command()
def split(
    recording: Path = typer.Argument(..., help="A recording folder that visited several apps."),
    proposal: Path | None = typer.Option(
        None,
        "--proposal",
        help="Proposal file to read (with --apply) or write (default: "
        "<recording>.split.yaml next to the recording).",
    ),
    apply: bool = typer.Option(False, "--apply", help="Write evidence/<app>/<recording>/ per app."),
    force: bool = typer.Option(False, "--force", help="Replace folders a former split wrote."),
) -> None:
    """Split one recording into one recording per application. Without --apply: propose and
    write the proposal file for editing. With --apply: write the per-app folders."""
    from labctl import split as sp

    try:
        root = repo.find_root()
    except repo.NotARepo:
        root = Path.cwd()
    if not recording.is_dir():
        typer.echo(f"{recording} is not a recording folder", err=True)
        raise typer.Exit(2)
    target = proposal or recording.parent / f"{recording.name}.split.yaml"
    try:
        if apply and proposal is not None and proposal.is_file():
            prop = sp.load(proposal)
        else:
            prop = sp.propose(recording)
            if apply and target.is_file():
                prop = sp.load(target)
            elif not apply:
                sp.save(prop, target)
        if not apply:
            typer.echo(f"{'id':<28} {'origin':<34} {'kind':<8} steps screens")
            for e in prop.apps:
                drop = "" if e.include else "  (excluded)"
                typer.echo(
                    f"{e.id:<28} {e.origin:<34} {e.kind:<8} {e.steps:>5} {e.screens:>7}{drop}"
                )
                for o in e.embedded:
                    typer.echo(f"    embedded: {o}")
            for p in sp.problems(recording):
                typer.echo(f"recording: {p}", err=True)
            typer.echo(f"wrote {target}; edit it, then run again with --apply")
            return
        for path in sp.apply(recording, prop, root, overwrite=force):
            typer.echo(f"wrote {path}")
        typer.echo(f"sequence across apps: {sp.sequence_path(recording, root)}")
        for p in sp.problems(recording):
            typer.echo(f"recording: {p}", err=True)
    except sp.ProposalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command("record-across")
def record_across(
    name: str = typer.Argument(..., help="Recording name (lower case, digits, dashes)."),
    url: str = typer.Argument(..., help="Where the flow starts, in the first application."),
    no_screenshots: bool = typer.Option(False, "--no-screenshots"),
) -> None:
    """Record one flow that goes through several applications (every tab and popup it opens),
    into evidence/_recordings/<name>/. Then `labctl evidence build-all` makes one evidence set,
    brief and build package per application."""
    import re as _re

    from labctl import multiapp, recorder

    if not _re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        typer.echo("the name is lower-case letters, digits and dashes", err=True)
        raise typer.Exit(2)
    try:
        root = repo.find_root()
    except repo.NotARepo:
        root = Path.cwd()
    out = multiapp.recordings_dir(root) / name
    if out.exists():
        typer.echo(f"{out} already exists", err=True)
        raise typer.Exit(1)
    typer.echo(f"recording into {out}; close the last browser window to finish")
    recorder.record(url, out, screenshots=not no_screenshots)
    typer.echo(f"done: labctl evidence build-all {out}")


@app.command("fetch-code")
def fetch_code(
    recording: Path = typer.Argument(..., help="A recording folder."),
    yes: bool = typer.Option(False, "--yes", help="Fetch. Without it the files are only listed."),
    storage_state: Path | None = typer.Option(
        None, "--storage-state", help="The recorder's saved browser state: send its cookies."
    ),
    delay: float = typer.Option(1.0, "--delay", help="Seconds between two requests (min 0.2)."),
    max_files: int = typer.Option(50, "--max", help="At most this many files."),
) -> None:
    """Download the static code (scripts, style sheets, JSON) the recording's code names but the
    browser never loaded, such as the lazy chunk of a route nobody opened: GET only, the
    application's own origin only, rate limited, stored beside the recording."""
    from labctl import codefetch

    try:
        done = codefetch.fetch(recording, yes=yes, storage_state=storage_state, delay=delay,
                               max_files=max_files, say=typer.echo)  # fmt: skip
    except codefetch.FetchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    for p in done["planned"]:
        typer.echo(f"{'fetch' if yes else 'would fetch'}: {p['path']} ({p['why']})")
    for r in done["refused"]:
        typer.echo(f"refused: {r['path']}: {r['reason']}")
    if done["stopped"]:
        typer.echo(f"stopped: {done['stopped']}")
    if not yes and done["planned"]:
        typer.echo("nothing fetched: run again with --yes to fetch these files")
    elif yes:
        typer.echo(f"fetched {len(done['fetched'])} file(s) into {recording / 'code'}; "
                   "regenerate the brief to use them")  # fmt: skip


@app.command("build-all")
def build_all(
    recording: Path = typer.Argument(..., help="A recording folder (one or several applications)."),
    proposal: Path | None = typer.Option(
        None, "--proposal", help="The reviewed split proposal (default: propose, then stop)."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Accept the proposal as it is (the one beside the recording, if any)."
    ),
    db_engine: str | None = typer.Option(None, "--db-engine", help="For every app's brief."),
    out: Path | None = typer.Option(None, "--out", help="Folder for the packages."),
) -> None:
    """From one recording to a brief and a build package per application: propose the split
    (and stop for review), split, draft each app's requested flow, write each brief, build each
    package, and say what still needs a person. Safe to run again: every stage repeats or skips."""
    from labctl import multiapp

    try:
        root = repo.find_root()
    except repo.NotARepo:
        root = Path.cwd()
    if not recording.is_dir():
        typer.echo(f"{recording} is not a recording folder", err=True)
        raise typer.Exit(2)
    if proposal is not None and not proposal.is_file():
        typer.echo(f"{proposal} not found", err=True)
        raise typer.Exit(2)
    from labctl import split as sp

    try:
        done = multiapp.build_all(root, recording, proposal=proposal, yes=yes, db_engine=db_engine,
                                  package_dir=out, say=typer.echo)  # fmt: skip
    except (sp.ProposalError, multiapp.BuildAllError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if done.stopped:
        return
    if any(a.errors for a in done.apps):
        raise typer.Exit(1)


@app.command("check-links")
def check_links(
    recording: Path = typer.Argument(..., help="A recording folder."),
    yes: bool = typer.Option(
        False, "--yes", help="Ask the real application. Without it: list what would be asked."
    ),
    max_links: int = typer.Option(50, "--max", help="At most this many links are asked for."),
    delay_ms: int = typer.Option(1000, "--delay-ms", help="Time between two requests."),
    timeout: float = typer.Option(10.0, "--timeout", help="Seconds per request."),
    storage_state: Path | None = typer.Option(
        None,
        "--storage-state",
        help="The browser state the recording started from (Playwright storage state): its "
        "cookies are sent. Without it every request is anonymous.",
    ),
) -> None:
    """Where the links the recording saw but never followed lead, asked of the real application
    with HEAD or GET only, own origins only, rate limited; state-changing addresses are skipped.
    Writes <recording>/link-check.json; the next brief uses it. Opt-in: needs --yes."""
    from labctl import linkcheck

    if not recording.is_dir():
        typer.echo(f"{recording} is not a recording folder", err=True)
        raise typer.Exit(2)
    if storage_state is not None and not storage_state.is_file():
        typer.echo(f"{storage_state} not found", err=True)
        raise typer.Exit(2)
    try:
        linkcheck.check(recording, yes=yes, max_links=max_links, delay_ms=delay_ms,
                        timeout=timeout, storage_state=storage_state, say=typer.echo)  # fmt: skip
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
