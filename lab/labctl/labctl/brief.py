"""Build brief: the demand report joined with what the real application's source contains.

Scope follows the demand, never the source: a screen, route or table is in scope only when a
registered automation needs it, directly or through something it needs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from labctl import source as src
from labctl.source import fastapi_app, react_app
from labctl.source import sqlalchemy_models as sam

HEALTH = re.compile(r"/(health|healthz|ready|actuator/health)$")


@dataclass
class Element:
    selector: str
    actions: list[str]
    automations: list[str]
    status: str  # found | missing | unchecked
    detail: str = ""


@dataclass
class ScreenScope:
    name: str
    demanded_path: str | None
    screen: react_app.Screen | None
    elements: list[Element]


@dataclass
class Brief:
    app: str
    system: str
    source: Path
    digest: str
    needed_by: list[str]
    frontend: react_app.Frontend
    backend: fastapi_app.Backend
    screens: list[ScreenScope] = field(default_factory=list)
    routes: dict[str, list[str]] = field(default_factory=dict)  # route key -> reasons
    tables: dict[str, list[str]] = field(default_factory=dict)  # table name -> reasons
    gaps: list[str] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    db_engine: str = "sqlite"
    db_engine_set: bool = False  # True when a person named the engine, not the source

    @property
    def found_engine(self) -> str:
        return self.backend.stack["db"]["engine"]

    def route(self, key: str) -> fastapi_app.Route:
        return next(r for r in self.backend.routes if r.key == key)

    def table(self, name: str) -> sam.Table:
        return next(t for t in self.backend.tables if t.name == name)

    @property
    def out_screens(self) -> list[react_app.Screen]:
        used = {id(s.screen) for s in self.screens if s.screen}
        return [s for s in self.frontend.screens if id(s) not in used]

    @property
    def out_routes(self) -> list[fastapi_app.Route]:
        return [r for r in self.backend.routes if r.key not in self.routes]

    @property
    def out_tables(self) -> list[sam.Table]:
        return [t for t in self.backend.tables if t.name not in self.tables]


def _add(reasons: dict[str, list[str]], key: str, reason: str) -> None:
    items = reasons.setdefault(key, [])
    if reason not in items:
        items.append(reason)


def digest(paths: list[Path], root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return "sha256:" + h.hexdigest()[:16]


def check_selector(selector: str, ids: set[str], classes: set[str]) -> tuple[str, str]:
    """Whether the ids and classes a CSS selector names appear in the screen's source."""
    if selector.startswith("uipath:"):
        return "unchecked", "not CSS; the conformance check decides"
    if "[" in selector:
        return "unchecked", "attribute selector; the conformance check decides"
    want_ids = re.findall(r"#([\w-]+)", selector)
    want_classes = re.findall(r"\.([\w-]+)", selector)
    missing = [f"#{i}" for i in want_ids if i not in ids] + [
        f".{c}" for c in want_classes if c not in classes
    ]
    if missing:
        return "missing", f"{', '.join(missing)} not in this screen's source"
    if not want_ids and not want_classes:
        return "unchecked", "tag only"
    return "found", ""


