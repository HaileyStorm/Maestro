"""Fail-closed, model-free admission documents for the H3 prompt rewriter.

This module deliberately does not load or execute the Qwen base or LightX2V
adapter.  It records immutable source identities, performs bounded passive
inspection of an explicitly supplied local candidate, and constructs canonical
request/preview/apply documents.  Model execution belongs to a later, separately
accepted runtime integration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
ADAPTER_REPO_ID = "lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B"
ADAPTER_REVISION = "a795219bd1677df34259eb4f3a77e2ec282e154f"
ADAPTER_FILENAME = "adapter_model.safetensors"
ADAPTER_SIZE_BYTES = 2_793_483_400
ADAPTER_SHA256 = "216590f4a02265fa625b8a6a1666bc1dae499a01f0badce4fc112d8f6aa36ffb"
ADAPTER_TENSOR_COUNT = 504
ADAPTER_PAIR_COUNT = 252
ADAPTER_RANK = 256
ADAPTER_LAYER_COUNT = 36
ADAPTER_TARGET_MODULES = (
    "down_proj", "gate_proj", "k_proj", "o_proj", "q_proj", "up_proj", "v_proj",
)
PEFT_VERSION = "0.20.0"

BASE_REPO_ID = "Qwen/Qwen3-VL-8B-Instruct"
BASE_REVISION = "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b"
BASE_TENSOR_TOTAL_SIZE = 17_534_247_392
BASE_SHARDS = (
    ("model-00001-of-00004.safetensors", 4_902_275_944, "d5d0aef0eb170fc7453a296c43c0849a56f510555d3588e4fd662bb35490aefa"),
    ("model-00002-of-00004.safetensors", 4_915_962_496, "8be88fb5501e4d5719a6d4cc212e6a13480330e74f3e8c77daa1a68f199106b5"),
    ("model-00003-of-00004.safetensors", 4_999_831_048, "83de00eafe6e0d57ccd009dbcf71c9974d74df2f016c27afb7e95aafd16b2192"),
    ("model-00004-of-00004.safetensors", 2_716_270_024, "0a88b98e9f96270973f567e6a2c103ede6ccdf915ca3075e21c755604d0377a5"),
)

SUPPORTED_MODES = ("t2va", "i2va", "l2va", "fl2va")
IMAGE_ROLES_BY_MODE = {
    "t2va": (),
    "i2va": ("first_frame",),
    "l2va": ("last_frame",),
    "fl2va": ("first_frame", "last_frame"),
}
CANDIDATE_KINDS = ("deterministic", "base", "adapted")

_REQUEST_KEYS = frozenset({
    "schema_version", "original_prompt", "mode", "image_roles",
    "literal_anchors", "role_commitments", "execution_policy", "commitment",
})
_IMAGE_ROLE_KEYS = frozenset({"role", "input_id"})
_ANCHOR_KEYS = frozenset({"anchor_id", "literal"})
_ROLE_KEYS = frozenset({"role_id", "commitment"})
_EXECUTION_POLICY_KEYS = frozenset({
    "explicit_compose_only", "auto_apply", "learned_fallback",
    "provider_fallback", "content_classification",
})
_PREVIEW_KEYS = frozenset({
    "schema_version", "request_commitment", "original_prompt", "candidates",
    "selection", "runtime_evidence", "commitment",
})
_CANDIDATE_KEYS = frozenset({"kind", "text", "produced_by_runtime"})
_DECISION_KEYS = frozenset({
    "schema_version", "request_commitment", "preview_commitment",
    "selected_kind", "selected_text_commitment", "decision_token",
})
_STATUS_KEYS = frozenset({
    "schema_version", "adapter_metadata_compatible", "base_metadata_compatible",
    "base_shards_compatible", "base_shards", "execution_available",
    "runtime_accepted", "gpu_accepted", "human_accepted", "reason",
})
_STATUS_SHARD_KEYS = frozenset({"name", "size_matches", "metadata_matches"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PATH_KEY = re.compile(r"(?:^|_)(?:path|filepath|filename|directory|cwd)(?:$|_)")
_MAX_SMALL_FILE_BYTES = 256 * 1024


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _commit(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label} must contain exactly {sorted(expected)}")


def _plain_string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise ValueError(f"{label} must be a{' possibly empty' if allow_empty else ' non-empty'} string")
    if "\x00" in value:
        raise ValueError(f"{label} contains a NUL byte")
    return value


def _identifier(value: Any, label: str) -> str:
    value = _plain_string(value, label)
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is not a valid identifier")
    return value


def _reject_path_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or _PATH_KEY.search(key.lower()):
                raise ValueError("public prompt-rewriter documents may not contain path fields")
            _reject_path_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_path_fields(child)


def _exact_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise ValueError(f"{label} must be integer {expected}")


def _exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise ValueError(f"{label} must be boolean {expected}")


def _sha256_string(value: Any, label: str) -> str:
    if type(value) is not str or not _HEX64.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 commitment")
    return value


def _regular_file_size(path: Path) -> int | None:
    """Return a regular, non-symlink leaf size without following links."""
    try:
        result = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
        return None
    return result.st_size


def adapter_descriptor() -> dict[str, Any]:
    """Return a fresh, path-free copy of the exact adapter identity."""
    return {
        "repo_id": ADAPTER_REPO_ID,
        "revision": ADAPTER_REVISION,
        "artifact": {"name": ADAPTER_FILENAME, "size_bytes": ADAPTER_SIZE_BYTES, "sha256": ADAPTER_SHA256},
        "structure": {
            "tensor_count": ADAPTER_TENSOR_COUNT,
            "complete_lora_pairs": ADAPTER_PAIR_COUNT,
            "rank": ADAPTER_RANK,
            "layer_count": ADAPTER_LAYER_COUNT,
            "target_modules": list(ADAPTER_TARGET_MODULES),
            "peft_version": PEFT_VERSION,
            "base_repo_id": BASE_REPO_ID,
        },
        "evidence": "cpu_header_verified",
        "runtime_accepted": False,
        "gpu_accepted": False,
        "human_accepted": False,
    }


def base_descriptor() -> dict[str, Any]:
    """Return a fresh, path-free copy of the pinned base identity."""
    return {
        "repo_id": BASE_REPO_ID,
        "revision": BASE_REVISION,
        "shards": [
            {"name": name, "size_bytes": size, "lfs_sha256": digest}
            for name, size, digest in BASE_SHARDS
        ],
        "tensor_total_size": BASE_TENSOR_TOTAL_SIZE,
        "metadata_offline_load_observed": True,
        "processor_offline_load_observed": True,
        "adapter_shapes_match_base": True,
        "runtime_accepted": False,
        "gpu_accepted": False,
        "human_accepted": False,
    }


def _read_small_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode) or not result.st_size or result.st_size > _MAX_SMALL_FILE_BYTES:
            raise ValueError(f"bounded metadata file has invalid size: {path.name}")
        data = os.read(descriptor, _MAX_SMALL_FILE_BYTES + 1)
        if len(data) > _MAX_SMALL_FILE_BYTES:
            raise ValueError(f"bounded metadata file is too large: {path.name}")
        return data
    finally:
        os.close(descriptor)


def _read_small_json(path: Path) -> Any:
    return json.loads(_read_small_bytes(path))


def _read_small_text(path: Path) -> str:
    return _read_small_bytes(path).decode("utf-8")


def inspect_local_candidate(adapter_directory: os.PathLike[str] | str, base_directory: os.PathLike[str] | str) -> dict[str, Any]:
    """Passively inspect exact names, sizes, and bounded sidecars only.

    Large weight bytes are never opened or hashed. Local paths are inputs only
    and are intentionally absent from the returned public status document.
    """
    adapter_root = Path(adapter_directory)
    base_root = Path(base_directory)
    adapter_weight = adapter_root / ADAPTER_FILENAME
    adapter_size_matches = _regular_file_size(adapter_weight) == ADAPTER_SIZE_BYTES

    source_matches = False
    config_matches = False
    try:
        source = _read_small_json(adapter_root / "adapter_model.maestro-source.json")
        source_matches = (
            isinstance(source, dict)
            and source.get("repo_id") == ADAPTER_REPO_ID
            and source.get("revision") == ADAPTER_REVISION
            and source.get("filename") == ADAPTER_FILENAME
            and type(source.get("size_bytes")) is int
            and source.get("size_bytes") == ADAPTER_SIZE_BYTES
            and source.get("sha256") == ADAPTER_SHA256
            and type(source.get("tensor_count")) is int
            and source.get("tensor_count") == ADAPTER_TENSOR_COUNT
        )
        config = _read_small_json(adapter_root / "adapter_config.json")
        config_matches = (
            isinstance(config, dict)
            and config.get("base_model_name_or_path") == BASE_REPO_ID
            and config.get("peft_version") == PEFT_VERSION
            and type(config.get("r")) is int
            and config.get("r") == ADAPTER_RANK
            and isinstance(config.get("target_modules"), list)
            and all(type(item) is str for item in config["target_modules"])
            and set(config["target_modules"]) == set(ADAPTER_TARGET_MODULES)
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    observed_shards: list[dict[str, Any]] = []
    for name, expected_size, expected_hash in BASE_SHARDS:
        shard = base_root / name
        size_matches = _regular_file_size(shard) == expected_size
        metadata_matches = False
        metadata = base_root / ".cache" / "huggingface" / "download" / f"{name}.metadata"
        try:
            fields = _read_small_text(metadata).split()
            metadata_matches = len(fields) >= 2 and fields[0] == BASE_REVISION and fields[1] == expected_hash
        except (OSError, ValueError):
            pass
        observed_shards.append({"name": name, "size_matches": size_matches, "metadata_matches": metadata_matches})

    base_metadata_present = False
    try:
        config = _read_small_json(base_root / "config.json")
        processor = _read_small_json(base_root / "preprocessor_config.json")
        index = _read_small_json(base_root / "model.safetensors.index.json")
        metadata = index.get("metadata") if isinstance(index, dict) else None
        weight_map = index.get("weight_map") if isinstance(index, dict) else None
        expected_names = {name for name, _size, _digest in BASE_SHARDS}
        base_metadata_present = (
            isinstance(config, dict)
            and config.get("model_type") == "qwen3_vl"
            and config.get("architectures") == ["Qwen3VLForConditionalGeneration"]
            and isinstance(processor, dict)
            and processor.get("image_processor_type") == "Qwen2VLImageProcessorFast"
            and processor.get("processor_class") == "Qwen3VLProcessor"
            and isinstance(metadata, dict)
            and type(metadata.get("total_size")) is int
            and metadata.get("total_size") == BASE_TENSOR_TOTAL_SIZE
            and isinstance(weight_map, dict)
            and bool(weight_map)
            and all(type(key) is str and type(value) is str for key, value in weight_map.items())
            and set(weight_map.values()) == expected_names
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    adapter_metadata_present = adapter_size_matches and source_matches and config_matches
    base_shards_present = all(item["size_matches"] and item["metadata_matches"] for item in observed_shards)
    status = {
        "schema_version": SCHEMA_VERSION,
        "adapter_metadata_compatible": adapter_metadata_present,
        "base_metadata_compatible": base_metadata_present,
        "base_shards_compatible": base_shards_present,
        "base_shards": observed_shards,
        "execution_available": False,
        "runtime_accepted": False,
        "gpu_accepted": False,
        "human_accepted": False,
        "reason": "runtime_not_implemented_or_accepted",
    }
    _reject_path_fields(status)
    return status


def _normalize_images(mode: str, image_roles: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if type(image_roles) is not list:
        raise ValueError("image_roles must be a list")
    expected = IMAGE_ROLES_BY_MODE[mode]
    if len(image_roles) != len(expected):
        raise ValueError(f"{mode} requires exactly {len(expected)} image roles")
    result = []
    for index, (entry, role) in enumerate(zip(image_roles, expected)):
        _exact_keys(entry, _IMAGE_ROLE_KEYS, f"image_roles[{index}]")
        if entry["role"] != role:
            raise ValueError(f"image_roles[{index}].role must be {role}")
        result.append({"role": role, "input_id": _identifier(entry["input_id"], f"image_roles[{index}].input_id")})
    if len({entry["input_id"] for entry in result}) != len(result):
        raise ValueError("image role input IDs must be distinct")
    return result


def _normalize_anchors(anchors: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if type(anchors) is not list:
        raise ValueError("literal_anchors must be a list")
    result = []
    for index, entry in enumerate(anchors):
        _exact_keys(entry, _ANCHOR_KEYS, f"literal_anchors[{index}]")
        result.append({
            "anchor_id": _identifier(entry["anchor_id"], f"literal_anchors[{index}].anchor_id"),
            "literal": _plain_string(entry["literal"], f"literal_anchors[{index}].literal"),
        })
    if len({entry["anchor_id"] for entry in result}) != len(result):
        raise ValueError("literal anchor IDs must be distinct")
    if len({entry["literal"] for entry in result}) != len(result):
        raise ValueError("literal anchor strings must be distinct")
    return result


def _normalize_roles(roles: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if type(roles) is not list:
        raise ValueError("role_commitments must be a list")
    result = []
    for index, entry in enumerate(roles):
        _exact_keys(entry, _ROLE_KEYS, f"role_commitments[{index}]")
        result.append({
            "role_id": _identifier(entry["role_id"], f"role_commitments[{index}].role_id"),
            "commitment": _plain_string(entry["commitment"], f"role_commitments[{index}].commitment"),
        })
    if len({entry["role_id"] for entry in result}) != len(result):
        raise ValueError("role commitment IDs must be distinct")
    return result


def create_rewrite_request(*, original_prompt: str, mode: str, image_roles: Sequence[Mapping[str, Any]] | None = None, literal_anchors: Sequence[Mapping[str, Any]] | None = None, role_commitments: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Create an explicit, canonical, non-executing Compose-for-H3 request."""
    original_prompt = _plain_string(original_prompt, "original_prompt", allow_empty=True)
    if type(mode) is not str or mode not in SUPPORTED_MODES:
        raise ValueError(f"mode must be one of {SUPPORTED_MODES}; Ref2VA is unsupported")
    normalized_anchors = _normalize_anchors([] if literal_anchors is None else literal_anchors)
    if any(anchor["literal"] not in original_prompt for anchor in normalized_anchors):
        raise ValueError("every literal anchor must occur in the original prompt")
    request = {
        "schema_version": SCHEMA_VERSION,
        "original_prompt": original_prompt,
        "mode": mode,
        "image_roles": _normalize_images(mode, [] if image_roles is None else image_roles),
        "literal_anchors": normalized_anchors,
        "role_commitments": _normalize_roles([] if role_commitments is None else role_commitments),
        "execution_policy": {
            "explicit_compose_only": True,
            "auto_apply": False,
            "learned_fallback": False,
            "provider_fallback": False,
            "content_classification": False,
        },
    }
    _reject_path_fields(request)
    request["commitment"] = _commit(request)
    return request


