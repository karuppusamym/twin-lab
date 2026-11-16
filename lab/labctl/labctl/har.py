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
from datetime import datetime
from pathlib import Path
from typing import Any
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
    lines = (folder / "steps.jsonl").read_text(encoding="utf-8").splitlines()
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
                            str(data.get("action") or ""), data.get("selector"), dom))  # fmt: skip
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
            )
        )  # fmt: skip
    return out


# --- framework -------------------------------------------------------------------------------------

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


def _framework(pages: list[sanitize.PageInfo], scripts: list[tuple[str, str | None]]) -> dict:
    """The front-end framework, from page markers, bundles and script names, with the marker."""
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
            if name not in found and pattern.search(path.lower()):
                found[name] = {
                    "marker": f"{marker} {sanitize.route_pattern(path).rsplit('/', 1)[-1]}"
                }
    for server in ("aspnet-webforms", "jsf"):
        if server in found:
            label = {"aspnet-webforms": "ASP.NET Web Forms", "jsf": "JSF"}[server]
            return {"framework": label, "rendering": "server", "marker": found[server]["marker"]}
    for client in ("angular", "react", "vue"):
        if client in found:
            return {"framework": client, "version": found[client].get("version"),
                    "rendering": "client", "marker": found[client]["marker"]}  # fmt: skip
    if any(p.has_content for p in pages):
        return {"framework": "server-rendered", "rendering": "server",
                "marker": "pages arrive with their content; no client framework marker"}  # fmt: skip
    return {}


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
        name = re.sub(r"[^a-z0-9_]+", "_", seg.lower()).strip("_")
        return name or None
    return None


def _items(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        lists = [k for k in ITEM_KEYS if isinstance(data.get(k), list)]
        if lists:
            return [x for x in data[lists[0]] if isinstance(x, dict)]
        return [data]
    return []


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
    others = [e for e in entries if e.host not in hosts and e.kind == "api"]
    entries = [e for e in entries if e.host in hosts]
    if others:
        model.problems.append(
            f"{ev}: {len(others)} API request(s) to {len({e.host for e in others})} other host(s) "
            "were left out; if the application's API lives there, say so and analyse again"
        )

    screens: dict[str, _ScreenAcc] = {}

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
    known: set[str] = set()
    for text in dom_text.values():
        known |= sanitize.page(text).values
    for e in entries:
        # API answers keep no text (resp_text is for documents and scripts), so their values come
        # from the parsed JSON. A name an API returned is data wherever a page shows it, even as
        # one title-case word in a heading or option.
        if e.resp_json is not None and e.status < 400:
            known |= sanitize.words(e.resp_json)
        if e.resp_text and e.resp_mime == "text/html":
            known |= sanitize.page(e.resp_text).values
        elif e.resp_text and "json" in (e.resp_mime or ""):
            try:
                known |= sanitize.words(json.loads(e.resp_text))
            except ValueError:
                pass

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
            acc.pages.append(sanitize.page(dom_text[id(step)], known))
            before = acc.pattern

    # Screens from the HTML documents; empty single-page-app shells give an address only.
    shells: list[sanitize.PageInfo] = []
    for e in entries:
        if e.kind not in ("document", "form") or not (200 <= e.status < 300):
            continue
        if e.resp_mime != "text/html" or e.resp_text is None:
            continue
        info = sanitize.page(e.resp_text, known)
        if e.started:
            timeline.append((e.started, sanitize.route_pattern(e.path)))
        if info.has_content:
            screen(e.path).pages.append(info)
        else:
            shells.append(info)
            screen(e.path)
    timeline.sort(key=lambda t: t[0])

    scripts = [(e.path, e.resp_text) for e in entries if e.kind == "asset" and e.resp_text]
    all_pages = [p for s in screens.values() for p in s.pages]
    frontend = _framework(all_pages + shells, scripts)
    server_rendered = frontend.get("rendering") == "server"
    # Words seen as values, to mask names in error messages. A page shows the application's own
    # messages as text too; those words count only if a request or response also held them.
    shown = set().union(*(p.values for p in all_pages + shells))
    own = set().union(*(sanitize.words(_error(e.status, e.resp_json)[1] or "") for e in entries
                        if e.status >= 400))  # fmt: skip
    values = set().union(*(e.values for e in entries)) | (shown - own)

    def screen_at(e: Exchange) -> str | None:
        ref = urlsplit(e.request.get("referer", ""))
        if ref.netloc.lower() in hosts and ref.path:
            return sanitize.route_pattern(ref.path)
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
    # A change sent to a resource that a read showed belongs to the same table.
    for (method, pattern), acc in routes.items():
        entity = _entity(pattern)
        if method != "GET" and entity in tables and entity not in acc.tables:
            acc.tables.append(entity)

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

    for name, cols in tables.items():
        model.tables.append(
            Table(
                name=name,
                basis="inferred",
                **{"from": [ev]},
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
                        basis="observed", **{"from": [ev]},
                    )  # fmt: skip
                else:
                    have.label = have.label or el["label"]
                    have.options = have.options + [
                        o for o in el["options"] if o not in have.options
                    ]
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
            best = max(enumerate(acc.pages), key=lambda p: (len(p[1].ids), p[0]))[1]
            pages_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip("-.")[:80] or "page"
            out = (
                pages_dir / f"{safe_name}.html"
            )  # no ; : or other characters a file system mangles
            out.write_text(
                f"<!-- {acc.pattern}: value-free snapshot from {ev}. Structure, ids, classes and the "
                "application's own labels are real; every other text and value is masked "
                "(letters x/X, digits 9). -->\n" + best.html,
                encoding="utf-8",
                newline="\n",  # generated files are LF on every OS (they go into Git and images)
            )
            where = rel(out, repo_root)
            if Path(where).is_absolute():  # never write a machine's absolute paths into the model
                where = f"{pages_dir.name}/{out.name}"
            files = [where]
        if not acc.pages:
            no_content.append(acc.pattern)
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

    if frontend:
        model.stack = Stack(
            frontend={k: v for k, v in frontend.items() if v is not None},
            pinned={"react-dom": frontend["version"]}
            if frontend.get("framework") == "react" and frontend.get("version")
            else {"@angular/core": frontend["version"]}
            if frontend.get("framework") == "angular" and frontend.get("version")
            else {},
            **{"from": [ev]},
        )
    else:
        model.problems.append(f"{ev}: no framework marker found in the recorded pages or scripts")
    return model


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
