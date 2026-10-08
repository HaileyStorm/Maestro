"""Real ASGI/CPU media reads and bounded authorizer/cancellation fixtures."""
import asyncio
from dataclasses import replace
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services import h3_face_refine_preview as preview
from services import upload_usage
from services.h3_face_refine_reads import FacePreviewAccess, register_face_preview_reads

import ast
import copy
from contextlib import nullcontext
import json
import os
import types
from fastapi import Request
from services.account_auth import AccountAuthStore, AccountStoreCorruptError, resolve_account_capabilities
from services.account_project_membership import ProjectMembershipError, ProjectMembershipStoreUnavailableError, role_allows
from services.queue_recovery_adapter import QueueRecoveryAdapterError
from services.search_index import load_media_sidecars

BASE = "/api/v1/tools/h3-face-refine"
QUERY = {"workspace": "project", "name": "clip.mkv", "revision": "r1"}


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class FaceReadTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="face-read-http-")
        cls.source = Path(cls.storage.name) / "clip.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=96x64:r=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6", "-map", "0:v", "-map", "1:a",
            "-frames:v", "124", "-c:v", "ffv1", "-threads", "1", "-c:a", "pcm_s16le", str(cls.source)],
            check=True, capture_output=True, timeout=30)
        cls.rgb = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(cls.source), "-map", "0:v:0",
            "-an", "-threads", "1", "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            check=True, capture_output=True, timeout=30).stdout

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()

    async def asyncSetUp(self):
        self.app = FastAPI()
        self.authorized = FacePreviewAccess(**QUERY, path=str(self.source), private=True, explicit=False)
        self.calls = []
        self.denial = None
        self.revoke_after_read = False
        self.changed_after_read = None
        def authorize(request, **binding):
            self.calls.append((binding, request.headers.get("x-session")))
            if self.denial or (self.revoke_after_read and len(self.calls) > 1):
                raise HTTPException(self.denial or 403, "Project access is unavailable", headers={"X-Access-Check": "denied"})
            if self.changed_after_read and len(self.calls) > 1:
                return self.changed_after_read
            return self.authorized
        register_face_preview_reads(self.app, authorize_source=authorize)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    def assert_no_store(self, response):
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn(str(self.source), response.text if response.headers.get("content-type", "").startswith("application/json") else "")

    async def test_actual_source_and_exact_png_routes_match_browser_contract_without_paths(self):
        import numpy as np
        from PIL import Image
        response = await self.client.get(BASE + "/source", params=QUERY, headers={"x-session": "owner"})
        self.assertEqual(response.status_code, 200)
        self.assert_no_store(response)
        value = response.json()
        self.assertEqual({key: value[key] for key in QUERY}, QUERY)
        self.assertEqual((value["width"], value["height"], value["frame_count"], value["fps"]), (96, 64, 124, "24/1"))
        self.assertEqual([stream["ordinal"] for stream in value["audio_streams"]], [0])
        self.assertNotIn("sha256", value)
        original = np.frombuffer(self.rgb, dtype=np.uint8).reshape(124, 64, 96, 3)
        for index in (0, 53, 123):
            response = await self.client.get(BASE + "/frame", params={**QUERY, "frame_index": index, "preview_attempt": 1})
            self.assertEqual(response.status_code, 200)
            self.assert_no_store(response)
            self.assertEqual(response.headers["content-type"], "image/png")
            np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(response.content))), original[index])
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(self.calls[0], (QUERY, "owner"))

    async def test_initial_flag_session_permission_denials_start_no_worker_and_are_not_cached(self):
        for code in (401, 403, 409, 423):
            self.denial = code
            with patch.object(preview, "read_face_source") as read:
                response = await self.client.get(BASE + "/source", params=QUERY)
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.headers["x-access-check"], "denied")
                self.assert_no_store(response)
                read.assert_not_called()

    async def test_revoked_access_after_decode_never_returns_pixels(self):
        self.revoke_after_read = True
        response = await self.client.get(BASE + "/frame", params={**QUERY, "frame_index": 0})
        self.assertEqual(response.status_code, 403)
        self.assert_no_store(response)
        self.assertEqual(len(self.calls), 2)

    async def test_changed_privacy_path_or_revision_after_read_never_returns_facts(self):
        for change in ({"private": False}, {"explicit": True}, {"path": str(self.source) + ".other"}, {"revision": "r2"}):
            self.calls.clear()
            self.changed_after_read = replace(self.authorized, **change)
            response = await self.client.get(BASE + "/source", params=QUERY)
            self.assertEqual(response.status_code, 409)
            self.assert_no_store(response)

    async def test_query_cannot_supply_paths_duplicates_unknown_fields_or_noninteger_frames(self):
        bad = [list(QUERY.items()) + [("name", "other.mkv")], {**QUERY, "path": str(self.source)},
               {**QUERY, "name": "../clip.mkv"}, {**QUERY, "revision": ""}]
        with patch.object(preview, "read_face_source") as read:
            for params in bad:
                response = await self.client.get(BASE + "/source", params=params)
                self.assertEqual(response.status_code, 400)
                self.assert_no_store(response)
            read.assert_not_called()
        self.assertFalse(self.calls)
        for index in ("-1", "1.0", "True", "345", "0001", ""):
            response = await self.client.get(BASE + "/frame", params={**QUERY, "frame_index": index})
            self.assertEqual(response.status_code, 400)
            self.assert_no_store(response)
        response = await self.client.get(BASE + "/frame", params={**QUERY, "frame_index": "0", "preview_attempt": "1e9"})
        self.assertEqual(response.status_code, 400)
        self.assert_no_store(response)

    async def test_invalid_authorizer_binding_and_decoder_error_are_redacted_without_fallback(self):
        self.authorized = replace(self.authorized, workspace="foreign")
        response = await self.client.get(BASE + "/source", params=QUERY)
        self.assertEqual(response.status_code, 409)
        self.assert_no_store(response)
        self.authorized = replace(self.authorized, workspace="project")
        with patch.object(preview, "read_face_frame", side_effect=ValueError(str(self.source))):
            response = await self.client.get(BASE + "/frame", params={**QUERY, "frame_index": 0})
        self.assertEqual(response.status_code, 409)
        self.assert_no_store(response)

    async def wait_for(self, predicate):
        deadline = asyncio.get_running_loop().time() + 5
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                self.fail("CPU reader event did not arrive")
            await asyncio.sleep(0.005)

    async def test_cancelled_waiters_retain_capacity_until_both_workers_drain(self):
        started, cancelled, finished = [], [], []
        offload_tasks = []
        real_to_thread = upload_usage.to_thread
        async def submit(function, *args, **kwargs):
            offload_tasks.append(asyncio.current_task())
            return await real_to_thread(function, *args, **kwargs)
        lock = threading.Lock()
        def work(path, *, cancel_check):
            with lock:
                started.append(True)
            while not cancel_check():
                time.sleep(0.005)
            with lock:
                cancelled.append(True)
            time.sleep(0.25)
            with lock:
                finished.append(True)
            raise preview.FacePreviewCancelled("cancelled")
        with patch.object(preview, "read_face_source", work), patch.object(upload_usage, "to_thread", submit):
            tasks = [asyncio.create_task(self.client.get(BASE + "/source", params=QUERY)) for _ in range(2)]
            try:
                await self.wait_for(lambda: len(started) == 2)
                # Cancellation of the offload coroutine can precede actual
                # thread completion. Neither path may free a running slot.
                for task in offload_tasks:
                    task.cancel()
                for task in tasks:
                    task.cancel()
                await self.wait_for(lambda: len(cancelled) == 2)
                for task in tasks:
                    task.cancel()  # A second cancellation cannot abandon drain.
                response = await self.client.get(BASE + "/source", params=QUERY)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers["retry-after"], "1")
                self.assert_no_store(response)
                self.assertFalse(finished)
                results = await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(all(isinstance(result, asyncio.CancelledError) for result in results))
                self.assertEqual(len(finished), 2)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        response = await self.client.get(BASE + "/source", params=QUERY)
        self.assertEqual(response.status_code, 200)

    async def test_http_disconnect_cancels_reader_and_sends_no_png(self):
        started, drained = threading.Event(), threading.Event()
        messages = asyncio.Queue()
        await messages.put({"type": "http.request", "body": b"", "more_body": False})
        sent = []
        async def receive():
            return await messages.get()
        async def send(message):
            sent.append(message)
        def work(path, index, *, cancel_check):
            started.set()
            while not cancel_check():
                time.sleep(0.005)
            drained.set()
            raise preview.FacePreviewCancelled("disconnected")
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                 "scheme": "http", "path": BASE + "/frame", "raw_path": (BASE + "/frame").encode(),
                 "query_string": b"workspace=project&name=clip.mkv&revision=r1&frame_index=0", "headers": [],
                 "client": ("127.0.0.1", 1234), "server": ("test", 80), "root_path": ""}
        with patch.object(preview, "read_face_frame", work):
            task = asyncio.create_task(self.app(scope, receive, send))
            try:
                await self.wait_for(started.is_set)
                await messages.put({"type": "http.disconnect"})
                await asyncio.wait_for(task, timeout=5)
                self.assertTrue(drained.is_set())
                self.assertEqual(next(item["status"] for item in sent if item["type"] == "http.response.start"), 499)
                self.assertFalse(any(b"\x89PNG" in item.get("body", b"") for item in sent))
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_duplicate_registration_cannot_create_another_reader_pool(self):
        with self.assertRaisesRegex(ValueError, "already registered"):
            register_face_preview_reads(self.app, authorize_source=lambda *a, **k: self.authorized)

    async def test_two_failed_submissions_release_capacity_and_late_invocations_cannot_decode(self):
        queued = []
        async def failed(function, *args, **kwargs):
            queued.append(function)
            raise RuntimeError("Executor submission failed")
        with patch.object(upload_usage, "to_thread", failed):
            for _ in range(2):
                response = await self.client.get(BASE + "/source", params=QUERY)
                self.assertEqual(response.status_code, 409)
                self.assert_no_store(response)
        with patch.object(preview, "read_face_source") as decode:
            for invocation in queued:
                with self.assertRaises(preview.FacePreviewCancelled):
                    await asyncio.to_thread(invocation)
            decode.assert_not_called()
        response = await self.client.get(BASE + "/source", params=QUERY)
        self.assertEqual(response.status_code, 200)

    async def test_cancelled_submission_before_worker_start_does_not_leak_capacity(self):
        submitted = []
        async def pending(function, *args, **kwargs):
            submitted.append(asyncio.current_task())
            await asyncio.Future()
        with patch.object(upload_usage, "to_thread", pending):
            task = asyncio.create_task(self.client.get(BASE + "/source", params=QUERY))
            try:
                await self.wait_for(lambda: bool(submitted))
                submitted[0].cancel()
                result, = await asyncio.gather(task, return_exceptions=True)
                self.assertIsInstance(result, asyncio.CancelledError)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        response = await self.client.get(BASE + "/source", params=QUERY)
        self.assertEqual(response.status_code, 200)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class FaceMountAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        FaceReadTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        FaceReadTests.tearDownClass.__func__(cls)

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="face-mount-auth-")
        self.addCleanup(self.temporary.cleanup)
        self.store = AccountAuthStore(str(Path(self.temporary.name) / "auth.json"), b"synthetic-secret" * 4, password_n=1024)
        nonce = self.store.issue_nonce("a" * 32, "bootstrap")["nonce"]
        boot = self.store.bootstrap_owner(username="FixtureOwner", password="fixture password only", email="", device_label="Fixture", nonce_session_id="a" * 32, nonce=nonce, remote=False)
        self.session = boot["account_session_id"]
        self.record = {"state": "active", "bindings": [{"account_id": boot["account"]["id"], "role": "owner"}]}
        self.enforced = True
        self.enabled = True
        self.unlocked = True
        self.model_visible = True
        self.remote = False
        self.store_calls = 0
        self.source.with_suffix(".meta.json").write_text(json.dumps({"workspace": "project", "private": True, "explicit": False}))

        def account_store():
            self.store_calls += 1
            return self.store if self.enabled else None

        def project_status(*args):
            return types.SimpleNamespace(protected=True, unlocked=self.unlocked)

        self.ns = dict(os=os, Request=Request, HTTPException=HTTPException,
            AccountStoreCorruptError=AccountStoreCorruptError,
            ProjectMembershipError=ProjectMembershipError,
            ProjectMembershipStoreUnavailableError=ProjectMembershipStoreUnavailableError,
            QueueRecoveryAdapterError=QueueRecoveryAdapterError,
            resolve_account_capabilities=resolve_account_capabilities, role_allows=role_allows,
            _face_refine_available=lambda: self.enabled,
            _account_auth_store=account_store,
            _require_account_store=lambda request: self.store,
            _account_project_access_state=lambda: {"enforced": self.enforced},
            _account_project_membership_store=lambda: types.SimpleNamespace(lookup=lambda **kwargs: copy.deepcopy(self.record)),
            _queue_recovery_existing_project_identity=lambda path: "fixture-project-instance",
            _existing_workspace_dir=lambda workspace: str(self.source.parent),
            _workspace_dir=lambda workspace: str(self.source.parent),
            _project_access=types.SimpleNamespace(status=project_status, authorize=project_status),
            _STATE_CHANGING_METHODS=frozenset({"POST", "PUT", "PATCH", "DELETE"}),
            _reserve_workspace_operations=lambda workspace: nullcontext(),
            _output_lineage_mutation_guard=lambda out_dir: nullcontext(),
            _require_h3_legal_execution=lambda models: None,
            _require_model_recipe_terms=lambda models: None,
            _model_visibility_response=lambda: {"configured": True, "enabled_models": ["minimax_h3"] if self.model_visible else []},
            load_media_sidecars=load_media_sidecars)
        text = (ROOT / "app/launch.py").read_text()
        names = {"_attach_account_request_state", "_require_account_project_permission", "_require_project_access", "_require_authorized_output", "_request_project_workspace", "_output_revision", "_h3_gallery_av_source_state", "_remote_visible_model_ids", "_require_remote_visible_models", "_authorize_face_refine_preview_source"}
        nodes = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name in names]
        assert {n.name for n in nodes} == names
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "private-mount-candidate", "exec"), self.ns)
        self.query = {"workspace": "project", "name": self.source.name, "revision": self.ns["_output_revision"](str(self.source), str(self.source.parent), self.source.name), "frame_index": "0"}
        self.app = FastAPI()
        @self.app.middleware("http")
        async def fixture_session(request, call_next):
            request.state.maestro_account_session_id = self.session
            request.state.maestro_session_id = "a" * 32
            request.state.maestro_remote = self.remote
            self.ns["_attach_account_request_state"](request, self.session, remote=self.remote)
            return await call_next(request)
        register_face_preview_reads(self.app, authorize_source=self.ns["_authorize_face_refine_preview_source"])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://fixture")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def frame(self):
        return await self.client.get(BASE + "/frame", params=self.query)

    async def changed_during_decode(self, change):
        original = preview.read_face_frame
        def decode_then_change(*args, **kwargs):
            result = original(*args, **kwargs)
            change()
            return result
        with patch.object(preview, "read_face_frame", side_effect=decode_then_change):
            response = await self.frame()
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("image/png", response.headers.get("content-type", ""))
        self.assertNotIn(str(self.source), response.text)
        return response

    async def test_real_frame_and_current_membership_are_admitted(self):
        import numpy as np
        from PIL import Image
        response = await self.frame()
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        pixels = np.asarray(Image.open(io.BytesIO(response.content)))
        self.assertEqual(pixels.tobytes(), self.rgb[:96 * 64 * 3])

    async def test_real_account_session_revocation_during_decode_returns_no_pixels(self):
        def revoke():
            current = next(s for s in self.store.list_sessions(self.session) if s["current"])
            nonce = self.store.issue_nonce(self.session, "revoke_session")["nonce"]
            self.store.revoke_session(actor_session_id=self.session, target_handle=current["id"], nonce=nonce)
        response = await self.changed_during_decode(revoke)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.store.resolve_session(self.session))

    async def test_current_membership_and_remote_catalog_changes_withhold_pixels(self):
        response = await self.changed_during_decode(lambda: self.record["bindings"][0].update(role="viewer"))
        self.assertEqual(response.status_code, 404)
        self.record["bindings"][0]["role"] = "owner"
        self.remote = True
        response = await self.changed_during_decode(lambda: setattr(self, "model_visible", False))
        self.assertEqual(response.status_code, 404)

    async def test_accounts_disabled_preserve_current_legacy_project_grants(self):
        # Face flags are independently opt-in; account stores stay disabled.
        self.enforced = False
        self.enabled = False
        self.ns["_face_refine_available"] = lambda: True
        self.ns["_account_auth_store"] = lambda: None
        response = await self.frame()
        self.assertEqual(response.status_code, 200)
        response = await self.changed_during_decode(lambda: setattr(self, "unlocked", False))
        self.assertEqual(response.status_code, 423)
        self.assertEqual(self.store_calls, 0)


if __name__ == "__main__":
    unittest.main()
