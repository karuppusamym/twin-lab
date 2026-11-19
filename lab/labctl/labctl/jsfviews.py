"""JavaServer Faces views: what a recording of a JSF (or PrimeFaces) application shows that its
addresses do not.

Office JSF applications break the rule "one address, one screen" in two ways:

- **A postback answered with the next view** (navigation by forward, no redirect): the address
  bar keeps the address of the page the form was on. After signing in, the workspace shows while
  the address still says `/login.xhtml`; a screen opened from the workspace shows under
  `/workspace.xhtml`. Screens told apart by address merge the wrong pages and lose their links.
- **Sub-pages inside one view**: an ajax request re-renders a region (a dynamic `ui:include`, a
  `p:outputPanel` updated by `p:ajax`), a tab, a wizard step, an accordion panel, a dialog (a
  `p:dialog`, a `p:confirmDialog`, a dialog-framework page shown in an iframe), or a data table
  (a page, a sort, a filter, a selected or expanded row), while the address never changes.

This module reads both from what the recording already holds, without running any of the page's
code:

- `view_id(html)`: the view a snapshot shows, from the page itself: the address its JSF forms
  post to (a JSF form posts to its own view), else PrimeFaces' `PrimeFaces.settings.viewId`.
  `step_views(steps, doms)` gives every step its view; the screen of a step is that view, not
  the address (`stepfacts`, `navmap` and `har.analyse` use it), and `address_lag` lists where the
  address shows another page than the view.
- `partials(har, steps)`: every JSF partial response in the HAR (`<partial-response>`): the
  component that sent it, its event, what it asked to execute and render, and what the answer
  did: the regions it updated, the scripts it ran (by name), a redirect, an error, PrimeFaces'
  callback arguments (`validationFailed`). The view state and the head resources it also updated
  are listed as ignored: a rebuilt application makes its own.
- `subviews(...)`: per screen, every state the recording reached inside it (a sub-page of a
  region, a tab, a wizard step, an accordion panel, a dialog, a dialog-framework page, the page,
  sort, row count, selection and expansion of a data table), each with the step and element that
  opened it, the regions that action re-rendered, and its own value-free snapshot.
- `widgets(recordings)`: the PrimeFaces widgets the pages and partial responses create
  (`PrimeFaces.cw("DataTable", "casesTable", {id: "main:cases", ...})`): component type, widget
  variable, client id and the option names it was given (never their values).

Everything written is structure and interface text: client ids, component types, the titles of
tabs, steps and dialogs as the sanitiser leaves them. No value a person typed and no data cell.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

VIEW_STATE = re.compile(r"(?:javax|jakarta)\.faces\.ViewState")
PF_VIEW_ID = re.compile(r"PrimeFaces\.settings\.viewId\s*=\s*['\"]([^'\"]+)['\"]")
FORM = re.compile(r"<form\b[^>]*>", re.I)
ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
TL_FRAME = re.compile(r"<tl-frame\b.*?</tl-frame>", re.I | re.S)
GENERATED = re.compile(r"(^|:)j_id")  # JSF names a component without an id j_idtN: it moves
HEADING = re.compile(r"h[1-3]")
PARTIAL = re.compile(r"<partial-response\b", re.I)
CW = re.compile(r"""PrimeFaces\.cw\(\s*["'](\w+)["']\s*,\s*["']([\w$.:-]*)["']\s*,\s*\{""")
SCRIPT_CALL = re.compile(r"\b(PrimeFaces(?:\.\w+){1,3}|PF\(\s*['\"][\w$:-]+['\"]\s*\)\.\w+|"
                         r"jsf\.\w+(?:\.\w+)?|faces\.\w+(?:\.\w+)?)\s*\(")  # fmt: skip
IGNORED_UPDATES = re.compile(r"(?:javax|jakarta)\.faces\.(ViewState|Resource)|ViewRoot$")
FACES_PARAMS = ("source", "partial.event", "behavior.event", "partial.execute", "partial.render")


# Read-only: whether PrimeFaces still holds a request (queued, in flight, or a data table's filter
# waiting out its delay) or jQuery animates. The replay waits for it as the recorder does.
BUSY_JS = """() => {
  try {
    const pf = window.PrimeFaces, q = pf && pf.ajax && pf.ajax.Queue;
    if (q && ((q.requests && q.requests.length) || (q.xhrs && q.xhrs.length))) return true;
    if (pf && pf.widgets) for (const k in pf.widgets) if (pf.widgets[k] && pf.widgets[k].filterTimeout) return true;
    const jq = window.jQuery;
    return !!(jq && (jq.active > 0 || (jq.timers && jq.timers.length)));
  } catch (e) { return false; }
}"""


# --- which view a snapshot shows ------------------------------------------------------------------


def is_jsf(html: str | None) -> bool:
    return bool(html) and VIEW_STATE.search(html) is not None


def _top(html: str) -> str:
    """The top document of a snapshot: the documents of its frames (`<tl-frame>`) left out."""
    prev = None
    while prev != html:
        prev, html = html, TL_FRAME.sub("", html)
    return html


def view_id(html: str | None) -> str | None:
    """The address pattern of the view a JSF snapshot shows, None when it is not a JSF page or
    says nothing. A JSF form posts to its own view, so the forms' `action` names it even when
    the address bar shows the page before (a forward); PrimeFaces also writes the view id."""
    from labctl import sanitize

    if not is_jsf(html):
        return None
    top = _top(html)
    actions = Counter()
    for tag in FORM.findall(top):
        attrs = {m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3)
                 for m in ATTR.finditer(tag)}  # fmt: skip
        action = attrs.get("action") or ""
        if (attrs.get("method") or "get").lower() == "post" and action.startswith("/"):
            actions[sanitize.route_pattern(action)] += 1
    if actions:
        return actions.most_common(1)[0][0]
    m = PF_VIEW_ID.search(top)
    return sanitize.route_pattern(m.group(1)) if m else None


