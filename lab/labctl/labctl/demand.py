"""Demand report: everything registered automations need from one application.

This is the scope of the demo app. Nothing outside it is built.
"""

from __future__ import annotations

from labctl.models import ApiTouch, Automation, DbTouch, WebTouch


def _add(items: list, value) -> None:
    if value not in items:
        items.append(value)


def demand(app: str, automations: list[Automation]) -> dict:
    """Union of what `automations` touch on `app`, each line listing who needs it."""
    screens: dict[str, dict] = {}
    routes: dict[tuple[str, str], dict] = {}
    tables: dict[str, dict] = {}
    needed_by: list[str] = []
    unresolved: list[dict] = []

    for auto in automations:
        mine = [t for t in auto.touches if t.app == app]
        if not mine:
            continue
        needed_by.append(auto.id)
        if auto.analysis.gaps:
            unresolved.append({"automation": auto.id, "gaps": len(auto.analysis.gaps)})
        for touch in mine:
            if isinstance(touch, WebTouch):
                for screen in touch.screens:
                    key = screen.path or screen.name
                    entry = screens.setdefault(
                        key, {"name": screen.name, "path": screen.path, "elements": {}}
                    )
                    for use in screen.uses:
                        el = entry["elements"].setdefault(
                            use.selector,
                            {"selector": use.selector, "actions": [], "automations": []},
                        )
                        _add(el["actions"], use.action)
                        _add(el["automations"], auto.id)
            elif isinstance(touch, ApiTouch):
                for route in touch.routes:
                    entry = routes.setdefault(
                        (route.method, route.path),
                        {"method": route.method, "path": route.path, "automations": []},
                    )
                    _add(entry["automations"], auto.id)
            elif isinstance(touch, DbTouch):
                for use in touch.tables:
                    entry = tables.setdefault(
                        use.table.lower(),
                        {"table": use.table, "columns": [], "access": [], "automations": []},
                    )
                    for column in use.columns:
                        _add(entry["columns"], column)
                    for access in (
                        ("read", "write") if use.access == "read_write" else (use.access,)
                    ):
                        _add(entry["access"], access)
                    _add(entry["automations"], auto.id)

    return {
        "app": app,
        "needed_by": needed_by,
        "unresolved": unresolved,
        "screens": [{**s, "elements": list(s["elements"].values())} for s in screens.values()],
        "routes": list(routes.values()),
        "tables": list(tables.values()),
    }


def render(report: dict) -> str:
    """The report as plain text."""
    lines = [f"Demand for {report['app']}"]
    if not report["needed_by"]:
        return lines[0] + "\n  No registered automation touches this application."
    lines.append(f"  needed by: {', '.join(report['needed_by'])}")
    for item in report["unresolved"]:
        lines.append(
            f"  INCOMPLETE: {item['automation']} has {item['gaps']} unresolved gap(s); "
            "this report may be missing what those gaps hide"
        )
    for screen in report["screens"]:
        lines.append(f"\n  screen {screen['name']}  {screen['path'] or '(path unknown)'}")
        for el in screen["elements"]:
            lines.append(
                f"    {el['selector']:<32} {'/'.join(el['actions']):<12} "
                f"{', '.join(el['automations'])}"
            )
    if report["routes"]:
        lines.append("\n  routes")
        for route in report["routes"]:
            lines.append(
                f"    {route['method']:<6} {route['path']:<38} {', '.join(route['automations'])}"
            )
    if report["tables"]:
        lines.append("\n  tables")
        for table in report["tables"]:
            columns = ", ".join(table["columns"]) or "(columns not known)"
            lines.append(
                f"    {table['table']:<18} {'+'.join(table['access']):<11} {columns}  "
                f"<- {', '.join(table['automations'])}"
            )
    return "\n".join(lines)
