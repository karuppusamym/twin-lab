"""The lab agent: front door (HTTP proxy), admin API under /_lab/, and the DB link proxy."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
from collections import deque
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from lab_agent.conditions import NONE, AgentCondition, ConditionEngine, parse_condition
from lab_agent.config import SLUG, Settings
from lab_agent.data import Database, DataProblems
from lab_agent.dbproxy import DbLinkProxy
from lab_agent.httpproxy import HttpProxy, send_json
from lab_agent.schema import tables_in_ddl
from lab_agent.sessions import SessionStore, iso, utcnow
from lab_agent.store import read_json, remove, write_json

log = logging.getLogger("lab_agent")

RECENT_REQUESTS = 5000
BASELINE_ROWS = "baseline.rows.json"
BASELINE_META = "baseline.meta.json"
KEEP_BASELINES = 20
LEASE = "lease.json"
MAX_LEASE_MINUTES = 8 * 60
BASELINE_ID = re.compile(r"^bl_\d{8}T\d{6}Z_[0-9a-f]{6}$")
JSON_TYPE = "application/json"


def error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message, **extra}}, status_code=status)


def _not_json(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Why an admin request is refused for its body type, or None.

    A body must be JSON (`Content-Type: application/json`). A cross-site HTML form can only send
    urlencoded, multipart or text/plain, so this keeps forms on other sites from driving the
    lab. A request with no body is fine without a Content-Type, but one that names a type other
    than JSON is refused even when empty: browsers always label form posts.
    """
    values: dict[bytes, bytes] = {}
    for k, v in headers:
        values[k.lower()] = v
    ctype = values.get(b"content-type")
    length = values.get(b"content-length", b"").strip()
    has_body = b"transfer-encoding" in values or (length not in (b"", b"0"))
    if ctype is None:
        return f"a request body must be sent as {JSON_TYPE}" if has_body else None
    media = ctype.split(b";", 1)[0].strip().lower()
    if media != JSON_TYPE.encode():
        return f"Content-Type must be {JSON_TYPE}, not {media.decode('latin-1')[:80]!r}"
    return None


