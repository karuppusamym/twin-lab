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
    assert "no outcome checks defined" in page
    assert "No database changes were recorded" in page  # the old prompt is back


def _browser_app(repo_root) -> None:
    path = repo_root / "apps" / "showcase" / "app.yaml"
    conf = yaml.safe_load(path.read_text(encoding="utf-8"))
    conf["stack"] = {"data": {"kind": "browser", "referenced": ["localStorage"]}}
    conf["lab"] = {"port": 8720, "credentials": {"password": "pw"}}
    path.write_text(yaml.safe_dump(conf), encoding="utf-8")


def _outcome_card(page: str) -> str:
    start = page.index('<div class="card" id="outcome">')
    return page[start : page.index("</div>", start)]


def test_a_browser_only_session_shows_its_business_outcome(repo, monkeypatch):  # noqa: F811
    setup_app(repo, monkeypatch)
    _browser_app(repo)
    page = TestClient(create_app(repo)).get(f"/apps/showcase/sessions/{SESSION}").text
    assert 'Database change check: <span class="pill">not applicable</span>' in page
    assert page.count('id="outcome"') == 1 and page.count("Business outcome:") == 1
    card = _outcome_card(page)
    assert "no outcome checks defined" not in card
    assert any(s in card for s in (">MET<", ">NOT MET<", ">CANNOT CHECK<")), card


def test_a_database_backed_session_shows_the_outcome_once_next_to_the_verdict(repo, monkeypatch):  # noqa: F811
    setup_app(repo, monkeypatch)
    page = TestClient(create_app(repo)).get(f"/apps/showcase/sessions/{SESSION}").text
    assert page.count('id="verdict"') == 1 and page.count('id="outcome"') == 1
    assert page.count("Business outcome:") == 1
    assert page.index('id="verdict"') < page.index('id="outcome"') < page.index("Rows changed")
    assert "no outcome checks defined" not in _outcome_card(page)


def test_a_browser_only_session_without_outcome_checks_says_so(repo, monkeypatch):  # noqa: F811
    setup_app(repo, monkeypatch)
    _browser_app(repo)
    path = repo / "automations/uipath-case-update/automation.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.pop("outcome")
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    page = TestClient(create_app(repo)).get(f"/apps/showcase/sessions/{SESSION}").text
    assert "no outcome checks defined" in _outcome_card(page)
