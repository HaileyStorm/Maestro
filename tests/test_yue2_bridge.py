import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
