"""Behavioral contracts for detached LLM preparation and request recovery."""

from __future__ import annotations

import ast
import asyncio
import copy
from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import json
import sys
from tempfile import TemporaryDirectory
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import llm_operations
from services.llm_cancellation import LlmCancellationHandle
from services.llm_operations import (
    ChatAdmissionError,
    ChatRequestMismatchError,
    LlmChatOperationManager,
    LlmPreparationManager,
    LlmOperationCapacityError,
    LlmRouteAdmissionError,
    LlmRouteOperationConflictError,
    LlmRouteOperationManager,
    PromptEnhancementOperationManager,
    ROUTE_OPERATION_TTL_SECONDS,
    run_blocking_shielded,
)
from services.queue_recovery_adapter import (
    PromptEnhancementRecoveryCapacityError,
    PromptEnhancementRecoveryError,
    PromptEnhancementRecoveryConflictError,
    PromptEnhancementRecoveryStore,
    QueueRecoveryAdapterError,
)
from services.queue_recovery_runtime import QueueRecoveryRuntimeError


async def _wait_for_status(manager, operation_id, status):
    for _ in range(200):
        result = manager.status(
            operation_id, owner_key="owner", project_key="project",
        )
        if result and result["status"] == status:
            return result
        await asyncio.sleep(0.001)
    raise AssertionError(f"operation did not reach {status}")


class PreparationOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cold_prepare_returns_quickly_and_same_key_coalesces(self):
        release = threading.Event()
        entered = threading.Event()
        calls = []

        def cold_load():
            calls.append(True)
            entered.set()
            release.wait(timeout=2)

        manager = LlmPreparationManager(ttl_seconds=60)
        started = time.perf_counter()
        first = await manager.start(
            owner_key="owner", project_key="project",
            selection_key="exact-model", purpose="chat", prepare=cold_load,
        )
        elapsed = time.perf_counter() - started
        second = await manager.start(
            owner_key="owner", project_key="project",
            selection_key="exact-model", purpose="chat", prepare=cold_load,
        )

        self.assertLess(elapsed, 0.1)
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertNotIn("result", first)
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        self.assertEqual(len(calls), 1)
        release.set()
        await _wait_for_status(manager, first["operation_id"], "ready")
        revalidated = await manager.start(
            owner_key="owner", project_key="project",
            selection_key="exact-model", purpose="chat", prepare=cold_load,
        )
        self.assertNotEqual(first["operation_id"], revalidated["operation_id"])
        await _wait_for_status(manager, revalidated["operation_id"], "ready")
        self.assertEqual(len(calls), 2)

    async def test_running_prepare_survives_more_than_terminal_ttl(self):
        now = [0.0]
        release = threading.Event()
        entered = threading.Event()

        def long_cold_load():
            entered.set()
            release.wait(timeout=2)

        manager = LlmPreparationManager(
            ttl_seconds=10, clock=lambda: now[0],
        )
        started = await manager.start(
            owner_key="owner", project_key="project",
            selection_key="31b-model", purpose="configured",
            prepare=long_cold_load,
        )
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        now[0] = 100.0
        active = manager.status(
            started["operation_id"],
            owner_key="owner", project_key="project",
        )
        self.assertIsNotNone(active)
        self.assertEqual(active["status"], "preparing")
        release.set()
        await _wait_for_status(manager, started["operation_id"], "ready")
        now[0] = 111.0
        self.assertIsNone(manager.status(
            started["operation_id"],
            owner_key="owner", project_key="project",
        ))

    async def test_ready_receipt_expires_before_runtime_idle_unload(self):
        now = [0.0]
        manager = LlmPreparationManager(clock=lambda: now[0])
        started = await manager.start(
            owner_key="owner", project_key="project",
            selection_key="exact-model", purpose="chat", prepare=lambda: None,
        )
        await _wait_for_status(manager, started["operation_id"], "ready")
        now[0] = 46.0
        self.assertIsNone(manager.status(
            started["operation_id"],
            owner_key="owner", project_key="project",
        ))

    async def test_cross_project_is_opaque_and_failure_can_retry(self):
        manager = LlmPreparationManager(ttl_seconds=60)

        def fail():
            raise RuntimeError("private model path and provider detail")

        failed = await manager.start(
            owner_key="owner", project_key="project",
            selection_key="exact-model", purpose="chat", prepare=fail,
        )
        public = await _wait_for_status(
            manager, failed["operation_id"], "failed",
        )
        self.assertIsNone(manager.status(
            failed["operation_id"],
            owner_key="owner", project_key="other-project",
        ))
        self.assertNotIn("private model path", repr(public))
        self.assertNotIn("model", repr(public).casefold())

        retried = await manager.start(
            owner_key="owner", project_key="project",
            selection_key="exact-model", purpose="chat", prepare=lambda: None,
        )
        self.assertNotEqual(failed["operation_id"], retried["operation_id"])
        await _wait_for_status(manager, retried["operation_id"], "ready")

    async def test_cancelled_waiter_does_not_cancel_blocking_worker(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def blocking_work():
            entered.set()
            release.wait(timeout=2)
            finished.set()

        waiter = asyncio.create_task(run_blocking_shielded(blocking_work))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(finished.is_set())
        release.set()
        self.assertTrue(await asyncio.to_thread(finished.wait, 1))

    async def test_blocking_route_worker_keeps_event_loop_responsive(self):
        entered = threading.Event()
        release = threading.Event()

        def blocking_work():
            entered.set()
            release.wait(timeout=2)
            return "done"

        worker = asyncio.create_task(run_blocking_shielded(blocking_work))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        ticked = False

        async def ticker():
            nonlocal ticked
            await asyncio.sleep(0)
            ticked = True

        await ticker()
        self.assertTrue(ticked)
        self.assertFalse(worker.done())
        release.set()
        self.assertEqual(await worker, "done")


class ChatRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_final_survives_new_manager_without_reexecution(self):
        from services.llm_chat_recovery import ChatRecoveryStore
        with TemporaryDirectory() as directory:
            key = b"test-chat-secret" * 2
            root = Path(directory) / "chat"
            manager = LlmChatOperationManager(store=ChatRecoveryStore(root, key), epoch="epoch1")
            calls, released = [], []
            final = {"text": "full answer " + "x" * (700 * 1024), "model_id": "local", "guide_ids": ["guide"]}

            async def execute(progress):
                calls.append(True)
                progress({"text": "private partial"})
                return final

            def release():
                # Publication must already be durable before inputs/admission
                # are released, even though public state updates afterward.
                released.append(manager._store.read_result("a" * 32, owner_key="owner", project_key="project"))

            arguments = dict(request_id="a" * 32, owner_key="owner", project_key="project", request_digest="d" * 64,
                             execute=execute, admit=lambda: True, release=release)
            manager.submit(**arguments)
            await _wait_for_status(manager, "a" * 32, "completed")
            self.assertEqual(released, [final])
            restored = LlmChatOperationManager(store=ChatRecoveryStore(root, key), epoch="epoch2")
            self.assertEqual(restored.status("a" * 32, owner_key="owner", project_key="project")["result"], final)
            recovered = restored.submit(**arguments)
            self.assertEqual(recovered["result"], final)
            recovered["result"]["guide_ids"].append("caller mutation")
            self.assertEqual(restored.status("a" * 32, owner_key="owner", project_key="project")["result"], final)
            self.assertEqual(calls, [True])
            self.assertIsNone(restored.status("a" * 32, owner_key="other-account", project_key="project"))
            self.assertIsNone(restored.submit(**{**arguments, "owner_key": "other-account"}))
            with self.assertRaises(ChatRequestMismatchError):
                restored.submit(**{**arguments, "request_digest": "e" * 64})
            self.assertNotIn(b"private partial", (root / "chat.json").read_bytes())

    async def test_restart_interruption_never_resubmits_and_stale_worker_cannot_publish(self):
        from services.llm_chat_recovery import ChatRecoveryStore
        with TemporaryDirectory() as directory:
            key = b"test-chat-secret" * 2
            root = Path(directory) / "chat"
            manager = LlmChatOperationManager(store=ChatRecoveryStore(root, key), epoch="old")
            finish = asyncio.Event()
            calls, releases = [], []

            async def execute(_progress):
                calls.append(True)
                await finish.wait()
                return {"text": "late old answer", "model_id": "local", "guide_ids": []}

            arguments = dict(request_id="b" * 32, owner_key="owner", project_key="project", request_digest="d" * 64,
                             execute=execute, admit=lambda: True, release=lambda: releases.append(True))
            manager.submit(**arguments)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            restored = LlmChatOperationManager(store=ChatRecoveryStore(root, key), epoch="new")
            recovered = restored.status("b" * 32, owner_key="owner", project_key="project")
            self.assertEqual(recovered["error"]["code"], "chat_interrupted")
            self.assertEqual(restored.submit(**arguments), recovered)
            finish.set()
            await manager._operations["b" * 32].task
            self.assertEqual(calls, [True])
            self.assertEqual(releases, [True])
            self.assertEqual(restored.status("b" * 32, owner_key="owner", project_key="project"), recovered)
            self.assertNotIn("result", manager.status("b" * 32, owner_key="owner", project_key="project"))

    async def test_ambiguous_bind_or_completion_never_reexecutes(self):
        from services.llm_chat_recovery import ChatRecoveryStore, ChatRecoveryError
        for phase in ("bind", "complete"):
            with self.subTest(phase=phase), TemporaryDirectory() as directory:
                store = ChatRecoveryStore(Path(directory) / "chat", b"test-chat-secret" * 2)
                manager = LlmChatOperationManager(store=store)
                original = getattr(store, phase)
                calls, admissions = [], []

                def commit_then_raise(*args, **kwargs):
                    original(*args, **kwargs)
                    raise ChatRecoveryError("acknowledgement lost")

                async def execute(_progress):
                    calls.append(True)
                    return {"text": "committed final", "model_id": "local", "guide_ids": []}

                arguments = dict(request_id="c" * 32, owner_key="owner", project_key="project", request_digest="d" * 64,
                                 execute=execute, admit=lambda: admissions.append(True) or True, release=lambda: None)
                with mock.patch.object(store, phase, side_effect=commit_then_raise):
                    if phase == "bind":
                        with self.assertRaises(ChatRecoveryError):
                            manager.submit(**arguments)
                    else:
                        manager.submit(**arguments)
                        completed = await _wait_for_status(manager, "c" * 32, "completed")
                        self.assertEqual(completed["result"]["text"], "committed final")
                duplicate = manager.submit(**arguments)
                self.assertEqual(calls, [] if phase == "bind" else [True])
                self.assertEqual(admissions, [] if phase == "bind" else [True])
                self.assertEqual(duplicate["status"], "failed" if phase == "bind" else "completed")

    async def test_cancel_before_executor_starts_releases_once_and_is_durable(self):
        from services.llm_chat_recovery import ChatRecoveryStore
        with TemporaryDirectory() as directory:
            store = ChatRecoveryStore(Path(directory) / "chat", b"test-chat-secret" * 2)
            manager = LlmChatOperationManager(store=store)
            calls, releases = [], []
            async def execute(_progress):
                calls.append(True)
                return {"text": "must not run", "model_id": "local", "guide_ids": []}
            manager.submit(request_id="d" * 32, owner_key="owner", project_key="project", request_digest="d" * 64,
                           execute=execute, admit=lambda: True, release=lambda: releases.append(True))
            task = manager._operations["d" * 32].task
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            self.assertEqual(calls, [])
            self.assertEqual(releases, [True])
            self.assertEqual(store.lookup("d" * 32, owner_key="owner", project_key="project")["status"], "cancelled")

    async def test_cancelled_chat_holds_inputs_and_capacity_until_blocking_worker_exits(self):
        for worker_fails in (False, True):
            with self.subTest(worker_fails=worker_fails), TemporaryDirectory() as directory:
                manager = LlmChatOperationManager(max_operations=1)
                entered = threading.Event()
                finish = threading.Event()
                exited = threading.Event()
                released = []
                input_path = Path(directory) / "claimed-image.png"
                input_path.write_bytes(b"owned input bytes")
                reconciliations = []

                def reconcile_inputs():
                    reconciliations.append(exited.is_set())
                    input_path.unlink()
                    return "cleaned"

                def blocking_worker():
                    entered.set()
                    try:
                        if not finish.wait(timeout=5):
                            raise AssertionError("test did not release its worker")
                        self.assertEqual(input_path.read_bytes(), b"owned input bytes")
                        if worker_fails:
                            raise ValueError("private executor error")
                        return {"text": "late answer", "model_id": "model"}
                    finally:
                        exited.set()

                async def execute(_progress):
                    return await run_blocking_shielded(blocking_worker)

                manager.submit(
                    request_id="a" * 32, owner_key="owner", project_key="project",
                    request_digest="digest", execute=execute,
                    admit=lambda: True, release=lambda: released.append(exited.is_set()),
                )
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                    outer = manager._operations["a" * 32].task
                    outer.cancel()
                    failed = await _wait_for_status(manager, "a" * 32, "failed")
                    outer.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(outer.done())
                    self.assertEqual(released, [])
                    self.assertEqual(failed["partial_text"], "")
                    self.assertNotIn("result", failed)
                    recovered, cleanup = manager.status_or_reconcile_absent(
                        "a" * 32, owner_key="owner", project_key="project",
                        reconcile_absent=reconcile_inputs,
                        reconcile_terminal=reconcile_inputs,
                    )
                    self.assertEqual(recovered["status"], "failed")
                    self.assertIsNone(cleanup)
                    self.assertEqual(reconciliations, [])
                    self.assertTrue(input_path.exists())
                    hidden, cleanup = manager.status_or_reconcile_absent(
                        "a" * 32, owner_key="rotated-account", project_key="project",
                        reconcile_absent=reconcile_inputs, reconcile_terminal=reconcile_inputs,
                    )
                    self.assertIsNone(hidden)
                    self.assertIsNone(cleanup)
                    self.assertTrue(input_path.exists())
                    with self.assertRaises(LlmOperationCapacityError):
                        manager.submit(
                            request_id="b" * 32, owner_key="owner", project_key="project",
                            request_digest="other", execute=execute,
                            admit=lambda: True, release=lambda: None,
                        )
                finally:
                    finish.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(outer, 2)
                self.assertEqual(released, [True])
                recovered, cleanup = manager.status_or_reconcile_absent(
                    "a" * 32, owner_key="owner", project_key="project",
                    reconcile_absent=reconcile_inputs,
                    reconcile_terminal=reconcile_inputs,
                )
                self.assertEqual(recovered["status"], "failed")
                self.assertEqual(cleanup, "cleaned")
                self.assertEqual(reconciliations, [True])
                self.assertFalse(input_path.exists())
                self.assertNotIn("late answer", repr(manager.status(
                    "a" * 32, owner_key="owner", project_key="project",
                )))

                async def successor(_progress):
                    return {"text": "successor", "model_id": "model"}

                manager.submit(
                    request_id="b" * 32, owner_key="owner", project_key="project",
                    request_digest="other", execute=successor,
                    admit=lambda: True, release=lambda: None,
                )
                completed = await _wait_for_status(manager, "b" * 32, "completed")
                self.assertEqual(completed["result"]["text"], "successor")

    async def test_absent_reconciliation_is_atomic_against_duplicate_submit(self):
        manager = LlmChatOperationManager(ttl_seconds=60)
        cleanup_entered = threading.Event()
        allow_cleanup = threading.Event()
        cleaned = threading.Event()
        submit_started = threading.Event()
        submit_finished = threading.Event()
        reconcile_result = []
        submit_errors = []

        def cleanup_absent():
            cleanup_entered.set()
            allow_cleanup.wait(timeout=2)
            cleaned.set()
            return "cleaned"

        def reconcile():
            reconcile_result.append(manager.status_or_reconcile_absent(
                "a" * 32,
                owner_key="owner",
                project_key="project",
                reconcile_absent=cleanup_absent,
            ))

        async def execute(_progress):
            return {"text": "must not run", "model_id": "model"}

        def duplicate_submit():
            submit_started.set()
            try:
                manager.submit(
                    request_id="a" * 32,
                    owner_key="owner",
                    project_key="project",
                    request_digest="digest",
                    execute=execute,
                    admit=lambda: not cleaned.is_set(),
                    release=lambda: None,
                )
            except Exception as error:
                submit_errors.append(error)
            finally:
                submit_finished.set()

        reconcile_thread = threading.Thread(target=reconcile)
        reconcile_thread.start()
        self.assertTrue(await asyncio.to_thread(cleanup_entered.wait, 1))
        submit_thread = threading.Thread(target=duplicate_submit)
        submit_thread.start()
        self.assertTrue(await asyncio.to_thread(submit_started.wait, 1))
        self.assertFalse(await asyncio.to_thread(submit_finished.wait, 0.05))
        allow_cleanup.set()
        await asyncio.to_thread(reconcile_thread.join, 1)
        await asyncio.to_thread(submit_thread.join, 1)
        self.assertEqual(reconcile_result, [(None, "cleaned")])
        self.assertEqual(len(submit_errors), 1)
        self.assertIsInstance(submit_errors[0], ChatAdmissionError)
        self.assertIsNone(manager.status(
            "a" * 32, owner_key="owner", project_key="project",
        ))

    async def test_same_request_coalesces_and_result_is_owner_scoped(self):
        manager = LlmChatOperationManager(ttl_seconds=60)
        release = asyncio.Event()
        calls = []
        releases = []

        async def execute(_progress):
            calls.append(True)
            await release.wait()
            return {"text": "private answer", "model_id": "curated"}

        first = manager.submit(
            request_id="a" * 32,
            owner_key="owner", project_key="project",
            request_digest="digest", execute=execute,
            admit=lambda: True, release=lambda: releases.append(True),
        )
        second = manager.submit(
            request_id="a" * 32,
            owner_key="owner", project_key="project",
            request_digest="digest", execute=execute,
            admit=lambda: True, release=lambda: releases.append(True),
        )
        self.assertEqual(first, second)
        self.assertIsNone(manager.status(
            "a" * 32, owner_key="owner", project_key="other",
        ))
        with self.assertRaises(ChatRequestMismatchError):
            manager.submit(
                request_id="a" * 32,
                owner_key="owner", project_key="project",
                request_digest="changed", execute=execute,
                admit=lambda: True, release=lambda: releases.append(True),
            )
        self.assertEqual(releases, [])
        await asyncio.sleep(0)
        release.set()
        for _ in range(100):
            completed = manager.status(
                "a" * 32, owner_key="owner", project_key="project",
            )
            if completed and completed["status"] == "completed":
                break
            await asyncio.sleep(0.001)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(releases), 1)
        self.assertEqual(completed["result"]["text"], "private answer")

    async def test_progress_is_scoped_bounded_and_discards_rejected_attempt(self):
        manager = LlmChatOperationManager(ttl_seconds=60)
        release = asyncio.Event()

        async def execute(progress):
            progress({
                "phase": "generating",
                "text": "rejected first attempt",
                "generated_tokens_approx": 7,
                "elapsed_seconds": 2.5,
                "live_tps": 3.5,
                "average_tps": 2.25,
                "attempt": 1,
                "attempt_cap": 2,
            })
            await asyncio.sleep(0)
            progress({"phase": "retrying", "attempt": 2})
            await release.wait()
            progress({
                "phase": "generating",
                "text": "accepted partial",
                "generated_tokens_approx": 4,
                "elapsed_seconds": 1.25,
                "live_tps": 5.0,
                "average_tps": 4.0,
                "attempt": 2,
            })
            return {"text": "accepted final", "model_id": "curated"}

        manager.submit(
            request_id="b" * 32,
            owner_key="owner", project_key="project",
            request_digest="digest", execute=execute,
            admit=lambda: True, release=lambda: None,
        )
        for _ in range(100):
            retrying = manager.status(
                "b" * 32, owner_key="owner", project_key="project",
            )
            if retrying and retrying["attempt"] == 2:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(retrying["attempt_limit"], 2)
        self.assertEqual(retrying["partial_text"], "")
        self.assertEqual(retrying["generated_tokens_approx"], 0)
        self.assertEqual(retrying["elapsed_seconds"], 0.0)
        self.assertIsNone(manager.status(
            "b" * 32, owner_key="other", project_key="project",
        ))

        release.set()
        completed = await _wait_for_status(manager, "b" * 32, "completed")
        self.assertEqual(completed["partial_text"], "accepted final")
        self.assertEqual(completed["attempt"], 2)
        self.assertEqual(completed["generated_tokens_approx"], 4)
        self.assertEqual(completed["elapsed_seconds"], 1.25)
        self.assertEqual(completed["live_tps"], 5.0)
        self.assertEqual(completed["average_tps"], 4.0)
        self.assertNotIn("rejected first attempt", repr(completed))


class ScopedRouteOperationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _submit(manager, request_id, execute, **overrides):
        values = {
            "request_id": request_id,
            "owner_key": "owner",
            "project_instance_key": "project-instance",
            "operation_kind": "enhance",
            "effective_input_digest": "effective-digest",
            "execute": execute,
        }
        values.update(overrides)
        return manager.submit(**values)

    async def test_exact_identity_coalesces_and_mismatches_stay_opaque(self):
        manager = LlmRouteOperationManager()
        release = asyncio.Event()
        calls = []
        payload = {"enhanced": "generated output"}

        async def execute(_progress, _cancellation):
            calls.append(True)
            await release.wait()
            return payload

        request_id = self._id()
        first = self._submit(manager, request_id, execute)
        second = self._submit(manager, request_id, execute)
        self.assertEqual(first, second)
        self.assertEqual(first["request_id"], uuid.UUID(request_id).hex)
        self.assertEqual(first["status"], "running")
        self.assertIsNone(manager.status(
            request_id,
            owner_key="other",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ))
        self.assertIsNone(manager.status(
            request_id,
            owner_key="owner",
            project_instance_key="other-project",
            operation_kind="enhance",
        ))
        self.assertIsNone(manager.status(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="image_review",
        ))
        self.assertIsNone(self._submit(
            manager, request_id, execute, owner_key="other",
        ))
        with self.assertRaises(LlmRouteOperationConflictError):
            self._submit(
                manager, request_id, execute,
                effective_input_digest="different",
            )
        with self.assertRaises(LlmRouteOperationConflictError):
            self._submit(
                manager, request_id, execute,
                operation_kind="director_preview",
            )
        await asyncio.sleep(0)
        release.set()
        completed = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["result_available"])
        self.assertNotIn("result", completed)
        payload["enhanced"] = "mutated by execute caller"
        recovered = manager.result(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(recovered, {"enhanced": "generated output"})
        recovered["enhanced"] = "mutated by result caller"
        self.assertEqual(manager.result(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ), {"enhanced": "generated output"})
        self.assertIsNone(
            manager._operations[uuid.UUID(request_id).hex].worker_task,
        )
        self.assertIsNone(manager.result(
            request_id,
            owner_key="other",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ))
        captured_progress = []

        async def capture(progress, _cancellation):
            captured_progress.append(progress)
            return "done"

        late_id = self._id()
        self._submit(manager, late_id, capture)
        await manager.wait(
            late_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        terminal_public = manager.status(
            late_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        captured_progress[0]({"text": "late", "phase": "generating"})
        self.assertEqual(manager.status(
            late_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ), terminal_public)

    async def test_concurrent_operations_keep_progress_and_results_isolated(self):
        manager = LlmRouteOperationManager()
        release = asyncio.Event()
        entered_a = asyncio.Event()
        entered_b = asyncio.Event()

        async def execute_a(progress, _cancellation):
            progress({"text": "alpha", "generated_tokens_approx": 3})
            entered_a.set()
            await release.wait()
            return {"value": "alpha-result"}

        async def execute_b(progress, _cancellation):
            progress({"text": "beta", "generated_tokens_approx": 7})
            entered_b.set()
            await release.wait()
            return {"value": "beta-result"}

        first_id, second_id = self._id(), self._id()
        self._submit(manager, first_id, execute_a)
        self._submit(
            manager, second_id, execute_b,
            operation_kind="image_review",
            effective_input_digest="image-digest",
        )
        await asyncio.gather(
            asyncio.wait_for(entered_a.wait(), 1),
            asyncio.wait_for(entered_b.wait(), 1),
        )
        first = manager.status(
            first_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        second = manager.status(
            second_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="image_review",
        )
        self.assertEqual(first["partial_text"], "alpha")
        self.assertEqual(first["generated_tokens_approx"], 3)
        self.assertEqual(second["partial_text"], "beta")
        self.assertEqual(second["generated_tokens_approx"], 7)
        release.set()
        await asyncio.gather(
            manager.wait(
                first_id,
                owner_key="owner",
                project_instance_key="project-instance",
                operation_kind="enhance",
            ),
            manager.wait(
                second_id,
                owner_key="owner",
                project_instance_key="project-instance",
                operation_kind="image_review",
            ),
        )
        self.assertEqual(manager.result(
            first_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ), {"value": "alpha-result"})
        self.assertEqual(manager.result(
            second_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="image_review",
        ), {"value": "beta-result"})

    async def test_progress_is_bounded_finite_and_tracks_pass_separately(self):
        manager = LlmRouteOperationManager()
        gate = asyncio.Event()

        async def execute(progress, _cancellation):
            progress({
                "phase": "generating",
                "stage": "draft",
                "pass": 1,
                "pass_limit": 3,
                "attempt": 1,
                "attempt_limit": 4,
                "text": "x" * (llm_operations.ROUTE_PROGRESS_MAX_CHARS + 7),
                "generated_tokens_approx": 12,
                "elapsed_seconds": 2.5,
                "live_tps": 4.8,
                "average_tps": 3.2,
                "provider": "must-not-project",
                "media_path": "/private/image.png",
            })
            progress({
                "pass": 2,
                "pass_limit": 2,
                "attempt_limit": 2,
                "stage": "review",
                "elapsed_seconds": float("inf"),
                "live_tps": 1 << 100_000,
                "average_tps": float("nan"),
            })
            await gate.wait()
            progress({
                "attempt": 2,
                "stage": "repair",
                "text": "accepted partial",
                "generated_tokens_approx": 5,
                "elapsed_seconds": 1.0,
                "live_tps": 6.0,
                "average_tps": 5.0,
            })
            return "final"

        request_id = self._id()
        self._submit(manager, request_id, execute)
        for _ in range(100):
            current = manager.status(
                request_id,
                owner_key="owner",
                project_instance_key="project-instance",
                operation_kind="enhance",
            )
            if current and current["pass"] == 2:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(current["pass"], 2)
        self.assertEqual(current["pass_limit"], 3)
        self.assertEqual(current["attempt"], 1)
        self.assertEqual(current["attempt_limit"], 4)
        self.assertEqual(current["stage"], "review")
        self.assertEqual(current["partial_text"], "")
        self.assertEqual(current["generated_tokens_approx"], 0)
        self.assertEqual(current["elapsed_seconds"], 0.0)
        self.assertIsNone(current["live_tps"])
        self.assertNotIn("provider", repr(current).casefold())
        self.assertNotIn("private/image", repr(current))
        gate.set()
        completed = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["partial_text"], "accepted partial")
        self.assertEqual(completed["attempt"], 2)
        self.assertEqual(completed["pass"], 2)
        self.assertIsNone(completed["live_tps"])
        self.assertEqual(completed["average_tps"], 5.0)

    async def test_cancel_closes_blocked_response_and_clears_metrics(self):
        manager = LlmRouteOperationManager()
        entered = asyncio.Event()
        closed = threading.Event()

        class Response:
            def close(self):
                closed.set()

        async def execute(progress, cancellation):
            progress({
                "phase": "generating",
                "text": "discard me",
                "generated_tokens_approx": 20,
                "elapsed_seconds": 4.0,
                "live_tps": 5.0,
                "average_tps": 4.5,
            })
            cancellation.register_response(Response())
            entered.set()
            await asyncio.to_thread(closed.wait, 2)
            cancellation.checkpoint()

        request_id = self._id()
        self._submit(manager, request_id, execute)
        await asyncio.wait_for(entered.wait(), 1)
        cancelled = manager.cancel(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertTrue(closed.is_set())
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["partial_text"], "")
        self.assertEqual(cancelled["generated_tokens_approx"], 0)
        self.assertEqual(cancelled["elapsed_seconds"], 0.0)
        self.assertIsNone(cancelled["live_tps"])
        self.assertIsNone(cancelled["average_tps"])
        terminal = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(terminal["status"], "cancelled")
        self.assertFalse(terminal["result_available"])

    async def test_cancel_before_worker_start_never_calls_execute(self):
        manager = LlmRouteOperationManager()
        calls = []
        releases = []

        async def execute(_progress, _cancellation):
            calls.append(True)
            return "must not run"

        request_id = self._id()
        self._submit(
            manager, request_id, execute,
            release=lambda: releases.append(True),
        )
        cancelled = manager.cancel(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        terminal = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(terminal["status"], "cancelled")
        self.assertEqual(calls, [])
        self.assertEqual(releases, [True])

    async def test_cancel_during_result_snapshot_clears_consumed_worker(self):
        manager = LlmRouteOperationManager()
        deepcopy_entered = threading.Event()
        deepcopy_release = threading.Event()
        request_id = self._id()

        class BlockingResult(dict):
            def __deepcopy__(self, _memo):
                deepcopy_entered.set()
                if not deepcopy_release.wait(timeout=2):
                    raise AssertionError("deepcopy race was not released")
                return dict(self)

        async def execute(_progress, _cancellation):
            return BlockingResult(value="private result")

        def cancel_during_copy():
            if not deepcopy_entered.wait(timeout=2):
                raise AssertionError("deepcopy race was not entered")
            try:
                manager.cancel(
                    request_id,
                    owner_key="owner",
                    project_instance_key="project-instance",
                    operation_kind="enhance",
                )
            finally:
                deepcopy_release.set()

        canceller = asyncio.create_task(asyncio.to_thread(cancel_during_copy))
        await asyncio.sleep(0)
        self._submit(manager, request_id, execute)
        terminal = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        await canceller
        self.assertEqual(terminal["status"], "cancelled")
        self.assertFalse(terminal["result_available"])
        self.assertIsNone(
            manager._operations[uuid.UUID(request_id).hex].worker_task,
        )

    async def test_stop_before_response_registration_closes_late_response(self):
        manager = LlmRouteOperationManager()
        entered = asyncio.Event()
        register = asyncio.Event()
        closed = threading.Event()

        class Response:
            def close(self):
                closed.set()

        async def execute(_progress, cancellation):
            entered.set()
            await register.wait()
            cancellation.register_response(Response())
            cancellation.checkpoint()

        request_id = self._id()
        self._submit(manager, request_id, execute)
        await asyncio.wait_for(entered.wait(), 1)
        manager.cancel(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        register.set()
        terminal = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertTrue(closed.is_set())
        self.assertEqual(terminal["status"], "cancelled")

    async def test_outer_task_cancel_holds_admission_until_inner_worker_exits(self):
        manager = LlmRouteOperationManager(max_operations=1)
        entered = asyncio.Event()
        inner_release = asyncio.Event()
        releases = []

        async def execute(_progress, cancellation):
            entered.set()
            await inner_release.wait()
            cancellation.checkpoint()
            return "must not complete"

        request_id = self._id()
        self._submit(
            manager, request_id, execute,
            release=lambda: releases.append(True),
        )
        await asyncio.wait_for(entered.wait(), 1)
        outer_task = manager._operations[uuid.UUID(request_id).hex].task
        self.assertIsNotNone(outer_task)
        outer_task.cancel()
        await asyncio.sleep(0)
        self.assertEqual(releases, [])
        failed = manager.status(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(failed["status"], "failed")
        with self.assertRaises(LlmOperationCapacityError):
            self._submit(manager, self._id(), execute)

        inner_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await outer_task
        self.assertEqual(releases, [True])

        async def successor(_progress, _cancellation):
            return "successor"

        successor_id = self._id()
        self._submit(manager, successor_id, successor)
        successor_status = await manager.wait(
            successor_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(successor_status["status"], "completed")

    async def test_waiter_disconnect_does_not_cancel_then_exact_stop_wins(self):
        manager = LlmRouteOperationManager()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def execute(_progress, cancellation):
            entered.set()
            await release.wait()
            cancellation.checkpoint()
            return "late"

        request_id = self._id()
        self._submit(manager, request_id, execute)
        await asyncio.wait_for(entered.wait(), 1)
        waiter = asyncio.create_task(manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        running = manager.status(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(running["status"], "running")
        manager.cancel(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        release.set()
        terminal = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(terminal["status"], "cancelled")
        self.assertIsNone(manager.result(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ))

    async def test_stale_progress_cannot_mutate_successor_after_ttl(self):
        now = [0.0]
        manager = LlmRouteOperationManager(clock=lambda: now[0])
        old_callbacks = []

        async def old_execute(progress, _cancellation):
            old_callbacks.append(progress)
            progress({"text": "old"})
            return "old result"

        request_id = self._id()
        self._submit(manager, request_id, old_execute)
        await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        now[0] = ROUTE_OPERATION_TTL_SECONDS + 1
        self.assertIsNone(manager.status(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ))

        successor_entered = asyncio.Event()
        successor_release = asyncio.Event()

        async def successor(progress, _cancellation):
            progress({"text": "new"})
            successor_entered.set()
            await successor_release.wait()
            return "new result"

        self._submit(manager, request_id, successor)
        await asyncio.wait_for(successor_entered.wait(), 1)
        old_callbacks[0]({
            "phase": "failed", "text": "stale overwrite", "attempt": 9,
        })
        current = manager.status(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(current["partial_text"], "new")
        self.assertEqual(current["status"], "running")
        successor_release.set()
        await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )

    async def test_running_admission_is_bounded_and_terminal_is_evictable(self):
        manager = LlmRouteOperationManager(max_operations=1)
        release = asyncio.Event()

        async def blocked(_progress, _cancellation):
            await release.wait()
            return "done"

        first_id = self._id()
        self._submit(manager, first_id, blocked)
        with self.assertRaises(LlmOperationCapacityError):
            self._submit(manager, self._id(), blocked)
        release.set()
        await manager.wait(
            first_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )

        second_id = self._id()
        self._submit(manager, second_id, blocked)
        self.assertIsNone(manager.status(
            first_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        ))
        await manager.wait(
            second_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        with self.assertRaises(LlmRouteAdmissionError):
            LlmRouteOperationManager().submit(
                request_id=self._id(),
                owner_key="owner",
                project_instance_key="project-instance",
                operation_kind="image_review",
                effective_input_digest="digest",
                execute=blocked,
                admit=lambda: False,
            )

        cancelled_manager = LlmRouteOperationManager(max_operations=1)
        cancelled_entered = asyncio.Event()
        cancelled_release = asyncio.Event()

        async def stubborn(_progress, _cancellation):
            cancelled_entered.set()
            await cancelled_release.wait()
            return "ignored after cancellation"

        cancelled_id = self._id()
        self._submit(cancelled_manager, cancelled_id, stubborn)
        await asyncio.wait_for(cancelled_entered.wait(), 1)
        cancelled_manager.cancel(
            cancelled_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        with self.assertRaises(LlmOperationCapacityError):
            self._submit(cancelled_manager, self._id(), blocked)
        cancelled_release.set()
        await cancelled_manager.wait(
            cancelled_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )

    async def test_failure_and_public_projection_never_retain_private_input(self):
        manager = LlmRouteOperationManager()
        private = "SECRET prompt /media/private.png provider-token"

        async def execute(progress, _cancellation):
            progress({
                "phase": "generating",
                "text": "safe generated partial",
                "prompt": private,
                "media": private,
                "provider": private,
                "exception": private,
            })
            raise RuntimeError(private)

        request_id = self._id()
        public = self._submit(manager, request_id, execute)
        self.assertNotIn(private, repr(public))
        failed = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project-instance",
            operation_kind="enhance",
        )
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn(private, repr(failed))
        self.assertEqual(failed["partial_text"], "")
        self.assertFalse(failed["result_available"])

    def test_uuid_and_resume_ttl_contract(self):
        manager = LlmRouteOperationManager()
        self.assertGreaterEqual(
            manager.retention_seconds, ROUTE_OPERATION_TTL_SECONDS,
        )
        with self.assertRaises(ValueError):
            self._submit(manager, "not-a-uuid", lambda *_args: None)


class DirectRouteSourceTests(unittest.TestCase):
    def test_every_owned_async_llm_route_uses_shielded_worker(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        }
        for name in (
            "llm_load", "_execute_llm_chat", "llm_generate",
            "llm_write_song", "director_generate_music",
            "llm_enhance_prompt", "_enhance_with_wangp",
            "llm_describe_image", "director_plan_prompts",
            "director_plan_angle_prompts", "director_classify_sections",
            "director_plan_prompts_and_images",
            "director_plan_short_film_prompts",
            "director_plan_short_film_script",
        ):
            with self.subTest(route=name):
                self.assertIn("run_blocking_shielded", functions[name])

        for name in (
            "llm_generate", "llm_write_song", "director_generate_music",
            "director_plan_prompts", "director_plan_angle_prompts",
            "director_classify_sections", "director_plan_prompts_and_images",
            "director_plan_short_film_prompts",
            "director_plan_short_film_script",
        ):
            with self.subTest(remote_promotion=name):
                self.assertIn("_promote_external_llm_request", functions[name])
                self.assertIn(
                    "_run_authorized_llm_with_selection", functions[name],
                )
                self.assertIn("_resolve_direct_llm_selection", functions[name])

        enhance_source = functions["llm_enhance_prompt"]
        self.assertIn("_promote_external_llm_request", enhance_source)
        self.assertIn(
            "_run_authorized_llm_with_selection", enhance_source,
        )
        self.assertIn(
            "_resolve_prompt_enhancer_runtime_selection", enhance_source,
        )
        self.assertNotIn("_resolve_direct_llm_selection", enhance_source)

        describe_source = functions["llm_describe_image"]
        self.assertIn("_promote_external_llm_request", describe_source)
        self.assertIn(
            "_run_authorized_llm_with_selection", describe_source,
        )
        self.assertIn("_resolve_vision_llm_selection", describe_source)
        self.assertIn("image_paths=[image_path]", describe_source)
        self.assertIn("progress_callback=", describe_source)
        self.assertIn(
            "_resolved_local_response_assist", describe_source,
        )
        self.assertIn(
            "progress_callback=", functions["llm_enhance_prompt"],
        )
        self.assertIn(
            "_resolved_local_response_assist",
            functions["llm_enhance_prompt"],
        )
        for route_name in (
            "llm_generate", "llm_write_song", "director_generate_music",
        ):
            with self.subTest(response_assist_route=route_name):
                self.assertIn(
                    "_run_llm_route_operation", functions[route_name],
                )
        director_v2 = functions["director_v2_plan"]
        self.assertIn("_llm_route_progress_callback", director_v2)
        self.assertEqual(
            director_v2.count("_with_llm_route_progress"), 2,
        )
        self.assertIn("_run_authorized_llm_with_selection", director_v2)
        self.assertIn("run_director_plan", director_v2)
        self.assertNotIn("_ensure_llm_loaded", director_v2)
        self.assertNotIn("run_in_executor", director_v2)
        for route_name in (
            "director_plan_prompts", "director_plan_angle_prompts",
            "director_classify_sections", "director_plan_prompts_and_images",
            "director_plan_short_film_prompts",
            "director_plan_short_film_script",
        ):
            with self.subTest(legacy_operation_route=route_name):
                self.assertIn(
                    "_run_llm_route_operation", functions[route_name],
                )

        self.assertIn(
            "await asyncio.to_thread(", functions["llm_prepare"],
        )
        self.assertIn(
            "request_id is required for recoverable Chat", functions["llm_chat"],
        )
        self.assertNotIn(
            "return await asyncio.shield(execution)", functions["llm_chat"],
        )

        mismatch = functions["llm_chat"].split(
            "except ChatRequestMismatchError", 1,
        )[1].split("except ChatAdmissionError", 1)[0]
        self.assertNotIn("cleanup_chat_uploads", mismatch)

        self.assertNotIn("else str(error)", functions["llm_load"])
        self.assertNotIn(
            'detail=f"Song writing failed:',
            functions["director_generate_music"],
        )
        self.assertNotIn(
            'detail=f"Music generation failed:',
            functions["director_generate_music"],
        )


class PromptEnhanceScopedRouteTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _function_node(name):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        node.decorator_list = []
        return source, node

    def test_scoped_source_orders_authorization_before_runtime_and_forwards_cancel(self):
        source, node = self._function_node("llm_enhance_prompt")
        route = ast.get_source_segment(source, node) or ""
        self.assertLess(
            route.index("_require_project_access("),
            route.index("_prompt_enhancement_runtime_snapshot("),
        )
        self.assertLess(
            route.index('globals().get("_resolve_prompt_enhancement_images")'),
            route.index("_prompt_enhancement_runtime_snapshot("),
        )
        self.assertIn("existing_only=True", route)
        self.assertLess(
            route.index("_seal_prompt_enhancement_images,"),
            route.index("_prompt_enhancement_runtime_snapshot("),
        )
        scoped_submit = route[route.index("if raw_request_id is not None:"):]
        self.assertLess(
            scoped_submit.index('body.get("project_instance")'),
            scoped_submit.index("_prompt_enhancement_runtime_snapshot("),
        )
        self.assertLess(
            scoped_submit.index(
                "expected_project_instance, project_instance_key"
            ),
            scoped_submit.index("_prompt_enhancement_runtime_snapshot("),
        )
        self.assertLess(
            route.index("_revalidate_prompt_enhancement_images,"),
            route.index("expected_project_instance ="),
        )
        self.assertIn("_prompt_enhancement_operation_manager.submit(", route)
        self.assertIn("return JSONResponse(status, status_code=202)", route)
        self.assertIn("cancel_handle=cancel_handle", route)
        self.assertIn(
            "image_seals = await run_blocking_shielded(", route,
        )
        self.assertIn(
            "materialized_images = await run_blocking_shielded(", route,
        )
        self.assertIn(
            "_remove_prompt_enhancement_snapshots(materialized_images)",
            route,
        )
        self.assertIn(
            "await run_blocking_shielded(\n"
            "            _revalidate_prompt_enhancement_images,",
            route,
        )
        self.assertEqual(
            route.count("_validate_standalone_enhanced_prompt_cardinality("),
            2,
        )
        self.assertIn(
            "_ScopedPromptEnhancementRequest.snapshot_authority(request)",
            route,
        )
        execute = next(
            child for child in ast.walk(node)
            if isinstance(child, ast.AsyncFunctionDef)
            and child.name == "execute"
        )
        execute_source = ast.get_source_segment(source, execute) or ""
        self.assertIn("detached_authority", execute_source)
        self.assertNotIn("snapshot_authority(request)", execute_source)
        self.assertNotIn("get_stream_status", route)
        self.assertNotIn("_stream_buffer", route)
        cancelled = route.index("except LlmRequestCancelled:")
        fallback = route.index("except Exception as e:")
        self.assertLess(cancelled, fallback)

    def test_effective_digest_normalizes_request_id_and_image_alias(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_llm_route_effective_input_digest",
            "_prompt_enhancement_effective_digest",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        namespace = {
            "Any": object,
            "Mapping": dict,
            "hashlib": __import__("hashlib"),
            "hmac": __import__("hmac"),
            "json": __import__("json"),
            "_session_secret": lambda: b"digest-secret",
            "_llm_selection_key": lambda _purpose, selection: (
                f"selection:{selection.get('model_id')}"
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), "launch.py", "exec"), namespace)
        runtime = {
            "enhancer_enabled": 0,
            "explicit_provider": "local",
            "raw_enhancer_mode": False,
            "selection": {"model_id": "local-model"},
            "response_assist_snapshot": types.SimpleNamespace(revision=7),
        }
        first = namespace["_prompt_enhancement_effective_digest"](
            {
                "request_id": str(uuid.uuid4()),
                "workspace": "project",
                "prompt": "private prompt",
                "image_path": "upload.png",
            },
            workspace="project",
            authorized_image_paths=["/private/upload.png"],
            image_seals=[{
                "path": "/private/upload.png",
                "size": 7,
                "sha256": "a" * 64,
            }],
            runtime_snapshot=runtime,
        )
        second = namespace["_prompt_enhancement_effective_digest"](
            {
                "request_id": str(uuid.uuid4()),
                "workspace": "project",
                "prompt": "private prompt",
                "image_paths": ["different public alias"],
            },
            workspace="project",
            authorized_image_paths=["/private/upload.png"],
            image_seals=[{
                "path": "/private/upload.png",
                "size": 7,
                "sha256": "a" * 64,
            }],
            runtime_snapshot=runtime,
        )
        changed = namespace["_prompt_enhancement_effective_digest"](
            {
                "request_id": str(uuid.uuid4()),
                "workspace": "project",
                "prompt": "different prompt",
            },
            workspace="project",
            authorized_image_paths=["/private/upload.png"],
            image_seals=[{
                "path": "/private/upload.png",
                "size": 7,
                "sha256": "a" * 64,
            }],
            runtime_snapshot=runtime,
        )
        replaced_image = namespace["_prompt_enhancement_effective_digest"](
            {
                "request_id": str(uuid.uuid4()),
                "workspace": "project",
                "prompt": "private prompt",
            },
            workspace="project",
            authorized_image_paths=["/private/upload.png"],
            image_seals=[{
                "path": "/private/upload.png",
                "size": 7,
                "sha256": "b" * 64,
            }],
            runtime_snapshot=runtime,
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertNotEqual(first, replaced_image)
        self.assertEqual(len(first), 64)

    def test_every_supplied_image_must_resolve_to_owned_media(self):
        from fastapi import HTTPException

        _source, node = self._function_node(
            "_resolve_prompt_enhancement_images",
        )
        namespace = {
            "Any": object,
            "Mapping": dict,
            "Request": object,
            "HTTPException": HTTPException,
            "_LLM_ENHANCE_MAX_IMAGES": 4,
            "_resolve_authorized_request_media": (
                lambda _request, value, _workspace:
                f"/owned/{value}" if value == "owned.png" else None
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)
        resolve = namespace["_resolve_prompt_enhancement_images"]
        self.assertEqual(
            resolve(object(), {"image_path": "owned.png"}, "project"),
            ["/owned/owned.png"],
        )
        for body in (
            {"image_path": "foreign.png"},
            {"image_paths": ["owned.png", "foreign.png"]},
            {"image_paths": "owned.png"},
            {"image_paths": ["owned.png"] * 5},
        ):
            with self.subTest(body=body), self.assertRaises(HTTPException) as raised:
                resolve(object(), body, "project")
            self.assertIn(raised.exception.status_code, {400, 404})

    def test_same_name_image_replacement_fails_closed_before_worker_use(self):
        import hashlib
        import os
        import stat
        import tempfile
        from fastapi import HTTPException

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {
                "_seal_prompt_enhancement_images",
                "_revalidate_prompt_enhancement_images",
                "_remove_prompt_enhancement_snapshots",
                "_materialize_prompt_enhancement_images",
            }
        ]
        namespace = {
            "Any": object,
            "HTTPException": HTTPException,
            "hashlib": hashlib,
            "os": os,
            "stat": stat,
            "_LLM_CHAT_MAX_IMAGE_BYTES": 32 * 1024 * 1024,
            "_LLM_ENHANCE_MAX_TOTAL_IMAGE_BYTES": 64 * 1024 * 1024,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), "launch.py", "exec"), namespace)
        with tempfile.TemporaryDirectory() as root:
            image_path = os.path.join(root, "owned.png")
            with open(image_path, "wb") as handle:
                handle.write(b"admitted-bytes")
            seals = namespace["_seal_prompt_enhancement_images"]([image_path])
            namespace["_revalidate_prompt_enhancement_images"](
                [image_path], seals,
            )
            real_write = os.write

            def short_write(descriptor, value):
                partial = max(1, len(value) // 2)
                return real_write(descriptor, value[:partial])

            with mock.patch.object(
                os, "write", side_effect=short_write,
            ), mock.patch.object(os, "fchmod", new=None, create=True):
                materialized = namespace[
                    "_materialize_prompt_enhancement_images"
                ](seals)
            self.assertEqual(len(materialized), 1)
            with open(materialized[0], "rb") as handle:
                self.assertEqual(handle.read(), b"admitted-bytes")
            real_unlink = os.unlink

            def windows_unlink(path):
                if not os.stat(path).st_mode & stat.S_IWRITE:
                    raise PermissionError("simulated Windows read-only file")
                return real_unlink(path)

            with mock.patch.object(
                os, "unlink", side_effect=windows_unlink,
            ):
                namespace["_remove_prompt_enhancement_snapshots"](
                    materialized,
                )
            self.assertFalse(os.path.exists(materialized[0]))

            replacement = os.path.join(root, "replacement.png")
            with open(replacement, "wb") as handle:
                handle.write(b"changed!-bytes")
            os.replace(replacement, image_path)
            with self.assertRaises(HTTPException) as raised:
                namespace["_revalidate_prompt_enhancement_images"](
                    [image_path], seals,
                )
            self.assertEqual(raised.exception.status_code, 404)
            with self.assertRaises(HTTPException) as materialize_raised:
                namespace["_materialize_prompt_enhancement_images"](seals)
            self.assertEqual(materialize_raised.exception.status_code, 404)

    def test_image_seal_rejects_per_file_and_aggregate_size_before_hashing(self):
        import hashlib
        import os
        import stat
        import tempfile
        from fastapi import HTTPException

        _source, node = self._function_node(
            "_seal_prompt_enhancement_images",
        )
        namespace = {
            "Any": object,
            "HTTPException": HTTPException,
            "hashlib": hashlib,
            "os": os,
            "stat": stat,
            "_LLM_CHAT_MAX_IMAGE_BYTES": 8,
            "_LLM_ENHANCE_MAX_TOTAL_IMAGE_BYTES": 12,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)
        seal = namespace["_seal_prompt_enhancement_images"]
        with tempfile.TemporaryDirectory() as root:
            oversized = os.path.join(root, "oversized.png")
            with open(oversized, "wb") as handle:
                handle.truncate(9)
            with mock.patch.object(
                os, "read", side_effect=AssertionError("must not hash"),
            ), self.assertRaises(HTTPException) as per_file:
                seal([oversized])
            self.assertEqual(per_file.exception.status_code, 413)

            aggregate = []
            for index in range(2):
                image_path = os.path.join(root, f"aggregate-{index}.png")
                with open(image_path, "wb") as handle:
                    handle.truncate(7)
                aggregate.append(image_path)
            with mock.patch.object(
                os, "read", side_effect=AssertionError("must not hash"),
            ), self.assertRaises(HTTPException) as total:
                seal(aggregate)
            self.assertEqual(total.exception.status_code, 413)

    def test_snapshot_cleanup_removes_windows_read_only_files_for_all_terminals(self):
        import os
        import stat
        import tempfile

        _source, node = self._function_node(
            "_remove_prompt_enhancement_snapshots",
        )
        namespace = {"os": os, "stat": stat}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)
        cleanup = namespace["_remove_prompt_enhancement_snapshots"]
        real_unlink = os.unlink

        def windows_unlink(path):
            if not os.stat(path).st_mode & stat.S_IWRITE:
                raise PermissionError("simulated Windows read-only file")
            return real_unlink(path)

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            os, "unlink", side_effect=windows_unlink,
        ):
            for terminal in ("success", "cancel", "error"):
                snapshot = os.path.join(root, f"{terminal}.png")
                with open(snapshot, "wb") as handle:
                    handle.write(b"private-image")
                os.chmod(snapshot, 0o400)
                try:
                    if terminal == "cancel":
                        raise asyncio.CancelledError()
                    if terminal == "error":
                        raise RuntimeError("inference failed")
                except (asyncio.CancelledError, RuntimeError):
                    pass
                finally:
                    cleanup([snapshot])
                self.assertFalse(os.path.exists(snapshot), terminal)

    def test_image_byte_caps_survive_replace_and_growth_during_read(self):
        import hashlib
        import os
        import stat
        import tempfile
        from fastapi import HTTPException

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {
                "_seal_prompt_enhancement_images",
                "_remove_prompt_enhancement_snapshots",
                "_materialize_prompt_enhancement_images",
            }
        ]
        namespace = {
            "Any": object,
            "HTTPException": HTTPException,
            "hashlib": hashlib,
            "os": os,
            "stat": stat,
            "_LLM_CHAT_MAX_IMAGE_BYTES": 32 * 1024 * 1024,
            "_LLM_ENHANCE_MAX_TOTAL_IMAGE_BYTES": 64 * 1024 * 1024,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), "launch.py", "exec"), namespace)
        seal = namespace["_seal_prompt_enhancement_images"]
        materialize = namespace["_materialize_prompt_enhancement_images"]
        with tempfile.TemporaryDirectory() as root:
            image_path = os.path.join(root, "owned.png")
            original_bytes = b"admitted"
            with open(image_path, "wb") as handle:
                handle.write(original_bytes)
            replacement = os.path.join(root, "replacement.png")
            with open(replacement, "wb") as handle:
                handle.write(b"replacement-is-larger")

            real_read = os.read
            replaced = False
            source_read_sizes = []

            def replace_during_read(descriptor, count):
                nonlocal replaced
                data = real_read(descriptor, count)
                if not replaced and count > 1:
                    replaced = True
                    source_read_sizes.append(count)
                    os.replace(replacement, image_path)
                return data

            with mock.patch.object(
                os, "read", side_effect=replace_during_read,
            ), self.assertRaises(HTTPException) as replaced_error:
                seal([image_path])
            self.assertEqual(replaced_error.exception.status_code, 404)
            self.assertEqual(source_read_sizes, [len(original_bytes)])

            with open(image_path, "wb") as handle:
                handle.write(original_bytes)
            seals = seal([image_path])
            admitted_inode = seals[0]["inode"]
            grown = False
            source_read_sizes = []

            def grow_during_read(descriptor, count):
                nonlocal grown
                data = real_read(descriptor, count)
                try:
                    is_source = os.fstat(descriptor).st_ino == admitted_inode
                except OSError:
                    is_source = False
                if is_source:
                    source_read_sizes.append(count)
                    if not grown and data:
                        grown = True
                        with open(image_path, "ab") as handle:
                            handle.write(b"growth-beyond-admitted-size")
                return data

            with mock.patch.object(
                os, "read", side_effect=grow_during_read,
            ), self.assertRaises(HTTPException) as grown_error:
                materialize(seals)
            self.assertEqual(grown_error.exception.status_code, 404)
            self.assertEqual(
                source_read_sizes, [len(original_bytes), 1],
            )

    async def test_detached_worker_rejects_replaced_image_before_runtime(self):
        import hashlib
        import os
        import stat
        import tempfile
        from fastapi import HTTPException

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_seal_prompt_enhancement_images",
            "_revalidate_prompt_enhancement_images",
            "llm_enhance_prompt",
        }
        nodes = []
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in wanted
            ):
                node.decorator_list = []
                nodes.append(node)
        runtime_calls = []
        namespace = {
            "Any": object,
            "Request": object,
            "HTTPException": HTTPException,
            "JSONResponse": object,
            "copy": __import__("copy"),
            "hashlib": hashlib,
            "hmac": __import__("hmac"),
            "os": os,
            "stat": stat,
            "_LLM_CHAT_MAX_IMAGE_BYTES": 32 * 1024 * 1024,
            "_LLM_ENHANCE_MAX_TOTAL_IMAGE_BYTES": 64 * 1024 * 1024,
            "_promote_external_llm_request": lambda _request: None,
            "_request_project_workspace": lambda _request, value: value,
            "_require_project_access": lambda *_args, **_kwargs: None,
            "_prompt_enhancement_runtime_snapshot": (
                lambda *_args, **_kwargs: runtime_calls.append(True)
            ),
        }
        with tempfile.TemporaryDirectory() as root:
            image_path = os.path.join(root, "owned.png")
            with open(image_path, "wb") as handle:
                handle.write(b"admitted-bytes")
            exec(compile(ast.fix_missing_locations(ast.Module(
                body=nodes, type_ignores=[],
            )), "launch.py", "exec"), namespace)
            seals = namespace["_seal_prompt_enhancement_images"]([image_path])
            replacement = os.path.join(root, "replacement.png")
            with open(replacement, "wb") as handle:
                handle.write(b"changed!-bytes")
            os.replace(replacement, image_path)
            namespace["_resolve_prompt_enhancement_images"] = (
                lambda *_args: [image_path]
            )

            class Request:
                state = types.SimpleNamespace(
                    maestro_remote=False,
                    maestro_session_id="owner",
                    maestro_llm_enhance_image_seals=seals,
                )

                async def json(self):
                    return {"workspace": "project", "prompt": "private"}

            with self.assertRaises(HTTPException) as raised:
                await namespace["llm_enhance_prompt"](Request())
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(runtime_calls, [])

    async def test_synchronous_image_enhance_uses_private_snapshot_and_cleans_it(self):
        import hashlib
        import os
        import stat
        import tempfile
        from fastapi import HTTPException
        from starlette.responses import JSONResponse

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_seal_prompt_enhancement_images",
            "_remove_prompt_enhancement_snapshots",
            "_materialize_prompt_enhancement_images",
            "_ScopedPromptEnhancementRequest",
            "llm_enhance_prompt",
        }
        nodes = []
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in wanted
            ) or (
                isinstance(node, ast.ClassDef) and node.name in wanted
            ):
                node.decorator_list = []
                nodes.append(node)
        namespace = {
            "Any": object,
            "Mapping": dict,
            "Request": object,
            "HTTPException": HTTPException,
            "JSONResponse": JSONResponse,
            "copy": __import__("copy"),
            "hashlib": hashlib,
            "hmac": __import__("hmac"),
            "os": os,
            "stat": stat,
            "_LLM_CHAT_MAX_IMAGE_BYTES": 32 * 1024 * 1024,
            "_LLM_ENHANCE_MAX_TOTAL_IMAGE_BYTES": 64 * 1024 * 1024,
            "_promote_external_llm_request": lambda _request: None,
            "_request_project_workspace": lambda _request, value: value,
            "_require_project_access": lambda *_args, **_kwargs: None,
            "_prompt_enhancement_runtime_snapshot": (
                lambda *_args, **_kwargs: {"selection": {"model_id": "local"}}
            ),
            "_CPU_TEXT_OPERATIONS": frozenset({
                "prompt_enhancement",
                "generation_preparation",
                "reference_planning",
            }),
            "_llm_operation_scope": lambda *_args: ("owner", "a" * 64),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=nodes, type_ignores=[],
        )), "launch.py", "exec"), namespace)
        route = namespace["llm_enhance_prompt"]
        observed_snapshots = []

        async def execute_detached(detached_request):
            paths = list(
                detached_request.state.maestro_llm_enhance_materialized_images
            )
            observed_snapshots.extend(paths)
            self.assertEqual(len(paths), 1)
            self.assertTrue(os.path.isfile(paths[0]))
            self.assertEqual(Path(paths[0]).read_bytes(), b"admitted-image")
            self.assertTrue(
                detached_request.state.maestro_generation_preparation
            )
            self.assertEqual(
                detached_request.state.maestro_cpu_text_operation,
                "generation_preparation",
            )
            self.assertFalse(
                detached_request.state.maestro_cpu_text_text_only
            )
            return {"enhanced": "done"}

        namespace["llm_enhance_prompt"] = execute_detached

        class Request:
            headers = {}
            base_url = "http://local/"
            client = types.SimpleNamespace(host="127.0.0.1")
            state = types.SimpleNamespace(
                maestro_session_id="owner",
                maestro_remote=False,
                maestro_account_principal={"id": "owner"},
                maestro_generation_preparation=True,
                maestro_cpu_text_operation="generation_preparation",
                maestro_cpu_text_text_only=False,
            )

            def __init__(self, image_path):
                self.image_path = image_path

            async def json(self):
                return {
                    "workspace": "project",
                    "prompt": "private prompt",
                    "image_path": "owned.png",
                }

        with tempfile.TemporaryDirectory() as root:
            image_path = os.path.join(root, "owned.png")
            Path(image_path).write_bytes(b"admitted-image")
            namespace["_resolve_prompt_enhancement_images"] = (
                lambda request, *_args: [request.image_path]
            )
            result = await route(Request(image_path))
            self.assertEqual(result, {"enhanced": "done"})
            self.assertTrue(observed_snapshots)
            self.assertTrue(all(
                not os.path.exists(path) for path in observed_snapshots
            ))

            symlink_path = os.path.join(root, "linked.png")
            os.symlink(image_path, symlink_path)
            with self.assertRaises(HTTPException) as raised:
                await route(Request(symlink_path))
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(len(observed_snapshots), 1)

    def test_scoped_request_preserves_remote_provider_denial(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = [
            node for node in tree.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "_ScopedPromptEnhancementRequest"
            ) or (
                isinstance(node, ast.FunctionDef)
                and node.name == "_run_authorized_llm_with_selection"
            )
        ]
        namespace = {
            "Request": object,
            "copy": __import__("copy"),
            "_CPU_TEXT_OPERATIONS": frozenset({
                "prompt_enhancement",
                "generation_preparation",
                "reference_planning",
            }),
            "_llm_chat_request_is_external": (
                lambda request: bool(request.state.maestro_remote)
            ),
            "_run_llm_with_selection": lambda *_args, **_kwargs: self.fail(
                "remote provider reached the model lease"
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=selected, type_ignores=[],
        )), "launch.py", "exec"), namespace)
        original = types.SimpleNamespace(
            headers={},
            base_url="https://maestro.example/",
            client=None,
            url=types.SimpleNamespace(path="/api/v1/llm/enhance-prompt"),
            state=types.SimpleNamespace(
                maestro_session_id="remote-owner",
                maestro_remote=True,
            ),
        )
        authority = namespace[
            "_ScopedPromptEnhancementRequest"
        ].snapshot_authority(original)
        detached = namespace["_ScopedPromptEnhancementRequest"](
            authority,
            {"workspace": "project", "prompt": "private"},
            progress_callback=lambda _event: None,
            cancel_handle=object(),
            project_instance_key="project-instance",
            runtime_snapshot={"selection": {"provider": "openai"}},
            image_seals=[],
            materialized_image_paths=[],
        )
        self.assertTrue(detached.state.maestro_remote)
        self.assertEqual(detached.headers, {})
        self.assertFalse(hasattr(detached.state, "private_header"))
        with self.assertRaises(PermissionError):
            namespace["_run_authorized_llm_with_selection"](
                detached,
                {
                    "provider": "openai",
                    "remote_url": "https://provider.invalid",
                    "api_key": "host-secret",
                },
                lambda: self.fail("remote provider received content"),
            )

    def test_configured_enhancer_uses_exact_provider_catalog_selection(self):
        from fastapi import HTTPException
        from services import llm_service as _loaded_llm_service

        _source, node = self._function_node(
            "_resolve_prompt_enhancer_runtime_selection",
        )
        resolved = {
            "model_id": "provider-model",
            "device": "cpu",
            "provider": "openai",
            "remote_url": "https://provider.invalid",
            "api_key": "host-secret",
            "local_gguf_path": "",
            "gguf_file_override": "",
        }
        namespace = {
            "Any": object,
            "Request": object,
            "HTTPException": HTTPException,
            "hmac": __import__("hmac"),
            "_resolve_prompt_enhancer_selection": (
                lambda *_args, **_kwargs: ("provider-model", "cuda", False)
            ),
            "_llm_model_catalog": lambda *_args: [{
                "id": "provider-model", "provider": "openai",
            }],
            "_resolve_llm_chat_model": (
                lambda *_args: dict(resolved)
            ),
            "_llm_chat_request_is_external": lambda _request: False,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)
        fake_service = types.SimpleNamespace(MODEL_REGISTRY={})
        services = {
            "enhance_llm_model_id": "provider-model",
            "llm_provider": "openai",
        }
        with mock.patch.object(
            sys.modules["services"], "llm_service", fake_service,
        ):
            selection, raw_mode = namespace[
                "_resolve_prompt_enhancer_runtime_selection"
            ](object(), "", services, has_images=False)
        self.assertEqual(selection, resolved)
        self.assertFalse(raw_mode)

        namespace["_resolve_llm_chat_model"] = lambda *_args: {
            **resolved, "provider": "local", "remote_url": "", "api_key": "",
        }
        with mock.patch.object(
            sys.modules["services"], "llm_service", fake_service,
        ), self.assertRaises(HTTPException) as raised:
            namespace["_resolve_prompt_enhancer_runtime_selection"](
                object(), "", services, has_images=False,
            )
        self.assertEqual(raised.exception.status_code, 409)

    def test_external_configured_provider_without_local_catalog_fails_closed(self):
        from fastapi import HTTPException
        from services import llm_service as _loaded_llm_service

        _source, node = self._function_node(
            "_resolve_prompt_enhancer_runtime_selection",
        )
        provider_calls = []
        namespace = {
            "Any": object,
            "Request": object,
            "HTTPException": HTTPException,
            "hmac": __import__("hmac"),
            "_resolve_prompt_enhancer_selection": (
                lambda *_args, **_kwargs: ("provider-model", "cuda", False)
            ),
            "_llm_model_catalog": lambda *_args: [],
            "_resolve_llm_chat_model": (
                lambda *_args: provider_calls.append(True)
            ),
            "_llm_chat_request_is_external": lambda _request: True,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)
        fake_service = types.SimpleNamespace(MODEL_REGISTRY={})
        with mock.patch.object(
            sys.modules["services"], "llm_service", fake_service,
        ), self.assertRaises(HTTPException) as raised:
            namespace["_resolve_prompt_enhancer_runtime_selection"](
                object(),
                "",
                {
                    "enhance_llm_model_id": "provider-model",
                    "llm_provider": "openai",
                },
                has_images=False,
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(provider_calls, [])

    async def test_wangp_cancel_is_terminal_and_never_falls_back(self):
        from fastapi import HTTPException
        from services.llm_cancellation import (
            LlmCancellationHandle,
            LlmRequestCancelled,
        )

        _source, node = self._function_node("llm_enhance_prompt")
        fallback_calls = []

        async def cancelled_wangp(*_args, **_kwargs):
            raise LlmRequestCancelled("cancelled")

        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "JSONResponse": object,
            "copy": __import__("copy"),
            "hmac": __import__("hmac"),
            "wgp": types.SimpleNamespace(server_config={
                "enhancer_enabled": 1,
                "services": {},
            }),
            "_promote_external_llm_request": lambda _request: None,
            "_request_project_workspace": lambda _request, value: value,
            "_require_project_access": lambda *_args, **_kwargs: None,
            "_resolve_prompt_enhancement_images": lambda *_args: [],
            "_explicit_llm_guidance_allowed": lambda _body: False,
            "_llm_route_progress_callback": lambda _request: None,
            "_emit_llm_progress": lambda *_args: None,
            "_enhance_with_wangp": cancelled_wangp,
            "_resolve_prompt_enhancer_selection": (
                lambda *_args, **_kwargs: fallback_calls.append(True)
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)

        class Request:
            state = types.SimpleNamespace(
                maestro_session_id="owner",
                maestro_remote=False,
                maestro_llm_cancel_handle=LlmCancellationHandle(),
            )

            async def json(self):
                return {"workspace": "project", "prompt": "content"}

        with self.assertRaises(LlmRequestCancelled):
            await namespace["llm_enhance_prompt"](Request())
        self.assertEqual(fallback_calls, [])

    def test_wangp_postcondition_preserves_exact_global_timestamps(self):
        from shared.utils import prompt_parser

        _source, node = self._function_node(
            "_validate_standalone_enhanced_prompt_cardinality",
        )
        namespace = {
            "Any": object,
            "Mapping": dict,
            "wgp": types.SimpleNamespace(
                prompt_parser=prompt_parser,
                _validate_enhanced_prompt_cardinality=(
                    lambda *_args: self.fail("timeline reached cardinality")
                ),
            ),
            "_ENHANCED_PROMPT_CARDINALITY_VERSION": 1,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)
        validate = namespace[
            "_validate_standalone_enhanced_prompt_cardinality"
        ]
        cases = (
            (
                "[00:00] Begin.\n[00:05] End.",
                "[00:00] Improved.\n[00:05] Finish.",
                "Opening at [00:00], then finishing at [00:05].",
            ),
            (
                "10s Begin.\n20 seconds End.",
                "10s Improved.\n20 seconds Finish.",
                "10s Improved.\n21 seconds Finish.",
            ),
            (
                "[10-18s] Begin.\n[18-25s] End.",
                "[10-18s] Improved.\n[18-25s] Finish.",
                "[10-19s] Improved.\n[19-25s] Finish.",
            ),
        )
        for source_prompt, same, changed in cases:
            body = {
                "prompt": source_prompt,
                "mode": "video",
                "window_count": 2,
                "preserve_global_timeline": True,
            }
            with self.subTest(source=source_prompt):
                self.assertEqual(validate(body, "ltx_test", same), same)
                with self.assertRaises(ValueError):
                    validate(body, "ltx_test", changed)

    async def test_worker_image_copy_keeps_status_responsive_and_honors_cancel(self):
        manager = LlmRouteOperationManager()
        entered = threading.Event()
        release = threading.Event()
        request_id = str(uuid.uuid4())

        def bounded_copy(cancellation):
            entered.set()
            while not release.wait(0.01):
                cancellation.checkpoint()
            cancellation.checkpoint()

        async def execute(progress, cancellation):
            progress({"phase": "loading", "stage": "image_snapshot"})
            await run_blocking_shielded(bounded_copy, cancellation)
            return {"enhanced": "must not complete"}

        manager.submit(
            request_id=request_id,
            owner_key="owner",
            project_instance_key="project",
            operation_kind="enhance",
            effective_input_digest="digest",
            execute=execute,
        )
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        started = time.monotonic()
        status = manager.status(
            request_id,
            owner_key="owner",
            project_instance_key="project",
            operation_kind="enhance",
        )
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(status["stage"], "image_snapshot")
        manager.cancel(
            request_id,
            owner_key="owner",
            project_instance_key="project",
            operation_kind="enhance",
        )
        release.set()
        terminal = await manager.wait(
            request_id,
            owner_key="owner",
            project_instance_key="project",
            operation_kind="enhance",
        )
        self.assertEqual(terminal["status"], "cancelled")

    async def test_caller_uuid_submission_returns_202_after_authorized_digest(self):
        from fastapi import HTTPException
        from starlette.responses import JSONResponse

        _source, node = self._function_node("llm_enhance_prompt")
        events = []
        captured = {}
        request_id = str(uuid.uuid4())

        class Manager:
            @staticmethod
            def submit(**kwargs):
                events.append("submit")
                captured.update(kwargs)
                return {
                    "request_id": kwargs["request_id"],
                    "operation_kind": "enhance",
                    "status": "running",
                }

        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "JSONResponse": JSONResponse,
            "copy": __import__("copy"),
            "hmac": __import__("hmac"),
            "_promote_external_llm_request": (
                lambda _request: events.append("promote")
            ),
            "_request_project_workspace": (
                lambda _request, value: events.append("workspace") or value
            ),
            "_require_project_access": (
                lambda *_args, **_kwargs: events.append("authorize")
            ),
            "_resolve_prompt_enhancement_images": (
                lambda *_args: events.append("images") or ["/owned/image.png"]
            ),
            "_seal_prompt_enhancement_images": (
                lambda *_args: events.append("seal") or [{
                    "path": "/owned/image.png",
                    "size": 1,
                    "sha256": "a" * 64,
                }]
            ),
            "_normalize_llm_route_request_id": (
                lambda value: uuid.UUID(value).hex
            ),
            "_prompt_enhancement_runtime_snapshot": (
                lambda *_args, **_kwargs: events.append("runtime") or {
                    "selection": {"model_id": "local"},
                }
            ),
            "_prompt_enhancement_effective_digest": (
                lambda *_args, **_kwargs: events.append("digest")
                or "effective-digest"
            ),
            "_llm_operation_scope": (
                lambda *_args: ("owner", "b" * 64)
            ),
            "_prompt_enhancement_operation_scope": (
                lambda *_args: ("account", "b" * 64, "session")
            ),
            "_prompt_enhancement_operation_manager": Manager(),
            "_ScopedPromptEnhancementRequest": types.SimpleNamespace(
                snapshot_authority=lambda _request: {"session_id": "owner"},
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)

        class Request:
            state = types.SimpleNamespace(
                maestro_session_id="owner", maestro_remote=False,
            )

            async def json(self):
                return {
                    "request_id": request_id,
                    "project_instance": "b" * 64,
                    "workspace": "project",
                    "prompt": "private prompt",
                }

        response = await namespace["llm_enhance_prompt"](Request())
        self.assertEqual(response.status_code, 202)
        self.assertEqual(captured["request_id"], uuid.UUID(request_id).hex)
        self.assertEqual(captured["account_key"], "account")
        self.assertEqual(captured["session_key"], "session")
        self.assertEqual(captured["request_digest"], "effective-digest")
        self.assertTrue(callable(captured["execute"]))
        self.assertEqual(captured["job_context"]["workspace"], "project")
        self.assertEqual(
            captured["job_context"]["body"]["prompt"], "private prompt",
        )
        self.assertLess(events.index("authorize"), events.index("runtime"))
        self.assertLess(events.index("images"), events.index("runtime"))
        self.assertLess(events.index("seal"), events.index("runtime"))
        self.assertLess(events.index("digest"), events.index("submit"))

    async def test_scoped_submit_rejects_project_recreation_before_runtime(self):
        from fastapi import HTTPException
        from starlette.responses import JSONResponse

        _source, node = self._function_node("llm_enhance_prompt")
        runtime_calls = []
        submit_calls = []
        request_id = str(uuid.uuid4())
        old_instance = "a" * 64
        recreated_instance = "b" * 64

        class Manager:
            @staticmethod
            def submit(**kwargs):
                submit_calls.append(kwargs)
                return {"status": "running"}

        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "JSONResponse": JSONResponse,
            "copy": __import__("copy"),
            "hmac": __import__("hmac"),
            "_promote_external_llm_request": lambda _request: None,
            "_request_project_workspace": lambda _request, value: value,
            "_require_project_access": lambda *_args, **_kwargs: None,
            "_resolve_prompt_enhancement_images": lambda *_args: [],
            "_normalize_llm_route_request_id": (
                lambda value: uuid.UUID(value).hex
            ),
            "_llm_operation_scope": (
                lambda *_args: ("owner", recreated_instance)
            ),
            "_prompt_enhancement_operation_scope": (
                lambda *_args: ("account", recreated_instance, "session")
            ),
            "_prompt_enhancement_operation_manager": Manager(),
            "_seal_prompt_enhancement_images": lambda *_args: [],
            "_prompt_enhancement_runtime_snapshot": (
                lambda *_args, **_kwargs: runtime_calls.append(True)
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)

        class Request:
            state = types.SimpleNamespace(
                maestro_session_id="owner", maestro_remote=False,
            )

            async def json(self):
                return {
                    "request_id": request_id,
                    "project_instance": old_instance,
                    "workspace": "project",
                    "prompt": "private prompt",
                }

        with self.assertRaises(HTTPException) as raised:
            await namespace["llm_enhance_prompt"](Request())
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail, "Prompt Enhance request does not match",
        )
        self.assertEqual(runtime_calls, [])
        self.assertEqual(submit_calls, [])

    async def test_generic_routes_are_exact_scope_recoverable_and_cancellable(self):
        from fastapi import HTTPException
        from starlette.responses import JSONResponse

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_llm_route_operation_scope_or_404",
            "_llm_route_public_status",
            "llm_route_operation_status",
            "llm_route_operation_result",
            "cancel_llm_route_operation",
        }
        nodes = []
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in wanted
            ):
                node.decorator_list = []
                nodes.append(node)
        request_id = str(uuid.uuid4())
        gate = asyncio.Event()

        async def execute(progress, cancellation):
            progress({"phase": "generating", "text": "bounded partial"})
            await gate.wait()
            cancellation.checkpoint()
            return {"enhanced": "private result"}

        with TemporaryDirectory() as temp_dir:
            manager = PromptEnhancementOperationManager(
                PromptEnhancementRecoveryStore(
                    Path(temp_dir) / "prompt-enhancement",
                    b"scope-test-secret-material-32b",
                )
            )
            manager.submit(
                request_id=request_id,
                account_key="account",
                project_instance_key="project-one",
                session_key="session",
                request_digest="d" * 64,
                execute=execute,
            )
            namespace = {
                "Request": object,
                "HTTPException": HTTPException,
                "JSONResponse": JSONResponse,
                "_LLM_ROUTE_OPERATION_KINDS": frozenset({
                    "enhance", "director_preview",
                }),
                "_promote_external_llm_request": lambda _request: None,
                "_request_project_workspace": lambda _request, value: value,
                "_require_project_access": lambda *_args, **_kwargs: None,
                "_llm_operation_scope": (
                    lambda _request, workspace: ("owner", workspace)
                ),
                "_prompt_enhancement_operation_scope": (
                    lambda _request, workspace: (
                        "account", workspace, "session",
                    )
                ),
                "_prompt_enhancement_operation_manager": manager,
            }
            exec(compile(ast.fix_missing_locations(ast.Module(
                body=nodes, type_ignores=[],
            )), "launch.py", "exec"), namespace)
            request = types.SimpleNamespace(state=types.SimpleNamespace())
            for _ in range(100):
                status = namespace["llm_route_operation_status"](
                    request, "enhance", request_id, "project-one",
                )
                if status["partial_text"]:
                    break
                await asyncio.sleep(0.001)
            self.assertEqual(status["partial_text"], "bounded partial")
            waiting = namespace["llm_route_operation_result"](
                request, "enhance", request_id, "project-one",
            )
            self.assertEqual(waiting.status_code, 202)
            with self.assertRaises(HTTPException) as foreign:
                namespace["llm_route_operation_status"](
                    request, "enhance", request_id, "recreated-project",
                )
            self.assertEqual(foreign.exception.status_code, 404)
            cancelled = namespace["cancel_llm_route_operation"](
                request, "enhance", request_id, "project-one",
            )
            self.assertEqual(cancelled["status"], "cancelled")
            terminal = namespace["llm_route_operation_result"](
                request, "enhance", request_id, "project-one",
            )
            self.assertEqual(terminal.status_code, 409)
            gate.set()
            await asyncio.sleep(0)


