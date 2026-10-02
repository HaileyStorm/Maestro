"""CPU contracts for private AV transport through the real WGP wrapper."""

# AST execution below uses only reviewed repository source, never user input.
# ruff: noqa: S102

from __future__ import annotations

import ast
import inspect
import os
import pickle
import sys
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.h3_cumulative_dispatch import H3CumulativeDispatch
from services.h3_native_continuation import (
    plan_h3_native_continuation_step,
    plan_h3_native_continuation_tail,
)
from services.h3_oom_relief import H3OomReliefRetry
from test_minimax_h3_cumulative import fake_model


def wrapper(impl):
    module = ast.parse((ROOT / "app/wgp.py").read_text())
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "generate_video"
    )
    cleanup = []
    namespace = {
        "inspect": inspect,
        "traceback": traceback,
        "get_default_profile": lambda *args: 5,
        "get_output_type_for_model": lambda *args: "video",
        "_generate_video_impl": impl,
        "_notify_h3_profile_observer": lambda *args: None,
        "_release_failed_generation_resources": lambda: cleanup.append(True),
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            "wgp-cumulative-wrapper",
            "exec",
        ),
        namespace,
    )
    return namespace["generate_video"], cleanup


class H3CumulativeDispatchTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.model = fake_model()
        self.error = None
        self.success = True
        self.calls = []
        self.samples = None

        def impl(
            task=None,
            send_cmd=None,
            *,
            model_type="minimax_h3",
            video_length=141,
            image_mode=0,
            batch_size=1,
            repeat_generation=1,
            prompt="A moving scene",
            multi_prompts_gen_type=2,
            override_profile=5,
            resolution="64x64",
            _h3_cumulative_dispatch=None,
        ):
            self.calls.append(prompt)
            kwargs = (
                {}
                if _h3_cumulative_dispatch is None
                else _h3_cumulative_dispatch.model_kwargs(
                    base_model_type=model_type,
                    frame_num=video_length,
                    repeat_no=1,
                    window_no=1,
                )
            )
            self.samples = self.model.generate(
                prompt,
                height=64,
                width=64,
                frame_num=video_length,
                sampling_steps=2,
                seed=123,
                custom_settings={"h3_attention_engine": "sdpa"},
                **kwargs,
            )
            if _h3_cumulative_dispatch is not None:
                _h3_cumulative_dispatch.capture(self.samples)
            if self.error is not None:
                raise self.error
            return self.success

        self.run, self.cleanup = wrapper(impl)

    def test_capture_then_short_append_preserves_prefix_and_complete_output(self):
        first = H3CumulativeDispatch(frames=141)
        self.assertTrue(self.run(_h3_cumulative_dispatch=first))
        prior = first.handoff["state"]
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )
        next_call = H3CumulativeDispatch(frames=56, previous=first.handoff, step=step)
        self.assertTrue(self.run(video_length=56, _h3_cumulative_dispatch=next_call))
        result = next_call.handoff["state"]
        self.assertEqual(result.frame_count, 175)
        self.assertEqual(self.samples["x"].shape[1], 175)
        torch.testing.assert_close(result.video[:, :, :42], prior.video, rtol=0, atol=0)
        torch.testing.assert_close(result.audio[..., :235], prior.audio, rtol=0, atol=0)

    def test_terminal_publication_trim_keeps_full_retained_state(self):
        first = H3CumulativeDispatch(frames=141)
        self.run(_h3_cumulative_dispatch=first)
        step = plan_h3_native_continuation_tail(
            50, absolute_context_start_frame=119
        ).steps[0]
        next_call = H3CumulativeDispatch(
            frames=step.target_frames, previous=first.handoff, step=step
        )
        self.run(video_length=step.target_frames, _h3_cumulative_dispatch=next_call)
        state = next_call.handoff["state"]
        self.assertEqual((state.frame_count, state.published_frames), (192, 191))

    def test_failed_output_and_encoder_exception_discard_candidate(self):
        self.success = False
        channel = H3CumulativeDispatch(frames=141)
        self.assertFalse(self.run(_h3_cumulative_dispatch=channel))
        self.assertIsNone(channel.handoff)
        self.assertIsNone(channel._candidate)
        self.assertEqual(len(self.cleanup), 1)
        self.success = True
        self.error = RuntimeError("fake encoder failure")
        channel = H3CumulativeDispatch(frames=141)
        with self.assertRaisesRegex(RuntimeError, "encoder failure"):
            self.run(_h3_cumulative_dispatch=channel)
        self.assertIsNone(channel.handoff)
        self.assertIsNone(channel._candidate)
        self.assertEqual(len(self.cleanup), 2)

    def test_oom_does_not_change_geometry_or_repeat_a_cumulative_dispatch(self):
        self.error = H3OomReliefRetry({"resolution": "32x32", "num_inference_steps": 1})
        channel = H3CumulativeDispatch(frames=141)
        with self.assertRaises(H3OomReliefRetry):
            self.run(_h3_cumulative_dispatch=channel)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(channel.handoff)
        self.assertEqual(len(self.cleanup), 1)

    def test_ordinary_generation_has_no_private_kwargs_or_result(self):
        self.assertTrue(self.run())
        self.assertNotIn("_h3_cumulative_handoff", self.samples)

    def test_runtime_object_gate_and_single_use_are_enforced_before_generation(self):
        with self.assertRaises(TypeError):
            self.run(_h3_cumulative_dispatch={"frames": 141})
        channel = H3CumulativeDispatch(frames=141)
        with (
            patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}),
            self.assertRaisesRegex(ValueError, "gate"),
        ):
            self.run(_h3_cumulative_dispatch=channel)
        self.assertEqual(self.calls, [])
        self.assertEqual(channel._phase, "failed")
        channel = H3CumulativeDispatch(frames=141)
        self.run(_h3_cumulative_dispatch=channel)
        with self.assertRaisesRegex(ValueError, "single-use"):
            self.run(_h3_cumulative_dispatch=channel)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(TypeError):
            pickle.dumps(channel)

    def test_preamble_and_observer_failures_release_previous_state(self):
        first = H3CumulativeDispatch(frames=141)
        self.run(_h3_cumulative_dispatch=first)
        step = plan_h3_native_continuation_step(
            22, 34, absolute_context_start_frame=119
        )
        for stage in ("validation", "profile", "observer"):
            channel = H3CumulativeDispatch(frames=56, previous=first.handoff, step=step)
            with self.subTest(stage=stage):
                if stage == "validation":
                    with self.assertRaises(ValueError):
                        self.run(
                            model_type="minimax_h3",
                            video_length=57,
                            _h3_cumulative_dispatch=channel,
                        )
                elif stage == "profile":
                    with (
                        patch(
                            "services.h3_oom_relief.apply_h3_baseline_offload_profile",
                            side_effect=RuntimeError("profile setup"),
                        ),
                        self.assertRaisesRegex(RuntimeError, "profile setup"),
                    ):
                        self.run(
                            model_type="minimax_h3",
                            video_length=56,
                            _h3_cumulative_dispatch=channel,
                        )
                else:
                    with (
                        patch.dict(
                            self.run.__globals__,
                            {
                                "_notify_h3_profile_observer": lambda *args: (
                                    _ for _ in ()
                                ).throw(RuntimeError("observer setup"))
                            },
                        ),
                        self.assertRaisesRegex(RuntimeError, "observer setup"),
                    ):
                        self.run(
                            model_type="minimax_h3",
                            video_length=56,
                            _h3_cumulative_dispatch=channel,
                        )
                self.assertEqual(channel._phase, "failed")
                self.assertIsNone(channel.previous)
                self.assertIsNone(channel.handoff)
        self.assertEqual(len(self.calls), 1)

    def test_request_rejects_concat_prefix_repeat_and_processing_without_prompt_judgment(
        self,
    ):
        settings = {
            "model_type": "minimax_h3",
            "video_length": 141,
            "batch_size": 1,
            "repeat_generation": 1,
            "image_mode": 0,
            "prompt": "Adult, violent, controversial scene\nwith dialogue",
            "multi_prompts_gen_type": 2,
        }
        variants = (
            {"multi_clip_info": {"total": 2}},
            {"multi_clip_info": {"source_prefix": {}}},
            {"multi_clip_info": {"trim_tail": 1}},
            {"repeat_generation": 2},
            {"repeat_generation": True},
            {"batch_size": 2},
            {"video_length": 142},
            {"model_type": "minimax_h3_ref2va"},
            {"image_start": "frame.png"},
            {"force_fps": 24},
            {"trim_tail_frames": 1},
            {"retake_video": "previous.mp4"},
            {"audio_frame_offset": 1},
            {"temporal_upsampling": "rife"},
            {"_h3_native_boundary": {}},
            {"after_repeat_output": lambda: True},
        )
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                H3CumulativeDispatch(frames=141).begin(dict(settings, **changed))
        channel = H3CumulativeDispatch(frames=141)
        channel.begin(
            dict(settings, multi_clip_info={"total": 2, "defer_concat": True})
        )
        other = H3CumulativeDispatch(frames=141)
        self.run(prompt=settings["prompt"], _h3_cumulative_dispatch=other)
        self.assertEqual(self.calls, [settings["prompt"]])

    def test_changed_window_or_malformed_result_cannot_finish(self):
        channel = H3CumulativeDispatch(frames=141)
        channel.begin(
            {
                "model_type": "minimax_h3",
                "video_length": 141,
                "batch_size": 1,
                "repeat_generation": 1,
                "image_mode": 0,
                "prompt": "scene",
            }
        )
        with self.assertRaisesRegex(ValueError, "invocation changed"):
            channel.model_kwargs(
                base_model_type="minimax_h3", frame_num=141, repeat_no=1, window_no=2
            )
        with self.assertRaisesRegex(ValueError, "without retained"):
            channel.finish(True)
        self.assertIsNone(channel.handoff)
        first = H3CumulativeDispatch(frames=141)
        self.run(_h3_cumulative_dispatch=first)
        for changed in (
            {"audio_sampling_rate": 44100},
            {"x": torch.zeros(3, 140, 64, 64)},
            {"audio": None},
        ):
            channel = H3CumulativeDispatch(frames=141)
            channel.begin(
                {
                    "model_type": "minimax_h3",
                    "video_length": 141,
                    "batch_size": 1,
                    "repeat_generation": 1,
                    "image_mode": 0,
                    "prompt": "scene",
                }
            )
            channel.model_kwargs(
                base_model_type="minimax_h3", frame_num=141, repeat_no=1, window_no=1
            )
            with (
                self.subTest(changed=tuple(changed)),
                self.assertRaisesRegex(ValueError, "different AV timeline"),
            ):
                channel.capture(dict(self.samples, **changed))

    def test_real_wgp_call_transport_and_metadata_strip_execute(self):
        module = ast.parse((ROOT / "app/wgp.py").read_text())
        calls = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "model_kwargs"
            and ast.unparse(node.func.value) == "_h3_cumulative_dispatch"
        ]
        self.assertEqual(len(calls), 1)
        channel = H3CumulativeDispatch(frames=141)
        channel.begin(
            {
                "model_type": "minimax_h3",
                "video_length": 141,
                "batch_size": 1,
                "repeat_generation": 1,
                "image_mode": 0,
                "prompt": "scene",
            }
        )
        namespace = {
            "_h3_cumulative_dispatch": channel,
            "base_model_type": "minimax_h3",
            "current_video_length": 141,
            "model_def": {},
            "repeat_no": 1,
            "window_no": 1,
            "align_model_frame_count": lambda frames, *args, **kwargs: frames,
        }
        kwargs = eval(
            compile(ast.Expression(calls[0]), "wgp-model-transport", "eval"), namespace
        )
        self.assertEqual(kwargs, {"_h3_cumulative_capture": True})
        capture = next(
            node
            for node in ast.walk(module)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "_h3_cumulative_dispatch is not None"
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "capture"
                for child in ast.walk(node)
            )
        )
        self.samples = self.model.generate(
            "scene",
            height=64,
            width=64,
            frame_num=141,
            sampling_steps=2,
            custom_settings={"h3_attention_engine": "sdpa"},
            **kwargs,
        )
        namespace["samples"] = self.samples
        exec(
            compile(
                ast.Module(body=[capture], type_ignores=[]), "wgp-model-capture", "exec"
            ),
            namespace,
        )
        metadata = next(
            node
            for node in ast.walk(module)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "_h3_cumulative_dispatch is not None"
            and any(
                isinstance(child, ast.Assign)
                and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "inputs"
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "video_length"
                    for target in child.targets
                )
                for child in ast.walk(node)
            )
        )
        namespace["inputs"] = {"video_length": 56, "duration_seconds": 0}
        exec(
            compile(
                ast.Module(body=[metadata], type_ignores=[]),
                "wgp-cumulative-timing",
                "exec",
            ),
            namespace,
        )
        self.assertEqual(
            namespace["inputs"], {"video_length": 141, "duration_seconds": 141 / 24.0}
        )
        channel.finish(True)
        self.assertIsNotNone(channel.handoff)
        pops = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "inputs.pop"
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and node.value.args[0].value == "_h3_cumulative_dispatch"
        ]
        self.assertEqual(len(pops), 2)
        for node in pops:
            settings = {"prompt": "scene", "_h3_cumulative_dispatch": channel}
            exec(
                compile(
                    ast.Module(body=[node], type_ignores=[]),
                    "wgp-private-strip",
                    "exec",
                ),
                {"inputs": settings},
            )
            self.assertEqual(settings, {"prompt": "scene"})


if __name__ == "__main__":
    unittest.main()
