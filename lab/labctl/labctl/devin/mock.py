"""A local stand-in for the Devin API v3, for tests and offline demos.

    python -m labctl.devin.mock --port 8740

Environment: MOCK_API_KEY (the bearer key it accepts, default `cog_mock_key`), MOCK_STEP_SECONDS
(seconds per lifecycle step, default 2), MOCK_RETRY_AFTER (Retry-After on simulated 429s,
default 1).

Lifecycle, one step per MOCK_STEP_SECONDS: `new` -> `running/working` -> `running/finished` with
one open pull request on the first repo given. Words in the prompt, or in any file of an attached
zip (such as a brief), change the script:

- `[mock:ask]`: after working, `running/waiting_for_user` until a message arrives, then working
  for one more step, then finished.
- `[mock:error]`: ends in `error` instead of finished.
- `[mock:out_of_credits]`: ends `suspended/out_of_credits`.
- `[mock:no_pr]`: finishes without a pull request.

Request headers for tests: `X-Mock-Rate-Limit: <token>` answers 429 the first time a token is
seen; `X-Mock-Fail: <status>` together with `X-Mock-Fail-Token: <token>` answers that status once.
Attachments are kept in memory and served back at `/_mock/attachments/{id}` (no auth).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import itertools
import os
import secrets
import socket
import threading
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response

DEFAULT_KEY = "cog_mock_key"
_REQUIRED_FILE = File(...)
PROBLEM = "application/problem+json"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def problem(status: int, title: str, detail: str, headers: dict[str, str] | None = None):
    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type=PROBLEM,
        headers=headers,
    )


@dataclass
class MockSession:
    session_id: str
    org_id: str
    prompt: str
    title: str | None
    tags: list[str]
    repos: list[str]
    attachment_urls: list[str]
    max_acu_limit: int | None
    created_at: str
    phase: str = "new"  # new, working, asking, finished, error, suspended, exit
    phase_start: float = field(default_factory=time.monotonic)
    answered: bool = False
    messages: list[dict] = field(default_factory=list)
    pull_requests: list[dict] = field(default_factory=list)
    updated_at: str = field(default_factory=_now)
    archived: bool = False


class MockState:
    def __init__(self, api_key: str, step_seconds: float, retry_after: str) -> None:
        self.api_key = api_key
        self.step = step_seconds
        self.retry_after = retry_after
        self.lock = threading.Lock()
        self.sessions: dict[str, MockSession] = {}
        self.attachments: dict[str, tuple[str, bytes, str]] = {}
        self.seen_tokens: set[str] = set()
        self.pr_numbers = itertools.count(1)

    def advance(self, s: MockSession) -> None:
        """Move a session along its script according to the time spent in its current phase."""
        while True:
            if s.phase in ("finished", "error", "suspended", "exit", "asking"):
                return
            if time.monotonic() - s.phase_start < self.step:
                return
            s.phase_start += self.step
            s.updated_at = _now()
            if s.phase == "new":
                s.phase = "working"
            elif s.phase == "working":
                if "[mock:ask]" in s.prompt and not s.answered:
                    s.phase = "asking"
                elif "[mock:error]" in s.prompt:
                    s.phase = "error"
                elif "[mock:out_of_credits]" in s.prompt:
                    s.phase = "suspended"
                else:
                    s.phase = "finished"
                    if "[mock:no_pr]" not in s.prompt:
                        repo = s.repos[0] if s.repos else "example/twinlab"
                        number = next(self.pr_numbers)
                        s.pull_requests = [
                            {
                                "pr_url": f"https://github.com/{repo}/pull/{number}",
                                "pr_state": "open",
                            }
                        ]

    def view(self, s: MockSession) -> dict[str, Any]:
        status, detail = {
            "new": ("new", None),
            "working": ("running", "working"),
            "asking": ("running", "waiting_for_user"),
            "finished": ("running", "finished"),
            "error": ("error", None),
            "suspended": ("suspended", "out_of_credits"),
            "exit": ("exit", None),
        }[s.phase]
        acus = 0.0 if s.phase == "new" else round(0.5 + 0.25 * len(s.messages), 2)
        return {
            "session_id": s.session_id,
            "url": f"https://app.devin.ai/sessions/{s.session_id}",
            "status": status,
            "status_detail": detail,
            "tags": s.tags,
            "org_id": s.org_id,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "acus_consumed": acus,
            "pull_requests": s.pull_requests,
            "title": s.title,
        }


MARKERS = ("[mock:ask]", "[mock:error]", "[mock:out_of_credits]", "[mock:no_pr]")


def _attached_markers(state: MockState, urls: list[str]) -> str:
    """Script markers found inside attached files (a zip is searched member by member)."""
    found: set[str] = set()
    for url in urls:
        item = state.attachments.get(url.rstrip("/").rsplit("/", 1)[-1])
        if item is None:
            continue
        blobs = [item[1]]
        if item[1][:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(item[1])) as zf:
                    blobs = [zf.read(n) for n in zf.namelist()]
            except zipfile.BadZipFile:
                pass
        for blob in blobs:
            found.update(m for m in MARKERS if m.encode() in blob)
    return "".join(f"\n{m}" for m in sorted(found))


def create_app(
    api_key: str | None = None, step_seconds: float | None = None, retry_after: str | None = None
) -> FastAPI:
    state = MockState(
        api_key=api_key or os.environ.get("MOCK_API_KEY") or DEFAULT_KEY,
        step_seconds=float(
            step_seconds if step_seconds is not None else os.environ.get("MOCK_STEP_SECONDS", "2")
        ),
        retry_after=retry_after or os.environ.get("MOCK_RETRY_AFTER", "1"),
    )
    app = FastAPI(title="Devin API v3 (Twin Lab mock)")
    app.state.mock = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/_mock/"):
            return await call_next(request)
        token = request.headers.get("x-mock-rate-limit")
        if token:
            with state.lock:
                first = token not in state.seen_tokens
                state.seen_tokens.add(token)
            if first:
                return problem(
                    429,
                    "Too Many Requests",
                    "Simulated rate limit",
                    headers={"Retry-After": state.retry_after},
                )
        fail, fail_token = (
            request.headers.get("x-mock-fail"),
            request.headers.get("x-mock-fail-token"),
        )
        if fail and fail_token:
            key = f"fail:{fail_token}"
            with state.lock:
                first = key not in state.seen_tokens
                state.seen_tokens.add(key)
            if first:
                return problem(int(fail), "Simulated failure", f"X-Mock-Fail {fail}")
        auth = request.headers.get("authorization", "")
        scheme, _, key = auth.partition(" ")
        if scheme.lower() != "bearer" or not key:
            return problem(401, "Unauthorized", "Missing bearer token")
        if not secrets.compare_digest(key.strip(), state.api_key):
            return problem(401, "Unauthorized", "Invalid API key")
        return await call_next(request)

    def find(org_id: str, devin_id: str) -> MockSession | JSONResponse:
        if not devin_id.startswith("devin-"):
            return problem(
                404, "Not Found", f"Session {devin_id} not found (expected devin- prefix)"
            )
        s = state.sessions.get(devin_id.removeprefix("devin-"))
        if s is None or s.org_id != org_id:
            return problem(404, "Not Found", f"Session {devin_id} not found")
        return s

    @app.post("/v3/organizations/{org_id}/attachments")
    async def upload(org_id: str, request: Request, file: UploadFile = _REQUIRED_FILE):
        data = await file.read()
        attachment_id = secrets.token_hex(8)
        name = file.filename or "upload"
        with state.lock:
            state.attachments[attachment_id] = (name, data, file.content_type or "")
        url = str(request.base_url).rstrip("/") + f"/_mock/attachments/{attachment_id}"
        return {"attachment_id": attachment_id, "name": name, "url": url}

    @app.get("/_mock/attachments/{attachment_id}")
    def download(attachment_id: str):
        item = state.attachments.get(attachment_id)
        if item is None:
            return problem(404, "Not Found", "No such attachment")
        name, data, mime = item
        return Response(data, media_type=mime or "application/octet-stream")

    @app.post("/v3/organizations/{org_id}/sessions")
    async def create(org_id: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            return problem(400, "Bad Request", "Body must be JSON")
        if not isinstance(body, dict) or not str(body.get("prompt") or "").strip():
            return problem(422, "Unprocessable Content", "prompt is required")
        for key in ("tags", "repos", "attachment_urls"):
            value = body.get(key)
            if value is not None and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)
            ):
                return problem(422, "Unprocessable Content", f"{key} must be a list of strings")
        limit = body.get("max_acu_limit")
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            return problem(422, "Unprocessable Content", "max_acu_limit must be a positive integer")
        sid = secrets.token_hex(12)
        urls = list(body.get("attachment_urls") or [])
        s = MockSession(
            session_id=sid,
            org_id=org_id,
            prompt=body["prompt"] + _attached_markers(state, urls),
            title=body.get("title"),
            tags=list(body.get("tags") or []),
            repos=list(body.get("repos") or []),
            attachment_urls=urls,
            max_acu_limit=limit,
            created_at=_now(),
        )
        with state.lock:
            state.sessions[sid] = s
            return state.view(s)

    @app.get("/v3/organizations/{org_id}/sessions")
    def list_sessions(org_id: str, first: int = 100):
        with state.lock:
            items = [s for s in state.sessions.values() if s.org_id == org_id][: max(0, first)]
            for s in items:
                state.advance(s)
            return {"items": [state.view(s) for s in items], "has_next_page": False}

    @app.get("/v3/organizations/{org_id}/sessions/{devin_id}")
    def get(org_id: str, devin_id: str):
        with state.lock:
            s = find(org_id, devin_id)
            if isinstance(s, JSONResponse):
                return s
            state.advance(s)
            return state.view(s)

    @app.post("/v3/organizations/{org_id}/sessions/{devin_id}/messages")
    async def message(org_id: str, devin_id: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            return problem(400, "Bad Request", "Body must be JSON")
        text = str((body or {}).get("message") or "").strip()
        if not text:
            return problem(422, "Unprocessable Content", "message is required")
        with state.lock:
            s = find(org_id, devin_id)
            if isinstance(s, JSONResponse):
                return s
            state.advance(s)
            if s.phase == "exit":
                return problem(409, "Conflict", "Session has been terminated")
            s.messages.append({"message": text, "attachment_urls": body.get("attachment_urls")})
            if s.phase in ("asking", "suspended"):
                if s.phase == "suspended":  # credits were topped up: do not suspend again
                    s.prompt = s.prompt.replace("[mock:out_of_credits]", "")
                s.answered = True
                s.phase = "working"
                s.phase_start = time.monotonic()
            s.updated_at = _now()
            return state.view(s)

    @app.delete("/v3/organizations/{org_id}/sessions/{devin_id}")
    def terminate(org_id: str, devin_id: str, archive: bool = False):
        with state.lock:
            s = find(org_id, devin_id)
            if isinstance(s, JSONResponse):
                return s
            state.advance(s)
            s.phase = "exit"
            s.archived = archive
            s.updated_at = _now()
            return state.view(s)

    return app


# --- running it ------------------------------------------------------------------------------------


def free_port(low: int = 18400, high: int = 18499) -> int:
    for port in range(low, high + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {low}-{high}")


@contextlib.contextmanager
def serve_in_thread(app: FastAPI, port: int | None = None) -> Iterator[str]:
    """Run `app` with uvicorn on 127.0.0.1 in a thread; yields its base URL. For tests."""
    import uvicorn

    port = port or free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="off", ws="none"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError(f"mock server did not start on port {port}")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Local mock of the Devin API v3.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8740)
    args = parser.parse_args(argv)
    app = create_app()
    print(
        f"Devin mock on http://{args.host}:{args.port} "
        f"(step {app.state.mock.step}s; key from MOCK_API_KEY or the default)"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
