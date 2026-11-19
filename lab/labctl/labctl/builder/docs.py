"""The build documents of a package: a constitution, the architecture, a build plan, one page
file per screen and a glossary, written only from the files the package already holds.

A coding agent that builds page by page should not have to collect one screen's facts from the
brief, the navigation map, the rules, the API files, the look and the steps. So the packaging
step writes, under `apps/<app>/spec/` (not `build/`: the repository ignores every `build/`
folder, so a builder who commits the package files would lose them):

- `CONSTITUTION.md`: the rules that are not negotiable, numbered. Its rules list is the same
  text as the task's *Rules that are not negotiable* (`rules_text()`, called by both), and its
  "done" list the same as the task's.
- `ARCHITECTURE.md`: the stack, the layers, every route, the API surface, the data model, sign-in
  and session with every cookie's attributes, how the app runs and is configured, the flow across
  applications, a text diagram.
- `BUILD-PLAN.md`: the build in order, each step with its "done when" lines.
- `PAGES/<NN>-<screen>.md`: one per screen, recorded screens first in recorded order, then the
  screens shown in a frame or popup that no step opened, then pages that exist and were not
  recorded, then screens found only in the application's code (`codemap.json`, when present).
  Each holds everything needed to build that one screen.
- `GLOSSARY.md`: option lists, code lists, the statuses the application answered, the words
  the package uses.

Every statement comes from a package file and names it; what the package does not hold is
written "unknown" with where it would come from. Nothing here reads the recording: the inputs
are the package entries (`generate(app_id, files)`), so nothing can reach these files that the
package's own privacy checks have not passed. `gaps()` is the completeness check of these files
(a screen without a page file is a gap).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DIR = "spec"  # apps/<app>/spec/
PAGES = "PAGES"
CONSTITUTION = "CONSTITUTION.md"
ARCHITECTURE = "ARCHITECTURE.md"
PLAN = "BUILD-PLAN.md"
GLOSSARY = "GLOSSARY.md"
CODEMAP = "codemap.json"  # apps/<app>/codemap.json (labctl.codemap), read when present
MASKED = re.compile(r"^[\sXx9.,:;@/+()#_'-]*[Xx9][\sXx9.,:;@/+()#_'-]*$")


def folder(app_id: str) -> str:
    return f"apps/{app_id}/{DIR}"


def applies(conf: dict) -> bool:
    """A web application gets the documents; a desktop one (its own task) does not."""
    return not (conf.get("stack") or {}).get("desktop")


# =================================================================================================
# The rules: one source for the task and the constitution
# =================================================================================================


def rules(app: str, packaged, browser: bool, seed_loaded: bool) -> list[str]:
    """The rules a build may never break, each one sentence first. `packaged` is the set of
    package paths; the task (`package.prompt`) and `CONSTITUTION.md` both call this."""
    have = set(packaged)
    nav = f"{app}/navigation.json" in have
    api = f"{app}/api/catalogue.json" in have
    look = any(p.startswith(f"{app}/look/") for p in have)
    pictures = any(p.startswith(f"{app}/look/screens/") for p in have)
    spec = f"{app}/{DIR}/"
    out = [
        "**Same technology and versions.** Build with the front-end framework and version, the "
        "server technology and the UI library the brief's *Stack to pin* names "
        f"(`{spec}{ARCHITECTURE}` repeats them). Do not substitute another because it is "
        "easier; where it names none, use the default it states, and pin and list every version "
        "in BUILD-NOTES.md.",
        "**Same selectors and ids.** Keep **every recorded selector, element id, class, "
        "`name`, iframe and shadow-DOM structure** exactly as the brief's screen tables and "
        f"`{app}/pages/*.html` show them. Automations find elements by these, and a changed id "
        "breaks them silently.",
        "**Same literal texts.** Write every label, heading, button, option, link and message "
        "exactly as the snapshots and the brief have it, letter for letter. Text shown masked "
        "(`Xxxx`, `9999`) is data: show the record's value there.",
        "**Same addresses and API contract.** Every screen keeps its path. Every route keeps "
        "its method, path, parameters, field names, types, codes and statuses"
        + (f" (`{app}/api/`)." if api else "."),
    ]
    if look:
        out.append(
            "**Same look.** Start from the style sheets in `" + app + "/look/css/`, check against "
            f"`{app}/look/theme.json`"
            + (f", and match each screen to its picture in `{app}/look/screens/`: layout, "
               "colours, spacing and fonts." if pictures else ".")
        )  # fmt: skip
    else:
        out.append("**Same look.** The package has no pictures or style sheets: the styles inside "
                   f"`{app}/pages/*.html` are the reference for each screen.")  # fmt: skip
    out.append(
        "**Every navigation and every action works.** "
        + (f"Each link, button, form, key, redirect, dialog, tab, frame and popup window in "
           f"`{app}/navigation.json` behaves as listed. " if nav else
           "Each link, button and form of the brief leads where the brief says. ")
        + "An element nobody used in the recording is rendered and gets no behaviour the brief "
        "does not state."
    )  # fmt: skip
    if f"{app}/rules.json" in have:
        from labctl import pagerules

        out.append(pagerules.task_rule(app))
    if browser:
        out.append("**Data stays where the original keeps it.** That is the browser, as the "
                   "brief's *Browser storage* section says. There is no server database.")  # fmt: skip
    else:
        out.append("**Data lives in the backend database.** Users, sessions and every record "
                   "are stored in and read from the database by the server. Nothing important "
                   "is kept only in the page or in the server's memory.")  # fmt: skip
        out.append("**The seed is loaded at start.** Create the tables from `db/schema.sql` "
                   + ("and load exactly the rows of `db/seed.sql`, once, when the tables are "
                      "empty." if seed_loaded else
                      "and fill them with synthetic rows as the brief says, once, when the "
                      "tables are empty."))  # fmt: skip
    out += [
        "**Synthetic data only.** No real name, number, address, hostname or credential in any "
        "file. Use the values the package gives.",
        "**Build what was recorded, nothing else.** Nothing under *Out of scope* is built or "
        "stubbed. What the package does not state is an assumption you write down, never a "
        "feature you invent.",
        "**Assumptions are written down.** Where the package is silent, or says *hypothesis*, "
        "*assumed*, *unknown*, *not recorded* or *seen, not used*, choose the simplest behaviour "
        f"that fits it and write one line under **Assumptions** in `{app}/BUILD-NOTES.md`: what "
        "the package did not say, what you chose, the file where you built it.",
        "**Done means every check holds.** The build is done when every item of *What \"done\" "
        "means* holds and every acceptance check of each page file "
        f"(`{spec}{PAGES}/*.md`) passes, checked and written in BUILD-NOTES.md.",
        "**The brief and the checks are not yours to change.** A check that fails is a finding "
        "about your app.",
    ]  # fmt: skip
    return out


def rules_text(app: str, packaged, browser: bool, seed_loaded: bool) -> str:
    return "\n".join(f"{n}. {t}" for n, t in enumerate(rules(app, packaged, browser,
                                                              seed_loaded), 1))  # fmt: skip


def done_items(app: str, packaged, browser: bool, values: bool) -> list[str]:
    """What must hold for the build to be done: the task's list and the constitution's."""
    have = set(packaged)
    items = [
        "Every screen of the brief opens at its address and has every element of its snapshot, "
        "with the same selectors, ids and texts.",
    ]
    if f"{app}/navigation.json" in have:
        items += [
            f"Every way from screen to screen, and every dialog, tab, frame and popup window "
            f"in `{app}/navigation.json`, works on the same element.",
            "Every element the recording used does what the brief's *Actions on each screen* "
            "says: the same request, the same message, the same next page.",
        ]
    if f"{app}/api/catalogue.json" in have:
        items.append(f"Every endpoint in `{app}/api/` answers on the same method and path, with "
                     "the same keys, types, codes and statuses.")  # fmt: skip
    if browser:
        items.append("The data is kept where the brief's *Browser storage* section says, under "
                     "the same keys.")  # fmt: skip
    else:
        items += [
            "Sign-in, the session and sign-out work as the brief's *Sign-in and session* says "
            "(where the brief has that section).",
            "Users, sessions and every record are stored in and read from the database. The "
            "tables come from `db/schema.sql`; the seed is loaded at start.",
        ]
    if values:
        items.append(f"The recorded steps, replayed in order with the values of "
                     f"`{app}/steps/*.values.yaml`, give at each step the outcome the recording "
                     "showed. This is the acceptance run.")  # fmt: skip
    items.append(f"`{app}/BUILD-NOTES.md` says what you built, what you checked and what you "
                 "assumed.")  # fmt: skip
    return items


def done_text(app: str, packaged, browser: bool, values: bool) -> str:
    return "\n".join(f"{n}. {t}" for n, t in enumerate(done_items(app, packaged, browser,
                                                                   values), 1))  # fmt: skip


def reading_rows(app: str) -> list[str]:
    """The first rows of the task's reading order: the documents of this module."""
    spec = f"{app}/{DIR}"
    return [
        f'`{spec}/{CONSTITUTION}`: the rules of this build, numbered, and what "done" means. '
        "The same rules as below.",
        f"`{spec}/{ARCHITECTURE}`: the stack, the layers, every route, the API surface, the data "
        "model, sign-in, session and cookies, how the app runs and is configured.",
        f'`{spec}/{PLAN}`: the build in order, each step with its "done when" lines.',
        f"`{spec}/{PAGES}/*.md`, one by one, in their numbered order, as you build each screen: "
        "everything one screen needs (address, ways in and out, picture, elements, actions, "
        "rules, API calls, data, seed rows, checks, open questions), with the file each fact "
        f"comes from. `{spec}/{GLOSSARY}` (when present) explains the codes and words.",
    ]


# =================================================================================================
# Reading the package
# =================================================================================================


def _cell(text) -> str:
    return re.sub(r"\s+", " ", str(text if text is not None else "")).replace("|", "\\|").strip()


def _code(text) -> str:
    text = "" if text is None else str(text)
    if text == "":
        return '`""` (empty)'
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def _slug(path: str) -> str:
    s = re.sub(r"[{}]", "", path).strip("/")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "home"


@dataclass
class Screen:
    path: str
    name: str
    kind: str  # recorded | frame | popup | stand-in | unrecorded | code
    nav: dict = field(default_factory=dict)
    page: str | None = None
    in_scope: bool = True
    host: tuple[str, str] | None = None  # (host screen, frame selector) for a frame
    file: str = ""
    number: int = 0


