# Changelog

One paragraph per plan item: what exists now, how to run it, what was left open.

## 2026-11-17 — Any coding agent builds; several apps in one recording; SQLite
- **One recording, several applications.** `labctl evidence split <recording>` proposes the
  applications in a recording (by the site each step's page belongs to; sign-on pages are left
  out; an embedded iframe is listed so it can be separated), writes a proposal file a person can
  edit (rename, merge, split one host by path, drop), and with `--apply` writes one recording per
  app under `evidence/<app>/` plus the whole sequence across apps in
  `evidence/_sequences/<recording>.flow.yaml`.
- **Wider technology detection.** A recording now names the server technology (PHP, Java with
  JSP/Struts/JSF/Spring, .NET Web Forms/MVC/Blazor, classic ASP, Python, Ruby, Node, ColdFusion,
  Oracle APEX/Forms, SAP, Siebel, ServiceNow, Salesforce), the web server, client libraries and
  UI suites (jQuery, Bootstrap, PrimeFaces, Telerik, Kendo, DevExpress and others), each with
  the marker that showed it and a confidence. Markers are header product names, cookie names and
  file names: never a value. The brief prints them; a part with no marker says so.
- **Tables from HTML grids.** Server-rendered pages with no JSON now give tables: a data grid's
  headers become columns with types read from cell shapes, detail forms add columns, selects and
  radios become code lists. These are the weakest evidence, so DDL or JSON wins.
- **The build package is for any coding agent.** The zip holds `BUILD_TASK.md` (was
  `DEVIN_TASK.md`): same technology, same selectors, tables and data only. When the app is
  already built it says what the new brief adds. `labctl build package|start|status|attach-pr`;
  the builder is `manual` (default), `claude` (a headless Claude Code session in a worktree on
  `build/<app>-<digest>`; commits and opens a pull request only with `--pr`) or `devin`, set by
  `builder:` in `twinlab.local.yaml` or `TWINLAB_BUILDER`. The guard treats `build/...`
  branches as AI-built.
- **SQLite** is a third built-in database (`labctl db use <app> sqlite`): one file on a volume
  the app and the lab agent share, no database container or port. Load, reset, baselines, row
  changes per session and copies work. Slow-database conditions cannot apply and are refused or
  reported as unavailable; bots get no database address.
- **Data shaped by what was recorded.** `labctl data observe <app>` writes
  `apps/<app>/db/observed-profile.yaml`: code values seen (kept as seen, listed under
  `verbatim:` for review), and for everything else only its shape. `labctl data generate
  --from-observed` follows it and adds the rows a recorded flow opens. Names, emails, phone
  numbers and long identifiers are never copied.
- Left open: a live build by a Claude session has not been run; desktop and mainframe capture;
  SQLite on OpenShift.

## 2026-11-16 — The "partly" items closed; end to end in one command
- **A lab copy per run.** `labctl lab copy <app>` starts `<app>.run-1`: its own database, agent
  and app on free ports, from the app lab's baseline (or `--data current|app|<pack>`), with the
  app's image. `labctl lab copies`, `labctl lab drop`. `harness run --copy` makes one for the run
  and drops it; `bindings` and `conformance` take `--on <app>.<copy>`. Copies need Twin Lab's own
  database (postgres or sqlserver), not a DBA's server.
- **Fidelity replay is a gate.** Readiness reads the app's newest replay: BLOCKED when it differs
  on an element the bot uses, noted when it differs elsewhere, not counted when the app changed
  after it. `--require-fidelity` also blocks when there is none. `behaviour fidelity --app` keeps
  a command-line replay where the portal and readiness find it.
- **Bindings readable by Application Twin Studio automations**: each app also has `http`, and
  the file has `arguments` (resolved) and `output_path`.
- **`labctl e2e <automation>`**: contracts, lab, copy, readiness, bot run under a condition
  (session, verdict, parity with `--golden`), fidelity replay, cleanup, in one report. The
  `e2e` workflow runs it on GitHub for `uipath-case-update` under `slow-database`.
