# Multi-application recording trial - 17 November 2026

## Result

**Partial pass: replicas work for the tested flow; automatic cross-app fidelity replay is not ready.**

One continuous 65.81-second Chromium recording visited two public applications across five
pages, in the order A -> B -> A -> B. Only synthetic form text was used. This tested multiple
applications in one tab/browser context, not multiple tabs or shared authentication.

| Application | Recorded pages | Local replica |
|---|---|---|
| The Internet | /checkboxes, /dropdown, /add_remove_elements/ | http://127.0.0.1:8737/checkboxes |
| Selenium form | /selenium/web/web-form.html, /selenium/web/submitted-form.html | http://127.0.0.1:8738/selenium/web/web-form.html |

## What actually ran

1. Twin Lab recorder captured 31 events: 30 HTTP-page events plus an initial about:blank event.
   The trial included check/uncheck, select, repeated add/delete, text and textarea input, radio
   selection, two form submissions, Back navigation and returns between the applications.
2. Twin Lab split.propose identified both origins and five pages. Its IDs were renamed to
   web-controls and web-form. split.apply produced two evidence folders and a cross-app sequence.
3. The audit verified all 31 events were partitioned exactly once, no referenced DOM/screenshot
   files were missing, and all 31 events remained in the cross-app sequence. Selenium's CDN assets
   remained with its application. Proposal counts exclude about:blank while the first split keeps it.
4. Twin Lab factory.generate generated both briefs/models/schemas/page captures from the split
   evidence plus explicit per-app scope. Generation ran in staging to preserve shared judging files.
5. Both apps were built and run in separate Docker containers. They reuse recorded delivered
   frontend code and assets, with a small local hosting wrapper. No external AI builder was invoked.
6. The same independent driver ran 23 asserted business checks against the original pair and
   replica pair. Both passed all 23. The replica pair made zero external requests and had zero
   JavaScript page errors. Health, unrecorded-route 404 and mobile overflow checks passed.
7. Strict Twin Lab replay was also run without editing evidence or relaxing comparison rules.

## Replay results - keep separate from the business checks

| Run | Result | Events replayed | Compared snapshots | Failed steps | Skipped / unresolved |
|---|---|---|---|---|---|
| Split web-controls against replica | FAIL | 15/15 | 15 | 14 | 0 / 0 |
| Split web-form against replica | FAIL | 16/16 | 14 | 12 | 0 / 0 |
| Original combined recording against original sites | FAIL | 31/31 | 29 | 2 | 0 / 0 |
| Combined recording with first replica as sole base URL | BLOCKED | 12/31 | 12 | 11 | 18 / 1 |

The first app's 14 failures are the removed out-of-scope home link. The form app has removed
index-link differences plus two submission snapshot differences. Critically, the original sites
also fail the two submission comparisons: events 20 and 30 retain form snapshots while replay
has already reached the submitted-result page. This is a recorder/replayer observation-boundary
problem, not evidence that the replica failed to submit.

No expected-action manifest was supplied to fidelity.replay; those reports show expected=None.
Consequently they do not establish independent semantic capture completeness. The split audit
establishes preservation of captured events, not proof that every possible user action was captured.

## Required fixes exposed by this trial

