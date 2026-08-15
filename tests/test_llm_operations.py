"""Behavioral contracts for detached LLM preparation and request recovery."""

from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
import sys
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
from services.llm_operations import (
    ChatRequestMismatchError,
    LlmChatOperationManager,
    LlmPreparationManager,
    LlmOperationCapacityError,
    LlmRouteAdmissionError,
    LlmRouteOperationConflictError,
    LlmRouteOperationManager,
    ROUTE_OPERATION_TTL_SECONDS,
    run_blocking_shielded,
)


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
            "llm_enhance_prompt",
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

        namespace = {
            "Request": object,
            "asyncio": asyncio,
            "HTTPException": HTTPException,
            "_gen_lock": threading.RLock(),
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

        def resolve_assist(_body, resolved_selection):
            assist_checks.append((
                resident["model_id"], resolved_selection["model_id"],
            ))
            return {"retry_on_refusal": True}

        namespace["_resolved_local_response_assist"] = resolve_assist

        class Request:
            state = types.SimpleNamespace(
                maestro_llm_progress_callback=lambda _event: None,
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


if __name__ == "__main__":
    unittest.main()
