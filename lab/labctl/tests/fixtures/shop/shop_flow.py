"""Scripted sessions on the fixture shop (shop_server.py).

`run` is a short shopping session: the home page, a category with infinite scroll and "Load
more", a product with its gallery, colour and size picker, cart (a retry), wish list, more
reviews and a review with a photo (refused once), a search with facets, sort and a second page,
the cart with a quantity change and a removal, and a link that redirects twice.

`long(page, products)` is the same kind of session over many products: for the scale test.

`TYPED` holds what the sessions type; none may appear in a package. `DATA` are values the shop
holds (product names, review authors and texts) that must not appear either.
"""

from __future__ import annotations

import shop_server

TYPED = {"search": "linen", "review_title": "Sturdy seams overall", "review_body": "Wore it daily"}
DATA = ["Ilse Varga", "Teo Brask", "Runa Hale", "Bought it for the", "Amber Linen Shirt",
        "Cedar Cotton Coat", "amber-linen", "cedar-cotton"]  # fmt: skip
PHOTO = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 120, "image/png")


def pause(page, ms: int = 400) -> None:
    page.wait_for_timeout(ms)


def _scroll(page) -> None:
    page.mouse.wheel(0, 4000)
    pause(page, 500)


def product_visit(page, n: int, review: bool = False) -> None:
    """On a product page: gallery, colour and size, add to cart, wish list, more reviews."""
    pause(page, 500)
    page.click("#gallery .thumb:nth-child(2)")
    pause(page, 200)
    page.click("#colourPicker .swatch:nth-child(2)")
    pause(page, 400)
    page.select_option("#sizeSelect", shop_server.SIZES[n % 4])
    pause(page, 400)
    page.click("#addToCart")
    pause(page, 900)
    if n % 2 == 0:
        page.click("#addToWishlist")
        pause(page, 400)
    page.click("#moreReviews")
    pause(page, 500)
    if review:
        page.fill("#reviewTitle", TYPED["review_title"])
        page.click("#sendReview")  # no rating: refused
        pause(page, 500)
        page.select_option("#rating", "4")
        page.fill("#reviewBody", TYPED["review_body"])
        page.set_input_files("#photo", files=[{"name": "photo.png", "mimeType": PHOTO[1],
                                               "buffer": PHOTO[0]}])  # fmt: skip
        page.click("#sendReview")
        pause(page, 600)


def run(page) -> dict:
    """Start the recording at `/`."""
    pause(page, 800)
    page.click("#categoryTree a.tree-all")  # every product, from the category tree
    pause(page, 700)
    _scroll(page)  # infinite scroll asks the next page
    page.click("#loadMore")
    pause(page, 600)
    page.click("#productGrid a.product-link >> nth=0")
    product_visit(page, 0, review=True)
    page.click("nav[aria-label=Breadcrumb] li:nth-child(2) a")  # up the breadcrumb
    pause(page, 700)
    _scroll(page)
    page.click("#productGrid a.product-link >> nth=1")
    product_visit(page, 1)
    # the header search, with facets, a sort and the second page
    page.fill("#searchBox", TYPED["search"])
    pause(page, 500)
    page.press("#searchBox", "Enter")
    pause(page, 700)
    page.check("#facets input[name=color][value=blue]")
    pause(page, 700)
    page.select_option("#sortBy", "price-asc")
    pause(page, 700)
    page.goto(page.url.split("?")[0] + "?k=a&sort=price-asc")
    pause(page, 500)
    page.click("#pager a.page-link >> nth=0")
    pause(page, 700)
    page.click("#results a.product-link >> nth=0")
    product_visit(page, 2)
    page.click("#cartLink")
    pause(page, 700)
    page.select_option("#cartTable select.qty >> nth=0", "3")
    pause(page, 600)
    page.click("#cartTable button.remove >> nth=-1")
    pause(page, 600)
    page.hover("#megaMenu li.top >> nth=0")  # the mega-menu opens on hover
    page.click("#megaMenu a.menu-all >> nth=0")  # redirected twice
    pause(page, 800)
    return {}


def long(page, products: int = 24) -> dict:
    """Many products: through every department and the search, `products` product pages."""
    pause(page, 800)
    seen, rounds = 0, 0
    depts = list(shop_server.DEPARTMENTS)
    origin = "/".join(page.url.split("/", 3)[:3])
    while seen < products:
        dept = depts[rounds % len(depts)]  # women, men, home, women, ...: 10 products each
        page.goto(f"{origin}/c/{dept}")
        pause(page, 500)
        _scroll(page)
        for k in range(min(3, products - seen)):
            page.click(f"#productGrid a.product-link >> nth={(rounds // 3 * 3 + k) % 10}")
            product_visit(page, seen, review=seen % 7 == 0)
            seen += 1
            page.go_back()
            pause(page, 500)
        rounds += 1
        page.fill("#searchBox", TYPED["search"] if seen % 2 else "wool")
        page.press("#searchBox", "Enter")
        pause(page, 600)
    page.click("#cartLink")
    pause(page, 600)
    return {"products": seen}
