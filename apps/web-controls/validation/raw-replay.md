# Fidelity: combined

**Result: fail**

| Steps | Replayed | Implied | Compared | Failed | Skipped | Unresolved | Missing value | Unsupported | Expected actions | Captured | Not captured |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 15 | 15 | 0 | 15 | 14 | 0 | 0 | 0 | 0 | not declared | 15 | 0 |

## Why not pass

- 14 step(s) failed, first step 2 (goto)

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
| 13 | check | input | structure #checkboxes > input [0 of 2] | fail |
| 14 | goto |  |  | fail |
| 15 | select | select#dropdown | #dropdown | fail |

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
- step 12: a.home-link missing (recorded: 1, live: 0)
- step 13: a.home-link missing (recorded: 1, live: 0)
- step 14: a.home-link missing (recorded: 1, live: 0)
- step 15: a.home-link missing (recorded: 1, live: 0)
