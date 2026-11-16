"""Finding and loading contract files in a Twin Lab repository."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from labctl import appmodel  # noqa: F401  (registers the model and flow contracts)
from labctl.models import KINDS, Contract

ROOT_MARKERS = ("automations", "apps", "lab")


class NotARepo(Exception):
    pass


def find_root(start: Path | None = None) -> Path:
    """The repository root: LABCTL_ROOT, or the nearest parent holding the top-level folders."""
    env = os.environ.get("LABCTL_ROOT")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if all((candidate / m).is_dir() for m in ROOT_MARKERS):
            return candidate
    raise NotARepo(f"no Twin Lab repository found at or above {here} (set LABCTL_ROOT)")


def kind_of(path: Path) -> str | None:
    """Which contract a file holds, from its name or folder. None if it is not a contract file."""
    if path.suffix not in (".yaml", ".yml", ".json"):
        return None
    if path.parent.name == "conditions":
        return "condition"
    if path.parent.name == "flows":
        return "flow"
    stem = path.name.split(".")[0]
    return stem if stem in KINDS else None


@dataclass(frozen=True)
class Problem:
    path: Path
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.field}: {self.message}"


def load(path: Path, kind: str | None = None) -> tuple[Contract | None, list[Problem]]:
    """Parse and validate one file. Returns the model, or the problems naming file and field."""
    kind = kind or kind_of(path)
    if kind not in KINDS:
        return None, [Problem(path, "-", "cannot tell which contract this is; pass --kind")]
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return None, [Problem(path, "-", f"cannot read: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(path, "-", "expected a mapping at the top level")]
    try:
        return KINDS[kind].model_validate(data), []
    except ValidationError as exc:
        return None, [
            Problem(path, ".".join(str(p) for p in e["loc"]) or "-", e["msg"]) for e in exc.errors()
        ]


def contract_files(root: Path) -> list[Path]:
    """Every contract file the repository is expected to hold, in a stable order."""
    patterns = (
        "automations/*/automation.yaml",
        "apps/*/app.yaml",
        "apps/*/model.yaml",
        "flows/*.yaml",
        "data-packs/*/pack.yaml",
        "conditions/*.yaml",
        "specs/*.example.yaml",
        "specs/*.example.json",
    )
    return [p for pattern in patterns for p in sorted(root.glob(pattern))]


def files_of(root: Path, kind: str) -> list[Path]:
    return [p for p in contract_files(root) if p.parent.name != "specs" and kind_of(p) == kind]
