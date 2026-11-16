# Direction

## What we have today

| | Prod Echo (`prod-echo/`) | Application Twin Studio (`ApplicationTwinStudio/`) |
|---|---|---|
| State | Docs, contracts, ADRs, 30-item plan. No code. | Working Python slice: 8 sample twins, 7 processes, 41 scenarios, portal, tests. |
| How an app is reproduced | Capture the DOM in production next to the bot, regenerate screens as static HTML over a masked copy of production data | Hand-written YAML spec per app, interpreted by one engine that renders web, REST, SOAP, 3270 and SQL |
| What it proves | Agent repeats the bot's interactions on the same selectors (parity against a production trace) | Original and replacement reach the same business outcome against a model (A/B diff, assertions) |
| Needs before anything works | Approvals to capture on a robot and read replicas | Someone to write each twin spec and each scenario by hand |
| Look and feel of the app | Same DOM as the real app | Generic server-rendered pages; nothing like the real app |

## Why neither is what we want now

The goal has shifted to **showing what is possible** on apps people recognise, with QTT as the first
real target. That changes three things:

- **The demo app must look and behave like the real application**, in its own framework. Application
  Twin Studio's generic pages do not, and one engine cannot host a React 16 app and an Angular 14 app
  with their real DOM and selectors.
- **We cannot wait for production capture approvals.** Prod Echo's first real output sits behind
  weeks of tickets. The real app's *source code* is a faster and richer starting point: the backend
  code gives the tables and the rules, the frontend code gives the screens and selectors.
- **The platform should not be the place automations run.** Automations are triggered manually, where
  they live. The platform builds, hosts and controls the apps.

So Twin Lab is a third thing: **a factory and a control room for real demo apps**.

## The planning notes, mapped to decisions