def _url_key(url: str) -> str:
    from labctl import sanitize

    parts = urlsplit(url)
    return f"{parts.netloc.lower()}{sanitize.route_pattern(parts.path or '/')}?{parts.query}"


def step_views(steps: list[dict], doms: dict[int, str]) -> dict[int, str]:
    """seq -> the view (address pattern) each step was on, for the steps of a JSF recording
    whose snapshot (or the last snapshot of the same tab at the same address) names one."""
    out: dict[int, str] = {}
    last: dict[str, tuple[str, str]] = {}  # tab -> (address key, view)
    for s in steps:
        seq, url = s.get("seq"), str(s.get("url") or "")
        if not isinstance(seq, int) or not url.startswith(("http:", "https:")):
            continue
        key, tab = _url_key(url), str(s.get("tab") or "t1")
        view = view_id(doms.get(seq))
        if view:
            out[seq] = view
            last[tab] = (key, view)
        elif tab in last and last[tab][0] == key:
            out[seq] = last[tab][1]
    return out


def address_lag(steps: list[dict], views: dict[int, str]) -> list[dict]:
    """The page loads after which the address bar showed another page than the view: a postback
    answered with the next view (a forward) instead of a redirect."""
    from labctl import stepfacts

    out = []
    for s in steps:
        view = views.get(s.get("seq"))
        shown = stepfacts._pattern(s.get("url"))
        if s.get("action") == "goto" and view and shown and view != shown:
            out.append({"step": s["seq"], "address": shown, "view": view})
    return out


# --- partial responses --------------------------------------------------------------------------


def _cdata(text: str) -> str:
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1), text, flags=re.S)


def parse_partial(text: str | None) -> dict | None:
    """What a JSF partial response did: regions updated (client ids, with whether the new HTML
    holds a heading and how many components), scripts run (by the functions they call),
    a redirect, an error, PrimeFaces' callback arguments (names, and `validationFailed`)."""
    if not text or not PARTIAL.search(text):
        return None
    out: dict = {"updates": [], "ignored": [], "evals": [], "redirect": None, "error": None}
    for m in re.finditer(r'<update\s+id="([^"]+)"\s*>(.*?)</update>', text, re.S):
        cid, body = m.group(1), _cdata(m.group(2))
        if IGNORED_UPDATES.search(cid):
            out["ignored"].append(cid)
            continue
        ids = re.findall(r'\sid="([^"]+)"', body)
        out["updates"].append({"id": cid, "heading": bool(re.search(r"<h[1-3][\s>]", body)),
                               "components": len({i for i in ids if not GENERATED.search(i)})})  # fmt: skip
    for m in re.finditer(r"<eval>(.*?)</eval>", text, re.S):
        calls = sorted({re.sub(r"\(\s*['\"][^'\"]*['\"]\s*\)", "('...')", c)
                        for c in SCRIPT_CALL.findall(_cdata(m.group(1)))})  # fmt: skip
        out["evals"].append(calls or ["(a script)"])
    red = re.search(r'<redirect\s+url="([^"]*)"', text)
    if red:
        from labctl import sanitize

        out["redirect"] = sanitize.route_pattern(red.group(1).replace("&amp;", "&"))
    err = re.search(r"<error>.*?<error-name>(.*?)</error-name>", text, re.S)
    if err:
        out["error"] = _cdata(err.group(1)).strip()[:120]
    for m in re.finditer(r'<extension\s+[^>]*type="args"[^>]*>(.*?)</extension>', text, re.S):
        try:
            args = json.loads(_cdata(m.group(1)))
        except ValueError:
            continue
        if isinstance(args, dict):
            out["callback_args"] = sorted(args)
            if args.get("validationFailed") is True:
                out["validation_failed"] = True
    return out


