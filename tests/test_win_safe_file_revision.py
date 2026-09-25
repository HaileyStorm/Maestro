"""An Editor preview must stream the exact output that was validated."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import win_safe_files
from services.win_safe_files import file_revision_identity, share_delete_file_response


class TestValidatedOutputStream(unittest.IsolatedAsyncioTestCase):
    async def _send(self, response):
        messages = []

        async def send(message):
            messages.append(message)

        await response._stream_response({"type": "http", "headers": []}, send)
        return messages

    async def test_replaced_file_is_rejected_before_headers_or_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "clip.mp4")
            path.write_bytes(b"original")
            identity = file_revision_identity(str(path))
            response = share_delete_file_response(
                str(path), expected_file_identity=identity,
            )
            replacement = Path(folder, "replacement.mp4")
            replacement.write_bytes(b"newbytes")
            os.utime(replacement, ns=(identity[3], identity[3]))
            os.replace(replacement, path)
            messages = await self._send(response)
            self.assertEqual(messages[0]["status"], 409)
            self.assertNotIn(b"newbytes", b"".join(
                message.get("body", b"") for message in messages
            ))

    async def test_sidecar_change_is_rejected_and_unchanged_file_streams(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "clip.mp4")
            sidecar = Path(folder, "clip.meta.json")
            path.write_bytes(b"original")
            sidecar.write_bytes(b'{"private":false}')
            identity = file_revision_identity(str(path))
            sidecar_identity = file_revision_identity(str(sidecar))
            response = share_delete_file_response(
                str(path), expected_file_identity=identity,
                expected_sidecar=(str(sidecar), sidecar_identity),
            )
            intact = await self._send(response)
            self.assertEqual(intact[0]["status"], 200)
            self.assertEqual(b"".join(m.get("body", b"") for m in intact), b"original")

            sidecar.write_bytes(b'{"private":true}')
            stale = await self._send(response)
            self.assertEqual(stale[0]["status"], 409)
            self.assertNotIn(b"original", b"".join(m.get("body", b"") for m in stale))

    @unittest.skipIf(os.name == "nt", "revision-bound Windows handle denies concurrent writes")
    async def test_in_place_rewrite_during_read_never_sends_new_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "clip.mp4")
            path.write_bytes(b"original")
            identity = file_revision_identity(str(path))
            response = share_delete_file_response(
                str(path), expected_file_identity=identity,
            )
            real_open = win_safe_files._open_share_delete

            class RewritingHandle:
                def __init__(self, handle):
                    self.handle = handle

                def fileno(self):
                    return self.handle.fileno()

                def read(self, size=-1):
                    with open(path, "r+b", buffering=0) as writer:
                        writer.write(b"newbytes")
                        os.fsync(writer.fileno())
                    self.handle.seek(0)
                    return self.handle.read(size)

                def close(self):
                    self.handle.close()

            def mutating_open(file_path, *, allow_write=True):
                return RewritingHandle(real_open(file_path, allow_write=allow_write))

            messages = []

            async def send(message):
                messages.append(message)

            with mock.patch.object(win_safe_files, "_open_share_delete", side_effect=mutating_open):
                with self.assertRaisesRegex(OSError, "changed while streaming"):
                    await response._stream_response({"type": "http", "headers": []}, send)
            self.assertEqual(messages[0]["status"], 200)
            self.assertNotIn(b"newbytes", b"".join(m.get("body", b"") for m in messages))


if __name__ == "__main__":
    unittest.main()