def _norm_holder(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def _holder(headers) -> str | None:
    """The `X-Lab-Holder` header (UTF-8, or Latin-1 as HTTP sends it), normalised."""
    for k, v in headers:
        if k.lower() == b"x-lab-holder":
            try:
                text = v.decode("utf-8")
            except UnicodeDecodeError:
                text = v.decode("latin-1")
            return _norm_holder(text)
    return None


class LabAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        start_db_proxy: bool = True,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.start_db_proxy = start_db_proxy
        self.state_dir = settings.state_dir
        self.engine = ConditionEngine()
        self.condition_applied_at: str | None = None
        self.baseline: dict[str, Any] | None = None  # meta only; rows stay on disk
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_REQUESTS)
        self.lock = asyncio.Lock()
        self.client: httpx.AsyncClient | None = None
        self.db_proxy: DbLinkProxy | None = None
        self.sessions: SessionStore | None = None
        only = None
        if settings.schema_path and settings.schema_path.is_file():
            only = tables_in_ddl(settings.schema_path) or None
        self.known_tables = {t.lower() for t in only or []}
        self.database = (
            Database(settings.admin_database_url, settings.db_schema, only)
            if settings.admin_database_url
            else None
        )
        self.proxy: HttpProxy | None = None
        self.admin = Starlette(routes=self._routes(), lifespan=self._lifespan)

    # --- lifecycle ---------------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _lifespan(self, _app: Starlette) -> AsyncIterator[None]:
        await self.startup()
        try:
            yield
        finally:
            await self.shutdown()

    async def startup(self) -> None:
        s = self.settings
        self.state_dir.mkdir(parents=True, exist_ok=True)
        stored = read_json(self.state_dir / "condition.json")
        if stored:
            try:
                self.engine.apply(AgentCondition.model_validate(stored["condition"]))
                self.condition_applied_at = stored.get("applied_at")
            except (ValidationError, KeyError, TypeError) as exc:
                log.error("ignoring unreadable %s: %s", self.state_dir / "condition.json", exc)
        self.baseline = read_json(self.state_dir / BASELINE_META)
        self._upgrade_baseline_rows()
        self._upgrade_history()
        self.sessions = SessionStore(self.state_dir, s.app_id, s.app_version)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(s.upstream_timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=False,
            trust_env=False,
        )
        self.proxy = HttpProxy(s.app_id, s.app_upstream, self.client, self.engine, self._record)
        if self.start_db_proxy and s.db_upstream_addr:
            self.db_proxy = DbLinkProxy(
                s.db_upstream_addr,
                lambda: self.engine.db_link,
                self.engine.db_rng,
                on_sql=self._on_sql,
            )
            await self.db_proxy.start("0.0.0.0", s.db_proxy_port)
            log.info("db link proxy on :%s -> %s", s.db_proxy_port, s.db_upstream)
        log.info("lab agent for %s: proxying to %s", s.app_id, s.app_upstream)

    async def shutdown(self) -> None:
        if self.db_proxy is not None:
            await self.db_proxy.stop()
        if self.client is not None:
            await self.client.aclose()

    def _on_sql(self, sql: str) -> None:
        """Tables a statement on the database link reads or writes, kept for the session."""
        if self.sessions is None or self.sessions.active is None:
            return
        from labctl.analyse.sql import tables as sql_tables

        found, _ = sql_tables(sql)
        known = self.known_tables
        found = [
            t
            for t in found
            if not t["table"].lower().startswith(("pg_", "information_schema"))
            and (not known or t["table"].lower() in known)
        ]
        if found:
            self.sessions.note_tables(found)

    def _record(self, rec: dict[str, Any]) -> None:
        self.recent.append(rec)
        if self.sessions is not None:
            self.sessions.record(rec)

    # --- ASGI --------------------------------------------------------------------------------

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.admin(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/_lab" or path.startswith("/_lab/"):
            if kind != "http":
                return
            headers = scope.get("headers") or []
            token = self.settings.lab_token
            if token:
                given = [v for k, v in headers if k.lower() == b"x-lab-token"]
                # Constant time; exactly one header, equal to the token.
                ok = secrets.compare_digest(given[0] if len(given) == 1 else b"", token.encode())
                if not ok or len(given) != 1:
                    await send_json(
                        send,
                        401,
                        {
                            "detail": {
                                "code": "LAB_TOKEN_REQUIRED",
                                "message": "X-Lab-Token missing or wrong",
                            }
                        },
                    )
                    return
            refused = _not_json(headers)
            if refused:
                await send_json(
                    send, 415, {"detail": {"code": "LAB_JSON_REQUIRED", "message": refused}}
                )
                return
            held = self._held_by_other(scope.get("method", "GET"), path, headers)
            if held is not None:
                await send_json(send, 423, {"detail": {
                    "code": "LAB_LEASED",
                    "message": f"{held['holder']} has reserved this lab until {held['until']}"
                    + (f" ({held['purpose']})" if held.get("purpose") else "")
                    + "; changes are refused until then",
                    "lease": held,
                }})  # fmt: skip
                return
            await self.admin(scope, receive, send)
            return
        if kind == "http":
            assert self.proxy is not None
            await self.proxy(scope, receive, send)
            return
        if kind == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1011})

    # --- lease: one person reserves the shared lab for a while ------------------------------

    def _lease(self) -> dict[str, Any] | None:
        lease = read_json(self.state_dir / LEASE)
        if not isinstance(lease, dict) or not lease.get("until"):
            return None
        if lease["until"] <= iso(utcnow()):
            return None  # expired: nobody holds the lab
        return lease

    def _held_by_other(self, method: str, path: str, headers) -> dict[str, Any] | None:
        """The lease that refuses this change, if any. Reads and the lease calls always pass."""
        if method in ("GET", "HEAD", "OPTIONS") or path.startswith("/_lab/lease"):
            return None
        lease = self._lease()
        if lease is None:
            return None
        return None if _holder(headers) == _norm_holder(lease["holder"]) else lease

    async def get_lease(self, request: Request) -> Response:
        return JSONResponse({"lease": self._lease()})

    async def take_lease(self, request: Request) -> Response:
        try:
            body = await self._body(request)
        except ValueError as exc:
            return error(400, "LAB_BAD_REQUEST", str(exc))
        holder = str((body or {}).get("holder") or "").strip()
        purpose = str((body or {}).get("purpose") or "").strip()[:200] or None
        minutes = (body or {}).get("minutes", 60)
        if not holder or len(holder) > 80:
            return error(422, "LAB_BAD_REQUEST", "'holder' is a name of 1 to 80 characters")
        if not isinstance(minutes, int) or not 1 <= minutes <= MAX_LEASE_MINUTES:
            return error(422, "LAB_BAD_REQUEST", f"'minutes' is 1 to {MAX_LEASE_MINUTES}")
        async with self.lock:
            current = self._lease()
            if current and _norm_holder(current["holder"]) != _norm_holder(holder):
                return error(409, "LAB_LEASED", f"{current['holder']} holds this lab until "
                             f"{current['until']}", lease=current)  # fmt: skip
            now = utcnow()
            lease = {"holder": holder, "purpose": purpose, "since": (current or {}).get("since") or iso(now),
                     "until": iso(now + timedelta(minutes=minutes))}  # fmt: skip
            write_json(self.state_dir / LEASE, lease)
        return JSONResponse({"lease": lease})

    async def release_lease(self, request: Request) -> Response:
        """The holder releases; anyone may with `force` (the portal asks before it does)."""
        try:
            body = await self._body(request)
        except ValueError as exc:
            return error(400, "LAB_BAD_REQUEST", str(exc))
        async with self.lock:
            current = self._lease()
            if current is None:
                remove(self.state_dir / LEASE)
                return JSONResponse({"lease": None})
            mine = _holder(request.headers.raw) == _norm_holder(current["holder"])
            if not mine and not (isinstance(body, dict) and body.get("force") is True):
                return error(423, "LAB_LEASED", f"only {current['holder']} can release it, "
                             "or send force: true", lease=current)  # fmt: skip
            remove(self.state_dir / LEASE)
        return JSONResponse({"lease": None, "released": current})

    # --- routes ------------------------------------------------------------------------------

    def _routes(self) -> list[Route]:
        return [
            Route("/_lab/lease", self.get_lease, methods=["GET"]),
            Route("/_lab/lease", self.take_lease, methods=["POST"]),
            Route("/_lab/lease", self.release_lease, methods=["DELETE"]),
            Route(
                "/_lab/baselines/{baseline_id}/quarantine",
                self.quarantine_baseline,
                methods=["POST"],
            ),
            Route("/_lab/health", self.health, methods=["GET"]),
            Route("/_lab/info", self.info, methods=["GET"]),
            Route("/_lab/data", self.data, methods=["GET"]),
            Route("/_lab/data/load", self.load, methods=["POST"]),
            Route("/_lab/data/baseline", self.make_baseline, methods=["POST"]),
            Route("/_lab/baselines", self.baselines, methods=["GET"]),
            Route("/_lab/baselines/{baseline_id}/restore", self.restore_baseline, methods=["POST"]),
            Route("/_lab/baselines/{baseline_id}/pin", self.pin_baseline, methods=["POST"]),
            Route("/_lab/reset", self.reset, methods=["POST"]),
            Route("/_lab/condition", self.get_condition, methods=["GET"]),
            Route("/_lab/condition", self.put_condition, methods=["PUT"]),
            Route("/_lab/condition", self.delete_condition, methods=["DELETE"]),
            Route("/_lab/condition/rewind", self.rewind_condition, methods=["POST"]),
            Route("/_lab/session/start", self.session_start, methods=["POST"]),
            Route("/_lab/session/stop", self.session_stop, methods=["POST"]),
            Route("/_lab/session/discard", self.session_discard, methods=["POST"]),
            Route("/_lab/sessions", self.session_list, methods=["GET"]),
            Route("/_lab/sessions/{session_id}", self.session_get, methods=["GET"]),
            Route("/_lab/sessions/{session_id}/detail", self.session_detail, methods=["GET"]),
            Route("/_lab/requests", self.requests, methods=["GET"]),
        ]

    async def _body(self, request: Request) -> Any:
        raw = await request.body()
        if not raw.strip():
            return {}
        try:
            return await request.json()
        except ValueError:
            raise ValueError("body is not valid JSON") from None

    def _no_db(self) -> JSONResponse:
        return error(503, "LAB_NO_DATABASE", "ADMIN_DATABASE_URL is not set")

    def _db_down(self, exc: Exception) -> JSONResponse:
        log.warning("database error: %s", exc)
        return error(503, "LAB_DATABASE_DOWN", f"database unreachable: {exc}")

    # health and info

    async def _app_ok(self) -> bool:
        assert self.client is not None
        try:
            r = await self.client.get(
                self.settings.app_upstream + self.settings.app_health_path, timeout=3.0
            )
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def _db_ok(self) -> str:
        if self.database is not None:
            return "ok" if await self.database.ping() else "down"
        addr = self.settings.db_upstream_addr
        if addr is None:
            return "ok"  # an app without a database
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(*addr), 3)
            w.close()
            return "ok"
        except (OSError, TimeoutError):
            return "down"

    async def health(self, request: Request) -> Response:
        app_ok, db = await asyncio.gather(self._app_ok(), self._db_ok())
        body = {"app": "ok" if app_ok else "down", "db": db}
        return JSONResponse(body, status_code=200 if app_ok and db == "ok" else 503)

    def _baseline_meta(self) -> dict[str, Any] | None:
        if not self.baseline:
            return None
        keys = ("id", "loaded_at", "label", "pack", "kind", "pinned", "quarantined")
        return {k: self.baseline.get(k) for k in keys}

    async def info(self, request: Request) -> Response:
        s = self.settings
        tables: list[dict[str, Any]] | None = None
        db_error = None
        if self.database is not None:
            try:
                schema = await self.database.read_schema()
                tables = [t.info() for t in schema.tables.values()]
            except Exception as exc:  # noqa: BLE001
                db_error = str(exc)
        assert self.sessions is not None
        active = self.sessions.active
        return JSONResponse(
            {
                "app": s.app_id,
                "version": s.app_version,
                "upstream": s.app_upstream,
                "tables": tables,
                "db_error": db_error,
                "condition": self.engine.condition.id,
                "baseline": self._baseline_meta(),
                "lease": self._lease(),
                "session": active.session_id if active else None,
                "db_proxy": {
                    "port": s.db_proxy_port,
                    "upstream": s.db_upstream,
                    "connections": self.db_proxy.active if self.db_proxy else 0,
                }
                if s.db_upstream
                else None,
            }
        )

    # data

    async def data(self, request: Request) -> Response:
        if self.database is None:
            return self._no_db()
        wanted = request.query_params.get("tables")
        names = [t.strip() for t in wanted.split(",") if t.strip()] if wanted else None
        try:
            _, rows = await self.database.snapshot(names)
        except Exception as exc:  # noqa: BLE001
            return self._db_down(exc)
        return JSONResponse(rows)

    def _save_baseline(self, rows: dict, label: str | None, pack: str | None, kind: str) -> dict:
        now = utcnow()
        meta = {
            "id": f"bl_{now.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}",
            "loaded_at": iso(now),
            "label": label,
            "pack": pack,
            "kind": kind,
            "tables": sorted(rows),
        }
        # The history copy first: a crash after it leaves an extra entry, never a current
        # baseline missing from the history.
        write_json(self._history_path(meta["id"]), {"meta": meta, "rows": rows})
        self._make_current(meta, rows)
        self._prune_history()
        return meta

    def _make_current(self, meta: dict, rows: dict) -> None:
        # Two files, each written atomically and each carrying the baseline id; reset checks the
        # ids agree, so a crash between the writes can never restore rows under the wrong label.
        write_json(self.state_dir / BASELINE_ROWS, {"baseline_id": meta["id"], "rows": rows})
        write_json(self.state_dir / BASELINE_META, meta)
        self.baseline = meta

    # baseline history: every baseline is kept under baselines/<id>.json, newest KEEP_BASELINES
    # unpinned ones plus every pinned one.

    def _history_path(self, baseline_id: str):
        return self.state_dir / "baselines" / f"{baseline_id}.json"

    def _history(self) -> list[dict[str, Any]]:
        folder = self.state_dir / "baselines"
        out = []
        for path in folder.glob("bl_*.json") if folder.is_dir() else []:
            doc = read_json(path)
            if isinstance(doc, dict) and isinstance(doc.get("meta"), dict):
                meta = dict(doc["meta"])
                rows = doc.get("rows") or {}
                meta["counts"] = {t: len(r) for t, r in rows.items()}
                out.append(meta)
        return sorted(out, key=lambda m: m.get("loaded_at") or "", reverse=True)

    def _prune_history(self) -> None:
        current = (self.baseline or {}).get("id")
        unpinned = [
            m
            for m in self._history()
            if not m.get("pinned") and not m.get("quarantined") and m["id"] != current
        ]
        for meta in unpinned[KEEP_BASELINES - 1 :]:
            remove(self._history_path(meta["id"]))

    def _upgrade_history(self) -> None:
        """A current baseline from an agent without history gets its history entry."""
        if not self.baseline or self._history_path(self.baseline["id"]).exists():
            return
        rows, problem = self._read_baseline_rows()
        if problem is None:
            write_json(
                self._history_path(self.baseline["id"]), {"meta": self.baseline, "rows": rows}
            )

    def _upgrade_baseline_rows(self) -> None:
        """Agents before the baseline id wrote `{table: [rows]}`. At startup nothing else is
        writing, so such a file belongs to the meta beside it: stamp it with that id once, so
        Reset keeps working after an upgrade instead of asking for a fresh baseline."""
        path = self.state_dir / BASELINE_ROWS
        stored = read_json(path)
        meta_id = self.baseline.get("id") if isinstance(self.baseline, dict) else None
        if (
            meta_id
            and isinstance(stored, dict)
            and "baseline_id" not in stored
            and all(isinstance(v, list) for v in stored.values())
        ):
            write_json(path, {"baseline_id": meta_id, "rows": stored})
            log.info("baseline rows upgraded to the current format (%s)", meta_id)

    def _read_baseline_rows(self) -> tuple[dict | None, JSONResponse | None]:
        """The baseline rows, checked against the baseline metadata. Call with the lock held."""
        if not self.baseline:
            return None, error(
                409, "LAB_NO_BASELINE", "no baseline yet: load data or take a baseline"
            )
        meta = read_json(self.state_dir / BASELINE_META)
        stored = read_json(self.state_dir / BASELINE_ROWS)
        if stored is None:
            return None, error(409, "LAB_NO_BASELINE", "baseline rows are missing from STATE_DIR")
        want = self.baseline.get("id")
        got = stored.get("baseline_id") if isinstance(stored, dict) else None
        meta_id = meta.get("id") if isinstance(meta, dict) else None
        if not want or got != want or meta_id != want or not isinstance(stored.get("rows"), dict):
            return None, error(
                409,
                "LAB_BASELINE_MISMATCH",
                "the baseline rows on disk do not belong to the current baseline "
                f"({want}); nothing was changed. Load data or take a baseline again",
                baseline=want,
                rows_baseline=got,
            )
        return stored["rows"], None

    async def load(self, request: Request) -> Response:
        if self.database is None:
            return self._no_db()
        try:
            body = await self._body(request)
        except ValueError as exc:
            return error(400, "LAB_BAD_REQUEST", str(exc))
        rows = body.get("rows") if isinstance(body, dict) else None
        if not isinstance(rows, dict) or not rows:
            return error(422, "LAB_DATA_INVALID", "body needs 'rows': {table: [row, ...]}")
        label = body.get("label")
        pack = body.get("pack")
        if pack is not None and (not isinstance(pack, str) or not SLUG.match(pack)):
            return error(422, "LAB_DATA_INVALID", "'pack' must be a slug")
        async with self.lock:
            try:
                result = await self.database.replace(rows)
            except DataProblems as exc:
                return error(
                    422,
                    "LAB_DATA_INVALID",
                    f"{len(exc.problems)} problem(s); nothing was loaded",
                    problems=[p.as_dict() for p in exc.problems],
                )
            except Exception as exc:  # noqa: BLE001
                return self._db_down(exc)
            meta = self._save_baseline(result.snapshot, label, pack, "load")
        return JSONResponse(
            {
                "baseline": self._baseline_meta(),
                "replaced": result.replaced,
                "cleared": result.cleared,
                "counts": result.counts,
                "triggers": result.triggers,
                "id": meta["id"],
            }
        )

    async def make_baseline(self, request: Request) -> Response:
        if self.database is None:
            return self._no_db()
        try:
            body = await self._body(request)
        except ValueError as exc:
            return error(400, "LAB_BAD_REQUEST", str(exc))
        label = body.get("label") if isinstance(body, dict) else None
        pack = body.get("pack") if isinstance(body, dict) else None
        if pack is not None and (not isinstance(pack, str) or not SLUG.match(pack)):
            return error(422, "LAB_BAD_REQUEST", "'pack' must be a slug")
        async with self.lock:
            try:
                _, rows = await self.database.snapshot()
            except Exception as exc:  # noqa: BLE001
                return self._db_down(exc)
            self._save_baseline(rows, label, pack, "snapshot")
        return JSONResponse(
            {"baseline": self._baseline_meta(), "counts": {t: len(r) for t, r in rows.items()}}
        )

    async def reset(self, request: Request) -> Response:
        if self.database is None:
            return self._no_db()
        if (self.baseline or {}).get("quarantined"):
            return error(409, "LAB_BASELINE_QUARANTINED", "the current baseline is quarantined ("
                         f"{self.baseline['quarantined']}): restore or load other data first")  # fmt: skip
        async with self.lock:  # a load cannot commit between reading the baseline and restoring
            rows, problem = await asyncio.to_thread(self._read_baseline_rows)
            if problem is not None:
                return problem
            try:
                result = await self.database.replace(rows)
            except DataProblems as exc:
                return error(
                    409,
                    "LAB_BASELINE_MISMATCH",
                    "the baseline no longer fits the database; nothing was changed",
                    problems=[p.as_dict() for p in exc.problems],
                )
            except Exception as exc:  # noqa: BLE001
                return self._db_down(exc)
            meta = self._baseline_meta()
        return JSONResponse({"baseline": meta, "counts": result.counts, "cleared": result.cleared})

    async def baselines(self, request: Request) -> Response:
        current = (self.baseline or {}).get("id")
        items = await asyncio.to_thread(self._history)
        for meta in items:
            meta["current"] = meta["id"] == current
            meta.pop("tables", None)
        return JSONResponse(items)

    def _stored_baseline(self, baseline_id: str) -> tuple[dict | None, JSONResponse | None]:
        if not BASELINE_ID.match(baseline_id):
            return None, error(404, "LAB_BASELINE_NOT_FOUND", "no such baseline")
        doc = read_json(self._history_path(baseline_id))
        if not isinstance(doc, dict) or not isinstance(doc.get("rows"), dict):
            return None, error(404, "LAB_BASELINE_NOT_FOUND", "no such baseline")
        return doc, None

    async def restore_baseline(self, request: Request) -> Response:
        """Put an earlier baseline's rows back and make it the current baseline again."""
        if self.database is None:
            return self._no_db()
        async with self.lock:
            doc, problem = await asyncio.to_thread(
                self._stored_baseline, request.path_params["baseline_id"]
            )
            if problem is not None:
                return problem
            if doc["meta"].get("quarantined"):
                return error(409, "LAB_BASELINE_QUARANTINED", "this baseline is quarantined: "
                             f"{doc['meta']['quarantined']}")  # fmt: skip
            try:
                result = await self.database.replace(doc["rows"])
            except DataProblems as exc:
                return error(
                    409,
                    "LAB_BASELINE_MISMATCH",
                    "that baseline no longer fits the database (the schema changed); "
                    "nothing was changed",
                    problems=[p.as_dict() for p in exc.problems],
                )
            except Exception as exc:  # noqa: BLE001
                return self._db_down(exc)
            meta = {k: v for k, v in doc["meta"].items() if k != "counts"}
            self._make_current(meta, doc["rows"])
        return JSONResponse(
            {"baseline": self._baseline_meta(), "counts": result.counts, "cleared": result.cleared}
        )

    async def pin_baseline(self, request: Request) -> Response:
        try:
            body = await self._body(request)
        except ValueError as exc:
            return error(400, "LAB_BAD_REQUEST", str(exc))
        pinned = body.get("pinned", True) if isinstance(body, dict) else True
        if not isinstance(pinned, bool):
            return error(422, "LAB_BAD_REQUEST", "'pinned' must be true or false")
        async with self.lock:
            baseline_id = request.path_params["baseline_id"]
            doc, problem = await asyncio.to_thread(self._stored_baseline, baseline_id)
            if problem is not None:
                return problem
            doc["meta"]["pinned"] = pinned
            write_json(self._history_path(baseline_id), doc)
            if self.baseline and self.baseline.get("id") == baseline_id:
                self.baseline = {**self.baseline, "pinned": pinned}
                write_json(self.state_dir / BASELINE_META, self.baseline)
            if not pinned:
                self._prune_history()
        return JSONResponse({"id": baseline_id, "pinned": pinned})

    async def quarantine_baseline(self, request: Request) -> Response:
        """Mark a baseline unusable (say, data found to be unmasked) with a reason, or release it
        with `{"release": true}`. A quarantined baseline cannot be restored or reset to, and is
        kept, so the reason stays on record."""
        try:
            body = await self._body(request)
        except ValueError as exc:
            return error(400, "LAB_BAD_REQUEST", str(exc))
        body = body if isinstance(body, dict) else {}
        reason = str(body.get("reason") or "").strip()[:300]
        release = body.get("release") is True
        if not release and not reason:
            return error(422, "LAB_BAD_REQUEST", "give a 'reason', or 'release': true")
        baseline_id = request.path_params["baseline_id"]
        async with self.lock:
            doc, problem = await asyncio.to_thread(self._stored_baseline, baseline_id)
            if problem is not None:
                return problem
            doc["meta"]["quarantined"] = None if release else reason
            write_json(self._history_path(baseline_id), doc)
            if self.baseline and self.baseline.get("id") == baseline_id:
                self.baseline = {**self.baseline, "quarantined": doc["meta"]["quarantined"]}
                write_json(self.state_dir / BASELINE_META, self.baseline)
        return JSONResponse({"id": baseline_id, "quarantined": doc["meta"]["quarantined"]})

    # condition

    def _condition_body(self) -> dict[str, Any]:
        return {
            **self.engine.condition.model_dump(mode="json"),
            "applied_at": self.condition_applied_at,
        }

    async def get_condition(self, request: Request) -> Response:
        return JSONResponse(self._condition_body())

    async def put_condition(self, request: Request) -> Response:
        try:
            body = await self._body(request)
            condition = parse_condition(body or None, self.settings.app_id)
        except ValidationError as exc:
            return error(
                422,
                "LAB_CONDITION_INVALID",
                "condition is not valid",
                errors=[
                    {"loc": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            )
        except ValueError as exc:
            return error(422, "LAB_CONDITION_INVALID", str(exc))
        self.condition_applied_at = iso(utcnow())
        write_json(
            self.state_dir / "condition.json",
            {
                "condition": condition.model_dump(mode="json"),
                "applied_at": self.condition_applied_at,
            },
        )
        self.engine.apply(condition)
        return JSONResponse(self._condition_body())

    async def delete_condition(self, request: Request) -> Response:
        remove(self.state_dir / "condition.json")
        self.engine.apply(NONE)
        self.condition_applied_at = None
        return JSONResponse(self._condition_body())

    async def rewind_condition(self, request: Request) -> Response:
        self.engine.rewind()
        return JSONResponse(self._condition_body())

    # sessions

    async def session_start(self, request: Request) -> Response:
        assert self.sessions is not None
        if self.database is None:
            return self._no_db()
        try:
            body = await self._body(request)
        except ValueError as exc:
            return error(400, "LAB_BAD_REQUEST", str(exc))
        if not isinstance(body, dict):
            return error(422, "LAB_BAD_REQUEST", "body must be an object")
        automation = body.get("automation")
        label = body.get("label")
        if automation is not None and (
            not isinstance(automation, str) or not SLUG.match(automation)
        ):
            return error(422, "LAB_BAD_REQUEST", "'automation' must be a slug")
        async with self.lock:
            if self.sessions.active is not None:
                return error(
                    409,
                    "LAB_SESSION_RUNNING",
                    f"session {self.sessions.active.session_id} is already running",
                )
            try:
                _, before = await self.database.snapshot()
            except Exception as exc:  # noqa: BLE001
                return self._db_down(exc)
            pack = (self.baseline or {}).get("pack") or (self.baseline or {}).get("label")
            if not (isinstance(pack, str) and SLUG.match(pack)):
                pack = None
            cond = self.engine.condition.id
            active = self.sessions.start(before, automation, label, cond, pack)
            self.engine.rewind()  # the same run under the same seeded condition repeats
        return JSONResponse({"session_id": active.session_id, "started": iso(active.started)})

    async def session_stop(self, request: Request) -> Response:
        assert self.sessions is not None
        if self.database is None:
            return self._no_db()
        async with self.lock:
            if self.sessions.active is None:
                return error(409, "LAB_NO_SESSION", "no session is running")
            try:
                schema, after = await self.database.snapshot()
            except Exception as exc:  # noqa: BLE001
                return self._db_down(exc)
            pks = {name: t.primary_key for name, t in schema.tables.items()}
            doc = self.sessions.stop(after, pks)
        return JSONResponse(doc)

    async def session_discard(self, request: Request) -> Response:
        assert self.sessions is not None
        async with self.lock:
            if self.sessions.active is None:
                return error(409, "LAB_NO_SESSION", "no session is running")
            sid = self.sessions.active.session_id
            self.sessions.discard()
        return JSONResponse({"discarded": sid})

    async def session_list(self, request: Request) -> Response:
        assert self.sessions is not None
        return JSONResponse(self.sessions.list())

    async def session_get(self, request: Request) -> Response:
        assert self.sessions is not None
        doc = self.sessions.get(request.path_params["session_id"])
        if doc is None:
            return error(404, "LAB_SESSION_NOT_FOUND", "no such session")
        return JSONResponse(doc)

    async def session_detail(self, request: Request) -> Response:
        assert self.sessions is not None
        doc = self.sessions.detail(request.path_params["session_id"])
        if doc is None:
            return error(404, "LAB_SESSION_NOT_FOUND", "no such session")
        return JSONResponse(doc)

    async def requests(self, request: Request) -> Response:
        try:
            limit = max(1, min(RECENT_REQUESTS, int(request.query_params.get("limit", 100))))
        except ValueError:
            limit = 100
        return JSONResponse(list(self.recent)[-limit:])


def create_app(settings: Settings | None = None, **kwargs: Any) -> LabAgent:
    return LabAgent(settings or Settings.from_env(), **kwargs)
