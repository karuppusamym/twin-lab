# Lab agent

The front door of one demo app. One instance runs beside each app; the app itself carries no lab
code. Contract: `docs/dev/integration.md` (WP-B), `docs/03-contracts.md` (condition, session).

```
browser / robot ──► :LAB_PORT  lab agent ──► APP_UPSTREAM (demo app) ──► :DB_PROXY_PORT ──► DB_UPSTREAM
                       │ /_lab/* admin API                                (DB link proxy, in the agent)
                       └── ADMIN_DATABASE_URL (direct, never through the DB link)
```

- **HTTP proxy.** Everything not under `/_lab/` goes to the app: method, path as sent, query,
  headers (hop-by-hop ones dropped; `X-Forwarded-For/-Proto/-Host` added; `Host` kept), body,
  status, response headers and every `Set-Cookie`. Redirects are passed back, never followed.
  Response bodies stream through undecoded. Each request is recorded as `{ts, app, method, path,
  status, app_ms, injected_ms, fault}`; `app_ms` is time spent waiting on the app (headers and
  body), `injected_ms` the delay the condition added.
- **DB link proxy.** Raw TCP to `DB_UPSTREAM`: any Postgres driver works, TLS included (the
  SSLRequest and handshake are just bytes). Under a condition with `db`, every server-to-client
  chunk is held back `latency_ms ± jitter_ms` (uniform, never below 0), in order, so each round
  trip gets slower by about the latency. Changing the condition applies to open connections at once.
- **Data, baseline, reset, sessions** through `ADMIN_DATABASE_URL`. Postgres, SQL Server and
  Oracle: the same checks before loading (types, missing keys, missing parents), children deleted
  first, explicit ids loaded into identity columns and the counters moved past them. The DB link
  proxy is raw TCP, so it works for TDS and Oracle Net as well. The SQL statement log in sessions
  is Postgres only.

## Run

```bash
# from the repository root
docker build -f lab/lab-agent/Dockerfile -t twinlab/lab-agent:dev .
docker run --rm -p 8720:9000 \
  -e APP_ID=showcase -e APP_UPSTREAM=http://app:8000 -e APP_HEALTH_PATH=/api/health \
  -e DB_UPSTREAM=db:5432 -e ADMIN_DATABASE_URL=postgresql://USER:PASSWORD@db:5432/showcase \
  -v showcase-lab:/var/lib/twinlab twinlab/lab-agent:dev
```

Point the app's `DATABASE_URL` at the agent (`postgresql://USER:PASSWORD@<agent>:15432/<db>`).
`lab/lab-agent/tests/compose.yaml` is a complete example (Postgres, showcase, agent).

Without Docker: `pip install -r lab/lab-agent/requirements.txt`, then
`PYTHONPATH=lab/lab-agent:lab/labctl python -m lab_agent`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `APP_ID` | required | App slug, used in requests, sessions and condition profiles |
| `APP_UPSTREAM` | required | App base URL, e.g. `http://app:8000` |
| `STATE_DIR` | `/var/lib/twinlab` in the image | Condition, baseline, sessions (mount a volume) |
| `LAB_PORT` | `9000` | Front door and admin API |
| `DB_UPSTREAM` | none | `host:port` of the database; no DB link proxy without it |
| `DB_PROXY_PORT` | `15432` | DB link proxy port |
| `ADMIN_DATABASE_URL` | none | Direct URL for data, reset and sessions: `postgresql://…`, `mssql+pymssql://…` (SQL Server) or `oracle+oracledb://…/?service_name=…` (Oracle, thin mode, no client install) |
| `SCHEMA_PATH` | none | If set, only the tables (and views, which the demo database holds as tables) this DDL creates are read, loaded and reset |
| `LAB_TOKEN` | none | When set, every `/_lab/*` call needs exactly one header `X-Lab-Token` (`labctl` sets it; see `.lab-state/lab-token`) |
| `APP_VERSION` | `dev` | Written into sessions (`apps[].version`) |
| `APP_HEALTH_PATH` | `/` | App URL `/_lab/health` checks (ok when status < 500) |
| `DB_SCHEMA` | `public` | Database schema the tables live in |
| `UPSTREAM_TIMEOUT_S` | `300` | Read timeout towards the app (then 504) |
| `LOG_LEVEL`, `ACCESS_LOG` | `INFO`, `0` | Logging |

## Admin API

All replies are JSON. Errors are `{"detail": {"code": "LAB_...", "message": ...}}`.