class Pkg:
    """The package's files, parsed once."""

    def __init__(self, app_id: str, files: dict[str, bytes]):
        self.app_id = app_id
        self.app = f"apps/{app_id}"
        self.files = files
        self.nav = self.json(f"{self.app}/navigation.json") or {}
        self.cat = self.json(f"{self.app}/api/catalogue.json") or {}
        self.rules = self.json(f"{self.app}/rules.json") or {}
        self.session = self.json(f"{self.app}/session.json")
        self.codemap = self.json(f"{self.app}/{CODEMAP}")
        self.seed = self.json(f"{self.app}/db/seed.json") or {}
        self.conf = self.yaml(f"{self.app}/app.yaml") or {}
        self.brief = self.text(f"{self.app}/BRIEF.md")
        self.schema = self.text(f"{self.app}/db/schema.sql")
        self.look = self.json(f"{self.app}/look/manifest.json") or {}
        self.theme = self.json(f"{self.app}/look/theme.json") or {}
        self.texts = (self.json(f"{self.app}/look/texts.json") or {}).get("screens") or {}
        self.endpoints: dict[str, dict] = {}
        for row in self.cat.get("endpoints") or []:
            doc = self.json(f"{self.app}/{row.get('file')}") if row.get("file") else None
            self.endpoints[f"{row.get('method')} {row.get('path')}"] = {**row, "doc": doc or {}}
        self.steps: list[tuple[str, list[dict]]] = []
        self.values: dict[str, dict[int, dict]] = {}
        for rel in sorted(files):
            if not rel.startswith(f"{self.app}/steps/"):
                continue
            if rel.endswith(".values.yaml"):
                doc = self.yaml(rel) or {}
                name = Path(rel).name[: -len(".values.yaml")]
                self.values[name] = {r["n"]: r for r in doc.get("steps") or []
                                     if isinstance(r, dict) and isinstance(r.get("n"), int)}  # fmt: skip
            elif rel.endswith(".json"):
                doc = self.json(rel) or {}
                self.steps.append((Path(rel).stem, [s for s in doc.get("steps") or []
                                                    if isinstance(s, dict)]))  # fmt: skip
        self.tables = self._tables()
        self.screens = self._screens()

    # --- raw files ---------------------------------------------------------------------------

    def text(self, rel: str) -> str:
        data = self.files.get(rel)
        return data.decode("utf-8", "replace") if data is not None else ""

    def json(self, rel: str):
        try:
            return json.loads(self.text(rel)) if rel in self.files else None
        except ValueError:
            return None

    def yaml(self, rel: str):
        try:
            return yaml.safe_load(self.text(rel)) if rel in self.files else None
        except yaml.YAMLError:
            return None

    def has(self, rel: str) -> bool:
        return rel in self.files

    def section(self, heading: str, level: str = "## ") -> str:
        from labctl.builder.package import _section

        return _section(self.brief, heading, level)

    @property
    def browser(self) -> bool:
        return (((self.conf.get("stack") or {}).get("data") or {}).get("kind")) == "browser"

    # --- the data model -------------------------------------------------------------------------

    def _tables(self) -> list[dict]:
        from labctl.builder.package import _columns, _create_tables

        out = []
        for name, body in _create_tables(self.schema):
            cols = _columns(body)
            pk = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", body, re.I)
            keys = [k.strip().strip('"[]`') for k in pk.group(1).split(",")] if pk else [
                c for c in cols if re.search(rf'^\s*"?{re.escape(c)}"?\s[^,]*PRIMARY\s+KEY', body,
                                             re.I | re.M)]  # fmt: skip
            refs = [{"column": m.group(1).strip('"[]`'), "table": m.group(2).strip('"[]`'),
                     "to": m.group(3).strip('"[]`')}
                    for m in re.finditer(r'FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+([\w."\[\]`]+)'
                                         r"\s*\(([^)]*)\)", body, re.I)]  # fmt: skip
            refs += [{"column": m.group(1).strip('"[]`'), "table": m.group(2).strip('"[]`'),
                      "to": m.group(3).strip('"[]`')}
                     for m in re.finditer(r'^\s*("?\w+"?)\s[^,\n]*?REFERENCES\s+([\w."\[\]`]+)\s*'
                                          r"\(([^)]*)\)", body, re.I | re.M)]  # fmt: skip
            types = {}
            for line in body.split(","):
                m = re.match(r'\s*"?(\w+)"?\s+([A-Za-z][\w ()]*?)(?:\s+(?:NOT|NULL|PRIMARY|'
                             r"DEFAULT|REFERENCES|UNIQUE|CHECK)\b|$)", line.strip() + " ", re.I)  # fmt: skip
                if m and m.group(1).lower() not in ("primary", "foreign", "constraint", "unique"):
                    types[m.group(1)] = m.group(2).strip()
            rows = self.seed.get(name) if isinstance(self.seed, dict) else None
            out.append({"name": name, "columns": cols, "keys": keys, "refs": refs,
                        "types": types, "seed_rows": len(rows) if isinstance(rows, list) else None,
                        "rows": rows if isinstance(rows, list) else []})  # fmt: skip
        return out

    def table(self, name: str) -> dict | None:
        return next((t for t in self.tables if t["name"] == str(name).lower()), None)

    # --- the screens ----------------------------------------------------------------------------

    def _screens(self) -> list[Screen]:
        navs = [s for s in self.nav.get("screens") or [] if isinstance(s, dict) and s.get("path")]
        by_path = {s["path"]: s for s in navs}
        order: list[str] = []
        for _, steps in self.steps:
            for s in steps:
                p = s.get("screen")
                if p in by_path and p not in order:
                    order.append(p)
        order += [s["path"] for s in navs if s["path"] not in order]
        out: list[Screen] = []
        hosts = {}
        for s in navs:
            for v in s.get("subviews") or []:
                if v.get("shows") and str(v.get("kind", "")).startswith(("iframe", "frame")):
                    hosts.setdefault(v["shows"], (s["path"], v.get("selector") or "iframe"))
        for p in order:
            s = by_path[p]
            kind = str(s.get("kind") or "")
            k = ("frame" if kind.startswith("shown in a frame") else
                 "popup" if "popup" in kind else
                 "stand-in" if kind.startswith("stand-in") else "recorded")  # fmt: skip
            out.append(Screen(p, s.get("name") or _slug(p), k, s, s.get("page"),
                              bool(s.get("in_scope", True)), hosts.get(p)))  # fmt: skip
        seen = {s.path for s in out}
        for u in self.nav.get("unrecorded_pages") or []:
            if u.get("path") and u["path"] not in seen:
                seen.add(u["path"])
                out.append(Screen(u["path"], _slug(u["path"]), "unrecorded", u, u.get("page"),
                                  False))  # fmt: skip
        for r in _code_routes(self.codemap):
            if r["path"] not in seen:
                seen.add(r["path"])
                out.append(Screen(r["path"], _slug(r["path"]), "code", r, None, False))
        taken: set[str] = set()
        for n, s in enumerate(out, 1):
            base = f"{n:02d}-{_slug(s.name)}"
            while base in taken:
                base += "-x"
            taken.add(base)
            s.number, s.file = n, f"{self.app}/{DIR}/{PAGES}/{base}.md"
        return out

    def screen(self, path: str) -> Screen | None:
        return next((s for s in self.screens if s.path == path), None)

    def rules_of(self, path: str) -> dict:
        return next((s for s in self.rules.get("screens") or [] if s.get("path") == path), {})

    def look_of(self, sc: Screen) -> dict:
        return next((s for s in self.look.get("screens") or []
                     if s.get("address") == sc.path or s.get("screen") == sc.name), {})  # fmt: skip

    def calls_of(self, path: str) -> list[tuple[str, dict, dict]]:
        """(endpoint, catalogue row with its doc, trigger) for every trigger on this screen."""
        out = []
        for name, row in self.endpoints.items():
            for t in row["doc"].get("triggered_by") or []:
                if isinstance(t, dict) and t.get("screen") == path:
                    out.append((name, row, t))
        return out

    def elements_of(self, sc: Screen) -> list[dict]:
        if sc.host:
            host = self.screen(sc.host[0])
            prefix = f"{sc.host[1]} >>> "
            return [{**e, "selector": e["selector"][len(prefix):], "_host": e["selector"]}
                    for e in (host.nav.get("elements") if host else []) or []
                    if str(e.get("selector", "")).startswith(prefix)]  # fmt: skip
        return [e for e in sc.nav.get("elements") or [] if isinstance(e, dict)]


def _code_routes(codemap) -> list[dict]:
    """The screens a code map (labctl.codemap) found: tolerant of its shape, which is another
    module's. A route is a dict with `path` (or `route`)."""
    if not isinstance(codemap, dict):
        return []
    out = []
    for key in ("routes", "screens"):
        for r in codemap.get(key) or []:
            if isinstance(r, str):
                r = {"path": r}
            if isinstance(r, dict) and (r.get("path") or r.get("route")):
                path = str(r.get("path") or r.get("route"))
                if not path.startswith("/"):
                    path = "/" + path
                out.append({**r, "path": path})
    return out


def _code_calls(codemap, path: str) -> list[dict]:
    if not isinstance(codemap, dict):
        return []
    out = []
    for key in ("api_calls", "api", "calls"):
        for c in codemap.get(key) or []:
            if isinstance(c, dict) and (not c.get("screen") or c.get("screen") == path):
                out.append(c)
    return out


# =================================================================================================
# The snapshot of a screen
# =================================================================================================


def _snapshot(pkg: Pkg, sc: Screen) -> dict:
    """What the page snapshot says: its title, its first heading, every element with an id or a
    name (outside the recorder's `tl-frame` copies of frame documents) and the selectors."""
    from labctl import navmap
    from labctl.behaviour import dom as domlib

    html = pkg.text(sc.page) if sc.page else ""
    out = {"title": None, "h1": None, "ids": [], "selectors": set()}
    if not html:
        return out
    tree = domlib.parse_html(html)

    def inside_frame_copy(node) -> bool:
        cur = node.parent
        while cur is not None:
            if cur.tag == "tl-frame":
                return True
            cur = cur.parent
        return False

    for node in domlib._walk(tree):
        if node.tag == "tl-frame" or inside_frame_copy(node):
            continue
        if node.tag == "title" and out["title"] is None:
            out["title"] = domlib._text(node)
        if node.tag == "h1" and out["h1"] is None:
            out["h1"] = domlib._text(node)
        sel = navmap.canon(node)
        out["selectors"].add(sel)
        if node.id or node.attrs.get("name"):
            leaf = not any(c.tag not in ("br",) for c in node.children)
            text = domlib._text(node) if leaf else ""
            out["ids"].append({"selector": sel, "tag": node.tag,
                               "text": text[:80] + ("..." if len(text) > 80 else ""),
                               "hidden": "hidden" in node.attrs})  # fmt: skip
    return out


