"""Build brief: the app model, scoped to what is needed, written for the builder.

The brief reads the app model only (docs/06-evidence.md). Scope follows need, never the evidence:
a screen, route or table is in scope only when a registered automation or a requested flow needs
it, directly or through something it needs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from labctl import model_build as mb
from labctl import source as src
from labctl.appmodel import AppModel, Flow, Route, Screen, Table
from labctl.models import Automation
from labctl.source import sqlalchemy_models as sam

HEALTH = re.compile(r"/(health|healthz|ready|actuator/health)$")
NEED = ("automation:", "flow:")
DEFAULT_STACK = "React, FastAPI and Postgres (the lab default)"
check_selector = mb.check_selector  # kept here for callers of the T-05 interface

# What gets built, by the evidence a screen (or the app) rests on (docs/06-evidence.md).
MODES: dict[str, str] = {
    "copy": "built from the application's source: same screens, API, tables and rules",
    "reconstruction": "built from a recording of the real application: real markup and API on "
    "the recorded paths; rules only where an error was recorded; nothing beyond what was recorded",
    "skeleton": "built from an automation only: the bot's own selectors exist, but layout, look "
    "and everything the bot does not name are invented",
    "backend": "no screens: only the API and the database",
    "sample": "built from a description: it follows the described flow; everything exact is "
    "invented, so it is a sample, not a copy",
}
FIDELITY = ("copy", "reconstruction", "skeleton", "sample")  # strongest first


@dataclass
class ElementView:
    selector: str | None
    label: str | None
    actions: list[str]
    by: list[str]
    basis: str
    status: str  # found | missing | unchecked
    detail: str = ""


@dataclass
class ScreenScope:
    screen: Screen
    reasons: list[str]
    elements: list[ElementView]
    mode: str = "sample"


@dataclass
class Brief:
    app: str
    system: str
    model: AppModel
    needed_by: list[str]
    db_engine: str = "postgres"
    db_engine_set: bool = False  # True when a person named the engine
    screens: list[ScreenScope] = field(default_factory=list)
    routes: dict[str, list[str]] = field(default_factory=dict)  # route key -> reasons
    tables: dict[str, list[str]] = field(default_factory=dict)  # table name -> reasons
    gaps: list[str] = field(default_factory=list)
    # Where the business data lives (R07): the model's finding, or what a person named with
    # `--data`. None for an app whose evidence says nothing (as before R07).
    data: dict | None = None

    @property
    def data_kind(self) -> str | None:
        return (self.data or {}).get("kind")

    @property
    def browser_only(self) -> bool:
        """The business data is in the browser: no database tables, no database claims."""
        return self.data_kind == "browser"

    def route(self, key: str) -> Route:
        return self.model.route(key)

    @property
    def mode(self) -> str:
        """The app's mode: the weakest of its screens' modes, or backend without screens."""
        if not self.screens:
            return "backend" if self.routes or self.tables else "sample"
        return max((s.mode for s in self.screens), key=FIDELITY.index)

    def table(self, name: str) -> Table:
        return self.model.table(name)

    @property
    def found_engine(self) -> str | None:
        return (self.model.stack.db or {}).get("engine")

    @property
    def source(self):
        return next((e for e in self.model.evidence if e.kind == "source"), None)

    @property
    def digest(self) -> str:
        return self.source.ref if self.source else mb.model_digest(self.model)

    @property
    def out_screens(self) -> list[Screen]:
        used = {id(s.screen) for s in self.screens}
        return [s for s in self.model.screens if id(s) not in used]

    @property
    def out_routes(self) -> list[Route]:
        return [r for r in self.model.routes if r.key not in self.routes]

    @property
    def out_tables(self) -> list[Table]:
        return [t for t in self.model.tables if t.name not in self.tables]


def _add(reasons: dict[str, list[str]], key: str, reason: str) -> None:
    items = reasons.setdefault(key, [])
    if reason not in items:
        items.append(reason)


def _by(ids: list[str]) -> list[str]:
    return [i.split(":", 1)[1] for i in ids if i.startswith(NEED)]


def screen_mode(model: AppModel, screen: Screen) -> str:
    """copy (source), reconstruction (recorded), skeleton (declared by an automation), sample."""
    kinds = {e.id: e.kind for e in model.evidence}
    seen = {kinds.get(f, f.split(":", 1)[0]) for f in screen.from_}
    if "source" in seen:
        return "copy"
    if "har" in seen:
        return "reconstruction"
    if seen & {"automation", "openapi", "postman", "ddl"}:
        return "skeleton"
    return "sample"


def make_model(
    app: str,
    automations: list[Automation],
    repo_root: Path,
    source: Path | None = None,
    flows: list[Flow] = (),
    recordings: list[Path] = (),
    openapi: list[Path] = (),
    pages_dir: Path | None = None,
) -> AppModel:
    """Every kind of evidence available for `app`, merged.

    `recordings` are recording folders or bare `.har` files; their value-free page snapshots are
    written to `pages_dir` when it is given (normally `apps/<app>/pages/`). `openapi` are OpenAPI
    3.x descriptions or Postman v2.1 collections.
    """
    parts = [mb.from_automations(app, automations), mb.from_flows(app, list(flows))]
    if source is not None:
        parts.append(mb.from_source(app, source, repo_root))
    parts += [mb.from_recording(app, Path(p), repo_root, pages_dir) for p in recordings]
    parts += [mb.from_openapi(app, Path(p)) for p in openapi]
    return mb.merge(app, parts)


def build(
    app: str,
    system: str,
    *,
    automations: list[Automation],
    repo_root: Path,
    source: Path | None = None,
    flows: list[Flow] = (),
    db_engine: str | None = None,
    recordings: list[Path] = (),
    openapi: list[Path] = (),
    pages_dir: Path | None = None,
) -> Brief:
    model = make_model(app, automations, repo_root, source, flows, recordings, openapi, pages_dir)
    return scope(app, system, model, db_engine)


def has_field(shape, path: str) -> bool:
    """Whether a recorded response shape (`{"[]": item}` for lists) has `items[].review_no`."""
    node = shape
    for token in re.findall(r"\[\]|[^.\[\]]+", path):
        if not isinstance(node, dict):
            return False
        if token == "[]":
            if "[]" not in node:
                return False
            node = node["[]"]
            continue
        if token not in node:
            return False
        node = node[token]
    return True


def data_location(model: AppModel, override: str | None) -> dict | None:
    """The model's finding about where the data lives; `override` (browser | server) replaces it
    and says so."""
    found = dict(model.stack.data) if model.stack.data else None
    if override is None:
        return found
    if override not in ("browser", "server"):
        raise ValueError("--data is browser or server")
    was = (found or {}).get("kind", "not assessed")
    base = found or {"storage": [], "referenced": [], "keys": [], "shape": {}, "evidence": []}
    note = f"overridden by a person with --data {override}; the recording showed: {was}"
    return {**base, "kind": override, "overridden": True, "evidence": [note, *base["evidence"]]}


def scope(
    app: str,
    system: str,
    model: AppModel,
    db_engine: str | None = None,
    data: str | None = None,
) -> Brief:
    needed = [e.id for e in model.evidence if e.id.startswith(NEED)]
    brief = Brief(app=app, system=system, model=model, needed_by=_by(needed))
    brief.gaps += model.problems + [c.what for c in model.conflicts]
    brief.data = data_location(model, data)

    found = brief.found_engine
    brief.db_engine = db_engine or (found if found in sam.ENGINES else "postgres")
    brief.db_engine_set = db_engine is not None
    if brief.browser_only:
        pass  # there is no database whose engine could be unknown
    elif db_engine is None and found is None:
        brief.gaps.append("no evidence shows the database engine; postgres assumed (--db-engine)")
    elif db_engine is None and found not in sam.ENGINES:
        brief.gaps.append(f"database engine '{found}' not recognised; postgres assumed")
    elif db_engine is None and found == "sqlite":
        brief.gaps.append(
            "the source only shows SQLite (often a development default); name the engine the "
            "real application runs with --db-engine. The lab needs a database server it can "
            "put a proxy in front of"
        )

    def route_for(method: str, path: str) -> Route | None:
        same = [r for r in model.routes if r.method == method]
        return src.best_match(path, same, key=lambda r: r.path)

    # Screens somebody needs, and the API calls they make.
    for screen in model.screens:
        need = [f for f in screen.from_ if f.startswith(NEED)]
        if not need:
            continue
        reasons = [f"used by {a}" for a in _by([f for f in need if f.startswith("automation:")])]
        reasons += [
            f"requested in flow {f}" for f in _by([f for f in need if f.startswith("flow:")])
        ]
        inventory = bool(screen.ids or screen.classes)
        views = []
        for el in screen.elements:
            if not el.selector:
                status, detail = "unchecked", "described; no selector known yet"
            elif inventory:
                status, detail = mb.check_selector(
                    el.selector, set(screen.ids), set(screen.classes)
                )
            elif screen.snapshots == 0:
                status, detail = "unchecked", "recorded by address only; no page content"
            else:
                status, detail = "unchecked", "no recording or source of this screen"
            views.append(
                ElementView(
                    el.selector, el.label, el.actions, _by(el.from_), el.basis, status, detail
                )
            )
        brief.screens.append(ScreenScope(screen, reasons, views, screen_mode(model, screen)))
        for call in screen.calls:
            route = route_for(call.method, call.path)
            where = f"{call.at.file}:{call.at.line}" if call.at else screen.name
            if route is None:
                brief.gaps.append(
                    f"{where} calls {call.method} {call.path}, which no backend route serves"
                )
                continue
            origin = call.at.file if call.at else "recorded"
            _add(brief.routes, route.key, f"called by screen {screen.path} ({origin})")

    # A server-rendered screen is served by a GET route on its own path: that route is the page.
    for scope_ in brief.screens:
        if scope_.screen.path:
            for route in model.routes:
                if route.method == "GET" and src.same_shape(route.path, scope_.screen.path):
                    _add(brief.routes, route.key, f"serves screen {scope_.screen.path}")

    # Buttons on in-scope screens that nobody ever pressed: what they do is unknown. Say so, so the
    # builder neither guesses nor drops them silently (the QTT build found Approve and Reopen).
    for scope_ in brief.screens:
        for el in scope_.screen.elements:
            pressed = bool(el.actions) or any(f.startswith(NEED) for f in el.from_)
            if el.tag == "button" and el.selector and not pressed:
                brief.gaps.append(
                    f"{el.selector} on {scope_.screen.path} was never pressed in any recording: what "
                    "it does is unknown. Record it, or build it to show it is not available in the demo"
                )

    # Routes somebody calls directly, and a health route for the lab agent.
    for route in model.routes:
        callers = _by([f for f in route.from_ if f.startswith("automation:")])
        if callers:
            _add(brief.routes, route.key, f"called directly by {', '.join(callers)}")
    health = [r for r in model.routes if r.method == "GET" and HEALTH.search(r.path)]
    for route in health[:1]:
        _add(brief.routes, route.key, "health check for the lab agent")
    if not health:
        brief.gaps.append("no health route in the evidence; the demo app must add GET /api/health")

    # Fields a bot's code reads that the recorded answer does not have: the bot would break.
    for key in brief.routes:
        route = model.route(key)
        shape = next(
            (
                v
                for s, v in route.responses.items()
                if s.startswith("2") and v and "(content)" not in v
            ),
            None,
        )
        missing = [f for f in route.reads if shape is not None and not has_field(shape, f)]
        if missing:
            brief.gaps.append(
                f"`{key}`: automations read {', '.join(f'`{f}`' for f in missing)}, which the "
                "recorded answer does not have; check the bot or the recording"
            )

    # Tables behind those routes, tables queried directly, and their parents.
    listed = set(model.complete("tables"))  # evidence that lists every table (source, DDL)
    for key in list(brief.routes):
        for table in model.route(key).tables:
            known = next((x for x in model.tables if x.name == table), None)
            if listed and known is not None and known.basis == "inferred":
                continue  # a shape guessed from a response; the listed tables are the real ones
            _add(brief.tables, table, f"used by {key}")
    # Without source code nothing shows which tables a screen reads (server-rendered pages read
    # the database directly), so every listed table may be needed by those screens.
    sources = {e.id for e in model.evidence if e.kind == "source"}
    blind = [s.screen.name for s in brief.screens if not sources & set(s.screen.from_)]
    if blind and listed:
        names = ", ".join(blind[:3]) + (f" and {len(blind) - 3} more" if len(blind) > 3 else "")
        for table in model.tables:
            if listed & set(table.from_):
                _add(brief.tables, table.name,
                     f"may be read by {names}: no source code shows which tables these screens use")  # fmt: skip
    for table in model.tables:
        callers = _by([f for f in table.from_ if f.startswith("automation:")])
        if callers:
            _add(brief.tables, table.name, f"queried directly by {', '.join(callers)}")
    names = {t.name for t in model.tables}
    changed = True
    while changed:
        changed = False
        for name in list(brief.tables):
            for column in model.table(name).columns:
                parent = column.references[0] if column.references else None
                if parent and parent != name and parent in names:
                    changed = changed or parent not in brief.tables
                    _add(brief.tables, parent,
                         f"parent of {name} ({column.name} -> {parent}.{column.references[1]})")  # fmt: skip

    order = [t.name for t in sam.ordered(sam_tables(model))]
    brief.tables = {k: brief.tables[k] for k in order if k in brief.tables}
    brief.routes = {r.key: brief.routes[r.key] for r in model.routes if r.key in brief.routes}
    _data_gaps(brief)
    return brief


def _data_gaps(brief: Brief) -> None:
    """What the brief owes the reader about where the data lives."""
    data, model = brief.data or {}, brief.model
    if brief.browser_only:
        if brief.tables or brief.routes:
            brief.gaps.append(
                f"--data browser overrides the evidence: {len(brief.tables)} table(s) and "
                f"{len(brief.routes)} route(s) the evidence shows are left out of the build"
            )
            brief.tables = {}
        if not data.get("keys"):
            brief.gaps.append("browser storage keys were not observed: the recorder does not capture "
                              "storage; choose them, say so in BUILD-NOTES.md, or record again with "
                              "storage capture")  # fmt: skip
        if not data.get("shape"):
            brief.gaps.append("the shape of the stored records was not observed: build it from what "
                              "the screens show and say so in BUILD-NOTES.md")  # fmt: skip
    elif data.get("kind") == "unknown":
        brief.gaps.append(
            "where this app keeps its business data is not shown by the evidence (no "
            "state-changing request, no recorded browser storage): no table or database is "
            "inferred from the screens; say which with --data browser or --data server"
        )
    elif (
        data.get("overridden") and not model.tables and all(r.method == "GET" for r in model.routes)
    ):
        brief.gaps.append("--data server overrides the evidence: the recording shows no server "
                          "tables or routes, so none are in the brief; add DDL or an API description")  # fmt: skip


BASIS_NOTE = {
    "observed": "observed; column types as recorded",
    "declared": "declared by an automation or a DDL file",
    "inferred": "inferred from recorded responses; column types inferred",
    "described": "described by a person; column types assumed",
    "assumed": "assumed; column types assumed",
}


def _table_note(table: Table) -> str | None:
    if table.basis == "declared":
        if any(e.startswith("ddl:") for e in table.from_):
            return "declared in a DDL file; column types as declared"
        return "declared only; column types assumed"
    return BASIS_NOTE.get(table.basis)


def sam_tables(model: AppModel, names: list[str] | None = None) -> list[sam.Table]:
    """Model tables in the form the DDL writer takes."""
    out = []
    for t in model.tables:
        if names is not None and t.name not in names:
            continue
        table = sam.Table(
            name=t.name,
            model=t.model or t.name,
            file=Path(t.at.file) if t.at else None,
            line=t.at.line if t.at else 0,
            note=None if t.at else _table_note(t),
        )
        table.columns = [
            sam.Column(
                name=c.name,
                type=c.type,
                nullable=c.nullable,
                primary_key=c.primary_key,
                unique=c.unique,
                index=c.index,
                references=tuple(c.references) if c.references else None,
                autoincrement=c.autoincrement,
            )
            for c in t.columns
        ]
        out.append(table)
    return out


def rules(brief: Brief) -> list[dict]:
    """In-scope rules, each listed once with where it applies. All are hypotheses."""
    in_scope = set(brief.routes) | {
        n for s in brief.screens for n in (s.screen.name, *s.screen.aliases)
    }
    out = []
    for rule in brief.model.rules:
        where = [w for w in rule.where if w in in_scope]
        if where:
            out.append(
                {
                    "id": f"R{len(out) + 1}",
                    "where": ", ".join(where),
                    "rule": rule.text,
                    "verification": "hypothesis",
                    "basis": rule.basis,
                }
            )
    return out


def schema(brief: Brief, repo_root: Path) -> str:
    if brief.browser_only:
        # Written, but with no table: every downstream step reads this file (the lab mounts it,
        # the package carries it, `validate` checks it exists) and an empty schema creates nothing.
        return (
            f"-- Schema for the {brief.app} demo app. Generated by labctl brief; do not edit by hand.\n"
            "-- No tables: this app keeps its business data in browser storage, not in a database.\n"
            "-- See the Browser storage section of BRIEF.md.\n"
        )
    left_out = ", ".join(t.name for t in brief.out_tables) or "none"
    evidence = ", ".join(e.id for e in brief.model.evidence)
    header = (
        f"Schema for the {brief.app} demo app. Generated by labctl brief; do not edit by hand.\n"
        f"From the app model ({evidence}).\n"
        f"Only tables something needs. Left out: {left_out}.\n"
        f"Dialect: {brief.db_engine}. Table and column names must not change."
    )
    return sam.ddl(sam_tables(brief.model, list(brief.tables)), header, brief.db_engine)


def app_yaml(brief: Brief, repo_root: Path) -> dict:
    stack: dict = {} if brief.browser_only else {"db": {"engine": brief.db_engine}}
    data = brief.data or {}
    if data.get("kind") in ("browser", "unknown") or data.get("overridden"):
        stack["data"] = {k: v for k, v in data.items() if v not in ([], {}, None, False)}
    front = brief.model.stack.frontend or {}
    if front.get("framework"):
        stack["frontend"] = {
            k: front[k] for k in ("framework", "version", "ui_library", "marker") if front.get(k)
        }
    if brief.model.stack.backend:
        stack["backend"] = brief.model.stack.backend
    source = brief.source
    return {
        "id": brief.app,
        "name": f"{brief.system} (demo copy)",
        "mode": brief.mode,
        "copies": {
            "system": brief.system,
            "source": source.id.split(":", 1)[1] if source else None,
            "ref": brief.digest,
            "analysed": date.today().isoformat(),
        },
        "stack": stack,
        "run": {},
        "db": {
            "schema": "db/schema.sql",
            "derived_from": sorted({f for n in brief.tables for f in brief.table(n).from_}),
        },
        "built_for": brief.needed_by,
        "rules": [{k: v for k, v in r.items() if k != "basis"} for r in rules(brief)],
    }


NOT_IDENTIFIED = "not identified — choose at onboarding"


def _name_version(item: dict, key: str = "name") -> str:
    return " ".join(str(item[k]) for k in (key, "version") if item.get(k))


def detected_stack(stack) -> list[str]:
    """Markdown lines for the technology a recording showed: what, the marker, how sure. Empty
    when the stack did not come from a recording."""
    server = stack.server or {}
    if not (server or stack.libraries or any(f.startswith("har:") for f in stack.from_)):
        return []
    rows: list[tuple[str, str, str, str]] = []
    front = stack.frontend or {}
    if front.get("framework") and front.get("marker"):
        sure = "guess" if front["framework"] == "server-rendered" else "certain"
        rows.append(("Front end", _name_version(front, "framework"), front["marker"], sure))
    else:
        rows.append(("Front end", NOT_IDENTIFIED, "", ""))
    if server.get("technology"):
        what = server["technology"]
        if server.get("framework"):
            what += f" ({server['framework']})"
        if server.get("version"):
            what += f" {server['version']}"
        marker = server["marker"]
        if server.get("framework_marker") and server["framework_marker"] != marker:
            marker += f"; {server['framework_marker']}"
        rows.append(("Server technology", what, marker, server.get("confidence", "")))
        for other in server.get("also", []):
            rows.append(("Also seen", other["technology"], other["marker"], other["confidence"]))
    else:
        rows.append(("Server technology", NOT_IDENTIFIED, "", ""))
    web = server.get("web_server")
    rows.append(("Web server", _name_version(web) if web else NOT_IDENTIFIED,
                 web["marker"] if web else "", web["confidence"] if web else ""))  # fmt: skip
    ui = [x for x in stack.libraries if x.get("role") == "ui"]
    rows.append(("UI components", ", ".join(_name_version(x) for x in ui) or NOT_IDENTIFIED,
                 "; ".join(x["marker"] for x in ui[:3]), ui[0]["confidence"] if ui else ""))  # fmt: skip
    libs = [x for x in stack.libraries if x.get("role") != "ui"]
    rows.append(("Client libraries", ", ".join(_name_version(x) for x in libs) or NOT_IDENTIFIED,
                 "; ".join(x["marker"] for x in libs[:3]), libs[0]["confidence"] if libs else ""))  # fmt: skip
    out = ["**Technology read from the recording.** Build with the same technology where it says so; "
           "where it says *not identified*, choose at onboarding and record the choice in "
           "BUILD-NOTES.md.\n",
           "| Part | Detected | Marker | Confidence |", "|---|---|---|---|"]  # fmt: skip
    out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    return out + [""]


RESETS = {
    "localStorage": "survives reloads and browser restarts; cleared by clearing the site's data, "
    "or by a fresh browser profile or context",
    "sessionStorage": "lasts for one tab; cleared when the tab closes, or by a fresh context",
    "indexedDB": "survives reloads and restarts; cleared by clearing the site's data, or by a "
    "fresh profile or context",
}


def browser_storage(brief: Brief) -> list[str]:
    """The *Browser storage* section of a browser-only app's brief. Keys and record shapes only:
    stored values are data and are not in the evidence."""
    data = brief.data or {}
    kinds = data.get("storage") or []
    referenced = [k for k in data.get("referenced", []) if k not in kinds]
    out = ["### Browser storage\n"]
    if data.get("overridden"):
        out.append(
            "**Overridden by a person** with `labctl brief --data browser`; the evidence below is "
            "what the recording showed.\n"
        )
    out.append(
        "This app keeps its business data **in the browser**, not in a server database: no "
        "table, no database container and no database address. Build it to keep the data in the "
        "same storage, under the same keys and record shape. Do not add a server-side store for "
        "it.\n"
    )
    out += ["| | |", "|---|---|"]
    if kinds:
        out.append(f"| Storage kind | {', '.join(f'`{k}`' for k in kinds)} (observed) |")
    else:
        hint = (
            " (scripts reference " + ", ".join(f"`{k}`" for k in referenced) + ")"
            if referenced
            else ""
        )
        out.append(
            f"| Storage kind | **not observed**{hint}: the recording holds no browser storage; "
            "it is inferred from the absence of requests |"
        )
    keys = data.get("keys") or []
    script_keys = [k for k in data.get("script_keys", []) if k not in keys]
    if keys:
        text = ", ".join(f"`{k}`" for k in keys)
    elif script_keys:
        text = (
            "**not observed in storage**; the page's scripts name "
            + ", ".join(f"`{k}`" for k in script_keys)
            + " as literals, which is not proof of what was stored"
        )
    else:
        text = "**not observed** (a script that sets a key from a variable shows none)"
    out.append(f"| Keys | {text} |")
    shapes = data.get("shape") or {}
    if shapes:
        text = "; ".join(
            f"`{k}`: `{json.dumps(v, separators=(',', ':'))}`" for k, v in shapes.items()
        )
        out.append(f"| Record shape (values masked) | {text} |")
    else:
        out.append(
            "| Record shape | **not observed**: stored values are never kept; build the record "
            "from the fields the screens show and say so in BUILD-NOTES.md |"
        )
    if kinds or referenced:
        resets = [f"`{k}`: {RESETS[k]}" for k in (kinds or referenced) if k in RESETS]
    else:
        resets = [
            "storage kind unknown: `localStorage` survives reloads and restarts, `sessionStorage` "
            "lasts one tab; either is cleared by clearing the site's data or a fresh browser context"
        ]
    out.append(
        "| What resets it | "
        + "; ".join(resets)
        + ". The lab's data reset, baselines and data packs do not touch it |"
    )
    out.append("")
    for line in data.get("evidence", []):
        out.append(f"- Evidence: {line}")
    return out + [""]


def render(brief: Brief, repo_root: Path) -> str:
    model = brief.model
    out: list[str] = []
    w = out.append
    w(f"# Build brief: {brief.app}\n")
    w(
        f"A demo copy of **{brief.system}**, scoped to what registered automations and requested "
        "flows need. Generated by `labctl brief` from `model.yaml`; regenerate rather than edit. "
        "Rules for building it: `docs/factory-playbook.md`.\n"
    )
    w("| | |\n|---|---|")
    for ev in model.evidence:
        w(f"| Evidence | `{ev.id}` ({ev.kind}{', ' + ev.ref if ev.ref else ''}) |")
    w(f"| Needed by | {', '.join(f'`{a}`' for a in brief.needed_by)} |")
    w(f"| Mode | **{brief.mode}**: {MODES[brief.mode]} |")
    w(f"| Generated | {date.today().isoformat()} |")
    w("| Reviewed by | *nobody yet* |\n")
    w("Basis, on every item: **observed** (seen in a recording or the real code), **declared** (an "
      "automation or a specification says so), **inferred**, **described** (a person said so), "
      "**assumed** (nobody said).\n")  # fmt: skip

    w("## Stack to pin\n")
    if model.stack.pinned or model.stack.backend:
        w("Use the same versions as the evidence shows, so components render the same elements.\n")
        w("| Part | Evidence shows |\n|---|---|")
        for name, version in model.stack.pinned.items():
            w(f"| `{name}` | {version} |")
        versions = []
        for part in ("backend", "orm"):
            info = getattr(model.stack, part)
            if info:
                w(f"| `{info['framework']}` | {info['version']} |")
                versions.append(str(info["version"]))
        origin = (
            f"named when the brief was generated; the evidence shows {brief.found_engine or 'nothing'}"
            if brief.db_engine_set
            else "found in the evidence"
            if brief.found_engine
            else "assumed"
        )
        if brief.browser_only:
            w("| database | none: the business data is in browser storage |\n")
        else:
            w(f"| database | {brief.db_engine} ({origin}) |\n")
        if any(v and not v[0].isdigit() for v in versions):
            w(
                "Backend versions are ranges in the evidence. Pin the newest release in each range, "
                "and say which in BUILD-NOTES.md, unless the reviewer has named the version the real "
                "application runs.\n"
            )
    elif brief.browser_only and not (model.stack.frontend or {}).get("marker"):
        w("No evidence shows the stack. The business data is in browser storage: no database.\n")
    elif not (model.stack.frontend or {}).get("marker"):
        w(f"No evidence shows the stack. Use {DEFAULT_STACK}; database: {brief.db_engine}.\n")
    front = model.stack.frontend or {}
    if front.get("marker"):
        name = " ".join(str(front[k]) for k in ("framework", "version") if front.get(k))
        w(f"Front end: **{name}**, read from the recording ({front['marker']}). Build the screens "
          "with it, so they render the same elements.")  # fmt: skip
        if brief.browser_only:
            w("No server database: the business data is in browser storage (see *Browser "
              "storage*). The container only serves the files; say in BUILD-NOTES.md what serves "
              "them.")  # fmt: skip
        elif not model.stack.backend:
            w("No evidence shows the backend: use FastAPI (the lab default) and say so in "
              f"BUILD-NOTES.md; database: {brief.db_engine}.")  # fmt: skip
        w("")
    for line in detected_stack(model.stack):
        w(line)

    w("## Build mode\n")
    w(f"This app is a **{brief.mode}**: {MODES[brief.mode]}. A bot that passes on it has been "
      "shown to work only as far as that evidence goes.\n")  # fmt: skip
    if brief.screens:
        w("| Screen | Mode |\n|---|---|")
        for sc in brief.screens:
            w(f"| `{sc.screen.path or sc.screen.name}` | {sc.mode} |")
        w("")
        others = [
            m for m in FIDELITY if m != brief.mode and any(s.mode == m for s in brief.screens)
        ]
        for mode in others:
            w(f"- **{mode}**: {MODES[mode]}.")
        if others:
            w("")

    w("## In scope\n")
    w("### Screens\n")
    for scope in brief.screens:
        screen = scope.screen
        if screen.component and screen.at:
            where = (f"route `{screen.path}` in `{screen.at.file}:{screen.at.line}`, component "
                     f"`{screen.component}` in `{screen.files[0] if screen.files else '?'}`")  # fmt: skip
        else:
            where = "known only from " + ", ".join(f"`{f}`" for f in screen.from_)
        w(f"**{screen.path or '(path unknown)'}** ({screen.name}, {screen.basis}, {scope.mode}; "
          f"{'; '.join(scope.reasons)}): {where}\n")  # fmt: skip
        if screen.snapshots == 0:
            w("Recorded by address only: no page content was captured, so its elements are "
              "unknown. Do not invent them as if they were observed.\n")  # fmt: skip
        w("| Element | Used for | By | Basis | In evidence |\n|---|---|---|---|---|")
        for el in scope.elements:
            mark = {"found": "yes", "missing": "**missing**", "unchecked": "not checked"}[el.status]
            note = f" ({el.detail})" if el.detail else ""
            name = f"`{el.selector}`" if el.selector else f"*{el.label}*"
            w(
                f"| {name} | {', '.join(el.actions) or '-'} | {', '.join(el.by)} | {el.basis} | {mark}{note} |"
            )
        if screen.files:
            files = ", ".join(f"`{f}`" for f in screen.files)
            w(f"\nRead: {files}. Reproduce what these files render and do on this screen, "
              "including elements nothing names: they decide layout and behaviour.\n")  # fmt: skip
        else:
            w("")

    w("### API routes\n")
    if brief.browser_only and not brief.routes:
        w("None: no request for data, and none that changes state, reached the app's own origin.\n")
    else:
        w("| Route | Basis | Handler | Why |\n|---|---|---|---|")
    for key, reasons in brief.routes.items():
        route = brief.route(key)
        handler = f"`{route.at.file}:{route.at.line}` `{route.handler}`" if route.at else "-"
        w(f"| `{key}` | {route.basis} | {handler} | {'; '.join(reasons)} |")
    w("")
    for key in brief.routes:
        route = brief.route(key)
        details = []
        if route.doc:
            details.append(route.doc.replace("\n", " ").rstrip("."))
        if route.query:
            details.append("query: " + ", ".join(f"`{q}`" for q in route.query))
        if route.body:
            details.append("body: " + "; ".join(f"`{b}`" for b in route.body))
        if route.responses:
            details.append("responses: " + ", ".join(f"`{s}`" for s in route.responses))
            ok = next((v for k, v in route.responses.items() if k.startswith("2") and v), None)
            if ok:
                fields = ok["[]"] if isinstance(ok.get("[]"), dict) else ok
                details.append("fields: " + ", ".join(f"`{f}`" for f in fields)
                               + " (shapes in `model.yaml`)")  # fmt: skip
        if route.sends:
            details.append("automations send: " + ", ".join(f"`{f}`" for f in route.sends))
        if route.reads:
            details.append("automations read: " + ", ".join(f"`{f}`" for f in route.reads)
                           + " (return every one, with these names)")  # fmt: skip
        if route.timings:
            t = route.timings
            details.append(f"recorded {t.count} call(s): p50 {t.p50_ms} ms, p95 {t.p95_ms} ms, "
                           f"max {t.max_ms} ms")  # fmt: skip
        if details:
            w(f"- `{key}`: " + ". ".join(details))
    w("")

    if brief.browser_only:
        for line in browser_storage(brief):
            w(line)
    else:
        w("### Tables\n")
        w("Create them from [`db/schema.sql`](db/schema.sql), unchanged.\n")
        w("| Table | Basis | Model | Why |\n|---|---|---|---|")
    for name, reasons in ({} if brief.browser_only else brief.tables).items():
        table = brief.table(name)
        where = (
            f"`{table.model}` ({table.at.file}:{table.at.line})"
            if table.at
            else "columns and types inferred from recorded responses"
            if table.basis == "inferred"
            else "column types assumed"
        )
        w(f"| `{name}` | {table.basis} | {where} | {'; '.join(reasons)} |")
    w("")

    codes = {k: v for k, v in model.codes.items() if k.split(".", 1)[0] in brief.tables}
    if codes:
        w("### Code lists\n")
        w(
            "Values seen in the evidence. Use the same codes; add others only if a person names them.\n"
        )
        w("| Field | Codes |\n|---|---|")
        for key, values in codes.items():
            w(f"| `{key}` | {', '.join(f'`{v}`' for v in values)} |")
        w("")

    w("### Rules\n")
    w(
        "Each is a hypothesis, recorded in `app.yaml`. Reproduce it; do not raise its verification.\n"
    )
    w("| Id | Where | Basis | Rule |\n|---|---|---|---|")
    for rule in rules(brief):
        text = rule["rule"].replace("|", "\\|")
        w(f"| {rule['id']} | `{rule['where']}` | {rule['basis']} | {text} |")
    w("")

    w("## Out of scope\n")
    w("In the evidence, needed by no registered automation or requested flow. Do not build these. "
      "If one is needed later, register the automation or flow that needs it and regenerate.\n")  # fmt: skip
    if brief.out_screens:
        w("- Screens: " + ", ".join(
            f"`{s.path or s.name}`" + (f" ({s.component})" if s.component else "") for s in brief.out_screens))  # fmt: skip
    if brief.out_routes:
        w("- Routes: " + ", ".join(f"`{r.key}`" for r in brief.out_routes))
    if brief.out_tables:
        w("- Tables: " + ", ".join(f"`{t.name}`" for t in brief.out_tables))
    if not (brief.out_screens or brief.out_routes or brief.out_tables):
        w("- Nothing: everything in the evidence is needed.")
    w("")

    w("## Running in the lab\n")
    w(f"- Read configuration from the same environment variables as the evidence shows: "
      f"{', '.join(f'`{e}`' for e in model.stack.env) or 'none found'}.")  # fmt: skip
    if brief.browser_only:
        w("- There is no database. The container only serves the files; the business data stays in "
          "browser storage under the keys in *Browser storage*. Its health is hosting health, not "
          "business-data health.")  # fmt: skip
    else:
        w(f"- The database is a {brief.db_engine} server outside the container, reached through "
          "the address in the environment, so the lab can put a proxy in front of it. Include its "
          "driver in the image. Create tables from `db/schema.sql` when the database has "
          "none.")  # fmt: skip
    w("- Ship no customer data. A synthetic seed may be kept for local runs; the lab loads data "
      "packs over it.")  # fmt: skip
    w("- One Dockerfile for the app, no lab code inside it.\n")

    w("## Acceptance checks\n")
    w(
        "1. Every element with a selector under *Screens* resolves on its screen (`labctl conformance`, T-07)."
    )
    w("2. Every route under *API routes* answers on the same method and path, with the same request "
      "and response fields as the evidence shows. Fields marked *automations read* come from the "
      "bots' own code: a missing one breaks the bot even when conformance is READY.")  # fmt: skip
    if brief.browser_only:
        w("3. The app writes and reads its data in the browser storage named under *Browser "
          "storage*, with the same keys and record shape; `db/schema.sql` has no table and "
          "nothing in the app opens a database.")  # fmt: skip
    else:
        w("3. `db/schema.sql` applies unchanged and the app works on it.")
    w("4. Every rule under *Rules* behaves as stated; each stays `hypothesis` in `app.yaml`.")
    w("5. Nothing under *Out of scope* exists in the demo app.\n")

    w("## Open questions for the reviewer\n")
    if brief.gaps:
        for gap in brief.gaps:
            w(f"- {gap}")
    else:
        w("- None found by the analysers. A person still reviews this brief before it is built.")
    w("")
    return "\n".join(out)
