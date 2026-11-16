"""React + React Router front ends: screens, the files behind each, the API calls they make, and
the element ids and classes they render. Pattern-based reading of TS/JS/JSX; nothing is run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from labctl.source import UnsupportedStack, files

SUFFIXES = (".tsx", ".ts", ".jsx", ".js")
ROUTE_TAG = re.compile(r"<Route\b")
GENERIC = r"(?:<(?:[^<>]|<[^<>]*>)*>)?"
LITERAL = r"(`[^`]*`|\"[^\"]*\"|'[^']*')"
CALLS = (
    # api("GET", "/x") / request<T>('POST', `/x/${id}`)
    re.compile(rf"\b[\w.$]+\s*{GENERIC}\s*\(\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']\s*,\s*{LITERAL}"),
    # axios.get("/x") / http.post<T>(`/x`)
    re.compile(rf"\b(?:axios|http|client|api|apiClient|instance)\.(get|post|put|patch|delete)\s*{GENERIC}\s*\(\s*{LITERAL}", re.I),
)  # fmt: skip
FETCH = re.compile(rf"\bfetch\s*\(\s*{LITERAL}")
IMPORT = re.compile(
    r"""\bimport\s+(?:([\w$]+)\s*,?\s*)?(?:\{([^}]*)\}\s*)?(?:from\s+)?["'](\.[^"']+)["']"""
)
ID_ATTR = re.compile(r"""\bid=(?:"([^"]+)"|'([^']+)'|\{\s*["'`]([^"'`$]+)["'`]\s*\})""")
CLASS_ATTR = re.compile(
    r"""\bclass(?:Name)?=(?:"([^"]*)"|'([^']*)'|\{\s*`([^`]*)`\s*\}|\{\s*["']([^"']*)["']\s*\})"""
)
ENV = re.compile(r"(?:process\.env|import\.meta\.env)\.(\w+)")
UI_LIBRARIES = {
    "@mui/material": "mui", "@material-ui/core": "material-ui", "antd": "antd",
    "@chakra-ui/react": "chakra", "react-bootstrap": "react-bootstrap", "bootstrap": "bootstrap",
    "primereact": "primereact", "@fluentui/react": "fluentui", "@blueprintjs/core": "blueprint",
}  # fmt: skip


@dataclass
class Call:
    method: str
    path: str
    file: Path
    line: int


@dataclass
class Screen:
    path: str
    component: str
    file: Path
    router_file: Path
    line: int
    files: list[Path] = field(default_factory=list)


@dataclass
class Frontend:
    root: Path
    stack: dict
    screens: list[Screen]
    calls: dict[Path, list[Call]]
    env: list[str]
    problems: list[str]

    def calls_of(self, screen: Screen) -> list[Call]:
        return [c for f in screen.files for c in self.calls.get(f, [])]


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _path_of(literal: str) -> str | None:
    """`/api/cases/${caseNo}?x=1` -> `/api/cases/{caseNo}`. None if it is not a relative address."""
    body = literal[1:-1]
    body = re.sub(r"^\$\{[^}]*\}", "", body)  # a base-URL variable in front
    body = re.sub(
        r"\$\{\s*([^}]*?)\s*\}", lambda m: "{" + re.sub(r"\W+", "_", m.group(1)) + "}", body
    )
    body = re.split(r"[?#{]\s*$|\?|#", body, maxsplit=1)[0] if "?" in body or "#" in body else body
    if re.match(r"^https?://", body):
        body = "/" + body.split("/", 3)[3] if body.count("/") >= 3 else "/"
    return body if body.startswith("/") else None


def _resolve(base: Path, spec: str, root: Path) -> Path | None:
    target = (base.parent / spec).resolve()
    candidates = [target, *[target.with_name(target.name + s) for s in SUFFIXES]]
    candidates += [target / f"index{s}" for s in SUFFIXES]
    for cand in candidates:
        if cand.is_file() and root.resolve() in cand.parents and cand.suffix in SUFFIXES:
            return cand
    return None


def _imports(path: Path, text: str, root: Path) -> dict[str, Path]:
    """Local name -> file for relative imports (default and named)."""
    out: dict[str, Path] = {}
    for m in IMPORT.finditer(text):
        target = _resolve(path, m.group(3), root)
        if target is None:
            continue
        if m.group(1):
            out[m.group(1)] = target
        for name in (m.group(2) or "").split(","):
            name = name.strip().split(" as ")[-1].strip()
            if name:
                out[name] = target
    return out


