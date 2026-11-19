"""The navigation map of an application and the actions of each screen, for the builder.

A rebuilt application is only right when every screen is reached the way the original reaches
it and every control does what it did. The recording shows that; this module writes it down in
one file, `apps/<app>/navigation.json`, and in two sections of the brief:

- **Screens and how they are linked**: per screen every way in and out that was observed (a
  link, a button, a form sent, a key, a redirect, back and forward), with the element (selector
  and literal text), the requests it sent and the address pattern it led to.
- **Sub-views**: modal dialogs and other regions that open and close, in-page tabs and their
  panels, iframes ("a page within a page": the page they show, and whether it is this
  application's or another origin's), popup windows and new tabs (what opened them, what closed
  them).
- **Links seen, not followed**: every link target the pages hold that the recording never
  visited, with what the rebuilt application answers there; when `labctl evidence check-links`
  was run (`labctl.linkcheck`), with what the real application answered, and the pages that
  exist but were not recorded.
- **Actions per screen**: every interactive element seen on the screen (button, link, input,
  select, check box, radio, file input, tab), with its selector, literal label, type, where it
  sits (a dialog, a form, a frame), and what happened each time the recording used it (the
  requests, the message, the navigation, the sub-view it opened or closed). An element nobody
  used is listed as "seen, not used": nothing is left to guess, and nothing is invented.

Labels and texts are read from sanitised pages (`sanitize.page`), never from the raw
snapshots: interface text is kept, data is masked, exactly as in `apps/<app>/pages/`.
Visibility and structure (an `open` dialog, a `hidden` panel) are read from the raw snapshots;
they hold no text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from labctl import jsfviews, sanitize, stepfacts

FILE = "navigation.json"
FORMAT = "twinlab-navigation/1"
INTERACTIVE = {"a", "button", "input", "select", "textarea", "summary"}
ROLES = {"button", "tab", "link", "menuitem", "checkbox", "radio", "switch", "option"}
DIALOG_ROLES = {"dialog", "alertdialog"}
RULE = (
    "Every screen, transition and sub-view listed here was observed and must work the same way "
    "in the rebuilt application: the same element (selector and text) leads to the same address "
    "or opens the same dialog, tab panel, frame or window. A link listed as seen, not visited "
    "keeps its element and is answered as its `answer` says. An element listed as seen, not "
    "used is rendered as the snapshot has it; give it no behaviour the brief does not state."
)


# --- reading the pages ---------------------------------------------------------------------------


def _attr_classes(node) -> list[str]:
    return (node.attrs.get("class") or "").split()


def canon(node) -> str:
    """The selector an element is listed under: its id, else its name, test id or first class."""
    tag = node.tag
    if node.id:
        return f"{tag}#{node.id}"
    if node.attrs.get("name"):
        return f'{tag}[name="{node.attrs["name"]}"]'
    for attr in ("data-testid", "data-test", "data-qa", "data-cy"):
        if node.attrs.get(attr):
            return f'{tag}[{attr}="{node.attrs[attr]}"]'
    classes = _attr_classes(node)
    if classes:
        return f"{tag}.{classes[0]}"
    return tag


def _is_interactive(node) -> bool:
    if node.tag == "input":
        return (node.attrs.get("type") or "").lower() != "hidden"
    if node.tag == "a":
        return "href" in node.attrs or bool(node.id) or bool(node.attrs.get("role"))
    return node.tag in INTERACTIVE or (node.attrs.get("role") or "").lower() in ROLES


def _ancestors(node):
    return list(stepfacts._ancestors(node))


def _frame_prefix(node) -> str:
    frames = [a.attrs.get("data-tl-frame") for a in reversed(_ancestors(node))
              if a.tag == "tl-frame" and a.attrs.get("data-tl-frame")]  # fmt: skip
    return "".join(f"{f} >>> " for f in frames)


def _kind(node) -> str:
    tag = node.tag
    role = (node.attrs.get("role") or "").lower()
    if role in ("tab", "menuitem", "switch"):
        return role if role != "menuitem" else "menu entry"
    if tag == "a":
        return "link"
    if tag == "input":
        kind = (node.attrs.get("type") or "text").lower()
        names = {"checkbox": "check box", "radio": "radio button", "file": "file input",
                 "submit": "button (submit)", "button": "button", "reset": "button (reset)",
                 "image": "button (image)"}  # fmt: skip
        return names.get(kind, f"input ({kind})")
    if tag == "button":
        kind = (node.attrs.get("type") or "submit").lower()
        return "button (submit)" if kind == "submit" else "button"
    if tag == "select":
        return "select (several)" if "multiple" in node.attrs else "select"
    if tag == "textarea":
        return "text area"
    return role or tag


def _label(node, labels: dict[str, str]) -> str:
    from labctl.behaviour import dom as domlib

    if node.tag == "input" and (node.attrs.get("type") or "").lower() in ("submit", "button",
                                                                           "reset"):  # fmt: skip
        return node.attrs.get("value") or ""
    if node.tag in ("input", "select", "textarea"):
        if node.id and node.id in labels:
            return labels[node.id]
        for attr in ("aria-label", "placeholder", "title"):
            if node.attrs.get(attr):
                return node.attrs[attr]
        wrap = next((a for a in _ancestors(node) if a.tag == "label"), None)
        return domlib._text(wrap)[:80] if wrap is not None else ""
    text = domlib._text(node)[:80]
    return text or node.attrs.get("aria-label") or node.attrs.get("title") or ""


def _inside(node) -> list[str]:
    out = []
    widget = False  # inside a carousel or slider: not the menu that happens to hold it
    for a in _ancestors(node):
        role = (a.attrs.get("role") or "").lower()
        classes = _attr_classes(a)
        if "carousel" in classes or a.attrs.get("data-ride") == "carousel" or (
                a.attrs.get("aria-roledescription") or "").lower() in ("carousel", "slider"):  # fmt: skip
            out.append(f"carousel `{canon(a)}`")
            widget = True
        elif a.tag == "dialog" or role in DIALOG_ROLES or a.attrs.get("aria-modal") == "true":
            out.append(f"dialog `{canon(a)}`")
        elif role == "tabpanel":
            out.append(f"tab panel `{canon(a)}`")
        elif a.tag == "form":
            out.append(f"form `{canon(a)}`")
        elif (a.tag in ("nav", "menu") or role in ("navigation", "menu", "menubar")) and not widget:
            out.append(f"menu `{canon(a)}`")
    return out


def _href(node) -> str | None:
    href = node.attrs.get("href")
    if href is None:
        return None
    if href.startswith(("http://", "https://", "//")):
        parts = urlsplit(href)
        return (
            f"{parts.scheme or 'https'}://{parts.netloc}{sanitize.route_pattern(parts.path or '/')}"
        )
    if href.startswith(("#", "javascript:", "mailto:", "tel:")) or href == "":
        return href.split(":", 1)[0] + ":" if ":" in href else (href or "#")
    return sanitize.route_pattern(urlsplit(href).path or "/")


def _matches(selector: str, node) -> bool:
    """Whether a recorded selector (its last part) names this element."""
    last = selector.split(">>>")[-1].strip().split(" ")[-1]
    m = re.match(r"^([A-Za-z][\w-]*)?(.*)$", last)
    tag, rest = (m.group(1) or "").lower(), m.group(2)
    if tag and tag != node.tag:
        return False
    for ident in re.findall(r"#((?:\\.|[\w-])+)", rest):
        if node.id != ident.replace("\\", ""):
            return False
    for cls in re.findall(r"\.((?:\\.|[\w-])+)", re.sub(r"\[[^\]]*\]", "", rest)):
        if cls.replace("\\", "") not in _attr_classes(node):
            return False
    for name, value in re.findall(r"\[([\w-]+)=[\"']?([^\"'\]]*)[\"']?\]", rest):
        if node.attrs.get(name) != value:
            return False
    return bool(tag or rest)


class Pages:
    """The snapshots of one recording part, by screen, sanitised once."""

    def __init__(self, ctx):
        from labctl.behaviour import dom as domlib

        self.c = ctx
        self.snaps: list[tuple[int, str, str, object, object]] = []  # seq, when, screen, raw, safe
        part = ctx.part
        views = jsfviews.step_views(part.raw, part.doms)  # JSF: the view, not the address bar
        for s in part.raw:
            screen = views.get(s["seq"]) or stepfacts._pattern(s.get("url"))
            if not screen:
                continue
            for when, store in (("before", part.befores), ("after", part.doms)):
                html = store.get(s["seq"])
                if html is None or (when == "before" and s.get("before_scope") == "frame"):
                    continue
                safe = sanitize.page(html, ctx.known, ctx.vocab).html
                self.snaps.append((s["seq"], when, screen, domlib.parse_html(html),
                                   domlib.parse_html(safe)))  # fmt: skip

    def of(self, screen: str):
        return [x for x in self.snaps if x[2] == screen]


# --- elements and their use ----------------------------------------------------------------------


def elements(pages: Pages, screen: str) -> list[dict]:
    """Every interactive element the snapshots of a screen hold, in page order."""
    from labctl.behaviour import dom as domlib

    found: dict[str, dict] = {}
    for _, _, _, _, safe in pages.of(screen):
        labels = {n.attrs["for"]: domlib._text(n)[:80] for n in domlib._walk(safe)
                  if n.tag == "label" and n.attrs.get("for")}  # fmt: skip
        counts: dict[str, int] = {}
        for node in domlib._walk(safe):
            if not _is_interactive(node):
                continue
            sel = _frame_prefix(node) + canon(node)
            counts[sel] = counts.get(sel, 0) + 1
            el = found.get(sel)
            if el is None:
                el = found[sel] = {"selector": sel, "label": _label(node, labels),
                                   "type": _kind(node), "_node": node}  # fmt: skip
                inside = _inside(node)
                if inside:
                    el["inside"] = inside
                if node.tag == "a" and _href(node) is not None:
                    el["target"] = _href(node)
                    if node.attrs.get("target"):
                        el["opens_in"] = node.attrs["target"]
                if node.tag == "select":
                    el["options"] = [
                        {"value": o.attrs.get("value", domlib._text(o)), "label": domlib._text(o)}
                        for o in domlib._walk(node) if o.tag == "option"][:40]  # fmt: skip
                for flag in ("required", "readonly", "maxlength", "min", "max", "pattern"):
                    if flag in node.attrs:
                        el.setdefault("accepts", {})[flag] = node.attrs[flag] or True
            elif not el.get("label"):
                el["label"] = _label(node, labels)
            if "disabled" in node.attrs:
                el["seen_disabled"] = True
        for sel, n in counts.items():
            if n > 1:
                found[sel]["repeated"] = max(found[sel].get("repeated", 0), n)
    return list(found.values())


def _subview_states(tree) -> dict[str, tuple[str, bool]]:
    """selector -> (kind, open or shown) for every dialog and tab panel of a raw snapshot."""
    from labctl.behaviour import dom as domlib

    out: dict[str, tuple[str, bool]] = {}
    for node in domlib._walk(tree):
        role = (node.attrs.get("role") or "").lower()
        prefix = _frame_prefix(node)
        if domlib.is_dialog(node):
            if _in_player(node):  # a media player's own dialogs (captions settings): its library's
                continue
            # open as drawn: Bootstrap keeps every modal in the page, `show`n when open
            out[prefix + canon(node)] = ("dialog", domlib.shown(node))
        elif role == "tabpanel":
            out[prefix + canon(node)] = ("tab panel", domlib._visible(node))
    return out


def _in_player(node) -> bool:
    return any(sanitize._player(a.attrs.get("class") or "") for a in [node, *_ancestors(node)])


def subviews(pages: Pages, screen: str) -> dict[str, dict]:
    """Dialogs and tab panels of a screen, and the steps that opened and closed each."""
    from labctl.behaviour import dom as domlib

    c = pages.c
    views: dict[str, dict] = {}
    last: dict[str, bool] = {}
    loads = {s["seq"] for s in c.part.raw if s.get("action") == "goto"}
    for seq, _when, _, raw, safe in pages.of(screen):
        if seq in loads:  # a page load shows every sub-view in its first state again
            last = {}
        states = _subview_states(raw)
        for sel, (kind, shown) in states.items():
            view = views.get(sel)
            if view is None:
                view = views[sel] = {"kind": kind, "selector": sel, "opened_by": [],
                                     "closed_by": [], "first_seen": "open" if shown else "closed"}  # fmt: skip
                node = next((n for n in domlib._walk(safe) if _frame_prefix(n) + canon(n) == sel),
                            None)  # fmt: skip
                if node is not None:
                    title = _view_title(node, safe)
                    if title:
                        view["title"] = title
                    view["contains"] = [_frame_prefix(n) + canon(n) for n in domlib._walk(node)
                                        if _is_interactive(n)][:40]  # fmt: skip
            if sel in last and last[sel] != shown:
                step = c.root_step(seq) or {}
                who = {"step": step.get("n", seq), "action": step.get("action"),
                       "selector": step.get("selector")}  # fmt: skip
                side = view["opened_by" if shown else "closed_by"]
                if not any(w["selector"] == who["selector"] and w["action"] == who["action"]
                           for w in side):  # fmt: skip
                    side.append(who)
                else:
                    have = next(w for w in side if w["selector"] == who["selector"])
                    have.setdefault("also_steps", []).append(who["step"])
            last[sel] = shown
        # the tab that controls a panel
        for node in domlib._walk(safe):
            if (node.attrs.get("role") or "").lower() == "tab" and node.attrs.get("aria-controls"):
                panel = next((v for s, v in views.items()
                              if s.endswith("#" + node.attrs["aria-controls"])), None)  # fmt: skip
                if panel is not None:
                    panel["tab"] = _frame_prefix(node) + canon(node)
                    panel["tab_text"] = domlib._text(node)[:80]
    return views


def _view_title(node, tree) -> str:
    from labctl.behaviour import dom as domlib

    # Its own title first: an `aria-labelledby` that names an element outside the dialog is a
    # mistake of the page (Demoblaze's video modal names the Log in modal's title).
    ref = node.attrs.get("aria-labelledby")
    inside = list(domlib._walk(node))
    named = next((n for n in inside if ref and n.id == ref), None)
    if named is not None:
        return domlib._text(named)[:80]
    head = next((n for n in inside if re.fullmatch(r"h[1-6]|legend", n.tag)
                 or "modal-title" in (n.attrs.get("class") or "").split()), None)  # fmt: skip
    if head is not None and domlib._text(head):
        return domlib._text(head)[:80]
    if node.attrs.get("aria-label"):
        return node.attrs["aria-label"][:80]
    named = next((n for n in domlib._walk(tree) if ref and n.id == ref), None)
    return domlib._text(named)[:80] if named is not None else ""


def _record_of(ctx, step: dict) -> str | None:
    """For a click on one row of a list (`a.ticket-link`, repeated per record): the identifier
    of the record it acted on, read from the address of the call or page that followed. Only an
    identifier the application generated is given (it is kept everywhere else too)."""
    raw = ctx.part.step(step["n"])
    if not any(loc.get("kind") == "record" for loc in raw.get("locators") or []):
        return None
    chain = {step["n"]} | {x["n"] for x in ctx.steps() if x.get("caused_by") == step["n"]}
    for n in sorted(chain):
        for e in ctx.part.owner.get(n, []):
            for seg, pat in zip(e.path.split("/"), e.route.split("/"), strict=False):
                if pat.startswith("{") and stepfacts.ID_VALUE.match(seg):
                    return seg
    return None


def _uses(ctx, pages: Pages, screen: str, views: dict[str, dict]) -> dict[str, list[dict]]:
    """Recorded selector -> what happened each time the element was used on the screen."""
    effects = {r["n"]: r for r in ctx.part.facts.effects}
    out: dict[str, list[dict]] = {}
    for s in ctx.steps():
        if s.get("screen") != screen or s.get("action") not in stepfacts.USER_ACTIONS:
            continue
        if s["action"] == "submit" and s.get("caused_by"):
            continue
        if not s.get("selector"):
            continue
        use: dict = {"step": s["n"], "action": s["action"],
                     "_locators": ctx.part.step(s["n"]).get("locators") or []}  # fmt: skip
        if s.get("global") is not None:
            use["global_step"] = s["global"]
        if s.get("key"):
            use["key"] = s["key"]
        if s["n"] in ctx.values:
            use["replay_value"] = ctx.values[s["n"]]
        elif s.get("typed_shape"):
            use["typed_shape"] = s["typed_shape"]
        rec_id = _record_of(ctx, s)
        if rec_id:
            use["record"] = rec_id
        bound = getattr(ctx, "records", {}).get(s["n"])
        if bound is not None and not rec_id:  # a click on one of many rows or cards
            use["record"] = bound.get("record") or "TO DO"
            if not bound.get("record"):
                use["record_candidates"] = bound.get("candidates") or []
            use["record_basis"] = bound.get("basis")
        eff = effects.get(s["n"])
        if eff:
            if eff.get("requests"):
                use["requests"] = eff["requests"]
            if eff.get("then") and eff["then"] != screen:
                use["goes_to"] = eff["then"]
            if eff.get("messages"):
                use["messages"] = [f'{m["selector"]}: "{m["text"]}"' for m in eff["messages"]]
        for sel, view in views.items():
            if any(w["step"] == s["n"] or s["n"] in w.get("also_steps", [])
                   for w in view["opened_by"]):  # fmt: skip
                use.setdefault("opens", []).append(f"{view['kind']} {sel}")
            if any(w["step"] == s["n"] or s["n"] in w.get("also_steps", [])
                   for w in view["closed_by"]):  # fmt: skip
                use.setdefault("closes", []).append(f"{view['kind']} {sel}")
        out.setdefault(s["selector"], []).append(use)
    return out


def _pick(cands: list[dict], locators: list[dict]) -> dict | None:
    """Of the elements a recorded selector matches, the one the step's own locators name (its
    test id, its accessible name or its text); else the first."""
    from labctl.behaviour import dom as domlib

    for loc in locators:
        kind = loc.get("kind")
        for el in cands:
            node = el["_node"]
            if kind == "testid" and node.attrs.get(loc.get("attr") or "data-testid") == loc.get(
                    "value"):  # fmt: skip
                return el
            if kind == "id" and node.id == loc.get("value"):
                return el
            text = loc.get("name") or loc.get("text")
            if kind in ("role", "text", "label") and text and (
                    domlib._text(node) == text or el.get("label") == text):  # fmt: skip
                return el
    return cands[0] if cands else None


def screen_actions(ctxs: list, pages_by: dict, screen: str, views: dict[str, dict]) -> list[dict]:
    """The elements of a screen with their recorded use, or "seen, not used"."""
    merged: dict[str, dict] = {}
    uses: dict[str, list[dict]] = {}
    for c in ctxs:
        for el in elements(pages_by[c.name], screen):
            merged.setdefault(el["selector"], el)
        for sel, rows in _uses(c, pages_by[c.name], screen, views).items():
            for r in rows:
                uses.setdefault(sel, []).append({"recording": c.name, **r} if len(ctxs) > 1 else r)
    for sel, rows in uses.items():
        for row in rows:
            locators = row.pop("_locators", [])
            el = merged.get(sel)
            if el is None:  # a recorded selector that is not an element's listed one: find it
                frame, _, _ = sel.rpartition(">>>")
                prefix = frame.strip() + " >>> " if frame else ""
                cands = [e for e in merged.values() if "_node" in e
                         and e["selector"].startswith(prefix) and _matches(sel, e["_node"])]  # fmt: skip
                el = _pick(cands, locators)
                if el is None:
                    el = merged[sel] = {"selector": sel, "label": "", "type": "element"}
                else:
                    el["recorded_as"] = sel
            el.setdefault("used", []).append(row)
    out = []
    for el in merged.values():
        el = {k: v for k, v in el.items() if k != "_node"}
        if not el.get("used"):
            el["used"] = "seen, not used"
        out.append(el)
    return out


# --- transitions ---------------------------------------------------------------------------------


def _way(kind_of: dict, selector: str | None, action: str) -> str:
    if action == "browser back or forward":
        return "back or forward"
    kind = kind_of.get(selector or "", "")
    if action == "keypress":
        return "key press in a field (sends its form)"
    if action in ("select", "check", "uncheck", "type"):
        return f"{action} (the page reacts to the change)"
    if kind.startswith("link"):
        return "link"
    if kind.startswith(("button", "tab", "menu")):
        return kind.split(" (")[0]
    return action


def transitions(ctxs: list, kinds: dict[str, dict], by_step: dict | None = None) -> list[dict]:
    """Every observed move from one screen to another, merged by (from, element, to)."""
    out: dict[tuple, dict] = {}
    for c in ctxs:
        steps = c.steps()
        for r in c.part.facts.effects:
            if not r.get("then") or r["then"] == r.get("screen"):
                continue
            sel = r.get("selector")
            el = (by_step or {}).get((c.name, r["n"])) or kinds.get(
                r.get("screen") or "", {}).get(sel or "")  # fmt: skip
            form = next((s.get("selector") for s in steps if s["action"] == "submit"
                         and s.get("caused_by") == r["n"]), None)  # fmt: skip
            key = (r.get("screen"), (el or {}).get("selector") or sel, r["action"], r["then"])
            row = out.setdefault(key, {
                "from": r.get("screen"), "to": r["then"],
                "by": _way({k: v.get("type", "") for k, v in kinds.get(r.get("screen") or "", {}).items()},
                           sel, r["action"]),
                "selector": (el or {}).get("selector") or sel,
                "text": (el or {}).get("label", ""), "steps": []})  # fmt: skip
            if el and el.get("recorded_as") and el["recorded_as"] != row["selector"]:
                row["recorded_as"] = el["recorded_as"]
            if r.get("key"):
                row["key"] = r["key"]
            if form:
                row["sends_form"] = form
            if r.get("requests") and "requests" not in row:
                row["requests"] = r["requests"]
            row["steps"].append(r["n"])
    return list(out.values())


def windows(ctxs: list) -> list[dict]:
    """Popup windows and new tabs: what opened each, what it showed, what closed it."""
    out = []
    for c in ctxs:
        steps = c.steps()
        raw = {s["seq"]: s for s in c.part.raw}
        for s in steps:
            if s["action"] != "tab-open":
                continue
            tab = s.get("tab")
            opener = raw.get(s["n"], {}).get("opener")
            before = [x for x in steps if x["n"] < s["n"] and x.get("tab") == opener
                      and x["action"] in stepfacts.USER_ACTIONS and x.get("selector")]  # fmt: skip
            trigger = before[-1] if before else None
            shown = next((x.get("then") or x.get("screen") for x in steps
                          if x["n"] > s["n"] and x.get("tab") == tab and x["action"] == "goto"),
                         stepfacts._pattern(raw.get(s["n"], {}).get("url")))  # fmt: skip
            row = {"kind": "popup window or new tab", "shows": shown, "tab": tab,
                   "opened_at_step": s["n"]}  # fmt: skip
            if trigger:
                row["opened_from"] = trigger.get("screen")
                row["opened_by"] = {"step": trigger["n"], "action": trigger["action"],
                                    "selector": trigger["selector"]}  # fmt: skip
            inside = [x for x in steps if x.get("tab") == tab and x["n"] > s["n"]]
            close = next((x for x in inside if x["action"] == "tab-close"), None)
            if close:
                last = [x for x in inside if x["n"] < close["n"]
                        and x["action"] in stepfacts.USER_ACTIONS and x.get("selector")]  # fmt: skip
                near = last[-1] if last else None
                gap = (close["n"] - near["n"]) if near else None
                row["closed"] = ({"step": close["n"], "after": f"{near['action']} {near['selector']}",
                                  "note": "closed right after this action: the element closes "
                                          "the window (hypothesis)"} if near and gap == 1
                                 else {"step": close["n"], "note": "closed by the person"})  # fmt: skip
                back = next((x for x in steps if x["n"] > close["n"]
                             and x["action"] == "tab-switch"), None)  # fmt: skip
                if back:
                    row["then"] = "back to the window it was opened from" + (
                        f" (`{row['opened_from']}`)" if row.get("opened_from") else ""
                    )
            out.append(row)
    return out


def redirects(ctxs: list) -> list[dict]:
    out: dict[tuple, dict] = {}
    for c in ctxs:
        signed_in = False
        for e in c.part.exchanges:
            if e.cookies_set and 200 <= e.status < 400 and e.method == "POST":
                signed_in = True
            if 300 <= e.status < 400 and e.location:
                key = (e.method, e.route, sanitize.route_pattern(e.location))
                row = out.setdefault(key, {"request": f"{e.method} {e.route}", "status": e.status,
                                           "to": key[2], "times": 0})  # fmt: skip
                row["times"] += 1
                if not signed_in and e.method == "GET":
                    row["when"] = "asked for before signing in (no session)"
    return list(out.values())


# --- the whole map -------------------------------------------------------------------------------


def resolve_target(target: str, base: str = "/") -> str:
    """A link's address as a screen's: `index.html` on `/cart.html` is `/index.html` (a relative
    `href` resolved against the page it is on); an absolute path stays as it is."""
    if target.startswith("/"):
        return target
    return sanitize.route_pattern(urlsplit(urljoin(base or "/", target)).path or "/")


def build(root: Path, app: str, brief, ctxs: list) -> dict:
    from labctl import multiapp

    root = Path(root)
    pages_by = {c.name: Pages(c) for c in ctxs}
    in_scope = {sc.screen.path: sc for sc in brief.screens if sc.screen.path}
    out_scope = {s.path for s in brief.out_screens if s.path}
    seen_screens: list[str] = list(in_scope)
    for c in ctxs:
        for s in c.steps():
            for p in (s.get("screen"), s.get("then")):
                if p and p not in seen_screens:
                    seen_screens.append(p)
    others: dict[str, str] = {}
    for _, data in multiapp.sequences_of(root, app):
        for r in data.get("steps") or []:
            if r.get("app") and r.get("app") != app and r.get("path"):
                others[r["path"]] = r["app"]
    recorded: dict[str, str] = {}
    for c in ctxs:
        for e in c.part.exchanges:
            if e.method == "GET" and e.status:
                tail = f" then `{sanitize.route_pattern(e.location)}`" if e.location else ""
                recorded.setdefault(e.route, f"recorded as `GET {e.route} {e.status}`{tail}")
    first = stepfacts._first_screen(brief)
    from labctl import linkcheck  # what `labctl evidence check-links` found, when it was run

    checked = linkcheck.for_app(root, ctxs)
    signin = next((x for s in (checked.summary if checked else []) for x in s["sign_in"]), None)

    def answer(target: str | None, base: str = "/") -> tuple[str, str]:
        if target is None:
            return "none", "the element has no address"
        if target.startswith(("http://", "https://")):
            return "external", "another site: keep the link as it is; nothing is built for it"
        if target.endswith(":") or target.startswith("#"):
            return "in page", "stays on the page"
        target = resolve_target(target, base)
        if target in in_scope:
            return "in scope", f"the screen `{target}` of this brief"
        if target in others:
            return "another application", (f"the application `{others[target]}` (its own "
                                           "package); see the brief's cross-application section")  # fmt: skip
        if target in recorded:
            return "recorded", recorded[target] + ": answer the same way"
        if first:
            return "out of scope", (f"not built: keep the element, answer `GET {target}` with a "
                                    f"redirect (303) to `{first}`")  # fmt: skip
        return "undefined", "no screen, no recorded answer and no first screen to redirect to"

    screens = []
    kinds: dict[str, dict] = {}
    undefined: list[str] = []
    for path in seen_screens:
        sc = in_scope.get(path)
        views = {}
        for c in ctxs:
            for sel, view in subviews(pages_by[c.name], path).items():
                have = views.setdefault(sel, view)
                if have is not view:
                    have["opened_by"] += [
                        w for w in view["opened_by"] if w not in have["opened_by"]
                    ]
                    have["closed_by"] += [
                        w for w in view["closed_by"] if w not in have["closed_by"]
                    ]
        jsf = [v for c in ctxs for v in jsfviews.subviews(c, path, pages_by[c.name])]
        acts = screen_actions(ctxs, pages_by, path, views)
        kinds[path] = {a["selector"]: a for a in acts}
        for a in acts:
            if a.get("recorded_as"):
                kinds[path][a["recorded_as"]] = a
        row: dict = {"path": path, "in_scope": sc is not None}
        also = sorted(a for a, to in sanitize.aliases().items() if to == path)
        if also:  # the same document at another address (`/index.html` of `/`): serve both
            row["aliases"] = also
        if sc is not None:
            row["name"] = sc.screen.name
            if sc.screen.files:
                row["page"] = sc.screen.files[0]
            if getattr(sc, "standin", None):
                row["kind"] = "stand-in for a page of another origin, shown in a frame"
        elif path in out_scope:
            row["note"] = "recorded, out of scope: not built"
        elif path in others:
            row["note"] = f"a screen of the application `{others[path]}`"
        frames = []
        for f in sc.screen.frames if sc is not None else []:
            target = sanitize.route_pattern(urlsplit(str(f.get("src") or "")).path or "/")
            inner = in_scope.get(target)
            emb = (inner.screen.embedded or {}) if inner is not None else {}
            frames.append({
                "kind": "iframe (a page within the page)", "selector": f.get("selector"),
                "title": f.get("title") or "", "shows": target,
                "origin": "another origin" if emb else "this application",
                "in_scope": inner is not None,
                "build": ("a stand-in page this application serves at the same path (the brief "
                          "says how)" if emb else f"the screen `{target}` of this brief, loaded "
                          "in the frame" if inner is not None else answer(target, path)[1])})  # fmt: skip
        framed_in = [(p, f.get("selector")) for p, other in in_scope.items()
                     for f in other.screen.frames
                     if sanitize.route_pattern(urlsplit(str(f.get("src") or "")).path or "/") == path]  # fmt: skip
        if framed_in:
            row["kind"] = "shown in a frame"
            row["note"] = ("shown in " + ", ".join(f"`{sel}` on `{p}`" for p, sel in framed_in)
                           + ": its elements and their use are listed under that screen, with "
                           "the frame's selector in front (`iframe#x >>> ...`)")  # fmt: skip
        sub = jsfviews.merge([dict(view) for view in views.values()], jsf, frames)
        if sub:
            row["subviews"] = sub
        row["elements"] = acts
        not_followed = []
        for a in acts:
            if a.get("type") != "link" or isinstance(a.get("used"), list):
                continue
            status, text = answer(a.get("target"), path)
            found = checked.lookup(a.get("target")) if checked else None
            if found is not None and status in ("out of scope", "undefined"):
                said, why = linkcheck.answer(found, a["target"], first, signin)
                status, text = (said, why) if said else (status, f"{text}; {why}")
            if status == "undefined":
                undefined.append(f"{path}: {a['selector']} -> {a.get('target')}")
            not_followed.append({"selector": a["selector"], "text": a.get("label", ""),
                                 "target": a.get("target"), "status": status, "answer": text})  # fmt: skip
        if not_followed:
            row["links_seen_not_visited"] = not_followed
        screens.append(row)
    # The same element (selector and text) used on one screen and only seen on another, as in a
    # menu every screen shares: it is one control, and behaves the same wherever it is.
    used_on: dict[tuple, str] = {}
    for row in screens:
        for el in row["elements"]:
            if isinstance(el.get("used"), list):
                used_on.setdefault((el["selector"], el.get("label")), row["path"])
    for row in screens:
        for el in row["elements"]:
            where = used_on.get((el["selector"], el.get("label")))
            if not isinstance(el.get("used"), list) and where and el.get("type") != "link":
                el["used"] = (f"seen, not used on this screen; the same element was used on "
                              f"`{where}`: it behaves the same here")  # fmt: skip
    for row in screens:  # a link into another application of the recording
        for el in row["elements"]:
            app_to = others.get(el.get("target") or "")
            for use in el["used"] if app_to and isinstance(el.get("used"), list) else []:
                use.setdefault("goes_to", f"{el['target']} of the application {app_to}"
                               + (" (in a new tab)" if el.get("opens_in") == "_blank" else ""))  # fmt: skip
    by_step = {}
    for row in screens:
        for el in row["elements"]:
            for use in el["used"] if isinstance(el.get("used"), list) else []:
                by_step[(use.get("recording") or (ctxs[0].name if ctxs else ""), use["step"])] = el
    moves = transitions(ctxs, kinds, by_step)
    for m in moves:
        if m["to"] not in in_scope and m["to"] not in others and m["to"] not in out_scope:
            undefined.append(f"{m['from']}: {m.get('selector')} -> {m['to']} (visited, no screen)")
    wins = windows(ctxs)
    for w in wins:  # the element that opened a window, and the one that closed it, say so
        marks = [((w.get("opened_by") or {}).get("step"), "opens",
                  f"popup window or new tab showing {w.get('shows')}")]  # fmt: skip
        closed = w.get("closed") or {}
        if closed.get("after"):
            marks.append((closed["step"] - 1, "closes", "this window"))
        for step, what, text in marks:
            for row in screens:
                for el in row["elements"]:
                    for use in el["used"] if isinstance(el.get("used"), list) else []:
                        if use["step"] == step and text not in use.get(what, []):
                            use.setdefault(what, []).append(text)
    for row in screens:
        row["ways_in"] = [{k: m[k] for k in ("from", "by", "selector", "text", "steps") if k in m}
                          for m in moves if m["to"] == row["path"]]  # fmt: skip
        row["ways_out"] = [{k: m[k] for k in ("to", "by", "selector", "text", "steps") if k in m}
                           for m in moves if m["from"] == row["path"]]  # fmt: skip
        for w in wins:
            if w.get("shows") == row["path"]:
                row["kind"] = "shown in a popup window or new tab"
                row["ways_in"].append({"from": w.get("opened_from"), "by": "popup window or new tab",
                                       "selector": (w.get("opened_by") or {}).get("selector"),
                                       "steps": [w["opened_at_step"]]})  # fmt: skip
            if w.get("opened_from") == row["path"]:
                row.setdefault("subviews", []).append(w)
    entry = None
    jsf_doc = {"screens": screens, "transitions": moves}
    jsfviews.annotate(jsf_doc, ctxs)  # JSF: partial requests per use, forwards, the jsf section
    for c in ctxs:
        start = next((s for s in c.steps() if s["action"] == "goto" and not s.get("caused_by")
                      and (s.get("then") or s.get("screen"))), None)  # fmt: skip
        if start and entry is None:
            entry = {"screen": start.get("then") or start.get("screen"), "step": start["n"]}
    return {
        "format": FORMAT,
        "app": app,
        "rule": RULE,
        "entry": entry,
        "screens": screens,
        "transitions": moves,
        "redirects": redirects(ctxs),
        "windows": wins,
        "undefined_targets": sorted(set(undefined)),
        **({"jsf": jsf_doc["jsf"]} if "jsf" in jsf_doc else {}),
        **({"link_check": checked.summary,
            "unrecorded_pages": linkcheck.unrecorded_pages(checked, app)} if checked else {}),
    }  # fmt: skip


def write(root: Path, app: str, brief, ctxs: list, extend=None) -> dict | None:
    """`extend(doc)`: adds what other evidence shows (labctl.codemap: the code's routes)."""
    path = Path(root) / "apps" / app / FILE
    if not ctxs:
        path.unlink(missing_ok=True)
        return None
    doc = build(root, app, brief, ctxs)
    jsfviews.write_snapshots(root, app, doc)
    if extend is not None:
        doc = extend(doc) or doc
    from labctl import linkcheck

    linkcheck.copy_pages(root, app, doc.get("unrecorded_pages") or [])
    text = json.dumps(doc, indent=1, ensure_ascii=False).replace("\r\n", "\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return doc


# --- the brief -----------------------------------------------------------------------------------


def _steps(nums: list[int]) -> str:
    return ", ".join(str(n) for n in nums[:6]) + (" ..." if len(nums) > 6 else "")


def _el(sel: str | None, text: str = "") -> str:
    if not sel:
        return "-"
    return f"`{sel}`" + (f' "{text}"' if text else "")


def brief_sections(app: str, doc: dict | None) -> str:
    if not doc:
        return ""
    out = ["## Navigation map\n",
           f"Machine-readable: `apps/{app}/navigation.json`. {RULE}\n"]  # fmt: skip
    if doc.get("entry"):
        out.append(f"The recording started on `{doc['entry']['screen']}`.\n")
    if doc["transitions"]:
        out += ["**From screen to screen** (step numbers of `steps/<recording>.json`):\n",
                "| From | Element | How | Requests | To | Steps |", "|---|---|---|---|---|---|"]  # fmt: skip
        for m in doc["transitions"]:
            if m.get("source", "recording") != "recording":
                continue  # links on pages and the code's own: in their sections
            how = (
                m["by"]
                + (f" (key {m['key']})" if m.get("key") else "")
                + (f", sends `{m['sends_form']}`" if m.get("sends_form") else "")
            )
            reqs = "<br>".join(f"`{r}`" for r in (m.get("requests") or [])[:4]) or "-"
            out.append(f"| `{m['from']}` | {_el(m.get('selector'), m.get('text', ''))} | {how} | "
                       f"{reqs} | `{m['to']}` | {_steps(m['steps'])} |")  # fmt: skip
        out.append("")
    if doc["redirects"]:
        out.append("**Redirects the application answered:**\n")
        for r in doc["redirects"]:
            when = f" ({r['when']})" if r.get("when") else ""
            out.append(f"- `{r['request']}` answered `{r['status']}` to `{r['to']}`{when}, "
                       f"{r['times']} time(s).")  # fmt: skip
        out.append("")
    subs = [(s["path"], v) for s in doc["screens"] for v in s.get("subviews") or []]
    if subs:
        out += ["**Sub-views: dialogs, tab panels, frames, windows.** Each opens and closes as "
                "recorded, on the same element:\n"]  # fmt: skip
        for path, v in subs:
            kind = v["kind"]
            if kind.startswith("iframe"):
                out.append(f"- `{path}`: frame `{v['selector']}`"
                           + (f' (title "{v["title"]}")' if v.get("title") else "")
                           + f" shows `{v['shows']}` ({v['origin']}): {v['build']}.")  # fmt: skip
            elif kind.startswith("popup"):
                by = v.get("opened_by") or {}
                closed = v.get("closed") or {}
                out.append(f"- `{path}`: {by.get('action', 'an action')} `{by.get('selector')}` "
                           f"(step {by.get('step')}) opens `{v['shows']}` in a popup window or new "
                           "tab (keep how the element opens it: a link's `target`, or a script's "
                           "`window.open`)"
                           + (f"; {closed.get('note')}"
                              + (f" (`{closed['after']}`)" if closed.get("after") else "")
                              if closed else "")
                           + (f"; then {v['then']}" if v.get("then") else "") + ".")  # fmt: skip
            else:
                title = f' "{v["title"]}"' if v.get("title") else ""
                tab = (
                    f', the panel of tab `{v["tab"]}` "{v.get("tab_text", "")}"'
                    if v.get("tab")
                    else ""
                )
                opened = "; ".join(f"{w['action']} `{w['selector']}` (step {w['step']})"
                                   for w in v["opened_by"]) or "not opened in the recording"  # fmt: skip
                closed = "; ".join(f"{w['action']} `{w['selector']}` (step {w['step']})"
                                   for w in v["closed_by"]) or "not closed in the recording"  # fmt: skip
                out.append(f"- `{path}`: {kind} `{v['selector']}`{title}{tab}; first seen "
                           f"{v['first_seen']}; opened by: {opened}; closed by: {closed}.")  # fmt: skip
        out.append("")
    loose = [(s["path"], x) for s in doc["screens"] for x in s.get("links_seen_not_visited") or []]
    if loose:
        out += ["**Links seen, not visited.** Keep each element as the snapshot has it:\n"]
        merged: dict[tuple, list[str]] = {}
        for path, x in loose:
            merged.setdefault((x["selector"], x.get("text", ""), x.get("target"), x["answer"]),
                              []).append(path)  # fmt: skip
        for (sel, text, target, ans), paths in merged.items():
            on = (f"{len(paths)} screens" if len(paths) > 3
                  else ", ".join(f"`{p}`" for p in paths))  # fmt: skip
            out.append(f"- {_el(sel, text)} on {on} -> `{target}`: {ans}.")
        out.append("")
    from labctl import linkcheck

    out += linkcheck.brief_lines(doc)
    if doc.get("undefined_targets"):
        out += ["**Targets with no definition (a gap in this package):**\n",
                *[f"- {u}" for u in doc["undefined_targets"]], ""]  # fmt: skip
    out += ["## Actions on each screen\n",
            "Every interactive element seen on each screen, in page order, and what happened each "
            "time the recording used it. *Seen, not used* means nobody used it: render it as the "
            "snapshot has it and give it no behaviour this brief does not state. Full detail "
            f"(options, replay values, every step): `apps/{app}/navigation.json`.\n"]  # fmt: skip
    for s in doc["screens"]:
        if s["in_scope"] and not s.get("elements") and s.get("note"):
            out += [f"**`{s['path']}`**: {s['note']}.", ""]
        if not s["in_scope"] or not s.get("elements"):
            continue
        out += [f"**`{s['path']}`**\n", "| Element | Text or label | Type | Where | In the recording |",
                "|---|---|---|---|---|"]  # fmt: skip
        for el in s["elements"]:
            where = "; ".join(el.get("inside") or []) or "-"
            if el.get("repeated"):
                where += f"; repeated (up to {el['repeated']} at once)"
            out.append(f"| `{el['selector']}` | {el.get('label') or '-'} | {el['type']}"
                       + (f" -> `{el['target']}`" if el.get("target") else "")
                       + f" | {where} | {_use_text(el)} |")  # fmt: skip
        out.append("")
    return "\n".join(out) + "\n"


def _use_text(el: dict) -> str:
    uses = el.get("used")
    if not isinstance(uses, list):
        return str(uses or "seen, not used")
    said: dict[str, list[int]] = {}
    for u in uses:
        bits = [u["action"] + (f" (key {u['key']})" if u.get("key") else "")]
        if "replay_value" in u or "typed_shape" in u:
            bits.append("a value" if u["action"] == "type" else "an option")
        if u.get("requests"):
            bits.append("sent " + ", ".join(f"`{r}`" for r in u["requests"][:3]))
        if u.get("opens"):
            bits.append("opened " + ", ".join(f"`{x}`" for x in u["opens"]))
        if u.get("closes"):
            bits.append("closed " + ", ".join(f"`{x}`" for x in u["closes"]))
        if u.get("record"):
            bits.append(f"on the row of `{u['record']}`")
        if u.get("goes_to"):
            bits.append(f"went to `{u['goes_to']}`")
        if u.get("messages"):
            bits.append("showed " + "; ".join(u["messages"][:3]))
        if len(bits) == 1 and u["action"] in ("click", "dblclick", "keypress"):
            bits.append("no request, navigation or message recorded")
        said.setdefault(": ".join([bits[0], ", ".join(bits[1:])]) if len(bits) > 1 else bits[0],
                        []).append(u["step"])  # fmt: skip
    return "<br>".join(f"{text} (step {_steps(nums)})" for text, nums in list(said.items())[:6])
