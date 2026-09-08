"""H3 checkpoint and attention-engine compatibility, CPU/static only."""
from __future__ import annotations

import ast
import copy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from tests.test_minimax_h3 import _w4a8_admission_namespace


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH = APP / "launch.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
BASE = "minimax_h3"
REF = "minimax_h3_ref2va"
W4 = "minimax_h3_w4a8_fl2va"
PINK = "minimax_h3_pinkcherry_fl2va"
FL2VA = {BASE, W4, PINK}
STUDIO = FL2VA | {REF}
SAGE_ERROR = (
    "SageAttention2++ requires Base H3 for every segment. Choose Dense SDPA "
    "for this setup."
)


def _load_helpers() -> dict:
    source = LAUNCH.read_text(encoding="utf-8")
    names = {
        "_trusted_h3_prepared_plan",
        "_require_h3_acceleration_available",
        "_apply_h3_adaptive_checkpoint",
        "_h3_preferred_fl2va_model",
        "_h3_effective_model_types",
    }
    nodes = [
        node for node in ast.parse(source, filename=str(LAUNCH)).body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "_H3_BASE_FL2VA_MODEL": BASE,
        "_H3_REF2VA_MODEL": REF,
        "_H3_W4A8_FL2VA_MODEL": W4,
        "_H3_FL2VA_MODELS": FL2VA,
        "_H3_LONG_STUDIO_MODELS": STUDIO,
        "wgp": types.SimpleNamespace(
            get_model_def=lambda _model: {"frames_maximum": 345},
        ),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(LAUNCH), "exec"), namespace)
    return namespace


def _acceleration_module(*, available: bool, reason: str = "not installed"):
    module = types.ModuleType("services.h3_acceleration")
    calls: list[bool] = []

    def status(*, probe_kernel: bool):
        calls.append(probe_kernel)
        return {"w4a8": {"available": available, "reason": reason}}

    module.get_h3_acceleration_status = status
    return module, calls


