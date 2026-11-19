# Building apps from evidence

**Decision (2026-11-15):** we will not have access to the real applications' source code as a
rule. Each demo app is built from whatever evidence exists for that application, in any
combination. Source code is one possible input among several, not the starting point.

## What we may have, per application

| Evidence | Gives | Does not give | Handling |
|---|---|---|---|
| **Automation code** (UiPath XAML, Python, agent) | Screens in order, exact selectors, fields typed and read, API calls, SQL with tables and columns | Layout, the rest of each screen, response shapes, rules | Parsed statically (T-03) |
| **HAR file** (browser recording) | The real HTML of every page visited (ids, classes, structure, framework), every API request and response with real shapes, status codes and error bodies, timings | Screens never visited, server rules beyond what was seen, behaviour hidden in scripts | **Contains customer data and session tokens.** Parsed where it is stored; only value-free shapes are kept |
| **Source code** (sometimes) | Everything, including rules | — | Analysed statically (T-05) |
| **Database schema** (DDL, export of table definitions) | Tables, columns, types, keys; views (as tables) | Which tables a screen uses; what a view, procedure or trigger computes | Imported as is, in any dialect. Each view becomes a table of the same name with the view's columns, so sample rows load straight into it and the tables behind it need not be known. Procedures, functions and triggers are listed as not created |
| **Database export with rows** | Real-shaped values, code lists | — | Customer data: never committed; shapes and code lists only, or masked |
| **API description** (OpenAPI, WSDL, Postman collection) | Routes, request and response shapes | Which calls the screens make | Imported as is |
| **Screenshots or screen recordings** | Layout, labels, order of fields, look | Element ids, behaviour | Given to the AI builder to look at; not parsed |
| **Flow description** from a person | Steps, fields, rules in words | Anything exact | Written as a structured flow file (below) |
| **Robot logs** | Values typed and read, timing per step | Structure | Parsed (Prod Echo I-08), used mostly for slowness |

Any of these may be missing. Common combinations:

- automation code and a HAR, but no source
- a HAR only, recorded by someone clicking through the flow
- a flow description and some screenshots, nothing else
- database and API automations with DDL or an API description, and no UI at all
- source code, occasionally

## One app model, whatever the evidence

Every analyser writes into one model per application, `apps/<app>/model.yaml`. The brief, the
schema and the conformance check are generated from the model, never straight from one kind of
evidence. Each item in the model records where it came from and how sure we are:

| Basis | Meaning | Example |
|---|---|---|
| `observed` | Seen in a recording or the real code | An `<input id="caseNo">` in HTML captured in a HAR |
| `declared` | A specification or an automation says it exists | A selector in a UiPath project; a column in DDL |
| `inferred` | Worked out from other evidence | Table `case` with column `status` deduced from an API response |
| `described` | A person said so | "Status can only be changed to Resolved after a note is added" |
| `assumed` | Nobody said; the builder filled the gap | Layout of a screen we only know from selectors |

When evidence disagrees, the stronger kind wins, and the conflict becomes an open question in the
brief:

**source code > HAR > database schema and API description > automation code > screenshots >
flow description.**

An automation's selector is never overridden silently. If a HAR shows `#caseNumber` where the bot
uses `#caseNo`, that is a question for a person: the recording or the bot may be out of date.

## What gets built, by evidence available

The build mode is not chosen up front. It follows from the evidence, and is usually mixed within one
app: one screen observed in a HAR, another known only from a bot's selectors.

| Mode | Built from | Result | Label shown in the portal |
|---|---|---|---|
| **Copy** | Source code, scoped by demand | Same screens, API, tables and rules | copy |
| **Reconstruction** | HAR, plus automation code if any | Real markup and API for the paths recorded; rules only where an error was recorded | reconstruction |
| **Skeleton** | Automation code only | Screens with the bot's exact selectors and the routes and tables it uses; layout and look invented | skeleton |
| **Backend only** | Database and API automations, DDL or API description | API and database, no screens | backend |
| **Sample** | Flow description, screenshots if any | A complete small app that follows the described flow; everything exact is invented | sample, not a copy |

The label matters. A bot that passes on a reconstruction has been shown to work against the
real markup it will meet. A bot that passes on a skeleton has only been shown to find its own
selectors.

## What decides scope

Before: only what registered automations touch. Now, either of:

- **Automation demand**: what registered automations touch (T-04), when their code is shared.
- **Requested flows**: flows a person asks for, written down in `flows/<id>.yaml`, when there is
  no automation code or when the request is "build a complete sample app" for a demo.

Nothing outside those is built. A request for "the whole application" becomes a list of flows,
each one reviewed.

```yaml
# flows/case-update.yaml
id: case-update
app: showcase
requested_by: <name or team>
purpose: Demo of a UiPath bot and an agent updating a case
steps:
  - screen: Sign in
    do: enter user id and password, press Sign in
  - screen: Find a case
    do: type the case number, press Search, read the status in the results
  - screen: Case
    do: choose category, then sub-category (depends on category), set status, Save
    rules:
      - Resolving needs at least one note
      - Escalating needs a reason of at least 10 characters
evidence: [har:case-update-2026-11-20, screenshots:case-screen]
```

