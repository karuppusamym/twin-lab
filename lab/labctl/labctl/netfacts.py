"""What the network shows beyond the API contract: headers, body formats, caching, timing,
dependencies between calls, streams, static files, third parties, errors and retries.

`labctl.apicatalog` describes each endpoint's parameters and bodies with synthetic examples. A
builder who has to make a demo behave like the original needs more of what the HAR holds, and
none of its values. This module reads the HAR again, where it is stored, and adds:

- per endpoint (`network` in `apps/<app>/api/<endpoint>.json`): the request headers that matter
  (content type, accept, custom `X-` headers by name with what their value is, never the value),
  the CSRF token by name and where the page took it from, the body format (JSON, form,
  multipart with file fields, GraphQL operations with their selection tree and variables,
  JSON-RPC methods, SOAP operations), what each query parameter does (search, filter, sort,
  page, page size, cursor, offset), per status the content type, caching (Cache-Control,
  ETag, Last-Modified, Vary, answers from the browser cache, 304 to a conditional request),
  compression, CORS and custom response headers by name, the paging envelope (total, next
  cursor, `Link` rels, `X-Total-Count`) and how the page asked for more (infinite scroll or a
  "load more" control), the response time (median and maximum, and the server's wait), which
  earlier answer or page gave each value the request sent (an identifier, a cursor, a token:
  by field name only), and retries after an error;
- per application (`apps/<app>/api/network.json`): the order of the calls a page load makes, the
  redirect chains, the streams (server-sent events, WebSocket messages when the HAR has them,
  long polling), the static files by kind with their sizes and caching, the third parties by
  purpose (a known vendor is named with its public domain; any other host is never written,
  it is `third-party-<n>`), an API of the application served from another origin, and the
  errors and retries seen.

Nothing here is a value of the recording: header values are classified (`a constant`, `the
CSRF token`), dependencies name fields, never what they held, and the files are checked like the
rest of `api/` (`labctl.apicatalog.leaks`) and for secrets and host names
(`labctl.codemap.check_text`) before they are written.
"""

from __future__ import annotations

import base64
import json
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from labctl import sanitize

FILE = "network.json"  # apps/<app>/api/network.json
FORMAT = "twinlab-network/1"
MAX_LEAVES = 3000  # leaves of one answer indexed for the dependencies
RETRY_WINDOW = 15.0  # seconds: a repeat of a failed call within this is a retry
LONG_POLL_MS = 1500  # a GET the server held at least this long, again and again

# Request headers every browser sends: not part of the application's contract.
BROWSER_HEADERS = {
    "accept-encoding", "accept-language", "connection", "content-length", "cookie", "host",
    "origin", "referer", "user-agent", "upgrade-insecure-requests", "dnt", "pragma", "priority",
    "cache-control", "if-none-match", "if-modified-since", "range", "te", "keep-alive",
    "purpose", "sec-purpose",
}  # fmt: skip
CSRF_NAME = re.compile(
    r"csrf|xsrf|authenticity[-_]?token|requestverificationtoken|^_?token$|anti[-_]?forgery", re.I
)
CODE_TOKEN = re.compile(r"^[A-Za-z][\w.:-]{0,40}$")
FIELD = re.compile(r"^[A-Za-z_$][\w$-]{0,63}$")

ROLE_WORDS = [
    (re.compile(r"recommend|similar|also[-_]?(viewed|bought)|for[-_]?you|personali[sz]", re.I),
     "recommendations or personalisation: the original chooses per visitor; any seed rows will do"),
    (re.compile(r"suggest|autocomplete|typeahead|complete", re.I),
     "search suggestions while typing"),
    (re.compile(r"cart|basket|bag(?![a-z])|checkout", re.I), "cart state"),
    (re.compile(r"wish[-_]?list|favou?rite|saved", re.I), "wish list state"),
    (re.compile(r"review|rating", re.I), "reviews"),
    (re.compile(r"variant|sku|option", re.I), "variant of a product (price, picture, stock)"),
    (re.compile(r"search|query|find", re.I), "search"),
]  # fmt: skip
QUERY_ROLES = [
    (re.compile(r"^(q|k|query|search|term|terms|keyword|keywords|text|s|kw|searchterm)$", re.I),
     "search"),
    (re.compile(r"^(sort|sort_?by|order|order_?by|orderby|dir|direction|sortorder)$", re.I),
     "sort"),
    (re.compile(r"^(page|p|pg|page_?no|pagenumber|page_?index)$", re.I), "page"),
    (re.compile(r"^(size|limit|per_?page|page_?size|pagesize|count|rows|take|num|max)$", re.I),
     "page size"),
    (re.compile(r"^(cursor|after|before|next|continuation|continuation_?token|starting_?after|"
                r"ending_?before|page_?token|next_?token|scroll_?id|since_?id|max_?id)$", re.I),
     "cursor"),
    (re.compile(r"^(offset|start|skip|from|first)$", re.I), "offset"),
]  # fmt: skip
TOTAL_KEYS = {"total", "count", "totalcount", "total_count", "totalresults", "total_results",
              "totalitems", "total_items", "hits"}  # fmt: skip
NEXT_KEYS = {"next", "nextcursor", "next_cursor", "cursor", "nextpagetoken", "next_page_token",
             "continuation", "endcursor", "after", "nexttoken", "next_token", "scroll_id"}  # fmt: skip
MORE_KEYS = {"has_more", "hasmore", "hasnextpage", "has_next_page", "more", "is_last", "last"}

# Third parties named by their public domain (never a host of the recording that is not here).
VENDORS = [
    ("google-analytics.com", "Google Analytics", "analytics"),
    ("googletagmanager.com", "Google Tag Manager", "tag manager (loads other tags)"),
    ("doubleclick.net", "Google Ads (DoubleClick)", "advertising"),
    ("googlesyndication.com", "Google AdSense", "advertising"),
    ("googleadservices.com", "Google Ads", "advertising"),
    ("facebook.net", "Meta Pixel", "advertising and analytics"),
    ("facebook.com", "Meta", "advertising and social"),
    ("hotjar.com", "Hotjar", "session recording and analytics"),
    ("segment.com", "Segment", "analytics"), ("segment.io", "Segment", "analytics"),
    ("mixpanel.com", "Mixpanel", "analytics"), ("amplitude.com", "Amplitude", "analytics"),
    ("newrelic.com", "New Relic", "performance monitoring"),
    ("nr-data.net", "New Relic", "performance monitoring"),
    ("sentry.io", "Sentry", "error reporting"), ("datadoghq.com", "Datadog", "monitoring"),
    ("browser-intake-datadoghq.com", "Datadog", "monitoring"),
    ("stripe.com", "Stripe", "payment widget"), ("stripe.network", "Stripe", "payment widget"),
    ("paypal.com", "PayPal", "payment widget"), ("paypalobjects.com", "PayPal", "payment widget"),
    ("adyen.com", "Adyen", "payment widget"), ("braintreegateway.com", "Braintree", "payment"),
    ("klarna.com", "Klarna", "payment widget"),
    ("maps.googleapis.com", "Google Maps", "maps"), ("maps.gstatic.com", "Google Maps", "maps"),
    ("mapbox.com", "Mapbox", "maps"), ("openstreetmap.org", "OpenStreetMap", "map tiles"),
    ("fonts.googleapis.com", "Google Fonts", "web fonts"),
    ("fonts.gstatic.com", "Google Fonts", "web fonts"),
    ("use.typekit.net", "Adobe Fonts", "web fonts"),
    ("cdnjs.cloudflare.com", "cdnjs", "library files (CDN)"),
    ("cdn.jsdelivr.net", "jsDelivr", "library files (CDN)"), ("unpkg.com", "unpkg", "CDN"),
    ("code.jquery.com", "jQuery CDN", "library files (CDN)"),
    ("ajax.googleapis.com", "Google Hosted Libraries", "library files (CDN)"),
    ("cloudfront.net", "Amazon CloudFront", "files (CDN)"),
    ("akamaihd.net", "Akamai", "files (CDN)"), ("fastly.net", "Fastly", "files (CDN)"),
    ("recaptcha.net", "reCAPTCHA", "bot check"), ("hcaptcha.com", "hCaptcha", "bot check"),
    ("youtube.com", "YouTube", "video"), ("ytimg.com", "YouTube", "video"),
    ("vimeo.com", "Vimeo", "video"), ("intercom.io", "Intercom", "chat widget"),
    ("zendesk.com", "Zendesk", "chat or help widget"), ("onetrust.com", "OneTrust", "consent"),
    ("cookielaw.org", "OneTrust", "cookie consent"), ("cookiebot.com", "Cookiebot", "consent"),
    ("optimizely.com", "Optimizely", "A/B testing"),
]  # fmt: skip
PURPOSE_WORDS = [
    (re.compile(r"collect|track|beacon|pixel|event|analytic|telemetry|log|metric|stat", re.I),
     "analytics or tracking"),
    (re.compile(r"pay|checkout|card|wallet", re.I), "payment"),
    (re.compile(r"map|tile|geo", re.I), "maps"),
    (re.compile(r"font", re.I), "web fonts"),
    (re.compile(r"ads?(?![a-z])|advert|banner", re.I), "advertising"),
    (re.compile(r"captcha", re.I), "bot check"),
    (re.compile(r"consent|cookie", re.I), "consent"),
]  # fmt: skip


