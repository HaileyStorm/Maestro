"""Durable, local-first account and browser-session primitives.

Accounts are an optional authority layered on top of Maestro's anonymous
browser session.  The store never persists bearer session IDs, nonces,
passwords, or recovery codes.  Session IDs, nonces, and recovery codes are
represented by keyed digests under Maestro's owner-only session secret;
passwords use salted scrypt verifiers inside the keyed sealed store.  Email is
optional profile data and is never a login credential by itself.

Passkey credential records are schema-reserved for a future WebAuthn
implementation.  This module deliberately exposes no passkey ceremony and
makes no claim that passkey authentication is currently available.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import subprocess
import threading
import time
import unicodedata
from collections.abc import Mapping
from typing import Any

ACCOUNT_STORE_VERSION = 1
MIN_ACCOUNT_PASSWORD_LENGTH = 12
MAX_ACCOUNT_PASSWORD_BYTES = 1024
ACCOUNT_ROLES = frozenset({"owner", "user"})
ACCOUNT_NONCE_PURPOSES = frozenset({
    "bootstrap",
    "login",
    "reauth",
    "recover",
    "change_password",
    "rotate_recovery_codes",
    "create_account",
    "disable_account",
    "revoke_session",
    "revoke_all_sessions",
})
ACCOUNT_SESSION_COOKIE_NAME = "maestro_account_session"

_DEFAULT_SCRYPT_N = 1 << 15
_DEFAULT_SCRYPT_R = 8
_DEFAULT_SCRYPT_P = 1
_PASSWORD_DKLEN = 32
_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
_REAUTH_TTL_SECONDS = 10 * 60
_NONCE_TTL_SECONDS = 5 * 60
_REVOKED_RETENTION_SECONDS = 90 * 24 * 60 * 60
_MAX_RECENT_NONCES = 4096
_MAX_RECENT_NONCES_PER_SESSION = 16
_MAX_STORE_BYTES = 8 * 1024 * 1024
_MAX_ACCOUNTS = 512
_MAX_SESSION_RECORDS = 4096
_MAX_SESSION_RECORDS_PER_ACCOUNT = 128
_MAX_ACTIVE_SESSIONS_PER_ACCOUNT = 64
_MAX_ATTEMPTS = 8192
_MAX_PASSKEYS_PER_ACCOUNT = 32
_MAX_RECOVERY_CODES_PER_ACCOUNT = 32
_GLOBAL_KDF_FREE_FAILURES = 12
_STORE_SEAL_DOMAIN = b"maestro-account-store-v1\0"
_BOOTSTRAP_MARKER_VERSION = 1
_BOOTSTRAP_MARKER_MAX_BYTES = 4096
_BOOTSTRAP_MARKER_SEAL_DOMAIN = b"maestro-account-bootstrap-marker-v1\0"
_BOOTSTRAP_MARKER_BINDING_DOMAIN = b"maestro-account-bootstrap-store-v1\0"
_SESSION_DIGEST_DOMAIN = b"maestro-account-session-v1\0"
_NONCE_DIGEST_DOMAIN = b"maestro-account-nonce-v1\0"
_SESSION_BINDING_DOMAIN = b"maestro-account-session-binding-v1\0"
_RECOVERY_DIGEST_DOMAIN = b"maestro-account-recovery-v1\0"
_RATE_KEY_DOMAIN = b"maestro-account-rate-v1\0"
_ACCOUNT_COOKIE_DOMAIN = b"maestro-account-cookie-v1\0"
_INVALID_USERNAME_KEY = "\0invalid-username"

_BASE_CAPABILITIES = frozenset({
    "generation.submit",
    "outputs.read",
    "projects.read",
    "projects.write",
})
_AUTHENTICATED_CAPABILITIES = frozenset({
    "account.self",
    "messages.use",
})
_OWNER_CAPABILITIES = frozenset({
    "accounts.admin",
    "models.admin",
    "owner.admin",
    "owner.remote_parity",
    "queue.admin",
    "services.admin",
    "storage.admin",
})


class AccountAuthError(RuntimeError):
    """Safe account error carrying a stable public code."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "account_request_failed",
        retry_after: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = max(0, int(retry_after))


class AccountStoreCorruptError(AccountAuthError):
    def __init__(self) -> None:
        super().__init__(
            "The local account store could not be verified.",
            code="account_store_unavailable",
        )


class AccountStoreCapacityError(AccountAuthError):
    def __init__(self, message: str = "The local account store is at capacity.") -> None:
        super().__init__(message, code="account_store_capacity")


_windows_acl_cache_lock = threading.Lock()
_windows_acl_cache: dict[tuple[str, bool], bytes] = {}


def _portable_owner_matches(info: os.stat_result) -> bool:
    return not hasattr(os, "getuid") or info.st_uid == os.getuid()


def _windows_security_descriptor_fingerprint(path: str) -> bytes:
    """Return a cheap owner, group, and DACL fingerprint via native Win32."""
    if os.name != "nt":
        return b""
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        get_file_security = advapi32.GetFileSecurityW
        get_file_security.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        get_file_security.restype = wintypes.BOOL
        requested = 0x00000001 | 0x00000002 | 0x00000004
        required = wintypes.DWORD()
        get_file_security(path, requested, None, 0, ctypes.byref(required))
        if required.value <= 0 or ctypes.get_last_error() != 122:
            raise OSError(ctypes.get_last_error(), "security descriptor query failed")
        descriptor = ctypes.create_string_buffer(required.value)
        if not get_file_security(
            path, requested, descriptor, required.value, ctypes.byref(required),
        ):
            raise OSError(ctypes.get_last_error(), "security descriptor read failed")
        return hashlib.sha256(descriptor.raw[:required.value]).digest()
    except (AttributeError, OSError, ValueError) as error:
        raise AccountStoreCorruptError() from error


def _tighten_windows_acl(path: str, *, directory: bool) -> None:
    """Replace inherited Windows ACLs with one verified current-user ACE."""
    if os.name != "nt":
        return
    absolute = os.path.abspath(path)
    try:
        os.lstat(absolute)
    except OSError as error:
        raise AccountStoreCorruptError() from error
    fingerprint = _windows_security_descriptor_fingerprint(absolute)
    cache_key = (absolute, bool(directory))
    with _windows_acl_cache_lock:
        if hmac.compare_digest(
            _windows_acl_cache.get(cache_key, b""), fingerprint,
        ):
            return
    inheritance = "ContainerInherit,ObjectInherit" if directory else "None"
    script = r"""
$ErrorActionPreference = 'Stop'
$target = $env:MAESTRO_ACL_TARGET
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = if ($env:MAESTRO_ACL_DIRECTORY -eq '1') {
    New-Object Security.AccessControl.DirectorySecurity
} else {
    New-Object Security.AccessControl.FileSecurity
}
$acl.SetOwner($identity)
$acl.SetAccessRuleProtection($true, $false)
$inheritance = [Security.AccessControl.InheritanceFlags]$env:MAESTRO_ACL_INHERITANCE
$rule = New-Object Security.AccessControl.FileSystemAccessRule(
    $identity,
    [Security.AccessControl.FileSystemRights]::FullControl,
    $inheritance,
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow
)
$acl.SetAccessRule($rule)
Set-Acl -LiteralPath $target -AclObject $acl
$verified = Get-Acl -LiteralPath $target
$verifiedOwner = $verified.GetOwner([Security.Principal.SecurityIdentifier])
$rules = @($verified.GetAccessRules(
    $true,
    $false,
    [Security.Principal.SecurityIdentifier]
))
if (-not $verified.AreAccessRulesProtected) { exit 41 }
if ($verifiedOwner.Value -ne $identity.Value) { exit 42 }
if ($rules.Count -ne 1) { exit 43 }
$actual = $rules[0]
if ($actual.IdentityReference.Value -ne $identity.Value) { exit 44 }
if ($actual.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) { exit 45 }
if (($actual.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl) { exit 46 }
if ($actual.InheritanceFlags -ne $inheritance) { exit 47 }
if ($actual.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) { exit 48 }
[Console]::Out.Write([Convert]::ToBase64String(
    $verified.GetSecurityDescriptorBinaryForm()
))
"""
    try:
        environment = os.environ.copy()
        environment.update({
            "MAESTRO_ACL_TARGET": absolute,
            "MAESTRO_ACL_DIRECTORY": "1" if directory else "0",
            "MAESTRO_ACL_INHERITANCE": inheritance,
        })
        completed = subprocess.run(
            [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            raise AccountStoreCorruptError()
        encoded_descriptor = completed.stdout
        if not isinstance(encoded_descriptor, bytes) or len(encoded_descriptor) > 128 * 1024:
            raise AccountStoreCorruptError()
        try:
            verified_descriptor = base64.b64decode(encoded_descriptor, validate=True)
        except (TypeError, ValueError) as error:
            raise AccountStoreCorruptError() from error
        if not verified_descriptor:
            raise AccountStoreCorruptError()
        verified_fingerprint = hashlib.sha256(verified_descriptor).digest()
        with _windows_acl_cache_lock:
            _windows_acl_cache[cache_key] = verified_fingerprint
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise AccountStoreCorruptError() from error


def _fsync_directory(path: str) -> None:
    """Durably flush a containing directory or fail the mutation closed."""
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    try:
        import ctypes
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            os.path.abspath(path),
            0x40000000,  # GENERIC_WRITE
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        invalid = wintypes.HANDLE(-1).value
        if handle == invalid:
            raise OSError(ctypes.get_last_error(), "directory open failed")
        try:
            if not ctypes.windll.kernel32.FlushFileBuffers(handle):
                raise OSError(ctypes.get_last_error(), "directory flush failed")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError) as error:
        raise AccountStoreCorruptError() from error


def _ensure_private_directory(path: str) -> None:
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        info = os.lstat(path)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or not _portable_owner_matches(info)
        ):
            raise AccountStoreCorruptError()
        if os.name != "nt" and info.st_mode & 0o077:
            os.chmod(path, 0o700)
            tightened = os.lstat(path)
            if tightened.st_mode & 0o077 or not _portable_owner_matches(tightened):
                raise AccountStoreCorruptError()
        if os.name == "nt":
            _tighten_windows_acl(path, directory=True)
    except OSError as error:
        raise AccountStoreCorruptError() from error


