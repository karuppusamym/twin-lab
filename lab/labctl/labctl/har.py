"""Read a browser recording (HAR plus page snapshots) into an app model.

A recording is a folder `evidence/<app>/<recording-id>/` with `recording.har` (HAR 1.2, content
embedded), optional `dom/<seq>.html` (the page after each step) and optional `steps.jsonl` (see
`labctl.recorder`). A bare `.har` file saved from browser developer tools is read the same way.

What a recording shows, and what it does not:

- Screens: one per address pattern (`/reviews/R-1001` and `/reviews/R-1002` are `/reviews/{id}`).
  Their elements come from page snapshots and from server-rendered pages in the HAR. A single-page
  app's HTML documents are empty shells (JavaScript builds the screen), so without snapshots its
  screens are known only by address and their elements stay unknown. The model says so.
- Routes: method, path pattern, query names, body field names and types, response shapes per
  status, timings. Error responses become observed rules.
- Tables: inferred from response objects, never observed.
- Nothing recorded is kept as a value (`labctl.sanitize`). A recording is never complete: what was
  not exercised is not in it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import parse_qsl, urlsplit

from labctl import sanitize
from labctl.appmodel import (
    AppModel,
    Call,
    Column,
    Element,
    Evidence,
    Route,
    Rule,
    Screen,
    Stack,
    Table,
    Timing,
)

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
ASSET = re.compile(
    r"\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|otf|eot|mp4|webm)$", re.I
)
MAX_BODY = 5 * 1024 * 1024
ACTION_WORDS = {"search", "list", "find", "query", "all", "export", "count", "filter", "lookup"}
ITEM_KEYS = ("items", "data", "results", "rows", "content", "records", "value", "list")
CODE_VALUE = re.compile(r"^[A-Z][A-Z]*(_[A-Z]+)*$")
CODE_FIELD = re.compile(
    r"(status|state|type|kind|code|priority|severity|level|stage|role|mode|category|"
    r"channel|currency|outcome|result)$",
    re.I,
)
NOT_CODE_FIELD = re.compile(
    r"name|email|mail|phone|address|street|city|town|post|zip|title|text|note|comment|descr|"
    r"message|user|author|owner|account|iban|card|token|secret|password|ssn|birth|"
    r"first|last|surname",
    re.I,
)
MAX_CODES = 12
DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FIELD_NAME = re.compile(r"^[A-Za-z_$][\w$.-]{0,63}$")


# --- reading a recording ---------------------------------------------------------------------------


@dataclass
class Step:
    seq: int
    ts: datetime | None
    url: str
    action: str
    selector: str | None
    dom: Path | None
    # Optional browser storage after the step (today's recorder does not write it; see
    # `storage_facts`): {"localStorage": {key: value}, "sessionStorage": {...}, "indexedDB": [...]}
    storage: dict | None = None


@dataclass
class Recording:
    id: str
    har: dict
    digest: str
    steps: list[Step] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def load(path: Path) -> Recording:
    """A recording folder (`recording.har`, `dom/`, `steps.jsonl`) or a bare `.har` file."""
    path = Path(path)
    if path.is_dir():
        hars = [path / "recording.har"] if (path / "recording.har").exists() else []
        hars = hars or sorted(path.glob("*.har"))
        if not hars:
            raise FileNotFoundError(f"{path.name}: no recording.har in the recording folder")
        har_file, folder, rec_id = hars[0], path, path.name
    else:
        har_file, folder, rec_id = path, None, path.stem
    raw = har_file.read_bytes()
    try:
        har = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"{har_file.name} is not a HAR file: {e}") from e
    if not isinstance(har, dict) or "log" not in har:
        raise ValueError(f"{har_file.name} is not a HAR file: no 'log'")
    rec = Recording(
        id=_slug(rec_id),
        har=har,
        digest="sha256:" + hashlib.sha256(raw).hexdigest()[:16],
    )
    if folder is not None and (folder / "steps.jsonl").exists():
        rec.steps = _steps(folder, rec)
    return rec


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-") or "recording"


def _time(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _steps(folder: Path, rec: Recording) -> list[Step]:
    out = []
    root = folder.resolve()
    # errors="replace": a file cut off inside a character loses its last line, not every step
    lines = (folder / "steps.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            dom = None
            if data.get("dom"):
                dom = (folder / data["dom"]).resolve()
                if root not in dom.parents or not dom.is_file():
                    rec.problems.append(f"steps.jsonl line {n}: snapshot {data['dom']} not found")
                    dom = None
            out.append(Step(int(data.get("seq", n)), _time(data.get("ts")), str(data.get("url") or ""),
                            str(data.get("action") or ""), data.get("selector"), dom,
                            data.get("storage") if isinstance(data.get("storage"), dict) else None))  # fmt: skip
        except (ValueError, TypeError, AttributeError):
            rec.problems.append(f"steps.jsonl line {n} is not a step; skipped")
    return out


# --- shapes ----------------------------------------------------------------------------------------


def shape(value: Any) -> Any:
    """The shape of a JSON value: field names and types, never the values."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = _field(str(key))
            out[name] = merge_shapes(out[name], shape(item)) if name in out else shape(item)
        return out
    if isinstance(value, list):
        item: Any = "unknown"
        for x in value[:200]:
            item = shape(x) if item == "unknown" else merge_shapes(item, shape(x))
        return {"[]": item}
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, str):
        if DATE_TIME.match(value):
            return "string(date-time)"
        if DATE.match(value):
            return "string(date)"
        if EMAIL.match(value):
            return "string(email)"
        return "string"
    return "unknown"


def _field(key: str) -> str:
    """A field name, or `{key}` when the key is itself a value (an id, a code with digits)."""
    if not FIELD_NAME.match(key) or sanitize.is_id_segment(key) or "@" in key:
        return "{key}"
    return key


def merge_shapes(a: Any, b: Any) -> Any:
    if a == b:
        return a
    if a == "unknown":
        return b
    if b == "unknown":
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge_shapes(out[k], v) if k in out else v
        return out
    kinds = set()
    for x in (a, b):
        if isinstance(x, dict):
            kinds.add("array" if "[]" in x else "object")
        else:
            kinds.update(str(x).split("|"))
    return "|".join(sorted(kinds))


def type_of(s: Any) -> str:
    if isinstance(s, dict):
        return "array" if "[]" in s else "object"
    return str(s)


SQL_TYPES = {"integer": "INTEGER", "number": "NUMERIC", "boolean": "BOOLEAN",
             "string(date-time)": "TIMESTAMP", "string(date)": "DATE"}  # fmt: skip


def _sql_type(s: Any) -> str:
    kinds = [k for k in type_of(s).split("|") if k != "null"]
    if len(kinds) == 1:
        return SQL_TYPES.get(kinds[0], "TEXT")
    if set(kinds) <= {"integer", "number"} and kinds:
        return "NUMERIC"
    return "TEXT"


# --- entries ---------------------------------------------------------------------------------------


@dataclass
class Exchange:
    """One request and its response, reduced at once: no header that could be a credential, no
    value. Bodies are kept only as parsed JSON in memory, for shapes and code lists."""

    started: datetime | None
    method: str
    host: str
    path: str
    query: list[str]
    request: dict[str, str]  # safe request headers
    req_mime: str
    req_fields: list[str]
    status: int
    resp_mime: str
    resp_json: Any
    resp_text: str | None  # HTML and scripts only
    ms: float
    kind: str  # document | form | api | asset | other
    values: set[str] = field(default_factory=set)  # words of the values sent and received
    resp_headers: dict[str, str] = field(
        default_factory=dict
    )  # Server, X-Powered-By...: products only
    cookies: set[str] = field(default_factory=set)  # cookie NAMES sent or set; never a value


# The second-level labels under a two-letter country code that are not a site of their own
# (`example.co.uk` is the site, not `co.uk`). An approximation of the public suffix list: a host
# it gets wrong is at worst one more host of another site, which `api_hosts` never adds.
GENERIC_SLD = frozenset({"ac", "co", "com", "edu", "gov", "govt", "ltd", "mil", "ne", "net", "nic",
                         "or", "org", "plc", "sch"})  # fmt: skip


def site_of(host: str) -> str | None:
    """The site a host belongs to (`api.shop.example` and `www.shop.example`: `shop.example`), or
    None when it shares none: an address with a port, an IP address, a single label."""
    host = (host or "").lower().strip().rstrip(".")
    if not host or ":" in host or "." not in host or re.fullmatch(r"[\d.]+", host):
        return None
    labels = host.split(".")
    n = 3 if len(labels[-1]) == 2 and labels[-2] in GENERIC_SLD else 2
    return ".".join(labels[-n:]) if len(labels) >= n else None


def api_hosts(app_hosts: set[str], entries: list[Exchange]) -> set[str]:
    """Hosts of the same site as the application's pages that the pages only called an API on,
    and never loaded a page from: the application's own API on a separate origin (pages on
    `www.shop.example` calling `api.shop.example`). A host of another site is never one, nor an
    address with a port (`localhost:8101` beside `localhost:8102` is another application)."""
    sites = {s for h in app_hosts if (s := site_of(h))}
    if not sites:
        return set()
    pages = {e.host for e in entries if e.kind in ("document", "form")}
    return {e.host for e in entries
            if e.kind == "api" and e.host not in app_hosts and e.host not in pages
            and site_of(e.host) in sites}  # fmt: skip


def referer_path(referer: str, request_host: str) -> str:
    """The path of the page a Referer names, or "" when it does not name one. A request to
    another origin carries only the page's origin by the browsers' default policy
    (`strict-origin-when-cross-origin`): its `/` is not the page, so it says nothing."""
    ref = urlsplit(referer or "")
    if not ref.netloc:
        return ""
    if ref.netloc.lower() != (request_host or "").lower() and ref.path in ("", "/") \
            and not ref.query:  # fmt: skip
        return ""
    return ref.path


def _text(content: dict) -> str | None:
    text = content.get("text")
    if text is None:
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except ValueError:
            return None
    return text if len(text) <= MAX_BODY else None


def _json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _request_fields(post: dict | None) -> tuple[str, list[str]]:
    if not post:
        return "", []
    mime = str(post.get("mimeType", "")).split(";")[0].strip().lower()
    params = post.get("params") or []
    if params:
        names = list(dict.fromkeys(_field(str(p.get("name", ""))) for p in params))
        kind = {"multipart/form-data": "string|file"}.get(mime, "string")
        return mime, [f"{n}: {'file' if any(p.get('fileName') for p in params if p.get('name') == n) else kind}"
                      for n in names]  # fmt: skip
    text = post.get("text") or ""
    if "json" in mime or text.lstrip().startswith(("{", "[")):
        data = _json(text)
        s = shape(data)
        if isinstance(s, dict) and "[]" not in s:
            return mime or "application/json", [f"{k}: {type_of(v)}" for k, v in s.items()]
        if s != "unknown":
            return mime or "application/json", [f"(body): {type_of(s)}"]
    if mime == "application/x-www-form-urlencoded":
        names = dict.fromkeys(_field(k) for k, _ in parse_qsl(text, keep_blank_values=True))
        return mime, [f"{n}: string" for n in names]
    return mime, []


def _values(entry: dict) -> set[str]:
    """Words of every value in a request and response (query, path, bodies), lower-case."""
    req, resp = entry.get("request") or {}, entry.get("response") or {}
    url = urlsplit(str(req.get("url", "")))
    out = sanitize.words(" ".join(v for _, v in parse_qsl(url.query, keep_blank_values=True)))
    out |= sanitize.words(" ".join(s for s in url.path.split("/") if sanitize.is_id_segment(s)))
    post = req.get("postData") or {}
    out |= sanitize.words([str(p.get("value", "")) for p in post.get("params") or []])
    body = _json(post.get("text"))
    out |= sanitize.words(body if body is not None else str(post.get("text") or "")[:MAX_BODY])
    if int(resp.get("status") or 0) < 400:  # an error body is the application's own message
        out |= sanitize.words(_json(_text(resp.get("content") or {})))
    return out


def exchanges(har: dict) -> list[Exchange]:
    out = []
    for entry in har.get("log", {}).get("entries", []) or []:
        seen = _values(entry)
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        url = urlsplit(str(req.get("url", "")))
        if url.scheme not in ("http", "https"):
            continue
        method = str(req.get("method", "GET")).upper()
        headers = sanitize.safe_headers(req.get("headers"))  # credentials dropped here
        req_mime, req_fields = _request_fields(req.get("postData"))
        content = resp.get("content") or {}
        resp_mime = str(content.get("mimeType", "")).split(";")[0].strip().lower()
        text = _text(content)
        rtype = str(entry.get("_resourceType", "")).lower()
        status = int(resp.get("status") or 0)
        if rtype in ("xhr", "fetch") or "json" in resp_mime or "json" in req_mime:
            kind = "api"
        elif rtype == "document" or (resp_mime == "text/html" and method == "GET"):
            kind = "document"
        elif method == "POST" and req_mime in (
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        ):
            kind = "form"
        elif (
            ASSET.search(url.path)
            or resp_mime.startswith(("text/css", "image/", "font/"))
            or ("javascript" in resp_mime)
        ):
            kind = "asset"
        elif method != "GET":
            kind = "api"
        else:
            kind = "other"
        keep_text = kind in ("document", "form") or (kind == "asset" and "javascript" in resp_mime)
        keep_text = keep_text or (kind == "asset" and url.path.endswith((".js", ".mjs")))
        out.append(
            Exchange(
                started=_time(entry.get("startedDateTime")),
                method=method,
                host=url.netloc.lower(),
                path=url.path or "/",
                query=list(dict.fromkeys(_field(k) for k, _ in parse_qsl(url.query, keep_blank_values=True))),
                request=headers,
                req_mime=req_mime,
                req_fields=req_fields,
                status=status,
                resp_mime=resp_mime,
                resp_json=_json(text) if "json" in resp_mime or kind == "api" else None,
                resp_text=text if keep_text else None,
                ms=float(entry.get("time") or 0),
                kind=kind,
                values=seen,
                resp_headers=sanitize.tech_headers(resp.get("headers")),
                cookies=sanitize.cookie_names(entry),
            )
        )  # fmt: skip
    return out