# --- reading the HAR ----------------------------------------------------------------------------


@dataclass
class Raw:
    """One HAR entry, read for its structure. Values stay in memory, for comparisons only."""

    n: int
    started: datetime | None
    method: str
    host: str
    origin: str
    path: str
    route: str
    query: list[tuple[str, str]]
    req_headers: dict[str, str]
    resp_headers: dict[str, list[str]]
    status: int
    mime: str
    req_mime: str
    post_text: str | None
    post_params: list[dict]
    text: str | None
    ms: float
    wait: float
    transfer: int | None
    size: int | None
    ws: list[dict] = field(default_factory=list)
    own: bool = True

    def header(self, name: str) -> str:
        values = self.resp_headers.get(name.lower())
        return values[0] if values else ""

    @property
    def key(self) -> str:
        return f"{self.method} {self.route}"

    @property
    def from_cache(self) -> bool:
        return self.transfer == 0 and self.status in (200, 203)


def _decoded(content: dict) -> str | None:
    text = content.get("text")
    if not isinstance(text, str):
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except ValueError:
            return None
    return text


def _when(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def read(har: dict, own_hosts: set[str]) -> list[Raw]:
    out = []
    for n, entry in enumerate((har.get("log") or {}).get("entries") or []):
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        url = urlsplit(str(req.get("url") or ""))
        if url.scheme not in ("http", "https", "ws", "wss"):
            continue
        req_h = {}
        for h in req.get("headers") or []:
            name = str(h.get("name") or "").strip().lower()
            if name and not name.startswith(":"):
                req_h[name] = str(h.get("value") or "")
                _CASE.setdefault(name, str(h.get("name")).strip())
        resp_h: dict[str, list[str]] = defaultdict(list)
        for h in resp.get("headers") or []:
            name = str(h.get("name") or "").strip().lower()
            if name:
                resp_h[name].append(str(h.get("value") or ""))
        content = resp.get("content") or {}
        post = req.get("postData") or {}
        timings = entry.get("timings") or {}
        host = url.netloc.lower()
        out.append(Raw(
            n, _when(entry.get("startedDateTime")), str(req.get("method") or "GET").upper(), host,
            f"{url.scheme}://{host}", url.path or "/", sanitize.route_pattern(url.path or "/"),
            parse_qsl(url.query, keep_blank_values=True), req_h, dict(resp_h),
            int(resp.get("status") or 0),
            str(content.get("mimeType") or "").split(";")[0].strip().lower(),
            str(post.get("mimeType") or req_h.get("content-type") or "").split(";")[0].strip().lower(),
            post.get("text") if isinstance(post.get("text"), str) else None,
            [p for p in post.get("params") or [] if isinstance(p, dict)],
            _decoded(content), float(entry.get("time") or 0),
            float(timings.get("wait") or 0) if isinstance(timings.get("wait"), (int, float)) else 0,
            resp.get("_transferSize") if isinstance(resp.get("_transferSize"), int) else None,
            content.get("size") if isinstance(content.get("size"), int) else None,
            [m for m in entry.get("_webSocketMessages") or [] if isinstance(m, dict)],
            host in own_hosts or not own_hosts,
        ))  # fmt: skip
    out.sort(key=lambda r: (r.started or datetime.min.replace(tzinfo=None), r.n)
             if r.started is None else (r.started.replace(tzinfo=None), r.n))  # fmt: skip
    return out


def _json(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def kind_of(r: Raw) -> str:
    """document | api | stream | websocket | script | style | image | font | media | other"""
    path = r.path.lower()
    if r.status == 101 or r.ws or r.origin.startswith("ws"):
        return "websocket"
    if r.mime == "text/event-stream":
        return "stream"
    if "javascript" in r.mime or path.endswith((".js", ".mjs")):
        return "script"
    if r.mime == "text/css" or path.endswith(".css"):
        return "style"
    if r.mime.startswith("font/") or path.endswith((".woff", ".woff2", ".ttf", ".otf", ".eot")):
        return "font"
    if r.mime.startswith("image/") or path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg",
                                                       ".webp", ".avif", ".ico")):  # fmt: skip
        return "image"
    if r.mime.startswith(("video/", "audio/")):
        return "media"
    if r.method == "GET" and r.mime == "text/html" and not r.req_headers.get("x-requested-with"):
        dest = r.req_headers.get("sec-fetch-dest", "document")
        return "document" if dest in ("document", "iframe", "frame") else "api"
    if 300 <= r.status < 400 and r.method == "GET" and "json" not in r.mime:
        dest = r.req_headers.get("sec-fetch-dest", "document")
        if dest in ("document", "iframe", "frame", ""):
            return "document"
    if r.method != "GET" or "json" in r.mime or "xml" in r.mime or r.status in (204, 304):
        return "api"
    return "other"


# --- small helpers -------------------------------------------------------------------------------


def _ms(values: list[float]) -> dict:
    vals = [v for v in values if v >= 0]
    if not vals:
        return {}
    return {"median": round(statistics.median(vals)), "max": round(max(vals))}


def _safe_path(path: str) -> str:
    """A field path of an answer with every key that is not a field name made `{key}`."""
    out = []
    for part in re.split(r"(?<=[^.])\.", path):
        name, brackets = re.match(r"^(.*?)((?:\[\])*)$", part).groups()
        if name and (not FIELD.match(name) or sanitize.is_id_segment(name)):
            name = "{key}"
        out.append(name + brackets)
    return ".".join(out)


def _leaves(data, path: str = ""):
    if isinstance(data, dict):
        for k, v in data.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for v in data[:200]:
            yield from _leaves(v, f"{path}[]")
    else:
        yield path, data


def _flowing(value) -> str | None:
    """A value distinctive enough to follow from one call to the next (an identifier, a cursor,
    a token), as text; None for short words, small numbers and booleans."""
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if isinstance(value, (int, float)):
        return text if len(text) >= 3 else None
    if len(text) >= 6 or (len(text) >= 4 and re.search(r"\d", text)):
        return text if len(text) <= 4000 else None
    return None


def value_kind(value: str) -> str:
    """What a header or parameter value is, without the value."""
    if re.fullmatch(r"\d+", value):
        return "a number"
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value, re.I):
        return "a UUID"
    if re.fullmatch(r"[A-Za-z0-9_\-+/=.]{16,}", value) and re.search(r"\d", value):
        return "an opaque token"
    if re.search(r"^\W*\w+://|[\w-]+\.[\w-]+\.[A-Za-z]{2,}", value):
        return "an address (not kept)"
    from labctl.behaviour import dom as domlib

    return f"a value of shape `{domlib.shape(value)[:24]}`"


def _cache(r: Raw) -> dict:
    out: dict = {}
    cc = r.header("cache-control")
    if cc:
        keep = [d.strip().lower() for d in cc.split(",")
                if re.fullmatch(r"[a-z-]+(=\d{1,10})?", d.strip().lower())]  # fmt: skip
        if keep:
            out["cache_control"] = ", ".join(keep)
    tag = r.header("etag")
    if tag:
        out["etag"] = "weak" if tag.startswith("W/") else "strong"
    if r.header("last-modified"):
        out["last_modified"] = "present"
    if r.header("expires"):
        out["expires"] = "present"
    vary = r.header("vary")
    if vary:
        out["vary"] = sorted({v.strip() for v in vary.split(",") if FIELD.match(v.strip())})
    return out


def _cors(r: Raw, page_origins: set[str]) -> dict:
    out = {}
    allow = r.header("access-control-allow-origin")
    if allow:
        out["allow_origin"] = ("any (*)" if allow.strip() == "*" else
                               "the page's origin" if allow.strip().lower() in page_origins else
                               "another origin")  # fmt: skip
    for name in ("access-control-allow-credentials", "access-control-allow-methods",
                 "access-control-allow-headers", "access-control-expose-headers",
                 "access-control-max-age"):  # fmt: skip
        v = r.header(name)
        if v:
            if name.endswith("max-age"):
                out["max_age"] = v if v.isdigit() else "present"
            elif name.endswith("credentials"):
                out["allow_credentials"] = v.strip().lower() == "true"
            else:
                out[name.split("-", 3)[-1].replace("-", "_")] = sorted(
                    {x.strip() for x in v.split(",") if FIELD.match(x.strip()) or x.strip() == "*"}
                )
    return out


def _link_rels(r: Raw) -> list[dict]:
    out = []
    for value in r.resp_headers.get("link") or []:
        for part in value.split(","):
            m = re.search(r"<([^>]*)>\s*;(.*)", part)
            if not m:
                continue
            rel = re.search(r'rel="?([\w ]+)"?', m.group(2))
            target = urlsplit(m.group(1))
            out.append({"rel": rel.group(1).strip() if rel else "?",
                        "to": sanitize.route_pattern(target.path or "/"),
                        "query": sorted({k for k, _ in parse_qsl(target.query)})})  # fmt: skip
    return out


# --- request bodies ------------------------------------------------------------------------------

GQL_TOKEN = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|'
                       r"\.\.\.|[A-Za-z_]\w*|[{}()\[\]:!$=@,|&]|#[^\n]*")  # fmt: skip


