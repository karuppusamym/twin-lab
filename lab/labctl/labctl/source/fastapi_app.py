"""FastAPI + SQLAlchemy backends: routes, the tables each route touches, and the rules it enforces.

Read with `ast`; nothing is imported or run. A route's tables are the model classes named in its
handler, in the helpers and dependencies it calls, followed by name. Its rules are the `raise`
statements that carry a 4xx status, and the constraints on its request body model.
"""

from __future__ import annotations

import ast
import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

from labctl.source import UnsupportedStack, files
from labctl.source import sqlalchemy_models as sam

METHODS = {"get", "post", "put", "patch", "delete"}
CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
ENV = re.compile(r"""(?:environ(?:\.get)?\(|getenv\(|environ\[)\s*["'](\w+)["']""")
ENGINES = ("postgresql", "postgres", "mysql", "mariadb", "mssql", "oracle", "sqlite")
CONSTRAINTS = ("min_length", "max_length", "ge", "gt", "le", "lt", "pattern", "max_digits")


@dataclass
class Rule:
    status: int | None
    code: str | None
    message: str | None
    when: str | None
    via: str | None
    file: Path
    line: int

    def text(self) -> str:
        head = " ".join(str(x) for x in (self.status, self.code) if x)
        out = head + (f" when {self.when}" if self.when else "")
        if self.message:
            out += f": {self.message}"
        return out + (f" (in {self.via})" if self.via else "")


@dataclass
class Route:
    method: str
    path: str
    handler: str
    file: Path
    line: int
    doc: str | None = None
    tables: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    body: list[str] = field(default_factory=list)  # request body constraints
    query: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class Backend:
    root: Path
    stack: dict
    routes: list[Route]
    tables: list[sam.Table]
    env: list[str]
    problems: list[str]


@dataclass
class _Fn:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    file: Path