# --- technology ------------------------------------------------------------------------------------
#
# What the recording shows about the technology, from signals that are cheap and reliable: response
# header product names, cookie NAMES, address extensions, script and style file names, element ids,
# classes, field names, attribute names. Values are never read or kept: a marker is a label written
# here, not text copied from the recording. Each finding has a confidence: certain (only this
# technology does it), likely (usually it, but another could), guess (a convention others share).

CERTAIN, LIKELY, GUESS = "certain", "likely", "guess"
RANK = {CERTAIN: 3, LIKELY: 2, GUESS: 1}
GENERIC_FRAMEWORKS = {"ASP.NET", "JSP/Servlet"}  # a more specific framework of the same wins
UI_ORDER = ["Bootstrap"]  # component suites outrank Bootstrap as "the" ui_library


class _Rule(NamedTuple):
    source: str  # where to look: cookie, header:<name>, path, url, host, script, link, class, ...
    rx: re.Pattern
    kind: str  # server | web | front | lib | ui
    name: str  # server: the technology; others: the product
    conf: str
    marker: str
    fw: str | None = None  # server: the framework within the technology
    implies: tuple[str, str | None] | None = None  # a server technology (and framework) it proves
    version: str | None = None  # when the signal fixes the version without showing it


def _rule(source: str, rx: str, kind: str, name: str, conf: str, marker: str, **kw) -> _Rule:
    return _Rule(source, re.compile(rx, re.I), kind, name, conf, marker, **kw)


def _srv(source, rx, tech, fw, conf, marker, **kw):
    return _rule(source, rx, "server", tech, conf, marker, fw=fw, **kw)


C_, L_, G_ = CERTAIN, LIKELY, GUESS
VER = r"(?:/(?P<v>\d[\w.]*))?"

SERVER_RULES: list[_Rule] = [
    # response headers
    _srv("header:x-powered-by", r"\bPHP" + VER, "PHP", None, C_, "X-Powered-By header names PHP"),
    _srv("header:x-powered-by", r"\bExpress\b", "Node.js", "Express", C_, "X-Powered-By: Express"),
    _srv("header:x-powered-by", r"ASP\.NET", ".NET", "ASP.NET", C_, "X-Powered-By: ASP.NET"),
    _srv(
        "header:x-powered-by",
        r"\b(?:Servlet|JSP)\b" + VER,
        "Java",
        "JSP/Servlet",
        C_,
        "X-Powered-By names Servlet/JSP",
    ),
    _srv(
        "header:x-powered-by",
        r"\b(?:Undertow|JBoss|WildFly)\b",
        "Java",
        None,
        C_,
        "X-Powered-By names a Java application server",
    ),
    _srv(
        "header:x-powered-by",
        r"Phusion Passenger",
        "Ruby",
        None,
        L_,
        "X-Powered-By: Phusion Passenger",
    ),
    _srv(
        "header:x-aspnet-version",
        r"^(?P<v>\d[\d.]*)?",
        ".NET",
        "ASP.NET",
        C_,
        "X-AspNet-Version header",
    ),
    _srv(
        "header:x-aspnetmvc-version",
        r"^(?P<v>\d[\d.]*)?",
        ".NET",
        "ASP.NET MVC",
        C_,
        "X-AspNetMvc-Version header",
    ),
    _srv(
        "header:x-generator", r"Drupal(?: (?P<v>\d+))?", "PHP", "Drupal", L_, "X-Generator: Drupal"
    ),
    _srv("header:x-generator", r"WordPress", "PHP", "WordPress", L_, "X-Generator: WordPress"),
    _srv("header:x-runtime", r"^", "Ruby", "Rails", G_, "X-Runtime header (a Rails convention)"),
    _srv(
        "header:x-pingback",
        r"^",
        "PHP",
        "WordPress",
        G_,
        "X-Pingback header (a WordPress convention)",
    ),
    # cookie names
    _srv("cookie", r"^PHPSESSID$", "PHP", None, C_, "PHPSESSID cookie"),
    _srv("cookie", r"^laravel_session$", "PHP", "Laravel", C_, "laravel_session cookie"),
    _srv(
        "cookie",
        r"^XSRF-TOKEN$",
        "PHP",
        "Laravel",
        G_,
        "XSRF-TOKEN cookie (Laravel; Angular and Spring use the name too)",
    ),
    _srv(
        "cookie",
        r"^(wordpress_|wp-settings-|wordpress_logged_in)",
        "PHP",
        "WordPress",
        C_,
        "WordPress cookie names",
    ),
    _srv("cookie", r"^(ci_session)$", "PHP", "CodeIgniter", L_, "ci_session cookie"),
    _srv("cookie", r"^(CAKEPHP)$", "PHP", "CakePHP", L_, "CAKEPHP cookie"),
    _srv("cookie", r"^SSESS[0-9a-f]+$", "PHP", "Drupal", L_, "SSESS... cookie (Drupal)"),
    _srv("cookie", r"^JSESSIONID$", "Java", None, C_, "JSESSIONID cookie"),
    _srv("cookie", r"^ASP\.NET_SessionId$", ".NET", "ASP.NET", C_, "ASP.NET_SessionId cookie"),
    _srv("cookie", r"^\.ASPXAUTH$", ".NET", "ASP.NET", C_, ".ASPXAUTH cookie"),
    _srv("cookie", r"^\.AspNetCore\.", ".NET", "ASP.NET Core", C_, ".AspNetCore.* cookies"),
    _srv("cookie", r"^\.AspNet\.", ".NET", "ASP.NET", C_, ".AspNet.* cookies (OWIN)"),
    _srv("cookie", r"^ASPSESSIONID", "Classic ASP", None, C_, "ASPSESSIONID* cookie"),
    _srv("cookie", r"^csrftoken$", "Python", "Django", L_, "csrftoken cookie (Django)"),
    _srv(
        "cookie",
        r"^sessionid$",
        "Python",
        "Django",
        G_,
        "sessionid cookie (Django's name; others use it)",
    ),
    _srv("cookie", r"^session$", "Python", "Flask", G_, "session cookie (Flask's default name)"),
    _srv("cookie", r"^_[\w-]+_session$", "Ruby", "Rails", L_, "_*_session cookie (Rails)"),
    _srv("cookie", r"^rack\.session$", "Ruby", None, L_, "rack.session cookie"),
    _srv(
        "cookie", r"^(connect\.sid|express:sess)$", "Node.js", "Express", L_, "connect.sid cookie"
    ),
    _srv("cookie", r"^(CFID|CFTOKEN)$", "ColdFusion", None, C_, "CFID/CFTOKEN cookies"),
    _srv(
        "cookie",
        r"^(ORA_WWV_|WWV_FLOW)",
        "Oracle",
        "Oracle APEX",
        C_,
        "ORA_WWV_* cookie (Oracle APEX)",
    ),
    _srv(
        "cookie",
        r"^(sap-usercontext|SAP_SESSIONID_|MYSAPSSO2|sap-ssr)",
        "SAP",
        None,
        C_,
        "SAP cookie names",
    ),
    _srv(
        "cookie",
        r"^(glide_user_route|glide_session_store|glide_user_activity|glide_user)$",
        "ServiceNow",
        None,
        C_,
        "glide_* cookies",
    ),
    _srv(
        "cookie",
        r"^(BrowserId|sfdc-stream|oinfo)$",
        "Salesforce",
        None,
        L_,
        "Salesforce cookie names",
    ),
    # addresses
    _srv("path", r"\.jsp[xf]?$", "Java", "JSP", C_, ".jsp addresses"),
    _srv("path", r"\.do$", "Java", "Struts", L_, ".do addresses (Struts convention)"),
    _srv("path", r"\.action$", "Java", "Struts", L_, ".action addresses (Struts 2 convention)"),
    _srv("path", r"\.(xhtml|jsf|faces)$", "Java", "JSF", L_, ".xhtml/.jsf addresses (JSF)"),
    _srv(
        "path",
        r"/(javax|jakarta)\.faces\.resource/",
        "Java",
        "JSF",
        C_,
        "javax.faces.resource addresses",
    ),
    # JSF's own script says which specification it implements: 23000 is 2.3, 40000 is 4.0.
    _srv(
        "text",
        r"\b(?:jsf|faces)\.specversion\s*=\s*(?P<v>\d)(?P<vminor>\d)000\b",
        "Java",
        "JSF",
        C_,
        "specversion in JSF's own script (jsf.js)",
    ),
    _srv(
        "path",
        r"/j_spring_security_check$|/j_security_check$",
        "Java",
        "Spring MVC",
        L_,
        "j_spring_security_check address (Spring Security)",
    ),
    _srv("path", r"^/actuator(/|$)", "Java", "Spring MVC", L_, "/actuator address (Spring Boot)"),
    _srv("path", r"\.nocache\.js$", "Java", "GWT", C_, ".nocache.js script (GWT)"),
    _srv("script", r"\.nocache\.js$", "Java", "GWT", C_, ".nocache.js script (GWT)"),
    _srv("path", r"/VAADIN/", "Java", "Vaadin", C_, "/VAADIN/ addresses"),
    _srv(
        "path",
        r"/wicket/resource/|/wicket-ajax|wicket-event-jquery",
        "Java",
        "Wicket",
        L_,
        "Wicket resource addresses",
    ),
    _srv("flag", r"^jsessionid-in-url$", "Java", None, C_, ";jsessionid in an address"),
    _srv("path", r"\.aspx$", ".NET", "ASP.NET Web Forms", L_, ".aspx addresses"),
    _srv("path", r"\.(ashx|asmx|svc)$", ".NET", "ASP.NET", C_, ".ashx/.asmx/.svc addresses"),
    _srv(
        "path",
        r"/(WebResource|ScriptResource)\.axd$",
        ".NET",
        "ASP.NET Web Forms",
        C_,
        "WebResource.axd / ScriptResource.axd",
    ),
    _srv("path", r"\.axd$", ".NET", "ASP.NET", C_, ".axd addresses"),
    _srv(
        "path",
        r"/_blazor(/|$)|^/_framework/blazor\.[\w.]*js$",
        ".NET",
        "Blazor",
        C_,
        "_blazor / _framework/blazor.*.js",
    ),
    _srv("path", r"^/_framework/", ".NET", "Blazor", L_, "/_framework/ addresses (Blazor)"),
    _srv("path", r"\.asp$", "Classic ASP", None, L_, ".asp addresses"),
    _srv("path", r"\.cf[mc]$", "ColdFusion", None, C_, ".cfm/.cfc addresses"),
    _srv("path", r"\.php\d?$", "PHP", None, L_, ".php addresses"),
    _srv(
        "path",
        r"/wp-(content|includes|json|admin)/",
        "PHP",
        "WordPress",
        C_,
        "/wp-content/ addresses",
    ),
    _srv(
        "path",
        r"/(sites/default/files|core/misc/drupal)",
        "PHP",
        "Drupal",
        L_,
        "Drupal file addresses",
    ),
    _srv("path", r"^/(_profiler|_wdt)/", "PHP", "Symfony", C_, "Symfony profiler addresses"),
    _srv(
        "path",
        r"^/assets/application-[0-9a-f]{8,}\.(js|css)$",
        "Ruby",
        "Rails",
        L_,
        "assets/application-<digest> (Rails asset pipeline)",
    ),
    _srv("path", r"^/rails/active_storage/", "Ruby", "Rails", C_, "rails/active_storage addresses"),
    _srv(
        "path", r"^/static/admin/", "Python", "Django", L_, "static/admin/ addresses (Django admin)"
    ),
    _srv(
        "path",
        r"/forms/(frmservlet|lservlet)",
        "Oracle",
        "Oracle Forms",
        C_,
        "forms/frmservlet address",
    ),
    _srv("path", r"^/pls/\w+/", "Oracle", None, L_, "/pls/<dad>/ address (mod_plsql)"),
    _srv("url", r"/f\?p\b", "Oracle", "Oracle APEX", C_, "f?p= addresses (Oracle APEX)"),
    _srv("url", r"wwv_flow", "Oracle", "Oracle APEX", C_, "wwv_flow addresses (Oracle APEX)"),
    _srv(
        "name",
        r"^(p_flow_id|p_flow_step_id|wwv_flow)",
        "Oracle",
        "Oracle APEX",
        C_,
        "wwv_flow / p_flow_id fields (Oracle APEX)",
    ),
    _srv("path", r"/sap/(bc|public|opu)/", "SAP", None, C_, "/sap/bc/ or /webdynpro/ address"),
    _srv("path", r"^/webdynpro/", "SAP", "WebDynpro", C_, "/webdynpro/ address"),
    _srv("url", r"[?,](sap-client|sap-language|sap-ui-)", "SAP", None, L_, "sap-* query fields"),
    _srv("path", r"/start\.swe$", "Siebel", None, C_, "start.swe address"),
    _srv("url", r"[?,](SWECmd|SWEView|SWEApplet|SWEC)\b", "Siebel", None, C_, "SWE* query fields"),
    _srv("url", r"[?,]sysparm_", "ServiceNow", None, C_, "sysparm_* query fields"),
    _srv(
        "path", r"/nav_to\.do$|^/api/now/", "ServiceNow", None, C_, "nav_to.do or /api/now/ address"
    ),
    _srv("host", r"\.service-now\.com$", "ServiceNow", None, C_, "service-now.com host"),
    _srv(
        "host",
        r"\.(force|salesforce|lightning\.force)\.com$",
        "Salesforce",
        None,
        C_,
        "Salesforce host",
    ),
    _srv(
        "path",
        r"^/(aura|lightning/|s/sfsites)",
        "Salesforce",
        None,
        L_,
        "/aura or /lightning/ address",
    ),
    # page contents: field names, ids, meta, attributes
    _srv(
        "name",
        r"^(javax|jakarta)\.faces\.ViewState$",
        "Java",
        "JSF",
        C_,
        "javax.faces.ViewState field",
    ),
    _srv("name", r"^(javax|jakarta)\.faces\.", "Java", "JSF", C_, "javax.faces.* field"),
    _srv("name", r"^_csrf$", "Java", "Spring MVC", L_, "_csrf field (Spring Security)"),
    _srv(
        "meta", r"^_csrf(_header)?$", "Java", "Spring MVC", L_, "_csrf meta tag (Spring Security)"
    ),
    _srv("id", r"^__gwt_", "Java", "GWT", C_, "__gwt_* element ids"),
    _srv("script", r"vaadinBootstrap|vaadin-flow|/VAADIN/", "Java", "Vaadin", C_, "Vaadin scripts"),
    _srv("tag", r"^vaadin-", "Java", "Vaadin", C_, "vaadin-* elements"),
    _srv("attr", r"^data-wicket", "Java", "Wicket", L_, "data-wicket attributes"),
    _srv(
        "name",
        r"^__(VIEWSTATE|EVENTVALIDATION|VIEWSTATEGENERATOR|EVENTTARGET|EVENTARGUMENT)$",
        ".NET",
        "ASP.NET Web Forms",
        C_,
        "__VIEWSTATE / __EVENTVALIDATION fields",
    ),
    _srv(
        "name",
        r"^__RequestVerificationToken$",
        ".NET",
        "ASP.NET MVC / Razor",
        L_,
        "__RequestVerificationToken field (ASP.NET anti-forgery)",
    ),
    _srv(
        "script",
        r"/_framework/blazor\.[\w.]*js$",
        ".NET",
        "Blazor",
        C_,
        "_framework/blazor.*.js script",
    ),
    _srv("tag", r"^blazor-", ".NET", "Blazor", L_, "blazor-* elements"),
    _srv(
        "name",
        r"^csrfmiddlewaretoken$",
        "Python",
        "Django",
        C_,
        "csrfmiddlewaretoken field (Django)",
    ),
    _srv("name", r"^authenticity_token$", "Ruby", "Rails", C_, "authenticity_token field (Rails)"),
    _srv("meta", r"^csrf-param$", "Ruby", "Rails", L_, "csrf-param meta tag (Rails)"),
    _srv(
        "attr", r"^data-turbo(links)?$", "Ruby", "Rails", L_, "data-turbo(links) attributes (Rails)"
    ),
    _srv(
        "script",
        r"turbolinks|rails-ujs|/rails\.js$|@rails/ujs",
        "Ruby",
        "Rails",
        L_,
        "Turbolinks / rails-ujs script",
    ),
    _srv("name", r"^_token$", "PHP", "Laravel", L_, "_token field (Laravel)"),
    _srv(
        "meta",
        r"^csrf-token$",
        "PHP",
        "Laravel",
        G_,
        "csrf-token meta tag (Laravel; Rails has it too)",
    ),
    _srv("name", r"\[_token\]$", "PHP", "Symfony", L_, "form[_token] field (Symfony forms)"),
    _srv("class", r"^sf-toolbar", "PHP", "Symfony", C_, "sf-toolbar (Symfony debug bar)"),
    _srv(
        "name", r"^(form_build_id|form_token)$", "PHP", "Drupal", L_, "form_build_id field (Drupal)"
    ),
    _srv("meta", r"^generator:WordPress", "PHP", "WordPress", C_, "generator meta: WordPress"),
    _srv("meta", r"^generator:Drupal", "PHP", "Drupal", C_, "generator meta: Drupal"),
    _srv("meta", r"^generator:Joomla", "PHP", "Joomla", C_, "generator meta: Joomla"),
    _srv("class", r"^wp-block-|^wp-admin$", "PHP", "WordPress", L_, "wp-* classes (WordPress)"),
    _srv("script", r"/wp-(content|includes)/", "PHP", "WordPress", C_, "wp-content scripts"),
    _srv(
        "script",
        r"sap-ui-core\.js|/sap/public/bc/ui5",
        "SAP",
        "SAP UI5",
        C_,
        "sap-ui-core.js script",
    ),
    _srv("tag", r"^ui5-", "SAP", "SAP UI5", L_, "ui5-* elements"),
    _srv("class", r"^sapM|^sapUi", "SAP", "SAP UI5", C_, "sapM*/sapUi* classes (SAP UI5)"),
    _srv(
        "class", r"^lsSbar|^lsButton|^urBtn", "SAP", "WebDynpro", L_, "ls*/ur* classes (WebDynpro)"
    ),
    _srv("class", r"^slds-", "Salesforce", None, L_, "slds-* classes (Salesforce Lightning)"),
    _srv("tag", r"^lightning-|^force:|^aura:", "Salesforce", None, C_, "lightning-* elements"),
    _srv("attr", r"^data-aura", "Salesforce", None, C_, "data-aura* attributes"),
    _srv("script", r"/aura_(prod|dev)|/auraFW/", "Salesforce", None, C_, "Aura framework scripts"),
    _srv("id", r"^SWE", "Siebel", None, L_, "SWE* element ids"),
    _srv("id", r"^gsft_main$|^sysparm_", "ServiceNow", None, C_, "gsft_main / sysparm_ ids"),
    _srv("class", r"^(sn-|now-)\w", "ServiceNow", None, G_, "sn-* / now-* classes"),
]

