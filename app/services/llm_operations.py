"""Bounded, in-memory lifecycle tracking for LLM operations.

Preparation state is content-free. Chat state may retain only the current
partial/final response needed for owner-scoped recovery; prompt text, messages,
media, rejected-attempt output, and exception text must never be retained here.
All records disappear on eviction, TTL expiry, or process restart.
"""

from __future__ import annotations

import asyncio
import hmac
import math
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

PREPARE_TTL_SECONDS = 15 * 60
PREPARE_READY_TTL_SECONDS = 45
PREPARE_MAX_OPERATIONS = 256
CHAT_PROGRESS_MAX_CHARS = 512 * 1024
_CHAT_PROGRESS_PHASES = frozenset({
    "queued",
    "loading",
    "prefill",
    "inference",
    "thinking",
    "generating",
    "detecting",
    "retrying",
    "finalizing",
    "complete",
    "completed",
    "failed",
})


class LlmOperationCapacityError(RuntimeError):
    """The bounded in-memory operation table is temporarily full."""


@dataclass
class _PreparationOperation:
    operation_id: str
    owner_key: str
    project_key: str
    selection_key: str
    purpose: str
    created_at: float
    updated_at: float
    status: str = "preparing"
    phase: str = "queued"
    task: asyncio.Task[None] | None = None


