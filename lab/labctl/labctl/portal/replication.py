"""The portal side of the replication workflow (R10).

What a person does here, after the applications exist and their lab is up: fill the synthetic
values (`bindings.yaml`), declare the baseline, the acceptance cases, what is out of scope and the
pictures (`baseline.yaml`, `acceptance.yaml`, `scope.yaml`, `visual.yaml`), replay, and read the
verdict and the coverage matrix.

The judging inputs are kept apart from the implementation. They live beside the recording, in
`evidence/<app>/<recording>/`, which Git ignores: no pull request carries them, so a builder
cannot change them. `tools/guard_ai_changes.py` protects `automations/`, `flows/`, `lab/` and
`conditions/` on AI-built pull requests; it does not look at `evidence/`. This module refuses to
write a judging file anywhere under `apps/`, where the builder works. The behaviour modules do the
parsing and validation (their own loaders); nothing is validated twice here.
"""

from __future__ import annotations

import inspect
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from labctl import coverage as coverage_mod
from labctl import factory
from labctl import lab as lab_mod
from labctl.behaviour import acceptance as acc_mod
from labctl.behaviour import baseline as base_mod
from labctl.behaviour import bindings as bnd_mod
from labctl.behaviour import durable, fidelity
from labctl.behaviour import visual as vis_mod

KINDS = ("bindings", "baseline", "acceptance", "scope", "visual")
FILES = {
    "bindings": (
        "bindings.yaml",
        "Synthetic values",
        "The values the replay types or selects, one per step, and the records a step "
        "acts on. Made up, never real.",
    ),  # fmt: skip
    "baseline": (
        "baseline.yaml",
        "Baseline",
        "The browser data the replay starts from (localStorage, sessionStorage, cookies, "
        "IndexedDB): synthetic fixtures. Empty is an empty baseline.",
    ),  # fmt: skip
    "acceptance": (
        "acceptance.yaml",
        "Acceptance cases",
        "What must be true after a step or at the end, written independently of the "
        "replica: texts, counts, saved values, persistence. Also scope, exclusions, and "
        "what is unobserved or unsupported.",
    ),  # fmt: skip
    "scope": (
        coverage_mod.SCOPE_FILE,
        "Scope exclusions",
        "What is out of scope, with the reason, set before the replay. Optional: "
        "acceptance.yaml's excluded: list does the same.",
    ),  # fmt: skip
    "visual": (
        "visual.yaml",
        "Pictures",
        "Which screens are compared as pictures, the viewport, thresholds and regions left "
        "out. Set before any comparison.",
    ),  # fmt: skip
}
TODO_LINE = re.compile(r"(^|[:\-\s\[,])TODO\s*($|[,\]#])")
REPLICA_PARAMS = ("replicas", "replica_map", "origin_map", "base_urls", "origins")
ARTIFACT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".gif": "image/gif", ".webp": "image/webp"}  # fmt: skip
GUARD_NOTE = (
    "Kept beside the recording in evidence/, which Git ignores: no pull request carries these "
    "files, and the portal refuses to write them under apps/, where a builder works. The guard "
    "workflow (tools/guard_ai_changes.py) protects automations/, flows/, lab/ and conditions/ on "
    "AI-built pull requests; it does not look at evidence/, so these files are protected by not "
    "being in the repository."
)


class Refused(Exception):
    """A judging file may not be written there."""


# --- where files live ----------------------------------------------------------------------------


def recording_path(root: Path, app_id: str, name: str) -> Path | None:
    for rec in factory.discover(root, app_id).recordings:
        if rec.is_dir() and rec.name == name and (rec / "steps.jsonl").is_file():
            return rec
    return None


