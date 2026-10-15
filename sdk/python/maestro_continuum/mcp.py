"""Stdio MCP adapter over the current public HTTP contract. No execution backdoor."""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import copy
import hashlib
import json
import os
from urllib.parse import quote

from .client import MaestroClient, MaestroError


def tool_input(operation: dict, schema: dict) -> dict:
    properties, required = {}, []
    for location in ("path", "query"):
        parameters = [p for p in operation.get("parameters", []) if p.get("in") == location]
        if parameters:
            fields = {p["name"]: {**p.get("schema", {}), "description": p.get("description", "")} for p in parameters}
            needed = [p["name"] for p in parameters if p.get("required")]
            properties[location] = {"type": "object", "properties": fields, "required": needed, "additionalProperties": False}
            if needed:
                required.append(location)
    content = operation.get("requestBody", {}).get("content", {})
    if "application/json" in content:
        properties["body"] = copy.deepcopy(content["application/json"].get("schema", {"type": "object"}))
        if operation["requestBody"].get("required"):
            required.append("body")
    if "multipart/form-data" in content:
        form = copy.deepcopy(content["multipart/form-data"].get("schema", {}))
        if "$ref" in form:
            form = copy.deepcopy(schema["components"]["schemas"][form["$ref"].rsplit("/", 1)[1]])
        file_fields, form_fields = {}, {}
        for name, field in form.get("properties", {}).items():
            if field.get("format") == "binary":
                file_fields[name] = {"type": "object", "properties": {
                    "filename": {"type": "string", "pattern": r"^[^/\\\x00-\x1f]{1,200}$"},
                    "media_type": {"type": "string", "default": "application/octet-stream", "maxLength": 100},
                    "data_base64": {"type": "string", "contentEncoding": "base64", "maxLength": 22369624},
                }, "required": ["filename", "data_base64"], "additionalProperties": False}
            else:
                form_fields[name] = field
        needed = set(form.get("required", []))
        for group, fields in (("files", file_fields), ("body", form_fields)):
            if fields:
                properties[group] = {"type": "object", "properties": fields,
                    "required": sorted(needed.intersection(fields)), "additionalProperties": False}
                if needed.intersection(fields):
                    required.append(group)
    result = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    # OpenAPI component refs use a different base from standalone JSON Schema.
    definitions = {}
    components = schema.get("components", {}).get("schemas", {})
    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                if reference.startswith("#/components/schemas/"):
                    name = reference[len("#/components/schemas/"):]
                    value["$ref"] = "#/$defs/" + name
                    if name not in definitions:
                        definitions[name] = copy.deepcopy(components[name])
                        refs(definitions[name])
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(result)
    if definitions:
        result["$defs"] = definitions
    return result


def tool_output(operation: dict, schema: dict) -> dict:
    shapes = []
    for status, response in operation.get("responses", {}).items():
        if not str(status).startswith("2"):
            continue
        for media_type, media in response.get("content", {}).items():
            if "json" in media_type:
                shapes.append(media.get("schema", {}))
            elif media_type.startswith("text/"):
                shapes.append({"type": "string"})
            else:
                shapes.append({"type": "object", "properties": {
                    "media_url": {"type": "string", "format": "uri"},
                    "requires_same_authorized_session": {"const": True},
                    "content_type": {"type": "string"},
                    "content_length": {"type": ["string", "null"]},
                }, "required": ["media_url", "requires_same_authorized_session"]})
    # Reuse component resolution; response logic remains in the HTTP handler.
    projected = tool_input({"requestBody": {"content": {"application/json": {
        "schema": {"anyOf": shapes} if shapes else {}}}}}, schema)
    result = {"type": "object", "properties": {
        "result": projected["properties"]["body"],
        "error": {"type": "object", "properties": {
            "status": {"type": ["integer", "null"]}, "detail": {},
            "operation": {"type": "string"}, "uncertain": {"type": "boolean"},
            "next_action": {"type": "string"},
        }, "required": ["detail", "uncertain", "next_action"], "additionalProperties": True},
    }, "additionalProperties": False, "oneOf": [{"required": ["result"]}, {"required": ["error"]}]}
    if "$defs" in projected:
        result["$defs"] = projected["$defs"]
    return result


