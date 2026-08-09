from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_lightx2v  # noqa: E402
from services.h3_lightx2v import (  # noqa: E402
    H3_LIGHTX2V_EFFECTIVE_SCALE,
    H3_LIGHTX2V_FILENAME,
    H3_LIGHTX2V_REVISION,
    H3_LIGHTX2V_SHA256,
    H3_LIGHTX2V_SIZE,
    H3_LIGHTX2V_SOURCE_COMMIT,
    H3_LIGHTX2V_TENSOR_SHAPES,
    H3LightX2VAssets,
    H3LightX2VCompatibilityError,
    call_with_lightx2v_cleanup,
    guard_lightx2v_lora_load,
    lightx2v_runtime_identity,
    lightx2v_scheduler_grid_points,
    validate_lightx2v_runtime_identity,
    validate_lightx2v_request,
)
from models.minimax_h3.scheduler import MiniMaxH3Scheduler  # noqa: E402
from services.h3_profiles import profile_settings, build_profile_options  # noqa: E402
from services.h3_benchmark import (  # noqa: E402
    H3BenchmarkError,
    normalize_estimate_context,
)


def valid(**overrides):
    value = {
        "selected_model_type": "minimax_h3",
        "model_def": {},
        "custom_settings": {
            "h3_attention_engine": "sdpa",
            "h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1",
        },
        "authored_steps": 4,
        "semantic_references": False,
        "multisegment": False,
        "activated_loras": [],
        "loras_multipliers": "",
        "skip_steps_cache_type": 0,
        "native_boundary": False,
    }
    value.update(overrides)
    return value