## Choosing the stack without source

Frameworks generate the markup that selectors depend on, so the demo should use the same one.
Without source, the framework is read from the HAR's HTML and script names where possible:

- `ng-version="14.2.0"` → Angular 14.2
- React root markers and bundle names → React
- `__VIEWSTATE` → ASP.NET Web Forms
- `javax.faces.ViewState` → JSF
- no client framework → server-rendered pages

If nothing identifies it, the lab default is used: React, FastAPI and Postgres. The brief says
which, and why.

## What a recording captures

The recorder needs no browser extension. It drives Chromium through Playwright, or attaches to a
running Chrome or Edge over the DevTools protocol, and adds a small script to every page and
every frame. Per step it keeps:
- the page as the action met it;
- the page after it, once it settles;
- a picture;
- the network (HAR, with bodies).

Typed values are never kept, only their shape (`A-9`).

A plain copy of the page's HTML misses several things, so the recorder writes them into the
snapshot as ordinary elements:

| On the screen | In the snapshot | Notes |
|---|---|---|
| An iframe or frame, same origin or not | `<tl-frame data-tl-frame="iframe#x">` with the frame's page, right after the `<iframe>` | Actions inside are recorded as `iframe#x >>> button#save`; frames inside frames chain. A frame from another site is read from outside by Playwright. A step lists `frames_unread` if one could not be read. |
| A web component's shadow root, open or closed | `<tl-shadow data-tl-mode="open\|closed">` inside its host | Closed roots are reached through a hook that runs before the page's own code. When attaching to an open browser, closed roots made before the recording started stay out of reach. |
| A `<canvas>` (charts, drawn forms) | `data-tl-canvas="c1"`, then `<tl-canvas-text>` with the text OCR read | A picture of each canvas is kept (`screens/0007.c1.png`). OCR uses Tesseract when installed (`pip install -e ".[ocr]"` and the `tesseract` program), otherwise Windows' built-in OCR, otherwise none, and the step says which. |
| An embedded PDF (`<embed>`, `<object>`, `<iframe>`) | `data-tl-document="pdf"` | The PDF is kept in `docs/`, with its text. `docs/documents.json` lists its address, pages and form field names, never field values. The app model gets a screen per document. |

Selectors follow the same convention everywhere: the UiPath analyser writes
`<webctrl tag='IFRAME' id='x'/><webctrl id='y'/>` as `iframe#x >>> #y`. Conformance, the
harness bot and fidelity replay enter the frames. Open shadow roots need nothing, because
Playwright looks inside them. An element in a closed shadow root is recorded and compared, but
no selector can reach it, so a bot that clicks one can only be checked by fidelity replay of
the page around it.

**What no web recorder can read:**
- **Citrix or remote desktop.** A Citrix session shows the application as a picture of a remote
  screen; there is no HTML to read. Record the application where it runs as a web app (inside
  the Citrix session, Twin Lab's `evidence attach` works against a browser started there with
  a debugging port). Otherwise build from the database schema, API descriptions and the
  robot's logs, which give a backend-only app.
- **Plug-ins and native windows** (Java applets, ActiveX) are the same case. A Windows desktop
  client is recorded by `labctl desktop` instead ([09-desktop.md](09-desktop.md)).
- **Pictures of text inside images** are read only on canvases, not in `<img>`. The picture of
  every step is kept for a person to look at.

## One recording, several applications

A person often crosses applications in one recording. `labctl evidence split <recording>` groups
the steps by the site of the page they happened on and writes a proposal to edit: rename an
application, merge two, split one host by path, leave one out. Sign-on pages are left out by
default, and an iframe from another site is listed under its page's application so it can be
made its own. `--apply` then writes one recording per application under `evidence/<app>/`, each
step keeping its number in the whole recording, and the sequence across applications in
`evidence/_sequences/<recording>.flow.yaml`. Requests are assigned by their Referer and time, so
check the proposal's step counts before applying.

### Every tab, and from a recording to packages

The recorder follows every tab and popup the flow opens, on one numbered sequence. `labctl
evidence record-across <name> <url>` records such a flow, and `labctl evidence build-all
<recording>` takes it to one evidence set, draft flow, brief and build package per application,
stopping first for a person to confirm the proposed applications. The portal's Recordings page
does the same.

To replay a recording that crosses applications in one run, give each source origin its
replica: `labctl behaviour fidelity <recording> --map https://real.example=http://localhost:8101`
(repeat per application), or keep a `replicas.yaml` beside the recording. What is deliberately
left out of the comparison goes in a reviewed `scope.yaml`.

## The look of the application

A builder must also make the demo application look like the original. `labctl brief` (and
`evidence build-all`) therefore writes `apps/<app>/look/` beside the page snapshots, from the
same recordings, and the build package carries it (`labctl/lookfeel.py`):

