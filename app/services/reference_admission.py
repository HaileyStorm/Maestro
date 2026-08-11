"""Durable idempotency reservations for project Reference admission.

Records are intentionally content-free.  Client payloads, project names,
paths, labels, and session identifiers are reduced to keyed digests before any
filesystem write; only bounded opaque job/asset identifiers remain in clear.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_LEASE_SECONDS = 30.0
DEFAULT_MAX_RECORDS = 4096
MAX_REQUEST_ID_BYTES = 128
MAX_RECORD_BYTES = 4096
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ReferenceAdmissionError(RuntimeError):
    """Base class for durable Reference-admission errors."""


class ReferenceAdmissionValidationError(ReferenceAdmissionError, ValueError):
    """Raised for malformed caller input before persistence."""


class ReferenceAdmissionMismatchError(ReferenceAdmissionError):
    """Raised when a request ID is rebound to a different private payload."""


class ReferenceAdmissionPersistenceError(ReferenceAdmissionError):
    """Raised when admission state cannot be read or committed safely."""


class ReferenceAdmissionCapacityError(ReferenceAdmissionError):
    """Raised when the bounded live-record allowance is exhausted."""


class ReferenceAdmissionCorruptionError(ReferenceAdmissionError):
    """Raised when an existing record is malformed; callers must fail closed."""


@dataclass(frozen=True)
class AdmissionReservation:
    """One stable response identity and its current admission disposition."""

    disposition: str
    job_id: str
    asset_id: str
    lease_token: str | None = None

    @property
    def owns_lease(self) -> bool:
        return self.disposition in {"new", "resume"} and bool(self.lease_token)


def normalize_request_id(value: Any) -> str | None:
    """Return one bounded opaque token, retaining omitted-ID compatibility."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ReferenceAdmissionValidationError(
            "Reference request_id must be an opaque string."
        )
    normalized = value.strip()
    if (
        normalized != value
        or len(normalized.encode("utf-8")) > MAX_REQUEST_ID_BYTES
        or _REQUEST_ID_RE.fullmatch(normalized) is None
    ):
        raise ReferenceAdmissionValidationError(
            "Reference request_id must be 8-128 opaque URL-safe characters."
        )
    return normalized


