"""Static analyser for UiPath projects: which screens, selectors, routes and tables a bot touches.

Parses `project.json` and the XAML workflows without executing anything. Anything it cannot
interpret becomes a gap; nothing is silently skipped.

Scope note: this covers the classic activity set (OpenBrowser, TypeInto, Click, GetText, ...) and
was developed against hand-authored XAML that follows the Studio layout, starting from
ApplicationTwinStudio's importer. Modern-design activities (NApplicationCard, NTypeInto, ...) are
reported as gaps until a real Studio export is available to code against.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from labctl.analyse import sql as sqlscan
from labctl.analyse import vb
from labctl.analyse.vb import Part
from labctl.models import Gap

VERSION = "uipath-analyser@0.1.0"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 20
X_NS = "http://schemas.microsoft.com/winfx/2006/xaml"

# Element use: activity -> (action, attribute naming the variable or value it carries)
UI = {
    "TypeInto": ("type", "Text"),
    "TypeSecureText": ("type", "SecureText"),
    "SetText": ("type", "Text"),
    "SendHotkey": ("type", None),
    "Click": ("click", None),
    "DoubleClick": ("click", None),
    "Check": ("click", None),
    "Hover": ("hover", None),
    "GetText": ("read", "Value"),
    "GetFullText": ("read", "Text"),
    "GetValue": ("read", "Value"),
    "GetAttribute": ("read", "Result"),
    "SelectItem": ("select", "Item"),
    "SelectMultipleItems": ("select", None),
    "UiElementExists": ("wait", "Exists"),
    "ElementExists": ("wait", "Exists"),
    "WaitUiElementAppear": ("wait", None),
    "WaitUiElementVanish": ("wait", None),
    "FindElement": ("wait", None),
}
# Activities that carry no touch of their own. Their children are still walked.
NEUTRAL = {
    "Sequence", "Flowchart", "StateMachine", "If", "Switch", "While", "DoWhile", "ForEach",
    "ParallelForEach", "Parallel", "Pick", "PickBranch", "TryCatch", "RetryScope", "Assign",
    "MultipleAssign", "LogMessage", "WriteLine", "Comment", "Delay", "Throw", "Rethrow",
    "TerminateWorkflow", "DeserializeJson", "DeserializeJsonArray", "CloseTab", "CloseApplication",
    "BrowserScope", "DatabaseConnect", "DatabaseDisconnect", "InvokeWorkflowFile", "ForEachRow",
    "AddToCollection", "BuildDataTable", "ShouldStop", "Break", "Continue", "Catch",
}  # fmt: skip
# Not activities: structure, arguments, metadata.
STRUCTURAL = {
    "Activity", "ActivityAction", "DelegateInArgument", "DelegateOutArgument", "Variable",
    "InArgument", "OutArgument", "InOutArgument", "Target", "TextExpression", "AssemblyReference",
    "VisualBasicValue", "VisualBasicReference", "VisualBasicSettings", "Literal", "FlowStep",
    "FlowDecision", "FlowSwitch", "State", "Transition", "AssignOperation", "CSharpValue",
    "CSharpReference", "ActivityFunc",
}  # fmt: skip
DISABLED = {"CommentOut"}
NON_ACTIVITY_NAMESPACES = (
    X_NS,
    "http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation",
    "http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation",
    "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "clr-namespace:System",
    "clr-namespace:Microsoft.VisualBasic",
)
BINDING = re.compile(r"^\{\{\s*([a-z0-9][a-z0-9-]*)\.(web|api|db)\s*\}\}$")
TEXT_ONLY_ATTRS = ("aaname", "innertext", "idx", "tableRow", "tableCol", "title", "text")


class AnalysisError(Exception):
    """The project cannot be analysed safely or at all."""


@dataclass
class Result:
    touches: list[dict] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    notes: list[Gap] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    unbound: dict[str, str] = field(
        default_factory=dict
    )  # argument -> web | api | db, used as an address


# --- reading XAML safely -----------------------------------------------------------------------


def _decode(raw: bytes) -> str:
    for bom, encoding in (
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
    ):
        if raw.startswith(bom):
            return raw.decode(encoding, "ignore")
    return raw.decode("utf-8", "ignore")


def safe_parse(path: Path) -> ET.Element:
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise AnalysisError(f"{path.name}: larger than {MAX_FILE_BYTES} bytes")
    text = _decode(raw)
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise AnalysisError(f"{path.name}: DTD and entity declarations are rejected")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AnalysisError(f"{path.name}: not well-formed XML ({exc})") from exc


def resolve_inside(base: Path, name: str) -> Path:
    target = (base / name.replace("\\", "/")).resolve()
    if base.resolve() not in target.parents:
        raise AnalysisError(f"workflow reference escapes the project folder: {name}")
    return target


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def argument(el: ET.Element, name: str) -> str | None:
    """An activity argument, written as an attribute or as `<X.Name><InArgument>…`."""
    if el.get(name) is not None:
        return el.get(name)
    for child in el:
        if local(child.tag) == f"{local(el.tag)}.{name}":
            for d in child.iter():
                if d is not child and (d.text or "").strip():
                    return d.text.strip()
    return None


# --- selectors ---------------------------------------------------------------------------------


def selector_of(el: ET.Element) -> str | None:
    if el.get("Selector"):
        return el.get("Selector")
    for child in el:
        if local(child.tag) == f"{local(el.tag)}.Target":
            for d in child.iter():
                if local(d.tag) == "Target" and d.get("Selector"):
                    return d.get("Selector")
    return None


def _wild(attr: str, value: str) -> str | None:
    """A UiPath wildcard value as a CSS attribute test. None if it cannot be expressed."""
    if "?" in value:
        return None
    core = value.strip("*")
    if "*" in core or not core:
        return None
    op = {(True, True): "*=", (False, True): "^=", (True, False): "$="}[
        (value.startswith("*"), value.endswith("*"))
    ]
    return f"[{attr}{op}'{core}']"


def to_css(selector: str) -> str | None:
    """UiPath selector string -> CSS selector, or None when it has nothing CSS can test."""
    steps = []
    for tag, body in re.findall(r"<(\w+)((?:\s+[\w-]+\s*=\s*'[^']*')*)\s*/?>", selector):
        if tag.lower() != "webctrl":
            continue  # <html>, <wnd>: the window, not an element in the page
        attrs = dict(re.findall(r"([\w-]+)\s*=\s*'([^']*)'", body))
        if attrs.get("css-selector"):
            steps.append(attrs["css-selector"])
            continue
        css = attrs.get("tag", "").lower()
        for key, prefix in (("id", "#"), ("class", ".")):
            for value in attrs.get(key, "").split() if key == "class" else [attrs.get(key, "")]:
                if not value:
                    continue
                if "*" in value or "?" in value:
                    test = _wild(key, value)
                    if test is None:
                        return None
                    css += test
                else:
                    css += prefix + value
        for key in ("name", "type", "href"):
            if key in attrs:
                test = _wild(key, attrs[key]) if "*" in attrs[key] else f"[{key}='{attrs[key]}']"
                if test is None:
                    return None
                css += test
        if "parentid" in attrs:
            css = f"#{attrs['parentid']} {css or '*'}"
        if (
            not css
            or css == attrs.get("tag", "").lower()
            and any(a in attrs for a in TEXT_ONLY_ATTRS)
        ):
            return None
        steps.append(css)
    return " ".join(steps) or None


# --- addresses ---------------------------------------------------------------------------------


def split_address(parts: list[Part]) -> tuple[tuple[str, str] | None, str, str | None]:
    """Resolved expression parts -> ((app, interface) or None, path template, problem or None)."""
    if not parts:
        return None, "", "address is empty"
    head, rest = parts[0], parts[1:]
    target = None
    problem = None
    if head.kind == "bind":
        app, iface = head.value.split(".")
        target = (app, iface)
    elif head.kind == "lit" and re.match(r"^https?://", head.value):
        rest = [Part("lit", urlsplit(head.value).path), *rest]
        problem = "address is hard-coded, so the application it belongs to cannot be told"
    else:
        problem = f"address starts with '{head.value}', which is not bound to an application"
    path = ""
    for part in rest:
        if part.kind == "lit":
            path += part.value
        elif part.kind == "var":
            path += "{" + part.value + "}"
        else:
            path += "{?}"
            problem = problem or "part of the address is computed and could not be read"
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return target, path, problem


def screen_name(path: str) -> str:
    fixed = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    name = "-".join(fixed) or "home"
    return name + "-item" if path.rstrip("/").endswith("}") else name


# --- the walk ----------------------------------------------------------------------------------


@dataclass
class Browser:
    app: str | None = None
    path: str | None = None
    clicked: str | None = None  # where a click happened since the last navigation
    warned: bool = False


class _Walker:
    def __init__(self, project_dir: Path, bindings: dict[str, str]):
        self.dir = project_dir
        self.result = Result()
        self.screens: dict[tuple[str, str], dict] = {}
        self.routes: dict[str, list[dict]] = {}
        self.tables: dict[str, dict[str, dict]] = {}
        self.order: list[tuple[str, str]] = []
        self.top_env: dict[str, list[Part]] = {}
        for name, value in bindings.items():
            m = BINDING.match(value)
            if m:
                self.top_env[name] = [Part("bind", f"{m.group(1)}.{m.group(2)}")]

    def gap(self, where: str, what: str) -> None:
        gap = Gap(where=where, what=what)
        if gap not in self.result.gaps:
            self.result.gaps.append(gap)

    def note(self, where: str, what: str) -> None:
        note = Gap(where=where, what=what)
        if note not in self.result.notes:
            self.result.notes.append(note)

    def file(
        self, rel: str, env: dict[str, list[Part]], browser: Browser | None, depth: int, where: str
    ) -> None:
        if depth > MAX_DEPTH:
            self.gap(where, "workflows invoke each other too deeply to follow")
            return
        path = resolve_inside(self.dir, rel)
        if not path.exists():
            self.gap(where, f"invoked workflow {rel} is not in the project folder")
            return
        if rel not in self.result.files:
            self.result.files.append(rel)
        self.children(safe_parse(path), rel, dict(env), browser, depth)

    def children(
        self, el: ET.Element, rel: str, env: dict, browser: Browser | None, depth: int
    ) -> None:
        for child in el:
            self.visit(child, rel, env, browser, depth)

    def visit(
        self, el: ET.Element, rel: str, env: dict, browser: Browser | None, depth: int
    ) -> None:
        name = local(el.tag)
        where = f"{rel}: {name} '{el.get('DisplayName') or name}'"
        if name in DISABLED:
            return
        transparent = (
            "." in name
            or name in STRUCTURAL
            or namespace(el.tag).startswith(NON_ACTIVITY_NAMESPACES)
        )
        if transparent:
            if name.endswith(".Target") or name.endswith(".Arguments"):
                return
            self.children(el, rel, env, browser, depth)
            return

        if name == "Assign":
            target = vb.variable(argument(el, "To"))
            if target:
                env[target] = vb.resolve(vb.parse(argument(el, "Value")), env)
        elif name == "OpenBrowser":
            browser = self.open_browser(el, env, where)
        elif name == "NavigateTo":
            self.navigate(el, env, browser, where)
        elif name in UI:
            self.element(el, name, browser, where)
        elif name == "HttpClient":
            self.http(el, env, where)
        elif name in ("ExecuteQuery", "ExecuteNonQuery"):
            self.query(el, env, where)
        elif name == "InvokeWorkflowFile":
            self.invoke(el, env, browser, depth, where)
        elif name not in NEUTRAL:
            kind = "modern-design activity" if re.match(r"^N[A-Z]", name) else "activity"
            self.gap(where, f"{kind} {name} is not interpreted; what it touches is unknown")
        self.children(el, rel, env, browser, depth)

    # -- web --

    def open_browser(self, el: ET.Element, env: dict, where: str) -> Browser:
        browser = Browser()
        if (el.get("BrowserType") or "").upper() == "IE":
            self.gap(
                where, "Internet Explorer automation; the demo app is checked in a modern browser"
            )
        self.goto(browser, argument(el, "Url"), env, where)
        return browser

    def navigate(self, el: ET.Element, env: dict, browser: Browser | None, where: str) -> None:
        if browser is None:
            self.gap(where, "navigation outside an OpenBrowser scope; the application is unknown")
            return
        self.goto(browser, argument(el, "Url"), env, where)

    def head(self, parts: list[Part], kind: str) -> None:
        """Remember an argument used as the start of an address, so bindings can be proposed."""
        if parts and parts[0].kind == "var":
            self.result.unbound.setdefault(parts[0].value, kind)

    def goto(self, browser: Browser, raw: str | None, env: dict, where: str) -> None:
        parts = vb.resolve(vb.parse(raw), env)
        self.head(parts, "web")
        target, path, problem = split_address(parts)
        if problem:
            self.gap(where, problem)
        if target and target[1] != "web":
            self.gap(where, f"browser opens an address bound as '{target[1]}', expected 'web'")
        if target:
            browser.app = target[0]
        browser.path = path if target or browser.app else None
        browser.clicked = None
        browser.warned = False

    def element(self, el: ET.Element, name: str, browser: Browser | None, where: str) -> None:
        action, carrier = UI[name]
        raw = selector_of(el)
        if raw is None:
            self.gap(where, "no selector found on this activity")
            return
        if browser is None or browser.app is None or browser.path is None:
            self.gap(where, "element used outside a browser opened on a known application")
            return
        if browser.clicked and not browser.warned:
            browser.warned = True
            self.note(
                browser.clicked,
                f"elements used after this click are listed under screen '{browser.path}'; "
                "if the click opens another screen, move them there",
            )
        css = to_css(raw)
        if css is None:
            self.gap(where, "selector relies on text or position only and cannot be checked as CSS")
        key = (browser.app, browser.path)
        if key not in self.screens:
            self.screens[key] = {
                "name": screen_name(browser.path),
                "path": browser.path,
                "uses": [],
            }
        use = {"action": action, "selector": css or f"uipath:{raw}"}
        variable = vb.variable(argument(el, carrier)) if carrier else None
        if variable:
            use["field"] = variable
        if css:
            use["raw"] = raw
        if use not in self.screens[key]["uses"]:
            self.screens[key]["uses"].append(use)
        if action == "click":
            browser.clicked = where

    # -- api --

    def http(self, el: ET.Element, env: dict, where: str) -> None:
        parts = vb.resolve(vb.parse(argument(el, "EndPoint")), env)
        self.head(parts, "api")
        target, path, problem = split_address(parts)
        if problem:
            self.gap(where, problem)
        if not target:
            return
        method = (el.get("Method") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            self.gap(where, f"HTTP method {method} is not one the lab records")
            return
        route = {"method": method, "path": path}
        if route not in self.routes.setdefault(target[0], []):
            self.routes[target[0]].append(route)

    # -- db --

    def query(self, el: ET.Element, env: dict, where: str) -> None:
        conn = vb.resolve(vb.parse(argument(el, "ConnectionString")), env)
        conn = conn or vb.resolve(vb.parse(argument(el, "ExistingDbConnection")), env)
        self.head(conn, "db")
        if len(conn) != 1 or conn[0].kind != "bind":
            self.gap(where, "database connection is not bound to an application")
            return
        app = conn[0].value.split(".")[0]
        statement = vb.parse(argument(el, "Sql"))
        if len(statement) != 1 or statement[0].kind != "lit":
            self.gap(where, "SQL is built at run time and could not be read")
            return
        found, problem = sqlscan.tables(statement[0].value)
        if problem:
            self.gap(where, problem)
        for item in found:
            have = self.tables.setdefault(app, {}).setdefault(
                item["table"], {"table": item["table"], "columns": [], "access": item["access"]}
            )
            have["columns"] += [c for c in item["columns"] if c not in have["columns"]]
            if have["access"] != item["access"]:
                have["access"] = "read_write"

    # -- invoked workflows --

    def invoke(
        self, el: ET.Element, env: dict, browser: Browser | None, depth: int, where: str
    ) -> None:
        parts = vb.parse(el.get("WorkflowFileName"))
        if len(parts) != 1 or parts[0].kind != "lit":
            self.gap(where, "workflow file name is computed and cannot be followed")
            return
        child_env: dict[str, list[Part]] = {}
        for child in el:
            if local(child.tag) == "InvokeWorkflowFile.Arguments":
                for arg in child.iter():
                    key = arg.get(f"{{{X_NS}}}Key")
                    if key and (arg.text or "").strip():
                        child_env[key] = vb.resolve(vb.parse(arg.text), env)
        self.file(parts[0].value.replace("\\", "/"), child_env, browser, depth + 1, where)

    def touches(self) -> list[dict]:
        out: list[dict] = []
        apps = dict.fromkeys([a for a, _ in self.screens] + list(self.routes) + list(self.tables))
        for app in apps:
            screens = [s for (a, _), s in self.screens.items() if a == app]
            if screens:
                out.append({"app": app, "via": "web", "screens": screens})
            if self.routes.get(app):
                out.append({"app": app, "via": "api", "routes": self.routes[app]})
            if self.tables.get(app):
                out.append({"app": app, "via": "db", "tables": list(self.tables[app].values())})
        return out


def analyse(
    project_dir: Path, entry: str | None = None, bindings: dict[str, str] | None = None
) -> Result:
    """Analyse a UiPath project folder. `bindings` says which argument is which app's address."""
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise AnalysisError(f"project folder not found: {project_dir}")
    meta: dict = {}
    manifest = project_dir / "project.json"
    if manifest.exists():
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise AnalysisError(f"project.json is not valid JSON ({exc})") from exc
    entry = entry or meta.get("main") or "Main.xaml"
    walker = _Walker(project_dir, bindings or {})
    if not resolve_inside(project_dir, entry).exists():
        raise AnalysisError(f"entry workflow {entry} not found in {project_dir}")
    walker.file(entry, walker.top_env, None, 0, entry)
    if (meta.get("expressionLanguage") or "VisualBasic") != "VisualBasic":
        walker.gap(
            "project.json", "expression language is not Visual Basic; addresses may be misread"
        )
    walker.result.touches = walker.touches()
    return walker.result


CREDENTIAL_HINTS = (("password", "password"), ("passwd", "password"), ("pwd", "password"),
                    ("user", "username"), ("login", "username"))  # fmt: skip


def suggest_bindings(
    project_dir: Path, app: str, entry: str | None = None, known: dict[str, str] | None = None
) -> dict[str, str]:
    """Bindings proposed for a project that runs against one app: every argument used as the start
    of an address, and user and password arguments by name. A person reviews them."""
    known = dict(known or {})
    first = analyse(project_dir, entry, known)
    proposed = {
        arg: "{{" + f"{app}.{kind}" + "}}"
        for arg, kind in first.unbound.items()
        if arg not in known
    }
    main = Path(project_dir) / (entry or "Main.xaml")
    if main.exists():
        for prop in safe_parse(main).iter(f"{{{X_NS}}}Property"):
            name = prop.get("Name") or ""
            if (
                name in known
                or name in proposed
                or not (prop.get("Type") or "").startswith("InArgument")
            ):
                continue
            for hint, field_name in CREDENTIAL_HINTS:
                if hint in name.lower():
                    proposed[name] = "{{" + f"{app}.credentials.{field_name}" + "}}"
                    break
    return {**known, **proposed}
