"""Server-only identity and CPU media helpers for the Quad Character Sheet.

This module does not load a model, grant project access, or inspect creative
content.  The launch adapter supplies independently checked model, LoRA, and
terms facts, then uses the signed identity to bind one accepted anchor and its
four fixed output roles across queueing and publication.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from services.character_sheet_quad import (
    QUAD_FLUX_BASE_MODEL,
    QUAD_FLUX_LORA_FILENAME,
    QUAD_FLUX_LORA_REVISION,
    QUAD_FLUX_LORA_SHA256,
    QUAD_FLUX_LORA_SIZE,
    QUAD_FLUX_RECIPE_ID,
)
from services.host_terms import (
    BFL_FLUX2_REVIEW_TERM,
    CHARACTER_SHEET_QUAD_CREATOR_TERM,
)


EXECUTOR_SCHEMA_VERSION = 1
PROFILE_ID = "quad_flux2_klein"
WORKSPACE_ID = "main"
CANVAS_WIDTH = 1024
CANVAS_HEIGHT = 1024
PANEL_WIDTH = CANVAS_WIDTH // 2
PANEL_HEIGHT = CANVAS_HEIGHT // 2
PANEL_ROLES = (
    "face_closeup",
    "front_full_body",
    "side_full_body",
    "back_full_body",
)
PANEL_LAYOUT = (
    (0, 0, PANEL_WIDTH, PANEL_HEIGHT),
    (PANEL_WIDTH, 0, PANEL_WIDTH, PANEL_HEIGHT),
    (0, PANEL_HEIGHT, PANEL_WIDTH, PANEL_HEIGHT),
    (PANEL_WIDTH, PANEL_HEIGHT, PANEL_WIDTH, PANEL_HEIGHT),
)

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_DOMAIN = b"maestro.character-sheet.quad-job-identity.v1\0"
_PUBLICATION_DOMAIN = b"maestro.character-sheet.quad-publication.v1\0"
_ANCHOR_KEYS = frozenset({
    "schema_version", "project_id", "anchor_id", "kind", "sha256",
    "source_model_id", "source_model_family",
})
_RUNTIME_KEYS = frozenset({
    "base_model_id", "base_model_family", "model_artifact_commitment",
    "lora", "terms", "schedule",
})
_IDENTITY_KEYS = frozenset({
    "schema_version", "profile_id", "project_id", "workspace_id",
    "job_id", "asset_id", "variant_id", "generation_job_id", "anchor",
    "anchor_variant_id", "resources", "seed", "role_outputs",
    "identity_seal",
})
_ROLE_OUTPUT_KEYS = frozenset({
    "role", "output_index", "slot_id", "basename", "x", "y",
    "width", "height",
})
_PUBLICATION_KEYS = frozenset({
    "schema_version", "identity_seal", "variant_id", "panels", "signature",
})
_PANEL_DIGEST_KEYS = frozenset({"role", "output_index", "sha256"})


class CharacterSheetExecutorError(ValueError):
    """The fixed Quad execution identity or its local artifacts changed."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise CharacterSheetExecutorError(
            "Character Sheet execution identity is not canonical JSON."
        ) from error


def commitment_digest(value: Any) -> str:
    """Return a deterministic SHA-256 commitment for server-resolved facts."""
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _secret(key: bytes | bytearray | memoryview | str) -> bytes:
    if isinstance(key, str):
        value = key.encode("utf-8")
    elif isinstance(key, (bytes, bytearray, memoryview)):
        value = bytes(key)
    else:
        raise CharacterSheetExecutorError("Character Sheet proof key is invalid.")
    if len(value) < 32:
        raise CharacterSheetExecutorError("Character Sheet proof key is invalid.")
    return value


def _token(value: Any, *, field: str) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise CharacterSheetExecutorError(f"Character Sheet {field} is invalid.")
    return value


def _safe_id(value: Any, *, field: str, maximum: int = 64) -> str:
    if (
        type(value) is not str
        or len(value) > maximum
        or _SAFE_ID.fullmatch(value) is None
    ):
        raise CharacterSheetExecutorError(f"Character Sheet {field} is invalid.")
    return value


