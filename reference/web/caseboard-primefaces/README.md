# Caseboard PF (PrimeFaces)

A real JavaServer Faces application with PrimeFaces, built to reproduce what office JSF
applications do to a recording: sub-pages swapped by ajax inside one address, navigation by
postback and forward (the address bar is one step behind), tabs, a wizard, an accordion, dialogs
(including a dialog-framework page in an iframe) and a lazy data table. Synthetic data only, held
in memory; no request leaves the machine at run time.

| | |
|---|---|
| Framework | JSF 2.3, Mojarra 2.3.9, PrimeFaces 10.0.0 |
| Pages | Facelets (`webapp/*.xhtml`), sub-pages in `webapp/WEB-INF/sub/` (a dynamic `ui:include`) |
| Beans | classic managed beans in `WEB-INF/faces-config.xml` (no CDI container) |
| State | server-side view state, session by cookie |
| Server | embedded Jetty 9.4.57 (`src/caseboardpf/Main.java`), `127.0.0.1` only |
| Java | built with `--release 11`; run here on Java 17 |

## What it does

- `/login.xhtml`: sign in (any user name, password `demo`) is a full postback that **forwards**
  to the workspace: the address stays `/login.xhtml`.
- `/workspace.xhtml`: one address for four sub-pages, swapped inside `main:content` by ajax
  (`p:commandLink update="main:content"` and a dynamic `ui:include`):
  - *Cases*: a lazy `p:dataTable` (paging, sorting on two columns, a column filter sent on Enter,
    row expansion, single row selection with `p:ajax event="rowSelect"`), a `p:dialog` (quick
    note), a `p:confirmDialog` (close a case), and *Open case page*, a postback that forwards to
    `/case.xhtml` while the address shows `/workspace.xhtml`;
  - a case's detail: a `p:tabView` whose tabs load on first open (`dynamic="true"`);
  - *New case*: a `p:wizard` (a required title, then the owner picked in `picker.xhtml`, a
    dialog-framework page shown in an iframe: `PrimeFaces.current().dialog().openDynamic`, then
    confirm);
  - *Reports*: a `p:accordionPanel` whose panels load on first open;
  - a `p:remoteCommand` (`autoRun`) refreshes the case count whenever the workspace loads.
- `/case.xhtml`: the case's own page; *Back to the workspace* forwards again (the address then
  shows `/case.xhtml` for the workspace).
- `/picker.xhtml` answers its GET after 900 ms (a filter in `Main.java`), as a slow office
  server does: a recorder that snapshots as soon as the ajax answer is in misses the page the
  dialog shows.

## Fetch, build, run

Needs a JDK 11 or newer and Python 3.11+. No Maven or Gradle.

```bash
python reference/web/caseboard-primefaces/fetch_libs.py   # the JSF app's 14 jars, and PrimeFaces
python reference/web/caseboard-primefaces/build.py
python reference/web/caseboard-primefaces/run.py --port 8139
```

`fetch_libs.py` uses the plain JSF application's jars (`reference/web/caseboard-jsf/lib/`, same
pins, fetched by that folder's script) and downloads one jar of its own into `lib/`, from
`https://repo1.maven.org/maven2/`, checked against the SHA-1 Maven Central publishes beside it:

| Jar | Version | SHA-1 | Bytes | Licence |
|---|---|---|---|---|
| `org.primefaces:primefaces` | 10.0.0 | `e32fd22721d5cd0f89fb0cb025b073248196d721` | 5,233,705 | MIT |

PrimeFaces 10 needs nothing else at run time here: its other dependencies are optional (file
upload, exports, bean validation). `lib/` and `classes/` are Git-ignored.

## The scripted session and the gates

`flow.py` is the session (`flow(page, base_url)`), with `TYPED` (every value typed or chosen) and
`RECORDS` (the rows clicked, by title) for the replay's bindings. `certify_local.py` records it,
builds the package with `labctl evidence build-all` and replays the recording against a fresh
instance:

```bash
python reference/web/caseboard-primefaces/certify_local.py [--keep FOLDER]
python -m pytest lab/labctl/tests/test_reference_primefaces.py -q
```

The test skips where there is no JDK or the jars are missing (CI).

## Notes for whoever changes it

- The dialog framework needs `DialogActionListener` and `DialogNavigationHandler` in
  `faces-config.xml`, and `picker.xhtml` posts its `pfdlgcid` back (a hidden field), which
  `closeDynamic` needs.
- The wizard's current step is bound to the bean (`step="#{wizard.step}"`): with the dynamic
  include the component's own state does not survive the swap.
- The flow types no word that an id or a label of the interface also holds: the recorder takes
  every typed value out of what it writes, ids included (`report` would turn `main:navReports`
  into `main:navAs`).
