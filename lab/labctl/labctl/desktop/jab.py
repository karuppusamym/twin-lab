"""Windows only: the Java Access Bridge (WindowsAccessBridge-64.dll) through ctypes.

Java Swing and AWT windows show UI Automation one empty pane. Their controls are reached
through the Java Access Bridge instead, which the JVM must load: either for every Java program
of the user (`jabswitch -enable`, then restart the application; a person decides that) or for
one run only, by starting the JVM with
`-Djavax.accessibility.assistive_technologies=com.sun.java.accessibility.AccessBridge`
(what `labctl desktop record --java` and the tests do, so nothing persistent changes).

The bridge answers on the thread that called `Windows_run` and pumps its messages, so every call
goes through `Bridge`, one worker thread that pumps and runs calls in turn. Nodes come out in the
UI Automation tree shape (type, name, ...) plus `role` and `fw: "Java"`, so privacy, locators,
the model and the comparison treat both alike. A node's `rid` is its index path from the
window: Java objects have no runtime id that survives between calls.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
from concurrent.futures import Future
from ctypes import wintypes
from pathlib import Path

from labctl.desktop import locator

MAX_STRING, SHORT_STRING = 1024, 256
jint = ctypes.c_int
JOBJECT = ctypes.c_int64  # windowsaccessbridge-64: JOBJECT64 is a jlong
VMID = ctypes.c_long
MAX_NODES, MAX_ROWS, MAX_DEPTH = 1500, 25, 30
JVM_FLAG = "-Djavax.accessibility.assistive_technologies=com.sun.java.accessibility.AccessBridge"

ROLE_TYPE = {
    "push button": "Button", "toggle button": "Button", "text": "Edit", "password text": "Edit",
    "combo box": "ComboBox", "check box": "CheckBox", "radio button": "RadioButton",
    "label": "Text", "table": "Table", "menu bar": "MenuBar", "menu": "MenuItem",
    "menu item": "MenuItem", "check box menu item": "MenuItem", "radio button menu item": "MenuItem",
    "popup menu": "Menu", "frame": "Window", "dialog": "Window", "internal frame": "Window",
    "panel": "Pane", "root pane": "Pane", "layered pane": "Pane", "scroll pane": "Pane",
    "viewport": "Pane", "option pane": "Pane", "split pane": "Pane", "filler": "Pane",
    "scroll bar": "ScrollBar", "list": "List", "list item": "ListItem", "page tab list": "Tab",
    "page tab": "TabItem", "tree": "Tree", "tool bar": "ToolBar", "column header": "HeaderItem",
    "spin box": "Spinner", "slider": "Slider", "progress bar": "ProgressBar",
    "hyperlink": "Hyperlink", "separator": "Custom", "glass pane": "Pane", "canvas": "Pane",
}  # fmt: skip


class AccessibleContextInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * MAX_STRING), ("description", ctypes.c_wchar * MAX_STRING),
                ("role", ctypes.c_wchar * SHORT_STRING), ("role_en_US", ctypes.c_wchar * SHORT_STRING),
                ("states", ctypes.c_wchar * SHORT_STRING), ("states_en_US", ctypes.c_wchar * SHORT_STRING),
                ("indexInParent", jint), ("childrenCount", jint), ("x", jint), ("y", jint),
                ("width", jint), ("height", jint), ("accessibleComponent", wintypes.BOOL),
                ("accessibleAction", wintypes.BOOL), ("accessibleSelection", wintypes.BOOL),
                ("accessibleText", wintypes.BOOL), ("accessibleInterfaces", wintypes.BOOL)]  # fmt: skip


class AccessibleTextInfo(ctypes.Structure):
    _fields_ = [("charCount", jint), ("caretIndex", jint), ("indexAtPoint", jint)]


class AccessibleActionInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * SHORT_STRING)]


class AccessibleActionsToDo(ctypes.Structure):
    _fields_ = [("actionsCount", jint), ("actions", AccessibleActionInfo * 32)]


def find_dll() -> Path | None:
    """WindowsAccessBridge-64.dll: in System32 when a JRE installed it, else in a JDK's bin."""
    candidates = [
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsAccessBridge-64.dll"
    ]
    for var in ("TWINLAB_JAVA_HOME", "JAVA_HOME"):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]) / "bin" / "windowsaccessbridge-64.dll")
    for base in (Path(r"C:\Program Files\Microsoft"), Path(r"C:\Program Files\Java"),
                 Path(r"C:\Program Files\Eclipse Adoptium")):  # fmt: skip
        if base.is_dir():
            candidates += sorted(base.glob("*/bin/windowsaccessbridge-64.dll"), reverse=True)
    return next((c for c in candidates if c.is_file()), None)


class BridgeUnavailable(RuntimeError):
    pass