def _post_params(req: dict) -> dict[str, str]:
    post = req.get("postData") or {}
    params = {str(p.get("name")): str(p.get("value") or "") for p in post.get("params") or []
              if isinstance(p, dict)}  # fmt: skip
    if not params and post.get("text"):
        params = dict(parse_qsl(str(post["text"]), keep_blank_values=True))
    return params


def partials(har_doc: dict, steps: list[dict]) -> dict[int, list[dict]]:
    """step seq -> the JSF partial requests it caused (the last step at or before each one),
    each with its request (component, event, execute, render: client ids only, never a value)
    and what the answer did (`parse_partial`)."""
    from labctl import har, sanitize

    timed = [(s.get("seq"), har._time(s.get("ts"))) for s in steps]
    timed = [(n, t) for n, t in timed if isinstance(n, int) and t is not None]
    out: dict[int, list[dict]] = {}
    for entry in (har_doc.get("log") or {}).get("entries") or []:
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        if str(req.get("method") or "").upper() != "POST":
            continue
        done = parse_partial((resp.get("content") or {}).get("text"))
        params = _post_params(req)
        if done is None and "javax.faces.partial.ajax" not in params \
                and "jakarta.faces.partial.ajax" not in params:  # fmt: skip
            continue
        started = har._time(entry.get("startedDateTime"))
        at = None
        for n, t in timed:
            if started is not None and t <= started:
                at = n
        if at is None:
            continue
        row: dict = {"request": f"POST {sanitize.route_pattern(urlsplit(str(req.get('url'))).path)}"
                     f" {resp.get('status')}"}  # fmt: skip
        for name in FACES_PARAMS:
            v = params.get(f"javax.faces.{name}") or params.get(f"jakarta.faces.{name}")
            if v:
                key = name.replace("partial.", "").replace("behavior.", "")
                row["event" if key == "event" else key] = (
                    v if key in ("source", "event") else v.split()
                )
        if done is not None:
            row.update({k: v for k, v in done.items() if v not in (None, [], {})})
        out.setdefault(at, []).append(row)
    return out


# --- the states inside a view -------------------------------------------------------------------


def _text(node) -> str:
    from labctl.behaviour import dom as domlib

    return " ".join(domlib._text(node).split())[:80]


def _shown(node) -> bool:
    from labctl.behaviour import dom as domlib

    if (node.attrs.get("aria-hidden") or "").lower() == "true":
        return False
    if node.tag == "dialog" and "open" not in node.attrs:
        return False
    return domlib._visible(node)


def _sel(node) -> str:
    from labctl import navmap

    return navmap._frame_prefix(node) + navmap.canon(node)


def _top_ids(region) -> tuple[str, ...]:
    """The ids of the components directly inside a region (not inside another one with an id),
    generated ones (`j_idtN`) and scripts left out: what tells one sub-page from another."""
    out: list[str] = []

    def walk(node):
        for child in node.children:
            if child.tag in ("script", "style", "tl-frame"):
                continue
            if child.id:
                if not GENERATED.search(child.id):
                    out.append(child.id)
                continue
            walk(child)

    walk(region)
    return tuple(sorted(set(out)))


def _title_in(node) -> str:
    from labctl.behaviour import dom as domlib

    head = next((n for n in domlib._walk(node) if HEADING.fullmatch(n.tag)), None)
    return _text(head) if head is not None else ""


