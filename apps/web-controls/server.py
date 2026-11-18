"""Serve only the recorded pages and local assets. No task database was observed."""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
ROUTES = json.loads((HERE / "routes.json").read_text())
LEAVES_SCOPE = set()  # the home link is / itself, redirected above
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/health")
def health():
    return {"status": "ok", "storage": "no server database observed"}


@app.get("/")
def index():
    return RedirectResponse(next(iter(ROUTES)))


@app.get("/{path:path}")
def serve(path: str):
    if "/" + path in LEAVES_SCOPE:
        # a link the recorded pages have to a page outside the scope: it stays on the page
        # and is answered with the first screen in scope (BRIEF.md, Links that leave the scope)
        return RedirectResponse(next(iter(ROUTES)))
    target = (SITE / ROUTES.get("/" + path, path)).resolve()
    if SITE.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "Not in recorded scope")
    if target.suffix == ".html" and "/" + path not in ROUTES:
        raise HTTPException(404, "Not in recorded scope")
    return FileResponse(target)
