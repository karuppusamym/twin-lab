"""Named data packs: a saved copy of an app's lab data, loaded again by name.

A pack is the rows of every table at the moment it was saved, under
`.lab-state/packs/<app>/<name>.json`. It stays on this machine (it may hold masked real data, so it
is never committed). Loading a pack replaces the lab's data and makes it the baseline, labelled
with the pack's name, so sessions say which data they ran on and Reset returns to it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labctl import lab as lab_mod

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class PackError(Exception):
    pass


@dataclass
class PackInfo:
    name: str
    saved_at: str
    label: str | None
    counts: dict[str, int]
    from_baseline: str | None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def folder(root: Path, app_id: str) -> Path:
    return lab_mod.state_dir(root) / "packs" / app_id


def _path(root: Path, app_id: str, name: str) -> Path:
    if not NAME.match(name):
        raise PackError("a pack name is lower-case letters, digits and dashes (at most 63)")
    return folder(root, app_id) / f"{name}.json"


def save(root: Path, app_id: str, name: str, rows: dict, label: str | None = None,
         from_baseline: str | None = None, overwrite: bool = False) -> PackInfo:  # fmt: skip
    path = _path(root, app_id, name)
    if path.exists() and not overwrite:
        raise PackError(f"pack {name} exists already; choose another name or replace it")
    if not isinstance(rows, dict) or not rows:
        raise PackError("there is no data to save")
    doc = {
        "pack": name,
        "app": app_id,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "label": label,
        "from_baseline": from_baseline,
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return _info(doc)


def _info(doc: dict) -> PackInfo:
    rows = doc.get("rows") or {}
    return PackInfo(
        name=doc.get("pack", ""),
        saved_at=doc.get("saved_at", ""),
        label=doc.get("label"),
        counts={t: len(r) for t, r in rows.items()},
        from_baseline=doc.get("from_baseline"),
    )


def read(root: Path, app_id: str, name: str) -> dict:
    path = _path(root, app_id, name)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackError(f"no pack {name} for {app_id}") from exc
    except (OSError, ValueError) as exc:
        raise PackError(f"pack {name} cannot be read: {exc}") from exc
    if not isinstance(doc.get("rows"), dict):
        raise PackError(f"pack {name} has no rows")
    return doc


def listing(root: Path, app_id: str) -> list[PackInfo]:
    out = []
    for path in sorted(folder(root, app_id).glob("*.json")):
        try:
            out.append(_info(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def delete(root: Path, app_id: str, name: str) -> None:
    path = _path(root, app_id, name)
    if not path.exists():
        raise PackError(f"no pack {name} for {app_id}")
    path.unlink()


def save_from_lab(root: Path, app_id: str, name: str, label: str | None = None,
                  overwrite: bool = False) -> PackInfo:  # fmt: skip
    """Save what the running lab holds now."""
    agent = lab_mod.agent(root, app_id)
    rows = agent.data()
    baseline = (agent.info().get("baseline") or {}).get("id")
    return save(root, app_id, name, rows, label, baseline, overwrite)


def load_into_lab(root: Path, app_id: str, name: str) -> dict:
    """Replace the lab's data with the pack and make it the baseline."""
    doc = read(root, app_id, name)
    return lab_mod.agent(root, app_id).load(doc["rows"], doc.get("label") or name, pack=name)