def graphql_doc(query: str) -> dict:
    """A GraphQL document as structure: per operation its type, name, variables with their types
    and the selection tree (`field(args)` -> sub-selection, a leaf -> null). Literals in the
    document are replaced: strings by `"..."`, numbers by `0`."""
    toks = [t for t in GQL_TOKEN.findall(query or "") if not t.startswith("#") and t != ","]
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def value_text() -> str:
        t = take()
        if t == "$":
            return "$" + take()
        if t.startswith('"'):
            return '"..."'
        if re.fullmatch(r"-?\d.*", t):
            return "0"
        if t in ("[", "{"):
            close = "]" if t == "[" else "}"
            parts = []
            while peek() not in (close, None):
                if t == "{":
                    k = take()
                    take()  # :
                    parts.append(f"{k}: {value_text()}")
                else:
                    parts.append(value_text())
            take()
            return t + ", ".join(parts) + close
        return t  # an enum value, true, false, null

    def args() -> str:
        if peek() != "(":
            return ""
        take()
        parts = []
        while peek() not in (")", None):
            name = take()
            take()  # :
            parts.append(f"{name}: {value_text()}")
        take()
        return "(" + ", ".join(parts) + ")"

    def directives() -> str:
        out = ""
        while peek() == "@":
            take()
            out += " @" + take() + args()
        return out

    def selection() -> dict:
        out: dict = {}
        take()  # {
        while peek() not in ("}", None):
            t = take()
            if t == "...":
                if peek() == "on":
                    take()
                    cond = take()
                    out[f"... on {cond}"] = selection()
                else:
                    out[f"...{take()}"] = None
                continue
            name = t
            if peek() == ":":  # an alias
                take()
                name = f"{t}: {take()}"
            name += args() + directives()
            out[name] = selection() if peek() == "{" else None
        take()
        return out

    ops = []
    fragments = {}
    while pos < len(toks):
        t = peek()
        if t == "{":
            ops.append({"type": "query", "name": None, "variables": {}, "selection": selection()})
        elif t in ("query", "mutation", "subscription"):
            take()
            name = take() if peek() not in ("(", "{", "@") else None
            variables = {}
            if peek() == "(":
                take()
                while peek() not in (")", None):
                    if take() == "$":
                        var = take()
                        take()  # :
                        typ = ""
                        while peek() not in ("$", ")", "=", None):
                            typ += take()
                        if peek() == "=":
                            take()
                            value_text()
                        variables[var] = typ
                take()
            directives()
            ops.append({"type": t, "name": name, "variables": variables,
                        "selection": selection() if peek() == "{" else {}})  # fmt: skip
        elif t == "fragment":
            take()
            name = take()
            take()  # on
            on = take()
            directives()
            fragments[name] = {"on": on, "selection": selection() if peek() == "{" else {}}
        else:
            take()
    return {"operations": ops, "fragments": fragments}


def _is_graphql(body) -> bool:
    items = body if isinstance(body, list) else [body]
    return bool(items) and all(isinstance(b, dict) and isinstance(b.get("query"), str)
                               and re.match(r"\s*(query|mutation|subscription|fragment|\{)",
                                            b["query"]) for b in items[:20])  # fmt: skip


def _is_persisted(body) -> bool:
    items = body if isinstance(body, list) else [body]
    return bool(items) and all(isinstance(b, dict) and isinstance(b.get("extensions"), dict)
                               and "persistedQuery" in b["extensions"] for b in items[:20])  # fmt: skip


def _shape(value):
    """Keys and types of a JSON value, no value."""
    if isinstance(value, dict):
        return {
            (k if FIELD.match(str(k)) else "{key}"): _shape(v) for k, v in list(value.items())[:60]
        }
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return "string"


def _multipart(r: Raw, uploads: list[dict]) -> dict:
    out: dict = {"format": "multipart/form-data"}
    fields: dict[str, dict] = {}
    text = r.post_text
    if text:
        boundary = re.match(r"^--([^\r\n]+)", text)
        for part in text.split("--" + boundary.group(1)) if boundary else []:
            head, _, body = part.partition("\r\n\r\n")
            name = re.search(r'name="([^"]*)"', head)
            if not name:
                continue
            fname = re.search(r'filename="([^"]*)"', head)
            info: dict = {"kind": "file" if fname else "text"}
            if fname:
                ctype = re.search(r"Content-Type:\s*([^\r\n;]+)", head, re.I)
                if ctype:
                    info["content_type"] = ctype.group(1).strip().lower()
                ext = re.search(r"\.([A-Za-z0-9]{1,6})$", fname.group(1))
                if ext:
                    info["extension"] = ext.group(1).lower()
                info["bytes"] = max(0, len(body) - 2)
            fields[_field_name(name.group(1))] = info
    for p in r.post_params:
        name = _field_name(str(p.get("name") or ""))
        info = fields.setdefault(name, {"kind": "file" if p.get("fileName") else "text"})
        if p.get("contentType"):
            info["content_type"] = str(p["contentType"]).lower()
    if not text and not r.post_params:
        out["body"] = ("not in the HAR (the browser recorded no body: a file was sent); the "
                       "fields are those of the form and the upload step")  # fmt: skip
    for up in uploads:
        info = fields.setdefault(up["field"], {"kind": "file"})
        if up.get("type"):
            info["content_type"] = up["type"]
        info.pop("bytes", None) if info.get("bytes") == 0 else None
        if up.get("size"):
            info["size_class"] = up["size"]
        if up.get("accept"):
            info["accept"] = up["accept"]
    out["fields"] = fields
    return out


def _field_name(name: str) -> str:
    return name if FIELD.match(name.replace("[", "").replace("]", "")) else "{field}"


def _soap(r: Raw) -> dict | None:
    text = r.post_text or ""
    if not (
        "xml" in r.req_mime or r.req_headers.get("soapaction") or text.lstrip().startswith("<")
    ):
        return None
    out: dict = {"format": "SOAP" if re.search(r"<(\w+:)?Envelope\b", text) else "XML"}
    body = re.search(r"<(?:\w+:)?Body[^>]*>\s*<(?:(\w+):)?([\w.-]+)", text)
    root = body or re.search(r"<(?:(\w+):)?([\w.-]+)[\s>]", re.sub(r"<\?.*?\?>", "", text))
    if root:
        out["operation"] = root.group(2)
        start = root.end()
        children = re.findall(r"<(?:\w+:)?([\w.-]+)[\s/>]", text[start : start + 20000])
        out["elements"] = list(dict.fromkeys(c for c in children if c != root.group(2)))[:60]
    action = r.req_headers.get("soapaction", "").strip('"')
    if action:
        out["soap_action"] = re.split(r"[/#]", action)[-1] or "present"
    return out


def body_format(r: Raw, uploads: list[dict]) -> dict | None:
    if r.req_mime.startswith("multipart/") or (r.method in ("POST", "PUT", "PATCH") and any(
            p.get("fileName") for p in r.post_params)):  # fmt: skip
        return _multipart(r, uploads)
    if r.post_text is None and not r.post_params:
        return None
    body = _json(r.post_text)
    if body is not None:
        if _is_graphql(body) or _is_persisted(body):
            return {"format": "GraphQL"}
        items = body if isinstance(body, list) else [body]
        if items and all(isinstance(b, dict) and "jsonrpc" in b and "method" in b for b in items):
            return {"format": "JSON-RPC", "batch": isinstance(body, list)}
        return {"format": "json", "top": "array" if isinstance(body, list) else "object"}
    if r.req_mime == "application/x-www-form-urlencoded":
        names = [_field_name(k) for k, _ in parse_qsl(r.post_text or "", keep_blank_values=True)]
        return {"format": "application/x-www-form-urlencoded", "fields": list(dict.fromkeys(names))}
    soap = _soap(r)
    if soap:
        return soap
    return {"format": r.req_mime or "text", "note": "not JSON, a form or XML: the body is not read"}


# --- the facts ----------------------------------------------------------------------------------


@dataclass
class _Ep:
    raws: list[Raw] = field(default_factory=list)
    deps: Counter = field(default_factory=Counter)
    retries: list[dict] = field(default_factory=list)
    uploads: list[dict] = field(default_factory=list)


def _sources(raws: list[Raw]):
    """(time, value, label) of every value a page or an answer gave, in time order."""
    for r in raws:
        if not r.own:
            continue
        k = kind_of(r)
        if k == "api" and r.status < 400:
            data = _json(r.text)
            for i, (path, v) in enumerate(_leaves(data)):
                if i >= MAX_LEAVES:
                    break
                text = _flowing(v)
                if text:
                    yield r.started, text, f"{r.key} answer `{_safe_path(path)}`"
            for value in r.resp_headers.get("set-cookie") or []:
                name, _, rest = value.partition("=")
                cookie = rest.split(";", 1)[0]
                if _flowing(cookie) and sanitize.COOKIE_NAME.match(name.strip()):
                    yield r.started, cookie, f"the cookie `{name.strip()}`"
        elif k == "document" and r.text:
            html = r.text[:2_000_000]
            for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
                tag = m.group()
                name = re.search(r'\b(?:name|property)\s*=\s*["\']([^"\']+)', tag, re.I)
                content = re.search(r'\bcontent\s*=\s*["\']([^"\']*)', tag, re.I)
                if name and content and _flowing(content.group(1)):
                    yield (
                        r.started,
                        content.group(1),
                        (f'`<meta name="{_field_name(name.group(1))}">` of the page `{r.route}`'),
                    )
            for m in re.finditer(r"<input\b[^>]*>", html, re.I):
                tag = m.group()
                if not re.search(r'type\s*=\s*["\']?hidden', tag, re.I):
                    continue
                name = re.search(r'\bname\s*=\s*["\']([^"\']+)', tag, re.I)
                value = re.search(r'\bvalue\s*=\s*["\']([^"\']*)', tag, re.I)
                if name and value and _flowing(value.group(1)):
                    yield (
                        r.started,
                        value.group(1),
                        (
                            f"the hidden field `{_field_name(name.group(1))}` of the page `{r.route}`"
                        ),
                    )
            for m in re.finditer(r"<(\w+)\b([^>]*)>", html):
                attrs = m.group(2)
                ident = re.search(r'\bid\s*=\s*["\']([\w-]+)', attrs)
                for d in re.finditer(r'\bdata-([\w-]+)\s*=\s*["\']([^"\']*)', attrs):
                    if _flowing(d.group(2)):
                        where = f"{m.group(1)}#{ident.group(1)}" if ident else m.group(1)
                        yield (
                            r.started,
                            d.group(2),
                            (f"`data-{d.group(1)}` of `{where}` on the page `{r.route}`"),
                        )
            for value in r.resp_headers.get("set-cookie") or []:
                name, _, rest = value.partition("=")
                cookie = rest.split(";", 1)[0]
                if _flowing(cookie) and sanitize.COOKIE_NAME.match(name.strip()):
                    yield r.started, cookie, f"the cookie `{name.strip()}`"


