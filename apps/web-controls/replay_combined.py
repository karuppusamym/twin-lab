from pathlib import Path

from labctl.behaviour.bindings import Bindings
from labctl.behaviour.fidelity import replay

out = Path("apps/web-controls/validation")
b = Bindings(
    values={
        6: "Option 1",
        7: "Option 2",
        13: "Synthetic trial",
        14: "Synthetic multi application test",
        15: "Two",
        26: "Option 1",
        28: "Second synthetic visit",
    }
)
for name, base in [
    ("source-self-replay", None),
    ("combined-single-base-replay", "http://127.0.0.1:8737"),
]:
    r = replay(Path("evidence/multi-app-trial/combined"), base_url=base, bindings=b)
    (out / (name + ".json")).write_text(r.model_dump_json(indent=2), encoding="utf-8")
    (out / (name + ".md")).write_text(r.markdown(), encoding="utf-8")
    print(name, r.status, r.coverage, r.problems, flush=True)
