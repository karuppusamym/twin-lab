"""The application's map from its own code: every route, menu, tab, dialog and API call.

A recording shows the screens a person visited. The code the browser downloaded while they did
(the HTML, the scripts, the style sheets, the JSON files) describes much more of the application:
its whole route table, the menus and tabs of every screen, the dialogs code opens, every API call
it can make, its interface texts and its lists of values. This module reads that code without
running it (`labctl.jstokens`) and writes `apps/<app>/codemap.json`, a section of the brief
("What the application's code defines") and the routes the recording did not visit into
`navigation.json` (`extend_navigation`), each transition with its source: `recording`, `page`
(a link on a recorded page) or `code`.

What is read, from the HAR (and the files `labctl evidence fetch-code` added beside it):

- the inventory of the application's own files: documents, scripts (which are lazy chunks, which
  were never downloaded), style sheets, JSON configuration, translation bundles, manifests;
  third-party libraries are named with their version where the code shows it, and not analysed;
- route tables: Angular (`provideRouter`, `RouterModule.forRoot/forChild`, route objects with
  `component`, `loadComponent`, `loadChildren`, `redirectTo`, `canActivate`, `children`), React
  Router (route objects with `element`, `Component`, `lazy`, and `<Route path>` compiled to
  `jsx(Route, {path})`), Vue Router (`createRouter({routes})`): path, parent and children,
  guards and where they send a refused visitor, the lazy chunk, the redirect, the title;
- navigation elements in templates: Angular's compiled templates (attribute arrays and element
  instructions), HTML inside scripts, compiled JSX: links (`routerLink`, `href`, `to`), menus,
  navigation bars, tab sets and tabs, tab panels, dialogs, and which component and route each
  belongs to; dialogs opened by code (`dialog.open(X)`, `$('#x').modal('show')`, `showModal()`)
  and the element whose handler opens or closes them where the code says so;
- API calls: `fetch`, `XMLHttpRequest.open`, `axios`, `$.ajax`, `http.get/post/...`, with the
  method and the address template; each is marked *called in the recording* (it matches an
  endpoint of `api/catalogue.json`) or *in code, not called*;
- interface texts: translation bundles, lists of values (status codes and their labels), page
  titles. Every text goes through the same data checks as the page snapshots and the look
  (`sanitize`, the data the recording shows): one that may be data is dropped and counted.

Privacy: no script leaves the evidence, only this map. No value that looks like a secret (a key,
a token, a password, a private key, a connection string) and no host name but the application's
own is kept; `check_text` finds one, `write` refuses to write a map that holds one and the build
package refuses a `codemap.json` that does (`package_check`).
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

from labctl import jstokens as js
from labctl import sanitize

FILE = "codemap.json"
FORMAT = "twinlab-codemap/1"
CODE_DIR = "code"  # beside a recording: the static files `labctl evidence fetch-code` added
CODE_MANIFEST = "manifest.json"
HEADING = "What the application's code defines"
MAX_TEXT = 4_000_000  # a script larger than this is listed, not read
RULE = (
    "Build every route found in code at least as a reachable page, with its navigation (tabs, "
    "menus, sub-pages, dialogs) wired as the code shows. Recorded screens follow their snapshots "
    "exactly. A screen found only in code is built from its route, its title, the text of the "
    "menu entries and links that lead to it, the API calls its code makes and the field rules if "
    "known, and is reported as reconstructed in BUILD-NOTES.md."
)
LIMITS = [
    "What a handler or a service computes beyond the calls and navigations it makes: the code is "
    "read, never run.",
    "Server-side rules, validation done only by the server, and anything the server renders "
    "without the browser asking for code.",
    "Names the build minified: a component or guard is then named by its selector or by the "
    "short name the build gave it.",
    "Code the recording never downloaded (a lazy chunk of a route nobody visited), until "
    "`labctl evidence fetch-code` adds it.",
    "An address or a text built at run time from variables: its variable parts are shown as "
    "`{name}`.",
    "Texts that may be data: dropped (counted), never kept.",
]

TEXT_KINDS = ("document", "script", "style sheet", "json configuration", "i18n bundle", "manifest")
SCRIPT_EXT = {".js", ".mjs", ".cjs"}
DOC_EXT = {".html", ".htm", ".xhtml", ".jsf", ".jsp", ".aspx", ".php", ".faces", ".do"}
MANIFEST_NAMES = {"manifest.json", "manifest.webmanifest", "asset-manifest.json", "ngsw.json",
                  "build-manifest.json", "site.webmanifest", "app-manifest.json"}  # fmt: skip
I18N_PATH = re.compile(
    r"(?:^|/)(?:i18n|l10n|locales?|lang|langs|languages|translations?|messages|nls|strings)/"
    r"|(?:^|[/._-])(?:messages|translations?|strings|i18n|locale|resources?)"
    r"(?:[._-][a-z]{2}(?:[-_][A-Za-z]{2})?)?\.(?:json|properties)$",
    re.IGNORECASE,
)
LANG_FILE = re.compile(r"(?:^|/)([a-z]{2}(?:[-_][A-Za-z]{2})?)\.json$")
# Third-party libraries, by the first words of a file name. Two-word names first.
LIBRARIES = ("jquery-ui", "jquery-migrate", "react-dom", "react-router", "react-router-dom",
             "vue-router", "font-awesome", "core-js", "sweetalert2", "ext-all", "zone.js",
             "jquery", "bootstrap", "popper", "lodash", "underscore", "moment", "react", "vue",
             "angular", "zone", "polyfills", "rxjs", "primefaces", "primeng", "d3", "chart",
             "highcharts", "select2", "datatables", "knockout", "backbone", "require", "axios",
             "tinymce", "ckeditor", "fontawesome", "materialize", "foundation", "modernizr",
             "handlebars", "mustache", "three", "leaflet", "echarts", "swiper", "sweetalert",
             "toastr", "mojarra", "myfaces", "richfaces", "icefaces", "dojo", "kendo", "telerik",
             "webresource", "scriptresource", "vendor", "vendors", "hammer", "htmx",
             "alpine", "stimulus", "turbo", "preact", "svelte", "ember", "jsf", "faces")  # fmt: skip
JSF_LIBRARIES = {"primefaces", "javax.faces", "jakarta.faces", "omnifaces", "bootsfaces",
                 "richfaces", "icefaces", "mojarra", "myfaces", "faces"}  # fmt: skip
# Hosts that only serve public libraries: named; any other host is never kept.
PUBLIC_HOSTS = {"cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "code.jquery.com",
                "ajax.googleapis.com", "stackpath.bootstrapcdn.com", "maxcdn.bootstrapcdn.com",
                "fonts.googleapis.com", "fonts.gstatic.com", "ajax.aspnetcdn.com",
                "cdn.datatables.net", "use.fontawesome.com", "kit.fontawesome.com"}  # fmt: skip
HTML_TAGS = set(
    "a abbr address article aside audio b bdi blockquote body br button canvas caption cite code "
    "col colgroup data datalist dd del details dfn dialog div dl dt em fieldset figcaption figure "
    "footer form h1 h2 h3 h4 h5 h6 header hr i iframe img input ins kbd label legend li link main "
    "map mark menu meter nav noscript object ol optgroup option output p picture pre progress q s "
    "samp section select slot small source span strong sub summary sup table tbody td template "
    "textarea tfoot th thead time tr u ul video svg path g".split()
)
TAG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
ROUTE_KEYS = {"path", "component", "components", "loadComponent", "loadChildren", "redirectTo",
              "pathMatch", "canActivate", "canActivateChild", "canDeactivate", "canMatch",
              "canLoad", "children", "title", "data", "resolve", "outlet", "providers",
              "runGuardsAndResolvers", "matcher", "element", "Component", "lazy", "loader",
              "action", "errorElement", "ErrorBoundary", "HydrateFallback", "index", "id",
              "caseSensitive", "handle", "shouldRevalidate", "hasErrorBoundary", "name", "meta",
              "props", "beforeEnter", "alias", "redirect"}  # fmt: skip
ROUTE_STRONG = {"component", "components", "loadComponent", "loadChildren", "redirectTo",
                "pathMatch", "canActivate", "canActivateChild", "canMatch", "canLoad",
                "children", "element", "Component", "lazy", "loader", "errorElement",
                "beforeEnter", "redirect"}  # fmt: skip
GUARD_KEYS = ("canActivate", "canActivateChild", "canMatch", "canLoad", "canDeactivate",
              "beforeEnter", "loader")  # fmt: skip
DIALOG_RECEIVER = re.compile(r"dialog|modal|overlay|sheet|drawer|popup|popover|lightbox", re.I)
VALUE_KEYS = {"value", "code", "key", "id", "status", "type", "val"}
LABEL_KEYS = {"label", "name", "text", "title", "description", "display", "caption", "desc"}
I18N_NAMES = re.compile(
    r"^(?:messages|translations?|i18n|locales?|resources|strings|labels|texts|dictionary|"
    r"lang|setTranslation|addResourceBundle|addResources|addTranslation|addMessages)$",
    re.IGNORECASE,
)


class CodeMapError(Exception):
    """The map would hold a value that looks like a secret or a foreign host: not written."""

    def __init__(self, places: list[str]) -> None:
        super().__init__("; ".join(places[:10]))
        self.places = places


# --- privacy -------------------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bssh-(?:rsa|ed25519|dss) AAAA[0-9A-Za-z+/]{20,}"), "an SSH key"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
        "a JSON web token",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "a cloud access key"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a cloud API key"),
    (re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"), "a chat token"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "a code-host token"),
    (re.compile(r"\b[sprk]k_(?:live|test)_[0-9A-Za-z]{16,}"), "a payment key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"), "a bearer token"),
    (
        re.compile(
            r"(?i)\b(?:AccountKey|SharedAccessKey|SharedAccessSignature|sig)=[^\s;&\"']{8,}"
        ),
        "a storage key",
    ),
    (
        re.compile(
            r"(?i)\b(?:jdbc:[a-z0-9]+:|mongodb(?:\+srv)?://|postgres(?:ql)?://|mysql://|"
            r"mariadb://|redis://|amqps?://|sqlserver://|oracle:thin:)"
        ),
        "a connection string",
    ),
    (
        re.compile(
            r"(?i)\b(?:server|data source|host|user id|uid|initial catalog|database|password|"
            r"pwd)\s*=\s*[^;\"'\s]+\s*;"
        ),
        "a connection string",
    ),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@\"']+:[^@\s/\"']+@"), "a password in an address"),
    (
        re.compile(
            r"(?i)\b(?:jsessionid|phpsessid|aspsessionid\w*|sessionid|session_id|sid)=[^\s;&\"'/]{6,}"
        ),
        "a session identifier",
    ),
    (
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|client[_-]?secret|secret[_-]?key|access[_-]?"
            r"token|auth[_-]?token|private[_-]?key|password|passwd|pwd|secret|token)[\\\"']*"
            r"\s*[:=]\s*[\\\"']*([^\\\"'\s,;}]{8,})"
        ),
        "a credential",
    ),
]
_RANDOM = re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_-]{32,}={0,2}(?![\w/.-])")
_URL_HOST = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^/\s@\"']*@)?([^/\s:\"'?#)\]}]+)", re.I)
_REL_HOST = re.compile(r"(?<![:\w/])//([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?=[/:\"'\s]|$)", re.I)
_INTERNAL = re.compile(
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|internal|corp|lan|intra|intranet|private|"
    r"localdomain|home\.arpa|ad|priv)\b",
    re.I,
)
_PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
)
_DOMAIN = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|net|org|io|gov|edu|biz|info|co|de|uk|"
    r"fr|nl|ch|at|be|se|no|dk|fi|it|es|pl|eu|us|ca|au|in|jp|cn|ru|br|cloud|app|dev|tech|online|"
    r"site|me|ly|ai)\b",
    re.I,
)
_EXAMPLE_HOST = re.compile(r"(?:^|\.)(?:example\.(?:com|net|org)|example|test|invalid|localhost)$",
                           re.I)  # fmt: skip


def _random(token: str) -> bool:
    """A long token that looks generated (a key, a token), not a word or a path."""
    classes = sum(bool(re.search(p, token)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
    if re.fullmatch(r"[0-9a-f]{32,}", token, re.I):
        return True
    return classes >= 3 and not re.fullmatch(r"[A-Za-z]+(?:[-_][A-Za-z]+)*", token)


def check_text(text: str, own_hosts: set[str] = frozenset()) -> list[str]:
    """What in a text looks like a secret or a host other than the application's own: one line
    per finding, naming the kind and the shape, never the value."""
    own = {h.lower().split(":")[0] for h in own_hosts} | {"localhost", "127.0.0.1"}
    found: list[str] = []
    for rx, what in _SECRET_PATTERNS:
        for m in rx.finditer(text):
            if what == "a credential":
                v = m.group(1)
                if not (re.search(r"\d", v) and re.search(r"[A-Za-z]", v)) and not _random(v):
                    continue  # `"password": "Password"`: a word, not a credential
            found.append(f"{what} (shape `{sanitize.mask(m.group())[:24]}`)")
    for m in _RANDOM.finditer(text):
        if _random(m.group()):
            found.append(f"a long random value (shape `{sanitize.mask(m.group())[:24]}`)")
    hosts = [m.group(1) for m in _URL_HOST.finditer(text)] + [m.group(1) for m in
                                                             _REL_HOST.finditer(text)]  # fmt: skip
    for host in hosts:
        h = host.lower().strip(".")
        if h in own or h in PUBLIC_HOSTS or _EXAMPLE_HOST.search(h):
            continue
        found.append(f"a host name other than the application's own (shape `{sanitize.mask(h)}`)")
    for rx in (_INTERNAL, _PRIVATE_IP):
        for m in rx.finditer(text):
            h = m.group().lower()
            if h not in own and not _EXAMPLE_HOST.search(h):
                found.append(f"an internal host name or address (shape `{sanitize.mask(h)}`)")
    for m in _DOMAIN.finditer(text):
        h = m.group().lower()
        before = text[max(0, m.start() - 1) : m.start()]
        after = text[m.end() : m.end() + 1]
        if before in ("/", ".", "-", "_") or after in (".", "-", "_"):
            continue  # part of a path or a file name (`chunk.co`), not a host on its own
        if h in own or h in PUBLIC_HOSTS or _EXAMPLE_HOST.search(h):
            continue
        found.append(f"a host name other than the application's own (shape `{sanitize.mask(h)}`)")
    return sorted(set(found))


# --- the files the application delivered ---------------------------------------------------------


@dataclass
class Res:
    """One file the browser downloaded (or `fetch-code` added). `text` only for code."""

    url: str
    host: str
    path: str
    query: str
    mime: str
    kind: str
    text: str | None
    size: int
    own: bool
    source: str = "recording"  # or "fetched"

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def kind_of(path: str, mime: str) -> str:
    """document, script, style sheet, json configuration, i18n bundle, manifest or other."""
    ext = os.path.splitext(path)[1].lower()
    name = path.rsplit("/", 1)[-1].lower()
    if name in MANIFEST_NAMES or ext == ".webmanifest":
        return "manifest"
    if ext in SCRIPT_EXT or "javascript" in mime or "ecmascript" in mime:
        return "script"
    if ext == ".css" or mime == "text/css":
        return "style sheet"
    if ext in (".json", ".properties"):
        return "i18n bundle" if I18N_PATH.search(path) or LANG_FILE.search(path) else (
            "json configuration")  # fmt: skip
    if "html" in mime or (ext in DOC_EXT and not mime):
        return "document"
    return "other"


def own_hosts(recordings: list[Path]) -> set[str]:
    """The hosts of the pages the recordings were on: the application's own origin(s)."""
    out: set[str] = set()
    for rec in recordings:
        steps = Path(rec) / "steps.jsonl"
        if not steps.is_file():
            continue
        for line in steps.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                url = str(json.loads(line).get("url") or "")
            except ValueError:
                continue
            if url.startswith(("http://", "https://")):
                out.add(urlsplit(url).netloc.lower())
    return out


