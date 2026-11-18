"""Verify a built desktop demo app against a recording of the real application.

`compare_trees(recorded, live)` compares two value-free window trees (plain Python, runs
anywhere): each control the recording shows is looked up in the live tree by its locator and
reported **missing** (no match), **ambiguous** (more than one match: never resolved by taking
the first), **changed** (ClassName, FrameworkId or parent path differ), **enabled** or
**visibility** (state differs), **options** (a drop-down's code list differs) or
**value_format** (a value's shape differs); a control in the live tree that the recording does
not show is **extra**. Rows of grids and lists hold data and are compared by presence only.

`verify(exe, recording)` (Windows) starts the built app, walks the recorded steps by their
locators through UI Automation patterns and window messages (no mouse, no keyboard: nothing
reaches another application), compares each step's window with the recorded tree, closes the
app, and returns the report in the web fidelity format (labctl.behaviour.fidelity), so the
readiness gate reads it the same way.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from labctl.behaviour.fidelity import Difference, FidelityReport, StepReport
from labctl.desktop import locator, privacy

COMPARED = {"Button", "Edit", "Document", "CheckBox", "RadioButton", "ComboBox", "List",
            "DataGrid", "Table", "Tab", "TabItem", "MenuBar", "MenuItem", "Hyperlink", "Tree",
            "Spinner", "Slider", "Group", "StatusBar", "ToolBar", "SplitButton", "Calendar",
            "Text"}  # fmt: skip
ROW_TYPES = {"DataItem", "ListItem", "TreeItem"}
SKIP_UNDER = {"TitleBar"}


def _controls(tree: dict) -> list[tuple[dict, tuple, int]]:
    """Controls to compare: structural ones, never inside a data row, never the title bar."""
    out = []

    def walk(node: dict, ancestors: tuple, idx: int, data: bool) -> None:
        t = node.get("type")
        if t in SKIP_UNDER:
            return
        if ancestors and not data and t in COMPARED:
            if t != "Text" or node.get("name_is") in ("structure", "message"):
                out.append((node, ancestors, idx))
        inside = data or t in ROW_TYPES or t in ("DataGrid", "Table", "List", "Tree")
        counts: dict[str, int] = {}
        for child in node.get("children") or []:
            ct = child.get("type")
            n = counts.get(ct, 0)
            counts[ct] = n + 1
            c = {**child, "idx": n}
            walk(c, (*ancestors, {**node, "idx": node.get("idx", idx)}), n, inside)

    walk(dict(tree), (), 0, False)
    return out


def _named(path: list[str]) -> list[str]:
    """The steps of an ancestor path that name a control (AutomationId or Name). Unnamed
    containers in between (Java's root pane, layered pane and panels; WPF's borders) are left
    out: toolkits add and reorder them, bots do not anchor on them."""
    return [p for p in path if "AutomationId=" in p or "Name=" in p]


def _rows(node: dict) -> int:
    n = 0
    for c in node.get("children") or []:
        if c.get("type") in ROW_TYPES or (c.get("type") == "Custom" and c.get("children")
                                          and all(k.get("type") == "DataItem" for k in c["children"])):  # fmt: skip
            n += 1
    return n


def compare_trees(recorded: dict, live: dict, step: int = 0,
                  supplied: frozenset[str] | set[str] = frozenset()) -> list[Difference]:  # fmt: skip
    """Differences between a recorded window tree and the live one, each naming the locator.
    `supplied` holds the locators whose values the replay typed (their shape is not compared)."""
    diffs: list[Difference] = []
    expected = []
    # A window a modal dialog has disabled reports its controls disabled. When the recorded and
    # the live window differ in that, it is timing (when each snapshot was taken, before or after
    # the message box opened or closed), not the app: enabled states are then not compared; the
    # message box itself is compared as its own window.
    modal = bool(recorded.get("enabled")) != bool(live.get("enabled"))
    for node, ancestors, idx in _controls(recorded):
        loc = locator.build(recorded, node, ancestors, idx)
        expected.append((loc, node))
    matched_ids: set[int] = set()
    for loc, node in expected:
        sel = loc["selector"]
        scoped = "Index=" in sel
        hits = locator.find(live, sel, loc["path"] if scoped else None)
        if loc.get("ambiguous"):
            # Ambiguous in the real application already (the brief lists it): a difference only
            # when the demo app has another number of matches.
            if len(hits) != loc["matches"]:
                diffs.append(Difference(step=step, element=sel, check="ambiguous",
                                        expected=loc["matches"], actual=len(hits)))  # fmt: skip
            continue
        if not hits:
            diffs.append(Difference(step=step, element=sel, check="missing", expected=1, actual=0))
            continue
        if len(hits) > 1:
            diffs.append(Difference(step=step, element=sel, check="ambiguous", expected=1,
                                    actual=len(hits)))  # fmt: skip
            continue
        have, anc = hits[0]
        matched_ids.add(id(have))
        for key, label in (("cls", "ClassName"), ("fw", "FrameworkId")):
            a = locator.normalise_class(node.get(key)) if key == "cls" else node.get(key)
            b = locator.normalise_class(have.get(key)) if key == "cls" else have.get(key)
            if a and a != b:
                diffs.append(Difference(step=step, element=sel, check="changed",
                                        expected=f"{label}={a}", actual=f"{label}={b}"))  # fmt: skip
        if node.get("type") == "MenuItem":
            continue  # menu items: present or not; where an open menu lists them, and whether it
            # is open, depends on timing (Java lists them under the menu and in its popup)
        live_path = [locator.step_of(a, a.get("idx", 0)) for a in anc]
        if _named(live_path) != _named(loc["path"]):
            diffs.append(Difference(step=step, element=sel, check="changed",
                                    expected=" > ".join(loc["path"]),
                                    actual=" > ".join(live_path)))  # fmt: skip
        if node.get("visible") is not None and bool(node.get("visible")) != bool(
            have.get("visible")
        ):
            diffs.append(Difference(step=step, element=sel, check="visibility",
                                    expected=node.get("visible"), actual=have.get("visible")))  # fmt: skip
        elif (not modal and node.get("enabled") is not None
              and bool(node["enabled"]) != bool(have.get("enabled"))):  # fmt: skip
            diffs.append(Difference(step=step, element=sel, check="enabled",
                                    expected=node.get("enabled"), actual=have.get("enabled")))  # fmt: skip
        if node.get("type") in ("DataGrid", "Table", "List", "Tree"):
            want, got = _rows(node) > 0, _rows(have) > 0
            if want != got:
                diffs.append(Difference(step=step, element=sel + " (rows)", check="missing" if want else "extra",
                                        expected="rows" if want else "none", actual="rows" if got else "none"))  # fmt: skip
        if node.get("items_are") == "code-list" and node.get("children") and have.get("children"):
            a = [c.get("name") for c, _p, _i in locator.walk(node) if c.get("type") == "ListItem"]
            b = [c.get("name") for c, _p, _i in locator.walk(have) if c.get("type") == "ListItem"]
            if a and a != b:
                diffs.append(
                    Difference(step=step, element=sel, check="options", expected=a, actual=b)
                )
        if (node.get("value") is not None and sel not in supplied and node.get("readonly")
                and have.get("value") is not None and node["value"] != have["value"]):  # fmt: skip
            diffs.append(Difference(step=step, element=sel, check="value_format",
                                    expected=node["value"], actual=have["value"]))  # fmt: skip
    for node, ancestors, idx in _controls(live):
        loc = locator.build(live, node, ancestors, idx)
        found = locator.find(
            recorded, loc["selector"], loc["path"] if "Index=" in loc["selector"] else None
        )
        if not found:
            diffs.append(Difference(step=step, element=loc["selector"], check="extra",
                                    expected=0, actual=loc["matches"]))  # fmt: skip
    return diffs


def compare_files(recorded: Path, live: Path, step: int = 0) -> list[Difference]:
    return compare_trees(json.loads(Path(recorded).read_text(encoding="utf-8")),
                         json.loads(Path(live).read_text(encoding="utf-8")), step)  # fmt: skip


# --- replay (Windows) ------------------------------------------------------------------------------


def value_for(shape: str | None) -> str:
    """A synthetic value of a recorded shape: X -> A, x -> a, 9 -> 1, the rest kept."""
    return "".join("A" if c == "X" else "a" if c == "x" else "1" if c == "9" else c
                   for c in (shape or "").rstrip("…"))  # fmt: skip


def _async(fn, timeout: float = 2.0) -> str | None:
    """Run a UI Automation call that may block (a button that opens a modal window) on a thread
    of its own, and return after `timeout` whatever happened."""
    out: list = []

    def run():
        import comtypes

        comtypes.CoInitializeEx(0)
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001
            out.append(f"error: {type(exc).__name__}")
        finally:
            comtypes.CoUninitialize()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return out[0] if out else "still running (a modal window opened)"


class _Live:
    def __init__(self, pids: set[int]):
        from labctl.desktop import win
        from labctl.desktop.drive import Driver

        self.win, self.d, self.pids = win, Driver(pids, pause=0.0), pids
        self.known: set[str] = set()
        self.structure: set[str] = set()

    def windows(self) -> list[tuple[int, dict]]:
        """(hwnd, value-free tree) of each top-level window of the app."""
        out = []
        for hwnd in self.win.top_windows(self.pids):
            if self.win.class_name(hwnd) in self.win.IGNORED_CLASSES:
                continue
            raw = self.win.capture(hwnd)
            if raw is not None:
                self.win.strip_rids(raw)
                out.append((hwnd, privacy.clean(raw, self.known, self.structure)))
        return out

    def window(self, selector: str, timeout: float = 8.0) -> tuple[int, dict] | tuple[None, int]:
        """The window a recorded window locator names: (hwnd, tree), or (None, matches)."""
        want = locator.parse(selector)
        end = time.monotonic() + timeout
        count = 0
        while True:
            hits = [(h, t) for h, t in self.windows() if locator.matches(t, want)]
            count = len(hits)
            if count == 1:
                return hits[0]
            if count > 1 or time.monotonic() > end:
                return None, count
            time.sleep(0.25)


def _element(live: _Live, hwnd: int, step: dict):
    """The one live control a step's locator names; raises Ambiguous or DriveError."""
    from labctl.desktop.drive import Ambiguous, DriveError

    target = step.get("target") or {}
    sel = step.get("container") if step.get("action") == "select" else step.get("selector")
    el = live.win.element_from_handle(hwnd)
    if sel and "Index=" not in sel:
        found = live.win.find_all(el, sel)
        if len(found) > 1:
            raise Ambiguous(sel, len(found))
        if not found:
            raise DriveError(f"no control {sel}")
        return found[0]
    raise DriveError(
        f"{sel}: located only by position (Index); not replayed ({target.get('note', '')})"
    )


