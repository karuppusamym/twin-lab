# Certification: which technologies are proven

Twin Lab has detection rules for about 30 web stacks and several desktop toolkits. A rule in the
code is not proof. The certification matrix says, per technology, what was run on a real
application and what came out. The current state is in
[certification-matrix.md](certification-matrix.md), written by `labctl certify`.

Code: `lab/labctl/labctl/certify.py`. Matrix: `certification/matrix.yaml`. Results:
`certification/results/<id>.json`. Flows: `certification/flows/`, or `flow.py` in the reference
application's own folder (`reference/web/`, `reference/desktop/`).

## The gates

Every entry is a technology and a version on a small reference application. Every entry goes
through the same four gates.

| Gate | Passes when | How it is checked |
|---|---|---|
| detect | The technology and version detected from the recording equal what the entry declares. | Web: `model_build.from_recording`, the same call the brief uses to fill `stack.frontend`. Desktop: the `technology` in `desktop.json`. Every key in `gates.detect` is compared, a nested one (`server`, `runtime`) key by key. `version` is always compared: an entry with no version passes only when none was detected. A declared version may be coarser than the detected one (`6` and `6.0.36`). |
| capture | A scripted flow was recorded with the real recorder, and every expected action is in the recording, in order. | The expected actions are written by hand from the flow, not from a recording. They are compared with `fidelity.missing_actions`, as `labctl behaviour fidelity --expect` does. The order counts: an action recorded before the one the flow made first is reported as missing. |
| self-replay | The recording replays against a fresh instance of the same application with no failed, blocked or skipped step. | Web: `fidelity.replay` with an origin map from the recorded instance to the fresh one. Desktop: `desktop.verify.verify` or `desktop.java.verify`. Desktop verify marks a focus change and a menu or list opening as covered by the steps around them; those are counted apart (`implied`). A step it could not perform (a selection that holds data, for one) fails the gate, although verify's own status stays `pass`. |
| rebuild | A coding agent rebuilt the application from the package alone, and the replay against the rebuilt copy ended `VERIFIED COMPLETE`. | Never run by `labctl certify`. It reads the kept replay report the entry names in `gates.rebuild.report`. |

A gate is `PASS`, `FAIL` or `NOT RUN`. A gate that did not run has a reason and is never counted
as passed.

## The result

| Result | Meaning |
|---|---|
| `CERTIFIED` | All four gates passed. |
| `CAPTURE VERIFIED` | Detect, capture and self-replay passed. Rebuild was not run. |
| `PARTIAL` | A gate failed, or only some of the gates ran. The result names each one and why. |
| `NOT RUN` | Detect, capture and self-replay did not run. The reason is given. |

### What CAPTURE VERIFIED means

It means three things, each on the one flow the entry runs, on this reference application:

- the lab named the technology and the version the entry declares, from the recording alone;
- every action the flow performs is in the recording, in the flow's order;
- the recording, with the entry's synthetic values, drove a second, fresh instance of the same
  application through every step, and what each step showed was what the recording holds.

It does not mean:

- that a copy built from the package is right. No copy was built; that is the rebuild gate, and
  no entry has passed it yet;
- that the comparison would catch a wrong copy. An application compared with itself agrees
  with itself. (The applications' own tests replay against a deliberately wrong instance:
  `test_reference_jsf.py`, `test_desktop_gui.py`.);
- that another version, another control or another flow of that technology works. Each entry
  lists what it does not cover;
- that the application's acceptance cases passed. `labctl certify` replays without them;
  `test_reference_jsf.py` runs the ones the JSF flow declares.

## Running it

```bash
labctl certify                      # every entry
labctl certify --only react-16      # one entry (repeat --only for more)
labctl certify --keep some/folder   # keep the recordings to look at
```

Each run writes one JSON file per entry it ran and regenerates
`docs/certification-matrix.md` from all kept results. An entry that has no result file is shown
as `NOT RUN (never run)`. The command exits 1 when a gate that ran failed. Commit the results
and the table together.

Web entries start the reference application twice on free ports of 127.0.0.1: once to record,
once fresh for the replay. `labctl certify` downloads nothing. Typed values are never stored:
the recording keeps shapes, and the replay types the synthetic values the entry gives.

### How each entry is started

| Entry | Application | Started by | Needs |
|---|---|---|---|
| `html-forms` | the multi-application fixture (`lab/labctl/tests/fixtures/multiapp`) | its `apps.serve()`: three Python servers | nothing else |
| `react-16` | `apps/web-todo/static` | the harness's own file server | `vendor/react.js` and `react-dom.js`, built from `node_modules` by `build.cjs`, not in Git. Absent (as in CI): `NOT RUN` with that reason |
| `angular` | Caseboard, `reference/web/caseboard-angular` | the application's `serve.serve()`, which answers a route's address (`/cases/CB-1007`) with `index.html`; a plain file server would answer 404 | the committed production build `site/`. No npm |
| `jsf-mojarra` | Caseboard, `reference/web/caseboard-jsf` | the application's `run.serve()`: compiles with `javac` when needed and starts a Java process (embedded Jetty) per instance; `stop()` ends the process tree | a JDK 11 or newer, and the 14 jars `python reference/web/caseboard-jsf/fetch_libs.py` downloads from Maven Central into `lib/` (not in Git; each checked against a pinned SHA-1). Without either, as in CI: `NOT RUN` with the reason `build.unavailable()` gives |
| `winforms`, `wpf` | CaseDesk, CaseDeskWpf (`reference/desktop/`) | `flow.build()` (`dotnet build -c Release`), then the lab's desktop recorder starts the program and runs `flow.flow` | Windows, the desktop extra, the .NET SDK, `TWINLAB_DESKTOP_GUI=1` |
| `java-swing` | CaseDeskSwing | `flow.build()` (`javac`), then `desktop.java.record` with the Access Bridge loaded for that run only | Windows, the desktop extra, a JDK at `TWINLAB_JAVA_HOME` (default: JDK 11), `TWINLAB_DESKTOP_GUI=1` |