def file_path(root: Path, rec: Path, kind: str) -> Path:
    """The judging file's place beside the recording. Refuses anything under `apps/`, and
    anything that is not inside the repository's `evidence/` folder."""
    if kind not in FILES:
        raise Refused(f"{kind} is not a declaration file")
    target = (Path(rec) / FILES[kind][0]).resolve()
    apps = (Path(root) / "apps").resolve()
    evidence = (Path(root) / "evidence").resolve()
    if target == apps or apps in target.parents:
        raise Refused(
            f"{FILES[kind][0]} is judging input and is never written under apps/: the builder "
            "works there and must not be able to change what judges it"
        )
    if evidence not in target.parents:
        raise Refused(f"{FILES[kind][0]} is written only beside a recording under evidence/")
    return target


# --- reading and checking ---------------------------------------------------------------------


@dataclass
class Check:
    hard: list[str] = field(default_factory=list)  # the file cannot be saved
    problems: list[str] = field(default_factory=list)  # saved, but the run is blocked
    todo: list[int] = field(default_factory=list)  # lines still holding TODO


def todo_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1)
            if not line.lstrip().startswith("#") and TODO_LINE.search(line)]  # fmt: skip


def _load_recording(rec: Path):
    from labctl.botlog.recording import load

    return load(rec)


def check_text(kind: str, text: str, rec: Path) -> Check:
    """Validate with the behaviour module's own loader, on a temporary copy of the text."""
    out = Check(todo=todo_lines(text))
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        out.hard.append(f"not valid YAML: {str(exc).splitlines()[0] if str(exc) else exc}")
        return out
    if data is None:
        data = {}
    if not isinstance(data, dict):
        out.hard.append("the file must be a mapping (key: value) at the top level")
        return out
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / FILES[kind][0]
        path.write_text(text, encoding="utf-8")
        try:
            if kind == "bindings":
                loaded = bnd_mod.load(path)
                out.problems += _binding_gaps(rec, loaded)
                out.problems += [f"step {s}: the value is still TODO"
                                 for s, v in {**loaded.values, **loaded.records}.items()
                                 if v.strip().upper() == "TODO"]  # fmt: skip
            elif kind == "acceptance":
                spec = acc_mod.load(path)
                out.problems += [f"case {c.id}: {c.problem}" for c in spec.cases if c.problem]
                if spec.cases and not any(c.required and not c.problem for c in spec.cases):
                    out.problems.append("no required case can run: nothing would be judged")
            elif kind == "baseline":
                base = base_mod.load(path)
                out.problems += list(base.problems if base else [])
            elif kind == "visual":
                spec = vis_mod.load(path)
                out.problems += [f"state {s.id}: {s.problem}" for s in spec.states if s.problem]
            elif kind == "scope":
                for key in ("excluded", "unobserved", "unsupported", "scope"):
                    if key in data and not isinstance(data[key], list):
                        out.hard.append(f"{key}: must be a list")
        except (ValueError, TypeError, yaml.YAMLError, OSError) as exc:
            out.hard.append(str(exc).replace(str(path), FILES[kind][0]))
    if kind == "acceptance" and out.todo:
        out.problems.append(f"{len(out.todo)} TODO value(s) at line(s) "
                            + ", ".join(map(str, out.todo[:8])) + ": a TODO blocks its case")  # fmt: skip
    elif out.todo and kind != "bindings":
        out.problems.append(f"TODO value(s) at line(s) {', '.join(map(str, out.todo[:8]))}")
    return out


def _binding_gaps(rec: Path, loaded) -> list[str]:
    try:
        recording = _load_recording(rec)
        needs = bnd_mod.needs(recording)
    except Exception as exc:  # noqa: BLE001 - an unreadable recording is reported, not raised
        return [f"the recording cannot be read to list the values it needs: {exc}"]
    out = []
    for n in needs:
        given = loaded.values if n.kind == "value" else loaded.records
        if n.seq not in given:
            what = "type into" if n.kind == "value" else "act on a record in"
            out.append(f"step {n.seq}: no {n.kind} for the step that must {what} {n.field or '?'}")
    return out


