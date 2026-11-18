# Replication gaps and fix plan

Updated: 17 November 2026. Status: implementation backlog, not a declaration that the fixes below are complete.

## Target: fully verified replication within an agreed scope

A browser recording cannot prove every behavior of an arbitrary application. Hidden business
rules, roles, integrations and unvisited error paths require additional evidence. Use the claim
**100% of the agreed replication acceptance cases passed** only when the scope and denominator
are published, every required case ran, and none is skipped, unknown, assumed or failing.
Do not convert that result into a claim of complete equivalence to the original application.

Agree on screens, actions, roles, APIs, data states, business outcomes, errors, storage behavior,
browsers and viewports before building. Label excluded or unobservable behavior explicitly.
Retain separate results for capture completeness, replay, behavior, appearance and runtime
isolation. A weighted average must never hide a failed required category.

## Evidence from the public TodoMVC trial

- Source: https://demo.playwright.dev/todomvc/ ; replica: apps/web-todo/.
- Twin Lab recorded 17 events plus HAR, DOM and screenshots in evidence/web-todo/public-demo/.
- Twin Lab generated a reconstruction brief and detected React 16.7.0. An agent implemented the app.
- An explicitly written shared driver passed 13 paired business-state checks and Twin Lab task-area
  DOM comparisons. See apps/web-todo/verify.py and validation/report.json.
- This was assisted reconstruction. The original event log alone cannot replay the entire flow.
- The task area matched the checked structure; the banner and footer intentionally differ.
  Full-page pixel equivalence, backend equivalence and enterprise authentication were not tested.
- Tasks use browser localStorage. No task API or server database was observed.

## Prioritized gaps and acceptance criteria

| ID / priority | Evidence and gap | Required fix / likely code area | Acceptance criterion |
|---|---|---|---|
| R01 / P0 | Enter submissions are missing; double-click editing appears as separate clicks. | Extend recorder.py, recording models and fidelity.py with semantic keypress and double-click actions; include modifiers, order and frame context. Preserve compatibility with older recordings. | Capture and replay add-by-Enter, edit-by-double-click, Enter-save and Escape-cancel without a handwritten action driver. Exactly one intended state transition occurs for each action. |
| R02 / P0 | Generic targets such as `a` cannot distinguish filters. Fidelity replay currently selects `.first`. | Build stable locators from unique IDs/test IDs, role/name and scoped structure; retain fallback candidates and match counts. Reject ambiguous targets instead of silently choosing the first. | All, Active and Completed target the correct links; repeated-row controls target the intended synthetic record. Reordering rows does not redirect an action. Ambiguity produces an actionable BLOCKED result. |
| R03 / P0 | New-task typing followed by Enter is not represented completely in this trial's event log. Field values are deliberately omitted for privacy. | Flush pending input before submit/keypress/navigation. Capture checkbox actions as check/uncheck, not fill. Add per-event synthetic input bindings instead of one reusable value per selector. | Two different titles entered through the same input replay correctly. No typed secret enters HAR, DOM, screenshots or exported bindings. Missing required inputs block replay rather than silently skipping actions. |
| R04 / P0 | Thirteen driver checkpoints passed, but this does not prove the raw recording replays. | Add an independent capture-completeness test, replay coverage counters and a fail-closed result in recorder/replay reporting. | A known scripted source flow captures all expected semantic actions; its emitted log replays on a fresh replica. Required steps have zero skips, unresolved targets or missing values. Removing one required event causes the validation gate to fail. |
| R05 / P0 | Structural DOM similarity does not establish correct values or business success. | Extend fidelity acceptance cases to cover text, counts, ordering, visible messages and browser state; integrate existing outcome checks where server-side evidence exists. | Incorrect count, title, completion flag, saved value, persistence or error message fails its case even when selectors and DOM structure match. Expected outcomes are specified independently of replica output. |
| R06 / P0 | Setup state is not represented as a reusable browser-data baseline. | Model localStorage, sessionStorage, cookies and IndexedDB explicitly with sanitized fixtures, origin rebasing and deterministic reset. Keep secrets separate. | Empty and populated baselines replay predictably; refresh persistence and fresh-context isolation match the source. No source-domain cookie or credential is silently sent to a replica. |
| R07 / P1 | The brief describes SQLite as an external proxied server, although the observed app has no server-side task storage. | Update brief.py, app models and lab capability display to distinguish browser storage, SQLite files and server databases. Separate hosting health from business-data health. | A browser-only brief does not invent task tables or claim database fault/data-pack coverage. A database-backed sample identifies its real supported data controls. Unknown backend behavior stays unknown. |
| R08 / P1 | Only task-area DOM structure was compared; full visual fidelity is unmeasured. | Add screenshot comparisons with pinned browser, viewport, fonts, assets, zoom and animation/clock controls. Review and version any excluded regions. | Required states have visual baselines at agreed desktop/mobile sizes. Thresholds and exclusions are set before comparison; unexpected layout changes fail. Exact-pixel claims require zero differing pixels in the declared comparison region. |
| R09 / P1 | Recorded paths do not establish hidden rules, permissions, error handling or API/database fidelity. | Add a coverage matrix and evidence requests for roles, boundaries, negative paths, API responses and persistence. Use approved schema/API evidence where available. | Every required case maps to source evidence and an independent assertion. Unobserved cases are BLOCKED or explicitly excluded, never inferred as verified. A second trial includes a real API and database effects. |
| R10 / P1 | This trial required manually prepared scope and an agent-written implementation/test flow. | Wire capture, scope review, brief generation, builder output and replay into a reproducible platform workflow. Keep implementation and judging inputs separate. | A new supported app can traverse the documented workflow without hidden staging steps, manual repair of event logs or changes to judging tests by the builder. Artifacts and failures are visible in the portal. |
| R11 / P1 | A passing result can become obsolete after the app, fixture or evidence changes. | Integrate existing conformance fingerprints with fidelity reports, browser version, source recording hash, build/image digest and fixture hash. | Any relevant change invalidates the prior result until rechecked. Report exactly which source capture and replica build were compared. |
| R12 / P2 | This small app does not validate broader interaction surfaces or runtime integrations. | Add targeted fixtures for iframe/shadow DOM, uploads/downloads, popups, async responses and supported browser variations; classify unsupported surfaces. | Each advertised capability has a passing independent fixture; unsupported features produce explicit gaps. Desktop/Citrix/canvas-only behavior is never advertised as covered by ordinary DOM replay. |

