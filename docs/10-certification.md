# Certification: which technologies are proven

Twin Lab has detection rules for about 30 web stacks and several desktop toolkits. A rule in the
code is not proof. The certification matrix says, per technology, what was run on a real
application and what came out. The current state is in
[certification-matrix.md](certification-matrix.md), written by `labctl certify`.

Code: `lab/labctl/labctl/certify.py`. Matrix: `certification/matrix.yaml`. Results:
`certification/results/<id>.json`. Flows: `certification/flows/`.

## The gates

Every entry is a technology and a version on a small reference application. Every entry goes
through the same four gates.

| Gate | Passes when | How it is checked |
|---|---|---|
| detect | The technology and version detected from the recording equal what the entry declares. | Web: `model_build.from_recording`, the same call the brief uses to fill `stack.frontend`. Desktop: the `technology` in `desktop.json`. Every key in `gates.detect` is compared. `version` is always compared: an entry with no version passes only when none was detected. A declared version may be coarser than the detected one (`6` and `6.0.36`). |
| capture | A scripted flow was recorded with the real recorder, and every expected action is in the recording, in order. | The expected actions are written by hand from the flow, not from a recording. They are compared with `fidelity.missing_actions`, as `labctl behaviour fidelity --expect` does. |
| self-replay | The recording replays against a fresh instance of the same application with no failed, blocked or skipped step. | Web: `fidelity.replay` with an origin map from the recorded instance to the fresh one. Desktop: `desktop.verify.verify` or `desktop.java.verify`. |
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
once fresh for the replay. Nothing is downloaded. Typed values are never stored: the recording
keeps shapes, and the replay types the synthetic values in `gates.self-replay.values`.

Desktop entries start a GUI application and click with the real mouse. They run only on Windows
with `TWINLAB_DESKTOP_GUI=1`. Without it they are `NOT RUN (set TWINLAB_DESKTOP_GUI=1)`. A
missing application folder is `NOT RUN (reference app not present)`.

The React entry needs `apps/web-todo/static/vendor/react.js` and `react-dom.js`. They are built
from `node_modules` by `build.cjs` and are not in Git. Where they are absent, as in CI, the
entry is `NOT RUN` with that reason.

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
   returns running servers; see the `html-forms` entry) or, for desktop, `winforms`, `wpf` or
   `swing` (a folder with `flow.py` exposing `build()` and `flow`).
5. Run `labctl certify --only <id>` and commit the result and the table. If a gate fails, the
   failure stays in the table until the cause is fixed. Do not change the expected actions to
   match the recording.
6. After a coding agent rebuilt the application and its replay report is kept in the
   repository, name it in `gates.rebuild.report` and run the entry again.

An entry can be declared before its application exists: give `gates.detect`, set `app` and
`flow` to null and give the reason in `not_run`. Angular, Vue, JSF / PrimeFaces and ASP.NET Web
Forms are declared this way now.

## What the matrix does not say

- A pass covers the flow that was run, not every control the technology has. Each entry lists
  what it does not cover.
- Self-replay compares an application with itself. It proves the recorder and the comparison
  understand that technology's pages. It does not prove a rebuilt copy is right; that is the
  rebuild gate.
- The rebuild gate trusts the report it is given. It checks the status and the assurance, not
  which technology the copy was built in.
- Server technology (Java, .NET, PHP) is compared only when the entry declares `server` under
  `gates.detect`. No entry that ran declares one yet.
