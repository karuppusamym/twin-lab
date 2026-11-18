"""The scripted session across the two fixture applications (apps.py), as a person would do it:
sign in to orders (an error first), search with paging, create an order with validation errors,
change it, open billing in a new tab from the saved order, look the order up there (an error
first), write and save its invoice (an error first), look it up again, close the tab, and back in
orders search for the new order, go back and forward, and sign out.

`TYPED` holds every value the session types (none may appear in any file written from the
recording). `EXPECTED` is the order of the actions, declared from the flow below (not from what
the recorder produced): (action, tab, a part of the selector) for every step but page loads and
the submits a click or key press causes. `SYNTHETIC` are the values a replay types instead, per
application, per field, in order.
"""

from __future__ import annotations

TYPED = {
    "user": "clerk.marsh",
    "password": "Tide-Pool-77",
    "customer_search": "Hartley",
    "name": "Mirela Vasquez-Ode",
    "bad_email": "mirela.at.example",
    "email": "mirela.vo@example.test",
    "phone": "0161 496 0731",
    "date": "2026-12-04",
    "notes": "Ring the bell twice",
    "phone2": "0161 496 0999",
    "notes2": "Leave it at reception",
    "bad_ref": "Q7Z",
    "bad_amount": "twelve",
    "amount": "149.85",
    "memo": "Gift order, invoice by post",
    "no_hit": "ORD-9999",
}

A, B = "t1", "t2"
EXPECTED = [
    ("type", A, "#username"), ("click", A, "#signIn"),  # no password: an error
    ("type", A, "#password"), ("keypress:Enter", A, "#password"),
    ("select", A, "#searchBy"), ("click", A, "#searchBtn"),  # nothing to search for: an error
    ("type", A, "#q"), ("click", A, "#searchBtn"),
    ("click", A, "#nextPage"), ("click", A, "#nextPage"), ("click", A, "#prevPage"),
    ("click", A, "#newOrder"), ("click", A, "#toItems"),  # nothing given: errors
    ("type", A, "#customerName"), ("type", A, "#customerEmail"), ("click", A, "#toItems"),
    ("type", A, "#customerEmail"), ("type", A, "#customerPhone"), ("click", A, "#toItems"),
    ("select", A, "#product"), ("type", A, "#quantity"), ("click", A, "#saveOrder"),  # errors
    ("type", A, "#quantity"), ("type", A, "#deliveryDate"), ("check", A, "#giftWrap"),
    ("type", A, "#notes"), ("click", A, "#saveOrder"),
    ("click", A, "#editSaved"), ("type", A, "#customerPhone"), ("click", A, "#toItems"),
    ("click", A, "#backToCustomer"), ("click", A, "#toItems"),
    ("uncheck", A, "#giftWrap"), ("type", A, "#notes"), ("click", A, "#saveOrder"),
    ("click", A, "#openBilling"), ("tab-open", B, None),
    ("click", B, "a.nav__item"),
    ("type", B, "orderRef"), ("click", B, "button"),  # not an order reference: an error
    ("type", B, "orderRef"), ("click", B, "button"),
    ("type", B, "payer"), ("select", B, "terms"), ("type", B, "amount"), ("click", B, "button"),
    ("type", B, "amount"), ("check", B, "vat"), ("type", B, "memo"), ("click", B, "button"),
    ("click", B, "a.btn"), ("type", B, "orderRef"), ("keypress:Enter", B, "orderRef"),
    ("tab-close", B, None), ("tab-switch", A, None),
    ("click", A, "#searchAgain"),
    ("select", A, "#searchBy"), ("type", A, "#q"), ("keypress:Enter", A, "#q"),
    ("type", A, "#q"), ("click", A, "#searchBtn"),
    ("click", A, "#signOut"),
]  # fmt: skip

# What a replay types instead, per application: selector part -> values in the order typed.
SYNTHETIC = {
    "orders": {
        "#username": ["clerk.syn"], "#password": ["Syn-pass-1"],
        "#searchBy": ["Customer name", "Order number"], "#q": ["Lindqvist", "ORD-1007", "QQQ-0001"],
        "#customerName": ["Syn Customer"], "#customerEmail": ["syn-bad", "syn@example.test"],
        "#customerPhone": ["0100 111 2222", "0100 111 3333"], "#product": ["Desk lamp"],
        "#quantity": ["0", "2"], "#deliveryDate": ["2027-01-15"],
        "#notes": ["Syn note one", "Syn note two"],
    },
    "billing": {
        "orderRef": ["BAD9", "ORD-2001", "ORD-2001"], "payer": ["Syn Payer"],
        "terms": ["30 days"], "amount": ["abc", "88.20"], "memo": ["Syn memo"],
    },
}  # fmt: skip


def pause(page, ms: int = 450) -> None:
    page.wait_for_timeout(ms)


