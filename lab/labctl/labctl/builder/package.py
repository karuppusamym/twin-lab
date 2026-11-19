"""The build package: one zip holding the task and everything a coding agent needs.

Builder-neutral. A person gives the zip to any coding agent (Claude Code, GitHub Copilot's coding
agent, Devin, ...) and pastes `BUILD_TASK.md`; the Claude builder passes the same text to a
headless session; the Devin API path sends it as the session prompt. Nothing in the task depends
on one product's features. Raw evidence never goes in: no HAR files, nothing under `evidence/`
but the app's screenshots, no `.env` files, no `twinlab.local.yaml`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import yaml

from labctl.builder import complete

FORMAT = "twinlab-build-package/1"
TASK_FILE = "BUILD_TASK.md"
MANIFEST_FILE = "MANIFEST.json"
SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_APP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed, so the same inputs give the same file contents


class PackageError(Exception):
    pass


class PackageWarning(UserWarning):
    """A package was written although the completeness check found gaps."""


def normalize_repo(repo: str) -> str:
    """`owner/name` from `owner/name`, `https://github.com/owner/name` or `...name.git`."""
    text = repo.strip()
    text = re.sub(r"^(https?://)?(www\.)?github\.com/", "", text)
    text = re.sub(r"^git@github\.com:", "", text)
    text = text.removesuffix("/").removesuffix(".git")
    if not _REPO_RE.match(text):
        raise PackageError(f"GitHub repository must look like owner/name, got {repo!r}")
    return text


def excluded(rel: str) -> bool:
    """True for a repository-relative path that must never be packaged."""
    path = PurePosixPath(rel)
    name = path.name.lower()
    if name.endswith(".har") or name == "twinlab.local.yaml":
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    parts = path.parts
    if parts and parts[0] == "evidence":
        return not (len(parts) >= 4 and parts[2] == "screenshots")
    return False


def _app_yaml(repo_root: Path, app_id: str) -> dict:
    path = repo_root / "apps" / app_id / "app.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackageError(f"{path}: cannot read: {exc}") from exc
    return data if isinstance(data, dict) else {}


def brief_digest(repo_root: Path, app_id: str) -> str:
    """The brief's digest as `labctl brief` wrote it into app.yaml (`copies.ref`).

    Falls back to a digest of BRIEF.md when app.yaml has none.
    """
    ref = ((_app_yaml(repo_root, app_id).get("copies") or {}).get("ref") or "").strip()
    if ref:
        return ref
    brief = repo_root / "apps" / app_id / "BRIEF.md"
    return "sha256:" + hashlib.sha256(_content(brief)).hexdigest()[:16]


def _check_inputs(repo_root: Path, app_id: str) -> None:
    if not _APP_RE.match(app_id):
        raise PackageError(f"app id must be lower-case letters, digits, - or _, got {app_id!r}")
    app_dir = repo_root / "apps" / app_id
    missing = [
        f"apps/{app_id}/{rel}"
        for rel in ("BRIEF.md", "db/schema.sql")
        if not (app_dir / rel).is_file()
    ]
    if missing:
        raise PackageError(
            f"cannot package {app_id}: missing {', '.join(missing)}. "
            f"Generate the brief first: labctl brief {app_id} ..."
        )


def _collect(repo_root: Path, app_id: str) -> list[tuple[str, Path]]:
    """(path in the zip, file on disk), repository-relative, sorted, exclusions applied."""
    app = f"apps/{app_id}"
    wanted = [
        f"{app}/BRIEF.md",
        f"{app}/model.yaml",
        f"{app}/db/schema.sql",
        f"{app}/db/seed.sql",
        f"{app}/db/seed.json",
        f"{app}/app.yaml",
        f"{app}/behaviours.json",
        f"{app}/navigation.json",
        "AGENTS.md",
        "docs/factory-playbook.md",
    ]
    found: dict[str, Path] = {}
    for rel in wanted:
        path = repo_root / rel
        if path.is_file():
            found[rel] = path
    for rel in ("AGENTS.md", "docs/factory-playbook.md"):
        if rel not in found:
            raise PackageError(f"cannot package {app_id}: {rel} is missing from the repository")
    pages = repo_root / app / "pages"
    if pages.is_dir():
        for path in sorted(pages.glob("*.html")):
            if path.is_file() and not path.is_symlink():
                found[f"{app}/pages/{path.name}"] = path
    # Reference snapshots of pages a link check found and the recording never visited.
    unrecorded = repo_root / app / "unrecorded"
    for path in sorted(unrecorded.glob("*.html")) if unrecorded.is_dir() else []:
        if path.is_file() and not path.is_symlink():
            found[f"{app}/unrecorded/{path.name}"] = path
    # The API catalogue: one file per endpoint the pages called (labctl.apicatalog).
    api = repo_root / app / "api"
    if api.is_dir():
        for path in sorted(api.glob("*.json")):
            if path.is_file() and not path.is_symlink():
                found[f"{app}/api/{path.name}"] = path
    # The recorded steps in order and the sequence across applications (structure, no value),
    # and the requested flows for this app (drafts from a recording included).
    steps = repo_root / app / "steps"
    if steps.is_dir():
        for path in sorted([*steps.glob("*.json"), *steps.glob("*.sequence.yaml"),
                            *steps.glob("*.values.yaml")]):  # fmt: skip
            if path.is_file() and not path.is_symlink():
                found[f"{app}/steps/{path.name}"] = path
    for path in (
        sorted((repo_root / "flows").glob("*.yaml")) if (repo_root / "flows").is_dir() else []
    ):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("app") == app_id and not path.is_symlink():
            found[f"flows/{path.name}"] = path
    # The look (labctl.lookfeel): style sheets, design tokens, rendered pictures, texts.
    from labctl import lookfeel

    found.update(lookfeel.package_files(repo_root, app_id))
    # Field rules (labctl.pagerules): rules.json, and the scripts only on the owner's request.
    from labctl import pagerules

    found.update(pagerules.package_files(repo_root, app_id))
    shots = repo_root / "evidence" / app_id / "screenshots"
    if shots.is_dir():
        for path in sorted(shots.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() in SCREENSHOT_SUFFIXES
            ):
                found[path.relative_to(repo_root).as_posix()] = path
    return sorted((rel, path) for rel, path in found.items() if not excluded(rel))


def _run_env(conf: dict) -> tuple[str | None, int | None, str | None]:
    run = conf.get("run") or {}
    env = (run.get("env") or {}).get("database_url")
    port = (run.get("api") or {}).get("port") or (run.get("web") or {}).get("port")
    health = (run.get("api") or {}).get("health")
    return env, port, health


def _db_example(engine: str, app_id: str, db_var: str, port: str) -> str:
    """Commands to try the app against a throwaway database of the right engine."""
    net, db, app = f"twinlab-{app_id}", f"twinlab-{app_id}-db", f"twinlab-{app_id}-app"
    e = (engine or "postgres").lower()
    if e not in ("mssql", "sqlserver", "sql server", "sqlite", "oracle", "postgres"):
        e = "postgres"  # a named server: any engine the lab maps it to; try a local Postgres
    if e in ("mssql", "sqlserver", "sql server"):
        lines = [
            f"docker network create {net}",
            f"docker run -d --name {db} --network {net} -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=Lab_pass1 mcr.microsoft.com/mssql/server:2022-latest",
            f'docker exec {db} /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P Lab_pass1 -Q "CREATE DATABASE [{app_id}]"',
            f"docker run -d --name {app} --network {net} -p 18080:{port} -e {db_var}=mssql+pymssql://sa:Lab_pass1@{db}:1433/{app_id} twinlab/{app_id}:dev",
        ]
    elif e == "sqlite":
        lines = [
            f"docker volume create {net}",
            f"docker run -d --name {app} -p 18080:{port} -v {net}:/data -e {db_var}=sqlite:////data/{app_id}.db twinlab/{app_id}:dev",
        ]
    elif e == "oracle":
        lines = [
            f"docker network create {net}",
            f"docker run -d --name {db} --network {net} -e ORACLE_PASSWORD=lab -e APP_USER=lab -e APP_USER_PASSWORD=lab gvenzl/oracle-free:slim",
            f"docker run -d --name {app} --network {net} -p 18080:{port} -e {db_var}=oracle+oracledb://lab:lab@{db}:1521/?service_name=FREEPDB1 twinlab/{app_id}:dev",
        ]
    else:
        lines = [
            f"docker network create {net}",
            f"docker run -d --name {db} --network {net} -e POSTGRES_USER=lab -e POSTGRES_PASSWORD=lab -e POSTGRES_DB={app_id} postgres:17",
            f"docker run -d --name {app} --network {net} -p 18080:{port} -e {db_var}=postgresql://lab:lab@{db}:5432/{app_id} twinlab/{app_id}:dev",
        ]
    return "   ```bash\n" + "\n".join(f"   {line}" for line in lines) + "\n   ```"


def default_state_dir(repo_root: Path) -> Path:
    """`STATE_DIR` from the environment or twinlab.local.yaml, else `<repo>/.lab-state`."""
    value = os.environ.get("STATE_DIR", "").strip()
    if not value:
        local = Path(repo_root) / "twinlab.local.yaml"
        if local.is_file():
            try:
                data = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                data = {}
            if isinstance(data, dict):
                for key, val in data.items():
                    if str(key).upper() == "STATE_DIR" and val:
                        value = str(val).strip()
    path = Path(value or ".lab-state")
    return path if path.is_absolute() else Path(repo_root) / path


def digest8(digest: str) -> str:
    """First 8 hex digits of a brief digest such as `sha256:0123456789abcdef`."""
    hexes = re.sub(r"[^0-9a-f]", "", digest.split(":", 1)[-1].lower())
    return (hexes or "00000000")[:8]


def branch_name(repo_root: Path, app_id: str) -> str:
    return f"build/{app_id}-{digest8(brief_digest(Path(repo_root), app_id))}"


# --- what changed since the last build --------------------------------------------------------

_SKIP_COLUMN = {"primary", "foreign", "constraint", "unique", "check", "key", "index", "exclude"}


def _section(text: str, heading: str, level: str = "## ") -> str:
    """The body under `heading` up to the next heading of the same or a higher level."""
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    depth = len(level.strip())
    for line in lines:
        if not inside:
            inside = line.strip() == level + heading
            continue
        if inside:
            m = re.match(r"^(#+) ", line)
            if m and len(m.group(1)) <= depth:
                break
            out.append(line)
    return "\n".join(out).strip()


def brief_inventory(brief: str, schema: str = "") -> dict[str, set[str]]:
    """What a brief and its schema define: screens, elements, routes, tables, columns."""
    screens: set[str] = set()
    elements: set[str] = set()
    scope = _section(brief, "Screens", "### ")
    current = ""
    for line in scope.splitlines():
        m = re.match(r"^\*\*([^*]+)\*\*\s*\(", line)
        if m:
            current = m.group(1).strip()
            screens.add(current)
            continue
        row = re.match(r"^\|\s*(`[^`]+`|\*[^*|]+\*)\s*\|", line)
        if current and row and not row.group(1).startswith("`Element"):
            elements.add(f"{current} {row.group(1).strip('`*')}")
    routes = {
        m.group(1).strip()
        for m in re.finditer(r"^\|\s*`([^`]+)`\s*\|", _section(brief, "API routes", "### "), re.M)
        if m.group(1).strip() not in ("Route",)
    }
    tables = {
        m.group(1).strip()
        for m in re.finditer(r"^\|\s*`([^`]+)`\s*\|", _section(brief, "Tables", "### "), re.M)
    }
    columns: set[str] = set()
    for name, body in _create_tables(schema):
        tables.add(name)
        columns.update(f"{name}.{c}" for c in _columns(body))
    return {
        "screens": screens,
        "elements": elements,
        "routes": routes,
        "tables": tables,
        "columns": columns,
    }


def _create_tables(sql: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"\[\]`]+)\s*\(", sql, re.I
    ):
        depth, i = 1, m.end()
        while i < len(sql) and depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        name = m.group(1).strip('"[]`').split(".")[-1].strip('"[]`')
        found.append((name.lower(), sql[m.end() : i - 1]))
    return found


def _columns(body: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    cols = []
    for part in parts:
        tokens = part.strip().split()
        if tokens and tokens[0].strip('"[]`').lower() not in _SKIP_COLUMN:
            cols.append(tokens[0].strip('"[]`').lower())
    return cols


def _git_show(repo_root: Path, spec: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), "show", spec],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (
        done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        if done.returncode == 0
        else None
    )


def _git_commits(repo_root: Path, rel: str) -> list[str]:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-n", "2", "--format=%H", "--", rel],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return done.stdout.decode().split() if done.returncode == 0 else []


def baseline_dir(repo_root: Path, app_id: str, state_dir: Path | None = None) -> Path:
    return Path(state_dir or default_state_dir(repo_root)) / "builds" / app_id / "baseline"


def record_baseline(repo_root: Path, app_id: str, state_dir: Path | None = None) -> Path:
    """Remember the brief (and schema) a build was started from, for the next enhancement."""
    repo_root = Path(repo_root)
    target = baseline_dir(repo_root, app_id, state_dir)
    target.mkdir(parents=True, exist_ok=True)
    app = repo_root / "apps" / app_id
    (target / "BRIEF.md").write_bytes(_content(app / "BRIEF.md"))
    schema = app / "db" / "schema.sql"
    (target / "schema.sql").write_bytes(_content(schema) if schema.is_file() else b"")
    (target / "baseline.json").write_text(
        json.dumps(
            {
                "brief_digest": brief_digest(repo_root, app_id),
                "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def previous_brief(
    repo_root: Path, app_id: str, state_dir: Path | None = None
) -> tuple[str, str, str] | None:
    """(brief text, schema text, where it came from) for the build before this brief, or None.

    1. The snapshot a builder stored under `<state>/builds/<app>/baseline/` when it last started.
    2. The brief committed at HEAD, when the working copy differs from it.
    3. The committed brief before the latest change to `apps/<app>/BRIEF.md`.
    """
    repo_root = Path(repo_root)
    now = _content(repo_root / "apps" / app_id / "BRIEF.md").decode("utf-8", "replace")
    snap = baseline_dir(repo_root, app_id, state_dir)
    if (snap / "BRIEF.md").is_file():
        text = (snap / "BRIEF.md").read_bytes().decode("utf-8", "replace")
        schema = (
            (snap / "schema.sql").read_text(encoding="utf-8")
            if (snap / "schema.sql").is_file()
            else ""
        )
        return text, schema, "the brief stored when the last build started"
    rel = f"apps/{app_id}/BRIEF.md"
    schema_rel = f"apps/{app_id}/db/schema.sql"
    head = _git_show(repo_root, f"HEAD:{rel}")
    if head is not None and head != now:
        return (
            head,
            _git_show(repo_root, f"HEAD:{schema_rel}") or "",
            "the brief at the last commit",
        )
    commits = _git_commits(repo_root, rel)
    if len(commits) == 2:
        old = _git_show(repo_root, f"{commits[1]}:{rel}")
        if old is not None:
            schema = _git_show(repo_root, f"{commits[1]}:{schema_rel}") or ""
            return old, schema, f"the brief at commit {commits[1][:10]}"
    return None


def _items(label: str, items: list[str], cap: int = 25) -> list[str]:
    if not items:
        return []
    shown = [f"  - `{i}`" for i in sorted(items)[:cap]]
    more = len(items) - cap
    if more > 0:
        shown.append(f"  - ... and {more} more")
    return [f"- {label} ({len(items)}):", *shown]


def whats_new(repo_root: Path, app_id: str, state_dir: Path | None = None) -> str:
    """A short "what is new" section comparing this brief with the one the app was built from."""
    repo_root = Path(repo_root)
    old = previous_brief(repo_root, app_id, state_dir)
    if old is None:
        return (
            "No earlier brief is stored, so this task cannot list what changed. Run "
            f"`git log -p -- apps/{app_id}/BRIEF.md` and compare the brief with what the app does "
            "today before you change anything."
        )
    old_brief, old_schema, source = old
    app = repo_root / "apps" / app_id
    new_brief = _content(app / "BRIEF.md").decode("utf-8", "replace")
    schema_file = app / "db" / "schema.sql"
    new_schema = _content(schema_file).decode("utf-8", "replace") if schema_file.is_file() else ""
    before, after = brief_inventory(old_brief, old_schema), brief_inventory(new_brief, new_schema)
    lines: list[str] = []
    labels = {
        "screens": "New screens",
        "elements": "New elements (screen, then selector or label)",
        "routes": "New routes",
        "tables": "New tables",
        "columns": "New columns (table.column)",
    }
    for key, label in labels.items():
        lines += _items(label, sorted(after[key] - before[key]))
    removed: list[str] = []
    for key, label in (
        ("screens", "screens"),
        ("elements", "elements"),
        ("routes", "routes"),
        ("tables", "tables"),
        ("columns", "columns"),
    ):
        gone = sorted(before[key] - after[key])
        if gone:
            removed += _items(f"No longer in the brief: {label}", gone, cap=10)
    head = f"Compared with {source}."
    if not lines and not removed:
        if old_brief.strip() == new_brief.strip():
            return (
                head + " The brief has not changed: only check that the existing app still passes."
            )
        return (
            head + " No screen, element, route, table or column was added or removed; wording, "
            "rules or behaviour notes changed. Read the brief against the app and fix the "
            "differences."
        )
    return "\n".join([head, "", *lines, *removed])


def _stack_text(repo_root: Path, app_id: str) -> str:
    brief = repo_root / "apps" / app_id / "BRIEF.md"
    text = _content(brief).decode("utf-8", "replace")
    body = _section(text, "Stack to pin")
    return "\n".join(f"   > {line}" if line else "   >" for line in body.splitlines()[:14])


CROSS_APP = "Part of a flow across applications"


def _cross_app_text(repo_root: Path, app_id: str) -> str:
    """The brief's section on the flow across applications this app is part of, for the task
    ("" for an app recorded on its own)."""
    text = _content(repo_root / "apps" / app_id / "BRIEF.md").decode("utf-8", "replace")
    body = _section(text, CROSS_APP)
    if not body:
        return ""
    return (f"## {CROSS_APP}\n\n{body}\n\nThe other applications are built from their own "
            "packages: do not build them here, and do not stub them inside this app.\n\n")  # fmt: skip


def _engine_text(engine: str, db_var: str) -> str:
    e = (engine or "postgres").lower()
    if e in ("postgres", "postgresql"):
        return "**PostgreSQL**: include its driver."
    if e in ("mssql", "sqlserver", "sql server"):
        return "**SQL Server**: include its driver (for example `mssql+pymssql://`)."
    if e == "sqlite":
        return (
            "**SQLite**: a single database file, no server. Read its location from "
            f"`{db_var}` (`sqlite:///<path>`) and keep the file outside the image."
        )
    if e == "oracle":
        return "**Oracle**: include its driver."
    return (
        f"the lab's **named database server `{engine}`**. Which engine that is depends on the "
        "lab's server list, so do not hard-code one: read `DB_ENGINE` at start-up and connect "
        "with the matching driver (include drivers for PostgreSQL and SQL Server)."
    )


def browser_only(conf: dict) -> bool:
    """app.yaml says the business data is in the browser (R07): no database, no tables."""
    return (((conf.get("stack") or {}).get("data") or {}).get("kind")) == "browser"


def _serve_example(app_id: str, port: str) -> str:
    app = f"twinlab-{app_id}-app"
    return f"   ```bash\n   docker run -d --name {app} -p 18080:{port} twinlab/{app_id}:dev\n   ```"


def _browser_database_text(conf: dict, app: str) -> str:
    data = (conf.get("stack") or {}).get("data") or {}
    kinds = data.get("storage") or data.get("referenced") or []
    keys = data.get("keys") or []
    named = [k for k in data.get("script_keys") or [] if k not in keys]
    observed = bool(data.get("storage"))
    persistence = data.get("persistence") or ("storage" if observed else "transient")
    if persistence == "storage":
        where = (
            "Its business data is kept in browser storage, "
            + ", ".join(f"`{k}`" for k in kinds)
            + " (observed)"
            + (
                ", under the keys " + ", ".join(f"`{k}`" for k in keys)
                if keys
                else ", key names not observed"
                + (
                    " (the page's scripts name " + ", ".join(f"`{k}`" for k in named) + ")"
                    if named
                    else ""
                )
            )
            + ". Keep it there, under the same keys and the same record shape as the brief's "
            "*Browser storage* section; where the brief says a key or shape was not observed, "
            "choose one, write the choice in BUILD-NOTES.md and do not claim it was recorded."
        )
    else:
        refs = (
            "storage "
            + ", ".join(f"`{k}`" for k in kinds)
            + " is referenced by the page's scripts but not observed"
            if kinds
            else "no browser storage was observed"
        )
        where = (
            "No server write was observed, and nothing shows that the data persists in "
            f"`localStorage`, `sessionStorage` or anywhere else ({refs}): the absence of server "
            "writes is not evidence of browser storage. Follow the brief's *Browser storage* "
            "section: keep the data as the recording shows it (page state or values in the "
            "address), add storage only if the owner says the real application keeps it there, "
            "and write that decision in BUILD-NOTES.md."
        )
    return (
        "**There is no server database.** "
        + where
        + " The container only serves the files: its health is hosting health, not "
        f"business-data health. `{app}/db/schema.sql` has no table: create no table, no "
        "server-side store and no endpoint for the data, and do not read a database address."
    )


def _package_only_text(app_id: str, app: str, engine: str, has_steps: bool, db_var: str,
                       nav: bool = False, api: bool = False) -> str:  # fmt: skip
    """What applies to a builder who has the zip and no repository: where to build, what of the
    steps below to skip, and how to check the app without labctl."""
    e = (engine or "").lower()
    schema_check = (
        f"`python -c \"import sqlite3; sqlite3.connect(':memory:').executescript(open('{app}/db/"
        "schema.sql').read())\"` (the schema loads in SQLite as it is)"
        if e == "sqlite"
        else f"apply `{app}/db/schema.sql` to a throwaway database of the engine (as step 3.6 says)"
    )
    steps_check = (
        f" Walk `{app}/steps/*.json` in order against the running app on the seed (by hand or "
        "with a short browser script of your own): perform each step's action on its selector "
        "(or its `target`), type the value `steps/<recording>.values.yaml` gives the step (where "
        "there is none, a synthetic value of the shape given), and check that the page that "
        "follows (`then`), the requests, the messages and the rows are those the step lists, the "
        "values file records and the brief's *What each recorded action did* shows."
        if has_steps
        else ""
    )
    extra_check = (
        (f" Then walk `{app}/navigation.json`: every transition, dialog, tab, frame and popup "
         "window opens and closes on its element, and every element marked as used does what "
         "its `used` entries say." if nav else "")
        + (f" Call every endpoint of `{app}/api/` with the request of each recorded call "
           "(`calls`), in order, on a fresh seed: the status and the keys of the answer are "
           "those of the call." if api else "")
    )  # fmt: skip
    return f"""## Working from this package alone

