"""The Devin package: one zip holding the task prompt and everything a builder needs.

Used by both paths. A person uploads the zip to Devin and pastes `DEVIN_TASK.md`; the direct path
uploads the same zip through the API. Raw evidence never goes in: no HAR files, nothing under
`evidence/` but the app's screenshots, no `.env` files, no `twinlab.local.yaml`.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import yaml

FORMAT = "twinlab-devin-package/1"
TASK_FILE = "DEVIN_TASK.md"
MANIFEST_FILE = "MANIFEST.json"
SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_APP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed, so the same inputs give the same file contents


class PackageError(Exception):
    pass


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
        f"{app}/app.yaml",
        f"{app}/behaviours.json",
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
    if e in ("mssql", "sqlserver", "sql server"):
        lines = [
            f"docker network create {net}",
            f"docker run -d --name {db} --network {net} -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=Lab_pass1 mcr.microsoft.com/mssql/server:2022-latest",
            f'docker exec {db} /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P Lab_pass1 -Q "CREATE DATABASE [{app_id}]"',
            f"docker run -d --name {app} --network {net} -p 18080:{port} -e {db_var}=mssql+pymssql://sa:Lab_pass1@{db}:1433/{app_id} twinlab/{app_id}:dev",
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


def prompt(repo_root: Path, app_id: str, repo: str | None) -> str:
    """The task text for Devin. Pasted unchanged by a person, or sent by the API."""
    repo_root = Path(repo_root)
    _check_inputs(repo_root, app_id)
    conf = _app_yaml(repo_root, app_id)
    digest = brief_digest(repo_root, app_id)
    # Only registered automations can be replayed; requested flows have nothing to run.
    built_for = [
        str(a) for a in conf.get("built_for") or []
        if (repo_root / "automations" / str(a) / "automation.yaml").is_file()
    ]  # fmt: skip
    engine = ((conf.get("stack") or {}).get("db") or {}).get("engine") or "postgres"
    db_var, port, health = _run_env(conf)
    db_var = db_var or "DATABASE_URL"  # the lab's convention when the evidence names none
    port_text = str(port) if port else "<container port>"
    health_text = f"`{health}`" if health else "`/api/health`"
    app = f"apps/{app_id}"
    if repo:
        repo_line = (
            f"1. Use the GitHub repository **`{normalize_repo(repo)}`**. Clone it and start from "
            "its default branch (normally `main`). Do not use any other repository."
        )
    else:
        repo_line = (
            "1. Use the GitHub repository connected to this Devin organisation that contains "
            "`AGENTS.md` and an `apps/` folder at its root. If there is more than one such "
            "repository, or none, stop and ask which to use."
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
            "6. Conformance: `app.yaml` lists no automations under `built_for`, so there is "
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
    else:
        brief_rule = (
            f"   - If the repository already has a `{app}/BRIEF.md` that differs from the package, "
            "stop and ask which one to build from."
        )
        enhance_rule = ""
        title = f"{app_id}: demo app from brief {digest}"
    db_example = _db_example(engine, app_id, db_var, port_text)
    return f"""# Twin Lab build task: `{app_id}`

You are building the demo app `{app_id}` for Twin Lab from its build brief and opening one pull
request. Follow the steps below in order. Brief digest: `{digest}`.

The attached Twin Lab package (`twinlab-{app_id}-*.zip`) holds this task, the brief and its
inputs at their repository paths. `MANIFEST.json` in the package lists every file with its sha256.

## 1. Repository and branch

{repo_line}
2. Create a branch named `devin/{app_id}-<yyyymmdd-hhmm>` from the default branch, using the
   current UTC date and time (for example `devin/{app_id}-20261115-0930`). Do all work on this
   branch. Never push to the default branch.

## 2. Read before you build

1. Read `AGENTS.md`, then `docs/factory-playbook.md`. Their rules apply to everything below.
2. Read `{app}/BRIEF.md`.
   - If `{app}/BRIEF.md` is not on the default branch yet, copy the brief's files from the
     attached package to the same paths, unchanged: `{app}/BRIEF.md`, `{app}/db/schema.sql`,
     `{app}/app.yaml`, `{app}/model.yaml`, `{app}/behaviours.json` and `{app}/pages/*.html` where present. Check each
     against its sha256 in `MANIFEST.json`, and commit them first, on their own, with the
     message `{app_id}: brief {digest} from Twin Lab package`.
{brief_rule}
3. Read what the brief builds from. If it lists source files, read them and only what they
   import. Otherwise (no source code, the usual case) read `{app}/pages/*.html`: value-free
   snapshots of each recorded screen, with the real element ids, classes, labels and layout;
   `{app}/behaviours.json` and the brief's "Behaviour seen in the recordings": what each screen
   does; and `{app}/model.yaml`: request and response shapes, codes and timings. Files under
   `evidence/{app_id}/screenshots/` in the package show how screens look; use them for
   reference only and do not commit them (`evidence/` is Git-ignored on purpose).