# (source, pattern, product, implied server technology and framework, confidence, marker)
WEB_RULES: list[_Rule] = [
    _rule("header:server", r"Microsoft-IIS" + VER, "web", "IIS", C_, "Server header: IIS"),
    _rule(
        "header:server",
        r"\bApache(?!-)(?! Tomcat)" + VER,
        "web",
        "Apache HTTP Server",
        C_,
        "Server header: Apache",
    ),
    _rule("header:server", r"\bnginx" + VER, "web", "nginx", C_, "Server header: nginx"),
    _rule(
        "header:server",
        r"Tomcat" + VER,
        "web",
        "Tomcat",
        C_,
        "Server header: Tomcat",
        implies=("Java", None),
    ),
    _rule(
        "header:server",
        r"Apache-Coyote" + VER,
        "web",
        "Tomcat",
        L_,
        "Server header: Apache-Coyote (Tomcat's connector)",
        implies=("Java", None),
    ),
    _rule(
        "header:server",
        r"WebLogic(?: Server)?(?: (?P<v>\d[\d.]*))?",
        "web",
        "WebLogic",
        C_,
        "Server header: WebLogic",
        implies=("Java", None),
    ),
    _rule(
        "header:server",
        r"WebSphere(?: Application Server)?" + VER,
        "web",
        "WebSphere",
        C_,
        "Server header: WebSphere",
        implies=("Java", None),
    ),
    _rule(
        "header:server",
        r"IBM_HTTP_Server" + VER,
        "web",
        "IBM HTTP Server",
        C_,
        "Server header: IBM_HTTP_Server",
    ),
    _rule(
        "header:server",
        r"\b(JBoss|WildFly|Undertow)\b(?:-(?P<v>\d[\w.]*))?",
        "web",
        "JBoss",
        C_,
        "Server header: JBoss/WildFly",
        implies=("Java", None),
    ),
    _rule(
        "header:server",
        r"\bJetty[ (]*(?P<v>\d[\d.]*)?",
        "web",
        "Jetty",
        C_,
        "Server header: Jetty",
        implies=("Java", None),
    ),
    _rule(
        "header:server",
        r"\bKestrel\b",
        "web",
        "Kestrel",
        C_,
        "Server header: Kestrel",
        implies=(".NET", "ASP.NET Core"),
    ),
    _rule(
        "header:server",
        r"Oracle-(HTTP-Server|Application-Server)",
        "web",
        "Oracle HTTP Server",
        L_,
        "Server header: Oracle HTTP Server",
    ),
    _rule(
        "header:server",
        r"\b(gunicorn|uvicorn)" + VER,
        "web",
        "gunicorn/uvicorn",
        L_,
        "Server header: gunicorn/uvicorn",
        implies=("Python", None),
    ),
    _rule(
        "header:server",
        r"Werkzeug" + VER,
        "web",
        "Werkzeug",
        L_,
        "Server header: Werkzeug",
        implies=("Python", "Flask"),
    ),
    _rule(
        "header:server",
        r"\b(Puma|Unicorn|Phusion_Passenger)\b",
        "web",
        "Ruby app server",
        L_,
        "Server header: Puma/Unicorn/Passenger",
        implies=("Ruby", None),
    ),
]

