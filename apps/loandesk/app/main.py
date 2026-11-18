"""Loan Desk demo copy: server-rendered screens (login, loan search, loan detail), one process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth, db

logging.basicConfig(level=logging.INFO)

HERE = Path(__file__).resolve().parent
VERSION = "1.0.0"
LOAN_LIMIT = 50  # R2
DEFAULT_NEXT = "/loans"

PRODUCTS = {
    "F30": "Fixed 30",
    "F15": "Fixed 15",
    "J30": "Fixed Jumbo 30",
    "A71": "ARM 7/1",
    "A51": "ARM 5/1",
    "VA": "VA",
    "FHA": "FHA 30",
}

templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["version"] = VERSION
templates.env.globals["product_name"] = lambda code: PRODUCTS.get(code, code)
templates.env.filters["money"] = lambda v: f"${float(v):,.2f}"
templates.env.filters["rate"] = lambda v: f"{float(v):.3f}%"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.start_initialiser()
    yield


app = FastAPI(
    title="Loan Desk demo copy", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def current_user(conn, request: Request):
    user_id = auth.read_cookie(request.cookies.get(auth.COOKIE))
    if user_id is None:
        return None
    return conn.execute(
        "SELECT user_id, username, full_name, role FROM users WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()


def find_user(conn, username: str, password: str):
    user = conn.execute(
        "SELECT user_id, username, full_name, role, password_hash FROM users"
        " WHERE username = ? AND is_active = 1",
        (username,),
    ).fetchone()
    if user and auth.verify_password(password, user["password_hash"]):
        return user
    return None


def to_login(request: Request) -> RedirectResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


def page(request: Request, name: str, user, http_status: int = 200, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"user": user, **ctx}, status_code=http_status)


def safe_next(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return DEFAULT_NEXT


# --- health --------------------------------------------------------------------------------------


@app.get("/api/health")
def health():
    if db.ping():
        return {"status": "ok"}
    return JSONResponse({"status": "database unreachable"}, status_code=503)


# --- sign in -------------------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = DEFAULT_NEXT):
    return page(request, "login.html", None, next=safe_next(next), username="", error=None)


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    target = safe_next(str(form.get("next", "") or DEFAULT_NEXT))
    with db.connect() as conn:
        user = find_user(conn, username, str(form.get("password", "")))
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


# --- loans ---------------------------------------------------------------------------------------

LOAN_COLUMNS = (
    "loan_id, loan_no, borrower_name, co_borrower_name, product_code, loan_amount,"
    " interest_rate, closing_date, branch_code, branch_name, loan_officer, property_state"
)


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@app.get("/loans", response_class=HTMLResponse)
def loans(request: Request, q: str | None = None):
    query = (q or "").strip()
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return to_login(request)
        rows = None
        if query:
            # R1: part of the loan number or the borrower name, ignoring case. R2: at most 50.
            pattern = _like(query)
            rows = conn.execute(
                f"SELECT {LOAN_COLUMNS} FROM loans"
                " WHERE lower(loan_no) LIKE lower(?) ESCAPE '\\'"
                " OR lower(borrower_name) LIKE lower(?) ESCAPE '\\'"
                " ORDER BY loan_no LIMIT ?",
                (pattern, pattern, LOAN_LIMIT),
            ).fetchall()
    return page(request, "loans.html", user, q=query, rows=rows)


@app.get("/loans/{loan_no}", response_class=HTMLResponse)
def loan(request: Request, loan_no: str):
    with db.connect() as conn:
        user = current_user(conn, request)
        if user is None:
            return to_login(request)
        row = conn.execute(
            f"SELECT {LOAN_COLUMNS} FROM loans WHERE loan_no = ?", (loan_no,)
        ).fetchone()
        loan_reviews = []
        if row is not None:
            loan_reviews = conn.execute(
                "SELECT r.review_no, r.review_type, r.status, r.due_date, u.username AS assignee"
                " FROM qc_reviews r LEFT JOIN users u ON u.user_id = r.assigned_to"
                " WHERE r.loan_id = ? ORDER BY r.review_no",
                (row["loan_id"],),
            ).fetchall()
    if row is None:
        # R4
        return page(
            request, "error.html", user, http_status=404, message=f"Loan {loan_no} was not found."
        )
    return page(request, "loan.html", user, loan=row, loan_reviews=loan_reviews)
