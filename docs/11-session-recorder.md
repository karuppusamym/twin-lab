# One recorder across applications

Status: **first delivery built** (2026-11-19): the workstation session in Twin Lab
(`lab/labctl/labctl/workstation/`), and the portable recorder and the portal references in
TwinLab2 (`twin-lab2/agent/`, `twin-lab2/portal.py`). What is built, what is not, and how it was
checked is in *Delivery* at the end. The design was reviewed once by a second model before it was
built; the review's corrections are folded in here, and its cuts are listed under *Later*.

A UiPath (or any RPA) process rarely stays in one application: it looks a customer up in one
application, types the number into a web portal, files the result in a WinForms client. To
rebuild the applications such a process touches, Twin Lab records the whole session in one go,
on the person's own PC: every action in the applications that will be replicated, in order, and
only the fact that other applications were used.

## Decisions

| Decision | Rule |
|---|---|
| Where it runs | A **portable program** (`TwinLabRecorder.exe`, one folder) copied to the PC and run as a normal user: no installer, no administrator rights |
| What leaves the PC | **References only**: the session, the applications seen, and per application a flow of filtered step labels. Recordings stay on the PC. Upload to GitHub or S3 is a later item |
| Portal | TwinLab2's portal (on OpenShift): machines, sessions, applications, recordings, automations. It maps each recording to its applications |
| New applications | Registered in the portal as `discovered` when a session recorded them, and the recording is mapped to them |
| Capture detail | Per application: **full** for the session's targets, **steps** for applications opted in, **presence** for everything else; a deny list is always presence |
| Mainframe | A TN3270 proxy in the session: the terminal (UiPath's direct connection, an emulator) connects to `127.0.0.1:23270`, the proxy forwards to the host (*Mainframe*) |
| Out of scope | Citrix, remote desktop |

## What exists in Twin Lab, and what the session adds

| Piece | Code | What it does |
|---|---|---|
| Web recorder | `labctl/recorder.py` (`record`, `attach`) | Playwright / Chrome DevTools protocol; numbers steps on arrival, then reorders and renumbers them once at `close()` (double clicks, tab steps); follows every tab |
| Desktop recorder | `labctl/desktop/recorder.py` (`record`, `attach`) | UI Automation events and low-level hooks; a step is written after its batch settles (0.4 s, up to 0.8 s for a click's pattern event), with the time of its input |
| Identity facts | `labctl/desktop/win.py` (`exe_info`, `signals`), `labctl/desktop/detect.py` | version resource of the executable (product, company, versions); toolkit |
| Split | `labctl/split.py` (web, by origin), `labctl/desktop/split.py` (desktop, by process; also `interleave`) | one part per application, `global_seq` kept |
| Build all | `labctl/multiapp.py` | web recordings only |

The session adds the layer above the two recorders: one clock (they run in one process), one
decision per application about how much to record, and one timeline.

## The workstation session (`labctl.workstation`)

```
 TwinLabRecorder.exe (one process)
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ Session                                                                     │
 │  foreground watcher (250 ms) ── identity ── level ──┬─ presence row → journal│
 │                                                    ├─ desktop: add pid ──────┼─► desktop/  (today's format)
 │                                                    └─ our Edge window: web ──┼─► web/      (today's format)
 │  journal.jsonl (per event, flushed)                                          │
 │  at stop: finalise each recorder → merge by time → session.jsonl, outbox     │
 └─────────────────────────────────────────────────────────────────────────────┘
```

### Identity

| Surface | Key | Kept as facts, not identity |
|---|---|---|
| desktop | `desktop:<company>/<product>` from the version resource; `desktop:exe/<name>` when it has none | executable name (alias), version, toolkit |
| web | `web:<origin>` (scheme, host, port) | — |

- **Hosts** (`java.exe`, `javaw.exe`, `electron.exe`, `python.exe`, `pythonw.exe`, `dotnet.exe`,
  `node.exe`, `ApplicationFrameHost.exe`, `msedgewebview2.exe`, `rundll32.exe`, `dllhost.exe`)
  run many applications under one name: they are recorded as presence and never registered,
  unless a target names the host explicitly.
- **Browsers** are never applications. The only browser windows recorded beyond presence are
  those of the Edge the session launched itself; in it, each origin is an application. A browser
  the person opened is presence.
- A renamed executable keeps its key when its company and product stay; the portal can merge two
  discovered applications into one.

### Levels and the deny list

| Level | For | Kept |
|---|---|---|
| **full** | the session's targets | everything `09-desktop.md` and `06-evidence.md` describe (trees, pictures without data; HAR and snapshots of that origin) |
| **steps** | applications named with level `steps` | the desktop step (action, selectors, window), **no tree file and no picture** |
| **presence** | every other application | executable name, identity key, time it came to the front; **no title, no class, no click, no key** |

The **deny list** is presence whatever a session says, and never registered: Windows itself
(any executable under `C:\Windows\`, `explorer.exe`, `SearchHost.exe`, `StartMenuExperienceHost.exe`,
`ShellExperienceHost.exe`, `LockApp.exe`, `Taskmgr.exe`, `SystemSettings.exe`), the recorder
itself, Adobe (`Acrobat.exe`, `AcroRd32.exe`, `AcroCEF.exe`, `Adobe*.exe`), Outlook and Teams
(`OUTLOOK.EXE`, `olk.exe`, `ms-teams.exe`, `msteams.exe`, `Teams.exe`), password managers
(`1Password.exe`, `KeePass.exe`, `KeePassXC.exe`, `Bitwarden.exe`, `LastPass.exe`), and the web
origins `outlook.office.com`, `outlook.office365.com`, `teams.microsoft.com`,
`login.microsoftonline.com`. A target can name a denied executable explicitly (`allow_denied: true`).

The hooks already drop a click or key before it is queued unless the window's process is
recorded (`_Hooks.wanted`): a presence application's input never reaches the recorder.

### Collectors in a session

What changed in the recorders, and nothing else:

- **Desktop**: `Recorder(session=True)` does not stop when its processes are gone or when it has
  none yet (it stops on `stop()`); `add_process(pid, level)` adds a process through a queue the
  recorder's own loop drains; at `steps` level `_write_step` writes no tree file and no picture.
  The words the recorder holds as data are shared by all processes of the recording: one
  application's data words can only make another's masking stricter.
- **Web**: `attach(..., origins=..., outside=...)`. An action, tab step or download on a page
  whose origin is not a target is not numbered and not written: `outside(origin, ts)` gets it,
  and the session writes a presence row. The HAR keeps only requests made by pages of a target
  origin (their CDN and API requests included: they belong to the application). The session
  launches Edge itself (`msedge.exe --remote-debugging-port=<free> --user-data-dir=<session>/web/profile`
  `--no-first-run`) and attaches to it; the profile folder is deleted when the session ends.
- **Version resource**: `exe_info` also reads `CompanyName`.

### One timeline

The two recorders finalise their steps differently (the web recorder renumbers at close; the
desktop recorder writes a step a second after its input), so numbering happens once, at stop:

1. During the session, the journal (`journal.jsonl`, one line per event, flushed) holds the
   presence rows and every application seen, with its level.
2. At stop, each recorder finalises its own `steps.jsonl` as today.
3. `session.jsonl` merges the journal and both recordings by time; each recording keeps its own
   order (a stable merge), `global_seq` is the position. Between two rows of different
   applications an `app-switch` row is written (`from`, `to`: identity keys only).
4. `session.json` holds the state (`recording`, `stopped`, `recovered`), the targets, the
   applications seen with their levels and identity facts, and the Twin Lab commit.

**Recovery**: on start, a session folder left `recording` (the program was killed) is
finalised from what is on disk: the journal, each recording's `steps.jsonl` as far as it was
flushed. A web recording loses its HAR then (the HAR is written at the end); the session says so.

## What goes to the portal

At stop the session writes `portal-outbox.json` beside the recording. The recorder shows it
before the first send of each session, and sends it only if the person agrees ("Send references"
in the recorder; `--no-send` keeps it local).

```json
{"session": {"id": "...", "name": "...", "machine": "...", "started": "...", "ended": "...",
             "location": {"kind": "local", "path": "recordings/<id>"}},
 "apps": [{"key": "desktop:Contoso/CaseDesk", "surface": "desktop", "level": "full",
           "display": "CaseDesk", "exe": "CaseDesk.exe", "toolkit": "WinForms"}],
 "flows": {"desktop:Contoso/CaseDesk": [{"n": 1, "action": "type", "control": "Edit",
            "label": "txtSearch", "screen": "CaseDesk"}]},
 "timeline": [{"n": 1, "app": "desktop:Contoso/CaseDesk"}, {"n": 2, "app": "presence"}]}
```

Label rules (`labctl.workstation.flow`), so a label is never data:

- **desktop**: the control's AutomationId when it has one; otherwise its Name only when the
  control type is one of Button, CheckBox, RadioButton, Edit's label, MenuItem, TabItem, Tab,
  ComboBox, Window, Group, ToolBar, MenuBar and the name is at most 40 characters, has no digit
  and no `@`, and the recorder did not mark it as a shape; never the name of a Text, ListItem,
  DataItem, TreeItem, Hyperlink, Custom or Document. Otherwise the control type alone.
- **web**: the element's `id` or `name` from the selector when it has one and it passes the same
  test; otherwise the tag. Never link text, row text or a quoted text selector.
- **screen**: desktop, the application part of the window title (after the last ` - `, the same
  test); web, the origin and a route template (a path segment with a digit or longer than 24
  characters becomes `{id}`; no query, no fragment, no user information).
- **presence** applications appear in the timeline as `presence` only: no name, no time.
- **Applications sent** are those recorded at full or steps level. Presence applications are not
  sent and not registered.

## Portal (TwinLab2)

- **Sign-in**: the portal requires a signed-in user for every page: behind the OpenShift OAuth
  proxy it takes the user from `X-Forwarded-User` (only with `TWINLAB2_TRUST_PROXY_USER=1`);
  locally, `TWINLAB2_PASSWORD`. Without either it serves `127.0.0.1` only, as before.
  `SECRET_KEY` comes from `TWINLAB2_SECRET_KEY` (an OpenShift Secret) when set.
- **Machines and pairing**: *Machines → Pair a machine* makes a one-time code: 10 characters
  from a 32-symbol alphabet, valid 10 minutes, single use, at most 5 wrong attempts per code.
  The recorder exchanges it for a machine token (256-bit random). The portal stores only the
  token's SHA-256; the recorder stores the token with Windows DPAPI (current user). A machine
  can be revoked. The machine's name is what the person types when pairing.
- **Agent API** (`/api/agent/*`): bearer token only, no cookies, no form token; JSON with a fixed
  schema; the recorder treats every answer as data (targets by catalog id and identity key, no
  paths, no commands).
  - `POST /api/agent/pair` `{code, name}` → `{token, machine_id}`
  - `GET /api/agent/sessions` → sessions *offered* to this machine (created in the portal),
    and the identity keys of applications marked *not replicated*
  - `GET /api/agent/apps` → catalog applications with an identity, for a session started on
    the recorder
  - `POST /api/agent/references` the outbox; idempotent by session id
- **Sessions**: created in the portal (name, machine, target applications, optional automation)
  are **offered**: the recorder lists them, and recording starts only when the person at the PC
  presses Start. A session can also start on the recorder with no portal session; it is created
  when its references arrive.
- **Mapping**: each application in the references is found by identity key; one not in the
  catalog is created with status `discovered`. The recording is linked to every application it
  recorded, with that application's flow. A session made for an automation links the automation
  to its target applications.
- **Pages**: *Recordings* (machine, time, applications and levels, timeline across
  applications); *an application* (status, identity, its recordings and each recording's flow,
  the automations that touch it, or *no automation yet*); *Automations* (name, applications,
  recordings). A discovered application can be confirmed, renamed, merged into another, or
  marked *not replicated* (later sessions record it as presence: it is sent to the recorder as
  a deny entry with the targets).

## The portable program (TwinLab2 `agent/`)

- PyInstaller one-folder build of `agent/main.py` with Twin Lab's `labctl` collectors imported
  in-process (not `capture.py`, which starts `python` against a checkout). The Twin Lab commit is
  written into the build (`agent/_build.json`).
- `comtypes` generates UI Automation's wrapper on first use; in a frozen program the build
  generates it beforehand and ships `comtypes.gen`, because the program's folder may be
  read-only.
- No browser is bundled: the recorder starts the Edge installed on the PC (Chrome if Edge is
  missing). Playwright's driver (`node.exe`) is in the folder.
- Data: `%LOCALAPPDATA%\TwinLabRecorder\` (settings, token, `recordings/`), or `data\` beside the
  program with `--portable-data`. The token is DPAPI-protected, so a copied folder does not carry
  a usable token to another machine or user.
- Window: a small Tk window (status, portal, offered sessions, targets, Start / Stop, the outbox
  before sending). Command line: `pair`, `record`, `sessions`, `send`, `recover`.
- Transport: HTTPS to the portal (HTTP only for `localhost`), the system's certificate store and
  proxy, `--ca-bundle` for a private CA; no switch that ignores certificates; poll every 15 s
  with jitter.
- **Risks**: an unsigned program with low-level keyboard hooks is likely to be flagged by
  endpoint protection or blocked by AppLocker/WDAC. "No install" does not avoid that. The
  program must be signed before it runs on any machine other than a developer's. An application
  running elevated cannot be read by the unelevated recorder (Windows integrity levels); the
  recorder notes it when such a window comes to the front.

## Later

Cut from the first delivery, in this order of value: clipboard hand-offs and proposed
`crossapp` links; split and build-all reading a session (`desktop/split.py` and `split.py`
already split each part; a session-level proposal delegates to both); upload to GitHub or S3;
code signing; Electron as web; the Java Access Bridge in a session; portal-initiated stop;
HLLAPI capture for an emulator whose host cannot be changed; 5250; the mainframe build target.

## Delivery

Built on 2026-11-19, on one Windows 11 PC (Edge 142, .NET 6 SDK, Python 3.13):

| Piece | Where | Checked by |
|---|---|---|
| Identity, levels, deny list | `labctl/workstation/levels.py` | `tests/test_workstation.py` |
| Portal labels | `labctl/workstation/flow.py` | `tests/test_workstation.py` (no value, shape, digit, title or presence name in the outbox) |
| Timeline and outbox | `labctl/workstation/timeline.py` | `tests/test_workstation.py` |
| Session, recovery | `labctl/workstation/session.py`, `labctl workstation record / finalize` | `tests/test_workstation.py`; live: `tests/test_workstation_gui.py` (real Edge, real CaseDesk, Notepad, a second web origin) |
| Desktop session mode, `steps` level | `labctl/desktop/recorder.py` | the live test above |
| Web origin filter | `labctl/recorder.py` (`attach(origins=, outside=)`) | `tests/test_workstation.py`; the live test (no DOM, HAR entry or step of the other origin) |
| Portal: sign-in, machines, pairing, agent API, sessions, mapping, automations | `twin-lab2/registry.py`, `portal.py` | `twin-lab2/tests/test_registry.py`, `test_agent.py` |
| Portable program | `twin-lab2/agent/` (`build.py`) | `twin-lab2/tests/test_agent_e2e.py` (portal on this PC, pairing, offered session, real Edge and CaseDesk, send, portal pages); the built folder, copied elsewhere and run with no Python on `PATH`, end to end against a portal process |

Found while building, and handled:

- Edge hands itself over to a new process at start ("compat layer relaunch"), so the process the
  session starts is not the browser: the session passes `--edge-skip-compat-layer-relaunch` and
  finds the browser's processes by their profile folder, to recognise its windows and to end it.
- A browser profile's paths can exceed 260 characters; it is deleted through the `\\?\` form.
- Message boxes and closing windows are written by the desktop recorder without a process id:
  the timeline gives such a step to the desktop application before it.
- COM must be initialised in the thread that runs the desktop recorder before another thread
  imports comtypes.
- The session's journal has a `browser` row with the session browser's DevTools address, for a
  robot or a test that drives it (it stays on the PC; it is not in the outbox).

Not built or not checked:

- The portable folder is 248 MB (Python, labctl and its dependencies, Playwright's driver), not
  the ~100 MB first estimated. It is unsigned; it was not run on a second machine or a clean VM.
- The portal's container image (`twin-lab2/Dockerfile`) was built and run as OpenShift runs it
  (arbitrary UID); its OpenShift manifest (`deploy/openshift-portal.yaml`, OAuth proxy sidecar)
  was written, not applied to a cluster.
- *steps* level is desktop only; a web target is always full. Portal-initiated stop is not built.
- The window (Tk) was opened and looked at once; its flows were exercised through the same
  functions by the command line and the tests, not by clicking the window.

### Second review: recording a UiPath robot (2026-11-19)

A second deep review (a higher model) looked at recording a UiPath robot across web, desktop
and mainframe. Fixed and checked:

| Finding | Fix | Checked by |
|---|---|---|
| In the built program the desktop recorder ran in a single-threaded COM apartment with no message pump: no UI Automation events (typing, selections) reached it; the tests imported UI Automation first and never saw this | the session loads `labctl.desktop.win` (which sets COM to multithreaded) before anything else; a note when the apartment is not multithreaded | `test_a_fresh_process_hears_ui_automation_events` (a fresh process); the built program now records 37 CaseDesk steps where it recorded 28 |
| Web recording ended when the first tab closed (UiPath closes its tab at the end of a scope) | the web recorder waits on any tab, and for the next tab while the browser lives (`keep_open`); a browser closed during the session is opened again with the same profile and port, recorded into `web-2`, `web-3`... | live tests |
| The robot's browser is not the session's: no UiPath extension in a throwaway profile; Edge only | a kept profile (`browser_profile`, port 9333) where UiPath's extension is installed once, and UiPath's *CustomFolder* user data folder pointing at it; attaching by DevTools address; Edge or Chrome | `test_attach_to_a_running_browser_and_keep_a_profile` |
| Pictures of desktop windows held in memory until the end | off by default in the recorder program (`--pictures` to turn on) | — |
| Labels could carry data (title parts, tab and window names, aria-labels, route segments) | stricter rules (`flow.py`): identifiers only, a title part only when it is the application's name, route words from a fixed list | `test_workstation.py` |
| UiPath's Simulate typing sets values without the keyboard focus, which the recorder ignored | robot input: such value changes are steps (on in the recorder program) | the fresh-process test (the CaseDesk script types through the Value pattern) |
| A target was picked up by a 250 ms poll; a robot acts sooner | the desktop recorder asks the session about a process the moment its first input or event arrives (`admit`) | the no-portal run (CaseDesk started outside the session, recorded from its first step) |
| Targets demoted silently (hosts, browsers, `C:\Windows`, not replicated); applications running as administrator | a note for each, once; notes shown in the recorder's review and by `show` | — |
| Failures invisible in the window | each run's output in `logs\<session>.log`; an error exit is shown | — |
| HAR kept tokens in headers | in a session, headers that may carry a credential keep their name only | — |
| Ctrl+C, a closed window, a crash between pictures and their check | Ctrl+C joins the recorders; the session stops when the window that started it is gone; recovery deletes unchecked pictures | — |

Not done from that review: re-installing input hooks that Windows removed after a slow
callback; streaming the HAR to disk with a body size cap; pairing off the window's thread; the
robot's own log (`%LOCALAPPDATA%\UiPath\Logs`) as a fourth stream aligned to the steps
(`labctl/botlog/robotlog.py` reads it); proposing targets from the workflow's XAML
(`labctl/analyse/uipath.py`).

### Built after the second review (2026-11-20)

- **The robot's own log** (`workstation/robot.py`): with UiPath's logging level at *Verbose*,
  `%LOCALAPPDATA%\UiPath\Logs\*_Execution.log` lists every activity as it starts and closes. At
  the end of a session the activities logged in its time are written to `robot/steps.jsonl`
  (type, the developer's display name with digits masked, the selector's application, never an
  argument or a value), and `robot/coverage.json` says which UI activities left no recorded
  step: the places the recording missed (a Simulate click, an application that was not a
  target, a terminal not on the proxy). The outbox carries the counts only.
- **Targets from the workflow** (`workstation/propose.py`, `labctl workstation propose`,
  `TwinLabRecorder propose`, and a button in the window): window applications, web origins
  (selectors, arguments, default values written in the XAML), terminal hosts, the browser, and
  notes for Simulate input and for addresses held in variables (not guessed).
- **Modern Windows applications** (UWP / WinUI: Calculator, the Store apps): their top-level
  window belongs to `ApplicationFrameHost.exe`, the application's own window is a child. The
  watcher, the desktop recorder's windows and its snapshots look behind the frame
  (`win.app_pid`, `win.frame_windows`). Checked with Windows Calculator.
- **Browser ready**: the session announces its browser (the journal's `browser` row, the
  window's "The browser is ready") only once the web recorder listens; a built recorder loads
  Playwright slowly, and actions before that were lost.
- **Hooks renewed** every 20 s; **HAR bodies** over 2 MB keep their size only (in a session).
- **Signing**: `agent/build.py` signs with `TWINLAB_SIGN_CERT` (a certificate's thumbprint);
  not exercised (no certificate on the build PC).

A real recording with the built program, through the portal (2026-11-20): the public Selenium
test form (`https://www.selenium.dev/selenium/web/web-form.html`, synthetic text, submitted),
Windows Calculator (128 × 4 =) and the synthetic mainframe, for an offered session with an
automation. The portal mapped the three applications and the automation; the flows read
`my-text-id`, `my-select`, `multiplyButton`, `USERID`, `ACCOUNT NO`, `PF3`; example.com, visited
in the session's browser, and the other applications were only "used"; no typed text reached
the portal.

## Mainframe

A 3270 screen is a grid of fields; the user types into unprotected fields and presses an AID
key (Enter, PF1–PF24, PA1–PA3, Clear), which sends the changed fields.

| Web / desktop | Mainframe |
|---|---|
| page / window | screen (one host write) |
| DOM / control tree | field table: position, length, attributes (protected, numeric, intensified, non-display) |
| selector | screen id + field position, with the label beside the field |
| click / submit | AID key |
| HAR | the 3270 data stream, structure only |

Built (`labctl/workstation/tn3270.py`; a target `{"tn3270": "host:port"}`, `tn3270s://` for
TLS to the host):

- **Capture**: a TN3270 proxy in the session, one per mainframe target, on `127.0.0.1:23270`
  (23271... for more; a free port, said in the session's notes, when that one is taken). Every
  byte is forwarded first and read after, so recording never delays the terminal. TN3270 and
  TN3270E, terminal models 2 to 5, Write, Erase/Write (Alternate), Write Structured Field,
  Erase All Unprotected; the orders SBA, SF, SFE, MF, SA, IC, PT, RA, EUA, GE.
- **Steps**: `screen` (the first after connecting and the answer to each key), `type` (each
  field the terminal sent: label, shape; `secret` for a non-display field), `aid` (the key and the
  cursor). `screens/<seq>.json` keeps the field table.
- **Screen id**: a hash of the field layout (positions, lengths, protected or not): the same map
  with another record's data is the same screen. Title: the first row with protected text.
- **Privacy**: protected text with every token holding a digit masked and every value typed
  earlier in the session masked; unprotected values as shapes; non-display fields keep nothing.
  A protected field that shows data without a digit (a name) stays in the local screen file as
  shown; it becomes a label only when it stands right before an input field on the same row.
- **Identity**: `mainframe:<host>:<port>` (the host application id is not on the wire before
  sign-on).
- **Checked** with a synthetic host and terminal (`reference/mainframe/demo_host.py`), not with a
  real mainframe or emulator.
- Not built: HLLAPI, 5250, the build target (a TN3270 server serving the recorded screens).