# Front-end frameworks and libraries. `implies` names the server technology a library proves.
CLIENT_RULES: list[_Rule] = [
    # AngularJS 1.x, distinct from Angular 2+
    _rule(
        "attr",
        r"^(data-)?ng-(app|controller)$",
        "front",
        "AngularJS",
        C_,
        "ng-app / ng-controller attribute",
    ),
    _rule(
        "attr", r"^ng-(model|repeat|click|bind|if)$", "front", "AngularJS", L_, "ng-* directives"
    ),
    _rule(
        "script",
        r"angular(?:js)?(?:[-.](?P<v>1\.\d+(?:\.\d+)?))?(?:\.min)?\.js$|/angularjs/",
        "front",
        "AngularJS",
        C_,
        "angular.js script",
    ),
    _rule(
        "text",
        r"AngularJS v(?P<v>1\.\d+\.\d+)",
        "front",
        "AngularJS",
        C_,
        "AngularJS banner in a bundle",
    ),
    _rule(
        "script",
        r"svelte|/_app/immutable/",
        "front",
        "Svelte",
        L_,
        "svelte script / _app/immutable",
    ),
    _rule("class", r"^svelte-[a-z0-9]{4,8}$", "front", "Svelte", L_, "svelte-<hash> classes"),
    _rule("class", r"^ember-(view|application)$", "front", "Ember", L_, "ember-view classes"),
    _rule("id", r"^ember\d+$", "front", "Ember", L_, "ember<n> element ids"),
    _rule(
        "script",
        r"(^|/)ember(\.min|\.debug)?(-(?P<v>\d[\d.]*))?\.js$",
        "front",
        "Ember",
        C_,
        "ember.js script",
    ),
    _rule(
        "script",
        r"knockout(?:-(?P<v>\d[\d.]*))?(?:\.min|\.debug)?\.js$",
        "lib",
        "Knockout",
        C_,
        "knockout.js script",
    ),
    _rule("attr", r"^data-bind$", "lib", "Knockout", L_, "data-bind attributes"),
    _rule(
        "text",
        r"Knockout JavaScript library v(?P<v>\d[\d.]*)",
        "lib",
        "Knockout",
        C_,
        "Knockout banner in a bundle",
    ),
    _rule(
        "script",
        r"backbone(?:-(?P<v>\d[\d.]*))?(?:-min|\.min)?\.js$",
        "lib",
        "Backbone",
        C_,
        "backbone.js script",
    ),
    _rule(
        "text",
        r"Backbone\.js (?P<v>\d[\d.]*)",
        "lib",
        "Backbone",
        C_,
        "Backbone banner in a bundle",
    ),
    _rule(
        "id", r"^__next$|^__NEXT_DATA__$", "lib", "Next.js", C_, "__next / __NEXT_DATA__ element"
    ),
    _rule("script", r"/_next/static/", "lib", "Next.js", C_, "/_next/static/ scripts"),
    _rule("header:x-powered-by", r"Next\.js", "lib", "Next.js", C_, "X-Powered-By: Next.js"),
    _rule("id", r"^__nuxt$|^__NUXT__$", "lib", "Nuxt", C_, "__nuxt element"),
    _rule("script", r"/_nuxt/", "lib", "Nuxt", C_, "/_nuxt/ scripts"),
    _rule("script", r"(^|/)dojo(/dojo)?\.js$|/dijit/|/dojox/", "lib", "Dojo", C_, "dojo.js script"),
    _rule("class", r"^dijit", "lib", "Dojo", C_, "dijit* classes"),
    _rule(
        "script",
        r"(^|/)ext(-all|-debug|-all-debug|-base)?(-(?P<v>\d[\d.]*))?\.js$|/sencha|/ext-\d",
        "lib",
        "ExtJS",
        L_,
        "ext-all.js script",
    ),
    _rule(
        "class",
        r"^x-(grid3?|panel|window|btn|form-field|toolbar)\b",
        "lib",
        "ExtJS",
        L_,
        "x-panel / x-grid classes",
    ),
    _rule("id", r"^ext-(gen|comp)-?\d+", "lib", "ExtJS", L_, "ext-gen<n> element ids"),
    _rule(
        "script",
        r"jquery(?:[-.]|/)?(?P<v>\d+\.\d+(?:\.\d+)?)?(?:\.slim)?(?:\.min)?\.js$|/jquery/jquery",
        "lib",
        "jQuery",
        C_,
        "jquery script",
    ),
    _rule(
        "text",
        r"jQuery(?: JavaScript Library)? v(?P<v>\d+\.\d+(?:\.\d+)?)",
        "lib",
        "jQuery",
        C_,
        "jQuery banner in a bundle",
    ),
    _rule(
        "script",
        r"jquery[-.]?ui(?:[-.](?P<v>\d+\.\d+(?:\.\d+)?))?|jquery\.ui\.",
        "lib",
        "jQuery UI",
        C_,
        "jquery-ui script",
    ),
    _rule(
        "text",
        r"jQuery UI(?: -)? v(?P<v>\d+\.\d+(?:\.\d+)?)",
        "lib",
        "jQuery UI",
        C_,
        "jQuery UI banner in a bundle",
    ),
    _rule(
        "class",
        r"^ui-(datepicker|autocomplete|dialog-titlebar|sortable|tabs-nav)\b",
        "lib",
        "jQuery UI",
        L_,
        "ui-datepicker / ui-dialog classes",
    ),
    # UI component libraries
    _rule(
        "script",
        r"bootstrap(?:[-.@/]v?(?P<v>\d+\.\d+(?:\.\d+)?))?(?:\.bundle)?(?:\.min)?\.js",
        "ui",
        "Bootstrap",
        C_,
        "bootstrap script",
    ),
    _rule(
        "link",
        r"bootstrap(?:[-.@/]v?(?P<v>\d+\.\d+(?:\.\d+)?))?(?:\.min)?\.css",
        "ui",
        "Bootstrap",
        C_,
        "bootstrap stylesheet",
    ),
    _rule(
        "script",
        r"/bootstrap[@/](?P<v>\d+\.\d+\.\d+)/",
        "ui",
        "Bootstrap",
        C_,
        "bootstrap/<version> script",
    ),
    _rule(
        "link",
        r"/bootstrap[@/](?P<v>\d+\.\d+\.\d+)/",
        "ui",
        "Bootstrap",
        C_,
        "bootstrap/<version> stylesheet",
    ),
    _rule(
        "text",
        r"Bootstrap v(?P<v>\d+\.\d+\.\d+)",
        "ui",
        "Bootstrap",
        C_,
        "Bootstrap banner in a bundle",
    ),
    _rule(
        "attr",
        r"^data-bs-(toggle|target)$",
        "ui",
        "Bootstrap",
        L_,
        "data-bs-* attributes (Bootstrap 5)",
        version="5",
    ),
    _rule(
        "class",
        r"^(col-xs-\d+|glyphicon)",
        "ui",
        "Bootstrap",
        L_,
        "col-xs-* / glyphicon classes (Bootstrap 3)",
        version="3",
    ),
    _rule(
        "class",
        r"^(custom-select|custom-control|form-row|badge-pill)$",
        "ui",
        "Bootstrap",
        L_,
        "custom-control / form-row classes (Bootstrap 4)",
        version="4",
    ),
    _rule(
        "class",
        r"^(form-select|visually-hidden|ms-auto|me-auto|d-grid)$",
        "ui",
        "Bootstrap",
        L_,
        "form-select / ms-auto classes (Bootstrap 5)",
        version="5",
    ),
    _rule(
        "class",
        r"^(navbar-nav|btn-primary|container-fluid|col-(sm|md|lg)-\d+)$",
        "ui",
        "Bootstrap",
        G_,
        "navbar-nav / btn-primary / col-md-* classes",
    ),
    _rule(
        "class",
        r"^ui-(datatable|selectonemenu|inputfield|panelgrid|outputlabel|commandlink)",
        "ui",
        "PrimeFaces",
        C_,
        "ui-datatable / ui-selectonemenu classes (PrimeFaces)",
        implies=("Java", "JSF"),
    ),
    _rule(
        "path",
        r"/primefaces[/-]|primefaces\.(js|css)",
        "ui",
        "PrimeFaces",
        C_,
        "primefaces resources",
        implies=("Java", "JSF"),
    ),
    _rule(
        "script",
        r"primefaces",
        "ui",
        "PrimeFaces",
        C_,
        "primefaces script",
        implies=("Java", "JSF"),
    ),
    # Which JSF implementation serves the pages, as its own script (jsf.js) names itself.
    _rule(
        "text",
        r"\bmojarra\.\w+\s*=\s*function\b",
        "lib",
        "Mojarra",
        C_,
        "mojarra.* functions in JSF's own script (jsf.js)",
        implies=("Java", "JSF"),
    ),
    _rule(
        "text",
        r"\bmyfaces\._impl\b",
        "lib",
        "MyFaces",
        C_,
        "myfaces._impl in JSF's own script (jsf.js)",
        implies=("Java", "JSF"),
    ),
    _rule(
        "class",
        r"^rf-(dt|p|tb|tbl|pnl|ac|cal|ddm|ei)\b",
        "ui",
        "RichFaces",
        L_,
        "rf-* classes (RichFaces)",
        implies=("Java", "JSF"),
    ),
    _rule(
        "path",
        r"/org\.richfaces/|richfaces",
        "ui",
        "RichFaces",
        C_,
        "richfaces resources",
        implies=("Java", "JSF"),
    ),
    _rule(
        "path",
        r"/icefaces/|ice\.core|icefaces",
        "ui",
        "ICEfaces",
        C_,
        "icefaces resources",
        implies=("Java", "JSF"),
    ),
    _rule(
        "class",
        r"^ice(Dat|Pnl|Cmd|Out|Sel|Inp)",
        "ui",
        "ICEfaces",
        L_,
        "ice* classes (ICEfaces)",
        implies=("Java", "JSF"),
    ),
    _rule(
        "class",
        r"^(?-i:Rad(Grid|ComboBox|Window|Input|Button|TextBox|DatePicker)|rgRow|rgAltRow|riTextBox)",
        "ui",
        "Telerik UI for ASP.NET AJAX",
        C_,
        "RadGrid / RadComboBox classes",
        implies=(".NET", "ASP.NET Web Forms"),
    ),
    _rule(
        "path",
        r"Telerik\.(Web\.UI|Sitefinity)",
        "ui",
        "Telerik UI for ASP.NET AJAX",
        C_,
        "Telerik.Web.UI.WebResource.axd",
        implies=(".NET", "ASP.NET Web Forms"),
    ),
    _rule(
        "script",
        r"kendo(?:[-.](?P<v>\d[\d.]*))?(?:\.all|\.web|\.ui)?(?:\.min)?\.js|/kendo",
        "ui",
        "Kendo UI",
        C_,
        "kendo script",
    ),
    _rule(
        "class",
        r"^k-(grid|widget|dropdown|textbox|state-default|button)\b",
        "ui",
        "Kendo UI",
        L_,
        "k-grid / k-widget classes (Kendo UI)",
    ),
    _rule("tag", r"^kendo-", "ui", "Kendo UI", C_, "kendo-* elements"),
    _rule(
        "class",
        r"^(dxgv|dxeEditArea|dxpc|dxm|dxbs-|dx-widget|dx-datagrid|dx-viewport)",
        "ui",
        "DevExpress",
        C_,
        "dx* classes (DevExpress)",
    ),
    _rule(
        "path",
        r"/DXR\.axd$",
        "ui",
        "DevExpress",
        C_,
        "DXR.axd",
        implies=(".NET", "ASP.NET Web Forms"),
    ),
    _rule("script", r"devextreme|dx\.(all|web|viz)", "ui", "DevExpress", C_, "devextreme script"),
    _rule(
        "class",
        r"^(ui-igedit|ui-iggrid|ig(Grid|Combo|Editor|Tree|Chart|Upload))",
        "ui",
        "Infragistics",
        C_,
        "ig* classes (Infragistics)",
    ),
    _rule(
        "script", r"infragistics|/ig\.ui|igniteui", "ui", "Infragistics", C_, "infragistics script"
    ),
    _rule("script", r"syncfusion|/ej2|ej\.web\.all", "ui", "Syncfusion", C_, "syncfusion script"),
    _rule("tag", r"^ejs-", "ui", "Syncfusion", C_, "ejs-* elements (Syncfusion)"),
    _rule(
        "class",
        r"^e-(control|lib|grid|ddl)\b",
        "ui",
        "Syncfusion",
        L_,
        "e-control / e-grid classes",
    ),
    _rule("tag", r"^mat-", "ui", "Angular Material", C_, "mat-* elements"),
    _rule(
        "class",
        r"^mat-(mdc-|form-field|toolbar|table|raised-button|card)",
        "ui",
        "Angular Material",
        C_,
        "mat-* classes",
    ),
    _rule(
        "tag",
        r"^p-(table|dropdown|calendar|button|dialog|menubar|toast|inputtext)$",
        "ui",
        "PrimeNG",
        C_,
        "p-table / p-dropdown elements (PrimeNG)",
    ),
    _rule(
        "class",
        r"^p-(datatable|component)\b",
        "ui",
        "PrimeNG",
        G_,
        "p-datatable / p-component classes (PrimeNG; PrimeReact and PrimeVue use them too)",
    ),
    _rule("class", r"^(?-i:Mui[A-Z]\w+)", "ui", "MUI", C_, "Mui* classes"),
    _rule(
        "class",
        r"^ant-(btn|table|select|input|layout|form|modal|menu|row|col)\b",
        "ui",
        "Ant Design",
        C_,
        "ant-* classes",
    ),
    _rule("class", r"^ag-(root|theme-|header|cell|row)", "ui", "AG Grid", C_, "ag-* classes"),
    _rule("script", r"ag-grid", "ui", "AG Grid", C_, "ag-grid script"),
    _rule(
        "class",
        r"^(dataTables_wrapper|dataTables_length|dataTable|dt-container)\b",
        "ui",
        "DataTables",
        C_,
        "dataTables_wrapper classes",
    ),
    _rule(
        "script",
        r"datatables|dataTables(?:[-.](?P<v>\d[\d.]*))?",
        "ui",
        "DataTables",
        C_,
        "datatables script",
    ),
    _rule(
        "text",
        r"DataTables (?P<v>\d+\.\d+\.\d+)",
        "ui",
        "DataTables",
        C_,
        "DataTables banner in a bundle",
    ),
]
RULES = SERVER_RULES + WEB_RULES + CLIENT_RULES

ANGULARJS_SCRIPT = re.compile(
    r"angular(?:js)?(?:[-.]1\.\d+(?:\.\d+)?)?(?:\.min)?\.js$|/angularjs/", re.I
)
TEXT_BANNER = 4000  # bytes of a script searched for a version banner
FACES_SCRIPT = re.compile(r"/(?:javax|jakarta)\.faces\.resource/(?:jsf|faces)\.js", re.I)
FACES_SCRIPT_FACTS = re.compile(
    r"\b(?:jsf|faces)\.specversion\s*=\s*\d{5}\b|\bmojarra\.\w+\s*=\s*function\b|\bmyfaces\._impl\b"
)
JSF_IMPLEMENTATIONS = ("Mojarra", "MyFaces")

# JavaScript bundles that show a framework by what is inside them.
JS_MARKERS: list[tuple[str, re.Pattern, str]] = [
    ("react", re.compile(r'version:"(\d+\.\d+\.\d+[\w.-]*)",rendererPackageName:"react-dom"'),
     "react-dom bundle, rendererPackageName"),
    ("react", re.compile(r'rendererPackageName:"react-dom"'), "react-dom bundle, rendererPackageName"),
    ("react", re.compile(r"__reactContainer\$|__reactFiber\$|__SECRET_INTERNALS_DO_NOT_USE"),
     "React internals in a bundle"),
    ("vue", re.compile(r'__vue_app__|__VUE_OPTIONS_API__|__VUE__'), "Vue internals in a bundle"),
    ("angular", re.compile(r"ɵcmp|ɵɵdefineComponent|@angular/core"), "Angular internals in a bundle"),
]  # fmt: skip
SCRIPT_NAMES: list[tuple[str, re.Pattern, str]] = [
    ("react", re.compile(r"/_next/|react(-dom)?[.-]"), "script name"),
    ("vue", re.compile(r"/_nuxt/|vue[.-]|chunk-vendors"), "script name"),
    ("angular", re.compile(r"angular|/main-es20\d\d|/polyfills[.-]"), "script name"),
]  # fmt: skip
FRONT_KEYS = ("framework", "version", "rendering", "marker", "ui_library")


def _signals(
    entries: list[Exchange], pages: list[sanitize.PageInfo], scripts: list[tuple[str, str | None]]
) -> dict[str, set[str]]:
    """Every signal the recording shows, by where it was seen. Names and file names only."""
    out: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        out["cookie"] |= e.cookies
        for name, value in e.resp_headers.items():
            out[f"header:{name}"].add(value)
        if ";jsessionid" in e.path.lower():
            out["flag"].add("jsessionid-in-url")
        e_path = e.path.split(";", 1)[0]
        out["path"].add(e_path)
        out["url"].add(f"{e_path}?{','.join(e.query)}" if e.query else e_path)
        out["host"].add(e.host.split(":")[0])
        out["name"].update(f.partition(": ")[0] for f in e.req_fields)
        out["name"].update(e.query)
        if e_path.endswith((".js", ".mjs")):
            out["script"].add(e_path)
        elif e_path.endswith(".css"):
            out["link"].add(e_path)
    for page in pages:
        for kind, values in page.facts.items():
            out[kind] |= values
        for fact in page.facts.get("url", ()):
            out["path"].add(fact.partition("?")[0])
        out["class"] |= page.classes
        out["id"] |= page.ids
    for path, text in scripts:
        out["script"].add(path)
        if text:
            out["text"].add(text[:TEXT_BANNER])
            if FACES_SCRIPT.search(path):
                # JSF's own script: what it says of itself is anywhere in it (a licence comment
                # may fill the banner, and the implementation's namespace comes last).
                out["text"].update(m.group(0) for m in FACES_SCRIPT_FACTS.finditer(text))
    return out


def _hits(values: dict[str, set[str]]) -> list[dict]:
    """Apply every rule; one hit per rule, with the version when the signal showed one."""
    hits = []
    for rule in RULES:
        found, version = False, None
        for value in values.get(rule.source, ()):
            m = rule.rx.search(value)
            if m:
                found = True
                groups = m.groupdict()
                if not version and groups.get("v"):  # `vminor`: the digit after the dot
                    version = ".".join(x for x in (groups["v"], groups.get("vminor")) if x)
                if version:
                    break
        if found:
            hits.append({"rule": rule, "version": version or rule.version})
    return hits


def _best(items: list[dict]) -> dict:
    """The hit to report: highest confidence, then one that shows a version."""
    top = max(items, key=lambda h: (RANK[h["rule"].conf], bool(h["version"])))
    if top["version"]:
        return top
    shown = [h for h in sorted(items, key=lambda h: -RANK[h["rule"].conf]) if h["version"]]
    return {**top, "version": shown[0]["version"]} if shown else top


def _evidence(hits: list[dict], legacy: list[dict]) -> list[dict]:
    out, seen = [], set()
    for h in sorted(hits, key=lambda h: (-RANK[h["rule"].conf], h["rule"].kind, h["rule"].name)):
        r = h["rule"]
        name = f"{r.name} / {r.fw}" if r.kind == "server" and r.fw else r.name
        item = {"kind": r.kind, "name": name, "marker": r.marker, "confidence": r.conf}
        if h["version"]:
            item["version"] = h["version"]
        if (name, r.marker) not in seen:
            seen.add((name, r.marker))
            out.append(item)
    return out + [x for x in legacy if (x["name"], x["marker"]) not in seen]


