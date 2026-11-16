"""Showcase demo app: a copy of the Case Workbench API, scoped by apps/showcase/BRIEF.md.

Route handlers are copied from reference/case-workbench/backend/app/main.py. Out of scope and not
built: the canned-response routes and table. The same process also serves the built front end.
Synthetic data only.
"""

import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    AppUser,
    Case,
    CaseAudit,
    CaseNote,
    Category,
    Customer,
    Subcategory,
)
from app.seed import hash_password, seed_if_empty

# apps/showcase in the repository, /app in the image.
APP_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = APP_ROOT / "db" / "schema.sql"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", APP_ROOT / "frontend" / "dist"))
SCHEMA_TABLES = {
    "app_user",
    "customer",
    "category",
    "subcategory",
    "cases",
    "case_note",
    "case_audit",
}
MIN_ESCALATION_REASON = 10


class LoginIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class CaseUpdateIn(BaseModel):
    status: Literal["OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED"]
    category_id: int
    subcategory_id: int
    escalation_reason: str | None = None


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


def fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def case_json(case: Case) -> dict:
    return {
        "id": case.id,
        "case_no": case.case_no,
        "title": case.title,
        "status": case.status,
        "priority": case.priority,
        "category_id": case.category_id,
        "category": case.category.name if case.category else None,
        "subcategory_id": case.subcategory_id,
        "subcategory": case.subcategory.name if case.subcategory else None,
        "escalation_reason": case.escalation_reason,
        "opened_at": case.opened_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "customer": {
            "cust_no": case.customer.cust_no,
            "full_name": case.customer.full_name,
            "email": case.customer.email,
            "phone": case.customer.phone,
            "city": case.customer.city,
        },
    }


def schema_statements(sql: str) -> list[str]:
    """Split db/schema.sql into statements. It has no string literals, only `--` comments."""
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def postgres_url(url: str | None) -> str:
    """The SQLAlchemy URL for DATABASE_URL, using the psycopg (v3) driver in the image.

    db/schema.sql is Postgres DDL, so there is no SQLite fallback: without a Postgres address the
    app refuses to start rather than run on a database the schema cannot create.
    """
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to the Postgres server, for example "
            "postgresql://USER:PASSWORD@HOST:5432/DBNAME"
        )
    scheme, sep, rest = url.partition("://")
    if not sep or scheme.split("+", 1)[0] not in ("postgresql", "postgres"):
        raise RuntimeError(
            f"DATABASE_URL must be a postgresql:// URL, not {scheme or 'no scheme'}://"
        )
    return f"postgresql+psycopg://{rest}"


