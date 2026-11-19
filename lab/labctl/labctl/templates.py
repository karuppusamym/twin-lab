"""Screens as templates: one structure, many instances, and what is shared by all of them.

A shop with thousands of product pages records many screens with the same structure and
different data. A package must describe each such screen once, as a template, and say how many
instances were seen, which parameters vary, and what data each instance shows; nothing in it
may grow with the number of instances where it should grow with the number of templates.

- **Learning templates** (`rules_for`): an address segment without digits is a route name for
  `labctl.sanitize.route_pattern` (`/p/blue-linen-shirt` stays as it is), so every product with
  a slug would be a screen of its own. Here, before the brief is generated, the addresses the
  recording visited are grouped: addresses equal but in one segment, at least `MIN_INSTANCES`
  different values there, and pages of the same structure (tags, ids and classes, outside the
  layout every page shares) become one template, `/p/{slug}`. While the brief is generated
  (`labctl.factory.generate`), `sanitize.route_pattern` applies the learned rules, so the
  screens, page snapshots, navigation map, steps, API patterns and page documents are per
  template.
- **The shared layout** (`extend_navigation`): the header, menus, mega-menu and footer every
  screen has are listed once in `navigation.json` (`shared_layout`), and each screen's element
  that belongs to them says so (`layout`), so a builder builds them once.
- **Per template** (`apps/<app>/templates.json`): instances and visits, the path and query
  parameters that vary (with their role: search, filter, sort, page), the data fields (an
  element whose text differs between instances; one whose text is the same is the interface's)
  and lists, the widgets found in the page (breadcrumbs, image gallery, variant picker with the
  API it calls and what it changes, recommendation or personalisation blocks, search facets as
  code lists, infinite scroll or "load more" with its cursor, category tree), and one seed row
  per instance seen with synthetic values of the recorded shapes.
- **Per application**: the navigation hierarchy (category tree from the menus, levels from the
  breadcrumbs) and the cart and wish-list state (endpoints, the badge, where the state lives).

Privacy: instance data is never carried. Each seed row holds synthetic values of the same shape
(`labctl.apicatalog.World`); a field the owner declares public data could be carried as
recorded, through `reveal` (none is declared today: the declaration is a separate piece of
work), and the row says which. Labels come from the sanitised pages (`sanitize.page`), as in
`navigation.json`; the file is checked against the recording (`labctl.apicatalog.leaks`) and for
secrets and hosts (`labctl.codemap.check_text`) before it is written.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from labctl import jsfviews, sanitize

FILE = "templates.json"  # apps/<app>/templates.json
FORMAT = "twinlab-templates/1"
MIN_INSTANCES = 3  # addresses of one shape before a segment is a template parameter
MIN_LINKS = 5  # links of one repeated element, when fewer than two such pages were visited
MEGA_LINKS = 6  # links of a nested menu in the shared layout before it is a mega-menu
SIMILAR = 0.7  # median Jaccard similarity of the page structures (outside the shared layout)
SHARED = 0.8  # share of the screens a region must be on to be the shared layout
MAX_FIELDS = 40
REGION = re.compile(
    r"(^|[-_])(header|footer|masthead|navbar|nav|menu|topbar|sidebar)($|[-_])", re.I
)
GALLERY = re.compile(r"gallery|carousel|slider|swiper|thumbs?|media|images?-?viewer|zoom", re.I)
VARIANT = re.compile(r"variant|swatch|colou?r|size|option|picker|flavou?r|style|fit", re.I)
RECOMMEND = re.compile(
    r"recommend|also[-_ ]?(viewed|bought|like)|similar|related|for[-_ ]?you|trending|"
    r"sponsored|recently[-_ ]?viewed|personali[sz]|inspired|bestseller|popular|you[-_ ]?may",
    re.I,
)
FACETS = re.compile(r"facet|filter|refine|refinement|narrow", re.I)
BREADCRUMB = re.compile(r"breadcrumb", re.I)
BADGE = re.compile(r"cart|basket|bag|trolley|wish", re.I)
STATE_ROLES = ("cart state", "wish list state")


# --- structure ------------------------------------------------------------------------------------


def _tokens(html: str) -> set[str]:
    """The structure of a page: tag, id and classes of every element, digits masked."""
    from labctl.behaviour import dom as domlib

    out = set()
    for node in domlib._walk(domlib.parse_html(html)):
        if node.tag in ("script", "style", "path", "g", "svg", "#document"):
            continue
        ident = re.sub(r"\d+", "9", node.id or "")
        cls = ".".join(re.sub(r"\d+", "9", c) for c in node.classes[:4])
        out.add(f"{node.tag}#{ident}.{cls}")
    return out


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))


def _visited(recordings: list[Path]) -> dict[str, str]:
    """address path -> the page HTML (the last snapshot after a step, else the HAR document)."""
    from labctl import har

    pages: dict[str, str] = {}
    for rec in recordings:
        try:
            loaded = har.load(rec)
        except (OSError, ValueError):
            continue
        hosts = {urlsplit(s.url).netloc.lower() for s in loaded.steps if s.url.startswith("http")}
        for e in (loaded.har.get("log") or {}).get("entries") or []:
            req, resp = e.get("request") or {}, e.get("response") or {}
            url = urlsplit(str(req.get("url") or ""))
            content = resp.get("content") or {}
            mime = str(content.get("mimeType") or "")
            if (req.get("method") == "GET" and resp.get("status") == 200 and "html" in mime
                    and (not hosts or url.netloc.lower() in hosts)
                    and isinstance(content.get("text"), str)):  # fmt: skip
                pages.setdefault(url.path or "/", content["text"])
        for s in loaded.steps:
            if s.dom is not None and s.url.startswith("http"):
                try:
                    pages[urlsplit(s.url).path or "/"] = s.dom.read_text("utf-8", "replace")
                except OSError:
                    pass
    # A JSF view is a screen of its own (labctl.jsfviews), whatever the address bar showed, and
    # its sub-pages are sub-views of it: no JSF page is a template's instance.
    return {p: h for p, h in pages.items() if not jsfviews.is_jsf(h)}


def _page_links(html: str) -> list[tuple[str, str]]:
    """(selector, address path) of every link of a page to its own site."""
    from labctl import navmap
    from labctl.behaviour import dom as domlib

    out = []
    for node in domlib._walk(domlib.parse_html(html)):
        href = node.attrs.get("href") if node.tag == "a" else None
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        parts = urlsplit(href)
        if parts.netloc or not (parts.path or "").startswith("/"):
            continue
        out.append((navmap.canon(node), parts.path))
    return out


def _shape_key(path: str, i: int) -> tuple | None:
    """The address with segment `i` left out and its values written `{}`, or None."""
    segs = path.split("/")
    if i >= len(segs) or not segs[i]:
        return None
    with sanitize.template_rules([]):
        base = sanitize.route_pattern(path).split("/")
    if len(base) != len(segs):
        return None
    return (len(segs), i, tuple("{}" if s.startswith("{") else s
                                for j, s in enumerate(base) if j != i))  # fmt: skip


def _rule(rest: tuple, i: int) -> tuple | None:
    """The rule for a template varying at segment `i`; None when the rest has no literal name
    (`/{slug}` alone would make every one-segment address one screen)."""
    if not any(s and s != "{}" for s in rest):
        return None
    rule = list(rest)
    rule.insert(i, "*")
    return tuple(rule)


def learn(pages: dict[str, str]) -> list[dict]:
    """The templates the visited addresses show: [{"rule": (...), "pattern", "instances",
    "similarity" | "links"}]. `pages`: address path -> its HTML.

    A template: addresses equal but in one segment (the rest holds a literal name), at least
    `MIN_INSTANCES` values there (addresses visited, or linked from the visited pages), and
    either two visited ones of the same structure outside the shared layout, or, when fewer were
    visited, one repeated link of the pages (the same selector) leading to `MIN_LINKS` of them."""
    with sanitize.template_rules([]):
        bases = {p: sanitize.route_pattern(p) for p in pages}
    tokens = {p: _tokens(h) for p, h in pages.items()}
    # what every screen has (the layout) does not tell two screens apart
    by_screen: dict[str, set] = {}
    for p, b in bases.items():
        by_screen.setdefault(b, tokens[p])
    counts = Counter(t for ts in by_screen.values() for t in ts)
    layout = {t for t, n in counts.items() if len(by_screen) >= 3 and n >= SHARED * len(by_screen)}
    visited: dict[tuple, dict[str, str]] = defaultdict(dict)  # key -> value -> visited path
    linked: dict[tuple, set[str]] = defaultdict(set)
    families: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for p in pages:
        for i in range(1, len(p.split("/"))):
            key = _shape_key(p, i)
            if key:
                visited[key].setdefault(p.split("/")[i], p)
    for html in pages.values():
        for sel, target in _page_links(html):
            for i in range(1, len(target.split("/"))):
                key = _shape_key(target, i)
                if key:
                    value = target.split("/")[i]
                    linked[key].add(value)
                    if sel != sel.split(".")[0].split("#")[0].split("[")[0]:  # not a bare tag
                        families[key][sel].add(value)
    found = []
    keys = sorted(set(visited) | set(families), key=lambda k: (-len(visited.get(k, {})), k))
    for key in keys:
        n, i, rest = key
        rule = _rule(rest, i)
        if rule is None or any(rule == f["rule"] for f in found):
            continue
        members = visited.get(key, {})
        values = set(members) | linked.get(key, set())
        literal = [v for v in values if not sanitize.is_id_segment(v)]
        if len(values) < MIN_INSTANCES or len(literal) < 2:
            continue
        row: dict = {"rule": rule, "instances": len(values), "visited": len(members)}
        if len(members) >= 2:
            sample = sorted(members.values())[:12]
            sims = [_jaccard(tokens[a] - layout, tokens[b] - layout)
                    for k, a in enumerate(sample) for b in sample[k + 1 :]]  # fmt: skip
            sim = statistics.median(sims) if sims else 0.0
            if sim < SIMILAR:
                continue
            row["similarity"] = round(sim, 2)
        else:
            fam = max((len(v), sel) for sel, v in families.get(key, {}).items()) if families.get(
                key) else (0, None)  # fmt: skip
            if fam[0] < MIN_LINKS:
                continue
            row["links"] = f"{fam[0]} addresses linked from `{fam[1]}`"
        with sanitize.template_rules([rule]):
            row["pattern"] = sanitize.route_pattern("/".join(
                "x" if s == "*" else "1" if s == "{}" else s for s in rule))  # fmt: skip
        found.append(row)
    return found


def rules_for(root: Path, app_id: str, recordings: list[Path] | None = None) -> list[dict]:
    """The templates of an application's recordings (see `learn`)."""
    if recordings is None:
        from labctl import factory

        try:
            recordings = factory.discover(Path(root), app_id).recordings
        except (OSError, ValueError):
            recordings = []
    pages = _visited(list(recordings))
    return learn(pages) if len(pages) >= 2 else []


