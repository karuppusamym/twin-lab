"""Harness bot (Prod Echo I-01's fake bot): run an automation's web steps against a running app.

Reads the web `touches` of an `automation.yaml` (the screens, in order, and the elements each one
uses), resolves values the way bindings do (`{{app.web}}`, `{{app.credentials.x}}`, then the run's
inputs), drives Chromium with Playwright, and writes:

- a UiPath-Verbose-shaped JSONL robot log (an Executing and a Closed line per activity, typed
  argument values, "execution started/ended" around each job; one field is Private and is logged
  without arguments, as UiPath does), and
- a recording per run in the agreed format: `<record-dir>/<recording-id>/recording.har`,
  `dom/<seq>.html` (outerHTML after each step, live values written into the markup) and
  `steps.jsonl`.

Synthetic data only. The HAR is written without response bodies and is scrubbed of credential
headers, cookies and password-like body fields before it is kept.

    python tools/harness_bot.py automations/uipath-case-update \\
        --bindings bindings.json --inputs inputs.json \\
        --log out/robot.log.jsonl --record evidence/showcase

`bindings.json` has the shape of specs/bindings.example.json. `inputs.json` is one object of
inputs or a list of them (one run each). Inputs are looked up by the automation's variable name
(`in_CaseNo`), then its snake-case form without the direction prefix (`case_no`); an element used
without a variable (a literal in the automation) takes its value from an input keyed by its
selector (`"select#caseStatus": "ESCALATED"`).
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
try:
    import labctl  # noqa: F401
except ImportError:  # running from a checkout without the package installed
    sys.path.insert(0, str(ROOT / "lab" / "labctl"))

import yaml  # noqa: E402

from labctl.behaviour.dom import SYNC_JS  # noqa: E402

ACTIVITY = {
    "open": "UiPath.Core.Activities.OpenBrowser",
    "goto": "UiPath.Core.Activities.NavigateTo",
    "type": "UiPath.Core.Activities.TypeInto",
    "click": "UiPath.Core.Activities.Click",
    "select": "UiPath.Core.Activities.SelectItem",
    "read": "UiPath.Core.Activities.GetText",
    "wait": "UiPath.Core.Activities.UiElementExists",
    "hover": "UiPath.Core.Activities.Hover",
}
VERB = {
    "type": "Type into",
    "click": "Click",
    "select": "Select item in",
    "read": "Get text from",
    "wait": "Element exists",
    "hover": "Hover over",
}
TEMPLATE = re.compile(r"\{\{\s*([a-z0-9][a-z0-9-]*)\.(web|api|db|credentials\.[\w-]+)\s*\}\}")
SECRET_KEYS = re.compile(r"pass|secret|token|pwd", re.I)
CREDENTIAL_HEADERS = {"authorization", "cookie", "set-cookie", "proxy-authorization", "x-lab-token"}


class BotFault(Exception):
    pass


# --- values ------------------------------------------------------------------------------------


def snake(name: str) -> str:
    bare = re.sub(r"^(in|out|io)_", "", name)
    return re.sub(r"(?<!^)(?=[A-Z])", "_", bare).lower()


def resolve_template(text: str, bindings: dict) -> str:
    def one(m: re.Match) -> str:
        app = (bindings.get("apps") or {}).get(m.group(1))
        if app is None:
            raise BotFault(f"no binding for app '{m.group(1)}'")
        part = m.group(2)
        if part.startswith("credentials."):
            value = (app.get("credentials") or {}).get(part.split(".", 1)[1])
        else:
            value = app.get(part)
        if value is None:
            raise BotFault(f"binding {m.group(0)} has no value")
        return str(value)

    return TEMPLATE.sub(one, text)


def value_for(name: str | None, selector: str, auto_bindings: dict, inputs: dict, bindings: dict):
    if name:
        if name in inputs:
            return inputs[name]
        if snake(name) in inputs:
            return inputs[snake(name)]
        if name in auto_bindings:
            return resolve_template(str(auto_bindings[name]), bindings)
        return None
    return inputs.get(selector)


def fill_path(path: str, auto_bindings: dict, inputs: dict, bindings: dict) -> str:
    def one(m: re.Match) -> str:
        v = value_for(m.group(1), "", auto_bindings, inputs, bindings)
        if v is None:
            raise BotFault(f"no value for '{m.group(1)}' in the path {path}")
        return str(v)

    return re.sub(r"\{(\w+)\}", one, path)


# --- output ------------------------------------------------------------------------------------


def now() -> datetime:
    return datetime.now(timezone.utc)


def dotnet(ts: datetime) -> str:
    """The .NET round-trip format UiPath writes: seven fractional digits, Z."""
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"


class RobotLog:
    def __init__(self, path: Path, process: str, job_id: str, robot: str, machine: str):
        self.fh = path.open("a", encoding="utf-8")
        self.base = {
            "logType": "Default",
            "windowsIdentity": f"{machine}\\harness",
            "machineName": machine,
            "processName": process,
            "processVersion": "1.0.0",
            "jobId": job_id,
            "robotName": robot,
        }

    def write(self, message: str, level: str = "Information", **extra: Any) -> None:
        line = {"message": message, "level": level, "timeStamp": dotnet(now())}
        line["fingerprint"] = str(uuid.uuid4())
        line.update(self.base)
        line.update(extra)
        self.fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.fh.flush()

    def activity(
        self,
        state: str,
        kind: str,
        display: str,
        args: dict | None,
        variables: dict | None,
        message: str | None = None,
    ) -> None:
        info: dict[str, Any] = {"Activity": ACTIVITY[kind], "DisplayName": display, "State": state}
        if args is not None:
            info["Arguments"] = args
        if variables is not None:
            info["Variables"] = variables
        level = "Error" if state == "Faulted" else "Verbose"
        self.write(message or f"{display} {state}", level, activityInfo=info)

    def close(self) -> None:
        self.fh.close()


class Recorder:
    def __init__(self, root: Path | None, page):
        self.root = root
        self.page = page
        self.seq = 0
        if root:
            (root / "dom").mkdir(parents=True, exist_ok=True)
            self.steps = (root / "steps.jsonl").open("w", encoding="utf-8")

    def step(self, action: str, selector: str | None, started: datetime, value: str | None):
        if not self.root:
            return
        self.seq += 1
        rel = f"dom/{self.seq:04d}.html"
        (self.root / rel).write_text(self.page.evaluate(SYNC_JS), encoding="utf-8")
        line = {
            "seq": self.seq,
            "ts": started.isoformat().replace("+00:00", "Z"),
            "url": self.page.url,
            "action": action,
            "selector": selector,
            "dom": rel,
        }
        if value is not None:
            line["value"] = value
        self.steps.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.steps.flush()

    def close(self) -> None:
        if self.root:
            self.steps.close()


def scrub_har(path: Path) -> None:
    """Drop credential headers, cookies and password-like JSON body fields from a HAR in place."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in (data.get("log") or {}).get("entries") or []:
        for part in ("request", "response"):
            msg = entry.get(part) or {}
            msg["headers"] = [
                h
                for h in msg.get("headers") or []
                if h.get("name", "").lower() not in CREDENTIAL_HEADERS
            ]
            msg["cookies"] = []
        post = (entry.get("request") or {}).get("postData")
        if post and post.get("text"):
            try:
                body = json.loads(post["text"])
            except ValueError:
                body = None
            if isinstance(body, dict):
                body = {k: ("***" if SECRET_KEYS.search(k) else v) for k, v in body.items()}
                post["text"] = json.dumps(body)
            elif SECRET_KEYS.search(post["text"]):
                post["text"] = "***"
        content = (entry.get("response") or {}).get("content") or {}
        content.pop("text", None)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


