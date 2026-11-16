"""Conformance (T-07): can this automation work against the demo apps as they are built?

- Screens: the automation's web steps are replayed in a headless browser, in order, with its test
  inputs and the lab's test credentials. Each selector must resolve where the bot expects it.
- Routes: each route it calls must exist. A route that is missing answers exactly like a route
  that cannot exist, so each is compared with a probe of a made-up path on the same method.
- Tables: every table and column it queries must exist in the app's database.

Verdict per automation: READY (no failures) or BLOCKED (each failure named). Items that cannot be
checked (selectors CSS cannot express) are listed as not checked. With the app model at hand, the
report also says how much of what was checked is `observed` rather than only `declared`.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import yaml

from labctl import bindings as bnd
from labctl.models import ApiTouch, Automation, Bindings, DbTouch, WebTouch

PARAM = re.compile(r"\{([^}]+)\}")
STEP_TIMEOUT_MS = 8000


@dataclass
class Item:
    kind: str  # screen | route | table | fidelity
    app: str
    what: str  # e.g. "input#caseNo on /cases (type)"
    status: str  # ok | failed | unchecked | skipped
    detail: str = ""
    basis: str | None = None


@dataclass
class Report:
    automation: str
    verdict: str = "READY"
    items: list[Item] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1
        return out

    def basis_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            if item.status == "ok" and item.basis:
                out[item.basis] = out.get(item.basis, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "automation": self.automation,
            "verdict": self.verdict,
            "counts": self.counts(),
            "checked_basis": self.basis_counts(),
            "errors": self.errors,
            "items": [asdict(i) for i in self.items],
        }


def _fill(path: str, values: dict[str, str]) -> tuple[str, list[str]]:
    missing: list[str] = []

    def one(m: re.Match) -> str:
        name = m.group(1)
        if name in values:
            return values[name]
        missing.append(name)
        return "x"

    return PARAM.sub(one, path), missing


def _model_basis(root: Path, app_id: str) -> dict[tuple[str, str], str]:
    """(kind, key) -> basis from apps/<app>/model.yaml, when there is one."""
    path = root / "apps" / app_id / "model.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[tuple[str, str], str] = {}
    for screen in data.get("screens", []):
        for el in screen.get("elements", []):
            if el.get("selector"):
                out[("screen", el["selector"])] = el.get("basis", "")
    for route in data.get("routes", []):
        out[("route", f"{route['method']} {route['path']}")] = route.get("basis", "")
    for table in data.get("tables", []):
        out[("table", table["name"].lower())] = table.get("basis", "")
    return out


def _route_basis(basis: dict, method: str, path: str) -> str | None:
    from labctl.source import best_match

    keys = [k for (kind, k) in basis if kind == "route" and k.startswith(method + " ")]
    found = best_match(path, keys, key=lambda k: k.split(" ", 1)[1])
    return basis.get(("route", found)) if found else None


# --- screens -------------------------------------------------------------------------------------


def _check_screens(
    auto: Automation, b: Bindings, args: dict[str, str], report: Report, basis_by_app: dict
) -> None:
    web_touches = [t for t in auto.touches if isinstance(t, WebTouch)]
    if not web_touches:
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.errors.append("Playwright is not installed; screens not checked")
        for t in web_touches:
            for s in t.screens:
                for u in s.uses:
                    report.items.append(
                        Item(
                            "screen", t.app, f"{u.selector} on {s.path}", "unchecked", "no browser"
                        )
                    )
        return

    values = {**args, **auto.test_inputs}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - e.g. `playwright install chromium` never ran
            report.errors.append(
                f"the browser could not start ({_short(exc)}); run `python -m playwright install chromium`"
            )
            for t in web_touches:
                for s in t.screens:
                    for u in s.uses:
                        report.items.append(Item("screen", t.app, f"{u.selector} on {s.path or s.name}", "unchecked", "no browser"))  # fmt: skip
            return
        page = browser.new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        try:
            for touch in web_touches:
                base = b.apps[touch.app].web.rstrip("/")
                basis = basis_by_app.get(touch.app, {})
                for screen in touch.screens:
                    label = screen.path or screen.name
                    if screen.path:
                        path, missing = _fill(screen.path, values)
                        if missing:
                            report.errors.append(
                                f"{screen.path}: no test input for {', '.join(missing)}; "
                                "add them under test_inputs in automation.yaml"
                            )
                        if not _on_screen(page.url, base, screen.path):
                            try:
                                page.goto(base + path, wait_until="domcontentloaded")
                            except Exception as exc:  # noqa: BLE001
                                report.items.append(
                                    Item("screen", touch.app, f"open {path}", "failed", _short(exc))
                                )
                                continue
                    for use in screen.uses:
                        what = f"{use.selector} on {label} ({use.action})"
                        b_ = basis.get(("screen", use.selector))
                        if use.selector.startswith("uipath:"):
                            report.items.append(
                                Item(
                                    "screen", touch.app, what, "unchecked", "not a CSS selector", b_
                                )
                            )
                            continue
                        try:
                            _act(page, use.action, use.selector, values.get(use.field or "", ""))
                            report.items.append(Item("screen", touch.app, what, "ok", "", b_))
                        except Exception as exc:  # noqa: BLE001 - one step, not the run
                            report.items.append(
                                Item("screen", touch.app, what, "failed", _short(exc), b_)
                            )
        finally:
            browser.close()


def _on_screen(url: str, base: str, pattern: str) -> bool:
    from labctl.source import match_score

    if not url.startswith(base):
        return False
    path = url[len(base) :].split("?", 1)[0].split("#", 1)[0] or "/"
    return match_score(pattern, path) is not None and _same_literals(pattern, path)


def _same_literals(pattern: str, path: str) -> bool:
    from labctl.source import segments

    return all(a is None or a == b for a, b in zip(segments(pattern), segments(path), strict=False))


def _act(page, action: str, selector: str, value: str) -> None:
    from labctl.locate import locate

    locator = locate(page, selector).first  # "iframe#x >>> #y" goes into the frame
    if action in ("read", "wait"):
        locator.wait_for(state="attached")
        return
    locator.wait_for(state="visible")
    if action == "type":
        locator.fill(value or "test")
    elif action == "click":
        locator.click()
        try:
            page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - a page that keeps polling is still usable
            pass
    elif action == "select":
        _select(page, selector, locator, value)
    elif action == "hover":
        locator.hover()


def _select(page, selector: str, locator, value: str) -> None:
    # Options may arrive late (a cascade): wait until there is something besides a placeholder.
    page.wait_for_function(
        "s => { const e = document.querySelector(s); return e && !e.disabled && e.options.length > 1; }",
        arg=selector,
        timeout=STEP_TIMEOUT_MS,
    )
    if value:
        try:
            locator.select_option(label=value, timeout=2000)
            return
        except Exception:  # noqa: BLE001
            try:
                locator.select_option(value=value, timeout=2000)
                return
            except Exception:  # noqa: BLE001
                pass
    options = locator.locator("option").evaluate_all(
        "os => os.map(o => o.value).filter(v => v !== '')"
    )
    if not options:
        raise _NoOption(f"{selector} has no option to choose")
    locator.select_option(value=options[0])


class _NoOption(Exception):
    pass


def _short(exc: Exception) -> str:
    text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return text[:300]


# --- routes and tables ---------------------------------------------------------------------------


def _check_routes(
    auto: Automation, b: Bindings, values: dict[str, str], report: Report, basis_by_app: dict
) -> None:
    for touch in (t for t in auto.touches if isinstance(t, ApiTouch)):
        base = b.apps[touch.app].api.rstrip("/")
        with httpx.Client(base_url=base, timeout=15, follow_redirects=False) as client:
            for route in touch.routes:
                path, _ = _fill(route.path, values)
                what = f"{route.method} {route.path}"
                b_ = _route_basis(basis_by_app.get(touch.app, {}), route.method, route.path)
                try:
                    kwargs = {"json": {}} if route.method in ("POST", "PUT", "PATCH") else {}
                    real = client.request(route.method, path, **kwargs)
                    probe = client.request(
                        route.method, f"/__twinlab_probe__/{uuid.uuid4().hex}", **kwargs
                    )
                except httpx.HTTPError as exc:
                    report.items.append(
                        Item("route", touch.app, what, "failed", f"not reachable: {exc}", b_)
                    )
                    continue
                if real.status_code == 405:
                    report.items.append(Item("route", touch.app, what, "failed",
                                             f"the path exists but not for {route.method} (405)", b_))  # fmt: skip
                elif real.status_code == probe.status_code and real.content == probe.content:
                    if real.status_code in (401, 403):
                        report.items.append(Item("route", touch.app, what, "unchecked",
                                                 "the app answers every request without a sign-in the same way", b_))  # fmt: skip
                    else:
                        report.items.append(Item("route", touch.app, what, "failed",
                                                 f"answers like a route that does not exist ({real.status_code})", b_))  # fmt: skip
                else:
                    report.items.append(
                        Item("route", touch.app, what, "ok", f"{real.status_code}", b_)
                    )


def _check_tables(auto: Automation, b: Bindings, report: Report, basis_by_app: dict) -> None:
    touches = [t for t in auto.touches if isinstance(t, DbTouch)]
    if not touches:
        return
    try:
        import psycopg
    except ImportError:
        report.errors.append("psycopg is not installed; tables not checked")
        return
    for touch in touches:
        url = b.apps[touch.app].db
        try:
            with psycopg.connect(url, connect_timeout=10) as conn:
                rows = conn.execute(
                    "SELECT lower(table_name), lower(column_name) FROM information_schema.columns "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
                ).fetchall()
        except Exception as exc:  # noqa: BLE001
            report.items.append(
                Item("table", touch.app, "database", "failed", f"cannot connect: {_short(exc)}")
            )
            continue
        columns: dict[str, set[str]] = {}
        for table, column in rows:
            columns.setdefault(table, set()).add(column)
        for use in touch.tables:
            b_ = basis_by_app.get(touch.app, {}).get(("table", use.table.lower()))
            have = columns.get(use.table.lower())
            if have is None:
                report.items.append(
                    Item("table", touch.app, use.table, "failed", "table does not exist", b_)
                )
                continue
            missing = [c for c in use.columns if c.lower() not in have]
            if missing:
                report.items.append(
                    Item(
                        "table",
                        touch.app,
                        use.table,
                        "failed",
                        f"missing columns: {', '.join(missing)}",
                        b_,
                    )
                )
            else:
                report.items.append(
                    Item("table", touch.app, use.table, "ok", f"{len(use.columns)} column(s)", b_)
                )


def _reset(root: Path, apps: list[str], report: Report, when: str) -> None:
    from labctl import lab as lab_mod

    for app_id in apps:
        try:
            lab_mod.agent(root, app_id).reset()  # app id or a copy's lab id
        except Exception as exc:  # noqa: BLE001 - a check still runs without a baseline
            report.errors.append(f"{app_id}: data not reset {when} the check ({_short(exc)})")


def _app_changed_at(root: Path, app_id: str) -> float:
    """When the app's own files last changed (its build), to tell a replay made before it."""
    folder = root / "apps" / app_id
    skip = {"node_modules", ".venv", "__pycache__", "dist", "build"}
    newest = 0.0
    for path in folder.rglob("*"):
        if path.is_file() and not skip.intersection(path.relative_to(folder).parts):
            newest = max(newest, path.stat().st_mtime)
    return newest


