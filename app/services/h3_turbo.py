"""Managed LarryVRH MiniMax-H3 Turbo assets and runtime policy.

Turbo is deliberately an accelerator profile for the ordinary ``minimax_h3``
Base FL2VA model, not a separately selectable model and not a public/user LoRA.
The managed release is published as one versioned directory only after both
artifacts pass size, digest, safetensors-header, key, dtype, and shape checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping
from urllib.request import Request, urlopen


H3_TURBO_PROFILE_ID = "h3_turbo_v4"
H3_TURBO_LORA_FILENAME = "minimax_h3_turbo_v4_step600_ema.safetensors"
H3_TURBO_GRID_FILENAME = "h3_silu_temb_grid.safetensors"

H3_TURBO_LORA_REPO = "larryvrh/MiniMax-H3-Turbo-Lora"
H3_TURBO_LORA_REVISION = "afc0346516372a17162c14df3c5264de1d9aa1c0"
H3_TURBO_LORA_URL = (
    f"https://huggingface.co/{H3_TURBO_LORA_REPO}/resolve/"
    f"{H3_TURBO_LORA_REVISION}/{H3_TURBO_LORA_FILENAME}"
)
H3_TURBO_LORA_SIZE = 779_849_816
H3_TURBO_LORA_SHA256 = "5f3a626cd72c93a8b9318d6760c510bc5092d2ab13aaba1f932c5bab07a416d3"

H3_TURBO_NODE_REPO = "Larryvrh/ComfyUI-MiniMax-H3-Turbo"
H3_TURBO_NODE_COMMIT = "55fee864dd7b2976b1c4ce3c3d5f7968f181409f"
H3_TURBO_GRID_URL = (
    f"https://raw.githubusercontent.com/{H3_TURBO_NODE_REPO}/"
    f"{H3_TURBO_NODE_COMMIT}/{H3_TURBO_GRID_FILENAME}"
)
H3_TURBO_GRID_SIZE = 5_510_600
H3_TURBO_GRID_SHA256 = "30eb3c2cc7fb6b470d9717ff840d359313ac27cd64b705e32da1baa10f72d6a8"

H3_TURBO_AUTHORED_STEPS_MIN = 4
H3_TURBO_AUTHORED_STEPS_MAX = 8
H3_TURBO_STRENGTH = 1.0
H3_TURBO_SCHEDULE_ALGORITHM_VERSION = "maestro_h3_turbo_dual_clock_v1"
H3_TURBO_BASE_CHECKPOINT = "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
H3_TURBO_W4A8_CHECKPOINT = "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"
H3_TURBO_PINKCHERRY_CHECKPOINT = "PinkCherry_h3_fl2va_int8_convrot_v0.2-alpha.safetensors"
H3_TURBO_REF2VA_CHECKPOINT = "minimax_h3_ref2va_pruned_fp8_scaled.safetensors"

_MODEL_VARIANTS = {
    H3_TURBO_BASE_CHECKPOINT: {
        "id": "base_fp8",
        "label": "Base FL2VA scaled FP8",
        "reference_mode": False,
        "adaln": "compact_curve",
        "backbone": "mmgp",
    },
    H3_TURBO_W4A8_CHECKPOINT: {
        "id": "base_w4a8",
        "label": "Base FL2VA W4A8",
        "reference_mode": False,
        "adaln": "compact_curve",
        "backbone": "residual_output",
    },
    H3_TURBO_PINKCHERRY_CHECKPOINT: {
        "id": "pinkcherry_int8",
        "label": "PinkCherry FL2VA INT8 ConvRot",
        "reference_mode": False,
        "adaln": "checkpoint_selected",
        "backbone": "residual_output",
    },
    H3_TURBO_REF2VA_CHECKPOINT: {
        "id": "ref2va_fp8",
        "label": "Ref2VA scaled FP8",
        "reference_mode": True,
        "adaln": "compact_curve",
        "backbone": "mmgp",
    },
}

_MANIFEST_VERSION = 1
_MANIFEST_FILENAME = "current.json"
_REF2VA_VALIDATION_FILENAME = "ref2va-validation.json"
_HEADER_LIMIT = 16 * 1024 * 1024
_APP_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANAGED_ROOT = _APP_ROOT / "loras" / "minimax_h3" / ".managed"


class H3TurboError(RuntimeError):
    """Base class for managed Turbo failures."""


class H3TurboCompatibilityError(H3TurboError):
    """The selected request/model/artifact is not in the validated matrix."""


class H3TurboAssetsUnavailable(H3TurboError):
    """The managed release has not been acquired or is no longer valid."""


@dataclass(frozen=True)
class H3TurboAssets:
    profile_id: str
    release_dir: Path
    lora_path: Path
    grid_path: Path


@dataclass(frozen=True)
class H3TurboSchedule:
    """Immutable execution plan for H3 Turbo's video and audio clocks."""

    profile_id: str
    algorithm_version: str
    authored_video_steps: int
    video_scheduler_steps: int
    audio_scheduler_steps: int
    master_evaluations: int
    video_grid_points: int
    audio_grid_points: int
    video_timestep_indices: tuple[int, ...]
    audio_timestep_indices: tuple[int, ...]
    video_advance_ticks: tuple[int, ...]

    def public_identity(self) -> dict[str, str | int]:
        """Return the canonical JSON-safe identity used by later durable seams."""

        return {
            "profile_id": self.profile_id,
            "algorithm_version": self.algorithm_version,
            "authored_video_steps": self.authored_video_steps,
            # These two names are retained as stable compatibility aliases for
            # launch/benchmark consumers; their values mean scheduler steps,
            # not joint transformer-head executions.
            "video_evaluations": self.video_scheduler_steps,
            "audio_evaluations": self.audio_scheduler_steps,
            "evaluation_alias_semantics": "scheduler_steps",
            "master_evaluations": self.master_evaluations,
            "transformer_evaluations": self.master_evaluations,
            "video_scheduler_steps": self.video_scheduler_steps,
            "audio_scheduler_steps": self.audio_scheduler_steps,
        }


