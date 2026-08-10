"""Project-scoped reusable reference cards and their media variants.

The store deliberately owns persistence, path containment, and media copying,
but not authentication.  ``password_metadata`` is opaque JSON supplied by an
auth layer; this module neither accepts passwords nor encrypts local data.

One manifest is stored per project.  Assets are partitioned by workspace and
variant media is copied beneath the project directory.  Only relative media
paths are persisted, so moving the store does not leave stale absolute paths.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
from typing import Any, Iterable, Mapping, Optional
import uuid


SCHEMA_VERSION = 1
PROVENANCE_KINDS = frozenset({"imported", "typed", "generated"})
VARIANT_STATUSES = frozenset({"candidate", "kept", "rejected"})

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ProjectAssetError(RuntimeError):
    """Base error for project asset operations."""


class ProjectAssetNotFoundError(ProjectAssetError, KeyError):
    """Raised when an asset or variant does not exist in the requested scope."""


class ProjectAssetPersistenceError(ProjectAssetError):
    """Raised for malformed or unreadable persisted state."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _validate_id(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_ID_RE.fullmatch(value)
        or value.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(
            f"{field} must start with a letter or number and contain at most "
            "64 letters, numbers, hyphens, or underscores"
        )
    return value


def _validate_token(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not _SAFE_TOKEN_RE.fullmatch(value):
        raise ValueError(
            f"{field} must contain 1-64 letters, numbers, spaces, hyphens, or underscores"
        )
    return value


def _validate_basename(name: str) -> str:
    if (
        not name
        or name in {".", ".."}
        or name.startswith(".")
        or len(name) > 255
        or name.endswith((" ", "."))
        or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
    ):
        raise ValueError(f"Unsafe media basename: {name!r}")
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"Reserved media basename: {name!r}")
    return name


