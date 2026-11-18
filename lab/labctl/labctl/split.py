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

Every reference to a step is renumbered with an explicit map from the whole recording's numbers
to the part's (`global_seq` keeps the original): `caused_by`, a record locator's `text_from`,
the bindings' step keys, and the `after:` steps and `{binding: n}` / `{record: n}` values of an
`acceptance.yaml` or `visual.yaml` beside the recording (each case or state goes to the part it
is evaluated in). A reference to a step that went to ANOTHER application is kept as a cross-app
one, `{app, step}` (`caused_by_outside`, `text_from_outside`, `{binding: {app, step}}`), and the
value the whole recording's bindings give that step is copied under the part's bindings
`outside:`, so a replay of the part takes it as supplied from outside. Setup events (the blank
page a browser starts on: labctl.botlog.recording.setup_reason) are in no application: the
proposal (`setup_steps`), the parts and the sequence file (`setup`, `counts`) leave them out the
same way, and the sequence file keeps them for provenance.

A recording that used several tabs (`tabs.json` beside it) is split the same way: each step
belongs to the application of the page it happened on, whatever tab that was, and the proposal
lists the tabs each application was seen in (the same application in two tabs is one entry).
Requests go to the application of the tab that made them (the HAR's `pageref`), by the page that
tab showed at the time; without that, by their Referer and time. The tab steps themselves
(`tab-open`, `tab-switch`, `tab-close`) are in an application's recording only when it was used in
more than one tab (so the part replays on its own: the tabs it opened, closed and switched to);
all of them are in the sequence file, which also says where the flow hands over from one application to another and
which values appear to travel between them (by shape only, a guess). A `bindings.yaml` beside the
recording is split with it, its step numbers changed to each part's own.

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
KNOWN_KEYS = {"id", "origin", "kind", "include", "path_prefix", "embedded", "steps", "screens",
              "tabs"}  # fmt: skip
TAB_ACTIONS = ("tab-open", "tab-switch", "tab-close")
BINDINGS = "bindings.yaml"


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
    tabs: list[str] = field(default_factory=list)  # informational: the tabs it was seen in

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
        if self.tabs:
            out["tabs"] = list(self.tabs)
        return out


@dataclass
class Proposal:
    recording: str
    apps: list[Entry] = field(default_factory=list)
    setup: int = 0  # informational: setup events (blank pages), in no application
    # What is damaged in the recording (see `problems()`); filled by propose(), never saved.
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict = {"recording": self.recording}
        if self.setup:
            out["setup_steps"] = self.setup
        out["apps"] = [e.to_dict() for e in self.apps]
        return out


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
        tabs = raw.get("tabs") or []
        if not isinstance(tabs, list):
            raise _err(f"{label}: tabs must be a list")
        entries.append(Entry(app_id, normalise_origin(origin), kind, include, prefix or None, emb,
                             int(raw.get("steps") or 0), int(raw.get("screens") or 0),
                             [str(t) for t in tabs]))  # fmt: skip
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
    return Proposal(rec, entries, int(data.get("setup_steps") or 0))


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
    tab: str = ""

    @property
    def is_tab(self) -> bool:
        """A tab was opened, switched to or closed: no application's own action."""
        return str(self.data.get("action") or "") in TAB_ACTIONS

    @property
    def setup(self) -> str | None:
        """Why this is a setup event (a blank page, a browser page), or None."""
        from labctl.botlog.recording import setup_reason

        return setup_reason(self.data)


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
    pageref: str = ""
    top_document: bool = False  # a page's own load (not an iframe's)


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


def _load_steps(rec: Path, problems: list[str] | None = None) -> list[_Step]:
    """The steps as written. A line that is not a step is left out; `problems` (when given) gets
    a line for each, for each snapshot a step names that is not there, and for step numbers
    used twice (see `problems()`)."""
    from labctl.botlog.recording import bad_line, seq_problems

    problems = problems if problems is not None else []
    steps_file = rec / "steps.jsonl"
    if not steps_file.is_file():
        raise ProposalError(
            f"{rec.name}: no steps.jsonl; a recording without steps cannot be split"
        )
    out: list[_Step] = []
    text = steps_file.read_text(encoding="utf-8-sig", errors="replace")
    lines = [(n, line) for n, line in enumerate(text.splitlines()) if line.strip()]
    for n, line in lines:
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                raise TypeError("not an object")
            seq = int(data.get("seq") or n + 1)
        except (ValueError, TypeError) as exc:
            problems.append(bad_line(n + 1, n == lines[-1][0], exc))
            continue
        url = str(data.get("url") or "")
        p = urlsplit(url)
        step = _Step(n, data, seq, _time(data.get("ts")), url,
                     normalise_origin(url), p.path or "/", tab=str(data.get("tab") or ""))  # fmt: skip
        frames: list[tuple[str, str]] = []
        for key in ("dom", "dom_before", "screenshot"):
            rel = data.get(key)
            if rel and not (isinstance(rel, str) and (rec / rel).is_file()):
                problems.append(f"step {seq}: {rel} not found; the step is kept without it")
        for key in ("dom", "dom_before"):
            if data.get(key) and isinstance(data[key], str):
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
    problems += seq_problems([s.seq for s in out])
    return out


def problems(recording: Path) -> list[str]:
    """What is damaged in a recording's steps (a recorder that crashed or was restarted): lines
    that are not steps, snapshots a step names that are not there, step numbers used twice."""
    found: list[str] = []
    _load_steps(Path(recording), found)
    return found


def _load_har(rec: Path) -> dict:
    har = rec / "recording.har"
    if not har.is_file():
        found = sorted(rec.glob("*.har"))
        if not found:
            raise ProposalError(
                f"{rec.name}: no recording.har; without the requests the steps cannot be given "
                "to an application (was the recorder stopped before it wrote the HAR?)"
            )
        har = found[0]
    try:
        text = har.read_text(encoding="utf-8-sig", errors="replace")
        if not text.strip():
            raise ValueError("the file is empty")
        doc = json.loads(text)
        if not isinstance(doc["log"]["entries"], list):
            raise TypeError("'entries' is not a list")
        doc["log"]["entries"] = [e for e in doc["log"]["entries"] if isinstance(e, dict)]
    except (ValueError, KeyError, TypeError) as exc:
        why = f"no {exc}" if isinstance(exc, KeyError) else str(exc)
        raise ProposalError(
            f"{rec.name}: {har.name} is not a complete HAR file ({why}); the recorder may have "
            "stopped before it was written. Record again, or restore the file"
        ) from exc
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
                          300 <= status < 400, str(raw.get("pageref") or ""),
                          dest == "document"))  # fmt: skip
    return out


