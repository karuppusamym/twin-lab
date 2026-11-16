# Architecture

```
 KNOW                        BUILD                              RUN
 ----                        -----                              ---
 Automation registry         App factory                        Twin Lab portal
   automation.yaml             build brief  <── evidence       apps · data · conditions · sessions
   analysers:                  AI coding agent ──► pull request          │ controls
     UiPath XAML               apps/<app>/ in the monorepo               ▼
     Python / agent code       per-app pipeline ──► container     lab agent ── demo app ── DB link ── DB
   demand per app ──────────►  conformance check  ◄── automation.yaml    ▲            (one set per app)
                                 READY / BLOCKED                         │ drives, triggered by hand
                                                                  UiPath robot · agent · Python script
```

Three stages. **Know** what the automations need. **Build** the apps to that need. **Run** them
under conditions we choose.

## 1. Automation registry

The entry point. Every automation is a folder `automations/<id>/` with an `automation.yaml`
(contract in `03-contracts.md`) and either its source or a pointer to it.

| Field | Values |
|---|---|
| `type` | `uipath`, `agentic`, `python`, `other` |
| `surface` | `frontend`, `backend`, `combo` |
| interfaces used | `web` (screens and selectors), `api` (routes), `db` (tables and columns), `file` |

Analysers fill in what the automation touches, so a person reviews rather than types:

- **UiPath**: static parse of `project.json` and every `*.xaml`. Extracts target applications from
  selector `app` and `title`, each `TypeInto` / `Click` / `GetText` with its selector and variable,
  `ExecuteQuery` SQL, HTTP request activities, and the workflow tree. Nothing is executed.
  Activities it does not understand are listed as gaps, never skipped silently.
- **Python and agentic**: the automation declares its tools and targets in `automation.yaml`; a
  static scan of the code for URLs, SQL strings and selectors cross-checks the declaration and
  reports anything undeclared.
- **Robot logs**, where available: per-activity timings, used later for slowness calibration.

Output per application is a **demand report**: the screens, selectors, routes and tables that at
least one registered automation uses, each with the automations that need it. That report is the
scope of the demo app.

## 2. App factory

Turns whatever evidence exists for an application into a demo app. Source code is usually not
available; the full design is in [06-evidence.md](06-evidence.md).

1. **Collect evidence** into the evidence store: automation code, HAR recordings, database schema,
   API descriptions, screenshots, flow descriptions, and source code when there is any. Raw
   evidence never enters the repository.
2. **Analyse** each piece into one app model, `apps/<app>/model.yaml`: screens and elements,
   flows, routes with request and response shapes, tables, rules, stack. Every item records the
   evidence it came from and its basis: `observed`, `declared`, `inferred`, `described` or
   `assumed`. Conflicts between kinds of evidence become open questions.
3. **Scope** the model to the demand: what registered automations touch, or the flows a person
   requested in `flows/`.
4. **Build brief** (`apps/<app>/BRIEF.md`) and `db/schema.sql` from the scoped model. It says
   which mode each part is built in (copy, reconstruction, skeleton, backend only, sample) and
   what was assumed. A person reviews it.
5. **Build.** An AI coding agent (Devin, Copilot agent or Claude Code) implements the brief under
   [factory-playbook.md](factory-playbook.md) and opens a pull request. Screenshots are given to
   it directly. It reports results as they are and cannot approve its own rules.
6. **Conformance check.** Run in the pipeline against the built app:
   - every selector in every mapped `automation.yaml` resolves on its screen (headless browser)
   - every route exists with the declared method
   - every table and column exists in the schema
   - result per automation: `READY`, or `BLOCKED` with each gap named, and how much of what was
     checked is `observed` rather than `assumed`

## 3. Monorepo and pipelines

```
twin-lab/
  automations/<id>/          automation.yaml, source or pointer
  flows/<id>.yaml            flows a person asked for, when there is no automation code
  evidence/                  raw HAR files, exports, screenshots; ignored by Git, never committed
  apps/<app>/                app.yaml, BRIEF.md, frontend/, backend/, db/schema.sql, Dockerfile
  lab/
    portal/                  the Twin Lab UI and its API
    lab-agent/               one image, runs beside every app
    labctl/                  CLI: analyse, conformance, data, conditions
  reference/<name>/          stand-in source applications the factory is exercised on
  data-packs/<pack>/         pack.yaml and workbook or generator settings
  conditions/                normal.yaml, peak-hour.yaml, db-degraded.yaml ...
  .github/workflows/
    app.yml                  reusable: build, test, conformance, image, deploy
    app-<app>.yml            eight lines: path filter on apps/<app>/** and a call to app.yml
    platform.yml
  docs/
```

- A change under `apps/qtt/` runs only QTT's pipeline. A change to an `automation.yaml` re-runs
  conformance for the apps it maps to.
- Each app's toolchain (Node 14 and Angular, Java 11 and Maven, .NET) lives in its own Dockerfile,
  so the pipeline itself stays the same for all of them.
- Each app deploys as: demo app container(s), its database, one lab agent.

**Hosting.** Containers. On a laptop, `docker compose up` brings up the portal and the chosen apps.
For a shared environment, the same images run on whatever container host is approved, behind a
reverse proxy that gives each app its own hostname. The choice of host is an open question; nothing
in the design depends on it. Static hosting alone is not enough, because the apps have backends and
databases.

## 4. Lab agent