def wait_for_database(engine: Engine, seconds: float) -> None:
    """Give a Postgres server that is still starting a little time before giving up."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def apply_schema(engine: Engine) -> None:
    """Create the tables from db/schema.sql, unchanged, when the database has none of them."""
    existing = set(inspect(engine).get_table_names()) & SCHEMA_TABLES
    if existing == SCHEMA_TABLES:
        return
    if existing:
        missing = ", ".join(sorted(SCHEMA_TABLES - existing))
        raise RuntimeError(f"Database has some showcase tables but not: {missing}")
    with engine.begin() as conn:
        for statement in schema_statements(SCHEMA_PATH.read_text(encoding="utf-8")):
            conn.execute(text(statement))


def sync_identities(engine: Engine) -> None:
    """Move each identity sequence past the highest id in its table.

    The seed (copied from the source) sets explicit ids on category, subcategory, customer and
    cases, and data packs may do the same. Postgres does not advance an identity sequence for
    explicit ids, so without this a later insert that leaves the id out could collide.
    """
    with engine.begin() as conn:
        for table in sorted(SCHEMA_TABLES):
            conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                )
            )


def create_app(database_url: str | None = None) -> FastAPI:
    url = postgres_url(database_url or os.environ.get("DATABASE_URL"))
    engine = create_engine(url, pool_pre_ping=True)
    wait_for_database(engine, float(os.environ.get("DB_WAIT_SECONDS", "30")))
    apply_schema(engine)
    make_session = sessionmaker(engine, expire_on_commit=False)
    with make_session() as db:
        seed_if_empty(db)
    sync_identities(engine)

    # Sign-in tokens live in memory: restarting the API signs everyone out.
    tokens: dict[str, str] = {}
    app = FastAPI(title="Case Workbench", version="1.0.0")

    def get_db():
        with make_session() as db:
            yield db

    Db = Annotated[Session, Depends(get_db)]

    def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token not in tokens:
            raise fail(401, "NOT_SIGNED_IN", "Sign in to continue")
        return tokens[token]

    User = Annotated[str, Depends(current_user)]

    def load_case(db: Session, case_no: str) -> Case:
        case = db.scalar(select(Case).where(Case.case_no == case_no))
        if case is None:
            raise fail(404, "CASE_NOT_FOUND", f"Case {case_no} not found")
        return case

    def audit(db: Session, case: Case, event: str, detail: str, actor: str) -> None:
        now = datetime.now(UTC)
        case.updated_at = now
        db.add(CaseAudit(case_id=case.id, event=event, detail=detail, actor=actor, created_ts=now))

    @app.get("/api/health")
    def health(db: Db):
        return {"status": "ok", "cases": db.scalar(select(func.count(Case.id)))}

    @app.post("/api/login")
    def login(body: LoginIn, db: Db):
        user = db.scalar(select(AppUser).where(AppUser.username == body.username))
        if user is None or not secrets.compare_digest(
            user.password_hash, hash_password(body.password)
        ):
            raise fail(401, "INVALID_CREDENTIALS", "Invalid user ID or password")
        token = secrets.token_urlsafe(24)
        tokens[token] = user.username
        return {"token": token, "username": user.username, "display_name": user.display_name}

    @app.get("/api/categories")
    def categories(db: Db, user: User):
        rows = db.scalars(select(Category).order_by(Category.name))
        return [{"id": c.id, "code": c.code, "name": c.name} for c in rows]

    @app.get("/api/categories/{category_id}/subcategories")
    def subcategories(category_id: int, db: Db, user: User):
        rows = db.scalars(
            select(Subcategory)
            .where(Subcategory.category_id == category_id)
            .order_by(Subcategory.name)
        )
        return [{"id": s.id, "code": s.code, "name": s.name} for s in rows]

    @app.get("/api/cases/search")
    def search(
        db: Db,
        user: User,
        case_no: Annotated[str, Query(alias="caseNo")] = "",
        customer: str = "",
        status: str = "",
    ):
        """Case number matches by prefix, customer name by substring; both ignore case."""
        query = select(Case).join(Customer).order_by(Case.case_no).limit(50)
        if case_no.strip():
            query = query.where(func.upper(Case.case_no).like(case_no.strip().upper() + "%"))
        if customer.strip():
            query = query.where(
                func.upper(Customer.full_name).like("%" + customer.strip().upper() + "%")
            )
        if status:
            query = query.where(Case.status == status)
        items = [
            {
                "case_no": c.case_no,
                "customer": c.customer.full_name,
                "title": c.title,
                "status": c.status,
                "priority": c.priority,
            }
            for c in db.scalars(query)
        ]
        return {"items": items, "count": len(items)}

    @app.get("/api/cases/{case_no}")
    def get_case(case_no: str, db: Db, user: User):
        return case_json(load_case(db, case_no))

    @app.put("/api/cases/{case_no}")
    def update_case(case_no: str, body: CaseUpdateIn, db: Db, user: User):
        case = load_case(db, case_no)
        if case.status == "CLOSED":
            raise fail(409, "CASE_CLOSED", f"Case {case_no} is closed and cannot be changed")
        sub = db.get(Subcategory, body.subcategory_id)
        if sub is None or sub.category_id != body.category_id:
            raise fail(422, "SUBCATEGORY_MISMATCH", "Sub-category does not belong to the category")
        reason = (body.escalation_reason or "").strip()
        if body.status == "ESCALATED" and len(reason) < MIN_ESCALATION_REASON:
            raise fail(
                422,
                "ESCALATION_REASON_REQUIRED",
                f"Escalation needs a reason of at least {MIN_ESCALATION_REASON} characters",
            )
        if body.status == "RESOLVED":
            notes = db.scalar(select(func.count(CaseNote.id)).where(CaseNote.case_id == case.id))
            if not notes:
                raise fail(422, "NOTE_REQUIRED", "Add a note before resolving the case")
        previous = case.status
        case.status = body.status
        case.category_id = body.category_id
        case.subcategory_id = body.subcategory_id
        case.escalation_reason = reason if body.status == "ESCALATED" else None
        audit(db, case, "UPDATED", f"{previous} -> {body.status}; {sub.code}", user)
        db.commit()
        db.refresh(case)
        return case_json(case)

    @app.get("/api/cases/{case_no}/notes")
    def list_notes(case_no: str, db: Db, user: User):
        case = load_case(db, case_no)
        rows = db.scalars(select(CaseNote).where(CaseNote.case_id == case.id).order_by(CaseNote.id))
        return [
            {"id": n.id, "text": n.text, "author": n.author, "created_at": n.created_at.isoformat()}
            for n in rows
        ]

    @app.post("/api/cases/{case_no}/notes", status_code=201)
    def add_note(case_no: str, body: NoteIn, db: Db, user: User):
        case = load_case(db, case_no)
        if case.status == "CLOSED":
            raise fail(409, "CASE_CLOSED", f"Case {case_no} is closed and cannot be changed")
        note = CaseNote(
            case_id=case.id, text=body.text.strip(), author=user, created_at=datetime.now(UTC)
        )
        db.add(note)
        audit(db, case, "NOTE_ADDED", f"{len(note.text)} characters", user)
        db.commit()
        return {"id": note.id, "text": note.text, "author": note.author}

    mount_frontend(app)
    return app


def mount_frontend(app: FastAPI) -> None:
    """Serve the built front end: its assets, and index.html for the in-scope screens only.

    Paths of the source's router that are out of scope (/admin/responses) are not served, so they
    answer 404 like any other unknown path. "/" is served because the source router sends it on to
    /cases.
    """
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    def page() -> FileResponse:
        return FileResponse(index, media_type="text/html")

    for path in ("/", "/login", "/cases", "/cases/{case_no}"):
        app.add_api_route(path, page, methods=["GET"], include_in_schema=False)