P0 means required before claiming an automatically replayable replica for the TodoMVC flow.
P1 expands trustworthy app replication and the repeatable product workflow. P2 expands supported
surfaces after those foundations pass. These priorities describe replication scope, not production
release approval.

## Implementation order

1. **Close capture and replay gaps together (R01-R04).** Add an independent source fixture with
   deterministic actions and outcomes first. Extend the event contract, recorder and replayer in
   the same platform change. Verify old recordings still load. Do not modify the demo to hide a
   recording failure.
2. **Make state and outcomes reproducible (R05-R07).** Bind synthetic values per action, reset the
   correct storage, compare expected business state, and generate accurate capability statements.
3. **Make fidelity measurable and durable (R08-R11).** Add visual baselines, evidence coverage,
   report fingerprints and the portal workflow. A failed category remains visible and blocks the
   scoped completion claim.
4. **Repeat on a richer public/UAT sample (R09, R12).** Include API success/error bodies, role-based
   behavior and database writes. Do not extrapolate TodoMVC results to enterprise applications.

## First completion gate: TodoMVC without manual replay repair

Start from a new browser context and record these operations on the source: reject blank input;
add two distinct tasks using Enter; complete one; switch all three filters; start editing by
double-click; cancel an edit with Escape; save a different edit with Enter; reload; toggle all;
clear completed; add and delete a task; delete a task by saving an empty title.

Replay the recorded events against a fresh replica using only explicit synthetic bindings and
its declared baseline. No supplemental handwritten actions are allowed in the acceptance run.
Compare every expected state transition, resulting titles/counts/flags, storage state and agreed
screenshots. Require zero missing events, ambiguous targets, unsupported actions, failed cases
or unapproved visual differences. Run at least three fresh-context repetitions to expose races;
this is a minimum stability check, not statistical proof of universal equivalence.

Keep evidence and implementation changes separately reviewable as required by AGENTS.md. Rules
remain hypothesis until the existing independent approval process verifies them. Passing an
engineering replay test does not set owner_approved or reference_verified automatically.

## Report each run with these fields

- Source URL/environment, capture time/hash, requested scope and excluded capabilities.
- Replica commit/image digest, stack versions, browser/viewport and data-baseline hash.
- Expected versus captured actions; replayed, failed, skipped and unresolved action counts.
- Required acceptance cases passed / total, separately for behavior, APIs/data and appearance.
- Evidence-backed, assumed, unobserved and unsupported requirements, listed individually.
- First divergence with before/after screenshots, locator, state and related request evidence.
- Status: PASS WITHIN DECLARED SCOPE, FAIL, or BLOCKED. Never infer PASS from zero executed cases.

## Existing work to integrate, not duplicate

The working tree already contains outcome.py, outcome models/tests and conformance fingerprint/
freshness work. Their presence was inspected during this documentation update, but their complete
behavior was not revalidated here. R05 and R11 require integration and acceptance evidence for the
replication path, not a claim that those features do not exist. The earlier product-review document
is a dated snapshot and should be read with this update.

## Status update, 17 November 2026 (later)

- R01–R04: implemented. Evidence: `lab/labctl/tests/test_capture_replay.py` (nine acceptance
  tests on an independent TodoMVC-style fixture: capture completeness, replay to the same
  state with bindings only, fail-closed on a removed event, three repetitions).
- R07: implemented, except that the recorder does not yet capture storage keys and shapes
  (R06). Evidence: `lab/labctl/tests/test_browser_only_apps.py`.
- R05, R06, R08–R12: open.

Status on 18 November 2026:

- R05, R06, R08, R11: implemented. Evidence: `test_acceptance_cases.py`,
  `test_browser_baseline.py`, `test_visual_fidelity.py`, `test_fidelity_fingerprint.py`. Mobile
  pictures need approved images; database outcome cases were tested with a stand-in.
- R06 capture: the recorder now keeps storage keys with masked shapes.
- R10: partly. `labctl evidence build-all` and the portal's Recordings page; acceptance,
  baseline and picture files are not yet editable in the portal.
- R09, R12: open. The findings of the multi-application trial are being checked against the
  multi-tab recorder and are not marked fixed here.

The section below is the status when this document was first written.

## Current status

No replication gap above is marked fixed by this document update. The demonstrated result remains
13 passing explicit paired checkpoints for a browser-only sample, with the capture/replay gaps
listed above. Next implementation task: R01-R04, with an independent complete-flow acceptance test.

## Multi-application follow-up

See [the two-application, five-page trial](multi-app-recording-trial.md): 23 paired business
checks passed, but strict replay did not. New concrete fixes are split reference renumbering,
submit/navigation snapshot boundaries and per-origin replica routing. Source-against-source
replay reproduced the two submission failures. These findings are not marked fixed.
