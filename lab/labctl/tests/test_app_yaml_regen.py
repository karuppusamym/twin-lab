"""An app.yaml generated again updates its generated parts (Demoblaze trial: `stack.data: browser`
of a first run stayed and BUILD_TASK.md contradicted the brief); `run:`, approved rule levels and
onboarding choices stay."""

from __future__ import annotations

from labctl import brief


def test_regenerated_app_yaml_keeps_only_what_people_set():
    old = {"id": "shop", "stack": {"data": {"kind": "browser"}, "db": {"engine": "sqlite"}},
           "run": {"web": 8080, "health": "/health"}, "lab": {"database": "pg-local"},
           "rules": [{"id": "R1", "verification": "owner_approved", "text": "old"},
                     {"id": "R2", "verification": "hypothesis"}]}  # fmt: skip
    new = {"id": "shop", "stack": {"db": {"engine": "sqlite"}}, "run": {},
           "rules": [{"id": "R1", "verification": "hypothesis", "text": "new"},
                     {"id": "R2", "verification": "hypothesis"},
                     {"id": "R3", "verification": "hypothesis"}]}  # fmt: skip
    got = brief.merge_app_yaml(old, new)
    assert got["stack"] == {"db": {"engine": "sqlite"}}  # no stale `data: browser`
    assert got["run"] == {"web": 8080, "health": "/health"}
    assert got["lab"] == {"database": "pg-local"}
    assert got["rules"] == [{"id": "R1", "verification": "owner_approved", "text": "new"},
                            {"id": "R2", "verification": "hypothesis"},
                            {"id": "R3", "verification": "hypothesis"}]  # fmt: skip
    assert brief.merge_app_yaml({}, new) == new
