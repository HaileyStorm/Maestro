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
PREPARED_VOICE_FAMILIES = ["drama3", "voxcpm2", "qwen3_tts_base", "kugelaudio_0_open", "chatterbox"]


def _prepared_voice_operations():
    path = "/api/v1/projects/{project}/assets/{asset_id}/variants/{variant_id}/prepared-voices"
    parameters = [{"name": name, "in": "path", "required": True,
                   "schema": {"type": "string", "minLength": 1}}
                  for name in ("project", "asset_id", "variant_id")]
    parameters.append({"name": "family", "in": "query", "required": False,
        "description": "Limit matching prepared features to this model family; omit for all supported families.",
        "schema": {"type": "string", "enum": PREPARED_VOICE_FAMILIES}})
    result = {}
    for method, suffix, name, summary, description in (
        ("get", "", "prepared_voice_status", "Inspect prepared voice features",
         "Read matching cache entries and their current CPU-feature readiness without loading features into memory."),
        ("post", "/load", "prepared_voice_load", "Load prepared voice features into CPU memory",
         "Preload valid existing disk features into bounded CPU memory. Missing features are first prepared "
         "by ordinary authorized generation; this operation does not create them."),
        ("delete", "", "prepared_voice_evict", "Evict prepared voice features",
         "Remove matching prepared features from disk and CPU memory. Original recordings, Reference cards "
         "and model files are preserved. Content-identical references can share the evicted features."),
    ):
        readonly = method == "get"
        result[(method, path + suffix)] = {
            "operationId": name, "summary": summary,
            "description": description + " Select a project-authorized voice asset and one kept voice_reference "
                "variant containing exactly one WAV or FLAC recording. No request body, model load, GPU "
                "dispatch or generation submission. Readiness is CPU features only, not model/GPU readiness "
                "or voice quality. Follow next_action when no valid features exist.",
            "parameters": copy.deepcopy(parameters),
            "responses": {"200": {"description": "Current matching prepared voice cache state.",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PreparedVoiceState"}}}}},
            "x-maestro": {
                "feature": "references.voices.prepared", "operation_kind": "read" if readonly else "mutation",
                "permissions": ["project.read" if readonly else "project.mutate"], "mcp": True,
                "destructive": method == "delete", "idempotent": True, "open_world": False,
                "contract_status": "authored",
                "boundary": "Existing session, project authorization, source-media privacy and mutation-origin "
                    "guards apply. Only content-bound prepared cache features are accessible; no arbitrary paths, "
                    "tensor payloads, credentials or native execution authority are exposed.",
                "idempotency": "Read-only." if readonly else
                    "Idempotent cache operation; state may change with generation or eviction. "
                    "Clients do not automatically retry; inspect current state after uncertainty.",
                "evidence": "Authored source contract. Live endpoint and native cache validation remain separate.",
            },
        }
    return result


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
    result.update(_prepared_voice_operations())
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
    schema["components"]["schemas"]["PreparedVoiceEntry"] = {
        "type": "object", "additionalProperties": False,
        "required": ["key", "family", "bytes", "valid", "disk_ready", "memory_ready"],
        "properties": {
            "key": {"type": "string", "pattern": "^[0-9a-f]{64}$",
                    "description": "Opaque content-bound cache key; not a path or permission grant."},
            "family": {"type": "string", "enum": PREPARED_VOICE_FAMILIES},
            "bytes": {"type": "integer", "minimum": 0},
            "valid": {"type": "boolean", "description": "Valid on disk or in CPU memory."},
            "disk_ready": {"type": "boolean"}, "memory_ready": {"type": "boolean"},
        },
    }
    schema["components"]["schemas"]["PreparedVoiceState"] = {
        "type": "object", "additionalProperties": False,
        "required": ["asset_id", "variant_id", "entries", "matched", "valid", "memory_ready", "evicted",
                     "failures", "readiness_scope", "generation_submitted", "next_action"],
        "properties": {
            "asset_id": {"type": "string"}, "variant_id": {"type": "string"},
            "entries": {"type": "array", "items": {"$ref": "#/components/schemas/PreparedVoiceEntry"}},
            **{name: {"type": "integer", "minimum": 0} for name in
               ("matched", "valid", "memory_ready", "evicted", "failures")},
            "readiness_scope": {"type": "string", "const": "cpu_features"},
            "generation_submitted": {"type": "boolean", "const": False},
            "next_action": {"type": "string", "description": "Current next step, including ordinary "
                            "authorized generation when prepared features do not exist."},
        },
    }
    schema["components"]["schemas"]["Drama3CustomSettings"] = {
        "type": "object", "additionalProperties": True,
        "description": "Drama 3 optional settings. References use authorized uploads and require actual transcripts. "
            "The GPU budget covers weights and worst-case expert staging; caches and CUDA overhead are additional.",
        "properties": {
            "drama3_gpu_budget_gib": {"type": "number", "minimum": 8, "maximum": 26, "default": 22},
            "drama3_separate_turns": {"type": "integer", "enum": [0, 1], "default": 0,
                "description": "1 preserves each explicitly tagged speaker turn as a separate generation chunk, including repeated speakers, with native audio history. Experimental mitigation for skipped turns; not a word-completeness guarantee. 0 retains Fish's native batching."},
            "drama3_reference_cache": {"type": "integer", "enum": [0, 1], "default": 1,
                "description": "1 automatically reuses content-bound prepared reference codes in bounded CPU "
                    "memory and private disk storage. Explicit 0 opts out for this generation. "
                    "This does not cache model weights or establish GPU readiness."},
            **{("drama3_reference_text" + (str(i) if i > 1 else "")): {
                "type": "string", "default": "", "description":
                    f"Actual transcript of audio_guide{str(i) if i > 1 else ''} for zero-based Speaker {i-1}; "
                    "required when that reference is present, at most 16000 UTF-8 bytes."}
               for i in range(1, 4)},
        },
    }
    schema["components"]["schemas"]["Drama3GenerationSettings"] = {
        "type": "object", "additionalProperties": True, "required": ["model_type", "prompt", "workspace"],
        "description": "Model-specific projection of the existing /api/v1/generate request. Start from discovered "
            "defaults. Experimental local NVIDIA FP8/CPU expert offload; no cross-request history, Q6 or LoRA "
            "support. Duration caps output and can truncate speech. Terms and GPU authority remain required.",
        "properties": {
            "model_type": {"type": "string", "const": "drama3"},
            "workspace": {"type": "string"}, "prompt": {"type": "string", "minLength": 1,
                "description": "Speech with inline [delivery] cues and <|speaker:0|> through <|speaker:15|>, "
                    "or Maestro Speaker 1: through Speaker 16: lines."},
            "alt_prompt": {"type": "string", "default": "", "description": "Performance cue applied to every "
                "speaker in this job; adherence depends on the model."},
            "duration_seconds": {"type": "number", "minimum": 1, "maximum": 300, "default": 20},
            "temperature": {"type": "number", "exclusiveMinimum": 0, "maximum": 2, "default": 0.7},
            "top_p": {"type": "number", "exclusiveMinimum": 0, "maximum": 1, "default": 0.7},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 30},
            "audio_prompt_type": {"type": "string", "enum": ["", "A", "AB", "ABC"], "default": ""},
            **{("audio_guide" + (str(i) if i > 1 else "")): {"type": "string", "description":
                "Project-authorized upload filename; maximum 60 seconds, with its corresponding transcript. "
                "Never an arbitrary filesystem path."} for i in range(1, 4)},
            "custom_settings": {"$ref": "#/components/schemas/Drama3CustomSettings"},
        },
    }
    for model, name, steps in (("voxcpm2", "VoxCPM2GenerationSettings", 30),
                               ("index_tts2", "IndexTTS2GenerationSettings", 25),
                               ("kugelaudio_0_open", "KugelAudioGenerationSettings", 20),
                               ("chatterbox", "ChatterboxGenerationSettings", 10)):
        properties = {
            "model_type": {"type": "string", "const": model},
            "workspace": {"type": "string"}, "prompt": {"type": "string", "minLength": 1},
            "num_inference_steps": {"type": "integer", "minimum": 0, "maximum": 100, "default": steps,
                "description": f"Iterative decoder steps, 1–100; legacy 0 uses {steps}. More steps cost time and do not guarantee better quality."},
            "audio_guide": {"type": "string", "description": "Project-authorized uploaded voice reference filename. Required for IndexTTS2."},
            "alt_prompt": {"type": "string", "default": "", "description": "Voice/emotion direction; adherence is model-dependent."},
        }
        if model == "voxcpm2":
            properties.update({
                "guidance_scale": {"type": "number", "exclusiveMinimum": 0, "maximum": 20, "default": 2},
                "custom_settings": {"type": "object", "additionalProperties": True, "properties": {
                    "voxcpm_max_len": {"type": "integer", "minimum": 2, "maximum": 4096, "default": 4096},
                    "voxcpm_retry_badcase": {"type": "integer", "enum": [0, 1], "default": 1,
                        "description": "0 disables native automatic bad-case retries; 1 enables them."},
                }},
            })
        if model == "chatterbox":
            properties["alt_prompt"]["description"] = "Unused by Chatterbox; use custom_settings.exaggeration and pace."
            properties["custom_settings"] = {"type": "object", "additionalProperties": True, "properties": {
                "exaggeration": {"type": "number", "minimum": 0.25, "maximum": 2, "default": 0.5},
                "pace": {"type": "number", "minimum": 0.2, "maximum": 1, "default": 0.5,
                    "description": "Classifier-free guidance weight, exposed as Pace."},
            }}
        schema["components"]["schemas"][name] = {
            "type": "object", "additionalProperties": True,
            "required": ["model_type", "workspace", "prompt"] + (["audio_guide"] if model == "index_tts2" else []),
            "description": "Projection of the existing generation operation; discover current model-options/defaults. "
                + ("VoxCPM ignores temperature, top_p, top_k, seed and duration_seconds; token limit bounds speech."
                   if model == "voxcpm2" else "KugelAudio uses these steps in its native diffusion head; alt_prompt is not a voice-direction input."
                   if model == "kugelaudio_0_open" else "Chatterbox uses these steps in its acoustic flow-matching decoder; autoregressive sampler settings remain separate."
                   if model == "chatterbox" else "IndexTTS2 uses these steps in its diffusion decoder; GPT sampler settings remain separate."),
            "properties": properties,
        }
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
    schema["components"]["schemas"]["DirectorRenderSettings"] = {
        "type": "object", "additionalProperties": True,
        "description": "Director render settings. Discover /api/v1/defaults/{model_type} and "
            "/api/v1/model-options/{model_type}; model locks, native canvas and frame constraints still apply. "
            "Extra saved fields remain compatible but do not imply native forwarding: current video builders "
            "use steps, guidance and resolution, set seed to -1, and do not forward arbitrary flow_shift or "
            "custom_settings. H3 may use extra profile settings for shot planning.",
        "properties": {
            "resolution": {"type": "string", "description": "Exact native canvas from model discovery; "
                "must agree with the selected Director preset/aspect and resolved video or image resolution."},
            "num_inference_steps": {"type": "integer", "description": "Requested native steps; respect "
                "model/accelerator locks. Director's generic fallback is 8, not the model's recommended default."},
            "guidance_scale": {"type": "number", "description": "Native guidance strength; discover model "
                "locks and supported phases. Director's generic fallback is 1."},
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
                            properties.update({
                                "video_params": {"$ref": "#/components/schemas/DirectorRenderSettings"},
                                "image_params": {"$ref": "#/components/schemas/DirectorRenderSettings"},
                                "shot_image_guidance": {"type": "string", "default": "auto",
                                    "enum": ["auto", "prompt_only", "generate"],
                                    "description": "Request per-shot image behavior. Models requiring start images "
                                        "still generate them; direct-reference models retain direct references. "
                                        "Optional-image models can use prompt_only. Auto uses their references when present. "
                                        "The effective policy is frozen at admission."},
                                "director_max_shot_frames": {"anyOf": [{"type": "integer"}, {"type": "string"},
                                    {"type": "null"}], "description": "Optional expert H3 segment ceiling, aligned and "
                                        "clamped to the selected model's native frame lattice. Omit for automatic planning. "
                                        "It does not override resource limits or a model's minimum frames."},
                                "seamless": {"type": "boolean", "default": True,
                                    "description": "Request continuity across video shots. Compatibility is model-specific; "
                                        "it may require native endpoint conditioning. False keeps independent shots."},
                            })
                        for nested in value.values():
                            bind_video_loras(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            bind_video_loras(nested)
                bind_video_loras(operation.get("requestBody", {}))
            if method == "post" and path == "/api/v1/generate":
                operation["x-maestro-model-contracts"] = {model: {
                    "$ref": "#/components/schemas/" + name} for model, name in (
                        ("drama3", "Drama3GenerationSettings"), ("voxcpm2", "VoxCPM2GenerationSettings"),
                        ("index_tts2", "IndexTTS2GenerationSettings"))}
                operation["description"] += " Drama 3's directed speech and reference settings are documented " \
                    "in Drama3GenerationSettings; use current defaults and model-options. No extra TTS execution path."
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