def run(page) -> dict:
    """Drive the whole session from the orders sign-in page. `page` is the recorder's page (its
    `wrap` hands out the billing tab the same way) or a plain Playwright page."""
    t = TYPED
    wrap = getattr(page, "wrap", lambda p: p)
    page.fill("#username", t["user"])
    page.click("#signIn")
    pause(page, 700)
    page.fill("#password", t["password"])
    page.press("#password", "Enter")
    pause(page, 800)
    page.select_option("#searchBy", label="Customer name")
    pause(page)
    page.click("#searchBtn")
    pause(page)
    page.fill("#q", t["customer_search"])
    page.click("#searchBtn")
    pause(page, 700)
    for button in ("#nextPage", "#nextPage", "#prevPage"):
        page.click(button)
        pause(page, 700)
    page.click("#newOrder")
    pause(page, 700)
    page.click("#toItems")
    pause(page, 700)
    page.fill("#customerName", t["name"])
    page.fill("#customerEmail", t["bad_email"])
    page.click("#toItems")
    pause(page, 700)
    page.fill("#customerEmail", t["email"])
    page.fill("#customerPhone", t["phone"])
    page.click("#toItems")
    pause(page, 700)
    page.select_option("#product", label="Desk lamp")
    pause(page)
    page.fill("#quantity", "0")
    page.click("#saveOrder")
    pause(page, 700)
    page.fill("#quantity", "3")
    page.fill("#deliveryDate", t["date"])
    page.check("#giftWrap")
    pause(page, 300)
    page.fill("#notes", t["notes"])
    page.click("#saveOrder")
    pause(page, 800)
    number = (page.text_content("#savedNumber") or "").strip()
    page.click("#editSaved")
    pause(page, 700)
    page.fill("#customerPhone", t["phone2"])
    page.click("#toItems")
    pause(page, 700)
    page.click("#backToCustomer")
    pause(page, 700)
    page.click("#toItems")
    pause(page, 700)
    page.uncheck("#giftWrap")
    pause(page, 300)
    page.fill("#notes", t["notes2"])
    page.click("#saveOrder")
    pause(page, 800)

    # billing, in a new tab
    with page.context.expect_page() as opened:
        page.click("#openBilling")
    b = wrap(opened.value)
    b.wait_for_load_state()
    pause(b, 1200)  # the exchange-rate widget loads in its frame
    b.click("[data-testid=nav-lookup]")
    pause(b, 700)
    b.fill("[name=orderRef]", t["bad_ref"])
    b.click("[data-testid=find]")
    pause(b, 700)
    b.fill("[name=orderRef]", number)
    b.click("[data-testid=find]")
    pause(b, 700)
    b.fill("[name=payer]", t["name"])
    b.select_option("[name=terms]", label="30 days")
    pause(b)
    b.fill("[name=amount]", t["bad_amount"])
    b.click("[data-testid=save-invoice]")
    pause(b, 700)
    b.fill("[name=amount]", t["amount"])
    b.check("[name=vat]")
    pause(b, 300)
    b.fill("[name=memo]", t["memo"])
    b.click("[data-testid=save-invoice]")
    pause(b, 800)
    b.click("[data-testid=find-another]")
    pause(b, 700)
    b.fill("[name=orderRef]", number)
    b.press("[name=orderRef]", "Enter")
    pause(b, 900)
    b.close()

    # back in orders
    page.bring_to_front()
    pause(page, 300)
    page.click("#searchAgain")
    pause(page, 700)
    page.go_back()
    pause(page, 700)
    page.go_forward()
    pause(page, 700)
    page.select_option("#searchBy", label="Order number")
    pause(page)
    page.fill("#q", number)
    page.press("#q", "Enter")
    pause(page, 700)
    page.fill("#q", t["no_hit"])
    page.click("#searchBtn")
    pause(page, 700)
    page.click("#signOut")
    pause(page, 800)
    return {"number": number}


SECOND_TYPED = ["Second Flow Person", "second.flow@example.test", "2027-02-02"]


def run_second(page) -> dict:
    """A second, different session on orders alone: sign in, look at the customer list (a screen
    the first session never opened, with its own API call), and create an order for an office
    chair."""
    t = TYPED
    page.fill("#username", t["user"])
    page.fill("#password", t["password"])
    page.click("#signIn")
    pause(page, 800)
    page.click("#navCustomers")
    pause(page, 800)
    page.click("#newOrder")
    pause(page, 700)
    page.fill("#customerName", "Second Flow Person")
    page.fill("#customerEmail", "second.flow@example.test")
    page.click("#toItems")
    pause(page, 700)
    page.select_option("#product", label="Office chair")
    page.fill("#quantity", "1")
    page.fill("#deliveryDate", "2027-02-02")
    page.click("#saveOrder")
    pause(page, 800)
    return {}
