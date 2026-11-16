"""Bot trace / process analysis (Prod Echo I-09): join the XAML analysis, a run trace and,
optionally, a recording (or NetLog requests) into one ordered BotTrace.

Each step names the app, screen, action, field (the automation's variable), selector and value
(or `redacted`), with its duration and the gap until the next step: what the bot waited for. The
wait statistics across runs calibrate slowness (`calibrate`).

Joining rules, in order: a step's logged selector equals an analysed use's selector; otherwise
the next analysed use with the same action (noted as a gap: "matched by order"); otherwise the
step stays unmatched and is listed as a gap. Navigation steps match a screen by URL path.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from labctl.analyse import uipath
from labctl.behaviour import dom as domlib
from labctl.botlog.models import BotTrace, NetRequest, RunTrace, TraceGap, TraceStep, WaitStat
from labctl.botlog.recording import Recording, RecStep, is_asset, request_key
from labctl.models import DbLink, HttpRule

NAVIGATION = {"OpenBrowser", "NavigateTo", "GoToUrl"}
QUERIES = {"ExecuteQuery", "ExecuteNonQuery"}
REQUESTS = {"HttpClient"}
IN_KEYS = {"type": ("Text", "SecureText"), "select": ("Item",)}
OUT_KEYS = {"read": ("Value", "Text", "Result"), "wait": ("Exists",)}


def _touches(analysis: Any) -> list[dict]:
    if analysis is None:
        return []
    if hasattr(analysis, "touches"):
        return list(analysis.touches)
    if isinstance(analysis, dict):
        return list(analysis.get("touches") or [])
    return list(analysis)


def flat_uses(analysis: Any) -> list[dict]:
    """Every web use in automation order, with its app, screen name and path."""
    out = []
    for touch in _touches(analysis):
        if touch.get("via") != "web":
            continue
        for screen in touch.get("screens") or []:
            for use in screen.get("uses") or []:
                out.append(
                    {
                        **use,
                        "app": touch["app"],
                        "screen": screen["name"],
                        "path": screen.get("path"),
                    }
                )
    return out


def screens_of(analysis: Any) -> list[dict]:
    out = []
    for touch in _touches(analysis):
        if touch.get("via") == "web":
            out += [{**s, "app": touch["app"]} for s in touch.get("screens") or []]
    return out


def path_regex(template: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]*\})", template.rstrip("/") or "/")
    body = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{body}/?$")


def screen_for(path: str | None, screens: list[dict]) -> dict | None:
    if not path:
        return None
    for s in screens:
        if s.get("path") and path_regex(s["path"]).match(path):
            return s
    return None


def _pick(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None


def _ms(a: datetime | None, b: datetime | None) -> int | None:
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def _window_requests(start, end, recording: Recording | None, netlog: list[NetRequest] | None):
    keys: list[str] = []
    if recording is not None:
        for e in recording.requests_between(start, end):
            if not e.is_asset() and e.key() not in keys:
                keys.append(e.key())
    for r in netlog or []:
        if r.start is None or start is None or r.start < start or (end and r.start >= end):
            continue
        path = urlsplit(r.url or "").path
        if r.url and not is_asset(path):
            key = request_key(r.method or "GET", r.url)
            if key not in keys:
                keys.append(key)
    return keys


def _finish_timing(steps: list[TraceStep], ends: list[datetime | None]) -> None:
    for i, step in enumerate(steps):
        nxt = steps[i + 1].ts if i + 1 < len(steps) else None
        end = ends[i] or step.ts
        step.gap_ms = _ms(end, nxt) if nxt else None
        if step.duration_ms is not None or step.gap_ms is not None:
            step.wait_ms = (step.duration_ms or 0) + (step.gap_ms or 0)


def build(
    analysis: Any,
    run: RunTrace,
    recording: Recording | None = None,
    netlog: list[NetRequest] | None = None,
    automation: str | None = None,
    source: str = "unknown",
) -> BotTrace:
    """The bot trace of one run. `analysis` is `uipath.analyse(...)`, or an automation's touches."""
    uses = flat_uses(analysis)
    screens = screens_of(analysis)
    gaps: list[TraceGap] = []
    steps: list[TraceStep] = []
    ends: list[datetime | None] = []
    used: set[int] = set()
    cursor = 0
    current: dict | None = None
    rec_cursor = 0

    def gap(where: str, what: str) -> None:
        g = TraceGap(where=where, what=what)
        if g not in gaps:
            gaps.append(g)

    for s in run.steps:
        where = f"step {s.seq} {s.activity} '{s.display or s.activity}'"
        kind = s.activity
        if kind in NAVIGATION:
            url = s.values.in_.get("Url") if isinstance(s.values.in_.get("Url"), str) else None
            path = urlsplit(url).path if url else None
            current = screen_for(path, screens) or current
            if current is not None:
                first = next(
                    (i for i, u in enumerate(uses) if u["screen"] == current["name"]), None
                )
                if first is not None and first > cursor:
                    cursor = first
            elif not s.redacted:
                gap(
                    where,
                    f"navigation to {path or 'an unknown address'} matches no analysed screen",
                )
            step = TraceStep(
                seq=len(steps) + 1,
                ts=s.ts,
                app=current["app"] if current else None,
                screen=current["name"] if current else path,
                path=current.get("path") if current else path,
                action="goto",
                display=s.display,
                duration_ms=s.duration_ms,
                redacted=s.redacted,
                matched_by="url" if current else "none",
            )
        elif kind in uipath.UI:
            action = uipath.UI[kind][0]
            css = (s.selector or {}).get("css")
            raw = (s.selector or {}).get("raw")
            matched_by = "none"
            candidates = [i for i in range(len(uses)) if i not in used]

            def same(i: int, action=action, css=css, raw=raw) -> bool:
                u = uses[i]
                return u["action"] == action and bool(
                    css and u["selector"] == css or raw and u.get("raw") == raw
                )

            match = next((i for i in candidates if i >= cursor and same(i)), None)
            if match is None:
                match = next((i for i in candidates if same(i)), None)
            if match is not None:
                matched_by = "selector"
            else:
                match = next(
                    (i for i in candidates if i >= cursor and uses[i]["action"] == action), None
                )
                if match is not None:
                    matched_by = "order"
                    gap(where, "no selector logged; matched to the next analysed use by order")
                else:
                    gap(where, "matches no use in the automation analysis")
            use = uses[match] if match is not None else None
            if match is not None:
                used.add(match)
                cursor = match + 1
            value = None
            if not s.redacted:
                if action in IN_KEYS:
                    value = _pick(s.values.in_, IN_KEYS[action])
                elif action in OUT_KEYS:
                    value = _pick(s.values.out, OUT_KEYS[action])
            step = TraceStep(
                seq=len(steps) + 1,
                ts=s.ts,
                app=use["app"] if use else current["app"] if current else None,
                screen=use["screen"] if use else current["name"] if current else None,
                path=use.get("path") if use else current.get("path") if current else None,
                action=action,
                field=use.get("field") if use else None,
                selector=use["selector"] if use else css or (f"uipath:{raw}" if raw else None),
                value=value,
                redacted=s.redacted,
                display=s.display,
                duration_ms=s.duration_ms,
                matched_by=matched_by,
            )
        elif kind in QUERIES or kind in REQUESTS:
            step = TraceStep(
                seq=len(steps) + 1,
                ts=s.ts,
                action="query" if kind in QUERIES else "request",
                display=s.display,
                duration_ms=s.duration_ms,
                redacted=s.redacted,
            )
        else:
            if kind not in uipath.NEUTRAL and kind != "Unknown":
                gap(where, f"activity {kind} is not interpreted by the trace")
            continue
        if s.state == "Faulted":
            gap(where, f"faulted: {s.message or 'no message'}")
        steps.append(step)
        ends.append(s.end_ts)

    _finish_timing(steps, ends)
    for i, step in enumerate(steps):
        nxt = steps[i + 1].ts if i + 1 < len(steps) else run.ended
        step.requests = _window_requests(step.ts, nxt, recording, netlog)
        if recording is not None:
            rec_cursor, rstep = _match_recording(recording, step, rec_cursor)
            step.dom = rstep.dom if rstep else None
    for i, use in enumerate(uses):
        if i not in used:
            gap(f"{use['screen']} {use['action']} {use['selector']}", "analysed use never ran")
    return BotTrace(
        run_id=run.run_id,
        process=run.process,
        automation=automation,
        source=source,  # type: ignore[arg-type]
        started=run.started,
        ended=run.ended,
        recording=recording.id if recording else None,
        steps=steps,
        waits=wait_stats([steps]),
        gaps=gaps,
    )