def _names(node: ast.AST) -> set[str]:
    """Names used in a node. For a function: its arguments and body, not its decorators."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parts: list[ast.AST] = [node.args, *node.body]
        if node.returns is not None:
            parts.append(node.returns)
        return {n.id for part in parts for n in ast.walk(part) if isinstance(n, ast.Name)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_dependency(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", None)) in ("Depends", "Security")
        for n in ast.walk(node)
    )


def _const_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class _Inline(ast.NodeTransformer):
    """Replace names of module constants by their values, so a rule reads `< 10`, not `< MIN`."""

    def __init__(self, constants: dict[str, object]):
        self.constants = constants

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.constants:
            return ast.copy_location(ast.Constant(self.constants[node.id]), node)
        return node


def _render(node: ast.AST, constants: dict[str, object]) -> str:
    node = _Inline(constants).visit(copy.deepcopy(node))
    if isinstance(node, ast.JoinedStr):
        out = ""
        for part in node.values:
            if isinstance(part, ast.Constant):
                out += str(part.value)
            elif isinstance(part, ast.FormattedValue):
                inner = part.value
                out += (
                    str(inner.value)
                    if isinstance(inner, ast.Constant)
                    else "{" + ast.unparse(inner) + "}"
                )
        return out
    return ast.unparse(node)


def _strings(node: ast.AST, constants: dict[str, object]) -> list[str]:
    """String constants and f-strings in source order, f-string parts not counted twice."""
    found: list[tuple[int, int, str]] = []

    def walk(n: ast.AST) -> None:
        if isinstance(n, ast.JoinedStr):
            found.append((n.lineno, n.col_offset, _render(n, constants)))
            return
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            found.append((n.lineno, n.col_offset, n.value))
            return
        if isinstance(n, ast.Name) and isinstance(constants.get(n.id), str):
            found.append((n.lineno, n.col_offset, str(constants[n.id])))
            return
        for child in ast.iter_child_nodes(n):
            walk(child)

    walk(node)
    return [s for _, _, s in sorted(found)]


def _status(call: ast.Call, constants: dict[str, object]) -> int | None:
    candidates = [
        *call.args,
        *[k.value for k in call.keywords if k.arg in ("status_code", "status")],
    ]
    for arg in candidates:
        value = constants.get(arg.id) if isinstance(arg, ast.Name) else getattr(arg, "value", None)
        if isinstance(arg, ast.Attribute) and re.match(r"HTTP_(\d{3})", arg.attr):
            value = int(re.match(r"HTTP_(\d{3})", arg.attr).group(1))
        if isinstance(value, int) and 400 <= value <= 599:
            return value
    return None


def _rules(fn: _Fn, via: str | None, constants: dict[str, object]) -> list[Rule]:
    out: list[Rule] = []

    def walk(stmts: list[ast.stmt], when: str | None) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                test = _render(stmt.test, constants)
                walk(stmt.body, test)
                walk(stmt.orelse, f"not ({test})")
            elif isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
                status = _status(stmt.exc, constants)
                texts = _strings(stmt.exc, constants)
                code = next((t for t in texts if CODE.match(t)), None)
                message = next(
                    (t for t in texts if t != code and t not in ("code", "message")), None
                )
                if status or code:
                    out.append(Rule(status, code, message, when, via, fn.file, stmt.lineno))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # nested definitions are followed by name when they are called
            else:
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    inner = getattr(stmt, attr, None)
                    if isinstance(inner, list) and inner and isinstance(inner[0], ast.AST):
                        walk(
                            [
                                s
                                for h in inner
                                for s in (h.body if isinstance(h, ast.ExceptHandler) else [h])
                            ],
                            when,
                        )

    walk(fn.node.body, None)
    return out


def _model_fields(cls: ast.ClassDef) -> list[str]:
    out = []
    for stmt in cls.body:
        if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
            continue
        name, ann = stmt.target.id, stmt.annotation
        parts: list[str] = []
        if isinstance(ann, ast.Subscript) and getattr(ann.value, "id", None) == "Literal":
            items = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            parts.append("one of " + ", ".join(str(getattr(i, "value", "?")) for i in items))
        else:
            parts.append(ast.unparse(ann))
        if isinstance(stmt.value, ast.Call) and getattr(stmt.value.func, "id", None) == "Field":
            for kw in stmt.value.keywords:
                if kw.arg in CONSTRAINTS:
                    parts.append(f"{kw.arg}={ast.unparse(kw.value)}")
        elif stmt.value is not None:
            parts.append(f"default {ast.unparse(stmt.value)}")
        out.append(f"{name}: {', '.join(parts)}")
    return out


def _requirement(texts: list[str], package: str) -> str | None:
    rx = re.compile(
        rf"^\s*[\"']?{re.escape(package)}(?:\[[^\]]*\])?\s*([<>=!~][^\"'#;\s,]*)?", re.I | re.M
    )
    for text in texts:
        m = rx.search(text)
        if m:
            return m.group(1) or "unpinned"
    return None


def analyse(root: Path) -> Backend:
    root = Path(root).resolve()
    py_files = files(root, (".py",))
    sources: dict[Path, str] = {
        p: p.read_text(encoding="utf-8", errors="replace") for p in py_files
    }
    if not any(re.search(r"^\s*(from fastapi|import fastapi)", s, re.M) for s in sources.values()):
        raise UnsupportedStack(f"no FastAPI application found under {root}")
    problems: list[str] = []
    trees: dict[Path, ast.Module] = {}
    for path, text in sources.items():
        try:
            trees[path] = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            problems.append(f"{path.name}: cannot parse ({exc.msg}, line {exc.lineno})")

    tables: list[sam.Table] = []
    for path, text in sources.items():
        if "__tablename__" in text:
            found, issues = sam.parse(path, text)
            tables += found
            problems += issues
    model_table = {t.model: t.name for t in tables}

    functions: dict[str, list[_Fn]] = {}
    aliases: dict[str, set[str]] = {}
    constants: dict[str, object] = {}
    body_models: dict[str, ast.ClassDef] = {}
    prefixes: dict[str, str] = {}
    for path, tree in trees.items():
        for stmt in tree.body:  # module constants: NAME = literal
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        constants[target.id] = stmt.value.value
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, []).append(_Fn(node, path))
            elif isinstance(node, ast.ClassDef) and any(
                getattr(b, "id", getattr(b, "attr", "")) == "BaseModel" for b in node.bases
            ):
                body_models[node.name] = node
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name = node.targets[0].id
                if _is_dependency(node.value):  # e.g. User = Annotated[str, Depends(current_user)]
                    aliases[name] = _names(node.value)
                if (
                    isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "id", None) == "APIRouter"
                ):
                    for kw in node.value.keywords:
                        if kw.arg == "prefix" and _const_str(kw.value):
                            prefixes[name] = _const_str(kw.value)
            elif (
                isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "include_router"
            ):
                for kw in node.keywords:
                    if kw.arg == "prefix" and node.args and isinstance(node.args[0], ast.Name):
                        extra = _const_str(kw.value) or ""
                        prefixes[node.args[0].id] = extra + prefixes.get(node.args[0].id, "")

    def closure(fn: _Fn) -> list[tuple[str, _Fn]]:
        """Helpers and dependencies a handler reaches by name, the handler first."""
        reached: dict[str, _Fn] = {}
        seen: set[str] = {fn.node.name}
        todo = list(_names(fn.node))
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            if name in functions:
                reached[name] = functions[name][0]
                todo += _names(functions[name][0].node)
            elif name in aliases:
                todo += aliases[name]
        return [(fn.node.name, fn), *reached.items()]

    routes: list[Route] = []
    for fns in functions.values():
        for fn in fns:
            for deco in fn.node.decorator_list:
                if not (
                    isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Attribute)
                    and deco.func.attr in METHODS
                    and deco.args
                    and _const_str(deco.args[0]) is not None
                ):
                    continue
                owner = deco.func.value.id if isinstance(deco.func.value, ast.Name) else ""
                route = Route(
                    method=deco.func.attr.upper(),
                    path=prefixes.get(owner, "") + _const_str(deco.args[0]),
                    handler=fn.node.name,
                    file=fn.file,
                    line=fn.node.lineno,
                    doc=ast.get_docstring(fn.node),
                )
                reached = closure(fn)
                refs: set[str] = set()
                for name, helper in reached:
                    refs |= _names(helper.node)
                    route.rules += _rules(helper, None if helper is fn else name, constants)
                route.tables = sorted({model_table[r] for r in refs if r in model_table})
                path_params = set(re.findall(r"\{(\w+)", route.path))
                for arg in fn.node.args.args + fn.node.args.kwonlyargs:
                    ann = arg.annotation
                    ann_names = _names(ann) if ann is not None else set()
                    if ann_names & set(body_models):
                        for model in ann_names & set(body_models):
                            route.body += _model_fields(body_models[model])
                        continue
                    hidden = {"Depends", "Header", "Cookie", "Request", "Response", "Security"}
                    if any(n in hidden or aliases.get(n, set()) & hidden for n in ann_names):
                        continue
                    if arg.arg in path_params:
                        continue
                    alias = next(
                        (
                            _const_str(kw.value)
                            for n in ast.walk(ann or ast.Module(body=[], type_ignores=[]))
                            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Query"
                            for kw in n.keywords
                            if kw.arg == "alias"
                        ),
                        None,
                    )
                    route.query.append(alias or arg.arg)
                routes.append(route)
    routes.sort(key=lambda r: (str(r.file), r.line))

    texts = [p.read_text(encoding="utf-8", errors="replace") for p in files(root, (".txt", ".toml", ".cfg"))
             if p.name.startswith("requirements") or p.name in ("pyproject.toml", "setup.cfg")]  # fmt: skip
    engine = None
    for text in sources.values():
        m = re.search(rf"[\"']({'|'.join(ENGINES)})(?:\+\w+)?://", text)
        if m:
            engine = "postgres" if m.group(1) == "postgresql" else m.group(1)
            break
    stack = {
        "backend": {
            "framework": "fastapi",
            "version": _requirement(texts, "fastapi") or "not declared",
            "language": "python",
        },
        "orm": {
            "framework": "sqlalchemy",
            "version": _requirement(texts, "sqlalchemy") or "not declared",
        },
        "db": {"engine": engine or "unknown"},
    }
    env = sorted({m.group(1) for text in sources.values() for m in ENV.finditer(text)})
    return Backend(root, stack, routes, tables, env, problems)
