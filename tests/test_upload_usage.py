"""Upload readers survive disconnects until their real consumers drain."""
import asyncio
import ast
import copy
import os
from unittest.mock import patch
import sys
import threading
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services import upload_usage
from services.llm_operations import run_blocking_shielded


class UploadUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_body_holds_reader_until_final_send(self):
        entered = asyncio.Event()
        drain = asyncio.Event()
        path = "/temporary/upload-response.png"

        async def app(scope, receive, send):
            upload_usage.pin(path, types.SimpleNamespace(scope=scope))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            entered.set()
            await drain.wait()
            await send({"type": "http.response.body", "body": b"done"})

        async def send(_message):
            self.assertTrue(upload_usage.in_use(path))

        task = asyncio.create_task(upload_usage.UploadUsageMiddleware(app)(
            {"type": "http"}, None, send,
        ))
        await entered.wait()
        self.assertTrue(upload_usage.in_use(path))
        drain.set()
        await task
        self.assertFalse(upload_usage.in_use(path))

    async def test_cancelled_chat_waiter_keeps_blocking_worker_pinned(self):
        path = "/temporary/ordinary-chat.png"
        entered = threading.Event()
        drain = threading.Event()

        def worker():
            entered.set()
            drain.wait(3)

        async def request():
            with upload_usage.reader([path]):
                await run_blocking_shielded(worker)

        task = asyncio.create_task(request())
        try:
            await asyncio.to_thread(entered.wait, 2)
            self.assertTrue(entered.is_set())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(upload_usage.in_use(path))
        finally:
            drain.set()
        for _ in range(100):
            if not upload_usage.in_use(path):
                break
            await asyncio.sleep(.01)
        self.assertFalse(upload_usage.in_use(path))

    async def test_detached_task_retains_usage_after_request_returns(self):
        path = "/temporary/detached-upload.png"
        drain = asyncio.Event()
        with upload_usage.reader([path]):
            task = upload_usage.create_task(drain.wait())
        self.assertTrue(upload_usage.in_use(path))
        drain.set()
        await task
        await asyncio.sleep(0)
        self.assertFalse(upload_usage.in_use(path))

    async def test_unscoped_http_reader_cannot_silently_skip_reservation(self):
        with self.assertRaises(RuntimeError):
            upload_usage.pin("/temporary/unscoped.png", types.SimpleNamespace(scope={"type": "http"}))


    async def test_live_reader_uses_physical_volume_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physical = root / "physical"
            physical.mkdir()
            alias = root / "alias"
            alias.symlink_to(physical, target_is_directory=True)
            (physical / "voice.wav").write_bytes(b"voice")
            with upload_usage.reader([str(alias / "voice.wav")]):
                self.assertTrue(upload_usage.in_use(str(physical / "voice.wav")))
            self.assertFalse(upload_usage.in_use(str(physical / "voice.wav")))


    async def test_gallery_file_upload_route_pins_until_stream_finishes(self):
        from starlette.requests import Request
        from fastapi import HTTPException
        from services.output_access import can_access_upload, write_upload_access_sidecar
        launch = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        tree = ast.parse(launch.read_text())
        names = {"serve_file", "_resolve_authorized_request_media"}
        nodes = [copy.deepcopy(node) for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        for node in nodes:
            node.decorator_list = []
        namespace = dict(Request=Request, HTTPException=HTTPException, os=os,
                         can_access_upload=can_access_upload,
                         _request_project_workspace=lambda request, workspace: workspace,
                         _require_upload_content_access=lambda request: None)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(launch), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            uploads = Path(directory) / "uploads"
            uploads.mkdir()
            media = uploads / "gallery.wav"
            media.write_bytes(b"streamed upload")
            owner = "a" * 32
            write_upload_access_sidecar(str(media), owner)
            entered = asyncio.Event()
            drain = asyncio.Event()
            scope = {"type": "http", "method": "GET", "path": "/api/v1/file/gallery.wav",
                     "headers": [], "state": {"maestro_session_id": owner}}
            async def app(scope, receive, send):
                response = namespace["serve_file"](Request(scope), media.name, "__uploads__")
                await response(scope, receive, send)
            async def send(message):
                if message["type"] == "http.response.body":
                    self.assertEqual(message["body"], b"streamed upload")
                    self.assertTrue(upload_usage.in_use(str(media)))
                    entered.set()
                    await drain.wait()
            async def receive():
                await asyncio.Future()
            with patch("os.getcwd", return_value=directory):
                task = asyncio.create_task(upload_usage.UploadUsageMiddleware(app)(scope, receive, send))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    self.assertTrue(upload_usage.in_use(str(media)))
                finally:
                    drain.set()
                await task
            self.assertFalse(upload_usage.in_use(str(media)))
