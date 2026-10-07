"""Durable exclusion for native Blender operations with uncertain completion.

This is admission protection, not GPU lease authority or a process terminator.
Never remove a pending record on waiter cancellation, timeout or backend exit.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import uuid


class BlenderNativeUnresolved(RuntimeError):
    pass


_HOLD = ("A previous Blender operation has not been confirmed finished. "
         "Stop that Blender instance and start Blender again before starting more GPU work.")


def process_identity(pid):
    """Independently inspect process birth; unreadable/reused identities hold."""
    if type(pid) is not int or pid < 1:
        raise BlenderNativeUnresolved(_HOLD)
    if sys.platform.startswith("linux"):
        root = Path("/proc") / str(pid)
        try:
            raw = (root / "stat").read_text()
            fields = raw[raw.rindex(")") + 2:].split()
            return {"platform": "linux", "pid": pid, "owner": str(root.stat().st_uid),
                    "birth": str(int(fields[19])), "exe": os.readlink(root / "exe"),
                    "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
        except FileNotFoundError:
            if not root.exists():
                return None
        except (OSError, ValueError, IndexError):
            pass
        raise BlenderNativeUnresolved(_HOLD)
    try:
        import psutil
        process = psutil.Process(pid)
        with process.oneshot():
            owner = str(process.uids().real) if os.name == "posix" else process.username()
            return {"platform": sys.platform, "pid": pid, "owner": owner,
                    "birth": repr(process.create_time()), "exe": process.exe(),
                    "boot": "process-create-time"}
    except ImportError:
        raise BlenderNativeUnresolved(_HOLD) from None
    except psutil.NoSuchProcess:
        return None
    except (psutil.Error, OSError):
        raise BlenderNativeUnresolved(_HOLD) from None


def _validate_identity(value):
    if (type(value) is not dict
            or set(value) != {"platform", "pid", "owner", "birth", "exe", "boot"}
            or type(value["pid"]) is not int or value["pid"] < 1
            or any(type(value[k]) is not str or not value[k] for k in
                   ("platform", "owner", "birth", "exe", "boot"))
            or not Path(value["exe"]).is_absolute()):
        raise BlenderNativeUnresolved(_HOLD)
    return value


class NativeFence:
    def __init__(self, root, *, process_reader=process_identity):
        self.root = Path(os.path.abspath(root))
        self._process_reader = process_reader
        self._lock = threading.RLock()

    def _private_file(self, path, flags):
        if path.exists() or path.is_symlink():
            self._reject_redirect(path)
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or (os.name == "posix" and (metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077))):
            os.close(fd)
            raise BlenderNativeUnresolved(_HOLD)
        return fd

    @staticmethod
    def _reject_redirect(path):
        metadata = path.lstat()
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise BlenderNativeUnresolved(_HOLD)

    @contextmanager
    def _serialized(self):
        with self._lock:
            fd = None
            locked = False
            try:
                # Resolve neither links nor reparse redirects into a new state store.
                for path in reversed((self.root, *self.root.parents)):
                    if path.exists() or path.is_symlink():
                        self._reject_redirect(path)
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory = self.root.lstat()
                if (not stat.S_ISDIR(directory.st_mode)
                        or (os.name == "posix" and (directory.st_uid != os.getuid()
                            or directory.st_mode & 0o077))):
                    raise BlenderNativeUnresolved(_HOLD)
                try:
                    fd = self._private_file(self.root / "lock", os.O_RDWR | os.O_CREAT | os.O_EXCL)
                    new = True
                    os.write(fd, b"0")
                    os.fsync(fd)
                except FileExistsError:
                    fd = self._private_file(self.root / "lock", os.O_RDWR)
                    new = False
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
                lock_path = (self.root / "lock").lstat()
                descriptor = os.fstat(fd)
                if (lock_path.st_dev, lock_path.st_ino) != (descriptor.st_dev, descriptor.st_ino):
                    raise BlenderNativeUnresolved(_HOLD)
                current = self.root.lstat()
                if (current.st_dev, current.st_ino) != (directory.st_dev, directory.st_ino):
                    raise BlenderNativeUnresolved(_HOLD)
                if new and not (self.root / "state.json").exists():
                    self._write(None)
                yield
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                raise BlenderNativeUnresolved(_HOLD) from None
            finally:
                if fd is not None:
                    try:
                        if locked:
                            if os.name == "nt":
                                import msvcrt
                                os.lseek(fd, 0, os.SEEK_SET)
                                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl
                                fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)

    def _read(self):
        fd = self._private_file(self.root / "state.json", os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise BlenderNativeUnresolved(_HOLD)
        value = json.loads(raw)
        if (type(value) is not dict or set(value) != {"schema", "pending"}
                or type(value["schema"]) is not int or value["schema"] != 1):
            raise BlenderNativeUnresolved(_HOLD)
        pending = value["pending"]
        if pending is not None:
            if (type(pending) is not dict
                    or set(pending) != {"token", "identity", "incarnation", "command_sha256"}
                    or any(type(pending[k]) is not str for k in
                           ("token", "incarnation", "command_sha256"))
                    or not re.fullmatch(r"[0-9a-f]{32}", pending["token"])
                    or not re.fullmatch(r"[0-9a-f]{32}", pending["incarnation"])
                    or not re.fullmatch(r"[0-9a-f]{64}", pending["command_sha256"])):
                raise BlenderNativeUnresolved(_HOLD)
            _validate_identity(pending["identity"])
        return pending

    def _write(self, pending):
        temporary = self.root / (".state-" + uuid.uuid4().hex)
        fd = self._private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "pending": pending}, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.root / "state.json")
        if os.name == "posix":
            fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _idle(self):
        pending = self._read()
        if pending is None:
            return
        platform = "linux" if sys.platform.startswith("linux") else sys.platform
        if pending["identity"]["platform"] != platform:
            raise BlenderNativeUnresolved(_HOLD)
        current = self._process_reader(pending["identity"]["pid"])
        if current is None:
            # Only an independently absent exact PID qualifies. Reuse holds.
            self._write(None)
            return
        raise BlenderNativeUnresolved(_HOLD)

    def require_idle(self):
        with self._serialized():
            self._idle()

    def begin(self, identity, incarnation, name, arguments):
        _validate_identity(identity)
        if not re.fullmatch(r"[0-9a-f]{32}", str(incarnation)):
            raise BlenderNativeUnresolved(_HOLD)
        with self._serialized():
            self._idle()
            if self._process_reader(identity["pid"]) != identity:
                raise BlenderNativeUnresolved(_HOLD)
            token = uuid.uuid4().hex
            command = json.dumps([name, arguments], sort_keys=True, allow_nan=False).encode()
            self._write({"token": token, "identity": identity, "incarnation": incarnation,
                         "command_sha256": hashlib.sha256(command).hexdigest()})
            return token

    def completed(self, token, identity, incarnation):
        """Called only after the private same-incarnation terminal/idle probe."""
        with self._serialized():
            pending = self._read()
            if (pending is None or pending["token"] != token
                    or pending["identity"] != identity or pending["incarnation"] != incarnation
                    or self._process_reader(identity["pid"]) != identity):
                raise BlenderNativeUnresolved(_HOLD)
            self._write(None)

    def status(self):
        try:
            self.require_idle()
            return {"native_operation_unresolved": False}
        except BlenderNativeUnresolved:
            return {"native_operation_unresolved": True, "recovery_action": _HOLD}

    def wrap_lock(self, lock):
        return _FencedLock(lock, self)


class _FencedLock:
    def __init__(self, lock, fence):
        self._lock, self._fence = lock, fence

    def acquire(self, blocking=True, timeout=-1):
        self._fence.require_idle()
        acquired = self._lock.acquire(blocking, timeout)
        if acquired:
            try:
                self._fence.require_idle()
            except BaseException:
                self._lock.release()
                raise
        return acquired

    def release(self):
        self._lock.release()

    def locked(self):
        return self._lock.locked()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_args):
        self.release()


fence = NativeFence(Path(__file__).resolve().parents[1] / "storage" / "blender-native")
