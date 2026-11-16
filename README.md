# Twin Lab

A platform for **building demo copies of the applications our automations touch, hosting them, and
controlling them from one portal**. The demo apps exist so UiPath, agentic and Python automations can
be run, compared and shown off without production and without waiting on app teams.

Twin Lab does not run the automations. They are triggered where they live (a UiPath robot, a terminal,
an agent runtime). The portal controls what they run *against*: which app version, which data, how
slow.

> **Status: design, plan, repository scaffold and contracts.** Nothing that builds or runs a demo app
> exists yet; see `docs/CHANGELOG.md`. This folder replaces neither `prod-echo/` nor
> `ApplicationTwinStudio/`; it takes ideas and code from both. See `docs/01-direction.md`.

## Setup

```bash
pip install -e ".[dev]"
```

```bash
labctl validate
```

```bash
pytest -q
```

## Read in this order

| Doc | What it answers |
|---|---|
| [docs/01-direction.md](docs/01-direction.md) | What changes from Prod Echo and Application Twin Studio, and why. Each of the planning notes mapped to a decision. |
| [docs/02-architecture.md](docs/02-architecture.md) | The pieces: automation registry, app factory, monorepo and pipelines, lab agent, data packs, slowness, portal. |
| [docs/03-contracts.md](docs/03-contracts.md) | The files everything agrees on: `automation.yaml`, `app.yaml`, data pack, condition profile, session. |
| [docs/04-plan.md](docs/04-plan.md) | Ordered build items with a Definition of Done each. |
| [docs/05-open-questions.md](docs/05-open-questions.md) | What must be answered before specific items, starting with QTT. |
| [docs/06-evidence.md](docs/06-evidence.md) | Building apps without source code: kinds of evidence, the app model, build modes, privacy. |

## The idea in six lines

1. **Start from need.** A demo app is built for registered automations, or for flows a person asks
   for when there is no automation code. Nothing beyond that is built.
2. **Gather evidence**: automation code, HAR recordings, database schema, API descriptions,
   screenshots, a description of the flow, source code when there is any. Real source is usually
   not available.
3. **Build a demo app** per application with an AI coding agent, from one app model that records
   where every screen, element, route and table came from. Each app says how close it is: copy,
   reconstruction, skeleton, backend only, or sample.
4. **One Git repository, one pipeline per app.** Each app is its own container with its own database.
5. **Map** each automation to the built apps and check it mechanically: every selector, route and
   table it needs must exist.
6. **Twin Lab portal**: pick data (synthetic or your own Excel), pick conditions (normal, slow app,
   slow database), reset, then trigger the automation by hand and see what it did.
