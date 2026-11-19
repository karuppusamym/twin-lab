"""`labctl evidence fetch-code`: download the code a recording never loaded, and nothing else.

A lazy chunk is downloaded by the browser only when someone opens its route. The route table in
the code still names it (`labctl.codemap`), so the map of the application can be completed by
asking the application's own server for those static files. This is the only request Twin Lab
makes of the real application outside a recording, so it is narrow:

- **opt-in**: without `yes=True` (`--yes`) nothing is asked; the plan is only listed;
- **only static code**: a path ending `.js`, `.mjs`, `.css` or `.json`, never under an API path,
  never with a word that changes state (`logout`, `delete` ...), no query but a version
  (`?v=3`); the answer must be 200 with a content type of that kind of file, no larger than
  `MAX_BYTES`; a redirect is refused, not followed;
- **only the application's own origin** (the origin of the recorded pages), only GET;
- **rate limited**: at least `MIN_DELAY` seconds between two requests (default `DELAY`), at most
  `max_files` files, and it stops at the first 429 or 503;
- **no session** unless the person passes the recorder's saved browser state
  (`storage_state`): then only its cookies for that host are sent;
- the files are stored beside the recording (`<recording>/code/`), never in a package; the code
  map reads them like files of the HAR (`source: fetched`).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from labctl import codemap

EXT_TYPES = {
    ".js": ("javascript", "ecmascript"),
    ".mjs": ("javascript", "ecmascript"),
    ".css": ("text/css",),
    ".json": ("application/json", "+json", "text/json"),
}
VERSION_KEYS = {"v", "ver", "version", "rev", "hash", "h", "cb", "t", "ts", "_"}
STATE_WORDS = re.compile(
    r"log-?out|sign-?out|log-?off|delete|remove|destroy|reset|unsubscribe|cancel|approve|"
    r"submit|confirm|pay|transfer|purge|revoke|disable|enable|activate|kill",
    re.IGNORECASE,
)
API_SEGMENTS = {"api", "apis", "rest", "graphql", "rpc", "service", "services", "ws", "odata",
                "soap", "ajax", "data", "auth", "oauth", "session", "sessions"}  # fmt: skip
DELAY = 1.0
MIN_DELAY = 0.2
MAX_BYTES = 5 * 1024 * 1024
MAX_FILES = 50
USER_AGENT = "twinlab-fetch-code (static files only)"

Getter = Callable[[str, dict[str, str]], tuple[int, str, bytes]]


class FetchError(Exception):
    pass


def refusal(url: str, origins: set[str]) -> str | None:
    """Why an address may not be fetched (None when it may): only static code of the
    application's own origin."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "not an http(s) address"
    if parts.username or parts.password:
        return "an address with a user name or password"
    origin = f"{parts.scheme}://{parts.netloc.lower()}"
    if origin not in origins:
        return "not the application's own origin"
    path = parts.path
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if ext not in EXT_TYPES:
        return "not a static script, style sheet or JSON file by its extension"
    if ".." in path.split("/"):
        return "a path that climbs out of its folder"
    if {s.lower() for s in path.split("/")} & API_SEGMENTS:
        return "under an API path"
    if STATE_WORDS.search(path):
        return "a word in its address that could change state"
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() not in VERSION_KEYS:
            return "a query that is not a version"
    return None


def type_ok(path: str, content_type: str) -> bool:
    ext = "." + path.rsplit(".", 1)[-1].lower()
    kind = content_type.split(";")[0].strip().lower()
    return any(k in kind for k in EXT_TYPES.get(ext, ()))


def origins_of(recording: Path) -> set[str]:
    out = set()
    steps = Path(recording) / "steps.jsonl"
    for line in (
        steps.read_text(encoding="utf-8", errors="replace").splitlines() if steps.is_file() else []
    ):
        try:
            url = str(json.loads(line).get("url") or "")
        except ValueError:
            continue
        if url.startswith(("http://", "https://")):
            p = urlsplit(url)
            out.add(f"{p.scheme}://{p.netloc.lower()}")
    return out


def plan(recording: Path) -> list[dict]:
    """The files the code names and the recording does not hold: (url, why, routes). Only files
    of an origin the recording was on; a chunk the code names by a relative path is resolved
    against the script that names it."""
    recording = Path(recording)
    hosts = codemap.own_hosts([recording])
    res = codemap.resources([recording], hosts)
    an = codemap.Analysis(res, hosts)
    have = {(r.host, r.path) for r in res}
    by_path = {r.path: r for r in res if r.kind == "script"}
    wanted: dict[str, list[str]] = {}
    for r in an.routes:
        ch = r.get("lazy_chunk")
        if ch and not ch.startswith("("):
            wanted.setdefault(ch, []).append(r["path"])
    for ch in an.dynamic_refs:
        wanted.setdefault(ch, [])
    out = []
    for path, routes in wanted.items():
        refs = an.dynamic_refs.get(path) or [r.get("file") for r in an.routes
                                             if r.get("lazy_chunk") == path]  # fmt: skip
        src = next((by_path[p] for p in refs if p in by_path), None)
        host = src.host if src else next(iter(sorted(hosts)), "")
        scheme = urlsplit(src.url).scheme if src else "https"
        if not host or (host, path) in have:
            continue
        out.append({"url": f"{scheme}://{host}{path}", "path": path,
                    "why": "a lazy chunk the code loads" + (f" for {', '.join(sorted(set(routes)))}"
                                                            if routes else ""),
                    "routes": sorted(set(routes))})  # fmt: skip
    return out


