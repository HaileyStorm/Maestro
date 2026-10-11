"""Maintain the source inventory and client reference from the public contract.

No application imports, model loads, tests or network calls. Authored contracts
remain separate from the source-derived inventory so regeneration cannot erase
reviewed constraints. Export the running server's /openapi.json before --schema.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def literal_schema(value):
    if isinstance(value, bool):
        return {"type": "boolean", "default": value}
    if isinstance(value, str):
        return {"type": "string", "default": value}
    if isinstance(value, int):
        return {"type": "integer", "default": value}
    if isinstance(value, float):
        return {"type": "number", "default": value}
    if isinstance(value, list):
        return {"type": "array", "items": {}, "default": value}
    if isinstance(value, dict):
        return {"type": "object", "additionalProperties": True, "default": value}
    return {}


def inventory():
    source = ROOT / "app/launch.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "api"
                    and decorator.func.attr in {"get", "post", "put", "patch", "delete", "head"}
                    and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                continue
            path = decorator.args[0].value
            if not isinstance(path, str) or not path.startswith("/api/v1/"):
                continue
            fields, raw_body = {}, False
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if child.func.attr == "json" and isinstance(child.func.value, ast.Name) and child.func.value.id == "request":
                        raw_body = True
                    if (isinstance(child.func.value, ast.Name) and child.func.value.id == "body"
                            and child.func.attr in {"get", "pop"} and child.args
                            and isinstance(child.args[0], ast.Constant) and isinstance(child.args[0].value, str)):
                        name = child.args[0].value
                        if name.startswith("_"):
                            continue  # Never promote server authority to an input contract.
                        field = {}
                        if len(child.args) > 1:
                            try:
                                field = literal_schema(ast.literal_eval(child.args[1]))
                            except (ValueError, TypeError):
                                pass
                        fields.setdefault(name, field)
            operation = {"method": decorator.func.attr.upper(), "path": path,
                "description": ast.get_docstring(node) or node.name.replace("_", " "),
                "x-maestro": {"handler": node.name, "source": "app/launch.py",
                    "contract_status": "source_extracted", "mcp": False,
                    "boundary": "Production access/locality guards remain authoritative. Semantic contract review remains pending for this extracted entry.",
                    "idempotency": "No inferred replay guarantee. Read operation-specific authored contract before retrying."}}
            if raw_body:
                operation["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": {"type": "object", "properties": fields, "additionalProperties": True,
                        "description": "Source inventory of directly read fields; nested shapes, conditional requirements and constraints are defined by authored overrides."}}}}
            result.append(operation)
    file = ROOT / "app/continuum_api/operations.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated source inventory ({len(result)} operations; this is not a parity verdict)")


def client_reference(schema_path: Path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    operations = []
    for path, methods in schema["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete", "head"}:
                continue
            operations.append({"operation_id": operation["operationId"], "method": method.upper(), "path": path,
                "summary": operation.get("summary", ""), "parameters": operation.get("parameters", []),
                "request_body": operation.get("requestBody"), "responses": operation.get("responses", {}),
                "metadata": operation.get("x-maestro", {})})
    # Existing client call() consumes these exact IDs and shapes from the live
    # contract. This maintained reference is useful offline, never a runtime
    # replacement for current capability/access discovery.
    lines = ["<!-- Generated by scripts/generate_continuum_clients.py; edit authored contracts instead. -->",
             "# Operation reference", "", "See [API setup and workflows](README.md). Current server discovery controls availability.", "",
             "| Operation ID | HTTP operation | Contract | MCP |", "|---|---|---|---|"]
    for op in operations:
        meta = op["metadata"]
        lines.append(f"| `{op['operation_id']}` | `{op['method']} {op['path']}` | {meta.get('contract_status', 'framework_only')} | {meta.get('mcp') is True} |")
    (ROOT / "docs/api/operations.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "sdk/python/maestro_continuum/operations.json").write_text(json.dumps(operations, indent=2) + "\n", encoding="utf-8")
    ids = " | ".join(json.dumps(op["operation_id"]) for op in operations) or "never"
    (ROOT / "sdk/typescript/src/operations.ts").write_text(
        "// Generated from the authoritative exported OpenAPI contract.\n"
        + "export type OperationId = " + ids + ";\n"
        + "export type OperationReference = { operation_id: OperationId; method: string; path: string; summary: string; parameters: unknown[]; request_body: unknown; responses: Record<string, unknown>; metadata: Record<string, unknown> };\n"
        + "export const operationReference: readonly OperationReference[] = " + json.dumps(operations, indent=2) + ";\n", encoding="utf-8")
    print("Generated Python/TypeScript operation references and offline operation documentation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--schema", type=Path)
    arguments = parser.parse_args()
    if arguments.inventory:
        inventory()
    if arguments.schema:
        client_reference(arguments.schema)
    if not arguments.inventory and not arguments.schema:
        parser.error("Use --inventory or --schema PATH")
