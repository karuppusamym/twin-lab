"""NetLog parser (Prod Echo I-03): Chrome `--log-net-log` JSON -> one record per URL request.

Format (Prod Echo docs/05-capture-tools.md): `{"constants": {...}, "events": [...]}`. Event and
source types are integers named in `constants.logEventTypes` and `constants.logSourceType`;
`time` is milliseconds relative to `constants.timeTickOffset` (milliseconds since the Unix epoch).
A file from a browser that was killed lacks the closing `]}` and may end in a partial event; the
events before it are kept.

Headers are read for the method, path and status line only. Header values (cookies, tokens) are
never copied into the output.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from labctl.botlog.models import NetRequest

PHASE_BEGIN, PHASE_END = 1, 2
STATUS_LINE = re.compile(r"^HTTP/\S+\s+(\d{3})")
REQUEST_LINE = re.compile(r"^([A-Z]+)\s+(\S+)\s+HTTP/")


def _load(text: str) -> tuple[dict, list[dict], bool]:
    """(constants, events, complete). Tolerates a truncated tail."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data.get("constants") or {}, list(data.get("events") or []), True
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    constants: dict = {}
    m = re.search(r'"constants"\s*:\s*', text)
    if m:
        try:
            constants, _ = decoder.raw_decode(text, m.end())
        except ValueError:
            constants = {}
    events: list[dict] = []
    m = re.search(r'"events"\s*:\s*\[', text)
    if not m:
        return constants if isinstance(constants, dict) else {}, events, False
    pos = m.end()
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] == "]":
            break
        try:
            event, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break  # the partial last event of a killed browser
        if isinstance(event, dict):
            events.append(event)
    return constants if isinstance(constants, dict) else {}, events, False


def _names(table: Any) -> dict[int, str]:
    if not isinstance(table, dict):
        return {}
    return {v: k for k, v in table.items() if isinstance(v, int)}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _header_lines(params: dict) -> list[str]:
    headers = params.get("headers")
    if isinstance(headers, list):
        return [str(h) for h in headers]
    if isinstance(headers, dict):
        return [f"{k}: {v}" for k, v in headers.items()]
    return []


def _resolve(url: str | None, line: str | None, headers: list[str]) -> str | None:
    if url or not line:
        return url
    m = REQUEST_LINE.match(line)
    if not m:
        return None
    host = next((h.split(":", 1)[1].strip() for h in headers if h.lower().startswith("host:")), "")
    return f"https://{host}{m.group(2)}" if host else m.group(2)


def parse_text(text: str) -> list[NetRequest]:
    constants, events, _complete = _load(text)
    event_names = _names(constants.get("logEventTypes"))
    source_names = _names(constants.get("logSourceType"))
    phases = constants.get("logEventPhase") or {}
    begin = phases.get("PHASE_BEGIN", PHASE_BEGIN)
    end = phases.get("PHASE_END", PHASE_END)
    offset = _num(constants.get("timeTickOffset")) or 0.0

    def when(event: dict) -> datetime | None:
        t = _num(event.get("time"))
        if t is None:
            return None
        return datetime.fromtimestamp((offset + t) / 1000.0, tz=timezone.utc)

    found: dict[int, dict] = {}
    depends: dict[int, int] = {}  # source id -> the source id it was started by
    for event in events:
        source = event.get("source") or {}
        sid = source.get("id")
        if not isinstance(sid, int):
            continue
        stype = source.get("type")
        sname = source_names.get(stype, stype if isinstance(stype, str) else "")
        etype = event.get("type")
        ename = event_names.get(etype, etype if isinstance(etype, str) else "")
        phase = event.get("phase")
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        dep = params.get("source_dependency")
        if isinstance(dep, dict) and isinstance(dep.get("id"), int):
            depends.setdefault(sid, dep["id"])
        if sname != "URL_REQUEST":
            continue
        rec = found.setdefault(sid, {"source_id": sid})
        if ename == "REQUEST_ALIVE" and phase == begin:
            rec["start"] = when(event)
        elif ename == "REQUEST_ALIVE" and phase == end:
            rec["end"] = when(event)
            rec["complete"] = True
        elif ename in ("URL_REQUEST_START_JOB", "URL_REQUEST_REDIRECT_JOB"):
            if ename == "URL_REQUEST_START_JOB" and phase in (begin, None, 0):
                rec["url"] = params.get("url", rec.get("url"))
                rec["method"] = params.get("method", rec.get("method"))
                if params.get("initiator"):
                    rec["initiator"] = str(params["initiator"])
            rec.setdefault("start", when(event))
        elif ename in (
            "HTTP_TRANSACTION_SEND_REQUEST_HEADERS",
            "HTTP_TRANSACTION_HTTP2_SEND_REQUEST_HEADERS",
        ):
            lines = _header_lines(params)
            line = params.get("line") if isinstance(params.get("line"), str) else None
            m = REQUEST_LINE.match(line or (lines[0] if lines else ""))
            if m and not rec.get("method"):
                rec["method"] = m.group(1)
            rec["url"] = _resolve(rec.get("url"), line, lines)
            for h in lines:
                if h.lower().startswith(":method:") and not rec.get("method"):
                    rec["method"] = h.split(":", 2)[2].strip()
        elif ename == "HTTP_TRANSACTION_READ_RESPONSE_HEADERS":
            for h in _header_lines(params):
                m = STATUS_LINE.match(h)
                if m:
                    rec["status"] = int(m.group(1))
                    break
                if h.lower().startswith(":status:"):
                    rec["status"] = int(h.split(":", 2)[2].strip()[:3])
                    break

    # The top-level document that caused a request: follow source dependencies to a URL_REQUEST
    # whose URL looks like a page (or, failing that, any URL_REQUEST).
    out: list[NetRequest] = []
    for sid, rec in sorted(found.items(), key=lambda kv: (kv[1].get("start") or _EPOCH, kv[0])):
        seen = {sid}
        cur = depends.get(sid)
        initiator_url = None
        while cur is not None and cur not in seen:
            seen.add(cur)
            if cur in found and found[cur].get("url"):
                initiator_url = found[cur]["url"]
                break
            cur = depends.get(cur)
        out.append(NetRequest(**{**rec, "initiator_url": initiator_url}))
    return out


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse(path: Path) -> list[NetRequest]:
    raw = Path(path).read_bytes()
    return parse_text(raw.decode("utf-8-sig", "replace"))


def write_jsonl(requests: list[NetRequest], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in requests:
            fh.write(r.model_dump_json(exclude_none=False) + "\n")
    return path
