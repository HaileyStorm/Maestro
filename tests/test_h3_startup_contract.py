"""CPU-only startup checks for the sealed H3 execution contract."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

from tests.test_minimax_h3 import _w4a8_admission_namespace


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH = APP / "launch.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

BASE = "minimax_h3"
SEPARATOR = "\n---CLIP_BOUNDARY---\n"


def _v1_single_shot_plan(prompt: str = "A quiet camera move.") -> dict:
    execution_slice = {
        "segment_index": 0,
        "physical_segment_index": 0,
        "start_frame": 0,
        "end_frame_exclusive": 5,
    }
    return {
        "clip_count": 1,
        "clip_frames": [5],
        "clip_published_frames": [5],
        "clip_trim_tail_frames": [0],
        "planned_frames": 5,
        "requested_frames": 5,
        "published_frames": 5,
        "final_trim_frames": 0,
        "shot_plan": {
            "semantic_physical_contract_version": 1,
            "clip_frames": [5],
            "clip_published_frames": [5],
            "clip_trim_tail_frames": [0],
            "semantic_shots": [{
                "source_index": 0,
                "semantic_prompt": prompt,
                "segment_indices": [0],
                "execution_slices": [copy.deepcopy(execution_slice)],
                "prompt_rewrite_for_physical_split": False,
            }],
            "shots": [{
                "index": 0,
                "prompt": prompt,
                "source_index": 0,
                "physical_segment_index": 0,
                "physical_segment_id": "shot-1:segment-1",
                "execution_slice": copy.deepcopy(execution_slice),
                "frames": 5,
                "published_frames": 5,
                "trim_tail_frames": 0,
                "execution_cursor_frame": 0,
                "predecessor_segment_index": None,
                "predecessor_physical_segment_id": None,
            }],
        },
    }


class H3StartupContractTests(unittest.TestCase):
    def test_worker_uses_shared_startup_validator_and_dispatch_parser(self):
        tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
        worker = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_generation"
        )
        calls = [
            node.func.id
            for node in ast.walk(worker)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertEqual(calls.count("validate_h3_execution_request"), 1)
        self.assertEqual(calls.count("multiclip_prompt_inputs"), 1)
        self.assertEqual(calls.count("_h3_execution_shots_for_dispatch"), 1)

    def test_multiclip_parser_precedence_shapes_and_nonmutation(self):
        from services.multiclip_inputs import multiclip_prompt_inputs

        cases = (
            (
                {
                    "prompt": f"fallback one{SEPARATOR}fallback two",
                    "per_clip_prompts": [" authored one ", "", 7],
                    "image_start": ["start-a.png", "start-b.png"],
                    "image_end": ["end.png"],
                },
                ([" authored one ", "", "7"],
                 ["start-a.png", "start-b.png"], ["end.png"]),
            ),
            (
                {
                    "prompt": f" first {SEPARATOR}  {SEPARATOR} second ",
                    "per_clip_prompts": [],
                    "image_start": "start.png",
                    "image_end": "",
                },
                (["first", "second"], ["start.png"], []),
            ),
            (
                {
                    "prompt": " first\n\n second \n",
                    "per_clip_prompts": "not-a-list",
                    "image_start": None,
                    "image_end": "end.png",
                },
                (["first", "second"], [], ["end.png"]),
            ),
            ({"prompt": "", "image_start": [], "image_end": []}, ([], [], [])),
        )
        for params, expected in cases:
            with self.subTest(params=params):
                original = copy.deepcopy(params)
                self.assertEqual(
                    multiclip_prompt_inputs(params, separator=SEPARATOR),
                    expected,
                )
                self.assertEqual(params, original)

    def test_execution_request_accepts_valid_contract_and_rejects_prompt_or_count(self):
        from services.h3_execution_contract import (
            resolve_h3_execution_shots,
            validate_h3_execution_request,
        )
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError

        prompt = "A quiet camera move."
        longform = _v1_single_shot_plan(prompt)
        original_longform = copy.deepcopy(longform)
        params = {
            "multi_prompts_gen_type": 3,
            "prompt": prompt,
            "per_clip_frames": [5],
            "video_length": 5,
        }
        original_params = copy.deepcopy(params)

        resolved = resolve_h3_execution_shots(longform, [prompt], 1)
        self.assertEqual(resolved, longform["shot_plan"]["shots"])
        self.assertIsNot(resolved, longform["shot_plan"]["shots"])
        validate_h3_execution_request(params, longform, separator=SEPARATOR)
        self.assertEqual(params, original_params)
        self.assertEqual(longform, original_longform)

        invalid_cases = []
        mismatched_params = copy.deepcopy(params)
        mismatched_params["per_clip_frames"] = [6]
        invalid_cases.append(("request frames", mismatched_params, longform))
        mismatched_plan = copy.deepcopy(longform)
        mismatched_plan["clip_trim_tail_frames"] = [1]
        invalid_cases.append(("publication geometry", params, mismatched_plan))
        mismatched_shot = copy.deepcopy(longform)
        mismatched_shot["shot_plan"]["shots"][0]["frames"] = 6
        invalid_cases.append(("shot frames", params, mismatched_shot))
        partial_publication = copy.deepcopy(longform)
        partial_publication.pop("clip_trim_tail_frames")
        invalid_cases.append((
            "partial publication geometry", params, partial_publication,
        ))
        wrong_mode = copy.deepcopy(params)
        wrong_mode["multi_prompts_gen_type"] = 2
        missing_clip_count = copy.deepcopy(longform)
        missing_clip_count.pop("clip_count")
        invalid_cases.extend((
            ("empty plan", params, {}),
            ("non-dict plan", params, []),
            ("wrong mode", wrong_mode, longform),
            ("missing clip count", params, missing_clip_count),
        ))
        for label, invalid_params, invalid_plan in invalid_cases:
            with self.subTest(label=label):
                params_before = copy.deepcopy(invalid_params)
                plan_before = copy.deepcopy(invalid_plan)
                with self.assertRaises(QueueRecoveryRuntimeError):
                    validate_h3_execution_request(
                        invalid_params, invalid_plan, separator=SEPARATOR,
                    )
                self.assertEqual(invalid_params, params_before)
                self.assertEqual(invalid_plan, plan_before)

        for invalid_prompt in (
            "A different executable prompt.",
            f"{prompt}{SEPARATOR}An unexpected second clip.",
        ):
            with self.subTest(invalid_prompt=invalid_prompt):
                invalid = {"multi_prompts_gen_type": 3, "prompt": invalid_prompt}
                with self.assertRaises(QueueRecoveryRuntimeError):
                    validate_h3_execution_request(
                        invalid, longform, separator=SEPARATOR,
                    )
        self.assertEqual(longform, original_longform)

    def test_execution_request_rejects_stale_two_clip_aggregate_totals(self):
        from services.h3_execution_contract import validate_h3_execution_request
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError

        prompts = ["First clip.", "Second clip."]
        params = {
            "multi_prompts_gen_type": 3,
            "prompt": SEPARATOR.join(prompts),
            "per_clip_prompts": prompts,
            "per_clip_frames": [121, 121],
            "video_length": 242,
        }
        stale_plan = {
            "clip_count": 2,
            "clip_frames": [121, 121],
            "clip_published_frames": [121, 104],
            "clip_trim_tail_frames": [0, 17],
            "planned_frames": 242,
            "requested_frames": 242,
            "published_frames": 242,
            "final_trim_frames": 17,
        }
        legacy_stale = copy.deepcopy(stale_plan)
        legacy_stale.pop("clip_published_frames")
        legacy_stale.pop("clip_trim_tail_frames")
        for label, candidate in (
            ("explicit arrays", stale_plan),
            ("legacy final trim", legacy_stale),
        ):
            with self.subTest(label=label):
                original_params = copy.deepcopy(params)
                original_plan = copy.deepcopy(candidate)
                with self.assertRaises(QueueRecoveryRuntimeError):
                    validate_h3_execution_request(
                        params, candidate, separator=SEPARATOR,
                    )
                self.assertEqual(params, original_params)
                self.assertEqual(candidate, original_plan)

    def test_execution_request_accepts_legacy_final_trim_geometry(self):
        from services.h3_execution_contract import validate_h3_execution_request

        prompt = "A legacy final-trim shot."
        longform = _v1_single_shot_plan(prompt)
        longform.pop("clip_published_frames")
        longform.pop("clip_trim_tail_frames")
        longform["final_trim_frames"] = 1
        longform["requested_frames"] = 4
        longform.pop("published_frames")
        longform["shot_plan"].pop("clip_published_frames")
        longform["shot_plan"].pop("clip_trim_tail_frames")
        execution_slice = {
            "segment_index": 0,
            "physical_segment_index": 0,
            "start_frame": 0,
            "end_frame_exclusive": 4,
        }
        semantic = longform["shot_plan"]["semantic_shots"][0]
        semantic["execution_slices"] = [copy.deepcopy(execution_slice)]
        shot = longform["shot_plan"]["shots"][0]
        shot.update({
            "execution_slice": execution_slice,
            "published_frames": 4,
            "trim_tail_frames": 1,
        })
        params = {
            "multi_prompts_gen_type": 3,
            "prompt": prompt,
            "per_clip_frames": [5],
            "video_length": 4,
        }
        original_plan = copy.deepcopy(longform)
        original_params = copy.deepcopy(params)

        validate_h3_execution_request(params, longform, separator=SEPARATOR)

        self.assertEqual(longform, original_plan)
        self.assertEqual(params, original_params)

    def test_worker_plan_mismatch_is_terminal_before_cuda_or_model_work(self):
        from services import h3_execution_contract
        from services.public_failure_copy import H3_PLAN_MISMATCH_DETAIL
        from services.queue_recovery_runtime import QueueRecoveryRuntimeError

        namespace, _acceleration, forbidden, *_rest = (
            _w4a8_admission_namespace()
        )
        cuda_calls: list[str] = []
        model_calls: list[str] = []
        validation_calls: list[tuple] = []
        finish_calls: list[dict] = []
        replan_calls: list[dict] = []
        private_error = (
            "CUDA out of memory while reading /private/authored.png "
            "provider-token=secret"
        )
        production_validate_request = (
            h3_execution_contract.validate_h3_execution_request
        )
        plan = {
            "clip_count": 1,
            "clip_frames": [81],
            "clip_published_frames": [81],
            "clip_trim_tail_frames": [0],
            "planned_frames": 81,
            "requested_frames": 81,
            "published_frames": 81,
            "final_trim_frames": 0,
            "segment_models": [{"model_type": BASE}],
        }
        authored_params = {
            "model_type": BASE,
            "multi_prompts_gen_type": 3,
            "prompt": "Keep this authored prompt byte-for-byte.",
            "per_clip_prompts": ["Keep this authored prompt byte-for-byte."],
            "per_clip_frames": [80],
            "video_length": 81,
            "image_start": ["authored-start.png"],
            "image_end": [],
            "_h3_longform": plan,
        }
        original_params = copy.deepcopy(authored_params)
        job = {
            "id": "startup-contract-mismatch",
            "params": authored_params,
            "status": "queued",
            "out_dir": "",
            "retry_count": 0,
            "retry_history": [],
        }

        class NativeSlot:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return True

            def __exit__(self, *_args):
                return None

        def validate_request(params, trusted_plan, *, separator):
            validation_calls.append((copy.deepcopy(params), trusted_plan, separator))
            try:
                production_validate_request(
                    params, trusted_plan, separator=separator,
                )
            except QueueRecoveryRuntimeError:
                pass
            else:
                raise AssertionError("invalid worker frames passed validation")
            raise QueueRecoveryRuntimeError(private_error)

        def lifecycle_update(target, **updates):
            target.update(updates)
            return True

        def lifecycle_finish(target, status, **updates):
            finish_calls.append({"status": status, **copy.deepcopy(updates)})
            target.update(updates)
            target["status"] = status
            return True

        def safe_failure_updates(details, _job):
            return {
                "failure_details": copy.deepcopy(details),
                "error": details["detail"],
                "message": details["detail"],
            }

        def prepare_plan(params):
            replan_calls.append(copy.deepcopy(params))
            return plan

        namespace.update({
            "_jobs": {job["id"]: job},
            "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign",
            "_sample_campaign_execution_attempt": types.SimpleNamespace(
                get=lambda: None,
            ),
            "_sample_campaign_transition_lock": NativeSlot(),
            "_lifecycle_update_job": lifecycle_update,
            "_lifecycle_record_job_outputs": lambda *_args, **_kwargs: [],
            "_lifecycle_finish_job": lifecycle_finish,
            "_lifecycle_register_abort_state": lambda *_args, **_kwargs: True,
            "_WgpNativeGpuExecutionSlot": NativeSlot,
            "_generation_native_gpu_cancel_checkpoint": lambda _job: None,
            "_wgp_native_gpu_slot_state": types.SimpleNamespace(current_slot=None),
            "_credit_admission_evaluations": {},
            "_require_job_runtime_model_admission": lambda _job: None,
            "_prepare_h3_long_studio_request": prepare_plan,
            "_validate_h3_segment_plan": lambda *_args, **_kwargs: None,
            "_validate_h3_lora_request": lambda *_args, **_kwargs: None,
            "_h3_lora_asset_selections": lambda *_args, **_kwargs: [],
            "_validate_h3_lightx2v_recovery_identity": (
                lambda *_args, **_kwargs: None
            ),
            "_require_h3_acceleration_available": lambda *_args, **_kwargs: None,
            "_require_h3_generation_terms": lambda *_args, **_kwargs: None,
            "_require_h3_legal_execution": lambda _models: None,
            "_safe_failure_updates": safe_failure_updates,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "_MULTI_CLIP_SEPARATOR": SEPARATOR,
            "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(
                is_available=lambda: cuda_calls.append("is_available") or True,
                reset_peak_memory_stats=lambda: cuda_calls.append("reset"),
            )),
            "wgp": types.SimpleNamespace(
                get_model_def=(
                    lambda model: model_calls.append(model) or {}
                ),
            ),
        })
        thread_utils = types.ModuleType("shared.utils.thread_utils")
        thread_utils.AsyncStream = object
        thread_utils.Listener = object
        thread_utils.async_run = lambda *_args, **_kwargs: None

        with (
            mock.patch.object(
                h3_execution_contract,
                "validate_h3_execution_request",
                side_effect=validate_request,
            ),
            mock.patch.dict(
                sys.modules, {"shared.utils.thread_utils": thread_utils},
            ),
        ):
            self.assertFalse(namespace["_run_generation"](job["id"]))

        self.assertEqual(len(validation_calls), 1)
        self.assertEqual(validation_calls[0][0], original_params)
        self.assertIs(validation_calls[0][1], plan)
        self.assertEqual(validation_calls[0][2], SEPARATOR)
        self.assertEqual(cuda_calls, [])
        self.assertEqual(model_calls, [])
        self.assertEqual(forbidden, [])
        self.assertEqual(replan_calls, [])
        self.assertEqual(len(finish_calls), 1)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["failure_details"], {
            "stage": "generation",
            "code": "h3_plan_mismatch",
            "detail": H3_PLAN_MISMATCH_DETAIL,
            "exception_type": "QueueRecoveryRuntimeError",
            "is_oom": False,
        })
        self.assertEqual(job["error"], H3_PLAN_MISMATCH_DETAIL)
        self.assertEqual(job["message"], H3_PLAN_MISMATCH_DETAIL)
        self.assertIsNone(job["oom_info"])
        self.assertEqual(job["retry_count"], 0)
        self.assertEqual(job["retry_history"], [])
        self.assertIs(job["params"], authored_params)
        self.assertEqual(job["params"], original_params)
        public_job = json.dumps(job)
        self.assertNotIn("CUDA out of memory", public_job)
        self.assertNotIn("/private/", public_job)
        self.assertNotIn("provider-token", public_job)

        for label, case_plan, mode in (
            ("empty plan", {}, 3),
            ("wrong mode", plan, 2),
        ):
            with self.subTest(worker_case=label):
                cuda_calls.clear()
                model_calls.clear()
                validation_calls.clear()
                finish_calls.clear()
                forbidden.clear()
                replan_calls.clear()
                case_params = copy.deepcopy(original_params)
                case_params.update({
                    "multi_prompts_gen_type": mode,
                    "per_clip_frames": [81],
                    "_h3_longform": case_plan,
                })
                case_original = copy.deepcopy(case_params)
                case_job = {
                    "id": f"startup-contract-{label.replace(' ', '-')}",
                    "params": case_params,
                    "status": "queued",
                    "out_dir": "",
                    "retry_count": 0,
                    "retry_history": [],
                }
                namespace["_jobs"] = {case_job["id"]: case_job}
                with (
                    mock.patch.object(
                        h3_execution_contract,
                        "validate_h3_execution_request",
                        side_effect=validate_request,
                    ),
                    mock.patch.dict(
                        sys.modules,
                        {"shared.utils.thread_utils": thread_utils},
                    ),
                ):
                    self.assertFalse(
                        namespace["_run_generation"](case_job["id"])
                    )
                self.assertEqual(len(validation_calls), 1)
                self.assertIs(validation_calls[0][1], case_plan)
                self.assertEqual(cuda_calls, [])
                self.assertEqual(model_calls, [])
                self.assertEqual(forbidden, [])
                self.assertEqual(replan_calls, [])
                self.assertEqual(len(finish_calls), 1)
                self.assertEqual(case_job["status"], "failed")
                self.assertFalse(case_job["failure_details"]["is_oom"])
                self.assertIsNone(case_job["oom_info"])
                self.assertEqual(case_job["params"], case_original)


if __name__ == "__main__":
    unittest.main()
