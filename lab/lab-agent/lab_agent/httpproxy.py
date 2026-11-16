"""The HTTP front door: forwards every request that is not under /_lab/ to the app.

Method, path (as sent), query, headers (except hop-by-hop), body, status, response headers and
cookies pass through unchanged. Redirects are returned to the client, never followed. Response
bodies are streamed and not decoded (`Content-Encoding` stays as the app sent it).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from lab_agent.conditions import ConditionEngine
from lab_agent.sessions import iso, utcnow

log = logging.getLogger("lab_agent.proxy")

HOP_BY_HOP = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"proxy-connection",
    b"te",
    b"trailer",
    b"trailers",
    b"transfer-encoding",
    b"upgrade",
}

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def _connection_tokens(headers: list[tuple[bytes, bytes]]) -> set[bytes]:
    tokens: set[bytes] = set()
    for k, v in headers:
        if k.lower() == b"connection":
            tokens |= {t.strip().lower() for t in v.split(b",") if t.strip()}
    return tokens


def strip_hop_by_hop(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    drop = HOP_BY_HOP | _connection_tokens(headers)
    return [(k, v) for k, v in headers if k.lower() not in drop]


async def read_body(receive: Receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ConnectionAbortedError("client disconnected")
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


async def send_json(send: Send, status: int, body: Any, headers: list | None = None) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class HttpProxy:
    def __init__(
        self,
        app_id: str,
        upstream: str,
        client: httpx.AsyncClient,
        engine: ConditionEngine,
        record: Callable[[dict[str, Any]], None],
    ) -> None:
        self.app_id = app_id
        self.upstream = upstream.rstrip("/")
        self.client = client
        self.engine = engine
        self.record = record

    def _request_headers(self, scope: Scope) -> list[tuple[bytes, bytes]]:
        headers = strip_hop_by_hop(list(scope.get("headers") or []))
        client = scope.get("client")
        if client:
            prior = [v for k, v in headers if k.lower() == b"x-forwarded-for"]
            chain = b", ".join([*prior, client[0].encode()])
            headers = [(k, v) for k, v in headers if k.lower() != b"x-forwarded-for"]
            headers.append((b"x-forwarded-for", chain))
        if not any(k.lower() == b"x-forwarded-proto" for k, _ in headers):
            headers.append((b"x-forwarded-proto", scope.get("scheme", "http").encode()))
        host = next((v for k, v in headers if k.lower() == b"host"), None)
        if host and not any(k.lower() == b"x-forwarded-host" for k, _ in headers):
            headers.append((b"x-forwarded-host", host))
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        method: str = scope["method"]
        path: str = scope["path"]
        query: bytes = scope.get("query_string") or b""
        ts = iso(utcnow())
        rec: dict[str, Any] = {
            "ts": ts,
            "app": self.app_id,
            "method": method,
            "path": path,
            "status": 0,
            "app_ms": 0,
            "injected_ms": 0,
            "fault": False,
        }
        try:
            body = await read_body(receive)
        except ConnectionAbortedError:
            return
        decision = self.engine.decide(method, path, query.decode("latin-1"))
        if decision.delay_ms > 0:
            started = time.monotonic()
            await asyncio.sleep(decision.delay_ms / 1000)
            rec["injected_ms"] = round((time.monotonic() - started) * 1000)
        try:
            if decision.fault is not None:
                fault = decision.fault
                rec.update(status=fault.status, fault=True)
                condition = self.engine.condition.id or "unnamed"
                await send_json(
                    send,
                    fault.status,
                    {
                        "detail": {
                            "code": "LAB_INJECTED_FAULT",
                            "message": f"Fault injected by Twin Lab condition '{condition}' "
                            f"(rule '{fault.match}', rate {fault.rate})",
                        }
                    },
                    [(b"x-twinlab-fault", b"1"), (b"cache-control", b"no-store")],
                )
                return
            await self._forward(scope, send, method, query, body, rec)
        finally:
            self.record(rec)

    async def _forward(
        self, scope: Scope, send: Send, method: str, query: bytes, body: bytes, rec: dict
    ) -> None:
        raw_path = (scope.get("raw_path") or scope["path"].encode()).split(b"?", 1)[0]
        url = self.upstream + raw_path.decode("latin-1")
        if query:
            url += "?" + query.decode("latin-1")
        request = self.client.build_request(
            method, url, headers=self._request_headers(scope), content=body or None
        )
        if not body and method in ("GET", "HEAD", "DELETE", "OPTIONS"):
            request.headers.pop("content-length", None)
        started = time.monotonic()
        try:
            response = await self.client.send(request, stream=True, follow_redirects=False)
        except httpx.TimeoutException as exc:
            rec.update(status=504, app_ms=round((time.monotonic() - started) * 1000))
            await send_json(
                send,
                504,
                {"detail": {"code": "LAB_UPSTREAM_TIMEOUT", "message": str(exc) or "timeout"}},
            )
            return
        except httpx.HTTPError as exc:
            rec.update(status=502, app_ms=round((time.monotonic() - started) * 1000))
            await send_json(
                send,
                502,
                {
                    "detail": {
                        "code": "LAB_UPSTREAM_UNAVAILABLE",
                        "message": f"app unreachable: {exc}",
                    }
                },
            )
            return
        app_s = time.monotonic() - started
        rec["status"] = response.status_code
        try:
            headers = strip_hop_by_hop(list(response.headers.raw))
            await send(
                {"type": "http.response.start", "status": response.status_code, "headers": headers}
            )
            chunks = response.aiter_raw().__aiter__()
            while True:
                t = time.monotonic()
                try:
                    chunk = await chunks.__anext__()
                except StopAsyncIteration:
                    app_s += time.monotonic() - t
                    break
                app_s += time.monotonic() - t
                if chunk:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except httpx.HTTPError as exc:
            log.warning("upstream response broke off: %s %s: %s", method, scope["path"], exc)
            raise
        finally:
            rec["app_ms"] = round(app_s * 1000)
            await response.aclose()
