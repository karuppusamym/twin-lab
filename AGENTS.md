# Instructions for AI coding agents

This repository builds demo copies of real applications so automations can be run against them.
Full playbook for building a demo app: [docs/factory-playbook.md](docs/factory-playbook.md).

## Building a demo app

- Your input is `apps/<app>/BRIEF.md` and the source files it lists. Build only what it puts in
  scope, in the same framework and versions, with the same element ids and classes.
- Write only under `apps/<app>/`. Never edit `BRIEF.md`, `db/schema.sql`, or `app.yaml` outside its
  `run:` section.
- Every rule stays `verification: hypothesis`. Never set `owner_approved` or `reference_verified`.
- One `Dockerfile` per app; the app reads its database address from the environment.
- Record what you did, what you assumed and what you could not check in `apps/<app>/BUILD-NOTES.md`.

## Before opening a pull request

```bash
python -m pytest -q
```

```bash
labctl validate
```

```bash
docker build -t twinlab/<app>:dev apps/<app>
```

Run the container and check it as the playbook describes. Report results as they are; never
loosen a check to make it pass.

## Hard rules

- Synthetic data only. No real customer data, account numbers, hostnames or credentials.
- Nothing under "Out of scope" in a brief is built, linked or stubbed.
- Unknown behaviour is listed as an assumption, not guessed silently.
