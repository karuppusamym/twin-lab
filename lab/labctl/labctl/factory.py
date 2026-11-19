"""The app factory in one place: gather evidence, build the model, write the brief.

Used by `labctl brief` and by the portal so both produce exactly the same files.

Evidence for an app is discovered under `evidence/<app>/` (Git-ignored):
    <recording-id>/recording.har (+ dom/, steps.jsonl)   browser recordings
    <recording-id>/desktop.json (+ steps.jsonl, trees/)  desktop recordings (labctl.desktop)
    *.har                                                bare HAR files
    *.sql                                                DDL exports
    openapi*.json|yaml|yml, *.postman_collection.json    API descriptions
    screenshots/                                         listed for the builder, not parsed

A recording split from one that went through several applications (`labctl evidence split`) also
has its sequence across them in `evidence/_sequences/<recording>.flow.yaml`; the brief then says
where this app sits in that flow (labctl.multiapp.cross_app_section).
"""

from __future__ import annotations

import shutil
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
    desktop: list[Path] = field(default_factory=list)

    def kinds(self) -> list[str]:
        return [k for k in ("recordings", "desktop", "ddl", "openapi", "screenshots")
                if getattr(self, k)]  # fmt: skip


def evidence_dir(root: Path, app_id: str) -> Path:
    return root / "evidence" / app_id


