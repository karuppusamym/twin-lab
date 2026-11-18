"""The builder interface and the job record every builder writes.

A build job is one JSON file, `<state>/builds/<app>/<stamp>/job.json`, next to the session log. The
portal can list `<state>/builds/*/*/job.json` to show builds; the fields are the same whichever
builder ran. States follow the Devin jobs: preparing, running, waiting_for_user, finished, failed,
cancelled.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

TERMINAL = frozenset({"finished", "failed", "cancelled"})


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


class BuilderError(Exception):
    pass


@dataclass
class Job:
    id: str
    app_id: str
    builder: str  # manual | claude | devin
    state: str = "preparing"
    branch: str | None = None
    brief_digest: str | None = None
    package_path: str | None = None
    job_dir: str | None = None
    worktree: str | None = None
    log_path: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    changed_files: list[str] = field(default_factory=list)
    pr_urls: list[str] = field(default_factory=list)
    committed: bool = False
    open_pr: bool = False  # claude: commit, push and open the pull request when the session ends
    repo_root: str | None = None
    command: list[str] = field(default_factory=list)  # `{PROMPT}` stands for the task text
    external_id: str | None = None  # the Devin job id
    session_url: str | None = None
    note: str | None = None  # what a person should do next, or what went wrong
    last_error: str | None = None
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    finished_at: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_job(app_id: str, builder: str, **fields: Any) -> Job:
    return Job(id=uuid.uuid4().hex[:12], app_id=app_id, builder=builder, **fields)


def job_file(job_dir: Path) -> Path:
    return Path(job_dir) / "job.json"


def save(job: Job) -> Job:
    if not job.job_dir:
        raise BuilderError("job has no directory")
    job.updated_at = now()
    target = job_file(Path(job.job_dir))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.part")
    tmp.write_text(json.dumps(job.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return job


def load(path: Path) -> Job:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    known = {k: v for k, v in data.items() if k in Job.__dataclass_fields__}
    return Job(**known)


def jobs_for(state_dir: Path, app_id: str | None = None) -> list[Job]:
    """All build jobs under `<state>/builds`, newest first."""
    root = Path(state_dir) / "builds"
    out: list[Job] = []
    pattern = f"{app_id}/*/job.json" if app_id else "*/*/job.json"
    for path in root.glob(pattern):
        try:
            out.append(load(path))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda j: (j.created_at, j.id), reverse=True)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Builder(Protocol):
    name: str

    def available(self) -> bool:
        """Whether this builder can start here (executable found, key set, ...)."""
        ...

    def availability(self) -> str:
        """One line saying what was found, or what is missing."""
        ...

    def start(self, package: Path, repo_root: Path, app_id: str, branch: str) -> Job: ...

    def status(self, job: Job) -> Job: ...
