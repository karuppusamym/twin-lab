"""R10: declarations, replay, report, coverage and pictures in the portal (no Docker, no browser).

The replay function is a stand-in with the real one's keywords; everything else is the portal's
own code, the behaviour modules' own loaders and template functions.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_coverage_matrix import (  # noqa: F401
    APP,
    REC,
    _no_docker,
    keep_report,
    make_world,
    write_cases,
)
from test_portal import wait_job

from labctl import conformance, factory
from labctl import coverage as cov
from labctl import lab as lab_mod
from labctl.behaviour import acceptance as acc
from labctl.behaviour import durable, fidelity
from labctl.behaviour import judging as judge
from labctl.botlog.recording import load as load_recording
from labctl.portal import guide
from labctl.portal import replication as rep
from labctl.portal.app import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
BASE = f"/apps/{APP}/recordings/{REC}"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("LABCTL_ROOT", str(tmp_path / "repo"))
    monkeypatch.setattr(conformance, "image_id", lambda app_id: None)
    return make_world(tmp_path)


@pytest.fixture
def client(world):
    return TestClient(create_app(world))


def up(monkeypatch, running=True):
    monkeypatch.setattr(lab_mod, "status",
                        lambda root, app_id: lab_mod.Status(app_id, running, {}, {}))  # fmt: skip


def rec_file(world: Path, name: str) -> Path:
    return world / "evidence" / APP / REC / name


def post(client, url, **data):
    return client.post(url, data=data, follow_redirects=False)


def flash(r) -> str:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(r.headers["location"]).query).get("msg", [""])[0]


# --- pages and files -------------------------------------------------------------------------------


def test_the_declarations_page_lists_all_five_files_and_the_separation(client):
    r = client.get(BASE)
    assert r.status_code == 200
    for kind in rep.KINDS:
        assert f'id="{kind}"' in r.text
    for name in ("bindings.yaml", "baseline.yaml", "acceptance.yaml", "scope.yaml", "visual.yaml"):
        assert name in r.text
    assert 'id="separation"' in r.text and "Git ignores" in r.text
    assert "tools/guard_ai_changes.py" in r.text and "does not look at evidence/" in r.text
    assert "no pull request carries" in r.text
    assert "step 2: no value for the step that must type into #search" in r.text  # blocks a run


def test_each_file_is_saved_only_when_its_own_loader_accepts_it(world, client):
    ok = {
        "bindings": "version: 1\nsteps:\n  2:\n    value: Water the plants\n",
        "baseline": "version: 1\nlocalStorage:\n  todos: [{id: 1, title: Syn alpha}]\n",
        "acceptance": "version: 1\ncases:\n  - id: c1\n    check: count\n    locator: li\n    equals: 2\n",
        "scope": "version: 1\nexcluded:\n  - id: banner\n    reason: not part of the replica\n",
        "visual": "version: 1\nviewport: desktop\nstates:\n  - id: s\n    after: 2\n    mode: exact\n",
    }
    for kind, text in ok.items():
        r = post(client, f"{BASE}/files/{kind}", text=text)
        assert r.status_code == 303 and "saved and valid" in flash(r), (kind, flash(r))
        assert rec_file(world, rep.FILES[kind][0]).read_text(encoding="utf-8") == text
    for kind in rep.KINDS:
        rec_file(world, rep.FILES[kind][0]).unlink()
    bad = {
        "bindings": "steps: [1, 2",  # not YAML
        "baseline": "- not\n- a mapping\n",
        "acceptance": "cases: 3\n",
        "scope": "excluded: not-a-list\n",
        "visual": "states: {}\nviewport: [x\n",
    }
    for kind, text in bad.items():
        r = post(client, f"{BASE}/files/{kind}", text=text)
        assert "was not saved" in flash(r), (kind, flash(r))
        assert not rec_file(world, rep.FILES[kind][0]).exists()


def test_a_saved_file_with_problems_is_marked_as_blocking(world, client):
    text = ("version: 1\ncases:\n  - id: todo-case\n    check: text\n    locator: h1\n"
            "    equals: TODO\n  - id: typo\n    check: nonsense\n")  # fmt: skip
    r = post(client, f"{BASE}/files/acceptance", text=text)
    assert "saved, but it blocks a run" in flash(r)
    assert rec_file(world, "acceptance.yaml").exists()  # work in progress is kept
    page = client.get(BASE).text
    assert "case todo-case: not completed: a value is still TODO" in page
    assert "case typo: unknown check" in page
    assert "line 6" in page or "line 5" in page  # the TODO line is named
    d = rep.read_declaration(world, rec_file(world, ""), "acceptance")
    assert d["state"] == "blocks" and d["todo"]
    # a baseline that sends a source credential is refused by the loader's own rule
    post(client, f"{BASE}/files/baseline",
         text="cookies:\n  - {name: session_id, value: abc}\n")  # fmt: skip
    assert "looks like a session or credential" in client.get(BASE).text


def test_bindings_block_until_every_typed_step_has_a_value(world, client):
    blocked = rep.blockers(world, rec_file(world, ""))
    assert any(b.startswith("bindings.yaml: step 2") for b in blocked)
    post(
        client,
        f"{BASE}/files/bindings",
        text="version: 1\nsteps:\n  2:\n    value: Synthetic one\n",
    )
    assert not [b for b in rep.blockers(world, rec_file(world, "")) if "bindings" in b]
    post(client, f"{BASE}/files/bindings", text="version: 1\nsteps:\n  2:\n    value: TODO\n")
    assert any("still TODO" in b for b in rep.blockers(world, rec_file(world, "")))


def test_template_buttons_call_the_behaviour_modules_and_never_overwrite(world, client):
    for kind in ("bindings", "acceptance", "baseline", "visual", "scope"):
        r = post(client, f"{BASE}/files/{kind}/template")
        assert "Template written" in flash(r), (kind, flash(r))
        assert rec_file(world, rep.FILES[kind][0]).is_file()
    assert "version: 1" in rec_file(world, "acceptance.yaml").read_text(encoding="utf-8")
    assert "TODO" in rec_file(world, "scope.yaml").read_text(encoding="utf-8")
    again = post(client, f"{BASE}/files/acceptance/template")
    assert "is not overwritten" in flash(again)
    # the bindings template completes the file and keeps what was given
    rec_file(world, "bindings.yaml").write_text(
        "version: 1\nsteps:\n  2:\n    action: type\n    value: Kept value\n", encoding="utf-8"
    )
    post(client, f"{BASE}/files/bindings/template")
    assert "Kept value" in rec_file(world, "bindings.yaml").read_text(encoding="utf-8")
    # a TODO in a fresh template is marked, and blocks a run
    page = client.get(BASE).text
    assert "TODO" in page and "still in this file at line" in page


def test_judging_files_are_never_written_under_apps(world, client, monkeypatch):
    sneaky = world / "apps" / APP / "rec2"
    sneaky.mkdir()
    (sneaky / "steps.jsonl").write_text("", encoding="utf-8")
    (sneaky / "recording.har").write_text("{}", encoding="utf-8")
    real = factory.discover

    def discover(root, app_id):
        found = real(root, app_id)
        found.recordings.append(sneaky)
        return found

    monkeypatch.setattr(factory, "discover", discover)
    r = post(client, f"/apps/{APP}/recordings/rec2/files/acceptance", text="version: 1\n")
    assert "Refused" in flash(r) and "never written under apps/" in flash(r)
    t = post(client, f"/apps/{APP}/recordings/rec2/files/visual/template")
    assert "Refused" in flash(t)
    assert not list((world / "apps").rglob("acceptance.yaml"))
    assert not list((world / "apps").rglob("visual.yaml"))
    with pytest.raises(rep.Refused):
        rep.file_path(world, sneaky, "acceptance")
    with pytest.raises(rep.Refused):
        rep.file_path(world, world / "elsewhere" / "rec", "baseline")  # not under evidence/
    with pytest.raises(rep.Refused):
        rep.file_path(world, sneaky, "../apps/x")  # not a declaration kind
    assert rep.file_path(world, rec_file(world, ""), "scope").name == "scope.yaml"


def test_an_unknown_recording_or_file_kind_is_404(client):
    assert client.get(f"/apps/{APP}/recordings/nope").status_code == 404
    assert post(client, f"{BASE}/files/passwords", text="x").status_code == 404
    assert client.get(f"/apps/{APP}/recordings/Bad_Name").status_code in (400, 404)


# --- replay as a job ---------------------------------------------------------------------------------


def fake_report(world: Path, folder: Path | None, verdict: str, *, pictures=True):
    rec_path = world / "evidence" / APP / REC
    rec = load_recording(rec_path)
    used = {n: rec_path / f"{n}.yaml" for n in durable.FILES}
    fp = durable.for_run(
        rec, used, {"name": "chromium", "version": "130"}, {"width": 1280, "height": 720}
    )
    source = rec_path / "screens" / "0002.png"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(PNG)
    after = diff = None
    if folder is not None and pictures:
        folder.mkdir(parents=True, exist_ok=True)
        after, diff = folder / "step-0002.after.png", folder / "diff.png"
        after.write_bytes(PNG)
        diff.write_bytes(PNG)
    cats = {
        "capture": judge.Category(status="PASS", required_passed=4, required_total=4),
        "replay": judge.Category(status="PASS", required_passed=4, required_total=4),
        "behaviour": judge.Category(status="PASS" if verdict.startswith("PASS") else "FAIL",
                                    required_passed=1, required_total=2, note="failed: c1"),
        "data": judge.Category(status="NOT_DECLARED"),
        "appearance": judge.Category(status="BLOCKED", required_passed=0, required_total=1,
                                     note="blocked: s"),
    }  # fmt: skip
    if verdict.startswith("PASS"):
        cats["behaviour"] = judge.Category(status="PASS", required_passed=2, required_total=2)
        cats["appearance"] = judge.Category(status="PASS", required_passed=1, required_total=1)
    return fidelity.FidelityReport(
        recording=REC, base_url="http://127.0.0.1:8801", status="pass" if verdict.startswith("PASS") else "fail",
        verdict=verdict, verdict_reasons=["behaviour FAILED (failed: c1)"] if verdict == "FAIL" else
        ["no required acceptance case ran: structure alone is never a scoped PASS"] if verdict == "BLOCKED" else [],
        categories=cats,
        cases=[acc.CaseResult(id="c1", check="count", category="behaviour",
                              status="FAIL" if verdict == "FAIL" else "PASS", step=2,
                              screenshot=str(after) if after else None)],
        coverage=fidelity.Coverage(steps=4, replayed=4, failed=0, skipped=0, unresolved=0,
                                   expected=4, captured=4, compared=3),
        requirements=judge.Requirements(scope=["add and reload"], excluded=["banner styling"],
                                        unobserved=["server validation"]),
        first_divergence=judge.Divergence(step=2, kind="case", what="c1", locator="#search",
                                          state="FAIL: expected 2, saw 1", before=str(source),
                                          after=str(after) if after else None,
                                          diff=str(diff) if diff else None) if verdict != "PASS WITHIN DECLARED SCOPE" else None,
        fingerprint=fp, artifacts=str(folder) if folder else None)  # fmt: skip


def install_fake(monkeypatch, world: Path, verdict: str, seen: dict, *, signature="full"):
    def replay(recording, base_url=None, values=None, *, artifacts=None, viewport=None, **kw):
        seen.update(recording=Path(recording), base_url=base_url, values=values,
                    artifacts=artifacts, viewport=viewport, kw=kw)  # fmt: skip
        return fake_report(world, Path(artifacts) if artifacts else None, verdict)

    def narrow(recording, base_url=None):
        seen.update(recording=Path(recording), base_url=base_url, narrow=True)
        return fake_report(world, None, verdict)

    monkeypatch.setattr(fidelity, "replay", replay if signature == "full" else narrow)


def replay_via_portal(client, **form):
    r = post(client, f"{BASE}/replay", **form)
    assert r.status_code == 303, flash(r)
    return wait_job(client, r.headers["location"])


def test_a_replay_is_refused_while_the_lab_is_down_or_a_value_is_missing(
    world, client, monkeypatch
):
    seen: dict = {}
    install_fake(monkeypatch, world, "FAIL", seen)
    up(monkeypatch, running=False)
    assert "Start the lab" in flash(post(client, f"{BASE}/replay"))
    up(monkeypatch)
    stopped = post(client, f"{BASE}/replay")
    assert "Not started" in flash(stopped) and "bindings.yaml" in flash(stopped)
    assert not seen  # nothing ran


def test_the_replay_job_runs_with_the_declarations_and_keeps_the_report(world, client, monkeypatch):
    post(client, f"{BASE}/files/bindings", text="steps:\n  2:\n    value: Synthetic\n")
    write_cases(world, [{"id": "c1", "check": "count", "locator": "li", "equals": 2}])
    seen: dict = {}
    install_fake(monkeypatch, world, "FAIL", seen)
    up(monkeypatch)
    job = replay_via_portal(client, viewport="mobile")
    assert job["status"] == "done", job["log"]
    assert seen["base_url"] == "http://127.0.0.1:8801" and seen["viewport"] == "mobile"
    folder = Path(seen["artifacts"])
    assert folder.parent.name == APP and folder.parent.parent.name == "artifacts"
    assert (folder.parent.parent.parent / "portal-jobs.db").is_file()  # in the state folder
    assert "acceptance.yaml: blocks" not in job["log"] and "Verdict: FAIL" in job["log"]
    assert "First divergence: case c1 at step 2" in job["log"]
    result = job["result"]
    assert result["verdict"] == "FAIL" and result["report"].startswith(f"/apps/{APP}/reports/")
    assert {link["text"] for link in result["links"]} >= {"Open the report", "Coverage matrix"}
    # kept where readiness and the app page already find reports
    name, report = fidelity.latest(world, APP)
    assert report.verdict == "FAIL" and result["report"].endswith(name)
    assert fidelity.reports_dir(world, APP).joinpath(f"{name}.md").is_file()
    # the coverage matrix was refreshed from it
    saved = cov.load_saved(world, APP)
    assert saved is not None and name in saved.reports_used
    # the job page links its artifacts
    assert client.get(f"/jobs/{job['id']}").status_code == 200
    assert client.get(f"/jobs/{job['id']}.json").json()["result"]["links"]


def test_a_replay_that_cannot_take_a_keyword_is_given_only_what_it_accepts(
    world, client, monkeypatch
):
    post(client, f"{BASE}/files/bindings", text="steps:\n  2:\n    value: Synthetic\n")
    seen: dict = {}
    install_fake(monkeypatch, world, "BLOCKED", seen, signature="narrow")
    up(monkeypatch)
    job = replay_via_portal(client, viewport="desktop")
    assert job["status"] == "done" and seen["narrow"] and job["result"]["verdict"] == "BLOCKED"


def test_a_failing_replay_job_shows_its_failure(world, client, monkeypatch):
    post(client, f"{BASE}/files/bindings", text="steps:\n  2:\n    value: Synthetic\n")

    def broken(*a, **k):
        raise RuntimeError("the browser could not start")

    monkeypatch.setattr(fidelity, "replay", broken)
    up(monkeypatch)
    job = replay_via_portal(client)
    assert job["status"] == "failed" and "the browser could not start" in job["log"]


def test_run_anyway_starts_a_replay_that_has_blockers(world, client, monkeypatch):
    seen: dict = {}
    install_fake(monkeypatch, world, "BLOCKED", seen)
    up(monkeypatch)
    job = replay_via_portal(client, anyway="1")
    assert job["status"] == "done" and seen


def test_replay_across_applications_is_taken_only_when_the_replay_can(world, client, monkeypatch):
    def with_map(recording, base_url=None, *, replicas=None, artifacts=None):
        return None

    def with_kw(recording, **kw):
        return None

    def without(recording, base_url=None):
        return None

    assert rep.replica_param(with_map) == "replicas"
    assert rep.replica_param(with_kw) is None  # takes anything: a map is not assumed
    assert rep.replica_param(without) is None
    assert rep.replica_param(fidelity.replay) in (
        None,
        "replicas",
        "replica_map",
        "origin_map",
        "base_urls",
        "origins",
    )  # whatever this build has
    multi = world / "evidence" / "_recordings" / "multi"
    multi.mkdir(parents=True)
    (multi / "steps.jsonl").write_text(
        '{"seq":1,"url":"https://a.test/x","action":"goto"}\n'
        '{"seq":2,"url":"https://b.test/y","action":"goto"}\n',
        encoding="utf-8",
    )
    (multi / "recording.har").write_text("{}", encoding="utf-8")
    page = client.get("/recordings/multi/files")
    assert page.status_code == 200 and "Create the applications first" in page.text
    refused = post(client, "/recordings/multi/replay")
    assert "not available here" in flash(refused)
    saved = post(client, "/recordings/multi/files/acceptance", text="version: 1\n")
    assert "saved and valid" in flash(saved) and (multi / "acceptance.yaml").is_file()


def test_replica_addresses_for_a_replay_across_applications(world, client, monkeypatch):
    rows = [
        {"id": "orders", "origin": "https://orders.test", "lab": "http://127.0.0.1:8101"},
        {"id": "billing", "origin": "https://billing.test", "lab": ""},
    ]
    src = [r["origin"] for r in rows]
    # the lab port is the default; a typed address replaces it and is not waited for as a lab
    mapping, apps, on_lab, wrong = rep.chosen_replicas(rows, src, ["", "http://box.test:9000/"])
    assert not wrong and apps == ["orders", "billing"] and on_lab == ["orders"]
    assert mapping == {
        "https://orders.test": "http://127.0.0.1:8101",
        "https://billing.test": "http://box.test:9000",
    }
    # no address at all, not an address, and the real application are each refused
    for typed, said in (
        ("", "no replica address"),
        ("box.test:9000", "is not an address"),
        ("http://box.test/path", "is not an address"),
        ("https://orders.test", "recorded source"),
        ("https://BILLING.test/", "recorded source"),
    ):
        _, _, _, wrong = rep.chosen_replicas(rows, src, ["", typed])
        assert len(wrong) == 1 and said in wrong[0], (typed, wrong)

    multi = world / "evidence" / "_recordings" / "multi2"
    multi.mkdir(parents=True)
    (multi / "steps.jsonl").write_text(
        '{"seq":1,"url":"https://orders.test/x","action":"goto"}\n'
        '{"seq":2,"url":"https://billing.test/y","action":"goto"}\n',
        encoding="utf-8",
    )
    (multi / "recording.har").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(rep, "replica_rows", lambda root, info: rows)
    page = client.get("/recordings/multi2/files")
    assert page.status_code == 200 and 'id="replicas"' in page.text
    assert 'value="http://127.0.0.1:8101"' in page.text and "https://billing.test" in page.text
    if rep.replica_param(fidelity.replay) is not None:
        refused = client.post(
            "/recordings/multi2/replay",
            data={"source": src, "replica": ["", "https://orders.test"]},
            follow_redirects=False,
        )
        assert "recorded source" in flash(refused)


# --- the report page --------------------------------------------------------------------------------


def keep(world, monkeypatch, verdict, state):
    folder = state / "artifacts" / APP / f"r-{verdict[:4]}"
    report = fake_report(world, folder, verdict)
    return fidelity.save(world, APP, report), report


def test_the_report_page_for_fail_pass_and_blocked(world, client, tmp_path, monkeypatch):
    state = tmp_path / "state"
    names = {}
    for verdict in ("FAIL", "BLOCKED", "PASS WITHIN DECLARED SCOPE"):
        names[verdict], _ = keep(world, monkeypatch, verdict, state)
        time.sleep(1.1)  # report names carry a one-second stamp
    fail = client.get(f"/apps/{APP}/reports/{names['FAIL']}")
    assert fail.status_code == 200
    t = fail.text
    assert 'id="verdict"' in t and ">FAIL<" in t and "behaviour FAILED" in t
    assert "failed" in t and "1 / 2" in t  # the failed required category: passed / total
    assert "%" not in re.sub(r"<[^>]+>", "", t.split('id="categories"')[1].split("</section>")[0])
    assert "First divergence" in t and "step <b>2</b>" in t and "<code>#search</code>" in t
    assert "FAIL: expected 2, saw 1" in t
    assert t.count("/artifacts/") >= 3  # source, replica and difference pictures
    assert "Exclusions applied" in t and "banner styling" in t
    assert "Expected actions" in t and ">4<" in t
    assert "Compared:" in t and "recording rec1" in t
    assert "Still current" in t
    blocked = client.get(f"/apps/{APP}/reports/{names['BLOCKED']}").text
    assert ">BLOCKED<" in blocked and "structure alone is never a scoped PASS" in blocked
    assert "blocked" in blocked.split('id="categories"')[1]
    ok = client.get(f"/apps/{APP}/reports/{names['PASS WITHIN DECLARED SCOPE']}").text
    assert ">PASS WITHIN DECLARED SCOPE<" in ok and "First divergence" not in ok


def test_a_failed_category_stays_visible_on_the_app_page_and_the_next_step_says_replay(
    world, client, tmp_path, monkeypatch
):
    up(monkeypatch)
    keep(world, monkeypatch, "FAIL", tmp_path / "state")
    page = client.get(f"/apps/{APP}").text
    assert 'id="latest-replay"' in page and "FAIL" in page and "behaviour failed" in page
    assert "1 / 2 required passed" in page
    assert "Values and checks" in page and "Replay" in page and 'id="replication"' in page
    assert 'id="rec-files"' in page and "Values, checks and replay" in page


def test_a_changed_declaration_makes_the_report_out_of_date(world, client, tmp_path, monkeypatch):
    name, _ = keep(world, monkeypatch, "PASS WITHIN DECLARED SCOPE", tmp_path / "state")
    assert "Still current" in client.get(f"/apps/{APP}/reports/{name}").text
    post(client, f"{BASE}/files/acceptance", text="version: 1\ncases: []\n")
    page = client.get(f"/apps/{APP}/reports/{name}").text
    assert "Out of date" in page and "acceptance file was added" in page


def test_unknown_or_malformed_report_names_are_404(client):
    for name in ("nope", "..%2f..%2fapp", "rec1--20261118T000000Z"):
        assert client.get(f"/apps/{APP}/reports/{name}").status_code == 404


# --- pictures: only from the state folder --------------------------------------------------------


def test_pictures_are_served_from_the_artifact_folder_only(world, client, tmp_path, monkeypatch):
    state = tmp_path / "state"
    folder = state / "artifacts" / APP / "run1"
    folder.mkdir(parents=True)
    (folder / "a.png").write_bytes(PNG)
    (folder / "notes.txt").write_text("secret", encoding="utf-8")
    (state / "artifacts" / "other").mkdir()
    (state / "artifacts" / "other" / "b.png").write_bytes(PNG)
    (world / "apps" / APP / "leak.png").write_bytes(PNG)
    (state / "outside.png").write_bytes(PNG)
    ok = client.get(f"/apps/{APP}/artifacts/run1/a.png")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    assert ok.headers["x-content-type-options"] == "nosniff"
    for rel in ("run1/notes.txt", "run1/missing.png", "../other/b.png", "..%2fother%2fb.png",
                "%2e%2e/%2e%2e/outside.png", "run1/../../outside.png", "..%5c..%5coutside.png",
                "/etc/passwd", "C:/Windows/win.ini", f"..%2f..%2f..%2f..%2fapps/{APP}/leak.png",
                "run1/%2e%2e/%2e%2e/%2e%2e/outside.png"):  # fmt: skip
        got = client.get(f"/apps/{APP}/artifacts/{rel}")
        assert got.status_code == 404, rel
    assert client.get("/apps/other/artifacts/run1/a.png").status_code == 404  # another app's folder
    assert rep.resolve_artifact(state, APP, "run1/a.png") is not None
    for rel in ("", "..", "../x.png", "/abs.png", "run1/../a.png", "a.png\x00", "run1\\..\\a.png"):
        assert rep.resolve_artifact(state, APP, rel) is None, rel


def test_a_picture_outside_the_artifact_folder_is_not_linked(world, client, tmp_path, monkeypatch):
    state = tmp_path / "state"
    report = fake_report(world, None, "FAIL")  # before: in the recording's folder, after: none
    name = fidelity.save(world, APP, report)
    page = client.get(f"/apps/{APP}/reports/{name}").text
    assert "/artifacts/" not in page
    assert "not in the portal's artifact folder" in page or "No picture was kept" in page
    assert (
        rep.artifact_url(state, APP, str(world / "evidence" / APP / REC / "screens" / "0002.png"))
        is None
    )


def test_localise_copies_a_source_picture_into_the_artifact_folder(world, tmp_path):
    state = tmp_path / "state"
    folder = state / "artifacts" / APP / "run"
    report = fake_report(world, folder, "FAIL")
    rep.localise(report, folder)
    before = Path(report.first_divergence.before)
    assert folder.resolve() in before.resolve().parents and before.is_file()
    assert rep.artifact_url(state, APP, str(before)).startswith(
        f"/apps/{APP}/artifacts/run/source/"
    )


# --- coverage in the portal ---------------------------------------------------------------------


def test_coverage_page_builds_shows_requests_and_goes_stale(world, client):
    page = client.get(f"/apps/{APP}/coverage")
    assert page.status_code == 200 and 'id="no-coverage"' in page.text
    r = post(client, f"/apps/{APP}/coverage")
    assert "Coverage matrix built" in flash(r)
    page = client.get(f"/apps/{APP}/coverage").text
    assert 'id="claim"' in page and "Not claimable" in page
    assert "Evidence requests" in page and "Record a failed sign-in" in page
    assert "Denominator" in page and "unobserved" in page
    assert "still current" in page
    assert client.get(f"/apps/{APP}/coverage.json").json()["claim"] is False
    assert client.get(f"/apps/{APP}/coverage.md").text.startswith("# Coverage matrix")
    summary = client.get(f"/apps/{APP}").text
    assert 'id="coverage-summary"' in summary and "not claimable" in summary
    write_cases(world, [{"id": "c", "check": "value", "locator": "#search", "equals": "a"}])
    assert "Out of date" in client.get(f"/apps/{APP}/coverage").text
    assert "out of date" in client.get(f"/apps/{APP}").text


def test_coverage_without_a_model_says_so(world, client):
    (world / "apps" / APP / "model.yaml").unlink()
    r = post(client, f"/apps/{APP}/coverage")
    assert "model.yaml" in flash(r)
    assert client.get(f"/apps/{APP}/coverage.json").status_code == 404


# --- the next step ---------------------------------------------------------------------------------


def _steps(**rep_facts):
    base = dict(has_evidence=True, has_brief=True, built=True, running=True, automations=[],
                ready=[], has_baseline=False, sessions=0, declared=[])  # fmt: skip
    return guide.app_steps(APP, **base, replication=rep_facts or None)


def test_the_steps_are_unchanged_for_an_app_without_recordings():
    ids = [s.id for s in _steps()]
    assert "declare" not in ids and "replay" not in ids and "coverage" not in ids
    assert [s.id for s in _steps(recordings=[])] == ids


def test_the_workflow_steps_follow_the_documented_order():
    steps = _steps(recordings=[REC], blocked=["bindings.yaml: step 2: no value"], flow=True,
                   declared_acceptance=False)  # fmt: skip
    ids = [s.id for s in steps]
    assert ids.index("evidence") < ids.index("flow") < ids.index("brief") < ids.index("build")
    assert ids.index("lab") < ids.index("declare") < ids.index("replay") < ids.index("coverage")
    by = {s.id: s for s in steps}
    assert by["declare"].state == "next"  # lab is up, so the values are the next thing
    assert "bindings.yaml: step 2" in by["declare"].hint and by["declare"].href.endswith(
        f"/recordings/{REC}"
    )
    assert by["replay"].state == "todo" and by["coverage"].state == "todo"


def test_the_next_step_moves_through_replay_and_coverage():
    ready = dict(recordings=[REC], blocked=[], flow=True, declared_acceptance=True)
    s = {x.id: x for x in _steps(**ready)}
    assert s["declare"].state == "done" and s["replay"].state == "next"
    failed = {x.id: x for x in _steps(**ready, verdict="FAIL", report_state="current")}
    assert failed["replay"].state == "next" and "FAIL" in failed["replay"].hint
    stale = {
        x.id: x for x in _steps(**ready, verdict="PASS WITHIN DECLARED SCOPE", report_state="stale")
    }
    assert stale["replay"].state == "next" and "out of date" in stale["replay"].hint
    passed = {x.id: x for x in _steps(**ready, verdict="PASS WITHIN DECLARED SCOPE",
                                      report_state="current")}  # fmt: skip
    assert passed["replay"].state == "done" and passed["coverage"].state == "next"
    assert "Build the coverage matrix" in passed["coverage"].hint
    short = {x.id: x for x in _steps(**ready, verdict="PASS WITHIN DECLARED SCOPE",
                                     report_state="current", coverage_state="current",
                                     claim=False, shortfall="3 unobserved (UNOBSERVED)")}  # fmt: skip
    assert short["coverage"].state == "next" and "3 unobserved" in short["coverage"].hint
    full = {x.id: x for x in _steps(**ready, verdict="PASS WITHIN DECLARED SCOPE",
                                    report_state="current", coverage_state="current", claim=True)}  # fmt: skip
    assert full["coverage"].state == "done"
    old = {x.id: x for x in _steps(**ready, coverage_state="stale")}
    assert "out of date" in old["coverage"].hint


def test_the_app_page_shows_the_workflow_steps(world, client, monkeypatch):
    up(monkeypatch)
    page = client.get(f"/apps/{APP}").text
    for title in ("Flow and scope", "Values and checks", "Replay", "Coverage"):
        assert title in page
    assert "Next step" in page or "First rehearsal" in page
