"""Java Swing / AWT applications: recording, driving and verify through the Java Access Bridge.

Same recording format, privacy rule, locator model and report as the UI Automation path; only
how the tree is read and how actions are seen differ (docs/09-desktop.md):

| Action | How it is seen |
|---|---|
| click, double_click, right_click, menu, toggle | the same mouse hook; the control at the point is read through the bridge (`getAccessibleContextAt`) |
| key | the same keyboard hook |
| type | the focused control's text, polled through the bridge every 300 ms; its shape is kept when focus leaves it or another action comes |
| select (combo box), toggle by keyboard | the window's tree compared with the one read before: when a click or a key arrives (the tree is asked for in the hook), after each batch of actions, and every 1.5 s when idle: a combo box showing another item, a check box changing state |
| window_open, window_close, menu_open | polled, as for UI Automation |

The Access Bridge has no reliable event for typing or selection that works for every Swing
component, so this path polls. A change is therefore seen later than it happened; its step is
placed by what was seen: a change already there in the tree read when a click or a key arrived
happened before that input (unless the input was on that very control or in its list, see
`input_made`), one found only after the batch happened after it.

Locators: `jab:Name=Save;Role=push button` (Role and Name from the AccessibleContext; Index
among siblings of the same role when there is no name), with the ancestor path, exactly as UIA
locators. UiPath: `<wnd app='java.exe' cls='SunAwtFrame' title='...' /><java role='push button'
name='Save' />`.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from labctl.behaviour.fidelity import Difference, FidelityReport, StepReport
from labctl.desktop import locator, privacy, recorder
from labctl.desktop.recorder import Recorder

FOCUS_POLL_S = 0.3
IDLE_DIFF_S = 1.5
JAVA_POPUP_CLASSES = ("SunAwtWindow",)  # heavyweight popup menus and combo lists


def java_command(java: str, classpath: str, main: str, *args: str) -> list[str]:
    """A `java` command line that loads the Access Bridge for this run only."""
    from labctl.desktop import jab

    return [java, jab.JVM_FLAG, "-cp", classpath, main, *args]


def paths(tree: dict, path: tuple = (), ancestors: tuple = (), idx: int = 0):
    """(node, ancestors, idx, index path) for every node, as locator.walk with the child path."""
    yield tree, ancestors, idx, path
    counts: dict[tuple, int] = {}
    for i, child in enumerate(tree.get("children") or []):
        k = (child.get("type"), child.get("role"))
        n = counts.get(k, 0)
        counts[k] = n + 1
        yield from paths({**child, "idx": n}, (*path, i), (*ancestors, {**tree, "idx": idx}), n)


def find_paths(tree: dict, selector: str) -> list[tuple]:
    want = locator.parse(selector)
    out, seen = [], set()
    for node, _a, i, p in paths(tree):
        if locator.matches(node, want, i if "Index" in want else None):
            key = locator._same_control(node)
            if key is not None and key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def input_made(change: dict, target: dict | None) -> bool:
    """Whether a change found in the tree read when an input arrived is that input's own doing.

    The tree is asked for in the hook, but the bridge answers a moment later, when the click
    may already have had its effect. `change` is the control that changed (`rid`: its index
    path, `type`), `target` the control the mouse was pressed on or, for a key, the control with
    the keyboard focus. The input made the change when it was on that control, inside it, or
    (for a combo box) on an item of a list. Every other change was there before the input: it
    is a step of its own, placed before it."""
    if not target:
        return False
    rid, trid = tuple(change.get("rid") or ()), tuple(target.get("rid") or ())
    if rid and trid[: len(rid)] == rid:
        return True
    return change.get("type") == "ComboBox" and (
        target.get("type") in ("ListItem", "List")
        or "ComboBox" in (target.get("ancestor_types") or [])
    )


def place_changes(changes: list[dict], target: dict | None, t: float) -> list[dict]:
    """The changes found in the tree read when an input arrived at time `t`, each with the time
    that puts it where it happened: just before the input, or just after it when the input made
    it (`input_made`). A check box the input itself changed is left out: the click on it is the
    toggle step."""
    out = []
    for c in changes:
        own = input_made(c["desc"], target)
        if own and c["kind"] == "toggle":
            continue
        out.append({**c, "t": t + 0.001 if own else t - 0.001})
    return out


def same_structure(a: dict, b: dict) -> bool:
    """Whether two raw trees of a window hold the same controls at the same index paths (their
    values and states may differ)."""
    shape = lambda t: [(tuple(n.get("rid") or ()), n.get("role")) for n, _a, _i in locator.walk(t)]  # noqa: E731
    return shape(a) == shape(b)


class JavaRecorder(Recorder):
    """The recorder for Java windows: the bridge instead of UI Automation (see the module)."""

    def __init__(self, out_dir: Path, pids: set[int], **kw):
        super().__init__(out_dir, pids, **kw)
        from labctl.desktop import jab

        self.jab = jab
        self.bridge = jab.shared()
        self.focus_state: dict | None = None  # {"rid", "window", "type", "shape0", "shape", "desc"}
        self.focused_ctl: dict | None = None  # the control with the keyboard focus, any type
        self.last_diff = time.monotonic()

    # UI Automation is not used for Java windows.
    def _register(self, hwnd: int) -> None:
        return

    def _snapshot(self, hwnd: int):
        if not hwnd or self.win.window_pid(hwnd) not in self.pids:
            return None
        # Where the window is: a click that closes it is located by this. Read before the
        # tree, because the window can close while the bridge reads the tree (the tree still
        # comes back whole, the window's rectangle is then empty).
        rect = self._rect(hwnd)
        if rect is None:
            return None
        try:
            raw = self.bridge.call(self.jab.capture, hwnd)
        except Exception:  # noqa: BLE001 - the window went away
            return None
        if raw is None or not self.win.user32.IsWindowVisible(hwnd):
            return None  # gone while it was read: the last tree of the window stays
        raw["_screen"] = rect[:2]
        return raw, privacy.clean(raw, self.known, self.structure)

    def _rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        """The window's rectangle on the screen, or None when it is gone."""
        import ctypes
        from ctypes import wintypes

        r = wintypes.RECT()
        if not hwnd or not self.win.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right, r.bottom) if r.right > r.left else None

    def _desc_of(self, hwnd: int, rid: tuple) -> dict:
        snap = self.trees.get(hwnd)
        if not snap:
            return {}
        path = self.win.find_rid(snap[0], tuple(rid or ()))
        node = snap[0]
        for i in path or []:
            node = node["children"][i]
        return {k: node.get(k) for k in ("type", "name", "role", "fw")} if path is not None else {}

    def run(self) -> Path:
        win = self.win
        hooks = None
        try:
            for pid in self.pids:
                self._process(pid)
            if self.use_hooks:
                bridge, jab = self.bridge, self.jab

                def resolve(x, y, hwnd):
                    top = win.root_window(hwnd or 0)
                    # Taken now, in the hook: where the window is (a click can close it
                    # before the event is handled). Asked now, ahead of the calls waiting for
                    # the bridge thread: what is at the point, then the window's tree as the
                    # input finds it (what changed in it since the last read came before).
                    return top, {"rect": self._rect(top), "pid": win.window_pid(top),
                                 "desc": bridge.submit(jab.describe_at, top, x, y, urgent=True),
                                 "pre": bridge.submit(jab.capture, top, urgent=True)}  # fmt: skip

                def on_key(fg):
                    top = win.root_window(fg or 0)
                    return {"hwnd": top, "pre": bridge.submit(jab.capture, top, urgent=True)}

                hooks = recorder._Hooks(self.q, lambda pid: pid in self.pids, resolve, on_key)
                hooks.start()
                hooks.ready.wait(5)
                if hooks.failed:
                    self.notes.append(f"input hooks unavailable ({hooks.failed})")
            self.notes.append("Java window: read through the Java Access Bridge; typing and "
                              "selection are polled (see labctl.desktop.java)")  # fmt: skip
            started, next_poll, next_focus = time.monotonic(), 0.0, 0.0
            try:
                while True:
                    now = time.monotonic()
                    if self.stop_fn is not None and self.stop_fn():
                        break
                    if now - started > self.max_seconds:
                        self.notes.append(f"stopped after max_seconds={self.max_seconds:.0f}")
                        break
                    if now >= next_poll:
                        next_poll = now + recorder.POLL_S
                        if not any(win.alive(p) for p in self.pids):
                            break
                        self._poll_windows()
                    if now >= next_focus:
                        next_focus = now + FOCUS_POLL_S
                        self._poll_focus()
                    self._drain()
                    self._finish(force=False)
                    if not self.pending and now - self.last_diff > IDLE_DIFF_S:
                        self._idle_diff()
                    time.sleep(0.02)
            except KeyboardInterrupt:
                self.notes.append("stopped with Ctrl+C")
            time.sleep(0.2)
            self._poll_focus(leaving=True)
            self._drain()
            self._finish(force=True)
        finally:
            if hooks is not None:
                hooks.stop()
            self.file.close()
            self._write_session()
        return self.out

    def _poll_windows(self) -> None:
        super()._poll_windows()
        for hwnd in list(self.windows):  # a heavyweight Swing popup is a top-level window too
            if self.win.class_name(hwnd) in JAVA_POPUP_CLASSES and not (
                self.trees.get(hwnd) or ({},)
            )[0].get("name"):
                self.windows.discard(hwnd)
                self.popup_owner[hwnd] = self._owner_window(hwnd)
                self.pending = [a for a in self.pending if a.get("hwnd") != hwnd]
                snap = self.trees.get(hwnd)
                self._add({"kind": "menu_open", "hwnd": hwnd, "owner": self.popup_owner[hwnd],
                           "t": time.time(), "snap": snap})  # fmt: skip

    def _poll_focus(self, leaving: bool = False) -> None:
        """Typing: the focused text field's shape, read through the bridge, never kept raw."""
        fg = self.win.foreground()
        node = None
        if fg and self.win.window_pid(fg) in self.pids and not leaving:
            try:
                node = self.bridge.call(self.jab.focused, fg, timeout=5)
            except Exception:  # noqa: BLE001
                node = None
        self.focused_ctl = (
            {k: node.get(k) for k in ("rid", "window", "type")} if node is not None else None
        )
        f = self.focus_state
        same = (
            node is not None
            and f is not None
            and node.get("rid") == f["rid"]
            and node.get("window") == f["window"]
        )
        if f is not None and not same:
            self._flush_typing()
        if node is None:
            if leaving or (f is not None and not same):
                self.focus_state = None
            return
        raw = node.pop("value", None)
        length = node.pop("_length", None)  # a password's length: compared here, never kept
        shape = ("#", length) if node.get("password") else privacy.shape(raw or "")
        if raw:
            self.known |= privacy.words(raw) - self.structure
        if same:
            f["shape"] = shape
            return
        if node.get("type") == "Edit" and not node.get("readonly"):
            node["pid"] = self.win.window_pid(fg)
            self.focus_state = {"rid": node.get("rid"), "window": node.get("window"), "shape0": shape,
                                "shape": shape, "desc": node, "t": time.time()}  # fmt: skip
        else:
            self.focus_state = None

    def _flush_typing(self) -> None:
        f = self.focus_state
        if f is None:
            return
        desc = f["desc"]
        if f["shape"] == f["shape0"]:
            return
        if desc.get("password"):
            # A password field's text is never read (its length only shows that it changed).
            self._add(
                {
                    "kind": "type",
                    "desc": desc,
                    "password": True,
                    "t": f["t"],
                    "via": "jab-focus-poll",
                }
            )
        else:
            self._add(
                {
                    "kind": "type",
                    "desc": desc,
                    "shape": f["shape"],
                    "t": f["t"],
                    "via": "jab-focus-poll",
                }
            )
        f["shape0"] = f["shape"]

    def _drain(self) -> None:
        win = self.win
        while True:
            try:
                ev = self.q.get_nowait()
            except Exception:  # noqa: BLE001 - queue.Empty
                return
            kind = ev["kind"]
            if kind in ("typing", "input"):
                continue  # a character or navigation key: typing and selection are polled
            self._flush_typing()  # what was typed comes before the action that follows it
            quick = ev.get("quick") or {}
            if kind in ("click", "double_click", "right_click"):
                # The window as it was before the click (its last snapshot) says what was under
                # the point; the bridge's answer comes later, when a dialog may cover it.
                top = ev.get("el") or 0
                desc = self._hit(top, ev["x"], ev["y"], quick.get("rect"))
                fut = quick.get("desc")
                if desc is None:
                    try:
                        desc = fut.result(timeout=5) if fut is not None else None
                    except Exception:  # noqa: BLE001
                        desc = None
                self._before_input(top, quick.get("pre"), desc, ev["t"])
                if recorder.DEBUG:
                    import sys as _sys

                    print("DEBUG java click", ev.get("el"), ev["x"], ev["y"], (desc or {}).get("name"),
                          (desc or {}).get("rid"), file=_sys.stderr)  # fmt: skip
                if not desc:
                    continue
                hwnd = desc.get("window") or 0
                # The hook passes only clicks in windows of the recorded processes; the
                # window itself may be gone by now (the click closed it).
                desc["pid"] = win.window_pid(hwnd) or quick.get("pid")
                if not desc["pid"] and (hwnd in self.trees or hwnd in self.popup_owner):
                    desc["pid"] = next(iter(self.pids))
                if desc["pid"] not in self.pids:
                    continue
                if desc.get("type") == "MenuItem" and kind == "click":
                    kind = "menu"
                self._add({"kind": kind, "desc": desc, "t": ev["t"], "via": "mouse-hook+jab",
                           "injected": ev.get("injected")})  # fmt: skip
            elif kind == "key":
                self._before_input(quick.get("hwnd") or 0, quick.get("pre"), self.focused_ctl,
                                   ev["t"])  # fmt: skip
                self._add({"kind": "key", "key": ev["key"], "t": ev["t"], "via": "keyboard-hook",
                           "injected": ev.get("injected"), "hwnd": win.foreground()})  # fmt: skip

    def _before_input(self, hwnd: int, fut, target: dict | None, t: float) -> None:
        """The window's tree as a click or a key found it (asked for in the hook), compared
        with the tree read before: what changed in between is a step of its own, placed before
        the input, or after it when the input itself made the change (`place_changes`). Without
        this a combo box item chosen before a click was only found after the click's batch,
        and written after it.

        Only a tree with the controls of the last one is used (`same_structure`): controls are
        known by their index path, and the click itself may already have changed the window (a
        menu that opened shifts the paths). Such a tree is dropped; what changed is then found
        after the batch, as before."""
        old = self.trees.get(hwnd)
        if fut is None or not hwnd or old is None:
            return
        try:
            raw = fut.result(timeout=5)
        except Exception:  # noqa: BLE001 - the window went away, or the JVM did
            raw = None
        if raw is None or not same_structure(old[0], raw):
            return
        rect = self._rect(hwnd)
        if rect is None or not self.win.user32.IsWindowVisible(hwnd):
            return  # the window is closing: its last tree stays
        new = (raw, privacy.clean(raw, self.known, self.structure))
        self.pending.extend(place_changes(self._changes(hwnd, old, new, covered=False), target, t))
        raw["_screen"] = rect[:2]
        self.trees[hwnd] = new  # the same controls at the same paths, their newest state

    HIT_TYPES = {
        "Button",
        "Edit",
        "CheckBox",
        "RadioButton",
        "ComboBox",
        "MenuItem",
        "ListItem",
        "TabItem",
        "Hyperlink",
        "Text",
        "HeaderItem",
        "Table",
        "List",
        "Tree",
        "Spinner",
    }

    def _hit(self, hwnd: int, x: int, y: int, rect: tuple | None = None) -> dict | None:
        """The control at a screen point, from the window's last snapshot. `rect` is where the
        window was when the mouse was pressed (read in the hook); without it, where it is now
        or, when it is gone, where it was when the snapshot was read."""
        snap = self.trees.get(hwnd)
        if not hwnd or snap is None:
            return None
        rect = rect or self._rect(hwnd)
        left, top = rect[:2] if rect else snap[0].get("_screen", (0, 0))
        px, py = x - left, y - top
        best, best_anc = None, ()
        for node, anc, _i in locator.walk(snap[0]):
            rect = node.get("rect")
            if not rect or not node.get("visible", True):
                continue
            rx, ry, w, h = rect
            if rx <= px < rx + w and ry <= py < ry + h and node.get("type") in self.HIT_TYPES:
                best, best_anc = node, anc  # walk is depth first: the last hit is the deepest
        if recorder.DEBUG:
            import sys as _sys

            print("DEBUG hit", hwnd, (left, top), (px, py), snap[0].get("rect"),
                  snap[0].get("name"), best and best.get("name"), file=_sys.stderr)  # fmt: skip
        if best is None:
            return None
        desc = {k: best.get(k) for k in ("type", "name", "role", "fw", "rid", "patterns")}
        desc.update(
            {"window": hwnd, "ancestor_types": [a.get("type") for a in reversed(best_anc)][:-1]}
        )
        return desc

    def _finish(self, force: bool) -> None:
        if not self.pending:
            return
        before = {h: s for h, s in self.trees.items()}
        batch_windows = {self._window_of(a) for a in self.pending}
        super()._finish(force)
        if not self.pending:  # the batch was written: compare its windows before and after
            for hwnd in batch_windows:
                self._diff(hwnd, before.get(hwnd), self.trees.get(hwnd), covered=True)

    def _idle_diff(self) -> None:
        self.last_diff = time.monotonic()
        fg = self.win.foreground()
        if fg not in self.windows:
            return
        old = self.trees.get(fg)
        new = self._snapshot(fg)
        if new is None or not self.q.empty():
            # A click or a key arrived while the tree was read: it may show the window half
            # way through what that input does, and the input is located in the last tree.
            # That tree stays; the window is compared again after the input's batch.
            return
        self.trees[fg] = new
        self._diff(fg, old, new, covered=False)
        if self.pending:
            self._finish(force=True)

    def _diff(self, hwnd: int, old, new, covered: bool) -> None:
        """Steps the tree shows but no event did, found after a batch or when idle: written
        now, with the time they were found."""
        added = self._changes(hwnd, old, new, covered)
        for a in added:
            self.pending.append(a)
        if added:
            self.last_activity = 0  # write them now
            super()._finish(force=True)

    def _changes(self, hwnd: int, old, new, covered: bool) -> list[dict]:
        """What two trees of a window show changed: a combo box showing another item, a check
        box in another state (not when `covered`: a click on it was recorded)."""
        if old is None or new is None:
            return []
        o = {tuple(n.get("rid") or ()): n for n, _a, _i in locator.walk(old[0])}
        added = []
        for n, _a, _i in locator.walk(new[0]):
            rid = tuple(n.get("rid") or ())
            was = o.get(rid)
            if was is None or was.get("type") != n.get("type") or not rid:
                continue
            desc = {k: n.get(k) for k in ("type", "name", "role", "fw", "rid")}
            desc.update({"window": hwnd, "pid": self.win.window_pid(hwnd), "ancestor_types": []})
            t = n.get("type")
            if t == "ComboBox" and was.get("value") != n.get("value") and n.get("value"):
                item = n["value"]
                code = privacy.code_list([item], self.known)
                added.append({"kind": "select", "desc": desc, "t": time.time(), "via": "jab-tree-diff",
                              "item": privacy.keep_text(item) if code else privacy.shape(item),
                              "item_is": "code" if code else "data"})  # fmt: skip
            elif t == "CheckBox" and was.get("toggle") != n.get("toggle") and not covered:
                added.append({"kind": "toggle", "desc": desc, "state": n.get("toggle"),
                              "t": time.time(), "via": "jab-tree-diff"})  # fmt: skip
        return added