class Bridge:
    """One worker thread that owns the Access Bridge: it pumps messages and runs calls."""

    def __init__(self, dll: Path | None = None, warmup: float = 1.0):
        self.path = dll or find_dll()
        if self.path is None:
            raise BridgeUnavailable("WindowsAccessBridge-64.dll not found (install a JDK/JRE, or "
                                    "set TWINLAB_JAVA_HOME to one)")  # fmt: skip
        self.q: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.error: str | None = None
        self.stopping = False
        self.t = threading.Thread(target=self._run, daemon=True, name="twinlab-java-bridge")
        self.t.start()
        self.ready.wait(10)
        if self.error:
            raise BridgeUnavailable(self.error)
        time.sleep(warmup)  # the bridge finds the JVMs only after a few messages

    def _run(self) -> None:
        try:
            os.add_dll_directory(str(self.path.parent))
            dll = ctypes.WinDLL(str(self.path))
            self._bind(dll)
            dll.Windows_run()
            self.dll = dll
        except OSError as exc:
            self.error = f"cannot load {self.path.name}: {exc}"
            self.ready.set()
            return
        self.ready.set()
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        while not self.stopping:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            try:
                fn, args, fut = self.q.get(timeout=0.01)
            except queue.Empty:
                continue
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn(self, *args))
                except Exception as exc:  # noqa: BLE001 - handed to the caller
                    fut.set_exception(exc)

    @staticmethod
    def _bind(d) -> None:
        P = ctypes.POINTER
        d.isJavaWindow.argtypes = [wintypes.HWND]
        d.getAccessibleContextFromHWND.argtypes = [wintypes.HWND, P(VMID), P(JOBJECT)]
        d.getAccessibleContextInfo.argtypes = [VMID, JOBJECT, P(AccessibleContextInfo)]
        d.getAccessibleChildFromContext.argtypes = [VMID, JOBJECT, jint]
        d.getAccessibleChildFromContext.restype = JOBJECT
        d.getAccessibleParentFromContext.argtypes = [VMID, JOBJECT]
        d.getAccessibleParentFromContext.restype = JOBJECT
        d.releaseJavaObject.argtypes = [VMID, JOBJECT]
        d.releaseJavaObject.restype = None
        d.getAccessibleContextAt.argtypes = [VMID, JOBJECT, jint, jint, P(JOBJECT)]
        d.getAccessibleContextWithFocus.argtypes = [wintypes.HWND, P(VMID), P(JOBJECT)]
        d.getAccessibleTextInfo.argtypes = [VMID, JOBJECT, P(AccessibleTextInfo), jint, jint]
        d.getAccessibleTextRange.argtypes = [
            VMID,
            JOBJECT,
            jint,
            jint,
            ctypes.c_wchar_p,
            ctypes.c_short,
        ]
        d.setTextContents.argtypes = [VMID, JOBJECT, ctypes.c_wchar_p]
        d.doAccessibleActions.argtypes = [VMID, JOBJECT, P(AccessibleActionsToDo), P(jint)]
        d.addAccessibleSelectionFromContext.argtypes = [VMID, JOBJECT, ctypes.c_int]
        d.addAccessibleSelectionFromContext.restype = None
        d.clearAccessibleSelectionFromContext.argtypes = [VMID, JOBJECT]
        d.clearAccessibleSelectionFromContext.restype = None
        d.isSameObject.argtypes = [VMID, JOBJECT, JOBJECT]
        d.requestFocus.argtypes = [VMID, JOBJECT]
        d.getAccessibleSelectionFromContext.argtypes = [VMID, JOBJECT, ctypes.c_int]
        d.getAccessibleSelectionFromContext.restype = JOBJECT

    def submit(self, fn, *args) -> Future:
        fut: Future = Future()
        self.q.put((fn, args, fut))
        return fut

    def call(self, fn, *args, timeout: float = 15.0):
        return self.submit(fn, *args).result(timeout)

    def close(self) -> None:
        self.stopping = True
        self.t.join(3)


_shared: Bridge | None = None
_shared_lock = threading.Lock()


def shared() -> Bridge:
    """The process's one Bridge (the recorder and a script driving the app share it)."""
    global _shared
    with _shared_lock:
        if _shared is None or _shared.stopping:
            _shared = Bridge()
        return _shared


# --- calls that run on the bridge thread (first argument: the Bridge) ------------------------------


def is_java(b: Bridge, hwnd: int) -> bool:
    return bool(b.dll.isJavaWindow(hwnd))


def _root(b: Bridge, hwnd: int) -> tuple[int, int] | None:
    vm, ac = VMID(), JOBJECT()
    if not b.dll.getAccessibleContextFromHWND(hwnd, ctypes.byref(vm), ctypes.byref(ac)):
        return None
    return vm.value, ac.value


