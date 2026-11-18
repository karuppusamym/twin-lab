"""The manual builder: write the zip and say what a person does with any coding agent."""

from __future__ import annotations

from pathlib import Path

from labctl.builder import package as pkg
from labctl.builder.base import Job, new_job, save, stamp


class ManualBuilder:
    name = "manual"

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = Path(state_dir) if state_dir else None

    def available(self) -> bool:
        return True

    def availability(self) -> str:
        return "always available: you copy the task into an agent yourself"

    def _state(self, repo_root: Path) -> Path:
        return self.state_dir or pkg.default_state_dir(repo_root)

    @staticmethod
    def instructions(package: Path, branch: str, app_id: str = "<app>") -> str:
        return (
            f"Package: {package}\n\n"
            "What to do, with any coding agent (Claude Code, GitHub Copilot's coding agent, "
            "Claude on the web, Devin, ...):\n"
            "  1. Open the agent on this repository (or give it the repository's GitHub name).\n"
            f"  2. Attach {Path(package).name}, or unzip it into the repository root if the agent "
            "works on a local checkout. The brief files are repository files: do not edit them.\n"
            f"  3. Paste the whole of {pkg.TASK_FILE} (inside the zip) as the task, unchanged.\n"
            f"  4. The agent should work on branch {branch} and open one pull request labelled "
            "ai-built. Answer it if it asks a question.\n"
            f"  5. Review the pull request. Record it with: labctl build attach-pr {app_id} <url>"
        )

    def start(self, package: Path, repo_root: Path, app_id: str, branch: str) -> Job:
        repo_root = Path(repo_root)
        manifest = pkg.read_manifest(Path(package))
        job_dir = self._state(repo_root) / "builds" / app_id / stamp()
        job = new_job(
            app_id,
            "manual",
            state="running",
            branch=branch,
            brief_digest=manifest.get("brief_digest"),
            package_path=str(package),
            job_dir=str(job_dir),
            repo_root=str(repo_root),
            note=self.instructions(Path(package), branch, app_id),
        )
        pkg.record_baseline(repo_root, app_id, self._state(repo_root))
        return save(job)

    def status(self, job: Job) -> Job:
        return job  # a person is working: only attach-pr moves it on
