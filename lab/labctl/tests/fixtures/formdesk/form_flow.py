"""A scripted session on the permit form (form_app.py): a person leaves the name empty (its
message shows), types an address that is not one (its message shows), enters dates in the wrong
order (the message shows), then fills the form correctly: a country (its regions load from the
API), a region, the check box that shows the vehicle block, a plate, notes; the Send button
becomes usable and the application is sent.

Rules the session never triggers, on purpose: the reference code's length and characters, the
phone number's pattern, "a phone number or an e-mail address", the two addresses differing, the
plate's pattern, the session-timeout warning.

`TYPED` holds every value the session types: none may appear in any file written from the
recording.
"""

from __future__ import annotations

TYPED = {
    "name": "Ingrid Solberg",
    "bad_email": "ingrid.at.fixture",
    "email": "ingrid.solberg@fixture-mail.test",
    "phone": "+47 912 34 567",
    "ref": "kq48zt",
    "start": "2026-12-01",
    "bad_end": "2026-11-20",
    "end": "2026-12-31",
    "plate": "EL48213",
    "notes": "Visitor parking near the north gate",
}


def pause(page, ms: int = 450) -> None:
    page.wait_for_timeout(ms)


def run(page) -> None:
    t = TYPED
    pause(page, 600)
    page.click("#fullName")
    pause(page)
    page.click("#email")  # the name was left empty: its message shows
    page.wait_for_selector("#fullNameError:not([hidden])")
    pause(page)
    page.fill("#fullName", t["name"])
    page.fill("#email", t["bad_email"])
    page.click("#confirmEmail")  # not an address: its message shows
    page.wait_for_selector("#emailError:not([hidden])")
    pause(page)
    page.fill("#email", t["email"])
    page.fill("#confirmEmail", t["email"])
    page.fill("#phone", t["phone"])
    page.fill("#refCode", t["ref"])
    page.fill("#startDate", t["start"])
    page.fill("#endDate", t["bad_end"])  # before the start: the message shows
    page.wait_for_selector("#dateError:not([hidden])")
    pause(page)
    page.fill("#endDate", t["end"])
    pause(page)
    page.select_option("#country", "NO")  # the regions load
    page.wait_for_selector("#region:not([disabled])")
    pause(page)
    page.select_option("#region", "OS")
    pause(page)
    page.check("#hasVehicle")  # the vehicle block shows
    page.wait_for_selector("#vehicleBlock:not([hidden])")
    pause(page)
    page.fill("#plate", t["plate"])
    page.fill("#notes", t["notes"])
    page.wait_for_selector("#submitBtn:not([disabled])")
    pause(page)
    page.click("#submitBtn")
    page.wait_for_selector("#formMessage:has-text('Application received.')")
    pause(page, 700)