def build(
    app: str,
    system: str,
    source: Path,
    report: dict,
    repo_root: Path,
    db_engine: str | None = None,
) -> Brief:
    source = source.resolve()
    frontend = react_app.analyse(source)
    backend = fastapi_app.analyse(source)
    read = sorted(
        {p.resolve() for p in src.files(source, (".py", ".tsx", ".ts", ".jsx", ".js", ".json"))}
    )
    read = [p for p in read if "package-lock" not in p.name]
    brief = Brief(
        app=app,
        system=system,
        source=source,
        digest=digest(read, source),
        needed_by=report["needed_by"],
        frontend=frontend,
        backend=backend,
        unresolved=report.get("unresolved", []),
    )
    found = backend.stack["db"]["engine"]
    brief.db_engine = db_engine or (found if found in sam.ENGINES else "postgres")
    brief.db_engine_set = db_engine is not None
    if db_engine is None and found not in sam.ENGINES:
        brief.gaps.append(f"database engine '{found}' not recognised; postgres assumed")
    if db_engine is None and found == "sqlite":
        brief.gaps.append(
            "the source only shows SQLite (often a development default); name the engine the "
            "real application runs with --db-engine. The lab needs a database server it can "
            "put a proxy in front of"
        )
    brief.gaps += [f"frontend: {p}" for p in frontend.problems]
    brief.gaps += [f"backend: {p}" for p in backend.problems]

    def route_for(method: str, path: str) -> fastapi_app.Route | None:
        same = [r for r in backend.routes if r.method == method]
        return src.best_match(path, same, key=lambda r: r.path)

    # Screens the automations use, and the API calls their source makes.
    router_texts = {s.router_file for s in frontend.screens}
    for demanded in report["screens"]:
        path = demanded["path"] or ""
        screen = src.best_match(path, frontend.screens, key=lambda s: s.path) if path else None
        if screen is None:
            brief.gaps.append(
                f"screen {demanded['name']} ({path or 'path unknown'}) is used by an automation "
                "but no route in the frontend matches it"
            )
        texts = [
            p.read_text(encoding="utf-8", errors="replace")
            for p in (screen.files if screen else [])
        ]
        texts += [p.read_text(encoding="utf-8", errors="replace") for p in router_texts]
        ids, classes = react_app.ids_and_classes(texts)
        elements = []
        for el in demanded["elements"]:
            status, detail = (
                check_selector(el["selector"], ids, classes) if screen else ("unchecked", "")
            )
            elements.append(
                Element(el["selector"], el["actions"], el["automations"], status, detail)
            )
            if status == "missing":
                brief.gaps.append(
                    f"selector {el['selector']} on {path}: {detail}; it may be built at run time, "
                    "or the automation and the source disagree"
                )
        brief.screens.append(ScreenScope(demanded["name"], path, screen, elements))
        if screen is None:
            continue
        for call in frontend.calls_of(screen):
            route = route_for(call.method, call.path)
            rel = call.file.relative_to(frontend.root).as_posix()
            if route is None:
                brief.gaps.append(
                    f"{rel}:{call.line} calls {call.method} {call.path}, which no backend route serves"
                )
                continue
            _add(brief.routes, route.key, f"called by screen {screen.path} ({rel})")

    # Routes the automations call directly.
    for demanded in report["routes"]:
        route = route_for(demanded["method"], demanded["path"])
        if route is None:
            brief.gaps.append(
                f"{demanded['method']} {demanded['path']} is called by "
                f"{', '.join(demanded['automations'])} but no backend route serves it"
            )
            continue
        _add(brief.routes, route.key, f"called directly by {', '.join(demanded['automations'])}")

    health = [r for r in backend.routes if r.method == "GET" and HEALTH.search(r.path)]
    for route in health[:1]:
        _add(brief.routes, route.key, "health check for the lab agent")
    if not health:
        brief.gaps.append("the source has no health route; the demo app must add GET /api/health")

    # Tables behind those routes, tables the automations query, and their parents.
    for key in list(brief.routes):
        for table in brief.route(key).tables:
            _add(brief.tables, table, f"used by {key}")
    by_lower = {t.name.lower(): t for t in backend.tables}
    for demanded in report["tables"]:
        table = by_lower.get(demanded["table"].lower())
        who = ", ".join(demanded["automations"])
        if table is None:
            brief.gaps.append(
                f"table {demanded['table']} is queried by {who} but no model declares it"
            )
            continue
        _add(brief.tables, table.name, f"queried directly by {who}")
        have = {c.name.lower() for c in table.columns}
        for column in demanded["columns"]:
            if column.lower() not in have:
                brief.gaps.append(
                    f"{table.name}.{column} is queried by {who} but the model has no such column"
                )
    changed = True
    while changed:
        changed = False
        for name in list(brief.tables):
            for column in brief.table(name).columns:
                if (
                    column.references
                    and column.references[0] != name
                    and column.references[0] in by_lower
                ):
                    if column.references[0] not in brief.tables:
                        changed = True
                    _add(
                        brief.tables,
                        column.references[0],
                        f"parent of {name} ({column.name} -> {column.references[0]}.{column.references[1]})",
                    )
    order = [t.name for t in sam.ordered(backend.tables)]
    brief.tables = {k: brief.tables[k] for k in order if k in brief.tables}
    brief.routes = {r.key: brief.routes[r.key] for r in backend.routes if r.key in brief.routes}
    return brief