def _managed_root(root: str | os.PathLike[str] | None = None) -> Path:
    return Path(root) if root is not None else _DEFAULT_MANAGED_ROOT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_header(path: Path) -> tuple[dict[str, Any], int, int]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw_size = handle.read(8)
        if len(raw_size) != 8:
            raise H3TurboCompatibilityError(f"{path.name} has no safetensors header")
        header_size = struct.unpack("<Q", raw_size)[0]
        if header_size <= 1 or header_size > _HEADER_LIMIT or 8 + header_size > size:
            raise H3TurboCompatibilityError(f"{path.name} has an invalid safetensors header size")
        raw_header = handle.read(header_size)
    try:
        header = json.loads(raw_header)
    except Exception as error:
        raise H3TurboCompatibilityError(f"{path.name} has invalid safetensors JSON") from error
    if not isinstance(header, dict):
        raise H3TurboCompatibilityError(f"{path.name} has a non-object safetensors header")
    return header, size, 8 + header_size


def _tensor_shapes() -> dict[str, tuple[int, ...]]:
    shapes: dict[str, tuple[int, ...]] = {}

    def add(module: str, a_shape: tuple[int, ...], b_shape: tuple[int, ...]) -> None:
        shapes[f"{module}.lora_A.weight"] = a_shape
        shapes[f"{module}.lora_B.weight"] = b_shape

    for block in range(50):
        prefix = f"blocks.{block}"
        add(f"{prefix}.adaln_proj.linear", (16, 2688), (96768, 16))
        add(f"{prefix}.attn.out_proj", (64, 7168), (5376, 64))
        add(f"{prefix}.attn.qkv_proj", (64, 5376), (21504, 64))
        add(f"{prefix}.mlp.fc1", (64, 5376), (28672, 64))
        add(f"{prefix}.mlp.fc2", (64, 14336), (5376, 64))
    for block in range(2):
        prefix = f"token_refiner.blocks.{block}"
        add(f"{prefix}.attn.out_proj", (64, 7168), (5376, 64))
        add(f"{prefix}.attn.qkv_proj", (64, 5376), (21504, 64))
        add(f"{prefix}.mlp.fc1", (64, 5376), (28672, 64))
        add(f"{prefix}.mlp.fc2", (64, 14336), (5376, 64))
    add("final_layer.adaln_proj.linear", (16, 2688), (10752, 16))
    return shapes


H3_TURBO_TENSOR_SHAPES = _tensor_shapes()
H3_TURBO_ADALN_MODULES = tuple(
    name.removesuffix(".lora_A.weight")
    for name in H3_TURBO_TENSOR_SHAPES
    if name.endswith("adaln_proj.linear.lora_A.weight")
)


