"""Record Caseboard PF, build its package and replay the recording on a fresh instance.

    python reference/web/caseboard-primefaces/certify_local.py [--keep FOLDER]

1. RECORD: flow.py with the lab's recorder, then the bindings (every typed value, every row
   clicked by its title), as a person writes them before a replay.
2. PACKAGE: `labctl evidence build-all` on a scratch repository: the navigation map, the page
   snapshots and the page documents. Every sub-page, tab, wizard step, accordion panel, dialog,
   dialog-framework page and table state must be a state of its screen with its opener and its
   own snapshot and page-document section, and the forwards must be transitions.
3. SELF-REPLAY: the recording against a FRESH instance, with no failed step.

Prints what each gate found and exits 0 only when all pass. `labctl` is imported from this
checkout (lab/labctl), whatever is installed.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "lab" / "labctl"))

import yaml  # noqa: E402

APP = "caseboard-pf"


def _sibling(name: str):
    """A module of this folder, loaded by its path under a name of its own: `flow`, `run` and
    `build` are names other folders of the repository use too."""
    import importlib.util

    full = f"caseboard_pf_{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


flow_mod = _sibling("flow")
run_mod = _sibling("run")

# What the package must hold for the workspace: (kind, title) of each state flow.py reaches.
# Titles are interface text as the sanitiser leaves it (a word the data also holds is masked).
EXPECTED_KINDS = {
    "sub-page (ajax region)": 4,  # cases, a case's detail, new case, reports
    "tab": 3,  # summary, notes, history
    "wizard step": 3,  # basics, owner, confirm
    "accordion panel": 2,  # by owner, by priority
    "dialog": 1,  # quick note
    "confirm dialog": 1,  # close case
    "dialog-framework page (iframe)": 1,  # the owner picker
    "table page": 3,  # pages 1, 2, 3
    "table sort": 4,  # unsorted, number ascending and descending, owner ascending
    "table result (filter)": 2,  # every case, the filtered ones
    "table row expansion": 1,
    "table row selection": 1,
}
FORWARDS = [
    ("/login.xhtml", "/workspace.xhtml", "/login.xhtml"),
    ("/workspace.xhtml", "/case.xhtml", "/workspace.xhtml"),
    ("/case.xhtml", "/workspace.xhtml", "/case.xhtml"),
]


def steps_of(rec: Path) -> list[dict]:
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def record(base: str, folder: Path) -> Path:
    from labctl import recorder

    recorder.record(base + flow_mod.START, folder, headless=True,
                    script=lambda page: flow_mod.flow(page, base))  # fmt: skip
    steps = steps_of(folder)
    typed = [s["seq"] for s in steps if s["action"] in ("type", "select")]
    rows = [s["seq"] for s in steps if (s.get("locators") or [{}])[0].get("kind") == "record"]
    (folder / "bindings.yaml").write_text(
        yaml.safe_dump(flow_mod.bindings_for(typed, rows), sort_keys=False), "utf-8"
    )
    return folder


def _root(work: Path) -> Path:
    root = work / "repo"
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (root / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "AGENTS.md", root / "AGENTS.md")
    shutil.copy(ROOT / "docs" / "factory-playbook.md", root / "docs" / "factory-playbook.md")
    return root


def package(root: Path, rec: Path) -> dict:
    """Gate 2. build-all with the proposal accepted and the application named `caseboard-pf`."""
    from labctl import multiapp

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        multiapp.build_all(root, rec, say=lambda m: None)
        path = multiapp.proposal_file(rec)
        data = yaml.safe_load(path.read_text("utf-8"))
        data["apps"][0]["id"] = APP
        path.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
        summary = multiapp.build_all(root, rec, proposal=path, say=lambda m: None,
                                     package_dir=root / "packages")  # fmt: skip
    app = summary.apps[0]
    with zipfile.ZipFile(root / app.package) as zf:
        texts = {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}
    nav = json.loads(texts[f"apps/{APP}/navigation.json"])
    screens = {s["path"]: s for s in nav["screens"]}
    work = screens.get("/workspace.xhtml") or {}
    states = [v for v in work.get("subviews") or [] if v.get("state_of")]
    kinds: dict[str, int] = {}
    for v in states:
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    docs = {n: t for n, t in texts.items() if n.startswith(f"apps/{APP}/spec/PAGES/")}
    work_doc = next((t for n, t in docs.items() if "workspace" in n), "")
    snapshots = [n for n in texts if n.startswith(f"apps/{APP}/pages/") and n.endswith(".html")]
    found = {
        "errors": app.errors,
        "screens": sorted(screens),
        "states": len(states),
        "kinds": kinds,
        "without_opener": [
            f"{v['kind']} {v.get('title')}"
            for v in states
            if not v["opened_by"] and v.get("first_seen") != "when the page loads"
        ],  # fmt: skip
        "without_snapshot": [
            f"{v['kind']} {v.get('title')}" for v in states if v.get("snapshot") not in texts
        ],  # fmt: skip
        "without_section": [
            f"{v['kind']} {v.get('title')}" for v in states if f"### {v['kind']}" not in work_doc
        ],  # fmt: skip
        "transitions": [(t["from"], t["to"], t.get("address_after")) for t in nav["transitions"]],
        "address_lag": (nav.get("jsf") or {}).get("address_lag") or [],
        "partial_actions": len((nav.get("jsf") or {}).get("partial_actions") or []),
        "snapshots": len(snapshots),
        "page_documents": len(docs),
        "widgets": len(json.loads(texts[f"apps/{APP}/codemap.json"]).get("widgets") or []),
    }
    forwards = [t for t in found["transitions"] if t[2]]
    found["ok"] = (
        not app.errors
        and all(kinds.get(k, 0) >= n for k, n in EXPECTED_KINDS.items())
        and not found["without_opener"]
        and not found["without_snapshot"]
        and not found["without_section"]
        and all(f in forwards for f in FORWARDS)
    )
    found["navigation"] = nav
    return found


def replay(rec: Path, source_base: str, replica_base: str):
    from labctl.behaviour import fidelity

    return fidelity.replay(rec, origins={source_base: replica_base}, resolve_timeout_ms=5000)


def replay_summary(report) -> dict:
    cov = report.coverage
    failing = next((s for s in report.steps if s.status in ("fail", "blocked")), None)
    return {
        "status": report.status,
        "steps": cov.steps,
        "replayed": cov.replayed,
        "failed": cov.failed,
        "skipped": cov.skipped,
        "blocked": cov.unresolved + cov.missing_value + cov.unsupported + cov.unmapped,
        "differences": [d.text() for s in report.steps for d in s.differences],
        # a request refused to the source (or an origin with no replica) fails a replay whose
        # steps all passed: the summary says which, and during which step
        "blocked_requests": list(report.blocked_requests)[:20],
        "problems": list(report.problems)[:10],
        "first_failing_step": None if failing is None else {
            "seq": failing.seq, "action": failing.action, "selector": failing.selector,
            "status": failing.status, "note": failing.note,
            "differences": [d.text() for d in failing.differences][:5],
        },
        "ok": cov.failed == 0 and cov.skipped == 0 and cov.replayed == cov.steps,
    }  # fmt: skip


def run_gates(work: Path) -> dict:
    work = Path(work)
    root = _root(work)
    from labctl import multiapp

    rec = multiapp.recordings_dir(root) / "pf-run"
    source = run_mod.serve()
    try:
        record(source.base, rec)
    finally:
        source.stop()
    result = {"recording": str(rec), "package": package(root, rec)}
    fresh = run_mod.serve()
    try:
        report = replay(rec, source.base, fresh.base)
    finally:
        fresh.stop()
    (work / "replay-report.md").write_text(report.markdown(), "utf-8")
    result["replay"] = replay_summary(report)
    result["ok"] = result["package"]["ok"] and result["replay"]["ok"]
    return result


def print_result(result: dict) -> None:
    p, r = result["package"], result["replay"]
    mark = {True: "PASS", False: "FAIL"}
    print(f"PACKAGE      {mark[p['ok']]}")
    print(f"  screens {', '.join(p['screens'])}; states in the workspace {p['states']}; "
          f"snapshots {p['snapshots']}; page documents {p['page_documents']}; "
          f"partial actions {p['partial_actions']}; widgets {p['widgets']}")  # fmt: skip
    for kind, n in sorted(p["kinds"].items()):
        print(f"    {kind}: {n} (expected at least {EXPECTED_KINDS.get(kind, 0)})")
    for t in p["transitions"]:
        print(f"  transition {t[0]} -> {t[1]}" + (f" (forward: address {t[2]})" if t[2] else ""))
    for what in ("without_opener", "without_snapshot", "without_section"):
        if p[what]:
            print(f"  {what.replace('_', ' ')}: {', '.join(p[what])}")
    print(f"SELF-REPLAY  {mark[r['ok']]}")
    print(f"  status {r['status']}: steps {r['steps']}, replayed {r['replayed']}, failed "
          f"{r['failed']}, skipped {r['skipped']}, blocked {r['blocked']}")  # fmt: skip
    if r["first_failing_step"]:
        print(f"  first failing step: {json.dumps(r['first_failing_step'], indent=2)}")
    print(f"recording: {result['recording']}")
    print("ALL GATES PASS" if result["ok"] else "NOT CERTIFIED")


def main(argv: list[str]) -> int:
    reason = run_mod.build_mod.unavailable()
    if reason:
        print(f"cannot run: {reason}")
        return 2
    if "--keep" in argv:
        work = Path(argv[argv.index("--keep") + 1]).resolve()
        work.mkdir(parents=True, exist_ok=True)
        result = run_gates(work)
    else:
        with tempfile.TemporaryDirectory(prefix="caseboard-pf-") as tmp:
            result = run_gates(Path(tmp))
        result["recording"] = "removed with its temporary folder (keep it with --keep FOLDER)"
    print_result(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
