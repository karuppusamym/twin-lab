"""JSF with PrimeFaces, on a real application: Caseboard PF (reference/web/caseboard-primefaces:
Mojarra 2.3 and PrimeFaces 10 on embedded Jetty).

The scripted session (its flow.py) is recorded with the lab's recorder, taken through
`labctl evidence build-all`, and replayed against a fresh instance. What office JSF applications
do and these tests go through: navigation by postback and forward (the address bar one step
behind), four sub-pages swapped by ajax inside one address, a tab view and an accordion whose
panels load on first open, a wizard, a dialog, a confirm dialog, a dialog-framework page in an
iframe (slow to answer), a lazy data table (paging, sorting, filtering, row expansion and
selection) and a remote command.

Skipped where the application cannot be built: without a JDK, or without the jars
(`python reference/web/caseboard-primefaces/fetch_libs.py`; they are not committed, so CI skips).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "reference" / "web" / "caseboard-primefaces"


def _load(name: str):
    full = f"caseboard_pf_{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, APP / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
    return sys.modules[full]


build = _load("build")  # standard library only
REASON = build.unavailable()
pytestmark = pytest.mark.skipif(
    REASON is not None, reason=f"the PrimeFaces reference application cannot run here: {REASON}"
)

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def gates(tmp_path_factory):
    """Recorded once, packaged, and replayed against a fresh instance (certify_local.run_gates)."""
    return _load("certify_local").run_gates(tmp_path_factory.mktemp("caseboard-pf"))


def test_each_view_is_its_own_screen_and_each_forward_a_transition(gates):
    p = gates["package"]
    assert p["errors"] == [], p["errors"]
    assert p["screens"] == ["/case.xhtml", "/login.xhtml", "/picker.xhtml", "/workspace.xhtml"]
    # the address lagged after each forward; the screens follow the view all the same
    assert [(x["address"], x["view"]) for x in p["address_lag"]] == [
        ("/login.xhtml", "/workspace.xhtml"), ("/workspace.xhtml", "/case.xhtml"),
        ("/case.xhtml", "/workspace.xhtml")]  # fmt: skip
    certify = _load("certify_local")
    for move in certify.FORWARDS:
        assert move in p["transitions"], p["transitions"]
    nav = p["navigation"]
    signin = next(t for t in nav["transitions"] if t["from"] == "/login.xhtml")
    assert signin["selector"] == "button#loginForm:signIn" and signin["forward"] is True
    # the login screen is the sign-in form only, not the workspace shown at its address
    login = next(s for s in nav["screens"] if s["path"] == "/login.xhtml")
    assert {e["selector"] for e in login["elements"]} == {
        "input#loginForm:username", "input#loginForm:password", "button#loginForm:signIn"}  # fmt: skip


def test_every_state_inside_the_workspace_has_its_opener_snapshot_and_section(gates):
    p = gates["package"]
    certify = _load("certify_local")
    for kind, n in certify.EXPECTED_KINDS.items():
        assert p["kinds"].get(kind, 0) >= n, (kind, p["kinds"])
    assert p["without_opener"] == [] and p["without_snapshot"] == []
    assert p["without_section"] == []
    assert p["snapshots"] == 4 + p["states"] and p["page_documents"] == 4
    work = next(s for s in p["navigation"]["screens"] if s["path"] == "/workspace.xhtml")
    states = [v for v in work["subviews"] if v.get("state_of")]
    by = {(v["kind"], v["selector"]): v for v in states}
    picker = by[("dialog-framework page (iframe)", "div#main:pickOwner_dlg")]
    assert picker["shows"] == "/picker.xhtml"
    assert picker["opened_by"][0]["selector"] == "button#main\\:pickOwner"
    assert picker["closed_by"][0]["selector"].startswith("iframe >>> button#pick")
    new_case = next(v for v in states if v["kind"] == "sub-page (ajax region)"
                    and v["opened_by"] and v["opened_by"][0]["selector"] == "a#main\\:navNew")  # fmt: skip
    assert new_case["rerendered"] == ["main:content"] and "main:wiz" in new_case["components"]
    # the element that opened a state says so, with what its partial response re-rendered
    nav_new = next(e for e in work["elements"] if e["selector"] == "a#main:navNew")
    use = nav_new["used"][0]
    assert any(x.startswith("sub-page (ajax region)") for x in use["opens"])
    assert use["jsf_ajax"][0]["source"] == "main:navNew"
    assert use["jsf_ajax"][0]["updates"] == ["main:content"]
    actions = p["navigation"]["jsf"]["partial_actions"]
    assert any(a.get("event") == "dialogReturn" for a in actions)
    assert any("PrimeFaces.openDialog" in str(a.get("evals")) for a in actions)
    assert p["widgets"] >= 10  # DataTable, Dialog, Wizard, TabView, AccordionPanel, ...


def test_typing_report_leaves_the_reports_menu_item_its_id(gates):
    """The flow types "report" into the table's filter. A typed value used to be taken out of
    every attribute as a substring, so `main:navReports` became `main:navAs` and the Reports
    sub-page lost its opener's selector; a typed value is now taken out only where it is a
    value (labctl.typedvalues)."""
    assert _load("flow").FILTER == "report"
    p = gates["package"]
    work = next(s for s in p["navigation"]["screens"] if s["path"] == "/workspace.xhtml")
    assert "a#main:navReports" in {e["selector"] for e in work["elements"]}
    openers = {v["opened_by"][0]["selector"] for v in work["subviews"]
               if v.get("state_of") and v["opened_by"]}  # fmt: skip
    assert "a#main\\:navReports" in openers
    assert "navAs" not in json.dumps(p["navigation"])


def test_the_recording_replays_on_a_fresh_instance_with_no_failed_step(gates):
    r = gates["replay"]
    assert r["failed"] == 0 and r["skipped"] == 0 and r["blocked"] == 0, r["first_failing_step"]
    assert r["replayed"] == r["steps"] and r["status"] == "pass", r
    assert gates["ok"]