| Priority | Finding | Fix and acceptance test |
|---|---|---|
| P0 | split._write_part renumbers seq but leaves caused_by at the global number. In web-form local events 9 and 15 point to 19 and 29; they should point to 8 and 14. | Remap all event references, including locator text_from and binding keys, using an explicit global-to-local map. Test split submission and typed-record references. Keep global_seq for provenance. This defect is independently confirmed; it is not asserted to cause every current replay failure. |
| P0 | Submit snapshots describe the pre-navigation form, while replay compares the post-navigation result. Source-against-source replay fails events 20 and 30. | Define consistent before/after boundaries for click -> submit -> navigation, and coalesce or compare implied events accordingly. The untouched combined recording must replay against the original sites with zero false state comparisons. |
| P0 | fidelity.replay accepts one base_url and rebases both source origins to that address. The form is requested from the first replica and blocks at event 13. | Support an explicit source-origin-to-replica-origin map and execute the original sequence in one context. Require both replicas to receive their own events, correct Back behavior, and no source-network fallback. |
| P1 | Factory/browser-storage wording claims persistent browser business data without observing storage. | Distinguish transient DOM state, navigation/form state, observed storage and unknown persistence. Absence of writes is insufficient evidence for localStorage. |
| P1 | Full-page fidelity and scope removal conflict: required removal of out-of-scope index links produces failures. | Define reviewed scope/exclusions before the run, or capture and include the index pages. Keep strict results visible; do not silently ignore missing elements after seeing failures. |
| P1 | The initial about:blank event is omitted from proposal counts but retained in the first split/sequence. | Classify setup events explicitly and report consistent counts, while preserving their provenance and excluding them from claimed business coverage. |

## Re-run with the current code, 18 November 2026

The same combined recording (31 events, unchanged) was split and replayed again after the fixes.

| Finding | State now | How it was checked |
|---|---|---|
| P0 split leaves `caused_by` at the global number | Fixed | Re-split: web-form steps 9 and 15 point to 8 and 14; `global_seq` is kept |
| P0 submit compared with the form snapshot | Fixed in this change | Source against source: 30 of 30 replayed, 0 failed (was 2). The recorder of that day named the click behind a submit but not the page load the submit caused; a load within half a second of a submit is now taken as its result |
| P0 one base address for two origins | Fixed | One run with a source-to-replica map: 30 of 30 replayed on both replicas, 0 blocked (was 12 of 31) |
| P1 browser-storage wording | Fixed in the brief generator | The committed `apps/web-form/BRIEF.md` and `apps/web-controls/BRIEF.md` were generated before the fix and still carry the old wording |
| P1 removed out-of-scope links fail the comparison | Fixed | With a `scope.yaml` naming the two removed links: 0 failed, 24 differences reported as excluded and not counted as passed |
| P1 `about:blank` counted differently | Fixed | Proposal and parts both report 14 and 16 steps and 1 setup event |

The replica run's status is BLOCKED, not PASS: no acceptance case or expected action list was
declared for this recording, and structure alone is never a scoped PASS. The `scope.yaml` of the
re-run was written for the check and is not a person's review.

## Artifacts and reproduction

- Combined evidence: evidence/multi-app-trial/combined/ (HAR, DOM, screenshots, trial checkpoints).
- Reviewed split: evidence/multi-app-trial/split-proposal.yaml.
- Per-app evidence: evidence/web-controls/combined/ and evidence/web-form/combined/.
- Sequence: evidence/_sequences/combined.flow.yaml.
- Build inputs/output: apps/web-controls/ and apps/web-form/; read their BUILD-NOTES.md.
- Audit and paired results: apps/web-controls/validation/split-audit.json and paired-business-checks.json.
- Strict reports: each app's validation/raw-replay.json and .md.
- Isolation controls: apps/web-controls/validation/source-self-replay.* and combined-single-base-replay.*.
- Paired screenshots: apps/web-controls/validation/source-*.png and replica-*.png.

From the repository root:

```powershell
$env:PYTHONPATH = 'lab/labctl'
python apps/web-controls/verify_multi.py
python apps/web-controls/replay_parts.py
python apps/web-controls/replay_combined.py
```

Evidence is local and Git-ignored; do not publish raw recordings. The reproducer uses only public
demo sites and invented inputs. No shared recorder, splitter or judging logic was changed in this
trial. The existing splitter/recorder tests passed (36 tests), but they do not cover all failures
found by this live experiment. Both generated contracts validate; neither application has raised
rule approval levels.

## Limits

This is not a multi-tab, SSO, role, upload or server-database trial. Backends were not reconstructed
from hidden business rules. Visual screenshots were inspected; no pixel-equality gate ran.
The recorded page controls and actual submitted query fields are the proven scope. Broader
application fidelity remains unverified until the listed gates pass.
