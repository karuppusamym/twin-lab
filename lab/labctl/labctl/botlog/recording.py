"""Reading a recording: `evidence/<app>/<recording-id>/` with `recording.har`, `dom/<seq>.html`
(outerHTML after each step) and `steps.jsonl`.

steps.jsonl lines: `{"seq", "ts", "url", "action": "goto|click|type|select|submit", "selector",
"dom": "dom/0003.html"}`. `ts` is when the action started and `url` is the page's URL after it.
The harness bot also writes `value` for typed and selected values that are not Private; other
writers may omit it, in which case the value is read from the after-snapshot.

The recorder also writes (all optional, so older recordings load unchanged):

- actions `keypress` (with `key` and `modifiers`: only non-text keys and chords, never typed
  characters), `dblclick`, `check` and `uncheck`;
- `locators`: ranked candidates for the target, each with the number of elements it matched when
  recorded (see labctl.locate.resolve);
- `blank`: a typed value that was empty or only spaces (the value itself, as it is not data);
- `nav` on a `goto` (`navigate`, `reload`, `back_forward`, `push`, `replace`, `pop`) and
  `caused_by` (the seq of the click or key press that caused a submit or navigation);
- `tab`, `opener` and `origin`: the tab a step happened in, the tab that opened it, and the
  top-level page's origin; a recording that used several tabs also has the actions `tab-open`,
  `tab-switch` and `tab-close`, and `storage` (browser storage keys and masked shapes, read by
  labctl.har).

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
    typed_shape: str | None = None  # the recorder's shape of what was typed (never the value)
    dom_before: str | None = None  # the page as the action met it (the recorder writes it)
    before_scope: str | None = None  # "frame": dom_before is only the frame's document
    frame: str | None = None  # the frame the action happened in (`iframe#x >>> iframe#y`)
    key: str | None = None  # keypress: `Enter`, `Escape`, `a` (with modifiers: a chord)
    modifiers: list[str] = field(default_factory=list)  # keypress: Control, Alt, Meta, Shift
    locators: list[dict] = field(default_factory=list)  # ranked target candidates (locate.py)
    blank: str | None = None  # type: the value was empty or spaces only (kept, it is no data)
    nav: str | None = None  # goto: how the page was reached
    caused_by: int | None = None  # submit/goto: the step whose action caused it
    tab: str | None = None  # the tab it happened in (t1, t2...)
    opener: str | None = None  # the tab that opened that tab (a popup, target=_blank)
    origin: str | None = None  # of the top-level page

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

    def dom(self, step: RecStep | None, before: bool = False) -> str | None:
        rel = (step.dom_before if before else step.dom) if step is not None else None
        if not rel:
            return None
        target = (self.path / rel).resolve()
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
                        selector=_mask_ids(d.get("selector")),
                        dom=d.get("dom"),
                        value=d.get("value") if isinstance(d.get("value"), str) else None,
                        typed_shape=(
                            d.get("typed_shape") if isinstance(d.get("typed_shape"), str) else None
                        ),
                        dom_before=(
                            d.get("dom_before") if isinstance(d.get("dom_before"), str) else None
                        ),
                        before_scope=d.get("before_scope")
                        if isinstance(d.get("before_scope"), str)
                        else None,
                        **_extra(d),
                    )
                )
            except (ValueError, TypeError, AttributeError):
                rec.bad_lines += 1
    rec.steps.sort(key=lambda s: s.seq)
    har = path / "recording.har"
    if har.exists():
        rec.entries = read_har(har)
    return rec


def _extra(d: dict) -> dict:
    """The optional fields newer recorders write; anything malformed is left out."""
    out: dict = {}
    for name in ("frame", "key", "nav", "blank", "tab", "opener", "origin"):
        if isinstance(d.get(name), str):
            out[name] = d[name]
    mods = d.get("modifiers")
    if isinstance(mods, list):
        out["modifiers"] = [m for m in mods if isinstance(m, str)]
    locs = d.get("locators")
    if isinstance(locs, list):
        out["locators"] = [_mask_locator(c) for c in locs if isinstance(c, dict) and c.get("kind")]
    if isinstance(d.get("caused_by"), int):
        out["caused_by"] = d["caused_by"]
    return out


def _mask_locator(cand: dict) -> dict:
    """Record numbers in a candidate's ids and selectors are masked like page ids."""
    out = dict(cand)
    for name in ("css", "rows", "control", "value"):
        if isinstance(out.get(name), str):
            out[name] = _mask_ids(out[name])
    return out


def _mask_ids(selector):
    """Record numbers built into a recorded selector (#open-R-1001) are masked like page ids."""
    if not isinstance(selector, str):
        return selector
    from labctl.sanitize import mask_identifiers

    return mask_identifiers(selector)