def ids_and_classes(texts: list[str]) -> tuple[set[str], set[str]]:
    ids, classes = set(), set()
    for text in texts:
        for m in ID_ATTR.finditer(text):
            ids.add(next(g for g in m.groups() if g))
        for m in CLASS_ATTR.finditer(text):
            value = next((g for g in m.groups() if g is not None), "")
            value = re.sub(r"\$\{[^}]*\}", " ", value)
            classes |= {c for c in value.split() if c and not c.endswith("-")}
    return ids, classes


def _package(root: Path) -> tuple[Path, dict]:
    for pkg in sorted(root.rglob("package.json"), key=lambda p: len(p.parts)):
        if "node_modules" in pkg.parts:
            continue
        data = json.loads(pkg.read_text(encoding="utf-8"))
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "react" in deps:
            return pkg.parent, deps
    raise UnsupportedStack(f"no React package.json found under {root}")


def analyse(root: Path) -> Frontend:
    app_root, deps = _package(Path(root).resolve())
    if not any(k in deps for k in ("react-router-dom", "react-router")):
        raise UnsupportedStack("React app without React Router; screens cannot be read from routes")
    src = [
        p.resolve()
        for p in files(app_root, SUFFIXES)
        if not p.name.endswith((".config.ts", ".config.js"))
    ]
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in src}
    problems: list[str] = []

    calls: dict[Path, list[Call]] = {}
    for path, text in texts.items():
        found = []
        for rx in CALLS:
            for m in rx.finditer(text):
                address = _path_of(m.group(2))
                if address:
                    found.append(Call(m.group(1).upper(), address, path, _line(text, m.start())))
        for m in FETCH.finditer(text):
            address = _path_of(m.group(1))
            if address:
                method = re.search(r"method\s*:\s*[\"'](\w+)[\"']", text[m.end() : m.end() + 300])
                found.append(
                    Call(
                        (method.group(1) if method else "GET").upper(),
                        address,
                        path,
                        _line(text, m.start()),
                    )
                )
        if found:
            calls[path] = sorted(found, key=lambda c: c.line)

    screens: list[Screen] = []
    for path, text in texts.items():
        starts = [m.start() for m in ROUTE_TAG.finditer(text)]
        if not starts:
            continue
        imports = _imports(path, text, app_root)
        for i, start in enumerate(starts):
            chunk = text[start : starts[i + 1] if i + 1 < len(starts) else start + 600]
            route_path = re.search(r"""\bpath=["']([^"']+)["']""", chunk)
            comp = re.search(r"\belement=\{\s*<\s*([\w.]+)|\bcomponent=\{\s*([\w.]+)\s*\}", chunk)
            if not route_path or not comp or route_path.group(1) == "*":
                continue
            name = comp.group(1) or comp.group(2)
            if name in ("Navigate", "Redirect"):
                continue
            rpath = route_path.group(1)
            if not rpath.startswith("/"):
                problems.append(
                    f"{path.name}: route '{rpath}' is relative; its parent prefix was not added"
                )
                rpath = "/" + rpath
            if name in imports:
                comp_file = imports[name]
            elif re.search(rf"\b(?:function|const|class)\s+{re.escape(name)}\b", text):
                comp_file = path
            else:
                problems.append(f"{path.name}: component {name} for '{rpath}' could not be found")
                continue
            screens.append(Screen(rpath, name, comp_file, path, _line(text, start)))

    for screen in screens:  # local files each screen's component pulls in
        todo, seen = [screen.file], []
        while todo:
            current = todo.pop()
            if current in seen or current not in texts:
                continue
            seen.append(current)
            todo += [
                p for p in _imports(current, texts[current], app_root).values() if p not in seen
            ]
        screen.files = seen

    ui = next((label for pkg, label in UI_LIBRARIES.items() if pkg in deps), None)
    stack = {
        "frontend": {"framework": "react", "version": deps["react"], "ui_library": ui},
        "pinned": {
            name: deps[name]
            for name in ("react", "react-dom", "react-router-dom", "react-router", "typescript", "vite",
                         "react-scripts", "webpack", *UI_LIBRARIES)
            if name in deps
        },
    }  # fmt: skip
    env = sorted(
        {
            m.group(1)
            for p in files(app_root, (*SUFFIXES,))
            for m in ENV.finditer(p.read_text(encoding="utf-8", errors="replace"))
        }
    )
    return Frontend(app_root, stack, screens, calls, env, problems)