def _targets(r: Raw):
    """(field, value) of every value a request sent: path parameters, query, body, headers."""
    pats = r.route.split("/")
    for seg, pat in zip(r.path.split("/"), pats, strict=False):
        if pat.startswith("{"):
            yield f"path.{pat.strip('{}')}", seg
    for k, v in r.query:
        yield f"query.{_field_name(k)}", v
    body = _json(r.post_text)
    if body is not None:
        for path, v in _leaves(body):
            if _is_graphql(body) and path in ("query", "operationName"):
                continue
            text = _flowing(v)
            if text:
                yield f"body.{_safe_path(path)}", text
    elif r.post_text and r.req_mime == "application/x-www-form-urlencoded":
        for k, v in parse_qsl(r.post_text, keep_blank_values=True):
            yield f"form.{_field_name(k)}", v
    for name, v in r.req_headers.items():
        if name not in BROWSER_HEADERS and not name.startswith("sec-") and name not in (
                "accept", "content-type", "authorization"):  # fmt: skip
            yield f"header.{_header_name(name)}", v


_CASE: dict[str, str] = {}  # a header's name as the recording wrote it


def _echo(label: str, key: str) -> bool:
    """A value the same endpoint answered before (it echoes what it was sent), unless that is
    its next cursor or token: no dependency to report."""
    if not label.startswith(f"{key} answer "):
        return False
    leaf = label.rsplit("`", 2)[-2].split(".")[-1].replace("[]", "").lower()
    return not (leaf in NEXT_KEYS or re.search(r"cursor|token|next", leaf))


def _header_name(name: str) -> str:
    written = _CASE.get(name.lower())
    if written and FIELD.match(written.replace("-", "_")):
        return written
    return "-".join(p[:1].upper() + p[1:] for p in name.split("-"))


def _uploads(ctx) -> list[dict]:
    """The upload steps of a recording part: the file input, type, size class and `accept`."""
    out = []
    for s in getattr(ctx.part, "raw", []):
        if s.get("action") != "upload":
            continue
        sel = str(s.get("selector") or "")
        html = ctx.part.html_at(s["seq"]) or ""
        ident = re.search(r"#([\w-]+)", sel)
        accept, name = None, None
        if ident:
            tag = re.search(rf'<input\b[^>]*\bid="{re.escape(ident.group(1))}"[^>]*>', html)
            if tag:
                a = re.search(r'\baccept="([^"]*)"', tag.group())
                n = re.search(r'\bname="([^"]*)"', tag.group())
                accept = a.group(1) if a else None
                name = n.group(1) if n else None
        for f in s.get("files") or [{}]:
            out.append({"ts": _when(s.get("ts")), "field": _field_name(name or ident.group(1)
                        if ident else "file"), "type": f.get("type"), "size": f.get("size"),
                        "accept": accept})  # fmt: skip
    return out


def _query_roles(names: list[str], deps: dict[str, str], values=None) -> dict[str, str]:
    out = {}
    for name in names:
        role = next((r for rx, r in QUERY_ROLES if rx.match(name)), None)
        seen = (values or {}).get(name) or []
        if role in ("page", "page size", "offset") and seen and not all(
                v.isdigit() for v in seen if v):  # fmt: skip
            role = None  # `size=M` is a choice, not a page size
        src = deps.get(f"query.{name}")
        if src and ("cursor" in src.lower() or "next" in src.lower() or role == "cursor"):
            role = f"cursor: the value of {src}"
        elif src and role is None:
            role = f"taken from {src}"
        out[name] = role or "filter or option (see `query` for its values)"
    return out


def _envelope(raws: list[Raw]) -> dict:
    out: dict = {}
    totals, nexts, mores, last = set(), set(), set(), False
    for r in raws:
        data = _json(r.text) if r.status < 400 else None
        if isinstance(data, dict):
            for path, v in _leaves(data):
                key = path.split(".")[-1].replace("[]", "").lower()
                if "[]" in path:
                    continue
                if key in TOTAL_KEYS and isinstance(v, int):
                    totals.add(_safe_path(path))
                elif key in NEXT_KEYS:
                    nexts.add(_safe_path(path))
                    last = last or v in (None, "")
                elif key in MORE_KEYS:
                    mores.add(_safe_path(path))
        if r.header("x-total-count"):
            totals.add("header X-Total-Count")
    rels = sorted({lr["rel"] for r in raws for lr in _link_rels(r)})
    if totals:
        out["total"] = sorted(totals)
    if nexts:
        out["next"] = sorted(nexts)
        out["last_page_seen"] = (
            "yes: an answer had no next value (null or empty) on the last page"
            if last else "no: every recorded answer had a next value")  # fmt: skip
    if mores:
        out["more_flag"] = sorted(mores)
    if rels:
        out["link_header"] = rels
    return out


def endpoint_facts(key: str, ep: _Ep, doc: dict | None, ctx_steps, page_origins,
                   loads: list[datetime] = ()) -> dict:  # fmt: skip
    raws = ep.raws
    out: dict = {}
    # request headers
    heads: dict[str, list[str]] = defaultdict(list)
    for r in raws:
        for name, v in r.req_headers.items():
            if name in BROWSER_HEADERS or name.startswith("sec-") or name in ("authorization",):
                continue
            heads[name].append(v)
    req_h = {}
    for name, vals in sorted(heads.items()):
        label = _header_name(name)
        if name == "content-type":
            req_h[label] = sorted({v.split(";")[0].strip().lower() for v in vals})
        elif name == "accept":
            req_h[label] = sorted({v for v in vals if len(v) < 200})
        elif CSRF_NAME.search(name):
            req_h[label] = "the CSRF token (see `csrf`)"
        elif sanitize.is_credential_header(name):
            req_h[label] = "a credential: its value is never kept; issue your own"
        else:
            src = ep.deps.most_common()
            dep = next((s for (f, s), _ in src if f == f"header.{label}"), None)
            if dep:
                req_h[label] = f"taken from {dep}"
            elif len(set(vals)) == 1:
                req_h[label] = (f"the same on every call ({value_kind(vals[0])}): "
                                "send a constant of the same shape")  # fmt: skip
            else:
                req_h[label] = f"varies per call ({value_kind(vals[-1])})"
    if req_h:
        out["request_headers"] = req_h
    # CSRF
    csrf = []
    for (fld, src), n in ep.deps.most_common():
        name = fld.split(".", 1)[1]
        if CSRF_NAME.search(name):
            csrf.append({"name": name, "in": fld.split(".", 1)[0], "comes_from": src, "calls": n})
    for fld in sorted({f for r in raws for f, _ in _targets(r)}):
        name = fld.split(".", 1)[1]
        if CSRF_NAME.search(name) and not any(c["name"] == name for c in csrf):
            csrf.append({"name": name, "in": fld.split(".", 1)[0],
                         "comes_from": "not found in an earlier page or answer"})  # fmt: skip
    if csrf:
        refused = sorted({r.status for r in raws if r.status in (401, 403, 419)})
        for c in csrf:
            c["without_it"] = (f"answered {', '.join(map(str, refused))} in the recording"
                               if refused else "not recorded; the usual answer is 403")  # fmt: skip
        out["csrf"] = csrf
    # body
    fmts = [body_format(r, [u for u in ep.uploads]) for r in raws if r.method != "GET"]
    fmts = [f for f in fmts if f]
    if fmts:
        merged = fmts[0]
        if merged.get("format") == "multipart/form-data":
            for f in fmts[1:]:
                for name, info in (f.get("fields") or {}).items():
                    merged["fields"].setdefault(name, info)
            merged.pop("body", None) if any("body" not in f for f in fmts) else None
        out["body"] = merged
        if merged.get("format") == "GraphQL":
            out["graphql"] = _graphql(raws)
        elif merged.get("format") == "JSON-RPC":
            out["json_rpc"] = _jsonrpc(raws)
    # query roles
    names = list(dict.fromkeys(_field_name(k) for r in raws for k, _ in r.query))
    if names:
        deps = {}
        for (fld, src), _ in ep.deps.most_common():
            deps.setdefault(fld, src)
        values: dict[str, list[str]] = defaultdict(list)
        for r in raws:
            for k, v in r.query:
                values[_field_name(k)].append(v)
        out["query_roles"] = _query_roles(names, deps, values)
    # dependencies
    if ep.deps:
        out["depends_on"] = [{"field": f, "from": s, "calls": n}
                             for (f, s), n in ep.deps.most_common(12)
                             if not CSRF_NAME.search(f)]  # fmt: skip
        if not out["depends_on"]:
            del out["depends_on"]
    # answers
    by_status: dict[str, dict] = {}
    for status in sorted({r.status for r in raws}):
        group = [r for r in raws if r.status == status]
        info: dict = {"count": len(group)}
        mimes = sorted({r.mime for r in group if r.mime})
        if mimes:
            info["content_type"] = mimes
        cache = next((c for c in (_cache(r) for r in group if not r.from_cache) if c), {})
        if cache:
            info["caching"] = cache
        hits = sum(1 for r in group if r.from_cache)
        if hits:
            info["from_browser_cache"] = f"{hits} of {len(group)} answer(s)"
        cond = sum(1 for r in group if r.req_headers.get("if-none-match")
                   or r.req_headers.get("if-modified-since"))  # fmt: skip
        if cond:
            info["conditional_requests"] = (
                f"{cond} of {len(group)} sent If-None-Match or If-Modified-Since"
            )
        enc = sorted({r.header("content-encoding") for r in group if r.header("content-encoding")})
        if enc:
            info["compression"] = enc
        cors = next((c for c in (_cors(r, page_origins) for r in group) if c), {})
        if cors:
            info["cors"] = cors
        extra = sorted({_header_name(n) for r in group for n in r.resp_headers
                        if (n.startswith("x-") or n in ("link", "retry-after", "location"))
                        and not sanitize.is_credential_header(n)
                        and n not in ("x-powered-by", "x-aspnet-version")})  # fmt: skip
        if extra:
            info["headers"] = {h: _resp_header_kind(h, group) for h in extra}
        empty = all((r.size or 0) <= 0 and not r.text for r in group)
        if empty:
            info["body"] = "empty"
        by_status[str(status)] = info
    out["answers"] = by_status
    env = _envelope(raws)
    paged = any(v in ("page", "cursor", "offset") for v in (out.get("query_roles") or {}).values())
    if env and (paged or env.keys() & {"next", "link_header", "more_flag"}):
        out["paging"] = env
        how = _more_by(doc, raws, ctx_steps, out.get("query_roles") or {}, loads)
        if how:
            out["paging"]["asked_by"] = how
    # timing
    t = _ms([r.ms for r in raws])
    if t:
        t["calls"] = len(raws)
        w = _ms([r.wait for r in raws])
        if w:
            t["server_wait_median"] = w["median"]
        out["time_ms"] = t
    if ep.retries:
        out["retries"] = ep.retries
    role = next((text for rx, text in ROLE_WORDS if rx.search(key.split(" ", 1)[1])), None)
    if role:
        out["role"] = role
    return out


