"""Everything a desktop build package carries beyond the brief and the model, in one call.

`extend(root, app, model, text)` is called by `labctl brief` (labctl.factory) for a desktop
application: it writes the look (labctl.desktop.look), the navigation map
(labctl.desktop.navigation), the field rules (labctl.desktop.fieldrules), the recorded steps,
seed and replay values (labctl.desktop.seeddata) and the build documents
(labctl.desktop.docs), and adds their sections to the brief. `package_files` and
`package_check` are what the build package (labctl.builder.package) takes from here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from labctl.desktop import docs, fieldrules, look, navigation, pictures, seeddata

MARKER = "## Running in the lab"


def _insert(text: str, section: str) -> str:
    if not section:
        return text
    return text.replace(MARKER, section + MARKER, 1) if MARKER in text else f"{text}\n{section}"


def extend(root: Path, app_id: str, model, text: str) -> str:
    recs = look.recordings(root, app_id)
    if not recs:
        return text
    app = f"apps/{app_id}"
    lk = look.write(root, app_id, recs)
    nav = navigation.write(root, app_id, recs)
    rules = fieldrules.write(root, app_id, model, recs)
    seed = seeddata.write(root, app_id, model, recs)
    steps_docs = []
    for rec in recs:
        p = Path(root) / app / "steps" / f"{rec.id}.json"
        if p.is_file():
            steps_docs.append(json.loads(p.read_text(encoding="utf-8")))
    docs.write(root, app_id, model, lk, nav, rules, steps_docs)
    for section in (look.brief_section(lk, app), navigation.brief_section(nav, app),
                    fieldrules.brief_section(rules, app), seeddata.brief_section(seed, app, recs),
                    _docs_section(app)):  # fmt: skip
        text = _insert(text, section)
    return text


def _docs_section(app: str) -> str:
    return ("## Build documents\n\n"
            f"`{app}/spec/CONSTITUTION.md` (what never changes), `{app}/spec/ARCHITECTURE.md`, "
            f"`{app}/spec/BUILD-PLAN.md` (the order to build in, each step with its check) and one "
            f"document per window, `{app}/spec/WINDOWS/<window>.md`: everything about that window "
            "in one place.\n\n")  # fmt: skip


def package_files(root: Path, app_id: str) -> dict[str, Path]:
    app = Path(root) / "apps" / app_id
    out: dict[str, Path] = dict(look.package_files(root, app_id))
    for sub in (docs.DIR,):
        folder = app / sub
        for p in sorted(folder.rglob("*")) if folder.is_dir() else []:
            if p.is_file() and not p.is_symlink():
                out[f"apps/{app_id}/{p.relative_to(app).as_posix()}"] = p
    steps = app / "steps"
    for p in sorted(steps.glob("*/*.png")) if steps.is_dir() else []:
        if p.is_file() and not p.is_symlink():
            out[f"apps/{app_id}/{p.relative_to(app).as_posix()}"] = p
    for name in (fieldrules.FILE, navigation.FILE):
        if (app / name).is_file():
            out[f"apps/{app_id}/{name}"] = app / name
    return out


def package_check(root: Path, app_id: str) -> list[str]:
    """Why a picture must not travel: every PNG under steps/ must be a picture the recorder made
    without data and kept after its check (byte for byte), when the recording is here."""
    app = Path(root) / "apps" / app_id
    problems = []
    kept: set[str] = set()
    have_recordings = False
    for rec in look.recordings(root, app_id):
        have_recordings = True
        man = rec.path / pictures.MANIFEST
        if not man.is_file():
            continue
        doc = json.loads(man.read_text(encoding="utf-8"))
        for name in doc.get("kept") or []:
            f = rec.path / pictures.DIR / name
            if f.is_file():
                kept.add(hashlib.sha256(f.read_bytes()).hexdigest())
    for p in sorted((app / "steps").glob("*/*.png")) if (app / "steps").is_dir() else []:
        if have_recordings and hashlib.sha256(p.read_bytes()).hexdigest() not in kept:
            problems.append(f"{p.relative_to(app).as_posix()} is not a picture the recorder made "
                            "without data and kept after its check")  # fmt: skip
    return problems