def states(raw, safe, regions: set[str]) -> dict[str, dict]:
    """key -> state of every view part a snapshot shows: `kind`, `selector`, `value` (what tells
    one state from another), `title` (interface text, from the sanitised tree), `toggle` (a
    dialog, a panel, a row: shown or not) and, for a dialog-framework page, `shows`."""
    from labctl import sanitize
    from labctl.behaviour import dom as domlib

    safe_by_id = {n.id: n for n in domlib._walk(safe) if n.id}

    def title(node, fallback: str = "") -> str:
        s = safe_by_id.get(node.id) if node.id else None
        return (_text(s) if s is not None else "") or fallback

    out: dict[str, dict] = {}
    for node in domlib._walk(raw):
        if any(a.tag == "tl-frame" for a in _ancestors(node)):
            continue  # a frame's document is its own screen
        cls = set(node.classes)
        role = (node.attrs.get("role") or "").lower()
        if not node.id:
            continue
        sel = _sel(node)
        # dialogs: <dialog>, role=dialog, PrimeFaces' div.ui-dialog (whose role it empties when
        # hidden); a dialog holding a frame is a dialog-framework page
        if node.tag == "dialog" or "ui-dialog" in cls or role in ("dialog", "alertdialog"):
            if node.id.endswith("_modal"):
                continue
            frame = next((n for n in domlib._walk(node) if n.tag == "iframe"), None)
            head = next((n for n in domlib._walk(node) if "ui-dialog-title" in n.classes), None)
            name = title(head) if head is not None and head.id else ""
            if not name:
                s = safe_by_id.get(node.id)
                name = _title_in(s) if s is not None else ""
                if not name and head is not None and s is not None:
                    hs = next((n for n in domlib._walk(s) if "ui-dialog-title" in n.classes), None)
                    name = _text(hs) if hs is not None else ""
            row = {"kind": "dialog-framework page (iframe)" if frame is not None else
                   "confirm dialog" if "ui-confirm-dialog" in cls else "dialog",
                   "selector": sel, "value": _shown(node), "toggle": True, "title": name}  # fmt: skip
            if frame is not None:
                row["shows"] = sanitize.route_pattern(str(frame.attrs.get("src") or "/"))
            out[f"dialog {sel}"] = row
        elif "ui-wizard" in cls:
            steps = [n for n in domlib._walk(node) if "ui-wizard-step-title" in n.classes]
            cur = next((i for i, n in enumerate(steps) if "ui-state-highlight" in n.classes), None)
            if cur is not None:
                s = safe_by_id.get(node.id)
                ss = (
                    [n for n in domlib._walk(s) if "ui-wizard-step-title" in n.classes] if s else []
                )
                name = _text(ss[cur]) if cur < len(ss) else f"step {cur + 1}"
                out[f"wizard {sel}"] = {"kind": "wizard step", "selector": sel, "value": cur,
                                        "title": name, "step": f"{cur + 1} of {len(steps)}"}  # fmt: skip
        elif role == "tablist" or "ui-tabs" in cls:
            accordion = "ui-accordion" in cls
            tabs = [n for n in domlib._walk(node) if (n.attrs.get("role") or "").lower() == "tab"]
            if not tabs:
                continue
            s = safe_by_id.get(node.id)
            stabs = ([n for n in domlib._walk(s) if (n.attrs.get("role") or "").lower() == "tab"]
                     if s is not None else [])  # fmt: skip
            for i, t in enumerate(tabs):
                name = _text(stabs[i]) if i < len(stabs) else f"tab {i + 1}"
                if accordion:
                    shown = (t.attrs.get("aria-expanded") or "").lower() == "true"
                    out[f"panel {sel} {i}"] = {"kind": "accordion panel", "selector": sel,
                                               "value": shown, "toggle": True, "title": name,
                                               "tab": _sel(t) if t.id else None}  # fmt: skip
                elif (t.attrs.get("aria-selected") or "").lower() == "true":
                    out[f"tabs {sel}"] = {"kind": "tab", "selector": sel, "value": i,
                                          "title": name}  # fmt: skip
        elif "ui-datatable" in cls:
            out.update(_table(node, sel, safe_by_id))
        elif node.id in regions:
            ids = _top_ids(node)
            s = safe_by_id.get(node.id)
            out[f"region {sel}"] = {"kind": "sub-page (ajax region)", "selector": sel,
                                    "value": ids, "title": _title_in(s) if s is not None else "",
                                    "components": list(ids)}  # fmt: skip
    return out