If you were given only this package and no repository (no `.git`, no `lab/` folder):

1. Unpack the zip into a new, empty folder and work there: it already has the repository's
   layout (`AGENTS.md`, `docs/`, `{app}/`). Build under `{app}/` as below; check each file
   against its sha256 in `MANIFEST.json` first.
2. Skip what needs the repository: the branch and the pull request (sections 1 and 6), the
   repository's `python -m pytest -q`, `python -m labctl validate`, `labctl conformance`,
   `labctl lab ...` and `labctl data load-across`. Hand back the folder (or a zip of `{app}/`)
   with `BUILD-NOTES.md`, and say there which checks you could not run.
3. Check the app without labctl: your own tests for it; {schema_check}; `docker build` if Docker
   is available, else run the app directly with `{db_var}` set; then open every screen in the
   brief and check that each listed selector resolves, that each route answers on its method
   and path, and that each message appears with its exact text where the brief says.{steps_check}{extra_check}
4. There is nobody to ask. Where this task says "stop and ask", working from the package alone
   means: choose the simplest behaviour that fits the package, and report it under Assumptions
   in `BUILD-NOTES.md`.

"""


def _done_text(app: str, packaged: list[str], browser: bool, values: bool) -> str:
    """The numbered list of what must hold for the build to be done."""
    items = [
        "Every screen of the brief opens at its address and has every element of its snapshot, "
        "with the same selectors, ids and texts.",
    ]
    if f"{app}/navigation.json" in packaged:
        items += [
            f"Every way from screen to screen, and every dialog, tab, frame and popup window "
            f"in `{app}/navigation.json`, works on the same element.",
            "Every element the recording used does what the brief's *Actions on each screen* "
            "says: the same request, the same message, the same next page.",
        ]
    if f"{app}/api/catalogue.json" in packaged:
        items.append(f"Every endpoint in `{app}/api/` answers on the same method and path, with "
                     "the same keys, types, codes and statuses.")  # fmt: skip
    if browser:
        items.append("The data is kept where the brief's *Browser storage* section says, under "
                     "the same keys.")  # fmt: skip
    else:
        items += [
            "Sign-in, the session and sign-out work as the brief's *Sign-in and session* says "
            "(where the brief has that section).",
            "Users, sessions and every record are stored in and read from the database. The "
            "tables come from `db/schema.sql`; the seed is loaded at start.",
        ]
    if values:
        items.append(f"The recorded steps, replayed in order with the values of "
                     f"`{app}/steps/*.values.yaml`, give at each step the outcome the recording "
                     "showed. This is the acceptance run.")  # fmt: skip
    items.append(f"`{app}/BUILD-NOTES.md` says what you built, what you checked and what you "
                 "assumed.")  # fmt: skip
    return "\n".join(f"{n}. {text}" for n, text in enumerate(items, 1))


def _reading_order(app: str, app_id: str, packaged: list[str]) -> str:
    """The exact order to read the package in: only files that are in it, plus the line kept
    for the look of the screens (`look/`, written by another step when it has run)."""
    have = set(packaged)

    def any_under(prefix: str) -> bool:
        return any(p.startswith(prefix) for p in have)

    rows: list[str] = [
        "`BUILD_TASK.md` (this file), then `INDEX.md`: every file and what it is for.",
        "`AGENTS.md`, then `docs/factory-playbook.md`: the lab's rules. They apply to everything.",
        f"`{app}/BRIEF.md`, whole, top to bottom. It is the specification. Its sections: the "
        "scope, the stack, each screen with its elements and fields, the API, the tables, what "
        "each recorded action did, the seed and the replay values, sign-in and session, the "
        "navigation map, the actions of each screen, how it runs in the lab, the acceptance "
        "checks, the open questions.",
    ]
    if any_under(f"{app}/pages/"):
        rows.append(f"`{app}/pages/*.html`: one snapshot per screen. The structure, ids, classes "
                    "and interface text are real; data is masked (letters x, digits 9). Build "
                    "each screen from its snapshot.")  # fmt: skip
    if any_under(f"{app}/look/"):
        rows.append(f"`{app}/look/` (style sheets, `theme.json`, one rendered picture per screen): "
                    "how the screens look. Match each screen to its picture.")  # fmt: skip
    else:
        rows.append("The package has no pictures or style sheets of the screens: the styles "
                    "inside the snapshots are the reference.")  # fmt: skip
    if f"{app}/navigation.json" in have:
        rows.append(f"`{app}/navigation.json`: every screen, each way in and out, dialogs, tabs, "
                    "frames and popup windows, and every element of each screen with what "
                    "happened when the recording used it.")  # fmt: skip
    if f"{app}/rules.json" in have:
        rows.append(f"`{app}/rules.json`: per screen and field, every validation rule with its "
                    "message, level and evidence, rules between fields, what the page does "
                    "while a person types or chooses, its state on load, session timers"
                    + (f"; `{app}/scripts/`: the application's own scripts, carried on the "
                       "owner's request, for reference" if any_under(f"{app}/scripts/") else "")
                    + ".")  # fmt: skip
    if f"{app}/api/catalogue.json" in have:
        rows.append(f"`{app}/api/catalogue.json`, then each `{app}/api/*.json`: one file per "
                    "endpoint, with its parameters, schemas and every recorded call with "
                    "synthetic values.")  # fmt: skip
    if f"{app}/db/schema.sql" in have:
        seed = (f", then `{app}/db/seed.sql` (the same rows as JSON: `db/seed.json`): the rows "
                "to load at start" if f"{app}/db/seed.sql" in have else "")  # fmt: skip
        rows.append(f"`{app}/db/schema.sql`: the tables{seed}.")
    if any_under(f"{app}/steps/"):
        rows.append(f"`{app}/steps/<recording>.json`: the recorded steps in order. "
                    f"`{app}/steps/<recording>.values.yaml`: the value the acceptance replay "
                    "types at each step and the outcome the recording showed. "
                    "`<recording>.sequence.yaml`, when present: the order across applications.")  # fmt: skip
    if any_under("flows/"):
        rows.append("`flows/*.yaml`: the flow in words.")
    rows.append(f"`{app}/behaviours.json`, `{app}/model.yaml`, `{app}/app.yaml`: reference. "
                "Look things up in them; the brief is generated from them.")  # fmt: skip
    if any_under(f"evidence/{app_id}/screenshots/"):
        rows.append(f"`evidence/{app_id}/screenshots/`: pictures of the screens, for reference.")
    return "\n".join(f"{n}. {text}" for n, text in enumerate(rows, 1))


def _rules_text(app: str, packaged: list[str], browser: bool, seed_loaded: bool) -> str:
    """The rules a build may never break, numbered, each one sentence first."""
    nav = f"{app}/navigation.json" in packaged
    api = f"{app}/api/catalogue.json" in packaged
    rules = [
        "**Same selectors and ids.** Keep **every recorded selector, element id, class, "
        "`name`, iframe and shadow-DOM structure** exactly as the brief's screen tables and "
        f"`{app}/pages/*.html` show them. Automations find elements by these, and a changed id "
        "breaks them silently.",
        "**Same literal texts.** Write every label, heading, button, option, link and message "
        "exactly as the snapshots and the brief have it, letter for letter. Text shown masked "
        "(`Xxxx`, `9999`) is data: show the record's value there.",
        "**Same addresses and API contract.** Every screen keeps its path. Every route keeps "
        "its method, path, parameters, field names, types, codes and statuses"
        + (f" (`{app}/api/`)." if api else "."),
        "**Every navigation and every action works.** "
        + (f"Each link, button, form, key, redirect, dialog, tab, frame and popup window in "
           f"`{app}/navigation.json` behaves as listed. " if nav else
           "Each link, button and form of the brief leads where the brief says. ")
        + "An element nobody used in the recording is rendered and gets no behaviour the brief "
        "does not state.",
    ]  # fmt: skip
    if f"{app}/rules.json" in packaged:
        from labctl import pagerules

        rules.append(pagerules.task_rule(app))
    if browser:
        rules.append("**Data stays where the original keeps it.** That is the browser, as the "
                     "brief's *Browser storage* section says. There is no server database.")  # fmt: skip
    else:
        rules.append("**Data lives in the backend database.** Users, sessions and every record "
                     "are stored in and read from the database by the server. Nothing important "
                     "is kept only in the page or in the server's memory.")  # fmt: skip
        rules.append("**The seed is loaded at start.** Create the tables from `db/schema.sql` "
                     + ("and load exactly the rows of `db/seed.sql`, once, when the tables are "
                        "empty." if seed_loaded else
                        "and fill them with synthetic rows as the brief says, once, when the "
                        "tables are empty."))  # fmt: skip
    rules += [
        "**Synthetic data only.** No real name, number, address, hostname or credential in any "
        "file. Use the values the package gives.",
        "**Build what was recorded, nothing else.** Nothing under *Out of scope* is built or "
        "stubbed. What the package does not state is an assumption you write down, never a "
        "feature you invent.",
        "**The brief and the checks are not yours to change.** A check that fails is a finding "
        "about your app.",
    ]
    return "\n".join(f"{n}. {text}" for n, text in enumerate(rules, 1))


def prompt(repo_root: Path, app_id: str, repo: str | None, state_dir: Path | None = None) -> str:
    """The task text for the coding agent. Pasted unchanged by a person, or passed by a builder."""
    repo_root = Path(repo_root)
    _check_inputs(repo_root, app_id)
    conf = _app_yaml(repo_root, app_id)
    digest = brief_digest(repo_root, app_id)
    branch = f"build/{app_id}-{digest8(digest)}"
    # Only registered automations can be replayed; requested flows have nothing to run.
    built_for = [
        str(a) for a in conf.get("built_for") or []
        if (repo_root / "automations" / str(a) / "automation.yaml").is_file()
    ]  # fmt: skip
    # The database the demo app will run on is the lab's choice; the real system's engine
    # (`stack.db`) only matters when the lab names none.
    # Next, the dialect the brief wrote the schema in (it says when that is only a default).
    schema_text = _content(repo_root / "apps" / app_id / "db" / "schema.sql").decode(
        "utf-8", "replace"
    )
    dialect = re.search(r"^-- Dialect: (\w+)( \(a default)?", schema_text, re.M)
    engine = (
        (conf.get("lab") or {}).get("database")
        or (dialect.group(1) if dialect else None)
        or ((conf.get("stack") or {}).get("db") or {}).get("engine")
        or "sqlite"
    )
    engine_default = not (conf.get("lab") or {}).get("database") and (
        (bool(dialect and dialect.group(2))) or not (dialect or (conf.get("stack") or {}).get("db")))  # fmt: skip
    db_var, port, health = _run_env(conf)
    db_var = db_var or "DATABASE_URL"  # the lab's convention when the evidence names none
    port_text = str(port) if port else "<container port>"
    health_text = f"`{health}`" if health else "`/api/health`"
    app = f"apps/{app_id}"
    if repo:
        repo_line = (
            f"1. Use the GitHub repository **`{normalize_repo(repo)}`**. If you are not already "
            "inside a checkout of it, clone it and start from its default branch (normally "
            "`main`). Do not use any other repository."
        )
    else:
        repo_line = (
            "1. Use the repository you were given, the one that contains `AGENTS.md` and an "
            "`apps/` folder at its root. If you are already inside a checkout of it, work there. "
            "If there is more than one such repository, or none, stop and ask which to use."
        )
    if built_for:
        conformance = "\n".join(f"   python -m labctl conformance {a}" for a in built_for)
        conformance_step = (
            "6. Conformance, one run per automation the app is built for. First run "
            "`python -m labctl --help`; if it lists `lab` and `conformance` commands, start the "
            "app in the lab (it needs the ports under `lab:` in `app.yaml` to be free), check, "
            "and stop it:\n\n"
            f"   ```bash\n   python -m labctl lab up {app_id}\n{conformance}\n"
            f"   python -m labctl lab down {app_id} --wipe\n   ```\n\n"
            "   Every automation must report READY. A BLOCKED report names each missing selector, "
            "route or table: fix the app, never the automation or the check.\n"
            '   If the commands do not exist yet, do not write them; record "conformance: not '
            'available in this repository" in BUILD-NOTES.md and the pull request.'
        )
    else:
        conformance_step = (
            "6. Conformance: `app.yaml` lists no registered automation under `built_for` (a "
            "requested flow has nothing to run), so there is "
            "nothing to run. Say so in BUILD-NOTES.md."
        )
    enhance = (repo_root / "apps" / app_id / "Dockerfile").is_file()
    if enhance:
        brief_rule = (
            "   - **This app already exists: this is an enhancement.** The brief was regenerated "
            "from new evidence (a new flow, screens, fields, routes or tables), and a person has "
            "committed it to the default branch. If the repository's `BRIEF.md` differs from the "
            "package's, stop and ask: never replace a brief yourself (CI refuses it). Then change "
            "the existing app: keep everything that works and every element id, route and table "
            "it already has; add what the new brief adds; remove only what the brief now lists as "
            "out of scope. `git log -p` on `BRIEF.md` shows what changed."
        )
        enhance_rule = (
            "\n7. Enhancement: every automation that was READY before must still be READY. In "
            'BUILD-NOTES.md, add a section "Enhancement" listing what you added, changed and '
            "removed, and why."
        )
        title = f"{app_id}: enhance from brief {digest}"
        new_section = (
            f"## What is new in this brief (enhancement)\n\n`{app}/` already holds a built app, "
            "so this is an **enhancement**, not a new build. Extend the app and its schema "
            "without breaking any existing screen, selector, route, table or row of data. "
            "Change only what the brief now adds or removes.\n\n"
            f"{whats_new(repo_root, app_id, state_dir)}\n\n"
            "Run the app's existing checks first, so you know what passed before you start.\n\n"
        )
    else:
        brief_rule = (
            f"   - If the repository already has a `{app}/BRIEF.md` that differs from the package, "
            "stop and ask which one to build from."
        )
        enhance_rule = ""
        title = f"{app_id}: demo app from brief {digest}"
        new_section = ""
    browser = browser_only(conf)
    db_example = (
        _serve_example(app_id, port_text)
        if browser
        else _db_example(engine, app_id, db_var, port_text)
    )
    if browser:
        database_section = _browser_database_text(conf, app)
        env_step = (
            "5. One `Dockerfile` in `{app}/`. The app has no database address: it opens no "
            "database and reads no database variable. No secrets in the image. Synthetic data "
            "only."
        ).format(app=app)
        objects_step = (
            "6. Database objects: none. `{app}/db/schema.sql` holds no table because the "
            "business data is in browser storage; create no table, no server-side store and no "
            "endpoint for the data."
        ).format(app=app)
        run_check = (
            "4. Run the container alone (there is no database to start), as "
            "`docs/factory-playbook.md` describes. For example:"
        )
        run_tail = (
            "   Then open the app in a browser, add and change records, reload, and confirm "
            "they are kept in the browser storage under the keys the brief names."
        )
        health_check = (
            f"5. Against the running container: the health route {health_text} answers 200 "
            "(this is hosting health: it says the files are served, not that any business data "
            "is correct); each screen"
        )
    else:
        default_note = (
            " This engine is **a default, not evidence**: nothing in the recording shows the real "
            "application's database and none was chosen for this app at onboarding (`lab.database` "
            "in `app.yaml`). Keep the data access plain (standard SQL, the driver chosen from "
            "`DB_ENGINE`) so another engine can be given later."
            if engine_default
            else ""
        )
        seed_file = repo_root / app / "db" / "seed.sql"
        fill = (
            f"load **exactly** the rows of `{app}/db/seed.sql` (generated with the replay values "
            f"in `{app}/steps/*.values.yaml`; the same rows as JSON in `{app}/db/seed.json`) "
            "once, when the tables are empty, and no other rows: the acceptance replay types those "
            "values and expects the outcomes the recording showed on exactly these rows (the "
            "brief's *Seed data and replay values*)"
            if seed_file.is_file()
            else "fill them with synthetic rows (the brief's *Identifiers and seed data* says how "
            "many and how they are numbered, where the recording shows it)"
        )
        database_section = (
            f"The database engine to target is {_engine_text(engine, db_var)}{default_note} Only "
            f"the **tables and data** are needed: create the tables from `{app}/db/schema.sql` and "
            f"{fill}. Do not rebuild the original system's business logic, stored procedures or "
            "jobs beyond the behaviours the brief lists."
        )
        env_step = (
            f"5. One `Dockerfile` in `{app}/`. The app reads its database address from the "
            f"environment\n   variable `{db_var}` (a URL such as "
            "`mssql+pymssql://user:password@host:1433/db`); the lab also\n   sets `DB_ENGINE`, "
            "`DB_HOST`, `DB_PORT`, `DB_NAME` (Oracle: `DB_SERVICE`), `DB_USER`,\n   "
            "`DB_PASSWORD` and `DB_CONNECTION_STRING` (ADO.NET style) for stacks that want the "
            "parts. No\n   secrets in the image. Synthetic data only."
        )
        same = dialect is not None and {"postgresql": "postgres", "sqlserver": "mssql"}.get(
            str(engine).lower(), str(engine).lower()) == dialect.group(1)  # fmt: skip
        dialect_text = (
            f"`schema.sql` is written for this\n   engine ({dialect.group(1)}; names an engine "
            "reserves are quoted): apply it unchanged."
            if same
            else "`schema.sql` is written in\n   the real system's SQL dialect, which can differ "
            "from the demo database's engine: when you\n   create the tables yourself, translate "
            "types and identity columns to that engine; do not run\n   the file unchanged."
        )
        objects_step = (
            f"6. Database objects: create tables from `{app}/db/schema.sql` when the database has "
            "none (the lab\n   may have created them already: then leave them). A view in the "
            "schema is a **table** in the\n   demo database, with sample rows; read it like a "
            "table. Stored procedures, functions and\n   triggers are not created; the brief "
            f"says which the real app has. {dialect_text}"
        )
        run_check = (
            "4. Run the container against a real database of the brief's engine, as\n   "
            "`docs/factory-playbook.md` describes. For example (throwaway local values, not "
            "secrets):"
        )
        run_tail = "   Use the URL scheme the app's database driver expects."
        health_check = (
            f"5. Against the running container: the health route {health_text} answers 200; each "
            "screen"
        )
    stack = _stack_text(repo_root, app_id)
    stack_block = f"\n{stack}\n" if stack.strip() else ""
    cross_section = _cross_app_text(repo_root, app_id)
    if (conf.get("stack") or {}).get("desktop"):  # a Windows desktop app: no container
        from labctl.desktop import brief as desktop_brief

        return desktop_brief.task(repo_root, app_id, conf, digest, repo_line, branch, brief_rule,
                                  stack_block)  # fmt: skip
    packaged = [rel for rel, _ in _collect(repo_root, app_id)]
    shots = [rel for rel in packaged if rel.startswith("evidence/")]
    step_files = [rel for rel in packaged if rel.startswith(f"{app}/steps/")
                  and not rel.endswith(".values.yaml")]  # fmt: skip
    value_files = [rel for rel in packaged if rel.startswith(f"{app}/steps/")
                   and rel.endswith(".values.yaml")]  # fmt: skip
    flow_files = [rel for rel in packaged if rel.startswith("flows/")]
    shots_text = (
        f" Files under `evidence/{app_id}/screenshots/` in the package show how screens look; "
        "use them for reference only and do not commit them (`evidence/` is Git-ignored on "
        "purpose)."
        if shots
        else " The package has no pictures of the screens: the snapshots are the reference for "
        "layout (their styles are the application's own)."
    )
    steps_text = (
        f"\n4. Read {', '.join(f'`{f}`' for f in step_files)}: the recorded steps in order (the "
        "action, the selector, the screen, the requests each caused and the page that followed; "
        "typed values only as their shape)"
        + (
            f", and {', '.join(f'`{f}`' for f in value_files)}: the synthetic value the "
            "acceptance replay types at each of those steps and what the recording showed then "
            "(the outcome your app must give on `db/seed.sql`)"
            if value_files
            else ""
        )
        + (
            f", and {', '.join(f'`{f}`' for f in flow_files)}: the flow as a person would describe "
            "it"
            if flow_files
            else ""
        )
        + ". Your app must let these steps happen in this order, with these selectors."
        if step_files
        else ""
    )
    package_only = _package_only_text(app_id, app, engine, bool(step_files), db_var,
                                      nav=f"{app}/navigation.json" in packaged,
                                      api=f"{app}/api/catalogue.json" in packaged)  # fmt: skip
    # The look (labctl.lookfeel): its own section, its files in the lists, its check.
    from labctl import lookfeel

    look_section = lookfeel.task_text(repo_root, app_id)
    from labctl import publicdata

    with publicdata.active_for(repo_root, app_id) as pub:  # real public catalogue data, declared
        public_note = pub.note() + "\n" if pub is not None else ""
    look_section = public_note + look_section
    look_files = f", `{app}/look/**`" if look_section else ""
    look_check = lookfeel.check_step(repo_root, app_id)
    if look_check:
        shots_text = (f" `{app}/look/screens/*.png` are pictures of the screens, rendered "
                      "without data: your screens must match them (see *Same look*)."
                      + (shots_text if shots else ""))  # fmt: skip
        package_only = package_only.replace("exact text where the brief says.",
                                            "exact text where the brief says." + look_check, 1)  # fmt: skip
    done_text = _done_text(app, packaged, browser, bool(value_files))
    reading = _reading_order(app, app_id, packaged)
    rules_text = _rules_text(app, packaged, browser, seed_loaded=f"{app}/db/seed.sql" in packaged)
    gaps = complete.problems(repo_root, app_id)
    gaps_section = (
        "## Known gaps in this package\n\nThe packaging step checked the package and found "
        "these gaps. Each is something the evidence does not hold. Where one matters, choose the "
        "simplest behaviour that fits the brief and report it as an assumption:\n\n"
        + "\n".join(f"- {g}" for g in gaps) + "\n\n"
        if gaps else ""
    )  # fmt: skip
    return f"""# Twin Lab build task: `{app_id}`

