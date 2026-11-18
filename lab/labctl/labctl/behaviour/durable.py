"""Durable fidelity results (replication gap R11): what a replay compared, and whether that
still holds.

A replay's report carries a `fingerprint`:

- `recording`: its id, folder, source origin, capture time and a digest of `steps.jsonl` and every
  snapshot under `dom/`;
- `files`: digests of the bindings, acceptance, baseline and visual files used (the visual digest
  covers the baseline images it names), or None when there was none;
- `browser` (name, version) and `viewport`;
- `app` (added when the report is kept for an app): conformance's app facts (description, brief,
  schema, build time, database choice), the image id when Docker knows it, and the commit.

`assess` compares a kept report with what exists now, like `conformance.assess`: any change makes
it `stale` ("out of date: <reason> — replay again"), and readiness does not count it. A report
kept before this was tracked has no fingerprint: `legacy`, "checked before this was tracked".
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

VERSION = 1
FILES = ("bindings", "acceptance", "baseline", "visual")


def recording_digest(path: Path) -> str | None:
    """steps.jsonl and every snapshot (dom/*), in name order."""
    path = Path(path)
    steps = path / "steps.jsonl"
    if not steps.exists():
        return None
    h = hashlib.sha256(steps.read_bytes())
    dom = path / "dom"
    if dom.is_dir():
        for f in sorted(dom.rglob("*")):
            if f.is_file():
                h.update(f.relative_to(path).as_posix().encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:12]


def file_digest(path: Path | None) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12] if path else None
    except OSError:
        return None


def file_digests(rec_path: Path, used: dict[str, Path | None]) -> dict[str, dict]:
    from labctl.behaviour import visual as vis

    out = {}
    for name in FILES:
        p = used.get(name)
        if name == "visual":
            spec = vis.load(p) if p is not None and Path(p).exists() else None
            d = vis.digest(spec, rec_path, vis.pictures_of(rec_path)) if spec else None
        else:
            d = file_digest(p) if p is not None and Path(p).exists() else None
        out[name] = {"path": str(Path(p).resolve()) if p is not None else None, "digest": d}
    return out


def for_run(rec, used: dict[str, Path | None], browser: dict | None, viewport: dict | None) -> dict:
    """The fingerprint of a replay, as it ran (the app part is added when it is kept)."""
    from labctl.behaviour.baseline import origin_of

    source = next((o for o in (origin_of(s.url) for s in rec.steps) if o), None)
    captured = next((s.ts for s in rec.steps if s.ts), None)
    return {
        "version": VERSION,
        "replayed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "recording": {
            "id": rec.id,
            "path": str(Path(rec.path).resolve()),
            "digest": recording_digest(rec.path),
            "source_origin": source,
            "captured_at": captured.isoformat(timespec="seconds") if captured else None,
            "steps": len(rec.steps),
        },
        "files": file_digests(rec.path, used),
        "browser": browser,
        "viewport": viewport,
    }


def commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def add_app(fp: dict, root: Path, app_id: str, image: str | None = None) -> dict:
    """The replica the replay ran against: conformance's app facts, image, commit."""
    from labctl import conformance

    fp = dict(fp)
    fp["app"] = {**conformance.app_facts(root, app_id), "id": app_id, "image": image,
                 "commit": commit(root)}  # fmt: skip
    return fp


def assess(root: Path, app_id: str, report, facts=None, image: str | None = None):
    """Is a kept replay report still true of what exists now? A `conformance.Freshness` whose
    verdict is the report's scoped status (or its status, for older reports)."""
    from labctl import conformance

    facts = facts or conformance.app_facts
    verdict = getattr(report, "verdict", None) or str(getattr(report, "status", "")).upper()
    fp = getattr(report, "fingerprint", None)
    if not isinstance(fp, dict):
        return conformance.Freshness("legacy", verdict)
    at = fp.get("replayed_at")
    reasons: list[str] = []
    rec = fp.get("recording") or {}
    path = Path(rec.get("path") or "")
    if not rec.get("path") or not (path / "steps.jsonl").exists():
        reasons.append("the recording is no longer there")
    elif recording_digest(path) != rec.get("digest"):
        reasons.append("the recording changed")
    else:
        then = fp.get("files") or {}
        for name in FILES:
            saved = then.get(name) or {}
            p = Path(saved["path"]) if saved.get("path") else None
            now = file_digests(path, {name: p})[name]["digest"] if p else None
            if saved.get("digest") != now:
                if saved.get("digest") is None:
                    reasons.append(f"a {name} file was added")
                elif now is None:
                    reasons.append(f"the {name} file was removed")
                else:
                    reasons.append(f"the {name} file changed")
        # a file of that kind now beside the recording, where the run had none
        defaults = {"bindings": "bindings.yaml", "acceptance": "acceptance.yaml",
                    "baseline": "baseline.yaml", "visual": "visual.yaml"}  # fmt: skip
        for name, fname in defaults.items():
            saved = then.get(name) or {}
            if saved.get("path") is None and (path / fname).exists():
                reasons.append(f"a {name} file was added")
    app = fp.get("app")
    if isinstance(app, dict):
        now = facts(root, app.get("id") or app_id)
        described = [label for key, label in (("app_yaml", "description"), ("brief", "brief"),
                                              ("schema", "schema"))
                     if app.get(key) != now.get(key)]  # fmt: skip
        if described:
            reasons.append(f"the app's {' and '.join(described)} changed")
        newer = (now.get("changed_at") or 0) > (
            app.get("changed_at") or 0
        ) + conformance.TOLERANCE_S
        if (image and app.get("image") and image != app["image"]) or (newer and not described):
            reasons.append("the app was rebuilt")
        if app.get("database") != now.get("database"):
            reasons.append("the database choice changed")
    if reasons:
        return conformance.Freshness("stale", verdict, at, reasons)
    return conformance.Freshness("current", verdict, at)


def describe(fp: dict | None) -> str:
    """One line: what was compared."""
    if not isinstance(fp, dict):
        return "Compared: not recorded (a report from before this was tracked)"
    rec = fp.get("recording") or {}
    files = fp.get("files") or {}
    bits = [f"recording {rec.get('id')} {rec.get('digest')} (captured {rec.get('captured_at')}, "
            f"source {rec.get('source_origin')})"]  # fmt: skip
    bits += [f"{n} {(files.get(n) or {}).get('digest') or 'none'}" for n in FILES]
    b = fp.get("browser") or {}
    if b:
        bits.append(f"{b.get('name')} {b.get('version')}")
    v = fp.get("viewport") or {}
    if v:
        bits.append(f"viewport {v.get('width')}x{v.get('height')}")
    app = fp.get("app") or {}
    if app:
        bits.append(f"app {app.get('id')} commit {app.get('commit') or '?'} image "
                    f"{app.get('image') or 'unknown'} database {app.get('database')}")  # fmt: skip
    return "Compared: " + "; ".join(bits)
