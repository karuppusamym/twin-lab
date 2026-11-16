"""Baseline history: every baseline is kept, listed, restorable and pinnable; old ones pruned."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")
pytest.importorskip("psycopg")

from test_admin_api import TOKEN, make_agent, rows, run  # noqa: E402

from lab_agent import app as agent_app  # noqa: E402
from lab_agent.sessions import SessionStore  # noqa: E402

H = {"X-Lab-Token": TOKEN}


def test_loads_are_kept_and_an_earlier_one_restores(tmp_path):
    agent = make_agent(tmp_path, token=TOKEN)

    async def scenario(c):
        first = (
            await c.post(
                "/_lab/data/load", json={"rows": rows("one"), "pack": "month-end"}, headers=H
            )
        ).json()
        await c.post("/_lab/data/load", json={"rows": rows("two")}, headers=H)
        listed = (await c.get("/_lab/baselines", headers=H)).json()
        restored = await c.post(f"/_lab/baselines/{first['id']}/restore", headers=H)
        info = (await c.get("/_lab/info", headers=H)).json()
        reset = await c.post("/_lab/reset", headers=H)
        return first, listed, restored, info, reset

    first, listed, restored, info, reset = run(agent, scenario)
    assert [b["label"] for b in listed] == [None, None] and len(listed) == 2
    assert listed[0]["current"] and not listed[1]["current"]
    assert listed[1]["pack"] == "month-end" and listed[1]["counts"] == {"cases": 1}
    assert restored.status_code == 200
    assert agent.database.replaced[-2] == rows("one")  # restore put the first rows back
    assert info["baseline"]["id"] == first["id"] and info["baseline"]["pack"] == "month-end"
    assert reset.status_code == 200 and agent.database.replaced[-1] == rows("one")


def test_unknown_or_malformed_baseline_is_404(tmp_path):
    agent = make_agent(tmp_path, token=TOKEN)

    async def scenario(c):
        a = await c.post("/_lab/baselines/bl_20260101T000000Z_abcdef/restore", headers=H)
        b = await c.post("/_lab/baselines/..%2F..%2Fsecret/restore", headers=H)
        return a, b

    a, b = run(agent, scenario)
    assert a.status_code == 404 and a.json()["detail"]["code"] == "LAB_BASELINE_NOT_FOUND"
    assert b.status_code == 404


def test_old_unpinned_baselines_are_pruned_pinned_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_app, "KEEP_BASELINES", 3)
    agent = make_agent(tmp_path, token=TOKEN)
    ids = iter(f"bl_20260101T0000{n:02d}Z_aaaaaa" for n in range(10))

    def fake_save(rows_, label, pack, kind):
        meta = {"id": next(ids), "loaded_at": f"2026-01-01T00:00:{len(agent.database.replaced):02d}Z",
                "label": label, "pack": pack, "kind": kind, "tables": sorted(rows_)}  # fmt: skip
        agent_app.write_json(agent._history_path(meta["id"]), {"meta": meta, "rows": rows_})
        agent._make_current(meta, rows_)
        agent._prune_history()
        return meta

    monkeypatch.setattr(agent, "_save_baseline", fake_save)

    async def scenario(c):
        first = (await c.post("/_lab/data/load", json={"rows": rows("0")}, headers=H)).json()
        pin = await c.post(f"/_lab/baselines/{first['id']}/pin", json={"pinned": True}, headers=H)
        for n in range(1, 6):
            await c.post("/_lab/data/load", json={"rows": rows(str(n))}, headers=H)
        return pin, (await c.get("/_lab/baselines", headers=H)).json()

    pin, listed = run(agent, scenario)
    assert pin.status_code == 200
    kept = [b["id"] for b in listed]
    assert "bl_20260101T000000Z_aaaaaa" in kept  # pinned, so never pruned
    assert len(kept) == 1 + 3  # the pinned one plus the newest three
    assert [b for b in listed if b["id"] == "bl_20260101T000000Z_aaaaaa"][0]["pinned"] is True


def test_baseline_from_an_older_agent_enters_the_history(tmp_path):
    meta = {"id": "bl_20260101T000000Z_bbbbbb", "loaded_at": "2026-01-01T00:00:00Z",
            "label": "old", "pack": None, "kind": "load", "tables": ["cases"]}  # fmt: skip
    agent_app.write_json(tmp_path / agent_app.BASELINE_META, meta)
    agent_app.write_json(
        tmp_path / agent_app.BASELINE_ROWS, {"baseline_id": meta["id"], "rows": rows("old")}
    )
    agent = make_agent(tmp_path, token=TOKEN)

    async def scenario(c):
        return (await c.get("/_lab/baselines", headers=H)).json()

    listed = run(agent, scenario)
    assert [b["id"] for b in listed] == [meta["id"]] and listed[0]["current"]


def test_session_document_carries_the_changed_columns(tmp_path):
    store = SessionStore(tmp_path, "demo", "1")
    store.start({"cases": [{"id": 1, "status": "open", "note": "a"}]}, "bot", None, None, None)
    doc = store.stop({"cases": [{"id": 1, "status": "closed", "note": "a"}]}, {"cases": ["id"]})
    assert doc["changes"] == [
        {"app": "demo", "table": "cases", "op": "update", "key": "1", "columns": ["status"]}
    ]
