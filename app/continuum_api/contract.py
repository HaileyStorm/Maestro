"""Enrich FastAPI's contract without importing or duplicating model execution.

Authored operation contracts override the source-extracted inventory. Extraction
is a maintenance aid, never evidence of complete behavioral or device coverage.
Only explicitly reviewed operations can become MCP tools.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from fastapi.openapi.utils import get_openapi

DIRECTORY = Path(__file__).resolve().parent
METHODS = frozenset({"get", "post", "put", "patch", "delete", "head"})
ERRORS = {
    "400": "Invalid input. Correct the named field; do not change the submission identity.",
    "401": "Authentication required. Ask the owner to sign in through the existing account flow.",
    "403": "Access, origin, locality or recent confirmation is missing. Read access-context and project access.",
    "404": "Not found or not authorized in this session/project. Verify the returned identity and scope.",
    "409": "State or revision conflict. Read current state and resolve the conflict before mutation.",
    "422": "Invalid settings or unavailable model input. Inspect model options and the error detail.",
    "423": "Resource is locked. Preserve the operation and wait for its owner.",
    "503": "Temporarily unavailable. Observe an existing operation; never blindly resubmit a mutation.",
}


def _json_schema_nullable(value):
    """Translate authored OpenAPI 3.0 nullability to the current 3.1 dialect."""
    if isinstance(value, list):
        return [_json_schema_nullable(child) for child in value]
    if not isinstance(value, dict):
        return value
    result = {key: _json_schema_nullable(child) for key, child in value.items() if key != "nullable"}
    if value.get("nullable") is True and any(key in value for key in ("type", "$ref", "enum", "anyOf", "oneOf", "allOf")):
        return {"anyOf": [result, {"type": "null"}]}
    return result


def authored_operations() -> dict[tuple[str, str], dict]:
    result = {}
    for name in ("operations.json", "studio.json", "director.json", "queue.json", "admin.json", "public.json"):
        file = DIRECTORY / name
        if not file.exists():
            continue
        for item in json.loads(file.read_text(encoding="utf-8")):
            # Starlette converters disappear from the public OpenAPI path.
            path = re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", item["path"])
            key = (item["method"].lower(), path)
            if name != "operations.json":
                item.setdefault("x-maestro", {}).setdefault("contract_status", "authored")
            previous = result.get(key, {})
            result[key] = {**previous, **item}
    return result


def enrich_openapi(schema: dict) -> dict:
    schema = copy.deepcopy(schema)
    contracts = authored_operations()
    # Reuse the authored save shape for reads; timestamps are seconds, not dates.
    editor_save = contracts[("put", "/api/v1/projects/{project}/editor/projects/{editor_id}")]
    timeline = copy.deepcopy(editor_save["responses"]["200"]["content"]["application/json"]
                             ["schema"]["properties"]["project"])
    timeline["properties"].update({
        "schema_version": {"type": "integer", "description": "Saved Editor format version; currently 5."},
        "created_at": {"type": "number", "description": "Unix timestamp in seconds, including fractional seconds."},
        "updated_at": {"type": "number", "description": "Unix timestamp in seconds; reads preserve the saved value."},
        "markers": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "export": {"type": "object", "additionalProperties": True,
                   "description": "Saved export settings; use the export operation contract for supported options."},
        "exports": {"type": "array", "maxItems": 50, "items": {"type": "object", "additionalProperties": True},
                    "description": "Retained export history, newest first. Output access is reauthorized separately."},
        "opening_source": {"type": "object", "additionalProperties": True,
                           "description": "Retained source identity; never replace it with an arbitrary path."},
    })
    timeline["properties"]["revision"]["minimum"] = 0
    timeline["required"] = ["schema_version", "id", "name", "workspace", "revision", "created_at", "updated_at",
                            "canvas", "assets", "tracks", "markers", "export", "exports"]
    schema.setdefault("components", {}).setdefault("schemas", {})["EditorTimeline"] = timeline
    schema["components"]["schemas"]["DirectorVideoLoras"] = {
        "type": "object", "additionalProperties": True,
        "description": (
            "Director's public video LoRA selection. Read /api/v1/loras/{video_model}/details first. "
            "Parameter filenames must also be active; copy the current schema digest and obey its "
            "types, required values and defaults. Generation scope with empty roles is required. "
            "Admission freezes the selection; continue cannot replace it. Native qualification "
            "depends on the model and scheduler, independently of schema availability."
        ),
        "properties": {
            "activated_loras": {"type": "array", "maxItems": 64, "uniqueItems": True,
                "items": {"type": "string"}, "default": [],
                "description": "Exact filenames from this video model's active LoRA catalog."},
            "loras_multipliers": {"type": "string", "default": "",
                "description": "Complete native strength schedule in catalog order; empty uses native defaults. "
                    "Spaces separate adapters, commas schedule steps and semicolons separate supported phases. "
                    "The consuming model validates its supported phases."},
            "lora_parameters": {"type": "object", "maxProperties": 64, "default": {},
                "description": "At most 65536 UTF-8 bytes after canonical serialization. Keys are active "
                    "catalog basenames, at most 512 UTF-8 bytes, without slash, backslash or NUL; '.' and '..' are invalid. "
                    "A server-known parameter contract must be supplied even when using its defaults.",
                "additionalProperties": {
                    "type": "object", "additionalProperties": False,
                    "required": ["parameter_schema_digest", "parameter_values"],
                    "properties": {
                        "parameter_schema_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$",
                            "description": "Exact schema_digest returned by LoRA details, never a client-generated hash."},
                        "parameter_values": {"type": "object", "maxProperties": 64,
                            "description": "IDs match [A-Za-z][A-Za-z0-9._:-]{0,63}; values must match the discovered schema. "
                                "Strings are bounded to 500 UTF-8 bytes. Integers must be safe JavaScript integers; "
                                "non-integer numbers must be finite with absolute value at most 1e12. No null or nested values.",
                            "additionalProperties": {"anyOf": [
                                {"type": "boolean"},
                                {"type": "integer", "minimum": -9007199254740991, "maximum": 9007199254740991},
                                {"type": "number", "minimum": -1000000000000, "maximum": 1000000000000},
                                {"type": "string", "maxLength": 500},
                            ]}},
                    },
                }},
        },
    }
    schema["info"].update(
        title="Maestro Continuum HTTP API",
        version="1.0.0",
        description=("Continuum's production HTTP API. Model-specific settings remain extensible. "
                     "Discover current access, model options, readiness and terms before submission. "
                     "A timeout does not cancel work. See /api/v1/api/discovery and the API guide."),
    )
    schema["servers"] = [{"url": "/", "description": "This authorized Maestro origin; use the current configured URL."}]
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update({
        "MaestroSession": {"type": "apiKey", "in": "cookie", "name": "maestro_session",
                           "description": "Existing signed session cookie, issued by Maestro; never an API-key bypass."},
        "MaestroAccount": {"type": "apiKey", "in": "cookie", "name": "maestro_account_session",
                           "description": "Existing account session when account enforcement is enabled. Project access and recent reauthentication still apply."},
    })
    schema["security"] = [{"MaestroSession": []}]
    schema["x-maestro-authentication"] = {
        "account_session": "Additionally required when account enforcement is active; discover /api/v1/account/context.",
        "session_bootstrap": "The server issues a signed local compatibility session on first access where policy permits it.",
        "project_scope": "Account membership or an existing project unlock remains required per operation.",
        "origin": "Exact app Origin/Referer for mutations while sharing is enabled; account changes always require exact Origin.",
    }
    for path, methods in schema.get("paths", {}).items():
        if not path.startswith("/api/v1/"):
            continue
        for method, operation in methods.items():
            if method not in METHODS:
                continue
            item = contracts.get((method, path))
            if item:
                for key, value in item.items():
                    if key not in {"method", "path", "parameters", "responses"}:
                        operation[key] = copy.deepcopy(value)
                if "parameters" in item:
                    original = {(p["in"], p["name"]): p for p in operation.get("parameters", [])}
                    original.update({(p["in"], p["name"]): p for p in item["parameters"]})
                    operation["parameters"] = list(original.values())
                operation.setdefault("responses", {}).update(copy.deepcopy(item.get("responses", {})))
            if path.startswith("/api/v1/director/"):
                def bind_video_loras(value):
                    if isinstance(value, dict):
                        properties = value.get("properties")
                        if isinstance(properties, dict) and "video_loras" in properties:
                            properties["video_loras"] = {"$ref": "#/components/schemas/DirectorVideoLoras"}
                        for nested in value.values():
                            bind_video_loras(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            bind_video_loras(nested)
                bind_video_loras(operation.get("requestBody", {}))
            metadata = operation.setdefault("x-maestro", {})
            metadata.setdefault("mcp", False)
            metadata.setdefault("contract_status", "source_extracted" if item else "framework_only")
            metadata.setdefault("boundary", "Existing handler authorization and locality apply. This operation has not been approved as an MCP tool.")
            metadata.setdefault("idempotency", "No general replay guarantee. Reconcile existing state before another mutation.")
            operation.setdefault("description", operation.get("summary", "Continuum operation"))
            operation.setdefault("tags", [str(metadata.get("feature", "administration")).split(".")[0]])
            for status, description in ERRORS.items():
                operation.setdefault("responses", {}).setdefault(status, {
                    "description": description,
                    "content": {"application/json": {"schema": {"type": "object", "properties": {
                        "detail": {"description": "Actionable handler-specific error; may be text, object or validation list."}
                    }, "required": ["detail"], "additionalProperties": True}}},
                })
            if method not in {"get", "head"}:
                parameters = operation.setdefault("parameters", [])
                if any(p.get("name", "").lower() == "origin" and p.get("in") == "header" for p in parameters):
                    continue
                parameters.append({
                    "name": "Origin", "in": "header", "required": False,
                    "description": "Exact configured app origin. Required while sharing is enabled; account mutations always require exact origin. Not a permission grant.",
                    "schema": {"type": "string", "format": "uri"},
                })
    schema["x-maestro-contract"] = {
        "guide": "docs/api/README.md", "coverage": "docs/api/coverage.md",
        "discovery": "/api/v1/api/discovery", "full_parity_verified": False,
        "contract_status_meaning": {
            "authored": "Actual request/result and action boundary reviewed against the production handler.",
            "source_extracted": "Source-derived inventory; further authored semantic review remains.",
            "framework_only": "Framework schema only; not a complete raw-body contract.",
        },
    }
    return _json_schema_nullable(schema)


def install_contract(api) -> None:
    """Call after all production routes are registered, before serving the SPA."""
    def openapi():
        if api.openapi_schema is None:
            api.openapi_schema = enrich_openapi(get_openapi(
                title=api.title, version=api.version, routes=api.routes,
                description=api.description,
            ))
        return api.openapi_schema
    api.openapi_schema = None
    api.openapi = openapi


def public_operation_index(schema: dict) -> list[dict]:
    return [
        {"operation_id": op["operationId"], "method": method.upper(), "path": path,
         "summary": op.get("summary", ""), **op.get("x-maestro", {})}
        for path, methods in schema.get("paths", {}).items()
        if path.startswith("/api/v1/")
        for method, op in methods.items() if method in METHODS
    ]
