"""CPU tensor tests; no checkpoint, model, sampler, CUDA, or media execution."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import torch
from services.h3_cumulative_latents import (
    H3CumulativeLatents,
    append_h3_cumulative_window,
    extract_h3_cumulative_context,
)
from services.h3_native_continuation import (
    H3NativeContinuationError,
    audio_tick_at_frame,
    latent_frames_for_video_frames,
    plan_h3_native_continuation_step,
    plan_h3_native_continuation_tail,
)


def state(frames=124):
    video = torch.arange(
        24 * latent_frames_for_video_frames(frames) * 4, dtype=torch.float32
    ).reshape(1, 24, -1, 2, 2)
    audio = torch.arange(64 * audio_tick_at_frame(frames), dtype=torch.float32).reshape(
        2, 32, -1
    )
    return H3CumulativeLatents(video, audio, frames)


def window(step):
    video = torch.full((1, 24, step.target_latent_frames, 2, 2), 17.0)
    audio = torch.full((2, 32, step.target_audio_ticks), 29.0)
    video[:, :, : step.context_latent_frames] = -7
    audio[..., : step.context_audio_ticks] = -11
    return video, audio


class TestH3CumulativeLatents(unittest.TestCase):
    def test_suffix_only_append_preserves_prefix_and_inputs(self):
        previous = state()
        step = plan_h3_native_continuation_step(
            22, 119, absolute_context_start_frame=102
        )
        video, audio = window(step)
        old = (
            previous.video.clone(),
            previous.audio.clone(),
            video.clone(),
            audio.clone(),
        )
        result = append_h3_cumulative_window(previous, video, audio, step)
        self.assertEqual((result.frame_count, result.published_frames), (243, 243))
        self.assertTrue(torch.equal(result.video[:, :, :37], previous.video))
        self.assertTrue(torch.equal(result.audio[..., :207], previous.audio))
        self.assertTrue(torch.all(result.video[:, :, 37:] == 17))
        self.assertTrue(torch.all(result.audio[..., 207:] == 29))
        for before, after in zip(old, (previous.video, previous.audio, video, audio)):
            self.assertTrue(torch.equal(before, after))
        result.video.zero_()
        result.audio.zero_()
        self.assertTrue(torch.equal(previous.video, old[0]))
        self.assertTrue(torch.equal(previous.audio, old[1]))

    def test_context_is_detached_copy_and_uses_absolute_audio_span(self):
        previous = state(243)
        step = plan_h3_native_continuation_step(
            22, 119, absolute_context_start_frame=221
        )
        context = extract_h3_cumulative_context(previous, step)
        self.assertEqual(context.video.shape[2], 7)
        self.assertEqual(context.audio.shape[-1], step.context_audio_ticks)
        self.assertTrue(
            torch.equal(context.audio, previous.audio[..., -step.context_audio_ticks :])
        )
        saved = previous.video.clone(), previous.audio.clone()
        context.video.zero_()
        context.audio.zero_()
        self.assertTrue(torch.equal(saved[0], previous.video))
        self.assertTrue(torch.equal(saved[1], previous.audio))

    def test_twenty_appends_reconcile_absolute_clock_without_drift(self):
        previous = state()
        for _ in range(20):
            step = plan_h3_native_continuation_step(
                22, 119, absolute_context_start_frame=previous.frame_count - 22
            )
            previous = append_h3_cumulative_window(previous, *window(step), step)
            self.assertEqual(
                previous.audio.shape[-1], audio_tick_at_frame(previous.frame_count)
            )
            self.assertEqual(
                previous.video.shape[2],
                latent_frames_for_video_frames(previous.frame_count),
            )
        self.assertEqual(previous.frame_count, 2504)

    def test_final_publication_trim_retains_full_state_and_ends_chain(self):
        previous = state(22)
        step = plan_h3_native_continuation_tail(1).steps[0]
        result = append_h3_cumulative_window(previous, *window(step), step)
        self.assertEqual((result.frame_count, result.published_frames), (39, 23))
        self.assertEqual(result.video.shape[2], latent_frames_for_video_frames(39))
        self.assertEqual(result.audio.shape[-1], audio_tick_at_frame(39))
        successor = plan_h3_native_continuation_step(
            22, 17, absolute_context_start_frame=17
        )
        with self.assertRaisesRegex(H3NativeContinuationError, "trimmed chain"):
            extract_h3_cumulative_context(result, successor)

    def test_stale_step_rejected_before_allocation(self):
        previous = state()
        step = plan_h3_native_continuation_step(22, 119)
        with patch("services.h3_cumulative_latents.torch.cat") as concatenate:
            with self.assertRaisesRegex(
                H3NativeContinuationError, "previous frame boundary"
            ):
                append_h3_cumulative_window(previous, *window(step), step)
            concatenate.assert_not_called()

    def test_invalid_generated_shapes_and_dtype_fail_before_append(self):
        previous = state(22)
        step = plan_h3_native_continuation_step(22, 119)
        video, audio = window(step)
        for bad_video, bad_audio in (
            (video[:, :, :-1], audio),
            (video, audio[..., :-1]),
            (video.double(), audio),
            (video, audio.double()),
            (video[..., :1], audio),
        ):
            with (
                self.subTest(
                    video=tuple(bad_video.shape), audio=tuple(bad_audio.shape)
                ),
                self.assertRaises(H3NativeContinuationError),
            ):
                append_h3_cumulative_window(previous, bad_video, bad_audio, step)

    def test_invalid_state_geometry_and_types(self):
        previous = state(22)
        for changes in (
            {"frame_count": True},
            {"frame_count": 21},
            {"published_frames": True},
            {"published_frames": 23},
            {"audio": previous.audio[..., :-1]},
            {"audio": previous.audio.to(torch.int32)},
            {"video": previous.video.repeat(2, 1, 1, 1, 1)},
            {"video": torch.empty(previous.video.shape, device="meta")},
        ):
            with (
                self.subTest(changes=tuple(changes)),
                self.assertRaises(H3NativeContinuationError),
            ):
                replace(previous, **changes)

    def test_mutated_borrowed_state_is_revalidated(self):
        previous = state(22)
        previous.audio.resize_(2, 32, 1)
        step = plan_h3_native_continuation_step(22, 119)
        with self.assertRaisesRegex(H3NativeContinuationError, "audio"):
            extract_h3_cumulative_context(previous, step)

    def test_byte_budget_checked_before_concatenation(self):
        previous = state(22)
        step = plan_h3_native_continuation_step(22, 119)
        video, audio = window(step)
        expected = (
            24 * step.target_latent_frames * 4 * 4 + 64 * step.target_audio_ticks * 4
        )
        with patch("services.h3_cumulative_latents.torch.cat") as concatenate:
            for budget in (True, 0, expected - 1):
                with self.assertRaises(H3NativeContinuationError):
                    append_h3_cumulative_window(
                        previous, video, audio, step, max_output_bytes=budget
                    )
            concatenate.assert_not_called()
        result = append_h3_cumulative_window(
            previous, video, audio, step, max_output_bytes=expected
        )
        self.assertEqual(result.frame_count, 141)

    def test_context_byte_budget_checked_before_cloning(self):
        previous = state(22)
        step = plan_h3_native_continuation_step(22, 119)
        expected = previous.video.numel() * 4 + previous.audio.numel() * 4
        with patch.object(torch.Tensor, "clone") as clone:
            for budget in (True, 0, expected - 1):
                with self.assertRaises(H3NativeContinuationError):
                    extract_h3_cumulative_context(
                        previous, step, max_output_bytes=budget
                    )
            clone.assert_not_called()
        context = extract_h3_cumulative_context(
            previous, step, max_output_bytes=expected
        )
        self.assertEqual(context.audio.shape[-1], step.context_audio_ticks)


if __name__ == "__main__":
    unittest.main()
