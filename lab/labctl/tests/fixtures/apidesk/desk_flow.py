"""Two scripted sessions on the help-desk fixture (desk_app.py).

`run` is a working session: a protected page asked for before signing in, sign-in (an error
first), the list with paging, search, filter and sort, a new ticket in a modal dialog (two
refusals first), the new ticket's page with its History tab, a change (a refusal first), the
print view in a popup window, the help panel in its iframe, a delete behind a confirmation
dialog (declined once), and sign-out.

`browse` only looks around, as a person asked to "record the screens" would: no record is
created, changed or deleted and nothing is searched for.

`TYPED` holds every value the sessions type; none may appear in any file written from the
recording. `DATA` are values the application itself holds (titles, people, addresses): they may
not appear in any file of a build package either.
"""

from __future__ import annotations

TYPED = {
    "user": "dana.whitlock",
    "password": "Harbour-Lamp-42",
    "search": "printer",
    "title": "Projector bulb gone in room Birch",
    "bad_requester": "wendy.at.fixture",
    "requester": "wendy.arkell@fixture-mail.test",
    "description": "Second floor, by the window",
    "assignee": "Mira Holt",
}
DATA = ["Office printer offline", "Intranet account locked", "Laptop screen flickers", "VPN drops every hour",
        "Mailbox quota reached", "Jonas Weir", "Petra Lund", "Oskar Brandt", "ines.calvo",
        "tomas.reyl", "greta.moss", "fixture-mail.test", "Reported at the front desk"]  # fmt: skip


def pause(page, ms: int = 500) -> None:
    page.wait_for_timeout(ms)


def sign_in(page) -> None:
    t = TYPED
    page.fill("#username", t["user"])
    page.click("#signIn")  # no password: refused
    pause(page, 700)
    page.fill("#password", t["password"])
    page.press("#password", "Enter")
    pause(page, 1000)


def run(page) -> dict:
    """Start the recording at `/tickets` (signed out, so the application redirects to /login)."""
    t = TYPED
    wrap = getattr(page, "wrap", lambda p: p)
    pause(page, 600)
    sign_in(page)
    for button in ("#nextPage", "#nextPage", "#prevPage"):
        page.click(button)
        pause(page, 700)
    page.fill("#q", t["search"])
    page.click("#searchBtn")
    pause(page, 700)
    page.select_option("#statusFilter", label="Open")
    pause(page, 700)
    page.select_option("#sortBy", label="Title")
    pause(page, 700)
    # a new ticket, in the dialog
    page.click("#newTicket")
    pause(page)
    page.click("#saveTicket")  # nothing given: refused
    pause(page, 700)
    page.fill("#title", t["title"])
    page.fill("#requester", t["bad_requester"])
    page.click("#saveTicket")  # not an address: refused
    pause(page, 700)
    page.fill("#requester", t["requester"])
    page.select_option("#priority", label="High")
    page.fill("#description", t["description"])
    page.click("#saveTicket")
    pause(page, 900)
    number = (page.text_content("#tickets tbody tr:first-child td.ticket-id") or "").strip()
    # its page, the tabs, a change
    page.click("#tickets tbody tr:first-child a.ticket-link")
    pause(page, 900)
    page.click("#tabHistory")
    pause(page, 700)
    page.click("#tabDetails")
    pause(page)
    page.select_option("#status", label="Closed")
    page.click("#saveChanges")  # closing without an assignee: refused
    pause(page, 700)
    page.fill("#assignee", t["assignee"])
    page.click("#saveChanges")
    pause(page, 800)
    page.click("#tabHistory")
    pause(page, 700)
    # the print view, in a popup window
    with page.context.expect_page() as opened:
        page.click("#printTicket")
    popup = wrap(opened.value)
    popup.wait_for_load_state()
    pause(popup, 800)
    popup.click("#closePrint")
    page.bring_to_front()
    pause(page, 500)
    page.click("#backToList")
    pause(page, 900)
    # the help panel, a page within the page
    page.frame_locator("#helpFrame").locator("#helpToggle").click()
    pause(page, 600)
    # delete the second-oldest ticket: declined once, then confirmed
    page.select_option("#sortBy", label="Oldest first")
    pause(page, 700)
    page.click("#tickets tbody tr:nth-child(2) a.ticket-link")
    pause(page, 900)
    page.click("#deleteTicket")
    pause(page)
    page.click("#confirmDeleteNo")
    pause(page)
    page.click("#deleteTicket")
    pause(page)
    page.click("#confirmDeleteYes")
    pause(page, 1000)
    page.click("#signOut")
    pause(page, 900)
    return {"number": number}


def browse(page) -> dict:
    """Only looking: sign in, page through the list, open a ticket and its History tab, open
    and cancel the dialogs, look at the print view, sign out."""
    wrap = getattr(page, "wrap", lambda p: p)
    pause(page, 600)
    sign_in(page)
    page.click("#nextPage")
    pause(page, 700)
    page.click("#newTicket")
    pause(page)
    page.click("#cancelTicket")
    pause(page)
    page.click("#tickets tbody tr:first-child a.ticket-link")
    pause(page, 900)
    page.click("#tabHistory")
    pause(page, 700)
    page.click("#deleteTicket")
    pause(page)
    page.click("#confirmDeleteNo")
    pause(page)
    with page.context.expect_page() as opened:
        page.click("#printTicket")
    popup = wrap(opened.value)
    popup.wait_for_load_state()
    pause(popup, 800)
    popup.close()
    page.bring_to_front()
    pause(page, 400)
    page.click("#backToList")
    pause(page, 900)
    page.click("#signOut")
    pause(page, 900)
    return {}
