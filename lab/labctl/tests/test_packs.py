"""Named data packs: saved locally, listed, loaded by name into the lab with the pack named."""

from __future__ import annotations

import pytest

from labctl import packs


class FakeAgent:
    def __init__(self):
        self.loaded = []

    def data(self):
        return {"cases": [{"id": 1}], "users": [{"id": 1}, {"id": 2}]}

    def info(self):
        return {"baseline": {"id": "bl_x"}}

    def load(self, rows, label, pack=None):
        self.loaded.append((rows, label, pack))
        return {"counts": {t: len(r) for t, r in rows.items()}}


@pytest.fixture
def agent(monkeypatch, tmp_path):
    fake = FakeAgent()
    monkeypatch.setattr(packs.lab_mod, "agent", lambda root, app_id: fake)
    monkeypatch.setattr(packs.lab_mod, "state_dir", lambda root: tmp_path / ".lab-state")
    return fake


def test_save_list_use_delete(tmp_path, agent):
    info = packs.save_from_lab(tmp_path, "qtt", "month-end", "Month end")
    assert info.counts == {"cases": 1, "users": 2} and info.from_baseline == "bl_x"
    assert [p.name for p in packs.listing(tmp_path, "qtt")] == ["month-end"]
    assert packs.listing(tmp_path, "showcase") == []
    reply = packs.load_into_lab(tmp_path, "qtt", "month-end")
    assert reply["counts"] == {"cases": 1, "users": 2}
    assert agent.loaded[0][1:] == ("Month end", "month-end")
    with pytest.raises(packs.PackError, match="exists already"):
        packs.save_from_lab(tmp_path, "qtt", "month-end")
    packs.save_from_lab(tmp_path, "qtt", "month-end", overwrite=True)
    packs.delete(tmp_path, "qtt", "month-end")
    assert packs.listing(tmp_path, "qtt") == []


@pytest.mark.parametrize("name", ["../x", "Month", "", "a/b", "x" * 64])
def test_bad_names_refused(tmp_path, agent, name):
    with pytest.raises(packs.PackError):
        packs.save_from_lab(tmp_path, "qtt", name)


def test_missing_pack(tmp_path, agent):
    with pytest.raises(packs.PackError, match="no pack"):
        packs.load_into_lab(tmp_path, "qtt", "nothing")
