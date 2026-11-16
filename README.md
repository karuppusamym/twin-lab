# Twin Lab

A platform for **building demo copies of the applications our automations touch, hosting them, and
controlling them from one portal**. The demo apps exist so UiPath, agentic and Python automations can
be run, compared and shown off without production and without waiting on app teams.

Twin Lab does not run the automations. They are triggered where they live (a UiPath robot, a terminal,
an agent runtime). The portal controls what they run *against*: which app version, which data, how
slow.

> **Status: working.** Evidence analysis, briefs, Devin hand-off (package and API), the lab (Docker,
> lab agent with slowdowns and data reset), readiness checks, data packs and the portal all run and
> are tested (450+ tests). Two reference apps prove it: a React app built from its source, and a
> QTT stand-in built from recordings and DDL only. See `docs/CHANGELOG.md` and `docs/04-plan.md`.

## Start here

**Setting it up in the office:** [docs/office-setup.md](docs/office-setup.md). In short:

```bash
pip install -e ".[dev]"
```

```bash
python -m playwright install chromium
```

```bash
labctl portal
```

Then open http://127.0.0.1:8790. Every portal action is also a `labctl` command (`labctl --help`).

Checks:

```bash
labctl validate
```

```bash
python -m pytest -q
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
| [docs/07-prod-echo-coverage.md](docs/07-prod-echo-coverage.md) | Every Prod Echo plan item and Application Twin Studio feature, and where it lives now. |
| [docs/office-setup.md](docs/office-setup.md) | Installing and running it on an office machine; the everyday flow. |
| [docs/devin.md](docs/devin.md) | Building apps with Devin: the manual package and the API. |
| [docs/factory-playbook.md](docs/factory-playbook.md) | What the AI builder must do; `AGENTS.md` is the short form. |

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
