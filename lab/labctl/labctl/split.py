"""Split one recording that visited several applications into one recording per application.

`propose(recording)` reads `steps.jsonl`, the saved pages (`dom/`) and the HAR, and groups them by
the origin of the top-level page each step happened on. A request made by a page (a CDN, an API on
another host) belongs to the application of the page that made it. A cross-origin iframe belongs
to the page that embeds it and is listed as embedded, so a person can separate it. Single sign-on
hosts are marked kind `sign-on` and left out by default.

The proposal is a small YAML or JSON file (`save`, `load`) a person edits: rename an id, give two
entries the same id to merge them, add entries with a `path_prefix` to split one host, set
`include: false` to drop one. `apply(recording, proposal, root)` writes
`root/evidence/<app>/<recording>/` for each included application in the recording format
(`recording.har`, `steps.jsonl`, `dom/`, `screens/`, `docs/`), so `labctl.har` and
`labctl.model_build.from_recording` read each part unchanged, and one cross-app sequence file,
`root/evidence/_sequences/<recording>.flow.yaml`. The original recording is never changed.

Nothing is read from typed values: steps carry only what the recorder wrote (selector and the
shape of what was typed), and the HAR entries are copied as the recorder wrote them.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml

KINDS = ("app", "sign-on")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SIGN_ON_WORDS = re.compile(r"login|logon|signin|sign-in|sso|auth|adfs|okta|oauth|saml", re.I)
SEQUENCES = "_sequences"
DEFAULT_PORTS = {"http": 80, "https": 443}
KNOWN_KEYS = {"id", "origin", "kind", "include", "path_prefix", "embedded", "steps", "screens"}


class ProposalError(ValueError):
    """The proposal cannot be used; the message says what to change."""


# --- the proposal ------------------------------------------------------------------------------------


@dataclass
class Entry:
    """One application (or part of one) in the proposal."""

    id: str
    origin: str
    kind: str = "app"
    include: bool = True
    path_prefix: str | None = None
    embedded: list[str] = field(default_factory=list)  # informational: cross-origin iframes seen
    steps: int = 0  # informational, filled by propose()
    screens: int = 0

    def to_dict(self) -> dict:
        out: dict = {
            "id": self.id,
            "origin": self.origin,
            "kind": self.kind,
            "include": self.include,
        }
        if self.path_prefix:
            out["path_prefix"] = self.path_prefix
        if self.embedded:
            out["embedded"] = [f"embedded: {o}" for o in self.embedded]
        out["steps"] = self.steps
        out["screens"] = self.screens
        return out


@dataclass
class Proposal:
    recording: str
    apps: list[Entry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"recording": self.recording, "apps": [e.to_dict() for e in self.apps]}


def save(proposal: Proposal, path: Path) -> Path:
    """Write the proposal as YAML (or JSON for a `.json` path)."""
    path = Path(path)
    data = proposal.to_dict()
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        path.write_text(
            "# Edit this file, then run `labctl evidence split <recording> --proposal <file> --apply`.\n"
            "# Rename an id; give two entries the same id to merge them; add entries with the same\n"
            "# origin and different path_prefix to split one host; set include: false to drop one.\n"
            + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return path


def _err(msg: str) -> ProposalError:
    return ProposalError(msg)


def load(path: Path) -> Proposal:
    """Read and validate a proposal file. Raises ProposalError with a message that names the entry."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))  # JSON is valid YAML
    except (OSError, yaml.YAMLError) as exc:
        raise _err(f"{path.name}: cannot read the proposal: {exc}") from exc
    return from_dict(data, name=path.name)