def _pick_server(hits: list[dict]) -> dict:
    """The server technology (with its framework and web server) the hits show. Several technologies in one recording: the best proven is reported, the others
    as `also`."""
    by_tech: dict[str, list[tuple[str, str | None, dict]]] = defaultdict(list)
    for h in hits:
        r = h["rule"]
        if r.kind == "server":
            by_tech[r.name].append((r.conf, r.fw, h))
        if r.implies:  # a UI suite or web server proves its platform, never beyond "likely"
            conf = r.conf if RANK[r.conf] <= RANK[LIKELY] else LIKELY
            by_tech[r.implies[0]].append((conf, r.implies[1], {**h, "implied": True}))
    web = [h for h in hits if h["rule"].kind == "web"]
    web_info: dict = {}
    if web:
        h = max(
            web, key=lambda h: (RANK[h["rule"].conf], bool(h["rule"].implies), bool(h["version"]))
        )
        web_info = {
            "name": h["rule"].name,
            "marker": h["rule"].marker,
            "confidence": h["rule"].conf,
        }
        if h["version"]:
            web_info["version"] = h["version"]
    if not by_tech:
        return {"web_server": web_info} if web_info else {}

    def score(tech: str) -> tuple[int, int]:
        items = by_tech[tech]
        return max(RANK[c] for c, _, _ in items), len({h["rule"].marker for _, _, h in items})

    ranked = sorted(by_tech, key=score, reverse=True)
    top = ranked[0]
    items = by_tech[top]
    conf, _, hit = max(
        items, key=lambda i: (RANK[i[0]], not i[2].get("implied"), bool(i[2]["version"]))
    )
    server = {"technology": top, "marker": hit["rule"].marker, "confidence": conf}
    fws = [(c, fw, h) for c, fw, h in items if fw]
    if fws:
        c, fw, h = max(fws, key=lambda i: (RANK[i[0]], i[1] not in GENERIC_FRAMEWORKS,
                                           bool(i[2]["version"])))  # fmt: skip
        server["framework"] = fw
        server["framework_marker"] = h["rule"].marker
        server["framework_confidence"] = c
        # The version a marker of this framework itself showed (`version` below is of any marker
        # of the technology, such as a Servlet header beside JSF).
        own = [x["version"] for _, f, x in fws if f == fw and x["version"] and not x.get("implied")]
        if own:
            server["framework_version"] = own[0]
    version = next((h["version"] for _, _, h in sorted(items, key=lambda i: -RANK[i[0]])
                    if h["version"] and not h.get("implied")), None)  # fmt: skip
    if version:
        server["version"] = version
    others = [{"technology": t, "marker": max(by_tech[t], key=lambda i: RANK[i[0]])[2]["rule"].marker,
               "confidence": max(by_tech[t], key=lambda i: RANK[i[0]])[0]}
              for t in ranked[1:] if score(t)[0] >= RANK[LIKELY]]  # fmt: skip
    if others:
        server["also"] = others
    if web_info:
        server["web_server"] = web_info
    return server


def _framework(
    pages: list[sanitize.PageInfo],
    scripts: list[tuple[str, str | None]],
    entries: list[Exchange] | None = None,
) -> dict:
    """What the recording shows of the technology, with the marker that proved each part.

    Keeps `framework`, `version`, `rendering`, `marker` and `ui_library` (the front end) and adds
    `server` (technology, framework, marker, confidence, web server), `libraries` and `evidence`.
    The front-end framework and the server technology are separate facts: a React front end on a
    Java server reports both.
    """
    entries = entries or []
    found: dict[str, dict] = {}
    for page in pages:
        for name, marker in page.markers.items():
            info = found.setdefault(name, {"marker": marker})
            if name == "angular" and marker:
                info["version"] = marker
                info["marker"] = f'ng-version="{marker}"'
    for path, text in scripts:
        for name, pattern, marker in JS_MARKERS:
            m = pattern.search(text or "")
            if m:
                info = found.setdefault(name, {"marker": marker})
                if m.groups() and not info.get("version"):
                    info["version"], info["marker"] = m.group(1), marker
        for name, pattern, marker in SCRIPT_NAMES:
            if name == "angular" and ANGULARJS_SCRIPT.search(path):
                continue  # angular.js is AngularJS 1.x, not Angular
            if name not in found and pattern.search(path.lower()):
                found[name] = {
                    "marker": f"{marker} {sanitize.route_pattern(path).rsplit('/', 1)[-1]}"
                }
    hits = _hits(_signals(entries, pages, scripts))
    server = _pick_server(hits)
    for h in hits:  # a certain server-side framework is the page framework, as markers show it
        r = h["rule"]
        if r.kind == "server" and r.conf == CERTAIN:
            key = {"ASP.NET Web Forms": "aspnet-webforms", "JSF": "jsf"}.get(r.fw or "")
            if key:
                found.setdefault(key, {"marker": r.marker})
    by_name: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        if h["rule"].kind in ("front", "lib", "ui"):
            by_name[h["rule"].name].append(h)
    best = {name: _best(items) for name, items in by_name.items()}

    result: dict = {}
    for srv in ("aspnet-webforms", "jsf"):
        if srv in found:
            label = {"aspnet-webforms": "ASP.NET Web Forms", "jsf": "JSF"}[srv]
            result = {"framework": label, "rendering": "server", "marker": found[srv]["marker"]}
            if server.get("framework") == label and server.get("framework_version"):
                result["version"] = server["framework_version"]
            break
    if server.get("framework") == "JSF":  # Mojarra or MyFaces, when the pages load its script
        impl = next((best[n] for n in JSF_IMPLEMENTATIONS if n in best), None)
        if impl:
            server["implementation"] = impl["rule"].name
            server["implementation_marker"] = impl["rule"].marker
    if not result:
        for client in ("angular", "react", "vue"):
            if client in found:
                result = {"framework": client, "version": found[client].get("version"),
                          "rendering": "client", "marker": found[client]["marker"]}  # fmt: skip
                break
    if not result:
        for name in ("AngularJS", "Ember", "Svelte"):
            if name in best and best[name]["rule"].kind == "front":
                h = best[name]
                result = {"framework": name.lower(), "version": h["version"], "rendering": "client",
                          "marker": h["rule"].marker}  # fmt: skip
                break
    if not result and any(p.has_content for p in pages):
        result = {"framework": "server-rendered", "rendering": "server",
                  "marker": "pages arrive with their content; no client framework marker"}  # fmt: skip

    libraries = []
    chosen = (result.get("framework") or "").lower()
    for name, h in sorted(best.items(), key=lambda kv: (-RANK[kv[1]["rule"].conf], kv[0])):
        r = h["rule"]
        if name.lower() == chosen:
            continue
        item = {"name": name, "marker": r.marker, "confidence": r.conf,
                "role": "ui" if r.kind == "ui" else "library"}  # fmt: skip
        if h["version"]:
            item["version"] = h["version"]
        libraries.append(item)
    ui = [x for x in libraries if x["role"] == "ui" and x["confidence"] != GUESS]
    ui.sort(key=lambda x: (x["name"] in UI_ORDER, -RANK[x["confidence"]]))
    if ui and result:
        result["ui_library"] = ui[0]["name"]

    legacy = [{"kind": "front", "name": name, "marker": info["marker"], "confidence": CERTAIN}
              for name, info in found.items()]  # fmt: skip
    return {**result, "server": server, "libraries": libraries,
            "evidence": _evidence(hits, legacy)}  # fmt: skip


# --- analysis --------------------------------------------------------------------------------------


@dataclass
class _ScreenAcc:
    pattern: str
    pages: list[sanitize.PageInfo] = field(default_factory=list)
    actions: dict[str, list[str]] = field(default_factory=dict)
    calls: dict[tuple[str, str], None] = field(default_factory=dict)


@dataclass
class _RouteAcc:
    method: str
    pattern: str
    query: dict[str, None] = field(default_factory=dict)
    body: dict[str, str] = field(default_factory=dict)
    responses: dict[str, Any] = field(default_factory=dict)
    ms: list[float] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


def _pct(values: list[float], p: float) -> int:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round(p * len(ordered) + 0.5)) - 1))
    return int(round(ordered[rank]))


def _error(status: int, data: Any) -> tuple[str | None, str | None]:
    """Code and message of an error body, in the shapes APIs commonly use."""
    if isinstance(data, dict):
        for key in ("detail", "error", "errors", "fault"):
            inner = data.get(key)
            if isinstance(inner, dict):
                data = inner
                break
            if isinstance(inner, str):
                return None, inner
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                first = inner[0]
                loc = first.get("loc") or first.get("field") or first.get("path")
                where = loc[-1] if isinstance(loc, list) and loc else loc
                msg = first.get("msg") or first.get("message") or "invalid"
                return "VALIDATION", f"{_field(str(where)) if where else 'input'}: {msg}"
        code = data.get("code") or data.get("error_code") or data.get("errorCode")
        msg = data.get("message") or data.get("msg") or data.get("title") or data.get("description")
        code = str(code) if code is not None and re.match(r"^[\w.-]{1,64}$", str(code)) else None
        return code, str(msg) if msg is not None else None
    if isinstance(data, str):
        return None, data
    return None, None


def _entity(pattern: str) -> str | None:
    """The resource a path names: `/api/cases/{id}/notes` -> `notes`, `/api/cases/search` -> `cases`."""
    segs = [s for s in pattern.split("/") if s]
    for seg in reversed(segs):
        if seg.startswith("{") or seg.lower() in ACTION_WORDS or seg.lower() == "api":
            continue
        if sanitize.VERSION_SEGMENT.match(seg):
            continue
        seg = re.sub(r"\.(json|xml|csv|aspx?|php|jsp|do|action|html?)$", "", seg, flags=re.I)
        name = re.sub(r"[^a-z0-9_]+", "_", seg.lower()).strip("_")
        return name or None
    return None


def list_key(data: Any):
    """The key under which an envelope answer holds its list of records (`items`, `Items`,
    `DATA`: `ITEM_KEYS` in any case), or None. `{"Items": [...], "LastEvaluatedKey": {...}}`
    (DynamoDB) is a list of items; its other keys describe the page, not records."""
    if not isinstance(data, dict):
        return None
    by_lower: dict[str, Any] = {}
    for k in data:
        by_lower.setdefault(str(k).lower(), k)
    for name in ITEM_KEYS:
        k = by_lower.get(name)
        if k is not None and isinstance(data[k], list):
            return k
    return None


def is_list_answer(data: Any) -> bool:
    """A list of records: a JSON array, or an envelope with one (`list_key`)."""
    return isinstance(data, list) or list_key(data) is not None


