# Plan

Three milestones. The first is a thin slice through every stage on a small showcase app, so the
platform is proven before QTT depends on it. Items are in order; an item is done when every line of
its Definition of Done (DoD) is demonstrated by a test or a command, not described.

Sizes: S (half a day), M (one to two days), L (three to five days).

## Milestone 1: one automation, one app, end to end

Goal: a person registers a UiPath automation, an AI agent builds the app it needs, the portal resets
it, slows it down and records a run. Uses a showcase app, not QTT.

- [ ] **T-00 Monorepo scaffold** · S
  Folder layout from `02-architecture.md`, `labctl` CLI with empty command groups, lint and test
  pipeline for `lab/`.
  DoD: `labctl --help` lists the groups; tests pass on a fresh clone; pipeline green.
  *Open: built and passing locally (2026-11-15). Not ticked because the folder is not yet a Git
  repository, so the fresh-clone and pipeline lines have not been shown.*

- [x] **T-01 Contracts** · S
  Pydantic models and `specs/` examples for `automation.yaml`, `app.yaml`, data pack, condition
  profile, session, bindings.
  DoD: every example validates; `labctl validate` reports file and field for a deliberately broken one.

- [x] **T-02 Showcase source app** · M
  Decide the showcase and write its *reference* version: a small two-screen case application
  (search, detail with a form) with a real frontend framework, an API and five or six tables. This
  stands in for "the real application's source" so the factory can be exercised without approvals.
  Kept outside `apps/`, under `reference/`.
  DoD: runs locally; has a cascading dropdown, a conditional field and one server-side validation,
  so later items have behaviour to reproduce.
  *Built as `reference/case-workbench/`: React 18 + FastAPI + SQLite, three screens, seven tables.*

- [x] **T-03 UiPath analyser** · M
  Port the importer from `ApplicationTwinStudio/ats/importers/uipath.py`; add selector, variable and
  SQL extraction as specified in Prod Echo I-07. Writes `touches` and `gaps` into `automation.yaml`.
  DoD: a UiPath project written against the T-02 app yields every screen, selector and route it
  uses; an unsupported activity appears in `gaps`; a file with a DTD is rejected.

- [x] **T-04 Demand report** · S
  `labctl demand <app>`: union of `touches` across registered automations, each line listing who
  needs it.
  DoD: with two automations registered, a selector used by both is listed once with both ids.

- [x] **T-05 Source analysis and build brief** · M
  `labctl brief <app> --source <path>`: extracts schema, routes and screens from the reference
  source and joins them with the demand report into `apps/<app>/BRIEF.md`. Start with one backend
  and one frontend stack; others are added when an app needs them.
  DoD: the brief for the showcase lists exactly the tables behind the demanded screens, with
  `db/schema.sql` generated; nothing outside the demand report is in scope.

- [x] **T-06 Factory playbook and first build** · L
  Adapt `ApplicationTwinStudio/docs/devin-playbook.md` and `AGENTS.md` to demo apps. Hand the brief
  to an AI coding agent; it opens a pull request adding `apps/showcase/`.
  DoD: the app builds and runs from its Dockerfile; record how many rounds of correction it took
  and what the agent got wrong.
  *Done in 2 rounds, see `apps/showcase/BUILD-LOG.md`. No pull request: the folder is not a Git
  repository yet, so the PR text sits at the end of `BUILD-NOTES.md`.*

- [ ] **T-07 Conformance check** · M
  `labctl conformance <automation>`: selectors in a headless browser, routes, tables and columns.
  DoD: READY for the showcase automation; renaming one element id in the demo app gives BLOCKED
  naming that selector and screen.

- [ ] **T-08 Per-app pipeline** · M
  Reusable workflow (build, test, conformance, image) and the path-filtered caller for the showcase.
  DoD: a change under `apps/showcase/` runs only that pipeline; a change to its `automation.yaml`
  re-runs conformance; a change to `docs/` runs neither.

- [ ] **T-09 Lab agent: proxy, info, health, request log** · M
  DoD: the showcase is reachable only through the agent; `/_lab/info` reports version and tables;
  every request appears in the log with its duration.

- [ ] **T-10 Data: template, upload, load, reset** · L
  Note from T-06: after loading rows with explicit ids, move identity sequences past them.
  Excel template from the schema; validation; all-or-nothing load; reset to baseline.
  DoD: a filled template loads and shows in the app; a workbook with a bad foreign key is refused
  naming sheet, row and column, and nothing is loaded; after an automation changes rows, reset
  returns every table to the baseline (row-level comparison).

