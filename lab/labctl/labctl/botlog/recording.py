"""Reading a recording: `evidence/<app>/<recording-id>/` with `recording.har`, `dom/<seq>.html`
(outerHTML after each step) and `steps.jsonl`.

steps.jsonl lines: `{"seq", "ts", "url", "action": "goto|click|type|select|submit", "selector",
"dom": "dom/0003.html"}`. `ts` is when the action started and `url` is the page's URL after it.
The harness bot also writes `value` for typed and selected values that are not Private; other
writers may omit it, in which case the value is read from the after-snapshot.

Only request shapes are taken from the HAR (method, URL path, status, timing). Headers, cookies
and bodies are never read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ASSET_EXT = re.compile(
    r"\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|otf|eot|mp4|webm)$", re.I
)
ASSET_TYPES = {"script", "stylesheet", "image", "font", "media", "manifest", "texttrack"}


@dataclass
class RecStep:
    seq: int
    ts: datetime | None
    url: str | None
    action: str
    selector: str | None
    dom: str | None
    value: str | None = None

    @property
    def path(self) -> str | None:
        return urlsplit(self.url).path if self.url else None


@dataclass
class HarEntry:
    method: str
    url: str
    status: int | None
    start: datetime | None
    end: datetime | None
    resource_type: str | None = None
    mime: str | None = None

    @property
    def path(self) -> str:
        return urlsplit(self.url).path or "/"

    @property
    def has_query(self) -> bool:
        return bool(urlsplit(self.url).query)

    def is_asset(self) -> bool:
        return is_asset(self.path, self.resource_type, self.mime)

    def key(self) -> str:
        return request_key(self.method, self.url)


@dataclass
class Recording:
    id: str
    path: Path
    app: str | None
    steps: list[RecStep] = field(default_factory=list)
    entries: list[HarEntry] = field(default_factory=list)
    bad_lines: int = 0

    def dom(self, step: RecStep | None) -> str | None:
        if step is None or not step.dom:
            return None
        target = (self.path / step.dom).resolve()
        if self.path.resolve() not in target.parents or not target.exists():
            return None
        return target.read_text(encoding="utf-8", errors="replace")

    def before(self, step: RecStep) -> RecStep | None:
        idx = self.steps.index(step)
        return self.steps[idx - 1] if idx > 0 else None

    def requests_between(self, start: datetime | None, end: datetime | None) -> list[HarEntry]:
        if start is None:
            return []
        return [
            e
            for e in self.entries
            if e.start is not None and e.start >= start and (end is None or e.start < end)
        ]


def _ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def is_asset(path: str, resource_type: str | None = None, mime: str | None = None) -> bool:
    if resource_type and resource_type.lower() in ASSET_TYPES:
        return True
    if mime and re.match(r"^(image|font|text/css|(application|text)/javascript)", mime):
        return True
    return bool(ASSET_EXT.search(path or ""))


def path_pattern(path: str) -> str:
    """`/api/cases/C-1003/notes` -> `/api/cases/*/notes`: segments holding a digit are ids."""
    segs = [("*" if re.search(r"\d", s) else s) for s in (path or "/").split("/")]
    return "/".join(segs) or "/"


def request_key(method: str, url: str) -> str:
    """`GET /api/cases/search*` (a query was present) or `PUT /api/cases/*`: method and pattern."""
    parts = urlsplit(url or "/")
    star = "*" if parts.query else ""
    return f"{(method or 'GET').upper()} {path_pattern(parts.path or '/')}{star}"


def read_har(path: Path) -> list[HarEntry]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    entries = []
    for e in (data.get("log") or {}).get("entries") or []:
        req = e.get("request") or {}
        res = e.get("response") or {}
        start = _ts(e.get("startedDateTime"))
        total = e.get("time")
        end = start + timedelta(milliseconds=float(total)) if start and total is not None else None
        entries.append(
            HarEntry(
                method=str(req.get("method") or "GET"),
                url=str(req.get("url") or ""),
                status=res.get("status") if isinstance(res.get("status"), int) else None,
                start=start,
                end=end,
                resource_type=e.get("_resourceType"),
                mime=(res.get("content") or {}).get("mimeType"),
            )
        )
    entries.sort(key=lambda x: x.start or datetime.min.replace(tzinfo=timezone.utc))
    return entries


def load(path: Path) -> Recording:
    path = Path(path)
    rec = Recording(id=path.name, path=path, app=path.parent.name or None)
    steps_file = path / "steps.jsonl"
    if steps_file.exists():
        for line in steps_file.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                rec.steps.append(
                    RecStep(
                        seq=int(d.get("seq") or len(rec.steps) + 1),
                        ts=_ts(d.get("ts")),
                        url=d.get("url"),
                        action=str(d.get("action") or ""),
                        selector=d.get("selector"),
                        dom=d.get("dom"),
                        value=d.get("value") if isinstance(d.get("value"), str) else None,
                    )
                )
            except (ValueError, TypeError, AttributeError):
                rec.bad_lines += 1
    rec.steps.sort(key=lambda s: s.seq)
    har = path / "recording.har"
    if har.exists():
        rec.entries = read_har(har)
    return rec