def _table(node, sel: str, safe_by_id: dict) -> dict[str, dict]:
    from labctl.behaviour import dom as domlib

    out: dict[str, dict] = {}
    walk = list(domlib._walk(node))
    pages = [n for n in walk if "ui-paginator-page" in n.classes]
    active = next((n for n in pages if "ui-state-active" in n.classes), None)
    if active is not None:
        label = active.attrs.get("aria-label") or f"Page {_text(active)}"
        out[f"table-page {sel}"] = {"kind": "table page", "selector": sel,
                                    "value": label, "title": label}  # fmt: skip
    out[f"table-rows {sel}"] = {"kind": "table result (filter)", "selector": sel,
                                "value": len(pages), "title": f"{len(pages)} page(s) of rows"}  # fmt: skip
    order = "unsorted"
    for th in walk:
        sort = (th.attrs.get("aria-sort") or "").lower()
        if th.tag == "th" and sort in ("ascending", "descending"):
            s = safe_by_id.get(th.id) if th.id else None
            head = next((n for n in domlib._walk(s) if "ui-column-title" in n.classes), None) \
                if s is not None else None  # fmt: skip
            order = f"sorted by {_text(head) if head is not None else th.id} {sort}"
            break
    out[f"table-sort {sel}"] = {"kind": "table sort", "selector": sel, "value": order,
                                "title": order}  # fmt: skip
    expanded = any("ui-expanded-row-content" in n.classes for n in walk)
    out[f"table-expand {sel}"] = {"kind": "table row expansion", "selector": sel,
                                  "value": expanded, "toggle": True, "title": "a row expanded"}  # fmt: skip
    chosen = any(n.tag == "tr" and (n.attrs.get("aria-selected") or "") == "true" for n in walk)
    out[f"table-select {sel}"] = {"kind": "table row selection", "selector": sel,
                                  "value": chosen, "toggle": True, "title": "a row selected"}  # fmt: skip
    return out


def _ancestors(node):
    cur = node.parent
    while cur is not None:
        yield cur
        cur = cur.parent


def regions_of(calls: dict[int, list[dict]]) -> set[str]:
    """Client ids that a partial response replaced with a sub-page: new HTML with a heading and
    more than one component of its own (a dynamic `ui:include`, an `outputPanel` swapped)."""
    return {u["id"] for rows in calls.values() for r in rows for u in r.get("updates") or []
            if u.get("heading") and u.get("components", 0) >= 2}  # fmt: skip


# --- per screen ----------------------------------------------------------------------------------


_CACHE: dict[tuple, dict[int, list[dict]]] = {}


def calls_of(part) -> dict[int, list[dict]]:
    """The partial requests of a recording part (`seedplan.Part`), by the step that caused them."""
    from labctl import har

    path = Path(part.path)
    hfile = path / "recording.har"
    key = (str(path), hfile.stat().st_mtime if hfile.is_file() else 0)
    if key not in _CACHE:
        try:
            doc = har.load(path).har
        except (OSError, ValueError):
            doc = {}
        _CACHE[key] = partials(doc, part.raw)
    return _CACHE[key]


