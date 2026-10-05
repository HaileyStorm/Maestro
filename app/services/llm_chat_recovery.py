"""Private, bounded Chat receipts; this store never schedules or retries work."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from .queue_recovery_adapter import (
    PromptEnhancementResultStore,
    PromptEnhancementRecoveryError,
    PromptEnhancementRecoveryStore,
    PROMPT_ENHANCEMENT_MAX_RESULT_BYTES,
)

SCHEMA = "maestro.chat-recovery.v1"
MAX_RESULT_BYTES = PROMPT_ENHANCEMENT_MAX_RESULT_BYTES
MAX_LEDGER_BYTES = 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL = frozenset({"completed", "failed", "interrupted", "cancelled"})
_KEYS = frozenset({"request_id", "owner_scope", "project_scope", "request_digest",
                   "claim_digest", "epoch_digest", "status", "created_at", "updated_at",
                   "expires_at", "failure_code", "result_reference"})
_FAILURES = frozenset({"execution_failed", "interrupted", "cancelled", "storage_failed"})


class ChatRecoveryError(RuntimeError):
    pass


class ChatRecoveryConflict(ChatRecoveryError):
    pass


class ChatRecoveryCapacityError(ChatRecoveryError):
    pass


class ChatRecoveryCorruptionError(ChatRecoveryError):
    pass


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ChatRecoveryError("Chat recovery value is not durable JSON.") from None


def _uuid(value):
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Chat request ID must be a UUID.") from None
    if value not in {parsed.hex, str(parsed)}:
        raise ValueError("Chat request ID must be canonical.")
    return parsed.hex


def _digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError("Chat request digest is invalid.")
    return value


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class ChatRecoveryStore:
    def __init__(self, root, secret, *, ttl_seconds=900, max_records=128, clock=time.time):
        if not isinstance(secret, bytes) or len(secret) < 16:
            raise ValueError("Chat recovery requires a server secret.")
        if type(ttl_seconds) not in {int, float} or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("Chat retention must be finite and positive.")
        if type(max_records) is not int or not 1 <= max_records <= 128:
            raise ValueError("Chat record bound is invalid.")
        self.root = Path(root).absolute()
        self.metadata_path = self.root / "chat.json"
        self._lock_path = self.root / ".chat.lock"
        self._secret = secret
        self.ttl_seconds = float(ttl_seconds)
        self.max_records = max_records
        self._clock = clock
        self._lock = threading.RLock()
        self._epoch = None
        self._expected_ledger = (self.metadata_path.exists() or self.metadata_path.is_symlink()
                                 or self._lock_path.exists() or self._lock_path.is_symlink())
        self._directories = (self.root, self.root / "payloads", self.root / "payloads/results")
        try:
            for path in self._directories:
                if not path.exists() and not path.is_symlink():
                    path.mkdir(mode=0o700, parents=True)
                self._safe_directory(path)
            self._results = PromptEnhancementResultStore(self.root / "payloads", max_records=max_records + 1)
            self._directory_seals = {path: (path.stat().st_dev, path.stat().st_ino) for path in self._directories}
        except (OSError, PromptEnhancementRecoveryError) as error:
            raise ChatRecoveryError("Chat recovery storage is unavailable.") from error

    @staticmethod
    def _safe_directory(path):
        for ancestor in (path, *path.parents):
            info = ancestor.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ChatRecoveryCorruptionError("Chat recovery directory is unsafe.")
        info = path.lstat()
        if os.name != "nt" and (stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid()):
            raise ChatRecoveryCorruptionError("Chat recovery directory is not private.")

    def _storage_check(self):
        try:
            for path in self._directories:
                self._safe_directory(path)
                info = path.lstat()
                if (info.st_dev, info.st_ino) != self._directory_seals[path]:
                    raise ChatRecoveryCorruptionError("Chat recovery directory was replaced.")
        except OSError as error:
            raise ChatRecoveryError("Chat recovery storage is unavailable.") from error

    def _hmac(self, domain, value):
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096:
            raise ValueError("Chat recovery identity is invalid.")
        return hmac.new(self._secret, domain.encode("ascii") + b"\0" + value.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def _now(self):
        now = self._clock()
        if type(now) not in {int, float} or not math.isfinite(now):
            raise ChatRecoveryError("Chat recovery clock is unavailable.")
        return float(now)

    def _seal(self, ledger):
        return hmac.new(self._secret, b"chat-ledger-v1\0" + _json(ledger), hashlib.sha256).hexdigest()

    @contextmanager
    def _serialized(self):
        with self._lock:
            self._storage_check()
            deadline = time.monotonic() + 3.0
            descriptor = -1
            try:
                descriptor = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
                self._safe_file(descriptor, self._lock_path)
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(descriptor).st_size == 0:
                        os.write(descriptor, b"\0")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    while True:
                        try:
                            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise ChatRecoveryError("Chat recovery is temporarily busy.") from None
                            time.sleep(0.01)
                else:
                    import fcntl
                    while True:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise ChatRecoveryError("Chat recovery is temporarily busy.") from None
                            time.sleep(0.01)
                self._safe_file(descriptor, self._lock_path)
                self._storage_check()
                yield
            except (OSError, PromptEnhancementRecoveryError) as error:
                raise ChatRecoveryError("Chat recovery storage is unavailable.") from error
            finally:
                # Closing releases the OS lock; a persistent file is never stolen by age.
                if descriptor >= 0:
                    os.close(descriptor)

    @staticmethod
    def _safe_file(descriptor, path):
        info = os.fstat(descriptor)
        named = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or _identity(info) != _identity(named)
                or (os.name != "nt" and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()))):
            raise ChatRecoveryCorruptionError("Chat recovery file is unsafe.")
        return info

    def _read(self):
        try:
            descriptor = os.open(self.metadata_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            if self._expected_ledger or any(self._results._files.results_root.iterdir()):
                raise ChatRecoveryCorruptionError("Chat recovery ledger is missing.") from None
            return {"schema": SCHEMA, "epoch_digest": None, "records": {}}
        except OSError as error:
            raise ChatRecoveryError("Chat recovery ledger is unavailable.") from error
        try:
            initial = self._safe_file(descriptor, self.metadata_path)
            raw = bytearray()
            while len(raw) <= MAX_LEDGER_BYTES:
                part = os.read(descriptor, min(65536, MAX_LEDGER_BYTES + 1 - len(raw)))
                if not part:
                    break
                raw.extend(part)
            if _identity(initial) != _identity(self._safe_file(descriptor, self.metadata_path)):
                raise ChatRecoveryCorruptionError("Chat recovery ledger changed during reading.")
        finally:
            os.close(descriptor)
        try:
            sealed = json.loads(raw)
            if type(sealed) is not dict or set(sealed) != {"schema", "epoch_digest", "records", "integrity"}:
                raise ValueError()
            integrity = sealed.pop("integrity")
            if (len(raw) > MAX_LEDGER_BYTES or not isinstance(integrity, str)
                    or not hmac.compare_digest(integrity, self._seal(sealed))):
                raise ValueError()
            self._validate_ledger(sealed)
        except (ValueError, TypeError, UnicodeError, RecursionError, ChatRecoveryError):
            raise ChatRecoveryCorruptionError("Chat recovery ledger is corrupt.") from None
        return sealed

    def _validate_ledger(self, ledger):
        if (ledger["schema"] != SCHEMA or not _DIGEST.fullmatch(ledger["epoch_digest"] or "")
                or type(ledger["records"]) is not dict or len(ledger["records"]) > self.max_records):
            raise ValueError()
        for key, record in ledger["records"].items():
            if type(record) is not dict or set(record) != _KEYS or _uuid(key) != record["request_id"]:
                raise ValueError()
            for field in ("owner_scope", "project_scope", "request_digest", "claim_digest", "epoch_digest"):
                _digest(record[field])
            if record["status"] not in _TERMINAL | {"running"}:
                raise ValueError()
            for field in ("created_at", "updated_at", "expires_at"):
                if type(record[field]) not in {int, float} or not math.isfinite(record[field]):
                    raise ValueError()
            if not record["created_at"] <= record["updated_at"] <= record["expires_at"]:
                raise ValueError()
            if record["failure_code"] not in _FAILURES | {None}:
                raise ValueError()
            if (record["status"] in {"failed", "interrupted", "cancelled"}) != (record["failure_code"] is not None):
                raise ValueError()
            ref = PromptEnhancementRecoveryStore._result_reference(record["result_reference"])
            if (record["status"] == "completed") != (ref is not None):
                raise ValueError()
            if ref is not None and not Path(ref["path"]).name.startswith(uuid.UUID(key).hex + "."):
                raise ValueError()

    def _write(self, ledger):
        self._validate_ledger(ledger)
        document = {**ledger, "integrity": self._seal(ledger)}
        encoded = _json(document)
        if len(encoded) > MAX_LEDGER_BYTES:
            raise ChatRecoveryCapacityError("Chat recovery history is full.")
        temporary = self.root / (".chat." + uuid.uuid4().hex + ".tmp")
        descriptor = -1
        try:
            self._storage_check()
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            view = memoryview(encoded)
            while view:
                size = os.write(descriptor, view)
                if size <= 0:
                    raise OSError("short write")
                view = view[size:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.metadata_path)
            self._results._files._fsync_directory(self.root)
            self._expected_ledger = True
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _gate(self, ledger, epoch=None):
        if self._epoch is None or ledger["epoch_digest"] != self._epoch:
            raise ChatRecoveryError("Chat recovery startup initialization is required.")
        if epoch is not None and self._hmac("chat-epoch-v1", epoch) != self._epoch:
            raise ChatRecoveryConflict("Chat execution epoch is stale.")

    @staticmethod
    def _receipt(record):
        return deepcopy({key: record[key] for key in ("request_id", "status", "created_at", "updated_at",
                          "expires_at", "failure_code", "result_reference")})

    def _scope(self, record, owner_key, project_key, request_digest=None):
        if not (hmac.compare_digest(record["owner_scope"], self._hmac("chat-owner-v1", owner_key))
                and hmac.compare_digest(record["project_scope"], self._hmac("chat-project-v1", project_key))):
            return False
        if request_digest is not None and not hmac.compare_digest(record["request_digest"], _digest(request_digest)):
            raise ChatRecoveryConflict("Chat request ID is already bound to different inputs.")
        return True

    def _prune(self, ledger, now):
        retired = []
        for key, record in list(ledger["records"].items()):
            if record["status"] in _TERMINAL and record["expires_at"] <= now:
                if record["result_reference"] is not None:
                    retired.append((key, record["result_reference"]))
                del ledger["records"][key]
        return retired

    def _read_final(self, request_id, reference):
        clean = PromptEnhancementRecoveryStore._result_reference(reference)
        path = self._results._files.root / clean["path"]
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (os.name != "nt" and (stat.S_IMODE(before.st_mode) != 0o600 or before.st_uid != os.getuid()))):
            raise ChatRecoveryCorruptionError("Chat result is unsafe.")
        result = self._result(self._results.read(request_id, clean))
        if _identity(before) != _identity(path.lstat()):
            raise ChatRecoveryCorruptionError("Chat result changed during reading.")
        return result

    def _remove_retired(self, retired):
        for request_id, reference in retired:
            path = self._results._files.root / reference["path"]
            before = path.lstat()
            self._read_final(request_id, reference)
            if _identity(before) != _identity(path.lstat()):
                raise ChatRecoveryCorruptionError("Retired Chat result changed before removal.")
            path.unlink()
        if retired:
            self._results._files._fsync_directory(self._results._files.results_root)

    def initialize_epoch(self, epoch):
        epoch_digest = self._hmac("chat-epoch-v1", epoch)
        if self._epoch is not None and self._epoch != epoch_digest:
            raise ChatRecoveryConflict("Chat startup epoch is already initialized.")
        with self._serialized():
            ledger = self._read()
            if self._epoch is not None:
                self._gate(ledger, epoch)
            now = self._now()
            for key, record in ledger["records"].items():
                if record["result_reference"] is not None:
                    self._read_final(key, record["result_reference"])
            retired = self._prune(ledger, now)
            interrupted = 0
            if ledger["epoch_digest"] != epoch_digest:
                for record in ledger["records"].values():
                    if record["status"] == "running":
                        record.update(status="interrupted", failure_code="interrupted",
                                      updated_at=max(now, record["updated_at"]),
                                      expires_at=max(now, record["updated_at"]) + self.ttl_seconds)
                        interrupted += 1
            ledger["epoch_digest"] = epoch_digest
            for key, reference in retired:
                self._read_final(key, reference)
            self._write(ledger)
            self._remove_retired(retired)
            self._epoch = epoch_digest
            return interrupted

    def lookup(self, request_id, *, owner_key, project_key, request_digest=None):
        request_id = _uuid(request_id)
        with self._serialized():
            ledger = self._read()
            self._gate(ledger)
            record = ledger["records"].get(request_id)
            if (record is None or (record["status"] in _TERMINAL and record["expires_at"] <= self._now())
                    or not self._scope(record, owner_key, project_key, request_digest)):
                return None
            return self._receipt(record)

    def bind(self, request_id, *, owner_key, project_key, request_digest, execution_claim, epoch):
        request_id = _uuid(request_id)
        request_digest = _digest(request_digest)
        owner = self._hmac("chat-owner-v1", owner_key)
        project = self._hmac("chat-project-v1", project_key)
        claim = self._hmac("chat-claim-v1", execution_claim)
        with self._serialized():
            ledger = self._read()
            self._gate(ledger, epoch)
            now = self._now()
            record = ledger["records"].get(request_id)
            if record is not None:
                if not self._scope(record, owner_key, project_key, request_digest):
                    raise ChatRecoveryConflict("Chat request ID is unavailable in this scope.")
                return False, self._receipt(record)
            retired = self._prune(ledger, now)
            if len(ledger["records"]) >= self.max_records:
                terminal = [r for r in ledger["records"].values() if r["status"] in _TERMINAL]
                if not terminal:
                    raise ChatRecoveryCapacityError("Chat recovery is busy.")
                oldest = min(terminal, key=lambda r: r["updated_at"])
                if oldest["result_reference"] is not None:
                    retired.append((oldest["request_id"], oldest["result_reference"]))
                del ledger["records"][oldest["request_id"]]
            record = dict(request_id=request_id, owner_scope=owner, project_scope=project,
                          request_digest=request_digest, claim_digest=claim, epoch_digest=self._epoch,
                          status="running", created_at=now, updated_at=now, expires_at=now + self.ttl_seconds,
                          failure_code=None, result_reference=None)
            ledger["records"][request_id] = record
            for key, reference in retired:
                self._read_final(key, reference)
            self._write(ledger)
            self._remove_retired(retired)
            return True, self._receipt(record)

    @staticmethod
    def _result(result):
        if (type(result) is not dict or not {"text", "model_id", "guide_ids"} <= set(result)
                or set(result) - {"text", "model_id", "guide_ids", "stats"}
                or not isinstance(result["text"], str) or not isinstance(result["model_id"], str)
                or not 1 <= len(result["model_id"]) <= 512 or type(result["guide_ids"]) is not list
                or len(result["guide_ids"]) > 32 or any(not isinstance(x, str) or not 1 <= len(x) <= 512 for x in result["guide_ids"])
                or type(result.get("stats", {})) is not dict or len(result.get("stats", {})) > 32):
            raise ChatRecoveryError("Chat final result is invalid.")
        for key, value in result.get("stats", {}).items():
            if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                    or (value is not None and (type(value) not in {int, float} or value < 0
                                               or (type(value) is float and not math.isfinite(value))
                                               or (type(value) is int and value > 2**63 - 1)))):
                raise ChatRecoveryError("Chat result statistics are invalid.")
        if len(_json(result)) > MAX_RESULT_BYTES - 256:
            raise ChatRecoveryCapacityError("Chat final result exceeds its durable bound.")
        return deepcopy(result)

    def _finish(self, request_id, *, owner_key, project_key, execution_claim, epoch,
                status, result=None, failure_code=None):
        request_id = _uuid(request_id)
        claim = self._hmac("chat-claim-v1", execution_claim)
        with self._serialized():
            ledger = self._read()
            self._gate(ledger, epoch)
            record = ledger["records"].get(request_id)
            if record is None or not self._scope(record, owner_key, project_key):
                return None
            if record["claim_digest"] != claim or record["epoch_digest"] != self._epoch:
                raise ChatRecoveryConflict("Chat execution claim is stale.")
            if record["status"] != "running":
                return self._receipt(record)
            reference = self._results.write(request_id, self._result(result)) if status == "completed" else None
            now = max(self._now(), record["updated_at"])
            record.update(status=status, failure_code=failure_code, result_reference=reference,
                          updated_at=now, expires_at=now + self.ttl_seconds)
            # On an ambiguous commit keep the result: a fresh read alone decides visibility.
            self._write(ledger)
            return self._receipt(record)

    def complete(self, request_id, *, result, **scope):
        return self._finish(request_id, status="completed", result=result, **scope)

    def fail(self, request_id, *, failure_code="execution_failed", **scope):
        if failure_code not in _FAILURES - {"interrupted", "cancelled"}:
            raise ValueError("Chat failure code is invalid.")
        return self._finish(request_id, status="failed", failure_code=failure_code, **scope)

    def cancel(self, request_id, **scope):
        return self._finish(request_id, status="cancelled", failure_code="cancelled", **scope)

    def read_result(self, request_id, *, owner_key, project_key, request_digest=None, result_reference=None):
        request_id = _uuid(request_id)
        with self._serialized():
            ledger = self._read()
            self._gate(ledger)
            record = ledger["records"].get(request_id)
            if (record is None or not self._scope(record, owner_key, project_key, request_digest)
                    or record["status"] != "completed" or record["expires_at"] <= self._now()):
                return None
            reference = record["result_reference"]
            if result_reference is not None and result_reference != reference:
                raise ChatRecoveryConflict("Chat result reference is stale.")
            return self._read_final(request_id, reference)
