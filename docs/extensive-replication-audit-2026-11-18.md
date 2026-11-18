# Extensive replication audit - 18 November 2026

## Decision

Do not claim all gaps are closed or arbitrary applications are replicated completely. The audit
reproduced two correctness defects in verdict aggregation and two policy gaps against the stricter
100%-verified target. A passing existing test suite does not cover these newly demonstrated cases.

No line/branch coverage percentage is claimed; test counts are not a completeness metric.

This is a coverage-based assessment: automated repository tests, live public-app comparisons,
source-against-source replay, code review and adversarial gate probes. It is not an exhaustive
proof over all applications, states, browsers, permissions and integrations.

## Status after the fixes, 18 November 2026

`python tools/audit_replication_gates.py` now exits 0. The same cases are regression tests in
`lab/labctl/tests/test_audit_gates.py`.

| ID | State | What changed |
|---|---|---|
| A01 | Fixed | A case whose category cannot be read is counted under behaviour and named in the note. Every required result decides the verdict whatever category it carries: the probe returns BLOCKED. |
| A02 | Fixed | `visual.yaml` with one id twice: both states are kept, the second gets a problem and is BLOCKED. A result is never stored over another. The probe returns FAIL with both states counted. |
| Status and verdict disagree | Fixed | A report whose replay status is not pass cannot carry PASS WITHIN DECLARED SCOPE. |
| A03 | Decided and enforced | A second line beside a scoped PASS, the assurance tier. `VERIFIED COMPLETE` needs an expected action list declared outside the recording with nothing missing. Without one the report says `DECLARED CASES ONLY`. |
| A04 | Decided and enforced | A required case with `basis: assumed` holds the tier at `DECLARED CASES ONLY` and is named. |
| Removed links in the two public-site replicas | Fixed in the apps | The home link and the index link are back on the pages; the link leaving the scope is answered with a redirect to the first screen in scope. Strict replay of the 30-event recording across both replicas: 0 failed, no exclusions. |

`labctl behaviour fidelity ... --verified` exits 1 unless the tier is `VERIFIED COMPLETE`. The
scoped verdict itself is unchanged for A03 and A04: declared cases that passed did pass. What
changed is that the report can no longer be read as verified completeness.

Still not verified, and not claimed: production sign-in (SSO, MFA, token expiry), roles,
recovery of a long or interrupted recording, an independently rebuilt application with a real
API and database, other browsers. Canvas actions and closed shadow DOM stay UNSUPPORTED in the
capability table and are reported as such, never as passed.

## Fresh checks performed

- Full repository suite invoked with a saved JUnit report; final totals are appended below.
- Collected test inventory: 1,192 tests at audit time.
- Contract validation: 34 files, zero problems.
- Original multi-app recording against its original public sites: 30 business events replayed,
  four implied events, 26 compared snapshots, zero failed/skipped/unresolved events. One browser
  setup event is reported separately. Structure PASS; scoped verdict BLOCKED because no required
  acceptance cases were declared in this run.
- Both local replicas: 30 business events replayed with the explicit two-origin map. Strict
  full-page comparison reports 24 failing steps. The intentionally removed home/index links
  remain outside the reproduced scope; no exclusions were added to make this run pass.
- Independent source/replica business driver: 23 checks passed on each pair, including navigation
  between apps, form submission fields, radio exclusivity, checkbox/select state and Back behavior.
- Real-browser gate probes confirm A01-A04 below. The A02 control used two actual screenshot
  baselines: unique IDs produce FAIL then PASS; changing only the IDs to duplicate values
  incorrectly produces an overall PASS and retains only the latter result.
- Four retained aggregation/policy probes confirm A01-A04. The visual aggregation probe injects
  FAIL then PASS comparison results to isolate ID handling; it does not claim to test pixels.
- Ruff passed for the retained audit probe script.

