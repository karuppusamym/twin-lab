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

## Privacy

A HAR or a database export from a real system holds customer data, cookies and tokens. So:

- Raw evidence lives in an evidence store outside the repository (`evidence/`, ignored by Git, or
  a shared location with access control). Analysers run where it is.
- Parsing drops cookies, `Authorization` and other credential headers at once.
- The model keeps shapes, not values: field names, types, lengths, code lists. Example values
  are replaced by synthetic values of the same shape.
- Where real-shaped values are needed, they go through the keyed masking from Prod Echo first.
- Only the model, the brief and the generated schema are committed.

## Proving it on the reference app

We own the reference app, so we can produce every kind of evidence from it: source, a HAR from a
scripted browser run, its DDL, its OpenAPI description, a written flow description and
screenshots. Building briefs from each subset, and comparing each with the brief built from
source, measures what each mode loses. That comparison is how we will know how far to trust a
reconstruction of QTT before QTT is attempted.
