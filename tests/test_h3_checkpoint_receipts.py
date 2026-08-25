from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_checkpoint_receipts as receipts  # noqa: E402


def _process_verify_checkpoint(
    checkpoint: str, state: str, digest: str, size: int, ready, output,
) -> None:
    ready.wait(10)
    try:
        result = receipts.verify_checkpoint_integrity(
            checkpoint,
            expected_sha256=digest,
            expected_size=size,
            compatibility="suspected_compatible_base",
            receipt_root=state,
        )
        output.put(("ok", result["receipt_reused"]))
    except Exception as error:  # pragma: no cover - reported to parent test
        output.put(("error", f"{type(error).__name__}: {error}"))


class H3CheckpointReceiptTests(unittest.TestCase):
    def _verify(self, checkpoint: Path, state: Path, **overrides):
        payload = checkpoint.read_bytes()
        arguments = {
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "expected_size": len(payload),
            "compatibility": "suspected_compatible_base",
            "receipt_root": state,
        }
        arguments.update(overrides)
        return receipts.verify_checkpoint_integrity(checkpoint, **arguments)

    def test_first_hash_then_exact_receipt_reuse_and_private_path_free_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "checkpoint.safetensors"
            checkpoint.write_bytes(b"immutable-checkpoint")
            state = root / "receipts"
            with mock.patch.object(
                receipts, "_stream_sha256", wraps=receipts._stream_sha256,
            ) as stream:
                first = self._verify(checkpoint, state)
                second = self._verify(checkpoint, state)
            self.assertEqual(stream.call_count, 1)
            self.assertFalse(first["receipt_reused"])
            self.assertTrue(second["receipt_reused"])
            self.assertEqual(stat_mode(state), 0o700)
            receipt_path = next(state.glob("*.json"))
            lock_path = next(state.glob("*.lock"))
            self.assertEqual(stat_mode(receipt_path), 0o600)
            self.assertEqual(stat_mode(lock_path), 0o600)
            serialized = receipt_path.read_text(encoding="utf-8")
            record = json.loads(serialized)
            self.assertEqual(record.keys(), receipts._RECEIPT_KEYS)
            self.assertNotIn(str(checkpoint), serialized)
            self.assertNotIn(str(root), serialized)
            for private_key in ("path", "uid", "ino", "dev", "mtime_ns", "ctime_ns"):
                self.assertNotIn(private_key, first)

    def test_same_size_replacement_invalidates_and_rehashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "checkpoint.safetensors"
            checkpoint.write_bytes(b"same-content")
            state = root / "receipts"
            self._verify(checkpoint, state)
            replacement = root / "replacement.tmp"
            replacement.write_bytes(b"same-content")
            os.replace(replacement, checkpoint)
            with mock.patch.object(
                receipts, "_stream_sha256", wraps=receipts._stream_sha256,
            ) as stream:
                result = self._verify(checkpoint, state)
            self.assertEqual(stream.call_count, 1)
            self.assertFalse(result["receipt_reused"])

    def test_ctime_only_mode_change_invalidates_and_rehashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "checkpoint.safetensors"
            checkpoint.write_bytes(b"same-content")
            state = root / "receipts"
            self._verify(checkpoint, state)
            original_mtime = checkpoint.stat().st_mtime_ns
            checkpoint.chmod(0o600)
            self.assertEqual(checkpoint.stat().st_mtime_ns, original_mtime)
            with mock.patch.object(
                receipts, "_stream_sha256", wraps=receipts._stream_sha256,
            ) as stream:
                result = self._verify(checkpoint, state)
            self.assertEqual(stream.call_count, 1)
            self.assertFalse(result["receipt_reused"])

    def test_role_contract_change_invalidates_and_rehashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "checkpoint.safetensors"
            checkpoint.write_bytes(b"same-content")
            state = root / "receipts"
            self._verify(checkpoint, state)
            with mock.patch.object(
                receipts, "_stream_sha256", wraps=receipts._stream_sha256,
            ) as stream:
                changed = self._verify(checkpoint, state, role="vae")
            self.assertEqual(stream.call_count, 1)
            self.assertEqual(changed["role"], "vae")
            self.assertFalse(changed["receipt_reused"])

    def test_tampered_oversized_mode_and_role_receipts_are_rejected_then_recovered(self):
        for corruption in ("tampered", "oversized", "mode", "path", "role"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                checkpoint = root / "checkpoint.safetensors"
                checkpoint.write_bytes(b"checkpoint-data")
                state = root / "receipts"
                self._verify(checkpoint, state)
                receipt_path = next(state.glob("*.json"))
                if corruption == "tampered":
                    receipt_path.write_text("{}", encoding="utf-8")
                elif corruption == "oversized":
                    receipt_path.write_bytes(b"{" + b" " * (receipts.CHECKPOINT_RECEIPT_MAX_BYTES + 1))
                elif corruption == "mode":
                    receipt_path.chmod(0o644)
                else:
                    record = json.loads(receipt_path.read_text(encoding="utf-8"))
                    if corruption == "path":
                        record["path_digest"] = "0" * 64
                    else:
                        record["role"] = "vae"
                    receipt_path.write_text(json.dumps(record), encoding="utf-8")
                with mock.patch.object(
                    receipts, "_stream_sha256", wraps=receipts._stream_sha256,
                ) as stream:
                    result = self._verify(checkpoint, state)
                self.assertEqual(stream.call_count, 1)
                self.assertFalse(result["receipt_reused"])
                self.assertEqual(stat_mode(next(state.glob("*.json"))), 0o600)

    def test_symlink_nonregular_and_wrong_owner_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "checkpoint.safetensors"
            checkpoint.write_bytes(b"checkpoint")
            link = root / "link.safetensors"
            link.symlink_to(checkpoint)
            state = root / "receipts"
            with self.assertRaises(receipts.H3CheckpointIntegrityError):
                self._verify(link, state)
            real_state = root / "real-receipts"
            real_state.mkdir()
            linked_state = root / "linked-receipts"
            linked_state.symlink_to(real_state, target_is_directory=True)
            with self.assertRaises(receipts.H3CheckpointIntegrityError):
                self._verify(checkpoint, linked_state)
            directory = root / "directory.safetensors"
            directory.mkdir()
            with self.assertRaises(receipts.H3CheckpointIntegrityError):
                receipts.verify_checkpoint_integrity(
                    directory,
                    expected_sha256="0" * 64,
                    expected_size=1,
                    compatibility="suspected_compatible_base",
                    receipt_root=state,
                )
            with (
                mock.patch.object(receipts, "_same_owner", return_value=False),
                self.assertRaises(receipts.H3CheckpointIntegrityError),
            ):
                self._verify(checkpoint, state)

    def test_mutation_during_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "checkpoint.safetensors"
            checkpoint.write_bytes(b"0123456789")
            state = root / "receipts"
            expected = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            original_stream = receipts._stream_sha256

            def mutate(descriptor: int) -> str:
                digest = original_stream(descriptor)
                checkpoint.write_bytes(b"abcdefghij")
                return digest

            with (
                mock.patch.object(receipts, "_stream_sha256", side_effect=mutate),
                self.assertRaises(receipts.H3CheckpointIntegrityError),
            ):
                receipts.verify_checkpoint_integrity(
                    checkpoint,
                    expected_sha256=expected,
                    expected_size=10,
                    compatibility="suspected_compatible_base",
                    receipt_root=state,
                )

    @unittest.skipUnless(os.name == "posix", "POSIX cross-process lock contract")
    def test_cross_process_initial_hash_is_serialized_by_one_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "checkpoint.safetensors"
            checkpoint.write_bytes(b"cross-process-checkpoint" * 4096)
            state = root / "receipts"
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            context = multiprocessing.get_context("fork")
            ready = context.Event()
            output = context.Queue()
            processes = [
                context.Process(
                    target=_process_verify_checkpoint,
                    args=(
                        str(checkpoint), str(state), digest,
                        checkpoint.stat().st_size, ready, output,
                    ),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            ready.set()
            results = [output.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(timeout=15)
                self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(results), [("ok", False), ("ok", True)])


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
