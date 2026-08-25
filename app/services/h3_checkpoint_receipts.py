"""Owner-local integrity receipts for immutable MiniMax H3 transformers.

The receipt is deliberately content-free: it stores only a digest of the
canonical path plus the stat and immutable contract needed to decide whether
the expensive content hash may be reused.  It is not a catalog or a runtime
selection mechanism.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Iterator


CHECKPOINT_RECEIPT_SCHEMA_VERSION = 1
CHECKPOINT_CONTRACT_REVISION = "minimax-h3-transformer-integrity-v1"
CHECKPOINT_FAMILY = "minimax_h3"
CHECKPOINT_ROLE = "transformer"
CHECKPOINT_RECEIPT_MAX_BYTES = 16 * 1024
CHECKPOINT_LOCK_TIMEOUT_SECONDS = 30.0

_APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECEIPT_ROOT = _APP_ROOT / "cache" / "h3_checkpoint_integrity"
_HEX_DIGEST_LENGTH = 64
_CHUNK_BYTES = 8 * 1024 * 1024


class H3CheckpointIntegrityError(RuntimeError):
    """The selected H3 transformer or its private receipt is unsafe."""


def _ns(stat_result: os.stat_result, field: str) -> int:
    value = getattr(stat_result, field, None)
    if value is not None:
        return int(value)
    seconds_field = field.removesuffix("_ns")
    return int(float(getattr(stat_result, seconds_field)) * 1_000_000_000)


def _uid(stat_result: os.stat_result) -> int:
    return int(getattr(stat_result, "st_uid", -1))


def _identity(stat_result: os.stat_result) -> dict[str, int]:
    return {
        "dev": int(stat_result.st_dev),
        "ino": int(stat_result.st_ino),
        "size": int(stat_result.st_size),
        "mtime_ns": _ns(stat_result, "st_mtime_ns"),
        "ctime_ns": _ns(stat_result, "st_ctime_ns"),
        "uid": _uid(stat_result),
    }


def _same_owner(stat_result: os.stat_result) -> bool:
    return os.name != "posix" or _uid(stat_result) == os.geteuid()


def _entry_matches(path: str, identity: dict[str, int]) -> bool:
    try:
        entry_stat = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISREG(entry_stat.st_mode)
        and _same_owner(entry_stat)
        and _identity(entry_stat) == identity
    )


def _path_digest(path: str) -> str:
    return hashlib.sha256(os.fsencode(os.path.normcase(path))).hexdigest()


def _safe_component(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or any(not (character.isalnum() or character in "-_.") for character in value)
    ):
        raise H3CheckpointIntegrityError(
            f"H3 checkpoint {field} is invalid"
        )
    return value


def _normalize_sha256(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if len(normalized) != _HEX_DIGEST_LENGTH or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise H3CheckpointIntegrityError("H3 checkpoint SHA256 is invalid")
    return normalized


def _ensure_private_root(root: Path) -> None:
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_stat = root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or not _same_owner(root_stat):
            raise H3CheckpointIntegrityError(
                "H3 checkpoint receipt root is unsafe"
            )
        os.chmod(root, 0o700)
        checked = root.lstat()
        if stat.S_IMODE(checked.st_mode) != 0o700:
            raise H3CheckpointIntegrityError(
                "H3 checkpoint receipt root is not private"
            )
    except H3CheckpointIntegrityError:
        raise
    except OSError as error:
        raise H3CheckpointIntegrityError(
            "H3 checkpoint receipt root is unavailable"
        ) from error


def _open_regular_nofollow(path: str) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise H3CheckpointIntegrityError(
                "This host cannot safely verify H3 checkpoint links"
            )
        flags |= nofollow
    try:
        before_lstat = os.lstat(path)
        if stat.S_ISLNK(before_lstat.st_mode):
            raise H3CheckpointIntegrityError(
                "The selected H3 checkpoint cannot be a symbolic link"
            )
        descriptor = os.open(path, flags)
    except H3CheckpointIntegrityError:
        raise
    except OSError as error:
        raise H3CheckpointIntegrityError(
            "The selected H3 checkpoint is unavailable"
        ) from error
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or not _same_owner(opened_stat)
            or _identity(before_lstat) != _identity(opened_stat)
        ):
            raise H3CheckpointIntegrityError(
                "The selected H3 checkpoint is not a stable owner file"
            )
        return descriptor, opened_stat
    except BaseException:
        os.close(descriptor)
        raise


def _open_private_file(path: Path, flags: int, mode: int = 0o600) -> int:
    requested_flags = flags | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise H3CheckpointIntegrityError(
                "This host cannot safely open H3 checkpoint receipts"
            )
        requested_flags |= nofollow
    try:
        descriptor = os.open(path, requested_flags, mode)
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or not _same_owner(file_stat)
            or stat.S_IMODE(file_stat.st_mode) != 0o600
        ):
            raise H3CheckpointIntegrityError(
                "H3 checkpoint receipt state is unsafe"
            )
        return descriptor
    except BaseException:
        descriptor_to_close = locals().get("descriptor")
        if isinstance(descriptor_to_close, int):
            os.close(descriptor_to_close)
        raise


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    descriptor = _open_private_file(path, os.O_RDWR | os.O_CREAT)
    deadline = time.monotonic() + CHECKPOINT_LOCK_TIMEOUT_SECONDS
    locked = False
    try:
        if os.name == "posix":
            try:
                import fcntl
            except ImportError as error:  # pragma: no cover - platform guard
                raise H3CheckpointIntegrityError(
                    "This host cannot serialize H3 checkpoint verification"
                ) from error
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise H3CheckpointIntegrityError(
                            "Timed out waiting for H3 checkpoint verification"
                        )
                    time.sleep(0.05)
        elif os.name == "nt":  # pragma: no cover - exercised on Windows
            try:
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                while True:
                    try:
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise H3CheckpointIntegrityError(
                                "Timed out waiting for H3 checkpoint verification"
                            )
                        time.sleep(0.05)
            except ImportError as error:
                raise H3CheckpointIntegrityError(
                    "This host cannot serialize H3 checkpoint verification"
                ) from error
        else:  # pragma: no cover - fail closed on unsupported hosts
            raise H3CheckpointIntegrityError(
                "This host cannot serialize H3 checkpoint verification"
            )
        yield
    finally:
        if locked:
            try:
                if os.name == "posix":
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                elif os.name == "nt":  # pragma: no cover - Windows only
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(descriptor)


def _receipt_record(
    *,
    path_digest: str,
    identity: dict[str, int],
    expected_sha256: str,
    expected_size: int,
    family: str,
    role: str,
) -> dict[str, object]:
    return {
        "schema_version": CHECKPOINT_RECEIPT_SCHEMA_VERSION,
        "contract_revision": CHECKPOINT_CONTRACT_REVISION,
        "family": family,
        "role": role,
        "expected_sha256": expected_sha256,
        "expected_size": expected_size,
        "path_digest": path_digest,
        **identity,
    }


_RECEIPT_KEYS = {
    "schema_version", "contract_revision", "family", "role",
    "expected_sha256", "expected_size", "path_digest", "dev", "ino",
    "size", "mtime_ns", "ctime_ns", "uid",
}


def _load_receipt(
    path: Path, expected: dict[str, object], *, remove_invalid: bool = True,
) -> bool:
    try:
        descriptor = _open_private_file(path, os.O_RDONLY)
        try:
            serialized = os.read(descriptor, CHECKPOINT_RECEIPT_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(serialized) > CHECKPOINT_RECEIPT_MAX_BYTES:
            raise ValueError("oversized receipt")
        receipt = json.loads(serialized.decode("utf-8"))
        if (
            not isinstance(receipt, dict)
            or set(receipt) != _RECEIPT_KEYS
            or not all(type(receipt[key]) is int for key in (
                "schema_version", "expected_size", "dev", "ino", "size",
                "mtime_ns", "ctime_ns", "uid",
            ))
            or not all(isinstance(receipt[key], str) for key in (
                "contract_revision", "family", "role", "expected_sha256",
                "path_digest",
            ))
            or receipt != expected
        ):
            raise ValueError("stale receipt")
        return True
    except FileNotFoundError:
        return False
    except (H3CheckpointIntegrityError, OSError, UnicodeError, ValueError):
        if remove_invalid:
            try:
                path.unlink()
            except OSError:
                pass
        return False


def _private_binding(
    *, path_digest: str, identity: dict[str, int], expected_sha256: str,
    expected_size: int, family: str, role: str,
) -> dict[str, object]:
    return _receipt_record(
        path_digest=path_digest,
        identity=identity,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        family=family,
        role=role,
    )


def _public_projection(
    *, expected_sha256: str, expected_size: int, compatibility: str,
    family: str, role: str, receipt_reused: bool,
) -> dict[str, object]:
    return {
        "verified": True,
        "sha256": expected_sha256,
        "size": expected_size,
        "family": family,
        "role": role,
        "contract_revision": CHECKPOINT_CONTRACT_REVISION,
        "compatibility": compatibility,
        "receipt_reused": receipt_reused,
    }


def _stream_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, _CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _store_receipt(root: Path, receipt_path: Path, record: dict[str, object]) -> None:
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".checkpoint-", suffix=".tmp", dir=root,
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        os.replace(temporary_path, receipt_path)
        temporary_path = None
        os.chmod(receipt_path, 0o600)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(root, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise H3CheckpointIntegrityError(
            "Could not persist the H3 checkpoint receipt"
        ) from error
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def verify_checkpoint_integrity(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str,
    expected_size: int,
    compatibility: str,
    family: str = CHECKPOINT_FAMILY,
    role: str = CHECKPOINT_ROLE,
    receipt_root: str | os.PathLike[str] | None = None,
    include_private_binding: bool = False,
) -> dict[str, object]:
    """Verify once, then reuse an exact private stat-bound receipt.

    The returned projection is safe for public status payloads and contains no
    path, device, inode, uid, or timestamp fields.
    """
    family = _safe_component(family, field="family")
    role = _safe_component(role, field="role")
    compatibility = _safe_component(compatibility, field="compatibility")
    expected_sha256 = _normalize_sha256(expected_sha256)
    if type(expected_size) is not int or expected_size <= 0:
        raise H3CheckpointIntegrityError("H3 checkpoint size is invalid")

    supplied_path = os.path.abspath(os.fspath(path))
    descriptor, opened_stat = _open_regular_nofollow(supplied_path)
    try:
        canonical_path = os.path.realpath(supplied_path)
        if opened_stat.st_size != expected_size:
            raise H3CheckpointIntegrityError(
                "The selected H3 checkpoint size does not match its contract"
            )
        path_digest = _path_digest(canonical_path)
        root = Path(receipt_root) if receipt_root is not None else DEFAULT_RECEIPT_ROOT
        _ensure_private_root(root)
        receipt_path = root / f"{path_digest}.json"
        lock_path = root / f"{path_digest}.lock"

        with _exclusive_lock(lock_path):
            current_stat = os.fstat(descriptor)
            current_identity = _identity(current_stat)
            expected_record = _receipt_record(
                path_digest=path_digest,
                identity=current_identity,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                family=family,
                role=role,
            )
            reused = _load_receipt(receipt_path, expected_record)
            if reused and (
                _identity(os.fstat(descriptor)) != current_identity
                or not _entry_matches(supplied_path, current_identity)
            ):
                raise H3CheckpointIntegrityError(
                    "The selected H3 checkpoint changed during receipt reuse"
                )
            if not reused:
                before_identity = current_identity
                actual_sha256 = _stream_sha256(descriptor)
                after_stat = os.fstat(descriptor)
                after_identity = _identity(after_stat)
                if (
                    before_identity != after_identity
                    or after_stat.st_size != expected_size
                    or actual_sha256 != expected_sha256
                    or not _entry_matches(supplied_path, after_identity)
                ):
                    raise H3CheckpointIntegrityError(
                        "The selected H3 checkpoint failed integrity verification"
                    )
                expected_record = _receipt_record(
                    path_digest=path_digest,
                    identity=after_identity,
                    expected_sha256=expected_sha256,
                    expected_size=expected_size,
                    family=family,
                    role=role,
                )
                _store_receipt(root, receipt_path, expected_record)
        result = _public_projection(
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            compatibility=compatibility,
            family=family,
            role=role,
            receipt_reused=reused,
        )
        if include_private_binding:
            result["_checkpoint_binding"] = _private_binding(
                path_digest=path_digest,
                identity=_identity(os.fstat(descriptor)),
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                family=family,
                role=role,
            )
        return result
    finally:
        os.close(descriptor)


def inspect_checkpoint_receipt(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str,
    expected_size: int,
    compatibility: str,
    family: str = CHECKPOINT_FAMILY,
    role: str = CHECKPOINT_ROLE,
    receipt_root: str | os.PathLike[str] | None = None,
) -> dict[str, object] | None:
    """Inspect an existing receipt without creating state or hashing content."""
    family = _safe_component(family, field="family")
    role = _safe_component(role, field="role")
    compatibility = _safe_component(compatibility, field="compatibility")
    expected_sha256 = _normalize_sha256(expected_sha256)
    if type(expected_size) is not int or expected_size <= 0:
        raise H3CheckpointIntegrityError("H3 checkpoint size is invalid")
    supplied_path = os.path.abspath(os.fspath(path))
    descriptor, opened_stat = _open_regular_nofollow(supplied_path)
    try:
        if opened_stat.st_size != expected_size:
            return None
        canonical_path = os.path.realpath(supplied_path)
        path_digest = _path_digest(canonical_path)
        root = Path(receipt_root) if receipt_root is not None else DEFAULT_RECEIPT_ROOT
        try:
            root_stat = root.lstat()
        except OSError:
            return None
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or not _same_owner(root_stat)
            or stat.S_IMODE(root_stat.st_mode) != 0o700
        ):
            return None
        identity = _identity(opened_stat)
        expected = _receipt_record(
            path_digest=path_digest,
            identity=identity,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            family=family,
            role=role,
        )
        if not _load_receipt(
            root / f"{path_digest}.json", expected, remove_invalid=False,
        ):
            return None
        if (
            _identity(os.fstat(descriptor)) != identity
            or not _entry_matches(supplied_path, identity)
        ):
            return None
        return _public_projection(
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            compatibility=compatibility,
            family=family,
            role=role,
            receipt_reused=True,
        )
    finally:
        os.close(descriptor)


def recheck_checkpoint_binding(
    path: str | os.PathLike[str] | None,
    binding: object,
) -> bool:
    """Compare current path/stat identity to an admitted private binding."""
    if path is None or not isinstance(binding, dict) or set(binding) != _RECEIPT_KEYS:
        return False
    supplied_path = os.path.abspath(os.fspath(path))
    try:
        descriptor, opened_stat = _open_regular_nofollow(supplied_path)
    except H3CheckpointIntegrityError:
        return False
    try:
        current = _private_binding(
            path_digest=_path_digest(os.path.realpath(supplied_path)),
            identity=_identity(opened_stat),
            expected_sha256=str(binding.get("expected_sha256") or ""),
            expected_size=int(binding.get("expected_size") or 0),
            family=str(binding.get("family") or ""),
            role=str(binding.get("role") or ""),
        )
        return (
            current == binding
            and _entry_matches(supplied_path, _identity(opened_stat))
        )
    except (TypeError, ValueError):
        return False
    finally:
        os.close(descriptor)


__all__ = [
    "CHECKPOINT_CONTRACT_REVISION",
    "CHECKPOINT_FAMILY",
    "CHECKPOINT_ROLE",
    "DEFAULT_RECEIPT_ROOT",
    "H3CheckpointIntegrityError",
    "inspect_checkpoint_receipt",
    "recheck_checkpoint_binding",
    "verify_checkpoint_integrity",
]
