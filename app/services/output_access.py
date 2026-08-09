"""Project access-control and presentation-policy helpers for gallery outputs.

Project passwords protect Maestro's HTTP/UI surface while the machine owner
retains direct access to project storage.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping


SESSION_COOKIE_NAME = "maestro_session"
UPLOAD_ACCESS_SIDECAR_SUFFIX = ".access.json"
_UPLOAD_ACCESS_VERSION = 1
_PBKDF2_ITERATIONS = 310_000
MIN_PROJECT_PASSWORD_LENGTH = 8
_OUTPUT_SHARE_VERSION = 1
_OUTPUT_SHARE_TOKEN_DOMAIN = b"maestro-output-share-v1\0"


class OutputShareManager:
    """Durable, revocable capabilities for exactly one output revision.

    The persisted random identifier is not itself a bearer credential.  A
    valid token also needs an HMAC made with Maestro's owner-only session
    secret, so copying the JSON store cannot mint working public links.
    """

    def __init__(self, path: str, secret: bytes, clock=time.time) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("output-share secret must contain at least 32 bytes")
        self.path = os.path.abspath(path)
        self._secret = secret
        self._clock = clock
        self._lock = threading.RLock()

    def _signature(self, share_id: str) -> str:
        return hmac.new(
            self._secret,
            _OUTPUT_SHARE_TOKEN_DOMAIN + share_id.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def token_for(self, share_id: str) -> str:
        if (
            not isinstance(share_id, str)
            or len(share_id) != 64
            or any(character not in "0123456789abcdef" for character in share_id)
        ):
            raise ValueError("invalid output-share identifier")
        return f"{share_id}.{self._signature(share_id)}"

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, Mapping) or payload.get("version") != _OUTPUT_SHARE_VERSION:
            return {}
        raw_records = payload.get("shares")
        if not isinstance(raw_records, list):
            return {}
        records: dict[str, dict[str, Any]] = {}
        for raw in raw_records:
            if not isinstance(raw, Mapping):
                continue
            share_id = raw.get("id")
            workspace = raw.get("workspace")
            filename = raw.get("filename")
            revision = raw.get("revision")
            created_at = raw.get("created_at")
            revoked_at = raw.get("revoked_at")
            if (
                not isinstance(share_id, str)
                or len(share_id) != 64
                or any(character not in "0123456789abcdef" for character in share_id)
                or not isinstance(workspace, str)
                or not workspace
                or not isinstance(filename, str)
                or not filename
                or not isinstance(revision, str)
                or not revision
                or not isinstance(created_at, (int, float))
                or isinstance(created_at, bool)
                or (revoked_at is not None and (
                    not isinstance(revoked_at, (int, float))
                    or isinstance(revoked_at, bool)
                ))
            ):
                continue
            records[share_id] = {
                "id": share_id,
                "workspace": workspace,
                "filename": filename,
                "revision": revision,
                "created_at": float(created_at),
                "revoked_at": None if revoked_at is None else float(revoked_at),
                "media_type": str(raw.get("media_type") or "application/octet-stream"),
                "explicit": bool(raw.get("explicit", False)),
            }
        return records

    def _save(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        temp = f"{self.path}.{secrets.token_hex(4)}.tmp"
        descriptor = None
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = None
                json.dump({
                    "version": _OUTPUT_SHARE_VERSION,
                    "shares": sorted(records.values(), key=lambda item: item["created_at"]),
                }, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.remove(temp)
            except OSError:
                pass

    def create(
        self,
        *,
        workspace: str,
        filename: str,
        revision: str,
        media_type: str,
        explicit: bool,
    ) -> dict[str, Any]:
        """Create or return the stable active capability for this revision."""
        with self._lock:
            records = self._load()
            for record in records.values():
                if (
                    record["revoked_at"] is None
                    and record["workspace"] == workspace
                    and record["filename"] == filename
                    and record["revision"] == revision
                ):
                    return {**record, "token": self.token_for(record["id"])}
            now = float(self._clock())
            for record in records.values():
                if (
                    record["revoked_at"] is None
                    and record["workspace"] == workspace
                    and record["filename"] == filename
                ):
                    record["revoked_at"] = now
            share_id = secrets.token_hex(32)
            record = {
                "id": share_id,
                "workspace": workspace,
                "filename": filename,
                "revision": revision,
                "created_at": now,
                "revoked_at": None,
                "media_type": str(media_type or "application/octet-stream"),
                "explicit": bool(explicit),
            }
            records[share_id] = record
            self._save(records)
            return {**record, "token": self.token_for(share_id)}

    def resolve(self, token: str) -> dict[str, Any] | None:
        if not isinstance(token, str) or token.count(".") != 1:
            return None
        share_id, signature = token.split(".", 1)
        try:
            expected = self._signature(share_id)
        except (UnicodeEncodeError, AttributeError):
            return None
        if (
            len(share_id) != 64
            or len(signature) != 64
            or any(character not in "0123456789abcdef" for character in share_id + signature)
            or not hmac.compare_digest(signature, expected)
        ):
            return None
        with self._lock:
            record = self._load().get(share_id)
        if record is None or record["revoked_at"] is not None:
            return None
        return dict(record)

    def revoke(self, *, workspace: str, filename: str) -> int:
        with self._lock:
            records = self._load()
            now = float(self._clock())
            changed = 0
            for record in records.values():
                if (
                    record["revoked_at"] is None
                    and record["workspace"] == workspace
                    and record["filename"] == filename
                ):
                    record["revoked_at"] = now
                    changed += 1
            if changed:
                self._save(records)
            return changed

    def revoke_workspace(self, workspace: str) -> int:
        """Revoke every active capability rooted in one project.

        Project deletion must invalidate capabilities even if the same
        project name and filenames are recreated later.  Keeping this as a
        manager operation makes that lifecycle rule atomic with the durable
        capability store instead of relying on missing files to make links
        incidentally fail.
        """
        with self._lock:
            records = self._load()
            now = float(self._clock())
            changed = 0
            for record in records.values():
                if (
                    record["revoked_at"] is None
                    and record["workspace"] == workspace
                ):
                    record["revoked_at"] = now
                    changed += 1
            if changed:
                self._save(records)
            return changed


class ProjectUnlockRateLimiter:
    """Bound online project-password guessing per browser and per project."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._session_failures: dict[tuple[str, str], tuple[int, float]] = {}
        self._project_failures: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _delay(failures: int, free_attempts: int) -> int:
        if failures < free_attempts:
            return 0
        return min(300, 2 ** min(8, failures - free_attempts))

    def retry_after(self, project: str, session_id: str) -> int:
        now = self._clock()
        with self._lock:
            blocked_until = max(
                self._session_failures.get((project, session_id), (0, 0.0))[1],
                self._project_failures.get(project, (0, 0.0))[1],
            )
        return max(0, int(blocked_until - now + 0.999))

    def record_failure(self, project: str, session_id: str) -> int:
        now = self._clock()
        with self._lock:
            session_count = self._session_failures.get(
                (project, session_id), (0, 0.0),
            )[0] + 1
            project_count = self._project_failures.get(project, (0, 0.0))[0] + 1
            session_until = now + self._delay(session_count, 5)
            project_until = now + self._delay(project_count, 12)
            self._session_failures[(project, session_id)] = (
                session_count, session_until,
            )
            self._project_failures[project] = (project_count, project_until)
        return max(0, int(max(session_until, project_until) - now + 0.999))

    def record_success(self, project: str, session_id: str) -> None:
        with self._lock:
            self._session_failures.pop((project, session_id), None)
            self._project_failures.pop(project, None)