def _decode(content: dict, kind: str) -> tuple[str | None, int]:
    text = content.get("text")
    if text is None:
        return None, int(content.get("size") or 0)
    if content.get("encoding") == "base64":
        try:
            raw = base64.b64decode(text)
        except ValueError:
            return None, 0
        return (raw.decode("utf-8", "replace") if kind in TEXT_KINDS else None), len(raw)
    return (str(text) if kind in TEXT_KINDS else None), len(str(text).encode("utf-8"))


def resources(recordings: list[Path], hosts: set[str] | None = None) -> list[Res]:
    """Every file the recordings' browser downloaded (GET, answered 200), and the files fetched
    beside them, in order, once each. API answers (JSON without a `.json` file name) are not
    files: they are the API catalogue's."""
    from labctl import har

    hosts = own_hosts(recordings) if hosts is None else hosts
    out: list[Res] = []
    seen: set[tuple[str, str, str]] = set()
    for rec in recordings:
        try:
            data = har.load(Path(rec)).har
        except (OSError, ValueError):
            data = {}
        for entry in (data.get("log") or {}).get("entries") or []:
            req, resp = entry.get("request") or {}, entry.get("response") or {}
            method = str(req.get("method", "")).upper()
            if method not in ("GET", "POST") or resp.get("status") != 200:
                continue
            url = str(req.get("url") or "")
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https"):
                continue
            content = resp.get("content") or {}
            mime = str(content.get("mimeType") or "").split(";")[0].strip().lower()
            kind = kind_of(parts.path, mime)
            # a page a form post answered (JSF, ASP.NET) is a page too; nothing else a POST is
            if kind == "other" or (method == "POST" and kind != "document"):
                continue
            key = (parts.netloc.lower(), parts.path, parts.query, method,
                   len(str(content.get("text") or "")) if method == "POST" else 0)  # fmt: skip
            if key in seen:
                continue
            seen.add(key)
            text, size = _decode(content, kind)
            out.append(Res(url, parts.netloc.lower(), parts.path or "/", parts.query, mime, kind,
                           text, size, parts.netloc.lower() in hosts))  # fmt: skip
        out += _fetched(Path(rec), hosts, seen)
    return out


