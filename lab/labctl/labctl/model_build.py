"""Build app models from each kind of evidence, and merge them into one.

Each builder returns a model holding only what its evidence shows. `merge` combines them: the
same screen, route or table seen by several kinds of evidence becomes one item listing all of
them, with the strongest basis; disagreements become conflicts for a person to settle.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from labctl import source as src
from labctl.appmodel import (
    PRIORITY,
    STRENGTH,
    AppModel,
    Call,
    Column,
    Conflict,
    Element,
    Evidence,
    Flow,
    Route,
    Rule,
    Screen,
    Stack,
    Table,
    Where,
    stronger,
)
from labctl.models import ApiTouch, Automation, DbTouch, WebTouch
from labctl.source import fastapi_app, react_app


def rel(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def digest(paths: list[Path], root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return "sha256:" + h.hexdigest()[:16]


def model_digest(model: AppModel) -> str:
    data = json.dumps(model.model_dump(mode="json", by_alias=True), sort_keys=True)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()[:16]


def screen_name(path: str) -> str:
    fixed = [s for s in src.segments(path) if s]
    name = "-".join(fixed) or "home"
    return name + "-item" if src.segments(path) and src.segments(path)[-1] is None else name


def who(evidence_ids: list[str], kind: str = "automation") -> str:
    return ", ".join(e.split(":", 1)[1] for e in evidence_ids if e.startswith(kind + ":"))


def check_selector(selector: str, ids: set[str], classes: set[str]) -> tuple[str, str]:
    """Whether the ids and classes a CSS selector names appear on a screen."""
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


# --- source code -------------------------------------------------------------------------------


def from_source(app: str, source: Path, repo_root: Path) -> AppModel:
    source = Path(source).resolve()
    if repo_root.resolve() not in source.parents:
        repo_root = source.parent  # never write a machine's absolute paths into the model
    frontend = react_app.analyse(source)
    backend = fastapi_app.analyse(source)
    read = sorted(
        {p.resolve() for p in src.files(source, (".py", ".tsx", ".ts", ".jsx", ".js", ".json"))}
    )
    read = [p for p in read if "package-lock" not in p.name]
    ev = f"source:{rel(source, repo_root)}"
    model = AppModel(
        app=app,
        evidence=[
            Evidence(
                id=ev,
                kind="source",
                ref=digest(read, source),
                complete=["screens", "routes", "tables"],
            )
        ],
    )
    model.problems += [f"frontend: {p}" for p in frontend.problems]
    model.problems += [f"backend: {p}" for p in backend.problems]

    routers = {s.router_file for s in frontend.screens}
    for screen in frontend.screens:
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in [*screen.files, *routers]]
        ids, classes = react_app.ids_and_classes(texts)
        model.screens.append(
            Screen(
                name=screen_name(screen.path),
                path=screen.path,
                basis="observed",
                **{"from": [ev]},
                component=screen.component,
                at=Where(file=rel(screen.router_file, repo_root), line=screen.line),
                files=[rel(f, repo_root) for f in screen.files],
                ids=sorted(ids),
                classes=sorted(classes),
                calls=[
                    Call(
                        method=c.method,
                        path=c.path,
                        at=Where(file=rel(c.file, repo_root), line=c.line),
                    )
                    for c in frontend.calls_of(screen)
                ],
            )
        )

    for route in backend.routes:
        model.routes.append(
            Route(
                method=route.method,
                path=route.path,
                basis="observed",
                **{"from": [ev]},
                handler=route.handler,
                at=Where(file=rel(route.file, repo_root), line=route.line),
                doc=route.doc,
                query=route.query,
                body=route.body,
                tables=route.tables,
            )
        )
        for found in route.rules:
            _add_rule(model, Rule(text=found.text(), where=[route.key], basis="observed",
                                  status=found.status, code=found.code, **{"from": [ev]}))  # fmt: skip
        for constraint in route.body:
            if "one of" in constraint or any(
                f"{c}=" in constraint for c in fastapi_app.CONSTRAINTS
            ):
                _add_rule(model, Rule(text=f"request body {constraint}", where=[route.key],
                                      basis="observed", **{"from": [ev]}))  # fmt: skip

    for table in backend.tables:
        model.tables.append(
            Table(
                name=table.name,
                basis="observed",
                **{"from": [ev]},
                model=table.model,
                at=Where(file=rel(table.file, repo_root), line=table.line),
                columns=[
                    Column(
                        name=c.name,
                        type=c.type,
                        nullable=c.nullable,
                        primary_key=c.primary_key,
                        unique=c.unique,
                        index=c.index,
                        references=list(c.references) if c.references else None,
                        autoincrement=c.autoincrement,
                    )
                    for c in table.columns
                ],
            )
        )

    model.stack = Stack(
        frontend=frontend.stack["frontend"],
        backend=backend.stack["backend"],
        orm=backend.stack["orm"],
        db=backend.stack["db"],
        pinned=frontend.stack["pinned"],
        env=backend.env + frontend.env,
        **{"from": [ev]},
    )
    return model


# --- automations -------------------------------------------------------------------------------


def from_automations(app: str, automations: list[Automation]) -> AppModel:
    model = AppModel(app=app)
    for auto in automations:
        touches = [t for t in auto.touches if t.app == app]
        if not touches:
            continue
        ev = f"automation:{auto.id}"
        model.evidence.append(Evidence(id=ev, kind="automation", ref=auto.analysis.by))
        if auto.analysis.gaps:
            model.problems.append(
                f"{auto.id} has {len(auto.analysis.gaps)} unresolved analysis gap(s); "
                "what those gaps hide is not in this model"
            )
        part = AppModel(app=app)
        for touch in touches:
            if isinstance(touch, WebTouch):
                for screen in touch.screens:
                    part.screens.append(
                        Screen(
                            name=screen.name,
                            path=screen.path,
                            basis="declared",
                            **{"from": [ev]},
                            elements=[
                                Element(
                                    selector=u.selector,
                                    actions=[u.action],
                                    basis="declared",
                                    **{"from": [ev]},
                                )
                                for u in screen.uses
                            ],
                        )
                    )
            elif isinstance(touch, ApiTouch):
                for r in touch.routes:
                    part.routes.append(
                        Route(
                            method=r.method,
                            path=r.path,
                            basis="declared",
                            sends=r.sends,
                            reads=r.reads,
                            **{"from": [ev]},
                        )
                    )
            elif isinstance(touch, DbTouch):
                for t in touch.tables:
                    part.tables.append(
                        Table(
                            name=t.table,
                            basis="declared",
                            **{"from": [ev]},
                            columns=[Column(name=c, type="TEXT") for c in t.columns],
                        )
                    )
        _merge_into(model, part)
    return model


# --- flows a person asked for ------------------------------------------------------------------


def from_flows(app: str, flows: list[Flow]) -> AppModel:
    model = AppModel(app=app)
    for flow in (f for f in flows if f.app == app):
        ev = f"flow:{flow.id}"
        model.evidence.append(Evidence(id=ev, kind="flow", ref=flow.requested_by))
        model.flows.append(flow.id)
        part = AppModel(app=app)
        for step in flow.steps:
            part.screens.append(
                Screen(
                    name=step.screen,
                    path=step.path,
                    basis="described",
                    **{"from": [ev]},
                    elements=[
                        Element(label=f, basis="described", **{"from": [ev]}) for f in step.fields
                    ],
                )
            )
            for text in step.rules:
                _add_rule(
                    part, Rule(text=text, where=[step.screen], basis="described", **{"from": [ev]})
                )
        _merge_into(model, part)
    return model


# --- recordings and API descriptions --------------------------------------------------------------


def from_recording(app: str, path: Path, repo_root: Path, pages_dir: Path | None) -> AppModel:
    """What a browser recording shows (`labctl.har`): a folder or a bare `.har` file.

    Writes value-free page snapshots to `pages_dir` (normally `apps/<app>/pages/`) when given.
    """
    from labctl import har

    model = har.analyse(app, Path(path), Path(repo_root), pages_dir)
    _documents(model, Path(path))
    return model


def _documents(model: AppModel, path: Path) -> None:
    """PDFs the recording showed (the recorder's docs/documents.json) become screens of their own:
    the builder sees the document's address and the names of its form fields, never values."""
    import json
    from urllib.parse import urlsplit

    from labctl import sanitize

    index = path / "docs" / "documents.json" if path.is_dir() else None
    if index is None or not index.exists():
        return
    try:
        docs = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    ev = model.evidence[0].id if model.evidence else f"recording:{path.name}"
    for doc in docs:
        route = sanitize.route_pattern(urlsplit(doc.get("url") or "").path or "/")
        fields = [f.get("name") for f in doc.get("fields") or [] if f.get("name")]
        model.screens.append(Screen(
            name=f"document {route}", path=route, basis="observed", from_=[ev], snapshots=0,
            elements=[Element(selector=None, label=f, basis="observed", from_=[ev]) for f in fields],
        ))  # fmt: skip
        pages = doc.get("pages")
        model.problems.append(
            f"{route} is a PDF document ({pages or '?'} page(s), {len(fields)} form field(s)): "
            "the demo app must serve a PDF there; its text is in the recording's docs/ folder"
        )


from_har = from_recording  # the name docs/dev/integration.md uses


def from_openapi(app: str, path: Path) -> AppModel:
    """Declared routes with request and response shapes: OpenAPI 3.x or a Postman v2.1 collection."""
    from labctl import openapi

    return openapi.analyse(app, Path(path))


# --- merging -----------------------------------------------------------------------------------


def _union(a: list[str], b: list[str]) -> list[str]:
    return a + [x for x in b if x not in a]


def _add_rule(model: AppModel, rule: Rule) -> None:
    for have in model.rules:
        if have.text == rule.text:
            have.where = _union(have.where, rule.where)
            have.from_ = _union(have.from_, rule.from_)
            have.basis = stronger(have.basis, rule.basis)
            return
    model.rules.append(rule)


def same_id(a: str | None, b: str | None) -> bool:
    """`#message` and `div#message` name the same element."""
    simple = re.compile(r"^([a-z0-9]*)#([\w-]+)$", re.I)
    ma, mb = simple.match(a or ""), simple.match(b or "")
    if not (ma and mb) or ma.group(2) != mb.group(2):
        return False
    return not ma.group(1) or not mb.group(1) or ma.group(1).lower() == mb.group(1).lower()


def _find_screen(screens: list[Screen], screen: Screen) -> Screen | None:
    """The screen with the best-matching path, or with the same name when a path is missing."""
    if screen.path:
        found = next((s for s in screens if s.path and src.same_shape(s.path, screen.path)), None)
        if found:
            return found
    name = screen.name.strip().lower()
    return next(
        (s for s in screens if s.name.strip().lower() == name and not (s.path and screen.path)),
        None,
    )


def _merge_screen(have: Screen, new: Screen) -> None:
    keep_have = have.basis == stronger(have.basis, new.basis)
    strong, weak = (have, new) if keep_have else (new, have)
    for attr in ("path", "component", "at"):
        setattr(have, attr, getattr(strong, attr) or getattr(weak, attr))
    name = strong.name if strong.basis != "described" else weak.name
    for other in (have.name, new.name, *have.aliases, *new.aliases):
        if other != name and other not in have.aliases:
            have.aliases.append(other)
    have.name = name
    have.files = strong.files or weak.files
    for attr in ("ids", "classes"):
        if have.basis == new.basis == "observed":  # source and a recording: both are real
            setattr(have, attr, sorted(set(getattr(have, attr)) | set(getattr(new, attr))))
        else:
            setattr(have, attr, getattr(strong, attr) or getattr(weak, attr))
    if new.snapshots is not None:
        have.snapshots = (have.snapshots or 0) + new.snapshots
    have.calls = have.calls + [
        c for c in new.calls if (c.method, c.path) not in {(x.method, x.path) for x in have.calls}
    ]
    for el in new.elements:
        same = next(
            (
                e
                for e in have.elements
                if (el.selector and e.selector == el.selector)
                or (el.selector and same_id(e.selector, el.selector))
                or (not el.selector and e.label == el.label)
            ),
            None,
        )
        if same:
            if same.selector != el.selector and any(f.startswith("automation:") for f in el.from_):
                same.selector = el.selector  # the automation's selector is what must resolve
            for attr in ("label", "tag", "name", "type"):
                setattr(same, attr, getattr(same, attr) or getattr(el, attr))
            for attr in ("classes", "options"):
                setattr(same, attr, _union(getattr(same, attr), getattr(el, attr)))
            same.actions = _union(same.actions, el.actions)
            same.from_ = _union(same.from_, el.from_)
            same.basis = stronger(same.basis, el.basis)
        else:
            have.elements.append(el)
    have.basis = stronger(have.basis, new.basis)
    have.from_ = _union(have.from_, new.from_)


def _merge_into(model: AppModel, part: AppModel) -> None:
    model.evidence += [e for e in part.evidence if e.id not in {x.id for x in model.evidence}]
    model.problems += [p for p in part.problems if p not in model.problems]
    model.flows = _union(model.flows, part.flows)
    if not model.stack.frontend and not model.stack.backend:
        model.stack = part.stack
    else:  # a recording's detected server technology and libraries add to a stack read from source
        if part.stack.server and not model.stack.server:
            model.stack.server = part.stack.server
        for kind in ("libraries", "detected"):
            have = getattr(model.stack, kind)
            have += [x for x in getattr(part.stack, kind) if x not in have]
    for screen in part.screens:
        have = _find_screen(model.screens, screen)
        if have:
            _merge_screen(have, screen)
        else:
            model.screens.append(screen)
    for route in part.routes:
        have = next(
            (
                r
                for r in model.routes
                if r.method == route.method and src.same_shape(r.path, route.path)
            ),
            None,
        )
        if have:
            attrs = ("handler", "at", "doc", "query", "body", "tables", "responses", "timings")
            if route.basis == stronger(route.basis, have.basis) and route.basis != have.basis:
                for attr in attrs:
                    setattr(have, attr, getattr(route, attr) or getattr(have, attr))
                have.path = route.path
            else:  # weaker evidence fills only what the stronger one lacks
                for attr in attrs:
                    setattr(have, attr, getattr(have, attr) or getattr(route, attr))
            have.basis = stronger(have.basis, route.basis)
            have.from_ = _union(have.from_, route.from_)
            have.sends = _union(have.sends, route.sends)  # every automation's needs count
            have.reads = _union(have.reads, route.reads)
        else:
            model.routes.append(route)
    for table in part.tables:
        have = next((t for t in model.tables if t.name.lower() == table.name.lower()), None)
        if have is None:
            model.tables.append(table)
            continue
        strong, weak = (
            (table, have) if STRENGTH[table.basis] > STRENGTH[have.basis] else (have, table)
        )
        names = {c.name.lower() for c in strong.columns}
        extra = [c for c in weak.columns if c.name.lower() not in names]
        if weak.basis == "inferred" and all(f.startswith("automation:") for f in strong.from_):
            # An automation names columns, not types; recorded payloads show the types.
            seen = {c.name.lower(): c for c in weak.columns}
            strong.columns = [
                seen[c.name.lower()].model_copy(update={"name": c.name})
                if c.name.lower() in seen and c.type == "TEXT"
                else c
                for c in strong.columns
            ]
        if strong.basis == "observed":  # a column the evidence lacks is a question, not a column
            for column in extra:
                model.conflicts.append(Conflict(
                    what=f"{strong.name}.{column.name} is queried by {who(weak.from_)} "
                         "but the model has no such column",
                    evidence=_union(weak.from_, strong.from_),
                ))  # fmt: skip
            extra = []
        have.name, have.model, have.at = strong.name, strong.model, strong.at
        have.columns = list(strong.columns) + extra
        have.basis = stronger(have.basis, table.basis)
        have.from_ = _union(have.from_, table.from_)
    for rule in part.rules:
        _add_rule(model, rule)
    for key, values in part.codes.items():
        model.codes[key] = sorted(set(model.codes.get(key, [])) | set(values))


def merge(app: str, parts: list[AppModel]) -> AppModel:
    """One model from several. Stronger evidence first, so its names and details are kept."""

    def rank(part: AppModel) -> int:
        kinds = [e.kind for e in part.evidence] or ["flow"]
        return min(PRIORITY.index(k) if k in PRIORITY else len(PRIORITY) for k in kinds)

    model = AppModel(app=app)
    for part in sorted(parts, key=rank):
        _merge_into(model, part)
    names = {t.name.lower(): t.name for t in model.tables}
    for route in model.routes:  # table names as merged (case may differ between evidence)
        route.tables = list(
            dict.fromkeys(names[t.lower()] for t in route.tables if t.lower() in names)
        )
    _find_conflicts(model)
    return model


def _find_conflicts(model: AppModel) -> None:
    complete = {area: model.complete(area) for area in ("screens", "routes", "tables")}
    for screen in model.screens:
        inventory = screen.basis == "observed" and (screen.ids or screen.classes)
        for el in screen.elements:
            if not el.selector or el.basis == "observed" or not inventory:
                continue
            status, detail = check_selector(el.selector, set(screen.ids), set(screen.classes))
            if status == "found":
                el.basis = "observed"
                el.from_ = _union(
                    el.from_,
                    [e for e in screen.from_ if not e.startswith(("automation:", "flow:"))],
                )
            elif status == "missing":
                model.conflicts.append(Conflict(
                    what=f"selector {el.selector} on {screen.path}: {detail}; it may be built at run time, "
                         "or the automation and the source disagree",
                    evidence=_union(el.from_, [e for e in screen.from_ if e not in el.from_]),
                ))  # fmt: skip
        if (
            complete["screens"]
            and screen.basis == "declared"
            and not set(screen.from_) & set(complete["screens"])
        ):
            model.conflicts.append(Conflict(
                what=f"screen {screen.name} ({screen.path or 'path unknown'}) is used by {who(screen.from_)} "
                     "but no route in the frontend matches it",
                evidence=screen.from_ + complete["screens"],
            ))  # fmt: skip
    for route in model.routes:
        if (
            complete["routes"]
            and route.basis == "declared"
            and not set(route.from_) & set(complete["routes"])
        ):
            model.conflicts.append(Conflict(
                what=f"{route.key} is called by {who(route.from_)} but no backend route serves it",
                evidence=route.from_ + complete["routes"],
            ))  # fmt: skip
    for table in model.tables:
        if (
            complete["tables"]
            and table.basis == "declared"
            and not set(table.from_) & set(complete["tables"])
        ):
            model.conflicts.append(Conflict(
                what=f"table {table.name} is queried by {who(table.from_)} but no model declares it",
                evidence=table.from_ + complete["tables"],
            ))  # fmt: skip
