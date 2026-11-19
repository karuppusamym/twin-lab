"""The desktop parts of the brief (labctl.brief) and of the build task (labctl.builder.package).

A desktop app is rebuilt in the same toolkit, with the same UI Automation properties and control
hierarchy, its data in a local database (SQLite by default), and delivered as a build script and
an installable folder under apps/<app>/: no Dockerfile, no OpenShift.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_DB = "sqlite"

# How to build and package each toolkit (the brief names what the evidence shows).
TOOLCHAIN = {
    "WinForms": ".NET (C#) Windows Forms; `dotnet publish -c Release -r win-x64 --self-contained "
    "true -o apps/<app>/publish/<app>` and a zip of that folder",
    "WPF": ".NET (C#) WPF; `dotnet publish -c Release -r win-x64 --self-contained true` and a zip",
    "UWP / WinUI": "WinUI 3 (Windows App SDK) unpackaged, `dotnet publish` self-contained and a "
    "zip; MSIX only if the real application is packaged",
    "Win32 / MFC": "C++ with MFC (Visual Studio Build Tools, `msbuild`); the .exe and its DLLs in "
    "a zip",
    "Win32": "C or C++ Win32 dialogs (resource-script dialogs keep control ids); a zip",
    "Delphi / VCL": "Delphi VCL (or Free Pascal/Lazarus LCL with the same class names, if no "
    "Delphi licence: say so); a zip",
    "Qt": "Qt Widgets of the same major version, with the accessibility plug-in; "
    "`windeployqt` and a zip",
    "Java Swing / AWT": "Java Swing on the same Java major version; a runnable jar and a "
    "`jpackage` app image; the Java Access Bridge must be enabled to check it",
    "JavaFX": "JavaFX on the same version; `jpackage` app image",
    "VB6": "VB6 is not buildable on a current machine: rebuild in WinForms with the same control "
    "names and Win32 class names where UiPath selectors use them, and say so",
    "PowerBuilder": "PowerBuilder needs its IDE and licence: if unavailable, rebuild the frame "
    "and standard controls in WinForms and list every DataWindow as not reproduced",
}


def is_desktop(model) -> bool:
    return bool(model.stack.desktop) or any(s.surface == "desktop" for s in model.screens)


def stack_lines(model, db_engine: str) -> list[str]:
    d = model.stack.desktop or {}
    tk = d.get("toolkit") or "not identified"
    rt = d.get("runtime") or {}
    exe = d.get("executable") or {}
    rows = [
        ("Surface", "Windows desktop application", "UI Automation recording", ""),
        ("Toolkit", tk + (f" {d['version']}" if d.get("version") else ""), d.get("marker") or "",
         d.get("confidence") or ""),
        ("Runtime", " ".join(x for x in (rt.get("name"), rt.get("version")) if x) or "not identified",
         rt.get("marker") or "", rt.get("confidence") or ""),
        ("Executable", " ".join(x for x in (exe.get("file"), exe.get("product_version")) if x)
         or "not identified", "version resource" if exe else "", "certain" if exe else ""),
        ("Database", f"{db_engine} (the lab's default for desktop demo apps)", "", ""),
    ]  # fmt: skip
    for other in d.get("also") or []:
        rows.append(("Also seen", other["toolkit"], other["marker"], other["confidence"]))
    out = [
        "**Desktop application.** Rebuild it in the **same toolkit and runtime** the recording "
        "shows: bots find its controls by UI Automation properties the toolkit generates "
        "(ClassName, FrameworkId, the control hierarchy), so another toolkit gives other "
        "selectors.\n",
        "| Part | Detected | Marker | Confidence |",
        "|---|---|---|---|",
    ]
    out += [f"| {a} | {b} | {c} | {e} |" for a, b, c, e in rows]
    out.append("")
    how = TOOLCHAIN.get(tk)
    if how:
        out.append(f"Build with: {how}.\n")
    if d.get("support") and d.get("support") != "full":
        out.append(f"Recorder support for this toolkit: {d['support']}.\n")
    if d.get("problem"):
        out.append(f"**Problem:** {d['problem']}.\n")
    return out


def _esc(text) -> str:
    return str(text).replace("|", "\\|")


def section(brief) -> list[str]:
    """`## Desktop windows and controls`: what a bot finds in each in-scope window."""
    out = ["## Desktop windows and controls\n",
           "Selectors are UI Automation locators (`uia:AutomationId=txtCase;ControlType=Edit`, "
           "docs/09-desktop.md). Every AutomationId, Name, ControlType and ClassName below, and "
           "the parent each control sits in, must be the same in the demo app. ClassName `*` "
           "stands for the toolkit's per-build suffix. Values are shapes only (`X-9999`): data "
           "never left the real system.\n"]  # fmt: skip
    for scope in brief.screens:
        screen = scope.screen
        if screen.surface != "desktop":
            continue
        d = screen.desktop or {}
        w = d.get("window") or {}
        out.append(f"### {d.get('kind', 'window').capitalize()}: {screen.name}\n")
        out.append(f"- Window locator: `{w.get('selector')}`; UiPath: `{w.get('uipath')}`")
        if len(d.get("titles") or []) > 1:
            out.append("- Titles seen: " + ", ".join(f"`{t}`" for t in d["titles"]))
        if d.get("menus"):
            for m in d["menus"]:
                items = (
                    ", ".join(
                        f"{i['name']}" + (f" ({i['accel']})" if i.get("accel") else "")
                        for i in m.get("items") or []
                    )
                    or "*items not opened in the recording*"
                )
                out.append(f"- Menu **{m['menu']}**: {items}")
        if d.get("grids"):
            for g in d["grids"]:
                out.append(f"- Grid `{g['selector']}`: columns " +
                           ", ".join(f"`{c}`" for c in g["columns"]) +
                           f" ({g.get('rows_seen', 0)} row(s) seen; rows hold data, synthesise them)")  # fmt: skip
        if d.get("tab_order_seen"):
            out.append("- Tab order seen: " + " → ".join(f"`{s}`" for s in d["tab_order_seen"]))
        else:
            out.append("- Tab order: not observed (no Tab key in the recording); keep the "
                       "order the controls are listed in")  # fmt: skip
        keys = d.get("shortcuts") or []
        out.append("- Keyboard shortcuts seen: " + (", ".join(f"`{k}`" for k in keys) or "none"))
        if d.get("access_keys"):
            out.append(
                "- Access keys (mnemonics): " + ", ".join(f"`{a}`" for a in d["access_keys"])
            )
        for msg in d.get("messages") or []:
            out.append(f'- Message box after an action: "{_esc(msg)}"')
        out.append("")
        out.append(
            "| Locator | ControlType | AutomationId | Name | ClassName | Path | Seen | Used for |"
        )
        out.append("|---|---|---|---|---|---|---|---|")
        for el in screen.elements:
            u = el.uia or {}
            path = (
                " > ".join(p.replace("uia:", "") for p in (u.get("path") or [])[1:]) or "(window)"
            )
            seen = []
            if u.get("enabled") is False:
                seen.append("disabled")
            if u.get("visible") is False:
                seen.append("off-screen")
            if u.get("password"):
                seen.append("password")
            if u.get("readonly"):
                seen.append("read-only")
            if u.get("value_shapes"):
                seen.append("value " + "/".join(f"`{v}`" for v in u["value_shapes"][:3]))
            if el.options:
                seen.append("items " + ", ".join(f"`{o}`" for o in el.options))
            if u.get("ambiguous"):
                seen.append("**ambiguous locator**")
            out.append(f"| `{_esc(el.selector)}` | {u.get('control_type') or ''} | "
                       f"{_esc(u.get('automation_id') or '')} | {_esc(el.label or '')} | "
                       f"`{_esc(u.get('class_name') or '')}` | {_esc(path)} | {'; '.join(seen)} | "
                       f"{', '.join(el.actions) or '-'} |")  # fmt: skip
        out.append("")
    out.append("**Not observed** (do not invent as if recorded): windows, menus and items the "
               "recording never opened (menus listed without items above); what buttons never "
               "pressed do; the real data and how the application stores and queries it; fonts "
               "and colours beyond what *Same look* lists (else the toolkit's defaults). List "
               "what you assumed in BUILD-NOTES.md.\n")  # fmt: skip
    return out


def running_lines(brief) -> list[str]:
    return [
        "- A desktop demo app runs on a Windows machine or VM, installed from the folder or zip "
        "this build produces. **No Dockerfile, no container, no OpenShift.**",
        f"- Its data lives in a {brief.db_engine} database created from `db/schema.sql`; read the "
        "location from `DATABASE_URL` (`sqlite:///C:/path/app.db`), defaulting to a file next to "
        "the executable.",
        "- Ship no customer data. When `db/seed.sql` exists, load exactly its rows once, into the "
        "empty tables (the replay depends on them); otherwise create synthetic rows on first "
        "start.\n",
    ]


def acceptance_lines() -> list[str]:
    return [
        "1. Every locator under *Desktop windows and controls* resolves to exactly one control "
        "in its window, with the same ControlType, ClassName and parent path "
        "(`labctl desktop verify <app> --exe <built exe>` replays the recording and compares).",
        "2. Message boxes appear after the same actions with the same title and text.",
        "3. `db/schema.sql` applies unchanged to the SQLite database and the app works on it.",
        "4. Every rule under *Rules* behaves as stated; each stays `hypothesis` in `app.yaml`.",
        "5. Nothing under *Out of scope* exists in the demo app.\n",
    ]


def task(repo_root: Path, app_id: str, conf: dict, digest: str, repo_line: str, branch: str,
         brief_rule: str, stack_block: str) -> str:  # fmt: skip
    """The build task for a desktop demo app (labctl.builder.package.prompt dispatches here)."""
    app = f"apps/{app_id}"
    d = (conf.get("stack") or {}).get("desktop") or {}
    tk = d.get("toolkit") or "the toolkit the brief names"
    how = TOOLCHAIN.get(d.get("toolkit") or "", "the toolkit's own build tools")
    return f"""# Twin Lab build task: `{app_id}` (Windows desktop application)

