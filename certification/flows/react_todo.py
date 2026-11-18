"""The scripted flow for the React entry of the certification matrix (apps/web-todo, a to-do
list in React 16.7), as a person would do it: add two tasks, complete the first, look at the
Active and Completed filters and back at All, start editing the second task and cancel with
Escape, edit it and save with Enter, reload the page (tasks stay, they are in the browser's
local storage), complete all, clear the completed ones, add a task and delete it.

Only invented task titles are typed. The expected actions for this flow are written by hand in
certification/matrix.yaml, not taken from a recording.
"""

from __future__ import annotations


def run(page) -> dict:
    new = page.locator(".new-todo")
    rows = page.locator(".todo-list li")

    def pause(ms: int = 400) -> None:
        page.wait_for_timeout(ms)

    def add(title: str) -> None:
        new.fill(title)
        new.press("Enter")
        pause()

    pause(500)
    add("Review synthetic application")
    add("Prepare demo report")
    rows.first.locator(".toggle").check()
    pause()
    for name in ("Active", "Completed", "All"):
        page.get_by_role("link", name=name, exact=True).click()
        pause()
    rows.nth(1).locator("label").dblclick()
    pause(300)
    page.locator(".editing .edit").fill("Discard this edit")
    page.locator(".editing .edit").press("Escape")
    pause()
    rows.nth(1).locator("label").dblclick()
    pause(300)
    page.locator(".editing .edit").fill("Prepare final demo report")
    page.locator(".editing .edit").press("Enter")
    pause()
    page.reload()
    pause(600)
    page.locator('label[for="toggle-all"]').click()
    pause()
    page.get_by_role("button", name="Clear completed").click()
    pause()
    add("Delete synthetic task")
    rows.first.hover()
    rows.first.locator(".destroy").click()
    pause()
    return {}