You are a coding agent. Build the demo app `{app_id}` for Twin Lab from this package and hand
the result back as one pull request (or, if you cannot open one, as one branch; working from the
package alone, as the folder `{app}/`). Everything you need is in the package: do not look for
the original application. Follow the sections below in order. Brief digest: `{digest}`.

The Twin Lab package (`twinlab-{app_id}-*.zip`) holds this task, the brief and its inputs at
their repository paths. `INDEX.md` lists every file with one line on what it is for.
`MANIFEST.json` lists every file with its sha256. If you are working inside the repository, the
files are already there.

## What to build, and what "done" means

Build a working look-alike of a real application: the same screens, the same behaviour, a real
backend and a real database. A person used the real application once while Twin Lab recorded
it. That recording is all the evidence there is, and this package is what it showed, with the
data taken out. Automations (UiPath processes, Python scripts, test bots) will run against your
build instead of the real application, so it must be the same wherever the recording shows
what the real one does: an automation that passes on your app must find the same screens,
selectors, routes and fields it would find in the real one. You do not need the original
business logic beyond the behaviours the brief lists.

The build is done when all of this holds:

{done_text}

## Read in this order

{reading}

## Technology and versions

Rebuild in the **same technology the brief's "Stack to pin" section names**: the front-end
framework and version, the server technology and the UI library. Do not substitute another
because it is easier. If that section names none, use the default it states and record that in
BUILD-NOTES.md. Pin the version of the language, the framework and every library you use, and
list them in BUILD-NOTES.md. From the brief, as it stands:
{stack_block}
## Rules that are not negotiable