# --- the shared layout ----------------------------------------------------------------------------


def _region_kind(node) -> str | None:
    role = (node.attrs.get("role") or "").lower()
    ident = " ".join([node.id or "", *node.classes])
    if node.tag == "header" or role == "banner":
        return "header"
    if node.tag == "footer" or role == "contentinfo":
        return "footer"
    if node.tag == "nav" or role in ("navigation", "menubar"):
        return "menu"
    if node.tag in ("div", "section", "aside", "ul") and REGION.search(ident):
        m = REGION.search(ident).group(2).lower()
        return {"masthead": "header", "topbar": "header", "navbar": "menu", "nav": "menu"}.get(m, m)
    return None


def _regions(tree) -> list:
    """Top-level layout regions of a page: not inside another region."""
    from labctl.behaviour import dom as domlib
    from labctl.navmap import _ancestors

    out = []
    for node in domlib._walk(tree):
        kind = _region_kind(node)
        label = " ".join([node.attrs.get("aria-label") or "", *node.classes, node.id or ""])
        if kind and BREADCRUMB.search(label):
            continue  # breadcrumbs belong to each screen
        if kind and not any(_region_kind(a) for a in _ancestors(node)):
            out.append((kind, node))
    return out


def _menu_tree(node, depth: int = 0) -> list[dict]:
    """A nested menu as a tree of {label, to, children}."""
    from labctl.behaviour import dom as domlib
    from labctl.navmap import _href

    items = []
    for li in [c for c in _children(node, "li")]:
        link = next((x for x in domlib._walk(li) if x.tag == "a"), None)
        if link is None:
            continue
        sub = next((x for x in domlib._walk(li) if x.tag in ("ul", "ol") and x is not node), None)
        item = {"label": domlib._text(link)[:60], "to": _href(link)}
        if sub is not None and depth < 4:
            kids = _menu_tree(sub, depth + 1)
            if kids:
                item["children"] = kids
        items.append(item)
    return items