def from_dict(data: object, name: str = "proposal") -> Proposal:
    if not isinstance(data, dict):
        raise _err(f"{name}: expected a mapping with 'recording' and 'apps'")
    rec = data.get("recording")
    apps = data.get("apps")
    if not isinstance(rec, str) or not rec:
        raise _err(f"{name}: 'recording' must be the recording folder's name")
    if not isinstance(apps, list) or not apps:
        raise _err(f"{name}: 'apps' must be a non-empty list")
    entries: list[Entry] = []
    for n, raw in enumerate(apps, 1):
        label = f"{name}: apps[{n}]"
        if not isinstance(raw, dict):
            raise _err(f"{label} must be a mapping")
        unknown = set(raw) - KNOWN_KEYS
        if unknown:
            raise _err(f"{label}: unknown field(s) {', '.join(sorted(unknown))}")
        app_id = raw.get("id")
        if not isinstance(app_id, str) or not SLUG.match(app_id):
            raise _err(f"{label}: id {app_id!r} must be lower-case letters, digits and dashes")
        label = f"{name}: app '{app_id}'"
        origin = raw.get("origin")
        if not isinstance(origin, str) or not _origin_ok(origin):
            raise _err(f"{label}: origin {origin!r} must look like https://host[:port]")
        kind = raw.get("kind", "app")
        if kind not in KINDS:
            raise _err(f"{label}: kind {kind!r} must be one of {', '.join(KINDS)}")
        include = raw.get("include", True)
        if not isinstance(include, bool):
            raise _err(f"{label}: include must be true or false")
        prefix = raw.get("path_prefix")
        if prefix is not None and (not isinstance(prefix, str) or not prefix.startswith("/")):
            raise _err(f"{label}: path_prefix {prefix!r} must start with '/'")
        if prefix:
            prefix = "/" + prefix.strip("/")
        emb = raw.get("embedded") or []
        if not isinstance(emb, list):
            raise _err(f"{label}: embedded must be a list")
        emb = [str(e).removeprefix("embedded:").strip() for e in emb]
        entries.append(Entry(app_id, normalise_origin(origin), kind, include, prefix or None, emb,
                             int(raw.get("steps") or 0), int(raw.get("screens") or 0)))  # fmt: skip
    seen: dict[tuple[str, str | None], str] = {}
    for e in entries:
        key = (e.origin, e.path_prefix)
        if key in seen:
            where = f" with path_prefix {e.path_prefix}" if e.path_prefix else ""
            raise _err(
                f"{name}: origin {e.origin}{where} appears twice (ids '{seen[key]}' and '{e.id}'); "
                "to merge two entries give them the same id, to split a host use different path_prefix"
            )
        seen[key] = e.id
    by_id: dict[str, set[bool]] = {}
    for e in entries:
        by_id.setdefault(e.id, set()).add(e.include)
    for app_id, flags in by_id.items():
        if len(flags) > 1:
            raise _err(f"{name}: entries with id '{app_id}' disagree on include; set it the same")
    if not any(e.include for e in entries):
        raise _err(f"{name}: every entry is dropped (include: false); nothing would be written")
    return Proposal(rec, entries)


def _origin_ok(origin: str) -> bool:
    p = urlsplit(origin)
    return (
        p.scheme in ("http", "https") and bool(p.hostname) and p.path in ("", "/") and not p.query
    )


def normalise_origin(url: str) -> str:
    """scheme://host[:port] in lower case, default ports left out; '' for anything not http(s)."""
    p = urlsplit(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    try:
        port = p.port
    except ValueError:
        return ""
    host = p.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port and port != DEFAULT_PORTS[p.scheme]:
        host += f":{port}"
    return f"{p.scheme}://{host}"


def suggest_id(origin: str) -> str:
    """`https://portal.example.com` -> `portal-example-com`; a non-default port is kept."""
    p = urlsplit(origin)
    host = (p.hostname or "app").removeprefix("www.")
    port = p.port if p.port and p.port != DEFAULT_PORTS.get(p.scheme) else None
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower() + (f"-{port}" if port else "")).strip("-")
    return slug or "app"


# --- reading a recording -----------------------------------------------------------------------------


@dataclass
class _Step:
    index: int  # position in steps.jsonl
    data: dict  # the step as the recorder wrote it
    seq: int
    ts: datetime | None
    url: str
    origin: str  # of the top page
    path: str
    frame_origin: str = ""  # of the cross-origin iframe the action happened in, when known
    embedded: list[str] = field(default_factory=list)  # cross-origin iframes in its pages
    has_password: bool = False


