# Twin Lab

Twin Lab builds **safe copies of the applications our bots work in**, runs them in a lab, and
judges every bot run there: with your own data, when the system is slow or failing, and with a
clear PASS or FAIL. The real application is never touched. It combines Prod Echo (evidence, real
data, robot logs, parity) and Application Twin Studio (readiness, bindings, fault injection, the
trust rules).

Twin Lab does not run the bots: they are triggered where they live (a UiPath robot, a terminal, an
agent runtime). It controls what they run *against*: which copy, which data, how slow, and what
counts as a correct run.

> **Status: working, tested, used end to end on two sample apps.** About 630 automated tests; CI
> must pass before anything merges into `main`. Not yet done: a run against a real OpenShift
> cluster, and anything that needs production access (see "Limits").

## Start here

[docs/office-setup.md](docs/office-setup.md) installs it on an office machine (Windows or Linux):

```bash
pip install -e ".[dev]"
```

```bash
python -m playwright install chromium
```

```bash
labctl portal
```

Open http://127.0.0.1:8790 and press **Start here**. It explains Twin Lab in four boxes, ticks off
what is done, and has one button for the next step. Every portal action is also a `labctl`
command (`labctl --help`).

## How it works

```
 bots' code ─┐                                   ┌─► lab: the demo app, its database, a lab agent in front
 recordings ─┼─► app model ─► brief ─► Devin ─► PR ─►    data · slowness · faults · reservations
 DDL, APIs  ─┘   (every item says                  │
 robot logs      where it came from)               └─► sessions: requests, row changes, tables used
                                                         ─► readiness · verdict · parity · provenance
```

1. **Register the bots.** UiPath projects and Python bots (agents included) are read without
   running them: screens, selectors, API calls, the fields each call sends and reads, SQL tables.
2. **Add evidence about the real app.** Recordings are the most useful kind. The recorder captures:
   - every page, before and after each action, with a picture;
   - the network, with bodies;
   - iframes from any site and web components (open or closed);
   - text drawn on a canvas, read by OCR;
   - embedded PDFs.

   Typed values are never kept. Also add the database schema (DDL), API descriptions,
   screenshots, and source code when there is any (rarely).
3. **Generate the brief.** One app model is built from all the evidence, scoped to what the bots
   need. It lists screens, selectors, routes with their fields, tables, rules and behaviours, and
   each item says what it is based on.
4. **Build.** Devin builds the demo app from the brief and opens a pull request, through its API
   or a downloaded package. CI checks it, and a guard stops AI-built changes from approving
   themselves.
5. **Run in the lab.** One copy per app, in Docker on the Twin Lab machine, or on OpenShift. A lab
   agent sits in front of the app and its database and provides:
   - data: Excel, synthetic, named packs, masked real data;
   - reset, and earlier baselines to restore, pin or quarantine;
   - conditions: peak hour, slow database, errors, timeouts, or calibrated from robot logs;
   - reservations, so one person's run is not disturbed.
6. **Judge each run:**
   - **Readiness**: does each bot find everything it needs? READY, or BLOCKED with what is
     missing.
   - **Sessions**: every request with its waiting time, every row changed, every table used.
   - **Verdict**: PASS when a run changed only what the bot is expected to change.
   - **Provenance**: which written values the bot typed or chose, and which the app set.
   - **Parity**: a production run against the same bot's run in the lab.
   - **Fidelity replay**: does the copy behave like the recording of the real app?
   - **Behaviour comparison**: does the app behave the same in UAT and production?

## What is in the repository

| Folder | What it is |
|---|---|
| `lab/labctl/` | `labctl` and the portal: analysers, factory, recorder, lab control, verdicts, real data, OpenShift |
| `lab/lab-agent/` | The lab agent: HTTP proxy, database link with slowdowns, data load and reset, sessions, leases |
| `apps/` | Built demo apps: `showcase` (from source) and `qtt` (from recordings and DDL only) |
| `automations/` | Registered bots: UiPath case update, an agentic triage bot, a UiPath QC review and a Python SLA report |
| `reference/` | The sample "real" applications the demo apps copy; invented, with synthetic data (see below) |
| `conditions/`, `flows/`, `specs/` | Condition profiles, written flows, an example of every contract file |
| `tools/` | Harness bot, fixture refresh, evidence ablation, the AI-change guard |
| `.github/workflows/` | Tests, the guard, a pipeline per app, OpenShift deploy |

**About the samples.** We have neither the real QTT nor its source, so `reference/qtt-sample/` is
an invented stand-in: its name, loan-review domain, screens and data were made up to test the
pipeline. `apps/qtt/` was built from recordings and DDL of that invented app alone. It proves the
process works without source code; it says nothing about the real QTT. For a real application,
record its flows in UAT, ask the DBA for its schema and register its real bots.

## Limits

- One shared copy per app (reserve it when that matters), not one per run.
- No browser recorder can read Citrix, remote desktops, plug-ins or desktop windows; those apps
  get a backend-only copy from schema, APIs and robot logs.
- OCR is optional. It only reads text drawn on a canvas; Windows has an engine built in. Every
  other kind of screen is read from its HTML.
- Elements inside closed web components are recorded but cannot be selected by a bot check.
- Binding values on *production* runs needs approval to capture production; on lab runs it is done.
- The OpenShift deploy is built and tested locally (images run as OpenShift runs them) but has
  not run against a real cluster.

## Read in this order

| Doc | What it answers |
|---|---|
| [docs/office-setup.md](docs/office-setup.md) | Installing and running it; the everyday flow; troubleshooting |
| [docs/01-direction.md](docs/01-direction.md) | What was kept from Prod Echo and Application Twin Studio, and why |
| [docs/02-architecture.md](docs/02-architecture.md) | The pieces and how they fit |
| [docs/03-contracts.md](docs/03-contracts.md) | The files everything agrees on: `automation.yaml`, `app.yaml`, data pack, condition, session |
| [docs/06-evidence.md](docs/06-evidence.md) | Building without source: kinds of evidence, what a recording captures, privacy |
| [docs/08-evidence-ablation.md](docs/08-evidence-ablation.md) | What each kind of evidence finds, measured |
| [docs/10-certification.md](docs/10-certification.md) | Which technologies are proven on a real application: the gates, the matrix, adding one |
| [docs/11-session-recorder.md](docs/11-session-recorder.md) | Design (not built): one recorder across web, desktop and mainframe applications |
| [docs/realdata.md](docs/realdata.md) | A masked slice of a real replica in a lab |
| [docs/devin.md](docs/devin.md) | Building apps with Devin: package and API |
| [docs/factory-playbook.md](docs/factory-playbook.md) | What the AI builder must do (`AGENTS.md` is the short form) |
| [docs/ocp.md](docs/ocp.md) | Running the labs on OpenShift: pipeline, setup, rollback |
| [docs/07-prod-echo-coverage.md](docs/07-prod-echo-coverage.md) | Every Prod Echo item and ATS feature, and where it lives |
| [docs/04-plan.md](docs/04-plan.md) | The build plan with a definition of done per item |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | What changed, round by round |