You are a coding agent. Build the desktop demo app `{app_id}` for Twin Lab from its build brief
and hand the result back as one pull request (or, if you cannot open one, as one branch).
Brief digest: `{digest}`.

The Twin Lab package (`twinlab-{app_id}-*.zip`) holds this task, the brief and its inputs at their
repository paths; `MANIFEST.json` lists every file with its sha256.

## What a desktop demo app is for

A look-alike of a real Windows application, built from a recording of a person using it, so that
bots (UiPath, pywinauto, WinAppDriver) can run against it with synthetic data. Bots find desktop
controls by **UI Automation properties**: AutomationId, Name, ControlType, ClassName, FrameworkId
and the ancestor path. Fidelity to those is the whole job.

## Same toolkit, same controls

Rebuild in **{tk}**, the toolkit the brief's "Stack to pin" names. Build with: {how}.
{stack_block}
- Every window, dialog and control under "Desktop windows and controls" in the brief must exist
  with **the same AutomationId, Name, ControlType and ClassName**, inside the same parent (the
  Path column). In WinForms the AutomationId is the control's `Name` property, the window's is
  the form's `Name`; Name comes from `Text` or from the label before the control in tab order.
- Same menus, items, shortcut keys and access keys (`&` mnemonics); same tab order where the
  brief shows it; same message boxes (title and text) after the same actions.
