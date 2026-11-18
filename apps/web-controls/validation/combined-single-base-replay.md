# Fidelity: combined

**Result: blocked**

**Status: BLOCKED**

- replay BLOCKED
- no required acceptance case ran: structure alone is never a scoped PASS

| Category | Result | Required passed / total | Optional passed / total | Note |
|---|---|---|---|---|
| capture | NOT_DECLARED | 0 / 0 | 0 / 0 | no expected action list was declared |
| replay | BLOCKED | 1 / 31 | 0 / 0 |  |
| behaviour | NOT_DECLARED | 0 / 0 | 0 / 0 |  |
| data | NOT_DECLARED | 0 / 0 | 0 / 0 |  |
| appearance | NOT_DECLARED | 0 / 0 | 0 / 0 |  |

Compared: recording combined be2e522380b4 (captured 2026-11-18T04:03:44+00:00, source None); bindings none; acceptance none; baseline none; visual none; chromium 143.0.7499.4; viewport 1280x900

## First divergence

- step goto at step 2
- step 2: a.home-link missing (recorded: 1, live: 0)

**Assumed:**

- the recording is complete (no expected action list was declared)

| Steps | Replayed | Implied | Compared | Failed | Skipped | Unresolved | Missing value | Unsupported | Expected actions | Captured | Not captured |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 31 | 12 | 0 | 12 | 11 | 18 | 1 | 0 | 0 | not declared | 31 | 0 |

## Why not pass

- step 13 (type) BLOCKED: no candidate matched exactly one element: #my-text-id: matched 0; role textbox "Text input": matched 0; label "Text input": matched 0; structure body > main.flex-shrink-2 > div.container > form > div.row > div.col-md-4.py-2 > label.form-label > input.form-control [0 of 8]: matched 0 (8 when recorded); css input#my-text-id: matched 0
- 11 step(s) failed, first step 2 (goto)

| Step | Action | Selector | Target | Result |
|---|---|---|---|---|
| 1 | goto |  |  | pass |
| 2 | goto |  |  | fail |
| 3 | check | input | structure #checkboxes > input [0 of 2] | fail |
| 4 | uncheck | input | structure #checkboxes > input [1 of 2] | fail |
| 5 | goto |  |  | fail |
| 6 | select | select#dropdown | #dropdown | fail |
| 7 | select | select#dropdown | #dropdown | fail |
| 8 | goto |  |  | fail |
| 9 | click | button | role button "Add Element" | fail |
| 10 | click | button | role button "Add Element" | fail |
| 11 | click | button.added-manually | structure #elements > button.added-manually [0 of 2] | fail |
| 12 | goto |  |  | fail |
| 13 | type | input#my-text-id |  | blocked |
| 14 | type | textarea[name="my-textarea"] |  | skipped |
| 15 | select | select[name="my-select"] |  | skipped |
| 16 | uncheck | input#my-check-1 |  | skipped |
| 17 | check | input#my-check-2 |  | skipped |
| 18 | check | input#my-radio-2 |  | skipped |
| 19 | click | button.btn.btn-outline-primary |  | skipped |
| 20 | submit | form |  | skipped |
| 21 | goto |  |  | skipped |
| 22 | goto |  |  | skipped |
| 23 | goto |  |  | skipped |
| 24 | check | input |  | skipped |
| 25 | goto |  |  | skipped |
| 26 | select | select#dropdown |  | skipped |
| 27 | goto |  |  | skipped |
| 28 | type | input#my-text-id |  | skipped |
| 29 | click | button.btn.btn-outline-primary |  | skipped |
| 30 | submit | form |  | skipped |
| 31 | goto |  |  | skipped |

## Differences

- step 2: a.home-link missing (recorded: 1, live: 0)
- step 3: a.home-link missing (recorded: 1, live: 0)
- step 4: a.home-link missing (recorded: 1, live: 0)
- step 5: a.home-link missing (recorded: 1, live: 0)
- step 6: a.home-link missing (recorded: 1, live: 0)
- step 7: a.home-link missing (recorded: 1, live: 0)
- step 8: a.home-link missing (recorded: 1, live: 0)
- step 9: a.home-link missing (recorded: 1, live: 0)
- step 10: a.home-link missing (recorded: 1, live: 0)
- step 11: a.home-link missing (recorded: 1, live: 0)
- step 12: input#my-text-id.form-control missing (recorded: 1, live: 0)
- step 12: input.form-control missing (recorded: 6, live: 0)
- step 12: textarea.form-control missing (recorded: 1, live: 0)
- step 12: a missing (recorded: 1, live: 0)
- step 12: select.form-select missing (recorded: 1, live: 0)
- step 12: input#my-check-1.form-check-input missing (recorded: 1, live: 0)
- step 12: input#my-check-2.form-check-input missing (recorded: 1, live: 0)
- step 12: input#my-radio-1.form-check-input missing (recorded: 1, live: 0)
- step 12: input#my-radio-2.form-check-input missing (recorded: 1, live: 0)
- step 12: button.btn.btn-outline-primary.mt-3 missing (recorded: 1, live: 0)
- step 12: input.form-control.form-control-color missing (recorded: 1, live: 0)
- step 12: input.form-range missing (recorded: 1, live: 0)
- step 12: input[name='my-password'] visibility (recorded: True, live: False)
- step 12: textarea[name='my-textarea'] visibility (recorded: True, live: False)
- step 12: input[name='my-disabled'] visibility (recorded: True, live: False)
- step 12: input[name='my-readonly'] visibility (recorded: True, live: False)
- step 12: select[name='my-select'] visibility (recorded: True, live: False)
- step 12: input[name='my-datalist'] visibility (recorded: True, live: False)
- step 12: #my-options visibility (recorded: True, live: False)
- step 12: input[name='my-file'] visibility (recorded: True, live: False)
- step 12: input[name='my-colors'] visibility (recorded: True, live: False)
- step 12: input[name='my-date'] visibility (recorded: True, live: False)
- step 12: input[name='my-range'] visibility (recorded: True, live: False)
- step 13: input#my-text-id target (recorded: 'type', live: 'no candidate matched exactly one element: #my-text-id: matched 0; role textbox "Text input": matched 0; label "Text input": matched 0; structure body > main.flex-shrink-2 > div.container > form > div.row > div.col-md-4.py-2 > label.form-label > input.form-control [0 of 8]: matched 0 (8 when recorded); css input#my-text-id: matched 0')