def read_declaration(root: Path, rec: Path, kind: str) -> dict:
    path = file_path(root, rec, kind)
    if not path.exists():
        gaps: list[str] = []
        if kind == "bindings":
            gaps = _binding_gaps(rec, bnd_mod.Bindings())
        return {"kind": kind, "name": FILES[kind][0], "title": FILES[kind][1],
                "help": FILES[kind][2], "exists": False, "text": "", "hard": [],
                "problems": gaps, "todo": [], "state": "blocks" if gaps else "missing"}  # fmt: skip
    text = path.read_text(encoding="utf-8")
    check = check_text(kind, text, rec)
    state = "invalid" if check.hard else "blocks" if check.problems else "ok"
    return {"kind": kind, "name": FILES[kind][0], "title": FILES[kind][1],
            "help": FILES[kind][2], "exists": True, "text": text, "hard": check.hard,
            "problems": check.problems, "todo": check.todo, "state": state}  # fmt: skip


def declarations(root: Path, rec: Path) -> list[dict]:
    return [read_declaration(root, rec, k) for k in KINDS]


def blockers(root: Path, rec: Path) -> list[str]:
    """Why a replay cannot give a meaningful result yet, in the words the page shows."""
    out = []
    for d in declarations(root, rec):
        for p in d["hard"] + d["problems"]:
            out.append(f"{d['name']}: {p}")
    return out


def notes(root: Path, rec: Path) -> list[str]:
    """What the result will lack, without blocking the run."""
    out = []
    if not file_path(root, rec, "acceptance").exists():
        out.append("No acceptance.yaml: the verdict will be BLOCKED, because structure alone "
                   "never earns PASS WITHIN DECLARED SCOPE.")  # fmt: skip
    if not file_path(root, rec, "visual").exists():
        out.append("No visual.yaml: appearance is not compared (declared before any comparison).")
    return out


def save_declaration(root: Path, rec: Path, kind: str, text: str) -> Check:
    """Write the file when the loader accepts it. Returns the check; `hard` means not saved."""
    path = file_path(root, rec, kind)  # raises Refused
    text = text.replace("\r\n", "\n")
    check = check_text(kind, text, rec)
    if check.hard:
        return check
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") or not text else text + "\n", encoding="utf-8",
                    newline="\n")  # fmt: skip
    return check


SCOPE_TEMPLATE = """# What is out of scope, set before the replay (an exclusion made after seeing a failure
# does not count). Each item: the requirement's id or a phrase from its description, and why.
version: 1
excluded:
  - id: TODO
    reason: TODO
unobserved: []
unsupported: []
"""


def write_template(root: Path, rec: Path, kind: str) -> Path:
    """The behaviour module's own template function; the scope file is ours."""
    path = file_path(root, rec, kind)
    if kind == "scope":
        if path.exists():
            raise FileExistsError(f"{path.name} exists: it is not overwritten")
        path.write_text(SCOPE_TEMPLATE, encoding="utf-8", newline="\n")
        return path
    recording = _load_recording(rec)
    module = {"bindings": bnd_mod, "baseline": base_mod, "acceptance": acc_mod,
              "visual": vis_mod}[kind]  # fmt: skip
    return Path(module.write_template(recording, path))


# --- replay ------------------------------------------------------------------------------------


def artifacts_root(state: Path, app_id: str) -> Path:
    return state / "artifacts" / app_id


def resolve_artifact(state: Path, app_id: str, rel: str) -> Path | None:
    """A kept picture, or None. Only files under the state folder's artifacts for this app, only
    pictures, never through `..`, an absolute path or a link that leaves the folder."""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel:
        return None
    parts = Path(rel.replace("\\", "/")).parts
    if any(p in ("..", ".", "") for p in parts):
        return None
    base = artifacts_root(state, app_id).resolve()
    target = (base / Path(*parts)).resolve()
    if base not in target.parents or target.suffix.lower() not in ARTIFACT_TYPES:
        return None
    return target if target.is_file() else None


def artifact_url(state: Path, app_id: str, path: str | None) -> str | None:
    """The URL of a picture a report names, when it is in this app's artifact folder."""
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(artifacts_root(state, app_id).resolve())
    except (ValueError, OSError):
        return None
    if resolve_artifact(state, app_id, rel.as_posix()) is None:
        return None
    return f"/apps/{app_id}/artifacts/{rel.as_posix()}"


