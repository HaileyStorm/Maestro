"""Plan-only descriptor for MiniMax's hosted H3 Regenerate-2K stage.

The official MiniMax H3 README at revision
``6da473b48daf91e5aebfb56451f8a0b116348df5`` describes Regenerate-2K as a
second, hosted H3 pass over a 768p base result and its original context.  The
module is not part of the open-source release.  This file therefore describes
and validates an explicitly selected future hosted hand-off, but deliberately
contains no client, authentication, request, response, or execution code.

Only commitments are retained for prompts and source media.  Raw prompts,
media paths, credentials, provider URLs, and private identifiers are outside
this contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from services.h3_profiles import H3_NATIVE_RESOLUTIONS


H3_REGENERATE_2K_KIND: Final = "minimax_h3_regenerate_2k"
H3_REGENERATE_2K_SCHEMA: Final = "maestro.h3.regenerate-2k.descriptor"
H3_REGENERATE_2K_VERSION: Final = 1
H3_REGENERATE_2K_SOURCE_REVISION: Final = (
    "6da473b48daf91e5aebfb56451f8a0b116348df5"
)

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SOURCE_ROLE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+ -]{0,239}")
_SOURCE_REVISION = re.compile(r"[0-9a-f]{40}")
_MAX_SOURCE_MEDIA = 64
_MAX_ARTIFACT_BYTES = 16 * 1024**4
_MAX_FPS = 240
_MIN_AUDIO_SAMPLE_RATE_HZ = 8_000
_MAX_AUDIO_SAMPLE_RATE_HZ = 384_000
_MAX_AUDIO_CHANNELS = 32

_TOP_LEVEL_FIELDS = frozenset(
    {
        "kind",
        "schema",
        "version",
        "official_source",
        "hosted_only",
        "execution_available",
        "automatic_fallback",
        "source_stage",
        "target",
        "prompt_commitments",
        "source_media",
        "verified_base_artifact",
        "hosted_opt_in",
        "plan_sha256",
    }
)
_OFFICIAL_SOURCE_FIELDS = frozenset({"document", "revision"})
_SOURCE_STAGE_FIELDS = frozenset(
    {
        "kind",
        "producer",
        "official_context_ir",
        "width",
        "height",
        "fps",
        "audio_sample_rate_hz",
        "audio_channels",
    }
)
_TARGET_FIELDS = frozenset({"kind", "name"})
_PROMPT_FIELDS = frozenset(
    {"semantic_prompt_sha256", "executable_prompt_sha256"}
)
_SOURCE_MEDIA_FIELDS = frozenset({"role", "sha256", "size_bytes"})
_BASE_ARTIFACT_FIELDS = frozenset(
    {"basename", "sha256", "size_bytes", "sidecar_sha256"}
)
_HOSTED_OPT_IN_FIELDS = frozenset(
    {
        "explicit_opt_in",
        "disclosure_revision_sha256",
        "opt_in_revision_sha256",
    }
)

class H3Regenerate2KError(ValueError):
    """Raised when a hosted Regenerate-2K descriptor is not exact."""


def _canonical_json(value: Mapping[str, object] | Sequence[object]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise H3Regenerate2KError("descriptor must be canonical plain JSON") from error


def _sha256(value: Mapping[str, object] | Sequence[object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise H3Regenerate2KError(
            f"{field} must be one lowercase sha256-prefixed digest"
        )
    return value


def _exact_dict(
    value: object,
    fields: frozenset[str],
    *,
    field: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise H3Regenerate2KError(f"{field} fields are not exact")
    return value


def _plain_size(value: object, *, field: str) -> int:
    if type(value) is not int or not 0 < value <= _MAX_ARTIFACT_BYTES:
        raise H3Regenerate2KError(f"{field} must be a positive bounded integer")
    return value


def _validate_source_media(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or not 1 <= len(value) <= _MAX_SOURCE_MEDIA:
        raise H3Regenerate2KError("source_media must be a non-empty bounded list")
    validated: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        media = _exact_dict(
            item,
            _SOURCE_MEDIA_FIELDS,
            field=f"source_media[{index}]",
        )
        role = media["role"]
        if type(role) is not str or _SOURCE_ROLE.fullmatch(role) is None:
            raise H3Regenerate2KError(
                f"source_media[{index}].role must be a bounded machine role"
            )
        _digest(media["sha256"], field=f"source_media[{index}].sha256")
        _plain_size(media["size_bytes"], field=f"source_media[{index}].size_bytes")
        validated.append(media)
    return validated


def _copy_source_media_input(value: object) -> list[dict[str, object]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise H3Regenerate2KError("source_media must be a sequence of mappings")
    copied: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise H3Regenerate2KError(
                f"source_media[{index}] must be a mapping"
            )
        try:
            copied.append(dict(item))
        except (TypeError, ValueError) as error:
            raise H3Regenerate2KError(
                f"source_media[{index}] could not be copied"
            ) from error
    return copied


def _source_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise H3Regenerate2KError(
            f"source_stage.{field} must be an exact bounded integer"
        )
    return value


def _validate_source_stage(value: object) -> dict[str, Any]:
    source_stage = _exact_dict(value, _SOURCE_STAGE_FIELDS, field="source_stage")
    if (
        source_stage["kind"] != "context_ir_formatted"
        or source_stage["producer"] != "local_h3_native"
        or source_stage["official_context_ir"] is not False
    ):
        raise H3Regenerate2KError("source_stage identity drifted")
    width = _source_integer(
        source_stage["width"], field="width", minimum=32, maximum=8192
    )
    height = _source_integer(
        source_stage["height"], field="height", minimum=32, maximum=8192
    )
    if f"{width}x{height}" not in H3_NATIVE_RESOLUTIONS:
        raise H3Regenerate2KError(
            "source_stage dimensions must be a reviewed native H3 resolution"
        )
    _source_integer(source_stage["fps"], field="fps", minimum=1, maximum=_MAX_FPS)
    _source_integer(
        source_stage["audio_sample_rate_hz"],
        field="audio_sample_rate_hz",
        minimum=_MIN_AUDIO_SAMPLE_RATE_HZ,
        maximum=_MAX_AUDIO_SAMPLE_RATE_HZ,
    )
    _source_integer(
        source_stage["audio_channels"],
        field="audio_channels",
        minimum=1,
        maximum=_MAX_AUDIO_CHANNELS,
    )
    return source_stage


def _validate_without_plan_digest(value: object) -> dict[str, Any]:
    descriptor = _exact_dict(value, _TOP_LEVEL_FIELDS, field="descriptor")

    if descriptor["kind"] != H3_REGENERATE_2K_KIND:
        raise H3Regenerate2KError("descriptor kind drifted")
    if descriptor["schema"] != H3_REGENERATE_2K_SCHEMA:
        raise H3Regenerate2KError("descriptor schema drifted")
    if type(descriptor["version"]) is not int or descriptor["version"] != 1:
        raise H3Regenerate2KError("descriptor version drifted")
    if descriptor["hosted_only"] is not True:
        raise H3Regenerate2KError("Regenerate-2K must remain hosted-only")
    if descriptor["execution_available"] is not False:
        raise H3Regenerate2KError("Regenerate-2K execution is not available")
    if descriptor["automatic_fallback"] is not False:
        raise H3Regenerate2KError("Regenerate-2K cannot have an automatic fallback")

    official = _exact_dict(
        descriptor["official_source"],
        _OFFICIAL_SOURCE_FIELDS,
        field="official_source",
    )
    if official["document"] != "MiniMax-H3 README":
        raise H3Regenerate2KError("official source document drifted")
    revision = official["revision"]
    if (
        type(revision) is not str
        or _SOURCE_REVISION.fullmatch(revision) is None
        or revision != H3_REGENERATE_2K_SOURCE_REVISION
    ):
        raise H3Regenerate2KError("official source revision drifted")

    _validate_source_stage(descriptor["source_stage"])

    target = _exact_dict(descriptor["target"], _TARGET_FIELDS, field="target")
    if target != {
        "kind": "hosted_regenerate_2k",
        "name": "hosted Regenerate-2K",
    }:
        raise H3Regenerate2KError("target is not the reviewed hosted Regenerate-2K stage")

    prompts = _exact_dict(
        descriptor["prompt_commitments"],
        _PROMPT_FIELDS,
        field="prompt_commitments",
    )
    _digest(prompts["semantic_prompt_sha256"], field="semantic_prompt_sha256")
    _digest(prompts["executable_prompt_sha256"], field="executable_prompt_sha256")
    _validate_source_media(descriptor["source_media"])

    base = _exact_dict(
        descriptor["verified_base_artifact"],
        _BASE_ARTIFACT_FIELDS,
        field="verified_base_artifact",
    )
    basename = base["basename"]
    if (
        type(basename) is not str
        or _BASENAME.fullmatch(basename) is None
        or basename in {".", ".."}
        or "/" in basename
        or "\\" in basename
    ):
        raise H3Regenerate2KError("base artifact must contain a safe basename only")
    _digest(base["sha256"], field="verified_base_artifact.sha256")
    _plain_size(base["size_bytes"], field="verified_base_artifact.size_bytes")
    _digest(base["sidecar_sha256"], field="verified_base_artifact.sidecar_sha256")

    opt_in = _exact_dict(
        descriptor["hosted_opt_in"],
        _HOSTED_OPT_IN_FIELDS,
        field="hosted_opt_in",
    )
    if opt_in["explicit_opt_in"] is not True:
        raise H3Regenerate2KError("explicit hosted Regenerate-2K opt-in is required")
    disclosure_revision = _digest(
        opt_in["disclosure_revision_sha256"],
        field="hosted_opt_in.disclosure_revision_sha256",
    )
    opt_in_revision = _digest(
        opt_in["opt_in_revision_sha256"],
        field="hosted_opt_in.opt_in_revision_sha256",
    )
    if not hmac.compare_digest(disclosure_revision, opt_in_revision):
        raise H3Regenerate2KError(
            "hosted opt-in must commit to the disclosed revision exactly"
        )
    _digest(descriptor["plan_sha256"], field="plan_sha256")
    return descriptor


def validate_h3_regenerate_2k_descriptor(value: object) -> dict[str, Any]:
    """Validate, reseal, and return an independent canonical descriptor copy."""

    descriptor = _validate_without_plan_digest(value)
    expected_document = {key: item for key, item in descriptor.items() if key != "plan_sha256"}
    expected_digest = _sha256(expected_document)
    if not hmac.compare_digest(descriptor["plan_sha256"], expected_digest):
        raise H3Regenerate2KError("Regenerate-2K plan digest drifted")
    return json.loads(_canonical_json(descriptor).decode("ascii"))


def build_h3_regenerate_2k_descriptor(
    *,
    semantic_prompt_sha256: str,
    executable_prompt_sha256: str,
    source_media: Sequence[Mapping[str, object]],
    source_width: int,
    source_height: int,
    source_fps: int,
    source_audio_sample_rate_hz: int,
    source_audio_channels: int,
    base_artifact_basename: str,
    base_artifact_sha256: str,
    base_artifact_size_bytes: int,
    base_sidecar_sha256: str,
    disclosure_revision_sha256: str,
    opt_in_revision_sha256: str,
    explicit_opt_in: bool,
) -> dict[str, Any]:
    """Build one inert hosted hand-off descriptor from precomputed commitments."""

    document: dict[str, Any] = {
        "kind": H3_REGENERATE_2K_KIND,
        "schema": H3_REGENERATE_2K_SCHEMA,
        "version": H3_REGENERATE_2K_VERSION,
        "official_source": {
            "document": "MiniMax-H3 README",
            "revision": H3_REGENERATE_2K_SOURCE_REVISION,
        },
        "hosted_only": True,
        "execution_available": False,
        "automatic_fallback": False,
        "source_stage": {
            "kind": "context_ir_formatted",
            "producer": "local_h3_native",
            "official_context_ir": False,
            "width": source_width,
            "height": source_height,
            "fps": source_fps,
            "audio_sample_rate_hz": source_audio_sample_rate_hz,
            "audio_channels": source_audio_channels,
        },
        "target": {
            "kind": "hosted_regenerate_2k",
            "name": "hosted Regenerate-2K",
        },
        "prompt_commitments": {
            "semantic_prompt_sha256": semantic_prompt_sha256,
            "executable_prompt_sha256": executable_prompt_sha256,
        },
        "source_media": _copy_source_media_input(source_media),
        "verified_base_artifact": {
            "basename": base_artifact_basename,
            "sha256": base_artifact_sha256,
            "size_bytes": base_artifact_size_bytes,
            "sidecar_sha256": base_sidecar_sha256,
        },
        "hosted_opt_in": {
            "explicit_opt_in": explicit_opt_in,
            "disclosure_revision_sha256": disclosure_revision_sha256,
            "opt_in_revision_sha256": opt_in_revision_sha256,
        },
    }
    document["plan_sha256"] = _sha256(document)
    return validate_h3_regenerate_2k_descriptor(document)


def public_h3_regenerate_2k_projection(value: object) -> dict[str, object]:
    """Return only coarse availability facts from a valid private descriptor."""

    validate_h3_regenerate_2k_descriptor(value)
    return {
        "kind": H3_REGENERATE_2K_KIND,
        "hosted_only": True,
        "availability": "unavailable",
        "execution_available": False,
        "automatic_fallback": False,
    }


__all__ = [
    "H3_REGENERATE_2K_KIND",
    "H3_REGENERATE_2K_SCHEMA",
    "H3_REGENERATE_2K_SOURCE_REVISION",
    "H3_REGENERATE_2K_VERSION",
    "H3Regenerate2KError",
    "build_h3_regenerate_2k_descriptor",
    "public_h3_regenerate_2k_projection",
    "validate_h3_regenerate_2k_descriptor",
]
