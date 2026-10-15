"""Private, single-launch Director transport; this module never loads models.

Linux is required for unreaped-leader process-group custody. Callbacks must fence
their own writes: an aborted daemon callback may outlive transport cleanup, but
its result can never acknowledge a request. The trusted worker must not detach
descendants from its owned session/group. No payload or worker output is logged.
"""

from __future__ import annotations

import base64
import builtins
import json
import math
import os
import select
import signal
import socket
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path

MAX_FRAME_BYTES = 128 * 1024 * 1024
MAX_VALUE_NODES = 100_000
AUTHORITY_INTERVAL = 1.0
AUTHORITY_DEADLINE = 4.0
DRAIN_SECONDS = 5.0
WORKER_FD_ENV = "MAESTRO_NATIVE_WORKER_FD"
FAILURE_STAGES = frozenset({
    "bootstrap", "validate_packet", "parent_watch", "consumer_binding", "private_runtime",
    "wgp_import", "runtime_binding", "generation", "model_release", "finish", "cleanup",
})
_TRUSTED_FAILURE_FILES = frozenset({"director_native_process.py", "director_native_generation.py"})
_CALLBACK_SOURCE_PATHS = frozenset(
    Path(__file__).absolute().parent.parent / relative for relative in (
        "services/director_native_process.py", "services/director_native_generation.py",
        "services/director_video_lora_execution.py", "launch.py", "shared/utils/thread_utils.py",
    )
)
_CALLBACK_ROLES = frozenset({"authority", "on_started", "on_request", "on_event"})
_CALLBACK_OPERATIONS = {
    "authority": frozenset({"none"}), "on_started": frozenset({"none"}),
    "on_request": frozenset({"validate_current", "retain_preparation", "retain_repeat",
        "after_repeat", "after_segment", "profile_observation", "style_begin", "style_fail", "style_encoded"}),
    "on_event": frozenset({"status", "progress", "preview", "output", "refresh_models",
        "info", "error", "worker_exit", "exit"}),
}
_CALLBACK_EXCEPTION_TYPES = frozenset({
    ("services.director_video_lora_execution", "DirectorLoraExecutionError"),
    ("services.queue_recovery_runtime", "QueueRecoveryRuntimeError"),
    ("services.native_acceptance_reservation", "NativeAcceptanceReserved"),
})
_CALLBACK_NATIVE_CODES = frozenset({
    "cancelled", "director_callback_binding", "director_output_count", "director_repeat_binding",
    "director_segment_binding", "director_observation_binding", "unsupported_director_request",
    "director_event_binding",
})
_CAUSE_CODES = frozenset({
    "aborted", "ack_mismatch", "authority_revoked", "authority_timeout", "callback_failed",
    "cancelled", "drain_timeout", "duplicate_key", "frame_limit", "group_inspection_limit",
    "identity_mismatch", "invalid_array", "invalid_bootstrap", "invalid_dtype", "invalid_envelope",
    "invalid_image", "invalid_image_pixels", "invalid_mapping", "invalid_message", "invalid_send",
    "invalid_value", "invalid_worker_failure", "noncontiguous_array", "output_read_failed",
    "post_terminal_message", "transport_failed", "unexpected_eof", "unexpected_message",
    "unsupported_value", "value_limit", "worker_exit", "worker_exit_without_drain", "worker_failure",
})
_CLEANUP_CODES = frozenset({
    "callback_drain_unresolved", "channel_drain_unresolved", "cleanup_failed",
    "custody_unresolved", "group_inspection_limit", "output_drain_unresolved",
})


def _failure_record(stage, exc, trusted_source):
    source = None
    trusted = Path(trusted_source).absolute()
    if trusted.parent == Path(__file__).absolute().parent and trusted.name in _TRUSTED_FAILURE_FILES:
        frame = exc.__traceback__
        for _ in range(128):
            if frame is None:
                break
            code = frame.tb_frame.f_code
            if (Path(code.co_filename).absolute() == trusted and code.co_name.isascii()
                    and code.co_name.isidentifier() and len(code.co_name) <= 128):
                source = {"basename": trusted.name, "function": code.co_name, "line": frame.tb_lineno}
            frame = frame.tb_next
    cls = type(exc)
    exception_type = cls.__name__ if getattr(builtins, cls.__name__, None) is cls else "Exception"
    if cls is NativeProcessError:
        exception_type = "NativeProcessError"
    return {"stage": stage, "exception_type": exception_type, "source": source}