def _children(node, tag: str) -> list:
    """The `tag` elements directly under the first list of `node`."""
    lst = node if node.tag in ("ul", "ol") else next(
        (x for x in _walk_list(node) if x.tag in ("ul", "ol")), None)  # fmt: skip
    return [c for c in (lst.children if lst is not None else []) if c.tag == tag]


def _walk_list(node):
    from labctl.behaviour import dom as domlib

    return domlib._walk(node)


def _depth(tree: list[dict]) -> int:
    return 1 + max((_depth(i["children"]) for i in tree if i.get("children")), default=0)


def _links(node) -> int:
    from labctl.behaviour import dom as domlib

    return sum(1 for x in domlib._walk(node) if x.tag == "a" and x.attrs.get("href") is not None)


def screen_of(step: dict, views: dict[int, str]) -> str:
    """The screen a step was on: its JSF view when it has one (labctl.jsfviews), else the
    pattern of its address, as `navmap` and `stepfacts` say."""
    return views.get(step.get("seq")) or sanitize.route_pattern(
        urlsplit(str(step.get("url") or "")).path or "/")  # fmt: skip


def _representatives(ctxs: list) -> dict[str, tuple[str, str]]:
    """screen pattern -> (raw HTML, sanitised HTML) of its fullest snapshot."""
    best: dict[str, tuple[int, str, object]] = {}
    for c in ctxs:
        views = jsfviews.step_views(c.part.raw, c.part.doms)
        for s in c.part.raw:
            url = str(s.get("url") or "")
            if not url.startswith("http") or s.get("seq") not in c.part.doms:
                continue
            screen = screen_of(s, views)
            html = c.part.doms[s["seq"]]
            if screen not in best or len(html) > best[screen][0]:
                best[screen] = (len(html), html, c)
    out = {}
    for screen, (_, html, c) in best.items():
        out[screen] = (html, sanitize.page(html, c.known, c.vocab).html)
    return out


def shared_layout(reps: dict[str, tuple[str, str]], screens: list[str]) -> dict | None:
    from labctl import navmap
    from labctl.behaviour import dom as domlib

    screens = [s for s in screens if s in reps]
    if len(screens) < 2:
        return None
    seen: dict[str, dict] = {}
    for screen in screens:
        safe = domlib.parse_html(reps[screen][1])
        for kind, node in _regions(safe):
            sel = navmap.canon(node)
            r = seen.setdefault(
                sel, {"selector": sel, "kind": kind, "screens": set(), "_node": node}
            )
            r["screens"].add(screen)
    need = max(2, SHARED * len(screens))
    regions = [r for r in seen.values() if len(r["screens"]) >= need]
    if not regions:
        return None
    out_regions = []
    elements: list[str] = []
    for r in regions:
        node = r.pop("_node")
        els = [navmap.canon(x) for x in domlib._walk(node) if navmap._is_interactive(x)]
        row = {"selector": r["selector"], "kind": r["kind"],
               "on_screens": f"{len(r['screens'])} of {len(screens)}",
               "interactive_elements": len(els), "links": _links(node)}  # fmt: skip
        for menu in [x for x in domlib._walk(node) if x.tag == "nav"] + [node]:
            tree = _menu_tree(menu)
            if tree and _depth(tree) >= 2 and _links(menu) >= MEGA_LINKS:
                row["mega_menu"] = {"selector": navmap.canon(menu), "top_items": len(tree),
                                    "depth": _depth(tree), "links": _links(menu), "tree": tree}  # fmt: skip
                break
        out_regions.append(row)
        elements += [e for e in els if e not in elements]
    return {"rule": "Build these regions once, as the layout every screen uses; a screen's "
                    "element marked `layout` is one of them and behaves the same everywhere.",
            "regions": out_regions, "elements": elements}  # fmt: skip


def extend_navigation(doc: dict, ctxs: list) -> dict:
    """Add the shared layout to a navigation map (`labctl.navmap.write(extend=...)`) and mark
    each screen's element that belongs to it."""
    if not doc or not ctxs:
        return doc
    reps = _representatives(ctxs)
    screens = [s["path"] for s in doc.get("screens") or [] if s.get("in_scope")
               and not str(s.get("kind") or "").startswith(("shown in", "stand-in"))]  # fmt: skip
    layout = shared_layout(reps, screens)
    if not layout:
        return doc
    doc["shared_layout"] = layout
    from labctl import navmap
    from labctl.behaviour import dom as domlib

    for screen in doc.get("screens") or []:
        rep = reps.get(screen.get("path"))
        if rep is None:
            continue
        where: dict[str, str] = {}
        for _, node in _regions(domlib.parse_html(rep[1])):
            sel = navmap.canon(node)
            if any(r["selector"] == sel for r in layout["regions"]):
                for x in domlib._walk(node):
                    if navmap._is_interactive(x):
                        where.setdefault(navmap.canon(x), sel)
        for el in screen.get("elements") or []:
            if el.get("selector") in where:
                el["layout"] = where[el["selector"]]
    return doc


# --- the templates -------------------------------------------------------------------------------


def _own_text(node) -> str:
    from labctl.behaviour import dom as domlib

    return domlib._text(node)[:200]


def _field_rows(trees: list, layout_sel: set[str], vocab: set[str] = frozenset()
                ) -> tuple[list[dict], list[dict]]:  # fmt: skip
    """(data fields, lists) of one template from the raw trees of its instances (each as it
    loaded). A text that differs between instances is data, unless every word of it is the
    interface's own (a message the page shows after an action)."""
    from labctl import har, navmap
    from labctl.behaviour import dom as domlib
    from labctl.navmap import _ancestors

    per: dict[str, list] = defaultdict(list)
    counts: dict[str, list[int]] = defaultdict(list)
    for tree in trees:
        local: dict[str, list] = defaultdict(list)
        for node in domlib._walk(tree):
            if node.tag in ("script", "style", "head", "title", "meta", "link", "option"):
                continue
            if any(navmap.canon(a) in layout_sel for a in _ancestors(node)):
                continue
            if node.tag != "img" and any(c.tag not in ("b", "i", "em", "strong", "span", "br")
                                         for c in node.children):  # fmt: skip
                continue
            sel = navmap.canon(node)
            if sel == node.tag:
                continue
            value = (node.attrs.get("src") or "") if node.tag == "img" else _own_text(node)
            local[sel].append(value)
        for sel, vals in local.items():
            counts[sel].append(len(vals))
            per[sel].append(vals[0] if len(vals) == 1 else None)
    fields, lists = [], []
    for sel, vals in per.items():
        if max(counts[sel]) > 1:
            lists.append({"selector": sel, "per_instance": {"min": min(counts[sel]),
                                                             "max": max(counts[sel])}})  # fmt: skip
            continue
        real = [v for v in vals if v]
        if len(real) < 2 or len(set(real)) < 2:
            continue
        words = {w.lower() for v in real for w in re.findall(r"[^\W\d_]{2,}", v)}
        if words and words <= vocab and not sel.startswith("img"):
            continue
        kind = ("picture" if sel.startswith("img") else
                "amount" if all(re.fullmatch(r"[^\d]{0,3}\d[\d,]*[.,]\d{2}[^\d]{0,4}", v) for v in real)
                else "number" if all(re.fullmatch(r"\d+", v) for v in real) else "text")  # fmt: skip
        ident = re.split(r"[#.\[]", sel, maxsplit=1)[-1].strip('"]').split("=")[-1].strip('"')
        fields.append({"selector": sel, "column": har.snake(ident)[:40] or "value", "kind": kind,
                       "varies": f"{len(set(real))} different in {len(real)} instance(s)",
                       "_values": vals})  # fmt: skip
    return fields[:MAX_FIELDS], lists[:MAX_FIELDS]