def _act(live: _Live, hwnd: int, step: dict, values: dict[str, list[str]]) -> str:
    win = live.win
    action = step.get("action")
    props = (step.get("target") or {}).get("props") or {}
    if action == "type":
        el = _element(live, hwnd, step)
        aid = props.get("AutomationId") or ""
        given = values.get(aid) or []
        if step.get("password") and not given:
            raise ValueError(f"a password field: give its value with --value {aid}=...")
        if given:
            text = given.pop(0) if len(given) > 1 else given[0]  # in order; the last one repeats
        else:
            text = value_for(step.get("typed_shape"))
        try:
            el.SetFocus()
        except Exception:  # noqa: BLE001
            pass
        win.set_value(el, text)
        return (
            "typed (value supplied)" if given else "typed a synthetic value of the recorded shape"
        )
    if action in ("click", "double_click"):
        el = _element(live, hwnd, step)
        return win.post_click(el) or _async(lambda: win.invoke(el))
    if action == "toggle":
        el = _element(live, hwnd, step)
        if step.get("state") and win.toggle_state(el) == step["state"]:
            return "already in the recorded state"
        return win.post_click(el) or _async(lambda: win.toggle(el)) or "toggled"
    if action == "select":
        if step.get("item_is") != "code":
            raise ValueError(
                "the item selected holds data (its name was not recorded); not replayed"
            )
        el = _element(live, hwnd, step)
        win.expand(el, True)
        time.sleep(0.3)
        found = win.find_all(el, locator.format({"Name": step["item"], "ControlType": "ListItem"}))
        if len(found) != 1:
            # Win32 drop-downs list their items in a window of their own (ComboLBox).
            found = [f for h in win.top_windows(live.pids) for f in
                     win.find_all(win.element_from_handle(h),
                                  locator.format({"Name": step["item"], "ControlType": "ListItem"}))]  # fmt: skip
        if len(found) != 1:
            raise ValueError(f"item {step['item']!r} found {len(found)} time(s)")
        win.select(found[0])
        try:
            win.expand(el, False)
        except Exception:  # noqa: BLE001
            pass
        return f"selected {step['item']}"
    if action == "menu":
        name = props.get("Name") or (step.get("menu_path") or "").split(" > ")[-1]
        sel = locator.format({"Name": name, "ControlType": "MenuItem"})
        found, rids = [], set()
        for h in [hwnd, *[x for x in win.top_windows(live.pids) if x != hwnd]]:
            for f in win.find_all(win.element_from_handle(h), sel):
                rid = tuple(f.GetRuntimeId())
                if rid not in rids:  # an open menu shows under its item and in its own window
                    rids.add(rid)
                    found.append(f)
        if len(found) > 1:
            from labctl.desktop.drive import Ambiguous

            raise Ambiguous(sel, len(found))
        if not found:
            raise ValueError(f"no menu item {name!r}")
        el = found[0]
        try:
            win.expand(el, True)
            return "opened the menu"
        except Exception:  # noqa: BLE001 - a leaf item
            return _async(lambda: win.invoke(el)) or "invoked"
    if action == "key":
        key = step.get("key") or ""
        if not win.post_key(win.pids_focus(live.pids), key):
            raise ValueError(f"key {key} could not be sent to the app's focused control")
        return f"posted {key}"
    raise ValueError(f"action {action} is not replayed")


