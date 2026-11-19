"""The scripted session on Caseboard PF (PrimeFaces) that Twin Lab records: `flow(page, base_url)`.

It goes through everything an office PrimeFaces application does that a recording must keep
apart, as a person would:

- sign-in by a full postback that FORWARDS to the workspace: the address bar keeps showing
  `/login.xhtml` while the workspace is on the screen (the address is one step behind);
- the workspace: one address (`/workspace.xhtml`) whose sub-pages (cases, a case's detail, the
  new-case wizard, reports) are swapped inside `main:content` by ajax (a dynamic `ui:include`);
- a lazy `p:dataTable`: paging, sorting (twice on one column, then another), filtering (Enter in
  the column filter), row expansion, row selection, all ajax;
- a `p:dialog` (quick note) opened and saved, a `p:confirmDialog` answered No, then Yes;
- a `p:tabView` whose tabs load on first open, a `p:wizard` (a required-field message on the first
  step, then the owner picked in a dialog-framework page shown in an iframe, then confirm), a
  `p:accordionPanel` whose panels load on first open;
- `p:remoteCommand` (autoRun) on every load of the workspace;
- "Open case page": a postback that forwards to `/case.xhtml` while the address shows
  `/workspace.xhtml`, and back again (the address then shows `/case.xhtml` for the workspace).

`TYPED` holds every value typed or chosen from a list, in order. The recorder never writes them;
a replay gets them from bindings (`bindings_for`). All of it is synthetic.
"""

from __future__ import annotations

START = "/login.xhtml"
PAUSE = 700  # longer than the recorder's settle time: each step is snapshotted before the next

USER, WRONG_PASSWORD, PASSWORD = "casey.morgan", "not-the-password", "demo"
# Three of the seventeen titles hold it. A typed value is taken out of what the recorder writes
# only where it is a value, as a whole word (labctl.typedvalues): the id `main:navReports` and
# the menu item "Reports" keep it, so the Reports sub-page is recorded and replayed by its id.
FILTER = "report"
NOTE = "Called the requester back"
NEW_TITLE = "Window blind stuck"

TYPED = [
    ("loginForm:username", USER),
    ("loginForm:password", WRONG_PASSWORD),
    ("loginForm:password", PASSWORD),
    ("main:cases:colTitle:filter", FILTER),
    ("main:noteText", NOTE),
    ("main:cases:colTitle:filter", ""),
    ("main:newTitle", NEW_TITLE),
    ("main:newPriority", "High"),  # chosen from the list: a replay needs it all the same
]


# The rows the flow clicks (row expansion, selection, the row opened as a page), by their title:
# the row's text without the text of its controls, as Caseboard PF's seed has them.
RECORDS = [
    "C-2013 Travel report overdue Abernathy Open",  # filtered, sorted by owner: the first row
    "C-2016 Quarterly report template Lindqvist Open",  # the second row
    "C-2001 Printer jam on floor three Okafor Open",  # the first row of the unsorted list
]


def by_id(client_id: str) -> str:
    """A selector for a JSF client id: the colon would start a pseudo-class in `#a:b`."""
    return f'[id="{client_id}"]'


def bindings_for(valued_steps: list[int], record_steps: list[int] = ()) -> dict:
    """The bindings document for a recording of this flow: each `type` or `select` step (by its
    number, in order) gets the value the flow gave; each step on a table row (`record_steps`,
    the steps whose first locator is a record) the title of that row."""
    if len(valued_steps) != len(TYPED):
        raise ValueError(
            f"{len(valued_steps)} type/select steps recorded, the flow has {len(TYPED)}"
        )
    steps = {seq: {"value": value} for seq, (_, value) in zip(valued_steps, TYPED, strict=True)}
    if record_steps:
        if len(record_steps) != len(RECORDS):
            raise ValueError(f"{len(record_steps)} row steps recorded, the flow has {len(RECORDS)}")
        for seq, title in zip(record_steps, RECORDS, strict=True):
            steps[seq] = {"record": title}
    return {"version": 1, "steps": steps}


