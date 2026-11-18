"""`labctl certify`: the certification matrix (docs/10-certification.md).

One entry per technology (and version), each on a small real reference application, each put
through the same four gates:

1. `detect`: the technology and version the lab detects from the recording equal what the entry
   declares;
2. `capture`: a scripted flow is recorded with the real recorder, and the expected actions the
   entry declares by hand are all in the recording;
3. `self-replay`: the recording replays against a fresh instance of the same application with no
   failed step;
4. `rebuild`: a coding agent rebuilt the application from the package and its replay ended
   VERIFIED COMPLETE. This gate is never run here: it reads a kept replay report when the entry
   names one.

The result of an entry is CERTIFIED (all four passed), CAPTURE VERIFIED (1-3 passed, rebuild not
run), PARTIAL (a gate failed, or only some ran) or NOT RUN. A gate that did not run is NOT RUN
with its reason and never counts as passed.

The matrix is `certification/matrix.yaml`. A run writes one JSON result per entry to
`certification/results/` and regenerates `docs/certification-matrix.md` from the results kept
there. Recordings go to a temporary folder and are not kept.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import typer
import yaml
from pydantic import BaseModel, ValidationError, field_validator, model_validator

GATES = ("detect", "capture", "self-replay", "rebuild")
PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT RUN"
CERTIFIED, CAPTURE_VERIFIED, PARTIAL = "CERTIFIED", "CAPTURE VERIFIED", "PARTIAL"
MATRIX = Path("certification") / "matrix.yaml"
RESULTS = Path("certification") / "results"
DOC = Path("docs") / "certification-matrix.md"
DESKTOP_ENV = "TWINLAB_DESKTOP_GUI"
DESKTOP_KINDS = ("winforms", "wpf", "swing")
NOT_DECLARED = "the entry declares no application and no flow yet"
DESKTOP_IMPLIED = "covered by the steps around it"  # desktop verify's note on such a step


class MatrixError(ValueError):
    """The matrix file is wrong; the message says where."""


# --- the matrix ------------------------------------------------------------------------------------


class AppSpec(BaseModel, extra="forbid"):
    """The reference application. `static`: a folder of files served as they are. `python`: a
    module with a function returning running servers (attributes named by `origins`, and
    `stop()`). `module`: the application's own starter, a file with a function returning one
    running instance (`.base`, its address, and `.stop()`). `winforms`, `wpf`, `swing`: a folder
    with `flow.py` exposing `build()` and `flow`."""

    kind: Literal["static", "python", "module", "winforms", "wpf", "swing"]
    folder: str | None = None
    file: str | None = None
    serve: str = "serve"
    origins: list[str] = []
    start: str = "/"
    requires: list[str] = []  # files under `folder` that must exist (built, not in Git)
    requires_reason: str | None = None
    # The application's files (and the flow file) load as `<namespace>_<stem>`, the names the
    # application's own modules give their neighbours, so each is loaded once.
    namespace: str | None = None
    # `path/to/file.py:function`: returns why the application cannot start here, or None.
    unavailable: str | None = None

    @model_validator(mode="after")
    def _complete(self):
        if self.unavailable is not None and ":" not in self.unavailable:
            raise ValueError("`unavailable` is `path/to/file.py:function`")
        if self.kind == "python":
            if not self.file or not self.origins:
                raise ValueError("a python app needs `file` and `origins`")
        elif self.kind == "module":
            if not self.file:
                raise ValueError("a module app needs `file`")
        elif not self.folder:
            raise ValueError(f"a {self.kind} app needs `folder`")
        return self


class FlowSpec(BaseModel, extra="forbid"):
    file: str
    run: str = "run"
    base_url: bool = False  # the function is `run(page, base_url)`, not `run(page)`


class Entry(BaseModel, extra="forbid"):
    id: str
    technology: str
    version: str | None = None
    surface: Literal["web", "desktop"]
    app: AppSpec | None = None
    flow: FlowSpec | None = None
    gates: dict[str, dict[str, Any]] = {}
    not_run: str | None = None  # why the entry cannot run yet (declared for later)
    not_covered: list[str] = []

    @field_validator("version", mode="before")
    @classmethod
    def _text(cls, v):
        return None if v is None else str(v)

    @field_validator("gates", mode="before")
    @classmethod
    def _gates(cls, v):
        v = v or {}
        if not isinstance(v, dict):
            raise ValueError("gates is a mapping of gate name to its settings")
        unknown = [g for g in v if g not in GATES]
        if unknown:
            raise ValueError(f"unknown gate {unknown[0]!r} (gates: {', '.join(GATES)})")
        return {g: (s or {}) for g, s in v.items()}

    @model_validator(mode="after")
    def _fits(self):
        if not self.gates.get("detect"):
            raise ValueError("gates.detect is missing: what the lab must detect")
        if self.app is not None and (self.app.kind in DESKTOP_KINDS) != (self.surface == "desktop"):
            raise ValueError(f"app kind {self.app.kind!r} does not fit surface {self.surface!r}")
        if self.surface == "web" and self.app is not None and self.flow is None:
            raise ValueError("a web entry with an app needs a flow")
        return self


class Matrix(BaseModel, extra="forbid"):
    version: int = 1
    entries: list[Entry]

    def entry(self, entry_id: str) -> Entry:
        for e in self.entries:
            if e.id == entry_id:
                return e
        raise MatrixError(
            f"no entry {entry_id!r} (entries: {', '.join(x.id for x in self.entries)})"
        )


def parse_matrix(data: Any, where: str = "matrix") -> Matrix:
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise MatrixError(f"{where}: needs `entries`, a list")
    try:
        matrix = Matrix.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = list(err["loc"])
        at = ""
        if len(loc) > 1 and loc[0] == "entries" and isinstance(loc[1], int):
            raw = data["entries"][loc[1]]
            name = raw.get("id") if isinstance(raw, dict) else None
            at = f" entry {name or loc[1] + 1}:"
        field = ".".join(str(x) for x in loc[2:]) if at else ".".join(str(x) for x in loc)
        msg = err["msg"].removeprefix("Value error, ")
        raise MatrixError(f"{where}:{at} {field + ': ' if field else ''}{msg}") from exc
    seen: set[str] = set()
    for e in matrix.entries:
        if e.id in seen:
            raise MatrixError(f"{where}: duplicate id {e.id!r}")
        seen.add(e.id)
    return matrix


def load_matrix(path: Path) -> Matrix:
    path = Path(path)
    if not path.is_file():
        raise MatrixError(f"{path}: no such file")
    return parse_matrix(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))


# --- results ---------------------------------------------------------------------------------------


class Gate(BaseModel):
    status: Literal["PASS", "FAIL", "NOT RUN"] = NOT_RUN
    detail: str = ""
    facts: dict[str, Any] = {}  # the numbers behind it


class EntryResult(BaseModel):
    id: str
    technology: str
    version: str | None = None
    surface: str
    result: str
    reason: str = ""
    date: str
    gates: dict[str, Gate]
    not_covered: list[str] = []


def decide(gates: dict[str, Gate]) -> tuple[str, str]:
    """The entry's result from its four gates, and the sentence that goes with it. A gate that
    did not run is never a pass: with nothing failed, only detect, capture and self-replay all
    passed gives CAPTURE VERIFIED, and only all four gives CERTIFIED."""
    got = {g: gates.get(g) or Gate(detail="no result") for g in GATES}
    failed = [g for g in GATES if got[g].status == FAIL]
    not_run = [g for g in GATES if got[g].status == NOT_RUN]
    if failed:
        why = "; ".join(f"{g} failed: {got[g].detail}" for g in failed)
        return PARTIAL, why
    if not not_run:
        return CERTIFIED, "all four gates passed"
    if not_run == ["rebuild"]:
        return CAPTURE_VERIFIED, f"rebuild not run: {got['rebuild'].detail}"
    if all(got[g].status == NOT_RUN for g in GATES[:3]) and got["rebuild"].status != PASS:
        return NOT_RUN, got[GATES[0]].detail
    why = "; ".join(f"{g} not run: {got[g].detail}" for g in not_run)
    return PARTIAL, why


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _result(entry: Entry, gates: dict[str, Gate]) -> EntryResult:
    result, reason = decide(gates)
    return EntryResult(id=entry.id, technology=entry.technology, version=entry.version,
                       surface=entry.surface, result=result, reason=reason, date=_today(),
                       gates={g: gates[g] for g in GATES}, not_covered=entry.not_covered)  # fmt: skip


def _all_not_run(reason: str) -> dict[str, Gate]:
    return {g: Gate(detail=reason) for g in GATES}


def _same_version(declared: str | None, detected: str | None) -> bool:
    """Equal, or the detected version is a finer one of the declared (`6.0` and `6.0.36`)."""
    if declared is None or detected is None:
        return declared is None and detected is None
    return detected == declared or detected.startswith(declared + ".")


def detect_gate(declared: dict, detected: dict | None) -> Gate:
    """Every key the entry declares must be what was detected. `version` is always compared: an
    entry that declares none passes only when none was detected. A nested mapping (`runtime`)
    is compared key by key."""
    detected = detected or {}
    want = {"version": None, **declared}
    wrong: list[str] = []
    seen: list[str] = []

    def check(key: str, exp, got) -> None:
        if isinstance(exp, dict):
            for k, v in exp.items():
                check(f"{key}.{k}", v, (got or {}).get(k) if isinstance(got, dict) else None)
            return
        exp_s = None if exp is None else str(exp)
        got_s = None if got is None else str(got)
        same = _same_version(exp_s, got_s) if key.endswith("version") else exp_s == got_s
        if not same:
            wrong.append(f"{key}: declared {exp_s or 'none'}, detected {got_s or 'none'}")
        elif got_s is not None:
            seen.append(f"{key} {got_s}")

    for key, exp in want.items():
        check(key, exp, detected.get(key))
    facts = {"declared": declared, "detected": {k: detected.get(k) for k in want}}
    if detected.get("marker"):
        facts["marker"] = detected["marker"]
    if wrong:
        return Gate(status=FAIL, detail="; ".join(wrong), facts=facts)
    return Gate(status=PASS, detail=f"detected {', '.join(seen)}", facts=facts)


def capture_gate(expected: list[str], captured: list[str]) -> Gate:
    from labctl.behaviour import fidelity

    if not expected:
        return Gate(detail="the entry declares no expected actions")
    missing = fidelity.missing_actions(expected, captured)
    facts = {"expected": len(expected), "captured": len(captured), "missing": missing}
    if missing:
        return Gate(status=FAIL, facts=facts,
                    detail=f"{len(missing)} of {len(expected)} expected action(s) not in the "
                    f"recording: {', '.join(missing[:8])}")  # fmt: skip
    return Gate(status=PASS, facts=facts,
                detail=f"{len(expected)} expected action(s), all in the recording "
                f"({len(captured)} action(s) recorded)")  # fmt: skip


def rebuild_gate(settings: dict, root: Path) -> Gate:
    """Read a kept replay report (`gates.rebuild.report`, a path from the repository root). It
    passes only when that replay passed with assurance VERIFIED COMPLETE."""
    from labctl.behaviour import judging

    name = settings.get("report")
    if not name:
        return Gate(detail="no rebuild replay report is named in the entry")
    path = Path(root) / str(name)
    if not path.is_file():
        return Gate(detail=f"the rebuild replay report {name} is not there")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        status, assurance = data.get("status"), data.get("assurance")
    except (ValueError, AttributeError):
        return Gate(status=FAIL, detail=f"{name} is not a replay report")
    facts = {"report": str(name), "status": status, "verdict": data.get("verdict"),
             "assurance": assurance}  # fmt: skip
    if status == "pass" and assurance == judging.VERIFIED:
        return Gate(status=PASS, detail=f"{name}: {judging.VERIFIED}", facts=facts)
    return Gate(status=FAIL, facts=facts,
                detail=f"{name}: status {status}, assurance {assurance or 'none'}")  # fmt: skip


# --- starting a reference application --------------------------------------------------------------


def _module(path: Path, namespace: str | None = None):
    """Load a Python file as a module of its own (a flow, a fixture's servers). Its folder is
    importable while it loads, so a fixture can import its neighbours. With a `namespace` the
    module is named `<namespace>_<stem>`, as a reference application names its own files."""
    path = Path(path).resolve()
    name = f"{namespace}_{path.stem}" if namespace else f"certify_{path.parent.name}_{path.stem}"
    loaded = sys.modules.get(name)
    if loaded is not None and Path(getattr(loaded, "__file__", "")).resolve() == path:
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look their module up while it loads
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        sys.path.remove(str(path.parent))
    return mod


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Running:
    """A started reference application: its origins by name, the address to record from."""

    def __init__(self, origins: dict[str, str], start: str, stop):
        self.origins, self.start, self._stop = origins, start, stop

    def stop(self) -> None:
        self._stop()


def start_app(app: AppSpec, root: Path) -> Running:
    """A fresh instance on free ports of 127.0.0.1."""
    if app.kind == "static":
        handler = partial(_Quiet, directory=str(Path(root) / app.folder))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_address[1]}"

        def stop() -> None:
            server.shutdown()
            server.server_close()

        return Running({"app": origin}, origin + app.start, stop)
    if app.kind == "python":
        run = getattr(_module(Path(root) / app.file), app.serve)()
        origins = {name: getattr(run, name) for name in app.origins}
        return Running(origins, app.start.format(**origins), run.stop)
    if app.kind == "module":
        run = getattr(_module(Path(root) / app.file, app.namespace), app.serve)()
        base = str(run.base).rstrip("/")
        return Running({"app": base}, base + app.start, run.stop)
    raise ValueError(f"{app.kind} is not a web application")


def missing_requirements(app: AppSpec, root: Path) -> str | None:
    """Why the application cannot start here, or None."""
    if app.file and not (Path(root) / app.file).is_file():
        return f"{app.file} is not there"
    if app.folder:
        folder = Path(root) / app.folder
        if not folder.is_dir():
            return "reference app not present"
        gone = [r for r in app.requires if not (folder / r).is_file()]
        if gone:
            why = f" ({app.requires_reason})" if app.requires_reason else ""
            return f"{', '.join(gone)} not in {app.folder}{why}"
    if app.unavailable:
        name, _, function = app.unavailable.rpartition(":")
        if not (Path(root) / name).is_file():
            return f"{name} is not there"
        why = getattr(_module(Path(root) / name, app.namespace), function)()
        return str(why) if why else None
    return None


# --- web entries -----------------------------------------------------------------------------------


def expected_actions(settings: dict, flow_mod, root: Path | None = None) -> list[str]:
    """The expected actions: `expected`, a list in the entry; `expected_from`, a list the flow
    file declares by hand (an item may be a tuple whose first part is the action); or
    `expected_file`, a YAML file kept beside the application whose `actions` is that list."""
    items = settings.get("expected")
    if items is None and settings.get("expected_from"):
        items = getattr(flow_mod, str(settings["expected_from"]))
    if items is None and settings.get("expected_file"):
        path = Path(root or ".") / str(settings["expected_file"])
        items = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("actions")
    return [str(i if isinstance(i, str) else i[0]) for i in items or []]


def bindings_from_flow(rec_dir: Path, make) -> None:
    """bindings.yaml as the flow file gives it: `make` is called with the numbers of the
    recording's `type` and `select` steps, in order, and returns the bindings document. A flow
    that does not fit the recording raises, and the replay is not started."""
    from labctl.behaviour import bindings as bnd

    lines = (Path(rec_dir) / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    steps = [json.loads(line) for line in lines if line.strip()]
    valued = [s["seq"] for s in steps if s.get("action") in bnd.VALUED]
    text = yaml.safe_dump(make(valued), sort_keys=False)
    (Path(rec_dir) / bnd.FILE).write_text(text, encoding="utf-8")


def fill_bindings(rec_dir: Path, values: dict[str, list], records: list | None = None) -> list[str]:
    """Give each typed or selected field its synthetic value, in the order the steps come: the
    longest key of `values` that is part of the field's selector, its next value. A step on a
    record that was there before the recording started gets the next title of `records`.
    Returns the fields no key matched (the replay then blocks there and says so)."""
    from labctl.behaviour import bindings as bnd
    from labctl.botlog.recording import load

    path = bnd.write_template(load(rec_dir))
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    used: dict[str, int] = {}
    unbound: list[str] = []
    titles = [str(t) for t in records or []]
    for entry in (doc.get("steps") or {}).values():
        if "record" in entry:
            if titles:
                entry["record"] = titles.pop(0)
            else:
                unbound.append(f"{entry.get('field') or ''} (a record)")
        if "value" not in entry:
            continue
        field = entry.get("field") or ""
        keys = [k for k in values if k in field]
        if not keys:
            unbound.append(field)
            continue
        key = max(keys, key=len)
        given = values[key] if isinstance(values[key], list) else [values[key]]
        n = used[key] = used.get(key, -1) + 1
        if n < len(given):
            entry["value"] = str(given[n])
        else:
            unbound.append(field)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return unbound


def detect_web(entry_id: str, rec_dir: Path, work: Path) -> dict:
    """What the lab detects from a web recording: the front end (`stack.frontend`, as the brief
    and app.yaml get it), with the server technology under `server`."""
    from labctl import model_build

    model = model_build.from_recording(entry_id, rec_dir, work, None)
    found = dict(model.stack.frontend or {})
    if model.stack.server:
        found["server"] = dict(model.stack.server)
    return found


def _with_base(script, base_url: str, page) -> None:
    script(page, base_url)


def _short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0] if text else ''}"[:300]


def run_web(entry: Entry, root: Path, work: Path) -> dict[str, Gate]:
    from labctl.behaviour import fidelity
    from labctl.botlog.recording import load

    try:
        from labctl import recorder

        importlib.import_module("playwright.sync_api")
    except ImportError:
        return _all_not_run("Playwright is not installed")
    gates = _all_not_run("the flow was not recorded")
    gates["rebuild"] = rebuild_gate(entry.gates.get("rebuild") or {}, root)
    flow_mod = _module(Path(root) / entry.flow.file, entry.app.namespace)
    expected = expected_actions(entry.gates.get("capture") or {}, flow_mod, root)
    rec_dir = work / entry.id
    source = start_app(entry.app, root)
    script = getattr(flow_mod, entry.flow.run)
    if entry.flow.base_url:
        script = partial(_with_base, script, next(iter(source.origins.values())))
    try:
        try:
            recorder.record(source.start, rec_dir, headless=True, script=script)
        except Exception as exc:  # noqa: BLE001 - whatever stopped the flow is the result
            gates["capture"] = Gate(status=FAIL, detail=f"the flow did not finish: {_short(exc)}")
            why = "the recording is not complete"
            gates["detect"], gates["self-replay"] = Gate(detail=why), Gate(detail=why)
            return gates
    finally:
        source.stop()

    rec = load(rec_dir)
    gates["detect"] = detect_gate(entry.gates["detect"], detect_web(entry.id, rec_dir, work))
    gates["capture"] = capture_gate(expected, fidelity.captured_actions(rec))
    gates["capture"].facts["steps"] = len(rec.steps)

    settings = entry.gates.get("self-replay") or {}
    try:
        if settings.get("bindings_from"):
            unbound: list[str] = []
            bindings_from_flow(rec_dir, getattr(flow_mod, str(settings["bindings_from"])))
        else:
            unbound = fill_bindings(rec_dir, settings.get("values") or {}, settings.get("records"))
    except Exception as exc:  # noqa: BLE001 - a flow whose values do not fit the recording
        gates["self-replay"] = Gate(status=FAIL, detail=f"no bindings: {_short(exc)}")
        return gates
    fresh = start_app(entry.app, root)
    try:
        origins = {source.origins[k]: fresh.origins[k] for k in source.origins}
        report = fidelity.replay(rec_dir, origins=origins, resolve_timeout_ms=4000,
                                 acceptance=False, visual=False, scope=False)  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        gates["self-replay"] = Gate(status=FAIL, detail=f"the replay stopped: {_short(exc)}")
        return gates
    finally:
        fresh.stop()
    gates["self-replay"] = replay_gate(report, unbound)
    return gates


def replay_gate(report, unbound: list[str] | None = None) -> Gate:
    """Passes when the replay's own status is pass: steps ran, none failed, blocked or skipped."""
    c = report.coverage
    facts = {"status": report.status, "steps": c.steps, "replayed": c.replayed,
             "compared": c.compared, "failed": c.failed, "skipped": c.skipped,
             "unresolved": c.unresolved, "missing_value": c.missing_value,
             "differences": sum(len(s.differences) for s in report.steps)}  # fmt: skip
    if report.status == "pass" and c.failed == 0 and c.replayed > 0:
        return Gate(status=PASS, facts=facts,
                    detail=f"{c.replayed} of {c.steps} step(s) replayed against a fresh instance, "
                    f"{c.compared} compared, 0 failed")  # fmt: skip
    first = next((s for s in report.steps if s.status in ("fail", "blocked")), None)
    where = ""
    if first is not None:
        diff = first.differences[0].text() if first.differences else (first.note or "")
        where = f"; first: step {first.seq} ({first.action}) {diff}"[:400]
    if unbound:
        facts["no_value_for"] = unbound
    return Gate(status=FAIL, facts=facts,
                detail=f"replay {report.status}: {c.failed} failed, {c.skipped} not run, "
                f"{c.unresolved} unresolved, {c.missing_value} without a value, of {c.steps} "
                f"step(s){where}")  # fmt: skip


# --- desktop entries -------------------------------------------------------------------------------


def desktop_actions(steps: list[dict]) -> list[str]:
    """Desktop steps as semantic actions, a key press as `key:Enter`."""
    return [f"key:{s.get('key') or '?'}" if s.get("action") == "key" else str(s.get("action"))
            for s in steps]  # fmt: skip


def desktop_blocked(entry: Entry, root: Path) -> str | None:
    """Why a desktop entry does not run here, or None. A desktop run starts a GUI application
    and clicks with the real mouse, so it runs only when asked for."""
    folder = Path(root) / entry.app.folder
    if not (folder / "flow.py").is_file():
        return "reference app not present"
    if os.environ.get(DESKTOP_ENV) != "1":
        return f"set {DESKTOP_ENV}=1"
    if sys.platform != "win32":
        return "needs Windows (UI Automation)"
    if importlib.util.find_spec("comtypes") is None:
        return "the desktop extra (comtypes) is not installed"
    if entry.app.kind in ("winforms", "wpf") and shutil.which("dotnet") is None:
        return "the .NET SDK is not installed"
    return None


def run_desktop(entry: Entry, root: Path, work: Path) -> dict[str, Gate]:
    """Build the reference application, record its scripted flow (`flow.py`: `build()` and
    `flow`), read what was detected, and verify the recording against a fresh start of the same
    build. The calls are the ones tests/test_desktop_gui.py makes."""
    why = desktop_blocked(entry, root)
    if why:
        return {**_all_not_run(why),
                "rebuild": rebuild_gate(entry.gates.get("rebuild") or {}, root)}  # fmt: skip
    flow = _module(Path(root) / entry.app.folder / "flow.py")
    java = entry.app.kind == "swing"
    if java and not (flow.JAVA_HOME / "bin" / "javac.exe").is_file():
        return _all_not_run(f"no JDK at {flow.JAVA_HOME} (set TWINLAB_JAVA_HOME)")
    gates = _all_not_run("the flow was not recorded")
    gates["rebuild"] = rebuild_gate(entry.gates.get("rebuild") or {}, root)
    out = work / entry.id
    values = (entry.gates.get("self-replay") or {}).get("values") or {}
    try:
        target = flow.build()
        if java:
            from labctl.desktop import java as java_mod

            java_mod.record(target, out, script=flow.flow, max_seconds=300)
        else:
            from labctl.desktop import recorder

            recorder.record(str(target), out, script=flow.flow, max_seconds=240)
    except Exception as exc:  # noqa: BLE001
        gates["capture"] = Gate(status=FAIL, detail=f"the flow did not finish: {_short(exc)}")
        return gates
    session = json.loads((out / "desktop.json").read_text(encoding="utf-8"))
    steps = [json.loads(line) for line in
             (out / "steps.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]  # fmt: skip
    processes = session.get("processes") or [{}]
    gates["detect"] = detect_gate(entry.gates["detect"], processes[0].get("technology"))
    expected = [str(a) for a in (entry.gates.get("capture") or {}).get("expected") or []]
    gates["capture"] = capture_gate(expected, desktop_actions(steps))
    gates["capture"].facts["steps"] = len(steps)
    try:
        if java:
            report = java_mod.verify(target, out, values=values)
        else:
            from labctl.desktop import verify

            report = verify.verify(str(target), out, values=values)
    except Exception as exc:  # noqa: BLE001
        gates["self-replay"] = Gate(status=FAIL, detail=f"the replay stopped: {_short(exc)}")
        return gates
    gates["self-replay"] = desktop_replay_gate(report)
    return gates


def desktop_replay_gate(report) -> Gate:
    """Passes when every recorded step was performed and compared against the fresh start, or
    is one the steps around it cover (a focus change, a menu or a list opening: verify marks
    those `skipped` with that note). A step verify could not perform (no value for a password
    field, an item that holds data) is `skipped` too and is never a pass."""
    failed = [s for s in report.steps if s.status == "fail"]
    ran = [s for s in report.steps if s.status == "pass"]
    implied = [s for s in report.steps if s.status == "skipped" and s.note == DESKTOP_IMPLIED]
    left = [s for s in report.steps if s not in failed and s not in ran and s not in implied]
    facts = {"status": report.status, "steps": len(report.steps), "replayed": len(ran),
             "implied": len(implied), "failed": len(failed), "skipped": len(left)}  # fmt: skip
    if report.status == "pass" and ran and not failed and not left:
        return Gate(status=PASS, facts=facts,
                    detail=f"{len(ran)} of {len(report.steps)} step(s) verified against a fresh "
                    f"start, {len(implied)} covered by the steps around them, 0 failed")  # fmt: skip
    first = next(iter([*failed, *left]), None)
    where = ""
    if first is not None:
        diff = first.differences[0].text() if first.differences else (first.note or "")
        where = f"; first: step {first.seq} ({first.action}) {diff}"[:400]
    return Gate(status=FAIL, facts=facts,
                detail=f"verify {report.status}: {len(failed)} failed, {len(left)} not performed, "
                f"of {len(report.steps)} step(s){where}")  # fmt: skip


# --- running the matrix ----------------------------------------------------------------------------


def run_entry(entry: Entry, root: Path, work: Path) -> EntryResult:
    root, work = Path(root), Path(work)
    if entry.not_run or entry.app is None:
        gates = _all_not_run(entry.not_run or NOT_DECLARED)
    elif entry.surface == "desktop":
        gates = run_desktop(entry, root, work)
    else:
        why = missing_requirements(entry.app, root)
        if why is None and not (root / entry.flow.file).is_file():
            why = f"{entry.flow.file} is not there"
        gates = _all_not_run(why) if why else run_web(entry, root, work)
    return _result(entry, gates)


def write_result(result: EntryResult, out: Path) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.id}.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def read_results(matrix: Matrix, out: Path) -> list[EntryResult]:
    """The kept result of every entry, in the matrix's order. An entry with no result file is
    NOT RUN: it was never run."""
    results = []
    for entry in matrix.entries:
        path = Path(out) / f"{entry.id}.json"
        if path.is_file():
            results.append(EntryResult.model_validate_json(path.read_text(encoding="utf-8")))
        else:
            got = _result(entry, _all_not_run("never run"))
            got.date = ""
            results.append(got)
    return results


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def markdown(results: list[EntryResult]) -> str:
    lines = [
        "# Certification matrix",
        "",
        "Written by `labctl certify`. Do not edit by hand. The gates and how to add a technology",
        "are in [10-certification.md](10-certification.md).",
        "",
        "A gate is PASS, FAIL or NOT RUN. NOT RUN is never a pass. CERTIFIED needs all four gates.",
        "CAPTURE VERIFIED means detect, capture and self-replay passed and no rebuild was judged.",
        "",
        "| Technology | Version | Surface | Detect | Capture | Self-replay | Rebuild | Result "
        "| Date | Not covered |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        cells = [r.technology, r.version or "-", r.surface,
                 *(r.gates[g].status if g in r.gates else NOT_RUN for g in GATES),
                 f"**{r.result}**", r.date or "-", "; ".join(r.not_covered) or "-"]  # fmt: skip
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    lines += ["", "## Details", ""]
    for r in results:
        lines += [f"### {r.technology}{' ' + r.version if r.version else ''} (`{r.id}`)", ""]
        lines += [f"Result: **{r.result}**. {_cell(r.reason)}", ""]
        for g in GATES:
            gate = r.gates.get(g) or Gate()
            if r.result == NOT_RUN and gate.detail == r.reason:
                continue  # said once, above
            lines.append(f"- {g}: {gate.status}. {_cell(gate.detail)}")
        if lines[-1]:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run(root: Path, *, only: list[str] | None = None, out: Path | None = None,
        doc: Path | None = None, matrix: Path | None = None, keep: Path | None = None,
        echo=None) -> list[EntryResult]:  # fmt: skip
    """Run the matrix (or the entries in `only`), write each result, regenerate the document from
    every kept result. Returns the results of the entries that were run. Recordings go to a
    temporary folder, or stay in `keep` (one folder per entry) to be looked at."""
    root = Path(root)
    mx = load_matrix(Path(matrix) if matrix else root / MATRIX)
    out = Path(out) if out else root / RESULTS
    doc = Path(doc) if doc else root / DOC
    chosen = [mx.entry(i) for i in only] if only else mx.entries
    done = []
    with tempfile.TemporaryDirectory(prefix="twinlab-certify-", ignore_cleanup_errors=True) as tmp:
        work = Path(keep) if keep else Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        for entry in chosen:
            if (work / entry.id).exists():
                shutil.rmtree(work / entry.id)  # never a recording on top of an older one
            result = run_entry(entry, root, work)
            write_result(result, out)
            done.append(result)
            if echo:
                echo(
                    f"{entry.id}: {result.result}"
                    + (f" ({result.reason})" if result.reason else "")
                )
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(markdown(read_results(mx, out)), encoding="utf-8", newline="\n")
    return done


# --- command (wired into labctl by labctl.cli) -----------------------------------------------------


def certify(
    only: list[str] = typer.Option([], "--only", help="run this entry only (repeat for more)"),
    out: Path | None = typer.Option(None, "--out", help="folder for the result JSON files "
                                    "(default: certification/results)"),
    doc: Path | None = typer.Option(None, "--doc", help="the Markdown table to regenerate "
                                    "(default: docs/certification-matrix.md)"),
    matrix: Path | None = typer.Option(None, "--matrix", help="the matrix file "
                                       "(default: certification/matrix.yaml)"),
    keep: Path | None = typer.Option(None, "--keep", help="keep the recordings in this folder "
                                     "(default: a temporary folder, removed)"),
) -> None:  # fmt: skip
    """Run the certification gates for each technology in the matrix and write the results.
    Exit 1 when a gate that ran failed. A gate that did not run is reported as NOT RUN."""
    from labctl import repo

    try:
        root = repo.find_root()
        results = run(
            root, only=list(only), out=out, doc=doc, matrix=matrix, keep=keep, echo=typer.echo
        )
    except (MatrixError, repo.NotARepo) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    failed = [r.id for r in results if any(g.status == FAIL for g in r.gates.values())]
    if failed:
        typer.echo(f"failed gate(s) in: {', '.join(failed)}", err=True)
        raise typer.Exit(1)
