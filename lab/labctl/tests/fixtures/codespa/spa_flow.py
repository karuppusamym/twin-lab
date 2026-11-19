"""The recorded session on the parcel depot (spa_server.py): sign in, the parcel list, the New
parcel dialog opened and cancelled, one parcel's Overview and History tabs, back to the list.

Never visited, so only the code knows them: `/depots`, `/help`, `/admin/users`,
`/admin/settings` (the lazy chunk `admin.js` is never downloaded), sign-out, saving or deleting
a parcel. `TYPED` are the values typed; none may appear in a file written from the recording.
"""

from __future__ import annotations

TYPED = {"user": "wren.okafor", "password": "Synthetic-pass-7"}
DATA = ["Wren Okafor", "Basil Tamm", "Ivo Marsh", "Lena Brook", "Harbour North", "Quarry Lane"]


def run(page) -> None:
    page.wait_for_selector("#signInForm")
    page.wait_for_timeout(500)
    page.fill("#username", TYPED["user"])
    page.fill("#password", TYPED["password"])
    page.click("#signIn")
    page.wait_for_selector("a.parcel-link")
    page.wait_for_timeout(600)
    page.click("#newParcel")
    page.wait_for_selector("#newParcelDialog[open]")
    page.wait_for_timeout(500)
    page.click("#cancelParcel")
    page.wait_for_timeout(500)
    page.click("a.parcel-link >> nth=0")
    page.wait_for_selector("#overview")
    page.wait_for_timeout(600)
    page.click("#tabHistory")
    page.wait_for_selector("#history")
    page.wait_for_timeout(600)
    page.click("#navParcels")
    page.wait_for_selector("a.parcel-link")
    page.wait_for_timeout(600)