def _fetched(rec: Path, hosts: set[str], seen: set) -> list[Res]:
    folder = rec / CODE_DIR
    try:
        doc = json.loads((folder / CODE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for row in doc.get("files") or []:
        url = str(row.get("url") or "")
        parts = urlsplit(url)
        key = (parts.netloc.lower(), parts.path, parts.query)
        path = folder / str(row.get("file") or "")
        if key in seen or not path.is_file() or folder not in path.resolve().parents:
            continue
        seen.add(key)
        mime = str(row.get("content_type") or "").split(";")[0].strip().lower()
        kind = kind_of(parts.path, mime)
        body = path.read_bytes()
        out.append(Res(url, parts.netloc.lower(), parts.path or "/", parts.query, mime, kind,
                       body.decode("utf-8", "replace"), len(body), parts.netloc.lower() in hosts,
                       "fetched"))  # fmt: skip
    return out


def _version_in(text: str) -> str | None:
    m = re.search(r"(?<![\d.])v?(\d+\.\d+(?:\.\d+)?(?:[-.](?:rc|beta|alpha)\.?\d*)?)(?![\d])", text)
    return m.group(1) if m else None


def _lib_version(text: str) -> str | None:
    """A library's version as its own code states it: a banner (`/*! jQuery v3.7.1`), or JSF's
    `jsf.specversion=23000` (specification 2.3)."""
    m = re.search(r"specversion\s*=\s*(\d)(\d)(\d{3})\b", text[:200_000])
    if m:
        return f"specification {int(m.group(1))}.{int(m.group(2))}"
    m = re.search(r"/\*[!*]?[^*]{0,200}?\bv?(\d+\.\d+\.\d+)", text[:3000])
    return m.group(1) if m else None


def library_of(res: Res) -> tuple[str, str | None] | None:
    """(name, version) when a file is a third-party library: by its host (a public library
    host), its JSF resource library (`?ln=primefaces&v=12.0.0`), or its file name."""
    query = dict(parse_qsl(res.query))
    low = res.path.lower()
    m = re.search(r"/(?:javax|jakarta)\.faces\.resource/(.+?)(?:\.xhtml|\.jsf|\.faces)?$", low)
    if m:
        ln = (query.get("ln") or "").lower()
        if ln in JSF_LIBRARIES or not ln and m.group(1) in ("jsf.js", "faces.js"):
            return ln or "javax.faces", query.get("v")
        return None
    if not res.own:
        cdn = re.search(
            r"/(?:ajax/libs|npm|gh)/(@?[\w.-]+(?:/[\w.-]+)?)[@/]v?(\d+\.\d+[\w.-]*)", low
        )
        if cdn:
            return cdn.group(1), cdn.group(2)
        cdn = re.search(r"^/(@?[\w.-]+)@(\d+\.\d+[\w.-]*)/", low)
        if cdn:
            return cdn.group(1), cdn.group(2)
    stem = re.sub(r"\.(?:min\.)?(?:m?js|css)$", "", res.name.lower())
    for lib in LIBRARIES:
        if stem == lib or re.match(rf"{re.escape(lib)}(?:[.\-_~@]|\d|$)", stem):
            if lib in ("vendor", "vendors"):
                return "third-party bundle (libraries not named in its file name)", None
            return lib, _version_in(stem[len(lib) :]) or query.get("v") or query.get("ver")
    if not res.own:
        return "a script or style sheet from another origin", None
    return None


# --- reading one script --------------------------------------------------------------------------


@dataclass
class Script:
    res: Res
    t: js.Tokens
    defs: dict[str, tuple[int, int]] = field(default_factory=dict)
    funcs: dict[str, tuple[int, int]] = field(default_factory=dict)
    exports: dict[str, str] = field(default_factory=dict)
    imports: list[str] = field(default_factory=list)  # static imports (paths)
    dynamic: list[tuple[int, str]] = field(default_factory=list)  # (token, path) of import()
    consts: dict[str, str] = field(default_factory=dict)
    ng: list = field(default_factory=list)  # compiled Angular components: (Component, Obj)
    blocks: set[str] = field(default_factory=set)  # defs that are a class, function or object

    @property
    def path(self) -> str:
        return self.res.path


def _declared(t: js.Tokens, i: int) -> bool:
    """Whether `NAME =` at `i` declares a variable (`var/let/const NAME =`, also after a comma
    in the same declaration), not a class field or an assignment."""
    j = i - 1
    level = t.parent[i]
    seen_decl = False
    while j > level:
        tok = t.toks[j]
        if tok.kind == "punct" and tok.value in (")", "]", "}") and j in t.pairs:
            j = t.pairs[j] - 1
            continue
        if tok.is_p(";"):
            break
        if tok.is_n("var", "let", "const"):
            seen_decl = True
            break
        j -= 1
    return seen_decl


def _depth(t: js.Tokens, i: int) -> int:
    d = 0
    while i >= 0 and d < 400:
        i = t.parent[i]
        d += 1
    return d


def _def_end(t: js.Tokens, i: int) -> int:
    """The end of the definition whose value starts at token `i`."""
    tok = t.at(i)
    if tok is None:
        return i
    if tok.is_n("function", "class") or (
        tok.is_n("async") and t.at(i + 1) and t[i + 1].is_n("function")
    ):
        j = i
        while j < len(t) and not t[j].is_p("{"):
            if t[j].is_p("("):
                j = t.pairs.get(j, j)
            j += 1
        return t.pairs.get(j, j) + 1
    return js.skip_expr(t, i)


def read_script(res: Res) -> Script:
    t = js.tokens(res.text or "")
    sc = Script(res, t)
    best: dict[str, tuple[int, tuple[int, int]]] = {}
    n = len(t)
    for i, tok in enumerate(t.toks):
        if tok.kind != "name":
            continue
        nxt = t.at(i + 1)
        prev = t.at(i - 1)
        span = None
        name = None
        if tok.value in ("function", "class") and nxt is not None and nxt.kind == "name" and not (
                prev is not None and prev.is_p(".")):  # fmt: skip
            name, span = nxt.value, (i, _def_end(t, i))
            if tok.value == "function":
                k = i + 2
                if t.at(k) and t[k].is_p("("):
                    body = t.pairs.get(k, k) + 1
                    if t.at(body) and t[body].is_p("{"):
                        sc.funcs.setdefault(str(nxt.value), (body, t.pairs.get(body, body)))
        elif (nxt is not None and nxt.is_p("=") and not (prev is not None and prev.is_p("."))
              and i + 2 < n and _declared(t, i)):  # fmt: skip
            name, span = tok.value, (i + 2, _def_end(t, i + 2))
            v = t[i + 2]
            if v.kind == "str" and (v.value.startswith(("/", "http://", "https://", "./"))
                                    and " " not in v.value):  # fmt: skip
                sc.consts.setdefault(str(tok.value), v.value)
        elif (nxt is not None and nxt.is_p(":") and i + 2 < n and t[i + 2].kind == "str"
              and prev is not None and prev.is_p("{", ",")):  # fmt: skip
            v = t[i + 2].value
            if v.startswith(("/", "http://", "https://")) and " " not in v and len(v) > 1:
                sc.consts.setdefault(str(tok.value), v)
        if name is not None and span is not None:
            v = t.at(span[0])
            after = t.at(span[0] + 1)
            if v is not None and (v.is_n("class", "function", "async") or v.is_p("{") or (
                    v.kind == "name" and after is not None and after.is_p("=>")) or (
                    v.is_p("(") and t.at(t.pairs.get(span[0], span[0]) + 1) is not None
                    and t[t.pairs.get(span[0], span[0]) + 1].is_p("=>"))):  # fmt: skip
                sc.blocks.add(str(name))
            elif v is not None and v.is_n("Object") and t.at(span[0] + 2) and t[span[0] + 2].is_n(
                    "freeze", "assign"):  # fmt: skip
                sc.blocks.add(str(name))
        if name is not None:
            d = _depth(t, i)
            if name not in best or d < best[name][0]:
                best[name] = (d, span)
        if tok.value == "import" and nxt is not None and nxt.is_p("("):
            arg = t.at(i + 2)
            if arg is not None and arg.kind in ("str", "tmpl"):
                path = arg.value if arg.kind == "str" else arg.value.strings[0]
                if arg.kind == "str" or not arg.value.exprs:
                    sc.dynamic.append((i, path))
        if tok.value in ("import", "export") and (prev is None or not prev.is_p(".")):
            j = i + 1
            if nxt is not None and nxt.is_p("{"):
                close = t.pairs.get(i + 1, i + 1)
                if tok.value == "export":
                    k = i + 2
                    while k < close:
                        local = t[k]
                        if local.kind == "name":
                            if t.at(k + 1) and t[k + 1].is_n("as") and t.at(k + 2):
                                sc.exports[str(t[k + 2].value)] = str(local.value)
                                k += 3
                            else:
                                sc.exports[str(local.value)] = str(local.value)
                                k += 1
                        else:
                            k += 1
                j = close + 1
            if t.at(j) and t[j].is_n("from") and t.at(j + 1) and t[j + 1].kind == "str":
                sc.imports.append(t[j + 1].value)
            elif tok.value == "import" and nxt is not None and nxt.kind == "str":
                sc.imports.append(nxt.value)
            if tok.value == "export" and nxt is not None and nxt.is_n("default") and t.at(i + 2):
                v = t[i + 2]
                sc.exports["default"] = (str(t[i + 3].value) if v.is_n("class", "function")
                                         and t.at(i + 3) and t[i + 3].kind == "name"
                                         else str(v.value) if v.kind == "name" else "")  # fmt: skip
                if v.is_p("{"):
                    sc.defs["(default export)"] = (i + 2, t.pairs.get(i + 2, i + 2) + 1)
                    sc.exports["default"] = "(default export)"
    for name, (_, span) in best.items():
        sc.defs.setdefault(str(name), span)
    return sc


def _strip(path: str) -> str:
    """A path without matrix parameters (`;jsessionid=...`): a session is never part of it."""
    return "/".join(seg.split(";", 1)[0] for seg in path.split("/"))


def _fp(path: str) -> str:
    """A file's path as the map writes it: no matrix parameters, record numbers (3 digits or
    more) masked; a folder name such as `i18n` stays."""
    return sanitize.mask_identifiers(_strip(path))


def _resolve(base: str, ref: str) -> str:
    """A path the code names (`./chunk-x.js`, `/static/a.js`), as a path on the same origin."""
    return _strip(urlsplit(urljoin(base, ref)).path)


# --- text checks ---------------------------------------------------------------------------------


class Checker:
    """Keeps a text only when nothing says it may be data: no shape of data (an e-mail address, a
    date, a number of three digits or more), no word the recording shows as data, not a value a
    page or an API answer held, no secret, no host. If unsure, it is data."""

    def __init__(self, data=None, observed: set[str] = frozenset(), hosts: set[str] = frozenset()):
        from labctl import lookfeel

        self.data = data or lookfeel.Data()
        self.observed = {v.strip().lower() for v in observed if len(v.strip()) >= 3}
        self.hosts = set(hosts)
        self.dropped = 0

    def ok(self, text, *, label: bool = True, names: bool = False) -> bool:
        if isinstance(text, bool):
            return True
        if isinstance(text, (int, float)):
            text = str(text)  # a number of three digits or more may be a record's
        if not isinstance(text, str):
            return False
        s = " ".join(text.split())
        if not s:
            return False
        from labctl import publicdata

        pub = publicdata.current()  # a value the owner declared public may be kept as it is
        if pub is not None and pub.text(s) and not check_text(s, self.hosts):
            return True
        bad = (
            sanitize._mask_data_tokens(s) != s
            or bool(self.data.findings(s, label=label))
            or s.lower() in self.observed
            or bool(check_text(s, self.hosts))
            or (names and sanitize.app_text(s, self.data.names, names=True) != s)
        )
        if not bad:
            from labctl import lookfeel

            if lookfeel.text_kind(s) != "literal":
                return False  # a placeholder of the sanitiser (`Xxxx 9999`): nothing to keep
        if bad:
            self.dropped += 1
        return not bad

    def path_ok(self, path: str) -> bool:
        """An address the code names: kept with record numbers masked, never with a secret or a
        foreign host."""
        if check_text(path, self.hosts):
            self.dropped += 1
            return False
        return True


# --- the analysis --------------------------------------------------------------------------------


def _str_of(t: js.Tokens, start: int, end: int, consts: dict[str, str] | None = None) -> str | None:
    """An address or text template from an expression: strings and templates joined, variable
    parts as `{name}`; None when no part of it is literal."""
    consts = consts or {}
    parts: list[str] = []
    literal = False
    i = start
    while i < end:
        tok = t[i]
        if tok.is_p("+"):
            i += 1
            continue
        stop = i
        while stop < end and not t[stop].is_p("+"):
            if t[stop].kind == "punct" and t[stop].value in ("(", "[", "{"):
                stop = t.pairs.get(stop, stop)
            stop += 1
        if stop - i == 1 and tok.kind == "str":
            parts.append(tok.value)
            literal = True
        elif stop - i == 1 and tok.kind == "tmpl":
            tpl = tok.value
            for k, s in enumerate(tpl.strings):
                parts.append(s)
                literal = literal or bool(s)
                if k < len(tpl.exprs):
                    parts.append(_placeholder(tpl.exprs[k], consts))
        elif tok.is_p("(") and t.pairs.get(i) == stop - 1:
            inner = _str_of(t, i + 1, stop - 1, consts)
            parts.append(inner if inner is not None else "{value}")
            literal = literal or inner is not None
        else:
            parts.append(_placeholder(t.toks[i:stop], consts))
            literal = literal or not parts[-1].startswith("{")
        i = stop
    return "".join(parts) if literal else None


def _placeholder(toks: list[js.Tok], consts: dict[str, str]) -> str:
    names = [str(x.value) for x in toks if x.kind == "name" and x.value not in ("this", "encodeURIComponent",
                                                                                 "String", "encodeURI")]  # fmt: skip
    if len(toks) == 1 and toks[0].kind == "str":
        return toks[0].value
    if names and names[-1] in consts and not any(x.is_p("(") for x in toks):
        return consts[names[-1]]
    name = names[-1] if names else "value"
    return "{" + (name if len(name) >= 2 else "param") + "}"


def _norm_route(path: str) -> str:
    out = []
    for seg in path.split("/"):
        if not seg:
            continue
        if seg in ("*", "**") or re.fullmatch(r":\w+\(\.\*\)\*?", seg) or seg == "(.*)":
            out.append("**")
        elif seg.startswith(":"):
            out.append("{" + re.sub(r"[^\w]", "", seg[1:].split("(")[0]) + "}")
        elif re.fullmatch(r"\[\.{0,3}\w+\]", seg):
            out.append("{" + seg.strip("[].") + "}")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def _join(parent: str | None, path: str) -> str:
    if path.startswith("/") or not parent:
        return _norm_route(path)
    return _norm_route(parent.rstrip("/") + "/" + path)


def _segs(path: str) -> list[str]:
    return [s for s in path.split("?")[0].split("/") if s]


def path_matches(code: str, other: str) -> bool:
    """Whether a route or address template of the code (`/cases/{id}`) is the same as a
    recorded pattern (`/cases/{id}`, `/cases/{id2}`): placeholders match any one part."""
    a, b = _segs(code), _segs(other)
    if a and a[-1] == "**":
        return len(b) >= len(a) - 1 and all(x == y or x.startswith("{") or y.startswith("{")
                                             for x, y in zip(a[:-1], b, strict=False))  # fmt: skip
    if a and a[0].startswith("{") and len(a) > 1 and len(b) >= len(a) - 1:
        # an unresolved base address in front (`{base}/tickets`): compare the rest from the end
        tail = a[1:]
        return all(x == y or x.startswith("{") or y.startswith("{")
                   for x, y in zip(tail[::-1], b[::-1], strict=False))  # fmt: skip
    if len(a) != len(b):
        return False
    return all(x == y or x.startswith("{") or y.startswith("{") for x, y in zip(a, b, strict=True))


def _canon(tag: str, attrs: dict[str, str], classes: list[str]) -> str:
    def clean(v: str) -> str:
        # a row of a server-rendered table: `list:table:3:open` is one element per row
        return re.sub(r":\d+:", ":{row}:", sanitize.mask_identifiers(v))

    if attrs.get("id") and not attrs["id"].startswith("{"):
        return f"{tag}#{clean(attrs['id'])}"
    if attrs.get("name"):
        return f'{tag}[name="{clean(attrs["name"])}"]'
    for a in ("data-testid", "data-test", "data-qa", "data-cy"):
        if attrs.get(a):
            return f'{tag}[{a}="{clean(attrs[a])}"]'
    if classes:
        return f"{tag}.{classes[0]}"
    return tag


DIALOG_TAGS = {"dialog", "mat-dialog-container", "p-dialog", "p-confirmdialog", "ngb-modal",
               "b-modal", "el-dialog", "a-modal", "v-dialog", "modal"}  # fmt: skip
TAB_TAGS = {"mat-tab", "mat-tab-link", "p-tabpanel", "p-tab", "ngb-tab", "tab", "v-tab",
            "el-tab-pane", "a-tab-pane", "b-tab", "md-tab"}  # fmt: skip
TABSET_TAGS = {"mat-tab-group", "mat-tab-nav-bar", "p-tabview", "p-tabs", "ngb-tabset", "v-tabs",
               "el-tabs", "a-tabs", "b-tabs", "tabset", "md-tabs"}  # fmt: skip
MENU_TAGS = {"mat-menu", "p-menu", "p-menubar", "p-tieredmenu", "p-panelmenu", "p-megamenu",
             "p-contextmenu", "el-menu", "a-menu", "v-menu", "menu"}  # fmt: skip
NAVBAR_TAGS = {"nav", "mat-sidenav", "mat-nav-list", "mat-toolbar", "p-breadcrumb", "el-breadcrumb"}


def nav_kind(tag: str, attrs: dict[str, str], classes: list[str], bound: set[str]) -> str | None:
    """What a template element is for navigation: dialog, tab set, tab, tab panel, menu, menu
    entry, navigation bar, link; None for anything else."""
    role = (attrs.get("role") or "").lower()
    low = {k.lower() for k in attrs} | {b.lower() for b in bound}
    cls = set(classes)
    toggle = (attrs.get("data-bs-toggle") or attrs.get("data-toggle") or "").lower()
    if role in ("dialog", "alertdialog") or tag in DIALOG_TAGS or attrs.get("aria-modal") == "true" \
            or "modal" in cls:  # fmt: skip
        return "dialog"
    if role == "tablist" or tag in TABSET_TAGS or cls & {"nav-tabs", "nav-pills", "tabs"}:
        return "tab set"
    if role == "tab" or tag in TAB_TAGS or toggle in ("tab", "pill") or "mat-tab-link" in low \
            or "ngbnavitem" in low:  # fmt: skip
        return "tab"
    if role == "tabpanel" or tag == "mat-tab-nav-panel" or "tab-pane" in cls:
        return "tab panel"
    if role in ("menuitem", "menuitemcheckbox", "menuitemradio") or "mat-menu-item" in low:
        return "menu entry"
    if role in ("menu", "menubar") or tag in MENU_TAGS:
        return "menu"
    if tag in NAVBAR_TAGS or role == "navigation" or cls & {"navbar", "sidebar", "sidenav",
                                                            "breadcrumb", "breadcrumbs", "menu"}:  # fmt: skip
        return "navigation bar"
    if toggle == "modal":
        return "dialog opener"
    if (
        "routerlink" in low
        or tag == "router-link"
        or (tag == "a" and ("href" in low or "to" in low))
    ):
        return "link"
    return None


def _target_of(attrs: dict[str, str]) -> str | None:
    for key in ("routerLink", "routerlink", "to", "href", "data-href", "action"):
        v = attrs.get(key)
        if v is None:
            continue
        v = str(v).strip()
        if not v or v.lower().startswith(("javascript:", "mailto:", "tel:")):
            return None
        if v.startswith("#"):
            return v.split("?")[0]
        return v.split("#")[0].split("?")[0] or "/"
    return None


class _HtmlNav(HTMLParser):
    """Navigation elements of an HTML fragment (a template inside a script, or a document)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[dict] = []
        self.stack: list[tuple[str, dict | None]] = []
        self.ids: dict[str, dict] = {}
        self.texts_by_id: dict[str, str] = {}
        self.open_text: list[tuple[str, list[str]]] = []
        self.forms: list[dict] = []
        self.scripts: list[str] = []
        self.base: str | None = None
        self.title: list[str] | None = None
        self.title_text = ""
        self.handlers: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        classes = a.get("class", "").split()
        bound = {k.strip("[]()") for k in a if k.startswith(("[", ":", "v-bind:"))}
        clean = {k.strip("[]():").replace("v-bind:", ""): v for k, v in a.items()}
        for k, v in a.items():
            if k.startswith("on") and v:
                self.handlers.append(v)
        if tag == "base" and a.get("href"):
            self.base = a["href"]
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        if tag == "link" and a.get("href") and a.get("rel", "").lower() in (
                "modulepreload", "preload", "stylesheet", "manifest", "prefetch"):  # fmt: skip
            self.scripts.append(a["href"])
        if tag == "title":
            self.title = []
        if tag == "form" and (a.get("action") is not None or a.get("method")):
            self.forms.append({"method": (a.get("method") or "GET").upper(),
                               "action": a.get("action") or "", "selector": _canon(tag, a, classes)})  # fmt: skip
        kind = nav_kind(tag, clean, classes, bound)
        script = " ".join(v for k, v in a.items() if k.startswith("on"))
        if re.search(r"jsfcljs\(|__doPostBack\(|PrimeFaces\.ab\(|\.submit\(", script):
            kind = "command link (posts the page's form: the server decides what follows)"
        elif re.search(r"mojarra\.ab\(|jsf\.ajax\.request\(|faces\.ajax\.request\(", script):
            kind = "ajax update (posts part of the form; the server re-renders a part)"
        el = None
        if kind:
            el = {"kind": kind, "tag": tag, "selector": _canon(tag, clean, classes), "text": ""}
            target = _target_of({k: v for k, v in clean.items()})
            if target is not None and kind in ("link", "tab", "menu entry"):
                if bound & {"routerlink", "to", "href"}:
                    target = _bound_path(clean.get("routerLink") or clean.get("routerlink")
                                         or clean.get("to") or clean.get("href") or "")  # fmt: skip
                el["target"] = target
            for ref in ("aria-controls", "data-bs-target", "data-target", "aria-labelledby"):
                if a.get(ref):
                    el[ref] = a[ref].lstrip("#")
            self.found.append(el)
        if a.get("id"):
            self.ids[a["id"]] = el or {"tag": tag}
        if tag not in sanitize.VOID:
            self.stack.append((tag, el))
            self.open_text.append((a.get("id", ""), []))

    def handle_endtag(self, tag):
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                for _ in range(len(self.stack) - k):
                    t, _el = self.stack.pop()
                    ident, parts = self.open_text.pop()
                    if ident:
                        self.texts_by_id[ident] = " ".join(" ".join(parts).split())[:120]
                    if self.open_text:
                        self.open_text[-1][1].extend(parts)
                break
        if tag == "title" and self.title is not None:
            self.title_text = " ".join(" ".join(self.title).split())
            self.title = None

    def handle_data(self, data):
        if self.title is not None:
            self.title.append(data)
        if not data.strip():
            return
        if self.open_text:
            self.open_text[-1][1].append(data)
        for _tag, el in reversed(self.stack):
            if el is not None:
                if not el["text"]:
                    el["text"] = " ".join(data.split())[:120]
                break


def _bound_path(expr: str) -> str:
    """`['/cases', c.id]` -> `/cases/{id}`; `'/x'` -> `/x`; `{name: 'x'}` -> `{route x}`."""
    parts = re.findall(r"'([^']*)'|\"([^\"]*)\"|([A-Za-z_$][\w$.]*)", expr)
    out = []
    for a, b, name in parts:
        lit = a or b
        if lit:
            out.append(lit.strip("/"))
        elif name:
            out.append("{" + name.rsplit(".", 1)[-1] + "}")
    path = "/" + "/".join(p for p in out if p)
    return path if expr.strip().startswith(("[", "'", '"', "`")) else "{" + expr.strip()[:40] + "}"


def html_nav(html: str) -> _HtmlNav:
    p = _HtmlNav()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - a fragment HTMLParser cannot read: what it read stays
        pass
    for el in p.found:  # a dialog's title: the element its aria-labelledby names
        ref = el.get("aria-labelledby")
        if el["kind"] == "dialog" and ref and p.texts_by_id.get(ref):
            el["title"] = p.texts_by_id[ref]
    return p


def _chained(t: js.Tokens, j: int) -> bool:
    """Whether the `)` at `j` ends a call chain on a name: `f(a)(b)(c)`."""
    k = 0
    while j in t.pairs and t[j].is_p(")") and k < 200:
        o = t.pairs[j]
        before = t.at(o - 1)
        if before is None:
            return False
        if before.kind == "name":
            return before.value not in ("if", "for", "while", "switch", "catch", "function")
        j = o - 1
        k += 1
    return False


@dataclass
class Component:
    key: tuple[str, str]  # (file, binding)
    selector: str | None = None
    name: str | None = None
    shell: bool = False  # holds the router outlet: around every routed screen


class Analysis:
    """Everything the code of one application says (before any of it is written out)."""

    def __init__(self, res: list[Res], hosts: set[str], checker: Checker | None = None):
        self.res = res
        self.hosts = hosts
        self.check = checker or Checker(hosts=hosts)
        self.scripts: dict[str, Script] = {}
        self.libraries: dict[tuple[str, str | None], dict] = {}
        self.routes: list[dict] = []
        self.nav: list[dict] = []
        self.dialogs: list[dict] = []
        self.calls: list[dict] = []
        self.enums: list[dict] = []
        self.texts: list[dict] = []
        self.titles: list[dict] = []
        self.components: dict[tuple[str, str], Component] = {}
        self.static_refs: set[str] = set()
        self.dynamic_refs: dict[str, list[str]] = {}  # path -> files referencing it
        self.base = "/"
        self.hash_routing = False
        self.frameworks: set[str] = set()
        self.nav_code: list[dict] = []  # location.href = ..., window.open(...)
        self.shells: set[tuple[str, str]] = set()  # definitions that hold the router outlet
        self.server_pages: list[dict] = []  # pages a server rendered (JSF, JSP, ASP.NET ...)
        self._run()

    # -- driver -----------------------------------------------------------------------------------

    def _lib(self, name: str, version: str | None, how: str, path: str | None = None) -> None:
        key = (name, version)
        row = self.libraries.setdefault(key, {"name": name, "version": version, "found_by": how,
                                              "files": []})  # fmt: skip
        if path and path not in row["files"]:
            row["files"].append(path)

    def _run(self) -> None:
        self.config_consts: dict[str, str] = {}
        for r in self.res:
            if r.kind == "json configuration" and r.own and r.text:
                self._config(r)
        for r in self.res:
            if r.kind == "document" and r.own and r.text:
                self._document(r)
        for r in self.res:
            if r.kind not in ("script", "style sheet", "json configuration", "i18n bundle",
                              "manifest"):  # fmt: skip
                continue
            lib = library_of(r)
            if lib:
                name, version = lib
                version = version or _lib_version(r.text or "")
                self._lib(name, version, "its address" if version or not r.own else "its file name",
                          _fp(r.path) if r.own else None)  # fmt: skip
                continue
            if r.kind == "script" and r.text and len(r.text) <= MAX_TEXT:
                self.scripts[r.path] = read_script(r)
        for sc in self.scripts.values():
            for ref in sc.imports:
                self.static_refs.add(_resolve(sc.path, ref))
            for _, ref in sc.dynamic:
                self.dynamic_refs.setdefault(_resolve(sc.path, ref), []).append(sc.path)
        self._webpack_chunks()
        for sc in self.scripts.values():
            self._banners(sc)
            self._angular_components(sc)
            self._jit_components(sc)
        for sc in self.scripts.values():
            self._routes(sc)
        self._link_routes()
        for sc in self.scripts.values():
            self._angular_templates(sc)
            self._html_strings(sc)
            self._jsx(sc)
            self._code_dialogs(sc)
            self._api(sc)
            self._enums(sc)
            self._i18n_js(sc)
            self._titles(sc)
            self._location(sc)
        for r in self.res:
            if r.kind == "i18n bundle" and r.own and r.text and not library_of(r):
                self._i18n_file(r)

    # -- documents --------------------------------------------------------------------------------

    def _document(self, r: Res) -> None:
        raw = html_nav(r.text or "")  # addresses and structure; its texts may be data
        safe = sanitize.page(r.text or "", self.check.data.names).html
        texts = {el["selector"]: el.get("text", "") for el in html_nav(safe).found}
        p = raw
        for el in p.found:  # the texts as the value-free page has them
            el["text"] = texts.get(el["selector"], "")
        p.title_text = html_nav(safe).title_text
        page = sanitize.route_pattern(r.path)
        server = not any(m in (r.text or "") for m in ("<app-root", 'id="root"', 'id="app"',
                                                       "ng-version", "data-reactroot"))  # fmt: skip
        if server:
            self.frameworks.add("server pages")
            row = {"path": page, "file": _fp(r.path), "framework": "server pages",
                   "written": "a page the server answered"}  # fmt: skip
            title = p.title_text
            if title and self.check.ok(title):
                row["title"] = title
            self.server_pages.append(row)
            for el in p.found:
                tgt = el.get("target")
                if el["kind"] == "link" and tgt and tgt.startswith("/") or el["kind"] != "link":
                    nav = {"kind": el["kind"], "selector": el["selector"], "routes": [page],
                           "file": _fp(r.path), "found_in": "a page the recording downloaded"}  # fmt: skip
                    if el.get("text") and self.check.ok(el["text"]):
                        nav["text"] = el["text"]
                    if tgt:
                        nav["target"] = sanitize.route_pattern(_resolve(r.path, tgt)) if not (
                            tgt.startswith("#")) else tgt  # fmt: skip
                    (self.dialogs if el["kind"] == "dialog" else self.nav).append(nav)
        if p.base:
            self.base = urlsplit(urljoin(r.url, p.base)).path or "/"
        for ref in p.scripts:
            full = urljoin(r.url, ref)
            if urlsplit(full).netloc.lower() == r.host:
                self.static_refs.add(urlsplit(full).path)
        for handler in p.handlers:  # `onclick="location.href='/x.xhtml'"`
            for m in re.finditer(r"(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\(|"
                                 r"window\.open\()\s*['\"]([^'\"]+)['\"]", handler):  # fmt: skip
                self.nav_code.append({"target": _resolve(r.path, m.group(1)), "how": "a page's "
                                      "inline handler", "file": _fp(r.path)})  # fmt: skip
        for form in p.forms:
            action = form["action"] or r.path
            if urlsplit(urljoin(r.url, action)).netloc.lower() not in ("", r.host):
                continue
            self.calls.append({"method": form["method"], "path": _resolve(r.path, action),
                               "how": "an HTML form", "file": _fp(r.path),
                               "selector": form["selector"], "_page": True})  # fmt: skip

    # -- third-party libraries --------------------------------------------------------------------

    def _banners(self, sc: Script) -> None:
        for text in sc.t.banners:
            m = re.search(r"([@A-Za-z][\w@./-]{1,60}?)\s+v?(\d+\.\d+\.\d+(?:-[\w.]+)?)", text)
            if m and m.group(1).lower() not in ("license", "@license", "version", "v", "copyright",
                                                "@version", "release"):  # fmt: skip
                self._lib(m.group(1), m.group(2), "a licence banner in the code",
                          _fp(sc.path))  # fmt: skip
        t = sc.t
        for i, tok in enumerate(t.toks):
            if tok.kind == "str" and tok.value == "ng-version":
                v = t.at(i + 2)
                if (
                    v is not None
                    and v.kind == "str"
                    and re.fullmatch(r"\d+\.\d+\.\d+[\w.-]*", v.value)
                ):
                    self._lib("@angular/core", v.value, "the `ng-version` the code writes",
                              _fp(sc.path))  # fmt: skip
                    self.frameworks.add("angular")
            elif tok.kind == "name" and tok.value == "rendererPackageName":
                self.frameworks.add("react")
                o = t.parent[i]
                if o >= 0 and t[o].is_p("{"):
                    obj = js.obj(t, o)
                    if isinstance(obj.get("version"), str):
                        self._lib(str(obj.get("rendererPackageName") or "react-dom"),
                                  obj["version"], "a version the code declares",
                                  _fp(sc.path))  # fmt: skip
            elif tok.kind == "str" and tok.value in ("__VUE__", "__VUE_OPTIONS_API__"):
                self.frameworks.add("vue")

    # -- components -------------------------------------------------------------------------------

    def _component(self, sc: Script, binding: str) -> Component:
        key = (sc.path, binding)
        return self.components.setdefault(key, Component(key))

    def _binding_at(self, sc: Script, i: int) -> str | None:
        """The innermost definition (`var X = class ...`, `function X`) around token `i`."""
        best, size = None, None
        for name, (s, e) in sc.defs.items():
            if s <= i < e and name in sc.blocks and (size is None or e - s < size):
                best, size = name, e - s
        return best

    def _angular_components(self, sc: Script) -> None:
        """Compiled Angular components: `{type: a, selectors: [["app-x"]], consts: [...],
        template: function(...) {...}}`. Their template functions (and the helper functions
        they use for `@if` blocks) are read in `_angular_templates`."""
        t = sc.t
        for i, tok in enumerate(t.toks):
            if not (tok.kind == "name" and tok.value == "selectors" and t.at(i + 1)
                    and t[i + 1].is_p(":")):  # fmt: skip
                continue
            o = t.parent[i]
            if o < 0 or not t[o].is_p("{"):
                continue
            obj = js.obj(t, o)
            sel = obj.get("selectors")
            if not isinstance(sel, list) or not sel or not isinstance(sel[0], list):
                continue
            selector = next((s for s in sel[0] if isinstance(s, str) and s), None)
            binding = self._binding_at(sc, o)
            if binding is None:
                continue
            comp = self._component(sc, binding)
            comp.selector = selector
            self.frameworks.add("angular")
            if "template" in obj:
                sc.ng.append((comp, obj))

    def _jit_components(self, sc: Script) -> None:
        """Components declared with a template string (`@Component({selector, template})`,
        `{name, template}` objects): their HTML is read like any HTML in a script."""
        t = sc.t
        for i, tok in enumerate(t.toks):
            if not (tok.kind == "name" and tok.value == "selector" and t.at(i + 2)
                    and t[i + 1].is_p(":") and t[i + 2].kind == "str"):  # fmt: skip
                continue
            o = t.parent[i]
            if o < 0 or not t[o].is_p("{"):
                continue
            obj = js.obj(t, o)
            if not isinstance(obj.get("template"), str) and "templateUrl" not in obj:
                continue
            binding = self._binding_at(sc, o)
            # `X = __decorate([Component({...})], X)`: the class is the last argument
            close = t.pairs.get(o)
            k = (close or o) + 1
            while k < len(t) and k < (close or o) + 6 and not t[k].is_p(","):
                k += 1
            if t.at(k + 1) and t[k + 1].kind == "name" and t.at(k + 2) and t[k + 2].is_p(")"):
                binding = str(t[k + 1].value)
            if binding:
                self._component(sc, binding).selector = obj["selector"]

    # -- routes -----------------------------------------------------------------------------------

    def _routes(self, sc: Script) -> None:
        t = sc.t
        found: list[tuple[int, js.Obj]] = []
        for i, tok in enumerate(t.toks):
            if not ((tok.kind == "name" and tok.value == "path") or (tok.kind == "str"
                    and tok.value == "path")):  # fmt: skip
                continue
            nxt, prev = t.at(i + 1), t.at(i - 1)
            if nxt is None or not nxt.is_p(":") or prev is None or not prev.is_p("{", ","):
                continue
            o = t.parent[i]
            if o < 0 or not t[o].is_p("{"):
                continue
            obj = js.obj(t, o)
            path = obj.get("path")
            if not isinstance(path, str) or len(path) > 200 or " " in path:
                continue
            keys = set(obj)
            if not keys & ROUTE_STRONG or len(keys - ROUTE_KEYS) > 2:
                continue
            found.append((o, obj))
        if not found:
            return
        spans = {o: obj for o, obj in found}
        child_span = {o: obj.value_spans.get("children") for o, obj in found}
        parent_of: dict[int, int | None] = {}
        for o, _obj in found:
            best = None
            for p, span in child_span.items():
                if span and span[0] <= o < span[1] and p != o and (best is None or p > best):
                    best = p
            parent_of[o] = best
        rows: dict[int, dict] = {}

        def full(o: int) -> str:
            p = parent_of[o]
            own = str(spans[o].get("path") or "")
            return _join(full(p) if p is not None else None, own)

        call_names = {str(t[k].value) for k in range(max(0, found[0][0] - 12), found[0][0])
                      if t[k].kind == "name"}  # fmt: skip
        for o, obj in found:
            row = {"path": self._based(full(o)), "written": obj.get("path"),
                   "file": _fp(sc.path), "framework": self._framework(obj, call_names)}  # fmt: skip
            if parent_of[o] is not None:
                row["parent"] = self._based(full(parent_of[o]))
            comp = obj.get("component") if "component" in obj else obj.get("Component")
            if (
                isinstance(comp, js.Expr)
                and comp.end - comp.start == 1
                and t[comp.start].kind == "name"
            ):
                row["_component"] = (sc.path, str(t[comp.start].value))
            elif isinstance(obj.get("element"), js.Expr):
                e = obj["element"]
                for k in range(e.start, min(e.end, e.start + 4)):
                    if t[k].kind == "name" and t.at(k + 1) and t[k + 1].is_p(","):
                        row["_component"] = (sc.path, str(t[k].value))
                        break
            for key in ("loadComponent", "loadChildren", "lazy", "component", "components"):
                v = obj.get(key)
                if isinstance(v, js.Expr):
                    chunk = self._chunk_in(sc, v.start, v.end)
                    if chunk:
                        row["lazy_chunk"], row["chunk_export"] = chunk
                        row["lazy_kind"] = key
                        break
            redirect = obj.get("redirectTo", obj.get("redirect"))
            if isinstance(redirect, dict):
                redirect = redirect.get("path") or (f"{{route {redirect.get('name')}}}"
                                                    if redirect.get("name") else None)  # fmt: skip
            if isinstance(redirect, str):
                base = row.get("parent") if not redirect.startswith("/") else None
                row["redirect_to"] = self._based(_join(base, redirect)) if not redirect.startswith(
                    "{") else redirect  # fmt: skip
            elif isinstance(redirect, js.Expr):
                row["redirect_to"] = "decided by code"
            if obj.get("pathMatch"):
                row["path_match"] = obj["pathMatch"]
            title = obj.get("title")
            if title is None and isinstance(obj.get("meta"), dict):
                title = obj["meta"].get("title")
            if isinstance(title, str):
                if self.check.ok(title):
                    row["title"] = title
                    self.titles.append({"text": title, "where": f"route {row['path']}"})
            elif isinstance(title, js.Expr):
                row["title"] = "set by code"
            if obj.get("name") and isinstance(obj["name"], str) and self.check.ok(obj["name"]):
                row["name"] = obj["name"]
            guards = []
            for key in GUARD_KEYS:
                v = obj.get(key)
                items = v if isinstance(v, list) else [v] if v is not None else []
                for item in items:
                    if isinstance(item, js.Expr):
                        guards.append(self._guard(sc, key, item))
            if guards:
                row["guards"] = guards
            if obj.get("index") is True:
                row["index"] = True
            rows[o] = row
        for o in sorted(rows):  # a child route is of its parent's kind
            p = parent_of[o]
            if p is not None and rows[o]["framework"] == "a route table":
                rows[o]["framework"] = rows[p]["framework"]
        kinds = {r["framework"] for r in rows.values()} - {"a route table"}
        if len(kinds) == 1:  # one table, one router: a plain `{path, component}` is of its kind
            for r in rows.values():
                r["framework"] = next(iter(kinds))
        for o, row in rows.items():
            kids = [rows[c]["path"] for c, p in parent_of.items() if p == o]
            if kids:
                row["children"] = kids
        self.routes += list(rows.values())

    def _based(self, path: str) -> str:
        if self.base and self.base != "/" and not path.startswith(self.base.rstrip("/") + "/"):
            return _norm_route(self.base.rstrip("/") + path)
        return path

    def _framework(self, obj: dict, call_names: set[str]) -> str:
        keys = set(obj)
        if keys & {"loadComponent", "loadChildren", "redirectTo", "pathMatch", "canActivate",
                   "canMatch", "canActivateChild", "canLoad", "outlet"}:  # fmt: skip
            return "angular"
        if keys & {"element", "Component", "lazy", "loader", "errorElement", "index"}:
            return "react router"
        if keys & {"beforeEnter", "redirect", "meta", "props", "alias", "components"}:
            return "vue router"
        if call_names & {"createRouter", "VueRouter"}:
            return "vue router"
        if call_names & {
            "createBrowserRouter",
            "createHashRouter",
            "createMemoryRouter",
            "useRoutes",
        }:
            return "react router"
        if "angular" in self.frameworks:
            return "angular"
        return "a route table"

    def _chunk_in(self, sc: Script, start: int, end: int) -> tuple[str, str | None] | None:
        """The chunk a lazy route loads: `import("./chunk.js").then(m => m.X)`, or a webpack
        `n.e(12)` resolved through the chunk map of the runtime."""
        t = sc.t
        for k in range(start, end):
            tok = t[k]
            if tok.is_n("import") and t.at(k + 1) and t[k + 1].is_p("(") and t.at(k + 2) \
                    and t[k + 2].kind == "str":  # fmt: skip
                export = None
                for j in range(k + 3, end - 1):
                    if t[j].is_p(".") and t[j + 1].kind == "name" and t[j + 1].value not in (
                            "then", "catch", "default"):  # fmt: skip
                        export = str(t[j + 1].value)
                    elif t[j].is_p(".") and t[j + 1].is_n("default"):
                        export = "default"
                return _resolve(sc.path, t[k + 2].value), export
            if tok.is_p(".") and t.at(k + 1) and t[k + 1].is_n("e") and t.at(k + 2) \
                    and t[k + 2].is_p("(") and t.at(k + 3) and t[k + 3].kind in ("num", "str"):  # fmt: skip
                cid = str(t[k + 3].value)
                path = self.chunk_map.get(cid)
                return (path or f"(webpack chunk {cid}: file name not found in the code)"), None
        return None

    def _webpack_chunks(self) -> None:
        """webpack's runtime names each chunk's file: `n.u = e => "static/js/" + e + "." +
        {12: "ab12cd"}[e] + ".chunk.js"`. Read that, so `n.e(12)` names a file."""
        self.chunk_map: dict[str, str] = {}
        for sc in self.scripts.values():
            t = sc.t
            for i, tok in enumerate(t.toks):
                if not (tok.is_p(".") and t.at(i + 1) and t[i + 1].is_n("u") and t.at(i + 2)
                        and t[i + 2].is_p("=")):  # fmt: skip
                    continue
                j = i + 3
                param = t.at(j)
                if param is None or param.kind != "name":
                    continue
                if t.at(j + 1) is None or not t[j + 1].is_p("=>"):
                    continue
                end = js.skip_expr(t, j + 2)
                maps: list[js.Obj] = []
                parts: list[object] = []
                k = j + 2
                while k < end:
                    x = t[k]
                    if x.is_p("+") or x.is_p("("):
                        k += 1
                        continue
                    if x.kind == "str":
                        parts.append(x.value)
                    elif x.is_n(str(param.value)):
                        parts.append(None)
                    elif x.is_p("{"):
                        o = js.obj(t, k)
                        maps.append(o)
                        parts.append(o)
                        k = t.pairs.get(k, k) + 1
                        if t.at(k) and t[k].is_p("["):
                            k = t.pairs.get(k, k)
                    k += 1
                ids = set()
                for o in maps:
                    ids |= set(o)
                for cid in ids:
                    name = "".join(cid if p is None else str(p.get(cid, cid)) if isinstance(p, dict)
                                   else str(p) for p in parts)  # fmt: skip
                    self.chunk_map[cid] = _resolve("/", ("/" if not name.startswith("/") else "")
                                                   + name)  # fmt: skip

    def _guard(self, sc: Script, key: str, e: js.Expr) -> dict:
        t = sc.t
        out: dict = {"kind": key}
        name = str(t[e.start].value) if e.end - e.start == 1 and t[e.start].kind == "name" else None
        span = (e.start, e.end)
        if name:
            out["name"] = name if len(name) >= 3 else f"{name} (a name the build shortened)"
            span = sc.defs.get(name, span)
        else:
            out["name"] = "written in the route"
        target = self._redirect_in(sc, *span)
        if target:
            out["sends_refused_visitor_to"] = target
        return out

    def _redirect_in(self, sc: Script, start: int, end: int) -> str | None:
        t = sc.t
        for k in range(start, min(end, len(t) - 2)):
            tok = t[k]
            if tok.kind == "name" and tok.value in ("createUrlTree", "navigate", "navigateByUrl",
                                                   "parseUrl", "redirect", "replace", "push",
                                                   "next", "Navigate") and t[k + 1].is_p("("):  # fmt: skip
                args = js.call_args(t, k + 1)
                if not args:
                    continue
                v = js.value(t, *args[0])
                if isinstance(v, list):
                    path = "/" + "/".join(p.strip("/") for p in (x if isinstance(x, str) else "{param}"
                                                                 for x in v) if p.strip("/"))  # fmt: skip
                    return _norm_route(path)
                if isinstance(v, str) and v.startswith("/"):
                    return _norm_route(v)
                if isinstance(v, dict) and isinstance(v.get("path"), str):
                    return _norm_route(v["path"])
                if isinstance(v, dict) and isinstance(v.get("name"), str):
                    return f"{{route {v['name']}}}"
        return None

    def _link_routes(self) -> None:
        """Each route's component, as a key into the components, also through a lazy chunk's
        exports; the component that holds the router outlet is the shell around them."""
        for row in self.routes:
            chunk = row.get("lazy_chunk")
            if chunk and chunk in self.scripts:
                sc = self.scripts[chunk]
                export = row.get("chunk_export") or "default"
                local = sc.exports.get(export, export)
                row["_component"] = (chunk, local)
            comp = self.components.get(row.get("_component") or ("", ""))
            if comp is not None:
                row["component"] = {k: v for k, v in (("name", comp.name), ("selector", comp.selector))
                                    if v}  # fmt: skip
            elif row.get("_component"):
                b = row["_component"][1]
                row["component"] = {"name": b if len(b) >= 3 else f"{b} (a name the build "
                                                                  "shortened)"}  # fmt: skip
        for (_, binding), comp in self.components.items():
            if comp.name is None and len(binding) >= 3 and not binding.startswith("("):
                comp.name = binding

    def routes_of(self, key: tuple[str, str] | None, file: str | None = None) -> list[str]:
        """The routes a component (or, with no component, any code of a lazy chunk) is shown on."""
        out = [r["path"] for r in self.routes if key and r.get("_component") == key]
        if not out and file:
            out = [r["path"] for r in self.routes if r.get("lazy_chunk") == file]
        if (
            not out
            and key
            and ((self.components.get(key) and self.components[key].shell) or key in self.shells)
        ):
            out = ["(every screen: the shell around the routed screens)"]
        return out

    # -- templates --------------------------------------------------------------------------------

    def _attrs(self, arr) -> tuple[dict[str, str], list[str], set[str]]:
        """Angular's compiled attribute array: name/value pairs, then after a marker number the
        classes (1), styles (2) or bound names (3, 4)."""
        attrs: dict[str, str] = {}
        classes: list[str] = []
        bound: set[str] = set()
        marker = None
        i = 0
        items = list(arr) if isinstance(arr, list) else []
        while i < len(items):
            v = items[i]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                marker = int(v)
                i += 1
                continue
            if not isinstance(v, str):
                i += 1
                continue
            if marker is None:
                nxt = items[i + 1] if i + 1 < len(items) else ""
                attrs[v] = nxt if isinstance(nxt, str) else ""
                i += 2
            elif marker == 1:
                classes.append(v)
                i += 1
            elif marker == 2:
                i += 2
            else:
                bound.add(v)
                i += 1
        return attrs, classes, bound

    def _consts(self, sc: Script, obj: js.Obj) -> list:
        c = obj.get("consts")
        if isinstance(c, list):
            return c
        if isinstance(c, js.Expr):  # `consts: () => [...]` or `function(){ return [...] }`
            t = sc.t
            for k in range(c.start, c.end):
                if t[k].is_p("[") and (t[k - 1].is_p("=>") or t[k - 1].is_n("return")):
                    v = js.value(t, k, t.pairs.get(k, k) + 1)
                    return v if isinstance(v, list) else []
        return []

    def _angular_templates(self, sc: Script) -> None:
        t = sc.t
        for comp, obj in sc.ng:
            consts = self._consts(sc, obj)
            tpl = obj.get("template")
            if not isinstance(tpl, js.Expr):
                continue
            spans = [(tpl.start, tpl.end, None)]
            seen = set()
            k = 0
            while k < len(spans):  # the helper functions of `@if`/`@for` blocks, recursively
                s, e, _ = spans[k]
                for j in range(s, e):
                    tok = t[j]
                    if tok.kind == "name" and tok.value in sc.funcs and tok.value not in seen \
                            and t.at(j - 1) and t[j - 1].is_p(","):  # fmt: skip
                        seen.add(tok.value)
                        b0, b1 = sc.funcs[tok.value]
                        spans.append((b0, b1, str(tok.value)))
                k += 1
            elements: list[dict] = []
            listeners: list[dict] = []
            conditions: dict[str, str] = {}  # helper function -> the name its block depends on
            blocks: dict[str, int] = {}  # helper function -> its block index
            for s, e, fn in spans:
                current = None
                for j in range(s, e):
                    tok = t[j]
                    # a call `t(0, "a", 3)`, or one chained on the last (`t(...)(1, "b", 4)`)
                    if not (t.at(j + 1) and t[j + 1].is_p("(")):
                        continue
                    if not (tok.kind == "name" or _chained(t, j)):
                        continue
                    if tok.kind == "name" and tok.value in ("function", "if", "for", "while",
                                                            "switch", "catch", "return"):  # fmt: skip
                        continue
                    args = js.call_args(t, j + 1)
                    first_s = t[args[0][0]] if args and args[0][1] > args[0][0] else None
                    is_fn = len(args) >= 2 and (t[args[1][0]].is_n("function") or any(
                        t[x].is_p("=>") for x in range(args[1][0], min(args[1][1], args[1][0] + 8))))  # fmt: skip
                    if (
                        len(args) >= 2
                        and args[0][1] - args[0][0] == 1
                        and t[args[0][0]].kind == "num"
                    ):
                        a1 = args[1]
                        second = t[a1[0]] if a1[1] - a1[0] == 1 else None
                        if second is not None and second.kind == "str" and TAG.match(second.value) \
                                and (second.value in HTML_TAGS or "-" in second.value):  # fmt: skip
                            ci = None
                            if len(args) >= 3 and t[args[2][0]].kind == "num":
                                ci = int(t[args[2][0]].value)
                            attrs, classes, bound = self._attrs(
                                consts[ci] if ci is not None and ci < len(consts) else []
                            )
                            el = {"tag": second.value, "attrs": attrs, "classes": classes,
                                  "bound": bound, "text": "", "fn": fn, "at": j}  # fmt: skip
                            elements.append(el)
                            current = el
                            if second.value == "router-outlet":
                                comp.shell = True
                        elif second is not None and second.kind == "str" and len(args) == 2:
                            if current is not None and not current["text"]:
                                current["text"] = " ".join(second.value.split())
                        elif (
                            second is not None
                            and second.kind == "name"
                            and second.value in sc.funcs
                        ):
                            blocks[str(second.value)] = int(t[args[0][0]].value)
                    elif first_s is not None and first_s.kind == "str" and is_fn:
                        event = first_s.value
                        listeners.append({"event": event, "el": current if fn != "host" else None,
                                          "span": args[1], "fn": fn})  # fmt: skip
            for b_fn, idx in blocks.items():
                for s, e, _ in spans:
                    for j in range(s, e - 2):
                        if t[j].is_p("?") and t[j + 1].kind == "num" and int(t[j + 1].value) == idx \
                                and t[j + 2].is_p(":"):  # fmt: skip
                            names = [str(t[x].value) for x in range(max(s, j - 8), j)
                                     if t[x].kind == "name" and t.at(x + 1) and t[x + 1].is_p("(")]  # fmt: skip
                            if names:
                                conditions[b_fn] = names[-1]
            host = obj.get("hostBindings")
            if isinstance(host, js.Expr):
                for j in range(host.start, host.end):
                    if t[j].kind == "str" and t.at(j + 1) and t[j + 1].is_p(",") and t.at(j - 1) \
                            and t[j - 1].is_p("("):  # fmt: skip
                        end = js.skip_expr(t, j + 2)
                        listeners.append({"event": t[j].value, "el": None, "span": (j + 2, end),
                                          "fn": "host"})  # fmt: skip
            self._bind_router_links(sc, spans, elements)
            routes = self.routes_of(comp.key) or (["(every screen: the shell around the routed "
                                                   "screens)"] if comp.shell else [])  # fmt: skip
            label = comp.selector or comp.name or comp.key[1]
            for el in elements:
                kind = nav_kind(el["tag"], el["attrs"], el["classes"], el["bound"])
                if not kind:
                    continue
                attrs = el["attrs"]
                row = {"kind": kind, "selector": _canon(el["tag"], attrs, el["classes"]),
                       "component": label, "routes": routes,
                       "file": _fp(sc.path)}  # fmt: skip
                if el.get("id_template"):
                    row["selector"] = f'{el["tag"]}[id^="{el["id_template"].split("{")[0]}"]'
                if el["text"] and self.check.ok(el["text"]):
                    row["text"] = el["text"]
                target = el.get("target") or _target_of(attrs)
                if target and kind in ("link", "tab", "menu entry"):
                    row["target"] = self._target(target, routes)
                if kind == "dialog":
                    ref = attrs.get("aria-labelledby")
                    title_el = next((x for x in elements if x["attrs"].get("id") == ref), None)
                    if title_el and title_el["text"] and self.check.ok(title_el["text"]):
                        row["title"] = title_el["text"]
                    cond = conditions.get(el["fn"] or "")
                    opened, closed = [], []
                    for lis in listeners:
                        sets = self._sets(sc, lis["span"], cond)
                        who = (f"{lis['event']} on `{self._sel(lis['el'])}`" if lis["el"]
                               else f"{lis['event']} (anywhere on the page)")  # fmt: skip
                        if sets is True:
                            opened.append(who)
                        elif sets is False:
                            closed.append(who)
                    row["shown_when"] = f"`{cond}` is true" if cond else "always rendered"
                    row["opened_by"], row["closed_by"] = opened, closed
                    self.dialogs.append(row)
                    continue
                self.nav.append(row)

    def _sel(self, el: dict) -> str:
        return _canon(el["tag"], el["attrs"], el["classes"])

    def _sets(self, sc: Script, span: tuple[int, int], cond: str | None, depth: int = 0):
        """True when a handler sets `cond` to true (`x.set(!0)`, `x = true`), False when to
        false, None otherwise; one level into a method it calls (`r.closeDialog()`)."""
        if not cond:
            return None
        t = sc.t
        for k in range(span[0], min(span[1], len(t) - 3)):
            if (
                t[k].is_n(cond)
                and t[k + 1].is_p(".")
                and t[k + 2].is_n("set")
                and t[k + 3].is_p("(")
            ):
                v = js.value(t, k + 4, js.skip_expr(t, k + 4))
                if isinstance(v, bool):
                    return v
            if t[k].is_n(cond) and t[k + 1].is_p("="):
                v = js.value(t, k + 2, js.skip_expr(t, k + 2))
                if isinstance(v, bool):
                    return v
            if depth == 0 and t[k].is_p(".") and t[k + 1].kind == "name" and t[k + 2].is_p("(") \
                    and str(t[k + 1].value) not in ("set", "emit", "next"):  # fmt: skip
                for m in self._methods(sc, str(t[k + 1].value)):
                    r = self._sets(sc, m, cond, depth + 1)
                    if r is not None:
                        return r
        return None

    def _methods(self, sc: Script, name: str) -> list[tuple[int, int]]:
        """The bodies of every method of that name in the script (the class is not known)."""
        cache = sc.__dict__.setdefault("_method_cache", {})
        if name in cache:
            return cache[name]
        t = sc.t
        out = []
        for k in range(1, len(t) - 3):
            if t[k].is_n(name) and t[k + 1].is_p("(") and t[k - 1].is_p("}", ";", "{"):
                close = t.pairs.get(k + 1)
                if close is not None and t.at(close + 1) and t[close + 1].is_p("{"):
                    out.append((close + 1, t.pairs.get(close + 1, close + 1)))
        cache[name] = out
        return out

    def _bind_router_links(self, sc: Script, spans, elements) -> None:
        """A bound `[routerLink]` or `[id]`: the property instruction `("routerLink", value)`
        in the update block, paired in order with the elements that bind it."""
        t = sc.t
        for prop, key in (("routerLink", "target"), ("id", "id_template")):
            for s, e, fn in spans:
                els = [x for x in elements if x["fn"] == fn and prop in x["bound"]]
                vals = []
                for j in range(s, e - 2):
                    if t[j].kind == "str" and t[j].value == prop and t[j + 1].is_p(",") \
                            and t[j - 1].is_p("("):  # fmt: skip
                        end = js.skip_expr(t, j + 2)
                        vals.append(self._link_value(sc, j + 2, end))
                for el, v in zip(els, vals, strict=False):
                    if v:
                        el[key] = v

    def _link_value(self, sc: Script, start: int, end: int) -> str | None:
        t = sc.t
        v = js.value(t, start, end)
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return _norm_route("/".join(x if isinstance(x, str) else "{param}" for x in v))
        # `pureFunction(12, fn, x)` with `fn = a => ["/cases", a]`
        for k in range(start, end):
            if (
                t[k].kind == "name"
                and str(t[k].value) in sc.defs
                and t.at(k - 1)
                and t[k - 1].is_p(",")
            ):
                ds, de = sc.defs[str(t[k].value)]
                for x in range(ds, de):
                    if t[x].is_p("[") and t[x - 1].is_p("=>"):
                        arr = js.value(t, x, t.pairs.get(x, x) + 1)
                        if isinstance(arr, list):
                            return _norm_route("/".join(a if isinstance(a, str) else "{param}"
                                                        for a in arr))  # fmt: skip
        tpl = _str_of(t, start, end, {})
        return tpl

    def _target(self, target: str, routes: list[str]) -> str:
        if target.startswith(("#", "{")) or re.match(r"^[a-z]+:", target):
            return target
        if not target.startswith("/"):
            base = routes[0] if routes and routes[0].startswith("/") else "/"
            return _join(base, target)
        return _norm_route(target)

    def _html_strings(self, sc: Script) -> None:
        """HTML in strings and template literals: Vue and AngularJS templates, components that
        write their own markup."""
        t = sc.t
        for i, tok in enumerate(t.toks):
            if tok.kind == "str":
                html = tok.value
            elif tok.kind == "tmpl":
                exprs = tok.value.exprs
                html = "".join(s + (_placeholder(exprs[k], {}) if k < len(exprs) else "")
                               for k, s in enumerate(tok.value.strings))  # fmt: skip
                for expr in tok.value.exprs:  # `${'<a href="/x">'}` inside
                    for x in expr:
                        if x.kind == "str" and "<" in x.value:
                            html += x.value
            else:
                continue
            if "<" not in html or not re.search(r"<[a-z][\w-]*[\s>]", html):
                continue
            p = html_nav(html)
            if not p.found and not p.forms:
                continue
            binding = self._binding_at(sc, i)
            key = (sc.path, binding) if binding else None
            comp = self.components.get(key) if key else None
            routes = self.routes_of(key, sc.path)
            label = (comp.selector or comp.name) if comp else (binding if binding and len(binding) >= 3
                                                                else None)  # fmt: skip
            if "router-outlet" in html or "router-view" in html or 'id="outlet"' in html:
                if comp:
                    comp.shell = True
                if key:
                    self.shells.add(key)
                routes = routes or ["(every screen: the shell around the routed screens)"]
            for el in p.found:
                row = {"kind": el["kind"], "selector": el["selector"], "routes": routes,
                       "file": _fp(sc.path)}  # fmt: skip
                if label:
                    row["component"] = label
                if el["kind"] != "dialog" and el.get("text") and "{" not in el["text"]                         and self.check.ok(el["text"]):  # fmt: skip
                    row["text"] = el["text"]
                if el.get("target"):
                    row["target"] = self._target(el["target"], routes)
                if el["kind"] == "dialog":
                    if el.get("title") and self.check.ok(el["title"]):
                        row["title"] = el["title"]
                    row["opened_by"], row["closed_by"] = [], []
                    row["shown_when"] = "opened by code"
                    self.dialogs.append(row)
                elif el["kind"] == "dialog opener":
                    target = el.get("data-bs-target") or el.get("data-target")
                    row["opens"] = f"#{target}" if target else "a dialog"
                    self.nav.append(row)
                else:
                    for ref in ("aria-controls",):
                        if el.get(ref):
                            row["controls"] = f"#{el[ref]}"
                    self.nav.append(row)
            for form in p.forms:
                if form["action"] and not form["action"].startswith(("http", "//", "{")):
                    self.calls.append({"method": form["method"], "path": _resolve(sc.path if not
                                       form["action"].startswith("/") else "/", form["action"]),
                                       "how": "an HTML form in a template",
                                       "file": _fp(sc.path), "_at": i})  # fmt: skip

    def _jsx(self, sc: Script) -> None:
        """Compiled JSX and render functions: `jsx("a", {href})`, `createElement(Link, {to})`,
        `h(RouterLink, {to})`; a `<Route path element>` is a route (read in `_routes`)."""
        t = sc.t
        for i, tok in enumerate(t.toks):
            if not (tok.is_p("(") and t.at(i + 1) and t.at(i + 2) and t[i + 2].is_p(",")
                    and t.at(i + 3) and t[i + 3].is_p("{")):  # fmt: skip
                continue
            first = t[i + 1]
            if first.kind not in ("str", "name"):
                continue
            o = js.obj(t, i + 3)
            props = {k: v for k, v in o.items() if isinstance(v, str)}
            if first.kind == "str":
                if (
                    not TAG.match(first.value)
                    or first.value not in HTML_TAGS | DIALOG_TAGS | TAB_TAGS
                ):
                    continue
                tag = first.value
            else:
                if not ({"to", "href"} & set(o)):
                    continue
                tag = "a"
            attrs = {("class" if k == "className" else k): v for k, v in props.items()}
            for k in ("to", "href"):
                if isinstance(o.get(k), js.Expr):
                    e = o[k]
                    v = _str_of(t, e.start, e.end, sc.consts)
                    if v:
                        attrs[k] = v
            classes = attrs.get("class", "").split()
            kind = nav_kind(tag, attrs, classes, set())
            if kind is None and first.kind == "name" and "to" in attrs:
                kind = "link"
            if not kind:
                continue
            binding = self._binding_at(sc, i)
            key = (sc.path, binding) if binding else None
            routes = self.routes_of(key, sc.path)
            row = {"kind": kind, "selector": _canon(tag, attrs, classes), "routes": routes,
                   "file": _fp(sc.path)}  # fmt: skip
            if binding and len(binding) >= 3:
                row["component"] = binding
            text = o.get("children")
            if isinstance(text, str) and self.check.ok(text):
                row["text"] = text
            target = _target_of(attrs)
            if target and kind in ("link", "tab", "menu entry"):
                row["target"] = self._target(target, routes)
            if kind == "dialog":
                row["opened_by"], row["closed_by"], row["shown_when"] = [], [], "opened by code"
                self.dialogs.append(row)
            else:
                self.nav.append(row)

    def _receiver_selector(self, sc: Script, k: int) -> str | None:
        """For a call at `k` (the `.` before the method): the element it is called on, when the
        code names it: `document.getElementById("x")`, `querySelector("#x")`, `$("#x")`."""
        t = sc.t
        end = t.at(k - 1)
        if end is None or not end.is_p(")") or (k - 1) not in t.pairs:
            return None
        o = t.pairs[k - 1]
        callee, arg = t.at(o - 1), t.at(o + 1)
        if callee is None or arg is None or arg.kind != "str" or o + 2 != k - 1:
            return None
        if callee.is_n("getElementById"):
            return f"#{arg.value}"
        if callee.is_n("querySelector", "$", "jQuery", "getOrCreateInstance"):
            return arg.value
        return None

    def _element_var(self, sc: Script, name: str) -> str | None:
        """`const d = document.getElementById("x")`: the element a variable holds."""
        t = sc.t
        for k in range(len(t) - 5):
            if t[k].is_n(name) and t[k + 1].is_p("="):
                end = js.skip_expr(t, k + 2)
                for j in range(k + 2, end - 2):
                    if t[j].is_n("getElementById") and t[j + 2].kind == "str":
                        return f"#{t[j + 2].value}"
                    if t[j].is_n("querySelector") and t[j + 2].kind == "str":
                        return t[j + 2].value
        return None

    def _handler_owner(self, sc: Script, k: int) -> str | None:
        """The element whose event handler a call at `k` runs in: `el.addEventListener("click",
        () => ...)` or `el.onclick = ...`, with the element named by id or selector."""
        t = sc.t
        p = t.parent[k]
        while p >= 0:
            if t[p].is_p("(") and t.at(p - 1) and t[p - 1].is_n("addEventListener"):
                args = js.call_args(t, p)
                event = t[args[0][0]].value if args and t[args[0][0]].kind == "str" else "event"
                who = self._receiver_selector(sc, p - 2)
                if who is None and t.at(p - 3) and t[p - 3].kind == "name":
                    who = self._element_var(sc, str(t[p - 3].value))
                return f"{event} on `{who}`" if who else f"{event} (an element the code finds)"
            p = t.parent[p]
        return None

    def _code_dialogs(self, sc: Script) -> None:
        t = sc.t
        for k, tok in enumerate(t.toks):
            if tok.kind != "name" or not (t.at(k + 1) and t[k + 1].is_p("(")):
                continue
            how, selector, comp = None, None, None
            if tok.value == "showModal" and t.at(k - 1) and t[k - 1].is_p("."):
                how = "showModal()"
                selector = self._receiver_selector(sc, k - 1)
                if selector is None and t.at(k - 2) and t[k - 2].kind == "name":
                    selector = self._element_var(sc, str(t[k - 2].value))
            elif tok.value == "modal" and t.at(k + 2) and t[k + 2].kind == "str" \
                    and t[k + 2].value == "show":  # fmt: skip
                how = "modal('show')"
                selector = self._receiver_selector(sc, k - 1)
            elif tok.value == "open" and t.at(k - 2) and t[k - 1].is_p(".") \
                    and t[k - 2].kind == "name" and DIALOG_RECEIVER.search(str(t[k - 2].value)):  # fmt: skip
                args = js.call_args(t, k + 1)
                if args and args[0][1] - args[0][0] == 1 and t[args[0][0]].kind == "name":
                    how = f"{t[k - 2].value}.open()"
                    b = str(t[args[0][0]].value)
                    c = self.components.get((sc.path, b))
                    comp = (c.selector or c.name) if c else (b if len(b) >= 3 else None)
            elif tok.value in ("confirm", "alert", "prompt") and not (t.at(k - 1) and t[k - 1].is_p(".")
                                                                     and not t[k - 2].is_n("window")):  # fmt: skip
                close = t.pairs.get(k + 1)
                if (close is not None and t.at(close + 1) and t[close + 1].is_p("{")) or (
                        t.at(k - 1) and t[k - 1].is_n("function")):  # fmt: skip
                    continue  # a method or function of that name, not the browser's
                args = js.call_args(t, k + 1)
                text = js.value(t, *args[0]) if args else None
                binding = self._binding_at(sc, k)
                row = {"kind": f"browser {tok.value}", "file": _fp(sc.path),
                       "routes": self.routes_of((sc.path, binding) if binding else None, sc.path)}  # fmt: skip
                if isinstance(text, str) and self.check.ok(text):
                    row["text"] = text
                owner = self._handler_owner(sc, k)
                if owner:
                    row["opened_by"] = [owner]
                self.dialogs.append(row)
                continue
            if how is None:
                continue
            owner = self._handler_owner(sc, k)
            have = next((d for d in self.dialogs if selector and (d.get("selector", "").endswith(
                selector) or d.get("selector") == selector)), None)  # fmt: skip
            if have is not None:
                if owner and owner not in have["opened_by"]:
                    have["opened_by"].append(owner)
                have["how"] = how
                continue
            binding = self._binding_at(sc, k)
            row = {"kind": "dialog", "selector": selector or "(an element the code finds)",
                   "how": how, "file": _fp(sc.path),
                   "routes": self.routes_of((sc.path, binding) if binding else None, sc.path), "opened_by": [owner] if owner else [],
                   "closed_by": []}  # fmt: skip
            if comp:
                row["component"] = comp
            self.dialogs.append(row)
        for d in self.dialogs:  # a dialog element closed by `.close()` in a handler
            sel = d.get("selector") or ""
            if not sel.startswith(("dialog#", "#")) and "#" not in sel:
                continue
            ident = sel.split("#", 1)[1]
            for k, tok in enumerate(t.toks):
                if tok.is_n("close") and t.at(k - 1) and t[k - 1].is_p(".") and t.at(k + 1) \
                        and t[k + 1].is_p("("):  # fmt: skip
                    who = self._receiver_selector(sc, k - 1) or (
                        self._element_var(sc, str(t[k - 2].value)) if t[k - 2].kind == "name" else None)  # fmt: skip
                    if who and who.lstrip("#") == ident:
                        owner = self._handler_owner(sc, k)
                        if owner and owner not in d["closed_by"]:
                            d["closed_by"].append(owner)

    # -- API calls --------------------------------------------------------------------------------

    def _api(self, sc: Script) -> None:
        t = sc.t
        consts = {**self.config_consts, **sc.consts}
        for k, tok in enumerate(t.toks):
            if tok.kind != "name" or not (t.at(k + 1) and t[k + 1].is_p("(")):
                continue
            prev = t.at(k - 1)
            member = prev is not None and prev.is_p(".", "?.")
            recv = str(t[k - 2].value) if member and t.at(k - 2) and t[k - 2].kind == "name" else ""
            name = str(tok.value)
            args = js.call_args(t, k + 1)
            if not args:
                continue
            method, url_span, how = None, None, None
            if name == "fetch" and (not member or recv in ("window", "globalThis", "self")):
                url_span, how, method = args[0], "fetch", "GET"
                if len(args) > 1:
                    opts = js.value(t, *args[1])
                    if isinstance(opts, dict) and "method" in opts:
                        m = opts["method"]
                        method = m.upper() if isinstance(m, str) else "?"
            elif name == "axios" and not member:
                v = js.value(t, *args[0])
                if isinstance(v, dict):
                    sp = v.value_spans.get("url")
                    url_span = sp
                    m = v.get("method", "get")
                    method = m.upper() if isinstance(m, str) else "?"
                else:
                    url_span, method = args[0], "GET"
                    if len(args) > 1:
                        o = js.value(t, *args[1])
                        if isinstance(o, dict) and isinstance(o.get("method"), str):
                            method = o["method"].upper()
                how = "axios"
            elif name == "ajax" and member:
                v = js.value(t, *args[0])
                o = v if isinstance(v, dict) else (js.value(t, *args[1]) if len(args) > 1 else None)
                if not isinstance(v, dict):
                    url_span = args[0]
                if isinstance(o, dict):
                    url_span = url_span or o.value_spans.get("url")
                    m = o.get("type", o.get("method", "GET"))
                    method = m.upper() if isinstance(m, str) else "?"
                else:
                    method = "GET"
                how = "$.ajax"
            elif member and name in ("get", "post", "put", "patch", "delete", "head", "options",
                                     "getJSON", "del"):  # fmt: skip
                url_span, method = (
                    args[0],
                    {"getJSON": "GET", "del": "DELETE"}.get(name, name.upper()),
                )
                how = f"{recv or 'a client'}.{name}"
            elif member and name in ("request", "open") and len(args) >= 2:
                m = js.value(t, *args[0])
                if isinstance(m, str) and m.upper() in HTTP_METHODS:
                    method, url_span = m.upper(), args[1]
                    how = (
                        "XMLHttpRequest.open" if name == "open" else f"{recv or 'a client'}.request"
                    )
            elif name in ("EventSource", "WebSocket") and prev is not None and prev.is_n("new"):
                url_span, method, how = (
                    args[0],
                    "GET" if name == "EventSource" else "WEBSOCKET",
                    name,
                )
            elif name == "sendBeacon" and member:
                url_span, method, how = args[0], "POST", "navigator.sendBeacon"
            if url_span is None or method is None:
                continue
            url = _str_of(t, url_span[0], url_span[1], consts)
            if not url or not self._url_like(url):
                continue
            self.calls.append({"method": method, "url": url, "how": how,
                               "file": _fp(sc.path), "_at": k, "_sc": sc.path})  # fmt: skip

    @staticmethod
    def _url_like(url: str) -> bool:
        if any(c in url for c in " <>\n") or len(url) > 300:
            return False
        if url.startswith(("/", "./", "../", "http://", "https://", "//", "ws://", "wss://")):
            return True
        if url.startswith("{") and "/" in url:
            return True
        return bool(re.match(r"^[\w.-]+/[\w{}/.-]*", url))

    def _config(self, r: Res) -> None:
        """A JSON configuration: its addresses (a base address for the API) by key."""
        try:
            data = json.loads(r.text or "")
        except ValueError:
            return
        out = self.config_consts

        def walk(v, key=""):
            if isinstance(v, dict):
                for k, x in list(v.items())[:500]:
                    walk(x, str(k))
            elif isinstance(v, str) and key and v.startswith(("/", "http://", "https://")) \
                    and " " not in v:  # fmt: skip
                out.setdefault(key, v)

        walk(data)
        self.config_consts = out

    # -- interface texts --------------------------------------------------------------------------

    def _enums(self, sc: Script) -> None:
        t = sc.t
        for i, tok in enumerate(t.toks):
            if tok.is_p("[") and t.at(i + 1) and t[i + 1].is_p("{"):
                arr = js.arr(t, i)
                self._enum_list(sc, i, arr)
        self._ts_enums(sc)
        for i, tok in enumerate(t.toks):  # Object.freeze({OPEN: "open", ...})
            if tok.is_n("freeze") and t.at(i + 2) and t[i + 2].is_p("{"):
                o = js.obj(t, i + 2)
                if len(o) >= 2 and all(isinstance(v, str) for v in o.values()) and all(
                        re.fullmatch(r"[A-Z][A-Z0-9_]*", k) for k in o):  # fmt: skip
                    self._enum_out(sc, i, [{"value": v, "label": k} for k, v in o.items()])

    def _enum_list(self, sc: Script, i: int, arr) -> None:
        objs = [x for x in arr if isinstance(x, dict)]
        if len(objs) < 2 or len(objs) != len(arr):
            return
        keys = set(objs[0])
        if not 2 <= len(keys) <= 4 or any(set(o) != keys for o in objs):
            return
        if not all(isinstance(v, (str, int, float, bool)) for o in objs for v in o.values()):
            return
        vk = next(
            (k for k in ("value", "code", "key", "id", "status", "type", "val") if k in keys), None
        )
        lk = next((k for k in ("label", "text", "title", "name", "description", "display", "caption",
                               "desc") if k in keys), None)  # fmt: skip
        if not vk or not lk or vk == lk:
            return
        self._enum_out(sc, i, [{"value": o[vk], "label": o[lk]} for o in objs], names=lk == "name")

    def _ts_enums(self, sc: Script) -> None:
        t = sc.t
        groups: dict[tuple[int, str], list[dict]] = {}
        for i in range(len(t) - 8):
            # e[e.Open = 0] = "Open"
            if t[i].kind == "name" and t[i + 1].is_p("[") and t[i + 2].is_n(str(t[i].value)) \
                    and t[i + 3].is_p(".") and t[i + 4].kind == "name" and t[i + 5].is_p("=") \
                    and t[i + 6].kind in ("num", "str") and t[i + 7].is_p("]"):  # fmt: skip
                key = (t.parent[i], str(t[i].value))
                groups.setdefault(key, []).append({"value": js.value(t, i + 6, i + 7),
                                                   "label": str(t[i + 4].value)})  # fmt: skip
            # e.Open = "open" inside `(function(e){ ... })(X || (X = {}))`
            elif t[i].kind == "name" and t[i + 1].is_p(".") and t[i + 2].kind == "name" \
                    and t[i + 3].is_p("=") and t[i + 4].kind == "str" and t[i + 5].is_p(",", ";", "}") \
                    and (t[i - 1].is_p("{", ",", ";")):  # fmt: skip
                p = t.parent[i]
                if p > 0 and t[p].is_p("{") and t.at(p - 1) and t[p - 1].is_p(")") \
                        and t.pairs.get(p - 1) is not None:  # fmt: skip
                    op = t.pairs[p - 1]
                    if t.at(op + 1) and t[op + 1].is_n(str(t[i].value)) and t[op + 2].is_p(")"):
                        key = (p, str(t[i].value))
                        groups.setdefault(key, []).append({"value": t[i + 4].value,
                                                           "label": str(t[i + 2].value)})  # fmt: skip
        for (p, _name), rows in groups.items():
            # A numeric enum is flags and states of the code (`OnPush = 0`), not a list a person
            # chooses from: only string enums are interface values.
            if len(rows) >= 2 and not all(isinstance(r["value"], (int, float)) for r in rows):
                self._enum_out(sc, max(p, 0), rows)

    def _enum_out(self, sc: Script, i: int, rows: list[dict], names: bool = False) -> None:
        t = sc.t
        name = None
        j = i - 1
        while j >= 0 and j > i - 4:
            if t[j].is_p("=") and t.at(j - 1) and t[j - 1].kind == "name":
                name = str(t[j - 1].value)
                break
            j -= 1
        before = self.check.dropped
        kept = [r for r in rows if self.check.ok(r["value"], label=False)
                and self.check.ok(r["label"], names=names)]  # fmt: skip
        dropped = self.check.dropped - before
        if len(kept) * 2 < len(rows):  # most of it looks like data: none of it is kept
            self.enums.append({"name": name if name and len(name) >= 3 else None,
                               "file": _fp(sc.path), "values": [],
                               "dropped": len(rows)})  # fmt: skip
            return
        key = tuple((r["value"], r["label"]) for r in kept)
        if any(tuple((r["value"], r["label"]) for r in e["values"]) == key for e in self.enums):
            return
        row = {"name": name if name and len(name) >= 3 else None,
               "file": _fp(sc.path), "values": kept}  # fmt: skip
        if dropped:
            row["dropped"] = dropped
        self.enums.append(row)

    def _flatten(self, v, prefix: str, out: dict[str, str]) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                self._flatten(x, f"{prefix}.{k}" if prefix else str(k), out)
        elif isinstance(v, str):
            out[prefix] = v

    def _table(self, source: str, language: str | None, flat: dict[str, str]) -> None:
        before = self.check.dropped
        entries = {}
        for k, v in flat.items():
            if not self.check.path_ok(k):
                continue
            if self.check.ok(v):
                entries[k] = v
        row = {"source": source, "entries": entries, "dropped": self.check.dropped - before}
        if language:
            row["language"] = language
        self.texts.append(row)

    def _i18n_file(self, r: Res) -> None:
        flat: dict[str, str] = {}
        if r.path.lower().endswith(".properties"):
            for line in (r.text or "").splitlines():
                m = re.match(r"^\s*([\w.-]+)\s*[=:]\s*(.*)$", line)
                if m and not line.lstrip().startswith(("#", "!")):
                    flat[m.group(1)] = js.cook(m.group(2))
        else:
            try:
                self._flatten(json.loads(r.text or ""), "", flat)
            except ValueError:
                return
        m = LANG_FILE.search(r.path) or re.search(r"[._-]([a-z]{2}(?:[-_][A-Za-z]{2})?)\.(?:json|"
                                                    r"properties)$", r.path)  # fmt: skip
        self._table(f"translation file {_fp(r.path)}",
                    m.group(1) if m else None, flat)  # fmt: skip

    def _i18n_js(self, sc: Script) -> None:
        t = sc.t
        for i, tok in enumerate(t.toks):
            if tok.kind != "name" or not I18N_NAMES.match(str(tok.value)):
                continue
            nxt = t.at(i + 1)
            start = None
            if nxt is not None and nxt.is_p(":", "=") and t.at(i + 2) and t[i + 2].is_p("{"):
                start = i + 2
            elif nxt is not None and nxt.is_p("("):
                for a, b in js.call_args(t, i + 1):
                    if t[a].is_p("{") and t.pairs.get(a) == b - 1:
                        start = a
            if start is None:
                continue
            o = js.obj(t, start)
            flat: dict[str, str] = {}
            self._flatten(o, "", flat)
            if len(flat) < 3 or sum(" " in v or v[:1].isupper() for v in flat.values()) * 2 < len(
                flat
            ):
                continue
            self._table(f"texts in {_fp(sc.path)} (`{tok.value}`)", None, flat)

    def _titles(self, sc: Script) -> None:
        t = sc.t
        for i, tok in enumerate(t.toks):
            text = None
            if tok.is_n("title") and t.at(i - 1) and t[i - 1].is_p(".") and t.at(i - 2) \
                    and t[i - 2].is_n("document") and t.at(i + 1) and t[i + 1].is_p("="):  # fmt: skip
                text = _str_of(t, i + 2, js.skip_expr(t, i + 2))
            elif tok.is_n("setTitle") and t.at(i + 1) and t[i + 1].is_p("("):
                args = js.call_args(t, i + 1)
                text = _str_of(t, *args[0]) if args else None
            if text and "{" not in text and self.check.ok(text):
                self.titles.append({"text": text, "where": _fp(sc.path)})

    def _location(self, sc: Script) -> None:
        """Navigation by code: `location.href = "/x"`, `location.assign(...)`, `window.open(...)`,
        `router.navigate(["/x"])`, `navigate("/x")`, `history.pushState(..., "/x")`."""
        t = sc.t
        for i, tok in enumerate(t.toks):
            target, how = None, None
            if tok.is_n("href") and t.at(i - 2) and t[i - 2].is_n("location") and t.at(i + 1) \
                    and t[i + 1].is_p("="):  # fmt: skip
                target, how = _str_of(t, i + 2, js.skip_expr(t, i + 2), sc.consts), "location.href"
            elif tok.kind == "name" and tok.value in ("navigate", "navigateTo", "redirect")                     and t.at(i + 1) and t[i + 1].is_p("(") and not (t.at(i - 1) and (
                        t[i - 1].is_p(".") or t[i - 1].is_n("function", "async")))                     and not (t.pairs.get(i + 1) and t.at(t.pairs[i + 1] + 1)
                             and t[t.pairs[i + 1] + 1].is_p("{")):  # fmt: skip
                args = js.call_args(t, i + 1)
                target = _str_of(t, *args[0], sc.consts) if args else None
                how = f"{tok.value}()"
            elif tok.kind == "name" and tok.value in ("assign", "replace", "open", "navigate",
                                                      "navigateByUrl", "pushState", "push") \
                    and t.at(i + 1) and t[i + 1].is_p("(") and t.at(i - 1) and t[i - 1].is_p("."):  # fmt: skip
                recv = str(t[i - 2].value) if t.at(i - 2) and t[i - 2].kind == "name" else ""
                if tok.value in ("assign", "replace") and recv != "location":
                    continue
                if tok.value == "open" and recv != "window":
                    continue
                if tok.value == "push" and not re.search(r"router|history", recv, re.I):
                    continue
                args = js.call_args(t, i + 1)
                if not args:
                    continue
                span = args[2] if tok.value == "pushState" and len(args) > 2 else args[0]
                v = js.value(t, *span)
                if isinstance(v, list):
                    target = _norm_route(
                        "/".join(x if isinstance(x, str) else "{param}" for x in v)
                    )
                else:
                    target = _str_of(t, span[0], span[1], sc.consts)
                how = f"{recv + '.' if recv else ''}{tok.value}()"
            if not target or not target.startswith("/") or " " in target:
                continue
            owner = self._handler_owner(sc, i)
            binding = self._binding_at(sc, i)
            key = (sc.path, binding) if binding else None
            row = {"target": _norm_route(target.split("?")[0]), "how": how,
                   "file": _fp(sc.path), "routes": self.routes_of(key, sc.path)}  # fmt: skip
            if owner:
                row["by"] = owner
            self.nav_code.append(row)


# --- the document ----------------------------------------------------------------------------------


def _inventory(an: Analysis) -> list[dict]:
    lazy = set(an.dynamic_refs) | {r.get("lazy_chunk") for r in an.routes if r.get("lazy_chunk")}
    out = []
    listed = set()
    for r in an.res:
        if not r.own and r.kind not in ("script", "style sheet"):
            continue
        if _fp(r.path) in listed and r.own:
            continue
        listed.add(_fp(r.path))
        lib = library_of(r)
        row = {"path": _fp(r.path) if r.own else "(another origin)",
               "kind": r.kind, "bytes": r.size, "source": r.source}  # fmt: skip
        if r.kind == "script":
            row["loaded"] = ("lazily (a chunk the code imports when needed)" if r.path in lazy
                             else "with the page")  # fmt: skip
        if lib:
            row["third_party"] = lib[0] + (f" {lib[1]}" if lib[1] else "")
            row["analysed"] = False
        else:
            row["analysed"] = r.kind in ("script", "document", "i18n bundle", "json configuration")
        out.append(row)
    return out


def _recorded_screens(ctxs) -> list[str]:
    out: list[str] = []
    for c in ctxs:
        for s in c.steps():
            for p in (s.get("screen"), s.get("then")):
                if p and p not in out:
                    out.append(p)
    return out


def _endpoint(call: dict, endpoints: list[dict]) -> str | None:
    for e in endpoints:
        same = call["method"] in ("?", e.get("method")) or (
            call["method"] == "GET" and e.get("method") == "HEAD")  # fmt: skip
        if same and path_matches(call["path"], str(e.get("path") or "")):
            return f"{e.get('method')} {e.get('path')}"
    return None


def _api_path(url: str, hosts: set[str], base: str = "/") -> tuple[str | None, list[str]]:
    """(path, query parameter names); path None for another origin (never kept)."""
    if url.startswith(("ws://", "wss://", "http://", "https://", "//")):
        parts = urlsplit(url if not url.startswith("//") else "http:" + url)
        if parts.netloc.lower() not in hosts:
            return None, []
        path, query = parts.path or "/", parts.query
    else:
        path, _, query = url.partition("?")
        if not path.startswith(("/", "{")):
            path = _resolve(base, path)
    names = []
    for part in query.split("&") if query else []:
        name = part.split("=", 1)[0]
        if name and re.fullmatch(r"[\w.\[\]-]{1,40}", name):
            names.append(name)
    path = re.sub(r"/{2,}", "/", path)
    path = "/".join(sanitize.mask_identifiers(s) if not s.startswith("{") else s
                    for s in path.split("/"))  # fmt: skip
    return path, names


def build(recordings: list[Path], app: str, *, hosts: set[str] | None = None, ctxs=None,
          endpoints: list[dict] | None = None, checker: Checker | None = None,
          whole: bool = True) -> dict:  # fmt: skip
    """The code map of an application from its recordings (and the files fetched beside them)."""
    hosts = own_hosts(recordings) if hosts is None else hosts
    an = Analysis(resources(recordings, hosts), hosts, checker)
    return document(an, app, ctxs=ctxs, endpoints=endpoints, whole=whole)


def document(an: Analysis, app: str, *, ctxs=None, endpoints: list[dict] | None = None,
             whole: bool = True, screens: list[str] | None = None) -> dict:  # fmt: skip
    endpoints = endpoints or []
    screens = screens if screens is not None else _recorded_screens(ctxs or [])
    have = {r.path for r in an.res}
    routes = []
    seen = set()
    pages = {p["path"] for p in an.server_pages}
    extra = list(an.server_pages)
    for n in an.nav:  # a server page's link to a page the recording did not download
        tgt = n.get("target")
        if n.get("found_in") and tgt and tgt.startswith("/") and tgt not in pages \
                and not re.search(r"\.(?:js|css|png|jpe?g|gif|svg|ico|pdf|json)$", tgt):  # fmt: skip
            pages.add(tgt)
            extra.append({"path": tgt, "file": n["file"], "framework": "server pages",
                          "written": "a link on a page"})  # fmt: skip
    for r in [*an.routes, *extra]:
        row = {k: v for k, v in r.items() if not k.startswith("_")}
        key = (
            row["path"],
            json.dumps(row.get("component"), sort_keys=True),
            row.get("redirect_to"),
        )
        if key in seen:
            continue
        seen.add(key)
        hit = next((s for s in screens if path_matches(row["path"], s)
                    and "**" not in row["path"]), None)  # fmt: skip
        if row.get("redirect_to") and not row.get("component") and not row.get("lazy_chunk"):
            row["status"] = "redirect"
        elif hit:
            row["status"] = "recorded"
        elif row.get("written") == "a link on a page":
            # A server page's link to a page nobody opened: only its address and link text are
            # known, nothing of the page. The navigation map answers it as a link seen, not
            # visited; it is not reconstructed.
            row["status"] = "linked from a page, not visited"
        else:
            row["status"] = "only in code"
        if hit:
            row["screen"] = hit
        chunk = row.get("lazy_chunk")
        if chunk:
            row["lazy_chunk"] = _fp(chunk) if not chunk.startswith("(") else chunk
            row["chunk_downloaded"] = chunk in have
        routes.append(row)
    calls = []
    seen_calls = set()
    for c in an.calls:
        path, query = (c["path"], []) if "path" in c else _api_path(c["url"], an.hosts, an.base)
        row = {"method": c["method"], "how": c["how"], "file": c["file"]}
        if path is None:
            row["path"] = "(another origin: not kept)"
        else:
            if not an.check.path_ok(path):
                continue
            row["path"] = path
        if query:
            row["query"] = query
        sc = an.scripts.get(c.get("_sc") or "")
        if sc is not None:
            binding = an._binding_at(sc, c["_at"])
            key = (sc.path, binding) if binding else None
            comp = an.components.get(key) if key else None
            if comp and (comp.selector or comp.name):
                row["component"] = comp.selector or comp.name
            elif binding and len(binding) >= 3:
                row["in"] = binding
            row["routes"] = an.routes_of(key, sc.path)
        if c.get("selector"):
            row["selector"] = c["selector"]
        hit = _endpoint(row, endpoints) if path else None
        row["status"] = "called in the recording" if hit else "in code, not called"
        if hit:
            row["endpoint"] = hit
        k = (row["method"], row["path"], row["file"])
        if k in seen_calls:
            continue
        seen_calls.add(k)
        calls.append(row)
    missing = []
    wanted: dict[str, list[str]] = {}
    for r in an.routes:
        ch = r.get("lazy_chunk")
        if ch and not ch.startswith("("):
            wanted.setdefault(ch, []).append(r["path"])
    for ch in an.dynamic_refs:
        wanted.setdefault(ch, [])
    for ch, rs in wanted.items():
        if ch not in have:
            missing.append({"file": _fp(ch), "routes": sorted(set(rs))})
    nav = _dedupe(an.nav)
    dialogs = _dedupe(an.dialogs)
    titles = _dedupe(an.titles)
    nav_code = _dedupe(an.nav_code)
    limits = list(LIMITS)
    if an.hash_routing:
        limits.append("The application routes after `#`: its screens are `/#/...` addresses.")
    return {
        "format": FORMAT,
        "app": app,
        "rule": RULE,
        "scope": "whole application" if whole else "set by automations or flows",
        "frameworks": sorted(
            an.frameworks
            | {r.get("framework") for r in routes if r.get("framework")} - {"a route table"}
        ),  # fmt: skip
        "base": an.base,
        "files": _inventory(an),
        "third_party": sorted(
            an.libraries.values(), key=lambda x: (x["name"].lower(), str(x["version"]))
        ),
        "routes": routes,
        "navigation": nav,
        "dialogs": dialogs,
        "navigation_by_code": nav_code,
        "api_calls": calls,
        "text_tables": an.texts,
        "enumerations": an.enums,
        "titles": titles,
        "missing_code": missing,
        "dropped_as_data": an.check.dropped,
        "cannot_be_recovered": limits,
    }


def _dedupe(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        k = json.dumps(r, sort_keys=True, default=str)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def dump(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False).replace("\r\n", "\n") + "\n"


def _static_code(path: str) -> bool:
    """A JSON or properties file of the application's code (a translation, a configuration, a
    manifest), whose texts are the interface's own and not data the recording showed."""
    return path.lower().endswith((".json", ".properties", ".webmanifest")) and kind_of(
        path, "") in ("i18n bundle", "json configuration", "manifest")  # fmt: skip


def known_data(recordings: list[Path]):
    """`lookfeel.known_data`, but without the application's own static JSON files: a
    translation file names the interface's words, which are not data for being in it. A word
    that also shows anywhere else as data (a cell, a field, an API answer) stays data."""
    from labctl import har, lookfeel

    data = lookfeel.Data()
    for path in recordings:
        try:
            rec = har.load(path)
        except (OSError, ValueError):
            continue
        entries = [e for e in har.exchanges(rec.har) if not _static_code(e.path)]
        doms = [s.dom.read_text(encoding="utf-8", errors="replace") for s in rec.steps
                if s.dom is not None]  # fmt: skip
        known, _vocab = har.page_knowledge(doms, entries)
        data.names |= known
        texts: list[str] = []
        for html in doms + [e.resp_text for e in entries
                            if e.resp_text and e.resp_mime == "text/html"]:  # fmt: skip
            raw = lookfeel._RawText()
            raw.feed(html)
            raw.close()
            texts += raw.parts
        for e in entries:
            if e.resp_json is not None:
                lookfeel._strings(e.resp_json, texts)
        data.tokens |= lookfeel._data_tokens(texts)
    data.tokens.discard("")
    return data


def _without_code(ctxs: list) -> list:
    """The recording parts without the exchanges of the application's static JSON files."""
    import dataclasses

    out = []
    for c in ctxs:
        part = dataclasses.replace(c.part, exchanges=[e for e in c.part.exchanges
                                                      if not _static_code(e.path)])  # fmt: skip
        out.append(dataclasses.replace(c, part=part))
    return out


def write(root: Path, app: str, ctxs: list, done: dict | None = None, whole: bool = True) -> dict:
    """Write `apps/<app>/codemap.json` from the recordings of `ctxs`. Raises `CodeMapError`
    (and writes nothing) when the map would hold a secret or a foreign host."""
    from labctl import apicatalog

    path = Path(root) / "apps" / app / FILE
    path.unlink(missing_ok=True)
    recordings = [c.part.path for c in ctxs]
    hosts = own_hosts(recordings)
    data = known_data(recordings)
    try:
        plain = _without_code(ctxs)
        observed = apicatalog.observed_values(plain, apicatalog.kept_values(root, app, plain))
    except Exception:  # noqa: BLE001 - without them the other checks still run
        observed = set()
    checker = Checker(data, observed, hosts)
    endpoints = ((done or {}).get("index") or {}).get("endpoints") or []
    doc = build(recordings, app, hosts=hosts, ctxs=ctxs, endpoints=endpoints, checker=checker,
                whole=whole)  # fmt: skip
    text = dump(doc)
    bad = check_text(text, hosts)
    if bad:
        raise CodeMapError(bad)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return doc


def package_check(root: Path, app: str) -> list[str]:
    """What in `apps/<app>/codemap.json` looks like a secret or a foreign host ([] when none).
    The build package refuses a map that holds one."""
    path = Path(root) / "apps" / app / FILE
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    hosts: set[str] = set()
    for rec in (Path(root) / "evidence" / app).glob("*/steps.jsonl"):
        hosts |= own_hosts([rec.parent])
    return [f"{FILE}: {x}" for x in check_text(text, hosts)]


# --- navigation.json ---------------------------------------------------------------------------------


def _screen_for(path: str, screens: list[str]) -> str | None:
    return next((s for s in screens if path_matches(path, s) or path_matches(s, path)), None)


def extend_navigation(nav: dict, code: dict | None) -> dict:
    """Give every transition of the navigation map its source and add what the code shows:
    the links seen on recorded pages (`page`), the routes, links, tabs, menus, redirects and
    navigations of the code (`code`), and a screen for each route only the code has."""
    for m in nav.get("transitions") or []:
        m.setdefault("source", "recording")
    screens = nav.get("screens") or []
    paths = [s["path"] for s in screens]
    have = {(m.get("from"), m.get("to"), m.get("selector")) for m in nav.get("transitions") or []}
    added: list[dict] = []
    whole = (code or {}).get("scope", "whole application") == "whole application"
    code_routes = [r for r in (code or {}).get("routes") or [] if "**" not in r["path"]
                   and r.get("status") != "linked from a page, not visited"]  # fmt: skip
    for s in screens:
        for link in s.get("links_seen_not_visited") or []:
            target = link.get("target")
            if not target or not str(target).startswith("/"):
                continue
            hit = next((r for r in code_routes if path_matches(r["path"], target)), None)
            if hit is not None and link.get("status") in ("out of scope", "undefined"):
                link["status"] = "found in code"
                link["answer"] = (f"the screen of route `{hit['path']}`, reconstructed from the code "
                                  f"(`codemap.json`)" if whole else link["answer"])  # fmt: skip
            key = (s["path"], target, link.get("selector"))
            if key not in have:
                have.add(key)
                added.append({"from": s["path"], "to": target, "by": "link", "selector":
                              link.get("selector"), "text": link.get("text", ""), "steps": [],
                              "source": "page"})  # fmt: skip
    if code:
        for r in code_routes:
            if r.get("status") != "only in code" or _screen_for(r["path"], paths):
                continue
            row = {"path": r["path"], "in_scope": whole, "source": "code", "recorded": False,
                   "kind": "found in code, not recorded",
                   "build": ("reconstructed from the code: its route, title, menu text, the API "
                             "calls of its code and the field rules if known" if whole else
                             "outside the scope set by automations or flows: not built")}  # fmt: skip
            for k in ("title", "component", "guards", "lazy_chunk", "parent", "children"):
                if r.get(k):
                    row[k] = r[k]
            row["elements"] = [{"selector": n["selector"], "label": n.get("text", ""),
                                "type": n["kind"], "used": "found in code, not recorded",
                                **({"target": n["target"]} if n.get("target") else {})}
                               for n in code.get("navigation") or [] if r["path"] in n.get("routes", [])]  # fmt: skip
            calls = [f"{c['method']} {c['path']}" for c in code.get("api_calls") or []
                     if r["path"] in (c.get("routes") or [])]  # fmt: skip
            if calls:
                row["api_calls"] = calls
            row["ways_in"], row["ways_out"] = [], []
            screens.append(row)
            paths.append(r["path"])
        for r in code.get("routes") or []:
            if r.get("redirect_to") and r["redirect_to"].startswith("/"):
                src = r["path"]
                key = (src, r["redirect_to"], None)
                if key not in have:
                    have.add(key)
                    added.append({"from": src, "to": r["redirect_to"], "by": "redirect (route table)",
                                  "selector": None, "text": "", "steps": [], "source": "code"})  # fmt: skip
            for g in r.get("guards") or []:
                to = g.get("sends_refused_visitor_to")
                if to and to.startswith("/") and (r["path"], to, g.get("name")) not in have:
                    have.add((r["path"], to, g.get("name")))
                    added.append({"from": r["path"], "to": to, "by": f"guard {g.get('name')} "
                                  f"({g.get('kind')}) refuses", "selector": None, "text": "",
                                  "steps": [], "source": "code"})  # fmt: skip
        for n in [*(code.get("navigation") or []), *(code.get("navigation_by_code") or [])]:
            target = n.get("target")
            if not target or not str(target).startswith("/") or n.get("found_in"):
                continue  # a link of a downloaded page: the map lists it as a page's link
            froms = [x for x in n.get("routes") or [] if x.startswith("/")] or ["(every screen)"]
            for f in froms:
                sel = n.get("selector") or n.get("by")
                key = (_screen_for(f, paths) or f, target, sel)
                if key in have:
                    continue
                have.add(key)
                added.append({"from": key[0], "to": _screen_for(target, paths) or target,
                              "by": (n.get("kind") or n.get("how") or "code") + " (in code)",
                              "selector": sel, "text": n.get("text", ""), "steps": [],
                              "source": "code"})  # fmt: skip
    nav.setdefault("transitions", []).extend(added)
    for s in screens:
        if s.get("source") == "code":
            s["ways_in"] = [{k: m[k] for k in ("from", "by", "selector", "text", "source") if k in m}
                            for m in nav["transitions"] if m["to"] == s["path"]]  # fmt: skip
            s["ways_out"] = [{k: m[k] for k in ("to", "by", "selector", "text", "source") if k in m}
                             for m in nav["transitions"] if m["from"] == s["path"]]  # fmt: skip
    known = set(paths)
    undefined = set(nav.get("undefined_targets") or [])
    for u in list(undefined):
        target = u.rsplit("-> ", 1)[-1].split(" (")[0].strip()
        if any(path_matches(r["path"], target) for r in code_routes):
            undefined.discard(u)
    for m in added:
        if m["source"] == "code" and m["to"] not in known and not any(
                path_matches(r["path"], m["to"]) for r in code_routes):  # fmt: skip
            undefined.add(f"{m['from']}: {m.get('selector')} -> {m['to']} (in code, no route)")
    nav["undefined_targets"] = sorted(undefined)
    if code:
        nav["code_map"] = f"see `{FILE}`: {len(code.get('routes') or [])} route(s) in code"
    return nav


# --- the brief -----------------------------------------------------------------------------------


def _cell(v) -> str:
    return str(v).replace("|", "\\|") if v not in (None, "") else "-"


def brief_section(app: str, doc: dict | None, refused: list[str] | None = None) -> str:
    if refused:
        return (f"## {HEADING}\n\nNot written: the map read from the application's code held "
                "something that looks like a secret or a host other than the application's own "
                f"({'; '.join(refused[:5])}). Nothing of the code is carried. Build from the "
                "recorded screens only.\n\n")  # fmt: skip
    if not doc:
        return ""
    whole = doc.get("scope") == "whole application"
    files = doc.get("files") or []
    count = {k: sum(1 for f in files if f["kind"] == k and f.get("analysed"))
             for k in ("script", "document", "json configuration", "i18n bundle")}  # fmt: skip
    fetched = sum(1 for f in files if f.get("source") == "fetched")
    out = [f"## {HEADING}\n",
           f"Machine-readable: `apps/{app}/{FILE}`. Read from the code the browser downloaded "
           f"while recording ({count['script']} script(s), {count['document']} page(s), "
           f"{count['json configuration'] + count['i18n bundle']} JSON file(s)"
           + (f"; {fetched} more fetched afterwards with `labctl evidence fetch-code`" if fetched
              else "") + "), without running any of it. No script is carried in this package, "
           "only what was read from it.\n"]  # fmt: skip
    libs = doc.get("third_party") or []
    if libs:
        out.append("Third-party libraries (named, not analysed; use the same ones): "
                   + ", ".join(f"{x['name']}" + (f" {x['version']}" if x.get("version") else "")
                               for x in libs) + ".\n")  # fmt: skip
    if whole:
        out.append(f"**What to build.** {RULE}\n")
    else:
        out.append("**Scope.** Automations or flows set the scope of this brief: the routes below "
                   "that only the code has are listed for reference and are not built; a link to "
                   "one is answered as *Links that leave the scope* says.\n")  # fmt: skip
    routes = doc.get("routes") or []
    if routes:
        out += ["| Route | Recorded | Component | Guard (where a refused visitor goes) | "
                "Code | Title |", "|---|---|---|---|---|---|"]  # fmt: skip
        for r in routes:
            comp = r.get("component") or {}
            comp_t = " ".join(f"`{v}`" for v in (comp.get("selector"), comp.get("name")) if v)
            guards = "; ".join(f"{g['name']}" + (f" -> `{g['sends_refused_visitor_to']}`"
                                                 if g.get("sends_refused_visitor_to") else "")
                               for g in r.get("guards") or [])  # fmt: skip
            status = {"recorded": f"yes (`{r.get('screen')}`)", "only in code": "**no: only in "
                      "code**", "redirect": f"redirect to `{r.get('redirect_to')}`",
                      "linked from a page, not visited": "no: a link on a page leads there "
                      "(see *Links seen, not visited*)"}[r["status"]]  # fmt: skip
            if r.get("lazy_chunk"):
                code = f"lazy `{r['lazy_chunk']}`" + ("" if r.get("chunk_downloaded")
                                                       else " (never downloaded)")  # fmt: skip
            else:
                code = f"`{r.get('file')}`"
            out.append(f"| `{r['path']}` | {status} | {comp_t or '-'} | {_cell(guards)} | {code} | "
                       f"{_cell(r.get('title'))} |")  # fmt: skip
        out.append("")
    nav = doc.get("navigation") or []
    if nav:
        out.append("**Navigation the code defines** (menus, links, tabs; wire each as listed, "
                   "on every route it shows on):\n")  # fmt: skip
        merged: dict[tuple, list[str]] = {}
        for n in nav:
            where = ", ".join(f"`{x}`" if x.startswith("/") else x for x in n.get("routes") or []) \
                or (f"component `{n['component']}`" if n.get("component") else f"`{n['file']}`")  # fmt: skip
            key = (n["kind"], n["selector"], n.get("text", ""), n.get("target"))
            merged.setdefault(key, [])
            if where not in merged[key]:
                merged[key].append(where)
        for (kind, sel, text, target), wheres in merged.items():
            out.append(f"- {kind} `{sel}`" + (f' "{text}"' if text else "")
                       + (f" -> `{target}`" if target else "") + f" on {'; '.join(wheres)}.")  # fmt: skip
        out.append("")
    dialogs = doc.get("dialogs") or []
    if dialogs:
        out.append("**Dialogs** (open and close each as the code does):\n")
        for d in dialogs:
            bits = [f"- {d['kind']}" + (f" `{d['selector']}`" if d.get("selector") else "")
                    + (f' "{d["title"]}"' if d.get("title") else "")
                    + (f' "{d["text"]}"' if d.get("text") else "")]  # fmt: skip
            if d.get("component"):
                bits.append(f"in `{d['component']}`")
            if d.get("shown_when"):
                bits.append(f"shown when {d['shown_when']}" if d["shown_when"].startswith("`")
                            else d["shown_when"])  # fmt: skip
            if d.get("opened_by"):
                bits.append("opened by " + "; ".join(d["opened_by"]))
            if d.get("closed_by"):
                bits.append("closed by " + "; ".join(d["closed_by"]))
            out.append(", ".join(bits) + ".")
        out.append("")
    moves = doc.get("navigation_by_code") or []
    if moves:
        out.append("**Navigation by code** (a handler changes the address):\n")
        for m in moves:
            out.append(f"- `{m['how']}` to `{m['target']}`" + (f", by {m['by']}" if m.get("by") else "")
                       + (f" (on {', '.join(m['routes'])})" if m.get("routes") else "") + ".")  # fmt: skip
        out.append("")
    calls = doc.get("api_calls") or []
    if calls:
        out += ["**API calls in the code.** Build the ones the recording never made too: their "
                "address and method are certain, their bodies are not (keep them to what the "
                "screens need, and say so in BUILD-NOTES.md).\n",
                "| Method | Address | In the recording | Where |", "|---|---|---|---|"]  # fmt: skip
        for c in calls:
            where = ", ".join(f"`{x}`" for x in c.get("routes") or []) or f"`{c['file']}`"
            q = f" (query: {', '.join(c['query'])})" if c.get("query") else ""
            status = (f"yes: `{c['endpoint']}`" if c["status"] == "called in the recording"
                      else "**no: in code, not called**")  # fmt: skip
            out.append(f"| {c['method']} | `{c['path']}`{q} | {status} | {where} |")
        out.append("")
    texts = doc.get("text_tables") or []
    if texts:
        kept = sum(len(x["entries"]) for x in texts)
        dropped = sum(x.get("dropped", 0) for x in texts)
        out.append(f"**Interface texts:** {len(texts)} table(s), {kept} text(s) kept"
                   + (f", {dropped} dropped because they may be data" if dropped else "")
                   + f". Use them as the screens' words; full lists in `{FILE}` (`text_tables`).\n")  # fmt: skip
    enums = [e for e in doc.get("enumerations") or [] if e["values"]]
    if enums:
        out.append("**Lists of values in the code** (offer exactly these):\n")
        for e in enums:
            vals = ", ".join(f'`{v["value"]}` "{v["label"]}"' for v in e["values"][:20])
            out.append(f"- {e.get('name') or 'a list'} (`{e['file']}`): {vals}.")
        out.append("")
    missing = doc.get("missing_code") or []
    if missing:
        out.append("**Code the recording never downloaded:** "
                   + "; ".join(f"`{m['file']}`" + (f" (route {', '.join(f'`{r}`' for r in m['routes'])})"
                                                   if m["routes"] else "") for m in missing)
                   + ". What those routes show beyond their route entry is unknown until the code "
                   "is fetched (`labctl evidence fetch-code <recording> --yes`, then regenerate "
                   "the brief). Build them from what is listed here.\n")  # fmt: skip
    out.append("**What the code cannot tell:** " + " ".join(doc.get("cannot_be_recovered") or [])
               + "\n")  # fmt: skip
    return "\n".join(out) + "\n"
