"""The scripted session on Caseboard (JSF) that Twin Lab records: `flow(page, base_url)`.

As a person would: a wrong password, then signing in; paging the list forward and back; a
search; opening a case; Save with the title emptied (a required-field message); Save with
letters for the hours (a converter message); choosing the status "Closed", which re-renders a
part of the form by ajax and shows a Resolution field; filling that in, a radio button, a check
box; Save, the confirmation page, back to the list where the new title shows; sign out.

`page` is Playwright's sync API page (the recorder hands in its own wrapper of it). Every
element is addressed by the id JSF generates from the component ids (`form:component`, with a
colon, and `list:table:1:open` for the second row of the table), except Sign out, whose form has
no id in the page source (JSF names it `j_idtN`, a number that moves when the page changes), so
it is found by its role and name as a person finds it.

`TYPED` holds every value typed or chosen from a list, in order. The recorder never writes
them; a replay gets them from bindings (`bindings_for`). All of it is synthetic.
"""

from __future__ import annotations

START = "/login.xhtml"
PAUSE = 600  # longer than the recorder's settle time: each page is snapshotted before the next

USER, WRONG_PASSWORD, PASSWORD = "casey.morgan", "not-the-password", "demo"
SEARCH = "Okafor"  # an owner: three of the twelve cases
NEW_TITLE = "Expense report resubmitted"
BAD_HOURS, HOURS = "seven", "7"
RESOLUTION = "Paid in the March run"
OPENED = "C-1003"  # the second of Okafor's cases

# (field id, value) for every step that types or chooses from a list, in order.
TYPED = [
    ("loginForm:username", USER),
    ("loginForm:password", WRONG_PASSWORD),
    ("loginForm:password", PASSWORD),
    ("search:q", SEARCH),
    ("edit:title", ""),
    ("edit:title", NEW_TITLE),
    ("edit:hours", BAD_HOURS),
    ("edit:hours", HOURS),
    ("edit:status", "Closed"),  # chosen from the list, not typed: a replay needs it all the same
    ("edit:resolution", RESOLUTION),
]


def by_id(client_id: str) -> str:
    """A selector for a JSF client id: the colon would start a pseudo-class in `#a:b`."""
    return f'[id="{client_id}"]'


def bindings_for(valued_steps: list[int]) -> dict:
    """The bindings document for a recording of this flow: each `type` or `select` step (by its
    number, in order) gets the value the flow gave. An emptied field is bound to the empty
    string."""
    if len(valued_steps) != len(TYPED):
        raise ValueError(
            f"{len(valued_steps)} type/select steps recorded, the flow has {len(TYPED)}"
        )
    steps = {seq: {"value": value} for seq, (_, value) in zip(valued_steps, TYPED, strict=True)}
    return {"version": 1, "steps": steps}


