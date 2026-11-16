"""Copies of an app's lab, one per run: `labctl lab copy showcase` starts `showcase.run-1`, a lab
of its own (database, agent, app) on free ports, with the data the app's lab has as its
baseline. A run on a copy cannot disturb anyone, and anyone cannot disturb it. Drop it after.

The copy uses the app's image and schema; only the Compose project, the ports and the data are
its own. Its record (ports, where its data came from) is kept in `.lab-state/copies/`.
"""

from __future__ import annotations

import re
import socket
from datetime import UTC, datetime
from pathlib import Path

import yaml

from labctl.models import App

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
FIRST_PORT = 18000  # copies' ports: from here up, away from the apps' own


class CopyError(Exception):
    pass


def _dir(root: Path) -> Path:
    from labctl import lab

    path = lab.state_dir(root) / "copies"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(root: Path, lab_id: str) -> Path:
    return _dir(root) / f"{lab_id}.yaml"


def record(root: Path, lab_id: str) -> dict | None:
    path = _record_path(root, lab_id)
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


def apply(root: Path, app: App, copy: str) -> App:
    """The app as its copy runs it: the same app, the copy's ports."""
    from labctl.bindings import BindingError

    rec = record(root, f"{app.id}.{copy}")
    if rec is None:
        raise BindingError(f"there is no copy {copy!r} of {app.id} (labctl lab copies {app.id})")
    if app.lab is None:
        raise BindingError(f"apps/{app.id}/app.yaml has no `lab:` section")
    lab = app.lab.model_copy(update={"port": rec["port"], "db_port": rec["db_port"]})
    return app.model_copy(update={"lab": lab})


def listing(root: Path, app_id: str | None = None) -> list[dict]:
    out = []
    for path in sorted(_dir(root).glob("*.yaml")):
        rec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if app_id is None or rec.get("app") == app_id:
            out.append(rec)
    return out


def _used_ports(root: Path) -> set[int]:
    used: set[int] = set()
    for path in (root / "apps").glob("*/app.yaml"):
        try:
            lab = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("lab") or {}
        except yaml.YAMLError:
            continue
        used |= {p for p in (lab.get("port"), lab.get("db_port")) if isinstance(p, int)}
    for rec in listing(root):
        used |= {rec.get("port"), rec.get("db_port")}
    return used


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_ports(root: Path, count: int = 2, start: int = FIRST_PORT) -> list[int]:
    used, out, port = _used_ports(root), [], start
    while len(out) < count:
        if port > 65000:
            raise CopyError("no free ports for a copy")
        if port not in used and _free(port):
            out.append(port)
        port += 1
    return out


def _next_name(root: Path, app_id: str) -> str:
    taken = {r["lab"] for r in listing(root, app_id)}
    n = 1
    while f"{app_id}.run-{n}" in taken:
        n += 1
    return f"run-{n}"


def create(
    root: Path, app_id: str, name: str | None = None, data: str = "baseline", build: bool = True
) -> dict:
    """Start a copy of `app_id`'s lab. `data`: "baseline" (the app's lab baseline when that lab
    is running, else the app's own starting data), "current" (the app lab's data as it is now),
    "app" (the app's own starting data) or a data pack name. Returns the copy's record."""
    from labctl import dbservers, lab, packs
    from labctl.bindings import load_app

    app = load_app(root, app_id)
    if app.lab is None:
        raise CopyError(f"apps/{app_id}/app.yaml has no `lab:` section")
    if dbservers.choice(app.lab.database) not in ("postgres", "sqlserver"):
        raise CopyError(
            f"{app_id} runs on the server {app.lab.database!r}: a copy needs a database of its own, "
            "so copies run on Twin Lab's own databases (postgres or sqlserver) only"
        )
    name = name or _next_name(root, app_id)
    if not NAME.match(name):
        raise CopyError(f"copy name {name!r}: lower-case letters, digits and dashes")
    lab_id = f"{app_id}.{name}"
    if record(root, lab_id):
        raise CopyError(f"{lab_id} exists already; drop it first (labctl lab drop {lab_id})")
    rows = None
    source = data
    if data in ("baseline", "current"):
        try:
            parent = lab.agent(root, app_id)
            rows = parent.baseline_rows() if data == "baseline" else parent.data()
        except lab.LabError:
            source = "app"  # the app's lab is not running: the app's own starting data
        if data == "baseline" and rows is None and source == "baseline":
            source = "app"
    elif data != "app":
        try:
            rows = packs.read(root, app_id, data)["rows"]
        except packs.PackError as exc:
            raise CopyError(str(exc)) from exc
        source = f"pack {data}"
    port, db_port = free_ports(root)
    rec = {"lab": lab_id, "app": app_id, "name": name, "port": port, "db_port": db_port,
           "data": source, "created": datetime.now(UTC).isoformat(timespec="seconds")}  # fmt: skip
    _record_path(root, lab_id).write_text(yaml.safe_dump(rec, sort_keys=False), encoding="utf-8")
    try:
        lab.up(root, lab_id, build=build)
        if rows:
            lab.agent(root, lab_id).load(rows, f"copy of {app_id} ({source})")
    except Exception:
        try:
            drop(root, lab_id)
        except Exception:  # noqa: BLE001 - the first error is the one worth reporting
            pass
        raise
    return rec


def drop(root: Path, lab_id: str) -> None:
    """Stop the copy and delete its containers and data."""
    from labctl import lab
    from labctl.bindings import split_lab_id

    base, copy = split_lab_id(lab_id)
    if not copy:
        raise CopyError(f"{lab_id} is an app's own lab, not a copy; use labctl lab down")
    if record(root, lab_id) is None:
        raise CopyError(f"there is no copy {lab_id}")
    try:
        lab.down(root, lab_id, wipe=True)
    finally:
        _record_path(root, lab_id).unlink(missing_ok=True)
        (lab.state_dir(root) / "compose" / f"{lab_id}.yml").unlink(missing_ok=True)
