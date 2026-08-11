"""Durable, content-private in-app message thread primitives.

This module owns persistence and participant-scoped projections only.  It does
not inspect message content, read attachment sources, invoke a model or scanner,
send email, or publish queue jobs.  Callers supply already-authorized opaque
attachment and object-grant descriptors.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.account_auth import (
    AccountAuthError,
    _AccountStoreLock,
    _fsync_directory,
    _portable_owner_matches,
    _tighten_windows_acl,
)

PRIVATE_MESSAGE_STORE_VERSION = 1
MAX_STORE_BYTES = 8 * 1024 * 1024
MAX_THREADS = 4_096
MAX_THREADS_PER_CREATOR = 256
MAX_MESSAGES = 50_000
MAX_MESSAGES_PER_CREATOR = 4_096
MAX_EVENTS_PER_THREAD = 4_096
MAX_MUTATIONS = 50_000
MAX_MUTATIONS_PER_ACTOR = 10_000
MAX_ATTACHMENTS_PER_MESSAGE = 32
MAX_ATTACHMENT_BYTES = 512 * 1024 * 1024
MAX_GRANTS_PER_MESSAGE = 64
MAX_SUBJECT_BYTES = 512
MAX_BODY_BYTES = 128 * 1024

THREAD_STAGES = frozenset({
    "draft_saved",
    "waiting_for_model",
    "triaging",
    "scanning_attachments",
    "queued_for_delivery",
    "delivered",
    "refused",
    "failed",
    "cancelled",
})
TRIAGE_STATES = frozenset({
    "not_started", "waiting_for_model", "triaging", "complete", "failed",
})
SCAN_STATES = frozenset({"not_started", "scanning", "clean", "failed"})
DELIVERY_STATES = frozenset({
    "not_started", "queued", "delivered", "refused", "failed", "cancelled",
})
REASON_CODES = frozenset({
    "none",
    "owner_unavailable",
    "model_unavailable",
    "attachment_scan_failed",
    "delivery_unavailable",
    "delivery_refused",
    "cancelled_by_creator",
    "cancelled_by_owner",
    "retry_requested",
    "capacity_limited",
    "transient_failure",
})
MESSAGE_KINDS = frozenset({"created", "reply"})
EVENT_KINDS = frozenset({
    *MESSAGE_KINDS,
    "read",
    "archive",
    "mute",
    "cancel",
    "retry",
    "stage_update",
})
GRANT_OBJECT_TYPES = frozenset({
    "output", "component", "reference", "project", "job", "asset",
})

_ACCOUNT_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{7,127}\Z")
_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_THREAD_ID_RE = re.compile(r"msg_[0-9a-f]{32}\Z")
_EVENT_ID_RE = re.compile(r"evt_[0-9a-f]{32}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_GENESIS_HMAC = "0" * 64
_STORE_SEAL_DOMAIN = b"maestro-private-message-store-v1\0"
_ANCHOR_SEAL_DOMAIN = b"maestro-private-message-anchor-v1\0"
_IDENTITY_DOMAIN = b"maestro-private-message-account-v1\0"
_THREAD_DOMAIN = b"maestro-private-message-thread-v1\0"
_MUTATION_DOMAIN = b"maestro-private-message-mutation-v1\0"
_PAYLOAD_DOMAIN = b"maestro-private-message-payload-v1\0"
_EVENT_DOMAIN = b"maestro-private-message-event-v1\0"
_LOCK_ANCHOR_SLOT_BYTES = 512
_LOCK_ANCHOR_SLOT_OFFSETS = (4_096, 8_192)
_LOCK_ANCHOR_SLOT_COUNT = len(_LOCK_ANCHOR_SLOT_OFFSETS)
_LOCK_ANCHOR_BYTES = _LOCK_ANCHOR_SLOT_OFFSETS[-1] + _LOCK_ANCHOR_SLOT_BYTES


class PrivateMessageError(ValueError):
    """Content-free domain error safe for bounded UI translation."""

    def __init__(self, code: str, message: str = "Private message operation failed."):
        super().__init__(message)
        self.code = code


class PrivateMessageValidationError(PrivateMessageError):
    def __init__(self, code: str = "invalid_request"):
        super().__init__(code, "Private message request is invalid.")


class PrivateMessageAuthorizationError(PrivateMessageError):
    def __init__(self):
        super().__init__("not_authorized", "Private message access is unavailable.")


class PrivateMessageOwnerUnavailableError(PrivateMessageError):
    def __init__(self):
        super().__init__("owner_unavailable", "Private messaging is unavailable.")


class PrivateMessageConflictError(PrivateMessageError):
    def __init__(self, code: str = "request_conflict"):
        super().__init__(code, "Private message operation conflicts with current state.")


class PrivateMessageCapacityError(PrivateMessageError):
    def __init__(self):
        super().__init__("capacity_limited", "Private message capacity is unavailable.")


class PrivateMessageCorruptionError(PrivateMessageError):
    def __init__(self):
        super().__init__("store_unavailable", "Private message state is unavailable.")


@dataclass(frozen=True, repr=False)
class AttachmentDescriptor:
    attachment_id: str
    byte_count: int
    sha256: str

    def __repr__(self) -> str:
        return f"<AttachmentDescriptor byte_count={self.byte_count}>"


@dataclass(frozen=True, repr=False)
class GrantDescriptor:
    grant_id: str
    object_type: str
    object_id: str
    revision_id: str

    def __repr__(self) -> str:
        return f"<GrantDescriptor object_type={self.object_type!r}>"


@dataclass(frozen=True)
class MutationReceipt:
    thread_id: str
    revision: int
    event_sequence: int
    operation: str
    stage: str


@dataclass(frozen=True)
class ThreadCard:
    thread_id: str
    revision: int
    stage: str
    triage_state: str
    scan_state: str
    delivery_state: str
    reason: str
    progress_current: int
    progress_total: int
    created_at: float
    updated_at: float
    attachment_count: int
    attachment_bytes: int
    unread_count: int
    unread_bump_order: int
    archived: bool
    muted: bool
    can_retry: bool
    can_cancel: bool


@dataclass(frozen=True, repr=False)
class MessageView:
    sequence: int
    event_id: str
    sender_role: str
    occurred_at: float
    body: str
    attachments: tuple[AttachmentDescriptor, ...]
    grants: tuple[GrantDescriptor, ...]

    def __repr__(self) -> str:
        return f"<MessageView sequence={self.sequence} event_id={self.event_id!r}>"


@dataclass(frozen=True, repr=False)
class ThreadDetail:
    card: ThreadCard
    participant_role: str
    subject: str
    messages: tuple[MessageView, ...]

    def __repr__(self) -> str:
        return (
            f"<ThreadDetail thread_id={self.card.thread_id!r} "
            f"revision={self.card.revision}>"
        )


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, ValueError):
        raise PrivateMessageValidationError() from None


def _finite_number(value: Any) -> bool:
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate_identifier(value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PrivateMessageValidationError()
    return value


def _validate_text(value: Any, *, maximum_bytes: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise PrivateMessageValidationError()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise PrivateMessageValidationError() from None
    if size > maximum_bytes or (not allow_empty and size == 0):
        raise PrivateMessageValidationError()
    return value


def _normalize_attachments(
    values: Sequence[AttachmentDescriptor | Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise PrivateMessageValidationError()
    if len(values) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise PrivateMessageCapacityError()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for value in values:
        record = (
            {
                "attachment_id": value.attachment_id,
                "byte_count": value.byte_count,
                "sha256": value.sha256,
            }
            if isinstance(value, AttachmentDescriptor)
            else dict(value) if isinstance(value, Mapping) else None
        )
        if record is None or set(record) != {"attachment_id", "byte_count", "sha256"}:
            raise PrivateMessageValidationError()
        attachment_id = _validate_identifier(record["attachment_id"], _OPAQUE_ID_RE)
        byte_count = record["byte_count"]
        digest = record["sha256"]
        if (
            attachment_id in seen
            or type(byte_count) is not int
            or not 0 <= byte_count <= MAX_ATTACHMENT_BYTES
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise PrivateMessageValidationError()
        seen.add(attachment_id)
        total += byte_count
        if total > MAX_ATTACHMENT_BYTES:
            raise PrivateMessageCapacityError()
        result.append({
            "attachment_id": attachment_id,
            "byte_count": byte_count,
            "sha256": digest,
        })
    return tuple(result)


def _normalize_grants(
    values: Sequence[GrantDescriptor | Mapping[str, Any]] | None,
) -> tuple[dict[str, str], ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise PrivateMessageValidationError()
    if len(values) > MAX_GRANTS_PER_MESSAGE:
        raise PrivateMessageCapacityError()
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        record = (
            {
                "grant_id": value.grant_id,
                "object_type": value.object_type,
                "object_id": value.object_id,
                "revision_id": value.revision_id,
            }
            if isinstance(value, GrantDescriptor)
            else dict(value) if isinstance(value, Mapping) else None
        )
        if record is None or set(record) != {
            "grant_id", "object_type", "object_id", "revision_id",
        }:
            raise PrivateMessageValidationError()
        grant_id = _validate_identifier(record["grant_id"], _OPAQUE_ID_RE)
        object_type = record["object_type"]
        object_id = _validate_identifier(record["object_id"], _OPAQUE_ID_RE)
        revision_id = _validate_identifier(record["revision_id"], _OPAQUE_ID_RE)
        if grant_id in seen or object_type not in GRANT_OBJECT_TYPES:
            raise PrivateMessageValidationError()
        seen.add(grant_id)
        result.append({
            "grant_id": grant_id,
            "object_type": object_type,
            "object_id": object_id,
            "revision_id": revision_id,
        })
    return tuple(result)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrivateMessageCorruptionError()
        result[key] = value
    return result


class PrivateMessageStore:
    """Atomic HMAC-sealed store for account-bound in-app conversations."""

    CANONICAL_PATH = (
        Path(__file__).resolve().parents[1]
        / "storage" / "private_messages" / "threads.json"
    )

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        integrity_key: bytes | str,
        owner_account_id: str,
        owner_enabled: bool = True,
        allow_test_path: bool = False,
        clock=time.time,
        max_store_bytes: int = MAX_STORE_BYTES,
        max_threads: int = MAX_THREADS,
        max_threads_per_creator: int = MAX_THREADS_PER_CREATOR,
        max_messages: int = MAX_MESSAGES,
        max_messages_per_creator: int = MAX_MESSAGES_PER_CREATOR,
        max_events_per_thread: int = MAX_EVENTS_PER_THREAD,
        max_mutations: int = MAX_MUTATIONS,
        max_mutations_per_actor: int = MAX_MUTATIONS_PER_ACTOR,
    ) -> None:
        candidate = Path(os.path.abspath(os.fspath(path or self.CANONICAL_PATH)))
        if candidate != self.CANONICAL_PATH:
            try:
                candidate.resolve(strict=False).relative_to(
                    Path(tempfile.gettempdir()).resolve(),
                )
            except (OSError, RuntimeError, ValueError):
                raise PrivateMessageValidationError() from None
            if not allow_test_path:
                raise PrivateMessageValidationError()
        try:
            secret = (
                integrity_key.encode("utf-8")
                if isinstance(integrity_key, str)
                else integrity_key
            )
        except UnicodeEncodeError:
            raise PrivateMessageValidationError() from None
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise PrivateMessageValidationError()
        self.path = candidate
        self.anchor_path = Path(str(candidate) + ".anchor")
        self._secret = secret
        self._owner_account_id = _validate_identifier(owner_account_id, _ACCOUNT_ID_RE)
        if type(owner_enabled) is not bool:
            raise PrivateMessageValidationError()
        self._owner_enabled = owner_enabled
        self._clock = clock
        self._max_store_bytes = self._bounded_limit(
            max_store_bytes, maximum=MAX_STORE_BYTES, minimum=1_024,
        )
        self._max_threads = self._bounded_limit(max_threads, maximum=MAX_THREADS)
        self._max_threads_per_creator = self._bounded_limit(
            max_threads_per_creator, maximum=MAX_THREADS_PER_CREATOR,
        )
        self._max_messages = self._bounded_limit(max_messages, maximum=MAX_MESSAGES)
        self._max_messages_per_creator = self._bounded_limit(
            max_messages_per_creator, maximum=MAX_MESSAGES_PER_CREATOR,
        )
        self._max_events_per_thread = self._bounded_limit(
            max_events_per_thread, maximum=MAX_EVENTS_PER_THREAD,
        )
        self._max_mutations = self._bounded_limit(
            max_mutations, maximum=MAX_MUTATIONS,
        )
        self._max_mutations_per_actor = self._bounded_limit(
            max_mutations_per_actor, maximum=MAX_MUTATIONS_PER_ACTOR,
        )
        self._owner_key = self._account_key(self._owner_account_id)
        self._lock_path = Path(str(self.path) + ".lock")
        self._lock = _AccountStoreLock(str(self.path))
        self._lock_anchor: dict[str, Any] | None = None
        self._lock_anchor_damaged = False
        self._observed_generation = 0
        self._observed_seal = _GENESIS_HMAC

    @staticmethod
    def _bounded_limit(value: Any, *, maximum: int, minimum: int = 1) -> int:
        if type(value) is not int or not minimum <= value <= maximum:
            raise PrivateMessageValidationError()
        return value

    def _digest(self, domain: bytes, *parts: bytes) -> str:
        return hmac.new(self._secret, domain + b"\0".join(parts), hashlib.sha256).hexdigest()

    def _account_key(self, account_id: str) -> str:
        selected = _validate_identifier(account_id, _ACCOUNT_ID_RE)
        return self._digest(_IDENTITY_DOMAIN, selected.encode("ascii"))

    def _seal(self, unsigned: Mapping[str, Any]) -> str:
        return self._digest(_STORE_SEAL_DOMAIN, _canonical(unsigned))

    def _anchor_seal(self, unsigned: Mapping[str, Any]) -> str:
        return self._digest(_ANCHOR_SEAL_DOMAIN, _canonical(unsigned))

    def _event_hmac(self, unsigned: Mapping[str, Any]) -> str:
        return self._digest(_EVENT_DOMAIN, _canonical(unsigned))

    def _thread_id(self, creator_id: str, request_id: str) -> str:
        digest = self._digest(
            _THREAD_DOMAIN,
            creator_id.encode("ascii"),
            request_id.encode("ascii"),
        )
        return f"msg_{digest[:32]}"

    def _mutation_key(
        self,
        *,
        actor_id: str,
        thread_id: str,
        operation: str,
        request_id: str,
    ) -> str:
        return self._digest(
            _MUTATION_DOMAIN,
            actor_id.encode("ascii"),
            thread_id.encode("ascii"),
            operation.encode("ascii"),
            request_id.encode("ascii"),
        )

    def _payload_digest(self, payload: Mapping[str, Any]) -> str:
        return self._digest(_PAYLOAD_DOMAIN, _canonical(payload))

    @staticmethod
    def _empty(owner_key: str) -> dict[str, Any]:
        return {
            "version": PRIVATE_MESSAGE_STORE_VERSION,
            "generation": 0,
            "clock_high_water": 0.0,
            "owner_key": owner_key,
            "threads": [],
            "mutations": [],
        }

    def _assert_private_directory(self) -> None:
        parent = self.path.parent
        self._assert_no_symlink_components(parent)
        try:
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            info = parent.lstat()
        except OSError:
            raise PrivateMessageCorruptionError() from None
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or not _portable_owner_matches(info)
            or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o700)
        ):
            raise PrivateMessageCorruptionError()
        if os.name == "nt":
            try:
                _tighten_windows_acl(str(parent), directory=True)
            except AccountAuthError:
                raise PrivateMessageCorruptionError() from None
        self._assert_no_symlink_components(parent)

    @staticmethod
    def _assert_no_symlink_components(path: Path) -> None:
        parts = path.parts
        if not parts:
            raise PrivateMessageCorruptionError()
        current = Path(parts[0])
        for part in parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            except OSError:
                raise PrivateMessageCorruptionError() from None
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise PrivateMessageCorruptionError()

    @staticmethod
    def _assert_safe_regular(path: Path, *, expected_mode: int) -> None:
        try:
            before = path.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise PrivateMessageCorruptionError() from None
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or not _portable_owner_matches(before)
            or (os.name != "nt" and stat.S_IMODE(before.st_mode) != expected_mode)
        ):
            raise PrivateMessageCorruptionError()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._assert_private_directory()
        self._assert_safe_regular(self._lock_path, expected_mode=0o600)
        self._assert_safe_regular(self.anchor_path, expected_mode=0o600)
        try:
            with self._lock:
                self._assert_safe_regular(self._lock_path, expected_mode=0o600)
                self._assert_safe_regular(self.anchor_path, expected_mode=0o600)
                self._lock_anchor_damaged = False
                self._lock_anchor = self._read_lock_anchor(
                    allow_initialize=self._state_files_absent(),
                )
                try:
                    yield
                finally:
                    self._lock_anchor = None
                    self._lock_anchor_damaged = False
        except PrivateMessageError:
            raise
        except AccountAuthError:
            raise PrivateMessageCorruptionError() from None

    def _state_files_absent(self) -> bool:
        for candidate in (self.path, self.anchor_path):
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise PrivateMessageCorruptionError() from None
            return False
        return True

    def _read_bytes(self) -> bytes | None:
        try:
            before = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise PrivateMessageCorruptionError() from None
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise PrivateMessageCorruptionError()
        try:
            descriptor = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError:
            raise PrivateMessageCorruptionError() from None
        try:
            info = os.fstat(descriptor)
            current = self.path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or not os.path.samestat(info, current)
                or info.st_nlink != 1
                or not _portable_owner_matches(info)
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600)
                or not 0 < info.st_size <= self._max_store_bytes
            ):
                raise PrivateMessageCorruptionError()
            if os.name == "nt":
                _tighten_windows_acl(str(self.path), directory=False)
            chunks: list[bytes] = []
            remaining = info.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise PrivateMessageCorruptionError()
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise PrivateMessageCorruptionError()
            return b"".join(chunks)
        except PrivateMessageError:
            raise
        except (AccountAuthError, OSError):
            raise PrivateMessageCorruptionError() from None
        finally:
            os.close(descriptor)

    def _parse_anchor(self, raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise PrivateMessageCorruptionError() from None
        if not isinstance(value, dict) or set(value) != {
            "version", "generation", "store_seal", "seal",
        }:
            raise PrivateMessageCorruptionError()
        unsigned = {key: item for key, item in value.items() if key != "seal"}
        if (
            value["version"] != PRIVATE_MESSAGE_STORE_VERSION
            or type(value["generation"]) is not int
            or not 0 <= value["generation"] <= (1 << 63) - 1
            or not isinstance(value["store_seal"], str)
            or _DIGEST_RE.fullmatch(value["store_seal"]) is None
            or not isinstance(value["seal"], str)
            or _DIGEST_RE.fullmatch(value["seal"]) is None
            or not hmac.compare_digest(value["seal"], self._anchor_seal(unsigned))
        ):
            raise PrivateMessageCorruptionError()
        return unsigned

    def _anchor_bytes(self, generation: int, store_seal: str) -> bytes:
        unsigned = {
            "version": PRIVATE_MESSAGE_STORE_VERSION,
            "generation": generation,
            "store_seal": store_seal,
        }
        return _canonical({**unsigned, "seal": self._anchor_seal(unsigned)})

    def _read_lock_anchor(self, *, allow_initialize: bool) -> dict[str, Any]:
        descriptor = self._lock._descriptor
        if descriptor is None:
            raise PrivateMessageCorruptionError()
        try:
            info = os.fstat(descriptor)
            named = self._lock_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not _portable_owner_matches(info)
                or not os.path.samestat(info, named)
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600)
            ):
                raise PrivateMessageCorruptionError()
            os.lseek(descriptor, 0, os.SEEK_SET)
            raw = os.read(descriptor, _LOCK_ANCHOR_BYTES + 1)
            if len(raw) > _LOCK_ANCHOR_BYTES or os.read(descriptor, 1):
                raise PrivateMessageCorruptionError()
        except OSError:
            raise PrivateMessageCorruptionError() from None
        if raw in {b"", b"\0"}:
            if not allow_initialize:
                raise PrivateMessageCorruptionError()
            self._write_lock_anchor(0, _GENESIS_HMAC)
            return {"generation": 0, "store_seal": _GENESIS_HMAC}
        if raw[:1] != b"\0":
            raise PrivateMessageCorruptionError()
        padded = raw.ljust(_LOCK_ANCHOR_BYTES, b"\0")
        reserved_ranges = (
            (1, _LOCK_ANCHOR_SLOT_OFFSETS[0]),
            (
                _LOCK_ANCHOR_SLOT_OFFSETS[0] + _LOCK_ANCHOR_SLOT_BYTES,
                _LOCK_ANCHOR_SLOT_OFFSETS[1],
            ),
        )
        if any(any(padded[start:end]) for start, end in reserved_ranges):
            raise PrivateMessageCorruptionError()
        anchors: list[dict[str, Any]] = []
        damaged = False
        for start in _LOCK_ANCHOR_SLOT_OFFSETS:
            slot = padded[start:start + _LOCK_ANCHOR_SLOT_BYTES]
            encoded = slot.rstrip(b"\0")
            if not encoded:
                continue
            try:
                anchors.append(self._parse_anchor(encoded))
            except PrivateMessageCorruptionError:
                damaged = True
        if not anchors:
            raise PrivateMessageCorruptionError()
        selected = max(anchors, key=lambda value: value["generation"])
        if any(
            value["generation"] == selected["generation"]
            and not hmac.compare_digest(
                value["store_seal"], selected["store_seal"],
            )
            for value in anchors
        ):
            raise PrivateMessageCorruptionError()
        self._lock_anchor_damaged = damaged
        return selected

    def _write_lock_anchor(self, generation: int, store_seal: str) -> None:
        descriptor = self._lock._descriptor
        if descriptor is None:
            raise PrivateMessageCorruptionError()
        encoded = self._anchor_bytes(generation, store_seal)
        if len(encoded) > _LOCK_ANCHOR_SLOT_BYTES:
            raise PrivateMessageCorruptionError()
        slot = encoded.ljust(_LOCK_ANCHOR_SLOT_BYTES, b"\0")
        try:
            info = os.fstat(descriptor)
            named = self._lock_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not _portable_owner_matches(info)
                or not os.path.samestat(info, named)
            ):
                raise PrivateMessageCorruptionError()
            if info.st_size == 0:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.write(descriptor, b"\0") != 1:
                    raise OSError("short lock-anchor header write")
            elif info.st_size < 1:
                raise PrivateMessageCorruptionError()
            os.lseek(
                descriptor,
                _LOCK_ANCHOR_SLOT_OFFSETS[generation % _LOCK_ANCHOR_SLOT_COUNT],
                os.SEEK_SET,
            )
            view = memoryview(slot)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short lock-anchor write")
                view = view[written:]
            os.fsync(descriptor)
            _fsync_directory(str(self.path.parent))
        except PrivateMessageError:
            raise
        except OSError:
            raise PrivateMessageCorruptionError() from None
        self._lock_anchor = {"generation": generation, "store_seal": store_seal}
        self._lock_anchor_damaged = False

    def _read_anchor(self) -> dict[str, Any] | None:
        self._assert_safe_regular(self.anchor_path, expected_mode=0o600)
        try:
            descriptor = os.open(
                self.anchor_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise PrivateMessageCorruptionError() from None
        try:
            info = os.fstat(descriptor)
            current = self.anchor_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or not os.path.samestat(info, current)
                or info.st_nlink != 1
                or not _portable_owner_matches(info)
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600)
                or not 0 < info.st_size <= 1_024
            ):
                raise PrivateMessageCorruptionError()
            if os.name == "nt":
                _tighten_windows_acl(str(self.anchor_path), directory=False)
            raw = os.read(descriptor, 1_025)
            if len(raw) != info.st_size or len(raw) > 1_024 or os.read(descriptor, 1):
                raise PrivateMessageCorruptionError()
        except PrivateMessageError:
            raise
        except OSError:
            raise PrivateMessageCorruptionError() from None
        finally:
            os.close(descriptor)
        return self._parse_anchor(raw)

    def _read_private_target_for_rollback(self, target: Path) -> bytes | None:
        maximum = 1_024 if target == self.anchor_path else self._max_store_bytes
        self._assert_safe_regular(target, expected_mode=0o600)
        try:
            descriptor = os.open(
                target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise PrivateMessageCorruptionError() from None
        try:
            info = os.fstat(descriptor)
            named = target.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not _portable_owner_matches(info)
                or not os.path.samestat(info, named)
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600)
                or not 0 < info.st_size <= maximum
            ):
                raise PrivateMessageCorruptionError()
            chunks: list[bytes] = []
            remaining = info.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise PrivateMessageCorruptionError()
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise PrivateMessageCorruptionError()
            return b"".join(chunks)
        except PrivateMessageError:
            raise
        except OSError:
            raise PrivateMessageCorruptionError() from None
        finally:
            os.close(descriptor)

    @staticmethod
    def _restore_failed_publication(
        target: Path,
        descriptor: int,
        previous: bytes | None,
    ) -> None:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            if previous is not None:
                view = memoryview(previous)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short private-message restore")
                    view = view[written:]
            os.fsync(descriptor)
            try:
                named = target.lstat()
            except FileNotFoundError:
                named = None
            if previous is None and named is not None and os.path.samestat(
                os.fstat(descriptor), named,
            ):
                target.unlink()
            _fsync_directory(str(target.parent))
        except (AccountAuthError, OSError):
            raise PrivateMessageCorruptionError() from None

    def _publish_private_file(self, target: Path, encoded: bytes) -> None:
        temporary = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
        previous = self._read_private_target_for_rollback(target)
        descriptor: int | None = None
        verification: int | None = None
        published = False
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
            )
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short private-message write")
                view = view[written:]
            os.fsync(descriptor)
            written_info = os.fstat(descriptor)
            os.close(descriptor)
            descriptor = None
            verification = os.open(
                temporary, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            )
            verified_info = os.fstat(verification)
            named = temporary.lstat()
            if (
                not stat.S_ISREG(verified_info.st_mode)
                or verified_info.st_nlink != 1
                or not _portable_owner_matches(verified_info)
                or (os.name != "nt" and stat.S_IMODE(verified_info.st_mode) != 0o600)
                or not os.path.samestat(written_info, verified_info)
                or not os.path.samestat(verified_info, named)
            ):
                raise PrivateMessageCorruptionError()
            os.replace(temporary, target)
            published = True
            publication_info = target.lstat()
            if (
                not stat.S_ISREG(publication_info.st_mode)
                or publication_info.st_nlink != 1
                or not _portable_owner_matches(publication_info)
                or not os.path.samestat(verified_info, publication_info)
                or (
                    os.name != "nt"
                    and stat.S_IMODE(publication_info.st_mode) != 0o600
                )
            ):
                raise PrivateMessageCorruptionError()
            if os.name == "nt":
                _tighten_windows_acl(str(target), directory=False)
            _fsync_directory(str(target.parent))
        except PrivateMessageError:
            if published and verification is not None:
                self._restore_failed_publication(target, verification, previous)
            raise
        except (AccountAuthError, OSError):
            if published and verification is not None:
                self._restore_failed_publication(target, verification, previous)
            raise PrivateMessageCorruptionError() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if verification is not None:
                os.close(verification)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _write_anchor(self, generation: int, store_seal: str) -> None:
        self._publish_private_file(
            self.anchor_path, self._anchor_bytes(generation, store_seal),
        )

    def _load(self) -> dict[str, Any]:
        lock_anchor = self._lock_anchor
        if lock_anchor is None:
            raise PrivateMessageCorruptionError()
        raw = self._read_bytes()
        anchor = self._read_anchor()
        if raw is None:
            if self._lock_anchor_damaged:
                raise PrivateMessageCorruptionError()
            if (
                lock_anchor["generation"] != 0
                or lock_anchor["store_seal"] != _GENESIS_HMAC
            ):
                raise PrivateMessageCorruptionError()
            if anchor is None:
                self._write_anchor(0, _GENESIS_HMAC)
                anchor = {"generation": 0, "store_seal": _GENESIS_HMAC}
            if anchor["generation"] != 0 or anchor["store_seal"] != _GENESIS_HMAC:
                raise PrivateMessageCorruptionError()
            if self._observed_generation != 0 or self._observed_seal != _GENESIS_HMAC:
                raise PrivateMessageCorruptionError()
            return self._empty(self._owner_key)
        if anchor is None:
            raise PrivateMessageCorruptionError()
        try:
            document = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
            )
            payload = self._validate_payload(document)
        except PrivateMessageCorruptionError:
            raise
        except (
            OverflowError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            raise PrivateMessageCorruptionError() from None
        store_seal = document["seal"]
        generation = payload["generation"]
        lock_needs_repair = False
        if lock_anchor["generation"] == generation:
            if not hmac.compare_digest(lock_anchor["store_seal"], store_seal):
                raise PrivateMessageCorruptionError()
        elif lock_anchor["generation"] + 1 == generation:
            lock_needs_repair = True
        else:
            raise PrivateMessageCorruptionError()
        if self._lock_anchor_damaged and not lock_needs_repair:
            raise PrivateMessageCorruptionError()
        anchor_needs_repair = False
        if anchor["generation"] == generation:
            if not hmac.compare_digest(anchor["store_seal"], store_seal):
                raise PrivateMessageCorruptionError()
        elif anchor["generation"] + 1 == generation:
            anchor_needs_repair = True
        else:
            raise PrivateMessageCorruptionError()
        if (
            generation < self._observed_generation
            or (
                generation == self._observed_generation
                and not hmac.compare_digest(store_seal, self._observed_seal)
            )
        ):
            raise PrivateMessageCorruptionError()
        if anchor_needs_repair:
            self._write_anchor(generation, store_seal)
        if lock_needs_repair:
            self._write_lock_anchor(generation, store_seal)
        self._observed_generation = generation
        self._observed_seal = store_seal
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        unsigned = {key: value for key, value in payload.items() if key != "seal"}
        unsigned["generation"] = int(unsigned["generation"]) + 1
        unsigned["clock_high_water"] = max(
            float(unsigned["clock_high_water"]),
            max(
                (
                    float(thread["updated_at"])
                    for thread in unsigned["threads"]
                ),
                default=0.0,
            ),
        )
        document = {**unsigned, "seal": self._seal(unsigned)}
        self._validate_payload(document)
        encoded = _canonical(document)
        if len(encoded) > self._max_store_bytes:
            raise PrivateMessageCapacityError()
        self._publish_private_file(self.path, encoded)
        self._write_anchor(unsigned["generation"], document["seal"])
        self._write_lock_anchor(unsigned["generation"], document["seal"])
        self._observed_generation = unsigned["generation"]
        self._observed_seal = document["seal"]
        payload.clear()
        payload.update(unsigned)

    def _validate_event(
        self,
        value: Any,
        *,
        thread_id: str,
        sequence: int,
        previous_hmac: str,
    ) -> dict[str, Any]:
        keys = {
            "global_sequence", "sequence", "event_id", "kind", "actor_key",
            "occurred_at", "subject", "body", "attachments", "grants",
            "stage", "triage_state", "scan_state", "delivery_state", "reason",
            "progress_current", "progress_total", "read_through", "flag_value",
            "previous_hmac", "event_hmac",
        }
        if not isinstance(value, dict) or set(value) != keys:
            raise PrivateMessageCorruptionError()
        if (
            type(value["global_sequence"]) is not int
            or value["global_sequence"] < 1
            or value["sequence"] != sequence
            or type(value["sequence"]) is not int
            or not isinstance(value["event_id"], str)
            or _EVENT_ID_RE.fullmatch(value["event_id"]) is None
            or value["kind"] not in EVENT_KINDS
            or not isinstance(value["actor_key"], str)
            or _DIGEST_RE.fullmatch(value["actor_key"]) is None
            or not _finite_number(value["occurred_at"])
            or float(value["occurred_at"]) < 0
            or value["previous_hmac"] != previous_hmac
            or not isinstance(value["event_hmac"], str)
            or _DIGEST_RE.fullmatch(value["event_hmac"]) is None
        ):
            raise PrivateMessageCorruptionError()
        expected_id = "evt_" + self._digest(
            _EVENT_DOMAIN,
            thread_id.encode("ascii"),
            str(sequence).encode("ascii"),
            str(value["global_sequence"]).encode("ascii"),
        )[:32]
        unsigned = {key: item for key, item in value.items() if key != "event_hmac"}
        if (
            value["event_id"] != expected_id
            or not hmac.compare_digest(value["event_hmac"], self._event_hmac(unsigned))
        ):
            raise PrivateMessageCorruptionError()
        try:
            attachments = _normalize_attachments(value["attachments"])
            grants = _normalize_grants(value["grants"])
        except PrivateMessageError:
            raise PrivateMessageCorruptionError() from None
        if list(attachments) != value["attachments"] or list(grants) != value["grants"]:
            raise PrivateMessageCorruptionError()
        kind = value["kind"]
        if kind in MESSAGE_KINDS:
            try:
                subject = _validate_text(
                    value["subject"], maximum_bytes=MAX_SUBJECT_BYTES,
                    allow_empty=False,
                ) if kind == "created" else value["subject"]
                _validate_text(
                    value["body"], maximum_bytes=MAX_BODY_BYTES, allow_empty=True,
                )
            except PrivateMessageError:
                raise PrivateMessageCorruptionError() from None
            if (kind == "created" and subject != value["subject"]) or (
                kind == "reply" and value["subject"] is not None
            ):
                raise PrivateMessageCorruptionError()
        elif (
            value["subject"] is not None
            or value["body"] is not None
            or value["attachments"] != []
            or value["grants"] != []
        ):
            raise PrivateMessageCorruptionError()
        state_fields = {
            "stage": THREAD_STAGES,
            "triage_state": TRIAGE_STATES,
            "scan_state": SCAN_STATES,
            "delivery_state": DELIVERY_STATES,
            "reason": REASON_CODES,
        }
        for field, allowed in state_fields.items():
            if value[field] is not None and value[field] not in allowed:
                raise PrivateMessageCorruptionError()
        for field in ("progress_current", "progress_total", "read_through"):
            if value[field] is not None and (
                type(value[field]) is not int or not 0 <= value[field] <= (1 << 63) - 1
            ):
                raise PrivateMessageCorruptionError()
        if value["flag_value"] is not None and type(value["flag_value"]) is not bool:
            raise PrivateMessageCorruptionError()
        required: dict[str, set[str]] = {
            "created": {
                "stage", "triage_state", "scan_state", "delivery_state", "reason",
                "progress_current", "progress_total",
            },
            "reply": {
                "stage", "triage_state", "scan_state", "delivery_state", "reason",
                "progress_current", "progress_total",
            },
            "read": {"read_through"},
            "archive": {"flag_value"},
            "mute": {"flag_value"},
            "cancel": {"stage", "delivery_state", "reason"},
            "retry": {
                "stage", "triage_state", "scan_state", "delivery_state", "reason",
                "progress_current", "progress_total",
            },
            "stage_update": {
                "stage", "triage_state", "scan_state", "delivery_state", "reason",
                "progress_current", "progress_total",
            },
        }
        optional_fields = {
            "stage", "triage_state", "scan_state", "delivery_state", "reason",
            "progress_current", "progress_total", "read_through", "flag_value",
        }
        populated = {field for field in optional_fields if value[field] is not None}
        if kind == "stage_update":
            if not populated or not populated <= required[kind]:
                raise PrivateMessageCorruptionError()
        elif populated != required[kind]:
            raise PrivateMessageCorruptionError()
        if kind == "read" and value["read_through"] > value["global_sequence"]:
            raise PrivateMessageCorruptionError()
        if kind == "stage_update" and (
            value["stage"] == "cancelled"
            or value["delivery_state"] == "cancelled"
            or value["reason"] in {
                "cancelled_by_creator", "cancelled_by_owner", "retry_requested",
            }
        ):
            raise PrivateMessageCorruptionError()
        if (
            value["progress_current"] is not None
            and value["progress_total"] is not None
            and value["progress_current"] > value["progress_total"]
        ):
            raise PrivateMessageCorruptionError()
        return dict(value)

    def _validate_thread(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "thread_id", "creator_key", "owner_key", "revision",
            "created_at", "updated_at", "events",
        }:
            raise PrivateMessageCorruptionError()
        if (
            not isinstance(value["thread_id"], str)
            or _THREAD_ID_RE.fullmatch(value["thread_id"]) is None
            or not isinstance(value["creator_key"], str)
            or _DIGEST_RE.fullmatch(value["creator_key"]) is None
            or value["creator_key"] == self._owner_key
            or value["owner_key"] != self._owner_key
            or type(value["revision"]) is not int
            or not isinstance(value["events"], list)
            or not 1 <= len(value["events"]) <= self._max_events_per_thread
            or value["revision"] != len(value["events"])
            or not _finite_number(value["created_at"])
            or not _finite_number(value["updated_at"])
            or not 0 <= float(value["created_at"]) <= float(value["updated_at"])
        ):
            raise PrivateMessageCorruptionError()
        events = []
        previous_hmac = _GENESIS_HMAC
        previous_time = float(value["created_at"])
        for sequence, raw_event in enumerate(value["events"], start=1):
            event = self._validate_event(
                raw_event,
                thread_id=value["thread_id"],
                sequence=sequence,
                previous_hmac=previous_hmac,
            )
            if float(event["occurred_at"]) < previous_time:
                raise PrivateMessageCorruptionError()
            if event["actor_key"] not in {value["creator_key"], value["owner_key"]}:
                raise PrivateMessageCorruptionError()
            if sequence == 1 and (
                event["kind"] != "created"
                or event["actor_key"] != value["creator_key"]
                or float(event["occurred_at"]) != float(value["created_at"])
            ):
                raise PrivateMessageCorruptionError()
            previous_hmac = event["event_hmac"]
            previous_time = float(event["occurred_at"])
            events.append(event)
        if float(value["updated_at"]) != previous_time:
            raise PrivateMessageCorruptionError()
        normalized = {**value, "events": events}
        for index, event in enumerate(events, start=1):
            if event["kind"] in {"created", "reply"} and (
                event["stage"] != "draft_saved"
                or event["triage_state"] != "not_started"
                or event["scan_state"] != "not_started"
                or event["delivery_state"] != "not_started"
                or event["reason"] != "none"
                or event["progress_current"] != 0
                or event["progress_total"] != 0
            ):
                raise PrivateMessageCorruptionError()
            if event["kind"] == "cancel" and (
                event["stage"] != "cancelled"
                or event["delivery_state"] != "cancelled"
                or event["reason"] != (
                    "cancelled_by_owner"
                    if event["actor_key"] == value["owner_key"]
                    else "cancelled_by_creator"
                )
            ):
                raise PrivateMessageCorruptionError()
            if event["kind"] == "retry" and (
                event["stage"] != "waiting_for_model"
                or event["triage_state"] != "waiting_for_model"
                or event["scan_state"] != "not_started"
                or event["delivery_state"] != "not_started"
                or event["reason"] != "retry_requested"
                or event["progress_current"] != 0
                or event["progress_total"] != 0
            ):
                raise PrivateMessageCorruptionError()
            try:
                self._assert_state_coherent({
                    **self._state({**normalized, "events": events[:index]}),
                })
            except PrivateMessageError:
                raise PrivateMessageCorruptionError() from None
        return normalized

    def _validate_mutation(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "mutation_key", "actor_key", "thread_id", "operation",
            "payload_digest", "result",
        }:
            raise PrivateMessageCorruptionError()
        result = value.get("result")
        if (
            not isinstance(value["mutation_key"], str)
            or _DIGEST_RE.fullmatch(value["mutation_key"]) is None
            or not isinstance(value["actor_key"], str)
            or _DIGEST_RE.fullmatch(value["actor_key"]) is None
            or not isinstance(value["thread_id"], str)
            or _THREAD_ID_RE.fullmatch(value["thread_id"]) is None
            or value["operation"] not in {
                "create", "reply", "read", "archive", "mute", "cancel",
                "retry", "stage_update",
            }
            or not isinstance(value["payload_digest"], str)
            or _DIGEST_RE.fullmatch(value["payload_digest"]) is None
            or not isinstance(result, dict)
            or set(result) != {
                "thread_id", "revision", "event_sequence", "operation", "stage",
            }
            or result["thread_id"] != value["thread_id"]
            or result["operation"] != value["operation"]
            or type(result["revision"]) is not int
            or type(result["event_sequence"]) is not int
            or result["revision"] < 1
            or result["event_sequence"] < 1
            or result["stage"] not in THREAD_STAGES
        ):
            raise PrivateMessageCorruptionError()
        return dict(value)

    def _validate_payload(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "version", "generation", "clock_high_water", "owner_key",
            "threads", "mutations", "seal",
        }:
            raise PrivateMessageCorruptionError()
        seal = value["seal"]
        unsigned = {key: item for key, item in value.items() if key != "seal"}
        if (
            value["version"] != PRIVATE_MESSAGE_STORE_VERSION
            or type(value["generation"]) is not int
            or not 0 <= value["generation"] <= (1 << 63) - 1
            or not _finite_number(value["clock_high_water"])
            or float(value["clock_high_water"]) < 0
            or value["owner_key"] != self._owner_key
            or not isinstance(value["threads"], list)
            or len(value["threads"]) > self._max_threads
            or not isinstance(value["mutations"], list)
            or len(value["mutations"]) > self._max_mutations
            or not isinstance(seal, str)
            or _DIGEST_RE.fullmatch(seal) is None
            or not hmac.compare_digest(seal, self._seal(unsigned))
        ):
            raise PrivateMessageCorruptionError()
        threads = [self._validate_thread(thread) for thread in value["threads"]]
        mutations = [self._validate_mutation(item) for item in value["mutations"]]
        if len({thread["thread_id"] for thread in threads}) != len(threads):
            raise PrivateMessageCorruptionError()
        mutation_keys = [item["mutation_key"] for item in mutations]
        if len(set(mutation_keys)) != len(mutation_keys):
            raise PrivateMessageCorruptionError()
        all_events = [event for thread in threads for event in thread["events"]]
        if len(all_events) > MAX_THREADS * MAX_EVENTS_PER_THREAD:
            raise PrivateMessageCorruptionError()
        if sorted(event["global_sequence"] for event in all_events) != list(
            range(1, len(all_events) + 1),
        ):
            raise PrivateMessageCorruptionError()
        if any(
            float(event["occurred_at"]) > float(value["clock_high_water"])
            for event in all_events
        ):
            raise PrivateMessageCorruptionError()
        by_thread = {thread["thread_id"]: thread for thread in threads}
        if any(
            sum(thread["creator_key"] == creator for thread in threads)
            > self._max_threads_per_creator
            for creator in {thread["creator_key"] for thread in threads}
        ):
            raise PrivateMessageCorruptionError()
        message_events = [
            event for thread in threads for event in thread["events"]
            if event["kind"] in MESSAGE_KINDS
        ]
        if len(message_events) > self._max_messages:
            raise PrivateMessageCorruptionError()
        if any(
            sum(
                event["kind"] in MESSAGE_KINDS
                for thread in threads
                if thread["creator_key"] == creator
                for event in thread["events"]
            ) > self._max_messages_per_creator
            for creator in {thread["creator_key"] for thread in threads}
        ):
            raise PrivateMessageCorruptionError()
        if any(
            sum(item["actor_key"] == actor for item in mutations)
            > self._max_mutations_per_actor
            for actor in {item["actor_key"] for item in mutations}
        ):
            raise PrivateMessageCorruptionError()
        operation_kinds = {
            "create": "created",
            "reply": "reply",
            "read": "read",
            "archive": "archive",
            "mute": "mute",
            "cancel": "cancel",
            "retry": "retry",
            "stage_update": "stage_update",
        }
        for mutation in mutations:
            thread = by_thread.get(mutation["thread_id"])
            result = mutation["result"]
            event = (
                thread["events"][result["event_sequence"] - 1]
                if thread is not None
                and 1 <= result["event_sequence"] <= len(thread["events"])
                else None
            )
            if (
                thread is None
                or event is None
                or result["revision"] > thread["revision"]
                or result["event_sequence"] > thread["revision"]
                or result["revision"] != result["event_sequence"]
                or mutation["actor_key"] not in {thread["creator_key"], thread["owner_key"]}
                or event["actor_key"] != mutation["actor_key"]
                or event["kind"] != operation_kinds[mutation["operation"]]
                or result["stage"] != self._state({
                    **thread,
                    "events": thread["events"][:result["event_sequence"]],
                })["stage"]
            ):
                raise PrivateMessageCorruptionError()
        return {**unsigned, "threads": threads, "mutations": mutations}

    def _now(self, payload: Mapping[str, Any], thread: Mapping[str, Any] | None = None) -> float:
        try:
            observed = float(self._clock())
        except (OverflowError, TypeError, ValueError):
            raise PrivateMessageCorruptionError() from None
        if not math.isfinite(observed) or observed < 0:
            raise PrivateMessageCorruptionError()
        values = [observed, float(payload["clock_high_water"])]
        if thread is not None:
            values.append(float(thread["updated_at"]))
        return max(values)

    @staticmethod
    def _find_thread(payload: Mapping[str, Any], thread_id: str) -> dict[str, Any]:
        selected = next(
            (thread for thread in payload["threads"] if thread["thread_id"] == thread_id),
            None,
        )
        if selected is None:
            raise PrivateMessageAuthorizationError()
        return selected

    def _authorize(
        self,
        thread: Mapping[str, Any],
        actor_account_id: str,
    ) -> tuple[str, str]:
        actor_id = _validate_identifier(actor_account_id, _ACCOUNT_ID_RE)
        actor_key = self._account_key(actor_id)
        if actor_key == self._owner_key and not self._owner_enabled:
            raise PrivateMessageOwnerUnavailableError()
        if actor_key not in {thread["creator_key"], thread["owner_key"]}:
            raise PrivateMessageAuthorizationError()
        role = "owner" if actor_key == thread["owner_key"] else "creator"
        return actor_key, role

    @staticmethod
    def _state(thread: Mapping[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = {
            "stage": "draft_saved",
            "triage_state": "not_started",
            "scan_state": "not_started",
            "delivery_state": "not_started",
            "reason": "none",
            "progress_current": 0,
            "progress_total": 0,
            "archived_by": {},
            "muted_by": {},
            "read_through_by": {},
        }
        for event in thread["events"]:
            for field in (
                "stage", "triage_state", "scan_state", "delivery_state", "reason",
                "progress_current", "progress_total",
            ):
                if event[field] is not None:
                    state[field] = event[field]
            if event["kind"] == "read":
                state["read_through_by"][event["actor_key"]] = max(
                    state["read_through_by"].get(event["actor_key"], 0),
                    event["read_through"],
                )
            elif event["kind"] == "archive":
                state["archived_by"][event["actor_key"]] = event["flag_value"]
            elif event["kind"] == "mute":
                state["muted_by"][event["actor_key"]] = event["flag_value"]
        return state

    @staticmethod
    def _assert_state_coherent(state: Mapping[str, Any]) -> None:
        stage = state["stage"]
        required = {
            "waiting_for_model": ("triage_state", "waiting_for_model"),
            "triaging": ("triage_state", "triaging"),
            "scanning_attachments": ("scan_state", "scanning"),
            "queued_for_delivery": ("delivery_state", "queued"),
            "delivered": ("delivery_state", "delivered"),
            "refused": ("delivery_state", "refused"),
            "cancelled": ("delivery_state", "cancelled"),
        }
        if stage in required:
            field, expected = required[stage]
            if state[field] != expected:
                raise PrivateMessageConflictError("invalid_state")
        reverse_required = {
            ("triage_state", "waiting_for_model"): "waiting_for_model",
            ("triage_state", "triaging"): "triaging",
            ("scan_state", "scanning"): "scanning_attachments",
            ("delivery_state", "queued"): "queued_for_delivery",
            ("delivery_state", "delivered"): "delivered",
            ("delivery_state", "refused"): "refused",
            ("delivery_state", "cancelled"): "cancelled",
        }
        for (field, selected), expected_stage in reverse_required.items():
            if (
                state[field] == selected
                and stage not in {"failed", "cancelled"}
                and stage != expected_stage
            ):
                raise PrivateMessageConflictError("invalid_state")
        if stage in {
            "scanning_attachments", "queued_for_delivery", "delivered", "refused",
        } and state["triage_state"] != "complete":
            raise PrivateMessageConflictError("invalid_state")
        if stage in {"queued_for_delivery", "delivered", "refused"} and (
            state["scan_state"] != "clean"
        ):
            raise PrivateMessageConflictError("invalid_state")
        if stage == "failed" and "failed" not in {
            state["triage_state"], state["scan_state"], state["delivery_state"],
        }:
            raise PrivateMessageConflictError("invalid_state")
        if stage == "delivered" and state["reason"] != "none":
            raise PrivateMessageConflictError("invalid_state")
        if stage == "refused" and state["reason"] in {"none", "retry_requested"}:
            raise PrivateMessageConflictError("invalid_state")
        if stage == "cancelled" and state["reason"] not in {
            "cancelled_by_creator", "cancelled_by_owner",
        }:
            raise PrivateMessageConflictError("invalid_state")

    @staticmethod
    def _receipt(record: Mapping[str, Any]) -> MutationReceipt:
        return MutationReceipt(**record["result"])

    def _replay(
        self,
        payload: Mapping[str, Any],
        *,
        mutation_key: str,
        payload_digest: str,
    ) -> MutationReceipt | None:
        existing = next(
            (
                item for item in payload["mutations"]
                if hmac.compare_digest(item["mutation_key"], mutation_key)
            ),
            None,
        )
        if existing is None:
            return None
        if not hmac.compare_digest(existing["payload_digest"], payload_digest):
            raise PrivateMessageConflictError()
        return self._receipt(existing)

    def _check_mutation_capacity(
        self,
        payload: Mapping[str, Any],
        *,
        actor_key: str,
    ) -> None:
        if len(payload["mutations"]) >= self._max_mutations:
            raise PrivateMessageCapacityError()
        if sum(
            1 for item in payload["mutations"] if item["actor_key"] == actor_key
        ) >= self._max_mutations_per_actor:
            raise PrivateMessageCapacityError()

    def _check_message_capacity(
        self,
        payload: Mapping[str, Any],
        *,
        creator_key: str,
    ) -> None:
        message_count = sum(
            event["kind"] in MESSAGE_KINDS
            for thread in payload["threads"]
            for event in thread["events"]
        )
        if message_count >= self._max_messages:
            raise PrivateMessageCapacityError()
        creator_count = sum(
            event["kind"] in MESSAGE_KINDS
            for thread in payload["threads"]
            if thread["creator_key"] == creator_key
            for event in thread["events"]
        )
        if creator_count >= self._max_messages_per_creator:
            raise PrivateMessageCapacityError()

    def _new_event(
        self,
        payload: Mapping[str, Any],
        thread: Mapping[str, Any],
        *,
        kind: str,
        actor_key: str,
        occurred_at: float,
        subject: str | None = None,
        body: str | None = None,
        attachments: Sequence[Mapping[str, Any]] = (),
        grants: Sequence[Mapping[str, Any]] = (),
        stage: str | None = None,
        triage_state: str | None = None,
        scan_state: str | None = None,
        delivery_state: str | None = None,
        reason: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        read_through: int | None = None,
        flag_value: bool | None = None,
    ) -> dict[str, Any]:
        sequence = len(thread["events"]) + 1
        global_sequence = sum(len(item["events"]) for item in payload["threads"]) + 1
        previous_hmac = (
            thread["events"][-1]["event_hmac"] if thread["events"] else _GENESIS_HMAC
        )
        event_id = "evt_" + self._digest(
            _EVENT_DOMAIN,
            thread["thread_id"].encode("ascii"),
            str(sequence).encode("ascii"),
            str(global_sequence).encode("ascii"),
        )[:32]
        unsigned = {
            "global_sequence": global_sequence,
            "sequence": sequence,
            "event_id": event_id,
            "kind": kind,
            "actor_key": actor_key,
            "occurred_at": occurred_at,
            "subject": subject,
            "body": body,
            "attachments": [dict(value) for value in attachments],
            "grants": [dict(value) for value in grants],
            "stage": stage,
            "triage_state": triage_state,
            "scan_state": scan_state,
            "delivery_state": delivery_state,
            "reason": reason,
            "progress_current": progress_current,
            "progress_total": progress_total,
            "read_through": read_through,
            "flag_value": flag_value,
            "previous_hmac": previous_hmac,
        }
        return {**unsigned, "event_hmac": self._event_hmac(unsigned)}

    def _commit(
        self,
        payload: dict[str, Any],
        thread: dict[str, Any],
        *,
        actor_key: str,
        operation: str,
        mutation_key: str,
        payload_digest: str,
        event: dict[str, Any],
    ) -> MutationReceipt:
        if len(thread["events"]) >= self._max_events_per_thread:
            raise PrivateMessageCapacityError()
        self._check_mutation_capacity(payload, actor_key=actor_key)
        thread["events"].append(event)
        thread["revision"] += 1
        thread["updated_at"] = event["occurred_at"]
        state = self._state(thread)
        self._assert_state_coherent(state)
        result = {
            "thread_id": thread["thread_id"],
            "revision": thread["revision"],
            "event_sequence": event["sequence"],
            "operation": operation,
            "stage": state["stage"],
        }
        payload["mutations"].append({
            "mutation_key": mutation_key,
            "actor_key": actor_key,
            "thread_id": thread["thread_id"],
            "operation": operation,
            "payload_digest": payload_digest,
            "result": result,
        })
        self._save(payload)
        return MutationReceipt(**result)

    @staticmethod
    def _validate_revision(expected_revision: Any, actual_revision: int) -> int:
        if type(expected_revision) is not int or expected_revision < 1:
            raise PrivateMessageValidationError()
        if expected_revision != actual_revision:
            raise PrivateMessageConflictError("revision_conflict")
        return expected_revision

    def create_thread(
        self,
        *,
        actor_account_id: str,
        request_id: str,
        subject: str,
        body: str,
        attachments: Sequence[AttachmentDescriptor | Mapping[str, Any]] | None = None,
        grants: Sequence[GrantDescriptor | Mapping[str, Any]] | None = None,
    ) -> MutationReceipt:
        actor_id = _validate_identifier(actor_account_id, _ACCOUNT_ID_RE)
        selected_request = _validate_identifier(request_id, _REQUEST_ID_RE)
        selected_subject = _validate_text(
            subject, maximum_bytes=MAX_SUBJECT_BYTES, allow_empty=False,
        )
        selected_body = _validate_text(body, maximum_bytes=MAX_BODY_BYTES, allow_empty=True)
        selected_attachments = _normalize_attachments(attachments)
        selected_grants = _normalize_grants(grants)
        actor_key = self._account_key(actor_id)
        if actor_key == self._owner_key:
            raise PrivateMessageValidationError()
        thread_id = self._thread_id(actor_id, selected_request)
        mutation_key = self._mutation_key(
            actor_id=actor_id,
            thread_id=thread_id,
            operation="create",
            request_id=selected_request,
        )
        digest = self._payload_digest({
            "subject": selected_subject,
            "body": selected_body,
            "attachments": list(selected_attachments),
            "grants": list(selected_grants),
        })
        with self._locked():
            payload = self._load()
            replay = self._replay(
                payload, mutation_key=mutation_key, payload_digest=digest,
            )
            if replay is not None:
                return replay
            if not self._owner_enabled:
                raise PrivateMessageOwnerUnavailableError()
            if len(payload["threads"]) >= self._max_threads:
                raise PrivateMessageCapacityError()
            if sum(
                thread["creator_key"] == actor_key for thread in payload["threads"]
            ) >= self._max_threads_per_creator:
                raise PrivateMessageCapacityError()
            if any(thread["thread_id"] == thread_id for thread in payload["threads"]):
                raise PrivateMessageConflictError()
            self._check_message_capacity(payload, creator_key=actor_key)
            self._check_mutation_capacity(payload, actor_key=actor_key)
            occurred_at = self._now(payload)
            thread: dict[str, Any] = {
                "thread_id": thread_id,
                "creator_key": actor_key,
                "owner_key": self._owner_key,
                "revision": 0,
                "created_at": occurred_at,
                "updated_at": occurred_at,
                "events": [],
            }
            payload["threads"].append(thread)
            event = self._new_event(
                payload,
                thread,
                kind="created",
                actor_key=actor_key,
                occurred_at=occurred_at,
                subject=selected_subject,
                body=selected_body,
                attachments=selected_attachments,
                grants=selected_grants,
                stage="draft_saved",
                triage_state="not_started",
                scan_state="not_started",
                delivery_state="not_started",
                reason="none",
                progress_current=0,
                progress_total=0,
            )
            return self._commit(
                payload,
                thread,
                actor_key=actor_key,
                operation="create",
                mutation_key=mutation_key,
                payload_digest=digest,
                event=event,
            )

    def _apply_existing(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        operation: str,
        expected_revision: int,
        private_payload: Mapping[str, Any],
        event_factory,
        message_event: bool = False,
        owner_only: bool = False,
    ) -> MutationReceipt:
        actor_id = _validate_identifier(actor_account_id, _ACCOUNT_ID_RE)
        selected_thread = _validate_identifier(thread_id, _THREAD_ID_RE)
        selected_request = _validate_identifier(request_id, _REQUEST_ID_RE)
        mutation_key = self._mutation_key(
            actor_id=actor_id,
            thread_id=selected_thread,
            operation=operation,
            request_id=selected_request,
        )
        digest = self._payload_digest({
            "expected_revision": expected_revision,
            **private_payload,
        })
        with self._locked():
            payload = self._load()
            thread = self._find_thread(payload, selected_thread)
            actor_key, role = self._authorize(thread, actor_id)
            if owner_only and role != "owner":
                raise PrivateMessageAuthorizationError()
            replay = self._replay(
                payload, mutation_key=mutation_key, payload_digest=digest,
            )
            if replay is not None:
                return replay
            self._validate_revision(expected_revision, thread["revision"])
            if message_event:
                self._check_message_capacity(
                    payload, creator_key=thread["creator_key"],
                )
            occurred_at = self._now(payload, thread)
            event = event_factory(payload, thread, actor_key, role, occurred_at)
            return self._commit(
                payload,
                thread,
                actor_key=actor_key,
                operation=operation,
                mutation_key=mutation_key,
                payload_digest=digest,
                event=event,
            )

    def reply(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        expected_revision: int,
        body: str,
        attachments: Sequence[AttachmentDescriptor | Mapping[str, Any]] | None = None,
        grants: Sequence[GrantDescriptor | Mapping[str, Any]] | None = None,
    ) -> MutationReceipt:
        selected_body = _validate_text(body, maximum_bytes=MAX_BODY_BYTES, allow_empty=True)
        selected_attachments = _normalize_attachments(attachments)
        selected_grants = _normalize_grants(grants)
        if not selected_body and not selected_attachments and not selected_grants:
            raise PrivateMessageValidationError()

        def event_factory(payload, thread, actor_key, _role, occurred_at):
            if self._state(thread)["stage"] == "cancelled":
                raise PrivateMessageConflictError("invalid_state")
            return self._new_event(
                payload,
                thread,
                kind="reply",
                actor_key=actor_key,
                occurred_at=occurred_at,
                body=selected_body,
                attachments=selected_attachments,
                grants=selected_grants,
                stage="draft_saved",
                triage_state="not_started",
                scan_state="not_started",
                delivery_state="not_started",
                reason="none",
                progress_current=0,
                progress_total=0,
            )

        return self._apply_existing(
            actor_account_id=actor_account_id,
            thread_id=thread_id,
            request_id=request_id,
            operation="reply",
            expected_revision=expected_revision,
            private_payload={
                "body": selected_body,
                "attachments": list(selected_attachments),
                "grants": list(selected_grants),
            },
            event_factory=event_factory,
            message_event=True,
        )

    def mark_read(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        expected_revision: int,
    ) -> MutationReceipt:
        def event_factory(payload, thread, actor_key, _role, occurred_at):
            read_through = sum(len(item["events"]) for item in payload["threads"])
            return self._new_event(
                payload,
                thread,
                kind="read",
                actor_key=actor_key,
                occurred_at=occurred_at,
                read_through=read_through,
            )

        return self._apply_existing(
            actor_account_id=actor_account_id,
            thread_id=thread_id,
            request_id=request_id,
            operation="read",
            expected_revision=expected_revision,
            private_payload={},
            event_factory=event_factory,
        )

    def set_archived(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        expected_revision: int,
        archived: bool,
    ) -> MutationReceipt:
        if type(archived) is not bool:
            raise PrivateMessageValidationError()

        def event_factory(payload, thread, actor_key, _role, occurred_at):
            return self._new_event(
                payload,
                thread,
                kind="archive",
                actor_key=actor_key,
                occurred_at=occurred_at,
                flag_value=archived,
            )

        return self._apply_existing(
            actor_account_id=actor_account_id,
            thread_id=thread_id,
            request_id=request_id,
            operation="archive",
            expected_revision=expected_revision,
            private_payload={"archived": archived},
            event_factory=event_factory,
        )

    def set_muted(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        expected_revision: int,
        muted: bool,
    ) -> MutationReceipt:
        if type(muted) is not bool:
            raise PrivateMessageValidationError()

        def event_factory(payload, thread, actor_key, _role, occurred_at):
            return self._new_event(
                payload,
                thread,
                kind="mute",
                actor_key=actor_key,
                occurred_at=occurred_at,
                flag_value=muted,
            )

        return self._apply_existing(
            actor_account_id=actor_account_id,
            thread_id=thread_id,
            request_id=request_id,
            operation="mute",
            expected_revision=expected_revision,
            private_payload={"muted": muted},
            event_factory=event_factory,
        )

    def cancel(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        expected_revision: int,
    ) -> MutationReceipt:
        def event_factory(payload, thread, actor_key, role, occurred_at):
            if self._state(thread)["stage"] in {
                "queued_for_delivery", "delivered", "refused", "cancelled",
            }:
                raise PrivateMessageConflictError("invalid_state")
            return self._new_event(
                payload,
                thread,
                kind="cancel",
                actor_key=actor_key,
                occurred_at=occurred_at,
                stage="cancelled",
                delivery_state="cancelled",
                reason=f"cancelled_by_{role}",
            )

        return self._apply_existing(
            actor_account_id=actor_account_id,
            thread_id=thread_id,
            request_id=request_id,
            operation="cancel",
            expected_revision=expected_revision,
            private_payload={},
            event_factory=event_factory,
        )

    def retry(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        expected_revision: int,
    ) -> MutationReceipt:
        def event_factory(payload, thread, actor_key, _role, occurred_at):
            if self._state(thread)["stage"] not in {"failed", "refused", "cancelled"}:
                raise PrivateMessageConflictError("invalid_state")
            return self._new_event(
                payload,
                thread,
                kind="retry",
                actor_key=actor_key,
                occurred_at=occurred_at,
                stage="waiting_for_model",
                triage_state="waiting_for_model",
                scan_state="not_started",
                delivery_state="not_started",
                reason="retry_requested",
                progress_current=0,
                progress_total=0,
            )

        return self._apply_existing(
            actor_account_id=actor_account_id,
            thread_id=thread_id,
            request_id=request_id,
            operation="retry",
            expected_revision=expected_revision,
            private_payload={},
            event_factory=event_factory,
        )

    def update_stage(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
        request_id: str,
        expected_revision: int,
        stage: str | None = None,
        triage_state: str | None = None,
        scan_state: str | None = None,
        delivery_state: str | None = None,
        reason: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
    ) -> MutationReceipt:
        values = {
            "stage": stage,
            "triage_state": triage_state,
            "scan_state": scan_state,
            "delivery_state": delivery_state,
            "reason": reason,
            "progress_current": progress_current,
            "progress_total": progress_total,
        }
        if all(value is None for value in values.values()):
            raise PrivateMessageValidationError()
        for field, allowed in (
            ("stage", THREAD_STAGES),
            ("triage_state", TRIAGE_STATES),
            ("scan_state", SCAN_STATES),
            ("delivery_state", DELIVERY_STATES),
            ("reason", REASON_CODES),
        ):
            if values[field] is not None and values[field] not in allowed:
                raise PrivateMessageValidationError()
        if (progress_current is None) != (progress_total is None):
            raise PrivateMessageValidationError()
        if progress_current is not None and (
            type(progress_current) is not int
            or type(progress_total) is not int
            or not 0 <= progress_current <= progress_total <= (1 << 31) - 1
        ):
            raise PrivateMessageValidationError()
        if stage in {"failed", "refused", "cancelled"} and reason in {None, "none"}:
            raise PrivateMessageValidationError()
        if (
            stage == "cancelled"
            or delivery_state == "cancelled"
            or reason in {
                "cancelled_by_creator", "cancelled_by_owner", "retry_requested",
            }
        ):
            raise PrivateMessageValidationError()

        def event_factory(payload, thread, actor_key, _role, occurred_at):
            current_stage = self._state(thread)["stage"]
            if current_stage == "delivered" and stage not in {None, "delivered"}:
                raise PrivateMessageConflictError("invalid_state")
            if current_stage in {"failed", "refused", "cancelled"}:
                raise PrivateMessageConflictError("invalid_state")
            return self._new_event(
                payload,
                thread,
                kind="stage_update",
                actor_key=actor_key,
                occurred_at=occurred_at,
                **values,
            )

        return self._apply_existing(
            actor_account_id=actor_account_id,
            thread_id=thread_id,
            request_id=request_id,
            operation="stage_update",
            expected_revision=expected_revision,
            private_payload=values,
            event_factory=event_factory,
            owner_only=True,
        )

    def _card(self, thread: Mapping[str, Any], actor_key: str) -> ThreadCard:
        state = self._state(thread)
        read_through = state["read_through_by"].get(actor_key, 0)
        unread_events = [
            event for event in thread["events"]
            if (
                event["kind"] in MESSAGE_KINDS
                and event["actor_key"] != actor_key
                and event["global_sequence"] > read_through
            )
        ]
        message_events = [
            event for event in thread["events"] if event["kind"] in MESSAGE_KINDS
        ]
        attachment_count = sum(len(event["attachments"]) for event in message_events)
        attachment_bytes = sum(
            attachment["byte_count"]
            for event in message_events
            for attachment in event["attachments"]
        )
        stage = state["stage"]
        return ThreadCard(
            thread_id=thread["thread_id"],
            revision=thread["revision"],
            stage=stage,
            triage_state=state["triage_state"],
            scan_state=state["scan_state"],
            delivery_state=state["delivery_state"],
            reason=state["reason"],
            progress_current=state["progress_current"],
            progress_total=state["progress_total"],
            created_at=float(thread["created_at"]),
            updated_at=float(thread["updated_at"]),
            attachment_count=attachment_count,
            attachment_bytes=attachment_bytes,
            unread_count=len(unread_events),
            unread_bump_order=max(
                (event["global_sequence"] for event in unread_events), default=0,
            ),
            archived=bool(state["archived_by"].get(actor_key, False)),
            muted=bool(state["muted_by"].get(actor_key, False)),
            can_retry=stage in {"failed", "refused", "cancelled"},
            can_cancel=stage not in {
                "queued_for_delivery", "delivered", "refused", "cancelled",
            },
        )

    def list_cards(self, *, actor_account_id: str) -> tuple[ThreadCard, ...]:
        actor_id = _validate_identifier(actor_account_id, _ACCOUNT_ID_RE)
        actor_key = self._account_key(actor_id)
        if actor_key == self._owner_key and not self._owner_enabled:
            raise PrivateMessageOwnerUnavailableError()
        with self._locked():
            payload = self._load()
            threads = (
                payload["threads"]
                if actor_key == self._owner_key
                else [
                    thread for thread in payload["threads"]
                    if thread["creator_key"] == actor_key
                ]
            )
            cards = [self._card(thread, actor_key) for thread in threads]
        cards.sort(
            key=lambda card: (
                card.unread_count > 0,
                card.unread_bump_order,
                card.updated_at,
                card.thread_id,
            ),
            reverse=True,
        )
        return tuple(cards)

    def thread_detail(
        self,
        *,
        actor_account_id: str,
        thread_id: str,
    ) -> ThreadDetail:
        actor_id = _validate_identifier(actor_account_id, _ACCOUNT_ID_RE)
        selected_thread = _validate_identifier(thread_id, _THREAD_ID_RE)
        with self._locked():
            payload = self._load()
            thread = self._find_thread(payload, selected_thread)
            actor_key, role = self._authorize(thread, actor_id)
            card = self._card(thread, actor_key)
            created = thread["events"][0]
            messages = []
            for event in thread["events"]:
                if event["kind"] not in MESSAGE_KINDS:
                    continue
                sender_role = (
                    "owner" if event["actor_key"] == thread["owner_key"] else "creator"
                )
                messages.append(MessageView(
                    sequence=event["sequence"],
                    event_id=event["event_id"],
                    sender_role=sender_role,
                    occurred_at=float(event["occurred_at"]),
                    body=event["body"],
                    attachments=tuple(
                        AttachmentDescriptor(**value) for value in event["attachments"]
                    ),
                    grants=tuple(GrantDescriptor(**value) for value in event["grants"]),
                ))
            return ThreadDetail(
                card=card,
                participant_role=role,
                subject=created["subject"],
                messages=tuple(messages),
            )
