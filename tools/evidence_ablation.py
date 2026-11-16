"""E-04: how much of an app each kind of evidence finds, measured on the reference app.

The brief built from the reference app's source code is the yardstick. The same brief is then
built from subsets of evidence, as a team without source would have them, and each is compared
with it: screens, selectors, routes, tables and rules in scope, found and missed.

    python tools/evidence_ablation.py              # prints the table
    python tools/evidence_ablation.py --write      # also writes docs/08-evidence-ablation.md

Evidence used (all synthetic):
- source: `reference/case-workbench/`;
- recording: `lab/labctl/tests/fixtures/showcase-rec/` (a recording of the demo copy, which
  serves the same screens and API as the reference app);
- OpenAPI: generated from the reference backend itself;
- DDL: the schema the source brief generates (what a DBA's export of the same database holds);
- automation: `automations/uipath-case-update`; a database-only automation is its `db` touch;
- flow: `specs/flow.example.yaml`. Screenshots are listed for the builder, never parsed, so
  "flow + screenshots" finds what the flow finds.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    import labctl  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT / "lab" / "labctl"))

import yaml  # noqa: E402

from labctl import brief as brief_mod  # noqa: E402
from labctl import model_build as mb  # noqa: E402
from labctl import repo  # noqa: E402
from labctl.appmodel import Flow  # noqa: E402
from labctl.data import ddl as ddl_mod  # noqa: E402

APP = "showcase"
SOURCE = ROOT / "reference" / "case-workbench"
RECORDING = ROOT / "lab" / "labctl" / "tests" / "fixtures" / "showcase-rec"


def automation():
    model, problems = repo.load(
        ROOT / "automations/uipath-case-update/automation.yaml", "automation"
    )
    assert model is not None, problems
    return model


def db_only(auto):
    return auto.model_copy(update={
        "id": "db-report", "surface": "backend",
        "touches": [t for t in auto.touches if t.via == "db"],
    })  # fmt: skip


def flow():
    data = yaml.safe_load((ROOT / "specs" / "flow.example.yaml").read_text(encoding="utf-8"))
    return Flow.model_validate(data)


def openapi_file(tmp: Path) -> Path:
    sys.path.insert(0, str(SOURCE / "backend"))
    from app.main import create_app  # the reference backend, on an in-memory database

    spec = create_app("sqlite:///:memory:").openapi()
    path = tmp / "openapi.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _shape(key: str) -> str:
    """`/api/cases/{case_no}`, `/api/cases/{id}` and `/cases/:caseNo` name the same place."""
    import re

    return re.sub(r"\{[^}]*\}|:[A-Za-z_]\w*", "{}", key)


def _rule_key(rule: str) -> str:
    """Rules worded differently by different evidence: compare their status and error code
    (`422 NOTE_REQUIRED`), or a body constraint's field (`body password`)."""
    import re

    m = re.match(r"^(\d{3}) ([A-Z][A-Z0-9_]+)", rule)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.match(r"^request body (\w+)", rule)
    return f"body {m.group(1)}" if m else rule


def measure(b: brief_mod.Brief) -> dict[str, set[str]]:
    selectors = {
        f"{_shape(s.screen.path or s.screen.name)} {e.selector}"
        for s in b.screens
        for e in s.elements
        if e.selector and e.status != "missing"
    }
    return {
        "screens": {_shape(s.screen.path or s.screen.name) for s in b.screens},
        "selectors": selectors,
        "routes": {_shape(k) for k in b.routes},
        "tables": {t.lower() for t in b.tables},
        "rules": {_rule_key(r["rule"]) for r in brief_mod.rules(b)},
    }


def brief_from(parts) -> brief_mod.Brief:
    return brief_mod.scope(APP, "Case Workbench", mb.merge(APP, parts), "postgres")


