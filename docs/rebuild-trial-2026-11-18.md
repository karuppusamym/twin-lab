# Blind rebuild trial, 18 November 2026

Question: is a build package enough for a coding agent to rebuild an application it has never
seen, so that the recording of the original replays on the rebuild?

## Setup

- Source: two small multi-page applications, `orders` and `billing` (billing embeds a widget
  from a third origin), in `lab/labctl/tests/fixtures/multiapp/`.
- Recording: one scripted flow of 108 steps across two tabs: sign in, search with paging, a new
  order over two forms with validation errors, save, edit, open billing in a new tab, look up
  the order, create an invoice, return to orders and search again.
- Pipeline: `labctl evidence build-all` split the recording into orders (75 steps) and billing
  (29 steps) and wrote one brief and one build package per application.
- Builders: two separate coding-agent sessions (Claude Sonnet), one per application. Each could
  read only its own unpacked package. Neither saw the source, the other package or the judge.
- Judge: `labctl behaviour fidelity` on the whole recording with an origin map
  (`--map source=replica` per application), and on each application's part, on fresh instances.

## Results

| Round | Package | Whole flow | Orders part | Billing part |
|---|---|---|---|---|
| 1 | schema, masked pages, brief | FAIL: 104 of 107 steps, 11 differences | FAIL | FAIL: 29 of 29 steps, 0 page differences, 1 acceptance case |
| 2 | plus seed rows, replay values, display formats, validation triggers | PASS: 107 of 107, 0 differences, 16 cases | PASS: 75 of 75, 10 cases | PASS: 29 of 29, 6 cases |

Round 1 replicated screens and behaviour but failed on data: the package had no seed rows, so
the search the replay typed found nothing, and no display format was stated for the invoice
amount. Before round 1 the first package review had already found that interface text was
masked along with data; that was fixed first. Round 2 used new builders and new packages.

The round 2 verdict is PASS WITHIN DECLARED SCOPE. Three computed values were excluded from the
comparison because no recording can show their rule: the invoice total, the order total on the
saved page, and the Total column of the search list.

## What this does and does not show

- It shows that the capture, the split into applications, the package and the replay work
  together on a multi-page flow across two applications and two tabs.
- The source applications are fixtures written for the test, small and server-rendered. A real
  application has more screens, more rules and data no recording shows.
- The builders used the lab default stack (FastAPI, SQLite) because the evidence identified no
  server technology. A rebuild in a named technology (JSP, ASP.NET, Angular) was not tried here.
- Prices, tax factors, the exact search rule and valid credentials cannot be learned from a
  recording. The package states them as unknown or as hypotheses; an application owner has to
  supply them if they matter.
- The trial files (packages, both builds, judge reports) are kept outside the repository.