class H3LightX2VTests(unittest.TestCase):
    @staticmethod
    def _launch_functions(*names):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in set(names)
        ]
        namespace = {
            "_H3_BASE_FL2VA_MODEL": "minimax_h3",
            "wgp": type(
                "WGP",
                (),
                {"get_model_def": staticmethod(lambda _name: {})},
            )(),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "launch.py", "exec"), namespace)
        return namespace

    def test_release_identity_and_scheduler_are_exact(self):
        self.assertEqual(H3_LIGHTX2V_REVISION, "b65e359c0d128b3c5e08e0f5bf2791b794378588")
        self.assertEqual(H3_LIGHTX2V_FILENAME, "minimax_h3_fl2v_turbo_4step_v0.1.safetensors")
        self.assertEqual(H3_LIGHTX2V_SIZE, 1_383_677_888)
        self.assertEqual(H3_LIGHTX2V_SHA256, "5ff4a12c8b4599fec716e1b15a45e504e0d1129111896bdcde5ac4a15e395b29")
        self.assertEqual(H3_LIGHTX2V_EFFECTIVE_SCALE, 0.0625)
        self.assertEqual(lightx2v_scheduler_grid_points(4), 5)
        self.assertEqual(
            H3_LIGHTX2V_SOURCE_COMMIT,
            "82423dcbcf4d99fd5a31086a7633521438443c8f",
        )

    def test_complete_header_contract_is_exact(self):
        self.assertEqual(len(H3_LIGHTX2V_TENSOR_SHAPES), 624)
        self.assertNotIn(
            "transformer_blocks.0.adaln_proj.lora_A.default.weight",
            H3_LIGHTX2V_TENSOR_SHAPES,
        )
        self.assertEqual(
            H3_LIGHTX2V_TENSOR_SHAPES[
                "transformer_blocks.49.ff.net.2.lora_B.default.weight"
            ],
            (5376, 128),
        )
        # Independent proof captured from the immutable upstream header on
        # 2026-08-08. It hashes sorted (key, dtype, shape) rows rather than
        # deriving an expectation from the implementation's block loops.
        proof = [
            (name, "BF16", list(shape))
            for name, shape in sorted(H3_LIGHTX2V_TENSOR_SHAPES.items())
        ]
        proof_digest = hashlib.sha256(
            json.dumps(proof, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        self.assertEqual(
            proof_digest,
            "e1af37efafad07f45c7dddf79f97e501a91a34f1c63c22a5cbb73334fea4c4a2",
        )
        header = {"__metadata__": dict(h3_lightx2v._PINNED_METADATA)}
        cursor = 0
        for name, shape in H3_LIGHTX2V_TENSOR_SHAPES.items():
            length = shape[0] * shape[1] * 2
            header[name] = {
                "dtype": "BF16",
                "shape": list(shape),
                "data_offsets": [cursor, cursor + length],
            }
            cursor += length
        h3_lightx2v._validate_tensor_header_contract(header, cursor)
        broken = dict(header)
        broken.pop(next(iter(H3_LIGHTX2V_TENSOR_SHAPES)))
        with self.assertRaises(H3LightX2VCompatibilityError):
            h3_lightx2v._validate_tensor_header_contract(broken, cursor)

    def test_four_model_evaluations_use_five_scheduler_grid_points(self):
        video = MiniMaxH3Scheduler(shift=3.0)
        audio = MiniMaxH3Scheduler(shift=3.0)
        points = lightx2v_scheduler_grid_points(4)
        video.set_timesteps(points, device="cpu")
        audio.set_timesteps(points, device="cpu")
        self.assertEqual(points, 5)
        self.assertEqual(len(video.timesteps), 4)
        self.assertEqual(len(audio.timesteps), 4)
        self.assertEqual(video.timesteps.tolist(), audio.timesteps.tolist())

    def test_corrupt_same_release_is_quarantined_and_atomically_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "releases" / h3_lightx2v._release_name()
            release.mkdir(parents=True)
            target = release / H3_LIGHTX2V_FILENAME
            target.write_bytes(b"corrupt")
            source = root / "source.safetensors"
            source.write_bytes(b"validated")

            def validate(path):
                candidate = Path(path)
                if candidate.read_bytes() != b"validated":
                    raise H3LightX2VCompatibilityError("corrupt")
                return candidate

            assets = H3LightX2VAssets(
                "h3_lightx2v_fl2v_4_v1", release, target,
            )
            with (
                patch.object(
                    h3_lightx2v, "validate_lightx2v_lora",
                    side_effect=validate,
                ),
                patch.object(
                    h3_lightx2v, "resolve_lightx2v_assets",
                    return_value=assets,
                ),
            ):
                h3_lightx2v.publish_lightx2v_asset(source, root=root)
            self.assertEqual(target.read_bytes(), b"validated")
            quarantined = list((root / "quarantine").glob("*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(
                (quarantined[0] / H3_LIGHTX2V_FILENAME).read_bytes(),
                b"corrupt",
            )

    def test_partial_managed_load_always_runs_cleanup(self):
        events = []

        def prepare():
            events.append("partial-load")
            raise RuntimeError("load failed")

        def cleanup():
            events.append("cleanup")

        with self.assertRaisesRegex(RuntimeError, "load failed"):
            guard_lightx2v_lora_load(prepare, cleanup)
        self.assertEqual(events, ["partial-load", "cleanup"])

    def test_lightx_is_unloaded_before_any_post_decode_failure(self):
        class PostDecodeStageError(RuntimeError):
            pass

        events = []
        try:
            sample = call_with_lightx2v_cleanup(
                True,
                lambda: events.append("unload"),
                lambda: events.append("generate") or "sample",
            )
            self.assertEqual(sample, "sample")
            events.append("post-decode")
            raise PostDecodeStageError("concat failed")
        except PostDecodeStageError:
            pass
        self.assertEqual(events, ["generate", "unload", "post-decode"])

        events.clear()
        call_with_lightx2v_cleanup(
            False,
            lambda: events.append("unload"),
            lambda: events.append("native"),
        )
        self.assertEqual(events, ["native"])

    def test_recovery_identity_pins_asset_scale_and_scheduler(self):
        identity = lightx2v_runtime_identity()
        self.assertEqual(identity["profile_id"], "h3_lightx2v_fl2v_4_v1")
        self.assertEqual(identity["effective_scale"], 0.0625)
        self.assertEqual(identity["authored_evaluations"], 4)
        self.assertEqual(identity["scheduler_grid_points"], 5)
        self.assertEqual(validate_lightx2v_runtime_identity(identity), identity)
        with self.assertRaises(H3LightX2VCompatibilityError):
            validate_lightx2v_runtime_identity({**identity, "effective_scale": 1})

        namespace = self._launch_functions(
            "_stamp_h3_lightx2v_recovery_identity",
            "_validate_h3_lightx2v_recovery_identity",
        )
        params = {"custom_settings": valid()["custom_settings"]}
        namespace["_stamp_h3_lightx2v_recovery_identity"](params)
        self.assertEqual(params["_h3_lightx2v_identity"], identity)
        namespace["_validate_h3_lightx2v_recovery_identity"](params)
        params["_h3_lightx2v_identity"]["scheduler_grid_points"] = 4
        with self.assertRaises(H3LightX2VCompatibilityError):
            namespace["_validate_h3_lightx2v_recovery_identity"](params)

    def test_fail_closed_initial_matrix(self):
        self.assertTrue(validate_lightx2v_request(**valid()))
        cases = (
            {"selected_model_type": "minimax_h3_ref2va"},
            {"semantic_references": True},
            {"multisegment": True},
            {"authored_steps": 8},
            {"activated_loras": ["user.safetensors"]},
            {"skip_steps_cache_type": "tea"},
            {"native_boundary": True},
            {"custom_settings": {"h3_attention_engine": "sage2", "h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1"}},
            {"custom_settings": {"h3_attention_engine": "sdpa", "h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1", "h3_turbo_profile": "h3_turbo_v4"}},
            {"custom_settings": {"h3_attention_engine": "sdpa", "h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1", "h3_spectrum_profile": "spectrum_h3_v1"}},
            {"model_def": {"h3_w4a8": True}},
            {"model_def": {"h3_convrot": True}},
            {"loras_multipliers": "0.5"},
            {"custom_settings": {"h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1"}},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(H3LightX2VCompatibilityError):
                validate_lightx2v_request(**valid(**overrides))

    def test_empty_profile_is_native_noop_and_bad_step_types_fail_closed(self):
        request = valid()
        request["custom_settings"] = {"h3_lightx2v_profile": ""}
        self.assertFalse(validate_lightx2v_request(**request))
        for value in (True, 4.5, "bad"):
            with self.subTest(value=value), self.assertRaises(
                H3LightX2VCompatibilityError
            ):
                lightx2v_scheduler_grid_points(value)

    def test_estimator_executes_the_runtime_matrix(self):
        validate = self._launch_functions(
            "_validate_h3_lightx2v_estimate_context",
        )["_validate_h3_lightx2v_estimate_context"]
        context = {
            "model_type": "minimax_h3",
            "num_inference_steps": 4,
            "custom_settings": valid()["custom_settings"],
            "reference_shape": {},
            "activated_loras": [],
            "loras_multipliers": "",
            "tea_cache": 0,
        }
        validate(context)
        with self.assertRaises(H3LightX2VCompatibilityError):
            validate({**context, "_segment_contexts": [context, context]})
        with self.assertRaises(H3LightX2VCompatibilityError):
            validate({**context, "reference_shape": {"image_count": 1}})
        estimator_context = {
            **context,
            "duration_seconds": 5,
            "window_seconds": 15,
            "resolution": "608x352",
        }
        normalized = normalize_estimate_context(estimator_context)
        self.assertEqual(normalized["engine_id"], "sdpa")
        with self.assertRaisesRegex(H3BenchmarkError, "Dense SDPA"):
            normalize_estimate_context({
                **estimator_context,
                "custom_settings": {
                    "h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1",
                },
            })

    def test_profile_is_manual_and_does_not_replace_draft_or_fast(self):
        settings = profile_settings("minimax_h3", "lightx2v_experimental")
        self.assertEqual(settings["num_inference_steps"], 4)
        self.assertEqual(settings["custom_settings"], {
            "h3_attention_engine": "sdpa",
            "h3_lightx2v_profile": "h3_lightx2v_fl2v_4_v1",
        })
        options = build_profile_options(
            {"model_type": "minimax_h3", "reference_shape": {}},
            model_exists=lambda _model: True,
            model_downloaded=lambda _model: True,
            lightx2v_status={"downloaded": False},
            lightx2v_compatibility=lambda _settings: (True, None),
        )
        ids = [item["id"] for item in options]
        self.assertEqual(ids[:2], ["draft", "fast"])
        light = next(item for item in options if item["id"] == "lightx2v_experimental")
        self.assertTrue(light["available"])
        self.assertTrue(light["download_required"])
        self.assertIn("LightX2V adapter", light["download_components"])

    def test_runtime_and_ui_wiring_keep_the_adapter_managed(self):
        wgp = (APP / "wgp.py").read_text(encoding="utf-8")
        handler = (
            APP / "models/minimax_h3/minimax_h3_handler.py"
        ).read_text(encoding="utf-8")
        main = (
            APP / "models/minimax_h3/minimax_h3_main.py"
        ).read_text(encoding="utf-8")
        launch = (APP / "launch.py").read_text(encoding="utf-8")
        store = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
        types = (ROOT / "ui/src/types/index.ts").read_text(encoding="utf-8")
        self.assertIn("acquire_lightx2v_asset()", wgp)
        self.assertIn("str(H3_LIGHTX2V_EFFECTIVE_SCALE)", wgp)
        self.assertIn("lightx2v_assets_status()", wgp)
        self.assertIn("offload.load_loras_into_model(", wgp)
        self.assertIn("wan_model.finalize_loras()", wgp)
        self.assertIn("offload.unload_loras_from_model(trans_lora)", wgp)
        self.assertIn("_cleanup_generation_resources()", wgp)
        load_helper = wgp[
            wgp.index("def _load_generation_loras"):
            wgp.index("seed = None if seed == -1")
        ]
        self.assertIn(
            "guard_lightx2v_lora_load(prepare, _unload_generation_loras)",
            load_helper,
        )
        inference_start = wgp.index("def set_header_text")
        call_start = wgp.index(
            "samples = call_with_lightx2v_cleanup", inference_start,
        )
        inference_try = wgp[inference_start:call_start + 500]
        self.assertLess(
            inference_try.index("_load_generation_loras()"),
            inference_try.index("wan_model.generate"),
        )
        self.assertIn("samples = call_with_lightx2v_cleanup(", inference_try)
        self.assertIn("lightx2v_runtime_requested", inference_try)
        self.assertIn("_unload_generation_loras", inference_try)
        error_start = wgp.index("except Exception as e:", call_start)
        generation_error = wgp[
            error_start:wgp.index("return False", error_start)
        ]
        self.assertIn("_unload_generation_loras()", generation_error)
        self.assertNotIn(H3_LIGHTX2V_FILENAME, store)
        self.assertIn('"h3_lightx2v_profile"', handler)
        self.assertIn("lightx2v_scheduler_grid_points(sampling_steps)", main)
        self.assertIn("self.audio_scheduler.set_timesteps(scheduler_points", main)
        self.assertIn("zip(timesteps, audio_timesteps)", main)
        self.assertIn("_stamp_h3_lightx2v_recovery_identity", launch)
        self.assertIn("_validate_h3_lightx2v_recovery_identity", launch)
        recovery = launch[
            launch.index("def _queue_recovery_register_and_publish"):
            launch.index("def _queue_recovery_worker")
        ]
        self.assertLess(
            recovery.index("_stamp_h3_lightx2v_recovery_identity"),
            recovery.index("atomic_write_request_manifest"),
        )
        observation = launch[
            launch.index("def _record_h3_benchmark_observation"):
            launch.index("def _h3_estimate_context")
        ]
        self.assertIn("if spectrum_stats is not None else steps", observation)
        self.assertIn("if spectrum_stats is not None else 0", observation)
        self.assertIn("'h3_lightx2v_profile'", store)
        self.assertIn("'lightx2v_experimental'", types)


if __name__ == "__main__":
    unittest.main()