def _check_fidelity(root: Path, automation: Automation, report: Report, require: bool) -> None:
    """Fidelity replay as a gate. For each app the bot uses on screen, the newest replay of a
    recording of the real application against the demo app (Lab > Fidelity replay):

    - it failed on an element this bot uses: BLOCKED (the demo app differs where the bot looks);
    - it failed only on other elements: noted, not blocking;
    - it passed: ok;
    - none, or made before the app last changed: not checked (BLOCKED with `require`).
    """
    from datetime import UTC, datetime

    from labctl.behaviour import fidelity

    used: dict[str, set[str]] = {}
    for touch in automation.touches:
        if isinstance(touch, WebTouch):
            sels = used.setdefault(touch.app, set())
            for screen in touch.screens:
                sels |= {u.selector for u in screen.uses}
    for app_id, selectors in used.items():
        found = fidelity.latest(root, app_id)
        missing = "failed" if require else "unchecked"
        if found is None:
            report.items.append(Item("fidelity", app_id, "replay of a real recording", missing,
                                     "no fidelity replay yet (Lab > Fidelity replay)"))  # fmt: skip
            continue
        name, rep = found
        stamp = name.rsplit("--", 1)[-1]
        when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        what = f"replay of {rep.recording} ({when:%Y-%m-%d %H:%M} UTC)"
        if when.timestamp() + 2 < _app_changed_at(root, app_id):  # the stamp is whole seconds
            report.items.append(Item("fidelity", app_id, what, missing,
                                     "the app changed after this replay: replay again"))  # fmt: skip
            continue
        failed = [s for s in rep.steps if s.status == "fail"]
        if not failed:
            report.items.append(Item("fidelity", app_id, what, "ok"))
            continue
        elements = {s.selector for s in failed if s.selector} | {
            d.element for s in failed for d in s.differences
        }
        hit = sorted(e for e in elements if any(e == sel or e in sel or sel in e
                                                for sel in selectors))  # fmt: skip
        if hit:
            report.items.append(Item("fidelity", app_id, what, "failed",
                                     f"differs on what this bot uses: {', '.join(hit[:5])}"))  # fmt: skip
        else:
            report.items.append(Item("fidelity", app_id, what, "ok",
                                     f"{len(failed)} step(s) differ, none on what this bot uses"))  # fmt: skip
            report.errors.append(
                f"{app_id}: the fidelity replay differs on {len(failed)} step(s) this bot does not "
                f"use; see /apps/{app_id}/fidelity/{name}"
            )


