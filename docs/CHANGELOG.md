# Changelog

One paragraph per plan item: what exists now, how to run it, what was left open.

## 2026-11-19 — Build packages carry the open-source libraries and web fonts
The owner's decision ("Carry them"): a blind rebuild of Demoblaze passed every check but set
its text in a serif fallback, lost the video player's icon font and wrote its own stand-ins for
Bootstrap's dialogs and carousel, because the libraries were named, not carried. Now the brief
step carries them by default (`labctl/vendor.py`, `labctl/fontname.py`):
- **Libraries**: every third-party style sheet and script the browser received, byte for byte,
  under `apps/<app>/vendor/<library>/<version>/` with the fonts and images its style sheets
  reference, when `libraries.py` identifies it (a file name alone is not enough), its licence
  allows redistribution (MIT, BSD, Apache-2.0, ISC, MPL-2.0, OFL-1.1, Unlicense, CC0; from its
  banner or the known licence of a well-known library) and it holds no key, token or internal
  host. Anything else is named with the reason. The application's own scripts are never carried
  by this rule. `vendor/vendor.json` lists every file with its address and sha256 and which page
  loads it; `TWINLAB_VENDOR=off` switches it off.
- **Fonts**: the font files of the HAR go into `look/fonts/` when their own `name` table (read
  without a font package: TrueType, OpenType, WOFF 1; WOFF 2 when a `brotli` module is
  installed) or a known open family says the licence allows it; the carried style sheets point
  at them and the pictures are rendered with them. `TWINLAB_LOOK_FONTS` stays an override:
  `bundle` (every file, with a warning), `off` (none). Fonts embedded in a library's style
  sheet stay with it (video.js's icons); `@font-face` with a `data:` address is now read.
- **Notices**: `apps/<app>/THIRD-PARTY-NOTICES.md` (name, version, licence, address, sha256,
  files, and the licence texts from `labctl/licences/`); purposes in `MANIFEST.json`/`INDEX.md`.
  The brief (*Client libraries and fonts*), `spec/ARCHITECTURE.md`, the page files, the
  constitution's *same technology* rule and the task say to use these exact files. The package
  refuses a `vendor/` file it did not carry. The guard and `factory.GENERATED` protect
  `vendor/` and the notices. Tests: `test_vendor.py` (fixture `vendorapp`, fonts built in the
  test).
- Demoblaze: Bootstrap 4.0.0-alpha.6, jQuery 3.2.1, video.js 7.6.5 and SweetAlert, tether and
  videojs-contrib-hls (versions not stated in the files) are carried; Lato Regular (OFL-1.1)
  is in `look/fonts/` and the pictures now show Lato. Open: three libraries have no stated
  version (`version-unknown`); the Lato weights the recording never loaded are not carried.

## 2026-11-19 — Demoblaze blind rebuild, round 2: the lab's own replay and judging defects
The judge's official replay failed only on defects in the lab's tooling; each is fixed with a
test (`test_replay_empty_fragment.py`, `test_replay_settle.py`,
`test_recorder_unsettled_picture.py`, `test_visual_self_changing.py`,
`test_scope_element_subtree.py`, `test_draft_numbering.py`, `test_replay_refused_request.py`):
- **Empty fragment**: `/#` (a link `href="#"`) is the same address as `/` in the replay's
  address compare; a fragment that is not empty is still compared. The origin map keeps the
  recorded empty fragment; the recorder writes the address as the browser shows it.
- **Settling**: after each step (typing aside) the replay waits at least the recorder's quiet
  time (`recording.json`) and until running CSS transitions and animations have ended (read-only
  `document.getAnimations()`, at most 3 s; endless ones are never waited for).
- **Unsettled pictures**: a recorder picture taken after the next action began is kept and
  marked `picture_settled: false` (older recordings: read by the quiet time); the visual draft
  makes that state optional with the reason.
- **Self-changing regions**: the visual draft excludes riding carousels, videos, CSS animations
  and elements that changed with no action between, in every state, with the reason.
- **Element exclusions** in `scope.yaml` cover the element and everything inside it, resolved
  against the recorded (and live) snapshot.
- **Numbering and pointers**: drafted acceptance ids and notes use the numbering of `after:`;
  `bindings.yaml` says which files show a clicked record (`check_against:` its `before`
  snapshot and the next step's picture).
- **Refused requests** name the step they came during: with an origin map one fails a replay
  whose steps all passed (the one cause in `fidelity.py` for the PrimeFaces reference's
  unreproduced fail).

## 2026-11-19 — Demoblaze package, second trial: what the builder and the judge could not use
A package built from the public Demoblaze recording (with the approved public-data declaration),
a blind rebuild from it and a judge's replay found these; each is fixed in `labctl` with a test
(`tests/test_package_trial_fixes.py`, `test_package_trial_fixes2.py`,
`test_recorder_record_rows.py`):
- **Front end**: static HTML filled in the browser from an API is no longer a guessed
  "server-rendered": `har.client_rendering` reads it from the recording (containers empty in the
  served HTML and filled in the snapshots, answer values in the snapshots and not in the served
  HTML) and says "static pages rendered in the browser from the API" with that evidence (likely).
  Page addresses stay routes (the server sends the files).
- **Origins**: state-changing requests are counted per origin (the pages' own, or the API origin
  of the same site); the network note says the API origin's endpoints are in the endpoint files.
- **Media origin**: a host of the pages' site that the application's configuration or code
  names (`HLS_URL` in `/config.json`) is the application's media origin
  (`network.json` `media_origins`: what it serves, an HLS playlist and segments): serve a
  stand-in or leave the video out with a reason. Real third parties are unchanged.
- **Static JSON configuration** (`/config.json`): a file, not a table; carried as
  `apps/<app>/static/config.json` with its keys and local addresses (`http://localhost:18080`,
  `.../hls`; a relative `.` and `./hls` when the pages only write `KEY + '/path'`), never the
  real hosts. `static/` is a generated folder for the guard.
- **Public data, consistently**: a declared value in an element with an id (`p#article`) is
  carried in snapshots, `texts.json` and pictures; a number in an element of its own (`#totalp`)
  and cart rows stay masked; capitalised parts of declared values (`Mac` of `MacBook`) pass the
  look's check. Every record any answer returned reaches the seed (`/bycat`'s laptops and
  monitors: 15 rows, one per id) and their declared photos are carried.
- **Record bindings** from the evidence: the clicked row or card whose text has the recorded
  shape holds exactly one seed title (rows of one shape: the page the click opened decides);
  written to `steps/*.values.yaml` `records`, `steps/*.json` `record`, `navigation.json` `record`
  and `bindings.yaml` (`generated:`); `TO DO` with candidates only when ambiguous. The replay
  accepts a title that a row's text holds as a whole phrase, once.
- **Drafts to judge with**, all unapproved: acceptance cases the recording showed (row counts,
  the count after a delete, the record's title on the page a click opened), `visual.yaml`
  as `visual.draft.yaml` (threshold mode, 2 %, tolerance 16; text regions left out when no
  font is carried; the `labctl behaviour visual` template defaults to threshold too),
  `expected-actions.draft.yaml` and a `scope.yaml` excluding the media player. The new
  acceptance cases are `required: false` and the visual draft has its own name, so no
  unapproved draft decides a replay.
- **Aliases**: `/index.html` beside `/` with the same document is one screen
  (`sanitize.screen_aliases`); the brief says to serve both addresses, `navigation.json` lists
  `aliases`. Relative links resolve against their page, so in-scope screens are no longer
  "links that leave the scope".
- **Look**: pictures are exactly the recorded viewport (1280 x 720; full-page screenshots were
  1295 wide from a horizontal overflow); a video's `poster` (`/imgs/front.jpg`, drawn by video.js as
  a background) is an interface image and carried; style text stays raw (`a>b`, not `a&gt;b`).
- **Masking**: Bootstrap's behaviour attributes that point at elements (`data-target="#id"`,
  `data-slide-to`), utility classes (`h-100`), `sizes` and declared query values
  (`prod.html?idp_=1`) are kept; a row's own control (`Delete`) keeps its label.
- **Replay and recorder**: a dialog after a hash navigation is armed for the click that caused
  it; a record locator is checked against the page it was recorded on (the list itself may be
  the identified element); a screen's snapshot is one with no dialog left closed by script.
- **Smaller ones**: code-only endpoints (`/login`, `/signup`, `/check`, ...) get a stub API file
  with the fields the code sends; each list states its observed order; `LastEvaluatedKey` keeps
  its paging value; a dialog whose text the recording lost takes the code's, saying so; library
  versions seen twice are not "certain"; template values say how many are left out.
- **BUILD_TASK**: a builder with the package alone gets checks it can run without the repository,
  labctl or Docker (start, open each screen at each address, compare with the pictures at the
  viewport, run the replay values, navigation, API); section 4 is for builders in the repository.
