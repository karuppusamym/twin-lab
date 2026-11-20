"""Run a workstation session: the web and desktop recorders in one process, one folder.

    Session(root, Targets.from_list([...]), name="order-to-invoice").run()

`run()` blocks until `stop()`, a `stop` file in the session folder, `max_seconds`, or (with
`script`) the script returning. While it runs:

- a **watcher** (its own thread, every 250 ms) reads which window is in front, identifies its
  process (labctl.workstation.levels) and gives it its level: a target's process is added to the
  desktop recorder (`add_process`), every other application gets a presence row in the journal
  (executable name and time, kept on this machine only); every 2 s it also looks for target
  processes that have a window but have not been in front yet;
- the **desktop recorder** runs in the calling thread, in session mode (it starts with no process
  and stops only when the session stops); it is not started when no target is a desktop one, so
  no input hook is installed then;
- the **web recorder** runs in its own thread when a target is a web origin: the session starts
  the Edge installed on the machine (Chrome when there is no Edge) with a profile of its own and
  attaches to it (labctl.recorder.attach with the target origins); pages of other origins are
  presence rows. The profile is deleted when the session ends.

At the end `session.json` says `stopped` and labctl.workstation.timeline writes `session.jsonl`
and `portal-outbox.json`. A folder left `recording` (the program was killed) is finalised by
`recover()`; its web HAR is lost then (the web recorder writes it at the end), and it says so.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from labctl.workstation import levels as lv
from labctl.workstation import timeline

POLL_S = 0.25
SCAN_S = 2.0
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]  # fmt: skip
CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]  # fmt: skip


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def find_browser(explicit: str | None = None) -> str | None:
    for path in ([explicit] if explicit else []) + EDGE + CHROME:
        if path and Path(path).is_file():
            return path
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for rel in (
            r"Microsoft\Edge\Application\msedge.exe",
            r"Google\Chrome\Application\chrome.exe",
        ):
            if (Path(local) / rel).is_file():
                return str(Path(local) / rel)
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def twinlab_commit() -> str | None:
    """The Twin Lab commit these collectors come from: baked into a frozen build
    (`TWINLAB_COMMIT`), else read from the checkout."""
    if os.environ.get("TWINLAB_COMMIT"):
        return os.environ["TWINLAB_COMMIT"]
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)  # fmt: skip
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class Session:
    def __init__(self, root: Path, targets: lv.Targets, *, name: str = "",
                 session_id: str | None = None, automation_id: int | None = None,
                 portal_session_id: int | None = None, browser: str | None = None,
                 max_seconds: float = 4 * 3600, stop: Callable[[], bool] | None = None,
                 pictures: bool = True, recorder: str = "labctl.workstation") -> None:  # fmt: skip
        self.id = session_id or new_id()
        self.folder = Path(root) / self.id
        self.targets = targets
        self.name = name or self.id
        self.automation_id = automation_id
        self.portal_session_id = portal_session_id
        self.browser = browser
        self.max_seconds = max_seconds
        self.stop_fn = stop
        self.pictures = pictures
        self.recorder_name = recorder
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.notes: list[str] = []
        self.apps: dict[str, dict] = {}
        self.desktop = None  # the desktop Recorder, once it runs
        self._pending: list[tuple[int, str]] = []  # processes found before the recorder ran
        self.edge_pid: int | None = None
        self.browser_pids: set[int] = set()  # every process of the session's browser
        self.cdp: str | None = None  # the session browser's DevTools address, once it is up
        self._journal = None
        self.started = None

    # -- journal ---------------------------------------------------------------------------------

    def journal(self, row: dict) -> None:
        with self._lock:
            if self._journal is None:
                return
            self._journal.write(json.dumps(dict(row, ts=row.get("ts") or _now()),
                                           ensure_ascii=False) + "\n")  # fmt: skip
            self._journal.flush()

    def note(self, text: str) -> None:
        self.notes.append(text)
        self.journal({"kind": "note", "text": text})

    def _meta(self, state: str) -> dict:
        return {"format": timeline.FORMAT, "id": self.id, "name": self.name, "state": state,
                "started": self.started, "ended": _now() if state != "recording" else None,
                "recorder": self.recorder_name, "twinlab_commit": twinlab_commit(),
                "automation_id": self.automation_id,
                "portal_session_id": self.portal_session_id,
                "targets": self.targets.as_list(), "deny_keys": sorted(self.targets.deny_keys),
                "apps": self.apps, "notes": self.notes}  # fmt: skip

    # -- stopping ------------------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def stopping(self) -> bool:
        if self._stop.is_set():
            return True
        if (self.folder / "stop").exists() or (self.stop_fn is not None and self.stop_fn()):
            self._stop.set()
        return self._stop.is_set()

    # -- applications --------------------------------------------------------------------------

    def _identify(self, pid: int, cache: dict) -> tuple[dict, str]:
        if pid in cache and not (
            cache[pid][0].get("browser")
            and self._session_browser(pid)
            and not cache[pid][0].get("session_browser")
        ):
            return cache[pid]
        from labctl.desktop import win

        path = win.process_path(pid)
        ident = lv.desktop_identity(path, win.exe_info(path) if path else {})
        ours = self._session_browser(pid)
        level = lv.PRESENCE if ours else self.targets.level(ident, path)
        if ours:
            ident = dict(ident, browser=True, session_browser=True)
        cache[pid] = (ident, level)
        return ident, level

    def _session_browser(self, pid: int) -> bool:
        return pid == self.edge_pid or pid in self.browser_pids

    def _record_process(self, pid: int, ident: dict, level: str) -> None:
        target = self.targets.target_of(ident)
        facts = {k: ident.get(k) for k in ("key", "surface", "exe", "company", "product",
                                           "version", "display")}  # fmt: skip
        facts["level"] = level
        if target is not None and target.app_id is not None:
            facts["app_id"] = target.app_id
        if target is not None and target.name:
            facts["display"] = target.name
        self.apps.setdefault(ident["key"], facts)
        self.journal({"kind": "app", "pid": pid, **facts})
        if self.desktop is not None:
            self.desktop.add_process(pid, level)
        else:
            self._pending.append((pid, level))

    def _watch(self) -> None:
        from labctl.desktop import win

        own = os.getpid()
        cache: dict[int, tuple[dict, str]] = {}
        added: set[int] = set()
        last_pid, next_scan = None, 0.0
        while not self.stopping():
            try:
                now = time.monotonic()
                if now >= next_scan:
                    next_scan = now + SCAN_S
                    for pid in _window_pids(win):
                        if pid in added or pid == own:
                            continue
                        ident, level = self._identify(pid, cache)
                        if level in (lv.FULL, lv.STEPS):
                            added.add(pid)
                            self._record_process(pid, ident, level)
                pid = win.window_pid(win.foreground()) if win.foreground() else 0
                if pid and pid != own and pid != last_pid:
                    ident, level = self._identify(pid, cache)
                    if level in (lv.FULL, lv.STEPS):
                        if pid not in added:
                            added.add(pid)
                            self._record_process(pid, ident, level)
                    elif not self._session_browser(pid):
                        # Only that some other application was used; never its title.
                        self.journal({"kind": "presence", "exe": ident.get("exe")})
                    last_pid = pid
            except Exception as exc:  # noqa: BLE001 - the watcher must outlive one bad read
                self.notes.append(f"watcher: {type(exc).__name__}")
            time.sleep(POLL_S)

    def _outside(self, origin: str, ts: str) -> None:
        self.journal({"kind": "outside", "ts": ts, "denied": lv.denied_origin(origin)})

    # -- web ---------------------------------------------------------------------------------------

    def _web(self) -> None:
        from labctl import recorder

        exe = find_browser(self.browser)
        if exe is None:
            self.note("no Edge or Chrome found: the web targets were not recorded")
            return
        start = self.targets.web_start() or "about:blank"
        port = _free_port()
        profile = self.folder / "web-profile"
        proc = subprocess.Popen([exe, f"--remote-debugging-port={port}",
                                 f"--user-data-dir={profile}", "--no-first-run",
                                 "--no-default-browser-check", "--disable-sync",
                                 "--disable-features=msEdgeStartupBoost,Translate",
                                 "--edge-skip-compat-layer-relaunch",
                                 "--new-window", start])  # fmt: skip
        self.edge_pid = proc.pid
        cdp = f"http://127.0.0.1:{port}"
        try:
            if not _wait_cdp(cdp, 30, self.stopping):
                self.note("the browser did not open its DevTools port: web targets not recorded")
                return
            # A browser can hand itself over to another process when it starts: its windows
            # belong to whatever process runs with the session's profile.
            self.browser_pids = set(_profile_pids(profile)) | {proc.pid}
            self.cdp = cdp
            # For a robot or a test that drives the session's browser; stays on this machine.
            self.journal({"kind": "browser", "cdp": cdp})
            recorder.attach(cdp, self.folder / "web", max_seconds=self.max_seconds,
                            stop=self.stopping, origins=self.targets.origins(),
                            outside=self._outside)  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - the desktop part goes on
            self.note(f"web recorder stopped: {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            _close_browser(cdp, proc, profile)
            if not remove_profile(profile):
                self.note("the browser profile folder could not be deleted: delete web-profile")

    # -- run ---------------------------------------------------------------------------------------

    def run(self, script: Callable[[Session], None] | None = None) -> Path:
        self.folder.mkdir(parents=True, exist_ok=False)
        self.started = _now()
        self._journal = (self.folder / "journal.jsonl").open("a", encoding="utf-8")
        _write_json(self.folder / "session.json", self._meta("recording"))
        threads = []
        desktop_targets = [t for t in self.targets.targets if not t.origin]
        started_procs = []
        try:
            if sys.platform == "win32":
                # COM for this thread (the desktop recorder runs here) before any other thread
                # imports comtypes, which would initialise it there instead.
                import comtypes

                try:
                    comtypes.CoInitialize()
                except OSError:  # COM is already set up in this thread, in the other mode
                    pass
                from labctl.desktop import win  # noqa: F401 - loads UI Automation here
            for t in desktop_targets:
                if t.start:
                    started_procs.append(subprocess.Popen([t.start]))
            if sys.platform == "win32":
                threads.append(threading.Thread(target=self._watch, name="tl-watch", daemon=True))
            if self.targets.origins():
                threads.append(threading.Thread(target=self._web, name="tl-web", daemon=True))
            for th in threads:
                th.start()
            failure: list[BaseException] = []
            if script is not None:
                threading.Thread(target=self._script, args=(script, failure), daemon=True,
                                 name="tl-script").start()  # fmt: skip
            if desktop_targets and sys.platform == "win32":
                self._run_desktop()
            else:
                end = time.monotonic() + self.max_seconds
                while not self.stopping() and time.monotonic() < end:
                    time.sleep(0.2)
            self.stop()
            for th in threads:
                th.join(120)
            if failure:
                self.note(f"the script failed: {type(failure[0]).__name__}: {failure[0]}"[:300])
        finally:
            self.stop()
            for p in started_procs:
                _end_process_tree(p)
            _write_json(self.folder / "session.json", self._meta("stopped"))
            with self._lock:
                self._journal.close()
                self._journal = None
            timeline.build(self.folder)
        return self.folder

    def _run_desktop(self) -> None:
        from labctl.desktop.recorder import Recorder

        rec = Recorder(self.folder / "desktop", set(), session=True, stop=self.stopping,
                       max_seconds=self.max_seconds, pictures=self.pictures)  # fmt: skip
        for pid, level in self._pending:
            rec.add_process(pid, level)
        self._pending = []
        self.desktop = rec
        rec.run()

    def _script(self, script, failure: list) -> None:
        try:
            import comtypes

            comtypes.CoInitializeEx(0)
        except Exception:  # noqa: BLE001 - not Windows, or COM already set up
            pass
        try:
            time.sleep(1.0)  # the recorders have their listeners in place
            script(self)
            time.sleep(1.5)  # the last step settles
        except BaseException as exc:  # noqa: BLE001 - reported in the session's notes
            failure.append(exc)
        finally:
            self.stop()


def remove_profile(profile: Path, seconds: float = 60) -> bool:
    """Delete the session browser's profile (its cookies and cache). Windows keeps a deleted
    file pending while a scanner or the font cache still has it open, extensions install
    read-only files, and a profile's paths can be longer than MAX_PATH (the browser handles them,
    Python's plain paths do not): so the deletion uses the long-path form, clears read-only bits
    and is tried again for a while."""
    import stat

    def writable(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass

    target = str(Path(profile).resolve())
    if sys.platform == "win32" and not target.startswith("\\\\?\\"):
        target = "\\\\?\\" + target
    end = time.monotonic() + seconds
    while profile.exists():
        shutil.rmtree(target, onexc=writable)
        if not profile.exists() or time.monotonic() > end:
            break
        time.sleep(1.0)
    return not profile.exists()


def _window_pids(win) -> set[int]:
    pids: set[int] = set()

    def cb(hwnd, _):
        if win.user32.IsWindowVisible(hwnd):
            pids.add(win.window_pid(hwnd))
        return True

    win.user32.EnumWindows(win.WNDENUMPROC(cb), 0)
    return pids


def _wait_cdp(cdp: str, timeout: float, stopping: Callable[[], bool]) -> bool:
    import urllib.request

    end = time.monotonic() + timeout
    while time.monotonic() < end and not stopping():
        try:
            with urllib.request.urlopen(cdp + "/json/version", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def _close_browser(cdp: str, proc: subprocess.Popen, profile: Path) -> None:
    """Close the session's browser so that it lets go of its profile: its tabs through the
    DevTools port first (the browser exits when the last one closes), then its process tree,
    then any process still started with this profile (a browser hands work to helpers that are
    not its children)."""
    import urllib.request

    try:
        with urllib.request.urlopen(cdp + "/json/list", timeout=3) as r:
            targets = json.loads(r.read().decode("utf-8"))
        for t in targets:
            if t.get("type") == "page" and t.get("id"):
                try:
                    req = urllib.request.Request(f"{cdp}/json/close/{t['id']}", method="PUT")
                    urllib.request.urlopen(req, timeout=3).close()
                except OSError:
                    pass
        proc.wait(10)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    _end_process_tree(proc)
    for _ in range(3):
        pids = _profile_pids(profile)
        if not pids:
            return
        for pid in pids:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        time.sleep(1.0)


def _profile_pids(profile: Path) -> list[int]:
    """Processes whose command line names this browser profile folder (Windows)."""
    if sys.platform != "win32":
        return []
    needle = str(profile).replace("'", "''")
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" "
        f"| Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{needle}') }} "
        "| ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=60)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.stdout.split() if x.strip().isdigit()]


def _end_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def recover(folder: Path) -> bool:
    """Finalise a session folder left `recording`. True when it was finalised now."""
    folder = Path(folder)
    meta_path = folder / "session.json"
    if not meta_path.is_file():
        return False
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("state") != "recording":
        return False
    rows = timeline.read_jsonl(folder / "journal.jsonl")
    meta["state"] = "recovered"
    meta["ended"] = rows[-1].get("ts") if rows else meta.get("started")
    notes = list(meta.get("notes") or [])
    notes.append("recovered after the recorder stopped unexpectedly: what was written until "
                 "then is kept; a web recording has no HAR")  # fmt: skip
    meta["notes"] = notes
    _write_json(meta_path, meta)
    remove_profile(folder / "web-profile", seconds=10)
    timeline.build(folder)
    return True


def recover_all(root: Path) -> list[str]:
    """Finalise every unfinished session under `root`, and delete browser profiles an earlier
    session could not delete."""
    root = Path(root)
    if not root.is_dir():
        return []
    done = [p.name for p in sorted(root.iterdir()) if p.is_dir() and recover(p)]
    for left in root.glob("*/web-profile"):
        remove_profile(left, seconds=5)
    return done