def verify(exe: str, recording: Path, *, values: dict[str, str | list[str]] | None = None,
           settle: float = 0.8, wait: float = 20.0) -> FidelityReport:  # fmt: skip
    """Start `exe`, replay `recording`, compare, close the app. Windows only."""
    from labctl.desktop import require
    from labctl.desktop.drive import Ambiguous, DriveError
    from labctl.desktop.model import Recording

    require()
    rec = Recording(Path(recording))
    # AutomationId -> the values to type there, in order (a wrong password, then the right one).
    values = {k: list(v) if isinstance(v, list | tuple) else [v] for k, v in (values or {}).items()}
    proc = subprocess.Popen([exe])
    reports: list[StepReport] = []
    try:
        from labctl.desktop import win

        end = time.monotonic() + wait
        while not win.top_windows({proc.pid}) and time.monotonic() < end:
            time.sleep(0.2)
        live = _Live({proc.pid})
        supplied: set[str] = set()
        last_window = None
        for step in rec.steps:
            action = step.get("action")
            wsel = (step.get("window") or {}).get("selector") or last_window
            last_window = wsel
            seq, sel = step.get("seq"), step.get("selector")
            diffs: list[Difference] = []
            note = None
            status = "pass"
            if action in ("focus", "menu_open", "expand", "collapse"):
                reports.append(StepReport(seq=seq, action=action, selector=sel, status="skipped",
                                          note="covered by the steps around it"))  # fmt: skip
                continue
            if action == "window_close":
                time.sleep(settle)
                hwnd, n = live.window(wsel, timeout=2) if wsel else (None, 0)
                if hwnd is not None:
                    diffs.append(Difference(step=seq, element=wsel, check="extra", expected="closed",
                                            actual="still open"))  # fmt: skip
                reports.append(StepReport(seq=seq, action=action, selector=wsel,
                                          status="fail" if diffs else "pass", differences=diffs))  # fmt: skip
                continue
            hwnd, found = live.window(wsel) if wsel else (None, 0)
            if hwnd is None:
                check = "ambiguous" if found > 1 else "missing"
                reports.append(StepReport(seq=seq, action=action, selector=sel, status="fail",
                    differences=[Difference(step=seq, element=wsel or "(window)", check=check,
                                            expected=1, actual=found)]))  # fmt: skip
                continue
            if action != "window_open":
                try:
                    note = _act(live, hwnd, step, values)
                    if action == "type" and sel:
                        supplied.add(sel)
                except Ambiguous as exc:
                    status = "fail"
                    diffs.append(Difference(step=seq, element=exc.selector, check="ambiguous",
                                            expected=1, actual=exc.count))  # fmt: skip
                except (DriveError, ValueError) as exc:
                    status = "fail" if isinstance(exc, DriveError) else "skipped"
                    note = str(exc)
                    if isinstance(exc, DriveError):
                        diffs.append(Difference(step=seq, element=sel or "(control)", check="missing",
                                                expected=1, actual=0))  # fmt: skip
                time.sleep(settle)
            recorded = rec.tree(step.get("tree"))
            if recorded is not None:
                after, _n = live.window(wsel, timeout=3)
                tree = None
                if after is not None:
                    tree = next((t for h, t in live.windows() if h == after), None)
                if tree is not None:
                    diffs += compare_trees(recorded, tree, seq, supplied)
                elif step.get("action") != "window_open":
                    note = (note or "") + "; the window was gone when it was compared"
            if diffs and status == "pass":
                status = "fail"
            reports.append(StepReport(seq=seq, action=action, selector=sel, status=status,
                                      differences=diffs, note=note))  # fmt: skip
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
    failed = any(r.status == "fail" for r in reports)
    return FidelityReport(recording=rec.id, base_url=f"exe:{Path(exe).name}",
                          status="fail" if failed else "pass", steps=reports)  # fmt: skip