- The lab agent answers `GET /_lab/data/baseline` (the rows Reset would put back).

## 2026-11-16 — Demo databases on SQL Server, Oracle or Postgres
Until now every demo database was a Postgres container. Now each app chooses:

- **`postgres`** (default) or **`sqlserver`**: a container Twin Lab runs (SQL Server 2022
  Developer, with the app's database, an owner and a rows-only `bot` login created on Start).
- **A DBA's server**: SQL Server, Oracle or Postgres, any number of them, configured in
  `twinlab.local.yaml` `databases:` (passwords from environment variables) or the
  `TWINLAB_DATABASES` secret on OpenShift. **Settings > Database servers** lists and checks them;
  the app page (or `labctl db use`) picks one.
- **Tables from one `schema.sql` on any engine.** Twin Lab creates them on Start, leaving existing
  ones alone. **Views become tables** of the same name with the view's columns, so sample rows
  load straight in. Procedures, functions and triggers are listed as not created.
- **The lab agent** loads, resets, takes baselines and records sessions on SQL Server and Oracle
  too (identity columns reseeded, circular keys handled). Conditions apply through the same
  database port.
- **App and bots** get the engine, URL and an ADO.NET connection string (`DB_*` variables,
  `{{app.db_engine}}`, `{{app.db_connection_string}}`).
- **OpenShift**: an app on a DBA's server gets no database pod; a Job creates the tables and a
  Secret written on each deploy holds the addresses. `OCP_EXPOSE_DB` / `--expose-db` puts the
  database link on a NodePort for bots outside the cluster.
- **Devin** is told the engine, its driver and the `DB_*` variables, that views are tables, and,
  for an app already built, to enhance it rather than start again.

Tried live on SQL Server 2022: tables (and a view-table) created, explicit ids loaded into
identity columns, refusals for missing parents, reset, a session catching the app's update, a bot
login that can change rows but not drop tables, and slow-database through the proxy (≈300 ms to
≈1,700 ms). Oracle is tested against its generated SQL only, not a live database.

## 2026-11-16 — Recordings: frames, shadow DOM, canvas and PDFs
The recorder used to read only the top page's HTML. Now:

- **Iframes and frames, same origin or not.** A listener runs in every frame and reports to the
  top page. Actions inside a frame are recorded as `iframe#x >>> button#save`. The frame's page
  is written into the snapshot as `<tl-frame>`, read from outside by Playwright when the frame
  comes from another site.
- **Shadow DOM, open and closed.** A hook installed before the page's own code reaches closed
  roots; roots appear as `<tl-shadow>`. A click inside a closed root is recorded on the real
  element, not its host.
- **Canvas.** A picture of each canvas, and the text OCR reads in it: Tesseract, or Windows'
  built-in OCR with nothing to install.
- **Embedded PDFs.** Kept with their text and form field names (never values), fetched with the
  page's sign-in when the browser does not load them itself. They become screens in the app
  model.
- **Selectors into frames.** One convention everywhere: the UiPath analyser writes iframe
  selectors that way; conformance, the harness bot and fidelity replay enter the frames.
- **Scripted recordings** now have a picture per step too.

Tried on a test page with a same-origin and a cross-origin iframe, open and closed components, a
chart drawn on a canvas, and a PDF with a form field. Each was recorded; Windows OCR read "Total
due" from the chart, and fidelity replay went into both frames. A Citrix session, a plug-in or a
desktop window has no HTML to read; [06-evidence.md](06-evidence.md) says what to do instead.

## 2026-11-16 — Round 5: a portal anyone can start with, OpenShift, the rest of Prod Echo
QTT and Synapse are left aside on purpose; everything else from Prod Echo is built.

- **Portal for newcomers:**
  - **Start here** explains Twin Lab in four boxes and ticks off what is done. It has one
    **Do it now** button for the next step, and a glossary in plain words.
  - Each app shows its progress and its next step, on the Apps list and at the top of its
    page.
  - People type their name once.
  - Errors from the lab read as sentences.
- **OpenShift** ([ocp.md](ocp.md)):
  - `labctl ocp render / deploy / smoke`.
  - Pipeline `deploy-ocp.yml`: it builds the agent and app images to GHCR, then deploys each
    app (database, setup job, lab agent, app, Route) when `OCP_SERVER` and `OCP_TOKEN` are
    set. A push deploys only the apps it changed, or all of them when the lab agent changed.
  - The images now run as a random user ID, as OpenShift requires. This was checked locally
    for both apps. It has not run against a real cluster.
- **Where each value came from (I-11, I-12).** A bot run is joined with its lab session; each
  written column is typed by the bot, chosen by it, or set by the app, and codes are traced
  back to the chosen label. Tried on the showcase:
  - typed `C-1013` became `case_id 13`;
  - the chosen "Payments" became `category_id 2`;
  - `updated_at` was set by the app.
- **Reservations (I-23).** Reserve a shared lab: others can look, but their changes are refused
  with who and until when. Tried live in the portal.
- **Baseline quarantine.** Mark data unusable, with a reason; it can no longer be restored or
  reset to.
- **UAT against production (I-22b).** Compare the behaviour of recordings from two places.
- **T-17.** `agentic-case-update`, a triage agent that works out the category from the case and
  explains it in a note. It is READY beside the UiPath bot, with sessions under normal and
  slow-database, both PASS.
- **T-19.** `labctl data load-across` loads one pack into several apps. A linked key missing on
  either side refuses the pack, naming the key; if one app fails, the others are put back.
- **E-04.** [08-evidence-ablation.md](08-evidence-ablation.md) measures what each kind of
  evidence finds against the source.

## 2026-11-16 — Public repository, required checks
The repository is public. `main` accepts a pull request only when `guard` and `test` have passed.
`test` now runs on every pull request; before, a change outside the code paths skipped it.

## 2026-11-16 — Round 4: the open gaps closed
PR 4 was merged. Round 4 builds what round 3 left open, and each piece was tried on the
running labs.

- **A verdict on every session.** An automation declares `expects:`, the row changes it may
  make: tables, operations, columns that may change, and row counts. The usual way is to read
  a session's changes and press **These changes are correct** (`labctl session accept`). After
  that, every session is PASS, or FAIL with each unexpected change marked. Tried live:
  - `uipath-case-update` now expects an insert into `case_audit` and updates to four columns of
    `cases`.
  - `python-qtt-sla-report` reads only, so it expects `[]`.
- **Named data packs and baseline history.** The lab agent keeps every baseline (the newest 20,
  plus pinned ones), and any of them can be restored. **Save current data** stores a named
  pack on this machine, and it loads again by name.
- **Recorder.** `labctl evidence attach`, or the portal, records a browser that is already
  running (a robot's), over the DevTools protocol; the HAR is built from network events. Each
  step keeps the page as the action met it, the page after it, and a PNG. A step's time is now
  when the action happened, not when the page settled. Typed values are still never kept, only
  their shape (`A9`), and that is how input masks are found.
- **Fidelity replay in the portal** (Lab > Fidelity replay), with the test password filled in.
- **`labctl harness run`.** Resets the data, applies a condition and records a session around
  the harness bot. It then imports the robot log, judges the changes, checks parity with
  `--golden` and writes a report. With `--repeat N` it also checks that every run changed the
  same kinds of rows. Tried live on showcase:
  - normal: 5.6 s, PASS;
  - slow-database: 12.8 s, PASS, and parity PASS against the normal run;
  - peak-hour: 19.7 s, PASS.
- **Two harness fixes the slow runs found:**
  - The bot moved on while the login request was still running. Playwright's network-idle
    wait returns at once on a page that was idle before. The harness bot and fidelity replay
    now wait until nothing is in flight.
  - A harness run left its condition applied; the earlier condition is now put back.
- **`tools/refresh_fixtures.py`** checks the test fixtures against a fresh harness run, or
  replaces them with `--write`. It found no drift.
- **Reference app.** The case number field has an input mask (`c1003` becomes `C-1003`), and
  search shows "Searching…" for at least 600 ms. The catalog finds both; the showcase demo app
  predates them, so a fidelity replay reports them.
- **Tables used.** The lab's database link reads the SQL text that passes through it and keeps
  the table names per session, shown on the session page. Tried live: the showcase bot run
  touched 7 tables (writes to `cases` and `case_audit` only); the QTT Python bot read 4.
  `realdata trace-diff --observed-session` uses them.
- **Real data:**
  - `realdata draft-spec` drafts a subset spec from the inventory.
  - `realdata synapse-job` and `synapse-read` hand the extraction to a data platform and check
    what comes back.
- **T-16.** The Python analyser also reports tables in the code's SQL, and Playwright or Selenium
  selectors, that `touches` does not list.

Still open (end of `07-prod-echo-coverage.md`):
- baseline quarantine
- a check of UAT behaviour against production
- value binding, which needs production capture
- agreeing the Synapse file format

## 2026-11-16 — Round 3: the bots' own fields, review fixes, Prod Echo gaps closed
PR 3 was merged, and every merged branch and worktree was removed. Only `main` is left.

**The brief now carries the fields each bot's code uses.** The UiPath analyser follows
`HttpClient`, then `DeserializeJson`, then `obj("field")`. A new Python analyser reads the code
statically and follows each response through variables, loops, functions and `sorted(key=...)`.
For each route both record what the bot sends and reads, for example `/api/login`: sends
`username` and `password`, reads `token`. The brief lists those fields for the builder. It also
flags a read field that the recorded answer lacks. This is exactly the gap that broke the QTT
build in round 2.

**An independent review found 12 problems; all are fixed and tested.**
- **Privacy.**
  - Names returned by an API survived into page snapshots; they are now masked.
  - Snapshots from deleted evidence stayed in `pages/`; regenerating now drops them.
- **Portal.**
  - Without a password, the portal answers only to its own host names, which stops DNS
    rebinding.
  - `git pull` is judged by its exit code, and never waits for a password prompt.
  - Docker and git output is decoded as UTF-8 on Windows.
  - A failed upload leaves nothing half-registered.
  - Recordings run outside the job pool, stop after 30 minutes, and refuse to start from another
    machine.
  - A corrected Devin key is used without a restart.
  - A by-hand build records only its own app's package.
- **Lab.**
  - Bots get a database login, `bot`, that can change rows only. It cannot run programs, read
    files or drop tables. This was verified.
  - The database owner's password is generated per machine.
  - `TWINLAB_LAB_BIND` and `TWINLAB_HOST` are remembered, so the portal and the CLI agree.
  - Admin calls always go to `127.0.0.1`.
- **Real data.** The read-only guard now also refuses:
  - a second statement after `;`
  - writes inside `WITH`
  - `SELECT ... INTO`
  - comments, and `;` in a seed's `where`

**Prod Echo and ATS gaps closed.** An audit checked every coverage claim against the code, and
`07-prod-echo-coverage.md` now says what is really there. New:
- **Robot runs in the portal.** Upload a robot log (I-08, I-09). Turn its timings into a
  `robot_logs` condition (T-26). Compare a production run with the same bot's run in the lab
  (parity, I-24). Tried live: screens and fields matched, and three value differences were
  found.
- **`labctl realdata load`.** Masked rows go into a lab, with real names mapped onto the demo
  schema. A mask receipt is required (I-18). Tried live: 267 rows from SQL Server-style names
  were loaded into QTT, and conformance stayed READY.
- **The AI-change guard (T-22)** runs in CI as `guard.yml`.
- **Optional drivers** for SQL Server, Oracle and MySQL.
- **[realdata.md](realdata.md)**, a new guide.

**Still open (see the end of `07-prod-echo-coverage.md`):**
- a pass/fail verdict on unexpected changes
- named data packs and versioned baselines
- a recorder that attaches to a running browser
- fidelity replay in the portal
- the harness chain in one command

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
