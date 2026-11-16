"""Build jobs: one per hand-off of a brief to Devin, kept in SQLite under the state directory.

Direct jobs are started through the API and followed by a background poller. Manual jobs record
that a person took the package to Devin by hand; the pull request is attached later. Both end up
with the same fields, so the portal shows them the same way.

State machine (raw Devin `status` / `status_detail` are kept alongside):

    preparing -> uploading -> starting -> running <-> waiting_for_user
                                             |
                                             +-> finished | failed | cancelled

- running: Devin `new`, `claimed`, `resuming`, or `running/working` (and anything unknown).
- waiting_for_user: `running/waiting_for_user`, `running/waiting_for_approval`, or `suspended`
  for a reason a person can resolve (inactivity, user_request). Not a failure; polling goes on.
- finished: `running/finished` or `exit`. `last_error` says so when no pull request was opened.
- failed: `error`; `suspended` for a usage, credit or quota reason; the session disappeared; or
  the hand-off failed before a session existed.
- cancelled: `cancel()` was called (the Devin session is terminated).

Manual jobs go running -> finished when `attach_pr` records the pull request.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labctl.devin import package as pkg
from labctl.devin.client import DevinClient, DevinError

log = logging.getLogger(__name__)

STATES = (
    "preparing",
    "uploading",
    "starting",
    "running",
    "waiting_for_user",
    "finished",
    "failed",
    "cancelled",
)
TERMINAL = frozenset({"finished", "failed", "cancelled"})
STARTING = frozenset({"preparing", "uploading", "starting"})
DEFAULT_POLL_SECONDS = 30.0
_PR_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+/?$")
_MONEY_WORDS = ("usage", "credit", "quota", "limit", "billing", "payment")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_status(status: str | None, detail: str | None) -> tuple[str, str | None]:
    """Twin Lab job state for a Devin status, and an explanation to record when there is one."""
    status = (status or "").lower()
    detail = (detail or "").lower()
    if status == "error":
        return "failed", "Devin reported an error; open the session for details"
    if status == "exit":
        return "finished", None
    if status == "running":
        if detail == "finished":
            return "finished", None
        if detail in ("waiting_for_user", "waiting_for_approval"):
            return "waiting_for_user", None
        return "running", None
    if status == "suspended":
        if any(word in detail for word in _MONEY_WORDS):
            return "failed", f"Devin suspended the session: {detail} (add credits or raise limits)"
        return "waiting_for_user", None
    return "running", None


@dataclass
class Job:
    id: str
    app_id: str
    mode: str  # "direct" or "manual"
    state: str
    repo: str | None = None
    package_path: str | None = None
    package_digest: str | None = None
    brief_digest: str | None = None
    max_acu_limit: int | None = None
    attachment_url: str | None = None
    devin_id: str | None = None
    session_url: str | None = None
    status: str | None = None
    status_detail: str | None = None
    pull_requests: list[dict] = field(default_factory=list)
    acus_consumed: float | None = None
    last_error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_polled_at: str | None = None
    finished_at: str | None = None

    @property
    def pr_urls(self) -> list[str]:
        return [p["pr_url"] for p in self.pull_requests if p.get("pr_url")]

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "pr_urls": self.pr_urls}


_COLUMNS = [f for f in Job.__dataclass_fields__]
_TERMINAL_SQL = ", ".join(f"'{s}'" for s in sorted(TERMINAL))


class JobStore:
    """SQLite store of jobs. Safe to share between threads; one connection per call."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "devin-jobs.sqlite3"
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                + ", ".join(f"{c} TEXT PRIMARY KEY" if c == "id" else f"{c} TEXT" for c in _COLUMNS)
                + ")"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _value(column: str, v: Any) -> Any:
        return json.dumps(v) if column == "pull_requests" else (None if v is None else str(v))

    @classmethod
    def _encode(cls, job: Job) -> list[Any]:
        return [cls._value(c, getattr(job, c)) for c in _COLUMNS]

    @staticmethod
    def _decode(row: tuple) -> Job:
        data = dict(zip(_COLUMNS, row, strict=True))
        data["pull_requests"] = json.loads(data["pull_requests"] or "[]")
        if data["max_acu_limit"] is not None:
            data["max_acu_limit"] = int(data["max_acu_limit"])
        if data["acus_consumed"] is not None:
            data["acus_consumed"] = float(data["acus_consumed"])
        return Job(**data)

    def save(self, job: Job) -> Job:
        job.updated_at = _now()
        with self._lock, self._connect() as db:
            db.execute(
                f"INSERT OR REPLACE INTO jobs ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                self._encode(job),
            )
        return job

    def insert_unless_unfinished(self, job: Job) -> Job | None:
        """Insert `job` unless an unfinished direct job of the same app exists, atomically
        (also across processes). Returns that unfinished job when there is one, else None."""
        job.updated_at = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM jobs WHERE app_id = ? AND mode = 'direct' "
                    f"AND state NOT IN ({_TERMINAL_SQL}) ORDER BY created_at DESC LIMIT 1",
                    (job.app_id,),
                ).fetchone()
                if row is None:
                    db.execute(
                        f"INSERT INTO jobs ({', '.join(_COLUMNS)}) "
                        f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                        self._encode(job),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return None if row is None else self._decode(row)

    def update(self, job_id: str, *, from_poller: bool = False, **fields: Any) -> Job:
        """Write only `fields` (one SQL UPDATE of those columns) and return the job as stored.

        `from_poller=True` marks a write based on a Devin session read, which may be stale: it
        never touches a cancelled job, and never moves a finished, failed or cancelled job back
        to a non-terminal state. The guard is in the SQL, so it holds across processes.
        """
        for key in fields:
            if key not in _COLUMNS or key == "id":
                raise AttributeError(key)
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        fields["updated_at"] = _now()
        where = "id = ?"
        if from_poller:
            where += " AND state != 'cancelled'"
            if "state" in fields and fields["state"] not in TERMINAL:
                where += f" AND state NOT IN ({_TERMINAL_SQL})"
        with self._lock, self._connect() as db:
            db.execute(
                f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE {where}",
                [*(self._value(k, v) for k, v in fields.items()), current.id],
            )
        job = self.get(current.id)
        if job is None:  # pragma: no cover - deleted in between
            raise KeyError(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        """A job by id, or by a unique prefix of its id."""
        with self._lock, self._connect() as db:
            rows = db.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM jobs WHERE id = ? OR id LIKE ? ESCAPE '\\'",
                (
                    job_id,
                    job_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
                ),
            ).fetchall()
        exact = [r for r in rows if r[0] == job_id]
        if exact:
            return self._decode(exact[0])
        return self._decode(rows[0]) if len(rows) == 1 else None

    def list(self, app_id: str | None = None) -> list[Job]:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM jobs"
        args: tuple = ()
        if app_id:
            sql, args = sql + " WHERE app_id = ?", (app_id,)
        with self._lock, self._connect() as db:
            rows = db.execute(sql + " ORDER BY created_at DESC, id DESC", args).fetchall()
        return [self._decode(r) for r in rows]


class JobError(Exception):
    pass


class BuildJobs:
    """Starts, follows and records builds. `client_factory()` returns a `DevinClient`; it is
    called when a client is first needed, so a missing key only matters for direct jobs."""

    def __init__(
        self,
        store: JobStore,
        client_factory: Callable[[], DevinClient],
        repo_root: Path,
        poll_interval: float = DEFAULT_POLL_SECONDS,
        packages_dir: Path | None = None,
        fingerprint: Callable[[], object] | None = None,
    ) -> None:
        self.store = store
        self.client_factory = client_factory
        # What identifies the client's settings (key, org, address). When it changes, for example
        # after a wrong key is corrected, the next call builds a new client instead of the old one.
        self.fingerprint = fingerprint
        self._made_with: object = None
        self._refused = False  # a poller stopped on 401/403; restart them with the next client
        self.repo_root = Path(repo_root)
        self.poll_interval = poll_interval
        self.packages_dir = Path(packages_dir) if packages_dir else store.state_dir / "packages"
        self._client: DevinClient | None = None
        self._lock = threading.RLock()
        self._pollers: dict[str, tuple[threading.Thread, threading.Event]] = {}

    # --- helpers ---------------------------------------------------------------------------------

    def _devin(self) -> DevinClient:
        restart = False
        with self._lock:
            now = self.fingerprint() if self.fingerprint else None
            if self._client is None or now != self._made_with:
                self._client = self.client_factory()
                self._made_with = now
                restart, self._refused = self._refused, False
            client = self._client
        if restart:  # pollers that stopped on a refused key follow their jobs again
            for job in self.store.list():
                if job.mode == "direct" and not job.terminal and job.devin_id:
                    self._ensure_poller(job.id)
        return client

    def _require(self, job_id: str) -> Job:
        job = self.store.get(job_id)
        if job is None:
            raise JobError(f"no job {job_id!r}")
        return job

    # --- direct path -----------------------------------------------------------------------------

    def start(
        self, app_id: str, repo: str, max_acu_limit: int | None = None, *, force: bool = False
    ) -> Job:
        """Package, upload, create the session, save the job and start polling it.

        Returns the job. A hand-off that fails is recorded as `failed` with `last_error`, not
        raised, so it shows in the job list; a bad argument (repo format) raises. Refuses with
        JobError while an unfinished direct build of the same app exists, unless `force`.
        """
        repo = pkg.normalize_repo(repo)
        job = Job(
            id=uuid.uuid4().hex[:12],
            app_id=app_id,
            mode="direct",
            state="preparing",
            repo=repo,
            max_acu_limit=max_acu_limit,
        )
        if force:
            job = self.store.save(job)
        else:
            running = self.store.insert_unless_unfinished(job)
            if running is not None:
                raise JobError(
                    f"a direct build of {app_id} is already in progress (job {running.id}, "
                    f"{running.state}, started {running.created_at}). Follow or cancel it first, "
                    "or start anyway with force (this creates a second, separately paid session)"
                )
        try:
            path = pkg.build(self.repo_root, app_id, self.packages_dir, repo=repo)
            manifest = pkg.read_manifest(path)
            job = self.store.update(
                job.id,
                from_poller=True,  # never revives a job cancelled meanwhile
                state="uploading",
                package_path=str(path),
                package_digest=manifest["package_digest"],
                brief_digest=manifest["brief_digest"],
            )
            client = self._devin()
            uploaded = client.upload(path)
            url = uploaded.get("url")
            if not url:
                raise DevinError(0, "Upload answered without a url", json.dumps(uploaded)[:300])
            job = self.store.update(job.id, from_poller=True, state="starting", attachment_url=url)
            text = pkg.prompt(self.repo_root, app_id, repo) + f'\nATTACHMENT:"{url}"\n'
            session = client.create_session(
                text,
                repos=[repo],
                attachment_urls=[url],
                title=f"Twin Lab: {app_id} demo app ({manifest['brief_digest']})",
                tags=["twinlab", f"app:{app_id}"],
                max_acu_limit=max_acu_limit,
            )
            sid = session.get("session_id")
            if not sid:
                raise DevinError(0, "Session created without a session_id", str(session)[:300])
            job = self.store.update(job.id, devin_id=str(sid))
            job = self._record(job.id, session)
        except (pkg.PackageError, DevinError, OSError, ValueError) as exc:
            return self.store.update(
                job.id,
                from_poller=True,
                state="failed",
                last_error=_describe(exc),
                finished_at=_now(),
            )
        if not job.terminal:
            self._ensure_poller(job.id)
        return job

    def _record(self, job_id: str, session: dict) -> Job:
        """Store what a session read says, and the job state it maps to."""
        with self._lock:
            job = self._require(job_id)
            if job.state == "cancelled":
                return job
            status, detail = session.get("status"), session.get("status_detail")
            state, note = map_status(status, detail)
            prs = session.get("pull_requests")
            fields: dict[str, Any] = {
                "status": status,
                "status_detail": detail,
                "state": state,
                "last_polled_at": _now(),
                "last_error": note,
            }
            if session.get("url"):
                fields["session_url"] = session["url"]
            if isinstance(prs, list):
                from_devin = [p for p in prs if isinstance(p, dict)]
                urls = {p.get("pr_url") for p in from_devin}
                # Keep pull requests attached by hand that Devin does not list.
                fields["pull_requests"] = from_devin + [
                    p for p in job.pull_requests if p.get("pr_url") not in urls
                ]
            if session.get("acus_consumed") is not None:
                fields["acus_consumed"] = float(session["acus_consumed"])
            if state == "finished" and not (fields.get("pull_requests") or job.pull_requests):
                fields["last_error"] = "Devin finished without opening a pull request"
            if state in TERMINAL and not job.finished_at:
                fields["finished_at"] = _now()
            if state not in TERMINAL:
                fields["finished_at"] = None
            return self.store.update(job_id, from_poller=True, **fields)

    def refresh(self, job_id: str) -> Job:
        """Read the session once and record it. Used by the poller and by `labctl devin status`."""
        job = self._require(job_id)
        if job.mode != "direct" or not job.devin_id or job.state == "cancelled":
            return job
        try:
            session = self._devin().get_session(job.devin_id)
        except DevinError as exc:
            if exc.status == 404:
                return self.store.update(
                    job_id,
                    from_poller=True,
                    state="failed",
                    last_error=f"Devin session {job.devin_id} no longer exists ({_describe(exc)})",
                    last_polled_at=_now(),
                    finished_at=_now(),
                )
            self.store.update(
                job_id, from_poller=True, last_error=_describe(exc), last_polled_at=_now()
            )
            raise
        return self._record(job_id, session)

    def _ensure_poller(self, job_id: str) -> None:
        with self._lock:
            current = self._pollers.get(job_id)
            if current and current[0].is_alive():
                return
            stop = threading.Event()
            thread = threading.Thread(
                target=self._poll, args=(job_id, stop), name=f"devin-poll-{job_id}", daemon=True
            )
            self._pollers[job_id] = (thread, stop)
            thread.start()

    def _poll(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.poll_interval):
            try:
                job = self.refresh(job_id)
            except DevinError as exc:
                if exc.status in (401, 403):
                    log.error("job %s: polling stopped, Devin refused the key: %s", job_id, exc)
                    with self._lock:
                        self._refused = True
                        self._client = None  # the next call reads the settings again
                    return  # resumes once a call succeeds with the corrected key
                log.warning("job %s: poll failed, will retry: %s", job_id, exc)
                continue
            except JobError:
                return
            except Exception:  # keep the thread alive on surprises, but say so
                log.exception("job %s: unexpected error while polling", job_id)
                continue
            if job.terminal:
                return

    def resume_unfinished(self) -> list[str]:
        """Start pollers for direct jobs left running by an earlier process. Returns their ids."""
        resumed = []
        for job in self.store.list():
            if job.mode != "direct" or job.terminal:
                continue
            if job.state in STARTING and not job.devin_id:
                self.store.update(
                    job.id,
                    state="failed",
                    last_error=(
                        f"interrupted while {job.state}, before a Devin session was recorded. "
                        "Check the Devin web app for a session titled 'Twin Lab: "
                        f"{job.app_id} ...' before starting again"
                    ),
                    finished_at=_now(),
                )
                continue
            self._ensure_poller(job.id)
            resumed.append(job.id)
        return resumed

    def list(self, app_id: str | None = None) -> list[Job]:
        return self.store.list(app_id)

    def get(self, job_id: str) -> Job | None:
        return self.store.get(job_id)

    def get_for_app(self, job_id: str, app_id: str) -> Job:
        """The job, which must belong to `app_id` (so a page for one app cannot act on another's)."""
        job = self._require(job_id)
        if job.app_id != app_id:
            raise JobError(f"job {job.id} belongs to {job.app_id}, not {app_id}")
        return job

    def message(self, job_id: str, text: str) -> Job:
        """Send a message to the job's Devin session (answers a question, resumes a suspended
        session) and follow it again."""
        job = self._require(job_id)
        if job.mode != "direct" or not job.devin_id:
            raise JobError(f"job {job.id} has no Devin session to message (mode {job.mode})")
        if job.state == "cancelled":
            raise JobError(f"job {job.id} was cancelled")
        if not text.strip():
            raise JobError("message is empty")
        session = self._devin().send_message(job.devin_id, text)
        with self._lock:
            job = self.store.update(job.id, state="running", finished_at=None, last_error=None)
            if session.get("status"):
                job = self._record(job.id, session)
                if job.terminal and job.state != "cancelled":
                    # Devin resumes after a message; the read may still show the old state.
                    job = self.store.update(job.id, state="running", finished_at=None)
        self._ensure_poller(job.id)
        return job

    def cancel(self, job_id: str) -> Job:
        """Stop following the job and terminate its Devin session."""
        job = self._require(job_id)
        if job.terminal:
            raise JobError(f"job {job.id} is already {job.state}")
        with self._lock:
            poller = self._pollers.pop(job.id, None)
        if poller:
            poller[1].set()
        error = None
        if job.mode == "direct" and job.devin_id:
            try:
                self._devin().terminate(job.devin_id)
            except DevinError as exc:
                if exc.status != 404:
                    error = f"terminate failed: {_describe(exc)}"
        with self._lock:  # _record checks for cancelled under the same lock
            job = self.store.update(
                job.id, state="cancelled", finished_at=_now(), last_error=error or job.last_error
            )
        if error:
            raise JobError(f"job {job.id} marked cancelled, but {error}; stop it in the Devin app")
        return job

    def stop(self, timeout: float = 5.0) -> None:
        """Stop all pollers in this process (jobs stay as they are and resume on next start)."""
        with self._lock:
            pollers = list(self._pollers.values())
            self._pollers.clear()
        for _, event in pollers:
            event.set()
        for thread, _ in pollers:
            thread.join(timeout)
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    # --- manual path -----------------------------------------------------------------------------

    def record_manual(self, app_id: str, package_path: Path, repo: str | None = None) -> Job:
        """Record that a person took this package to Devin by hand."""
        path = Path(package_path)
        manifest: dict = {}
        if path.is_file():
            try:
                manifest = pkg.read_manifest(path)
            except (OSError, KeyError, ValueError) as exc:
                raise JobError(f"{path} is not a Twin Lab package: {exc}") from exc
        return self.store.save(
            Job(
                id=uuid.uuid4().hex[:12],
                app_id=app_id,
                mode="manual",
                state="running",
                repo=pkg.normalize_repo(repo) if repo else manifest.get("repo"),
                package_path=str(path),
                package_digest=manifest.get("package_digest"),
                brief_digest=manifest.get("brief_digest"),
                status="manual",
                status_detail="handed_to_person",
            )
        )

    def attach_pr(self, job_id: str, pr_url: str, session_url: str | None = None) -> Job:
        """Record the pull request a build produced (manual jobs, or a correction)."""
        pr_url = pr_url.strip()
        if not _PR_RE.match(pr_url):
            raise JobError(
                f"not a GitHub pull request URL: {pr_url!r} "
                "(expected https://github.com/owner/name/pull/123)"
            )
        with self._lock:
            job = self._require(job_id)
            prs = [p for p in job.pull_requests if p.get("pr_url") != pr_url]
            prs.append({"pr_url": pr_url, "pr_state": "open"})
            fields: dict[str, Any] = {"pull_requests": prs}
            if session_url:
                fields["session_url"] = session_url.strip()
            if job.mode == "manual":
                fields.update(
                    state="finished",
                    status_detail="pr_recorded",
                    finished_at=_now(),
                    last_error=None,
                )
            return self.store.update(job.id, **fields)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, DevinError):
        return str(exc) + (f" [{hint}]" if (hint := http_hint(exc.status)) else "")
    return str(exc) or exc.__class__.__name__


def http_hint(status: int) -> str | None:
    """What a Devin API status usually means for this office's setup."""
    return {
        0: "network: check DEVIN_BASE_URL, proxy and firewall",
        400: "request rejected: report it with the detail",
        401: "key rejected: check DEVIN_API_KEY is a current service-user key (cog_...)",
        403: "not allowed: the service user lacks permission, or DEVIN_ORG_ID is wrong",
        404: "not found: check DEVIN_ORG_ID, or the session was deleted",
        409: "conflict: the session is in a state that does not allow this",
        422: "invalid request: see the detail",
        429: "rate limited: wait and retry",
    }.get(status) or ("Devin server error: retry later" if status >= 500 else None)
