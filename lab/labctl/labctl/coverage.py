"""The coverage matrix (replication gap R09): what is claimed, what shows it, what checks it.

`labctl coverage <app>` lists every REQUIREMENT of a replica as a row: each in-scope screen, each
action on it, each route with the response statuses seen, each table and column, each browser
storage key, each rule in app.yaml, and the categories a recording rarely shows (roles and
permissions, input boundaries, negative paths, an API's error responses, persistence, concurrency
where a rule mentions it). A row has the evidence that shows it (a recording, DDL, a source file,
an automation), an independent assertion that checks it (an acceptance case, a conformance item, a
fidelity comparison) or none, and a status:

- VERIFIED    evidence, and a passing assertion in the newest report that is still current;
- EVIDENCED   seen, but no assertion (or one that was not run, or was skipped);
- ASSUMED     inferred by the factory or the builder, with no evidence (guessed tables, the default
              stack, assumptions in BUILD-NOTES.md);
- UNOBSERVED  a required category nothing recorded shows (never counted as verified);
- UNSUPPORTED a surface the tooling cannot capture;
- EXCLUDED    declared out of scope, with the declaration;
- BLOCKED     an assertion exists but failed or could not run, so nothing is verified.

"100% of the agreed replication acceptance cases passed" is printed only when every required row is
VERIFIED or EXCLUDED and none is skipped, unknown, assumed or failing; otherwise the exact shortfall
is printed instead. An assertion is tied to a row by `covers:` on an acceptance case (row ids, see
`Row.id`) or automatically (storage cases, reload and fresh-context cases, a case whose locator is
an element's selector, the replay's own compared steps, conformance items). A category row with
nothing recorded becomes VERIFIED only when a covering case also cites where the expectation comes
from (`evidence: "owner's reply of 12 November"`): without that, a passing case never turns an
UNOBSERVED row into a verified one.

The result is kept in `.lab-state/coverage/<app>.json` (and `.md`) with a fingerprint of what it was
built from; it is stale when the app, the evidence, the declarations or the reports change.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel

STATUSES = ("VERIFIED", "EVIDENCED", "ASSUMED", "UNOBSERVED", "UNSUPPORTED", "EXCLUDED", "BLOCKED")
CATEGORIES = (
    "screens", "actions", "apis", "data", "rules", "roles", "boundaries", "negative", "errors",
    "persistence", "concurrency", "assumptions", "unsupported", "exclusions",
)  # fmt: skip
CLAIM = "100% of the agreed replication acceptance cases passed"
SCOPE_FILE = "scope.yaml"
WRITE = ("POST", "PUT", "PATCH", "DELETE")
ROLE_WORDS = re.compile(r"\b(role|roles|permission|permissions|authori[sz]|privilege|admin|approver|"
                        r"supervisor|entitle)", re.I)  # fmt: skip
CONCURRENT = re.compile(r"concurren|simultaneous|same time|\block(ed|ing)?\b|race|optimistic|"
                        r"version conflict|stale", re.I)  # fmt: skip
INPUT_TYPES_SKIPPED = {"button", "submit", "reset", "checkbox", "radio", "hidden", "image"}


class CoverageError(Exception):
    pass


class Row(BaseModel):
    id: str
    kind: str
    category: str
    label: str
    required: bool = True
    status: str
    evidence: list[str] = []  # what shows it: recording steps, HAR, DDL, source, automation
    assertion: str | None = None  # what checks it, independently of the replica
    assertion_result: str | None = None  # PASS | FAIL | BLOCKED | SKIPPED | None (not run)
    declaration: str | None = None  # EXCLUDED: where it was declared
    note: str | None = None


class Request(BaseModel):
    audience: str  # owner (ask the application owner) | lab (work for whoever runs the lab)
    row: str
    text: str


class CoverageReport(BaseModel):
    app: str
    generated_at: str
    fingerprint: dict
    rows: list[Row]
    counts: dict[str, int]
    by_category: dict[str, dict[str, int]]
    denominator: int  # every row; the required ones are `required`
    required: int
    claim: bool
    claim_text: str
    shortfall: list[str] = []
    requests: list[Request] = []
    reports_used: list[str] = []  # fidelity reports the assertions came from
    notes: list[str] = []

    def markdown(self) -> str:
        return render(self)


# --- small helpers -------------------------------------------------------------------------------


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def _safe_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _strs(value) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _pattern_re(pattern: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]*\}|:[A-Za-z_]\w*|\*)", pattern.rstrip("/") or "/")
    body = "".join("[^/]+" if i % 2 else re.escape(p) for i, p in enumerate(parts))
    return re.compile(r"^" + body + r"/?$")


def _same_path(pattern: str | None, path: str | None) -> bool:
    if not pattern or not path:
        return False
    return bool(_pattern_re(pattern).match(path))


def _recordings(root: Path, app_id: str) -> list[Path]:
    from labctl import factory

    return [p for p in factory.discover(root, app_id).recordings
            if p.is_dir() and (p / "steps.jsonl").is_file()]  # fmt: skip


def _raw_steps(rec: Path) -> list[dict]:
    out = []
    try:
        lines = (rec / "steps.jsonl").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


# --- declarations -----------------------------------------------------------------------------


def declarations(rec: Path) -> dict:
    """What a person declared beside one recording: acceptance cases (with `covers:` and
    `evidence:` read from the file as written), exclusions, unobserved/unsupported lists."""
    from labctl.behaviour import acceptance as acc

    out: dict[str, Any] = {"spec": None, "raw": {}, "excluded": [], "unobserved": [],
                           "unsupported": [], "assumed": [], "scope": [], "problems": []}  # fmt: skip
    path = rec / acc.FILE
    if path.exists():
        try:
            out["spec"] = acc.load(path)
        except (ValueError, yaml.YAMLError, OSError) as exc:
            out["problems"].append(f"{acc.FILE}: {exc}")
        data = _safe_yaml(path)
        if isinstance(data, dict):
            for entry in data.get("cases") or []:
                if isinstance(entry, dict) and entry.get("id") is not None:
                    out["raw"][str(entry["id"])] = entry
    spec = out["spec"]
    if spec is not None:
        for name in ("excluded", "unobserved", "unsupported", "assumed", "scope"):
            out[name] += [(s, f"{acc.FILE} of {rec.name}") for s in getattr(spec, name)]
    scope = _safe_yaml(rec / SCOPE_FILE) if (rec / SCOPE_FILE).exists() else None
    if isinstance(scope, dict):
        for name in ("excluded", "unobserved", "unsupported", "scope"):
            for item in scope.get(name) or []:
                text = item.get("id") or item.get("what") if isinstance(item, dict) else item
                why = item.get("reason") if isinstance(item, dict) else None
                if text:
                    out[name].append((str(text), f"{SCOPE_FILE} of {rec.name}"
                                      + (f": {why}" if why else "")))  # fmt: skip
    elif (rec / SCOPE_FILE).exists():
        out["problems"].append(f"{SCOPE_FILE}: not a mapping")
    return out


# --- reports and assertions ---------------------------------------------------------------------


class Assertion(BaseModel):
    id: str
    result: str | None  # PASS | FAIL | BLOCKED | SKIPPED | None
    cited: str | None = None  # where the expectation comes from, for a category row


def current_reports(root: Path, app_id: str) -> tuple[dict[str, tuple[str, Any]], list[str]]:
    """The newest report of each recording that is still current: {recording: (name, report)}, and
    notes about newer reports that are not."""
    from labctl.behaviour import durable, fidelity

    folder = fidelity.reports_dir(root, app_id)
    found = sorted(folder.glob("*--*.json"), key=lambda p: p.stem.rsplit("--", 1)[-1])
    out: dict[str, tuple[str, Any]] = {}
    notes: list[str] = []
    seen: set[str] = set()
    for path in reversed(found):
        try:
            report = fidelity.FidelityReport.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rec = Path(report.recording).name
        if rec in seen:
            continue
        seen.add(rec)
        fresh = durable.assess(root, app_id, report)
        if fresh.state == "current":
            out[rec] = (path.stem, report)
        else:
            notes.append(f"the newest report of {rec} ({path.stem}) cannot be used: "
                         + ("it is out of date: " + "; ".join(fresh.reasons) if fresh.reasons
                            else f"its state is {fresh.state}"))  # fmt: skip
    return out, notes


def conformance_results(root: Path, app_id: str) -> list[dict]:
    """The portal's latest saved readiness results that were made against this app as it is now."""
    from labctl import conformance

    path = _state(root) / "portal-jobs.db"
    if not path.exists():
        return []
    try:
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
        rows = db.execute("SELECT subject, result FROM jobs WHERE kind='conformance' AND "
                          "status='done' ORDER BY created DESC").fetchall()  # fmt: skip
        db.close()
    except sqlite3.Error:
        return []
    now = conformance.app_facts(root, app_id)
    seen, out = set(), []
    for subject, result in rows:
        if subject in seen:
            continue
        seen.add(subject)
        try:
            data = json.loads(result) if result else None
        except ValueError:
            continue
        then = ((data or {}).get("fingerprint") or {}).get("apps", {}).get(app_id)
        if not then or any(then.get(k) != now.get(k) for k in ("app_yaml", "brief", "schema")):
            continue  # made against another version of the app: not an assertion about this one
        out.append(data)
    return out