def check(
    root: Path,
    automation: Automation,
    host: str | None = None,
    reset: bool = True,
    labs: dict | None = None,
    require_fidelity: bool = False,
) -> Report:
    """Run the check. With `reset`, each app's data is put back to its baseline before and after,
    because replaying a bot clicks its buttons and the route checks send real requests. `labs`
    checks against copies of the apps' labs (`{"showcase": "showcase.run-1"}`)."""
    report = Report(automation.id)
    labs = labs or {}
    if automation.analysis.gaps:
        report.errors.append(
            f"{len(automation.analysis.gaps)} unresolved analysis gap(s) in automation.yaml"
        )
    try:
        b = bnd.for_automation(root, automation, host or bnd.LAB_HOST, labs)  # checked from here
        args = bnd.arguments(automation, b)
    except bnd.BindingError as exc:
        report.verdict = "BLOCKED"
        report.errors.append(str(exc))
        return report
    basis_by_app = {app_id: _model_basis(root, app_id) for app_id in b.apps}
    for app_id in b.apps:  # readiness is about what exists, not speed: say when a slowdown is on
        try:
            from labctl import lab as lab_mod

            current = httpx.get(
                f"{b.apps[app_id].api.rstrip('/')}/_lab/condition",
                headers={"X-Lab-Token": lab_mod.lab_token(root)},
                timeout=5,
            ).json()
        except (httpx.HTTPError, ValueError):
            continue
        if isinstance(current, dict) and current.get("id"):
            report.errors.append(
                f"{app_id} is running under the condition '{current['id']}'; slow responses can make "
                "steps time out. Check readiness under normal conditions."
            )
    values = {**args, **automation.test_inputs}
    lab_ids = [labs.get(a, a) for a in b.apps]
    if reset:
        _reset(root, lab_ids, report, "before")
    try:
        _check_screens(automation, b, args, report, basis_by_app)
        _check_routes(automation, b, values, report, basis_by_app)
        _check_tables(automation, b, report, basis_by_app)
    finally:
        if reset:
            _reset(root, lab_ids, report, "after")
    _check_fidelity(root, automation, report, require_fidelity)
    if any(i.status == "failed" for i in report.items) or automation.analysis.gaps:
        report.verdict = "BLOCKED"
    if any(i.kind == "screen" and i.status == "failed" for i in report.items):
        report.errors.append(
            "A screen step failed. Either the app lacks the element, or the screen looks different for "
            f"this record (for example a closed one hides its form): check test_inputs "
            f"{automation.test_inputs or '{}'} against the data loaded now, or reset the data."
        )
    return report


def render(report: Report) -> str:
    lines = [f"{report.automation}: {report.verdict}"]
    for item in report.items:
        mark = {"ok": "ok  ", "failed": "FAIL", "unchecked": "--  ", "skipped": "skip"}[item.status]
        basis = f" [{item.basis}]" if item.basis else ""
        detail = f"  {item.detail}" if item.detail and item.status != "ok" else ""
        lines.append(f"  {mark} {item.kind:<6} {item.app}: {item.what}{basis}{detail}")
    for error in report.errors:
        lines.append(f"  NOTE {error}")
    counts = report.counts()
    lines.append(
        "  "
        + ", ".join(f"{v} {k}" for k, v in counts.items())
        + (f"; checked items by basis: {report.basis_counts()}" if report.basis_counts() else "")
    )
    return "\n".join(lines)
