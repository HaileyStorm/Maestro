"""Bounded, in-memory lifecycle tracking for LLM operations.

Preparation state is content-free. Public Chat and route lifecycle state retains
only bounded generated partial/final output needed for exact owner/project
recovery; effective input, prompt text, messages, media references, provider
selection, rejected-attempt output, and exception text are never projected.
Private terminal route results are exact-scope snapshots, never persisted, and
are copied again on read. All records disappear on eviction, TTL expiry, or
process restart.
"""

from __future__ import annotations

import asyncio
import copy
import hmac
import math
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from services.llm_cancellation import (
    LlmCancellationHandle,
    LlmRequestCancelled,
)

PREPARE_TTL_SECONDS = 15 * 60
PREPARE_READY_TTL_SECONDS = 45
PREPARE_MAX_OPERATIONS = 256
CHAT_PROGRESS_MAX_CHARS = 512 * 1024
ROUTE_OPERATION_TTL_SECONDS = 45 * 60
ROUTE_OPERATION_MAX_OPERATIONS = 128
ROUTE_PROGRESS_MAX_CHARS = 512 * 1024
ROUTE_PROGRESS_MAX_PASSES = 32
ROUTE_PROGRESS_MAX_ATTEMPTS = 16
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
_ROUTE_PROGRESS_PHASES = _CHAT_PROGRESS_PHASES - frozenset({
    "complete", "completed", "failed",
})


class LlmOperationCapacityError(RuntimeError):
    """The bounded in-memory operation table is temporarily full."""


class LlmRouteOperationConflictError(ValueError):
    """A caller UUID was reused for a different exact route operation."""


class LlmRouteAdmissionError(RuntimeError):
    """The bounded route executor has no free admission slot."""


def _request_operation_evictable(operation: Any) -> bool:
    """Return true only after both public terminal state and worker exit."""
    if operation.status == "running":
        return False
    task = getattr(operation, "task", None)
    return task is None or task.done()


def _prune_request_operations(
    operations: dict[str, Any],
    *,
    now: float,
    ttl_seconds: float,
    max_operations: int,
) -> None:
    """Apply the shared terminal TTL and bounded-table contract in place."""
    for request_id, operation in list(operations.items()):
        if (
            _request_operation_evictable(operation)
            and now - operation.updated_at >= ttl_seconds
        ):
            operations.pop(request_id, None)
    overflow = len(operations) - max_operations
    if overflow <= 0:
        return
    terminal = sorted(
        (
            operation for operation in operations.values()
            if _request_operation_evictable(operation)
        ),
        key=lambda item: item.updated_at,
    )
    for operation in terminal[:overflow]:
        operations.pop(operation.request_id, None)


def _evict_oldest_terminal_for_admission(
    operations: dict[str, Any], max_operations: int,
) -> None:
    """Make one bounded slot without ever evicting an active worker."""
    if len(operations) < max_operations:
        return
    terminal = [
        operation for operation in operations.values()
        if _request_operation_evictable(operation)
    ]
    if terminal:
        oldest = min(terminal, key=lambda item: item.updated_at)
        operations.pop(oldest.request_id, None)


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
        _prune_request_operations(
            self._operations,
            now=now,
            ttl_seconds=self._ttl_seconds,
            max_operations=self._max_operations,
        )

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
            _evict_oldest_terminal_for_admission(
                self._operations, self._max_operations,
            )
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


@dataclass
class _RouteOperation:
    request_id: str
    owner_key: str
    project_instance_key: str
    operation_kind: str
    effective_input_digest: str
    created_at: float
    updated_at: float
    cancellation: LlmCancellationHandle
    status: str = "running"
    phase: str = "queued"
    stage: str = "queued"
    pass_index: int = 1
    pass_limit: int = 1
    attempt: int = 1
    attempt_limit: int = 1
    partial_text: str = ""
    token_count: int = 0
    elapsed_seconds: float = 0.0
    live_tps: float | None = None
    average_tps: float | None = None
    result: Any = None
    result_available: bool = False
    worker_task: asyncio.Task[Any] | None = None
    task: asyncio.Task[None] | None = None


