# QTT sample (an invented stand-in, not the real QTT)

We have neither the real QTT nor a description of it. This folder is an **invented** application
used to test Twin Lab end to end: its expansion of the name ("Quality Tracking Tool"), its domain
(loan quality-control analysts reviewing closed loan files against a checklist, a reviewer
approving or returning the review), its screens and its data were all made up. Nothing here
describes the real QTT. Twin Lab treats it as a **black box**:
it records the app, analyses the recordings and the bots written against it, and builds a demo
copy under `apps/qtt/` **without reading this source**. Keep that in mind when changing it: what
matters is the markup, the API and the database the outside world sees.

Synthetic data only. The seed is generated from a fixed number, so it is the same every time.

| | |
|---|---|
| Pages | Server-rendered with Jinja2; plain JavaScript (`fetch`) for saves on the review page |
| Backend | FastAPI |
| Database | SQLite file `data/qtt.db`, created and seeded on first start |
| Sign-in | Form post to `/login`; session cookie `QTTSESSION` (also accepted as a bearer token) |
| Tables | `users`, `loans`, `qc_reviews`, `checklist_items`, `review_items`, `review_audit` |
| SQL Server DDL | `db/ddl-sqlserver.sql`, the same tables as a DBA would hand them over (evidence) |

## Run

From this folder (`reference/qtt-sample/`):

```bash
python -m uvicorn app.main:app --port 8730
```

Open http://localhost:8730. To start again from the seed, stop the app and delete `data/qtt.db`
(`QTT_DB_PATH` points the app at another file).

| User | Role | Password |
|---|---|---|
| `analyst1` (Avery Holt) | ANALYST | `Qtt-Sample-2026` |
| `analyst2` (Jordan Pike) | ANALYST | `Qtt-Sample-2026` |
| `reviewer1` (Morgan Reyes) | REVIEWER | `Qtt-Sample-2026` |

The password is a synthetic test value, valid only for this sample.

## Seed

80 loans (`L-200001` to `L-200080`), a 12-item checklist in 4 categories, and 50 reviews
(`R-1001` to `R-1050`): 15 OPEN, 10 IN_PROGRESS, 10 SUBMITTED, 5 RETURNED, 10 APPROVED, assigned
alternately to `analyst1` and `analyst2`. Reviews used by the automations and recordings:

| Review | State | Assignee | Used by |
|---|---|---|---|
| R-1001 | OPEN | analyst1 | UiPath bot, `qc-review` recording |
| R-1003 | SUBMITTED | analyst1 | `review-return` recording |
| R-1005 | APPROVED | analyst1 | `error-cases` (locked page) |
| R-1007 | SUBMITTED | analyst1 | `error-cases` (short return reason) |
| R-1011 | OPEN | analyst1 | `error-cases` (submit incomplete, Fail without comment) |

## Screens and element ids

| Screen | Path | Ids an automation can use |
|---|---|---|
| Sign in | `/login` | `#loginForm`, `#username`, `#password`, `#btnLogin`, `#loginError` |
| Work queue | `/queue?status=&q=&mine=1` | `#queueFilters`, `#statusFilter`, `#reviewSearch`, `#mineOnly`, `#btnFilter`, `#btnClearFilters`, `#resultCount`, `table#queueTable` (`tr.review-row[data-review-no]`, `td.review-no`, `td.loan-no`, `td.borrower`, `td.product`, `td.status`, `td.due-date`, `td.assignee`), `#open-{review_no}`, `#noReviews` |
| Review detail | `/reviews/{review_no}` | `#reviewNo`, `#reviewStatus`, `#message`, `#lockNotice`, `#returnNotice`, `#returnReasonText`, `#loanSummary` (`#loanNo`, `#borrowerName`, `#product`, `#loanAmount`, `#closingDate`, `#branch`), `#reviewType`, `#assignee`, `#dueDate`, `#progress`, `table#checklist` (`tbody.category[data-category]`, `tr#item-{item}`), `#result-{item}`, `#comment-{item}`, `#save-{item}`, `#itemStatus-{item}`, `#findings` (`li.finding`), `#findingsCount`, `#noFindings`, `#btnSubmit`, `#btnReopen`, `#reviewerPanel`, `#returnReason`, `#btnApprove`, `#btnReturn`, `table#auditTable` (`#auditRows`, `tr.audit-row`) |
| Loan search | `/loans?q=` | `#loanSearchForm`, `#loanQuery`, `#btnSearch`, `#searchHint`, `#loanCount`, `table#loanResults` (`tr.loan-row`, `td.loan-no`, `td.borrower`, ...), `#loan-{loan_no}`, `#noResults` |
| Loan detail | `/loans/{loan_no}` | `#loanNo`, `#borrowerName`, `#coBorrowerName`, `#product`, `#loanAmount`, `#interestRate`, `#closingDate`, `#branch`, `#loanOfficer`, `#propertyState`, `table#loanReviews`, `#backToSearch` |
| Checklist templates | `/admin/checklist` (reviewer only) | `table#templateTable`, `#tplDesc-{item}`, `#tplActive-{item}`, `#tplSave-{item}`, `#adminMessage`. **No automation uses this screen.** |