def _resp_header_kind(name: str, group: list[Raw]) -> str:
    low = name.lower()
    vals = [r.header(low) for r in group if r.header(low)]
    if low == "link":
        rels = sorted({lr["rel"] for r in group for lr in _link_rels(r)})
        return "paging links, rel " + ", ".join(rels) if rels else "present"
    if low == "location":
        return "the address it leads to: " + ", ".join(
            sorted({sanitize.route_pattern(urlsplit(v).path or "/") for v in vals})
        )
    if low == "retry-after":
        return "seconds to wait before trying again" if all(v.isdigit() for v in vals) else "a date"
    if re.search(r"total|count", low) and all(v.isdigit() for v in vals):
        return "a number: the count of all rows, beside this page of them"
    if re.search(r"rate|limit|remaining", low):
        return "a rate limit figure"
    if re.search(r"request-?id|trace|correlation", low):
        return "an identifier per answer: make your own"
    if len(set(vals)) == 1:
        return f"the same on every answer ({value_kind(vals[0])})"
    return f"varies per answer ({value_kind(vals[-1])})"


def _more_by(doc: dict | None, raws: list[Raw], ctx_steps, roles: dict,
             loads: list[datetime] = ()) -> list[str] | None:  # fmt: skip
    """How the page asked for the next page: a control a person used just before, or nothing
    (the person scrolled, or the page asked on its own)."""
    if not doc:
        return None
    cursorish = {k for k, v in roles.items() if v.startswith(("cursor", "page", "offset"))}
    if not cursorish:
        return None
    out = []
    for i, call in enumerate(doc.get("calls") or []):
        q = call.get("query") or {}
        if not any(q.get(k) not in (None, "", "1", "0") for k in cursorish):
            continue
        step = ctx_steps(call.get("recording"), call.get("step"))
        when = _when(step.get("ts")) if step else None
        started = raws[i].started if i < len(raws) else None
        late = bool(when and started and (started - when).total_seconds() > 1.0)
        if when and started:  # the click loaded a page, and the page asked: not "load more"
            w, s0 = when.replace(tzinfo=None), started.replace(tzinfo=None)
            late = late or any(w <= t <= s0 for t in loads)
        if step and not late and step.get("action") in ("click", "dblclick", "press",
                                                          "keypress") and step.get("selector"):  # fmt: skip
            text = f'a click on `{step["selector"]}` ("load more" or a next-page control)'
        else:
            text = ("no control: the page asked while a person scrolled (infinite scroll) or on "
                    "its own")  # fmt: skip
        if text not in out:
            out.append(text)
    return out or None


def _graphql(raws: list[Raw]) -> dict:
    ops: dict[str, dict] = {}
    for r in raws:
        body = _json(r.post_text)
        answer = _json(r.text)
        items = body if isinstance(body, list) else [body]
        answers = answer if isinstance(answer, list) else [answer] * len(items)
        for b, a in zip(items, answers, strict=False):
            if not isinstance(b, dict):
                continue
            parsed = graphql_doc(b.get("query") or "") if isinstance(b.get("query"), str) else {}
            first = (parsed.get("operations") or [{}])[0]
            name = b.get("operationName") or first.get("name") or "(anonymous)"
            name = name if CODE_TOKEN.match(str(name)) else "(unnamed)"
            op = ops.setdefault(name, {"type": first.get("type") or "query", "calls": 0,
                                       "statuses": set(), "variables": {}, "data": None,
                                       "errors": 0})  # fmt: skip
            op["calls"] += 1
            op["statuses"].add(r.status)
            if parsed and "selection" not in op:
                op["selection"] = first.get("selection")
                if parsed.get("fragments"):
                    op["fragments"] = parsed["fragments"]
                op["declared"] = first.get("variables") or {}
            if _is_persisted(b):
                op["persisted_query"] = "the page sends a hash of a stored query, not the text"
            for k, v in (
                (b.get("variables") or {}).items() if isinstance(b.get("variables"), dict) else []
            ):
                op["variables"].setdefault(_field_name(k), _shape(v))
            if isinstance(a, dict):
                if a.get("data") is not None and op["data"] is None:
                    op["data"] = _shape(a["data"])
                if a.get("errors"):
                    op["errors"] += 1
    out = {}
    for name, op in ops.items():
        op["statuses"] = sorted(op["statuses"])
        declared = op.pop("declared", {})
        op["variables"] = {k: {"type": declared.get(k) or "", "json": v}
                           for k, v in op["variables"].items()} | {
            k: {"type": t, "json": "not sent"} for k, t in declared.items()
            if k not in op["variables"]}  # fmt: skip
        if not op["errors"]:
            op.pop("errors")
        out[name] = op
    return {"endpoint_note": "One address, several operations: answer each operation by its "
                             "name with the selection listed (the shape of `data`).",
            "operations": out}  # fmt: skip


def _jsonrpc(raws: list[Raw]) -> dict:
    methods: dict[str, dict] = {}
    for r in raws:
        body = _json(r.post_text)
        answer = _json(r.text)
        items = body if isinstance(body, list) else [body]
        for b in items:
            if not isinstance(b, dict) or not isinstance(b.get("method"), str):
                continue
            name = b["method"] if CODE_TOKEN.match(b["method"]) else "(unnamed)"
            m = methods.setdefault(name, {"calls": 0, "params": None, "result": None,
                                          "error_codes": set()})  # fmt: skip
            m["calls"] += 1
            if m["params"] is None and "params" in b:
                m["params"] = _shape(b["params"])
            if isinstance(answer, dict):
                if "result" in answer and m["result"] is None:
                    m["result"] = _shape(answer["result"])
                err = answer.get("error")
                if isinstance(err, dict) and isinstance(err.get("code"), int):
                    m["error_codes"].add(err["code"])
    for m in methods.values():
        m["error_codes"] = sorted(m["error_codes"])
    return {"version": "2.0", "methods": methods}


# --- the application -----------------------------------------------------------------------------


def _third_party(host: str) -> tuple[str | None, str | None, str | None]:
    """(public domain, vendor, purpose) when the host belongs to a known vendor."""
    h = host.split(":")[0].lower()
    for domain, vendor, purpose in VENDORS:
        if h == domain or h.endswith("." + domain):
            return domain, vendor, purpose
    return None, None, None