class DirectorV2LeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_planning_holds_exact_model_lease_against_qwen_switch(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "_run_llm_with_selection",
            "_run_authorized_llm_with_selection",
            "_llm_route_progress_callback",
            "_with_llm_route_progress",
            "director_v2_plan",
        }
        selected_nodes = []
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in wanted
            ):
                node.decorator_list = []
                selected_nodes.append(node)
        module = ast.Module(body=selected_nodes, type_ignores=[])
        ast.fix_missing_locations(module)

        lease_lock = threading.Lock()
        planning_entered = threading.Event()
        release_planning = threading.Event()
        switch_started = threading.Event()
        switch_done = threading.Event()
        resident = {"model_id": ""}
        generated_with = []
        assist_checks = []

        @contextmanager
        def loaded_model_lease(**selection):
            with lease_lock:
                resident["model_id"] = selection["model_id"]
                yield (selection["model_id"],)

        def generate(*_args, **_kwargs):
            generated_with.append(resident["model_id"])
            return "answer"

        fake_service = types.SimpleNamespace(
            loaded_model_lease=loaded_model_lease,
            generate=generate,
            generate_streaming=generate,
        )

        class Plan:
            @staticmethod
            def to_dict():
                return {"ok": True}

        class DirectorOrchestrator:
            def __init__(self, *, llm_generate, llm_generate_streaming, flags):
                self.generate = llm_generate
                self.generate_streaming = llm_generate_streaming

            def plan(self, _skill_type, **_kwargs):
                planning_entered.set()
                self.generate("first")
                release_planning.wait(timeout=2)
                self.generate_streaming("second")
                return Plan()

            @staticmethod
            def render_plan(plan, **_kwargs):
                return plan

            @staticmethod
            def plan_to_clip_plans(_rendered):
                return [{"video_prompt": "planned"}]

        director_flags = types.SimpleNamespace(from_dict=lambda _value: object())
        orchestrator_module = types.SimpleNamespace(
            DirectorOrchestrator=DirectorOrchestrator,
            DirectorFlags=director_flags,
        )
        guidance_module = types.SimpleNamespace(
            EXPLICIT_GUIDANCE_SNAPSHOT_KEY="_server_snapshot",
        )
        selection = {
            "model_id": "heavy/heretic",
            "device": "cpu",
            "provider": "local",
            "remote_url": "",
            "api_key": "",
            "local_gguf_path": "",
            "gguf_file_override": "",
        }

        class HTTPException(RuntimeError):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        @contextmanager
        def native_gpu_slot(*_args, **_kwargs):
            yield False

        namespace = {
            "Request": object,
            "asyncio": asyncio,
            "copy": copy,
            "HTTPException": HTTPException,
            "_gen_lock": threading.RLock(),
            "_WgpNativeGpuExecutionSlot": native_gpu_slot,
            "_local_llm_uses_native_gpu": lambda _selection: False,
            "wgp": types.SimpleNamespace(server_config={"services": {
                "director_prompt_polish": "off",
            }}, transformer_type="", wan_model=None, offloadobj=None),
            "_reject_client_director_image_role_internals": lambda _body: None,
            "_authorize_director_media_inputs": lambda *_args: None,
            "_resolve_director_image_role_request": (
                lambda _request, _body: "legacy"
            ),
            "_resolve_h3_style_workflow_request": (
                lambda _body, *, model_field="video_model": None
            ),
            "_apply_h3_style_workflow_to_director_clips": (
                lambda _clips, _workflow: None
            ),
            "_llm_chat_request_is_external": lambda _request: False,
            "_resolve_direct_llm_selection": lambda _request: dict(selection),
            "_explicit_llm_guidance_allowed": lambda _body: True,
        }
        exec(compile(module, str(APP / "launch.py"), "exec"), namespace)

        def resolve_assist(_body, resolved_selection, **_kwargs):
            assist_checks.append((
                resident["model_id"], resolved_selection["model_id"],
            ))
            return {"retry_on_refusal": True}

        namespace["_resolved_local_response_assist"] = resolve_assist

        class Request:
            state = types.SimpleNamespace(
                maestro_llm_progress_callback=lambda _event: None,
                maestro_llm_cancel_handle=LlmCancellationHandle(),
                maestro_director_preview_worker=True,
                maestro_director_preview_selection=dict(selection),
                maestro_director_preview_assist=types.SimpleNamespace(
                    revision=1,
                ),
                maestro_director_preview_guidance=True,
                maestro_director_preview_workflow=None,
                maestro_director_preview_polish_mode="off",
            )

            async def json(self):
                return {
                    "skill_type": "music_video",
                    "explicit_output": True,
                    "director_flags": {},
                }

        def switch_to_qwen():
            switch_started.set()
            with lease_lock:
                resident["model_id"] = "light/qwen"
            switch_done.set()

        with mock.patch.object(
            sys.modules["services"], "llm_service", fake_service,
            create=True,
        ), mock.patch.dict(sys.modules, {
            "services.director.nsfw_guidance": guidance_module,
            "services.director.orchestrator": orchestrator_module,
            "services.llm_operations": llm_operations,
        }):
            planning = asyncio.create_task(namespace["director_v2_plan"](Request()))
            self.assertTrue(await asyncio.to_thread(planning_entered.wait, 1))
            switch = asyncio.create_task(asyncio.to_thread(switch_to_qwen))
            self.assertTrue(await asyncio.to_thread(switch_started.wait, 1))
            self.assertFalse(await asyncio.to_thread(switch_done.wait, 0.05))
            release_planning.set()
            result = await planning
            await switch

        self.assertEqual(result["production_plan"], {"ok": True})
        self.assertEqual(generated_with, ["heavy/heretic", "heavy/heretic"])
        self.assertEqual(
            assist_checks, [("heavy/heretic", "heavy/heretic")],
        )
        self.assertTrue(switch_done.is_set())