def subviews(ctx, screen: str, pages) -> list[dict]:
    """Every state the recording reached inside the JSF screen `screen` (see `states`), in the
    order first reached, each with the steps that opened (and closed) it, the regions those
    actions re-rendered and the snapshot that shows it (`_html`, written by `write_snapshots`).
    [] for a screen that is not a JSF view."""
    from labctl import sanitize

    snaps = [x for x in pages.of(screen) if x[1] == "after"]
    part = ctx.part
    if not snaps or not any(is_jsf(part.doms.get(seq)) for seq, *_ in snaps):
        return []
    calls = calls_of(part)
    regions = regions_of(calls)
    loads = {s["seq"]: s for s in part.raw if s.get("action") == "goto"}
    rows: dict[tuple, dict] = {}
    prev: dict[str, dict] = {}
    order: list[tuple] = []
    for seq, _when, _screen, raw, safe in snaps:
        if seq in loads:
            prev = {}
        now = states(raw, safe, regions)
        root = ctx.root_step(seq) or {}
        cause = None
        if root.get("action") != "goto" or root.get("caused_by"):
            cause = {"step": root.get("n", seq), "action": root.get("action"),
                     "selector": root.get("selector")}  # fmt: skip
        for key, st in now.items():
            before = prev.get(key)
            if before is not None and before["value"] == st["value"]:
                continue
            if st.get("toggle"):
                if not st["value"]:
                    if before is not None and before["value"]:
                        row = rows.get((key, True))
                        if row is not None and cause:
                            _add(row["closed_by"], cause)
                    continue
                ident = (key, True)
            else:
                ident = (key, json.dumps(st["value"]))
            row = rows.get(ident)
            if row is not None and before is None:
                continue  # shown again with what holds it (the table back on its sub-page)
            if row is None:
                row = rows[ident] = {
                    "kind": st["kind"], "selector": st["selector"],
                    "title": st.get("title") or "", "state_of": screen,
                    "first_seen": f"after step {root.get('n', seq)}" if cause else "when the page loads",
                    "opened_by": [], "closed_by": [], "_seq": seq}  # fmt: skip
                for extra in ("shows", "step", "components", "tab"):
                    if st.get(extra):
                        row[extra] = st[extra]
                order.append(ident)
            if cause:
                _add(row["opened_by"], cause)
                done = calls.get(cause["step"]) or calls.get(seq) or []
                upd = [u["id"] for r in done for u in r.get("updates") or []]
                if upd:
                    row.setdefault("rerendered", [])
                    row["rerendered"] += [u for u in upd if u not in row["rerendered"]]
        for key, st in prev.items():  # a dialog or panel taken out of the page closed too
            if key not in now and st.get("toggle") and st["value"] and cause:
                row = rows.get((key, True))
                if row is not None:
                    _add(row["closed_by"], cause)
        prev = now
    out = [rows[i] for i in order]
    # one value-free snapshot per state, sanitised like the screen's own page file
    infos = [sanitize.page(part.doms[seq], ctx.known, ctx.vocab) for seq, *_ in snaps]
    vary = sanitize.varying(infos)
    for row in out:
        row["_html"] = sanitize.page(part.doms[row.pop("_seq")], ctx.known, ctx.vocab, vary).html
    return out


def _add(side: list[dict], who: dict) -> None:
    have = next((w for w in side if w["selector"] == who["selector"]
                 and w["action"] == who["action"]), None)  # fmt: skip
    if have is None:
        side.append(dict(who))
    elif who["step"] != have["step"] and who["step"] not in have.get("also_steps", []):
        have.setdefault("also_steps", []).append(who["step"])


def merge(generic: list[dict], jsf: list[dict], frames: list[dict] = ()) -> list[dict]:
    """A screen's sub-views: the JSF states replace the generic dialogs and tab panels (read by
    role and inline style, which PrimeFaces' hidden dialogs and panels do not show); a frame a
    dialog-framework page showed takes that page's opener and closer."""
    frames = [dict(f) for f in frames]
    if not jsf:
        return [*generic, *frames]
    for f in frames:
        page = next((v for v in jsf if v.get("shows") and v["shows"] == f.get("shows")), None)
        if page is not None:
            f["opened_by"], f["closed_by"] = page["opened_by"], page["closed_by"]
    return [v for v in generic if v.get("kind") not in ("dialog", "tab panel")] + jsf + frames


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40] or "view"


def write_snapshots(root: Path, app: str, doc: dict) -> int:
    """Write each JSF state's snapshot as `apps/<app>/pages/<screen>--NN-<kind>-<title>.html`
    and name it in the state (`snapshot`). Returns how many were written."""
    pages = Path(root) / "apps" / app / "pages"
    for old in pages.glob("*--*.html") if pages.is_dir() else []:
        old.unlink()
    n = 0
    for screen in doc.get("screens") or []:
        base = Path(str(screen.get("page") or "")).name.removesuffix(".html") or _slug(
            screen["path"]
        )
        k = 0
        for v in screen.get("subviews") or []:
            html = v.pop("_html", None)
            if html is None:
                continue
            k += 1
            name = f"{base}--{k:02d}-{_slug(v['kind'])}-{_slug(v.get('title') or '')}.html"
            pages.mkdir(parents=True, exist_ok=True)
            note = (f"<!-- {screen['path']}: {v['kind']} `{v['selector']}`"
                    + (f' "{v["title"]}"' if v.get("title") else "")
                    + ": value-free snapshot of the state (data masked as in the screen's page "
                    "file). -->\n")  # fmt: skip
            (pages / name).write_text(note + html, encoding="utf-8", newline="\n")
            v["snapshot"] = f"apps/{app}/pages/{name}"
            n += 1
    return n