class _ReleaseOnce:
    """Run one admission-release callback exactly once across task races."""

    def __init__(self, release: Callable[[], None]) -> None:
        self._release = release
        self._lock = threading.Lock()
        self._released = False

    def __call__(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        try:
            self._release()
        except Exception:  # noqa: BLE001, S110 - lifecycle state still wins
            pass


class LlmRouteOperationManager:
    """Bounded request-scoped execution for non-Chat LLM/VLM routes.

    The status projection is safe to return from an HTTP 202/poll endpoint: it
    contains only opaque identity plus bounded telemetry. Effective input,
    provider selection, media references, exception details, and the raw result
    are never part of that projection. A route that needs the result must read
    it through :meth:`result` after repeating the exact authorization scope.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = ROUTE_OPERATION_TTL_SECONDS,
        max_operations: int = ROUTE_OPERATION_MAX_OPERATIONS,
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
        self._operations: dict[str, _RouteOperation] = {}

    @property
    def retention_seconds(self) -> float:
        """The bounded terminal-result recovery window for this manager."""
        return self._ttl_seconds

    @staticmethod
    def _validate_submit_identity(
        request_id: str,
        owner_key: str,
        project_instance_key: str,
        operation_kind: str,
        effective_input_digest: str,
    ) -> str:
        if not all(
            isinstance(value, str) and value
            for value in (
                request_id,
                owner_key,
                project_instance_key,
                operation_kind,
                effective_input_digest,
            )
        ):
            raise ValueError("opaque route operation identity is required")
        try:
            return uuid.UUID(request_id).hex
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("request_id must be a UUID") from error

    @staticmethod
    def _lookup_request_id(request_id: str) -> str | None:
        try:
            return uuid.UUID(request_id).hex
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _same_scope(
        operation: _RouteOperation,
        *,
        owner_key: str,
        project_instance_key: str,
        operation_kind: str,
    ) -> bool:
        return (
            isinstance(owner_key, str)
            and isinstance(project_instance_key, str)
            and isinstance(operation_kind, str)
            and hmac.compare_digest(operation.owner_key, owner_key)
            and hmac.compare_digest(
                operation.project_instance_key, project_instance_key,
            )
            and hmac.compare_digest(operation.operation_kind, operation_kind)
        )

    @staticmethod
    def _clear_progress(operation: _RouteOperation) -> None:
        operation.partial_text = ""
        operation.token_count = 0
        operation.elapsed_seconds = 0.0
        operation.live_tps = None
        operation.average_tps = None

    def _prune_locked(self, now: float) -> None:
        _prune_request_operations(
            self._operations,
            now=now,
            ttl_seconds=self._ttl_seconds,
            max_operations=self._max_operations,
        )

    @staticmethod
    def _public(operation: _RouteOperation) -> dict[str, Any]:
        response: dict[str, Any] = {
            "request_id": operation.request_id,
            "operation_kind": operation.operation_kind,
            "status": operation.status,
            "phase": operation.phase,
            "stage": operation.stage,
            "pass": operation.pass_index,
            "pass_limit": operation.pass_limit,
            "attempt": operation.attempt,
            "attempt_limit": operation.attempt_limit,
            "partial_text": operation.partial_text,
            "generated_tokens_approx": operation.token_count,
            "elapsed_seconds": operation.elapsed_seconds,
            "live_tps": operation.live_tps,
            "average_tps": operation.average_tps,
            "result_available": operation.result_available,
            "retryable": operation.status == "failed",
        }
        if operation.status == "failed":
            response["error"] = {
                "code": "llm_operation_failed",
                "message": "LLM operation failed",
                "retryable": True,
            }
        return response

    def _update_progress(
        self,
        operation: _RouteOperation,
        event: dict[str, Any],
    ) -> None:
        if not isinstance(event, dict):
            return
        with self._lock:
            if (
                self._operations.get(operation.request_id) is not operation
                or operation.status != "running"
            ):
                return

            pass_limit = event.get("pass_limit")
            if (
                isinstance(pass_limit, int)
                and not isinstance(pass_limit, bool)
                and operation.pass_limit
                <= pass_limit
                <= ROUTE_PROGRESS_MAX_PASSES
            ):
                operation.pass_limit = pass_limit
            pass_index = event.get("pass")
            if (
                isinstance(pass_index, int)
                and not isinstance(pass_index, bool)
                and operation.pass_index <= pass_index <= operation.pass_limit
            ):
                if pass_index > operation.pass_index:
                    self._clear_progress(operation)
                operation.pass_index = pass_index

            attempt_limit = event.get("attempt_limit", event.get("attempt_cap"))
            if (
                isinstance(attempt_limit, int)
                and not isinstance(attempt_limit, bool)
                and operation.attempt_limit
                <= attempt_limit
                <= ROUTE_PROGRESS_MAX_ATTEMPTS
            ):
                operation.attempt_limit = attempt_limit
            attempt = event.get("attempt")
            if (
                isinstance(attempt, int)
                and not isinstance(attempt, bool)
                and operation.attempt <= attempt <= operation.attempt_limit
            ):
                if attempt > operation.attempt:
                    self._clear_progress(operation)
                operation.attempt = attempt

            phase = event.get("phase")
            if isinstance(phase, str) and phase in _ROUTE_PROGRESS_PHASES:
                operation.phase = phase
            stage = event.get("stage")
            if (
                isinstance(stage, str)
                and 1 <= len(stage) <= 64
                and all(character.isalnum() or character in "_-" for character in stage)
            ):
                operation.stage = stage

            text = event.get("text")
            if isinstance(text, str):
                operation.partial_text = text[-ROUTE_PROGRESS_MAX_CHARS:]

            token_count = event.get("generated_tokens_approx")
            if (
                isinstance(token_count, int)
                and not isinstance(token_count, bool)
                and token_count >= 0
            ):
                operation.token_count = min(token_count, 10_000_000)
            elapsed_seconds = event.get("elapsed_seconds")
            try:
                finite_elapsed = float(elapsed_seconds)
            except (OverflowError, TypeError, ValueError):
                finite_elapsed = -1.0
            if (
                isinstance(elapsed_seconds, (int, float))
                and not isinstance(elapsed_seconds, bool)
                and math.isfinite(finite_elapsed)
                and finite_elapsed >= 0
            ):
                operation.elapsed_seconds = min(
                    finite_elapsed, 31_536_000.0,
                )
            for field_name, event_name in (
                ("live_tps", "live_tps"),
                ("average_tps", "average_tps"),
            ):
                value = event.get(event_name)
                try:
                    finite_value = float(value)
                except (OverflowError, TypeError, ValueError):
                    finite_value = -1.0
                if value is None and event.get("done") is True:
                    setattr(operation, field_name, None)
                elif (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(finite_value)
                    and finite_value >= 0
                ):
                    setattr(operation, field_name, finite_value)
            operation.updated_at = self._clock()

    def submit(
        self,
        *,
        request_id: str,
        owner_key: str,
        project_instance_key: str,
        operation_kind: str,
        effective_input_digest: str,
        execute: Callable[
            [Callable[[dict[str, Any]], None], LlmCancellationHandle],
            Awaitable[Any],
        ],
        admit: Callable[[], bool] = lambda: True,
        release: Callable[[], None] = lambda: None,
    ) -> dict[str, Any] | None:
        """Start once or coalesce the exact authorized route operation."""
        request_id = self._validate_submit_identity(
            request_id,
            owner_key,
            project_instance_key,
            operation_kind,
            effective_input_digest,
        )
        if not callable(execute) or not callable(admit) or not callable(release):
            raise TypeError("execute, admit, and release must be callable")
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            existing = self._operations.get(request_id)
            if existing is not None:
                if not (
                    hmac.compare_digest(existing.owner_key, owner_key)
                    and hmac.compare_digest(
                        existing.project_instance_key, project_instance_key,
                    )
                ):
                    return None
                if not (
                    hmac.compare_digest(existing.operation_kind, operation_kind)
                    and hmac.compare_digest(
                        existing.effective_input_digest, effective_input_digest,
                    )
                ):
                    raise LlmRouteOperationConflictError
                return self._public(existing)

            _evict_oldest_terminal_for_admission(
                self._operations, self._max_operations,
            )
            if len(self._operations) >= self._max_operations:
                raise LlmOperationCapacityError
            if not admit():
                raise LlmRouteAdmissionError

            operation = _RouteOperation(
                request_id=request_id,
                owner_key=owner_key,
                project_instance_key=project_instance_key,
                operation_kind=operation_kind,
                effective_input_digest=effective_input_digest,
                created_at=now,
                updated_at=now,
                cancellation=LlmCancellationHandle(),
            )
            self._operations[request_id] = operation
            release_once = _ReleaseOnce(release)
            try:
                operation.task = asyncio.create_task(
                    self._run(operation, execute, release_once),
                )
            except Exception:
                self._operations.pop(request_id, None)
                release_once()
                raise

            def finish_outer(done: asyncio.Task[None]) -> None:
                # A task cancelled before its coroutine starts never reaches
                # _run's finally block. This callback closes that exact-once
                # lifecycle gap without releasing early after worker creation.
                if done.cancelled():
                    with self._lock:
                        if (
                            self._operations.get(operation.request_id)
                            is operation
                            and operation.status == "running"
                        ):
                            operation.status = "failed"
                            operation.phase = "failed"
                            operation.stage = "failed"
                            self._clear_progress(operation)
                            operation.updated_at = self._clock()
                release_once()
                try:
                    done.exception()
                except asyncio.CancelledError:
                    pass

            operation.task.add_done_callback(finish_outer)
            return self._public(operation)

    async def _run(
        self,
        operation: _RouteOperation,
        execute: Callable[
            [Callable[[dict[str, Any]], None], LlmCancellationHandle],
            Awaitable[Any],
        ],
        release_once: _ReleaseOnce,
    ) -> None:
        worker_task: asyncio.Task[Any] | None = None
        try:
            async def execute_if_current() -> Any:
                # This second checkpoint runs inside the newly scheduled inner
                # task. Cancellation after manager admission but before the
                # coroutine receives CPU therefore cannot enter execute().
                with self._lock:
                    if (
                        self._operations.get(operation.request_id) is not operation
                        or operation.status != "running"
                    ):
                        raise LlmRequestCancelled("LLM request cancelled")
                    operation.cancellation.checkpoint()
                return await execute(
                    lambda event: self._update_progress(operation, event),
                    operation.cancellation,
                )

            with self._lock:
                if (
                    self._operations.get(operation.request_id) is not operation
                    or operation.status != "running"
                    or operation.cancellation.cancelled
                ):
                    raise LlmRequestCancelled("LLM request cancelled")
                operation.cancellation.checkpoint()
                operation.phase = "inference"
                operation.stage = "inference"
                operation.updated_at = self._clock()
                worker_task = asyncio.create_task(execute_if_current())
                operation.worker_task = worker_task

            result = await asyncio.shield(worker_task)
            operation.cancellation.checkpoint()
            result_snapshot = copy.deepcopy(result)
        except LlmRequestCancelled:
            with self._lock:
                if worker_task is not None and worker_task.done():
                    operation.worker_task = None
                if (
                    self._operations.get(operation.request_id) is operation
                    and operation.status == "running"
                ):
                    operation.status = "cancelled"
                    operation.phase = "cancelled"
                    operation.stage = "cancelled"
                    self._clear_progress(operation)
                    operation.updated_at = self._clock()
            return
        except asyncio.CancelledError:
            # This is manager-owned outer-task cancellation, not a browser
            # waiter disconnect (wait() shields this task). Close the exact
            # transport outside the manager lock, then remain alive until the
            # inner worker exits so admission and eviction stay truthful.
            operation.cancellation.cancel()
            with self._lock:
                if (
                    self._operations.get(operation.request_id) is operation
                    and operation.status == "running"
                ):
                    operation.status = "failed"
                    operation.phase = "failed"
                    operation.stage = "failed"
                    self._clear_progress(operation)
                    operation.updated_at = self._clock()
                worker_task = operation.worker_task
            if worker_task is not None:
                while not worker_task.done():
                    try:
                        await asyncio.shield(worker_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:  # noqa: BLE001 - cleanup only
                        break
                if worker_task.done():
                    try:
                        worker_task.exception()
                    except asyncio.CancelledError:
                        pass
                    with self._lock:
                        if operation.worker_task is worker_task:
                            operation.worker_task = None
            raise
        except Exception:  # noqa: BLE001 - public state must stay redacted
            with self._lock:
                if worker_task is not None and worker_task.done():
                    operation.worker_task = None
                if (
                    self._operations.get(operation.request_id) is operation
                    and operation.status == "running"
                ):
                    operation.status = "failed"
                    operation.phase = "failed"
                    operation.stage = "failed"
                    self._clear_progress(operation)
                    operation.updated_at = self._clock()
            return
        finally:
            release_once()

        with self._lock:
            if (
                self._operations.get(operation.request_id) is operation
                and operation.worker_task is worker_task
                and worker_task is not None
                and worker_task.done()
            ):
                operation.worker_task = None
            if (
                self._operations.get(operation.request_id) is operation
                and operation.status == "running"
                and not operation.cancellation.cancelled
            ):
                operation.result = result_snapshot
                operation.result_available = True
                operation.status = "completed"
                operation.phase = "completed"
                operation.stage = "completed"
                operation.live_tps = None
                operation.updated_at = self._clock()

    def status(
        self,
        request_id: str,
        *,
        owner_key: str,
        project_instance_key: str,
        operation_kind: str,
    ) -> dict[str, Any] | None:
        request_id = self._lookup_request_id(request_id)
        if request_id is None:
            return None
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            operation = self._operations.get(request_id)
            if operation is None or not self._same_scope(
                operation,
                owner_key=owner_key,
                project_instance_key=project_instance_key,
                operation_kind=operation_kind,
            ):
                return None
            return self._public(operation)

    def result(
        self,
        request_id: str,
        *,
        owner_key: str,
        project_instance_key: str,
        operation_kind: str,
    ) -> Any:
        """Return the private terminal result only to its exact route scope."""
        request_id = self._lookup_request_id(request_id)
        if request_id is None:
            return None
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            operation = self._operations.get(request_id)
            if (
                operation is None
                or not self._same_scope(
                    operation,
                    owner_key=owner_key,
                    project_instance_key=project_instance_key,
                    operation_kind=operation_kind,
                )
                or not operation.result_available
            ):
                return None
            result = operation.result
        try:
            return copy.deepcopy(result)
        except Exception:  # noqa: BLE001 - retained state stays private
            return None

    async def wait(
        self,
        request_id: str,
        *,
        owner_key: str,
        project_instance_key: str,
        operation_kind: str,
    ) -> dict[str, Any] | None:
        """Await terminal state without letting waiter cancellation stop work."""
        request_id = self._lookup_request_id(request_id)
        if request_id is None:
            return None
        with self._lock:
            self._prune_locked(self._clock())
            operation = self._operations.get(request_id)
            if operation is None or not self._same_scope(
                operation,
                owner_key=owner_key,
                project_instance_key=project_instance_key,
                operation_kind=operation_kind,
            ):
                return None
            task = operation.task
        if task is not None:
            await asyncio.shield(task)
        return self.status(
            request_id,
            owner_key=owner_key,
            project_instance_key=project_instance_key,
            operation_kind=operation_kind,
        )

    def cancel(
        self,
        request_id: str,
        *,
        owner_key: str,
        project_instance_key: str,
        operation_kind: str,
    ) -> dict[str, Any] | None:
        """Cancel one exact operation and close its response outside the lock."""
        request_id = self._lookup_request_id(request_id)
        if request_id is None:
            return None
        cancellation: LlmCancellationHandle | None = None
        with self._lock:
            self._prune_locked(self._clock())
            operation = self._operations.get(request_id)
            if operation is None or not self._same_scope(
                operation,
                owner_key=owner_key,
                project_instance_key=project_instance_key,
                operation_kind=operation_kind,
            ):
                return None
            if operation.status == "running":
                operation.status = "cancelled"
                operation.phase = "cancelled"
                operation.stage = "cancelled"
                operation.result = None
                operation.result_available = False
                self._clear_progress(operation)
                operation.updated_at = self._clock()
                cancellation = operation.cancellation
            public = self._public(operation)
        if cancellation is not None:
            cancellation.cancel()
        return public


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
llm_route_operation_manager = LlmRouteOperationManager()
