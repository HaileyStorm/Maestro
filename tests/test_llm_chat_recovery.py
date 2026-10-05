"""Durable Chat finality, private byte-exact results and storage failure proof."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock
import uuid

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from services.llm_chat_recovery import (  # noqa: E402
    ChatRecoveryStore, ChatRecoveryError, ChatRecoveryConflict,
    ChatRecoveryCapacityError, ChatRecoveryCorruptionError, MAX_RESULT_BYTES,
)

SECRET = b"chat-recovery-test-server-secret"
SCOPE = {"owner_key": "ACCOUNT+SESSION PRIVATE", "project_key": "PROJECT INCARNATION PRIVATE"}
DIGEST = hashlib.sha256(b"PRIVATE PROMPT INPUT").hexdigest()
CLAIM = {**SCOPE, "execution_claim": "PRIVATE EXECUTION CLAIM", "epoch": "process-1"}
RESULT = {"text": "PRIVATE ANSWER\n成人 violent controversial 🦊\r\n", "model_id": "local-model", "guide_ids": ["guide-model"]}


def _process_bind(root, request_id, barrier, results):
    store = ChatRecoveryStore(root, SECRET)
    store.initialize_epoch("process-1")
    barrier.wait(timeout=10)
    results.put(store.bind(request_id, request_digest=DIGEST, **CLAIM)[0])


def _process_fifo_startup(root, results):
    try:
        ChatRecoveryStore(root, SECRET).initialize_epoch("process-2")
    except ChatRecoveryError:
        results.put("rejected")
    else:
        results.put("unexpected acceptance")


class ChatRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "chat"
        self.now = 1000.0
        self.store = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
        self.store.initialize_epoch("process-1")
        self.request_id = str(uuid.uuid4())

    def bind(self, request_id=None, **overrides):
        return self.store.bind(request_id or self.request_id, request_digest=DIGEST, **{**CLAIM, **overrides})

    def test_restart_full_answer_exact_bytes_and_copy_on_read(self):
        result = {**RESULT, "text": RESULT["text"] + "x" * (700 * 1024),
                  "stats": {"elapsed_seconds": 1.5, "generated_tokens": 44, "live_tps": None}}
        self.assertTrue(self.bind()[0])
        receipt = self.store.complete(self.request_id, result=result, **CLAIM)
        self.assertEqual(receipt["status"], "completed")
        result["text"] = "mutated caller"
        restored = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
        self.assertEqual(restored.initialize_epoch("process-2"), 0)
        answer = restored.read_result(self.request_id, **SCOPE, request_digest=DIGEST,
                                      result_reference=receipt["result_reference"])
        self.assertEqual(answer["text"], RESULT["text"] + "x" * (700 * 1024))
        self.assertEqual(answer["guide_ids"], RESULT["guide_ids"])
        answer["guide_ids"].append("caller mutation")
        self.assertEqual(restored.read_result(self.request_id, **SCOPE)["guide_ids"], RESULT["guide_ids"])
        ledger = self.store.metadata_path.read_text()
        for private in [RESULT["text"].splitlines()[0], SCOPE["owner_key"], SCOPE["project_key"],
                        CLAIM["execution_claim"], "PRIVATE PROMPT INPUT", "local-model", "guide-model"]:
            self.assertNotIn(private, ledger)
        self.assertEqual(stat.S_IMODE(self.store.metadata_path.stat().st_mode), 0o600)

    def test_scope_and_digest_do_not_expose_foreign_receipt_or_result(self):
        self.bind()
        self.store.complete(self.request_id, result=RESULT, **CLAIM)
        for scope in [{**SCOPE, "owner_key": "other-account-or-session"}, {**SCOPE, "project_key": "other-incarnation"}]:
            self.assertIsNone(self.store.lookup(self.request_id, **scope, request_digest="0" * 64))
            self.assertIsNone(self.store.read_result(self.request_id, **scope))
            with self.assertRaises(ChatRecoveryConflict):
                self.bind(**scope)
        with self.assertRaises(ChatRecoveryConflict):
            self.store.lookup(self.request_id, **SCOPE, request_digest="0" * 64)
        with self.assertRaises(ChatRecoveryConflict):
            self.store.read_result(self.request_id, **SCOPE, result_reference={"path": "foreign"})

    def test_startup_gate_and_interrupted_execution_cannot_resend_or_finish(self):
        unopened = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
        with self.assertRaises(ChatRecoveryError):
            unopened.lookup(self.request_id, **SCOPE)
        self.bind()
        self.now += 2000  # active records are never retired by TTL
        restored = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
        self.assertEqual(restored.initialize_epoch("process-2"), 1)
        self.assertEqual(restored.initialize_epoch("process-2"), 0)
        receipt = restored.lookup(self.request_id, **SCOPE)
        self.assertEqual(receipt["status"], "interrupted")
        self.assertEqual(receipt["failure_code"], "interrupted")
        self.assertIsNone(restored.read_result(self.request_id, **SCOPE))
        fresh, repeated = restored.bind(self.request_id, request_digest=DIGEST,
                                       **{**CLAIM, "epoch": "process-2", "execution_claim": "successor"})
        self.assertFalse(fresh)
        self.assertEqual(repeated, receipt)
        with self.assertRaises(ChatRecoveryError):
            self.store.complete(self.request_id, result=RESULT, **CLAIM)
        with self.assertRaises(ChatRecoveryConflict):
            restored.complete(self.request_id, result=RESULT, **{**CLAIM, "epoch": "process-2"})
        self.assertFalse(tuple(self.store._results._files.results_root.iterdir()))

    def test_uuid_forms_share_one_binding_and_same_epoch_initialization_keeps_active_work(self):
        hyphenated = "a4c31c0e-2f85-4b73-91e0-68a8b90f7ce2"
        compact = uuid.UUID(hyphenated).hex
        fresh, receipt = self.bind(hyphenated)
        self.assertTrue(fresh)
        self.assertEqual(receipt["request_id"], compact)
        self.assertFalse(self.bind(compact)[0])
        before = self.store.metadata_path.read_bytes()
        self.assertEqual(self.store.initialize_epoch("process-1"), 0)
        self.assertEqual(self.store.metadata_path.read_bytes(), before)
        self.assertEqual(self.store.lookup(compact, **SCOPE)["status"], "running")
        for alias in [hyphenated.upper(), compact.upper(), "{" + hyphenated + "}", "urn:uuid:" + hyphenated]:
            with self.assertRaises(ValueError):
                self.bind(alias)
        restored = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
        self.assertEqual(restored.initialize_epoch("process-2"), 1)
        successor_id = uuid.uuid4().hex
        restored.bind(successor_id, request_digest=DIGEST, **{**CLAIM, "epoch": "process-2"})
        with self.assertRaises(ChatRecoveryError):
            self.store.initialize_epoch("process-1")
        self.assertEqual(restored.lookup(successor_id, **SCOPE)["status"], "running")

    def test_stale_claim_and_terminal_idempotency_preserve_answer(self):
        self.bind()
        with self.assertRaises(ChatRecoveryConflict):
            self.store.complete(self.request_id, result=RESULT, **{**CLAIM, "execution_claim": "foreign"})
        self.assertFalse(tuple(self.store._results._files.results_root.iterdir()))
        completed = self.store.complete(self.request_id, result=RESULT, **CLAIM)
        self.assertEqual(self.store.complete(self.request_id, result={**RESULT, "text": "replacement"}, **CLAIM), completed)
        self.assertEqual(self.store.fail(self.request_id, **CLAIM), completed)
        self.assertEqual(self.store.cancel(self.request_id, **CLAIM), completed)
        self.assertEqual(self.store.read_result(self.request_id, **SCOPE), RESULT)
        self.assertEqual(len(tuple(self.store._results._files.results_root.iterdir())), 1)

    def test_cancelled_and_failed_are_final_without_exception_text(self):
        self.bind()
        cancelled = self.store.cancel(self.request_id, **CLAIM)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.store.complete(self.request_id, result=RESULT, **CLAIM), cancelled)
        next_id = str(uuid.uuid4())
        self.bind(next_id)
        failed = self.store.fail(next_id, **CLAIM)
        self.assertEqual(failed["failure_code"], "execution_failed")
        self.assertFalse(self.bind(next_id)[0])
        with self.assertRaises(ValueError):
            self.store.fail(next_id, failure_code="PRIVATE EXCEPTION TEXT", **CLAIM)
        self.assertFalse(tuple(self.store._results._files.results_root.iterdir()))

    def test_threads_and_separate_processes_admit_exactly_once(self):
        barrier = threading.Barrier(4)
        def attempt(_):
            store = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
            store.initialize_epoch("process-1")
            barrier.wait(timeout=10)
            return store.bind(self.request_id, request_digest=DIGEST, **CLAIM)[0]
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(attempt, range(4))), 1)
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(3)
        results = context.Queue()
        next_id = str(uuid.uuid4())
        processes = [context.Process(target=_process_bind, args=(str(self.root), next_id, barrier, results)) for _ in range(3)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=15)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(results.get(timeout=3) for _ in processes), 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                process.join(timeout=3)
            results.close()

    def test_terminal_only_capacity_and_expiry_are_bounded(self):
        store = ChatRecoveryStore(self.root, SECRET, max_records=2, clock=lambda: self.now)
        store.initialize_epoch("process-1")
        ids = [str(uuid.uuid4()) for _ in range(3)]
        for request_id in ids[:2]:
            store.bind(request_id, request_digest=DIGEST, **CLAIM)
        self.now += 5000
        with self.assertRaises(ChatRecoveryCapacityError):
            store.bind(ids[2], request_digest=DIGEST, **CLAIM)
        self.assertIsNotNone(store.lookup(ids[0], **SCOPE))
        store.complete(ids[0], result=RESULT, **CLAIM)
        old_ref = store.lookup(ids[0], **SCOPE)["result_reference"]
        store.bind(ids[2], request_digest=DIGEST, **CLAIM)
        self.assertIsNone(store.lookup(ids[0], **SCOPE))
        self.assertFalse((store._results._files.root / old_ref["path"]).exists())
        self.assertEqual(len(json.loads(store.metadata_path.read_text())["records"]), 2)
        store.fail(ids[1], **CLAIM)
        self.now += 901
        self.assertIsNone(store.lookup(ids[1], **SCOPE))
        restored = ChatRecoveryStore(self.root, SECRET, max_records=2, clock=lambda: self.now)
        self.assertEqual(restored.initialize_epoch("process-2"), 1)
        self.assertEqual(len(json.loads(store.metadata_path.read_text())["records"]), 1)

    def test_retirement_preserves_bytes_changed_after_integrity_read(self):
        store = ChatRecoveryStore(self.root, SECRET, max_records=1, clock=lambda: self.now)
        store.initialize_epoch("process-1")
        store.bind(self.request_id, request_digest=DIGEST, **CLAIM)
        completed = store.complete(self.request_id, result=RESULT, **CLAIM)
        path = store._results._files.root / completed["result_reference"]["path"]
        changed = path.read_bytes().replace(b"PRIVATE ANSWER", b"CHANGED ANSWER")
        original_read = store._read_final
        reads = 0
        def change_after_read(*args):
            nonlocal reads
            answer = original_read(*args)
            reads += 1
            if reads == 2:
                before = path.stat()
                path.write_bytes(changed)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            return answer
        with mock.patch.object(store, "_read_final", side_effect=change_after_read):
            with self.assertRaises(ChatRecoveryCorruptionError):
                store.bind(uuid.uuid4().hex, request_digest=DIGEST, **CLAIM)
        self.assertEqual(reads, 2)
        self.assertEqual(path.read_bytes(), changed)
        self.assertIsNone(store.read_result(self.request_id, **SCOPE))

    def test_corrupt_ledger_never_becomes_empty_or_overwritten(self):
        self.bind()
        original = self.store.metadata_path.read_bytes()
        for raw in [b"broken json", original.replace(b'"running"', b'"completed"'), b"{}"]:
            with self.subTest(raw=raw[:10]):
                self.store.metadata_path.write_bytes(raw)
                restored = ChatRecoveryStore(self.root, SECRET)
                with self.assertRaises(ChatRecoveryCorruptionError):
                    restored.initialize_epoch("process-2")
                self.assertEqual(self.store.metadata_path.read_bytes(), raw)
        self.store.metadata_path.write_bytes(original)
        self.store.metadata_path.unlink()
        restored = ChatRecoveryStore(self.root, SECRET)
        with self.assertRaises(ChatRecoveryCorruptionError):
            restored.initialize_epoch("process-2")
        self.assertFalse(self.store.metadata_path.exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO guard requires POSIX")
    def test_fifo_ledger_fails_promptly_in_owned_process_without_removing_fifo(self):
        self.bind()
        saved = self.root / "saved-ledger.json"
        self.store.metadata_path.rename(saved)
        os.mkfifo(self.store.metadata_path, 0o600)
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        process = context.Process(target=_process_fifo_startup, args=(str(self.root), results))
        try:
            process.start()
            process.join(timeout=3)
            self.assertFalse(process.is_alive(), "FIFO open blocked recovery initialization")
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(results.get(timeout=1), "rejected")
            self.assertTrue(stat.S_ISFIFO(self.store.metadata_path.lstat().st_mode))
            self.assertEqual(json.loads(saved.read_text())["records"][uuid.UUID(self.request_id).hex]["status"], "running")
        finally:
            if process.is_alive():
                process.kill()
            process.join(timeout=3)
            results.close()

    def test_private_permissions_and_link_guards(self):
        self.bind()
        self.store.complete(self.request_id, result=RESULT, **CLAIM)
        ref = self.store.lookup(self.request_id, **SCOPE)["result_reference"]
        result_path = self.store._results._files.root / ref["path"]
        original = result_path.read_bytes()
        replaced = self.root / "saved-results"
        results_root = self.store._results._files.results_root
        results_root.rename(replaced)
        results_root.mkdir(mode=0o700)
        with self.assertRaises(ChatRecoveryCorruptionError):
            self.store.lookup(self.request_id, **SCOPE)
        self.assertEqual((replaced / result_path.name).read_bytes(), original)
        results_root.rmdir()
        with self.assertRaises(ChatRecoveryError):
            self.store.lookup(self.request_id, **SCOPE)
        replaced.rename(results_root)
        foreign = Path(self.temporary.name) / "foreign"
        foreign.write_bytes(original)
        foreign.chmod(0o600)
        result_path.unlink()
        result_path.symlink_to(foreign)
        with self.assertRaises(ChatRecoveryError):
            self.store.read_result(self.request_id, **SCOPE)
        with self.assertRaises(ChatRecoveryError):
            ChatRecoveryStore(self.root, SECRET).initialize_epoch("process-2")
        self.assertEqual(foreign.read_bytes(), original)
        result_path.unlink()
        os.link(foreign, result_path)
        with self.assertRaises(ChatRecoveryError):
            self.store.read_result(self.request_id, **SCOPE)
        result_path.unlink()
        result_path.write_bytes(original)
        result_path.chmod(0o644)
        with self.assertRaises(ChatRecoveryError):
            self.store.read_result(self.request_id, **SCOPE)
        result_path.chmod(0o600)
        self.store.metadata_path.chmod(0o644)
        with self.assertRaises(ChatRecoveryError):
            self.store.lookup(self.request_id, **SCOPE)
        self.store.metadata_path.chmod(0o600)
        self.root.chmod(0o755)
        with self.assertRaises(ChatRecoveryError):
            ChatRecoveryStore(self.root, SECRET)
        self.root.chmod(0o700)

    def test_result_corruption_prevents_read_and_startup(self):
        self.bind()
        self.store.complete(self.request_id, result=RESULT, **CLAIM)
        ref = self.store.lookup(self.request_id, **SCOPE)["result_reference"]
        path = self.store._results._files.root / ref["path"]
        path.write_bytes(path.read_bytes().replace(b"PRIVATE ANSWER", b"CHANGED ANSWER"))
        ledger_before = self.store.metadata_path.read_bytes()
        for action in [lambda: self.store.read_result(self.request_id, **SCOPE),
                       lambda: ChatRecoveryStore(self.root, SECRET).initialize_epoch("process-2")]:
            with self.assertRaises(ChatRecoveryError):
                action()
        self.assertEqual(self.store.metadata_path.read_bytes(), ledger_before)

    def test_failed_result_or_precommit_write_never_exposes_completion(self):
        self.bind()
        before = self.store.metadata_path.read_bytes()
        with mock.patch.object(self.store._results, "write", side_effect=OSError("PRIVATE DISK ERROR")):
            with self.assertRaises(ChatRecoveryError):
                self.store.complete(self.request_id, result=RESULT, **CLAIM)
        self.assertEqual(self.store.metadata_path.read_bytes(), before)
        real_replace = os.replace
        def fail_commit(src, dst):
            if Path(dst) == self.store.metadata_path:
                raise OSError("PRIVATE DISK ERROR")
            return real_replace(src, dst)
        with mock.patch("services.llm_chat_recovery.os.replace", side_effect=fail_commit):
            with self.assertRaises(ChatRecoveryError):
                self.store.complete(self.request_id, result=RESULT, **CLAIM)
        self.assertEqual(self.store.metadata_path.read_bytes(), before)
        self.assertIsNone(self.store.read_result(self.request_id, **SCOPE))
        self.assertEqual(len(tuple(self.store._results._files.results_root.iterdir())), 1)
        restored = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
        self.assertEqual(restored.initialize_epoch("process-2"), 1)
        self.assertIsNone(restored.read_result(self.request_id, **SCOPE))

    def test_ambiguous_directory_fsync_retains_result_for_fresh_read(self):
        self.bind()
        sync = self.store._results._files._fsync_directory
        def fail_after_replace(path):
            if path == self.root:
                raise OSError("PRIVATE AFTER COMMIT ERROR")
            sync(path)
        with mock.patch.object(self.store._results._files, "_fsync_directory", side_effect=fail_after_replace):
            with self.assertRaises(ChatRecoveryError):
                self.store.complete(self.request_id, result=RESULT, **CLAIM)
        restored = ChatRecoveryStore(self.root, SECRET, clock=lambda: self.now)
        self.assertEqual(restored.initialize_epoch("process-2"), 0)
        self.assertEqual(restored.read_result(self.request_id, **SCOPE), RESULT)
        self.assertEqual(len(tuple(self.store._results._files.results_root.iterdir())), 1)

    def test_initialization_storage_failure_blocks_admission_and_preserves_bytes(self):
        unopened_root = Path(self.temporary.name) / "new-chat"
        store = ChatRecoveryStore(unopened_root, SECRET)
        with mock.patch("services.llm_chat_recovery.os.replace", side_effect=OSError("PRIVATE DISK ERROR")):
            with self.assertRaises(ChatRecoveryError):
                store.initialize_epoch("process-1")
        with self.assertRaises(ChatRecoveryError):
            store.bind(self.request_id, request_digest=DIGEST, **CLAIM)
        self.assertFalse(store.metadata_path.exists())
        self.assertFalse(tuple(store._results._files.results_root.iterdir()))

    def test_result_size_and_structural_bounds_do_not_truncate_or_commit(self):
        self.bind()
        invalid = [{**RESULT, "text": "x" * MAX_RESULT_BYTES}, {**RESULT, "stats": {"elapsed_seconds": float("nan")}},
                   {**RESULT, "stats": {"generated_tokens": 10**500}}, {**RESULT, "stats": {"error": "PRIVATE ERROR"}}, {**RESULT, "request": "PRIVATE PROMPT"},
                   {**RESULT, "guide_ids": ["guide"] * 33}]
        for result in invalid:
            with self.subTest(keys=list(result)):
                with self.assertRaises(ChatRecoveryError):
                    self.store.complete(self.request_id, result=result, **CLAIM)
                self.assertEqual(self.store.lookup(self.request_id, **SCOPE)["status"], "running")
        self.assertFalse(tuple(self.store._results._files.results_root.iterdir()))
        with self.assertRaises(ValueError):
            self.bind(request_id=self.request_id.upper())
        with self.assertRaises(ValueError):
            ChatRecoveryStore(Path(self.temporary.name) / "bad", SECRET, ttl_seconds=float("inf"))
        self.store.complete(self.request_id, result=deepcopy(RESULT), **CLAIM)
        self.assertNotIn("stats", self.store.read_result(self.request_id, **SCOPE))


if __name__ == "__main__":
    unittest.main()