{rules_text}

{look_section}## Database

{database_section}

## How to report assumptions

Where the package is silent, or says *hypothesis*, *assumed*, *not recorded* or *seen, not
used*, do not stop and do not invent a feature. Choose the simplest behaviour that fits
everything the package does say. Write it in `{app}/BUILD-NOTES.md` under **Assumptions**, one
line each: what the package did not say, what you chose, the file where you built it. Ask a
person only where section 7 says to stop.

{gaps_section}{cross_section}{package_only}{new_section}## 1. Repository and branch

(In the repository. Working from the package alone, see *Working from this package alone*.)

{repo_line}
2. Work on a branch named `{branch}`, created from the default branch (if you are already on a
   branch you were told to use, stay on it). Do all work on it. Never push to the default branch.

## 2. Start from the brief's files

1. Read `AGENTS.md`, then `docs/factory-playbook.md`. Their rules apply to everything below.
2. Read `{app}/BRIEF.md`.
   - If `{app}/BRIEF.md` is not on the default branch yet, copy the brief's files from the
     attached package to the same paths, unchanged: `{app}/BRIEF.md`, `{app}/db/schema.sql`,
     `{app}/app.yaml`, `{app}/model.yaml`, `{app}/behaviours.json`, `{app}/db/seed.sql`, `{app}/db/seed.json`, `{app}/navigation.json`, `{app}/rules.json`, `{app}/api/*`, `{app}/pages/*.html`{look_files} and `{app}/steps/*` where present. Check each
     against its sha256 in `MANIFEST.json`, and commit them first, on their own, with the
     message `{app_id}: brief {digest} from Twin Lab package`.
{brief_rule}
3. Read what the brief builds from, in the order *Read in this order* gives. If the brief lists
   source files, read them and only what they import. Otherwise (no source code, the usual
   case) read `{app}/pages/*.html`: value-free snapshots of each recorded screen, with the real
   element ids, classes, labels and layout; `{app}/behaviours.json` and the brief's "Behaviour
   seen in the recordings": what each screen does; and `{app}/model.yaml`: request and response
   shapes, codes and timings.{shots_text}{steps_text}