def _validate_tensor_header(
    path: Path,
    expected: Mapping[str, tuple[int, ...]],
    *,
    expected_metadata: Mapping[str, str],
) -> None:
    header, size, data_start = _read_header(path)
    metadata = header.get("__metadata__")
    if metadata != dict(expected_metadata):
        raise H3TurboCompatibilityError(f"{path.name} metadata does not match the pinned artifact")
    tensors = {name: value for name, value in header.items() if name != "__metadata__"}
    if set(tensors) != set(expected):
        missing = sorted(set(expected) - set(tensors))[:3]
        extra = sorted(set(tensors) - set(expected))[:3]
        raise H3TurboCompatibilityError(
            f"{path.name} tensor key set is not exact (missing={missing}, extra={extra})"
        )
    intervals: list[tuple[int, int, str]] = []
    payload_size = size - data_start
    for name, shape in expected.items():
        spec = tensors[name]
        if not isinstance(spec, dict) or spec.get("dtype") != "BF16":
            raise H3TurboCompatibilityError(f"{path.name}:{name} must be BF16")
        if tuple(spec.get("shape") or ()) != tuple(shape):
            raise H3TurboCompatibilityError(
                f"{path.name}:{name} has shape {spec.get('shape')}; expected {list(shape)}"
            )
        offsets = spec.get("data_offsets")
        if not (
            isinstance(offsets, list)
            and len(offsets) == 2
            and all(isinstance(item, int) and not isinstance(item, bool) for item in offsets)
        ):
            raise H3TurboCompatibilityError(f"{path.name}:{name} has invalid data offsets")
        start, end = offsets
        elements = 1
        for dimension in shape:
            elements *= dimension
        if start < 0 or end - start != elements * 2 or end > payload_size:
            raise H3TurboCompatibilityError(f"{path.name}:{name} has an invalid byte extent")
        intervals.append((start, end, name))
    intervals.sort()
    cursor = 0
    for start, end, name in intervals:
        if start != cursor:
            raise H3TurboCompatibilityError(
                f"{path.name}:{name} does not form a contiguous safetensors payload"
            )
        cursor = end
    if cursor != payload_size:
        raise H3TurboCompatibilityError(f"{path.name} has unaccounted payload bytes")


def validate_turbo_lora(path: str | os.PathLike[str]) -> Path:
    source = Path(path)
    if not source.is_file() or source.stat().st_size != H3_TURBO_LORA_SIZE:
        raise H3TurboCompatibilityError(
            f"{source.name} must be exactly {H3_TURBO_LORA_SIZE} bytes"
        )
    if _sha256(source) != H3_TURBO_LORA_SHA256:
        raise H3TurboCompatibilityError(f"{source.name} SHA256 does not match the pinned v4 artifact")
    _validate_tensor_header(
        source,
        H3_TURBO_TENSOR_SHAPES,
        expected_metadata={
            "application": "W_eff = W + lora_B @ lora_A",
            "base_model": "MiniMax-H3",
        },
    )
    return source


def validate_turbo_grid(path: str | os.PathLike[str]) -> Path:
    source = Path(path)
    if not source.is_file() or source.stat().st_size != H3_TURBO_GRID_SIZE:
        raise H3TurboCompatibilityError(
            f"{source.name} must be exactly {H3_TURBO_GRID_SIZE} bytes"
        )
    if _sha256(source) != H3_TURBO_GRID_SHA256:
        raise H3TurboCompatibilityError(f"{source.name} SHA256 does not match the pinned node commit")
    _validate_tensor_header(
        source,
        {"silu_t_emb_grid": (1025, 2688)},
        expected_metadata={
            "grid": "linspace(0,1,1025)",
            "desc": "silu(time_embedder(t)) aligned with adaln_t_table rows",
        },
    )
    return source


def _release_name() -> str:
    return f"{H3_TURBO_PROFILE_ID}-{H3_TURBO_LORA_SHA256[:12]}-{H3_TURBO_GRID_SHA256[:12]}"


def _write_manifest_atomically(root: Path, release_name: str) -> None:
    manifest = {
        "version": _MANIFEST_VERSION,
        "profile_id": H3_TURBO_PROFILE_ID,
        "release": release_name,
        "lora_revision": H3_TURBO_LORA_REVISION,
        "node_commit": H3_TURBO_NODE_COMMIT,
    }
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".current-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / _MANIFEST_FILENAME)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_publish_validated_pair(
    first_source: Path,
    second_source: Path,
    release_dir: Path,
    *,
    first_name: str,
    second_name: str,
    validate_first: Callable[[Path], Any],
    validate_second: Callable[[Path], Any],
) -> None:
    """Copy, validate, and atomically rename one directory containing both files."""
    release_dir.parent.mkdir(parents=True, exist_ok=True)
    if release_dir.exists():
        validate_first(release_dir / first_name)
        validate_second(release_dir / second_name)
        return
    temporary = Path(tempfile.mkdtemp(prefix=".publish-", dir=release_dir.parent))
    try:
        first_target = temporary / first_name
        second_target = temporary / second_name
        shutil.copyfile(first_source, first_target)
        shutil.copyfile(second_source, second_target)
        validate_first(first_target)
        validate_second(second_target)
        os.replace(temporary, release_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def publish_turbo_assets(
    lora_source: str | os.PathLike[str],
    grid_source: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] | None = None,
) -> H3TurboAssets:
    """Validate and atomically publish the pinned LoRA/grid as one release."""
    lora_source = validate_turbo_lora(lora_source)
    grid_source = validate_turbo_grid(grid_source)
    managed_root = _managed_root(root)
    release_name = _release_name()
    release_dir = managed_root / "releases" / release_name
    _atomic_publish_validated_pair(
        lora_source,
        grid_source,
        release_dir,
        first_name=H3_TURBO_LORA_FILENAME,
        second_name=H3_TURBO_GRID_FILENAME,
        validate_first=validate_turbo_lora,
        validate_second=validate_turbo_grid,
    )
    _write_manifest_atomically(managed_root, release_name)
    return resolve_turbo_assets(managed_root)


