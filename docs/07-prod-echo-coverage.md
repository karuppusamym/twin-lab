# What Twin Lab keeps from Prod Echo and Application Twin Studio

Every Prod Echo plan item (`prod-echo/docs/03-plan.md`) and the Application Twin Studio features
worth keeping, and where each now lives. "Replaced" means the goal is met another way.

## Prod Echo

| Item | Prod Echo goal | In Twin Lab | Where |
|---|---|---|---|
| I-00 | Repo scaffold | Done | T-00 |
| I-01 | Local harness: two fake apps and a fake bot | Done: two reference apps (React SPA and server-rendered QTT stand-in) and a harness bot that writes UiPath-Verbose logs | `reference/`, `tools/harness_bot.py` |
| I-02 | Evidence bundle | Replaced by the recording folder (`recording.har`, `dom/`, `steps.jsonl`) under `evidence/<app>/` | `factory.discover` |
| I-03 | NetLog parser | Done, tolerates truncated files | `labctl botlog netlog` |
| I-04 | CDP recorder (HAR with bodies) | Done as the Playwright recorder; also accepts HAR saved from browser developer tools | `labctl evidence record`, portal "Record a flow" |
| I-05 | Screen capture (DOM per step) | Done inside the recorder: a snapshot after every step | `labctl/recorder.py` |
| I-06 | Capture correlation | Done: steps, HAR entries and robot-log steps joined by time and selector | `labctl/botlog/trace.py` |
| I-07 | XAML parser | Done (T-03) | `labctl automation analyse` |
| I-08 | Robot log parser | Done: Orchestrator export and NLog; Private redaction; unknown fields kept | `labctl botlog parse` |
| I-09 | Process analyzer, bot trace | Done | `labctl botlog trace` |
| I-09a | Behaviour catalog | Done; goes into every brief and Devin package | `labctl behaviour catalog`, `apps/<app>/behaviours.json` |
| I-10 | Schema inventory from a replica | Done, read-only | `labctl realdata inventory` |
| I-11 | Value binding (typed value -> column) | Not built. Without production capture approvals it has no input; the brief binds screens to tables through recordings, routes and DDL instead | - |
| I-12 | Read/write provenance per column | Replaced: lab sessions record exactly which rows and columns a run changed | portal Sessions |
| I-13 | Subset spec | Done | `realdata/specs/subset.example.yaml` |
| I-14 | Closure engine | Done: parents always, bounded children, cycles end | `labctl realdata extract` |
| I-15 | App-load trace diff | Done in a simpler form: tables a screen reads that the closure missed | `labctl realdata trace-diff` |
| I-16 | Extractor protocol, Synapse behind it | Protocol and own extractor done; Synapse adapter not built (no Synapse access) | `realdata/closure.py` |
| I-17 | Keyed deterministic masking (FF1, HMAC) | Done; FF1 checked against the NIST vectors; sanitize checks | `labctl realdata mask` |
| I-18 | Loader (MERGE, batch tag, idempotent) | Replaced by the lab agent's all-or-nothing load with a baseline | `POST /_lab/data/load` |
| I-19 to I-22 | DOM-to-template app generator | Replaced by apps built by an AI coding agent from the brief, in the real framework | app factory |
| I-21a | Behaviour runtime in generated apps | Replaced: behaviours are written into the brief for the builder to reproduce | brief "Behaviour seen in the recordings" |
| I-22a | Fidelity replay | Done: replays a recording against the demo app and compares each step's screen | `labctl behaviour fidelity` |
| I-22b | UAT behaviour probing | Covered by recording in UAT with the same recorder | portal "Record a flow" |
| I-23 | Runtime API, leases | Partly: the lab agent's admin API; no leases (one shared copy per app) | lab agent |
| I-24 | Parity: agent run vs bot trace | Done | `labctl botlog parity` |
| I-25 | Baselines, refresh, reset | Done: baseline per load, exact reset | lab agent |
| I-26 | First real process end to end | Done on the QTT stand-in from recordings and DDL only; the real QTT needs its evidence | E-05 |

## Application Twin Studio

| Feature | In Twin Lab |
|---|---|
| UiPath importer, gaps, READY/BLOCKED | Analyser (T-03) and conformance (T-07) |
| Bindings contract (`ATS_BINDINGS`) | Same shape: `labctl bindings`, portal downloads |
| Fault injection per call | Lab agent conditions: latency, faults, seeded and repeatable |
| Isolation per run | One Compose project per app; reset before each session |
| "Nothing else changed" check | Session row changes, and comparison of two sessions |
| Trust rules for AI-authored changes, `verification: hypothesis` | Factory playbook and `AGENTS.md`; rules stay hypotheses |
| Repeated runs for agent consistency | Run the session more than once; compare sessions |
| TN3270 and SOAP adapters | Not carried over (browser, API and database only) |
| YAML twin engine | Replaced by real demo apps |
