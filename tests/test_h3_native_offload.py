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


class NativeFloorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wgp_tree = ast.parse((ROOT / "app/wgp.py").read_text())
        cls.launch_tree = ast.parse((ROOT / "app/launch.py").read_text())

    def runtime_namespace(self):
        sys.path.insert(0, str(ROOT / "app"))
        try:
            from services.job_lifecycle import make_residency_key
        finally:
            sys.path.pop(0)
        names = {"compute_profile", "get_requested_residency_identity", "_model_load_configuration",
                 "_model_load_configuration_matches", "_release_for_model_reprofile"}
        namespace = dict(
            math=__import__("math"), get_default_profile=lambda output: 4,
            args=types.SimpleNamespace(vram_safety_coefficient=0.8, gpu="synthetic"),
            server_config={}, vae_config=None, transformer_quantization="int8",
            transformer_dtype_policy="bf16", text_encoder_quantization="int8", attention_mode="sdpa",
            get_base_model_type=lambda model: "minimax_h3",
            _model_load_environment_signature=lambda model, profile: {"profile": profile},
            make_residency_key=make_residency_key,
        )
        nodes = [node for node in self.wgp_tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "wgp-floor-contract", "exec"), namespace)
        return namespace

    def test_runtime_key_and_reprofile_follow_effective_floor(self):
        ns = self.runtime_namespace()
        key = ns['get_requested_residency_identity']
        small = key('minimax_h3', override_profile=4, resolution='960x544')[0]
        large = key('minimax_h3', override_profile=4, resolution='1344x768')[0]
        self.assertNotEqual(small, large)
        self.assertEqual(large, key('minimax_h3', override_profile=3, resolution='768x1344')[0])
        self.assertEqual(large, key('minimax_h3', override_profile=5, resolution='960x544')[0])
        config = ns['_model_load_configuration']
        old = config(0.8, 4.5, 'video', None, None)
        new = config(0.8, 5.0, 'video', None, None)
        releases = []
        self.assertTrue(ns['_release_for_model_reprofile'](object(), old, new, lambda: releases.append(1)))
        self.assertEqual(releases, [1])
        self.assertFalse(ns['_release_for_model_reprofile'](object(), new, new, lambda: releases.append(1)))

    def test_loader_and_generation_policy_blocks_receive_the_same_resolution(self):
        for name in ('load_models', '_generate_video_impl'):
            node = next(n for n in self.wgp_tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            start = next(i for i, n in enumerate(node.body) if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == 'profile' for t in n.targets)
                         and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                         and n.value.func.id == 'compute_profile')
            end = next(i for i in range(start + 1, len(node.body)) if isinstance(node.body[i], ast.If))
            policy = node.body[start:end + 1]
            for resolution, expected in [('960x544', 4.5), ('1344x768', 5.0), ('768x1344', 5.0)]:
                ns = self.runtime_namespace()
                ns.update(override_profile=4, output_type='video', model_type='minimax_h3',
                          base_model_type='minimax_h3', resolution=resolution)
                exec(compile(ast.Module(body=policy, type_ignores=[]), 'runtime-floor-block', 'exec'), ns)
                self.assertEqual(ns['profile'], expected)
            target = 'get_requested_residency_identity' if name == 'load_models' else 'load_models'
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == target]
            self.assertTrue(calls)
            for call in calls:
                self.assertTrue(any(k.arg == 'resolution' and isinstance(k.value, ast.Name)
                                    and k.value.id == 'resolution' for k in call.keywords))

    def test_sealed_integer_request_is_distinct_from_expected_runtime_profile(self):
        from services.h3_offload_plan import seal_h3_offload_plan, validate_h3_offload_plan
        ns = self.runtime_namespace()
        ns['wgp'] = types.SimpleNamespace(compute_profile=ns['compute_profile'], get_output_type_for_model=lambda *args: 'video')
        names = {'_h3_requested_offload_profile', '_h3_effective_offload_profile'}
        exec(compile(ast.Module(body=[n for n in self.launch_tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), 'profile-domains', 'exec'), ns)
        for resolution, effective in [('960x544', 4.5), ('1344x768', 5.0), ('768x1344', 5.0)]:
            params = dict(model_type='minimax_h3', resolution=resolution, video_length=124,
                          num_inference_steps=20, override_profile=3)
            requested = ns['_h3_requested_offload_profile'](params)
            self.assertEqual(requested, 3)
            plan = seal_h3_offload_plan(params, effective_profile=requested)
            before = __import__('json').dumps(plan, sort_keys=True)
            self.assertEqual(ns['_h3_effective_offload_profile'](params), effective)
            self.assertEqual(validate_h3_offload_plan(plan)['profile'], 3)
            self.assertEqual(__import__('json').dumps(plan, sort_keys=True), before)

    def test_queue_stamp_matches_first_effective_model_and_resolution(self):
        ns = self.runtime_namespace()
        from services.job_lifecycle import stamp_job_residency
        fake = types.SimpleNamespace(
            get_requested_residency_identity=ns['get_requested_residency_identity'],
            get_output_type_for_model=lambda *args: 'video', get_model_def=lambda model: {}, server_config={})
        ns.update(wgp=fake, _H3_LONG_STUDIO_MODELS={'minimax_h3', 'minimax_h3_ref2va'},
                  _apply_h3_adaptive_checkpoint=lambda candidate: candidate.update(model_type='minimax_h3_ref2va') if candidate.get('synthetic_adaptive') else None,
                  _prepare_h3_long_studio_request=lambda candidate: None,
                  _apply_per_job_coefficient=lambda *args, **kwargs: {'effective_coef': 0.8},
                  _requested_vae_residency_setting=lambda *args: None,
                  stamp_job_residency=stamp_job_residency)
        names = {'_first_requested_generation_model', '_residency_request_params', '_stamp_requested_generation_residency_locked'}
        exec(compile(ast.Module(body=[n for n in self.launch_tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), 'queue-profile-floor', 'exec'), ns)
        for extra, expected in [({}, 'minimax_h3'), ({'synthetic_adaptive': True}, 'minimax_h3_ref2va'),
                                ({'_h3_longform': {'segment_models': [{'model_type': 'minimax_h3_ref2va'}]}}, 'minimax_h3_ref2va')]:
            params = dict(model_type='minimax_h3', resolution='1344x768', override_profile=4, **extra)
            job = {'status': 'queued', 'params': params}
            self.assertTrue(ns['_stamp_requested_generation_residency_locked'](job))
            key = fake.get_requested_residency_identity(expected, override_profile=4, resolution='1344x768')[0]
            self.assertEqual(job['residency_base_key'], key)
            self.assertEqual(params['model_type'], 'minimax_h3')


if __name__ == "__main__":
    unittest.main()
