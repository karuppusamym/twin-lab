"""Behaviour catalog (Prod Echo I-09a): declarative client-side rules inferred from recordings.

For each recorded step, the snapshot before it (the previous step's after-snapshot) is compared
with the snapshot after it. Differences become rules of fixed kinds: show, hide, enable, disable,
set_options (a cascade), format, set_value, navigate and delay (HAR timing between the action and
the requests that fed the change). The bot's own navigations (`goto`) are not behaviour and are
skipped.

Each rule names its trigger (element, event, the values seen when inferable, and the values for
which it did not happen) and its evidence: the snapshot pairs. A rule seen once is marked
`single_observation`. A rule without an evidence pair is never emitted.

Privacy: option texts and select choices are code lists and are kept; typed values and page text
are kept as their shape only (`Smith` -> `A`, `C-1001` -> `A-9`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from labctl.behaviour import dom as domlib
from labctl.botlog.recording import Recording, RecStep, path_pattern
from labctl.botlog.recording import load as load_recording
from labctl.models import Contract

VERSION = "behaviour-catalog@0.1.0"
Kind = Literal[
    "show", "hide", "enable", "disable", "set_options", "format", "set_value", "navigate", "delay"
]
EVENTS = {"type": "input", "select": "change", "click": "click", "submit": "submit"}
FORM = {"input", "select", "textarea"}
DELAY_WINDOW_S = 10.0


class Trigger(Contract):
    element: str
    event: str
    values: list[str] = []  # trigger values seen when the rule happened (code lists or shapes)
    not_values: list[str] = []  # trigger values seen when it did not happen


class Evidence(Contract):
    recording: str
    step: int
    before: str
    after: str


class Rule(Contract):
    id: str
    kind: Kind
    screen: str | None = None  # path pattern of the page the trigger is on
    trigger: Trigger
    target: str  # element key, or the path pattern navigated to
    detail: dict[str, Any] = {}
    observations: int = Field(ge=1)
    single_observation: bool
    evidence: list[Evidence] = Field(min_length=1)


class Catalog(Contract):
    app: str | None = None
    generated_by: str = VERSION
    recordings: list[str] = []
    rules: list[Rule] = []


# --- one step ----------------------------------------------------------------------------------


def _code(el: domlib.ElementState | None, raw: str | None) -> str | None:
    """A trigger value fit to keep: a select's option text, else the value's shape."""
    if el is not None and el.tag == "select":
        return el.selected
    value = raw if raw is not None else el.value if el is not None else None
    return domlib.shape(value) if value is not None else None


def _kept(el: domlib.ElementState) -> str | None:
    if el.tag == "select":
        return el.selected
    if el.tag in FORM:
        return domlib.shape(el.value)
    return domlib.shape(el.text)


def _requests(rec: Recording, step: RecStep, nxt: RecStep | None):
    if step.ts is None:
        return []
    end = nxt.ts if nxt and nxt.ts else step.ts + timedelta(seconds=DELAY_WINDOW_S)
    return [e for e in rec.requests_between(step.ts, end) if not e.is_asset()]


def observe(rec: Recording) -> list[dict]:
    """Raw observations from one recording: one per rule instance, each with its evidence pair."""
    out: list[dict] = []
    steps = rec.steps
    for i, step in enumerate(steps):
        if i == 0 or step.action == "goto" or step.action not in EVENTS:
            continue
        before = steps[i - 1]
        html_b, html_a = rec.dom(before), rec.dom(step)
        before_file = before.dom
        if html_b is None:
            # A click and the submit it caused share one snapshot: the page the first of them
            # met (the recorder keeps it) is the page before the step.
            first = i
            while first > 0 and steps[first - 1].dom is None and steps[first - 1].ts == step.ts:
                first -= 1
            for candidate in (steps[first], step):
                if candidate.before_scope == "frame":
                    continue  # only the frame's document: not comparable with the whole page
                html_b = rec.dom(candidate, before=True)
                if html_b is not None:
                    before_file = candidate.dom_before
                    break
        if html_b is None or html_a is None:
            continue
        evidence = {"recording": rec.id, "step": step.seq, "before": before_file, "after": step.dom}
        trig = domlib.key_of(step.selector) or step.selector or "?"
        event = EVENTS[step.action]
        sb, sa = domlib.snapshot(html_b), domlib.snapshot(html_a)
        trig_el = sa.elements.get(trig) or sb.elements.get(trig)
        value = _code(trig_el, step.value) if step.action in ("type", "select") else None
        base = {
            "screen": path_pattern(before.path) if before.path else None,
            "trigger": trig,
            "event": event,
            "value": value,
            "evidence": evidence,
        }
        out.append({**base, "kind": None})  # every trigger occurrence, for not_values
        if before.path and step.path and before.path != step.path:
            out.append({**base, "kind": "navigate", "target": path_pattern(step.path)})
            continue
        changed: list[str] = []
        for key in dict.fromkeys([*sb.elements, *sa.elements]):
            eb, ea = sb.elements.get(key), sa.elements.get(key)
            if key == trig:
                typed_other = (
                    step.value is not None
                    and ea is not None
                    and ea.value is not None
                    and ea.value != step.value
                )
                shaped_other = (  # a recorder keeps only the shape of what was typed
                    step.value is None
                    and step.typed_shape is not None
                    and ea is not None
                    and ea.value is not None
                    and domlib.shape(ea.value) != step.typed_shape
                )
                if step.action == "type" and (typed_other or shaped_other):
                    out.append(
                        {
                            **base,
                            "kind": "format",
                            "target": key,
                            "detail": {"shape": domlib.shape(ea.value)},
                        }
                    )
                continue
            vb = bool(eb and eb.visible)
            va = bool(ea and ea.visible)
            if not vb and va:
                out.append({**base, "kind": "show", "target": key})
                changed.append(key)
                continue
            if vb and not va:
                out.append({**base, "kind": "hide", "target": key})
                changed.append(key)
                continue
            if not (vb and va):
                continue
            if (eb.interactive or ea.interactive) and eb.enabled != ea.enabled:
                kind = "enable" if ea.enabled else "disable"
                out.append({**base, "kind": kind, "target": key})
                changed.append(key)
            if eb.options is not None and ea.options is not None and eb.options != ea.options:
                out.append(
                    {
                        **base,
                        "kind": "set_options",
                        "target": key,
                        "detail": {"options": ea.options},
                    }
                )
                changed.append(key)
            if ea.container:
                continue  # a container's text changes with everything in it
            if _kept(eb) != _kept(ea) or (eb.tag in FORM and eb.value != ea.value):
                out.append(
                    {**base, "kind": "set_value", "target": key, "detail": {"value": _kept(ea)}}
                )
                changed.append(key)
        reqs = _requests(rec, step, steps[i + 1] if i + 1 < len(steps) else None)
        if changed and reqs and step.ts is not None:
            ends = [e.end for e in reqs if e.end is not None]
            if ends:
                ms = int((max(ends) - step.ts).total_seconds() * 1000)
                mine = [o for o in out if o.get("evidence") is evidence and o.get("kind")]
                rank = {"set_options": 0, "show": 1, "enable": 2, "set_value": 3}
                mine.sort(key=lambda o: rank.get(o["kind"], 9))
                target = mine[0]["target"] if mine else changed[0]
                out.append(
                    {
                        **base,
                        "kind": "delay",
                        "target": target,
                        "detail": {"ms": max(ms, 0), "requests": sorted({e.key() for e in reqs})},
                    }
                )
    return out


# --- aggregation -------------------------------------------------------------------------------


def build(recordings: Iterable[Recording | Path], app: str | None = None) -> Catalog:
    recs = [r if isinstance(r, Recording) else load_recording(Path(r)) for r in recordings]
    obs = [o for r in recs for o in observe(r)]
    occurrences: dict[tuple, list[dict]] = {}
    groups: dict[tuple, list[dict]] = {}
    for o in obs:
        if o["kind"] is None:
            occurrences.setdefault((o["screen"], o["trigger"], o["event"]), []).append(o)
        else:
            key = (o["kind"], o["screen"], o["trigger"], o["event"], o["target"])
            groups.setdefault(key, []).append(o)
    rules: list[Rule] = []
    order = {k: i for i, k in enumerate(Kind.__args__)}  # type: ignore[attr-defined]
    for key in sorted(groups, key=lambda k: (k[1] or "", k[2], order[k[0]], k[4])):
        kind, screen, trig, event, target = key
        items = groups[key]
        seen = [o["evidence"] for o in items]
        evidence = [
            Evidence(**e) for e in {json.dumps(e, sort_keys=True): e for e in seen}.values()
        ]
        if not evidence:
            continue
        values = sorted({o["value"] for o in items if o["value"] is not None})
        hit = {json.dumps(o["evidence"], sort_keys=True) for o in items}
        others = occurrences.get((screen, trig, event), [])
        not_values = sorted(
            set()
            if kind not in ("show", "hide")
            else {
                o["value"]
                for o in others
                if o["value"] is not None
                and json.dumps(o["evidence"], sort_keys=True) not in hit
                and o["value"] not in values
            }
        )
        detail: dict[str, Any] = {}
        if kind == "set_options":
            examples = {}
            for o in items:
                examples.setdefault(o["value"] or "", o["detail"]["options"])
            detail = {"examples": [{"when": w, "options": opts} for w, opts in examples.items()]}
        elif kind == "set_value":
            detail = {"values": sorted({str(o["detail"]["value"]) for o in items})}
        elif kind == "format":
            detail = {"shapes": sorted({o["detail"]["shape"] for o in items})}
        elif kind == "delay":
            ms = sorted(o["detail"]["ms"] for o in items)
            detail = {
                "ms": ms,
                "p50_ms": ms[len(ms) // 2],
                "max_ms": ms[-1],
                "requests": sorted({r for o in items for r in o["detail"]["requests"]}),
            }
        rules.append(
            Rule(
                id=f"B{len(rules) + 1:02d}",
                kind=kind,
                screen=screen,
                trigger=Trigger(element=trig, event=event, values=values, not_values=not_values),
                target=target,
                detail=detail,
                observations=len(evidence),
                single_observation=len(evidence) == 1,
                evidence=evidence,
            )
        )
    return Catalog(
        app=app or next((r.app for r in recs if r.app), None),
        recordings=[r.id for r in recs],
        rules=rules,
    )


def write(catalog: Catalog, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(catalog.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


# --- the brief ---------------------------------------------------------------------------------


def _when(rule: Rule) -> str:
    t = rule.trigger
    if t.event == "click":
        return f"When {t.element} is clicked"
    if t.event == "submit":
        return f"When {t.element} is submitted"
    verb = "changes" if t.event == "change" else "is typed into"
    if t.event == "input":
        return f"When {t.element} is typed into"
    if t.values and rule.kind in ("show", "hide", "enable", "disable", "set_value"):
        return f"When {t.element} {verb} to {' or '.join(t.values)}"
    return f"When {t.element} {verb}"


def describe(rule: Rule) -> str:
    when = _when(rule)
    k, target = rule.kind, rule.target
    if k == "set_options":
        text = f"{when}, {target} options are replaced"
    elif k == "show":
        text = f"{when}, {target} appears"
    elif k == "hide":
        text = f"{when}, {target} disappears"
    elif k in ("enable", "disable"):
        text = f"{when}, {target} is {k}d"
    elif k == "set_value":
        text = f"{when}, {target} is set to {' or '.join(repr(v) for v in rule.detail.get('values', []))}"
    elif k == "format":
        text = f"{when}, the value is formatted as {' or '.join(rule.detail.get('shapes', []))}"
    elif k == "navigate":
        text = f"{when}, the app opens {target}"
    else:  # delay
        reqs = ", ".join(rule.detail.get("requests", []))
        text = f"{when}, {target} updates after about {rule.detail.get('p50_ms')} ms ({reqs})"
    if rule.trigger.not_values:
        text += f"; not for {', '.join(rule.trigger.not_values)}"
    if rule.single_observation:
        return text + " (seen once; single observation, needs review)"
    return text + f" (seen {rule.observations} times)"


def brief_lines(catalog: Catalog) -> list[str]:
    """Markdown bullet lines for the brief, one per rule."""
    return [f"- {describe(r)}" for r in catalog.rules]