def accepted(fn) -> set[str] | None:
    """The names `fn` takes, or None when it takes anything."""
    params = inspect.signature(fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    return set(params)


def replica_param(fn) -> str | None:
    """The keyword with which `fn` takes a source-origin to replica-origin map, if it does."""
    names = accepted(fn)
    if names is None:
        return None
    return next((n for n in REPLICA_PARAMS if n in names), None)


def localise(report, folder: Path) -> None:
    """Copy every picture the report names into the artifact folder and point the report at the
    copies, so the portal serves pictures from the state folder only."""
    folder.mkdir(parents=True, exist_ok=True)
    taken: dict[str, str] = {}

    def keep(value: str | None) -> str | None:
        if not value:
            return value
        if value in taken:
            return taken[value]
        src = Path(value)
        try:
            inside = folder.resolve() in src.resolve().parents
        except OSError:
            inside = False
        if inside or not src.is_file() or src.suffix.lower() not in ARTIFACT_TYPES:
            taken[value] = value
            return value
        dest = folder / "source" / f"{len(taken):03d}-{src.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        taken[value] = str(dest)
        return str(dest)

    d = report.first_divergence
    if d is not None:
        d.before, d.after, d.diff = keep(d.before), keep(d.after), keep(d.diff)
    for c in report.cases:
        c.screenshot = keep(c.screenshot)
    for v in report.visual:
        v.before, v.after_path, v.diff = keep(v.before), keep(v.after_path), keep(v.diff)


def run_replay(root: Path, state: Path, app_id: str, rec: Path, base_url: str, *,
               given: dict[str, str] | None = None, viewport: str | None = None,
               replicas: dict[str, str] | None = None, apps: list[str] | None = None,
               say=print) -> dict:  # fmt: skip
    """Replay `rec` with the declarations beside it and keep the report where readiness and the
    app page find it. `fidelity.replay` is looked up when called and given only the keywords it
    accepts; a replica map is passed only when it takes one."""
    fn = fidelity.replay
    names = accepted(fn)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    folder = artifacts_root(state, app_id) / f"{rec.name}-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"artifacts": folder}
    if given:
        kwargs["values"] = given
    if viewport:
        kwargs["viewport"] = viewport
    mapkey = replica_param(fn)
    if replicas:
        if mapkey is None:
            raise RuntimeError(
                "replay across applications is not available in this build: fidelity.replay "
                "takes one base address. Replay each application's own recording from its page.")  # fmt: skip
        kwargs[mapkey] = replicas
    if names is not None:
        kwargs = {k: v for k, v in kwargs.items() if k in names}
    say(f"Replaying {rec.name} against {base_url} with the declarations beside it ...")
    for d in declarations(root, rec):
        say(f"  {d['name']}: " + ("not declared" if not d["exists"] else d["state"]))
    report = fn(rec, base_url, **kwargs)
    localise(report, folder)
    saved_for = apps or [app_id]
    names_saved = []
    for n, target in enumerate(saved_for):
        item = report if n == 0 else report.model_copy(deep=True)
        if n and isinstance(item.fingerprint, dict):
            item.fingerprint = {k: v for k, v in item.fingerprint.items() if k != "app"}
        names_saved.append((target, fidelity.save(root, target, item)))
    name = names_saved[0][1]
    verdict = report.verdict or report.status.upper()
    say(f"Verdict: {verdict}")
    if report.first_divergence:
        d = report.first_divergence
        say(f"First divergence: {d.kind} {d.what} at step {d.step}: {d.state}")
    cov = report.coverage
    if cov:
        say(f"Steps {cov.steps}: replayed {cov.replayed}, failed {cov.failed}, skipped "
            f"{cov.skipped}, unresolved {cov.unresolved}")  # fmt: skip
    try:  # the matrix reads the new report
        coverage_mod.save(root, coverage_mod.build(root, app_id))
        say("Coverage matrix refreshed.")
    except coverage_mod.CoverageError as exc:
        say(f"Coverage matrix not refreshed: {exc}")
    return {
        "status": report.status, "verdict": verdict, "report": f"/apps/{app_id}/reports/{name}",
        "links": [{"href": f"/apps/{app_id}/reports/{name}", "text": "Open the report"},
                  {"href": f"/apps/{app_id}/coverage", "text": "Coverage matrix"},
                  {"href": f"/apps/{app_id}/recordings/{rec.name}", "text": "The recording's files"}],
        "artifacts": folder.relative_to(state).as_posix(),
    }  # fmt: skip


# --- what the app page and the next step need ---------------------------------------------------

REPORT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*--\d{8}T\d{6}Z$")


def latest_report(root: Path, app_id: str):
    """(name, report, freshness) of the newest kept replay report, or None."""
    found = fidelity.latest(root, app_id)
    if found is None:
        return None
    name, report = found
    return name, report, durable.assess(root, app_id, report)


def app_summary(root: Path, state: Path, app_id: str) -> dict:
    """Everything the app page's replication section and the next-step logic read."""
    recs = []
    for rec in factory.discover(root, app_id).recordings:
        if not (rec.is_dir() and (rec / "steps.jsonl").is_file()):
            continue
        decls = declarations(root, rec)
        recs.append({"name": rec.name, "files": decls,
                     "blocked": [f"{d['name']}: {p}" for d in decls for p in d["hard"] + d["problems"]],
                     "todo": sum(len(d["todo"]) for d in decls)})  # fmt: skip
    latest = latest_report(root, app_id)
    saved = coverage_mod.load_saved(root, app_id)
    cov = None
    if saved is not None:
        state_, why = coverage_mod.freshness(root, app_id, saved)
        cov = {"report": saved, "state": state_, "reasons": why}
    return {"recordings": recs, "latest": latest, "coverage": cov}


def report_view(root: Path, state: Path, app_id: str, name: str) -> dict | None:
    """A kept report with its freshness and the URLs of the pictures that may be served."""
    if not REPORT_NAME.match(name):
        return None
    path = fidelity.reports_dir(root, app_id) / f"{name}.json"
    try:
        report = fidelity.FidelityReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fresh = durable.assess(root, app_id, report)

    def pic(p):
        return artifact_url(state, app_id, p)

    d = report.first_divergence
    pictures = []
    if d is not None:
        for label, p in (("Source (the recording)", d.before), ("Replica", d.after),
                         ("Difference", d.diff)):  # fmt: skip
            pictures.append({"label": label, "url": pic(p), "given": bool(p)})
    return {"name": name, "report": report, "fresh": fresh, "pictures": pictures,
            "verdict": report.verdict or report.status.upper(),
            "legacy": report.verdict is None, "fingerprint_text": durable.describe(report.fingerprint),
            "case_pics": {c.id: pic(c.screenshot) for c in report.cases if c.screenshot},
            "visual_pics": {v.id: [pic(v.before), pic(v.after_path), pic(v.diff)] for v in report.visual}}  # fmt: skip


def replica_map(root: Path, rec_info: dict) -> tuple[dict[str, str], list[str]]:
    """{source origin: replica address} and the apps, from a combined recording's saved proposal
    and each included application's lab port."""
    from labctl import multiapp, repo
    from labctl import split as split_mod

    prop_path = multiapp.proposal_file(rec_info["path"])
    if not prop_path.is_file():
        return {}, []
    prop = split_mod.load(prop_path)
    mapping: dict[str, str] = {}
    apps: list[str] = []
    for e in prop.apps:
        if not e.include:
            continue
        model, _ = repo.load(root / "apps" / e.id / "app.yaml", "app")
        if model is None or model.lab is None:
            continue
        mapping[e.origin] = f"http://127.0.0.1:{model.lab.port}"
        apps.append(e.id)
    return mapping, apps


def lab_running(root: Path, app_id: str) -> bool:
    try:
        status = lab_mod.status(root, app_id)
    except Exception:  # noqa: BLE001 - an unreadable lab is a lab that is not up
        return False
    return bool(status and status.running)
