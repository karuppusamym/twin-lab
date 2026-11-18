"""The Claude Code builder: a headless session in a git worktree on a new branch.

    claude [extra args] -p "<task>" --permission-mode acceptEdits [--model M]

The worktree is `<state>/builds/<app>/<stamp>/worktree`, branch `build/<app>-<digest8>`, cut from
the checkout's HEAD. The package is not unpacked: the repository already has the files, and only
brief inputs that differ from HEAD (a brief regenerated but not yet committed) are copied in. The
session never commits, pushes or opens a pull request: that happens only with `open_pr`, done here
after the session ends. Output goes to `session.log` next to `job.json`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

from labctl.builder import package as pkg
from labctl.builder.base import BuilderError, Job, load, new_job, now, pid_alive, save, stamp
from labctl.builder.config import BuilderConfig

RUN_MODE = """

## Run mode (added by Twin Lab for this headless session)

You are running unattended in a git worktree that is already checked out on branch `{branch}`.
Skip the clone and branch steps. Do NOT run `git commit`, `git push` or open a pull request:
Twin Lab does that afterwards, only if the person asked for it. Leave your work in the working
tree. You cannot ask questions: where the task says to stop and ask, write the question under
"Open questions" in `apps/{app}/BUILD-NOTES.md` and stop. The brief's files are already in place
at their repository paths. Never print or write secrets.
"""

# What may be copied from a package into the worktree.
_SYNC_PREFIXES = ("apps/", "docs/", "evidence/")


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if check and done.returncode:
        detail = (done.stderr or done.stdout).strip()
        raise BuilderError(f"git {' '.join(args[:3])} failed: {detail}")
    return done


class ClaudeBuilder:
    name = "claude"

    def __init__(
        self,
        config: BuilderConfig | None = None,
        state_dir: Path | None = None,
        *,
        open_pr: bool = False,
        detach: bool = False,
    ) -> None:
        self.config = config or BuilderConfig(name="claude")
        self.state_dir = Path(state_dir) if state_dir else None
        self.open_pr = open_pr
        self.detach = detach

    # --- availability ----------------------------------------------------------------------------

    def _resolved(self) -> str | None:
        return shutil.which(self.config.claude_executable)

    def available(self) -> bool:
        return self._resolved() is not None and shutil.which("git") is not None

    def availability(self) -> str:
        found = self._resolved()
        if not found:
            return (
                f"`{self.config.claude_executable}` not found on PATH: install Claude Code or set "
                "builder.claude.executable in twinlab.local.yaml"
            )
        return f"claude found at {found}"

    # --- start -----------------------------------------------------------------------------------

    def command(self) -> list[str]:
        exe = self._resolved() or self.config.claude_executable
        cmd = [exe, *self.config.claude_args, "-p", "{PROMPT}", "--permission-mode", "acceptEdits"]
        if self.config.claude_model:
            cmd += ["--model", self.config.claude_model]
        return cmd

    def start(self, package: Path, repo_root: Path, app_id: str, branch: str) -> Job:
        repo_root = Path(repo_root)
        state = self.state_dir or pkg.default_state_dir(repo_root)
        if _git(repo_root, "rev-parse", "--git-dir", check=False).returncode:
            raise BuilderError(f"{repo_root} is not a git repository: the claude builder needs one")
        manifest = pkg.read_manifest(Path(package))
        with zipfile.ZipFile(package) as zf:
            task = zf.read(pkg.TASK_FILE).decode("utf-8")
            entries = {
                n: zf.read(n)
                for n in zf.namelist()
                if n not in (pkg.TASK_FILE, pkg.MANIFEST_FILE) and self._safe(n)
            }
        job_dir = state / "builds" / app_id / stamp()
        n = 1
        while job_dir.exists():  # two builds in the same second
            n += 1
            job_dir = state / "builds" / app_id / f"{stamp()}-{n}"
        job_dir.mkdir(parents=True)
        worktree = job_dir / "worktree"
        real_branch = self._add_worktree(repo_root, worktree, branch)
        self._sync_inputs(worktree, entries)
        text = task + RUN_MODE.format(branch=real_branch, app=app_id)
        (job_dir / "prompt.md").write_text(text, encoding="utf-8")
        job = new_job(
            app_id,
            "claude",
            state="running",
            branch=real_branch,
            brief_digest=manifest.get("brief_digest"),
            package_path=str(package),
            job_dir=str(job_dir),
            worktree=str(worktree),
            log_path=str(job_dir / "session.log"),
            open_pr=self.open_pr,
            repo_root=str(repo_root),
            command=self.command(),
        )
        save(job)
        if self.detach:
            proc = subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "labctl.builder.claude", str(job_dir / "job.json")],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(worktree),
                start_new_session=True,
            )
            job.pid = proc.pid
            return save(job)
        return run_job(job_dir / "job.json")

    @staticmethod
    def _safe(name: str) -> bool:
        """A package path that stays inside the repository, is not excluded, and is allowed."""
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not name.startswith(_SYNC_PREFIXES):
            return False
        if name.startswith("evidence/"):
            return not name.lower().endswith(".har") and not pkg.excluded(name)
        return not pkg.excluded(name)

    @staticmethod
    def _add_worktree(repo_root: Path, worktree: Path, branch: str) -> str:
        for i in range(1, 30):
            name = branch if i == 1 else f"{branch}-{i}"
            ref = f"refs/heads/{name}"
            if (
                _git(repo_root, "rev-parse", "--verify", "--quiet", ref, check=False).returncode
                == 0
            ):
                continue
            _git(repo_root, "worktree", "add", "-b", name, str(worktree), "HEAD")
            return name
        raise BuilderError(f"too many existing branches named {branch}-N")

    @staticmethod
    def _sync_inputs(worktree: Path, entries: dict[str, bytes]) -> None:
        """Copy package files that differ from the worktree (a brief not yet committed)."""
        for rel, data in entries.items():
            target = worktree / rel
            if target.is_file() and target.read_bytes().replace(b"\r\n", b"\n") == data:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    # --- status ----------------------------------------------------------------------------------

    def status(self, job: Job) -> Job:
        path = Path(job.job_dir or "") / "job.json"
        if path.is_file():
            job = load(path)
        if job.state == "running" and job.pid and not pid_alive(job.pid):
            job.state, job.finished_at = "failed", now()
            job.last_error = "the build process ended without recording a result; see the log"
            save(job)
        return job


def changed_files(worktree: Path) -> list[str]:
    out = _git(worktree, "status", "--porcelain", "-uall", check=False).stdout
    files = []
    for line in out.splitlines():
        entry = line[3:].strip().strip('"')
        files.append(entry.split(" -> ")[-1])
    return sorted(files)


def run_job(job_file: Path) -> Job:
    """Run the session for a saved job, then record the outcome. Used inline and by `detach`."""
    job = load(job_file)
    job_dir = Path(job.job_dir or "")
    worktree = Path(job.worktree or "")
    task = (job_dir / "prompt.md").read_text(encoding="utf-8")
    cmd = [task if a == "{PROMPT}" else a for a in job.command]
    try:
        with open(job.log_path or job_dir / "session.log", "ab") as log:
            header = f"# twinlab build {job.id} started {now()}: {job.command[0]} -p <task>\n"
            log.write(header.encode())
            log.flush()
            done = subprocess.run(  # noqa: S603
                cmd,
                cwd=str(worktree),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        job.exit_code = done.returncode
    except OSError as exc:
        job.exit_code, job.last_error = None, f"could not start {job.command[0]}: {exc}"
    job.changed_files = changed_files(worktree)
    if job.exit_code == 0:
        job.state = "finished"
        pkg.record_baseline(Path(job.repo_root or "."), job.app_id, job_dir.parents[2])
        job.note = (
            f"{len(job.changed_files)} file(s) changed on branch {job.branch} in {worktree}. "
            "Nothing was committed."
            if job.changed_files
            else "The session finished but changed nothing; read the log."
        )
        if job.open_pr and job.changed_files:
            _publish(job)
    else:
        job.state = "failed"
        job.last_error = job.last_error or (
            f"claude exited with code {job.exit_code}{_last_log_line(job)}; see the log"
        )
    job.finished_at = now()
    return save(job)


def _last_log_line(job: Job) -> str:
    """What the session last said (such as a sign-in or credit message), for the error line."""
    try:
        lines = Path(job.log_path or "").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    said = [x.strip() for x in lines if x.strip() and not x.startswith("# twinlab build")]
    return f": {said[-1][:200]}" if said else ""


def _publish(job: Job) -> None:
    """Commit on the build branch, push and open the pull request. Only with --pr."""
    worktree = Path(job.worktree or "")
    try:
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-m", f"{job.app_id}: build from brief {job.brief_digest}")
        job.committed = True
        _git(worktree, "push", "-u", "origin", job.branch or "HEAD")
        body = (
            f"Built by a headless Claude Code session from brief {job.brief_digest}.\n\n"
            f"Files changed: {len(job.changed_files)}. See apps/{job.app_id}/BUILD-NOTES.md for "
            "the checks and assumptions."
        )
        args = [
            "pr", "create", "--head", job.branch or "",
            "--title", f"{job.app_id}: build from brief {job.brief_digest}",
            "--body", body,
        ]  # fmt: skip
        done = _gh(worktree, [*args, "--label", "ai-built"])
        if done.returncode:  # the label may not exist yet
            done = _gh(worktree, args)
            if not done.returncode:
                job.note = (job.note or "") + " The PR has no ai-built label: add it by hand."
        if done.returncode:
            raise BuilderError((done.stderr or done.stdout).strip()[:300])
        url = next((ln.strip() for ln in done.stdout.splitlines() if "/pull/" in ln), "")
        if url:
            job.pr_urls.append(url)
    except (BuilderError, OSError, subprocess.SubprocessError) as exc:
        job.last_error = f"pull request not opened: {exc}"


def _gh(cwd: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args], cwd=str(cwd), capture_output=True, text=True, check=False, timeout=120
    )


if __name__ == "__main__":  # pragma: no cover - the detached runner
    run_job(Path(sys.argv[1]))
