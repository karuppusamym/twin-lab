"""From one recording across several applications to a build package for each of them.

`build_all(root, recording)` is `labctl evidence build-all`. Each stage can be run again and does
the same thing again; one that failed for one application does not stop the others:

1. **Propose** the split (labctl.split) and write it beside the recording
   (`<recording>.split.yaml`). Without `yes` and without a reviewed proposal it stops here, so a
   person can check which applications were found before anything is written.
2. **Split**: one recording per application under `evidence/<app>/<recording>/` and the sequence
   across them in `evidence/_sequences/<recording>.flow.yaml`. Skipped when the parts already
   come from the same recording and proposal; bindings a person filled in a part are kept.
3. **Draft a requested flow** `flows/<app>-<recording>.yaml` from that application's steps: its
   screens in the order visited, what the person did on each in plain words (actions and element
   labels, never a value) and the fields touched. It is a draft: `requested_by` says so, and a
   person reviews it. Not written when an automation or a flow a person wrote already says what
   the app needs, or when the draft exists (a person may have edited it).
4. **Brief** each application (labctl.factory: every piece of evidence the app has is merged, so
   an app built before is enhanced, not replaced).
5. **Package** each application for a coding agent (labctl.builder.package).
6. **Summary**: printed and written beside the recording (`<recording>.build-all.json`): the
   applications, per app its evidence, brief, package, detected technology, where its data
   lives, and what still needs a person.

`cross_app_section(root, app)` is the brief's "Part of a flow across applications" section, read
from the sequence files of the recordings the app's evidence came from.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from labctl import split as sp

RECORDINGS = "_recordings"  # evidence/_recordings/<name>/: recordings across applications
MARK = "split.json"  # in each part: which recording and proposal it was split from
DRAFT_BY = "labctl evidence build-all (draft: review it before building)"
SECTION = "Part of a flow across applications"


class BuildAllError(Exception):
    pass


def recordings_dir(root: Path) -> Path:
    return Path(root) / "evidence" / RECORDINGS


def proposal_file(recording: Path) -> Path:
    rec = Path(recording)
    return rec.parent / f"{rec.name}.split.yaml"


def summary_file(recording: Path) -> Path:
    rec = Path(recording)
    return rec.parent / f"{rec.name}.build-all.json"


@dataclass
class AppResult:
    id: str
    origin: str
    kind: str = "app"
    tabs: list[str] = field(default_factory=list)
    steps: int = 0
    evidence: str = ""
    flow: str | None = None
    flow_note: str = ""
    brief: str | None = None
    package: str | None = None
    technology: str = ""
    data: str = ""
    screens: int = 0
    routes: int = 0
    tables: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)  # what a person still has to do
    errors: list[str] = field(default_factory=list)


@dataclass
class Summary:
    recording: str
    proposal: str
    stopped: bool = False
    message: str = ""
    sequence: str | None = None
    apps: list[AppResult] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)
    handovers: int = 0
    travels: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        out = [f"recording {self.recording}: proposal {self.proposal}"]
        if self.stopped:
            return "\n".join([*out, self.message])
        out.append(f"{len(self.apps)} application(s); sequence {self.sequence} "
                   f"({self.handovers} hand-over(s), {self.travels} value(s) that may travel)")  # fmt: skip
        for name in self.left_out:
            out.append(f"  left out: {name}")
        for a in self.apps:
            tabs = f", tabs {', '.join(a.tabs)}" if a.tabs else ""
            out.append(f"\n{a.id}  ({a.origin}{tabs}, {a.steps} step(s))")
            out.append(f"  evidence   {a.evidence}")
            out.append(
                f"  flow       {a.flow or '-'}{'  (' + a.flow_note + ')' if a.flow_note else ''}"
            )
            out.append(f"  brief      {a.brief or '-'}: {a.screens} screen(s), {a.routes} route(s), "
                       f"{len(a.tables)} table(s){': ' + ', '.join(a.tables) if a.tables else ''}")  # fmt: skip
            out.append(f"  package    {a.package or '-'}")
            out.append(f"  technology {a.technology or 'not identified'}")
            out.append(f"  data       {a.data or 'not assessed'}")
            for need in a.needs:
                out.append(f"  TO DO      {need}")
            for err in a.errors:
                out.append(f"  FAILED     {err}")
        return "\n".join(out)


def _rel(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


# --- 1. the proposal --------------------------------------------------------------------------------


def _proposal(rec: Path, given: Path | None, yes: bool) -> tuple[sp.Proposal, Path, bool]:
    """(proposal, its file, whether a person has accepted it)."""
    if given is not None:
        return sp.load(given), Path(given), True
    path = proposal_file(rec)
    if path.is_file():  # written by an earlier run, perhaps edited since: never overwritten
        return sp.load(path), path, yes
    prop = sp.propose(rec)
    sp.save(prop, path)
    return prop, path, yes


def proposal_table(prop: sp.Proposal) -> str:
    lines = [f"{'id':<24} {'origin':<34} {'kind':<9} {'tabs':<8} steps screens"]
    for e in prop.apps:
        drop = "" if e.include else "  (left out)"
        prefix = f" {e.path_prefix}" if e.path_prefix else ""
        lines.append(f"{e.id:<24} {e.origin + prefix:<34} {e.kind:<9} {','.join(e.tabs) or '-':<8} "
                     f"{e.steps:>5} {e.screens:>7}{drop}")  # fmt: skip
        for o in e.embedded:
            lines.append(f"{'':<24} {o:<34} embedded in {e.id}")
    return "\n".join(lines)


# --- 2. the split -----------------------------------------------------------------------------------


def _digest(rec: Path, prop: sp.Proposal) -> str:
    h = hashlib.sha256()
    data = prop.to_dict()
    for app in data["apps"]:  # what a person cannot change in effect does not count
        app.pop("steps", None)
        app.pop("screens", None)
    h.update(json.dumps(data, sort_keys=True).encode())
    for name in ("steps.jsonl", sp.BINDINGS):
        if (rec / name).is_file():
            h.update((rec / name).read_bytes())
    return "sha256:" + h.hexdigest()[:16]


def _split(root: Path, rec: Path, prop: sp.Proposal, say: Callable[[str], None]) -> list[Path]:
    from labctl.behaviour import bindings as bnd
    from labctl.botlog.recording import load as load_rec

    digest = _digest(rec, prop)
    ids = list(dict.fromkeys(e.id for e in prop.apps if e.include))
    parts = [root / "evidence" / i / rec.name for i in ids]
    if all(_marked(p) == digest for p in parts) and sp.sequence_path(rec, root).is_file():
        say("split: already done for this recording and proposal")
        return parts
    kept: dict[str, tuple[bnd.Bindings, dict]] = {}
    for part in parts:  # values a person gave in a part survive splitting again
        if (part / bnd.FILE).is_file():
            try:
                kept[part.parent.name] = (bnd.load(part / bnd.FILE), _fields(part / bnd.FILE))
            except ValueError:
                pass
    written = sp.apply(rec, prop, root, overwrite=True)
    for part in written:
        rec_part = load_rec(part)
        have = bnd.load(part / bnd.FILE) if (part / bnd.FILE).is_file() else bnd.Bindings()
        old, fields = kept.get(part.parent.name, (bnd.Bindings(), {}))
        by_seq = {s.seq: s for s in rec_part.steps}
        for seq, value in old.values.items():  # only where the step is still the same field
            if seq in by_seq and fields.get(seq) == by_seq[seq].selector:
                have.values.setdefault(seq, value)
        for seq, title in old.records.items():
            if seq in by_seq and fields.get(seq) == by_seq[seq].selector:
                have.records.setdefault(seq, title)
        (part / bnd.FILE).write_text(bnd.template(rec_part, have), encoding="utf-8")
        (part / MARK).write_text(json.dumps({"recording": rec.name, "digest": digest}),
                                 encoding="utf-8")  # fmt: skip
        say(f"split: wrote {_rel(root, part)}")
    return written


def _marked(part: Path) -> str | None:
    try:
        return json.loads((part / MARK).read_text(encoding="utf-8")).get("digest")
    except (OSError, ValueError, AttributeError):
        return None


def _fields(path: Path) -> dict[int, str | None]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out = {}
    for key, entry in (data.get("steps") or {}).items():
        try:
            out[int(key)] = entry.get("field") if isinstance(entry, dict) else None
        except (TypeError, ValueError):
            continue
    return out


def open_bindings(part: Path) -> int:
    """How many steps of a part's bindings file still have no value (or record) given."""
    from labctl.behaviour import bindings as bnd
    from labctl.botlog.recording import load as load_rec

    rec = load_rec(part)
    have = bnd.load(part / bnd.FILE) if (part / bnd.FILE).is_file() else bnd.Bindings()
    missing = 0
    for need in bnd.needs(rec):
        given = have.values if need.kind == "value" else have.records
        missing += need.seq not in given
    return missing


