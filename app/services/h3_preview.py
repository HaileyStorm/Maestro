"""Opt-in, in-memory preview decoding for native MiniMax H3 latents.

The adapter is observational: it clones detached packed rows before invoking
the already-loaded native model and never persists prompts, latents, frames,
or errors.  A preview failure is always a typed, droppable side result and
cannot request or influence production retry/recovery behavior.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

import torch

H3_PREVIEW_SCHEMA_VERSION = 1
H3_PREVIEW_MODE = "native_vae"
H3_PREVIEW_DECODER_IDENTITY = "minimax_h3_native_video_vae_v1"
H3_PREVIEW_STATUSES = frozenset({"ready", "dropped", "unsupported"})
H3_PREVIEW_REASONS = frozenset({
    "ready",
    "not_requested",
    "schema_unsupported",
    "mode_unsupported",
    "decoder_unavailable",
    "cancelled",
    "invalid_geometry",
    "out_of_memory",
    "decode_error",
})


class H3PreviewGeometryError(ValueError):
    """The native decoder rejected preview tensor or pixel geometry."""


class H3PreviewDecoder(Protocol):
    """The narrow native-model seam consumed by the preview adapter."""

    def decode_h3_preview_rows(
        self,
        *,
        packed_rows: torch.Tensor,
        latent_frames: int,
        latent_height: int,
        latent_width: int,
        pixel_frames: int,
        pixel_height: int,
        pixel_width: int,
        channels: int,
        patch_size: tuple[int, int, int],
    ) -> torch.Tensor: ...


@dataclass(frozen=True, slots=True)
class H3PreviewRequest:
    """One explicit request to observe packed H3 video rows in memory."""

    enabled: bool
    packed_rows: torch.Tensor = field(repr=False)
    latent_frames: int
    latent_height: int
    latent_width: int
    pixel_frames: int
    pixel_height: int
    pixel_width: int
    schema_version: int = H3_PREVIEW_SCHEMA_VERSION
    mode: str = H3_PREVIEW_MODE
    channels: int = 24
    patch_size: tuple[int, int, int] = (1, 2, 2)
    cancelled: bool = False
    cancel_requested: Callable[[], bool] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class H3PreviewGeometry:
    """Content-free geometry attached to a successful preview result."""

    packed_rows: int
    packed_channels: int
    latent_frames: int
    latent_height: int
    latent_width: int
    pixel_frames: int
    pixel_height: int
    pixel_width: int
    channels: int
    patch_size: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class H3PreviewResult:
    """A ready frame tensor or a bounded, safe preview-only disposition."""

    status: Literal["ready", "dropped", "unsupported"]
    reason: str
    decoder_identity: str
    geometry: H3PreviewGeometry | None = None
    frames: torch.Tensor | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.status not in H3_PREVIEW_STATUSES:
            raise ValueError("Invalid H3 preview status")
        if self.reason not in H3_PREVIEW_REASONS:
            raise ValueError("Invalid H3 preview reason")
        if self.status == "ready" and (self.frames is None or self.geometry is None):
            raise ValueError("Ready H3 previews require frames and geometry")
        if self.status != "ready" and self.frames is not None:
            raise ValueError("Dropped H3 previews cannot retain frames")


def _result(
    status: Literal["ready", "dropped", "unsupported"],
    reason: str,
    *,
    geometry: H3PreviewGeometry | None = None,
    frames: torch.Tensor | None = None,
) -> H3PreviewResult:
    return H3PreviewResult(
        status=status,
        reason=reason,
        decoder_identity=H3_PREVIEW_DECODER_IDENTITY,
        geometry=geometry,
        frames=frames,
    )


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _geometry(request: H3PreviewRequest) -> H3PreviewGeometry | None:
    dimensions = (
        request.latent_frames,
        request.latent_height,
        request.latent_width,
        request.pixel_frames,
        request.pixel_height,
        request.pixel_width,
        request.channels,
    )
    if not all(_positive_int(value) for value in dimensions):
        return None
    patch = request.patch_size
    if (
        not isinstance(patch, tuple)
        or len(patch) != 3
        or not all(_positive_int(value) for value in patch)
    ):
        return None
    patch_t, patch_h, patch_w = patch
    if (
        request.latent_frames % patch_t
        or request.latent_height % patch_h
        or request.latent_width % patch_w
    ):
        return None
    rows = request.packed_rows
    if not isinstance(rows, torch.Tensor) or rows.ndim != 2:
        return None
    expected_rows = (
        (request.latent_frames // patch_t)
        * (request.latent_height // patch_h)
        * (request.latent_width // patch_w)
    )
    expected_channels = request.channels * math.prod(patch)
    if tuple(rows.shape) != (expected_rows, expected_channels):
        return None
    return H3PreviewGeometry(
        packed_rows=expected_rows,
        packed_channels=expected_channels,
        latent_frames=request.latent_frames,
        latent_height=request.latent_height,
        latent_width=request.latent_width,
        pixel_frames=request.pixel_frames,
        pixel_height=request.pixel_height,
        pixel_width=request.pixel_width,
        channels=request.channels,
        patch_size=patch,
    )


def _cancelled(request: H3PreviewRequest) -> bool:
    if request.cancelled:
        return True
    callback = request.cancel_requested
    return bool(callback()) if callable(callback) else False


def decode_h3_preview(
    request: H3PreviewRequest,
    decoder: H3PreviewDecoder | None,
) -> H3PreviewResult:
    """Decode one native preview without changing production tensors or state."""

    if not request.enabled:
        return _result("unsupported", "not_requested")
    if request.schema_version != H3_PREVIEW_SCHEMA_VERSION:
        return _result("unsupported", "schema_unsupported")
    if request.mode != H3_PREVIEW_MODE:
        return _result("unsupported", "mode_unsupported")
    if decoder is None or not callable(getattr(decoder, "decode_h3_preview_rows", None)):
        return _result("dropped", "decoder_unavailable")
    try:
        if _cancelled(request):
            return _result("dropped", "cancelled")
    except Exception:  # noqa: BLE001 - preview callback failures fail closed to a drop
        return _result("dropped", "decode_error")
    geometry = _geometry(request)
    if geometry is None:
        return _result("dropped", "invalid_geometry")

    try:
        # Clone is intentional: even an incorrectly stateful decoder cannot
        # alter the sampler's final packed rows through this adapter. Keep the
        # allocation inside the droppable preview failure boundary.
        rows = request.packed_rows.detach().clone()
        frames = decoder.decode_h3_preview_rows(
            packed_rows=rows,
            latent_frames=geometry.latent_frames,
            latent_height=geometry.latent_height,
            latent_width=geometry.latent_width,
            pixel_frames=geometry.pixel_frames,
            pixel_height=geometry.pixel_height,
            pixel_width=geometry.pixel_width,
            channels=geometry.channels,
            patch_size=geometry.patch_size,
        )
        if _cancelled(request):
            return _result("dropped", "cancelled")
        if not isinstance(frames, torch.Tensor) or tuple(frames.shape) != (
            1,
            3,
            geometry.pixel_frames,
            geometry.pixel_height,
            geometry.pixel_width,
        ):
            return _result("dropped", "invalid_geometry")
        return _result(
            "ready",
            "ready",
            geometry=geometry,
            frames=frames.detach(),
        )
    except H3PreviewGeometryError:
        return _result("dropped", "invalid_geometry")
    except (MemoryError, torch.OutOfMemoryError):
        return _result("dropped", "out_of_memory")
    except InterruptedError:
        return _result("dropped", "cancelled")
    except Exception:  # noqa: BLE001 - decode failures are deliberately preview-only
        return _result("dropped", "decode_error")
