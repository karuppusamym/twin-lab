# Integration contract for the end-to-end build

How the remaining pieces fit together, so they can be built in parallel and joined without
rework. Each work package (WP) owns its files; nobody edits another package's files. Shared files
(`lab/labctl/labctl/cli.py`, `models.py`, `pyproject.toml`, `docs/04-plan.md`, `docs/CHANGELOG.md`,
`README.md`) are changed only by the integrator. A package that needs a change there says so in
its report.

Everything is synthetic. No real customer data, hostnames or credentials anywhere.

## Shared data shapes

```python
Rows = dict[str, list[dict[str, Any]]]   # table name -> rows; values JSON-safe (dates as ISO strings)
```

Tables are described by `labctl.source.sqlalchemy_models.Table` / `Column` (name, type, nullable,
primary_key, unique, index, references=(table, column), autoincrement). `db/schema.sql` files are
written by `labctl brief` in the dialect named in `app.yaml` (Postgres for every lab app).

## Ports (host)

| Use | Port |
|---|---|
| Portal | 8790 |
| Showcase lab agent (the app's front door) | 8720 |
| QTT demo app lab agent | 8721 |
| QTT sample (stand-in for the real QTT, the black box) | 8730 |
| Devin mock server | 8740 |
| Throwaway test containers | 18000-18999 |

## WP-A: evidence analysers (E-02, E-03 part)

Owns: `labctl/har.py`, `labctl/sanitize.py`, `labctl/openapi.py`, `labctl/model_build.py`
(adds `from_har`, `from_openapi`), `labctl/brief.py` (mode labels), `labctl/appmodel.py`
(new optional fields only), tests `tests/test_har.py`, `tests/test_openapi.py`, `tests/test_modes.py`.

- `from_har(app, har_path, repo_root, pages_dir) -> AppModel`. Screens from captured documents
  and the DOM recorded around them, API routes with request and response field shapes, status
  codes, error bodies as `observed` rules, timings, framework markers, code lists (low-cardinality
  values), tables `inferred` from payloads. Drops cookies and credential headers immediately. Never
  stores a recorded value: text is shape-masked (`Smith` -> `Xxxxx`, `1001` -> `9999`) except
  labels, headings, buttons, table headers, options and titles.
- Writes value-free page snapshots to `apps/<app>/pages/<n>.html` for the builder.
- `from_openapi(app, spec_path) -> AppModel` (`declared` routes with shapes; OpenAPI 3 and Postman
  v2.1).
- Mode per screen and per app: `copy`, `reconstruction`, `skeleton`, `backend`, `sample` (rules in
  `docs/06-evidence.md`), shown in the brief and written to `app.yaml` as `mode`.

## WP-B: lab agent (T-09, T-10 load and reset, T-12, T-13, T-14)

Owns: `lab/lab-agent/` (package `lab_agent`, Dockerfile, tests). Image `twinlab/lab-agent`. May
depend on the `labctl` package (copied into the image).

Environment: `APP_ID`, `APP_UPSTREAM` (e.g. `http://app:8000`), `DB_UPSTREAM` (`db:5432`),
`DB_PROXY_PORT` (15432), `ADMIN_DATABASE_URL` (direct, never through the proxy), `SCHEMA_PATH`,
`STATE_DIR`, `LAB_PORT` (9000), `LAB_TOKEN` (optional; when set, `/_lab/*` needs header
`X-Lab-Token`).

Everything not under `/_lab/` is proxied to the app (method, headers, body, redirects, cookies
untouched), after applying the current condition. The database proxy forwards TCP to
`DB_UPSTREAM`, adding the condition's latency and jitter to server-to-client traffic.

| Admin API | Does |
|---|---|
| `GET /_lab/health` | `{app: "ok"|"down", db: "ok"|"down"}`, 200 only if both ok |
| `GET /_lab/info` | app id, tables with columns (from the database), current condition id, baseline `{id, loaded_at, label}`, active session id |
| `GET /_lab/data` | current rows of every table (`Rows`) |
| `POST /_lab/data/load` | body `{rows: Rows, label}`: replaces the tables given; child tables that reference them are cleared unless also given (listed in the reply); identity sequences moved past the highest id; the result becomes the baseline. All or nothing. |
| `POST /_lab/data/baseline` | current data becomes the baseline |
| `POST /_lab/reset` | restore the baseline exactly; reply counts per table |
| `GET/PUT /_lab/condition` | the condition for this app: `{id, seed, http: [...], db: {latency_ms, jitter_ms}, faults: [...]}` as in `docs/03-contracts.md` |
| `POST /_lab/session/start` | `{automation?, label?}` -> `{session_id}`; snapshots data |
| `POST /_lab/session/stop` | snapshots again; returns a `Session` (contract in `labctl.models`) with requests and row changes |
| `GET /_lab/sessions`, `GET /_lab/sessions/{id}` | stored sessions |

## WP-C: data packs (T-10 workbook, T-11, E-03 DDL)

Owns: `labctl/data/` (`ddl.py`, `workbook.py`, `synthetic.py`, `cli.py`), tests
`tests/test_data_*.py`.

- `ddl.parse(text) -> list[Table]`: our generated DDL and ordinary `CREATE TABLE` from Postgres,
  SQL Server, Oracle and MySQL dumps (types, NOT NULL, PRIMARY KEY, UNIQUE, FOREIGN KEY, identity).
- `ddl.to_model(app, text, evidence_id) -> AppModel` (`declared` tables, evidence kind `ddl`,
  complete for tables).
- `workbook.template(tables, rows: Rows | None) -> bytes` (xlsx: a sheet per table, header row,
  types and required flags in a notes row or comments; optional current rows).
- `workbook.read(data: bytes, tables, existing: Rows | None) -> (Rows, problems)`: unknown sheets
  or columns, types, required, unique and primary keys, foreign keys (against the workbook and
  `existing`). Problems name sheet, row and column. Values converted to JSON-safe form.
- `synthetic.generate(tables, seed, counts, samples: Rows | None) -> Rows`: deterministic per seed;
  respects types, lengths, uniqueness, foreign keys; picks low-cardinality text values (status
  codes) from `samples` so the app's own code lists survive.
- `labctl data template|validate|generate` commands in `labctl/data/cli.py` (typer app).

## WP-E: Devin (build hand-off)

Owns: `labctl/devin/` (`package.py`, `client.py`, `jobs.py`, `mock.py`, `cli.py`), tests
`tests/test_devin_*.py`, `docs/devin.md`.

- `package.build(repo_root, app_id, out_dir) -> Path`: a zip a person can upload to Devin by hand:
  `DEVIN_TASK.md` (the prompt), the brief, model, schema, `app.yaml`, `apps/<app>/pages/`,
  screenshots from `evidence/<app>/screenshots/` if any, `AGENTS.md`, the playbook. No raw HAR.
- `client.DevinClient(api_key, org_id, base_url="https://api.devin.ai")` on API v3: `upload(path)`,
  `create_session(prompt, repos, attachment_urls, title, tags, max_acu_limit)`,
  `get_session(devin_id)`, `send_message`, `terminate`. Bearer auth. Statuses `new claimed running
  exit error suspended resuming`; `status_detail` `finished` / `waiting_for_user`; PRs in
  `pull_requests[].pr_url`. Retries on 429 and 5xx with backoff.
- `jobs`: SQLite job store under the state directory; `start_build(app_id, repo)` uploads the
  package, creates the session, and a background poller records status, `status_detail`, the
  session URL and the pull request URL until a stop state. Restart-safe: unfinished jobs resume
  polling on start.
- `mock`: a FastAPI app implementing the same v3 endpoints with a scripted lifecycle (`new` ->
  `running/working` -> `running/finished` with a pull request), for tests and offline demos.
- Config from environment or `twinlab.local.yaml` (Git-ignored): `DEVIN_API_KEY`, `DEVIN_ORG_ID`,
  `DEVIN_BASE_URL`, `GITHUB_REPO` (`owner/name`).

## WP-F: QTT sample (the black box)

Owns: `reference/qtt-sample/`, `automations/uipath-qtt-*/`, `automations/python-qtt-*/`,
`flows/qtt-*.yaml`, `tools/record_har.py`.

A realistic stand-in for QTT built in a different style from the showcase (server-rendered pages
with some fetch-based API calls), so the factory is exercised on something it has no source for.
Plus: a UiPath project and a Python API automation against it, a flow file for a screen no
automation covers, and a Playwright script that records HAR files of each flow into
`evidence/qtt/` (Git-ignored) and exports its DDL to `evidence/qtt/ddl.sql`.

## Integrator (main thread)

`labctl/conformance.py` (T-07), `labctl/lab.py` (compose up/down per app), the portal
(`lab/portal/`), wiring of all CLI groups, the per-app pipeline, the end-to-end run, docs.
