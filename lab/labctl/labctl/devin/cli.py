"""`labctl devin ...`: hand a brief to Devin, by hand (package) or through the API (start).

Wired into the main CLI by the integrator: `app.add_typer(devin_cli.app, name="devin")`.
Onboarding and troubleshooting: docs/devin.md.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from labctl import repo as repo_mod
from labctl.devin import package as pkg
from labctl.devin.client import DevinClient, DevinError
from labctl.devin.config import ConfigError, DevinConfig, load_config
from labctl.devin.jobs import BuildJobs, Job, JobError, JobStore, http_hint

app = typer.Typer(no_args_is_help=True, help="Hand a build brief to Devin (docs/devin.md).")


def _root() -> Path:
    try:
        return repo_mod.find_root()
    except repo_mod.NotARepo as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _config(root: Path) -> DevinConfig:
    try:
        return load_config(root)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _client(cfg: DevinConfig) -> DevinClient:
    if not cfg.configured:
        raise JobError(
            f"Devin is not configured: {', '.join(cfg.missing)} not set. "
            "See docs/devin.md, or use the manual path: labctl devin package <app>"
        )
    return DevinClient(cfg.api_key or "", cfg.org_id or "", cfg.base_url)


def _jobs(root: Path, cfg: DevinConfig) -> BuildJobs:
    return BuildJobs(JobStore(cfg.state_dir), lambda: _client(cfg), root)


def _repo(given: str | None, cfg: DevinConfig, required: bool) -> str | None:
    value = given or cfg.github_repo
    if not value:
        if required:
            typer.echo("No GitHub repository: pass --repo owner/name or set GITHUB_REPO.", err=True)
            raise typer.Exit(2)
        return None
    try:
        return pkg.normalize_repo(value)
    except pkg.PackageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _show(job: Job, detail: bool = False) -> None:
    prs = ", ".join(job.pr_urls) or "-"
    raw = "/".join(x for x in (job.status, job.status_detail) if x) or "-"
    if not detail:
        typer.echo(f"{job.id}  {job.app_id:<12} {job.mode:<6} {job.state:<16} {raw:<24} {prs}")
        return
    lines = [
        ("job", job.id),
        ("app", job.app_id),
        ("mode", job.mode),
        ("state", job.state),
        ("devin status", raw),
        ("repository", job.repo),
        ("session", job.session_url),
        ("pull requests", prs),
        ("ACUs used", job.acus_consumed),
        ("package", job.package_path),
        ("brief", job.brief_digest),
        ("created", job.created_at),
        ("updated", job.updated_at),
        ("last polled", job.last_polled_at),
        ("finished", job.finished_at),
        ("last error", job.last_error),
    ]
    for label, value in lines:
        if value not in (None, ""):
            typer.echo(f"{label + ':':<15}{value}")
    if job.state == "waiting_for_user":
        typer.echo(
            "\nDevin is waiting for a person. Open the session, or answer with:\n"
            f'  labctl devin message {job.id} "<your answer>"'
        )


def _fail(exc: Exception) -> None:
    text = str(exc)
    if isinstance(exc, DevinError) and (hint := http_hint(exc.status)):
        text += f"\n  hint: {hint}"
    typer.echo(text, err=True)
    raise typer.Exit(1) from exc


@app.command()
def package(
    app_id: str = typer.Argument(..., help="App folder under apps/."),
    repo: str = typer.Option(None, "--repo", help="GitHub repository owner/name for Devin."),
    out: Path = typer.Option(None, "--out", help="Folder for the zip. Default: <state>/packages."),
    record: bool = typer.Option(True, "--record/--no-record", help="Track it as a manual job."),
) -> None:
    """Build the zip a person uploads to Devin by hand (the manual path)."""
    root = _root()
    cfg = _config(root)
    gh = _repo(repo, cfg, required=False)
    try:
        path = pkg.build(root, app_id, out or cfg.state_dir / "packages", repo=gh)
    except pkg.PackageError as exc:
        _fail(exc)
    typer.echo(f"Package: {path}")
    if not gh:
        typer.echo(
            "Warning: no repository given (--repo or GITHUB_REPO); the task asks Devin to find it."
        )
    if record:
        job = _jobs(root, cfg).record_manual(app_id, path, repo=gh)
        typer.echo(f"Manual job: {job.id}")
        step5 = f"labctl devin attach-pr {job.id} <pull request URL>"
    else:
        step5 = "record the pull request URL in Twin Lab"
    typer.echo(
        "\nNext:\n"
        "  1. Open https://app.devin.ai and start a new session.\n"
        f"  2. Attach {path.name}.\n"
        "  3. Paste the whole of DEVIN_TASK.md (inside the zip) as the message, unchanged.\n"
        "  4. Wait for Devin to open the pull request; answer if it asks.\n"
        f"  5. {step5}"
    )


@app.command()
def start(
    app_id: str = typer.Argument(..., help="App folder under apps/."),
    repo: str = typer.Option(None, "--repo", help="GitHub repository owner/name."),
    max_acu: int = typer.Option(
        None, "--max-acu", min=1, help="Stop the session at this many ACUs."
    ),
    wait: bool = typer.Option(False, "--wait", help="Stay and follow the job until it stops."),
    interval: float = typer.Option(30.0, "--interval", min=1.0, help="Seconds between polls."),
    force: bool = typer.Option(
        False, "--force", help="Start even though a build of this app is still unfinished."
    ),
) -> None:
    """Start a Devin session through the API (the direct path)."""
    root = _root()
    cfg = _config(root)
    gh = _repo(repo, cfg, required=True)
    if not cfg.configured:
        typer.echo(
            f"Devin is not configured ({', '.join(cfg.missing)} missing). See docs/devin.md, "
            f"or use the manual path: labctl devin package {app_id}",
            err=True,
        )
        raise typer.Exit(2)
    jobs = _jobs(root, cfg)
    jobs.poll_interval = interval
    try:
        job = jobs.start(app_id, gh or "", max_acu_limit=max_acu, force=force)
    except (pkg.PackageError, JobError, DevinError) as exc:
        jobs.stop()
        _fail(exc)
    _show(job, detail=True)
    if job.state == "failed":
        raise typer.Exit(1)
    if not wait:
        jobs.stop()
        typer.echo(f"\nFollow it with: labctl devin status {job.id}")
        return
    last = None
    try:
        while True:
            job = jobs.get(job.id) or job
            now = (job.state, job.status, job.status_detail, tuple(job.pr_urls))
            if now != last:
                _show(job)
                last = now
            if job.terminal:
                break
            time.sleep(1)
    finally:
        jobs.stop()
    _show(job, detail=True)
    raise typer.Exit(0 if job.state == "finished" else 1)


@app.command()
def status(
    job_id: str = typer.Argument(None, help="Job id (or a unique prefix). Default: all jobs."),
    refresh: bool = typer.Option(True, "--refresh/--no-refresh", help="Ask Devin first."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Show build jobs. Unfinished direct jobs are refreshed from Devin when it is configured."""
    root = _root()
    cfg = _config(root)
    jobs = _jobs(root, cfg)
    selected = [jobs.get(job_id)] if job_id else jobs.list()
    if job_id and selected[0] is None:
        typer.echo(f"no job {job_id!r}", err=True)
        raise typer.Exit(1)
    shown: list[Job] = []
    for job in selected:
        assert job is not None
        if (
            refresh
            and cfg.configured
            and job.mode == "direct"
            and job.devin_id
            and not job.terminal
        ):
            try:
                job = jobs.refresh(job.id)
            except DevinError as exc:
                typer.echo(f"{job.id}: could not refresh: {exc}", err=True)
                job = jobs.get(job.id) or job
        shown.append(job)
    jobs.stop()
    if as_json:
        typer.echo(json.dumps([j.to_dict() for j in shown], indent=2))
    elif job_id:
        _show(shown[0], detail=True)
    elif not shown:
        typer.echo("No build jobs yet. Start one with: labctl devin package|start <app>")
    else:
        for job in shown:
            _show(job)