def _state(root: Path) -> Path:
    from labctl import lab

    return lab.state_dir(root)


class _Index:
    """Assertions by the row they check."""

    def __init__(self) -> None:
        self.by_row: dict[str, list[Assertion]] = {}

    def add(self, row_id: str, a: Assertion) -> None:
        mine = self.by_row.setdefault(row_id, [])
        if all(x.id != a.id for x in mine):
            mine.append(a)

    def get(self, row_id: str) -> list[Assertion]:
        return self.by_row.get(row_id, [])


def _case_rows(case, raw: dict, rows_by_selector: dict[str, list[str]]) -> list[str]:
    """The rows an acceptance case checks."""
    ids = _strs(raw.get("covers"))
    if case.check == "storage" and case.key:
        ids.append(f"storage:{case.area}:{case.key}")
    if case.reload:
        ids.append("persistence:reload")
    if case.fresh_context:
        ids.append("persistence:new-session")
    if isinstance(case.locator, str):
        ids += rows_by_selector.get(case.locator, [])
    return list(dict.fromkeys(ids))


def _assertions(root, app_id, recs, reports, conf, rows_by_selector, screens) -> _Index:
    idx = _Index()
    for rec in recs:
        decl = declarations(rec)
        spec = decl["spec"]
        name = rec.name
        got = reports.get(name)
        results = {c.id: c for c in got[1].cases} if got else {}
        for case in spec.cases if spec else []:
            raw = decl["raw"].get(case.id, {})
            result = None
            if case.problem:
                result = "BLOCKED"
            elif case.id in results:
                result = results[case.id].status
            cited = str(raw["evidence"]).strip() if raw.get("evidence") else None
            tag = f"acceptance case {case.id} ({name})"
            if cited and case.basis == "assumed":
                cited = None  # an assumed expectation is not evidence
            for rid in _case_rows(case, raw, rows_by_selector):
                idx.add(rid, Assertion(id=tag, result=result, cited=cited))
        if got:
            _from_steps(idx, name, got[1], rows_by_selector, screens)
    for result in conf:
        for item in result.get("items") or []:
            if item.get("app") != app_id or item.get("status") not in ("ok", "failed"):
                continue
            res = "PASS" if item["status"] == "ok" else "FAIL"
            tag = f"conformance item ({result.get('automation')}): {item.get('what')}"
            what = str(item.get("what") or "")
            if item.get("kind") == "route":
                idx.add(f"route:{what}", Assertion(id=tag, result=res))
            elif item.get("kind") == "table" and what != "database":
                idx.add(f"table:{what}", Assertion(id=tag, result=res))
            elif item.get("kind") == "screen":
                for sel, ids in rows_by_selector.items():
                    if sel and sel in what:
                        for rid in ids:
                            idx.add(rid, Assertion(id=tag, result=res))
    return idx


