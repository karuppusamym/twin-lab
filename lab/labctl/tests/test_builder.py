import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from test_devin_package import make_repo
from typer.testing import CliRunner

from labctl.builder import claude as claude_mod
from labctl.builder import cli as builder_cli
from labctl.builder import package as bpkg
from labctl.builder.base import jobs_for
from labctl.builder.claude import ClaudeBuilder
from labctl.builder.config import BuilderConfig, BuilderConfigError, load_config
from labctl.builder.devin import DevinBuilder
from labctl.builder.manual import ManualBuilder
from labctl.builder.select import select
from labctl.devin import package as devin_pkg

BRIEF_V1 = """# Build brief: demo

## Stack to pin

Front end: **Angular 17** with PrimeNG. Backend: ASP.NET Core 8; database: sqlserver.

## In scope

### Screens

**/login** (login, observed)

| Element | Used for | By | Basis | In evidence |
|---|---|---|---|---|
| `form#loginForm` | - |  | observed | yes |
| `input#username` | type | bot | observed | yes |

### API routes

| Route | Basis | Handler | Why |
|---|---|---|---|
| `GET /login` | observed | - | serves screen /login |

### Tables

| Table | Basis | Model | Why |
|---|---|---|---|
| `users` | declared | assumed | x |

## Out of scope
"""
BRIEF_V2 = (
    BRIEF_V1.replace(
        "| `input#username` | type | bot | observed | yes |\n",
        "| `input#username` | type | bot | observed | yes |\n| `button#btnSearch` | click | bot | observed | yes |\n",
    )
    .replace(
        "**/login** (login, observed)\n",
        "**/login** (login, observed)\n",
    )
    .replace(
        "### API routes",
        "**/loans** (list, observed)\n\n| Element | Used for | By | Basis | In evidence |\n|---|---|---|---|---|\n"
        "| `table#loanGrid` | - |  | observed | yes |\n\n### API routes",
    )
    .replace(
        "| `GET /login` | observed | - | serves screen /login |\n",
        "| `GET /login` | observed | - | serves screen /login |\n| `GET /api/loans` | observed | - | x |\n",
    )
)
SCHEMA_V1 = "CREATE TABLE users (id integer PRIMARY KEY, name varchar(20));\n"
SCHEMA_V2 = (
    "CREATE TABLE users (id integer PRIMARY KEY, name varchar(20), email varchar(80));\n"
    "CREATE TABLE loans (loan_id integer, amount numeric(10,2));\n"
)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return done.stdout.strip()