# --- 3. the draft requested flow ---------------------------------------------------------------------

VERBS = {"click": "clicks", "dblclick": "double-clicks", "type": "types into",
         "select": "chooses in", "check": "ticks", "uncheck": "unticks"}  # fmt: skip
FIELD_ACTIONS = ("type", "select", "check", "uncheck")


def _label(step) -> str:
    """What a person would call the element: its accessible name, label, placeholder, text or
    test id from the recorded locators (structure only: candidates holding typed text were
    already dropped by the recorder); else its selector."""
    for kind, key in (("label", "text"), ("role", "name"), ("placeholder", "text"),
                      ("text", "text"), ("testid", "value"), ("id", "value")):  # fmt: skip
        for cand in step.locators:
            if cand.get("kind") == kind and isinstance(cand.get(key), str) and cand[key].strip():
                return cand[key].strip()[:60]
    return (step.selector or "the page")[:60]


def _describe(step) -> str | None:
    action = step.action
    if action in VERBS:
        return f"{VERBS[action]} {_label(step)}"
    if action == "keypress":
        return f"presses {'+'.join([*step.modifiers, step.key or '?'])} in {_label(step)}"
    if action == "submit" and step.caused_by is None:
        return "submits the form"
    return None


def draft_flow(app_id: str, part: Path, recording: str) -> dict:
    """The requested flow a person would write from what the recording shows (as a mapping in
    the flow contract's shape)."""
    from labctl import sanitize
    from labctl.botlog.recording import load as load_rec
    from labctl.model_build import screen_name

    rec = load_rec(part)
    screens: dict[str, dict] = {}
    for step in rec.steps:
        if not step.url or not step.url.startswith(("http:", "https:")):
            continue
        pattern = sanitize.route_pattern(urlsplit(step.url).path or "/")
        entry = screens.setdefault(pattern, {"do": [], "fields": []})
        words = _describe(step)
        if words and words not in entry["do"]:
            entry["do"].append(words)
        if step.action in FIELD_ACTIONS:
            name = _label(step)
            if name not in entry["fields"]:
                entry["fields"].append(name)
    steps = []
    for pattern, entry in screens.items():
        do = "; ".join(entry["do"][:14]) or "opens the page"
        steps.append({"screen": screen_name(pattern), "path": pattern, "do": do,
                      "fields": entry["fields"][:20]})  # fmt: skip
    slug = re.sub(r"[^a-z0-9-]+", "-", f"{app_id}-{recording}".lower()).strip("-")[:80]
    return {
        "id": slug,
        "app": app_id,
        "requested_by": DRAFT_BY,
        "purpose": (
            f"Draft from recording {recording}: the screens of {app_id} the person went "
            "through, in order. Remove what the demo app does not need, then rebuild the brief."
        ),  # fmt: skip
        "steps": steps,
        "evidence": [f"evidence/{app_id}/{recording}"],
    }


