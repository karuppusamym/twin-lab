"""Local hosting; task persistence intentionally remains browser-local."""

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/health")
def health():
    url = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
    if os.environ.get("DB_ENGINE", "sqlite") != "sqlite" or not url.startswith("sqlite:///"):
        raise HTTPException(503, "This replica supports SQLite only")
    try:
        with sqlite3.connect(url.removeprefix("sqlite:///")) as db:
            db.executescript((HERE / "db/schema.sql").read_text(encoding="utf-8"))
            db.execute("SELECT 1")
    except sqlite3.Error as exc:
        raise HTTPException(503, "Database unavailable") from exc
    return {"status": "ok", "task_storage": "browser-local"}


@app.get("/")
def index():
    return RedirectResponse("/todomvc/")


app.mount("/todomvc", StaticFiles(directory=HERE / "static", html=True), name="todo")
