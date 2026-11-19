"""Drafts to judge a rebuild with, written beside a recording by `labctl evidence build-all`.

Every draft is unapproved, as the templates of `labctl behaviour acceptance/visual/scope` are: a
person reviews it and approves it (or deletes what should not count). Nothing here approves.

- `expected-actions.draft.yaml`: the recording's semantic actions in order (what `--expect`
  takes), each a person checks against the flow before passing it.
- `visual.draft.yaml` (the replay reads `visual.yaml` by itself, so the draft has another
  name): one state per recorder picture, in threshold mode with a small ratio. The text
  regions are left out by an explicit option (`text_regions`, or `TWINLAB_DRAFT_TEXT=exclude`
  / `compare`): on by default when the package carries no font (a fallback font never matches
  the original's pixels exactly), off when it carries the fonts (then text matches pixel for
  pixel). In every state, text included or not, a region showing data the package masks and
  the page itself carries (the footer's contact lines: in the served HTML, not from the seed)
  is left out with the reason: the build shows its own synthetic values there. The person may
  still choose `mode: exact`.
- `scope.yaml`: exclusions for what the package tells the builder not to replicate (the player of
  a media origin the builder may leave out), unapproved.
- acceptance cases the recording visibly established (`acceptance_cases`, used by
  `labctl.seedplan.acceptance`): the count of a list's rows where a step acted on one of them,
  that count after the step when the step changed it (a cart row deleted), and the record's title
  on the page a click on it opened (`{record: <step>}`: a seed value, never a recorded one),
  and the titles of a list that holds only records the flow acted on (a cart after two adds),
  `unordered: true` unless its order is shown to be stable (`labctl.listorder`: two or more
  page views agree on an order that follows a sortable key that is not random, or a sort
  control the recording used; a UUID, a token or a hash is random). They are
  `required: false` until a person sets `required: true`: an unapproved case never decides a
  replay's result.
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
        at = next((i for i, x in enumerate(sibs, 1) if x is node), None)  # by identity
        if at != nth:
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


def acceptance_cases(part, records: list[dict], number=None) -> list[dict]:
    """Cases from what the recording visibly showed (see the module's doc); `part` is a
    `labctl.seedplan.Part`, `records` its record bindings (`seedplan.record_bindings`).
    `number(seq)` is the step number ids and notes use (the whole recording's, when the cases
    are for it): the same numbering `after:` ends up in. By default the part's own."""
    num = (lambda seq: number(seq) or seq) if number else (lambda seq: seq)
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
            add({"id": f"rows-before-{num(st['seq'])}", "after": earlier[-1], "check": "count",
                 "locator": rows, "equals": n, "basis": "evidence", "required": False,
                 "note": f"the list showed {n} row(s) when step {num(st['seq'])} acted on one"})  # fmt: skip
        later = _settled(part.raw, st)
        if later:
            after_html = part.doms.get(later[-1]["seq"]) or ""
            m = len(select(after_html, rows))
            if m and m != n:
                add({"id": f"rows-after-{num(st['seq'])}", "after": later[-1]["seq"], "check": "count",
                     "locator": rows, "equals": m, "basis": "evidence", "required": False,
                     "note": f"step {num(st['seq'])} changed the list from {n} to {m} row(s)"})  # fmt: skip
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
            add({"id": f"record-shown-{num(r['n'])}", "after": target["seq"], "check": "text",
                 "locator": loc, "equals": {"record": r["n"]}, "basis": "evidence", "required": False,
                 "note": f"the page step {num(r['n'])} opened showed the record it acted on"})  # fmt: skip
    for case in list_cases(part, records, num):
        add(case)
    return out


# --- lists of records, and whether their order is stable ---------------------------------------

_SORT = re.compile(r"\bsort|order[-_ ]?by|\bsorting\b", re.I)
_ARG = re.compile(r"""\(\s*["']([^"']{1,80})["']""")


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _text_of(node) -> str:
    return " ".join(" ".join(node.text).split())


def _position(sibs: list, node) -> int:
    """The 1-based position of a node among its siblings, by identity (nodes compare by value,
    and a node's value holds its parent: `==` would walk the whole tree)."""
    return next(i for i, x in enumerate(sibs, 1) if x is node)


def _list_selector(node) -> tuple[str, object] | None:
    """`#container > row > cell:nth-child(n)...` for a title in a list: the nearest ancestor
    with an id is the container, the level under it the row (by tag and classes), each level
    below by its position. With the row element."""
    path = []
    cur = node
    while cur.parent is not None and cur.parent.tag != "#document":
        path.append(cur)
        if cur.parent.id:
            break
        cur = cur.parent
    else:
        return None
    container, row = path[-1].parent, path[-1]
    parts = [f"#{container.id}", row.tag + "".join(f".{c}" for c in row.classes[:3])]
    for n in reversed(path[:-1]):
        sibs = [x for x in n.parent.children if x.tag]
        parts.append(f"{n.tag}:nth-child({_position(sibs, n)})")
    return " > ".join(parts), row


def row_keys(row, title: str) -> dict:
    """What a row could be ordered by, in memory only: its title, and its key values (ids,
    `data-*`, a quoted argument of an `onclick`, an address's query)."""
    keys: dict = {"title": title}
    for n in [row, *domlib._walk(row)]:
        for name, value in n.attrs.items():
            if not value:
                continue
            if name == "id" or name.startswith("data-"):
                keys.setdefault(f"{n.tag}[{name}]", value)
            elif name.startswith("on"):
                m = _ARG.search(value)
                if m:
                    keys.setdefault(f"{n.tag}[{name}]", m.group(1))
            elif name == "href" and ("?" in value or "#" in value.strip("#")):
                keys.setdefault(f"{n.tag}[href]", value)
    return keys


def _sort_used(part, before_seq: int) -> bool:
    """A recorded step before `before_seq` used a sort control (its selector, text or the
    element's `aria-sort` / class says sort)."""
    for s in part.raw:
        if s["seq"] >= before_seq or s.get("action") not in USER_ACTIONS:
            continue
        said = " ".join(str(x) for x in (s.get("selector"), s.get("text"), s.get("label")) if x)
        if _SORT.search(said):
            return True
        before = part.befores.get(s["seq"])
        for n in select(before, s.get("selector") or "") if before and s.get("selector") else []:
            if "aria-sort" in n.attrs or _SORT.search(" ".join(n.classes)):
                return True
    return False


def list_cases(part, records: list[dict], num=lambda seq: seq) -> list[dict]:
    """A `texts` case for each list a page view showed whose items are all records the flow
    acted on (a cart holding the products added): `{record: <step>}` per item, in the order
    shown, and `unordered: true` unless the order is stable (`labctl.listorder`: two or more
    page views agree on an order that follows a sortable key that is not random, or a sort
    control the recording used). Only lists shown with two items or more somewhere."""
    from labctl import listorder

    found: dict[str, list[tuple[dict, list, list[dict]]]] = {}
    for st in part.raw:
        html = part.doms.get(st["seq"]) if st.get("dom") else None
        acted = {}
        for r in records:
            if r.get("record") and r["n"] < st["seq"]:
                acted[_norm(r["record"])] = r["n"]  # the latest step that acted on it
        if not html or not acted:
            continue
        root = domlib.parse_html(html)
        hits = [x for x in domlib._walk(root) if _norm(_text_of(x)) in acted]
        held = {id(x) for x in hits}
        inner = [x for x in hits if not any(id(c) in held for c in domlib._walk(x))]
        sels = {}
        for x in inner:
            got = _list_selector(x)
            if got:
                sels.setdefault(got[0], []).append(x)
        for sel in sels:
            shown = select(html, sel)
            texts = [_text_of(n) for n in shown]
            if not shown or any(_norm(t) not in acted for t in texts):
                continue  # the list holds rows the flow did not act on
            keys = [row_keys(_list_selector(n)[1], t) for n, t in zip(shown, texts, strict=True)]
            views = found.setdefault(sel, [])
            # the same page shown again with nothing loaded is not another view of the list
            if views and views[-1][2] == keys and st.get("action") != "goto":
                continue
            views.append((st, [acted[_norm(t)] for t in texts], keys))
    out = []
    for sel, views in found.items():
        if max(len(v[1]) for v in views) < 2:
            continue  # one item everywhere: not a list (a title on a detail page)
        last = max(st["seq"] for st, _r, _k in views)
        order = listorder.judge([kv for _s, _r, kv in views], what="page view",
                                sort_used=_sort_used(part, last))  # fmt: skip
        drafted = None
        for st, refs, _keys in views:
            if refs == drafted:
                continue  # the same records again (a reload): one case says it
            drafted = refs
            case = {"id": f"list-{_slug(sel)}-after-{num(st['seq'])}", "after": st["seq"],
                    "check": "texts", "locator": sel, "equals": [{"record": n} for n in refs],
                    "basis": "evidence", "required": False}  # fmt: skip
            steps = ", ".join(str(num(n)) for n in dict.fromkeys(refs))
            note = f"the list showed the record(s) of step(s) {steps}"
            if order.unordered and len(refs) > 1:
                case["unordered"] = True
                note += f"; order {order.label}: {order.reason}; compared in any order"
            elif len(refs) > 1:
                note += f"; order stable: {order.reason}"
            case["note"] = note
            out.append(case)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


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


TEXT_OPTION = "TWINLAB_DRAFT_TEXT"  # exclude | compare: the text regions in the visual draft


def text_regions_default(fonts_carried: bool) -> bool:
    """Whether the visual draft leaves the text regions out: `TWINLAB_DRAFT_TEXT` (`exclude` or
    `compare`) when set, else on exactly when the package carries no font."""
    import os

    said = (os.environ.get(TEXT_OPTION) or "").strip().lower()
    if said in ("exclude", "compare"):
        return said == "exclude"
    return not fonts_carried


def _token_rx(token: str) -> str:
    """A masked token as a pattern: digits `9` any digits, letters `x`/`X` any letters."""
    out = ""
    for run in re.finditer(r"9+|[xX]+|.", token):
        r = run.group(0)
        out += r"\d+" if r[0] == "9" else r"[^\W\d_]+" if r[0] in "xX" else re.escape(r)
    return out


def _fits_masked(masked: str, real: str) -> bool:
    """Whether a recorded text is what the package's masked text stands for: literal words the
    same, each placeholder (`9999`, `Xxxxxx`) a value of that kind."""
    from labctl.lookfeel import _MASKED_TOKEN

    mt, rt = masked.split(), real.split()
    if len(mt) != len(rt) or not mt:
        return False
    some = False
    for m, r in zip(mt, rt, strict=True):
        if m.lower() == r.lower() and not _MASKED_TOKEN.fullmatch(m.strip(".,;:!?()")):
            continue
        if not re.fullmatch(_token_rx(m), r):
            return False
        some = True
    return some


def served_texts(rec_path: Path) -> str:
    """The text of every HTML page the HAR holds as the server sent it, whitespace collapsed,
    in memory only: what the page itself carries, not what its scripts filled in."""
    import base64
    import json

    har = Path(rec_path) / "recording.har"
    if not har.is_file():
        return ""
    try:
        data = json.loads(har.read_text(encoding="utf-8-sig", errors="replace"))
    except ValueError:
        return ""
    out = []
    for e in (data.get("log") or {}).get("entries") or []:
        content = (e.get("response") or {}).get("content") or {}
        if "html" not in str(content.get("mimeType") or "") or not content.get("text"):
            continue
        text = content["text"]
        if content.get("encoding") == "base64":
            try:
                text = base64.b64decode(text).decode("utf-8", errors="replace")
            except ValueError:
                continue
        out.append(" ".join(re.sub(r"\s*(<[^>]*>)\s*", r"\1", text).split()))
    return "\n".join(out)


def _css_path(node) -> str:
    """`#id > tag:nth-child(n) > ...` from the nearest ancestor with an id (else `body`)."""
    parts = []
    cur = node
    while cur.parent is not None and cur.tag not in ("#document", "body", "html"):
        if cur.id and not re.search(r"999", cur.id):
            parts.append(f"#{cur.id}")
            break
        sibs = [x for x in cur.parent.children if x.tag]
        parts.append(f"{cur.tag}:nth-child({_position(sibs, cur)})")
        cur = cur.parent
    else:
        parts.append("body")
    return " > ".join(reversed(parts))


MASKED_REASON = ("data the package masks ({how} text `{text}` in look/texts.json) that the page "
                 "itself carries (in the served HTML, not from the seed): the build shows its own "
                 "synthetic values here, so these pixels never match the recording's")  # fmt: skip


def masked_regions(rec, masked: list[str]) -> dict[int, list[dict]]:
    """Per recorded snapshot, the elements that show data the package masks (`masked`: the
    `data` and `mixed` texts of look/texts.json) and that the served page itself carries (the
    footer's address, phone and e-mail), so the build fills them with its own synthetic values.
    Each is `{css, reason}`, for `exclude:`. Data the page's scripts fill in from the API (a
    cart's rows, a total) comes from the seed and stays compared."""
    literal = {_norm(m["text"]) for m in masked if m.get("is") == "literal"}
    masked = [m for m in masked if m.get("is") in ("data", "mixed")
              # one word of letters (`Xxxx`) would fit any interface word: too vague
              and not re.fullmatch(r"[xX]+", m["text"].strip())]  # fmt: skip
    served = served_texts(rec.path) if masked else ""
    if not served:
        return {}
    out: dict[int, list[dict]] = {}
    for step in getattr(rec, "steps", None) or []:
        html = rec.dom(step)
        if not html:
            continue
        root = domlib.parse_html(html)
        found: dict[str, dict] = {}
        for node in domlib._walk(root):
            if node.tag in ("script", "style", "title", "head"):
                continue
            real = _text_of(node)
            if not real or any(_text_of(c) == real for c in node.children):
                continue  # not the innermost element holding the text
            if _norm(real) in literal:
                continue  # interface text the package gives as written: compared
            hit = next((m for m in masked if _fits_masked(m["text"], real)), None)
            if hit is None or f">{real}<" not in served:
                continue
            css = _css_path(node)
            found.setdefault(css, {"css": css, "reason": MASKED_REASON.format(
                how=hit["is"], text=hit["text"])})  # fmt: skip
        if found:
            out[step.seq] = list(found.values())
    return out


def masked_texts(root: Path, apps: list[str]) -> list[dict]:
    """The texts of the apps' look/texts.json (`text` and `is`: data, mixed or literal; the
    literal ones say which texts are interface text, never excluded)."""
    import json

    out, seen = [], set()
    for a in apps:
        f = Path(root) / "apps" / a / "look" / "texts.json"
        try:
            doc = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        except ValueError:
            continue
        for items in (doc.get("screens") or {}).values():
            for it in items or []:
                key = (it.get("text"), it.get("is"))
                if it.get("is") in ("data", "mixed", "literal") and it.get("text") and (
                        key not in seen):  # fmt: skip
                    seen.add(key)
                    out.append({"text": it["text"], "is": it["is"]})
    return out


def visual_draft(rec, fonts_carried: bool, *, text_regions: bool | None = None,
                 masked: list[dict] | None = None) -> str:  # fmt: skip
    """The visual draft. `text_regions`: leave the text regions out (default:
    `text_regions_default`). `masked`: the package's masked texts (`masked_texts`), whose
    regions are left out in every state when the page itself carries them."""
    from labctl.behaviour import visual

    if text_regions is None:
        text_regions = text_regions_default(fonts_carried)
    text = visual.template(rec)
    data = yaml.safe_load(text) or {}
    moving = [ln for ln in text.splitlines() if ln.startswith("#   ")]
    regions = masked_regions(rec, masked or [])
    for st in data.get("states") or []:
        st["mode"], st["max_ratio"], st["tolerance"] = "threshold", VISUAL_RATIO, VISUAL_TOLERANCE
        extra = [dict(x) for x in regions.get(st.get("after"), [])]
        if text_regions:  # with the regions that change by themselves (the template's)
            st["exclude"] = [dict(TEXT_REGIONS), *(st.get("exclude") or []), *extra]
        else:
            st["exclude"] = [*(st.get("exclude") or []), *extra]
    head = ("# DRAFT, unapproved: one state per recorder picture. threshold mode: up to "
            f"{VISUAL_RATIO:.0%} of the\n# compared pixels may differ (tolerance {VISUAL_TOLERANCE}"
            " per channel), so anti-aliasing and\n# sub-pixel layout do not fail a state.")  # fmt: skip
    how = "the package carries no font" if not fonts_carried else "the package carries the fonts"
    head += (f"\n# Text regions: {'excluded' if text_regions else 'compared'} ({how}; "
             f"{TEXT_OPTION}=exclude|compare sets it).\n")  # fmt: skip
    if text_regions and not fonts_carried:
        head += "# A fallback font never draws the original's pixels, so text is left out.\n"
    if regions:
        head += ("# Data the package masks and the page itself carries (the build shows its own\n"
                 "# synthetic values there) is excluded in the states that show it, text\n"
                 "# included or not: its entry says why.\n")  # fmt: skip
    if moving:
        head += ("# Regions that change by themselves are excluded in every state (delete an entry\n"
                 "# to compare it):\n" + "\n".join(moving) + "\n")  # fmt: skip
    if any(st.get("required") is False for st in data.get("states") or []):
        head += ("# A state whose recorder picture is not settled (the next action had begun when it\n"
                 "# was taken) is optional: its note says why.\n")  # fmt: skip
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


def write_all(root: Path, recording: Path, apps: list[str],
              text_regions: bool | None = None) -> list[Path]:  # fmt: skip
    """The drafts beside the whole recording; a file that exists is never replaced.
    `text_regions`: see `visual_draft`."""
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
    texts = {EXPECTED: expected_draft(rec),
             VISUAL: visual_draft(rec, fonts, text_regions=text_regions,
                                  masked=masked_texts(root, apps))}  # fmt: skip
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