def _canonical_payload_bytes(value: Any) -> bytes:
    """Encode a bounded JSON value without ever returning a rendered string."""
    nodes = 0
    string_bytes = 0
    active: set[int] = set()

    def validate(item: Any, depth: int) -> Any:
        nonlocal nodes, string_bytes
        nodes += 1
        if nodes > 10_000 or depth > 24:
            raise ReferenceAdmissionValidationError(
                "Reference request payload exceeds the structural limit."
            )
        if item is None or type(item) in {bool, int}:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ReferenceAdmissionValidationError(
                    "Reference request payload must contain finite numbers."
                )
            return item
        if type(item) is str:
            encoded_length = len(item.encode("utf-8"))
            string_bytes += encoded_length
            if encoded_length > 1_000_000 or string_bytes > 4_000_000:
                raise ReferenceAdmissionValidationError(
                    "Reference request payload contains an oversized string."
                )
            return item
        if type(item) not in {dict, list, tuple}:
            raise ReferenceAdmissionValidationError(
                "Reference request payload must be JSON-safe."
            )
        identity = id(item)
        if identity in active:
            raise ReferenceAdmissionValidationError(
                "Reference request payload must not contain cycles."
            )
        active.add(identity)
        try:
            if type(item) in {list, tuple}:
                return [validate(value, depth + 1) for value in item]
            result = {}
            for key, value in item.items():
                if type(key) is not str:
                    raise ReferenceAdmissionValidationError(
                        "Reference request payload keys must be strings."
                    )
                string_bytes += len(key.encode("utf-8"))
                if string_bytes > 4_000_000:
                    raise ReferenceAdmissionValidationError(
                        "Reference request payload exceeds the byte limit."
                    )
                result[key] = validate(value, depth + 1)
            return result
        finally:
            active.remove(identity)

    safe = validate(value, 0)
    try:
        encoded = json.dumps(
            safe,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > 5_000_000:
            raise ReferenceAdmissionValidationError(
                "Reference request payload exceeds the byte limit."
            )
        return encoded
    except (RecursionError, TypeError, ValueError, OverflowError):
        raise ReferenceAdmissionValidationError(
            "Reference request payload must be JSON-safe."
        ) from None


class ReferenceAdmissionStore:
    """O_EXCL-serialized, atomic, content-free Reference reservations."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        secret: bytes,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        max_records: int = DEFAULT_MAX_RECORDS,
        clock=time.time,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) < 16:
            raise ReferenceAdmissionValidationError(
                "Reference admission requires a server secret."
            )
        if not 60 <= int(ttl_seconds) <= 90 * 24 * 60 * 60:
            raise ReferenceAdmissionValidationError("Invalid admission TTL.")
        if not 1.0 <= float(lease_seconds) <= 300.0:
            raise ReferenceAdmissionValidationError("Invalid admission lease.")
        if not 1 <= int(max_records) <= 100_000:
            raise ReferenceAdmissionValidationError("Invalid admission bound.")
        self.root = Path(root)
        self._secret = secret
        self.ttl_seconds = int(ttl_seconds)
        self.lease_seconds = float(lease_seconds)
        self.max_records = int(max_records)
        self._clock = clock
        self._ensure_root()

    def _ensure_root(self) -> None:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.root.is_symlink() or not self.root.is_dir():
                raise OSError("unsafe admission directory")
            os.chmod(self.root, 0o700)
        except OSError as error:
            raise ReferenceAdmissionPersistenceError(
                "Reference admission state is unavailable."
            ) from error

    def _digest(self, domain: bytes, *parts: bytes) -> str:
        message = domain + b"\0" + b"\0".join(parts)
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _scope_values(
        self,
        request_id: str,
        owner_principal: str,
        project_instance: str,
        operation: str,
        payload: Any,
    ) -> tuple[str, str, str]:
        values = (owner_principal, project_instance, operation)
        if any(not isinstance(value, str) or not value for value in values):
            raise ReferenceAdmissionValidationError(
                "Reference admission scope is incomplete."
            )
        if any(len(value.encode("utf-8")) > 1024 for value in values):
            raise ReferenceAdmissionValidationError(
                "Reference admission scope exceeds the safety limit."
            )
        scope_digest = self._digest(
            b"reference-admission-scope-v1",
            owner_principal.encode("utf-8"),
            project_instance.encode("utf-8"),
            operation.encode("utf-8"),
        )
        request_key = self._digest(
            b"reference-admission-request-v1",
            scope_digest.encode("ascii"),
            request_id.encode("ascii"),
        )
        payload_digest = self._digest(
            b"reference-admission-payload-v1",
            scope_digest.encode("ascii"),
            _canonical_payload_bytes(payload),
        )
        return request_key, scope_digest, payload_digest

    def _record_path(self, request_key: str) -> Path:
        return self.root / f"{request_key}.json"

    def _integrity_digest(self, record: Mapping[str, Any]) -> str:
        unsigned = dict(record)
        unsigned.pop("integrity", None)
        encoded = json.dumps(
            unsigned,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return self._digest(b"reference-admission-integrity-v1", encoded)

    @contextmanager
    def _record_lock(self, request_key: str) -> Iterator[None]:
        lock_path = self.root / f".{request_key}.lock"
        deadline = time.monotonic() + 3.0
        descriptor = None
        while descriptor is None:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                try:
                    age = self._clock() - lock_path.stat().st_mtime
                    if age > 2 * self.lease_seconds:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise ReferenceAdmissionPersistenceError(
                        "Reference admission lock is unavailable."
                    ) from error
                if time.monotonic() >= deadline:
                    raise ReferenceAdmissionPersistenceError(
                        "Reference admission is temporarily busy."
                    )
                time.sleep(0.01)
            except OSError as error:
                raise ReferenceAdmissionPersistenceError(
                    "Reference admission lock is unavailable."
                ) from error
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            yield
        finally:
            try:
                os.close(descriptor)
            finally:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    # A stale empty lock is bounded and recoverable by age.
                    pass

    def _validate_opaque_id(self, value: str, field: str) -> str:
        if not isinstance(value, str) or _OPAQUE_ID_RE.fullmatch(value) is None:
            raise ReferenceAdmissionValidationError(
                f"Reference admission {field} must be opaque."
            )
        return value

    def _validate_record(self, value: Any) -> dict[str, Any]:
        if type(value) is not dict or set(value) != {
            "schema", "state", "request_key", "scope_digest",
            "payload_digest", "job_id", "asset_id", "lease_digest",
            "created_at", "updated_at", "expires_at", "lease_expires_at",
            "integrity",
        }:
            raise ReferenceAdmissionCorruptionError(
                "Reference admission state is corrupt."
            )
        if value.get("schema") != SCHEMA_VERSION:
            raise ReferenceAdmissionCorruptionError(
                "Reference admission state is corrupt."
            )
        if value.get("state") not in {"reserved", "accepted", "failed"}:
            raise ReferenceAdmissionCorruptionError(
                "Reference admission state is corrupt."
            )
        for field in (
            "request_key", "scope_digest", "payload_digest", "lease_digest",
            "integrity",
        ):
            if _DIGEST_RE.fullmatch(str(value.get(field) or "")) is None:
                raise ReferenceAdmissionCorruptionError(
                    "Reference admission state is corrupt."
                )
        for field in ("job_id", "asset_id"):
            try:
                self._validate_opaque_id(value.get(field), field)
            except ReferenceAdmissionValidationError:
                raise ReferenceAdmissionCorruptionError(
                    "Reference admission state is corrupt."
                ) from None
        for field in (
            "created_at", "updated_at", "expires_at", "lease_expires_at",
        ):
            number = value.get(field)
            if type(number) not in {int, float} or not math.isfinite(number):
                raise ReferenceAdmissionCorruptionError(
                    "Reference admission state is corrupt."
                )
        if not (
            value["created_at"] <= value["updated_at"] <= value["expires_at"]
        ):
            raise ReferenceAdmissionCorruptionError(
                "Reference admission state is corrupt."
            )
        expected_integrity = self._integrity_digest(value)
        if not hmac.compare_digest(value["integrity"], expected_integrity):
            raise ReferenceAdmissionCorruptionError(
                "Reference admission state failed its integrity check."
            )
        return dict(value)

    def _read_record(self, path: Path) -> dict[str, Any] | None:
        try:
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ReferenceAdmissionPersistenceError(
                "Reference admission state is unavailable."
            ) from error
        try:
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            opened = os.fstat(descriptor)
            named = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                or (os.name != "nt" and mode != 0o600)
            ):
                raise ReferenceAdmissionCorruptionError(
                    "Reference admission state has unsafe permissions."
                )
            raw = os.read(descriptor, MAX_RECORD_BYTES + 1)
            if len(raw) > MAX_RECORD_BYTES:
                raise ReferenceAdmissionCorruptionError(
                    "Reference admission state is corrupt."
                )
        finally:
            os.close(descriptor)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ReferenceAdmissionCorruptionError(
                "Reference admission state is corrupt."
            ) from None
        return self._validate_record(value)

    def _fsync_root(self) -> None:
        if os.name == "nt":
            # Python exposes no durable directory fsync on Windows. File
            # contents are still fsynced before the atomic replacement.
            return
        try:
            descriptor = os.open(
                self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise ReferenceAdmissionPersistenceError(
                "Reference admission directory could not be synchronized."
            ) from error

    def _write_record(self, path: Path, record: Mapping[str, Any], *, create: bool) -> None:
        document = dict(record)
        document["integrity"] = self._integrity_digest(document)
        encoded = json.dumps(
            document, allow_nan=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ReferenceAdmissionPersistenceError(
                "Reference admission record exceeds its safety limit."
            )
        target = path
        if not create:
            target = self.root / f".{path.name}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(target, flags, 0o600)
            try:
                if os.name != "nt":
                    os.fchmod(descriptor, 0o600)
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short admission write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if not create:
                os.replace(target, path)
            self._fsync_root()
        except FileExistsError:
            raise ReferenceAdmissionPersistenceError(
                "Reference admission record changed concurrently."
            ) from None
        except OSError as error:
            raise ReferenceAdmissionPersistenceError(
                "Reference admission state could not be committed."
            ) from error
        finally:
            if target != path:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def _purge_expired_unlocked(self, now: float) -> int:
        live = 0
        scanned = 0
        try:
            entries = tuple(self.root.iterdir())
        except OSError as error:
            raise ReferenceAdmissionPersistenceError(
                "Reference admission state is unavailable."
            ) from error
        for path in entries:
            if scanned >= self.max_records + 256:
                raise ReferenceAdmissionCapacityError(
                    "Reference admission record bound is exhausted."
                )
            if not path.name.endswith(".json"):
                continue
            scanned += 1
            record = self._read_record(path)
            if record is None:
                continue
            if record["expires_at"] <= now:
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise ReferenceAdmissionPersistenceError(
                        "Expired Reference admission could not be removed."
                    ) from error
            else:
                live += 1
        return live

    def begin(
        self,
        request_id: str,
        *,
        owner_principal: str,
        project_instance: str,
        operation: str,
        payload: Any,
        proposed_job_id: str,
        proposed_asset_id: str,
    ) -> AdmissionReservation:
        """Reserve or replay one payload-bound response identity."""
        normalized = normalize_request_id(request_id)
        if normalized is None:
            raise ReferenceAdmissionValidationError(
                "Reference admission request_id is required."
            )
        proposed_job_id = self._validate_opaque_id(proposed_job_id, "job_id")
        proposed_asset_id = self._validate_opaque_id(
            proposed_asset_id, "asset_id"
        )
        request_key, scope_digest, payload_digest = self._scope_values(
            normalized, owner_principal, project_instance, operation, payload,
        )
        path = self._record_path(request_key)
        now = float(self._clock())
        with self._record_lock(request_key):
            record = self._read_record(path)
            if record is not None and record["expires_at"] <= now:
                try:
                    path.unlink()
                    self._fsync_root()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    raise ReferenceAdmissionPersistenceError(
                        "Expired Reference admission could not be replaced."
                    ) from error
                record = None
            if record is None:
                lease_token = secrets.token_urlsafe(32)
                record = {
                    "schema": SCHEMA_VERSION,
                    "state": "reserved",
                    "request_key": request_key,
                    "scope_digest": scope_digest,
                    "payload_digest": payload_digest,
                    "job_id": proposed_job_id,
                    "asset_id": proposed_asset_id,
                    "lease_digest": self._digest(
                        b"reference-admission-lease-v1",
                        lease_token.encode("ascii"),
                    ),
                    "created_at": now,
                    "updated_at": now,
                    "expires_at": now + self.ttl_seconds,
                    "lease_expires_at": now + self.lease_seconds,
                }
                # Distinct request keys have distinct locks.  This second,
                # fixed lock serializes the directory-wide bound without
                # weakening the per-key create fence.
                with self._record_lock("capacity"):
                    if self._purge_expired_unlocked(now) >= self.max_records:
                        raise ReferenceAdmissionCapacityError(
                            "Reference admission record bound is exhausted."
                        )
                    self._write_record(path, record, create=True)
                return AdmissionReservation(
                    "new", proposed_job_id, proposed_asset_id, lease_token,
                )

            if (
                not hmac.compare_digest(record["request_key"], request_key)
                or not hmac.compare_digest(record["scope_digest"], scope_digest)
                or not hmac.compare_digest(record["payload_digest"], payload_digest)
            ):
                raise ReferenceAdmissionMismatchError(
                    "Reference request_id is already bound to another request."
                )
            if record["state"] == "accepted":
                return AdmissionReservation(
                    "replay", record["job_id"], record["asset_id"], None,
                )
            if record["state"] == "failed":
                return AdmissionReservation(
                    "failed", record["job_id"], record["asset_id"], None,
                )
            if record["lease_expires_at"] > now:
                return AdmissionReservation(
                    "pending", record["job_id"], record["asset_id"], None,
                )

            lease_token = secrets.token_urlsafe(32)
            record.update({
                "lease_digest": self._digest(
                    b"reference-admission-lease-v1",
                    lease_token.encode("ascii"),
                ),
                "updated_at": now,
                "expires_at": now + self.ttl_seconds,
                "lease_expires_at": now + self.lease_seconds,
            })
            self._write_record(path, record, create=False)
            return AdmissionReservation(
                "resume", record["job_id"], record["asset_id"], lease_token,
            )

    def _finish(
        self,
        request_id: str,
        *,
        owner_principal: str,
        project_instance: str,
        operation: str,
        payload: Any,
        lease_token: str,
        state: str,
    ) -> AdmissionReservation:
        normalized = normalize_request_id(request_id)
        if normalized is None or not isinstance(lease_token, str):
            raise ReferenceAdmissionValidationError(
                "Reference admission lease is incomplete."
            )
        request_key, scope_digest, payload_digest = self._scope_values(
            normalized, owner_principal, project_instance, operation, payload,
        )
        path = self._record_path(request_key)
        now = float(self._clock())
        lease_digest = self._digest(
            b"reference-admission-lease-v1", lease_token.encode("ascii"),
        )
        with self._record_lock(request_key):
            record = self._read_record(path)
            if record is None:
                raise ReferenceAdmissionPersistenceError(
                    "Reference admission reservation is missing."
                )
            if (
                not hmac.compare_digest(record["request_key"], request_key)
                or not hmac.compare_digest(record["scope_digest"], scope_digest)
                or not hmac.compare_digest(record["payload_digest"], payload_digest)
            ):
                raise ReferenceAdmissionMismatchError(
                    "Reference request_id is already bound to another request."
                )
            if record["state"] == "accepted":
                return AdmissionReservation(
                    "replay", record["job_id"], record["asset_id"], None,
                )
            if (
                record["state"] != "reserved"
                or not hmac.compare_digest(record["lease_digest"], lease_digest)
            ):
                raise ReferenceAdmissionPersistenceError(
                    "Reference admission lease is no longer authoritative."
                )
            record.update({
                "state": state,
                "updated_at": now,
                "expires_at": now + self.ttl_seconds,
                "lease_expires_at": now,
            })
            self._write_record(path, record, create=False)
            return AdmissionReservation(
                state, record["job_id"], record["asset_id"], None,
            )

    def accept(self, request_id: str, **scope: Any) -> AdmissionReservation:
        """Durably publish the canonical opaque acceptance identity."""
        return self._finish(request_id, state="accepted", **scope)

    def fail(self, request_id: str, **scope: Any) -> AdmissionReservation:
        """Fail closed after an unrecoverable post-reservation admission error."""
        return self._finish(request_id, state="failed", **scope)

    def inspect(
        self,
        request_id: str,
        *,
        owner_principal: str,
        project_instance: str,
        operation: str,
        payload: Any,
    ) -> AdmissionReservation | None:
        """Read one matching disposition for bounded same-process polling."""
        normalized = normalize_request_id(request_id)
        if normalized is None:
            return None
        request_key, scope_digest, payload_digest = self._scope_values(
            normalized, owner_principal, project_instance, operation, payload,
        )
        with self._record_lock(request_key):
            record = self._read_record(self._record_path(request_key))
        if record is None:
            return None
        if (
            not hmac.compare_digest(record["request_key"], request_key)
            or not hmac.compare_digest(record["scope_digest"], scope_digest)
            or not hmac.compare_digest(record["payload_digest"], payload_digest)
        ):
            raise ReferenceAdmissionMismatchError(
                "Reference request_id is already bound to another request."
            )
        disposition = {
            "reserved": "pending", "accepted": "replay", "failed": "failed",
        }[record["state"]]
        return AdmissionReservation(
            disposition, record["job_id"], record["asset_id"], None,
        )
