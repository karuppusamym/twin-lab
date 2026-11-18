"""What to do next, in plain words: the onboarding checklist and each app's progress.

Pure functions over facts the portal already has, so the pages and the tests agree on them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Step:
    id: str
    title: str
    done: bool
    hint: str  # what to do, in one plain sentence
    href: str
    optional: bool = False
    state: str = "todo"  # done | next | todo | optional


def mark(steps: list[Step]) -> list[Step]:
    """The first required step not done is `next`; the rest keep done/todo/optional."""
    seen_next = False
    for s in steps:
        if s.done:
            s.state = "done"
        elif s.optional:
            s.state = "optional"
        elif not seen_next:
            s.state, seen_next = "next", True
        else:
            s.state = "todo"
    return steps


def next_step(steps: list[Step]) -> Step | None:
    return next((s for s in steps if s.state == "next"), None)


def completed_automations(sessions: list[dict]) -> list[str]:
    """An active or anonymous session does not complete a bot's first run."""
    return sorted({s["automation"] for s in sessions if s.get("ended") and s.get("automation")})


def app_steps(
    app_id: str,
    *,
    has_evidence: bool,
    has_brief: bool,
    built: bool,
    running: bool,
    automations: list[str],
    ready: list[str],
    has_baseline: bool,
    sessions: int,
    declared: list[str],
    recorded_automations: list[str] | None = None,
    recheck: dict[str, str] | None = None,
) -> list[Step]:
    """Progress of one app, in the order the app page is laid out.

    `ready` holds only bots whose READY is still current. `recheck` maps a bot whose earlier READY
    can no longer be relied on to why (the app was rebuilt, the lab cannot be read, ...)."""
    base = f"/apps/{app_id}"
    recheck = recheck or {}
    not_ready = [a for a in automations if a not in ready]
    again = [a for a in not_ready if a in recheck]
    never = [a for a in not_ready if a not in recheck]
    if again:
        readiness_hint = "Check readiness again: " + "; ".join(f"{a} ({recheck[a]})" for a in again)
        readiness_hint += "." + (f" Not checked yet: {', '.join(never)}." if never else "")
    elif not_ready:
        readiness_hint = (
            "Check readiness: does each bot find what it needs? Not ready yet: "
            + ", ".join(not_ready)
        )
    else:
        readiness_hint = "Check readiness for each bot."
    undeclared = [a for a in automations if a not in declared]
    unrecorded = [a for a in automations if a not in (recorded_automations or [])]
    steps = [
        Step("evidence", "Evidence", has_evidence,
             "Add what you know about the real app: a recording of the flow is best, the "
             "database schema helps.", f"{base}#evidence"),
        Step("brief", "Brief", has_brief,
             "Generate the brief: the build instructions, made from the evidence.", f"{base}#brief"),
        Step("build", "Build", built,
             "Have the app built: start a Devin build, or download the package for Devin.",
             f"{base}#build"),
        Step("lab", "Lab", running, "Start the app's lab (the first start takes a few minutes).",
             f"{base}#lab"),
        Step("readiness", "Readiness", bool(automations) and not not_ready,
             readiness_hint, "/mapping", optional=not automations),
        Step("data", "Data", has_baseline,
             "Load data: your own Excel sheet, synthetic data, or a saved pack.", f"{base}#data"),
        Step("conditions", "Conditions", False,
             "Optional: make the app slow or flaky to see how bots cope.", f"{base}#conditions",
             optional=True),
        Step("sessions", "Sessions", sessions > 0 and not undeclared and not unrecorded,
             ("Record a run of each bot and accept its changes, so later runs get PASS or FAIL."
              + (f" Still to record: {', '.join(unrecorded)}." if unrecorded else "")
              + (f" Still to accept: {', '.join(undeclared)}." if sessions and undeclared else "")),
             f"{base}#sessions", optional=not automations),
    ]  # fmt: skip
    return mark(steps)


