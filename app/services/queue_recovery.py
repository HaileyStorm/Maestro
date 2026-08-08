"""Strict durable state journal for Maestro queue recovery.

Callers commit complete, allowlisted job and queue-control snapshots.  A single
``state_commit`` event can atomically update several jobs, tombstones, and the
global queue state with one write and one file fsync.  Per-job and global
revisions fence stale workers within an epoch. Compaction advances the epoch so
every pre-compaction writer is invalidated while obsolete tombstone IDs can be
discarded. Large active snapshots are split into bounded records inside one
atomic replacement.

This module never converts runtime objects into JSON.  It accepts only plain
JSON dictionaries/lists/scalars and applies a conservative credential/runtime
field denylist as defense in depth.  Launch integration still owns the positive
allowlist of fields that are actually safe and sufficient to resume.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any
import uuid


SCHEMA_VERSION = 2
STATE_COMMIT_EVENT = "state_commit"
STATE_SNAPSHOT_EVENT = "state_snapshot"
STATE_SNAPSHOT_CHUNK_EVENT = "state_snapshot_chunk"
TERMINAL_JOB_STATUSES = frozenset({
    "cancelled",
    "canceled",
    "completed",
    "deleted",
    "error",
    "failed",
    "succeeded",
})

_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEY_PARTS = frozenset({
    "authorization",
    "capability",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passphrase",
    "passwd",
    "secret",
    "secrets",
    "session",
    "sessions",
    "token",
    "tokens",
})
_RUNTIME_KEYS = frozenset({
    "callback",
    "coroutine",
    "event_loop",
    "file_handle",
    "future",
    "generator",
    "handler",
    "lock",
    "pil_image",
    "process",
    "request",
    "response",
    "runtime",
    "runtime_state",
    "socket",
    "stream",
    "subprocess",
    "task",
    "tensor",
    "thread",
    "websocket",
})
_RUNTIME_SUFFIXES = (
    "_callback",
    "_coroutine",
    "_future",
    "_generator",
    "_handle",
    "_lock",
    "_process",
    "_socket",
    "_task",
    "_thread",
)
_FORBIDDEN_NORMALIZED_KEYS = frozenset({
    "api_key",
    "apikey",
    "private_key",
})
_FORBIDDEN_NORMALIZED_SUFFIXES = (
    "_api_key",
    "_private_key",
)
_COMPACT_UNSET = object()


class QueueRecoveryError(RuntimeError):
    """Base class for queue-recovery journal errors."""


class QueueRecoveryValidationError(QueueRecoveryError, ValueError):
    """Raised before an unsafe or stale mutation can be written."""


class QueueRecoveryPersistenceError(QueueRecoveryError):
    """Raised when durable filesystem work cannot be completed."""


class QueueRecoveryCorruptionError(QueueRecoveryError):
    """Raised after corrupt state has been quarantined if possible."""

    def __init__(self, *, quarantined: bool) -> None:
        super().__init__(
            "Queue recovery state was corrupt and has been quarantined."
            if quarantined
            else "Queue recovery state is corrupt and could not be quarantined."
        )
        self.quarantined = quarantined


@dataclass(frozen=True)
class CommitReceipt:
    """Revision information returned after one durable state event."""

    sequence: int
    epoch: int
    job_revisions: dict[str, int]
    global_revision: int


@dataclass(frozen=True)
class RecoverySnapshot:
    """Latest state and revision fences obtained from strict replay."""

    jobs: dict[str, dict[str, Any]]
    job_revisions: dict[str, int]
    epoch: int
    global_state: dict[str, Any] | None
    global_revision: int
    last_sequence: int
    event_count: int
    discarded_torn_tail: bool = False


class _RecordCorruption(Exception):
    pass


class _DuplicateKey(Exception):
    pass


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, OverflowError):
        raise QueueRecoveryValidationError(
            "Queue recovery snapshots must contain only bounded JSON-safe values."
        ) from None


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Any:
    raise ValueError


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        _DuplicateKey,
    ):
        raise _RecordCorruption from None


def _normalized_key(key: str) -> str:
    # Split both ordinary camelCase and acronym-to-word boundaries before the
    # punctuation fold: clientSecret -> client_secret, HFToken -> hf_token.
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _validate_json_mapping(
    value: Mapping[str, Any],
    *,
    max_depth: int,
    max_nodes: int,
    max_string_bytes: int,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise QueueRecoveryValidationError(
            "Queue recovery snapshots must be plain JSON objects."
        )

    active_containers: set[int] = set()
    nodes = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise QueueRecoveryValidationError(
                "Queue recovery snapshots exceed the structural safety limit."
            )
        if item is None or type(item) is bool or type(item) is int:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise QueueRecoveryValidationError(
                    "Queue recovery snapshots must contain finite numbers."
                )
            return item
        if type(item) is str:
            if len(item.encode("utf-8")) > max_string_bytes:
                raise QueueRecoveryValidationError(
                    "Queue recovery snapshots contain an oversized string."
                )
            return item
        if type(item) is dict:
            identity = id(item)
            if identity in active_containers:
                raise QueueRecoveryValidationError(
                    "Queue recovery snapshots may not contain cyclic values."
                )
            active_containers.add(identity)
            try:
                result: dict[str, Any] = {}
                for key, child in item.items():
                    if type(key) is not str or not key or len(key) > 256:
                        raise QueueRecoveryValidationError(
                            "Queue recovery snapshot keys are invalid."
                        )
                    if any(ord(character) < 32 for character in key):
                        raise QueueRecoveryValidationError(
                            "Queue recovery snapshot keys are invalid."
                        )
                    normalized = _normalized_key(key)
                    parts = frozenset(part for part in normalized.split("_") if part)
                    if (
                        parts.intersection(_FORBIDDEN_KEY_PARTS)
                        or normalized in _FORBIDDEN_NORMALIZED_KEYS
                        or normalized.endswith(_FORBIDDEN_NORMALIZED_SUFFIXES)
                        or normalized in _RUNTIME_KEYS
                        or normalized.startswith("runtime_")
                        or normalized.endswith(_RUNTIME_SUFFIXES)
                    ):
                        raise QueueRecoveryValidationError(
                            "Queue recovery snapshots contain a forbidden field."
                        )
                    result[key] = visit(child, depth + 1)
                return result
            finally:
                active_containers.remove(identity)
        if type(item) is list:
            identity = id(item)
            if identity in active_containers:
                raise QueueRecoveryValidationError(
                    "Queue recovery snapshots may not contain cyclic values."
                )
            active_containers.add(identity)
            try:
                return [visit(child, depth + 1) for child in item]
            finally:
                active_containers.remove(identity)
        raise QueueRecoveryValidationError(
            "Queue recovery snapshots must contain only plain JSON-safe values."
        )

    result = visit(value, 0)
    assert type(result) is dict
    return result


def _validate_job_id(job_id: object) -> str:
    if (
        type(job_id) is not str
        or not job_id
        or len(job_id) > 256
        or job_id in {".", ".."}
        or "/" in job_id
        or "\\" in job_id
        or any(ord(character) < 32 for character in job_id)
    ):
        raise QueueRecoveryValidationError("Queue recovery job IDs are invalid.")
    return job_id


def _validate_revision(value: object) -> int:
    if type(value) is not int or value < 0:
        raise QueueRecoveryValidationError("Queue recovery revisions are invalid.")
    return value


def _validate_epoch(value: object) -> int:
    if type(value) is not int or value < 0:
        raise QueueRecoveryValidationError("Queue recovery epochs are invalid.")
    return value


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _fsync_directory(directory: Path) -> None:
    """Flush a changed directory entry where the platform exposes it."""
    if os.name == "nt":
        # Windows has no Python-level directory fsync. os.replace remains
        # atomic and all journal/temp file contents are fsynced first.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class QueueRecoveryJournal:
    """Checksummed, revision-fenced durable queue-state journal."""

    _locks_guard = threading.Lock()
    _path_locks: dict[str, threading.RLock] = {}
    _inode_paths: dict[tuple[int, int], str] = {}
    _path_inodes: dict[str, tuple[int, int]] = {}

    def __init__(
        self,
        journal_path: os.PathLike[str] | str,
        *,
        max_record_bytes: int = 8 * 1024 * 1024,
        max_journal_bytes: int = 256 * 1024 * 1024,
        max_events: int = 100_000,
        max_compacted_jobs: int = 10_000,
        max_depth: int = 32,
        max_nodes: int = 250_000,
        max_string_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if (
            max_record_bytes < 256
            or max_journal_bytes < max_record_bytes
            or max_events < 1
            or max_compacted_jobs < 1
            or max_depth < 1
            or max_nodes < 1
            or max_string_bytes < 1
        ):
            raise ValueError("Queue recovery journal limits are invalid.")
        try:
            requested = Path(journal_path).expanduser().absolute()
            requested.parent.mkdir(parents=True, exist_ok=True)
            if requested.parent.is_symlink() or not requested.parent.is_dir():
                raise ValueError
            parent = requested.parent.resolve(strict=True)
            candidate = parent / requested.name
            existing = self._safe_lstat(candidate, missing_ok=True)
            if existing is not None:
                self._validate_regular_single_link(existing)
        except Exception:
            raise ValueError("Queue recovery journal path is invalid.") from None

        self.path = candidate
        self.lock_path = candidate.with_name(candidate.name + ".lock")
        self.max_record_bytes = max_record_bytes
        self.max_journal_bytes = max_journal_bytes
        self.max_events = max_events
        self.max_compacted_jobs = max_compacted_jobs
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_string_bytes = max_string_bytes

        path_key = os.path.normcase(str(candidate))
        with self._locks_guard:
            self._thread_lock = self._path_locks.setdefault(path_key, threading.RLock())
            if existing is not None:
                self._claim_inode_locked(self._identity(existing), path_key)

    @staticmethod
    def _safe_lstat(path: Path, *, missing_ok: bool) -> os.stat_result | None:
        try:
            return os.lstat(path)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise

    @staticmethod
    def _validate_regular_single_link(info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, int]:
        return int(info.st_dev), int(info.st_ino)

    @classmethod
    def _claim_inode_locked(cls, identity: tuple[int, int], path_key: str) -> None:
        old_identity = cls._path_inodes.get(path_key)
        if (
            old_identity is not None
            and old_identity != identity
            and cls._inode_paths.get(old_identity) == path_key
        ):
            cls._inode_paths.pop(old_identity, None)
        previous = cls._inode_paths.get(identity)
        if previous is not None and previous != path_key:
            try:
                prior_info = os.lstat(previous)
                if cls._identity(prior_info) == identity:
                    raise ValueError
            except FileNotFoundError:
                pass
        cls._inode_paths[identity] = path_key
        cls._path_inodes[path_key] = identity

    def _claim_inode(self, info: os.stat_result) -> None:
        path_key = os.path.normcase(str(self.path))
        with self._locks_guard:
            try:
                self._claim_inode_locked(self._identity(info), path_key)
            except ValueError:
                raise QueueRecoveryPersistenceError(
                    "Queue recovery journal has an unsafe filesystem alias."
                ) from None

    def _open_regular(
        self,
        path: Path,
        flags: int,
        *,
        create: bool,
        claim_journal: bool,
    ) -> tuple[int, os.stat_result, bool]:
        before = self._safe_lstat(path, missing_ok=True)
        if before is None and not create:
            raise QueueRecoveryPersistenceError(
                "Queue recovery state does not exist."
            )
        if before is not None:
            try:
                self._validate_regular_single_link(before)
            except ValueError:
                raise QueueRecoveryPersistenceError(
                    "Queue recovery state path is unsafe."
                ) from None
        open_flags = flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        created = False
        try:
            if create and before is None:
                try:
                    descriptor = os.open(
                        path,
                        open_flags | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    created = True
                except FileExistsError:
                    before = self._safe_lstat(path, missing_ok=False)
                    self._validate_regular_single_link(before)
                    descriptor = os.open(path, open_flags, 0o600)
            else:
                descriptor = os.open(path, open_flags, 0o600)
        except OSError:
            raise QueueRecoveryPersistenceError(
                "Queue recovery state could not be opened safely."
            ) from None
        try:
            opened = os.fstat(descriptor)
            self._validate_regular_single_link(opened)
            after = self._safe_lstat(path, missing_ok=False)
            self._validate_regular_single_link(after)
            if self._identity(opened) != self._identity(after):
                raise ValueError
            if before is not None and self._identity(before) != self._identity(opened):
                raise ValueError
            if claim_journal:
                self._claim_inode(opened)
            return descriptor, opened, created
        except (OSError, ValueError, QueueRecoveryPersistenceError):
            os.close(descriptor)
            raise QueueRecoveryPersistenceError(
                "Queue recovery state changed during a safe open."
            ) from None

    @contextmanager
    def _serialized(self) -> Iterator[None]:
        with self._thread_lock:
            descriptor = -1
            handle = None
            try:
                descriptor, _info, created = self._open_regular(
                    self.lock_path,
                    os.O_RDWR,
                    create=True,
                    claim_journal=False,
                )
                handle = os.fdopen(descriptor, "r+b", closefd=True)
                descriptor = -1
                handle.seek(0, os.SEEK_END)
                if handle.tell() < 1:
                    handle.seek(0)
                    handle.write(b"\0")
                    handle.flush()
                    os.fsync(handle.fileno())
                if created:
                    _fsync_directory(self.lock_path.parent)
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except QueueRecoveryError:
                raise
            except (OSError, EOFError):
                raise QueueRecoveryPersistenceError(
                    "Queue recovery journal locking failed."
                ) from None
            finally:
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:
                        pass
                elif descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    def _sanitize(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        return _validate_json_mapping(
            snapshot,
            max_depth=self.max_depth,
            max_nodes=self.max_nodes,
            max_string_bytes=self.max_string_bytes,
        )

    def _event_bytes(
        self,
        *,
        sequence: int,
        epoch: int,
        event: str,
        jobs: list[dict[str, Any]],
        tombstones: list[dict[str, Any]],
        global_entry: dict[str, Any] | None,
    ) -> bytes:
        base = {
            "epoch": epoch,
            "event": event,
            "global": global_entry,
            "jobs": jobs,
            "schema": SCHEMA_VERSION,
            "sequence": sequence,
            "tombstones": tombstones,
        }
        checksum = hashlib.sha256(_canonical_bytes(base)).hexdigest()
        record = dict(base)
        record["checksum"] = checksum
        encoded = _canonical_bytes(record) + b"\n"
        if len(encoded) > self.max_record_bytes:
            raise QueueRecoveryValidationError(
                "Queue recovery event exceeds the durable record size limit."
            )
        return encoded

    def _validate_record(self, value: Any, expected_sequence: int) -> dict[str, Any]:
        if type(value) is not dict or set(value) != {
            "checksum",
            "epoch",
            "event",
            "global",
            "jobs",
            "schema",
            "sequence",
            "tombstones",
        }:
            raise _RecordCorruption
        event = value.get("event")
        if event not in {
            STATE_COMMIT_EVENT,
            STATE_SNAPSHOT_EVENT,
            STATE_SNAPSHOT_CHUNK_EVENT,
        }:
            raise _RecordCorruption
        if event == STATE_SNAPSHOT_EVENT and expected_sequence != 1:
            raise _RecordCorruption
        if event == STATE_SNAPSHOT_CHUNK_EVENT and expected_sequence == 1:
            raise _RecordCorruption
        if type(value.get("schema")) is not int or value["schema"] != SCHEMA_VERSION:
            raise _RecordCorruption
        if type(value.get("sequence")) is not int or value["sequence"] != expected_sequence:
            raise _RecordCorruption
        try:
            epoch = _validate_epoch(value.get("epoch"))
        except QueueRecoveryValidationError:
            raise _RecordCorruption from None
        if event in {STATE_SNAPSHOT_EVENT, STATE_SNAPSHOT_CHUNK_EVENT} and epoch == 0:
            raise _RecordCorruption
        checksum = value.get("checksum")
        if type(checksum) is not str or not _CHECKSUM_RE.fullmatch(checksum):
            raise _RecordCorruption

        unsigned = dict(value)
        unsigned.pop("checksum")
        try:
            calculated = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
        except QueueRecoveryValidationError:
            raise _RecordCorruption from None
        if not hmac.compare_digest(checksum, calculated):
            raise _RecordCorruption

        jobs = value.get("jobs")
        tombstones = value.get("tombstones")
        if type(jobs) is not list or type(tombstones) is not list:
            raise _RecordCorruption
        seen: set[str] = set()
        previous_id: str | None = None
        for entry in jobs:
            if type(entry) is not dict or set(entry) != {"job_id", "payload", "revision"}:
                raise _RecordCorruption
            try:
                job_id = _validate_job_id(entry["job_id"])
                revision = _validate_revision(entry["revision"])
                if revision == 0:
                    raise QueueRecoveryValidationError
                entry["payload"] = self._sanitize(entry["payload"])
            except (KeyError, QueueRecoveryValidationError):
                raise _RecordCorruption from None
            if job_id in seen or (previous_id is not None and job_id <= previous_id):
                raise _RecordCorruption
            embedded_id = entry["payload"].get("id")
            if embedded_id is not None and embedded_id != job_id:
                raise _RecordCorruption
            seen.add(job_id)
            previous_id = job_id

        previous_id = None
        for entry in tombstones:
            if type(entry) is not dict or set(entry) != {"job_id", "revision"}:
                raise _RecordCorruption
            try:
                job_id = _validate_job_id(entry["job_id"])
                revision = _validate_revision(entry["revision"])
                if revision == 0:
                    raise QueueRecoveryValidationError
            except (KeyError, QueueRecoveryValidationError):
                raise _RecordCorruption from None
            if job_id in seen or (previous_id is not None and job_id <= previous_id):
                raise _RecordCorruption
            seen.add(job_id)
            previous_id = job_id

        global_entry = value.get("global")
        if global_entry is not None:
            if type(global_entry) is not dict or set(global_entry) != {"payload", "revision"}:
                raise _RecordCorruption
            try:
                revision = _validate_revision(global_entry["revision"])
                if revision == 0:
                    raise QueueRecoveryValidationError
                global_entry["payload"] = self._sanitize(global_entry["payload"])
            except (KeyError, QueueRecoveryValidationError):
                raise _RecordCorruption from None
        if (
            event == STATE_SNAPSHOT_CHUNK_EVENT
            and (global_entry is not None or tombstones)
        ):
            raise _RecordCorruption
        if event == STATE_COMMIT_EVENT and not jobs and not tombstones and global_entry is None:
            raise _RecordCorruption
        if event == STATE_SNAPSHOT_CHUNK_EVENT and not jobs:
            raise _RecordCorruption
        return value

    def _quarantine(self, expected_identity: tuple[int, int] | None) -> bool:
        if expected_identity is None:
            return False
        destination = self.path.with_name(
            f".{self.path.name}.corrupt-{uuid.uuid4().hex}"
        )
        try:
            current = self._safe_lstat(self.path, missing_ok=False)
            self._validate_regular_single_link(current)
            if self._identity(current) != expected_identity:
                return False
            os.replace(self.path, destination)
            _fsync_directory(self.path.parent)
            return True
        except (OSError, ValueError):
            return False

    def _scan_locked(
        self,
    ) -> tuple[list[dict[str, Any]], bool, tuple[int, int] | None]:
        try:
            descriptor, info, _created = self._open_regular(
                self.path,
                os.O_RDWR,
                create=False,
                claim_journal=True,
            )
        except QueueRecoveryPersistenceError:
            if self._safe_lstat(self.path, missing_ok=True) is None:
                return [], False, None
            raise

        identity = self._identity(info)
        records: list[dict[str, Any]] = []
        total = 0
        torn_offset: int | None = None
        try:
            with os.fdopen(os.dup(descriptor), "rb") as reader:
                while True:
                    offset = reader.tell()
                    line = reader.readline(self.max_record_bytes + 1)
                    if not line:
                        break
                    total += len(line)
                    if total > self.max_journal_bytes or len(line) > self.max_record_bytes:
                        raise _RecordCorruption
                    if not line.endswith(b"\n"):
                        torn_offset = offset
                        break
                    body = line[:-1]
                    if body.endswith(b"\r"):
                        body = body[:-1]
                    record = self._validate_record(_parse_json(body), len(records) + 1)
                    records.append(record)
                    if len(records) > self.max_events:
                        raise _RecordCorruption
            # Revision semantics are part of journal validity, not a caller
            # concern. Verify them before repairing any otherwise-tolerable tail.
            self._replay(records, discarded_torn_tail=False)
            if torn_offset is not None:
                os.ftruncate(descriptor, torn_offset)
                os.fsync(descriptor)
                _fsync_directory(self.path.parent)
                return records, True, identity
            return records, False, identity
        except _RecordCorruption:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = -1
            quarantined = self._quarantine(identity)
            raise QueueRecoveryCorruptionError(quarantined=quarantined) from None
        except OSError:
            raise QueueRecoveryPersistenceError(
                "Queue recovery journal read or repair failed."
            ) from None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @staticmethod
    def _replay(
        records: list[dict[str, Any]],
        *,
        discarded_torn_tail: bool,
    ) -> RecoverySnapshot:
        jobs: dict[str, dict[str, Any]] = {}
        revisions: dict[str, int] = {}
        global_state: dict[str, Any] | None = None
        global_revision = 0
        epoch = 0
        snapshot_prefix_open = False
        snapshot_last_job_id: str | None = None
        try:
            for index, record in enumerate(records):
                is_snapshot = record["event"] == STATE_SNAPSHOT_EVENT
                is_snapshot_chunk = record["event"] == STATE_SNAPSHOT_CHUNK_EVENT
                if is_snapshot:
                    if index != 0:
                        raise _RecordCorruption
                    jobs.clear()
                    revisions.clear()
                    global_state = None
                    global_revision = 0
                    epoch = record["epoch"]
                    snapshot_prefix_open = True
                    snapshot_last_job_id = None
                elif is_snapshot_chunk:
                    if (
                        index == 0
                        or not snapshot_prefix_open
                        or record["epoch"] != epoch
                    ):
                        raise _RecordCorruption
                else:
                    if record["epoch"] != epoch:
                        raise _RecordCorruption
                    snapshot_prefix_open = False
                for entry in record["jobs"]:
                    job_id = entry["job_id"]
                    revision = entry["revision"]
                    if is_snapshot or is_snapshot_chunk:
                        if (
                            job_id in revisions
                            or (
                                snapshot_last_job_id is not None
                                and job_id <= snapshot_last_job_id
                            )
                        ):
                            raise _RecordCorruption
                        snapshot_last_job_id = job_id
                    elif revision != revisions.get(job_id, 0) + 1:
                        raise _RecordCorruption
                    jobs[job_id] = deepcopy(entry["payload"])
                    revisions[job_id] = revision
                for entry in record["tombstones"]:
                    job_id = entry["job_id"]
                    revision = entry["revision"]
                    if not is_snapshot and revision != revisions.get(job_id, 0) + 1:
                        raise _RecordCorruption
                    jobs.pop(job_id, None)
                    revisions[job_id] = revision
                global_entry = record["global"]
                if global_entry is not None:
                    revision = global_entry["revision"]
                    if not is_snapshot and revision != global_revision + 1:
                        raise _RecordCorruption
                    global_state = deepcopy(global_entry["payload"])
                    global_revision = revision
        except (KeyError, TypeError):
            raise _RecordCorruption from None
        return RecoverySnapshot(
            jobs=jobs,
            job_revisions=revisions,
            epoch=epoch,
            global_state=global_state,
            global_revision=global_revision,
            last_sequence=records[-1]["sequence"] if records else 0,
            event_count=len(records),
            discarded_torn_tail=discarded_torn_tail,
        )

    def recover(self) -> RecoverySnapshot:
        """Strictly replay the latest state and revision fences."""
        with self._serialized():
            records, discarded, _identity = self._scan_locked()
            return self._replay(records, discarded_torn_tail=discarded)

    def _append_record_locked(
        self,
        encoded: bytes,
        expected_identity: tuple[int, int] | None,
    ) -> None:
        before = self._safe_lstat(self.path, missing_ok=True)
        descriptor, info, created = self._open_regular(
            self.path,
            os.O_APPEND | os.O_RDWR,
            create=True,
            claim_journal=True,
        )
        original_size = os.lseek(descriptor, 0, os.SEEK_END)
        try:
            opened_identity = self._identity(info)
            if (
                (expected_identity is None and not created)
                or (
                    expected_identity is not None
                    and opened_identity != expected_identity
                )
            ):
                raise QueueRecoveryPersistenceError(
                    "Queue recovery state changed before append."
                )
            if original_size + len(encoded) > self.max_journal_bytes:
                raise QueueRecoveryValidationError(
                    "Queue recovery journal must be compacted before another event."
                )
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
            if created or before is None:
                _fsync_directory(self.path.parent)
        except OSError:
            try:
                os.ftruncate(descriptor, original_size)
                os.fsync(descriptor)
            except OSError:
                pass
            raise QueueRecoveryPersistenceError(
                "Queue recovery event could not be committed."
            ) from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def commit_state(
        self,
        *,
        jobs: Mapping[str, Mapping[str, Any]] | None = None,
        tombstones: Iterable[str] = (),
        global_state: Mapping[str, Any] | None = None,
        expected_job_revisions: Mapping[str, int] | None = None,
        expected_global_revision: int | None = None,
        expected_epoch: int,
    ) -> CommitReceipt:
        """Atomically commit correlated full snapshots and tombstones.

        ``expected_job_revisions`` must contain exactly every job being updated
        or tombstoned. Use revision ``0`` for an ID never seen in this journal.
        When ``global_state`` is supplied, ``expected_global_revision`` is also
        mandatory. ``expected_epoch`` must come from recovery or the preceding
        receipt. Any mismatch rejects the whole batch before a write.
        """
        if jobs is None:
            jobs = {}
        if expected_job_revisions is None:
            expected_job_revisions = {}
        if type(jobs) is not dict or type(expected_job_revisions) is not dict:
            raise QueueRecoveryValidationError(
                "Queue recovery state batches must be plain mappings."
            )
        if isinstance(tombstones, (str, bytes)):
            raise QueueRecoveryValidationError("Queue recovery tombstones are invalid.")
        if type(tombstones) not in {list, tuple, set, frozenset}:
            raise QueueRecoveryValidationError("Queue recovery tombstones are invalid.")
        try:
            tombstone_ids = list(tombstones)
        except (TypeError, RecursionError):
            raise QueueRecoveryValidationError("Queue recovery tombstones are invalid.") from None

        sanitized_jobs: dict[str, dict[str, Any]] = {}
        for job_id, snapshot in jobs.items():
            durable_id = _validate_job_id(job_id)
            payload = self._sanitize(snapshot)
            embedded_id = payload.get("id")
            if embedded_id is not None and embedded_id != durable_id:
                raise QueueRecoveryValidationError(
                    "Queue recovery job identity does not match its snapshot."
                )
            sanitized_jobs[durable_id] = payload
        clean_tombstones = {_validate_job_id(job_id) for job_id in tombstone_ids}
        if len(clean_tombstones) != len(tombstone_ids):
            raise QueueRecoveryValidationError("Queue recovery tombstones are invalid.")
        changed_ids = set(sanitized_jobs).union(clean_tombstones)
        if set(expected_job_revisions) != changed_ids:
            raise QueueRecoveryValidationError(
                "Queue recovery expected revisions do not match the batch."
            )
        expected = {
            _validate_job_id(job_id): _validate_revision(revision)
            for job_id, revision in expected_job_revisions.items()
        }
        if set(sanitized_jobs).intersection(clean_tombstones):
            raise QueueRecoveryValidationError(
                "Queue recovery jobs cannot be updated and tombstoned together."
            )
        clean_global = None if global_state is None else self._sanitize(global_state)
        if clean_global is not None:
            expected_global = _validate_revision(expected_global_revision)
        elif expected_global_revision is not None:
            raise QueueRecoveryValidationError(
                "Queue recovery global revision has no matching state update."
            )
        else:
            expected_global = None
        if not changed_ids and clean_global is None:
            raise QueueRecoveryValidationError("Queue recovery state batch is empty.")
        clean_expected_epoch = _validate_epoch(expected_epoch)

        with self._serialized():
            records, _discarded, journal_identity = self._scan_locked()
            recovered = self._replay(records, discarded_torn_tail=False)
            if recovered.epoch != clean_expected_epoch:
                raise QueueRecoveryValidationError(
                    "Queue recovery state changed before this commit."
                )
            if len(records) >= self.max_events:
                raise QueueRecoveryValidationError(
                    "Queue recovery journal must be compacted before another event."
                )
            for job_id in changed_ids:
                if recovered.job_revisions.get(job_id, 0) != expected[job_id]:
                    raise QueueRecoveryValidationError(
                        "Queue recovery state changed before this commit."
                    )
            if (
                clean_global is not None
                and recovered.global_revision != expected_global
            ):
                raise QueueRecoveryValidationError(
                    "Queue recovery state changed before this commit."
                )

            job_entries = [
                {
                    "job_id": job_id,
                    "payload": sanitized_jobs[job_id],
                    "revision": expected[job_id] + 1,
                }
                for job_id in sorted(sanitized_jobs)
            ]
            tombstone_entries = [
                {"job_id": job_id, "revision": expected[job_id] + 1}
                for job_id in sorted(clean_tombstones)
            ]
            global_entry = (
                {
                    "payload": clean_global,
                    "revision": expected_global + 1,
                }
                if clean_global is not None and expected_global is not None
                else None
            )
            sequence = len(records) + 1
            encoded = self._event_bytes(
                sequence=sequence,
                epoch=recovered.epoch,
                event=STATE_COMMIT_EVENT,
                jobs=job_entries,
                tombstones=tombstone_entries,
                global_entry=global_entry,
            )
            self._append_record_locked(encoded, journal_identity)
            return CommitReceipt(
                sequence=sequence,
                epoch=recovered.epoch,
                job_revisions={
                    job_id: expected[job_id] + 1 for job_id in sorted(changed_ids)
                },
                global_revision=(
                    expected_global + 1
                    if expected_global is not None
                    else recovered.global_revision
                ),
            )

    def commit_job(
        self,
        job_id: str,
        snapshot: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_epoch: int,
    ) -> CommitReceipt:
        """Commit one full job snapshot with mandatory stale-writer fencing."""
        return self.commit_state(
            jobs={job_id: snapshot},
            expected_job_revisions={job_id: expected_revision},
            expected_epoch=expected_epoch,
        )

    def commit_global(
        self,
        snapshot: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_epoch: int,
    ) -> CommitReceipt:
        """Commit one full global queue-control snapshot with revision fencing."""
        return self.commit_state(
            global_state=snapshot,
            expected_global_revision=expected_revision,
            expected_epoch=expected_epoch,
        )

    def tombstone(
        self,
        job_id: str,
        *,
        expected_revision: int,
        expected_epoch: int,
    ) -> CommitReceipt:
        """Remove a job while advancing its durable anti-resurrection fence."""
        return self.commit_state(
            tombstones=(job_id,),
            expected_job_revisions={job_id: expected_revision},
            expected_epoch=expected_epoch,
        )

    def compact(
        self,
        *,
        drop_terminal: bool = True,
        terminal_statuses: frozenset[str] = TERMINAL_JOB_STATUSES,
        replacement_jobs: Mapping[str, Mapping[str, Any]] | None = None,
        replacement_global_state: Mapping[str, Any] | None | object = _COMPACT_UNSET,
        expected_job_revisions: Mapping[str, int] | None = None,
        expected_global_revision: int | None = None,
        expected_epoch: int | None = None,
    ) -> RecoverySnapshot:
        """Atomically replace history with a new-epoch bounded snapshot set.

        A strict caller may provide complete sanitized replacement snapshots.
        Their IDs, revisions, global-state presence, and epoch must exactly
        match recovered state, so compaction can remove unsafe generic fields
        without an append or any opportunity to add/drop durable identities.
        Replacement revisions advance once inside the new epoch, making the
        sanitized rewrite visible to every subsequent revision fence.
        Omitting ``replacement_jobs`` preserves the legacy replay-as-is mode.
        """
        normalized_terminal = frozenset(status.casefold() for status in terminal_statuses)
        with self._serialized():
            records, _discarded, journal_identity = self._scan_locked()
            recovered = self._replay(records, discarded_torn_tail=False)
            if replacement_jobs is None:
                if (
                    replacement_global_state is not _COMPACT_UNSET
                    or expected_job_revisions is not None
                    or expected_global_revision is not None
                    or expected_epoch is not None
                ):
                    raise QueueRecoveryValidationError(
                        "Queue recovery compaction replacement arguments are incomplete."
                    )
                source_jobs = recovered.jobs
                source_global = recovered.global_state
                replacement_revision_advance = 0
            else:
                if (
                    type(replacement_jobs) is not dict
                    or type(expected_job_revisions) is not dict
                    or replacement_global_state is _COMPACT_UNSET
                ):
                    raise QueueRecoveryValidationError(
                        "Queue recovery compaction replacement arguments are incomplete."
                    )
                clean_epoch = _validate_epoch(expected_epoch)
                clean_global_revision = _validate_revision(expected_global_revision)
                if (
                    clean_epoch != recovered.epoch
                    or clean_global_revision != recovered.global_revision
                    or set(replacement_jobs) != set(recovered.jobs)
                    or set(expected_job_revisions) != set(recovered.job_revisions)
                ):
                    raise QueueRecoveryValidationError(
                        "Queue recovery state changed before compaction."
                    )
                clean_revisions = {
                    _validate_job_id(job_id): _validate_revision(revision)
                    for job_id, revision in expected_job_revisions.items()
                }
                if clean_revisions != recovered.job_revisions:
                    raise QueueRecoveryValidationError(
                        "Queue recovery state changed before compaction."
                    )
                source_jobs: dict[str, dict[str, Any]] = {}
                for job_id, snapshot in replacement_jobs.items():
                    durable_id = _validate_job_id(job_id)
                    payload = self._sanitize(snapshot)
                    embedded_id = payload.get("id")
                    if embedded_id is not None and embedded_id != durable_id:
                        raise QueueRecoveryValidationError(
                            "Queue recovery job identity does not match its snapshot."
                        )
                    source_jobs[durable_id] = payload
                if recovered.global_state is None:
                    if replacement_global_state is not None:
                        raise QueueRecoveryValidationError(
                            "Queue recovery global state presence changed before compaction."
                        )
                    source_global = None
                else:
                    if not isinstance(replacement_global_state, Mapping):
                        raise QueueRecoveryValidationError(
                            "Queue recovery global state presence changed before compaction."
                        )
                    source_global = self._sanitize(replacement_global_state)
                replacement_revision_advance = 1
            retained_jobs: dict[str, dict[str, Any]] = {}
            for job_id, snapshot in source_jobs.items():
                if (
                    drop_terminal
                    and type(snapshot.get("status")) is str
                    and snapshot["status"].casefold() in normalized_terminal
                ):
                    continue
                retained_jobs[job_id] = snapshot
            if len(retained_jobs) > self.max_compacted_jobs:
                raise QueueRecoveryValidationError(
                    "Queue recovery active-job count exceeds the compaction limit."
                )

            jobs = [
                {
                    "job_id": job_id,
                    "payload": retained_jobs[job_id],
                    "revision": (
                        recovered.job_revisions[job_id]
                        + replacement_revision_advance
                    ),
                }
                for job_id in sorted(retained_jobs)
            ]
            global_entry = (
                {
                    "payload": source_global,
                    "revision": (
                        recovered.global_revision
                        + replacement_revision_advance
                    ),
                }
                if source_global is not None
                else None
            )
            new_epoch = recovered.epoch + 1
            replacement_records: list[bytes] = []
            sequence = 1
            current_jobs: list[dict[str, Any]] = []

            def encode_snapshot(candidate_jobs: list[dict[str, Any]]) -> bytes:
                first = sequence == 1
                return self._event_bytes(
                    sequence=sequence,
                    epoch=new_epoch,
                    event=(
                        STATE_SNAPSHOT_EVENT
                        if first else STATE_SNAPSHOT_CHUNK_EVENT
                    ),
                    jobs=candidate_jobs,
                    tombstones=[],
                    global_entry=global_entry if first else None,
                )

            for entry in jobs:
                try:
                    encode_snapshot(current_jobs + [entry])
                except QueueRecoveryValidationError:
                    # The first record may legitimately contain only global
                    # control state. Every later chunk must contain at least
                    # one complete job, and a job that cannot fit alone is an
                    # unrecoverable caller sizing/configuration error.
                    replacement_records.append(encode_snapshot(current_jobs))
                    sequence += 1
                    current_jobs = [entry]
                    encode_snapshot(current_jobs)
                else:
                    current_jobs.append(entry)
            replacement_records.append(encode_snapshot(current_jobs))
            replacement = b"".join(replacement_records)
            if (
                len(replacement) > self.max_journal_bytes
                or len(replacement_records) > self.max_events
            ):
                raise QueueRecoveryValidationError(
                    "Queue recovery compacted state exceeds its durable limit."
                )

            descriptor = -1
            temporary = ""
            replaced = False
            try:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    dir=self.path.parent,
                )
                _write_all(descriptor, replacement)
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = -1
                current = self._safe_lstat(self.path, missing_ok=True)
                if current is not None:
                    self._validate_regular_single_link(current)
                if (
                    (journal_identity is None and current is not None)
                    or (
                        journal_identity is not None
                        and (
                            current is None
                            or self._identity(current) != journal_identity
                        )
                    )
                ):
                    raise ValueError
                os.replace(temporary, self.path)
                replaced = True
                _fsync_directory(self.path.parent)
            except (OSError, ValueError):
                raise QueueRecoveryPersistenceError(
                    "Queue recovery compaction could not be committed."
                ) from None
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                if temporary and not replaced:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass

            compacted_records, discarded, _identity = self._scan_locked()
            return self._replay(
                compacted_records,
                discarded_torn_tail=discarded,
            )


__all__ = [
    "CommitReceipt",
    "QueueRecoveryCorruptionError",
    "QueueRecoveryError",
    "QueueRecoveryJournal",
    "QueueRecoveryPersistenceError",
    "QueueRecoveryValidationError",
    "RecoverySnapshot",
    "SCHEMA_VERSION",
    "STATE_COMMIT_EVENT",
    "STATE_SNAPSHOT_CHUNK_EVENT",
    "STATE_SNAPSHOT_EVENT",
    "TERMINAL_JOB_STATUSES",
]