| Call | Body | Reply |
|---|---|---|
| `GET /_lab/health` | | `{app: "ok"\|"down", db: "ok"\|"down"}`, 200 only if both ok, else 503 |
| `GET /_lab/info` | | `{app, version, upstream, tables: [{name, primary_key, columns: [{name, type, nullable, max_length, default, identity, primary_key, references}]}], db_error, condition, baseline: {id, loaded_at, label, pack, kind}, session, db_proxy}` |
| `GET /_lab/data[?tables=a,b]` | | `Rows`: `{table: [row, ...]}`, rows ordered by primary key |
| `POST /_lab/data/load` | `{rows: Rows, label?, pack?}` | `{baseline, replaced, cleared, counts, triggers, id}`; 422 `LAB_DATA_INVALID` with `problems: [{table, row, column, message}]` (row is 1-based) |
| `POST /_lab/data/baseline` | `{label?, pack?}` | `{baseline, counts}`: the current data becomes the baseline |
| `GET /_lab/data/baseline` | | `{baseline, tables}`: the rows Reset puts back (`tables` null without a baseline); a lab copy starts from them |
| `GET /_lab/baselines` | | every kept baseline, newest first: `[{id, loaded_at, label, pack, kind, pinned, counts, current}]` |
| `POST /_lab/baselines/{id}/restore` | | puts that baseline's rows back and makes it current; 404 `LAB_BASELINE_NOT_FOUND`, 409 `LAB_BASELINE_MISMATCH` |
| `POST /_lab/baselines/{id}/pin` | `{pinned: true\|false}` | a pinned baseline is never pruned (the newest 20 unpinned are kept) |
| `POST /_lab/baselines/{id}/quarantine` | `{reason}` or `{release: true}` | a quarantined baseline cannot be restored (409 `LAB_BASELINE_QUARANTINED`), reset refuses while the current one is quarantined; kept, with its reason |
| `GET /_lab/lease` | | `{lease: {holder, purpose, since, until} \| null}` |
| `POST /_lab/lease` | `{holder, minutes (1-480), purpose?}` | reserve or extend; 409 `LAB_LEASED` when someone else holds it |
| `DELETE /_lab/lease` | `{force?}` | release: the holder (header `X-Lab-Holder`), or anyone with `force: true` |
| `POST /_lab/reset` | | `{baseline, counts, cleared}`; 409 `LAB_NO_BASELINE` |
| `GET /_lab/condition` | | `{id, description, seed, http, db, faults, applied_at}` (`id: null` = none) |
| `PUT /_lab/condition` | condition or profile | as GET; 422 `LAB_CONDITION_INVALID`. `{}` clears |
| `DELETE /_lab/condition` | | clears it |
| `POST /_lab/condition/rewind` | | restart the seeded sequence |
| `POST /_lab/session/start` | `{automation?, label?}` | `{session_id, started}`; 409 if one is running |
| `POST /_lab/session/stop` | | a `labctl.models.Session` document |
| `POST /_lab/session/discard` | | drop the running session |
| `GET /_lab/sessions` | | `[{session_id, automation, label, started, ended, summary}]`, newest first |
| `GET /_lab/sessions/{id}` | | the stored `Session` |
| `GET /_lab/sessions/{id}/detail` | | `{label, changes: [{app, table, op, key, columns, before, after}], faults, tables_used: [{table, read, write}]}` |
| `GET /_lab/requests[?limit=100]` | | the last requests recorded (up to 5000 kept) |

### Conditions

`PUT /_lab/condition` takes the stored form

```json
{"id": "peak-hour", "seed": 7,
 "http":   [{"match": "GET /api/cases/search*", "latency_ms": {"p50": 4000, "p95": 15000}}],
 "db":     {"latency_ms": 150, "jitter_ms": 80},
 "faults": [{"match": "POST /api/cases/*/notes", "status": 503, "rate": 0.05}]}
```

or a whole profile (`conditions/<id>.yaml` as JSON, with `targets`), of which the target for
`APP_ID` is used, or else the target for `app: any`. The condition is kept in `STATE_DIR/condition.json` and survives restarts.

- `match`: `"METHOD /path-glob"`, method optional (`"/api/*"`, `"*"`). `*` matches anything,
  `/` included. Matched against the path, or `path?query` when the glob contains `?`.
- `http`: the first matching rule adds a delay from a lognormal with that median and 95th
  percentile (`mu = ln p50`, `sigma = (ln p95 - ln p50) / 1.645`), capped at 10 x p95.
  `p50 == p95` gives a fixed delay.
- `faults`: the first matching rule, at `rate`, answers `status` with
  `{"detail": {"code": "LAB_INJECTED_FAULT", "message": ...}}` and `X-Twinlab-Fault: 1` without
  calling the app (after any `http` delay for the same request).
- `seed`: each request's draws come from a generator seeded by (seed, method, path, how many times
  that method and path were seen). The same request sequence gives the same delays and faults,
  even when other routes interleave differently. The count restarts on `PUT`, on
  `/condition/rewind` and on session start. Without a seed the draws are random.

### Load and reset

One transaction, all or nothing:

1. Every value is checked against the column (types, NOT NULL, text length), primary keys for
   duplicates, foreign keys against the rows given, the tables being cleared, or the database.
