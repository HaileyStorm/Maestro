"""CPU-only retained H3 timing and authorized queue projections."""

import copy
import json
import os
import types
import unittest
from unittest.mock import patch

import test_queue_launch_recovery as queue_fixture


class H3CumulativePublicQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = queue_fixture._tree("app/launch.py")

    def setUp(self):
        self.params = {
            "_h3_cumulative_append": True,
            "model_type": "minimax_h3",
            "resolution": "1344x768",
            "video_length": 141,
            "sliding_window_size": 124,
            "prompt": "PRIVATE_AUTHORED_PROMPT: An adult courier crosses the hall.",
            "repeat_generation": 1,
            "batch_size": 1,
        }
        self.job = {
            "id": "held-job",
            "status": "queued",
            "progress": 0,
            "queue_held": True,
            "params": self.params,
            "output_files": [],
            "workspace": "project",
            "message": "",
            "error": None,
        }
        self.jobs = {self.job["id"]: self.job}
        self.ns = queue_fixture._isolated_functions(
            self.launch,
            ("get_status", "list_jobs"),
            {
                "api": types.SimpleNamespace(get=lambda *a, **k: lambda f: f),
                "Request": object,
                "Response": object,
                "HTTPException": HTTPException,
                "_jobs": self.jobs,
                "_set_recovery_no_store": lambda response: response.headers.update(
                    {"Cache-Control": "private, no-store"}
                ),
                "_job_owned_by_request": lambda job, request: job["id"] != "foreign",
                "_generic_job_visible": lambda job: True,
                "snapshot_job": copy.deepcopy,
                "queue_scheduler_snapshot": queue_fixture._synthetic_scheduler_snapshot,
                "authorized_logical_queue_projection": queue_fixture.authorized_logical_queue_projection,
                "_queue_recovery_is_blocked": lambda job: False,
                "_job_eta_values": lambda job: (None, None),
                "queue_position": lambda job: None,
                "_queue_wait_reason_for_job": lambda job: "held",
                "_public_queue_residency_metadata": lambda *a, **k: {},
                "_public_resource_metadata": lambda job: {},
                "_public_parent_job_id": lambda job: None,
                "_public_logical_job_kind": lambda job: None,
                "_public_progress_telemetry": lambda job: {},
                "public_h3_offload_plan": lambda plan: None,
                "_public_h3_boundary": lambda boundary: None,
                "_public_job_created_at": lambda job: 0,
                "_public_job_prompt_fields": lambda *a, **k: {
                    "prompt_preview": "",
                    "active_window_prompt": "",
                },
                "job_events": lambda *a: [],
                "queue_control_state": dict,
                "_public_queue_recovery_metadata": lambda job: {},
            },
        )
        self.request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=True)
        )

    def test_retained_geometry_survives_gate_off_without_private_fields(self):
        before = copy.deepcopy(self.job)
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "0"}):
            plan = self.ns["_public_job_h3_cumulative_plan"](self.job)
        self.assertEqual(self.job, before)
        self.assertEqual(plan["published_frames"] / plan["fps"], 5.875)
        self.assertEqual(plan["requested_frames"], 141)
        self.assertEqual(plan["window_count"], 2)
        self.assertEqual(
            [
                (w["sampler_frames"], w["context_frames"], w["new_published_frames"])
                for w in plan["windows"]
            ],
            [(124, 0, 124), (39, 22, 17)],
        )
        self.assertEqual(
            set(plan),
            {
                "mode",
                "fps",
                "requested_frames",
                "published_frames",
                "window_count",
                "windows",
            },
        )
        self.assertNotIn("PRIVATE_AUTHORED_PROMPT", json.dumps(plan))
        self.assertEqual(
            set(plan["windows"][0]),
            {
                "index",
                "sampler_frames",
                "context_frames",
                "new_published_frames",
                "cumulative_published_frames",
            },
        )

    def test_status_and_reconnection_share_owner_checked_geometry(self):
        self.jobs["foreign"] = {**self.job, "id": "foreign"}
        status_response = types.SimpleNamespace(headers={})
        status = self.ns["get_status"]("held-job", self.request, status_response)
        list_response = types.SimpleNamespace(headers={})
        listed = self.ns["list_jobs"](self.request, list_response)
        self.assertEqual([row["job_id"] for row in listed["jobs"]], ["held-job"])
        self.assertEqual(
            status["h3_cumulative_plan"], listed["jobs"][0]["h3_cumulative_plan"]
        )
        self.assertEqual(status["h3_cumulative_plan"]["published_frames"], 141)
        for response in (status_response, list_response):
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        with self.assertRaises(HTTPException):
            self.ns["get_status"](
                "foreign", self.request, types.SimpleNamespace(headers={})
            )

    def test_ordinary_or_malformed_requests_do_not_project_timing(self):
        for params in (
            None,
            {},
            {**self.params, "_h3_cumulative_append": False},
            {**self.params, "video_length": False},
            {**self.params, "resolution": "bad"},
        ):
            with self.subTest(params=params):
                self.assertIsNone(
                    self.ns["_public_job_h3_cumulative_plan"]({"params": params})
                )


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