| In `apps/<app>/look/` | What it is |
|---|---|
| `css/*.css` | The style sheets the application delivered (from the HAR), as written. `css/inline/<screen>.css` holds the `<style>` blocks of each screen. |
| `theme.json` | Design tokens: the colours in use (page, text, links, buttons, borders, messages), the font per role (body, headings, labels, inputs, buttons, table header and cell) with size and weight, padding, border and corner radius of the controls, spacing, the window size and the regions of each screen. |
| `screens/<screen>.png` | One picture per screen, to compare a built screen with. |
| `assets/*` | The images of the interface: logo, icons, sprites. |
| `fonts/*` | Web font files, only with `TWINLAB_LOOK_FONTS=bundle`. |
| `texts.json` | Per screen, every text: `literal` (copy it exactly), `data` (a placeholder) or `mixed`. |
| `manifest.json` | Every carried file, every font and image with the reason it is or is not carried, and the result of the privacy check. |

The brief gets a section *Look and feel* that tells the builder how to use these, and lists per
screen the literal texts and the placeholders. The build task names the files.

How it stays free of data:

- **Pictures are rendered, never recorded.** The recorder's own screenshots show real data and
  never leave the evidence. A picture in the package is the value-free page snapshot drawn again
  in a headless browser at the recorded window size, with only the carried style sheets and
  images. It shows the real styling and the real interface texts, and `Xxxx 9999` where data was.
  The tokens in `theme.json` are the computed styles of that same rendering, so recordings made
  before this existed work too, and the recorder captures nothing new.
- **Style sheets are code.** Selectors, colours and sizes are kept as written. Addresses are not:
  `url(...)` and `@import` point into `look/` or are removed (never a host, never a query), and
  source-map comments are dropped. In strings and comments, e-mail addresses, numbers of three
  or more digits and names the recording shows as data are masked. Font names are left alone.
- **Fonts are licensed.** Each font family is named with where it came from (a system font, or
  a web font file seen in the HAR with its path, format and size) and the closest generic
  fallback. The files are copied only when the owner sets `TWINLAB_LOOK_FONTS=bundle`.
- **Images.** Only a static file of the application's own origin that the interface uses (a
  style sheet, or an `<img>` or icon outside data cells), with no record number in its path, no
  query but a version (`?v=3`), not under a folder for user content (`uploads`, `avatars`,
  `attachments`, ...) and no larger than the analysis reads. Everything else is listed with the
  reason and not carried.
- **A check before anything is kept, and again when packaging.** Every carried text and the
  text each picture shows are compared with what the recording shows as data: the Title-case
  words of data cells, field values and API answers that the interface does not use itself, and
  every part with the shape of data (e-mail address, date, amount, three or more digits). One
  hit and no look is written; the brief says so. `labctl build package` refuses a `look/` that fails
  the same check, and a picture that is not one Twin Lab rendered (its sha256 is in
  `look/manifest.json`).
- `TWINLAB_PAGE_TEXT=strict` carries no look at all: every text is masked and no style sheet
  leaves. `TWINLAB_LOOK=off` switches the look off on its own.

Not carried: styles a page adds through script without a `<style>` element (constructed or
adopted style sheets), addresses inside inline `<style>` blocks and `style` attributes (they
are `url(#)` in the snapshots), hover and focus states (only as the style sheets define them),
and one picture per screen (the fullest snapshot), not one per state.

### What a package says about the API, the navigation and the sign-in

A recording's HAR holds every call the pages made. The package carries that contract and none
of the data:

- `apps/<app>/api/`: one file per endpoint (method, address pattern, parameters, schemas by
  status, the action that sent it, where the page showed the answer) with every recorded call
  as a synthetic request and answer. The synthetic records are the rows of `db/seed.json` and
  the values of `steps/*.values.yaml`, so the seed, the examples and the replay agree. Field
  names, code lists, identifiers the application generated, paging numbers and the
  application's own error messages are kept; every other value is replaced, and the written
  files are checked against the HAR (`labctl.apicatalog.leaks`).
- `apps/<app>/navigation.json`: every screen, each way in and out, dialogs, in-page tabs,
  frames and popup windows, links seen and not visited, and every interactive element with
  what happened when the recording used it, or "seen, not used".
- The brief's *Sign-in and session* and `apps/<app>/session.json`: what was observed, by name
  only, and the tables `app_users` and `app_sessions` the rebuilt application keeps users and
  sessions in. Every cookie a response set is listed by name with the response that set it
  (request, status, step, screen), its attributes (`Path` as an address pattern, `Domain`
  relative to the application's host, `HttpOnly`, `Secure`, `SameSite`, `Partitioned`, the
  lifetime as a duration: session or persistent) and the response that cleared it, read from
  the HAR's `Set-Cookie` headers (`sanitize.cookie_attributes`). A cookie's value is never read
  into the package and is not needed: the rebuilt application issues its own. Neither is the
  host name a `Domain` names.
- The brief's *Scope*: with no automation and no flow written by a person, everything the
  recording shows, in recorded order.
