"""What a Python automation sends to and reads from each API route, read statically.

Nothing is executed. The analyser finds HTTP calls with a literal path
(`client.post("/api/login", json={...})`, `httpx.get(f"/api/reviews/{no}")`) and follows each
response through the module: `.json()`, subscripts and `.get("x")`, assignments, `for` loops,
comprehensions, `sorted(..., key=lambda r: r["x"])`, and calls to the module's own functions
(arguments to parameters, `return` back to the caller). The result is field names only:
`token`, `items`, `items[].review_no`. Anything it cannot follow is simply not listed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

METHODS = {"get", "post", "put", "patch", "delete"}
BODY_KWARGS = ("json", "data", "params")
PASS_THROUGH = {"sorted", "list", "reversed", "tuple", "filter", "iter"}
ROUNDS = 4  # passes over the module so returns and parameters reach their users

Key = tuple[str, str]  # (METHOD, /path)


@dataclass(frozen=True)
class Ref:
    """A value that came from a route's response. `path` is None for the response object itself,
    "" for its JSON body, and a dotted field path below that."""

    route: Key
    path: str | None


@dataclass
class Fields:
    sends: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)


def _join(base: str, name: str) -> str:
    return f"{base}.{name}" if base else name


def _path_of(node: ast.expr) -> str | None:
    """A literal route path, with f-string holes as `{name}`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                inner = v.value
                name = inner.id if isinstance(inner, ast.Name) else getattr(inner, "attr", "id")
                parts.append("{" + name + "}")
        text = "".join(parts)
    else:
        return None
    text = text.split("?", 1)[0]
    if not text.startswith("/"):
        m = re.match(r"^\{[^}]*\}(/.*)$", text)  # f"{base}/api/x": drop the base address
        text = m.group(1) if m else ""
    return text if text.startswith("/") else None