def _load_tabs(rec: Path) -> dict[str, dict]:
    """`tabs.json` (the recorder writes it when several tabs were used): HAR page id -> tab."""
    try:
        rows = json.loads((rec / "tabs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("har_page") and row.get("tab"):
            out[str(row["har_page"])] = row
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
    damage: list[str] = []
    steps = _load_steps(rec, damage)
    if not steps:
        raise ProposalError(
            f"{rec.name}: steps.jsonl has no step; an empty recording cannot be split"
            + "".join(f"\n  {p}" for p in damage)
        )
    entries = _entries(_load_har(rec))
    setup = sum(1 for s in steps if s.setup)
    steps = [s for s in steps if s.origin and not s.is_tab and not s.setup] or []
    if not steps:
        raise ProposalError(f"{rec.name}: no step has an http(s) page address; nothing to group")
    several_tabs = len({s.tab for s in steps if s.tab}) > 1
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
        tabs = list(dict.fromkeys(s.tab for s in ss if s.tab)) if several_tabs else []
        result.append(Entry(app_id, origin, "sign-on" if sign_on else "app", not sign_on, None,
                            embedded, len(ss), len({_pattern(s.path) for s in ss}), tabs))  # fmt: skip
    if not any(e.include for e in result):  # a recording of nothing but sign-on: keep it usable
        for e in result:
            e.include = True
    return Proposal(rec.name, result, setup, damage)


# --- applying ----------------------------------------------------------------------------------------


class _Router:
    """Which application (by id) a step or HAR entry belongs to."""

    def __init__(self, proposal: Proposal, steps: list[_Step], entries: list[_Entry] = (),
                 tabs: dict[str, dict] | None = None):  # fmt: skip
        self.by_origin: dict[str, list[Entry]] = {}
        for e in proposal.apps:
            self.by_origin.setdefault(e.origin, []).append(e)
        self.steps = steps
        real = [s for s in steps if not s.is_tab]
        self.timed = sorted((s for s in real if s.ts), key=lambda s: s.ts)
        self.step_app: dict[int, Entry] = {}
        last: Entry | None = None
        for s in steps:
            hit = None if s.is_tab else self.of_origin(s.origin, s.path, s.ts)
            if hit is None:
                hit = last  # about:blank, a tab being opened, stay with the page before
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
        # A switch belongs to the tab switched to; a close to the tab closed.
        for n, s in enumerate(steps):
            action = s.data.get("action")
            if action == "tab-switch":
                nxt = next((x for x in steps[n + 1 :] if not x.is_tab), None)
                if nxt is not None and nxt.index in self.step_app:
                    self.step_app[s.index] = self.step_app[nxt.index]
            elif action == "tab-close":
                before = next((x for x in reversed(steps[:n]) if not x.is_tab and x.tab == s.tab),
                              None)  # fmt: skip
                if before is not None and before.index in self.step_app:
                    self.step_app[s.index] = self.step_app[before.index]
        # Requests by the tab that made them (the HAR says which: `pageref`), when the recorder
        # wrote which tab that is: the application of the page that tab showed at the time.
        tabs = tabs or {}
        top = {s.origin for s in real if s.origin}
        self.frame_only = {o for o in self.by_origin if o not in top}
        self.tab_pages: dict[str, list[tuple[datetime, Entry]]] = {}
        for e in entries:
            if e.pageref in tabs and e.top_document and e.ts and e.origin in self.by_origin:
                app = self.of_origin(e.origin, e.path, e.ts)
                if app is not None:
                    self.tab_pages.setdefault(e.pageref, []).append((e.ts, app))
        for loads in self.tab_pages.values():
            loads.sort(key=lambda x: x[0])

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
        if e.origin in self.frame_only:  # an embedded site made its own application
            return self.of_origin(e.origin, e.path, e.ts)
        loads = self.tab_pages.get(e.pageref)
        if loads:
            pick = loads[0][1]
            for at, app in loads:
                if e.ts is not None and at <= e.ts:
                    pick = app
            return pick
        if e.ref_origin and e.ref_origin in self.by_origin:
            return self.of_origin(e.ref_origin, e.ref_path, e.ts)
        return self.by_time(e.ts)


def _flow_text(step: _Step) -> str:
    d = step.data
    action = str(d.get("action") or "")
    if action == "goto":
        return "open the page"
    if action == "tab-open":
        opener = f" from tab {d['opener']}" if d.get("opener") else ""
        return f"open tab {d.get('tab')}{opener}"
    if action == "tab-switch":
        return f"switch to tab {d.get('tab')}"
    if action == "tab-close":
        return f"close tab {d.get('tab')}"
    sel = d.get("selector")
    return f"{action} {sel}" if sel else action


TOKEN = re.compile(r"[^\s<>\"']+")


def _token_shapes(html: str) -> set[str]:
    """The shapes (letters A, digits 9) of the words a page shows and its fields hold."""
    from labctl.behaviour.dom import shape

    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", html, flags=re.S | re.I)
    words = re.findall(r">([^<]+)<", text) + re.findall(r'\bvalue="([^"]*)"', text)
    out = set()
    for chunk in words:
        for tok in TOKEN.findall(chunk):
            tok = tok.strip(".,;:()[]{}!?")
            if tok:
                out.add(shape(tok))
    return out


def _shown_in(html: str, shape: str) -> str | None:
    """The element a page shows a value of this shape in (`b#savedNumber`): the one with the
    shortest text holding a word of that shape; None when no element with an id or name does."""
    from labctl.behaviour import dom as domlib

    best: tuple[int, str] | None = None
    for key, el in domlib.snapshot(html).elements.items():
        text = el.text if el.tag not in ("input", "select", "textarea") else el.value
        if not text:
            continue
        shapes = {domlib.shape(t.strip(".,;:()[]{}!?")) for t in TOKEN.findall(text)}
        if shape not in shapes:
            continue
        sel = f"{el.tag}{key}" if key.startswith("#") else key
        rank = (len(text) + (1000 if el.container else 0), sel)
        if best is None or rank < best:
            best = rank
    return best[1] if best else None


def _distinct(shape: str | None, shown: bool) -> bool:
    """A shape worth matching across applications: a value typed in both can be anything but a
    lone word or number; one shown in one and typed in the other must hold a digit and more."""
    if not shape or len(shape) < 3:
        return False
    return ("9" in shape and any(c not in "9" for c in shape)) if shown else True


def _travels(rec: Path, steps: list[_Step], router: _Router) -> list[dict]:
    """Values that appear to go from one application to the next: a value typed in one that has
    the shape of a value typed earlier in another, or of a value the other showed on its last
    page before. Shapes only, never values: a guess for a person to confirm."""
    from labctl.model_build import screen_name

    real = [s for s in steps if not s.is_tab and s.index in router.step_app]
    out: list[dict] = []
    seen: set[tuple] = set()
    for i, s in enumerate(real):
        shape = s.data.get("typed_shape")
        if s.data.get("action") != "type" or not isinstance(shape, str):
            continue
        app = router.step_app[s.index].id
        found = None
        for p in reversed(real[:i]):
            other = router.step_app[p.index].id
            if (
                other != app
                and p.data.get("action") == "type"
                and p.data.get("typed_shape") == shape
            ):
                found = ("typed", p) if _distinct(shape, False) else None
                break
        if found is None and _distinct(shape, True):
            for p in reversed(real[:i]):
                if router.step_app[p.index].id == app or not p.data.get("dom"):
                    continue
                if shape in _token_shapes(_read(rec / p.data["dom"])):
                    found = ("shown", p)
                break  # only the other application's latest page
        if found is None:
            continue
        how, p = found
        key = (shape, how, router.step_app[p.index].id, _pattern(p.path), p.data.get("selector"),
               app, _pattern(s.path), s.data.get("selector"))  # fmt: skip
        if key in seen:  # the same value going the same way again: said once
            continue
        seen.add(key)
        shown_in = _shown_in(_read(rec / p.data["dom"]), shape) if how == "shown" else None
        out.append({"shape": shape, "guess": True, "how": how,
                    "from": {"app": router.step_app[p.index].id, "step": p.seq,
                             "screen": screen_name(_pattern(p.path)),
                             **({"field": p.data.get("selector")} if how == "typed" else {}),
                             **({"element": shown_in} if shown_in else {})},
                    "to": {"app": app, "step": s.seq, "screen": screen_name(_pattern(s.path)),
                           "field": s.data.get("selector")}})  # fmt: skip
    return out


def _handovers(rows: list[dict]) -> list[dict]:
    """Where the flow leaves one application for another: the last step in the one, the first
    in the next, and how (in the same tab, in a new tab, back to a tab already open)."""
    out = []
    prev = None
    between: list[dict] = []
    for row in rows:
        if row.get("excluded"):
            continue
        if row["do"].startswith(("open tab", "switch to tab", "close tab")):
            between.append(row)
            continue
        if prev is not None and row["app"] != prev["app"]:
            opened = [b for b in between if b["do"].startswith("open tab")]
            switched = [b for b in between if b["do"].startswith("switch to tab")]
            how = ("in a new tab" if opened and row.get("tab") == opened[-1].get("tab")
                   else "back to a tab already open" if switched
                   else "in the same tab" if row.get("tab") == prev.get("tab")
                   else "in another tab")  # fmt: skip
            out.append({"from": {"app": prev["app"], "step": prev["n"], "screen": prev["screen"],
                                 "do": prev["do"]},
                        "to": {"app": row["app"], "step": row["n"], "screen": row["screen"]},
                        "how": how})  # fmt: skip
        prev, between = row, []
    return out


def apply(
    recording: Path, proposal: Proposal, root: Path, *, overwrite: bool = False
) -> list[Path]:
    """Write `root/evidence/<app>/<recording>/` for each included application, and the sequence
    file `root/evidence/_sequences/<recording>.flow.yaml`. Returns the recording folders."""
    rec, root = Path(recording), Path(root)
    if not rec.is_dir():
        raise ProposalError(f"{rec}: a recording folder is needed")
    proposal = from_dict(proposal.to_dict(), "proposal")  # validate what is being applied
    # A setup event (the blank page a browser starts on) belongs to no application: it is kept,
    # for provenance, in the sequence file's `setup` list.
    damage: list[str] = []
    every = _load_steps(rec, damage)
    seqs = [s.seq for s in every]
    twice = sorted({q for q in seqs if seqs.count(q) > 1})
    if twice:  # every reference between steps is by number: two steps with one number are lost
        raise ProposalError(
            f"{rec.name}: step number(s) {', '.join(map(str, twice[:10]))} are used more than "
            "once in steps.jsonl (the recorder may have been restarted and appended), so a "
            "reference to one of them cannot be followed. Keep one run's lines, or record again"
        )
    setup = [s for s in every if s.setup and not s.is_tab]
    steps = [s for s in every if not (s.setup and not s.is_tab)
             and (s.origin or s.is_tab or s.data.get("action") != "goto")]  # fmt: skip
    doc = _load_har(rec)
    entries = _entries(doc)
    tabs = _load_tabs(rec)
    top = {s.origin for s in steps if s.origin and not s.is_tab}
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
    router = _Router(proposal, steps, entries, tabs)
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
    app_of = {s.seq: router.step_app[s.index].id for s in steps
              if not s.is_tab and s.index in router.step_app}  # fmt: skip
    last_app = next((app_of[s.seq] for s in reversed(steps) if s.seq in app_of), None)
    paths = []
    for i in ids:
        mine = [s for s in steps if router.step_app[s.index].id == i and not s.is_tab]
        _write_part(rec, outs[i], doc, entries, ent_app, i, _with_tabs(mine, steps),
                    app_of, last_app == i)  # fmt: skip
        paths.append(outs[i])
    _write_sequence(rec, root, steps, router, included, proposal, tabs, setup, len(every), damage)
    return paths


def _with_tabs(mine: list[_Step], steps: list[_Step]) -> list[_Step]:
    """The part's steps, with the tab steps it needs to replay on its own when the application
    was used in more than one tab: each tab opened after the first (its opener kept only when
    that tab is the part's too), each tab closed, and a switch wherever the next step is in
    another tab than the one before (the switches are made again for the part)."""
    used = list(dict.fromkeys(s.tab for s in mine if s.tab))
    if len(used) < 2:
        return mine
    keep = {id(s) for s in mine}
    out: list[_Step] = []
    current = None
    for s in steps:
        action = str(s.data.get("action") or "")
        if s.is_tab:
            if s.tab not in used or action == "tab-switch":
                continue
            if action == "tab-open":
                if not out:  # the part starts in this tab: nothing to open
                    continue
                data = dict(s.data)
                if data.get("opener") not in used:
                    data.pop("opener", None)  # opened from another application: open it here
                s = _Step(s.index, data, s.seq, s.ts, s.url, s.origin, s.path, tab=s.tab)
                current = s.tab
            elif action == "tab-close" and current == s.tab:
                current = None
            out.append(s)
            continue
        if id(s) not in keep:
            continue
        if current is not None and s.tab and s.tab != current or (current is None and out):
            if s.tab:
                data = {"seq": None, "ts": s.data.get("ts"), "url": s.url, "action": "tab-switch",
                        "selector": None, "dom": None, "tab": s.tab}  # fmt: skip
                if s.data.get("origin"):
                    data["origin"] = s.data["origin"]
                out.append(_Step(-1, data, 0, s.ts, s.url, s.origin, s.path, tab=s.tab))
        current = s.tab or current
        out.append(s)
    return out


def _write_part(rec: Path, out: Path, doc: dict, entries: list[_Entry], ent_app: dict[int, str],
                app_id: str, mine: list[_Step], app_of: dict[int, str] | None = None,
                has_end: bool = True) -> None:  # fmt: skip
    (out / "dom").mkdir()
    lines = []
    # The explicit map from the whole recording's step numbers to this part's. A reference to a
    # step that went to another application is kept as a cross-app reference ({app, step}: the
    # whole recording's number), never dropped or left pointing at a wrong step.
    local_of = {s.seq: n for n, s in enumerate(mine, 1) if s.seq}
    refs = _Refs(local_of, app_of or {}, app_id)
    for local, s in enumerate(mine, 1):
        step = json.loads(json.dumps(s.data))  # a deep copy: locators are changed below
        old = s.seq or None
        step["seq"] = local
        step["global_seq"] = old
        if isinstance(step.get("caused_by"), int):
            new, outside = refs.step(step["caused_by"])
            if new is not None:
                step["caused_by"] = new
            else:
                step.pop("caused_by")
                step["caused_by_outside"] = outside
        for cand in step.get("locators") or []:
            if isinstance(cand, dict) and isinstance(cand.get("text_from"), int):
                new, outside = refs.step(cand["text_from"])
                cand["text_from"] = new
                if outside is not None:
                    cand["text_from_outside"] = outside
        for key, suffix in (("dom", ".html"), ("dom_before", ".before.html")):
            rel = step.get(key)
            if rel and (rec / rel).is_file():
                new = f"dom/{local:04d}{suffix}"
                _copy_text(rec / rel, out / new, old, local)
                step[key] = new
            elif rel:
                step[key] = None
        shot = step.get("screenshot")
        if shot and old:
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
    _split_declarations(rec, out, refs, has_end)
    _split_bindings(rec, out, local_of, refs)
    if (rec / "recording.json").is_file():  # how it was recorded: browser, viewport
        shutil.copyfile(rec / "recording.json", out / "recording.json")


class _Refs:
    """Step references, from the whole recording's numbers to a part's: (local number, None), or
    (None, {app, step}) for a step that went to another application (or to none: a tab or setup
    step, app None). Every cross-app reference made is remembered for the bindings."""

    def __init__(self, local_of: dict[int, int], app_of: dict[int, str], app_id: str):
        self.local_of, self.app_of, self.app_id = local_of, app_of, app_id
        self.outside: list[dict] = []

    def step(self, seq: int) -> tuple[int | None, dict | None]:
        if seq in self.local_of:
            return self.local_of[seq], None
        ref = {"app": self.app_of.get(seq), "step": seq}
        if ref not in self.outside:
            self.outside.append(ref)
        return None, ref

    def value(self, value):
        """`{binding: n}` and `{record: n}` in an expected value, renumbered or made cross-app."""
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            if len(value) == 1 and next(iter(value)) in ("binding", "record"):
                (kind, ref), = value.items()  # fmt: skip
                try:
                    new, outside = self.step(int(ref))
                except (TypeError, ValueError):
                    return value
                return {kind: new if new is not None else outside}
            return {k: self.value(v) for k, v in value.items()}
        return value

    def owns(self, after, has_end: bool) -> bool:
        """Whether a declaration evaluated `after` a step (or at the end) is this part's."""
        if after in (None, "end") or str(after).strip().lower() == "end":
            return has_end
        try:
            return int(after) in self.local_of
        except (TypeError, ValueError):
            return has_end


DECLARED = (("acceptance.yaml", "cases"), ("visual.yaml", "states"))


def _split_declarations(rec: Path, out: Path, refs: _Refs, has_end: bool) -> None:
    """The acceptance cases and visual states beside the recording, each in the part whose step
    it is evaluated after (`end` in the part the recording ends in), with its step numbers
    renumbered and references to another application's steps kept as cross-app ones. A scope
    or baseline declaration has no step numbers: it is copied to every part."""
    for name, key in DECLARED:
        src = rec / name
        if not src.is_file():
            continue
        try:
            data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        items = []
        for item in data.get(key) or []:
            if not isinstance(item, dict) or not refs.owns(item.get("after", "end"), has_end):
                continue
            item = dict(item)
            if isinstance(item.get("after"), int) or str(item.get("after", "")).isdigit():
                item["after"] = refs.local_of[int(item["after"])]
            for op in ("equals", "contains", "matches"):
                if op in item:
                    item[op] = refs.value(item[op])
            items.append(item)
        body = dict(data)
        body["recording"] = out.name
        body[key] = items
        (out / name).write_text(
            f"# Split from {name} of the whole recording: step numbers are this part's own;\n"
            "# {app, step} is a step of another application (the whole recording's number).\n"
            + yaml.safe_dump(body, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    for name in ("scope.yaml", "baseline.yaml"):
        if (rec / name).is_file():
            shutil.copyfile(rec / name, out / name)


def _split_bindings(
    rec: Path, out: Path, local_of: dict[int, int], refs: _Refs | None = None
) -> None:
    """The part of the recording's bindings file that binds this part's steps, renumbered, and,
    under `outside:`, the values the whole recording's bindings give the other applications'
    steps this part refers to (`<app>#<step>`): supplied from outside."""
    src = rec / BINDINGS
    if not src.is_file():
        return
    try:
        data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    steps = data.get("steps") if isinstance(data, dict) else None
    mine, given = {}, {}
    for key, entry in (steps or {}).items():
        try:
            seq = int(key)
        except (TypeError, ValueError):
            continue
        if seq in local_of:
            mine[local_of[seq]] = entry
        given[seq] = entry
    outside = dict(data.get("outside") or {}) if isinstance(data, dict) else {}
    for ref in refs.outside if refs else []:
        entry = given.get(ref["step"])
        value = (
            (entry.get("value") if entry.get("value") is not None else entry.get("record"))
            if isinstance(entry, dict)
            else entry
        )
        if value is not None:
            outside[f"{ref['app']}#{ref['step']}"] = str(value)
    if not mine and not outside:
        return
    body = {"version": data.get("version", 1), "recording": out.name,
            "steps": dict(sorted(mine.items()))}  # fmt: skip
    if outside:
        body["outside"] = outside
    (out / BINDINGS).write_text(
        "# Split from the bindings of the whole recording; step numbers are this part's own.\n"
        "# outside: values of other applications' steps this part refers to (<app>#<step>).\n"
        + yaml.safe_dump(body, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


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
                    proposal: Proposal, tabs: dict[str, dict] | None = None,
                    setup: list[_Step] = (), total: int | None = None,
                    damage: list[str] = ()) -> Path:  # fmt: skip
    """The whole order across applications, in the shape of `appmodel.Flow` steps (screen, path,
    do) plus the global step number and the app id; `apps` replaces the single `app`. With
    `tabs` (which tab each application was used in), `handovers` (where the flow goes from one
    application to the next) and `travels` (values that appear to go along, by shape: a guess)."""
    from labctl.model_build import screen_name

    rows = []
    last_screen: dict[str, str] = {}
    for s in steps:
        app = router.step_app[s.index]
        pattern = _pattern(s.path)
        screen = screen_name(pattern)
        if s.is_tab:  # a tab step has no page of its own: the app's page at the time
            screen = last_screen.get(app.id, screen)
            pattern = next((r["path"] for r in reversed(rows) if r["app"] == app.id), pattern)
        last_screen[app.id] = screen
        row = {"n": s.seq, "app": app.id, "screen": screen, "path": pattern, "do": _flow_text(s)}
        if s.tab:
            row["tab"] = s.tab
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
    }
    try:
        data["source"] = rec.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        pass
    if tabs or any(s.tab and s.tab != "t1" for s in steps):
        data["tabs"] = []
        info = {str(t.get("tab")): t for t in (tabs or {}).values()}
        for tab in dict.fromkeys(s.tab for s in steps if s.tab):
            apps = list(dict.fromkeys(router.step_app[s.index].id for s in steps
                                      if s.tab == tab and not s.is_tab))  # fmt: skip
            data["tabs"].append({"tab": tab, "opener": (info.get(tab) or {}).get("opener"),
                                 "apps": apps})  # fmt: skip
    # Setup events: kept for provenance, in no application and in no count of one. The counts
    # are the ones the proposal and the parts have: total = setup + tab steps + each app's.
    data["setup"] = [{"n": s.seq, "do": "open a blank page" if s.setup == "blank page"
                      else "open a browser page", "reason": s.setup} for s in setup]  # fmt: skip
    per_app: dict[str, int] = dict.fromkeys(ids, 0)
    for s in steps:
        if not s.is_tab:
            per_app[router.step_app[s.index].id] = per_app.get(router.step_app[s.index].id, 0) + 1
    data["counts"] = {"total": total if total is not None else len(steps) + len(setup),
                      "setup": len(setup), "tab": sum(1 for s in steps if s.is_tab),
                      "apps": per_app}  # fmt: skip
    if damage:  # what was damaged in the recording: the parts were written without it
        data["problems"] = list(damage)
    data["handovers"] = _handovers(rows)
    data["travels"] = [t for t in _travels(rec, steps, router)
                       if t["from"]["app"] in included and t["to"]["app"] in included]  # fmt: skip
    data["steps"] = rows
    folder = root / "evidence" / SEQUENCES
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rec.name}.flow.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def sequence_path(recording: Path, root: Path) -> Path:
    return Path(root) / "evidence" / SEQUENCES / f"{Path(recording).name}.flow.yaml"
