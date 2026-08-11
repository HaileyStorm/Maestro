"""Offline contracts for revocable, single-output capability links."""

from __future__ import annotations

import ast
import asyncio
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response

from app.services.output_access import OutputShareManager


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class OutputShareManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / "storage" / "output-shares.json"
        self.clock_value = 1000.0
        self.manager = OutputShareManager(
            str(self.store), b"s" * 32, clock=lambda: self.clock_value,
        )

    def create(self, revision: str = "media.sidecar") -> dict:
        return self.manager.create(
            workspace="private-project",
            filename="final.mp4",
            revision=revision,
            media_type="video/mp4",
            explicit=True,
        )

    def test_create_is_stable_and_store_does_not_contain_bearer_token(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first["token"], second["token"])
        self.assertEqual(self.manager.resolve(first["token"])["filename"], "final.mp4")

        persisted = self.store.read_text(encoding="utf-8")
        self.assertNotIn(first["token"], persisted)
        self.assertNotIn(first["token"].split(".", 1)[1], persisted)
        self.assertEqual(json.loads(persisted)["version"], 1)
        if os.name != "nt":
            self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)

    def test_invalid_and_tampered_tokens_fail_closed(self):
        token = self.create()["token"]
        self.assertIsNone(self.manager.resolve(""))
        self.assertIsNone(self.manager.resolve("not-a-token"))
        self.assertIsNone(self.manager.resolve(token[:-1] + ("0" if token[-1] != "0" else "1")))
        other_secret = OutputShareManager(str(self.store), b"x" * 32)
        self.assertIsNone(other_secret.resolve(token))

    def test_new_revision_revokes_old_link_and_delete_action_revokes_current(self):
        old = self.create("revision-one")
        self.clock_value += 1
        new = self.create("revision-two")
        self.assertNotEqual(old["token"], new["token"])
        self.assertIsNone(self.manager.resolve(old["token"]))
        self.assertEqual(self.manager.resolve(new["token"])["revision"], "revision-two")
        self.assertEqual(
            self.manager.revoke(workspace="private-project", filename="final.mp4"),
            1,
        )
        self.assertIsNone(self.manager.resolve(new["token"]))

    def test_revoked_identical_revision_gets_a_new_capability(self):
        old = self.create("identical-content")
        self.assertEqual(
            self.manager.revoke(
                workspace="private-project", filename="final.mp4",
            ),
            1,
        )
        self.clock_value += 1
        replacement = self.create("identical-content")
        self.assertNotEqual(old["token"], replacement["token"])
        self.assertIsNone(self.manager.resolve(old["token"]))
        self.assertIsNotNone(self.manager.resolve(replacement["token"]))

    def test_corrupt_store_never_reanimates_a_link(self):
        token = self.create()["token"]
        self.store.write_text('{"version": 1, "shares": "broken"}', encoding="utf-8")
        self.assertIsNone(self.manager.resolve(token))

    def test_workspace_revoke_invalidates_every_active_link_and_never_revives(self):
        first = self.create("first")
        second = self.manager.create(
            workspace="private-project",
            filename="other.png",
            revision="second",
            media_type="image/png",
            explicit=False,
        )
        unrelated = self.manager.create(
            workspace="other-project",
            filename="final.mp4",
            revision="third",
            media_type="video/mp4",
            explicit=False,
        )

        self.assertEqual(self.manager.revoke_workspace("private-project"), 2)
        self.assertIsNone(self.manager.resolve(first["token"]))
        self.assertIsNone(self.manager.resolve(second["token"]))
        self.assertIsNotNone(self.manager.resolve(unrelated["token"]))
        self.assertEqual(self.manager.revoke_workspace("private-project"), 0)


class OutputShareRouteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "app" / "launch.py").read_text(encoding="utf-8")
        cls.client = (ROOT / "ui" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
        cls.card = (
            ROOT / "ui" / "src" / "components" / "MainContent" / "MediaFeedItem.tsx"
        ).read_text(encoding="utf-8")

    def test_creation_is_authorized_but_capability_read_does_not_create_session(self):
        self.assertIn("_require_authorized_output(\n                request, workspace, name,", self.source)
        self.assertIn("with _reserve_workspace_operations(workspace):", self.source)
        self.assertIn("with _output_lineage_mutation_guard(out_dir):", self.source)
        capability_start = self.source.index("    capability_read = ")
        capability_read = self.source[
            capability_start:self.source.index(
                "    secret = _session_secret()", capability_start,
            )
        ]
        self.assertIn('request.url.path.startswith("/share/")', capability_read)
        self.assertIn('request.state.maestro_session_id = ""', capability_read)
        self.assertIn('request.state.maestro_account_session_id = ""', capability_read)
        self.assertIn(
            "return await _call_next_with_recovery_no_store(request, call_next)",
            capability_read,
        )
        self.assertNotIn("_set_maestro_session_cookie", capability_read)

    def test_bearer_has_dedicated_read_only_routes_and_no_metadata_endpoint(self):
        self.assertIn('@api.get("/share/{token}"', self.source)
        self.assertIn('@api.get("/api/v1/output-shares/{token}/media"', self.source)
        self.assertNotIn('/api/v1/output-shares/{token}/metadata', self.source)
        self.assertIn('Content-Security-Policy', self.source)
        self.assertIn('Referrer-Policy', self.source)
        self.assertIn('"/share/", "/api/v1/output-shares/"', self.source)

    def test_content_revision_and_direct_basename_are_rechecked_on_every_read(self):
        self.assertIn("is_safe_direct_basename(filename)", self.source)
        self.assertIn("safe_direct_file_under(out_dir, filename)", self.source)
        self.assertIn("_output_share_revision(filepath, out_dir, filename)", self.source)
        self.assertIn("hashlib.sha256()", self.source)
        self.assertIn('detail="Shared output not found"', self.source)

    def test_capability_stream_pins_verified_inode_before_releasing_locks(self):
        tree = ast.parse(self.source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.ClassDef)
            and item.name == "_PinnedOutputShareResponse"
        )

        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory, "final.mp4")
            replacement = Path(directory, "replacement.mp4")
            media.write_bytes(b"verified-old-bytes")
            replacement.write_bytes(b"replacement-bytes")
            record = {
                "workspace": "project", "filename": media.name,
                "revision": "revision", "media_type": "video/mp4",
            }
            lineage_lock = threading.RLock()
            resolved = threading.Event()
            mutation_done = threading.Event()

            @contextmanager
            def reservation(*_args):
                yield

            @contextmanager
            def lineage_guard(*_args):
                with lineage_lock:
                    yield

            def resolve(_token):
                resolved.set()
                # Give the replacement thread time to block on the same lock.
                time.sleep(0.05)
                return dict(record), str(media)

            manager = type("Manager", (), {"resolve": lambda self, token: dict(record)})()
            namespace = {
                "Response": Response,
                "HTTPException": HTTPException,
                "hmac": hmac,
                "os": os,
                "_output_share_manager": lambda: manager,
                "_reserve_workspace_operations": reservation,
                "_existing_workspace_dir": lambda _workspace: directory,
                "_output_lineage_mutation_guard": lineage_guard,
                "_resolve_shared_output": resolve,
                "_share_not_found": lambda: HTTPException(
                    status_code=404, detail="Shared output not found",
                ),
            }
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(ROOT / "app" / "launch.py"), "exec"), namespace)
            response = namespace["_PinnedOutputShareResponse"](
                "token", "video/mp4",
            )

            def mutate():
                self.assertTrue(resolved.wait(1))
                with lineage_lock:
                    os.replace(replacement, media)
                mutation_done.set()

            mutation = threading.Thread(target=mutate)
            mutation.start()
            messages = []

            async def send(message):
                if message["type"] == "http.response.start":
                    await asyncio.to_thread(mutation_done.wait, 1)
                messages.append(message)

            asyncio.run(response({"headers": []}, None, send))
            mutation.join(1)
            self.assertTrue(mutation_done.is_set())
            body = b"".join(
                message.get("body", b"") for message in messages
                if message["type"] == "http.response.body"
            )
            self.assertEqual(body, b"verified-old-bytes")
            self.assertEqual(media.read_bytes(), b"replacement-bytes")

    def test_only_gallery_media_can_be_shared_and_lifecycle_mutations_revoke(self):
        self.assertIn("not in _GALLERY_MEDIA_EXTENSIONS", self.source)
        self.assertIn(
            "_revoke_output_shares(source_workspace, unique_names)",
            self.source,
        )
        self.assertIn("_revoke_output_shares(workspace, unique_names)", self.source)
        self.assertIn("_revoke_output_shares(project_id, [filename])", self.source)
        self.assertIn("_output_share_manager().revoke_workspace(name)", self.source)
        delete = self.source[self.source.index("def delete_output("):]
        self.assertLess(
            delete.index("_revoke_output_shares(selected_workspace, [name])"),
            delete.index('return {"deleted": name}'),
        )
        revoke = self.source[
            self.source.index("async def revoke_output_share"):
            self.source.index('@api.get("/api/v1/output-shares/{token}/media"')
        ]
        self.assertNotIn("_require_authorized_output", revoke)
        self.assertIn("_output_share_manager().revoke", revoke)

    def test_public_origin_is_verified_runtime_cloudflare_not_forwarded_local(self):
        self.assertIn("Return only the public origin verified during this launch", self.source)
        self.assertIn("_is_workers_dev_origin(origin)", self.source)
        self.assertIn('body.get("stable_verified") is True', self.source)
        self.assertIn("_is_quick_tunnel_origin(quick_tunnel)", self.source)
        self.assertIn("if _request_is_cloudflare_remote(request):", self.source)
        self.assertIn("if not _approved_local_origin(origin)", self.source)

    def test_local_gallery_exposes_create_copy_and_revoke_controls(self):
        self.assertIn("export async function createOutputShare", self.client)
        self.assertIn("export async function revokeOutputShare", self.client)
        self.assertIn("createOutputShare(file.name, file.workspace, file.revision)", self.card)
        self.assertIn("revokeOutputShare(file.name, file.workspace)", self.card)
        self.assertIn("it also works through your Cloudflare address", self.card)


if __name__ == "__main__":
    unittest.main()