def rules(brief: Brief) -> list[dict]:
    """In-scope rules, each listed once with every route it applies to. All are hypotheses."""
    grouped: dict[str, list[str]] = {}
    for key in brief.routes:
        route = brief.route(key)
        for rule in route.rules:
            grouped.setdefault(rule.text(), []).append(key)
        for constraint in route.body:
            if "one of" in constraint or any(
                f"{c}=" in constraint for c in fastapi_app.CONSTRAINTS
            ):
                grouped.setdefault(f"request body {constraint}", []).append(key)
    return [
        {"id": f"R{i}", "where": ", ".join(wheres), "rule": text, "verification": "hypothesis"}
        for i, (text, wheres) in enumerate(grouped.items(), start=1)
    ]


def schema(brief: Brief, repo_root: Path) -> str:
    tables = [brief.table(name) for name in brief.tables]
    left_out = ", ".join(t.name for t in brief.out_tables) or "none"
    header = (
        f"Schema for the {brief.app} demo app. Generated by labctl brief; do not edit by hand.\n"
        f"Read statically from {_rel(brief.source, repo_root)} ({brief.digest}).\n"
        f"Only tables the registered automations need. Left out: {left_out}.\n"
        f"Dialect: {brief.db_engine}. Table and column names must not change."
    )
    return sam.ddl(tables, header, brief.db_engine)


