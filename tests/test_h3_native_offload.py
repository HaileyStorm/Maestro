"""Offload observations must not alias a different sealed profile request."""
from __future__ import annotations

import ast
import gc
import inspect
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeOffloadEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "app" / "launch.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_h3_calibrated_peak_choice")
        cls.code = compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec")
        cls.policy_version = next(ast.literal_eval(node.value) for node in tree.body
                                  if isinstance(node, ast.Assign) and any(
                                      isinstance(target, ast.Name)
                                      and target.id == "_H3_PEAK_RECOVERY_POLICY_VERSION"
                                      for target in node.targets))

    def choose(self, profile, *, record_policy_version=None):
        gib = 1 << 30
        namespace = {
            "sys": types.SimpleNamespace(modules={}),
            "torch": types.SimpleNamespace(
                __version__="synthetic", version=types.SimpleNamespace(cuda=None),
                cuda=types.SimpleNamespace(is_available=lambda: False),
            ),
            "wgp": types.SimpleNamespace(_host_memory_snapshot=lambda: (48 * gib, 64 * gib)),
            "QueueRecoveryRuntimeError": ValueError,
            "_H3_PEAK_RECOVERY_POLICY_VERSION": self.policy_version,
            "_H3_PEAK_RECOVERY_HEADROOM_RATIO": 0.8,
            "_H3_PEAK_RECOVERY_HOST_HEADROOM_RATIO": 0.25,
            "_h3_effective_offload_profile": lambda params: 4,
            "_h3_peak_recovery_identity": lambda params, **kwargs: {
                "model_type": "minimax_h3", "width": 1344, "height": 768,
                "sampling_steps": 20, "attention_engine": "sdpa",
            },
            "_h3_allocation_scenario": lambda params, **kwargs: params,
            "_get_h3_allocation_ledger": lambda: types.SimpleNamespace(
                snapshot=lambda scenario: {"revision": 1, "digest": "synthetic"},
            ),
        }
        exec(self.code, namespace)
        record = {
            "output_valid": True, "peak_gpu_memory_bytes": gib,
            "spec": {
                "task": {"offload_profile": profile, "frame_count": 128,
                         "width": 1344, "height": 768, "sampling_steps": 20,
                         "recovery_policy_version": (self.policy_version if record_policy_version is None
                                                     else record_policy_version)},
                "model": {"id": "minimax_h3"}, "engine": {"id": "sdpa"},
                "hardware": {"gpu": "cpu"},
                "runtime": {"torch": "synthetic", "cuda": "", "triton": "unknown"},
            },
        }
        return namespace["_h3_calibrated_peak_choice"](
            {"video_length": 256}, [record],
            allocator={"total_bytes": 32 * gib, "free_bytes": 28 * gib},
        )

    def test_fractional_and_malformed_observations_cannot_authorize_integer_recovery(self):
        for profile in (4.5, "4.5", 5.7, float("nan"), float("inf"), True, None):
            with self.subTest(profile=profile):
                self.assertIsNone(self.choose(profile))

    def test_requested_profile_era_records_are_not_loaded_profile_authority(self):
        self.assertGreaterEqual(self.policy_version, 2)
        self.assertIsNone(self.choose(5, record_policy_version=1))
        self.assertIsNotNone(self.choose(5, record_policy_version=self.policy_version))

    def test_matching_integer_observations_retain_recovery_authority(self):
        for profile in (4, 5, "4"):
            with self.subTest(profile=profile):
                selected = self.choose(profile)
                self.assertEqual(selected["offload_profile"], int(profile))
                self.assertEqual(selected["frame_ceiling"], 128)


class OffloadWrapperBindingTests(unittest.TestCase):
    def test_retry_updates_positional_keyword_and_mixed_calls_without_duplicate_arguments(self):
        app = ROOT / "app"
        sys.path.insert(0, str(app))
        try:
            from services.h3_oom_relief import H3OomReliefRetry
        finally:
            sys.path.pop(0)
        path = app / "wgp.py"
        node = next(node for node in ast.parse(path.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "generate_video")
        observer_helper = next(item for item in ast.parse(path.read_text()).body
                               if isinstance(item, ast.FunctionDef)
                               and item.name == "_notify_h3_profile_observer")
        code = compile(ast.Module(body=[observer_helper, node], type_ignores=[]), str(path), "exec")
        for style in ("positional", "keyword", "mixed"):
            with self.subTest(style=style):
                calls = []

                def implementation(task, model_type, resolution, override_profile=-1,
                                   num_inference_steps=20):
                    calls.append((resolution, override_profile, num_inference_steps))
                    if len(calls) == 1:
                        raise H3OomReliefRetry({
                            "resolution": "864x480", "override_profile": 5.0,
                            "num_inference_steps": 18,
                        })
                    return False

                namespace = {
                    "inspect": inspect, "gc": gc, "_generate_video_impl": implementation,
                    "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
                    "get_default_profile": lambda output: 4,
                    "get_output_type_for_model": lambda model: "video",
                }
                exec(code, namespace)
                task = {"params": {}}
                if style == "positional":
                    result = namespace["generate_video"](task, "minimax_h3", "960x544", 4, 20)
                elif style == "keyword":
                    result = namespace["generate_video"](
                        task=task, model_type="minimax_h3", resolution="960x544",
                        override_profile=4, num_inference_steps=20,
                    )
                else:
                    result = namespace["generate_video"](
                        task, "minimax_h3", "960x544", override_profile=4,
                        num_inference_steps=20,
                    )
                self.assertFalse(result)
                self.assertEqual(calls, [("960x544", 4.5, 20), ("864x480", 5.0, 18)])
                self.assertEqual(task["params"]["override_profile"], 5.0)
                self.assertEqual(task["params"]["resolution"], "864x480")
                self.assertEqual(task["params"]["num_inference_steps"], 18)


if __name__ == "__main__":
    unittest.main()
