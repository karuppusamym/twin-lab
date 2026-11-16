"""The app factory in one place: gather evidence, build the model, write the brief.

Used by `labctl brief` and by the portal so both produce exactly the same files.

Evidence for an app is discovered under `evidence/<app>/` (Git-ignored):
    <recording-id>/recording.har (+ dom/, steps.jsonl)   browser recordings
    *.har                                                bare HAR files
    *.sql                                                DDL exports
    openapi*.json|yaml|yml, *.postman_collection.json    API descriptions
    screenshots/                                         listed for the builder, not parsed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from labctl import brief as brief_mod
from labctl import model_build as mb
from labctl import repo
from labctl.appmodel import Flow
from labctl.models import Automation


@dataclass
class Evidence:
    recordings: list[Path] = field(default_factory=list)
    ddl: list[Path] = field(default_factory=list)
    openapi: list[Path] = field(default_factory=list)
    screenshots: list[Path] = field(default_factory=list)

    def kinds(self) -> list[str]:
        return [k for k in ("recordings", "ddl", "openapi", "screenshots") if getattr(self, k)]


def evidence_dir(root: Path, app_id: str) -> Path:
    return root / "evidence" / app_id


def discover(root: Path, app_id: str) -> Evidence:
    base = evidence_dir(root, app_id)
    found = Evidence()
    if not base.is_dir():
        return found
    for path in sorted(base.iterdir()):
        name = path.name.lower()
        if path.is_dir() and name == "screenshots":
            found.screenshots = sorted(p for p in path.iterdir() if p.is_file())
        elif path.is_dir() and ((path / "recording.har").exists() or any(path.glob("*.har"))):
            found.recordings.append(path)
        elif path.is_file() and name.endswith(".har"):
            found.recordings.append(path)
        elif path.is_file() and name.endswith(".sql"):
            found.ddl.append(path)
        elif path.is_file() and (
            (name.startswith(("openapi", "swagger")) and name.endswith((".json", ".yaml", ".yml")))
            or name.endswith(".postman_collection.json")
        ):
            found.openapi.append(path)
    return found


def automations(root: Path, app_id: str | None = None) -> list[Automation]:
    out = []
    for path in repo.files_of(root, "automation"):
        model, _ = repo.load(path)
        if model is not None and (app_id is None or any(t.app == app_id for t in model.touches)):
            out.append(model)
    return out


def flows(root: Path, app_id: str) -> list[Flow]:
    out = []
    for path in repo.files_of(root, "flow"):
        model, _ = repo.load(path)
        if model is not None and model.app == app_id:
            out.append(model)
    return out


def needed_apps(root: Path) -> list[str]:
    """Apps that some automation or flow needs, built or not."""
    names = {t.app for a in automations(root) for t in a.touches}
    for path in repo.files_of(root, "flow"):
        model, _ = repo.load(path)
        if model is not None:
            names.add(model.app)
    return sorted(names)


def build_model(
    root: Path, app_id: str, source: Path | None = None, evidence: Evidence | None = None
):
    evidence = evidence or discover(root, app_id)
    pages_dir = root / "apps" / app_id / "pages"
    parts = [
        mb.from_automations(app_id, automations(root, app_id)),
        mb.from_flows(app_id, flows(root, app_id)),
    ]
    if source is not None:
        parts.append(mb.from_source(app_id, source, root))
    for rec in evidence.recordings:
        parts.append(mb.from_recording(app_id, rec, root, pages_dir))
    for spec in evidence.openapi:
        parts.append(mb.from_openapi(app_id, spec))
    if evidence.ddl:
        from labctl.data import ddl as ddl_mod

        for path in evidence.ddl:
            parts.append(ddl_mod.to_model(app_id, path.read_text(encoding="utf-8", errors="replace"),
                                          f"ddl:{path.name}"))  # fmt: skip
    return mb.merge(app_id, parts)


@dataclass
class Result:
    brief: brief_mod.Brief
    files: list[Path]
    kept_app_yaml: bool


class FactoryError(Exception):
    pass


def generate(
    root: Path,
    app_id: str,
    system: str | None = None,
    source: Path | None = None,
    db_engine: str | None = None,
    force: bool = False,
) -> Result:
    if not automations(root, app_id) and not flows(root, app_id):
        raise FactoryError(
            f"no registered automation or requested flow needs '{app_id}'; nothing to build"
        )
    out = root / "apps" / app_id
    model_file, brief_file = out / "model.yaml", out / "BRIEF.md"
    schema_file, app_file = out / "db" / "schema.sql", out / "app.yaml"
    if (brief_file.exists() or schema_file.exists() or model_file.exists()) and not force:
        raise FactoryError(f"apps/{app_id} already has a brief; replace it explicitly")
    model = build_model(root, app_id, source)
    result = brief_mod.scope(app_id, system or app_id, model, db_engine)
    schema_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text(
        yaml.safe_dump(
            model.model_dump(mode="json", by_alias=True, exclude_none=True),
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )
    brief_file.write_text(brief_mod.render(result, root), encoding="utf-8")
    schema_file.write_text(brief_mod.schema(result, root), encoding="utf-8")
    write_workflow(root, app_id)
    kept = app_file.exists()
    if not kept:
        app_file.write_text(
            yaml.safe_dump(brief_mod.app_yaml(result, root), sort_keys=False, width=100),
            encoding="utf-8",
        )
    return Result(result, [model_file, brief_file, schema_file, app_file], kept)


WORKFLOW = """# Pipeline for the {app} demo app (written by labctl brief). The steps are in app.yml.
name: app-{app}
on:
  push:
    branches: [main]
    paths: ["apps/{app}/**", "automations/**", "lab/**", ".github/workflows/app.yml", ".github/workflows/app-{app}.yml"]
  pull_request:
    paths: ["apps/{app}/**", "automations/**", "lab/**", ".github/workflows/app.yml", ".github/workflows/app-{app}.yml"]
jobs:
  app:
    uses: ./.github/workflows/app.yml
    with:
      app: {app}
"""


def write_workflow(root: Path, app_id: str) -> Path | None:
    """One small workflow per app calling the reusable one; never overwrites an edited file."""
    path = root / ".github" / "workflows" / f"app-{app_id}.yml"
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WORKFLOW.format(app=app_id), encoding="utf-8")
    return path
