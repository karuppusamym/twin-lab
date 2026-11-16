import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from labctl.devin import package as pkg
from labctl.devin.config import load_config

ROOT = Path(__file__).resolve().parents[3]


def make_repo(root: Path, app: str = "demo", brief_text: str = "# Build brief: demo\n") -> Path:
    """A minimal Twin Lab repository, plus things that must never be packaged."""
    (root / "apps" / app / "db").mkdir(parents=True)
    (root / "apps" / app / "pages").mkdir()
    (root / "docs").mkdir()
    (root / "automations").mkdir()
    (root / "lab").mkdir()
    (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (root / "docs" / "factory-playbook.md").write_text("# playbook\n", encoding="utf-8")
    a = root / "apps" / app
    (a / "BRIEF.md").write_text(brief_text, encoding="utf-8")
    (a / "db" / "schema.sql").write_text("CREATE TABLE t (id integer);\n", encoding="utf-8")
    (a / "model.yaml").write_text("app: demo\n", encoding="utf-8")
    (a / "app.yaml").write_text(
        "id: demo\ncopies:\n  ref: sha256:0123456789abcdef\n"
        "run:\n  api:\n    port: 8000\n    health: /api/health\n  env:\n    database_url: DATABASE_URL\n"
        "built_for:\n- uipath-demo\n- python-demo\n",
        encoding="utf-8",
    )
    (a / "pages" / "1.html").write_text("<html></html>", encoding="utf-8")
    (a / "pages" / "notes.txt").write_text("not a page", encoding="utf-8")
    (a / ".env").write_text("SECRET=x", encoding="utf-8")
    (a / "capture.har").write_text("{}", encoding="utf-8")
    shots = root / "evidence" / app / "screenshots"
    shots.mkdir(parents=True)
    (shots / "login.png").write_bytes(b"\x89PNG fake")
    (shots / "flow.har").write_text("{}", encoding="utf-8")
    (root / "evidence" / app / "session.har").write_text("{}", encoding="utf-8")
    (root / "evidence" / app / "ddl.sql").write_text("CREATE TABLE x();", encoding="utf-8")
    (root / "twinlab.local.yaml").write_text("DEVIN_API_KEY: cog_fake\n", encoding="utf-8")
    (root / ".env").write_text("X=1", encoding="utf-8")
    return root


def names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def test_package_holds_exactly_the_build_inputs(tmp_path):
    repo = make_repo(tmp_path / "repo")
    path = pkg.build(repo, "demo", tmp_path / "out", repo="acme/twin-lab")
    assert path.parent == tmp_path / "out"
    assert path.name.startswith("twinlab-demo-") and len(path.stem) == len("twinlab-demo-") + 8
    assert names(path) == {
        "DEVIN_TASK.md",
        "MANIFEST.json",
        "AGENTS.md",
        "docs/factory-playbook.md",
        "apps/demo/BRIEF.md",
        "apps/demo/model.yaml",
        "apps/demo/db/schema.sql",
        "apps/demo/app.yaml",
        "apps/demo/pages/1.html",
        "evidence/demo/screenshots/login.png",
    }


def test_package_never_holds_har_env_local_config_or_other_evidence(tmp_path):
    repo = make_repo(tmp_path / "repo")
    found = names(pkg.build(repo, "demo", tmp_path / "out"))
    assert not [n for n in found if n.endswith(".har")]
    assert not [n for n in found if Path(n).name.startswith(".env")]
    assert "twinlab.local.yaml" not in {Path(n).name for n in found}
    assert not [n for n in found if n.startswith("evidence/") and "/screenshots/" not in n]


@pytest.mark.parametrize(
    "rel,out",
    [
        ("a/b.har", True),
        ("x.HAR", True),
        (".env", True),
        ("apps/a/.env.local", True),
        ("twinlab.local.yaml", True),
        ("evidence/a/ddl.sql", True),
        ("evidence/a/screenshots/s.png", False),
        ("apps/a/BRIEF.md", False),
    ],
)
def test_exclusion_rules(rel, out):
    assert pkg.excluded(rel) is out


def test_manifest_lists_every_file_with_its_sha256(tmp_path):
    repo = make_repo(tmp_path / "repo")
    path = pkg.build(repo, "demo", tmp_path / "out", repo="acme/twin-lab")
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("MANIFEST.json"))
        listed = {f["path"]: f["sha256"] for f in manifest["files"]}
        assert set(listed) == set(zf.namelist()) - {"MANIFEST.json"}
        for name, digest in listed.items():
            assert hashlib.sha256(zf.read(name)).hexdigest() == digest
    assert manifest["app"] == "demo"
    assert manifest["repo"] == "acme/twin-lab"
    assert manifest["brief_digest"] == "sha256:0123456789abcdef"
    assert path.stem.endswith(manifest["package_digest"][:8])


