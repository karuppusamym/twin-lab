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

A recording that used several tabs (`tabs.json` beside it) is split the same way: each step
belongs to the application of the page it happened on, whatever tab that was, and the proposal
lists the tabs each application was seen in (the same application in two tabs is one entry).
Requests go to the application of the tab that made them (the HAR's `pageref`), by the page that
tab showed at the time; without that, by their Referer and time. The tab steps themselves
(`tab-open`, `tab-switch`, `tab-close`) are not part of any application's recording: they are in
the sequence file, which also says where the flow hands over from one application to another and
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
    tab: str = ""

    @property
    def is_tab(self) -> bool:
        """A tab was opened, switched to or closed: no application's own action."""
        return str(self.data.get("action") or "") in TAB_ACTIONS


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
                     normalise_origin(url), p.path or "/", tab=str(data.get("tab") or ""))  # fmt: skip
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
    steps = _load_steps(rec)
    entries = _entries(_load_har(rec))
    steps = [s for s in steps if s.origin and not s.is_tab] or []
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
    return Proposal(rec.name, result)


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
        out.append({"shape": shape, "guess": True, "how": how,
                    "from": {"app": router.step_app[p.index].id, "step": p.seq,
                             "screen": screen_name(_pattern(p.path)),
                             **({"field": p.data.get("selector")} if how == "typed" else {})},
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
    # A page load with no web address (a new tab's about:blank) belongs to no application.
    steps = [s for s in _load_steps(rec)
             if s.origin or s.is_tab or s.data.get("action") != "goto"]  # fmt: skip
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
    paths = []
    for i in ids:
        mine = [s for s in steps if router.step_app[s.index].id == i and not s.is_tab]
        _write_part(rec, outs[i], doc, entries, ent_app, i, mine)
        paths.append(outs[i])
    _write_sequence(rec, root, steps, router, included, proposal, tabs)
    return paths


def _write_part(rec: Path, out: Path, doc: dict, entries: list[_Entry], ent_app: dict[int, str],
                app_id: str, mine: list[_Step]) -> None:  # fmt: skip
    (out / "dom").mkdir()
    lines = []
    local_of = {s.seq: n for n, s in enumerate(mine, 1)}  # whole recording's step -> this part's
    for local, s in enumerate(mine, 1):
        step = json.loads(json.dumps(s.data))  # a deep copy: locators are changed below
        old = s.seq
        step["seq"] = local
        step["global_seq"] = old
        # Step numbers inside a step are this part's numbers too. A cause in another application
        # (a link in one app that loads the other in the same tab) is no cause here.
        if isinstance(step.get("caused_by"), int):
            if step["caused_by"] in local_of:
                step["caused_by"] = local_of[step["caused_by"]]
            else:
                step.pop("caused_by")
        for cand in step.get("locators") or []:
            if isinstance(cand, dict) and isinstance(cand.get("text_from"), int):
                cand["text_from"] = local_of.get(cand["text_from"])
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
    _split_bindings(rec, out, local_of)
    if (rec / "recording.json").is_file():  # how it was recorded: browser, viewport
        shutil.copyfile(rec / "recording.json", out / "recording.json")


def _split_bindings(rec: Path, out: Path, local_of: dict[int, int]) -> None:
    """The part of the recording's bindings file that binds this part's steps, renumbered."""
    src = rec / BINDINGS
    if not src.is_file():
        return
    try:
        data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    steps = data.get("steps") if isinstance(data, dict) else None
    mine = {}
    for key, entry in (steps or {}).items():
        try:
            seq = int(key)
        except (TypeError, ValueError):
            continue
        if seq in local_of:
            mine[local_of[seq]] = entry
    if not mine:
        return
    body = {"version": data.get("version", 1), "recording": out.name,
            "steps": dict(sorted(mine.items()))}  # fmt: skip
    (out / BINDINGS).write_text(
        "# Split from the bindings of the whole recording; step numbers are this part's own.\n"
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
                    proposal: Proposal, tabs: dict[str, dict] | None = None) -> Path:  # fmt: skip
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