def write_draft_flow(
    root: Path, app_id: str, part: Path, recording: str
) -> tuple[Path | None, str]:
    """(the flow file, a note). Nothing is written when the app's scope is already given."""
    from labctl import factory
    from labctl.appmodel import Flow

    if factory.automations(root, app_id):
        return None, "scope from its registered automation(s); no draft flow written"
    own = [f for f in factory.flows(root, app_id) if not f.requested_by.startswith("labctl ")]
    if own:
        return None, f"scope from flow {own[0].id}, written by a person; no draft flow written"
    data = draft_flow(app_id, part, recording)
    path = root / "flows" / f"{data['id']}.yaml"
    if path.exists():
        return path, "draft kept as it is (it may have been edited)"
    Flow.model_validate(data)  # the contract the brief reads
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ("# DRAFT requested flow, written by `labctl evidence build-all` from the recording.\n"
            "# Review it: remove screens and fields the demo app does not need, then rebuild the\n"
            "# brief. Structure only: no value typed in the recording is in this file.\n")  # fmt: skip
    path.write_bytes((head + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
                     .replace("\r\n", "\n").encode("utf-8"))  # fmt: skip
    return path, "draft: review it"


# --- 4-5. brief and package ---------------------------------------------------------------------------


def _technology(model) -> str:
    stack = model.stack
    parts = []
    front = stack.frontend or {}
    if front.get("framework"):
        parts.append(" ".join(str(front[k]) for k in ("framework", "version") if front.get(k)))
    server = stack.server or {}
    if server.get("technology"):
        what = server["technology"] + (
            f" ({server['framework']})" if server.get("framework") else ""
        )
        parts.append(f"{what}, {server.get('confidence', 'guess')}")
    web = server.get("web_server") or {}
    if web.get("name"):
        parts.append(f"web server {web['name']}")
    return "; ".join(parts)


def _data_text(brief) -> str:
    data = brief.data or {}
    kind = data.get("kind")
    if not kind:
        return ""
    ev = (data.get("evidence") or [""])[0]
    if kind == "server":
        engine = brief.db_engine + ("" if brief.db_engine_set or brief.found_engine else " assumed")
        return f"server database ({engine}): {ev}"
    return f"{kind}: {ev}"


# --- the whole chain ----------------------------------------------------------------------------------


def build_all(
    root: Path,
    recording: Path,
    *,
    proposal: Path | None = None,
    yes: bool = False,
    db_engine: str | None = None,
    package_dir: Path | None = None,
    say: Callable[[str], None] = print,
) -> Summary:
    from labctl import factory
    from labctl.builder import package as pkg

    root, rec = Path(root), Path(recording)
    if not rec.is_dir():
        raise BuildAllError(f"{rec} is not a recording folder")
    prop, prop_path, accepted = _proposal(rec, proposal, yes)
    summary = Summary(rec.name, _rel(root, prop_path) or str(prop_path))
    if not accepted:
        summary.stopped = True
        summary.message = (
            "Applications found in the recording:\n" + proposal_table(prop) + "\n\n"
            f"Check {prop_path}: rename an id, give two entries the same id to merge them, add an "
            "entry with a path_prefix to split a site, set include: false to leave one out, or "
            "add an entry for an embedded site to make it its own application. Then run again "
            f"with --proposal {prop_path} (or --yes to accept it as it is)."
        )
        say(summary.message)
        return summary
    say("Applications:\n" + proposal_table(prop))
    parts = _split(root, rec, prop, say)
    seq_path = sp.sequence_path(rec, root)
    summary.sequence = _rel(root, seq_path)
    seq = yaml.safe_load(seq_path.read_text(encoding="utf-8")) if seq_path.is_file() else {}
    summary.handovers = len(seq.get("handovers") or [])
    summary.travels = len(seq.get("travels") or [])
    summary.left_out = [f"{e.id} ({e.origin}, {e.kind})" for e in prop.apps if not e.include]
    entries = {e.id: e for e in reversed(prop.apps)}
    out_dir = Path(package_dir) if package_dir else pkg.default_state_dir(root) / "packages"
    for part in parts:
        app_id = part.parent.name
        entry = entries[app_id]
        result = AppResult(app_id, entry.origin, entry.kind, list(entry.tabs),
                           evidence=_rel(root, part) or str(part))  # fmt: skip
        summary.apps.append(result)
        result.steps = sum(1 for line in (part / "steps.jsonl").read_text("utf-8").splitlines()
                           if line.strip())  # fmt: skip
        try:
            flow_path, result.flow_note = write_draft_flow(root, app_id, part, rec.name)
            result.flow = _rel(root, flow_path)
            if flow_path is not None and result.flow_note.startswith("draft"):
                result.needs.append(f"review the draft flow {result.flow}")
        except Exception as exc:  # noqa: BLE001 - reported per app, the others go on
            result.errors.append(f"draft flow: {exc}")
        missing = open_bindings(part)
        if missing:
            result.needs.append(f"fill {missing} value(s) in {_rel(root, part / 'bindings.yaml')} "
                                "(synthetic) to replay the recording")  # fmt: skip
        try:
            done = factory.generate(root, app_id, None, None, db_engine, force=True)
            b = done.brief
            result.brief = _rel(root, root / "apps" / app_id / "BRIEF.md")
            result.screens, result.routes = len(b.screens), len(b.routes)
            result.tables = list(b.tables)
            result.technology = _technology(b.model)
            result.data = _data_text(b)
            say(f"brief: {app_id}: {result.screens} screen(s), {result.routes} route(s), "
                f"{len(result.tables)} table(s)")  # fmt: skip
            if any("database engine" in g for g in b.gaps) and not b.browser_only:
                result.needs.append(f"choose the database (labctl db use {app_id} <name>, or "
                                    "labctl brief --db-engine)")  # fmt: skip
            if b.data_kind == "unknown":
                result.needs.append("say where the data lives (labctl brief --data browser|server)")
            other = [g for g in b.gaps if "database engine" not in g]
            if other:
                result.needs.append(f"read the {len(other)} open question(s) in the brief")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"brief: {exc}")
            continue
        try:
            result.package = _rel(root, pkg.build(root, app_id, out_dir))
            say(f"package: {result.package}")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"package: {exc}")
    target = summary_file(rec)
    target.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
    say("\n" + summary.text())
    say(f"\nsummary: {_rel(root, target)}")
    return summary