def validate_rewrite_request(request: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(request, _REQUEST_KEYS, "rewrite request")
    _exact_int(request["schema_version"], SCHEMA_VERSION, "rewrite request schema_version")
    _sha256_string(request["commitment"], "rewrite request commitment")
    policy = request["execution_policy"]
    _exact_keys(policy, _EXECUTION_POLICY_KEYS, "execution_policy")
    for key, expected in {
        "explicit_compose_only": True,
        "auto_apply": False,
        "learned_fallback": False,
        "provider_fallback": False,
        "content_classification": False,
    }.items():
        _exact_bool(policy[key], expected, f"execution_policy.{key}")
    rebuilt = create_rewrite_request(
        original_prompt=request["original_prompt"], mode=request["mode"],
        image_roles=request["image_roles"], literal_anchors=request["literal_anchors"],
        role_commitments=request["role_commitments"],
    )
    if request != rebuilt:
        raise ValueError("rewrite request commitment or policy does not match its canonical content")
    return rebuilt


def _anchor_sequence(anchors: Sequence[Mapping[str, str]], text: str) -> list[str]:
    occurrences: list[tuple[int, str]] = []
    for anchor in anchors:
        start = 0
        while True:
            index = text.find(anchor["literal"], start)
            if index < 0:
                break
            occurrences.append((index, anchor["anchor_id"]))
            start = index + len(anchor["literal"])
    occurrences.sort(key=lambda item: item[0])
    return [anchor_id for _index, anchor_id in occurrences]


def _anchors_are_preserved(request: Mapping[str, Any], text: str) -> bool:
    return _anchor_sequence(request["literal_anchors"], text) == _anchor_sequence(
        request["literal_anchors"], request["original_prompt"],
    )


def create_rewrite_preview(request: Mapping[str, Any], *, deterministic: str, base: str, adapted: str) -> dict[str, Any]:
    """Create an editable preview from caller-supplied candidate text.

    Supplying text is not evidence that a model ran; every candidate is marked
    unexecuted until a future runtime integration can provide its own receipt.
    """
    request = validate_rewrite_request(request)
    candidates = []
    for kind, value in zip(CANDIDATE_KINDS, (deterministic, base, adapted)):
        text = _plain_string(value, f"{kind} candidate", allow_empty=True)
        if not _anchors_are_preserved(request, text):
            raise ValueError(f"{kind} candidate does not preserve every literal anchor")
        candidates.append({"kind": kind, "text": text, "produced_by_runtime": False})
    preview = {
        "schema_version": SCHEMA_VERSION,
        "request_commitment": request["commitment"],
        "original_prompt": request["original_prompt"],
        "candidates": candidates,
        "selection": None,
        "runtime_evidence": {
            "execution_available": False,
            "base_executed": False,
            "adapter_executed": False,
            "fallback_used": False,
        },
    }
    _reject_path_fields(preview)
    preview["commitment"] = _commit(preview)
    return preview


def validate_rewrite_preview(request: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    request = validate_rewrite_request(request)
    _exact_keys(preview, _PREVIEW_KEYS, "rewrite preview")
    _exact_int(preview["schema_version"], SCHEMA_VERSION, "rewrite preview schema_version")
    _sha256_string(preview["request_commitment"], "preview request_commitment")
    _sha256_string(preview["commitment"], "preview commitment")
    if preview["request_commitment"] != request["commitment"] or preview["original_prompt"] != request["original_prompt"]:
        raise ValueError("preview is not bound to the exact request and original prompt")
    candidates = preview["candidates"]
    if type(candidates) is not list or len(candidates) != len(CANDIDATE_KINDS):
        raise ValueError("preview must contain exactly deterministic, base, and adapted candidates")
    texts = []
    for index, (candidate, kind) in enumerate(zip(candidates, CANDIDATE_KINDS)):
        _exact_keys(candidate, _CANDIDATE_KEYS, f"candidates[{index}]")
        if _plain_string(candidate["kind"], f"candidates[{index}].kind") != kind or candidate["produced_by_runtime"] is not False:
            raise ValueError("candidate order or execution evidence is invalid")
        text = _plain_string(candidate["text"], f"candidates[{index}].text", allow_empty=True)
        if not _anchors_are_preserved(request, text):
            raise ValueError("candidate does not preserve every literal anchor")
        texts.append(text)
    if preview["selection"] is not None:
        raise ValueError("preview selection must remain null until an explicit decision")
    runtime = preview["runtime_evidence"]
    expected_runtime = {
        "execution_available": False, "base_executed": False,
        "adapter_executed": False, "fallback_used": False,
    }
    _exact_keys(runtime, frozenset(expected_runtime), "runtime_evidence")
    for key, expected in expected_runtime.items():
        _exact_bool(runtime[key], expected, f"runtime_evidence.{key}")
    rebuilt = create_rewrite_preview(request, deterministic=texts[0], base=texts[1], adapted=texts[2])
    if preview != rebuilt:
        raise ValueError("preview commitment, selection, or runtime evidence is invalid")
    return rebuilt


def create_apply_decision(request: Mapping[str, Any], preview: Mapping[str, Any], selected_kind: str) -> dict[str, str | int]:
    """Create a reusable integrity-bound decision; this is not authorization."""
    request = validate_rewrite_request(request)
    preview = validate_rewrite_preview(request, preview)
    if type(selected_kind) is not str or selected_kind not in CANDIDATE_KINDS:
        raise ValueError(f"selected_kind must be one of {CANDIDATE_KINDS}")
    selected = next(item for item in preview["candidates"] if item["kind"] == selected_kind)
    decision = {
        "schema_version": SCHEMA_VERSION,
        "request_commitment": request["commitment"],
        "preview_commitment": preview["commitment"],
        "selected_kind": selected_kind,
        "selected_text_commitment": _commit(selected["text"]),
    }
    decision["decision_token"] = _commit(decision)
    return decision


def apply_preview_decision(request: Mapping[str, Any], preview: Mapping[str, Any], decision: Mapping[str, Any]) -> str:
    """Validate a reusable decision token and return a new selected string."""
    request = validate_rewrite_request(request)
    preview = validate_rewrite_preview(request, preview)
    _exact_keys(decision, _DECISION_KEYS, "apply decision")
    _exact_int(decision["schema_version"], SCHEMA_VERSION, "apply decision schema_version")
    _plain_string(decision["selected_kind"], "selected_kind")
    for key in ("request_commitment", "preview_commitment", "selected_text_commitment", "decision_token"):
        _sha256_string(decision[key], key)
    expected = create_apply_decision(request, preview, decision["selected_kind"])
    if decision != expected:
        raise ValueError("apply decision token or commitment is invalid")
    return next(item["text"] for item in preview["candidates"] if item["kind"] == decision["selected_kind"])


def _validate_public_status(document: Mapping[str, Any]) -> None:
    _exact_keys(document, _STATUS_KEYS, "local status")
    _exact_int(document["schema_version"], SCHEMA_VERSION, "local status schema_version")
    for key in (
        "adapter_metadata_compatible", "base_metadata_compatible", "base_shards_compatible",
        "execution_available", "runtime_accepted", "gpu_accepted", "human_accepted",
    ):
        _exact_bool(document[key], False if key in {"execution_available", "runtime_accepted", "gpu_accepted", "human_accepted"} else document[key], key)
    _plain_string(document["reason"], "reason")
    if document["reason"] != "runtime_not_implemented_or_accepted":
        raise ValueError("status reason is not recognized")
    shards = document["base_shards"]
    if type(shards) is not list or len(shards) != len(BASE_SHARDS):
        raise ValueError("status must contain exactly four shard observations")
    for observed, expected in zip(shards, BASE_SHARDS):
        _exact_keys(observed, _STATUS_SHARD_KEYS, "status shard")
        if observed["name"] != expected[0]:
            raise ValueError("status shard order or name is invalid")
        _exact_bool(observed["size_matches"], observed["size_matches"], "size_matches")
        _exact_bool(observed["metadata_matches"], observed["metadata_matches"], "metadata_matches")


def _validate_public_decision(document: Mapping[str, Any]) -> None:
    _exact_keys(document, _DECISION_KEYS, "apply decision")
    _exact_int(document["schema_version"], SCHEMA_VERSION, "apply decision schema_version")
    for key in ("request_commitment", "preview_commitment", "selected_text_commitment", "decision_token"):
        _sha256_string(document[key], key)
    if type(document["selected_kind"]) is not str or document["selected_kind"] not in CANDIDATE_KINDS:
        raise ValueError("selected_kind is invalid")
    unsigned = dict(document)
    token = unsigned.pop("decision_token")
    if token != _commit(unsigned):
        raise ValueError("decision_token does not match the decision")


def _validate_public_preview(document: Mapping[str, Any]) -> None:
    _exact_keys(document, _PREVIEW_KEYS, "rewrite preview")
    _exact_int(document["schema_version"], SCHEMA_VERSION, "rewrite preview schema_version")
    for key in ("request_commitment", "commitment"):
        _sha256_string(document[key], key)
    _plain_string(document["original_prompt"], "original_prompt", allow_empty=True)
    if document["selection"] is not None:
        raise ValueError("unapplied preview selection must be null")
    candidates = document["candidates"]
    if type(candidates) is not list or len(candidates) != len(CANDIDATE_KINDS):
        raise ValueError("preview candidate shape is invalid")
    for candidate, kind in zip(candidates, CANDIDATE_KINDS):
        _exact_keys(candidate, _CANDIDATE_KEYS, "preview candidate")
        if _plain_string(candidate["kind"], "candidate kind") != kind:
            raise ValueError("preview candidate order is invalid")
        _plain_string(candidate["text"], "candidate text", allow_empty=True)
        _exact_bool(candidate["produced_by_runtime"], False, "produced_by_runtime")
    expected_runtime = {
        "execution_available": False, "base_executed": False,
        "adapter_executed": False, "fallback_used": False,
    }
    _exact_keys(document["runtime_evidence"], frozenset(expected_runtime), "runtime_evidence")
    for key, expected in expected_runtime.items():
        _exact_bool(document["runtime_evidence"][key], expected, f"runtime_evidence.{key}")
    unsigned = dict(document)
    commitment = unsigned.pop("commitment")
    if commitment != _commit(unsigned):
        raise ValueError("preview commitment does not match")


def canonical_public_projection(document: Mapping[str, Any]) -> str:
    """Return canonical JSON only for an enumerated, validated public schema."""
    if type(document) is not dict:
        raise ValueError("public document must be an object")
    _reject_path_fields(document)
    keys = set(document)
    if keys == _REQUEST_KEYS:
        validate_rewrite_request(document)
    elif keys == _PREVIEW_KEYS:
        _validate_public_preview(document)
    elif keys == _DECISION_KEYS:
        _validate_public_decision(document)
    elif keys == _STATUS_KEYS:
        _validate_public_status(document)
    elif _canonical(document) in {_canonical(adapter_descriptor()), _canonical(base_descriptor())}:
        pass
    else:
        raise ValueError("public document does not match a known prompt-rewriter schema")
    return _canonical(document).decode("utf-8")
