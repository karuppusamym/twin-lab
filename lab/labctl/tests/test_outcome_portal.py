"""The portal session page shows the business outcome apart from the database change check."""

from __future__ import annotations

import yaml
from fastapi.testclient import TestClient
from test_portal import repo  # noqa: F401 - the fixture
from test_portal_round4 import SESSION, FakeAgent
from test_portal_round4 import setup as setup_app

from labctl.portal.app import create_app


class QuietAgent(FakeAgent):
    """A session that changed nothing."""

    def session(self, sid):
        return {**super().session(sid), "changes": []}


def test_outcome_line_and_zero_change_prompt(repo, monkeypatch):  # noqa: F811
    fake = setup_app(repo, monkeypatch)
    quiet = QuietAgent()
    quiet.__dict__.update({k: v for k, v in fake.__dict__.items()})
    from labctl import lab as lab_mod

    monkeypatch.setattr(lab_mod, "agent", lambda root, app_id, host=None: quiet)
    client = TestClient(create_app(repo))
    url = f"/apps/showcase/sessions/{SESSION}"

    page = client.get(url).text  # the reference automation declares an outcome
    assert "Business outcome:" in page and "NOT MET" in page
    assert "expected exactly 1 update of cases" in page
    assert "No database changes were recorded" not in page  # replaced by the outcome result
    assert "Database change check" in page and ">PASS<" in page  # the verdict is unchanged

    path = repo / "automations/uipath-case-update/automation.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.pop("outcome")
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    page = client.get(url).text
    assert "no outcome declared" in page
    assert "No database changes were recorded" in page  # the old prompt is back
