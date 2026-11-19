"""What a builder needs beyond the screens: the API contract, the navigation, the actions of each
screen, sign-in and session, and the scope when nobody wrote a flow.

`labctl.factory.generate` calls three functions here, so the logic stays out of the factory:

- `implied_flows`: with no automation and no requested flow, the scope is every screen and action
  the recordings show (one flow per recording, kept in memory, never written);
- `adopt`: before the brief is rendered, take the tables inferred only from session answers out
  of scope (they become `app_users`, `labctl.signin`);
- `extend`: after the schema and the seed are written, write `api/` (`labctl.apicatalog`),
  `navigation.json` (`labctl.navmap`) and the users of the seed (`labctl.signin`), and put their
  sections into the brief.
"""

from __future__ import annotations

import re
from pathlib import Path

RECORDED_BY = "labctl (no flow or automation: every screen and action recorded)"
DRAFTED_BY = "labctl "  # a flow labctl wrote from a recording (build-all's draft, or the above)
SCOPE = "## Scope"
STATE = "_contract_state"


def implied_flows(root: Path, app_id: str) -> list:
    """One flow per recording of the app holding every screen it went through, in order."""
    from labctl import multiapp, stepfacts
    from labctl.appmodel import Flow

    out = []
    for part in stepfacts.parts_of(Path(root), app_id):
        data = multiapp.draft_flow(app_id, part, part.name)
        data["id"] = ("recorded-" + data["id"])[:80].strip("-")
        data["requested_by"] = RECORDED_BY
        data["purpose"] = (f"Everything recording {part.name} shows of {app_id}: no automation "
                           "and no flow written by a person narrows the scope.")  # fmt: skip
        if data["steps"]:
            out.append(Flow.model_validate(data))
    return out


def _sign_in_typed(root: Path, app_id: str) -> bool:
    """Whether a recording typed into a password field (cheap: the step list only)."""
    from labctl import stepfacts

    for part in stepfacts.parts_of(Path(root), app_id):
        for s in stepfacts._steps(part):
            if s.get("action") == "type" and re.search(r"pass|pwd", str(s.get("selector") or ""),
                                                       re.I):  # fmt: skip
                return True
    return False


def adopt(root: Path, app_id: str, brief, model) -> dict:
    """Before the brief is rendered. Returns the state `extend` needs."""
    from labctl import signin

    state: dict = {"taken": {}, "tables": False}
    if brief.browser_only or model.stack.desktop:
        return state
    if _sign_in_typed(root, app_id):
        state["taken"] = signin.adopt(brief)
        state["tables"] = True
    return state


def recorded_scope(root: Path, app_id: str) -> bool:
    """True when no automation and no flow a person wrote sets the scope."""
    from labctl import factory

    if factory.automations(root, app_id):
        return False
    return not any(not f.requested_by.startswith(DRAFTED_BY) for f in factory.flows(root, app_id))


def scope_section(root: Path, app_id: str, brief, ctxs: list) -> str:
    """Who set the scope; with nobody, the recorded screens and actions in recorded order."""
    from labctl import factory, stepfacts

    out = [SCOPE + "\n"]
    if not recorded_scope(root, app_id):
        autos = [a.id for a in factory.automations(root, app_id)]
        flows = [f.id for f in factory.flows(root, app_id)
                 if not f.requested_by.startswith(DRAFTED_BY)]  # fmt: skip
        names = ", ".join(f"`{n}`" for n in [*autos, *flows])
        out.append(f"The scope is what these need: {names}. Screens and actions the recordings "
                   "show beyond that are listed under *Out of scope*.\n")  # fmt: skip
        return "\n".join(out) + "\n"
    out.append("**Every screen and every action recorded.** No automation and no flow written by "
               "a person narrows the scope, so the application to build is everything the "
               "recording(s) show, in the recorded order: every screen, every element on it, "
               "every action taken, every call, every message. Nothing recorded is optional. "
               "What was not recorded is not built: a control nobody used is rendered and does "
               "nothing more (*Actions on each screen* lists each as *seen, not used*).\n")  # fmt: skip
    drafts = [f.id for f in factory.flows(root, app_id)]
    if drafts:
        out.append("The flow file(s) " + ", ".join(f"`flows/{d}.yaml`" for d in drafts)
                   + " were drafted from the recording by `labctl`; they only list its screens.\n")  # fmt: skip
    for c in ctxs:
        steps = c.steps()
        acts = [s for s in steps if s["action"] in stepfacts.USER_ACTIONS
                and not (s["action"] == "submit" and s.get("caused_by"))]  # fmt: skip
        screens: list[str] = []
        for s in steps:
            for p in (s.get("screen"), s.get("then")):
                if p and p not in screens:
                    screens.append(p)
        out.append(f"Recording `{c.name}`: {len(steps)} step(s), {len(acts)} action(s) a person "
                   f"took, {len(screens)} screen(s). In recorded order (the steps: "
                   f"`apps/{app_id}/steps/{c.name}.json`):\n")  # fmt: skip
        run: list[str] = []
        here = None
        n = 0
        for s in acts:
            if s.get("screen") != here:
                if run:
                    n += 1
                    out.append(f"{n}. `{here}`: " + "; ".join(run) + ".")
                here, run = s.get("screen"), []
            text = f"{s['action']} `{s.get('selector') or '-'}`"
            if text not in run:
                run.append(text)
        if run:
            out.append(f"{n + 1}. `{here}`: " + "; ".join(run) + ".")
        out.append("")
    in_scope = {sc.screen.path for sc in brief.screens}
    missed = [p for c in ctxs for s in c.steps() for p in (s.get("screen"), s.get("then"))
              if p and p not in in_scope]  # fmt: skip
    if missed:
        out.append("Recorded but not in scope (a gap: say so in BUILD-NOTES.md): "
                   + ", ".join(f"`{p}`" for p in dict.fromkeys(missed)) + ".\n")  # fmt: skip
    return "\n".join(out) + "\n"