Docker was initially unavailable (Docker Desktop Linux-engine pipe absent). The first local replay therefore
blocked on connection refusal. That diagnostic was preserved, then the same app source was served
with local Uvicorn processes on ports 8737 and 8738 and the browser checks rerun. A local process
pass is not a fresh container build, database-proxy trial or OpenShift result. Docker Desktop was
subsequently started in the background; Docker Engine 28.5.2 became available before the full
suite reached its lab-agent integration tests. Their final execution status is recorded below.

## Newly reproduced gaps

| ID | Priority / classification | Reproducer and observed result | Required fix and acceptance criterion |
|---|---|---|---|
| A01 | P0 correctness | A required text case has category `behavor` instead of `behaviour`. Parsing marks the case BLOCKED. Category aggregation ignores the unknown category; with one other passing case, the scoped verdict is PASS WITHIN DECLARED SCOPE. A real replay reports status=blocked and verdict=PASS simultaneously. | Every required result must affect the final verdict even if its schema/category is invalid. Validate declarations before execution; return BLOCKED for invalid required cases. Regression must assert both status and scoped verdict never report pass, and retain the invalid case in the denominator/report. |
| A02 | P0 correctness | Two required visual states use the same ID. A real browser comparison against an intentionally wrong solid-color baseline returns FAIL (921,600 differing pixels); a correct baseline returns PASS (zero differing pixels). Judging.visual stores results by ID, replacing the failure. The final scoped result is PASS with only one state counted. | Reject duplicate visual IDs at load time or assign unique internal IDs while retaining a required validation failure. Both declared states must remain accounted for; a passing later comparison cannot erase an earlier failure. |
| A03 | P1 assurance policy | A real replay with a passing required behavior case and no expected-action manifest returns PASS WITHIN DECLARED SCOPE; capture is NOT_DECLARED. This is currently exercised as accepted behavior by existing tests. | Distinguish "declared behavior cases passed" from independently verified capture completeness. For the 100%-verified tier require a nonempty independent action manifest and zero missing actions. Do not derive the expected list from the recording being judged. |
| A04 | P1 assurance policy | A real replay whose sole required case is basis=assumed returns PASS WITHIN DECLARED SCOPE. The assumption is listed, but does not constrain the verdict. | An assumption can support a provisional model, not independently verified fidelity. Block the stricter verified tier until required assumptions have evidence/review; preserve provenance and never auto-raise approval levels. |

A01 and A02 are implementation correctness defects. A03 and A04 require an explicit product policy
and verdict distinction; they are not presented as regressions against the current narrower test
contract. No production implementation was changed or test weakened in this audit.

## Coverage matrix and remaining work

| Area | Evidence exercised by the repository suite | Limit of the assurance |
|---|---|---|
| Semantic capture/replay | test_capture_replay, test_fidelity_human_recordings, test_submit_load_boundary | Local fixtures and the two public demos; not every interaction library or input method. |
| Multiple apps/tabs/history | test_replay_across_apps, test_split; live two-origin trial | Includes 108-step local fixture, repeated fresh replicas, wrong-value and missing-page mutations. A fixture replay against the same source implementation does not prove an independently generated enterprise replica. |
| Frames and shadow DOM | test_capability_fixtures, test_recorder_rich | Closed-shadow replay is explicitly unsupported; ordinary iframe fixtures do not establish every auth/sandbox combination. |
| Files, dialogs, asynchronous pages | test_capability_fixtures | Synthetic local fixtures; full malware/upload processing, very large files and enterprise content gateways are not validated. |
| Values and business outcomes | test_acceptance_cases, test_outcome, test_outcome_portal | Several broken-value mutations are caught. This audit adds malformed-category coverage the suite missed. Real business rules still require independent acceptance cases. |
| Browser data and reset | test_browser_baseline | Synthetic localStorage/sessionStorage/IndexedDB/cookie baselines; not production identity/session lifecycle. |
| Visual fidelity | test_visual_fidelity | Pixel fixtures and controlled viewport cases; duplicate IDs remain a gate defect. No full pixel-equality run was performed for the public-app replicas. |
| Readiness/freshness | test_fidelity_fingerprint, test_readiness_freshness | Unit/integration checks of invalidation; actual deployed build/image provenance remains environment-dependent. |
| Privacy and sanitization | test_privacy_identifiers, test_sanitize_rich, recorder fixtures | Synthetic privacy cases only. Raw local evidence is not equivalent to an approved production-data export. |
| Coverage/portal reports | test_coverage_matrix, test_portal_replication | Required declarations and aggregation need the A01-A04 fixes/decisions before stronger readiness claims. |
| Database and containers | Agent/unit/SQLite tests; Docker tests conditional | Docker was restored during this run; final integration results are listed below. Real API/database-backed independent reconstruction remains a separate acceptance trial. |
| Other browsers/native apps | Capability/desktop tests are conditional | Treat skipped engines and GUI tests as untested here. Browser fixtures do not establish native GUI or Citrix support. |