- Left open: without a declaration a record binding needs seed titles in the page (synthetic
  rows never are), so it stays TO DO; G2 (carrying the application's scripts) stays an owner
  opt-in; duplicate dialog texts in `codemap.json` are the code-map work's.

## 2026-11-19 — Typed values: taken out where they are a value, never out of ids
- The recorder took every typed value out of snapshots, the HAR and locators as a substring, so
  it also changed ids, classes and names that held it: typing `report` on Caseboard PF turned
  `main:navReports` into `main:navAs` and broke that element's selector in the rebuild and the
  replay. One rule now decides (`labctl/typedvalues.py`, used by `recorder._Secrets`, the
  browser's picture mask `MARK_TYPED_JS`, and the HAR scrub): a typed value is replaced by its
  shape only where it is a value (text nodes, form values, `title`/`alt`/`placeholder`/
  `aria-label` and other content attributes, `data-*`, an address's query, fragment and a path
  segment that is the whole value, JSON strings and numbers, form fields), and only as a whole
  token (`Reports`, `main:navReports`, `report-row` keep it). `id`, `class`, `name`, `for`,
  `form`, ARIA id references, test ids and enumerated attributes (`type`, `role`, ...) are never
  changed by a value inside them; one whose whole value is a typed value becomes the placeholder
  `tl-typed-a` (`-b`, ... per value) in the snapshot, the step's selector and locators, and the
  page's scripts and style sheets that name it, and the step says so (`typed_placeholders`:
  placeholder and the step it was typed at, never the value). The replay tries the placeholder
  (the rebuilt page) and then the value it types for that step (the application itself), and
  compares the live page with the placeholder in place (`locate.typed_values`,
  `fidelity._placed`). The HAR is read by what each string is: markup as markup, JSON as JSON,
  an address as an address.
- Caseboard PF's flow filters by `report` again (it typed `missing` to dodge this) and
  `test_reference_primefaces.py` checks `a#main:navReports` opens the Reports sub-page in the
  package. Tests: `test_typed_values.py` (a recording typing `report` beside `main:navReports`,
  a name in a text node, a value inside a class, an id equal to a value, a value in a query,
  replayed on a fresh copy).
- Left open: a value the replay must see in the address bar (a typed word in the page's own
  address) is still compared by its shape and fails the address check, as before; a JSF request
  parameter that names an id equal to a typed value (`javax.faces.source`) gets its shape, not
  the placeholder.
## 2026-11-18 — Desktop recorder: the four faults the certification runs found
- WPF and Java Swing are now CAPTURE VERIFIED, as WinForms: all three pass detect, capture (20
  expected actions each) and self-replay (WinForms and WPF 29 of 36 steps verified, 7 covered by
  the steps around them; Swing 29 of 32, 3 covered; none failed). Gates and expected lists are
  unchanged; the faults were in the recorder (`labctl/desktop/recorder.py`, `java.py`, `jab.py`).
- Swing, order: an item chosen in a combo box without mouse or key was found only by the tree
  comparison after the next click's batch and written after that click. The window's tree is now
  asked for in the hook when a click or a key arrives; a change it already shows goes before
  that input.
- Swing, a click lost or put on the wrong control (about one run in three): the idle tree
  comparison could read a window while a click took effect (a message box closing, a menu
  opening) and replace the window's last good tree with a torn one, in which the waiting click
  was then looked up. A tree read while its window closes is dropped, a torn reading is read
  again, an idle reading is dropped when an input arrived meanwhile, and the hook notes where
  the window is.
- WPF, a selection the application makes itself (the grid selects the saved row after Save) is
  no longer a `select` step: it is an `effects` entry of the step that caused it, which verify
  neither replays nor counts. A row the user selects (a mouse press or a key in that grid) is
  still a step. WinForms raises no selection event at that moment.
- WPF, a message box written before the click that opened it: the click took the time of its
  Invoke event, read after a slow call into the application. It keeps the time of the mouse
  press.
- Left open: a script that selects a list row by a pattern call alone is taken for the
  application's doing; in Swing, a selection made without input just before a click that opens
  a menu is still written after that click ([09-desktop.md](09-desktop.md), Limits).
## 2026-11-18 — Certification matrix: Angular, JSF and the three desktop toolkits run
- The matrix ([certification-matrix.md](certification-matrix.md)) now has real results for
  seven of its nine entries. Angular 21.2.26 and JSF 2.3 (Mojarra) run on the two Caseboard
  reference applications; WinForms, WPF and Java Swing were run for the first time
  (`TWINLAB_DESKTOP_GUI=1`, one at a time, on the real mouse and keyboard).
- CAPTURE VERIFIED: server-rendered HTML forms (62 expected actions, 107 of 107 steps replayed),
  React 16.7.0 (20; 24 of 24), Angular 21.2.26 (34; 34 of 34), JSF 2.3 / Mojarra on Jetty
  (24; 48 of 48, with the server detected as Java, JSF 2.3, Mojarra), WinForms on .NET 6.0.3
  (20; 29 of 36 steps verified, the other 7 covered by the steps around them). None failed a step.
- PARTIAL, published as they ended: WPF (detect and capture pass, 20 expected actions; verify
  does not perform the selection of a data row the application makes after Save, so one of 37
  steps was not performed and self-replay fails; one earlier run also recorded a message box
  opening before the click that caused it). Java Swing (detect and self-replay pass, 30 of 33
  steps verified; capture fails because the recording has the check box toggle before the
  status selection the flow makes first; one run of three also missed the OK click on the first
  message box, the intermittent fault known on this machine).
- The harness, `labctl certify`: an application can be started by its own module
  (`app.kind: module`, with `namespace` and `unavailable`), a flow can take the address
  (`flow.base_url`), the expected actions can be the application's own `expected-actions.yaml`
  (`expected_file`), a replay's values can come from a function of the flow file
  (`bindings_from`) and a step on an existing record gets its title (`records`). The desktop
  gate now fails on a step verify could not perform, where verify's own status stays `pass`;
  the detect line names the declared keys instead of printing a nested mapping.
- The JSF entry is named for what it is (`jsf-mojarra`, not PrimeFaces). It needs a JDK and
  the 14 jars `fetch_libs.py` downloads; without them, as in CI, it is NOT RUN with that reason.
- Left open: no entry is CERTIFIED (no rebuild was judged). Vue and ASP.NET Web Forms are
  declared and NOT RUN: no reference application has been built for either. The two desktop
  failures are defects of the recorder and of verify, not of the entries; they stay in the
  table until fixed. PrimeFaces is no longer declared.
## 2026-11-18 — Certification matrix: which technologies are proven
- Detection has rules for about 30 web stacks; few were proven on a live application. The
  matrix (`certification/matrix.yaml`, [10-certification.md](10-certification.md)) has one entry
  per technology, each on a small reference application, each through four gates: detect
  (technology and version as declared), capture (a scripted flow recorded with the real
  recorder holds every expected action, written by hand), self-replay (the recording replays
  against a fresh instance with no failed step) and rebuild (a kept replay report of a rebuilt
  copy is VERIFIED COMPLETE; read, never run).
- `labctl certify [--only <id>] [--out <folder>]` runs it, writes `certification/results/<id>.json`
  and regenerates [certification-matrix.md](certification-matrix.md). A gate that did not run
  is NOT RUN with its reason and never counts as passed.
- Run here: server-rendered HTML forms (the two-application fixture: 62 expected actions all
  captured, 107 of 107 steps replayed, 0 failed) and React 16.7.0 (`apps/web-todo`: version
  detected from the bundle, 20 expected actions all captured, 24 of 24 steps replayed, 0
  failed). Both are CAPTURE VERIFIED. No entry is CERTIFIED: no rebuild was judged.
- Left open: Angular, Vue, JSF / PrimeFaces and ASP.NET Web Forms are declared but NOT RUN
  (their packages are not on this machine). WinForms, WPF and Java Swing have their gates in
  code but were not run: they take the real mouse and need `TWINLAB_DESKTOP_GUI=1`. The React
  entry is NOT RUN in CI, where its vendor files are not built.
## 2026-11-18 — JSF sub-pages: views, ajax states and partial responses (PrimeFaces reference)
- Reported from the office: in JSF applications with ajax, sub-pages were not recorded and linked.
  Reproduced on a new reference, `reference/web/caseboard-primefaces/` (Mojarra 2.3.9 and
  PrimeFaces 10.0.0, the one new jar, from Maven Central with its pinned SHA-1): sign-in and a
  case page reached by postback and forward (the address one step behind), four sub-pages swapped
  by ajax at one address, a tab view and an accordion loaded on first open, a wizard, a dialog,
  a confirm dialog, a dialog-framework page in an iframe (slow to answer), a lazy data table
  (paging, sorting, filtering, row expansion and selection) and a remote command.
- Before, the package had 4 screens, the wrong ones: the workspace was merged into `/login.xhtml`
  (whose page file and page document showed it), the case page into `/workspace.xhtml`; none of
  the 3 transitions was right and sign-in was not linked; 11 sub-views, none with an opener (every
  PrimeFaces dialog and panel "first seen open"); no sub-page, wizard step or table state; 4
  snapshots. The replay of the recording stopped at step 20 of 56 (18 replayed, 4 failed).
- Now (`labctl/jsfviews.py`): a step's screen is the view its page shows (the address its JSF
  forms post to), so the 4 screens are right and the 3 forwards are transitions with `forward`
  and `address_after`; 26 states inside the workspace, each with its opener, what its partial
  response re-rendered, its own snapshot (30 snapshots) and a section in the page document; every
  `<partial-response>` read from the HAR (updates, scripts by name, redirect, error,
  `validationFailed`), with what is ignored (view state, head resources); PrimeFaces widgets in
  `codemap.json`. The recording replays on a fresh instance: 56 of 56 steps, 0 failed.