def _sel(node) -> str:
    """The element's selector (`labctl.navmap.canon`), with its `aria-label` when it has no
    id, name or class."""
    from labctl import navmap

    sel = navmap.canon(node)
    if sel == node.tag and node.attrs.get("aria-label"):
        return f'{node.tag}[aria-label="{node.attrs["aria-label"][:40]}"]'
    return sel


def _widgets(raw, safe, screen: str, nav_screen: dict, endpoints: dict) -> list[dict]:
    from labctl import navmap
    from labctl.behaviour import dom as domlib

    out = []
    used = {e.get("selector"): e for e in nav_screen.get("elements") or []}

    def label_of(node) -> str:
        head = next((x for x in domlib._walk(node) if x.tag in ("legend", "h2", "h3", "h4",
                                                                  "label", "summary")), None)  # fmt: skip
        return (domlib._text(head) if head is not None else node.attrs.get("aria-label") or "")[:60]

    def calls_of(selectors: set[str]) -> list[str]:
        reqs = []
        for sel in selectors:
            for u in used.get(sel, {}).get("used") or [] if isinstance(
                    used.get(sel, {}).get("used"), list) else []:  # fmt: skip
                for r in u.get("requests") or []:
                    key = " ".join(r.split(" ")[:2])
                    if key not in reqs:
                        reqs.append(key)
        return reqs

    nodes = list(domlib._walk(safe))
    # breadcrumbs
    for node in nodes:
        ident = " ".join([node.id or "", *node.classes, node.attrs.get("aria-label") or "",
                          node.attrs.get("itemtype") or ""])  # fmt: skip
        if node.tag in ("nav", "ol", "ul", "div") and BREADCRUMB.search(ident):
            items = [x for x in domlib._walk(node) if x.tag == "li"]
            levels = []
            for li in items:
                a = next((x for x in domlib._walk(li) if x.tag == "a"), None)
                levels.append(navmap._href(a) if a is not None else "(this page)")
            out.append({"kind": "breadcrumbs", "selector": _sel(node),
                        "depth": len(items), "levels": levels})  # fmt: skip
            break
    # image gallery
    for node in nodes:
        ident = " ".join([node.id or "", *node.classes])
        imgs = [x for x in domlib._walk(node) if x.tag == "img"]
        if GALLERY.search(ident) and len(imgs) >= 2 and node.tag != "img":
            thumbs = [x for x in domlib._walk(node) if x.tag in ("button", "a", "li")
                      and any(y.tag == "img" for y in domlib._walk(x))]  # fmt: skip
            main = next((x for x in imgs if not any(x is y for t in thumbs
                                                    for y in domlib._walk(t))), imgs[0])  # fmt: skip
            sels = {_sel(t) for t in thumbs}
            row = {"kind": "image gallery", "selector": _sel(node),
                   "main_image": _sel(main), "thumbnails": len(thumbs),
                   "thumbnail": sorted(sels)[0] if sels else None}  # fmt: skip
            clicked = [s for s in sels if isinstance(used.get(s, {}).get("used"), list)]
            if clicked:
                row["used"] = (
                    "a thumbnail click was recorded: it shows that picture as the main one"
                )
            out.append(row)
            break
    # variant picker
    for node in nodes:
        ident = " ".join([node.id or "", *node.classes])
        if node.tag in ("div", "section", "form", "fieldset") and re.search(
                r"variant|swatch|picker|options", ident, re.I):  # fmt: skip
            dims = []
            groups = [x for x in domlib._walk(node) if x.tag in ("fieldset", "select")
                      or (x.attrs.get("role") or "") == "radiogroup"]  # fmt: skip
            for g in groups:
                if g.tag == "select":
                    opts = [o for o in domlib._walk(g) if o.tag == "option"]
                    dims.append(
                        {
                            "name": g.attrs.get("name") or _sel(g),
                            "control": _sel(g),
                            "options": len(opts),
                        }
                    )
                else:
                    opts = [o for o in domlib._walk(g) if o.tag in ("button", "input", "a")]
                    dims.append({"name": label_of(g), "control": _sel(g),
                                 "options": len(opts),
                                 "option": _sel(opts[0]) if opts else None})  # fmt: skip
            if not dims:
                continue
            controls = {d["control"] for d in dims} | {
                d.get("option") for d in dims if d.get("option")
            }
            reqs = calls_of(controls)
            own = [r for r in reqs if ((endpoints.get(r) or {}).get("network") or {}).get(
                "role", "").startswith("variant")]  # fmt: skip
            reqs = own or [r for r in reqs if r.startswith("GET ")]
            row = {"kind": "variant picker", "selector": _sel(node), "dimensions": dims}
            if reqs:
                row["api"] = reqs
                changes = set()
                for r in reqs:
                    ep = endpoints.get(r) or {}
                    for ans in (ep.get("responses") or {}).values():
                        props = (ans.get("schema") or {}).get("properties") or {}
                        changes |= {k for k in props if re.search(
                            r"price|image|picture|stock|avail|sku|variant", k, re.I)}  # fmt: skip
                row["changes"] = sorted(changes)
                row["build"] = ("Choosing an option calls the API listed and shows its answer: "
                                "the price and the picture change without a new page.")  # fmt: skip
            out.append(row)
            break
    # recommendation and personalisation blocks
    for node in nodes:
        ident = " ".join([node.id or "", *node.classes, node.attrs.get("aria-label") or ""])
        if node.tag in ("section", "div", "aside", "ul") and RECOMMEND.search(ident):
            out.append({"kind": "recommendations or personalisation", "selector": _sel(node),
                        "title": label_of(node),
                        "api": sorted(k for k, ep in endpoints.items()
                                      if RECOMMEND.search(k) and any(
                                          t.get("screen") == screen
                                          for t in ep.get("triggered_by") or [])),
                        "build": "The original chooses these per visitor: fill the block from any "
                                 "seed rows, the same way, and say so."})  # fmt: skip
    # search facets, sort and pages
    for node in nodes:
        ident = " ".join([node.id or "", *node.classes])
        if node.tag in ("aside", "div", "section", "form", "nav") and FACETS.search(ident):
            facets = []
            for g in [x for x in domlib._walk(node) if x.tag == "fieldset"
                      or (x.attrs.get("data-facet") is not None)]:  # fmt: skip
                opts = [o for o in domlib._walk(g) if o.tag == "input" and (o.attrs.get(
                    "type") or "").lower() in ("checkbox", "radio")]  # fmt: skip
                links = [o for o in domlib._walk(g) if o.tag == "a" and o.attrs.get("href")]
                values = [o.attrs.get("value", "") for o in opts]
                params = sorted({o.attrs.get("name") for o in opts if o.attrs.get("name")})
                if links and not opts:
                    qs = [parse_qsl(urlsplit(a.attrs["href"]).query) for a in links]
                    params = sorted({k for q in qs for k, _ in q})
                    values = [domlib._text(a)[:40] for a in links]
                if not values:
                    continue
                facets.append({"name": label_of(g), "param": params[0] if len(params) == 1
                               else params, "control": "check box" if opts and (opts[0].attrs.get(
                                   "type") or "").lower() == "checkbox" else "radio button"
                               if opts else "link", "values": values[:40]})  # fmt: skip
            if facets:
                out.append({"kind": "search facets", "selector": _sel(node),
                            "facets": facets,
                            "build": "Each facet is a code list: offer the same values, send the "
                                     "same parameter, and narrow the results to rows with that "
                                     "value."})  # fmt: skip
                break
    sort = next((x for x in nodes if x.tag == "select" and re.search(
        r"sort|order", (x.attrs.get("name") or "") + (x.id or ""), re.I)), None)  # fmt: skip
    if sort is not None:
        out.append({"kind": "sort", "selector": _sel(sort),
                    "param": sort.attrs.get("name"),
                    "values": [o.attrs.get("value", "") for o in domlib._walk(sort)
                               if o.tag == "option"][:20]})  # fmt: skip
    pager = next((x for x in nodes if x.tag in ("nav", "div", "ul") and re.search(
        r"pag(er|ination|es)", " ".join([x.id or "", *x.classes, x.attrs.get("aria-label") or ""]),
        re.I)), None)  # fmt: skip
    if pager is not None:
        params = sorted({k for a in domlib._walk(pager) if a.tag == "a" and a.attrs.get("href")
                         for k, _ in parse_qsl(urlsplit(a.attrs["href"]).query)
                         if re.fullmatch(r"page|p|pg|start|offset", k, re.I)})  # fmt: skip
        out.append({"kind": "pages (links)", "selector": _sel(pager), "param": params})
    # a category tree outside the shared layout
    for node in nodes:
        if node.tag == "nav" or (node.id and re.search(r"categor|tree|departments", node.id, re.I)):
            tree = _menu_tree(node)
            if tree and _depth(tree) >= 2 and not re.search(r"breadcrumb", node.attrs.get(
                    "aria-label") or "", re.I):  # fmt: skip
                if node.id and re.search(r"megaMenu|mega", node.id, re.I):
                    continue
                out.append({"kind": "category tree", "selector": _sel(node),
                            "depth": _depth(tree), "top_items": len(tree), "tree": tree})  # fmt: skip
                break
    # lists that load more: infinite scroll or "load more"
    for key, ep in endpoints.items():
        net = ep.get("network") or {}
        paging = net.get("paging") or {}
        if paging and any(t.get("screen") == screen for t in ep.get("triggered_by") or []):
            roles = net.get("query_roles") or {}
            out.append({"kind": "list loaded in pages", "api": key,
                        "parameters": {k: v for k, v in roles.items()
                                       if v.startswith(("cursor", "page", "offset"))},
                        "envelope": {k: v for k, v in paging.items() if k != "asked_by"},
                        "asked_by": paging.get("asked_by") or []})  # fmt: skip
    return out


