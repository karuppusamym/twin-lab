# Contracts

The files every part agrees on (the app model and flow files are described in
[06-evidence.md](06-evidence.md) and get their contracts in plan item E-01). Each becomes a pydantic model in `lab/labctl` with an
example under `specs/`; a test loads the example and asserts it validates. All names and values
below are illustrative.

## `automations/<id>/automation.yaml`

```yaml
id: uipath-case-update
name: Case update (UiPath original)
type: uipath                # uipath | agentic | python | other
surface: combo              # frontend | backend | combo
trigger: manual
owner: unassigned
source: {path: ./project, entry: Main.xaml}     # or {repo: <alias>, ref: <commit>}
replaced_by: agentic-case-update                # optional pairing for comparisons

touches:
  - app: qtt
    via: web
    screens:
      - name: search
        path: /cases/search
        uses:
          - {action: type,  selector: "#caseNo",      field: case_no}
          - {action: click, selector: "#btnSearch"}
          - {action: read,  selector: "td.status",    field: status}
  - app: qtt
    via: api
    routes:
      - {method: POST, path: "/api/cases/{id}/notes"}
  - app: qtt
    via: db
    tables:
      - {table: CASE_AUDIT, columns: [CASE_ID, EVENT, CREATED_TS], access: read}

bindings:                   # what the automation needs to be told; filled by the portal
  in_QttUrl:  "{{qtt.web}}"
  in_QttApi:  "{{qtt.api}}"
  in_User:    "{{qtt.credentials.username}}"

analysis:
  by: uipath-analyser@0.1.0
  gaps: []                  # each {where, what}: what the analyser could not interpret
  notes: []                 # same shape; worth a look, but do not block readiness
  reviewed_by: null
```

A `uses` entry may also carry `raw`, the selector as the automation wrote it; `selector` is its CSS
form, or `uipath:<raw>` when CSS cannot express it. `field` is the variable carried, never a typed
value. Actions: `type`, `click`, `read`, `select`, `wait`, `hover`.

Rules: `touches` is produced by an analyser and confirmed by a person. An analyser never drops what
it cannot interpret; it goes in `gaps`, and a non-empty `gaps` keeps readiness at BLOCKED until a
person resolves each one.

## `apps/<app>/app.yaml`

```yaml
id: qtt
name: QTT (demo copy)
copies: {system: QTT, source: <repo alias>, ref: <commit>, analysed: 2026-11-20}
stack:
  frontend: {framework: angular, version: "14.2", ui_library: "material 14"}
  backend:  {framework: spring-boot, version: "2.7", language: java11}
  db:       {engine: postgres, version: "15"}
run:
  web: {port: 4200}
  api: {port: 8080, health: /actuator/health}
  env: {database_url: DATABASE_URL}
db:
  schema: db/schema.sql
  derived_from: [backend entities, migrations]
built_for: [uipath-case-update, agentic-case-update]
rules:
  - {id: R1, where: "POST /api/cases/{id}/notes", rule: "note text max 500", verification: hypothesis}
links:                      # same real-world thing under another app's key
  - {entity: customer, column: CASE.CUST_ID, to: crm.CUSTOMER.ID, via: equal}
```

`verification` is `hypothesis`, `owner_approved` or `reference_verified`. An AI-authored pull
request may only write `hypothesis`.

## Data pack: `data-packs/<pack>/pack.yaml`

```yaml
id: qtt-basic
apps: [qtt]
kind: synthetic             # synthetic | byo
generator: {seed: 20261115, rows: {CASE: 200, CUSTOMER: 150}}   # synthetic only
workbook: qtt-basic.xlsx    # byo, and the output of a synthetic run
derived_from_real_data: false      # true requires masked: true
masked: false
schema_ref: {qtt: "sha256:<hash of db/schema.sql>"}
```

Workbook shape: one sheet per table, named `<app>.<TABLE>`. Row 1 is column names exactly as in the
schema. Empty cell means null. A pack whose `schema_ref` no longer matches the app's schema is
refused at load with the differing tables listed.

## Condition profile: `conditions/<id>.yaml`

```yaml
id: peak-hour
description: Search and submit slow, database under load.
calibration: estimate       # robot_logs | har | estimate
calibrated_from: "interview with bot support, 2026-11"
seed: 7                     # optional; fixes the delays and faults for a repeatable demo
targets:
  - app: qtt
    http:
      - {match: "GET /api/cases/search*", latency_ms: {p50: 4000, p95: 15000}}
      - {match: "POST /api/cases/*",      latency_ms: {p50: 2500, p95: 9000}}
      - {match: "*",                      latency_ms: {p50: 400,  p95: 1200}}
    db: {latency_ms: 150, jitter_ms: 80}
    faults:
      - {match: "POST /api/cases/*/notes", status: 503, rate: 0.05}
```

First matching `http` rule wins. `db` applies to the link between the app and its database. A
profile with a fixed random seed gives the same delays on every run, for a repeatable demo.

## Session (written by the lab agent, read by the portal)

```json
{
  "session_id": "ses_20261120T101500Z_a41c9e",
  "automation": "uipath-case-update",
  "apps": [{"app": "qtt", "version": "0.3.1", "data_pack": "qtt-basic", "condition": "peak-hour"}],
  "started": "2026-11-20T10:15:00Z",
  "ended": "2026-11-20T10:19:42Z",
  "requests": [
    {"ts": "2026-11-20T10:15:08.120Z", "app": "qtt", "method": "GET",
     "path": "/api/cases/search", "status": 200, "app_ms": 38, "injected_ms": 6120}
  ],
  "changes": [
    {"app": "qtt", "table": "CASE_NOTE", "op": "insert", "key": "N-0201"}
  ],
  "summary": {"requests": 41, "wall_s": 282, "waiting_on_app_s": 197, "errors": 2}
}
```

`app_ms` and `injected_ms` are kept apart so a report never passes off injected delay as the demo
app's own speed.

## Bindings (given to an automation)

```json
{
  "apps": {
    "qtt": {
      "web": "http://localhost:4200",
      "api": "http://localhost:8080",
      "db":  "postgresql://lab:<generated>@localhost:5432/qtt",
      "credentials": {"username": "demo.user", "password": "<generated>"}
    }
  }
}
```

Same shape as Application Twin Studio's `ATS_BINDINGS`, so `ats/sdk.py` keeps working. Credentials
are test values created by the data pack, valid only inside the lab.