2. The tables given, plus every table that references them (directly or through others) and is
   not given, are emptied (`TRUNCATE`; `DELETE` if a lock blocks it for 5 s). The cleared
   tables are listed in the reply.
3. Rows go in parents first (self-references ordered too). Generated columns are skipped;
   `GENERATED ALWAYS` identities get `OVERRIDING SYSTEM VALUE`.
4. Every identity or serial sequence of those tables moves past `MAX(column)`.
5. The whole database (every table) is read back in the same transaction; after commit it is
   stored as the baseline in `STATE_DIR/baseline.rows.json` (meta in `baseline.meta.json`).
   Both files carry the same baseline id; `reset` refuses with 409 `LAB_BASELINE_MISMATCH` when
   they disagree. A rows file from an older agent (no id) is stamped with the meta's id once, at
   startup.

When the admin role may, triggers (and FK triggers, already checked in step 1) are off during
the load (`session_replication_role = replica`, reported as `triggers: "disabled"`), so app
triggers cannot add rows a pack did not ask for.

`reset` is the same with the baseline as the rows: it restores every table of the baseline row for
row, and the sequences, so the same actions afterwards produce the same ids.

### Values

JSON-safe both ways: dates, times and timestamps as ISO 8601 (`timestamptz` read in UTC with
`+00:00`; a naive one loaded into `timestamptz` is UTC), **numeric always as a string**
(`"12.50"`; numbers are accepted on load), bytea as base64, uuid and other types as strings,
json/jsonb as JSON, intervals exactly as `"<days> days <seconds> seconds"` (e.g.
`"30 days 1.5 seconds"`), arrays as lists.

A `/_lab/*` request with a body must say `Content-Type: application/json`; anything else gets
415 `LAB_JSON_REQUIRED`, so a browser form on another site cannot drive the agent.

### Sessions

`start` snapshots every table (one repeatable-read transaction) and starts recording requests;
`stop` snapshots again and diffs by primary key: `insert`, `update` (changed columns, before and
after, in `/detail`), `delete`. A table without a primary key is compared as a multiset of whole
rows; its key is `row:<hash>`. A change's `key` is the primary key value (composite keys joined
with `,`). Summary: `requests`, `wall_s`, `waiting_on_app_s` (sum of `app_ms + injected_ms`),
`errors` (status >= 500 or an injected fault). A running session is kept on disk under
`STATE_DIR/active/` and resumes after a restart; finished ones are `STATE_DIR/sessions/<id>.json`.

While a session runs, the DB link reads the SQL text of each statement clients send (simple
queries and Parse messages; never parameter values, and nothing on a TLS connection). It keeps
only the table names, with read or write, as `tables_used` in `/detail`. Tables outside the
app's schema file and system catalogs are left out. This list is in memory, so a session resumed
after a restart lists only what it saw since.

### Reservations (leases)

While a lease is held, every changing admin call (`POST`, `PUT`, `DELETE` other than the lease
itself) must carry `X-Lab-Holder` with the holder's name (compared ignoring case and spacing),
or it gets 423 `LAB_LEASED` naming the holder, until when and why. Reads always pass. `labctl`
sends the holder from `TWINLAB_HOLDER` or the login name; the portal sends the name a person
typed. An expired lease holds nothing. The lease is `STATE_DIR/lease.json`.

### Baseline history

Every load and every "current data becomes the baseline" is kept as
`STATE_DIR/baselines/<id>.json`: the newest 20 unpinned ones, the current one, and every pinned
one. Restoring one replaces the data with its rows and makes it the current baseline, so Reset
returns to it. A current baseline written by an older agent is added to the history at startup.

## Tests

```bash
python -m pytest -q lab/lab-agent/tests                                     # all
python -m pytest -q -s lab/lab-agent/tests/test_docker_integration.py       # Docker only, timings
```

The Docker tests skip when Docker is not available (or `TL_SKIP_DOCKER=1`), build
`twinlab/lab-agent:dev` (and `twinlab/showcase:dev` if missing), use host ports 18210 and 18211,
and remove their containers and volumes afterwards. They need `cryptography` (a throwaway TLS
certificate for Postgres), `psycopg` and `asyncpg` on the host.

## Limits

- HTTP/1.1 only. WebSocket upgrades are refused (closed with 1011). Request bodies are read whole
  before forwarding (responses stream).
- Latency is added before the app is called, not spread over the response.
- The DB link delays server-to-client traffic only, and does not drop or reset connections.
- Postgres only for data, reset and sessions. Snapshots and baselines are held in memory and as
  JSON files: fine for demo-sized data (tens of thousands of rows), not for millions.
- Unique constraints other than the primary key, and check constraints, are left to Postgres; a
  violation is reported with the table and column but no row number.
- Tables are read from one schema (`DB_SCHEMA`). Foreign keys to other schemas are not followed.
- One running session per agent. Two people on the same app share its data and condition.