def _query_params(urls: list[str], codes: set[str]) -> dict[str, dict]:
    from labctl import netfacts
    from labctl.behaviour import dom as domlib

    vals: dict[str, list[str]] = defaultdict(list)
    for u in urls:
        for k, v in parse_qsl(urlsplit(u).query, keep_blank_values=True):
            vals[netfacts._field_name(k)].append(v)
    out = {}
    for k, vs in vals.items():
        role = next((r for rx, r in netfacts.QUERY_ROLES if rx.match(k)), None)
        info: dict = {"role": role or "filter or option", "distinct": len(set(vs)),
                      "sent_in": f"{len(vs)} of {len(urls)} visit(s)"}  # fmt: skip
        coded = sorted({v for v in vs if v in codes}) if role != "search" else []
        if coded:  # a search is what a person typed: its shape only, even when it is a code
            info["values"] = coded[:30]
        elif role in ("page", "page size", "offset") and all(v.isdigit() for v in vs):
            info["values"] = sorted({int(v) for v in vs})[:20]
        else:
            info["shapes"] = sorted({domlib.shape(v) or "" for v in vs})[:5]
        out[k] = info
    return out


def _codes_of(tree) -> set[str]:
    """Values the page offers as choices: option, check box and radio values."""
    from labctl.behaviour import dom as domlib

    out = set()
    for node in domlib._walk(tree):
        if node.tag == "option" or (node.tag == "input" and (node.attrs.get("type") or "").lower()
                                    in ("checkbox", "radio")):  # fmt: skip
            v = node.attrs.get("value")
            if v and not re.fullmatch(r"[Xx9 ._/-]+", v):
                out.add(v)
    return out


