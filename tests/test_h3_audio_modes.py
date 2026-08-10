"""Model-free contracts for clean-room H3 source-audio behavior."""

from __future__ import annotations

from pathlib import Path
import ast
import hashlib
import os
import sys
import tempfile
import unittest

import torch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from models.minimax_h3.minimax_h3_main import (  # noqa: E402
    MiniMaxH3Model,
    _advance_paired_h3_latents,
    _fit_h3_source_audio_latents,
    _fit_h3_source_waveform,
    _run_h3_master_schedule,
)
from models.minimax_h3.minimax_h3_handler import family_handler  # noqa: E402
from services.h3_audio import (  # noqa: E402
    H3AudioCompatibilityError,
    H3MediaMapError,
    remap_primary_audio,
    remap_prompt_audio_ordinals,
    resolve_h3_audio_roles,
    validate_prompt_media_ordinals,
)
from services.h3_turbo import resolve_h3_turbo_schedule  # noqa: E402
from services.h3_benchmark import (  # noqa: E402
    H3BenchmarkError,
    build_benchmark_spec,
    normalize_estimate_context,
)


BASE_DEF = {"minimax_h3_reference_mode": False}


def request(mode: str, **overrides):
    values = {
        "selected_model_type": "minimax_h3",
        "model_def": BASE_DEF,
        "custom_settings": {
            "h3_source_audio_mode": mode,
            "h3_attention_engine": "sdpa",
        },
        "sampling_steps": 20,
        "attention_engine": "sdpa",
        "audio_prompt_type": "A",
        "audio_guides": ("drive.wav",),
        "final_audio": None,
        "semantic_references": False,
        "multisegment": False,
        "activated_loras": (),
        "loras_multipliers": "",
        "skip_steps_cache_type": "",
        "native_boundary": False,
    }
    values.update(overrides)
    return values