def _match_recording(recording: Recording, step: TraceStep, cursor: int):
    if step.action not in ("goto", "type", "click", "select"):
        return cursor, None
    for i in range(cursor, len(recording.steps)):
        r = recording.steps[i]
        if r.action != step.action:
            continue
        if step.action == "goto" or (r.selector and r.selector == step.selector):
            return i + 1, r
    return cursor, None


def _value_from_dom(recording: Recording, r: RecStep) -> Any:
    html = recording.dom(r)
    key = domlib.key_of(r.selector)
    if html is None or key is None:
        return None
    el = domlib.snapshot(html).elements.get(key)
    if el is None:
        return None
    return el.selected if el.tag == "select" else el.value


def from_recording(
    recording: Recording, analysis: Any = None, source: str = "agent", run_id: str | None = None
) -> BotTrace:
    """A trace of a recorded run (an agent's, or a person's), for parity with a bot trace."""
    uses = flat_uses(analysis)
    screens = screens_of(analysis)
    steps: list[TraceStep] = []
    gaps: list[TraceGap] = []
    prev_url: str | None = None
    for r in recording.steps:
        page = r.url if r.action == "goto" else prev_url or r.url
        path = urlsplit(page).path if page else None
        scr = screen_for(path, screens)
        use = None
        if r.selector:
            same = [u for u in uses if u["selector"] == r.selector]
            use = next(
                (u for u in same if scr and u["screen"] == scr["name"]), same[0] if same else None
            )
        if r.action in ("type", "select"):
            value = r.value if r.value is not None else _value_from_dom(recording, r)
        else:
            value = None
        action = r.action if r.action in ("goto", "type", "click", "select", "submit") else "click"
        if r.action not in ("goto", "type", "click", "select", "submit"):
            gaps.append(
                TraceGap(where=f"recording step {r.seq}", what=f"unknown action {r.action}")
            )
        steps.append(
            TraceStep(
                seq=len(steps) + 1,
                ts=r.ts,
                app=(use or scr or {}).get("app") or recording.app,
                screen=(use["screen"] if use else scr["name"] if scr else path),
                path=(use.get("path") if use else scr.get("path") if scr else path),
                action=action,  # type: ignore[arg-type]
                field=use.get("field") if use else None,
                selector=r.selector,
                value=value,
                redacted=r.action == "type" and value is None,
                dom=r.dom,
                matched_by="selector" if use else "url" if scr else "none",
            )
        )
        prev_url = r.url
    _finish_timing(steps, [None] * len(steps))
    for i, step in enumerate(steps):
        nxt = steps[i + 1].ts if i + 1 < len(steps) else None
        step.requests = _window_requests(step.ts, nxt, recording, None)
    return BotTrace(
        run_id=run_id or recording.id,
        source=source,  # type: ignore[arg-type]
        started=steps[0].ts if steps else None,
        ended=steps[-1].ts if steps else None,
        recording=recording.id,
        steps=steps,
        waits=wait_stats([steps]),
        gaps=gaps,
    )


