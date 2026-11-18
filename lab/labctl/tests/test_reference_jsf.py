"""The certification gates for JSF, on a real JSF application: Caseboard
(reference/web/caseboard-jsf: Mojarra 2.3 on embedded Jetty, Facelets pages, server-side state).

The application is started twice. The scripted session (its flow.py) is recorded on the first
instance with the lab's recorder; then:

- DETECT: the recording's analysis names JSF, the implementation (Mojarra) and the
  specification version (2.3), from what the pages and JSF's own script show;
- CAPTURE: every action of expected-actions.yaml (written from the flow) is in the recording;
- SELF-REPLAY: the recording replays against the second, fresh instance with no failed step,
  no difference, and every acceptance case (declared from the application's rules) passing.

What JSF brings and these tests go through: ids with colons (`list:table:1:open`), a generated
form id (`j_idtN`), a hidden `javax.faces.ViewState` that differs on every page and run,
`;jsessionid=` in the first form's address, `javax.faces.resource` addresses, an ajax partial
update, POST then redirect, and a command link that submits its form from script.

Skipped where the application cannot be built: without a JDK, or without the jars
(`python reference/web/caseboard-jsf/fetch_libs.py`; they are not committed, so CI skips).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "reference" / "web" / "caseboard-jsf"


def _load(name: str):
    full = f"caseboard_jsf_{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, APP / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
    return sys.modules[full]


build = _load("build")  # standard library only
REASON = build.unavailable()
pytestmark = pytest.mark.skipif(
    REASON is not None, reason=f"the JSF reference application cannot run here: {REASON}"
)

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def gates(tmp_path_factory):
    """Recorded once, analysed and replayed against a fresh instance (certify_local.run_gates)."""
    return _load("certify_local").run_gates(tmp_path_factory.mktemp("caseboard-jsf"))


def steps_of(rec: Path) -> list[dict]:
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def test_detect_names_jsf_its_implementation_and_version(gates):
    found = gates["detect"]
    assert found["ok"], found
    assert (found["framework"], found["rendering"]) == ("JSF", "server")
    assert (found["server_technology"], found["server_framework"]) == ("Java", "JSF")
    assert found["server_confidence"] == "certain"
    assert (found["implementation"], found["version"]) == ("Mojarra", "2.3")
    assert found["web_server"] == "Jetty"
    assert {"javax.faces.ViewState field", "javax.faces.resource addresses",
            ";jsessionid in an address", "JSESSIONID cookie"} <= set(found["markers"])  # fmt: skip


def test_capture_holds_every_expected_action(gates):
    captured = gates["capture"]
    assert captured["expected"] == 24 and captured["missing"] == [], captured
    rec = Path(gates["recording"])
    steps = steps_of(rec)
    # A command link is one click by the person: the submit button Mojarra's script adds to the
    # form and clicks is not a step of its own.
    assert [s["selector"] for s in steps if s["action"] == "click"] == [
        "input#loginForm\\:signIn", "input#loginForm\\:signIn", "a#list\\:next", "a#list\\:next",
        "a#list\\:previous", "input#search\\:go", "a#list\\:table\\:1\\:open",
        "input#edit\\:save", "input#edit\\:save", "input#edit\\:save", "a#backToList",
        next(s["selector"] for s in steps if (s.get("selector") or "").endswith("\\:signOut")),
    ]  # fmt: skip
    assert steps[-2]["selector"].startswith("form#j_idt")  # the form JSF named by itself
    # the ajax update is in the snapshot of the step that caused it
    chosen = next(s for s in steps if s["action"] == "select")
    assert 'id="edit:resolution"' in (rec / chosen["dom"]).read_text("utf-8")
    assert 'id="edit:resolution"' not in (rec / chosen["dom_before"]).read_text("utf-8")
    # nothing typed or chosen is in any file of the recording but the bindings a person writes
    flow = _load("flow")
    text = "".join(p.read_text("utf-8", "replace") for p in rec.rglob("*") if p.is_file()
                   and p.suffix != ".png" and p.name not in ("bindings.yaml", "acceptance.yaml"))  # fmt: skip
    for value in (flow.USER, flow.WRONG_PASSWORD, flow.SEARCH, flow.NEW_TITLE, flow.RESOLUTION):
        assert value not in text, value


def test_self_replay_on_a_fresh_instance_has_no_failed_step(gates):
    replayed = gates["replay"]
    assert replayed["ok"], replayed
    assert replayed["status"] == "pass" and replayed["verdict"] == "PASS WITHIN DECLARED SCOPE"
    assert replayed["failed"] == replayed["skipped"] == replayed["blocked"] == 0
    assert replayed["steps"] == replayed["replayed"] == 48 and replayed["differences"] == []
    assert len(replayed["cases"]) == 23 and set(replayed["cases"].values()) == {"PASS"}
    assert replayed["blocked_requests"] == []
    assert gates["ok"]


def test_the_replay_notices_an_instance_that_answers_differently(gates, tmp_path):
    """The gate is not a formality: the same recording, replayed with a wrong password where the
    session signed in, fails at the page that sign-in should have led to."""
    certify, run = _load("certify_local"), _load("run")
    rec = tmp_path / "wrong-password"
    shutil.copytree(gates["recording"], rec)
    bindings = yaml.safe_load((rec / "bindings.yaml").read_text("utf-8"))
    typed = [s["seq"] for s in steps_of(rec) if s["selector"] == "input#loginForm\\:password"]
    bindings["steps"][typed[1]]["value"] = "still-not-the-password"
    (rec / "bindings.yaml").write_text(yaml.safe_dump(bindings, sort_keys=False), "utf-8")
    source = "http://" + steps_of(rec)[1]["url"].split("/")[2]
    with run.serve() as fresh:
        report = certify.replay(rec, source, fresh.base)
    signed_in = next(s["seq"] for s in steps_of(rec) if s["url"].endswith("/cases.xhtml"))
    failing = next(s for s in report.steps if s.status == "fail")
    assert report.status != "pass" and failing.seq == signed_in, report.markdown()
    assert failing.differences[0].check == "url"