def _heading_text(text: str | None) -> str:
    if not text:
        return ""
    return "data (masked in the snapshot)" if MASKED.match(text) else f'"{text}"'


# =================================================================================================
# One page file
# =================================================================================================


def _link(from_rel: str, to_rel: str) -> str:
    """A relative link between two package files, shown with the target's package path."""
    import posixpath

    rel = posixpath.relpath(to_rel, posixpath.dirname(from_rel))
    return f"[`{to_rel}`]({rel})"


def _used_summary(used) -> str:
    if isinstance(used, str):
        return used
    if not isinstance(used, list) or not used:
        return "-"
    parts = []
    for u in used:
        bits = [f"step {u.get('step')}: {u.get('action')}"]
        if u.get("key"):
            bits.append(f"key {u['key']}")
        if u.get("goes_to"):
            bits.append(f"goes to `{u['goes_to']}`")
        if u.get("opens"):
            bits.append("opens " + ", ".join(u["opens"]))
        if u.get("closes"):
            bits.append("closes " + ", ".join(u["closes"]))
        parts.append(", ".join(bits))
    return "; ".join(parts)


def _action_lines(els: list[dict]) -> list[str]:
    rows = []
    for e in els:
        for u in e.get("used") if isinstance(e.get("used"), list) else []:
            rows.append((u.get("step") or 0, e, u))
    out = []
    for step, e, u in sorted(rows, key=lambda r: r[0]):
        did = []
        if u.get("record"):
            did.append(f"on the row of `{u['record']}`")
        if u.get("requests"):
            did.append("sent " + ", ".join(f"`{r}`" for r in u["requests"]))
        if u.get("opens"):
            did.append("opened " + ", ".join(f"`{x}`" for x in u["opens"]))
        if u.get("closes"):
            did.append("closed " + ", ".join(f"`{x}`" for x in u["closes"]))
        if u.get("goes_to"):
            did.append(f"went to `{u['goes_to']}`")
        if u.get("messages"):
            did.append("showed " + "; ".join(u["messages"]))
        if u.get("action") in ("type", "select") and not did:
            did.append("a value " + ("typed" if u["action"] == "type" else "chosen")
                       + (f" (the replay uses `{u['replay_value']}`)"
                          if u.get("replay_value") is not None else ""))  # fmt: skip
        key = f" (key {u['key']})" if u.get("key") else ""
        out.append(f"- Step {step}: {u.get('action')} `{e['selector']}`{key}: "
                   + ("; ".join(did) if did else "nothing visible was recorded") + ".")  # fmt: skip
    return out


def _rule_rows(screen_rules: dict) -> list[str]:
    out = []
    for f in screen_rules.get("fields") or []:
        for r in f.get("rules") or []:
            value = f" `{r['value']}`" if r.get("value") is not None else ""
            out.append(f"| `{f.get('selector')}` ({_cell(f.get('label'))}) | "
                       f"{_cell(r.get('rule'))}{value} | {_cell(r.get('message'))} | "
                       f"{_cell(r.get('level'))} | {_cell('; '.join(r.get('evidence') or []))} |")  # fmt: skip
    for m in screen_rules.get("messages") or []:
        out.append(f"| (the form; shown in `{m.get('selector')}`) | {_cell(m.get('rule'))} | "
                   f"{_cell(m.get('message'))} | {_cell(m.get('level'))} | "
                   f"{_cell('; '.join(m.get('evidence') or []))} |")  # fmt: skip
    return out


def _sub_line(v: dict) -> str:
    what = v.get("kind") or "sub-view"
    sel = f" `{v['selector']}`" if v.get("selector") else ""
    title = f' "{v["title"]}"' if v.get("title") else ""
    bits = [f"{what}{sel}{title}"]
    if v.get("tab") and what == "tab panel":
        bits.append(f"the panel of tab `{v['tab']}`" + (f' "{v["tab_text"]}"'
                                                        if v.get("tab_text") else ""))  # fmt: skip
    if v.get("shows"):
        bits.append(f"shows `{v['shows']}`" + (f" ({v['origin']})" if v.get("origin") else ""))
    if v.get("first_seen"):
        bits.append(f"first seen {v['first_seen']}")

    def ways(items) -> str:
        items = items if isinstance(items, list) else [items] if isinstance(items, dict) else []
        return ", ".join(f"{w.get('action')} `{w.get('selector')}` (step {w.get('step')})"
                         for w in items if isinstance(w, dict))  # fmt: skip

    if ways(v.get("opened_by")):
        bits.append("opened by " + ways(v["opened_by"]))
    if ways(v.get("closed_by")):
        bits.append("closed by " + ways(v["closed_by"]))
    if isinstance(v.get("closed"), dict):
        c = v["closed"]
        bits.append(
            f"closed after `{c.get('after')}`" + (f": {c['note']}" if c.get("note") else "")
        )
    if v.get("then"):
        bits.append(f"then {v['then']}")
    if v.get("contains"):
        bits.append("holds " + ", ".join(f"`{x}`" for x in v["contains"]))
    if v.get("build"):
        bits.append(f"build: {v['build']}")
    return "- " + "; ".join(bits) + "."


def _brief_rows(pkg: Pkg, heading: str, first: str) -> list[list[str]]:
    """Rows of a table in the brief whose first cell is `first` (a backticked value)."""
    out: list[list[str]] = []
    inside, table = False, False
    for line in pkg.brief.splitlines():
        if line.startswith(heading):
            inside = True
            continue
        if not inside:
            continue
        if line.startswith("|"):
            table = True
            if line.startswith(f"| `{first}` |"):
                out.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif table or line.startswith("#"):
            break
    return out


