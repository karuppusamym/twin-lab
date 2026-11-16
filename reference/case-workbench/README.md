# Case Workbench (reference application)

A small case-management application that stands in for "the real application's source". The Twin
Lab factory will read this folder the way it will later read a real repository, and build a demo
copy of it under `apps/`. It is **not** a demo app itself and is never deployed by the lab.

Synthetic data only. The seed is generated from a fixed number, so it is the same every time.

| | |
|---|---|
| Frontend | React 18, TypeScript, Vite, React Router 6 |
| Backend | FastAPI, SQLAlchemy 2 |
| Database | SQLite by default; any SQLAlchemy URL through `DATABASE_URL` |
| Tables | `app_user`, `customer`, `category`, `subcategory`, `cases`, `case_note`, `case_audit`, `canned_response` |

## Run

API on port 8710, from `backend/`:

```bash
python -m uvicorn app.server:app --port 8710
```

Web on port 5173, from `frontend/`:

```bash
npm install --os=win32 --cpu=x64
```

```bash
npm run dev
```

Open http://localhost:5173. The test sign-in is in `backend/app/seed.py` (`DEMO_USER`,
`DEMO_PASSWORD`). To start again from the seed, stop the API and delete
`backend/case_workbench.db`.

The `--os` and `--cpu` flags are only needed on a machine whose npm configuration sets another
operating system; without them the native build tools for the wrong system are installed.

## Screens

| Screen | Path | Elements an automation uses |
|---|---|---|
| Sign in | `/login` | `#username`, `#password`, `#btnLogin`, `#loginError` |
| Find a case | `/cases` | `#caseNo`, `#customerName`, `#status`, `#btnSearch`, `#resultCount`, `table#results`, `td.status`, `a.case-link` |
| Case detail | `/cases/{caseNo}` | `#fCaseNo`, `#fStatus`, `#fCustomerName`, `#category`, `#subcategory`, `#caseStatus`, `#escalationReason`, `#btnSave`, `#noteText`, `#btnAddNote`, `#message`, `ul#notes` |
| Canned responses | `/admin/responses` | `table#responses`, `#respCode`, `#respTitle`, `#respBody`, `#btnAddResponse`. **No automation uses this screen**: it is here so the brief has something to leave out. |

## Behaviour worth reproducing

Client side:

- **Cascade.** Choosing a category reloads the sub-category list and clears the previous choice.
- **Conditional field.** `#escalationReason` exists only while status is `ESCALATED`.
- **Disabled until valid.** Save needs a category and sub-category; Add note needs text.
- **Search runs on the button**, not as you type. Results replace the previous ones.

Server side (each returns `{"detail": {"code", "message"}}`):

| Rule | Status | Code |
|---|---|---|
| Escalating needs a reason of at least 10 characters | 422 | `ESCALATION_REASON_REQUIRED` |
| Resolving needs at least one note on the case | 422 | `NOTE_REQUIRED` |
| Sub-category must belong to the category | 422 | `SUBCATEGORY_MISMATCH` |
| Note text is 1 to 500 characters | 422 | (framework validation shape) |
| A closed case cannot be updated or noted | 409 | `CASE_CLOSED` |
| Every update and note writes a `case_audit` row | | |
| Case number search is by prefix, customer name by substring, both ignoring case; at most 50 rows | | |

## Tests

From the repository root:

```bash
python -m pytest reference -q
```

Backend rules are covered by tests. The frontend has no automated tests; it was checked by hand in
a browser.
