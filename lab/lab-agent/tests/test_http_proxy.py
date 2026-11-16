"""The front door in-process: a small upstream app, the agent in front, no Docker, no database."""

from __future__ import annotations

import asyncio
import time

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("psycopg")
pytest.importorskip("starlette")

from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, Response, StreamingResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

from lab_agent.app import LabAgent  # noqa: E402
from lab_agent.config import Settings  # noqa: E402


async def echo(request: Request) -> Response:
    return JSONResponse(
        {
            "method": request.method,
            "path": request.url.path,
            "raw_path": request.scope.get("raw_path", b"").decode(),
            "query": request.url.query,
            "headers": {k: v for k, v in request.headers.items()},
            "body": (await request.body()).decode(),
        }
    )


async def redirect(request: Request) -> Response:
    return Response(status_code=302, headers={"location": "/elsewhere"})


async def cookies(request: Request) -> Response:
    r = JSONResponse({"ok": True}, status_code=201)
    r.set_cookie("a", "1")
    r.set_cookie("b", "2", httponly=True)
    return r


async def big(request: Request) -> Response:
    async def gen():
        for _ in range(64):
            yield b"x" * 65536

    return StreamingResponse(gen(), media_type="application/octet-stream")


UPSTREAM = Starlette(
    routes=[
        Route("/echo/{rest:path}", echo, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        Route("/redirect", redirect),
        Route("/cookies", cookies, methods=["POST"]),
        Route("/big", big),
    ]
)


def make_agent(tmp_path, token=None) -> LabAgent:
    settings = Settings(
        app_id="demo",
        app_upstream="http://upstream",
        state_dir=tmp_path,
        lab_token=token,
    )
    return LabAgent(settings, transport=httpx.ASGITransport(app=UPSTREAM), start_db_proxy=False)


def run(agent: LabAgent, scenario):
    async def main():
        await agent.startup()
        try:
            transport = httpx.ASGITransport(app=agent, client=("10.0.0.9", 5000))
            async with httpx.AsyncClient(transport=transport, base_url="http://lab") as client:
                return await scenario(client)
        finally:
            await agent.shutdown()

    return asyncio.run(main())


def test_forwards_request_and_response(tmp_path):
    agent = make_agent(tmp_path)

    async def scenario(c):
        r = await c.post(
            "/echo/a%2Fb/c?x=1&y=two",
            content=b"hello",
            headers={"x-custom": "v", "connection": "x-drop", "x-drop": "1", "host": "front:1"},
        )
        echoed = r.json()
        red = await c.get("/redirect")
        cook = await c.post("/cookies")
        big = await c.get("/big")
        return echoed, red, cook, big

    echoed, red, cook, big = run(agent, scenario)
    assert echoed["method"] == "POST" and echoed["body"] == "hello"
    assert echoed["query"] == "x=1&y=two"
    assert echoed["raw_path"] == "/echo/a%2Fb/c"
    assert echoed["headers"]["x-custom"] == "v"
    assert "x-drop" not in echoed["headers"]
    assert echoed["headers"]["host"] == "front:1"
    assert echoed["headers"]["x-forwarded-for"] == "10.0.0.9"
    assert red.status_code == 302 and red.headers["location"] == "/elsewhere"
    assert cook.status_code == 201
    assert len(cook.headers.get_list("set-cookie")) == 2
    assert len(big.content) == 64 * 65536
    paths = [(r["method"], r["path"], r["status"]) for r in agent.recent]
    assert paths == [
        ("POST", "/echo/a/b/c", 200),
        ("GET", "/redirect", 302),
        ("POST", "/cookies", 201),
        ("GET", "/big", 200),
    ]


def test_condition_latency_faults_and_persistence(tmp_path):
    agent = make_agent(tmp_path)
    condition = {
        "id": "slow",
        "seed": 11,
        "http": [{"match": "GET /echo/slow*", "latency_ms": {"p50": 300, "p95": 300}}],
        "faults": [{"match": "POST /echo/*", "status": 503, "rate": 0.5}],
    }

    async def scenario(c):
        assert (await c.get("/_lab/condition")).json()["id"] is None
        put = await c.put("/_lab/condition", json=condition)
        assert put.status_code == 200, put.text
        t = time.monotonic()
        await c.get("/echo/slow/1")
        slow = time.monotonic() - t
        t = time.monotonic()
        await c.get("/echo/fast")
        fast = time.monotonic() - t
        statuses = [(await c.post(f"/echo/n{i}")).status_code for i in range(20)]
        await c.post("/_lab/condition/rewind")
        again = [(await c.post(f"/echo/n{i}")).status_code for i in range(20)]
        fault = await c.post("/echo/n0")
        bad = await c.put("/_lab/condition", json={"http": [{"match": "nope"}]})
        return slow, fast, statuses, again, fault, bad

    slow, fast, statuses, again, fault, bad = run(agent, scenario)
    assert slow >= 0.3 and fast < 0.2
    assert statuses == again and 503 in statuses and 200 in statuses
    assert bad.status_code == 422
    rec = [r for r in agent.recent if r["path"] == "/echo/slow/1"][0]
    assert rec["injected_ms"] >= 300
    faulted = [r for r in agent.recent if r["fault"]]
    assert faulted and all(r["status"] == 503 and r["app_ms"] == 0 for r in faulted)

    # A fault looks like this, and the condition survives a restart.
    second = make_agent(tmp_path)

    async def after_restart(c):
        return (await c.get("/_lab/condition")).json(), await c.post("/echo/n1")

    cond, _ = run(second, after_restart)
    assert cond["id"] == "slow" and cond["seed"] == 11


def test_fault_body(tmp_path):
    agent = make_agent(tmp_path)

    async def scenario(c):
        await c.put(
            "/_lab/condition",
            json={"id": "down", "faults": [{"match": "*", "status": 503, "rate": 1}]},
        )
        return await c.get("/echo/x")

    r = run(agent, scenario)
    assert r.status_code == 503
    assert r.headers["x-twinlab-fault"] == "1"
    assert r.json()["detail"]["code"] == "LAB_INJECTED_FAULT"


def test_token_required(tmp_path):
    agent = make_agent(tmp_path, token="t0ken")

    async def scenario(c):
        return (
            await c.get("/_lab/condition"),
            await c.get("/_lab/condition", headers={"x-lab-token": "t0ken"}),
            await c.get("/echo/open"),
        )

    no, yes, app = run(agent, scenario)
    assert no.status_code == 401 and yes.status_code == 200 and app.status_code == 200


def test_upstream_down_is_502(tmp_path):
    settings = Settings(app_id="demo", app_upstream="http://127.0.0.1:9", state_dir=tmp_path)
    agent = LabAgent(settings, start_db_proxy=False)

    async def scenario(c):
        return await c.get("/anything"), await c.get("/_lab/health")

    r, health = run(agent, scenario)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "LAB_UPSTREAM_UNAVAILABLE"
    assert health.status_code == 503 and health.json()["app"] == "down"