def record(command: list[str], out_dir: Path, *, script=None, wait: float = 30.0, **kw) -> Path:
    """Start a Java application (a full command line, see `java_command`) and record it."""
    from labctl.desktop import require

    require()
    proc = subprocess.Popen(command)
    try:
        if not recorder._wait_window({proc.pid}, wait):
            raise RuntimeError(f"{Path(command[0]).name} showed no window within {wait:.0f} s")
        return recorder._run(Path(out_dir), {proc.pid}, script, JavaRecorder, **kw)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


# --- driving (scripted evidence runs, verify) ---------------------------------------------------


class JavaDriver:
    """Drives a Java application through the bridge (text, selection) and, for buttons, menus
    and check boxes, with real mouse clicks guarded like labctl.desktop.drive (a person's hand
    is what the recorder's hook sees) or, with `post=True`, with mouse messages posted to the
    Java window (verify: no input device is touched)."""

    def __init__(self, pids: set[int], pause: float = 0.6, post: bool = False):
        from labctl.desktop import jab, win

        self.win, self.jab, self.pids, self.pause_s, self.post = win, jab, set(pids), pause, post
        self.bridge = jab.shared()

    def pause(self, s: float | None = None) -> None:
        time.sleep(self.pause_s if s is None else s)

    def tree(self, hwnd: int) -> dict | None:
        raw = self.bridge.call(self.jab.capture, hwnd)
        return privacy.clean(raw) if raw else None

    def window(self, selector: str, timeout: float = 10.0) -> int:
        want = locator.parse(selector)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            hits = []
            for h in self.win.top_windows(self.pids):
                t = self.tree(h)
                if t is not None and locator.matches(t, want):
                    hits.append(h)
            if len(hits) == 1:
                self.pause()  # a person reads a window before acting in it
                return hits[0]
            if len(hits) > 1:
                from labctl.desktop.drive import Ambiguous

                raise Ambiguous(selector, len(hits))
            time.sleep(0.3)
        from labctl.desktop.drive import DriveError

        raise DriveError(f"no window {selector} within {timeout:.0f} s")

    def path(self, hwnd: int, selector: str, timeout: float = 5.0) -> tuple[tuple, dict]:
        from labctl.desktop.drive import Ambiguous, DriveError

        end = time.monotonic() + timeout
        while True:
            t = self.tree(hwnd) or {}
            found = find_paths(t, selector)
            if len(found) == 1:
                node = t
                for i in found[0]:
                    node = node["children"][i]
                return found[0], node
            if len(found) > 1:
                raise Ambiguous(selector, len(found))
            if time.monotonic() > end:
                raise DriveError(f"no control {selector}")
            time.sleep(0.2)

    def type(self, hwnd: int, selector: str, text: str) -> None:
        p, _node = self.path(hwnd, selector)
        self.bridge.call(self.jab.focus, hwnd, p)
        self.pause(0.4)
        self.bridge.call(self.jab.set_text, hwnd, p, text)
        self.pause()

    def choose(self, hwnd: int, selector: str, index: int) -> None:
        p, _node = self.path(hwnd, selector)
        self.bridge.call(self.jab.select_index, hwnd, p, index)
        self.pause()

    def click(self, hwnd: int, selector: str, scope: list[int] | None = None) -> None:
        """Click a control, looked for in `hwnd` (or in every window of the app with `scope`)."""
        from labctl.desktop.drive import DriveError

        windows = scope or [hwnd]
        end = time.monotonic() + 5
        while True:
            hit = []
            for h in windows:
                try:
                    p, node = self.path(h, selector, timeout=0)
                    hit.append((h, node))
                except DriveError:
                    continue
            if hit or time.monotonic() > end:
                break
            time.sleep(0.2)
            windows = scope and self.win.top_windows(self.pids) or windows
        if len(hit) != 1:
            raise DriveError(f"{selector}: found {len(hit)} time(s)")
        h, node = hit[0]
        x0, y0 = self._origin(h)
        rx, ry, w, hh = node["rect"]
        x, y = x0 + rx + w // 2, y0 + ry + hh // 2
        if self.post:
            self._post_press(h, node, x, y)
        else:
            if self.win.foreground() != h:
                self.win.user32.SetForegroundWindow(h)
                time.sleep(0.3)
            fg = self.win.foreground()
            if not fg or self.win.window_pid(fg) not in self.pids:
                raise DriveError("the Java application is not in the foreground; not clicking")
            if self.win.window_pid(self.win.window_at(x, y)) not in self.pids:
                raise DriveError(f"{selector}: covered by another window; not clicking")
            self.win.mouse_click(x, y)
        self.pause()

    def _origin(self, hwnd: int) -> tuple[int, int]:
        import ctypes
        from ctypes import wintypes

        r = wintypes.RECT()
        self.win.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top

    def _post_press(self, hwnd: int, node: dict, x: int, y: int) -> None:
        """Press without input devices: menus and check boxes through the bridge's action (it
        returns at once for them); a button by giving it focus through the bridge and posting
        the space key to its window (a button's action can open a modal dialog, and the
        bridge's action call would then wait until that dialog closes)."""
        p = self._path_of_node(hwnd, node)
        if node.get("role") in ("menu", "menu item", "check box", "radio button") and p is not None:
            fut = self.bridge.submit(self.jab.act, hwnd, p, "click")
            try:
                fut.result(timeout=5)
            except Exception:  # noqa: BLE001 - Exit ends the JVM while answering
                pass
            return
        if p is not None and self.bridge.call(self.jab.focus, hwnd, p):
            time.sleep(0.2)
            from ctypes import wintypes

            post = self.win.user32.PostMessageW
            post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            post(hwnd, 0x0100, 0x20, 0x00390001)  # WM_KEYDOWN VK_SPACE
            post(hwnd, 0x0101, 0x20, 0xC0390001)  # WM_KEYUP
            return
        self._post_click(hwnd, x, y)

    def _path_of_node(self, hwnd: int, node: dict) -> tuple | None:
        t = self.tree(hwnd) or {}
        for n, _a, _i, p in paths(t):
            if (n.get("role"), n.get("name"), n.get("rect")) == (
                node.get("role"),
                node.get("name"),
                node.get("rect"),
            ):
                return p
        return None

    def _post_click(self, hwnd: int, x: int, y: int) -> None:
        """WM_LBUTTONDOWN/UP posted to the Java window at the point: AWT turns them into a
        click; no input device is touched."""
        import ctypes
        from ctypes import wintypes

        pt = wintypes.POINT(x, y)
        self.win.user32.ScreenToClient(hwnd, ctypes.byref(pt))
        lp = (pt.y << 16) | (pt.x & 0xFFFF)
        post = self.win.user32.PostMessageW
        post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        post(hwnd, 0x0200, 0, lp)  # WM_MOUSEMOVE
        post(hwnd, 0x0201, 1, lp)  # WM_LBUTTONDOWN
        post(hwnd, 0x0202, 0, lp)  # WM_LBUTTONUP