@dataclass
class _Entry:  # a HAR entry
    raw: dict
    ts: datetime | None
    url: str
    origin: str
    path: str
    ref_origin: str
    ref_path: str
    is_document: bool
    redirect: bool


def _time(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


IFRAME_TAG = re.compile(r"<iframe\b[^>]*>", re.I)


def _iframes(html: str, base: str) -> list[tuple[str, str]]:
    """(id, origin) of each iframe with an address."""
    out = []
    for tag in IFRAME_TAG.findall(html):
        src = re.search(r'\bsrc="([^"]*)"', tag)
        if not src:
            continue
        ident = re.search(r'\bid="([^"]*)"', tag)
        origin = normalise_origin(urljoin(base, src.group(1).replace("&amp;", "&")))
        out.append((ident.group(1) if ident else "", origin))
    return out


def _load_steps(rec: Path) -> list[_Step]:
    steps_file = rec / "steps.jsonl"
    if not steps_file.is_file():
        raise ProposalError(
            f"{rec.name}: no steps.jsonl; a recording without steps cannot be split"
        )
    out: list[_Step] = []
    for n, line in enumerate(steps_file.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        url = str(data.get("url") or "")
        p = urlsplit(url)
        step = _Step(n, data, int(data.get("seq", n + 1)), _time(data.get("ts")), url,
                     normalise_origin(url), p.path or "/")  # fmt: skip
        frames: list[tuple[str, str]] = []
        for key in ("dom", "dom_before"):
            if data.get(key):
                html = _read(rec / data[key])
                frames += _iframes(html, url)
                if 'type="password"' in html:
                    step.has_password = True
        cross = [(i, o) for i, o in frames if o and o != step.origin]
        step.embedded = list(dict.fromkeys(o for _, o in cross))
        if data.get("frame") and cross:
            first = str(data["frame"]).split(">>>")[0].strip()
            ident = first.split("#", 1)[1] if "#" in first else ""
            match = [o for i, o in cross if ident and i == ident]
            step.frame_origin = (
                (match or [cross[0][1]])[0] if (match or len(set(o for _, o in cross)) == 1) else ""
            )
        out.append(step)
    return out


def _load_har(rec: Path) -> dict:
    har = rec / "recording.har"
    if not har.is_file():
        found = sorted(rec.glob("*.har"))
        if not found:
            raise ProposalError(f"{rec.name}: no recording.har")
        har = found[0]
    try:
        doc = json.loads(har.read_text(encoding="utf-8-sig"))
        doc["log"]["entries"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ProposalError(f"{har.name} is not a HAR file: {exc}") from exc
    return doc


def _header(headers: object, name: str) -> str:
    for h in headers or []:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == name:
            return str(h.get("value") or "")
    return ""


def _entries(doc: dict) -> list[_Entry]:
    out = []
    for raw in doc["log"]["entries"]:
        req = raw.get("request") or {}
        url = str(req.get("url") or "")
        ref = _header(req.get("headers"), "referer")
        dest = _header(req.get("headers"), "sec-fetch-dest").lower()
        mime = str((raw.get("response") or {}).get("content", {}).get("mimeType") or "")
        status = int((raw.get("response") or {}).get("status") or 0)
        out.append(_Entry(raw, _time(raw.get("startedDateTime")), url, normalise_origin(url),
                          urlsplit(url).path or "/", normalise_origin(ref), urlsplit(ref).path or "/",
                          dest in ("document", "iframe", "frame")
                          or (not dest and mime.startswith("text/html")),
                          300 <= status < 400))  # fmt: skip
    return out


# --- proposing ---------------------------------------------------------------------------------------


def _is_sign_on(origin: str, steps: list[_Step], doc_entries: list[_Entry]) -> bool:
    host = urlsplit(origin).hostname or ""
    if SIGN_ON_WORDS.search(host):
        return True
    if steps and all(SIGN_ON_WORDS.search(s.path) for s in steps):
        return True
    # Only redirects and a credential form: a short visit to a page with a password field, entered
    # through a redirect, never used for anything else.
    redirected = any(e.redirect for e in doc_entries)
    return bool(steps) and len(steps) <= 5 and redirected and all(s.has_password for s in steps)


def _pattern(path: str) -> str:
    from labctl import sanitize

    return sanitize.route_pattern(path)


def propose(recording: Path) -> Proposal:
    """Group a recording's steps by the origin of their top-level page."""
    rec = Path(recording)
    if not rec.is_dir():
        raise ProposalError(f"{rec}: a recording folder is needed (steps.jsonl and recording.har)")
    steps = _load_steps(rec)
    entries = _entries(_load_har(rec))
    steps = [s for s in steps if s.origin] or []
    if not steps:
        raise ProposalError(f"{rec.name}: no step has an http(s) page address; nothing to group")
    order: list[str] = []
    groups: dict[str, list[_Step]] = {}
    for s in steps:
        if s.origin not in groups:
            order.append(s.origin)
            groups[s.origin] = []
        groups[s.origin].append(s)
    taken: set[str] = set()
    result = []
    for origin in order:
        ss = groups[origin]
        docs = [e for e in entries if e.origin == origin and e.is_document]
        base = suggest_id(origin)
        app_id, n = base, 2
        while app_id in taken:
            app_id, n = f"{base}-{n}", n + 1
        taken.add(app_id)
        embedded = list(dict.fromkeys(o for s in ss for o in s.embedded))
        sign_on = _is_sign_on(origin, ss, docs)
        result.append(Entry(app_id, origin, "sign-on" if sign_on else "app", not sign_on, None,
                            embedded, len(ss), len({_pattern(s.path) for s in ss})))  # fmt: skip
    if not any(e.include for e in result):  # a recording of nothing but sign-on: keep it usable
        for e in result:
            e.include = True
    return Proposal(rec.name, result)


# --- applying ----------------------------------------------------------------------------------------


class _Router:
    """Which application (by id) a step or HAR entry belongs to."""

    def __init__(self, proposal: Proposal, steps: list[_Step]):
        self.by_origin: dict[str, list[Entry]] = {}
        for e in proposal.apps:
            self.by_origin.setdefault(e.origin, []).append(e)
        self.steps = steps
        self.timed = sorted((s for s in steps if s.ts), key=lambda s: s.ts)
        self.step_app: dict[int, Entry] = {}
        last: Entry | None = None
        for s in steps:
            hit = self.of_origin(s.origin, s.path, s.ts)
            if hit is None:
                hit = last  # about:blank and the like stay with the page before
            if hit is not None:
                self.step_app[s.index] = hit
                last = hit
        # leading steps without an address take the first application found after them
        first = next((self.step_app[s.index] for s in steps if s.index in self.step_app), None)
        for s in steps:
            if s.index not in self.step_app and first is not None:
                self.step_app[s.index] = first
        for s in steps:  # an iframe separated into its own application
            if s.frame_origin and s.frame_origin in self.by_origin:
                self.step_app[s.index] = self.by_origin[s.frame_origin][0]

    def of_origin(self, origin: str, path: str, ts: datetime | None) -> Entry | None:
        options = self.by_origin.get(origin)
        if not options:
            return None
        if len(options) == 1:
            return options[0]
        prefixed = [o for o in options if o.path_prefix]
        best = None
        for o in prefixed:
            p = o.path_prefix or ""
            if (path == p or path.startswith(p + "/")) and (
                best is None or len(p) > len(best.path_prefix or "")
            ):
                best = o
        if best is not None:
            return best
        default = [o for o in options if not o.path_prefix]
        if default:
            return default[0]
        near = self.by_time(ts)  # prefixes only, none matches: the page open at that time
        return near if near is not None and near.origin == origin else options[0]

    def by_time(self, ts: datetime | None) -> Entry | None:
        if not self.timed:
            return self.step_app.get(self.steps[0].index) if self.steps else None
        pick = self.timed[0]
        if ts is not None:
            for s in self.timed:
                if s.ts <= ts:
                    pick = s
                else:
                    break
        return self.step_app.get(pick.index)

    def entry_app(self, e: _Entry) -> Entry | None:
        # A page's own document request: by its address. Anything else: the page that asked
        # (its Referer), and when that says nothing, the page that was open at the time.
        if e.is_document and e.origin in self.by_origin:
            return self.of_origin(e.origin, e.path, e.ts)
        if e.ref_origin and e.ref_origin in self.by_origin:
            return self.of_origin(e.ref_origin, e.ref_path, e.ts)
        return self.by_time(e.ts)


def _flow_text(step: _Step) -> str:
    d = step.data
    action = str(d.get("action") or "")
    if action == "goto":
        return "open the page"
    sel = d.get("selector")
    return f"{action} {sel}" if sel else action


def apply(
    recording: Path, proposal: Proposal, root: Path, *, overwrite: bool = False
) -> list[Path]:
    """Write `root/evidence/<app>/<recording>/` for each included application, and the sequence
    file `root/evidence/_sequences/<recording>.flow.yaml`. Returns the recording folders."""
    rec, root = Path(recording), Path(root)
    if not rec.is_dir():
        raise ProposalError(f"{rec}: a recording folder is needed")
    proposal = from_dict(proposal.to_dict(), "proposal")  # validate what is being applied
    steps = _load_steps(rec)
    doc = _load_har(rec)
    entries = _entries(doc)
    top = {s.origin for s in steps if s.origin}
    known = top | {o for s in steps for o in s.embedded}
    have = {e.origin for e in proposal.apps}
    for e in proposal.apps:
        if e.origin not in known:
            raise ProposalError(
                f"app '{e.id}': origin {e.origin} does not appear in {rec.name}; "
                f"origins seen: {', '.join(sorted(top)) or 'none'}"
            )
    missing = sorted(top - have)
    if missing:
        raise ProposalError(
            f"origin(s) {', '.join(missing)} appear in {rec.name} but not in the proposal; "
            "add an entry for each or set include: false"
        )
    router = _Router(proposal, steps)
    included = {e.id for e in proposal.apps if e.include}
    ids = list(dict.fromkeys(e.id for e in proposal.apps if e.include))
    name = rec.name
    outs = {i: root / "evidence" / i / name for i in ids}
    for out in outs.values():
        if out.exists() and not overwrite:
            raise ProposalError(f"{out} already exists; remove it or pass overwrite")
        if out.resolve() == rec.resolve():
            raise ProposalError(f"{out} is the recording being split")
    # which entries and documents go where
    ent_app: dict[int, str] = {}
    for n, e in enumerate(entries):
        a = router.entry_app(e)
        if a is not None:
            ent_app[n] = a.id
    for out in outs.values():
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
    paths = []
    for i in ids:
        mine = [s for s in steps if router.step_app[s.index].id == i]
        _write_part(rec, outs[i], doc, entries, ent_app, i, mine)
        paths.append(outs[i])
    _write_sequence(rec, root, steps, router, included, proposal)
    return paths


def _write_part(rec: Path, out: Path, doc: dict, entries: list[_Entry], ent_app: dict[int, str],
                app_id: str, mine: list[_Step]) -> None:  # fmt: skip
    (out / "dom").mkdir()
    lines = []
    for local, s in enumerate(mine, 1):
        step = dict(s.data)
        old = s.seq
        step["seq"] = local
        step["global_seq"] = old
        for key, suffix in (("dom", ".html"), ("dom_before", ".before.html")):
            rel = step.get(key)
            if rel and (rec / rel).is_file():
                new = f"dom/{local:04d}{suffix}"
                _copy_text(rec / rel, out / new, old, local)
                step[key] = new
            elif rel:
                step[key] = None
        shot = step.get("screenshot")
        if shot:
            step["screenshot"] = _copy_screens(rec, out, old, local, shot)
        lines.append(json.dumps(step))
    (out / "steps.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    mine_entries = [e.raw for n, e in enumerate(entries) if ent_app.get(n) == app_id]
    pages = {e.get("pageref") for e in mine_entries}
    log = dict(doc["log"])
    log["entries"] = mine_entries
    log["pages"] = [p for p in doc["log"].get("pages") or [] if p.get("id") in pages]
    (out / "recording.har").write_text(json.dumps({"log": log}), encoding="utf-8")
    _copy_docs(rec, out, entries, ent_app, app_id)


def _copy_text(src: Path, dst: Path, old: int, new: int) -> None:
    text = src.read_text(encoding="utf-8")
    text = text.replace(f"screens/{old:04d}.", f"screens/{new:04d}.")  # canvas pictures
    dst.write_text(text, encoding="utf-8")


def _copy_screens(rec: Path, out: Path, old: int, new: int, shot: str) -> str | None:
    folder = rec / "screens"
    found = False
    if folder.is_dir():
        for f in sorted(folder.glob(f"{old:04d}.*")):
            (out / "screens").mkdir(exist_ok=True)
            shutil.copyfile(f, out / "screens" / f"{new:04d}.{f.name.split('.', 1)[1]}")
            found = True
    return (
        f"screens/{new:04d}.png"
        if found and (out / "screens" / f"{new:04d}.png").exists()
        else None
    )


def _copy_docs(
    rec: Path, out: Path, entries: list[_Entry], ent_app: dict[int, str], app_id: str
) -> None:
    index = rec / "docs" / "documents.json"
    if not index.is_file():
        return
    try:
        docs = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    url_app = {e.url: ent_app.get(n) for n, e in enumerate(entries)}
    kept = []
    for d in docs if isinstance(docs, list) else []:
        url = str(d.get("url") or "")
        owner = url_app.get(url)
        if owner is None:
            owner = ent_app.get(
                next((n for n, e in enumerate(entries) if e.origin == normalise_origin(url)), -1)
            )
        if owner == app_id:
            kept.append(d)
    if not kept:
        return
    (out / "docs").mkdir()
    result = []
    for n, d in enumerate(kept, 1):
        d = dict(d)
        old = str(d.get("file") or "")
        stem = Path(old).stem
        for ext in (".pdf", ".txt"):
            if (rec / "docs" / f"{stem}{ext}").is_file():
                shutil.copyfile(rec / "docs" / f"{stem}{ext}", out / "docs" / f"{n:02d}{ext}")
        d["file"] = f"docs/{n:02d}.pdf"
        result.append(d)
    (out / "docs" / "documents.json").write_text(json.dumps(result, indent=1), encoding="utf-8")


def _write_sequence(rec: Path, root: Path, steps: list[_Step], router: _Router, included: set[str],
                    proposal: Proposal) -> Path:  # fmt: skip
    """The whole order across applications, in the shape of `appmodel.Flow` steps (screen, path,
    do) plus the global step number and the app id; `apps` replaces the single `app`."""
    from labctl.model_build import screen_name

    rows = []
    for s in steps:
        app = router.step_app[s.index]
        pattern = _pattern(s.path)
        row = {"n": s.seq, "app": app.id, "screen": screen_name(pattern), "path": pattern,
               "do": _flow_text(s)}  # fmt: skip
        if s.data.get("frame"):
            row["frame"] = str(s.data["frame"])
        if app.id not in included:
            row["excluded"] = True
        rows.append(row)
    ids = list(dict.fromkeys(e.id for e in proposal.apps))
    data = {
        "id": re.sub(r"[^a-z0-9-]+", "-", rec.name.lower()).strip("-") or "recording",
        "kind": "cross-app-sequence",
        "recording": rec.name,
        "requested_by": "labctl evidence split",
        "purpose": f"Order of the steps of recording {rec.name} across its applications",
        "apps": ids,
        "steps": rows,
    }
    folder = root / "evidence" / SEQUENCES
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rec.name}.flow.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def sequence_path(recording: Path, root: Path) -> Path:
    return Path(root) / "evidence" / SEQUENCES / f"{Path(recording).name}.flow.yaml"
