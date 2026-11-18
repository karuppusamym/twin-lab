"""Roles and session lifecycle, recorded and replayed on a local fixture (the audit listed both
as never exercised).

The fixture (fixtures/roles/roles_app.py) has a sign-in form, a clerk and a supervisor, a page
and a menu entry for the supervisor only (a clerk gets a refusal page with HTTP 403), a cookie
session that can end, and sign-out. Each test records a short scripted session against one
instance and replays it against a fresh instance: the same code (a correct replica), or the same
code wrong in one named way (`roles_app.FAULTS`).

What these tests prove, and what they do not:

- A role is who signs in: the replay types the user name and password the bindings give, so a
  recording replays as the role its bindings name. Passwords here are synthetic; no file of a
  recording holds a typed user name or password.
- The comparison is of DOM structure (and of the address a step arrives at), plus the declared
  acceptance cases. The HTTP status of a page is NOT compared: a replica answering the refusal
  page with 200 instead of 403 would pass.
- A session that ends because of what the recorded steps do (here: a lifetime counted in pages)
  replays. One that ends because time passed, or from outside the browser, does not: nothing in
  a recording says so, and a replay cannot end the replica's session between two steps
  (`test_expiry_between_steps_from_outside_is_not_replayed`, a strict xfail).
- Single sign-on, multi-factor sign-in and token renewal are not covered at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "fixtures" / "roles"))

import roles_app as app  # noqa: E402

pytest.importorskip("playwright.sync_api")

from labctl import recorder  # noqa: E402
from labctl.behaviour import fidelity  # noqa: E402

CLERK, SUPERVISOR = "clerk.avery", "supervisor.blake"
PAUSE = 600  # longer than the recorder's settle time: every page is snapshotted before the next


def steps_of(rec: Path) -> list[dict]:
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def seq_of(rec: Path, action: str, part: str, nth: int = 0) -> int:
    hits = [s["seq"] for s in steps_of(rec) if s["action"] == action
            and (s.get("selector") or s.get("url") or "").endswith(part)]  # fmt: skip
    return hits[nth]


def sign_in(page, user: str) -> None:
    page.fill("#username", user)
    page.fill("#password", app.USERS[user]["password"])
    page.click("#signIn")
    page.wait_for_url("**/home")
    page.wait_for_timeout(PAUSE)


def follow(page, selector: str, address: str) -> None:
    page.click(selector)
    page.wait_for_url(address)
    page.wait_for_timeout(PAUSE)


def record(source, folder: Path, script, user: str) -> Path:
    """Record `script` from the sign-in page, then do what a person does before a replay: give
    each typing step its synthetic value in bindings.yaml (user name, password, in order)."""
    recorder.record(f"{source.base}/signin", folder, headless=True, script=script)
    typed = [s for s in steps_of(folder) if s["action"] == "type"]
    assert [s["selector"] for s in typed[:2]] == ["input#username", "input#password"]
    bind(folder, user)
    return folder


def bind(rec: Path, user: str) -> None:
    typed = [s["seq"] for s in steps_of(rec) if s["action"] == "type"]
    values = [user, app.USERS[user]["password"]] * (len(typed) // 2)
    steps = {seq: {"value": v} for seq, v in zip(typed, values, strict=True)}
    (rec / "bindings.yaml").write_text(yaml.safe_dump({"version": 1, "steps": steps}), "utf-8")


def declare(rec: Path, scope: str, cases: list[dict]) -> None:
    """The acceptance cases, declared from the fixture's rules (never from a replica)."""
    (rec / "acceptance.yaml").write_text(
        yaml.safe_dump({"version": 1, "scope": [scope], "cases": cases}), "utf-8"
    )


def replay(rec: Path, source, faults=(), session_views=None):
    replica = app.serve(faults=faults, session_views=session_views)
    try:
        report = fidelity.replay(rec, origins={source.base: replica.base}, resolve_timeout_ms=3000)
    finally:
        replica.stop()
    return report, replica


def assert_clean_pass(report) -> None:
    diffs = [d.text() for s in report.steps for d in s.differences]
    assert report.status == "pass" and diffs == [], report.markdown()
    assert report.coverage.failed == 0 and report.coverage.skipped == 0, report.markdown()


def failed(report) -> list[int]:
    return [s.seq for s in report.steps if s.status == "fail"]


def step(report, seq: int):
    return next(s for s in report.steps if s.seq == seq)