def onboarding(
    *,
    named: bool,
    automations: int,
    flows: int,
    evidence: bool,
    briefs: bool,
    built: bool,
    running: bool,
    ready: bool,
    declared: bool,
    first_app: str | None,
    has_baseline: bool = False,
    recheck: str = "",
) -> list[Step]:
    """The whole path for someone new, ticked off from what exists. `recheck` says why an earlier
    READY can no longer be relied on, when there is one."""
    app = f"/apps/{first_app}" if first_app else "/"
    steps = [
        Step("you", "Say who you are", named,
             "Type your name at the top of the page. It shows who reserved a lab or accepted "
             "a change.", "#you"),
        Step("automation", "Register a bot", automations + flows > 0,
             "Register an automation (upload a UiPath project as a zip), or write down a flow "
             "when there is no bot code.", "/automations"),
        Step("evidence", "Add evidence", evidence,
             "On the app's page, record the flow in the real app or upload its database schema.",
             f"{app}#evidence"),
        Step("brief", "Generate the brief", briefs,
             "Turn the evidence into build instructions. Read them: every line says where it came "
             "from.", f"{app}#brief"),
        Step("build", "Build the app", built,
             "Devin builds the demo app and opens a pull request; merge it, then get the latest "
             "from GitHub (Settings).", f"{app}#build"),
        Step("lab", "Start the lab", running,
             "Start the app with its database and lab agent. You get an address bots can use.",
             f"{app}#lab"),
        Step("ready", "Check readiness", ready,
             (f"Check readiness again: {recheck}." if recheck else
              "Readiness tells you, per bot, READY or exactly what is missing."), "/mapping",
             optional=automations == 0),
        Step("data", "Prepare test data", has_baseline,
             "Load a synthetic data pack or your test workbook before recording a run.",
             f"{app}#data"),
        Step("verdict", "Record and judge a run", declared,
             "Record a session while the bot runs, read what changed, and accept it. Every "
             "later run checks for unexpected database changes. Confirm the business outcome separately.",
             f"{app}#sessions", optional=automations == 0),
    ]  # fmt: skip
    return mark(steps)


GLOSSARY = [
    ("Automation", "A bot (UiPath, Python, an agent) registered here, with what it touches: "
                   "screens, API calls, tables."),
    ("Flow", "A written-down path through an app, for when there is no bot code."),
    ("Evidence", "What we know about the real app: recordings, database schema, API "
                 "descriptions, screenshots. It stays on this machine."),
    ("Brief", "Build instructions for the demo app, generated from evidence. Every line says "
              "where it came from."),
    ("Demo app", "A copy of the real application, built for the bots that need it, with "
                 "synthetic or masked data."),
    ("Lab", "A running demo app with its own database and a lab agent in front that can slow "
            "it down, reset its data and record what happens."),
    ("Readiness", "Whether a bot finds every screen, field, API and table it needs: READY or "
                  "BLOCKED with the reason."),
    ("Condition", "A way of running the app: normal, peak hour, slow database, errors now and "
                  "then."),
    ("Session", "A recording of one bot run in the lab: every request, its waiting time, every "
                "row changed, every table used."),
    ("Verdict", "PASS when a run changed only what the bot is expected to change; FAIL names "
                "each unexpected change."),
    ("Baseline", "The data Reset returns to. Earlier baselines are kept; pin or quarantine "
                 "them."),
    ("Data pack", "A named copy of a lab's data (\"month-end\") to load again later."),
    ("Reservation", "A lab reserved by one person for a while: others can look but not change "
                    "it."),
    ("Parity", "A production run of a bot compared with the same bot's run in the lab."),
    ("Fidelity replay", "A recording of the real app replayed against the demo app, step by "
                        "step, to find differences."),
    ("Recording across applications", "One recorded flow that goes through several "
                                      "applications and tabs; split into one recording per "
                                      "application, the order across them kept."),
]  # fmt: skip