def run() -> tuple[list[tuple[str, dict]], dict]:
    auto = automation()
    tmp = Path(tempfile.mkdtemp())
    source_brief = brief_from([mb.from_automations(APP, [auto]), mb.from_source(APP, SOURCE, ROOT)])
    ddl_text = brief_mod.schema(source_brief, ROOT)
    ddl_part = ddl_mod.to_model(APP, ddl_text, "ddl:schema.sql")
    spec = openapi_file(tmp)
    subsets = [
        ("source + automation", [mb.from_automations(APP, [auto]), mb.from_source(APP, SOURCE, ROOT)]),
        ("recording + automation", [mb.from_automations(APP, [auto]), mb.from_recording(APP, RECORDING, ROOT, None)]),
        ("automation only", [mb.from_automations(APP, [auto])]),
        ("OpenAPI + automation", [mb.from_automations(APP, [auto]), mb.from_openapi(APP, spec)]),
        ("recording + OpenAPI + DDL + automation", [
            mb.from_automations(APP, [auto]), mb.from_recording(APP, RECORDING, ROOT, None),
            mb.from_openapi(APP, spec), ddl_part]),
        ("flow + screenshots", [mb.from_flows(APP, [flow()])]),
        ("DDL + database automation", [mb.from_automations(APP, [db_only(auto)]), ddl_part]),
    ]  # fmt: skip
    yard = measure(source_brief)
    rows = []
    for name, parts in subsets:
        b = brief_from(parts)
        got = measure(b)
        rows.append((name, {"mode": b.mode, **{k: (got[k] & yard[k], yard[k] - got[k], got[k] - yard[k])
                                                   for k in yard}}))  # fmt: skip
    return rows, yard


def table(rows, yard) -> str:
    areas = ["screens", "selectors", "routes", "tables", "rules"]
    head = "| Evidence | Mode | " + " | ".join(f"{a} (of {len(yard[a])})" for a in areas) + " |"
    lines = [head, "|" + "---|" * (len(areas) + 2)]
    for name, r in rows:
        cells = []
        for a in areas:
            found, missed, extra = r[a]
            cells.append(f"{len(found)}" + (f" (+{len(extra)} more)" if extra else ""))
        lines.append(f"| {name} | {r['mode']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def details(rows) -> str:
    out = []
    for name, r in rows[1:]:
        missed = {a: sorted(r[a][1]) for a in ("screens", "routes", "tables", "rules") if r[a][1]}
        if missed:
            out.append(f"**{name}** misses:")
            for a, items in missed.items():
                shown = ", ".join(f"`{i}`" for i in items[:8]) + (" ..." if len(items) > 8 else "")
                out.append(f"- {a}: {shown}")
            out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    rows, yard = run()
    text = table(rows, yard)
    print(text)
    print()
    print(details(rows))
    if a.write:
        doc = ROOT / "docs" / "08-evidence-ablation.md"
        doc.write_text(
            PAGE.format(table=text, details=details(rows)), encoding="utf-8", newline="\n"
        )
        print(f"wrote {doc}")
    return 0


PAGE = """# What each kind of evidence finds (E-04)

Measured on the reference app with `python tools/evidence_ablation.py --write`. The brief built
from the source code is the yardstick: each row is a brief built from less, as a team without
source would have it. A number is how many of the yardstick's items the brief also has;
"+N more" are items it has that the source brief does not (usually other names for the same
thing, or items outside the automation's needs).

{table}

What the rows mean for an office team:

- **Source** is complete but rarely available.
- **A recording plus the bot's code** finds the screens, selectors and routes the bot uses, with
  their real response shapes; it cannot see tables, and only rules the recording happened to
  trigger.
- **The bot's code alone** gives exactly what the bot touches and nothing around it: enough for
  READY, thin for a person exploring the app.
- **OpenAPI** adds every route and its body constraints (rules), but no screens.
- **A database schema (DDL)** gives every table and column, keys and constraints, and nothing
  about screens or routes.
- **A written flow** gives screens and fields by name only; a builder has to guess selectors.

So the usual office set is a recording of each flow, the DBA's DDL, an API description if one
exists, and the bots' code. Each brief says, per screen, which of these it was built from (its
mode: copy, reconstruction, skeleton, backend, sample).

## What the subsets miss

{details}
"""

if __name__ == "__main__":
    raise SystemExit(main())
