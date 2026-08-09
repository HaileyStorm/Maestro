"""Clean-room, generation-local forecasting for native MiniMax H3.

This module is independently implemented from the public Spectrum H3
algorithm/release description.  It contains no ComfyUI integration or copied
upstream implementation.  The experimental controller keeps paired H3 video
and audio predictions together, captures a conservative causal trajectory,
then performs one transformer-free offline replay.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping, Sequence

import torch


SPECTRUM_PROFILE_ID = "spectrum_h3_v1"
SPECTRUM_ALGORITHM_VERSION = "maestro-clean-room-2"
SPECTRUM_UPSTREAM_BEHAVIOR_VERSION = "0.2.1-audio-fix"


class SpectrumCompatibilityError(ValueError):
    """Raised before inference when the request is outside the proven lane."""


class SpectrumStateError(RuntimeError):
    """Raised when generation-local state no longer matches its sealed plan."""


@dataclass(frozen=True)
class SpectrumConfig:
    profile_id: str = SPECTRUM_PROFILE_ID
    algorithm_version: str = SPECTRUM_ALGORITHM_VERSION
    polynomial_degree: int = 1
    ridge: float = 0.10
    warmup_actual_steps: int = 1
    video_blend_weight: float = 0.5
    audio_blend_weight: float = 0.0
    offline_smoothing_replay: bool = True


def spectrum_requested(custom_settings: Mapping[str, Any] | None) -> bool:
    return bool(
        isinstance(custom_settings, Mapping)
        and custom_settings.get("h3_spectrum_profile")
    )


def validate_spectrum_request(
    *,
    selected_model_type: str,
    model_def: Mapping[str, Any] | None,
    reference_mode: bool,
    sampling_steps: Any,
    attention_engine: str,
    custom_settings: Mapping[str, Any] | None,
    activated_loras: Any = None,
    loras_multipliers: Any = None,
    skip_steps_cache_type: Any = None,
    native_boundary: bool = False,
) -> SpectrumConfig | None:
    """Return the immutable experimental config or reject unsupported input."""
    if not spectrum_requested(custom_settings):
        return None
    profile = str((custom_settings or {}).get("h3_spectrum_profile") or "")
    if profile != SPECTRUM_PROFILE_ID:
        raise SpectrumCompatibilityError("Unknown MiniMax H3 Spectrum profile")
    definition = dict(model_def or {})
    if selected_model_type == "minimax_h3_ref2va" or reference_mode:
        raise SpectrumCompatibilityError("Spectrum Experimental does not support Ref2VA")
    if "w4a8" in selected_model_type.lower():
        raise SpectrumCompatibilityError("Spectrum Experimental does not support H3 W4A8")
    if "pinkcherry" in selected_model_type.lower():
        raise SpectrumCompatibilityError(
            "Spectrum Experimental does not support PinkCherry or ConvRot checkpoints"
        )
    if selected_model_type != "minimax_h3":
        raise SpectrumCompatibilityError(
            "Spectrum Experimental currently supports only MiniMax H3 Base FL2VA"
        )
    if definition.get("minimax_h3_reference_mode") is True:
        raise SpectrumCompatibilityError("Spectrum Experimental does not support Ref2VA")
    if definition.get("h3_w4a8") is True:
        raise SpectrumCompatibilityError("Spectrum Experimental does not support H3 W4A8")
    if definition.get("h3_convrot") is True or "pinkcherry" in str(
        definition.get("name") or ""
    ).lower():
        raise SpectrumCompatibilityError(
            "Spectrum Experimental does not support PinkCherry or ConvRot checkpoints"
        )
    try:
        steps = int(sampling_steps)
    except (TypeError, ValueError) as exc:
        raise SpectrumCompatibilityError(
            "Spectrum Experimental requires exactly 20 native evaluations"
        ) from exc
    if isinstance(sampling_steps, bool) or steps != 20:
        raise SpectrumCompatibilityError(
            "Spectrum Experimental requires exactly 20 native evaluations"
        )
    if attention_engine not in {"sdpa", "sol_attn"}:
        raise SpectrumCompatibilityError(
            "Spectrum Experimental supports only Dense SDPA or Sol-Attn"
        )
    custom = dict(custom_settings or {})
    if custom.get("h3_turbo_profile"):
        raise SpectrumCompatibilityError("Spectrum Experimental cannot be combined with Turbo")
    if custom.get("h3_native_boundary_conditioning") or native_boundary:
        raise SpectrumCompatibilityError(
            "Spectrum Experimental does not support native boundary conditioning"
        )
    if activated_loras:
        raise SpectrumCompatibilityError(
            "Spectrum Experimental does not support user or managed LoRAs"
        )
    if str(loras_multipliers or "").strip():
        raise SpectrumCompatibilityError(
            "Spectrum Experimental does not support LoRA multipliers"
        )
    if skip_steps_cache_type not in (None, "", 0, False):
        raise SpectrumCompatibilityError(
            "Spectrum Experimental cannot be combined with another step cache"
        )
    return SpectrumConfig()


def tensor_identity(tensor: torch.Tensor) -> tuple[Any, ...]:
    """Return a generation-local identity; it is never persisted or logged."""
    return (
        tuple(int(value) for value in tensor.shape),
        str(tensor.dtype),
        str(tensor.device),
        int(tensor.data_ptr()),
    )


def small_tensor_signature(tensor: torch.Tensor) -> tuple[Any, ...]:
    """Capture exact small schedule/layout values without retaining content."""
    detached = tensor.detach().to(device="cpu").reshape(-1)
    if detached.numel() > 4096:
        raise SpectrumStateError("Spectrum schedule signature exceeded its safe bound")
    return (
        tuple(int(value) for value in tensor.shape),
        str(tensor.dtype),
        tuple(float(value) for value in detached.to(torch.float64).tolist()),
    )


def run_length_tensor_signature(tensor: torch.Tensor) -> tuple[Any, ...]:
    """Exactly sign a large piecewise-constant H3 row schedule.

    High-resolution timestep-index vectors contain many thousands of rows but
    only a few modality/layout runs. Retaining every row would waste memory;
    run values plus counts are lossless and still fail closed on pathological
    unbounded layouts.
    """
    detached = tensor.detach().to(device="cpu").reshape(-1)
    values, counts = torch.unique_consecutive(detached, return_counts=True)
    if values.numel() > 4096:
        raise SpectrumStateError("Spectrum schedule run signature exceeded its safe bound")
    return (
        tuple(int(value) for value in tensor.shape),
        str(tensor.dtype),
        tuple(
            (float(value), int(count))
            for value, count in zip(
                values.to(torch.float64).tolist(), counts.tolist(),
            )
        ),
    )


def spectrum_anchor_indices(total_steps: int) -> tuple[int, ...]:
    """Use alternating native anchors and always retain the final evaluation."""
    if int(total_steps) != 20:
        raise SpectrumCompatibilityError(
            "Spectrum Experimental currently requires a 20-step native schedule"
        )
    return tuple(sorted({*range(0, total_steps, 2), total_steps - 1}))


def spectrum_scheduler_grid_points(authored_evaluations: int) -> int:
    """Translate Spectrum's authored evaluations to H3's terminal-inclusive grid."""
    if isinstance(authored_evaluations, bool) or int(authored_evaluations) != 20:
        raise SpectrumCompatibilityError(
            "Spectrum Experimental currently requires 20 authored evaluations"
        )
    return 21


class SpectrumGenerationController:
    """Own one segment's target-feature history and transformer-free replay."""

    def __init__(
        self,
        config: SpectrumConfig,
        *,
        total_steps: int,
        context_signature: tuple[Any, ...],
        step_signatures: Sequence[tuple[Any, ...]],
        audio_row_count: int,
        video_row_count: int,
    ) -> None:
        if not config.offline_smoothing_replay:
            raise SpectrumCompatibilityError("Spectrum offline replay must remain enabled")
        if config.audio_blend_weight != 0:
            raise SpectrumCompatibilityError("Spectrum audio blend must remain zero")
        if config.polynomial_degree != 1 or config.warmup_actual_steps != 1:
            raise SpectrumCompatibilityError(
                "Spectrum Experimental requires its pinned degree-one warmup profile"
            )
        if not 0 <= float(config.video_blend_weight) <= 1:
            raise SpectrumCompatibilityError("Spectrum video blend must remain between zero and one")
        if not float(config.ridge) > 0:
            raise SpectrumCompatibilityError("Spectrum ridge must remain positive")
        self.config = config
        self.total_steps = int(total_steps)
        self.context_signature = tuple(context_signature)
        self.step_signatures = tuple(tuple(item) for item in step_signatures)
        if len(self.step_signatures) != self.total_steps:
            raise SpectrumStateError("Spectrum step-signature count does not match schedule")
        self.anchor_indices = spectrum_anchor_indices(self.total_steps)
        self.audio_row_count = int(audio_row_count)
        self.video_row_count = int(video_row_count)
        if self.audio_row_count < 1 or self.video_row_count < 1:
            raise SpectrumStateError("Spectrum requires target audio and video rows")
        self._anchors: dict[int, torch.Tensor] = {}
        self._validation_scores: dict[str, dict[int, float]] = {
            "audio": {}, "video": {},
        }
        self._capture_cursor = 0
        self._replay_cursor = 0
        self._sealed = False
        self._reset_reason = ""
        self._fallback_actual_calls = 0

    @property
    def active(self) -> bool:
        return not bool(self._reset_reason)

    def assert_context(
        self,
        context_signature: tuple[Any, ...],
        step_index: int,
        step_signature: tuple[Any, ...],
    ) -> None:
        if not self.active:
            raise SpectrumStateError("Spectrum generation cache has been reset")
        if tuple(context_signature) != self.context_signature:
            raise SpectrumStateError(
                "Spectrum conditioning/layout identity changed within a segment"
            )
        if not 0 <= int(step_index) < self.total_steps:
            raise SpectrumStateError("Spectrum step index is outside its sealed schedule")
        if tuple(step_signature) != self.step_signatures[int(step_index)]:
            raise SpectrumStateError("Spectrum timestep/layout schedule changed")

    def _retain(self, feature: torch.Tensor) -> torch.Tensor:
        if not isinstance(feature, torch.Tensor) or feature.ndim != 3:
            raise SpectrumStateError("Spectrum requires one packed target hidden feature")
        if feature.shape[0] != 1 or feature.shape[1] != (
            self.audio_row_count + self.video_row_count
        ):
            raise SpectrumStateError("Spectrum target hidden-feature layout changed")
        if feature.numel() < 1 or not bool(torch.isfinite(feature).all()):
            raise SpectrumStateError("Spectrum received an invalid target hidden feature")
        return feature.detach().clone()

    def requires_actual(self, step_index: int) -> bool:
        return int(step_index) in self.anchor_indices

    @staticmethod
    def _linear_feature(
        left_index: int,
        left: torch.Tensor,
        right_index: int,
        right: torch.Tensor,
        target_index: int,
    ) -> torch.Tensor:
        if right_index == left_index:
            return left
        ratio = (float(target_index) - left_index) / (right_index - left_index)
        return torch.lerp(left, right, ratio)

    def capture_feature(
        self,
        step_index: int,
        *,
        context_signature: tuple[Any, ...],
        step_signature: tuple[Any, ...],
        actual_call: Callable[[], torch.Tensor] | None,
    ) -> torch.Tensor:
        """Capture native anchors; skipped slots use a past-only local predictor.

        The causal capture never injects extrapolated video into a later joint
        video/audio transformer call. Offline replay may use future anchors
        only after every native anchor is sealed.
        """
        index = int(step_index)
        if self._sealed or index != self._capture_cursor:
            raise SpectrumStateError("Spectrum capture steps must be monotonic and unique")
        self.assert_context(context_signature, index, step_signature)
        if index in self.anchor_indices:
            if actual_call is None:
                raise SpectrumStateError("Spectrum actual anchor omitted its H3 block call")
            feature = self._retain(actual_call())
            self._anchors[index] = feature
        else:
            prior = [anchor for anchor in self._anchors if anchor < index]
            if not prior:
                # This should be unreachable with anchor zero, but fail toward
                # a real transformer call rather than emitting an unsafe guess.
                if actual_call is None:
                    raise SpectrumStateError("Spectrum fallback anchor omitted its H3 block call")
                feature = self._retain(actual_call())
                self._anchors[index] = feature
                self._fallback_actual_calls += 1
            elif len(prior) == 1:
                feature = self._anchors[prior[-1]]
            else:
                left_index, right_index = prior[-2], prior[-1]
                feature = self._linear_feature(
                    left_index,
                    self._anchors[left_index],
                    right_index,
                    self._anchors[right_index],
                    index,
                )
        self._capture_cursor += 1
        return feature

    def seal_capture(self) -> None:
        if self._capture_cursor != self.total_steps:
            raise SpectrumStateError("Spectrum capture ended before the native schedule")
        if any(index not in self._anchors for index in self.anchor_indices):
            raise SpectrumStateError("Spectrum capture is missing a required native anchor")
        self._build_validation_scores()
        self._sealed = True

    def _spectral_weights(
        self,
        anchor_indices: Sequence[int],
        target_index: int,
    ) -> torch.Tensor:
        indices = tuple(int(index) for index in anchor_indices)
        if len(indices) < 2:
            raise SpectrumStateError("Spectrum spectral fit requires two actual anchors")
        device = self._anchors[indices[0]].device
        coordinates = torch.tensor(
            [2.0 * index / (self.total_steps - 1) - 1.0 for index in indices],
            device=device,
            dtype=torch.float32,
        )
        target = torch.tensor(
            [2.0 * target_index / (self.total_steps - 1) - 1.0],
            device=device,
            dtype=torch.float32,
        )
        design = torch.stack((torch.ones_like(coordinates), coordinates), dim=1)
        target_design = torch.stack((torch.ones_like(target), target), dim=1)
        gram = design.transpose(0, 1) @ design
        gram = gram + torch.eye(2, device=gram.device, dtype=gram.dtype) * float(
            self.config.ridge
        )
        try:
            coefficients = torch.linalg.solve(gram, design.transpose(0, 1))
            weights = (target_design @ coefficients)[0]
        except RuntimeError as error:
            raise SpectrumStateError("Spectrum regression could not be solved safely") from error
        # Minimal affine correction: distribute only the ridge-induced sum
        # residual so any constant hidden trajectory remains exactly constant.
        weights = weights + (1.0 - weights.sum()) / len(indices)
        if not bool(torch.isfinite(weights).all()):
            raise SpectrumStateError("Spectrum regression weights are non-finite")
        return weights

    def _spectral_feature(
        self,
        anchor_indices: Sequence[int],
        target_index: int,
    ) -> torch.Tensor:
        indices = tuple(int(index) for index in anchor_indices)
        samples = [self._anchors[index] for index in indices]
        reference = samples[0]
        if any(
            sample.shape != reference.shape
            or sample.dtype != reference.dtype
            or sample.device != reference.device
            for sample in samples[1:]
        ):
            raise SpectrumStateError("Spectrum anchor tensor layout changed")
        weights = self._spectral_weights(indices, target_index)
        # Evaluate around one real anchor. Besides reducing cancellation, this
        # makes a constant trajectory bit-exact even if finite-precision
        # arithmetic leaves the corrected weight sum a few ulps from one.
        forecast = reference.clone()
        for weight, sample in zip(weights, samples):
            forecast.add_(sample - reference, alpha=float(weight))
        if not bool(torch.isfinite(forecast).all()):
            raise SpectrumStateError("Spectrum forecast is non-finite")
        return forecast

    @staticmethod
    def _sampled_rms(value: torch.Tensor) -> float:
        flat = value.detach().float().reshape(-1)
        if flat.numel() > 16384:
            positions = torch.linspace(
                0, flat.numel() - 1, 16384, device=flat.device,
            ).long()
            flat = flat.index_select(0, positions)
        return float(torch.sqrt(torch.mean(flat.square())).item())

    @staticmethod
    def _validation_positions(value: torch.Tensor) -> torch.Tensor | None:
        count = value.numel()
        if count <= 16384:
            return None
        return torch.linspace(
            0, count - 1, 16384, device=value.device,
        ).long()

    def _sample_modality(
        self,
        feature: torch.Tensor,
        modality: str,
        positions: torch.Tensor | None,
    ) -> torch.Tensor:
        flat = self._modality_slice(feature, modality).detach().float().reshape(-1)
        return flat if positions is None else flat.index_select(0, positions)

    def _modality_slice(self, feature: torch.Tensor, modality: str) -> torch.Tensor:
        if modality == "audio":
            return feature[:, :self.audio_row_count]
        return feature[:, self.audio_row_count:]

    def _build_validation_scores(self) -> None:
        anchors = sorted(self._anchors)
        for position in range(1, len(anchors) - 1):
            held_index = anchors[position]
            left_index, right_index = anchors[position - 1], anchors[position + 1]
            fit_indices = [index for index in anchors if index != held_index]
            fit_weights = self._spectral_weights(fit_indices, held_index)
            ratio = (held_index - left_index) / (right_index - left_index)
            for modality in ("audio", "video"):
                actual_part = self._modality_slice(
                    self._anchors[held_index], modality,
                )
                positions = self._validation_positions(actual_part)
                actual_sample = self._sample_modality(
                    self._anchors[held_index], modality, positions,
                )
                left_sample = self._sample_modality(
                    self._anchors[left_index], modality, positions,
                )
                right_sample = self._sample_modality(
                    self._anchors[right_index], modality, positions,
                )
                local_sample = torch.lerp(left_sample, right_sample, ratio)
                reference_sample = self._sample_modality(
                    self._anchors[fit_indices[0]], modality, positions,
                )
                spectral_sample = reference_sample.clone()
                for weight, anchor_index in zip(fit_weights, fit_indices):
                    anchor_sample = self._sample_modality(
                        self._anchors[anchor_index], modality, positions,
                    )
                    spectral_sample.add_(
                        anchor_sample - reference_sample, alpha=float(weight),
                    )
                spectral_error = self._sampled_rms(
                    spectral_sample - actual_sample
                )
                local_error = self._sampled_rms(local_sample - actual_sample)
                scale = max(1.0, self._sampled_rms(actual_sample))
                epsilon = torch.finfo(torch.float32).eps * scale
                score = spectral_error / max(local_error, epsilon)
                self._validation_scores[modality][held_index] = (
                    score if math.isfinite(score) else float("inf")
                )

    def _effective_blend(
        self,
        modality: str,
        left_index: int,
        right_index: int,
    ) -> float:
        configured = (
            self.config.audio_blend_weight
            if modality == "audio" else self.config.video_blend_weight
        )
        scores = self._validation_scores[modality]
        bracket_scores = [
            float(scores[index])
            for index in (left_index, right_index)
            if index in scores
        ]
        # A spectral branch is never trusted without finite leave-one-anchor-
        # out evidence. One validated side is enough at the two edge brackets;
        # an invalid side makes the conservative bracket invalid as well.
        if not bracket_scores or not all(math.isfinite(score) for score in bracket_scores):
            return 0.0
        validation_score = max(bracket_scores)
        return float(configured) / max(1.0, validation_score)

    def replay_feature(
        self,
        step_index: int,
        *,
        context_signature: tuple[Any, ...],
        step_signature: tuple[Any, ...],
    ) -> torch.Tensor:
        if not self._sealed:
            raise SpectrumStateError("Spectrum replay requires a sealed capture")
        index = int(step_index)
        if index != self._replay_cursor:
            raise SpectrumStateError("Spectrum replay steps must be monotonic and unique")
        self.assert_context(context_signature, index, step_signature)
        if index in self._anchors:
            feature = self._anchors[index]
        else:
            left_index = max(anchor for anchor in self._anchors if anchor < index)
            right_index = min(anchor for anchor in self._anchors if anchor > index)
            local = self._linear_feature(
                left_index, self._anchors[left_index],
                right_index, self._anchors[right_index], index,
            )
            spectral = self._spectral_feature(sorted(self._anchors), index)
            audio = torch.lerp(
                self._modality_slice(local, "audio"),
                self._modality_slice(spectral, "audio"),
                self._effective_blend("audio", left_index, right_index),
            )
            video = torch.lerp(
                self._modality_slice(local, "video"),
                self._modality_slice(spectral, "video"),
                self._effective_blend("video", left_index, right_index),
            )
            feature = torch.cat((audio, video), dim=1)
        self._replay_cursor += 1
        return feature

    def stats(self) -> dict[str, Any]:
        actual = len(self._anchors)
        return {
            "accelerator": "spectrum",
            "profile_id": self.config.profile_id,
            "algorithm_version": self.config.algorithm_version,
            "upstream_behavior_version": SPECTRUM_UPSTREAM_BEHAVIOR_VERSION,
            "actual_transformer_calls": actual,
            "forecast_transformer_calls": self.total_steps - actual,
            "replay_transformer_calls": 0,
            "replay_steps": self._replay_cursor,
            "fallback_actual_calls": self._fallback_actual_calls,
            "audio_blend_weight": self.config.audio_blend_weight,
            "video_blend_weight": self.config.video_blend_weight,
            "max_video_validation_score": max(
                self._validation_scores["video"].values(), default=None,
            ),
            "max_audio_validation_score": max(
                self._validation_scores["audio"].values(), default=None,
            ),
            "offline_smoothing_replay": True,
            "reset_reason": self._reset_reason or None,
        }

    def reset(self, reason: str) -> None:
        self._anchors.clear()
        self._validation_scores = {"audio": {}, "video": {}}
        self._reset_reason = str(reason or "reset")


__all__ = [
    "SPECTRUM_ALGORITHM_VERSION",
    "SPECTRUM_PROFILE_ID",
    "SPECTRUM_UPSTREAM_BEHAVIOR_VERSION",
    "SpectrumCompatibilityError",
    "SpectrumConfig",
    "SpectrumGenerationController",
    "SpectrumStateError",
    "small_tensor_signature",
    "run_length_tensor_signature",
    "spectrum_anchor_indices",
    "spectrum_scheduler_grid_points",
    "spectrum_requested",
    "tensor_identity",
    "validate_spectrum_request",
]
