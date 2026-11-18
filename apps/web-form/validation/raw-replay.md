# Fidelity: combined

**Result: fail**

| Steps | Replayed | Implied | Compared | Failed | Skipped | Unresolved | Missing value | Unsupported | Expected actions | Captured | Not captured |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 16 | 16 | 2 | 14 | 12 | 0 | 0 | 0 | 0 | not declared | 16 | 0 |

## Why not pass

- 12 step(s) failed, first step 1 (goto)

| Step | Action | Selector | Target | Result |
|---|---|---|---|---|
| 1 | goto |  |  | fail |
| 2 | type | input#my-text-id | #my-text-id | fail |
| 3 | type | textarea[name="my-textarea"] | role textbox "Textarea" | fail |
| 4 | select | select[name="my-select"] | label "Dropdown (select) Open this select menu One Two Three" | fail |
| 5 | uncheck | input#my-check-1 | #my-check-1 | fail |
| 6 | check | input#my-check-2 | #my-check-2 | fail |
| 7 | check | input#my-radio-2 | #my-radio-2 | fail |
| 8 | click | button.btn.btn-outline-primary | role button "Submit" | pass |
| 9 | submit | form | submitted by step 19 | fail |
| 10 | goto |  |  | pass |
| 11 | goto |  |  | fail |
| 12 | goto |  |  | fail |
| 13 | type | input#my-text-id | #my-text-id | fail |
| 14 | click | button.btn.btn-outline-primary | role button "Submit" | pass |
| 15 | submit | form | submitted by step 29 | fail |
| 16 | goto |  |  | pass |

## Differences

- step 1: a missing (recorded: 1, live: 0)
- step 2: a missing (recorded: 1, live: 0)
- step 3: a missing (recorded: 1, live: 0)
- step 4: a missing (recorded: 1, live: 0)
- step 5: a missing (recorded: 1, live: 0)
- step 6: a missing (recorded: 1, live: 0)
- step 7: a missing (recorded: 1, live: 0)
- step 9: input#my-text-id.form-control missing (recorded: 1, live: 0)
- step 9: input.form-control missing (recorded: 6, live: 0)
- step 9: textarea.form-control missing (recorded: 1, live: 0)
- step 9: a missing (recorded: 1, live: 0)
- step 9: select.form-select missing (recorded: 1, live: 0)
- step 9: input#my-check-1.form-check-input missing (recorded: 1, live: 0)
- step 9: input#my-check-2.form-check-input missing (recorded: 1, live: 0)
- step 9: input#my-radio-1.form-check-input missing (recorded: 1, live: 0)
- step 9: input#my-radio-2.form-check-input missing (recorded: 1, live: 0)
- step 9: button.btn.btn-outline-primary.mt-3 missing (recorded: 1, live: 0)
- step 9: input.form-control.form-control-color missing (recorded: 1, live: 0)
- step 9: input.form-range missing (recorded: 1, live: 0)
- step 9: input[name='my-password'] visibility (recorded: True, live: False)
- step 9: textarea[name='my-textarea'] visibility (recorded: True, live: False)
- step 9: input[name='my-disabled'] visibility (recorded: True, live: False)
- step 9: input[name='my-readonly'] visibility (recorded: True, live: False)
- step 9: select[name='my-select'] visibility (recorded: True, live: False)
- step 9: input[name='my-datalist'] visibility (recorded: True, live: False)
- step 9: #my-options visibility (recorded: True, live: False)
- step 9: input[name='my-file'] visibility (recorded: True, live: False)
- step 9: input[name='my-colors'] visibility (recorded: True, live: False)
- step 9: input[name='my-date'] visibility (recorded: True, live: False)
- step 9: input[name='my-range'] visibility (recorded: True, live: False)
- step 11: a missing (recorded: 1, live: 0)
- step 12: a missing (recorded: 1, live: 0)
- step 13: a missing (recorded: 1, live: 0)
- step 15: input#my-text-id.form-control missing (recorded: 1, live: 0)
- step 15: input.form-control missing (recorded: 6, live: 0)
- step 15: textarea.form-control missing (recorded: 1, live: 0)
- step 15: a missing (recorded: 1, live: 0)
- step 15: select.form-select missing (recorded: 1, live: 0)
- step 15: input#my-check-1.form-check-input missing (recorded: 1, live: 0)
- step 15: input#my-check-2.form-check-input missing (recorded: 1, live: 0)
- step 15: input#my-radio-1.form-check-input missing (recorded: 1, live: 0)
- step 15: input#my-radio-2.form-check-input missing (recorded: 1, live: 0)
- step 15: button.btn.btn-outline-primary.mt-3 missing (recorded: 1, live: 0)
- step 15: input.form-control.form-control-color missing (recorded: 1, live: 0)
- step 15: input.form-range missing (recorded: 1, live: 0)
- step 15: input[name='my-password'] visibility (recorded: True, live: False)
- step 15: textarea[name='my-textarea'] visibility (recorded: True, live: False)
- step 15: input[name='my-disabled'] visibility (recorded: True, live: False)
- step 15: input[name='my-readonly'] visibility (recorded: True, live: False)
- step 15: select[name='my-select'] visibility (recorded: True, live: False)
- step 15: input[name='my-datalist'] visibility (recorded: True, live: False)
- step 15: #my-options visibility (recorded: True, live: False)
- step 15: input[name='my-file'] visibility (recorded: True, live: False)
- step 15: input[name='my-colors'] visibility (recorded: True, live: False)
- step 15: input[name='my-date'] visibility (recorded: True, live: False)
- step 15: input[name='my-range'] visibility (recorded: True, live: False)