def _items(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        key = list_key(data)
        if key is not None:
            return [x for x in data[key] if isinstance(x, dict)]
        return [data]
    return []


def page_knowledge(doms, entries) -> tuple[set[str], set[str]]:
    """(data words, interface words) of a recording, lower-case, for masking its pages.

    Data words: what its data cells, field values and API answers show, and the short values of
    the answers as a whole (`sanitize.phrases_in`: they hold a space or a digit). Interface words: what
    its labels, buttons, headers, options and menus say. A word in both is the interface's."""
    doms = list(doms)
    pages = doms + [e.resp_text for e in entries if e.resp_text and e.resp_mime == "text/html"]
    known: set[str] = set()
    for e in entries:
        # API answers keep no text (resp_text is for documents and scripts), so their values come
        # from the parsed JSON. A name an API returned is data wherever a page shows it, even as
        # one title-case word in a heading or option.
        # A value shown whole (a product's or a customer's name in a link or a heading) is data
        # as a whole, lower-case words and short tokens included: `sanitize.phrases_in`.
        # Capitalised words of prose (descriptions) are not names: `names_in(prose=False)`.
        answer = e.resp_json if e.resp_json is not None and e.status < 400 else None
        if answer is None and e.resp_json is None and e.resp_text and "json" in (e.resp_mime or ""):
            try:
                answer = json.loads(e.resp_text)
            except ValueError:
                answer = None
        if answer is not None:
            known |= sanitize.names_in(answer, prose=False) | sanitize.phrases_in(answer)
    for text in pages:
        known |= sanitize.page(text).names
    # The interface's words, read knowing the data: an option or button that names a customer
    # the data shows is not the interface's vocabulary.
    vocab: set[str] = set()
    for text in pages:
        vocab |= sanitize.page(text, known).vocab
    return known - vocab, vocab


def analyse(app: str, path: Path, repo_root: Path, pages_dir: Path | None = None) -> AppModel:
    """The app model one recording shows. Writes value-free page snapshots when `pages_dir` is set."""
    from labctl.model_build import rel, same_id, screen_name

    rec = load(path)
    ev = f"har:{rec.id}"
    model = AppModel(app=app, evidence=[Evidence(id=ev, kind="har", ref=rec.digest, complete=[])])
    model.problems += [f"{ev}: {p}" for p in rec.problems]
    entries = exchanges(rec.har)

    hosts = {e.host for e in entries if e.kind in ("document", "form")}
    hosts |= {urlsplit(s.url).netloc.lower() for s in rec.steps if s.url.startswith("http")}
    if not hosts:
        hosts = {e.host for e in entries}
    siblings = api_hosts(hosts, entries)
    hosts |= siblings
    if siblings:
        n = sum(1 for e in entries if e.host in siblings and e.kind == "api")
        model.problems.append(
            f"{ev}: {n} API request(s) went to {len(siblings)} other host(s) of the pages' own "
            "site (a separate API origin, such as api. beside www.): analysed as this "
            "application's API on the same paths. The pages call that origin, not their own: "
            "say in BUILD-NOTES.md which address the rebuilt pages call and what serves it"
        )
    others = [e for e in entries if e.host not in hosts and e.kind == "api"]
    entries = [e for e in entries if e.host in hosts]
    if others:
        model.problems.append(
            f"{ev}: {len(others)} API request(s) to {len({e.host for e in others})} other host(s) "
            "were left out; if the application's API lives there, say so and analyse again"
        )
    # A host whose documents only ever loaded inside a frame (no step was on one of its pages) is
    # another application embedded in this one: its page is kept as the frame's content, its
    # own requests are not this application's routes or tables.
    step_hosts = {urlsplit(s.url).netloc.lower() for s in rec.steps if s.url.startswith("http")}
    framed = {e.host for e in entries if e.host not in step_hosts} if step_hosts else set()
    framed -= siblings  # an API origin of the same site is the pages' own API, not a frame
    framed_calls = sorted({f"{e.method} {sanitize.route_pattern(e.path)}" for e in entries
                           if e.host in framed and e.kind not in ("document", "asset")})  # fmt: skip
    framed_docs = {sanitize.route_pattern(e.path) for e in entries
                   if e.host in framed and e.kind == "document"}  # fmt: skip
    entries = [e for e in entries if e.host not in framed or e.kind in ("document", "asset")]

    screens: dict[str, _ScreenAcc] = {}
    served_grids = False  # a served document already holds a data grid: records came from a server

    def screen(url_path: str) -> _ScreenAcc:
        pattern = sanitize.route_pattern(url_path)
        return screens.setdefault(pattern, _ScreenAcc(pattern))

    # Screens from the steps and their snapshots, and the actions taken on them.
    # A step's address and snapshot are those after it; its action was taken on the page the
    # previous snapshot showed.
    # First pass: every word the recording shows as data (masked page text, typed values, response
    # bodies). A heading or option that repeats one of these words is showing data, not a label.
    dom_text = {
        id(step): step.dom.read_text(encoding="utf-8", errors="replace")
        for step in rec.steps
        if step.dom is not None
    }
    known, vocab = page_knowledge(dom_text.values(), entries)
    # What changes between snapshots of the same screen at the same place is data (a record's
    # name in a heading, say): a first reading of every snapshot, by screen.
    first: dict[str, list[sanitize.PageInfo]] = {}
    for step in rec.steps:
        url = urlsplit(step.url)
        if step.dom is not None and url.scheme in ("http", "https"):
            first.setdefault(sanitize.route_pattern(url.path or "/"), []).append(
                sanitize.page(dom_text[id(step)], known, vocab))  # fmt: skip
    for e in entries:
        if e.kind in ("document", "form") and 200 <= e.status < 300 and e.resp_mime == "text/html" \
                and e.resp_text is not None:  # fmt: skip
            first.setdefault(sanitize.route_pattern(e.path), []).append(
                sanitize.page(e.resp_text, known, vocab))  # fmt: skip
    vary = {pattern: sanitize.varying(pages) for pattern, pages in first.items()}

    timeline: list[tuple[datetime, str]] = []
    before: str | None = None
    for step in rec.steps:
        url = urlsplit(step.url)
        if url.scheme not in ("http", "https"):
            continue
        acc = screen(url.path or "/")
        if step.ts:
            timeline.append((step.ts, acc.pattern))
        if step.selector and step.action in ("click", "type", "select", "submit"):
            sel = _safe_selector(str(step.selector))
            acts = screens[before or acc.pattern].actions.setdefault(sel, [])
            if step.action not in acts:
                acts.append(step.action)
        if step.dom is not None:
            acc.pages.append(sanitize.page(dom_text[id(step)], known, vocab, vary.get(acc.pattern)))
            before = acc.pattern

    # Screens from the HTML documents; empty single-page-app shells give an address only.
    shells: list[sanitize.PageInfo] = []
    for e in entries:
        if e.kind not in ("document", "form") or not (200 <= e.status < 300):
            continue
        if e.resp_mime != "text/html" or e.resp_text is None:
            continue
        info = sanitize.page(e.resp_text, known, vocab, vary.get(sanitize.route_pattern(e.path)))
        served_grids = served_grids or bool(info.grids)
        if e.started:
            timeline.append((e.started, sanitize.route_pattern(e.path)))
        if info.has_content:
            screen(e.path).pages.append(info)
        else:
            shells.append(info)
            screen(e.path)
    timeline.sort(key=lambda t: t[0])
    # No request for data, no grid served, and the page changed by itself (or browser storage was
    # recorded): the state is not on the server (R07).
    browser_candidate = (
        not served_grids
        and not any(_is_data_call(e) for e in entries)
        and (_unexplained_changes(rec.steps, entries) or bool(storage_facts(rec.steps)["kinds"]))
    )

    scripts = [(e.path, e.resp_text) for e in entries if e.kind == "asset" and e.resp_text]
    all_pages = [p for s in screens.values() for p in s.pages]
    tech = _framework(all_pages + shells, scripts, entries)
    frontend = {k: tech[k] for k in FRONT_KEYS if k in tech}
    server_rendered = frontend.get("rendering") == "server"
    # Words seen as values, to mask names in error messages. A page shows the application's own
    # messages as text too; those words count only if a request or response also held them.
    shown = set().union(*(p.values for p in all_pages + shells))
    own = set().union(*(sanitize.words(_error(e.status, e.resp_json)[1] or "") for e in entries
                        if e.status >= 400))  # fmt: skip
    values = set().union(*(e.values for e in entries)) | (shown - own)

    def screen_at(e: Exchange) -> str | None:
        ref = urlsplit(e.request.get("referer", ""))
        path = referer_path(e.request.get("referer", ""), e.host)
        if ref.netloc.lower() in hosts and path:
            return sanitize.route_pattern(path)
        before = [p for t, p in timeline if e.started and t <= e.started]
        return before[-1] if before else None

    # Routes, rules, code lists and inferred tables from the API calls and form posts.
    routes: dict[tuple[str, str], _RouteAcc] = {}
    codes: dict[str, dict[str, None]] = defaultdict(dict)
    not_codes: set[str] = set()
    tables: dict[str, dict[str, Any]] = {}
    server_errors = 0
    for e in entries:
        is_page = e.kind in ("document", "form") and server_rendered
        if e.method not in METHODS or not (e.kind in ("api", "form") or is_page):
            continue
        pattern = sanitize.route_pattern(e.path)
        acc = routes.setdefault((e.method, pattern), _RouteAcc(e.method, pattern))
        acc.query.update(dict.fromkeys(e.query))
        for f in e.req_fields:
            name, _, kind = f.partition(": ")
            acc.body[name] = merge_shapes(acc.body[name], kind) if name in acc.body else kind
        if e.ms > 0:
            acc.ms.append(e.ms)
        if e.resp_json is not None:
            body = shape(e.resp_json)
            body = body if isinstance(body, dict) else {"(value)": body}
        elif e.resp_mime:
            body = {"(content)": e.resp_mime}
        else:
            body = {}
        key = str(e.status)
        acc.responses[key] = (
            merge_shapes(acc.responses[key], body) if key in acc.responses else body
        )
        if e.kind != "document":
            at = screen_at(e)
            if at is not None and at in screens:
                screens[at].calls[(e.method, pattern)] = None
        if 400 <= e.status < 500:
            code, message = _error(e.status, e.resp_json)
            if code or message:
                text = f"{e.status} {code}" if code else str(e.status)
                text += f": {sanitize.mask_message(message, values)}" if message else ""
                rule = Rule(text=text, where=[f"{e.method} {pattern}"], basis="observed",
                            status=e.status, code=code, **{"from": [ev]})  # fmt: skip
                _add_rule(model, rule)
        elif e.status >= 500:
            server_errors += 1
        if e.method == "GET" and 200 <= e.status < 300 and e.resp_json is not None:
            entity = _entity(pattern)
            if entity:
                _collect(entity, _items(e.resp_json), tables, codes, not_codes, acc.tables)
    if server_errors:
        model.problems.append(
            f"{ev}: {server_errors} response(s) were server errors (5xx); they are faults, not rules"
        )
    # An answer that only wraps another object (`{"user": {...}}`) names no column of its own:
    # that is not a table (a `CREATE TABLE` without columns does not even apply).
    for name in [n for n, cols in tables.items() if not cols]:
        del tables[name]
        for acc in routes.values():
            if name in acc.tables:
                acc.tables.remove(name)
    # A change sent to a resource that a read showed belongs to the same table.
    for (method, pattern), acc in routes.items():
        entity = _entity(pattern)
        if method != "GET" and entity in tables and entity not in acc.tables:
            acc.tables.append(entity)
    _rpc_tables(entries, routes, tables, codes, not_codes, screens)

    for (method, pattern), acc in routes.items():
        timing = None
        if acc.ms:
            timing = Timing(count=len(acc.ms), p50_ms=_pct(acc.ms, 0.5), p95_ms=_pct(acc.ms, 0.95),
                            max_ms=int(round(max(acc.ms))))  # fmt: skip
        model.routes.append(
            Route(
                method=method,
                path=pattern,
                basis="observed",
                **{"from": [ev]},
                query=list(acc.query),
                body=[f"{k}: {type_of(v)}" for k, v in acc.body.items()],
                tables=acc.tables,
                responses=dict(sorted(acc.responses.items())),
                timings=timing,
            )
        )

    # A grid on a page of an app whose data is in the browser is not a table (R07). Server-rendered
    # pages are server evidence, so they never take this branch.
    html_tables = (
        []
        if browser_candidate
        else _html_tables(screens, {_norm(n) for n in tables}, model.problems, ev)
    )
    # A form or grid whose fields are mostly the columns of a table the answers already showed is
    # that table seen through a screen (/billing/account's form is `invoices`), not another one.
    answered = [{_norm(c) for c in cols} for cols in tables.values()]
    html_tables = [t for t in html_tables
                   if not any(len({_norm(c) for c in t.columns} & cols) * 2 >= len(t.columns)
                              for cols in answered)]  # fmt: skip
    for name, cols in tables.items():
        model.tables.append(
            Table(
                name=name,
                basis="inferred",
                **{"from": [ev]},
                seen_on=[f"{m} {p}" for (m, p), acc in routes.items() if name in acc.tables],
                columns=[
                    Column(name=c, type=_sql_type(s), nullable=c != "id", primary_key=c == "id")
                    for c, s in cols.items()
                ],
            )
        )
    model.codes = {
        k: sorted(v)
        for k, v in sorted(codes.items())
        if k not in not_codes and 0 < len(v) <= MAX_CODES
    }
    for t in html_tables:  # weakest evidence: JSON and DDL name the same entity better
        first = next(iter(t.columns))
        pk = re.search(r"(^|_)(id|no|number|code|ref|key)$", first) is not None
        model.tables.append(
            Table(
                name=t.name,
                basis="assumed",
                **{"from": [ev]},
                seen_on=[f"page layout of {s}" for s in sorted(t.screens)],
                columns=[
                    Column(
                        name=c,
                        type=SQL_FOR[i["type"]],
                        nullable=not (pk and c == first),
                        primary_key=pk and c == first,
                    )
                    for c, i in t.columns.items()
                ],
            )
        )
        for col, values in t.codes.items():
            model.codes.setdefault(f"{t.name}.{col}", sorted(values))

    # Documents of an embedded application: which screen's frame shows them.
    embedded: dict[str, dict] = {}
    for pattern in sorted(framed_docs):
        found = next(((acc.pattern, f["selector"]) for acc in screens.values()
                      if acc.pattern not in framed_docs for info in acc.pages for f in info.frames
                      if sanitize.route_pattern(f["src"] or "/") == pattern), None)  # fmt: skip
        embedded[pattern] = {"in": found[0] if found else None,
                             "element": found[1] if found else None,
                             "origin": "another origin", "calls": framed_calls}  # fmt: skip
    if embedded:
        model.problems.append(
            f"{ev}: {', '.join(sorted(embedded))} loaded only inside a frame from another origin "
            f"(an embedded application); its own requests ({', '.join(framed_calls) or 'none'}) "
            "are not this application's routes"
        )

    # Screens.
    pages_dir = Path(pages_dir) if pages_dir is not None else None
    no_content = []
    for acc in screens.values():
        name = screen_name(acc.pattern)
        ids, classes = set(), set()
        elements: dict[str, Element] = {}
        for info in acc.pages:
            ids |= info.ids
            classes |= info.classes
            for el in info.elements:
                sel = _selector(el)
                have = elements.get(sel)
                if have is None:
                    elements[sel] = Element(
                        selector=sel, label=el["label"], tag=el["tag"], name=el["name"],
                        type=el["type"], classes=el["classes"], options=el["options"],
                        option_values=_option_values(el), rules=el.get("rules") or {},
                        basis="observed", **{"from": [ev]},
                    )  # fmt: skip
                else:
                    have.rules = have.rules or el.get("rules") or {}
                    have.label = have.label or el["label"]
                    for o, v in zip(el["options"], _option_values(el) or [None] * len(el["options"]),
                                    strict=False):  # fmt: skip
                        if o not in have.options:
                            have.options = have.options + [o]
                            if have.option_values:
                                have.option_values = have.option_values + [v or ""]
                    have.classes = have.classes + [
                        c for c in el["classes"] if c not in have.classes
                    ]
        for sel, actions in acc.actions.items():
            el = elements.get(sel) or next(
                (x for x in elements.values() if same_id(x.selector, sel)), None
            )
            if el is None:
                el = elements[sel] = Element(selector=sel, basis="observed", **{"from": [ev]})
            el.actions = el.actions + [a for a in actions if a not in el.actions]
        files = []
        if acc.pages and pages_dir is not None:
            # the fullest snapshot: most ids, then most classes (a list with its rows), then latest
            best = max(enumerate(acc.pages),
                       key=lambda p: (len(p[1].ids), len(p[1].classes), p[0]))[1]  # fmt: skip
            pages_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip("-.")[:80] or "page"
            out = (
                pages_dir / f"{safe_name}.html"
            )  # no ; : or other characters a file system mangles
            said = (
                "Structure, ids, classes, styles and the application's own interface text (labels, "
                "headings, buttons, options, links, messages) are real; data (field values, table "
                "and description cells, names, numbers, dates, e-mail addresses) is masked"
                if best.mode == "interface"
                else "Structure, ids, classes and styles are real; every text and value is masked "
                "(strict page-text mode)"
            )
            framed = (
                " A <tl-frame> element is not part of the page: the recorder put there the document "
                "the frame before it showed (its own screen in the brief); do not build it or copy "
                "its ids."
                if "<tl-frame" in best.html
                else ""
            )
            out.write_text(
                f"<!-- {acc.pattern}: value-free snapshot from {ev}. {said} (letters x/X, digits 9)."
                f"{framed} -->\n" + best.html,
                encoding="utf-8",
                newline="\n",  # generated files are LF on every OS (they go into Git and images)
            )
            where = rel(out, repo_root)
            if Path(where).is_absolute():  # never write a machine's absolute paths into the model
                where = f"{pages_dir.name}/{out.name}"
            files = [where]
        if not acc.pages:
            no_content.append(acc.pattern)
        messages: list[dict] = []
        frames: list[dict] = []
        grids: list[dict] = []
        for info in acc.pages:
            for m in info.messages:
                item = {"selector": m["selector"], "text": m["text"]}
                if item not in messages and len(messages) < 40:
                    messages.append(item)
            for f in info.frames:
                if f not in frames:
                    frames.append(f)
            for g in info.grids:
                if g.get("framed"):
                    continue
                sel = f"table#{g['id']}" if g.get("id") else "table"
                have = next((x for x in grids if x["selector"] == sel), None)
                if have is None:
                    grids.append({"selector": sel, "headers": g["headers"], "types": g["types"],
                                  "rows": g["rows"], "row_class": g.get("row_class") or None,
                                  "cell_classes": g.get("cell_classes") or []})  # fmt: skip
                else:
                    have["rows"] = max(have["rows"], g["rows"])
        model.screens.append(
            Screen(
                name=name,
                path=acc.pattern,
                basis="observed",
                **{"from": [ev]},
                files=files,
                ids=sorted(ids),
                classes=sorted(classes),
                elements=list(elements.values()),
                calls=[Call(method=m, path=p) for m, p in acc.calls],
                snapshots=len(acc.pages),
                messages=messages,
                frames=frames,
                embedded=embedded.get(acc.pattern),
                grids=grids,
            )
        )
    if no_content:
        why = (
            "the HAR's pages are empty single-page-app shells (JavaScript builds the screen) and "
            "the recording has no page snapshots"
            if shells
            else "the recording holds no page content for them"
        )
        model.problems.append(
            f"{ev}: screen(s) {', '.join(no_content)} are known only by address: {why}. Their "
            "elements are unknown; record again with `labctl.recorder` to capture the DOM"
        )

    # Always set, even when nothing was found: the brief then says "not identified" in plain words.
    model.stack = Stack(
        frontend={k: v for k, v in frontend.items() if v is not None} or None,
        pinned={"react-dom": frontend["version"]}
        if frontend.get("framework") == "react" and frontend.get("version")
        else {"@angular/core": frontend["version"]}
        if frontend.get("framework") == "angular" and frontend.get("version")
        else {},
        server=tech["server"] or None,
        libraries=tech["libraries"],
        detected=tech["evidence"],
        data=data_location(
            entries,
            rec.steps,
            scripts
            + [(e.path, e.resp_text) for e in entries if e.kind == "document" and e.resp_text],
            served_grids=served_grids,
        ),
        **{"from": [ev]},
    )
    if not frontend:
        model.problems.append(f"{ev}: no framework marker found in the recorded pages or scripts")
    return model


# --- where the data lives (R07) ------------------------------------------------------------------
#
# A recording shows requests, page snapshots and (when the recorder adds it) browser storage. It
# cannot show a server's database. What it can show is whether the application sent its state to
# its own server: state-changing requests (server), or none while the page changed (browser).
# Without either the answer is unknown, and unknown stays unknown: no table, no database is invented.

STATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
INTERACTIONS = {"click", "dblclick", "type", "fill", "select", "check", "uncheck", "press",
                "keypress", "submit"}  # fmt: skip
TELEMETRY = re.compile(
    r"/(collect|analytics|beacon|track|tracking|telemetry|metrics|csp-report|log|logs|"
    r"gtag|ping|rum|vitals|sentry)(/|$)",
    re.I,
)
HEALTHY_PATH = re.compile(r"/(health|healthz|ready|status|version|config)(\.json)?$", re.I)
STORAGE_REF = re.compile(r"\b(localStorage|sessionStorage)\b")
STORAGE_KEY = re.compile(
    r"\b(localStorage|sessionStorage)\s*(?:\.\s*(?:getItem|setItem|removeItem)\s*\(\s*|\[\s*)"
    r"([\"'`])([\w.:@/-]{1,80})\2"
)
INDEXED_DB = re.compile(r"\bindexedDB\b")
SECRET_KEY = re.compile(r"token|secret|passw|api[-_]?key|session|cookie|auth|credential|jwt", re.I)
MAX_KEYS = 30
SLACK = timedelta(seconds=1)  # a step is logged a moment after its request


def _is_data_call(e: Exchange) -> bool:
    """A request the application made to its own origin for its data (not a page, an asset,
    telemetry or a health probe)."""
    if e.kind not in ("api", "form") and not (e.method in STATE_METHODS and e.kind != "asset"):
        return False
    if TELEMETRY.search(e.path) or (e.method == "GET" and HEALTHY_PATH.search(e.path)):
        return False
    return True


def _unexplained_changes(steps: list[Step], entries: list[Exchange]) -> bool:
    """Some click or typing left the page different from the snapshot before it, and no request to
    the app's own origin (a page, a form, a call; not an asset or a beacon) started while that step
    was the latest: the page changed by itself. A link that loads another page is explained by its
    request. Steps without a time or a snapshot never count."""
    requests = sorted(
        e.started for e in entries
        if e.started and e.kind != "asset" and not TELEMETRY.search(e.path)
    )  # fmt: skip
    previous: bytes | None = None
    for n, step in enumerate(steps):
        current = None
        if step.dom is not None:
            try:
                current = step.dom.read_bytes()
            except OSError:
                current = None
        if step.action in INTERACTIONS and None not in (previous, current) and previous != current:
            later = [x.ts for x in steps[n + 1 :] if x.ts]
            end = later[0] if later else None
            if step.ts and not any(
                step.ts - SLACK <= t and (end is None or t < end) for t in requests
            ):
                return True
        if current is not None:
            previous = current
    return False


def _stored_shape(value: Any) -> Any:
    """The shape of a stored value: JSON text is parsed, anything else is a string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return "string"
    return shape(value)


def storage_facts(steps: list[Step]) -> dict:
    """Browser storage the recorder captured: {kinds, keys, shape}. Values are never kept, only
    key names and the shape of each stored record; the shape of a credential-like key's value is
    not described at all. Today's recorder writes no `storage` on its steps, so this is empty
    unless a recording from a newer recorder (or a person) adds it."""
    kinds: dict[str, None] = {}
    keys: dict[str, None] = {}
    shapes: dict[str, Any] = {}
    for step in steps:
        for kind in ("localStorage", "sessionStorage"):
            items = (step.storage or {}).get(kind)
            if not isinstance(items, dict) or not items:
                continue
            kinds[kind] = None
            for key, value in items.items():
                name = _field(str(key))
                label = f"{kind}:{name}"
                keys[label] = None
                if SECRET_KEY.search(name):
                    shapes[label] = "(credential-like key: not described)"
                else:
                    new = _stored_shape(value)
                    shapes[label] = merge_shapes(shapes[label], new) if label in shapes else new
        stores = (step.storage or {}).get("indexedDB")
        if isinstance(stores, list) and stores:
            kinds["indexedDB"] = None
            for item in stores:
                if isinstance(item, dict) and item.get("db"):
                    label = "indexedDB:" + _field(str(item["db"]))
                    keys[label] = None
                    names = item.get("stores")
                    names = list(names) if isinstance(names, (dict, list)) else []
                    shapes[label] = {"stores": [_field(str(n)) for n in names][:20]}
    return {"kinds": list(kinds), "keys": list(keys)[:MAX_KEYS], "shape": shapes}


def data_location(
    entries: list[Exchange],
    steps: list[Step],
    scripts: list[tuple[str, str]],
    *,
    served_grids: bool = False,
) -> dict:
    """Where the application's business data lives, with the evidence for it (R07).

    Five findings, in `persistence`; `kind` stays browser | server | unknown for the callers that
    decide on a database. The absence of server writes is NOT evidence of localStorage:

    - `server`: a state-changing request to the app's own origin was seen, or a response carries
      records (JSON items, or a data grid in a served page).
    - `storage` (kind browser): browser storage keys were observed in the recording, so they
      changed with the steps (or were still there after a reload).
    - `navigation` (kind browser): values travel in URLs (a query string on a page request) with
      no server write; nothing shows they are kept anywhere.
    - `transient` (kind browser): a click or typing changed the page with no request and no
      storage was observed. Labelled "browser (transient or unknown persistence)"; scripts that
      mention localStorage/sessionStorage are listed as `referenced`, which proves nothing.
    - `unknown`: none of this can be shown.
    """
    calls = [e for e in entries if _is_data_call(e)]
    writes = [e for e in calls if e.method in STATE_METHODS]
    records = [e for e in calls if e.method == "GET" and 200 <= e.status < 300
               and _items(e.resp_json)]  # fmt: skip
    seen = storage_facts(steps)
    referenced: dict[str, None] = {}
    keys: dict[str, None] = {}
    for _path, text in scripts:
        for m in STORAGE_REF.finditer(text or ""):
            referenced[m.group(1)] = None
        for m in STORAGE_KEY.finditer(text or ""):
            keys[f"{m.group(1)}:{m.group(3)}"] = None
        if text and INDEXED_DB.search(text):
            referenced["indexedDB"] = None
    changed = not served_grids and _unexplained_changes(steps, entries)
    navigation = _query_fields(entries)
    reloaded = _kept_after_reload(steps)
    out: dict[str, Any] = {"kind": "unknown", "persistence": "unknown", "storage": seen["kinds"],
                           "referenced": list(referenced), "keys": list(seen["keys"]),
                           "shape": seen["shape"], "evidence": []}  # fmt: skip
    ev: list[str] = out["evidence"]
    if writes or records or served_grids:
        out["kind"] = "server"
        out["persistence"] = "server"
        if writes:
            where = sorted({f"{e.method} {sanitize.route_pattern(e.path)}" for e in writes})
            ev.append(f"{len(writes)} state-changing request(s) to the app's own origin: "
                      + ", ".join(where[:5]) + (" ..." if len(where) > 5 else ""))  # fmt: skip
        if records:
            where = sorted({sanitize.route_pattern(e.path) for e in records})
            ev.append("responses carry records: GET " + ", ".join(where[:5]))
        if served_grids and not (writes or records):
            ev.append("the served pages already hold data grids: records came from the server")
        if seen["kinds"]:
            ev.append("browser storage was also recorded: " + ", ".join(seen["kinds"]))
    elif not calls and (seen["kinds"] or changed or navigation):
        out["kind"] = "browser"
        ev.append("no state-changing request and no data request to the app's own origin in the "
                  "whole recording")  # fmt: skip
        if seen["kinds"]:
            out["persistence"] = "storage"
            n = sum(1 for s in steps if s.storage)
            ev.append("browser storage observed with data: " + ", ".join(seen["kinds"])
                      + f" (keys changed at {n} step(s))")  # fmt: skip
            if reloaded:
                ev.append("the keys were still present at a step after a reload")
        else:
            out["persistence"] = "transient" if changed else "navigation"
            if changed:
                n = sum(1 for s in steps if s.action in INTERACTIONS)
                ev.append(f"the page changed after interaction ({n} click/typing step(s) "
                          "recorded) with no request")  # fmt: skip
            if navigation:
                ev.append("values travel in the address (query fields: " + ", ".join(navigation[:8])
                          + "); that is navigation state, not stored data")  # fmt: skip
            ev.append("browser storage: not observed changing or surviving a reload, so "
                      "persistence is not shown: absence of server writes is not evidence of "
                      "localStorage")  # fmt: skip
        if referenced:
            ev.append("scripts reference " + ", ".join(referenced)
                      + " (a script mention is not proof of what is stored)")  # fmt: skip
        out["script_keys"] = list(keys)[:MAX_KEYS]
    else:
        why = (
            "the recording has no page snapshots, so a change of state cannot be seen"
            if not any(s.dom for s in steps)
            else "no interaction changed the page and no state-changing request was seen"
        )
        ev.append(f"not shown: {why}")
        if referenced:
            ev.append("scripts reference " + ", ".join(referenced))
    out["label"] = DATA_LABELS[out["persistence"]].format(
        storage=", ".join(seen["kinds"]) or "storage"
    )
    return out


DATA_LABELS = {
    "server": "server (observed writes or served records)",
    "storage": "browser storage (observed: {storage})",
    "navigation": "browser (navigation or form state in URLs; persistence not shown)",
    "transient": "browser (transient or unknown persistence)",
    "unknown": "unknown persistence",
}


def _query_fields(entries: list[Exchange]) -> list[str]:
    """Query field names the app's own pages were requested with: values carried in URLs."""
    names: dict[str, None] = {}
    for e in entries:
        if e.kind == "document" and e.query:
            for q in e.query:
                names[q] = None
    return list(names)


def _kept_after_reload(steps: list[Step]) -> bool:
    """A storage key recorded at a step after a reload/goto step that followed its first sight."""
    seen_keys = False
    reload_after = False
    for step in steps:
        has = any((step.storage or {}).get(k) for k in ("localStorage", "sessionStorage"))
        if has and reload_after:
            return True
        if has:
            seen_keys = True
        elif seen_keys and step.action in ("reload", "goto", "navigate"):
            reload_after = True
    return False


# --- tables from HTML grids and forms ------------------------------------------------------------
#
# Server-rendered applications (JSP, Web Forms, PHP) show their data in HTML tables and detail forms
# and answer no JSON. A data grid (a header row over repeated rows) is a candidate table: columns
# are its header labels, types are guessed from the shape of each column's cells, never kept. A
# detail or edit form adds the columns its fields name. Both are weak evidence ("assumed"): JSON
# responses and DDL, when they name the same entity, win.

PAGE_WORDS = {"search", "find", "list", "lists", "listing", "all", "query", "browse", "manage",
              "management", "maintenance", "edit", "detail", "details", "view", "show", "display",
              "new", "create", "add", "update", "delete", "remove", "index", "default", "home",
              "main", "form", "page", "results", "result", "report", "reports", "grid", "table",
              "entry", "screen", "the", "a", "an", "of", "for", "my", "app", "action", "do", "faces",
              "jsp", "php", "asp", "aspx", "servlet", "pages", "admin", "export", "filter"}  # fmt: skip
NOT_ENTITY_PAGE = {"search", "find", "filter", "query", "login", "logon", "signin", "sign", "logout",
                   "password", "forgot", "reset", "auth"}  # fmt: skip
ID_PREFIX = re.compile(r"^(gv|gvw|grd|grid|dg|dgv|tbl|table|lst|list|rg|rpt|dt|rep)(?=[A-Z0-9_])")
ID_SUFFIX = re.compile(r"_?(grid|gridview|table|list|view|results?|repeater|data)$")
PLACEHOLDER = re.compile(r"^(-+|\.+|select\b.*|choose\b.*|please\b.*|all|any|none|n/?a|)$", re.I)
FIELD_NOISE = re.compile(
    r"^(j_?id|__|javax|jakarta|_token|csrf|authenticity|viewstate|ctl\d+$|search|q$|btn|cmd|submit|"
    r"action|page$|sort|_method|utf8|commit|form$|ice\.|wicket)",
    re.I,
)
FIELD_PREFIX = re.compile(r"^(txt|ddl|chk|cbx|rbl?|cbo|cb|lst|drp|tb|dd|ta|hid|lbl|cal|dtp|sel|inp|"
                          r"dropdown|text|input)(?=[A-Z_])")  # fmt: skip
COLUMN_TYPE_HINTS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"(^|_)(amount|price|total|balance|rate|salary|cost|fee|tax|charge|limit)(_|$)"),
        "decimal",
    ),
    (re.compile(r"(^|_)(qty|quantity|count|age|number_of|years|days)(_|$)"), "integer"),
    (re.compile(r"(_at|_time|timestamp)$"), "datetime"),
    (re.compile(r"(^|_)(date|dob|birth|expiry|due|start|end)(_|$)|_on$"), "date"),
    (re.compile(r"^(is|has|active|enabled|flag)(_|$)"), "boolean"),
]
SQL_FOR = {"integer": "INTEGER", "decimal": "NUMERIC", "date": "DATE", "datetime": "TIMESTAMP",
           "boolean": "BOOLEAN", "text": "TEXT"}  # fmt: skip
