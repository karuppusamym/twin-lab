"""QTT demo copy: server-rendered screens and the JSON API, in one FastAPI process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth, db, reviews

logging.basicConfig(level=logging.INFO)

HERE = Path(__file__).resolve().parent
VERSION = "1.0.0"
LOAN_LIMIT = 50  # R6

templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["version"] = VERSION
templates.env.globals["product_name"] = reviews.product_name
templates.env.filters["money"] = lambda v: f"${float(v):,.2f}"
templates.env.filters["rate"] = lambda v: f"{float(v):.3f}%"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.start_initialiser()
    yield


app = FastAPI(
    title="QTT demo copy", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# --- helpers -------------------------------------------------------------------------------------


def current_user(conn, request: Request) -> dict | None:
    user_id = auth.read_cookie(request.cookies.get(auth.COOKIE))
    if user_id is None:
        return None
    return conn.execute(
        "SELECT user_id, username, full_name, role FROM users"
        " WHERE user_id = %s AND is_active = true",
        (user_id,),
    ).fetchone()


def find_user(conn, username: str, password: str) -> dict | None:
    user = conn.execute(
        "SELECT user_id, username, full_name, role, password_hash FROM users"
        " WHERE username = %s AND is_active = true",
        (username,),
    ).fetchone()
    if user and auth.verify_password(password, user["password_hash"]):
        return user
    return None


def to_login(request: Request) -> RedirectResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


def page(
    request: Request, name: str, user: dict | None, http_status: int = 200, **ctx
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"user": user, **ctx}, status_code=http_status)


def not_found(request: Request, user: dict, message: str) -> HTMLResponse:
    return page(request, "error.html", user, http_status=404, message=message)


def api_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)


def unauthorised() -> JSONResponse:
    return api_error(401, "NOT_SIGNED_IN", "Sign in first.")


def safe_next(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/queue"


async def body_of(request: Request) -> dict:
    if "application/json" in request.headers.get("content-type", ""):
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


# --- health --------------------------------------------------------------------------------------


@app.get("/api/health")
def health():
    if db.ping():
        return {"status": "ok"}
    return JSONResponse({"status": "database unreachable"}, status_code=503)


# --- sign in -------------------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/queue"):
    return page(request, "login.html", None, next=safe_next(next), username="", error=None)


@app.post("/login")
async def login_submit(request: Request):
    form = await body_of(request)
    username = form.get("username", "").strip()
    target = safe_next(form.get("next", "") or "/queue")
    with db.connect() as conn:
        user = find_user(conn, username, form.get("password", ""))
    if user is None:
        return page(
            request,
            "login.html",
            None,
            http_status=401,
            next=target,
            username=username,
            error="Invalid user ID or password.",
        )
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        auth.COOKIE, auth.make_cookie(user["user_id"]), httponly=True, samesite="lax"
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


@app.post("/api/login")
async def api_login(request: Request):
    body = await body_of(request)
    with db.connect() as conn:
        user = find_user(conn, str(body.get("username", "")).strip(), str(body.get("password", "")))
    if user is None:
        return api_error(401, "INVALID_CREDENTIALS", "Invalid user ID or password.")
    response = JSONResponse(
        {"username": user["username"], "full_name": user["full_name"], "role": user["role"]}
    )
    response.set_cookie(
        auth.COOKIE, auth.make_cookie(user["user_id"]), httponly=True, samesite="lax"
    )
    return response


# --- work queue ----------------------------------------------------------------------------------


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request, status: str = "", q: str = "", mine: str = ""):
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return to_login(request)
        rows = reviews.list_reviews(
            conn, status=status, q=q.strip(), mine_user_id=user["user_id"] if mine else None
        )
    return page(
        request,
        "queue.html",
        user,
        rows=rows,
        status=status,
        q=q,
        mine=bool(mine),
        statuses=reviews.STATUSES,
    )


# --- review --------------------------------------------------------------------------------------


@app.get("/reviews/{review_no}", response_class=HTMLResponse)
def review_page(request: Request, review_no: str):
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return to_login(request)
        review = reviews.detail(conn, review_no, user)
    if review is None:
        return not_found(request, user, f"Review {review_no} was not found.")
    categories: list[tuple[str, list]] = []
    for item in review["items"]:
        if not categories or categories[-1][0] != item["category"]:
            categories.append((item["category"], []))
        categories[-1][1].append(item)
    return page(request, "review.html", user, review=review, categories=categories)


@app.get("/api/reviews")
def api_reviews(request: Request, status: str = "", q: str = "", mine: str = ""):
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return unauthorised()
        return reviews.list_reviews(
            conn, status=status, q=q.strip(), mine_user_id=user["user_id"] if mine else None
        )


@app.get("/api/reviews/{review_no}")
def api_review(request: Request, review_no: str):
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return unauthorised()
        review = reviews.detail(conn, review_no, user)
    if review is None:
        return api_error(404, "NOT_FOUND", f"Review {review_no} was not found.")
    return review


async def _change(request: Request, review_no: str, action) -> Response:
    body = await body_of(request)
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return unauthorised()
        try:
            action(conn, body, user)
        except reviews.RuleError as err:
            conn.rollback()
            return api_error(err.status, err.code, err.message)
        conn.commit()
        return JSONResponse(reviews.detail(conn, review_no, user))


@app.put("/api/reviews/{review_no}/items/{item_code}")
async def api_save_item(request: Request, review_no: str, item_code: str):
    return await _change(
        request,
        review_no,
        lambda conn, body, user: reviews.save_item(conn, review_no, item_code, body, user),
    )


@app.post("/api/reviews/{review_no}/submit")
async def api_submit(request: Request, review_no: str):
    return await _change(
        request, review_no, lambda conn, body, user: reviews.submit(conn, review_no, user)
    )


@app.post("/api/reviews/{review_no}/return")
async def api_return(request: Request, review_no: str):
    return await _change(
        request,
        review_no,
        lambda conn, body, user: reviews.return_review(conn, review_no, body, user),
    )


# --- loans ---------------------------------------------------------------------------------------

LOAN_COLUMNS = (
    "loan_id, loan_no, borrower_name, co_borrower_name, product_code, loan_amount,"
    " interest_rate, closing_date, branch_code, branch_name, loan_officer, property_state"
)


@app.get("/loans", response_class=HTMLResponse)
def loans(request: Request, q: str | None = None):
    query = (q or "").strip()
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return to_login(request)
        rows = None
        if query:
            # R5: part of the loan number or the borrower name, ignoring case. R6: at most 50.
            rows = conn.execute(
                f"SELECT {LOAN_COLUMNS} FROM loans"
                " WHERE loan_no ILIKE %s OR borrower_name ILIKE %s ORDER BY loan_no LIMIT %s",
                (f"%{query}%", f"%{query}%", LOAN_LIMIT),
            ).fetchall()
    return page(request, "loans.html", user, q=query, rows=rows)


@app.get("/loans/{loan_no}", response_class=HTMLResponse)
def loan(request: Request, loan_no: str):
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return to_login(request)
        row = conn.execute(f"SELECT {LOAN_COLUMNS} FROM loans WHERE loan_no = %s", (loan_no,))
        row = row.fetchone()
        loan_reviews = []
        if row is not None:
            loan_reviews = conn.execute(
                "SELECT r.review_no, r.review_type, r.status, r.due_date, u.username AS assignee"
                " FROM qc_reviews r LEFT JOIN users u ON u.user_id = r.assigned_to"
                " WHERE r.loan_id = %s ORDER BY r.review_no",
                (row["loan_id"],),
            ).fetchall()
    if row is None:
        # R8
        return not_found(request, user, f"Loan {loan_no} was not found.")
    return page(request, "loan.html", user, loan=row, loan_reviews=loan_reviews)