def load_or_create_session_secret(path: str) -> bytes:
    """Load a 256-bit signing key, creating it atomically and fail-closed."""

    def validated_secret() -> bytes:
        with open(path, "rb") as handle:
            value = handle.read()
        if len(value) < 32:
            raise RuntimeError(
                "Session signing secret is corrupt or shorter than 256 bits"
            )
        return value

    try:
        return validated_secret()
    except FileNotFoundError:
        pass

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    secret = secrets.token_bytes(32)
    temp = os.path.join(
        directory, f".{os.path.basename(path)}.{secrets.token_hex(8)}.tmp",
    )
    descriptor = None
    try:
        descriptor = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(secret)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Hard-link publication is an atomic create-if-absent operation:
            # concurrent starters never observe a zero-length secret and only
            # one fully-written candidate becomes authoritative.
            os.link(temp, path)
            return secret
        except FileExistsError:
            return validated_secret()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.remove(temp)
        except OSError:
            pass


def encode_session_cookie(session_id: str, secret: bytes) -> str:
    signature = hmac.new(secret, session_id.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{session_id}.{signature}"


def decode_session_cookie(value: str | None, secret: bytes) -> str | None:
    if not isinstance(value, str) or "." not in value:
        return None
    session_id, signature = value.split(".", 1)
    if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
        return None
    expected = hmac.new(secret, session_id.encode("ascii"), hashlib.sha256).hexdigest()
    return session_id if hmac.compare_digest(signature, expected) else None


def output_policy_from_request(
    params: MutableMapping[str, Any],
    *,
    owner_session_id: str,
    mature_output: bool = False,
    explicit_enabled: bool | None = None,
) -> dict[str, Any]:
    """Pop request-local flags and return the durable sidecar/job policy.

    ``explicit_enabled`` remains accepted for source compatibility but no
    longer supplies a default and never labels an omitted request explicit.

    ``mature_output`` remains accepted for source compatibility but is ignored:
    publication metadata comes only from the caller's explicit choices.
    """
    requested_private = params.pop("private_output", None)
    requested_explicit = params.pop("explicit_output", None)
    if requested_private is not None and not isinstance(requested_private, bool):
        raise ValueError("private_output must be a boolean")
    if requested_explicit is not None and not isinstance(requested_explicit, bool):
        raise ValueError("explicit_output must be a boolean")
    explicit = bool(requested_explicit) if requested_explicit is not None else False
    private = explicit if requested_private is None else requested_private
    return {
        "private": private,
        "explicit": explicit,
    }


def stamp_sidecar_policy(
    sidecar: MutableMapping[str, Any],
    policy: Mapping[str, Any],
    *,
    workspace: str,
) -> MutableMapping[str, Any]:
    """Stamp access metadata outside generation params (never restore it)."""
    sidecar["private"] = bool(policy.get("private", False))
    sidecar["explicit"] = bool(policy.get("explicit", False))
    # Private is durable presentation metadata: authorized project members
    # see the same output but start with its preview blurred.  Older builds
    # stamped a browser-session owner here; remove that obsolete field on
    # every natural sidecar rewrite rather than eagerly migrating user data.
    sidecar.pop("owner_session_id", None)
    sidecar["workspace"] = workspace
    return sidecar


def can_access_output(sidecar: Mapping[str, Any] | None, session_id: str) -> bool:
    """Return whether project authorization may expose a gallery output.

    Project membership is enforced by the route before this policy helper is
    called.  ``private`` is only a preview-blur flag, and legacy
    ``owner_session_id`` values are intentionally ignored.  The arguments are
    retained for source compatibility while callers are migrated naturally.
    """
    return True


def public_output_policy(sidecar: Mapping[str, Any] | None) -> dict[str, bool]:
    data = sidecar if isinstance(sidecar, Mapping) else {}
    return {
        "private": bool(data.get("private", False)),
        "explicit": bool(data.get("explicit", False)),
    }


def upload_access_sidecar_path(upload_path: str) -> str:
    """Return the non-ambiguous access sidecar adjacent to one upload.

    The media extension remains part of the sidecar name so ``clip.mp4`` and
    ``clip.wav`` cannot share authorization metadata.  Callers must pass an
    already-contained direct media path; non-normalized paths are rejected so
    this helper cannot turn a traversal-bearing value into a writable path.
    """
    try:
        path = os.fspath(upload_path)
    except TypeError as error:
        raise ValueError("Upload path must be a string path") from error
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("Upload path must be a non-empty string path")
    if os.path.normpath(path) != path:
        raise ValueError("Upload path must be normalized")
    name = os.path.basename(path)
    if name in {"", ".", ".."} or name.startswith("."):
        raise ValueError("Upload path must name a non-hidden direct file")
    if name.endswith(UPLOAD_ACCESS_SIDECAR_SUFFIX):
        raise ValueError("Upload path must name media, not an access sidecar")
    return f"{path}{UPLOAD_ACCESS_SIDECAR_SUFFIX}"


def _valid_upload_access_metadata(data: Any) -> dict[str, Any] | None:
    """Validate and normalize the current fail-closed upload schema."""
    if not isinstance(data, Mapping):
        return None
    version = data.get("version")
    private = data.get("private")
    owner = data.get("owner_session_id")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != _UPLOAD_ACCESS_VERSION
        or data.get("kind") != "upload"
        or not isinstance(private, bool)
        or not isinstance(owner, str)
        or len(owner) != 32
        or any(character not in "0123456789abcdef" for character in owner)
    ):
        return None
    return {
        "version": _UPLOAD_ACCESS_VERSION,
        "kind": "upload",
        "private": private,
        "owner_session_id": owner,
    }


