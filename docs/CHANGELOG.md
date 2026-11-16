# Changelog

One paragraph per plan item: what exists now, how to run it, what was left open.

## 2026-11-15 — End to end without source: QTT stand-in, real bots, review fixes
`apps/qtt/` was built by a stand-in Devin from its package alone: 4 recordings and SQL Server
DDL, with no source. Both QTT automations are READY (65 and 4 checks). Running the real Python
SLA bot then found three problems that conformance could not see.

1. **The bot needed fields the brief did not carry.** It reads `token` from `POST /api/login` and
   `items` from `GET /api/reviews`. The fix is round 2 in `apps/qtt/BUILD-NOTES.md`. Lesson: response
   fields a bot's own code reads must reach the brief.
2. **`localhost` cost about 20 s per connection on Windows.** It tries IPv6 first, and the lab
   ports listen on IPv4 only. Bindings and clients now use `127.0.0.1`. The bot run went from
   137 s to 2.7 s.
3. **Profiles for `app: any` applied nothing when sent to the lab agent directly.** They now apply
   to every app. The portal was not affected.

With those fixed, the same bot on the same data shows the slowness lab working:

| Condition | Login | Review list | Whole run |
|---|---|---|---|
| Normal | 32 ms | 14 ms | 2.7 s |
| slow-database | 1.7 s | 1.8 s | 7.8 s |

The admin calls of the review fixes now need a token. Other changes from the reviews:

- Masking is one-to-one, and `sanitize()` reports collisions.
- A Devin session is never created twice, and `labctl devin start --force` overrides that guard.
- Stale polls cannot undo a cancel.
- Reset refuses mismatched baseline files. A baseline from an older agent is upgraded at startup.
- Intervals keep exact precision.
- Admin calls need exactly one `X-Lab-Token` and a JSON body.
- Lab ports bind to `127.0.0.1` unless `TWINLAB_LAB_BIND` says otherwise.
- The admin token is generated in `.lab-state/lab-token`.
- `lab up` sets the first baseline. It also rebuilds the agent image every time, so a `git pull`
  takes effect.
- The agent image build works from any folder.

Open: the factory does not yet extract the fields a bot reads.

## 2026-11-15 — E-01 App model with provenance (done)
Everything known about an application now goes into one model, `apps/<app>/model.yaml`, and the
brief, schema and `app.yaml` are generated from it alone. Builders exist for three kinds of
evidence: source code (`observed`, and complete for screens, routes and tables), automations
(`declared`) and requested flows in `flows/<id>.yaml` (`described`). `merge` keeps the strongest
basis and every source of each item, never merges paths that only look alike (`/cases/search` and
`/cases/{id}` stay separate), and turns disagreements into conflicts: a selector the source does
not show, a route, screen, table or column that complete evidence lacks. A declared item is not a
conflict when nothing complete covers its area, which is what makes automation-only and
flow-only briefs possible. `labctl brief` takes `--source` optionally and reads flows; a requested
flow brings its screen, routes and tables into scope. Paths in the model are never absolute. For
the showcase the scope, rules and schema are unchanged from T-05. Not yet: HAR (E-02), DDL and API
descriptions and mode labels (E-03).

## 2026-11-15 — Git repository
`twin-lab` is a Git repository. `main` holds the platform (T-00 to T-05); the showcase demo app
from T-06 is on branch `app/showcase`, which stands in for the pull request T-06 could not open.
`.gitattributes` keeps Unix line endings, which the Linux image builds need. A fresh clone showed
that an unpinned ruff gives different lint results in a new environment, so ruff is now pinned.
No remote yet.

## 2026-11-15 — T-06 Factory playbook and first build (done)
`docs/factory-playbook.md` and `AGENTS.md`, adapted from Application Twin Studio, tell a coding
agent how to build a demo app from a brief. A fresh agent given only those files, the brief and the
source built `apps/showcase/`: one Docker image serving the React front end and the FastAPI back
end on port 8000, backed by a Postgres server named in `DATABASE_URL`. An independent review
(42 checks the builder never saw: routes, rules, screens in a real browser, the cascade, the
conditional field, the absence of the out-of-scope screen) passed in both rounds. Round 2 was
caused by the brief, not the agent: it had kept the source's SQLite default and emitted no
identity columns. `labctl brief` gained `--db-engine` with per-dialect DDL, tested on a real
Postgres. The log is `apps/showcase/BUILD-LOG.md`. Not done: no pull request (no Git repository);
the UiPath bot itself has not been run against the app.

