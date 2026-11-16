"""Leases (one person reserves the shared lab) and baseline quarantine."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")
pytest.importorskip("psycopg")

from test_admin_api import TOKEN, make_agent, rows, run  # noqa: E402

from lab_agent import app as agent_app  # noqa: E402

H = {"X-Lab-Token": TOKEN}


def who(name: str) -> dict:
    return {**H, "X-Lab-Holder": name}


def test_a_lease_refuses_changes_by_others_and_lets_reads_through(tmp_path):
    agent = make_agent(tmp_path, token=TOKEN)

    async def scenario(c):
        await c.post("/_lab/data/load", json={"rows": rows("one")}, headers=who("Priya"))
        took = await c.post("/_lab/lease", json={"holder": "Priya", "minutes": 30,
                                                 "purpose": "month-end run"}, headers=H)  # fmt: skip
        other = await c.post("/_lab/reset", headers=who("Sam"))
        nobody = await c.post("/_lab/reset", headers=H)
        mine = await c.post("/_lab/reset", headers=who("  priya "))  # same name, any spacing/case
        read = await c.get("/_lab/data", headers=who("Sam"))
        steal = await c.post("/_lab/lease", json={"holder": "Sam"}, headers=H)
        info = (await c.get("/_lab/info", headers=H)).json()
        release_other = await c.request("DELETE", "/_lab/lease", headers=who("Sam"))
        forced = await c.request("DELETE", "/_lab/lease", json={"force": True}, headers=who("Sam"))
        after = await c.post("/_lab/reset", headers=who("Sam"))
        return took, other, nobody, mine, read, steal, info, release_other, forced, after

    took, other, nobody, mine, read, steal, info, release_other, forced, after = run(
        agent, scenario
    )
    assert took.status_code == 200 and took.json()["lease"]["holder"] == "Priya"
    assert other.status_code == 423 and other.json()["detail"]["code"] == "LAB_LEASED"
    assert "Priya has reserved this lab" in other.json()["detail"]["message"]
    assert "month-end run" in other.json()["detail"]["message"]
    assert nobody.status_code == 423
    assert mine.status_code == 200 and read.status_code == 200
    assert steal.status_code == 409
    assert info["lease"]["holder"] == "Priya"
    assert release_other.status_code == 423
    assert forced.status_code == 200 and forced.json()["released"]["holder"] == "Priya"
    assert after.status_code == 200


def test_an_expired_lease_holds_nothing(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, token=TOKEN)
    agent_app.write_json(tmp_path / agent_app.LEASE,
                         {"holder": "Old", "until": "2020-01-01T00:00:00.000Z"})  # fmt: skip

    async def scenario(c):
        await c.post("/_lab/data/load", json={"rows": rows("x")}, headers=H)
        return await c.post("/_lab/reset", headers=H), (await c.get("/_lab/lease", headers=H))

    reset, lease = run(agent, scenario)
    assert reset.status_code == 200 and lease.json() == {"lease": None}


def test_lease_limits(tmp_path):
    agent = make_agent(tmp_path, token=TOKEN)

    async def scenario(c):
        a = await c.post("/_lab/lease", json={"holder": "", "minutes": 10}, headers=H)
        b = await c.post("/_lab/lease", json={"holder": "A", "minutes": 9999}, headers=H)
        return a, b

    a, b = run(agent, scenario)
    assert a.status_code == 422 and b.status_code == 422


def test_a_quarantined_baseline_cannot_be_restored_or_reset_to(tmp_path):
    agent = make_agent(tmp_path, token=TOKEN)

    async def scenario(c):
        first = (await c.post("/_lab/data/load", json={"rows": rows("one")}, headers=H)).json()
        second = (await c.post("/_lab/data/load", json={"rows": rows("two")}, headers=H)).json()
        q1 = await c.post(f"/_lab/baselines/{first['id']}/quarantine",
                          json={"reason": "found unmasked names"}, headers=H)  # fmt: skip
        restore = await c.post(f"/_lab/baselines/{first['id']}/restore", headers=H)
        await c.post(
            f"/_lab/baselines/{second['id']}/quarantine", json={"reason": "bad"}, headers=H
        )
        reset = await c.post("/_lab/reset", headers=H)
        listed = (await c.get("/_lab/baselines", headers=H)).json()
        await c.post(
            f"/_lab/baselines/{second['id']}/quarantine", json={"release": True}, headers=H
        )
        reset_again = await c.post("/_lab/reset", headers=H)
        empty = await c.post(f"/_lab/baselines/{first['id']}/quarantine", json={}, headers=H)
        return q1, restore, reset, listed, reset_again, empty

    q1, restore, reset, listed, reset_again, empty = run(agent, scenario)
    assert q1.json()["quarantined"] == "found unmasked names"
    assert (
        restore.status_code == 409
        and restore.json()["detail"]["code"] == "LAB_BASELINE_QUARANTINED"
    )
    assert reset.status_code == 409
    assert {b["quarantined"] for b in listed} == {"found unmasked names", "bad"}
    assert reset_again.status_code == 200
    assert empty.status_code == 422