def annotate(doc: dict, ctxs: list) -> None:
    """What the JSF views add to a navigation map: per element use the partial requests it sent
    (`jsf_ajax`: component, event, execute, render, regions updated, scripts run) and the states
    it opened or closed; per transition whether it was a forward (the address bar kept the page
    before); a `jsf` section with the views, the address lag and every partial action."""
    lag: list[dict] = []
    actions: dict[tuple, dict] = {}
    ignored: Counter = Counter()
    by_step: dict[tuple[str, int], list[dict]] = {}
    for c in ctxs:
        if not any(is_jsf(h) for h in c.part.doms.values()):
            continue  # not a JSF application: nothing to add
        views = step_views(c.part.raw, c.part.doms)
        lag += [{"recording": c.name, **x} if len(ctxs) > 1 else x
                for x in address_lag(c.part.raw, views)]  # fmt: skip
        for seq, rows in calls_of(c.part).items():
            root = c.root_step(seq) or {}
            by_step[(c.name, root.get("n", seq))] = (
                by_step.get((c.name, root.get("n", seq)), []) + rows
            )
            for r in rows:
                ignored.update(r.get("ignored") or [])
                key = (r.get("request"), r.get("source"), r.get("event"))
                a = actions.setdefault(key, {k: r[k] for k in ("request", "source", "event")
                                             if r.get(k)} | {"steps": [], "updates": []})  # fmt: skip
                a["steps"].append(root.get("n", seq))
                for u in r.get("updates") or []:
                    if u["id"] not in a["updates"]:
                        a["updates"].append(u["id"])
                for k in (
                    "execute",
                    "render",
                    "evals",
                    "redirect",
                    "error",
                    "callback_args",
                    "validation_failed",
                ):
                    if r.get(k) and k not in a:
                        a[k] = r[k]
    if not (lag or actions):
        return
    first = ctxs[0].name if ctxs else ""
    for screen in doc.get("screens") or []:
        states = [v for v in screen.get("subviews") or [] if v.get("state_of")]
        for el in screen.get("elements") or []:
            for use in el["used"] if isinstance(el.get("used"), list) else []:
                rows = by_step.get((use.get("recording") or first, use["step"]))
                if rows:
                    use["jsf_ajax"] = [{k: r[k] for k in ("source", "event", "updates", "evals",
                                                          "redirect", "error", "validation_failed")
                                        if r.get(k)} for r in rows]  # fmt: skip
                    for x in use["jsf_ajax"]:
                        if x.get("updates"):
                            x["updates"] = [u["id"] for u in x["updates"]]
                for v in states:
                    label = f"{v['kind']} `{v['selector']}`" + (
                        f' "{v["title"]}"' if v.get("title") else ""
                    )
                    for side, what in (("opened_by", "opens"), ("closed_by", "closes")):
                        if any(w["step"] == use["step"] or use["step"] in w.get("also_steps", [])
                               for w in v.get(side) or []):  # fmt: skip
                            if label not in use.get(what, []):
                                use.setdefault(what, []).append(label)
    forwards = {(x["step"]): x for x in lag}
    for t in doc.get("transitions") or []:
        hit = next((forwards[s] for s in t.get("steps") or [] if s in forwards), None)
        if hit is None:  # the page load the action caused carries the address
            hit = next((x for x in lag for s in t.get("steps") or []
                        if any(st.get("caused_by") == s and st.get("seq") == x["step"]
                               for c in ctxs for st in c.part.raw)), None)  # fmt: skip
        if hit is not None and hit["view"] == t.get("to"):
            t["forward"] = True
            t["address_after"] = hit["address"]
    doc["jsf"] = {
        "about": "JavaServer Faces: the screen of a step is the view its page shows (the address "
                 "its JSF forms post to), not the address bar. `address_lag`: page loads after "
                 "which the address showed another page than the view: a postback answered with "
                 "the next view (a forward, no redirect). Build it the same way: the form posts to "
                 "its own view's address and the answer is the next view, with no redirect, so the "
                 "address stays. `partial_actions`: every ajax request and what its partial "
                 "response re-rendered (client ids), the scripts it ran, a redirect or error; "
                 "`ignored`: what the partial responses also carried that a rebuilt application "
                 "makes itself (the view state, head resources).",
        "address_lag": lag,
        "partial_actions": list(actions.values()),
        "ignored": [{"update": k, "times": n} for k, n in sorted(ignored.items())],
    }  # fmt: skip


