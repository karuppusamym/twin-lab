# Twin Lab product review — 17 November 2026

## Product direction

Keep Twin Lab focused on one job: give automation teams a controlled application copy in
which they can rehearse a bot run, inspect its effects, and reproduce failures. The existing
separation between the factory, lab control, and externally executed bots supports that job.
The sample apps demonstrate the pipeline; they do not establish fidelity to a real QTT system.

Reviewed the direction and architecture documentation, repository contracts, portal setup and
application flows, session reporting, verdict implementation, and their tests. This is a focused
product and correctness review, not a certification of every subsystem or a real-cluster trial.

## Changes implemented

- Setup now has an application selector. Evidence, build, running state, readiness, data and
  completed runs belong to that application rather than being combined across unrelated apps.
- Added the missing test-data preparation step. A running container does not prove data is loaded.
- Dashboard progress reads the lab baseline and recorded sessions. An expectations declaration
  alone no longer counts as a recorded run. Every registered bot needs a completed, named session.
- Bot-specific steps are optional for flows without bot code. Saving the user's name preserves
  the chosen application.
- Session results explicitly describe the database change check. A zero-change PASS prompts the
  user to confirm whether a read-only run was intended. The existing PASS/FAIL contract is retained.
- Updates without recorded column details fail column-restricted rules instead of passing through
  an empty-set comparison. Unrestricted column rules retain their documented behavior.
- Clarified local evidence storage versus sharing a package with a builder.

These changes affect the platform and its tests only; the sample applications and their
verification contracts are not redesigned.

## Remaining product decisions, in priority order

1. **Define business success per automation.** Current expectations constrain permitted changes;
   they do not require a change or validate its final value. Agree on representative acceptance
   cases with the process owner before adding outcome assertions: a successful update, a read-only
   report, a rejected input, and a failed run that must leave data unchanged. Keep outcome results
   separate from allowed-change results so existing expectations retain their meaning.
2. **Prove fidelity with real evidence.** Obtain approved UAT recordings, the relevant schema and
   actual automation inputs for the first target application. Have the owner identify which rules
   matter. Retain hypothesis labels until independently verified.
3. **Make readiness freshness visible.** The portal currently uses the latest saved conformance
   job. A subsequent app rebuild or data change may invalidate that result. Bind readiness to the
   checked build/configuration before treating it as a deployment gate.
4. **Keep advanced controls secondary.** Baseline history, masking, parity and fault calibration
   are useful after a first successful rehearsal. Prioritize the guided path before adding more
   top-level features or another runner.

## Operational limits

Live baseline/session progress is conservative when the lab is stopped or unreachable: it does
not claim completion without observable evidence. Dashboard requests now read agent metadata for
running apps; larger installations may need bounded concurrent reads or a timestamped cache.
No production access, real QTT validation, or OpenShift deployment was performed in this review.

## Verification

Regression coverage includes mixed-app onboarding, missing data, absent/active/anonymous sessions,
per-bot completion, an unavailable agent, selection preservation, flows without bots, missing update
columns and disallowed column combinations. Browser checks cover changing the selected app and a
390-pixel viewport; the desktop setup screenshot was visually inspected.

Validation results:

- Full suite: 851 passed, 1 skipped (collected before the new regression cases were added).
- Final focused portal and verdict suite, including new regressions: 45 passed.
- Contract validation: 23 files checked, 0 problems.
- Ruff checks passed for the changed platform modules and tests.
- Browser app selection and mobile overflow checks passed.

The full run reported two dependency deprecation warnings. Containers and OpenShift were not
rebuilt or deployed because this change does not modify a demo app or its image.
