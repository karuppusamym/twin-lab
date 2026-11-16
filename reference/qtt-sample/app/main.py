"""QTT sample: Quality Tracking Tool for loan quality-control reviews.

Server-rendered pages (Jinja2) plus a JSON API that the review page calls with fetch.
Run from reference/qtt-sample/:  python -m uvicorn app.main:app --port 8730
"""

import sqlite3
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import auth, db, reviews
from app.reviews import ApiError
from app.seed import PRODUCTS

VERSION = "3.4.1"


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


class ItemBody(BaseModel):
    result: str | None = None
    comment: str | None = None


class ReturnBody(BaseModel):
    reason: str | None = None


class NotSignedIn(Exception):
    pass


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)


def create_app(db_path: Path | str | None = None) -> FastAPI:
    path = Path(db_path) if db_path else db.db_path_from_env()
    db.initialise(path)

    app = FastAPI(title="QTT - Quality Tracking Tool", version=VERSION, docs_url=None)
    app.mount("/static", StaticFiles(directory=db.PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=db.PACKAGE_DIR / "templates")
    templates.env.globals.update(
        version=VERSION, results=reviews.RESULTS, statuses=reviews.STATUSES
    )

    def get_conn():
        yield from db.connections(path)

    Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

    def token_of(request: Request) -> str | None:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return request.cookies.get(auth.COOKIE)

    def api_user(request: Request, conn: Conn) -> sqlite3.Row:
        user = auth.user(conn, auth.username_of(token_of(request)))
        if user is None:
            raise ApiError(401, "NOT_AUTHENTICATED", "Sign in first.")
        return user

    def page_user(request: Request, conn: Conn) -> sqlite3.Row:
        user = auth.user(conn, auth.username_of(token_of(request)))
        if user is None:
            raise NotSignedIn()
        return user

    ApiUser = Annotated[sqlite3.Row, Depends(api_user)]
    PageUser = Annotated[sqlite3.Row, Depends(page_user)]

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        if request.url.path.startswith("/api/"):
            return _error(exc.status, exc.code, exc.message)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"user": None, "status": exc.status, "code": exc.code, "message": exc.message},
            status_code=exc.status,
        )

    @app.exception_handler(NotSignedIn)
    async def _not_signed_in(request: Request, exc: NotSignedIn):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=303)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return _error(422, "VALIDATION_ERROR", f"{where}: {first.get('msg', 'invalid request')}")

    def page(request: Request, name: str, user, status_code: int = 200, **context):
        return templates.TemplateResponse(
            request, name, {"user": user, **context}, status_code=status_code
        )

    # --- pages -----------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def home() -> Response:
        return RedirectResponse("/queue", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/queue"):
        return page(request, "login.html", None, next=next, error=None, username="")

    @app.post("/login", response_class=HTMLResponse)
    def login_submit(
        request: Request,
        conn: Conn,
        username: Annotated[str, Form()] = "",
        password: Annotated[str, Form()] = "",
        next: Annotated[str, Form()] = "/queue",
    ):
        user = auth.check_password(conn, username, password)
        if user is None:
            return page(
                request,
                "login.html",
                None,
                status_code=401,
                next=next,
                username=username,
                error="Invalid user id or password.",
            )
        target = next if next.startswith("/") and not next.startswith("//") else "/queue"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(auth.COOKIE, auth.issue(user["username"]), httponly=True, path="/")
        return response

    @app.get("/logout")
    def logout() -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(auth.COOKIE, path="/")
        return response

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(
        request: Request, conn: Conn, user: PageUser, status: str = "", mine: str = "", q: str = ""
    ):
        assignee = user["username"] if mine else None
        rows = reviews.list_reviews(conn, status or None, assignee, q)
        return page(
            request, "queue.html", user, rows=rows, status=status.upper(), mine=bool(mine), q=q
        )

    @app.get("/reviews/{review_no}", response_class=HTMLResponse)
    def review_page(request: Request, conn: Conn, user: PageUser, review_no: str):
        review = reviews.get_review(conn, review_no, user)
        by_category = {
            c: [i for i in review["items"] if i["category"] == c] for c in reviews.CATEGORIES
        }
        return page(request, "review.html", user, review=review, by_category=by_category)

    @app.get("/loans", response_class=HTMLResponse)
    def loans_page(request: Request, conn: Conn, user: PageUser, q: str | None = None):
        rows = reviews.search_loans(conn, q) if q is not None else None
        return page(request, "loans.html", user, rows=rows, q=q or "")

    @app.get("/loans/{loan_no}", response_class=HTMLResponse)
    def loan_page(request: Request, conn: Conn, user: PageUser, loan_no: str):
        return page(request, "loan.html", user, loan=reviews.get_loan(conn, loan_no))

    def admin_only(request: Request, user: sqlite3.Row):
        if user["role"] != "REVIEWER":
            return page(
                request,
                "error.html",
                user,
                status_code=403,
                status=403,
                code="FORBIDDEN_ROLE",
                message="The checklist templates are available to reviewers only.",
            )
        return None

    @app.get("/admin/checklist", response_class=HTMLResponse)
    def admin_checklist(request: Request, conn: Conn, user: PageUser, saved: str = ""):
        denied = admin_only(request, user)
        if denied:
            return denied
        return page(
            request,
            "admin_checklist.html",
            user,
            items=reviews.checklist(conn),
            saved=saved,
            error=None,
        )

    @app.post("/admin/checklist/{item_code}", response_class=HTMLResponse)
    def admin_checklist_save(
        request: Request,
        conn: Conn,
        user: PageUser,
        item_code: str,
        description: Annotated[str, Form()] = "",
        is_active: Annotated[str, Form()] = "",
    ):
        denied = admin_only(request, user)
        if denied:
            return denied
        try:
            reviews.update_checklist_item(conn, item_code, description, bool(is_active))
        except ApiError as exc:
            return page(
                request,
                "admin_checklist.html",
                user,
                status_code=exc.status,
                items=reviews.checklist(conn),
                saved="",
                error=f"{item_code}: {exc.message}",
            )
        return RedirectResponse(f"/admin/checklist?saved={item_code.upper()}", status_code=303)

    # --- API -------------------------------------------------------------------------------

    @app.get("/api/health")
    def health(conn: Conn) -> dict:
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "ok", "version": VERSION}

    @app.post("/api/login")
    def api_login(body: LoginBody, conn: Conn) -> JSONResponse:
        user = auth.check_password(conn, body.username, body.password)
        if user is None:
            raise ApiError(401, "INVALID_CREDENTIALS", "Invalid user id or password.")
        token = auth.issue(user["username"])
        response = JSONResponse(
            {
                "token": token,
                "username": user["username"],
                "full_name": user["full_name"],
                "role": user["role"],
            }
        )
        response.set_cookie(auth.COOKIE, token, httponly=True, path="/")
        return response

    @app.get("/api/me")
    def me(user: ApiUser) -> dict:
        return {"username": user["username"], "full_name": user["full_name"], "role": user["role"]}

    @app.get("/api/reviews")
    def api_reviews(
        conn: Conn,
        user: ApiUser,
        status: str | None = None,
        assignee: str | None = None,
        q: str | None = None,
    ) -> dict:
        if assignee == "me":
            assignee = user["username"]
        items = reviews.list_reviews(conn, status, assignee, q)
        return {"items": items, "total": len(items)}

    @app.get("/api/reviews/{review_no}")
    def api_review(conn: Conn, user: ApiUser, review_no: str) -> dict:
        return reviews.get_review(conn, review_no, user)

    @app.put("/api/reviews/{review_no}/items/{item_id}")
    def api_save_item(
        conn: Conn, user: ApiUser, review_no: str, item_id: str, body: ItemBody
    ) -> dict:
        reviews.save_item(conn, review_no, item_id, body.result, body.comment, user)
        return reviews.get_review(conn, review_no, user)

    @app.post("/api/reviews/{review_no}/submit")
    def api_submit(conn: Conn, user: ApiUser, review_no: str) -> dict:
        reviews.submit(conn, review_no, user)
        return reviews.get_review(conn, review_no, user)

    @app.post("/api/reviews/{review_no}/approve")
    def api_approve(conn: Conn, user: ApiUser, review_no: str) -> dict:
        reviews.approve(conn, review_no, user)
        return reviews.get_review(conn, review_no, user)

    @app.post("/api/reviews/{review_no}/return")
    def api_return(conn: Conn, user: ApiUser, review_no: str, body: ReturnBody) -> dict:
        reviews.return_review(conn, review_no, body.reason, user)
        return reviews.get_review(conn, review_no, user)

    @app.post("/api/reviews/{review_no}/reopen")
    def api_reopen(conn: Conn, user: ApiUser, review_no: str) -> dict:
        reviews.reopen(conn, review_no, user)
        return reviews.get_review(conn, review_no, user)

    @app.get("/api/loans")
    def api_loans(conn: Conn, user: ApiUser, q: Annotated[str, Query()] = "") -> dict:
        items = reviews.search_loans(conn, q)
        return {"items": items, "total": len(items)}

    @app.get("/api/loans/{loan_no}")
    def api_loan(conn: Conn, user: ApiUser, loan_no: str) -> dict:
        return reviews.get_loan(conn, loan_no)

    app.state.db_path = path
    app.state.products = PRODUCTS
    return app


def __getattr__(name: str):
    # `uvicorn app.main:app` builds the app on first access, so importing the module for tests
    # does not create the default database file.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