# --- the run -----------------------------------------------------------------------------------


def settle(page, settle_ms: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001 - a page that never goes idle is still recorded
        pass
    page.wait_for_timeout(settle_ms)


def web_screens(automation: dict) -> list[tuple[str, dict]]:
    out = []
    for touch in automation.get("touches") or []:
        if touch.get("via") == "web":
            out += [(touch["app"], s) for s in touch.get("screens") or []]
    return out


def run_once(
    browser,
    automation: dict,
    bindings: dict,
    inputs: dict,
    log_path: Path,
    record_root: Path | None,
    private: set[str],
    settle_ms: int,
    scrub: bool,
) -> dict:
    job_id = str(uuid.uuid4())
    process = automation.get("process") or automation["id"]
    log = RobotLog(log_path, process, job_id, "harness-robot", "HARNESS01")
    rec_id = None
    context_args: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}}
    if record_root:
        rec_id = f"rec_{now().strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}"
        rec_dir = record_root / rec_id
        rec_dir.mkdir(parents=True)
        context_args.update(
            record_har_path=str(rec_dir / "recording.har"), record_har_content="omit"
        )
    else:
        rec_dir = None
    context = browser.new_context(**context_args)
    page = context.new_page()
    recorder = Recorder(rec_dir, page)
    auto_bindings = automation.get("bindings") or {}
    state = "Successful"
    read: dict[str, Any] = {}
    started = time.monotonic()
    log.write(f"{process} execution started")
    opened = False
    try:
        for app, screen in web_screens(automation):
            web = (bindings.get("apps") or {}).get(app, {}).get("web")
            if not web:
                raise BotFault(f"no web address bound for app '{app}'")
            url = web.rstrip("/") + fill_path(
                screen.get("path") or "/", auto_bindings, inputs, bindings
            )
            kind = "goto" if opened else "open"
            display = f"Open {screen['name']}" if opened else f"Open browser on {screen['name']}"
            log.activity("Executing", kind, display, {"Url": url, "TimeoutMS": 30000}, None)
            t0 = now()
            page.goto(url)
            settle(page, settle_ms)
            opened = True
            log.activity("Closed", kind, display, {"Url": url, "TimeoutMS": 30000}, None)
            recorder.step("goto", None, t0, None)
            for use in screen.get("uses") or []:
                one_use(
                    page,
                    log,
                    recorder,
                    use,
                    auto_bindings,
                    inputs,
                    bindings,
                    private,
                    read,
                    settle_ms,
                )
    except BotFault as exc:
        state = "Faulted"
        log.write(f"{process} faulted: {exc}", "Error")
    finally:
        log.write(
            f"{process} execution ended" + (" (faulted)" if state == "Faulted" else ""),
            totalExecutionTimeInSeconds=int(time.monotonic() - started),
        )
        log.close()
        recorder.close()
        context.close()
    if rec_dir and scrub and (rec_dir / "recording.har").exists():
        scrub_har(rec_dir / "recording.har")
    return {
        "job_id": job_id,
        "state": state,
        "recording": str(rec_dir) if rec_dir else None,
        "read": read,
    }


