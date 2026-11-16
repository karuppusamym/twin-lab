"""Portal (T-15) without Docker: registration, flows, evidence, brief, Devin package and API path."""

import io
import shutil
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from labctl.devin import mock
from labctl.portal.app import _unzip, create_app

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A copy of the repository's contracts and the showcase inputs, in a temp folder."""
    for folder in ("automations", "specs", "conditions", "lab"):
        shutil.copytree(
            ROOT / folder,
            tmp_path / folder,
            ignore=shutil.ignore_patterns("node_modules", "__pycache__"),
        )
    (tmp_path / "apps").mkdir()
    shutil.copytree(ROOT / "reference" / "case-workbench", tmp_path / "reference" / "case-workbench",
                    ignore=shutil.ignore_patterns("node_modules", "dist", "*.db"))  # fmt: skip
    for name in ("AGENTS.md",):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / "docs").mkdir()
    shutil.copy(ROOT / "docs" / "factory-playbook.md", tmp_path / "docs" / "factory-playbook.md")
    monkeypatch.setenv("LABCTL_ROOT", str(tmp_path))
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / ".lab-state"))
    for var in ("DEVIN_API_KEY", "DEVIN_ORG_ID", "DEVIN_BASE_URL", "GITHUB_REPO"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def wait_job(client: TestClient, location: str, timeout: float = 120) -> dict:
    job_id = location.rstrip("/").split("/")[-1]
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}.json").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_every_page_renders(repo):
    client = TestClient(create_app(repo))
    for url in ("/", "/automations", "/automations/uipath-case-update", "/flows", "/mapping", "/settings",
                "/apps/showcase"):  # fmt: skip
        r = client.get(url)
        assert r.status_code == 200, url
        assert "Twin Lab" in r.text
    assert client.get("/automations/nope").status_code == 404


def test_register_a_uipath_project_from_a_zip_and_it_is_analysed(repo):
    client = TestClient(create_app(repo))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for f in (ROOT / "automations/uipath-case-update/project").iterdir():
            z.write(f, f"CaseUpdate/{f.name}")  # zipped as a folder: the top folder is dropped
    form = {
        "automation_id": "uipath-copy",
        "name": "Copy",
        "kind": "uipath",
        "surface": "combo",
        "app_id": "showcase",
    }
    r = client.post(
        "/automations",
        data=form,
        files={"project": ("p.zip", buffer.getvalue(), "application/zip")},
    )
    assert r.status_code == 200
    assert "Analysed Main.xaml, AddNoteViaApi.xaml: 0 gap(s)" in r.text
    saved = yaml.safe_load(
        (repo / "automations/uipath-copy/automation.yaml").read_text(encoding="utf-8")
    )
    assert len(saved["touches"]) == 3
    assert saved["bindings"] == {
        "in_AppUrl": "{{showcase.web}}", "in_AppApi": "{{showcase.api}}", "in_Db": "{{showcase.db}}",
        "in_User": "{{showcase.credentials.username}}", "in_Password": "{{showcase.credentials.password}}",
    }  # fmt: skip