def build(root: Path, app: str, ctxs: list, nav: dict | None, done: dict | None,
          reveal=None) -> dict | None:  # fmt: skip
    """The templates document. `reveal(screen, column) -> bool`: a field declared public data
    whose recorded values may be carried; None (today): every value is synthetic."""
    from labctl import apicatalog as ac
    from labctl.behaviour import dom as domlib

    if not ctxs:
        return None
    nav = nav or {}
    endpoints = {f"{d.get('method')} {d.get('path')}": d
                 for d in ((done or {}).get("endpoints") or {}).values()}  # fmt: skip
    layout = nav.get("shared_layout") or {}
    layout_sel = {r["selector"] for r in layout.get("regions") or []}
    nav_by = {s.get("path"): s for s in nav.get("screens") or []}
    reps = _representatives(ctxs)
    urls: dict[str, list[str]] = defaultdict(list)  # screen -> address of every visit
    trees: dict[str, dict[str, object]] = defaultdict(dict)  # screen -> path -> raw tree
    jsf_screens: set[str] = set()  # JSF views: one screen each, its sub-pages sub-views
    for c in ctxs:
        last = None
        views = jsfviews.step_views(c.part.raw, c.part.doms)
        jsf_screens |= set(views.values())
        for s in c.part.raw:
            url = str(s.get("url") or "")
            if not url.startswith("http"):
                continue
            screen = screen_of(s, views)
            if (url, screen) != last:  # a JSF forward changes the view, not the address
                urls[screen].append(url)
                last = (url, screen)
            if s.get("seq") in c.part.doms:
                path = screen if s.get("seq") in views else urlsplit(url).path or "/"
                trees[screen].setdefault(path, c.part.doms[s["seq"]])  # as it loaded
    kept = ac.kept_values(root, app, ctxs)
    avoid = set().union(*(c.part.words for c in ctxs))
    world = ac.World({}, kept, avoid, {})
    rules = [r for r in sanitize._TEMPLATE_RULES]
    templates = []
    for screen in [s for s in nav_by if s in urls] + [s for s in urls if s not in nav_by]:
        row_nav = nav_by.get(screen) or {}
        if row_nav and not row_nav.get("in_scope", True):
            continue
        paths = [screen] if screen in jsf_screens else list(
            dict.fromkeys(urlsplit(u).path or "/" for u in urls[screen]))  # fmt: skip
        raw_trees = [domlib.parse_html(h) for h in trees[screen].values()]
        rep = reps.get(screen)
        safe = domlib.parse_html(rep[1]) if rep else None
        row: dict = {"screen": screen, "instances": len(paths), "visits": len(urls[screen])}
        if row_nav.get("page"):
            row["page"] = row_nav["page"]
        params = {}
        for i, seg in enumerate(screen.split("/")):
            if seg.startswith("{"):
                vals = [p.split("/")[i] for p in paths if len(p.split("/")) > i]
                params[seg.strip("{}")] = {
                    "distinct": len(set(vals)),
                    "shapes": sorted({domlib.shape(v) or "" for v in vals})[:5],
                    "kind": "slug (a name in the address)" if seg.startswith("{slug")
                    else "identifier"}  # fmt: skip
        if params:
            row["path_parameters"] = params
        codes = set().union(*(_codes_of(t) for t in raw_trees)) if raw_trees else set()
        query = _query_params(urls[screen], codes)
        if query:
            row["query_parameters"] = query
        if len(raw_trees) >= 2:
            vocab = set().union(*(c.vocab for c in ctxs))
            fields, lists = _field_rows(raw_trees, layout_sel, vocab)
        else:
            fields, lists = [], []
        if fields:
            rows = []
            for k in range(len(paths) if len(raw_trees) >= 2 else 0):
                rec: dict = {"instance": k + 1}
                for f in fields:
                    vals = f["_values"]
                    if k >= len(vals) or not vals[k]:
                        continue
                    if reveal is not None and reveal(screen, f["column"]):
                        rec[f["column"]] = vals[k]
                    elif f["kind"] == "picture":
                        rec[f["column"]] = f"/img/synthetic/{k + 1}.svg"
                    elif f["kind"] in ("number", "amount"):
                        digits = re.sub(r"\D", "", vals[k]) or "0"
                        made = str(world.number(int(digits)))
                        rec[f["column"]] = (
                            f"{made[:-2] or '0'}.{made[-2:]}" if f["kind"] == "amount" else made
                        )
                    else:
                        rec[f["column"]] = world.text(vals[k])
                rows.append(rec)
            row["data_fields"] = [{k: v for k, v in f.items() if k != "_values"} for f in fields]
            row["seed"] = {
                "rows": rows,
                "basis": "synthetic values of the recorded shapes, one row per instance seen"
                + ("; columns declared public data carry the recorded value" if reveal else
                   " (no public-data declaration: every value is masked)"),
                "use": "Create these as records the template shows (one per address); the "
                       "address parameter of each is free to choose.",
            }  # fmt: skip
        if lists:
            row["lists"] = lists
        if safe is not None:
            raw_rep = domlib.parse_html(rep[0])
            widgets = _widgets(raw_rep, safe, screen, row_nav, endpoints)
            if widgets:
                row["widgets"] = widgets
        templates.append(row)
    doc = {
        "format": FORMAT,
        "app": app,
        "about": ABOUT,
        "learned": [
            {
                "pattern": sanitize.route_pattern(
                    "/".join("x" if p == "*" else ("1" if p == "{}" else p) for p in r)
                ),
                "rule": "/".join(r),
            }
            for r in rules
        ],  # fmt: skip
        "templates": templates,
        "screens": len(templates),
        "instances": sum(t["instances"] for t in templates),
    }
    if layout:
        doc["shared_layout"] = {"see": "navigation.json `shared_layout`",
                                "regions": [r["selector"] for r in layout.get("regions") or []]}  # fmt: skip
    hier = _hierarchy(templates, layout)
    if hier:
        doc["navigation_hierarchy"] = hier
    state = _state(endpoints, layout, nav)
    if state:
        doc["state"] = state
    return doc


def _hierarchy(templates: list[dict], layout: dict) -> dict | None:
    levels: dict[int, set] = defaultdict(set)
    for t in templates:
        for w in t.get("widgets") or []:
            if w["kind"] == "breadcrumbs":
                for i, lvl in enumerate(w["levels"]):
                    levels[i].add(t["screen"] if lvl == "(this page)" else lvl)
    tree = None
    for r in layout.get("regions") or []:
        if r.get("mega_menu"):
            tree = {"from": f"the mega-menu `{r['mega_menu']['selector']}`",
                    "tree": r["mega_menu"]["tree"]}  # fmt: skip
    for t in templates:
        for w in t.get("widgets") or []:
            if w["kind"] == "category tree" and tree is None:
                tree = {"from": f"the category tree `{w['selector']}` on `{t['screen']}`",
                        "tree": w["tree"]}  # fmt: skip
    if not levels and not tree:
        return None
    out: dict = {}
    if levels:
        out["levels"] = [{"depth": i + 1, "screens": sorted(v)} for i, v in sorted(levels.items())]
    if tree:
        out["categories"] = tree
    return out