NOT_A_COLUMN = {"actions", "action", "select", "edit", "delete", "view", "details", "detail"}


def snake(text: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _tokens(text: str) -> list[str]:
    return [t for t in snake(text).split("_") if t]


def _strip_words(tokens: list[str]) -> list[str]:
    while tokens and tokens[0] in PAGE_WORDS:
        tokens = tokens[1:]
    while tokens and tokens[-1] in PAGE_WORDS:
        tokens = tokens[:-1]
    return tokens


def _norm(name: str) -> str:
    """A name to compare entities by: singular, lower-case (`customers` and `customer` match)."""
    n = name.lower()
    if n.endswith("ies") and len(n) > 4:
        return n[:-3] + "y"
    if n.endswith(("sses", "xes", "ches", "shes")):
        return n[:-2]
    if n.endswith("s") and not n.endswith(("ss", "us", "is")) and len(n) > 3:
        return n[:-1]
    return n


def _entity_from_id(gid: str | None) -> str | None:
    """`ctl00_Main_gvOrders` -> `orders`; a generic id (`GridView1`, `results`) names nothing."""
    if not gid:
        return None
    for part in reversed([p for p in re.split(r"[_$:.]+", gid) if p]):
        part = ID_PREFIX.sub("", part)
        tokens = _strip_words(_tokens(ID_SUFFIX.sub("", snake(re.sub(r"\d+$", "", part)))))
        name = "_".join(tokens)
        if name and len(name) > 1 and not name.startswith(("ctl", "content")):
            return name
    return None


def _entity_from_path(pattern: str) -> str | None:
    for seg in reversed([s for s in pattern.split("/") if s]):
        if seg.startswith("{") or sanitize.VERSION_SEGMENT.match(seg) or seg.lower() == "api":
            continue
        seg = re.sub(r"\.\w{2,5}$", "", seg)
        tokens = _strip_words(_tokens(seg))
        if tokens and len("_".join(tokens)) > 1:
            return "_".join(tokens)
    return None


def _entity_from_heading(texts: list[str]) -> str | None:
    for text in texts:
        head = text.split(":", 1)[0]
        tokens = _strip_words(_tokens(re.sub(r"[^A-Za-z ]+", " ", head)))
        if tokens and len(tokens) <= 3 and not any(set(t) <= set("x") for t in tokens):
            return "_".join(tokens)
    return None


def _page_is_not_entity(pattern: str, headings: list[str]) -> bool:
    words = set(_tokens(pattern.rsplit("/", 1)[-1]))
    for h in headings[:2]:
        words |= set(_tokens(h.split(":", 1)[0]))
    return bool(words & NOT_ENTITY_PAGE)


def _column_name(label: str | None, name: str | None, ident: str | None) -> str | None:
    """A column name from a label (`Account number` -> `account_number`) or a field name."""
    if label:
        text = re.sub(r"[*:?]+$", "", label.strip())
        tokens = _tokens(re.sub(r"[^A-Za-z0-9 _/-]+", " ", text))
        if 0 < len(tokens) <= 5 and not any(set(t) <= set("x9") for t in tokens):
            return "_".join(tokens)
    for raw in (name, ident):
        parts = [p for p in re.split(r"[$:.]+", raw or "") if p]
        if not parts:
            continue
        part = FIELD_PREFIX.sub("", re.sub(r"\[\]$", "", parts[-1]))
        col = snake(part)
        if col and not FIELD_NOISE.match(col) and not FIELD_NOISE.match(part) and len(col) <= 40:
            return col
    return None


def _guess_type(col: str, kind: str | None) -> str:
    if kind in ("integer", "decimal", "date", "datetime", "boolean"):
        return kind
    for rx, t in COLUMN_TYPE_HINTS:
        if rx.search(col):
            return t
    return "text"


def _merge_type(a: str, b: str) -> str:
    if a == b or b == "text":
        return a
    if a == "text":
        return b
    if {a, b} <= {"integer", "decimal"}:
        return "decimal"
    if {a, b} <= {"date", "datetime"}:
        return "datetime"
    return "text"


def _option_values(el: dict) -> list[str]:
    """The codes a select's options post, in option order ([] when the page gave none)."""
    values = el.get("option_values") or []
    if len(values) != len(el.get("options") or []) or not any(values):
        return []
    return [v or "" for v in values]


def _code(option: str) -> str | None:
    """An option's label as a code (`In progress` -> `IN_PROGRESS`); None for placeholders and
    for anything that looks like data (digits, masked text)."""
    option = " ".join(option.split())
    if PLACEHOLDER.match(option) or re.search(r"\d", option) or set(option) <= set("Xx9 ._'-"):
        return None
    code = re.sub(r"[^A-Z]+", "_", option.upper()).strip("_")
    return code if CODE_VALUE.match(code) and len(code) <= 32 else None


class _HtmlTable:
    def __init__(self, name: str):
        self.name = name
        self.columns: dict[str, dict] = {}  # column -> {"type", "optional"}
        self.codes: dict[str, dict[str, None]] = {}  # column -> code values
        self.screens: set[str] = set()

    def add(self, col: str, kind: str, optional: bool = True) -> None:
        have = self.columns.get(col)
        if have is None:
            self.columns[col] = {"type": kind, "optional": optional}
        else:
            have["type"] = _merge_type(have["type"], kind)
            have["optional"] = have["optional"] and optional


def _html_tables(screens: dict[str, _ScreenAcc], skip: set[str], problems: list[str],
                 ev: str) -> list[_HtmlTable]:  # fmt: skip
    """Candidate tables from the data grids and detail forms of every recorded page. `skip` holds
    the (normalised) names JSON or DDL evidence already gives; those entities are left alone."""
    found: dict[str, _HtmlTable] = {}

    def table(name: str, pattern: str) -> _HtmlTable | None:
        key = _norm(name)
        if key in skip:
            return None
        t = found.setdefault(key, _HtmlTable(name))
        t.screens.add(pattern)
        return t

    for acc in screens.values():
        for info in acc.pages:
            heads = info.headings or ([info.title] if info.title else [])
            used: dict[str, int] = {}
            for grid in info.grids:
                if grid.get("framed"):
                    continue  # the grid of an embedded document: not this application's data
                entity =(_entity_from_id(grid["id"]) or _entity_from_path(acc.pattern)
                          or _entity_from_heading(heads))  # fmt: skip
                if not entity:
                    problems.append(
                        f"{ev}: a data grid on {acc.pattern} has no usable name; left out"
                    )
                    continue
                n = used[entity] = used.get(entity, 0) + 1
                t = table(entity if n == 1 else f"{entity}_{n}", acc.pattern)
                if t is None:
                    continue
                for label, kind, optional in zip(grid["headers"], grid["types"], grid["optional"],
                                                 strict=True):  # fmt: skip
                    col = _column_name(label, None, None) if label else None
                    if col and col not in NOT_A_COLUMN:
                        t.add(col, _guess_type(col, kind), optional)
            _form_columns(info, acc.pattern, heads, table)
    return [t for t in found.values() if len(t.columns) >= 2]


def _form_columns(info: sanitize.PageInfo, pattern: str, heads: list[str], table) -> None:
    """Columns from a detail or edit form: field labels or names; selects and radios as code lists."""
    fields = [f for f in info.fields if not f["in_grid"] and (f["name"] or f["id"])]
    if len(fields) < 2 or _page_is_not_entity(pattern, heads):
        return
    if any(e["type"] == "password" for e in info.elements):
        return
    entity = _entity_from_path(pattern) or _entity_from_heading(heads)
    t = table(entity, pattern) if entity else None
    if t is None:
        return
    groups: dict[str, dict] = {}
    for f in fields:
        col = _column_name(f["label"] if f["type"] != "radio" else None, f["name"], f["id"])
        if not col or col in NOT_A_COLUMN:
            continue
        g = groups.setdefault(col, {"kind": f["type"] or f["tag"], "options": [], "values": []})
        g["options"] += f["options"]
        g["values"] += [v for v, o in zip(_option_values(f), f["options"], strict=False)
                        if v and not PLACEHOLDER.match(" ".join(o.split()))]  # fmt: skip
        if f["type"] == "radio" and f["label"]:
            g["options"].append(f["label"])
    for col, g in groups.items():
        kind = {"number": "integer", "date": "date", "datetime-local": "datetime",
                "checkbox": "boolean"}.get(g["kind"])  # fmt: skip
        t.add(col, _guess_type(col, kind))
        codes = [_code(o) for o in g["options"] if not PLACEHOLDER.match(" ".join(o.split()))]
        values = list(dict.fromkeys(g["values"]))
        if (
            values
            and len(values) == len(dict.fromkeys(codes))
            and all(
                CODE_VALUE.match(v.upper()) or re.fullmatch(r"[a-z][a-z0-9_]*", v) for v in values
            )
        ):
            codes = values  # the codes the options post, as the page has them, not their labels
        if (2 <= len(codes) <= MAX_CODES and all(codes) and not NOT_CODE_FIELD.search(col)
                and col not in ("id",)):  # fmt: skip
            t.codes.setdefault(col, {}).update(dict.fromkeys(codes))


# A remote-procedure path names a verb and the resource it acts on in one word: `viewcart`,
# `addtocart`, `deleteitem`, `get_orders`.
RPC_PATH = re.compile(
    r"^(?:view|get|list|load|fetch|read|show|find|search|add|create|new|insert|save|update|edit|"
    r"set|put|delete|del|remove|rm)(?:_?(?:to|from|in|into))?_?(?P<noun>[a-z][a-z_]{2,})$"
)
GENERIC_NOUNS = {"item", "items", "row", "rows", "entry", "record", "records", "line", "lines"}


def rpc_noun(pattern: str) -> str | None:
    """The resource a remote-procedure path acts on: `/addtocart` -> `cart`, `/viewcart` ->
    `cart`, `/deleteitem` -> `item`. None for a path that is not a verb and a noun."""
    last = next((s for s in reversed(pattern.split("/")) if s and not s.startswith("{")), "")
    m = RPC_PATH.match(last.lower())
    return m.group("noun").strip("_") if m else None


def _rpc_tables(entries, routes, tables, codes, not_codes, screens) -> None:
    """Tables a remote-procedure API shows through POST answers (no GET lists them):

    - an answer whose records have exactly the columns of a known table is a read of that table
      (`POST /bycat` filtering `entries`, `POST /view` reading one of them);
    - another list answer of a verb-and-noun path is a table of that noun (`POST /viewcart`
      listing `cart`), and every verb on the same noun writes it (`/addtocart`);
    - a verb on a generic noun (`/deleteitem`) acts on the table such a list read showed on the
      same screen."""
    read: dict[tuple[str, str], str] = {}
    for e in entries:
        if e.method == "GET" or not (200 <= e.status < 300) or e.resp_json is None:
            continue
        key = (e.method, sanitize.route_pattern(e.path))
        if key not in routes:
            continue
        items = _items(e.resp_json)
        cols = {str(k) for i in items[:20] for k, v in i.items() if not isinstance(v, (dict, list))}
        same = next((t for t, have in tables.items() if len(cols) >= 2 and cols ==
                     {k for k in have if not k.startswith("{")}), None)  # fmt: skip
        noun = rpc_noun(key[1])
        if same is None and is_list_answer(e.resp_json) and noun and noun not in GENERIC_NOUNS:
            same = _entity("/" + noun)
        if same is None:
            continue
        _collect(same, items, tables, codes, not_codes, routes[key].tables)
        read[key] = same
    for key, acc in routes.items():
        if key[0] == "GET" or acc.tables:
            continue
        noun = rpc_noun(key[1])
        table = _entity("/" + noun) if noun else None
        if table in tables:
            acc.tables.append(table)
        elif noun in GENERIC_NOUNS:
            near = {read[k] for scr in screens.values() if key in scr.calls
                    for k in scr.calls if k in read and rpc_noun(k[1])}  # fmt: skip
            if len(near) == 1:
                acc.tables.append(near.pop())


def _collect(entity, items, tables, codes, not_codes, route_tables) -> None:
    """Columns of an inferred table, nested objects as their own tables, and code values."""
    if not items:
        return
    cols = tables.setdefault(entity, {})
    if route_tables is not None and entity not in route_tables:
        route_tables.append(entity)
    for item in items[:200]:
        for key, value in item.items():
            name = _field(str(key))
            if name == "{key}":
                continue
            if isinstance(value, dict):
                _collect(_entity("/" + name), [value], tables, codes, not_codes, route_tables)
                continue
            if isinstance(value, list):
                if value and all(isinstance(x, dict) for x in value):
                    _collect(_entity("/" + name), value, tables, codes, not_codes, route_tables)
                continue
            s = shape(value)
            cols[name] = merge_shapes(cols[name], s) if name in cols else s
            ref = f"{entity}.{name}"
            if value is None:
                continue
            if (
                isinstance(value, str)
                and CODE_VALUE.match(value)
                and len(value) <= 32
                and CODE_FIELD.search(name)
                and not NOT_CODE_FIELD.search(name)
            ):
                codes[ref][value] = None
            else:
                not_codes.add(ref)


def _add_rule(model: AppModel, rule: Rule) -> None:
    for have in model.rules:
        if have.text == rule.text:
            have.where += [w for w in rule.where if w not in have.where]
            return
    model.rules.append(rule)


def _selector(el: dict) -> str:
    if el["id"]:
        return f"{el['tag']}#{el['id']}"
    return f'{el["tag"]}[name="{el["name"]}"]'


def _safe_selector(selector: str) -> str:
    """A recorded selector with any attribute value shape-masked (ids and classes are kept)."""

    def fix(m: re.Match) -> str:
        name, value = m.group(1), m.group(3)
        if name in ("name", "type", "role", "id", "for"):
            return m.group(0)
        return f'[{name}{m.group(2)}"{sanitize.mask(value)}"]'

    cleaned = re.sub(r'\[([\w-]+)\s*([~|^$*]?=)\s*["\']?([^"\'\]]*)["\']?\]', fix, selector)
    return sanitize.mask_identifiers(cleaned)[:200]  # record numbers built into ids: #open-R-1001