def read_upload_access_sidecar(upload_path: str) -> dict[str, Any] | None:
    """Read valid upload access metadata; missing/malformed data is ``None``."""
    path = upload_access_sidecar_path(upload_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    return _valid_upload_access_metadata(data)


def write_upload_access_sidecar(
    upload_path: str,
    owner_session_id: str,
    *,
    private: bool = True,
) -> dict[str, Any]:
    """Atomically stamp one upload with its owner and deliberate visibility."""
    metadata = _valid_upload_access_metadata({
        "version": _UPLOAD_ACCESS_VERSION,
        "kind": "upload",
        "private": private,
        "owner_session_id": owner_session_id,
    })
    if metadata is None:
        raise ValueError("Invalid upload access metadata")

    path = upload_access_sidecar_path(upload_path)
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        raise ValueError("Upload directory does not exist")
    temp = f"{path}.{secrets.token_hex(4)}.tmp"
    descriptor = None
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(metadata, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.remove(temp)
        except OSError:
            pass
    return metadata


def can_access_upload(upload_path: str, session_id: str) -> bool:
    """Authorize one session-owned upload, independent of its blur flag."""
    try:
        metadata = read_upload_access_sidecar(upload_path)
    except ValueError:
        return False
    if metadata is None:
        return False
    if not isinstance(session_id, str) or not session_id:
        return False
    return hmac.compare_digest(metadata["owner_session_id"], session_id)


@dataclass(frozen=True)
class ProjectAccessStatus:
    protected: bool
    unlocked: bool


class ProjectAccessManager:
    """Password metadata and per-session project unlocks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._unlocked: set[tuple[str, str]] = set()

    @staticmethod
    def metadata_path(workspace_dir: str) -> str:
        return os.path.join(workspace_dir, ".project-access.json")

    def _load(self, workspace_dir: str) -> dict[str, Any]:
        try:
            with open(self.metadata_path(workspace_dir), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return {"_access_metadata_invalid": True}
            return data
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError):
            # A corrupt/unreadable lock must never turn a protected project
            # into an unprotected one. Recovery remains possible by repairing
            # or deliberately removing the local metadata file.
            return {"_access_metadata_invalid": True}

    def status(self, workspace: str, workspace_dir: str, session_id: str) -> ProjectAccessStatus:
        metadata = self._load(workspace_dir)
        invalid = bool(metadata.get("_access_metadata_invalid"))
        protected = invalid or bool(metadata.get("password_hash"))
        with self._lock:
            unlocked = not invalid and (
                not protected or (workspace, session_id) in self._unlocked
            )
        return ProjectAccessStatus(protected=protected, unlocked=unlocked)

    def require(self, workspace: str, workspace_dir: str, session_id: str) -> bool:
        return self.status(workspace, workspace_dir, session_id).unlocked

    def set_password(
        self,
        workspace: str,
        workspace_dir: str,
        session_id: str,
        password: str | None,
    ) -> ProjectAccessStatus:
        value = str(password or "")
        path = self.metadata_path(workspace_dir)
        if not value:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            with self._lock:
                self._unlocked = {item for item in self._unlocked if item[0] != workspace}
            return ProjectAccessStatus(False, True)
        if len(value) < MIN_PROJECT_PASSWORD_LENGTH:
            raise ValueError(
                f"Project password must be at least {MIN_PROJECT_PASSWORD_LENGTH} characters"
            )
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", value.encode("utf-8"), salt, _PBKDF2_ITERATIONS,
        )
        payload = {
            "version": 1,
            "algorithm": "pbkdf2-sha256",
            "iterations": _PBKDF2_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": base64.b64encode(digest).decode("ascii"),
            "encrypted": False,
        }
        temp = f"{path}.{secrets.token_hex(4)}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
        with self._lock:
            self._unlocked = {item for item in self._unlocked if item[0] != workspace}
            self._unlocked.add((workspace, session_id))
        return ProjectAccessStatus(True, True)

    def unlock(self, workspace: str, workspace_dir: str, session_id: str, password: str) -> bool:
        data = self._load(workspace_dir)
        if data.get("_access_metadata_invalid"):
            return False
        encoded_hash = data.get("password_hash")
        if not encoded_hash:
            return True
        try:
            if data.get("version") != 1 or data.get("algorithm") != "pbkdf2-sha256":
                return False
            salt = base64.b64decode(data["salt"], validate=True)
            expected = base64.b64decode(encoded_hash, validate=True)
            iterations = int(data.get("iterations"))
            if iterations != _PBKDF2_ITERATIONS or len(salt) != 16 or len(expected) != 32:
                return False
        except (KeyError, ValueError, TypeError):
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", str(password or "").encode("utf-8"), salt, iterations,
        )
        if not hmac.compare_digest(actual, expected):
            return False
        with self._lock:
            self._unlocked.add((workspace, session_id))
        return True

    def lock(self, workspace: str, session_id: str) -> None:
        with self._lock:
            self._unlocked.discard((workspace, session_id))