def _download_pinned_url(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    request = Request(url, headers={"User-Agent": "Maestro-H3-Turbo/1"})
    try:
        with urlopen(request, timeout=30) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=8 * 1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def acquire_turbo_assets(
    *,
    root: str | os.PathLike[str] | None = None,
    download: Callable[[str, Path], str | os.PathLike[str] | None] | None = None,
) -> H3TurboAssets:
    """Acquire both pinned sources, then publish only after joint validation.

    ``download`` is injectable for launchers/tests. It receives an immutable
    revision URL and destination path and may either write there or return a
    different completed path. The default downloader never addresses a moving
    branch. Existing valid managed assets are returned without network access.
    """
    try:
        return resolve_turbo_assets(root)
    except H3TurboAssetsUnavailable:
        pass

    managed_root = _managed_root(root)
    staging = managed_root / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    downloader = download or _download_pinned_url

    def fetch(url: str, filename: str) -> Path:
        destination = staging / filename
        result = downloader(url, destination)
        return Path(result) if result is not None else destination

    lora_source = fetch(H3_TURBO_LORA_URL, H3_TURBO_LORA_FILENAME)
    grid_source = fetch(H3_TURBO_GRID_URL, H3_TURBO_GRID_FILENAME)
    return publish_turbo_assets(lora_source, grid_source, root=managed_root)


def resolve_turbo_assets(
    root: str | os.PathLike[str] | None = None,
    *,
    verify: bool = True,
) -> H3TurboAssets:
    managed_root = _managed_root(root)
    manifest_path = managed_root / _MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise H3TurboAssetsUnavailable("H3 Turbo managed assets are not installed") from error
    if not isinstance(manifest, dict) or manifest.get("version") != _MANIFEST_VERSION:
        raise H3TurboAssetsUnavailable("H3 Turbo managed manifest is invalid")
    if manifest.get("profile_id") != H3_TURBO_PROFILE_ID:
        raise H3TurboAssetsUnavailable("H3 Turbo managed manifest has the wrong profile")
    if manifest.get("lora_revision") != H3_TURBO_LORA_REVISION:
        raise H3TurboAssetsUnavailable("H3 Turbo LoRA revision is not pinned")
    if manifest.get("node_commit") != H3_TURBO_NODE_COMMIT:
        raise H3TurboAssetsUnavailable("H3 Turbo node companion commit is not pinned")
    release = manifest.get("release")
    if release != _release_name():
        raise H3TurboAssetsUnavailable("H3 Turbo managed release ID is invalid")
    release_dir = managed_root / "releases" / release
    assets = H3TurboAssets(
        H3_TURBO_PROFILE_ID,
        release_dir,
        release_dir / H3_TURBO_LORA_FILENAME,
        release_dir / H3_TURBO_GRID_FILENAME,
    )
    if verify:
        try:
            validate_turbo_lora(assets.lora_path)
            validate_turbo_grid(assets.grid_path)
        except H3TurboCompatibilityError as error:
            raise H3TurboAssetsUnavailable(str(error)) from error
    return assets


def turbo_assets_status(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    try:
        assets = resolve_turbo_assets(root)
    except H3TurboError as error:
        return {
            "available": False,
            "profile_id": H3_TURBO_PROFILE_ID,
            "reason": str(error),
            "lora_revision": H3_TURBO_LORA_REVISION,
            "node_commit": H3_TURBO_NODE_COMMIT,
        }
    return {
        "available": True,
        "profile_id": assets.profile_id,
        "reason": None,
        "lora_filename": assets.lora_path.name,
        "lora_revision": H3_TURBO_LORA_REVISION,
        "node_commit": H3_TURBO_NODE_COMMIT,
    }


def turbo_assets_available(root: str | os.PathLike[str] | None = None) -> bool:
    return bool(turbo_assets_status(root)["available"])


def turbo_requested(custom_settings: Mapping[str, Any] | None) -> bool:
    if not isinstance(custom_settings, Mapping):
        return False
    value = custom_settings.get("h3_turbo_profile")
    return value not in (None, "", False)


def _validated_authored_steps(authored_steps: Any) -> int:
    if isinstance(authored_steps, bool):
        raise H3TurboCompatibilityError(
            "H3 Turbo steps must be an integer from 4 through 8"
        )
    try:
        steps = int(authored_steps)
    except (TypeError, ValueError) as error:
        raise H3TurboCompatibilityError(
            "H3 Turbo steps must be an integer from 4 through 8"
        ) from error
    if (
        steps != authored_steps
        or not H3_TURBO_AUTHORED_STEPS_MIN
        <= steps
        <= H3_TURBO_AUTHORED_STEPS_MAX
    ):
        raise H3TurboCompatibilityError(
            "H3 Turbo supports exactly 4 through 8 model evaluations"
        )
    return steps


def resolve_h3_turbo_schedule(authored_steps: Any) -> H3TurboSchedule:
    """Resolve the exact versioned video/audio cadence for one Turbo request.

    Turbo-4 runs two audio/transformer ticks while the video state is frozen,
    then advances video using the second prediction in the pair. Other managed
    Turbo operating points retain the historical one-to-one paired cadence.
    """

    steps = _validated_authored_steps(authored_steps)
    if steps == 4:
        audio_evaluations = master_evaluations = 8
        video_timestep_indices = tuple(index // 2 for index in range(8))
        video_advance_ticks = (1, 3, 5, 7)
    else:
        audio_evaluations = master_evaluations = steps
        video_timestep_indices = tuple(range(steps))
        video_advance_ticks = tuple(range(steps))
    return H3TurboSchedule(
        profile_id=H3_TURBO_PROFILE_ID,
        algorithm_version=H3_TURBO_SCHEDULE_ALGORITHM_VERSION,
        authored_video_steps=steps,
        video_scheduler_steps=steps,
        audio_scheduler_steps=audio_evaluations,
        master_evaluations=master_evaluations,
        video_grid_points=steps + 1,
        audio_grid_points=audio_evaluations + 1,
        video_timestep_indices=video_timestep_indices,
        audio_timestep_indices=tuple(range(audio_evaluations)),
        video_advance_ticks=video_advance_ticks,
    )


def turbo_schedule_identity(authored_steps: Any) -> dict[str, str | int]:
    """Return the canonical public identity for a resolved Turbo schedule."""

    return resolve_h3_turbo_schedule(authored_steps).public_identity()


def validate_turbo_schedule_identity(
    identity: Mapping[str, Any],
) -> dict[str, str | int]:
    """Validate an untrusted persisted identity against the current resolver."""

    if not isinstance(identity, Mapping):
        raise H3TurboCompatibilityError("H3 Turbo schedule identity must be an object")
    authored_steps = identity.get("authored_video_steps")
    expected = turbo_schedule_identity(authored_steps)
    if dict(identity) != expected:
        raise H3TurboCompatibilityError(
            "H3 Turbo schedule identity does not match the current runtime"
        )
    return expected


def scheduler_grid_points(authored_steps: int) -> int:
    """Translate LarryVRH's model-evaluation count to Maestro grid points."""
    return resolve_h3_turbo_schedule(authored_steps).video_grid_points


def h3_turbo_adaln_delta(lora_a: Any, lora_b: Any, silu_t_emb: Any) -> Any:
    """Shared upstream equation, kept generic so it is model-free testable."""
    return (lora_b @ (lora_a @ silu_t_emb.T)).T


def h3_turbo_residual_delta(input_tensor: Any, lora_a: Any, lora_b: Any) -> Any:
    """Quantization-safe activation update ``B(A(x))`` for a linear output."""
    return (input_tensor @ lora_a.T) @ lora_b.T


def _model_url_names(model_def: Mapping[str, Any]) -> set[str]:
    urls = model_def.get("URLs")
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, (list, tuple)):
        return set()
    return {os.path.basename(str(item).split("?", 1)[0]) for item in urls}


def turbo_model_variant(model_def: Mapping[str, Any]) -> dict[str, Any]:
    names = _model_url_names(model_def)
    if len(names) != 1:
        raise H3TurboCompatibilityError(
            "H3 Turbo requires one structurally validated H3 transformer checkpoint"
        )
    filename = next(iter(names))
    variant = _MODEL_VARIANTS.get(filename)
    if variant is None:
        raise H3TurboCompatibilityError(
            f"H3 Turbo has no structural key/shape proof for checkpoint {filename!r}"
        )
    return {**variant, "checkpoint": filename}


def turbo_compatibility_matrix() -> dict[str, Any]:
    """Expose the combinations ready for synthetic/live integration gates."""
    try:
        from services.h3_acceleration import sage2_validation_status
        sage2_validation = sage2_validation_status()
    except Exception as error:
        sage2_validation = {"passed": False, "reason": str(error)}
    variants = {
        details["id"]: {
            **details,
            "checkpoint": checkpoint,
            "status": "ready_for_live_validation",
            "reason": (
                "The runtime uses the shared MiniMaxH3Transformer module map; compact AdaLN uses the pinned grid "
                "while full AdaLN remains 2688-wide, "
                + (
                    "and packed/INT8 backbone weights receive quantization-safe residual-output hooks."
                    if details["backbone"] == "residual_output"
                    else "and MMGP owns the additive backbone adapters."
                )
            ),
        }
        for checkpoint, details in _MODEL_VARIANTS.items()
    }
    variants["ref2va_fp8"]["status"] = "live_visual_gate_required"
    variants["ref2va_fp8"]["reason"] = (
        "Transformer keys/shapes are compatible, but semantic-reference adherence cannot be proven structurally; "
        "both 4- and 8-evaluation synthetic visual gates must pass before default enablement."
    )
    ref2va_validation = ref2va_live_validation_status()
    if ref2va_validation["passed"]:
        variants["ref2va_fp8"]["status"] = "ready"
        variants["ref2va_fp8"]["reason"] = "Release-bound 4/8 Ref2VA visual gates are recorded as passing."
    variants["pinkcherry_int8"]["status"] = "unsupported"
    variants["pinkcherry_int8"]["reason"] = (
        "PinkCherry is a manually selected native checkpoint and is incompatible "
        "with the managed H3 Turbo adapter."
    )
    return {
        "profile_id": H3_TURBO_PROFILE_ID,
        "variants": variants,
        "attention": {
            "sdpa": {
                "status": "ready_for_live_validation",
                "reason": "Dense attention does not alter LoRA keys or timestep/AdaLN semantics.",
            },
            "sol_attn": {
                "status": "ready_for_live_validation",
                "reason": (
                    "Allowed with dense_steps covering every effective transformer evaluation, which makes each Turbo step "
                    "take the exact SDPA fallback; sparse Sol still requires a quality gate."
                ),
            },
            "sage2": {
                "status": (
                    "validated_base_draft_fast"
                    if sage2_validation.get("passed")
                    else "ready_for_live_4_8_validation"
                ),
                "reason": (
                    "Release/runtime-bound Base Draft/Fast kernel, visual, and audio gates passed at their exact geometries. "
                    "Fast's cold SDPA baseline is provenance-only, not a speed claim. "
                    "W4A8 and Ref2VA remain unvalidated; PinkCherry is incompatible with Turbo."
                    if sage2_validation.get("passed")
                    else str(
                        sage2_validation.get("reason")
                        or "Base Turbo 4/8 timing and visual/audio validation is required."
                    )
                ),
            },
        },
        "cache": {
            "none": {"status": "ready_for_live_validation", "reason": "Every authored evaluation executes."},
            "tea": {
                "status": "unsupported",
                "reason": "MiniMaxH3Transformer has no TeaCache execution hook, so selecting it cannot safely skip work.",
            },
            "mag": {
                "status": "unsupported",
                "reason": "MiniMaxH3Transformer has no MagCache execution hook, so selecting it cannot safely skip work.",
            },
        },
        "stacking": {
            "managed_only": {
                "status": "ready_for_live_validation",
                "reason": "Managed Turbo strength is fixed at 1.0.",
            },
            "user_loras": {
                "status": "variant_dependent",
                "reason": (
                    "Supported on scaled-FP8 Base/Ref2VA where MMGP keeps adapters additive and independently "
                    "scaled. W4A8/PinkCherry user stacking remains unsupported because arbitrary user adapters "
                    "cannot safely use their packed/INT8 generic path."
                ),
                "variants": {
                    "base_fp8": "ready_for_live_validation",
                    "ref2va_fp8": "live_visual_gate_required",
                    "base_w4a8": "unsupported",
                    "pinkcherry_int8": "unsupported",
                },
            },
        },
        "authored_evaluations": {"minimum": 4, "maximum": 8},
    }


def ref2va_live_validation_status(
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    path = _managed_root(root) / _REF2VA_VALIDATION_FILENAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"passed": False, "reason": "Ref2VA Turbo 4/8 visual gate has not been recorded"}
    expected = {
        "profile_id": H3_TURBO_PROFILE_ID,
        "lora_sha256": H3_TURBO_LORA_SHA256,
        "grid_sha256": H3_TURBO_GRID_SHA256,
    }
    if not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items()):
        return {"passed": False, "reason": "Ref2VA Turbo validation record does not match this managed release"}
    cases = record.get("cases")
    required_criteria = {"reference_adherence", "motion", "coherence", "no_collapse"}
    if not isinstance(cases, dict):
        return {"passed": False, "reason": "Ref2VA Turbo validation record has no cases"}
    for steps in ("4", "8"):
        case = cases.get(steps)
        if not isinstance(case, dict) or case.get("passed") is not True:
            return {"passed": False, "reason": f"Ref2VA Turbo {steps}-evaluation visual gate has not passed"}
        criteria = case.get("criteria")
        if not isinstance(criteria, dict) or any(criteria.get(item) is not True for item in required_criteria):
            return {"passed": False, "reason": f"Ref2VA Turbo {steps}-evaluation criteria are incomplete"}
    return {"passed": True, "reason": None, "recorded_at": record.get("recorded_at")}


def record_ref2va_live_validation(
    cases: Mapping[str, Any],
    *,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Persist a release-bound 4/8 visual gate after the reviewer supplies it."""
    record = {
        "profile_id": H3_TURBO_PROFILE_ID,
        "lora_sha256": H3_TURBO_LORA_SHA256,
        "grid_sha256": H3_TURBO_GRID_SHA256,
        "recorded_at": time.time(),
        "cases": dict(cases),
    }
    managed_root = _managed_root(root)
    managed_root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ref2va-validation-", suffix=".json", dir=managed_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, managed_root / _REF2VA_VALIDATION_FILENAME)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return ref2va_live_validation_status(managed_root)


def validate_turbo_request(
    *,
    base_model_type: str,
    model_def: Mapping[str, Any],
    custom_settings: Mapping[str, Any] | None,
    authored_steps: Any,
    activated_loras: Any = None,
    loras_multipliers: Any = None,
    skip_steps_cache_type: Any = None,
    _h3_turbo_validation_authorized: bool = False,
) -> bool:
    """Validate the initial fail-closed Turbo compatibility matrix."""
    if not turbo_requested(custom_settings):
        return False
    profile = custom_settings.get("h3_turbo_profile") if custom_settings else None
    if profile != H3_TURBO_PROFILE_ID:
        raise H3TurboCompatibilityError(f"Unknown MiniMax H3 Turbo profile: {profile!r}")
    if base_model_type not in {"minimax_h3", "minimax_h3_ref2va"}:
        raise H3TurboCompatibilityError("H3 Turbo requires a MiniMax H3 FL2VA or Ref2VA transformer")
    variant = turbo_model_variant(model_def)
    reference_mode = bool(model_def.get("minimax_h3_reference_mode"))
    if reference_mode != bool(variant["reference_mode"]):
        raise H3TurboCompatibilityError(
            "H3 Turbo checkpoint and FL2VA/Ref2VA conditioning mode do not match"
        )
    if variant["id"] == "pinkcherry_int8":
        raise H3TurboCompatibilityError(
            "H3 Turbo is incompatible with PinkCherry; choose a native profile "
            "such as Quality or select a different H3 checkpoint"
        )
    if reference_mode:
        validation = ref2va_live_validation_status()
        if (
            not validation["passed"]
            and _h3_turbo_validation_authorized is not True
        ):
            raise H3TurboCompatibilityError(
                "H3 Turbo Ref2VA is structurally compatible but unavailable by default until its 4/8 "
                "reference-adherence, motion, coherence, and collapse visual gates pass"
            )
    duplicate_managed = any(
        os.path.basename(str(item)) == H3_TURBO_LORA_FILENAME
        for item in (activated_loras or [])
    )
    if duplicate_managed:
        raise H3TurboCompatibilityError(
            "The managed H3 Turbo LoRA is selected by its profile and cannot also be stacked manually"
        )
    if activated_loras and variant["backbone"] == "residual_output":
        raise H3TurboCompatibilityError(
            "H3 Turbo user-LoRA stacking is unsupported on W4A8/PinkCherry: their packed/INT8 generic adapter path is not dtype-safe"
        )
    if skip_steps_cache_type:
        raise H3TurboCompatibilityError(
            "H3 Turbo cache combination is unsupported: MiniMaxH3Transformer has no Tea/Mag cache execution hook"
        )
    evaluated_steps = resolve_h3_turbo_schedule(authored_steps).master_evaluations
    attention = str((custom_settings or {}).get("h3_attention_engine") or "sdpa")
    if attention not in {"sdpa", "sol_attn", "sage2"}:
        raise H3TurboCompatibilityError(f"Unknown H3 Turbo attention engine: {attention!r}")
    if attention == "sage2" and variant["id"] != "base_fp8":
        raise H3TurboCompatibilityError(
            "SageAttention2++ Turbo is release-validated only for Base H3; "
            "W4A8, PinkCherry, and Ref2VA must use SDPA or their separately validated engine"
        )
    if attention == "sol_attn":
        try:
            dense_steps = int((custom_settings or {}).get("h3_sol_dense_steps", 10))
        except (TypeError, ValueError) as error:
            raise H3TurboCompatibilityError("H3 Turbo Sol-Attn dense step count is invalid") from error
        if dense_steps < evaluated_steps:
            raise H3TurboCompatibilityError(
                "Sparse Sol-Attn is not yet validated with H3 Turbo; "
                "h3_sol_dense_steps must cover every effective transformer evaluation"
            )
    return True


def validate_runtime_state_dict(state_dict: Mapping[str, Any]) -> None:
    """Fail closed before MMGP consumes any loaded Turbo tensor."""
    if set(state_dict) != set(H3_TURBO_TENSOR_SHAPES):
        raise H3TurboCompatibilityError("Loaded H3 Turbo tensor key set is not exact")
    try:
        import torch
    except ImportError as error:
        raise H3TurboCompatibilityError("PyTorch is required to load H3 Turbo") from error
    for name, shape in H3_TURBO_TENSOR_SHAPES.items():
        tensor = state_dict[name]
        if not torch.is_tensor(tensor) or tensor.dtype != torch.bfloat16:
            raise H3TurboCompatibilityError(f"Loaded H3 Turbo tensor {name} must be BF16")
        if tuple(tensor.shape) != tuple(shape):
            raise H3TurboCompatibilityError(
                f"Loaded H3 Turbo tensor {name} has shape {tuple(tensor.shape)}; expected {shape}"
            )


def strip_and_capture_adaln(
    state_dict: MutableMapping[str, Any],
) -> dict[str, tuple[Any, Any]]:
    validate_runtime_state_dict(state_dict)
    captured: dict[str, tuple[Any, Any]] = {}
    for module in H3_TURBO_ADALN_MODULES:
        captured[module] = (
            state_dict.pop(f"{module}.lora_A.weight"),
            state_dict.pop(f"{module}.lora_B.weight"),
        )
    return captured


def prepare_h3_turbo_runtime(
    transformer: Any,
    *,
    custom_settings: Mapping[str, Any] | None,
    model_def: Mapping[str, Any],
    managed_lora_index: int = 0,
    root: str | os.PathLike[str] | None = None,
) -> H3TurboAssets | None:
    """Arm a transformer before MMGP loads the managed backbone adapters."""
    if not turbo_requested(custom_settings):
        clear_h3_turbo_runtime(transformer)
        return None
    assets = resolve_turbo_assets(root)
    variant = turbo_model_variant(model_def)
    transformer.prepare_h3_turbo(
        str(assets.grid_path),
        lora_path=str(assets.lora_path),
        backbone_mode=str(variant["backbone"]),
        managed_lora_index=managed_lora_index,
    )
    return assets


def activate_h3_turbo_runtime(transformer: Any) -> None:
    transformer.activate_h3_turbo()


def clear_h3_turbo_runtime(transformer: Any) -> None:
    if transformer is not None and hasattr(transformer, "clear_h3_turbo"):
        transformer.clear_h3_turbo()


__all__ = [
    "H3_TURBO_ADALN_MODULES",
    "H3_TURBO_AUTHORED_STEPS_MAX",
    "H3_TURBO_AUTHORED_STEPS_MIN",
    "H3_TURBO_BASE_CHECKPOINT",
    "H3_TURBO_GRID_FILENAME",
    "H3_TURBO_GRID_SHA256",
    "H3_TURBO_GRID_SIZE",
    "H3_TURBO_LORA_FILENAME",
    "H3_TURBO_LORA_REVISION",
    "H3_TURBO_LORA_SHA256",
    "H3_TURBO_LORA_SIZE",
    "H3_TURBO_NODE_COMMIT",
    "H3_TURBO_PINKCHERRY_CHECKPOINT",
    "H3_TURBO_PROFILE_ID",
    "H3_TURBO_REF2VA_CHECKPOINT",
    "H3_TURBO_SCHEDULE_ALGORITHM_VERSION",
    "H3_TURBO_STRENGTH",
    "H3_TURBO_TENSOR_SHAPES",
    "H3_TURBO_W4A8_CHECKPOINT",
    "H3TurboAssets",
    "H3TurboAssetsUnavailable",
    "H3TurboCompatibilityError",
    "H3TurboSchedule",
    "activate_h3_turbo_runtime",
    "acquire_turbo_assets",
    "clear_h3_turbo_runtime",
    "h3_turbo_adaln_delta",
    "h3_turbo_residual_delta",
    "prepare_h3_turbo_runtime",
    "publish_turbo_assets",
    "resolve_turbo_assets",
    "record_ref2va_live_validation",
    "ref2va_live_validation_status",
    "resolve_h3_turbo_schedule",
    "scheduler_grid_points",
    "strip_and_capture_adaln",
    "turbo_assets_available",
    "turbo_assets_status",
    "turbo_compatibility_matrix",
    "turbo_model_variant",
    "turbo_requested",
    "turbo_schedule_identity",
    "validate_runtime_state_dict",
    "validate_turbo_grid",
    "validate_turbo_lora",
    "validate_turbo_request",
    "validate_turbo_schedule_identity",
]
