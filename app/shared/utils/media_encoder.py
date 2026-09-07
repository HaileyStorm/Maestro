"""Bounded lifecycle control for owned media encoder subprocesses."""

from __future__ import annotations

import math
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from os import PathLike
from typing import BinaryIO


_POLL_INTERVAL_SECONDS = 0.05
_TERMINATE_GRACE_SECONDS = 0.5
_MONITOR_THREAD_NAME = "maestro-media-encoder-monitor"


def _validated_timeout(timeout: object) -> float:
    if isinstance(timeout, bool):
        raise ValueError("timeout must be a finite positive number")
    try:
        value = float(timeout)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timeout must be a finite positive number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a finite positive number")
    return value


def _platform_popen_options() -> dict:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP,
    }


class EncoderProcess:
    """Context-managed encoder child with bounded cancellation and cleanup.

    ``abort_check`` must be a quick, non-blocking callback. Its exceptions are
    re-raised after the owned child has been stopped and reaped. Streams passed
    through ``stdout`` remain owned by the caller.
    """

    def __init__(
        self,
        command: Sequence[str | bytes | PathLike[str] | PathLike[bytes]],
        *,
        timeout: float,
        abort_check: Callable[[], object] | None = None,
        stdout: BinaryIO | int | None = subprocess.DEVNULL,
    ) -> None:
        self._command = command
        self._timeout = _validated_timeout(timeout)
        if abort_check is not None and not callable(abort_check):
            raise TypeError("abort_check must be callable")
        self._abort_check = abort_check
        self._stdout = stdout

        self._process: subprocess.Popen[bytes] | None = None
        self._monitor_thread: threading.Thread | None = None
        self._stop_monitor = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._failure_lock = threading.Lock()
        self._failure: BaseException | None = None
        self._returncode: int | None = None
        self._entered = False
        self._finished = False

    def __enter__(self) -> EncoderProcess:
        if self._entered:
            raise RuntimeError("Encoder process context cannot be entered twice")
        self._entered = True

        self._check_pre_cancel()
        deadline = time.monotonic() + self._timeout
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=self._stdout,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                **_platform_popen_options(),
            )
        except (OSError, TypeError, ValueError):
            raise RuntimeError("Encoder process could not be started") from None

        try:
            self._monitor_thread = threading.Thread(
                target=self._monitor,
                args=(deadline,),
                name=_MONITOR_THREAD_NAME,
            )
            self._monitor_thread.start()
        except BaseException:
            self._stop_owned_child()
            self._finished = True
            raise RuntimeError("Encoder monitor could not be started") from None
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            if not self._finished:
                self.finish()
        else:
            self._cleanup_after_caller_failure()
        return False

    def _check_pre_cancel(self) -> None:
        if self._abort_check is not None and self._abort_check():
            raise InterruptedError("Encoder operation cancelled")

    def _set_failure(self, error: BaseException) -> None:
        with self._failure_lock:
            if self._failure is None:
                self._failure = error

    def _get_failure(self) -> BaseException | None:
        with self._failure_lock:
            return self._failure

    def _monitor(self, deadline: float) -> None:
        process = self._require_process()
        while process.poll() is None and not self._stop_monitor.is_set():
            if self._abort_check is not None:
                try:
                    cancelled = self._abort_check()
                except BaseException as error:
                    self._set_failure(error)
                    self._stop_owned_child()
                    return
                if cancelled:
                    self._set_failure(InterruptedError("Encoder operation cancelled"))
                    self._stop_owned_child()
                    return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._set_failure(TimeoutError("Encoder operation timed out"))
                self._stop_owned_child()
                return
            self._stop_monitor.wait(min(_POLL_INTERVAL_SECONDS, remaining))

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            raise RuntimeError("Encoder process context is not active")
        return self._process

    @staticmethod
    def _close_stdin(process: subprocess.Popen[bytes]) -> None:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    def _stop_owned_child(self) -> None:
        process = self._require_process()
        with self._cleanup_lock:
            try:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except OSError:
                            pass
                        process.wait()
                else:
                    process.wait()
                self._returncode = process.returncode
            finally:
                self._close_stdin(process)

    def _join_monitor(self) -> None:
        self._stop_monitor.set()
        monitor = self._monitor_thread
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join()

    def _cleanup_after_caller_failure(self) -> None:
        if self._finished or self._process is None:
            return
        try:
            self._stop_owned_child()
        finally:
            self._finished = True
            self._join_monitor()

    def _raise_monitor_failure(self) -> None:
        failure = self._get_failure()
        if failure is not None:
            raise failure

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if self._finished:
            raise RuntimeError("Encoder process is already finished")
        process = self._require_process()
        if process.stdin is None or process.stdin.closed:
            raise RuntimeError("Encoder process input is closed")

        remaining = memoryview(data)
        try:
            while remaining:
                written = process.stdin.write(remaining)
                if not written:
                    raise BrokenPipeError
                remaining = remaining[written:]
        except (BrokenPipeError, OSError, ValueError):
            try:
                process.wait()
            finally:
                self._close_stdin(process)
                self._returncode = process.returncode
                self._finished = True
                self._join_monitor()
            self._raise_monitor_failure()
            raise RuntimeError("Encoder process closed its input unexpectedly") from None

    def finish(self) -> int:
        if self._finished:
            self._raise_monitor_failure()
            if self._returncode is None:
                raise RuntimeError("Encoder process did not return a status")
            return self._returncode

        process = self._require_process()
        self._close_stdin(process)
        try:
            returncode = process.wait()
        except BaseException:
            self._stop_owned_child()
            raise
        finally:
            self._returncode = process.returncode
            self._finished = True
            self._join_monitor()
        self._raise_monitor_failure()
        if returncode is None:
            raise RuntimeError("Encoder process did not return a status")
        return returncode

    close = finish


def run_encoder(
    command: Sequence[str | bytes | PathLike[str] | PathLike[bytes]],
    *,
    timeout: float,
    abort_check: Callable[[], object] | None = None,
    stdout: BinaryIO | int | None = subprocess.DEVNULL,
) -> int:
    """Run an encoder command without stdin and return its exit status."""
    with EncoderProcess(
        command,
        timeout=timeout,
        abort_check=abort_check,
        stdout=stdout,
    ) as encoder:
        return encoder.finish()


__all__ = ["EncoderProcess", "run_encoder"]
