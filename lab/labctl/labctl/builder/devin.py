"""The Devin builder: a thin adapter over `labctl.devin` (package upload and API session).

It reuses `BuildJobs`, so the Devin job (kept in `devin-jobs.sqlite3`, the one the portal already
shows) stays the source of truth; the build job written here only points at it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from labctl.builder import package as pkg
from labctl.builder.base import BuilderError, Job, new_job, save, stamp
from labctl.devin.client import DevinClient, DevinError
from labctl.devin.config import ConfigError, DevinConfig, load_config
from labctl.devin.jobs import BuildJobs, JobError, JobStore


def origin_repo(repo_root: Path) -> str | None:
    """`owner/name` of the `origin` remote, when it is a GitHub one."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=20, check=False,
        )  # fmt: skip
        return pkg.normalize_repo(done.stdout.strip()) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, pkg.PackageError):
        return None


class DevinBuilder:
    name = "devin"

    def __init__(self, repo_root: Path, cfg: DevinConfig | None = None) -> None:
        self.repo_root = Path(repo_root)
        try:
            self.cfg = cfg or load_config(self.repo_root)
        except ConfigError as exc:
            raise BuilderError(str(exc)) from exc
        self._jobs: BuildJobs | None = None

    def available(self) -> bool:
        return self.cfg.configured

    def availability(self) -> str:
        if self.cfg.configured:
            return f"Devin configured (key {self.cfg.masked_key})"
        return f"Devin is not configured: {', '.join(self.cfg.missing)} missing (docs/devin.md)"

    def jobs(self) -> BuildJobs:
        if self._jobs is None:
            cfg = self.cfg
            self._jobs = BuildJobs(
                JobStore(cfg.state_dir),
                lambda: DevinClient(cfg.api_key or "", cfg.org_id or "", cfg.base_url),
                self.repo_root,
            )
        return self._jobs

    def start(self, package: Path, repo_root: Path, app_id: str, branch: str) -> Job:
        if not self.available():
            raise BuilderError(self.availability())
        repo = self.cfg.github_repo or origin_repo(Path(repo_root))
        if not repo:
            raise BuilderError("no GitHub repository: set GITHUB_REPO or add an origin remote")
        try:
            devin_job = self.jobs().start(app_id, repo)
        except (JobError, DevinError, pkg.PackageError) as exc:
            raise BuilderError(str(exc)) from exc
        job = new_job(
            app_id,
            "devin",
            branch=branch,
            package_path=devin_job.package_path or str(package),
            job_dir=str(self.cfg.state_dir / "builds" / app_id / stamp()),
            repo_root=str(repo_root),
        )
        return save(self._mirror(job, devin_job))

    @staticmethod
    def _mirror(job: Job, devin_job) -> Job:
        job.external_id = devin_job.id
        job.state = devin_job.state
        job.brief_digest = devin_job.brief_digest
        job.session_url = devin_job.session_url
        job.pr_urls = list(devin_job.pr_urls)
        job.last_error = devin_job.last_error
        job.finished_at = devin_job.finished_at
        return job

    def status(self, job: Job) -> Job:
        if not job.external_id:
            return job
        try:
            devin_job = self.jobs().refresh(job.external_id)
        except (JobError, DevinError) as exc:
            job.last_error = str(exc)
            return job
        return save(self._mirror(job, devin_job)) if job.job_dir else self._mirror(job, devin_job)