# --- the cross-app section of a brief -----------------------------------------------------------------


def sequences_of(root: Path, app_id: str) -> list[tuple[Path, dict]]:
    """The sequence files of the recordings this app's evidence came from."""
    folder = Path(root) / "evidence" / sp.SEQUENCES
    out = []
    for path in sorted(folder.glob("*.flow.yaml")) if folder.is_dir() else []:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if app_id not in (data.get("apps") or []):
            continue
        if not (Path(root) / "evidence" / app_id / str(data.get("recording"))).is_dir():
            continue  # the part was removed: no longer this app's evidence
        mine = [
            r for r in data.get("steps") or [] if r.get("app") == app_id and not r.get("excluded")
        ]
        if mine:
            out.append((path, data))
    return out


def _runs(data: dict) -> list[tuple[str, int, int]]:
    """(app, first step, last step) for each stretch of the flow in one application."""
    out: list[list] = []
    for row in data.get("steps") or []:
        if row.get("excluded") or str(row.get("do", "")).startswith(("open tab", "switch to tab",
                                                                      "close tab")):  # fmt: skip
            continue
        if out and out[-1][0] == row["app"]:
            out[-1][2] = row["n"]
        else:
            out.append([row["app"], row["n"], row["n"]])
    return [tuple(r) for r in out]


