# Caseboard (Angular): the reference application for Angular

A small real Angular application, with synthetic data only, that Twin Lab's certification matrix
uses to prove three things about Angular: the lab **detects** it, the recorder **captures** a
scripted flow in full, and the recording **replays** against a fresh instance of the same
application with no failed step.

It is not a demo copy of anything and holds no real data: twelve invented cases, one invented
password (`demo`).

## What is in it

| Business behaviour | Where |
|---|---|
| Sign-in form (user name, password); a wrong password shows a message; any user name with the password `demo` signs in | `src/app/sign-in.ts`, `src/app/auth.ts` |
| Route guard: a signed-out user is sent to `/sign-in` | `src/app/auth.guard.ts` |
| Routed list (`/cases`): a search box that filters the table as text is entered, paging (5 of 12 per page) | `src/app/case-list.ts` |
| Routed edit form (`/cases/:id`), **lazy-loaded**, Angular reactive forms: required fields with messages, a `<select>`, a check box, radio buttons, a date input, a text area; Save returns to the list with a confirmation and the saved values | `src/app/case-edit.ts` |
| A modal dialog (sign-out confirmation), closed by its buttons or Escape | `src/app/app.ts` |
| State in memory (`src/app/case-store.ts`); the signed-in user name also in `sessionStorage` | |

Inputs and buttons have stable ids. Nothing is requested from another host at run time: no CDN,
no fonts, no analytics (`test_reference_angular.py` checks the recording's requests).

## Versions

| | |
|---|---|
| Angular (`@angular/core`, `common`, `forms`, `router`, `platform-browser`) | 21.2.26 |
| Angular CLI and `@angular/build` | 21.2.30 |
| TypeScript | 5.9.3 |
| RxJS | 7.8.2 |
| Node used for the build | 24.3.0 (npm 11.4.2) |

Angular 21 is the newest major that runs on the Node 24 release the build machine had (24.3.0):
Angular 22 asks for Node 24.15 or later. The application is zoneless (Angular 21's default),
uses standalone components and signals, and the production build has hashed file names.

## Serve it (no npm needed)

`site/` is the committed production build. `serve.py` needs only Python:

```bash
python reference/web/caseboard-angular/serve.py --port 4210
```

It binds 127.0.0.1, answers an address that names no file with `index.html` (client-side
routes), and serves nothing from a cache. Open <http://127.0.0.1:4210/> and sign in with any
user name and the password `demo`.

## Rebuild it

Only when the source changes. Packages come from the public npm registry.

```bash
cd reference/web/caseboard-angular
npm ci
npm run site
```

`npm run site` builds for production (`dist/`, ignored by Git) and copies the result into
`site/`, with `3rdpartylicenses.txt`. Commit `site/` with the source: the tests and the
certification run on the committed build. If `~/.npmrc` forces another platform, say which one
you are on (`npm ci --os=win32 --cpu=x64`).

## The three gates

```bash
python reference/web/caseboard-angular/certify_local.py
```

Needs `labctl` (this repository's `lab/labctl`), Playwright and Chromium. It serves `site/`,
records `flow.py` with the lab's recorder, runs the lab's detection on the recording, checks
`expected-actions.yaml` against what was captured, and replays the recording against a second,
fresh instance. `--keep <folder>` keeps the recording and the replay report. The same gates run
in `lab/labctl/tests/test_reference_angular.py`.

- `flow.py`: `flow(page, base_url)`, the scripted flow, and the values it types (`INPUTS`,
  `RECORDS`), from which the replay's `bindings.yaml` is written. A recording never holds a
  typed value.
- `expected-actions.yaml`: the semantic actions the flow performs, written from the flow.

## What this does not prove

- Angular only as built here: one version, zoneless, the default (emulated) view encapsulation,
  the `application` builder, no server-side rendering or hydration, no Angular Material or CDK
  overlay, no `HttpClient` call (state is in memory, so no request is replayed), no service
  worker, no internationalisation.
- The replay opens the address the route guard led to (`/sign-in`): the recorder writes a page
  load with the address the page ended at, as it does for a server's redirect. That the guard
  redirects from `/` is seen in the recording, not checked again by the replay.
- A self-replay compares the application with itself. It shows that the recording is complete
  and replayable, not that the lab would tell a wrong replica from a right one.
- The flow clicks like a person (the mouse button is held for 80 ms). A script that presses and
  releases in the same instant is snapshotted before Angular has rendered what leaving a field
  changes; see `_click` in `flow.py`.