def _from_steps(idx: _Index, rec_name: str, report, rows_by_selector, screens) -> None:
    """The replay's own comparisons: a passed, compared step checks the element it acted on and
    the screen it was on."""
    per_screen: dict[str, list[Any]] = {}
    for step in report.steps:
        path = urlsplit(step.url or "").path
        for key, pattern in screens.items():
            if _same_path(pattern, path):
                per_screen.setdefault(key, []).append(step)
        if step.selector and step.outcome in ("replayed", "implied"):
            res = "PASS" if step.status == "pass" else "FAIL"
            for rid in rows_by_selector.get(step.selector, []):
                idx.add(rid, Assertion(id=f"replay of {rec_name}, step {step.seq} ({step.action})",
                                       result=res))  # fmt: skip
    for key, steps in per_screen.items():
        compared = [s for s in steps if s.compared]
        if not compared:
            continue
        res = "FAIL" if any(s.status == "fail" for s in compared) else (
            "BLOCKED" if any(s.status == "blocked" for s in compared) else "PASS")  # fmt: skip
        first, last = compared[0].seq, compared[-1].seq
        idx.add(f"screen:{key}", Assertion(
            id=f"replay of {rec_name}, {len(compared)} compared step(s) {first}-{last}",
            result=res))  # fmt: skip


# --- rows ------------------------------------------------------------------------------------


class _Builder:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, rid: str, kind: str, category: str, label: str, *, evidence=None,
            assumed: bool = False, unsupported: bool = False, required: bool = True,
            note: str | None = None, weak: bool = False) -> dict | None:  # fmt: skip
        if any(r["id"] == rid for r in self.rows):
            return None
        row = {"id": rid, "kind": kind, "category": category, "label": label,
               "evidence": list(evidence or []), "assumed": assumed,
               "unsupported": unsupported, "required": required, "note": note,
               "weak": weak}  # fmt: skip
        self.rows.append(row)
        return row


def _from_text(item) -> list[str]:
    return [str(f) for f in getattr(item, "from_", [])]


def _is_input(el) -> bool:
    return (el.tag in ("input", "textarea", "select")
            and (el.type or "text") not in INPUT_TYPES_SKIPPED)  # fmt: skip