def app_yaml(brief: Brief, repo_root: Path) -> dict:
    fe, be = brief.frontend.stack["frontend"], brief.backend.stack
    return {
        "id": brief.app,
        "name": f"{brief.system} (demo copy)",
        "copies": {
            "system": brief.system,
            "source": _rel(brief.source, repo_root),
            "ref": brief.digest,
            "analysed": date.today().isoformat(),
        },
        "stack": {
            "frontend": {k: v for k, v in fe.items() if v is not None},
            "backend": be["backend"],
            "db": {"engine": brief.db_engine},
        },
        "run": {},
        "db": {"schema": "db/schema.sql", "derived_from": ["sqlalchemy models"]},
        "built_for": brief.needed_by,
        "rules": rules(brief),
    }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def render(brief: Brief, repo_root: Path) -> str:
    def rel(p: Path) -> str:
        return _rel(p, repo_root)

    out: list[str] = []
    w = out.append
    w(f"# Build brief: {brief.app}\n")
    w(
        f"A demo copy of **{brief.system}**, scoped to what registered automations use. Generated by "
        "`labctl brief`; regenerate rather than edit. Rules for building it: "
        "`docs/factory-playbook.md` (T-06).\n"
    )
    w("| | |\n|---|---|")
    w(f"| Source | `{rel(brief.source)}` ({brief.digest}) |")
    w(f"| Needed by | {', '.join(f'`{a}`' for a in brief.needed_by)} |")
    w(f"| Generated | {date.today().isoformat()} |")
    w("| Reviewed by | *nobody yet* |\n")

    if brief.unresolved:
        w("> **Incomplete demand.** " + "; ".join(
            f"`{u['automation']}` has {u['gaps']} unresolved analysis gap(s)" for u in brief.unresolved
        ) + ". What those gaps hide is not in this brief.\n")  # fmt: skip

    w("## Stack to pin\n")
    w("Use the same versions as the source, so components render the same elements.\n")
    w("| Part | Source has |\n|---|---|")
    for name, version in brief.frontend.stack["pinned"].items():
        w(f"| `{name}` | {version} |")
    be = brief.backend.stack
    w(f"| `fastapi` | {be['backend']['version']} |")
    w(f"| `sqlalchemy` | {be['orm']['version']} |")
    origin = (
        f"named when the brief was generated; the source shows {brief.found_engine}"
        if brief.db_engine_set
        else "found in the source"
    )
    w(f"| database | {brief.db_engine} ({origin}) |\n")
    if any(not v[0].isdigit() for v in [be["backend"]["version"], be["orm"]["version"]]):
        w(
            "Backend versions are ranges in the source. Pin the newest release in each range, and "
            "say which in BUILD-NOTES.md, unless the reviewer has named the version the real "
            "application runs.\n"
        )

    w("## In scope\n")
    w("### Screens\n")
    for scope in brief.screens:
        screen = scope.screen
        where = (
            f"route `{screen.path}` in `{rel(screen.router_file)}:{screen.line}`, component "
            f"`{screen.component}` in `{rel(screen.file)}`"
            if screen
            else "**not found in the source**"
        )
        w(f"**{scope.demanded_path}** ({scope.name}): {where}\n")
        w("| Selector | Used for | By | In source |\n|---|---|---|---|")
        for el in scope.elements:
            mark = {"found": "yes", "missing": "**missing**", "unchecked": "not checked"}[el.status]
            note = f" ({el.detail})" if el.detail else ""
            w(
                f"| `{el.selector}` | {', '.join(el.actions)} | {', '.join(el.automations)} | {mark}{note} |"
            )
        if screen:
            files = ", ".join(f"`{rel(f)}`" for f in screen.files)
            w(f"\nRead: {files}. Reproduce what these files render and do on this screen, "
              "including elements no automation names: they decide layout and behaviour.\n")  # fmt: skip

    w("### API routes\n")
    w("| Route | Handler | Why |\n|---|---|---|")
    for key, reasons in brief.routes.items():
        route = brief.route(key)
        w(
            f"| `{key}` | `{rel(route.file)}:{route.line}` `{route.handler}` | {'; '.join(reasons)} |"
        )
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
        if details:
            w(f"- `{key}`: " + ". ".join(details))
    w("")

    w("### Tables\n")
    w("Create them from [`db/schema.sql`](db/schema.sql), unchanged.\n")
    w("| Table | Model | Why |\n|---|---|---|")
    for name, reasons in brief.tables.items():
        table = brief.table(name)
        w(f"| `{name}` | `{table.model}` ({rel(table.file)}:{table.line}) | {'; '.join(reasons)} |")
    w("")

    w("### Rules\n")
    w("Each is a hypothesis read from the source, recorded in `app.yaml`. Reproduce it; do not "
      "raise its verification.\n")  # fmt: skip
    w("| Id | Where | Rule |\n|---|---|---|")
    for rule in rules(brief):
        text = rule["rule"].replace("|", "\\|")
        w(f"| {rule['id']} | `{rule['where']}` | {text} |")
    w("")

    w("## Out of scope\n")
    w("Present in the source, needed by no registered automation. Do not build these. If one is "
      "needed later, register the automation that needs it and regenerate this brief.\n")  # fmt: skip
    if brief.out_screens:
        w("- Screens: " + ", ".join(
            f"`{s.path}` ({s.component})" for s in brief.out_screens))  # fmt: skip
    if brief.out_routes:
        w("- Routes: " + ", ".join(f"`{r.key}`" for r in brief.out_routes))
    if brief.out_tables:
        w("- Tables: " + ", ".join(f"`{t.name}`" for t in brief.out_tables))
    if not (brief.out_screens or brief.out_routes or brief.out_tables):
        w("- Nothing: every screen, route and table in the source is needed.")
    w("")

    w("## Running in the lab\n")
    env = brief.backend.env + brief.frontend.env
    w(f"- Read configuration from the same environment variables as the source: "
      f"{', '.join(f'`{e}`' for e in env) or 'none found'}.")  # fmt: skip
    w(
        f"- The database is a {brief.db_engine} server outside the container, reached through the "
        "address in the environment, so the lab can put a proxy in front of it. Include its "
        "driver in the image. Create tables from `db/schema.sql` when the database has none."
    )
    w("- The database address must come from the environment; the lab agent puts a proxy in front "
      "of the database to slow it down.")  # fmt: skip
    w("- Ship no customer data. The source's own seed may be kept for local runs; the lab loads "
      "data packs over it.")  # fmt: skip
    w("- One Dockerfile for the app, no lab code inside it.\n")

    w("## Acceptance checks\n")
    w("1. Every selector under *Screens* resolves on its screen (`labctl conformance`, T-07).")
    w("2. Every route under *API routes* answers on the same method and path, with the same request "
      "and response fields as its handler.")  # fmt: skip
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
