"""Request-scoped cancellation for in-flight LLM transports."""

from __future__ import annotations

import threading
from typing import Optional


class LlmRequestCancelled(Exception):
    """The owner cancelled one exact LLM request."""


class LlmCancellationHandle:
    """Close only the response currently owned by one request.

    Response closure happens outside the state lock because requests-compatible
    response implementations may block briefly while tearing down a socket.
    Identity-checked unregistering prevents a stale attempt from detaching a
    successor response registered by the same request.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._response: Optional[object] = None

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        response = None
        with self._lock:
            self._cancelled = True
            response = self._response
            self._response = None
        self._close_response(response)

    def checkpoint(self) -> None:
        if self.cancelled:
            raise LlmRequestCancelled("LLM request cancelled")

    def register_response(self, response: object) -> None:
        if response is None:
            return
        close_immediately = False
        with self._lock:
            if self._cancelled:
                close_immediately = True
            else:
                self._response = response
        if close_immediately:
            self._close_response(response)

    def unregister_response(self, response: object) -> bool:
        with self._lock:
            if self._response is response:
                self._response = None
                return True
            return False

    @staticmethod
    def _close_response(response: Optional[object]) -> None:
        close = getattr(response, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            # Cancellation state remains authoritative even if a third-party
            # response object reports a teardown error.
            pass
