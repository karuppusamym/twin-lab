"""Robot runs in the portal: a robot log in, bot traces, calibration and parity out.

Uploaded logs and their traces stay in `.lab-state/botruns/<automation>/<upload>/` (Git-ignored:
a production log can hold values). The parser already masks fields UiPath marks Private.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

import yaml

from labctl.botlog import netlog as netlog_mod
from labctl.botlog import parity as parity_mod
from labctl.botlog import robotlog
from labctl.botlog import trace as trace_mod
from labctl.botlog.models import BotTrace
from labctl.models import Condition, Session

SOURCES = ("production", "harness", "agent", "unknown")


class RunsError(Exception):
    pass


def folder(state: Path, automation_id: str) -> Path:
    return state / "botruns" / automation_id


def import_log(
    root: Path,
    state: Path,
    automation_id: str,
    log: bytes,
    filename: str,
    netlog: bytes | None = None,
    source: str = "unknown",
) -> dict:
    """Save a robot log (and an optional Chrome NetLog), build its bot traces. Returns a summary."""
    from labctl.botlog.cli import load_analysis

    if source not in SOURCES:
        raise RunsError(f"source must be one of {', '.join(SOURCES)}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    upload = folder(state, automation_id) / f"{stamp}-{secrets.token_hex(2)}"
    upload.mkdir(parents=True)
    suffix = ".jsonl" if filename.lower().endswith((".jsonl", ".json", ".log")) else ".txt"
    log_path = upload / f"robot{suffix}"
    log_path.write_bytes(log)
    requests = None
    if netlog:
        (upload / "netlog.json").write_bytes(netlog)
        requests = netlog_mod.parse(upload / "netlog.json")
    analysis = load_analysis(root / "automations" / automation_id)
    try:
        runs = robotlog.parse(log_path)
    except Exception as exc:  # noqa: BLE001 - any unreadable log is reported, not raised as 500
        raise RunsError(f"the robot log could not be read: {exc}") from exc
    if not runs:
        raise RunsError("no robot runs found in that file (Orchestrator export or NLog JSON lines)")
    traces = [
        trace_mod.build(analysis, run, None, requests, automation=automation_id, source=source)
        for run in runs
    ]
    (upload / "traces.json").write_text(
        json.dumps([t.model_dump(mode="json") for t in traces], indent=1), encoding="utf-8"
    )
    meta = {"upload": upload.name, "file": Path(filename).name, "source": source,
            "runs": len(traces), "at": datetime.now(UTC).isoformat()}  # fmt: skip
    (upload / "upload.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def traces(state: Path, automation_id: str, upload: str) -> list[BotTrace]:
    if not re.match(r"^[\w-]+$", upload):
        raise RunsError("bad upload name")
    path = folder(state, automation_id) / upload / "traces.json"
    if not path.exists():
        raise RunsError(f"no upload {upload}")
    return [BotTrace.model_validate(t) for t in json.loads(path.read_text(encoding="utf-8"))]


def uploads(state: Path, automation_id: str) -> list[dict]:
    """Each upload with its runs (id, steps, gaps, wall time) for the page, newest first."""
    out = []
    base = folder(state, automation_id)
    for meta_file in sorted(base.glob("*/upload.json"), reverse=True) if base.exists() else []:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        runs = []
        for t in traces(state, automation_id, meta["upload"]):
            waits = [s.duration_ms for s in t.steps if s.duration_ms is not None]
            runs.append({"run_id": t.run_id, "steps": len(t.steps), "gaps": len(t.gaps),
                         "total_ms": int(sum(waits)) if waits else None})  # fmt: skip
        out.append({**meta, "run_list": runs})
    return out


def calibrated_condition(state: Path, automation_id: str, upload: str) -> dict:
    """A condition profile whose delays are the waits these runs saw (robot-log timings)."""
    frag = trace_mod.calibrate(traces(state, automation_id, upload))
    if not frag.get("app"):
        raise RunsError("the runs name no application, so the timings belong to nothing")
    profile = {
        "id": f"calibrated-{automation_id}"[:60],
        "description": f"Waits seen in robot runs of {automation_id} ({upload}).",
        "calibration": frag["calibration"],
        "calibrated_from": frag["calibrated_from"],
        "targets": [{"app": frag["app"], "http": frag["http"], "db": frag["db"]}],
    }
    Condition.model_validate(profile)
    return profile


def save_condition(root: Path, profile: dict) -> Path:
    path = root / "conditions" / f"{profile['id']}.yaml"
    path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def run(state: Path, automation_id: str, ref: str) -> BotTrace:
    """`<upload>/<run_id>` -> that run's bot trace."""
    upload, _, run_id = ref.partition("/")
    found = next((t for t in traces(state, automation_id, upload) if t.run_id == run_id), None)
    if found is None:
        raise RunsError(f"no run {run_id} in {upload}")
    return found


def parity(
    state: Path,
    automation_id: str,
    base: str,
    other: str | None = None,
    other_session: dict | None = None,
) -> tuple[str, str]:
    """(status, Markdown report): a run (usually production) against another run of the same bot
    (usually its log from a lab run), with the lab session of that run for the row changes.
    With only a session, screens and values cannot be compared and are reported as skipped."""
    golden = run(state, automation_id, base)
    session = Session.model_validate(other_session) if other_session else None
    if other:
        theirs: BotTrace | Session = run(state, automation_id, other)
    elif session is not None:
        theirs = session
    else:
        raise RunsError("choose another run or a lab session to compare with")
    report = parity_mod.compare(golden, theirs, None, None, session if other else None)
    return report.status, report.markdown()
