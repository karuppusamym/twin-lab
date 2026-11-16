"""E-03 (part): declared routes from OpenAPI 3.x and Postman v2.1 collections."""

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl import brief as brief_mod
from labctl import model_build as mb
from labctl import openapi
from labctl.evidence_cli import app as evidence_app
from labctl.models import Automation

FIXTURE = Path(__file__).parent / "fixtures" / "showcase-rec"
runner = CliRunner()

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Orders", "version": "1"},
    "servers": [{"url": "https://orders.example.test/api"}],
    "paths": {
        "/orders/{order_no}": {
            "parameters": [{"name": "order_no", "in": "path", "required": True}],
            "get": {
                "operationId": "getOrder",
                "summary": "One order",
                "parameters": [{"$ref": "#/components/parameters/Expand"}],
                "responses": {
                    "200": {"description": "ok", "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/Order"}}}},
                    "404": {"$ref": "#/components/responses/NotFound"},
                },
            },
            "put": {
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object", "required": ["status"],
                    "properties": {
                        "status": {"type": "string", "enum": ["OPEN", "SHIPPED"]},
                        "note": {"type": "string", "maxLength": 200},
                    }}}}},
                "responses": {"204": {"description": "saved"}},
            },
        },
        "/orders": {"get": {"parameters": [{"name": "status", "in": "query"}],
                            "responses": {"200": {"description": "ok", "content": {"application/json": {
                                "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Order"}}}}}}}},
    },
    "components": {
        "parameters": {"Expand": {"name": "expand", "in": "query"}},
        "responses": {"NotFound": {"description": "no", "content": {"application/json": {
            "schema": {"type": "object", "properties": {"code": {"type": "string"},
                                                        "message": {"type": "string"}}}}}}},
        "schemas": {
            "Order": {"allOf": [
                {"type": "object", "properties": {"order_no": {"type": "string"},
                                                  "placed_at": {"type": "string", "format": "date-time"}}},
                {"type": "object", "properties": {"lines": {"type": "array", "items": {
                    "type": "object", "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}}}}}},
            ]},
        },
    },
}  # fmt: skip

POSTMAN = {
    "info": {"name": "Orders", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
    "item": [
        {"name": "Orders", "item": [
            {"name": "Get order", "request": {"method": "GET", "url": {
                "raw": "{{baseUrl}}/api/orders/:order_no?expand=lines",
                "host": ["{{baseUrl}}"], "path": ["api", "orders", ":order_no"],
                "query": [{"key": "expand", "value": "lines"}]}},
             "response": [{"code": 200, "body": json.dumps({"order_no": "O-77123", "owner": "Hal Brennick", "total": 12.5})}]},
            {"name": "Update order", "request": {"method": "PUT", "url": "{{baseUrl}}/api/orders/{{orderNo}}",
                                                 "body": {"mode": "raw", "raw": "{\"status\": \"SHIPPED\", \"qty\": {{qty}}}"}}},
        ]},
        {"name": "Upload", "request": {"method": "POST", "url": "{{baseUrl}}/api/files",
                                       "body": {"mode": "formdata", "formdata": [{"key": "file", "type": "file"}, {"key": "label", "value": "x"}]}}},
    ],
}  # fmt: skip


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_openapi_gives_declared_routes_with_shapes_and_is_complete_for_routes(tmp_path, suffix):
    path = tmp_path / f"orders{suffix}"
    path.write_text(
        yaml.safe_dump(SPEC, sort_keys=False) if suffix == ".yaml" else json.dumps(SPEC),
        encoding="utf-8",
    )
    model = mb.from_openapi("orders", path)
    (ev,) = model.evidence
    assert ev.id == "openapi:orders" and ev.kind == "openapi" and ev.complete == ["routes"]
    assert [r.key for r in model.routes] == [
        "GET /api/orders/{order_no}", "PUT /api/orders/{order_no}", "GET /api/orders"]  # fmt: skip
    get = model.route("GET /api/orders/{order_no}")
    assert get.basis == "declared" and get.handler == "getOrder" and get.doc == "One order"
    assert get.query == ["expand"]
    assert get.responses["200"] == {"order_no": "string", "placed_at": "string(date-time)",
                                    "lines": {"[]": {"sku": "string", "qty": "integer"}}}  # fmt: skip
    assert get.responses["404"] == {"code": "string", "message": "string"}
    put = model.route("PUT /api/orders/{order_no}")
    assert put.body == [
        "status: string, one of OPEN, SHIPPED",
        "note: string, optional, maxLength=200",
    ]
    assert put.responses == {"204": {}}
    assert model.route("GET /api/orders").responses["200"]["[]"]["order_no"] == "string"


def test_postman_collection_gives_declared_routes_without_example_values(tmp_path):
    path = tmp_path / "orders.postman_collection.json"
    path.write_text(json.dumps(POSTMAN), encoding="utf-8")
    model = mb.from_openapi("orders", path)
    (ev,) = model.evidence
    assert ev.kind == "postman" and ev.complete == []
    assert [r.key for r in model.routes] == [
        "GET /api/orders/{order_no}", "PUT /api/orders/{orderNo}", "POST /api/files"]  # fmt: skip
    get = model.route("GET /api/orders/{order_no}")
    assert get.query == ["expand"] and get.doc == "Get order"
    assert get.responses == {"200": {"order_no": "string", "owner": "string", "total": "number"}}
    assert model.route("PUT /api/orders/{orderNo}").body == ["status: string", "qty: integer"]
    assert model.route("POST /api/files").body == ["file: file", "label: string"]
    text = yaml.safe_dump(model.model_dump(mode="json", by_alias=True))
    assert "Hal Brennick" not in text and "O-77123" not in text


def test_an_openapi_route_merges_with_what_a_bot_calls_and_flags_what_it_lacks(tmp_path):
    path = tmp_path / "orders.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    bot = Automation.model_validate({
        "id": "bot", "name": "bot", "type": "python", "surface": "backend", "source": {"path": "."},
        "touches": [{"app": "orders", "via": "api", "routes": [
            {"method": "PUT", "path": "/api/orders/{no}"}, {"method": "DELETE", "path": "/api/orders/{no}"}]}],
    })  # fmt: skip
    built = brief_mod.build(
        "orders", "Orders", automations=[bot], openapi=[path], repo_root=tmp_path
    )
    put = built.model.route("PUT /api/orders/{order_no}")
    assert put.from_ == ["openapi:orders", "automation:bot"] and put.body
    assert any(
        "DELETE /api/orders/{no} is called by bot but no backend route" in g for g in built.gaps
    )
    assert built.mode == "backend"


def test_unsupported_descriptions_are_refused(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"swagger": "2.0", "paths": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Swagger 2.0"):
        openapi.analyse("x", path)
    path.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="neither"):
        openapi.analyse("x", path)


def test_evidence_analyse_command_prints_the_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("LABCTL_ROOT", str(tmp_path))
    result = runner.invoke(evidence_app, ["analyse", "showcase", str(FIXTURE), "--pages"])
    assert result.exit_code == 0, result.output
    assert "evidence har:showcase-rec (har" in result.output
    assert "front end: react 18.3.1" in result.output
    assert "/cases/{id}" in result.output and "PUT /api/cases/{id}" in result.output
    assert "422 NOTE_REQUIRED" in result.output
    assert (tmp_path / "apps" / "showcase" / "pages" / "cases-item.html").exists()
    spec = tmp_path / "orders.yaml"
    spec.write_text(yaml.safe_dump(SPEC), encoding="utf-8")
    result = runner.invoke(
        evidence_app, ["analyse", "orders", str(spec), "--model", str(tmp_path / "m.yaml")]
    )
    assert result.exit_code == 0, result.output
    assert "GET /api/orders/{order_no}" in result.output and (tmp_path / "m.yaml").exists()
    assert runner.invoke(evidence_app, ["analyse", "x", str(tmp_path / "nope.har")]).exit_code == 2
