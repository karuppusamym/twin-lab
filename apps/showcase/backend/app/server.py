"""Entry point for uvicorn: `uvicorn app.server:app`. Reads DATABASE_URL."""

from app.main import create_app

app = create_app()