# =================================================================================================
# Recorded once each
# =================================================================================================


@pytest.fixture(scope="module")
def source():
    run = app.serve()
    yield run
    run.stop()


@pytest.fixture(scope="module")
def supervisor_rec(source, tmp_path_factory):
    """The supervisor signs in, opens Approvals from the menu, signs out, and presses Back."""

    def flow(page):
        sign_in(page, SUPERVISOR)
        follow(page, "#navApprovals", "**/approvals")
        follow(page, "#signOut", "**/signin")
        page.go_back()
        page.wait_for_timeout(PAUSE)

    rec = record(source, tmp_path_factory.mktemp("roles") / "supervisor", flow, SUPERVISOR)
    at = seq_of(rec, "goto", "/approvals")
    declare(rec, "the supervisor's page", [
        {"id": "signed-in-as-supervisor", "after": at, "check": "text", "locator": "#who",
         "contains": "(supervisor)"},
        {"id": "approvals-listed", "after": at, "check": "count", "locator": "#pending tbody tr",
         "equals": len(app.PENDING)},
    ])  # fmt: skip
    return rec


@pytest.fixture(scope="module")
def clerk_rec(source, tmp_path_factory):
    """The clerk signs in, opens Orders, types the address of the supervisor's page (no menu
    entry leads there), is refused, and goes back to Home by the menu."""

    def flow(page):
        sign_in(page, CLERK)
        follow(page, "#navOrders", "**/orders")
        page.goto(f"{source.base}/approvals")
        page.wait_for_timeout(PAUSE)
        follow(page, "#navHome", "**/home")

    rec = record(source, tmp_path_factory.mktemp("roles") / "clerk", flow, CLERK)
    declare(rec, "what a clerk may see", [
        {"id": "no-approvals-entry-for-a-clerk", "after": seq_of(rec, "goto", "/home"),
         "check": "count", "locator": "#navApprovals", "equals": 0},
        {"id": "clerk-is-refused", "after": seq_of(rec, "goto", "/approvals"), "check": "text",
         "locator": "#refused", "equals": "Not allowed"},
    ])  # fmt: skip
    return rec


EXPIRY_VIEWS = 2  # Home and Orders; the third protected page is refused


@pytest.fixture(scope="module")
def expiring_source():
    run = app.serve(session_views=EXPIRY_VIEWS)
    yield run
    run.stop()


@pytest.fixture(scope="module")
def expiry_rec(expiring_source, tmp_path_factory):
    """The clerk signs in, opens Orders, and on the next page the session has ended: sent to
    sign-in with the notice, the clerk signs in again and is back at Home."""

    def flow(page):
        sign_in(page, CLERK)
        follow(page, "#navOrders", "**/orders")
        follow(page, "#navHome", "**/signin?expired=1")
        sign_in(page, CLERK)

    return record(expiring_source, tmp_path_factory.mktemp("roles") / "expiry", flow, CLERK)


# =================================================================================================
# a. the supervisor's recording, and what a recording keeps of a password
# =================================================================================================


def test_supervisor_recording_replays_against_a_correct_replica(supervisor_rec, source):
    report, replica = replay(supervisor_rec, source)
    assert_clean_pass(report)
    assert report.verdict == "PASS WITHIN DECLARED SCOPE", report.markdown()
    assert {c.id: c.status for c in report.cases} == {
        "signed-in-as-supervisor": "PASS", "approvals-listed": "PASS"}  # fmt: skip
    approvals = step(report, seq_of(supervisor_rec, "goto", "/approvals"))
    assert approvals.compared and approvals.target == (
        f"navigation caused by step {seq_of(supervisor_rec, 'click', 'a#navApprovals')}"
    )
    assert ("GET", "/approvals", 200) in replica.hits


def test_a_recording_as_another_role_is_the_bindings_choice(supervisor_rec, source, tmp_path):
    """The role is who signs in. The supervisor's recording replayed with the clerk's user name
    and password fails where the supervisor's menu entry is first missing, not later."""
    import shutil

    rec = tmp_path / "as-clerk"
    shutil.copytree(supervisor_rec, rec)
    bind(rec, CLERK)
    report, _ = replay(rec, source)
    home = seq_of(rec, "goto", "/home")
    assert report.status != "pass" and failed(report)[0] == home, report.markdown()
    assert any(d.check == "missing" and "navApprovals" in d.element
               for d in step(report, home).differences)  # fmt: skip