class H3AttentionRoutingTests(unittest.TestCase):
    def setUp(self):
        helpers = _load_helpers()
        self.apply_checkpoint = helpers["_apply_h3_adaptive_checkpoint"]
        self.require_acceleration = helpers["_require_h3_acceleration_available"]

    def assert_sage_rejected(self, body: dict, plan: dict | None = None, **kwargs):
        original_custom = copy.deepcopy(body.get("custom_settings"))
        with self.assertRaises(ValueError) as caught:
            self.require_acceleration(body, plan, **kwargs)
        self.assertEqual(str(caught.exception), SAGE_ERROR)
        self.assertEqual(body.get("custom_settings"), original_custom)

    def test_base_sage2_is_accepted_without_rewriting_or_w4_probe(self):
        body = {
            "model_type": BASE,
            "custom_settings": {"h3_attention_engine": "sage2", "h3_sol_tau": 1.25},
        }
        module, calls = _acceleration_module(available=False)
        with patch.dict(sys.modules, {"services.h3_acceleration": module}):
            self.require_acceleration(body)
        self.assertEqual(body["custom_settings"], {
            "h3_attention_engine": "sage2",
            "h3_sol_tau": 1.25,
        })
        self.assertEqual(calls, [])

    def test_semantic_ref_route_preserves_requested_sage2_then_rejects(self):
        custom = {"h3_attention_engine": "sage2", "h3_sol_tau": 1.0}
        body = {
            "model_type": BASE,
            "image_refs": ["character.png"],
            "video_length": 81,
            "custom_settings": custom,
        }
        self.assertEqual(self.apply_checkpoint(body), REF)
        self.assertIs(body["custom_settings"], custom)
        self.assert_sage_rejected(body)

    def test_explicit_and_adaptive_non_base_routes_reject_sage2(self):
        cases = (
            {"model_type": REF, "h3_adaptive_conditioning": False},
            {"model_type": W4, "h3_adaptive_conditioning": False},
            {"model_type": PINK, "h3_adaptive_conditioning": False},
            {"model_type": BASE, "h3_adaptive_fl2va_model": W4},
            {"model_type": BASE, "h3_adaptive_fl2va_model": PINK},
        )
        for fields in cases:
            with self.subTest(fields=fields):
                body = {
                    **fields,
                    "video_length": 81,
                    "custom_settings": {"h3_attention_engine": "sage2"},
                }
                self.apply_checkpoint(body)
                self.assert_sage_rejected(body)

    def test_mixed_long_plan_checks_every_effective_segment(self):
        body = {
            "model_type": BASE,
            "custom_settings": {"h3_attention_engine": "sage2"},
        }
        plan = {
            "segment_models": [
                {"model_type": BASE},
                {"model_type": REF},
                {"model_type": BASE},
            ],
        }
        self.assert_sage_rejected(body, plan)

    def test_sdpa_and_sol_routes_remain_unchanged(self):
        for engine in ("sdpa", "sol_attn"):
            with self.subTest(engine=engine):
                body = {
                    "model_type": BASE,
                    "image_refs": ["character.png"],
                    "video_length": 81,
                    "custom_settings": {"h3_attention_engine": engine, "h3_sol_tau": 0.75},
                }
                expected = copy.deepcopy(body["custom_settings"])
                self.assertEqual(self.apply_checkpoint(body), REF)
                self.require_acceleration(body)
                self.assertEqual(body["custom_settings"], expected)

    def test_w4a8_availability_gate_is_preserved_for_compatible_attention(self):
        body = {
            "model_type": W4,
            "custom_settings": {"h3_attention_engine": "sdpa"},
        }
        unavailable, calls = _acceleration_module(available=False, reason="kernel missing")
        with patch.dict(sys.modules, {"services.h3_acceleration": unavailable}):
            with self.assertRaisesRegex(
                ValueError, "Kijai W4A8 FL2VA is unavailable: kernel missing",
            ):
                self.require_acceleration(body)
        self.assertEqual(calls, [False])

        available, calls = _acceleration_module(available=True)
        with patch.dict(sys.modules, {"services.h3_acceleration": available}):
            self.require_acceleration(body)
        self.assertEqual(calls, [False])

    def test_embedded_plan_requires_the_existing_explicit_trust_path(self):
        plan = {"segment_models": [{"model_type": REF}]}
        body = {
            "model_type": BASE,
            "custom_settings": {"h3_attention_engine": "sage2"},
            "_h3_longform": plan,
        }
        self.require_acceleration(body)
        self.assert_sage_rejected(body, allow_server_prepared=True)

    def test_durable_preparation_rejects_known_sage_route_before_enhancement(self):
        namespace, *_rest = _w4a8_admission_namespace()
        enhancer_calls: list[dict] = []
        progress_calls: list[dict] = []
        job = {
            "id": "ref-sage-preparation",
            "params": {
                "model_type": REF,
                "prompt": "Enhance only after routing validation.",
                "custom_settings": {"h3_attention_engine": "sage2"},
            },
            "status": "preparing",
            "execution_attempt": 1,
            "out_dir": "/tmp/project",
        }

        async def enhance(request):
            enhancer_calls.append(request)
            return {"enhanced": "must not run"}

        def fail_preparation(target, **updates):
            target.update(updates)
            target["status"] = "failed"
            return True

        namespace.update({
            "_jobs": {job["id"]: job},
            "_H3_BASE_FL2VA_MODEL": BASE,
            "_H3_REF2VA_MODEL": REF,
            "_H3_W4A8_FL2VA_MODEL": W4,
            "_H3_LONG_STUDIO_MODELS": STUDIO,
            "_require_h3_legal_execution": lambda _models: None,
            "_h3_job_model_types": lambda _job: [REF],
            "llm_enhance_prompt": enhance,
            "update_preparation_job": (
                lambda _job, **updates: progress_calls.append(updates) or True
            ),
            "fail_preparation": fail_preparation,
            "QueueRecoveryRuntimeError": RuntimeError,
        })
        services = types.ModuleType("services")
        services.__path__ = []
        planning_failure = types.ModuleType("services.planning_failure")
        planning_failure.planning_failure_event = (
            lambda _error, *, phase: ("invalid_request", f"phase={phase}")
        )
        planning_failure.public_planning_failure_message = (
            lambda _error, *, fallback: fallback
        )
        planning_failure.remove_exact_request_manifest = (
            lambda *_args, **_kwargs: True
        )
        with patch.dict(sys.modules, {
            "services": services,
            "services.planning_failure": planning_failure,
        }):
            namespace["_run_generation_preparation"](
                job["id"],
                types.SimpleNamespace(state=types.SimpleNamespace()),
                enhance=True,
            )

        self.assertEqual(enhancer_calls, [])
        self.assertEqual(progress_calls, [])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "Prompt enhancement failed")

    def test_worker_rejects_mixed_sage_plan_before_cuda_benchmark_setup(self):
        namespace, _acceleration, *_rest = _w4a8_admission_namespace()
        cuda_calls: list[str] = []

        class NativeSlot:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return True

            def __exit__(self, *_args):
                return None

        plan = {
            "segment_models": [
                {"model_type": BASE},
                {"model_type": REF},
            ],
            "clip_frames": [81, 81],
        }
        custom = {"h3_attention_engine": "sage2"}
        job = {
            "id": "mixed-sage-worker",
            "params": {
                "model_type": BASE,
                "custom_settings": custom,
                "_h3_longform": plan,
            },
            "status": "queued",
            "out_dir": "",
        }
        namespace.update({
            "_jobs": {job["id"]: job},
            "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign",
            "_sample_campaign_execution_attempt": types.SimpleNamespace(
                get=lambda: None,
            ),
            "_sample_campaign_transition_lock": NativeSlot(),
            "_lifecycle_update_job": lambda *_args, **_kwargs: True,
            "_lifecycle_record_job_outputs": lambda *_args, **_kwargs: [],
            "_lifecycle_finish_job": lambda *_args, **_kwargs: True,
            "_lifecycle_register_abort_state": lambda *_args, **_kwargs: True,
            "_WgpNativeGpuExecutionSlot": NativeSlot,
            "_generation_native_gpu_cancel_checkpoint": lambda _job: None,
            "_wgp_native_gpu_slot_state": types.SimpleNamespace(current_slot=None),
            "_credit_admission_evaluations": {},
            "_require_job_runtime_model_admission": lambda _job: None,
            "_H3_BASE_FL2VA_MODEL": BASE,
            "_H3_REF2VA_MODEL": REF,
            "_H3_W4A8_FL2VA_MODEL": W4,
            "_H3_FL2VA_MODELS": FL2VA,
            "_H3_LONG_STUDIO_MODELS": STUDIO,
            "_require_h3_legal_execution": lambda _models: None,
            "_validate_h3_segment_plan": lambda *_args, **_kwargs: None,
            "_validate_h3_lora_request": lambda *_args, **_kwargs: None,
            "_h3_lora_asset_selections": lambda *_args, **_kwargs: [],
            "_validate_h3_lightx2v_recovery_identity": (
                lambda *_args, **_kwargs: None
            ),
            "_require_h3_generation_terms": lambda *_args, **_kwargs: None,
            "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(
                is_available=(
                    lambda: cuda_calls.append("is_available") or True
                ),
                reset_peak_memory_stats=(
                    lambda: cuda_calls.append("reset")
                ),
            )),
        })

        self.assertFalse(namespace["_run_generation"](job["id"]))
        self.assertEqual(cuda_calls, [])
        self.assertIs(job["params"]["custom_settings"], custom)

    def test_planning_preparation_and_worker_call_the_shared_guard(self):
        tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {
                "_plan_generation_submission",
                "_run_generation_preparation",
                "_run_generation",
            }
        }
        planning_calls = [
            node for node in ast.walk(functions["_plan_generation_submission"])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_require_h3_acceleration_available"
        ]
        self.assertEqual(len(planning_calls), 1)
        self.assertEqual([arg.id for arg in planning_calls[0].args], ["body", "plan"])

        preparation_calls = [
            node for node in ast.walk(functions["_run_generation_preparation"])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_require_h3_acceleration_available"
        ]
        self.assertEqual(len(preparation_calls), 1)
        self.assertEqual(
            [arg.id for arg in preparation_calls[0].args], ["prepared_params"],
        )

        worker_calls = [
            node for node in ast.walk(functions["_run_generation"])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_require_h3_acceleration_available"
        ]
        self.assertEqual(len(worker_calls), 1)
        self.assertEqual(
            [arg.id for arg in worker_calls[0].args],
            ["raw_params", "trusted_h3_plan"],
        )
        self.assertEqual(
            [
                (item.arg, isinstance(item.value, ast.Constant) and item.value.value)
                for item in worker_calls[0].keywords
            ],
            [("allow_server_prepared", True)],
        )


if __name__ == "__main__":
    unittest.main()