def _insert(text: str, section: str, before: tuple[str, ...]) -> str:
    if not section:
        return text
    for marker in before:
        if marker in text:
            return text.replace(marker, section + marker, 1)
    return f"{text}\n{section}"


def _never_pressed(text: str, nav: dict | None) -> str:
    """The brief's open questions say a button "was never pressed" from the model alone. Drop
    the line when the navigation map shows a use (an element inside a frame), and make the rest
    agree with *Actions on each screen*: rendered, no behaviour."""
    used = set()
    for screen in (nav or {}).get("screens") or []:
        for el in screen.get("elements") or []:
            if isinstance(el.get("used"), list):
                used.add(el["selector"].split(">>>")[-1].strip())
    out = []
    for line in text.split("\n"):
        m = re.match(r"^- (\S+) on (\S+) was never pressed in any recording", line)
        if m and m.group(1) in used:
            continue
        if m:
            line = (f"- {m.group(1)} on {m.group(2)} was never pressed in any recording: what it "
                    "does is unknown. It is rendered and does nothing (*Actions on each screen*: "
                    "seen, not used); record it to have it built")  # fmt: skip
        out.append(line)
    return "\n".join(out)


def extend(root: Path, app_id: str, brief, model, text: str, state: dict | None) -> str:
    """After `db/schema.sql` and the seed are written: the carried files, and the brief text."""
    from labctl import apicatalog, codemap, navmap, signin, templates

    root = Path(root)
    state = state or {"taken": {}, "tables": False}
    out = root / "apps" / app_id
    if model.stack.desktop:
        return text
    done = apicatalog.write(root, app_id)
    ctxs = done.get("ctxs") or []
    if not ctxs:
        (out / navmap.FILE).unlink(missing_ok=True)
        (out / signin.FILE).unlink(missing_ok=True)
        (out / templates.FILE).unlink(missing_ok=True)
        return text
    found = signin.observe(ctxs)
    signin.write_facts(root, app_id, found)
    wrote = None
    if found and state.get("tables"):
        wrote = signin.write(root, app_id, found, state.get("taken") or {},
                             done.get("endpoints") or {})  # fmt: skip
    # What the code the browser downloaded defines (labctl.codemap): codemap.json, and the
    # routes the recording did not visit in navigation.json.
    code, refused = None, None
    try:
        code = codemap.write(root, app_id, ctxs, done, whole=recorded_scope(root, app_id))
    except codemap.CodeMapError as exc:
        refused = exc.places
    nav = navmap.write(root, app_id, brief, ctxs,
                       extend=lambda doc: templates.extend_navigation(
                           codemap.extend_navigation(doc, code) or doc, ctxs))  # fmt: skip
    # Screens as templates, the shared layout, the network behaviour (labctl.templates)
    shaped = templates.write(root, app_id, ctxs, nav, done)
    # sign-in: one section, in place of the paragraph labctl.stepfacts writes
    text = re.sub(r"\*\*Signing in\.\*\* [^\n]*\n\n?", "", text)
    end = ("## Behaviour seen in the recordings", "## Part of a flow across applications",
           "## Running in the lab")  # fmt: skip
    text = _insert(text, apicatalog.brief_section(app_id, done), end)
    text = _insert(text, signin.brief_section(app_id, found, wrote), end)
    text = _insert(text, navmap.brief_sections(app_id, nav), end)
    text = _insert(text, codemap.brief_section(app_id, code, refused), end)
    text = _insert(text, templates.brief_section(app_id, shaped, done.get("network")), end)
    scope = scope_section(root, app_id, brief, ctxs)
    if code and any(r.get("status") == "only in code" for r in code.get("routes") or [])             and recorded_scope(root, app_id):  # fmt: skip
        scope = scope.replace(
            "What was not recorded is not built:",
            "Screens the application's code defines and the recording did not visit are built "
            "too, as reconstructions (*What the application's code defines*). Otherwise, what "
            "was not recorded is not built:", 1)  # fmt: skip
    text = _insert(text, scope, ("## Stack to pin",))
    text = _never_pressed(text, nav)
    if wrote and wrote["rows"]:
        text = text.replace(
            "- Ship no customer data. Load `db/seed.sql` (synthetic) at start-up",
            f"- Users and sessions are in the database (`{signin.USERS}`, `{signin.SESSIONS}`): "
            "see *Sign-in and session*.\n- Ship no customer data. Load `db/seed.sql` (synthetic) "
            "at start-up", 1)  # fmt: skip
    return text