def _info(b: Bridge, vm: int, ac: int) -> AccessibleContextInfo | None:
    info = AccessibleContextInfo()
    return info if b.dll.getAccessibleContextInfo(vm, ac, ctypes.byref(info)) else None


def _text(b: Bridge, vm: int, ac: int) -> str | None:
    ti = AccessibleTextInfo()
    if not b.dll.getAccessibleTextInfo(vm, ac, ctypes.byref(ti), 0, 0):
        return None
    n = max(0, min(ti.charCount, 2000))
    if n == 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    if not b.dll.getAccessibleTextRange(vm, ac, 0, n - 1, buf, n + 1):
        return None
    return buf.value


def _scale(hwnd: int, info: AccessibleContextInfo) -> tuple[float, int, int]:
    """Java reports positions in its own (DPI-scaled) units; Windows in physical pixels. The
    factor between them, and the window's physical top-left corner."""
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    width = r.right - r.left
    return (width / info.width if info.width > 0 and width > 0 else 1.0), r.left, r.top


def _node(b: Bridge, vm: int, ac: int, info: AccessibleContextInfo, rid: tuple, origin,
          scale: float = 1.0) -> dict:  # fmt: skip
    role = info.role_en_US or info.role
    states = set((info.states_en_US or "").split(","))
    t = ROLE_TYPE.get(role, "Custom")
    node: dict = {"type": t, "role": role, "name": info.name or None, "fw": "Java",
                  "enabled": "enabled" in states, "visible": "showing" in states,
                  "focusable": "focusable" in states, "rid": rid,
                  "rect": [round((info.x - origin[0]) * scale), round((info.y - origin[1]) * scale),
                           round(info.width * scale), round(info.height * scale)]}  # fmt: skip
    if info.description and info.description != info.name:
        node["help"] = info.description
    pats = []
    if info.accessibleAction:
        pats.append("invoke")
    if t == "CheckBox" or role in ("toggle button", "radio button"):
        node["toggle"] = "on" if ("checked" in states or "pressed" in states) else "off"
        pats.append("toggle")
    if "selectable" in states:
        node["selected"] = "selected" in states
    if t == "Edit":
        node["readonly"] = "editable" not in states
        if role == "password text":
            node["password"] = True
            ti = AccessibleTextInfo()  # its length only, to see that it changed; never the text
            if info.accessibleText and b.dll.getAccessibleTextInfo(vm, ac, ctypes.byref(ti), 0, 0):
                node["_length"] = ti.charCount
        elif info.accessibleText:
            node["value"] = _text(b, vm, ac)
        pats.append("value")
    if t == "ComboBox":
        sel = b.dll.getAccessibleSelectionFromContext(vm, ac, 0) if info.accessibleSelection else 0
        si = _info(b, vm, sel) if sel else None
        node["value"] = (
            si.name if si is not None else None
        )  # the item shown (data: privacy decides)
        if sel:
            b.dll.releaseJavaObject(vm, sel)
    if "expanded" in states or "collapsed" in states:
        node["expand"] = "expanded" if "expanded" in states else "collapsed"
    if pats:
        node["patterns"] = pats
    return node


def capture(b: Bridge, hwnd: int) -> dict | None:
    """The raw accessible tree of a Java window (names and values as shown: pass it to
    privacy.clean at once). Rows beyond MAX_ROWS are left out and counted."""
    from labctl.desktop import win

    root = _root(b, hwnd)
    if root is None:
        return None
    vm, ac = root
    info = _info(b, vm, ac)
    if info is None:
        return None
    origin = (info.x, info.y)
    scale = _scale(hwnd, info)[0]
    count = [0]

    def walk(obj: int, info, rid: tuple, depth: int) -> dict:
        node = _node(b, vm, obj, info, rid, origin, scale)
        count[0] += 1
        n = info.childrenCount
        if node["type"] in ("Table", "List", "Tree") and n > MAX_ROWS * 8:
            node["truncated"] = f"{n} children; first {MAX_ROWS * 8} kept"
            n = MAX_ROWS * 8
        if depth >= MAX_DEPTH or count[0] >= MAX_NODES:
            node["truncated"] = "depth or size limit"
            return node
        kids = []
        for i in range(n):
            child = b.dll.getAccessibleChildFromContext(vm, obj, i)
            if not child:
                continue
            ci = _info(b, vm, child)
            if ci is not None:
                kids.append(walk(child, ci, (*rid, i), depth + 1))
            b.dll.releaseJavaObject(vm, child)
        if kids:
            node["children"] = kids
        return node

    tree = walk(ac, info, (), 0)
    b.dll.releaseJavaObject(vm, ac)
    tree["cls"] = win.class_name(hwnd)
    # A JOptionPane: role "alert" for warnings and errors, "option pane" otherwise.
    box = next((n.get("role") for n, _a, _i in locator.walk(tree)
                if n.get("role") in ("alert", "option pane")), None)  # fmt: skip
    if box:
        tree["message_box"] = box
    return tree


