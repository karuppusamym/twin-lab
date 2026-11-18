"""Independent paired business checks, across two origins and five pages."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def exercise(page, a, b, output, label):
    checks = []

    def passed(name):
        checks.append(name)

    page.goto(a + "/checkboxes")
    first, second = (
        page.locator("#checkboxes input").nth(0),
        page.locator("#checkboxes input").nth(1),
    )
    expect(first).not_to_be_checked()
    expect(second).to_be_checked()
    passed("checkbox initial state")
    first.check()
    expect(first).to_be_checked()
    passed("check first")
    second.uncheck()
    expect(second).not_to_be_checked()
    passed("uncheck second")
    page.screenshot(path=str(output / f"{label}-checkboxes.png"), full_page=True)
    page.goto(a + "/dropdown")
    page.select_option("#dropdown", "1")
    expect(page.locator("#dropdown")).to_have_value("1")
    passed("select option one")
    page.select_option("#dropdown", "2")
    expect(page.locator("#dropdown")).to_have_value("2")
    passed("select option two")
    page.screenshot(path=str(output / f"{label}-dropdown.png"), full_page=True)
    page.goto(a + "/add_remove_elements/")
    page.get_by_role("button", name="Add Element").click()
    expect(page.locator(".added-manually")).to_have_count(1)
    passed("add element")
    page.get_by_role("button", name="Add Element").click()
    expect(page.locator(".added-manually")).to_have_count(2)
    passed("add second element")
    page.locator(".added-manually").first.click()
    expect(page.locator(".added-manually")).to_have_count(1)
    passed("delete element")
    page.screenshot(path=str(output / f"{label}-elements.png"), full_page=True)
    page.goto(b + "/selenium/web/web-form.html")
    expect(page.locator("[name=my-disabled]")).to_be_disabled()
    passed("disabled input")
    assert page.locator("[name=my-readonly]").get_attribute("readonly") is not None
    passed("readonly input")
    page.locator("[name=my-text]").fill("Synthetic trial")
    expect(page.locator("[name=my-text]")).to_have_value("Synthetic trial")
    passed("text entry")
    page.locator("[name=my-textarea]").fill("Synthetic multi application test")
    expect(page.locator("[name=my-textarea]")).to_have_value("Synthetic multi application test")
    passed("textarea entry")
    page.select_option("[name=my-select]", "2")
    expect(page.locator("[name=my-select]")).to_have_value("2")
    passed("form selection")
    page.locator("#my-check-1").uncheck()
    expect(page.locator("#my-check-1")).not_to_be_checked()
    passed("form uncheck")
    page.locator("#my-check-2").check()
    expect(page.locator("#my-check-2")).to_be_checked()
    passed("form check")
    page.locator("#my-radio-2").check()
    expect(page.locator("#my-radio-1")).not_to_be_checked()
    expect(page.locator("#my-radio-2")).to_be_checked()
    passed("radio exclusivity")
    page.screenshot(path=str(output / f"{label}-form.png"), full_page=True)
    page.get_by_role("button", name="Submit", exact=True).click()
    expect(page.locator("#message")).to_have_text("Received!")
    passed("submission navigation and message")
    query = parse_qs(urlsplit(page.url).query)
    assert query["my-text"] == ["Synthetic trial"] and query["my-select"] == ["2"]
    passed("submitted query values")
    page.screenshot(path=str(output / f"{label}-submitted.png"), full_page=True)
    page.go_back()
    expect(page.locator("[name=my-text]")).to_have_value("Synthetic trial")
    passed("back restores form")
    page.goto(a + "/checkboxes")
    expect(page.locator("#checkboxes input").nth(0)).not_to_be_checked()
    expect(page.locator("#checkboxes input").nth(1)).to_be_checked()
    passed("return to first app resets page state")
    page.goto(a + "/dropdown")
    page.select_option("#dropdown", "1")
    expect(page.locator("#dropdown")).to_have_value("1")
    passed("first app second visit")
    page.goto(b + "/selenium/web/web-form.html")
    expect(page.locator("[name=my-text]")).to_have_value("")
    passed("second app fresh navigation clears input")
    page.locator("[name=my-text]").fill("Second synthetic visit")
    page.get_by_role("button", name="Submit", exact=True).click()
    expect(page.locator("#message")).to_have_text("Received!")
    assert parse_qs(urlsplit(page.url).query)["my-text"] == ["Second synthetic visit"]
    passed("second cross-app submission")
    return checks


def main():
    out = ROOT / "apps/web-controls/validation"
    out.mkdir(exist_ok=True)
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, a, b in [
            ("source", "https://the-internet.herokuapp.com", "https://www.selenium.dev"),
            ("replica", "http://127.0.0.1:8737", "http://127.0.0.1:8738"),
        ]:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            external = []
            errors = []
            if label == "replica":

                def gate(route, external=external):
                    if urlsplit(route.request.url).netloc not in (
                        "127.0.0.1:8737",
                        "127.0.0.1:8738",
                    ):
                        external.append(route.request.url)
                        route.abort()
                    else:
                        route.continue_()

                context.route("**/*", gate)
            page = context.new_page()
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            checks = exercise(page, a, b, out, label)
            results[label] = {
                "passed": len(checks),
                "checks": checks,
                "external_attempts": external,
                "page_errors": errors,
            }
            if label == "replica":
                assert not external, external
                assert not errors, errors
                for base, path in [(a, "/checkboxes"), (b, "/selenium/web/web-form.html")]:
                    assert context.request.get(base + "/api/health").status == 200
                    assert context.request.get(base + "/not-recorded").status == 404
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.goto(base + path)
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            context.close()
        browser.close()
    assert results["source"]["checks"] == results["replica"]["checks"]
    (out / "paired-business-checks.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