def _keys(node: ast.expr | None) -> list[str]:
    if isinstance(node, ast.Dict):
        return [
            k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "dict":
        return [kw.arg for kw in node.keywords if kw.arg]
    return []


class _Module:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.functions = {
            n.name: n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        self.returns: dict[str, Ref] = {}
        self.params: dict[str, dict[str, Ref]] = {}
        self.fields: dict[Key, Fields] = {}

    # -- results --

    def _route(self, key: Key) -> Fields:
        return self.fields.setdefault(key, Fields())

    def _read(self, ref: Ref) -> None:
        if ref.path:
            reads = self._route(ref.route).reads
            if ref.path not in reads:
                reads.append(ref.path)

    # -- expressions --

    def expr(self, node: ast.AST | None, env: dict[str, Ref]) -> Ref | None:  # noqa: C901, PLR0911, PLR0912
        """The response value `node` evaluates to, if any. Visits every sub-expression."""
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Await):
            return self.expr(node.value, env)
        if isinstance(node, ast.Subscript):
            base = self.expr(node.value, env)
            self.expr(node.slice, env)
            if base is None or base.path is None:
                return None
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                ref = Ref(base.route, _join(base.path, key.value))
                self._read(ref)
                return ref
            if isinstance(key, ast.Constant | ast.UnaryOp) or not isinstance(key, ast.Slice):
                return Ref(base.route, base.path + "[]")  # one item of a list
            return base  # a slice is still the list
        if isinstance(node, ast.Lambda):
            return None  # bodies are visited where the lambda's argument is known
        if isinstance(node, ast.Call):
            return self.call(node, env)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.comprehension):
                continue
            self.expr(child, env)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            inner = dict(env)
            for gen in node.generators:
                source = self.expr(gen.iter, inner)
                self.bind(gen.target, self.item(source), inner)
                for cond in gen.ifs:
                    self.expr(cond, inner)
            parts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            for part in parts:
                self.expr(part, inner)
        if isinstance(node, ast.IfExp):
            return self.expr(node.body, env) or self.expr(node.orelse, env)
        if isinstance(node, ast.BoolOp):
            return next((r for r in (self.expr(v, env) for v in node.values) if r), None)
        return None

    def call(self, node: ast.Call, env: dict[str, Ref]) -> Ref | None:  # noqa: C901, PLR0911
        func = node.func
        args = [self.expr(a, env) for a in node.args]
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        for kw in node.keywords:
            if not isinstance(kw.value, ast.Lambda):
                self.expr(kw.value, env)
        if isinstance(func, ast.Attribute):
            owner = self.expr(func.value, env)
            name = func.attr
            if name in METHODS and node.args:
                path = _path_of(node.args[0])
                if path is not None:
                    key = (name.upper(), path)
                    sends = self._route(key).sends
                    for kw in BODY_KWARGS:
                        for k in _keys(kwargs.get(kw)):
                            if k not in sends:
                                sends.append(k)
                    return Ref(key, None)
            if owner is not None:
                if name == "json" and owner.path is None:
                    return Ref(owner.route, "")
                if name == "get" and owner.path is not None and node.args:
                    key = node.args[0]
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        ref = Ref(owner.route, _join(owner.path, key.value))
                        self._read(ref)
                        return ref
                if name in ("copy", "values") and owner.path is not None:
                    return owner if name == "copy" else Ref(owner.route, owner.path + "[]")
            return None
        if isinstance(func, ast.Name):
            if func.id in PASS_THROUGH and args:
                source = args[0]
                key = kwargs.get("key")
                if isinstance(key, ast.Lambda) and source is not None:
                    inner = dict(env)
                    if key.args.args:
                        inner[key.args.args[0].arg] = self.item(source)
                    self.expr(key.body, inner)
                return source
            if func.id == "enumerate" and args:
                return args[0]
            target = self.functions.get(func.id)
            if target is not None:
                names = [a.arg for a in target.args.args]
                bound = self.params.setdefault(func.id, {})
                for name, ref in zip(names, args, strict=False):
                    if ref is not None:
                        bound[name] = ref
                for kw, value in kwargs.items():
                    ref = self.expr(value, env)
                    if kw and ref is not None:
                        bound[kw] = ref
                return self.returns.get(func.id)
        return None

    @staticmethod
    def item(ref: Ref | None) -> Ref | None:
        if ref is None or ref.path is None:
            return None
        return Ref(ref.route, ref.path + "[]")

    def bind(self, target: ast.AST, ref: Ref | None, env: dict[str, Ref]) -> None:
        if isinstance(target, ast.Name):
            if ref is None:
                env.pop(target.id, None)
            else:
                env[target.id] = ref
        elif isinstance(target, ast.Tuple) and ref is not None:  # for i, row in enumerate(x)
            names = [e for e in target.elts if isinstance(e, ast.Name)]
            if names:
                env[names[-1].id] = ref

    # -- statements --

    def body(self, stmts: list[ast.stmt], env: dict[str, Ref], fn: str | None) -> None:  # noqa: C901
        for stmt in stmts:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if isinstance(stmt, ast.Assign):
                ref = self.expr(stmt.value, env)
                for target in stmt.targets:
                    self.bind(target, ref, env)
            elif isinstance(stmt, ast.AnnAssign | ast.AugAssign):
                ref = self.expr(stmt.value, env)
                if isinstance(stmt, ast.AnnAssign):
                    self.bind(stmt.target, ref, env)
            elif isinstance(stmt, ast.For | ast.AsyncFor):
                source = self.expr(stmt.iter, env)
                if (
                    isinstance(stmt.iter, ast.Call)
                    and getattr(stmt.iter.func, "id", "") == "enumerate"
                ):
                    self.bind(stmt.target, self.item(source), env)
                else:
                    self.bind(stmt.target, self.item(source), env)
                self.body(stmt.body, env, fn)
                self.body(stmt.orelse, env, fn)
            elif isinstance(stmt, ast.With | ast.AsyncWith):
                for item in stmt.items:
                    ref = self.expr(item.context_expr, env)
                    if item.optional_vars is not None:
                        self.bind(item.optional_vars, ref, env)
                self.body(stmt.body, env, fn)
            elif isinstance(stmt, ast.If | ast.While):
                self.expr(stmt.test, env)
                self.body(stmt.body, env, fn)
                self.body(stmt.orelse, env, fn)
            elif isinstance(stmt, ast.Try):
                for part in (
                    stmt.body,
                    *[h.body for h in stmt.handlers],
                    stmt.orelse,
                    stmt.finalbody,
                ):
                    self.body(part, env, fn)
            elif isinstance(stmt, ast.Return):
                ref = self.expr(stmt.value, env)
                if fn and ref is not None:
                    self.returns[fn] = ref
            elif isinstance(stmt, ast.Expr):
                self.expr(stmt.value, env)
            else:
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, ast.expr):
                        self.expr(child, env)

    def run(self) -> dict[Key, Fields]:
        for _ in range(ROUNDS):
            self.body(self.tree.body, {}, None)
            for name, fn in self.functions.items():
                self.body(fn.body, dict(self.params.get(name, {})), name)
        return self.fields


def _tidy(paths: list[str]) -> list[str]:
    """Drop a path another one extends (`items` when `items[].review_no` is there)."""
    out = []
    for p in paths:
        if any(o != p and (o.startswith(p + ".") or o.startswith(p + "[]")) for o in paths):
            continue
        out.append(p)
    return sorted(out)


