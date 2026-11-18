"""Record a person (or a script) using a Windows desktop application, through UI Automation.

A desktop recording is a folder holding:

- `desktop.json`: the session: format, when, the processes recorded (executable name, the
  toolkit detected and the signals it rests on), which UI Automation events were listened to and
  what was polled or hooked instead, and notes;
- `steps.jsonl`: one JSON object per user action (shape below);
- `trees/<seq>.json`: the value-free control tree of the step's window after the action
  (`labctl.desktop.privacy` decides what is structure and what is data);
- `screens/<seq>.png`: a picture of that window only, when `screenshots=True` (off by default:
  pictures show data, and stay in `evidence/` like everything else here).

A step: `{"seq", "ts", "surface": "desktop", "action", "process", "pid", "window": {"selector",
"title", "props"}, "target": {"selector", "matches", "path", "candidates", "props", "index"},
"selector", "via", "tree", "screenshot", "url": null, "dom": null}` plus, by action,
`typed_shape`, `item`, `state`, `key`, `menu_path`, `focus_after`. `action` is one of `click`,
`double_click`, `right_click`, `type`, `select`, `toggle`, `expand`, `collapse`, `menu`, `key`,
`window_open`, `window_close`, `menu_open`, `focus`. `url` and `dom` are the web recorder's fields,
kept empty, so desktop and web steps can share one timeline (`labctl.desktop.split.interleave`).

How each action is seen (and where UI Automation events were not enough):

| Action | Source |
|---|---|
| click, double_click, right_click | low-level mouse hook (WH_MOUSE_LL) on a thread of its own, then `ElementFromPoint`; dropped when a pattern event for the same control follows within 0.8 s |
| click on a button by a pattern (a script, an accessibility tool) | `Invoke_Invoked` event, when the toolkit raises it |
| type | `ValueProperty` change events (shape only, computed in the handler); also read once when focus leaves an edit control, for toolkits that raise no value events |
| select | `SelectionItem_ElementSelected` event |
| toggle, expand, collapse | `ToggleState` / `ExpandCollapseState` property change events |
| menu | `Invoke_Invoked` on a MenuItem; `menu_open` when a menu window appears (polled) |
| key (Enter, Tab, Esc, F-keys, Delete, chords with Ctrl or Alt) | low-level keyboard hook; plain characters are never recorded |
| window_open, window_close, focus (between windows) | polled every 250 ms (EnumWindows, GetForegroundWindow) |

Only windows of the recorded processes are read: the hooks check the process of the window under
the mouse or in the foreground before anything else, and every UI Automation event is dropped
unless its element belongs to a recorded process.
"""

from __future__ import annotations

import json
import os as _os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from labctl.desktop import FORMAT, SURFACE, detect, locator, privacy, require

DEBUG = bool(_os.environ.get("TWINLAB_DESKTOP_DEBUG"))
SETTLE_S = 0.4
CLICK_GRACE_S = 0.8
MAX_BATCH_S = 3.0
POLL_S = 0.25
NAMED_KEYS = {0x0D: "Enter", 0x09: "Tab", 0x1B: "Esc", 0x2E: "Delete", 0x21: "PageUp",
              0x22: "PageDown", 0x24: "Home", 0x23: "End",
              **{0x70 + i: f"F{i + 1}" for i in range(12)}}  # fmt: skip