def _state(endpoints: dict, layout: dict, nav: dict) -> list[dict]:
    out = []
    for role in STATE_ROLES:
        eps = {k: ep for k, ep in endpoints.items()
               if ((ep.get("network") or {}).get("role") or "") .startswith(role)}  # fmt: skip
        if not eps:
            continue
        fields = set()
        auth = set()
        for ep in eps.values():
            auth.add((ep.get("request") or {}).get("auth") or "none")
            for status, ans in (ep.get("responses") or {}).items():
                if status.startswith("2"):
                    fields |= set(((ans.get("schema") or {}).get("properties") or {}))
        words = r"cart|basket|bag|trolley" if role.startswith("cart") else r"wish"
        badge = next((e for e in layout.get("elements") or [] if re.search(words, e, re.I)), None)
        row = {"kind": role.replace(" state", ""), "endpoints": sorted(eps),
               "fields": sorted(fields)[:30],
               "kept_by": ("the server, per session: " + ", ".join(sorted(auth - {"none"})))
               if auth - {"none"} else "the server (no session credential was sent)"}  # fmt: skip
        if badge:
            row["badge"] = badge
        out.append(row)
    return out


ABOUT = (
    "Each recorded screen as a template: how many different addresses (instances) and visits "
    "the recording has, the parameters that vary, the data fields (the elements whose text "
    "differs between instances) with one seed row per instance (synthetic values), the lists, "
    "and the widgets of the page: breadcrumbs, image gallery, variant picker (with the API it "
    "calls and what changes), recommendation or personalisation blocks, search facets (code "
    "lists), sort and pages, lists that load more (cursor or page), a category tree. Build one "
    "screen per template, never one per instance. The shared layout (header, menus, footer) is "
    "in navigation.json `shared_layout`: build it once."
)