def test_no_recording_holds_a_typed_user_name_or_password(supervisor_rec, clerk_rec, expiry_rec):
    for rec in (supervisor_rec, clerk_rec, expiry_rec):
        typed = [s for s in steps_of(rec) if s["action"] == "type"]
        assert typed and all(not s.get("value") and not s.get("typed") for s in typed)
        written = [
            p
            for p in rec.rglob("*")
            if p.is_file() and p.suffix != ".png" and p.name != "bindings.yaml"
        ]  # the values a person supplies for the replay
        text = "".join(p.read_text("utf-8", "replace") for p in written)
        assert "recording.har" in {p.name for p in written} and len(text) > 1000
        for name, user in app.USERS.items():
            assert user["password"] not in text, (rec.name, name)
            assert name not in text, (rec.name, name)


def test_replay_blocks_without_the_password(clerk_rec, source, tmp_path):
    """No binding, no sign-in: the replay stops at the first typing step and says what to add."""
    import shutil

    rec = tmp_path / "unbound"
    shutil.copytree(clerk_rec, rec)
    (rec / "bindings.yaml").unlink()
    report, replica = replay(rec, source)
    first = next(s for s in report.steps if s.status == "blocked")
    assert report.status == "blocked" and first.outcome == "missing_value", report.markdown()
    assert first.seq == seq_of(rec, "type", "input#username")
    assert not any(method == "POST" for method, _, _ in replica.hits)


# =================================================================================================
# b. a replica that is wrong about roles is caught at the step concerned
# =================================================================================================


def test_a_menu_entry_shown_to_every_role_fails_where_the_menu_first_shows(clerk_rec, source):
    report, _ = replay(clerk_rec, source, faults={"menu_for_all"})
    home = seq_of(clerk_rec, "goto", "/home")  # the first page with the menu, after sign-in
    assert report.status == "fail" and failed(report)[0] == home, report.markdown()
    diffs = step(report, home).differences
    assert [(d.check, d.expected, d.actual) for d in diffs if "navApprovals" in d.element] == [
        ("extra", 0, 1)], report.markdown()  # fmt: skip
    assert all("navApprovals" in d.element for d in diffs)  # nothing vague beside it
    assert report.first_divergence is not None and report.first_divergence.step == home
    cases = {c.id: c.status for c in report.cases}
    assert cases["no-approvals-entry-for-a-clerk"] == "FAIL" and cases["clerk-is-refused"] == "PASS"
    assert report.verdict == "FAIL"


def test_a_clerk_let_into_the_supervisors_page_fails_at_that_page(clerk_rec, source):
    report, replica = replay(clerk_rec, source, faults={"no_role_check"})
    refused = seq_of(clerk_rec, "goto", "/approvals")
    assert report.status == "fail" and failed(report) == [refused], report.markdown()
    by_check = {(d.check, d.element.split(".")[0]) for d in step(report, refused).differences}
    assert {("visibility", "#refused"), ("missing", "a#backHome")} <= by_check
    assert ("extra", "button#approveAll") in by_check
    assert {c.id: c.status for c in report.cases}["clerk-is-refused"] != "PASS"
    assert ("GET", "/approvals", 200) in replica.hits  # the replica did let the clerk in


# =================================================================================================
# c. the refusal is a page like any other
# =================================================================================================


def test_the_clerks_refusal_replays_and_is_compared(clerk_rec, source):
    """The refusal page (HTTP 403 in the source) is snapshotted, replayed and compared by its
    structure, and its text by an acceptance case. The status code itself is not compared."""
    assert ("GET", "/approvals", 403) in source.hits
    refused = seq_of(clerk_rec, "goto", "/approvals")
    recorded = next(s for s in steps_of(clerk_rec) if s["seq"] == refused)
    assert 'id="refused"' in (clerk_rec / recorded["dom"]).read_text("utf-8")
    report, replica = replay(clerk_rec, source)
    assert_clean_pass(report)
    assert report.verdict == "PASS WITHIN DECLARED SCOPE", report.markdown()
    assert step(report, refused).compared and step(report, refused).outcome == "replayed"
    assert {c.status for c in report.cases} == {"PASS"} and len(report.cases) == 2
    assert ("GET", "/approvals", 403) in replica.hits


# =================================================================================================
# d. a session that ends
# =================================================================================================