Checklist items (`{item}`): `CRD-01..03` (Credit), `INC-01..03` (Income), `COL-01..03`
(Collateral), `CMP-01..03` (Compliance). Result options: Pass (`PASS`), Fail (`FAIL`), N/A (`NA`).

## JSON API

Every error body is `{"detail": {"code": ..., "message": ...}}`. `/api/*` needs the session
cookie or `Authorization: Bearer <token>` (from `POST /api/login`), except `/api/health`.

| Route | Does |
|---|---|
| `POST /api/login` | `{username, password}` -> `{token, username, full_name, role}`; sets the cookie |
| `GET /api/me` | the signed-in user |
| `GET /api/reviews?status=&assignee=&q=` | `{items, total}`; `assignee=me` for your own; at most 100 |
| `GET /api/reviews/{no}` | review with `loan`, `items`, `findings`, `progress`, `audit`, `locked`, `allowed` |
| `PUT /api/reviews/{no}/items/{item_id}` | `{result, comment}`; returns the review |
| `POST /api/reviews/{no}/submit` | analyst: OPEN / IN_PROGRESS -> SUBMITTED |
| `POST /api/reviews/{no}/approve` | reviewer: SUBMITTED -> APPROVED |
| `POST /api/reviews/{no}/return` | reviewer: `{reason}`; SUBMITTED -> RETURNED |
| `POST /api/reviews/{no}/reopen` | analyst: RETURNED -> IN_PROGRESS |
| `GET /api/loans?q=` | `{items, total}`; at most 50 |
| `GET /api/loans/{loan_no}` | loan with its reviews |
| `GET /api/health` | `{status, db, version}` |

## Rules

| Rule | Status | Code |
|---|---|---|
| Not signed in (API) | 401 | `NOT_AUTHENTICATED` (pages redirect to `/login?next=`) |
| Wrong user id or password | 401 | `INVALID_CREDENTIALS` (page: `#loginError`) |
| Unknown review / item / loan | 404 | `REVIEW_NOT_FOUND` / `ITEM_NOT_FOUND` / `LOAN_NOT_FOUND` |
| Only analysts save items, submit and reopen; only reviewers approve and return | 403 | `FORBIDDEN_ROLE` |
| Only the assigned analyst works on a review | 403 | `NOT_ASSIGNED` |
| An APPROVED review is read-only | 409 | `REVIEW_LOCKED` |
| Items change only while OPEN or IN_PROGRESS (a RETURNED review is reopened first) | 409 | `INVALID_STATUS` |
| Submit only from OPEN / IN_PROGRESS; approve and return only from SUBMITTED; reopen only from RETURNED | 409 | `INVALID_STATUS` |
| Result is PASS, FAIL or NA | 422 | `INVALID_RESULT` |
| Fail needs a comment (the page marks the comment required when Fail is chosen) | 422 | `COMMENT_REQUIRED` |
| Comment and return reason are at most 500 characters | 422 | `COMMENT_TOO_LONG` / `RETURN_REASON_TOO_LONG` |
| Submit needs every item answered (message lists the missing items, shown in `#message`) | 422 | `ITEMS_INCOMPLETE` |
| Return needs a reason of at least 15 characters | 422 | `RETURN_REASON_REQUIRED` |
| Malformed request body | 422 | `VALIDATION_ERROR` |
| The first saved item moves OPEN to IN_PROGRESS | | |
| Every item save and status change writes a `review_audit` row (`STARTED`, `ITEM_UPDATED`, `SUBMITTED`, `APPROVED`, `RETURNED`, `REOPENED`) | | |
| Searches match part of the text, ignoring case: review / loan no / borrower in the queue, loan no / borrower in loan search | | |

## Tests

From the repository root:

```bash
python -m pytest reference/qtt-sample/tests -q
```

They cover every route and rule above, the seed, the pages' element ids, and the two automations
written against the sample (`automations/uipath-qtt-qc-review/`, `automations/python-qtt-sla-report/`).
They are not in the repository's default `pytest` run, because this sample's package is also called
`app`; the folder has its own `pytest.ini`.

## Recording evidence

```bash
python tools/record_qtt.py
```

Deletes `data/qtt.db`, starts the sample on port 8730, records the flows `qc-review`,
`review-return`, `loan-search` and `error-cases` with headless Chromium into `evidence/qtt/<flow>/`
(`recording.har`, `dom/NNNN.html`, `steps.jsonl`), copies the DDL to `evidence/qtt/ddl.sql`, and
stops the sample. `evidence/` is Git-ignored: recordings are never committed. `--no-reset` records
against a sample you started yourself; `--flow NAME` records one flow.