## 2026-11-15 — T-05 Source analysis and build brief (done)
`labctl brief <app> --source <folder> [--system <name>] [--force]` reads the real application's
source statically (React + React Router front end, FastAPI + SQLAlchemy back end; anything else is
refused as unsupported), joins it with the demand report and writes `apps/<app>/BRIEF.md`,
`apps/<app>/db/schema.sql` and, only if missing, a first `apps/<app>/app.yaml`. Scope follows the
demand: demanded screens, the API calls their source makes, routes automations call directly, a
health route, the tables those routes use, tables automations query, and foreign-key parents.
Each item says why it is in. Rules come from `raise` statements with a 4xx status and from request
body constraints, all `hypothesis`. Demanded selectors are checked against the screen's source.
For the showcase: 3 screens, 9 routes and 7 tables in scope; the canned-responses screen (added to
the reference app for this purpose), its 2 routes and its table are out. The static schema is
tested against SQLAlchemy's own metadata. Known limits: one stack pair only; a rule written as
anything other than a `raise` with a status (a custom exception handler, a database constraint) is
not found; the brief is not reviewed by a person yet.

## 2026-11-15 — T-04 Demand report (done)
`labctl demand <app>` prints the union of what registered automations touch on one application:
screens with selectors, routes, tables with columns, each line naming the automations that need it.
`--json` for machines. An automation with unresolved gaps is flagged, because the report may be
missing what those gaps hide.

## 2026-11-15 — T-03 UiPath analyser (done)
`labctl automation analyse <id> [--write]` reads a UiPath project statically and fills `touches`,
`analysis.gaps` and `analysis.notes` in `automation.yaml`. It follows invoked workflows and
addresses built through assigned variables, converts UiPath selectors to CSS, and reads tables
from literal SQL. Rejects DTDs, oversized files and workflow paths outside the project. A sample
project, `automations/uipath-case-update/`, is written against the reference app and analyses
with no gaps. Known limits: classic activities only (modern-design `N…` activities become gaps);
every XAML it has seen is hand-authored, so a real Studio export is still untested; a click that
changes screen cannot be seen statically, so elements after a click get a note, not a new screen;
it will not overwrite a file with `reviewed_by` set unless `--force` is given.

## 2026-11-15 — T-02 Showcase source app (done)
`reference/case-workbench/`: a neutral case-management app (React 18 and TypeScript, FastAPI and
SQLAlchemy, SQLite) with sign-in, search and case detail over seven tables and a deterministic
seed. It has a cascading dropdown, a field that appears only on escalation, and five server-side
rules; see its README. Thirteen backend tests; the frontend was checked by hand in a browser and
has no automated tests. Also in this change: the top-level `platform/` folder was renamed `lab/`,
because pytest could load it in place of Python's built-in `platform` module.

## 2026-11-15 — T-01 Contracts (done)
Pydantic models for `automation.yaml`, `app.yaml`, data pack, condition profile, session and
bindings in `lab/labctl/labctl/models.py`, each with an example in `specs/`. Unknown keys are
errors. `labctl validate [files]` prints file, field and reason and exits 1 on any problem; with no
arguments it checks every contract file in the repository. Run: `pytest -q` (17 tests).

## 2026-11-15 — T-00 Monorepo scaffold (built, not closed)
Folder layout, root `pyproject.toml`, `labctl` with `validate` and `automation | app | data |
condition` groups (each has `list`), ruff and pytest configured, `.github/workflows/platform.yml`
written. Lint and tests pass locally on Windows, Python 3.13. Left open: the folder is not a Git
repository, so the workflow has never run and "fresh clone" is unshown. `uv` is not installed
here, so setup uses `pip install -e ".[dev]"`.

## 2026-11-15 — Design and plan
Docs 01 to 05. No code.
