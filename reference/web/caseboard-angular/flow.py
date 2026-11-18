"""The scripted user flow of the Caseboard reference application (Angular), synthetic data only.

`flow(page, base_url)` drives a Playwright page (sync API) the way a person uses the
application: a wrong sign-in, the right one, a search, paging, opening a case, a validation
error, filling the form and saving, back at the list, and signing out through the dialog.

Used with the lab's recorder:

    recorder.record(base_url + START, folder, headless=True, script=lambda p: flow(p, base_url))

The recorder never writes a typed value, so a replay needs each one given again: `INPUTS` lists,
in the order the flow makes them, every step that enters a value (the recorded selector and the
value), and `RECORDS` the title of each record a step is on. `certify_local.py` turns both into
the recording's `bindings.yaml`.

`expected-actions.yaml` is the list of semantic actions this flow performs, written from the
steps below (not from a recording). Keep the two in step.
"""

from __future__ import annotations

START = "/"  # not signed in: the route guard sends the browser to /sign-in
PAUSE = 600  # ms; longer than the recorder's settle time, so each step is snapshotted alone
CLICK_MS = 80  # ms between pressing and releasing the mouse button (see _click)

USER = "demo"
WRONG_PASSWORD = "not-the-password"
PASSWORD = "demo"  # the reference application's only (synthetic) password
SEARCH = "valve"  # two of the twelve cases: CB-1003 and CB-1007
CASE = "CB-1007"  # on the second page of the list
TITLE = "Position sensor recalibrated"
STATUS = "waiting"  # the option's value; its label is "Waiting for customer"
DUE = "2026-12-18"
NOTES = "Checked on site, readings are back to normal."

# Every step that enters a value, in order: (action, recorded selector, value). A field emptied
# (the title, before the validation error) is recorded as blank and needs no value.
INPUTS: list[tuple[str, str, str]] = [
    ("type", "input#username", USER),
    ("type", "input#password", WRONG_PASSWORD),
    ("type", "input#password", PASSWORD),
    ("type", "input#search", SEARCH),
    ("type", "input#title", TITLE),
    ("select", "select#status", STATUS),
    ("type", "input#dueDate", DUE),
    ("type", "textarea#notes", NOTES),
]

# Every step on a record that was there before the recording started: (action, the record's
# title). The recorder treats an id holding a record number (`open-CB-1007`) as data, so the
# step is recorded as "the link in the row titled ...", and the title is given here, as a person
# gives it in bindings.yaml. A row's title is its text.
RECORDS: list[tuple[str, str]] = [
    ("click", f"{CASE} Valve position sensor reads zero Gray Open Normal 2026-12-11 No"),
]


def _click(page, selector: str) -> None:
    """Click as a person does: the button is held for a moment. The recorder snapshots the step
    before a click as the click arrives. Pressing the mouse button is what takes the focus from
    the field typed into, and Angular renders what that changes (the field marked as visited, a
    required field's message) a moment later, at its next change detection: a script's instant
    click would arrive before that, a person's never does."""
    page.click(selector, delay=CLICK_MS)


def _rows(page, count: int) -> None:
    page.wait_for_function(
        "n => document.querySelectorAll('#caseTable tbody tr').length === n", arg=count
    )


def _page_is(page, number: int) -> None:
    page.wait_for_function(
        "t => (document.querySelector('#pageInfo') || {}).textContent.trim() === t",
        arg=f"Page {number} of 3",
    )
    page.wait_for_timeout(PAUSE)


def flow(page, base_url: str) -> None:
    base = base_url.rstrip("/")
    if not page.url.startswith(base):
        page.goto(base + START)

    # The guard: nobody is signed in, so the application shows the sign-in page.
    page.wait_for_url("**/sign-in")
    page.wait_for_selector("#signInForm")
    page.wait_for_timeout(PAUSE)

    # A wrong password shows a message; the right one signs in.
    page.fill("#username", USER)
    page.fill("#password", WRONG_PASSWORD)
    _click(page, "#btnSignIn")
    page.wait_for_selector("#signInError")
    page.wait_for_timeout(PAUSE)
    page.fill("#password", PASSWORD)
    _click(page, "#btnSignIn")
    page.wait_for_url("**/cases")
    _rows(page, 5)
    page.wait_for_timeout(PAUSE)

    # Search filters the table as the text is entered; Clear shows all twelve again.
    page.fill("#search", SEARCH)
    _rows(page, 2)
    page.wait_for_timeout(PAUSE)
    _click(page, "#btnClearSearch")
    _rows(page, 5)
    page.wait_for_timeout(PAUSE)

    # Paging: forward twice (the last page has two cases), back once.
    _click(page, "#btnNextPage")
    _page_is(page, 2)
    _click(page, "#btnNextPage")
    _page_is(page, 3)
    _click(page, "#btnPrevPage")
    _page_is(page, 2)

    # Open a case: a client-side route whose component is a lazy-loaded chunk.
    _click(page, f"#open-{CASE}")
    page.wait_for_url(f"**/cases/{CASE}")
    page.wait_for_selector("#caseForm")
    page.wait_for_timeout(PAUSE)

    # A required field emptied and left (a click into the next field): its message shows. Save
    # is then refused with a second message.
    page.fill("#title", "")
    _click(page, "#owner")
    page.wait_for_selector("#titleError")
    page.wait_for_timeout(PAUSE)
    _click(page, "#btnSave")
    page.wait_for_selector("#formError")
    page.wait_for_timeout(PAUSE)

    # Fill the form (text, select, radio button, check box, date, text area) and save.
    page.fill("#title", TITLE)
    page.select_option("#status", STATUS)
    page.wait_for_timeout(PAUSE)
    page.check("#priority-high")
    page.wait_for_timeout(PAUSE)
    page.check("#urgent")
    page.wait_for_timeout(PAUSE)
    page.fill("#dueDate", DUE)
    page.fill("#notes", NOTES)
    _click(page, "#btnSave")
    page.wait_for_url("**/cases")
    page.wait_for_selector("#savedMessage")
    page.wait_for_selector(f"#case-{CASE} >> text={TITLE}")  # the saved value, in the list
    page.wait_for_timeout(PAUSE)

    # Sign out: the dialog is closed once with Escape, then confirmed.
    _click(page, "#btnSignOut")
    page.wait_for_selector("#signOutDialog")
    page.wait_for_timeout(PAUSE)
    page.press("#btnSignOutCancel", "Escape")
    page.wait_for_selector("#signOutDialog", state="detached")
    page.wait_for_timeout(PAUSE)
    _click(page, "#btnSignOut")
    page.wait_for_selector("#signOutDialog")
    page.wait_for_timeout(PAUSE)
    _click(page, "#btnSignOutConfirm")
    page.wait_for_url("**/sign-in")
    page.wait_for_selector("#signInForm")
    page.wait_for_timeout(PAUSE)