class LlmPreparationManager:
    """Track bounded, owner-scoped background preparation operations."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PREPARE_TTL_SECONDS,
        max_operations: int = PREPARE_MAX_OPERATIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_operations <= 0:
            raise ValueError("max_operations must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._max_operations = int(max_operations)
        self._clock = clock
        self._lock = threading.RLock()
        self._operations: dict[str, _PreparationOperation] = {}

    def _expired(self, operation: _PreparationOperation, now: float) -> bool:
        if operation.status == "preparing":
            return False
        ttl_seconds = self._ttl_seconds
        if operation.status == "ready":
            # The runtime's idle lease currently expires after 60 seconds. A
            # ready receipt is only a short hand-off signal; it must disappear
            # first so a foregrounded client starts a fresh exact-identity
            # preparation instead of trusting a model that may be unloaded.
            ttl_seconds = min(ttl_seconds, PREPARE_READY_TTL_SECONDS)
        return now - operation.updated_at >= ttl_seconds

    def _prune_locked(self, now: float) -> None:
        expired = [
            operation_id
            for operation_id, operation in self._operations.items()
            if self._expired(operation, now)
        ]
        for operation_id in expired:
            self._operations.pop(operation_id, None)

        overflow = len(self._operations) - self._max_operations
        if overflow > 0:
            oldest = sorted(
                (
                    operation for operation in self._operations.values()
                    if operation.status != "preparing"
                ),
                key=lambda item: item.updated_at,
            )
            for operation in oldest[:overflow]:
                self._operations.pop(operation.operation_id, None)

    @staticmethod
    def _public(operation: _PreparationOperation) -> dict[str, Any]:
        result: dict[str, Any] = {
            "operation_id": operation.operation_id,
            "status": operation.status,
            "phase": operation.phase,
            "retryable": operation.status == "failed",
        }
        if operation.status == "failed":
            result["error"] = {
                "code": "preparation_failed",
                "message": "LLM preparation failed",
                "retryable": True,
            }
        return result

    async def start(
        self,
        *,
        owner_key: str,
        project_key: str,
        selection_key: str,
        purpose: str,
        prepare: Callable[[], object],
    ) -> dict[str, Any]:
        """Start or coalesce one exact, authorized preparation operation."""
        if not all(
            isinstance(value, str) and value
            for value in (owner_key, project_key, selection_key, purpose)
        ):
            raise ValueError("opaque preparation keys are required")
        if not callable(prepare):
            raise TypeError("prepare must be callable")

        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            for operation in self._operations.values():
                if (
                    # Coalesce only an in-flight exact request. A prior ready
                    # record cannot prove that its artifact/runtime/profile is
                    # still resident, so a later POST must re-enter the exact
                    # model lease (normally an immediate idempotent check).
                    operation.status == "preparing"
                    and hmac.compare_digest(operation.owner_key, owner_key)
                    and hmac.compare_digest(operation.project_key, project_key)
                    and hmac.compare_digest(operation.selection_key, selection_key)
                ):
                    return self._public(operation)

            if len(self._operations) >= self._max_operations:
                terminal = [
                    operation for operation in self._operations.values()
                    if operation.status != "preparing"
                ]
                if terminal:
                    oldest = min(terminal, key=lambda item: item.updated_at)
                    self._operations.pop(oldest.operation_id, None)
            if len(self._operations) >= self._max_operations:
                raise LlmOperationCapacityError

            operation = _PreparationOperation(
                operation_id=uuid.uuid4().hex,
                owner_key=owner_key,
                project_key=project_key,
                selection_key=selection_key,
                purpose=purpose,
                created_at=now,
                updated_at=now,
            )
            self._operations[operation.operation_id] = operation
            self._prune_locked(now)
            operation.task = asyncio.create_task(self._run(operation, prepare))
            return self._public(operation)

    async def _run(
        self,
        operation: _PreparationOperation,
        prepare: Callable[[], object],
    ) -> None:
        with self._lock:
            if self._operations.get(operation.operation_id) is operation:
                operation.phase = "loading"
                operation.updated_at = self._clock()
        try:
            # The task owns the worker future. Cancelling an HTTP waiter does
            # not cancel this manager-owned preparation.
            await asyncio.shield(asyncio.to_thread(prepare))
        except asyncio.CancelledError:
            with self._lock:
                if self._operations.get(operation.operation_id) is operation:
                    operation.status = "failed"
                    operation.phase = "failed"
                    operation.updated_at = self._clock()
            raise
        except Exception:  # noqa: BLE001 - public state must stay redacted
            with self._lock:
                if self._operations.get(operation.operation_id) is operation:
                    operation.status = "failed"
                    operation.phase = "failed"
                    operation.updated_at = self._clock()
            return
        with self._lock:
            if self._operations.get(operation.operation_id) is operation:
                operation.status = "ready"
                operation.phase = "ready"
                operation.updated_at = self._clock()

    def status(
        self,
        operation_id: str,
        *,
        owner_key: str,
        project_key: str,
    ) -> dict[str, Any] | None:
        """Return fixed public state, or None for expired/foreign operations."""
        if not all(
            isinstance(value, str) and value
            for value in (operation_id, owner_key, project_key)
        ):
            return None
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            operation = self._operations.get(operation_id)
            if operation is None:
                return None
            if not (
                hmac.compare_digest(operation.owner_key, owner_key)
                and hmac.compare_digest(operation.project_key, project_key)
            ):
                return None
            return self._public(operation)


class ChatRequestMismatchError(ValueError):
    """A request id was reused for different effective Chat input."""


class ChatAdmissionError(RuntimeError):
    """The bounded Chat executor has no free admission slot."""


@dataclass
class _ChatOperation:
    request_id: str
    owner_key: str
    project_key: str
    request_digest: str
    created_at: float
    updated_at: float
    status: str = "running"
    phase: str = "queued"
    partial_text: str = ""
    attempt: int = 1
    attempt_cap: int = 1
    token_count: int = 0
    elapsed_seconds: float = 0.0
    live_tps: float | None = None
    avg_tps: float | None = None
    result: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = None


class LlmChatOperationManager:
    """Bounded in-memory Chat recovery without persistence or logging."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PREPARE_TTL_SECONDS,
        max_operations: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_operations <= 0:
            raise ValueError("max_operations must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._max_operations = int(max_operations)
        self._clock = clock
        self._lock = threading.RLock()
        self._operations: dict[str, _ChatOperation] = {}

    def _prune_locked(self, now: float) -> None:
        expired = [
            request_id
            for request_id, operation in self._operations.items()
            if (
                operation.status != "running"
                and now - operation.updated_at >= self._ttl_seconds
            )
        ]
        for request_id in expired:
            self._operations.pop(request_id, None)
        overflow = len(self._operations) - self._max_operations
        if overflow > 0:
            oldest = sorted(
                (
                    operation for operation in self._operations.values()
                    if operation.status != "running"
                ),
                key=lambda item: item.updated_at,
            )
            for operation in oldest[:overflow]:
                self._operations.pop(operation.request_id, None)

    @staticmethod
    def _public(operation: _ChatOperation) -> dict[str, Any]:
        response: dict[str, Any] = {
            "request_id": operation.request_id,
            "status": operation.status,
            "phase": operation.phase,
            "partial_text": operation.partial_text,
            "attempt": operation.attempt,
            "attempt_limit": operation.attempt_cap,
            "generated_tokens_approx": operation.token_count,
            "elapsed_seconds": operation.elapsed_seconds,
            "live_tps": operation.live_tps,
            "average_tps": operation.avg_tps,
            "retryable": operation.status == "failed",
        }
        if operation.status == "completed" and operation.result is not None:
            response["result"] = dict(operation.result)
        elif operation.status == "failed":
            response["error"] = {
                "code": "chat_failed",
                "message": "LLM chat failed",
                "retryable": True,
            }
        return response

    def _update_progress(
        self,
        operation: _ChatOperation,
        event: dict[str, Any],
    ) -> None:
        """Apply one bounded, content-minimal runtime progress snapshot."""
        if not isinstance(event, dict):
            return
        with self._lock:
            if (
                self._operations.get(operation.request_id) is not operation
                or operation.status != "running"
            ):
                return

            attempt_cap = event.get("attempt_cap")
            if (
                isinstance(attempt_cap, int)
                and not isinstance(attempt_cap, bool)
                and 1 <= attempt_cap <= 2
            ):
                operation.attempt_cap = attempt_cap

            attempt = event.get("attempt")
            if (
                isinstance(attempt, int)
                and not isinstance(attempt, bool)
                and 1 <= attempt <= operation.attempt_cap
            ):
                if attempt > operation.attempt:
                    # Never retain rejected first-attempt text once the
                    # response-only retry begins.
                    operation.partial_text = ""
                    operation.token_count = 0
                    operation.elapsed_seconds = 0.0
                    operation.live_tps = None
                    operation.avg_tps = None
                operation.attempt = attempt

            phase = event.get("phase")
            if isinstance(phase, str) and phase in _CHAT_PROGRESS_PHASES:
                operation.phase = phase

            text = event.get("text")
            if isinstance(text, str):
                operation.partial_text = text[-CHAT_PROGRESS_MAX_CHARS:]

            token_count = event.get("generated_tokens_approx")
            if (
                isinstance(token_count, int)
                and not isinstance(token_count, bool)
                and token_count >= 0
            ):
                operation.token_count = min(token_count, 10_000_000)

            elapsed_seconds = event.get("elapsed_seconds")
            if (
                isinstance(elapsed_seconds, (int, float))
                and not isinstance(elapsed_seconds, bool)
                and math.isfinite(elapsed_seconds)
                and elapsed_seconds >= 0
            ):
                operation.elapsed_seconds = min(
                    float(elapsed_seconds), 31_536_000.0,
                )

            for public_name, event_name in (
                ("live_tps", "live_tps"),
                ("avg_tps", "average_tps"),
            ):
                value = event.get(event_name)
                if value is None and event.get("done") is True:
                    setattr(operation, public_name, None)
                    continue
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                ):
                    setattr(operation, public_name, float(value))
            operation.updated_at = self._clock()

    def submit(
        self,
        *,
        request_id: str,
        owner_key: str,
        project_key: str,
        request_digest: str,
        execute: Callable[
            [Callable[[dict[str, Any]], None]],
            Awaitable[dict[str, Any]],
        ],
        admit: Callable[[], bool],
        release: Callable[[], None],
    ) -> dict[str, Any] | None:
        """Submit once; return None when the opaque owner scope mismatches."""
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            existing = self._operations.get(request_id)
            if existing is not None:
                if not (
                    hmac.compare_digest(existing.owner_key, owner_key)
                    and hmac.compare_digest(existing.project_key, project_key)
                ):
                    return None
                if not hmac.compare_digest(
                    existing.request_digest, request_digest,
                ):
                    raise ChatRequestMismatchError
                return self._public(existing)
            if len(self._operations) >= self._max_operations:
                terminal = [
                    operation for operation in self._operations.values()
                    if operation.status != "running"
                ]
                if terminal:
                    oldest = min(terminal, key=lambda item: item.updated_at)
                    self._operations.pop(oldest.request_id, None)
            if len(self._operations) >= self._max_operations:
                raise LlmOperationCapacityError
            if not admit():
                raise ChatAdmissionError
            operation = _ChatOperation(
                request_id=request_id,
                owner_key=owner_key,
                project_key=project_key,
                request_digest=request_digest,
                created_at=now,
                updated_at=now,
            )
            self._operations[request_id] = operation
            self._prune_locked(now)
            try:
                operation.task = asyncio.create_task(
                    self._run(operation, execute, release),
                )
            except Exception:
                self._operations.pop(request_id, None)
                release()
                raise
            return self._public(operation)

    async def _run(
        self,
        operation: _ChatOperation,
        execute: Callable[
            [Callable[[dict[str, Any]], None]],
            Awaitable[dict[str, Any]],
        ],
        release: Callable[[], None],
    ) -> None:
        with self._lock:
            if self._operations.get(operation.request_id) is operation:
                operation.phase = "inference"
                operation.updated_at = self._clock()
        try:
            result = await asyncio.shield(execute(
                lambda event: self._update_progress(operation, event),
            ))
        except asyncio.CancelledError:
            with self._lock:
                if self._operations.get(operation.request_id) is operation:
                    operation.status = "failed"
                    operation.phase = "failed"
                    operation.partial_text = ""
                    operation.updated_at = self._clock()
            raise
        except Exception:  # noqa: BLE001 - public state must stay redacted
            with self._lock:
                if self._operations.get(operation.request_id) is operation:
                    operation.status = "failed"
                    operation.phase = "failed"
                    operation.partial_text = ""
                    operation.updated_at = self._clock()
            return
        finally:
            try:
                release()
            except Exception:  # noqa: BLE001, S110 - never lose a ready result
                pass
        with self._lock:
            if self._operations.get(operation.request_id) is operation:
                # Result content exists only in this bounded process-memory
                # recovery record and is discarded on TTL/eviction/restart.
                operation.result = dict(result)
                operation.status = "completed"
                operation.phase = "completed"
                result_text = result.get("text")
                if isinstance(result_text, str):
                    operation.partial_text = result_text[-CHAT_PROGRESS_MAX_CHARS:]
                operation.updated_at = self._clock()

    def status(
        self,
        request_id: str,
        *,
        owner_key: str,
        project_key: str,
    ) -> dict[str, Any] | None:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            operation = self._operations.get(request_id)
            if operation is None:
                return None
            if not (
                hmac.compare_digest(operation.owner_key, owner_key)
                and hmac.compare_digest(operation.project_key, project_key)
            ):
                return None
            return self._public(operation)


async def run_blocking_shielded(
    function: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run blocking work off-loop and keep it alive if its waiter disconnects."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))

    def consume_result(done: asyncio.Task[Any]) -> None:
        if not done.cancelled():
            done.exception()

    task.add_done_callback(consume_result)
    return await asyncio.shield(task)


llm_preparation_manager = LlmPreparationManager()
llm_chat_operation_manager = LlmChatOperationManager()