def cross_app_section(root: Path, app_id: str) -> str:
    """Markdown for the brief ("" when the app's evidence comes from no recording across
    applications)."""
    found = sequences_of(root, app_id)
    if not found:
        return ""
    out = [f"## {SECTION}\n"]
    for path, data in found:
        runs = _runs(data)
        others = [a for a in dict.fromkeys(r[0] for r in runs) if a != app_id]
        if not others:
            continue
        chain = " → ".join(
            (f"**{a}**" if a == app_id else f"`{a}`")
            + (f" (steps {s} to {e})" if s != e else f" (step {s})")
            for a, s, e in runs
        )
        rel = path.relative_to(Path(root)).as_posix()
        out.append(f"Recording `{data.get('recording')}` went through {len(runs)} stretch(es) in "
                   f"{len(others) + 1} applications (`{rel}`): {chain}. Build only this "
                   "application; each of the others has its own brief. Keep the elements where the "
                   "flow leaves or enters this application with the same ids, names and link "
                   "targets, so the hand-over works the same way; a link to another application "
                   "goes to that application's address in the lab.\n")  # fmt: skip
        before, after = [], []
        for n, (a, _s, _e) in enumerate(runs):
            if a != app_id:
                continue
            if n > 0:
                before.append(f"`{runs[n - 1][0]}` (step {runs[n - 1][2]})")
            if n + 1 < len(runs):
                after.append(f"`{runs[n + 1][0]}` (step {runs[n + 1][1]})")
        out.append(
            f"- Comes after: {', '.join(dict.fromkeys(before)) or 'nothing: the flow starts here'}."
        )
        out.append(
            f"- Comes before: {', '.join(dict.fromkeys(after)) or 'nothing: the flow ends here'}."
        )
        hand = [h for h in data.get("handovers") or []
                if app_id in (h["from"]["app"], h["to"]["app"])]  # fmt: skip
        if hand:
            out.append("- Hand-overs:")
            for h in hand:
                f, t = h["from"], h["to"]
                out.append(f"  - step {f['step']} → {t['step']}: from `{f['app']}` screen "
                           f"`{f['screen']}` (`{f.get('do', '')}`) to `{t['app']}` screen "
                           f"`{t['screen']}`, {h.get('how', '')}.")  # fmt: skip
        trav = [t for t in data.get("travels") or []
                if app_id in (t["from"]["app"], t["to"]["app"])]  # fmt: skip
        if trav:
            out.append("- Values that appear to travel between them (**a guess** from the shape of "
                       "what was typed and shown, letters A and digits 9; never the values):")  # fmt: skip
            for t in trav:
                f, to = t["from"], t["to"]
                where = (f"typed into `{f.get('field')}` on `{f['app']}` screen `{f['screen']}`"
                         if t.get("how") == "typed" else f"shown on `{f['app']}` screen `{f['screen']}`")  # fmt: skip
                out.append(f"  - shape `{t['shape']}`: {where} (step {f['step']}), then typed into "
                           f"`{to.get('field')}` on `{to['app']}` screen `{to['screen']}` "
                           f"(step {to['step']}).")  # fmt: skip
            out.append("  The demo apps hold synthetic data: a value that travels must exist in "
                       "both (one data pack across apps: `labctl data load-across`).")  # fmt: skip
        out.append("")
    return "\n".join(out) + "\n" if len(out) > 1 else ""