def one_use(page, log, recorder, use, auto_bindings, inputs, bindings, private, read, settle_ms):
    action = use["action"]
    selector = use["selector"]
    name = use.get("field")
    raw = use.get("raw") or selector
    display = f"{VERB.get(action, action)} {name or selector}"
    is_private = bool(name and name in private)
    if selector.startswith("uipath:"):
        log.activity("Executing", action, display, {"Selector": raw}, None)
        log.activity("Faulted", action, display, None, None, "selector cannot be driven as CSS")
        raise BotFault(f"{display}: selector cannot be driven as CSS")
    args: dict[str, Any] = {"Selector": raw, "TimeoutMS": 30000}
    variables: dict[str, Any] = {}
    value = None
    if action in ("type", "select"):
        value = value_for(name, selector, auto_bindings, inputs, bindings)
        if value is None:
            log.activity("Executing", action, display, None if is_private else args, None)
            log.activity("Faulted", action, display, None, None, f"no value for {name or selector}")
            raise BotFault(f"{display}: no value for {name or selector}")
        value = str(value)
        args["Text" if action == "type" else "Item"] = value
        if name:
            variables[name] = value
    log.activity(
        "Executing",
        action,
        display,
        None if is_private else args,
        None if is_private else variables or None,
    )
    t0 = now()
    out: dict[str, Any] = {}
    try:
        loc = page.locator(selector).first
        if action == "type":
            loc.fill(value, timeout=10000)
        elif action == "select":
            try:
                loc.select_option(label=value, timeout=10000)
            except Exception:  # noqa: BLE001 - UiPath matches the item by text; fall back to value
                loc.select_option(value=value, timeout=10000)
        elif action == "click":
            loc.click(timeout=10000)
        elif action == "hover":
            loc.hover(timeout=10000)
        elif action == "read":
            loc.wait_for(timeout=10000)
            text = (loc.text_content() or "").strip()
            out["Value"] = text
        elif action == "wait":
            try:
                loc.wait_for(timeout=5000)
                out["Exists"] = True
            except Exception:  # noqa: BLE001
                out["Exists"] = False
    except BotFault:
        raise
    except Exception as exc:  # noqa: BLE001 - the robot faults; the log says why
        log.activity(
            "Faulted", action, display, None, None, f"{display} faulted: {type(exc).__name__}"
        )
        raise BotFault(f"{display}: {exc}") from exc
    if action in ("type", "select", "click", "hover"):
        settle(page, settle_ms)
    if name and out:
        variables[name] = next(iter(out.values()))
        read[name] = variables[name]
    closed_args = None if is_private else {**args, **out}
    log.activity("Closed", action, display, closed_args, None if is_private else variables or None)
    if action in ("type", "select", "click"):
        recorder.step(action, selector, t0, None if is_private else value)