def page_doc(pkg: Pkg, sc: Screen) -> str:
    app = pkg.app
    me = sc.file
    snap = _snapshot(pkg, sc)
    nav = sc.nav
    els = pkg.elements_of(sc)
    sel_here = {e["selector"] for e in els} | snap["selectors"]
    lines: list[str] = []
    w = lines.append
    label = {"recorded": "recorded screen", "frame": "recorded screen, shown inside a frame",
             "popup": "recorded screen, shown in a popup window or new tab",
             "stand-in": "stand-in for a page of another origin, shown in a frame",
             "unrecorded": "NOT a recorded screen: a page the link check found, not in scope",
             "code": "RECONSTRUCTED: found only in the application's code (codemap.json), "
                     "never recorded"}[sc.kind]  # fmt: skip
    w(f"# {sc.number:02d}. `{sc.path}` ({sc.name})")
    w("")
    w(f"Kind: **{label}**. In scope: **{'yes' if sc.in_scope else 'no'}**. Generated from the "
      "package by `labctl` (every fact below names its file); do not edit, it is regenerated "
      "with the package. Where this file and the brief differ, the brief is right: say so in "
      "BUILD-NOTES.md.")  # fmt: skip
    w("")
    if sc.kind == "unrecorded":
        w("**Do not build this page.** It is listed so that a link to it is answered as "
          f"`{app}/navigation.json` says (*Links seen, not visited*); a further recording would "
          "bring it into scope.")  # fmt: skip
        w("")
    if sc.kind == "code":
        w("**Reconstructed, not recorded.** The application's scripts name this route, and no "
          "recording opened it, so nothing below is evidence of how it looks or behaves. It is "
          "out of scope: build nothing for it unless the brief puts it in scope.")  # fmt: skip
        w("")
    # --- purpose ------------------------------------------------------------------------------
    w("## Purpose")
    w("")
    title = snap["title"] or nav.get("title")
    heading = snap["h1"] or nav.get("heading")
    if title or heading:
        bits = []
        if title:
            bits.append(f"page title: {_heading_text(title)}")
        if heading:
            bits.append(f"main heading (`h1`): {_heading_text(heading)}")
        src = _link(me, sc.page) if sc.page and pkg.has(sc.page) else f"`{app}/navigation.json`"
        w(f"From {src}: {'; '.join(bits)}. What the screen is for beyond that is not in the "
          "package.")  # fmt: skip
    else:
        w("Unknown: the package has no snapshot, title or heading of this screen (a recording "
          "that opens it would give one).")  # fmt: skip
    w("")
    # --- address -----------------------------------------------------------------------------
    w("## Address")
    w("")
    w(f"- Pattern: `{sc.path}`" + (" (`{...}` is a value: see below)." if "{" in sc.path else "."))
    for m in re.findall(r"\{(\w+)\}", sc.path):
        pat = next((row["doc"].get("path_params", {}).get(m, {}).get("pattern")
                    for _, row, _ in pkg.calls_of(sc.path)
                    if row["doc"].get("path_params", {}).get(m, {}).get("pattern")), None)  # fmt: skip
        if pat:
            w(f"- `{{{m}}}`: the endpoints this screen calls take it with the pattern `{pat}` "
              "(their `path_params`, `api/*.json`).")  # fmt: skip
        else:
            w(f"- `{{{m}}}`: its format is unknown here (an endpoint's `path_params` would say).")
    entry = pkg.nav.get("entry") or {}
    if entry.get("screen") == sc.path:
        w(f"- The recording started here (step {entry.get('step')}).")
    for r in pkg.nav.get("redirects") or []:
        req = str(r.get("request") or "")
        if req.split(" ", 1)[-1] == sc.path:
            w(
                f"- `{req}` answered `{r.get('status')}` to `{r.get('to')}` "
                f"({r.get('when')}), {r.get('times')} time(s) (`{app}/navigation.json` redirects)."
            )
    ses = pkg.session or {}
    if ses and (ses.get("sign_in") or {}).get("screen") == sc.path:
        w(f"- This is the sign-in screen: it opens without a session (`{app}/session.json`).")
    elif ses and sc.in_scope:
        for u in ses.get("without_session") or []:
            if str(u.get("request", "")).split(" ", 1)[-1] == sc.path:
                w(f"- Without a session ({u.get('when')}): `{u.get('request')}` answered "
                  f"`{u.get('status')}`" + (f", to `{u['to']}`" if u.get("to") else "")
                  + f" (`{app}/session.json`).")  # fmt: skip
        if not any(str(u.get("request", "")).split(" ", 1)[-1] == sc.path
                   for u in ses.get("without_session") or []):  # fmt: skip
            w(
                f"- Needs a session, as every screen but the sign-in screen (`{app}/BRIEF.md` "
                "*Sign-in and session*); what this address answers without one was not recorded."
            )
    if sc.kind == "unrecorded":
        if nav.get("status"):
            w(f"- The real application answered `{nav['status']}` (link check).")
        if nav.get("linked_from"):
            w("- Linked from " + ", ".join(f"`{x}`" for x in nav["linked_from"]) + ".")
    w("")
    # --- ways in and out ---------------------------------------------------------------------
    w("## How you get here and where you can go")
    w("")
    w(f"From `{app}/navigation.json`:")
    w("")
    ins = nav.get("ways_in") or []
    outs = nav.get("ways_out") or []
    for x in ins:
        w(f"- In: from `{x.get('from')}` by {x.get('by')} `{x.get('selector')}`"
          + (f' "{x["text"]}"' if x.get("text") else "")
          + f" (steps {', '.join(map(str, x.get('steps') or []))}).")  # fmt: skip
    if sc.host:
        w(f"- In: loaded in `{sc.host[1]}` on `{sc.host[0]}` "
          f"(see {_link(me, pkg.screen(sc.host[0]).file)}).")  # fmt: skip
    for x in outs:
        w(f"- Out: to `{x.get('to')}` by {x.get('by')} `{x.get('selector')}`"
          + (f' "{x["text"]}"' if x.get("text") else "")
          + f" (steps {', '.join(map(str, x.get('steps') or []))}).")  # fmt: skip
    for x in nav.get("links_seen_not_visited") or []:
        w(f"- Link seen, not visited: `{x.get('selector')}`"
          + (f' "{x["text"]}"' if x.get("text") else "")
          + f" -> `{x.get('target')}` ({x.get('status')}): {x.get('answer')}.")  # fmt: skip
    if not (ins or outs or sc.host or nav.get("links_seen_not_visited")):
        w("- None recorded.")
    w("")
    # --- picture and layout ------------------------------------------------------------------
    w("## Layout and the picture to match")
    w("")
    lk = pkg.look_of(sc)
    if lk.get("picture"):
        pic = f"{app}/look/{lk['picture']}"
        w(f"- Picture: {_link(me, pic)} ({lk.get('width')} x {lk.get('height')} pixels): your "
          "screen must match it in layout, colours, spacing and fonts.")  # fmt: skip
    else:
        w("- Picture: unknown: the package has no picture of this screen (`look/screens/` is "
          "written by the look step when it renders the snapshot).")  # fmt: skip
    if sc.page and pkg.has(sc.page):
        what = "reference snapshot" if sc.kind == "unrecorded" else "snapshot"
        w(f"- Page {what}: {_link(me, sc.page)}: real structure, ids, classes and interface "
          "text; data masked (letters x, digits 9).")  # fmt: skip
    else:
        w("- Page snapshot: none in the package.")
    for c in pkg.look.get("inline_css") or []:
        if c.get("screen") == sc.name:
            w(f"- Styles written into the page: {_link(me, app + '/look/' + c['file'])}.")
    if pkg.look.get("css"):
        w(f"- Style sheets of the application: `{app}/look/css/` (see `{app}/look/manifest.json`).")
    layout = pkg.theme.get("layout") or {}
    page_box = next((p for p in layout.get("pages") or [] if p.get("screen") == sc.name), None)
    if page_box:
        w(f"- Page: content width {page_box.get('content_width')} px (max-width "
          f"{page_box.get('content_max_width')}), page height {page_box.get('page_height')} px "
          f"(`{app}/look/theme.json` layout).")  # fmt: skip
    regions = [r for r in layout.get("regions") or [] if r.get("screen") == sc.name]
    if regions:
        w("")
        w("Regions, as rendered (`look/theme.json` `layout.regions`):")
        w("")
        w("| Region | x | y | Width | Height | Display |")
        w("|---|---|---|---|---|---|")
        for r in regions:
            w(f"| `{r.get('selector')}` | {r.get('x')} | {r.get('y')} | {r.get('width')} | "
              f"{r.get('height')} | {r.get('display')} |")  # fmt: skip
    texts = pkg.texts.get(sc.name) or []
    lit = [t for t in texts if t.get("is") == "literal"]
    data = [t for t in texts if t.get("is") == "data"]
    mixed = [t for t in texts if t.get("is") == "mixed"]
    if texts:
        w("")
        w(f"Literal texts, copy exactly (`{app}/look/texts.json`):")
        w("")
        for t in lit:
            w(f"- `{t.get('where')}`: {_code(t.get('text'))}")
        for t in mixed:
            w(f"- `{t.get('where')}`: {_code(t.get('text'))} (literal words around data)")
        if data:
            w("- Placeholders for data (show the record's value there): "
              + ", ".join(sorted({f"`{t.get('where')}`" for t in data})) + ".")  # fmt: skip
    w("")
    # --- elements ----------------------------------------------------------------------------
    w("## Elements")
    w("")
    if els:
        w(f"Every interactive element, in page order (`{app}/navigation.json`):")
        w("")
        w("| Selector | Text or label | Type | Inside | Accepts or options | In the recording |")
        w("|---|---|---|---|---|---|")
        for e in els:
            acc = "; ".join(f"{k} {v}" if v is not True else k
                            for k, v in (e.get("accepts") or {}).items())  # fmt: skip
            opts = ", ".join(f"{_code(o.get('value'))} {o.get('label')}"
                             for o in e.get("options") or [])  # fmt: skip
            more = "; ".join(x for x in (acc, opts) if x)
            if e.get("target"):
                more = (more + "; " if more else "") + f"link to `{e['target']}`"
            if e.get("repeated"):
                more = (more + "; " if more else "") + f"repeated, up to {e['repeated']} at once"
            if e.get("seen_disabled"):
                more = (more + "; " if more else "") + "seen disabled"
            sel = f"`{e['selector']}`" + (f" (on the host: `{e['_host']}`)" if e.get("_host")
                                          else "")  # fmt: skip
            w(f"| {_cell(sel)} | {_cell(e.get('label'))} | {_cell(e.get('type'))} | "
              f"{_cell(', '.join(e.get('inside') or [])) or '-'} | {_cell(more) or '-'} | "
              f"{_cell(_used_summary(e.get('used')))} |")  # fmt: skip
        w("")
    elif sc.kind in ("recorded", "popup", "stand-in", "frame"):
        w("No interactive element was recorded on this screen.")
        w("")
    others = [i for i in snap["ids"] if i["selector"] not in {e["selector"] for e in els}]
    if others:
        w("Every other element with an id or a name in the snapshot (keep each one):")
        w("")
        w("| Selector | Tag | Text in the snapshot | Hidden in the snapshot |")
        w("|---|---|---|---|")
        for i in others:
            w(f"| `{i['selector']}` | {i['tag']} | {_cell(i['text']) or '-'} | "
              f"{'yes' if i['hidden'] else 'no'} |")  # fmt: skip
        w("")
    if not els and not others:
        w("Unknown: the package lists no element of this screen.")
        w("")
    # --- actions -----------------------------------------------------------------------------
    acts = _action_lines(els)
    w("## What each action did")
    w("")
    if acts:
        w(f"In step order (`{app}/navigation.json` `used`; step numbers of "
          f"`{app}/steps/<recording>.json`):")  # fmt: skip
        w("")
        lines += acts
    else:
        w("No action was recorded on this screen.")
    unused = [e["selector"] for e in els if isinstance(e.get("used"), str)]
    if unused:
        w("")
        w("Seen, not used (render as the snapshot has it, give it no behaviour the package does "
          "not state): " + ", ".join(f"`{u}`" for u in unused) + ".")  # fmt: skip
    w("")
    # --- sub-views ---------------------------------------------------------------------------
    subs = nav.get("subviews") or []
    w("## Dialogs, tabs, frames and popup windows")
    w("")
    if subs:
        w(f"Each opens and closes as recorded, on the same element (`{app}/navigation.json`):")
        w("")
        lines += [_sub_line(v) for v in subs]
    else:
        w("None recorded on this screen.")
    if sc.host:
        w(f"- This screen is itself shown in `{sc.host[1]}` on `{sc.host[0]}`.")
    w("")
    # --- rules -------------------------------------------------------------------------------
    sr = pkg.rules_of(sc.path)
    w("## Field rules and page behaviour")
    w("")
    if pkg.has(f"{app}/rules.json"):
        rows = _rule_rows(sr)
        if rows:
            levels = ", ".join(f"`{k}`" for k in pkg.rules.get("levels") or {})
            w(f"From `{app}/rules.json` (levels: {levels}). Implement each rule at level `seen` "
              "or `declared` exactly, with its message letter for letter, and each rule `in "
              "code, not triggered` too.")  # fmt: skip
            w("")
            w("| Field | Rule | Message | Level | Evidence |")
            w("|---|---|---|---|---|")
            lines += rows
            w("")
        for c in sr.get("cross_field") or []:
            w(f"- Between fields: {c.get('rule')} "
              + ", ".join(f"`{f}`" for f in c.get("fields") or [])
              + (f': "{c["message"]}"' if c.get("message") else "") + f" ({c.get('level')}).")  # fmt: skip
        for b in sr.get("behaviour") or []:
            who = b.get("field") or b.get("source")
            w(f"- Behaviour ({b.get('kind')}): " + (f"`{who}`" if who else "the form")
              + (f" -> `{b['target']}`" if b.get("target") else "")
              + f": {b.get('what')}" + (f", from `{b['api']}`" if b.get("api") else "")
              + (f" ({_link(me, app + '/' + b['api_file'])})" if b.get("api_file") else "")
              + f" ({b.get('level')}).")  # fmt: skip
        for s in sr.get("on_load") or []:
            w(f"- On load: `{s.get('selector')}` {s.get('what')} ({s.get('level')}).")
        for f in sr.get("fields") or []:
            if f.get("title") or f.get("placeholder"):
                hint = f.get("title") or f.get("placeholder")
                w(f'- Hint on `{f.get("selector")}`: "{hint}".')
        for t in pkg.rules.get("session") or []:
            w(f"- Session timer (every screen): {t.get('what')}"
              + (": " + "; ".join(f'"{m}"' for m in t["messages"]) if t.get("messages") else "")
              + (f", then `{t['goes_to']}`" if t.get("goes_to") else "")
              + f" ({t.get('level')}).")  # fmt: skip
        if not (rows or sr.get("cross_field") or sr.get("behaviour") or sr.get("on_load")):
            w("No field rule or page behaviour was found for this screen "
              f"(`{app}/rules.json`).")  # fmt: skip
    else:
        w(f"Unknown: the package has no `{app}/rules.json`.")
    w("")
    # --- API calls ---------------------------------------------------------------------------
    calls = pkg.calls_of(sc.path)
    w("## API calls of this screen")
    w("")
    if calls:
        w("| Endpoint | Sent by | Steps | Answers | Authorised by | File |")
        w("|---|---|---|---|---|---|")
        for name, row, t in calls:
            by = f"{t.get('action')}" + (f" `{t['selector']}`" if t.get("selector") else "")
            by += f" (key {t['key']})" if t.get("key") else ""
            by += f" (after {t['after']})" if t.get("after") else ""
            w(f"| `{name}` | {_cell(by)} | {', '.join(map(str, t.get('steps') or []))} | "
              f"{', '.join(row.get('statuses') or [])} | {_cell(row.get('auth'))} | "
              f"{_link(me, app + '/' + row['file'])} |")  # fmt: skip
        w("")
        for name, row in {n: r for n, r, _ in calls}.items():
            doc = row["doc"]
            req = doc.get("request") or {}
            props = list(((req.get("schema") or {}).get("properties") or {}))
            if props:
                ctl = {k: v.get("control") for k, v in (req["schema"]["properties"]).items()}
                w(f"- `{name}` sends {req.get('content_type') or 'a body'}: "
                  + ", ".join(f"`{k}`" + (f" (from `{ctl[k]}`)" if ctl.get(k) else "")
                              for k in props) + ".")  # fmt: skip
            q = doc.get("query") or {}
            if q:
                w(f"- `{name}` query: " + ", ".join(
                    f"`{k}`" + (f" (from `{v.get('control')}`)" if isinstance(v, dict)
                                and v.get("control") else "") for k, v in q.items()) + ".")  # fmt: skip
            for status, r in (doc.get("responses") or {}).items():
                if r.get("body") == "none":
                    w(f"- `{name}` answer `{status}` has no body.")
                elif r.get("body") and not r.get("schema"):
                    w(f"- `{name}` answer `{status}`: {r['body']}.")
                if r.get("sets_cookie"):
                    w(f"- `{name}` answer `{status}` sets the cookie "
                      + ", ".join(f"`{c}`" for c in r["sets_cookie"])
                      + " (attributes: ARCHITECTURE.md *Sign-in, session and cookies*).")  # fmt: skip
            for n in doc.get("notes") or []:
                w(f"- `{name}`: {n}")
        lists = [
            x for x in pkg.cat.get("lists") or [] if x.get("endpoint") in {n for n, _, _ in calls}
        ]
        for x in lists:
            w(f"- List `{x['endpoint']}`: " + (f"{x['page_size']} row(s) per page; "
                                               if x.get("page_size") else "")
              + (f"beside the rows it answers {', '.join(f'`{f}`' for f in x['answer_fields_beside_the_rows'])}"
                 if x.get("answer_fields_beside_the_rows") else "rows only") + ".")  # fmt: skip
            for p, info in (x.get("parameters") or {}).items():
                obs = "; ".join(info.get("observed") or [])
                w(f"  - `{p}`" + (f" (set by `{info['control']}`)" if info.get("control") else "")
                  + f": {info.get('role')}" + (f"; {info['empty']}" if info.get("empty") else "")
                  + (f"; {obs}" if obs else "") + ".")  # fmt: skip
    else:
        w(
            "None: the recording shows no API call sent from this screen"
            + (" (`api/catalogue.json`)." if pkg.cat else " (the package has no API catalogue).")
        )
    for c in _code_calls(pkg.codemap, sc.path) if sc.kind == "code" else []:
        w(f"- In the code (reconstructed, not recorded): `{c.get('method', '?')} "
          f"{c.get('path') or c.get('url') or '?'}` (`{app}/{CODEMAP}`).")  # fmt: skip
    w("")
    # --- data --------------------------------------------------------------------------------
    w("## Data shown and where it lives")
    w("")
    fmap = []
    for f in pkg.cat.get("field_map") or []:
        here = [s for s in f.get("screen") or [] if s.split(" ", 1)[0] in sel_here]
        if here:
            fmap.append((f, here))
    if fmap:
        w(f"From `{app}/api/catalogue.json` `field_map`:")
        w("")
        w("| Table.column | API field | On this screen | Basis |")
        w("|---|---|---|---|")
        for f, here in fmap:
            w(f"| `{f.get('table')}.{f.get('column')}` | `{f.get('api_field')}` | "
              + ", ".join(f"`{s}`" for s in here) + f" | {f.get('basis')} |")  # fmt: skip
        w("")
    fmt = _brief_rows(pkg, "**What the pages show", sc.path)
    if fmt:
        w(f"How each value is shown (`{app}/BRIEF.md` *What the pages show, and in which "
          "format*):")  # fmt: skip
        w("")
        for row in fmt:
            if len(row) >= 4:
                w(f"- {row[1]}: {row[2]}; format: {row[3]}.")
        w("")
    if not fmap and not fmt:
        w("Nothing on this screen is mapped to a table in the package.")
        w("")
    # --- seed --------------------------------------------------------------------------------
    w("## Seed rows the recorded flow needs")
    w("")
    tables = sorted({row.get("table") for _, row, _ in calls if row.get("table")}
                    | {f.get("table") for f, _ in fmap if f.get("table")})  # fmt: skip
    said = False
    for name in tables:
        t = pkg.table(name)
        if t is None:
            continue
        said = True
        if t["seed_rows"] is None:
            w(f"- `{name}`: no seed rows in the package.")
            continue
        key = t["keys"][0] if t["keys"] else None
        ids = sorted(
            str(r.get(key)) for r in t["rows"] if isinstance(r, dict) and key and r.get(key)
        )
        span = f", `{key}` from `{ids[0]}` to `{ids[-1]}`" if ids else ""
        w(f"- `{name}`: {t['seed_rows']} row(s) in `{app}/db/seed.sql` (`db/seed.json`){span}.")
    records = []
    for e in els:
        for u in e.get("used") if isinstance(e.get("used"), list) else []:
            if u.get("record") and u["record"] not in records:
                records.append(u["record"])
    for rec in records:
        holder = next((t["name"] for t in pkg.tables for r in t["rows"]
                       if isinstance(r, dict) and rec in [str(v) for v in r.values()]), None)  # fmt: skip
        maker = next((name for name, row in pkg.endpoints.items() if name.startswith("POST ")
                      for c in row["doc"].get("calls") or []
                      if 200 <= int(c.get("status") or 0) < 300
                      and rec in json.dumps(c.get("response"), ensure_ascii=False)), None)  # fmt: skip
        said = True
        w(f"- The recording opened the record `{rec}`: "
          + (f"a seed row of `{holder}`." if holder else
             f"not a seed row: `{maker}` makes it earlier in the recorded flow (its file's "
             "`calls`)." if maker else "not a seed row, and the package does not show where "
             "it comes from (unknown)."))  # fmt: skip
    if ses and (ses.get("sign_in") or {}).get("screen") == sc.path:
        users = pkg.table((ses.get("tables") or {}).get("users") or "app_users")
        if users is not None:
            said = True
            w(f"- `{users['name']}`: {users['seed_rows'] or 0} row(s): the user the replay signs "
              f"in as (the values of `{app}/steps/*.values.yaml` on the sign-in fields; the "
              "password stored as a hash).")  # fmt: skip
    for rec_name, rows in pkg.values.items():
        for n in sorted(rows):
            r = rows[n]
            if (r.get("field") in sel_here and "seed" in str(r.get("why") or "")
                    and _step_screen(pkg, rec_name, n) == sc.path):  # fmt: skip
                said = True
                w(f"- Step {n} (`{rec_name}`): `{r.get('field')}` = `{r.get('value')}`: "
                  f"{r.get('why')}.")  # fmt: skip
    if not said:
        w("None: no table is read or written from this screen.")
    w("")
    # --- checks and replay -------------------------------------------------------------------
    w("## Acceptance checks")
    w("")
    checks = []
    if sc.kind in ("unrecorded", "code"):
        checks.append(f"Nothing is built at `{sc.path}`; it is answered as "
                      f"`{app}/navigation.json` says for a link to it.")  # fmt: skip
    else:
        checks.append(f"`{sc.path}` opens" + (" with a value the seed holds" if "{" in sc.path
                                               else "") + " and every selector under *Elements* "
                      "resolves on it, with the same tag, id, classes and text.")  # fmt: skip
        if lit:
            checks.append(f"Each of the {len(lit)} literal text(s) under *Layout* appears exactly.")
        if lk.get("picture"):
            view = pkg.look.get("viewport") or {}
            checks.append(f"At {view.get('width')} x {view.get('height')} pixels it matches "
                          f"`look/{lk['picture']}` in layout, colours, spacing and fonts.")  # fmt: skip
        if outs or ins:
            checks.append("Each way in and out under *How you get here* works on the same element.")
        if subs:
            checks.append(f"Each of the {len(subs)} sub-view(s) opens and closes on its element.")
        if acts:
            checks.append("Each recorded action, replayed on `db/seed.sql`, gives the outcome under "
                          "*What each action did*: the same requests, messages and next page.")  # fmt: skip
        nrules = len(_rule_rows(sr))
        if nrules:
            checks.append(f"Each of the {nrules} rule(s) under *Field rules* behaves as stated, "
                          "its message letter for letter.")  # fmt: skip
        if calls:
            checks.append("Each endpoint under *API calls* answers the requests of its file's "
                          "`calls` with their statuses and keys.")  # fmt: skip
    lines += [f"{n}. {c}" for n, c in enumerate(checks, 1)]
    w("")
    replay = [(rec, s) for rec, steps in pkg.steps for s in steps if s.get("screen") == sc.path]
    w("## Replay steps on this screen")
    w("")
    if replay:
        w(f"From `{app}/steps/<recording>.json` and `<recording>.values.yaml` (the acceptance "
          "run replays them in order):")  # fmt: skip
        w("")
        for rec, s in replay:
            v = pkg.values.get(rec, {}).get(s.get("n")) or {}
            what = f"{s.get('action')}" + (f" `{s['selector']}`" if s.get("selector") else "")
            if s.get("key"):
                what += f" (key {s['key']})"
            if s.get("typed_shape"):
                what += f", typed shape `{s['typed_shape']}`"
            if "value" in v:
                what += f", replay value `{v['value']}`"
            if s.get("requests"):
                what += "; requests " + ", ".join(f"`{r}`" for r in s["requests"])
            if s.get("then"):
                what += f"; then `{s['then']}`"
            for m in s.get("messages") or []:
                if isinstance(m, dict):
                    what += f'; `{m.get("selector")}` "{m.get("text")}"'
            if v.get("recorded"):
                what += f" (recorded: {v['recorded']})"
            w(f"- `{rec}` step {s.get('n')}: {what}.")
    else:
        w("None: no recorded step was taken on this screen.")
    w("")
    # --- open questions ----------------------------------------------------------------------
    w("## Open questions")
    w("")
    qs = []
    for line in pkg.section("Open questions for the reviewer").splitlines():
        if line.startswith("- ") and (f" {sc.path} " in line + " " or f"`{sc.path}`" in line
                                      or any(f"{s} on {sc.path}" in line for s in sel_here)):  # fmt: skip
            qs.append(line[2:])
    for e in els:
        # a link says where it leads; a control nobody used does not say what it does
        if e.get("used") == "seen, not used" and not e.get("target") and not any(
                e["selector"].split(" >>> ")[-1] in q for q in qs):  # fmt: skip
            qs.append(f"`{e['selector']}` was never used: what it does is unknown (render it, give "
                      "it no behaviour).")  # fmt: skip
    for name, row, _ in calls:
        for status, r in (row["doc"].get("responses") or {}).items():
            if str(r.get("body") or "").startswith("not recorded") and not r.get("schema"):
                q = f"`{name}` answer `{status}`: its body is unknown (not recorded)."
                if q not in qs:
                    qs.append(q)
    for v in subs:
        if isinstance(v.get("closed"), dict) and "hypothesis" in str(v["closed"].get("note")):
            qs.append(f"How `{v.get('shows')}` closes is a hypothesis: {v['closed'].get('note')}.")
    if not lk.get("picture") and sc.kind not in ("unrecorded", "code"):
        qs.append("No picture of this screen: its look is only what the snapshot's styles give.")
    lines += [f"- {q}" for q in qs] if qs else ["None found in the package."]
    w("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"


def _step_screen(pkg: Pkg, rec: str, n: int) -> str | None:
    for name, steps in pkg.steps:
        if name == rec:
            for s in steps:
                if s.get("n") == n:
                    return s.get("screen")
    return None


# =================================================================================================
# The constitution, the architecture, the plan, the glossary
# =================================================================================================


def _header(title: str, what: str) -> list[str]:
    return [f"# {title}", "", what + " Generated from the package by `labctl`; every fact names "
            "the file it comes from. Do not edit it: it is regenerated with the package.", ""]  # fmt: skip


def constitution_doc(pkg: Pkg) -> str:
    app = pkg.app
    packaged = set(pkg.files)
    values = any(p.startswith(f"{app}/steps/") and p.endswith(".values.yaml") for p in packaged)
    lines = _header(
        f"Constitution: `{pkg.app_id}`",
        'The rules of this build that are not negotiable, and what "done" means.',
    )
    lines += [
        "These rules are the same text as *Rules that are not negotiable* in `BUILD_TASK.md`: "
        "both are written by one function, so they cannot differ. `AGENTS.md` and "
        "`docs/factory-playbook.md` are the lab's general rules; they apply too.",
        "",
        "## Rules",
        "",
        rules_text(app, packaged, pkg.browser, seed_loaded=f"{app}/db/seed.sql" in packaged),
        "",
        "## What \"done\" means",
        "",
        done_text(app, packaged, pkg.browser, values),
        "",
        "## Where to write what you decide",
        "",
        f"- `{app}/BUILD-NOTES.md`, under **Assumptions**: one line per thing the package did not "
        "say, what you chose, and the file where you built it.",
        f"- Never edit a file of the package (`{app}/BRIEF.md`, `{app}/db/`, `{app}/{DIR}/`, "
        f"`{app}/navigation.json`, `{app}/rules.json`, `{app}/api/`, `{app}/steps/`, "
        f"`{app}/look/`). If one is wrong, say so in BUILD-NOTES.md.",
        "",
    ]  # fmt: skip
    return "\n".join(lines)


def _stack_rows(pkg: Pkg) -> list[str]:
    body = pkg.section("Stack to pin")
    return body.splitlines() if body.strip() else []


def architecture_doc(pkg: Pkg) -> str:
    app = pkg.app
    me = f"{app}/{DIR}/{ARCHITECTURE}"
    lines = _header(
        f"Architecture: `{pkg.app_id}`",
        "How the application is put together, as far as the package shows it.",
    )
    w = lines.append
    # --- stack -------------------------------------------------------------------------------
    w("## Stack")
    w("")
    stack = pkg.conf.get("stack") or {}
    fe = stack.get("frontend") or {}
    db = (stack.get("db") or {}).get("engine")
    dialect = re.search(r"^-- Dialect: ([^\n]+)", pkg.schema, re.M)
    w(f"From `{app}/app.yaml` `stack`:")
    w("")
    w(f"- Front end: {('`' + str(fe['framework']) + '`') if fe.get('framework') else 'unknown'}"
      + (f" ({fe['marker']})" if fe.get("marker") else "")
      + (f", version `{fe['version']}`" if fe.get("version") else "") + ".")  # fmt: skip
    if not fe:
        w("  Unknown: `app.yaml` names no front end (the brief's *Stack to pin* says why).")
    for part, value in stack.items():
        if part in ("frontend", "db", "data") or not value:
            continue
        if isinstance(value, dict):
            value = ", ".join(
                f"{k} `{v}`" for k, v in value.items() if not isinstance(v, (dict, list))
            )
        w(f"- {part.replace('_', ' ').capitalize()}: {value}.")
    w("- Database: " + (f"`{db}`" if db else "unknown") + (
        f"; `db/schema.sql` says: {dialect.group(1).strip().rstrip('.')}" if dialect else "")
      + ".")  # fmt: skip
    if pkg.browser:
        w("- Data: kept in the browser (`stack.data.kind: browser`): there is no server database.")
    rows = _stack_rows(pkg)
    if rows:
        w("")
        w(f"From `{app}/BRIEF.md` *Stack to pin* (versions as detected; where it says *not "
          "identified*, the version is unknown and the brief's default applies):")  # fmt: skip
        w("")
        lines += rows
    w("")
    # --- layers ------------------------------------------------------------------------------
    w("## Layers")
    w("")
    w("| Layer | Responsibility | From |")
    w("|---|---|---|")
    w(
        f"| Screens (browser) | The pages of the route table below, with the snapshot's structure, "
        f"ids, classes and texts, styled as `look/` shows | `{app}/pages/`, `{app}/look/` |"
    )
    if pkg.cat:
        w(f"| API (server) | The endpoints of the API surface below, on the same methods, paths, "
          f"keys and statuses | `{app}/api/` |")  # fmt: skip
    if pkg.session:
        w(
            f"| Sign-in and session (server) | Users checked against `app_users`, sessions as rows "
            f"of `app_sessions`, the cookies below | `{app}/session.json`, `{app}/BRIEF.md` |"
        )
    if pkg.browser:
        w(f"| Storage (browser) | The data, under the keys the brief's *Browser storage* names | "
          f"`{app}/BRIEF.md` |")  # fmt: skip
    else:
        w(f"| Database | The tables of the data model below, created from `db/schema.sql`, the "
          f"seed loaded at start | `{app}/db/` |")  # fmt: skip
    w(
        "| Container | One Dockerfile, one process serving the screens and the API on one port | "
        "`AGENTS.md`, `docs/factory-playbook.md` step 7 |"
    )
    w("")
    # --- routes ------------------------------------------------------------------------------
    w("## Route table")
    w("")
    w(f"Every screen, recorded or not (`{app}/navigation.json`; page files in `{DIR}/{PAGES}/`):")
    w("")
    w("| Address | Screen | Kind | In scope | Page file |")
    w("|---|---|---|---|---|")
    for sc in pkg.screens:
        w(f"| `{sc.path}` | {sc.name} | {sc.kind} | {'yes' if sc.in_scope else 'no'} | "
          f"{_link(me, sc.file)} |")  # fmt: skip
    outside = {}
    for s in pkg.nav.get("screens") or []:
        for x in s.get("links_seen_not_visited") or []:
            if x.get("status") != "in scope" and x.get("target"):
                outside.setdefault(x["target"], x.get("answer"))
    if outside:
        w("")
        w("Addresses linked from the screens and not built (each keeps its link and is answered "
          "as said):")  # fmt: skip
        w("")
        for target, answer in outside.items():
            w(f"- `{target}`: {answer}.")
    redirects = pkg.nav.get("redirects") or []
    for r in redirects:
        w(f"- Redirect: `{r.get('request')}` answered `{r.get('status')}` to `{r.get('to')}` "
          f"({r.get('when')}).")  # fmt: skip
    run = pkg.conf.get("run") or {}
    health = (run.get("api") or {}).get("health")
    w(f"- Health route: `{health}` (`app.yaml` `run.api.health`)." if health else
      "- Health route: not in the evidence; `GET /api/health` (the task's default, "
      "`BUILD_TASK.md`), answering 200 only when the database is reachable.")  # fmt: skip
    w("")
    # --- API surface -------------------------------------------------------------------------
    w("## API surface")
    w("")
    if pkg.endpoints:
        w(f"From `{app}/api/catalogue.json`; each endpoint's file has its schemas and every "
          "recorded call:")  # fmt: skip
        w("")
        w("| Endpoint | Authorised by | Query | Request fields | Answers | Table | File |")
        w("|---|---|---|---|---|---|---|")
        for name, row in pkg.endpoints.items():
            w(f"| `{name}` | {_cell(row.get('auth'))} | "
              f"{', '.join(f'`{q}`' for q in row.get('query') or []) or '-'} | "
              f"{', '.join(f'`{q}`' for q in row.get('request_fields') or []) or '-'} | "
              f"{', '.join(row.get('statuses') or [])} | "
              f"{('`' + row['table'] + '`') if row.get('table') else '-'} | "
              f"{_link(me, app + '/' + row['file'])} |")  # fmt: skip
    else:
        w("No API call was recorded (no `api/catalogue.json`).")
    w("")
    # --- data model --------------------------------------------------------------------------
    w("## Data model")
    w("")
    if pkg.tables:
        w(f"From `{app}/db/schema.sql` (create it unchanged) and `{app}/db/seed.json`:")
        w("")
        w("| Table | Columns | Key | Seed rows |")
        w("|---|---|---|---|")
        for t in pkg.tables:
            cols = ", ".join(f"`{c}`" + (f" {t['types'][c]}" if t["types"].get(c) else "")
                             for c in t["columns"])  # fmt: skip
            w(f"| `{t['name']}` | {cols} | {', '.join(f'`{k}`' for k in t['keys']) or 'none'} | "
              f"{t['seed_rows'] if t['seed_rows'] is not None else 'no seed'} |")  # fmt: skip
        w("")
        rel = [f"- `{t['name']}.{r['column']}` references `{r['table']}({r['to']})` "
               "(`db/schema.sql`)." for t in pkg.tables for r in t["refs"]]  # fmt: skip
        for name, row in pkg.endpoints.items():
            for n in row["doc"].get("notes") or []:
                if "belongs to" in n:
                    rel.append(f"- `{name}`: {n}")
        w("Relations:")
        w("")
        lines += rel or ["- None declared: the schema has no foreign key and no endpoint "
                         "nests one record in another."]  # fmt: skip
        fmap = pkg.cat.get("field_map") or []
        if fmap:
            w("")
            w(f"Field map (`{app}/api/catalogue.json` `field_map`): which API field each column "
              "is, and where the screens show it:")  # fmt: skip
            w("")
            w("| Table.column | API field | On the screens |")
            w("|---|---|---|")
            for f in fmap:
                w(f"| `{f.get('table')}.{f.get('column')}` | `{f.get('api_field')}` | "
                  + (", ".join(f"`{s}`" for s in f.get("screen") or []) or "-") + " |")  # fmt: skip
    else:
        w("No table: `db/schema.sql` has none" + (" (the data is in the browser)." if pkg.browser
                                                  else "."))  # fmt: skip
    w("")
    # --- session -----------------------------------------------------------------------------
    lines += session_lines(pkg)
    # --- run ---------------------------------------------------------------------------------
    w("## How it runs and is configured")
    w("")
    db_var = (run.get("env") or {}).get("database_url") or "DATABASE_URL"
    for part in ("web", "api"):
        p = run.get(part) or {}
        if p.get("port"):
            w(f"- `{part}` listens on container port {p['port']} (`app.yaml` `run.{part}`).")
    if not (run.get("web") or run.get("api")):
        w("- Ports: unknown: `app.yaml` `run:` is empty; you choose the container port and fill "
          "`run:` (`BUILD_TASK.md`, section 3).")  # fmt: skip
    if not pkg.browser:
        w(
            f"- The database address is read from `{db_var}` (`app.yaml` `run.env.database_url`, "
            "else the lab's default); the lab also sets `DB_ENGINE`, `DB_HOST`, `DB_PORT`, "
            "`DB_NAME` (Oracle: `DB_SERVICE`), `DB_USER`, `DB_PASSWORD` and `DB_CONNECTION_STRING` "
            "(`BUILD_TASK.md`, section 3, step 5). On SQLite `DATABASE_URL` is `sqlite:////path`."
        )
    for line in pkg.section("Running in the lab").splitlines():
        if line.startswith("- "):
            w(f"{line} (`BRIEF.md` *Running in the lab*)")
    w("")
    # --- cross-app ---------------------------------------------------------------------------
    seq = [rel for rel in pkg.files if rel.startswith(f"{app}/steps/")
           and rel.endswith(".sequence.yaml")]  # fmt: skip
    cross = pkg.section("Part of a flow across applications")
    apps = sorted({a for rel in seq for a in ((pkg.yaml(rel) or {}).get("apps") or [])})
    if cross or len(apps) > 1:
        w("## Flow across applications")
        w("")
        if apps:
            w("Applications of the recording: " + ", ".join(f"`{a}`" for a in apps)
              + f" (`{', '.join(seq)}`).")  # fmt: skip
        for rel in seq:
            doc = pkg.yaml(rel) or {}
            for h in doc.get("handovers") or []:
                w(f"- Handover: `{json.dumps(h, ensure_ascii=False)}`.")
        if cross:
            w("")
            w(f"From `{app}/BRIEF.md` *Part of a flow across applications*:")
            w("")
            lines += cross.splitlines()
        w("")
        w("The other applications are built from their own packages; do not build them here.")
        w("")
    # --- diagram -----------------------------------------------------------------------------
    w("## Diagram")
    w("")
    shown = [s.path for s in pkg.screens if s.in_scope]
    w("```text")
    w("  Browser")
    for p in shown[:12]:
        w(f"    screen {p}")
    if len(shown) > 12:
        w(f"    ... {len(shown) - 12} more (route table)")
    w("      |  HTTP, one port")
    w("      v")
    w(
        f"  One container: {fe.get('framework') or 'front end'} screens + "
        f"{len(pkg.endpoints)} API endpoint(s)"
    )
    if pkg.session:
        w(f"      sign-in and session ({(pkg.session.get('session') or {}).get('mechanism')})")
    if not pkg.browser:
        w(f"      |  SQL ({db_var}, DB_ENGINE)")
        w("      v")
        w(f"  Database ({db or 'engine unknown'}): "
          + (", ".join(t["name"] for t in pkg.tables) or "no table"))  # fmt: skip
    else:
        w("  (data in browser storage)")
    w("```")
    w("")
    return "\n".join(lines)


def session_lines(pkg: Pkg) -> list[str]:
    """ARCHITECTURE's section on sign-in, the session and every cookie."""
    from labctl import signin

    app = pkg.app
    out = ["## Sign-in, session and cookies", ""]
    ses = pkg.session
    if not ses:
        body = pkg.section("Sign-in and session")
        out += [("Unknown: the package has no `session.json`. " if not body else "")
                + f"`{app}/BRIEF.md` *Sign-in and session* says:", ""]  # fmt: skip
        out += (body.splitlines() if body else ["(no such section)"]) + [""]
        return out
    si = ses.get("sign_in") or {}
    call = si.get("call") or {}
    out.append(f"From `{app}/session.json`:")
    out.append("")
    fields = ", ".join(f"`{f.get('selector')}`" + (" (the password)" if f.get("password") else "")
                       for f in si.get("fields") or [])  # fmt: skip
    out.append(f"- Sign-in screen `{si.get('screen')}`" + (f", fields {fields}" if fields else "")
               + (f"; the call `{call.get('method')} {call.get('path')}` with {call.get('body')} "
                  + ", ".join(f"`{f}`" for f in call.get("fields") or []) if call else "")
               + ".")  # fmt: skip
    for a in si.get("attempts") or []:
        verdict = "accepted" if a.get("accepted") else "refused"
        out.append(f"- Step {a.get('step')} (`{a.get('by')}`): {verdict}, status `{a.get('status')}`"
                   + (f", sent {', '.join(f'`{k}`' for k in a['sent_empty'])} empty"
                      if a.get("sent_empty") else "")
                   + (f", landed on `{a['lands_on']}`" if a.get("lands_on") else "")
                   + (", showed " + "; ".join(a["messages"]) if a.get("messages") else "") + ".")  # fmt: skip
    s = ses.get("session") or {}
    out.append(f"- Session mechanism: {s.get('mechanism')}"
               + (f" (cookie {', '.join(f'`{c}`' for c in s.get('cookies') or [])})"
                  if s.get("cookies") else "")
               + (f" (header `Authorization: {'/'.join(s['schemes'])} <token>`)"
                  if s.get("schemes") else "")
               + ("; later requests sent it back" if s.get("sent_back") else "") + ".")  # fmt: skip
    for w in ses.get("who_is_signed_in") or []:
        out.append(f"- Who is signed in: `{w.get('request')}` answered "
                   + ", ".join(f"`{f}`" for f in w.get("fields") or []) + ".")  # fmt: skip
    for x in ses.get("sign_out") or []:
        out.append(f"- Sign-out: `{x.get('by')}` on `{x.get('on')}` (step {x.get('step')}) sent "
                   f"`{x.get('request')} {x.get('status')}`"
                   + (f", then `{x['then']}`" if x.get("then") else "") + ".")  # fmt: skip
    for u in ses.get("without_session") or []:
        out.append(
            f"- Without a session ({u.get('when')}): `{u.get('request')}` answered "
            f"`{u.get('status')}`" + (f", to `{u['to']}`" if u.get("to") else "") + "."
        )
    tables = ses.get("tables") or {}
    out.append(
        f"- Users are rows of `{tables.get('users')}`, sessions rows of "
        f"`{tables.get('sessions')}` (`db/schema.sql`); a session survives a restart."
    )
    out += ["", "**Cookies.** " + signin.COOKIE_ABOUT, ""]
    cookies = ses.get("cookies") or []
    if cookies:
        out += ["| Cookie | Set by | Path | Domain | HttpOnly | Secure | SameSite | Lifetime | "
                "Cleared by |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
        for c in cookies:
            a = c.get("attributes") or {}

            def where(items) -> str:
                return "<br>".join(f"`{x.get('request')} {x.get('status')}`"
                                   + (f" (step {x['step']}, `{x.get('screen')}`)"
                                      if x.get("step") else "") for x in items) or "-"  # fmt: skip

            out.append(f"| `{c.get('name')}`" + (" (session)" if c.get("session_cookie") else "")
                       + f" | {where(c.get('set_by') or [])} | {_cell(a.get('path', 'unknown'))} | "
                       f"{_cell(a.get('domain', 'unknown'))} | "
                       f"{'yes' if a.get('http_only') else 'no' if a else 'unknown'} | "
                       f"{'yes' if a.get('secure') else 'no' if a else 'unknown'} | "
                       f"{_cell(a.get('same_site', 'unknown'))} | "
                       f"{_cell(a.get('lifetime', 'unknown'))} | "
                       f"{where(c.get('cleared_by') or [])} |")  # fmt: skip
        out += ["", "Set each cookie with the same name and attributes from the same response, "
                "with a value of your own; clear it where the recording cleared it.", ""]  # fmt: skip
    else:
        out += ["No response of the recording set a cookie with attributes the package holds "
                "(names, if any, are above).", ""]  # fmt: skip
    return out


def _deps(pkg: Pkg) -> list[Screen]:
    """Screens in the order to build them: from the entry, along the recorded ways (screens
    shown in a frame or popup right after their host), then the rest in recorded order."""
    by = {s.path: s for s in pkg.screens if s.kind not in ("unrecorded", "code")}
    nexts: dict[str, list[str]] = {}
    for t in pkg.nav.get("transitions") or []:
        nexts.setdefault(t.get("from"), []).append(t.get("to"))
    for s in pkg.nav.get("screens") or []:
        for v in s.get("subviews") or []:
            if v.get("shows"):
                nexts.setdefault(s.get("path"), []).insert(0, v["shows"])
    start = (pkg.nav.get("entry") or {}).get("screen")
    order: list[str] = []
    queue = [start] if start in by else []
    while queue:
        p = queue.pop(0)
        if p in order or p not in by:
            continue
        order.append(p)
        queue += [n for n in nexts.get(p, []) if n not in order]
    order += [p for p in by if p not in order]
    return [by[p] for p in order]


def plan_doc(pkg: Pkg) -> str:
    app = pkg.app
    me = f"{app}/{DIR}/{PLAN}"
    packaged = set(pkg.files)
    values = any(p.startswith(f"{app}/steps/") and p.endswith(".values.yaml") for p in packaged)
    done = done_items(app, packaged, pkg.browser, values)
    lines = _header(f"Build plan: `{pkg.app_id}`", "The build in order.")
    lines += ["Each step ends with the lines that say it is done. *Done item N* is item N of "
              f"*What \"done\" means* in `{app}/{DIR}/{CONSTITUTION}` (the same list as in "
              "`BUILD_TASK.md`):", ""]  # fmt: skip
    lines += [f"{n}. {t}" for n, t in enumerate(done, 1)] + [""]

    def item(*words: str) -> str:
        """`Done item N holds.` for the first item holding each word."""
        ns = [next((i for i, t in enumerate(done, 1) if word in t), None) for word in words]
        ns = [str(n) for n in ns if n]
        if not ns:
            return "The task's checks for it pass."
        return (f"Done item {ns[0]} holds." if len(ns) == 1 else
                f"Done items {', '.join(ns[:-1])} and {ns[-1]} hold.")  # fmt: skip

    steps: list[tuple[str, list[str], list[str]]] = []
    if pkg.browser:
        steps.append(("Browser storage", [f"Keep the data where `{app}/BRIEF.md` *Browser "
                                          "storage* says, under the same keys."],
                      [item("Browser storage")]))  # fmt: skip
    elif pkg.tables:
        names = ", ".join(f"`{t['name']}`" for t in pkg.tables)
        seeded = [f"`{t['name']}` {t['seed_rows']}" for t in pkg.tables if t["seed_rows"]]
        steps.append(("Database and seed", [
            f"Create {names} from `{app}/db/schema.sql`, unchanged, on the engine "
            f"`{app}/{DIR}/{ARCHITECTURE}` names; read the address from the environment.",
            f"Load `{app}/db/seed.sql` once, when the tables are empty."
            if pkg.has(f"{app}/db/seed.sql") else "Fill the tables with synthetic rows as the "
            "brief says.",
        ], ["`db/schema.sql` applies unchanged on a new database.",
            ("The tables hold exactly the seed rows: " + ", ".join(seeded) + ".") if seeded
            else "The tables start as the brief says.",
            item("stored in and read from the database")]))  # fmt: skip
    if pkg.session:
        si = pkg.session.get("sign_in") or {}
        steps.append(("Sign-in and session", [
            f"Build the sign-in on `{si.get('screen')}` and the session as "
            f"`{app}/{DIR}/{ARCHITECTURE}` *Sign-in, session and cookies* says, with every "
            "cookie's name and attributes.",
        ], ["Each recorded attempt gets its recorded status and message.",
            "Every request without a session gets what *Without a session* lists.",
            "Each cookie is set and cleared by the same responses, with the same attributes; "
            "its value is the app's own.",
            item("Sign-in")]))  # fmt: skip
    for sc in _deps(pkg):
        build = (f"Build it from {_link(me, sc.file)}"
                 + (f" and its snapshot `{sc.page}`." if sc.page else "."))  # fmt: skip
        steps.append((f"Screen `{sc.path}`", [build],
                      [f"Every acceptance check of {_link(me, sc.file)} passes.",
                       item("opens at its address")]))  # fmt: skip
    if pkg.nav:
        steps.append(("Navigation", [
            "Wire every transition, redirect, dialog, tab, frame and popup window of "
            f"`{app}/navigation.json`; answer every link out of scope as it says."],
            [item("Every way from screen to screen", "Every element the recording used")]))  # fmt: skip
    if pkg.endpoints:
        steps.append(("API", [f"Answer every endpoint of `{app}/api/` on its method and path."],
                      ["The requests of each endpoint file's `calls`, sent in order on a fresh "
                       "seed, get their statuses and keys.", item("Every endpoint in")]))  # fmt: skip
    if pkg.rules:
        steps.append(("Field rules and page behaviour", [
            f"Implement every rule and behaviour of `{app}/rules.json` (each page file lists its "
            "screen's)."], ["Each rule's message appears letter for letter where its page file "
                            "says; each behaviour and on-load state holds."]))  # fmt: skip
    if pkg.look:
        steps.append(("Look", [f"Style from `{app}/look/css/`, check against `look/theme.json`."],
                      ["Each screen matches its picture in `look/screens/` (each page file's "
                       "*Layout* section)."]))  # fmt: skip
    steps.append(("Self-check", [
        "Run the checks of `BUILD_TASK.md` section 4 (or *Working from this package alone*).",
        f"Write `{app}/BUILD-NOTES.md`."], [
        "Every page file's acceptance checks pass.",
        item("This is the acceptance run", "BUILD-NOTES.md") if values else
        item("BUILD-NOTES.md")]))  # fmt: skip
    for n, (title, do, done_when) in enumerate(steps, 1):
        lines += [f"## {n}. {title}", ""]
        lines += [f"- {d}" for d in do]
        lines += ["", "Done when:", ""] + [f"- {d}" for d in done_when] + [""]
    rest = [s for s in pkg.screens if s.kind in ("unrecorded", "code")]
    if rest:
        lines += ["## Not built", "", "Listed so their links are answered right; build nothing "
                  "for them:", ""]  # fmt: skip
        lines += [f"- `{s.path}` ({s.kind}): {_link(me, s.file)}." for s in rest] + [""]
    return "\n".join(lines)


def glossary_doc(pkg: Pkg) -> str | None:
    app = pkg.app
    lines = _header(f"Glossary: `{pkg.app_id}`", "Codes, option lists, statuses and words.")
    said = False
    options: dict[str, tuple[str, list]] = {}
    for s in pkg.nav.get("screens") or []:
        for e in s.get("elements") or []:
            if e.get("options"):
                options.setdefault(e["selector"], (s.get("path"), e["options"]))
    if options:
        said = True
        lines += ["## Option lists", "", f"The code each option posts, then its label "
                  f"(`{app}/navigation.json`):", ""]  # fmt: skip
        for sel, (path, opts) in options.items():
            lines.append(f"- `{sel}` on `{path}`: " + ", ".join(
                f"{_code(o.get('value'))} = {o.get('label')}" for o in opts) + ".")  # fmt: skip
        lines.append("")
    codes = [line for line in pkg.section("Code lists", "### ").splitlines()
             if line.startswith("| `")]  # fmt: skip
    if codes:
        said = True
        lines += ["## Code lists", "", f"Values seen in the evidence (`{app}/BRIEF.md` *Code "
                  "lists*); their meaning is the label of the option that posts them, where one "
                  "does, else unknown:", "", "| Field | Codes |", "|---|---|", *codes, ""]  # fmt: skip
    statuses: dict[str, list[str]] = {}
    for name, row in pkg.endpoints.items():
        for status, r in (row["doc"].get("responses") or {}).items():
            ex = r.get("example") if isinstance(r.get("example"), dict) else {}
            what = (
                f'`{name}` ("{ex["error"]}")' if isinstance(ex.get("error"), str) else f"`{name}`"
            )
            statuses.setdefault(str(status), []).append(what)
    if statuses:
        said = True
        lines += ["## Statuses the application answered", "", "What each status meant in this "
                  f"recording: the endpoints that answered it (`{app}/api/*.json`), with the "
                  "application's own message where it gave one:", ""]  # fmt: skip
        for st in sorted(statuses):
            lines.append(f"- `{st}`: " + "; ".join(statuses[st]) + ".")
        lines.append("")
    levels = pkg.rules.get("levels") or {}
    lines += ["## Words of the package", ""]
    for k, v in levels.items():
        lines.append(f"- Rule level `{k}`: {v} (`{app}/rules.json`).")
    basis = re.search(r"^Basis, on every item: (.+)$", pkg.brief, re.M)
    if basis:
        lines.append(f"- Basis: {basis.group(1)} (`{app}/BRIEF.md`).")
    lines += [
        "- *Seen, not used*: an element the recording showed and nobody used: render it, give "
        f"it no behaviour (`{app}/navigation.json`).",
        "- Placeholders `x`, `X`, `9` in a snapshot or picture: data was there; show the "
        "record's value, never the placeholder (`look/texts.json`).",
        "- *Unknown*: the package does not hold the fact; the sentence says where it would come "
        "from.",
        "",
    ]  # fmt: skip
    return "\n".join(lines) if (said or levels or basis) else None


# =================================================================================================
# The set, and its check
# =================================================================================================


def generate(app_id: str, files: dict[str, bytes]) -> dict[str, bytes]:
    """The documents for the package whose entries are `files` ({package path: bytes}):
    {package path: bytes}. Empty for a desktop application."""
    pkg = Pkg(app_id, files)
    if not applies(pkg.conf):
        return {}
    base = folder(app_id)
    out = {
        f"{base}/{CONSTITUTION}": constitution_doc(pkg),
        f"{base}/{ARCHITECTURE}": architecture_doc(pkg),
        f"{base}/{PLAN}": plan_doc(pkg),
    }
    glossary = glossary_doc(pkg)
    if glossary:
        out[f"{base}/{GLOSSARY}"] = glossary
    for sc in pkg.screens:
        out[sc.file] = page_doc(pkg, sc)
    return {k: v.encode("utf-8") for k, v in out.items()}


def inputs(repo_root: Path, app_id: str) -> dict[str, bytes]:
    """The package files the documents are made of, read from the repository (for the
    completeness check, which runs before a package exists)."""
    from labctl.builder.package import PackageError, _collect, _content

    try:
        return {rel: _content(path) for rel, path in _collect(Path(repo_root), app_id)}
    except PackageError:  # a partial tree (no AGENTS.md): read the application's own files
        app = Path(repo_root) / "apps" / app_id
        out = {}
        for path in sorted(app.rglob("*")) if app.is_dir() else []:
            rel = path.relative_to(app).as_posix()
            if path.is_file() and rel.split("/", 1)[0] in (
                    "BRIEF.md", "app.yaml", "navigation.json", "rules.json", "session.json",
                    CODEMAP, "db", "api", "pages", "unrecorded", "steps", "look"):  # fmt: skip
                out[f"apps/{app_id}/{rel}"] = _content(path)
        return out


def gaps(app_id: str, files: dict[str, bytes], docs: dict[str, bytes] | None = None) -> list[str]:
    """What the documents lack: a screen without a page file, a missing core document."""
    pkg = Pkg(app_id, files)
    if not applies(pkg.conf):
        return []
    docs = generate(app_id, files) if docs is None else docs
    out = []
    base = folder(app_id)
    for name in (CONSTITUTION, ARCHITECTURE, PLAN):
        if f"{base}/{name}" not in docs:
            out.append(f"`{base}/{name}` is missing")
    pages = {rel for rel in docs if rel.startswith(f"{base}/{PAGES}/")}
    for sc in pkg.screens:
        if sc.file not in pages:
            out.append(f"screen `{sc.path}` has no page file under `{base}/{PAGES}/`")
    named = {s["path"] for s in pkg.nav.get("screens") or [] if s.get("path")}
    for path in sorted(named - {s.path for s in pkg.screens}):
        out.append(f"screen `{path}` has no page file under `{base}/{PAGES}/`")
    return out
