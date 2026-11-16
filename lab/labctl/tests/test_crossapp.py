"""T-19: a pack across apps is refused when a linked key exists on one side only."""

from __future__ import annotations

import json

import pytest

from labctl import crossapp
from labctl import lab as lab_mod
from labctl.models import App


def app(app_id, links=()):
    return App.model_validate({"id": app_id, "name": app_id, "copies": {"system": app_id},
                               "stack": {}, "run": {"web": {"port": 8000}}, "links": list(links)})  # fmt: skip


EQUAL = {
    "entity": "customer",
    "column": "cases.cust_no",
    "to": "crm.customer.cust_no",
    "via": "equal",
}
APPS = {"cases": app("cases", [EQUAL]), "crm": app("crm")}


def pack(case_custs, crm_custs):
    return {
        "cases": {"cases": [{"id": n, "cust_no": c} for n, c in enumerate(case_custs)]},
        "crm": {"customer": [{"cust_no": c} for c in crm_custs]},
    }


def test_matching_keys_pass_and_one_sided_keys_are_named():
    assert crossapp.check(pack(["K1", "K2", "K2"], ["K1", "K2"]), APPS) == []
    problems = [str(p) for p in crossapp.check(pack(["K1", "K3"], ["K1", "K2"]), APPS)]
    assert problems == [
        "customer 'K3': in cases.cases.cust_no but not in crm.customer.cust_no",
        "customer 'K2': in crm.customer.cust_no but not in cases.cases.cust_no",
    ]


def test_a_pack_for_one_app_only_is_not_compared():
    assert crossapp.check({"cases": pack(["K9"], [])["cases"]}, APPS) == []


def test_crosswalk_links():
    link = {"entity": "customer", "column": "cases.cust_id", "to": "crm.customer.id",
            "via": "crosswalk", "table": "xref"}  # fmt: skip
    apps = {"cases": app("cases", [link]), "crm": app("crm")}
    good = {
        "cases": {"cases": [{"cust_id": 1}, {"cust_id": 2}],
                  "xref": [{"cust_id": 1, "id": "A"}, {"cust_id": 2, "id": "B"}]},
        "crm": {"customer": [{"id": "A"}, {"id": "B"}]},
    }  # fmt: skip
    assert crossapp.check(good, apps) == []
    bad = json.loads(json.dumps(good))
    bad["cases"]["cases"].append({"cust_id": 3})  # no partner
    bad["crm"]["customer"] = [{"id": "A"}, {"id": "C"}]  # B gone, C has no partner
    text = [str(p) for p in crossapp.check(bad, apps)]
    assert "customer '3': in cases.cases.cust_id but not in the crosswalk cases.xref" in text
    assert "customer 'B': in the crosswalk cases.xref but not in crm.customer.id" in text
    assert "customer 'C': in crm.customer.id but not in the crosswalk cases.xref" in text


class FakeAgent:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.loaded, self.restored = name, fail, [], []

    def info(self):
        return {"baseline": {"id": f"bl_{self.name}"}}

    def load(self, rows, label):
        if self.fail:
            raise lab_mod.LabError("table customer: 1 problem")
        self.loaded.append(label)
        return {"counts": {t: len(r) for t, r in rows.items()}}

    def restore_baseline(self, bid):
        self.restored.append(bid)


def test_all_apps_or_none(monkeypatch, tmp_path):
    agents = {"cases": FakeAgent("cases"), "crm": FakeAgent("crm", fail=True)}
    monkeypatch.setattr(crossapp, "load_app", lambda root, a: APPS[a])
    monkeypatch.setattr(lab_mod, "agent", lambda root, a, host=None: agents[a])
    with pytest.raises(crossapp.CrossAppError, match="Put back the previous data of: cases"):
        crossapp.load(tmp_path, pack(["K1"], ["K1"]), "x")
    assert agents["cases"].restored == ["bl_cases"]
    with pytest.raises(crossapp.CrossAppError, match="nothing was loaded"):
        crossapp.load(tmp_path, pack(["K1"], ["K2"]), "x")


def test_read_pack_shape(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"apps": pack(["K1"], ["K1"])}), encoding="utf-8")
    assert set(crossapp.read_pack(p)) == {"cases", "crm"}
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(crossapp.CrossAppError):
        crossapp.read_pack(p)