def acceptance_for(steps: list[dict]) -> dict:
    """The acceptance cases for a recording of this flow (`acceptance.yaml`): what Caseboard must
    show at each point, declared from the application's rules (CaseStore, the pages), not from
    what a recording or a replica showed. `steps` are the recording's steps (steps.jsonl); they
    are used only to find the step number each case is checked after."""

    def acted(action: str, client_id: str, nth: int = 0) -> int:
        escaped = client_id.replace(":", "\\:")
        hits = [s["seq"] for s in steps if s["action"] == action
                and (s.get("selector") or "").endswith("#" + escaped)]  # fmt: skip
        return hits[nth]

    def page_after(action: str, client_id: str, nth: int = 0) -> int:
        """The page load the nth such action caused."""
        cause = acted(action, client_id, nth)
        return next(
            s["seq"] for s in steps if s["action"] == "goto" and s.get("caused_by") == cause
        )

    def text(case: str, after: int, client_id: str, equals: str) -> dict:
        return {"id": case, "after": after, "check": "text", "locator": by_id(client_id),
                "equals": equals}  # fmt: skip

    def count(case: str, after: int, css: str, equals: int) -> dict:
        return {"id": case, "after": after, "check": "count", "locator": css, "equals": equals}

    rows = by_id("list:table") + " tbody tr"
    searched = page_after("click", "search:go")
    saved = page_after("click", "edit:save", 2)
    back = page_after("click", "backToList")
    cases = [
        {"id": "wrong-password-message", "after": page_after("click", "loginForm:signIn", 0),
         "check": "text", "locator": "#messages li", "equals": "Wrong user name or password"},
        count("list-first-page-of-twelve", page_after("click", "loginForm:signIn", 1), rows, 5),
        text("twelve-cases", page_after("click", "loginForm:signIn", 1), "list:total", "12 cases"),
        text("second-page", page_after("click", "list:next", 0), "list:pageOf", "Page 2 of 3"),
        text("third-page", page_after("click", "list:next", 1), "list:pageOf", "Page 3 of 3"),
        count("third-page-has-two", page_after("click", "list:next", 1), rows, 2),
        text("back-to-second", page_after("click", "list:previous"), "list:pageOf", "Page 2 of 3"),
        count("search-finds-three", searched, rows, 3),
        text("search-total", searched, "list:total", "3 cases"),
        text("opened-the-second-found", page_after("click", "list:table:1:open"), "number", OPENED),
        {"id": "required-message", "after": page_after("click", "edit:save", 0), "check": "text",
         "locator": "#messages li", "equals": "Title is required"},
        {"id": "converter-message", "after": page_after("click", "edit:save", 1), "check": "text",
         "locator": "#messages li", "equals": "Hours must be a whole number"},
        count("ajax-shows-resolution", acted("select", "edit:status"), by_id("edit:resolution"), 1),
        count("ajax-hides-the-open-note", acted("select", "edit:status"), by_id("edit:openNote"), 0),
        text("confirmation-number", saved, "savedNumber", OPENED),
        text("confirmation-title", saved, "savedTitle", NEW_TITLE),
        text("confirmation-status", saved, "savedStatus", "Closed"),
        text("confirmation-priority", saved, "savedPriority", "High"),
        text("confirmation-hours", saved, "savedHours", HOURS),
        text("list-shows-the-saved-title", back, "list:table:1:open", NEW_TITLE),
        count("list-still-searched", back, rows, 3),
        {"id": "signed-out-at-sign-in", "after": "end", "check": "url", "equals": START},
        count("sign-in-form-again", "end", by_id("loginForm:signIn"), 1),
    ]  # fmt: skip
    return {
        "version": 1,
        "scope": ["sign-in and its message", "paging and search of the list", "edit with "
                  "validation, the ajax update and Save", "the saved value in the list",
                  "sign out"],
        "cases": cases,
    }  # fmt: skip


def flow(page, base_url: str) -> None:
    base = base_url.rstrip("/")
    if "/login.xhtml" not in page.url:
        page.goto(base + START)
    page.wait_for_selector(by_id("loginForm:username"))
    page.wait_for_timeout(PAUSE)

    def click(client_id: str) -> None:
        page.click(by_id(client_id))
        page.wait_for_timeout(PAUSE)

    def fill(client_id: str, value: str) -> None:
        page.fill(by_id(client_id), value)

    # Sign in: a wrong password first (the page comes back with a message), then the right one.
    fill("loginForm:username", USER)
    fill("loginForm:password", WRONG_PASSWORD)
    click("loginForm:signIn")
    page.wait_for_selector("#messages li")
    fill("loginForm:password", PASSWORD)
    page.click(by_id("loginForm:signIn"))
    page.wait_for_url("**/cases.xhtml")  # POST, then a redirect
    page.wait_for_timeout(PAUSE)

    # Paging: twelve cases, five to a page.
    click("list:next")
    click("list:next")
    click("list:previous")

    # Search, then open the second case found.
    fill("search:q", SEARCH)
    click("search:go")
    page.click(by_id("list:table:1:open"))
    page.wait_for_url("**/edit.xhtml?id=*")
    page.wait_for_timeout(PAUSE)

    # A required field left empty.
    fill("edit:title", "")
    click("edit:save")
    page.wait_for_selector("#messages li")

    # Letters where a whole number is wanted.
    fill("edit:title", NEW_TITLE)
    fill("edit:hours", BAD_HOURS)
    click("edit:save")
    page.wait_for_selector("#messages li")

    # The status re-renders the note under it by ajax: "Closed" brings a Resolution field.
    fill("edit:hours", HOURS)
    page.select_option(by_id("edit:status"), "Closed")
    page.wait_for_selector(by_id("edit:resolution"))
    page.wait_for_timeout(PAUSE)
    fill("edit:resolution", RESOLUTION)
    page.check(by_id("edit:priority:2"))  # High
    page.check(by_id("edit:urgent"))

    # Save: POST, a redirect to the confirmation, and back to the list.
    page.click(by_id("edit:save"))
    page.wait_for_url("**/saved.xhtml?id=*")
    page.wait_for_timeout(PAUSE)
    page.click(by_id("backToList"))
    page.wait_for_url("**/cases.xhtml")
    page.wait_for_timeout(PAUSE)

    # Sign out: the form has no id of its own, so the link is found as a person finds it.
    page.get_by_role("link", name="Sign out").click()
    page.wait_for_url("**/login.xhtml")
    page.wait_for_timeout(PAUSE)