One small service, the same image for every app, deployed beside it. It is how the portal controls
an app without the app containing any lab code.

```
browser / robot ──► lab agent (HTTP reverse proxy) ──► demo app ──► DB link proxy ──► database
                        │  request log, latency, faults                 │ latency, drops
                        └── admin API  ◄── portal                       └── set by lab agent
```

| Admin API | Does |
|---|---|
| `GET /_lab/info` | App id, version, stack, schema tables, current data pack and condition |
| `GET /_lab/health` | Proxied app and database reachable |
| `POST /_lab/data/load` | Load a data pack as the baseline |
| `POST /_lab/reset` | Return the database to the loaded baseline |
| `PUT /_lab/condition` | Apply a condition profile |
| `POST /_lab/session/start`, `/stop` | Record requests and take before/after table snapshots |

The demo app's only obligations: read its database address from the environment, and expose a
health URL. Everything else is outside it, which is what lets apps in different frameworks be
controlled the same way.

## 5. Data packs

A data pack is a named set of rows for one or more apps. Loading one makes it the baseline; reset
returns to it.

- **Template.** The portal generates an Excel workbook from the app's schema: one sheet per table,
  one column per field, with types and required flags in a header note. This is the "Excel with the
  tables and fields we have in the demo".
- **Bring your own.** Fill the workbook, upload. Validation before anything loads: unknown sheets or
  columns, types, required fields, keys, foreign keys, and cross-app links (the same customer under
  each app's identifier). Errors are reported by sheet, row and column. Load is all or nothing.
- **Synthetic.** A seeded generator fills the same workbook shape from the schema, so a pack is
  reproducible from its seed. No model generates records.
- **Real-derived data.** If a bring-your-own pack came from real records, it passes through the
  keyed deterministic masking from Prod Echo before load, and the pack is marked as such. Unmasked
  real data is never accepted.

## 6. Slowness and conditions

The question: UiPath runs are slow mostly because the application or its database is slow. A demo
app on a laptop answers in milliseconds, so the demo would flatter every automation equally and
show nothing.

**Where the delay is added.** Outside the app, so no demo app carries delay code.

| Layer | Mechanism | Reproduces |
|---|---|---|
| HTTP, per route | Lab agent delays matching requests, drawing from a latency distribution | A slow search, a slow page load, a slow submit |
| Database link | TCP proxy between app and database adds latency and jitter (Toxiproxy or equivalent) | Every query slower; spinners and timeouts appear in the app as they would for real |
| Faults | Lab agent returns an error or drops a response at a set rate or on the Nth call | Intermittent 503, timeout, response lost after commit |
| Data volume | A large data pack | Slow because the query really is slow |

**Where the numbers come from.**

1. From robot logs: the gap between a click and the next element appearing is the application's
   response time as the bot experienced it. The Prod Echo log parser gives these per activity.
2. From HAR captures, if any exist: per-request timings.
3. From the people who run the bots, when neither exists: "search takes 8 to 20 seconds after
   10 a.m." is a usable profile. It is recorded as an estimate.

A condition profile (`03-contracts.md`) states which of the three it came from.

**Presets** to start: `normal`, `peak-hour`, `db-degraded`, `intermittent-errors`, `timeout`.

**What this shows.** Run the same automation under `normal` and `peak-hour` and compare sessions:
total time, time spent waiting on the app, retries, failures. A UiPath bot with fixed delays and
selector timeouts and an agent that waits on state respond differently to the same slow app, and
that difference is the demonstration.

**Limit.** Injected delay reproduces *how slow*, not *why*. Lock contention, a batch job holding a
table, or a cache stampede are not modelled.

## 7. Twin Lab portal

| Page | Shows and does |
|---|---|
| Automations | The registry: type, surface, owner, apps touched, readiness. Add or re-analyse. |
| Apps | Each demo app: version, stack, status, link to open it, current data pack and condition. Reset. |
| Mapping | Automations against apps. Each cell READY or BLOCKED; a blocked cell lists the missing selectors, routes or tables. |
| Data | Packs per app. Download template, upload, generate synthetic, load. |
| Conditions | Profiles. Apply one to an app or to all. |
| Sessions | Start a session, run the automation by hand, stop. See the request timeline with waiting time, the rows that changed, and anything that changed unexpectedly. |
| Bindings | For a chosen automation: the URLs, test credentials and database address it needs, as a file to download (UiPath config, `.env`, JSON). |

The portal is one more app in the monorepo. It talks only to lab agents and reads the registry from
the repository.

**A session, end to end.** Choose automation → portal checks it is READY → choose data pack and
condition → portal resets the apps and starts recording → download bindings if needed → trigger the
automation in UiPath, a terminal or the agent runtime → stop → review.

## Stack for the platform itself

Python and FastAPI for `lab-agent`, `labctl` and the portal API, which lets the XAML importer from
Application Twin Studio and the contracts from Prod Echo be reused directly. React and TypeScript
for the portal UI. Postgres for portal state, SQLite locally. GitHub Actions. Docker.

Demo apps use whatever the application they copy uses.

## Limits

- A demo app is a copy made from reading code, scoped to what automations use. It can be wrong where
  nobody checked. Every rule starts as `hypothesis`.
- The portal does not start automations and cannot tell that one finished; a person stops the session.
- Isolation is per deployed app. Two people using the same app at once will see each other's
  changes until leases are added.
- Browser applications, APIs and databases only. Desktop, Citrix and mainframe screens are out.
