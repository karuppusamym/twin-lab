"""Read an API description into declared routes: OpenAPI 3.x (JSON or YAML) or Postman v2.1.

An OpenAPI description lists every route the API serves, so it is complete for routes. A Postman
collection lists the requests somebody saved, so it is not. Example values in either are never
kept: request and response bodies become shapes, as for recordings.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from labctl.appmodel import AppModel, Evidence, Route
from labctl.har import merge_shapes, shape, type_of

METHODS = ("get", "post", "put", "patch", "delete")


def load(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except ValueError:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{Path(path).name} is not an OpenAPI description or Postman collection")
    return data


def analyse(app: str, path: Path) -> AppModel:
    path = Path(path)
    data = load(path)
    ref = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9-]+", "-", path.stem.lower()).strip("-") or "api"
    if "openapi" in data:
        if not str(data["openapi"]).startswith("3"):
            raise ValueError(f"{path.name}: OpenAPI {data['openapi']} is not supported (3.x only)")
        return _openapi(app, data, f"openapi:{slug}", ref)
    if "swagger" in data:
        raise ValueError(f"{path.name}: Swagger 2.0 is not supported; convert it to OpenAPI 3")
    if "item" in data:  # Postman v2.x (info.schema names the version)
        return _postman(app, data, f"postman:{slug}", ref)
    raise ValueError(f"{path.name} is neither OpenAPI 3.x nor a Postman v2.1 collection")


# --- OpenAPI ---------------------------------------------------------------------------------------


class _Schemas:
    def __init__(self, spec: dict):
        self.spec = spec

    def resolve(self, node: Any, seen: tuple[str, ...] = ()) -> Any:
        if isinstance(node, dict) and "$ref" in node:
            ref = str(node["$ref"])
            if ref in seen or not ref.startswith("#/"):
                return {}
            target: Any = self.spec
            for part in ref[2:].split("/"):
                target = (target or {}).get(part.replace("~1", "/").replace("~0", "~"), {})
            return self.resolve(target, (*seen, ref))
        return node

    def shape(self, schema: Any, seen: tuple[int, ...] = (), depth: int = 0) -> Any:
        schema = self.resolve(schema)
        if not isinstance(schema, dict) or depth > 8:
            return "unknown"
        for combo in ("allOf", "oneOf", "anyOf"):
            if combo in schema:
                out: Any = "unknown"
                for part in schema[combo]:
                    s = self.shape(part, seen, depth + 1)
                    out = s if out == "unknown" else merge_shapes(out, s)
                return out
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), None)
        if kind == "array" or "items" in schema:
            return {"[]": self.shape(schema.get("items", {}), seen, depth + 1)}
        if kind == "object" or "properties" in schema:
            return {
                str(k): self.shape(v, seen, depth + 1)
                for k, v in (schema.get("properties") or {}).items()
            }
        if kind in ("integer", "number", "boolean"):
            return kind
        if kind == "string":
            fmt = schema.get("format")
            return f"string({fmt})" if fmt in ("date-time", "date", "email") else "string"
        return "unknown"

    def fields(self, schema: Any) -> list[str]:
        """Request body fields with their types and constraints."""
        schema = self.resolve(schema)
        if isinstance(schema, dict) and "allOf" in schema:
            merged: dict = {"properties": {}, "required": []}
            for part in schema["allOf"]:
                part = self.resolve(part)
                merged["properties"].update(part.get("properties") or {})
                merged["required"] += part.get("required") or []
            schema = merged
        if not isinstance(schema, dict) or not schema.get("properties"):
            s = self.shape(schema)
            return [f"(body): {type_of(s)}"] if s != "unknown" else []
        required = set(schema.get("required") or [])
        out = []
        for name, prop in schema["properties"].items():
            prop = self.resolve(prop)
            parts = [type_of(self.shape(prop))]
            if name not in required:
                parts.append("optional")
            for key in ("minLength", "maxLength", "minimum", "maximum", "pattern"):
                if isinstance(prop, dict) and key in prop:
                    parts.append(f"{key}={prop[key]}")
            if isinstance(prop, dict) and prop.get("enum"):
                parts.append("one of " + ", ".join(str(v) for v in prop["enum"]))
            out.append(f"{name}: " + ", ".join(parts))
        return out


def _base(spec: dict) -> str:
    servers = spec.get("servers") or []
    url = str(servers[0].get("url", "")) if servers and isinstance(servers[0], dict) else ""
    path = urlsplit(url).path if "://" in url else url
    return re.sub(r"\{[^}]*\}", "", path).rstrip("/")


def _openapi(app: str, spec: dict, ev: str, ref: str) -> AppModel:
    model = AppModel(
        app=app, evidence=[Evidence(id=ev, kind="openapi", ref=ref, complete=["routes"])]
    )
    schemas = _Schemas(spec)
    base = _base(spec)
    for raw_path, item in (spec.get("paths") or {}).items():
        item = schemas.resolve(item) or {}
        shared = item.get("parameters") or []
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = [schemas.resolve(p) for p in [*shared, *(op.get("parameters") or [])]]
            query = list(
                dict.fromkeys(str(p.get("name")) for p in params if p.get("in") == "query")
            )
            body: list[str] = []
            content = (schemas.resolve(op.get("requestBody")) or {}).get("content") or {}
            media = _pick(content)
            if media is not None:
                body = schemas.fields(content[media].get("schema", {}))
            responses = {}
            for status, resp in (op.get("responses") or {}).items():
                resp = schemas.resolve(resp) or {}
                rcontent = resp.get("content") or {}
                rmedia = _pick(rcontent)
                if rmedia is None:
                    responses[str(status)] = {}
                    continue
                s = schemas.shape(rcontent[rmedia].get("schema", {}))
                responses[str(status)] = s if isinstance(s, dict) else {"(value)": s}
            doc = op.get("summary") or op.get("description")
            model.routes.append(
                Route(
                    method=method.upper(),
                    path=(base + raw_path) or "/",
                    basis="declared",
                    **{"from": [ev]},
                    handler=op.get("operationId"),
                    doc=str(doc).strip() if doc else None,
                    query=query,
                    body=body,
                    responses=responses,
                )
            )
    return model


def _pick(content: dict) -> str | None:
    if not content:
        return None
    for media in content:
        if "json" in media:
            return media
    return next(iter(content))


# --- Postman ---------------------------------------------------------------------------------------

POSTMAN_VAR = re.compile(r"^\{\{.*\}\}$")


def _requests(items: list, out: list) -> list:
    for item in items or []:
        if isinstance(item, dict) and "item" in item:
            _requests(item["item"], out)
        elif isinstance(item, dict) and "request" in item:
            out.append(item)
    return out


def _postman_path(url: Any) -> tuple[str, list[str]]:
    if isinstance(url, str):
        raw = url
        parts = urlsplit(re.sub(r"^\{\{[^}]*\}\}", "", raw))
        segs = [s for s in parts.path.split("/") if s]
        query = [q.split("=", 1)[0] for q in parts.query.split("&") if q]
    else:
        url = url or {}
        segs = [str(s) for s in url.get("path") or [] if s]
        if not segs and url.get("raw"):
            return _postman_path(str(url["raw"]))
        query = [str(q.get("key")) for q in url.get("query") or [] if q.get("key")]
    out = []
    for seg in segs:
        if seg.startswith(":"):
            out.append("{" + seg[1:] + "}")
        elif POSTMAN_VAR.match(seg):
            out.append("{" + seg.strip("{}") + "}")
        else:
            out.append(seg)
    return "/" + "/".join(out), list(dict.fromkeys(query))


def _postman_body(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    mode = body.get("mode")
    if mode == "raw":
        try:
            data = json.loads(re.sub(r"\{\{[^}]*\}\}", "0", str(body.get("raw") or "")))
        except ValueError:
            return []
        s = shape(data)
        if isinstance(s, dict) and "[]" not in s:
            return [f"{k}: {type_of(v)}" for k, v in s.items()]
        return [f"(body): {type_of(s)}"]
    if mode in ("urlencoded", "formdata"):
        return [
            f"{p.get('key')}: {'file' if p.get('type') == 'file' else 'string'}"
            for p in body.get(mode) or []
            if p.get("key")
        ]
    return []


def _postman(app: str, data: dict, ev: str, ref: str) -> AppModel:
    model = AppModel(app=app, evidence=[Evidence(id=ev, kind="postman", ref=ref, complete=[])])
    for item in _requests(data.get("item") or [], []):
        req = item["request"]
        if isinstance(req, str):
            req = {"method": "GET", "url": req}
        method = str(req.get("method", "GET")).upper()
        if method.lower() not in METHODS:
            continue
        path, query = _postman_path(req.get("url"))
        responses: dict[str, dict] = {}
        for example in item.get("response") or []:
            status = str(example.get("code") or "")
            try:
                parsed = json.loads(example.get("body") or "")
            except ValueError:
                continue
            s = shape(parsed)
            s = s if isinstance(s, dict) else {"(value)": s}
            responses[status] = merge_shapes(responses[status], s) if status in responses else s
        have = next((r for r in model.routes if r.method == method and r.path == path), None)
        if have:
            have.query += [q for q in query if q not in have.query]
            have.responses.update(responses)
            continue
        doc = item.get("name") or req.get("description")
        model.routes.append(
            Route(
                method=method,
                path=path,
                basis="declared",
                **{"from": [ev]},
                doc=str(doc) if doc else None,
                query=query,
                body=_postman_body(req.get("body")),
                responses=responses,
            )
        )
    return model