- Recorder and replay: the settle rule also waits while PrimeFaces holds a request (its queue, a
  table filter's delay) or a same-origin frame is loading, and briefly for jQuery animations
  (read-only looks); a PrimeFaces row is found again (its `<tbody>` is the list, `even`/`odd` and
  `ui-state-*` do not narrow it, a cell is named by its place); pointer classes
  (`ui-state-hover`, `ui-state-focus`, `ui-state-filled`) are not compared. Tests:
  `test_jsfviews.py`, `test_recorder_jsf.py`, `test_reference_primefaces.py` (skips without a
  JDK or the jars).
- Left open: a typed value is masked wherever the recorder writes it, ids included (a value an
  id holds changes that id in the evidence); the sanitiser masks some interface words of state
  titles (a column "Number", a tab "Notes") as data; sub-pages swapped by plain script with no
  partial response, client-side state saving, file upload and push are not covered.
## 2026-11-18 — JSF proven on a real application: the Caseboard reference
- Detection and capture had rules for JSF but had never met a JSF application. There is one now:
  `reference/web/caseboard-jsf/` (Mojarra 2.3.9 on embedded Jetty 9.4, Facelets pages, classic
  managed beans, server-side view state, an ajax partial update, POST then redirect; synthetic
  data in memory). Its jars are fetched from Maven Central with pinned checksums
  (`fetch_libs.py`) and never committed; `build.py` and `run.py` need only a JDK.
- The three certification gates run on it (`certify_local.py`,
  `tests/test_reference_jsf.py`, skipped without a JDK or the jars): DETECT names JSF, Mojarra
  and specification 2.3; CAPTURE holds all 24 expected actions; SELF-REPLAY against a fresh
  instance replays 48 of 48 steps with no difference and 23 of 23 acceptance cases.
- Four defects it found, each fixed with a test beside its neighbours: an id with a colon
  (`search:q`) was cut at the selector's backslash, so every field given a value was compared as
  a difference (`dom.key_of`); `;jsessionid=<id>` in an address was compared by its value, new
  in every session (`fidelity.same_address`); the submit button a command link adds and clicks
  from script was recorded as a click of its own that no replay could find (recorder); and the
  analysis did not say which JSF implementation or version (`server.implementation`,
  `server.framework_version`, read from JSF's own script).
- Left open: MyFaces and Jakarta Faces 4 are matched by rule but not proven on an application;
  the implementation's release (2.3.9) is in no page, so it is not reported; client-side state
  saving, PrimeFaces components, file upload and a session that times out were not exercised.

## 2026-11-18 — A recording cut short by a crash, a timeout or a restart
- An audit listed recovery after an interrupted recording as never validated. It is now tested
  (`tests/test_recording_recovery.py`, no browser): a last line of `steps.jsonl` cut off in the
  middle, a garbage line, a snapshot or picture a step names that was never written, a missing,
  empty or cut-off `recording.har`, step numbers repeated or out of order, and no steps at all.
- What could be read is used and the rest is said. `labctl.botlog.recording.load` fills a new
  `problems` list (the same idea as the brief loader's); `labctl evidence split` and
  `build-all` print each as `recording: ...`, the split writes them to the sequence file's
  `problems`, and a replay report lists the dropped lines and each step that ran but could
  not be compared because its snapshot is not in the recording.
- Fixed on the way: a file cut inside a character lost every step (now only its last line);
  step numbers used twice (a recorder restarted and appended) were sorted into each other,
  now they stay in the order written and the split refuses them with the reason, as every
  reference between steps is by number; a HAR that is JSON but not a HAR raised a traceback.
- Left open: a replay whose recording lost a line or a snapshot still reports `pass` when
  every remaining step passes; the damage is under "Why not pass" and in `compared`, but
  whether it should lower the status is a decision for a person. The replay loop itself was
  not run in a browser for these cases.

## 2026-11-19 — Code map: library versions from the files, one entry per library and dialog
Three defects of `codemap.json` found on the Demoblaze recording:
- Library versions were guessed: Bootstrap was reported as 5.0.0 while its own banner says
  v4.0.0-alpha.6. A library is now named and dated by `labctl.libraries.identify`, shared by the
  code map and the page rules (`rules.json` `scripts.libraries`): the version the file's content
  states comes first (a banner such as `Bootstrap v4.0.0-alpha.6` or `/*! jQuery v3.2.1`,
  `@version`, a `VERSION = "..."` assignment in a library, JSF's `specversion`); the address is
  only a fallback, and `version_from` then says it is a guess. A file under `node_modules/` or
  `bower_components/` is named by its package, so video.js, its HLS plug-in and Tether are no
  longer analysed as the application's own code.
- A library was listed twice (its style sheet and its script): one entry per library and
  version, listing its files; a file that states no version joins its library's only version.
  The page rules list their libraries the same way (`name`, `version`, `files`).
- `dialogs` held 47 entries: each page's modals once per page and each browser alert once per
  script. Each dialog is now listed once with its `routes` and `files`. A third-party widget's
  own dialogs (video.js `vjs-*` modal dialogs and other media players', inside the player's
  container or written by the library's script) are left out, with the rule the navigation map
  already used (`sanitize._player`). Demoblaze: 47 dialogs to 17 (5 modals, 12 alerts), six
  libraries with their files, Bootstrap 4.0.0-alpha.6.
- The navigation map already left out a player's dialogs; its sub-views stay per screen, as each
  screen has its own opening and closing steps.
- Left open: the media-player class list is the only widget rule; another library's dialog
  markup in a downloaded page (not from its script) is still listed.

## 2026-11-18 — The application's map from its code: every route, menu, tab, dialog and call
A recording shows the screens a person visited; the code the browser downloaded describes the
whole application. `labctl.codemap` reads it without running it (`labctl.jstokens`, a small
tokeniser: strings, regular expressions against division, nested template literals, comments)
and writes `apps/<app>/codemap.json`:
- the inventory of the application's own files (pages, scripts and which are lazy chunks, style
  sheets, JSON configuration, translation files, manifests); third-party libraries are named
  with their version (file name, JSF resource library, licence banner, `ng-version`, JSF's
  `specversion`) and not analysed;
- route tables (Angular, React Router objects and compiled `<Route>`, Vue Router, server pages
  and their links): path, parent and children, guards and where a refused visitor goes, the lazy
  chunk and whether it was downloaded, redirects, titles; Angular's compiled templates (attribute
  arrays and element instructions, `@if` blocks), HTML in scripts and compiled JSX give links,
  menus, navigation bars, tab sets, tabs, tab panels and dialogs with the component and route
  they belong to, and who opens and closes a dialog (`showModal()`, `.modal('show')`,
  `dialog.open(X)`, a signal set by a click handler); JSF command links and ajax updates;
- API calls (`fetch`, `http.get/post/...`, `axios`, `$.ajax`, `XMLHttpRequest.open`, forms),
  each marked *called in the recording* (it matches the API catalogue) or *in code, not called*;
- translation texts, lists of values and titles, each through the data checks; one that may be
  data is dropped and counted.
The brief gets *What the application's code defines* (build every route at least as a reachable
page; a screen only the code has is reconstructed and reported), `navigation.json` gets a screen
per route only the code has and a `source` per transition (`recording`, `page`, `code`), and the
package, its index, its reading order, `factory.GENERATED` and the guard carry `codemap.json`.
No script is carried. `codemap.check_text` finds secrets (keys, tokens, passwords, private keys,
connection strings, session identifiers) and host names other than the application's own; a map
that holds one is not written and the package refuses one. `labctl evidence fetch-code
<recording> --yes` fetches, opt-in, only the static code the recording's code names and the
browser never loaded (`.js`, `.mjs`, `.css`, `.json`; own origin; GET; no redirect; content type
checked; rate limited; stops on 429/503; no session unless the saved browser state is given) into
`<recording>/code/`. Proven on the Angular reference build, the JSF reference application and a
hand-written fixture (`tests/fixtures/codespa`), whose admin chunk is found, fetched and mapped.

## 2026-11-18 — Field rules and page behaviour: what each page checks, from its HTML and its JavaScript
Until now a package knew a rule only when the recording triggered it. `labctl.pagerules`
writes `apps/<app>/rules.json` and the brief's *Field rules and page behaviour*, per screen and
field, from three sources:
- **The HTML** of every snapshot (`declared`): `required`, `minlength`, `maxlength`, `min`,
  `max`, `step`, `pattern`, the input type, `readonly`, `disabled`, `autocomplete`,
  `inputmode`, placeholder, tooltip, label, help text, counter, options, ASP.NET `data-val-*`,
  jQuery Validate `data-rule-*` / `data-msg-*`, PrimeFaces `data-p-*`, messages the page holds
  hidden, and JSF / PrimeFaces partial updates in event attributes.
- **The application's own scripts** in the HAR (`in code, not triggered` or `in code, not tied
  to a field`): tokenised (strings, regex literals against division, template literals,
  comments), never run. Angular `Validators.*` on form controls, jQuery Validate `rules` and
  `messages`, `setCustomValidity`, plain checks with their messages, cross-field rules (equal,
  not before, one of two), masks, fields shown, enabled or made required by another, dependent
  drop-downs, a submit button enabled when the form is valid, session timers with their delay.
  Third-party scripts and library files (by name or banner) are named, not analysed.
- **The recording** (`seen`): messages that appeared, and what changed after a step that typed,
  chose or ticked (a field shown, a drop-down filled by an API call, linked to the catalogue).
- Messages go through `sanitize.mask_message` and the look's data check. Scripts are not
  carried; `TWINLAB_PAGE_SCRIPTS=carry` carries the application's own script files under
  `apps/<app>/scripts/` (source maps stripped, never a library), refused, by the brief and by
  `labctl build package`, when one holds something shaped like a secret, a host other than the
  application's own, or a value the recording shows as data.
- Proven on a new fixture (`tests/fixtures/formdesk`), on the Angular reference (both
  `Validators.required` of its source found from the minified build, the owner's never
  triggered; no wrong rule) and on the JSF reference (its three triggered messages and the
  partial update; its validators that were never triggered cannot be recovered: JSF checks on
  the server). The task text, the reading order, the index, the completeness check, the guard
  and `factory.GENERATED` name the new files.
## 2026-11-18 — Build documents: a constitution, the architecture, a plan, one page file per screen
A builder working screen by screen had to gather one screen's facts from the brief and six
machine files. `labctl.builder.docs` now writes, into every web package and only from the
files it already holds, `apps/<app>/spec/`:
- **`CONSTITUTION.md`**: the numbered rules and what "done" means. The task's *Rules that are
  not negotiable* and *done* list are written by the same functions (`docs.rules`,
  `docs.done_items`), so the two cannot differ. New rules in both: same technology and
  versions, same look, assumptions written down, done means every check holds.
- **`ARCHITECTURE.md`**: stack and versions, layers, the route table (every screen, recorded,
  in a frame or popup, found by the link check, or known only from `codemap.json`), the API
  surface, tables with keys and relations and the field map, sign-in, session and cookies,
  ports and environment variables, the flow across applications, a text diagram.
- **`BUILD-PLAN.md`**: database and seed, sign-in, each screen in order from the entry along
  the recorded ways, navigation, API, rules, look, self-check, each with "done when" lines
  that point at the done list and the page files' checks.
- **`PAGES/<NN>-<screen>.md`**: per screen its address, ways in and out, picture and regions,
  literal texts, every element, what each action did, rules and behaviour, API calls linked to
  their files, data and columns, seed rows, acceptance checks, replay steps, sub-views, open
  questions. Pages not recorded and screens known only from code are marked so.
- **`GLOSSARY.md`**: option lists, code lists, the statuses the application answered.
- **Cookies.** Do cookies need capturing? Their values never: a rebuild issues its own, and no
  value is read into the package. Their facts do: `sanitize.cookie_attributes` reads each
  `Set-Cookie` (name, `Path` as a pattern, `Domain` relative to the host, `HttpOnly`, `Secure`,
  `SameSite`, `Partitioned`, lifetime as a duration, cleared or not), `signin` records which
  response set and cleared each cookie, and `apps/<app>/session.json`, the brief's *Sign-in and
  session* and `ARCHITECTURE.md` carry them. Tests check that no cookie value from the HAR is in
  any packaged file.
- The task's reading order is now task, constitution, architecture, plan, page files, with the
  brief and the machine files as references; `INDEX.md` and `MANIFEST.json` describe each new
  file; the completeness check counts a screen without a page file as a gap; the guard and
  `factory.GENERATED` protect `spec/` and `session.json`. The folder is `spec/` because the
  repository ignores every `build/` folder.
## 2026-11-19 — Large sites: screens as templates, one shared layout, and what the network shows
A recording of a shop with many product pages made one screen per product, and most of what
the HAR holds beyond the endpoint contract never reached the package. Audited against a
checklist (request, response, timing, streams, static files, third parties, errors), then
closed, on a new fixture shop (`tests/fixtures/shop`: a mega-menu, categories with infinite
scroll, a search with facets, 30 products from one template with a gallery and a variant
picker, a cart, GraphQL reviews, a multipart photo upload, a server-sent price ticker, CSRF,
caching, gzip and a third-party analytics origin):
- **Templates** (`labctl.templates`): addresses that differ in one segment, with pages of one
  structure or many links of one repeated element, are one screen (`/p/{slug}`), applied
  through `sanitize.template_rules` while the brief and the package are made. New
  `apps/<app>/templates.json`: instances, varying parameters, data fields with one synthetic
  seed row per instance (synthetic; a field an approved `public-data.yaml` declares public
  keeps its recorded value), and
  each page's breadcrumbs, gallery, variant picker, recommendation blocks, facets as code
  lists, sort, pages, lists loaded in pages, category tree; the navigation hierarchy and the
  cart and wish-list state.
- **Shared layout**: `navigation.json` `shared_layout` lists the header, menus (a mega-menu as
  a tree) and footer once; elements that belong to it say `layout`, and page files leave them
  to `ARCHITECTURE.md` *Shared layout*.
- **Network** (`labctl.netfacts`): `network` in every endpoint file (headers by name, CSRF
  token and its source, multipart, GraphQL, JSON-RPC and SOAP bodies, query roles, caching,
  compression, CORS, paging envelopes and infinite scroll, response times, which earlier answer
  gave each value sent, retries) and `api/network.json` (page-load call order, redirect chains,
  streams, static files, third parties by purpose, errors). Page files and `ARCHITECTURE.md`
  carry them; GraphQL examples keep the document, literals removed.
- **Privacy**: no new value is carried; the leaks the fixture found in existing outputs are
  closed (record names in replay locators, draft flows, "one of" column formats and code-map
  file names; slugs in `behaviours.json`). `templates.json` is a generated file for the guard.
## 2026-11-18 — The build package carries the API, the navigation, every action and the sign-in
What a coding agent gets was measured on two recordings (the two-application fixture, and a new
help-desk fixture whose pages are filled by a JSON API), then the gaps were closed.
- **API catalogue** (`labctl.apicatalog`): `apps/<app>/api/catalogue.json` and one file per
  endpoint the pages called. Method, address pattern, path and query parameters with the
  control that sets each, the content type, how the call was authorised by name only (a
  cookie's name, the scheme of a header), a schema of the request and of each answer by status,
  the screen and action that sent it with step numbers, where the page showed the answer's
  fields, and every recorded call in order with synthetic values. The examples are one world:
  a listed record is the row of `db/seed.json` with that identifier, a typed value is the one
  `steps/*.values.yaml` gives the step, the record a call created is the one later calls read,
  change and delete. Kept as recorded: field names, code lists, identifiers the application
  generated, paging numbers, the application's own error messages. `apicatalog.leaks` checks
  the files against every other value of the HAR, and a file that fails is not written.
- **Search, filter, sort and paging** are stated from the answers: which parameter filtered,
  which ordered the rows and how, the page size, checked on every recorded answer.
- **Field map**: which table and column each API field and screen element is, with its basis.
- **Navigation map and actions** (`labctl.navmap`): `apps/<app>/navigation.json` and two brief
  sections. Every way from screen to screen with its element and text, redirects, modal
  dialogs, in-page tabs, iframes (this application's or another origin's), popup windows,
  links seen and not visited with what answers there, and every interactive element of every
  screen with what happened each time it was used, or "seen, not used".
- **Sign-in and session** (`labctl.signin`): what was observed (the call, each attempt, the
  cookie by name, sign-out, what a request without a session got), and the requirement that
  users and sessions are in the database: `app_users` and `app_sessions` in `db/schema.sql`,
  and in the seed the user the replay signs in as, the password as a PBKDF2 hash. This
  replaces "accept any non-empty user name and password".
- **No flow, no automation**: `labctl brief` no longer refuses an application that has only a
  recording. The scope is then every screen and action recorded, in recorded order, and the
  brief says so in a new *Scope* section (`labctl.contract`).
- **The task** (`BUILD_TASK.md`) is rewritten: what to build and what done means, the exact
  reading order, technology and versions, nine rules, how to report assumptions, the known
  gaps. The package gains `INDEX.md` (every file, one line each), a `purpose` per file in
  `MANIFEST.json`, and a completeness check (`labctl.builder.complete`): a screen without an
  actions list, an endpoint without a schema, an undefined navigation target, a table without
  seed rows, a typing step without a value. Gaps are written into the package and warned
  about; `strict=True` or `TWINLAB_PACKAGE_STRICT=1` refuses the package.
- **Defects the JSON fixture found**, fixed: a table with no columns in `db/schema.sql` (an
  answer that only wraps an object); a same-origin iframe's page left out of scope; a new
  record's identifier taken from the page after sign-in instead of the creating call's answer,
  so the seed held the record the recording made; the option a select chose not found when the
  form was sent as JSON; a filter that found other rows on the seed than in the recording
  (seeded rows now keep each record's own code values).
- **Blind rebuild**: a coding agent given only the unpacked help-desk package built the
  application (FastAPI, SQLite). The recording replayed on it: 58 of 58 steps, no difference,
  3 of 3 acceptance cases. Two record bindings of the replay were filled by hand from the
  package. The builder's questions led to five further fixes; what stays open is listed in
  `docs/06-evidence.md`.
## 2026-11-18 — Angular proven on a real application: the first reference for the certification matrix
- `reference/web/caseboard-angular/`: Caseboard, a small real Angular 21.2 application with
  synthetic data (sign-in, a route guard, a routed list with search and paging, a lazy-loaded
  edit form on reactive forms with required-field messages, a select, a check box, radio
  buttons and a date, a modal dialog). Its production build is committed as `site/` and served
  by `serve.py` (Python only), so no npm is needed to run it or to test with it.
- Three gates, run by `python reference/web/caseboard-angular/certify_local.py` and by
  `tests/test_reference_angular.py`: DETECT (angular, 21.2.26, `ng-version`), CAPTURE (the 34
  actions of the hand-written `expected-actions.yaml` are all in a recording of `flow.py`),
  SELF-REPLAY (34 steps replayed against a fresh instance, 26 compared, none failed).
- Fixed on the way, each found by the replay failing (`tests/test_recorder_spa_forms.py`, a
  page without a framework): an address the application gives itself while it starts (Angular's
  router, a guard's redirect) was written as a step before the load of its own page, and the
  replay waited for it on a page not opened yet; the step before typing was snapshotted when
  the typing ended, with the next field already marked as edited; and a field left after typing
  (Angular marks it visited, a required field shows its message) was compared with a replay
  that never left it: a `type` step now says `left`, and the replay leaves the field too.
- Left open: one Angular version, zoneless, no server-side rendering, no Angular Material, no
  HTTP call; the replay opens the address the guard led to, not the one typed; a script that
  clicks in one instant is snapshotted before Angular renders what leaving a field changes (the
  flow clicks like a person). The harness (`labctl certify`) is not part of this change.

## 2026-11-18 — Verdict gates from the audit: no silent PASS
- **A misspelled category no longer hides a case.** A required case with an unknown category
  is counted under behaviour and named; every required result decides the verdict whatever
  category it carries.
- **One id used twice in `visual.yaml`**: both states are kept, the second is BLOCKED. A result
  is never stored over another, so a later pass cannot erase a failure.
- **Status and verdict agree.** A replay whose status is not pass cannot report
  PASS WITHIN DECLARED SCOPE.
- **Assurance tier beside a PASS.** `VERIFIED COMPLETE` needs an expected action list
  (`--expect`) with nothing missing and no required case with `basis: assumed`; otherwise the
  report says `DECLARED CASES ONLY` and why. `labctl behaviour fidelity --verified` exits 1
  unless the tier is `VERIFIED COMPLETE`. The portal report shows the tier under the verdict.
- `tools/audit_replication_gates.py` is the audit's probe; it exits 0 now. Tests:
  `test_audit_gates.py`.

## 2026-11-18 — Link check: where the links nobody clicked really lead
- `labctl evidence check-links <recording> [--yes] [--max N] [--delay-ms M] [--storage-state F]`
  (`labctl/linkcheck.py`) collects every link target the snapshots hold and the recording never
  visited (`<a>`, `<area>`, GET `<form action>`, `<iframe src>`, `window.open`/`location.href`
  in inline handlers), one per address pattern, and asks the real application with HEAD (GET
  when HEAD is refused, and to read an HTML page's title). Opt-in: without `--yes` it only lists
  the plan; nothing else in the lab runs it.
- Never asked: an address that looks like it changes something (logout, delete, cancel, approve,
  pay, ...), a query carrying an action, a token or session id, `mailto:`/`tel:`/`javascript:`/
  `data:`, downloads, POST forms, any origin the recording did not use. Each skip has its reason.
  One request a second by default, at most 50 links, a timeout per request, stop on 429 or 503.
  Anonymous unless the recording's saved browser state is passed; no cookie value, token or
  body is written.
- `<recording>/link-check.json` says per link where it was seen, the final status and redirect
  chain (as patterns), sign-in, content type, masked title and heading, and whether it has the
  structure of a recorded screen. An unvisited HTML page is kept as a sanitised snapshot
  (`link-check/pages/`) after the look's data checks; a failed check keeps only title and heading.
- The navigation map uses it: "leads to a page *Order history* not recorded", "leads to the
  sign-in page", "missing (404) in the real application: keep the link, answer 404". The brief
  lists pages that exist but were not recorded; their snapshots travel as
  `apps/<app>/unrecorded/*.html`, never mixed with recorded screens. Without the file nothing
  changes. Tests: `test_linkcheck.py`.

## 2026-11-18 — A public shop trial: an API on another origin of the same site is the app's own
A scripted recording of a public demo shop (pages on `www.`, JSON API on `api.` of the same
site) gave a package with no endpoint, no table and "the data lives in the browser": every API
call was left out as another host's. Fixed, with tests (`test_har.py`, `test_seedplan.py`,
`test_pagerules.py`):
- `har.api_hosts`: a host of the same site as the pages (`har.site_of`, an approximation of
  the registrable domain; never an address with a port, an IP address or a single label) that
  the pages only called an API on is analysed as the application's own API, in the model, the
  step facts, the seed plan and the API catalogue. The brief says so as an open question; a host
  of another site is still left out.
- A request to another origin carries only the page's origin as its Referer (the browsers'
  default policy), which put every call on screen `/`. `har.referer_path` ignores such a `/`; the
  call goes to the page open at the time.
- A step on an id the page gives several elements (`a#itemc` on each category link) now gets
  its `target` (the role and name that tell them apart).
- A script under `node_modules/` or `bower_components/` is a library, named by its package, not
  analysed as the application's code.
- Found and left open (see the trial notes): a DynamoDB-style `Items`/`LastEvaluatedKey`
  answer is not read as a list (a table `lastevaluatedkey`, no seed rows); synthetic examples
  lose the shape of UUIDs, `name=value` cookies, URLs and small code lists; an asset name with
  `512x512` is masked as a record number (the logo is not carried); product photos are carried
  or not depending on where else they were shown; a `window.alert` text is not recorded.

## 2026-11-19 — Demoblaze trial: fourteen package defects fixed; public catalogue data on request
- Interface kept, data masked whole: static files of the application's own origin named by a word
  keep their file names in the snapshots and the look (`sanitize.mask_asset_url`; the logo
  `blazemeter-favicon-512x512.png` and `Lumia_1520.jpg` were `999x999`/digits and not carried);
  a media player's control texts are the library's (video.js: `Play Video`, `Current Time`);
  capitalised words of prose answers are no names (`Get in Touch`); an answer value shown whole
  (`Samsung galaxy s6`) is masked whole. Real data next to each stays masked (tests).
- Browser dialogs keep their message: recorded (masked as any message), carried in the steps,
  `rules.json` `dialogs` and the brief, read from the application's own scripts when the
  recording has none (`Product added.`, `Thanks for the message!!`), and compared by the replay.
- Envelope keys in any case (`{"Items": [...], "LastEvaluatedKey": ...}`); a remote-procedure API
  gives its tables through POST answers (`entries` read by `/bycat` and `/view`; `cart` from
  `/viewcart`, `/addtocart`, `/deleteitem`).
- Synthetic examples keep shapes and meaning (UUIDs, `user=<uuid>` cookies, URLs at
  `example.test`, the `cat` code list, carried photo file names) and one record has the same
  values in every endpoint; an empty answer is "empty body".
- Requests belong to the last step logged up to 150 ms after they started (one rule,
  `stepfacts.step_at`, the seed plan's), never to a dialog; a page change the click caused is
  folded into the click's effect (no more `POST /bycat` on a carousel click). This was also the
  cause of the two intermittent `test_pagerules` failures (under load a select's change event is
  logged a few ms after the fetch it starts); they pass at c561089 on an idle machine.
- Cookies by name, setter, attributes, use and value shape in the brief (`labctl.cookies`).
- Modals: open only when their markup shows it (`behaviour.dom.shown`), with opener, closer and
  their own title; carousel controls are the carousel's; a sign-in screen needs a shown password
  field. The screen picture is the fullest snapshot with no dialog open, each dialog the
  recording showed open has its own picture; gradients and background images are theme tokens;
  SweetAlert is named by its own file; a regenerated `app.yaml` updates its generated parts
  (keeps `run:`, approved levels, `lab:`).
- Public catalogue data (`public-data.yaml`, `labctl evidence public-data` drafts it; off by
  default, nothing used without `approved_by`): declared endpoints, fields, regions and image
  prefixes are carried as recorded in pages (not in data cells), API examples, seed rows and the
  look, and the brief and the task say who approved it; secrets, cookies, e-mail addresses and
  typed values are refused inside declared data ([06-evidence.md](06-evidence.md), Privacy).
- Left open: `/deleteitem` answered 16 bytes the page did not read, so its body is described as
  not recorded (not as empty); a dialog's picture is drawn by opening it in the screen's snapshot.

## 2026-11-18 — The build package carries the look of the application
- A rebuilt application did not look like the original: a builder got value-free page snapshots
  and nothing else. Style sheets delivered as files were not carried, no font was named, no
  picture was shipped (the recorder's screenshots show real data), and some texts of the
  interface came out as `Xxxx`.
- `labctl/lookfeel.py` writes `apps/<app>/look/` with the brief, and the package carries it:
  the application's style sheets as delivered (`css/`), design tokens (`theme.json`: colours,
  fonts per role, control sizes, spacing, layout), one picture per screen (`screens/`), the
  interface's images (`assets/`), every text marked literal or placeholder (`texts.json`) and a
  manifest. The brief has a section *Look and feel*; the build task names the files and adds
  the comparison with the pictures to its checks.
- Pictures are rendered, never recorded: the value-free snapshot is drawn again in a headless
  browser at the recorded window size with the carried style sheets. The tokens are the computed
  styles of that rendering, so older recordings work and the recorder is unchanged.
- Privacy rules. Style sheets are code and kept as written; addresses become paths inside
  `look/` or are removed; e-mail addresses, record numbers and recorded names in strings and
  comments are masked. Fonts are named with their source and a generic fallback, and copied
  only with `TWINLAB_LOOK_FONTS=bundle`. Images are carried only when they are static files of
  the application's own origin that the interface uses; avatars and attachments never. A check
  compares every carried text and the text of every picture with what the recording shows as
  data: one hit and no look is written; `labctl build package` refuses a look that fails it, or a
  picture Twin Lab did not render. `TWINLAB_PAGE_TEXT=strict` carries no look;
  `TWINLAB_LOOK=off` switches it off.
- Interface text that was masked by mistake in interface mode, measured on recordings of the
  Angular reference application and the multi-application fixture, each fixed with a test: an
  option or radio label the data also shows (`Open`, `Low`) is kept when it repeats the code
  its own markup gives it (`value="open"`, `for="priority-low"`); the application's name in an
  element with an id is kept when the page's own title shows it; `Signed in as Xxx` keeps
  `as`; a tooltip, `aria-label` or placeholder on a control is read as a label; an icon font's
  glyph in a style sheet string stays. No rule was loosened for anything else: a sentence in
  an element with an id that the interface says nowhere else stays masked.
- Left open: committed applications under `apps/` are not regenerated here (their pages were
  written before interface mode and still show `Xxxx`); styles added by script without a
  `<style>` element, addresses inside inline styles, and hover or focus states are not
  carried; one picture per screen, not per state; the check reads the text a picture is drawn
  from, it does not read the pixels.

## 2026-11-18 — WPF recorded and verified on a live application
- WPF was covered by fixtures only. `reference/desktop/CaseDeskWpf/` is CaseDesk in WPF
  (.NET 6, no NuGet package): the same screens, behaviour and control names as the WinForms
  one, with a `flow.py` that drives the same scripted flow. Three live tests record it, check
  the steps, the `uia:AutomationId=...` locators, that no typed value is stored and that the
  toolkit detected is WPF (certain, from `FrameworkId WPF`), replay it with `desktop verify`
  to a passing report, and find the deliberately wrong build. Run:
  `TWINLAB_DESKTOP_GUI=1 python -m pytest tests/test_desktop_gui.py -m desktop_gui -k wpf`.
- Fixed in the recorder, each found on the live app: a click on a button, check box or menu
  item was recorded on the TextBlock that draws its caption, with no locator when the click
  closed the window; a menu's drop-down and a combo box's list (WPF `Popup` windows) were
  recorded as windows opening and closing, with the menu steps put in them; the combo box's
  items were not read; a check box gave two toggle steps, the second without a state; Enter on
  the default button gave a key step and a click (now one key step that names the button as
  `invoked`); typing in a PasswordBox gave no step at all (now a key pressed while it has the
  focus does; the key is not kept); the snapshot of a window that was closing was kept without
  its content.
- Fixed in `verify`: Enter was not replayed, because a WPF control has no window of its own
  (the key is now posted to its top-level window); the items of a menu that was open in only
  one of the two trees were reported missing or extra.
- `Driver.keys` types with real key presses, only while the control has the keyboard focus.
- Left open (listed in `09-desktop.md`): a password set by a pattern call or pasted is not
  recorded; a row the application selects itself is recorded as a `select` step that `verify`
  skips; UWP/WinUI, Win32/MFC and JavaFX are still fixtures only.

## 2026-11-18 — Roles and session lifecycle, recorded and replayed on a local fixture
- A fixture application with two roles (`lab/labctl/tests/fixtures/roles/roles_app.py`): sign-in,
  a clerk and a supervisor, a page and a menu entry for the supervisor only (a clerk gets a
  refusal page with HTTP 403), a cookie session that can end, sign-out; `serve(faults=...)` gives
  a replica wrong in one named way. `tests/test_roles_and_sessions.py` records and replays it:
  the supervisor's recording passes against a correct replica; a menu entry shown to every role
  fails at the first page with the menu, and a clerk let into the supervisor's page fails at that
  page; the refusal page is compared like any other; a session that ends after a number of pages,
  and signing in again, replays, and a replica whose session never ends fails at the redirect.
  No file of a recording holds a typed user name or password; the replay types the bindings'.
- Fixed in the replay: Back after sign-out was not made as Back. The source answers the page gone
  back to with sign-in, so the recorded history step carries the sign-in address, which matched
  no entry of the replay's history; the replay loaded that address, and a replica still showing
  the protected page passed. With one direction possible the step is now made that way and the
  replica must arrive at the recorded address by itself.
- Left open: the HTTP status of a page is not compared (a refusal answered 200 passes if the
  page is the same). A session ended by elapsed time, or from outside the browser, between two
  steps is not replayed (a strict xfail says so). Single sign-on, multi-factor sign-in and token
  renewal are not tested.

## 2026-11-18 — A page load right after a submit is that submit's result
- Re-running the public-site trial recording showed one of its findings still failing on that
  recording: source against source, the two submit steps were compared with the form snapshot
  while the replay was already on the result page. That recorder named the click behind a submit
  but not the page load the submit caused. A load within half a second of a submit, in the same
  tab, is now attributed to it, so the click and the submit are compared at the loaded page.
- Result on the trial recording: source against source 30 of 30, 0 failed; against both replicas
  with a map and a declared scope 30 of 30, 0 failed, 24 differences excluded. The table is in
  `docs/multi-app-recording-trial.md`.

## 2026-11-18 — Portal: replica addresses for a replay across applications
- The page of a recording across applications now lists each included application with the
  address it was recorded at and a field for its replica address, filled from the lab port.
  Type another address to replay against a replica running elsewhere; only a lab the replay
  uses is required to be running.
- A replica address must be a plain `http(s)://host:port`. An address the recording was made
  at is refused: a replay never touches the real application.
- Desktop tests: the `Windows fatal exception: code 0x8001010d` thread dumps in a
  `TWINLAB_DESKTOP_GUI=1` run are not a recorder fault. UI Automation raises and handles that
  error inside `ElementFromPoint`; pytest's fault handler prints every such exception. Measured
  on the WinForms flow: 12 of 12 element lookups succeeded in 3 to 9 ms. `labctl desktop record`
  does not print it. Run the tests with `-p no:faulthandler` for a quiet log.

## 2026-11-18 — Seed data and replay values in the build packages (the blind rebuild's data gap)
- **One planner for the seed and the replay values** (`labctl/seedplan.py`, run by `labctl brief`
  and `labctl evidence build-all`). From an app's recordings it writes `apps/<app>/db/seed.sql`
  (the schema's dialect, every name quoted; the same rows in `db/seed.json`): rows numbered from
  the lowest to the highest identifier the lists showed, columns in the observed shapes and code
  lists, no rows for a table whose first record was written during the recording, and the rows a
  recorded search found carrying the synthetic word the replay will search for. Beside it
  `apps/<app>/steps/<recording>.values.yaml`: per typing or choosing step the synthetic value
  the acceptance replay types and the recorded outcome; the searches (rule, mode, rows, pages,
  the seeded rows that match); a refused and an accepted input per validation message with a
  hypothesis only where attributes, an empty refusal or the message's example support it; the
  display format of each shown field (two decimals, date pattern, closed word lists, "as typed",
  option code vs label); computed values marked "rule unknown"; values shared with sibling apps.
  A search counted 12 rows over 3 pages finds 12 over 3 pages on the seed; a "no results"
  search finds none; a new record's number is the highest seeded + 1 in every package that
  uses it. Words are checked against every word of the evidence; no typed value is ever read.
- `build-all` writes the values into each part's `bindings.yaml` and the whole recording's
  (marked `generated:`; a value a person writes is kept, and generated values do not trigger a
  re-split), and a draft `acceptance.yaml` where none exists: required only for what the package
  states, computed values excluded with the reason.
- The brief gains *Seed data and replay values*, *Paths the pages link to that the route list
  does not name* (e.g. `GET /orders/new/customer`, `GET /logout` as recorded), *Links that leave
  the scope* (keep the `href`, answer with a 303 to the first in-scope screen; BUILD_TASK and
  AGENTS.md now say the same), *Checkbox fields and their columns* (posted `vat=1`, BOOLEAN
  column), the `<tl-frame>` capture explained (brief and snapshot header), unambiguous identifier
  wording ("lowest seen, highest seen; 12 different values, not every number in between"),
  repeated requests shown as `×2`, option codes as the page posts them (model code lists use
  them too), and per step the ranked locator the replay finds a non-id selector by (`target`).
- Run: `labctl evidence build-all <recording> --proposal <file>`; tests:
  `tests/test_seedplan.py`, `tests/test_multi_app_flow.py` (seed reproduces the recorded counts,
  values shared across packages, formats and validations, a replay of the source code holding
  only the seed with the generated values passes). Trial: `rebuild-trial/_work/build.py`, proofs
  `proof.py good|seeded|unseeded|...`, commands for the next blind run in `rebuild-trial/JUDGE-RUN.md`.
- Left open: the matching rule of a search is a hypothesis from one recording (contains vs whole
  word, case); row order in lists is not stated; computed values (prices, VAT) stay unknown and
  unjudged; a table listed only without identifiers is seeded with as many rows as were listed
  before the first change; the acceptance *template* (`labctl behaviour acceptance`) still writes
  TODO cases a person completes; no CLI of its own (`labctl brief` and `build-all` run it).

## 2026-11-18 — Desktop applications, phase 1: record, model, brief, package, verify
- **`labctl desktop`** (`lab/labctl/labctl/desktop/`, [09-desktop.md](09-desktop.md)) records a
  person using a Windows application: UI Automation events, low-level mouse and keyboard hooks
  and window polling for WinForms, WPF, UWP/WinUI, Win32/MFC and JavaFX; the **Java Access
  Bridge** (ctypes, loaded per run, no `jabswitch`) for Swing/AWT. Each step has its window, a
  canonical locator (`uia:AutomationId=txtCase;ControlType=Edit`, `jab:Name=Save;Role=push
  button`) with ancestor path, fallbacks and UiPath/pywinauto/WinAppDriver forms, and the
  value-free control tree after it. Typed values and shown data keep only their shape.
- Toolkit detection (FrameworkId, window classes, module names, .NET runtime folders, version
  resource); Electron is sent to the web recorder; Delphi/VCL, Qt, VB6, PowerBuilder and
  Citrix/RDP are detected and reported not supported.
- Desktop recordings feed the app model (windows as screens, controls with UIA properties,
  menus, grids as candidate tables, drop-down code lists, message boxes as rules), the brief
  ("Desktop windows and controls"; SQLite by default) and a desktop build task (same toolkit and
  UIA properties, `build.ps1`, `dotnet publish` folder and zip, no Dockerfile). `labctl desktop
  split` gives one evidence set per executable; `verify` replays a recording against a built
  app and writes a fidelity report the readiness gate reads (new checks `changed`, `ambiguous`).
- Reference apps `reference/desktop/CaseDesk` (.NET 6 WinForms) and `CaseDeskSwing` (Java 11
  Swing), each recorded by a scripted flow and verified against its own recording; a wrong
  CaseDesk build is caught (missing, extra, ambiguous). The demo app built from the package
  (`apps/casedesk/`) comes on its own `build/` branch. Live tests run only with
  `TWINLAB_DESKTOP_GUI=1`.
- The R12 capability table now says what covers desktop: web DOM replay does not; `labctl
  desktop` does for WinForms, WPF, UWP, Win32 (UI Automation) and Swing (Access Bridge).
  Citrix/RDP, Delphi, Qt, VB6 and PowerBuilder are out of scope by decision.
- Proven live: WinForms (CaseDesk) and Swing (CaseDeskSwing). Left open: WPF, UWP/WinUI,
  Win32/MFC and JavaFX, and the Electron hand-off, are tested on fixtures only, not on a live
  app; WinForms raises no Invoke events (clicks come from the mouse hook only); snapshots are
  taken after a batch of quick actions; Java typing and selection are polled.

## 2026-11-18 — What a builder receives: fixes from the rebuild trial
- **Interface text is kept, data is masked.** Page snapshots in a brief now keep the application's
  own labels, headings, buttons, options and messages verbatim, and mask table cells, input
  values, identifiers, dates, amounts, e-mails and names. `TWINLAB_PAGE_TEXT=strict` masks all
  text and styles as before. Typed values are never kept in either mode. `data-testid`,
  `data-field` and `target` are no longer masked.
- **The brief says more**: a fields table per screen, messages per screen, list columns, what each
  recorded action did, identifier patterns and how the next one is formed, the sign-in rule,
  which element's value travels to which field of the sibling application, and the address
  variable of each sibling (`<APP>_URL`).
- **An embedded frame from another origin** is in scope as a stand-in served by the app itself;
  its requests are not the app's routes or tables. Pages are no longer listed as tables.
- **Database**: identifiers are quoted per engine with a reserved-word list. With no database
  chosen at onboarding and none evidenced, the brief and task use SQLite and say it is a
  default, not evidence.
- **The package** now holds the app's step list (no values), its sequence and draft flow; the task
  mentions screenshots only when present, and has a section for working from the zip alone.
- A format rule is written only when a real format was seen.
- Left open: recorded pictures are not shipped (they hide typed values only, not shown data);
  seed counts, prices and valid credentials cannot be learned from a recording.

## 2026-11-18 — One replay across applications and tabs; trial findings; R09, R10, R12
- **The recorder sends each action the moment it happens** and numbers it on arrival. Before, a
  step ended by a navigation was sent while the page unloaded, and newer Chromium drops that
  call: clicks, checks and Enter steps were lost (this failed CI on Linux). The blank first page
  and a `window.open` popup are also handled by the recorder itself.
- **One replay across applications and tabs.** `labctl behaviour fidelity <recording> --map
  source=replica` (repeat per application), or a `replicas.yaml` beside the recording; a single
  `--base-url` still works for one application. Tabs are opened, matched, switched and closed as
  recorded. A request to a source or unmapped origin is refused and reported; a step on an
  unmapped origin is BLOCKED and names the `--map` to add.
- **Splitting keeps references.** Steps are renumbered per application with a map, a reference
  to a step in another application is kept as `{app, step}`, and its value travels in the part's
  `bindings.yaml` under `outside:`. Acceptance and picture files are split too.
- **Observation boundaries.** A click or submit that loads a page is compared at that page, also
  when the page is slow or redirects; Back and Forward are replayed as such.
- **Setup events** (blank and browser pages) are counted the same way in the proposal, the parts,
  the sequence file and replay coverage.
- **Reviewed scope** (`scope.yaml`): elements, screens or origins left out, each with a reason and
  who approved. Excluded differences get their own report section and never count as passed.
  `labctl behaviour scope <recording> --from-brief` drafts one, unapproved.
- **R12 capability table** (`labctl/behaviour/capabilities.py`), each row proved by a fixture
  test. Supported: same- and cross-origin iframe, open shadow DOM, upload, download, popup,
  dialogs, late content, select by index, slow POST-redirect with history, multi-app replay.
  Partial: service worker (blocked during replay and reported), second engine. Unsupported with
  a message: closed shadow DOM, drag and drop, canvas, desktop.
- **R09 coverage matrix**: `labctl coverage <app>` and a portal page give each requirement one of
  VERIFIED, EVIDENCED, ASSUMED, UNOBSERVED, UNSUPPORTED, EXCLUDED or BLOCKED. **R10**: portal
  pages for a recording, a replay report, coverage and the declaration files.
- Left open: a popup that misses its start script is hooked only before the next action; element
  exclusions match tag, id and class, not ancestry; recording waits up to 15 s on a page that
  polls the network; the portal's replay button uses `replicas.yaml` or one base URL, it has no
  form for the map.

## 2026-11-18 — Several applications in one recording, end to end; gaps R05, R06, R08, R11; OpenShift by hand
- **The recorder follows every tab and popup** on one numbered sequence; each step names its
  tab, opener and origin, and opening, switching and closing a tab are steps. One HAR covers all
  tabs. Each step can carry browser storage keys with masked shapes, cookie names and IndexedDB
  names (never values). The recording notes the browser, viewport and the masked areas of each
  picture. Typed values are also removed from step addresses and storage keys.
- **From a recording to a package per application.** `labctl evidence record-across <name>
  <url>`, then `labctl evidence build-all <recording>`: it proposes the applications and stops
  for review; with `--proposal <file>` or `--yes` it writes each application's evidence, a draft
  flow to review, its brief (an existing app is enhanced, not replaced) and its build package,
  and a summary of what a person still has to do. Each brief and build task says which
  application comes before and after it in the flow. The portal has a Recordings page to review
  the split and create the applications; an app page links its recording and sibling apps.
- **R05 acceptance cases** beside a recording (`acceptance.yaml`): text, count, order, value,
  flags, address, browser storage and database outcome, each PASS, FAIL, BLOCKED or SKIPPED.
  **R06 browser-data baselines** (`baseline.yaml`): replay starts from a declared state in a fresh
  context and never sends a cookie of the real site to the demo app. **R08 pictures**
  (`visual.yaml`): pinned viewport, declared exclusions, exact mode. **R11**: a replay result
  names what it compared and goes out of date when the app, bindings, cases or baseline change.
  The verdict is PASS WITHIN DECLARED SCOPE, FAIL or BLOCKED; no executed case is BLOCKED.
  `labctl behaviour acceptance|baseline|visual <recording>` write templates.
- **OpenShift without a pipeline.** `labctl ocp render <app> --manual --out deploy/ocp/<app>`
  writes numbered manifests and a README of the manual steps; images are built in the cluster
  from the branch. `--all` renders every app; `labctl ocp check <dir>` validates a folder
  offline. `deploy/ocp/` holds showcase, qtt and loandesk. SQLite and browser-only labs run as
  one pod. Nothing here has been applied to a real cluster.
- Left open: replay of a recording across several tabs or applications in one run; attach mode
  cannot see what a tab did before attaching; downloads, uploads and service workers; R09, R12.

## 2026-11-17 — Replication gaps R01–R04 and R07 (`docs/replication-gaps.md`)
- **Recorder:** key presses that are not text (Enter, Escape, Tab, chords) and double-clicks are
  steps of their own; checkboxes are `check`/`uncheck`; typing is flushed before a submit, key
  press or navigation. Each step keeps ranked locators (test id, id, role and name, label, link
  text, structure) with their match count. Typed values are held in memory only and are removed
  from snapshots, the HAR and screenshots.
- **Fidelity replay:** a target must match exactly one element, or the step is BLOCKED with
  what each candidate matched; per-record controls bind to the record, not its position;
  values come from `bindings.yaml`, one per step (`labctl behaviour bindings <recording>` writes
  the template); a missing value blocks. The report counts expected, replayed, failed, skipped
  and unresolved actions and is `pass` only when every step ran. Readiness treats BLOCKED as
  blocking. Older recordings still load and replay.
- **Where the data lives.** A recording says `browser`, `server` or `unknown`. A browser-only
  app's brief has a "Browser storage" section and no invented tables or database; its build
  task says so; the lab marks data controls and database conditions "not applicable".
- Left open: R05, R06, R08–R12; elements inside shadow roots get no ranked locators; the
  recorder does not yet capture storage contents.

## 2026-11-17 — Product review: business outcome, readiness freshness, guided pages
From `docs/product-review-2026-11-17.md`.
- **Setup per application** with a test-data step; progress reads the lab's baseline and
  sessions; an update with no recorded columns fails a column-restricted rule.
- **Business outcome per automation.** `outcome:` in `automation.yaml` lists acceptance cases:
  `changes` (the run must make these changes, optionally `set:` final values), `read_only`,
  `rejected` and `failed_run` (the run ends unsuccessfully and leaves data unchanged). It is
  judged apart from `expects:`, which still says only what a run may change. `--case` picks one
  on `harness run`, `e2e` and `session verdict`; NOT_MET fails the run; values are checked only
  when the lab recorded them, otherwise the case is CANNOT_CHECK. `uipath-case-update` declares one.
- **Readiness says whether it is still current.** A check records what it was checked against
  (app files, brief, schema, database choice, data baseline, automation). Pages show "still
  current", "out of date" with the reason, or "cannot be confirmed" when the lab is stopped; only
  a current READY counts. A rebuild done outside the portal is caught through changed files.
- **Guided path first.** App-page controls for later (reservations, copies, fidelity, database,
  Excel data, packs, baselines, parity) sit under "More"; a "Next step" line shows until the
  first rehearsal is done. The dashboard reads labs concurrently with a 5-second cache.
- Left open: proving fidelity with real UAT evidence needs recordings from the office.

## 2026-11-17 — First build by a Claude agent; replay of a person's recording
- A Claude (Sonnet) agent built `loandesk`, a loan search on SQLite, from the build package
  alone. What the trial showed is fixed here: the task names the database chosen for the demo
  app (`lab.database`), not the real system's; it says the schema file is in the real system's
  dialect and must be translated; it says what to do with an in-scope element that leads out
  of scope; a failed Claude session shows its last message in the build status.
- **Fidelity replay works on a recording a person made.** A masked record id
  (`#loan-L-999999`) is matched to the first element of that shape; `--value 6:#field=text`
  gives the value typed at one step; a field whose value was supplied is not compared by
  format; a per-record element counts as the same when both pages have at least one.
- Left open: the standalone `claude` command needs an account with credit; a step that lands
  on an out-of-scope screen still shows as different.

## 2026-11-17 — Any coding agent builds; several apps in one recording; SQLite
- **One recording, several applications.** `labctl evidence split <recording>` proposes the
  applications in a recording (by the site each step's page belongs to; sign-on pages are left
  out; an embedded iframe is listed so it can be separated), writes a proposal file a person can
  edit (rename, merge, split one host by path, drop), and with `--apply` writes one recording per
  app under `evidence/<app>/` plus the whole sequence across apps in
  `evidence/_sequences/<recording>.flow.yaml`.
- **Wider technology detection.** A recording now names the server technology (PHP, Java with
  JSP/Struts/JSF/Spring, .NET Web Forms/MVC/Blazor, classic ASP, Python, Ruby, Node, ColdFusion,
  Oracle APEX/Forms, SAP, Siebel, ServiceNow, Salesforce), the web server, client libraries and
  UI suites (jQuery, Bootstrap, PrimeFaces, Telerik, Kendo, DevExpress and others), each with
  the marker that showed it and a confidence. Markers are header product names, cookie names and
  file names: never a value. The brief prints them; a part with no marker says so.
- **Tables from HTML grids.** Server-rendered pages with no JSON now give tables: a data grid's
  headers become columns with types read from cell shapes, detail forms add columns, selects and
  radios become code lists. These are the weakest evidence, so DDL or JSON wins.
- **The build package is for any coding agent.** The zip holds `BUILD_TASK.md` (was
  `DEVIN_TASK.md`): same technology, same selectors, tables and data only. When the app is
  already built it says what the new brief adds. `labctl build package|start|status|attach-pr`;
  the builder is `manual` (default), `claude` (a headless Claude Code session in a worktree on
  `build/<app>-<digest>`; commits and opens a pull request only with `--pr`) or `devin`, set by
  `builder:` in `twinlab.local.yaml` or `TWINLAB_BUILDER`. The guard treats `build/...`
  branches as AI-built.
- **SQLite** is a third built-in database (`labctl db use <app> sqlite`): one file on a volume
  the app and the lab agent share, no database container or port. Load, reset, baselines, row
  changes per session and copies work. Slow-database conditions cannot apply and are refused or
  reported as unavailable; bots get no database address.
- **Data shaped by what was recorded.** `labctl data observe <app>` writes
  `apps/<app>/db/observed-profile.yaml`: code values seen (kept as seen, listed under
  `verbatim:` for review), and for everything else only its shape. `labctl data generate
  --from-observed` follows it and adds the rows a recorded flow opens. Names, emails, phone
  numbers and long identifiers are never copied.
- Left open: a live build by a Claude session has not been run; desktop and mainframe capture;
  SQLite on OpenShift.

## 2026-11-16 — The "partly" items closed; end to end in one command
- **A lab copy per run.** `labctl lab copy <app>` starts `<app>.run-1`: its own database, agent
  and app on free ports, from the app lab's baseline (or `--data current|app|<pack>`), with the
  app's image. `labctl lab copies`, `labctl lab drop`. `harness run --copy` makes one for the run
  and drops it; `bindings` and `conformance` take `--on <app>.<copy>`. Copies need Twin Lab's own
  database (postgres or sqlserver), not a DBA's server.
- **Fidelity replay is a gate.** Readiness reads the app's newest replay: BLOCKED when it differs
  on an element the bot uses, noted when it differs elsewhere, not counted when the app changed
  after it. `--require-fidelity` also blocks when there is none. `behaviour fidelity --app` keeps
  a command-line replay where the portal and readiness find it.
- **Bindings readable by Application Twin Studio automations**: each app also has `http`, and
  the file has `arguments` (resolved) and `output_path`.
- **`labctl e2e <automation>`**: contracts, lab, copy, readiness, bot run under a condition
  (session, verdict, parity with `--golden`), fidelity replay, cleanup, in one report. The
  `e2e` workflow runs it on GitHub for `uipath-case-update` under `slow-database`.
- The lab agent answers `GET /_lab/data/baseline` (the rows Reset would put back).

## 2026-11-16 — Demo databases on SQL Server, Oracle or Postgres
Until now every demo database was a Postgres container. Now each app chooses:

- **`postgres`** (default) or **`sqlserver`**: a container Twin Lab runs (SQL Server 2022
  Developer, with the app's database, an owner and a rows-only `bot` login created on Start).
- **A DBA's server**: SQL Server, Oracle or Postgres, any number of them, configured in
  `twinlab.local.yaml` `databases:` (passwords from environment variables) or the
  `TWINLAB_DATABASES` secret on OpenShift. **Settings > Database servers** lists and checks them;
  the app page (or `labctl db use`) picks one.
- **Tables from one `schema.sql` on any engine.** Twin Lab creates them on Start, leaving existing
  ones alone. **Views become tables** of the same name with the view's columns, so sample rows
  load straight in. Procedures, functions and triggers are listed as not created.
- **The lab agent** loads, resets, takes baselines and records sessions on SQL Server and Oracle
  too (identity columns reseeded, circular keys handled). Conditions apply through the same
  database port.
- **App and bots** get the engine, URL and an ADO.NET connection string (`DB_*` variables,
  `{{app.db_engine}}`, `{{app.db_connection_string}}`).
- **OpenShift**: an app on a DBA's server gets no database pod; a Job creates the tables and a
  Secret written on each deploy holds the addresses. `OCP_EXPOSE_DB` / `--expose-db` puts the
  database link on a NodePort for bots outside the cluster.
- **Devin** is told the engine, its driver and the `DB_*` variables, that views are tables, and,
  for an app already built, to enhance it rather than start again.

Tried live on SQL Server 2022: tables (and a view-table) created, explicit ids loaded into
identity columns, refusals for missing parents, reset, a session catching the app's update, a bot
login that can change rows but not drop tables, and slow-database through the proxy (≈300 ms to
≈1,700 ms). Oracle is tested against its generated SQL only, not a live database.

## 2026-11-16 — Recordings: frames, shadow DOM, canvas and PDFs
The recorder used to read only the top page's HTML. Now:

- **Iframes and frames, same origin or not.** A listener runs in every frame and reports to the
  top page. Actions inside a frame are recorded as `iframe#x >>> button#save`. The frame's page
  is written into the snapshot as `<tl-frame>`, read from outside by Playwright when the frame
  comes from another site.
- **Shadow DOM, open and closed.** A hook installed before the page's own code reaches closed
  roots; roots appear as `<tl-shadow>`. A click inside a closed root is recorded on the real
  element, not its host.
- **Canvas.** A picture of each canvas, and the text OCR reads in it: Tesseract, or Windows'
  built-in OCR with nothing to install.
- **Embedded PDFs.** Kept with their text and form field names (never values), fetched with the
  page's sign-in when the browser does not load them itself. They become screens in the app
  model.
- **Selectors into frames.** One convention everywhere: the UiPath analyser writes iframe
  selectors that way; conformance, the harness bot and fidelity replay enter the frames.
- **Scripted recordings** now have a picture per step too.

Tried on a test page with a same-origin and a cross-origin iframe, open and closed components, a
chart drawn on a canvas, and a PDF with a form field. Each was recorded; Windows OCR read "Total
due" from the chart, and fidelity replay went into both frames. A Citrix session, a plug-in or a
desktop window has no HTML to read; [06-evidence.md](06-evidence.md) says what to do instead.

## 2026-11-16 — Round 5: a portal anyone can start with, OpenShift, the rest of Prod Echo
QTT and Synapse are left aside on purpose; everything else from Prod Echo is built.

- **Portal for newcomers:**
  - **Start here** explains Twin Lab in four boxes and ticks off what is done. It has one
    **Do it now** button for the next step, and a glossary in plain words.
  - Each app shows its progress and its next step, on the Apps list and at the top of its
    page.
  - People type their name once.
  - Errors from the lab read as sentences.
- **OpenShift** ([ocp.md](ocp.md)):
  - `labctl ocp render / deploy / smoke`.
  - Pipeline `deploy-ocp.yml`: it builds the agent and app images to GHCR, then deploys each
    app (database, setup job, lab agent, app, Route) when `OCP_SERVER` and `OCP_TOKEN` are
    set. A push deploys only the apps it changed, or all of them when the lab agent changed.
  - The images now run as a random user ID, as OpenShift requires. This was checked locally
    for both apps. It has not run against a real cluster.
- **Where each value came from (I-11, I-12).** A bot run is joined with its lab session; each
  written column is typed by the bot, chosen by it, or set by the app, and codes are traced
  back to the chosen label. Tried on the showcase:
  - typed `C-1013` became `case_id 13`;
  - the chosen "Payments" became `category_id 2`;
  - `updated_at` was set by the app.
- **Reservations (I-23).** Reserve a shared lab: others can look, but their changes are refused
  with who and until when. Tried live in the portal.
- **Baseline quarantine.** Mark data unusable, with a reason; it can no longer be restored or
  reset to.
- **UAT against production (I-22b).** Compare the behaviour of recordings from two places.
- **T-17.** `agentic-case-update`, a triage agent that works out the category from the case and
  explains it in a note. It is READY beside the UiPath bot, with sessions under normal and
  slow-database, both PASS.
- **T-19.** `labctl data load-across` loads one pack into several apps. A linked key missing on
  either side refuses the pack, naming the key; if one app fails, the others are put back.
- **E-04.** [08-evidence-ablation.md](08-evidence-ablation.md) measures what each kind of
  evidence finds against the source.

## 2026-11-16 — Public repository, required checks
The repository is public. `main` accepts a pull request only when `guard` and `test` have passed.
`test` now runs on every pull request; before, a change outside the code paths skipped it.

## 2026-11-16 — Round 4: the open gaps closed
PR 4 was merged. Round 4 builds what round 3 left open, and each piece was tried on the
running labs.

- **A verdict on every session.** An automation declares `expects:`, the row changes it may
  make: tables, operations, columns that may change, and row counts. The usual way is to read
  a session's changes and press **These changes are correct** (`labctl session accept`). After
  that, every session is PASS, or FAIL with each unexpected change marked. Tried live:
  - `uipath-case-update` now expects an insert into `case_audit` and updates to four columns of
    `cases`.
  - `python-qtt-sla-report` reads only, so it expects `[]`.
- **Named data packs and baseline history.** The lab agent keeps every baseline (the newest 20,
  plus pinned ones), and any of them can be restored. **Save current data** stores a named
  pack on this machine, and it loads again by name.
- **Recorder.** `labctl evidence attach`, or the portal, records a browser that is already
  running (a robot's), over the DevTools protocol; the HAR is built from network events. Each
  step keeps the page as the action met it, the page after it, and a PNG. A step's time is now
  when the action happened, not when the page settled. Typed values are still never kept, only
  their shape (`A9`), and that is how input masks are found.
- **Fidelity replay in the portal** (Lab > Fidelity replay), with the test password filled in.
- **`labctl harness run`.** Resets the data, applies a condition and records a session around
  the harness bot. It then imports the robot log, judges the changes, checks parity with
  `--golden` and writes a report. With `--repeat N` it also checks that every run changed the
  same kinds of rows. Tried live on showcase:
  - normal: 5.6 s, PASS;
  - slow-database: 12.8 s, PASS, and parity PASS against the normal run;
  - peak-hour: 19.7 s, PASS.
- **Two harness fixes the slow runs found:**
  - The bot moved on while the login request was still running. Playwright's network-idle
    wait returns at once on a page that was idle before. The harness bot and fidelity replay
    now wait until nothing is in flight.
  - A harness run left its condition applied; the earlier condition is now put back.
- **`tools/refresh_fixtures.py`** checks the test fixtures against a fresh harness run, or
  replaces them with `--write`. It found no drift.
- **Reference app.** The case number field has an input mask (`c1003` becomes `C-1003`), and
  search shows "Searching…" for at least 600 ms. The catalog finds both; the showcase demo app
  predates them, so a fidelity replay reports them.
- **Tables used.** The lab's database link reads the SQL text that passes through it and keeps
  the table names per session, shown on the session page. Tried live: the showcase bot run
  touched 7 tables (writes to `cases` and `case_audit` only); the QTT Python bot read 4.
  `realdata trace-diff --observed-session` uses them.
- **Real data:**
  - `realdata draft-spec` drafts a subset spec from the inventory.
  - `realdata synapse-job` and `synapse-read` hand the extraction to a data platform and check
    what comes back.
- **T-16.** The Python analyser also reports tables in the code's SQL, and Playwright or Selenium
  selectors, that `touches` does not list.

Still open (end of `07-prod-echo-coverage.md`):
- baseline quarantine
- a check of UAT behaviour against production
- value binding, which needs production capture
- agreeing the Synapse file format

## 2026-11-16 — Round 3: the bots' own fields, review fixes, Prod Echo gaps closed
PR 3 was merged, and every merged branch and worktree was removed. Only `main` is left.

**The brief now carries the fields each bot's code uses.** The UiPath analyser follows
`HttpClient`, then `DeserializeJson`, then `obj("field")`. A new Python analyser reads the code
statically and follows each response through variables, loops, functions and `sorted(key=...)`.
For each route both record what the bot sends and reads, for example `/api/login`: sends
`username` and `password`, reads `token`. The brief lists those fields for the builder. It also
flags a read field that the recorded answer lacks. This is exactly the gap that broke the QTT
build in round 2.

**An independent review found 12 problems; all are fixed and tested.**
- **Privacy.**
  - Names returned by an API survived into page snapshots; they are now masked.
  - Snapshots from deleted evidence stayed in `pages/`; regenerating now drops them.
- **Portal.**
  - Without a password, the portal answers only to its own host names, which stops DNS
    rebinding.
  - `git pull` is judged by its exit code, and never waits for a password prompt.
  - Docker and git output is decoded as UTF-8 on Windows.
  - A failed upload leaves nothing half-registered.
  - Recordings run outside the job pool, stop after 30 minutes, and refuse to start from another
    machine.
  - A corrected Devin key is used without a restart.
  - A by-hand build records only its own app's package.
- **Lab.**
  - Bots get a database login, `bot`, that can change rows only. It cannot run programs, read
    files or drop tables. This was verified.
  - The database owner's password is generated per machine.
  - `TWINLAB_LAB_BIND` and `TWINLAB_HOST` are remembered, so the portal and the CLI agree.
  - Admin calls always go to `127.0.0.1`.
- **Real data.** The read-only guard now also refuses:
  - a second statement after `;`
  - writes inside `WITH`
  - `SELECT ... INTO`
  - comments, and `;` in a seed's `where`

**Prod Echo and ATS gaps closed.** An audit checked every coverage claim against the code, and
`07-prod-echo-coverage.md` now says what is really there. New:
- **Robot runs in the portal.** Upload a robot log (I-08, I-09). Turn its timings into a
  `robot_logs` condition (T-26). Compare a production run with the same bot's run in the lab
  (parity, I-24). Tried live: screens and fields matched, and three value differences were
  found.
- **`labctl realdata load`.** Masked rows go into a lab, with real names mapped onto the demo
  schema. A mask receipt is required (I-18). Tried live: 267 rows from SQL Server-style names
  were loaded into QTT, and conformance stayed READY.
- **The AI-change guard (T-22)** runs in CI as `guard.yml`.
- **Optional drivers** for SQL Server, Oracle and MySQL.
- **[realdata.md](realdata.md)**, a new guide.

**Still open (see the end of `07-prod-echo-coverage.md`):**
- a pass/fail verdict on unexpected changes
- named data packs and versioned baselines
- a recorder that attaches to a running browser
- fidelity replay in the portal
- the harness chain in one command

## 2026-11-15 — End to end without source: QTT stand-in, real bots, review fixes
`apps/qtt/` was built by a stand-in Devin from its package alone: 4 recordings and SQL Server
DDL, with no source. Both QTT automations are READY (65 and 4 checks). Running the real Python
SLA bot then found three problems that conformance could not see.

1. **The bot needed fields the brief did not carry.** It reads `token` from `POST /api/login` and
   `items` from `GET /api/reviews`. The fix is round 2 in `apps/qtt/BUILD-NOTES.md`. Lesson: response
   fields a bot's own code reads must reach the brief.
2. **`localhost` cost about 20 s per connection on Windows.** It tries IPv6 first, and the lab
   ports listen on IPv4 only. Bindings and clients now use `127.0.0.1`. The bot run went from
   137 s to 2.7 s.
3. **Profiles for `app: any` applied nothing when sent to the lab agent directly.** They now apply
   to every app. The portal was not affected.

With those fixed, the same bot on the same data shows the slowness lab working:

| Condition | Login | Review list | Whole run |
|---|---|---|---|
| Normal | 32 ms | 14 ms | 2.7 s |
| slow-database | 1.7 s | 1.8 s | 7.8 s |

The admin calls of the review fixes now need a token. Other changes from the reviews:

- Masking is one-to-one, and `sanitize()` reports collisions.
- A Devin session is never created twice, and `labctl devin start --force` overrides that guard.
- Stale polls cannot undo a cancel.
- Reset refuses mismatched baseline files. A baseline from an older agent is upgraded at startup.
- Intervals keep exact precision.
- Admin calls need exactly one `X-Lab-Token` and a JSON body.
- Lab ports bind to `127.0.0.1` unless `TWINLAB_LAB_BIND` says otherwise.
- The admin token is generated in `.lab-state/lab-token`.
- `lab up` sets the first baseline. It also rebuilds the agent image every time, so a `git pull`
  takes effect.
- The agent image build works from any folder.

Open: the factory does not yet extract the fields a bot reads.

## 2026-11-15 — E-01 App model with provenance (done)
Everything known about an application now goes into one model, `apps/<app>/model.yaml`, and the
brief, schema and `app.yaml` are generated from it alone. Builders exist for three kinds of
evidence: source code (`observed`, and complete for screens, routes and tables), automations
(`declared`) and requested flows in `flows/<id>.yaml` (`described`). `merge` keeps the strongest
basis and every source of each item, never merges paths that only look alike (`/cases/search` and
`/cases/{id}` stay separate), and turns disagreements into conflicts: a selector the source does
not show, a route, screen, table or column that complete evidence lacks. A declared item is not a
conflict when nothing complete covers its area, which is what makes automation-only and
flow-only briefs possible. `labctl brief` takes `--source` optionally and reads flows; a requested
flow brings its screen, routes and tables into scope. Paths in the model are never absolute. For
the showcase the scope, rules and schema are unchanged from T-05. Not yet: HAR (E-02), DDL and API
descriptions and mode labels (E-03).

## 2026-11-15 — Git repository
`twin-lab` is a Git repository. `main` holds the platform (T-00 to T-05); the showcase demo app
from T-06 is on branch `app/showcase`, which stands in for the pull request T-06 could not open.
`.gitattributes` keeps Unix line endings, which the Linux image builds need. A fresh clone showed
that an unpinned ruff gives different lint results in a new environment, so ruff is now pinned.
No remote yet.

## 2026-11-15 — T-06 Factory playbook and first build (done)
`docs/factory-playbook.md` and `AGENTS.md`, adapted from Application Twin Studio, tell a coding
agent how to build a demo app from a brief. A fresh agent given only those files, the brief and the
source built `apps/showcase/`: one Docker image serving the React front end and the FastAPI back
end on port 8000, backed by a Postgres server named in `DATABASE_URL`. An independent review
(42 checks the builder never saw: routes, rules, screens in a real browser, the cascade, the
conditional field, the absence of the out-of-scope screen) passed in both rounds. Round 2 was
caused by the brief, not the agent: it had kept the source's SQLite default and emitted no
identity columns. `labctl brief` gained `--db-engine` with per-dialect DDL, tested on a real
Postgres. The log is `apps/showcase/BUILD-LOG.md`. Not done: no pull request (no Git repository);
the UiPath bot itself has not been run against the app.

## 2026-11-15 — T-05 Source analysis and build brief (done)
`labctl brief <app> --source <folder> [--system <name>] [--force]` reads the real application's
source statically (React + React Router front end, FastAPI + SQLAlchemy back end; anything else is
refused as unsupported), joins it with the demand report and writes `apps/<app>/BRIEF.md`,
`apps/<app>/db/schema.sql` and, only if missing, a first `apps/<app>/app.yaml`. Scope follows the
demand: demanded screens, the API calls their source makes, routes automations call directly, a
health route, the tables those routes use, tables automations query, and foreign-key parents.
Each item says why it is in. Rules come from `raise` statements with a 4xx status and from request
body constraints, all `hypothesis`. Demanded selectors are checked against the screen's source.
For the showcase: 3 screens, 9 routes and 7 tables in scope; the canned-responses screen (added to
the reference app for this purpose), its 2 routes and its table are out. The static schema is
tested against SQLAlchemy's own metadata. Known limits: one stack pair only; a rule written as
anything other than a `raise` with a status (a custom exception handler, a database constraint) is
not found; the brief is not reviewed by a person yet.

## 2026-11-15 — T-04 Demand report (done)
`labctl demand <app>` prints the union of what registered automations touch on one application:
screens with selectors, routes, tables with columns, each line naming the automations that need it.
`--json` for machines. An automation with unresolved gaps is flagged, because the report may be
missing what those gaps hide.

## 2026-11-15 — T-03 UiPath analyser (done)
`labctl automation analyse <id> [--write]` reads a UiPath project statically and fills `touches`,
`analysis.gaps` and `analysis.notes` in `automation.yaml`. It follows invoked workflows and
addresses built through assigned variables, converts UiPath selectors to CSS, and reads tables
from literal SQL. Rejects DTDs, oversized files and workflow paths outside the project. A sample
project, `automations/uipath-case-update/`, is written against the reference app and analyses
with no gaps. Known limits: classic activities only (modern-design `N…` activities become gaps);
every XAML it has seen is hand-authored, so a real Studio export is still untested; a click that
changes screen cannot be seen statically, so elements after a click get a note, not a new screen;
it will not overwrite a file with `reviewed_by` set unless `--force` is given.

## 2026-11-15 — T-02 Showcase source app (done)
`reference/case-workbench/`: a neutral case-management app (React 18 and TypeScript, FastAPI and
SQLAlchemy, SQLite) with sign-in, search and case detail over seven tables and a deterministic
seed. It has a cascading dropdown, a field that appears only on escalation, and five server-side
rules; see its README. Thirteen backend tests; the frontend was checked by hand in a browser and
has no automated tests. Also in this change: the top-level `platform/` folder was renamed `lab/`,
because pytest could load it in place of Python's built-in `platform` module.

## 2026-11-15 — T-01 Contracts (done)
Pydantic models for `automation.yaml`, `app.yaml`, data pack, condition profile, session and
bindings in `lab/labctl/labctl/models.py`, each with an example in `specs/`. Unknown keys are
errors. `labctl validate [files]` prints file, field and reason and exits 1 on any problem; with no
arguments it checks every contract file in the repository. Run: `pytest -q` (17 tests).

## 2026-11-15 — T-00 Monorepo scaffold (built, not closed)
Folder layout, root `pyproject.toml`, `labctl` with `validate` and `automation | app | data |
condition` groups (each has `list`), ruff and pytest configured, `.github/workflows/platform.yml`
written. Lint and tests pass locally on Windows, Python 3.13. Left open: the folder is not a Git
repository, so the workflow has never run and "fresh clone" is unshown. `uv` is not installed
here, so setup uses `pip install -e ".[dev]"`.

## 2026-11-15 — Design and plan
Docs 01 to 05. No code.