async def serve(client: MaestroClient, *, read_only: bool = False, features: tuple[str, ...] = ()):
    from jsonschema import Draft202012Validator, ValidationError
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    schema = await asyncio.to_thread(client.contract)
    all_operations = client.operations(mcp_only=True)
    operations = all_operations
    # Multipart uses bounded base64 bytes, never local paths or acquisition URLs.
    operations = {name: op for name, op in operations.items()
                  if (not read_only or op["method"] == "GET")
                  and (not features or any(str(op.get("x-maestro", {}).get("feature", "")) == feature
                       or str(op.get("x-maestro", {}).get("feature", "")).startswith(feature + ".") for feature in features))
                  and "application/octet-stream" not in op.get("requestBody", {}).get("content", {})}
    tools, bindings = {}, {}
    for name, operation in operations.items():
        meta = operation.get("x-maestro", {})
        readonly = operation["method"] == "GET"
        tool_name = name if len(name) <= 64 else name[:51] + "_" + hashlib.sha256(name.encode()).hexdigest()[:12]
        bindings[tool_name] = name
        tools[tool_name] = types.Tool(
            name=tool_name, description=(operation.get("description") or operation.get("summary", name))
            + "\nBoundary: " + meta.get("boundary", "Production authorization applies.")
            + "\nReplay: " + meta.get("idempotency", "No replay guarantee."),
            inputSchema=tool_input(operation, schema),
            outputSchema=tool_output(operation, schema),
            annotations=types.ToolAnnotations(readOnlyHint=readonly,
                destructiveHint=False if readonly else meta.get("destructive", True),
                idempotentHint=readonly or meta.get("idempotent", False),
                openWorldHint=meta.get("open_world", not readonly)),
        )
    transfer = "maestro_gallery_to_upload"
    if (not read_only and (not features or any(f in {"gallery", "studio", "h3"} for f in features))
            and any(op["method"] == "GET" and op["path"] == "/api/v1/file/{filename}" for op in all_operations.values())
            and any(op["method"] == "POST" and op["path"] == "/api/v1/upload" for op in all_operations.values())):
        tools[transfer] = types.Tool(name=transfer,
            description="Pin an authorized Gallery media revision into a new private session-owned upload using only the existing HTTP file/upload operations. Returns its filename for generation in the same project; does not submit generation. Maximum 16 MiB. No paths, URLs or credentials. Not idempotent: after an uncertain upload, reconcile or request owner resolution; never automatically retry. Larger media requires the SDK download/upload workflow.",
            inputSchema={"type": "object", "properties": {
                "workspace": {"type": "string", "minLength": 1, "maxLength": 200},
                "filename": {"type": "string", "pattern": r"^[^/\\\x00-\x1f]{1,200}$", "description": "Direct-child Gallery video, image or audio filename; no sidecars or paths."},
                "revision": {"type": "string", "minLength": 1, "maxLength": 128},
                "revision_kind": {"type": "string", "enum": ["listing_revision", "content_revision"], "default": "listing_revision"},
            }, "required": ["workspace", "filename", "revision"], "additionalProperties": False},
            outputSchema={"type": "object", "properties": {"result": {"type": "object", "properties": {
                "source": {"type": "object"}, "upload": {"type": "object"}, "bytes": {"type": "integer", "maximum": 16777216},
                "generation_submitted": {"const": False}}, "required": ["source", "upload", "bytes", "generation_submitted"]},
                "error": {"type": "object"}}, "oneOf": [{"required": ["result"]}, {"required": ["error"]}], "additionalProperties": False},
            annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    voice_prepare = "maestro_voice_reference_prepare"
    if (not read_only and (not features or any(f in {"references", "projects", "studio", "models"} for f in features))
            and any(op["method"] == "GET" and op["path"] == "/api/v1/projects/{project}/assets" for op in all_operations.values())
            and any(op["method"] == "POST" and op["path"] == "/api/v1/upload-audio" for op in all_operations.values())):
        tools[voice_prepare] = types.Tool(name=voice_prepare,
            description="Reuse one kept voice-reference variant in an authorized project. Reads its saved WAV/FLAC and transcript and creates a private session upload, at most16MiB. Returns upload.filename for audio_guide. No generation, training, filesystem paths, credentials, terms acceptance or GPU authority. QwenBase uses transcript as alt_prompt; Drama uses custom_settings.drama3_reference_text. Not idempotent; reconcile an uncertain upload without automatic retry.",
            inputSchema={"type": "object", "properties": {k: {"type": "string", "minLength": 1, "maxLength": 200} for k in ("project", "asset_id", "variant_id")},
                         "required": ["project", "asset_id", "variant_id"], "additionalProperties": False},
            outputSchema={"type": "object", "properties": {"result": {"type": "object", "properties": {
                "source": {"type": "object"}, "upload": {"type": "object"}, "transcript": {"type": "string"},
                "bytes": {"type": "integer", "maximum": 16777216}, "generation_submitted": {"const": False}},
                "required": ["source", "upload", "transcript", "bytes", "generation_submitted"]}, "error": {"type": "object"}},
                "oneOf": [{"required": ["result"]}, {"required": ["error"]}], "additionalProperties": False},
            annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    server = Server("maestro-continuum")

    @server.list_tools()
    async def list_tools():
        return list(tools.values())

    async def call_tool(request):
        name = request.params.name
        args = request.params.arguments or {}
        dispatched = False
        try:
            if name not in tools:
                raise ValueError("Unknown or unavailable tool; discover this server's current tools")
            Draft202012Validator(tools[name].inputSchema).validate(args)
            args = dict(args)
            if name == voice_prepare:
                dispatched = True
                result = await asyncio.to_thread(client.prepare_voice_reference, **args)
                structured = {"result": result}
                return types.ServerResult(types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(structured))], structuredContent=structured, isError=False))
            if name == transfer:
                if args["filename"].rsplit(".", 1)[-1].lower() not in {"mp4", "mov", "mkv", "avi", "webm", "m4v", "png", "jpg", "jpeg", "webp", "bmp", "gif", "wav", "mp3", "flac", "ogg", "m4a", "aac"}:
                    raise ValueError("Only Gallery media is supported")
                kind = args.get("revision_kind", "listing_revision")
                content = await asyncio.to_thread(client.request, "GET",
                    "/api/v1/file/" + quote(args["filename"], safe=""),
                    query={"workspace": args["workspace"], kind: args["revision"]},
                    maximum_response_bytes=16 * 1024 * 1024)
                if not isinstance(content, bytes) or not content:
                    raise ValueError("Expected retained media bytes")
                dispatched = True
                upload = await asyncio.to_thread(client.request, "POST", "/api/v1/upload",
                    query={"private": True}, files={"file": (args["filename"], content, "application/octet-stream")})
                result = {"source": {"workspace": args["workspace"], "filename": args["filename"], kind: args["revision"]},
                    "upload": {k:v for k,v in upload.items() if k != "path"}, "bytes": len(content), "generation_submitted": False}
                structured, error = {"result": result}, False
                return types.ServerResult(types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(structured))], structuredContent=structured, isError=error))
            operation_id = bindings[name]
            if "files" in args:
                files = {}
                total = 0
                for field, upload in args["files"].items():
                    data = base64.b64decode(upload["data_base64"], validate=True)
                    total += len(data)
                    if total > 16 * 1024 * 1024 or upload["filename"] in {".", ".."}:
                        raise ValueError("Upload exceeds adapter limit or has an invalid name")
                    files[field] = (upload["filename"], data, upload.get("media_type", "application/octet-stream"))
                args["files"] = files
            dispatched = True
            result = await asyncio.to_thread(client.call, operation_id, **args, media_reference=True)
            structured, error = {"result": result}, False
        except MaestroError as exc:
            structured, error = {"error": exc.as_dict()}, True
        except (ValueError, ValidationError, binascii.Error):
            structured, error = {"error": {"detail": "Inputs do not match this operation's public schema",
                "uncertain": dispatched and operations.get(bindings.get(name), {}).get("method") != "GET",
                "next_action": "Reconcile the same identity before retrying." if dispatched else
                    "Read tools/list and correct fields. No mutation was dispatched."}}, True
        except Exception:
            structured, error = {"error": {"detail": "Operation observation failed",
                "uncertain": dispatched and operations.get(bindings.get(name), {}).get("method") != "GET",
                "next_action": "Reconcile the same operation identity; never blindly resubmit a mutation."}}, True
        return types.ServerResult(types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))],
            structuredContent=structured, isError=error,
        ))

    server.request_handlers[types.CallToolRequest] = call_tool
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("MAESTRO_URL"))
    parser.add_argument("--origin", default=os.environ.get("MAESTRO_ORIGIN"))
    parser.add_argument("--cookie-file", default=os.environ.get("MAESTRO_COOKIE_FILE"))
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--feature", action="append", default=[], help="Limit tools to a feature family; repeat for multiple families")
    args = parser.parse_args()
    if not args.url:
        parser.error("Set --url or MAESTRO_URL to the current configured Maestro URL")
    with MaestroClient(args.url, origin=args.origin, cookie_file=args.cookie_file) as client:
        asyncio.run(serve(client, read_only=args.read_only, features=tuple(args.feature)))


if __name__ == "__main__":
    main()
