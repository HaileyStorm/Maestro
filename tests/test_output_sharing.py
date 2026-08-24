"""Offline contracts for revocable, single-output capability links."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

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

    @staticmethod
    def _share_revision(media: Path, sidecar: Path | None = None) -> str:
        digest = hashlib.sha256()
        for label, path in ((b"media\0", media), (b"sidecar\0", sidecar)):
            digest.update(label)
            if path is None or not path.exists():
                digest.update(b"missing\0")
                continue
            payload = path.read_bytes()
            digest.update(len(payload).to_bytes(16, "big", signed=False))
            digest.update(payload)
        return f"sha256:{digest.hexdigest()}"

    def _snapshot_helper(self, record: dict, directory: str, resolve):
        tree = ast.parse(self.source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_materialize_output_share_snapshot"
        )

        @contextmanager
        def reservation(*_args):
            yield

        manager = type(
            "Manager", (), {"resolve": lambda self, token: dict(record)},
        )()
        namespace = {
            "HTTPException": HTTPException,
            "hashlib": hashlib,
            "hmac": hmac,
            "os": os,
            "stat": stat,
            "_output_share_manager": lambda: manager,
            "_reserve_workspace_operations": reservation,
            "_existing_workspace_dir": lambda _workspace: directory,
            "_output_lineage_mutation_guard": reservation,
            "_resolve_shared_output": resolve,
            "_share_not_found": lambda: HTTPException(
                status_code=404, detail="Shared output not found",
            ),
        }
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(ROOT / "app" / "launch.py"), "exec"), namespace)
        return namespace["_materialize_output_share_snapshot"]

    def test_capability_stream_uses_verified_snapshot_for_exact_ranges(self):
        tree = ast.parse(self.source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.ClassDef)
            and item.name == "_PinnedOutputShareResponse"
        )
        payload = b"verified-old-bytes"
        snapshot_handles = []

        def materialize(_token):
            snapshot = tempfile.TemporaryFile(mode="w+b")
            snapshot.write(payload)
            snapshot.seek(0)
            snapshot_handles.append(snapshot)
            return snapshot, len(payload)

        namespace = {
            "Response": Response,
            "HTTPException": HTTPException,
            "asyncio": asyncio,
            "_materialize_output_share_snapshot": materialize,
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
        messages = []

        async def send(message):
            messages.append(message)

        asyncio.run(response(
            {"headers": [(b"range", b"bytes=9-11")]}, None, send,
        ))
        start = next(
            message for message in messages
            if message["type"] == "http.response.start"
        )
        headers = dict(start["headers"])
        body = b"".join(
            message.get("body", b"") for message in messages
            if message["type"] == "http.response.body"
        )
        self.assertEqual(start["status"], 206)
        self.assertEqual(headers[b"content-range"], b"bytes 9-11/18")
        self.assertEqual(headers[b"content-length"], b"3")
        self.assertEqual(body, b"old")
        self.assertTrue(snapshot_handles[0].closed)
        self.assertNotIn("b'\\x00' *", ast.unparse(node))

    def test_cancelled_snapshot_copy_closes_late_temporary_file(self):
        tree = ast.parse(self.source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.ClassDef)
            and item.name == "_PinnedOutputShareResponse"
        )
        entered = threading.Event()
        release = threading.Event()
        closed = threading.Event()

        class Snapshot:
            def close(self):
                closed.set()

        def materialize(_token):
            entered.set()
            release.wait(2)
            return Snapshot(), 0

        namespace = {
            "Response": Response,
            "HTTPException": HTTPException,
            "asyncio": asyncio,
            "_materialize_output_share_snapshot": materialize,
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

        async def scenario():
            task = asyncio.create_task(response(
                {"headers": []}, None, lambda _message: None,
            ))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            self.assertTrue(await asyncio.to_thread(closed.wait, 1))

        asyncio.run(scenario())

    def test_snapshot_materializes_stable_media_and_sidecar_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory, "final.mp4")
            sidecar = Path(directory, "final.meta.json")
            media.write_bytes(b"authorized-media-bytes")
            sidecar.write_text('{"explicit": false}', encoding="utf-8")
            record = {
                "workspace": "project", "filename": media.name,
                "revision": self._share_revision(media, sidecar),
                "media_type": "video/mp4",
            }

            def resolve(_token, *, verify_revision=True):
                self.assertFalse(verify_revision)
                return dict(record), str(media)

            helper = self._snapshot_helper(record, directory, resolve)
            snapshot, size = helper("token")
            try:
                self.assertEqual(size, len(b"authorized-media-bytes"))
                self.assertEqual(snapshot.read(), b"authorized-media-bytes")
            finally:
                snapshot.close()

    def test_unguarded_replacement_before_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory, "final.mp4")
            replacement = Path(directory, "replacement.mp4")
            media.write_bytes(b"verified-old-bytes")
            replacement.write_bytes(b"replacement-bytes")
            record = {
                "workspace": "project", "filename": media.name,
                "revision": self._share_revision(media),
                "media_type": "video/mp4",
            }

            def resolve(_token, *, verify_revision=True):
                self.assertFalse(verify_revision)
                os.replace(replacement, media)
                return dict(record), str(media)

            helper = self._snapshot_helper(record, directory, resolve)
            with self.assertRaises(HTTPException) as raised:
                helper("token")
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(media.read_bytes(), b"replacement-bytes")

    def test_in_place_mutation_during_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory, "final.mp4")
            media.write_bytes(b"a" * (1024 * 1024) + b"b" * 128)
            record = {
                "workspace": "project", "filename": media.name,
                "revision": self._share_revision(media),
                "media_type": "video/mp4",
            }

            def resolve(_token, *, verify_revision=True):
                self.assertFalse(verify_revision)
                return dict(record), str(media)

            helper = self._snapshot_helper(record, directory, resolve)
            from services import win_safe_files

            real_open = win_safe_files._open_share_delete
            mutated = False

            class MutatingHandle:
                def __init__(self, handle):
                    self.handle = handle

                def fileno(self):
                    return self.handle.fileno()

                def read(self, size=-1):
                    nonlocal mutated
                    chunk = self.handle.read(size)
                    if not mutated and chunk:
                        mutated = True
                        with open(media, "r+b", buffering=0) as writer:
                            writer.seek(1024 * 1024)
                            writer.write(b"c" * 128)
                            os.fsync(writer.fileno())
                    return chunk

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    self.handle.close()

            def mutating_open(path):
                opened = real_open(path)
                return MutatingHandle(opened) if path == str(media) else opened

            with mock.patch(
                "services.win_safe_files._open_share_delete",
                side_effect=mutating_open,
            ):
                with self.assertRaises((HTTPException, OSError)):
                    helper("token")
            self.assertTrue(mutated)

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
        self.assertIn("It does not grant project access", self.card)
        self.assertIn("this link itself may not open off your network", self.card)
        self.assertIn("navigator.share", self.card)

    def test_share_mutations_reject_non_object_json_through_the_bounded_parser(self):
        create = self.source[
            self.source.index('async def create_output_share'):
            self.source.index('@api.delete("/api/v1/output-shares")')
        ]
        revoke = self.source[
            self.source.index('async def revoke_output_share'):
            self.source.index('@api.get("/api/v1/output-shares/{token}/media"')
        ]
        self.assertIn("body = await _account_request_body(request)", create)
        self.assertIn("body = await _account_request_body(request)", revoke)


if __name__ == "__main__":
    unittest.main()
