"""Account- and project-scoped durable generation preset storage.

The store is deliberately content-free: it accepts only the technical fields
used by Maestro's saved-generation-settings UI. Prompt text, messages, media,
and reference payloads are outside this contract and are rejected rather than
silently discarded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from .entitlements import ExclusiveLeaseError, exclusive_file_lease


SCHEMA_VERSION = 1
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 2_000
MAX_RECORDS_PER_SCOPE = 256
MAX_PARAMS_BYTES = 64 * 1024
MAX_JSON_DEPTH = 8
MAX_JSON_ITEMS = 2_048

_STATE_FILENAME = "generation_presets.json"
_LOCK_FILENAME = ".generation_presets.lock"
_SCOPE_DOMAIN = b"maestro-generation-preset-scope-v1\0"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
_RESOLUTION_RE = re.compile(r"([1-9][0-9]{1,4})x([1-9][0-9]{1,4})\Z")
_TECHNICAL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+~-]{0,127}\Z")
_MULTIPLIER_RE = re.compile(
    r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?\Z",
)
_MODES = frozenset({"image", "video", "audio", "avatar", "tools"})
_LEGACY_PRESET_KEYS = frozenset(
    {
        "name",
        "mode",
        "model_type",
        "activated_loras",
        "loras_multipliers",
        "lora_weights",
        "spatial_upsampling",
        "params",
    }
)
_V2_PRESET_KEYS = _LEGACY_PRESET_KEYS | {"profile_version", "ui_settings"}
_V2_REQUIRED_PARAM_KEYS = frozenset(
    {
        "resolution",
        "video_length",
        "num_inference_steps",
        "guidance_scale",
        "seed",
        "image_mode",
        "repeat_generation",
        "settings_version",
    }
)
_PARAM_KEYS = frozenset(
    {
        "num_inference_steps",
        "guidance_scale",
        "resolution",
        "seed",
        "flow_shift",
        "self_refiner_setting",
        "stage2_steps",
        "tea_cache",
        "delivery_resolution",
        "delivery_fit",
        "custom_settings",
    }
)
_CUSTOM_SETTING_KEYS = frozenset(
    {
        "h3_spectrum_profile",
        "h3_lightx2v_profile",
        "h3_attention_engine",
        "h3_sol_tau",
        "h3_sol_dense_steps",
        "h3_sol_dense_blocks",
        "h3_sol_min_tokens",
        "h3_turbo_profile",
    }
)
_RECORD_METADATA_KEYS = frozenset({"id", "created_at"})
_STORED_METADATA_KEYS = _RECORD_METADATA_KEYS | {"sequence"}
_PROFILE_FIELDS_PATH = Path(__file__).with_name("generation_profile_fields.json")


class GenerationPresetError(ValueError):
    """Base error for invalid preset operations."""


class GenerationPresetConflict(GenerationPresetError):
    """A caller-supplied preset identifier was rebound to other settings."""


class GenerationPresetLimitError(GenerationPresetError):
    """A configured record or serialization bound would be exceeded."""


class GenerationPresetIntegrityError(GenerationPresetError):
    """Persisted preset state is corrupt, unsafe, or unavailable."""


class GenerationPresetCommitIndeterminate(GenerationPresetIntegrityError):
    """Atomic replacement completed but its directory sync did not."""


class _DuplicateJsonKey(ValueError):
    pass


def _validate_profile_descriptor(descriptor: Any) -> None:
    descriptor_keys = {
        "type", "nullable", "max_items", "max_bytes", "properties",
        "enum", "pattern", "minimum", "maximum",
    }
    supported_types = {
        "string", "number", "integer", "boolean", "string_array",
        "number_array", "object",
    }
    if (
        type(descriptor) is not dict
        or not set(descriptor).issubset(descriptor_keys)
        or descriptor.get("type") not in supported_types
        or (
            "nullable" in descriptor
            and type(descriptor["nullable"]) is not bool
        )
        or (
            "max_items" in descriptor
            and (
                type(descriptor["max_items"]) is not int
                or not 1 <= descriptor["max_items"] <= 512
            )
        )
        or (
            "max_bytes" in descriptor
            and (
                type(descriptor["max_bytes"]) is not int
                or not 1 <= descriptor["max_bytes"] <= 4_096
            )
        )
    ):
        raise RuntimeError("generation profile field manifest is invalid")
    kind = descriptor["type"]
    if "max_items" in descriptor and kind not in {"string_array", "number_array"}:
        raise RuntimeError("generation profile field manifest is invalid")
    if "max_bytes" in descriptor and kind not in {"string", "string_array"}:
        raise RuntimeError("generation profile field manifest is invalid")
    limits = [descriptor.get("minimum"), descriptor.get("maximum")]
    if any(limit is not None for limit in limits):
        if kind not in {"number", "integer", "number_array"} or any(
            type(limit) not in (int, float) or not math.isfinite(limit)
            for limit in limits
            if limit is not None
        ):
            raise RuntimeError("generation profile field manifest is invalid")
        if kind == "integer" and any(
            type(limit) is not int for limit in limits if limit is not None
        ):
            raise RuntimeError("generation profile field manifest is invalid")
        if limits[0] is not None and limits[1] is not None and limits[0] > limits[1]:
            raise RuntimeError("generation profile field manifest is invalid")
    pattern = descriptor.get("pattern")
    if pattern is not None:
        if kind != "string" or type(pattern) is not str or not pattern or len(pattern) > 512:
            raise RuntimeError("generation profile field manifest is invalid")
        try:
            re.compile(pattern)
        except re.error:
            raise RuntimeError("generation profile field manifest is invalid") from None
    choices = descriptor.get("enum")
    if choices is not None:
        if (
            kind not in {"string", "number", "integer", "boolean"}
            or type(choices) is not list
            or not 1 <= len(choices) <= 128
        ):
            raise RuntimeError("generation profile field manifest is invalid")
        allowed_type = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
        }[kind]
        for choice in choices:
            if (
                type(choice) is bool and kind != "boolean"
            ) or not isinstance(choice, allowed_type) or (
                type(choice) is float and not math.isfinite(choice)
            ):
                raise RuntimeError("generation profile field manifest is invalid")
        if len({_canonical(choice) for choice in choices}) != len(choices):
            raise RuntimeError("generation profile field manifest is invalid")
    properties = descriptor.get("properties")
    if properties is not None:
        if kind != "object" or type(properties) is not dict or not properties:
            raise RuntimeError("generation profile field manifest is invalid")
        for name, child in properties.items():
            if type(name) is not str or not name:
                raise RuntimeError("generation profile field manifest is invalid")
            _validate_profile_descriptor(child)


def _load_profile_fields() -> dict[str, Any]:
    try:
        value = json.loads(
            _PROFILE_FIELDS_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("generation profile field manifest is unreadable") from error
    if type(value) is not dict or set(value) != {
        "version", "params", "excluded_params", "ui", "custom_settings",
    } or value.get("version") != 2:
        raise RuntimeError("generation profile field manifest is invalid")
    for section in ("params", "ui", "custom_settings"):
        fields = value[section]
        if type(fields) is not dict or not fields:
            raise RuntimeError("generation profile field manifest is invalid")
        for key, descriptor in fields.items():
            if type(key) is not str or not key:
                raise RuntimeError("generation profile field manifest is invalid")
            _validate_profile_descriptor(descriptor)
    excluded = value["excluded_params"]
    if type(excluded) is not dict or any(
        type(key) is not str or not key or type(category) is not str or not category
        for key, category in excluded.items()
    ):
        raise RuntimeError("generation profile field manifest is invalid")
    classified = set(value["params"])
    excluded_top_level = {key for key in excluded if "." not in key}
    if classified & excluded_top_level:
        raise RuntimeError("generation profile field manifest is invalid")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey("duplicate JSON key")
        result[key] = value
    return result


GENERATION_PROFILE_FIELDS = _load_profile_fields()


def _bounded_text(
    value: Any,
    name: str,
    *,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise GenerationPresetError(f"{name} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise GenerationPresetError(f"{name} is invalid") from None
    if (not allow_empty and not encoded) or len(encoded) > maximum_bytes:
        raise GenerationPresetError(f"{name} exceeds its bound")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise GenerationPresetError(f"{name} contains control characters")
    return value


def _scope_secret(value: bytes | str) -> bytes:
    if type(value) is str:
        try:
            result = value.encode("utf-8")
        except UnicodeError:
            raise GenerationPresetError("scope key is invalid") from None
    elif type(value) is bytes:
        result = value
    else:
        raise GenerationPresetError("scope key must be bytes or text")
    if not 32 <= len(result) <= 4_096:
        raise GenerationPresetError("scope key must contain 32 to 4096 bytes")
    return result


def _scope_digest(
    scope_key: bytes,
    account_scope: Any,
    project_scope: Any,
) -> str:
    account = _bounded_text(
        account_scope, "account scope", maximum_bytes=512,
    ).encode("utf-8")
    project = _bounded_text(
        project_scope, "project scope", maximum_bytes=512,
    ).encode("utf-8")
    framed = (
        len(account).to_bytes(4, "big")
        + account
        + len(project).to_bytes(4, "big")
        + project
    )
    return hmac.new(scope_key, _SCOPE_DOMAIN + framed, hashlib.sha256).hexdigest()


def _preset_id(value: Any) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise GenerationPresetError("preset id is invalid")
    return value


def _number(
    value: Any,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> int | float:
    if type(value) not in (int, float):
        raise GenerationPresetError(f"{name} must be a number")
    if (
        (type(value) is float and not math.isfinite(value))
        or value < minimum
        or value > maximum
    ):
        raise GenerationPresetError(f"{name} is outside its supported range")
    return value


def _integer(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise GenerationPresetError(f"{name} must be a bounded integer")
    return value


def _plain_json(value: Any, *, depth: int = 0, counter: list[int]) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise GenerationPresetLimitError("params exceed the JSON depth bound")
    counter[0] += 1
    if counter[0] > MAX_JSON_ITEMS:
        raise GenerationPresetLimitError("params exceed the JSON item bound")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > 9_007_199_254_740_991:
            raise GenerationPresetError("params contain an unsafe integer")
        return value
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > 1_000_000_000_000:
            raise GenerationPresetError("params contain an unsafe number")
        return value
    if type(value) is str:
        return _bounded_text(
            value, "params string", maximum_bytes=4_096, allow_empty=True,
        )
    if type(value) is list:
        if len(value) > 512:
            raise GenerationPresetLimitError("params list exceeds its bound")
        return [
            _plain_json(item, depth=depth + 1, counter=counter)
            for item in value
        ]
    if type(value) is dict:
        if len(value) > 256:
            raise GenerationPresetLimitError("params object exceeds its bound")
        result: dict[str, Any] = {}
        for key, item in value.items():
            key = _bounded_text(
                key, "params key", maximum_bytes=128,
            )
            result[key] = _plain_json(
                item, depth=depth + 1, counter=counter,
            )
        return result
    raise GenerationPresetError("params must contain only plain JSON values")


def _profile_field_value(
    value: Any,
    descriptor: Mapping[str, Any],
    name: str,
) -> Any:
    if value is None:
        if descriptor.get("nullable") is True:
            return None
        raise GenerationPresetError(f"{name} cannot be null")
    kind = descriptor["type"]
    if kind == "string":
        normalized = _bounded_text(
            value,
            name,
            maximum_bytes=descriptor.get("max_bytes", 4_096),
            allow_empty=True,
        )
        if "enum" in descriptor and normalized not in descriptor["enum"]:
            raise GenerationPresetError(f"{name} is not a supported choice")
        pattern = descriptor.get("pattern")
        if pattern is not None and re.fullmatch(pattern, normalized) is None:
            raise GenerationPresetError(f"{name} has an invalid format")
        return normalized
    if kind == "number":
        normalized = _number(
            value, name,
            minimum=descriptor.get("minimum", -1_000_000_000_000),
            maximum=descriptor.get("maximum", 1_000_000_000_000),
        )
        if "enum" in descriptor and normalized not in descriptor["enum"]:
            raise GenerationPresetError(f"{name} is not a supported choice")
        return normalized
    if kind == "integer":
        normalized = _integer(
            value, name,
            minimum=descriptor.get("minimum", -9_007_199_254_740_991),
            maximum=descriptor.get("maximum", 9_007_199_254_740_991),
        )
        if "enum" in descriptor and normalized not in descriptor["enum"]:
            raise GenerationPresetError(f"{name} is not a supported choice")
        return normalized
    if kind == "boolean":
        if type(value) is not bool:
            raise GenerationPresetError(f"{name} must be a boolean")
        if "enum" in descriptor and value not in descriptor["enum"]:
            raise GenerationPresetError(f"{name} is not a supported choice")
        return value
    if kind in {"string_array", "number_array"}:
        if type(value) is not list or len(value) > descriptor.get("max_items", 512):
            raise GenerationPresetError(f"{name} must be a bounded list")
        item_descriptor = {
            "type": "string" if kind == "string_array" else "number",
            "max_bytes": descriptor.get("max_bytes", 4_096),
            **(
                {"minimum": descriptor["minimum"]}
                if "minimum" in descriptor else {}
            ),
            **(
                {"maximum": descriptor["maximum"]}
                if "maximum" in descriptor else {}
            ),
        }
        return [
            _profile_field_value(item, item_descriptor, f"{name} item")
            for item in value
        ]
    if kind == "object":
        if type(value) is not dict:
            raise GenerationPresetError(f"{name} must be a plain object")
        properties = descriptor.get("properties")
        if properties is not None:
            if set(value) != set(properties):
                raise GenerationPresetError(f"{name} has invalid fields")
            return {
                key: _profile_field_value(item, properties[key], f"{name}.{key}")
                for key, item in value.items()
            }
        return _plain_json(value, counter=[0])
    raise GenerationPresetError(f"{name} has an unsupported field type")


def _validated_resolution(value: Any, name: str) -> str:
    if type(value) is not str:
        raise GenerationPresetError(f"{name} must be a string")
    match = _RESOLUTION_RE.fullmatch(value)
    if match is None or any(
        not 16 <= int(dimension) <= 32_768
        for dimension in match.groups()
    ):
        raise GenerationPresetError(f"{name} must be a supported WxH value")
    return value


def _normalize_v2_custom_settings(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise GenerationPresetError("custom_settings must be a plain object")
    descriptors = GENERATION_PROFILE_FIELDS["custom_settings"]
    if not set(value).issubset(descriptors):
        raise GenerationPresetError(
            "custom_settings contain unsupported or private fields",
        )
    return {
        key: _profile_field_value(item, descriptors[key], f"custom_settings.{key}")
        for key, item in value.items()
    }


def _validate_profile_lora_pair(
    params: Mapping[str, Any],
    list_key: str,
    multiplier_key: str,
) -> None:
    selected = params.get(list_key)
    multipliers = params.get(multiplier_key)
    names = [] if selected is None else selected
    weights = "" if multipliers is None else multipliers
    if any(not name for name in names) or len(set(names)) != len(names):
        raise GenerationPresetError(f"params.{list_key} contains invalid assets")
    if not names and weights:
        raise GenerationPresetError(
            f"params.{multiplier_key} requires selected assets",
        )
    tokens = weights.split()
    if len(tokens) > len(names):
        raise GenerationPresetError(
            f"params.{multiplier_key} exceeds selected assets",
        )
    for token in tokens:
        phases = token.split(";")
        if not 1 <= len(phases) <= 3:
            raise GenerationPresetError(
                f"params.{multiplier_key} has invalid phases",
            )
        try:
            values = [float(phase) for phase in phases]
        except (OverflowError, ValueError):
            raise GenerationPresetError(
                f"params.{multiplier_key} has invalid weights",
            ) from None
        if any(not math.isfinite(item) or not -10 <= item <= 10 for item in values):
            raise GenerationPresetError(
                f"params.{multiplier_key} has invalid weights",
            )


def _normalize_v2_params(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise GenerationPresetError("params must be a plain JSON object")
    descriptors = GENERATION_PROFILE_FIELDS["params"]
    if not set(value).issubset(descriptors):
        raise GenerationPresetError("params contain unsupported or private fields")
    if not _V2_REQUIRED_PARAM_KEYS.issubset(value):
        raise GenerationPresetError(
            "params are missing required generation fields",
        )
    normalized = {
        key: (
            _normalize_v2_custom_settings(item)
            if key == "custom_settings"
            else _profile_field_value(item, descriptors[key], f"params.{key}")
        )
        for key, item in value.items()
    }
    _validated_resolution(normalized["resolution"], "resolution")
    delivery_resolution = normalized.get("delivery_resolution")
    if delivery_resolution not in (None, ""):
        _validated_resolution(delivery_resolution, "delivery_resolution")
    for architecture in ("fl2va", "ref2va"):
        _validate_profile_lora_pair(
            normalized,
            f"h3_{architecture}_loras",
            f"h3_{architecture}_loras_multipliers",
        )
    try:
        encoded = _canonical(normalized)
    except (OverflowError, TypeError, ValueError) as error:
        raise GenerationPresetError("params cannot be serialized safely") from error
    if len(encoded) > MAX_PARAMS_BYTES:
        raise GenerationPresetLimitError("params exceed their serialized bound")
    return normalized


def _normalize_ui_settings(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise GenerationPresetError("ui_settings must be a plain JSON object")
    descriptors = GENERATION_PROFILE_FIELDS["ui"]
    if set(value) != set(descriptors):
        raise GenerationPresetError(
            "ui_settings are incomplete, unsupported, or private",
        )
    normalized = {
        key: _profile_field_value(item, descriptors[key], f"ui_settings.{key}")
        for key, item in value.items()
    }
    edit_resolution = normalized.get("editVideoResolution")
    if edit_resolution:
        _validated_resolution(edit_resolution, "editVideoResolution")
    try:
        encoded = _canonical(normalized)
    except (OverflowError, TypeError, ValueError) as error:
        raise GenerationPresetError("ui_settings cannot be serialized safely") from error
    if len(encoded) > MAX_PARAMS_BYTES:
        raise GenerationPresetLimitError("ui_settings exceed their serialized bound")
    return normalized


def _normalize_params(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise GenerationPresetError("params must be a plain JSON object")
    if not set(value).issubset(_PARAM_KEYS):
        raise GenerationPresetError("params contain unsupported or private fields")
    required = {
        "num_inference_steps", "guidance_scale", "resolution", "seed",
    }
    if not required.issubset(value):
        raise GenerationPresetError(
            "params are missing required generation fields",
        )
    _integer(
        value["num_inference_steps"],
        "num_inference_steps",
        minimum=1,
        maximum=1_000,
    )
    _number(
        value["guidance_scale"],
        "guidance_scale",
        minimum=0,
        maximum=100,
    )
    resolution = _bounded_text(
        value["resolution"], "resolution", maximum_bytes=32,
    )
    _validated_resolution(resolution, "resolution")
    _integer(
        value["seed"], "seed", minimum=-1, maximum=9_223_372_036_854_775_807,
    )
    if "flow_shift" in value:
        _number(value["flow_shift"], "flow_shift", minimum=0, maximum=20)
    if "self_refiner_setting" in value:
        if value["self_refiner_setting"] not in (0, 1, 2) or type(
            value["self_refiner_setting"],
        ) is not int:
            raise GenerationPresetError("self_refiner_setting must be 0, 1, or 2")
    if "stage2_steps" in value:
        _integer(
            value["stage2_steps"], "stage2_steps", minimum=1, maximum=100,
        )
    if "tea_cache" in value:
        _integer(value["tea_cache"], "tea_cache", minimum=0, maximum=10)
    delivery_resolution = value.get("delivery_resolution")
    delivery_fit = value.get("delivery_fit")
    if delivery_resolution is not None or delivery_fit is not None:
        if type(delivery_resolution) is not str or type(delivery_fit) is not str:
            raise GenerationPresetError("delivery settings must be strings")
        if not delivery_resolution and not delivery_fit:
            pass
        elif (
            _RESOLUTION_RE.fullmatch(delivery_resolution) is None
            or delivery_fit not in {"upscale_exact", "center_crop"}
        ):
            raise GenerationPresetError("delivery settings are invalid")
    custom = value.get("custom_settings")
    if custom is not None:
        if type(custom) is not dict or not set(custom).issubset(
            _CUSTOM_SETTING_KEYS,
        ):
            raise GenerationPresetError(
                "custom_settings contain unsupported or private fields",
            )
        for key, item in custom.items():
            if key in {
                "h3_spectrum_profile", "h3_lightx2v_profile", "h3_turbo_profile",
            }:
                if type(item) is not str or _TECHNICAL_ID_RE.fullmatch(item) is None:
                    raise GenerationPresetError(f"{key} is invalid")
            elif key == "h3_attention_engine":
                if type(item) is not str or item not in {"sdpa", "sol_attn", "sage2"}:
                    raise GenerationPresetError("h3_attention_engine is invalid")
            elif key == "h3_sol_tau":
                _number(item, key, minimum=0.5, maximum=2.5)
            elif key == "h3_sol_dense_steps":
                _integer(item, key, minimum=0, maximum=100)
            elif key == "h3_sol_dense_blocks":
                _integer(item, key, minimum=1, maximum=128)
            elif key == "h3_sol_min_tokens":
                _integer(item, key, minimum=1, maximum=16_777_216)
    normalized = _plain_json(value, counter=[0])
    try:
        encoded = _canonical(normalized)
    except (OverflowError, TypeError, ValueError) as error:
        raise GenerationPresetError("params cannot be serialized safely") from error
    if len(encoded) > MAX_PARAMS_BYTES:
        raise GenerationPresetLimitError("params exceed their serialized bound")
    return normalized


def _normalize_lora_weights(value: Any) -> dict[str, list[int | float]]:
    if type(value) is not dict or len(value) > 64:
        raise GenerationPresetError("lora_weights must be a bounded plain object")
    result: dict[str, list[int | float]] = {}
    for raw_name, raw_weights in value.items():
        name = _bounded_text(raw_name, "LoRA weight key", maximum_bytes=512)
        if type(raw_weights) is not list or not 1 <= len(raw_weights) <= 3:
            raise GenerationPresetError("LoRA weights must contain 1 to 3 phases")
        weights: list[int | float] = []
        for weight in raw_weights:
            if type(weight) not in (int, float):
                raise GenerationPresetError("LoRA weights must be numbers")
            if (
                abs(weight) > 10
                or (type(weight) is float and not math.isfinite(weight))
            ):
                raise GenerationPresetError("LoRA weight is invalid")
            weights.append(weight)
        result[name] = weights
    return result


def _normalize_preset(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise GenerationPresetError(
            "preset fields are incomplete, unsupported, or private",
        )
    profile_version = value.get("profile_version")
    if profile_version is None:
        expected_keys = _LEGACY_PRESET_KEYS
    elif profile_version == 2 and type(profile_version) is int:
        expected_keys = _V2_PRESET_KEYS
    else:
        raise GenerationPresetError("generation profile version is unsupported")
    if set(value) != expected_keys:
        raise GenerationPresetError(
            "preset fields are incomplete, unsupported, or private",
        )
    name = _bounded_text(value["name"], "preset name", maximum_bytes=256)
    if name != name.strip():
        raise GenerationPresetError("preset name must not have outer whitespace")
    mode = value["mode"]
    if type(mode) is not str or mode not in _MODES:
        raise GenerationPresetError("generation mode is invalid")
    model_type = _bounded_text(
        value["model_type"], "model type", maximum_bytes=256,
    )
    if profile_version == 2 and _TECHNICAL_ID_RE.fullmatch(model_type) is None:
        raise GenerationPresetError("model type is invalid")
    raw_loras = value["activated_loras"]
    if type(raw_loras) is not list or len(raw_loras) > 64:
        raise GenerationPresetError("activated_loras must be a bounded plain list")
    activated_loras = [
        _bounded_text(item, "LoRA identifier", maximum_bytes=512)
        for item in raw_loras
    ]
    if len(set(activated_loras)) != len(activated_loras):
        raise GenerationPresetError("activated_loras must not contain duplicates")
    multipliers = _bounded_text(
        value["loras_multipliers"],
        "LoRA multipliers",
        maximum_bytes=4_096,
        allow_empty=True,
    )
    spatial_upsampling = _bounded_text(
        value["spatial_upsampling"],
        "spatial upsampling",
        maximum_bytes=128,
        allow_empty=True,
    )
    if spatial_upsampling and _TECHNICAL_ID_RE.fullmatch(spatial_upsampling) is None:
        raise GenerationPresetError("spatial upsampling is invalid")
    lora_weights = _normalize_lora_weights(value["lora_weights"])
    if not activated_loras:
        if multipliers or lora_weights:
            raise GenerationPresetError(
                "empty activated_loras require empty multiplier and weight state",
            )
    else:
        if set(lora_weights) != set(activated_loras):
            raise GenerationPresetError(
                "LoRA weight keys must exactly match activated_loras",
            )
        phase_counts = {len(weights) for weights in lora_weights.values()}
        if len(phase_counts) != 1:
            raise GenerationPresetError("all active LoRAs must use the same phases")
        phase_count = next(iter(phase_counts))
        multiplier_tokens = multipliers.split(" ")
        if (
            multipliers != " ".join(multiplier_tokens)
            or len(multiplier_tokens) != len(activated_loras)
        ):
            raise GenerationPresetError(
                "LoRA multipliers must exactly match activated_loras",
            )
        for lora_name, token in zip(activated_loras, multiplier_tokens):
            phase_tokens = token.split(";")
            if len(phase_tokens) != phase_count:
                raise GenerationPresetError(
                    "LoRA multiplier phases must match lora_weights",
                )
            parsed: list[float] = []
            for phase_token in phase_tokens:
                if _MULTIPLIER_RE.fullmatch(phase_token) is None:
                    raise GenerationPresetError("LoRA multiplier is invalid")
                try:
                    phase_value = float(phase_token)
                except (OverflowError, ValueError):
                    raise GenerationPresetError("LoRA multiplier is invalid") from None
                if not math.isfinite(phase_value) or not -10 <= phase_value <= 10:
                    raise GenerationPresetError("LoRA multiplier is invalid")
                parsed.append(phase_value)
            if any(
                not math.isclose(expected, actual, rel_tol=0, abs_tol=1e-9)
                for expected, actual in zip(lora_weights[lora_name], parsed)
            ):
                raise GenerationPresetError(
                    "LoRA multipliers and lora_weights do not agree",
                )
    normalized = {
        "name": name,
        "mode": mode,
        "model_type": model_type,
        "activated_loras": activated_loras,
        "loras_multipliers": multipliers,
        "lora_weights": lora_weights,
        "spatial_upsampling": spatial_upsampling,
        "params": (
            _normalize_v2_params(value["params"])
            if profile_version == 2
            else _normalize_params(value["params"])
        ),
    }
    if profile_version == 2:
        normalized["profile_version"] = 2
        normalized["ui_settings"] = _normalize_ui_settings(value["ui_settings"])
    return normalized


def _preset_keys(value: Mapping[str, Any]) -> frozenset[str]:
    return _V2_PRESET_KEYS if value.get("profile_version") == 2 else _LEGACY_PRESET_KEYS


def _empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "next_sequence": 1, "scopes": {}}


class GenerationPresetStore:
    """Atomic preset persistence below ``<runtime_root>/presets``."""

    _locks_guard = threading.Lock()
    _path_locks: ClassVar[dict[str, threading.RLock]] = {}

    def __init__(
        self,
        runtime_root: str | os.PathLike[str],
        *,
        scope_key: bytes | str,
        max_state_bytes: int = MAX_STATE_BYTES,
        max_records: int = MAX_RECORDS,
        clock: Any = time.time,
    ) -> None:
        raw = os.fspath(runtime_root)
        if not raw or "\0" in raw:
            raise GenerationPresetError("preset runtime root is invalid")
        candidate = Path(raw).expanduser().absolute()
        try:
            candidate_info = candidate.lstat()
            if not stat.S_ISDIR(candidate_info.st_mode) or candidate.is_symlink():
                raise ValueError
            root = candidate.resolve(strict=True)
            root_info = root.stat()
        except (OSError, ValueError):
            raise GenerationPresetError("preset runtime root is invalid") from None
        if type(max_state_bytes) is not int or not 4_096 <= max_state_bytes <= MAX_STATE_BYTES:
            raise GenerationPresetError("preset state byte bound is invalid")
        if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
            raise GenerationPresetError("preset record bound is invalid")
        if not callable(clock):
            raise GenerationPresetError("preset clock is invalid")
        self.runtime_root = root
        self._root_owner = getattr(root_info, "st_uid", None)
        self._scope_key = _scope_secret(scope_key)
        self.directory = root / "presets"
        self.path = self.directory / _STATE_FILENAME
        self.lock_path = self.directory / _LOCK_FILENAME
        self.max_state_bytes = max_state_bytes
        self.max_records = max_records
        self._clock = clock
        path_key = os.path.normcase(str(self.path))
        with self._locks_guard:
            self._thread_lock = self._path_locks.setdefault(
                path_key, threading.RLock(),
            )

    def _ensure_directory(self) -> None:
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise GenerationPresetIntegrityError(
                "preset storage directory is unavailable",
            ) from error
        try:
            named = self.directory.lstat()
            resolved = self.directory.resolve(strict=True)
        except OSError as error:
            raise GenerationPresetIntegrityError(
                "preset storage directory is unavailable",
            ) from error
        if not stat.S_ISDIR(named.st_mode) or resolved.parent != self.runtime_root:
            raise GenerationPresetIntegrityError("preset storage directory is unsafe")
        if os.name == "nt":
            return
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        try:
            descriptor = os.open(self.directory, flags)
            opened = os.fstat(descriptor)
            after = self.directory.lstat()
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
                or (
                    self._root_owner is not None
                    and getattr(opened, "st_uid", None) != self._root_owner
                )
            ):
                raise GenerationPresetIntegrityError(
                    "preset storage directory ownership is unsafe",
                )
            if stat.S_IMODE(opened.st_mode) != 0o700:
                os.fchmod(descriptor, 0o700)
                os.fsync(descriptor)
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
                    raise GenerationPresetIntegrityError(
                        "preset storage directory mode is unsafe",
                    )
        except GenerationPresetIntegrityError:
            raise
        except OSError as error:
            raise GenerationPresetIntegrityError(
                "preset storage directory cannot be secured",
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _secure_lock_file(self) -> None:
        if os.name == "nt":
            return
        try:
            before = self.lock_path.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise GenerationPresetIntegrityError(
                "preset storage lease path is unavailable",
            ) from error
        descriptor = -1
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            after = self.lock_path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
                or (
                    self._root_owner is not None
                    and getattr(opened, "st_uid", None) != self._root_owner
                )
            ):
                raise GenerationPresetIntegrityError(
                    "preset storage lease path is unsafe",
                )
            if stat.S_IMODE(opened.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                    raise GenerationPresetIntegrityError(
                        "preset storage lease mode is unsafe",
                    )
        except GenerationPresetIntegrityError:
            raise
        except OSError as error:
            raise GenerationPresetIntegrityError(
                "preset storage lease path cannot be secured",
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_directory()
        with self._thread_lock:
            try:
                with exclusive_file_lease(self.lock_path):
                    self._secure_lock_file()
                    yield
            except (ExclusiveLeaseError, OSError) as error:
                raise GenerationPresetIntegrityError(
                    "preset storage lease is unavailable",
                ) from error

    def _safe_existing_file(self) -> os.stat_result | None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise GenerationPresetIntegrityError(
                "preset state is unavailable",
            ) from error
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise GenerationPresetIntegrityError("preset state path is unsafe")
        if (
            os.name != "nt"
            and self._root_owner is not None
            and getattr(info, "st_uid", None) != self._root_owner
        ):
            raise GenerationPresetIntegrityError("preset state ownership is unsafe")
        return info

    def _read_unlocked(self) -> dict[str, Any]:
        before = self._safe_existing_file()
        if before is None:
            return _empty_state()
        if before.st_size > self.max_state_bytes:
            raise GenerationPresetIntegrityError("preset state exceeds its bound")
        descriptor = -1
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            after = self.path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
            ):
                raise GenerationPresetIntegrityError(
                    "preset state changed unsafely",
                )
            if os.name != "nt" and stat.S_IMODE(opened.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                opened = os.fstat(descriptor)
                if stat.S_IMODE(opened.st_mode) != 0o600:
                    raise GenerationPresetIntegrityError(
                        "preset state mode is unsafe",
                    )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(self.max_state_bytes + 1)
            if len(raw) > self.max_state_bytes:
                raise GenerationPresetIntegrityError(
                    "preset state exceeds its bound",
                )
            envelope = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("non-finite JSON number"),
                ),
            )
        except GenerationPresetIntegrityError:
            raise
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            raise GenerationPresetIntegrityError("preset state is unreadable") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if type(envelope) is not dict or set(envelope) != {"state", "state_sha256"}:
            raise GenerationPresetIntegrityError("preset state envelope is invalid")
        state = envelope["state"]
        checksum = envelope["state_sha256"]
        if (
            type(state) is not dict
            or type(checksum) is not str
            or _HEX64_RE.fullmatch(checksum) is None
        ):
            raise GenerationPresetIntegrityError("preset state envelope is invalid")
        try:
            expected = hashlib.sha256(_canonical(state)).hexdigest()
        except (OverflowError, TypeError, ValueError) as error:
            raise GenerationPresetIntegrityError(
                "preset state cannot be canonicalized safely",
            ) from error
        if not hmac.compare_digest(checksum, expected):
            raise GenerationPresetIntegrityError("preset state checksum failed")
        self._validate_state(state)
        return state

    def _write_unlocked(self, state: Mapping[str, Any]) -> None:
        self._validate_state(state)
        try:
            envelope = {
                "state": state,
                "state_sha256": hashlib.sha256(_canonical(state)).hexdigest(),
            }
            encoded = _canonical(envelope)
        except (OverflowError, TypeError, ValueError) as error:
            raise GenerationPresetIntegrityError(
                "preset state cannot be serialized safely",
            ) from error
        if len(encoded) > self.max_state_bytes:
            raise GenerationPresetLimitError(
                "preset state would exceed its serialized bound",
            )
        descriptor = -1
        temporary = ""
        replaced = False
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.directory,
            )
            if callable(getattr(os, "fchmod", None)):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._safe_existing_file()
            os.replace(temporary, self.path)
            replaced = True
            if os.name != "nt":
                directory_descriptor = os.open(
                    self.directory,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except GenerationPresetError:
            raise
        except OSError as error:
            if replaced:
                raise GenerationPresetCommitIndeterminate(
                    "preset mutation committed but directory sync failed",
                ) from error
            raise GenerationPresetIntegrityError(
                "preset state could not be published",
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                if temporary:
                    os.unlink(temporary)
            except OSError:
                pass

    def _validate_state(self, state: Any) -> None:
        try:
            if type(state) is not dict or set(state) != {
                "schema_version", "next_sequence", "scopes",
            }:
                raise ValueError
            if state["schema_version"] != SCHEMA_VERSION:
                raise ValueError
            next_sequence = state["next_sequence"]
            scopes = state["scopes"]
            if (
                type(next_sequence) is not int
                or not 1 <= next_sequence <= 9_007_199_254_740_991
            ):
                raise ValueError
            if type(scopes) is not dict or len(scopes) > self.max_records:
                raise ValueError
            total = 0
            sequences: set[int] = set()
            for scope_key, records in scopes.items():
                if type(scope_key) is not str or _HEX64_RE.fullmatch(scope_key) is None:
                    raise ValueError
                if (
                    type(records) is not list
                    or not records
                    or len(records) > MAX_RECORDS_PER_SCOPE
                ):
                    raise ValueError
                identifiers: set[str] = set()
                previous: tuple[int, str] | None = None
                for record in records:
                    if type(record) is not dict:
                        raise ValueError
                    preset_keys = _preset_keys(record)
                    if set(record) != preset_keys | _STORED_METADATA_KEYS:
                        raise ValueError
                    normalized = _normalize_preset(
                        {key: record[key] for key in preset_keys},
                    )
                    if any(record[key] != normalized[key] for key in preset_keys):
                        raise ValueError
                    identifier = _preset_id(record["id"])
                    created_at = record["created_at"]
                    sequence = record["sequence"]
                    if (
                        type(created_at) not in (int, float)
                        or created_at < 0
                        or created_at > 9_007_199_254_740_991
                        or (
                            type(created_at) is float
                            and not math.isfinite(created_at)
                        )
                        or type(sequence) is not int
                        or not 1 <= sequence <= 9_007_199_254_740_991
                        or identifier in identifiers
                        or sequence in sequences
                    ):
                        raise ValueError
                    order = (sequence, identifier)
                    if previous is not None and order <= previous:
                        raise ValueError
                    previous = order
                    identifiers.add(identifier)
                    sequences.add(sequence)
                    total += 1
            if total > self.max_records or (sequences and next_sequence <= max(sequences)):
                raise ValueError
        except GenerationPresetError:
            raise GenerationPresetIntegrityError(
                "preset state contains an invalid record",
            ) from None
        except (KeyError, OverflowError, TypeError, ValueError):
            raise GenerationPresetIntegrityError("preset state shape is invalid") from None

    @staticmethod
    def _public(record: Mapping[str, Any]) -> dict[str, Any]:
        keys = _preset_keys(record) | _RECORD_METADATA_KEYS
        public = deepcopy({key: record[key] for key in keys})
        public["revision"] = hashlib.sha256(_canonical(record)).hexdigest()
        return public

    def list(self, *, account_scope: str, project_scope: str) -> list[dict[str, Any]]:
        """Return only presets in the exact caller-supplied scope."""
        scope_key = _scope_digest(
            self._scope_key, account_scope, project_scope,
        )
        with self._locked():
            state = self._read_unlocked()
            return [self._public(record) for record in state["scopes"].get(scope_key, [])]

    def create(
        self,
        *,
        account_scope: str,
        project_scope: str,
        preset: Mapping[str, Any],
        preset_id: str,
    ) -> dict[str, Any]:
        """Create or idempotently replay one preset in an exact scope."""
        scope_key = _scope_digest(
            self._scope_key, account_scope, project_scope,
        )
        normalized = _normalize_preset(preset)
        if preset_id is None:
            raise GenerationPresetError(
                "caller-stable preset_id is required for durable idempotency",
            )
        preset_id = _preset_id(preset_id)
        with self._locked():
            state = self._read_unlocked()
            records = state["scopes"].get(scope_key, [])
            if preset_id is not None:
                existing = next(
                    (record for record in records if record["id"] == preset_id),
                    None,
                )
                if existing is not None:
                    normalized_keys = _preset_keys(normalized)
                    if (
                        _preset_keys(existing) == normalized_keys
                        and all(existing[key] == normalized[key] for key in normalized_keys)
                    ):
                        return self._public(existing)
                    raise GenerationPresetConflict(
                        "preset id is already bound to different settings",
                    )
            if len(records) >= MAX_RECORDS_PER_SCOPE:
                raise GenerationPresetLimitError("preset scope record bound reached")
            total = sum(len(items) for items in state["scopes"].values())
            if total >= self.max_records:
                raise GenerationPresetLimitError("preset store record bound reached")
            created_at = self._clock()
            if (
                type(created_at) not in (int, float)
                or created_at < 0
                or created_at > 9_007_199_254_740_991
                or (
                    type(created_at) is float
                    and not math.isfinite(created_at)
                )
            ):
                raise GenerationPresetError("preset clock returned an invalid value")
            record = {
                "id": preset_id,
                **normalized,
                "created_at": created_at,
                "sequence": state["next_sequence"],
            }
            state["next_sequence"] += 1
            records.append(record)
            state["scopes"][scope_key] = records
            self._write_unlocked(state)
            return self._public(record)

    def update(
        self,
        *,
        account_scope: str,
        project_scope: str,
        preset_id: str,
        preset: Mapping[str, Any],
        expected_revision: str,
    ) -> dict[str, Any]:
        """Replace one exact-scope preset using its public revision."""
        scope_key = _scope_digest(
            self._scope_key, account_scope, project_scope,
        )
        identifier = _preset_id(preset_id)
        normalized = _normalize_preset(preset)
        if (
            type(expected_revision) is not str
            or _HEX64_RE.fullmatch(expected_revision) is None
        ):
            raise GenerationPresetError("expected preset revision is invalid")
        with self._locked():
            state = self._read_unlocked()
            records = state["scopes"].get(scope_key, [])
            existing_index = next(
                (
                    index
                    for index, record in enumerate(records)
                    if record["id"] == identifier
                ),
                None,
            )
            if existing_index is None:
                raise GenerationPresetConflict(
                    "preset changed or is unavailable",
                )
            existing = records[existing_index]
            normalized_keys = _preset_keys(normalized)
            if (
                _preset_keys(existing) == normalized_keys
                and all(existing[key] == normalized[key] for key in normalized_keys)
            ):
                return self._public(existing)
            current_revision = hashlib.sha256(_canonical(existing)).hexdigest()
            if not hmac.compare_digest(current_revision, expected_revision):
                raise GenerationPresetConflict(
                    "preset changed or is unavailable",
                )
            replacement = {
                "id": existing["id"],
                **normalized,
                "created_at": existing["created_at"],
                "sequence": state["next_sequence"],
            }
            state["next_sequence"] += 1
            records[existing_index] = replacement
            records.sort(key=lambda record: (record["sequence"], record["id"]))
            self._write_unlocked(state)
            return self._public(replacement)

    def delete(
        self,
        *,
        account_scope: str,
        project_scope: str,
        preset_id: str,
    ) -> bool:
        """Delete within one exact scope; mismatches are indistinguishable."""
        scope_key = _scope_digest(
            self._scope_key, account_scope, project_scope,
        )
        identifier = _preset_id(preset_id)
        with self._locked():
            state = self._read_unlocked()
            records = state["scopes"].get(scope_key)
            if records is None:
                return False
            retained = [record for record in records if record["id"] != identifier]
            if len(retained) == len(records):
                return False
            if retained:
                state["scopes"][scope_key] = retained
            else:
                del state["scopes"][scope_key]
            self._write_unlocked(state)
            return True