def _path_of(b: Bridge, vm: int, ac: int, root_ac: int) -> tuple | None:
    """The index path of a context from its window's root, by its parents."""
    path, cur, steps = [], ac, 0
    while steps < 60:
        if b.dll.isSameObject(vm, cur, root_ac):
            return tuple(reversed(path))
        info = _info(b, vm, cur)
        if info is None:
            return None
        path.append(info.indexInParent)
        parent = b.dll.getAccessibleParentFromContext(vm, cur)
        if not parent:
            return None
        cur, steps = parent, steps + 1
    return None


def describe_at(b: Bridge, hwnd: int, x: int, y: int) -> dict | None:
    """The control at a screen point of a Java window: its node (raw, in memory) and its path."""
    root = _root(b, hwnd)
    if root is None:
        return None
    vm, rac = root
    rinfo = _info(b, vm, rac)
    if rinfo is None:
        return None
    scale, left, top = _scale(hwnd, rinfo)
    jx, jy = rinfo.x + round((x - left) / scale), rinfo.y + round((y - top) / scale)
    ac = JOBJECT()
    if not b.dll.getAccessibleContextAt(vm, rac, jx, jy, ctypes.byref(ac)) or not ac.value:
        return None
    info = _info(b, vm, ac.value)
    if info is None:
        return None
    rid = _path_of(b, vm, ac.value, rac)
    node = _node(b, vm, ac.value, info, rid or (), (0, 0))
    node["window"] = hwnd
    node.pop("rect", None)
    node["ancestor_types"] = _ancestor_types(b, vm, ac.value, rac)
    return node


def _ancestor_types(b: Bridge, vm: int, ac: int, root_ac: int) -> list[str]:
    out, cur = [], ac
    for _ in range(40):
        parent = b.dll.getAccessibleParentFromContext(vm, cur)
        if not parent or b.dll.isSameObject(vm, parent, root_ac):
            break
        info = _info(b, vm, parent)
        if info is None:
            break
        out.append(ROLE_TYPE.get(info.role_en_US, "Custom"))
        cur = parent
    return out


def focused(b: Bridge, hwnd: int) -> dict | None:
    """The focused control of a Java window: type, path and (raw) value."""
    vm, ac = VMID(), JOBJECT()
    if (
        not b.dll.getAccessibleContextWithFocus(hwnd, ctypes.byref(vm), ctypes.byref(ac))
        or not ac.value
    ):
        return None
    root = _root(b, hwnd)
    info = _info(b, vm.value, ac.value)
    if info is None or root is None:
        return None
    node = _node(b, vm.value, ac.value, info, (), (0, 0))
    node["rid"] = _path_of(b, vm.value, ac.value, root[1])
    node["window"] = hwnd
    return node


def _resolve(b: Bridge, hwnd: int, rid: tuple) -> tuple[int, int] | None:
    root = _root(b, hwnd)
    if root is None:
        return None
    vm, ac = root
    for i in rid:
        ac = b.dll.getAccessibleChildFromContext(vm, ac, i)
        if not ac:
            return None
    return vm, ac


def act(b: Bridge, hwnd: int, rid: tuple, action: str = "click") -> bool:
    """Do an accessible action (`click`, `toggle`...) on the control at a path."""
    found = _resolve(b, hwnd, rid)
    if found is None:
        return False
    vm, ac = found
    todo = AccessibleActionsToDo()
    todo.actionsCount = 1
    todo.actions[0].name = action
    fail = jint(0)
    return bool(b.dll.doAccessibleActions(vm, ac, ctypes.byref(todo), ctypes.byref(fail)))


def set_text(b: Bridge, hwnd: int, rid: tuple, text: str) -> bool:
    found = _resolve(b, hwnd, rid)
    if found is None:
        return False
    vm, ac = found
    b.dll.requestFocus(vm, ac)
    return bool(b.dll.setTextContents(vm, ac, text))


def select_index(b: Bridge, hwnd: int, rid: tuple, index: int) -> bool:
    """Select the index-th item of a combo box or list (AccessibleSelection)."""
    found = _resolve(b, hwnd, rid)
    if found is None:
        return False
    vm, ac = found
    b.dll.clearAccessibleSelectionFromContext(vm, ac)
    b.dll.addAccessibleSelectionFromContext(vm, ac, index)
    return True


def focus(b: Bridge, hwnd: int, rid: tuple) -> bool:
    found = _resolve(b, hwnd, rid)
    return bool(found and b.dll.requestFocus(*found))