- `network` in each endpoint file and `apps/<app>/api/network.json`: what the HAR shows beyond
  the contract, and `apps/<app>/templates.json`: each screen as a template (see *Large sites:
  templates, the shared layout and the network* below).

### Large sites: templates, the shared layout and the network

A shop with thousands of product pages records many pages of one structure. Before, an address
segment without digits was a route name, so `/p/blue-linen-shirt` and `/p/red-wool-scarf` were
two screens, each with its own snapshot and page file, and the product names travelled in the
addresses (the API catalogue's own check then refused most endpoint files). Now
`labctl.templates` groups the addresses a recording visited before the brief is generated:
addresses equal but in one segment, at least three values there, a literal name in the rest of
the address, and either two visited pages of the same structure outside the shared layout or
five links of one repeated element. While the brief and the package are made
(`factory.generate`, `evidence build-all`), `sanitize.route_pattern` writes that segment
`{slug}`: `/p/{slug}`, `/{slug}/dp/{id}`. Screens, snapshots, page files, steps, the navigation
map, the code map and the API patterns are then per template.

| In the package | What it holds |
|---|---|
| `templates.json` | Per template: instances (different addresses) and visits, path and query parameters with their role (search, filter, sort, page) and, for a choice the page offers, the values; the data fields (an element whose text differs between instances, unless every word is the interface's) with one seed row per instance, synthetic and of the recorded shape; repeated lists; the parts found in the page: breadcrumbs, image gallery, variant picker (the API it calls and what the answer changes), recommendation or personalisation blocks, search facets as code lists, sort, page links, a list loaded in pages (cursor or page, envelope, infinite scroll or "load more"), a category tree. Per application: the navigation hierarchy (levels from the breadcrumbs, categories from the mega-menu) and the cart and wish-list state (endpoints, fields, badge, where it is kept) |
| `navigation.json` `shared_layout` | The header, menus, mega-menu (as a tree) and footer every screen has, once; each screen's element that belongs to them says `layout` |
| `network` in `api/<endpoint>.json` | Request headers by name (content type, accept, custom `X-` headers classified: "the same on every call, shape `A/9.9`"), the CSRF token by name and where the page took it from (a `<meta>`, a hidden field, a cookie, an answer), the body format (form fields, multipart fields with file type, extension, size class and `accept`, GraphQL operations with variables, selection tree and literals removed, JSON-RPC methods, SOAP operations), query roles, per status the caching (`Cache-Control`, ETag, Last-Modified, Vary, answers from the browser cache, conditional requests), compression, CORS and custom response headers, the paging envelope (next cursor, total, `Link` rels, `X-Total-Count`, whether a last page was seen) and what asked for more, response time (median, maximum, server wait), which earlier answer or page gave each value sent (by field name), retries after an error, a role (cart, wish list, recommendations, suggestions, variant) |
| `api/network.json` | The calls each page load makes in order, every page request with status and time, redirect chains hop by hop, streams (server-sent events: event names and data shapes; WebSocket messages when the HAR has `_webSocketMessages`; long polling), static files by kind with sizes, caching and browser-cache hits, third parties by purpose (a known vendor by its public domain, any other host as `third-party-<n>`, never its name; never to be called or copied), an API of the application on another origin, errors and retries |

The build documents use them: each page file has *Template and instances* and, per API call,
its network facts, and leaves the shared layout's elements to `ARCHITECTURE.md`, which gains
*Shared layout*, *Screen templates* and *Network behaviour*. The brief gains *Screen templates
and shared layout* and *Network behaviour*.

Privacy, as for the rest of `api/`: no value is read into these files. Header values, cursors
and tokens are classified or followed by field name only; a GraphQL document is carried with
its literals replaced; a seed row of a template is synthetic, except where an approved
`public-data.yaml` (`labctl.publicdata`) declares the data public: a field in a declared region,
a text that is as a whole a declared value, or a picture under a declared image prefix keeps its
recorded value, the secret checks applied, and `templates.json` marks the column `public`. Both
files pass `apicatalog.leaks` and `codemap.check_text` before they are written.
Recording the fixture shop found and closed four leaks of record names elsewhere: a list's
link named like a record in the replay locators (`stepfacts._target`) and in the draft flow,
the values of a cart column listed as "one of" (`seedplan._format`), and a page's address in
the code map's file names; a template's slugs are masked in `behaviours.json` too.

Measured on the fixture shop (`tests/fixtures/shop`, 30 products from one template): a short
session (54 steps, 3 products) gave 9 snapshots, 50 page files and 4 of 11 endpoint files before
(the others and the catalogue index refused by the privacy check, product names in 13 files),
and 5 snapshots, 12 page files, 11 endpoint files plus `network.json`, and no product name,
after. A long session (280 steps, 24 products, a 2.5 MB HAR; `TWINLAB_SCALE_TEST=1` in
`test_templates.py`) gives the same 5 templates, 5 snapshots, 12 page files and 5 pictures, 24
seed rows for the product template, and a 307 KB package (the short one: 272 KB); what still
grows is per step or per call: `steps/*.json`, the brief's step list, and each endpoint's
`calls` up to its cap of 60.

What is still not captured:

- WebSocket frames in a recording made by `labctl evidence record`: Playwright's HAR has no
  messages (a HAR exported from the browser's developer tools does, and is read).
- CORS preflight requests and answers served from the memory cache: the browser does not put
  them in the HAR. A `304` appears only when the browser revalidated.
- The body of a multipart request that carried a file: the HAR has none; the fields come from
  the form and the recorder's upload step (type and size class).
- An API on another origin is listed in `network.json` (endpoints, CORS, times) but has no
  endpoint files: the catalogue reads the application's own origins.
- What a recommendation or personalisation block chooses, and server-side rules no call showed.
- A template is learned only from what the recording visited or linked: fewer than three
  addresses, or links of no repeated element, stay separate screens.

### What the application's code defines

The HAR also holds the code the browser downloaded: the pages, the scripts, the style sheets
and the JSON files. That code names much more than the recording visited, so `labctl brief`
reads it, without running any of it, into `apps/<app>/codemap.json` (`labctl/codemap.py`):

- every route of the route table (Angular, React Router, Vue Router; for server-rendered
  applications such as JSF, the pages and their links), with its guards, lazy chunk, redirect
  and title, and whether the recording visited it;
- the menus, links, tabs and dialogs of the templates, with the route each belongs to and the
  element that opens or closes a dialog where the code says so;
- every API call the code can make, *called in the recording* or *in code, not called*;
- the interface texts (translation files), lists of values and titles, each through the same
  data checks as the snapshots: a text that may be data is dropped;
- the third-party libraries, named with their version, not analysed.

The brief's *What the application's code defines* tells the builder to build every route at
least as a reachable page, wired as the code shows, and to report a screen only the code has as
reconstructed. `navigation.json` lists those screens too, and says of each transition whether
the recording, a link on a recorded page or the code shows it.

A lazy chunk of a route nobody opened was never downloaded. `labctl evidence fetch-code
<recording>` lists such files; with `--yes` it fetches them: only `.js`, `.mjs`, `.css` and
`.json` files of the application's own origin, with GET, never a page or an API address,
never a redirect, the answer's content type checked, at most one request a second by default
(`--delay`, at least 0.2 s), stopping at a 429 or 503, and without a session unless the person
passes the recorder's saved browser state (`--storage-state`). The files stay beside the
recording (`<recording>/code/`), never in a package; regenerate the brief to use them.

No script is carried in a package, only the map. A value that looks like a secret (a key, a
token, a password, a private key, a connection string, a session identifier) or a host name
other than the application's own is never kept: a map that holds one is not written, and
`labctl build package` refuses one.

What the code cannot tell: what a handler or a service computes beyond its calls and
navigations; server-side rules and where a server page's command button leads (JSF outcomes are
in the server's code); names the build minified (a component is then named by its selector);
addresses and texts built at run time (their variable parts are `{name}`).

### JSF and PrimeFaces: views, sub-pages and partial responses

Office JSF applications break "one address, one screen". A postback is often answered with the
next view (a forward, no redirect), so the address bar keeps the page before: the workspace shows
under `/login.xhtml`. And most of the work happens inside one view: ajax re-renders a region (a
dynamic `ui:include`), a tab, a wizard step, an accordion panel, a dialog, a dialog-framework page
in an iframe, or a data table's page, sort, filter, selection and expansion, while the address
never changes. `labctl/jsfviews.py` reads both from what the recording already holds, running none
of the page's code:

- **The screen of a step is its view**, not the address: the address its JSF forms post to (a JSF
  form posts to its own view; frames' documents left out), else PrimeFaces'
  `PrimeFaces.settings.viewId`; a step without a snapshot takes the view of the last page at the
  same address. The step list, the screens and their snapshots, the transitions and each call's
  screen follow it (`stepfacts`, `har.analyse`, `navmap`). `navigation.json` lists the page loads
  whose address showed another page (`jsf.address_lag`), and marks such a transition
  `forward: true` with `address_after`: build it the same way, a postback answered with the next
  view.
- **Partial responses** (`<partial-response>`, which the HAR holds in full): per ajax request the
  component that sent it, its event, what it asked to execute and render, and what the answer
  did: the regions it updated (client ids), the scripts it ran (by the functions they call:
  `PrimeFaces.openDialog`), a redirect, an error, PrimeFaces' callback arguments
  (`validationFailed`). `navigation.json` gives them per element use (`jsf_ajax`) and in all
  (`jsf.partial_actions`); the view state and head resources they also carried are listed as
  `jsf.ignored` (the rebuilt application makes its own).
- **States inside a view** are sub-views of their screen: `sub-page (ajax region)` (a region a
  partial response replaced with new HTML holding a heading; one state per set of components it
  holds), `tab`, `wizard step`, `accordion panel`, `dialog`, `confirm dialog`,
  `dialog-framework page (iframe)` (with the page it shows), `table page`, `table sort`,
  `table result (filter)`, `table row expansion`, `table row selection`. Each has the steps and
  elements that opened and closed it, what that action re-rendered, and its own value-free
  snapshot (`apps/<app>/pages/<screen>--NN-<kind>-<title>.html`, sanitised like the screen's page
  file); the screen's page document has a section per state. PrimeFaces hides a dialog by
  `aria-hidden` and empties its `role`: both are read.
- **PrimeFaces widgets** in `codemap.json` (`widgets`): every `PrimeFaces.cw(...)` of the pages and
  partial responses, by type, widget variable, client id and option names (never their values).
- **The recorder's settle rule** also waits while PrimeFaces still holds a request (its ajax queue,
  a data table's filter waiting out its delay) or a frame of the page's origin is still loading
  (a dialog-framework page), and up to 3 s while jQuery animates. These are read-only looks at the
  page. The replay waits the same way before it compares.
- A click on a PrimeFaces table row is named by a record locator that finds the row again: the
  identified `<tbody>` itself is the list, striping classes (`even`, `odd`) and PrimeFaces' state
  classes (`ui-state-*`) do not narrow it, and a cell is named by its place in the row. The replay
  does not compare PrimeFaces' pointer classes (`ui-state-hover`, `ui-state-focus`).

Proven on `reference/web/caseboard-primefaces` (Mojarra 2.3.9, PrimeFaces 10.0.0; its
`certify_local.py` and `tests/test_reference_primefaces.py`). Not covered: a forward to a view
with no JSF form on it (the address is then the only clue), sub-pages that no partial response
replaced (a region swapped by plain script), PrimeFaces' client-side state saving, `p:fileUpload`,
push (`p:socket`), and the server's navigation rules themselves (`faces-config.xml` outcomes are
code the HAR does not hold). The recorder takes a typed value out of what it writes only where it
is a value, as a whole word (`labctl/typedvalues.py`): typing `report` leaves the id
`main:navReports` and the menu item "Reports" as they are; an id that is the whole typed value
becomes a placeholder (`tl-typed-a`) that the rebuild and the replay share.

### What the application's code defines

The HAR also holds the code the browser downloaded: the pages, the scripts, the style sheets
and the JSON files. That code names much more than the recording visited, so `labctl brief`
reads it, without running any of it, into `apps/<app>/codemap.json` (`labctl/codemap.py`):

- every route of the route table (Angular, React Router, Vue Router; for server-rendered
  applications such as JSF, the pages and their links), with its guards, lazy chunk, redirect
  and title, and whether the recording visited it;
- the menus, links, tabs and dialogs of the templates, with the route each belongs to and the
  element that opens or closes a dialog where the code says so;
- every API call the code can make, *called in the recording* or *in code, not called*;
- the interface texts (translation files), lists of values and titles, each through the same
  data checks as the snapshots: a text that may be data is dropped;
- the third-party libraries, named with their version, not analysed.

The brief's *What the application's code defines* tells the builder to build every route at
least as a reachable page, wired as the code shows, and to report a screen only the code has as
reconstructed. `navigation.json` lists those screens too, and says of each transition whether
the recording, a link on a recorded page or the code shows it.

A lazy chunk of a route nobody opened was never downloaded. `labctl evidence fetch-code
<recording>` lists such files; with `--yes` it fetches them: only `.js`, `.mjs`, `.css` and
`.json` files of the application's own origin, with GET, never a page or an API address,
never a redirect, the answer's content type checked, at most one request a second by default
(`--delay`, at least 0.2 s), stopping at a 429 or 503, and without a session unless the person
passes the recorder's saved browser state (`--storage-state`). The files stay beside the
recording (`<recording>/code/`), never in a package; regenerate the brief to use them.

No script is carried in a package, only the map. A value that looks like a secret (a key, a
token, a password, a private key, a connection string, a session identifier) or a host name
other than the application's own is never kept: a map that holds one is not written, and
`labctl build package` refuses one.

What the code cannot tell: what a handler or a service computes beyond its calls and
navigations; server-side rules and where a server page's command button leads (JSF outcomes are
in the server's code); names the build minified (a component is then named by its selector);
addresses and texts built at run time (their variable parts are `{name}`).

### Field rules and page behaviour

A recording shows only the rules a person happened to trigger. The rest is in the page's HTML
and in the application's JavaScript, which the HAR holds. `apps/<app>/rules.json` and the
brief's *Field rules and page behaviour* (`labctl.pagerules`) list, per screen and field, every
rule with its message, its evidence and its level:

| Level | Meaning |
|---|---|
| `seen` | The recording triggered it: its message appeared |
| `declared` | The HTML declares it (`required`, `maxlength`, `pattern`, `data-val-*`, `data-p-*`, ...) |
| `in code, not triggered` | The application's own script has it; the recording never set it off |
| `in code, not tied to a field` | In the script; which field it belongs to could not be told |

From the scripts: Angular `Validators.*`, jQuery Validate `rules` and `messages`,
`setCustomValidity`, plain checks (`if (!x.value)`, `.length < n`, `/re/.test(x.value)`) with
the message beside them, cross-field rules, masks, fields shown, enabled or made required by
another, dependent drop-downs (with the API call and the step), a submit button disabled until
the form is valid, and session timers with their delay. From the recording: messages that
appeared, and what changed after a step that typed, chose or ticked. Each script is tokenised
and matched against these shapes, never run. Minified code keeps strings, property names and
call shapes, so a rule tied to an id, a name, a form-control key or a message survives it.
Third-party scripts and library files are named, not analysed.

Scripts are not carried, only the rules and messages taken from them (messages masked like
the API's). An owner with the right to share them sets `TWINLAB_PAGE_SCRIPTS=carry`: the
application's own script files then go to `apps/<app>/scripts/`, source maps stripped and never a
library, after a check that refuses all of them (and later the package) when one holds
something shaped like a secret (a private key, a token, a literal assigned to a credential-like
name), an address of another host, or a value the recording shows as data.

What it cannot recover: rules checked only on the server and never triggered (a JSF
validator: no attribute and no script states it), the logic of a custom validator function
(its message is listed when it reads as one), limits read from data at run time, and the
label words a sanitiser masks in a message (then the message stays the form's, not a field's).

### Where the links nobody clicked lead: `labctl evidence check-links`

Pages hold links the person never clicked. After a recording, a person may ask the real
application where they lead:

```bash
labctl evidence check-links evidence/shop/run            # lists what it would ask; asks nothing
labctl evidence check-links evidence/shop/run --yes      # asks, writes evidence/shop/run/link-check.json
```

Options: `--max N` (links asked for, default 50), `--delay-ms M` (between two requests, default
1000), `--timeout S` (per request, default 10), `--storage-state FILE` (the browser state the
recording started from, as given to `labctl evidence record --storage-state`).

What is checked: every `<a href>`, `<area href>`, GET `<form action>`, `<iframe src>` and
obvious `window.open('...')` or `location.href = '...'` of an inline handler in the snapshots,
resolved against its page (or frame), one per origin and address pattern (`/orders/1007` and
`/orders/1008` once). A pattern the recording visited is not checked.

Safety rules, enforced in `labctl/linkcheck.py` and tested in `test_linkcheck.py`:

- **Opt-in.** Without `--yes` nothing is requested. The portal and `build-all` never run it.
- **Read only.** HEAD, then GET only when HEAD is refused (405, 501) or to read the title of an
  HTML page. Never another method, never a request body, never a click or a script. Redirects are
  followed by hand, each hop checked like a link.
- **Nothing that changes state.** Skipped, with the reason: an address with `logout`, `signout`,
  `sign-out`, `delete`, `remove`, `cancel`, `approve`, `reject`, `submit`, `confirm`,
  `unsubscribe`, `reset`, `close`, `archive`, `pay`, `transfer` (and similar), a query carrying an
  action (`action=`, `op=`, `do=`, ... or an action verb), a token or session id in the query or
  the address, `mailto:`, `tel:`, `javascript:`, `data:`, downloads (pdf, xls*, doc*, csv, zip,
  exe, msi, ... or a `download` attribute), POST forms.
- **Own origins only.** The origins the recording used, and the applications its split proposal
  includes; never a third-party origin, not even as a redirect hop.
- **Anonymous by default.** With `--storage-state` its cookies for those origins are sent. No
  cookie value, token or response body is written; query values are not even printed.
- **Gentle.** One request a second by default, `--max` links, a timeout per request; the first
  429 or 503 stops the run and the rest is listed as not checked.

`link-check.json` gives per link: where it was seen (screen, selector, link text masked by the
sanitiser), the final status after redirects with the pattern of each hop, whether it ended on
the recorded sign-in screen, the content type, and for an HTML page of the recording's origins
its title and main heading (masked) and whether it has the structure of a recorded screen (same
address pattern, or the same form field names). An HTML page the recording never visited is kept
as a sanitised snapshot in `link-check/pages/`, sanitised exactly as the recorder's snapshots
(`TWINLAB_PAGE_TEXT=strict` masks all), after the look's data checks (the recording's data
values and data-shaped tokens); when a check fails only the title and heading are kept and the
file says why.

The next `labctl brief` (or `build-all`) uses it: in `navigation.json` and the brief's
*Navigation map*, a link seen and not visited says what the check found ("leads to a page
*Order history* not recorded: out of scope, answer with a redirect...", "leads to the sign-in
page", "missing (404) in the real application: keep the link, answer 404"); a skipped link keeps
its answer with the reason it was not asked. Pages that exist but were not recorded are listed
as candidates for a further recording (`unrecorded_pages`), and their snapshots travel as
`apps/<app>/unrecorded/*.html`, never as recorded screens. Without `link-check.json` nothing
changes. `labctl.linkcheck.SafeFetcher` is the HTTP part on its own (GET or HEAD only, given
origins only, rate limited, stops on 429 or 503) for other opt-in fetches.

What a package still cannot say, found by the blind rebuild of the help-desk fixture:

- The body of an answer the page never read (a 401 the page only tested the status of).
- Text the sanitiser masks although it is the application's own (an empty-list message that
  holds a word the data also holds), and the wording of generated text (a history line).
- What a control nobody used does. It is rendered and does nothing, even a Cancel button.
- A link between tables that no answer shows: rows of a nested list (`/tickets/{id}/history`)
  have no column for their parent in the inferred schema; the endpoint file says so.
- Rules beyond what was recorded and what the HTML and scripts state (see *Field rules and
  page behaviour*): server-side validation never triggered, paging at the edges, what an
  unknown identifier answers.
- Which row a click on a repeated element took is given (`record` in `navigation.json`), but
  the replay's own `bindings.yaml` still needs that record named by a person.

### The build documents: one file per screen

The brief is one long document; a builder who works screen by screen would collect one screen's
facts from the brief, `navigation.json`, `rules.json`, `api/`, `look/` and `steps/`. So the
packaging step (`labctl.builder.docs`) writes, under `apps/<app>/spec/` and only from the files
the package already holds:

| File | What it holds |
|---|---|
| `CONSTITUTION.md` | The rules that are not negotiable, numbered, and what "done" means: the same text as the task's (one function writes both) |
| `ARCHITECTURE.md` | The stack and versions, the layers, the route table (every screen, recorded or not), the API surface, the data model (tables, keys, relations, field map), sign-in, session and every cookie's attributes, ports and environment variables, the flow across applications, a text diagram |
| `BUILD-PLAN.md` | The build in order (database and seed, sign-in, each screen from the entry along the recorded ways, navigation, API, rules, look, self-check), each step with its "done when" lines |
| `PAGES/<NN>-<screen>.md` | One per screen, recorded screens first in recorded order, then pages the link check found (marked: not built) and screens known only from the code map (`codemap.json`, marked: reconstructed). Each holds the address, the ways in and out, the picture and regions to match, every element with selector, text and type, what each action did, the screen's rules and behaviour, its API calls with links to their files, the data and the table and column it maps to, the seed rows the recorded flow needs, the acceptance checks, the replay steps, its dialogs, tabs, frames and popup windows, and its open questions |
| `GLOSSARY.md` | Option lists, code lists, the statuses the application answered with its own messages, the package's words |

Every statement names the file it comes from; what the package does not hold is written as
*unknown*, with where it would come from. The folder is `spec/`, not `build/`, because the
repository ignores every `build/` folder. The completeness check counts a screen without a
page file as a gap. What the documents cannot say is what the package cannot: the purpose of a
screen beyond its title and heading, the body of an answer the page never read, what an unused
control does, a version nothing in the evidence shows.

## Privacy

A HAR or a database export from a real system holds customer data, cookies and tokens. So:

- Raw evidence lives in an evidence store outside the repository (`evidence/`, ignored by Git, or
  a shared location with access control). Analysers run where it is.
- Parsing drops cookies, `Authorization` and other credential headers at once. Of a cookie only
  its name and its attributes are read; its value never.
- The model keeps shapes, not values: field names, types, lengths, code lists. Example values
  are replaced by synthetic values of the same shape.
- Where real-shaped values are needed, they go through the keyed masking from Prod Echo first.
- Only the model, the brief and the generated schema are committed.
- Interface, not data, and kept because a builder needs it: the file names of static images,
  style sheets, scripts and fonts of the application's own origin that are named by a word
  (`blazemeter-favicon-512x512.png`; a file named by a number, hash or key prefix, or under a
  user-content folder, stays masked); the control texts of a media player library (video.js,
  Plyr, MediaElement, JW Player); short code lists (`cat`: phone, notebook, monitor); browser
  dialog messages, masked like any message. A short answer value a page shows whole (a product
  or customer name in a link or heading) is masked whole.
- **Public catalogue data, declared by the owner** (`public-data.yaml` beside the recording,
  `labctl.publicdata`). Off by default. For an application whose shown content is a public
  catalogue (product names, descriptions, prices, category codes, product photos) the owner may
  declare whole endpoints (method and path, every field or the fields named), page regions and
  image path prefixes public. `labctl evidence public-data <recording>` drafts the file from the
  recording, unapproved, with every endpoint and its fields under `seen:` and nothing public.
  Nothing is used without `approved_by`. Once approved, the declared values are carried as
  recorded in the page snapshots (outside data cells: a cart's rows stay masked), the API
  examples, the seed rows and `look/` (declared photos carried as images), and the brief and
  `BUILD_TASK.md` say which data is real and who approved it. Everything not declared is masked
  as before, and inside declared data a field named like a credential or a contact, a value
  shaped like an e-mail address, token, key, JWT, UUID or cookie, and a typed value's shape are
  never carried.

## Proving it on the reference app

We own the reference app, so we can produce every kind of evidence from it: source, a HAR from a
scripted browser run, its DDL, its OpenAPI description, a written flow description and
screenshots. Building briefs from each subset, and comparing each with the brief built from
source, measures what each mode loses. That comparison is how we will know how far to trust a
reconstruction of QTT before QTT is attempted.
