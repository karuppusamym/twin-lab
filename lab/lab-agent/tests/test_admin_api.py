"""The admin API in-process: token gate, JSON-only bodies, and the baseline kept as one unit.

The database is a stand-in that records what it is asked to restore; no Docker, no Postgres.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("psycopg")
pytest.importorskip("starlette")

from lab_agent import app as agent_app  # noqa: E402
from lab_agent.app import BASELINE_META, BASELINE_ROWS, LabAgent  # noqa: E402
from lab_agent.config import Settings  # noqa: E402
from lab_agent.data import LoadResult  # noqa: E402

TOKEN = "s3cret-lab-token"


class FakeDatabase:
    """Records each replace(); `gate`, when set, holds a replace until it is opened."""

    def __init__(self) -> None:
        self.replaced: list[dict] = []
        self.gate: asyncio.Event | None = None

    async def replace(self, rows):
        if self.gate is not None:
            await self.gate.wait()
        self.replaced.append(rows)
        return LoadResult(
            counts={t: len(r) for t, r in rows.items()},
            replaced=sorted(rows),
            cleared=[],
            snapshot=rows,
            triggers="enabled",
        )

    async def snapshot(self, tables=None):
        return None, self.replaced[-1] if self.replaced else {}

    async def ping(self, timeout: float = 3.0) -> bool:
        return True


def make_agent(tmp_path, token=None) -> LabAgent:
    settings = Settings(
        app_id="demo", app_upstream="http://upstream", state_dir=tmp_path, lab_token=token
    )
    agent = LabAgent(settings, start_db_proxy=False)
    agent.database = FakeDatabase()
    return agent


def run(agent: LabAgent, scenario):
    async def main():
        await agent.startup()
        try:
            transport = httpx.ASGITransport(app=agent)
            async with httpx.AsyncClient(transport=transport, base_url="http://lab") as client:
                return await scenario(client)
        finally:
            await agent.shutdown()

    return asyncio.run(main())


def rows(label: str) -> dict:
    return {"cases": [{"id": 1, "title": label}]}


# --- token ---------------------------------------------------------------------------------------


def admin_calls(agent: LabAgent) -> list[tuple[str, str]]:
    calls = []
    for route in agent.admin.routes:
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        calls += [(m, path) for m in sorted(route.methods or ()) if m != "HEAD"]
    return calls + [("GET", "/_lab"), ("GET", "/_lab/not-a-route"), ("POST", "/_lab/reset/")]


def test_every_admin_route_and_method_needs_the_token(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, token=TOKEN)
    calls = admin_calls(agent)
    assert len(calls) >= 18
    compared = []
    real = agent_app.secrets.compare_digest
    monkeypatch.setattr(
        agent_app.secrets, "compare_digest", lambda a, b: compared.append(1) or real(a, b)
    )
    bad_headers = [
        {},
        {"X-Lab-Token": "wrong"},
        {"X-Lab-Token": TOKEN[:-1]},
        {"X-Lab-Token": TOKEN + "x"},
        {"X-Lab-Token": ""},
        {"x-lab-token": TOKEN.upper()},
    ]

    async def scenario(c):
        refused = []
        for method, path in calls:
            for headers in bad_headers:
                r = await c.request(method, path, headers=headers)
                refused.append((method, path, r.status_code, r.json()["detail"]["code"]))
            # Two token headers, one of them right, are refused too.
            r = await c.request(
                method, path, headers=[("X-Lab-Token", TOKEN), ("X-Lab-Token", "wrong")]
            )
            refused.append((method, path, r.status_code, r.json()["detail"]["code"]))
        ok = await c.get("/_lab/condition", headers={"X-Lab-Token": TOKEN})
        return refused, ok

    refused, ok = run(agent, scenario)
    assert all(status == 401 and code == "LAB_TOKEN_REQUIRED" for _, _, status, code in refused)
    assert len(refused) == len(calls) * (len(bad_headers) + 1)
    assert ok.status_code == 200
    assert compared  # the check goes through secrets.compare_digest (constant time)


# --- JSON-only bodies ----------------------------------------------------------------------------


def test_admin_api_refuses_bodies_that_are_not_json(tmp_path):
    agent = make_agent(tmp_path)

    async def scenario(c):
        refused = [
            await c.post("/_lab/data/load", data={"rows": "x"}),  # an HTML form
            await c.post("/_lab/reset", content=b"a=1", headers={"content-type": "text/plain"}),
            await c.post("/_lab/reset", files={"f": ("a.txt", b"x")}),  # multipart
            await c.post(
                "/_lab/reset", headers={"content-type": "application/x-www-form-urlencoded"}
            ),  # an empty form is still a form
            await c.put("/_lab/condition", content=b'{"id": "x"}'),  # body without a type
            await c.put(
                "/_lab/condition",
                content=b'{"id": "x"}',
                headers={"content-type": "text/plain;charset=UTF-8"},
            ),
            await c.post(
                "/_lab/session/start",
                content=b"{}",
                headers={"content-type": "application/jsonp"},
            ),
        ]
        accepted = [
            await c.post("/_lab/condition/rewind"),  # no body, no type
            await c.put("/_lab/condition", json={"id": "plain"}),
            await c.put(
                "/_lab/condition",
                content=json.dumps({"id": "charset"}).encode(),
                headers={"content-type": "Application/JSON; charset=utf-8"},
            ),
            await c.post("/_lab/data/load", json={"rows": rows("a"), "label": "a"}),
            await c.post("/_lab/reset", headers={"content-type": "application/json"}),
            await c.delete("/_lab/condition"),
            await c.get("/_lab/condition"),
        ]
        return refused, accepted

    refused, accepted = run(agent, scenario)
    for r in refused:
        assert r.status_code == 415, (r.request.method, r.request.url.path, r.text)
        assert r.json()["detail"]["code"] == "LAB_JSON_REQUIRED"
    assert [r.status_code for r in accepted] == [200] * len(accepted), [r.text for r in accepted]
    assert agent.database.replaced == [rows("a"), rows("a")]  # the load and the JSON reset only


# --- the baseline as one unit --------------------------------------------------------------------


def test_reset_reads_the_baseline_inside_the_lock(tmp_path):
    agent = make_agent(tmp_path)
    db: FakeDatabase = agent.database

    async def scenario(c):
        r = await c.post("/_lab/data/load", json={"rows": rows("old"), "label": "old"})
        assert r.status_code == 200, r.text
        db.gate = asyncio.Event()
        load = asyncio.create_task(
            c.post("/_lab/data/load", json={"rows": rows("new"), "label": "new"})
        )
        await asyncio.sleep(0.05)  # the new load holds the lock, its commit not yet done
        reset = asyncio.create_task(c.post("/_lab/reset"))
        await asyncio.sleep(0.05)  # reset is waiting for the lock
        db.gate.set()
        return await load, await reset

    load, reset = run(agent, scenario)
    assert load.status_code == 200 and reset.status_code == 200, reset.text
    assert reset.json()["baseline"]["label"] == "new"
    assert db.replaced == [rows("old"), rows("new"), rows("new")]  # reset restored the new rows


def test_baseline_files_share_an_id(tmp_path):
    agent = make_agent(tmp_path)

    async def scenario(c):
        return await c.post("/_lab/data/load", json={"rows": rows("a"), "label": "a"})

    r = run(agent, scenario)
    meta = json.loads((tmp_path / BASELINE_META).read_text(encoding="utf-8"))
    stored = json.loads((tmp_path / BASELINE_ROWS).read_text(encoding="utf-8"))
    assert meta["id"] == stored["baseline_id"] == r.json()["id"]
    assert stored["rows"] == rows("a")


@pytest.mark.parametrize("damage", ["other_id", "legacy", "meta_changed"])
def test_reset_refuses_rows_from_another_baseline(tmp_path, damage):
    agent = make_agent(tmp_path)

    async def load(c):
        return await c.post("/_lab/data/load", json={"rows": rows("a"), "label": "a"})

    assert run(agent, load).status_code == 200
    rows_path, meta_path = tmp_path / BASELINE_ROWS, tmp_path / BASELINE_META
    stored = json.loads(rows_path.read_text(encoding="utf-8"))
    if damage == "other_id":  # a crash between the two writes of a later baseline
        stored = {"baseline_id": "bl_other", "rows": rows("b")}
        rows_path.write_text(json.dumps(stored), encoding="utf-8")
    elif damage == "legacy":  # rows without an id: cannot be shown to belong
        rows_path.write_text(json.dumps(rows("b")), encoding="utf-8")
    else:  # metadata replaced on disk behind the agent's back
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta_path.write_text(json.dumps({**meta, "id": "bl_other"}), encoding="utf-8")

    second = make_agent(tmp_path) if damage != "meta_changed" else agent

    async def reset(c):
        return await c.post("/_lab/reset")

    r = run(second, reset)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "LAB_BASELINE_MISMATCH"
    assert second.database.replaced == ([] if second is not agent else [rows("a")])
