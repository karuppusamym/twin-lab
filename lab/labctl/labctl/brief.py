"""Build brief: the app model, scoped to what is needed, written for the builder.

The brief reads the app model only (docs/06-evidence.md). Scope follows need, never the evidence:
a screen, route or table is in scope only when a registered automation or a requested flow needs
it, directly or through something it needs.
"""

from __future__ import annotations

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

    def route(self, key: str) -> Route:
        return self.model.route(key)

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


def make_model(
    app: str,
    automations: list[Automation],
    repo_root: Path,
    source: Path | None = None,
    flows: list[Flow] = (),
) -> AppModel:
    """Every kind of evidence available for `app`, merged."""
    parts = [mb.from_automations(app, automations), mb.from_flows(app, list(flows))]
    if source is not None:
        parts.append(mb.from_source(app, source, repo_root))
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
) -> Brief:
    model = make_model(app, automations, repo_root, source, flows)
    return scope(app, system, model, db_engine)


def scope(app: str, system: str, model: AppModel, db_engine: str | None = None) -> Brief:
    needed = [e.id for e in model.evidence if e.id.startswith(NEED)]
    brief = Brief(app=app, system=system, model=model, needed_by=_by(needed))
    brief.gaps += model.problems + [c.what for c in model.conflicts]

    found = brief.found_engine
    brief.db_engine = db_engine or (found if found in sam.ENGINES else "postgres")
    brief.db_engine_set = db_engine is not None
    if db_engine is None and found is None:
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
            else:
                status, detail = "unchecked", "no recording or source of this screen"
            views.append(
                ElementView(
                    el.selector, el.label, el.actions, _by(el.from_), el.basis, status, detail
                )
            )
        brief.screens.append(ScreenScope(screen, reasons, views))
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

    # Tables behind those routes, tables queried directly, and their parents.
    for key in list(brief.routes):
        for table in model.route(key).tables:
            _add(brief.tables, table, f"used by {key}")
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
    return brief


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
    stack: dict = {"db": {"engine": brief.db_engine}}
    if brief.model.stack.frontend:
        stack["frontend"] = {k: v for k, v in brief.model.stack.frontend.items() if v is not None}
    if brief.model.stack.backend:
        stack["backend"] = brief.model.stack.backend
    source = brief.source
    return {
        "id": brief.app,
        "name": f"{brief.system} (demo copy)",
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
        w(f"| database | {brief.db_engine} ({origin}) |\n")
        if any(v and not v[0].isdigit() for v in versions):
            w(
                "Backend versions are ranges in the evidence. Pin the newest release in each range, "
                "and say which in BUILD-NOTES.md, unless the reviewer has named the version the real "
                "application runs.\n"
            )
    else:
        w(f"No evidence shows the stack. Use {DEFAULT_STACK}; database: {brief.db_engine}.\n")

    w("## In scope\n")
    w("### Screens\n")
    for scope in brief.screens:
        screen = scope.screen
        if screen.component and screen.at:
            where = (f"route `{screen.path}` in `{screen.at.file}:{screen.at.line}`, component "
                     f"`{screen.component}` in `{screen.files[0] if screen.files else '?'}`")  # fmt: skip
        else:
            where = "known only from " + ", ".join(f"`{f}`" for f in screen.from_)
        w(f"**{screen.path or '(path unknown)'}** ({screen.name}, {screen.basis}; "
          f"{'; '.join(scope.reasons)}): {where}\n")  # fmt: skip
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
        if details:
            w(f"- `{key}`: " + ". ".join(details))
    w("")

    w("### Tables\n")
    w("Create them from [`db/schema.sql`](db/schema.sql), unchanged.\n")
    w("| Table | Basis | Model | Why |\n|---|---|---|---|")
    for name, reasons in brief.tables.items():
        table = brief.table(name)
        where = (
            f"`{table.model}` ({table.at.file}:{table.at.line})"
            if table.at
            else "column types assumed"
        )
        w(f"| `{name}` | {table.basis} | {where} | {'; '.join(reasons)} |")
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
    w(f"- The database is a {brief.db_engine} server outside the container, reached through the "
      "address in the environment, so the lab can put a proxy in front of it. Include its driver "
      "in the image. Create tables from `db/schema.sql` when the database has none.")  # fmt: skip
    w("- Ship no customer data. A synthetic seed may be kept for local runs; the lab loads data "
      "packs over it.")  # fmt: skip
    w("- One Dockerfile for the app, no lab code inside it.\n")

    w("## Acceptance checks\n")
    w(
        "1. Every element with a selector under *Screens* resolves on its screen (`labctl conformance`, T-07)."
    )
    w("2. Every route under *API routes* answers on the same method and path, with the same request "
      "and response fields as the evidence shows.")  # fmt: skip
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