def _cookies(storage_state: Path | None, host: str) -> str:
    if storage_state is None:
        return ""
    try:
        state = json.loads(Path(storage_state).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FetchError(f"cannot read the saved browser state {storage_state}: {exc}") from exc
    now = time.time()
    name = host.split(":")[0].lower()
    out = []
    for c in state.get("cookies") or []:
        domain = str(c.get("domain") or "").lstrip(".").lower()
        if not domain or not (name == domain or name.endswith("." + domain)):
            continue
        expires = c.get("expires")
        if isinstance(expires, (int, float)) and 0 < expires < now:
            continue
        out.append(f"{c.get('name')}={c.get('value')}")
    return "; ".join(out)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def http_get(url: str, headers: dict[str, str]) -> tuple[int, str, bytes]:
    """GET without following redirects. (status, content type, body)."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=20) as r:  # noqa: S310 - checked by `refusal` first
            body = r.read(MAX_BYTES + 1)
            return r.status, r.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", b""


def _safe_name(path: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/")) or "file"
    name, n = base[-120:], 1
    while name in taken:
        n += 1
        name = f"{n}-{base[-110:]}"
    taken.add(name)
    return name


def fetch(recording: Path, *, yes: bool = False, storage_state: Path | None = None,
          delay: float = DELAY, max_files: int = MAX_FILES, rounds: int = 3,
          get: Getter | None = None, sleep: Callable[[float], None] = time.sleep,
          say: Callable[[str], None] = lambda m: None) -> dict:  # fmt: skip
    """Fetch the static files the code names and the recording lacks (see the module text).
    Returns {"planned": [...], "fetched": [...], "refused": [...], "stopped": reason|None}."""
    recording = Path(recording)
    if not (recording / "steps.jsonl").is_file():
        raise FetchError(f"{recording} is not a recording folder (no steps.jsonl)")
    origins = origins_of(recording)
    get = get or http_get
    delay = max(float(delay), MIN_DELAY)
    folder = recording / codemap.CODE_DIR
    manifest_path = folder / codemap.CODE_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {"files": [], "refused": []}
    taken = {str(r.get("file")) for r in manifest["files"]}
    report: dict = {"planned": [], "fetched": [], "refused": [], "stopped": None}
    asked: set[str] = {str(r.get("url")) for r in manifest["files"]}
    last = 0.0
    for _ in range(max(1, rounds)):
        todo = [p for p in plan(recording) if p["url"] not in asked]
        report["planned"] += todo
        if not yes or not todo:
            break
        for item in todo:
            url = item["url"]
            asked.add(url)
            why = refusal(url, origins)
            if why:
                report["refused"].append({"path": item["path"], "reason": why})
                continue
            if len(report["fetched"]) >= max_files:
                report["stopped"] = f"the limit of {max_files} files"
                break
            wait = last + delay - time.monotonic()
            if last and wait > 0:
                sleep(wait)
            headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
            cookie = _cookies(storage_state, urlsplit(url).netloc)
            if cookie:
                headers["Cookie"] = cookie
            status, ctype, body = get(url, headers)
            last = time.monotonic()
            say(f"GET {item['path']} -> {status}")
            if status in (429, 503):
                report["stopped"] = f"the server answered {status}: stopped, try later"
                break
            if status != 200:
                report["refused"].append({"path": item["path"], "reason": f"answered {status}"})
                continue
            if not type_ok(urlsplit(url).path, ctype):
                report["refused"].append({"path": item["path"], "reason": "the answer's content "
                                          "type is not that of a static file of its kind"})  # fmt: skip
                continue
            if len(body) > MAX_BYTES:
                report["refused"].append({"path": item["path"], "reason": "larger than allowed"})
                continue
            folder.mkdir(parents=True, exist_ok=True)
            name = _safe_name(item["path"], taken)
            (folder / name).write_bytes(body)
            row = {"url": url, "file": name, "content_type": ctype.split(";")[0].strip(),
                   "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                   "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "why": item["why"], "with_session": bool(cookie)}  # fmt: skip
            manifest["files"].append(row)
            report["fetched"].append(row)
            manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        if report["stopped"]:
            break
    if report["refused"]:
        manifest.setdefault("refused", []).extend(report["refused"])
        if yes:
            folder.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return report
