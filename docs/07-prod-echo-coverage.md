# What Twin Lab keeps from Prod Echo and Application Twin Studio

This covers every Prod Echo plan item (`prod-echo/docs/03-plan.md`) and the Application Twin
Studio (ATS) features worth keeping, with where each now lives.

On 2026-11-16 an independent audit checked every claim against the code and the tests. Round 4
(the same day) closed most of the gaps it found; this page says what is there now.

**Status key:**
- **Done**: implemented and tested.
- **Partly**: some of it is built; the note says what is missing.
- **Replaced**: the goal is met another way.
- **Not built**: nothing yet.

"Portal" means it can be used without the command line.

## Prod Echo

| Item | Prod Echo goal | Status in Twin Lab | Where |
|---|---|---|---|
| I-00 | Repo scaffold, CLI, CI | Done | `labctl`, `.github/workflows/` |
| I-01 | Harness: fake apps and a fake bot | Done. Two reference apps: a React SPA and the server-rendered QTT stand-in. The SPA has an input mask (case number) and a fixed loading delay (search), and the behaviour catalog finds both. The harness bot writes UiPath-Verbose logs and recordings, and waits for the app's own requests the way a careful robot does. `tools/refresh_fixtures.py` checks or refreshes the test fixtures from a fresh run. | `reference/`, `tools/harness_bot.py` |
| I-02 | Evidence bundle | **Replaced** by the folder convention: `evidence/<app>/<recording>/` holds `recording.har`, `dom/`, `screens/` and `steps.jsonl`. There is no manifest or digest report. | `factory.discover` |
| I-03 | NetLog parser | Done; tolerates truncated files. Portal: upload it with a robot log. | `labctl botlog netlog` |
| I-04 | CDP recorder (HAR with bodies) | Done. The recorder opens its own browser, or attaches over the DevTools protocol to one already running (a robot's Chrome or Edge). It builds the HAR, with bodies, from network events. A HAR saved from browser developer tools is accepted too. | `labctl evidence record`, `labctl evidence attach`, portal "Record a flow" |
| I-05 | Screen capture | Done. Each step keeps the page as the action met it, the page after it, and a PNG. Scripted recordings have no PNG, because a picture taken later would show the wrong moment. Typed values are never kept, only their shape (`A9`); that is how input masks are found. | `labctl/recorder.py` |
| I-06 | Capture correlation | Done. Steps, HAR entries and robot-log steps are joined by time and selector. A step's time is when the action happened. | `labctl/botlog/trace.py` |
| I-07 | XAML parser | Done. It also reads the fields each API call sends and reads. | `labctl automation analyse`, portal |
| I-08 | Robot log parser | Done: Orchestrator export and NLog, Private fields masked, unknown fields kept. | portal **Robot runs**, `labctl botlog parse` |
| I-09 | Process analyser, bot trace | Done. | portal **Robot runs**, `labctl botlog trace` |
| I-09a | Behaviour catalog | Done; it goes into every brief and Devin package. All kinds are exercised, `format` included. | `apps/<app>/behaviours.json` |
| I-10 | Schema inventory from a replica | Done, read-only. Drivers: Postgres and SQLite built in; SQL Server, Oracle and MySQL as extras. | `labctl realdata inventory` |
| I-11 | Value binding (typed value to column) | Done on lab runs. A bot run is joined with its lab session: each value the bot typed or chose is found in the column it landed in. A chosen label is traced to the code stored for it (`Payments` to `category_id 2`). Binding production runs would need production capture, which is not planned. | `labctl session provenance`, harness report, portal **Robot runs > Compare** |
| I-12 | Read/write provenance per column | Done on lab runs. Every written column is typed by the bot, chosen by the bot, or set by the app. Values the bot entered that no column holds are listed. Sessions also record the tables read and written. | same as I-11, portal Sessions |
| I-13 | Subset spec | Done. `draft-spec` writes a first spec from the inventory for a person to change: the seed, child limits, and history tables left out. | `labctl realdata draft-spec` |
| I-14 | Closure engine | Done. Parents always, children bounded, cycles end. | `labctl realdata extract` |
| I-15 | App-load trace diff | Done. The tables can come from a lab session: the lab's database link reads each statement and keeps the table names ("Tables used" on the session page). This means something only when the lab schema keeps the real names, as when it was built from the real DDL. | `labctl realdata trace-diff --observed-session` |
| I-16 | Extractor protocol, Synapse behind it | Done on the harness. The adapter writes per-table SQL by key (portable, in chunks of 500). It reads the platform's output back and refuses missing or extra rows. A round trip matches the closure engine. The job format still has to be agreed with whoever runs the platform. | `labctl realdata synapse-job`, `synapse-read` |
| I-17 | Keyed deterministic masking | Done. FF1 is checked against the NIST vectors; masking is one-to-one; a sanitize check runs. | `labctl realdata mask` |
| I-18 | Loader | **Replaced** by the lab agent's load, which is all or nothing and becomes the baseline. Masked real rows reach it through `labctl realdata load`. That command maps real table and column names onto the demo schema and needs the mask receipt. | `labctl realdata load` |
| I-19 to I-22 | DOM-to-template app generator | **Replaced.** An AI coding agent builds the app from the brief, in the real framework. | app factory, Devin |
| I-21a | Behaviour runtime in generated apps | **Replaced.** Behaviours are written into the brief. Prod Echo's "unobserved interaction" signal is gone. | brief |
| I-22 | Package build and deploy | Done: one Dockerfile per app, `labctl lab up`, an app workflow in CI. | `labctl lab up`, `.github/workflows/app-*.yml` |
| I-22a | Fidelity replay | Done. It replays a recording against the demo app and compares each step's screen. | portal **Lab > Fidelity replay**, `labctl behaviour fidelity` |
| I-22b | UAT behaviour probing | Done. Recordings from two places (UAT and production) give two behaviour catalogs, compared rule by rule: only in one, a different outcome, or not comparable because the other side never did it. | `labctl behaviour compare`, portal **Evidence** |
| I-23 | Runtime API, leases | Done. The lab agent's admin API, and reservations: one person reserves a shared lab for a while, and changes by anyone else are refused until it ends. Still one copy per app, not one per run. | portal **Lab > Reserve**, `labctl lab reserve / release` |
| I-24 | Parity: a run against the bot trace | Done. A production run is compared with the same bot's run in the lab and that run's session. | portal **Robot runs > Compare**, `labctl botlog parity`, `labctl harness run --golden` |
| I-25 | Baselines, refresh, reset | Done. Every baseline is kept (the newest 20, plus pinned and quarantined ones) and can be restored. A quarantined baseline cannot be restored or reset to, and its reason stays on record. Named data packs save and load a data set by name, also across apps. | portal **Data**, `labctl data baselines / restore / pin / quarantine / save / use / load-across` |
| I-26 | First real process end to end | **Partly.** On the QTT stand-in: built from recordings and DDL alone, both bots READY, and the Python bot run in the lab under conditions, with a verdict. Prod Echo's definition of done (parity on three real runs, real data) needs the real QTT's evidence. | E-05 |
| T-26 | Calibrate conditions from robot logs | Done. Robot-log timings become a condition profile. | portal **Robot runs > Make a condition** |
| - | One command for the whole harness chain | Done. In order: reset, condition, session, harness bot, robot log, verdict, parity, report. The earlier condition is put back afterwards. | `labctl harness run` |

## Application Twin Studio

| Feature | Status in Twin Lab |
|---|---|
| UiPath importer, gaps, READY/BLOCKED | Done: the analyser (T-03) and conformance (T-07). The Python analyser also reports SQL tables and page selectors the code uses that `touches` does not list (T-16). |
| Bindings contract (`ATS_BINDINGS`) | **Partly.** The same envelope (`apps`, `inputs`, `output_path`), but different keys per app (`web`, `api`, `db`, `credentials`). ATS automations need small edits. |
| Fault injection per call | Done: lab agent conditions (latency, faults), seeded and repeatable. A database link proxy is added. |
| Isolation per run | **Partly.** Isolation is per app (one Compose project, or one OpenShift deployment, each), with reservations so one person's run is not disturbed. There is no copy per run. |
| "Nothing else changed" check | Done. An automation declares `expects:` (tables, operations, columns, row counts), usually by accepting a checked session. Every session then gets PASS or FAIL, with each unexpected change named. |
| Trust rules for AI-authored changes | Done. `tools/guard_ai_changes.py` runs in CI on every pull request. It blocks a self-approved rule, an AI edit to the generated brief, and an AI change to an app together with its checks. |
| Repeated runs for agent consistency | Done for web steps: `labctl harness run --repeat N` runs the chain N times under the same seeded condition. It is PASS only when every run passed and every session changed the same kinds of rows. An agent that is not the harness bot is repeated by hand and its sessions compared in the portal. |
| Upload safety (zip paths, size, XML entities) | Done. |
| xaml-replay and robot hand-off runners, SDK, scenarios | **Not carried over.** People run the bots; the harness bot replays web steps. |
| TN3270 and SOAP adapters, YAML twin engine | **Not carried over** (only browser, API and database), or replaced by real demo apps. |

## Also built after the audit

| What | Where |
|---|---|
| Deploy to OpenShift: images to GHCR, one deployment and Route per app, only changed apps | `.github/workflows/deploy-ocp.yml`, `labctl ocp`, [ocp.md](ocp.md) |
| An agentic bot beside the UiPath one (T-17) | `automations/agentic-case-update` |
| What each kind of evidence finds (E-04) | [08-evidence-ablation.md](08-evidence-ablation.md) |
| A Start here page, progress and next step per app, plain glossary | portal |

## Open gaps

| Gap | Why it stays open |
|---|---|
| Value binding on production runs | Needs approval to capture production. |
| A copy of an app per run | One copy per app, with reservations, has been enough; per-run copies cost a database each. |
| OpenShift tested on a real cluster | No cluster here; images were run as a random user ID like OpenShift does, and every manifest is tested. |
| Synapse job format, QTT items (T-23 to T-27, I-26) | Left aside on purpose: they need other teams and the real QTT. |