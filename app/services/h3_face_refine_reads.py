"""Installable exact Gallery reads; the launch adapter supplies access authority.

Not mounted by default. Installing routes does not advertise model capability.
The authorizer must check both feature flags, project.generate, model visibility,
legal/terms admission, finality, exact revision and remote privacy on every call.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import threading

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from services import h3_face_refine_preview as preview
from services import upload_usage

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_BASE = "/api/v1/tools/h3-face-refine"


@dataclass(frozen=True)
class FacePreviewAccess:
    workspace: str
    name: str
    revision: str
    path: str
    private: bool
    explicit: bool


def _query(request, frame):
    values = request.query_params
    keys = {"workspace", "name", "revision"}
    allowed = keys | ({"frame_index", "preview_attempt"} if frame else set())
    if set(values) - allowed or any(len(values.getlist(k)) != 1 for k in values):
        raise HTTPException(400, "Select one exact Gallery source")
    binding = {}
    for key in keys:
        value = values.get(key, "")
        if (not 1 <= len(value) <= 256 or value in (".", "..")
                or any(ord(c) < 32 or c in "/\\" for c in value)):
            raise HTTPException(400, "Select one exact Gallery source")
        binding[key] = value
    index = None
    if frame:
        text = values.get("frame_index", "")
        if not re.fullmatch(r"0|[1-9][0-9]{0,2}", text) or int(text) > 344:
            raise HTTPException(400, "Choose an integer frame within the source")
        index = int(text)
        attempt = values.get("preview_attempt")
        if attempt is not None and not re.fullmatch(r"0|[1-9][0-9]{0,8}", attempt):
            raise HTTPException(400, "Preview retry counter is invalid")
    return binding, index


def _access(value, binding):
    if (type(value) is not FacePreviewAccess
            or any(getattr(value, key) != item for key, item in binding.items())
            or type(value.path) is not str or not value.path
            or type(value.private) is not bool or type(value.explicit) is not bool):
        raise HTTPException(409, "The source changed. Refresh Gallery.")
    return value


async def _drain(task, stopped, settled):
    """Repeated waiter cancellation cannot abandon the owned CPU decoder."""
    stopped.set()
    while not settled.is_set():
        try:
            if task.done():
                await asyncio.sleep(0.05)
            else:
                await asyncio.shield(task)
        except asyncio.CancelledError:
            stopped.set()
        except Exception:
            pass
    if task.done() and not task.cancelled():
        task.exception()


def register_face_preview_reads(api, *, authorize_source):
    """Mount once; authorize_source(request, workspace, name, revision) is sync.

    It returns FacePreviewAccess after current guarded authorization, or raises
    HTTPException. It runs before decoding and again before returning pixels.
    No untrusted request may choose a path or supply its own access record.
    """
    if any(getattr(route, "path", None) in (_BASE + "/source", _BASE + "/frame")
           for route in api.routes):
        raise ValueError("Face preview reads are already registered")
    slots = threading.BoundedSemaphore(2)

    async def read(request: Request, frame):
        task, observer = None, None
        stopped = threading.Event()
        settled = threading.Event()
        try:
            binding, index = _query(request, frame)
            access = _access(authorize_source(request, **binding), binding)
            if await request.is_disconnected():
                raise HTTPException(499, "Source preview cancelled")
            if not slots.acquire(blocking=False):
                raise HTTPException(429, "Preview readers are busy. Try again shortly.", headers={"Retry-After": "1"})
            lifecycle_lock = threading.Lock()
            begun, retired = False, False
            def retire_pending(done=None):
                nonlocal retired
                with lifecycle_lock:
                    if not begun and not retired:
                        retired = True
                        slots.release()
                        settled.set()
                if done is not None and not done.cancelled():
                    done.exception()
            def work():
                nonlocal begun, retired
                with lifecycle_lock:
                    # A cancelled/failed submission can still have an executor
                    # invocation queued. It must not start reading after retire.
                    if retired:
                        raise preview.FacePreviewCancelled("Source preview cancelled")
                    begun = True
                try:
                    if frame:
                        return preview.read_face_frame(access.path, index, cancel_check=stopped.is_set)
                    return preview.read_face_source(access.path, cancel_check=stopped.is_set), None
                finally:
                    # The worker owns capacity until its decoder has drained.
                    with lifecycle_lock:
                        retired = True
                        slots.release()
                        settled.set()
            coroutine = upload_usage.to_thread(work)
            try:
                task = upload_usage.create_task(coroutine)
            except BaseException:
                coroutine.close()
                retire_pending()
                raise
            task.add_done_callback(retire_pending)
            async def observe_disconnect():
                try:
                    while not task.done():
                        if await request.is_disconnected():
                            stopped.set()
                            return
                        await asyncio.sleep(0.05)
                except Exception:
                    # An unreadable connection must not leave a decoder running.
                    stopped.set()
            observer = asyncio.create_task(observe_disconnect())
            facts, png = await asyncio.shield(task)
            if stopped.is_set() or await request.is_disconnected():
                raise HTTPException(499, "Source preview cancelled")
            current = _access(authorize_source(request, **binding), binding)
            if current != access:
                raise HTTPException(409, "The source changed. Refresh Gallery.")
            if frame:
                return Response(png, media_type="image/png", headers=_HEADERS)
            return JSONResponse({**binding, **facts.public_facts()}, headers=_HEADERS)
        except asyncio.CancelledError:
            if task is not None:
                await _drain(task, stopped, settled)
            raise
        except preview.FacePreviewCancelled:
            raise HTTPException(499, "Source preview cancelled", headers=_HEADERS) from None
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **_HEADERS}
            raise
        except Exception:
            raise HTTPException(409, "The source is unavailable. Refresh Gallery and review the clip.", headers=_HEADERS) from None
        finally:
            if observer is not None:
                observer.cancel()
                try:
                    await observer
                except asyncio.CancelledError:
                    pass

    @api.get(_BASE + "/source")
    async def face_preview_source(request: Request):
        return await read(request, False)

    @api.get(_BASE + "/frame")
    async def face_preview_frame(request: Request):
        return await read(request, True)