# --- for the portal ---------------------------------------------------------------------------------


def lineage(root: Path, app_id: str) -> list[dict]:
    """Which recordings across applications this app came from, with its sibling apps."""
    out = []
    for path, data in sequences_of(root, app_id):
        out.append({
            "recording": data.get("recording"),
            "sequence": path.relative_to(Path(root)).as_posix(),
            "siblings": [a for a in data.get("apps") or [] if a != app_id
                         and (Path(root) / "evidence" / a / str(data.get("recording"))).is_dir()],
            "source": data.get("source"),
            "handovers": len([h for h in data.get("handovers") or []
                              if app_id in (h["from"]["app"], h["to"]["app"])]),
        })  # fmt: skip
    return out


def recording_info(rec: Path) -> dict:
    """A recording's size and reach, read from its steps: for the Recordings page."""
    steps, tabs, origins = 0, set(), {}
    try:
        lines = (rec / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            s = json.loads(line)
        except ValueError:
            continue
        steps += 1
        if s.get("tab"):
            tabs.add(s["tab"])
        origin = sp.normalise_origin(str(s.get("url") or ""))
        if origin and str(s.get("action")) not in sp.TAB_ACTIONS:
            origins[origin] = origins.get(origin, 0) + 1
    done = None
    if summary_file(rec).is_file():
        try:
            done = json.loads(summary_file(rec).read_text(encoding="utf-8"))
        except ValueError:
            done = None
    return {"name": rec.name, "path": rec, "steps": steps, "tabs": sorted(tabs),
            "origins": origins, "proposal": proposal_file(rec).is_file(), "summary": done}  # fmt: skip


def recordings(root: Path) -> list[dict]:
    """Recordings across applications: those under evidence/_recordings/, and any recording a
    sequence file names as its source."""
    seen: dict[Path, None] = {}
    base = recordings_dir(root)
    for path in sorted(base.iterdir()) if base.is_dir() else []:
        if path.is_dir() and (path / "steps.jsonl").is_file():
            seen[path.resolve()] = None
    folder = Path(root) / "evidence" / sp.SEQUENCES
    for path in sorted(folder.glob("*.flow.yaml")) if folder.is_dir() else []:
        try:
            src = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("source")
        except (OSError, yaml.YAMLError):
            continue
        if src and (Path(root) / src / "steps.jsonl").is_file():
            seen[(Path(root) / src).resolve()] = None
    return [recording_info(p) for p in seen]