def _redirect_chains(raws: list[Raw]) -> list[dict]:
    chains: dict[tuple, dict] = {}
    docs = [r for r in raws if r.own and kind_of(r) == "document"]
    used = set()
    for i, r in enumerate(docs):
        if i in used or not (300 <= r.status < 400) or not r.header("location"):
            continue
        hops = []
        cur, j = r, i
        while 300 <= cur.status < 400 and cur.header("location") and len(hops) < 10:
            target = urlsplit(cur.header("location")).path or cur.path
            hops.append({"status": cur.status, "to": sanitize.route_pattern(target)})
            nxt = next((k for k in range(j + 1, min(j + 4, len(docs)))
                        if docs[k].path == target and docs[k].method == "GET"), None)  # fmt: skip
            if nxt is None:
                break
            used.add(nxt)
            cur, j = docs[nxt], nxt
        key = (r.key, tuple((h["status"], h["to"]) for h in hops))
        have = chains.setdefault(key, {"start": r.key, "hops": hops, "ends": None, "seen": 0})
        have["seen"] += 1
        if not (300 <= cur.status < 400):
            have["ends"] = f"{cur.status} {cur.route}"
    return list(chains.values())


def _streams(raws: list[Raw], screen_of) -> list[dict]:
    out: dict[str, dict] = {}
    for r in raws:
        k = kind_of(r)
        if not r.own or k not in ("stream", "websocket"):
            continue
        s = out.setdefault(r.key, {"endpoint": r.key, "type": "server-sent events" if k == "stream"
                                   else "WebSocket", "opened": 0, "query": set(), "events": {},
                                   "screens": set()})  # fmt: skip
        s["opened"] += 1
        s["query"] |= {_field_name(n) for n, _ in r.query}
        scr = screen_of(r)
        if scr:
            s["screens"].add(scr)
        if k == "stream":
            if not r.text:
                s.setdefault("note", "a stream was still open when the recording ended: no "
                                     "events read from it")  # fmt: skip
            for block in re.split(r"\r?\n\r?\n", r.text or ""):
                ev, data, has_id = "message", [], False
                for line in block.splitlines():
                    name, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if name == "event":
                        ev = value.strip() if CODE_TOKEN.match(value.strip()) else "(named)"
                    elif name == "data":
                        data.append(value)
                    elif name == "id":
                        has_id = True
                    elif name == "retry" and value.strip().isdigit():
                        s["retry_ms"] = int(value.strip())
                if not data:
                    continue
                e = s["events"].setdefault(ev, {"count": 0, "data": None, "id": False})
                e["count"] += 1
                e["id"] = e["id"] or has_id
                parsed = _json("\n".join(data))
                if e["data"] is None:
                    e["data"] = _shape(parsed) if parsed is not None else "text"
        else:
            for m in r.ws:
                way = "sent" if m.get("type") == "send" else "received"
                parsed = _json(m.get("data")) if m.get("opcode", 1) == 1 else None
                kind = "binary" if m.get("opcode") == 2 else "text"
                label = way
                if isinstance(parsed, dict):
                    for tk in ("type", "event", "action", "op", "channel", "method"):
                        v = parsed.get(tk)
                        if (
                            isinstance(v, str)
                            and CODE_TOKEN.match(v)
                            and not re.search(r"\d{3}", v)
                        ):
                            label = f"{way} {tk}={v}"
                            break
                e = s["events"].setdefault(label, {"count": 0, "data": None, "kind": kind})
                e["count"] += 1
                if e["data"] is None and parsed is not None:
                    e["data"] = _shape(parsed)
    # long polling: a GET held by the server, again and again
    by: dict[str, list[Raw]] = defaultdict(list)
    for r in raws:
        if r.own and r.method == "GET" and kind_of(r) == "api":
            by[r.key].append(r)
    for key, group in by.items():
        held = [r for r in group if r.wait >= LONG_POLL_MS]
        if len(held) >= 3 and len(held) >= len(group) * 0.6:
            out[key] = {"endpoint": key, "type": "long polling", "opened": len(group),
                        "server_wait_median_ms": round(statistics.median(r.wait for r in held))}  # fmt: skip
    for s in out.values():
        for k in ("query", "screens"):
            if k in s:
                s[k] = sorted(s[k])
    return list(out.values())


def _assets(raws: list[Raw]) -> dict:
    out: dict[str, dict] = {}
    for r in raws:
        if not r.own:
            continue
        k = kind_of(r)
        if k not in ("script", "style", "image", "font", "media"):
            continue
        a = out.setdefault(k, {"requests": 0, "files": {}, "bytes": 0, "from_browser_cache": 0})
        a["requests"] += 1
        if r.from_cache:
            a["from_browser_cache"] += 1
        if k == "image":
            stem = r.route.rsplit("/", 1)
            ext = re.search(r"\.([A-Za-z0-9]{1,5})$", r.path)
            pattern = (stem[0] if len(stem) > 1 else "") + "/{file}" + (
                f".{ext.group(1).lower()}" if ext else "")  # fmt: skip
        else:
            pattern = r.route
            names = sorted({_field_name(n) for n, _ in r.query})
            if names:
                pattern += "?" + "&".join(f"{n}=" for n in names)
        f = a["files"].setdefault(pattern, {"requests": 0, "distinct": set(), "bytes": 0})
        f["requests"] += 1
        f["distinct"].add(r.path)
        if r.size and r.size > 0 and not r.from_cache:
            f["bytes"] = max(f["bytes"], r.size)
        cache = _cache(r)
        if cache and not r.from_cache:
            f["caching"] = cache
        enc = r.header("content-encoding")
        if enc:
            f["compression"] = enc
    for a in out.values():
        for f in a["files"].values():
            f["distinct"] = len(f["distinct"])
            a["bytes"] += f["bytes"] * max(1, f["distinct"])
    return out


def _third_parties(raws: list[Raw], screen_of, page_origins: set[str]) -> tuple[list, list, set]:
    groups: dict[str, list[Raw]] = defaultdict(list)
    for r in raws:
        if not r.own:
            groups[r.host].append(r)
    third, apis, shown = [], [], set()
    n_third = n_api = 0
    for host, group in sorted(groups.items(), key=lambda kv: kv[1][0].n):
        domain, vendor, purpose = _third_party(host)
        kinds = Counter(_tp_kind(r) for r in group)
        reads = [r for r in group if "json" in r.mime and r.status < 400 and r.text]
        is_app_api = (domain is None and reads and any(
            _cors(r, page_origins).get("allow_origin") == "the page's origin" for r in reads)
            and kinds.get("api answer the page reads", 0) >= len(group) * 0.5)  # fmt: skip
        if is_app_api:
            n_api += 1
            apis.append({
                "origin": f"api-origin-{n_api}",
                "note": "The application's own API served from another origin (CORS answers name "
                        "the page's origin). Its calls are not in the endpoint files: serve them "
                        "from the demo's own origin at the same paths, or keep a second origin "
                        "with the same CORS headers.",
                "endpoints": sorted({r.key for r in group}),
                "cors": _cors(reads[-1], page_origins),
                "time_ms": _ms([r.ms for r in group]),
            })  # fmt: skip
            continue
        if domain is None:
            n_third += 1
            label = f"third-party-{n_third}"
            words = " ".join(r.path for r in group)
            purpose = next((p for rx, p in PURPOSE_WORDS if rx.search(words)), None) or (
                "files (scripts, styles or pictures)" if set(kinds) <= {"script", "style", "image",
                                                                         "font"} else "unknown")  # fmt: skip
        else:
            label = domain
            shown.add(domain)
        row = {
            "origin": label, "purpose": purpose, "requests": len(group),
            "kinds": dict(kinds.most_common()),
            "calls": sorted({f"{r.method} {r.route}" + (
                "?" + "&".join(sorted({_field_name(k) + "=" for k, _ in r.query})) if r.query else "")
                for r in group})[:20],
            "screens": sorted({s for s in (screen_of(r) for r in group) if s})[:20],
            "page_needs": _needs(kinds),
            "build": "Never call it and never copy it. Leave the tag out or put a stub of the same "
                     "name that does nothing; the demo must work with this origin unreachable.",
        }  # fmt: skip
        if vendor:
            row["vendor"] = vendor
        cors = next((_cors(r, page_origins) for r in group if _cors(r, page_origins)), None)
        if cors:
            row["cors"] = cors
        third.append(row)
    return third, apis, shown


def _tp_kind(r: Raw) -> str:
    k = kind_of(r)
    if k == "image" and (r.query or (r.size or 0) < 100):
        return "tracking pixel"
    if k in ("script", "style", "font", "image", "media"):
        return k
    if r.method in ("POST", "PUT") and (r.status == 204 or not r.text):
        return "beacon (no answer read)"
    if "json" in r.mime and r.text:
        return "api answer the page reads"
    if k == "document":
        return "page in a frame"
    return k


def _needs(kinds: Counter) -> str:
    parts = []
    if kinds.get("script"):
        parts.append("a script the pages load (the page may call its global: give a no-op stub)")
    if kinds.get("tracking pixel") or kinds.get("beacon (no answer read)"):
        parts.append("fire-and-forget calls: nothing the page shows depends on them")
    if kinds.get("api answer the page reads"):
        parts.append("answers the page reads: a stub endpoint of the demo answers the same keys")
    if kinds.get("page in a frame"):
        parts.append("a page shown in a frame: a blank stand-in page")
    if kinds.get("style") or kinds.get("font"):
        parts.append("styles or fonts: use the look's files or a local fallback")
    return "; ".join(parts) or "nothing the page shows"


