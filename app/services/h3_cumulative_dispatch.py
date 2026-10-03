"""Private one-output transport for H3 AV state through WGP.

This object belongs to the server's synchronous caller. Never put it in task
settings, queue JSON or sidecars. It supplies no durable identity or authority.
"""

from __future__ import annotations

import os
from fractions import Fraction

from services.h3_cumulative_latents import H3CumulativeLatents, _validate_step
from services.h3_native_continuation import (
    H3_DEFAULT_MAX_WINDOW_FRAMES,
    H3NativeContinuationStep,
    is_legal_h3_video_frame_count,
)


def h3_cumulative_streaming_enabled():
    """Private server selection, never a serialized job or public option."""
    return os.environ.get("MAESTRO_H3_CUMULATIVE_STREAMING_EXPERIMENTAL") == "1"


def validate_h3_cumulative_settings(settings, *, frames):
    """Pure eligibility check, also used during recovery with the gate off."""
    if (
        settings.get("model_type") != "minimax_h3"
        or type(settings.get("video_length")) is not int
        or settings["video_length"] != frames
        or type(settings.get("repeat_generation")) is not int
        or settings.get("repeat_generation") != 1
        or type(settings.get("batch_size")) is not int
        or settings.get("batch_size") != 1
        or settings.get("image_mode") not in (0, None)
        or settings.get("repeat_start_offset", 0) != 0
        or settings.get("after_repeat_output") is not None
    ):
        raise ValueError(
            "H3 cumulative dispatch requires one native FL2VA video output."
        )
    # A cumulative output already contains every retained frame. Ordinary
    # source-prefix restoration, concat, offsets and postprocessing would
    # change that timeline or duplicate it.
    clip = settings.get("multi_clip_info")
    if clip is not None and (
        type(clip) is not dict
        or clip.get("source_prefix") is not None
        or clip.get("trim_tail", 0)
        or (int(clip.get("total", 1)) > 1 and clip.get("defer_concat") is not True)
    ):
        raise ValueError(
            "H3 cumulative output requires deferred concatenation without a source prefix."
        )
    empty_only = (
        "video_source",
        "retake_video",
        "video_guide",
        "video_guide2",
        "video_guide3",
        "image_start",
        "image_end",
        "image_refs",
        "audio_source",
        "audio_guide",
        "audio_guide2",
        "audio_guide3",
        "audio_guide4",
        "audio_guide5",
        "audio_guide6",
        "audio_conditioning_guide",
        "audio_prompt_type",
        "video_prompt_type",
        "temporal_upsampling",
        "spatial_upsampling",
        "_h3_native_boundary",
        "activated_loras",
        "skip_steps_cache_type",
        "_h3_source_audio_premux_recovery",
    )
    for name in empty_only:
        value = settings.get(name)
        if value is not None and not (
            isinstance(value, str)
            and not value
            or isinstance(value, (list, tuple))
            and not value
        ):
            raise ValueError(f"H3 cumulative dispatch cannot combine with {name}.")
    if any(
        settings.get(name) not in (None, 0, "")
        for name in (
            "force_fps",
            "audio_frame_offset",
            "film_grain_intensity",
            "MMAudio_setting",
            "sliding_window_discard_last_frames",
            "trim_tail_frames",
            "h3_native_boundary_conditioning",
            "progressive_pipeline",
        )
    ):
        raise ValueError(
            "H3 cumulative dispatch requires an unchanged native AV timeline."
        )
    prompt = settings.get("prompt")
    if (
        not isinstance(prompt, str)
        or "\n" in prompt
        and settings.get("multi_prompts_gen_type") != 2
    ):
        raise ValueError("H3 cumulative dispatch requires one prompt invocation.")


