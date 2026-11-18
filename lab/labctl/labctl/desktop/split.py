"""Split a desktop recording that used several applications into one evidence set per
application, as labctl.split does for web recordings.

`propose(recording)` groups the steps by the executable they belong to (`process`); the proposal
is a small YAML file a person edits: rename an id, give two entries the same id to merge them,
set `include: false` to drop one. `apply(recording, proposal, root)` writes
`root/evidence/<app>/<recording>/` in the desktop recording format (`desktop.json`,
`steps.jsonl`, `trees/`, `screens/`), each step keeping its number in the whole recording as
`global_seq`, and the order across applications in `root/evidence/_sequences/<recording>.flow.yaml`
(rows carry `surface: desktop`). `interleave()` puts desktop and web recordings of the same session
on one timeline by their timestamps. The original recording is never changed.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from labctl.desktop import SURFACE

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEQUENCES = "_sequences"
KNOWN_KEYS = {"id", "process", "include", "steps", "windows"}


class ProposalError(ValueError):
    pass


@dataclass
class Entry:
    id: str
    process: str
    include: bool = True
    steps: int = 0
    windows: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "process": self.process, "include": self.include,
                "steps": self.steps, "windows": self.windows}  # fmt: skip


@dataclass
class Proposal:
    recording: str
    apps: list[Entry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"recording": self.recording, "surface": SURFACE,
                "apps": [e.to_dict() for e in self.apps]}  # fmt: skip


def suggest_id(process: str) -> str:
    """`CaseDesk.exe` -> `casedesk`."""
    stem = re.sub(r"\.exe$", "", process or "app", flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "app"


def _steps(rec: Path) -> list[dict]:
    f = rec / "steps.jsonl"
    if not f.is_file() or not (rec / "desktop.json").is_file():
        raise ProposalError(f"{rec.name}: not a desktop recording (desktop.json and steps.jsonl)")
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _owners(steps: list[dict]) -> list[str | None]:
    """The process of each step; a step without one (a window that closed) keeps the one before."""
    out, last = [], None
    for s in steps:
        p = s.get("process") or last
        out.append(p)
        last = p
    first = next((p for p in out if p), None)
    return [p or first for p in out]


def propose(recording: Path) -> Proposal:
    rec = Path(recording)
    steps = _steps(rec)
    owners = _owners(steps)
    if not any(owners):
        raise ProposalError(f"{rec.name}: no step names its process; nothing to group")
    order: list[str] = list(dict.fromkeys(p for p in owners if p))
    taken, result = set(), []
    for proc in order:
        mine = [s for s, p in zip(steps, owners, strict=True) if p == proc]
        base = suggest_id(proc)
        app_id, n = base, 2
        while app_id in taken:
            app_id, n = f"{base}-{n}", n + 1
        taken.add(app_id)
        titles = list(dict.fromkeys((s.get("window") or {}).get("title") for s in mine
                                    if (s.get("window") or {}).get("title")))  # fmt: skip
        result.append(Entry(app_id, proc, True, len(mine), titles[:20]))
    return Proposal(rec.name, result)


def save(proposal: Proposal, path: Path) -> Path:
    path = Path(path)
    path.write_text(
        "# Edit this file, then run `labctl desktop split <recording> --proposal <file> --apply`.\n"
        "# Rename an id; give two entries the same id to merge them; set include: false to drop one.\n"
        + yaml.safe_dump(proposal.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def from_dict(data: object, name: str = "proposal") -> Proposal:
    if not isinstance(data, dict) or not isinstance(data.get("recording"), str):
        raise ProposalError(f"{name}: expected a mapping with 'recording' and 'apps'")
    apps = data.get("apps")
    if not isinstance(apps, list) or not apps:
        raise ProposalError(f"{name}: 'apps' must be a non-empty list")
    entries = []
    for n, raw in enumerate(apps, 1):
        if not isinstance(raw, dict):
            raise ProposalError(f"{name}: apps[{n}] must be a mapping")
        unknown = set(raw) - KNOWN_KEYS
        if unknown:
            raise ProposalError(f"{name}: apps[{n}]: unknown field(s) {', '.join(sorted(unknown))}")
        app_id, proc = raw.get("id"), raw.get("process")
        if not isinstance(app_id, str) or not SLUG.match(app_id):
            raise ProposalError(
                f"{name}: apps[{n}]: id {app_id!r} must be lower-case letters, digits and dashes"
            )
        if not isinstance(proc, str) or not proc:
            raise ProposalError(f"{name}: app '{app_id}': process must be the executable name")
        include = raw.get("include", True)
        if not isinstance(include, bool):
            raise ProposalError(f"{name}: app '{app_id}': include must be true or false")
        entries.append(Entry(app_id, proc, include, int(raw.get("steps") or 0),
                             list(raw.get("windows") or [])))  # fmt: skip
    procs = [e.process.lower() for e in entries]
    dup = {p for p in procs if procs.count(p) > 1}
    if dup:
        raise ProposalError(f"{name}: process {', '.join(sorted(dup))} appears twice; "
                            "to merge two applications give them the same id")  # fmt: skip
    flags: dict[str, set[bool]] = {}
    for e in entries:
        flags.setdefault(e.id, set()).add(e.include)
    if any(len(f) > 1 for f in flags.values()):
        raise ProposalError(f"{name}: entries with the same id disagree on include")
    if not any(e.include for e in entries):
        raise ProposalError(
            f"{name}: every entry is dropped (include: false); nothing would be written"
        )
    return Proposal(data["recording"], entries)


def load(path: Path) -> Proposal:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProposalError(f"{path.name}: cannot read the proposal: {exc}") from exc
    return from_dict(data, path.name)


def apply(
    recording: Path, proposal: Proposal, root: Path, *, overwrite: bool = False
) -> list[Path]:
    rec, root = Path(recording), Path(root)
    proposal = from_dict(proposal.to_dict())
    steps = _steps(rec)
    owners = _owners(steps)
    session = json.loads((rec / "desktop.json").read_text(encoding="utf-8"))
    seen = {p.lower() for p in owners if p}
    by_proc = {e.process.lower(): e for e in proposal.apps}
    for p in seen - set(by_proc):
        raise ProposalError(f"process {p} appears in {rec.name} but not in the proposal; add an "
                            "entry for it or set include: false")  # fmt: skip
    for e in proposal.apps:
        if e.process.lower() not in seen:
            raise ProposalError(f"app '{e.id}': process {e.process} does not appear in {rec.name}")
    ids = list(dict.fromkeys(e.id for e in proposal.apps if e.include))
    outs = {i: root / "evidence" / i / rec.name for i in ids}
    for out in outs.values():
        if out.exists() and not overwrite:
            raise ProposalError(f"{out} already exists; remove it or pass overwrite")
        if out.resolve() == rec.resolve():
            raise ProposalError(f"{out} is the recording being split")
    for i, out in outs.items():
        if out.exists():
            shutil.rmtree(out)
        (out / "trees").mkdir(parents=True)
        procs = {e.process.lower() for e in proposal.apps if e.id == i}
        mine = [s for s, p in zip(steps, owners, strict=True) if (p or "").lower() in procs]
        lines = []
        for local, s in enumerate(mine, 1):
            step = dict(s)
            step["global_seq"], step["seq"] = s.get("seq"), local
            for key, folder, ext in (("tree", "trees", ".json"), ("screenshot", "screens", ".png")):
                old = step.get(key)
                if old and (rec / old).is_file():
                    new = f"{folder}/{local:04d}{ext}"
                    (out / folder).mkdir(exist_ok=True)
                    shutil.copyfile(rec / old, out / new)
                    step[key] = new
                elif old:
                    step[key] = None
            lines.append(json.dumps(step, ensure_ascii=False))
        (out / "steps.jsonl").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )
        part = dict(session)
        part["processes"] = [p for p in session.get("processes") or []
                             if (p.get("process") or "").lower() in procs]  # fmt: skip
        part["steps"] = len(mine)
        part["split_from"] = rec.name
        (out / "desktop.json").write_text(
            json.dumps(part, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    _sequence(rec, root, steps, owners, proposal)
    return [outs[i] for i in ids]


def _do(step: dict) -> str:
    action = step.get("action") or ""
    if action in ("window_open", "window_close", "focus", "menu_open"):
        return action.replace("_", " ")
    extra = step.get("menu_path") or step.get("key") or step.get("item") or ""
    what = (step.get("target") or {}).get("name") or step.get("selector") or ""
    return " ".join(x for x in (action, what, extra and f"({extra})") if x)


def _sequence(rec: Path, root: Path, steps: list[dict], owners: list, proposal: Proposal) -> Path:
    by_proc = {e.process.lower(): e for e in proposal.apps}
    rows = []
    for s, p in zip(steps, owners, strict=True):
        e = by_proc.get((p or "").lower())
        row = {"n": s.get("seq"), "app": e.id if e else None, "surface": SURFACE, "ts": s.get("ts"),
               "screen": (s.get("window") or {}).get("title"), "path": None, "do": _do(s)}  # fmt: skip
        if e is not None and not e.include:
            row["excluded"] = True
        rows.append(row)
    data = {"id": re.sub(r"[^a-z0-9-]+", "-", rec.name.lower()).strip("-") or "recording",
            "kind": "cross-app-sequence", "recording": rec.name,
            "requested_by": "labctl desktop split",
            "purpose": f"Order of the steps of desktop recording {rec.name} across its applications",
            "apps": list(dict.fromkeys(e.id for e in proposal.apps)), "steps": rows}  # fmt: skip
    folder = root / "evidence" / SEQUENCES
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rec.name}.flow.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _ts(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def interleave(*recordings: Path) -> list[dict]:
    """Web and desktop recordings of one session on one timeline, ordered by timestamp. Each row:
    `ts`, `surface` (web or desktop), `recording`, `seq`, `action`, `selector`, and `url` (web)
    or `window` (desktop)."""
    rows = []
    for rec in recordings:
        rec = Path(rec)
        surface = SURFACE if (rec / "desktop.json").is_file() else "web"
        for line in (rec / "steps.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            s = json.loads(line)
            row = {"ts": s.get("ts"), "surface": s.get("surface") or surface, "recording": rec.name,
                   "seq": s.get("global_seq") or s.get("seq"), "action": s.get("action"),
                   "selector": s.get("selector")}  # fmt: skip
            if row["surface"] == SURFACE:
                row["window"] = (s.get("window") or {}).get("title")
            else:
                row["url"] = s.get("url")
            rows.append(row)
    far = datetime.max.replace(tzinfo=None)
    return sorted(rows, key=lambda r: ((_ts(r["ts"]) or far).replace(tzinfo=None), r["seq"] or 0))