def _page_loads(raws: list[Raw], keys: set[str], actions: list[datetime]) -> dict[str, list[str]]:
    """Per screen, the endpoints a page load called, in order (the most common order): the
    calls after the page document until a person acted again."""
    orders: dict[str, Counter] = defaultdict(Counter)
    docs = [i for i, r in enumerate(raws) if r.own and kind_of(r) == "document" and r.status == 200]
    for di, i in enumerate(docs):
        doc = raws[i]
        end = docs[di + 1] if di + 1 < len(docs) else len(raws)
        seq = []
        start = doc.started.replace(tzinfo=None) if doc.started else None
        stop = next((a for a in actions if start and a > start), None)
        for r in raws[i + 1 : end]:
            if not r.own or r.key not in keys or not r.started or not start:
                continue
            at = r.started.replace(tzinfo=None)
            if (at - start).total_seconds() > 2.5 or (stop and at > stop):
                break
            if r.key not in seq:
                seq.append(r.key)
        if seq:
            orders[doc.route][tuple(seq)] += 1
    return {screen: list(c.most_common(1)[0][0]) for screen, c in orders.items()}


def build(ctxs: list, endpoints: dict[str, dict]) -> dict:
    """{"endpoints": {file name: network facts}, "network": the application's document}."""
    from labctl import har

    by_key = {f"{d.get('method')} {d.get('path')}": name for name, d in endpoints.items()}
    eps: dict[str, _Ep] = defaultdict(_Ep)
    all_raws: list[Raw] = []
    page_origins: set[str] = set()
    steps_by: dict[tuple[str, int], dict] = {}
    actions: list[datetime] = []  # when a person acted (a page load's calls end there)
    for c in ctxs:
        when = {r.get("seq"): r.get("ts") for r in c.part.raw}
        for s in c.steps():
            steps_by[(c.name, s.get("n"))] = {**s, "ts": when.get(s.get("n"))}
        actions += [t.replace(tzinfo=None) for r in c.part.raw
                    if r.get("action") not in ("goto", None) and (t := _when(r.get("ts")))]  # fmt: skip
        own = {urlsplit(str(s.get("url") or "")).netloc.lower() for s in c.part.raw
               if str(s.get("url") or "").startswith("http")}  # fmt: skip
        page_origins |= {f"{urlsplit(str(s.get('url'))).scheme}://{h}" for s in c.part.raw
                         for h in [urlsplit(str(s.get("url") or "")).netloc.lower()] if h}  # fmt: skip
        try:
            raws = read(har.load(c.part.path).har, own)
        except (OSError, ValueError):
            continue
        uploads = _uploads(c)
        index: dict[str, list[tuple]] = defaultdict(list)
        sources = list(_sources(raws))
        pos = 0
        visit = 0  # page loads so far: a value is taken from where the current page got it
        last_fail: dict[tuple, Raw] = {}
        for r in raws:
            if r.own and r.status == 200 and kind_of(r) == "document":
                visit += 1
            while (
                pos < len(sources) and sources[pos][0] and r.started and sources[pos][0] < r.started
            ):
                _, value, label = sources[pos]
                index[value].append((visit, label))
                pos += 1
            if not r.own:
                continue
            name = by_key.get(r.key)
            if name is None:
                continue
            ep = eps[name]
            ep.raws.append(r)
            for fld, value in _targets(r):
                found = [x for x in index.get(value, []) if not _echo(x[1], r.key)]
                if found:
                    here = [label for v, label in found if v == visit]
                    ep.deps[(fld, here[0] if here else found[-1][1])] += 1
            sig = (r.key, r.post_text, tuple(r.query))
            failed = last_fail.get(sig)
            if failed is not None and r.started and failed.started:
                gap = (r.started - failed.started).total_seconds()
                if 0 <= gap <= RETRY_WINDOW:
                    ep.retries.append({"after": failed.status, "delay_ms": round(gap * 1000),
                                       "then": r.status,
                                       "retry_after_header": bool(failed.header("retry-after"))})  # fmt: skip
                last_fail.pop(sig, None)
            if r.status >= 500 or r.status in (0, 408, 429):
                last_fail[sig] = r
            if r.req_mime.startswith("multipart/"):
                near = [u for u in uploads if u["ts"] and r.started
                        and 0 <= (r.started - u["ts"]).total_seconds() < 120]  # fmt: skip
                for u in near[-3:]:
                    if u not in ep.uploads:
                        ep.uploads.append({k: v for k, v in u.items() if k != "ts"})
        all_raws += raws

    def ctx_step(rec, n):
        return steps_by.get((rec, n))

    loads = sorted(r.started.replace(tzinfo=None) for r in all_raws
                   if r.own and r.started and r.status == 200 and kind_of(r) == "document")  # fmt: skip
    out_eps = {}
    for name, ep in eps.items():
        out_eps[name] = endpoint_facts(next(k for k, v in by_key.items() if v == name), ep,
                                       endpoints.get(name), ctx_step, page_origins, loads)  # fmt: skip

    shown: list[tuple[datetime, str]] = sorted(
        (r.started.replace(tzinfo=None), r.route) for r in all_raws
        if r.own and r.started and kind_of(r) == "document" and r.status == 200)  # fmt: skip

    def screen_of(r: Raw) -> str | None:
        """The screen a request was made from: the page document loaded last before it."""
        if r.started is None:
            return None
        at = r.started.replace(tzinfo=None)
        return next((route for t, route in reversed(shown) if t <= at), None)

    third, other_apis, vendors = _third_parties(all_raws, screen_of, page_origins)
    errors = []
    for name, ep in sorted(eps.items()):
        for status, n in sorted(Counter(r.status for r in ep.raws if r.status >= 400).items()):
            errors.append({"endpoint": ep.raws[0].key, "status": status, "count": n,
                           "body": "see the endpoint's answer schema for this status",
                           "file": f"api/{name}"})  # fmt: skip
    for r in all_raws:
        if r.own and r.status >= 400 and r.key not in by_key and kind_of(r) == "document":
            errors.append({"endpoint": r.key, "status": r.status, "count": 1, "body": "a page"})
    docs: dict[str, dict] = {}
    for r in all_raws:
        if r.own and kind_of(r) == "document":
            d = docs.setdefault(r.route, {"requests": 0, "statuses": set(), "_ms": []})
            d["requests"] += 1
            d["statuses"].add(r.status)
            d["_ms"].append(r.ms)
            cache = _cache(r)
            if cache:
                d["caching"] = cache
    for d in docs.values():
        d["statuses"] = sorted(d["statuses"])
        d["time_ms"] = _ms(d.pop("_ms"))
    slow = sorted(((f.get("time_ms") or {}).get("median", 0), endpoints[n].get("method", "") + " "
                   + endpoints[n].get("path", "")) for n, f in out_eps.items())[::-1][:5]  # fmt: skip
    network = {
        "format": FORMAT,
        "about": ABOUT,
        "page_loads": _page_loads(all_raws, set(by_key), sorted(actions)),
        "documents": docs,
        "redirect_chains": _redirect_chains(all_raws),
        "streams": _streams(all_raws, screen_of),
        "static_files": _assets(all_raws),
        "third_parties": third,
        "other_origin_apis": other_apis,
        "errors": errors,
        "retries": [
            {"endpoint": endpoints[n].get("method", "") + " " + endpoints[n].get("path", ""), **r}
            for n, ep in eps.items()
            for r in ep.retries
        ],  # fmt: skip
        "slowest": [{"endpoint": k, "median_ms": ms} for ms, k in slow if ms],
        "csrf": sorted(
            {
                f"{c['name']} ({c['in']}) from "
                + re.sub(r" of the page `[^`]*`$", " of each page", c["comes_from"])
                for f in out_eps.values()
                for c in f.get("csrf") or []
            }
        ),  # fmt: skip
        "_vendors": sorted(vendors),
    }
    return {
        "endpoints": out_eps,
        "network": network,
        "raws": {name: ep.raws for name, ep in eps.items()},
    }


ABOUT = (
    "What the recorded network shows beyond the endpoint contract. page_loads: the calls each "
    "screen's load made, in order. documents: each screen's page requests, statuses and times. "
    "redirect_chains: every redirect with its hops. streams: server-sent events, WebSocket "
    "messages and long polling (event names and data shapes). static_files: the files of the "
    "application by kind, with sizes and caching: serve the same set the same way. "
    "third_parties: other origins by purpose, never to be called or copied. errors and retries: "
    "the error answers and the calls the page repeated. Per endpoint, the same facts are under "
    "`network` in its file. No value of the recording is in this file."
)