def _digest(value: Any, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CharacterSheetExecutorError(
            f"Character Sheet {field} must be a lowercase SHA-256 digest."
        )
    return value


def _normalize_anchor(value: Any, *, project_id: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _ANCHOR_KEYS:
        raise CharacterSheetExecutorError("Character Sheet anchor schema is invalid.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 3:
        raise CharacterSheetExecutorError("Character Sheet anchor version is invalid.")
    if value["project_id"] != project_id:
        raise CharacterSheetExecutorError("Character Sheet anchor project changed.")
    if value["kind"] != "generated":
        raise CharacterSheetExecutorError("Character Sheet requires a generated anchor.")
    if value["source_model_family"] != "flux":
        raise CharacterSheetExecutorError("Character Sheet requires a verified FLUX anchor.")
    return {
        "schema_version": 3,
        "project_id": _safe_id(value["project_id"], field="anchor project"),
        "anchor_id": _safe_id(value["anchor_id"], field="anchor id"),
        "kind": "generated",
        "sha256": _digest(value["sha256"], field="anchor digest"),
        "source_model_id": _token(value["source_model_id"], field="anchor model"),
        "source_model_family": "flux",
    }


def _normalize_runtime(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _RUNTIME_KEYS:
        raise CharacterSheetExecutorError("Character Sheet resource snapshot is invalid.")
    if value["base_model_id"] != QUAD_FLUX_BASE_MODEL:
        raise CharacterSheetExecutorError("Quad requires FLUX.2 Klein 9B.")
    if value["base_model_family"] != "flux":
        raise CharacterSheetExecutorError("Quad requires the registered FLUX model family.")
    model_artifact_commitment = _digest(
        value["model_artifact_commitment"], field="model artifact commitment",
    )

    lora = value["lora"]
    if type(lora) is not dict or set(lora) != {
        "recipe_id", "revision", "filename", "size", "sha256",
    } or lora != {
        "recipe_id": QUAD_FLUX_RECIPE_ID,
        "revision": QUAD_FLUX_LORA_REVISION,
        "filename": QUAD_FLUX_LORA_FILENAME,
        "size": QUAD_FLUX_LORA_SIZE,
        "sha256": QUAD_FLUX_LORA_SHA256,
    }:
        raise CharacterSheetExecutorError("Quad LoRA artifact identity changed.")

    terms = value["terms"]
    if type(terms) is not dict or set(terms) != {"statuses", "commitment"}:
        raise CharacterSheetExecutorError("Character Sheet terms snapshot is invalid.")
    statuses = terms["statuses"]
    expected_term_ids = (
        CHARACTER_SHEET_QUAD_CREATOR_TERM,
        BFL_FLUX2_REVIEW_TERM,
    )
    if type(statuses) is not list or len(statuses) != len(expected_term_ids):
        raise CharacterSheetExecutorError("Character Sheet terms snapshot is invalid.")
    clean_statuses = []
    for expected_term, item in zip(expected_term_ids, statuses, strict=True):
        if type(item) is not dict or set(item) != {"term", "version", "accepted"}:
            raise CharacterSheetExecutorError("Character Sheet terms snapshot is invalid.")
        if (
            item["term"] != expected_term
            or type(item["version"]) not in (int, str)
            or type(item["version"]) is bool
            or item["accepted"] is not True
        ):
            raise CharacterSheetExecutorError("Quad creator and FLUX terms must be accepted.")
        clean_statuses.append({
            "term": expected_term,
            "version": item["version"],
            "accepted": True,
        })
    terms_commitment = _digest(terms["commitment"], field="terms commitment")
    expected_terms_commitment = commitment_digest(clean_statuses)
    if not hmac.compare_digest(terms_commitment, expected_terms_commitment):
        raise CharacterSheetExecutorError("Character Sheet terms snapshot changed.")

    schedule = value["schedule"]
    if type(schedule) is not dict or set(schedule) != {
        "model", "steps", "guidance", "commitment",
    }:
        raise CharacterSheetExecutorError("Character Sheet schedule snapshot is invalid.")
    if (
        schedule["model"] != QUAD_FLUX_BASE_MODEL
        or type(schedule["steps"]) is not int
        or not 1 <= schedule["steps"] <= 200
        or type(schedule["guidance"]) not in (int, float)
        or type(schedule["guidance"]) is bool
        or not math.isfinite(float(schedule["guidance"]))
        or not 0 <= float(schedule["guidance"]) <= 30
    ):
        raise CharacterSheetExecutorError("Character Sheet schedule snapshot is invalid.")
    schedule_values = {
        "model": QUAD_FLUX_BASE_MODEL,
        "steps": schedule["steps"],
        "guidance": float(schedule["guidance"]),
    }
    schedule_commitment = _digest(
        schedule["commitment"], field="schedule commitment",
    )
    if not hmac.compare_digest(
        schedule_commitment, commitment_digest(schedule_values),
    ):
        raise CharacterSheetExecutorError("Character Sheet schedule snapshot changed.")

    return {
        "base_model_id": QUAD_FLUX_BASE_MODEL,
        "base_model_family": "flux",
        "model_artifact_commitment": model_artifact_commitment,
        "lora": {
            "recipe_id": QUAD_FLUX_RECIPE_ID,
            "revision": QUAD_FLUX_LORA_REVISION,
            "filename": QUAD_FLUX_LORA_FILENAME,
            "size": QUAD_FLUX_LORA_SIZE,
            "sha256": QUAD_FLUX_LORA_SHA256,
        },
        "terms": {
            "statuses": clean_statuses,
            "commitment": terms_commitment,
        },
        "schedule": {
            **schedule_values,
            "commitment": schedule_commitment,
        },
    }


def build_quad_runtime_snapshot(
    *,
    model_id: Any,
    model_family: Any,
    model_artifact_commitment: Any,
    lora_sha256: Any,
    term_statuses: Any,
    schedule: Any,
) -> dict[str, Any]:
    """Bind exact server-checked model, LoRA, accepted terms, and schedule facts."""
    if model_id != QUAD_FLUX_BASE_MODEL or model_family != "flux":
        raise CharacterSheetExecutorError("Quad requires registered FLUX.2 Klein 9B.")
    if type(term_statuses) is not list:
        raise CharacterSheetExecutorError("Character Sheet terms are unavailable.")
    statuses = []
    for item in term_statuses:
        if type(item) is not dict or not {
            "term", "version", "accepted",
        }.issubset(item):
            raise CharacterSheetExecutorError("Character Sheet terms are unavailable.")
        statuses.append({
            "term": item["term"],
            "version": item["version"],
            "accepted": item["accepted"],
        })
    terms_commitment = commitment_digest(statuses)
    if type(schedule) is not dict or set(schedule) != {"model", "steps", "guidance"}:
        raise CharacterSheetExecutorError("Character Sheet schedule is invalid.")
    snapshot = {
        "base_model_id": model_id,
        "base_model_family": model_family,
        "model_artifact_commitment": model_artifact_commitment,
        "lora": {
            "recipe_id": QUAD_FLUX_RECIPE_ID,
            "revision": QUAD_FLUX_LORA_REVISION,
            "filename": QUAD_FLUX_LORA_FILENAME,
            "size": QUAD_FLUX_LORA_SIZE,
            "sha256": lora_sha256,
        },
        "terms": {
            "statuses": statuses,
            "commitment": terms_commitment,
        },
        "schedule": {
            "model": schedule["model"],
            "steps": schedule["steps"],
            "guidance": float(schedule["guidance"])
            if type(schedule["guidance"]) in (int, float)
            and type(schedule["guidance"]) is not bool
            else schedule["guidance"],
            "commitment": commitment_digest({
                "model": schedule["model"],
                "steps": schedule["steps"],
                "guidance": float(schedule["guidance"])
                if type(schedule["guidance"]) in (int, float)
                and type(schedule["guidance"]) is not bool
                else schedule["guidance"],
            }),
        },
    }
    return _normalize_runtime(snapshot)


def _role_outputs(job_id: str) -> list[dict[str, Any]]:
    outputs = []
    for index, (role, (x, y, width, height)) in enumerate(
        zip(PANEL_ROLES, PANEL_LAYOUT, strict=True),
    ):
        outputs.append({
            "role": role,
            "output_index": index,
            "slot_id": f"{job_id}_panel_{index + 1}",
            "basename": f"{job_id}_{role}.png",
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        })
    return outputs


def build_quad_job_identity(
    key: bytes | bytearray | memoryview | str,
    *,
    project_id: Any,
    workspace_id: Any,
    job_id: Any,
    asset_id: Any,
    anchor: Any,
    anchor_variant_id: Any,
    resources: Any,
    seed: Any,
) -> dict[str, Any]:
    """Create the server-HMAC identity for one fixed four-role Quad job."""
    secret = _secret(key)
    clean_project_id = _safe_id(project_id, field="project id")
    if workspace_id != WORKSPACE_ID:
        raise CharacterSheetExecutorError("Character Sheet workspace must be main.")
    clean_job_id = _safe_id(job_id, field="job id", maximum=56)
    clean_asset_id = _safe_id(asset_id, field="asset id")
    clean_anchor = _normalize_anchor(anchor, project_id=clean_project_id)
    clean_anchor_variant_id = _safe_id(
        anchor_variant_id, field="anchor variant id",
    )
    clean_resources = _normalize_runtime(resources)
    if type(seed) is not int or not 0 <= seed <= (2**63) - 1:
        raise CharacterSheetExecutorError("Character Sheet seed is invalid.")
    variant_id = f"{clean_job_id}_pack_1"
    generation_job_id = f"{clean_job_id}_quad"
    _safe_id(variant_id, field="variant id")
    _safe_id(generation_job_id, field="generation job id")
    unsigned = {
        "schema_version": EXECUTOR_SCHEMA_VERSION,
        "profile_id": PROFILE_ID,
        "project_id": clean_project_id,
        "workspace_id": WORKSPACE_ID,
        "job_id": clean_job_id,
        "asset_id": clean_asset_id,
        "variant_id": variant_id,
        "generation_job_id": generation_job_id,
        "anchor": clean_anchor,
        "anchor_variant_id": clean_anchor_variant_id,
        "resources": clean_resources,
        "seed": seed,
        "role_outputs": _role_outputs(clean_job_id),
    }
    signature = hmac.new(
        secret, _IDENTITY_DOMAIN + _canonical_json(unsigned), hashlib.sha256,
    ).hexdigest()
    return {**unsigned, "identity_seal": signature}


def validate_quad_job_identity(
    key: bytes | bytearray | memoryview | str,
    value: Any,
    *,
    expected_project_id: Any = None,
    expected_asset_id: Any = None,
    expected_job_id: Any = None,
    current_resources: Any = None,
) -> dict[str, Any]:
    """Verify HMAC, closed role order, request scope, and current resources."""
    secret = _secret(key)
    if type(value) is not dict or set(value) != _IDENTITY_KEYS:
        raise CharacterSheetExecutorError("Character Sheet job identity is invalid.")
    identity_seal = value["identity_seal"]
    if type(identity_seal) is not str or _SHA256.fullmatch(identity_seal) is None:
        raise CharacterSheetExecutorError("Character Sheet job identity is invalid.")
    unsigned = {key: item for key, item in value.items() if key != "identity_seal"}
    expected_seal = hmac.new(
        secret, _IDENTITY_DOMAIN + _canonical_json(unsigned), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(identity_seal, expected_seal):
        raise CharacterSheetExecutorError("Character Sheet job identity signature changed.")

    project_id = _safe_id(value["project_id"], field="project id")
    asset_id = _safe_id(value["asset_id"], field="asset id")
    job_id = _safe_id(value["job_id"], field="job id", maximum=56)
    expected_role_outputs = _role_outputs(job_id)
    supplied_role_outputs = value["role_outputs"]
    role_outputs_valid = (
        type(supplied_role_outputs) is list
        and len(supplied_role_outputs) == len(expected_role_outputs)
    )
    if role_outputs_valid:
        for supplied, expected in zip(
            supplied_role_outputs, expected_role_outputs, strict=True,
        ):
            if (
                type(supplied) is not dict
                or set(supplied) != _ROLE_OUTPUT_KEYS
                or any(type(supplied.get(field)) is not type(expected[field])
                       for field in expected)
                or supplied != expected
            ):
                role_outputs_valid = False
                break
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != EXECUTOR_SCHEMA_VERSION
        or value["profile_id"] != PROFILE_ID
        or value["workspace_id"] != WORKSPACE_ID
        or _safe_id(value["anchor_variant_id"], field="anchor variant id")
            != value["anchor_variant_id"]
        or type(value["seed"]) is not int
        or not 0 <= value["seed"] <= (2**63) - 1
        or value["variant_id"] != f"{job_id}_pack_1"
        or value["generation_job_id"] != f"{job_id}_quad"
        or not role_outputs_valid
    ):
        raise CharacterSheetExecutorError("Character Sheet four-role identity changed.")
    anchor = _normalize_anchor(value["anchor"], project_id=project_id)
    resources = _normalize_runtime(value["resources"])
    if expected_project_id is not None and project_id != expected_project_id:
        raise CharacterSheetExecutorError("Character Sheet project scope changed.")
    if expected_asset_id is not None and asset_id != expected_asset_id:
        raise CharacterSheetExecutorError("Character Sheet asset scope changed.")
    if expected_job_id is not None and job_id != expected_job_id:
        raise CharacterSheetExecutorError("Character Sheet job identity changed.")
    if current_resources is not None:
        clean_current = _normalize_runtime(current_resources)
        if _canonical_json(resources) != _canonical_json(clean_current):
            raise CharacterSheetExecutorError("Character Sheet runtime resources changed.")
    return {**unsigned, "anchor": anchor, "resources": resources, "identity_seal": identity_seal}


def create_quad_publication_proof(
    key: bytes | bytearray | memoryview | str,
    *,
    identity: Any,
    panel_digests: Any,
) -> dict[str, Any]:
    """Bind the staged four role hashes to the admitted job identity."""
    secret = _secret(key)
    clean_identity = validate_quad_job_identity(key, identity)
    if type(panel_digests) is not list or len(panel_digests) != len(PANEL_ROLES):
        raise CharacterSheetExecutorError("Character Sheet publication panels are invalid.")
    panels = []
    for index, (role, value) in enumerate(zip(PANEL_ROLES, panel_digests, strict=True)):
        if type(value) is not dict or set(value) != _PANEL_DIGEST_KEYS:
            raise CharacterSheetExecutorError("Character Sheet publication panels are invalid.")
        if value["role"] != role or type(value["output_index"]) is not int or value["output_index"] != index:
            raise CharacterSheetExecutorError("Character Sheet publication role order changed.")
        panels.append({
            "role": role,
            "output_index": index,
            "sha256": _digest(value["sha256"], field="panel digest"),
        })
    unsigned = {
        "schema_version": EXECUTOR_SCHEMA_VERSION,
        "identity_seal": clean_identity["identity_seal"],
        "variant_id": clean_identity["variant_id"],
        "panels": panels,
    }
    signature = hmac.new(
        secret, _PUBLICATION_DOMAIN + _canonical_json(unsigned), hashlib.sha256,
    ).hexdigest()
    return {**unsigned, "signature": signature}


def verify_quad_publication_proof(
    key: bytes | bytearray | memoryview | str,
    proof: Any,
    *,
    identity: Any,
    panel_digests: Any,
) -> bool:
    try:
        secret = _secret(key)
        clean_identity = validate_quad_job_identity(key, identity)
        expected = create_quad_publication_proof(
            key, identity=clean_identity, panel_digests=panel_digests,
        )
        if type(proof) is not dict or set(proof) != _PUBLICATION_KEYS:
            return False
        supplied_signature = proof.get("signature")
        if type(supplied_signature) is not str or _SHA256.fullmatch(supplied_signature) is None:
            return False
        supplied_unsigned = {key: item for key, item in proof.items() if key != "signature"}
        expected_unsigned = {key: item for key, item in expected.items() if key != "signature"}
        expected_signature = hmac.new(
            secret, _PUBLICATION_DOMAIN + _canonical_json(expected_unsigned), hashlib.sha256,
        ).hexdigest()
        return bool(
            supplied_unsigned == expected_unsigned
            and hmac.compare_digest(supplied_signature, expected_signature)
        )
    except (CharacterSheetExecutorError, TypeError, ValueError, UnicodeError):
        return False


def crop_quad_sheet(
    key: bytes | bytearray | memoryview | str,
    source_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    identity: Any,
) -> list[dict[str, Any]]:
    """Crop one exact 1024-square generated QuadView into four role outputs."""
    clean_identity = validate_quad_job_identity(key, identity)
    requested_root = Path(output_dir)
    if requested_root.is_symlink():
        raise CharacterSheetExecutorError("Character Sheet output directory is unavailable.")
    root = requested_root.resolve(strict=True)
    if not root.is_dir():
        raise CharacterSheetExecutorError("Character Sheet output directory is unavailable.")
    source = Path(source_path)
    try:
        resolved_source = source.resolve(strict=True)
        source_stat = os.lstat(source)
        if (
            resolved_source.parent != root
            or stat.S_ISLNK(source_stat.st_mode)
            or not stat.S_ISREG(source_stat.st_mode)
            or source_stat.st_nlink != 1
        ):
            raise OSError("generated sheet is not a contained regular file")
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise OSError("generated sheet is unsafe")
            payload = bytearray()
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                payload.extend(block)
                if len(payload) > 64 * 1024 * 1024:
                    raise OSError("generated sheet is too large")
            after = os.fstat(descriptor)
            named_after = os.lstat(source)
            stable_fields = lambda item: (
                item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
                item.st_size, item.st_mtime_ns, item.st_ctime_ns,
            )
            if (
                stable_fields(before) != stable_fields(after)
                or stable_fields(before) != stable_fields(named_after)
                or len(payload) != before.st_size
            ):
                raise OSError("generated sheet changed while being read")
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CharacterSheetExecutorError(
            "Character Sheet generated sheet is unavailable or changed."
        ) from error

    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(payload)) as opened:
            if opened.size != (CANVAS_WIDTH, CANVAS_HEIGHT):
                raise CharacterSheetExecutorError(
                    "Quad FLUX output must be exactly 1024 by 1024 pixels."
                )
            image = opened.convert("RGB")
    except CharacterSheetExecutorError:
        raise
    except Exception as error:
        raise CharacterSheetExecutorError(
            "Character Sheet generated sheet is not a valid image."
        ) from error

    outputs = []
    for slot in clean_identity["role_outputs"]:
        x, y = slot["x"], slot["y"]
        width, height = slot["width"], slot["height"]
        path = root / slot["basename"]
        descriptor, temporary = tempfile.mkstemp(
            prefix=".character-sheet-", suffix=".tmp", dir=str(root),
        )
        os.close(descriptor)
        try:
            image.crop((x, y, x + width, y + height)).save(
                temporary, format="PNG",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        try:
            path_stat = os.lstat(path)
            if (
                stat.S_ISLNK(path_stat.st_mode)
                or not stat.S_ISREG(path_stat.st_mode)
                or path_stat.st_nlink != 1
            ):
                raise OSError("cropped panel is unsafe")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                before = os.fstat(handle.fileno())
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                after = os.fstat(handle.fileno())
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or after.st_nlink != 1
            ):
                raise OSError("cropped panel changed while being hashed")
        except OSError as error:
            raise CharacterSheetExecutorError(
                "Character Sheet cropped panel is unavailable or changed."
            ) from error
        outputs.append({
            "role": slot["role"],
            "output_index": slot["output_index"],
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "basename": slot["basename"],
            "path": str(path),
            "sha256": digest.hexdigest(),
        })
    return outputs


__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "CharacterSheetExecutorError",
    "EXECUTOR_SCHEMA_VERSION",
    "PANEL_LAYOUT",
    "PANEL_ROLES",
    "PROFILE_ID",
    "WORKSPACE_ID",
    "build_quad_job_identity",
    "build_quad_runtime_snapshot",
    "commitment_digest",
    "create_quad_publication_proof",
    "crop_quad_sheet",
    "validate_quad_job_identity",
    "verify_quad_publication_proof",
]
