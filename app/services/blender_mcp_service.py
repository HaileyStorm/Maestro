"""Bounded Maestro facade for Blender Lab's official Blender MCP server.

The upstream server intentionally exposes arbitrary Python execution.  Maestro
never forwards caller-provided code: its public surface is five strict,
structured operations and the mutating operations compile only validated
values into code templates owned by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class BlenderMCPInstallMetadata:
    repository: str
    tag: str
    revision: str
    package_version: str
    license: str
    executable: str
    transport: str
    bridge_host: str
    bridge_port: int
    blender_min_version: str


PINNED_INSTALL = BlenderMCPInstallMetadata(
    repository="https://projects.blender.org/lab/blender_mcp.git",
    tag="v1.0.0",
    revision="03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4",
    package_version="1.0.0",
    license="GPL-3.0-or-later",
    executable="blender-mcp",
    transport="stdio",
    bridge_host="localhost",
    bridge_port=9876,
    blender_min_version="5.1.0",
)

_MCP_ATTESTATION_FILENAME = ".maestro-attested"
_RUNTIME_MARKER_TRANSPORT = "stdio"
_BLENDER_EXECUTABLE_NAMES = {"blender", "blender.exe"}
_DISCOVERY_SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "cache",
    "checkpoints",
    "downloads",
    "models",
    "node_modules",
    "outputs",
}

# Exact listing at the pinned v1.0.0 checkout.  Initialization fails closed if
# a different server (or a drifted Blender MCP version) is launched.
UPSTREAM_TOOL_ALLOWLIST = frozenset(
    {
        "execute_blender_code",
        "execute_blender_code_for_cli",
        "get_blendfile_summary_datablocks",
        "get_blendfile_summary_datablocks_for_cli",
        "get_blendfile_summary_missing_files",
        "get_blendfile_summary_missing_files_for_cli",
        "get_blendfile_summary_of_linked_libraries",
        "get_blendfile_summary_of_linked_libraries_for_cli",
        "get_blendfile_summary_path_info",
        "get_blendfile_summary_path_info_for_cli",
        "get_blendfile_summary_usage_guess",
        "get_blendfile_summary_usage_guess_for_cli",
        "get_object_detail_summary",
        "get_objects_summary",
        "get_python_api_docs",
        "get_screenshot_of_area_as_image",
        "get_screenshot_of_window_as_image",
        "get_screenshot_of_window_as_json",
        "jump_to_tab_by_name",
        "jump_to_tab_by_space_type",
        "jump_to_view3d_object_by_name",
        "jump_to_view3d_object_data_by_name",
        "render_thumbnail_to_path",
        "render_viewport_to_path",
        "search_api_docs",
        "search_manual_docs",
    }
)

SCENE_CREATE = "scene_create"
ANIMATE_KEYFRAMES = "animate_keyframes"
RENDER_PREVIEW = "render_preview"
RENDER_ANIMATION = "render_animation"
INSPECT_SCENE = "inspect_scene"
PUBLIC_TOOLS = frozenset(
    {
        SCENE_CREATE,
        ANIMATE_KEYFRAMES,
        RENDER_PREVIEW,
        RENDER_ANIMATION,
        INSPECT_SCENE,
    }
)
# Compatibility for registries that previously imported this name.  It now
# describes the Maestro surface, never the upstream tool listing.
ALLOWED_TOOLS = PUBLIC_TOOLS

EXECUTE_BLENDER_CODE = "execute_blender_code"
GET_OBJECTS_SUMMARY = "get_objects_summary"
GET_OBJECT_DETAIL_SUMMARY = "get_object_detail_summary"
RENDER_THUMBNAIL_TO_PATH = "render_thumbnail_to_path"
_INTERNAL_UPSTREAM_TOOLS = frozenset(
    {
        EXECUTE_BLENDER_CODE,
        GET_OBJECTS_SUMMARY,
        GET_OBJECT_DETAIL_SUMMARY,
        RENDER_THUMBNAIL_TO_PATH,
    }
)


PUBLIC_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    SCENE_CREATE: {
        "type": "object",
        "additionalProperties": False,
        "required": ["objects"],
        "properties": {
            "clear_scene": {"type": "boolean", "default": False},
            "objects": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "primitive"],
                    "properties": {
                        "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_.-]{0,63}$"},
                        "primitive": {"enum": ["cube", "sphere", "cylinder", "cone", "torus", "plane"]},
                        "location": {"$ref": "#/$defs/vector3"},
                        "rotation_degrees": {"$ref": "#/$defs/vector3"},
                        "scale": {"$ref": "#/$defs/positiveVector3"},
                        "material": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "color"],
                            "properties": {
                                "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_.-]{0,63}$"},
                                "color": {
                                    "type": "array",
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                                },
                            },
                        },
                    },
                },
            },
        },
        "$defs": {
            "vector3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": -1000000, "maximum": 1000000}},
            "positiveVector3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": 0.0001, "maximum": 10000}},
        },
    },
    ANIMATE_KEYFRAMES: {
        "type": "object",
        "additionalProperties": False,
        "required": ["frame_start", "frame_end", "objects"],
        "properties": {
            "frame_start": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "frame_end": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "objects": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "keyframes"],
                    "properties": {
                        "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_.-]{0,63}$"},
                        "keyframes": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 64,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["frame"],
                                "anyOf": [
                                    {"required": ["location"]},
                                    {"required": ["rotation_degrees"]},
                                    {"required": ["scale"]},
                                ],
                                "properties": {
                                    "frame": {"type": "integer", "minimum": 0, "maximum": 1000000},
                                    "location": {"$ref": "#/$defs/vector3"},
                                    "rotation_degrees": {"$ref": "#/$defs/vector3"},
                                    "scale": {"$ref": "#/$defs/positiveVector3"},
                                    "interpolation": {"enum": ["BEZIER", "LINEAR", "CONSTANT"]},
                                },
                            },
                        },
                    },
                },
            },
        },
        "$defs": {
            "vector3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": -1000000, "maximum": 1000000}},
            "positiveVector3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": 0.0001, "maximum": 10000}},
        },
    },
    RENDER_PREVIEW: {
        "type": "object",
        "additionalProperties": False,
        "required": ["output_path"],
        "not": {"required": ["frame", "frames"]},
        "properties": {
            "output_path": {"type": "string", "pattern": ".*\\.[Pp][Nn][Gg]$"},
            "overwrite": {"type": "boolean", "default": False},
            "frame": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "frames": {
                "type": "array",
                "minItems": 2,
                "maxItems": 32,
                "uniqueItems": True,
                "items": {"type": "integer", "minimum": 0, "maximum": 1000000},
            },
        },
    },
    RENDER_ANIMATION: {
        "type": "object",
        "additionalProperties": False,
        "required": ["output_path", "frame_start", "frame_end", "fps"],
        "properties": {
            "output_path": {"type": "string", "pattern": ".*\\.[Mm][Pp]4$"},
            "frame_start": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "frame_end": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "fps": {"type": "integer", "minimum": 1, "maximum": 240},
            "overwrite": {"type": "boolean", "default": False},
        },
    },
    INSPECT_SCENE: {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "objects": {
                "type": "array",
                "maxItems": 32,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_.-]{0,63}$"},
            }
        },
    },
}


@dataclass(frozen=True)
class BlenderMCPLimits:
    max_total_objects: int = 128
    max_keyframes_per_object: int = 64
    max_total_keyframes: int = 4096
    # Five minutes at H3/LTX's normal 24 fps. This keeps remote renders
    # bounded while still covering Maestro's complete long-video duration.
    max_total_frames: int = 7200
    max_inspect_objects: int = 32
    max_retries: int = 2
    max_response_bytes: int = 1_000_000
    max_preview_bytes: int = 25_000_000
    max_video_bytes: int = 2_000_000_000

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if name == "max_retries":
                if not 0 <= value <= 5:
                    raise ValueError("max_retries must be between 0 and 5")
            elif value <= 0:
                raise ValueError(f"{name} must be positive")


class BlenderMCPClient(Protocol):
    """Persistent transport contract used by :class:`BlenderMCPService`."""

    def connect(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        """Initialize, attest the server/tool listing, and probe Blender."""

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any] | str:
        """Call one upstream MCP tool over the already initialized session."""

    def close(self) -> None:
        """Close the persistent MCP session."""


class BlenderMCPError(RuntimeError):
    pass


class BlenderMCPSecurityError(BlenderMCPError):
    pass


class BlenderMCPValidationError(BlenderMCPError, ValueError):
    pass


class BlenderMCPToolError(BlenderMCPError):
    pass


class BlenderMCPCancelled(BlenderMCPError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _inferred_pinokio_home() -> Path | None:
    configured = os.environ.get("PINOKIO_HOME", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if candidate.is_dir():
            return candidate
    try:
        candidate = Path(__file__).resolve().parents[4]
    except IndexError:
        return None
    if (candidate / "api").is_dir() and (candidate / "bin").is_dir():
        return candidate
    return None


def _bounded_named_files(
    roots: Sequence[Path],
    names: set[str],
    *,
    max_depth: int = 8,
    max_directories: int = 4096,
) -> list[Path]:
    found: list[Path] = []
    visited = 0
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        if root.is_file():
            if root.name.lower() in names:
                found.append(root)
            continue
        if not root.is_dir():
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            visited += 1
            if visited > max_directories:
                return found
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                directories[:] = []
                continue
            directories[:] = [
                name
                for name in directories
                if name.lower() not in _DISCOVERY_SKIP_DIRECTORIES
                and not (current_path / name).is_symlink()
                and depth < max_depth
            ]
            for filename in files:
                if filename.lower() in names:
                    found.append(current_path / filename)
    return found


def attest_blender_executable(executable: str | os.PathLike[str]) -> dict[str, Any]:
    """Attest a local Blender executable without accepting caller code or flags."""

    try:
        binary = Path(executable).expanduser().resolve(strict=True)
        metadata = binary.stat()
    except (OSError, RuntimeError) as exc:
        raise BlenderMCPValidationError("Blender executable is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise BlenderMCPValidationError("Blender executable must be a regular file")
    if metadata.st_mode & stat.S_IWOTH:
        raise BlenderMCPSecurityError("Blender executable must not be world-writable")
    if os.name != "nt" and not os.access(binary, os.X_OK):
        raise BlenderMCPValidationError("Blender executable is not executable")
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BlenderMCPValidationError("Blender executable version probe failed") from exc
    output = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"(?m)^Blender\s+(\d+\.\d+(?:\.\d+)?)\b", output)
    if match is None:
        raise BlenderMCPValidationError("Blender executable returned an invalid version")
    version = match.group(1)
    if _version_tuple(version) < _version_tuple(PINNED_INSTALL.blender_min_version):
        raise BlenderMCPValidationError(
            f"Blender {PINNED_INSTALL.blender_min_version} or newer is required"
        )
    return {
        "binary": str(binary),
        "version": version,
        "executable_sha256": _sha256_file(binary),
    }


def discover_blender_runtimes(
    search_roots: Sequence[str | os.PathLike[str]] | None = None,
) -> list[dict[str, Any]]:
    """Find bounded system/Pinokio Blender installations and attest each one."""

    candidates: list[Path] = []
    if search_roots is None:
        resolved = shutil.which("blender") or shutil.which("blender.exe")
        if resolved:
            candidates.append(Path(resolved))
        if os.name == "nt":
            for variable in ("ProgramFiles", "ProgramFiles(x86)"):
                root = os.environ.get(variable)
                if root:
                    candidates.extend(Path(root).glob("Blender Foundation/Blender */blender.exe"))
        elif sys.platform == "darwin":
            candidates.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
        else:
            candidates.extend(
                Path(value)
                for value in ("/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender")
            )
        pinokio_home = _inferred_pinokio_home()
        roots = [pinokio_home] if pinokio_home is not None else []
    else:
        roots = [Path(value) for value in search_roots]
    candidates.extend(_bounded_named_files(roots, _BLENDER_EXECUTABLE_NAMES))

    attested: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved_candidate = candidate.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved_candidate in seen:
            continue
        seen.add(resolved_candidate)
        try:
            attested.append(attest_blender_executable(resolved_candidate))
        except BlenderMCPError:
            continue
    return attested


def read_blender_runtime_info(marker_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return an attested bundled/external runtime marker, or an empty mapping."""

    marker = Path(marker_path).expanduser()
    try:
        if marker.is_symlink() or not marker.is_file():
            return {}
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return {}
        executable = attest_blender_executable(str(payload.get("binary") or ""))
        if payload.get("version") != executable["version"]:
            return {}
        expected_digest = payload.get("executable_sha256")
        if expected_digest is not None and expected_digest != executable["executable_sha256"]:
            return {}
        transport = payload.get("transport", _RUNTIME_MARKER_TRANSPORT)
        if transport != _RUNTIME_MARKER_TRANSPORT:
            return {}
        if payload.get("bridge_host", PINNED_INSTALL.bridge_host) != PINNED_INSTALL.bridge_host:
            return {}
        if payload.get("bridge_port", PINNED_INSTALL.bridge_port) != PINNED_INSTALL.bridge_port:
            return {}
        revision = payload.get("mcp_revision")
        if revision is not None and revision != PINNED_INSTALL.revision:
            return {}
        binary = Path(executable["binary"])
        marker_root = marker.resolve().parent
        bundled_root = (marker_root / "runtime").resolve()
        actual_source = "bundled" if binary == bundled_root or bundled_root in binary.parents else "external"
        source = str(payload.get("source") or "").strip().lower()
        if not source:
            source = actual_source
        if source != actual_source:
            return {}
        user_home_value = payload.get("user_home")
        if not isinstance(user_home_value, str) or not user_home_value.strip():
            return {}
        user_home = Path(user_home_value).expanduser().resolve()
        if user_home != marker_root and marker_root not in user_home.parents:
            return {}
        return {
            **dict(payload),
            **executable,
            "source": source,
            "external": source == "external",
            "transport": transport,
            "bridge_host": PINNED_INSTALL.bridge_host,
            "bridge_port": PINNED_INSTALL.bridge_port,
            "user_home": str(user_home),
        }
    except (BlenderMCPError, OSError, TypeError, ValueError):
        return {}