def _model_rows(root: Path, app_id: str, b: _Builder) -> tuple[dict, dict[str, list[str]], dict]:
    """Rows from the app model and the brief's scope. Returns the model facts used elsewhere:
    {"model", "app", "brief"}, selector -> row ids, screen key -> path pattern."""
    from labctl import brief as brief_mod
    from labctl import repo

    folder = root / "apps" / app_id
    model, problems = repo.load(folder / "model.yaml", "model") if (folder / "model.yaml").exists() else (None, [])  # fmt: skip
    if model is None:
        raise CoverageError(
            f"apps/{app_id}/model.yaml is "
            + ("unreadable: " + "; ".join(str(p) for p in problems) if problems else "missing")
            + ". Generate the brief first (labctl brief)."
        )
    app = None
    if (folder / "app.yaml").exists():
        app, _ = repo.load(folder / "app.yaml", "app")
    try:
        brief = brief_mod.scope(app_id, app_id, model)
    except Exception:  # noqa: BLE001 - a model the brief cannot scope is covered whole
        brief = None
    facts = {"model": model, "app": app, "brief": brief}
    in_scope = list(brief.screens) if brief else None
    screens = [s.screen for s in in_scope] if in_scope is not None else list(model.screens)
    selectors: dict[str, list[str]] = {}
    screen_paths: dict[str, str] = {}
    complete = set(model.complete("tables"))

    for scr in screens:
        key = scr.path or scr.name
        screen_paths[key] = scr.path or ""
        ev = _from_text(scr) + ([f"{scr.snapshots} page snapshot(s)"] if scr.snapshots else [])
        b.add(f"screen:{key}", "screen", "screens", f"screen {key}", evidence=ev,
              assumed=scr.basis == "assumed" or not scr.from_,
              note=None if scr.from_ else "no evidence names this screen")  # fmt: skip
        inputs = [e for e in scr.elements if _is_input(e)]
        for el in scr.elements:
            if not (el.selector and el.actions):
                continue
            rid = f"action:{key}:{el.selector}"
            what = f"{'/'.join(el.actions)} on {el.label or el.selector} ({el.selector}) on {key}"
            b.add(rid, "action", "actions", what, evidence=_from_text(el),
                  assumed=el.basis == "assumed" or not el.from_)  # fmt: skip
            selectors.setdefault(el.selector, []).append(rid)
        if inputs:
            names = ", ".join(e.selector or e.name or "?" for e in inputs[:6])
            b.add(f"boundary:{key}", "boundary", "boundaries",
                  f"input boundaries (length, range, format, required) of {len(inputs)} field(s) "
                  f"on {key}: {names}", note="a recording shows that a field exists, not its limits")  # fmt: skip
            b.add(f"negative:validation:{key}", "negative", "negative",
                  f"validation error shown on {key} for a bad or missing value")  # fmt: skip

    routes = [model.route(k) for k in brief.routes] if brief else list(model.routes)
    error_seen = {"4xx": False}
    for r in routes:
        b.add(f"route:{r.key}", "route", "apis", f"{r.key}", evidence=_from_text(r),
              assumed=r.basis == "assumed" or not r.from_)  # fmt: skip
        codes = sorted(r.responses)
        for code in codes:
            b.add(f"status:{r.key}:{code}", "status", "apis", f"{r.key} answers {code}",
                  evidence=_from_text(r))  # fmt: skip
        is_api = r.method != "GET" or any(v and "(content)" not in v for v in r.responses.values())
        if is_api:
            bad = [c for c in codes if c[:1] in ("4", "5")]
            b.add(f"errors:{r.key}", "errors", "errors", f"error responses of {r.key}",
                  evidence=[f"observed {c}" for c in bad])  # fmt: skip
        if r.method in ("POST", "PUT", "PATCH"):
            seen = [c for c in codes if c in ("400", "409", "422")]
            b.add(f"negative:validation:{r.key}", "negative", "negative",
                  f"validation error of {r.key} (bad or missing field)",
                  evidence=[f"observed {c}" for c in seen])  # fmt: skip
        if "{" in r.path:
            b.add(f"negative:not-found:{r.key}", "negative", "negative",
                  f"{r.key} for an id that does not exist",
                  evidence=["observed 404"] if "404" in codes else [])  # fmt: skip
        if any(c in ("401", "403") for c in codes):
            error_seen["4xx"] = True
    if routes or screens:
        b.add("negative:unauthorised", "negative", "negative",
              "what a user without permission sees (sign-in required, access refused)",
              evidence=["observed 401/403"] if error_seen["4xx"] else [])  # fmt: skip

    tables = [model.table(n) for n in brief.tables] if brief else list(model.tables)
    for t in tables:
        guessed = t.basis in ("assumed", "inferred") and not (complete & set(t.from_))
        b.add(f"table:{t.name}", "table", "data", f"table {t.name}", evidence=_from_text(t),
              assumed=guessed or t.basis == "assumed" or not t.from_,
              note="columns and types guessed from recorded responses or page grids"
              if guessed else None)  # fmt: skip
        for c in t.columns:
            b.add(f"column:{t.name}.{c.name}", "column", "data", f"column {t.name}.{c.name} ({c.type})",
                  evidence=_from_text(t), assumed=guessed or t.basis == "assumed" or not t.from_)  # fmt: skip

    data = model.stack.data or {}
    for key in data.get("keys") or []:
        area, _, name = str(key).partition(":")
        b.add(f"storage:{area}:{name}", "storage", "data", f"browser storage key {key}",
              evidence=["observed in the recording's step storage"])  # fmt: skip
    persistence = data.get("persistence") or (
        "server" if data.get("kind") == "server" else "storage" if data.get("storage") else None
    )  # fmt: skip
    shows = persistence in ("storage", "server")
    b.add("persistence:location", "persistence", "persistence",
          "where the data persists: " + str(data.get("label") or persistence or "not assessed"),
          evidence=list(data.get("evidence") or [])[:3] if shows else [],
          note=None if shows else "absence of server writes is not evidence of browser storage")  # fmt: skip
    if app is not None:
        text_of = {r.text: r for r in model.rules}
        for rule in app.rules:
            src = text_of.get(rule.rule)
            ev = _from_text(src) if src and src.basis in ("observed", "declared") else []
            b.add(f"rule:{rule.id}", "rule", "rules",
                  f"rule {rule.id} ({rule.where}): {rule.rule}", evidence=ev,
                  assumed=not ev, note=f"verification: {rule.verification.value}")  # fmt: skip
            if CONCURRENT.search(rule.rule):
                b.add(f"concurrency:{rule.id}", "concurrency", "concurrency",
                      f"two users at once ({rule.id}): {rule.rule}",
                      evidence=[f"rule {rule.id} mentions it"], weak=True)  # fmt: skip
    roles_text = [r.rule for r in (app.rules if app else [])] + [r.text for r in model.rules]
    mention = next((t for t in roles_text if ROLE_WORDS.search(t)), None)
    b.add("roles:permissions", "roles", "roles", "roles and permissions: who may do what",
          evidence=[f"a rule mentions it: {mention[:80]}"] if mention else [],
          note="a mention shows roles exist, not which role may do what" if mention else None,
          weak=True)  # fmt: skip
    b.add("persistence:reload", "persistence", "persistence",
          "the data after a reload of the page")  # fmt: skip
    b.add("persistence:new-session", "persistence", "persistence",
          "the data in a new session (a fresh browser context)")  # fmt: skip
    # model-level assumptions
    if not (model.stack.frontend or {}).get("marker"):
        b.add("assumption:stack", "assumption", "assumptions",
              "technology stack: the default stack is used", assumed=True)  # fmt: skip
    for gap in brief.gaps if brief else []:
        if re.search(r"\bassumed\b", gap):
            b.add(f"assumption:{hashlib.sha1(gap.encode()).hexdigest()[:6]}", "assumption",
                  "assumptions", gap[:200], assumed=True)  # fmt: skip
    if brief:
        for s in brief.out_screens:
            made = b.add(f"screen:{s.path or s.name}", "screen", "screens",
                         f"screen {s.path or s.name}", evidence=_from_text(s))  # fmt: skip
            if made:
                made["brief_out"] = True
        for r in brief.out_routes:
            made = b.add(f"route:{r.key}", "route", "apis", r.key, evidence=_from_text(r))
            if made:
                made["brief_out"] = True
        for t in brief.out_tables:
            made = b.add(f"table:{t.name}", "table", "data", f"table {t.name}",
                         evidence=_from_text(t))  # fmt: skip
            if made:
                made["brief_out"] = True
    return facts, selectors, screen_paths