# --- wait statistics and calibration -----------------------------------------------------------


def _quantile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(q * len(ordered) + 0.5)) - 1))
    return ordered[idx]


def wait_stats(traces: list[BotTrace] | list[list[TraceStep]]) -> list[WaitStat]:
    """Per step (app, screen, action, field or selector): p50, p95 and max of the bot's wait."""
    groups: dict[tuple, list[int]] = {}
    reqs: dict[tuple, list[str]] = {}
    for t in traces:
        steps = t.steps if isinstance(t, BotTrace) else t
        for s in steps:
            if s.wait_ms is None:
                continue
            key = (s.app, s.screen, s.action, s.field or s.selector or s.path or s.display or "")
            groups.setdefault(key, []).append(s.wait_ms)
            for r in s.requests:
                if r not in reqs.setdefault(key, []):
                    reqs[key].append(r)
    return [
        WaitStat(
            app=k[0],
            screen=k[1],
            action=k[2],
            target=k[3],
            n=len(v),
            p50_ms=_quantile(v, 0.5),
            p95_ms=_quantile(v, 0.95),
            max_ms=max(v),
            requests=reqs.get(k, []),
        )
        for k, v in groups.items()
    ]


def calibrate(traces: list[BotTrace]) -> dict:
    """A condition-profile fragment from robot-log timings: http rules per screen step, db link.

    Each http rule takes the request(s) seen in a step's window (from a recording or NetLog) and
    the bot's p50/p95 wait at that step. Steps whose requests are unknown are listed under
    `steps` only. `db` comes from query activity durations.
    """
    stats = wait_stats(traces)
    rules: dict[str, dict] = {}
    for st in stats:
        if st.action in ("query", "read"):
            continue
        for match in st.requests:
            have = rules.get(match)
            if have is None or st.p95_ms > have["latency_ms"]["p95"]:
                rules[match] = {"match": match, "latency_ms": {"p50": st.p50_ms, "p95": st.p95_ms}}
    http = sorted(rules.values(), key=lambda r: (-len(r["match"].replace("*", "")), r["match"]))
    http = [HttpRule.model_validate(r).model_dump() for r in http]
    durations = [
        s.duration_ms
        for t in traces
        for s in t.steps
        if s.action == "query" and s.duration_ms is not None
    ]
    db = None
    if durations:
        p50, p95 = _quantile(durations, 0.5), _quantile(durations, 0.95)
        db = DbLink(latency_ms=p50, jitter_ms=p95 - p50).model_dump()
    apps = Counter(s.app for t in traces for s in t.steps if s.app)
    return {
        "calibration": "robot_logs",
        "calibrated_from": f"{len(traces)} run(s): " + ", ".join(t.run_id for t in traces),
        "app": apps.most_common(1)[0][0] if apps else None,
        "http": http,
        "db": db,
        "steps": [s.model_dump() for s in stats],
    }