def _validate_failure(record, *, trusted_files=_TRUSTED_FAILURE_FILES, exception_types=frozenset()):
    if (type(record) is not dict or set(record) != {"stage", "exception_type", "source"}
            or type(record["stage"]) is not str or record["stage"] not in FAILURE_STAGES
            or type(record["exception_type"]) is not str):
        raise NativeProcessError("invalid_worker_failure")
    name = record["exception_type"]
    cls = getattr(builtins, name, None)
    if name not in exception_types and name != "NativeProcessError" and (not isinstance(cls, type) or not issubclass(cls, BaseException)):
        raise NativeProcessError("invalid_worker_failure")
    source = record["source"]
    if source is not None and (type(source) is not dict or set(source) != {"basename", "function", "line"}
            or type(source["basename"]) is not str or source["basename"] not in trusted_files
            or type(source["function"]) is not str or not source["function"].isascii()
            or not source["function"].isidentifier() or len(source["function"]) > 128
            or type(source["line"]) is not int or not 0 < source["line"] <= 1_000_000):
        raise NativeProcessError("invalid_worker_failure")
    return record


def _callback_failure_record(role, operation, exc):
    failure = _failure_record("generation", exc, __file__)
    cls = type(exc)
    if (cls.__module__, cls.__name__) in _CALLBACK_EXCEPTION_TYPES:
        failure["exception_type"] = cls.__name__
    frame = exc.__traceback__
    for _ in range(128):
        if frame is None:
            break
        code = frame.tb_frame.f_code
        source = Path(code.co_filename).absolute()
        if (source in _CALLBACK_SOURCE_PATHS and code.co_name.isascii()
                and code.co_name.isidentifier() and len(code.co_name) <= 128):
            failure["source"] = {"basename": source.name, "function": code.co_name, "line": frame.tb_lineno}
        frame = frame.tb_next
    native_code = next((code for code in _CALLBACK_NATIVE_CODES
                        if cls is NativeProcessError and type(exc.code) is str and exc.code == code), None)
    return _validate_callback_failure({
        "role": role, "operation": operation if operation in _CALLBACK_OPERATIONS[role] else "unknown",
        "native_code": native_code, "failure": failure,
    })


def _validate_callback_failure(record):
    if (type(record) is not dict or set(record) != {"role", "operation", "native_code", "failure"}
            or type(record["role"]) is not str or record["role"] not in _CALLBACK_ROLES
            or type(record["operation"]) is not str
            or record["operation"] not in _CALLBACK_OPERATIONS[record["role"]] | {"unknown"}):
        raise NativeProcessError("invalid_callback_failure")
    _validate_failure(record["failure"], trusted_files=frozenset(path.name for path in _CALLBACK_SOURCE_PATHS),
                      exception_types=frozenset(name for _, name in _CALLBACK_EXCEPTION_TYPES))
    if record["native_code"] is not None and (
            type(record["native_code"]) is not str or record["native_code"] not in _CALLBACK_NATIVE_CODES
            or record["failure"]["exception_type"] != "NativeProcessError"):
        raise NativeProcessError("invalid_callback_failure")
    return record


class NativeProcessError(RuntimeError):
    """Content-free transport failure; do not chain private worker exceptions."""

    def __init__(self, code, *, worker_failure=None, exit_code=None, cause_code=None, cleanup_code=None,
                 callback_failure=None):
        self.code = code
        self.worker_failure = worker_failure
        self.exit_code = exit_code
        self.cause_code = cause_code if type(cause_code) is str and cause_code in _CAUSE_CODES else None
        self.cleanup_code = cleanup_code if type(cleanup_code) is str and cleanup_code in _CLEANUP_CODES else None
        self.callback_failure = None if callback_failure is None else _validate_callback_failure(callback_failure)
        super().__init__(f"Director native process failed ({code})")