class DirectRouteSecurityTests(unittest.TestCase):
    def test_external_selection_is_revalidated_before_provider_use(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_run_authorized_llm_with_selection"
        )
        namespace = {
            "Request": object,
            "_llm_chat_request_is_external": lambda _request: True,
            "_run_llm_with_selection": lambda *_args, **_kwargs: self.fail(
                "external provider reached the model lease"
            ),
        }
        exec(  # noqa: S102 - isolated helper contract
            compile(ast.fix_missing_locations(ast.Module(
                body=[node], type_ignores=[],
            )), "launch.py", "exec"),
            namespace,
        )
        with self.assertRaises(PermissionError):
            namespace["_run_authorized_llm_with_selection"](
                object(),
                {
                    "provider": "openai",
                    "remote_url": "https://provider.invalid",
                    "api_key": "host-secret",
                },
                lambda: None,
            )

    def test_external_origin_is_revalidated_after_model_lease(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_run_authorized_llm_with_selection"
        )
        external_reads = iter((False, True))
        provider_calls = []

        def run_with_selection(_selection, operation, *_args, **_kwargs):
            return operation()

        namespace = {
            "Request": object,
            "_promote_external_llm_request": lambda _request: None,
            "_llm_chat_request_is_external": (
                lambda _request: next(external_reads)
            ),
            "_run_llm_with_selection": run_with_selection,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "launch.py", "exec"), namespace)
        request = types.SimpleNamespace(state=types.SimpleNamespace())
        with self.assertRaises(PermissionError):
            namespace["_run_authorized_llm_with_selection"](
                request,
                {
                    "provider": "openai",
                    "remote_url": "https://provider.invalid",
                    "api_key": "host-secret",
                },
                lambda: provider_calls.append(True),
            )
        self.assertEqual(provider_calls, [])

    def test_lan_unload_and_stream_status_are_local_only(self):
        from fastapi import HTTPException

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = []
        for item in tree.body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name in {
                    "_promote_external_llm_request",
                    "_require_local_llm_control",
                    "llm_unload",
                    "llm_stream_status",
                }
            ):
                item.decorator_list = []
                selected.append(item)
        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "_llm_chat_request_is_external": lambda _request: True,
        }
        exec(  # noqa: S102 - isolated route functions only
            compile(ast.fix_missing_locations(ast.Module(
                body=selected, type_ignores=[],
            )), "launch.py", "exec"),
            namespace,
        )
        service = types.SimpleNamespace(
            unload_model=lambda: self.fail("LAN unload reached runtime"),
            get_stream_status=lambda: self.fail("LAN read reached runtime"),
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        with mock.patch.dict(sys.modules, {
            "services": types.SimpleNamespace(llm_service=service),
        }):
            for route in ("llm_unload", "llm_stream_status"):
                with self.subTest(route=route), self.assertRaises(
                    HTTPException,
                ) as raised:
                    namespace[route](request)
                self.assertEqual(raised.exception.status_code, 403)

    def test_operation_routes_are_private_no_store_on_success_and_error(self):
        from starlette.responses import Response

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == "_llm_operation_no_store"
        )
        node.decorator_list = []
        namespace = {"Request": object}
        exec(  # noqa: S102 - isolated middleware contract
            compile(ast.fix_missing_locations(ast.Module(
                body=[node], type_ignores=[],
            )), "launch.py", "exec"),
            namespace,
        )

        async def exercise(path, status_code):
            request = types.SimpleNamespace(
                url=types.SimpleNamespace(path=path),
            )

            async def call_next(_request):
                return Response(status_code=status_code)

            return await namespace["_llm_operation_no_store"](
                request, call_next,
            )

        for path, code in (
            ("/api/v1/llm/prepare", 202),
            ("/api/v1/llm/prepare/missing", 404),
            ("/api/v1/llm/chat", 400),
            ("/api/v1/llm/chat/request-id", 200),
            ("/api/v1/llm/chat-upload", 202),
            ("/api/v1/llm/chat-upload/private-name", 404),
            ("/api/v1/llm/chat-upload-request/request-id", 200),
            ("/api/v1/llm/enhance-prompt", 202),
            ("/api/v1/llm/operations/enhance/request-id", 200),
            ("/api/v1/llm/operations/enhance/request-id/result", 409),
            ("/api/v1/director/v2/plan", 202),
        ):
            with self.subTest(path=path, code=code):
                response = asyncio.run(exercise(path, code))
                self.assertEqual(
                    response.headers["Cache-Control"], "private, no-store",
                )


class DirectRouteAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lan_generate_is_remote_before_workspace_resolution(self):
        from fastapi import HTTPException

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        selected = []
        for item in tree.body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name in {
                    "_promote_external_llm_request",
                    "_request_project_workspace",
                    "llm_generate",
                }
            ):
                item.decorator_list = []
                selected.append(item)
        module = ast.Module(body=selected, type_ignores=[])
        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "_llm_chat_request_is_external": lambda _request: True,
        }
        exec(  # noqa: S102 - execute isolated route functions only
            compile(ast.fix_missing_locations(module), "launch.py", "exec"),
            namespace,
        )

        class Request:
            state = types.SimpleNamespace(
                maestro_remote=False, maestro_session_id="session",
            )

            async def json(self):
                return {"prompt": "content stays local"}

        with self.assertRaises(HTTPException) as raised:
            await namespace["llm_generate"](Request())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertTrue(Request.state.maestro_remote)


class PrepareRouteScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_project_status_is_fixed_opaque_404(self):
        from fastapi import HTTPException

        manager = LlmPreparationManager(ttl_seconds=60)
        started = await manager.start(
            owner_key="owner", project_key="project-one",
            selection_key="selection", purpose="chat", prepare=lambda: None,
        )
        await asyncio.sleep(0.01)

        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "llm_prepare_status"
        )
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "_llm_chat_request_is_external": lambda _request: False,
            "_request_project_workspace": (
                lambda _request, workspace: workspace
            ),
            "_require_project_access": lambda *_args, **_kwargs: None,
            "_llm_operation_scope": (
                lambda _request, workspace: ("owner", workspace)
            ),
        }
        exec(  # noqa: S102 - execute one isolated route AST for contract testing
            compile(ast.fix_missing_locations(module), "launch.py", "exec"),
            namespace,
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(
                maestro_remote=False, maestro_session_id="session",
            ),
        )
        with mock.patch.object(
            llm_operations, "llm_preparation_manager", manager,
        ), self.assertRaises(HTTPException) as raised:
            namespace["llm_prepare_status"](
                request, started["operation_id"], "project-two",
            )
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "LLM preparation not found")