def verify(command: list[str], recording: Path, *, values: dict | None = None, settle: float = 1.0,
           wait: float = 30.0) -> FidelityReport:  # fmt: skip
    """Start the Java app, replay the recording through the bridge (and posted clicks), compare
    each step's window with the recorded tree, close the app."""
    from labctl.desktop import require
    from labctl.desktop.drive import Ambiguous, DriveError
    from labctl.desktop.model import Recording
    from labctl.desktop.verify import compare_trees, value_for

    require()
    rec = Recording(Path(recording))
    values = {k: list(v) if isinstance(v, list | tuple) else [v] for k, v in (values or {}).items()}
    proc = subprocess.Popen(command)
    reports: list[StepReport] = []
    try:
        if not recorder._wait_window({proc.pid}, wait):
            raise RuntimeError("the Java application showed no window")
        d = JavaDriver({proc.pid}, pause=0.3, post=True)
        last = None
        for step in rec.steps:
            action, seq, sel = step.get("action"), step.get("seq"), step.get("selector")
            wsel = (step.get("window") or {}).get("selector") or last
            last = wsel
            if action in ("focus", "menu_open", "expand", "collapse"):
                reports.append(StepReport(seq=seq, action=action, selector=sel, status="skipped",
                                          note="covered by the steps around it"))  # fmt: skip
                continue
            diffs, note, status = [], None, "pass"
            try:
                if action == "window_close":
                    time.sleep(settle)
                    try:
                        d.window(wsel, timeout=1.5)
                        diffs.append(Difference(step=seq, element=wsel, check="extra",
                                                expected="closed", actual="still open"))  # fmt: skip
                    except DriveError:
                        pass
                    reports.append(StepReport(seq=seq, action=action, selector=wsel,
                                              status="fail" if diffs else "pass", differences=diffs))  # fmt: skip
                    continue
                hwnd = d.window(wsel)
                name = ((step.get("target") or {}).get("props") or {}).get("Name") or ""
                if action == "type":
                    given = values.get(name) or []
                    if step.get("password") and not given:
                        raise ValueError(
                            f"a password field: give its value with --value {name}=..."
                        )
                    text = (
                        (given.pop(0) if len(given) > 1 else given[0])
                        if given
                        else value_for(step.get("typed_shape"))
                    )
                    d.type(hwnd, sel, text)
                    note = "typed"
                elif action in ("click", "toggle", "menu", "double_click"):
                    scope = d.win.top_windows(d.pids) if action == "menu" else None
                    d.click(hwnd, sel, scope)
                    note = "clicked (posted)"
                elif action == "select":
                    if step.get("item_is") != "code":
                        raise ValueError("the item selected holds data; not replayed")
                    raw = d.bridge.call(d.jab.capture, hwnd)
                    p = find_paths(privacy.clean(raw), sel)
                    if len(p) != 1:
                        raise Ambiguous(sel, len(p)) if p else DriveError(f"no control {sel}")
                    combo = raw
                    for i in p[0]:
                        combo = combo["children"][i]
                    items = list(dict.fromkeys(n.get("name") for n, _a, _i in locator.walk(combo)
                                               if n.get("type") == "ListItem"))  # fmt: skip
                    # The items of a closed Swing combo box are often not in the tree: try them in
                    # order until the combo box shows the recorded one.
                    tries = [items.index(step["item"])] if step["item"] in items else range(30)
                    for idx in tries:
                        d.choose(hwnd, sel, idx)
                        shown = d.bridge.call(d.jab.capture, hwnd)
                        for i in p[0]:
                            shown = shown["children"][i]
                        if shown.get("value") == step["item"]:
                            break
                    else:
                        raise DriveError(f"{sel}: no item {step['item']!r} could be selected")
                    note = f"selected {step['item']}"
                elif action == "key":
                    raise ValueError("keys are not replayed into Java windows")
                time.sleep(settle)
            except Ambiguous as exc:
                status = "fail"
                diffs.append(Difference(step=seq, element=exc.selector, check="ambiguous",
                                        expected=1, actual=exc.count))  # fmt: skip
            except DriveError as exc:
                status, note = "fail", str(exc)
                diffs.append(Difference(step=seq, element=sel or wsel or "(control)", check="missing",
                                        expected=1, actual=0))  # fmt: skip
            except ValueError as exc:
                status, note = "skipped", str(exc)
            recorded = rec.tree(step.get("tree"))
            if recorded is not None and status != "fail":
                try:
                    h = d.window(wsel, timeout=3)
                    live = d.tree(h)
                    if live is not None:
                        diffs += compare_trees(recorded, live, seq)
                except DriveError:
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
    return FidelityReport(recording=rec.id, base_url=f"java:{Path(command[-1]).name}",
                          status="fail" if failed else "pass", steps=reports)  # fmt: skip
