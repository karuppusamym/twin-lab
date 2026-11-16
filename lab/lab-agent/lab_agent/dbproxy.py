"""The database link: a TCP proxy between the app and its database.

Bytes pass through untouched (Postgres wire protocol, SSLRequest and TLS included), so any client
library works. While the condition has `db.latency_ms` / `jitter_ms`, every chunk from the server
to the client is held back by latency +/- jitter (uniform, never below zero). Chunks keep their
order and are scheduled from the time they arrived, so a result that arrives in many chunks is
delayed once per round trip, not once per chunk. Client-to-server traffic is not delayed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import socket
import time
from collections.abc import Callable

from labctl.models import DbLink

log = logging.getLogger("lab_agent.dbproxy")

CHUNK = 64 * 1024
QUEUE_CHUNKS = 64  # held-back chunks per connection before the proxy stops reading the server


class DbLinkProxy:
    def __init__(
        self,
        upstream: tuple[str, int],
        link: Callable[[], DbLink | None],
        rng: Callable[[], random.Random] = random.Random,
        connect_timeout: float = 10.0,
    ) -> None:
        self.upstream = upstream
        self.link = link
        self.rng_factory = rng
        self.connect_timeout = connect_timeout
        self.server: asyncio.base_events.Server | None = None
        self.active = 0
        self.total = 0
        self._tasks: set[asyncio.Task] = set()

    async def start(self, host: str, port: int) -> None:
        self.server = await asyncio.start_server(
            self._handle, host, port, backlog=1024, reuse_address=True
        )

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.server.wait_closed()

    def _delay_s(self, rng: random.Random) -> float:
        link = self.link()
        if link is None:
            return 0.0
        jitter = rng.uniform(-link.jitter_ms, link.jitter_ms) if link.jitter_ms else 0.0
        return max(0.0, link.latency_ms + jitter) / 1000.0

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter):
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self.active += 1
        self.total += 1
        try:
            try:
                server_r, server_w = await asyncio.wait_for(
                    asyncio.open_connection(*self.upstream), self.connect_timeout
                )
            except (OSError, TimeoutError) as exc:
                log.warning("db link: cannot reach %s:%s: %s", *self.upstream, exc)
                return
            for w in (client_w, server_w):
                sock = w.get_extra_info("socket")
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            rng = self.rng_factory()
            up = asyncio.create_task(self._pipe(client_r, server_w))
            down = asyncio.create_task(self._pipe_delayed(server_r, client_w, rng))
            try:
                done, _ = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
                if down not in done:
                    # The client finished sending: let the server's last replies through.
                    await asyncio.wait({down}, timeout=30)
            finally:
                for t in (up, down):
                    t.cancel()
                await asyncio.gather(up, down, return_exceptions=True)
                await _close(server_w)
        finally:
            await _close(client_w)
            self.active -= 1
            if task is not None:
                self._tasks.discard(task)

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(CHUNK):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            await _half_close(writer)

    async def _pipe_delayed(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rng: random.Random
    ) -> None:
        queue: asyncio.Queue[tuple[float, bytes] | None] = asyncio.Queue(QUEUE_CHUNKS)
        held = 0  # chunks queued or being delivered; while > 0, everything goes through the queue

        async def deliver() -> None:
            nonlocal held
            last = 0.0
            while (item := await queue.get()) is not None:
                due, data = item
                due = max(due, last)
                wait = due - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                last = due
                writer.write(data)
                await writer.drain()
                held -= 1

        sender = asyncio.create_task(deliver())
        try:
            while True:
                read = asyncio.ensure_future(reader.read(CHUNK))
                done, _ = await asyncio.wait({read, sender}, return_when=asyncio.FIRST_COMPLETED)
                if sender in done:  # the client went away
                    read.cancel()
                    sender.result()
                    return
                data = read.result()
                if not data:
                    break
                delay = self._delay_s(rng)
                if delay == 0.0 and held == 0:
                    # No condition and nothing held back: write straight through.
                    writer.write(data)
                    await writer.drain()
                    continue
                held += 1
                await queue.put((time.monotonic() + delay, data))
            await queue.put(None)
            await sender
        except (ConnectionError, OSError):
            pass
        finally:
            if not sender.done():
                sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            await _half_close(writer)


async def _half_close(writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(Exception):
        if writer.can_write_eof() and not writer.is_closing():
            writer.write_eof()
            return
    await _close(writer)


async def _close(writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(Exception):
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 2)