class DurablePromptEnhancementOperationTests(unittest.IsolatedAsyncioTestCase):
    SECRET = b"prompt-enhance-test-secret-material"
    SCOPE = {
        "account_key": "account-one",
        "project_instance_key": "project-one",
        "session_key": "session-one",
    }

    async def test_resource_wait_precedes_generation_slot_and_replay_never_prepares_twice(self):
        events = []
        entered = asyncio.Event()
        ready = asyncio.Event()

        class Store(PromptEnhancementRecoveryStore):
            def mark_running(self, *args, **kwargs):
                events.append("slot")
                return super().mark_running(*args, **kwargs)

            def complete(self, *args, **kwargs):
                events.append("complete")
                return super().complete(*args, **kwargs)

        async def prepare(progress, cancellation):
            events.append("prepare")
            progress({"phase": "queued", "stage": "gpu_wait"})
            entered.set()
            await ready.wait()
            cancellation.checkpoint()

        async def execute(_progress, cancellation):
            cancellation.checkpoint()
            events.append("execute")
            return {"original": "untouched", "enhanced": "comparison"}

        async def close():
            events.append("close")

        with TemporaryDirectory() as directory:
            store = Store(Path(directory) / "enhance", self.SECRET)
            manager = PromptEnhancementOperationManager(store)
            request_id = str(uuid.uuid4())
            arguments = dict(request_id=request_id, request_digest="a" * 64,
                             execute=execute, prepare=prepare, close=close, **self.SCOPE)
            manager.submit(**arguments)
            await entered.wait()
            status = manager.status(request_id, **self.SCOPE)
            self.assertEqual(status["status"], "queued")
            self.assertEqual(status["stage"], "gpu_wait")
            manager.submit(**arguments)
            await asyncio.sleep(0)
            self.assertEqual(events, ["prepare"])
            ready.set()
            await manager.wait(request_id, **self.SCOPE)
            self.assertEqual(events, ["prepare", "slot", "execute", "close", "complete"])
            self.assertEqual(manager.status(request_id, **self.SCOPE)["status"], "completed")

    async def test_cancel_while_waiting_closes_without_acquiring_slot_or_executing(self):
        events = []
        entered = asyncio.Event()
        ready = asyncio.Event()

        class Store(PromptEnhancementRecoveryStore):
            def mark_running(self, *args, **kwargs):
                events.append("slot")
                return super().mark_running(*args, **kwargs)

        async def prepare(_progress, cancellation):
            entered.set()
            await ready.wait()
            cancellation.checkpoint()

        async def execute(*_args):
            events.append("execute")

        async def close():
            events.append("close")
            raise RuntimeError("private cancellation cleanup failure")

        with TemporaryDirectory() as directory:
            manager = PromptEnhancementOperationManager(
                Store(Path(directory) / "enhance", self.SECRET))
            request_id = str(uuid.uuid4())
            manager.submit(request_id=request_id, request_digest="b" * 64,
                           execute=execute, prepare=prepare, close=close, **self.SCOPE)
            await entered.wait()
            manager.cancel(request_id, **self.SCOPE)
            ready.set()
            await manager.wait(request_id, **self.SCOPE)
            self.assertEqual(events, ["close"])
            self.assertEqual(manager.status(request_id, **self.SCOPE)["status"], "cancelled")

    async def test_failed_admission_or_cleanup_never_publishes_completed_result(self):
        for fail_preparation in (True, False):
            with self.subTest(fail_preparation=fail_preparation), TemporaryDirectory() as directory:
                events = []
                manager = PromptEnhancementOperationManager(
                    PromptEnhancementRecoveryStore(Path(directory) / "enhance", self.SECRET))

                async def prepare(*_args):
                    events.append("prepare")
                    if fail_preparation:
                        raise RuntimeError("private admission failure")

                async def execute(*_args):
                    events.append("execute")
                    return {"enhanced": "must not be published"}

                async def close():
                    events.append("close")
                    raise RuntimeError("private unconfirmed withdrawal")

                request_id = str(uuid.uuid4())
                manager.submit(request_id=request_id, request_digest="c" * 64,
                               execute=execute, prepare=prepare, close=close, **self.SCOPE)
                await manager.wait(request_id, **self.SCOPE)
                expected = ["prepare", "close"] if fail_preparation else ["prepare", "execute", "close"]
                self.assertEqual(events, expected)
                status = manager.status(request_id, **self.SCOPE)
                self.assertEqual(status["status"], "failed")
                self.assertFalse(status["result_available"])
                self.assertNotIn("private", repr(status))

    async def test_task_cancellation_joins_blocking_preparation_and_close_before_release(self):
        import itertools
        import threading
        from services.llm_operations import run_blocking_shielded

        for blocked_phase, storage_fails in itertools.product(("prepare", "generation-slot", "close"), (False, True)):
            with self.subTest(phase=blocked_phase, storage_fails=storage_fails), TemporaryDirectory() as directory:
                events = []
                entered = asyncio.Event()
                ready = threading.Event()
                finished = threading.Event()
                loop = asyncio.get_running_loop()

                class Store(PromptEnhancementRecoveryStore):
                    def fail(self, *args, **kwargs):
                        if storage_fails:
                            raise RuntimeError("durable failure recording unavailable")
                        return super().fail(*args, **kwargs)

                    def mark_running(self, *args, **kwargs):
                        if blocked_phase == "generation-slot":
                            loop.call_soon_threadsafe(entered.set)
                            blocking()
                        return super().mark_running(*args, **kwargs)

                    def release(self, *_args, **_kwargs):
                        self_test.assertTrue(finished.is_set())
                        events.append("release")

                self_test = self
                manager = PromptEnhancementOperationManager(
                    Store(Path(directory) / "enhance", self.SECRET))

                def blocking():
                    if not ready.wait(3):
                        raise AssertionError("test background work was never released")
                    events.append("background-finished")
                    finished.set()

                async def prepare(*_args):
                    if blocked_phase == "prepare":
                        entered.set()
                        await run_blocking_shielded(blocking)

                async def execute(*_args):
                    events.append("execute")
                    return {"enhanced": "must not complete after cancellation"}

                async def close():
                    if blocked_phase == "close":
                        entered.set()
                        await run_blocking_shielded(blocking)
                    self.assertTrue(finished.is_set())
                    events.append("close")

                request_id = str(uuid.uuid4())
                manager.submit(request_id=request_id, request_digest="d" * 64,
                               execute=execute, prepare=prepare, close=close, **self.SCOPE)
                await asyncio.wait_for(entered.wait(), timeout=3)
                task = manager._active[uuid.UUID(request_id).hex].task
                task.cancel()
                await asyncio.sleep(0)
                self.assertNotIn("close", events)
                self.assertNotIn("release", events)
                ready.set()
                with self.assertRaises(RuntimeError if storage_fails else asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3)
                self.assertEqual(events[-3:], ["background-finished", "close", "release"])
                self.assertEqual(events.count("close"), 1)
                self.assertNotEqual(manager.status(request_id, **self.SCOPE)["status"], "completed")

    async def _wait_for(
        self,
        manager: PromptEnhancementOperationManager,
        request_id: str,
        expected: str,
    ) -> dict:
        for _ in range(300):
            status = manager.status(request_id, **self.SCOPE)
            if status is not None and status["status"] == expected:
                return status
            await asyncio.sleep(0.001)
        raise AssertionError(f"Prompt Enhance did not reach {expected}")

    def test_canonical_job_store_enqueue_failure_restart_and_retry(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.ClassDef)
            and item.name == "_CanonicalPromptEnhancementStore"
        )
        jobs = {}
        events = []

        class Results:
            retention_seconds = 3600.0

            @staticmethod
            def write(request_id, _result):
                return {
                    "path": f"results/{request_id}.result.json",
                    "schema": "maestro.prompt-enhancement-result.v1",
                    "sha256": "a" * 64,
                    "size": 10,
                }

            @staticmethod
            def read(_request_id, _reference):
                return {"enhanced": "private result"}

            @staticmethod
            def remove(_reference):
                events.append("remove-result")

        class Legacy:
            replay_result = None

            @staticmethod
            def replay(*_args, **_kwargs):
                return Legacy.replay_result

            @staticmethod
            def status(*_args, **_kwargs):
                return None

            @staticmethod
            def result(*_args, **_kwargs):
                return None

        def register(job, *, recovery_kind, defer_worker):
            events.append(("register", recovery_kind, defer_worker))
            job["kind"] = recovery_kind
            jobs[job["id"]] = job

        def acquire(_lock, job, **_kwargs):
            events.append("start")
            job["status"] = "running"
            job["phase"] = "loading"
            job["_generation_slot_owned"] = True
            return True

        def finish(job, status, **updates):
            events.append(("refund", status))
            job.update(updates)
            job["status"] = status
            return True

        namespace = {
            "Mapping": Mapping,
            "Any": object,
            "uuid": uuid,
            "hmac": __import__("hmac"),
            "copy": copy,
            "time": time,
            "os": __import__("os"),
            "_jobs": jobs,
            "RESOURCE_INTENT_TEXT": "text",
            "RESOURCE_EXECUTION_STANDARD": "standard",
            "PromptEnhancementRecoveryError": PromptEnhancementRecoveryError,
            "PromptEnhancementRecoveryConflictError": (
                PromptEnhancementRecoveryConflictError
            ),
            "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
            "QueueRecoveryAdapterError": QueueRecoveryAdapterError,
            "_queue_recovery_register_and_publish": register,
            "_credit_prepare_admission": lambda _job: events.append("credit-admit"),
            "acquire_and_start_generation_slot": acquire,
            "_gen_lock": object(),
            "_credit_prepare_dispatch": lambda _job: events.append("credit-dispatch"),
            "fail_queued_job": finish,
            "finish_job": finish,
            "block_resource_admission_failure": finish,
            "update_job": lambda job, **updates: job.update(updates) or True,
            "request_cancel": lambda job, **_kwargs: job.update(status="cancelled"),
            "consume_prompt_enhancement_result": lambda _job: None,
            "complete_prompt_enhancement_resource_release": lambda _job: True,
            "release_generation_slot": lambda _lock, job: (
                job.pop("_generation_slot_owned", False)
            ),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "canonical-prompt-store", "exec"), namespace)
        store = namespace["_CanonicalPromptEnhancementStore"](
            Results(), Legacy(),
        )
        request_id = str(uuid.uuid4())
        scope = {
            "account_key": "account",
            "project_instance_key": "project",
            "session_key": "session-a",
        }
        context = {
            "workspace": "workspace",
            "out_dir": "/project",
            "session_id": "session-a",
            "body": {"prompt": "private prompt"},
        }
        queued, created, _claim = store.bind(
            request_id=request_id,
            request_digest="b" * 64,
            job_context=context,
            **scope,
        )
        self.assertTrue(created)
        self.assertEqual(queued["status"], "queued")
        replay, created, _claim = store.bind(
            request_id=request_id,
            request_digest="b" * 64,
            job_context=context,
            **{**scope, "session_key": "session-b"},
        )
        self.assertFalse(created)
        self.assertEqual(replay["request_id"], uuid.UUID(request_id).hex)
        self.assertEqual(events.count(("register", "prompt_enhancement", True)), 1)
        self.assertEqual(store.mark_running(request_id, **scope)["status"], "running")
        failed = store.fail(request_id, **scope)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(events.count(("refund", "failed")), 1)

        restarted = namespace["_CanonicalPromptEnhancementStore"](
            Results(), Legacy(),
        )
        self.assertEqual(restarted.status(request_id, **scope)["status"], "failed")
        retry_id = str(uuid.uuid4())
        retry, retry_created, _claim = restarted.bind(
            request_id=retry_id,
            request_digest="c" * 64,
            job_context=context,
            **scope,
        )
        self.assertTrue(retry_created)
        self.assertEqual(retry["status"], "queued")
        namespace["_credit_prepare_admission"] = lambda _job: (
            (_ for _ in ()).throw(OSError("journal unavailable"))
        )
        namespace["block_resource_admission_failure"] = lambda job: (
            job.update(
                status="failed",
                phase="failed",
                recovery_state="terminal",
                resource_state="released",
                finished_at=1234.5,
            ) or True
        )
        admission_failed = restarted.mark_running(retry_id, **scope)
        self.assertEqual(admission_failed["status"], "failed")
        self.assertTrue(admission_failed["retryable"])
        Legacy.replay_result = {
            "request_id": uuid.uuid4().hex,
            "status": "queued",
            "phase": "queued",
        }
        historical, historical_created, _claim = restarted.bind(
            request_id=Legacy.replay_result["request_id"],
            request_digest="d" * 64,
            job_context=context,
            **scope,
        )
        self.assertFalse(historical_created)
        self.assertEqual(historical["status"], "failed")
        self.assertTrue(historical["retryable"])

    def test_canonical_result_reads_are_idempotent_after_lost_response(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.ClassDef)
            and item.name == "_CanonicalPromptEnhancementStore"
        )
        request_id = uuid.uuid4().hex
        reference = {
            "path": f"results/{request_id}.result.json",
            "schema": "maestro.prompt-enhancement-result.v1",
            "sha256": "a" * 64,
            "size": 10,
        }
        jobs = {
            request_id: {
                "id": request_id,
                "kind": "prompt_enhancement",
                "status": "completed",
                "params": {"_prompt_enhancement_operation": {
                    "account_key": "account",
                    "project_instance_key": "project",
                    "request_digest": "b" * 64,
                }},
                "prompt_result_reference": reference,
            },
        }

        class Results:
            retention_seconds = 3600.0

            @staticmethod
            def read(_request_id, _reference):
                return {"enhanced": "private result"}

        namespace = {
            "Mapping": Mapping,
            "Any": object,
            "uuid": uuid,
            "hmac": __import__("hmac"),
            "copy": copy,
            "time": time,
            "_jobs": jobs,
            "consume_prompt_enhancement_result": lambda _job: None,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "canonical-prompt-store", "exec"), namespace)
        store = namespace["_CanonicalPromptEnhancementStore"](
            Results(), object(),
        )
        scope = {
            "account_key": "account",
            "project_instance_key": "project",
            "session_key": "session",
        }
        self.assertEqual(store.result(request_id, **scope), {
            "enhanced": "private result",
        })
        self.assertEqual(store.result(request_id, **scope), {
            "enhanced": "private result",
        })
        self.assertEqual(jobs[request_id]["prompt_result_reference"], reference)
        cancelled = dict(jobs[request_id], status="cancelled", phase="inference")
        public = store._public(cancelled)
        self.assertEqual(public["phase"], "cancelled")
        self.assertEqual(public["stage"], "cancelled")

    def test_canonical_capacity_cleanup_evicts_only_expired_terminal_result(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        node = next(
            item for item in ast.parse(source).body
            if isinstance(item, ast.ClassDef)
            and item.name == "_CanonicalPromptEnhancementStore"
        )
        old_id = uuid.uuid4().hex
        current_id = uuid.uuid4().hex
        old_reference = {"path": f"results/{old_id}.result.json"}
        jobs = {
            old_id: {
                "id": old_id,
                "kind": "prompt_enhancement",
                "status": "completed",
                "finished_at": 1.0,
                "prompt_result_reference": old_reference,
            },
            current_id: {
                "id": current_id,
                "kind": "prompt_enhancement",
                "status": "running",
                "params": {"_prompt_enhancement_operation": {
                    "account_key": "account-b",
                    "project_instance_key": "project-b",
                    "request_digest": "b" * 64,
                }},
            },
        }
        events = []

        class Results:
            retention_seconds = 60.0
            attempts = 0

            def write(self, request_id, _result):
                self.attempts += 1
                if self.attempts == 1:
                    raise PromptEnhancementRecoveryCapacityError("full")
                return {"path": f"results/{request_id}.result.json"}

            @staticmethod
            def remove(reference):
                events.append(("removed", dict(reference)))

        def consume(job):
            reference = job.pop("prompt_result_reference", None)
            if reference is not None:
                job["prompt_result_consumed"] = True
            return reference

        def finish(job, status, **updates):
            job.update(updates)
            job["status"] = status
            return True

        namespace = {
            "Mapping": Mapping,
            "Any": object,
            "uuid": uuid,
            "hmac": __import__("hmac"),
            "copy": copy,
            "time": types.SimpleNamespace(time=lambda: 10_000.0),
            "_jobs": jobs,
            "PromptEnhancementRecoveryCapacityError": (
                PromptEnhancementRecoveryCapacityError
            ),
            "consume_prompt_enhancement_result": consume,
            "finish_job": finish,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[],
        )), "canonical-prompt-store", "exec"), namespace)
        store = namespace["_CanonicalPromptEnhancementStore"](
            Results(), object(),
        )
        completed = store.complete(
            current_id,
            result={"enhanced": "private"},
            account_key="account-b",
            project_instance_key="project-b",
            session_key="session-b",
        )
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(jobs[old_id]["prompt_result_consumed"])
        self.assertEqual(events, [("removed", old_reference)])

    async def test_completed_result_survives_restart_outside_generic_journal(self):
        private_prompt = "private prompt must not enter operation metadata"
        private_result = "private enhanced result survives by reference"
        request_id = str(uuid.uuid4())
        request_digest = hashlib.sha256(private_prompt.encode()).hexdigest()

        with TemporaryDirectory() as directory:
            root = Path(directory) / "prompt-enhancement"
            store = PromptEnhancementRecoveryStore(root, self.SECRET)
            manager = PromptEnhancementOperationManager(store)

            async def execute(progress, _cancellation):
                progress({
                    "phase": "generating",
                    "stage": "enhance",
                    "text": "bounded live output",
                })
                return {"original": private_prompt, "enhanced": private_result}

            queued = manager.submit(
                request_id=request_id,
                request_digest=request_digest,
                execute=execute,
                **self.SCOPE,
            )
            self.assertEqual(queued["status"], "queued")
            completed = await manager.wait(request_id, **self.SCOPE)
            self.assertEqual(completed["status"], "completed")
            self.assertTrue(completed["result_available"])

            metadata = store.metadata_path.read_text(encoding="ascii")
            self.assertNotIn(private_prompt, metadata)
            self.assertNotIn(private_result, metadata)
            self.assertNotIn("original", metadata)
            self.assertNotIn("enhanced", metadata)
            parsed = json.loads(metadata)
            record = parsed["records"][uuid.UUID(request_id).hex]
            self.assertEqual(record["operation_kind"], "prompt_enhancement")
            self.assertEqual(record["status"], "completed")
            self.assertEqual(
                set(record["result_reference"]),
                {"path", "schema", "sha256", "size"},
            )

            restarted = PromptEnhancementOperationManager(
                PromptEnhancementRecoveryStore(root, self.SECRET),
            )
            self.assertEqual(
                restarted.status(request_id, **self.SCOPE)["status"],
                "completed",
            )
            self.assertEqual(
                restarted.result(request_id, **self.SCOPE),
                {"original": private_prompt, "enhanced": private_result},
            )
            for field, foreign in (
                ("account_key", "account-two"),
                ("project_instance_key", "project-two"),
                ("session_key", "session-two"),
            ):
                scope = dict(self.SCOPE)
                scope[field] = foreign
                with self.subTest(foreign_field=field):
                    self.assertIsNone(restarted.status(request_id, **scope))
                    self.assertIsNone(restarted.result(request_id, **scope))

    def test_legacy_store_read_only_mode_never_reconciles_or_rewrites(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "legacy-prompt-enhancement"
            writable = PromptEnhancementRecoveryStore(root, self.SECRET)
            request_id = str(uuid.uuid4())
            writable.bind(
                request_id=request_id,
                request_digest="a" * 64,
                **self.SCOPE,
            )
            before = writable.metadata_path.read_bytes()
            legacy = PromptEnhancementRecoveryStore(
                root, self.SECRET, read_only=True,
            )
            self.assertEqual(
                legacy.status(request_id, **self.SCOPE)["status"], "queued",
            )
            self.assertEqual(legacy.metadata_path.read_bytes(), before)
            self.assertFalse((root / ".operations.lock").exists())
            with self.assertRaises(PromptEnhancementRecoveryError):
                legacy.reconcile_interrupted()
            with self.assertRaises(PromptEnhancementRecoveryError):
                legacy.bind(
                    request_id=str(uuid.uuid4()),
                    request_digest="b" * 64,
                    **self.SCOPE,
                )

    async def test_binding_is_idempotent_and_restart_never_auto_generates(self):
        request_id = str(uuid.uuid4())
        digest = "a" * 64
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        with TemporaryDirectory() as directory:
            root = Path(directory) / "prompt-enhancement"
            store = PromptEnhancementRecoveryStore(root, self.SECRET)
            manager = PromptEnhancementOperationManager(store)

            async def execute(_progress, cancellation):
                calls.append("first")
                entered.set()
                await release.wait()
                cancellation.checkpoint()
                return {"enhanced": "done"}

            def duplicate_admission():
                raise AssertionError("idempotent replay re-ran admission")

            first = manager.submit(
                request_id=request_id,
                request_digest=digest,
                execute=execute,
                **self.SCOPE,
            )
            replay = manager.submit(
                request_id=request_id,
                request_digest=digest,
                execute=lambda *_args: (_ for _ in ()).throw(
                    AssertionError("idempotent replay started another worker")
                ),
                admit=duplicate_admission,
                **self.SCOPE,
            )
            self.assertEqual(first["request_id"], replay["request_id"])
            await asyncio.wait_for(entered.wait(), timeout=1)
            self.assertEqual(calls, ["first"])
            with self.assertRaises(PromptEnhancementRecoveryConflictError):
                manager.submit(
                    request_id=request_id,
                    request_digest="b" * 64,
                    execute=execute,
                    **self.SCOPE,
                )
            manager.cancel(request_id, **self.SCOPE)
            release.set()
            cancelled = await manager.wait(request_id, **self.SCOPE)
            self.assertEqual(cancelled["status"], "cancelled")

            orphan_id = str(uuid.uuid4())
            orphan_store = PromptEnhancementRecoveryStore(
                Path(directory) / "orphan", self.SECRET,
            )
            bound, created, claim = orphan_store.bind(
                request_id=orphan_id,
                request_digest="c" * 64,
                **self.SCOPE,
            )
            self.assertEqual(bound["status"], "queued")
            self.assertTrue(created)
            self.assertIsNotNone(claim)
            restarted = PromptEnhancementOperationManager(
                PromptEnhancementRecoveryStore(
                    Path(directory) / "orphan", self.SECRET,
                ),
            )
            # Construction is read-only compatibility: canonical queue
            # startup owns interruption terminalization, while a legacy
            # sidecar remains inert and unchanged.
            self.assertEqual(
                restarted.status(orphan_id, **self.SCOPE)["status"], "queued",
            )
            no_auto_calls = []
            replayed = restarted.submit(
                request_id=orphan_id,
                request_digest="c" * 64,
                execute=lambda *_args: no_auto_calls.append(True),
                **self.SCOPE,
            )
            await asyncio.sleep(0)
            self.assertEqual(replayed["status"], "queued")
            self.assertEqual(no_auto_calls, [])

    async def test_all_states_are_explicit_and_consumption_is_bounded(self):
        now = [0.0]
        with TemporaryDirectory() as directory:
            store = PromptEnhancementRecoveryStore(
                Path(directory) / "prompt-enhancement",
                self.SECRET,
                retention_seconds=10,
                max_records=4,
                clock=lambda: now[0],
            )
            manager = PromptEnhancementOperationManager(store)
            completed_id = str(uuid.uuid4())

            async def complete(_progress, _cancellation):
                return {"enhanced": "one-use private result"}

            queued = manager.submit(
                request_id=completed_id,
                request_digest="d" * 64,
                execute=complete,
                **self.SCOPE,
            )
            self.assertEqual(queued["status"], "queued")
            completed = await self._wait_for(manager, completed_id, "completed")
            self.assertTrue(completed["result_available"])
            self.assertEqual(
                manager.consume_result(completed_id, **self.SCOPE),
                {"enhanced": "one-use private result"},
            )
            self.assertIsNone(manager.consume_result(completed_id, **self.SCOPE))
            consumed = manager.status(completed_id, **self.SCOPE)
            self.assertEqual(consumed["status"], "completed")
            self.assertFalse(consumed["result_available"])
            self.assertTrue(consumed["result_consumed"])

            failed_id = str(uuid.uuid4())

            async def fail(_progress, _cancellation):
                raise RuntimeError("private engine detail")

            manager.submit(
                request_id=failed_id,
                request_digest="e" * 64,
                execute=fail,
                **self.SCOPE,
            )
            failed = await self._wait_for(manager, failed_id, "failed")
            self.assertTrue(failed["retryable"])
            self.assertNotIn("engine detail", repr(failed))

            running_id = str(uuid.uuid4())
            gate = asyncio.Event()

            async def run(_progress, cancellation):
                await gate.wait()
                cancellation.checkpoint()
                return {"enhanced": "late"}

            manager.submit(
                request_id=running_id,
                request_digest="f" * 64,
                execute=run,
                **self.SCOPE,
            )
            await self._wait_for(manager, running_id, "running")
            cancelled = manager.cancel(running_id, **self.SCOPE)
            self.assertEqual(cancelled["status"], "cancelled")
            gate.set()
            await manager.wait(running_id, **self.SCOPE)

            now[0] = 11.0
            self.assertIsNone(manager.status(completed_id, **self.SCOPE))
            self.assertIsNone(manager.status(failed_id, **self.SCOPE))
            self.assertIsNone(manager.status(running_id, **self.SCOPE))


class H3ComposeRouteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Actual route orchestration with CPU-only injected execution boundaries."""

    def setUp(self):
        from fastapi import HTTPException
        from starlette.responses import JSONResponse
        names = {"_submit_h3_prompt_rewriter_operation", "llm_h3_prompt_rewriter_apply", "llm_enhance_prompt",
            "_restore_h3_prompt_rewriter_cleanup", "_continue_h3_prompt_rewriter_cleanup",
            "_run_startup_recovery_background", "_ScopedPromptEnhancementRequest",
            "_seal_prompt_enhancement_images", "_materialize_prompt_enhancement_images",
            "_remove_prompt_enhancement_snapshots"}
        nodes = [node for node in ast.parse((APP / "launch.py").read_text()).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names]
        for node in nodes:
            node.decorator_list = []
        self.namespace = {"__file__": str(APP / "launch.py"), "Path": Path,
            "Request": object, "Any": object, "Mapping": Mapping,
            "HTTPException": HTTPException, "JSONResponse": JSONResponse,
            "copy": copy, "os": __import__("os"), "stat": __import__("stat"),
            "hashlib": hashlib, "hmac": __import__("hmac"), "_CPU_TEXT_OPERATIONS": set(),
            "_LLM_CHAT_MAX_IMAGE_BYTES": 32 * 1024 * 1024,
            "_LLM_ENHANCE_MAX_TOTAL_IMAGE_BYTES": 64 * 1024 * 1024,
            "_normalize_llm_route_request_id": lambda value: uuid.UUID(value).hex,
            "_request_project_workspace": lambda _request, value: value,
            "_promote_external_llm_request": lambda _request: None,
            "_resolve_prompt_enhancement_images": lambda *_args: [],
            "_prompt_enhancement_operation_scope": lambda *_: ("account", "b" * 64, "session"),
            "_require_project_access": mock.Mock(),
            "_existing_workspace_dir": lambda _: "/unused/project",
            "_llm_route_effective_input_digest": lambda _kind, value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
            "PromptEnhancementRecoveryConflictError": PromptEnhancementRecoveryConflictError,
            "PromptEnhancementRecoveryError": PromptEnhancementRecoveryError}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP / "launch.py"), "exec"), self.namespace)
        self.request_id = uuid.uuid4().hex
        self.body = {"request_id": self.request_id, "project_instance": "b" * 64,
            "workspace": "project", "prompt": "A violent fictional battle under rain.",
            "engine": "h3_rewriter", "rewrite_mode": "t2va", "model_type": "minimax_h3", "duration_seconds": 10}
        self.request = types.SimpleNamespace(state=types.SimpleNamespace(), headers={}, base_url="", client=None)
        self.job = {"id": self.request_id, "kind": "prompt_enhancement", "execution_attempt": 1, "status": "queued"}
        self.manager = types.SimpleNamespace(submit=mock.Mock(return_value={"request_id": self.request_id, "status": "queued"}),
            _store=types.SimpleNamespace(_canonical=mock.Mock(return_value=self.job)),
            result=mock.Mock(), status=mock.Mock(return_value={"status": "completed"}))
        self.namespace["_prompt_enhancement_operation_manager"] = self.manager
        self.snapshot = types.SimpleNamespace(commitment="c" * 64, recheck=mock.Mock(),
            gpu_binding=mock.Mock(return_value=object()), build_execution_admission=mock.Mock(return_value=object()))
        self.lease = mock.Mock()
        self.lease.intent = {"state": "request_attempted"}
        self.lease.return_value = True
        from services import h3_prompt_rewriter_config, h3_prompt_rewriter_gpu_lease, h3_prompt_rewriter_runtime, job_lifecycle
        self.stack = __import__("contextlib").ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(h3_prompt_rewriter_config, "load_runtime_snapshot", return_value=self.snapshot))
        self.factory = self.stack.enter_context(mock.patch.object(h3_prompt_rewriter_gpu_lease, "H3PromptRewriterGpuLease", return_value=self.lease))
        self.checkpoint = self.stack.enter_context(mock.patch.object(job_lifecycle, "checkpoint_prompt_enhancement_gpu_intent", return_value=True))
        self.execute_mock = self.stack.enter_context(mock.patch.object(h3_prompt_rewriter_runtime, "execute_h3_prompt_rewrite", return_value=object()))

    async def submit(self, body=None, images=()):
        return await self.namespace["_submit_h3_prompt_rewriter_operation"](self.request, body or self.body, "project", list(images))

    async def test_exact_queued_submission_preserves_prompt_and_orders_private_frames(self):
        from services import h3_prompt_rewriter as rewrite, h3_prompt_rewriter_runtime as runtime
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("first.png", "last.png")]
            for index, path in enumerate(paths):
                path.write_bytes(bytes([index + 1]) * 20)
            body = {**self.body, "rewrite_mode": "fl2va"}
            response = await self.submit(body, [str(path) for path in paths])
            self.assertEqual(response.status_code, 202)
            callbacks = self.manager.submit.call_args.kwargs
            handle = LlmCancellationHandle()
            await callbacks["prepare"](lambda _: None, handle)
            self.lease.acquire.assert_called_once()
            self.job["status"] = "running"
            self.snapshot.build_execution_admission.side_effect = lambda *_args, **kw: (kw["child_reaped_observer"](False), kw["child_reaped_observer"](True), object())[-1]
            captured = {}
            def execute(_admission, canonical, **kw):
                captured.update(kw)
                kw["child_reaped_observer"](False)
                self.assertEqual([Path(item["path"]).read_bytes() for item in kw["image_bindings"]], [path.read_bytes() for path in paths])
                self.assertTrue(all((Path(item["trust_root"]).stat().st_mode & 0o777) == 0o700 for item in kw["image_bindings"]))
                kw["child_reaped_observer"](True)
                return object()
            self.execute_mock.side_effect = execute
            with mock.patch.object(rewrite, "create_executed_rewrite_preview", return_value={"fixture": "comparison"}):
                result = await callbacks["execute"](lambda _: None, handle, {"job": self.job, "generation_slot_owned": True})
            self.assertEqual(result["original"], body["prompt"])
            self.assertEqual(result["enhanced"], body["prompt"])
            self.assertEqual([role["role"] for role in result["h3_rewrite_request"]["image_roles"]], ["first_frame", "last_frame"])
            self.assertFalse(any(Path(binding["path"]).exists() for binding in captured["image_bindings"]))
            self.assertEqual(self.lease.before_start.call_count, 2)
            self.assertEqual(self.checkpoint.call_count, 2)
            await callbacks["close"]()
            self.lease.close.assert_called_once_with(child_reaped=True)

    async def test_invalid_duration_model_mode_images_or_project_never_admits(self):
        from fastapi import HTTPException
        for changes in ({"duration_seconds": 10.5}, {"duration_seconds": True}, {"duration_seconds": 16},
                        {"model_type": "minimax_h3_ref2va"}, {"rewrite_mode": "ref2va"},
                        {"rewrite_mode": "i2va"}, {"project_instance": "f" * 64}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException):
                await self.submit({**self.body, **changes})
        self.manager.submit.assert_not_called()
        self.factory.assert_not_called()

    async def test_startup_cleanup_reaches_fifth_intent_after_readiness_without_retrying_failures(self):
        from services import h3_prompt_rewriter_gpu_lease, queue_recovery_adapter
        candidates = [{"id": str(index), "kind": "prompt_enhancement", "execution_attempt": 1,
            "status": "failed", "recovery_cursor": {"h3_prompt_rewriter_child_reaped": True,
                "h3_prompt_rewriter_gpu": {"state": "withdraw_attempted", "request_id": str(index)}}}
            for index in range(5)]
        ready = []
        stop = __import__("threading").Event()
        self.namespace.update({"_startup_recovery_stop": stop,
            "_startup_recovery_once_complete": True,
            "_wait_for_listener_before_recovered_execution": lambda: None,
            "_startup_recovery_launch_operation": lambda operation: (operation(), True)[1],
            "_start_sample_campaign_preemption_after_recovery": lambda: None,
            "_set_startup_recovery_state": ready.append,
            "_reset_startup_recovery_lifecycle_after_stop": mock.Mock()})
        attempts = []
        def restore(_binding, intent, **_kwargs):
            attempts.append(intent["request_id"])
            if len(attempts) <= 4:
                raise ValueError("Exact saved ownership is unavailable")
            self.assertEqual(ready, ["ready"], "continuation must follow public readiness")
            return self.lease
        with mock.patch.object(queue_recovery_adapter, "prompt_enhancement_gpu_cleanup_pending", return_value=True), mock.patch.object(h3_prompt_rewriter_gpu_lease, "H3PromptRewriterGpuLease", types.SimpleNamespace(restore_from_intent=restore)):
            remainder = self.namespace["_restore_h3_prompt_rewriter_cleanup"](candidates, stop_event=stop)
            self.assertEqual(attempts, ["0", "1", "2", "3"])
            self.assertEqual(remainder, (candidates[4],))
            self.namespace["_h3_prompt_rewriter_cleanup_remainder"] = remainder
            self.namespace["_run_startup_recovery_background"]()
        self.assertEqual(attempts, ["0", "1", "2", "3", "4"])
        self.assertEqual(self.namespace["_h3_prompt_rewriter_cleanup_remainder"], ())
        self.lease.close.assert_called_once_with(child_reaped=True, confirm_seconds=2)
        self.factory.assert_not_called()

    async def test_startup_cleanup_stop_preserves_unvisited_intents(self):
        from services import h3_prompt_rewriter_gpu_lease, queue_recovery_adapter
        stop = __import__("threading").Event()
        candidates = [{"kind": "prompt_enhancement", "execution_attempt": 1,
            "recovery_cursor": {"h3_prompt_rewriter_child_reaped": True,
                "h3_prompt_rewriter_gpu": {"state": "withdraw_attempted"}}} for _ in range(5)]
        restore = mock.Mock(return_value=self.lease)
        self.lease.close.side_effect = lambda **_kwargs: stop.set()
        with mock.patch.object(queue_recovery_adapter, "prompt_enhancement_gpu_cleanup_pending", return_value=True), mock.patch.object(h3_prompt_rewriter_gpu_lease, "H3PromptRewriterGpuLease", types.SimpleNamespace(restore_from_intent=restore)):
            self.namespace.update({"_startup_recovery_stop": stop,
                "_h3_prompt_rewriter_cleanup_remainder": tuple(candidates)})
            self.namespace["_continue_h3_prompt_rewriter_cleanup"]()
        self.assertEqual(restore.call_count, 1)
        remainder = self.namespace["_h3_prompt_rewriter_cleanup_remainder"]
        self.assertEqual(len(remainder), 4)
        self.assertIs(remainder[0], candidates[1])
        stop.clear()
        self.lease.close.side_effect = None
        with mock.patch.object(queue_recovery_adapter, "prompt_enhancement_gpu_cleanup_pending", return_value=True), mock.patch.object(h3_prompt_rewriter_gpu_lease, "H3PromptRewriterGpuLease", types.SimpleNamespace(restore_from_intent=restore)):
            self.namespace["_continue_h3_prompt_rewriter_cleanup"]()
        self.assertEqual(restore.call_count, 5, "resume visits only the four unattempted intents")
        self.assertEqual(self.namespace["_h3_prompt_rewriter_cleanup_remainder"], ())
        self.factory.assert_not_called()

    async def test_public_enhance_route_enters_explicit_branch_without_ordinary_runtime(self):
        async def read():
            return copy.deepcopy(self.body)
        self.request.json = read
        ordinary = mock.Mock(side_effect=AssertionError("ordinary runtime must not be selected"))
        self.namespace["_prompt_enhancement_runtime_snapshot"] = ordinary
        response = await self.namespace["llm_enhance_prompt"](self.request)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.manager.submit.call_args.kwargs["job_context"]["body"]["engine"], "h3_rewriter")
        ordinary.assert_not_called()

    async def test_reauthorization_and_failed_spawn_checkpoint_prevent_execution(self):
        from fastapi import HTTPException
        from services.llm_cancellation import LlmRequestCancelled
        await self.submit()
        callbacks = self.manager.submit.call_args.kwargs
        handle = LlmCancellationHandle()
        self.namespace["_require_project_access"].side_effect = HTTPException(403, "revoked")
        with self.assertRaises(HTTPException):
            await callbacks["prepare"](lambda _: None, handle)
        self.factory.assert_not_called()
        self.namespace["_require_project_access"].side_effect = None
        await callbacks["prepare"](lambda _: None, handle)
        self.checkpoint.return_value = False
        self.snapshot.build_execution_admission.side_effect = lambda *_args, **kw: kw["child_reaped_observer"](False)
        with self.assertRaises(LlmRequestCancelled):
            await callbacks["execute"](lambda _: None, handle, {"job": self.job, "generation_slot_owned": True})
        await callbacks["close"]()
        self.lease.close.assert_called_once_with(child_reaped=True)

    async def test_apply_is_scoped_integrity_bound_and_never_consumes_or_generates(self):
        from services import h3_prompt_rewriter as rewrite
        from fastapi import HTTPException
        canonical = rewrite.create_rewrite_request(original_prompt=self.body["prompt"], mode="t2va")
        preview = rewrite._build_rewrite_preview(canonical,
            (self.body["prompt"], "Base comparison", "H3 comparison"), execution_receipt_sha256="a" * 64)
        result = {"h3_rewrite_request": canonical, "h3_rewrite_preview": preview}
        self.manager.result.return_value = result
        body = {"workspace": "project", "project_instance": "b" * 64, "request_commitment": canonical["commitment"],
            "preview_commitment": preview["commitment"], "selected_kind": "adapted"}
        async def read():
            return body
        self.request.json = read
        apply = self.namespace["llm_h3_prompt_rewriter_apply"]
        for _ in range(2):
            response = await apply(self.request, self.request_id)
            self.assertEqual(response["enhanced"], "H3 comparison")
        self.assertEqual(result["h3_rewrite_preview"], preview)
        self.manager.submit.assert_not_called()
        body["preview_commitment"] = "f" * 64
        with self.assertRaises(HTTPException) as error:
            await apply(self.request, self.request_id)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.manager.result.call_args.kwargs["account_key"], "account")
        self.manager.result.return_value = {"h3_rewrite_request": canonical,
            "h3_rewrite_preview": rewrite.create_rewrite_preview(canonical,
                deterministic=self.body["prompt"], base="Base comparison", adapted="H3 comparison")}
        with self.assertRaises(HTTPException) as error:
            await apply(self.request, self.request_id)
        self.assertEqual(error.exception.status_code, 400)
        self.manager.status.return_value = {"status": "cancelled"}
        with self.assertRaises(HTTPException) as error:
            await apply(self.request, self.request_id)
        self.assertEqual(error.exception.status_code, 404)

    async def test_startup_keeps_unconfirmed_children_and_only_reconciles_saved_intent(self):
        from services import h3_prompt_rewriter_gpu_lease, queue_recovery_adapter
        candidates = [{"kind": "prompt_enhancement", "execution_attempt": 1, "status": "failed",
            "recovery_cursor": {"h3_prompt_rewriter_gpu": {"state": "withdraw_attempted"},
                **({"h3_prompt_rewriter_child_reaped": reaped} if reaped is not None else {})}}
            for reaped in (None, False, True)]
        restore = mock.Mock(return_value=self.lease)
        with mock.patch.object(queue_recovery_adapter, "prompt_enhancement_gpu_cleanup_pending", return_value=True), mock.patch.object(h3_prompt_rewriter_gpu_lease, "H3PromptRewriterGpuLease", types.SimpleNamespace(restore_from_intent=restore)):
            self.namespace["_restore_h3_prompt_rewriter_cleanup"](candidates)
        restore.assert_called_once()
        self.lease.close.assert_called_once_with(child_reaped=True, confirm_seconds=2)
        callback = restore.call_args.kwargs["persist_intent"]
        self.assertTrue(callback({"state": "closed"}))
        self.assertIs(self.checkpoint.call_args.args[0], candidates[2])
        self.assertEqual(self.checkpoint.call_args.kwargs["child_reaped"], True)
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
