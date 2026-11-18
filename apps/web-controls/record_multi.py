import json
import time
from pathlib import Path

from playwright.sync_api import expect

from labctl.recorder import record

OUT = Path("evidence/multi-app-trial/combined")
A = "https://the-internet.herokuapp.com"
B = "https://www.selenium.dev"
checks = []


def flow(page):
    start = time.monotonic()

    def checkpoint(name):
        page.wait_for_timeout(2200)
        checks.append(
            {"name": name, "url": page.url, "elapsed_s": round(time.monotonic() - start, 2)}
        )
        print(name, flush=True)

    expect(page.locator("#checkboxes input").nth(0)).not_to_be_checked()
    checkpoint("A checkboxes initial")
    page.locator("#checkboxes input").nth(0).check()
    checkpoint("A first checked")
    page.locator("#checkboxes input").nth(1).uncheck()
    checkpoint("A second unchecked")
    page.goto(A + "/dropdown")
    checkpoint("A dropdown page")
    page.select_option("#dropdown", "1")
    checkpoint("A select one")
    page.select_option("#dropdown", "2")
    checkpoint("A select two")
    page.goto(A + "/add_remove_elements/")
    checkpoint("A elements page")
    page.get_by_role("button", name="Add Element").click()
    checkpoint("A add one")
    page.get_by_role("button", name="Add Element").click()
    checkpoint("A add two")
    expect(page.locator(".added-manually")).to_have_count(2)
    page.locator(".added-manually").first.click()
    checkpoint("A remove one")
    page.goto(B + "/selenium/web/web-form.html")
    checkpoint("B form page")
    page.locator("[name=my-text]").fill("Synthetic trial")
    checkpoint("B text input")
    page.locator("[name=my-textarea]").fill("Synthetic multi application test")
    checkpoint("B textarea")
    page.locator("[name=my-select]").select_option("2")
    checkpoint("B select two")
    page.locator("#my-check-1").uncheck()
    checkpoint("B first unchecked")
    page.locator("#my-check-2").check()
    checkpoint("B second checked")
    page.locator("#my-radio-2").check()
    checkpoint("B radio second")
    expect(page.locator("[name=my-disabled]")).to_be_disabled()
    page.get_by_role("button", name="Submit", exact=True).click()
    expect(page.locator("#message")).to_have_text("Received!")
    checkpoint("B submitted page")
    page.go_back()
    checkpoint("B back to form")
    page.goto(A + "/checkboxes")
    checkpoint("A return from B")
    expect(page.locator("#checkboxes input").nth(0)).not_to_be_checked()
    page.locator("#checkboxes input").nth(0).check()
    checkpoint("A check after return")
    page.goto(A + "/dropdown")
    checkpoint("A dropdown again")
    page.select_option("#dropdown", "1")
    checkpoint("A choose one again")
    page.goto(B + "/selenium/web/web-form.html")
    checkpoint("B revisit form")
    page.locator("[name=my-text]").fill("Second synthetic visit")
    checkpoint("B second entry")
    page.get_by_role("button", name="Submit", exact=True).click()
    expect(page.locator("#message")).to_have_text("Received!")
    checkpoint("B second submit")
    while time.monotonic() - start < 65:
        page.wait_for_timeout(1000)
    (OUT / "trial-checkpoints.json").write_text(
        json.dumps({"duration_s": round(time.monotonic() - start, 2), "checks": checks}, indent=2),
        encoding="utf-8",
    )


record(A + "/checkboxes", OUT, script=flow, headless=True)
print("Recording completed", flush=True)