def test_same_inputs_same_name_changed_brief_new_name(tmp_path):
    repo = make_repo(tmp_path / "repo")
    first = pkg.build(repo, "demo", tmp_path / "a", repo="acme/twin-lab")
    again = pkg.build(repo, "demo", tmp_path / "b", repo="acme/twin-lab")
    assert first.name == again.name
    (repo / "apps" / "demo" / "BRIEF.md").write_text("# changed\n", encoding="utf-8")
    assert pkg.build(repo, "demo", tmp_path / "c", repo="acme/twin-lab").name != first.name


@pytest.mark.parametrize("missing", ["BRIEF.md", "db/schema.sql"])
def test_refuses_without_brief_or_schema(tmp_path, missing):
    repo = make_repo(tmp_path / "repo")
    (repo / "apps" / "demo" / missing).unlink()
    with pytest.raises(pkg.PackageError, match=f"missing apps/demo/{missing}"):
        pkg.build(repo, "demo", tmp_path / "out")
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


def test_unknown_app_is_refused(tmp_path):
    repo = make_repo(tmp_path / "repo")
    with pytest.raises(pkg.PackageError, match="missing"):
        pkg.build(repo, "nope", tmp_path / "out")


def test_prompt_names_repo_branch_app_and_every_step(tmp_path):
    repo = make_repo(tmp_path / "repo")
    text = pkg.prompt(repo, "demo", "acme/twin-lab")
    for needle in [
        "`acme/twin-lab`",
        "devin/demo-<yyyymmdd-hhmm>",
        "AGENTS.md",
        "docs/factory-playbook.md",
        "apps/demo/BRIEF.md",
        "Write only under `apps/demo/`",
        "Never edit `apps/demo/BRIEF.md` or `apps/demo/db/schema.sql`",
        "change only the\n   `run:` section",
        "python -m pytest -q",
        "python -m labctl validate",
        "docker build -t twinlab/demo:dev apps/demo",
        "postgres:17",
        "-e DATABASE_URL=",
        "/api/health",
        "python -m labctl conformance uipath-demo",
        "python -m labctl conformance python-demo",
        "apps/demo/BUILD-NOTES.md",
        "exactly ONE pull request",
        "demo: demo app from brief sha256:0123456789abcdef",
        "stop and ask",
        "Report failures exactly as they are",
    ]:
        assert needle in text, needle


def test_prompt_is_the_task_file_in_the_package(tmp_path):
    repo = make_repo(tmp_path / "repo")
    path = pkg.build(repo, "demo", tmp_path / "out", repo="acme/twin-lab")
    with zipfile.ZipFile(path) as zf:
        assert zf.read("DEVIN_TASK.md").decode() == pkg.prompt(repo, "demo", "acme/twin-lab")


def test_prompt_without_repo_tells_devin_to_ask(tmp_path):
    repo = make_repo(tmp_path / "repo")
    assert "stop and ask which to use" in pkg.prompt(repo, "demo", None)


@pytest.mark.parametrize(
    "given",
    ["acme/twin-lab", "https://github.com/acme/twin-lab", "https://github.com/acme/twin-lab.git/"],
)
def test_repo_forms_are_normalised(given):
    assert pkg.normalize_repo(given) == "acme/twin-lab"


def test_bad_repo_is_refused():
    with pytest.raises(pkg.PackageError):
        pkg.normalize_repo("not a repo")


def test_the_showcase_packages(tmp_path):
    path = pkg.build(ROOT, "showcase", tmp_path, repo="acme/twin-lab")
    found = names(path)
    assert "apps/showcase/BRIEF.md" in found and "apps/showcase/db/schema.sql" in found
    assert "showcase: demo app from brief sha256:3adecca8a37e027b" in pkg.prompt(
        ROOT, "showcase", "acme/twin-lab"
    )


def test_config_env_wins_over_file_and_needs_key_and_org(tmp_path):
    (tmp_path / "twinlab.local.yaml").write_text(
        "DEVIN_API_KEY: cog_from_file\nDEVIN_ORG_ID: org-file\nGITHUB_REPO: acme/x\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path, env={"DEVIN_ORG_ID": "org-env"})
    assert cfg.configured
    assert cfg.api_key == "cog_from_file" and cfg.org_id == "org-env"
    assert cfg.sources["DEVIN_ORG_ID"] == "environment"
    assert cfg.state_dir == tmp_path / ".lab-state"
    assert cfg.base_url == "https://api.devin.ai"
    assert "cog_from_file" not in repr(cfg)
    assert not load_config(tmp_path / "none", env={"DEVIN_API_KEY": "cog_x"}).configured
    assert load_config(tmp_path / "none", env={}).missing == ["DEVIN_API_KEY", "DEVIN_ORG_ID"]
