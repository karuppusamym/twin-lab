# Fidelity: combined

**Result: fail**

**Status: FAIL**

- replay FAILED
- no required acceptance case ran: structure alone is never a scoped PASS

| Category | Result | Required passed / total | Optional passed / total | Note |
|---|---|---|---|---|
| capture | NOT_DECLARED | 0 / 0 | 0 / 0 | no expected action list was declared |
| replay | FAIL | 29 / 31 | 0 / 0 |  |
| behaviour | NOT_DECLARED | 0 / 0 | 0 / 0 |  |
| data | NOT_DECLARED | 0 / 0 | 0 / 0 |  |
| appearance | NOT_DECLARED | 0 / 0 | 0 / 0 |  |

Compared: recording combined be2e522380b4 (captured 2026-11-18T04:03:44+00:00, source None); bindings none; acceptance none; baseline none; visual none; chromium 143.0.7499.4; viewport 1280x900

## First divergence

- step submit at step 20, locator submitted by step 19
- step 20: input#my-text-id.form-control missing (recorded: 1, live: 0)
- source requests: GET /selenium/web/submitted-form.html*

**Assumed:**

- the recording is complete (no expected action list was declared)

| Steps | Replayed | Implied | Compared | Failed | Skipped | Unresolved | Missing value | Unsupported | Expected actions | Captured | Not captured |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 31 | 31 | 2 | 29 | 2 | 0 | 0 | 0 | 0 | not declared | 31 | 0 |

## Why not pass

- 2 step(s) failed, first step 20 (submit)

| Step | Action | Selector | Target | Result |
|---|---|---|---|---|
| 1 | goto |  |  | pass |
| 2 | goto |  |  | pass |
| 3 | check | input | structure #checkboxes > input [0 of 2] | pass |
| 4 | uncheck | input | structure #checkboxes > input [1 of 2] | pass |
| 5 | goto |  |  | pass |
| 6 | select | select#dropdown | #dropdown | pass |
| 7 | select | select#dropdown | #dropdown | pass |
| 8 | goto |  |  | pass |
| 9 | click | button | role button "Add Element" | pass |
| 10 | click | button | role button "Add Element" | pass |
| 11 | click | button.added-manually | structure #elements > button.added-manually [0 of 2] | pass |
| 12 | goto |  |  | pass |
| 13 | type | input#my-text-id | #my-text-id | pass |
| 14 | type | textarea[name="my-textarea"] | role textbox "Textarea" | pass |
| 15 | select | select[name="my-select"] | label "Dropdown (select) Open this select menu One Two Three" | pass |
| 16 | uncheck | input#my-check-1 | #my-check-1 | pass |
| 17 | check | input#my-check-2 | #my-check-2 | pass |
| 18 | check | input#my-radio-2 | #my-radio-2 | pass |
| 19 | click | button.btn.btn-outline-primary | role button "Submit" | pass |
| 20 | submit | form | submitted by step 19 | fail |
| 21 | goto |  |  | pass |
| 22 | goto |  |  | pass |
| 23 | goto |  |  | pass |
| 24 | check | input | structure #checkboxes > input [0 of 2] | pass |
| 25 | goto |  |  | pass |
| 26 | select | select#dropdown | #dropdown | pass |
| 27 | goto |  |  | pass |
| 28 | type | input#my-text-id | #my-text-id | pass |
| 29 | click | button.btn.btn-outline-primary | role button "Submit" | pass |
| 30 | submit | form | submitted by step 29 | fail |
| 31 | goto |  |  | pass |

## Differences

- step 20: input#my-text-id.form-control missing (recorded: 1, live: 0)
- step 20: input.form-control missing (recorded: 6, live: 0)
- step 20: textarea.form-control missing (recorded: 1, live: 0)
- step 20: a missing (recorded: 1, live: 0)
- step 20: select.form-select missing (recorded: 1, live: 0)
- step 20: input#my-check-1.form-check-input missing (recorded: 1, live: 0)
- step 20: input#my-check-2.form-check-input missing (recorded: 1, live: 0)
- step 20: input#my-radio-1.form-check-input missing (recorded: 1, live: 0)
- step 20: input#my-radio-2.form-check-input missing (recorded: 1, live: 0)
- step 20: button.btn.btn-outline-primary.mt-3 missing (recorded: 1, live: 0)
- step 20: input.form-control.form-control-color missing (recorded: 1, live: 0)
- step 20: input.form-range missing (recorded: 1, live: 0)
- step 20: input[name='my-password'] visibility (recorded: True, live: False)
- step 20: textarea[name='my-textarea'] visibility (recorded: True, live: False)
- step 20: input[name='my-disabled'] visibility (recorded: True, live: False)
- step 20: input[name='my-readonly'] visibility (recorded: True, live: False)
- step 20: select[name='my-select'] visibility (recorded: True, live: False)
- step 20: input[name='my-datalist'] visibility (recorded: True, live: False)
- step 20: #my-options visibility (recorded: True, live: False)
- step 20: input[name='my-file'] visibility (recorded: True, live: False)
- step 20: input[name='my-colors'] visibility (recorded: True, live: False)
- step 20: input[name='my-date'] visibility (recorded: True, live: False)
- step 20: input[name='my-range'] visibility (recorded: True, live: False)
- step 30: input#my-text-id.form-control missing (recorded: 1, live: 0)
- step 30: input.form-control missing (recorded: 6, live: 0)
- step 30: textarea.form-control missing (recorded: 1, live: 0)
- step 30: a missing (recorded: 1, live: 0)
- step 30: select.form-select missing (recorded: 1, live: 0)
- step 30: input#my-check-1.form-check-input missing (recorded: 1, live: 0)
- step 30: input#my-check-2.form-check-input missing (recorded: 1, live: 0)
- step 30: input#my-radio-1.form-check-input missing (recorded: 1, live: 0)
- step 30: input#my-radio-2.form-check-input missing (recorded: 1, live: 0)
- step 30: button.btn.btn-outline-primary.mt-3 missing (recorded: 1, live: 0)
- step 30: input.form-control.form-control-color missing (recorded: 1, live: 0)
- step 30: input.form-range missing (recorded: 1, live: 0)
- step 30: input[name='my-password'] visibility (recorded: True, live: False)
- step 30: textarea[name='my-textarea'] visibility (recorded: True, live: False)
- step 30: input[name='my-disabled'] visibility (recorded: True, live: False)
- step 30: input[name='my-readonly'] visibility (recorded: True, live: False)
- step 30: select[name='my-select'] visibility (recorded: True, live: False)
- step 30: input[name='my-datalist'] visibility (recorded: True, live: False)
- step 30: #my-options visibility (recorded: True, live: False)
- step 30: input[name='my-file'] visibility (recorded: True, live: False)
- step 30: input[name='my-colors'] visibility (recorded: True, live: False)
- step 30: input[name='my-date'] visibility (recorded: True, live: False)
- step 30: input[name='my-range'] visibility (recorded: True, live: False)