def _build_notes(root: Path, app_id: str, b: _Builder) -> None:
    """Assumptions the builder wrote in BUILD-NOTES.md (bullets under an Assumptions heading)."""
    path = root / "apps" / app_id / "BUILD-NOTES.md"
    if not path.exists():
        return
    inside, n = False, 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.match(r"^#{1,6}\s", line):
            inside = bool(re.search(r"assum", line, re.I))
            continue
        if inside and re.match(r"^\s*([-*]|\d+\.)\s+\S", line):
            n += 1
            text = re.sub(r"^\s*([-*]|\d+\.)\s+", "", line).strip()
            b.add(f"assumption:notes-{n}", "assumption", "assumptions",
                  f"BUILD-NOTES.md: {text[:200]}", assumed=True)  # fmt: skip


def _recording_rows(recs, reports, decl: dict[Path, dict], b: _Builder) -> None:
    """Evidence found in recordings for the category rows, and what the tooling cannot capture."""
    from labctl.behaviour import fidelity
    from labctl.split import TAB_ACTIONS

    benign = set(fidelity.SUPPORTED) | set(fidelity.ALSO_PERFORMED) | set(TAB_ACTIONS)
    unsupported: dict[str, list[str]] = {}
    for rec in recs:
        for d in _raw_steps(rec):
            action = str(d.get("action") or "")
            if d.get("action") == "goto" and d.get("nav") == "reload":
                row = next((r for r in b.rows if r["id"] == "persistence:reload"), None)
                if row is not None:
                    row["evidence"].append(f"reload recorded at step {d.get('seq')} ({rec.name})")
            if action and action not in benign:
                unsupported.setdefault(action, []).append(f"{rec.name} step {d.get('seq')}")
    for name, (_, report) in reports.items():
        for s in report.steps:
            if s.outcome == "unsupported":
                unsupported.setdefault(s.action, []).append(f"{name} step {s.seq}")
    for action, where in unsupported.items():
        b.add(f"unsupported:{action}", "unsupported", "unsupported",
              f"recorded action '{action}' cannot be replayed ({len(where)} step(s))",
              evidence=where[:4], unsupported=True)  # fmt: skip
    for d in decl.values():
        for text, source in d["unsupported"]:
            b.add(f"unsupported:{_slug(text)}", "unsupported", "unsupported", text,
                  evidence=[source], unsupported=True)  # fmt: skip


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "item"


# --- status --------------------------------------------------------------------------------------


def _excluded_by(row: dict, exclusions: list[tuple[str, str]]) -> tuple[str, str] | None:
    for text, source in exclusions:
        t = text.strip().lower()
        if t and (t == row["id"].lower() or t in row["label"].lower()
                  or row["id"].lower().endswith(":" + t)):  # fmt: skip
            return (text, source)
    return None


