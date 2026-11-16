"""Sign-in: password check and a signed session token used as cookie or bearer token."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3

from app.seed import password_hash

COOKIE = "QTTSESSION"


def _secret() -> bytes:
    return os.environ.get("QTT_SECRET", "qtt-sample-local-secret").encode()


def issue(username: str) -> str:
    body = base64.urlsafe_b64encode(username.encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def username_of(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    except ValueError:
        return None


def check_password(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username.strip(),)
    ).fetchone()
    if row is None:
        return None
    if not hmac.compare_digest(row["password_hash"], password_hash(row["username"], password)):
        return None
    return row


def user(conn: sqlite3.Connection, username: str | None) -> sqlite3.Row | None:
    if not username:
        return None
    return conn.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
    ).fetchone()
