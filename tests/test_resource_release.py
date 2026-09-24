"""Owner resource clearing must preserve queue and authorization boundaries."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
import sys
import threading
import types
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "app" / "launch.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))


def isolated(*names: str, globals_: dict | None = None) -> dict:
    nodes = [
        node for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    assert len(nodes) == len(names)
    namespace = dict(globals_ or {})
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=nodes, type_ignores=[],
    )), str(SOURCE), "exec"), namespace)
    return namespace


class HttpError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FakeApi:
    def get(self, _path):
        return lambda function: function

    post = get


class FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class FakeResponse:
    def __init__(self):
        self.headers = {}


def activity(*, running=0, queued=0, preparing=0, directors=0, token="a" * 64, paused=False):
    return {
        "activity_token": token,
        "running": running,
        "queued": queued,
        "preparing": preparing,
        "director_running": directors,
        "queue_paused": paused,
        "director_queue_paused": True,
        "_job_ids": (),
        "_pipeline_ids": (),
    }


class ResourceReleaseTests(unittest.TestCase):
    def test_account_mode_requires_reauthenticated_owner(self):
        owner = {"enabled": True, "capability": False, "reauth": False}
        namespace = isolated(
            "_require_owner_resource_control",
            globals_={
                "Request": object,
                "HTTPException": HttpError,
                "_accounts_enabled": lambda: owner["enabled"],
                "_require_account_store": lambda _request: object(),
                "_require_account_principal": lambda _request: {"role": "owner"},
                "_request_has_account_capability": lambda _request, _cap: owner["capability"],
                "_request_has_recent_account_reauth": lambda _request: owner["reauth"],
                "_request_is_cloudflare_remote": lambda _request: False,
            },
        )
        request = types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=False))
        check = namespace["_require_owner_resource_control"]
        with self.assertRaises(HttpError) as raised:
            check(request)
        self.assertEqual(raised.exception.status_code, 403)
        owner["capability"] = True
        with self.assertRaises(HttpError) as raised:
            check(request)
        self.assertIn("Confirm your password", raised.exception.detail)
        owner["reauth"] = True
        self.assertIsNone(check(request))
        owner["enabled"] = False
        request.state.maestro_remote = True
        with self.assertRaises(HttpError) as raised:
            check(request)
        self.assertEqual(raised.exception.status_code, 403)
        request.state.maestro_remote = False
        self.assertIsNone(check(request))

    def test_activity_token_changes_when_a_job_starts_and_counts_work(self):
        director = types.ModuleType("services.director_pipeline")
        director._pipelines = {"p": {"status": "running"}}
        director._ACTIVE_PIPELINE_STATUSES = ("queued", "planning", "running", "paused")
        director._director_queue_lock = threading.RLock()
        director._director_queue_state = {"paused": True, "entries": []}
        director._director_queue_base = "/test/project"
        jobs = {
            "a": {"status": "running"},
            "b": {"status": "queued", "queue_held": True},
            "c": {"status": "preparing"},
            "d": {"status": "waiting_for_plan_approval"},
            "done": {"status": "completed"},
        }
        namespace = isolated(
            "_resource_release_activity",
            globals_={
                "Mapping": Mapping,
                "_jobs": jobs,
                "_RESOURCE_RELEASE_ACTIVE_STATUSES": frozenset({
                    "queued", "running", "preparing", "waiting_for_plan_approval",
                }),
                "json": json,
                "hmac": hmac,
                "hashlib": hashlib,
                "_session_secret": lambda: b"test-only-key",
                "queue_control_state": lambda: {"paused": False},
            },
        )
        services = types.ModuleType("services")
        services.__path__ = []
        services.director_pipeline = director
        with mock.patch.dict("sys.modules", {
            "services": services, "services.director_pipeline": director,
        }):
            before = namespace["_resource_release_activity"]()
            self.assertEqual(
                (before["running"], before["queued"], before["preparing"], before["director_running"]),
                (1, 2, 1, 1),
            )
            jobs["b"]["status"] = "running"
            after = namespace["_resource_release_activity"]()
        self.assertNotEqual(before["activity_token"], after["activity_token"])
        self.assertEqual(after["running"], 2)

    def test_director_queue_dispatch_window_requires_confirmation(self):
        director = types.ModuleType("services.director_pipeline")
        director._pipelines = {}
        director._ACTIVE_PIPELINE_STATUSES = ("queued", "planning", "running", "paused")
        director._director_queue_lock = threading.RLock()
        director._director_queue_base = "/test/project"
        director._director_queue_state = {
            "paused": False,
            "entries": [{"id": "entry-a", "status": "running", "pipeline_id": None}],
        }
        namespace = isolated(
            "_resource_release_activity",
            globals_={
                "Mapping": Mapping,
                "_jobs": {},
                "_RESOURCE_RELEASE_ACTIVE_STATUSES": frozenset({
                    "queued", "running", "preparing", "waiting_for_plan_approval",
                }),
                "json": json, "hmac": hmac, "hashlib": hashlib,
                "_session_secret": lambda: b"test-only-key",
                "queue_control_state": lambda: {"paused": False},
            },
        )
        services = types.ModuleType("services")
        services.__path__ = []
        services.director_pipeline = director
        with mock.patch.dict("sys.modules", {
            "services": services, "services.director_pipeline": director,
        }):
            before = namespace["_resource_release_activity"]()
            self.assertEqual(before["director_running"], 1)
            director._pipelines["p"] = {"status": "planning"}
            director._director_queue_state["entries"][0]["pipeline_id"] = "p"
            after = namespace["_resource_release_activity"]()
        self.assertNotEqual(before["activity_token"], after["activity_token"])
        self.assertEqual(after["director_running"], 1)

    def route(self, *, state, events, owner=True, released=None):
        gate = threading.Lock()

        class Slot:
            def __init__(self, *, blocking=False):
                self.blocking = blocking

            def __enter__(self):
                events.append("gpu-acquire")
                return True

            def __exit__(self, *_args):
                events.append("gpu-release")

        def authorize(_request):
            events.append("authorize")
            if not owner:
                raise HttpError(403, "Owner access is required")

        def stop(_confirmed):
            events.append("stop")
            state.update(running=0, director_running=0, preparing=0)
            return 1, 1

        def unload(targets):
            self.assertFalse(gate.acquire(blocking=False))
            events.append("unload")
            return released if released is not None else (["generation model"], [])

        namespace = isolated(
            "owner_resource_release_preview", "owner_resource_release",
            "_owner_resource_release_execute",
            globals_={
                "api": FakeApi(), "Request": object, "Response": object,
                "Any": object, "Mapping": Mapping, "asyncio": asyncio,
                "HTTPException": HttpError,
                "_set_recovery_no_store": lambda response: response.headers.update({"Cache-Control": "private, no-store"}),
                "_require_owner_resource_control": authorize,
                "_resource_release_activity": lambda: dict(state),
                "_resource_release_loaded": lambda: ["generation model", "Director assistant"],
                "hmac": hmac,
                "_resource_release_stop_running": stop,
                "_resource_release_pause_director_queue": lambda _confirmed: events.append("pause-director"),
                "_resource_release_unload": unload,
                "_jobs": {},
                "_gen_lock": gate,
                "_WgpNativeGpuExecutionSlot": Slot,
                "set_queue_paused": lambda value: (events.append("pause"), state.update(queue_paused=value)),
                "queue_control_state": lambda: {"paused": state["queue_paused"]},
            },
        )
        return namespace

    def test_preview_requires_owner_and_hides_private_jobs(self):
        state = activity(running=1)
        events = []
        namespace = self.route(state=state, events=events, owner=False)
        with self.assertRaises(HttpError) as raised:
            namespace["owner_resource_release_preview"](FakeRequest({}), FakeResponse())
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(events, ["authorize"])

        events.clear()
        namespace = self.route(state=state, events=events)
        response = FakeResponse()
        preview = namespace["owner_resource_release_preview"](FakeRequest({}), response)
        self.assertEqual(preview["running"], 1)
        self.assertEqual(preview["loaded"], ["generation model", "Director assistant"])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_running_work_needs_fresh_explicit_consent(self):
        state = activity(running=1, queued=2, directors=1)
        events = []
        namespace = self.route(state=state, events=events)

        for body in (
            {"activity_token": "b" * 64, "confirm_active": True, "stop_running": True},
            {"activity_token": "a" * 64, "confirm_active": False, "stop_running": True},
            {"activity_token": "a" * 64, "confirm_active": True, "stop_running": False},
        ):
            with self.subTest(body=body), self.assertRaises(HttpError) as raised:
                asyncio.run(namespace["owner_resource_release"](FakeRequest(body), FakeResponse()))
            self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("pause", events)
        self.assertNotIn("stop", events)
        self.assertNotIn("unload", events)

    def test_active_work_is_not_stopped_for_no_loaded_resources(self):
        state = activity(running=1)
        events = []
        namespace = self.route(state=state, events=events)
        namespace["_resource_release_loaded"] = lambda: []
        with self.assertRaises(HttpError) as raised:
            asyncio.run(namespace["owner_resource_release"](
                FakeRequest({
                    "activity_token": "a" * 64, "confirm_active": True,
                    "stop_running": True,
                }), FakeResponse(),
            ))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("pause", events)
        self.assertNotIn("stop", events)

    def test_confirmed_running_work_pauses_then_stops_before_unloading(self):
        state = activity(running=1, queued=2, directors=1)
        events = []
        namespace = self.route(state=state, events=events)
        result = asyncio.run(namespace["owner_resource_release"](
            FakeRequest({
                "activity_token": "a" * 64, "confirm_active": True,
                "stop_running": True, "targets": ["generation model"],
            }), FakeResponse(),
        ))
        self.assertLess(events.index("pause"), events.index("stop"))
        self.assertLess(events.index("stop"), events.index("gpu-acquire"))
        self.assertLess(events.index("gpu-acquire"), events.index("unload"))
        self.assertEqual(result["stopped_jobs"], 1)
        self.assertEqual(result["stopped_pipelines"], 1)
        self.assertTrue(result["queue_paused"])

    def test_new_work_after_queue_pause_is_not_stopped_or_unloaded(self):
        state = activity(queued=1)
        events = []
        namespace = self.route(state=state, events=events)

        def pause(value):
            events.append("pause")
            state.update(queue_paused=value, activity_token="b" * 64, running=1)

        namespace["set_queue_paused"] = pause
        with self.assertRaises(HttpError) as raised:
            asyncio.run(namespace["owner_resource_release"](
                FakeRequest({
                    "activity_token": "a" * 64, "confirm_active": True,
                    "stop_running": True,
                }), FakeResponse(),
            ))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("stop", events)
        self.assertNotIn("unload", events)

    def test_active_paired_sample_keeps_dedicated_stop_path(self):
        state = activity(running=1)
        state["_job_ids"] = ("sample",)
        events = []
        namespace = self.route(state=state, events=events)
        namespace["_jobs"] = {"sample": {
            "status": "running", "kind": "sample_campaign_generation",
        }}
        with self.assertRaises(HttpError) as raised:
            asyncio.run(namespace["owner_resource_release"](
                FakeRequest({
                    "activity_token": "a" * 64, "confirm_active": True,
                    "stop_running": True,
                }), FakeResponse(),
            ))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("pause", events)
        self.assertNotIn("stop", events)

    def test_only_confirmed_jobs_are_cancelled(self):
        cancelled = []
        director = types.ModuleType("services.director_pipeline")
        director._pipelines = {}
        director.stop_pipeline = lambda _pid: False
        services = types.ModuleType("services")
        services.__path__ = []
        services.director_pipeline = director
        jobs = {
            "confirmed": {"status": "running"},
            "new": {"status": "running"},
        }
        namespace = isolated(
            "_resource_release_stop_running",
            globals_={
                "Mapping": Mapping, "_jobs": jobs,
                "request_cancel": lambda _job, *, job_id, **_kwargs: (
                    cancelled.append(job_id)
                    or types.SimpleNamespace(was_running=True, abort_signalled=False)
                ),
                "_active_gen_states": {},
                "_cpu_text_lane": types.SimpleNamespace(runtime_tokens=lambda _id: None),
            },
        )
        with mock.patch.dict(sys.modules, {
            "services": services, "services.director_pipeline": director,
        }):
            result = namespace["_resource_release_stop_running"]({
                "_job_ids": ("confirmed",), "_pipeline_ids": (),
            })
        self.assertEqual(result, (1, 0))
        self.assertEqual(cancelled, ["confirmed"])

    def test_queued_work_is_paused_without_cancellation(self):
        state = activity(queued=2)
        events = []
        namespace = self.route(state=state, events=events)
        result = asyncio.run(namespace["owner_resource_release"](
            FakeRequest({
                "activity_token": "a" * 64, "confirm_active": True,
                "stop_running": False,
            }), FakeResponse(),
        ))
        self.assertIn("pause", events)
        self.assertNotIn("stop", events)
        self.assertEqual(result["stopped_jobs"], 0)
        self.assertTrue(result["queue_paused"])

    def test_invalid_resource_selection_cannot_trigger_release(self):
        state = activity()
        events = []
        namespace = self.route(state=state, events=events)
        with self.assertRaises(HttpError) as raised:
            asyncio.run(namespace["owner_resource_release"](
                FakeRequest({
                    "activity_token": "a" * 64, "confirm_active": True,
                    "stop_running": False, "targets": ["unknown"],
                }), FakeResponse(),
            ))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("unload", events)

        with self.assertRaises(HttpError) as raised:
            asyncio.run(namespace["owner_resource_release"](
                FakeRequest({
                    "activity_token": "a" * 64, "confirm_active": True,
                    "stop_running": False, "targets": [],
                }), FakeResponse(),
            ))
        self.assertEqual(raised.exception.status_code, 400)

    def test_all_resident_components_are_released_independently(self):
        calls = []
        services = types.ModuleType("services")
        llm = types.ModuleType("services.llm_service")
        llm.unload_model = lambda: calls.append("Director assistant")
        services.llm_service = llm
        inpaint = types.ModuleType("services.inpaint_service")
        inpaint.SAM_SERVICE_URL = "http://127.0.0.1:8321"
        mmaudio = types.ModuleType("postprocessing.mmaudio.mmaudio")
        mmaudio.release_persistent_models = lambda: calls.append("MMAudio")
        analysis = types.ModuleType("services.audio_analysis")
        analysis.unload_whisper = lambda: calls.append("Whisper")
        analysis.unload_diarizer = lambda: calls.append("speaker diarizer")
        labels = [
            "generation model", "Director assistant", "prompt enhancer",
            "FlashVSR", "MMAudio", "Whisper", "speaker diarizer", "SAM",
        ]
        fake_wgp = types.SimpleNamespace(
            reset_prompt_enhancer=lambda: calls.append("enhancer reset"),
            reset_prompt_enhancer_if_requested=lambda: calls.append("prompt enhancer"),
            release_flashvsr_vram=lambda: calls.append("FlashVSR"),
        )
        response = types.SimpleNamespace(raise_for_status=lambda: calls.append("SAM"))
        namespace = isolated(
            "_resource_release_unload",
            globals_={
                "_resource_release_loaded": lambda: labels,
                "_release_wgp_model_with_native_gpu_exclusion": lambda: calls.append("generation model"),
                "wgp": fake_wgp,
                "sys": sys,
                "requests": types.SimpleNamespace(post=lambda *_args, **_kwargs: response),
                "gc": types.SimpleNamespace(collect=lambda: None),
                "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
            },
        )
        with mock.patch.dict(sys.modules, {
            "services": services,
            "services.llm_service": llm,
            "services.inpaint_service": inpaint,
            "services.audio_analysis": analysis,
            "postprocessing.mmaudio.mmaudio": mmaudio,
        }):
            released, failures = namespace["_resource_release_unload"](frozenset({"all"}))
        self.assertEqual(released, labels)
        self.assertEqual(failures, [])
        self.assertEqual(calls[0], "generation model")
        self.assertIn("SAM", calls)

    def test_selected_component_does_not_unload_others(self):
        calls = []
        services = types.ModuleType("services")
        llm = types.ModuleType("services.llm_service")
        llm.unload_model = lambda: calls.append("Director assistant")
        services.llm_service = llm
        inpaint = types.ModuleType("services.inpaint_service")
        inpaint.SAM_SERVICE_URL = "http://127.0.0.1:8321"
        namespace = isolated(
            "_resource_release_unload",
            globals_={
                "_resource_release_loaded": lambda: ["generation model", "Director assistant"],
                "_release_wgp_model_with_native_gpu_exclusion": lambda: calls.append("generation model"),
                "wgp": types.SimpleNamespace(),
                "sys": sys,
                "requests": types.SimpleNamespace(),
                "gc": types.SimpleNamespace(collect=lambda: None),
                "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
            },
        )
        with mock.patch.dict(sys.modules, {
            "services": services,
            "services.llm_service": llm,
            "services.inpaint_service": inpaint,
        }):
            released, failures = namespace["_resource_release_unload"](
                frozenset({"Director assistant"}),
            )
        self.assertEqual((released, failures), (["Director assistant"], []))
        self.assertEqual(calls, ["Director assistant"])


if __name__ == "__main__":
    unittest.main()