- Grids: the same columns in the same order; rows are data, synthesise them.
- Values in the brief are shapes (`X-9999`): produce synthetic values of that shape.

## Database

Data lives in **SQLite** (unless `app.yaml` names another engine): create the tables from
`{app}/db/schema.sql` when the file has none, and load **exactly** the rows of `{app}/db/seed.sql`
(when the package has it) once, into the empty tables, and no other rows: the replay types the
values in `{app}/steps/*.values.yaml` and expects the recorded outcomes on these rows. Without a
seed file, fill the tables with synthetic rows on first start. Read the location from
`DATABASE_URL` (`sqlite:///C:/path/{app_id}.db`), defaulting to a file next to the executable. Do
not rebuild stored procedures or jobs.

## 1. Repository and branch

{repo_line}
2. Work on a branch named `{branch}`, created from the default branch. Never push to the default
   branch.

## 2. Read before you build

1. Read `AGENTS.md` and `docs/factory-playbook.md`; their rules apply, except that a desktop app
   has **no Dockerfile** and is not deployed to a cluster.
2. Read `{app}/BRIEF.md` and `{app}/model.yaml` (the `uia` of each element holds its locator,
   path, fallback locators and its UiPath, pywinauto and WinAppDriver forms).
{brief_rule}
3. When the package has them (generated from the recording, never data), read:
   - `{app}/spec/CONSTITUTION.md`, `{app}/spec/ARCHITECTURE.md`, `{app}/spec/BUILD-PLAN.md` and
     one document per window, `{app}/spec/WINDOWS/<window>.md`: build in the plan's order;
   - `{app}/look/`: `windows.json` (each window's frame and each control's bounds, font and
     colours, physical pixels at the recorded DPI), `screens/*.png` (each window and opened
     menu without data: yours must look the same), `texts.json` (every word, with `&` access
     keys), `theme.json` (DPI, system fonts, colours);
   - `{app}/navigation.json` (which action opens and closes each window, menus and items),
     `{app}/rules.json` (field limits, read-only, default buttons, validation seen with the
     field shapes at that moment);
   - `{app}/steps/<recording>.json` (every recorded step in order, rows each grid showed after
     it, a picture after it) and `{app}/steps/<recording>.values.yaml` (the values the replay
     types: your app must give the recorded outcomes with them on `{app}/db/seed.sql`).

