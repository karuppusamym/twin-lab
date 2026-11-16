"""Password hashes and the signed session cookie."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

COOKIE = "qtt_session"
SESSION_SECONDS = 8 * 3600
# A fresh key per process: no secret is baked into the image; sessions end when the app restarts.
_KEY = secrets.token_bytes(32)

_ITERATIONS = 120_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


def _sign(payload: bytes) -> str:
    return hmac.new(_KEY, payload, hashlib.sha256).hexdigest()


def make_cookie(user_id: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"uid": user_id, "exp": int(time.time()) + SESSION_SECONDS}).encode()
    )
    return f"{payload.decode()}.{_sign(payload)}"


def read_cookie(value: str | None) -> int | None:
    if not value or "." not in value:
        return None
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload.encode()), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return int(data["uid"])
