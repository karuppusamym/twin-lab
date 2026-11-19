"""A small shop written for the tests of the network facts and the screen templates
(labctl.netfacts, labctl.templates). Synthetic data only; served from 127.0.0.1 and kept in
memory. A second origin (`localhost`, another port) plays a third-party analytics service.

What it has, on purpose:

- One shared layout on every page: a header with a search box, a mega-menu (departments, then
  categories), a cart badge, and a footer with links nobody visits.
- `/`: a category tree, a "Recommended for you" block (`GET /api/recommendations`).
- `/c/<slug>`: a department or category with breadcrumbs and an infinite-scroll list
  (`GET /api/products?category=..&cursor=..&limit=8`, an opaque cursor in `next_cursor` and in a
  `Link: rel="next"` header, `X-Total-Count`, gzip). A "Load more" button does the same.
- `/s?k=..`: a search with facets (colour, size, brand), a sort and pages, rendered by the server;
  typing in the header asks `GET /api/suggest?q=..`.
- `/p/<slug>`: 30 products from one template (slugs without digits), each with a gallery, a
  colour and size picker that asks `GET /api/products/<sku>/variant` (price and picture change),
  "Customers also viewed" (`GET /api/recommendations`), reviews from a GraphQL endpoint
  (`POST /graphql`, operation `ProductReviews`, a cursor for more), a review form sent as
  multipart with a photo (`POST /api/reviews`: 422 without a rating), a price ticker as
  server-sent events (`GET /api/price-stream`), add to cart (`POST /api/cart/items`: the first
  call of a session answers 503 with `Retry-After` and the page retries) and add to the wish list
  (`POST /api/wishlist`, form-urlencoded with the CSRF token as a field).
- `/cart`: lines changed by `PATCH /api/cart/items/<line>` and removed by `DELETE`;
  `GET /api/cart` answers with an ETag and 304 to `If-None-Match`.
- `/go/<slug>`: a redirect chain (301 to `/category/<slug>`, 302 to `/c/<slug>`).
- A CSRF token in `<meta name="csrf-token">`, sent as `X-CSRF-Token`; a 403 without it.
- Static files with Cache-Control, ETag and Last-Modified: a style sheet, a script, a logo, a
  font and one picture per product and colour.

Run by hand with `python shop_server.py [port]`. Tests use `serve()`.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import secrets
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

PAGE = 8  # rows per answer of the product list
SEARCH_PAGE = 6
COOKIE = "shop_sid"
COLOURS = ("red", "blue", "green", "black", "white")
SIZES = ("S", "M", "L", "XL")
BRANDS = ("Northwind", "Harbor", "Fjell")
DEPARTMENTS = {
    "women": ("Women", ["dresses", "shoes", "coats", "bags"]),
    "men": ("Men", ["shirts", "boots", "jackets", "watches"]),
    "home": ("Home", ["lamps", "rugs", "mugs", "throws"]),
}
ITEM = {
    "dresses": "Dress", "shoes": "Flats", "coats": "Coat", "bags": "Tote",
    "shirts": "Shirt", "boots": "Boots", "jackets": "Jacket", "watches": "Watch",
    "lamps": "Lamp", "rugs": "Rug", "mugs": "Mug", "throws": "Throw",
}  # fmt: skip
ADJECTIVES = [
    "amber", "birch", "cedar", "dune", "ember", "fern", "garnet", "heather", "indigo", "juniper",
    "kelp", "lagoon", "maple", "nectar", "onyx", "pebble", "quartz", "russet", "saffron",
    "thistle", "umber", "velvet", "willow", "xenon", "yarrow", "zephyr", "alder", "bramble",
    "clover", "drift",
]  # fmt: skip
MATERIALS = ["linen", "wool", "cotton", "suede", "canvas", "oak", "clay", "silk"]
SLOW = {"/api/recommendations": 0.12, "/graphql": 0.06, "/api/products": 0.03}


def _catalogue() -> list[dict]:
    cats = [(d, c) for d, (_, cs) in DEPARTMENTS.items() for c in cs]
    out = []
    for i, adj in enumerate(ADJECTIVES):
        dept, cat = cats[i % len(cats)]
        material = MATERIALS[i % len(MATERIALS)]
        title = f"{adj.title()} {material.title()} {ITEM[cat]}"
        out.append({
            "sku": f"SKU-{4001 + i}", "slug": f"{adj}-{material}-{ITEM[cat].lower()}",
            "title": title, "department": dept, "category": cat,
            "brand": BRANDS[i % 3], "price": 19 + (i * 7) % 60,
            "colours": [COLOURS[(i + k) % 5] for k in range(3)],
            "rating": 3 + i % 3,
        })  # fmt: skip
    return out


PRODUCTS = _catalogue()
BY_SLUG = {p["slug"]: p for p in PRODUCTS}
BY_SKU = {p["sku"]: p for p in PRODUCTS}
REVIEWS = {
    p["sku"]: [
        {"id": f"REV-{7001 + n + 10 * i}", "rating": 2 + (n + i) % 4, "title": f"Review {n + 1}",
         "body": f"Bought it for the {MATERIALS[(n + i) % 8]} feel.",
         "author": ["Ilse Varga", "Teo Brask", "Runa Hale", "Pim Osk", "Lior Veen"][n % 5]}
        for n in range(5)
    ]
    for i, p in enumerate(PRODUCTS)
}  # fmt: skip

CSS = b"""body{font-family:Georgia,serif;margin:0;color:#222;background:#fff}
header#siteHeader{display:flex;gap:16px;align-items:center;padding:8px 16px;background:#232f3e;color:#fff}
header a{color:#fff}#megaMenu ul{list-style:none;margin:0;padding:0}
#megaMenu>ul>li{display:inline-block;position:relative;margin-right:12px}
#megaMenu .sub{display:none;position:absolute;background:#fff;padding:8px}
#megaMenu li:hover .sub{display:block}#megaMenu .sub a{color:#232f3e}
main{padding:16px;max-width:980px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.card{border:1px solid #ddd;padding:8px}.breadcrumb{list-style:none;display:flex;gap:6px;padding:0}
.breadcrumb li+li:before{content:'/';margin-right:6px}.facet{border:0;margin:0 0 12px}
#gallery img.main{width:240px;height:240px}.thumb img{width:48px;height:48px}
.swatch[aria-pressed=true]{outline:2px solid #e47911}footer#siteFooter{padding:16px;background:#eee}
@font-face{font-family:ShopSans;src:url(/static/fonts/shop.woff2) format('woff2')}
h1,h2{font-family:ShopSans,Georgia,serif}
"""
JS = b"""(function(){
const meta = document.querySelector('meta[name=csrf-token]');
window.shopToken = meta ? meta.content : '';
window.api = async function(method, url, body, extra) {
  const h = {'Accept': 'application/json', 'X-Shop-Client': 'web/1.4'};
  if (method !== 'GET') h['X-CSRF-Token'] = window.shopToken;
  if (body !== undefined && !(body instanceof FormData)) h['Content-Type'] = 'application/json';
  return fetch(url, Object.assign({method, headers: h, credentials: 'same-origin',
    body: body === undefined ? undefined : (body instanceof FormData ? body : JSON.stringify(body))},
    extra || {}));
};
window.refreshCart = async function() {
  const r = await api('GET', '/api/cart', undefined, {cache: 'no-cache'});
  if (r.status === 200) { const d = await r.json(); document.getElementById('cartCount').textContent = d.count; }
};
refreshCart();
const box = document.getElementById('searchBox'), list = document.getElementById('suggestions');
let timer = null;
box.addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    if (box.value.length < 2) return;
    const r = await api('GET', '/api/suggest?q=' + encodeURIComponent(box.value));
    const d = await r.json();
    list.innerHTML = d.suggestions.map(s => '<li class="suggestion">' + s + '</li>').join('');
  }, 150);
});
window.addEventListener('load', () => {
  if (window.shopTag) shopTag.track('pageview');
});
})();
"""
FONT = b"wOF2" + bytes(range(60))
LOGO = (b'<svg xmlns="http://www.w3.org/2000/svg" width="90" height="24"><rect width="90" '
        b'height="24" fill="#e47911"/></svg>')  # fmt: skip
STARTED = formatdate(time.time() - 86400, usegmt=True)


def picture(sku: str, colour: str) -> bytes:
    shade = {"red": "#c33", "blue": "#36c", "green": "#3a3", "black": "#222", "white": "#eee"}
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><rect width="240" '
            f'height="240" fill="{shade.get(colour, "#999")}"/></svg>').encode()  # fmt: skip


def tag_js(collect: str) -> bytes:
    return (
        "window.shopTag = {track: function(e) { var i = new Image(); "
        f"i.src = '{collect}/pixel.gif?e=' + e + '&t=' + Date.now(); }}}};\n"
        f"fetch('{collect}/collect', {{method: 'POST', mode: 'cors', body: JSON.stringify("
        "{e: 'pageview', p: location.pathname})});\n"
    ).encode()


class Shop:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.lock = threading.Lock()

    def session(self, sid: str | None) -> tuple[str, dict, bool]:
        with self.lock:
            if sid and sid in self.sessions:
                return sid, self.sessions[sid], False
            sid = secrets.token_hex(12)
            self.sessions[sid] = {"csrf": secrets.token_urlsafe(24), "cart": [], "wish": [],
                                  "line": 0, "tried": False}  # fmt: skip
            return sid, self.sessions[sid], True


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def _offset(cursor: str) -> int:
    try:
        pad = "=" * (-len(cursor) % 4)
        return int(json.loads(base64.urlsafe_b64decode(cursor + pad))["o"])
    except (ValueError, KeyError, TypeError):
        return 0


def _card(p: dict) -> dict:
    return {"sku": p["sku"], "title": p["title"], "price": float(p["price"]), "currency": "EUR",
            "url": f"/p/{p['slug']}", "image": f"/img/p/{p['sku']}/{p['colours'][0]}.svg",
            "rating": p["rating"]}  # fmt: skip


def _menu() -> str:
    tops = []
    for d, (name, cats) in DEPARTMENTS.items():
        subs = "".join(
            f'<li><a class="menu-link" href="/c/{d}-{c}">{c.title()}</a></li>' for c in cats
        )
        tops.append(f'<li class="top"><a class="menu-top" href="/c/{d}">{name}</a>'
                    f'<ul class="sub">{subs}<li><a class="menu-all" href="/go/{d}">All {name}</a>'
                    "</li></ul></li>")  # fmt: skip
    return '<nav id="megaMenu" aria-label="Departments"><ul>' + "".join(tops) + "</ul></nav>"


def layout(title: str, body: str, csrf: str, collect: str, crumbs: list | None = None) -> str:
    trail = ""
    if crumbs:
        items = "".join(
            f'<li><a href="{href}">{text}</a></li>'
            if href
            else f'<li aria-current="page">{text}</li>'
            for text, href in crumbs
        )
        trail = f'<nav aria-label="Breadcrumb"><ol class="breadcrumb">{items}</ol></nav>'
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="csrf-token" content="{csrf}"><title>{title} | Fixture Shop</title>
<link rel="stylesheet" href="/static/shop.css?v=3"><script src="{collect}/tag.js" async></script>
</head><body><header id="siteHeader"><a id="logo" href="/"><img src="/static/logo.svg" alt="Fixture Shop"></a>
{_menu()}<form id="searchForm" action="/s" method="get" role="search"><label for="searchBox">Search</label>
<input id="searchBox" name="k" autocomplete="off"><button id="searchGo" type="submit">Go</button>
<ul id="suggestions"></ul></form>
<a id="wishLink" href="/wishlist">Wish list</a><a id="cartLink" href="/cart">Cart (<span id="cartCount">0</span>)</a>
</header><script src="/static/shop.js"></script><main>{trail}{body}</main>
<footer id="siteFooter"><a href="/help">Help</a> <a href="/returns">Returns</a> <a href="/about">About us</a></footer>
</body></html>"""  # noqa: E501


def home_body() -> str:
    tree = "".join(
        f'<li><a class="tree-dept" href="/c/{d}">{name}</a><ul>'
        + "".join(f'<li><a class="tree-cat" href="/c/{d}-{c}">{c.title()}</a></li>' for c in cats)
        + "</ul></li>"
        for d, (name, cats) in DEPARTMENTS.items()
    )
    tree += '<li><a class="tree-all" href="/c/all">All products</a></li>'
    return f"""<h1>Welcome to the shop</h1>
<aside><h2>Shop by category</h2><nav id="categoryTree"><ul>{tree}</ul></nav></aside>
<section id="recsHome" class="recommendations" aria-label="Recommended for you">
<h2>Recommended for you</h2><div class="grid" id="recsGrid"></div></section>
<script>
api('GET', '/api/recommendations?context=home').then(r => r.json()).then(d => {{
  document.getElementById('recsGrid').innerHTML = d.items.map(p =>
    '<div class="card"><a class="rec-link" href="' + p.url + '">' + p.title + '</a> <span class="price">'
    + p.price + '</span></div>').join('');
}});
</script>"""  # noqa: E501


def category_body(slug: str, title: str, rows: list[dict]) -> str:
    cards = "".join(_card_html(p) for p in rows[:PAGE])
    nxt = _cursor(PAGE) if len(rows) > PAGE else ""
    return f"""<h1>{title}</h1><p id="resultCount">{len(rows)} products</p>
<div class="grid" id="productGrid">{cards}</div>
<div id="scrollSentinel" data-cursor="{nxt}"></div>
<button id="loadMore" type="button" {"" if nxt else "hidden"}>Load more</button>
<script>
const grid = document.getElementById('productGrid'), sentinel = document.getElementById('scrollSentinel');
let cursor = sentinel.dataset.cursor, busy = false;
async function more() {{
  if (!cursor || busy) return; busy = true;
  const r = await api('GET', '/api/products?category={slug}&cursor=' + cursor + '&limit={PAGE}');
  const d = await r.json();
  grid.insertAdjacentHTML('beforeend', d.items.map(p => '<div class="card"><a class="product-link" href="'
    + p.url + '">' + p.title + '</a> <span class="price">' + p.price + '</span></div>').join(''));
  cursor = d.next_cursor; busy = false;
  if (!cursor) document.getElementById('loadMore').hidden = true;
}}
new IntersectionObserver(es => {{ if (es[0].isIntersecting) more(); }}).observe(sentinel);
document.getElementById('loadMore').addEventListener('click', more);
</script>"""  # noqa: E501


def _card_html(p: dict) -> str:
    return (f'<div class="card"><a class="product-link" href="/p/{p["slug"]}">{p["title"]}</a> '
            f'<span class="price">{p["price"]:.2f}</span></div>')  # fmt: skip


def search_body(query: dict, rows: list[dict], page_no: int, pages: int) -> str:
    k = query.get("k", "")
    facets = []
    for name, label, values in (("color", "Colour", COLOURS), ("size", "Size", SIZES),
                                ("brand", "Brand", BRANDS)):  # fmt: skip
        chosen = query.get(name, "")
        opts = "".join(
            f'<label><input type="checkbox" class="facet-option" name="{name}" value="{v}"'
            f"{' checked' if v == chosen else ''}> {v}</label>"
            for v in values
        )
        facets.append(
            f'<fieldset class="facet" data-facet="{name}"><legend>{label}</legend>{opts}</fieldset>'
        )
    sort = query.get("sort", "relevance")
    sorts = "".join(f'<option value="{v}"{" selected" if v == sort else ""}>{t}</option>'
                    for v, t in (("relevance", "Relevance"), ("price-asc", "Price: low to high"),
                                 ("price-desc", "Price: high to low")))  # fmt: skip
    cards = "".join(_card_html(p) for p in rows)
    base = {x: query[x] for x in ("k", "color", "size", "brand", "sort") if query.get(x)}
    links = "".join(
        f'<a class="page-link" href="/s?{"&".join(f"{a}={quote(b)}" for a, b in {**base, "page": str(n)}.items())}">{n}</a>'
        for n in range(1, pages + 1)
        if n != page_no
    )
    return f"""<h1>Results</h1><form id="refine" action="/s" method="get">
<input type="hidden" name="k" value="{k}"><aside id="facets">{"".join(facets)}</aside>
<label for="sortBy">Sort by</label><select id="sortBy" name="sort">{sorts}</select></form>
<p id="resultCount">{len(rows)} shown</p><div class="grid" id="results">{cards}</div>
<nav id="pager" aria-label="Pages">{links}</nav>
<script>
const refine = document.getElementById('refine');
refine.addEventListener('change', () => refine.submit());
</script>"""  # noqa: E501


def product_body(p: dict) -> str:
    thumbs = "".join(
        f'<button class="thumb" type="button" data-src="/img/p/{p["sku"]}/{c}.svg">'
        f'<img src="/img/p/{p["sku"]}/{c}.svg" alt="View {n + 1}"></button>'
        for n, c in enumerate(p["colours"])
    )
    swatches = "".join(
        f'<button class="swatch" type="button" data-colour="{c}" aria-pressed="{str(n == 0).lower()}">{c}</button>'
        for n, c in enumerate(p["colours"])
    )
    sizes = "".join(f'<option value="{s}">{s}</option>' for s in SIZES)
    return f"""<div id="product" data-sku="{p["sku"]}"><div id="gallery">
<img class="main" id="mainImage" src="/img/p/{p["sku"]}/{p["colours"][0]}.svg" alt="{p["title"]}">
<div class="thumbs">{thumbs}</div></div>
<h1 id="productTitle">{p["title"]}</h1><p id="brand">{p["brand"]}</p>
<p><span id="price">{p["price"]:.2f}</span> <span id="currency">EUR</span> <span id="ticker"></span></p>
<div id="variants"><fieldset id="colourPicker"><legend>Colour</legend>{swatches}</fieldset>
<label for="sizeSelect">Size</label><select id="sizeSelect" name="size">{sizes}</select></div>
<button id="addToCart" type="button">Add to cart</button>
<button id="addToWishlist" type="button">Add to wish list</button><p id="cartMsg" role="status"></p></div>
<section id="alsoViewed" class="recommendations" aria-label="Customers also viewed">
<h2>Customers also viewed</h2><div class="grid" id="alsoGrid"></div></section>
<section id="reviews"><h2>Reviews</h2><ul id="reviewList"></ul>
<button id="moreReviews" type="button">More reviews</button>
<form id="reviewForm" enctype="multipart/form-data"><h3>Write a review</h3>
<label for="rating">Rating</label><select id="rating" name="rating"><option value="">Choose</option>
<option value="5">5</option><option value="4">4</option><option value="3">3</option></select>
<label for="reviewTitle">Title</label><input id="reviewTitle" name="title" maxlength="60">
<label for="reviewBody">Review</label><textarea id="reviewBody" name="body"></textarea>
<label for="photo">Photo</label><input id="photo" name="photo" type="file" accept="image/png,image/jpeg">
<button id="sendReview" type="submit">Send review</button><p id="reviewMsg" role="alert"></p></form></section>
<script>
const sku = document.getElementById('product').dataset.sku;
let colour = '{p["colours"][0]}', after = null;
document.querySelectorAll('.thumb').forEach(b => b.addEventListener('click', () => {{
  document.getElementById('mainImage').src = b.dataset.src; }}));
async function variant() {{
  const size = document.getElementById('sizeSelect').value;
  const r = await api('GET', '/api/products/' + sku + '/variant?colour=' + colour + '&size=' + size);
  const d = await r.json();
  document.getElementById('price').textContent = d.price;
  document.getElementById('mainImage').src = d.image;
}}
document.querySelectorAll('.swatch').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.swatch').forEach(x => x.setAttribute('aria-pressed', 'false'));
  b.setAttribute('aria-pressed', 'true'); colour = b.dataset.colour; variant(); }}));
document.getElementById('sizeSelect').addEventListener('change', variant);
async function addToCart(again) {{
  const r = await api('POST', '/api/cart/items', {{sku, colour, size: document.getElementById('sizeSelect').value, qty: 1}});
  if (r.status === 503 && !again) {{ setTimeout(() => addToCart(true), 400); return; }}
  const d = await r.json();
  document.getElementById('cartCount').textContent = d.count;
  document.getElementById('cartMsg').textContent = 'Added to your cart.';
  if (window.shopTag) shopTag.track('add_to_cart');
}}
document.getElementById('addToCart').addEventListener('click', () => addToCart(false));
document.getElementById('addToWishlist').addEventListener('click', async () => {{
  const r = await fetch('/api/wishlist', {{method: 'POST', headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: 'sku=' + sku + '&_csrf=' + encodeURIComponent(window.shopToken)}});
  const d = await r.json(); document.getElementById('cartMsg').textContent = 'Saved to your wish list.';
}});
const QUERY = 'query ProductReviews($sku: ID!, $first: Int!, $after: String) {{ product(sku: $sku) {{ reviews(first: $first, after: $after) {{ edges {{ node {{ id rating title body author }} }} pageInfo {{ endCursor hasNextPage }} }} }} }}';
async function reviews() {{
  const r = await api('POST', '/graphql', {{operationName: 'ProductReviews', query: QUERY,
    variables: {{sku, first: 2, after}}}});
  const d = (await r.json()).data.product.reviews;
  document.getElementById('reviewList').insertAdjacentHTML('beforeend', d.edges.map(e =>
    '<li class="review"><b>' + e.node.rating + '</b> ' + e.node.title + ': ' + e.node.body + ' (' + e.node.author + ')</li>').join(''));
  after = d.pageInfo.endCursor;
  document.getElementById('moreReviews').hidden = !d.pageInfo.hasNextPage;
}}
reviews();
document.getElementById('moreReviews').addEventListener('click', reviews);
document.getElementById('reviewForm').addEventListener('submit', async ev => {{
  ev.preventDefault();
  const f = new FormData(ev.target); f.append('sku', sku);
  const r = await api('POST', '/api/reviews', f);
  const d = await r.json();
  document.getElementById('reviewMsg').textContent = r.ok ? 'Thank you: your review waits for approval.' : d.error;
}});
api('GET', '/api/recommendations?context=product&sku=' + sku).then(r => r.json()).then(d => {{
  document.getElementById('alsoGrid').innerHTML = d.items.map(x => '<div class="card"><a class="rec-link" href="'
    + x.url + '">' + x.title + '</a></div>').join('');
}});
const es = new EventSource('/api/price-stream?sku=' + sku);
es.addEventListener('price', e => {{ document.getElementById('ticker').textContent = JSON.parse(e.data).price; }});
es.addEventListener('end', () => es.close());
</script>"""  # noqa: E501


def cart_body(cart: list[dict]) -> str:
    rows = "".join(
        f'<tr data-line="{li["line"]}"><td class="title">{li["title"]}</td><td>{li["colour"]} {li["size"]}</td>'
        f'<td><select class="qty" data-line="{li["line"]}">'
        + "".join(
            f'<option value="{n}"{" selected" if n == li["qty"] else ""}>{n}</option>'
            for n in range(1, 6)
        )
        + "</select></td>"
        f'<td class="price">{li["price"]:.2f}</td><td><button class="remove" type="button" data-line="{li["line"]}">'
        "Remove</button></td></tr>"
        for li in cart
    )
    return f"""<h1>Your cart</h1><table id="cartTable"><thead><tr><th>Product</th><th>Variant</th>
<th>Quantity</th><th>Price</th><th></th></tr></thead><tbody>{rows}</tbody></table>
<p id="cartEmpty" {"hidden" if cart else ""}>Your cart is empty.</p>
<button id="checkout" type="button">Proceed to checkout</button>
<script>
document.querySelectorAll('.qty').forEach(i => i.addEventListener('change', async () => {{
  const r = await api('PATCH', '/api/cart/items/' + i.dataset.line, {{qty: Number(i.value)}});
  if (r.ok) refreshCart();
}}));
document.querySelectorAll('.remove').forEach(b => b.addEventListener('click', async () => {{
  const r = await api('DELETE', '/api/cart/items/' + b.dataset.line);
  if (r.status === 204) {{ b.closest('tr').remove(); refreshCart(); }}
}}));
</script>"""  # noqa: E501


def _rows(dept: str, cat: str | None) -> list[dict]:
    if dept == "all":
        return list(PRODUCTS)
    return [
        p for p in PRODUCTS if p["department"] == dept and (cat is None or p["category"] == cat)
    ]


def _category(slug: str) -> tuple[str, str | None, str] | None:
    if slug == "all":
        return "all", None, "All products"
    dept, _, cat = slug.partition("-")
    if dept not in DEPARTMENTS or (cat and cat not in DEPARTMENTS[dept][1]):
        return None
    return dept, cat or None, (cat.title() if cat else DEPARTMENTS[dept][0])


class Handler(BaseHTTPRequestHandler):
    server_version = "FixtureShop/1.0"

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    # --- answering -------------------------------------------------------------------------
    def send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if body or status not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def json(
        self, data, status: int = 200, headers: dict | None = None, zipped: bool = False
    ) -> None:
        body = json.dumps(data).encode()
        headers = {"Cache-Control": "no-store", **(headers or {})}
        if zipped and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        self.send(status, body, "application/json", headers)

    def html(self, text: str, status: int = 200) -> None:
        headers = {"Cache-Control": "private, no-cache"}
        if self.new_sid:
            headers["Set-Cookie"] = f"{COOKIE}={self.sid}; Path=/; HttpOnly; SameSite=Lax"
        self.send(status, text.encode(), "text/html; charset=utf-8", headers)

    def static(self, body: bytes, ctype: str, cache: str) -> None:
        tag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == tag:
            self.send(304, b"", ctype, {"ETag": tag, "Cache-Control": cache})
            return
        self.send(200, body, ctype, {"ETag": tag, "Cache-Control": cache, "Last-Modified": STARTED})

    def body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def data(self) -> dict:
        try:
            value = json.loads(self.body() or b"{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    # --- routing ---------------------------------------------------------------------------
    def _route(self, method: str) -> None:
        shop: Shop = self.server.shop  # type: ignore[attr-defined]
        url = urlsplit(self.path)
        path = url.path
        query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        sid = None
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                sid = value
        self.sid, self.state, self.new_sid = shop.session(sid)
        time.sleep(next((v for k, v in SLOW.items() if path.startswith(k)), 0.0))
        if path.startswith("/static/") or path.startswith("/img/"):
            self._files(path)
        elif path.startswith("/api/") or path == "/graphql":
            if method != "GET" and path != "/api/wishlist":
                if self.headers.get("X-CSRF-Token") != self.state["csrf"]:
                    self.json({"error": "Invalid CSRF token."}, 403)
                    return
            self._api(method, path, query)
        elif method == "GET":
            self._page(path, query)
        else:
            self.json({"error": "Method not allowed."}, 405)

    def _files(self, path: str) -> None:
        year = "public, max-age=31536000, immutable"
        if path == "/static/shop.css":
            self.static(CSS, "text/css", "public, max-age=86400")
        elif path == "/static/shop.js":
            self.static(JS, "application/javascript", year)
        elif path == "/static/logo.svg":
            self.static(LOGO, "image/svg+xml", year)
        elif path == "/static/fonts/shop.woff2":
            self.static(FONT, "font/woff2", year)
        elif m := re.fullmatch(r"/img/p/(SKU-\d+)/(\w+)\.svg", path):
            self.static(picture(m.group(1), m.group(2)), "image/svg+xml", "public, max-age=604800")
        else:
            self.send(404, b"", "text/plain")

    def _page(self, path: str, query: dict) -> None:
        collect = self.server.collect  # type: ignore[attr-defined]
        csrf = self.state["csrf"]
        home = ("Home", "/")
        if path == "/":
            self.html(layout("Home", home_body(), csrf, collect))
        elif m := re.fullmatch(r"/go/([a-z]+)", path):
            self.send(301, b"", "text/html", {"Location": f"/category/{m.group(1)}"})
        elif m := re.fullmatch(r"/category/([a-z]+)", path):
            self.send(302, b"", "text/html", {"Location": f"/c/{m.group(1)}"})
        elif m := re.fullmatch(r"/c/([a-z-]+)", path):
            found = _category(m.group(1))
            if found is None:
                self.html(layout("Not found", "<h1>Not found</h1>", csrf, collect), 404)
                return
            dept, cat, title = found
            crumbs = (
                [home] + ([(DEPARTMENTS[dept][0], f"/c/{dept}")] if cat else []) + [(title, None)]
            )
            body = category_body(m.group(1), title, _rows(dept, cat))
            self.html(layout(title, body, csrf, collect, crumbs))
        elif path == "/s":
            rows = self._search(query)
            pages = max(1, -(-len(rows) // SEARCH_PAGE))
            page_no = min(max(1, int(query.get("page") or 1)), pages)
            chunk = rows[(page_no - 1) * SEARCH_PAGE : page_no * SEARCH_PAGE]
            body = search_body(query, chunk, page_no, pages)
            self.html(layout("Search", body, csrf, collect, [home, ("Search", None)]))
        elif m := re.fullmatch(r"/p/([a-z-]+)", path):
            p = BY_SLUG.get(m.group(1))
            if p is None:
                self.html(layout("Not found", "<h1>Not found</h1>", csrf, collect), 404)
                return
            dept = DEPARTMENTS[p["department"]][0]
            crumbs = [home, (dept, f"/c/{p['department']}"),
                      (p["category"].title(), f"/c/{p['department']}-{p['category']}"), (p["title"], None)]  # fmt: skip
            self.html(layout(p["title"], product_body(p), csrf, collect, crumbs))
        elif path == "/cart":
            body = cart_body(self.state["cart"])
            self.html(layout("Cart", body, csrf, collect, [home, ("Cart", None)]))
        else:
            self.html(layout("Not found", "<h1>Not found</h1>", csrf, collect), 404)

    def _search(self, query: dict) -> list[dict]:
        k = (query.get("k") or "").strip().lower()
        rows = [p for p in PRODUCTS if not k or k in p["title"].lower() or k in p["category"]]
        if query.get("color"):
            rows = [p for p in rows if query["color"] in p["colours"]]
        if query.get("brand"):
            rows = [p for p in rows if p["brand"] == query["brand"]]
        sort = query.get("sort") or "relevance"
        if sort.startswith("price"):
            rows.sort(key=lambda p: p["price"], reverse=sort == "price-desc")
        return rows

    def _cart_doc(self) -> dict:
        cart = self.state["cart"]
        total = sum(li["price"] * li["qty"] for li in cart)
        return {"count": sum(li["qty"] for li in cart), "items": cart, "total": round(total, 2),
                "currency": "EUR"}  # fmt: skip

    def _api(self, method: str, path: str, query: dict) -> None:
        if path == "/api/products" and method == "GET":
            found = _category(query.get("category") or "all") or ("all", None, "")
            rows = _rows(found[0], found[1])
            start = _offset(query.get("cursor") or "")
            limit = min(int(query.get("limit") or PAGE), 24)
            chunk = rows[start : start + limit]
            nxt = _cursor(start + limit) if start + limit < len(rows) else None
            headers = {"X-Total-Count": str(len(rows))}
            if nxt:
                headers["Link"] = (
                    f'</api/products?category={query.get("category")}&cursor={nxt}&limit={limit}>; rel="next"'
                )
            self.json(
                {"items": [_card(p) for p in chunk], "next_cursor": nxt}, 200, headers, zipped=True
            )
        elif m := re.fullmatch(r"/api/products/(SKU-\d+)/variant", path):
            p = BY_SKU.get(m.group(1))
            colour, size = query.get("colour") or "", query.get("size") or ""
            if p is None or colour not in p["colours"] or size not in SIZES:
                self.json({"error": "No such variant."}, 404)
                return
            price = p["price"] + SIZES.index(size) * 2
            self.json({"sku": p["sku"], "variant": f"{p['sku']}-{colour.upper()}-{size}", "colour": colour,
                       "size": size, "price": float(price), "currency": "EUR",
                       "image": f"/img/p/{p['sku']}/{colour}.svg", "in_stock": size != "XL"})  # fmt: skip
        elif path == "/api/recommendations":
            sku = query.get("sku") or ""
            start = (PRODUCTS.index(BY_SKU[sku]) + 1) if sku in BY_SKU else 3
            items = [_card(PRODUCTS[(start + n * 4) % len(PRODUCTS)]) for n in range(4)]
            self.json(
                {"items": items, "personalised": True, "context": query.get("context") or "home"}
            )
        elif path == "/api/suggest":
            q = (query.get("q") or "").lower()
            self.json(
                {"suggestions": [p["title"] for p in PRODUCTS if q in p["title"].lower()][:5]}
            )
        elif path == "/api/cart" and method == "GET":
            doc = self._cart_doc()
            tag = '"' + hashlib.sha1(json.dumps(doc).encode()).hexdigest()[:12] + '"'
            headers = {"ETag": tag, "Cache-Control": "private, no-cache"}
            if self.headers.get("If-None-Match") == tag:
                self.send(304, b"", "application/json", headers)
                return
            self.send(200, json.dumps(doc).encode(), "application/json", headers)
        elif path == "/api/cart/items" and method == "POST":
            data = self.data()
            if not self.state["tried"]:  # the first add of a session: busy, try again
                self.state["tried"] = True
                self.json({"error": "Busy, try again."}, 503, {"Retry-After": "1"})
                return
            p = BY_SKU.get(str(data.get("sku")))
            if p is None:
                self.json({"error": "No such product."}, 404)
                return
            size = str(data.get("size") or "M")
            self.state["line"] += 1
            self.state["cart"].append({
                "line": self.state["line"], "sku": p["sku"], "title": p["title"],
                "colour": str(data.get("colour") or p["colours"][0]), "size": size,
                "qty": int(data.get("qty") or 1), "price": float(p["price"] + SIZES.index(size) * 2),
            })  # fmt: skip
            self.json(self._cart_doc(), 201)
        elif m := re.fullmatch(r"/api/cart/items/(\d+)", path):
            line = next((li for li in self.state["cart"] if li["line"] == int(m.group(1))), None)
            if line is None:
                self.json({"error": "No such line."}, 404)
            elif method == "PATCH":
                qty = int(self.data().get("qty") or 0)
                if qty < 1:
                    self.json(
                        {"error": "Check the marked fields.", "fields": {"qty": "At least 1."}}, 422
                    )
                    return
                line["qty"] = qty
                self.json(self._cart_doc())
            elif method == "DELETE":
                self.state["cart"].remove(line)
                self.send(204, b"", "application/json")
            else:
                self.json({"error": "Method not allowed."}, 405)
        elif path == "/api/wishlist" and method == "POST":
            form = {
                k: v[-1] for k, v in parse_qs(self.body().decode(), keep_blank_values=True).items()
            }
            if form.get("_csrf") != self.state["csrf"]:
                self.json({"error": "Invalid CSRF token."}, 403)
                return
            if form.get("sku") in BY_SKU and form["sku"] not in self.state["wish"]:
                self.state["wish"].append(form["sku"])
            self.json({"count": len(self.state["wish"])})
        elif path == "/graphql" and method == "POST":
            self._graphql(self.data())
        elif path == "/api/reviews" and method == "POST":
            self._review()
        elif path == "/api/price-stream":
            self._stream(query.get("sku") or "")
        else:
            self.json({"error": "Not found."}, 404)

    def _graphql(self, data: dict) -> None:
        v = data.get("variables") or {}
        if data.get("operationName") != "ProductReviews" or str(v.get("sku")) not in REVIEWS:
            self.json({"errors": [{"message": "Unknown operation."}], "data": None}, 200)
            return
        rows = REVIEWS[str(v["sku"])]
        start = _offset(v.get("after") or "") if v.get("after") else 0
        chunk = rows[start : start + int(v.get("first") or 2)]
        end = start + len(chunk)
        self.json({"data": {"product": {"reviews": {
            "edges": [{"node": r} for r in chunk],
            "pageInfo": {"endCursor": _cursor(end), "hasNextPage": end < len(rows)}}}}})  # fmt: skip

    def _review(self) -> None:
        raw = self.body()
        boundary = (self.headers.get("Content-Type") or "").partition("boundary=")[2].encode()
        fields, files = {}, {}
        for part in raw.split(b"--" + boundary) if boundary else []:
            head, _, value = part.partition(b"\r\n\r\n")
            name = re.search(rb'name="([^"]+)"', head)
            if not name:
                continue
            fname = re.search(rb'filename="([^"]*)"', head)
            if fname:
                ctype = re.search(rb"Content-Type: ([^\r\n]+)", head)
                files[name.group(1).decode()] = (
                    ctype.group(1).decode() if ctype else "",
                    len(value) - 2,
                )
            else:
                fields[name.group(1).decode()] = value[:-2].decode("utf-8", "replace")
        errors = {}
        if not fields.get("rating"):
            errors["rating"] = "Choose a rating."
        photo = files.get("photo")
        if photo and photo[1] > 0 and photo[0] not in ("image/png", "image/jpeg"):
            errors["photo"] = "A photo is a PNG or JPEG file."
        if errors:
            self.json({"error": "Check the marked fields.", "fields": errors}, 422)
            return
        self.json({"id": f"REV-{8000 + secrets.randbelow(999)}", "status": "PENDING"}, 201)

    def _stream(self, sku: str) -> None:
        p = BY_SKU.get(sku)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # A finite stream sent whole, with its length: the browser has the complete answer before
        # the page closes the stream on `end`, so every browser version keeps its body in the HAR
        # (a stream still open when the page closes it is aborted and keeps none).
        parts = ["retry: 3600000\n\n"]
        for n in range(3):
            price = (p["price"] if p else 10) + n * 0.5
            parts.append(
                f"id: {n + 1}\nevent: price\ndata: {json.dumps({'sku': sku, 'price': price})}\n\n"
            )
        parts.append("event: end\ndata: {}\n\n")
        body = "".join(parts).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PATCH(self):  # noqa: N802
        self._route("PATCH")

    def do_DELETE(self):  # noqa: N802
        self._route("DELETE")


class Analytics(BaseHTTPRequestHandler):
    """The third party: a tag script, a collect call (CORS) and a pixel."""

    server_version = "FixtureTag/2.0"

    def log_message(self, *args) -> None:
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", self.server.shop_origin)  # type: ignore[attr-defined]
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/tag.js":
            self._send(200, tag_js(self.server.own), "application/javascript")  # type: ignore[attr-defined]
        else:
            gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
            self._send(200, gif, "image/gif")

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self._send(204, b"", "text/plain")


class Running:
    def __init__(self, server: ThreadingHTTPServer, tag: ThreadingHTTPServer) -> None:
        self.server, self.tag = server, tag
        self.url = f"http://127.0.0.1:{server.server_address[1]}"
        self.tag_url = f"http://localhost:{tag.server_address[1]}"

    def stop(self) -> None:
        for s in (self.server, self.tag):
            s.shutdown()
            s.server_close()


def serve(port: int = 0) -> Running:
    """Start the shop on 127.0.0.1 and its analytics service on localhost (free ports)."""
    tag = ThreadingHTTPServer(("127.0.0.1", 0), Analytics)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.shop = Shop()  # type: ignore[attr-defined]
    server.collect = f"http://localhost:{tag.server_address[1]}"  # type: ignore[attr-defined]
    tag.shop_origin = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    tag.own = server.collect  # type: ignore[attr-defined]
    for s in (server, tag):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return Running(server, tag)


if __name__ == "__main__":
    import sys

    run = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8742)
    print(f"shop  {run.url}/")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        run.stop()
