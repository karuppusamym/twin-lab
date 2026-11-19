"""Drafts to judge a rebuild with, written beside a recording by `labctl evidence build-all`.

Every draft is unapproved, as the templates of `labctl behaviour acceptance/visual/scope` are: a
person reviews it and approves it (or deletes what should not count). Nothing here approves.

- `expected-actions.draft.yaml`: the recording's semantic actions in order (what `--expect`
  takes), each a person checks against the flow before passing it.
- `visual.draft.yaml` (the replay reads `visual.yaml` by itself, so the draft has another
  name): one state per recorder picture, in threshold mode with a small ratio, and the
  text regions left out when the package carries no font (a fallback font never matches the
  original's pixels exactly). The person may still choose `mode: exact`.
- `scope.yaml`: exclusions for what the package tells the builder not to replicate (the player of
  a media origin the builder may leave out), unapproved.
- acceptance cases the recording visibly established (`acceptance_cases`, used by
  `labctl.seedplan.acceptance`): the count of a list's rows where a step acted on one of them,
  that count after the step when the step changed it (a cart row deleted), and the record's title
  on the page a click on it opened (`{record: <step>}`: a seed value, never a recorded one).
  They are `required: false` until a person sets `required: true`: an unapproved case never
  decides a replay's result.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from labctl.behaviour import dom as domlib
from labctl.locate import row_selectors

EXPECTED = "expected-actions.draft.yaml"
# Not `visual.yaml`: the replay reads that one by itself; a draft is used only once a person
# reviewed it and passed it (`--visual visual.draft.yaml`) or renamed it.
VISUAL = "visual.draft.yaml"
VISUAL_RATIO = 0.02  # differing pixels allowed in threshold mode (2 %)
VISUAL_TOLERANCE = 16  # a channel difference up to this is no difference (anti-aliasing)
TEXT_REGIONS = {"css": "h1, h2, h3, h4, h5, h6, p, a, span, label, button, li, td, th, small, "
                       "strong, input, textarea, select"}  # fmt: skip


# --- a small CSS matcher: `#id > tag.class`, `tag.class tag`, `:nth-child(n)` -----------------

_PART = re.compile(r"^([a-zA-Z][\w-]*|\*)?((?:[#.][\w-]+)*)((?::nth-child\(\d+\))?)$")


def _compound(text: str):
    m = _PART.match(text.strip())
    if not m:
        return None
    tag = (m.group(1) or "*").lower()
    ids = re.findall(r"#([\w-]+)", m.group(2))
    classes = re.findall(r"\.([\w-]+)", m.group(2))
    nth = re.search(r"\d+", m.group(3) or "")
    return tag, ids, classes, int(nth.group()) if nth else None


def _parse(selector: str):
    tokens = re.split(r"\s*(>)\s*|\s+", selector.strip())
    parts, comb = [], " "
    for t in [x for x in tokens if x]:
        if t == ">":
            comb = ">"
            continue
        c = _compound(t)
        if c is None:
            return None
        parts.append((comb, c))
        comb = " "
    return parts


def _fits(node, c) -> bool:
    tag, ids, classes, nth = c
    if tag != "*" and node.tag != tag:
        return False
    if any(node.id != i for i in ids):
        return False
    have = set(node.classes)
    if any(k not in have for k in classes):
        return False
    if nth is not None:
        sibs = [n for n in (node.parent.children if node.parent else []) if n.tag]
        if node not in sibs or sibs.index(node) + 1 != nth:
            return False
    return True


def _matches(node, parts) -> bool:
    if not parts:
        return True
    comb, c = parts[-1]
    if not _fits(node, c):
        return False
    rest = parts[:-1]
    if not rest:
        return True
    up = node.parent
    if comb == ">":
        return up is not None and up.tag != "" and _matches(up, rest)
    while up is not None and up.tag:
        if _matches(up, rest):
            return True
        up = up.parent
    return False


def select(html: str, selector: str) -> list:
    """The elements of a page a simple selector matches (None-safe: [] when it cannot read it)."""
    parsed = _parse(selector or "")
    if not parsed:
        return []
    root = domlib.parse_html(html or "")
    return [n for n in domlib._walk(root) if _matches(n, parsed)]


# --- acceptance cases the recording established ----------------------------------------------


def _locator_of(node, html: str) -> str | None:
    """A selector that names this node alone in the page: `tag#id`, else `tag.class...`."""
    if node.id:
        sel = f"{node.tag}#{node.id}"
    elif node.classes:
        sel = node.tag + "".join(f".{c}" for c in node.classes[:3])
    else:
        return None
    return sel if len(select(html, sel)) == 1 else None


USER_ACTIONS = ("click", "dblclick", "type", "select", "check", "uncheck", "keypress", "submit",
                "upload")  # fmt: skip


def _settled(raw: list[dict], st: dict) -> list[dict]:
    """The snapshots that show what a step did: its own, those of steps it caused, and a page
    that loaded before the person acted again (a list reloaded after a delete)."""
    out = []
    for s in raw:
        if s["seq"] < st["seq"]:
            continue
        if s["seq"] > st["seq"] and s.get("action") in USER_ACTIONS and s.get("caused_by") is None:
            break
        if s.get("dom"):
            out.append(s)
    return out


def acceptance_cases(part, records: list[dict]) -> list[dict]:
    """Cases from what the recording visibly showed (see the module's doc); `part` is a
    `labctl.seedplan.Part`, `records` its record bindings (`seedplan.record_bindings`)."""
    out: list[dict] = []
    seen: set[str] = set()
    by_seq = {s["seq"]: s for s in part.raw}

    def add(case: dict) -> None:
        if case["id"] not in seen:
            seen.add(case["id"])
            out.append(case)

    for st in part.raw:
        locs = [c for c in st.get("locators") or [] if c.get("kind") == "record"]
        if not locs or not locs[0].get("rows"):
            continue
        before = part.befores.get(st["seq"])
        rows = next((r for r in row_selectors(locs[0]["rows"]) if before and select(before, r)),
                    locs[0]["rows"])  # fmt: skip
        earlier = [s["seq"] for s in part.raw if s["seq"] < st["seq"] and s.get("dom")]
        n = len(select(before, rows)) if before else 0
        if n and earlier:
            add({"id": f"rows-before-{st['seq']}", "after": earlier[-1], "check": "count",
                 "locator": rows, "equals": n, "basis": "evidence", "required": False,
                 "note": f"the list showed {n} row(s) when step {st['seq']} acted on one"})  # fmt: skip
        later = _settled(part.raw, st)
        if later:
            after_html = part.doms.get(later[-1]["seq"]) or ""
            m = len(select(after_html, rows))
            if m and m != n:
                add({"id": f"rows-after-{st['seq']}", "after": later[-1]["seq"], "check": "count",
                     "locator": rows, "equals": m, "basis": "evidence", "required": False,
                     "note": f"step {st['seq']} changed the list from {n} to {m} row(s)"})  # fmt: skip
    for r in records:
        title = r.get("record")
        if not title:
            continue
        caused = [s for s in part.raw if s.get("caused_by") == r["n"] and s.get("dom")]
        target = caused[-1] if caused else by_seq.get(r["n"])
        html = part.doms.get(target["seq"]) if target else None
        if not html:
            continue
        want = " ".join(title.split()).lower()
        nodes = [x for x in domlib._walk(domlib.parse_html(html))
                 if " ".join(" ".join(x.text).split()).lower() == want]  # fmt: skip
        inner = [x for x in nodes if not any(c in nodes for c in domlib._walk(x))]
        loc = next((sel for x in inner if (sel := _locator_of(x, html))), None)
        if loc and target["seq"] != r["n"]:
            add({"id": f"record-shown-{r['n']}", "after": target["seq"], "check": "text",
                 "locator": loc, "equals": {"record": r["n"]}, "basis": "evidence", "required": False,
                 "note": f"the page step {r['n']} opened showed the record it acted on"})  # fmt: skip
    return out


def dialog_lines(part, from_code: dict[int, dict] | None = None) -> list[str]:
    """What the recording's dialogs said, for the draft's scope (the replay compares them); a
    text the recording lost is the code's when `from_code` (`seedplan.dialog_texts`) has it."""
    out = []
    for s in part.raw:
        d = s.get("dialog") or {}
        if s.get("action") != "dialog":
            continue
        code = (from_code or {}).get(s["seq"])
        if d.get("message"):
            said = f": {d['message']!r}"
        elif code:
            said = f": {code['message']!r}, as the application's code says (not recorded)"
        else:
            said = " (its text was not recorded)"
        out.append(f"step {s['seq']}: a {d.get('kind', 'dialog')}{said}, compared by the replay")
    return out


# --- the whole recording's drafts ----------------------------------------------------------------


def expected_draft(rec) -> str:
    from labctl.behaviour.fidelity import action_label

    acts = [action_label(s) for s in rec.steps
            if not s.setup and s.caused_by is None and s.action != "goto"]  # fmt: skip
    first = next((s for s in rec.steps if s.action == "goto" and not s.setup), None)
    if first is not None:
        acts.insert(0, action_label(first))
    head = ("# DRAFT, unapproved: the semantic actions this recording captured, in order, for\n"
            "# `labctl behaviour fidelity --expect`. Check each against the flow the recording was\n"
            "# meant to show (delete what the flow does not need, add what it lacks), then use it.\n")  # fmt: skip
    return head + yaml.safe_dump(acts, sort_keys=False, allow_unicode=True)


def visual_draft(rec, fonts_carried: bool) -> str:
    from labctl.behaviour import visual

    text = visual.template(rec)
    data = yaml.safe_load(text) or {}
    for st in data.get("states") or []:
        st["mode"], st["max_ratio"], st["tolerance"] = "threshold", VISUAL_RATIO, VISUAL_TOLERANCE
        if not fonts_carried:
            st["exclude"] = [dict(TEXT_REGIONS)]
    head = ("# DRAFT, unapproved: one state per recorder picture. threshold mode: up to "
            f"{VISUAL_RATIO:.0%} of the\n# compared pixels may differ (tolerance {VISUAL_TOLERANCE}"
            " per channel), so anti-aliasing and\n# sub-pixel layout do not fail a state.")  # fmt: skip
    head += ("\n# The package carries no font (by design): the text regions are left out\n"
             "# (exclude), since a fallback font never draws the original's pixels.\n"
             if not fonts_carried else "\n")  # fmt: skip
    head += "# For a strict comparison set mode: exact, max_ratio: 0.0, tolerance: 0.\n"
    return head + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def scope_draft(rec_id: str, network: dict | None, pages: list[str]) -> str | None:
    """Exclusions for what the package says need not be replicated: the player of a media origin
    (the builder may leave the video out). None when there is nothing to exclude."""
    from labctl.behaviour import scope

    media = (network or {}).get("media_origins") or []
    if not media:
        return None
    player = "div.video-js" if any("video-js" in p for p in pages) else "video"
    text = scope.draft(rec_id)
    data = yaml.safe_load(text) or {}
    data["exclusions"] = [{
        "id": f"media-{m['origin']}", "element": player,
        "reason": f"the package lets the builder serve a stand-in for {m['origin']} ("
                  + ", ".join(m.get("serves") or []) + ") or leave the video out: its player is "
                  "not compared"} for m in media]  # fmt: skip
    head = text.split("version:", 1)[0]
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return head + body.replace("approved_by: null", "approved_by: null  # TODO: who approved")


def write_all(root: Path, recording: Path, apps: list[str]) -> list[Path]:
    """The drafts beside the whole recording; a file that exists is never replaced."""
    import json

    from labctl.botlog.recording import load as load_rec

    root, recording = Path(root), Path(recording)
    try:
        rec = load_rec(recording)
    except Exception:  # noqa: BLE001 - a recording that cannot be read gets no draft
        return []
    written = []
    fonts = any((root / "apps" / a / "look" / "fonts").is_dir()
                and any((root / "apps" / a / "look" / "fonts").iterdir()) for a in apps)  # fmt: skip
    texts = {EXPECTED: expected_draft(rec), VISUAL: visual_draft(rec, fonts)}
    for a in apps:
        net_file = root / "apps" / a / "api" / "network.json"
        try:
            net = json.loads(net_file.read_text(encoding="utf-8")) if net_file.is_file() else None
        except ValueError:
            net = None
        pages = [p.read_text(encoding="utf-8", errors="replace")
                 for p in sorted((root / "apps" / a / "pages").glob("*.html"))]  # fmt: skip
        sc = scope_draft(rec.id, net, pages)
        if sc:
            texts["scope.yaml"] = sc
            break
    for name, text in texts.items():
        path = recording / name
        if not path.exists():
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path)
    return written