class H3SourceAudioRoleTests(unittest.TestCase):
    def test_modes_keep_drive_and_final_roles_independent(self):
        native = resolve_h3_audio_roles(**request("native"))
        locked = resolve_h3_audio_roles(**request("lock_source"))
        remix = resolve_h3_audio_roles(**request("remix_source"))
        reference = resolve_h3_audio_roles(**request("reference_only"))
        self.assertIsNone(native.drive_audio)
        self.assertEqual(native.final_audio_kind, "generated")
        self.assertEqual((locked.drive_audio, locked.final_audio), ("drive.wav", "drive.wav"))
        self.assertEqual(locked.final_audio_kind, "source")
        self.assertEqual(remix.drive_audio, "drive.wav")
        self.assertIsNone(remix.final_audio)
        self.assertEqual(remix.final_audio_kind, "generated")
        self.assertEqual(reference.reference_audios, ("drive.wav",))
        self.assertIsNone(reference.final_audio)

        explicit = resolve_h3_audio_roles(
            **request("remix_source", final_audio="delivery.wav")
        )
        self.assertEqual(explicit.drive_audio, "drive.wav")
        self.assertEqual(explicit.final_audio, "delivery.wav")
        self.assertEqual(explicit.final_audio_kind, "explicit")

    def test_primary_audio_remap_is_stable_and_collision_safe(self):
        roles = resolve_h3_audio_roles(**request(
            "reference_only",
            audio_guides=("one.wav", "two.wav", "three.wav"),
            custom_settings={
                "h3_source_audio_mode": "reference_only",
                "h3_primary_audio_ordinal": 2,
                "h3_attention_engine": "sdpa",
            },
        ))
        self.assertEqual(
            roles.reference_audios,
            ("two.wav", "one.wav", "three.wav"),
        )
        self.assertEqual(dict(roles.audio_ordinal_remap), {1: 2, 2: 1, 3: 3})
        prompt = "<Audio 1> echoes <Audio 2>; <Audio 3> remains separate."
        self.assertEqual(
            remap_prompt_audio_ordinals(prompt, dict(roles.audio_ordinal_remap)),
            "<Audio 2> echoes <Audio 1>; <Audio 3> remains separate.",
        )
        with self.assertRaisesRegex(H3AudioCompatibilityError, "must be an integer"):
            resolve_h3_audio_roles(**request(
                "reference_only",
                audio_guides=("one.wav", "two.wav"),
                custom_settings={
                    "h3_source_audio_mode": "reference_only",
                    "h3_primary_audio_ordinal": 1.5,
                    "h3_attention_engine": "sdpa",
                },
            ))

    def test_experimental_matrix_rejects_every_unproven_combination(self):
        invalid = (
            {"selected_model_type": "minimax_h3_ref2va",
             "model_def": {"minimax_h3_reference_mode": True}},
            {"selected_model_type": "minimax_h3_w4a8_fl2va"},
            {"semantic_references": True},
            {"multisegment": True},
            {"native_boundary": True},
            {"sampling_steps": 19},
            {"sampling_steps": 20.5},
            {"attention_engine": "sage2"},
            {"activated_loras": ("user.safetensors",)},
            {"loras_multipliers": "1.0"},
            {"skip_steps_cache_type": "tea"},
            {"audio_prompt_type": "AN"},
        )
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(
                H3AudioCompatibilityError
            ):
                resolve_h3_audio_roles(**request("remix_source", **override))
        for accelerator in (
            "h3_turbo_profile", "h3_spectrum_profile", "h3_lightx2v_profile",
        ):
            custom = {
                "h3_source_audio_mode": "remix_source",
                "h3_attention_engine": "sdpa",
                accelerator: "enabled",
            }
            with self.subTest(accelerator=accelerator), self.assertRaises(
                H3AudioCompatibilityError
            ):
                resolve_h3_audio_roles(**request(
                    "remix_source", custom_settings=custom,
                ))

    def test_remix_strength_is_mode_specific_and_bounded(self):
        for strength in (0, -0.1, 1.01, True, "not-a-number"):
            with self.subTest(strength=strength), self.assertRaises(
                H3AudioCompatibilityError
            ):
                resolve_h3_audio_roles(**request(
                    "remix_source",
                    custom_settings={
                        "h3_source_audio_mode": "remix_source",
                        "h3_attention_engine": "sdpa",
                        "h3_audio_remix_strength": strength,
                    },
                ))
        for mode in ("lock_source", "reference_only"):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                H3AudioCompatibilityError, "applies only"
            ):
                resolve_h3_audio_roles(**request(
                    mode,
                    custom_settings={
                        "h3_source_audio_mode": mode,
                        "h3_attention_engine": "sdpa",
                        "h3_audio_remix_strength": 0.5,
                    },
                ))
        for disabled_cache in (0, False, "", None):
            with self.subTest(disabled_cache=disabled_cache):
                self.assertTrue(resolve_h3_audio_roles(**request(
                    "remix_source",
                    skip_steps_cache_type=disabled_cache,
                )).experimental)

    def test_obsolete_multirate_runtime_predecessor_is_removed(self):
        audio_source = (APP / "services" / "h3_audio.py").read_text(
            encoding="utf-8"
        )
        handler_source = (
            APP / "models" / "minimax_h3" / "minimax_h3_handler.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("validate_multirate_evidence_request", audio_source)
        self.assertNotIn("h3_multirate_profile", handler_source)


class H3MediaOrdinalTests(unittest.TestCase):
    def test_namespaces_are_independent_contiguous_and_content_neutral(self):
        prompt = (
            "<Picture 1> and <Video 1> establish the scene; "
            "<Audio 1> then <Audio 2> establish sound."
        )
        media_map = validate_prompt_media_ordinals(
            prompt, picture_count=1, video_count=1, audio_count=2,
        )
        self.assertEqual(
            [entry["tag"] for entry in media_map],
            ["<Picture 1>", "<Video 1>", "<Audio 1>", "<Audio 2>"],
        )
        # Creative subject matter is not inspected or classified; only the
        # structural tags are read.
        sensitive = "adult violent controversial political " + prompt
        self.assertEqual(
            validate_prompt_media_ordinals(
                sensitive, picture_count=1, video_count=1, audio_count=2,
            ),
            media_map,
        )

    def test_unknown_zero_and_gapped_ordinals_fail_closed(self):
        for prompt, counts in (
            ("<Audio 0>", {"audio_count": 1}),
            ("<Picture 2>", {"picture_count": 1}),
            ("<Video 1>", {"video_count": 0}),
            ("<Audio 2>", {"audio_count": 2}),
        ):
            with self.subTest(prompt=prompt), self.assertRaises(H3MediaMapError):
                validate_prompt_media_ordinals(prompt, **counts)

    def test_media_like_tags_must_use_the_exact_canonical_shape(self):
        for prompt in (
            "<Audio N>", "<Audio -1>", "<Audio 1 >", "<Picture>",
            "<Video two>", "<Audio  1>", "<Audio\t1>", "<Audio\n1>",
            "<Audio 01>", "<audio 1>",
        ):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(
                H3MediaMapError, "not a canonical"
            ):
                validate_prompt_media_ordinals(
                    prompt, picture_count=1, video_count=1, audio_count=1,
                )
        for prompt, counts in (
            (None, {}),
            ("<Audio 1>", {"audio_count": 1.5}),
        ):
            with self.subTest(prompt=prompt, counts=counts), self.assertRaises(
                H3MediaMapError
            ):
                validate_prompt_media_ordinals(prompt, **counts)


class H3AudioHandlerParityTests(unittest.TestCase):
    def test_experimental_audio_slots_are_not_misclassified_as_ref2va(self):
        for mode in ("lock_source", "remix_source", "reference_only"):
            inputs = {
                "prompt": "Use <Audio 1> as the source-audio role.",
                "num_inference_steps": 20,
                "audio_prompt_type": "A",
                "audio_guide": "drive.wav",
                "custom_settings": {
                    "h3_source_audio_mode": mode,
                    "h3_attention_engine": "sdpa",
                },
                "tea_cache": False,
            }
            with self.subTest(mode=mode):
                self.assertIsNone(
                    family_handler.validate_generative_settings(
                        "minimax_h3", BASE_DEF, inputs,
                    )
                )

    def test_real_semantic_reference_inputs_still_fail_closed(self):
        inputs = {
            "prompt": "Use <Audio 1>.",
            "num_inference_steps": 20,
            "audio_prompt_type": "A",
            "audio_guide": "drive.wav",
            "image_refs": [object()],
            "custom_settings": {
                "h3_source_audio_mode": "reference_only",
                "h3_attention_engine": "sdpa",
            },
        }
        self.assertIn(
            "semantic references",
            family_handler.validate_generative_settings(
                "minimax_h3", BASE_DEF, inputs,
            ),
        )


class H3SourceAudioLatentTests(unittest.TestCase):
    def test_waveform_coercion_rejects_multichannel_and_empty_inputs(self):
        model = object.__new__(MiniMaxH3Model)
        with self.assertRaisesRegex(ValueError, "multichannel"):
            model._coerce_waveform(torch.zeros((100, 6)), 32000)
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            model._coerce_waveform(torch.zeros((0, 2)), 32000)
        stereo = model._coerce_waveform(torch.zeros((100, 2)), 32000)
        self.assertEqual(tuple(stereo.shape), (2, 100))

    def test_waveform_and_latent_fit_use_exact_target_clock(self):
        waveform = torch.arange(10, dtype=torch.float32).repeat(2, 1)
        self.assertEqual(tuple(_fit_h3_source_waveform(waveform, 6).shape), (2, 6))
        padded = _fit_h3_source_waveform(waveform[:, :3], 6)
        self.assertTrue(torch.equal(padded[:, 3:], torch.zeros((2, 3))))
        latents = torch.ones((2, 32, 3))
        fitted = _fit_h3_source_audio_latents(latents, 5)
        self.assertEqual(tuple(fitted.shape), (2, 32, 5))
        self.assertTrue(torch.equal(fitted[..., 3:], torch.zeros((2, 32, 2))))

    def test_lock_source_advances_both_schedulers_but_restores_audio_rows(self):
        class Scheduler:
            def __init__(self, delta):
                self.delta = delta
                self.calls = 0

            def step(self, _velocity, _timestep, sample, return_dict=False):
                self.calls += 1
                return (sample + self.delta,)

        video = torch.zeros((2, 3))
        audio = torch.zeros((4, 3))
        locked = torch.full((4, 3), 7.0)
        video_scheduler = Scheduler(1)
        audio_scheduler = Scheduler(2)
        prediction = (torch.zeros((1, 2, 3)), torch.zeros((1, 4, 3)))
        _advance_paired_h3_latents(
            video_rows=video,
            audio_rows=audio,
            prediction=prediction,
            video_timestep=torch.tensor(0.0),
            audio_timestep=torch.tensor(0.0),
            video_scheduler=video_scheduler,
            audio_scheduler=audio_scheduler,
            num_condition_video_rows=0,
            num_condition_audio_rows=0,
            locked_target_audio_rows=locked,
        )
        self.assertTrue(torch.equal(video, torch.ones_like(video)))
        self.assertTrue(torch.equal(audio, locked))
        self.assertEqual((video_scheduler.calls, audio_scheduler.calls), (1, 1))

    def test_turbo_four_advances_audio_twice_per_frozen_video_state(self):
        class Scheduler:
            def __init__(self, delta):
                self.delta = delta
                self.calls = 0

            def step(self, _velocity, _timestep, sample, return_dict=False):
                self.calls += 1
                return (sample + self.delta,)

        schedule = resolve_h3_turbo_schedule(4)
        video = torch.zeros((2, 3))
        audio = torch.zeros((4, 3))
        video_scheduler = Scheduler(1)
        audio_scheduler = Scheduler(2)
        prediction = (torch.zeros((1, 2, 3)), torch.zeros((1, 4, 3)))
        video_states = []
        for tick in range(schedule.master_evaluations):
            _advance_paired_h3_latents(
                video_rows=video,
                audio_rows=audio,
                prediction=prediction,
                video_timestep=torch.tensor(0.0),
                audio_timestep=torch.tensor(0.0),
                video_scheduler=video_scheduler,
                audio_scheduler=audio_scheduler,
                num_condition_video_rows=0,
                num_condition_audio_rows=0,
                advance_video=tick in schedule.video_advance_ticks,
            )
            video_states.append(float(video[0, 0]))

        self.assertEqual(video_states, [0, 1, 1, 2, 2, 3, 3, 4])
        self.assertTrue(torch.equal(audio, torch.full_like(audio, 16)))
        self.assertEqual((video_scheduler.calls, audio_scheduler.calls), (4, 8))

    def test_tick_tensor_publication_is_atomic_when_audio_step_fails(self):
        class VideoScheduler:
            def step(self, _velocity, _timestep, sample, return_dict=False):
                return (sample + 1,)

        class FailingAudioScheduler:
            def step(self, *_args, **_kwargs):
                raise RuntimeError("audio clock failed")

        video = torch.zeros((2, 3))
        audio = torch.zeros((4, 3))
        prediction = (torch.zeros((1, 2, 3)), torch.zeros((1, 4, 3)))
        with self.assertRaisesRegex(RuntimeError, "audio clock failed"):
            _advance_paired_h3_latents(
                video_rows=video,
                audio_rows=audio,
                prediction=prediction,
                video_timestep=torch.tensor(0.0),
                audio_timestep=torch.tensor(0.0),
                video_scheduler=VideoScheduler(),
                audio_scheduler=FailingAudioScheduler(),
                num_condition_video_rows=0,
                num_condition_audio_rows=0,
            )
        self.assertTrue(torch.equal(video, torch.zeros_like(video)))
        self.assertTrue(torch.equal(audio, torch.zeros_like(audio)))

    def test_final_tick_cancellation_resets_both_clocks(self):
        interrupted = False
        resets = []
        advanced = []

        def after_step(index):
            nonlocal interrupted
            if index == 1:
                interrupted = True

        completed = _run_h3_master_schedule(
            timesteps=(0.0, 1.0),
            audio_timesteps=(0.0, 1.0),
            row_plan=(((), ()), ((), ())),
            video_advance_ticks=(0, 1),
            interrupt_requested=lambda: interrupted,
            predict=lambda *_args: object(),
            advance=lambda *_args, **kwargs: advanced.append(
                kwargs["advance_video"]
            ),
            after_step=after_step,
            reset=lambda: resets.append("both"),
        )
        self.assertFalse(completed)
        self.assertEqual(advanced, [True, True])
        self.assertEqual(resets, ["both"])


class H3AudioBenchmarkIsolationTests(unittest.TestCase):
    def test_estimator_executes_the_same_source_audio_compatibility_matrix(self):
        base = {
            "model_type": "minimax_h3",
            "duration_seconds": 5.0,
            "window_seconds": 15.0,
            "window_overlap": 0,
            "num_inference_steps": 20,
            "resolution": "608x352",
            "custom_settings": {
                "h3_source_audio_mode": "remix_source",
                "h3_attention_engine": "sdpa",
            },
            "reference_shape": {},
            "activated_loras": [],
            "loras_multipliers": "",
            "tea_cache": False,
        }
        self.assertEqual(
            normalize_estimate_context(base)["source_audio_mode"],
            "remix_source",
        )
        invalid = (
            {"model_type": "minimax_h3_ref2va"},
            {"model_type": "minimax_h3_w4a8_fl2va"},
            {"model_type": "minimax_h3_pinkcherry_fl2va"},
            {"reference_shape": {"image_count": 1}},
            {"duration_seconds": 20.0},
            {"num_inference_steps": 19},
            {"tea_cache": True},
            {"activated_loras": ["user.safetensors"]},
            {"custom_settings": {
                "h3_source_audio_mode": "remix_source",
                "h3_attention_engine": "sage2",
            }},
            {"custom_settings": {
                "h3_source_audio_mode": "remix_source",
                "h3_attention_engine": "sdpa",
                "h3_turbo_profile": "h3_turbo_v4",
            }},
            {"custom_settings": {
                "h3_source_audio_mode": "remix_source",
                "h3_attention_engine": "sdpa",
                "h3_spectrum_profile": "spectrum_h3_v1",
            }},
            {"custom_settings": {
                "h3_source_audio_mode": "remix_source",
                "h3_attention_engine": "sdpa",
                "h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1",
            }},
            {"custom_settings": {
                "h3_source_audio_mode": "remix_source",
                "h3_attention_engine": "sdpa",
                "h3_native_boundary_conditioning": True,
            }},
        )
        for override in invalid:
            candidate = dict(base)
            candidate.update(override)
            with self.subTest(override=override), self.assertRaises(
                H3BenchmarkError
            ):
                normalize_estimate_context(candidate)

    def test_explicit_source_audio_identity_is_path_free_and_cache_separated(self):
        native = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test"}, runtime={"model_load_state": "resident"},
            model={"id": "minimax_h3"}, engine={"id": "sdpa"},
            encoder={"id": "test"},
            task={"profile": "observed_job", "sampling_steps": 20},
        )
        remix = build_benchmark_spec(
            case_id="text_only",
            hardware={"gpu": "test"}, runtime={"model_load_state": "resident"},
            model={"id": "minimax_h3"}, engine={"id": "sdpa"},
            encoder={"id": "test"},
            task={
                "profile": "observed_job", "sampling_steps": 20,
                "source_audio_mode": "remix_source",
                "audio_algorithm_version": "maestro_h3_source_audio_v1",
            },
        )
        self.assertNotEqual(native["cache_key"], remix["cache_key"])

    def test_launch_capture_excludes_non_native_but_not_ordinary_requests(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_record_h3_benchmark_observation"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {"wgp": object(), "_H3_LONG_STUDIO_MODELS": set()}
        exec(compile(module, str(APP / "launch.py"), "exec"), namespace)
        capture = namespace["_record_h3_benchmark_observation"]

        class TrackingParams(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.keys_read = []

            def get(self, key, default=None):
                self.keys_read.append(key)
                return super().get(key, default)

        experimental = TrackingParams({
            "custom_settings": {"h3_source_audio_mode": "remix_source"},
            "model_type": "minimax_h3",
        })
        capture(
            experimental, wall_time_seconds=1, output_files=[], out_dir=".",
        )
        self.assertNotIn("model_type", experimental.keys_read)

        ordinary = TrackingParams({
            "custom_settings": {}, "model_type": "not-h3",
        })
        capture(ordinary, wall_time_seconds=1, output_files=[], out_dir=".")
        self.assertIn("model_type", ordinary.keys_read)

    def test_estimate_transport_allowlist_contains_only_path_free_audio_controls(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        start = source.index("async def h3_estimate(")
        end = source.index("@api.get(\"/api/v1/h3/benchmark\")", start)
        estimate = source[start:end]
        for key in (
            "h3_source_audio_mode", "h3_primary_audio_ordinal",
            "h3_audio_remix_strength",
        ):
            self.assertIn(f'"{key}"', estimate)
        self.assertNotIn("drive_audio", estimate)
        self.assertNotIn("final_audio", estimate)


class H3AudioSourceWiringTests(unittest.TestCase):
    def test_recovered_repeat_consumes_private_identity_before_fresh_repeat(self):
        import inspect

        source = (APP / "wgp.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "generate_video"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        state = {"gen": {"abort": False, "extra_orders": 0}}
        namespace = {
            "inspect": inspect,
            "get_gen_info": lambda current: current["gen"],
        }
        exec(compile(module, str(APP / "wgp.py"), "exec"), namespace)
        dispatch = namespace["generate_video"]
        signature = inspect.signature(dispatch)

        seen = []

        def succeed(**arguments):
            seen.append(arguments.get("_h3_source_audio_premux_recovery"))
            return True

        succeed.__signature__ = signature
        namespace["generate_video"] = succeed
        arguments = {
            name: None
            for name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty
        }
        arguments.update({
            "task": {},
            "send_cmd": lambda *_args: None,
            "state": state,
            "_h3_source_audio_premux_recovery": {"repeat_index": 1},
            "repeat_generation": 2,
            "after_repeat_output": lambda: True,
        })
        self.assertTrue(dispatch(**arguments))
        self.assertEqual(seen, [{"repeat_index": 1}, None])

        generate_source = ast.get_source_segment(source, node)
        failed_dispatch = generate_source.index(
            "if not generate_video(**recursive_arguments):",
        )
        consume = generate_source.index(
            'recursive_arguments.pop("_h3_source_audio_premux_recovery", None)',
            failed_dispatch,
        )
        advance = generate_source.index("completed_repeats += 1", consume)
        self.assertLess(failed_dispatch, consume)
        self.assertLess(consume, advance)

    def test_mux_failure_checkpoint_restarts_without_generation(self):
        source = (APP / "wgp.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "resume_h3_source_audio_premux"
        )

        class StageError(RuntimeError):
            def __init__(self, message, *, stage, code):
                super().__init__(message)
                self.stage, self.code = stage, code

        namespace = {
            "os": os,
            "PostDecodeStageError": StageError,
            "combine_and_concatenate_video_with_audio_tracks": None,
        }
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(APP / "wgp.py"), "exec"), namespace)
        resume = namespace["resume_h3_source_audio_premux"]

        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            video = staging / "unit-job-r0-premux-video.mp4"
            audio = staging / "unit-job-r0-premux-audio.wav"
            output = staging / "unit-job-r0.mp4"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            generation_calls = []

            def fail_mux(*_args, **_kwargs):
                raise RuntimeError("injected mux failure")

            with self.assertRaisesRegex(RuntimeError, "injected"):
                resume(
                    premux_video_path=video,
                    premux_audio_path=audio,
                    final_audio_path=None,
                    output_path=output,
                    recovery_staging_dir=staging,
                    combine_fn=fail_mux,
                )
            self.assertTrue(video.is_file())
            self.assertTrue(audio.is_file())
            self.assertFalse(output.exists())

            def finish_mux(output_path, input_path, *_args, **kwargs):
                generation_calls.append("mux-only")
                self.assertEqual(Path(input_path), video)
                self.assertEqual(kwargs["output_audio_channels"], 2)
                Path(output_path).write_bytes(b"final")

            result = resume(
                premux_video_path=video,
                premux_audio_path=audio,
                final_audio_path=None,
                output_path=output,
                recovery_staging_dir=staging,
                combine_fn=finish_mux,
            )
            self.assertEqual(Path(result), output)
            self.assertEqual(generation_calls, ["mux-only"])
            self.assertFalse(video.exists())
            self.assertFalse(audio.exists())
            self.assertEqual(output.read_bytes(), b"final")

    def test_launch_premux_unit_is_hash_bound_and_private(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            "_queue_recovery_validate_staged_artifact",
            "_queue_recovery_staged_artifact_path",
            "_queue_recovery_checkpoint_staged_premux",
        }
        nodes = [
            item for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name in names
        ]

        class RecoveryError(RuntimeError):
            pass

        checkpoints = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / ".maestro-recovery" / "staging"
            staging.mkdir(parents=True)

            def file_digest(path):
                payload = Path(path).read_bytes()
                return len(payload), hashlib.sha256(payload).hexdigest()

            namespace = {
                "os": os,
                "hmac": __import__("hmac"),
                "QueueRecoveryRuntimeError": RecoveryError,
                "ensure_recovery_staging_directory": lambda _root: str(staging),
                "_recovery_sha256_file": file_digest,
                "recovery_unit_id": lambda *_args, **_kwargs: "unit:v1:test",
                "_queue_recovery_units": lambda job: list(
                    (job.get("recovery_cursor") or {}).get("completed_units") or []
                ),
                "_queue_recovery_checkpoint": lambda job, **values: (
                    job.update(values), checkpoints.append(values)
                ),
            }
            module = ast.Module(body=nodes, type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(APP / "launch.py"), "exec"), namespace)
            video = staging / "unit-job-r0-premux-video.mp4"
            audio = staging / "unit-job-r0-premux-audio.wav"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            job = {"id": "job", "recovery_cursor": {}}
            unit = namespace["_queue_recovery_checkpoint_staged_premux"](
                job,
                index=0,
                project_dir=str(root),
                media_paths={"video": str(video), "audio": str(audio)},
                settings={
                    "algorithm_version": "maestro_h3_source_audio_v1",
                    "final_audio_kind": "generated",
                    "source_audio_mode": "remix_source",
                },
            )
            self.assertTrue(checkpoints)
            self.assertTrue(all(
                item["storage"] == "recovery_staging"
                and "path" not in item
                for item in unit["artifacts"]
            ))
            self.assertTrue(all(
                namespace["_queue_recovery_validate_staged_artifact"](
                    str(root), item,
                )
                for item in unit["artifacts"]
            ))
            video.write_bytes(b"changed")
            video_descriptor = next(
                item for item in unit["artifacts"]
                if item["media_kind"] == "video"
            )
            self.assertFalse(
                namespace["_queue_recovery_validate_staged_artifact"](
                    str(root), video_descriptor,
                )
            )

    def test_experimental_delivery_keeps_h3_32khz_stereo_contract(self):
        source = (APP / "wgp.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "resolve_mux_audio_contract"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "np": __import__("numpy"),
            "resolve_mux_audio_sampling_rate": lambda *_args: 48000,
        }
        exec(compile(module, str(APP / "wgp.py"), "exec"), namespace)
        contract = namespace["resolve_mux_audio_contract"]
        self.assertEqual(
            contract(
                "minimax_h3", None, 48000,
                audio_paths=["delivery.wav"],
                experimental_h3_audio_selected=True,
            ),
            (32000, 2),
        )
        self.assertEqual(
            contract(
                "minimax_h3", None, 48000,
                audio_paths=["delivery.wav"],
            ),
            (48000, 1),
        )

    def test_runtime_and_mux_wiring_preserve_separate_roles(self):
        main = (APP / "models/minimax_h3/minimax_h3_main.py").read_text(encoding="utf-8")
        wgp = (APP / "wgp.py").read_text(encoding="utf-8")
        handler = (APP / "models/minimax_h3/minimax_h3_handler.py").read_text(encoding="utf-8")
        self.assertIn("source_audio_roles = resolve_h3_audio_roles", main)
        self.assertIn("not experimental_source_audio", main)
        self.assertIn("if source_audio_roles.experimental:", main)
        self.assertIn("locked_target_audio_rows=locked_target_audio_rows", main)
        self.assertIn("audio_source = h3_audio_roles.final_audio", wgp)
        self.assertIn("not h3_experimental_source_audio", wgp)
        self.assertIn("experimental_h3_audio_selected=bool(", wgp)
        resume_branch = wgp.index("if _h3_source_audio_premux_recovery is not None:")
        model_load_branch = wgp.index('if "P" in preload_model_policy', resume_branch)
        self.assertLess(resume_branch, model_load_branch)
        self.assertIn(
            'recursive_arguments.pop("_h3_source_audio_premux_recovery", None)',
            wgp,
        )
        self.assertIn('"h3_source_audio_mode"', handler)
        self.assertNotIn("T8Mars/comfyui-minimax-h3-audio-T8", main)


if __name__ == "__main__":
    unittest.main()