## 3. Build

1. Build only what the brief puts in scope. Nothing under "Out of scope" is built or stubbed.
   Where an in-scope screen has an element that leads out of scope (a menu link, or the page the
   real app opens after sign-in), keep the element exactly as the snapshot has it (id, classes
   and `href`), because automations look for it, and make the app answer that path with a
   redirect (303) to the first in-scope screen (the brief's *Links that leave the scope* lists
   each one and where to). That redirect is all that is built for it; list each in
   `BUILD-NOTES.md`.
2. Files go under `{app}/`: the Dockerfile, the application code and `BUILD-NOTES.md`.
   Write only under `{app}/`. Do not change any file outside it.
3. Never edit `{app}/BRIEF.md` or `{app}/db/schema.sql` (nor `{app}/db/seed.sql`), and never edit a check (a test, an
   automation, a flow or a condition) to make it pass. In `{app}/app.yaml`, change only the
   `run:` section. Every rule keeps `verification: hypothesis`.
4. Same framework and versions as "Stack to pin", same element ids and classes as the brief's
   screen tables.
{env_step}
{objects_step}{enhance_rule}

## 4. Check

Run every command from the repository root, after `pip install -e ".[dev]"` (Python 3.11 or
later). Keep the exact result of each one for steps 5 and 6.

