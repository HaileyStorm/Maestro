"""Count upload readers through HTTP response and detached worker drain."""
from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import threading
from contextlib import contextmanager


lock = threading.RLock()
_readers: dict[str, int] = {}
_current = contextvars.ContextVar("ordinary_upload_usage", default=None)


class UploadUsage:
    def __init__(self):
        self.references = 1
        self.paths = set()

    def retain(self):
        with lock:
            if self.references <= 0:
                raise RuntimeError("Upload reader has already drained")
            self.references += 1

    def pin(self, path):
        with lock:
            if self.references <= 0:
                raise RuntimeError("Upload reader has already drained")
            key = os.path.normcase(os.path.realpath(path))
            if key not in self.paths:
                self.paths.add(key)
                _readers[key] = _readers.get(key, 0) + 1

    def release(self):
        with lock:
            if self.references <= 0:
                raise RuntimeError("Upload reader released twice")
            self.references -= 1
            if self.references == 0:
                for path in self.paths:
                    count = _readers[path] - 1
                    if count:
                        _readers[path] = count
                    else:
                        del _readers[path]


def pin(path, request):
    usage = _current.get()
    if usage is not None:
        usage.pin(path)
    elif getattr(request, "scope", {}).get("type") == "http":
        # HTTP readers must pass the installed middleware, including workers
        # which inherit its context. A missing context cannot admit deletion.
        raise RuntimeError("Upload reader context is unavailable")


def in_use(path):
    with lock:
        return bool(_readers.get(os.path.normcase(os.path.realpath(path))))


@contextmanager
def reader(paths):
    usage = UploadUsage()
    token = _current.set(usage)
    try:
        with lock:
            for path in paths:
                usage.pin(path)
        yield
    finally:
        _current.reset(token)
        usage.release()


class UploadUsageMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        usage = UploadUsage()
        token = _current.set(usage)
        try:
            return await self.app(scope, receive, send)
        finally:
            _current.reset(token)
            usage.release()


def create_task(coroutine):
    usage = _current.get()
    if usage is None:
        return asyncio.ensure_future(coroutine)
    usage.retain()
    try:
        task = asyncio.ensure_future(coroutine)
    except BaseException:
        usage.release()
        raise
    task.add_done_callback(lambda _task: usage.release())
    return task


async def to_thread(function, /, *args, **kwargs):
    usage = _current.get()
    if usage is None:
        return await asyncio.to_thread(function, *args, **kwargs)
    usage.retain()
    context = contextvars.copy_context()
    try:
        future = asyncio.get_running_loop().run_in_executor(
            None, context.run, functools.partial(function, *args, **kwargs),
        )
    except BaseException:
        usage.release()
        raise

    def drained(done):
        usage.release()
        if not done.cancelled():
            done.exception()

    future.add_done_callback(drained)
    # A cancelled waiter does not cancel queued/running executor work. The
    # executor completion callback owns its reference until actual drain.
    return await asyncio.shield(future)