def _dtype_spec(dtype):
    metadata = dict(dtype.metadata) if dtype.metadata is not None else None
    if dtype.fields:
        fields = tuple((name, _dtype_spec(dtype.fields[name][0]), dtype.fields[name][1],
                        dtype.fields[name][2] if len(dtype.fields[name]) == 3 else None)
                       for name in dtype.names)
        return ("record", fields, dtype.itemsize, dtype.isalignedstruct, metadata)
    if dtype.subdtype:
        return ("subarray", _dtype_spec(dtype.subdtype[0]), dtype.subdtype[1], metadata)
    return ("plain", dtype.str, metadata)


def _restore_dtype(spec):
    import numpy as np
    if spec[0] == "record":
        fields, size, aligned, metadata = spec[1:]
        dtype = np.dtype({"names": [f[0] for f in fields],
                          "formats": [_restore_dtype(f[1]) for f in fields],
                          "offsets": [f[2] for f in fields],
                          "titles": [f[3] for f in fields], "itemsize": size}, align=aligned)
    elif spec[0] == "subarray":
        dtype, metadata = np.dtype((_restore_dtype(spec[1]), spec[2])), spec[3]
    elif spec[0] == "plain":
        dtype, metadata = np.dtype(spec[1]), spec[2]
    else:
        raise NativeProcessError("invalid_dtype")
    return np.dtype(dtype, metadata=metadata) if metadata is not None else dtype