def git_repo(tmp_path: Path, brief: str = BRIEF_V1, schema: str = SCHEMA_V1) -> Path:
    root = make_repo(tmp_path / "repo", brief_text=brief)
    (root / "apps" / "demo" / "db" / "schema.sql").write_text(schema, encoding="utf-8")
    (root / ".gitignore").write_text("evidence/\n*.har\n.env\ntwinlab.local.yaml\n.lab-state/\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def task_of(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("BUILD_TASK.md").decode()


# --- package -----------------------------------------------------------------------------------


def test_package_has_build_task_and_is_deterministic(tmp_path):
    repo = make_repo(tmp_path / "repo")
    a = bpkg.build(repo, "demo", tmp_path / "a", repo="acme/twin-lab")
    b = bpkg.build(repo, "demo", tmp_path / "b", repo="acme/twin-lab")
    assert a.name == b.name
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        assert "BUILD_TASK.md" in za.namelist() and "DEVIN_TASK.md" not in za.namelist()
        for name in za.namelist():
            if name != "MANIFEST.json":  # holds the generation time
                assert za.read(name) == zb.read(name)
        assert [i.date_time for i in za.infolist()] == [(1980, 1, 1, 0, 0, 0)] * len(za.namelist())
        assert not [n for n in za.namelist() if n.endswith(".har") or n.startswith(".env")]
    assert devin_pkg.build is bpkg.build and devin_pkg.prompt is bpkg.prompt


def test_task_is_neutral_and_ordered(tmp_path):
    repo = make_repo(tmp_path / "repo", brief_text=BRIEF_V1)
    text = task_of(bpkg.build(repo, "demo", tmp_path / "out", repo="acme/twin-lab"))
    assert "devin" not in text.lower()
    order = [
        "## What a demo app is for",
        "## Same technology, same structure",
        "## Database",
        "## 1. Repository and branch",
        "## 3. Build",
        "## 4. Check",
        "## 7. Trust rules",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert "Angular 17" in text  # the brief's stack section is quoted
    for needle in [
        'same technology the brief\'s "Stack to pin"',
        "every recorded selector, element id, class",
        "iframe and shadow-DOM",
        "Only the **tables and data**",
        "Files go under `apps/demo/`",
        "do not edit `apps/demo/BRIEF.md`",
        "`build/demo-01234567`",
        "labelled `ai-built`",
        "python -m pytest -q",
    ]:
        assert needle in text, needle


@pytest.mark.parametrize(
    ("engine", "needle"),
    [
        ("postgres", "**PostgreSQL**"),
        ("sqlserver", "**SQL Server**"),
        ("sqlite", "**SQLite**"),
        ("reporting-dw", "named database server `reporting-dw`"),
    ],
)
def test_task_names_the_database_engine(tmp_path, engine, needle):
    repo = make_repo(tmp_path / "repo")
    app_yaml = repo / "apps" / "demo" / "app.yaml"
    app_yaml.write_text(
        f"id: demo\ncopies:\n  ref: sha256:0123456789abcdef\nstack:\n  db:\n    engine: {engine}\n",
        encoding="utf-8",
    )
    assert needle in bpkg.prompt(repo, "demo", None)


# --- enhancement mode ----------------------------------------------------------------------------


def test_new_app_has_no_enhancement_section(tmp_path):
    repo = make_repo(tmp_path / "repo")
    assert "What is new in this brief" not in bpkg.prompt(repo, "demo", None)


def test_inventory_reads_screens_elements_routes_tables_columns():
    inv = bpkg.brief_inventory(BRIEF_V2, SCHEMA_V2)
    assert inv["screens"] == {"/login", "/loans"}
    assert "/login input#username" in inv["elements"] and "/loans table#loanGrid" in inv["elements"]
    assert inv["routes"] == {"GET /login", "GET /api/loans"}
    assert inv["tables"] == {"users", "loans"}
    assert {"users.email", "loans.amount", "loans.loan_id"} <= inv["columns"]
    assert not any(c.endswith(".primary") for c in inv["columns"])


def test_enhancement_lists_what_changed_since_the_stored_baseline(tmp_path):
    repo = make_repo(tmp_path / "repo", brief_text=BRIEF_V1)
    (repo / "apps" / "demo" / "Dockerfile").write_text("FROM scratch\n")
    (repo / "apps" / "demo" / "db" / "schema.sql").write_text(SCHEMA_V1)
    bpkg.record_baseline(repo, "demo")
    assert (repo / ".lab-state" / "builds" / "demo" / "baseline" / "BRIEF.md").is_file()
    (repo / "apps" / "demo" / "BRIEF.md").write_text(BRIEF_V2, encoding="utf-8")
    (repo / "apps" / "demo" / "db" / "schema.sql").write_text(SCHEMA_V2)
    text = bpkg.prompt(repo, "demo", None)
    section = text[text.index("## What is new in this brief") : text.index("## 1. Repository")]
    assert "enhancement" in section and "without breaking" in section
    for needle in [
        "New screens (1)", "`/loans`", "`/loans table#loanGrid`", "`/login button#btnSearch`",
        "New routes (1)", "`GET /api/loans`", "New tables (1)", "`loans`", "`users.email`",
    ]:  # fmt: skip
        assert needle in section, needle
    assert "`/login`" not in section.split("New elements")[0]  # not new


def test_enhancement_falls_back_to_git_history(tmp_path):
    repo = git_repo(tmp_path)
    (repo / "apps" / "demo" / "Dockerfile").write_text("FROM scratch\n")
    (repo / "apps" / "demo" / "BRIEF.md").write_text(BRIEF_V2, encoding="utf-8")
    (repo / "apps" / "demo" / "db" / "schema.sql").write_text(SCHEMA_V2)
    text = bpkg.prompt(repo, "demo", None)  # working copy newer than HEAD
    assert "Compared with the brief at the last commit" in text and "`GET /api/loans`" in text
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "regenerate brief")
    text = bpkg.prompt(repo, "demo", None)  # committed: compare with the commit before
    assert "Compared with the brief at commit" in text and "`/loans`" in text


def test_enhancement_without_any_earlier_brief_says_so(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "apps" / "demo" / "Dockerfile").write_text("FROM scratch\n")
    assert "No earlier brief is stored" in bpkg.prompt(repo, "demo", None)


# --- selection -----------------------------------------------------------------------------------


def test_builder_selection(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    monkeypatch.delenv("TWINLAB_BUILDER", raising=False)
    monkeypatch.delenv("TWINLAB_CLAUDE_MODEL", raising=False)
    assert isinstance(select(repo), ManualBuilder)
    (repo / "twinlab.local.yaml").write_text(
        "builder:\n  name: claude\n  claude:\n    executable: myclaude\n    model: opus\n"
        "    args: [--verbose]\n",
        encoding="utf-8",
    )
    chosen = select(repo)
    assert isinstance(chosen, ClaudeBuilder)
    assert (chosen.config.claude_executable, chosen.config.claude_model) == ("myclaude", "opus")
    assert chosen.config.claude_args == ("--verbose",)
    monkeypatch.setenv("TWINLAB_BUILDER", "manual")
    assert isinstance(select(repo), ManualBuilder)  # environment wins over the file
    monkeypatch.setenv("TWINLAB_CLAUDE_MODEL", "sonnet")
    assert load_config(repo).claude_model == "sonnet"
    assert isinstance(select(repo, "devin"), DevinBuilder)  # explicit choice wins
    monkeypatch.setenv("TWINLAB_BUILDER", "gpt")
    with pytest.raises(BuilderConfigError):
        select(repo)
    (repo / "twinlab.local.yaml").write_text("builder: devin\n", encoding="utf-8")
    monkeypatch.delenv("TWINLAB_BUILDER")
    assert load_config(repo).name == "devin"


def test_default_model_is_unset_and_devin_needs_configuration(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    assert BuilderConfig().claude_model is None
    assert "--model" not in ClaudeBuilder(BuilderConfig(name="claude")).command()
    for key in ("DEVIN_API_KEY", "DEVIN_ORG_ID"):
        monkeypatch.delenv(key, raising=False)
    assert not DevinBuilder(repo).available()


def test_manual_builder_prints_what_a_person_does(tmp_path):
    repo = make_repo(tmp_path / "repo")
    package = bpkg.build(repo, "demo", tmp_path / "out")
    job = ManualBuilder().start(package, repo, "demo", "build/demo-01234567")
    assert job.state == "running" and job.builder == "manual"
    for needle in ["Claude Code", "Copilot", package.name, "BUILD_TASK.md", "build/demo-01234567"]:
        assert needle in job.note
    assert jobs_for(repo / ".lab-state", "demo")[0].id == job.id


# --- the claude builder, with a fake executable ----------------------------------------------------

FAKE = """
import json, os, sys
args = sys.argv[1:]
open(os.environ.get("FAKE_OUT", "fake-args.json"), "w").write(json.dumps({"args": args, "cwd": os.getcwd()}))
print("fake session output")
os.makedirs("apps/demo/app", exist_ok=True)
open("apps/demo/app/main.py", "w").write("print('built')\\n")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


def fake_claude(tmp_path: Path, **kw) -> ClaudeBuilder:
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")
    cfg = BuilderConfig(
        name="claude", claude_executable=sys.executable, claude_args=(str(script),), **kw
    )
    return ClaudeBuilder(cfg)


def test_claude_builder_runs_in_a_worktree_and_does_not_commit(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    (repo / "apps" / "demo" / "BRIEF.md").write_text(BRIEF_V2, encoding="utf-8")  # not committed
    package = bpkg.build(repo, "demo", tmp_path / "out")
    head = git(repo, "rev-parse", "HEAD")
    builder = fake_claude(tmp_path, claude_model="opus")
    assert builder.available()
    job = builder.start(package, repo, "demo", bpkg.branch_name(repo, "demo"))
    assert job.state == "finished" and job.exit_code == 0 and job.pid is None
    worktree = Path(job.worktree)
    assert worktree.is_dir() and job.branch.startswith("build/demo-")
    assert git(repo, "worktree", "list").count("build/demo-") == 1
    seen = json.loads((worktree / "fake-args.json").read_text())
    assert Path(seen["cwd"]).resolve() == worktree.resolve()
    args = seen["args"]
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--model") + 1] == "opus"
    prompt = args[args.index("-p") + 1]
    assert prompt.startswith(task_of(package)) and "Run mode" in prompt
    assert f"`{job.branch}`" in prompt and "Do NOT run `git commit`" in prompt
    assert "devin" not in prompt.lower()
    # the regenerated, uncommitted brief reached the worktree
    assert (worktree / "apps" / "demo" / "BRIEF.md").read_text() == BRIEF_V2
    assert "fake session output" in Path(job.log_path).read_text()
    assert Path(job.log_path).parent == Path(job.job_dir)
    assert "apps/demo/app/main.py" in job.changed_files
    # no commit, no push
    assert git(worktree, "rev-parse", "HEAD") == head and not job.committed and not job.pr_urls
    assert git(repo, "rev-parse", f"refs/heads/{job.branch}") == head
    # the saved record is what status() reports, and the baseline is stored for next time
    assert builder.status(job).state == "finished"
    assert jobs_for(repo / ".lab-state", "demo")[0].id == job.id
    assert (repo / ".lab-state" / "builds" / "demo" / "baseline" / "BRIEF.md").is_file()
    # a second build gets its own branch
    again = builder.start(package, repo, "demo", bpkg.branch_name(repo, "demo"))
    assert again.branch == job.branch + "-2"


def test_claude_builder_failure_is_recorded(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    package = bpkg.build(repo, "demo", tmp_path / "out")
    monkeypatch.setenv("FAKE_EXIT", "3")
    job = fake_claude(tmp_path).start(package, repo, "demo", "build/demo-x")
    assert job.state == "failed" and job.exit_code == 3 and "code 3" in job.last_error
    assert not (repo / ".lab-state" / "builds" / "demo" / "baseline").exists()


def test_claude_builder_missing_executable_fails_cleanly(tmp_path):
    repo = git_repo(tmp_path)
    package = bpkg.build(repo, "demo", tmp_path / "out")
    builder = ClaudeBuilder(BuilderConfig(name="claude", claude_executable="no-such-claude-xyz"))
    assert not builder.available() and "not found" in builder.availability()
    job = builder.start(package, repo, "demo", "build/demo-y")
    assert job.state == "failed" and "could not start" in job.last_error


def test_claude_builder_with_pr_commits_pushes_and_opens_it(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    git(repo, "remote", "add", "origin", str(origin))
    calls = []

    def fake_gh(cwd, args):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, stdout="https://github.com/acme/twin-lab/pull/7\n", stderr=""
        )

    monkeypatch.setattr(claude_mod, "_gh", fake_gh)
    for who in ("AUTHOR", "COMMITTER"):  # the builder commits as whoever git is set up for
        monkeypatch.setenv(f"GIT_{who}_NAME", "Twin Lab test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    package = bpkg.build(repo, "demo", tmp_path / "out")
    builder = fake_claude(tmp_path)
    builder.open_pr = True
    job = builder.start(package, repo, "demo", bpkg.branch_name(repo, "demo"))
    assert job.committed and job.pr_urls == ["https://github.com/acme/twin-lab/pull/7"]
    assert "--label" in calls[0] and "ai-built" in calls[0]
    assert git(origin, "rev-parse", f"refs/heads/{job.branch}") == git(
        Path(job.worktree), "rev-parse", "HEAD"
    )
    assert git(Path(job.worktree), "log", "-1", "--format=%s").startswith("demo: build from brief")


# --- the command line ------------------------------------------------------------------------------


def test_cli_package_start_status(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    monkeypatch.setenv("LABCTL_ROOT", str(repo))
    monkeypatch.delenv("TWINLAB_BUILDER", raising=False)
    runner = CliRunner()
    out = runner.invoke(builder_cli.app, ["package", "demo"])
    assert out.exit_code == 0, out.output
    assert "BUILD_TASK.md" in out.output and "Copilot" in out.output
    out = runner.invoke(builder_cli.app, ["start", "demo"])  # default builder: manual
    assert out.exit_code == 0 and "builder:" in out.output and "manual" in out.output
    monkeypatch.setenv("TWINLAB_CLAUDE_EXECUTABLE", "no-such-claude-xyz")  # never the real CLI
    out = runner.invoke(builder_cli.app, ["start", "demo", "--builder", "claude"])
    assert out.exit_code == 2 and "not available" in out.output
    out = runner.invoke(builder_cli.app, ["status", "demo"])
    assert out.exit_code == 0 and "manual" in out.output
    url = "https://github.com/acme/twin-lab/pull/9"
    out = runner.invoke(builder_cli.app, ["attach-pr", "demo", url])
    assert out.exit_code == 0 and url in out.output and "finished" in out.output
    assert runner.invoke(builder_cli.app, ["attach-pr", "demo", "nope"]).exit_code == 2
