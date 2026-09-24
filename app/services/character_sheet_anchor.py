"""Resolve a persisted, server-proven FLUX reference as a Character Sheet anchor.

ProjectAssetStore deliberately keeps free-form provenance metadata, so those
fields cannot establish that media came from generation.  The reference-pack
publisher must attach the HMAC proof created here; this resolver fails closed
when the proof, model registry check, or stored bytes do not agree.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from services.project_assets import ProjectAssetStore


ANCHOR_PROOF_SCHEMA_VERSION = 1
CHARACTER_SHEET_ANCHOR_SCHEMA_VERSION = 3
_PROOF_DOMAIN = b"maestro.character-sheet.reference-pack-anchor.v1\0"
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROOF_KEYS = frozenset({
    "schema_version", "source_model_id", "source_sha256", "job_id",
    "output_index", "basename", "signature",
})
_BINDING_KEYS = frozenset({
    "project_id", "workspace_id", "asset_id", "variant_id",
    "output_index", "basename", "source_model_id", "source_sha256",
    "job_id",
})
_MAX_ANCHOR_BYTES = 512 * 1024 * 1024


class CharacterSheetAnchorError(ValueError):
    """The selected output cannot be established as a safe FLUX anchor."""


def _fail(reason: str) -> None:
    raise CharacterSheetAnchorError(f"character_sheet_anchor_{reason}")


def _secret_bytes(key: bytes | bytearray | memoryview | str) -> bytes:
    if isinstance(key, str):
        secret = key.encode("utf-8")
    elif isinstance(key, (bytes, bytearray, memoryview)):
        secret = bytes(key)
    else:
        _fail("proof_key_invalid")
    if len(secret) < 32:
        _fail("proof_key_invalid")
    return secret


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _PROJECT_ID.fullmatch(value):
        _fail(f"{field}_invalid")
    return value


def _model_id(value: object) -> str:
    if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
        _fail("model_id_invalid")
    return value


def _basename(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or len(value.encode("utf-8")) > 255
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        _fail("basename_invalid")
    return value


def _sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail("digest_invalid")
    return value


def _normalize_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _BINDING_KEYS:
        _fail("proof_binding_invalid")
    index = value["output_index"]
    if type(index) is not int or index < 0:
        _fail("proof_binding_invalid")
    return {
        "project_id": _identifier(value["project_id"], "project_id"),
        "workspace_id": _identifier(value["workspace_id"], "workspace_id"),
        "asset_id": _identifier(value["asset_id"], "asset_id"),
        "variant_id": _identifier(value["variant_id"], "variant_id"),
        "output_index": index,
        "basename": _basename(value["basename"]),
        "source_model_id": _model_id(value["source_model_id"]),
        "source_sha256": _sha256(value["source_sha256"]),
        "job_id": _identifier(value["job_id"], "job_id"),
    }


def _canonical_payload(binding: Mapping[str, Any]) -> bytes:
    payload = {
        "schema_version": ANCHOR_PROOF_SCHEMA_VERSION,
        **_normalize_binding(binding),
    }
    return _PROOF_DOMAIN + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def create_reference_pack_anchor_proof(
    key: bytes | bytearray | memoryview | str,
    *,
    project_id: str,
    workspace_id: str,
    asset_id: str,
    variant_id: str,
    output_index: int,
    basename: str,
    source_model_id: str,
    source_sha256: str,
    job_id: str,
) -> dict[str, Any]:
    """Sign the generation identity before ProjectAssetStore assigns output IDs."""
    binding = _normalize_binding({
        "project_id": project_id,
        "workspace_id": workspace_id,
        "asset_id": asset_id,
        "variant_id": variant_id,
        "output_index": output_index,
        "basename": basename,
        "source_model_id": source_model_id,
        "source_sha256": source_sha256,
        "job_id": job_id,
    })
    signature = hmac.new(
        _secret_bytes(key), _canonical_payload(binding), hashlib.sha256,
    ).hexdigest()
    return {
        "schema_version": ANCHOR_PROOF_SCHEMA_VERSION,
        "source_model_id": binding["source_model_id"],
        "source_sha256": binding["source_sha256"],
        "job_id": binding["job_id"],
        "output_index": binding["output_index"],
        "basename": binding["basename"],
        "signature": signature,
    }


def verify_reference_pack_anchor_proof(
    key: bytes | bytearray | memoryview | str,
    proof: object,
    *,
    expected_binding: Mapping[str, Any],
) -> bool:
    """Verify a proof against IDs and media facts resolved by the server."""
    try:
        if not isinstance(proof, Mapping) or set(proof) != _PROOF_KEYS:
            return False
        if (
            type(proof["schema_version"]) is not int
            or proof["schema_version"] != ANCHOR_PROOF_SCHEMA_VERSION
        ):
            return False
        signature = proof["signature"]
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            return False
        binding = _normalize_binding(expected_binding)
        proof_claims = {
            "source_model_id": proof["source_model_id"],
            "source_sha256": proof["source_sha256"],
            "job_id": proof["job_id"],
            "output_index": proof["output_index"],
            "basename": proof["basename"],
        }
        if (
            proof_claims["source_model_id"] != binding["source_model_id"]
            or proof_claims["source_sha256"] != binding["source_sha256"]
            or proof_claims["job_id"] != binding["job_id"]
            or type(proof_claims["output_index"]) is not int
            or proof_claims["output_index"] != binding["output_index"]
            or proof_claims["basename"] != binding["basename"]
        ):
            return False
        expected_signature = hmac.new(
            _secret_bytes(key), _canonical_payload(binding), hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected_signature)
    except (CharacterSheetAnchorError, TypeError, ValueError, UnicodeError):
        return False


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _check_server_metadata(
    variant: Mapping[str, Any],
    output: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> None:
    variant_provenance = _mapping(variant.get("provenance"))
    if not variant_provenance or variant_provenance.get("kind") != "generated":
        _fail("not_generated")
    variant_provenance_details = _mapping(variant_provenance.get("details"))
    variant_metadata = _mapping(variant.get("metadata"))
    output_metadata = _mapping(output.get("metadata"))
    if not variant_provenance_details or not variant_metadata or not output_metadata:
        _fail("provenance_metadata_missing")
    if variant_provenance_details.get("service") != "reference_sheets":
        _fail("reference_metadata_unverified")

    variant_reference = _mapping(variant_metadata.get("reference_pack"))
    output_reference = _mapping(output_metadata.get("reference_pack"))
    job_metadata = _mapping(variant_metadata.get("job"))
    lineage = _mapping(output_metadata.get("lineage"))
    if not variant_reference or not output_reference or not job_metadata or not lineage:
        _fail("reference_metadata_missing")

    job_id = proof.get("job_id")
    job_claims = (
        variant_provenance_details.get("job_id"),
        job_metadata.get("id"),
        lineage.get("parent_job_id"),
    )
    if any(not isinstance(value, str) or value != job_id for value in job_claims):
        _fail("job_id_mismatch")

    model_id = proof.get("source_model_id")
    required_model_claims = (
        variant_reference.get("generation_model"),
        job_metadata.get("generation_model"),
        output_reference.get("model"),
    )
    if any(not isinstance(claim, str) for claim in required_model_claims):
        _fail("model_metadata_missing")
    optional_model_claims = (
        variant_reference.get("model"),
        output_reference.get("generation_model"),
        output_metadata.get("generation_model"),
    )
    if any(
        claim != model_id for claim in required_model_claims
    ) or any(
        claim is not None and (not isinstance(claim, str) or claim != model_id)
        for claim in optional_model_claims
    ):
        _fail("model_claim_mismatch")

    for provenance in (
        _mapping(output_metadata.get("provenance")),
        _mapping(output_reference.get("provenance")),
    ):
        if provenance and provenance.get("kind") not in (None, "generated"):
            _fail("not_generated")


def _file_stat_tuple(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _require_regular_single_link(value: os.stat_result) -> None:
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        _fail("source_file_invalid")


def _open_output_no_follow(
    store: ProjectAssetStore,
    project_id: str,
    workspace_id: str,
    relative_parts: tuple[str, ...],
    resolved_path: Path,
) -> tuple[int, Path]:
    root = Path(store.root)
    if not root.is_absolute():
        _fail("storage_root_invalid")
    path_parts = (
        "projects", project_id, "workspaces", workspace_id, *relative_parts,
    )
    expected_path = root.joinpath(*path_parts)
    try:
        root_real = root.resolve(strict=True)
        resolved_real = resolved_path.resolve(strict=True)
        expected_real = expected_path.resolve(strict=True)
    except OSError as error:
        raise CharacterSheetAnchorError(
            "character_sheet_anchor_source_unavailable"
        ) from error
    if (
        root_real != root
        or resolved_path != expected_path
        or resolved_real != expected_real
    ):
        _fail("source_path_invalid")

    required_flags = ("O_NOFOLLOW", "O_DIRECTORY")
    if any(not hasattr(os, flag) for flag in required_flags) or os.open not in os.supports_dir_fd:
        _fail("safe_file_open_unavailable")
    nofollow = os.O_NOFOLLOW
    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory = os.O_RDONLY | os.O_DIRECTORY | nofollow | cloexec
    leaf_flags = os.O_RDONLY | nofollow | cloexec

    root_fd: int | None = None
    current_fd: int | None = None
    leaf_fd: int | None = None
    try:
        root_fd = os.open(root, directory)
        current_fd = root_fd
        for part in path_parts[:-1]:
            next_fd = os.open(part, directory, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        leaf_fd = os.open(path_parts[-1], leaf_flags, dir_fd=current_fd)
        _require_regular_single_link(os.fstat(leaf_fd))
        return leaf_fd, expected_path
    except (OSError, ValueError) as error:
        if leaf_fd is not None:
            os.close(leaf_fd)
        if isinstance(error, CharacterSheetAnchorError):
            raise
        raise CharacterSheetAnchorError(
            "character_sheet_anchor_source_unavailable"
        ) from error
    finally:
        if current_fd is not None and current_fd != root_fd:
            os.close(current_fd)
        if root_fd is not None:
            os.close(root_fd)


def _stable_file_sha256(
    store: ProjectAssetStore,
    project_id: str,
    workspace_id: str,
    asset_id: str,
    variant_id: str,
    relative_path: str,
    resolved_path: Path,
) -> str:
    pure_path = PurePosixPath(relative_path)
    relative_parts = pure_path.parts
    if (
        pure_path.is_absolute()
        or len(relative_parts) != 4
        or relative_parts[0] != "media"
        or relative_parts[1] != asset_id
        or relative_parts[2] != variant_id
        or any(part in {"", ".", ".."} for part in relative_parts)
    ):
        _fail("source_path_invalid")

    descriptor, path = _open_output_no_follow(
        store, project_id, workspace_id, relative_parts, resolved_path,
    )
    try:
        before = os.fstat(descriptor)
        _require_regular_single_link(before)
        if before.st_size < 0 or before.st_size > _MAX_ANCHOR_BYTES:
            _fail("source_file_too_large")
        try:
            path_before = os.lstat(path)
        except OSError as error:
            raise CharacterSheetAnchorError(
                "character_sheet_anchor_source_unavailable"
            ) from error
        _require_regular_single_link(path_before)
        if _file_stat_tuple(before) != _file_stat_tuple(path_before):
            _fail("source_file_changed")

        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if size > _MAX_ANCHOR_BYTES:
                _fail("source_file_too_large")

        after = os.fstat(descriptor)
        try:
            path_after = os.lstat(path)
        except OSError as error:
            raise CharacterSheetAnchorError(
                "character_sheet_anchor_source_unavailable"
            ) from error
        _require_regular_single_link(after)
        _require_regular_single_link(path_after)
        if (
            size != before.st_size
            or _file_stat_tuple(before) != _file_stat_tuple(after)
            or _file_stat_tuple(before) != _file_stat_tuple(path_after)
        ):
            _fail("source_file_changed")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def resolve_character_sheet_anchor(
    store: ProjectAssetStore,
    key: bytes | bytearray | memoryview | str,
    *,
    project_id: str,
    workspace_id: str,
    asset_id: str,
    variant_id: str,
    output_id: str,
    is_verified_flux_model: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Resolve one kept generated output to a schema-v3 anchor.

    ``source_path`` is an internal server path for the local image workflow and
    must never be included in a public response.  Callers remain responsible
    for session/project authorization before invoking this function.
    """
    if workspace_id != "main":
        _fail("workspace_invalid")
    project_id = _identifier(project_id, "project_id")
    workspace_id = _identifier(workspace_id, "workspace_id")
    asset_id = _identifier(asset_id, "asset_id")
    variant_id = _identifier(variant_id, "variant_id")
    output_id = _identifier(output_id, "output_id")
    try:
        asset = store.get_asset(project_id, workspace_id, asset_id)
    except Exception as error:
        raise CharacterSheetAnchorError(
            "character_sheet_anchor_asset_unavailable"
        ) from error
    if not isinstance(asset, Mapping) or asset.get("id") != asset_id:
        _fail("asset_scope_mismatch")

    variants = asset.get("variants")
    if not isinstance(variants, list):
        _fail("variant_missing")
    matches = [
        item for item in variants
        if isinstance(item, Mapping) and item.get("id") == variant_id
    ]
    if len(matches) != 1:
        _fail("variant_ambiguous")
    variant = matches[0]
    if variant.get("variant_type") != "reference_pack" or variant.get("status") != "kept":
        _fail("variant_not_kept_reference_pack")

    outputs = variant.get("outputs")
    if not isinstance(outputs, list):
        _fail("output_missing")
    indexed_outputs = [
        (index, item)
        for index, item in enumerate(outputs)
        if isinstance(item, Mapping) and item.get("id") == output_id
    ]
    if len(indexed_outputs) != 1:
        _fail("output_ambiguous")
    output_index, output = indexed_outputs[0]
    output_metadata = _mapping(output.get("metadata"))
    output_reference = _mapping(
        output_metadata.get("reference_pack") if output_metadata else None
    )
    proof = _mapping(
        output_reference.get("anchor_provenance") if output_reference else None
    )
    if not proof:
        _fail("proof_missing")

    relative_path = output.get("relative_path")
    filename = output.get("filename")
    basename = _basename(filename)
    if not isinstance(relative_path, str):
        _fail("output_path_invalid")
    relative_parts = PurePosixPath(relative_path).parts
    if not relative_parts or relative_parts[-1] != basename:
        _fail("output_path_mismatch")

    source_model_id = _model_id(proof.get("source_model_id"))
    source_sha256 = _sha256(proof.get("source_sha256"))
    job_id = _identifier(proof.get("job_id"), "job_id")
    binding = {
        "project_id": project_id,
        "workspace_id": workspace_id,
        "asset_id": asset_id,
        "variant_id": variant_id,
        "output_index": output_index,
        "basename": basename,
        "source_model_id": source_model_id,
        "source_sha256": source_sha256,
        "job_id": job_id,
    }
    if not verify_reference_pack_anchor_proof(
        key, proof, expected_binding=binding,
    ):
        _fail("proof_invalid")

    if asset.get("asset_type") != "character":
        _fail("asset_type_invalid")
    _check_server_metadata(variant, output, proof)
    if is_verified_flux_model is None:
        _fail("model_registry_unavailable")
    try:
        model_verified = is_verified_flux_model(source_model_id)
    except Exception as error:
        raise CharacterSheetAnchorError(
            "character_sheet_anchor_model_registry_unavailable"
        ) from error
    if model_verified is not True:
        _fail("source_model_not_flux")

    try:
        resolved_path = Path(store.resolve_output_path(
            project_id, workspace_id, relative_path,
        ))
    except Exception as error:
        raise CharacterSheetAnchorError(
            "character_sheet_anchor_source_unavailable"
        ) from error
    actual_sha256 = _stable_file_sha256(
        store, project_id, workspace_id, asset_id, variant_id,
        relative_path, resolved_path,
    )
    if not hmac.compare_digest(actual_sha256, source_sha256):
        _fail("source_digest_mismatch")

    anchor = {
        "schema_version": CHARACTER_SHEET_ANCHOR_SCHEMA_VERSION,
        "project_id": project_id,
        "anchor_id": output_id,
        "kind": "generated",
        "sha256": actual_sha256,
        "source_model_id": source_model_id,
        "source_model_family": "flux",
    }
    return {"anchor": anchor, "source_path": str(resolved_path)}


__all__ = [
    "ANCHOR_PROOF_SCHEMA_VERSION",
    "CHARACTER_SHEET_ANCHOR_SCHEMA_VERSION",
    "CharacterSheetAnchorError",
    "create_reference_pack_anchor_proof",
    "resolve_character_sheet_anchor",
    "verify_reference_pack_anchor_proof",
]