def _status(row: dict, assertions: list[Assertion], cited: list[str]) -> Row:
    evidence = list(dict.fromkeys(row["evidence"] + cited))
    out = Row(id=row["id"], kind=row["kind"], category=row["category"], label=row["label"],
              required=row["required"], evidence=evidence, note=row["note"],
              status="UNOBSERVED")  # fmt: skip
    results = [a.result for a in assertions]
    if assertions:
        out.assertion = "; ".join(a.id for a in assertions[:3]) + (
            f" (+{len(assertions) - 3} more)" if len(assertions) > 3 else "")  # fmt: skip
        order = ("FAIL", "BLOCKED", "SKIPPED", None, "PASS")
        out.assertion_result = min(results, key=lambda r: order.index(r) if r in order else 3)
        if "PASS" in results and all(r in ("PASS", None) for r in results):
            out.assertion_result = "PASS"
    if row["unsupported"]:
        out.status = "UNSUPPORTED"
    elif not evidence:
        out.status = "ASSUMED" if row["assumed"] else "UNOBSERVED"
        if assertions and out.assertion_result == "PASS":
            out.note = ((out.note + "; ") if out.note else "") + (
                "a case passes but cites no evidence for what it expects: cite where the "
                "expectation comes from (evidence:) to count it")  # fmt: skip
    elif row["assumed"] and not cited:
        out.status = "ASSUMED"
    elif not assertions:
        out.status = "EVIDENCED"
    elif row["weak"] and not cited:
        out.status = "EVIDENCED"
        out.note = ((out.note + "; ") if out.note else "") + (
            "the evidence is a pointer, not an answer: a covering case must cite where its "
            "expectation comes from (evidence:)")  # fmt: skip
    elif out.assertion_result == "PASS":
        out.status = "VERIFIED"
    elif out.assertion_result in ("FAIL", "BLOCKED"):
        out.status = "BLOCKED"
        out.note = ((out.note + "; ") if out.note else "") + (
            "an assertion failed" if out.assertion_result == "FAIL" else "an assertion could not run")  # fmt: skip
    else:  # declared, but skipped or not run in a current report
        out.status = "EVIDENCED"
        out.note = ((out.note + "; ") if out.note else "") + (
            "an assertion is declared but " + ("was skipped" if out.assertion_result == "SKIPPED"
                                               else "was not run in a current report"))  # fmt: skip
    return out


def requests_for(rows: list[Row]) -> list[Request]:
    """Plain-words asks, for the application owner or for whoever runs the lab."""
    out: list[Request] = []

    def ask(audience: str, row: Row, text: str) -> None:
        if all(r.text != text for r in out):
            out.append(Request(audience=audience, row=row.id, text=text))

    for r in rows:
        if r.status in ("VERIFIED", "EXCLUDED") or not r.required:
            continue
        k, name = r.kind, r.label
        owner_kind = r.status == "UNOBSERVED" or k in ("roles", "concurrency")
        if r.status == "BLOCKED":
            ask("lab", r, f"Fix or review the failing check for: {name} ({r.assertion}).")
        elif r.status == "UNSUPPORTED":
            ask("owner", r, f"{name}: the tooling cannot capture this. Exclude it from scope or "
                            "provide another way to check it.")  # fmt: skip
        elif owner_kind and k == "roles":
            ask("owner", r, "Which roles may view, create, edit or delete each record? Provide a "
                            "role-by-action table, or record the same flow signed in as each role.")  # fmt: skip
        elif owner_kind and k == "boundary":
            ask("owner", r, "What are the limits (required, length, range, format) of the fields "
                            f"on {r.id.split(':', 1)[1]}?")  # fmt: skip
        elif owner_kind and r.id.startswith("negative:validation"):
            ask("owner", r, f"Record a failed submission (a missing or invalid value) for "
                            f"{r.id.split(':', 2)[2]} and what the user sees.")  # fmt: skip
        elif owner_kind and r.id.startswith("negative:not-found"):
            ask("owner", r, f"Provide the response body and status for an unknown id on "
                            f"{r.id.split(':', 2)[2]}.")  # fmt: skip
        elif owner_kind and r.id == "negative:unauthorised":
            ask("owner", r, "Record a failed sign-in and what a signed-in user without "
                            "permission sees.")  # fmt: skip
        elif owner_kind and k == "errors":
            ask("owner", r, f"Provide the error body (and status) the API returns for "
                            f"{r.id.split(':', 1)[1]}.")  # fmt: skip
        elif owner_kind and r.id == "persistence:reload":
            ask("owner", r, "Record a reload of the page after a change, to show whether the "
                            "change is still there.")  # fmt: skip
        elif owner_kind and r.id == "persistence:new-session":
            ask("owner", r, "Does the data survive a new session (a new browser, another user)? "
                            "Record opening the app fresh after a change.")  # fmt: skip
        elif owner_kind and r.id == "persistence:location":
            ask("owner", r, "Where does the application keep its data: a server database, "
                            "browser storage, or nowhere (it is lost on reload)?")  # fmt: skip
        elif owner_kind and k == "concurrency":
            ask("owner", r, f"How should the application behave when two users change the same "
                            f"record at once ({r.id.split(':', 1)[1]})?")  # fmt: skip
        elif r.status == "EVIDENCED":
            same = [x for x in rows if x.status == "EVIDENCED" and x.category == r.category]
            first = ", ".join(x.id for x in same[:3]) + (" ..." if len(same) > 3 else "")
            ask("lab", r, f"Add acceptance cases, with synthetic expected values, that check the "
                          f"{len(same)} {r.category} row(s) that are seen but not checked "
                          f"({first}); name each row under covers: in acceptance.yaml.")  # fmt: skip
        elif k == "assumption":
            ask("owner", r, f"Confirm or correct this assumption: {name}")
        elif r.status == "ASSUMED":
            ask("owner", r, f"Provide evidence (a recording, schema or document) for: {name}")
        else:
            ask("owner", r, f"Provide evidence for: {name}")
    return out


