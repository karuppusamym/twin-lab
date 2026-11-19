"""Is a build package complete, and what is each of its files for?

`problems()` reads the generated files of an application as a builder would and lists what is
missing: a screen without an actions list, an endpoint without a schema, a navigation target
nothing defines, a table without seed rows, a typing step without a value. The packaging step
writes the list into the package (MANIFEST.json and the task) and says it aloud; with
`strict=True` it refuses to write the package.

`purpose()` gives one line per packaged file, for `INDEX.md` and `MANIFEST.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

INDEX_FILE = "INDEX.md"
EMPTY_BY_DESIGN = {"app_sessions"}  # filled while the application runs


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


def problems(repo_root: Path, app_id: str) -> list[str]:
    """Every gap found, one line each; [] for a complete package. An application without
    recorded steps (built from source or an automation only) has no step-based file to check."""
    from labctl.builder.package import _create_tables

    app = Path(repo_root) / "apps" / app_id
    out: list[str] = []
    step_files = sorted((app / "steps").glob("*.json")) if (app / "steps").is_dir() else []
    nav = _json(app / "navigation.json")
    cat = _json(app / "api" / "catalogue.json")
    if step_files and nav is None:
        out.append("navigation.json is missing: no navigation map and no actions list "
                   "(regenerate the brief)")  # fmt: skip
    for screen in (nav or {}).get("screens") or []:
        if not screen.get("in_scope") or screen.get("source") == "code":
            continue  # a screen only the code has: no snapshot by nature (codemap.json)
        if not screen.get("elements") and not str(screen.get("kind") or "").startswith(
                ("shown in a frame", "stand-in")):  # fmt: skip
            out.append(f"screen `{screen.get('path')}` has no actions list (no snapshot of it "
                       "holds an interactive element)")  # fmt: skip
        if not screen.get("page"):
            out.append(f"screen `{screen.get('path')}` has no page snapshot")
    pages = list((app / "pages").glob("*.html")) if (app / "pages").is_dir() else []
    if step_files and pages and not (app / "rules.json").is_file():
        out.append("rules.json is missing: no field rules or page behaviour (regenerate the "
                   "brief)")  # fmt: skip
    for target in (nav or {}).get("undefined_targets") or []:
        out.append(f"navigation target undefined: {target}")
    for row in (cat or {}).get("endpoints") or []:
        doc = _json(app / row["file"]) if row.get("file") else None
        name = f"{row.get('method')} {row.get('path')}"
        if doc is None:
            out.append(f"endpoint `{name}`: its file {row.get('file')} is missing")
            continue
        req = doc.get("request") or {}
        if "json" in str(req.get("content_type") or "") and not req.get("schema"):
            out.append(f"endpoint `{name}`: the request has no schema")
        for status, r in (doc.get("responses") or {}).items():
            if "json" in str(r.get("content_type") or "") and not r.get("schema"):
                out.append(f"endpoint `{name}` answer {status}: no schema (its body was not "
                           "recorded: the page did not read it)")  # fmt: skip
    code = _json(app / "codemap.json")
    for m in (code or {}).get("missing_code") or []:
        routes = ", ".join(f"`{r}`" for r in m.get("routes") or [])
        out.append(f"code `{m.get('file')}`" + (f" (route {routes})" if routes else "")
                   + " was never downloaded: what it shows is unknown (`labctl evidence "
                   "fetch-code <recording> --yes`, then regenerate the brief)")  # fmt: skip
    api_dir = app / "api"
    if api_dir.is_dir() and cat is None and any(api_dir.glob("*.json")):
        out.append("api/catalogue.json is missing though endpoint files exist")
    schema = app / "db" / "schema.sql"
    seed = _json(app / "db" / "seed.json")
    if schema.is_file() and (app / "db" / "seed.sql").is_file() and isinstance(seed, dict):
        rows = {str(k).lower(): v for k, v in seed.items()}
        for name, _ in _create_tables(schema.read_text(encoding="utf-8", errors="replace")):
            if name in EMPTY_BY_DESIGN:
                continue
            if not rows.get(name):
                out.append(f"table `{name}` has no seed rows (the brief's *Seed data and replay "
                           "values* says why; the application starts with it empty)")  # fmt: skip
    elif schema.is_file() and step_files and _create_tables(
            schema.read_text(encoding="utf-8", errors="replace")):  # fmt: skip
        out.append("db/seed.sql is missing: the tables have no seed rows")
    for path in step_files:
        doc = _json(path) or {}
        typed = [s for s in doc.get("steps") or [] if s.get("action") == "type"]
        if not typed:
            continue
        vfile = path.with_name(path.stem + ".values.yaml")
        try:
            vdoc = yaml.safe_load(vfile.read_text(encoding="utf-8")) if vfile.is_file() else {}
        except yaml.YAMLError:
            vdoc = {}
        have = {r.get("n") for r in (vdoc or {}).get("steps") or [] if "value" in r}
        missing = [s["n"] for s in typed if s.get("n") not in have]
        if missing:
            out.append(f"steps/{path.name}: typing step(s) {', '.join(map(str, missing[:12]))}"
                       + (" ..." if len(missing) > 12 else "")
                       + " have no value in the values file")  # fmt: skip
    # The build documents the package will carry (labctl.builder.docs): a page file per screen.
    from labctl.builder import docs

    out += docs.gaps(app_id, docs.inputs(Path(repo_root), app_id))
    return out


def purpose(rel: str, app_id: str, data: bytes | None = None) -> str:
    """One line on what a packaged file is for."""
    app = f"apps/{app_id}/"
    fixed = {
        "BUILD_TASK.md": "The task: what to build, the rules, the reading order, how to check. "
                         "Start here.",
        INDEX_FILE: "This list: every file of the package and what it is for.",
        "MANIFEST.json": "Every file with its sha256, size and purpose, and the result of the "
                         "completeness check.",
        "AGENTS.md": "The lab's rules for coding agents.",
        "docs/factory-playbook.md": "How a demo application is built and run in the lab.",
        app + "BRIEF.md": "The full specification: scope, screens, API, sign-in, navigation, "
                          "actions, data, seed, checks. The reference behind every document "
                          "under spec/.",
        app + "session.json": "Sign-in and session as recorded: the call, each attempt, every "
                              "cookie by name with its attributes and who sets and clears it "
                              "(never a value or a credential).",
        app + "spec/CONSTITUTION.md": "The rules of this build that are not negotiable (the same "
                                      "text as the task's) and what \"done\" means. Read second.",
        app + "spec/ARCHITECTURE.md": "Stack, layers, route table, API surface, data model, "
                                      "sign-in, session and cookies, how the app runs. Read "
                                      "third.",
        app + "spec/BUILD-PLAN.md": "The build in order, each step with its \"done when\" lines. "
                                    "Read fourth.",
        app + "spec/GLOSSARY.md": "Option lists, code lists, the statuses the application "
                                  "answered and the package's words.",
        app + "model.yaml": "Reference: everything the evidence showed (shapes, timings), which "
                            "the brief is generated from.",
        app + "app.yaml": "The application's lab settings and rules; you change only `run:`.",
        app + "behaviours.json": "Reference: what changed on the page after each recorded step, "
                                 "with its evidence.",
        app + "db/schema.sql": "The tables. Create them unchanged.",
        app + "db/seed.sql": "The rows to load at start-up, once, into the empty tables. "
                             "Synthetic.",
        app + "db/seed.json": "The same rows as JSON.",
        app + "navigation.json": "Every screen, how each is reached and left, dialogs, tabs, "
                                 "frames, popup windows, and every element of each screen with "
                                 "what it did.",
        app + "codemap.json": "What the application's code defines: every route (recorded or "
                              "only in code), guards, lazy chunks, menus, tabs, dialogs, API "
                              "calls (called or not), interface texts, lists of values, "
                              "third-party libraries.",
        app + "api/catalogue.json": "Index of the endpoints the pages called, the list "
                                    "parameters' effects and the field map.",
        app + "api/network.json": "What the network showed beyond the endpoints: the calls of "
                                  "each page load in order, redirect chains, streams, static "
                                  "files with sizes and caching, third parties (never called), "
                                  "errors and retries.",
        app + "templates.json": "Each screen as a template: instances seen, the parameters "
                                "that vary, data fields with one synthetic seed row per "
                                "instance, widgets (breadcrumbs, gallery, variant picker, "
                                "facets, recommendations, lists that load more), the navigation "
                                "hierarchy and the cart and wish-list state.",
        app + "rules.json": "Per screen and field, every validation rule with its message, "
                            "level (seen, declared, in code) and evidence, rules between fields, "
                            "behaviour while typing or choosing, state on load, session timers.",
    }  # fmt: skip
    if rel == app + "THIRD-PARTY-NOTICES.md":
        return ("The licences of the open-source libraries and fonts the package carries: name, "
                "version, licence, original address, sha256, files, and the licence texts. Keep "
                "it with those files.")  # fmt: skip
    if rel == app + "vendor/vendor.json":
        return ("Every third-party library carried and not carried (with the reason), each "
                "file's original address and sha256, and which page loads which file.")  # fmt: skip
    if rel.startswith(app + "vendor/"):
        parts = rel[len(app + "vendor/") :].split("/")
        what = f"{parts[0]} {parts[1]}" if len(parts) > 2 else "a library"
        return (f"A file of the open-source library {what}, exactly as the browser received it: "
                "use it as it is (licence in THIRD-PARTY-NOTICES.md).")  # fmt: skip
    if rel.startswith(app + "look/fonts/"):
        return ("A web font file of the application, carried because its licence allows "
                "redistribution (THIRD-PARTY-NOTICES.md); the carried style sheets point at it.")  # fmt: skip
    if rel.startswith(app + "scripts/"):
        return ("The application's own script, carried on the owner's request (source maps "
                "stripped, checked for secrets and hosts): reference for its rules.")  # fmt: skip
    if rel in fixed:
        return fixed[rel]
    if rel.startswith(app + "spec/PAGES/"):
        text = data.decode("utf-8", "replace") if data else ""
        head = text.split("\n", 1)[0].lstrip("# ").strip()
        return (
            f"Page file {head or rel.rsplit('/', 1)[-1]}: everything one screen needs, with "
            "the file each fact comes from. Read the page files in order, one per screen."
        )
    if rel.startswith(app + "api/"):
        try:
            doc = json.loads(data.decode("utf-8")) if data else {}
        except ValueError:
            doc = {}
        name = f"`{doc.get('method')} {doc.get('path')}`" if doc.get("method") else "an endpoint"
        return f"Endpoint {name}: parameters, schemas, every recorded call with synthetic values."
    if rel.startswith(app + "static/"):
        return ("A static file the pages read (configuration), with every address made local: "
                "serve it at the same path.")  # fmt: skip
    if rel.startswith(app + "pages/"):
        return ("Snapshot of one screen: its real structure, ids, classes and interface text; "
                "data masked.")  # fmt: skip
    if rel.startswith(app + "unrecorded/"):
        return ("Reference snapshot of a page the link check found and the recording never "
                "visited: not a recorded screen, not in scope; data masked.")  # fmt: skip
    if rel.startswith(app + "look/"):
        return "How the screens look: style sheets, theme, one picture per screen."
    if rel.startswith(app + "steps/"):
        if rel.endswith(".values.yaml"):
            return ("The synthetic value the acceptance replay types at each step, and the "
                    "outcome the recording showed.")  # fmt: skip
        if rel.endswith(".sequence.yaml"):
            return "The order of the steps across the applications of the recording."
        return "The recorded steps in order: action, selector, screen, requests, next page."
    if rel.startswith("flows/"):
        return "The flow in words: the screens in order and what was done on each."
    if rel.startswith("evidence/"):
        return "A picture of a screen, for reference."
    return "Part of the package."


def index_md(app_id: str, files: list[tuple[str, str]], gaps: list[str]) -> str:
    """`INDEX.md`: every file with its purpose, and the completeness check's result."""
    lines = [f"# Package index: `{app_id}`", "",
             "Every file of this package, and what it is for. Read them in the order "
             "`BUILD_TASK.md` gives.", "", "| File | What it is for |", "|---|---|"]  # fmt: skip
    for rel, text in files:
        lines.append(f"| `{rel}` | {text} |")
    lines += ["", "## Completeness check", ""]
    if gaps:
        lines += ["The packaging step found these gaps. Each is something the evidence does not "
                  "hold; where one matters, choose the simplest behaviour that fits the brief and "
                  "write it under Assumptions in BUILD-NOTES.md:", "",
                  *[f"- {g}" for g in gaps]]  # fmt: skip
    else:
        lines.append("No gap found: every screen has an actions list, every endpoint a schema, "
                     "every navigation target a definition, every table seed rows, every typing "
                     "step a value, every screen a page file.")  # fmt: skip
    return "\n".join(lines) + "\n"