def load_automation(path: Path) -> dict:
    path = Path(path)
    file = path / "automation.yaml" if path.is_dir() else path
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    project = file.parent / ((data.get("source") or {}).get("path") or "") / "project.json"
    if project.is_file():
        try:
            data["process"] = json.loads(project.read_text(encoding="utf-8-sig")).get("name")
        except ValueError:
            pass
    return data


def run(
    automation_path: Path,
    bindings: dict,
    runs: list[dict],
    log_path: Path,
    record_root: Path | None = None,
    private: set[str] | None = None,
    headless: bool = True,
    settle_ms: int = 150,
    scrub: bool = True,
) -> list[dict]:
    from playwright.sync_api import sync_playwright

    automation = load_automation(automation_path)
    if private is None:
        private = {
            u.get("field")
            for _, s in web_screens(automation)
            for u in s.get("uses") or []
            if u.get("field") and re.search(r"pass", u["field"], re.I)
        }
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            for inputs in runs:
                merged = {**(bindings.get("inputs") or {}), **inputs}
                results.append(
                    run_once(
                        browser,
                        automation,
                        bindings,
                        merged,
                        log_path,
                        record_root,
                        private,
                        settle_ms,
                        scrub,
                    )
                )
        finally:
            browser.close()
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("automation", type=Path, help="automation folder or automation.yaml")
    p.add_argument(
        "--bindings", type=Path, required=True, help="bindings JSON (specs/bindings.example.json)"
    )
    p.add_argument("--inputs", type=Path, help="JSON object, or a list of objects (one run each)")
    p.add_argument("--log", type=Path, required=True, help="robot log to append to (JSON lines)")
    p.add_argument("--record", type=Path, help="evidence/<app> folder to write recordings into")
    p.add_argument("--private", action="append", help="variable logged as Private (repeatable)")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--settle-ms", type=int, default=150)
    p.add_argument("--no-scrub", action="store_true", help="keep the HAR as Playwright wrote it")
    a = p.parse_args(argv)
    bindings = json.loads(a.bindings.read_text(encoding="utf-8"))
    runs: Any = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else [{}]
    runs = runs if isinstance(runs, list) else [runs]
    results = run(
        a.automation,
        bindings,
        runs,
        a.log,
        a.record,
        set(a.private) if a.private else None,
        headless=not a.headed,
        settle_ms=a.settle_ms,
        scrub=not a.no_scrub,
    )
    print(json.dumps(results, indent=2))
    return 0 if all(r["state"] == "Successful" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