def _counts(rows: list[Row]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    total = {s: 0 for s in STATUSES}
    cats: dict[str, dict[str, int]] = {}
    for r in rows:
        total[r.status] += 1
        cats.setdefault(r.category, {s: 0 for s in STATUSES})[r.status] += 1
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return total, dict(sorted(cats.items(), key=lambda kv: order.get(kv[0], 99)))


def claim_of(rows: list[Row], notes: list[str], verdicts: list[str]) -> tuple[bool, str, list[str]]:
    """The claim line, and the exact shortfall when it cannot be made."""
    required = [r for r in rows if r.required]
    ok_status = [r for r in required if r.status in ("VERIFIED", "EXCLUDED")]
    verified = sum(r.status == "VERIFIED" for r in required)
    excluded = sum(r.status == "EXCLUDED" for r in required)
    short: list[str] = []
    labels = {"EVIDENCED": "seen but not checked by a passing assertion", "ASSUMED": "assumed",
              "UNOBSERVED": "unobserved", "UNSUPPORTED": "unsupported", "BLOCKED": "blocked"}  # fmt: skip
    for status, label in labels.items():
        n = sum(r.status == status for r in required)
        if n:
            short.append(f"{n} {label} ({status})")
    failing = sum(r.assertion_result == "FAIL" for r in required)
    skipped = sum(r.assertion_result == "SKIPPED" for r in required)
    not_run = sum(bool(r.status == "EVIDENCED" and r.assertion and r.assertion_result is None)
                  for r in required)  # fmt: skip
    if failing:
        short.append(f"{failing} failing")
    if skipped:
        short.append(f"{skipped} skipped")
    if not_run:
        short.append(f"{not_run} with an assertion not run in a current report")
    bad = [v for v in verdicts if v != "PASS WITHIN DECLARED SCOPE"]
    if bad:
        short.append("the newest current report(s) are " + ", ".join(sorted(set(bad))))
    if not required:
        short.append("no requirement was found: nothing to claim")
    claim = bool(required) and len(ok_status) == len(required) and not failing and not skipped \
        and not bad and verified > 0  # fmt: skip
    if claim:
        text = (f"{CLAIM}: {verified} required row(s) VERIFIED"
                + (f", {excluded} EXCLUDED by declaration" if excluded else "")
                + f", of {len(required)} in the declared scope. This is not a claim of complete "
                "equivalence to the original application.")  # fmt: skip
    else:
        text = (f"Not claimable: {CLAIM!r} needs every required row VERIFIED or EXCLUDED. Of "
                f"{len(required)} required row(s): {verified} VERIFIED, {excluded} EXCLUDED; "
                "short by " + (", ".join(short) or "nothing counted") + ".")  # fmt: skip
    return claim, text, short


# --- build, keep, freshness ---------------------------------------------------------------------


def fingerprint(root: Path, app_id: str) -> dict:
    from labctl import conformance
    from labctl.behaviour import durable, fidelity

    folder = root / "apps" / app_id
    facts = conformance.app_facts(root, app_id)
    recs = _recordings(root, app_id)
    files = {}
    for rec in recs:
        for fname in (
            "bindings.yaml",
            "acceptance.yaml",
            "baseline.yaml",
            "visual.yaml",
            SCOPE_FILE,
        ):
            files[f"{rec.name}/{fname}"] = _digest(rec / fname)
    sources = {}
    for sub in ("automations", "flows"):
        for path in sorted((root / sub).glob("**/*.yaml")) if (root / sub).is_dir() else []:
            text = path.read_text(encoding="utf-8", errors="replace")
            if app_id in text:
                sources[path.relative_to(root).as_posix()] = _digest(path)
    names = sorted(p.stem for p in fidelity.reports_dir(root, app_id).glob("*--*.json"))
    return {
        "app": {k: facts.get(k) for k in ("app_yaml", "brief", "schema")},
        "model": _digest(folder / "model.yaml"),
        "build_notes": _digest(folder / "BUILD-NOTES.md"),
        "recordings": {r.name: durable.recording_digest(r) for r in recs},
        "declarations": files,
        "sources": sources,
        "reports": names,
    }


def build(
    root: Path, app_id: str, *, conformance_items: list[dict] | None = None
) -> CoverageReport:
    """The matrix for one app, from what exists on disk now."""
    root = Path(root)
    recs = _recordings(root, app_id)
    b = _Builder()
    facts, selectors, screens = _model_rows(root, app_id, b)
    reports, notes = current_reports(root, app_id)
    decl = {rec: declarations(rec) for rec in recs}
    _build_notes(root, app_id, b)
    _recording_rows(recs, reports, decl, b)
    conf = conformance_results(root, app_id) if conformance_items is None else conformance_items
    idx = _assertions(root, app_id, recs, reports, conf, selectors, screens)
    for rec, d in decl.items():
        notes += [f"{rec.name}: {p}" for p in d["problems"]]

    exclusions = [x for d in decl.values() for x in d["excluded"]]
    used = set()
    rows: list[Row] = []
    for raw in b.rows:
        assertions = idx.get(raw["id"])
        cited = [f"cited with {a.id}: {a.cited}" for a in assertions if a.cited and a.result]
        row = _status(raw, assertions, cited)
        if raw.get("brief_out"):
            row.status = "EXCLUDED"
            row.declaration = "BRIEF.md, Out of scope: needed by no registered automation or flow"
            row.assertion = row.assertion_result = None
        else:
            hit = _excluded_by(raw, exclusions)
            if hit:
                used.add(hit)
                row.status = "EXCLUDED"
                row.declaration = f"declared out of scope: '{hit[0]}' ({hit[1]})"
        rows.append(row)
    for text, source in exclusions:
        if (text, source) not in used:
            rows.append(Row(id=f"excluded:{_slug(text)}", kind="exclusion", category="exclusions",
                            label=text, status="EXCLUDED",
                            declaration=f"declared out of scope: '{text}' ({source})"))  # fmt: skip
    for d in decl.values():  # declared unobserved/assumed items are shortfalls, listed by name
        for text, source in d["unobserved"]:
            rows.append(Row(id=f"unobserved:{_slug(text)}", kind="declared", category="exclusions",
                            label=text, status="UNOBSERVED", evidence=[],
                            note=f"declared unobserved ({source})"))  # fmt: skip
        for text, source in d["assumed"]:
            rows.append(Row(id=f"assumed:{_slug(text)}", kind="declared", category="assumptions",
                            label=text, status="ASSUMED", note=f"declared assumed ({source})"))  # fmt: skip
    rows = list({r.id: r for r in rows}.values())

    total, cats = _counts(rows)
    verdicts = [(getattr(rep, "verdict", None) or str(rep.status).upper())
                for _, rep in reports.values()]  # fmt: skip
    claim, text, short = claim_of(rows, notes, verdicts)
    return CoverageReport(
        app=app_id, generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        fingerprint=fingerprint(root, app_id), rows=rows, counts=total, by_category=cats,
        denominator=len(rows), required=sum(r.required for r in rows), claim=claim,
        claim_text=text, shortfall=short, requests=requests_for(rows),
        reports_used=[n for n, _ in reports.values()], notes=notes,
    )  # fmt: skip


def _folder(root: Path) -> Path:
    path = _state(root) / "coverage"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(root: Path, report: CoverageReport) -> Path:
    folder = _folder(root)
    (folder / f"{report.app}.md").write_text(render(report), encoding="utf-8", newline="\n")
    path = folder / f"{report.app}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_saved(root: Path, app_id: str) -> CoverageReport | None:
    path = _folder(root) / f"{app_id}.json"
    try:
        return CoverageReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def freshness(root: Path, app_id: str, report: CoverageReport) -> tuple[str, list[str]]:
    """(`current` | `stale`, reasons): the kept matrix against what exists now."""
    now = fingerprint(root, app_id)
    then = report.fingerprint or {}
    reasons: list[str] = []
    labels = {"app": "the app's description, brief or schema changed", "model": "the model changed",
              "build_notes": "BUILD-NOTES.md changed"}  # fmt: skip
    for key, text in labels.items():
        if now.get(key) != then.get(key):
            reasons.append(text)
    for key, text, _noun in (("recordings", "evidence", "recording"),
                            ("declarations", "declared cases", "declaration file"),
                            ("sources", "automation or flow", "file")):  # fmt: skip
        a, b = then.get(key) or {}, now.get(key) or {}
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if changed:
            reasons.append(f"the {text} changed: " + ", ".join(changed[:4])
                           + (" ..." if len(changed) > 4 else ""))  # fmt: skip
    if then.get("reports") != now.get("reports"):
        reasons.append("a replay report was added or removed")
    return ("stale" if reasons else "current"), reasons


# --- text ------------------------------------------------------------------------------------


def render(report: CoverageReport) -> str:
    esc = lambda s: str(s or "").replace("|", "/").replace("\n", " ")  # noqa: E731
    lines = [f"# Coverage matrix: {report.app}", "", f"Generated {report.generated_at}.", "",
             f"**{report.claim_text}**", ""]  # fmt: skip
    lines += [
        "Denominator: "
        + f"{report.denominator} row(s), {report.required} required. "
        + ", ".join(f"{n} {s}" for s, n in report.counts.items() if n),
        "",
    ]
    lines += ["| Category | " + " | ".join(STATUSES) + " |", "|---|" + "---|" * len(STATUSES)]
    for cat, c in report.by_category.items():
        lines.append(f"| {cat} | " + " | ".join(str(c[s]) for s in STATUSES) + " |")
    lines.append("")
    if report.notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in report.notes] + [""]
    lines += ["## Evidence requests", ""]
    if report.requests:
        for r in report.requests:
            who = "Ask the application owner" if r.audience == "owner" else "For the lab"
            lines.append(f"- {who}: {r.text}")
    else:
        lines.append("- None: every required row is verified or excluded.")
    lines += ["", "## Rows", "", "| Status | Category | Requirement | Evidence | Assertion | Note |",
              "|---|---|---|---|---|---|"]  # fmt: skip
    for r in report.rows:
        lines.append(f"| {r.status} | {r.category} | {esc(r.label)} | {esc('; '.join(r.evidence[:3]))} "
                     f"| {esc(r.assertion)}{' (' + r.assertion_result + ')' if r.assertion_result else ''} "
                     f"| {esc(r.declaration or r.note)} |")  # fmt: skip
    return "\n".join(lines) + "\n"