def write(root: Path, app: str, ctxs: list, nav: dict | None, done: dict | None) -> dict:
    """Write `apps/<app>/templates.json`; {"doc", "problems"}."""
    from labctl import apicatalog as ac
    from labctl import codemap

    path = Path(root) / "apps" / app / FILE
    doc = build(root, app, ctxs, nav, done)
    if not doc or not doc["templates"]:
        path.unlink(missing_ok=True)
        return {"doc": None, "problems": []}
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    own = {urlsplit(str(s.get("url") or "")).netloc.lower() for c in ctxs for s in c.part.raw
           if str(s.get("url") or "").startswith("http")}  # fmt: skip
    bad = ac.leaks(root, app, ctxs, {FILE: text}) + codemap.check_text(text, own)
    if bad:
        path.unlink(missing_ok=True)
        return {"doc": None, "problems": [f"{FILE} not written: {b}" for b in bad[:5]]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return {"doc": doc, "problems": []}


def brief_section(app: str, done: dict | None, net: dict | None = None) -> str:
    from labctl import netfacts

    doc = (done or {}).get("doc")
    lines = []
    if doc:
        lines += ["## Screen templates and shared layout", "",
                  f"Every screen is a template (`apps/{app}/{FILE}`): build it once, whatever the "
                  "number of addresses it has. " + str(doc["screens"]) + " template(s), "
                  + str(doc["instances"]) + " address(es) recorded.", ""]  # fmt: skip
        for t in doc["templates"]:
            extra = []
            if t.get("path_parameters"):
                extra.append("varies by " + ", ".join(f"`{k}`" for k in t["path_parameters"]))
            if t.get("query_parameters"):
                extra.append("query " + ", ".join(f"`{k}` ({v['role']})"
                                                  for k, v in t["query_parameters"].items()))  # fmt: skip
            kinds = sorted({w["kind"] for w in t.get("widgets") or []})
            if kinds:
                extra.append("widgets: " + ", ".join(kinds))
            if t.get("data_fields"):
                extra.append(f"{len(t['data_fields'])} data field(s), one seed row per instance")
            lines.append(f"- `{t['screen']}`: {t['instances']} instance(s), {t['visits']} visit(s)"
                         + ("; " + "; ".join(extra) if extra else ""))  # fmt: skip
        if doc.get("shared_layout"):
            lines += ["", "**Shared layout.** " + ", ".join(
                f"`{r}`" for r in doc["shared_layout"]["regions"]) + " are on every screen: one "
                "layout, built once (`navigation.json` `shared_layout`; an element marked `layout` "
                "is part of it)."]  # fmt: skip
        for s in doc.get("state") or []:
            lines.append(f"- {s['kind'].capitalize()}: kept by {s['kept_by']}; endpoints "
                         + ", ".join(f"`{e}`" for e in s["endpoints"])
                         + (f"; badge `{s['badge']}`" if s.get("badge") else ""))  # fmt: skip
        lines.append("")
    net_lines = netfacts.brief_lines(net)
    if net_lines:
        lines += ["## Network behaviour", "", *net_lines, ""]
    return "\n".join(lines) + ("\n" if lines else "")


# --- the build documents (labctl.builder.docs) ---------------------------------------------------


def _widget_line(w: dict) -> str:
    kind = w["kind"]
    sel = f" `{w['selector']}`" if w.get("selector") else ""
    if kind == "breadcrumbs":
        levels = " > ".join(f"`{x}`" for x in w["levels"])
        return f"Breadcrumbs{sel}: {w['depth']} level(s), {levels}."
    if kind == "image gallery":
        used = f"; {w['used']}" if w.get("used") else ""
        return (f"Image gallery{sel}: main picture `{w['main_image']}`, {w['thumbnails']} "
                f"thumbnail(s) `{w.get('thumbnail')}`{used}.")  # fmt: skip
    if kind == "variant picker":
        dims = "; ".join(f"{d['name']} (`{d['control']}`, {d['options']} option(s))"
                         for d in w["dimensions"])  # fmt: skip
        if w.get("api"):
            api = (" Choosing one calls " + ", ".join(f"`{a}`" for a in w["api"]) + ", which "
                   "changes " + ", ".join(f"`{c}`" for c in w.get("changes") or []) + ".")  # fmt: skip
        else:
            api = " No request was recorded when choosing."
        return f"Variant picker{sel}: {dims}.{api}"
    if kind.startswith("recommendations"):
        api = ", ".join(f"`{a}`" for a in w.get("api") or [])
        return (f"Recommendations or personalisation{sel} ({w.get('title') or 'no title'})"
                + (f", filled by {api}" if api else "") + f": {w['build']}")  # fmt: skip
    if kind == "search facets":
        facets = "; ".join(f"{f['name']} (`{f['param']}`, {f['control']}): "
                           + ", ".join(f"`{v}`" for v in f["values"]) for f in w["facets"])  # fmt: skip
        return f"Search facets{sel}: {facets}. {w['build']}"
    if kind == "sort":
        return f"Sort{sel} (`{w.get('param')}`): " + ", ".join(f"`{v}`" for v in w["values"]) + "."
    if kind == "pages (links)":
        return f"Page links{sel}, parameter " + ", ".join(f"`{p}`" for p in w["param"]) + "."
    if kind == "category tree":
        return f"Category tree{sel}: {w['top_items']} top item(s), {w['depth']} level(s)."
    if kind == "list loaded in pages":
        params = ", ".join(f"`{k}` ({v})" for k, v in (w.get("parameters") or {}).items())
        env = "; ".join(f"{k}: {', '.join(v) if isinstance(v, list) else v}"
                        for k, v in (w.get("envelope") or {}).items())  # fmt: skip
        asked = "; ".join(w.get("asked_by") or [])
        return (f"List loaded in pages by `{w['api']}`" + (f", parameters {params}" if params
                                                           else "") + f" ({env})"
                + (f"; asked by {asked}" if asked else "") + ".")  # fmt: skip
    return f"{kind}{sel}."


def page_lines(doc: dict | None, screen: str, app: str) -> list[str]:
    """The *Template and instances* section of a page document."""
    if not doc:
        return []
    t = next((x for x in doc.get("templates") or [] if x["screen"] == screen), None)
    if t is None:
        return []
    out = ["## Template and instances", "",
           f"From `{app}/{FILE}`: this screen is ONE template. Build it once; every address of "
           f"this pattern shows it with another record. The recording has {t['instances']} "
           f"address(es) of it and {t['visits']} visit(s).", ""]  # fmt: skip
    for name, p in (t.get("path_parameters") or {}).items():
        shapes = ", ".join(f"`{s}`" for s in p["shapes"])
        out.append(f"- Path parameter `{{{name}}}`: {p['kind']}, {p['distinct']} value(s) seen, "
                   f"shape {shapes}.")  # fmt: skip
    for name, q in (t.get("query_parameters") or {}).items():
        if q.get("values"):
            seen = "values " + ", ".join(f"`{v}`" for v in q["values"])
        else:
            seen = "shape " + ", ".join(f"`{s}`" for s in q.get("shapes") or [])
        out.append(f"- Query parameter `{name}`: {q['role']}, sent in {q['sent_in']}, {seen}.")
    if t.get("data_fields"):
        out += ["", "Data fields (their text differs between instances: show the record's value "
                "there; the seed rows of `templates.json` give one record per instance):", "",
                "| Element | Column | Kind | Seen |", "|---|---|---|---|"]  # fmt: skip
        out += [f"| `{f['selector']}` | `{f['column']}` | {f['kind']} | {f['varies']} |"
                for f in t["data_fields"]]  # fmt: skip
        out.append("")
    for li in t.get("lists") or []:
        out.append(f"- Repeated `{li['selector']}`: {li['per_instance']['min']} to "
                   f"{li['per_instance']['max']} per instance (a list of records).")  # fmt: skip
    if t.get("widgets"):
        out += ["", "Parts of this template:", ""]
        out += [f"- {_widget_line(w)}" for w in t["widgets"]]
    return [*out, ""]


def architecture_lines(doc: dict | None, nav: dict | None, app: str) -> list[str]:
    """The *Shared layout* and *Screen templates* sections of ARCHITECTURE.md."""
    out: list[str] = []
    layout = (nav or {}).get("shared_layout")
    if layout:
        out += ["## Shared layout", "",
                f"From `{app}/navigation.json` `shared_layout`: build these regions once, as the "
                "layout of every screen. A page file leaves their elements out.", "",
                "| Region | Kind | On screens | Interactive elements | Links |",
                "|---|---|---|---|---|"]  # fmt: skip
        for r in layout["regions"]:
            out.append(f"| `{r['selector']}` | {r['kind']} | {r['on_screens']} | "
                       f"{r['interactive_elements']} | {r['links']} |")  # fmt: skip
        for r in layout["regions"]:
            mm = r.get("mega_menu")
            if not mm:
                continue
            out += ["", f"Mega-menu `{mm['selector']}` in `{r['selector']}`: {mm['top_items']} "
                    f"top item(s), {mm['depth']} level(s), {mm['links']} link(s):", ""]  # fmt: skip
            for item in mm["tree"]:
                kids = ", ".join(f"{k['label']} (`{k['to']}`)" for k in item.get("children") or [])
                out.append(f"- {item['label']} (`{item['to']}`)" + (f": {kids}" if kids else ""))
        out.append("")
    if doc and doc.get("templates"):
        out += ["## Screen templates", "",
                f"From `{app}/{FILE}`: {doc['screens']} template(s) for {doc['instances']} "
                "recorded address(es). One page per template, never one per address.", "",
                "| Template | Instances | Visits | Varies by | Parts |", "|---|---|---|---|---|"]  # fmt: skip
        for t in doc["templates"]:
            names = [*(t.get("path_parameters") or {}), *(t.get("query_parameters") or {})]
            varies = ", ".join(f"`{k}`" for k in names) or "-"
            parts = ", ".join(sorted({w["kind"] for w in t.get("widgets") or []})) or "-"
            out.append(f"| `{t['screen']}` | {t['instances']} | {t['visits']} | {varies} | "
                       f"{parts} |")  # fmt: skip
        if doc.get("learned"):
            out += ["", "Learned from the addresses (same structure, one segment varying): "
                    + ", ".join(f"`{x['pattern']}`" for x in doc["learned"]) + "."]  # fmt: skip
        hier = doc.get("navigation_hierarchy") or {}
        if hier.get("levels"):
            levels = " > ".join("/".join(f"`{s}`" for s in lvl["screens"])
                                for lvl in hier["levels"])  # fmt: skip
            out += ["", f"Navigation hierarchy (from the breadcrumbs): {levels}."]
        for s in doc.get("state") or []:
            fields = ", ".join(f"`{f}`" for f in s["fields"])
            out.append(f"- {s['kind'].capitalize()} state: kept by {s['kept_by']}; "
                       + ", ".join(f"`{e}`" for e in s["endpoints"])
                       + (f"; fields {fields}" if fields else "")
                       + (f"; badge `{s['badge']}`" if s.get("badge") else "") + ".")  # fmt: skip
        out.append("")
    return out