| # | Note | Decision in this design |
|---|---|---|
| 1 | What automations are there; onboarding | **Automation registry** is the entry point. Every automation gets an `automation.yaml` and an analysis of what it touches. |
| 2 | Demo app build and check in to GitHub; UiPath, agentic, Python; "we need to know the automation, else no point building the demo app" | **Demand-driven scope**: the build brief for an app is the union of what its registered automations touch. No registered automation, no app. |
| 2 | Source code repo; Devin / Copilot build; where saved and hosted | **App factory**: an AI coding agent reads the real source repo plus the build brief and opens a pull request into `apps/<app>/`. Saved in the monorepo; hosted as a container per app. |
| 2 | DB tables can be retrieved from backend code; tables for the UI derived from the backend | Source analysis step extracts entities, migrations and SQL into `db/schema.sql`, and DTOs and routes into the API surface. The UI is built against those. |
| 2 | Host each app, CI/CD per app; why not a single app | **One container and one pipeline per app.** Reasons in "Why not one app" below. |
| 3 | Automation mapped to application after the demo app is built | **Conformance check**: each automation's selectors, routes and tables are tested against the built app; result is READY or BLOCKED with the gaps listed. |
| 4 | Triggered manually, but app controlled from the portal | Portal never executes automations. It controls apps through a **lab agent** beside each app: reset, data, conditions, recording. |
| 5 | Twin Lab: app, run app, data; synthetic or bring your own (Excel with the demo's tables and fields) | **Data packs.** The portal generates an Excel template from the app's schema; upload validates and loads it. Synthetic packs come from a seeded generator. |
| 6 | One Git repo with all demo apps, multiple CI/CD | **Monorepo** with path-filtered pipelines calling one reusable workflow. |
| 7 | All kinds of automation: frontend, backend only, combo; API, DB calls | Each demo app exposes web UI, API and database. `automation.yaml` declares `surface` and what it uses per interface. |
| — | Platform is just for building demos and hosting | Scope line: no automation runner, no A/B certificate engine in v1. |
| — | Demo shows the art of the possible; QTT is the actual app | Milestone 1 is a small showcase app end to end; Milestone 3 is QTT. |
| — | UiPath is slow because the app or DB is slow; how do we replicate that | **Condition profiles**: latency and faults injected outside the app, at the HTTP layer and on the database link, calibrated from robot logs where we have them. See `02-architecture.md`. |

## Why not one app

Hosting every demo screen inside a single application was the Application Twin Studio approach. It
does not survive real front ends:

- **Frameworks and versions differ.** Two major versions of React cannot share a page cleanly; an
  Angular app and a React app cannot share a build. Pinning each app to the real one's version is
  the point.
- **Selectors come from the framework.** Generated ids, component class names, ARIA from a specific
  UI library. A UiPath selector that works on the real app works on the demo only if the same
  library renders it.
- **Independent release.** When QTT changes, only the QTT demo is rebuilt and redeployed.
- **Independent conditions.** "QTT database is slow, the CRM is fine" needs separate processes.
- **Blast radius.** A broken build of one demo does not take the others down.

The cost is more containers and pipelines. One reusable pipeline and one lab agent image, the same
for every app, keep that cost flat.

## What is kept, changed and dropped

### From Prod Echo

| Concept | Fate | Note |
|---|---|---|
| Build only the screens the bots use | **Kept**, central | Now called demand-driven scope. |
| XAML parser, selector extraction, process map (I-07) | **Kept** | Becomes the UiPath analyser in the registry. |
| Robot log parser (I-08) | **Kept, repurposed** | Its timestamps calibrate slowness profiles. |
| Selectors preserved exactly | **Kept** | Enforced by the conformance check instead of by DOM templating. |
| Local harness first, nothing done on description alone | **Kept** | The showcase app plays the harness role. |
| Baselines, reset, lease (I-23, I-25) | **Kept, simplified** | Baseline = a loaded data pack; reset returns to it. Leases only if two people share an app. |
| Keyed deterministic masking (I-17, ADR-003) | **Kept, optional** | Applies only when a bring-your-own pack came from real data. |
| Cross-app identity `links` in the subset spec | **Kept** | A data pack that spans apps is validated against them. |
| Behaviour from evidence (ADR-008) | **Changed** | Behaviour now comes from reading the real source, not from before/after snapshots. |
| NetLog, CDP recorder, screen capture (I-03 to I-06) | **Optional evidence** | Useful input to a build brief when available. Not a prerequisite. |
| DOM-to-template app generator (I-19 to I-22) | **Dropped** | Replaced by AI-built apps in the native framework. |
| Schema inventory, value binding, closure, extractor, Synapse (I-10 to I-16) | **Dropped for now** | Production data extraction is out of scope. Returns if a real-data environment is wanted later. |
| Parity against a production bot trace (I-24) | **Deferred** | Sessions record what an automation did; comparing two sessions is a later item. |
| No LLM-generated records in the data path | **Kept** | Synthetic packs come from a seeded generator, not from a model. |
| No production hostnames, credentials or data in the repo | **Kept** | |

### From Application Twin Studio

| Piece | Fate | Note |
|---|---|---|
| UiPath importer (`ats/importers/uipath.py`) | **Reused** | Safe static XAML parsing; start of the UiPath analyser. |
| Onboarding gaps and READY / BLOCKED (`ats/onboarding.py`) | **Reused as a concept** | Now checked against a running demo app. |
| Bindings contract (`ATS_BINDINGS`, `ats/sdk.py`) | **Reused** | How an automation learns the demo app's URLs and credentials. |
| Fault injection, isolation per environment | **Reused as a concept** | Moves out of the engine into the lab agent. |
| Before/after state diff, "nothing else changed" (`ats/validation.py`) | **Reused** | Shown per session. |
| Trust rules for AI-authored changes (`tools/guard_ai_changes.py`, `docs/devin-playbook.md`) | **Reused** | The factory is AI-built, so this matters more. |
| Verification levels (`hypothesis`, `owner_approved`, ...) | **Reused** | Every rule in a demo app starts as `hypothesis`. |
| YAML twin spec and behaviour engine | **Dropped** | Real apps replace interpreted specs. |
| Generic web adapter and portal page | **Dropped** | |
| TN3270 and SOAP adapters | **Parked** | Kept in the old repo. A mainframe demo app can wrap them later. |
| Scenario assertions, A/B certificate | **Deferred** | Out of scope for a demo platform; revisit after QTT. |

## What a demo app is, and is not

It **is** the real application's screens, selectors, API shapes and tables, in the real framework,
with enough behaviour for the registered automations to complete, running on data we control.

It **is not** the real application. Business rules are reproduced only where an automation depends
on them, each marked `hypothesis` until an application owner confirms it. A passing automation on a
demo app shows the automation works against that copy. It is a demonstration, not a certificate.
