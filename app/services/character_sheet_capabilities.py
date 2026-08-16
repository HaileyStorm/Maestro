"""Strict, content-free Character Sheet capability projection.

This module exposes only server-authored workflow choices.  It cannot make a
profile available, mint execution authority, inspect project content, or carry
anchors, prompts, paths, or artifact digests across the API boundary.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from services.character_sheet_workflow import character_sheet_profile_catalog


CAPABILITY_SCHEMA_VERSION = 1
CAPABILITY_ID = "character_sheet"
MAX_CAPABILITY_BYTES = 16_384
MAX_STRING_LENGTH = 128


class CharacterSheetCapabilityError(ValueError):
    """Raised when a Character Sheet capability document is not exact."""


_PROFILE_SPECS = (
    {
        "id": "quad_flux2_klein",
        "label": "Quad — FLUX.2 Klein",
        "order": 0,
        "status": "requires_server_authorization",
        "available": False,
        "executable": False,
        "experimental": False,
        "default": True,
        "requires_explicit_selection": False,
    },
    {
        "id": "quad_krea2",
        "label": "Quad — Krea 2",
        "order": 1,
        "status": "legal_blocked",
        "available": False,
        "executable": False,
        "experimental": False,
        "default": False,
        "requires_explicit_selection": True,
    },
    {
        "id": "dynamic_krea2_experimental",
        "label": "Dynamic — Krea 2 (experimental)",
        "order": 2,
        "status": "legal_blocked",
        "available": False,
        "executable": False,
        "experimental": True,
        "default": False,
        "requires_explicit_selection": True,
    },
    {
        "id": "triple_flux2_klein",
        "label": "Triple — FLUX.2 Klein",
        "order": 3,
        "status": "later_unavailable",
        "available": False,
        "executable": False,
        "experimental": False,
        "default": False,
        "requires_explicit_selection": True,
    },
)

_WORKFLOW_STEPS = (
    {
        "id": "anchor",
        "label": "Create the anchor image",
        "order": 0,
        "required": True,
    },
    {
        "id": "local_vlm_review",
        "label": "Review locally with the VLM",
        "order": 1,
        "required": True,
    },
    {
        "id": "qwen_image_edit_repair",
        "label": "Repair with Qwen Image Edit",
        "order": 2,
        "required": False,
        "condition": "review_finds_failed_roles",
    },
)

_PROFILE_SOURCE_KEYS = frozenset(
    {
        "id",
        "label",
        "available",
        "executable",
        "experimental",
        "default",
        "requires_explicit_selection",
        "status",
    }
)


def _expected_source_profiles() -> tuple[dict[str, Any], ...]:
    return tuple(
        {key: value for key, value in profile.items() if key != "order"}
        for profile in _PROFILE_SPECS
    )


def _assert_workflow_catalog_matches() -> None:
    source = character_sheet_profile_catalog()
    expected = _expected_source_profiles()
    if type(source) is not tuple or len(source) != len(expected):
        raise CharacterSheetCapabilityError(
            "Character Sheet workflow profiles do not match the public capability contract."
        )
    for profile, expected_profile in zip(source, expected, strict=True):
        if type(profile) is not dict or not _PROFILE_SOURCE_KEYS.issubset(profile):
            raise CharacterSheetCapabilityError(
                "Character Sheet workflow profiles do not match the public capability contract."
            )
        for key, expected_value in expected_profile.items():
            actual_value = profile[key]
            if type(actual_value) is not type(expected_value) or actual_value != expected_value:
                raise CharacterSheetCapabilityError(
                    "Character Sheet workflow profiles do not match the public capability contract."
                )


def character_sheet_capability_projection() -> dict[str, Any]:
    """Return the sole deterministic, server-authored public projection."""
    _assert_workflow_catalog_matches()
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "capability_id": CAPABILITY_ID,
        "server_authored": True,
        "selection": {
            "default_profile_id": "quad_flux2_klein",
            "client_may_enable_profiles": False,
        },
        "workflow": [deepcopy(step) for step in _WORKFLOW_STEPS],
        "profiles": [deepcopy(profile) for profile in _PROFILE_SPECS],
    }


def canonical_character_sheet_capabilities() -> bytes:
    """Encode the exact projection as deterministic ASCII JSON."""
    return json.dumps(
        character_sheet_capability_projection(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CharacterSheetCapabilityError(
                "Character Sheet capability contains a duplicate key."
            )
        value[key] = item
    return value


def _require_bounded_plain_json(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise CharacterSheetCapabilityError("Character Sheet capability is too deeply nested.")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        if len(value) > MAX_STRING_LENGTH:
            raise CharacterSheetCapabilityError(
                "Character Sheet capability contains an oversized string."
            )
        return
    if type(value) is list:
        if len(value) > 16:
            raise CharacterSheetCapabilityError(
                "Character Sheet capability contains an oversized list."
            )
        for item in value:
            _require_bounded_plain_json(item, depth=depth + 1)
        return
    if type(value) is dict:
        if len(value) > 16:
            raise CharacterSheetCapabilityError(
                "Character Sheet capability contains too many keys."
            )
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 64:
                raise CharacterSheetCapabilityError(
                    "Character Sheet capability contains an invalid key."
                )
            _require_bounded_plain_json(item, depth=depth + 1)
        return
    raise CharacterSheetCapabilityError(
        "Character Sheet capability must contain exact plain JSON values."
    )


def decode_character_sheet_capabilities(payload: str | bytes) -> dict[str, Any]:
    """Decode only the exact server projection; reject client-authored variants."""
    if type(payload) is bytes:
        if len(payload) > MAX_CAPABILITY_BYTES:
            raise CharacterSheetCapabilityError("Character Sheet capability is too large.")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CharacterSheetCapabilityError(
                "Character Sheet capability is not UTF-8 JSON."
            ) from exc
    elif type(payload) is str:
        try:
            encoded_payload = payload.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CharacterSheetCapabilityError(
                "Character Sheet capability is not valid Unicode JSON."
            ) from exc
        if len(encoded_payload) > MAX_CAPABILITY_BYTES:
            raise CharacterSheetCapabilityError("Character Sheet capability is too large.")
        text = payload
    else:
        raise CharacterSheetCapabilityError(
            "Character Sheet capability payload must be text or bytes."
        )

    try:
        decoded = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except CharacterSheetCapabilityError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CharacterSheetCapabilityError(
            "Character Sheet capability is not valid JSON."
        ) from exc

    _require_bounded_plain_json(decoded)
    expected = character_sheet_capability_projection()
    if type(decoded) is not dict or decoded != expected:
        raise CharacterSheetCapabilityError(
            "Character Sheet capability is not the exact server-authored projection."
        )
    if canonical_character_sheet_capabilities() != json.dumps(
        decoded,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii"):
        raise CharacterSheetCapabilityError(
            "Character Sheet capability cannot be encoded canonically."
        )
    return expected