def test_zip_with_a_path_outside_the_target_is_refused(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("../evil.txt", "x")
    with pytest.raises(ValueError, match="Unsafe path"):
        _unzip(buffer.getvalue(), tmp_path / "t")
    assert not (tmp_path / "evil.txt").exists()


def test_a_flow_is_validated_before_it_is_saved(repo):
    client = TestClient(create_app(repo))
    bad = client.post("/flows", data={"text": "id: Bad Id\napp: x\n"})
    assert "Not saved" in bad.text and not (repo / "flows").exists()
    good = (ROOT / "specs/flow.example.yaml").read_text(encoding="utf-8")
    assert "saved" in client.post("/flows", data={"text": good}).text
    assert (repo / "flows/case-update.yaml").exists()


def test_evidence_upload_brief_and_package(repo):
    client = TestClient(create_app(repo))
    r = client.post("/apps/showcase/evidence", data={"kind": "ddl", "name": "schema"},
                    files={"file": ("x.sql", b"CREATE TABLE extra (id INTEGER PRIMARY KEY);", "text/plain")})  # fmt: skip
    assert "Added ddl" in r.text and (repo / "evidence/showcase/schema.sql").exists()
    r = client.post("/apps/showcase/evidence/delete", data={"path": "schema.sql"})
    assert "Removed" in r.text and not (repo / "evidence/showcase/schema.sql").exists()
    assert (
        client.post("/apps/showcase/evidence/delete", data={"path": "../../AGENTS.md"}).status_code
        == 400
    )

    r = client.post("/apps/showcase/brief", data={"system": "Case Workbench", "source": "reference/case-workbench",
                                                  "db_engine": "postgres"}, follow_redirects=False)  # fmt: skip
    job = wait_job(client, r.headers["location"])
    assert job["status"] == "done", job["log"]
    assert job["result"]["tables"] == 7
    assert (repo / "apps/showcase/BRIEF.md").exists() and (
        repo / ".github/workflows/app-showcase.yml"
    ).exists()
    assert "Build brief: showcase" in client.get("/apps/showcase/brief").text

    r = client.get("/apps/showcase/package")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "DEVIN_TASK.md" in names and "apps/showcase/BRIEF.md" in names
    assert "by hand" not in client.get("/apps/showcase").text  # a download records nothing
    pr = {"pr_url": "https://github.com/example/twin-lab/pull/7"}
    r = client.post("/apps/showcase/devin/manual", data=pr)
    assert "Build by hand recorded" in r.text and "pull/7" in r.text


def test_devin_build_through_the_api_end_to_end_with_the_mock(repo, monkeypatch):
    with mock.serve_in_thread(mock.create_app(api_key="cog_test_fake", step_seconds=0.3)) as base:
        monkeypatch.setenv("DEVIN_API_KEY", "cog_test_fake")
        monkeypatch.setenv("DEVIN_ORG_ID", "org-test")
        monkeypatch.setenv("DEVIN_BASE_URL", base)
        monkeypatch.setenv("GITHUB_REPO", "example/twin-lab")
        monkeypatch.setenv("TWINLAB_DEVIN_POLL_SECONDS", "0.5")
        app = create_app(repo)
        client = TestClient(app)
        r = client.post("/apps/showcase/brief", data={"source": "reference/case-workbench", "db_engine": "postgres"},
                        follow_redirects=False)  # fmt: skip
        assert wait_job(client, r.headers["location"])["status"] == "done"
        check = client.post("/settings/devin-check", follow_redirects=False)
        assert wait_job(client, check.headers["location"])["status"] == "done"

        r = client.post("/apps/showcase/devin/start", data={"max_acu": "5"})
        assert "Devin build started" in r.text
        deadline = time.time() + 30
        while time.time() < deadline:
            page = client.get("/apps/showcase").text
            if "https://github.com/example/twin-lab/pull/" in page:
                break
            time.sleep(0.3)
        assert "https://github.com/example/twin-lab/pull/" in page
        assert "finished" in page


def test_devin_start_without_configuration_says_what_to_do(repo):
    client = TestClient(create_app(repo))
    r = client.post("/apps/showcase/devin/start", data={})
    assert "Devin is not configured" in r.text


def test_password_protects_every_page(repo, monkeypatch):
    monkeypatch.setenv("TWINLAB_PORTAL_PASSWORD", "s3cret-for-test")
    client = TestClient(create_app(repo))
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("anyone", "s3cret-for-test")).status_code == 200
    assert client.get("/", auth=("anyone", "wrong")).status_code == 401


def test_parallel_jobs_keep_their_own_logs_and_release_stdout(tmp_path):
    import sys
    import threading

    from labctl.portal.jobs import JobRunner

    runner = JobRunner(tmp_path / "jobs.db", workers=4)
    gate = threading.Barrier(2)

    def job(name):
        def run(log):
            gate.wait(5)
            for i in range(50):
                print(f"{name}-{i}")
            return name

        return run

    a, b = runner.submit("t", "A", job("A")), runner.submit("t", "B", job("B"))
    for _ in range(200):
        if all(runner.get(j)["status"] == "done" for j in (a, b)):
            break
        time.sleep(0.05)
    log_a, log_b = runner.get(a)["log"], runner.get(b)["log"]
    assert "B-" not in log_a and "A-" not in log_b
    assert log_a.count("A-") == 50 and log_b.count("B-") == 50
    print("after the jobs")  # goes to the console, not into a finished job's log
    assert "after the jobs" not in runner.get(a)["log"] + runner.get(b)["log"]
    assert (
        getattr(sys.stdout, "local", None) is None or getattr(sys.stdout.local, "log", None) is None
    )


def test_cross_site_posts_are_refused(repo):
    client = TestClient(create_app(repo))
    r = client.post("/apps/showcase/data/reset", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/flows", data={"text": "x"}, headers={"origin": "http://testserver"})
    assert r.status_code == 200  # same site is fine


def test_uploads_replace_only_when_asked_and_briefs_are_backed_up(repo):
    client = TestClient(create_app(repo))
    files = {"file": ("x.sql", b"CREATE TABLE a (id INTEGER);", "text/plain")}
    first = client.post("/apps/showcase/evidence", data={"kind": "ddl", "name": "s"}, files=files)
    assert "Added ddl" in first.text
    again = client.post("/apps/showcase/evidence", data={"kind": "ddl", "name": "s"}, files=files)
    assert "already exists" in again.text
    forced = client.post(
        "/apps/showcase/evidence", data={"kind": "ddl", "name": "s", "replace": "1"}, files=files
    )
    assert "Added ddl" in forced.text
    (repo / "evidence/showcase/s.sql").unlink()

    form = {"source": "reference/case-workbench", "db_engine": "postgres"}
    for _ in range(2):
        r = client.post("/apps/showcase/brief", data=form, follow_redirects=False)
        assert wait_job(client, r.headers["location"])["status"] == "done"
    backups = list((repo / ".lab-state/backups/showcase").iterdir())
    assert len(backups) == 1 and (backups[0] / "BRIEF.md").exists()


def test_source_folder_must_be_inside_the_repository(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere")
    client = TestClient(create_app(repo))
    r = client.post("/apps/showcase/brief", data={"source": str(outside)}, follow_redirects=False)
    job = wait_job(client, r.headers["location"])
    assert job["status"] == "failed" and "inside the repository" in job["log"]
