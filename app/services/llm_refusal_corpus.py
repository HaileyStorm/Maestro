"""Private, host-wide storage for exact local response-refusal literals.

The corpus contains only owner-submitted literal text.  It is never exposed by
an API, interpreted as a regular expression, or expanded semantically.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import threading
import uuid


CORPUS_SCHEMA = 1
MAX_LEARNED_LITERALS = 16
MAX_LITERAL_CHARS = 256
_MAX_CORPUS_BYTES = 32 * 1024
_CORPUS_KEYS = frozenset({"schema", "revision", "literals"})


class RefusalCorpusError(Exception):
    """Base error for corpus validation or durable storage failures."""


class RefusalCorpusValidationError(RefusalCorpusError):
    """The requested literal cannot be stored under the bounded contract."""


class RefusalCorpusStorageError(RefusalCorpusError):
    """The private corpus could not be read or durably updated."""


@dataclass(frozen=True)
class RefusalCorpusSnapshot:
    """One immutable request-scoped corpus view."""

    revision: int = 0
    literals: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.literals)


@dataclass(frozen=True)
class RefusalCorpusUpdate:
    snapshot: RefusalCorpusSnapshot
    added: bool


EMPTY_REFUSAL_CORPUS = RefusalCorpusSnapshot()


def _validated_literal(value) -> str:
    if not isinstance(value, str):
        raise RefusalCorpusValidationError("literal must be text")
    if not value or len(value) > MAX_LITERAL_CHARS:
        raise RefusalCorpusValidationError("literal length is invalid")
    if not any(not character.isspace() for character in value):
        raise RefusalCorpusValidationError("literal must contain text")
    if any(
        (ord(character) < 32 and character not in "\t\n\r")
        or 127 <= ord(character) <= 159
        for character in value
    ):
        raise RefusalCorpusValidationError("literal contains a control character")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise RefusalCorpusValidationError("literal is not valid UTF-8 text") from error
    return value


def _decode_snapshot(raw: bytes) -> RefusalCorpusSnapshot:
    if len(raw) > _MAX_CORPUS_BYTES:
        raise RefusalCorpusStorageError("corpus is too large")

    def object_without_duplicate_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise RefusalCorpusStorageError("corpus keys are duplicated")
            value[key] = item
        return value

    try:
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=object_without_duplicate_keys,
        )
    except RefusalCorpusStorageError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RefusalCorpusStorageError("corpus is malformed") from error
    if not isinstance(document, dict) or set(document) != _CORPUS_KEYS:
        raise RefusalCorpusStorageError("corpus schema is invalid")
    schema = document.get("schema")
    if (
        isinstance(schema, bool)
        or not isinstance(schema, int)
        or schema != CORPUS_SCHEMA
    ):
        raise RefusalCorpusStorageError("corpus schema is unsupported")
    revision = document.get("revision")
    literals = document.get("literals")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 0 <= revision <= (2**63 - 1)
        or not isinstance(literals, list)
        or len(literals) > MAX_LEARNED_LITERALS
    ):
        raise RefusalCorpusStorageError("corpus values are invalid")
    try:
        validated = tuple(_validated_literal(value) for value in literals)
    except RefusalCorpusValidationError as error:
        raise RefusalCorpusStorageError("corpus literal is invalid") from error
    folded = [literal.casefold() for literal in validated]
    if len(set(folded)) != len(folded):
        raise RefusalCorpusStorageError("corpus literals are duplicated")
    return RefusalCorpusSnapshot(revision=revision, literals=validated)


class RefusalCorpusStore:
    """RLock-serialized exact-literal corpus with durable atomic updates."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._published: RefusalCorpusSnapshot | None = None

    def _read_regular_file_locked(self) -> bytes:
        try:
            directory = os.lstat(self.path.parent)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise RefusalCorpusStorageError("corpus directory is unavailable") from error
        if stat.S_ISLNK(directory.st_mode) or not stat.S_ISDIR(directory.st_mode):
            raise RefusalCorpusStorageError("corpus directory is unsafe")
        if os.name != "nt" and stat.S_IMODE(directory.st_mode) != 0o700:
            raise RefusalCorpusStorageError("corpus directory is not private")
        try:
            before = os.lstat(self.path)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise RefusalCorpusStorageError("corpus metadata is unavailable") from error
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RefusalCorpusStorageError("corpus is not a regular file")
        if os.name != "nt" and stat.S_IMODE(before.st_mode) != 0o600:
            raise RefusalCorpusStorageError("corpus file is not private")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError as error:
            raise RefusalCorpusStorageError("corpus cannot be opened safely") from error
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size > _MAX_CORPUS_BYTES
            ):
                raise RefusalCorpusStorageError("corpus file changed or is invalid")
            chunks: list[bytes] = []
            remaining = _MAX_CORPUS_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > _MAX_CORPUS_BYTES:
                raise RefusalCorpusStorageError("corpus is too large")
            return raw
        finally:
            os.close(descriptor)

    def _load_locked(self, *, mutation: bool) -> RefusalCorpusSnapshot:
        try:
            return _decode_snapshot(self._read_regular_file_locked())
        except FileNotFoundError:
            return EMPTY_REFUSAL_CORPUS
        except Exception as error:
            if mutation:
                if isinstance(error, RefusalCorpusStorageError):
                    raise
                raise RefusalCorpusStorageError(
                    "corpus could not be loaded safely",
                ) from error
            return EMPTY_REFUSAL_CORPUS

    def snapshot(self) -> RefusalCorpusSnapshot:
        """Return the published corpus, failing open on unsafe disk state."""
        with self._lock:
            # Revalidate disk identity and contents every time. The file is
            # tiny and atomically replaced, so this immediately observes an
            # external delete, symlink, malformed replacement, or repair.
            self._published = self._load_locked(mutation=False)
            return self._published

    def _ensure_private_directory_locked(self) -> Path:
        directory = self.path.parent
        created = False
        try:
            try:
                os.lstat(directory)
            except FileNotFoundError:
                created = True
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = os.lstat(directory)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RefusalCorpusStorageError("corpus directory is unsafe")
            if os.name != "nt":
                os.chmod(directory, 0o700)
            if created:
                self._fsync_directory(directory.parent)
        except RefusalCorpusStorageError:
            raise
        except OSError as error:
            raise RefusalCorpusStorageError("corpus directory is unavailable") from error
        return directory

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _cross_process_update_lock_locked(self):
        """Serialize the read/modify/write cycle across Maestro processes."""
        directory = self._ensure_private_directory_locked()
        lock_path = directory / ".corpus.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RefusalCorpusStorageError("corpus lock is unsafe")
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            else:
                import msvcrt

                if metadata.st_size < 1:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            yield
        except RefusalCorpusStorageError:
            raise
        except OSError as error:
            raise RefusalCorpusStorageError("corpus lock is unavailable") from error
        finally:
            if descriptor is not None:
                try:
                    if os.name != "nt":
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    else:
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                os.close(descriptor)

    @staticmethod
    def _encoded(snapshot: RefusalCorpusSnapshot) -> bytes:
        document = {
            "schema": CORPUS_SCHEMA,
            "revision": snapshot.revision,
            "literals": list(snapshot.literals),
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
        if len(encoded) > _MAX_CORPUS_BYTES:
            raise RefusalCorpusStorageError("corpus is too large")
        return encoded

    def _write_locked(self, snapshot: RefusalCorpusSnapshot) -> None:
        directory = self._ensure_private_directory_locked()
        encoded = self._encoded(snapshot)
        temporary = directory / f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = None
        try:
            descriptor = os.open(temporary, flags, 0o600)
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise RefusalCorpusStorageError("corpus write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None

            try:
                target = os.lstat(self.path)
            except FileNotFoundError:
                target = None
            if target is not None and (
                stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode)
            ):
                raise RefusalCorpusStorageError("corpus target is unsafe")
            os.replace(temporary, self.path)

            self._fsync_directory(directory)
        except RefusalCorpusStorageError:
            raise
        except (OSError, UnicodeError) as error:
            raise RefusalCorpusStorageError("corpus update was not durable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def add_literal(self, value) -> RefusalCorpusUpdate:
        """Durably add one exact literal, preserving its original text."""
        literal = _validated_literal(value)
        with self._lock:
            with self._cross_process_update_lock_locked():
                current = self._load_locked(mutation=True)
                folded = literal.casefold()
                if any(existing.casefold() == folded for existing in current.literals):
                    self._published = current
                    return RefusalCorpusUpdate(snapshot=current, added=False)
                if len(current.literals) >= MAX_LEARNED_LITERALS:
                    raise RefusalCorpusValidationError("literal capacity is reached")
                if current.revision >= 2**63 - 1:
                    raise RefusalCorpusStorageError("corpus revision is exhausted")
                updated = RefusalCorpusSnapshot(
                    revision=current.revision + 1,
                    literals=current.literals + (literal,),
                )
                self._write_locked(updated)
                # Publish only after file + directory fsync complete successfully.
                self._published = updated
                return RefusalCorpusUpdate(snapshot=updated, added=True)


_DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "storage"
    / "llm-refusal-corpus"
    / "corpus.json"
)
_default_store = RefusalCorpusStore(_DEFAULT_CORPUS_PATH)


def refusal_corpus_snapshot() -> RefusalCorpusSnapshot:
    """Freeze the current host corpus for one inference operation."""
    return _default_store.snapshot()


def add_refusal_literal(value) -> RefusalCorpusUpdate:
    """Durably add one owner-confirmed exact literal to the host corpus."""
    return _default_store.add_literal(value)