def _read_private_file(path: str, *, max_bytes: int) -> bytes | None:
    """Read one owner-only regular file without following or racing links."""
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise AccountStoreCorruptError()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise AccountStoreCorruptError() from error
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise AccountStoreCorruptError() from error
    try:
        info = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not os.path.samestat(info, current)
            or info.st_nlink != 1
            or info.st_size > max_bytes
            or not _portable_owner_matches(info)
        ):
            raise AccountStoreCorruptError()
        if os.name != "nt" and info.st_mode & 0o077:
            os.fchmod(descriptor, 0o600)
            if os.fstat(descriptor).st_mode & 0o077:
                raise AccountStoreCorruptError()
        if os.name == "nt":
            _tighten_windows_acl(path, directory=False)
        chunks = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise AccountStoreCorruptError()
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise AccountStoreCorruptError()
        return b"".join(chunks)
    except AccountAuthError:
        raise
    except OSError as error:
        raise AccountStoreCorruptError() from error
    finally:
        os.close(descriptor)


def _atomic_replace_private_file(path: str, encoded: bytes) -> None:
    """Durably replace one owner-only file within its private directory."""
    directory = os.path.dirname(path)
    _ensure_private_directory(directory)
    temporary = os.path.join(
        directory, f".{os.path.basename(path)}.{secrets.token_hex(8)}.tmp",
    )
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if os.name == "nt":
            _tighten_windows_acl(path, directory=False)
        _fsync_directory(directory)
    except AccountAuthError:
        raise
    except OSError as error:
        raise AccountStoreCorruptError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.remove(temporary)
        except OSError:
            pass


class _AccountStoreLock:
    """Thread and process lock pinned to one never-unlinked lock inode."""

    def __init__(self, store_path: str) -> None:
        self.path = store_path + ".lock"
        self._thread_lock = threading.RLock()
        self._descriptor: int | None = None

    def __enter__(self):
        self._thread_lock.acquire()
        descriptor = None
        try:
            _ensure_private_directory(os.path.dirname(self.path))
            try:
                before = os.lstat(self.path)
                if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                    raise AccountStoreCorruptError()
            except FileNotFoundError:
                before = None
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            info = os.fstat(descriptor)
            current = os.lstat(self.path)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or not os.path.samestat(info, current)
                or info.st_nlink != 1
                or not _portable_owner_matches(info)
            ):
                raise AccountStoreCorruptError()
            if os.name != "nt" and info.st_mode & 0o077:
                os.fchmod(descriptor, 0o600)
                if os.fstat(descriptor).st_mode & 0o077:
                    raise AccountStoreCorruptError()
            if os.name == "nt":
                _tighten_windows_acl(self.path, directory=False)
            if os.name == "nt":
                import msvcrt

                if info.st_size < 1:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX)
                        break
                    except OSError as error:
                        if error.errno != errno.EINTR:
                            raise
            self._descriptor = descriptor
            return self
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):
        descriptor = self._descriptor
        self._descriptor = None
        try:
            if descriptor is not None:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        finally:
            self._thread_lock.release()