## 3. Build

1. Write only under `{app}/`: the source (for .NET: `{app}/src/`), a build script
   `{app}/build.ps1`, and `{app}/BUILD-NOTES.md`. Never edit `BRIEF.md`, `db/schema.sql`, or
   `app.yaml` outside its `run:` section.
2. `build.ps1` builds the installable artifact: for .NET, `dotnet publish -c Release -r win-x64
   --self-contained true -o {app}/publish/{app_id}` and a zip of that folder,
   `{app}/publish/{app_id}-win-x64.zip`. An MSI (WiX) or MSIX installer is optional; say in
   BUILD-NOTES.md whether you made one. Do not commit `publish/`, `bin/` or `obj/`.
3. In `{app}/app.yaml`, under `run:`, set `desktop: {{exe: publish/{app_id}/<name>.exe}}` so the lab
   knows what to start.
4. Nothing under "Out of scope" is built. A control that leads out of scope keeps its
   AutomationId and Name but shows "not available in the demo".

## 4. Check

1. `python -m pytest -q`
2. `python -m labctl validate` (from `lab/labctl` if `labctl` is not installed)
3. `powershell -File {app}/build.ps1`
4. `python -m labctl desktop verify {app_id} --exe {app}/publish/{app_id}/<name>.exe` with the
   `verify_options` of `{app}/steps/*.values.yaml`, against each recording under
   `evidence/{app_id}/`, when the repository has one. Every step must pass; a missing, extra,
   changed or ambiguous control is a finding about your app. Without a recording, check by hand:
   walk the steps of `{app}/steps/*.json` with those values and compare each window with its
   picture; say in BUILD-NOTES.md what you could and could not check.

## 5. Build notes and pull request

Write `{app}/BUILD-NOTES.md`: what you built, the toolkit and versions, each check with its
command and result, assumptions, what you could not check. Open ONE pull request labelled
`ai-built`, titled `{app_id}: desktop demo app from brief {digest}`, with a table of the checks.

## 6. Trust rules

- Never change a brief, a check, `automations/`, `flows/`, `lab/` or `conditions/`.
- Report failures exactly as they are; never loosen a check.
- No real customer data and no secrets in any file, log or pull request.
"""
