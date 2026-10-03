"""CPU checks for cumulative dispatch through the existing sealed offload plan."""

# Execute the reviewed helper without importing or starting the live server.
# ruff: noqa: S102
from __future__ import annotations

import ast
import copy
import itertools
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from services.h3_cumulative_execution import (
    build_h3_cumulative_tasks,
    prepare_h3_cumulative_request,
)
from services.h3_offload_plan import (
    H3OffloadPlanError,
    assert_h3_offload_plan_parity,
    build_h3_offload_plan,
    public_h3_offload_plan,
    seal_h3_offload_plan,
    validate_h3_offload_plan,
)
from services.queue_recovery_runtime import QueueRecoveryRuntimeError

ROOT = Path(__file__).resolve().parents[1]


def source(**changes):
    return {
        "_h3_cumulative_append": True,
        "model_type": "minimax_h3",
        "resolution": "1344x768",
        "video_length": 141,
        "sliding_window_size": 124,
        "prompt": "An adult courier crosses a tiled hall.",
        "num_inference_steps": 28,
        "override_profile": -1,
        "custom_settings": {"h3_attention_engine": "sdpa"},
        "repeat_generation": 1,
        "batch_size": 1,
        **changes,
    }


class H3CumulativeOffloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        helper = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_apply_h3_offload_plan_to_manifest"
        )
        scope = {
            "validate_h3_offload_plan": validate_h3_offload_plan,
            "H3OffloadPlanError": H3OffloadPlanError,
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
        }
        exec(
            compile(
                ast.Module(body=[helper], type_ignores=[]), "launch-helper", "exec"
            ),
            scope,
        )
        cls.apply_plan = staticmethod(scope[helper.name])

    def setUp(self):
        self.environment = patch.dict(
            os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def fixture(self, **changes):
        params = source(**changes)
        cumulative = prepare_h3_cumulative_request(params, require_gate=False)
        offload = seal_h3_offload_plan(params, effective_profile=4)
        job = {
            "id": "test-cumulative-offload",
            "params": params,
            "_recovery_owner_digest": "test-owner",
            "_recovery_project_digest": "test-project",
        }
        tasks = build_h3_cumulative_tasks(job, cumulative, itertools.count(1).__next__)
        return params, cumulative, offload, tasks

    def test_seal_uses_sampler_and_new_publication_geometry_with_gate_off(self):
        params, cumulative, offload, _tasks = self.fixture()
        self.assertEqual(
            [
                (s["generated_frames"], s["published_frames"])
                for s in offload["segments"]
            ],
            [(124, 124), (39, 17)],
        )
        self.assertEqual(cumulative["windows"][-1]["cumulative_published_frames"], 141)
        self.assertEqual(offload["movement_count"], 1)
        self.assertEqual(offload["segments"][1]["transition"], "resident_reuse")
        self.assertEqual(
            assert_h3_offload_plan_parity(
                offload, copy.deepcopy(offload), params=params
            ),
            offload,
        )
        self.assertNotIn(params["prompt"], json.dumps(public_h3_offload_plan(offload)))
        changed = dict(params, video_length=158)
        with self.assertRaises(H3OffloadPlanError):
            assert_h3_offload_plan_parity(offload, offload, params=changed)
        with self.assertRaises(H3OffloadPlanError):
            build_h3_offload_plan(
                dict(params, _h3_cumulative_append=1), effective_profile=4
            )

    def test_actual_repeated_cumulative_tasks_receive_sealed_profiles(self):
        _params, cumulative, offload, tasks = self.fixture(repeat_generation=2)
        self.assertEqual(
            [t["params"]["video_length"] for t in tasks], [124, 39, 124, 39]
        )
        self.apply_plan(tasks, offload, cumulative_plan=cumulative)
        self.assertEqual([t["params"]["override_profile"] for t in tasks], [4] * 4)

    def test_changed_or_incomplete_child_geometry_fails_before_profile_mutation(self):
        _params, cumulative, offload, tasks = self.fixture()
        variants = []
        for key, value in (
            ("cumulative_plan_sha256", "0" * 64),
            ("generated_frames", 142),
            ("published_frames", 140),
            ("index", True),
            ("defer_concat", False),
        ):
            candidate = copy.deepcopy(tasks)
            candidate[-1]["params"]["multi_clip_info"][key] = value
            variants.append(candidate)
        candidate = copy.deepcopy(tasks)
        candidate[-1]["params"]["video_length"] = 56
        variants.append(candidate)
        variants.append(copy.deepcopy(tasks[:1]))
        for candidate in variants:
            with self.subTest(tasks=candidate):
                before = copy.deepcopy(candidate)
                with self.assertRaises(QueueRecoveryRuntimeError):
                    self.apply_plan(candidate, offload, cumulative_plan=cumulative)
                self.assertEqual(candidate, before)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.apply_plan(copy.deepcopy(tasks), offload)


if __name__ == "__main__":
    unittest.main()
