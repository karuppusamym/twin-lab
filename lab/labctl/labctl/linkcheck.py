"""Where the links a recording saw, but never followed, really lead: a safe, opt-in check.

A page holds more links than the person clicked. The rebuild should know what each of them
answers in the real application, so this module, run by a person after a recording
(`labctl evidence check-links <recording> --yes`), asks the real application for each one once
and writes what it found to `<recording>/link-check.json`. `labctl.navmap` reads that file when
it is there and says, per link, what the check found; without it nothing changes.

What is collected, from every snapshot of the recording (`dom/`): `<a href>`, `<area href>`,
`<form action>` (a GET form; a POST form is listed and never sent), `<iframe src>` and
`<frame src>`, and the address in an obvious `window.open('...')` or `location.href = '...'`
of an inline handler. Each is resolved against the page (or frame) it was on, and links are
de-duplicated by origin and address pattern (`sanitize.route_pattern`): `/orders/1007` and
`/orders/1008` are one link. A pattern the recording itself visited is not a link to check.

Safety, all enforced here (and tested):

- Opt-in only: without `yes=True` nothing is requested; the plan is listed. Nothing else in the
  lab (the portal, `build-all`) calls `check()`.
- `HEAD`, falling back to `GET` when `HEAD` is refused (405, 501), and one `GET` of an HTML page
  to read its title. Never another method, never a request body, never a click, never a script.
  Redirects are followed by hand, each hop checked as a link of its own.
- An address that looks like it changes something is never requested (`STATE_WORDS`, a query
  that carries an action), nor `mailto:`, `tel:`, `javascript:`, `data:`, a download (by
  extension or the `download` attribute), or an address with a token or session id in it.
  Each skip is listed with its reason.
- Only the origins the recording used itself (and the applications its split proposal
  includes); never a third-party origin, not even as a redirect hop.
- Anonymous, unless the person passes the browser state the recording started from
  (`storage_state`, Playwright's format, as `labctl evidence record --storage-state`): then its
  cookies for the checked origins are sent. No cookie value, token or response body is written.
- Rate limited (default one request a second), at most `max_links` links (default 50), a
  timeout per request; the first 429 or 503 stops the run.

Per link the file says where it was seen (screen, selector, link text masked by the
sanitiser), the final status after redirects (each hop as a pattern), whether it ended on the
recorded sign-in screen, the content type, and for an HTML page of the recording's own origins:
its title and main heading (masked) and whether it has the structure of a recorded screen
(same pattern, or the same form field names). An HTML page the recording never visited is also
kept as a sanitised snapshot (`link-check/pages/<pattern>.html`, exactly as the recorder's
snapshots are sanitised), after the data checks of `labctl.lookfeel` (the recording's known
data values and data-shaped tokens); when a check fails only its title and heading are kept, and
the file says why. A raw body is never kept.

`SafeFetcher` is the HTTP part on its own (GET or HEAD only, given origins only, rate limited,
stops on 429/503), for any other opt-in fetch of the lab.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

from labctl import sanitize

FILE = "link-check.json"
PAGES = "link-check/pages"
FORMAT = "twinlab-link-check/1"
UNRECORDED = "unrecorded"  # apps/<app>/unrecorded/<pattern>.html: reference snapshots
MAX_HOPS = 5
MAX_BODY = 2_000_000
USER_AGENT = "twinlab-linkcheck/1"
STATE_WORDS = {"logout", "signout", "logoff", "delete", "remove", "cancel", "approve", "reject",
               "submit", "confirm", "unsubscribe", "reset", "close", "archive", "pay",
               "transfer", "destroy", "purge", "revoke", "void", "refund"}  # fmt: skip
QUERY_VERBS = STATE_WORDS | {"add", "create", "update", "save", "edit", "set", "send", "execute",
                             "run", "toggle", "enable", "disable", "accept", "decline", "grant",
                             "clear", "insert", "upload", "lock", "unlock", "buy"}  # fmt: skip
ACTION_KEYS = {"action", "op", "operation", "cmd", "command", "do", "method", "_method", "task",
               "verb"}  # fmt: skip
SECRET_KEY = re.compile(
    r"token|session|sess|^sid$|jsessionid|phpsessid|aspsessionid|auth|^key$|api[-_]?key|apikey|"
    r"signature|^sig$|^code$|nonce|ticket|otp|csrf|xsrf|jwt|access|password|passwd|^pwd$|secret|"
    r"credential|saml|^state$",
    re.I,
)
DOWNLOAD_EXT = re.compile(r"\.(pdf|xls[xmb]?|doc[xm]?|csv|zip|exe|msi|7z|rar|gz|tgz|tar|dmg|"
                          r"pkg|iso|ppt[xm]?|odt|ods|rtf|jar|apk)$", re.I)  # fmt: skip
NOT_WEB = ("mailto:", "tel:", "javascript:", "data:", "blob:", "file:", "ftp:", "about:", "sms:")
JS_URL = re.compile(
    r"""(?:window\.open|location\.(?:assign|replace))\s*\(\s*(['"])([^'"]+)\1"""
    r"""|(?:(?:window|document|top|self)\.)?location(?:\.href)?\s*=\s*(['"])([^'"]+)\3"""
)
HTML_TYPES = ("text/html", "application/xhtml+xml")


class Refused(ValueError):
    """A request the safety rules do not allow (a method, an origin)."""


class Throttled(RuntimeError):
    """The application answered 429 or 503: the run stops."""

    def __init__(self, status: int, route: str) -> None:
        super().__init__(f"{status} on {route}")
        self.status, self.route = status, route


# --- the safe fetcher ----------------------------------------------------------------------------


def origin_of(url: str) -> str:
    from labctl import split as sp

    return sp.normalise_origin(url)


@dataclass
class Answer:
    method: str
    status: int
    content_type: str
    location: str | None
    body: bytes | None = None  # in memory only (GET), never written


class SafeFetcher:
    """GET or HEAD, to the given origins only, one request every `delay_ms`, `timeout` seconds
    each, no body, no automatic redirect, no proxy. The first 429 or 503 raises `Throttled`
    and every later request is refused. `cookies` (Playwright storage-state cookies) are sent
    to the hosts they belong to; their values are held in memory only."""

    def __init__(self, origins, *, delay_ms: int = 1000, timeout: float = 10.0,
                 cookies: list[dict] | None = None, max_body: int = MAX_BODY,
                 clock=time.monotonic, sleep=time.sleep) -> None:  # fmt: skip
        self.origins = {origin_of(o) for o in origins if origin_of(o)}
        self.delay = max(0, delay_ms) / 1000
        self.timeout = timeout
        self.cookies = list(cookies or [])
        self.max_body = max_body
        self.clock, self.sleep = clock, sleep
        self.last: float | None = None
        self.count = 0
        self.stopped: Throttled | None = None

    def _cookie_header(self, url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = parts.path or "/"
        now = time.time()
        out = []
        for c in self.cookies:
            domain = str(c.get("domain") or "").lower()
            bare = domain.lstrip(".")
            if not bare or not (host == bare or (domain.startswith(".") and host.endswith(domain))
                                or host.endswith("." + bare)):  # fmt: skip
                continue
            if not path.startswith(str(c.get("path") or "/")):
                continue
            if c.get("secure") and parts.scheme != "https":
                continue
            expires = c.get("expires")
            if isinstance(expires, (int, float)) and 0 < expires < now:
                continue
            if c.get("name"):
                out.append(f"{c['name']}={c.get('value', '')}")
        return "; ".join(out)

    def fetch(self, url: str, method: str = "HEAD") -> Answer:
        method = method.upper()
        if method not in ("GET", "HEAD"):
            raise Refused(f"{method}: only GET and HEAD are ever sent")
        if self.stopped is not None:
            raise Refused(f"stopped after {self.stopped}")
        origin = origin_of(url)
        if not origin or origin not in self.origins:
            raise Refused("not an origin of the recording")
        if self.last is not None:
            wait = self.last + self.delay - self.clock()
            if wait > 0:
                self.sleep(wait)
        parts = urlsplit(url)
        conn_type = (http.client.HTTPSConnection if parts.scheme == "https"
                     else http.client.HTTPConnection)  # fmt: skip
        conn = conn_type(parts.netloc, timeout=self.timeout)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.5"}
        cookie = self._cookie_header(url)
        if cookie:
            headers["Cookie"] = cookie
        self.count += 1
        try:
            conn.request(method, target, headers=headers)  # no body, ever
            resp = conn.getresponse()
            body = resp.read(self.max_body) if method == "GET" else None
            answer = Answer(method, resp.status,
                            (resp.getheader("Content-Type") or "").split(";")[0].strip().lower(),
                            resp.getheader("Location"), body)  # fmt: skip
        finally:
            conn.close()
            self.last = self.clock()
        if answer.status in (429, 503):
            self.stopped = Throttled(answer.status, sanitize.route_pattern(parts.path or "/"))
            raise self.stopped
        return answer


def storage_cookies(path: Path | None) -> list[dict]:
    """The cookies of a Playwright storage-state file (the recorder's `--storage-state`)."""
    if path is None:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [c for c in (data.get("cookies") or []) if isinstance(c, dict)]


# --- what is safe to ask for ---------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.findall(r"[a-z]+", text.lower())


def _has_state_word(text: str, words: set[str]) -> str | None:
    toks = _tokens(text)
    for a, b in zip(toks, toks[1:], strict=False):
        if a + b in ("signout", "logout", "logoff"):
            return a + b
    return next((t for t in toks if t in words), None)


def _secret_value(value: str) -> bool:
    """A long random-looking value: a token, whatever its parameter is called."""
    v = value.strip()
    return (len(v) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-.~+/=%]+", v) is not None
            and bool(re.search(r"\d", v)) and bool(re.search(r"[A-Za-z]", v)))  # fmt: skip


def unsafe(url: str, attrs: dict | None = None) -> str | None:
    """Why an address must not be requested, or None. Checks the address only."""
    low = url.strip().lower()
    for scheme in NOT_WEB:
        if low.startswith(scheme):
            return f"a {scheme} address: not a page"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "not an http(s) address"
    if attrs is not None and "download" in attrs:
        return "a download (the link has a download attribute)"
    path = parts.path or "/"
    if ";" in path and re.search(r";\s*(jsessionid|sid|session)", path, re.I):
        return "a session id in the address"
    if DOWNLOAD_EXT.search(path):
        return f"a download ({path.rsplit('.', 1)[-1].lower()} file)"
    for seg in path.split("/"):
        word = _has_state_word(seg, STATE_WORDS)
        if word:
            return f"looks like it changes something (`{word}` in the address)"
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if SECRET_KEY.search(key) or _secret_value(value):
            return f"a token or session id in the query (`{key}`)"
        if key.lower() in ACTION_KEYS:
            return f"the query carries an action (`{key}`)"
        word = _has_state_word(key, QUERY_VERBS) or _has_state_word(value, QUERY_VERBS)
        if word:
            return f"the query carries an action verb (`{word}`)"
    return None


# --- reading the recording -----------------------------------------------------------------------


@dataclass
class Link:
    url: str  # the first concrete address seen: in memory only, never written
    origin: str
    netloc: str
    route: str
    kind: str
    attrs: dict
    seen: list[dict] = field(default_factory=list)
    seen_count: int = 0


@dataclass
class Recording:
    path: Path
    origins: list[str]  # allowed: the recording's own and its included applications'
    visited: set[tuple[str, str]]  # (netloc, route)
    screens: dict[tuple[str, str], list[frozenset]]  # recorded screen -> form field names
    sign_in: set[tuple[str, str]]
    sign_in_fields: list[frozenset]
    links: list[Link]
    known: set[str]
    vocab: set[str]


def _steps(rec: Path) -> list[dict]:
    from labctl import stepfacts

    return stepfacts._steps(rec)


def _snapshot(rec: Path, rel: str | None) -> str | None:
    from labctl import stepfacts

    return stepfacts._read(rec, rel)


def _proposal_origins(rec: Path) -> list[str]:
    from labctl import split as sp

    for path in (rec.parent / f"{rec.name}.split.yaml",
                 rec.parent.parent / "_recordings" / f"{rec.name}.split.yaml"):  # fmt: skip
        if path.is_file():
            try:
                prop = sp.load(path)
            except (sp.ProposalError, OSError, ValueError):
                continue
            return [e.origin for e in prop.apps if e.include and e.kind != "sign-on"]
    return []


def field_names(tree) -> frozenset:
    from labctl.behaviour import dom as domlib

    return frozenset(
        n.attrs["name"] for n in domlib._walk(tree)
        if n.tag in ("input", "select", "textarea") and n.attrs.get("name")
        and (n.attrs.get("type") or "").lower() not in ("hidden", "submit", "button")
    )  # fmt: skip


def _has_password(tree) -> bool:
    from labctl.behaviour import dom as domlib

    # a password field the screen shows: a closed modal's (Bootstrap keeps its Log in dialog in
    # every page) does not make the screen a sign-in screen; an open one does
    return any(n.tag == "input" and (n.attrs.get("type") or "").lower() == "password"
               and domlib.shown(n) for n in domlib._walk(tree))  # fmt: skip


def _link_text(node) -> str:
    from labctl.behaviour import dom as domlib

    if node.tag in ("a", "area"):
        text = domlib._text(node) if node.tag == "a" else ""
        return (text or node.attrs.get("aria-label") or node.attrs.get("title")
                or node.attrs.get("alt") or "")[:80]  # fmt: skip
    if node.tag == "form":
        button = next((n for n in domlib._walk(node) if n.tag == "button"
                       or (n.tag == "input" and (n.attrs.get("type") or "") == "submit")), None)  # fmt: skip
        if button is None:
            return node.attrs.get("aria-label") or ""
        return (domlib._text(button) or button.attrs.get("value") or "")[:80]
    if node.tag in ("iframe", "frame"):
        return (node.attrs.get("title") or "")[:80]
    return (domlib._text(node) or node.attrs.get("aria-label") or "")[:80]


class _Masker:
    """Interface text through the sanitiser, as the page snapshots have it."""

    def __init__(self, known: set[str], vocab: set[str]) -> None:
        self.known, self.vocab, self.cache = known, vocab, {}

    def __call__(self, text: str) -> str:
        from labctl.behaviour import dom as domlib

        if not text:
            return ""
        if text not in self.cache:
            safe = sanitize.page(f"<p><a href='#'>{escape(text)}</a></p>", self.known, self.vocab)
            node = next((n for n in domlib._walk(domlib.parse_html(safe.html)) if n.tag == "a"),
                        None)  # fmt: skip
            self.cache[text] = domlib._text(node) if node is not None else ""
        return self.cache[text]


def _targets(tree, page_url: str):
    """(node, kind, absolute address) of every link target of a snapshot, in page order."""
    from labctl import navmap
    from labctl.behaviour import dom as domlib

    frames: dict[str, str] = {}

    def base_of(node) -> str:
        for a in navmap._ancestors(node):
            if a.tag == "tl-frame" and a.attrs.get("data-tl-frame"):
                key = a.attrs["data-tl-frame"].split(">>>")[-1].strip()
                return frames.get(key, page_url)
        return page_url

    base_el = next((n for n in domlib._walk(tree) if n.tag == "base" and n.attrs.get("href")), None)
    page_base = urljoin(page_url, base_el.attrs["href"]) if base_el is not None else page_url
    for node in domlib._walk(tree):
        base = base_of(node)
        base = page_base if base == page_url else base
        if node.tag in ("iframe", "frame") and node.attrs.get("src"):
            url = urljoin(base, node.attrs["src"].strip())
            frames[navmap.canon(node)] = url
            yield node, "iframe", url
        elif node.tag in ("a", "area") and node.attrs.get("href") is not None:
            href = node.attrs["href"].strip()
            if href and not href.startswith("#"):
                yield (
                    node,
                    "link" if node.tag == "a" else "area",
                    (href if href.lower().startswith(NOT_WEB) else urljoin(base, href)),
                )
        elif node.tag == "form":
            method = (node.attrs.get("method") or "get").strip().lower()
            action = (node.attrs.get("action") or "").strip()
            if not action:
                continue  # sends to the page itself, which was recorded
            url = action if action.lower().startswith(NOT_WEB) else urljoin(base, action)
            yield node, "form (GET)" if method == "get" else f"form ({method.upper()})", url
        for name, value in node.attrs.items():
            if name.startswith("on") and value:
                for m in JS_URL.finditer(value):
                    addr = (m.group(2) or m.group(4) or "").strip()
                    if addr and not addr.startswith("#"):
                        yield (
                            node,
                            "script",
                            (addr if addr.lower().startswith(NOT_WEB) else urljoin(base, addr)),
                        )


def read(rec: Path) -> Recording:
    """Everything the check needs from a recording folder; nothing is requested."""
    from labctl import har, navmap
    from labctl.behaviour import dom as domlib

    rec = Path(rec)
    raw = _steps(rec)
    if not raw:
        raise FileNotFoundError(f"{rec}: no steps.jsonl, not a recording folder")
    own: list[str] = []
    for s in raw:
        o = origin_of(str(s.get("url") or ""))
        if o and o not in own:
            own.append(o)
    allowed = list(own)
    proposed = _proposal_origins(rec)
    if proposed:
        allowed = [o for o in own if o in proposed] + [o for o in proposed if o not in own]
    visited: set[tuple[str, str]] = set()
    for s in raw:
        url = str(s.get("url") or "")
        if origin_of(url):
            visited.add((urlsplit(url).netloc.lower(), sanitize.route_pattern(urlsplit(url).path)))
    try:
        entries = har.exchanges(har.load(rec).har)
    except (OSError, ValueError, FileNotFoundError):
        entries = []
    hosts = {urlsplit(o).netloc for o in own}
    entries = [e for e in entries if e.host in hosts] if hosts else entries
    for e in entries:
        if e.method == "GET" and e.status:
            visited.add((e.host.lower(), sanitize.route_pattern(e.path)))
    snaps = []
    for s in raw:
        url = str(s.get("url") or "")
        if not origin_of(url):
            continue
        for key in ("dom", "dom_before"):
            html = _snapshot(rec, s.get(key))
            if html is not None:
                snaps.append((url, html))
    known, vocab = har.page_knowledge([h for _, h in snaps], entries)
    mask = _Masker(known, vocab)
    screens: dict[tuple[str, str], list[frozenset]] = {}
    sign_in: set[tuple[str, str]] = set()
    sign_fields: list[frozenset] = []
    links: dict[tuple[str, str], Link] = {}
    for url, html in snaps:
        tree = domlib.parse_html(html)
        here = (urlsplit(url).netloc.lower(), sanitize.route_pattern(urlsplit(url).path))
        names = field_names(tree)
        screens.setdefault(here, [])
        if names and names not in screens[here]:
            screens[here].append(names)
        if _has_password(tree):
            sign_in.add(here)
            if names and names not in sign_fields:
                sign_fields.append(names)
        for node, kind, target in _targets(tree, url):
            low = target.lower()
            if low.startswith(NOT_WEB):
                key = (low.split(":", 1)[0] + ":", "")
                netloc, route, origin = "", key[0], ""
            else:
                parts = urlsplit(target)
                netloc = parts.netloc.lower()
                route = sanitize.route_pattern(parts.path or "/")
                origin = origin_of(target)
                key = (netloc, route)
                if key in visited:
                    continue
            link = links.get(key)
            if link is None:
                link = links[key] = Link(target, origin, netloc, route, kind, dict(node.attrs))
            elif unsafe(link.url, link.attrs) and not unsafe(target, node.attrs):
                # the same pattern seen once with a safe address: that one is checked
                link.url, link.kind, link.attrs = target, kind, dict(node.attrs)
            link.seen_count += 1
            where = {"screen": here[1], "selector": navmap._frame_prefix(node) + navmap.canon(node),
                     "text": mask(_link_text(node))}  # fmt: skip
            if where not in link.seen and len(link.seen) < 10:
                link.seen.append(where)
    return Recording(rec, allowed, visited, screens, sign_in, sign_fields, list(links.values()),
                     known, vocab)  # fmt: skip


# --- the check -----------------------------------------------------------------------------------


def _why_not(link: Link, rec: Recording) -> str | None:
    if link.kind.startswith("form (") and link.kind != "form (GET)":
        return f"a {link.kind[6:-1]} form: only GET is ever sent"
    why = unsafe(link.url, link.attrs if link.kind in ("link", "area") else None)
    if why:
        return why
    if link.origin not in rec.origins:
        return "another site: only the recording's own origins are asked"
    return None


def slug(route: str, taken: set[str]) -> str:
    text = re.sub(r"[{}]", "", route.strip("/")).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60] or "root"
    name, n = text, 2
    while name in taken:
        name, n = f"{text}-{n}", n + 1
    taken.add(name)
    return name


def data_findings(safe_html: str, data) -> list[str]:
    """What the data checks of `labctl.lookfeel` find in a sanitised page: the recording's known
    data values and data-shaped tokens, by shape or kind, never the value."""
    from labctl import lookfeel
    from labctl.behaviour import dom as domlib

    read_ = lookfeel.read_page(safe_html)
    raw = lookfeel._RawText()
    raw.feed(safe_html)
    raw.close()
    found: list[str] = []
    for text in raw.parts:
        text = " ".join(text.split())
        if not text:
            continue
        for hit in data.findings(text, read_.codes, label=text in read_.labels):
            shape = domlib.shape(hit) if hit != "an e-mail address" else hit
            if shape not in found:
                found.append(shape)
    return found


def _classify(row: dict) -> str:
    status = row.get("status")
    if row.get("left_for"):
        return "redirects to another site (not followed)"
    if row.get("ends_on_sign_in"):
        return "sign-in"
    if status is None:
        return "no answer"
    if status in (401, 403):
        return "refused"
    if status in (404, 410):
        return "missing"
    if status >= 500:
        return "error"
    if 200 <= status < 300:
        if row.get("matches"):
            return "recorded screen"
        if row.get("content_type") in HTML_TYPES:
            return "unrecorded page"
        return "not a page"
    return f"status {status}"


def check(
    recording: Path,
    *,
    yes: bool = False,
    max_links: int = 50,
    delay_ms: int = 1000,
    timeout: float = 10.0,
    storage_state: Path | None = None,
    say=print,
    fetcher: SafeFetcher | None = None,
) -> dict:
    """Check the links of a recording (only with `yes=True`; otherwise list the plan) and, when
    checked, write `<recording>/link-check.json` and the sanitised snapshots."""
    from labctl import lookfeel

    rec = read(recording)
    plan = []
    for link in rec.links:
        why = _why_not(link, rec)
        plan.append((link, why))
    doc: dict = {
        "format": FORMAT,
        "recording": rec.path.name,
        "origins": rec.origins,
        "session": (
            "the recording's saved browser state (its cookies sent to the recording's "
            "origins; no value kept)"
            if storage_state
            else "anonymous"
        ),  # fmt: skip
        "limits": {"max_links": max_links, "delay_ms": delay_ms, "timeout_s": timeout},
        "sign_in": sorted({r for _, r in rec.sign_in}),
        "links": [],
    }
    if not yes:
        n = 0
        for link, why in plan:
            shown = _shown(link)
            if why:
                say(f"skip  {link.kind:<11} {shown}: {why}")
            elif n < max_links:
                n += 1
                say(f"would {link.kind:<11} HEAD {shown}")
            else:
                say(f"over  {link.kind:<11} {shown}: over the limit (--max {max_links})")
        say(f"{n} link(s) would be asked for, one every {delay_ms} ms "
            f"({doc['session']}); nothing was requested. Run again with --yes to check them.")  # fmt: skip
        doc["dry_run"] = True
        doc["links"] = [_row(link, "would request" if not why else "skipped", why)
                        for link, why in plan]  # fmt: skip
        return doc
    fetch = fetcher or SafeFetcher(rec.origins, delay_ms=delay_ms, timeout=timeout,
                                   cookies=storage_cookies(storage_state))  # fmt: skip
    data = lookfeel.known_data([rec.path])
    pages_dir = rec.path / PAGES
    shutil.rmtree(pages_dir, ignore_errors=True)
    taken: set[str] = set()
    done = 0
    stopped: dict | None = None
    for link, why in plan:
        if why:
            doc["links"].append(_row(link, "skipped", why))
            continue
        if stopped is not None:
            doc["links"].append(_row(link, "not checked", "the run stopped before it"))
            continue
        if done >= max_links:
            doc["links"].append(_row(link, "not checked", f"over the limit (--max {max_links})"))
            continue
        done += 1
        row = _row(link, "checked", None)
        try:
            _follow(fetch, link, rec, row, data, pages_dir, taken)
        except Throttled as exc:
            stopped = {"status": exc.status, "route": exc.route,
                       "note": f"the application answered {exc.status}: the run stopped there; "
                               "nothing after it was asked for"}  # fmt: skip
            row["state"] = "stopped here"
            row["status"] = exc.status
        except (OSError, http.client.HTTPException) as exc:
            row["status"] = None
            row["error"] = type(exc).__name__
        row["finding"] = _classify(row) if row["state"] == "checked" else "not known"
        doc["links"].append(row)
        say(f"{row['state']:<12} {link.kind:<11} {_shown(link)}: {row['finding']}"
            + (f" ({row['status']})" if row.get("status") else ""))  # fmt: skip
    doc["requests"] = fetch.count
    doc["stopped"] = stopped
    doc["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = rec.path / FILE
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"wrote {out} ({fetch.count} request(s))" + (f"; {stopped['note']}" if stopped else ""))
    return doc


def _shown(link: Link) -> str:
    """The address as printed: the query's values left out (they may be data)."""
    if not link.origin:
        return link.route
    parts = urlsplit(link.url)
    keys = [k for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
    return f"{link.origin}{parts.path or '/'}" + ("?" + "&".join(f"{k}=..." for k in keys)
                                                   if keys else "")  # fmt: skip


def _row(link: Link, state: str, why: str | None) -> dict:
    row = {"route": link.route, "origin": link.origin or None, "kind": link.kind,
           "seen": link.seen, "seen_count": link.seen_count, "state": state}  # fmt: skip
    if why:
        row["reason"] = why
    return row


def _follow(fetch: SafeFetcher, link: Link, rec: Recording, row: dict, data, pages_dir: Path,
            taken: set[str]) -> None:  # fmt: skip
    from labctl.behaviour import dom as domlib

    url, hops = link.url, []
    answer: Answer | None = None
    methods: list[str] = []
    for _ in range(MAX_HOPS + 1):
        answer = fetch.fetch(url, "HEAD")
        methods.append("HEAD")
        if answer.status in (405, 501):
            answer = fetch.fetch(url, "GET")
            methods.append("GET")
        if 300 <= answer.status < 400 and answer.location:
            nxt = urljoin(url, answer.location)
            hop = {"status": answer.status, "to": sanitize.route_pattern(urlsplit(nxt).path or "/")}
            o = origin_of(nxt)
            hop["origin"] = ("same" if o == link.origin else
                             "another origin of the recording" if o in rec.origins else "another site")  # fmt: skip
            hops.append(hop)
            why = unsafe(nxt) or (None if o in rec.origins else "another site")
            if why:
                row["left_for" if why == "another site" else "not_followed"] = (
                    f"`{hop['to']}`: {why}")  # fmt: skip
                break
            url = nxt
            continue
        break
    row["methods"] = methods
    if hops:
        row["redirects"] = hops
    final = (urlsplit(url).netloc.lower(), sanitize.route_pattern(urlsplit(url).path or "/"))
    row["status"] = answer.status if answer else None
    if row.get("left_for") or row.get("not_followed"):
        return
    row["final_route"] = final[1]
    row["content_type"] = answer.content_type or None
    row["ends_on_sign_in"] = final in rec.sign_in
    if row["ends_on_sign_in"] or not (
        200 <= answer.status < 300 and answer.content_type in HTML_TYPES
    ):
        return
    if answer.body is None:
        answer = fetch.fetch(url, "GET")
        methods.append("GET")
        if not (200 <= answer.status < 300 and answer.content_type in HTML_TYPES):
            row["status"] = answer.status
            return
    text = (answer.body or b"").decode("utf-8", errors="replace")
    tree = domlib.parse_html(text)
    names = field_names(tree)
    if names and _has_password(tree) and names in rec.sign_in_fields:
        row["ends_on_sign_in"] = True
    if row["ends_on_sign_in"]:
        return
    if final in rec.screens:
        row["matches"] = {"screen": final[1], "by": "the same address pattern"}
    else:
        same = next((r for (n, r), sets in rec.screens.items() if n == final[0]
                     and len(names) >= 2 and names in sets), None)  # fmt: skip
        if same:
            row["matches"] = {"screen": same, "by": "the same form field names"}
    info = sanitize.page(text, rec.known, rec.vocab)
    del text, tree  # the raw body goes no further
    answer.body = None
    title, heading = info.title or "", (info.headings[0] if info.headings else "")
    for name, value in (("title", title), ("heading", heading)):
        if value and not data.findings(value):
            row[name] = value
        elif value:
            row[f"{name}_refused"] = "the data check found a recorded data value in it"
    if final in rec.screens:
        return  # a recorded screen: its own snapshots are the reference
    found = data_findings(info.html, data)
    if found:
        row["snapshot_refused"] = ("the data check found what the recording shows as data ("
                                   + ", ".join(f"`{f}`" for f in found[:5])
                                   + "): only the title and heading are kept")  # fmt: skip
        return
    name = slug(final[1], taken) + ".html"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / name).write_text(info.html, encoding="utf-8")
    row["snapshot"] = f"{PAGES}/{name}"
    row["snapshot_mode"] = info.mode


# --- for the navigation map ----------------------------------------------------------------------


@dataclass
class Found:
    """The link checks of an application's recordings, for `labctl.navmap`."""

    by_key: dict[tuple[str, str], dict]  # (netloc, route) -> link row
    own: set[str]  # the application's netlocs
    files: dict[str, Path]  # snapshot rel path -> file
    summary: list[dict]

    def lookup(self, target: str | None) -> dict | None:
        if not target:
            return None
        if target.startswith(("http://", "https://")):
            parts = urlsplit(target)
            return self.by_key.get((parts.netloc.lower(), parts.path or "/"))
        if target.startswith("/"):
            return next((self.by_key[(n, target)] for n in self.own if (n, target) in self.by_key),
                        None)  # fmt: skip
        return None


def for_app(root: Path, ctxs: list) -> Found | None:
    """The link checks beside the recordings of `ctxs`, or None when there is none."""
    by_key: dict[tuple[str, str], dict] = {}
    own: set[str] = set()
    files: dict[str, Path] = {}
    summary = []
    for c in ctxs:
        part = Path(c.part.path)
        for s in c.part.raw:
            if origin_of(str(s.get("url") or "")):
                own.add(urlsplit(str(s["url"])).netloc.lower())
        for folder in (part, Path(root) / "evidence" / "_recordings" / part.name):
            path = folder / FILE
            if not path.is_file():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if doc.get("format") != FORMAT or doc.get("dry_run"):
                continue
            summary.append({"recording": doc.get("recording"), "checked_at": doc.get("checked_at"),
                            "session": doc.get("session"), "requests": doc.get("requests"),
                            "stopped": (doc.get("stopped") or {}).get("note"),
                            "sign_in": doc.get("sign_in") or []})  # fmt: skip
            for row in doc.get("links") or []:
                netloc = urlsplit(str(row.get("origin") or "")).netloc.lower()
                key = (netloc, str(row.get("route") or ""))
                by_key.setdefault(key, row)
                snap = row.get("snapshot")
                if snap and (folder / snap).is_file():
                    files[snap] = folder / snap
            break
    if not summary:
        return None
    return Found(by_key, own, files, summary)


def _sign_screen(row: dict, signin: str | None) -> str:
    return f"`{signin}`" if signin else "the sign-in screen"


def answer(row: dict, target: str, first: str | None, signin: str | None) -> tuple[str, str]:
    """(status, answer) of a link the check asked for, in the navigation map's words."""
    finding = row.get("finding")
    redirect = (f"answer `GET {target}` with a redirect (303) to `{first}` as the brief says"
                if first else "answer it as the brief's links out of scope say")  # fmt: skip
    if row.get("state") != "checked":
        why = row.get("reason") or "not checked"
        return "", f"not asked for by the link check ({why})"
    name = row.get("title") or row.get("heading")
    if finding == "unrecorded page":
        what = f"a page *{name}*" if name else "a page"
        return "exists, not recorded", (f"leads to {what} not recorded: out of scope, {redirect}")
    if finding == "recorded screen":
        m = row.get("matches") or {}
        return "recorded screen", (f"leads to a page with {m.get('by', 'the structure')} as the "
                                   f"recorded screen `{m.get('screen')}`: serve that screen")  # fmt: skip
    if finding == "sign-in":
        return "sign-in", (f"leads to the sign-in page ({_sign_screen(row, signin)}) when asked "
                           "without a session: keep the link; when signed out, answer with a "
                           f"redirect to the sign-in screen, else {redirect}")  # fmt: skip
    if finding == "missing":
        return "missing", (f"missing ({row.get('status')}) in the real application: keep the "
                           f"link, answer {row.get('status')}")  # fmt: skip
    if finding == "refused":
        return "refused", (f"the real application refused it ({row.get('status')}): keep the "
                           f"link, answer {row.get('status')}")  # fmt: skip
    if finding == "error":
        return "error", (f"the real application answered {row.get('status')}: keep the link; "
                         f"nothing to build: {redirect}")  # fmt: skip
    if finding == "not a page":
        return "not a page", (f"answers `{row.get('content_type') or 'no content type'}`, not a "
                              f"page: out of scope, {redirect}")  # fmt: skip
    if finding and finding.startswith("redirects to another site"):
        return "external", "redirects to another site: keep the link as it is; nothing is built"
    return "checked", f"the real application answered {row.get('status')}: {redirect}"


def unrecorded_pages(found: Found, app: str) -> list[dict]:
    """Pages of the application the check found and the recording never visited."""
    out = []
    taken: set[str] = set()
    for (netloc, route), row in found.by_key.items():
        if netloc not in found.own or row.get("finding") != "unrecorded page":
            continue
        item = {"path": route, "kind": "reference snapshot of a page not recorded",
                "in_scope": False,
                "linked_from": sorted({w["screen"] for w in row.get("seen") or []})}  # fmt: skip
        for key in ("title", "heading", "status"):
            if row.get(key):
                item[key] = row[key]
        if row.get("snapshot") in found.files:
            item["page"] = f"apps/{app}/{UNRECORDED}/{slug(route, taken)}.html"
            item["_from"] = str(found.files[row["snapshot"]])
        elif row.get("snapshot_refused"):
            item["no_snapshot"] = row["snapshot_refused"]
        item["note"] = ("found by the link check, never visited by the recording: not a recorded "
                        "screen and not in scope; a candidate for a further recording")  # fmt: skip
        out.append(item)
    return out


def copy_pages(root: Path, app: str, pages: list[dict]) -> None:
    """Write the reference snapshots to `apps/<app>/unrecorded/` (and remove stale ones)."""
    folder = Path(root) / "apps" / app / UNRECORDED
    shutil.rmtree(folder, ignore_errors=True)
    for item in pages:
        src = item.pop("_from", None)
        if not src or not item.get("page") or not Path(src).is_file():
            item.pop("page", None)
            continue
        html = Path(src).read_text(encoding="utf-8")
        if sanitize.page_text_mode() == "strict":
            html = sanitize.page(html).html
        dest = Path(root) / item["page"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(html.encode("utf-8"))


def brief_lines(doc: dict) -> list[str]:
    """The brief's lines on the pages that exist but were not recorded."""
    pages = doc.get("unrecorded_pages") or []
    if not pages:
        return []
    out = ["**Pages that exist but were not recorded** (found by `labctl evidence check-links`; "
           "candidates for a further recording). They are not screens of this brief and not in "
           "scope; a link to one is answered as *Links seen, not visited* says. A reference "
           "snapshot (sanitised, data masked) shows what the real page holds:\n"]  # fmt: skip
    for p in pages:
        name = p.get("title") or p.get("heading")
        out.append(f"- `{p['path']}`" + (f' "{name}"' if name else "")
                   + f", linked from {', '.join(f'`{s}`' for s in p['linked_from'])}"
                   + (f": reference snapshot `{p['page']}`" if p.get("page") else
                      f": no snapshot ({p['no_snapshot']})" if p.get("no_snapshot") else "")
                   + ".")  # fmt: skip
    out.append("")
    return out