def encode_account_session_cookie(session_id: str, secret: bytes) -> str:
    if not isinstance(session_id, str) or re.fullmatch(r"[0-9a-f]{32}", session_id) is None:
        raise ValueError("invalid account session identifier")
    signature = hmac.new(
        secret,
        _ACCOUNT_COOKIE_DOMAIN + session_id.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{session_id}.{signature}"


def decode_account_session_cookie(value: str | None, secret: bytes) -> str | None:
    if not isinstance(value, str) or value.count(".") != 1:
        return None
    session_id, signature = value.split(".", 1)
    if (
        re.fullmatch(r"[0-9a-f]{32}", session_id) is None
        or re.fullmatch(r"[0-9a-f]{64}", signature) is None
    ):
        return None
    expected = hmac.new(
        secret,
        _ACCOUNT_COOKIE_DOMAIN + session_id.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return session_id if hmac.compare_digest(signature, expected) else None


def resolve_account_capabilities(
    principal: Mapping[str, Any] | None,
    *,
    remote: bool,
) -> frozenset[str]:
    """Resolve server authority without treating an address as an identity."""
    capabilities = set(_BASE_CAPABILITIES)
    if principal is None:
        # Preserve Maestro's existing machine-local anonymous owner posture.
        if not remote:
            capabilities.update(_OWNER_CAPABILITIES)
            capabilities.add("machine.local")
        return frozenset(capabilities)
    capabilities.update(_AUTHENTICATED_CAPABILITIES)
    if principal.get("role") == "owner" and principal.get("disabled") is not True:
        capabilities.update(_OWNER_CAPABILITIES)
        if not remote:
            capabilities.add("machine.local")
    return frozenset(capabilities)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _normalize_username(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise AccountAuthError("Username is required.", code="invalid_username")
    username = unicodedata.normalize("NFKC", value)
    if username != username.strip() or not 3 <= len(username) <= 64:
        raise AccountAuthError(
            "Username must contain 3 to 64 characters with no outer whitespace.",
            code="invalid_username",
        )
    if any(unicodedata.category(character).startswith("C") for character in username):
        raise AccountAuthError("Username contains invalid characters.", code="invalid_username")
    return username, username.casefold()


def _normalize_email(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AccountAuthError("Email must be text.", code="invalid_email")
    if value == "":
        return ""
    email = unicodedata.normalize("NFKC", value).strip().casefold()
    if (
        not 3 <= len(email) <= 254
        or email.count("@") != 1
        or email.startswith("@")
        or email.endswith("@")
        or any(character.isspace() or ord(character) < 32 for character in email)
    ):
        raise AccountAuthError("Email is invalid.", code="invalid_email")
    return email


def _normalize_device_label(value: Any) -> str:
    if value is None:
        return "Browser"
    if not isinstance(value, str):
        raise AccountAuthError("Device label must be text.", code="invalid_device_label")
    if value == "":
        return "Browser"
    label = " ".join(value.split())
    if not 1 <= len(label) <= 80 or any(ord(character) < 32 for character in label):
        raise AccountAuthError(
            "Device label must contain 1 to 80 printable characters.",
            code="invalid_device_label",
        )
    return label


def _validate_password(value: Any) -> str:
    if not isinstance(value, str):
        raise AccountAuthError("Password is required.", code="invalid_password")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise AccountAuthError(
            "Password contains invalid text.", code="invalid_password",
        ) from None
    if len(value) < MIN_ACCOUNT_PASSWORD_LENGTH or len(encoded) > MAX_ACCOUNT_PASSWORD_BYTES:
        raise AccountAuthError(
            f"Password must contain at least {MIN_ACCOUNT_PASSWORD_LENGTH} characters.",
            code="invalid_password",
        )
    return value


class AccountAuthStore:
    """Atomic, sealed local account/session store."""

    def __init__(
        self,
        path: str,
        secret: bytes,
        *,
        clock=time.time,
        password_n: int = _DEFAULT_SCRYPT_N,
        session_ttl_seconds: int = _SESSION_TTL_SECONDS,
        reauth_ttl_seconds: int = _REAUTH_TTL_SECONDS,
        nonce_ttl_seconds: int = _NONCE_TTL_SECONDS,
        max_store_bytes: int = _MAX_STORE_BYTES,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("account-store secret must contain at least 32 bytes")
        if password_n < 1024 or password_n & (password_n - 1):
            raise ValueError("scrypt N must be a power of two of at least 1024")
        self.path = os.path.abspath(path)
        self._secret = secret
        self._clock = clock
        self._password_n = int(password_n)
        self._session_ttl = max(60, int(session_ttl_seconds))
        self._reauth_ttl = min(
            self._session_ttl, max(30, int(reauth_ttl_seconds)),
        )
        self._nonce_ttl = max(30, int(nonce_ttl_seconds))
        if not 1024 <= int(max_store_bytes) <= _MAX_STORE_BYTES:
            raise ValueError("account store size limit is invalid")
        self._max_store_bytes = int(max_store_bytes)
        self.bootstrap_marker_path = self.path + ".bootstrap-complete"
        self._lock = _AccountStoreLock(self.path)
        self._kdf_lock = _AccountStoreLock(self.path + ".kdf-work")
        dummy_salt = hmac.new(
            self._secret,
            b"maestro-account-dummy-password-salt-v1",
            hashlib.sha256,
        ).digest()[:16]
        self._dummy_password_record = {
            "algorithm": "scrypt",
            "salt": base64.b64encode(dummy_salt).decode("ascii"),
            "n": self._password_n,
            "r": _DEFAULT_SCRYPT_R,
            "p": _DEFAULT_SCRYPT_P,
            "dklen": _PASSWORD_DKLEN,
            # Verification still performs exactly one production-cost scrypt;
            # an all-zero impossible target avoids doing one during startup.
            "digest": base64.b64encode(b"\0" * _PASSWORD_DKLEN).decode("ascii"),
        }

    def _digest(self, domain: bytes, value: str) -> str:
        if not isinstance(value, str):
            raise AccountAuthError("Credential is invalid.", code="invalid_credential")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise AccountAuthError(
                "Credential is invalid.", code="invalid_credential",
            ) from None
        return hmac.new(self._secret, domain + encoded, hashlib.sha256).hexdigest()

    def _seal(self, payload: Mapping[str, Any]) -> str:
        unsigned = {key: value for key, value in payload.items() if key != "seal"}
        return hmac.new(
            self._secret,
            _STORE_SEAL_DOMAIN + _canonical_json(unsigned),
            hashlib.sha256,
        ).hexdigest()

    def _bootstrap_marker_store_binding(self) -> str:
        return hmac.new(
            self._secret,
            _BOOTSTRAP_MARKER_BINDING_DOMAIN + os.fsencode(os.path.normcase(self.path)),
            hashlib.sha256,
        ).hexdigest()

    def _seal_bootstrap_marker(self, payload: Mapping[str, Any]) -> str:
        unsigned = {key: value for key, value in payload.items() if key != "seal"}
        return hmac.new(
            self._secret,
            _BOOTSTRAP_MARKER_SEAL_DOMAIN + _canonical_json(unsigned),
            hashlib.sha256,
        ).hexdigest()

    def _load_bootstrap_marker(self) -> dict[str, Any] | None:
        encoded = _read_private_file(
            self.bootstrap_marker_path, max_bytes=_BOOTSTRAP_MARKER_MAX_BYTES,
        )
        if encoded is None:
            return None
        try:
            payload = json.loads(encoded.decode("utf-8"))
            if (
                not isinstance(payload, dict)
                or set(payload) != {"version", "store_binding", "owner_id", "seal"}
                or payload.get("version") != _BOOTSTRAP_MARKER_VERSION
                or not self._valid_hex(payload.get("store_binding"), 64)
                or not self._valid_hex(payload.get("owner_id"), 32)
                or not self._valid_hex(payload.get("seal"), 64)
                or not hmac.compare_digest(
                    payload["store_binding"], self._bootstrap_marker_store_binding(),
                )
                or not hmac.compare_digest(
                    payload["seal"], self._seal_bootstrap_marker(payload),
                )
            ):
                raise AccountStoreCorruptError()
            return payload
        except AccountStoreCorruptError:
            raise
        except (
            UnicodeDecodeError, ValueError, TypeError, RecursionError, OverflowError,
        ) as error:
            raise AccountStoreCorruptError() from error

    def _save_bootstrap_marker(self, owner_id: str) -> None:
        payload = {
            "version": _BOOTSTRAP_MARKER_VERSION,
            "store_binding": self._bootstrap_marker_store_binding(),
            "owner_id": owner_id,
        }
        payload["seal"] = self._seal_bootstrap_marker(payload)
        encoded = json.dumps(
            payload, indent=2, ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
        _atomic_replace_private_file(self.bootstrap_marker_path, encoded)

    def _ensure_bootstrap_marker(self, payload: Mapping[str, Any]) -> None:
        accounts = payload.get("accounts")
        marker = self._load_bootstrap_marker()
        if not accounts:
            if marker is not None:
                raise AccountStoreCorruptError()
            return
        owner_id = next(
            account["id"] for account in accounts if account["role"] == "owner"
        )
        if marker is None:
            self._save_bootstrap_marker(owner_id)
        elif not hmac.compare_digest(marker["owner_id"], owner_id):
            raise AccountStoreCorruptError()

    @staticmethod
    def _empty_payload() -> dict[str, Any]:
        return {
            "version": ACCOUNT_STORE_VERSION,
            "generation": 0,
            "clock_high_water": 0.0,
            "accounts": [],
            "sessions": [],
            "nonces": [],
            "attempts": {},
        }

    def _validate_payload(self, payload: Any) -> dict[str, Any]:
        if (
            not isinstance(payload, dict)
            or set(payload) != {
                "version", "generation", "clock_high_water", "accounts",
                "sessions", "nonces", "attempts", "seal",
            }
            or payload.get("version") != ACCOUNT_STORE_VERSION
        ):
            raise AccountStoreCorruptError()
        seal = payload.get("seal")
        if (
            not isinstance(seal, str)
            or len(seal) != 64
            or not hmac.compare_digest(seal, self._seal(payload))
        ):
            raise AccountStoreCorruptError()
        if (
            not isinstance(payload.get("generation"), int)
            or isinstance(payload.get("generation"), bool)
            or payload["generation"] < 0
            or payload["generation"] > (1 << 63) - 1
            or not _finite_number(payload.get("clock_high_water"))
            or float(payload["clock_high_water"]) < 0
            or not isinstance(payload.get("accounts"), list)
            or not isinstance(payload.get("sessions"), list)
            or not isinstance(payload.get("nonces"), list)
            or not isinstance(payload.get("attempts"), dict)
            or len(payload["accounts"]) > _MAX_ACCOUNTS
            or len(payload["sessions"]) > _MAX_SESSION_RECORDS
            or len(payload["nonces"]) > _MAX_RECENT_NONCES
            or len(payload["attempts"]) > _MAX_ATTEMPTS
        ):
            raise AccountStoreCorruptError()
        account_ids: set[str] = set()
        accounts_by_id: dict[str, dict[str, Any]] = {}
        usernames: set[str] = set()
        for account in payload["accounts"]:
            if not self._valid_account(account):
                raise AccountStoreCorruptError()
            if account["id"] in account_ids or account["username_key"] in usernames:
                raise AccountStoreCorruptError()
            account_ids.add(account["id"])
            accounts_by_id[account["id"]] = account
            usernames.add(account["username_key"])
        if payload["accounts"] and sum(
            account["role"] == "owner" for account in payload["accounts"]
        ) != 1:
            raise AccountStoreCorruptError()
        handles: set[str] = set()
        session_digests: set[str] = set()
        sessions_per_account: dict[str, int] = {}
        for session in payload["sessions"]:
            if (
                not self._valid_session(session)
                or session["account_id"] not in account_ids
                or float(session["created_at"]) < float(
                    accounts_by_id[session["account_id"]]["created_at"]
                )
                or session["handle"] in handles
                or session["session_digest"] in session_digests
            ):
                raise AccountStoreCorruptError()
            handles.add(session["handle"])
            session_digests.add(session["session_digest"])
            sessions_per_account[session["account_id"]] = (
                sessions_per_account.get(session["account_id"], 0) + 1
            )
            if sessions_per_account[session["account_id"]] > _MAX_SESSION_RECORDS_PER_ACCOUNT:
                raise AccountStoreCorruptError()
        nonce_digests: set[str] = set()
        for nonce in payload["nonces"]:
            if not self._valid_nonce(nonce) or nonce["nonce_digest"] in nonce_digests:
                raise AccountStoreCorruptError()
            nonce_digests.add(nonce["nonce_digest"])
        for key, attempt in payload["attempts"].items():
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[a-z-]{1,32}:[0-9a-f]{64}", key) is None
                or not isinstance(attempt, dict)
                or set(attempt) != {"failures", "blocked_until"}
                or not isinstance(attempt.get("failures"), int)
                or isinstance(attempt.get("failures"), bool)
                or not 0 <= attempt["failures"] <= 1_000_000
                or not _finite_number(attempt.get("blocked_until"))
                or float(attempt["blocked_until"]) < 0
            ):
                raise AccountStoreCorruptError()
        observed = self._monotonic_event_time(payload, 0.0, include_high_water=False)
        if float(payload["clock_high_water"]) < observed:
            raise AccountStoreCorruptError()
        return payload

    @staticmethod
    def _valid_hex(value: Any, length: int) -> bool:
        return (
            isinstance(value, str)
            and len(value) == length
            and all(character in "0123456789abcdef" for character in value)
        )

    @classmethod
    def _valid_account(cls, account: Any) -> bool:
        if not isinstance(account, dict):
            return False
        password = account.get("password")
        passkeys = account.get("passkey_credentials")
        recovery = account.get("recovery_codes")
        if (
            set(account) != {
                "id", "username", "username_key", "email", "role", "disabled",
                "created_at", "password", "passkey_credentials", "recovery_codes",
            }
            or
            not cls._valid_hex(account.get("id"), 32)
            or not isinstance(account.get("username"), str)
            or not isinstance(account.get("username_key"), str)
            or account.get("role") not in ACCOUNT_ROLES
            or type(account.get("disabled")) is not bool
            or not isinstance(account.get("email"), str)
            or not _finite_number(account.get("created_at"))
            or not isinstance(password, dict)
            or password.get("algorithm") != "scrypt"
            or set(password) != {
                "algorithm", "salt", "n", "r", "p", "dklen", "digest",
            }
            or not isinstance(password.get("n"), int)
            or not isinstance(password.get("r"), int)
            or not isinstance(password.get("p"), int)
            or password.get("dklen") != _PASSWORD_DKLEN
            or not isinstance(password.get("salt"), str)
            or not isinstance(password.get("digest"), str)
            or not isinstance(passkeys, list)
            or not isinstance(recovery, list)
            or len(passkeys) > _MAX_PASSKEYS_PER_ACCOUNT
            or len(recovery) > _MAX_RECOVERY_CODES_PER_ACCOUNT
        ):
            return False
        try:
            normalized_username, username_key = _normalize_username(account["username"])
            normalized_email = _normalize_email(account["email"])
        except AccountAuthError:
            return False
        if (
            normalized_username != account["username"]
            or username_key != account["username_key"]
            or normalized_email != account["email"]
            or float(account["created_at"]) < 0
            or isinstance(password["n"], bool)
            or password["n"] < 1024
            or password["n"] > 1 << 20
            or password["n"] & (password["n"] - 1)
            or isinstance(password["r"], bool)
            or not 1 <= password["r"] <= 32
            or isinstance(password["p"], bool)
            or not 1 <= password["p"] <= 16
        ):
            return False
        try:
            salt = base64.b64decode(password["salt"], validate=True)
            digest = base64.b64decode(password["digest"], validate=True)
        except (ValueError, TypeError):
            return False
        if len(salt) != 16 or len(digest) != _PASSWORD_DKLEN:
            return False
        # Schema only. No WebAuthn operation consumes these records yet.
        for credential in passkeys:
            if (
                not isinstance(credential, dict)
                or set(credential) != {
                    "credential_id", "public_key", "sign_count", "created_at",
                    "label", "transports", "disabled",
                }
                or not isinstance(credential["credential_id"], str)
                or not 1 <= len(credential["credential_id"]) <= 2048
                or re.fullmatch(r"[A-Za-z0-9_-]+", credential["credential_id"]) is None
                or not isinstance(credential["public_key"], str)
                or not 1 <= len(credential["public_key"]) <= 8192
                or re.fullmatch(
                    r"[A-Za-z0-9_-]+", credential["public_key"],
                ) is None
                or not isinstance(credential["sign_count"], int)
                or isinstance(credential["sign_count"], bool)
                or not 0 <= credential["sign_count"] <= (1 << 63) - 1
                or not _finite_number(credential["created_at"])
                or float(credential["created_at"]) < float(account["created_at"])
                or not isinstance(credential["label"], str)
                or not isinstance(credential["transports"], list)
                or len(credential["transports"]) > 8
                or len(set(credential["transports"])) != len(credential["transports"])
                or any(
                    transport not in {"ble", "hybrid", "internal", "nfc", "usb"}
                    for transport in credential["transports"]
                )
                or type(credential["disabled"]) is not bool
            ):
                return False
            try:
                if _normalize_device_label(credential["label"]) != credential["label"]:
                    return False
                base64.urlsafe_b64decode(
                    credential["public_key"] + "=" * (-len(credential["public_key"]) % 4)
                )
            except (AccountAuthError, ValueError, TypeError):
                return False
        return all(
            isinstance(item, dict)
            and set(item) == {"digest", "created_at", "used_at"}
            and cls._valid_hex(item.get("digest"), 64)
            and _finite_number(item.get("created_at"))
            and float(item["created_at"]) >= float(account["created_at"])
            and (
                item.get("used_at") is None
                or (
                    _finite_number(item.get("used_at"))
                    and float(item["used_at"]) >= float(item["created_at"])
                )
            )
            for item in recovery
        )

    @classmethod
    def _valid_session(cls, session: Any) -> bool:
        if not (
            isinstance(session, dict)
            and set(session) == {
                "handle", "session_digest", "account_id", "device_label",
                "remote_created", "created_at", "last_seen_at", "expires_at",
                "reauthenticated_until", "revoked_at",
            }
            and cls._valid_hex(session.get("handle"), 32)
            and cls._valid_hex(session.get("session_digest"), 64)
            and cls._valid_hex(session.get("account_id"), 32)
            and isinstance(session.get("device_label"), str)
            and type(session.get("remote_created")) is bool
            and all(_finite_number(session.get(key)) for key in (
                "created_at", "last_seen_at", "expires_at", "reauthenticated_until",
            ))
            and (
                session.get("revoked_at") is None
                or _finite_number(session.get("revoked_at"))
            )
        ):
            return False
        try:
            label_valid = _normalize_device_label(session["device_label"]) == session["device_label"]
        except AccountAuthError:
            return False
        created = float(session["created_at"])
        seen = float(session["last_seen_at"])
        expires = float(session["expires_at"])
        reauthenticated = float(session["reauthenticated_until"])
        revoked = session["revoked_at"]
        return bool(
            label_valid
            and 0 <= created <= seen <= expires
            and (reauthenticated == 0.0 or created <= reauthenticated <= expires)
            and (
                revoked is None
                or created <= float(revoked)
            )
        )

    @classmethod
    def _valid_nonce(cls, nonce: Any) -> bool:
        if not (
            isinstance(nonce, dict)
            and set(nonce) == {
                "nonce_digest", "session_binding", "purpose", "created_at",
                "expires_at", "used_at",
            }
            and cls._valid_hex(nonce.get("nonce_digest"), 64)
            and cls._valid_hex(nonce.get("session_binding"), 64)
            and nonce.get("purpose") in ACCOUNT_NONCE_PURPOSES
            and _finite_number(nonce.get("created_at"))
            and _finite_number(nonce.get("expires_at"))
            and (nonce.get("used_at") is None or _finite_number(nonce.get("used_at")))
        ):
            return False
        created = float(nonce["created_at"])
        expires = float(nonce["expires_at"])
        used = nonce["used_at"]
        return bool(
            0 <= created < expires
            and (used is None or created <= float(used) <= expires)
        )

    def _load(self) -> dict[str, Any]:
        encoded = _read_private_file(self.path, max_bytes=self._max_store_bytes)
        if encoded is None:
            if self._load_bootstrap_marker() is not None:
                raise AccountStoreCorruptError()
            return self._empty_payload()
        try:
            payload = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as error:
            raise AccountStoreCorruptError() from error
        try:
            payload = self._validate_payload(payload)
            self._ensure_bootstrap_marker(payload)
            return payload
        except AccountStoreCorruptError:
            raise
        except (OverflowError, RecursionError, TypeError, ValueError) as error:
            raise AccountStoreCorruptError() from error

    def _save(self, payload: dict[str, Any]) -> None:
        now = self._monotonic_event_time(payload, float(self._clock()))
        payload["clock_high_water"] = now
        self._prune(payload, now)
        payload = {key: value for key, value in payload.items() if key != "seal"}
        payload["generation"] = int(payload.get("generation", 0)) + 1
        payload["seal"] = self._seal(payload)
        self._validate_payload(payload)
        encoded = json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(encoded) > self._max_store_bytes:
            raise AccountStoreCapacityError()
        _atomic_replace_private_file(self.path, encoded)
        self._ensure_bootstrap_marker(payload)

    @staticmethod
    def _monotonic_event_time(
        payload: Mapping[str, Any],
        now: float,
        *,
        include_high_water: bool = True,
    ) -> float:
        observed = [float(now)]
        if include_high_water:
            observed.append(float(payload.get("clock_high_water", 0.0)))
        for account in payload.get("accounts", []):
            observed.append(float(account["created_at"]))
            for recovery in account.get("recovery_codes", []):
                observed.append(float(recovery["created_at"]))
                if recovery.get("used_at") is not None:
                    observed.append(float(recovery["used_at"]))
        for collection in ("sessions", "nonces"):
            for record in payload.get(collection, []):
                for key in ("created_at", "last_seen_at", "used_at", "revoked_at"):
                    if record.get(key) is not None:
                        observed.append(float(record[key]))
        return max(observed)

    def _prune(self, payload: dict[str, Any], now: float) -> None:
        payload["nonces"] = [
            item for item in payload["nonces"]
            if item["expires_at"] >= now - self._nonce_ttl
        ]
        payload["sessions"] = [
            item for item in payload["sessions"]
            if (
                item["expires_at"] >= now - _REVOKED_RETENTION_SECONDS
                and (
                    item["revoked_at"] is None
                    or item["revoked_at"] >= now - _REVOKED_RETENTION_SECONDS
                )
            )
        ]
        by_account: dict[str, list[dict[str, Any]]] = {}
        for item in payload["sessions"]:
            by_account.setdefault(item["account_id"], []).append(item)
        bounded_sessions = []
        for records in by_account.values():
            records.sort(key=lambda item: (
                item["revoked_at"] is None and item["expires_at"] > now,
                item["created_at"],
            ))
            bounded_sessions.extend(records[-_MAX_SESSION_RECORDS_PER_ACCOUNT:])
        bounded_sessions.sort(key=lambda item: item["created_at"])
        payload["sessions"] = bounded_sessions[-_MAX_SESSION_RECORDS:]
        payload["attempts"] = {
            key: value for key, value in payload["attempts"].items()
            if value["blocked_until"] >= now - 24 * 60 * 60
        }

    def _password_record(self, password: str) -> dict[str, Any]:
        password = _validate_password(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self._password_n,
            r=_DEFAULT_SCRYPT_R,
            p=_DEFAULT_SCRYPT_P,
            maxmem=128 * 1024 * 1024,
            dklen=_PASSWORD_DKLEN,
        )
        return {
            "algorithm": "scrypt",
            "salt": base64.b64encode(salt).decode("ascii"),
            "n": self._password_n,
            "r": _DEFAULT_SCRYPT_R,
            "p": _DEFAULT_SCRYPT_P,
            "dklen": _PASSWORD_DKLEN,
            "digest": base64.b64encode(digest).decode("ascii"),
        }

    @staticmethod
    def _verify_password(password: Any, record: Mapping[str, Any]) -> bool:
        supplied = password if isinstance(password, str) else ""
        try:
            if len(supplied.encode("utf-8")) > MAX_ACCOUNT_PASSWORD_BYTES:
                return False
        except UnicodeEncodeError:
            return False
        try:
            salt = base64.b64decode(record["salt"], validate=True)
            expected = base64.b64decode(record["digest"], validate=True)
            actual = hashlib.scrypt(
                supplied.encode("utf-8"),
                salt=salt,
                n=int(record["n"]),
                r=int(record["r"]),
                p=int(record["p"]),
                maxmem=128 * 1024 * 1024,
                dklen=int(record["dklen"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        return hmac.compare_digest(actual, expected)

    @staticmethod
    def _find_account(payload: Mapping[str, Any], username_key: str) -> dict[str, Any] | None:
        return next(
            (item for item in payload["accounts"] if item["username_key"] == username_key),
            None,
        )

    @staticmethod
    def _account_by_id(payload: Mapping[str, Any], account_id: str) -> dict[str, Any] | None:
        return next((item for item in payload["accounts"] if item["id"] == account_id), None)

    @staticmethod
    def _public_account(account: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": account["id"],
            "username": account["username"],
            "role": account["role"],
            "disabled": bool(account["disabled"]),
            "created_at": float(account["created_at"]),
            "has_email": bool(account.get("email")),
            "passkey_credentials": len(account.get("passkey_credentials") or []),
            "passkey_authentication_available": False,
        }

    def _recovery_codes(self, now: float) -> tuple[list[str], list[dict[str, Any]]]:
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        raw = []
        records = []
        for _ in range(10):
            compact = "".join(secrets.choice(alphabet) for _ in range(16))
            code = "-".join(compact[index:index + 4] for index in range(0, 16, 4))
            raw.append(code)
            records.append({
                "digest": self._digest(_RECOVERY_DIGEST_DOMAIN, compact),
                "created_at": now,
                "used_at": None,
            })
        return raw, records

    def _new_session(
        self,
        payload: dict[str, Any],
        *,
        account_id: str,
        device_label: str,
        remote: bool,
        now: float,
        reauthenticated: bool,
    ) -> tuple[str, dict[str, Any]]:
        account_records = [
            item for item in payload["sessions"]
            if item["account_id"] == account_id
        ]
        active_records = sorted(
            (
                item for item in account_records
                if item["revoked_at"] is None and item["expires_at"] > now
            ),
            key=lambda item: item["created_at"],
        )
        while len(active_records) >= _MAX_ACTIVE_SESSIONS_PER_ACCOUNT:
            active_records.pop(0)["revoked_at"] = now
        account_records = [
            item for item in payload["sessions"]
            if item["account_id"] == account_id
        ]
        if len(account_records) >= _MAX_SESSION_RECORDS_PER_ACCOUNT:
            removable = sorted(
                (
                    item for item in account_records
                    if item["revoked_at"] is not None or item["expires_at"] <= now
                ),
                key=lambda item: item["created_at"],
            )
            while (
                len(account_records) >= _MAX_SESSION_RECORDS_PER_ACCOUNT
                and removable
            ):
                victim = removable.pop(0)
                payload["sessions"].remove(victim)
                account_records.remove(victim)
        if len(payload["sessions"]) >= _MAX_SESSION_RECORDS:
            removable = sorted(
                (
                    item for item in payload["sessions"]
                    if item["revoked_at"] is not None or item["expires_at"] <= now
                ),
                key=lambda item: item["created_at"],
            )
            if not removable:
                raise AccountStoreCapacityError(
                    "The local account session store is at capacity."
                )
            payload["sessions"].remove(removable[0])
        session_id = secrets.token_hex(16)
        record = {
            "handle": secrets.token_hex(16),
            "session_digest": self._digest(_SESSION_DIGEST_DOMAIN, session_id),
            "account_id": account_id,
            "device_label": _normalize_device_label(device_label),
            "remote_created": bool(remote),
            "created_at": now,
            "last_seen_at": now,
            "expires_at": now + self._session_ttl,
            "reauthenticated_until": now + self._reauth_ttl if reauthenticated else 0.0,
            "revoked_at": None,
        }
        payload["sessions"].append(record)
        return session_id, record

    def _consume_nonce_locked(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        nonce: Any,
        purpose: str,
        now: float,
    ) -> None:
        if purpose not in ACCOUNT_NONCE_PURPOSES or not isinstance(nonce, str):
            raise AccountAuthError("Request nonce is invalid.", code="invalid_nonce")
        nonce_digest = self._digest(_NONCE_DIGEST_DOMAIN, nonce)
        binding = self._digest(_SESSION_BINDING_DOMAIN, session_id)
        record = next(
            (item for item in payload["nonces"] if hmac.compare_digest(item["nonce_digest"], nonce_digest)),
            None,
        )
        if (
            record is None
            or record["used_at"] is not None
            or record["expires_at"] <= now
            or record["purpose"] != purpose
            or not hmac.compare_digest(record["session_binding"], binding)
        ):
            error = AccountAuthError(
                "Request nonce is invalid or expired.", code="invalid_nonce",
            )
            error.persist_clock_high_water = bool(
                record is not None and float(record["expires_at"]) <= now
            )
            raise error
        record["used_at"] = now

    def _consume_nonce_for_mutation_locked(self, payload: dict[str, Any], **kwargs) -> None:
        try:
            self._consume_nonce_locked(payload, **kwargs)
        except AccountAuthError as error:
            if getattr(error, "persist_clock_high_water", False):
                self._save(payload)
            raise

    def issue_nonce(self, session_id: str, purpose: str) -> dict[str, Any]:
        if purpose not in ACCOUNT_NONCE_PURPOSES:
            raise AccountAuthError("Nonce purpose is invalid.", code="invalid_nonce_purpose")
        if not self._valid_hex(session_id, 32):
            raise AccountAuthError("Browser session is invalid.", code="invalid_session")
        raw = secrets.token_urlsafe(32)
        now = float(self._clock())
        with self._lock:
            payload = self._load()
            now = self._monotonic_event_time(payload, now)
            self._prune(payload, now)
            binding = self._digest(_SESSION_BINDING_DOMAIN, session_id)
            bound_nonces = [
                item for item in payload["nonces"]
                if hmac.compare_digest(item["session_binding"], binding)
            ]
            if len(bound_nonces) >= _MAX_RECENT_NONCES_PER_SESSION:
                retry_after = max(
                    1,
                    int(
                        min(
                            item["expires_at"] + self._nonce_ttl
                            for item in bound_nonces
                        )
                        - now
                        + 0.999
                    ),
                )
                self._save(payload)
                raise AccountAuthError(
                    "Nonce issuance is temporarily limited.",
                    code="rate_limited",
                    retry_after=retry_after,
                )
            # The global bound is a storage ceiling, not an authentication
            # lock. Rotating anonymous cookies may evict the oldest nonce but
            # can never force every legitimate client to wait for expiry.
            if len(payload["nonces"]) >= _MAX_RECENT_NONCES:
                payload["nonces"].sort(key=lambda item: (
                    item["used_at"] is None,
                    item["created_at"],
                ))
                del payload["nonces"][:
                    len(payload["nonces"]) - _MAX_RECENT_NONCES + 1
                ]
            payload["nonces"].append({
                "nonce_digest": self._digest(_NONCE_DIGEST_DOMAIN, raw),
                "session_binding": binding,
                "purpose": purpose,
                "created_at": now,
                "expires_at": now + self._nonce_ttl,
                "used_at": None,
            })
            self._save(payload)
        return {"nonce": raw, "purpose": purpose, "expires_in": self._nonce_ttl}

    def _rate_key(self, kind: str, identifier: str, session_id: str) -> str:
        digest = self._digest(_RATE_KEY_DOMAIN, f"{kind}\0{identifier}\0{session_id}")
        return f"{kind}:{digest}"

    def _rate_keys(
        self, kind: str, identifier: str, session_id: str,
    ) -> tuple[str, str]:
        return (
            self._rate_key(f"{kind}-identity", identifier, ""),
            self._rate_key(f"{kind}-browser", "", session_id),
        )

    @staticmethod
    def _retry_after(payload: Mapping[str, Any], key: str, now: float) -> int:
        record = payload["attempts"].get(key)
        if not isinstance(record, dict):
            return 0
        return max(0, int(float(record["blocked_until"]) - now + 0.999))

    @staticmethod
    def _reserve_attempt_capacity(
        payload: dict[str, Any],
        keys: frozenset[str],
        now: float,
    ) -> None:
        missing = sum(key not in payload["attempts"] for key in keys)
        while len(payload["attempts"]) + missing > _MAX_ATTEMPTS:
            candidates = {
                candidate for candidate, attempt in payload["attempts"].items()
                if candidate not in keys
                and not candidate.partition(":")[0].endswith("-global")
                and float(attempt["blocked_until"]) <= now
            }
            if not candidates:
                active_deadlines = [
                    float(attempt["blocked_until"])
                    for attempt in payload["attempts"].values()
                    if float(attempt["blocked_until"]) > now
                ]
                retry_after = max(
                    1,
                    int(min(active_deadlines) - now + 0.999)
                    if active_deadlines else 1,
                )
                raise AccountAuthError(
                    "Authentication is temporarily limited.",
                    code="rate_limited", retry_after=retry_after,
                )
            victim = min(
                candidates,
                key=lambda candidate: (
                    payload["attempts"][candidate]["blocked_until"],
                    payload["attempts"][candidate]["failures"],
                    candidate,
                ),
            )
            payload["attempts"].pop(victim, None)

    @staticmethod
    def _record_failure(
        payload: dict[str, Any],
        key: str,
        now: float,
        free: int,
        *,
        protected: frozenset[str] = frozenset(),
    ) -> int:
        AccountAuthStore._reserve_attempt_capacity(
            payload, frozenset(set(protected) | {key}), now,
        )
        existing = payload["attempts"].get(key) or {"failures": 0, "blocked_until": 0.0}
        failures = int(existing["failures"]) + 1
        delay = 0 if failures <= free else min(300, 2 ** min(8, failures - free))
        payload["attempts"][key] = {"failures": failures, "blocked_until": now + delay}
        return delay

    def has_accounts(self) -> bool:
        with self._lock:
            return bool(self._load()["accounts"])

    def bootstrap_owner(
        self,
        *,
        username: Any,
        password: Any,
        email: Any,
        device_label: Any,
        nonce_session_id: str,
        nonce: Any,
        remote: bool,
    ) -> dict[str, Any]:
        username, username_key = _normalize_username(username)
        email = _normalize_email(email)
        password = _validate_password(password)
        label = _normalize_device_label(device_label)
        with self._kdf_lock:
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                self._consume_nonce_for_mutation_locked(
                    payload, session_id=nonce_session_id, nonce=nonce,
                    purpose="bootstrap", now=now,
                )
                if payload["accounts"]:
                    self._save(payload)
                    raise AccountAuthError(
                        "Account bootstrap is already complete.",
                        code="bootstrap_complete",
                    )
                self._save(payload)

            password_record = self._password_record(password)
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                if payload["accounts"]:
                    raise AccountAuthError(
                        "Account bootstrap is already complete.",
                        code="bootstrap_complete",
                    )
                account = {
                    "id": secrets.token_hex(16),
                    "username": username,
                    "username_key": username_key,
                    "email": email,
                    "role": "owner",
                    "disabled": False,
                    "created_at": now,
                    "password": password_record,
                    "passkey_credentials": [],
                    "recovery_codes": [],
                }
                recovery_codes, account["recovery_codes"] = self._recovery_codes(now)
                payload["accounts"].append(account)
                session_id, _ = self._new_session(
                    payload, account_id=account["id"], device_label=label,
                    remote=remote, now=now, reauthenticated=True,
                )
                self._save(payload)
        return {
            "account": self._public_account(account),
            "account_session_id": session_id,
            "recovery_codes": recovery_codes,
        }

    def create_account(
        self,
        *,
        actor_session_id: str,
        nonce: Any,
        username: Any,
        password: Any,
        email: Any = "",
        role: str = "user",
    ) -> dict[str, Any]:
        if role != "user":
            raise AccountAuthError("Only user accounts can be created here.", code="invalid_role")
        username, username_key = _normalize_username(username)
        email = _normalize_email(email)
        password = _validate_password(password)
        with self._kdf_lock:
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                actor, actor_session = self._require_session_for_event_locked(
                    payload, actor_session_id, now,
                )
                self._require_owner_reauth(payload, actor, actor_session, now)
                self._consume_nonce_for_mutation_locked(
                    payload, session_id=actor_session_id, nonce=nonce,
                    purpose="create_account", now=now,
                )
                if self._find_account(payload, username_key) is not None:
                    self._save(payload)
                    raise AccountAuthError(
                        "Username is unavailable.", code="username_unavailable",
                    )
                if len(payload["accounts"]) >= _MAX_ACCOUNTS:
                    self._save(payload)
                    raise AccountStoreCapacityError()
                actor_id = actor["id"]
                self._save(payload)

            password_record = self._password_record(password)
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                actor, actor_session = self._require_session_for_event_locked(
                    payload, actor_session_id, now,
                )
                self._require_owner_reauth(payload, actor, actor_session, now)
                if actor["id"] != actor_id:
                    raise AccountAuthError(
                        "Owner access is required.", code="owner_required",
                    )
                if self._find_account(payload, username_key) is not None:
                    raise AccountAuthError(
                        "Username is unavailable.", code="username_unavailable",
                    )
                if len(payload["accounts"]) >= _MAX_ACCOUNTS:
                    raise AccountStoreCapacityError()
                account = {
                    "id": secrets.token_hex(16),
                    "username": username,
                    "username_key": username_key,
                    "email": email,
                    "role": role,
                    "disabled": False,
                    "created_at": now,
                    "password": password_record,
                    "passkey_credentials": [],
                    "recovery_codes": [],
                }
                recovery_codes, account["recovery_codes"] = self._recovery_codes(now)
                payload["accounts"].append(account)
                self._save(payload)
        return {"account": self._public_account(account), "recovery_codes": recovery_codes}

    def login(
        self,
        *,
        username: Any,
        password: Any,
        device_label: Any,
        nonce_session_id: str,
        presented_account_session_id: str | None = None,
        nonce: Any,
        remote: bool,
        source_id: str = "",
    ) -> dict[str, Any]:
        try:
            _, username_key = _normalize_username(username)
        except AccountAuthError:
            username_key = _INVALID_USERNAME_KEY
        label = _normalize_device_label(device_label)
        identity_rate_key, browser_rate_key = self._rate_keys(
            "login", username_key, nonce_session_id,
        )
        global_rate_key = self._rate_key("login-global", "", "")
        source_rate_key = self._rate_key(
            "login-source", str(source_id)[:256], "",
        )
        protected = frozenset(
            {identity_rate_key, browser_rate_key, global_rate_key, source_rate_key}
        )
        # Serialize only password work, not every account-store operation.
        # The nonce and durable gates are committed before releasing the
        # store lock, then the account/password snapshot is revalidated.
        with self._kdf_lock:
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                self._consume_nonce_for_mutation_locked(
                    payload, session_id=nonce_session_id, nonce=nonce,
                    purpose="login", now=now,
                )
                retry_after = max(
                    self._retry_after(payload, identity_rate_key, now),
                    self._retry_after(payload, browser_rate_key, now),
                    self._retry_after(payload, source_rate_key, now),
                    self._retry_after(payload, global_rate_key, now),
                )
                if retry_after:
                    self._save(payload)
                    raise AccountAuthError(
                        "Login is temporarily limited.", code="rate_limited",
                        retry_after=retry_after,
                    )
                try:
                    self._reserve_attempt_capacity(payload, protected, now)
                except AccountAuthError:
                    self._save(payload)
                    raise
                candidate = self._find_account(payload, username_key)
                account_id = None if candidate is None else candidate["id"]
                password_record = dict(
                    candidate["password"]
                    if candidate is not None else self._dummy_password_record
                )
                self._save(payload)

            password_valid = self._verify_password(password, password_record)
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                account = (
                    None if account_id is None
                    else self._account_by_id(payload, account_id)
                )
                valid = bool(
                    account is not None
                    and account["disabled"] is False
                    and account["username_key"] == username_key
                    and account["password"] == password_record
                    and password_valid
                )
                if not valid:
                    delays = [
                        self._record_failure(
                            payload, global_rate_key, now,
                            _GLOBAL_KDF_FREE_FAILURES, protected=protected,
                        ),
                        self._record_failure(
                            payload, source_rate_key, now, 8,
                            protected=protected,
                        ),
                        self._record_failure(
                            payload, browser_rate_key, now, 5,
                            protected=protected,
                        ),
                        self._record_failure(
                            payload, identity_rate_key, now, 12,
                            protected=protected,
                        ),
                    ]
                    retry_after = max(delays)
                    self._save(payload)
                    raise AccountAuthError(
                        "Username or password is invalid.", code="invalid_credentials",
                        retry_after=retry_after,
                    )
                payload["attempts"].pop(identity_rate_key, None)
                payload["attempts"].pop(browser_rate_key, None)
                payload["attempts"].pop(source_rate_key, None)
                if presented_account_session_id:
                    presented_digest = self._digest(
                        _SESSION_DIGEST_DOMAIN, presented_account_session_id,
                    )
                    for session in payload["sessions"]:
                        if session["revoked_at"] is None and hmac.compare_digest(
                            session["session_digest"], presented_digest,
                        ):
                            session["revoked_at"] = now
                session_id, _ = self._new_session(
                    payload, account_id=account["id"], device_label=label,
                    remote=remote, now=now, reauthenticated=True,
                )
                self._save(payload)
        return {
            "account": self._public_account(account),
            "account_session_id": session_id,
        }

    def _require_session_locked(
        self,
        payload: Mapping[str, Any],
        session_id: str,
        now: float,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        digest = self._digest(_SESSION_DIGEST_DOMAIN, session_id)
        session = next(
            (item for item in payload["sessions"] if hmac.compare_digest(item["session_digest"], digest)),
            None,
        )
        account = None if session is None else self._account_by_id(payload, session["account_id"])
        if (
            session is None
            or session["revoked_at"] is not None
            or session["expires_at"] <= now
            or account is None
            or account["disabled"] is True
        ):
            raise AccountAuthError("Authentication is required.", code="authentication_required")
        return account, session

    def _require_session_for_event_locked(
        self,
        payload: dict[str, Any],
        session_id: str,
        now: float,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            return self._require_session_locked(payload, session_id, now)
        except AccountAuthError:
            digest = self._digest(_SESSION_DIGEST_DOMAIN, session_id)
            candidate = next(
                (
                    item for item in payload["sessions"]
                    if hmac.compare_digest(item["session_digest"], digest)
                ),
                None,
            )
            if (
                candidate is not None
                and float(payload["clock_high_water"])
                < float(candidate["expires_at"]) <= now
            ):
                self._save(payload)
            raise

    def _require_recent_reauth_for_event_locked(
        self,
        payload: dict[str, Any],
        session: Mapping[str, Any],
        now: float,
    ) -> None:
        deadline = float(session.get("reauthenticated_until") or 0.0)
        if deadline <= now:
            if float(payload["clock_high_water"]) < deadline <= now:
                self._save(payload)
            raise AccountAuthError(
                "Recent password confirmation is required.",
                code="reauth_required",
            )

    def _require_owner_reauth(
        self,
        payload: dict[str, Any],
        account: Mapping[str, Any],
        session: Mapping[str, Any],
        now: float,
    ) -> None:
        if account.get("role") != "owner":
            raise AccountAuthError("Owner access is required.", code="owner_required")
        self._require_recent_reauth_for_event_locked(payload, session, now)

    def resolve_session(self, session_id: str) -> dict[str, Any] | None:
        observed_now = float(self._clock())
        with self._lock:
            payload = self._load()
            previous_high_water = float(payload["clock_high_water"])
            now = self._monotonic_event_time(payload, observed_now)
            digest = self._digest(_SESSION_DIGEST_DOMAIN, session_id)
            candidate = next(
                (
                    item for item in payload["sessions"]
                    if hmac.compare_digest(item["session_digest"], digest)
                ),
                None,
            )
            session_expiry_crossed = bool(
                candidate is not None
                and previous_high_water < float(candidate["expires_at"]) <= now
            )
            try:
                account, session = self._require_session_locked(payload, session_id, now)
            except AccountAuthError:
                if session_expiry_crossed:
                    self._save(payload)
                return None
            reauth_transitioned_to_expired = bool(
                float(session["reauthenticated_until"]) > previous_high_water
                and float(session["reauthenticated_until"]) <= now
            )
            activity_checkpoint = now - float(session["last_seen_at"]) >= 5 * 60
            if activity_checkpoint:
                session["last_seen_at"] = now
            if reauth_transitioned_to_expired or activity_checkpoint:
                self._save(payload)
            return {
                **self._public_account(account),
                "session_handle": session["handle"],
                "session_created_at": float(session["created_at"]),
                "reauthenticated_until": float(session["reauthenticated_until"]),
                "recently_reauthenticated": bool(
                    float(session["reauthenticated_until"]) > now
                ),
            }

    def reauthenticate(
        self, *, account_session_id: str, password: Any, nonce: Any,
    ) -> dict[str, Any]:
        with self._kdf_lock:
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                account, session = self._require_session_for_event_locked(
                    payload, account_session_id, now,
                )
                self._consume_nonce_for_mutation_locked(
                    payload, session_id=account_session_id, nonce=nonce,
                    purpose="reauth", now=now,
                )
                rate_key = self._rate_key(
                    "reauth", account["id"], account_session_id,
                )
                global_rate_key = self._rate_key("reauth-global", "", "")
                retry_after = max(
                    self._retry_after(payload, rate_key, now),
                    self._retry_after(payload, global_rate_key, now),
                )
                if retry_after:
                    self._save(payload)
                    raise AccountAuthError(
                        "Password confirmation is temporarily limited.",
                        code="rate_limited", retry_after=retry_after,
                    )
                try:
                    self._reserve_attempt_capacity(
                        payload, frozenset({rate_key, global_rate_key}), now,
                    )
                except AccountAuthError:
                    self._save(payload)
                    raise
                account_id = account["id"]
                password_record = dict(account["password"])
                self._save(payload)

            password_valid = self._verify_password(password, password_record)
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                account, session = self._require_session_for_event_locked(
                    payload, account_session_id, now,
                )
                valid = bool(
                    account["id"] == account_id
                    and account["password"] == password_record
                    and password_valid
                )
                if not valid:
                    protected = frozenset({rate_key, global_rate_key})
                    retry_after = max(
                        self._record_failure(
                            payload, rate_key, now, 3, protected=protected,
                        ),
                        self._record_failure(
                            payload, global_rate_key, now, 8,
                            protected=protected,
                        ),
                    )
                    self._save(payload)
                    raise AccountAuthError(
                        "Password is invalid.", code="invalid_credentials",
                        retry_after=retry_after,
                    )
                payload["attempts"].pop(rate_key, None)
                session["revoked_at"] = now
                rotated_session_id, rotated = self._new_session(
                    payload,
                    account_id=account["id"],
                    device_label=session["device_label"],
                    remote=bool(session["remote_created"]),
                    now=now,
                    reauthenticated=True,
                )
                self._save(payload)
                return {
                    "reauthenticated_until": rotated["reauthenticated_until"],
                    "account": self._public_account(account),
                    "account_session_id": rotated_session_id,
                }

    def list_sessions(self, session_id: str) -> list[dict[str, Any]]:
        now = float(self._clock())
        with self._lock:
            payload = self._load()
            previous_high_water = float(payload["clock_high_water"])
            now = self._monotonic_event_time(payload, now)
            account, current = self._require_session_for_event_locked(
                payload, session_id, now,
            )
            sibling_expiry_crossed = any(
                item["account_id"] == account["id"]
                and item["revoked_at"] is None
                and previous_high_water < float(item["expires_at"]) <= now
                for item in payload["sessions"]
            )
            sessions = []
            for item in payload["sessions"]:
                if (
                    item["account_id"] != account["id"]
                    or item["revoked_at"] is not None
                    or item["expires_at"] <= now
                ):
                    continue
                sessions.append({
                    "id": item["handle"],
                    "device_label": item["device_label"],
                    "remote_created": bool(item["remote_created"]),
                    "created_at": float(item["created_at"]),
                    "last_seen_at": float(item["last_seen_at"]),
                    "expires_at": float(item["expires_at"]),
                    "current": item["handle"] == current["handle"],
                })
            if sibling_expiry_crossed:
                self._save(payload)
            return sorted(sessions, key=lambda item: item["created_at"], reverse=True)

    def revoke_session(
        self,
        *,
        actor_session_id: str,
        target_handle: str,
        nonce: Any,
    ) -> dict[str, Any]:
        now = float(self._clock())
        with self._lock:
            payload = self._load()
            now = self._monotonic_event_time(payload, now)
            actor, actor_session = self._require_session_for_event_locked(
                payload, actor_session_id, now,
            )
            self._consume_nonce_for_mutation_locked(
                payload, session_id=actor_session_id, nonce=nonce,
                purpose="revoke_session", now=now,
            )
            target = next(
                (item for item in payload["sessions"] if item["handle"] == target_handle),
                None,
            )
            if target is None or target["account_id"] != actor["id"]:
                self._save(payload)
                raise AccountAuthError("Session was not found.", code="session_not_found")
            target["revoked_at"] = now
            self._save(payload)
            return {"revoked": True, "current": target["handle"] == actor_session["handle"]}

    def revoke_all_sessions(
        self,
        *,
        actor_session_id: str,
        nonce: Any,
        retain_current: bool,
    ) -> dict[str, Any]:
        now = float(self._clock())
        with self._lock:
            payload = self._load()
            now = self._monotonic_event_time(payload, now)
            account, current = self._require_session_for_event_locked(
                payload, actor_session_id, now,
            )
            self._require_recent_reauth_for_event_locked(payload, current, now)
            self._consume_nonce_for_mutation_locked(
                payload, session_id=actor_session_id, nonce=nonce,
                purpose="revoke_all_sessions", now=now,
            )
            revoked = 0
            for item in payload["sessions"]:
                if (
                    item["account_id"] == account["id"]
                    and item["revoked_at"] is None
                    and not (retain_current and item["handle"] == current["handle"])
                ):
                    item["revoked_at"] = now
                    revoked += 1
            self._save(payload)
            return {"revoked": revoked, "current_revoked": not retain_current}

    def change_password(
        self,
        *,
        session_id: str,
        new_password: Any,
        nonce: Any,
    ) -> None:
        new_password = _validate_password(new_password)
        with self._kdf_lock:
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                account, session = self._require_session_for_event_locked(
                    payload, session_id, now,
                )
                self._require_recent_reauth_for_event_locked(payload, session, now)
                self._consume_nonce_for_mutation_locked(
                    payload, session_id=session_id, nonce=nonce,
                    purpose="change_password", now=now,
                )
                account_id = account["id"]
                session_handle = session["handle"]
                self._save(payload)

            password_record = self._password_record(new_password)
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                account, session = self._require_session_for_event_locked(
                    payload, session_id, now,
                )
                self._require_recent_reauth_for_event_locked(payload, session, now)
                if (
                    account["id"] != account_id
                    or session["handle"] != session_handle
                ):
                    raise AccountAuthError(
                        "Recent password confirmation is required.",
                        code="reauth_required",
                    )
                account["password"] = password_record
                for item in payload["sessions"]:
                    if (
                        item["account_id"] == account["id"]
                        and item["handle"] != session["handle"]
                        and item["revoked_at"] is None
                    ):
                        item["revoked_at"] = now
                self._save(payload)

    def rotate_recovery_codes(self, *, session_id: str, nonce: Any) -> list[str]:
        now = float(self._clock())
        with self._lock:
            payload = self._load()
            now = self._monotonic_event_time(payload, now)
            self._prune(payload, now)
            account, session = self._require_session_for_event_locked(
                payload, session_id, now,
            )
            self._require_recent_reauth_for_event_locked(payload, session, now)
            self._consume_nonce_for_mutation_locked(
                payload, session_id=session_id, nonce=nonce,
                purpose="rotate_recovery_codes", now=now,
            )
            raw, account["recovery_codes"] = self._recovery_codes(now)
            self._save(payload)
            return raw

    def recover(
        self,
        *,
        username: Any,
        recovery_code: Any,
        new_password: Any,
        device_label: Any,
        nonce_session_id: str,
        nonce: Any,
        remote: bool,
        presented_account_session_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            _, username_key = _normalize_username(username)
        except AccountAuthError:
            username_key = _INVALID_USERNAME_KEY
        code = "".join(character for character in str(recovery_code or "").upper() if character != "-")
        new_password = _validate_password(new_password)
        label = _normalize_device_label(device_label)
        identity_rate_key, browser_rate_key = self._rate_keys(
            "recover", username_key, nonce_session_id,
        )
        protected = frozenset({identity_rate_key, browser_rate_key})
        digest = self._digest(_RECOVERY_DIGEST_DOMAIN, code)
        with self._kdf_lock:
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                self._consume_nonce_for_mutation_locked(
                    payload, session_id=nonce_session_id, nonce=nonce,
                    purpose="recover", now=now,
                )
                retry_after = max(
                    self._retry_after(payload, identity_rate_key, now),
                    self._retry_after(payload, browser_rate_key, now),
                )
                if retry_after:
                    self._save(payload)
                    raise AccountAuthError(
                        "Recovery is temporarily limited.", code="rate_limited",
                        retry_after=retry_after,
                    )
                try:
                    self._reserve_attempt_capacity(payload, protected, now)
                except AccountAuthError:
                    self._save(payload)
                    raise
                account = self._find_account(payload, username_key)
                recovery = None if account is None else next(
                    (
                        item for item in account["recovery_codes"]
                        if item["used_at"] is None
                        and hmac.compare_digest(item["digest"], digest)
                    ),
                    None,
                )
                if account is None or account["disabled"] is True or recovery is None:
                    retry_after = max(
                        self._record_failure(
                            payload, identity_rate_key, now, 8,
                            protected=protected,
                        ),
                        self._record_failure(
                            payload, browser_rate_key, now, 3,
                            protected=protected,
                        ),
                    )
                    self._save(payload)
                    raise AccountAuthError(
                        "Recovery information is invalid.",
                        code="invalid_recovery", retry_after=retry_after,
                    )
                account_id = account["id"]
                self._save(payload)

            password_record = self._password_record(new_password)
            now = float(self._clock())
            with self._lock:
                payload = self._load()
                now = self._monotonic_event_time(payload, now)
                self._prune(payload, now)
                account = self._account_by_id(payload, account_id)
                recovery = None if account is None else next(
                    (
                        item for item in account["recovery_codes"]
                        if item["used_at"] is None
                        and hmac.compare_digest(item["digest"], digest)
                    ),
                    None,
                )
                if account is None or account["disabled"] is True or recovery is None:
                    raise AccountAuthError(
                        "Recovery information is invalid.", code="invalid_recovery",
                    )
                payload["attempts"].pop(identity_rate_key, None)
                payload["attempts"].pop(browser_rate_key, None)
                recovery["used_at"] = now
                account["password"] = password_record
                for session in payload["sessions"]:
                    if (
                        session["account_id"] == account["id"]
                        and session["revoked_at"] is None
                    ):
                        session["revoked_at"] = now
                if presented_account_session_id:
                    presented_digest = self._digest(
                        _SESSION_DIGEST_DOMAIN, presented_account_session_id,
                    )
                    for session in payload["sessions"]:
                        if (
                            session["revoked_at"] is None
                            and hmac.compare_digest(
                                session["session_digest"], presented_digest,
                            )
                        ):
                            session["revoked_at"] = now
                replacement_codes, account["recovery_codes"] = self._recovery_codes(now)
                session_id, _ = self._new_session(
                    payload, account_id=account["id"], device_label=label,
                    remote=remote, now=now, reauthenticated=True,
                )
                self._save(payload)
        return {
            "account": self._public_account(account),
            "account_session_id": session_id,
            "recovery_codes": replacement_codes,
        }

    def list_accounts(self, actor_session_id: str) -> list[dict[str, Any]]:
        now = float(self._clock())
        with self._lock:
            payload = self._load()
            now = self._monotonic_event_time(payload, now)
            actor, session = self._require_session_for_event_locked(
                payload, actor_session_id, now,
            )
            self._require_owner_reauth(payload, actor, session, now)
            return [self._public_account(account) for account in payload["accounts"]]

    def set_account_disabled(
        self,
        *,
        actor_session_id: str,
        account_id: str,
        disabled: bool,
        nonce: Any,
    ) -> None:
        if type(disabled) is not bool:
            raise AccountAuthError("Disabled state must be boolean.", code="invalid_disabled_state")
        now = float(self._clock())
        with self._lock:
            payload = self._load()
            now = self._monotonic_event_time(payload, now)
            self._prune(payload, now)
            actor, actor_session = self._require_session_for_event_locked(
                payload, actor_session_id, now,
            )
            self._require_owner_reauth(payload, actor, actor_session, now)
            self._consume_nonce_for_mutation_locked(
                payload, session_id=actor_session_id, nonce=nonce,
                purpose="disable_account", now=now,
            )
            target = self._account_by_id(payload, account_id)
            if target is None:
                self._save(payload)
                raise AccountAuthError("Account was not found.", code="account_not_found")
            if target["id"] == actor["id"] and disabled:
                self._save(payload)
                raise AccountAuthError("The active owner cannot disable itself.", code="self_disable_rejected")
            target["disabled"] = disabled
            if disabled:
                for session in payload["sessions"]:
                    if session["account_id"] == target["id"] and session["revoked_at"] is None:
                        session["revoked_at"] = now
            self._save(payload)
