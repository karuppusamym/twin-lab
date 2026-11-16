"""Refresh (or check) the harness fixtures in `lab/labctl/tests/fixtures/botlog/`.

The robot log and the recordings there were made by running the harness bot against the showcase
demo app. When the app, the automation or the harness bot changes, run this:

    python tools/refresh_fixtures.py            # check: run the harness, compare, change nothing
    python tools/refresh_fixtures.py --write    # replace the fixtures with the new run

It resets the showcase lab's data before and after (the runs change cases), runs the harness bot
with the fixture's own `bindings.json` and `inputs.json`, and compares what matters to the tests:
per run, the activities and their states in the robot log, and the steps (action, selector) of
each recording. Timestamps, job ids and recording ids always differ and are not compared.

`netlog.json` cannot be made by the harness (it needs Chrome's own --log-net-log) and is left as
it is. The showcase lab must be running (`labctl lab up showcase`).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "lab" / "labctl" / "tests" / "fixtures" / "botlog"
AUTOMATION = ROOT / "automations" / "uipath-case-update"
try:
    import labctl  # noqa: F401
except ImportError:
    sys.path.insert(0, str(ROOT / "lab" / "labctl"))

from labctl import lab as lab_mod  # noqa: E402


def harness():
    spec = importlib.util.spec_from_file_location("harness_bot", ROOT / "tools" / "harness_bot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def log_shape(path: Path) -> list[list[tuple[str, str]]]:
    """Per job: (display name, state) of each activity line, in order."""
    jobs: dict[str, list[tuple[str, str]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        info = rec.get("activityInfo")
        if info:
            jobs.setdefault(rec.get("jobId", ""), []).append(
                (info.get("DisplayName", ""), info.get("State", ""))
            )
    return list(jobs.values())


def recording_shape(folder: Path) -> list[list[tuple[str, str | None]]]:
    out = []
    for rec in sorted(p for p in folder.iterdir() if (p / "steps.jsonl").exists()):
        steps = [json.loads(s) for s in (rec / "steps.jsonl").read_text().splitlines() if s]
        out.append([(s.get("action"), s.get("selector")) for s in steps])
    return out


def compare(what: str, old, new) -> list[str]:
    problems = []
    if len(old) != len(new):
        problems.append(f"{what}: {len(old)} run(s) in the fixture, {len(new)} now")
    for n, (a, b) in enumerate(zip(old, new, strict=False), 1):
        if a != b:
            first = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), None)
            where = (
                f"step {first + 1}: {a[first]} -> {b[first]}"
                if first is not None
                else (f"{len(a)} step(s) before, {len(b)} now")
            )
            problems.append(f"{what}, run {n}: {where}")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--write", action="store_true", help="replace the fixtures with the new run")
    a = p.parse_args(argv)
    agent = lab_mod.agent(ROOT, "showcase")
    try:
        agent.health()
    except lab_mod.LabError as exc:
        print(f"the showcase lab is not running: {exc}", file=sys.stderr)
        return 2
    bindings = json.loads((FIX / "bindings.json").read_text(encoding="utf-8"))
    runs = json.loads((FIX / "inputs.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        agent.reset()
        try:
            results = harness().run(
                AUTOMATION, bindings, runs, tmp_path / "robot.log.jsonl", tmp_path / "showcase"
            )
        finally:
            agent.reset()
        failed = [r for r in results if r["state"] != "Successful"]
        if failed:
            print(f"{len(failed)} harness run(s) did not succeed; fixtures left as they are")
            return 1
        problems = compare(
            "robot log", log_shape(FIX / "robot.log.jsonl"), log_shape(tmp_path / "robot.log.jsonl")
        ) + compare(
            "recordings",
            recording_shape(FIX / "recordings" / "showcase"),
            recording_shape(tmp_path / "showcase"),
        )
        for problem in problems:
            print(f"changed: {problem}")
        if not a.write:
            print("fixtures match the current harness run" if not problems else
                  f"{len(problems)} difference(s); run with --write to replace the fixtures")  # fmt: skip
            return 1 if problems else 0
        shutil.copyfile(tmp_path / "robot.log.jsonl", FIX / "robot.log.jsonl")
        shutil.rmtree(FIX / "recordings" / "showcase")
        shutil.copytree(tmp_path / "showcase", FIX / "recordings" / "showcase")
        print(f"fixtures replaced from {len(results)} run(s); run the tests next")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