@app.command()
def message(
    job_id: str = typer.Argument(..., help="Job id."),
    text: str = typer.Argument(..., help="What to tell Devin."),
) -> None:
    """Send a message to the job's Devin session (answers a question, resumes a suspension)."""
    root = _root()
    cfg = _config(root)
    jobs = _jobs(root, cfg)
    try:
        job = jobs.message(job_id, text)
    except (JobError, DevinError) as exc:
        _fail(exc)
    finally:
        jobs.stop()
    _show(job, detail=True)


@app.command()
def cancel(job_id: str = typer.Argument(..., help="Job id.")) -> None:
    """Terminate the job's Devin session and mark the job cancelled."""
    root = _root()
    cfg = _config(root)
    jobs = _jobs(root, cfg)
    try:
        job = jobs.cancel(job_id)
    except (JobError, DevinError) as exc:
        _fail(exc)
    finally:
        jobs.stop()
    _show(job, detail=True)


@app.command("attach-pr")
def attach_pr(
    job_id: str = typer.Argument(..., help="Job id."),
    pr_url: str = typer.Argument(..., help="https://github.com/owner/name/pull/123"),
    session_url: str = typer.Option(None, "--session", help="The Devin session URL, if known."),
) -> None:
    """Record the pull request of a build (finishes a manual job)."""
    root = _root()
    cfg = _config(root)
    jobs = _jobs(root, cfg)
    try:
        job = jobs.attach_pr(job_id, pr_url, session_url=session_url)
    except JobError as exc:
        _fail(exc)
    _show(job, detail=True)