def flow(page, base_url: str) -> None:
    base = base_url.rstrip("/")
    if "/login.xhtml" not in page.url:
        page.goto(base + START)
    page.wait_for_selector(by_id("loginForm:username"))
    page.wait_for_timeout(PAUSE)

    def pause() -> None:
        page.wait_for_timeout(PAUSE)

    def click(client_id: str) -> None:
        page.click(by_id(client_id))
        pause()

    def fill(client_id: str, value: str) -> None:
        page.fill(by_id(client_id), value)

    def shown(client_id: str) -> None:
        page.wait_for_selector(by_id(client_id), state="visible")

    # Sign in: a wrong password first, then the right one. Both are full postbacks; the second
    # FORWARDS to the workspace, so the address stays /login.xhtml.
    fill("loginForm:username", USER)
    fill("loginForm:password", WRONG_PASSWORD)
    click("loginForm:signIn")
    page.wait_for_selector(".ui-messages-error")
    fill("loginForm:password", PASSWORD)
    page.click(by_id("loginForm:signIn"))
    shown("main:cases")
    pause()

    # The lazy table: paging, sorting, filtering, row expansion, row selection.
    page.click('[aria-label="Next Page"]')
    pause()
    page.click('[aria-label="Next Page"]')
    pause()
    page.click('[aria-label="Previous Page"]')
    pause()
    click("main:cases:colNumber")  # ascending
    click("main:cases:colNumber")  # descending
    click("main:cases:colOwner")
    fill("main:cases:colTitle:filter", FILTER)
    page.press(by_id("main:cases:colTitle:filter"), "Enter")
    pause()
    page.click('[id="main:cases_data"] tr[data-ri="0"] .ui-row-toggler')
    page.wait_for_selector(".ui-expanded-row-content")
    pause()
    page.click('[id="main:cases_data"] tr[data-ri="1"] td:nth-child(3)')
    page.wait_for_selector(by_id("main:openDetail") + ":not(.ui-state-disabled)")
    pause()

    # A dialog: a quick note, saved.
    click("main:quickNote")
    shown("main:noteDlg")
    fill("main:noteText", NOTE)
    click("main:saveNote")
    page.wait_for_selector(by_id("main:noteDlg"), state="hidden")

    # A confirm dialog: No, then Yes.
    click("main:closeCase")
    shown("main:confirm")
    click("main:confirmNo")
    click("main:closeCase")
    shown("main:confirm")
    click("main:confirmYes")

    # The filter emptied again: every case.
    fill("main:cases:colTitle:filter", "")
    page.press(by_id("main:cases:colTitle:filter"), "Enter")
    pause()

    # The case's detail: a sub-page of the same address, with tabs that load on first open.
    click("main:openDetail")
    shown("main:tabs")
    page.get_by_role("tab", name="Notes").click()
    pause()
    page.get_by_role("tab", name="History").click()
    pause()
    click("main:backToList")
    shown("main:cases")

    # The new-case wizard: Next with no title (a message), then the steps.
    click("main:navNew")
    shown("main:wiz")
    click("main:wiz_next")
    page.wait_for_selector(".ui-messages-error")
    fill("main:newTitle", NEW_TITLE)
    page.select_option(by_id("main:newPriority"), "High")
    click("main:wiz_next")
    shown("main:pickOwner")
    # The dialog framework: picker.xhtml in an iframe inside a dialog.
    page.click(by_id("main:pickOwner"))
    frame = page.frame_locator("iframe")
    frame.locator(by_id("pick:owners:2:choose")).wait_for()
    pause()
    frame.locator(by_id("pick:owners:2:choose")).click()
    page.wait_for_selector("iframe", state="detached")
    pause()
    click("main:wiz_next")
    shown("main:finish")
    click("main:finish")
    shown("main:cases")

    # Reports: an accordion whose panels load on first open.
    click("main:navReports")
    shown("main:acc")
    click("main:acc:byOwner_header")
    click("main:acc:byPriority_header")

    # Back to the cases; open the selected case's own page by a postback that forwards: the
    # address shows /workspace.xhtml for /case.xhtml, then /case.xhtml for the workspace.
    click("main:navCases")
    shown("main:cases")
    page.click('[id="main:cases_data"] tr[data-ri="0"] td:nth-child(3)')
    page.wait_for_selector(by_id("main:openPage") + ":not(.ui-state-disabled)")
    pause()
    page.click(by_id("main:openPage"))
    shown("casePage:backToWorkspace")
    pause()
    page.click(by_id("casePage:backToWorkspace"))
    shown("main:cases")
    pause()

    # Sign out: a command link, then a redirect to sign-in.
    page.click(by_id("main:signOut"))
    page.wait_for_url("**/login.xhtml")
    pause()