1. `python -m pytest -q`
2. `python -m labctl validate` (if `labctl` is not installed, run it from `lab/labctl`)
3. `docker build -t twinlab/{app_id}:dev {app}`
{run_check}

{db_example}

{run_tail}
{health_check} in
   the brief opens and shows its listed selectors; each route in the brief answers on its method
   and path; each rule in `{app}/app.yaml` behaves as written; nothing out of scope answers
   (expect 404).{look_check} Remove the containers and the network afterwards.
{conformance_step}

## 5. Build notes

Write `{app}/BUILD-NOTES.md` with these sections: what you built; the versions you pinned; each
check above with its command and result; assumptions (one line each, as *How to report
assumptions* says); what you could not check and why; anything in the brief you think is wrong.

## 6. Pull request

Open exactly ONE pull request from your branch to the default branch, labelled `ai-built`
(CI applies the AI-change rules to it), titled:

    {title}

Body: a table of the checks from step 4 with pass or fail and, for each failure, the relevant
output; the assumptions; anything in the brief you think is wrong. If a pull request for this app
already exists from your branch, update it instead of opening another. If you cannot open a pull
request, leave the work committed on your branch and say so.

## 7. Trust rules and when things go wrong

- The brief and the checks are not yours to change: do not edit `{app}/BRIEF.md`,
  `{app}/db/schema.sql`, tests, `automations/`, `flows/`, `lab/` or `conditions/`. A check that
  fails is a finding about your app, to report, not a problem to remove.
