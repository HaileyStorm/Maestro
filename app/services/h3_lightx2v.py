"""Pinned managed LightX2V four-evaluation adapter for MiniMax H3."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import secrets
import struct
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen


H3_LIGHTX2V_PROFILE_ID = "h3_lightx2v_fl2v_4_v1"
H3_LIGHTX2V_FILENAME = "minimax_h3_fl2v_turbo_4step_v0.1.safetensors"
H3_LIGHTX2V_REPO = "lightx2v/Minimax-h3-Turbo"
H3_LIGHTX2V_REVISION = "b65e359c0d128b3c5e08e0f5bf2791b794378588"
H3_LIGHTX2V_URL = (
    f"https://huggingface.co/{H3_LIGHTX2V_REPO}/resolve/"
    f"{H3_LIGHTX2V_REVISION}/{H3_LIGHTX2V_FILENAME}"
)
H3_LIGHTX2V_SIZE = 1_383_677_888
H3_LIGHTX2V_SHA256 = "5ff4a12c8b4599fec716e1b15a45e504e0d1129111896bdcde5ac4a15e395b29"
H3_LIGHTX2V_SOURCE_COMMIT = "82423dcbcf4d99fd5a31086a7633521438443c8f"
H3_LIGHTX2V_EFFECTIVE_SCALE = 0.0625
H3_LIGHTX2V_AUTHORED_STEPS = 4

_HEADER_LIMIT = 16 * 1024 * 1024
_MANIFEST_VERSION = 1
_APP_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ROOT = _APP_ROOT / "loras" / "minimax_h3" / ".managed-lightx2v"
_PUBLISH_LOCK = threading.RLock()
_PINNED_METADATA = {
    "source": (
        "/mnt/aigc/fanxiangyu/repos/video_gen/zoe-diffusion/results/"
        "dmd_minimax_h3_t2va4/checkpointing/00000650/gen-base_dit.pt"
    ),
    "format": "pt",
    "floating_dtype": "bfloat16",
}


def _tensor_shapes() -> dict[str, tuple[int, int]]:
    """Return the complete immutable-release tensor contract.

    The map was derived from the safetensors header at
    ``H3_LIGHTX2V_REVISION``.  It is generated here to keep 624 exact entries
    reviewable without checking in a large machine-generated literal.
    """
    shapes: dict[str, tuple[int, int]] = {}

    def add(prefix: str) -> None:
        modules = {
            "attn.to_k": ((128, 5376), (7168, 128)),
            "attn.to_out.0": ((128, 7168), (5376, 128)),
            "attn.to_q": ((128, 5376), (7168, 128)),
            "attn.to_v": ((128, 5376), (7168, 128)),
            "ff.net.0.proj": ((128, 5376), (28672, 128)),
            "ff.net.2": ((128, 14336), (5376, 128)),
        }
        for module, (a_shape, b_shape) in modules.items():
            stem = f"{prefix}.{module}"
            shapes[f"{stem}.lora_A.default.weight"] = a_shape
            shapes[f"{stem}.lora_B.default.weight"] = b_shape

    for block in range(50):
        add(f"transformer_blocks.{block}")
    for block in range(2):
        add(f"token_refiner.refiner_blocks.{block}")
    return shapes


H3_LIGHTX2V_TENSOR_SHAPES = _tensor_shapes()


class H3LightX2VError(RuntimeError):
    pass


class H3LightX2VCompatibilityError(H3LightX2VError):
    pass


class H3LightX2VAssetsUnavailable(H3LightX2VError):
    pass


@dataclass(frozen=True)
class H3LightX2VAssets:
    profile_id: str
    release_dir: Path
    lora_path: Path


def _root(root: str | os.PathLike[str] | None = None) -> Path:
    return Path(root) if root is not None else _DEFAULT_ROOT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _header(path: Path) -> tuple[dict[str, Any], int]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(8)
        if len(raw) != 8:
            raise H3LightX2VCompatibilityError("LightX2V asset has no safetensors header")
        length = struct.unpack("<Q", raw)[0]
        if length < 2 or length > _HEADER_LIMIT or length + 8 > size:
            raise H3LightX2VCompatibilityError("LightX2V safetensors header is invalid")
        try:
            header = json.loads(handle.read(length))
        except Exception as error:
            raise H3LightX2VCompatibilityError("LightX2V safetensors JSON is invalid") from error
    if not isinstance(header, dict):
        raise H3LightX2VCompatibilityError("LightX2V safetensors header is not an object")
    return header, 8 + length


def _validate_tensor_header_contract(
    header: Mapping[str, Any], payload_size: int,
) -> None:
    if header.get("__metadata__") != _PINNED_METADATA:
        raise H3LightX2VCompatibilityError(
            "LightX2V metadata does not match the pinned release"
        )
    tensors = {
        key: value for key, value in header.items() if key != "__metadata__"
    }
    expected = H3_LIGHTX2V_TENSOR_SHAPES
    if set(tensors) != set(expected):
        missing = sorted(set(expected) - set(tensors))[:3]
        extra = sorted(set(tensors) - set(expected))[:3]
        raise H3LightX2VCompatibilityError(
            "LightX2V tensor key set is not exact "
            f"(missing={missing}, extra={extra})"
        )
    intervals: list[tuple[int, int, str]] = []
    for key, shape in expected.items():
        spec = tensors[key]
        if not isinstance(spec, dict) or spec.get("dtype") != "BF16":
            raise H3LightX2VCompatibilityError(f"{key} must be BF16")
        if tuple(spec.get("shape") or ()) != shape:
            raise H3LightX2VCompatibilityError(
                f"{key} has shape {spec.get('shape')}; expected {list(shape)}"
            )
        offsets = spec.get("data_offsets")
        if not (
            isinstance(offsets, list)
            and len(offsets) == 2
            and all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in offsets
            )
        ):
            raise H3LightX2VCompatibilityError(f"{key} has invalid offsets")
        start, end = offsets
        elements = int(shape[0]) * int(shape[1])
        if start < 0 or end - start != elements * 2 or end > payload_size:
            raise H3LightX2VCompatibilityError(
                f"{key} has an invalid byte extent"
            )
        intervals.append((start, end, key))
    cursor = 0
    for start, end, key in sorted(intervals):
        if start != cursor:
            raise H3LightX2VCompatibilityError(
                f"{key} does not form a contiguous safetensors payload"
            )
        cursor = end
    if cursor != payload_size:
        raise H3LightX2VCompatibilityError(
            "LightX2V payload has unaccounted bytes"
        )


def validate_lightx2v_lora(path: str | os.PathLike[str]) -> Path:
    source = Path(path)
    if not source.is_file() or source.stat().st_size != H3_LIGHTX2V_SIZE:
        raise H3LightX2VCompatibilityError(
            f"{source.name} must be exactly {H3_LIGHTX2V_SIZE} bytes"
        )
    if _sha256(source) != H3_LIGHTX2V_SHA256:
        raise H3LightX2VCompatibilityError("LightX2V SHA256 does not match the pinned release")
    header, data_start = _header(source)
    _validate_tensor_header_contract(
        header, source.stat().st_size - data_start,
    )
    return source


def _release_name() -> str:
    return f"{H3_LIGHTX2V_REVISION[:12]}-{H3_LIGHTX2V_SHA256[:12]}"


def _manifest() -> dict[str, Any]:
    return lightx2v_runtime_identity() | {
        "version": _MANIFEST_VERSION,
        "release": _release_name(),
    }


def lightx2v_runtime_identity() -> dict[str, Any]:
    """Return the complete content-free identity persisted for recovery."""
    return {
        "profile_id": H3_LIGHTX2V_PROFILE_ID,
        "repository": H3_LIGHTX2V_REPO,
        "revision": H3_LIGHTX2V_REVISION,
        "sha256": H3_LIGHTX2V_SHA256,
        "source_commit": H3_LIGHTX2V_SOURCE_COMMIT,
        "effective_scale": H3_LIGHTX2V_EFFECTIVE_SCALE,
        "authored_evaluations": H3_LIGHTX2V_AUTHORED_STEPS,
        "scheduler_grid_points": H3_LIGHTX2V_AUTHORED_STEPS + 1,
    }


def validate_lightx2v_runtime_identity(value: Any) -> dict[str, Any]:
    expected = lightx2v_runtime_identity()
    if value != expected:
        raise H3LightX2VCompatibilityError(
            "LightX2V recovery identity does not match the pinned runtime"
        )
    return expected


def guard_lightx2v_lora_load(
    prepare: Callable[[], Any], cleanup: Callable[[], Any],
) -> Any:
    """Guarantee managed tensors are removed after any partial load failure."""
    try:
        return prepare()
    except BaseException:
        cleanup()
        raise


def call_with_lightx2v_cleanup(
    enabled: bool,
    cleanup: Callable[[], Any],
    operation: Callable[..., Any],
    *args,
    **kwargs,
) -> Any:
    """Release LightX immediately after the native model call on every exit."""
    try:
        return operation(*args, **kwargs)
    finally:
        if enabled:
            cleanup()


def publish_lightx2v_asset(source: str | os.PathLike[str], *, root=None) -> H3LightX2VAssets:
    source = validate_lightx2v_lora(source)
    managed = _root(root)
    release = managed / "releases" / _release_name()
    with _PUBLISH_LOCK:
        managed.mkdir(parents=True, exist_ok=True)
        release.parent.mkdir(parents=True, exist_ok=True)
        if release.exists():
            try:
                validate_lightx2v_lora(release / H3_LIGHTX2V_FILENAME)
            except H3LightX2VError:
                quarantine = (
                    managed / "quarantine"
                    / f"{release.name}-{secrets.token_hex(4)}"
                )
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                os.replace(release, quarantine)
        if not release.exists():
            temporary = Path(
                tempfile.mkdtemp(prefix=".publish-", dir=release.parent)
            )
            try:
                shutil.copyfile(source, temporary / H3_LIGHTX2V_FILENAME)
                validate_lightx2v_lora(temporary / H3_LIGHTX2V_FILENAME)
                os.replace(temporary, release)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        fd, temp = tempfile.mkstemp(prefix=".manifest-", dir=managed)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(_manifest(), handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, managed / "current.json")
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return resolve_lightx2v_assets(managed)


def resolve_lightx2v_assets(root=None, *, verify: bool = True) -> H3LightX2VAssets:
    managed = _root(root)
    try:
        manifest = json.loads((managed / "current.json").read_text(encoding="utf-8"))
    except Exception as error:
        raise H3LightX2VAssetsUnavailable("LightX2V H3 asset is not installed") from error
    if manifest != _manifest():
        raise H3LightX2VAssetsUnavailable("LightX2V H3 manifest does not match the pinned release")
    release = managed / "releases" / _release_name()
    path = release / H3_LIGHTX2V_FILENAME
    if verify:
        try:
            validate_lightx2v_lora(path)
        except H3LightX2VCompatibilityError as error:
            raise H3LightX2VAssetsUnavailable(str(error)) from error
    elif not path.is_file() or path.stat().st_size != H3_LIGHTX2V_SIZE:
        raise H3LightX2VAssetsUnavailable(
            "LightX2V H3 managed asset is incomplete"
        )
    return H3LightX2VAssets(H3_LIGHTX2V_PROFILE_ID, release, path)


def acquire_lightx2v_asset(*, root=None, download: Callable | None = None) -> H3LightX2VAssets:
    try:
        return resolve_lightx2v_assets(root)
    except H3LightX2VError:
        pass
    managed = _root(root)
    staging = managed / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    destination = staging / H3_LIGHTX2V_FILENAME
    if download is None:
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            request = Request(
                H3_LIGHTX2V_URL,
                headers={"User-Agent": "Maestro-H3-LightX2V/1"},
            )
            with (
                urlopen(request, timeout=30) as response,
                temporary.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle, 8 * 1024 * 1024)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    else:
        result = download(H3_LIGHTX2V_URL, destination)
        if result is not None:
            destination = Path(result)
    return publish_lightx2v_asset(destination, root=managed)


def lightx2v_assets_status(root=None) -> dict[str, Any]:
    try:
        # The estimator polls this path continuously. The immutable manifest
        # and exact size are enough to report download state; generation calls
        # ``resolve_lightx2v_assets(..., verify=True)`` and rehashes before use.
        assets = resolve_lightx2v_assets(root, verify=False)
    except H3LightX2VError as error:
        return {"available": False, "profile_id": H3_LIGHTX2V_PROFILE_ID, "reason": str(error), "revision": H3_LIGHTX2V_REVISION}
    return {"available": True, "profile_id": assets.profile_id, "reason": None, "revision": H3_LIGHTX2V_REVISION, "filename": assets.lora_path.name}


def lightx2v_requested(custom: Mapping[str, Any] | None) -> bool:
    return isinstance(custom, Mapping) and custom.get("h3_lightx2v_profile") not in (None, "", False)


def lightx2v_scheduler_grid_points(authored_steps: Any) -> int:
    try:
        steps = int(authored_steps)
    except (TypeError, ValueError) as error:
        raise H3LightX2VCompatibilityError("LightX2V H3 requires exactly four model evaluations") from error
    if isinstance(authored_steps, bool) or steps != authored_steps or steps != H3_LIGHTX2V_AUTHORED_STEPS:
        raise H3LightX2VCompatibilityError("LightX2V H3 requires exactly four model evaluations")
    return 5


def validate_lightx2v_request(
    *,
    selected_model_type: str,
    model_def: Mapping[str, Any],
    custom_settings: Mapping[str, Any] | None,
    authored_steps: Any,
    semantic_references: bool = False,
    multisegment: bool = False,
    activated_loras=None,
    loras_multipliers=None,
    skip_steps_cache_type=None,
    native_boundary: bool = False,
) -> bool:
    if not lightx2v_requested(custom_settings):
        return False
    if custom_settings.get("h3_lightx2v_profile") != H3_LIGHTX2V_PROFILE_ID:
        raise H3LightX2VCompatibilityError("Unknown LightX2V H3 profile")
    if selected_model_type != "minimax_h3" or model_def.get("minimax_h3_reference_mode"):
        raise H3LightX2VCompatibilityError("LightX2V H3 supports only Base FL2VA")
    if semantic_references:
        raise H3LightX2VCompatibilityError(
            "LightX2V H3 does not support semantic references"
        )
    if multisegment:
        raise H3LightX2VCompatibilityError(
            "LightX2V H3 is limited to one native segment"
        )
    if custom_settings.get("h3_attention_engine") != "sdpa":
        raise H3LightX2VCompatibilityError("LightX2V H3 requires Dense SDPA")
    if (
        custom_settings.get("h3_turbo_profile")
        or custom_settings.get("h3_spectrum_profile")
    ):
        raise H3LightX2VCompatibilityError(
            "LightX2V cannot combine with another H3 accelerator"
        )
    if native_boundary:
        raise H3LightX2VCompatibilityError(
            "LightX2V does not support native boundary conditioning"
        )
    if activated_loras or str(loras_multipliers or "").strip():
        raise H3LightX2VCompatibilityError(
            "LightX2V does not support other LoRAs"
        )
    if skip_steps_cache_type:
        raise H3LightX2VCompatibilityError(
            "LightX2V does not support step caches"
        )
    if model_def.get("h3_w4a8") or model_def.get("h3_convrot"):
        raise H3LightX2VCompatibilityError(
            "LightX2V does not support W4A8, ConvRot, or PinkCherry"
        )
    lightx2v_scheduler_grid_points(authored_steps)
    return True


__all__ = [name for name in globals() if name.startswith("H3_LIGHTX2V_")] + [
    "H3LightX2VError", "H3LightX2VCompatibilityError",
    "H3LightX2VAssetsUnavailable", "acquire_lightx2v_asset",
    "call_with_lightx2v_cleanup", "guard_lightx2v_lora_load",
    "lightx2v_assets_status", "lightx2v_requested",
    "lightx2v_runtime_identity", "lightx2v_scheduler_grid_points",
    "publish_lightx2v_asset", "resolve_lightx2v_assets",
    "validate_lightx2v_lora", "validate_lightx2v_request",
    "validate_lightx2v_runtime_identity",
]
