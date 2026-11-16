"""The DB link proxy against a local echo server: bytes, order, latency, concurrency."""

from __future__ import annotations

import asyncio
import time

from lab_agent.dbproxy import DbLinkProxy
from labctl.models import DbLink


async def _echo_server():
    async def handle(r, w):
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
        w.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def _roundtrip(port: int, payload: bytes) -> tuple[bytes, float]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    t = time.monotonic()
    w.write(payload)
    await w.drain()
    got = await r.readexactly(len(payload))
    elapsed = time.monotonic() - t
    w.close()
    return got, elapsed


def test_passthrough_latency_and_concurrency():
    link: dict[str, DbLink | None] = {"v": None}

    async def main():
        server = await _echo_server()
        upstream = server.sockets[0].getsockname()[:2]
        proxy = DbLinkProxy(upstream, lambda: link["v"])
        await proxy.start("127.0.0.1", 0)
        try:
            payload = bytes(range(256)) * 4096  # 1 MiB, many chunks
            got, fast = await _roundtrip(proxy.port, payload)
            assert got == payload

            link["v"] = DbLink(latency_ms=100, jitter_ms=0)
            got, slow = await _roundtrip(proxy.port, b"ping")
            assert got == b"ping" and slow >= 0.1
            # A large reply in many chunks is delayed about once, not once per chunk.
            got, big = await _roundtrip(proxy.port, payload)
            assert got == payload and big < 2.0

            link["v"] = DbLink(latency_ms=50, jitter_ms=40)
            results = await asyncio.gather(
                *(_roundtrip(proxy.port, f"conn-{i}".encode() * 100) for i in range(100))
            )
            assert all(g == f"conn-{i}".encode() * 100 for i, (g, _) in enumerate(results))
            assert all(e >= 0.009 for _, e in results)
            return fast, slow
        finally:
            await proxy.stop()
            server.close()

    fast, slow = asyncio.run(main())
    assert slow > fast


def test_unreachable_upstream_closes_client():
    async def main():
        proxy = DbLinkProxy(("127.0.0.1", 9), lambda: None, connect_timeout=2)
        await proxy.start("127.0.0.1", 0)
        try:
            r, w = await asyncio.open_connection("127.0.0.1", proxy.port)
            data = await asyncio.wait_for(r.read(10), 5)
            w.close()
            return data
        finally:
            await proxy.stop()

    assert asyncio.run(main()) == b""
