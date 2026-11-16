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
    return "sha256:" + hashlib.sha256(brief.read_bytes()).hexdigest()[:16]


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


def prompt(repo_root: Path, app_id: str, repo: str | None) -> str:
    """The task text for Devin. Pasted unchanged by a person, or sent by the API."""
    repo_root = Path(repo_root)
    _check_inputs(repo_root, app_id)
    conf = _app_yaml(repo_root, app_id)
    digest = brief_digest(repo_root, app_id)
    built_for = [str(a) for a in conf.get("built_for") or []]
    engine = ((conf.get("stack") or {}).get("db") or {}).get("engine") or "postgres"
    db_var, port, health = _run_env(conf)
    db_var = db_var or "the database variable the brief names"
    port_text = str(port) if port else "<container port>"
    health_text = f"`{health}`" if health else "the health path the brief states"
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
            "`python -m labctl --help`; if it lists a `conformance` command, run:\n\n"
            f"   ```bash\n{conformance}\n   ```\n\n"
            '   If the command does not exist yet, do not write one; record "conformance: not '
            'available in this repository" in BUILD-NOTES.md and the pull request.'
        )
    else:
        conformance_step = (
            "6. Conformance: `app.yaml` lists no automations under `built_for`, so there is "
            "nothing to run. Say so in BUILD-NOTES.md."
        )
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
     `{app}/app.yaml`, `{app}/model.yaml` and `{app}/pages/*.html` where present. Check each
     against its sha256 in `MANIFEST.json`, and commit them first, on their own, with the
     message `{app_id}: brief {digest} from Twin Lab package`.
   - If the repository already has a `{app}/BRIEF.md` that differs from the package, stop and
     ask which one to build from.
3. Read the source files the brief lists, and only what they import. Files under
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
5. One `Dockerfile` in `{app}/`. The app reads its {engine} address from the environment
   variable `{db_var}`. No secrets in the image. Synthetic data only.

## 4. Check

Run every command from the repository root, after `pip install -e ".[dev]"` (Python 3.11 or
later). Keep the exact result of each one for steps 5 and 6.

1. `python -m pytest -q`
2. `python -m labctl validate`
3. `docker build -t twinlab/{app_id}:dev {app}`
4. Run the container against a real {engine} server, as `docs/factory-playbook.md` describes.
   For example (throwaway local values, not secrets):

   ```bash
   docker network create twinlab-{app_id}
   docker run -d --name twinlab-{app_id}-db --network twinlab-{app_id} -e POSTGRES_USER=lab -e POSTGRES_PASSWORD=lab -e POSTGRES_DB={app_id} postgres:17
   docker run -d --name twinlab-{app_id}-app --network twinlab-{app_id} -p 18080:{port_text} -e {db_var}=postgresql://lab:lab@twinlab-{app_id}-db:5432/{app_id} twinlab/{app_id}:dev
   ```

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

    {app_id}: demo app from brief {digest}

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
    entries += [(rel, path.read_bytes()) for rel, path in files]
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