## Additional unverified scenarios

These are coverage gaps, not claims that a defect has already been reproduced:

- Production-like SSO/OIDC/SAML, MFA, token expiry, role changes and cross-domain session isolation.
- Independent reconstruction of an API/database application: transactions, rollback, idempotency,
  permission failures, concurrent writes and externally visible side effects.
- Large sessions and recovery: long recordings, interrupted uploads, recorder/browser crashes,
  process restarts, partial/corrupt evidence, resource pressure and many simultaneous labs.
- Broad browser/device/accessibility combinations, IME/composition, locale/time-zone formatting,
  zoom/DPI variation and font loading across operating systems.
- Offline/service-worker semantics, WebSocket streams, canvas-only actions and drag/drop beyond
  explicitly supported capabilities. Unsupported labels must remain visible.
- Independent source-owner validation and approved UAT evidence for each actual target app.

## Required next gates

1. Fix A01/A02 with regression tests that fail on this audited implementation. Keep required-case
   accounting independent from category names and user-supplied visual IDs.
2. Decide and enforce A03/A04 for a distinct verified-complete tier; keep narrower structural and
   behavioral results separate and explicit.
3. Address any failed/skipped environment checks shown in the final results; run missing
   browser/GUI environments when those capabilities are in the requested product scope.
4. Complete a real API/database reconstruction trial with independent expected outcomes, then
   failure injection, concurrency and recovery cases. Do not claim this from a stand-in outcome judge.
5. Re-run the full suite, these probes and the declared app-specific acceptance matrix. Publish
   passed/failed/skipped counts, environment, source/build identifiers and outstanding exclusions.

## Reproduction and audit artifacts

Run `python tools/audit_replication_gates.py`. Exit 1 intentionally means an open unsafe gate was
reproduced; it is not a passing test. The script does not alter judging logic or application state.

Local audit artifacts (Git-ignored):

- .lab-state/extensive-audit-2026-11-18.xml - full-suite JUnit output.
- .lab-state/audit-2026-11-18/collected-tests.txt - collected test inventory.
- .lab-state/audit-2026-11-18/gate-probes.json - four retained probe results.
- .lab-state/audit-2026-11-18/*-browser.json - real-browser A01-A04 results, including
  unique-visual-browser.json versus duplicate-visual-browser.json.
- .lab-state/audit-2026-11-18/code-manifest.json - Git HEAD and reviewed-module hashes.
  The working tree contained pre-existing changes; this is a working-tree audit, not a clean-release certification.
- .lab-state/audit-2026-11-18/source-self.json - source replay control.
- .lab-state/audit-2026-11-18/replicas-strict.json - initial unavailable-service diagnostic.
- .lab-state/audit-2026-11-18/replicas-strict-running.json - strict local-process replica replay.
- .lab-state/audit-2026-11-18/paired-checks.json and source/replica PNGs - business checks and views.

This report supersedes broad "implemented" labels as evidence of completion: code presence,
passing fixture tests, passing a particular app's acceptance cases and verified production fidelity
are different levels of assurance.