class H3CumulativeDispatch:
    def __init__(self, *, frames: int, previous=None, step=None):
        if (
            not is_legal_h3_video_frame_count(frames)
            or frames > H3_DEFAULT_MAX_WINDOW_FRAMES
        ):
            raise ValueError("H3 cumulative dispatch requires a native frame count.")
        if previous is not None or step is not None:
            if (
                type(previous) is not dict
                or not isinstance(previous.get("state"), H3CumulativeLatents)
                or previous.get("model_token") is None
                or not isinstance(step, H3NativeContinuationStep)
                or frames != step.target_frames
            ):
                raise ValueError(
                    "H3 cumulative dispatch requires an exact prior handoff and step."
                )
            _validate_step(previous["state"], step)
        self.frames = frames
        self.previous = previous
        self.step = step
        self._phase = "new"
        self._forwarded = False
        self._candidate = None
        self.handoff = None
        self._video_settings = self._video_sink = self._encoded_video = None

    def __getstate__(self):
        raise TypeError("H3 cumulative dispatch is transient and cannot be serialized.")

    def begin(self, settings):
        if self._phase != "new":
            raise ValueError("H3 cumulative dispatch is single-use.")
        if os.environ.get("MAESTRO_H3_CUMULATIVE_EXPERIMENTAL") != "1":
            raise ValueError(
                "H3 cumulative dispatch requires the private experimental gate."
            )
        validate_h3_cumulative_settings(settings, frames=self.frames)
        self._phase = "running"

    def bind_loaded_model(self, model):
        """Optional deferred restore boundary, after WGP has finalized its load."""

    def configure_video_sink(self, **settings):
        if (
            self._phase != "running" or self._forwarded
            or self._video_settings is not None or not h3_cumulative_streaming_enabled()
        ):
            raise ValueError("H3 streaming transport must be selected once before sampling")
        self._video_settings = settings

    def cleanup_video_sink(self):
        if self._video_sink is not None:
            self._video_sink.cleanup()

    @property
    def encoded_video(self):
        return self._encoded_video

    def sampling_frames(self, requested_frames):
        """Keep a legal append window below the ordinary first-clip minimum."""
        if (
            self._phase != "running"
            or type(requested_frames) is not int
            or requested_frames != self.frames
        ):
            raise ValueError("H3 cumulative sampling window changed before dispatch.")
        return self.frames

    def model_kwargs(
        self, *, base_model_type, frame_num, repeat_no, window_no, model=None
    ):
        if (
            self._phase != "running"
            or self._forwarded
            or base_model_type != "minimax_h3"
            or type(frame_num) is not int
            or frame_num != self.frames
            or repeat_no != 1
            or window_no != 1
        ):
            raise ValueError(
                "H3 cumulative dispatch geometry or invocation changed before sampling."
            )
        self.bind_loaded_model(model)
        self._forwarded = True
        result = {"_h3_cumulative_capture": True}
        if self.previous is not None:
            result.update(
                _h3_cumulative_previous=self.previous, _h3_cumulative_step=self.step
            )
        if self._video_settings is not None:
            from services.h3_stream_video import H3VideoSink

            generated = self.frames if self.step is None else (
                self.previous["state"].frame_count + self.step.extension_frames
            )
            published = generated if self.step is None else generated - self.step.publication_trim_frames
            self._video_sink = H3VideoSink(
                generated_frames=generated, published_frames=published, **self._video_settings,
            )
            result["_h3_cumulative_video_sink"] = self._video_sink
        return result

    def capture(self, samples):
        if (
            self._phase != "running"
            or not self._forwarded
            or self._candidate is not None
        ):
            raise ValueError("H3 cumulative dispatch has an unexpected sampler result.")
        handoff = (
            samples.get("_h3_cumulative_handoff") if isinstance(samples, dict) else None
        )
        if (
            type(handoff) is not dict
            or not isinstance(handoff.get("state"), H3CumulativeLatents)
            or handoff.get("model_token") is None
        ):
            raise ValueError("H3 cumulative sampler did not return retained AV state.")
        state = handoff["state"]
        state.__post_init__()
        expected_frames = (
            self.frames
            if self.step is None
            else self.previous["state"].frame_count + self.step.extension_frames
        )
        expected_published = (
            expected_frames
            if self.step is None
            else expected_frames - self.step.publication_trim_frames
        )
        pixels, audio = samples.get("x"), samples.get("audio")
        video_valid = getattr(pixels, "ndim", None) == 4 and pixels.shape[1] == expected_published
        artifact = samples.get("_h3_encoded_video")
        if self._video_sink is not None:
            from services.h3_stream_video import H3EncodedVideo

            if type(artifact) is not H3EncodedVideo or artifact is not self._video_sink.receipt:
                raise ValueError("H3 sampler did not return its bound encoded video receipt")
            artifact.verify()
            video_valid = (
                pixels is None and artifact.generated_frames == expected_frames
                and artifact.published_frames == expected_published
            )
        elif artifact is not None:
            raise ValueError("H3 sampler returned an unrequested encoded video receipt")
        expected_samples = round(Fraction(expected_published * 32000, 24))
        if (
            state.frame_count != expected_frames
            or state.published_frames != expected_published
            or not video_valid
            or getattr(audio, "shape", None) != (expected_samples, 2)
            or samples.get("audio_sampling_rate") != 32000
        ):
            raise ValueError("H3 cumulative sampler returned a different AV timeline.")
        self._candidate = handoff
        self._encoded_video = artifact

    @property
    def published_frames(self):
        if self._candidate is None:
            raise ValueError(
                "H3 cumulative output has no verified publication timeline."
            )
        return self._candidate["state"].published_frames

    def finish(self, success):
        # WGP's successful return is the output commit receipt: its mux,
        # finality and durable publication already completed. A later abort
        # belongs to the next operation and must not invalidate that handoff.
        if success is True and self._video_sink is not None and not self._video_sink.transferred:
            self.discard()
            raise ValueError("H3 streaming output completed without transferring its verified video")
        if success is True and self._phase == "running" and self._candidate is not None:
            self.handoff = self._candidate
            self._candidate = None
            self.previous = None
            self._phase = "completed"
            return
        self.discard()
        if success:
            raise ValueError(
                "H3 cumulative output completed without retained AV state."
            )

    def discard(self):
        self._candidate = self.handoff = None
        self._encoded_video = None
        self.previous = None
        self._phase = "failed"
        self.cleanup_video_sink()


def begin_h3_cumulative_dispatch(dispatch, settings):
    if not isinstance(dispatch, H3CumulativeDispatch):
        raise TypeError("H3 cumulative dispatch requires a private runtime object.")
    try:
        dispatch.begin(settings)
    except BaseException:
        dispatch.discard()
        raise