def discover(root: Path, app_id: str) -> Evidence:
    base = evidence_dir(root, app_id)
    found = Evidence()
    if not base.is_dir():
        return found
    for path in sorted(base.iterdir()):
        name = path.name.lower()
        if path.is_dir() and (path / "desktop.json").is_file() and (path / "steps.jsonl").is_file():
            found.desktop.append(path)
        elif path.is_dir() and name == "screenshots":
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
    needs = flows(root, app_id)
    if not needs and not automations(root, app_id):
        # nobody narrowed the scope: every screen and action the recordings show
        from labctl import contract

        needs = contract.implied_flows(root, app_id)
    parts = [
        mb.from_automations(app_id, automations(root, app_id)),
        mb.from_flows(app_id, needs),
    ]
    if source is not None:
        parts.append(mb.from_source(app_id, source, root))
    for rec in evidence.recordings:
        parts.append(mb.from_recording(app_id, rec, root, pages_dir))
    for rec in evidence.desktop:
        parts.append(mb.from_desktop(app_id, rec))
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
    data: str | None = None,
) -> Result:
    from labctl import contract

    if (not automations(root, app_id) and not flows(root, app_id)
            and not contract.implied_flows(root, app_id)):  # fmt: skip
        raise FactoryError(
            f"no registered automation or requested flow needs '{app_id}' and it has no "
            "recording; nothing to build"
        )
    out = root / "apps" / app_id
    model_file, brief_file = out / "model.yaml", out / "BRIEF.md"
    schema_file, app_file = out / "db" / "schema.sql", out / "app.yaml"
    if (brief_file.exists() or schema_file.exists() or model_file.exists()) and not force:
        raise FactoryError(f"apps/{app_id} already has a brief; replace it explicitly")
    backup(root, app_id)
    # Snapshots are rewritten from the evidence there is now: one from deleted evidence (or from a
    # screen no longer recorded) must not stay and travel to Devin. The backup keeps the old set.
    shutil.rmtree(out / "pages", ignore_errors=True)
    model = build_model(root, app_id, source)
    if db_engine is None and model.stack.desktop and not model.stack.db:
        db_engine = "sqlite"  # a desktop demo app keeps its data in a local file
    if db_engine is None:
        db_engine = chosen_engine(root, app_file)
    result = brief_mod.scope(app_id, system or app_id, model, db_engine, data)
    extras = contract.adopt(root, app_id, result, model)  # session-only tables become app_users
    schema_file.parent.mkdir(parents=True, exist_ok=True)
    _write(
        model_file,
        yaml.safe_dump(
            model.model_dump(mode="json", by_alias=True, exclude_none=True),
            sort_keys=False,
            width=100,
        ),
    )
    text = brief_mod.render(result, root)
    behaviours_file = out / "behaviours.json"
    lines = write_behaviours(app_id, discover(root, app_id).recordings, behaviours_file)
    if lines:
        marker = "## Running in the lab"
        bullet_list = "\n".join(line if line.startswith("- ") else f"- {line}" for line in lines)
        section = (
            "## Behaviour seen in the recordings\n\n"
            "Inferred from the page before and after each recorded step; every rule has evidence in "
            "`behaviours.json`. Reproduce these. A rule seen once is marked as a single observation.\n\n"
            f"{bullet_list}\n\n"
        )
        text = text.replace(marker, section + marker, 1) if marker in text else f"{text}\n{section}"
    # The recorded steps in order (apps/<app>/steps/), what each action did, and identifiers.
    from labctl import stepfacts

    _, found = stepfacts.write_steps(root, app_id)
    # Seed rows and replay values, made together (labctl.seedplan): db/seed.sql and
    # steps/<recording>.values.yaml, and what the brief says about them.
    seeded = _seed(root, app_id, result, model)
    extra = (stepfacts.effects_section(found, app_id)
             + stepfacts.identifiers_section(found, list(result.tables), seeded=bool(seeded))
             + seeded)  # fmt: skip
    if extra:
        marker = "## Behaviour seen in the recordings"
        if marker not in text:
            marker = "## Running in the lab"
        text = text.replace(marker, extra + marker, 1) if marker in text else f"{text}\n{extra}"
    links = stepfacts.links_section(root, app_id, result, found)
    if links and "### Tables\n" in text:
        text = text.replace("### Tables\n", links + "### Tables\n", 1)
    if seeded:
        text = text.replace(
            "- Ship no customer data. A synthetic seed may be kept for local runs; the lab loads "
            "data packs over it.",
            "- Ship no customer data. Load `db/seed.sql` (synthetic) at start-up when the tables "
            "are empty: the acceptance replay depends on exactly those rows. The lab may load "
            "data packs over it.", 1)  # fmt: skip
    # A recording that went through several applications: where this one sits in that flow.
    from labctl import multiapp

    multiapp.sequence_copy(root, app_id)
    cross = multiapp.cross_app_section(root, app_id)
    if cross:
        marker = "## Running in the lab"
        text = text.replace(marker, cross + marker, 1) if marker in text else f"{text}\n{cross}"
    # The look (apps/<app>/look/): style sheets, design tokens, a picture per screen.
    from labctl import lookfeel

    try:
        lookfeel.write(root, app_id)
        look = lookfeel.brief_section(root, app_id)
    except lookfeel.LookError as exc:  # data in what would be carried: nothing is carried
        look = lookfeel.brief_section(root, app_id, refused=exc.places)
    if look:
        marker = "## Running in the lab"
        text = text.replace(marker, look + marker, 1) if marker in text else f"{text}\n{look}"
    _write(schema_file, brief_mod.schema(result, root))
    # The API catalogue, the navigation map, sign-in and the scope (labctl.contract).
    text = contract.extend(root, app_id, result, model, text, extras)
    # Field rules and page behaviour (labctl.pagerules): apps/<app>/rules.json, its brief section.
    from labctl import pagerules

    text = pagerules.extend(root, app_id, model, text)
    _write(brief_file, text)
    if not model.stack.desktop:  # the app pipeline builds a container; a desktop app has none
        write_workflow(root, app_id)
    kept = app_file.exists()
    made = brief_mod.app_yaml(result, root)
    if kept:  # generated again: only `run:`, approved rule levels and onboarding choices stay
        try:
            old = yaml.safe_load(app_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            old = {}
        made = brief_mod.merge_app_yaml(old if isinstance(old, dict) else {}, made)
    _write(app_file, yaml.safe_dump(made, sort_keys=False, width=100))
    files = [model_file, brief_file, schema_file, app_file]
    if behaviours_file.exists():
        files.append(behaviours_file)
    return Result(result, files, kept)


def _seed(root: Path, app_id: str, result, model) -> str:
    """Write the seed and the replay values of an app with a server database; the brief's
    section on them ("" when there is nothing to seed: no recording, browser or desktop data)."""
    from labctl import seedplan

    out = root / "apps" / app_id
    if result.browser_only or model.stack.desktop or not result.tables:
        for name in (seedplan.SEED_SQL, seedplan.SEED_JSON):
            (out / "db" / name).unlink(missing_ok=True)
        for old in (out / "steps").glob(f"*{seedplan.VALUES}") if (out / "steps").is_dir() else []:
            old.unlink()
        return ""
    try:
        tables = brief_mod.sam_tables(model, list(result.tables))
        done = seedplan.write_app(root, app_id, tables, result.db_engine)
    except Exception as exc:  # noqa: BLE001 - the brief says so; the rest of it still helps
        return ("## Seed data and replay values\n\nNot generated (an error in labctl: "
                f"{type(exc).__name__}: {exc}). Seed the tables by hand from *Identifiers and seed "
                "data* and say so in BUILD-NOTES.md.\n\n")  # fmt: skip
    return seedplan.brief_section(app_id, done)


def chosen_engine(root: Path, app_file: Path) -> str | None:
    """The engine of the database chosen for the app at onboarding (`lab.database` in its
    app.yaml), or None when none was chosen (or it names a server this machine does not know)."""
    from labctl import dbservers

    try:
        conf = yaml.safe_load(app_file.read_text(encoding="utf-8")) if app_file.is_file() else {}
    except (OSError, yaml.YAMLError):
        return None
    named = ((conf or {}).get("lab") or {}).get("database") if isinstance(conf, dict) else None
    if not named:
        return None
    try:
        engine = dbservers.engine_for(root, str(named))
    except Exception:  # noqa: BLE001 - an unknown server: the brief says nothing was chosen
        return None
    return {"postgresql": "postgres"}.get(engine, engine)


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
    _write(path, WORKFLOW.format(app=app_id))
    return path


def write_behaviours(app_id: str, recordings: list[Path], path: Path) -> list[str]:
    """Behaviour catalog from the recordings that have page snapshots; returns lines for the brief."""
    usable = [r for r in recordings if r.is_dir() and (r / "steps.jsonl").exists()]
    if not usable:
        if path.exists():
            path.unlink()
        return []
    from labctl.behaviour import catalog as catalog_mod

    found = catalog_mod.build(usable, app=app_id)
    catalog_mod.write(found, path)
    return catalog_mod.brief_lines(found)


GENERATED = ("BRIEF.md", "model.yaml", "db/schema.sql", "db/seed.sql", "db/seed.json",
             "behaviours.json", "pages", "steps", "look", "api",
             "navigation.json", "rules.json", "scripts")  # fmt: skip


def backup(root: Path, app_id: str) -> Path | None:
    """Copy the generated files about to be replaced to .lab-state/backups/<app>/<time>/."""
    from datetime import UTC, datetime

    from labctl.lab import state_dir

    out = root / "apps" / app_id
    present = [out / name for name in GENERATED if (out / name).exists()]
    if not present:
        return None
    target = state_dir(root) / "backups" / app_id / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for path in present:
        dest = target / path.relative_to(out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, dest)
        else:
            shutil.copy2(path, dest)
    return target


def _write(path: Path, text: str) -> None:
    """Generated files always have Unix line endings, on every machine, so their checksums in a
    Devin package match the copies in the repository."""
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
