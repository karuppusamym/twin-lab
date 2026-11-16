import json
import time
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labctl.devin import mock
from labctl.devin.cli import app

KEY = "cog_cli_fake_key"
runner = CliRunner()


@pytest.fixture(scope="module")
def server():
    with mock.serve_in_thread(mock.create_app(api_key=KEY, step_seconds=0.2)) as url:
        yield url


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for d in ("apps/demo/db", "docs", "automations", "lab"):
        (root / d).mkdir(parents=True)
    (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (root / "docs" / "factory-playbook.md").write_text("# playbook\n", encoding="utf-8")
    (root / "apps" / "demo" / "BRIEF.md").write_text("# brief\n", encoding="utf-8")
    (root / "apps" / "demo" / "db" / "schema.sql").write_text("CREATE TABLE t();", encoding="utf-8")
    monkeypatch.setenv("LABCTL_ROOT", str(root))
    for key in ("DEVIN_API_KEY", "DEVIN_ORG_ID", "DEVIN_BASE_URL", "GITHUB_REPO", "STATE_DIR"):
        monkeypatch.delenv(key, raising=False)
    return root


def configure(monkeypatch, server, key=KEY):
    monkeypatch.setenv("DEVIN_API_KEY", key)
    monkeypatch.setenv("DEVIN_ORG_ID", "org-cli")
    monkeypatch.setenv("DEVIN_BASE_URL", server)
    monkeypatch.setenv("GITHUB_REPO", "acme/twin-lab")


def test_manual_path_package_then_attach_pr(repo):
    result = runner.invoke(app, ["package", "demo", "--repo", "acme/twin-lab"])
    assert result.exit_code == 0, result.output
    zips = list((repo / ".lab-state" / "packages").glob("twinlab-demo-*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as zf:
        assert "`acme/twin-lab`" in zf.read("DEVIN_TASK.md").decode()
    job_id = next(line.split()[-1] for line in result.output.splitlines() if "Manual job" in line)
    assert "attach-pr" in result.output

    result = runner.invoke(app, ["attach-pr", job_id, "https://github.com/acme/twin-lab/pull/9"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["status", "--json"])
    jobs = json.loads(result.output)
    assert jobs[0]["state"] == "finished" and jobs[0]["pr_urls"][0].endswith("/pull/9")


def test_package_refuses_missing_brief(repo):
    (repo / "apps" / "demo" / "BRIEF.md").unlink()
    result = runner.invoke(app, ["package", "demo"])
    assert result.exit_code == 1 and "BRIEF.md" in result.output


def test_start_needs_config(repo):
    result = runner.invoke(app, ["start", "demo", "--repo", "acme/twin-lab"])
    assert result.exit_code == 2 and "not configured" in result.output


def test_check_without_config_names_what_is_missing(repo):
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1
    assert "DEVIN_API_KEY, DEVIN_ORG_ID not set" in result.output


def test_check_against_mock(repo, server, monkeypatch):
    configure(monkeypatch, server)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert "key and organisation accepted" in result.output
    assert KEY not in result.output


def test_check_with_wrong_key_explains_401(repo, server, monkeypatch):
    configure(monkeypatch, server, key="cog_wrong_fake_key")
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1
    assert "401" in result.output and "DEVIN_API_KEY" in result.output


def test_direct_path_start_wait_and_status(repo, server, monkeypatch):
    configure(monkeypatch, server)
    result = runner.invoke(app, ["start", "demo", "--wait", "--interval", "1", "--max-acu", "5"])
    assert result.exit_code == 0, result.output
    assert "https://github.com/acme/twin-lab/pull/" in result.output
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0 and "finished" in result.output


def test_start_then_status_refreshes(repo, server, monkeypatch):
    configure(monkeypatch, server)
    result = runner.invoke(app, ["start", "demo"])
    assert result.exit_code == 0, result.output
    job_id = next(
        line.split()[-1] for line in result.output.splitlines() if line.startswith("job:")
    )
    deadline = time.monotonic() + 5
    while True:
        result = runner.invoke(app, ["status", job_id])
        assert result.exit_code == 0, result.output
        if "state:         finished" in result.output or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert "state:         finished" in result.output, result.output
    assert "pull/" in result.output
    result = runner.invoke(app, ["cancel", job_id])
    assert result.exit_code == 1 and "already finished" in result.output


def test_local_yaml_is_read(repo, server):
    Path(repo / "twinlab.local.yaml").write_text(
        f"DEVIN_API_KEY: {KEY}\nDEVIN_ORG_ID: org-file\nDEVIN_BASE_URL: {server}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert "twinlab.local.yaml" in result.output