def test_session_expiry_and_signing_in_again_replays(expiry_rec, expiring_source):
    sent_back = seq_of(expiry_rec, "goto", "/signin?expired=1")
    recorded = {s["seq"]: s for s in steps_of(expiry_rec)}[sent_back]
    assert recorded["caused_by"] == seq_of(expiry_rec, "click", "a#navHome")
    assert 'id="expiredNotice"' in (expiry_rec / recorded["dom"]).read_text("utf-8")
    report, replica = replay(expiry_rec, expiring_source, session_views=EXPIRY_VIEWS)
    assert_clean_pass(report)
    assert step(report, sent_back).compared
    assert [h for h in replica.hits if h[0] == "POST"] == [("POST", "/signin", 303)] * 2
    assert ("GET", "/home", 302) in replica.hits  # the replica ended the session by itself


def test_a_replica_whose_session_never_ends_is_caught(expiry_rec, expiring_source):
    """The step concerned is the page the click should have been refused: the replica stays at
    Home instead of going to sign-in. The next step (typing the user name on a sign-in form that
    is not there) cannot run, so the replay stops there, BLOCKED, with the failure before it."""
    report, _ = replay(expiry_rec, expiring_source, faults={"no_expiry"},
                       session_views=EXPIRY_VIEWS)  # fmt: skip
    sent_back = seq_of(expiry_rec, "goto", "/signin?expired=1")
    assert failed(report) == [sent_back], report.markdown()
    diff = step(report, sent_back).differences[0]
    assert diff.check == "url" and "/signin?expired=1" in diff.actual and "/home" in diff.actual
    assert report.first_divergence is not None and report.first_divergence.step == sent_back
    after = step(report, seq_of(expiry_rec, "type", "input#username", 1))
    assert after.status == "blocked" and after.outcome == "unresolved"
    assert report.status != "pass"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "a session ended by elapsed time, or from outside the browser, between two steps is not in "
    "the recording: the replay cannot end the replica's session at that moment, so the replica "
    "is not sent to sign-in and the replay fails at that step even for a correct replica"))  # fmt: skip
def test_expiry_between_steps_from_outside_is_not_replayed(tmp_path):
    source = app.serve()
    try:

        def flow(page):
            sign_in(page, CLERK)
            source.expire_sessions()  # an idle timeout, as the browser sees it
            follow(page, "#navOrders", "**/signin?expired=1")
            sign_in(page, CLERK)

        rec = record(source, tmp_path / "timed-out", flow, CLERK)
        assert seq_of(rec, "goto", "/signin?expired=1")  # recorded: the redirect and the notice
        report, _ = replay(rec, source)
    finally:
        source.stop()
    assert report.status == "pass", report.problems


# =================================================================================================
# e. sign-out, then Back
# =================================================================================================


def test_back_after_sign_out_is_made_as_back_and_a_replica_showing_the_page_is_caught(
    supervisor_rec, source
):
    """After sign-out the supervisor presses Back. The browser asks for the protected page again
    and the source answers with sign-in, so the recorder writes a history step whose address is
    sign-in, not the page gone back to. The replay used to load that address (no entry of its
    history matched it), which passed any replica; it now goes Back, as recorded, and the replica
    must arrive at sign-in by itself. A replica whose sign-out leaves the session alive shows the
    protected page again and fails at that step.

    What the comparison sees is the address and the structure after Back. A page shown from the
    browser's cache for a moment before a redirect is not seen."""
    last = steps_of(supervisor_rec)[-1]
    assert (last["action"], last["nav"]) == ("goto", "back_forward")
    assert last["url"].endswith("/signin") and last.get("caused_by") is None
    assert ("GET", "/approvals", 302) in source.hits  # what Back asked for, and the answer
    assert 'id="signinForm"' in (supervisor_rec / last["dom"]).read_text("utf-8")

    report, replica = replay(supervisor_rec, source)
    assert_clean_pass(report)
    assert step(report, last["seq"]).target == "history back"
    assert replica.hits[-2:] == [("GET", "/approvals", 302), ("GET", "/signin", 200)]

    report, replica = replay(supervisor_rec, source, faults={"signout_keeps_session"})
    assert report.status == "fail" and failed(report) == [last["seq"]], report.markdown()
    diff = step(report, last["seq"]).differences[0]
    assert diff.check == "url" and "/signin, the page is at" in diff.actual
    assert diff.actual.endswith("/approvals")
    assert replica.hits[-1] == ("GET", "/approvals", 200)  # the protected page, after sign-out
