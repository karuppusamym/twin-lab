"""Short-lived, bounded reads of each app's lab for the portal's pages.

A page that shows several apps must not wait on every lab in turn, and one slow or dead lab must
not stall it. Reads of one app are kept for a few seconds, run concurrently in a bounded pool, and
give up after a short time; the portal's own actions that change a lab throw the kept read away.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

TTL_S = 5.0  # how long a read is kept
WAIT_S = 4.0  # the longest a page waits for any one lab
AGENT_TIMEOUT_S = 3.0  # the agent's own HTTP timeout, shorter than WAIT_S
WORKERS = 8


@dataclass
class Reading:
    """What one lab's agent said: `info` and `sessions`, or why it could not be read."""

    ok: bool
    info: dict
    sessions: list
    error: str | None = None


class LabReads:
    def __init__(
        self,
        ttl: float = TTL_S,
        wait: float = WAIT_S,
        workers: int = WORKERS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl, self.wait, self.clock = ttl, wait, clock
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="twinlab-read")
        self.lock = threading.Lock()
        self.kept: dict[str, tuple[float, Reading]] = {}
        self.running: dict[str, tuple[Future, float]] = {}
        self.dirty: set[str] = set()  # invalidated while a read of it was running
        self.facts_kept: dict[str, tuple[float, Any]] = {}
        self.images: dict[str, str] = {}  # last image id seen by an action of this portal
        self.reads = 0  # how many reads actually went to a lab (for tests and logs)

    # -- lab agent reads ----------------------------------------------------------------------

    def get_many(self, app_ids: list[str], load: Callable[[str], Reading]) -> dict[str, Reading]:
        """Reading for each app: kept ones at once, the rest concurrently. A read that fails or
        takes longer than `wait` comes back `ok=False` and is kept for the lifetime too, so a
        dead lab is not tried again on every request."""
        out: dict[str, Reading] = {}
        pending: dict[str, Future] = {}
        for app_id in app_ids:
            with self.lock:
                hit = self.kept.get(app_id)
                if hit and self.clock() - hit[0] < self.ttl:
                    out[app_id] = hit[1]
                    continue
                started = self.running.get(app_id)
                if started and time.monotonic() - started[1] > self.wait:
                    # still stuck from an earlier request: do not make this one wait again
                    out[app_id] = Reading(False, {}, [], "the lab did not answer in time")
                    continue
                if started is None:
                    future = self.pool.submit(self._read, app_id, load)
                    started = self.running[app_id] = (future, time.monotonic())
                pending[app_id] = started[0]
        deadline = time.monotonic() + self.wait
        for app_id, future in pending.items():
            try:
                reading = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                reading = Reading(False, {}, [], "the lab did not answer in time")
                with self.lock:
                    self.kept[app_id] = (self.clock(), reading)
            out[app_id] = reading
        return out

    def get(self, app_id: str, load: Callable[[str], Reading]) -> Reading:
        return self.get_many([app_id], load)[app_id]

    def _read(self, app_id: str, load: Callable[[str], Reading]) -> Reading:
        try:
            reading = load(app_id)
        except Exception as exc:  # noqa: BLE001 - any failure is "not reachable"
            reading = Reading(False, {}, [], str(exc) or exc.__class__.__name__)
        with self.lock:
            self.reads += 1
            self.running.pop(app_id, None)
            if app_id in self.dirty:  # a change was made while this was being read: do not keep it
                self.dirty.discard(app_id)
            else:
                self.kept[app_id] = (self.clock(), reading)
        return reading

    def run_all(
        self, items: list[str], fn: Callable[[str], Any], wait: float | None = None
    ) -> dict:
        """`fn(item)` for each item, concurrently and uncached; an item that raises or does not
        finish in time is left out of the result."""
        futures = {i: self.pool.submit(fn, i) for i in items}
        deadline = time.monotonic() + (self.wait if wait is None else wait)
        out = {}
        for item, future in futures.items():
            try:
                out[item] = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:  # noqa: BLE001 - includes the timeout
                continue
        return out

    def invalidate(self, app_id: str | None = None) -> None:
        """Forget what was read about one app (or all): its lab, its files."""
        with self.lock:
            for table in (self.kept, self.facts_kept):
                if app_id is None:
                    table.clear()
                else:
                    table.pop(app_id, None)
            # a read still running began before the change: let it answer, but do not keep it
            self.dirty |= {app_id} & set(self.running) if app_id else set(self.running)

    # -- facts read from files ---------------------------------------------------------------

    def facts(self, root, app_id: str, read: Callable[[Any, str], Any]) -> Any:
        """`read(root, app_id)`, kept for the same short time (it walks the app's files)."""
        with self.lock:
            hit = self.facts_kept.get(app_id)
            if hit and self.clock() - hit[0] < self.ttl:
                return hit[1]
        value = read(root, app_id)
        with self.lock:
            self.facts_kept[app_id] = (self.clock(), value)
        return value