def _codec(value, *, decode=False, depth=0, budget=None):
    budget = [MAX_VALUE_NODES] if budget is None else budget
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        raise NativeProcessError("value_limit")
    def visit(item):
        return _codec(item, decode=decode, depth=depth + 1, budget=budget)
    if value is None or type(value) in (bool, int, str):
        return value
    if decode:
        if type(value) is not list or len(value) != 2 or type(value[0]) is not str:
            raise NativeProcessError("invalid_value")
        kind, body = value
        if kind == "float" and type(body) is str:
            return float.fromhex(body)
        if kind == "bytes" and type(body) is str:
            return base64.b64decode(body, validate=True)
        if kind in ("list", "tuple") and type(body) is list:
            items = [visit(item) for item in body]
            return tuple(items) if kind == "tuple" else items
        if kind == "dict" and type(body) is list:
            result = {}
            for pair in body:
                if type(pair) is not list or len(pair) != 2:
                    raise NativeProcessError("invalid_mapping")
                key, item = visit(pair[0]), visit(pair[1])
                if key in result:
                    raise NativeProcessError("duplicate_key")
                result[key] = item
            return result
        if kind == "image":
            from PIL import Image, ImageMode, ImagePalette
            import numpy as np
            mode, size, pixels, palette, filename, info = visit(body)
            if (type(mode) is not str or type(size) is not tuple or len(size) != 2
                    or any(type(n) is not int or n < 1 for n in size)
                    or math.prod(size) > MAX_FRAME_BYTES or type(pixels) is not bytes
                    or type(info) is not dict or type(filename) is not tuple
                    or len(filename) not in (0, 1)):
                raise NativeProcessError("invalid_image")
            description = ImageMode.getmode(mode)
            expected = (((size[0] + 7) // 8) * size[1] if mode == "1" else
                        math.prod(size) * len(description.bands) * np.dtype(description.typestr).itemsize)
            if expected != len(pixels):
                raise NativeProcessError("invalid_image_pixels")
            image = Image.frombytes(mode, size, pixels)
            if image.tobytes() != pixels:
                raise NativeProcessError("invalid_image_pixels")
            if palette is not None:
                palette_mode, rawmode, data = palette
                image.palette = (ImagePalette.raw(rawmode, data) if rawmode else
                                 ImagePalette.ImagePalette(palette_mode, data))
            if filename:
                image.filename = filename[0]
            image.info = info
            return image
        if kind == "array":
            import numpy as np
            descriptor, shape, order, pixels = visit(body)
            dtype = _restore_dtype(descriptor)
            if (dtype.hasobject or type(shape) is not tuple or len(shape) > 32
                    or any(type(n) is not int or n < 0 for n in shape)
                    or math.prod(shape) > MAX_FRAME_BYTES
                    or order not in ("C", "F") or type(pixels) is not bytes
                    or math.prod(shape) * dtype.itemsize != len(pixels)):
                raise NativeProcessError("invalid_array")
            return np.frombuffer(pixels, dtype=dtype).reshape(shape, order=order).copy(order=order)
        raise NativeProcessError("unsupported_value")
    if type(value) is float:
        return ["float", value.hex()]
    if type(value) is bytes:
        if len(value) > MAX_FRAME_BYTES:
            raise NativeProcessError("value_limit")
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if type(value) in (list, tuple):
        return ["tuple" if type(value) is tuple else "list", [visit(item) for item in value]]
    if type(value) is dict:
        return ["dict", [[visit(key), visit(item)] for key, item in value.items()]]
    from PIL import Image, ImageMode
    import numpy as np
    if isinstance(value, Image.Image):
        description = ImageMode.getmode(value.mode)
        size = (((value.width + 7) // 8) * value.height if value.mode == "1" else
                value.width * value.height * len(description.bands) * np.dtype(description.typestr).itemsize)
        if size > MAX_FRAME_BYTES:
            raise NativeProcessError("value_limit")
        palette = value.palette
        palette = ((palette.mode, palette.rawmode, bytes(palette.palette))
                   if palette is not None else None)
        filename = (value.filename,) if hasattr(value, "filename") else ()
        return ["image", visit((value.mode, value.size, value.tobytes(),
                               palette, filename, value.info))]
    if type(value) is np.ndarray and not value.dtype.hasobject:
        if value.nbytes > MAX_FRAME_BYTES or value.size > MAX_FRAME_BYTES:
            raise NativeProcessError("value_limit")
        if not (value.flags.c_contiguous or value.flags.f_contiguous):
            raise NativeProcessError("noncontiguous_array")
        order = "C" if value.flags.c_contiguous else "F"
        return ["array", visit((_dtype_spec(value.dtype), value.shape,
                               order, value.tobytes(order=order)))]
    raise NativeProcessError("unsupported_value")


def _json_bytes(value):
    try:
        data = json.dumps(value, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("ascii")
        if not data or len(data) > MAX_FRAME_BYTES:
            raise NativeProcessError("frame_limit")
        return data
    except NativeProcessError:
        raise
    except Exception:
        raise NativeProcessError("invalid_value") from None


class Channel:
    """One serialized private stream; child RPC receives exactly one bound ACK."""

    def __init__(self, sock, nonce=None, stopped=None):
        self.socket = sock
        self.socket.settimeout(0.25)
        self.nonce = nonce
        self.sent = self.received = 0
        self.stopped = stopped or threading.Event()
        self._lock = threading.RLock()
        self._terminal = False

    def _check(self):
        if self.stopped.is_set():
            raise NativeProcessError("aborted")

    def _read(self, count, *, clean_eof=False):
        chunks = bytearray()
        while len(chunks) < count:
            self._check()
            try:
                part = self.socket.recv(min(count - len(chunks), 64 * 1024))
            except socket.timeout:
                continue
            if not part:
                if clean_eof and not chunks:
                    return None
                raise NativeProcessError("unexpected_eof")
            chunks.extend(part)
        return chunks

    def _receive(self, *, clean_eof=False):
        header = self._read(4, clean_eof=clean_eof)
        if header is None:
            return None
        size = struct.unpack("!I", header)[0]
        if not 0 < size <= MAX_FRAME_BYTES:
            raise NativeProcessError("frame_limit")
        try:
            message = json.loads(self._read(size))
            if (type(message) is not dict or set(message) != {"nonce", "seq", "kind", "name", "payload"}
                    or type(message["seq"]) is not int or message["seq"] != self.received + 1
                    or type(message["nonce"]) is not str or len(message["nonce"]) != 32
                    or type(message["kind"]) is not str or type(message["name"]) is not str
                    or len(message["name"]) > 128):
                raise NativeProcessError("invalid_envelope")
            if self.nonce is None:
                if message["kind"] != "bootstrap" or message["seq"] != 1:
                    raise NativeProcessError("invalid_bootstrap")
                self.nonce = message["nonce"]
            if message["nonce"] != self.nonce:
                raise NativeProcessError("identity_mismatch")
            message["payload"] = _codec(message["payload"], decode=True)
            self.received += 1
            return message
        except NativeProcessError:
            raise
        except Exception:
            raise NativeProcessError("invalid_message") from None

    def _send(self, kind, name, payload, *, encoded=False):
        self._check()
        if self._terminal or self.nonce is None or type(name) is not str or len(name) > 128:
            raise NativeProcessError("invalid_send")
        data = _json_bytes({"nonce": self.nonce, "seq": self.sent + 1,
                            "kind": kind, "name": name,
                            "payload": payload if encoded else _codec(payload)})
        frame = memoryview(struct.pack("!I", len(data)) + data)
        while frame:
            self._check()
            try:
                count = self.socket.send(frame[:64 * 1024])
            except socket.timeout:
                continue
            if not count:
                raise NativeProcessError("unexpected_eof")
            frame = frame[count:]
        self.sent += 1
        return self.sent

    def receive(self):
        with self._lock:
            message = self._receive()
            if message["kind"] != "bootstrap" or self.received != 1:
                raise NativeProcessError("invalid_bootstrap")
            return message["payload"]

    def send_event(self, name, payload):
        with self._lock:
            self._send("event", name, payload)

    def request(self, name, payload):
        with self._lock:
            sequence = self._send("request", name, payload)
            reply = self._receive()
            body = reply["payload"]
            if (reply["kind"] != "ack" or reply["name"] != name or type(body) is not dict
                    or set(body) != {"request_seq", "result"}
                    or type(body["request_seq"]) is not int or body["request_seq"] != sequence):
                raise NativeProcessError("ack_mismatch")
            return body["result"]

    def finish(self, result):
        with self._lock:
            self._send("finish", "", result)
            self._terminal = True
            self.socket.shutdown(socket.SHUT_WR)

    def fail(self, stage, exc, *, trusted_source):
        with self._lock:
            self._send("failure", "", _validate_failure(_failure_record(stage, exc, trusted_source)))
            self._terminal = True
            self.socket.shutdown(socket.SHUT_WR)

    def close(self):
        self.socket.close()

    def peer_closed(self):
        """Observe EOF without consuming a request/ACK; usable by a child watcher."""
        self._check()
        try:
            return self.socket.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
        except (BlockingIOError, socket.timeout):
            return False
        except OSError:
            return True


def worker_channel_from_fd():
    raw = os.environ.pop(WORKER_FD_ENV, "")
    if not raw.isascii() or not raw.isdecimal() or int(raw) < 3:
        raise NativeProcessError("missing_worker_channel")
    sock = socket.socket(fileno=int(raw))
    sock.set_inheritable(False)
    if sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM:
        sock.close()
        raise NativeProcessError("invalid_worker_channel")
    return Channel(sock)


def _group_live(pid):
    # The direct child remains unreaped, preventing reuse of this session ID.
    deadline = time.monotonic() + 1.0
    for count, entry in enumerate(Path("/proc").iterdir()):
        if count >= 65_536 or time.monotonic() >= deadline:
            raise NativeProcessError("group_inspection_limit")
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == pid and int(fields[3]) == pid and fields[0] != "Z":
            return True
    return False


def reap_owned_after_failure(error):
    """Reconcile retained custody without signalling a recycled process ID.

    The caller holds its generation slot while this returns False. The
    transport already sent stop to its own unreaped session; callbacks also
    have to return before the server can retire their publication lifetime.
    """
    if any(not event.is_set() for event in getattr(error, "callback_completion", ())):
        return False
    process = getattr(error, "process", None)
    if process is None or process.returncode is not None:
        return True
    try:
        if _group_live(process.pid):
            return False
        process.wait(timeout=1.0)
        return True
    except (OSError, NativeProcessError, subprocess.TimeoutExpired):
        return False


def run_native_process(packet, *, command, on_request, on_event, cancelled,
                       validate_authority, on_started=None, on_abort=None,
                       cwd=None, environment=None, invocation_fence=None):
    """Launch once, fence callbacks on abort, drain owned group, then reap.

    Authority must return exactly True. on_started receives the Popen object;
    The optional unset threading.Event invocation_fence is set synchronously
    before termination; callbacks must check it before side effects. on_abort
    is only an additional asynchronous state notice, never authority.
    on_started must never poll/wait/reap the Popen object. Callback exceptions
    are redacted. Cleanup failure retains the process handle on the raised error.
    """
    if (os.name != "posix" or not hasattr(os, "WNOWAIT") or not Path("/proc/self/stat").exists()
            or type(command) not in (list, tuple) or not command
            or any(type(arg) is not str or not arg or "\x00" in arg for arg in command)
            or not os.path.isabs(command[0])):
        raise NativeProcessError("unsupported_launch")
    if invocation_fence is not None and (type(invocation_fence) is not threading.Event
                                        or invocation_fence.is_set()):
        raise NativeProcessError("invalid_invocation_fence")
    bootstrap = _codec(packet)
    _json_bytes(bootstrap)
    stopped = invocation_fence if invocation_fence is not None else threading.Event()
    done, output_eof = threading.Event(), threading.Event()
    reason, checked, output, leader_exited = [], [time.monotonic()], bytearray(), [None]
    abort_notified = threading.Event()
    abort_lock = threading.Lock()
    lifecycle_lock = threading.Lock()
    callbacks_lock = threading.Lock()
    callbacks = []
    parent, child = socket.socketpair()
    channel = Channel(parent, uuid.uuid4().hex, stopped)
    process = None
    worker_failure, raised_error = None, None
    callback_failure = [None]

    def fence(code, *, force=False):
        with abort_lock:
            if reason or (done.is_set() and not force):
                return
            reason.append(code)
            stopped.set()
        if on_abort is not None:
            threading.Thread(target=notify_abort, args=(code,), daemon=True).start()
        else:
            abort_notified.set()

    def notify_abort(code):
        try:
            on_abort(code)
        except BaseException:
            pass
        finally:
            abort_notified.set()

    def authority():
        try:
            if cancelled():
                fence("cancelled")
            elif validate_authority() is not True:
                fence("authority_revoked")
            checked[0] = time.monotonic()
        except BaseException:
            fence("authority_revoked")

    def signal_group(sig):
        with lifecycle_lock:
            if process.returncode is not None:
                return
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass

    def watch():
        while not done.wait(0.1):
            if time.monotonic() - checked[0] >= AUTHORITY_DEADLINE:
                fence("authority_timeout")
            with lifecycle_lock:
                if done.is_set() or process.returncode is not None:
                    return
                status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if status is not None:
                if leader_exited[0] is None:
                    leader_exited[0] = time.monotonic()
                elif time.monotonic() - leader_exited[0] >= DRAIN_SECONDS:
                    fence("worker_exit_without_drain")
            if stopped.is_set():
                fence("cancelled")
                signal_group(signal.SIGTERM)
                if not done.wait(2.0):
                    signal_group(signal.SIGKILL)
                return

    def check_authority():
        while not done.is_set() and not stopped.is_set():
            authority()
            if done.wait(AUTHORITY_INTERVAL):
                return

    def capture_output():
        try:
            while not done.is_set():
                if not select.select([process.stdout], [], [], 0.2)[0]:
                    continue
                chunk = os.read(process.stdout.fileno(), 64 * 1024)
                if not chunk:
                    output_eof.set()
                    return
                output.extend(chunk)
                del output[:-64 * 1024]
        except (OSError, ValueError):
            fence("output_read_failed")

    def callback(function, *args, role, operation="none", timeout=None):
        completed, result, error = threading.Event(), [], []
        with callbacks_lock:
            callbacks[:] = [event for event in callbacks if not event.is_set()]
            callbacks.append(completed)
        deadline = time.monotonic() + timeout if timeout is not None else None
        def invoke():
            try:
                result.append(function(*args))
            except BaseException as exc:
                error.append(True)
                try:
                    record = _callback_failure_record(role, operation, exc)
                except BaseException:
                    record = {"role": role, "operation": "unknown", "native_code": None,
                              "failure": {"stage": "generation", "exception_type": "Exception", "source": None}}
                with callbacks_lock:
                    if callback_failure[0] is None:
                        callback_failure[0] = record
            finally:
                completed.set()
        threading.Thread(target=invoke, daemon=True).start()
        while not completed.wait(0.1):
            if deadline is not None and time.monotonic() >= deadline:
                fence("authority_timeout")
            channel._check()
        channel._check()
        if error:
            raise NativeProcessError("callback_failed", callback_failure=callback_failure[0])
        return result[0]

    try:
        callback(authority, role="authority", timeout=AUTHORITY_DEADLINE)
        channel._check()
        env = dict(os.environ if environment is None else environment)
        env[WORKER_FD_ENV] = str(child.fileno())
        process = subprocess.Popen(list(command), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   close_fds=True, pass_fds=(child.fileno(),), start_new_session=True)
        child.close()
        threading.Thread(target=watch, daemon=True).start()
        threading.Thread(target=check_authority, daemon=True).start()
        threading.Thread(target=capture_output, daemon=True).start()
        if on_started is not None:
            callback(on_started, process, role="on_started")
        channel._send("bootstrap", "", bootstrap, encoded=True)
        while True:
            message = channel._receive()
            kind, name, payload = message["kind"], message["name"], message["payload"]
            if kind == "event":
                callback(on_event, name, payload, role="on_event", operation=name)
            elif kind == "request":
                result = callback(on_request, name, payload, role="on_request", operation=name)
                channel._send("ack", name, {"request_seq": message["seq"], "result": result})
            elif kind == "failure" and name == "":
                worker_failure = _validate_failure(payload)
                raise NativeProcessError("worker_failure", worker_failure=worker_failure)
            elif kind == "finish" and name == "":
                result = payload
                break
            else:
                raise NativeProcessError("unexpected_message")
        deadline = time.monotonic() + DRAIN_SECONDS
        while time.monotonic() < deadline:
            channel._check()
            ready = select.select([parent], [], [], 0.1)[0]
            if ready:
                if parent.recv(1):
                    raise NativeProcessError("post_terminal_message")
                if output_eof.is_set() and not _group_live(process.pid):
                    break
        else:
            raise NativeProcessError("drain_timeout")
        channel._check()
        callback(authority, role="authority", timeout=AUTHORITY_DEADLINE)
        with abort_lock:
            channel._check()
            done.set()
        with lifecycle_lock:
            exit_code = process.wait(timeout=1.0)
        if exit_code != 0:
            raise NativeProcessError("worker_exit")
        return result
    except BaseException as exc:
        fence(exc.code if isinstance(exc, NativeProcessError) else "transport_failed", force=True)
        raised_error = NativeProcessError(reason[0], worker_failure=worker_failure, cause_code=reason[0],
                                          callback_failure=callback_failure[0])
        raise raised_error from None
    finally:
        try:
            if process is not None and process.returncode is None:
                # A stalled caller fence cannot prevent owned-group termination.
                if stopped.is_set():
                    abort_notified.wait(0.25)
                signal_group(signal.SIGTERM)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and _group_live(process.pid):
                    time.sleep(0.05)
                signal_group(signal.SIGKILL)
                deadline = time.monotonic() + DRAIN_SECONDS
                while time.monotonic() < deadline and _group_live(process.pid):
                    time.sleep(0.05)
                if _group_live(process.pid):
                    raise NativeProcessError("custody_unresolved")
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    try:
                        if not parent.recv(64 * 1024):
                            break
                    except socket.timeout:
                        continue
                else:
                    raise NativeProcessError("channel_drain_unresolved")
                if not output_eof.wait(1.0):
                    raise NativeProcessError("output_drain_unresolved")
                done.set()
                with lifecycle_lock:
                    process.wait(timeout=1.0)
            with callbacks_lock:
                unfinished = [event for event in callbacks if not event.is_set()]
            if unfinished:
                raise NativeProcessError("callback_drain_unresolved")
        except BaseException as cleanup_error:
            cause_code = reason[0] if reason else None
            cleanup_code = cleanup_error.code if isinstance(cleanup_error, NativeProcessError) else "cleanup_failed"
            if process is not None:
                try:
                    signal_group(signal.SIGKILL)
                except OSError:
                    pass
                error = NativeProcessError("custody_unresolved", worker_failure=worker_failure,
                                           exit_code=process.returncode, cause_code=cause_code,
                                           cleanup_code=cleanup_code, callback_failure=callback_failure[0])
                raised_error = error
                error.process = process
                with callbacks_lock:
                    error.callback_completion = [event for event in callbacks if not event.is_set()]
                raise error from None
            raise NativeProcessError("cleanup_failed", cause_code=cause_code, cleanup_code=cleanup_code,
                                     callback_failure=callback_failure[0]) from None
        finally:
            if raised_error is not None and raised_error.callback_failure is None:
                raised_error.callback_failure = callback_failure[0]
            if raised_error is not None and process is not None:
                raised_error.exit_code = process.returncode
            done.set()
            channel.close()
            child.close()
            if process is not None:
                process.stdout.close()
            output.clear()
