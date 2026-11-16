# What each kind of evidence finds (E-04)

Measured on the reference app with `python tools/evidence_ablation.py --write`. The brief built
from the source code is the yardstick: each row is a brief built from less, as a team without
source would have it. A number is how many of the yardstick's items the brief also has;
"+N more" are items it has that the source brief does not (usually other names for the same
thing, or items outside the automation's needs).

| Evidence | Mode | screens (of 3) | selectors (of 12) | routes (of 9) | tables (of 7) | rules (of 11) |
|---|---|---|---|---|---|---|
| source + automation | copy | 3 | 12 | 9 | 7 | 11 |
| recording + automation | reconstruction | 3 | 12 (+34 more) | 8 | 3 (+2 more) | 3 |
| automation only | skeleton | 3 | 12 | 2 | 2 | 0 |
| OpenAPI + automation | skeleton | 3 | 12 | 3 | 2 | 0 |
| recording + OpenAPI + DDL + automation | reconstruction | 3 | 12 (+34 more) | 9 | 7 | 3 |
| flow + screenshots | sample | 3 | 0 | 0 | 0 | 0 (+2 more) |
| DDL + database automation | backend | 0 | 0 | 0 | 5 | 0 |

What the rows mean for an office team:

- **Source** is complete but rarely available.
- **A recording plus the bot's code** finds the screens, selectors and routes the bot uses, with
  their real response shapes; it cannot see tables, and only rules the recording happened to
  trigger.
- **The bot's code alone** gives exactly what the bot touches and nothing around it: enough for
  READY, thin for a person exploring the app.
- **OpenAPI** adds every route and its body constraints (rules), but no screens.
- **A database schema (DDL)** gives every table and column, keys and constraints, and nothing
  about screens or routes.
- **A written flow** gives screens and fields by name only; a builder has to guess selectors.

So the usual office set is a recording of each flow, the DBA's DDL, an API description if one
exists, and the bots' code. Each brief says, per screen, which of these it was built from (its
mode: copy, reconstruction, skeleton, backend, sample).

## What the subsets miss

**recording + automation** misses:
- routes: `GET /api/health`
- tables: `app_user`, `case_note`, `category`, `subcategory`
- rules: `401 NOT_SIGNED_IN`, `404 CASE_NOT_FOUND`, `409 CASE_CLOSED`, `422 SUBCATEGORY_MISMATCH`, `body password`, `body status`, `body text`, `body username`

**automation only** misses:
- routes: `GET /api/cases/search`, `GET /api/cases/{}`, `GET /api/cases/{}/notes`, `GET /api/categories`, `GET /api/categories/{}/subcategories`, `GET /api/health`, `PUT /api/cases/{}`
- tables: `app_user`, `case_note`, `category`, `customer`, `subcategory`
- rules: `401 INVALID_CREDENTIALS`, `401 NOT_SIGNED_IN`, `404 CASE_NOT_FOUND`, `409 CASE_CLOSED`, `422 ESCALATION_REASON_REQUIRED`, `422 NOTE_REQUIRED`, `422 SUBCATEGORY_MISMATCH`, `body password` ...

**OpenAPI + automation** misses:
- routes: `GET /api/cases/search`, `GET /api/cases/{}`, `GET /api/cases/{}/notes`, `GET /api/categories`, `GET /api/categories/{}/subcategories`, `PUT /api/cases/{}`
- tables: `app_user`, `case_note`, `category`, `customer`, `subcategory`
- rules: `401 INVALID_CREDENTIALS`, `401 NOT_SIGNED_IN`, `404 CASE_NOT_FOUND`, `409 CASE_CLOSED`, `422 ESCALATION_REASON_REQUIRED`, `422 NOTE_REQUIRED`, `422 SUBCATEGORY_MISMATCH`, `body password` ...

**recording + OpenAPI + DDL + automation** misses:
- rules: `401 NOT_SIGNED_IN`, `404 CASE_NOT_FOUND`, `409 CASE_CLOSED`, `422 SUBCATEGORY_MISMATCH`, `body password`, `body status`, `body text`, `body username`

**flow + screenshots** misses:
- routes: `GET /api/cases/search`, `GET /api/cases/{}`, `GET /api/cases/{}/notes`, `GET /api/categories`, `GET /api/categories/{}/subcategories`, `GET /api/health`, `POST /api/cases/{}/notes`, `POST /api/login` ...
- tables: `app_user`, `case_audit`, `case_note`, `cases`, `category`, `customer`, `subcategory`
- rules: `401 INVALID_CREDENTIALS`, `401 NOT_SIGNED_IN`, `404 CASE_NOT_FOUND`, `409 CASE_CLOSED`, `422 ESCALATION_REASON_REQUIRED`, `422 NOTE_REQUIRED`, `422 SUBCATEGORY_MISMATCH`, `body password` ...

**DDL + database automation** misses:
- screens: `/cases`, `/cases/{}`, `/login`
- routes: `GET /api/cases/search`, `GET /api/cases/{}`, `GET /api/cases/{}/notes`, `GET /api/categories`, `GET /api/categories/{}/subcategories`, `GET /api/health`, `POST /api/cases/{}/notes`, `POST /api/login` ...
- tables: `app_user`, `case_note`
- rules: `401 INVALID_CREDENTIALS`, `401 NOT_SIGNED_IN`, `404 CASE_NOT_FOUND`, `409 CASE_CLOSED`, `422 ESCALATION_REASON_REQUIRED`, `422 NOTE_REQUIRED`, `422 SUBCATEGORY_MISMATCH`, `body password` ...

