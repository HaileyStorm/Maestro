"""Real cumulative model/dispatch and WGP seams with CPU fake learned models."""

# Execute only reviewed repository AST, never user-supplied code.
# ruff: noqa: S102
from __future__ import annotations

import ast
import copy
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services.h3_cumulative_dispatch import (
    H3CumulativeDispatch,
    validate_h3_cumulative_settings,
)
from services.h3_cumulative_execution import prepare_h3_cumulative_request
from services.h3_native_continuation import plan_h3_native_continuation_tail
from services.h3_stream_video import H3VideoSink
from test_h3_cumulative_dispatch import wrapper
from test_h3_cumulative_execution import request
from test_minimax_h3_cumulative import FakeVideoVAE, fake_model


class ChunkVideoVAE(FakeVideoVAE):
    def decode(self, *args, **kwargs):
        raise AssertionError("streaming must not materialize eager pixels")

    def decode_to_sink(self, latents, sink, *, abort_check):
        self.inputs.append(latents.clone())
        frames = (latents.shape[2] - 2) // 5 * 17 + 5
        for start in range(0, frames, 17):
            if abort_check():
                raise InterruptedError("fake chunk decode cancelled")
            sink(
                torch.zeros(
                    1,
                    3,
                    min(17, frames - start),
                    latents.shape[3] * 16,
                    latents.shape[4] * 16,
                )
            )
        return frames


def wgp_stream_branch(namespace):
    tree = ast.parse((ROOT / "app/wgp.py").read_text())
    node = next(
        x
        for x in ast.walk(tree)
        if isinstance(x, ast.If)
        and ast.unparse(x.test) == "h3_encoded_video is not None"
        and any(
            isinstance(y, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "output_fps" for t in y.targets)
            for y in x.body
        )
    )
    loop = ast.While(test=ast.Constant(True), body=[node, ast.Break()], orelse=[])
    module = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
    exec(compile(module, "wgp-post-decode-stream", "exec"), namespace)


def wgp_committed_success(receipt, *, abort_on_output):
    """Execute the actual WGP publication notification and return decision."""
    tree = ast.parse((ROOT / "app/wgp.py").read_text())
    function = next(
        x
        for x in tree.body
        if isinstance(x, ast.FunctionDef) and x.name == "_generate_video_impl"
    )
    assignment = lambda node: (
        isinstance(node, ast.Assign)
        and any(
            isinstance(x, ast.Name) and x.id == "h3_streamed_output_committed"
            for x in node.targets
        )
    )
    initialize = next(x for x in function.body if assignment(x))
    publish = next(
        x
        for x in ast.walk(function)
        if isinstance(x, ast.If) and any(assignment(y) for y in x.body)
    )
    body = next(
        value
        for x in ast.walk(function)
        for _field, value in ast.iter_fields(x)
        if isinstance(value, list) and publish in value
    )
    notify = body[body.index(publish) + 1]
    success = next(
        x
        for x in function.body
        if isinstance(x, ast.Assign)
        and any(isinstance(y, ast.Name) and y.id == "success" for y in x.targets)
    )
    namespace = {"h3_encoded_video": receipt, "gen": {"abort": False}}

    def send_cmd(event):
        if event == "output":
            namespace["gen"]["abort"] = abort_on_output

    namespace["send_cmd"] = send_cmd
    module = ast.fix_missing_locations(
        ast.Module(body=[initialize, publish, notify, success], type_ignores=[])
    )
    exec(compile(module, "wgp-stream-output-commit", "exec"), namespace)
    return namespace["success"]