# --- PrimeFaces widgets (for the code map) --------------------------------------------------------


def _options(text: str, start: int) -> tuple[str, list[str]]:
    """The client id and the option names of a `PrimeFaces.cw(...)` call's options object."""
    depth, i = 1, start
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    body = text[start : i - 1]
    cid = re.search(r"""\bid\s*:\s*["']([^"']+)["']""", body)
    names: list[str] = []
    depth = 0
    for m in re.finditer(r"[{}]|(?:^|,)\s*([A-Za-z_$][\w$]*)\s*:", body):
        tok = m.group(0)
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
        elif depth == 0 and m.group(1) not in ("id", "widgetVar") and m.group(1) not in names:
            names.append(m.group(1))
    return (cid.group(1) if cid else ""), names[:30]


def widgets(recordings: list[Path]) -> list[dict]:
    """The PrimeFaces widgets the recordings' pages and partial responses create: type, widget
    variable, client id, the option names (never their values) and the pages they were on."""
    from labctl import har, sanitize

    found: dict[tuple, dict] = {}
    for rec in recordings:
        try:
            doc = har.load(Path(rec)).har
        except (OSError, ValueError):
            continue
        for entry in (doc.get("log") or {}).get("entries") or []:
            req, resp = entry.get("request") or {}, entry.get("response") or {}
            text = (resp.get("content") or {}).get("text")
            if not isinstance(text, str) or "PrimeFaces.cw" not in text:
                continue
            page = sanitize.route_pattern(urlsplit(str(req.get("url") or "")).path or "/")
            how = "partial response" if PARTIAL.search(text) else "page"
            for m in CW.finditer(_cdata(text)):
                cid, names = _options(_cdata(text), m.end())
                key = (m.group(1), cid)
                w = found.setdefault(key, {"type": m.group(1), "widget_var": m.group(2),
                                           "id": cid, "options": [], "pages": [], "via": []})  # fmt: skip
                w["options"] += [n for n in names if n not in w["options"]]
                if page not in w["pages"]:
                    w["pages"].append(page)
                if how not in w["via"]:
                    w["via"].append(how)
    return sorted(found.values(), key=lambda w: (w["pages"][0] if w["pages"] else "", w["id"]))


# --- the page documents ------------------------------------------------------------------------


def doc_sections(subs: list[dict]) -> list[str]:
    """One section per JSF state of a screen, for its page document."""
    out: list[str] = []
    for v in subs:
        if not v.get("state_of"):
            continue
        name = v["kind"] + (f': "{v["title"]}"' if v.get("title") else "")
        out += [f"### {name}", ""]
        out.append(
            f"- Element: `{v['selector']}`" + (f" (step {v['step']})" if v.get("step") else "")
        )
        if v.get("shows"):
            out.append(f"- Shows the page `{v['shows']}` in a frame inside the dialog (its own "
                       "screen in this package).")  # fmt: skip

        def ways(items) -> str:
            return "; ".join(f"{w['action']} `{w['selector']}` (step {w['step']}"
                             + (", also " + ", ".join(str(x) for x in w["also_steps"])
                                if w.get("also_steps") else "") + ")" for w in items)  # fmt: skip

        out.append("- Opened by: " + (ways(v["opened_by"]) or f"nothing: {v['first_seen']}"))
        if v.get("closed_by"):
            out.append("- Closed by: " + ways(v["closed_by"]))
        if v.get("rerendered"):
            out.append("- The action re-rendered (partial response): "
                       + ", ".join(f"`{u}`" for u in v["rerendered"]))  # fmt: skip
        if v.get("components"):
            out.append("- Holds: " + ", ".join(f"`{c}`" for c in v["components"][:12]))
        if v.get("snapshot"):
            out.append(f"- Snapshot of this state: [`{v['snapshot']}`]({v['snapshot']})")
        out.append("")
    return out
