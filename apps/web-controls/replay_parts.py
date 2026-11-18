import json
from pathlib import Path

from labctl.behaviour.bindings import Bindings
from labctl.behaviour.fidelity import replay

ROOT = Path.cwd()
values = {
    6: "Option 1",
    7: "Option 2",
    13: "Synthetic trial",
    14: "Synthetic multi application test",
    15: "Two",
    26: "Option 1",
    28: "Second synthetic visit",
}
for app, port in [("web-controls", 8737), ("web-form", 8738)]:
    rec = ROOT / "evidence" / app / "combined"
    steps = [json.loads(s) for s in (rec / "steps.jsonl").read_text(encoding="utf-8").splitlines()]
    bnd = Bindings(
        values={s["seq"]: values[s["global_seq"]] for s in steps if s["global_seq"] in values}
    )
    out = ROOT / "apps" / app / "validation"
    out.mkdir(exist_ok=True)
    report = replay(rec, base_url=f"http://127.0.0.1:{port}", bindings=bnd, stop_on_fail=False)
    (out / "raw-replay.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out / "raw-replay.md").write_text(report.markdown(), encoding="utf-8")
    print(app, report.status, report.coverage, report.problems, flush=True)