class H3CumulativeStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.threads)

    def setUp(self):
        gate = patch.dict(
            os.environ,
            {
                "MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1",
                "MAESTRO_H3_CUMULATIVE_STREAMING_EXPERIMENTAL": "1",
            },
        )
        gate.start()
        self.addCleanup(gate.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.model = fake_model()
        self.model.vae = ChunkVideoVAE()
        self.samples = None
        self.fail_finality = False
        self.cancel_finality = False
        self.transform_samples = lambda samples: samples
        self.target_count = 0

        def impl(
            task=None,
            send_cmd=None,
            *,
            model_type="minimax_h3",
            video_length=141,
            image_mode=0,
            batch_size=1,
            repeat_generation=1,
            prompt="An adult scene",
            multi_prompts_gen_type=2,
            override_profile=5,
            resolution="64x64",
            _h3_cumulative_dispatch=None,
        ):
            channel = _h3_cumulative_dispatch
            channel.configure_video_sink(
                staging_directory=self.root,
                height=64,
                width=64,
                codec_type="libx264_lossless",
                container="mkv",
                abort_check=lambda: self.model._interrupt,
                timeout=30,
            )
            self.samples = self.model.generate(
                prompt,
                height=64,
                width=64,
                frame_num=video_length,
                sampling_steps=2,
                seed=123,
                custom_settings={"h3_attention_engine": "sdpa"},
                **channel.model_kwargs(
                    base_model_type=model_type,
                    frame_num=video_length,
                    repeat_no=1,
                    window_no=1,
                ),
            )
            if self.samples is None:
                return False
            channel.capture(self.transform_samples(self.samples))
            self.target_count += 1
            channel.encoded_video.transfer_to(
                self.root / f"video-{self.target_count}.mkv"
            )
            if self.fail_finality:
                raise RuntimeError("audio mux/finality failed")
            if self.cancel_finality:
                self.model._interrupt = True
            return wgp_committed_success(
                channel.encoded_video, abort_on_output=self.cancel_finality
            )

        self.run, self.cleanup = wrapper(impl)

    def test_native_model_returns_no_eager_pixels_and_terminal_trim_keeps_full_av(self):
        first = H3CumulativeDispatch(frames=141)
        self.assertTrue(self.run(_h3_cumulative_dispatch=first))
        self.assertIsNone(self.samples["x"])
        prior = first.handoff["state"]
        step = plan_h3_native_continuation_tail(
            50, absolute_context_start_frame=119
        ).steps[0]
        next_call = H3CumulativeDispatch(
            frames=step.target_frames, previous=first.handoff, step=step
        )
        self.assertTrue(
            self.run(video_length=step.target_frames, _h3_cumulative_dispatch=next_call)
        )
        state = next_call.handoff["state"]
        self.assertEqual((state.frame_count, state.published_frames), (192, 191))
        self.assertEqual(self.samples["audio"].shape, (round(191 * 32000 / 24), 2))
        torch.testing.assert_close(
            state.video[:, :, : prior.video.shape[2]], prior.video, rtol=0, atol=0
        )
        torch.testing.assert_close(
            state.audio[..., : prior.audio.shape[-1]], prior.audio, rtol=0, atol=0
        )
        self.assertEqual(self.model.vae.inputs[-1].shape[2], state.video.shape[2])
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_audio_decode_and_finality_failures_discard_candidate_and_keep_prior(self):
        first = H3CumulativeDispatch(frames=141)
        self.run(_h3_cumulative_dispatch=first)
        prior = first.handoff
        step = plan_h3_native_continuation_tail(
            17, absolute_context_start_frame=119
        ).steps[0]
        for failure in ("audio", "finality"):
            self.model.audio_vae.fail = failure == "audio"
            self.fail_finality = failure == "finality"
            channel = H3CumulativeDispatch(
                frames=step.target_frames, previous=prior, step=step
            )
            with (
                self.subTest(failure=failure),
                self.assertRaisesRegex(RuntimeError, "failure|failed"),
            ):
                self.run(
                    video_length=step.target_frames, _h3_cumulative_dispatch=channel
                )
            self.assertIsNone(channel.handoff)
            self.assertIsNotNone(prior["state"])
            self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_successful_finality_commits_handoff_despite_late_cancel(self):
        self.cancel_finality = True
        channel = H3CumulativeDispatch(frames=141)
        # Actual WGP commit/notification/return AST receives cancellation from
        # its output callback. It cannot revoke the completed output's handoff.
        self.assertTrue(self.run(_h3_cumulative_dispatch=channel))
        self.assertIsNotNone(channel.handoff)
        self.assertEqual(channel.handoff["state"].published_frames, 141)
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])
        self.assertEqual(len(list(self.root.glob("video-*.mkv"))), 1)

    def test_ordinary_wgp_success_still_obeys_live_abort(self):
        self.assertFalse(wgp_committed_success(None, abort_on_output=True))
        self.assertTrue(wgp_committed_success(None, abort_on_output=False))

    def test_substituted_receipt_cannot_be_captured(self):
        for copy_receipt in (False, True):

            def substitute(samples, copy_receipt=copy_receipt):
                receipt = samples["_h3_encoded_video"]
                return dict(
                    samples,
                    _h3_encoded_video=replace(receipt)
                    if copy_receipt
                    else {"path": "fake"},
                )

            self.transform_samples = substitute
            channel = H3CumulativeDispatch(frames=141)
            with (
                self.subTest(copy_receipt=copy_receipt),
                self.assertRaisesRegex(ValueError, "bound encoded"),
            ):
                self.run(_h3_cumulative_dispatch=channel)
            self.assertIsNone(channel.handoff)
            self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_cancel_after_audio_decode_returns_failure_without_advancing_handoff(self):
        self.model.audio_vae.after_decode = lambda: setattr(
            self.model, "_interrupt", True
        )
        channel = H3CumulativeDispatch(frames=141)
        self.assertFalse(self.run(_h3_cumulative_dispatch=channel))
        self.assertIsNone(channel.handoff)
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])
        self.assertEqual(list(self.root.glob("video-*.mkv")), [])

    def test_success_without_video_transfer_is_rejected_and_cleans_temporary(self):
        channel = H3CumulativeDispatch(frames=39)
        channel.begin(
            {
                "model_type": "minimax_h3",
                "video_length": 39,
                "batch_size": 1,
                "repeat_generation": 1,
                "prompt": "scene",
            }
        )
        channel.configure_video_sink(
            staging_directory=self.root,
            height=64,
            width=64,
            codec_type="libx264_lossless",
            container="mkv",
            abort_check=lambda: False,
            timeout=30,
        )
        samples = self.model.generate(
            "scene",
            height=64,
            width=64,
            frame_num=39,
            sampling_steps=2,
            **channel.model_kwargs(
                base_model_type="minimax_h3", frame_num=39, repeat_no=1, window_no=1
            ),
        )
        channel.capture(samples)
        with self.assertRaisesRegex(ValueError, "without transferring"):
            channel.finish(True)
        self.assertIsNone(channel.handoff)
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_selected_streaming_admission_preserves_source_and_retained_cap(self):
        source = request(
            resolution="1344x768", video_length=360, sliding_window_size=124
        )
        original = copy.deepcopy(source)
        self.assertGreater(
            prepare_h3_cumulative_request(source)["windows"][-1][
                "cumulative_generated_frames"
            ],
            360,
        )
        self.assertEqual(source, original)
        with (
            patch.dict(
                os.environ, {"MAESTRO_H3_CUMULATIVE_STREAMING_EXPERIMENTAL": "0"}
            ),
            self.assertRaisesRegex(ValueError, "2 GiB"),
        ):
            prepare_h3_cumulative_request(source)
        with self.assertRaisesRegex(ValueError, "512 MiB"):
            prepare_h3_cumulative_request(
                request(resolution="8192x8192", video_length=73, sliding_window_size=73)
            )

    def test_arbitrary_sink_or_changed_geometry_rejected_before_sampling(self):
        bad = H3VideoSink(
            self.root,
            generated_frames=39,
            published_frames=39,
            height=32,
            width=32,
            codec_type=None,
            container="mkv",
            abort_check=lambda: False,
            timeout=30,
        )
        self.addCleanup(bad.cleanup)
        for sink in ({"path": "fake"}, bad):
            with self.subTest(sink=sink), self.assertRaises(ValueError):
                self.model.generate(
                    "scene",
                    height=64,
                    width=64,
                    frame_num=39,
                    sampling_steps=2,
                    _h3_cumulative_capture=True,
                    _h3_cumulative_video_sink=sink,
                )
        self.assertEqual(self.model.transformer.calls, [])
        with self.assertRaises(ValueError):
            validate_h3_cumulative_settings(
                {
                    "model_type": "minimax_h3",
                    "video_length": 39,
                    "batch_size": 1,
                    "repeat_generation": 1,
                    "prompt": "scene",
                    "progressive_pipeline": True,
                },
                frames=39,
            )

    def test_actual_wgp_postdecode_branch_uses_verified_clock_without_tensor_conversion(
        self,
    ):
        sink = H3VideoSink(
            self.root,
            generated_frames=39,
            published_frames=38,
            height=64,
            width=64,
            codec_type="libx264_lossless",
            container="mkv",
            abort_check=lambda: False,
            timeout=30,
        )
        self.addCleanup(sink.cleanup)
        with sink:
            for frames in (17, 17, 5):
                sink(torch.zeros(1, 3, frames, 64, 64))
        audio = np.zeros((round(38 * 32000 / 24), 2), dtype=np.float32)
        namespace = {
            "h3_encoded_video": sink.receipt,
            "height": 64,
            "width": 64,
            "fps": 24,
            "generated_audio": audio,
            "output_audio_sampling_rate": 32000,
            "abort_scheduled": False,
            "gen": {},
            "is_image": False,
            "audio_only": False,
            "sliding_window": False,
            "prefix_video": None,
            "post_decode_pre_trim": 0,
            "_retake_stitch_info": None,
            "_progressive_pad_info": None,
            "temporal_upsampling": "",
            "spatial_upsampling": "",
            "film_grain_intensity": 0,
            "MMAudio_setting": 0,
            "source_video_frames_count": 0,
            "audio_source": None,
            "output_new_audio_filepath": None,
            "output_new_audio_data": None,
            "full_generated_audio": None,
            "samples": None,
            "_video_tensor_to_uint8_chunk_inplace": Mock(
                side_effect=AssertionError("tensor conversion")
            ),
            "save_video": Mock(side_effect=AssertionError("reencoding")),
        }
        wgp_stream_branch(namespace)
        self.assertEqual(
            (namespace["output_frame_count"], namespace["output_fps"]), (38, 24)
        )
        self.assertIs(namespace["output_new_audio_data"], audio)
        self.assertIsNone(namespace["output_video_frames"])
        namespace["_video_tensor_to_uint8_chunk_inplace"].assert_not_called()
        namespace["save_video"].assert_not_called()
        for change in (
            {"_progressive_pad_info": (32, 32)},
            {"gen": {"abort": True}},
            {"sliding_window": True},
        ):
            changed = dict(
                namespace,
                full_generated_audio=None,
                output_new_audio_data=None,
                **change,
            )
            with (
                self.subTest(change=change),
                self.assertRaises((ValueError, InterruptedError)),
            ):
                wgp_stream_branch(changed)


if __name__ == "__main__":
    unittest.main()
