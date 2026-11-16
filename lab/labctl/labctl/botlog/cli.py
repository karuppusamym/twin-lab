"""`labctl botlog ...`: robot logs, NetLog, bot traces, calibration and parity.

Wired into labctl by the integrator: `app.add_typer(botlog_cli.app, name="botlog")`.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml

from labctl.botlog import netlog as netlog_mod
from labctl.botlog import parity as parity_mod
from labctl.botlog import robotlog
from labctl.botlog import trace as trace_mod
from labctl.botlog.models import BotTrace, RunTrace
from labctl.botlog.recording import Recording
from labctl.botlog.recording import load as load_recording
from labctl.models import Session

app = typer.Typer(
    no_args_is_help=True, help="Bot evidence: robot logs, traces, calibration, parity."
)


def _dump(data, out: Path | None) -> None:
    text = json.dumps(data, indent=2, default=str)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


def load_analysis(automation: Path | None):
    """An automation folder (or automation.yaml) -> the XAML analysis, or its declared touches."""
    if automation is None:
        return None
    file = automation / "automation.yaml" if automation.is_dir() else automation
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    src = data.get("source") or {}
    project = file.parent / (src.get("path") or "")
    if data.get("type") == "uipath" and (project / "project.json").exists():
        from labctl.analyse import uipath

        return uipath.analyse(project, src.get("entry"), data.get("bindings") or {})
    return data.get("touches") or []


def _pair(run: RunTrace, recordings: list[Recording]) -> Recording | None:
    for rec in recordings:
        first = next((s.ts for s in rec.steps if s.ts), None)
        if first and run.started and run.ended and run.started <= first <= run.ended:
            return rec
    return None


@app.command()
def parse(
    log: Path = typer.Argument(..., exists=True, help="Orchestrator export or NLog JSON lines"),
    out: Path | None = typer.Option(None, "--out", help="write the run traces here (JSON)"),
) -> None:
    """Parse a robot log into one trace per run."""
    result = robotlog.parse_report(log)
    for r in result.runs:
        typer.echo(
            f"{r.run_id}  job={r.job_id}  steps={len(r.steps)}  bad_lines={r.bad_lines}"
            f"  unmapped={','.join(r.unmapped_fields) or '-'}",
            err=out is None,
        )
    typer.echo(f"{len(result.runs)} run(s), {result.bad_lines} bad line(s)", err=out is None)
    _dump([r.model_dump(mode="json", by_alias=True) for r in result.runs], out)


@app.command()
def netlog(
    file: Path = typer.Argument(..., exists=True, help="Chrome --log-net-log JSON"),
    out: Path | None = typer.Option(None, "--out", help="requests.jsonl to write"),
) -> None:
    """Parse a Chrome NetLog into requests (url, method, status, start, end, initiator)."""
    requests = netlog_mod.parse(file)
    if out:
        netlog_mod.write_jsonl(requests, out)
        typer.echo(f"wrote {len(requests)} request(s) to {out}")
    else:
        for r in requests:
            typer.echo(r.model_dump_json())


@app.command()
def trace(
    log: Path = typer.Argument(..., exists=True, help="robot log"),
    automation: Path | None = typer.Option(None, "--automation", help="automation folder"),
    recording: list[Path] = typer.Option([], "--recording", help="recording folder(s)"),
    netlog_file: Path | None = typer.Option(None, "--netlog", help="Chrome NetLog of the runs"),
    source: str = typer.Option("unknown", help="production, harness, agent or unknown"),
    out: Path | None = typer.Option(None, "--out", help="write the bot traces here (JSON)"),
) -> None:
    """Join a robot log with the automation analysis (and recordings) into bot traces."""
    analysis = load_analysis(automation)
    recs = [load_recording(p) for p in recording]
    requests = netlog_mod.parse(netlog_file) if netlog_file else None
    auto_id = None
    if automation is not None:
        file = automation / "automation.yaml" if automation.is_dir() else automation
        auto_id = (yaml.safe_load(file.read_text(encoding="utf-8")) or {}).get("id")
    traces = []
    for run in robotlog.parse(log):
        rec = _pair(run, recs) if recs else None
        t = trace_mod.build(analysis, run, rec, requests, automation=auto_id, source=source)
        traces.append(t)
        typer.echo(f"{t.run_id}: {len(t.steps)} step(s), {len(t.gaps)} gap(s)", err=out is None)
        for g in t.gaps:
            typer.echo(f"  gap: {g.where}: {g.what}", err=out is None)
    _dump([t.model_dump(mode="json") for t in traces], out)


def _traces(paths: list[Path]) -> list[BotTrace]:
    out: list[BotTrace] = []
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        out += [BotTrace.model_validate(d) for d in items]
    return out


@app.command()
def calibrate(
    traces: list[Path] = typer.Argument(..., exists=True, help="bot trace JSON file(s)"),
    out: Path | None = typer.Option(None, "--out", help="write the fragment here (YAML or JSON)"),
) -> None:
    """Condition-profile fragment (p50/p95 per screen step) from bot-trace timings."""
    fragment = trace_mod.calibrate(_traces(traces))
    if out and out.suffix in (".yaml", ".yml"):
        out.write_text(yaml.safe_dump(fragment, sort_keys=False), encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        _dump(fragment, out)


@app.command()
def parity(
    base: Path = typer.Argument(..., exists=True, help="bot trace JSON (the golden run)"),
    other: Path = typer.Argument(
        ..., exists=True, help="bot trace JSON, recording folder or Session JSON"
    ),
    automation: Path | None = typer.Option(
        None, "--automation", help="for a recording: names fields"
    ),
    base_session: Path | None = typer.Option(
        None, "--base-session", help="lab Session of the bot run"
    ),
    other_session: Path | None = typer.Option(
        None, "--other-session", help="lab Session of the other run"
    ),
    json_out: Path | None = typer.Option(None, "--json", help="write the report as JSON"),
    md_out: Path | None = typer.Option(None, "--md", help="write the report as Markdown"),
) -> None:
    """Compare another run with the bot trace. Exit 1 on fail."""
    golden = _traces([base])[0]
    if other.is_dir():
        theirs: BotTrace | Session = trace_mod.from_recording(
            load_recording(other), load_analysis(automation)
        )
    else:
        data = json.loads(other.read_text(encoding="utf-8"))
        data = data[0] if isinstance(data, list) else data
        theirs = (
            Session.model_validate(data) if "session_id" in data else BotTrace.model_validate(data)
        )

    def session(p: Path | None) -> Session | None:
        return Session.model_validate(json.loads(p.read_text(encoding="utf-8"))) if p else None

    report = parity_mod.compare(golden, theirs, None, session(base_session), session(other_session))
    if json_out:
        json_out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if md_out:
        md_out.write_text(report.markdown(), encoding="utf-8")
    typer.echo(report.markdown())
    if report.status == "fail":
        raise typer.Exit(1)
