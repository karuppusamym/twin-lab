"""What an application's own recordings show step by step, written for the builder.

From each recording under `evidence/<app>/` (raw evidence, Git-ignored) three things are derived,
none of them holding a value a person typed:

- **The step list** (`apps/<app>/steps/<recording>.json`): every step in order with its action,
  selector, screen (address pattern), tab, key, the shape of what was typed (letters A, digits 9),
  the requests it caused (method, address pattern, status) and the page that followed. Its own
  numbers and, for a part split from a recording across applications, the whole recording's.
- **What each action did** (brief section): per action that sent a request, changed the page or
  brought up a message: the requests with their status, the page that followed, the messages
  that appeared (interface text, data masked as in the page snapshots).
- **Identifiers and seed data** (brief section): identifiers the application listed or generated
  (an order number, an invoice number), as a pattern (prefix kept, digits counted), the lowest
  and highest number seen, how many rows its lists showed, and where a new one appeared after a
  save.

The rule for identifiers, so the builder can number records as the real application does: an
identifier the application showed is application output, and its pattern, its lowest and
highest number and the new ones it generated are kept verbatim; every other value is not listed.
A value a person typed is never kept, even when the application showed it first (the recorder
already put its shape in its place everywhere): such an identifier is reported as masked, with
its shape, and the rule left for the builder is "the next number is the highest + 1".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from labctl import sanitize

USER_ACTIONS = ("click", "dblclick", "keypress", "submit", "select", "check", "uncheck", "type",
                "upload", "dialog")  # fmt: skip
ID_VALUE = re.compile(r"^([A-Z]{1,8})([-_/.]?)(\d{2,12})$")
ID_FIELD = re.compile(r"(^|_)(id|no|number|num|ref|reference|code|key)$", re.I)
ID_WORDS = {"id", "no", "nr", "number", "num", "ref", "reference", "key", "ident"}
COUNT_FIELDS = ("total", "count", "total_count", "totalCount", "pages", "page_count")


def _steps(part: Path) -> list[dict]:
    out = []
    path = part / "steps.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _read(part: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    path = (part / rel).resolve()
    if part.resolve() not in path.parents or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _pattern(url: str | None) -> str | None:
    if not url or not url.startswith(("http:", "https:")):
        return None
    return sanitize.route_pattern(urlsplit(url).path or "/")


@dataclass
class Facts:
    recording: str
    steps: list[dict] = field(default_factory=list)  # the privacy-safe step list
    effects: list[dict] = field(default_factory=list)
    ids: list[dict] = field(default_factory=list)  # identifier patterns
    generated: list[dict] = field(default_factory=list)  # new identifiers after a save
    lists: list[dict] = field(default_factory=list)  # list answers: route, rows, total


def facts(part: Path) -> Facts:
    """Everything above for one recording folder (an app's part of a recording)."""
    from labctl import har
    from labctl.har import _safe_selector

    part = Path(part)
    raw = _steps(part)
    rec = har.load(part)
    hosts = {urlsplit(s.get("url") or "").netloc.lower() for s in raw
             if str(s.get("url") or "").startswith("http")}  # fmt: skip
    entries = [e for e in har.exchanges(rec.har) if not hosts or e.host in hosts]
    doms = {s["seq"]: t for s in raw if (t := _read(part, s.get("dom"))) is not None}
    befores = {s["seq"]: t for s in raw if (t := _read(part, s.get("dom_before"))) is not None}
    known, vocab = har.page_knowledge([*doms.values(), *befores.values()], entries)
    out = Facts(part.name)

    def messages(html: str | None) -> set[tuple[str, str]]:
        if html is None:
            return set()
        info = sanitize.page(html, known, vocab)
        return {(m["selector"], m["text"]) for m in info.messages if not m["hidden"]}

    # Each request goes to the last step at or before it; a page's own document to the page
    # load it became, when one followed within a few seconds (a back or forward is its own step).
    timed = [(i, har._time(s.get("ts"))) for i, s in enumerate(raw)]
    owner: dict[int, list] = {}
    for e in entries:
        if e.kind == "asset" or e.started is None:
            continue
        at = None
        for i, ts in timed:
            if ts is not None and ts <= e.started:
                at = i
        if e.kind == "document":
            load = next((i for i, ts in timed if ts is not None and ts >= e.started
                         and (ts - e.started).total_seconds() < 3 and raw[i].get("action") == "goto"
                         and _pattern(raw[i].get("url")) == sanitize.route_pattern(e.path)), None)  # fmt: skip
            at = load if load is not None else at
        if at is not None:
            owner.setdefault(at, []).append(e)

    page_before: str | None = None
    screen_before: str | None = None
    for i, s in enumerate(raw):
        action = str(s.get("action") or "")
        here = _pattern(s.get("url"))
        reqs = [f"{e.method} {sanitize.route_pattern(e.path)} {e.status}" for e in owner.get(i, [])
                if e.kind != "asset"]  # fmt: skip
        row = {"n": s.get("seq"), "action": action, "screen": screen_before or here}
        if s.get("global_seq") is not None:
            row["global"] = s["global_seq"]
        if s.get("selector"):
            row["selector"] = _safe_selector(str(s["selector"]))
            target = _target(s)
            if target:
                row["target"] = target
        for key in ("tab", "key", "nav", "caused_by", "frame"):
            if s.get(key) not in (None, "", []):
                row[key] = s[key]
        if s.get("modifiers"):
            row["key"] = "+".join([*s["modifiers"], str(s.get("key") or "")])
        if s.get("typed_shape"):
            row["typed_shape"] = str(s["typed_shape"])[:64]
        if s.get("option_index") is not None:
            row["option_index"] = s["option_index"]
        if reqs:
            row["requests"] = reqs
        if here and here != row["screen"]:
            row["then"] = here
        out.steps.append(row)
        html = doms.get(s["seq"])
        if html is not None:
            page_before_html = befores.get(s["seq"]) or page_before
            new = sorted(messages(html) - messages(page_before_html))
            if new:
                row["messages"] = [{"selector": a, "text": b} for a, b in new]
            page_before = html
        if here:
            screen_before = here
    out.effects = _effects(out.steps)
    ids, out.lists = _identifiers(entries, doms)
    out.generated = _generated(out.steps, raw, doms,
                               [i for i in ids if i["named"] or i["distinct"] >= 2])  # fmt: skip
    # The answer of a creating call (POST, 2xx, an identifier in it) names the new record exactly:
    # it replaces what the pages suggest for that pattern. Signing in creates no record.
    api = _api_generated(owner, out.steps)
    taken = {(g["prefix"], g["sep"], g["width"]) for g in api}
    out.generated = api + [g for g in out.generated
                           if (g["prefix"], g["sep"], g["width"]) not in taken
                           and not SESSION_ROUTE.search(g["request"])]  # fmt: skip
    made = {(g["prefix"], g["sep"], g["width"]) for g in out.generated}
    # a pattern is an identifier when it has several values, is named like one, or was generated
    out.ids = [i for i in ids if i["distinct"] >= 2 or i.pop("named", False)
               or (i["prefix"], i["sep"], i["width"]) in made]  # fmt: skip
    for i in out.ids:
        i.pop("named", None)
        i.pop("names", None)
    return out


SESSION_ROUTE = re.compile(r"log_?in|log_?on|sign_?in|auth|session|token", re.I)


def _api_generated(owner: dict[int, list], steps: list[dict]) -> list[dict]:
    """New identifiers the application answered a creating call with (`POST ... 201` and an
    `id`, `number`, `reference`... field holding an identifier)."""
    out = []
    for i in sorted(owner):
        for e in owner[i]:
            route = sanitize.route_pattern(e.path)
            if (e.method != "POST" or not (200 <= e.status < 300)
                    or not isinstance(e.resp_json, dict) or SESSION_ROUTE.search(route)):  # fmt: skip
                continue
            for k, v in e.resp_json.items():
                m = ID_VALUE.match(v.strip()) if isinstance(v, str) else None
                if not m or not (sanitize.name_words(str(k)) & ID_WORDS):
                    continue
                s = steps[i]
                item = {"prefix": m.group(1), "sep": m.group(2), "width": len(m.group(3))}
                out.append({"after": s["n"], "global": s.get("global"),
                            "request": f"{e.method} {route} {e.status}",
                            "element": f"answer field {k}", "screen": s.get("screen"),
                            "value": v.strip(), "shape": None,
                            "pattern": id_text(item, 0).replace("0", "9"), **item})  # fmt: skip
                break
    return out


SAFE_KINDS = ("testid", "id", "role", "label", "placeholder", "structure")


def _target(step: dict) -> str | None:
    """For a selector that is not an element's own id (`a.nav__item` may match several), the
    ranked locator the replay finds the element by: the first recorded candidate that matched
    exactly one element (a test id, role and name, label, or position in a structure). Names that
    hold digits are left out: they may be data."""
    from labctl import locate

    sel = str(step.get("selector") or "")
    if not sel or re.search(r"#[A-Za-z_][\w-]*$", sel.split(">>>")[-1].strip()):
        return None
    for cand in step.get("locators") or []:
        kind = cand.get("kind")
        if kind not in SAFE_KINDS:
            continue
        if kind == "structure":
            return locate.describe(cand)
        if cand.get("count") != 1:
            continue
        name = str(cand.get("name") or cand.get("text") or cand.get("value") or "")
        if kind in ("role", "label", "placeholder") and re.search(r"\d", name):
            continue
        return locate.describe(cand)
    return None


def _effects(steps: list[dict]) -> list[dict]:
    """Per action a person took: what it sent, where it went, what it said. A submit or page
    load an action caused is folded into that action."""
    rows: list[dict] = []
    by_n = {s["n"]: s for s in steps}
    for s in steps:
        back = s["action"] == "goto" and s.get("nav") == "back_forward"
        if s["action"] not in USER_ACTIONS and not back:
            continue
        cause = s.get("caused_by")
        if s["action"] == "submit" and cause in by_n and by_n[cause]["action"] in USER_ACTIONS:
            continue  # shown with the click or key press that caused it
        caused = [x for x in steps if x.get("caused_by") == s["n"]
                  or (x["action"] == "submit" and x.get("caused_by") == s["n"])]  # fmt: skip
        chain = [s, *caused, *[y for x in caused for y in steps if y.get("caused_by") == x["n"]]]
        reqs = [r for x in chain for r in x.get("requests", [])]
        then = next((x.get("then") for x in reversed(chain) if x.get("then")), None)
        msgs = [m for x in chain for m in x.get("messages", [])]
        if not (reqs or then or msgs):
            continue
        rows.append({"n": s["n"], "global": s.get("global"), "screen": s.get("screen"),
                     "action": "browser back or forward" if back else s["action"],
                     "selector": s.get("selector"), "target": s.get("target"), "key": s.get("key"),
                     "typed_shape": s.get("typed_shape"), "requests": list(dict.fromkeys(reqs)),
                     "times": {r: reqs.count(r) for r in reqs if reqs.count(r) > 1},
                     "then": then, "messages": msgs})  # fmt: skip
    return rows


def _identifiers(entries, doms: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """Identifier patterns in the answers and pages, and the list answers' sizes."""
    from labctl import har

    found: dict[tuple, dict] = {}
    lists: dict[str, dict] = {}

    def add(prefix, sep, width, number, where, name):
        key = (prefix, sep, width)
        item = found.setdefault(key, {"prefix": prefix, "sep": sep, "width": width,
                                      "numbers": set(), "where": [], "named": False,
                                      "names": set()})  # fmt: skip
        item["numbers"].add(number)
        item["names"] |= sanitize.name_words(name)
        item["named"] = item["named"] or bool(sanitize.name_words(name) & ID_WORDS)
        if where not in item["where"]:
            item["where"].append(where)

    for e in entries:
        if e.method != "GET" or not (200 <= e.status < 300) or e.resp_json is None:
            continue
        route = f"{e.method} {sanitize.route_pattern(e.path)}"
        items = har._items(e.resp_json)
        if isinstance(e.resp_json, dict) and any(isinstance(e.resp_json.get(k), list)
                                                 for k in har.ITEM_KEYS):  # fmt: skip
            info = lists.setdefault(route, {"route": route, "rows": 0, "answers": 0})
            info["answers"] += 1
            info["rows"] = max(info["rows"], len(items))
            for k in COUNT_FIELDS:
                v = e.resp_json.get(k)
                if isinstance(v, int) and not isinstance(v, bool):
                    info[k] = max(info.get(k, 0), v)
        for item in items[:500]:
            for k, v in item.items():
                if isinstance(v, str) and (m := ID_VALUE.match(v.strip())):
                    add(m.group(1), m.group(2), len(m.group(3)), int(m.group(3)),
                        f"{route} `items[].{k}`" if isinstance(e.resp_json, dict) else
                        f"{route} `[].{k}`", str(k))  # fmt: skip
                elif isinstance(v, int) and not isinstance(v, bool) and ID_FIELD.search(str(k)):
                    add("", "", 0, v, f"{route} `items[].{k}`", str(k))
    for html in doms.values():
        for sel, text in _id_texts(html):
            m = ID_VALUE.match(text)
            if m:
                add(m.group(1), m.group(2), len(m.group(3)), int(m.group(3)), f"`{sel}`",
                    sel.split("#", 1)[-1].split(".", 1)[-1])  # fmt: skip
    out = []
    for item in found.values():
        nums = sorted(item["numbers"])
        out.append({"prefix": item["prefix"], "sep": item["sep"], "width": item["width"],
                    "lowest": nums[0], "highest": nums[-1], "distinct": len(nums),
                    "where": item["where"][:6], "named": item["named"],
                    "names": item["names"]})  # fmt: skip
    return out, list(lists.values())


def _id_texts(html: str):
    """(selector, text) of elements whose whole text could be an identifier: no child element
    holds text of its own, and the element has an id or a class to find it by."""
    from labctl.behaviour import dom as domlib

    root = domlib.parse_html(html)

    def walk(node):
        for child in node.children:
            yield child
            yield from walk(child)

    for node in walk(root):
        if node.tag in ("script", "style", "input", "select", "textarea", "option"):
            continue
        text = " ".join("".join(node.text).split())
        if (
            not text
            or len(text) > 24
            or any(" ".join("".join(c.text).split()) for c in node.children)
        ):
            continue
        if node.id:
            yield f"{node.tag}#{node.id}", text
        elif node.classes:
            yield f"{node.tag}.{node.classes[0]}", text


def id_text(item: dict, number: int) -> str:
    return (
        f"{item['prefix']}{item['sep']}{number:0{item['width']}d}" if item["width"] else str(number)
    )


def _generated(
    steps: list[dict], raw: list[dict], doms: dict[int, str], ids: list[dict]
) -> list[dict]:
    """New identifiers a page showed right after a request that changed something."""
    from labctl.behaviour import dom as domlib

    by_shape: dict[str, list[dict]] = {}
    for i in ids:
        if i["width"]:
            by_shape.setdefault(domlib.shape(id_text(i, i["highest"])) or "", []).append(i)
    # A masked value matches a pattern only by its shape: trusted only when one pattern has that
    # shape, the element is named like an identifier and the write redirected (a record made).
    shapes = {k: v[0] for k, v in by_shape.items() if len(v) == 1}
    out = []
    seen: set[str] = set()  # an element reports its first identifier only (an edit keeps it)
    for s in steps:
        writes = [r for r in s.get("requests", []) if not r.startswith("GET ")
                  and r.split()[-1][:1] in "23"]  # fmt: skip
        if not writes:
            continue
        # the page that followed: this step's own snapshot or the first page load its action caused
        root = s.get("caused_by") or s["n"]
        later = [x for x in steps if x["n"] >= s["n"] and (x["n"] == s["n"]
                                                          or x.get("caused_by") == root)]  # fmt: skip
        for x in later:
            html = doms.get(x["n"])
            if html is None:
                continue
            for sel, text in _id_texts(html):
                m = ID_VALUE.match(text)
                item = None
                masked = False
                if m:
                    item = next((i for i in ids if (i["prefix"], i["sep"], i["width"]) ==
                                 (m.group(1), m.group(2), len(m.group(3)))), None)  # fmt: skip
                elif (text in shapes and writes[0].split()[-1].startswith("3")
                      and sanitize.name_words(sel.split("#", 1)[-1].split(".", 1)[-1])
                      & shapes[text]["names"] & ID_WORDS):  # fmt: skip
                    item, masked = shapes[text], True
                if item is None or sel in seen:
                    continue
                seen.add(sel)
                out.append({"after": s["n"], "global": s.get("global"), "request": writes[0],
                            "element": sel, "screen": x.get("then") or x.get("screen"),
                            "value": None if masked else text, "shape": text if masked else None,
                            "pattern": id_text(item, 0).replace("0", "9") if item["width"] else None,
                            "prefix": item["prefix"], "sep": item["sep"], "width": item["width"]})  # fmt: skip
            break
    return out


# --- writing ----------------------------------------------------------------------------------------


def parts_of(root: Path, app_id: str) -> list[Path]:
    """The app's recording folders that have a step list."""
    base = Path(root) / "evidence" / app_id
    if not base.is_dir():
        return []
    return [p for p in sorted(base.iterdir()) if p.is_dir() and (p / "steps.jsonl").is_file()
            and (p / "recording.har").is_file()]  # fmt: skip


def write_steps(root: Path, app_id: str) -> tuple[list[Path], list[Facts]]:
    """`apps/<app>/steps/<recording>.json` for each recording, rewritten from the evidence."""
    out_dir = Path(root) / "apps" / app_id / "steps"
    found = []
    for part in parts_of(root, app_id):
        try:
            found.append(facts(part))
        except (OSError, ValueError):
            continue
    if out_dir.is_dir():
        for old in out_dir.glob("*.json"):
            old.unlink()
    written = []
    for f in found:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{f.recording}.json"
        doc = {
            "recording": f.recording,
            "about": "The recorded steps of this application, in order: action, selector, screen "
            "(address pattern the action was taken on), tab, the shape of what was typed (letters "
            "A, digits 9; never the value), the requests each step caused (method, address "
            "pattern, status) and the page that followed (`then`). `n` is this application's "
            "step number; `global`, when present, the number in the whole recording across "
            "applications that the brief cites. `target`, where the selector is not an element's "
            "own id (it may match several elements), is the ranked locator the replay finds the "
            "element by. The value the acceptance replay types at each step, when generated, is "
            "in `<recording>.values.yaml` beside this file.",
            "steps": f.steps,
        }
        path.write_bytes(json.dumps(doc, indent=1, ensure_ascii=False).replace("\r\n", "\n")
                         .encode("utf-8") + b"\n")  # fmt: skip
        written.append(path)
    return written, found


def _num(row: dict) -> str:
    return f"{row['n']}" + (f" (whole recording {row['global']})" if row.get("global") else "")


def effects_section(found: list[Facts], app_id: str) -> str:
    if not any(f.effects for f in found):
        return ""
    out = ["## What each recorded action did\n",
           "Per action a person took (the submit or page load it caused is folded in): the requests "
           "it sent with their status (matched to the steps by time), the page that followed and "
           "the messages that appeared, as the application wrote them (data masked). Step numbers "
           "are this application's own, "
           f"in `apps/{app_id}/steps/<recording>.json`; the number in brackets is the whole "
           "recording's, which *Part of a flow across applications* cites. Typed values appear "
           "only as their shape (letters A, digits 9). The requests are listed as recorded: each "
           "is given to the last action logged before it started, so one action can list several "
           "(×2: the same request twice) and the action after it none; the recording does not "
           "say more than that. Where the selector is not an element's own id, *found by* is how "
           "the replay finds the element (the first recorded locator that matched only it).\n"]  # fmt: skip
    for f in found:
        if len(found) > 1:
            out.append(f"Recording `{f.recording}`:\n")
        out += [
            "| Step | Screen | Action | Requests | Then | Messages that appeared |",
            "|---|---|---|---|---|---|",
        ]
        for r in f.effects:
            what = r["action"] + (f" `{r['selector']}`" if r.get("selector") else "")
            if r.get("target"):
                what += f" (found by {r['target']})"
            if r.get("key"):
                what += f" (key {r['key']})"
            if r.get("typed_shape"):
                what += f" (typed shape `{r['typed_shape']}`)"
            times = r.get("times") or {}
            reqs = "<br>".join(f"`{x}`" + (f" ×{times[x]}" if x in times else "")
                               for x in r["requests"]) or "-"  # fmt: skip
            msgs = "<br>".join(f'`{m["selector"]}`: "{m["text"]}"' for m in r["messages"]) or "-"
            out.append(f"| {_num(r)} | `{r.get('screen') or '-'}` | {what} | {reqs} | "
                       f"{'`' + r['then'] + '`' if r.get('then') else '-'} | {msgs} |")  # fmt: skip
        out.append("")
        out += _sign_in(f)
    return "\n".join(out) + "\n"


def _first_screen(brief) -> str | None:
    """The first in-scope screen after signing in: where a link out of scope is sent."""
    for sc in brief.screens:
        path = sc.screen.path or ""
        if path and not re.search(r"log_?in|sign_?in|auth", path, re.I):
            return path
    return next((sc.screen.path for sc in brief.screens if sc.screen.path), None)


def links_section(root: Path, app_id: str, brief, found: list[Facts]) -> str:
    """Paths the in-scope pages link to that the route list does not name: a screen of this
    brief reached by a link only, an answer the recording saw (a sign-out redirect), or a path
    out of scope (keep the link; redirect to the first in-scope screen). And checkbox fields
    whose column is a boolean."""
    from labctl import multiapp
    from labctl.behaviour import dom as domlib

    routes = set(brief.routes)
    screens = {sc.screen.path for sc in brief.screens if sc.screen.path}
    others = {r.get("path") for _, data in multiapp.sequences_of(root, app_id)
              for r in data.get("steps") or [] if r.get("app") != app_id and r.get("path")}  # fmt: skip
    recorded: dict[str, tuple[str, str | None]] = {}
    for f in found:
        for e in f.effects:
            for r in e["requests"]:
                bits = r.split()
                if len(bits) == 3:
                    recorded.setdefault(f"{bits[0]} {bits[1]}", (bits[2], e.get("then")))
    links: dict[str, list[tuple[str, str]]] = {}
    boxes: list[tuple[str, str, str | None, str]] = []  # (selector, name, value, screen)
    for sc in brief.screens:
        for rel in sc.screen.files:
            path = Path(root) / rel
            if not path.is_file():
                continue
            html = path.read_text(encoding="utf-8", errors="replace")
            for node in domlib._walk(domlib.parse_html(html)):
                if any(a.tag == "tl-frame" for a in _ancestors(node)):
                    continue
                if node.tag == "a" and str(node.attrs.get("href") or "").startswith("/"):
                    target = sanitize.route_pattern(urlsplit(node.attrs["href"]).path or "/")
                    sel = (f"a#{node.id}" if node.id else
                           f"a.{node.classes[0]}" if node.classes else f'a[href="{node.attrs["href"]}"]')  # fmt: skip
                    if (sel, sc.screen.path) not in links.get(target, []):
                        links.setdefault(target, []).append((sel, sc.screen.path))
                if node.tag == "input" and (node.attrs.get("type") or "").lower() == "checkbox":
                    sel = (f"input#{node.id}" if node.id else
                           f'input[name="{node.attrs.get("name")}"]')  # fmt: skip
                    if node.attrs.get("name") and sel not in [b[0] for b in boxes]:
                        boxes.append((sel, node.attrs["name"], node.attrs.get("value"),
                                      sc.screen.path))  # fmt: skip
    serve, out_of_scope = [], []
    first = _first_screen(brief)
    for target, where in sorted(links.items()):
        key = f"GET {target}"
        if key in routes or target in others:
            continue
        sels = sorted({s for s, _ in where})
        on = sorted({p for _, p in where})
        src = f"linked from {', '.join(f'`{s}`' for s in sels)} on " + (
            f"{len(on)} screens" if len(on) > 2 else ", ".join(f"`{p}`" for p in on)
        )
        if target in screens:
            serve.append(f"- `{key}` ({src}): serve the screen `{target}` of this brief, the same "
                         "page the recording showed there (it was reached through another request, "
                         "never by this link).")  # fmt: skip
        elif key in recorded:
            status, then = recorded[key]
            serve.append(
                f"- `{key}` ({src}): recorded as `{key} {status}`"
                + (f", then `{then}`" if then else "")
                + ": answer the same way."
            )
        elif first:
            out_of_scope.append(
                f"- `{target}`: {', '.join(f'`{s}`' for s in sels)} ({src.split(' on ', 1)[1]})"
            )
    tables = {t: brief.table(t) for t in brief.tables}
    bools = []
    for sel, name, value, screen in boxes:
        hit = [(t, c) for t, table in tables.items() for c in table.columns
               if re.sub(r"[^a-z0-9]", "", c.name.lower()) == re.sub(r"[^a-z0-9]", "", name.lower())]  # fmt: skip
        if hit and str(hit[0][1].type).upper().startswith("BOOL"):
            t, c = hit[0]
            bools.append(f"- `{sel}` on `{screen}` posts `{name}={value or 'on'}` when ticked and "
                         f"nothing when not (so the route lists it as `{name}: string`); "
                         f"`{t}.{c.name}` is BOOLEAN: store ticked as true and not ticked as false, "
                         "and answer it as a JSON boolean, as the recorded answers did.")  # fmt: skip
    out = []
    if serve:
        out += ["### Paths the pages link to that the route list does not name\n",
                "The page snapshots link to these paths; the recording did not request them as a "
                "route above. Answer each as said:\n", *serve, ""]  # fmt: skip
    if out_of_scope:
        out += ["### Links that leave the scope\n",
                "These links lead to paths nothing in scope needs. Keep each element exactly as the "
                "snapshot has it (id, classes and `href`), and make the app answer the path with a "
                f"redirect (303) to `{first}`, the first in-scope screen after signing in. Build "
                "nothing else for it; list each in BUILD-NOTES.md:\n", *out_of_scope, ""]  # fmt: skip
    if bools:
        out += ["### Checkbox fields and their columns\n", *bools, ""]
    return "\n".join(out) + "\n" if out else ""


def _ancestors(node):
    cur = node.parent
    while cur is not None:
        yield cur
        cur = cur.parent


def _sign_in(f: Facts) -> list[str]:
    """What the recording shows about signing in, and what it does not (users, roles)."""
    typed = [
        s for s in f.steps if s["action"] == "type" and "pass" in (s.get("selector") or "").lower()
    ]
    if not typed:
        return []
    screen = typed[0].get("screen")
    rows = [r for r in f.effects if r.get("screen") == screen and any(
        q.startswith("POST ") for q in r["requests"])]  # fmt: skip
    refused = [r for r in rows if any(q.startswith("POST ") and q.endswith(" 200")
                                      for q in r["requests"]) and r["messages"]]  # fmt: skip
    taken = [r for r in rows if any(q.startswith("POST ") and q.split()[-1].startswith("3")
                                    for q in r["requests"])]  # fmt: skip
    said = []
    for r in refused[:2]:
        msg = "; ".join(f'`{m["selector"]}` "{m["text"]}"' for m in r["messages"])
        said.append(f"refused at step {_num(r)} ({', '.join(f'`{q}`' for q in r['requests'])}, "
                    f"then {msg})")  # fmt: skip
    for r in taken[:1]:
        said.append(f"accepted at step {_num(r)} ({', '.join(f'`{q}`' for q in r['requests'])}"
                    + (f", then `{r['then']}`" if r.get("then") else "") + ")")  # fmt: skip
    return [f"**Signing in.** The recording signed in on `{screen}` as one person"
            + (f": {'; '.join(said)}" if said else "") + ". Nothing in it shows which user names "
            "or passwords are valid, or any roles: accept any non-empty user name and password "
            "(an assumption: say so in BUILD-NOTES.md), refuse as recorded otherwise, and give "
            "every user the same screens.", ""]  # fmt: skip


def identifiers_section(found: list[Facts], tables: list[str], seeded: bool = False) -> str:
    """Identifier patterns, the numbers generated after a save, and seed-data guidance (a
    pointer to *Seed data and replay values* when `db/seed.sql` was generated)."""
    ids = [i for f in found for i in f.ids]
    gen = [g for f in found for g in f.generated]
    lists = [x for f in found for x in f.lists]
    if not (ids or gen or lists):
        return ""
    out = ["## Identifiers and seed data (observed)\n",
           "What the application listed and generated, so the demo numbers its records the same "
           "way. Identifiers the application showed are its output: their pattern, the lowest and "
           "highest number seen and the new ones it generated are given as they were. A value a "
           "person typed is never kept, even when the application showed it first: it is given "
           "as its shape (letters A, digits 9).\n"]  # fmt: skip
    merged: dict[tuple, dict] = {}
    for i in ids:
        key = (i["prefix"], i["sep"], i["width"])
        have = merged.setdefault(key, {**i, "where": []})
        have["lowest"], have["highest"] = min(have["lowest"], i["lowest"]), max(have["highest"],
                                                                                 i["highest"])  # fmt: skip
        have["distinct"] = max(have["distinct"], i["distinct"])
        have["where"] += [w for w in i["where"] if w not in have["where"]]
    for i in merged.values():
        pattern = (f"`{i['prefix']}{i['sep']}` + {i['width']} digits" if i["width"]
                   else "a whole number")  # fmt: skip
        span = i["highest"] - i["lowest"] + 1
        seen = (f"lowest seen `{id_text(i, i['lowest'])}`, highest seen "
                f"`{id_text(i, i['highest'])}`; {i['distinct']} different values were shown"
                + (f" (not every number in between: the range holds {span})"
                   if span > i["distinct"] else " (every number in between)")
                if i["distinct"] > 1 else f"one value seen, `{id_text(i, i['lowest'])}`")  # fmt: skip
        out.append(f"- Pattern {pattern}, in {', '.join(i['where'])}: {seen}.")
    for g in gen:
        where = f"`{g['element']}` on `{g['screen']}`"
        num = f"{g['after']}" + (f" (whole recording {g['global']})" if g.get("global") else "")
        if g["value"]:
            out.append(f"- After `{g['request']}` (step {num}) the application showed a new "
                       f"identifier in {where}: `{g['value']}`.")  # fmt: skip
        else:
            out.append(f"- After `{g['request']}` (step {num}) the application showed a new "
                       f"identifier in {where}. Its value is masked here because the person typed "
                       f"it later (shape `{g['shape']}`); it has the pattern "
                       f"`{g['prefix']}{g['sep']}` + {g['width']} digits. Generate it as the "
                       "highest existing number + 1.")  # fmt: skip
    for x in lists:
        extra = "".join(f", `{k}` up to {x[k]}" for k in COUNT_FIELDS if k in x)
        out.append(f"- `{x['route']}` answered {x['answers']} time(s) with up to {x['rows']} "
                   f"row(s) per answer{extra}.")  # fmt: skip
    out.append("")
    seeds = []
    for i in merged.values():
        if not i["width"] or not any(" " in w for w in i["where"]) and not any(
                (g["prefix"], g["sep"], g["width"]) == (i["prefix"], i["sep"], i["width"])
                for g in gen):  # fmt: skip
            continue  # seen on a page only, never listed or made: nothing to seed
        later = [g for g in gen if (g["prefix"], g["sep"], g["width"]) ==
                 (i["prefix"], i["sep"], i["width"])]  # fmt: skip
        first_new = next((g["value"] for g in later if g["value"]), None)
        first_num = int(ID_VALUE.match(first_new).group(3)) if first_new else None
        if first_new and first_num == i["lowest"]:
            seeds.append(f"- Records numbered `{i['prefix']}{i['sep']}`: the first one written in "
                         f"the recording was `{first_new}` and no earlier one was listed: start "
                         "with no seeded rows of this kind, numbering from that value.")  # fmt: skip
        else:
            seeds.append(f"- Records numbered `{i['prefix']}{i['sep']}` + {i['width']} digits: seed "
                         f"rows numbered without gaps from `{id_text(i, i['lowest'])}` to at least "
                         f"`{id_text(i, i['highest'])}` (the highest seen; the evidence does not "
                         "show how many rows exist in all). A new record gets the highest number "
                         "+ 1.")  # fmt: skip
    if seeded:
        out += ["The rows to start with are generated: [`db/seed.sql`](db/seed.sql), see *Seed data "
                "and replay values* below. A new record gets the highest number + 1.", ""]  # fmt: skip
    elif seeds:
        names = ", ".join(f"`{t}`" for t in tables) or "the tables in `db/schema.sql`"
        out += [f"Seed data for {names} (synthetic values, the shapes and code lists this brief "
                "gives):\n", *seeds, ""]  # fmt: skip
    return "\n".join(out) + "\n"