## 3. Build

1. Build only what the brief puts in scope. Nothing under "Out of scope" is built, linked or
   stubbed.
2. Write only under `{app}/`. Do not change any file outside it.
3. Never edit `{app}/BRIEF.md` or `{app}/db/schema.sql`. In `{app}/app.yaml`, change only the
   `run:` section. Every rule keeps `verification: hypothesis`.
4. Same framework and versions as "Stack to pin", same element ids and classes as the brief's
   screen tables.
5. One `Dockerfile` in `{app}/`. The database is **{engine}**: include its driver. The app reads
   its database address from the environment variable `{db_var}` (a URL such as
   `mssql+pymssql://user:password@host:1433/db`); the lab also sets `DB_ENGINE`, `DB_HOST`,
   `DB_PORT`, `DB_NAME` (Oracle: `DB_SERVICE`), `DB_USER`, `DB_PASSWORD` and
   `DB_CONNECTION_STRING` (ADO.NET style) for stacks that want the parts. No secrets in the
   image. Synthetic data only.
6. Database objects: create tables from `{app}/db/schema.sql` when the database has none (the lab
   may have created them already: then leave them). A view in the schema is a **table** in the
   demo database, with sample rows; read it like a table. Stored procedures, functions and
   triggers are not created; the brief says which the real app has.{enhance_rule}

## 4. Check

Run every command from the repository root, after `pip install -e ".[dev]"` (Python 3.11 or
later). Keep the exact result of each one for steps 5 and 6.

1. `python -m pytest -q`
2. `python -m labctl validate`
3. `docker build -t twinlab/{app_id}:dev {app}`
4. Run the container against a real {engine} server, as `docs/factory-playbook.md` describes.
   For example (throwaway local values, not secrets):

{db_example}

   Use the URL scheme the app's database driver expects.
5. Against the running container: the health route {health_text} answers 200; each screen in
   the brief opens and shows its listed selectors; each route in the brief answers on its method
   and path; each rule in `{app}/app.yaml` behaves as written; nothing out of scope answers
   (expect 404). Remove the containers and the network afterwards.
{conformance_step}

## 5. Build notes

Write `{app}/BUILD-NOTES.md` with these sections: what you built; the versions you pinned; each
check above with its command and result; assumptions; what you could not check and why; anything
in the brief you think is wrong.

## 6. Pull request

Open exactly ONE pull request from your branch to the default branch, titled:

    {title}

Body: a table of the checks from step 4 with pass or fail and, for each failure, the relevant
output; the assumptions; anything in the brief you think is wrong. If a pull request for this app
already exists from your branch, update it instead of opening another.

## 7. When things go wrong

- Report failures exactly as they are. Never change, skip or loosen a test or check to make it
  pass, and never claim a check passed that you did not run.
- If the brief contradicts itself, `AGENTS.md`, the playbook, `db/schema.sql` or the source it
  names, stop and ask in this session before building that part. Quote the conflicting lines.
- If you cannot clone, push or open the pull request (for example GitHub access is missing),
  stop and say so; do not work around it with another repository.
"""


def build(repo_root: Path, app_id: str, out_dir: Path, repo: str | None = None) -> Path:
    """Write `twinlab-<app>-<digest8>.zip` into `out_dir` and return its path.

    `<digest8>` is the first 8 hex digits of a sha256 over the packaged files (path and content),
    so a changed brief, page or prompt gives a new name, and the same inputs the same name.
    """
    repo_root = Path(repo_root)
    _check_inputs(repo_root, app_id)
    task = prompt(repo_root, app_id, repo).encode("utf-8")
    files = _collect(repo_root, app_id)
    entries: list[tuple[str, bytes]] = [(TASK_FILE, task)]
    entries += [(rel, _content(path)) for rel, path in files]
    listing = [
        {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        for rel, data in entries
    ]
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