- [ ] **T-11 Synthetic generator** · M
  Seeded generator producing the same workbook shape.
  DoD: the same seed gives byte-identical data twice; generated data passes T-10 validation.

- [ ] **T-12 Conditions: HTTP latency and faults** · M
  DoD: under `peak-hour`, the search route's measured latency falls in the profile's range and other
  routes are unaffected; a 5 % fault rate with a fixed seed fails the same calls on every run.

- [ ] **T-13 Conditions: database link** · M
  TCP proxy between app and database, set through the lab agent.
  DoD: with 150 ms on the link, a page that makes three queries is at least 450 ms slower, with no
  change to the app.

- [ ] **T-14 Sessions** · M
  Start, stop, request timeline, before/after table diff, summary.
  DoD: a session around the showcase automation lists its requests in order and exactly the rows
  it inserted; `app_ms` and `injected_ms` are reported separately.

- [ ] **T-15 Portal v1** · L
  Pages: Automations, Apps, Mapping, Data, Conditions, Sessions, Bindings.
  DoD: the whole Milestone 1 flow is done from the portal with no CLI, apart from triggering the
  automation itself.

  **Gate M1:** demo to the team. Same UiPath automation under `normal` and `peak-hour`, two sessions
  side by side.

## Milestone 2: more than one kind of automation, more than one app

- [ ] **T-16 Python and agentic analyser** · M
  Declared `touches` plus static scan for URLs, SQL and selectors.
  DoD: an undeclared table used in the script is reported; a backend-only automation
  (`surface: backend`, API and DB, no screens) reaches READY.

- [ ] **T-17 Agentic counterpart of the showcase automation** · M
  DoD: both automations READY against the same app; sessions for each under two conditions.

- [ ] **T-18 Second app in a different framework** · L
  Through the factory again, to prove the pipeline and lab agent are stack-independent and to
  measure onboarding effort.
  DoD: two apps in different frameworks deployed side by side, each controlled independently; hours
  for the second app recorded against the first.

- [ ] **T-19 Cross-app data packs** · M
  `links` validation across apps.
  DoD: a pack where a customer exists in one app and not the other is refused naming the key.

- [ ] **T-20 Session comparison** · M
  Two sessions side by side: time, waiting, retries, errors, row changes.
  DoD: the comparison names a row one automation changed and the other did not.

- [ ] **T-21 Shared hosting** · M · needs the hosting decision
  Deploy step in the reusable workflow; hostname per app.
  DoD: a merge to main updates only the changed app on the shared host.

- [ ] **T-22 Trust guard for AI-authored changes** · S
  Port `tools/guard_ai_changes.py`: no self-approval of rules; an app and the conformance
  expectations for it not changed in one pull request without a reviewer label.
  DoD: a pull request raising a rule to `owner_approved` without the label fails.

## Milestone 3: QTT

Needs answers to questions 1 to 6 in `05-open-questions.md`.

- [ ] **T-23 QTT automation inventory** · M
  Register every automation that touches QTT, analyse, review gaps with their owners.
  DoD: demand report for QTT reviewed by someone who knows the automations.

- [ ] **T-24 QTT source analysis and brief** · L
  Add stack support to T-05 if QTT's differs.
  DoD: brief reviewed by someone who knows QTT; schema generated; rule list written, all `hypothesis`.

- [ ] **T-25 QTT demo app build** · L (likely several rounds)
  DoD: all QTT automations READY, or each remaining gap listed with an owner.

- [ ] **T-26 Slowness calibration from real evidence** · M
  Port the robot log parser (Prod Echo I-08) to derive per-step wait times; build QTT profiles.
  DoD: a profile marked `robot_logs` whose route latencies are within an agreed tolerance of the
  measured ones; if no logs are obtainable, a profile marked `estimate` signed off by bot support.

- [ ] **T-27 QTT showcase** · M
  Original and agentic automation on the QTT demo, under normal and degraded conditions, with
  bring-your-own data.
  DoD: four sessions recorded and compared; limits of the demo stated on the same page.

## Deliberately not in this plan

- Running automations from the portal, scheduling them, or collecting their own logs.
- Scenario assertions, A/B certificates, pass/fail verdicts on an automation.
- Extracting or masking production data at scale (Prod Echo I-10 to I-18). Masking exists only as a
  gate on bring-your-own packs.
- Production capture tooling (Prod Echo I-03 to I-06).
- Mainframe, desktop and Citrix applications.
- Multi-user isolation beyond one deployed copy per app.

## Review checklist

- Can every Milestone 1 item be finished with no approval and no access to a real application?
- Does each DoD name something observable?
- Is anything in Milestone 1 not needed for Gate M1? (Candidates to move: T-11, T-13.)