def _git_output(checkout: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BlenderMCPSecurityError("could not attest Blender MCP checkout") from exc
    return completed.stdout.strip()


def attest_mcp_checkout(checkout_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify an exact, clean official MCP checkout usable by the stdio adapter."""

    try:
        checkout = Path(checkout_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BlenderMCPValidationError("Blender MCP checkout is unavailable") from exc
    required = (
        checkout / "mcp" / "blmcp" / "__init__.py",
        checkout / "addon" / "blender_mcp_addon" / "blender_manifest.toml",
    )
    if not checkout.is_dir() or not all(path.is_file() and not path.is_symlink() for path in required):
        raise BlenderMCPValidationError("Blender MCP checkout is incomplete")
    revision = _git_output(checkout, "rev-parse", "HEAD")
    tag = _git_output(checkout, "describe", "--tags", "--exact-match", "HEAD")
    repository = _git_output(checkout, "remote", "get-url", "origin").rstrip("/")
    dirty = _git_output(checkout, "status", "--porcelain", "--untracked-files=no")
    normalized_repository = repository.removesuffix(".git")
    normalized_expected = PINNED_INSTALL.repository.removesuffix(".git")
    if revision != PINNED_INSTALL.revision:
        raise BlenderMCPSecurityError("Blender MCP checkout revision does not match")
    if tag != PINNED_INSTALL.tag:
        raise BlenderMCPSecurityError("Blender MCP checkout tag does not match")
    if normalized_repository != normalized_expected:
        raise BlenderMCPSecurityError("Blender MCP checkout origin does not match")
    if dirty:
        raise BlenderMCPSecurityError("Blender MCP checkout has modified tracked files")
    return {
        "repository": PINNED_INSTALL.repository,
        "tag": PINNED_INSTALL.tag,
        "revision": PINNED_INSTALL.revision,
        "package_version": PINNED_INSTALL.package_version,
        "transport": PINNED_INSTALL.transport,
        "checkout": str(checkout),
    }


def _mcp_checkout_candidates(
    roots: Sequence[Path],
    *,
    max_depth: int = 8,
    max_directories: int = 4096,
) -> list[Path]:
    candidates: list[Path] = []
    visited = 0
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        if not root.is_dir():
            continue
        for current, directories, _files in os.walk(root, followlinks=False):
            visited += 1
            if visited > max_directories:
                return candidates
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                directories[:] = []
                continue
            if current_path.name == "blender_mcp" and (
                current_path / "mcp" / "blmcp" / "__init__.py"
            ).is_file():
                candidates.append(current_path)
                directories[:] = []
                continue
            directories[:] = [
                name
                for name in directories
                if name.lower() not in _DISCOVERY_SKIP_DIRECTORIES
                and not (current_path / name).is_symlink()
                and depth < max_depth
            ]
    return candidates


def discover_compatible_mcp_checkouts(
    search_roots: Sequence[str | os.PathLike[str]] | None = None,
) -> list[dict[str, Any]]:
    """Find only exact official MCP checkouts within bounded Pinokio roots."""

    if search_roots is None:
        pinokio_home = _inferred_pinokio_home()
        roots = [pinokio_home] if pinokio_home is not None else []
    else:
        roots = [Path(value) for value in search_roots]
    results: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for candidate in _mcp_checkout_candidates(roots):
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            results.append(attest_mcp_checkout(resolved))
        except BlenderMCPError:
            continue
    return results


def _write_mcp_attestation_marker(checkout: Path, facts: Mapping[str, Any]) -> None:
    marker_payload = {
        key: facts[key]
        for key in ("repository", "tag", "revision", "package_version", "transport")
    }
    _atomic_json(checkout / _MCP_ATTESTATION_FILENAME, marker_payload)


def reuse_compatible_mcp_checkout(
    destination: str | os.PathLike[str],
    search_roots: Sequence[str | os.PathLike[str]] | None = None,
) -> Path | None:
    """Reuse a compatible local checkout before the launcher performs a network clone."""

    target = Path(destination).expanduser().resolve()
    marker = target / _MCP_ATTESTATION_FILENAME
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    if target.exists():
        try:
            facts = attest_mcp_checkout(target)
        except BlenderMCPError:
            return None
        _write_mcp_attestation_marker(target, facts)
        return target

    for facts in discover_compatible_mcp_checkouts(search_roots):
        source = Path(str(facts["checkout"])).resolve()
        if source == target:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.reuse-{uuid.uuid4().hex}"
        try:
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "clone",
                    "--local",
                    "--no-hardlinks",
                    "--no-checkout",
                    str(source),
                    str(staging),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            _git_output(staging, "remote", "set-url", "origin", PINNED_INSTALL.repository)
            _git_output(staging, "checkout", "--detach", PINNED_INSTALL.revision)
            staged_facts = attest_mcp_checkout(staging)
            if target.exists():
                existing_facts = attest_mcp_checkout(target)
                _write_mcp_attestation_marker(target, existing_facts)
                return target
            os.replace(staging, target)
            _write_mcp_attestation_marker(target, staged_facts)
            return target
        except (BlenderMCPError, OSError, subprocess.SubprocessError):
            continue
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    return None


def quarantine_invalid_mcp_checkout(
    destination: str | os.PathLike[str],
) -> Path | None:
    """Move an incomplete managed checkout aside so repair can clone cleanly."""

    target = Path(os.path.abspath(Path(destination).expanduser()))
    if not target.exists() and not target.is_symlink():
        return None
    try:
        attest_mcp_checkout(target)
    except BlenderMCPError:
        quarantine = target.parent / f".{target.name}.invalid-{uuid.uuid4().hex}"
        os.replace(target, quarantine)
        return quarantine
    return None


def _runtime_environment(user_home: Path) -> dict[str, str]:
    allowed = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "COMSPEC",
            "LD_LIBRARY_PATH",
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "WINDIR",
        }
    }
    user_home.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        allowed.update(
            {
                "USERPROFILE": str(user_home),
                "APPDATA": str(user_home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(user_home / "AppData" / "Local"),
            }
        )
    else:
        allowed.update(
            {
                "HOME": str(user_home),
                "XDG_CONFIG_HOME": str(user_home / ".config"),
                "XDG_CACHE_HOME": str(user_home / ".cache"),
            }
        )
    return allowed


def provision_discovered_blender_runtime(
    checkout_root: str | os.PathLike[str],
    marker_path: str | os.PathLike[str],
    search_roots: Sequence[str | os.PathLike[str]] | None = None,
) -> dict[str, Any] | None:
    """Install the pinned addon into an isolated profile for an attested Blender."""

    marker = Path(marker_path).expanduser().resolve()
    current = read_blender_runtime_info(marker)
    if current:
        return current
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    checkout = Path(checkout_root).expanduser().resolve()
    attest_mcp_checkout(checkout)
    runtimes = discover_blender_runtimes(search_roots)
    for executable in runtimes:
        binary = Path(str(executable["binary"])).resolve()
        user_home = marker.parent / "home"
        build_dir = marker.parent / f".extension-build-{uuid.uuid4().hex}"
        try:
            build_dir.mkdir(parents=True, exist_ok=False)
            environment = _runtime_environment(user_home)
            subprocess.run(
                [
                    str(binary),
                    "--command",
                    "extension",
                    "build",
                    "--source-dir=" + str(checkout / "addon" / "blender_mcp_addon"),
                    "--output-dir=" + str(build_dir),
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            extension_zips = sorted(build_dir.glob("mcp-*.zip"))
            if not extension_zips:
                continue
            subprocess.run(
                [
                    str(binary),
                    "--online-mode",
                    "--background",
                    "--factory-startup",
                    "--command",
                    "extension",
                    "install-file",
                    str(extension_zips[-1]),
                    "--repo",
                    "user_default",
                    "--enable",
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            bundled_root = (marker.parent / "runtime").resolve()
            source = "bundled" if binary == bundled_root or bundled_root in binary.parents else "external"
            payload = {
                **executable,
                "source": source,
                "external": source == "external",
                "user_home": str(user_home),
                "mcp_revision": PINNED_INSTALL.revision,
                "transport": PINNED_INSTALL.transport,
                "bridge_host": PINNED_INSTALL.bridge_host,
                "bridge_port": PINNED_INSTALL.bridge_port,
            }
            _atomic_json(marker, payload)
            return read_blender_runtime_info(marker)
        except (OSError, subprocess.SubprocessError):
            continue
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)
    return None


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_PRIMITIVES = frozenset({"cube", "sphere", "cylinder", "cone", "torus", "plane"})
_INTERPOLATIONS = frozenset({"BEZIER", "LINEAR", "CONSTANT"})
_PREVIEW_EXTENSIONS = frozenset({".png"})
_VIDEO_EXTENSIONS = frozenset({".mp4"})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PROJECT_SCENE_FILENAME = ".maestro_blender_scene.blend"


class BlenderMCPService:
    """Strict public facade over a lazily connected official MCP client."""

    def __init__(
        self,
        client: BlenderMCPClient,
        project_root: str | os.PathLike[str],
        *,
        limits: BlenderMCPLimits | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise BlenderMCPValidationError("project_root must be an existing directory")
        self._client = client
        self.project_root = root
        self.scene_path = root / _PROJECT_SCENE_FILENAME
        self._validate_project_scene_path()
        self.limits = limits or BlenderMCPLimits()
        self._sleep = sleeper
        self._ready = False
        self._attestation: dict[str, Any] | None = None

    @property
    def attestation(self) -> dict[str, Any] | None:
        return dict(self._attestation) if self._attestation is not None else None

    def close(self) -> None:
        self._client.close()
        self._ready = False

    def invoke(
        self,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if tool not in PUBLIC_TOOLS:
            raise BlenderMCPSecurityError(f"Maestro Blender tool is not allowed: {tool!r}")
        args = {} if arguments is None else arguments
        if not isinstance(args, Mapping):
            raise BlenderMCPValidationError("tool arguments must be a mapping")
        handlers = {
            SCENE_CREATE: self.scene_create,
            ANIMATE_KEYFRAMES: self.animate_keyframes,
            RENDER_PREVIEW: self.render_preview,
            RENDER_ANIMATION: self.render_animation,
            INSPECT_SCENE: self.inspect_scene,
        }
        return handlers[tool](args, cancelled=cancelled)

    def scene_create(
        self,
        arguments: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        scene = self._normalize_scene_create(arguments)
        self._activate_project_scene(cancelled=cancelled)
        response = self._call_upstream(
            EXECUTE_BLENDER_CODE,
            {"code": _scene_create_code(scene)},
            cancelled=cancelled,
        )
        upstream = self._extract_result(response, EXECUTE_BLENDER_CODE)
        self._ensure_render_scene(cancelled=cancelled)
        self._save_project_scene(cancelled=cancelled)
        return {
            "status": "ok",
            "created": [obj["name"] for obj in scene["objects"]],
            "upstream": upstream,
        }

    def animate_keyframes(
        self,
        arguments: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        animation = self._normalize_animation(arguments)
        self._activate_project_scene(cancelled=cancelled)
        response = self._call_upstream(
            EXECUTE_BLENDER_CODE,
            {"code": _animate_keyframes_code(animation)},
            cancelled=cancelled,
        )
        upstream = self._extract_result(response, EXECUTE_BLENDER_CODE)
        self._save_project_scene(cancelled=cancelled)
        return {
            "status": "ok",
            "frame_range": {
                "start": animation["frame_start"],
                "end": animation["frame_end"],
            },
            "animated": [obj["name"] for obj in animation["objects"]],
            "upstream": upstream,
        }

    def inspect_scene(
        self,
        arguments: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        names = self._normalize_inspection(arguments)
        self._activate_project_scene(cancelled=cancelled)
        summary = self._call_upstream(
            GET_OBJECTS_SUMMARY,
            {},
            retries=self.limits.max_retries,
            cancelled=cancelled,
        )
        details: dict[str, Any] = {}
        for name in names:
            self._check_cancelled(cancelled)
            value = self._call_upstream(
                GET_OBJECT_DETAIL_SUMMARY,
                {"name": name},
                retries=self.limits.max_retries,
                cancelled=cancelled,
            )
            details[name] = self._extract_result(value, GET_OBJECT_DETAIL_SUMMARY)
        return {
            "status": "ok",
            "summary": self._extract_result(summary, GET_OBJECTS_SUMMARY),
            "objects": details,
        }

    def render_preview(
        self,
        arguments: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        destination, overwrite, frame, frames = self._normalize_render(arguments)
        if frames is not None:
            destinations = [
                destination.with_name(f"{destination.stem}_f{value:06d}.png")
                for value in frames
            ]
            if not overwrite and any(path.exists() for path in destinations):
                raise BlenderMCPValidationError("preview output already exists")
            self._activate_project_scene(cancelled=cancelled)
            self._ensure_render_scene(cancelled=cancelled)
            outputs = []
            for value, frame_destination in zip(frames, destinations, strict=True):
                self._set_frame(value, cancelled=cancelled)
                self._render_preview_to(
                    frame_destination,
                    overwrite=overwrite,
                    cancelled=cancelled,
                )
                outputs.append({"frame": value, "output_path": str(frame_destination)})
            return {
                "status": "ok",
                "outputs": outputs,
                "output_paths": [item["output_path"] for item in outputs],
            }

        if destination.exists() and not overwrite:
            raise BlenderMCPValidationError("preview output already exists")
        self._activate_project_scene(cancelled=cancelled)
        self._ensure_render_scene(cancelled=cancelled)
        if frame is not None:
            self._set_frame(frame, cancelled=cancelled)
        self._render_preview_to(
            destination,
            overwrite=overwrite,
            cancelled=cancelled,
        )
        result: dict[str, Any] = {"status": "ok", "output_path": str(destination)}
        if frame is not None:
            result["frame"] = frame
        return result

    def render_animation(
        self,
        arguments: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        destination, overwrite, frame_start, frame_end, fps = (
            self._normalize_render_animation(arguments)
        )
        if destination.exists() and not overwrite:
            raise BlenderMCPValidationError("animation output already exists")

        self._activate_project_scene(cancelled=cancelled)
        self._ensure_render_scene(cancelled=cancelled)
        scratch_root = Path(str(self._attestation["scratch_root"]))
        frame_directory = scratch_root / f"maestro_frames_{uuid.uuid4().hex}"
        frame_directory.mkdir(mode=0o700)
        frame_prefix = frame_directory / "frame_"
        scratch_name = f"maestro_{uuid.uuid4().hex}.mp4"
        source = scratch_root / scratch_name
        try:
            response = self._call_upstream(
                EXECUTE_BLENDER_CODE,
                {
                    "code": _render_animation_code(
                        frame_prefix,
                        frame_start=frame_start,
                        frame_end=frame_end,
                        fps=fps,
                    )
                },
                cancelled=cancelled,
            )
            self._extract_result(response, EXECUTE_BLENDER_CODE)
            _encode_png_sequence_to_mp4(
                frame_prefix,
                source,
                frame_start=frame_start,
                frame_end=frame_end,
                fps=fps,
                max_input_bytes=self.limits.max_video_bytes,
                cancelled=cancelled,
            )
            if (
                source.name != scratch_name
                or source.suffix.lower() != ".mp4"
                or source.is_symlink()
                or not source.is_file()
                or source.parent.resolve() != scratch_root
            ):
                raise BlenderMCPToolError(
                    "animation encoder produced an invalid scratch file"
                )
            self._check_cancelled(cancelled)
            self._copy_video_safely(
                source,
                destination,
                overwrite=overwrite,
                cancelled=cancelled,
            )
        except BlenderMCPError:
            raise
        except OSError as exc:
            raise BlenderMCPToolError(
                f"could not render or securely copy animation: {exc}"
            ) from exc
        finally:
            shutil.rmtree(frame_directory, ignore_errors=True)
            try:
                source.unlink()
            except FileNotFoundError:
                pass
        return {
            "status": "ok",
            "output_path": str(destination),
            "frame_range": {"start": frame_start, "end": frame_end},
            "fps": fps,
        }

    def _validate_project_scene_path(self) -> None:
        if self.scene_path.is_symlink():
            raise BlenderMCPSecurityError(
                "project Blender scene path must not be a symbolic link"
            )
        if self.scene_path.exists() and not self.scene_path.is_file():
            raise BlenderMCPSecurityError(
                "project Blender scene path must be a regular file"
            )

    def _activate_project_scene(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._validate_project_scene_path()
        response = self._call_upstream(
            EXECUTE_BLENDER_CODE,
            {"code": _activate_project_scene_code(self.scene_path)},
            cancelled=cancelled,
        )
        self._extract_result(response, EXECUTE_BLENDER_CODE)

    def _save_project_scene(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._validate_project_scene_path()
        response = self._call_upstream(
            EXECUTE_BLENDER_CODE,
            {"code": _save_project_scene_code(self.scene_path)},
            cancelled=cancelled,
        )
        self._extract_result(response, EXECUTE_BLENDER_CODE)

    def _set_frame(
        self,
        frame: int,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        response = self._call_upstream(
            EXECUTE_BLENDER_CODE,
            {"code": _frame_set_code(frame)},
            cancelled=cancelled,
        )
        self._extract_result(response, EXECUTE_BLENDER_CODE)

    def _ensure_render_scene(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        response = self._call_upstream(
            EXECUTE_BLENDER_CODE,
            {"code": _ensure_render_scene_code()},
            cancelled=cancelled,
        )
        self._extract_result(response, EXECUTE_BLENDER_CODE)

    def _render_preview_to(
        self,
        destination: Path,
        *,
        overwrite: bool,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        scratch_name = f"maestro_{uuid.uuid4().hex}{destination.suffix.lower()}"
        response = self._call_upstream(
            RENDER_THUMBNAIL_TO_PATH,
            {"output_path": scratch_name},
            retries=self.limits.max_retries,
            cancelled=cancelled,
        )
        result = self._extract_result(response, RENDER_THUMBNAIL_TO_PATH)
        source_value = result.get("filepath") if isinstance(result, Mapping) else None
        if not isinstance(source_value, str):
            raise BlenderMCPToolError("render_thumbnail_to_path returned no filepath")
        source = Path(source_value)
        attested_scratch = Path(str(self._attestation["scratch_root"]))
        if (
            not source.is_absolute()
            or source.name != scratch_name
            or source.suffix.lower() != destination.suffix.lower()
            or source.is_symlink()
            or not source.is_file()
            or source.parent.resolve() != attested_scratch
        ):
            raise BlenderMCPToolError("render_thumbnail_to_path returned an invalid scratch file")
        self._check_cancelled(cancelled)
        try:
            self._copy_preview_safely(source, destination, overwrite=overwrite)
        except BlenderMCPError:
            raise
        except OSError as exc:
            raise BlenderMCPToolError(f"could not securely copy rendered preview: {exc}") from exc

    def _ensure_ready(self, cancelled: Callable[[], bool] | None = None) -> None:
        if self._ready:
            return
        try:
            attestation = self._client.connect(cancelled=cancelled)
        except BlenderMCPError:
            raise
        except Exception as exc:
            raise BlenderMCPToolError(f"could not initialize Blender MCP: {exc}") from exc
        self._attestation = self._validate_attestation(attestation)
        self._ready = True

    def _validate_attestation(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise BlenderMCPSecurityError("Blender MCP attestation must be a mapping")
        expected = {
            "repository": PINNED_INSTALL.repository,
            "tag": PINNED_INSTALL.tag,
            "revision": PINNED_INSTALL.revision,
            "package_version": PINNED_INSTALL.package_version,
            "license": PINNED_INSTALL.license,
            "transport": PINNED_INSTALL.transport,
            "bridge_host": PINNED_INSTALL.bridge_host,
            "bridge_port": PINNED_INSTALL.bridge_port,
            "server_name": "blender-mcp",
        }
        for key, expected_value in expected.items():
            if value.get(key) != expected_value:
                raise BlenderMCPSecurityError(f"Blender MCP attestation mismatch: {key}")
        if value.get("probe_tool") != GET_OBJECTS_SUMMARY or value.get("probe_ok") is not True:
            raise BlenderMCPSecurityError("Blender MCP get_objects_summary probe failed")
        tools = value.get("tools")
        if isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence):
            raise BlenderMCPSecurityError("Blender MCP tool attestation is invalid")
        if frozenset(tools) != UPSTREAM_TOOL_ALLOWLIST or len(tools) != len(UPSTREAM_TOOL_ALLOWLIST):
            raise BlenderMCPSecurityError("Blender MCP upstream tool listing drifted")
        blender_version = value.get("blender_version")
        if not isinstance(blender_version, str) or _version_tuple(blender_version) < (5, 1, 0):
            raise BlenderMCPSecurityError("Blender 5.1 or newer is required")
        scratch_root = value.get("scratch_root")
        if not isinstance(scratch_root, str) or not Path(scratch_root).is_absolute():
            raise BlenderMCPSecurityError("Blender MCP scratch root attestation is invalid")
        return dict(value)

    def _call_upstream(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        retries: int = 0,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if name not in _INTERNAL_UPSTREAM_TOOLS:
            raise BlenderMCPSecurityError(f"upstream tool is not internally allowed: {name}")
        self._check_cancelled(cancelled)
        self._ensure_ready(cancelled)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            self._check_cancelled(cancelled)
            try:
                return self._normalize_response(
                    name,
                    self._client.call_tool(name, arguments, cancelled=cancelled),
                )
            except BlenderMCPCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - transport errors are adapter-defined
                last_error = exc
                if attempt >= retries:
                    break
                self._sleep(min(0.1 * (2**attempt), 1.0))
        if isinstance(last_error, BlenderMCPError):
            raise last_error
        raise BlenderMCPToolError(f"{name} failed: {last_error}") from last_error

    def _normalize_response(self, tool: str, response: Any) -> dict[str, Any]:
        if isinstance(response, str):
            if len(response.encode("utf-8")) > self.limits.max_response_bytes:
                raise BlenderMCPToolError(f"{tool} response exceeded the size limit")
            try:
                response = json.loads(response)
            except json.JSONDecodeError as exc:
                raise BlenderMCPToolError(f"{tool} returned invalid JSON") from exc
        if not isinstance(response, Mapping):
            raise BlenderMCPToolError(f"{tool} response must be a JSON object")
        try:
            encoded = json.dumps(response, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise BlenderMCPToolError(f"{tool} response is not JSON-compatible") from exc
        if len(encoded) > self.limits.max_response_bytes:
            raise BlenderMCPToolError(f"{tool} response exceeded the size limit")
        if response.get("isError") is True or str(response.get("status", "")).lower() == "error":
            reason = str(response.get("message") or response.get("error") or "upstream tool error")
            raise BlenderMCPToolError(f"{tool} failed: {reason[:500]}")
        return dict(response)

    @staticmethod
    def _extract_result(response: Mapping[str, Any], tool: str) -> Any:
        value: Any = response.get("structuredContent", response)
        if isinstance(value, Mapping) and set(value) == {"result"}:
            value = value["result"]
        if isinstance(value, Mapping) and str(value.get("status", "")).lower() == "error":
            reason = str(value.get("message") or value.get("error") or "upstream tool error")
            raise BlenderMCPToolError(f"{tool} failed: {reason[:500]}")
        if isinstance(value, Mapping) and "result" in value and value.get("status") == "ok":
            nested = value["result"]
            if isinstance(nested, Mapping) and str(nested.get("status", "")).lower() == "error":
                raise BlenderMCPToolError(f"{tool} failed: {nested.get('message', 'tool error')}")
            return nested
        return value

    def _normalize_scene_create(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise BlenderMCPValidationError("scene_create arguments must be a mapping")
        _reject_unknown(arguments, {"objects", "clear_scene"}, "scene_create")
        clear_scene = arguments.get("clear_scene", False)
        if not isinstance(clear_scene, bool):
            raise BlenderMCPValidationError("clear_scene must be boolean")
        objects = _require_sequence(arguments.get("objects"), "objects")
        if not 1 <= len(objects) <= self.limits.max_total_objects:
            raise BlenderMCPValidationError("objects count is outside the configured limit")
        normalized: list[dict[str, Any]] = []
        names: set[str] = set()
        materials: dict[str, tuple[float, float, float, float]] = {}
        for index, value in enumerate(objects):
            if not isinstance(value, Mapping):
                raise BlenderMCPValidationError(f"objects[{index}] must be a mapping")
            _reject_unknown(value, {"name", "primitive", "location", "rotation_degrees", "scale", "material"}, f"objects[{index}]")
            name = _safe_name(value.get("name"), f"objects[{index}].name")
            if name in names:
                raise BlenderMCPValidationError(f"duplicate object name: {name}")
            names.add(name)
            primitive = value.get("primitive")
            if primitive not in _PRIMITIVES:
                raise BlenderMCPValidationError(f"{name} has an unsupported primitive")
            item: dict[str, Any] = {"name": name, "primitive": primitive}
            if "location" in value:
                item["location"] = _vector3(value["location"], f"{name}.location")
            if "rotation_degrees" in value:
                item["rotation_degrees"] = _vector3(value["rotation_degrees"], f"{name}.rotation_degrees")
            if "scale" in value:
                item["scale"] = _vector3(value["scale"], f"{name}.scale", minimum=0.0001, maximum=10_000)
            if "material" in value:
                material = value["material"]
                if not isinstance(material, Mapping):
                    raise BlenderMCPValidationError(f"{name}.material must be a mapping")
                _reject_unknown(material, {"name", "color"}, f"{name}.material")
                material_name = _safe_name(material.get("name"), f"{name}.material.name")
                material_color = _color4(material.get("color"), f"{name}.material.color")
                signature = tuple(material_color)
                if material_name in materials and materials[material_name] != signature:
                    raise BlenderMCPValidationError(
                        f"material {material_name} has conflicting definitions"
                    )
                materials[material_name] = signature
                item["material"] = {"name": material_name, "color": material_color}
            normalized.append(item)
        return {"clear_scene": clear_scene, "objects": normalized}

    def _normalize_animation(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise BlenderMCPValidationError("animate_keyframes arguments must be a mapping")
        _reject_unknown(arguments, {"frame_start", "frame_end", "objects"}, "animate_keyframes")
        start = _bounded_integer(arguments.get("frame_start"), "frame_start", 0, 1_000_000)
        end = _bounded_integer(arguments.get("frame_end"), "frame_end", 0, 1_000_000)
        if end < start or end - start + 1 > self.limits.max_total_frames:
            raise BlenderMCPValidationError("frame range is invalid or exceeds the configured limit")
        objects = _require_sequence(arguments.get("objects"), "objects")
        if not 1 <= len(objects) <= self.limits.max_total_objects:
            raise BlenderMCPValidationError("objects count is outside the configured limit")
        normalized: list[dict[str, Any]] = []
        names: set[str] = set()
        total_keyframes = 0
        for index, value in enumerate(objects):
            if not isinstance(value, Mapping):
                raise BlenderMCPValidationError(f"objects[{index}] must be a mapping")
            _reject_unknown(value, {"name", "keyframes"}, f"objects[{index}]")
            name = _safe_name(value.get("name"), f"objects[{index}].name")
            if name in names:
                raise BlenderMCPValidationError(f"duplicate animation object: {name}")
            names.add(name)
            keyframes = _require_sequence(value.get("keyframes"), f"{name}.keyframes")
            if not 1 <= len(keyframes) <= self.limits.max_keyframes_per_object:
                raise BlenderMCPValidationError(f"{name} keyframe count is outside the configured limit")
            total_keyframes += len(keyframes)
            if total_keyframes > self.limits.max_total_keyframes:
                raise BlenderMCPValidationError("total keyframes exceed the configured limit")
            normalized_frames: list[dict[str, Any]] = []
            seen: set[int] = set()
            for key_index, raw in enumerate(keyframes):
                if not isinstance(raw, Mapping):
                    raise BlenderMCPValidationError(f"{name}.keyframes[{key_index}] must be a mapping")
                _reject_unknown(raw, {"frame", "location", "rotation_degrees", "scale", "interpolation"}, f"{name}.keyframes[{key_index}]")
                frame = _bounded_integer(raw.get("frame"), "frame", start, end)
                if frame in seen:
                    raise BlenderMCPValidationError(f"{name} has duplicate keyframe {frame}")
                seen.add(frame)
                item: dict[str, Any] = {"frame": frame}
                for field in ("location", "rotation_degrees", "scale"):
                    if field in raw:
                        item[field] = _vector3(raw[field], f"{name}.{field}", minimum=0.0001 if field == "scale" else -1_000_000, maximum=10_000 if field == "scale" else 1_000_000)
                if len(item) == 1:
                    raise BlenderMCPValidationError(f"{name} keyframe has no transform")
                interpolation = raw.get("interpolation", "BEZIER")
                if interpolation not in _INTERPOLATIONS:
                    raise BlenderMCPValidationError("unsupported interpolation")
                item["interpolation"] = interpolation
                normalized_frames.append(item)
            normalized.append({"name": name, "keyframes": normalized_frames})
        return {"frame_start": start, "frame_end": end, "objects": normalized}

    def _normalize_inspection(self, arguments: Mapping[str, Any]) -> list[str]:
        if not isinstance(arguments, Mapping):
            raise BlenderMCPValidationError("inspect_scene arguments must be a mapping")
        _reject_unknown(arguments, {"objects"}, "inspect_scene")
        raw = arguments.get("objects", ())
        names = _require_sequence(raw, "objects")
        if len(names) > self.limits.max_inspect_objects:
            raise BlenderMCPValidationError("too many objects requested for inspection")
        normalized = [_safe_name(value, "object name") for value in names]
        if len(set(normalized)) != len(normalized):
            raise BlenderMCPValidationError("inspection object names must be unique")
        return normalized

    def _normalize_render(
        self,
        arguments: Mapping[str, Any],
    ) -> tuple[Path, bool, int | None, list[int] | None]:
        if not isinstance(arguments, Mapping):
            raise BlenderMCPValidationError("render_preview arguments must be a mapping")
        _reject_unknown(
            arguments,
            {"output_path", "overwrite", "frame", "frames"},
            "render_preview",
        )
        overwrite = arguments.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise BlenderMCPValidationError("overwrite must be boolean")
        if "frame" in arguments and "frames" in arguments:
            raise BlenderMCPValidationError("frame and frames are mutually exclusive")
        frame = None
        frames = None
        if "frame" in arguments:
            frame = _bounded_integer(arguments["frame"], "frame", 0, 1_000_000)
        elif "frames" in arguments:
            raw_frames = _require_sequence(arguments["frames"], "frames")
            if not 2 <= len(raw_frames) <= 32:
                raise BlenderMCPValidationError("frames must contain between 2 and 32 items")
            frames = [
                _bounded_integer(value, f"frames[{index}]", 0, 1_000_000)
                for index, value in enumerate(raw_frames)
            ]
            if len(set(frames)) != len(frames):
                raise BlenderMCPValidationError("frames must contain unique values")
            frames.sort()
        path = self._resolve_project_file(arguments.get("output_path"), _PREVIEW_EXTENSIONS)
        return path, overwrite, frame, frames

    def _normalize_render_animation(
        self,
        arguments: Mapping[str, Any],
    ) -> tuple[Path, bool, int, int, int]:
        if not isinstance(arguments, Mapping):
            raise BlenderMCPValidationError(
                "render_animation arguments must be a mapping"
            )
        _reject_unknown(
            arguments,
            {"output_path", "frame_start", "frame_end", "fps", "overwrite"},
            "render_animation",
        )
        overwrite = arguments.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise BlenderMCPValidationError("overwrite must be boolean")
        frame_start = _bounded_integer(
            arguments.get("frame_start"), "frame_start", 0, 1_000_000
        )
        frame_end = _bounded_integer(
            arguments.get("frame_end"), "frame_end", 0, 1_000_000
        )
        if (
            frame_end < frame_start
            or frame_end - frame_start + 1 > self.limits.max_total_frames
        ):
            raise BlenderMCPValidationError(
                "frame range is invalid or exceeds the configured limit"
            )
        fps = _bounded_integer(arguments.get("fps"), "fps", 1, 240)
        path = self._resolve_project_file(
            arguments.get("output_path"), _VIDEO_EXTENSIONS
        )
        return path, overwrite, frame_start, frame_end, fps

    def _resolve_project_file(self, value: Any, extensions: frozenset[str]) -> Path:
        if not isinstance(value, (str, os.PathLike)):
            raise BlenderMCPValidationError("output_path must be string or path-like")
        raw = os.fspath(value)
        if not raw or "\x00" in raw:
            raise BlenderMCPValidationError("output_path cannot be empty or contain NUL")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise BlenderMCPSecurityError("output_path must remain inside the project root") from exc
        if resolved == self.project_root or resolved.suffix.lower() not in extensions:
            raise BlenderMCPValidationError("unsupported output path")
        return resolved

    def _copy_preview_safely(
        self,
        source: Path,
        destination: Path,
        *,
        overwrite: bool,
    ) -> None:
        self._copy_output_safely(
            source,
            destination,
            overwrite=overwrite,
            media_kind="preview",
            max_bytes=self.limits.max_preview_bytes,
        )

    def _copy_video_safely(
        self,
        source: Path,
        destination: Path,
        *,
        overwrite: bool,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._copy_output_safely(
            source,
            destination,
            overwrite=overwrite,
            media_kind="animation",
            max_bytes=self.limits.max_video_bytes,
            cancelled=cancelled,
        )

    def _copy_output_safely(
        self,
        source: Path,
        destination: Path,
        *,
        overwrite: bool,
        media_kind: str,
        max_bytes: int,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if os.name == "nt":
            raise BlenderMCPSecurityError(
                f"secure {media_kind} export is unavailable on Windows"
            )
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory = getattr(os, "O_DIRECTORY", 0)
        scratch_root = Path(str(self._attestation["scratch_root"]))
        scratch_fd = os.open(scratch_root, os.O_RDONLY | directory | nofollow)
        source_fd = -1
        root_fd = -1
        parent_fd = -1
        temp_name = f".maestro-{media_kind}-{uuid.uuid4().hex}.tmp"
        try:
            source_fd = os.open(
                source.name,
                os.O_RDONLY | nofollow,
                dir_fd=scratch_fd,
            )
            root_fd = os.open(
                self.project_root,
                os.O_RDONLY | directory | nofollow,
            )
            parent_fd = root_fd
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise BlenderMCPToolError(
                    f"rendered {media_kind} source is not a regular file"
                )
            if source_stat.st_size > max_bytes:
                raise BlenderMCPToolError(
                    f"rendered {media_kind} exceeded the size limit"
                )

            relative = destination.relative_to(self.project_root)
            for part in relative.parts[:-1]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow,
                    dir_fd=parent_fd,
                )
                if parent_fd != root_fd:
                    os.close(parent_fd)
                parent_fd = next_fd

            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                header_size = len(_PNG_SIGNATURE) if media_kind == "preview" else 16
                header = os.read(source_fd, header_size)
                if media_kind == "preview":
                    valid_header = header == _PNG_SIGNATURE
                    invalid_message = "rendered preview is not a PNG image"
                else:
                    valid_header = _is_mp4_header(header, source_stat.st_size)
                    invalid_message = "rendered animation is not an MP4 video"
                if not valid_header:
                    raise BlenderMCPToolError(invalid_message)
                _write_all(temp_fd, header)
                total = len(header)
                while chunk := os.read(source_fd, 1024 * 1024):
                    self._check_cancelled(cancelled)
                    total += len(chunk)
                    if total > max_bytes:
                        raise BlenderMCPToolError(
                            f"rendered {media_kind} exceeded the size limit"
                        )
                    _write_all(temp_fd, chunk)
                os.fsync(temp_fd)
            finally:
                os.close(temp_fd)

            if overwrite:
                os.replace(
                    temp_name,
                    relative.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            else:
                try:
                    os.link(
                        temp_name,
                        relative.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise BlenderMCPValidationError(
                        f"{media_kind} output already exists"
                    ) from exc
                os.unlink(temp_name, dir_fd=parent_fd)
        finally:
            if parent_fd >= 0:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            if parent_fd >= 0 and parent_fd != root_fd:
                os.close(parent_fd)
            if root_fd >= 0:
                os.close(root_fd)
            if source_fd >= 0:
                os.close(source_fd)
            os.close(scratch_fd)

    @staticmethod
    def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
        if cancelled is not None and cancelled():
            raise BlenderMCPCancelled("Blender MCP operation was cancelled")


def _write_all(file_descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(file_descriptor, view)
        view = view[written:]


def _is_mp4_header(header: bytes, file_size: int) -> bool:
    if len(header) < 16 or header[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(header[:4], "big")
    if box_size == 0:
        return file_size >= 16
    if box_size == 1:
        extended_size = int.from_bytes(header[8:16], "big")
        return 24 <= extended_size <= file_size
    return 16 <= box_size <= file_size


def _activate_project_scene_code(scene_path: Path) -> str:
    path_literal = repr(str(scene_path))
    return f"""# Maestro deterministic activate_project_scene v1
import bpy
import os
scene_path = {path_literal}
if os.path.isfile(scene_path):
    bpy.ops.wm.open_mainfile(filepath=scene_path)
else:
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.wm.save_as_mainfile(filepath=scene_path, check_existing=False)
result = {{"status": "ok", "scene_path": scene_path}}
"""


def _save_project_scene_code(scene_path: Path) -> str:
    path_literal = repr(str(scene_path))
    return f"""# Maestro deterministic save_project_scene v1
import bpy
scene_path = {path_literal}
bpy.ops.wm.save_as_mainfile(filepath=scene_path, check_existing=False)
result = {{"status": "ok", "scene_path": scene_path}}
"""


def _scene_create_code(scene: Mapping[str, Any]) -> str:
    payload = repr(json.dumps(scene, separators=(",", ":"), allow_nan=False))
    return f"""# Maestro deterministic scene_create v1
import bpy
import json
import math
spec = json.loads({payload})
existing = [item[\"name\"] for item in spec[\"objects\"] if bpy.data.objects.get(item[\"name\"]) is not None]
if existing and not spec[\"clear_scene\"]:
    raise ValueError(\"Objects already exist: \" + \", \".join(existing))
if spec[\"clear_scene\"]:
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
created = []
operators = {{
    \"cube\": bpy.ops.mesh.primitive_cube_add,
    \"sphere\": bpy.ops.mesh.primitive_uv_sphere_add,
    \"cylinder\": bpy.ops.mesh.primitive_cylinder_add,
    \"cone\": bpy.ops.mesh.primitive_cone_add,
    \"torus\": bpy.ops.mesh.primitive_torus_add,
    \"plane\": bpy.ops.mesh.primitive_plane_add,
}}
for item in spec[\"objects\"]:
    operators[item[\"primitive\"]](location=item.get(\"location\", (0.0, 0.0, 0.0)))
    obj = bpy.context.active_object
    obj.name = item[\"name\"]
    if \"rotation_degrees\" in item:
        obj.rotation_euler = tuple(math.radians(v) for v in item[\"rotation_degrees\"])
    if \"scale\" in item:
        obj.scale = item[\"scale\"]
    material_spec = item.get(\"material\")
    if material_spec:
        material = bpy.data.materials.get(material_spec[\"name\"])
        if material is None:
            material = bpy.data.materials.new(material_spec[\"name\"])
        material.diffuse_color = material_spec[\"color\"]
        obj.data.materials.clear()
        obj.data.materials.append(material)
    created.append(obj.name)
result = {{\"status\": \"ok\", \"created\": created}}
"""


def _animate_keyframes_code(animation: Mapping[str, Any]) -> str:
    payload = repr(json.dumps(animation, separators=(",", ":"), allow_nan=False))
    return f"""# Maestro deterministic animate_keyframes v1
import bpy
import json
import math
spec = json.loads({payload})
missing = [item[\"name\"] for item in spec[\"objects\"] if bpy.data.objects.get(item[\"name\"]) is None]
if missing:
    raise ValueError(\"Objects not found: \" + \", \".join(missing))
scene = bpy.context.scene
scene.frame_start = spec[\"frame_start\"]
scene.frame_end = spec[\"frame_end\"]
inserted = 0
for item in spec[\"objects\"]:
    obj = bpy.data.objects[item[\"name\"]]
    for key in item[\"keyframes\"]:
        frame = key[\"frame\"]
        if \"location\" in key:
            obj.location = key[\"location\"]
            obj.keyframe_insert(data_path=\"location\", frame=frame)
        if \"rotation_degrees\" in key:
            obj.rotation_euler = tuple(math.radians(v) for v in key[\"rotation_degrees\"])
            obj.keyframe_insert(data_path=\"rotation_euler\", frame=frame)
        if \"scale\" in key:
            obj.scale = key[\"scale\"]
            obj.keyframe_insert(data_path=\"scale\", frame=frame)
        action = obj.animation_data.action if obj.animation_data else None
        if action:
            data_paths = []
            if \"location\" in key:
                data_paths.append(\"location\")
            if \"rotation_degrees\" in key:
                data_paths.append(\"rotation_euler\")
            if \"scale\" in key:
                data_paths.append(\"scale\")
            for data_path in data_paths:
                for array_index in range(3):
                    curve = action.fcurve_ensure_for_datablock(obj, data_path, index=array_index)
                    for point in curve.keyframe_points:
                        if int(round(point.co.x)) == frame:
                            point.interpolation = key[\"interpolation\"]
        inserted += 1
result = {{\"status\": \"ok\", \"objects\": len(spec[\"objects\"]), \"keyframes\": inserted}}
"""


def _frame_set_code(frame: int) -> str:
    return f"""# Maestro deterministic render_preview frame v1
import bpy
scene = bpy.context.scene
scene.frame_set({frame})
result = {{"status": "ok", "frame": {frame}}}
"""


def _ensure_render_scene_code() -> str:
    return """# Maestro deterministic render_scene_setup v1
import bpy
from mathutils import Vector
scene = bpy.context.scene
cameras = sorted(
    (obj for obj in scene.objects if obj.type == "CAMERA"),
    key=lambda obj: obj.name,
)
camera_created = False
if scene.camera is None:
    if cameras:
        scene.camera = cameras[0]
    else:
        camera_data = bpy.data.cameras.new("MaestroCamera")
        camera_data.lens = 50.0
        camera = bpy.data.objects.new("MaestroCamera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = (8.0, -8.0, 6.0)
        camera.rotation_euler = (
            Vector((0.0, 0.0, 1.0)) - camera.location
        ).to_track_quat("-Z", "Y").to_euler()
        scene.camera = camera
        camera_created = True
lights = sorted(
    (obj for obj in scene.objects if obj.type == "LIGHT"),
    key=lambda obj: obj.name,
)
light_created = False
if not lights:
    light_data = bpy.data.lights.new("MaestroKeyLight", type="AREA")
    light_data.energy = 1200.0
    light_data.shape = "DISK"
    light_data.size = 5.0
    light = bpy.data.objects.new("MaestroKeyLight", light_data)
    scene.collection.objects.link(light)
    light.location = (4.0, -4.0, 8.0)
    light.rotation_euler = (
        Vector((0.0, 0.0, 1.0)) - light.location
    ).to_track_quat("-Z", "Y").to_euler()
    light_created = True
result = {
    "status": "ok",
    "camera": scene.camera.name,
    "camera_created": camera_created,
    "light_created": light_created,
}
"""


def _render_animation_code(
    frame_prefix: Path,
    *,
    frame_start: int,
    frame_end: int,
    fps: int,
) -> str:
    path_literal = repr(str(frame_prefix))
    return f"""# Maestro deterministic render_animation v1
import bpy
scene = bpy.context.scene
scene.frame_start = {frame_start}
scene.frame_end = {frame_end}
scene.render.fps = {fps}
scene.render.fps_base = 1.0
scene.render.image_settings.file_format = "PNG"
scene.render.use_file_extension = True
scene.render.filepath = {path_literal}
bpy.ops.render.render(animation=True)
result = {{
    "status": "ok",
    "frame_start": {frame_start},
    "frame_end": {frame_end},
    "fps": {fps},
}}
"""


def _encode_png_sequence_to_mp4(
    frame_prefix: Path,
    output_path: Path,
    *,
    frame_start: int,
    frame_end: int,
    fps: int,
    max_input_bytes: int,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    frame_count = frame_end - frame_start + 1
    total_bytes = 0
    for frame in range(frame_start, frame_end + 1):
        path = Path(f"{frame_prefix}{frame:04d}.png")
        try:
            metadata = path.lstat()
            with path.open("rb") as handle:
                signature = handle.read(len(_PNG_SIGNATURE))
        except OSError as exc:
            raise BlenderMCPToolError("Blender did not render every animation frame") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or path.parent.resolve() != frame_prefix.parent.resolve()
            or signature != _PNG_SIGNATURE
        ):
            raise BlenderMCPToolError("Blender rendered an invalid animation frame")
        total_bytes += metadata.st_size
        if total_bytes > max_input_bytes:
            raise BlenderMCPToolError("rendered animation frames exceeded the size limit")
        if cancelled is not None and cancelled():
            raise BlenderMCPCancelled("Blender MCP operation was cancelled")

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg = Path(get_ffmpeg_exe()).resolve(strict=True)
    except (ImportError, OSError, RuntimeError) as exc:
        raise BlenderMCPToolError("the managed FFmpeg encoder is unavailable") from exc
    command = [
        str(ffmpeg),
        "-v", "error",
        "-y",
        "-framerate", str(fps),
        "-start_number", str(frame_start),
        "-i", f"{frame_prefix}%04d.png",
        "-frames:v", str(frame_count),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + max(300.0, frame_count * 10.0)
    while process.poll() is None:
        if cancelled is not None and cancelled():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            process.communicate()
            raise BlenderMCPCancelled("Blender MCP operation was cancelled")
        if time.monotonic() >= deadline:
            process.kill()
            process.communicate()
            raise BlenderMCPToolError("animation encoding timed out")
        time.sleep(0.05)
    _stdout, stderr_value = process.communicate()
    stderr = (stderr_value or b"")[:500]
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise BlenderMCPToolError(
            f"managed FFmpeg could not encode the animation: {detail or 'unknown error'}"
        )


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise BlenderMCPValidationError(f"{label} has disallowed fields: {sorted(unknown)}")


def _require_sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BlenderMCPValidationError(f"{field} must be a sequence")
    return value


def _safe_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise BlenderMCPValidationError(f"{field} is invalid")
    return value


def _finite_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise BlenderMCPValidationError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise BlenderMCPValidationError(f"{field} is outside the allowed range")
    return number


def _vector3(
    value: Any,
    field: str,
    *,
    minimum: float = -1_000_000,
    maximum: float = 1_000_000,
) -> list[float]:
    values = _require_sequence(value, field)
    if len(values) != 3:
        raise BlenderMCPValidationError(f"{field} must contain exactly three numbers")
    return [_finite_number(item, field, minimum, maximum) for item in values]


def _color4(value: Any, field: str) -> list[float]:
    values = _require_sequence(value, field)
    if len(values) != 4:
        raise BlenderMCPValidationError(f"{field} must contain exactly four numbers")
    return [_finite_number(item, field, 0.0, 1.0) for item in values]


def _bounded_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise BlenderMCPValidationError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.match(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?", value)
    if match is None:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _provisioning_main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attest or reuse Maestro Blender support")
    commands = parser.add_subparsers(dest="command", required=True)

    provision_mcp = commands.add_parser("provision-mcp")
    provision_mcp.add_argument("--destination", required=True)

    attest_mcp = commands.add_parser("attest-mcp")
    attest_mcp.add_argument("--checkout", required=True)

    provision_runtime = commands.add_parser("provision-runtime")
    provision_runtime.add_argument("--checkout", required=True)
    provision_runtime.add_argument("--marker", required=True)

    attest_runtime = commands.add_parser("attest-runtime")
    attest_runtime.add_argument("--marker", required=True)

    options = parser.parse_args(arguments)
    if options.command == "provision-mcp":
        reused = reuse_compatible_mcp_checkout(options.destination)
        if reused:
            print("Reused an attested Blender MCP checkout")
        else:
            quarantined = quarantine_invalid_mcp_checkout(options.destination)
            if quarantined is not None:
                print("Moved an incomplete Blender MCP checkout aside for safe repair")
            else:
                print("No reusable Blender MCP checkout found")
        return 0
    if options.command == "attest-mcp":
        checkout = Path(options.checkout).expanduser().resolve()
        facts = attest_mcp_checkout(checkout)
        _write_mcp_attestation_marker(checkout, facts)
        print(f"Attested Blender MCP {facts['package_version']} over {facts['transport']}")
        return 0
    if options.command == "provision-runtime":
        runtime = provision_discovered_blender_runtime(options.checkout, options.marker)
        if runtime:
            print(f"Using {runtime['source']} Blender {runtime['version']}")
        else:
            print("No reusable Blender 5.1+ runtime found")
        return 0
    if options.command == "attest-runtime":
        runtime = read_blender_runtime_info(options.marker)
        if not runtime:
            raise BlenderMCPSecurityError("Blender runtime marker failed attestation")
        print(
            f"Attested {runtime['source']} Blender {runtime['version']} "
            f"over {runtime['transport']}"
        )
        return 0
    raise AssertionError("unreachable provisioning command")


if __name__ == "__main__":
    raise SystemExit(_provisioning_main())
