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


if __name__ == "__main__":
    unittest.main()
