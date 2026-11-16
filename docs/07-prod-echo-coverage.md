# What Twin Lab keeps from Prod Echo and Application Twin Studio

This covers every Prod Echo plan item (`prod-echo/docs/03-plan.md`) and the Application Twin
Studio (ATS) features worth keeping, with where each now lives.

On 2026-11-16 an independent audit checked every claim against the code and the tests. This page
says what it found.

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
| I-01 | Harness: fake apps and a fake bot | **Partly.** Two reference apps (a React SPA and the server-rendered QTT stand-in). The harness bot writes UiPath-Verbose logs and recordings, and is tested against a running lab. Neither app has Prod Echo's input mask or fixed loading delay, so that behaviour kind is never exercised. | `reference/`, `tools/harness_bot.py` |
| I-02 | Evidence bundle | **Replaced** by the folder convention: `evidence/<app>/<recording>/` holds `recording.har`, `dom/` and `steps.jsonl`. There is no manifest or digest report. | `factory.discover` |
| I-03 | NetLog parser | Done; tolerates truncated files. Portal: upload it with a robot log. | `labctl botlog netlog` |
| I-04 | CDP recorder (HAR with bodies) | **Partly.** The Playwright recorder opens its own browser; it cannot attach to a robot's running browser. A HAR saved from browser developer tools is accepted. | `labctl evidence record`, portal "Record a flow" |
| I-05 | Screen capture | **Partly.** Page HTML after every step. No PNGs, no separate snapshot before each action. Screenshots are kept as evidence but not read. | `labctl/recorder.py` |
| I-06 | Capture correlation | Done. Steps, HAR entries and robot-log steps are joined by time and selector. | `labctl/botlog/trace.py` |
| I-07 | XAML parser | Done. It also reads the fields each API call sends and reads. | `labctl automation analyse`, portal |
| I-08 | Robot log parser | Done: Orchestrator export and NLog, Private fields masked, unknown fields kept. | portal **Robot runs**, `labctl botlog parse` |
| I-09 | Process analyser, bot trace | Done. | portal **Robot runs**, `labctl botlog trace` |
| I-09a | Behaviour catalog | Done; it goes into every brief and Devin package. | `apps/<app>/behaviours.json` |
| I-10 | Schema inventory from a replica | Done, read-only. Drivers: Postgres and SQLite built in; SQL Server, Oracle and MySQL as extras. | `labctl realdata inventory` |
| I-11 | Value binding (typed value to column) | **Not built.** Without approval to capture production there is no input for it. Recordings, routes and DDL bind screens to tables instead. | - |
| I-12 | Read/write provenance per column | **Partly.** Lab sessions record which rows and columns a run changed. They do not separate values the bot typed from values the app derived. | portal Sessions |
| I-13 | Subset spec | Done for validation. The spec is written by hand; there is no generator. | `realdata/specs/subset.example.yaml` |
| I-14 | Closure engine | Done. Parents always, children bounded, cycles end. | `labctl realdata extract` |
| I-15 | App-load trace diff | Done in a simpler form, but the tables a screen read are typed by hand. | `labctl realdata trace-diff` |
| I-16 | Extractor protocol, Synapse behind it | **Partly.** The protocol and Twin Lab's own extractor are done; no Synapse adapter exists. | `realdata/closure.py` |
| I-17 | Keyed deterministic masking | Done. FF1 is checked against the NIST vectors; masking is one-to-one; a sanitize check runs. | `labctl realdata mask` |
| I-18 | Loader | **Replaced** by the lab agent's load, which is all or nothing and becomes the baseline. Masked real rows reach it through `labctl realdata load`, which maps real table and column names onto the demo schema and needs the mask receipt. | `labctl realdata load` |
| I-19 to I-22 | DOM-to-template app generator | **Replaced.** An AI coding agent builds the app from the brief, in the real framework. | app factory, Devin |
| I-21a | Behaviour runtime in generated apps | **Replaced.** Behaviours are written into the brief. Prod Echo's "unobserved interaction" signal is gone. | brief |
| I-22 | Package build and deploy | Done: one Dockerfile per app, `labctl lab up`, an app workflow in CI. | `labctl lab up`, `.github/workflows/app-*.yml` |
| I-22a | Fidelity replay | Done. It replays a recording against the demo app and compares each step's screen. Command line only. | `labctl behaviour fidelity` |
| I-22b | UAT behaviour probing | **Partly.** Recording in UAT uses the same recorder. There is no UAT-versus-production mismatch check. | portal "Record a flow" |
| I-23 | Runtime API, leases | **Partly.** The lab agent has an admin API. There are no leases: one shared copy per app. | lab agent |
| I-24 | Parity: a run against the bot trace | Done. A production run is compared with the same bot's run in the lab and that run's session. | portal **Robot runs > Compare**, `labctl botlog parity` |
| I-25 | Baselines, refresh, reset | **Partly.** One baseline per app with exact reset. No versioned baselines, pinning or quarantine. | lab agent |
| I-26 | First real process end to end | **Partly.** On the QTT stand-in: built from recordings and DDL alone, both bots READY, and the Python bot run in the lab under conditions. Prod Echo's definition of done (parity on three real runs, real data) needs the real QTT's evidence. | E-05 |
| T-26 | Calibrate conditions from robot logs | Done. Robot-log timings become a condition profile. | portal **Robot runs > Make a condition** |
| - | One command for the whole harness chain | **Not built.** | - |

## Application Twin Studio

| Feature | Status in Twin Lab |
|---|---|
| UiPath importer, gaps, READY/BLOCKED | Done: the analyser (T-03) and conformance (T-07). |
| Bindings contract (`ATS_BINDINGS`) | **Partly.** The same envelope (`apps`, `inputs`, `output_path`), but different keys per app (`web`, `api`, `db`, `credentials`). ATS automations need small edits. |
| Fault injection per call | Done: lab agent conditions (latency, faults), seeded and repeatable. A database link proxy is added. |
| Isolation per run | **Partly.** Isolation is per app (one Compose project each), not per run; reset is optional before a session. |
| "Nothing else changed" check | **Partly.** A session shows every row change, and two sessions can be compared. Nothing fails on an unexpected change yet. |
| Trust rules for AI-authored changes | Done. `tools/guard_ai_changes.py` runs in CI on every pull request. It blocks a self-approved rule, an AI edit to the generated brief, and an AI change to an app together with its checks. |
| Repeated runs for agent consistency | **Partly.** Run the session more than once and compare by hand. There is no `--repeat` helper. |
| Upload safety (zip paths, size, XML entities) | Done. |
| xaml-replay and robot hand-off runners, SDK, scenarios | **Not carried over.** People run the bots; the harness bot replays web steps. |
| TN3270 and SOAP adapters, YAML twin engine | **Not carried over** (only browser, API and database), or replaced by real demo apps. |

## Open gaps, by value to an office user

| Gap | Size | What it needs |
|---|---|---|
| Unexpected changes fail a session | M | Expected changes per automation, and a pass/fail verdict on the session. |
| Named data packs and versioned baselines | M | Save and restore a named data set; keep earlier baselines. |
| Recorder attaches to a running browser and takes screenshots | M | `connect_over_cdp`, PNGs, and a snapshot before each action. |
| Fidelity replay in the portal | S | A button on the app page. |
| One command for the harness chain, and a fixture refresh script | S–M | For demos and regression. |
| Input mask and loading delay in a reference app | S | So the `format` behaviour kind is exercised. |
| Synapse adapter, subset-spec generator, capturing observed tables | S each | |