def extend(root: Path, app: str, ctxs: list, built: dict) -> list[str]:
    """Add `network` to each endpoint document of `built` (labctl.apicatalog.build) and
    `built["network"]`; returns the problems (a file left out by the checks)."""
    from labctl import codemap

    endpoints = built.get("endpoints") or {}
    if not ctxs or not endpoints:
        return []
    found = build(ctxs, endpoints)
    own = {urlsplit(str(s.get("url") or "")).netloc.lower() for c in ctxs for s in c.part.raw
           if str(s.get("url") or "").startswith("http")}  # fmt: skip
    vendors = set(found["network"].pop("_vendors"))
    problems = []
    for name, facts in found["endpoints"].items():
        if "graphql" in facts:
            _graphql_examples(endpoints[name], found["raws"].get(name) or [])
        text = json.dumps(facts, ensure_ascii=False)
        bad = codemap.check_text(text, own | vendors)
        if bad:
            problems.append(f"{name}: network facts left out ({'; '.join(bad[:3])})")
            continue
        endpoints[name]["network"] = facts
    text = json.dumps(found["network"], ensure_ascii=False)
    bad = codemap.check_text(text, own | vendors)
    if bad:
        problems.append(f"{FILE}: not written ({'; '.join(bad[:3])})")
    else:
        built["network"] = found["network"]
    return problems


# --- the brief -----------------------------------------------------------------------------------


def graphql_text(query: str) -> str:
    """A GraphQL document on one line with every literal replaced (`"..."`, `0`): the code of
    the request without a value of the recording."""
    out = []
    for t in GQL_TOKEN.findall(query or ""):
        if t.startswith("#"):
            continue
        out.append('"..."' if t.startswith('"') else "0" if re.fullmatch(r"-?\d.*", t) else t)
    text = " ".join(out)
    text = re.sub(r"([(\[$])\s+", lambda m: m.group(1), text)
    return re.sub(r"\s+([,:()\]!])", lambda m: m.group(1), text)


def _graphql_examples(doc: dict, raws: list[Raw]) -> None:
    """The synthetic examples of a GraphQL endpoint get the request's document and operation
    name back (both are code), in place of generated text."""
    calls = doc.get("calls") or []
    first = None
    for i, call in enumerate(calls):
        body = _json(raws[i].post_text) if i < len(raws) else None
        req = call.get("request")
        if not isinstance(body, dict) or not isinstance(req, dict):
            continue
        if isinstance(body.get("query"), str):
            req["query"] = graphql_text(body["query"])
        name = body.get("operationName")
        if "operationName" in req:
            req["operationName"] = (
                name if isinstance(name, str) and CODE_TOKEN.match(name) else None
            )
        first = first or req
    example = (doc.get("request") or {}).get("example")
    if isinstance(example, dict) and first is not None:
        for k in ("query", "operationName"):
            if k in first:
                example[k] = first[k]


def brief_lines(net: dict | None) -> list[str]:
    """Short lines for the brief's API section."""
    if not net:
        return []
    out = [f"**Network behaviour** (`api/{FILE}`, and `network` in each endpoint file)."]
    for screen, calls in sorted((net.get("page_loads") or {}).items()):
        out.append(f"- Loading `{screen}` calls, in order: " + ", ".join(f"`{c}`" for c in calls))
    for ch in net.get("redirect_chains") or []:
        hops = " -> ".join(f"{h['status']} `{h['to']}`" for h in ch["hops"])
        out.append(f"- Redirect chain: `{ch['start']}` -> {hops}")
    for s in net.get("streams") or []:
        evs = ", ".join(f"`{k}`" for k in s.get("events") or {})
        out.append(f"- Stream `{s['endpoint']}`: {s['type']}" + (f", events {evs}" if evs else ""))
    for t in net.get("third_parties") or []:
        out.append(f"- Third party `{t['origin']}` ({t['purpose']}): {t['page_needs']}. Never "
                   "call it.")  # fmt: skip
    for r in net.get("retries") or []:
        out.append(f"- `{r['endpoint']}` answered {r['after']} and the page tried again after "
                   f"{r['delay_ms']} ms ({r['then']})")  # fmt: skip
    for c in net.get("csrf") or []:
        out.append(f"- CSRF token: {c}")
    return out


# --- the build documents (labctl.builder.docs) ---------------------------------------------------


def endpoint_lines(net: dict | None) -> list[str]:
    """Short facts of one endpoint for a page document (each without the endpoint's name)."""
    if not net:
        return []
    out = []
    t = net.get("time_ms")
    if t:
        out.append(f"answers in {t['median']} ms (median; max {t['max']} ms over {t['calls']} "
                   "call(s)): answer about as fast")  # fmt: skip
    for c in net.get("csrf") or []:
        out.append(f"sends the CSRF token as {c['in']} `{c['name']}`, taken from {c['comes_from']}")
    body = net.get("body") or {}
    if body.get("format") == "multipart/form-data":
        parts = []
        for k, v in (body.get("fields") or {}).items():
            bits = [v.get("kind") or "text"]
            if v.get("content_type"):
                bits.append(v["content_type"])
            if v.get("accept"):
                bits.append(f"accepts {v['accept']}")
            if v.get("size_class"):
                bits.append(f"size {v['size_class']}")
            parts.append(f"`{k}` ({', '.join(bits)})")
        out.append("sends multipart/form-data: " + ", ".join(parts))
    elif body.get("format") in ("GraphQL", "JSON-RPC", "SOAP", "XML"):
        ops = (net.get("graphql") or {}).get("operations") or (net.get("json_rpc") or {}).get(
            "methods") or {}  # fmt: skip
        names = (": operations " + ", ".join(f"`{o}`" for o in ops)) if ops else ""
        out.append(f"is {body['format']}{names} (see `network` in its file)")
    for d in net.get("depends_on") or []:
        out.append(f"takes `{d['field']}` from {d['from']}")
    for status, a in (net.get("answers") or {}).items():
        bits = []
        for k, v in (a.get("caching") or {}).items():
            bits.append(f"{k} {' '.join(v) if isinstance(v, list) else v}")
        if a.get("from_browser_cache"):
            bits.append(f"{a['from_browser_cache']} from the browser cache")
        if a.get("conditional_requests"):
            bits.append(a["conditional_requests"])
        if a.get("compression"):
            bits.append("compressed " + ", ".join(a["compression"]))
        for h, what in (a.get("headers") or {}).items():
            bits.append(f"header `{h}`: {what}")
        if bits:
            out.append(f"answer `{status}`: " + "; ".join(bits))
    paging = net.get("paging") or {}
    if paging:
        out.append("pages its answers: " + "; ".join(
            f"{k} {', '.join(v) if isinstance(v, list) else v}" for k, v in paging.items()))  # fmt: skip
    for r in net.get("retries") or []:
        out.append(f"answered {r['after']}; the page tried again after {r['delay_ms']} ms and got "
                   f"{r['then']}")  # fmt: skip
    if net.get("role"):
        out.append(f"is {net['role']}")
    return out


def architecture_lines(net: dict | None, app: str) -> list[str]:
    """The *Network behaviour* section of ARCHITECTURE.md."""
    if not net:
        return []
    out = ["## Network behaviour", "", f"From `{app}/api/{FILE}` (no value of the recording):", ""]
    for screen, calls in sorted((net.get("page_loads") or {}).items()):
        out.append(f"- Loading `{screen}` calls, in order: " + ", ".join(f"`{c}`" for c in calls))
    for ch in net.get("redirect_chains") or []:
        hops = " -> ".join(f"{h['status']} `{h['to']}`" for h in ch["hops"])
        out.append(f"- Redirect chain `{ch['start']}`: {hops}"
                   + (f", ends {ch['ends']}" if ch.get("ends") else ""))  # fmt: skip
    for s in net.get("streams") or []:
        evs = []
        for k, v in (s.get("events") or {}).items():
            data = f" data {json.dumps(v['data'])}" if v.get("data") else ""
            evs.append(f"`{k}` x{v['count']}{data}")
        out.append(f"- Stream `{s['endpoint']}` ({s['type']}, opened {s['opened']} time(s)"
                   + (f", retry {s['retry_ms']} ms" if s.get("retry_ms") else "") + ")"
                   + (": " + "; ".join(evs) if evs else "")
                   + (f". {s['note']}" if s.get("note") else ""))  # fmt: skip
    static = net.get("static_files") or {}
    if static:
        out += ["", "| Static files | Requests | Files | Bytes | From the browser cache | Caching |",
                "|---|---|---|---|---|---|"]  # fmt: skip
        for kind, a in static.items():
            caching = sorted({(f.get("caching") or {}).get("cache_control", "-")
                              for f in a["files"].values()})  # fmt: skip
            files = sum(f["distinct"] for f in a["files"].values())
            out.append(f"| {kind} | {a['requests']} | {files} | {a['bytes']} | "
                       f"{a['from_browser_cache']} | {'; '.join(caching)} |")  # fmt: skip
        out.append("")
    for t in net.get("third_parties") or []:
        out.append(f"- Third party `{t['origin']}`" + (f" ({t['vendor']})" if t.get("vendor") else "")
                   + f", {t['purpose']}: {t['requests']} request(s), {t['page_needs']}. "
                   + t["build"])  # fmt: skip
    for a in net.get("other_origin_apis") or []:
        out.append(f"- `{a['origin']}`: {a['note']} Endpoints: "
                   + ", ".join(f"`{e}`" for e in a["endpoints"]))  # fmt: skip
    for e in net.get("errors") or []:
        out.append(f"- Error answer: `{e['endpoint']}` {e['status']} x{e['count']}")
    for r in net.get("retries") or []:
        out.append(f"- Retry: `{r['endpoint']}` answered {r['after']}, asked again after "
                   f"{r['delay_ms']} ms ({r['then']})")  # fmt: skip
    for c in net.get("csrf") or []:
        out.append(f"- CSRF token: {c}")
    return [*out, ""]
