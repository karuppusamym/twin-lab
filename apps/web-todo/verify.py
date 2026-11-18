"""Run the same business checks on the public demo and local replica.

python apps/web-todo/verify.py --local http://127.0.0.1:8736/todomvc/
Only invented task titles are submitted. Each browser context starts empty.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent


def exercise(page, url):
    checkpoints = []
    page.goto(url)
    new = page.locator(".new-todo")
    rows = page.locator(".todo-list li")

    def check(name, titles):
        expect(page.locator(".todo-list li label")).to_have_text(titles)
        checkpoints.append(
            {
                "name": name,
                "titles": page.locator(".todo-list li label").all_text_contents(),
                "completed": rows.evaluate_all(
                    '(rows) => rows.map(r => r.classList.contains("completed"))'
                ),
                "remaining": page.locator(".todo-count").all_text_contents(),
                "html": page.locator(".todoapp").evaluate("(e) => e.outerHTML"),
            }
        )

    def add(title):
        new.fill(title)
        new.press("Enter")

    check("empty", [])
    add("   ")
    check("ignore blank task", [])
    add("Review synthetic application")
    add("Prepare demo report")
    check("add two tasks", ["Review synthetic application", "Prepare demo report"])
    expect(page.locator(".todo-count")).to_have_text("2 items left")
    rows.first.locator(".toggle").check()
    check("complete first task", ["Review synthetic application", "Prepare demo report"])
    expect(page.locator(".todo-count")).to_have_text("1 item left")
    page.get_by_role("link", name="Active", exact=True).click()
    check("active filter", ["Prepare demo report"])
    page.get_by_role("link", name="Completed", exact=True).click()
    check("completed filter", ["Review synthetic application"])
    page.get_by_role("link", name="All", exact=True).click()
    rows.nth(1).locator("label").dblclick()
    page.locator(".editing .edit").fill("Discard this edit")
    page.locator(".editing .edit").press("Escape")
    check("cancel edit", ["Review synthetic application", "Prepare demo report"])
    rows.nth(1).locator("label").dblclick()
    page.locator(".editing .edit").fill("Prepare final demo report")
    page.locator(".editing .edit").press("Enter")
    check("save edit", ["Review synthetic application", "Prepare final demo report"])
    page.screenshot(
        path=str(HERE / "validation" / ("source.png" if "https:" in url else "replica.png")),
        full_page=True,
    )
    page.reload()
    check("reload persists", ["Review synthetic application", "Prepare final demo report"])
    expect(rows.first).to_have_class("completed")
    page.locator('label[for="toggle-all"]').click()
    check("complete all", ["Review synthetic application", "Prepare final demo report"])
    expect(page.locator(".todo-count")).to_have_text("0 items left")
    page.get_by_role("button", name="Clear completed").click()
    check("clear completed", [])
    add("Delete synthetic task")
    rows.first.hover()
    rows.first.locator(".destroy").click()
    check("delete task", [])
    add("Erase title")
    rows.first.locator("label").dblclick()
    page.locator(".editing .edit").fill("")
    page.locator(".editing .edit").press("Enter")
    check("empty edit deletes", [])
    return checkpoints


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", default="http://127.0.0.1:8736/todomvc/")
    args = parser.parse_args()
    (HERE / "validation").mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        results = []
        for url in ["https://demo.playwright.dev/todomvc/", args.local]:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            if url == args.local:
                context.route(
                    "**/*",
                    lambda route: route.continue_()
                    if urlsplit(route.request.url).netloc == urlsplit(args.local).netloc
                    else route.abort(),
                )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            results.append(exercise(page, url))
            assert not errors, errors
            if url == args.local:
                # All replica assets and interactions must work with external access blocked.
                resources = page.evaluate(
                    'performance.getEntriesByType("resource").map(r => r.name)'
                )
                external = [
                    r
                    for r in resources
                    if urlsplit(r).scheme in ("http", "https")
                    and urlsplit(r).netloc != urlsplit(args.local).netloc
                ]
                assert not external, external
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            context.close()
        browser.close()
    from labctl.behaviour.fidelity import compare_dom

    report = []
    for source, replica in zip(*results, strict=True):
        semantic = {k: v for k, v in source.items() if k != "html"}
        assert semantic == {k: v for k, v in replica.items() if k != "html"}, source["name"]
        diffs = compare_dom(source["html"], replica["html"])
        report.append(
            {
                "checkpoint": source["name"],
                "business_state": "pass",
                "dom_differences": [d.model_dump() for d in diffs],
            }
        )
    (HERE / "validation/report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    assert all(not r["dom_differences"] for r in report), "DOM differences: see report.json"
    print(f"{len(report)} matching business checkpoints and Twin Lab DOM comparisons passed.")


if __name__ == "__main__":
    main()
