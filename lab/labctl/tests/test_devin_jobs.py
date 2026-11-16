import threading
import time
from pathlib import Path

import pytest

from labctl.devin import mock
from labctl.devin.client import DevinClient
from labctl.devin.jobs import BuildJobs, JobError, JobStore, map_status

KEY = "cog_jobs_fake_key"
ORG = "org-jobs"
STEP = 0.2
POLL = 0.05


def make_repo(root: Path, app: str, brief: str) -> Path:
    (root / "apps" / app / "db").mkdir(parents=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (root / "docs" / "factory-playbook.md").write_text("# playbook\n", encoding="utf-8")
    (root / "apps" / app / "BRIEF.md").write_text(brief, encoding="utf-8")
    (root / "apps" / app / "db" / "schema.sql").write_text("CREATE TABLE t();\n", encoding="utf-8")
    (root / "apps" / app / "app.yaml").write_text(
        f"id: {app}\ncopies:\n  ref: sha256:feedfacefeedface\nbuilt_for: [bot-a]\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="module")
def server():
    app = mock.create_app(api_key=KEY, step_seconds=STEP)
    with mock.serve_in_thread(app) as url:
        app.state.url = url
        yield app


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    make_repo(root, "demo", "# Build brief: demo\n")
    make_repo(root, "asker", "# Build brief: asker\n\n[mock:ask]\n")
    make_repo(root, "broke", "# Build brief: broke\n\n[mock:out_of_credits]\n")
    return root


def jobs_for(server, repo, state, key=KEY):
    return BuildJobs(
        JobStore(state),
        lambda: DevinClient(key, ORG, server.state.url, timeout=5),
        repo,
        poll_interval=POLL,
    )


def wait(jobs: BuildJobs, job_id: str, pred, timeout=6.0):
    deadline = time.monotonic() + timeout
    while True:
        job = jobs.get(job_id)
        if pred(job):
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; job is {job}")
        time.sleep(0.03)


def test_start_runs_to_finished_with_pr_recorded(server, repo, tmp_path):
    jobs = jobs_for(server, repo, tmp_path / "state")
    try:
        job = jobs.start("demo", "acme/twin-lab", max_acu_limit=7)
        assert job.mode == "direct" and job.state == "running" and job.status == "new"
        assert job.devin_id and job.session_url and job.attachment_url
        assert Path(job.package_path).is_file() and job.brief_digest == "sha256:feedfacefeedface"
        done = wait(jobs, job.id, lambda j: j.terminal)
    finally:
        jobs.stop()
    assert done.state == "finished"
    assert done.status == "running" and done.status_detail == "finished"
    assert done.pr_urls and done.pr_urls[0].startswith("https://github.com/acme/twin-lab/pull/")
    assert done.finished_at and done.last_polled_at and done.acus_consumed is not None
    assert done.last_error is None

    session = server.state.mock.sessions[done.devin_id.removeprefix("devin-")]
    assert session.tags == ["twinlab", "app:demo"]
    assert session.repos == ["acme/twin-lab"]
    assert session.max_acu_limit == 7
    assert session.attachment_urls == [job.attachment_url]
    assert f'ATTACHMENT:"{job.attachment_url}"' in session.prompt
    assert "devin/demo-<yyyymmdd-hhmm>" in session.prompt
    assert session.title.startswith("Twin Lab: demo")


def test_waiting_for_user_is_surfaced_and_a_message_resumes(server, repo, tmp_path):
    jobs = jobs_for(server, repo, tmp_path / "state")
    try:
        job = jobs.start("asker", "acme/twin-lab")
        asked = wait(jobs, job.id, lambda j: j.state == "waiting_for_user")
        assert asked.status == "running" and asked.status_detail == "waiting_for_user"
        assert not asked.terminal
        time.sleep(STEP * 2)  # it stays there and is not failed
        assert jobs.get(job.id).state == "waiting_for_user"
        jobs.message(job.id, "Choose the simplest reading and note it.")
        done = wait(jobs, job.id, lambda j: j.terminal)
    finally:
        jobs.stop()
    assert done.state == "finished" and done.pr_urls


def test_credit_suspension_fails_the_job(server, repo, tmp_path):
    jobs = jobs_for(server, repo, tmp_path / "state")
    try:
        job = jobs.start("broke", "acme/twin-lab")
        done = wait(jobs, job.id, lambda j: j.terminal)
    finally:
        jobs.stop()
    assert done.state == "failed" and done.status == "suspended"
    assert "out_of_credits" in done.last_error


def test_restart_resumes_polling(server, repo, tmp_path):
    state = tmp_path / "state"
    first = jobs_for(server, repo, state)
    job = first.start("demo", "acme/twin-lab")
    first.stop()  # the process "exits" while Devin is still working
    assert not first.get(job.id).terminal

    second = jobs_for(server, repo, state)
    try:
        assert second.resume_unfinished() == [job.id]
        done = wait(second, job.id, lambda j: j.terminal)
    finally:
        second.stop()
    assert done.state == "finished" and done.pr_urls


def test_restart_fails_jobs_interrupted_before_a_session(server, repo, tmp_path):
    jobs = jobs_for(server, repo, tmp_path / "state")
    from labctl.devin.jobs import Job

    jobs.store.save(Job(id="halfway", app_id="demo", mode="direct", state="uploading"))
    assert jobs.resume_unfinished() == []
    job = jobs.get("halfway")
    assert job.state == "failed" and "before a Devin session" in job.last_error


def test_cancel_terminates_the_session(server, repo, tmp_path):
    jobs = jobs_for(server, repo, tmp_path / "state")
    try:
        job = jobs.start("asker", "acme/twin-lab")
        cancelled = jobs.cancel(job.id)
        time.sleep(STEP * 3)
        after = jobs.get(job.id)
    finally:
        jobs.stop()
    assert cancelled.state == after.state == "cancelled"
    assert server.state.mock.sessions[job.devin_id.removeprefix("devin-")].phase == "exit"
    with pytest.raises(JobError, match="already cancelled"):
        jobs.cancel(job.id)


def test_failed_handoff_is_recorded_not_raised(server, repo, tmp_path):
    jobs = jobs_for(server, repo, tmp_path / "state", key="cog_wrong_fake")
    try:
        job = jobs.start("demo", "acme/twin-lab")
    finally:
        jobs.stop()
    assert job.state == "failed" and "401" in job.last_error and "DEVIN_API_KEY" in job.last_error
    assert jobs.list()[0].id == job.id


def test_missing_brief_fails_the_job(server, repo, tmp_path):
    jobs = jobs_for(server, repo, tmp_path / "state")
    job = jobs.start("nothere", "acme/twin-lab")
    assert job.state == "failed" and "missing" in job.last_error


def test_manual_record_and_attach_pr(server, repo, tmp_path):
    from labctl.devin import package as pkg

    jobs = jobs_for(server, repo, tmp_path / "state")
    path = pkg.build(repo, "demo", tmp_path / "out", repo="acme/twin-lab")
    job = jobs.record_manual("demo", path)
    assert job.mode == "manual" and job.state == "running" and job.repo == "acme/twin-lab"
    assert job.brief_digest == "sha256:feedfacefeedface" and job.package_path == str(path)
    assert jobs.resume_unfinished() == []  # nothing to poll for a manual job

    with pytest.raises(JobError, match="not a GitHub pull request"):
        jobs.attach_pr(job.id, "https://example.com/pr/1")
    done = jobs.attach_pr(
        job.id[:6],
        "https://github.com/acme/twin-lab/pull/42",
        session_url="https://app.devin.ai/s/1",
    )
    assert done.state == "finished" and done.pr_urls == ["https://github.com/acme/twin-lab/pull/42"]
    assert done.session_url == "https://app.devin.ai/s/1" and done.finished_at

    reloaded = JobStore(tmp_path / "state").get(job.id)
    assert reloaded.state == "finished" and reloaded.pr_urls == done.pr_urls


def test_store_is_thread_safe(tmp_path):
    from labctl.devin.jobs import Job

    store = JobStore(tmp_path / "state")
    store.save(Job(id="j1", app_id="a", mode="manual", state="running"))

    def bump(n):
        for i in range(20):
            store.update("j1", last_error=f"{n}-{i}")

    threads = [threading.Thread(target=bump, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.get("j1").state == "running"
    assert len(store.list()) == 1


@pytest.mark.parametrize(
    "status,detail,state",
    [
        ("new", None, "running"),
        ("claimed", None, "running"),
        ("resuming", None, "running"),
        ("running", "working", "running"),
        ("running", "waiting_for_user", "waiting_for_user"),
        ("running", "waiting_for_approval", "waiting_for_user"),
        ("running", "finished", "finished"),
        ("exit", None, "finished"),
        ("error", None, "failed"),
        ("suspended", "inactivity", "waiting_for_user"),
        ("suspended", "user_request", "waiting_for_user"),
        ("suspended", "usage_limit_exceeded", "failed"),
        ("suspended", "out_of_credits", "failed"),
        ("suspended", "out_of_quota", "failed"),
        ("something_new", None, "running"),
    ],
)
def test_status_mapping(status, detail, state):
    assert map_status(status, detail)[0] == state


def test_start_refuses_a_second_unfinished_direct_build(server, repo, tmp_path):
    state = tmp_path / "state"
    jobs = jobs_for(server, repo, state)
    other = jobs_for(server, repo, state)  # another process sharing the state directory
    try:
        first = jobs.start("asker", "acme/twin-lab")
        assert not first.terminal
        sessions = len(server.state.mock.sessions)
        with pytest.raises(JobError, match=rf"already in progress \(job {first.id}"):
            jobs.start("asker", "acme/twin-lab")
        with pytest.raises(JobError, match="already in progress"):
            other.start("asker", "acme/twin-lab")
        assert len(server.state.mock.sessions) == sessions  # no second session created
        assert [j.id for j in jobs.list("asker")] == [first.id]
        # Another app is not affected, and force starts a second build anyway.
        assert jobs.start("demo", "acme/twin-lab").state != "failed"
        forced = jobs.start("asker", "acme/twin-lab", force=True)
        assert forced.id != first.id and not forced.terminal
        jobs.cancel(first.id)
        jobs.cancel(forced.id)
        again = jobs.start("asker", "acme/twin-lab")  # nothing unfinished: allowed
        assert again.id not in (first.id, forced.id)
    finally:
        jobs.stop()
        other.stop()


def test_update_writes_only_the_fields_given(tmp_path):
    from labctl.devin.jobs import Job

    state = tmp_path / "state"
    a, b = JobStore(state), JobStore(state)
    a.save(Job(id="j1", app_id="app", mode="manual", state="running"))
    stale = a.get("j1")
    # Process B attaches a pull request while process A holds an older copy of the row.
    pr = [{"pr_url": "https://github.com/acme/x/pull/1", "pr_state": "open"}]
    b.update("j1", pull_requests=pr, state="finished")
    a.get = lambda job_id: stale if job_id == "j1" else None  # A still sees the old row
    try:
        a.update("j1", last_error="note from A")
    finally:
        del a.get
    got = b.get("j1")
    assert got.pull_requests == pr and got.state == "finished" and got.last_error == "note from A"


def test_a_stale_poll_never_overwrites_a_terminal_state(tmp_path):
    from labctl.devin.jobs import Job

    store = JobStore(tmp_path / "state")
    for state in ("cancelled", "finished", "failed"):
        store.save(Job(id=state, app_id="app", mode="direct", state=state, devin_id="devin-x"))
        got = store.update(state, from_poller=True, state="running", status="running")
        assert got.state == state and got.status is None
    # A poll may still record a terminal result over a terminal state, but never over cancelled.
    assert store.update("finished", from_poller=True, state="failed").state == "failed"
    assert store.update("cancelled", from_poller=True, state="finished").state == "cancelled"
    # Writes that are not from a poller (a person's message, resuming) still apply.
    assert store.update("finished", state="running").state == "running"


def test_poller_with_a_stale_read_cannot_undo_a_cancel(server, repo, tmp_path):
    from labctl.devin.jobs import Job

    state = tmp_path / "state"
    jobs = jobs_for(server, repo, state)
    jobs.store.save(Job(id="j2", app_id="demo", mode="direct", state="running", devin_id="d"))
    stale = jobs.store.get("j2")
    JobStore(state).update("j2", state="cancelled", finished_at="2026-11-15T00:00:00Z")  # the CLI
    jobs._require = lambda job_id: stale  # the tracker read the row before the cancel
    job = jobs._record("j2", {"status": "running", "status_detail": "working"})
    assert job.state == "cancelled" and job.finished_at == "2026-11-15T00:00:00Z"


def test_poll_keeps_a_pull_request_attached_by_hand(server, repo, tmp_path):
    from labctl.devin.jobs import Job

    jobs = jobs_for(server, repo, tmp_path / "state")
    jobs.store.save(Job(id="j3", app_id="demo", mode="direct", state="running", devin_id="d"))
    jobs.attach_pr("j3", "https://github.com/acme/x/pull/9")
    job = jobs._record("j3", {"status": "running", "status_detail": "working", "pull_requests": []})
    assert job.pr_urls == ["https://github.com/acme/x/pull/9"]


def test_get_for_app_checks_the_app(tmp_path):
    from labctl.devin.jobs import Job

    jobs = BuildJobs(JobStore(tmp_path / "state"), lambda: None, tmp_path)
    jobs.store.save(Job(id="abc123", app_id="demo", mode="manual", state="running"))
    assert jobs.get_for_app("abc123", "demo").id == "abc123"
    with pytest.raises(JobError, match="belongs to demo, not other"):
        jobs.get_for_app("abc123", "other")
    with pytest.raises(JobError, match="no job"):
        jobs.get_for_app("nope", "demo")


def test_a_corrected_key_is_used_without_restarting(server, repo, tmp_path):
    """Review finding 4: a wrong key cached forever meant every build after the fix still got 401."""
    settings = {"key": "cog_wrong"}
    jobs = BuildJobs(
        JobStore(tmp_path / "state"),
        lambda: DevinClient(settings["key"], ORG, server.state.url, timeout=5),
        repo,
        poll_interval=POLL,
        fingerprint=lambda: settings["key"],
    )
    try:
        refused = jobs.start("demo", "acme/twin-lab")
        assert refused.state == "failed" and "401" in (refused.last_error or "")
        settings["key"] = KEY  # the person fixes twinlab.local.yaml
        job = jobs.start("demo", "acme/twin-lab")
        assert wait(jobs, job.id, lambda j: j.terminal).state == "finished"
    finally:
        jobs.stop()
