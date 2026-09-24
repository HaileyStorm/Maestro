import ast
import asyncio
import copy
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock

from fastapi import HTTPException

from app.services import yue2_bridge


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Yue2BridgeTests(unittest.TestCase):
    def test_bridge_keeps_token_server_side(self):
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / "token"
            token.write_text("secret-value")
            seen = []

            def fake_open(request, timeout):
                seen.append((request, timeout))
                return Response(json.dumps({"generation": True, "model": "YuE2-3B"}).encode())

            with mock.patch.object(yue2_bridge, "urlopen", fake_open):
                result = yue2_bridge.Yue2Bridge(token_file=token).health()
            self.assertEqual(result["model"], "YuE2-3B")
            self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer secret-value")
            self.assertNotIn("secret-value", json.dumps(result))

    def test_bridge_preserves_public_service_detail(self):
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / "token"
            token.write_text("secret")

            def fake_open(_request, timeout=None):
                raise HTTPError(
                    "http://127.0.0.1:5191/api/generations",
                    422,
                    "bad",
                    {},
                    BytesIO(b'{"detail":"Add lyrics before generating."}'),
                )

            with mock.patch.object(yue2_bridge, "urlopen", fake_open):
                with self.assertRaisesRegex(yue2_bridge.Yue2BridgeError, "Add lyrics") as caught:
                    yue2_bridge.Yue2Bridge(token_file=token).submit({})
            self.assertEqual(caught.exception.status_code, 422)

    def test_public_status_removes_local_folder_paths(self):
        class Bridge:
            def health(self):
                return {"generation": True, "model": "YuE2-3B", "sample_rate": 48000,
                        "decoder_profiles": [{"id": "joint-v9", "available": True,
                                              "nar_path": "/private/decoder.safetensors"}]}

            def loras(self):
                return {
                    "folder": "/private/loras",
                    "groups": [{"id": "a", "name": "Style", "checkpoints": []}],
                }

        status = yue2_bridge.public_status(Bridge())
        self.assertTrue(status["available"])
        self.assertTrue(status["decoderProfiles"][0]["available"])
        self.assertNotIn("/private", json.dumps(status))

    def test_training_route_authorizes_before_calling_service_and_confines_tracks(self):
        launch = Path(__file__).resolve().parents[1] / "app/launch.py"
        tree = ast.parse(launch.read_text())
        functions = {"yue2_training_submit", "yue2_training_cancel"}
        selected = []
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name in functions:
                clone = copy.deepcopy(node)
                clone.decorator_list = []
                selected.append(clone)
        self.assertEqual({node.name for node in selected}, functions)
        module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
        calls = []
        allowed = False

        class Bridge:
            def project_library(self, workspace):
                calls.append(("library", workspace))
                return {"tracks": [{"id": "owned", "status": "succeeded"}, {"id": "unfinished", "status": "running"}]}

            def submit_training(self, payload):
                calls.append(("submit", payload))
                return payload

            def require_training_job(self, job_id, workspace):
                calls.append(("lookup", job_id, workspace))
                if job_id != "owned-job":
                    raise yue2_bridge.Yue2BridgeError("not found", status_code=404)

            def cancel_training(self, job_id):
                calls.append(("cancel", job_id))
                return {"status": "cancel-requested"}

        def check_access(_request, workspace, permission):
            if not allowed:
                raise HTTPException(403, "Project access required")
            calls.append(("auth", workspace, permission))

        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "_request_project_workspace": lambda _request, workspace: workspace,
            "_require_project_access": check_access,
            "_yue2_bridge": lambda: Bridge(),
            "_raise_yue2_bridge_error": lambda error: (_ for _ in ()).throw(error),
        }
        exec(compile(module, str(launch), "exec"), namespace)

        class Request:
            def __init__(self, body):
                self.body = body

            async def json(self):
                return self.body

        submit = namespace["yue2_training_submit"]
        cancel = namespace["yue2_training_cancel"]
        body = {"workspace": "alpha", "project": "beta", "requestId": "train-0001", "tracks": [{"takeId": "owned"}]}
        with self.assertRaises(HTTPException) as denied:
            asyncio.run(submit(Request(body)))
        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(calls, [])
        allowed = True
        with self.assertRaises(HTTPException) as foreign:
            asyncio.run(submit(Request({**body, "tracks": [{"takeId": "foreign"}]})))
        self.assertEqual(foreign.exception.status_code, 404)
        self.assertFalse(any(call[0] == "submit" for call in calls))
        result = asyncio.run(submit(Request(body)))
        self.assertEqual(result["project"], "alpha")
        self.assertNotIn("workspace", result)
        with self.assertRaises(yue2_bridge.Yue2BridgeError):
            asyncio.run(cancel("foreign-job", Request({"workspace": "alpha"})))
        self.assertFalse(any(call[0] == "cancel" for call in calls))
        self.assertEqual(asyncio.run(cancel("owned-job", Request({"workspace": "alpha"})))["status"], "cancel-requested")

    def test_project_library_and_take_lookup_fail_closed(self):
        bridge = yue2_bridge.Yue2Bridge(token_file=Path("/unused"))
        bridge.library = lambda: {
            "tracks": [
                {"id": "owned", "project": "alpha"},
                {"id": "foreign", "project": "beta"},
            ]
        }
        self.assertEqual(bridge.project_library("alpha")["tracks"], [{"id": "owned", "project": "alpha"}])
        self.assertEqual(bridge.require_take("owned", "alpha")["id"], "owned")
        with self.assertRaisesRegex(yue2_bridge.Yue2BridgeError, "not found") as caught:
            bridge.require_take("foreign", "alpha")
        self.assertEqual(caught.exception.status_code, 404)

    def test_training_jobs_are_project_filtered_and_cancel_requires_match(self):
        bridge = yue2_bridge.Yue2Bridge(token_file=Path("/unused"))
        calls = []

        def service(path, **kwargs):
            calls.append((path, kwargs))
            return {"jobs": [
                {"id": "owned", "project": "alpha"},
                {"id": "foreign", "project": "beta"},
            ]} if path.startswith("/api/training?") else {"status": "cancel-requested"}

        bridge._request = service
        self.assertEqual(bridge.training_jobs("alpha")["jobs"], [{"id": "owned", "project": "alpha"}])
        self.assertEqual(bridge.require_training_job("owned", "alpha")["id"], "owned")
        with self.assertRaisesRegex(yue2_bridge.Yue2BridgeError, "not found") as caught:
            bridge.require_training_job("foreign", "alpha")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertTrue(all("project=alpha" in path for path, _ in calls))

    def test_training_status_is_boolean_and_keeps_host_paths_private(self):
        class Bridge:
            def health(self):
                return {"generation": True, "training": True, "training_root": "/private/corpus"}

            def loras(self):
                return {"groups": []}

        status = yue2_bridge.public_status(Bridge())
        self.assertTrue(status["training"])
        self.assertNotIn("/private", json.dumps(status))

    def test_unconfirmed_gpu_worker_blocks_new_work_but_keeps_training_jobs_visible(self):
        class Bridge:
            def health(self):
                return {"generation": True, "training": True, "gpu_supervision_lost": True}

            def loras(self):
                return {"groups": []}

        status = yue2_bridge.public_status(Bridge())
        self.assertFalse(status["available"])
        self.assertTrue(status["gpuBlocked"])
        self.assertTrue(status["training"])
        self.assertIn("check the local Sound/Vision service", status["message"])


if __name__ == "__main__":
    unittest.main()