ARROWS = {0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down"}
POPUP_TYPES = {"Menu", "ToolTip"}
POPUP_CLASSES = (
    "#32768",
    "tooltips_class32",
    "ComboLBox",
    "WindowsForms10.Window.808.",
    "WindowsForms10.Window.20808.",
)  # 808: tool strip drop-downs


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- input hooks, on a thread of their own --------------------------------------------------------


class _Hooks(threading.Thread):
    """Low-level mouse and keyboard hooks. They need a message loop that never blocks, so they
    run here and only queue (x, y) or the chord; the recorder reads the element afterwards."""

    def __init__(self, out: queue.Queue, wanted: Callable[[int], bool],
                 resolve: Callable[[int, int], object] | None = None):  # fmt: skip
        super().__init__(daemon=True, name="twinlab-desktop-hooks")
        self.out, self.wanted, self.resolve = out, wanted, resolve
        self.thread_id = 0
        self.ready = threading.Event()
        self.failed: str | None = None
        self._last_down = (0.0, 0, 0)

    def run(self) -> None:  # noqa: C901 - one Win32 message loop
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            HOOKPROC,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        class MSLL(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("extra", ctypes.c_size_t)]  # fmt: skip

        class KBDLL(ctypes.Structure):
            _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]  # fmt: skip

        def pid_of(hwnd) -> int:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return pid.value

        dbl = user32.GetDoubleClickTime() / 1000.0

        def on_mouse(code, wparam, lparam):
            try:
                if code == 0 and wparam in (0x201, 0x204):
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLL)).contents
                    x, y = info.pt.x, info.pt.y
                    if self.wanted(pid_of(user32.WindowFromPoint(info.pt))):
                        t = time.time()
                        kind = "right_click" if wparam == 0x204 else "click"
                        last_t, lx, ly = self._last_down
                        if (
                            kind == "click"
                            and t - last_t < dbl
                            and abs(x - lx) < 5
                            and abs(y - ly) < 5
                        ):
                            kind = "double_click"
                        if kind != "right_click":
                            self._last_down = (t, x, y)
                        # The element is taken now, before the click is delivered: a moment
                        # later a dialog the click opens may cover the point.
                        el = quick = None
                        if self.resolve is not None:
                            try:
                                el, quick = self.resolve(x, y, user32.WindowFromPoint(info.pt))
                            except Exception:  # noqa: BLE001
                                el = quick = None
                        self.out.put({"kind": kind, "x": x, "y": y, "t": t, "el": el, "quick": quick,
                                      "injected": bool(info.flags & 1)})  # fmt: skip
            except Exception:  # noqa: BLE001 - a hook must never raise
                pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def on_key(code, wparam, lparam):
            try:
                if code == 0 and wparam in (0x100, 0x104):
                    info = ctypes.cast(lparam, ctypes.POINTER(KBDLL)).contents
                    if self.wanted(pid_of(user32.GetForegroundWindow())):
                        chord = self._chord(info.vk, user32)
                        if chord:
                            self.out.put({"kind": "key", "key": chord, "t": time.time(),
                                          "injected": bool(info.flags & 0x10)})  # fmt: skip
            except Exception:  # noqa: BLE001
                pass
            return user32.CallNextHookEx(None, code, wparam, lparam)

        if self.resolve is not None:
            import comtypes

            comtypes.CoInitializeEx(0)  # the multi-threaded apartment, as the recorder
        self._procs = (HOOKPROC(on_mouse), HOOKPROC(on_key))
        mod = kernel32.GetModuleHandleW(None)
        hm = user32.SetWindowsHookExW(14, self._procs[0], mod, 0)
        hk = user32.SetWindowsHookExW(13, self._procs[1], mod, 0)
        self.thread_id = kernel32.GetCurrentThreadId()
        if not hm or not hk:
            self.failed = f"SetWindowsHookEx failed (error {ctypes.get_last_error()})"
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for h in (hm, hk):
            if h:
                user32.UnhookWindowsHookEx(h)

    @staticmethod
    def _chord(vk: int, user32) -> str | None:
        down = lambda k: bool(user32.GetAsyncKeyState(k) & 0x8000)  # noqa: E731
        ctrl, alt, shift = down(0x11), down(0x12), down(0x10)
        if vk in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):
            return None  # a modifier on its own
        name = NAMED_KEYS.get(vk)
        if name is None and (ctrl or alt) and vk in ARROWS:
            name = ARROWS[vk]
        if name is None and (ctrl != alt) and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
            name = chr(vk)  # a shortcut (Ctrl+S, Alt+F); Ctrl+Alt is AltGr typing: never kept
        if name is None:
            return None
        mods = [m for m, on in (("Ctrl", ctrl), ("Alt", alt), ("Shift", shift)) if on]
        return "+".join([*mods, name])

    def stop(self) -> None:
        if self.thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT


# --- the recorder ----------------------------------------------------------------------------------