def _json_object(value: object, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    try:
        # Round-tripping both validates JSON compatibility and detaches caller
        # objects from the in-memory mutation performed by CRUD methods.
        return json.loads(json.dumps(dict(value), ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain only JSON-compatible values") from exc


def _normalize_tags(tags: Optional[Iterable[object]]) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, (str, bytes)):
        raise ValueError("tags must be a list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError("tags must be a list of strings")
        clean = tag.strip()
        if not clean or len(clean) > 64:
            raise ValueError("tags must be non-empty strings of at most 64 characters")
        folded = clean.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(clean)
    if len(result) > 100:
        raise ValueError("an asset may have at most 100 tags")
    return result


def _normalize_provenance(value: object, *, default: str = "typed") -> dict[str, Any]:
    if value is None:
        value = default
    if isinstance(value, str):
        kind = value
        details: dict[str, Any] = {}
    elif isinstance(value, Mapping):
        kind = value.get("kind")
        details = _json_object(value.get("details"), "provenance.details")
    else:
        raise ValueError("provenance must be a kind string or an object")
    if kind not in PROVENANCE_KINDS:
        allowed = ", ".join(sorted(PROVENANCE_KINDS))
        raise ValueError(f"provenance kind must be one of: {allowed}")
    return {"kind": kind, "details": details}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class ProjectAssetStore:
    """JSON-backed project asset repository.

    Parameters
    ----------
    storage_root:
        Root for manifests and copied variant media.
    allowed_source_roots:
        Directories from which uploaded/generated files may be imported.
        An empty allowlist permits metadata-only cards but rejects media copies.
    """

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(
        self,
        storage_root: os.PathLike[str] | str,
        allowed_source_roots: Iterable[os.PathLike[str] | str] = (),
    ) -> None:
        root = Path(storage_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("storage_root must be a real directory, not a symlink")
        self.root = root.resolve()

        sources: list[Path] = []
        for source_root in allowed_source_roots:
            candidate = Path(source_root).expanduser()
            if not candidate.is_dir():
                raise ValueError(f"allowed source root is not a directory: {candidate}")
            sources.append(candidate.resolve())
        self.allowed_source_roots = tuple(sources)

        lock_key = os.path.normcase(str(self.root))
        with self._locks_guard:
            self._lock = self._locks.setdefault(lock_key, threading.RLock())

    @contextmanager
    def publication_guard(self):
        """Hold the shared store lock across a caller-owned finality boundary."""
        with self._lock:
            yield

    def cleanup_unreferenced_media(self) -> int:
        """Remove only store-owned media trees absent from durable manifests.

        A corrupt or structurally uncertain project is skipped wholesale. The
        method returns only a bounded count and never exposes media paths.
        """
        removed = 0
        projects_root = self.root / "projects"
        with self._lock:
            if not projects_root.exists():
                return 0
            if projects_root.is_symlink() or not projects_root.is_dir():
                raise ProjectAssetPersistenceError(
                    "project asset root must be a real directory"
                )
            for project_dir in projects_root.iterdir():
                if (
                    project_dir.is_symlink()
                    or not project_dir.is_dir()
                    or not _SAFE_ID_RE.fullmatch(project_dir.name)
                    or project_dir.name.upper() in _WINDOWS_RESERVED_NAMES
                ):
                    continue
                try:
                    manifest = self._load_manifest(project_dir.name)
                    referenced = set()
                    for workspace_id, workspace in manifest["workspaces"].items():
                        _validate_id(workspace_id, "workspace_id")
                        if (
                            not isinstance(workspace, dict)
                            or not isinstance(workspace.get("assets"), list)
                        ):
                            raise ValueError("invalid workspace manifest")
                        for asset in workspace["assets"]:
                            if (
                                not isinstance(asset, dict)
                                or not isinstance(asset.get("variants"), list)
                            ):
                                raise ValueError("invalid asset manifest")
                            asset_id = _validate_id(asset.get("id"), "asset_id")
                            for variant in asset["variants"]:
                                if not isinstance(variant, dict):
                                    raise ValueError("invalid variant manifest")
                                variant_id = _validate_id(
                                    variant.get("id"), "variant_id",
                                )
                                referenced.add(
                                    (workspace_id, asset_id, variant_id)
                                )
                except (KeyError, TypeError, ValueError, ProjectAssetError):
                    continue
                workspaces_root = project_dir / "workspaces"
                if not workspaces_root.exists():
                    continue
                if workspaces_root.is_symlink() or not workspaces_root.is_dir():
                    continue
                for workspace_dir in workspaces_root.iterdir():
                    if (
                        workspace_dir.is_symlink()
                        or not workspace_dir.is_dir()
                        or not _SAFE_ID_RE.fullmatch(workspace_dir.name)
                    ):
                        continue
                    media_root = workspace_dir / "media"
                    if not media_root.exists() or media_root.is_symlink():
                        continue
                    if not media_root.is_dir():
                        continue
                    for asset_dir in media_root.iterdir():
                        if asset_dir.is_symlink() or not asset_dir.is_dir():
                            continue
                        if not _SAFE_ID_RE.fullmatch(asset_dir.name):
                            continue
                        for candidate in tuple(asset_dir.iterdir()):
                            keep = (
                                candidate.is_dir()
                                and not candidate.is_symlink()
                                and (
                                    workspace_dir.name,
                                    asset_dir.name,
                                    candidate.name,
                                ) in referenced
                            )
                            if keep:
                                continue
                            self._remove_uncommitted_media_tree(candidate)
                            removed += 1
                        try:
                            asset_dir.rmdir()
                        except OSError:
                            pass
        return removed

    # -- Public project metadata -----------------------------------------

    def get_password_metadata(self, project_id: str) -> Optional[dict[str, Any]]:
        """Return opaque auth-layer metadata; no password check is performed."""
        project_id = _validate_id(project_id, "project_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            value = manifest.get("password_metadata")
            return deepcopy(value) if isinstance(value, dict) else None

    def set_password_metadata(
        self,
        project_id: str,
        metadata: Optional[Mapping[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Persist opaque metadata from an external auth provider.

        Passing ``None`` clears the metadata.  Raw passwords should never be
        passed here; authentication and verifier generation belong to callers.
        """
        project_id = _validate_id(project_id, "project_id")
        clean = None if metadata is None else _json_object(metadata, "password_metadata")
        with self._lock:
            manifest = self._load_manifest(project_id)
            manifest["password_metadata"] = clean
            manifest["updated_at"] = _utc_now()
            self._write_manifest(project_id, manifest)
        return deepcopy(clean)

    def delete_project(self, project_id: str) -> bool:
        """Delete a project's manifest and copied reference media together."""
        project_id = _validate_id(project_id, "project_id")
        with self._lock:
            project_dir = self._project_dir(project_id)
            if not project_dir.exists():
                return False
            if project_dir.is_symlink() or not project_dir.is_dir():
                raise ProjectAssetPersistenceError(
                    "project asset directory must be a real directory"
                )
            try:
                shutil.rmtree(project_dir)
            except OSError as exc:
                raise ProjectAssetPersistenceError(
                    "could not delete project reference data"
                ) from exc
            return True

    # -- Asset CRUD ------------------------------------------------------

    def list_assets(
        self,
        project_id: str,
        workspace_id: str,
        *,
        asset_type: Optional[str] = None,
        tags: Optional[Iterable[object]] = None,
        variant_status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        type_filter = _validate_token(asset_type, "asset_type") if asset_type is not None else None
        tag_filter = {tag.casefold() for tag in _normalize_tags(tags)} if tags is not None else set()
        if variant_status is not None and variant_status not in VARIANT_STATUSES:
            raise ValueError("invalid variant_status")
        with self._lock:
            manifest = self._load_manifest(project_id)
            workspace = manifest["workspaces"].get(workspace_id)
            assets = [] if workspace is None else workspace.get("assets", [])
            result = []
            for asset in assets:
                if type_filter is not None and asset.get("asset_type") != type_filter:
                    continue
                asset_tags = {str(tag).casefold() for tag in asset.get("tags", [])}
                if tag_filter and not tag_filter.issubset(asset_tags):
                    continue
                if variant_status is not None and not any(
                    variant.get("status") == variant_status
                    for variant in asset.get("variants", [])
                ):
                    continue
                result.append(deepcopy(asset))
            return result

    def get_asset(
        self, project_id: str, workspace_id: str, asset_id: str,
    ) -> dict[str, Any]:
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            return deepcopy(self._find_asset(manifest, workspace_id, asset_id))

    def create_asset(
        self,
        project_id: str,
        workspace_id: str,
        *,
        name: str,
        asset_type: str,
        description: str = "",
        tags: Optional[Iterable[object]] = None,
        provenance: object = "typed",
        variants: Optional[Iterable[Mapping[str, Any]]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        asset_id: Optional[str] = None,
    ) -> dict[str, Any]:
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id or uuid.uuid4().hex, "asset_id")
        clean_name = self._text(name, "name", required=True, limit=200)
        clean_description = self._text(description, "description", limit=10_000)
        variant_specs = list(variants or [])
        created_at = _utc_now()
        asset: dict[str, Any] = {
            "id": asset_id,
            "asset_type": _validate_token(asset_type, "asset_type"),
            "name": clean_name,
            "description": clean_description,
            "tags": _normalize_tags(tags),
            "provenance": _normalize_provenance(provenance),
            "metadata": _json_object(metadata, "metadata"),
            "variants": [],
            "created_at": created_at,
            "updated_at": created_at,
        }

        with self._lock:
            manifest = self._load_manifest(project_id)
            workspace = self._workspace(manifest, workspace_id, create=True)
            if any(
                str(existing.get("id", "")).casefold() == asset_id.casefold()
                for existing in workspace["assets"]
            ):
                raise ValueError(f"asset_id already exists: {asset_id}")
            try:
                for spec in variant_specs:
                    asset["variants"].append(
                        self._build_variant(project_id, workspace_id, asset_id, spec)
                    )
                workspace["assets"].append(asset)
                workspace["updated_at"] = created_at
                manifest["updated_at"] = created_at
                self._write_manifest(project_id, manifest)
            except Exception:
                self._remove_uncommitted_media_tree(
                    self._asset_media_dir(project_id, workspace_id, asset_id),
                )
                raise
        return deepcopy(asset)

    def update_asset(
        self,
        project_id: str,
        workspace_id: str,
        asset_id: str,
        updates: Mapping[str, Any],
    ) -> dict[str, Any]:
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        if not isinstance(updates, Mapping):
            raise ValueError("updates must be an object")
        allowed = {"name", "asset_type", "description", "tags", "provenance", "metadata"}
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"unsupported asset fields: {', '.join(sorted(unknown))}")

        with self._lock:
            manifest = self._load_manifest(project_id)
            asset = self._find_asset(manifest, workspace_id, asset_id)
            if "name" in updates:
                asset["name"] = self._text(updates["name"], "name", required=True, limit=200)
            if "asset_type" in updates:
                asset["asset_type"] = _validate_token(updates["asset_type"], "asset_type")
            if "description" in updates:
                asset["description"] = self._text(updates["description"], "description", limit=10_000)
            if "tags" in updates:
                asset["tags"] = _normalize_tags(updates["tags"])
            if "provenance" in updates:
                asset["provenance"] = _normalize_provenance(updates["provenance"])
            if "metadata" in updates:
                asset["metadata"] = _json_object(updates["metadata"], "metadata")
            now = _utc_now()
            asset["updated_at"] = now
            self._touch_workspace(manifest, workspace_id, now)
            self._write_manifest(project_id, manifest)
            return deepcopy(asset)

    def delete_asset(
        self, project_id: str, workspace_id: str, asset_id: str,
    ) -> bool:
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            workspace = manifest["workspaces"].get(workspace_id)
            if workspace is None:
                return False
            assets = workspace.get("assets", [])
            remaining = [asset for asset in assets if asset.get("id") != asset_id]
            if len(remaining) == len(assets):
                return False
            workspace["assets"] = remaining
            now = _utc_now()
            self._touch_workspace(manifest, workspace_id, now)
            # Publish the manifest first: a failed media cleanup may orphan
            # bytes, but can never leave persisted references to deleted data.
            self._write_manifest(project_id, manifest)
            shutil.rmtree(self._asset_media_dir(project_id, workspace_id, asset_id), ignore_errors=True)
            return True

    # -- Variant operations ---------------------------------------------

    def add_variant(
        self,
        project_id: str,
        workspace_id: str,
        asset_id: str,
        *,
        variant_type: str,
        label: str,
        outputs: Iterable[os.PathLike[str] | str | Mapping[str, Any]],
        provenance: object = "generated",
        status: str = "candidate",
        metadata: Optional[Mapping[str, Any]] = None,
        variant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        variant_id = _validate_id(variant_id or uuid.uuid4().hex, "variant_id")
        spec = {
            "id": variant_id,
            "variant_type": variant_type,
            "label": label,
            "outputs": list(outputs),
            "provenance": provenance,
            "status": status,
            "metadata": metadata,
        }
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            asset = self._find_asset(manifest, workspace_id, asset_id)
            if any(
                str(existing.get("id", "")).casefold() == variant_id.casefold()
                for existing in asset.get("variants", [])
            ):
                raise ValueError(f"variant_id already exists: {variant_id}")
            variant = self._build_variant(project_id, workspace_id, asset_id, spec)
            try:
                asset.setdefault("variants", []).append(variant)
                now = _utc_now()
                asset["updated_at"] = now
                self._touch_workspace(manifest, workspace_id, now)
                self._write_manifest(project_id, manifest)
            except Exception:
                self._remove_uncommitted_media_tree(
                    self._variant_media_dir(
                        project_id, workspace_id, asset_id, variant["id"],
                    ),
                )
                raise
            return deepcopy(variant)

    def add_variants_atomic(
        self,
        project_id: str,
        workspace_id: str,
        asset_id: str,
        variants: Iterable[Mapping[str, Any]],
        *,
        expected_existing_variants: Iterable[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Validate exact replays and append a batch with one manifest commit.

        Media is copied before the manifest is replaced. Any validation, copy,
        or pre-commit persistence failure removes every directory created for
        this batch and leaves the prior asset exactly intact.
        """
        if isinstance(variants, (str, bytes, Mapping)):
            raise ValueError("variants must be a list of objects")
        specs = list(variants)
        if isinstance(expected_existing_variants, (str, bytes, Mapping)):
            raise ValueError("expected_existing_variants must be a list of objects")
        expected = list(expected_existing_variants)
        if not specs and not expected:
            raise ValueError(
                "variants or expected_existing_variants must be non-empty"
            )
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            asset = self._find_asset(manifest, workspace_id, asset_id)
            current_by_id = {
                str(item.get("id") or ""): item
                for item in asset.get("variants", [])
            }
            expected_ids: set[str] = set()
            for replay in expected:
                if not isinstance(replay, Mapping):
                    raise ValueError("each expected existing variant must be an object")
                replay_id = _validate_id(replay.get("id"), "variant_id")
                folded = replay_id.casefold()
                if folded in expected_ids:
                    raise ValueError(f"duplicate expected variant_id: {replay_id}")
                expected_ids.add(folded)
                if current_by_id.get(replay_id) != dict(replay):
                    raise ValueError(f"existing variant changed: {replay_id}")
                replay_outputs = replay.get("outputs")
                if not isinstance(replay_outputs, list) or not replay_outputs:
                    raise ValueError(
                        f"existing variant media changed: {replay_id}"
                    )
                for output in replay_outputs:
                    relative = (
                        output.get("relative_path")
                        if isinstance(output, Mapping) else None
                    )
                    if not isinstance(relative, str):
                        raise ValueError(
                            f"existing variant media changed: {replay_id}"
                        )
                    try:
                        resolved = Path(self.resolve_output_path(
                            project_id, workspace_id, relative,
                        ))
                    except (ProjectAssetError, ValueError):
                        raise ValueError(
                            f"existing variant media changed: {replay_id}"
                        ) from None
                    if resolved.is_symlink() or not resolved.is_file():
                        raise ValueError(
                            f"existing variant media changed: {replay_id}"
                        )
            if not specs:
                return []
            occupied = {
                str(item.get("id", "")).casefold()
                for item in asset.get("variants", [])
            }
            requested: set[str] = set()
            for spec in specs:
                if not isinstance(spec, Mapping):
                    raise ValueError("each variant must be an object")
                requested_id = spec.get("id")
                if requested_id is None:
                    continue
                normalized = _validate_id(requested_id, "variant_id").casefold()
                if normalized in occupied or normalized in requested:
                    raise ValueError(f"variant_id already exists: {requested_id}")
                requested.add(normalized)

            built: list[dict[str, Any]] = []
            try:
                for spec in specs:
                    variant = self._build_variant(
                        project_id, workspace_id, asset_id, spec,
                    )
                    folded = variant["id"].casefold()
                    if folded in occupied or any(
                        item["id"].casefold() == folded for item in built
                    ):
                        raise ValueError(
                            f"variant_id already exists: {variant['id']}"
                        )
                    built.append(variant)
                asset.setdefault("variants", []).extend(built)
                now = _utc_now()
                asset["updated_at"] = now
                self._touch_workspace(manifest, workspace_id, now)
                self._write_manifest(project_id, manifest)
            except Exception:
                for variant in built:
                    self._remove_uncommitted_media_tree(
                        self._variant_media_dir(
                            project_id, workspace_id, asset_id, variant["id"],
                        ),
                    )
                raise
            return deepcopy(built)

    def get_variant(
        self, project_id: str, workspace_id: str, asset_id: str, variant_id: str,
    ) -> dict[str, Any]:
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        variant_id = _validate_id(variant_id, "variant_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            asset = self._find_asset(manifest, workspace_id, asset_id)
            return deepcopy(self._find_variant(asset, variant_id))

    def set_variant_status(
        self,
        project_id: str,
        workspace_id: str,
        asset_id: str,
        variant_id: str,
        status: str,
    ) -> dict[str, Any]:
        if status not in VARIANT_STATUSES:
            raise ValueError("status must be candidate, kept, or rejected")
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        variant_id = _validate_id(variant_id, "variant_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            asset = self._find_asset(manifest, workspace_id, asset_id)
            variant = self._find_variant(asset, variant_id)
            now = _utc_now()
            variant["status"] = status
            variant["updated_at"] = now
            asset["updated_at"] = now
            self._touch_workspace(manifest, workspace_id, now)
            self._write_manifest(project_id, manifest)
            return deepcopy(variant)

    def keep_variant(
        self, project_id: str, workspace_id: str, asset_id: str, variant_id: str,
    ) -> dict[str, Any]:
        return self.set_variant_status(project_id, workspace_id, asset_id, variant_id, "kept")

    def reject_variant(
        self, project_id: str, workspace_id: str, asset_id: str, variant_id: str,
    ) -> dict[str, Any]:
        return self.set_variant_status(project_id, workspace_id, asset_id, variant_id, "rejected")

    def delete_variant(
        self, project_id: str, workspace_id: str, asset_id: str, variant_id: str,
    ) -> bool:
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        asset_id = _validate_id(asset_id, "asset_id")
        variant_id = _validate_id(variant_id, "variant_id")
        with self._lock:
            manifest = self._load_manifest(project_id)
            asset = self._find_asset(manifest, workspace_id, asset_id)
            variants = asset.get("variants", [])
            remaining = [variant for variant in variants if variant.get("id") != variant_id]
            if len(remaining) == len(variants):
                return False
            asset["variants"] = remaining
            now = _utc_now()
            asset["updated_at"] = now
            self._touch_workspace(manifest, workspace_id, now)
            self._write_manifest(project_id, manifest)
            shutil.rmtree(
                self._variant_media_dir(project_id, workspace_id, asset_id, variant_id),
                ignore_errors=True,
            )
            return True

    def resolve_output_path(
        self, project_id: str, workspace_id: str, relative_path: str,
    ) -> str:
        """Resolve a persisted output path while enforcing workspace containment."""
        project_id, workspace_id = self._validate_scope(project_id, workspace_id)
        if not isinstance(relative_path, str):
            raise ValueError("relative_path must be a string")
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError("relative_path must be a contained relative POSIX path")
        if pure.parts[0] != "media":
            raise ValueError("relative_path must point inside workspace media")
        workspace_dir = self._workspace_dir(project_id, workspace_id)
        candidate = workspace_dir.joinpath(*pure.parts)
        resolved = candidate.resolve(strict=False)
        media_root = (workspace_dir / "media").resolve(strict=False)
        if not _is_relative_to(resolved, media_root):
            raise ValueError("relative_path escapes workspace media")
        return str(resolved)

    # -- Internal persistence and media copying -------------------------

    @staticmethod
    def _text(value: object, field: str, *, required: bool = False, limit: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        clean = value.strip()
        if required and not clean:
            raise ValueError(f"{field} is required")
        if len(clean) > limit:
            raise ValueError(f"{field} must be at most {limit} characters")
        return clean

    @staticmethod
    def _validate_scope(project_id: str, workspace_id: str) -> tuple[str, str]:
        return (
            _validate_id(project_id, "project_id"),
            _validate_id(workspace_id, "workspace_id"),
        )

    def _project_dir(self, project_id: str) -> Path:
        project_id = _validate_id(project_id, "project_id")
        path = self.root / "projects" / project_id
        resolved = path.resolve(strict=False)
        if not _is_relative_to(resolved, self.root):
            raise ValueError("project path escapes storage root")
        if path.exists() and path.is_symlink():
            raise ValueError("project directory may not be a symlink")
        return path

    def _workspace_dir(self, project_id: str, workspace_id: str) -> Path:
        workspace_id = _validate_id(workspace_id, "workspace_id")
        path = self._project_dir(project_id) / "workspaces" / workspace_id
        resolved = path.resolve(strict=False)
        if not _is_relative_to(resolved, self.root):
            raise ValueError("workspace path escapes storage root")
        if path.exists() and path.is_symlink():
            raise ValueError("workspace directory may not be a symlink")
        return path

    def _asset_media_dir(self, project_id: str, workspace_id: str, asset_id: str) -> Path:
        return self._workspace_dir(project_id, workspace_id) / "media" / _validate_id(asset_id, "asset_id")

    def _variant_media_dir(
        self, project_id: str, workspace_id: str, asset_id: str, variant_id: str,
    ) -> Path:
        return self._asset_media_dir(project_id, workspace_id, asset_id) / _validate_id(variant_id, "variant_id")

    @classmethod
    def _remove_uncommitted_media_tree(cls, path: Path) -> None:
        """Remove media that never reached a manifest commit.

        ``shutil.rmtree`` is the fast path. A descriptor-independent fallback
        keeps stable variant IDs retryable when that helper itself fails.
        """
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except OSError:
            pass
        if not path.exists() and not path.is_symlink():
            return
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                for child in path.iterdir():
                    cls._remove_uncommitted_media_tree(child)
                path.rmdir()
        except OSError as exc:
            raise ProjectAssetPersistenceError(
                "could not remove uncommitted project asset media"
            ) from exc

    def _new_manifest(self, project_id: str) -> dict[str, Any]:
        now = _utc_now()
        return {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "password_metadata": None,
            "workspaces": {},
            "created_at": now,
            "updated_at": now,
        }

    def _load_manifest(self, project_id: str) -> dict[str, Any]:
        manifest_path = self._project_dir(project_id) / "project-assets.json"
        if not manifest_path.exists():
            return self._new_manifest(project_id)
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ProjectAssetPersistenceError("project asset manifest must be a regular file")
        try:
            with manifest_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectAssetPersistenceError(f"cannot read {manifest_path}") from exc
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != SCHEMA_VERSION
            or data.get("project_id") != project_id
            or not isinstance(data.get("workspaces"), dict)
        ):
            raise ProjectAssetPersistenceError("invalid project asset manifest schema")
        return data

    def _write_manifest(self, project_id: str, manifest: Mapping[str, Any]) -> None:
        project_dir = self._project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = project_dir / "project-assets.json"
        if manifest_path.exists() and manifest_path.is_symlink():
            raise ProjectAssetPersistenceError("refusing to replace a symlink manifest")
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".project-assets-", suffix=".tmp", dir=str(project_dir),
        )
        try:
            raw_handle = os.fdopen(descriptor, "w", encoding="utf-8")
            descriptor = -1  # raw_handle owns the descriptor from here on.
            with raw_handle as handle:
                json.dump(manifest, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, manifest_path)
            # Best-effort directory sync makes the rename durable on POSIX.
            if os.name != "nt":
                try:
                    directory_fd = os.open(project_dir, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    # The atomic replace above is the externally visible commit
                    # point. A directory-sync failure may weaken crash durability,
                    # but must not report an uncommitted write and prompt callers
                    # to delete media now referenced by the committed manifest.
                    pass
        except Exception:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.remove(temp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _workspace(
        manifest: dict[str, Any], workspace_id: str, *, create: bool,
    ) -> Optional[dict[str, Any]]:
        workspaces = manifest["workspaces"]
        workspace = workspaces.get(workspace_id)
        if workspace is None and create:
            now = _utc_now()
            workspace = {
                "workspace_id": workspace_id,
                "assets": [],
                "created_at": now,
                "updated_at": now,
            }
            workspaces[workspace_id] = workspace
        if workspace is not None and not isinstance(workspace.get("assets"), list):
            raise ProjectAssetPersistenceError("workspace assets must be a list")
        return workspace

    @staticmethod
    def _find_asset(
        manifest: dict[str, Any], workspace_id: str, asset_id: str,
    ) -> dict[str, Any]:
        workspace = manifest["workspaces"].get(workspace_id)
        if not isinstance(workspace, dict):
            raise ProjectAssetNotFoundError(asset_id)
        for asset in workspace.get("assets", []):
            if asset.get("id") == asset_id:
                return asset
        raise ProjectAssetNotFoundError(asset_id)

    @staticmethod
    def _find_variant(asset: dict[str, Any], variant_id: str) -> dict[str, Any]:
        for variant in asset.get("variants", []):
            if variant.get("id") == variant_id:
                return variant
        raise ProjectAssetNotFoundError(variant_id)

    @staticmethod
    def _touch_workspace(manifest: dict[str, Any], workspace_id: str, now: str) -> None:
        workspace = manifest["workspaces"].get(workspace_id)
        if workspace is not None:
            workspace["updated_at"] = now
        manifest["updated_at"] = now

    def _build_variant(
        self,
        project_id: str,
        workspace_id: str,
        asset_id: str,
        spec: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(spec, Mapping):
            raise ValueError("each variant must be an object")
        variant_id = _validate_id(spec.get("id") or uuid.uuid4().hex, "variant_id")
        status = spec.get("status", "candidate")
        if status not in VARIANT_STATUSES:
            raise ValueError("variant status must be candidate, kept, or rejected")
        raw_outputs = spec.get("outputs")
        if isinstance(raw_outputs, (str, bytes, os.PathLike)) or raw_outputs is None:
            raise ValueError("variant outputs must be a non-empty list")
        outputs = list(raw_outputs)
        if not outputs:
            raise ValueError("each variant must contain at least one output")

        variant_type = _validate_token(spec.get("variant_type", "other"), "variant_type")
        label = self._text(spec.get("label", ""), "label", required=True, limit=200)
        provenance = _normalize_provenance(spec.get("provenance"), default="generated")
        metadata = _json_object(spec.get("metadata"), "variant metadata")
        now = _utc_now()
        copied = self._copy_outputs(
            project_id, workspace_id, asset_id, variant_id, outputs,
        )
        return {
            "id": variant_id,
            "variant_type": variant_type,
            "label": label,
            "status": status,
            "provenance": provenance,
            "metadata": metadata,
            "outputs": copied,
            "created_at": now,
            "updated_at": now,
        }

    def _copy_outputs(
        self,
        project_id: str,
        workspace_id: str,
        asset_id: str,
        variant_id: str,
        outputs: list[object],
    ) -> list[dict[str, Any]]:
        if not self.allowed_source_roots:
            raise ValueError("media copying requires at least one allowed_source_root")
        final_dir = self._variant_media_dir(project_id, workspace_id, asset_id, variant_id)
        if final_dir.exists():
            raise ValueError(f"variant media directory already exists: {variant_id}")
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = final_dir.parent / f".{variant_id}.{uuid.uuid4().hex}.tmp"
        staging_dir.mkdir()
        records: list[dict[str, Any]] = []
        names: set[str] = set()
        try:
            for raw in outputs:
                if isinstance(raw, Mapping):
                    if "source_path" not in raw:
                        raise ValueError("output object requires source_path")
                    source_value = raw["source_path"]
                    output_metadata = _json_object(raw.get("metadata"), "output metadata")
                    label = self._text(raw.get("label", ""), "output label", limit=200)
                    expected_source = raw.get("expected_source_identity")
                else:
                    source_value = raw
                    output_metadata = {}
                    label = ""
                    expected_source = None
                if not isinstance(source_value, (str, os.PathLike)):
                    raise ValueError("output source_path must be a filesystem path")
                source = Path(source_value).expanduser()
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"output source must be an existing regular file: {source}")
                resolved_source = source.resolve()
                if not any(_is_relative_to(resolved_source, root) for root in self.allowed_source_roots):
                    raise ValueError(f"output source is outside allowed roots: {source}")
                basename = _validate_basename(resolved_source.name)
                folded = basename.casefold()
                if folded in names:
                    raise ValueError(f"duplicate output basename in variant: {basename}")
                names.add(folded)
                destination = staging_dir / basename
                if expected_source is None:
                    shutil.copy2(resolved_source, destination)
                else:
                    self._copy_verified_source(
                        resolved_source, destination, expected_source,
                    )
                relative_path = PurePosixPath("media", asset_id, variant_id, basename).as_posix()
                records.append({
                    "id": uuid.uuid4().hex,
                    "filename": basename,
                    "relative_path": relative_path,
                    "media_type": mimetypes.guess_type(basename)[0] or "application/octet-stream",
                    "label": label,
                    "metadata": output_metadata,
                })
            os.replace(staging_dir, final_dir)
            return records
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    @staticmethod
    def _copy_verified_source(
        source: Path,
        destination: Path,
        expected_source: object,
    ) -> None:
        """Copy only the exact regular-file bytes approved by a prior reviewer."""
        if not isinstance(expected_source, Mapping) or set(expected_source) != {
            "device", "inode", "size", "sha256",
        }:
            raise ValueError("expected source identity is invalid")
        device = expected_source.get("device")
        inode = expected_source.get("inode")
        size = expected_source.get("size")
        digest = expected_source.get("sha256")
        if (
            type(device) is not int
            or type(inode) is not int
            or type(size) is not int
            or min(device, inode, size) < 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("expected source identity is invalid")

        source_descriptor = -1
        destination_descriptor = -1
        try:
            source_descriptor = os.open(
                source,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            before = os.fstat(source_descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_dev != device
                or before.st_ino != inode
                or before.st_size != size
            ):
                raise ValueError("reviewed source identity changed")
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            copied = 0
            copied_digest = hashlib.sha256()
            while True:
                block = os.read(source_descriptor, 1024 * 1024)
                if not block:
                    break
                copied_digest.update(block)
                copied += len(block)
                offset = 0
                while offset < len(block):
                    written = os.write(destination_descriptor, block[offset:])
                    if written <= 0:
                        raise OSError("short write while copying reviewed source")
                    offset += written
            os.fsync(destination_descriptor)
            after = os.fstat(source_descriptor)
            published = os.fstat(destination_descriptor)
            if (
                after.st_dev != device
                or after.st_ino != inode
                or after.st_size != size
                or copied != size
                or published.st_size != size
                or copied_digest.hexdigest() != digest
            ):
                raise ValueError("reviewed source bytes changed")
        finally:
            if destination_descriptor >= 0:
                os.close(destination_descriptor)
            if source_descriptor >= 0:
                os.close(source_descriptor)


__all__ = [
    "PROVENANCE_KINDS",
    "SCHEMA_VERSION",
    "VARIANT_STATUSES",
    "ProjectAssetError",
    "ProjectAssetNotFoundError",
    "ProjectAssetPersistenceError",
    "ProjectAssetStore",
]