The two Caseboard applications load their own files under their own names
(`caseboard_angular_flow`, `caseboard_jsf_run`); the entry's `app.namespace` makes the harness
use the same names, so a file is never loaded twice under two names.

Desktop entries start a GUI application and click with the real mouse, and the WPF flow types a
password with real key presses. Nothing else may use the mouse or the keyboard while one runs,
and two never run at once. They run only on Windows with `TWINLAB_DESKTOP_GUI=1`. Without it
they are `NOT RUN (set TWINLAB_DESKTOP_GUI=1)`. A missing application folder is `NOT RUN
(reference app not present)`.

## Adding a technology

1. Choose a small real application in that technology and version. Put it in the repository,
   or name the files that must be built first in `app.requires`.
2. Write the flow as a Python file in `certification/flows/` with a function `run(page)`. It
   drives the page as a person would. Use synthetic values only.
3. Write the expected actions by hand from the flow, in order, using the recorder's names:
   `type`, `click`, `dblclick`, `select`, `check`, `uncheck`, `submit`, `keypress:Enter`,
   `goto:reload`, `tab-open`. Leave out page loads and what a click causes.
4. Add the entry to `certification/matrix.yaml`:

   ```yaml
   - id: vue-3
     technology: Vue
     version: "3.4.21"
     surface: web
     app: {kind: static, folder: certification/apps/vue-todo, start: /index.html}
     flow: {file: certification/flows/vue_todo.py, run: run}
     gates:
       detect: {framework: vue, version: "3.4.21", rendering: client}
       capture: {expected: [type, keypress:Enter, check, click]}
       self-replay:
         values: {input.new-todo: [Syn task one]}
       rebuild: {}
     not_covered: [a router, a data API]
   ```

   `app.kind` is `static` (a folder served as files), `python` (a file with a function that
   returns running servers; see the `html-forms` entry), `module` (the application's own
   starter; see below) or, for desktop, `winforms`, `wpf` or `swing` (a folder with `flow.py`
   exposing `build()` and `flow`).

   An application that needs more than a file server brings its own starter and flow:

   ```yaml
   app:
     kind: module
     file: reference/web/caseboard-jsf/run.py      # serve() returns .base and .stop()
     namespace: caseboard_jsf                      # its files load as caseboard_jsf_<name>
     start: /login.xhtml
     unavailable: reference/web/caseboard-jsf/build.py:unavailable  # why it cannot start, or None
   flow: {file: reference/web/caseboard-jsf/flow.py, run: flow, base_url: true}  # flow(page, base_url)
   gates:
     capture: {expected_file: reference/web/caseboard-jsf/expected-actions.yaml}  # its `actions`
     self-replay: {bindings_from: bindings_for}
   ```

   The synthetic values of a replay are given in one of two ways. `values` (and `records`)
   in the entry: per field, the values in order; `records` is the title of each record a step
   acts on that was there before the recording started (a click on the row of case CB-1007),
   in order. Or `bindings_from`: a function of the flow file, called with the numbers of the
   recording's `type` and `select` steps, that returns the whole `bindings.yaml`. A flow whose
   values do not fit the recording fails self-replay.
5. Run `labctl certify --only <id>` and commit the result and the table. If a gate fails, the
   failure stays in the table until the cause is fixed. Do not change the expected actions to
   match the recording.
6. After a coding agent rebuilt the application and its replay report is kept in the
   repository, name it in `gates.rebuild.report` and run the entry again.

An entry can be declared before its application exists: give `gates.detect`, set `app` and
`flow` to null and give the reason in `not_run`. Vue and ASP.NET Web Forms are declared this way
now: no reference application has been built for either yet.

## What the matrix does not say

- A pass covers the flow that was run, not every control the technology has. Each entry lists
  what it does not cover.
- Self-replay compares an application with itself. It proves the recorder and the comparison
  understand that technology's pages. It does not prove a rebuilt copy is right; that is the
  rebuild gate.
- The rebuild gate trusts the report it is given. It checks the status and the assurance, not
  which technology the copy was built in.
- Server technology (Java, .NET, PHP) is compared only when the entry declares `server` under
  `gates.detect`. The JSF entry does (Java, JSF 2.3, Mojarra); the implementation's release
  (2.3.9) is in no page or header and is not compared.
- A desktop run depends on timing: the recorder reads windows by polling. Where a run differed
  from the others (an event recorded out of order, a click missed), the entry says so under
  *Not covered*, and the table shows the last run as it ended.
