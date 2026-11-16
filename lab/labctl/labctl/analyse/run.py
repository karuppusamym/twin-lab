"""Analyse a registered automation by its type; one entry point for `labctl` and the portal.

- UiPath: the whole project is read (screens, routes, tables, fields), replacing `touches`.
- Python: what it touches stays as a person declared it; the code fills in, per declared route,
  the fields it sends and reads. Calls the code makes that no touch lists become notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from labctl.analyse import python as pyscan
from labctl.analyse import uipath
from labctl.models import Automation, Gap

PYTHON_VERSION = "python-fields/1"


class AnalysisError(Exception):
    pass


@dataclass
class Outcome:
    data: dict  # automation.yaml content, to validate and save
    files: list[str] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    notes: list[Gap] = field(default_factory=list)


def analyse(folder: Path, model: Automation) -> Outcome:
    """`folder` is `automations/<id>/`."""
    kind = model.type.value
    data = model.model_dump(mode="json", exclude_none=True)
    if kind == "uipath":
        project = folder / (model.source.path or "project")
        try:
            result = uipath.analyse(project, model.source.entry, model.bindings)
        except uipath.AnalysisError as exc:
            raise AnalysisError(str(exc)) from exc
        data["touches"] = result.touches
        out = Outcome(data, result.files, result.gaps, result.notes)
        by = uipath.VERSION
    elif kind == "python":
        out = _python(folder, model, data)
        by = PYTHON_VERSION
    else:
        raise AnalysisError(f"no analyser yet for type '{kind}'")
    data["analysis"] = {
        "by": by,
        "gaps": [g.model_dump() for g in out.gaps],
        "notes": [n.model_dump() for n in out.notes],
        "reviewed_by": None,
    }
    return out


def _python(folder: Path, model: Automation, data: dict) -> Outcome:
    base = (folder / (model.source.path or ".")).resolve()
    if not base.is_dir() or not base.is_relative_to(folder.resolve()):
        raise AnalysisError("source.path must be a folder inside the automation's folder")
    files = sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    if not files:
        raise AnalysisError(f"no .py files under {base.relative_to(folder.resolve()) or '.'}")
    found = pyscan.analyse(files)
    out = Outcome(data, [p.relative_to(base).as_posix() for p in files])
    # What touches is declared by a person here, so their notes stay; ours are added after.
    mine = ("the code calls ", "no HTTP call with a literal path")
    out.notes = [n for n in model.analysis.notes if not n.what.startswith(mine)]
    out.gaps = [g for g in model.analysis.gaps if not g.what.startswith(mine)]
    used = set()
    for touch in data.get("touches", []):
        if touch.get("via") != "api":
            continue
        for route in touch["routes"]:
            key = next(
                (
                    k
                    for k in found
                    if k[0] == route["method"] and pyscan.shape(k[1]) == pyscan.shape(route["path"])
                ),
                None,
            )
            if key is None:
                continue
            used.add(key)
            route["sends"] = found[key].sends
            route["reads"] = found[key].reads
    for key in found:
        if key not in used:
            out.notes.append(
                Gap(
                    where=out.files[0] if len(out.files) == 1 else "source",
                    what=f"the code calls {key[0]} {key[1]}, which no `touches` entry lists; "
                    "add it if the demo app must serve it",
                )
            )
    if not found and any(t.get("via") == "api" for t in data.get("touches", [])):
        out.gaps.append(
            Gap(where="source", what="no HTTP call with a literal path found; fields are unknown")
        )
    return out