def analyse(files: list[Path]) -> dict[Key, Fields]:
    """Fields per route across the given Python files. Unreadable files are skipped."""
    found: dict[Key, Fields] = {}
    for path in files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for key, f in _Module(tree).run().items():
            have = found.setdefault(key, Fields())
            have.sends += [s for s in f.sends if s not in have.sends]
            have.reads += [r for r in f.reads if r not in have.reads]
    for f in found.values():
        f.sends = sorted(f.sends)
        f.reads = _tidy(f.reads)
    return found


def shape(path: str) -> str:
    """`/api/reviews/{in_ReviewNo}` and `/api/reviews/{no}` compare equal."""
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/") or "/")


# --- SQL and selectors (T-16) --------------------------------------------------------------------

SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|MERGE)\b", re.I)
# Playwright page / locator methods -> the action a screen touch records.
PW_ACTIONS = {
    "click": "click", "dblclick": "click", "check": "click", "uncheck": "click", "tap": "click",
    "fill": "type", "type": "type", "press": "type", "press_sequentially": "type",
    "select_option": "select", "hover": "hover", "wait_for_selector": "wait",
    "text_content": "read", "inner_text": "read", "inner_html": "read", "input_value": "read",
    "get_attribute": "read", "is_visible": "read", "is_enabled": "read", "query_selector": "read",
    "query_selector_all": "read", "locator": "read", "wait_for": "wait",
}  # fmt: skip
BY_CSS = {"CSS_SELECTOR": "{}", "ID": "#{}", "CLASS_NAME": ".{}", "NAME": '[name="{}"]',
          "TAG_NAME": "{}"}  # fmt: skip


@dataclass
class Scan:
    tables: dict[str, dict] = field(default_factory=dict)  # name -> {columns, access}
    selectors: list[dict] = field(default_factory=list)  # {action, selector, where}
    problems: list[str] = field(default_factory=list)


def _text(node: ast.AST) -> str | None:
    """A string constant, or an f-string with its holes as `?` (good enough for SQL text)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "?" for v in node.values)
    return None


def _selector_call(call: ast.Call) -> tuple[str, str] | None:
    """(action, css) for `page.click("#x")`, `page.locator("#x").click()`, Selenium
    `driver.find_element(By.ID, "x")`."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    name = func.attr
    if name in ("find_element", "find_elements") and len(call.args) >= 2:
        by, value = call.args[0], _text(call.args[1])
        kind = by.attr if isinstance(by, ast.Attribute) else None
        if kind in BY_CSS and value:
            return "read", BY_CSS[kind].format(value)
        return None
    if name.startswith("find_element_by_") and call.args and _text(call.args[0]):
        kind = name.removeprefix("find_element_by_").upper()
        kind = "CSS_SELECTOR" if kind == "CSS_SELECTOR" else kind
        if kind in BY_CSS:
            return "read", BY_CSS[kind].format(_text(call.args[0]))
    if name not in PW_ACTIONS:
        return None
    # page.locator("#x").click(): the selector is on the inner call
    inner = func.value
    if (not call.args or _text(call.args[0]) is None) and isinstance(inner, ast.Call):
        found = _selector_call(inner)
        if found and isinstance(inner.func, ast.Attribute) and inner.func.attr == "locator":
            return PW_ACTIONS[name], found[1]
        return None
    if call.args and (sel := _text(call.args[0])):
        if name in ("press", "type") and isinstance(inner, ast.Call):
            return None  # locator(...).press("Enter"): the argument is a key, not a selector
        if sel.startswith(("http://", "https://", "/")):
            return None  # page.goto-like calls never get here, but keep addresses out
        return PW_ACTIONS[name], sel
    return None


def scan(files: list[Path]) -> Scan:
    """Tables named in SQL strings and selectors used with Playwright or Selenium."""
    from labctl.analyse import sql as sqlscan

    out = Scan()
    for path in files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        where = Path(path).name
        for node in ast.walk(tree):
            text = _text(node) if isinstance(node, ast.Constant | ast.JoinedStr) else None
            if text and SQL_START.match(text):
                found, problem = sqlscan.tables(text)
                if problem:
                    out.problems.append(f"{where}: {problem}")
                for t in found:
                    have = out.tables.setdefault(t["table"], {"columns": [], "access": t["access"]})
                    have["columns"] += [c for c in t["columns"] if c not in have["columns"]]
                    if have["access"] != t["access"]:
                        have["access"] = "read_write"
            if isinstance(node, ast.Call) and (hit := _selector_call(node)):
                entry = {"action": hit[0], "selector": hit[1], "where": where}
                if entry not in out.selectors:
                    out.selectors.append(entry)
    # locator("#x") alone and locator("#x").click() both match; keep the stronger action
    best: dict[str, dict] = {}
    for s in out.selectors:
        if s["selector"] not in best or best[s["selector"]]["action"] == "read":
            best[s["selector"]] = s
    out.selectors = list(best.values())
    return out