class Recorder:
    """Records the windows of a set of processes until they exit, `stop()` says so, Ctrl+C, or
    `max_seconds`."""

    def __init__(self, out_dir: Path, pids: set[int], *, screenshots: bool = False,
                 settle_ms: int = 400, max_seconds: float = 1800, hooks: bool = True,
                 stop: Callable[[], bool] | None = None) -> None:  # fmt: skip
        require()
        from labctl.desktop import win

        self.win = win
        self.out = Path(out_dir)
        (self.out / "trees").mkdir(parents=True, exist_ok=True)
        self.pids = set(pids)
        self.screenshots = screenshots
        self.settle = settle_ms / 1000.0
        self.max_seconds = max_seconds
        self.use_hooks = hooks
        self.stop_fn = stop
        self.q: queue.Queue = queue.Queue()
        self.seq = 0
        self.known: set[str] = set()  # words seen as data, in memory only
        self.structure: set[str] = set()  # words the application shows as structure
        self.trees: dict[int, tuple[dict, dict]] = {}  # hwnd -> (raw, clean), last seen
        self.titles: dict[int, dict] = {}  # hwnd -> window locator info
        self.windows: set[int] = set()
        self.popups: dict[int, int] = {}  # menu window -> owner window, while open
        self.popup_owner: dict[int, int] = {}  # every popup seen -> owner window
        self.last_combo: tuple[dict, float] | None = None  # the combo box last expanded
        self.opened_at: dict[int, float] = {}  # window -> when it was first seen
        self.last_window: dict = {"selector": None, "title": None, "props": {}}
        self.combo_items: dict[tuple, dict] = {}  # combo box runtime id -> its items (or count)
        self.pending: list[dict] = []
        self.last_activity = 0.0
        self.focus: dict | None = None
        self.typed_since_focus = False
        self.registered: set[int] = set()
        self.fg = 0
        self.processes: dict[int, dict] = {}
        self.notes: list[str] = []
        self.file = (self.out / "steps.jsonl").open("w", encoding="utf-8")
        self.started = _now()
        self._handlers: list = []

    # -- events from UI Automation (their threads) ------------------------------------------------

    def _ours(self, el) -> bool:
        try:
            return el.CurrentProcessId in self.pids
        except Exception:  # noqa: BLE001
            return False

    def _make_handlers(self):
        import comtypes

        rec, win = self, self.win

        class Event(comtypes.COMObject):
            _com_interfaces_ = [win.UIA.IUIAutomationEventHandler]

            def HandleAutomationEvent(self, sender, event_id):
                if not rec._ours(sender):
                    return
                kind = {win.E_INVOKED: "invoke", win.E_SELECTED: "select"}.get(event_id)
                desc = win.describe(sender)
                if kind and desc:
                    rec.q.put({"kind": kind, "desc": desc, "t": time.time()})

        class Prop(comtypes.COMObject):
            _com_interfaces_ = [win.UIA.IUIAutomationPropertyChangedEventHandler]

            def HandlePropertyChangedEvent(self, sender, prop, value):
                if not rec._ours(sender):
                    return
                value = win.variant(value)
                ev: dict = {"t": time.time()}
                if prop == win.P_VALUE:
                    try:
                        secret = bool(sender.CurrentIsPassword)
                        # Only what the user changes: a value the application fills in (a
                        # dialog loading its record) changes controls without keyboard focus.
                        if not sender.CurrentHasKeyboardFocus:
                            return
                        if sender.CurrentControlType == 50003:
                            return  # a combo box: its selection event says what was chosen
                        if not secret:
                            value = sender.GetCurrentPropertyValue(win.P_VALUE)
                    except Exception:  # noqa: BLE001
                        secret = True
                    ev["kind"] = "value"
                    if secret:
                        ev["password"] = True
                    else:
                        rec.known |= privacy.words(value) - rec.structure
                        ev["shape"] = privacy.shape(
                            value if isinstance(value, str) else str(value or "")
                        )
                    value = None  # the value itself goes no further
                elif prop == win.P_TOGGLE:
                    ev["kind"], ev["state"] = "toggle", win.TOGGLE.get(value)
                elif prop == win.P_EXPAND:
                    ev["kind"], ev["state"] = "expand", win.EXPAND.get(value)
                else:
                    return
                ev["desc"] = win.describe(sender)
                if ev["desc"]:
                    rec.q.put(ev)

        class Focus(comtypes.COMObject):
            _com_interfaces_ = [win.UIA.IUIAutomationFocusChangedEventHandler]

            def HandleFocusChangedEvent(self, sender):
                if rec._ours(sender):
                    desc = win.describe(sender)
                    if desc:
                        rec.q.put({"kind": "focus", "desc": desc, "t": time.time()})

        return Event(), Prop(), Focus()

    def _register(self, hwnd: int) -> None:
        if hwnd in self.registered:
            return
        import ctypes

        win, a = self.win, self.win.auto()
        try:
            el = win.element_from_handle(hwnd)
            for eid in (win.E_INVOKED, win.E_SELECTED):
                a.AddAutomationEventHandler(
                    eid, el, win.TREE_SCOPE_SUBTREE, None, self._handlers[0]
                )
            ids = (ctypes.c_int * 3)(win.P_VALUE, win.P_TOGGLE, win.P_EXPAND)
            a.AddPropertyChangedEventHandlerNativeArray(el, win.TREE_SCOPE_SUBTREE, None,
                                                        self._handlers[1], ids, 3)  # fmt: skip
            self.registered.add(hwnd)
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"could not listen to a window's events: {type(exc).__name__}")

    # -- the main loop --------------------------------------------------------------------------

    def run(self) -> Path:
        win = self.win
        self._handlers = list(self._make_handlers())
        hooks = None
        try:
            win.auto().AddFocusChangedEventHandler(None, self._handlers[2])
            for pid in self.pids:
                self._process(pid)
            if self.use_hooks:

                def resolve(x, y, hwnd):
                    el = win.element_from_point(x, y)
                    return el, win.describe_now(el, hwnd or 0)

                hooks = _Hooks(self.q, lambda pid: pid in self.pids, resolve)
                hooks.start()
                hooks.ready.wait(5)
                if hooks.failed:
                    self.notes.append(f"input hooks unavailable ({hooks.failed}): mouse clicks "
                                      "and key chords are not recorded, only pattern events")  # fmt: skip
            started = time.monotonic()
            next_poll = 0.0
            try:
                while True:
                    now = time.monotonic()
                    if self.stop_fn is not None and self.stop_fn():
                        break
                    if now - started > self.max_seconds:
                        self.notes.append(f"stopped after max_seconds={self.max_seconds:.0f}")
                        break
                    if now >= next_poll:
                        next_poll = now + POLL_S
                        if not any(win.alive(p) for p in self.pids):
                            break
                        self._poll_windows()
                    self._drain()
                    self._finish(force=False)
                    time.sleep(0.02)
            except KeyboardInterrupt:
                self.notes.append("stopped with Ctrl+C")
            time.sleep(0.2)
            self._drain()
            self._finish(force=True)
        finally:
            if hooks is not None:
                hooks.stop()
            try:
                win.auto().RemoveAllEventHandlers()
            except Exception:  # noqa: BLE001
                pass
            self.file.close()
            self._write_session()
        return self.out

    def _process(self, pid: int) -> None:
        if pid in self.processes:
            return
        sig = self.win.signals(pid)
        self.processes[pid] = {"pid": pid, "process": sig.get("process"), "signals": sig}

    def _poll_windows(self) -> None:
        win = self.win
        now = win.top_windows(self.pids)
        current = set(now)
        for hwnd in now:
            if hwnd in self.windows or hwnd in self.popups:
                continue
            el_type, cls = None, win.class_name(hwnd)
            if cls in win.IGNORED_CLASSES:
                continue
            try:
                el_type = win.CONTROL_TYPES.get(win.element_from_handle(hwnd).CurrentControlType)
            except Exception:  # noqa: BLE001
                pass
            self._register(hwnd)
            if el_type in POPUP_TYPES or cls.startswith(POPUP_CLASSES):
                owner = self._owner_window(hwnd)
                self.popups[hwnd] = owner
                self.popup_owner[hwnd] = owner
                if cls == "ComboLBox" or el_type == "List":
                    self._combo_list(hwnd)
                if el_type == "Menu" or cls == "#32768" or "808" in cls:
                    snap = self._snapshot(hwnd)
                    if snap is not None:
                        self.trees[hwnd] = snap
                    self._add({"kind": "menu_open", "hwnd": hwnd, "owner": owner,
                               "t": time.time(), "snap": snap})  # fmt: skip
                continue
            self.windows.add(hwnd)
            self.opened_at[hwnd] = time.time()
            # A window can come and go within one batch (a message box answered at once): its
            # tree is read now, while it is there.
            snap = self._snapshot(hwnd)
            if snap is not None:
                self.trees[hwnd] = snap
                self._window_info(hwnd, snap)
            self._add({"kind": "window_open", "hwnd": hwnd, "t": time.time(), "snap": snap})
        for hwnd in list(self.windows - current):
            self.windows.discard(hwnd)
            self._add({"kind": "window_close", "hwnd": hwnd, "t": time.time()})
        for hwnd in list(set(self.popups) - current):
            self.popups.pop(hwnd, None)
        fg = win.foreground()
        if fg != self.fg and fg in self.windows:
            just_opened = any(
                a["kind"] == "window_open" and a.get("hwnd") == fg for a in self.pending
            )
            if self.fg and not just_opened:
                self._add({"kind": "focus", "hwnd": fg, "t": time.time()})
            self.fg = fg

    def _combo_list(self, hwnd: int) -> None:
        """A combo box's drop-down list opened: its items, kept as a code list when the rule
        allows (labctl.desktop.privacy.code_list), else only counted."""
        raw = self.win.capture(hwnd)
        if raw is None or self.last_combo is None:
            return
        names = [
            n.get("name") or "" for n, _a, _i in locator.walk(raw) if n.get("type") == "ListItem"
        ]
        rid = tuple(self.last_combo[0].get("rid") or ())
        if names and privacy.code_list(names, self.known):
            kept = [privacy.keep_text(n) for n in names]
            self.combo_items[rid] = {"items": kept}
            for n in kept:
                self.structure |= privacy.words(n)
        elif names:
            self.combo_items[rid] = {"items_are": "data", "item_count": len(names)}

    def _owner_window(self, popup: int) -> int:
        own = self.win.owner(popup)
        if own in self.windows:
            return own
        return self.fg if self.fg in self.windows else next(iter(self.windows), 0)

    def _add(self, action: dict) -> None:
        self.pending.append(action)
        self.last_activity = time.monotonic()

    def _drain(self) -> None:
        win = self.win
        while True:
            try:
                ev = self.q.get_nowait()
            except queue.Empty:
                return
            kind = ev["kind"]
            if DEBUG:
                import sys as _sys

                print(
                    "DEBUG",
                    kind,
                    {k: v for k, v in ev.items() if k not in ("el",)},
                    file=_sys.stderr,
                )
            if kind in ("click", "double_click", "right_click"):
                try:
                    el = ev.get("el") or win.element_from_point(ev["x"], ev["y"])
                    desc = win.describe(el)
                    if not desc or desc.get("pid") not in self.pids:
                        desc = ev.get(
                            "quick"
                        )  # gone already (the click closed it): what the hook saw
                except Exception:  # noqa: BLE001
                    desc = None
                if not desc or desc.get("pid") not in self.pids:
                    continue
                if desc.get("type") == "MenuItem" and kind == "click":
                    kind = "menu"
                self._add({"kind": kind, "desc": desc, "t": ev["t"], "via": "mouse-hook",
                           "provisional": kind == "click", "injected": ev.get("injected")})  # fmt: skip
            elif kind == "key":
                self._add({"kind": "key", "key": ev["key"], "t": ev["t"], "via": "keyboard-hook",
                           "injected": ev.get("injected"), "hwnd": win.foreground()})  # fmt: skip
            elif kind == "value":
                self._typed(ev)
            elif kind == "focus":
                self._focus(ev)
            elif kind in ("invoke", "select", "toggle", "expand"):
                self._semantic(ev)

    def _same(self, a: dict, b: dict) -> bool:
        ra, rb = a.get("rid"), b.get("rid")
        return bool(ra and rb) and (
            ra == rb or ra in b.get("ancestors", ()) or rb in a.get("ancestors", ())
        )

    def _semantic(self, ev: dict) -> None:
        desc, kind, state = ev["desc"], ev["kind"], ev.get("state")
        for a in reversed(self.pending[-4:]):  # the same event raised twice (toolkits do)
            if (a.get("raw_kind") == kind and (a.get("desc") or {}).get("rid") == desc.get("rid")
                    and a.get("state") == state and ev["t"] - a["t"] < 0.6):  # fmt: skip
                return
        # A mouse click on the same control a moment before was this action.
        self.pending = [a for a in self.pending if not (
            a.get("provisional") and a.get("desc") and self._same(a["desc"], desc)
            and 0 <= ev["t"] - a["t"] < CLICK_GRACE_S)]  # fmt: skip
        base = {"desc": desc, "t": ev["t"], "raw_kind": kind, "state": state}
        if kind == "invoke":
            menu = desc.get("type") == "MenuItem"
            self._add({**base, "kind": "menu" if menu else "click", "via": "uia-invoke"})
        elif kind == "toggle":
            self._add({**base, "kind": "toggle", "via": "uia-property"})
        elif kind == "expand":
            if desc.get("type") == "MenuItem" or state not in ("expanded", "collapsed"):
                return
            action = "expand" if state == "expanded" else "collapse"
            if desc.get("type") == "ComboBox":
                self.last_combo = (desc, ev["t"])
            self._add({**base, "kind": action, "via": "uia-property"})
        elif kind == "select":
            self._add({**base, "kind": "select", "via": "uia-selection"})

    def _typed(self, ev: dict) -> None:
        desc = ev["desc"]
        for a in reversed(self.pending):
            if a["kind"] == "type" and a["desc"].get("rid") == desc.get("rid"):
                a["shape"] = ev.get("shape")
                self.last_activity = time.monotonic()
                break
        else:
            self._add({"kind": "type", "desc": desc, "shape": ev.get("shape"), "t": ev["t"],
                       "password": ev.get("password", False), "via": "uia-value-event"})  # fmt: skip
        if self.focus and self.focus.get("rid") == desc.get("rid"):
            self.typed_since_focus = True

    def _value_shape(self, rid: tuple) -> tuple[bool, str | None] | None:
        """The current value of the focused control, as (is_password, shape)."""
        win = self.win
        try:
            el = win.focused()
            if tuple(el.GetRuntimeId()) != rid:
                return None
            if el.CurrentIsPassword:
                return True, None
            raw = el.GetCurrentPropertyValue(win.P_VALUE)
            self.known |= privacy.words(raw)
            return False, privacy.shape(raw if isinstance(raw, str) else "")
        except Exception:  # noqa: BLE001
            return None

    def _focus(self, ev: dict) -> None:
        desc = ev["desc"]
        prev = self.focus
        if prev and prev.get("rid") != desc.get("rid") and not self.typed_since_focus:
            # No value event came for the control focus is leaving: compare its value now with
            # the value it had when it got focus (some toolkits raise no value events).
            now = self._last_shape_of(prev)
            if now is not None and now != prev.get("_shape"):
                self._add({"kind": "type", "desc": prev, "shape": now[1], "password": now[0],
                           "t": ev["t"], "via": "poll-at-focus-change"})  # fmt: skip
        for a in reversed(self.pending):
            if a["kind"] == "key" and a.get("key", "").endswith("Tab") and "focus_desc" not in a:
                a["focus_desc"] = desc
                break
        # The value it had when focus came (read in the event handler, so before any typing).
        raw = desc.pop("value", None)
        if "value" in (desc.get("patterns") or []):
            desc["_shape"] = (
                (True, None) if desc.get("password") else (False, privacy.shape(raw or ""))
            )
        else:
            desc["_shape"] = None
        self.focus = desc
        self.typed_since_focus = False

    def _last_shape_of(self, desc: dict):
        """The value shape of a control that just lost focus, read by its runtime id."""
        if "value" not in (desc.get("patterns") or []) or desc.get("readonly"):
            return None
        win = self.win
        try:
            cond = win.auto().CreatePropertyCondition(win.P_RUNTIME, list(desc["rid"]))
            el = win.element_from_handle(desc["window"]).FindFirst(win.TREE_SCOPE_SUBTREE, cond)
            if not el:
                return None
            if el.CurrentIsPassword:
                return (True, None)
            raw = el.GetCurrentPropertyValue(win.P_VALUE)
            self.known |= privacy.words(raw)
            return (False, privacy.shape(raw if isinstance(raw, str) else ""))
        except Exception:  # noqa: BLE001
            return None

    # -- steps ------------------------------------------------------------------------------------

    def _finish(self, force: bool) -> None:
        if not self.pending:
            return
        now = time.monotonic()
        quiet = now - self.last_activity >= self.settle
        oldest = time.time() - min(a["t"] for a in self.pending)
        if not force and not quiet and oldest < MAX_BATCH_S:
            return
        if not force and any(a.get("provisional") and time.time() - a["t"] < CLICK_GRACE_S
                             for a in self.pending):  # fmt: skip
            return
        batch = sorted(self.pending, key=lambda a: a["t"])
        self.pending = []
        after: dict[int, tuple[dict, dict] | None] = {}
        for a in batch:
            hwnd = self._window_of(a)
            if hwnd and hwnd not in after:
                after[hwnd] = self._snapshot(hwnd)
        for a in reversed(batch):  # a window gone by now: its tree as it was when it opened
            hwnd = self._window_of(a)
            if a.get("snap") is not None and after.get(hwnd) is None and a["kind"] == "window_open":
                after[hwnd] = a["snap"]
        for a in batch:
            if a["kind"] == "type" and self._unchanged(a, after):
                continue
            self._write_step(a, after)
        for hwnd, snap in after.items():
            if snap is not None:
                self.trees[hwnd] = snap

    def _unchanged(self, a: dict, after: dict) -> bool:
        """A value event whose value is the one the window showed before (or when it opened):
        the application raising an event while it fills the window in, not the user typing."""
        if a.get("password") or a.get("via") != "uia-value-event":
            return False
        hwnd = self._window_of(a)
        for snap in (self.trees.get(hwnd), after.get(hwnd)):
            if snap is None:
                continue
            path = self.win.find_rid(snap[0], tuple(a["desc"].get("rid") or ()))
            if path is None:
                continue
            node = snap[0]
            for i in path:
                node = node["children"][i]
            return privacy.shape(str(node.get("value") or "")) == a.get(
                "shape"
            ) and snap is self.trees.get(hwnd)
        return False

    def _window_of(self, a: dict) -> int:
        if a["kind"] == "menu_open":
            return a["hwnd"]
        hwnd = a.get("hwnd") or (a.get("desc") or {}).get("window") or 0
        if hwnd in self.popup_owner:
            hwnd = self.popup_owner[hwnd]
        elif hwnd and hwnd not in self.windows and self.win.class_name(hwnd) in ("ComboLBox",):
            hwnd = self.fg
        return hwnd

    def _snapshot(self, hwnd: int) -> tuple[dict, dict] | None:
        win = self.win
        if not hwnd or win.window_pid(hwnd) not in self.pids:
            return None
        raw = win.capture(hwnd)
        if raw is None:
            return None
        clean = privacy.clean(raw, self.known, self.structure)
        return raw, clean

    def _locate(self, desc: dict, snaps: list[tuple[dict, dict] | None]) -> dict:
        """The target's locator in the first snapshot that holds it (before, then after)."""
        for snap in snaps:
            if snap is None:
                continue
            raw, clean = snap
            path = self.win.find_rid(raw, tuple(desc.get("rid") or ()))
            if path is None:
                continue
            node, ancestors = {**clean, "idx": 0}, []
            for i in path:
                counts = {}
                kids = node.get("children") or []
                for k in kids[:i]:
                    counts[k.get("type")] = counts.get(k.get("type"), 0) + 1
                parent = dict(node)
                parent.setdefault("idx", 0)
                ancestors.append(parent)
                node = dict(kids[i])
                node["idx"] = counts.get(node.get("type"), 0)
            loc = locator.build(clean, node, tuple(ancestors), node["idx"])
            loc["type"] = node.get("type")
            if node.get("name_is") == "structure":
                loc["name"] = node.get("name")
            return loc
        # Not in any snapshot (it came and went between them): from its own properties.
        node = privacy.clean({k: v for k, v in desc.items() if k in ("type", "aid", "name", "cls", "fw")},
                             self.known)  # fmt: skip
        loc = {"selector": locator.step_of(node), "matches": None, "path": [], "candidates": [],
               "props": locator.props_of(node), "index": None, "type": node.get("type"),
               "note": "not found in a snapshot of its window; uniqueness not checked"}  # fmt: skip
        return loc

    def _window_info(self, hwnd: int, snap: tuple[dict, dict] | None) -> dict:
        if snap is not None:
            info = locator.window_locator(snap[1], self._pname(hwnd))
            info["title"] = snap[1].get("name")
            self.titles[hwnd] = info
        return dict(self.titles.get(hwnd) or {"selector": None, "title": None, "props": {}})

    def _pname(self, hwnd: int) -> str | None:
        pid = self.win.window_pid(hwnd) if hwnd else 0
        return (self.processes.get(pid) or {}).get("process")

    def _write_step(self, a: dict, after: dict) -> None:
        win = self.win
        hwnd = self._window_of(a)
        before = self.trees.get(hwnd)
        snap = after.get(hwnd)
        kind = a["kind"]
        self.seq += 1
        seq = self.seq
        pid = win.window_pid(hwnd) if hwnd and win.user32.IsWindow(hwnd) else None
        if pid is None:
            pid = (a.get("desc") or {}).get("pid")
        window = self._window_info(hwnd, snap or before)
        if not window.get("selector") and kind not in ("window_open", "menu_open"):
            window = dict(self.last_window)  # gone with the action (a menu that closed the app)
        elif window.get("selector"):
            self.last_window = window
        step: dict = {"seq": seq, "ts": datetime.fromtimestamp(a["t"], UTC).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"), "surface": SURFACE, "action": kind,
            "process": (self.processes.get(pid) or {}).get("process"), "pid": pid,
            "window": {k: window.get(k) for k in ("selector", "title", "props")},
            "target": None, "selector": None, "via": a.get("via", "poll"), "tree": None,
            "screenshot": None, "url": None, "dom": None}  # fmt: skip
        if a.get("injected"):
            step["injected"] = True  # synthetic input (a script), not a person's hand
        desc = a.get("desc")
        if desc:
            loc = self._locate(desc, [before, snap])
            step["target"] = loc
            step["selector"] = loc["selector"]
            if kind == "click" and desc.get("type") in ("CheckBox", "RadioButton"):
                # A click on a check box is a toggle: its state after, from the snapshot.
                kind = step["action"] = "toggle"
                path = win.find_rid(snap[0], tuple(desc.get("rid") or ())) if snap else None
                node = snap[0] if path is not None else None
                for i in path or []:
                    node = node["children"][i]
                a["state"] = (node or {}).get("toggle")
        if kind == "type":
            if a.get("password"):
                step["password"] = True
            elif a.get("shape") is not None:
                step["typed_shape"] = a["shape"]
        elif kind in ("toggle",):
            step["state"] = a.get("state")
        elif kind == "select" and desc:
            types = desc.get("ancestor_types") or []
            popup = desc.get("window") in self.popup_owner or not types
            if "ComboBox" in types or (
                popup and self.last_combo and a["t"] - self.last_combo[1] < 30
            ):
                if "ComboBox" in types:
                    rid = desc["ancestors"][types.index("ComboBox")]
                    cdesc = {"rid": rid, **self._desc_of(hwnd, rid)}
                else:
                    cdesc = self.last_combo[0]
                step["container"] = self._locate(cdesc, [before, snap])["selector"]
                step["selector"] = step["container"]  # a bot picks the item in the combo box
                name = desc.get("name") or ""
                if privacy.code_list([name], self.known):
                    step["item"], step["item_is"] = privacy.keep_text(name), "code"
                else:
                    step["item"], step["item_is"] = privacy.shape(name), "data"
            else:
                step["item"], step["item_is"] = (step["target"] or {}).get("name"), "data"
                step["container"] = self._container(desc, [before, snap])
        elif kind in ("expand", "collapse") and desc and desc.get("type") == "ComboBox":
            found = self.combo_items.get(tuple(desc.get("rid") or ()))
            if found is None:
                names = win.item_names(win.by_rid(hwnd, tuple(desc.get("rid") or ())))
                if names and privacy.code_list(names, self.known):
                    found = {"items": [privacy.keep_text(n) for n in names]}
                elif names:
                    found = {"items_are": "data", "item_count": len(names)}
            step.update(found or {})
        elif kind == "key":
            step["key"] = a.get("key")
            if a.get("focus_desc"):
                step["focus_after"] = self._locate(a["focus_desc"], [snap, before])["selector"]
        elif kind == "menu" and desc:
            step["menu_path"] = self._menu_path(desc, [before, snap])
        tree_snap = snap if kind != "window_close" else None
        if kind == "menu_open":
            tree_snap = a.get("snap") or self._snapshot(a["hwnd"])
            step["window"] = {k: self._window_info(a["owner"], self.trees.get(a["owner"])).get(k)
                              for k in ("selector", "title", "props")}  # fmt: skip
        if tree_snap is not None:
            rel = f"trees/{seq:04d}.json"
            clean = json.loads(json.dumps(tree_snap[1]))
            (self.out / rel).write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
            step["tree"] = rel
            if self.screenshots and kind != "window_close":
                shot = f"screens/{seq:04d}.png"
                if win.screenshot(hwnd if kind != "menu_open" else a["hwnd"], self.out / shot):
                    step["screenshot"] = shot
        self.file.write(json.dumps(step, ensure_ascii=False) + "\n")
        self.file.flush()

    def _desc_of(self, hwnd: int, rid: tuple) -> dict:
        """Properties of a control by runtime id (for one found only as an ancestor)."""
        el = self.win.by_rid(hwnd, tuple(rid or ()))
        d = self.win.describe(el) if el is not None else None
        return {k: v for k, v in (d or {}).items() if k in ("type", "aid", "name", "cls", "fw")}

    def _container(self, desc: dict, snaps) -> str | None:
        """The list, combo box or grid an item was selected in."""
        for snap in snaps:
            if snap is None:
                continue
            raw = snap[0]
            path = self.win.find_rid(raw, tuple(desc.get("rid") or ()))
            if not path:
                continue
            nodes, n = [raw], raw
            for i in path:
                n = n["children"][i]
                nodes.append(n)
            for k in range(len(nodes) - 2, -1, -1):
                if nodes[k].get("type") in ("ComboBox", "List", "DataGrid", "Table", "Tree", "Tab"):
                    cdesc = {"rid": nodes[k].get("rid")}
                    return self._locate(cdesc, [snap])["selector"]
        return None

    def _menu_path(self, desc: dict, snaps) -> str:
        """`Case > Open case`: the menu item names from the menu bar down (structure)."""
        names = []
        for snap in [*snaps, *list(self.trees.values())]:
            if snap is None:
                continue
            raw, clean = snap
            path = self.win.find_rid(raw, tuple(desc.get("rid") or ()))
            if path is None:
                continue
            n = clean
            for i in path:
                n = n["children"][i]
                if n.get("type") == "MenuItem" and n.get("name"):
                    names.append(n["name"])
            break
        if not names:
            names = [privacy.keep_text(desc.get("name") or "", self.known)]
        return " > ".join(names)

    def _write_session(self) -> None:
        procs = []
        for p in self.processes.values():
            sig = dict(p["signals"])
            trees = [json.loads((self.out / f).read_text(encoding="utf-8"))
                     for f in sorted((self.out / "trees").glob("*.json"))][:200]  # fmt: skip
            sig.update(detect.signals_from_trees(trees))
            tech = detect.detect(sig)
            sig["framework_ids"] = sorted(sig["framework_ids"])
            sig["classes"] = sorted(sig["classes"])
            procs.append({"process": p["process"], "pid": p["pid"], "technology": tech,
                          "signals": sig})  # fmt: skip
        doc = {
            "format": FORMAT,
            "surface": SURFACE,
            "recorder": "labctl.desktop.recorder",
            "started": self.started,
            "ended": _now(),
            "steps": self.seq,
            "processes": procs,
            "capture": {
                "uia_events": ["Invoke_Invoked", "SelectionItem_ElementSelected",
                               "ValueProperty changed", "ToggleState changed",
                               "ExpandCollapseState changed", "FocusChanged"],
                "hooks": ["WH_MOUSE_LL", "WH_KEYBOARD_LL"] if self.use_hooks else [],
                "polled": ["top-level windows of the recorded processes (open, close, menus)",
                           "foreground window", "edit value when focus leaves it"],
                "screenshots": self.screenshots,
            },
            "privacy": "names and values that may be data are kept as their shape only "
            "(labctl.desktop.privacy); typed values are never stored",
            "notes": self.notes,
        }  # fmt: skip
        (self.out / "desktop.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                                              encoding="utf-8")  # fmt: skip


# --- entry points ----------------------------------------------------------------------------------


def _wait_window(pids: set[int], timeout: float) -> bool:
    from labctl.desktop import win

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if win.top_windows(pids):
            return True
        time.sleep(0.2)
    return False


def attach(out_dir: Path, *, pid: int | None = None, process: str | None = None,
           title: str | None = None, script: Callable[[set[int]], None] | None = None,
           **kw) -> Path:  # fmt: skip
    """Record an application that is already running, found by pid, executable name or a
    regular expression on a window title. It is left running when the recording stops."""
    require()
    from labctl.desktop import win

    pids: set[int] = set()
    if pid:
        pids.add(int(pid))
    if process:
        pids |= set(win.pids_by_name(process))
    if title:
        pids |= set(win.pids_by_title(title))
    if not pids:
        raise RuntimeError("no running process matches: give --pid, --process or --title")
    return _run(Path(out_dir), pids, script, **kw)


def record(exe: str, out_dir: Path, *, args: list[str] | None = None,
           script: Callable[[set[int]], None] | None = None, close: bool = True,
           wait: float = 20.0, **kw) -> Path:  # fmt: skip
    """Start `exe` and record it until it exits (or Ctrl+C). With `script(pids)`, the script
    drives the application while it is recorded (scripted evidence runs and tests); the
    recording stops when the script returns. The application is closed at the end when
    `close` (the default), because the recorder started it."""
    require()
    proc = subprocess.Popen([exe, *(args or [])])
    try:
        if not _wait_window({proc.pid}, wait):
            raise RuntimeError(f"{Path(exe).name} showed no window within {wait:.0f} s")
        return _run(Path(out_dir), {proc.pid}, script, **kw)
    finally:
        if close and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _run(out: Path, pids: set[int], script, **kw) -> Path:
    if script is None:
        return Recorder(out, pids, **kw).run()
    done = threading.Event()
    failure: list[BaseException] = []

    def drive() -> None:
        try:
            time.sleep(0.8)  # the recorder has its handlers in place
            script(pids)
            time.sleep(1.2)  # the last step settles
        except BaseException as exc:  # noqa: BLE001 - reported after the recording is saved
            failure.append(exc)
        finally:
            done.set()

    def driver() -> None:
        import comtypes

        comtypes.CoInitializeEx(0)  # COINIT_MULTITHREADED
        try:
            drive()
        finally:
            comtypes.CoUninitialize()

    t = threading.Thread(target=driver, daemon=True, name="twinlab-desktop-script")
    t.start()
    path = Recorder(out, pids, stop=done.is_set, **kw).run()
    t.join(5)
    if failure:
        raise failure[0]
    return path
