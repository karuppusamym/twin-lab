# Changelog

One paragraph per plan item: what exists now, how to run it, what was left open.

## 2026-11-18 — Seed data and replay values in the build packages (the blind rebuild's data gap)
- **One planner for the seed and the replay values** (`labctl/seedplan.py`, run by `labctl brief`
  and `labctl evidence build-all`). From an app's recordings it writes `apps/<app>/db/seed.sql`
  (the schema's dialect, every name quoted; the same rows in `db/seed.json`): rows numbered from
  the lowest to the highest identifier the lists showed, columns in the observed shapes and code
  lists, no rows for a table whose first record was written during the recording, and the rows a
  recorded search found carrying the synthetic word the replay will search for. Beside it
  `apps/<app>/steps/<recording>.values.yaml`: per typing or choosing step the synthetic value
  the acceptance replay types and the recorded outcome; the searches (rule, mode, rows, pages,
  the seeded rows that match); a refused and an accepted input per validation message with a
  hypothesis only where attributes, an empty refusal or the message's example support it; the
  display format of each shown field (two decimals, date pattern, closed word lists, "as typed",
  option code vs label); computed values marked "rule unknown"; values shared with sibling apps.
  A search counted 12 rows over 3 pages finds 12 over 3 pages on the seed; a "no results"
  search finds none; a new record's number is the highest seeded + 1 in every package that
  uses it. Words are checked against every word of the evidence; no typed value is ever read.
- `build-all` writes the values into each part's `bindings.yaml` and the whole recording's
  (marked `generated:`; a value a person writes is kept, and generated values do not trigger a
  re-split), and a draft `acceptance.yaml` where none exists: required only for what the package
  states, computed values excluded with the reason.
- The brief gains *Seed data and replay values*, *Paths the pages link to that the route list
  does not name* (e.g. `GET /orders/new/customer`, `GET /logout` as recorded), *Links that leave
  the scope* (keep the `href`, answer with a 303 to the first in-scope screen; BUILD_TASK and
  AGENTS.md now say the same), *Checkbox fields and their columns* (posted `vat=1`, BOOLEAN
  column), the `<tl-frame>` capture explained (brief and snapshot header), unambiguous identifier
  wording ("lowest seen, highest seen; 12 different values, not every number in between"),
  repeated requests shown as `×2`, option codes as the page posts them (model code lists use
  them too), and per step the ranked locator the replay finds a non-id selector by (`target`).
- Run: `labctl evidence build-all <recording> --proposal <file>`; tests:
  `tests/test_seedplan.py`, `tests/test_multi_app_flow.py` (seed reproduces the recorded counts,
  values shared across packages, formats and validations, a replay of the source code holding
  only the seed with the generated values passes). Trial: `rebuild-trial/_work/build.py`, proofs
  `proof.py good|seeded|unseeded|...`, commands for the next blind run in `rebuild-trial/JUDGE-RUN.md`.
- Left open: the matching rule of a search is a hypothesis from one recording (contains vs whole
  word, case); row order in lists is not stated; computed values (prices, VAT) stay unknown and
  unjudged; a table listed only without identifiers is seeded with as many rows as were listed
  before the first change; the acceptance *template* (`labctl behaviour acceptance`) still writes
  TODO cases a person completes; no CLI of its own (`labctl brief` and `build-all` run it).

## 2026-11-18 — Desktop applications, phase 1: record, model, brief, package, verify
- **`labctl desktop`** (`lab/labctl/labctl/desktop/`, [09-desktop.md](09-desktop.md)) records a
  person using a Windows application: UI Automation events, low-level mouse and keyboard hooks
  and window polling for WinForms, WPF, UWP/WinUI, Win32/MFC and JavaFX; the **Java Access
  Bridge** (ctypes, loaded per run, no `jabswitch`) for Swing/AWT. Each step has its window, a
  canonical locator (`uia:AutomationId=txtCase;ControlType=Edit`, `jab:Name=Save;Role=push
  button`) with ancestor path, fallbacks and UiPath/pywinauto/WinAppDriver forms, and the
  value-free control tree after it. Typed values and shown data keep only their shape.
- Toolkit detection (FrameworkId, window classes, module names, .NET runtime folders, version
  resource); Electron is sent to the web recorder; Delphi/VCL, Qt, VB6, PowerBuilder and
  Citrix/RDP are detected and reported not supported.
- Desktop recordings feed the app model (windows as screens, controls with UIA properties,
  menus, grids as candidate tables, drop-down code lists, message boxes as rules), the brief
  ("Desktop windows and controls"; SQLite by default) and a desktop build task (same toolkit and
  UIA properties, `build.ps1`, `dotnet publish` folder and zip, no Dockerfile). `labctl desktop
  split` gives one evidence set per executable; `verify` replays a recording against a built
  app and writes a fidelity report the readiness gate reads (new checks `changed`, `ambiguous`).
- Reference apps `reference/desktop/CaseDesk` (.NET 6 WinForms) and `CaseDeskSwing` (Java 11
  Swing), each recorded by a scripted flow and verified against its own recording; a wrong
  CaseDesk build is caught (missing, extra, ambiguous). The demo app built from the package
  (`apps/casedesk/`) comes on its own `build/` branch. Live tests run only with
  `TWINLAB_DESKTOP_GUI=1`.
- The R12 capability table now says what covers desktop: web DOM replay does not; `labctl
  desktop` does for WinForms, WPF, UWP, Win32 (UI Automation) and Swing (Access Bridge).
  Citrix/RDP, Delphi, Qt, VB6 and PowerBuilder are out of scope by decision.
- Proven live: WinForms (CaseDesk) and Swing (CaseDeskSwing). Left open: WPF, UWP/WinUI,
  Win32/MFC and JavaFX, and the Electron hand-off, are tested on fixtures only, not on a live
  app; WinForms raises no Invoke events (clicks come from the mouse hook only); snapshots are
  taken after a batch of quick actions; Java typing and selection are polled.

## 2026-11-18 — What a builder receives: fixes from the rebuild trial
- **Interface text is kept, data is masked.** Page snapshots in a brief now keep the application's
  own labels, headings, buttons, options and messages verbatim, and mask table cells, input
  values, identifiers, dates, amounts, e-mails and names. `TWINLAB_PAGE_TEXT=strict` masks all
  text and styles as before. Typed values are never kept in either mode. `data-testid`,
  `data-field` and `target` are no longer masked.
- **The brief says more**: a fields table per screen, messages per screen, list columns, what each
  recorded action did, identifier patterns and how the next one is formed, the sign-in rule,
  which element's value travels to which field of the sibling application, and the address
  variable of each sibling (`<APP>_URL`).
- **An embedded frame from another origin** is in scope as a stand-in served by the app itself;
  its requests are not the app's routes or tables. Pages are no longer listed as tables.
- **Database**: identifiers are quoted per engine with a reserved-word list. With no database
  chosen at onboarding and none evidenced, the brief and task use SQLite and say it is a
  default, not evidence.
- **The package** now holds the app's step list (no values), its sequence and draft flow; the task
  mentions screenshots only when present, and has a section for working from the zip alone.
- A format rule is written only when a real format was seen.
- Left open: recorded pictures are not shipped (they hide typed values only, not shown data);
  seed counts, prices and valid credentials cannot be learned from a recording.

## 2026-11-18 — One replay across applications and tabs; trial findings; R09, R10, R12
- **The recorder sends each action the moment it happens** and numbers it on arrival. Before, a
  step ended by a navigation was sent while the page unloaded, and newer Chromium drops that
  call: clicks, checks and Enter steps were lost (this failed CI on Linux). The blank first page
  and a `window.open` popup are also handled by the recorder itself.
- **One replay across applications and tabs.** `labctl behaviour fidelity <recording> --map
  source=replica` (repeat per application), or a `replicas.yaml` beside the recording; a single
  `--base-url` still works for one application. Tabs are opened, matched, switched and closed as
  recorded. A request to a source or unmapped origin is refused and reported; a step on an
  unmapped origin is BLOCKED and names the `--map` to add.
- **Splitting keeps references.** Steps are renumbered per application with a map, a reference
  to a step in another application is kept as `{app, step}`, and its value travels in the part's
  `bindings.yaml` under `outside:`. Acceptance and picture files are split too.
- **Observation boundaries.** A click or submit that loads a page is compared at that page, also
  when the page is slow or redirects; Back and Forward are replayed as such.
- **Setup events** (blank and browser pages) are counted the same way in the proposal, the parts,
  the sequence file and replay coverage.
- **Reviewed scope** (`scope.yaml`): elements, screens or origins left out, each with a reason and
  who approved. Excluded differences get their own report section and never count as passed.
  `labctl behaviour scope <recording> --from-brief` drafts one, unapproved.
- **R12 capability table** (`labctl/behaviour/capabilities.py`), each row proved by a fixture
  test. Supported: same- and cross-origin iframe, open shadow DOM, upload, download, popup,
  dialogs, late content, select by index, slow POST-redirect with history, multi-app replay.
  Partial: service worker (blocked during replay and reported), second engine. Unsupported with
  a message: closed shadow DOM, drag and drop, canvas, desktop.
- **R09 coverage matrix**: `labctl coverage <app>` and a portal page give each requirement one of
  VERIFIED, EVIDENCED, ASSUMED, UNOBSERVED, UNSUPPORTED, EXCLUDED or BLOCKED. **R10**: portal
  pages for a recording, a replay report, coverage and the declaration files.
- Left open: a popup that misses its start script is hooked only before the next action; element
  exclusions match tag, id and class, not ancestry; recording waits up to 15 s on a page that
  polls the network; the portal's replay button uses `replicas.yaml` or one base URL, it has no
  form for the map.

## 2026-11-18 — Several applications in one recording, end to end; gaps R05, R06, R08, R11; OpenShift by hand
- **The recorder follows every tab and popup** on one numbered sequence; each step names its
  tab, opener and origin, and opening, switching and closing a tab are steps. One HAR covers all
  tabs. Each step can carry browser storage keys with masked shapes, cookie names and IndexedDB
  names (never values). The recording notes the browser, viewport and the masked areas of each
  picture. Typed values are also removed from step addresses and storage keys.
- **From a recording to a package per application.** `labctl evidence record-across <name>
  <url>`, then `labctl evidence build-all <recording>`: it proposes the applications and stops
  for review; with `--proposal <file>` or `--yes` it writes each application's evidence, a draft
  flow to review, its brief (an existing app is enhanced, not replaced) and its build package,
  and a summary of what a person still has to do. Each brief and build task says which
  application comes before and after it in the flow. The portal has a Recordings page to review
  the split and create the applications; an app page links its recording and sibling apps.
- **R05 acceptance cases** beside a recording (`acceptance.yaml`): text, count, order, value,
  flags, address, browser storage and database outcome, each PASS, FAIL, BLOCKED or SKIPPED.
  **R06 browser-data baselines** (`baseline.yaml`): replay starts from a declared state in a fresh
  context and never sends a cookie of the real site to the demo app. **R08 pictures**
  (`visual.yaml`): pinned viewport, declared exclusions, exact mode. **R11**: a replay result
  names what it compared and goes out of date when the app, bindings, cases or baseline change.
  The verdict is PASS WITHIN DECLARED SCOPE, FAIL or BLOCKED; no executed case is BLOCKED.
  `labctl behaviour acceptance|baseline|visual <recording>` write templates.
- **OpenShift without a pipeline.** `labctl ocp render <app> --manual --out deploy/ocp/<app>`
  writes numbered manifests and a README of the manual steps; images are built in the cluster
  from the branch. `--all` renders every app; `labctl ocp check <dir>` validates a folder
  offline. `deploy/ocp/` holds showcase, qtt and loandesk. SQLite and browser-only labs run as
  one pod. Nothing here has been applied to a real cluster.
- Left open: replay of a recording across several tabs or applications in one run; attach mode
  cannot see what a tab did before attaching; downloads, uploads and service workers; R09, R12.

## 2026-11-17 — Replication gaps R01–R04 and R07 (`docs/replication-gaps.md`)
- **Recorder:** key presses that are not text (Enter, Escape, Tab, chords) and double-clicks are
  steps of their own; checkboxes are `check`/`uncheck`; typing is flushed before a submit, key
  press or navigation. Each step keeps ranked locators (test id, id, role and name, label, link
  text, structure) with their match count. Typed values are held in memory only and are removed
  from snapshots, the HAR and screenshots.
- **Fidelity replay:** a target must match exactly one element, or the step is BLOCKED with
  what each candidate matched; per-record controls bind to the record, not its position;
  values come from `bindings.yaml`, one per step (`labctl behaviour bindings <recording>` writes
  the template); a missing value blocks. The report counts expected, replayed, failed, skipped
  and unresolved actions and is `pass` only when every step ran. Readiness treats BLOCKED as
  blocking. Older recordings still load and replay.
- **Where the data lives.** A recording says `browser`, `server` or `unknown`. A browser-only
  app's brief has a "Browser storage" section and no invented tables or database; its build
  task says so; the lab marks data controls and database conditions "not applicable".
- Left open: R05, R06, R08–R12; elements inside shadow roots get no ranked locators; the
  recorder does not yet capture storage contents.

## 2026-11-17 — Product review: business outcome, readiness freshness, guided pages
From `docs/product-review-2026-11-17.md`.
- **Setup per application** with a test-data step; progress reads the lab's baseline and
  sessions; an update with no recorded columns fails a column-restricted rule.
- **Business outcome per automation.** `outcome:` in `automation.yaml` lists acceptance cases:
  `changes` (the run must make these changes, optionally `set:` final values), `read_only`,
  `rejected` and `failed_run` (the run ends unsuccessfully and leaves data unchanged). It is
  judged apart from `expects:`, which still says only what a run may change. `--case` picks one
  on `harness run`, `e2e` and `session verdict`; NOT_MET fails the run; values are checked only
  when the lab recorded them, otherwise the case is CANNOT_CHECK. `uipath-case-update` declares one.
- **Readiness says whether it is still current.** A check records what it was checked against
  (app files, brief, schema, database choice, data baseline, automation). Pages show "still
  current", "out of date" with the reason, or "cannot be confirmed" when the lab is stopped; only
  a current READY counts. A rebuild done outside the portal is caught through changed files.
- **Guided path first.** App-page controls for later (reservations, copies, fidelity, database,
  Excel data, packs, baselines, parity) sit under "More"; a "Next step" line shows until the
  first rehearsal is done. The dashboard reads labs concurrently with a 5-second cache.
- Left open: proving fidelity with real UAT evidence needs recordings from the office.

## 2026-11-17 — First build by a Claude agent; replay of a person's recording
- A Claude (Sonnet) agent built `loandesk`, a loan search on SQLite, from the build package
  alone. What the trial showed is fixed here: the task names the database chosen for the demo
  app (`lab.database`), not the real system's; it says the schema file is in the real system's
  dialect and must be translated; it says what to do with an in-scope element that leads out
  of scope; a failed Claude session shows its last message in the build status.
- **Fidelity replay works on a recording a person made.** A masked record id
  (`#loan-L-999999`) is matched to the first element of that shape; `--value 6:#field=text`
  gives the value typed at one step; a field whose value was supplied is not compared by
  format; a per-record element counts as the same when both pages have at least one.
- Left open: the standalone `claude` command needs an account with credit; a step that lands
  on an out-of-scope screen still shows as different.

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
