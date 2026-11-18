"""`labctl build ...`: hand a brief to a coding agent.

Wired into the main CLI by the integrator: `app.add_typer(builder_cli.app, name="build")`.

    labctl build package <app>                      write the zip, print what to do by hand
    labctl build start <app> [--builder claude|devin|manual] [--pr]
    labctl build status <app>
    labctl build attach-pr <app> <pull request URL>
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from labctl import repo as repo_mod
from labctl.builder import package as pkg
from labctl.builder.base import Builder, BuilderError, Job, jobs_for, save
from labctl.builder.config import BuilderConfigError
from labctl.builder.devin import origin_repo
from labctl.builder.manual import ManualBuilder
from labctl.builder.select import select

app = typer.Typer(no_args_is_help=True, help="Hand a build brief to a coding agent.")
_PR_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+/?$")


def _root() -> Path:
    try:
        return repo_mod.find_root()
    except repo_mod.NotARepo as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _fail(exc: Exception, code: int = 1) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code) from exc


def _package(root: Path, app_id: str, repo: str | None, out: Path | None) -> Path:
    try:
        gh = pkg.normalize_repo(repo) if repo else origin_repo(root)
        return pkg.build(root, app_id, out or pkg.default_state_dir(root) / "packages", repo=gh)
    except pkg.PackageError as exc:
        _fail(exc)
        raise  # pragma: no cover


def _show(job: Job) -> None:
    rows = [
        ("job", job.id), ("app", job.app_id), ("builder", job.builder), ("state", job.state),
        ("branch", job.branch), ("worktree", job.worktree), ("log", job.log_path),
        ("brief", job.brief_digest), ("package", job.package_path),
        ("pull requests", ", ".join(job.pr_urls)), ("session", job.session_url),
        ("changed files", len(job.changed_files) or None), ("last error", job.last_error),
        ("created", job.created_at), ("finished", job.finished_at),
    ]  # fmt: skip
    for label, value in rows:
        if value not in (None, ""):
            typer.echo(f"{label + ':':<15}{value}")
    for rel in job.changed_files[:30]:
        typer.echo(f"  changed: {rel}")
    if len(job.changed_files) > 30:
        typer.echo(f"  ... and {len(job.changed_files) - 30} more")
    if job.note:
        typer.echo(f"\n{job.note}")


@app.command()
def package(
    app_id: str = typer.Argument(..., help="App folder under apps/."),
    repo: str = typer.Option(None, "--repo", help="GitHub repository owner/name."),
    out: Path = typer.Option(None, "--out", help="Folder for the zip. Default: <state>/packages."),
) -> None:
    """Write the build package and print what a person does with any coding agent."""
    root = _root()
    path = _package(root, app_id, repo, out)
    typer.echo(ManualBuilder.instructions(path, pkg.branch_name(root, app_id), app_id))


@app.command()
def start(
    app_id: str = typer.Argument(..., help="App folder under apps/."),
    builder: str = typer.Option(
        None, "--builder", help="claude, devin or manual (default: config)."
    ),
    pr: bool = typer.Option(False, "--pr", help="claude: commit, push and open the pull request."),
    detach: bool = typer.Option(
        False, "--detach", help="claude: return at once; follow with status."
    ),
    repo: str = typer.Option(None, "--repo", help="GitHub repository owner/name."),
) -> None:
    """Start a build with the chosen builder (default `manual`)."""
    root = _root()
    try:
        chosen: Builder = select(root, builder, open_pr=pr, detach=detach)
    except (BuilderError, BuilderConfigError) as exc:
        _fail(exc, 2)
        raise  # pragma: no cover
    if pr and chosen.name != "claude":
        typer.echo("--pr only applies to the claude builder; ignored.", err=True)
    if not chosen.available():
        typer.echo(f"The {chosen.name} builder is not available: {chosen.availability()}", err=True)
        typer.echo(f"Use the manual path: labctl build package {app_id}", err=True)
        raise typer.Exit(2)
    path = _package(root, app_id, repo, None)
    try:
        job = chosen.start(path, root, app_id, pkg.branch_name(root, app_id))
    except BuilderError as exc:
        _fail(exc)
        raise  # pragma: no cover
    _show(job)
    if job.state == "failed":
        raise typer.Exit(1)


@app.command()
def status(app_id: str = typer.Argument(..., help="App folder under apps/.")) -> None:
    """Show the latest build of an app (and refresh it from its builder)."""
    root = _root()
    jobs = jobs_for(pkg.default_state_dir(root), app_id)
    if not jobs:
        typer.echo(f"No builds of {app_id} yet. Start one: labctl build start {app_id}")
        raise typer.Exit(0)
    job = jobs[0]
    try:
        job = select(root, job.builder).status(job)
    except (BuilderError, BuilderConfigError) as exc:
        typer.echo(f"Could not refresh: {exc}", err=True)
    _show(job)
    if len(jobs) > 1:
        typer.echo(
            f"\n{len(jobs) - 1} earlier build(s) under {pkg.default_state_dir(root) / 'builds' / app_id}"
        )


@app.command("attach-pr")
def attach_pr(
    app_id: str = typer.Argument(...),
    url: str = typer.Argument(..., help="https://github.com/owner/name/pull/123"),
) -> None:
    """Record the pull request a manual build produced."""
    root = _root()
    if not _PR_RE.match(url.strip()):
        typer.echo(f"Not a GitHub pull request URL: {url!r}", err=True)
        raise typer.Exit(2)
    jobs = jobs_for(pkg.default_state_dir(root), app_id)
    if not jobs:
        typer.echo(f"No builds of {app_id} to attach to.", err=True)
        raise typer.Exit(1)
    job = jobs[0]
    if url.strip() not in job.pr_urls:
        job.pr_urls.append(url.strip())
    if job.builder == "manual":
        job.state, job.note = "finished", "Pull request recorded."
        job.finished_at = job.finished_at or job.updated_at
    _show(save(job))