@app.command()
def check() -> None:
    """Check the Devin settings and make one cheap authenticated call."""
    root = _root()
    cfg = _config(root)
    ok = True
    where = cfg.local_file or f"{root / 'twinlab.local.yaml'} (not present)"
    typer.echo(f"Settings file:  {where}")
    typer.echo(f"API key:        {cfg.masked_key}  [{cfg.sources.get('DEVIN_API_KEY', '-')}]")
    typer.echo(
        f"Organisation:   {cfg.org_id or '(not set)'}  [{cfg.sources.get('DEVIN_ORG_ID', '-')}]"
    )
    typer.echo(f"Base URL:       {cfg.base_url}")
    typer.echo(f"GitHub repo:    {cfg.github_repo or '(not set)'}")
    typer.echo(f"State folder:   {cfg.state_dir}")
    if cfg.api_key and not cfg.api_key.startswith("cog_"):
        typer.echo("Warning: service-user keys start with cog_; this one does not.")
    if cfg.github_repo:
        try:
            pkg.normalize_repo(cfg.github_repo)
        except pkg.PackageError as exc:
            typer.echo(f"Problem: GITHUB_REPO: {exc}")
            ok = False
    else:
        typer.echo("Note: GITHUB_REPO is not set; pass --repo to start and package.")
    if not cfg.configured:
        typer.echo(
            f"Problem: {', '.join(cfg.missing)} not set. Put them in twinlab.local.yaml or the "
            "environment (docs/devin.md). The manual path works without them."
        )
        raise typer.Exit(1)
    client = DevinClient(cfg.api_key or "", cfg.org_id or "", cfg.base_url, timeout=15, max_tries=2)
    try:
        client.list_sessions(first=1)
    except DevinError as exc:
        typer.echo(f"Problem: Devin API call failed: {exc}")
        if hint := http_hint(exc.status):
            typer.echo(f"  hint: {hint}")
        raise typer.Exit(1) from exc
    finally:
        client.close()
    typer.echo("Devin API: key and organisation accepted.")
    typer.echo(
        "Not checkable through the API: the Devin GitHub App connection. Confirm in Devin under "
        "Settings > Connections > GitHub that the repository above is connected."
    )
    if not ok:
        raise typer.Exit(1)
