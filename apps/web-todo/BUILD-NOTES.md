# Web Todo ? recorded public application replica

## What was built

A functional React 16.7.0 reconstruction of the public TodoMVC testing demo at
https://demo.playwright.dev/todomvc/ . Implemented add, complete, toggle-all, All/Active/Completed
filters, edit by double-click, Enter save, Escape cancel, delete, clear completed and persistence.
Task data stays in localStorage, as on the observed application. All test tasks are synthetic.

## Twin Lab pipeline actually used

1. `labctl.recorder.record` drove a fresh Chromium browser against the live public website.
   The initial recording contains 17 log entries, DOM snapshots, screenshots and a HAR under
   `evidence/web-todo/public-demo/` (ignored by Git).
2. `factory.generate` consumed that recording and a requested flow in an isolated staging root.
   It generated BRIEF.md, model.yaml, pages, behaviours.json, db/schema.sql and app.yaml.
   requested-flow.yaml preserves the scope input without changing shared flows or automations.
3. The brief detected React 16.7.0 from the recorded bundle. React versions are pinned exactly.
   The application logic was implemented here; no external AI builder was invoked.
4. Recorded CSS is reused with upstream credits and licenses. The app's JavaScript was reconstructed,
   not copied from the original bundle. A Docker image hosts local assets and FastAPI health checks.
5. verify.py runs the same explicit actions against both applications and uses Twin Lab compare_dom
   on the task area at 13 checkpoints. Business state, completion flags and remaining counts match;
   all 13 DOM comparisons have zero differences. External requests are blocked for the replica.

## Recorder limitation discovered

The current Twin Lab recorder does not capture Enter-key submissions or semantic double-clicks.
Its generic selectors (for example `a`) also do not distinguish every filter. The 17-event log alone
is therefore NOT a complete replay program. Verification uses the explicit action sequence in
verify.py, with independent expected titles and counts, rather than claiming automatic fidelity
replay passed. No recorder code or shared checks were changed to conceal this gap.

## Assumptions and brief limitations

- No server-side task API or database schema was observed. The generated schema is empty.
  FastAPI is the brief's default hosting choice, not a claim about the original server.
- SQLite is checked through DATABASE_URL by /api/health and the unchanged schema is applied.
  It does not store tasks. The generated brief's description of SQLite as a proxied server is
  inaccurate here; browser tasks cannot be controlled by lab database packs or database latency.
- Branding banner, footer, attribution page and document title deliberately identify the local copy.
  Task-area structure and selectors match; full-page pixel identity is not claimed.
- Blank input, Escape, empty-edit deletion and toggle-all were additionally exercised on the live
  original during verification. Rules retain hypothesis status; no owner approval is inferred.
- This is a browser-only example, not validation of enterprise login, backend reconstruction or
  database-backed applications. The next trial should include a public demo with a real API.

## Validation

- Docker image twinlab/web-todo:dev built and runs on localhost port 8736.
- 13 paired business-state checkpoints and Twin Lab DOM comparisons passed.
- No JavaScript page errors, no external runtime resources, and no mobile horizontal overflow.
- Screenshots: validation/source.png and validation/replica.png; replica visually inspected.
- Contract validation: 27 files checked, zero problems at time of build.
- All rule verification levels remain hypothesis.

## Run

From the repository root:

```powershell
docker build -t twinlab/web-todo:dev apps/web-todo
docker run --rm -p 127.0.0.1:8736:8000 twinlab/web-todo:dev
```

Open http://127.0.0.1:8736/todomvc/ . No sign-in is needed.

```powershell
$env:PYTHONPATH = 'lab/labctl'
python apps/web-todo/verify.py
```

Use a fresh browser context or clear this origin's localStorage to reset tasks. The current demo
container is named twinlab-web-todo-review; stop it with `docker stop twinlab-web-todo-review`.

## Platform fixes required

See [Replication gaps and fix plan](../../docs/replication-gaps.md). The first gate is complete
semantic capture and automatic replay of this flow without supplemental handwritten actions
(R01-R04), followed by state, outcome and visual acceptance checks.