- Report failures exactly as they are. Never change, skip or loosen a test or check to make it
  pass, and never claim a check passed that you did not run.
- If the brief contradicts itself, `AGENTS.md`, the playbook, `db/schema.sql` or the source it
  names, stop and ask before building that part. Quote the conflicting lines.
- No real customer data and no secrets in any file, log or pull request.
- If you cannot clone, push or open the pull request (for example GitHub access is missing),
  stop and say so; do not work around it with another repository.
"""


def completeness(repo_root: Path, app_id: str) -> list[str]:
    """What the package of `app_id` lacks, one line each ([] when complete): a screen without an
    actions list, an endpoint without a schema, an undefined navigation target, a table without
    seed rows, a typing step without a value (labctl.builder.complete)."""
    return complete.problems(Path(repo_root), app_id)


def build(repo_root: Path, app_id: str, out_dir: Path, repo: str | None = None,
          strict: bool | None = None) -> Path:  # fmt: skip
    """Write `twinlab-<app>-<digest8>.zip` into `out_dir` and return its path.

    `<digest8>` is the first 8 hex digits of a sha256 over the packaged files (path and content),
    so a changed brief, page or prompt gives a new name, and the same inputs the same name.

    The package is checked for completeness first. Every gap is written into the task, into
    `INDEX.md` and into `MANIFEST.json` (`completeness`), and printed as a warning. With
    `strict=True` (or `TWINLAB_PACKAGE_STRICT=1`) a gap refuses the package instead.
    """
    from labctl import publicdata

    with publicdata.active_for(Path(repo_root), app_id):  # an owner's declaration, if approved
        return _build(repo_root, app_id, out_dir, repo, strict)


def _build(repo_root: Path, app_id: str, out_dir: Path, repo: str | None,
           strict: bool | None) -> Path:  # fmt: skip
    repo_root = Path(repo_root)
    _check_inputs(repo_root, app_id)
    desktop = bool((_app_yaml(repo_root, app_id).get("stack") or {}).get("desktop"))
    gaps = [] if desktop else completeness(repo_root, app_id)
    if strict is None:
        strict = os.environ.get("TWINLAB_PACKAGE_STRICT", "").strip() in ("1", "true", "yes")
    if gaps and strict:
        raise PackageError(f"package of {app_id} is not complete ({len(gaps)} gap(s)):\n"
                           + "\n".join(f"  - {g}" for g in gaps))  # fmt: skip
    if gaps:
        import warnings

        warnings.warn(f"package of {app_id}: {len(gaps)} gap(s): " + "; ".join(gaps),
                      PackageWarning, stacklevel=2)  # fmt: skip
    task = prompt(repo_root, app_id, repo).encode("utf-8")
    files = _collect(repo_root, app_id)
    from labctl import lookfeel  # refuses a look that holds data, or a picture it did not render

    look_problems = lookfeel.package_check(repo_root, app_id)
    if look_problems:
        raise PackageError(
            f"cannot package {app_id}: the look (apps/{app_id}/look) failed the privacy check: "
            + "; ".join(look_problems[:10])
        )
    from labctl import pagerules  # refuses scripts that hold a secret, another host or data

    script_problems = pagerules.package_check(repo_root, app_id)
    if script_problems:
        raise PackageError(f"cannot package {app_id}: the scripts (apps/{app_id}/scripts) failed "
                           "the check: " + "; ".join(script_problems[:10]))  # fmt: skip
    entries: list[tuple[str, bytes]] = [(TASK_FILE, task)]
    entries += [(rel, _content(path)) for rel, path in files]
    purposes = [(rel, complete.purpose(rel, app_id, data)) for rel, data in entries]
    purposes += [(complete.INDEX_FILE, complete.purpose(complete.INDEX_FILE, app_id)),
                 (MANIFEST_FILE, complete.purpose(MANIFEST_FILE, app_id))]  # fmt: skip
    index = complete.index_md(app_id, purposes, gaps).encode("utf-8")
    entries.insert(1, (complete.INDEX_FILE, index))
    listing = [
        {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
         "purpose": complete.purpose(rel, app_id, data)}
        for rel, data in entries
    ]  # fmt: skip
    combined = hashlib.sha256(
        "\n".join(f"{f['path']} {f['sha256']}" for f in listing).encode("utf-8")
    ).hexdigest()
    manifest = {
        "format": FORMAT,
        "app": app_id,
        "repo": normalize_repo(repo) if repo else None,
        "brief_digest": brief_digest(repo_root, app_id),
        "package_digest": combined,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completeness": {"complete": not gaps, "gaps": gaps},
        "files": listing,
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"twinlab-{app_id}-{combined[:8]}.zip"
    tmp = target.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, data in [*entries, (MANIFEST_FILE, json.dumps(manifest, indent=2).encode())]:
            info = zipfile.ZipInfo(rel, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    tmp.replace(target)
    return target


def read_manifest(package: Path) -> dict:
    with zipfile.ZipFile(package) as zf:
        return json.loads(zf.read(MANIFEST_FILE))


TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".sql", ".html", ".txt", ".css", ".js"}


def _content(path: Path) -> bytes:
    """File bytes as the repository stores them: text with Unix line endings (a Windows checkout
    may hold CRLF), so the checksums in MANIFEST.json match the files on GitHub."""
    data = path.read_bytes()
    return data.replace(b"\r\n", b"\n") if path.suffix.lower() in TEXT_SUFFIXES else data
