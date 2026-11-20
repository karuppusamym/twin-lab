# Contributing to Twin Lab

Thank you for helping. This page explains how to propose a change and what a pull request needs
before it can merge. By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

- **Synthetic data only.** Never commit real customer data, account numbers, hostnames, user
  names, passwords, tokens or recordings of a production system. Recordings and packages in the
  repository must be of demo or reference applications.
- **No secrets** in manifests, logs, fixtures or test output.
- **Do not loosen a check to make it pass.** If a check is wrong, fix the check in its own pull
  request and say why.
- Changes to the checks that judge demo apps (`automations/`, `flows/`, `lab/`, `conditions/`)
  never go in the same pull request as an app under `apps/`.
- AI coding agents follow [AGENTS.md](AGENTS.md); a person reviews what they open.

## Getting set up

You need Python 3.11 or newer.

```bash
pip install -e ".[dev]"
```

```bash
python -m playwright install chromium
```

[docs/office-setup.md](docs/office-setup.md) has the full setup, Windows included.

## Making a change

1. Open an issue first for anything larger than a small fix, so the approach can be agreed.
2. Branch from `main` (`wp/<topic>` for tooling, `build/<app>` for an AI-built demo app).
3. Keep the pull request to one topic. Add or update tests with the code.
4. Add an entry at the top of [docs/CHANGELOG.md](docs/CHANGELOG.md): what changed, in plain
   words, and what is still open.
5. Run the checks below and paste the results into the pull request.

## Checks before a pull request

```bash
python -m ruff check .
```

```bash
python -m ruff format --check .
```

```bash
python -m pytest -q
```

The full suite takes about half an hour. Tests that drive a real desktop take over the mouse
and run only with `TWINLAB_DESKTOP_GUI=1`.

## Review and merge

- CI runs guard, the tests, the app checks and the end-to-end check. A pull request merges only
  when every check has finished and passed; auto-merge is not used.
- `develop` is kept level with `main` after each merge.
- Branches are deleted once merged; only `main` and `develop` live on.

## Licence

Twin Lab is licensed under the [Apache License 2.0](LICENSE). By submitting a contribution you
agree that it is licensed under the same terms (section 5 of the licence), and that you have the
right to submit it.

## Reporting a security problem

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).
