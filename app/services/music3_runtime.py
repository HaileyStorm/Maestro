"""Fail-closed local lifecycle for the pinned MiniMax Music 3 experiment.

The runtime is deliberately separate from Maestro's application environment.
Provisioning consumes an already-staged, locally attested generation; this
module never downloads packages or models and never reaches a remote service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

if os.name == "posix":
    import fcntl

from services.minimax_music3_sglang_contract import (
    MUSIC3_HF_REVISION,
    MUSIC3_MODEL_ID,
)

RUNTIME_SCHEMA = "maestro.music3.runtime.v1"
STAGE_SCHEMA = "maestro.music3.runtime-stage.v1"
PROCESS_SCHEMA = "maestro.music3.runtime-process.v1"
RESET_SCHEMA = "maestro.music3.runtime-reset.v1"

PINNED_SGLANG_SOURCE_REVISION = (
    "git:573ce7963fa7b95596459957a195c87cf60cda19"
)
PINNED_MODEL_REVISION = f"git:{MUSIC3_HF_REVISION}"
PINNED_UCX_VERSION = "1.20.1"
PINNED_UCX_SOURCE_REVISION = "git:d8e50df6651b9ea5b76f23aee0aefbf053a4137a"
PINNED_UCX_TARBALL_SHA256 = (
    "sha256:545c419a7b5e04643cb8bff5a19b3b5071a8f8f0605f1e8efb36f8f3d7bfb9d3"
)
PINNED_UCX_TARBALL_SIZE = 3_505_964
PINNED_UCX_CONFIGURE_FLAGS = (
    "--enable-mt",
    "--enable-shared",
    "--disable-static",
    "--disable-doxygen-doc",
    "--enable-optimizations",
    "--enable-cma",
    "--enable-devel-headers",
    "--with-cuda=/usr/local/cuda",
    "--with-verbs",
    "--with-dm",
)
REQUIRED_RUNTIME_LOCK_LINES = frozenset({
    "cryptography==50.0.0",
    "torch==2.11.0",
    "torchvision==0.26.0",
    "sglang==0.5.16",
    "transformers==5.12.1",
    "flashinfer-python[cu13]==0.6.14",
    "flash-attn-4==4.0.0b18",
})
_EXACT_REQUIREMENT = re.compile(
    r"[a-z0-9][a-z0-9._-]*(?:\[[a-z0-9,._-]+\])?=="
    r"[a-z0-9][a-z0-9.!+_-]*"
)

RUNTIME_RELATIVE_ROOT = Path("runtime") / "maestro" / "minimax-music3"
STAGE_MANIFEST_NAME = ".maestro-stage.json"
GENERATION_LOCK_NAME = ".maestro-generation.lock"
PYTHON_RUNTIME_RECORD = "env/.maestro-python-runtime.json"
CUDA_RUNTIME_RECORD = "env/.maestro-cuda-runtime.json"
CURRENT_MARKER_NAME = "current.json"
PROCESS_MARKER_NAME = "process.json"
MAX_JSON_BYTES = 128 * 1024
MIN_PROVISION_FREE_BYTES = 200 * 1024**3
_CONTENT_ADDRESS = re.compile(r"(?:git:[0-9a-f]{40}|sha256:[0-9a-f]{64})")
_GENERATION_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ARTIFACT_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}")
_LIFECYCLE_THREAD_LOCK = threading.RLock()
_SERVER_GATE_CODE = """\
import os
import sys

token = sys.stdin.buffer.readline()
if token != b"go\\n":
    sys.exit(125)
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
"""
_SUPERVISOR_CODE = f"""\
import os
import signal
import subprocess
import sys
import time

termination_requested = False


def request_termination(_number, _frame):
    global termination_requested
    termination_requested = True


def group_members():
    own_pid = os.getpid()
    own_group = os.getpgrp()
    members = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == own_pid:
            continue
        try:
            raw = open(f"/proc/{{name}}/stat", encoding="ascii").read()
            close = raw.rfind(")")
            fields = raw[close + 2:].split()
            if close > 0 and len(fields) >= 3 and int(fields[2]) == own_group:
                members.append(int(name))
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return members


signal.signal(signal.SIGTERM, request_termination)
token = sys.stdin.buffer.readline()
if token != b"go\\n":
    sys.exit(125)
if termination_requested:
    sys.exit(143)
child = subprocess.Popen(
    [sys.executable, "-I", "-S", "-c", {_SERVER_GATE_CODE!r}, *sys.argv[1:]],
    stdin=subprocess.PIPE,
    shell=False,
)
if termination_requested:
    child.terminate()
else:
    try:
        child.stdin.write(b"go\\n")
        child.stdin.flush()
        child.stdin.close()
    except (BrokenPipeError, OSError):
        pass
return_code = child.wait()
while group_members():
    time.sleep(0.05)
sys.exit(return_code)
"""


class Music3RuntimeError(RuntimeError):
    """A content-free local runtime lifecycle failure."""


class Music3RuntimeConflict(Music3RuntimeError):
    """Current runtime state no longer matches a reviewed action."""


class Music3RuntimeSecurityError(Music3RuntimeError):
    """Filesystem or process ownership could not be proven."""


class _Process(Protocol):
    pid: int

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Music3RuntimeLayout:
    pinokio_root: Path
    root: Path
    generations: Path
    staging: Path
    state: Path
    reset_quarantine: Path

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def temporary(self) -> Path:
        return self.root / "tmp"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def current_marker(self) -> Path:
        return self.state / CURRENT_MARKER_NAME

    @property
    def process_marker(self) -> Path:
        return self.state / PROCESS_MARKER_NAME


@dataclass(frozen=True, slots=True)
class Music3ProvisionPlan:
    layout: Music3RuntimeLayout
    runtime_source_revision: str
    ucx_version: str
    ucx_source_revision: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": RUNTIME_SCHEMA,
            "platform": "linux",
            "accelerator": "nvidia",
            "model_id": MUSIC3_MODEL_ID,
            "model_revision": PINNED_MODEL_REVISION,
            "runtime_source_revision": self.runtime_source_revision,
            "ucx_version": self.ucx_version,
            "ucx_source_revision": self.ucx_source_revision,
            "ucx_source_tarball_sha256": PINNED_UCX_TARBALL_SHA256,
            "ucx_source_tarball_size": PINNED_UCX_TARBALL_SIZE,
            "ucx_configure_flags": list(PINNED_UCX_CONFIGURE_FLAGS),
            "network": {
                "bind_host": "127.0.0.1",
                "dynamic_port_required": True,
                "wan": False,
                "lan": False,
                "cloudflare": False,
                "rented_compute": False,
            },
            "paths": {
                "pinokio_root": str(self.layout.pinokio_root),
                "runtime_root": str(self.layout.root),
                "generations": str(self.layout.generations),
                "rollback_generations": str(self.layout.generations),
                "staging": str(self.layout.staging),
                "generation_lock_name": GENERATION_LOCK_NAME,
                "state": str(self.layout.state),
                "current_state": str(self.layout.current_marker),
                "process_state": str(self.layout.process_marker),
                "reset_quarantine": str(self.layout.reset_quarantine),
                "home": str(self.layout.home),
                "cache": str(self.layout.cache),
                "temporary": str(self.layout.temporary),
            },
            "minimum_free_bytes_before_provision": MIN_PROVISION_FREE_BYTES,
            "filesystem_acceptance": {
                "cross_process_flock": True,
                "directory_fsync": True,
                "executable_mode": True,
                "atomic_same_filesystem_replace": True,
                "symlink_detection": True,
            },
        }

    @property
    def sha256(self) -> str:
        return _mapping_sha256(self.to_mapping())


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _mapping_sha256(value: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _regular_file_sha256(path: Path) -> str:
    before = os.lstat(path)
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
    ):
        raise Music3RuntimeSecurityError("runtime artifact ownership is ambiguous")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
        ):
            raise Music3RuntimeSecurityError("runtime artifact changed during validation")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    current = os.lstat(path)
    if (
        after.st_dev != current.st_dev
        or after.st_ino != current.st_ino
        or after.st_size != current.st_size
        or after.st_mtime_ns != current.st_mtime_ns
    ):
        raise Music3RuntimeSecurityError("runtime artifact changed during hashing")
    return "sha256:" + digest.hexdigest()


def music3_tree_sha256(root: Path, *, expected_device: int | None = None) -> str:
    """Hash one owned, link-free artifact tree by path, mode, size, and bytes."""

    _safe_tree(root, expected_device=expected_device)
    entries: list[tuple[str, str, int, int]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        info = os.lstat(path)
        if stat.S_ISDIR(info.st_mode):
            entries.append((relative, "dir", info.st_mode & 0o777, 0))
        elif stat.S_ISREG(info.st_mode):
            entries.append((
                relative,
                _regular_file_sha256(path),
                info.st_mode & 0o777,
                info.st_size,
            ))
        else:
            raise Music3RuntimeSecurityError("artifact tree contains an unsupported entry")
    if not any(entry[1] != "dir" for entry in entries):
        raise Music3RuntimeSecurityError("artifact tree cannot be empty")
    return _mapping_sha256({"entries": entries})


def _content_address(value: object, *, label: str) -> str:
    if type(value) is not str or _CONTENT_ADDRESS.fullmatch(value) is None:
        raise Music3RuntimeError(f"{label} must be an exact content address")
    return value


def _existing_path_is_safe(path: Path, *, directory: bool) -> None:
    info = os.lstat(path)
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not expected
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or (not directory and info.st_nlink != 1)
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise Music3RuntimeSecurityError("runtime path ownership is ambiguous")


def _assert_runtime_path(
    layout: Music3RuntimeLayout,
    path: Path,
    *,
    allow_missing: bool,
) -> None:
    if path != layout.root and layout.root not in path.parents:
        raise Music3RuntimeSecurityError("runtime path escaped the dedicated root")
    _assert_no_symlink_components(path)
    expected_device = layout.pinokio_root.stat().st_dev
    current = layout.pinokio_root
    relative = path.relative_to(layout.pinokio_root)
    for component in relative.parts:
        current /= component
        if not current.exists():
            if current.is_symlink() or not allow_missing:
                raise Music3RuntimeSecurityError("runtime path is unavailable")
            break
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or info.st_dev != expected_device:
            raise Music3RuntimeSecurityError("runtime path crosses a link or filesystem")
        protected_runtime_component = current == layout.root or layout.root in current.parents
        if info.st_uid != os.geteuid() or (
            protected_runtime_component
            and info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise Music3RuntimeSecurityError("runtime path ownership is ambiguous")


def _assert_no_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if (current.exists() or current.is_symlink()) and stat.S_ISLNK(
            os.lstat(current).st_mode
        ):
            raise Music3RuntimeSecurityError("runtime paths cannot contain symlinks")


def _path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _ensure_private_runtime_directory(
    layout: Music3RuntimeLayout,
    path: Path,
) -> None:
    """Create each missing runtime component privately, never via permissive parents."""

    if path != layout.root and layout.root not in path.parents:
        raise Music3RuntimeSecurityError("runtime directory escaped the dedicated root")
    missing: list[Path] = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise Music3RuntimeSecurityError("runtime paths cannot contain symlinks")
        missing.append(current)
        if current == layout.root:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    _assert_runtime_path(layout, path, allow_missing=False)
    _existing_path_is_safe(path, directory=True)


def _forbidden_runtime_location(path: Path) -> bool:
    forbidden = (Path("/home"), Path("/root"), Path("/tmp"), Path("/var/tmp"))
    return any(path == item or item in path.parents for item in forbidden)


def resolve_music3_runtime_layout(
    pinokio_root: str | os.PathLike[str],
) -> Music3RuntimeLayout:
    supplied = Path(pinokio_root)
    if not supplied.is_absolute():
        raise Music3RuntimeSecurityError("Pinokio root must be absolute")
    _assert_no_symlink_components(supplied)
    try:
        root = supplied.resolve(strict=True)
    except OSError as error:
        raise Music3RuntimeSecurityError("Pinokio root is unavailable") from error
    if root.name != "pinokio" or _forbidden_runtime_location(root):
        raise Music3RuntimeSecurityError("runtime must use a dedicated non-home Pinokio root")
    _existing_path_is_safe(root, directory=True)
    runtime_root = root / RUNTIME_RELATIVE_ROOT
    layout = Music3RuntimeLayout(
        pinokio_root=root,
        root=runtime_root,
        generations=runtime_root / "generations",
        staging=runtime_root / "staging",
        state=runtime_root / "state",
        reset_quarantine=runtime_root / "reset-quarantine",
    )
    _assert_runtime_path(layout, layout.root, allow_missing=True)
    return layout


@contextmanager
def _lifecycle_lock(layout: Music3RuntimeLayout):
    if os.name != "posix":
        raise Music3RuntimeError("Music 3 runtime lifecycle locking requires Linux")
    with _LIFECYCLE_THREAD_LOCK:
        _assert_runtime_path(layout, layout.state, allow_missing=True)
        _ensure_private_runtime_directory(layout, layout.state)
        state_before = os.lstat(layout.state)
        lock_path = layout.state / "lifecycle.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        lock_before = os.lstat(lock_path)
        released = False

        def verify_lock_identity() -> None:
            try:
                opened = os.fstat(descriptor)
                current = os.lstat(lock_path)
                state_current = os.lstat(layout.state)
            except OSError as error:
                raise Music3RuntimeSecurityError(
                    "lifecycle lock identity became unavailable"
                ) from error
            if (
                (opened.st_dev, opened.st_ino)
                != (lock_before.st_dev, lock_before.st_ino)
                or (current.st_dev, current.st_ino)
                != (opened.st_dev, opened.st_ino)
                or (state_current.st_dev, state_current.st_ino)
                != (state_before.st_dev, state_before.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or not stat.S_ISDIR(state_current.st_mode)
                or opened.st_nlink != 1
                or current.st_nlink != 1
                or opened.st_uid != os.geteuid()
                or current.st_uid != os.geteuid()
                or state_current.st_uid != os.geteuid()
                or opened.st_dev != layout.pinokio_root.stat().st_dev
                or state_current.st_dev != layout.pinokio_root.stat().st_dev
                or opened.st_size != 0
                or current.st_size != 0
                or stat.S_IMODE(opened.st_mode) != 0o600
                or stat.S_IMODE(current.st_mode) != 0o600
                or state_current.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise Music3RuntimeSecurityError("lifecycle lock identity split")

        def release() -> None:
            nonlocal released
            if released:
                return
            verify_lock_identity()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError as error:
                raise Music3RuntimeSecurityError(
                    "lifecycle lock release failed before commit boundary"
                ) from error
            released = True
            try:
                os.close(descriptor)
            except OSError:
                # LOCK_UN is the state boundary; a later close error cannot
                # truthfully turn the already-released transaction into failure.
                pass

        try:
            verify_lock_identity()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            verify_lock_identity()
            yield release
        finally:
            if not released:
                try:
                    release()
                finally:
                    if not released:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass


@contextmanager
def _generation_lock(
    layout: Music3RuntimeLayout,
    generation: Path,
    *,
    exclusive: bool,
):
    """Coordinate the final generation with its compliant stage builder."""

    _assert_runtime_path(layout, generation, allow_missing=False)
    generation_before = os.lstat(generation)
    lock_path = generation / GENERATION_LOCK_NAME
    _assert_runtime_path(layout, lock_path, allow_missing=False)
    before = os.lstat(lock_path)
    descriptor = os.open(
        lock_path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )

    def verify_locked_identity() -> None:
        try:
            opened = os.fstat(descriptor)
            current = os.lstat(lock_path)
            generation_current = os.lstat(generation)
        except OSError as error:
            raise Music3RuntimeSecurityError(
                "generation lock identity became unavailable"
            ) from error
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            or (generation_current.st_dev, generation_current.st_ino)
            != (generation_before.st_dev, generation_before.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or not stat.S_ISDIR(generation_current.st_mode)
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or generation_current.st_uid != os.geteuid()
            or opened.st_dev != layout.pinokio_root.stat().st_dev
            or generation_current.st_dev != layout.pinokio_root.stat().st_dev
            or opened.st_size != 0
            or current.st_size != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(current.st_mode) != 0o600
            or generation_current.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise Music3RuntimeSecurityError("generation lock identity split")

    try:
        verify_locked_identity()
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(descriptor, operation)
        verify_locked_identity()
        yield verify_locked_identity
    finally:
        try:
            verify_locked_identity()
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def build_music3_provision_plan(
    pinokio_root: str | os.PathLike[str],
    *,
    runtime_source_revision: object = PINNED_SGLANG_SOURCE_REVISION,
    ucx_version: object,
    ucx_source_revision: object,
) -> Music3ProvisionPlan:
    return _build_plan(
        pinokio_root,
        runtime_source_revision=runtime_source_revision,
        ucx_version=ucx_version,
        ucx_source_revision=ucx_source_revision,
        check_free_space=True,
    )


def _build_plan(
    pinokio_root: str | os.PathLike[str],
    *,
    runtime_source_revision: object,
    ucx_version: object,
    ucx_source_revision: object,
    check_free_space: bool,
) -> Music3ProvisionPlan:
    runtime_revision = _content_address(
        runtime_source_revision,
        label="SGLang-Omni source revision",
    )
    if runtime_revision != PINNED_SGLANG_SOURCE_REVISION:
        raise Music3RuntimeError("SGLang-Omni source revision is not the reviewed pin")
    if type(ucx_version) is not str or ucx_version != PINNED_UCX_VERSION:
        raise Music3RuntimeError("UCX version is not the reviewed pin")
    ucx_revision = _content_address(ucx_source_revision, label="UCX source revision")
    if ucx_revision != PINNED_UCX_SOURCE_REVISION:
        raise Music3RuntimeError("UCX source revision is not the reviewed pin")
    plan = Music3ProvisionPlan(
        layout=resolve_music3_runtime_layout(pinokio_root),
        runtime_source_revision=runtime_revision,
        ucx_version=ucx_version,
        ucx_source_revision=ucx_revision,
    )
    available = shutil.disk_usage(plan.layout.pinokio_root).free
    if check_free_space and available < MIN_PROVISION_FREE_BYTES:
        raise Music3RuntimeError("dedicated runtime storage has insufficient free space")
    return plan


def _constant_time_equal(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except UnicodeError:
        return False


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise Music3RuntimeSecurityError("runtime marker contains duplicate fields")
        value[key] = item
    return value


def _read_json(path: Path) -> dict[str, object]:
    before = os.lstat(path)
    _existing_path_is_safe(path, directory=False)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_size > MAX_JSON_BYTES
            ):
                raise Music3RuntimeSecurityError("runtime marker changed during validation")
            payload = handle.read(MAX_JSON_BYTES + 1)
        current = os.lstat(path)
        if (
            current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
            or current.st_size != opened.st_size
            or current.st_mtime_ns != opened.st_mtime_ns
        ):
            raise Music3RuntimeSecurityError("runtime marker changed during reading")
        if len(payload) > MAX_JSON_BYTES:
            raise Music3RuntimeSecurityError("runtime marker is too large")
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
        )
    except Music3RuntimeSecurityError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise Music3RuntimeSecurityError("runtime marker is invalid") from error
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise Music3RuntimeSecurityError("runtime marker must be a plain mapping")
    return value


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_generation_tree(root: Path, *, expected_device: int) -> None:
    """Durably order every reviewed generation byte before state publication."""

    _safe_tree(root, expected_device=expected_device)
    directories = [root]
    for current, child_directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.extend(current_path / name for name in child_directories)
        for name in files:
            path = current_path / name
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _sync_directory(directory)
    _sync_directory(root.parent)


def _filesystem_type(path: Path) -> str:
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise Music3RuntimeSecurityError("runtime filesystem type is unavailable") from error
    resolved = path.resolve(strict=True)
    matches: list[tuple[int, str]] = []
    for line in lines:
        before, separator, after = line.partition(" - ")
        fields = before.split()
        post = after.split()
        if not separator or len(fields) < 5 or not post:
            continue
        mount_text = fields[4]
        for escaped, literal in (("\\040", " "), ("\\011", "\t"), ("\\134", "\\")):
            mount_text = mount_text.replace(escaped, literal)
        mount = Path(mount_text)
        if resolved == mount or mount in resolved.parents:
            matches.append((len(mount.parts), post[0]))
    if not matches:
        raise Music3RuntimeSecurityError("runtime filesystem mount is ambiguous")
    return max(matches)[1]


def _filesystem_capability_evidence(layout: Music3RuntimeLayout) -> dict[str, object]:
    """Exercise every filesystem primitive relied on by publication and recovery."""

    if os.name != "posix" or not hasattr(os, "fork"):
        raise Music3RuntimeSecurityError("runtime filesystem acceptance requires Linux")
    _assert_runtime_path(layout, layout.root, allow_missing=False)
    probe = layout.root / f".fs-capability-{os.getpid()}-{time.time_ns()}"
    file_path = probe / "executable"
    renamed_path = probe / "renamed"
    link_path = probe / "link"
    lock_path = probe / "lock"
    probe.mkdir(mode=0o700)
    try:
        descriptor = os.open(
            file_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o700,
        )
        try:
            os.write(descriptor, b"#!/bin/sh\nexit 0\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _existing_path_is_safe(file_path, directory=False)
        if not os.access(file_path, os.X_OK):
            raise Music3RuntimeSecurityError("runtime filesystem does not preserve executable mode")
        link_path.symlink_to(file_path.name)
        if not stat.S_ISLNK(os.lstat(link_path).st_mode):
            raise Music3RuntimeSecurityError("runtime filesystem does not expose symlinks")
        link_path.unlink()
        original = os.lstat(file_path)
        os.replace(file_path, renamed_path)
        renamed = os.lstat(renamed_path)
        if file_path.exists() or (original.st_dev, original.st_ino) != (renamed.st_dev, renamed.st_ino):
            raise Music3RuntimeSecurityError("runtime filesystem atomic replace is not proven")
        lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                try:
                    os.close(read_fd)
                    child_descriptor = os.open(lock_path, os.O_RDWR)
                    try:
                        fcntl.flock(child_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        os.write(write_fd, b"blocked")
                    else:
                        os.write(write_fd, b"acquired")
                    finally:
                        os.close(child_descriptor)
                finally:
                    os.close(write_fd)
                    os._exit(0)
            os.close(write_fd)
            try:
                lock_result = os.read(read_fd, 16)
            finally:
                os.close(read_fd)
                _pid, status = os.waitpid(pid, 0)
            if lock_result != b"blocked" or status != 0:
                raise Music3RuntimeSecurityError("runtime filesystem cross-process flock is not proven")
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        _sync_directory(probe)
        return {
            "schema": "maestro.music3.filesystem-capability.v1",
            "filesystem_type": _filesystem_type(layout.root),
            "cross_process_flock": True,
            "directory_fsync": True,
            "executable_mode": True,
            "atomic_same_filesystem_replace": True,
            "symlink_detection": True,
        }
    except Music3RuntimeSecurityError:
        raise
    except OSError as error:
        raise Music3RuntimeSecurityError("runtime filesystem capability probe failed") from error
    finally:
        for candidate in (link_path, file_path, renamed_path, lock_path):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
        try:
            probe.rmdir()
            _sync_directory(layout.root)
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    _assert_no_symlink_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_no_symlink_components(path.parent)
    _existing_path_is_safe(path.parent, directory=True)
    temporary = path.with_name(f".{path.name}.stage-{os.getpid()}-{time.time_ns()}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _safe_tree(root: Path, *, expected_device: int | None = None) -> None:
    _assert_no_symlink_components(root)
    _existing_path_is_safe(root, directory=True)
    device = root.stat().st_dev if expected_device is None else expected_device
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        _existing_path_is_safe(current_path, directory=True)
        if current_path.stat().st_dev != device:
            raise Music3RuntimeSecurityError("runtime tree crosses a filesystem boundary")
        for name in directories:
            _existing_path_is_safe(current_path / name, directory=True)
            if (current_path / name).stat().st_dev != device:
                raise Music3RuntimeSecurityError("runtime tree crosses a filesystem boundary")
        for name in files:
            _existing_path_is_safe(current_path / name, directory=False)
            if (current_path / name).stat().st_dev != device:
                raise Music3RuntimeSecurityError("runtime tree crosses a filesystem boundary")


def _runtime_artifact_record(value: object, *, role: str) -> dict[str, object]:
    if type(value) is not dict:
        raise Music3RuntimeError(f"{role} runtime record must be a plain mapping")
    common = {
        "version",
        "artifact_filename",
        "artifact_sha256",
        "artifact_size",
    }
    expected = (
        common | {"implementation", "abi"}
        if role == "python"
        else common | {"architecture"}
    )
    if set(value) != expected:
        raise Music3RuntimeError(f"{role} runtime record fields are not exact")
    filename = value.get("artifact_filename")
    digest = value.get("artifact_sha256")
    size = value.get("artifact_size")
    version = value.get("version")
    if (
        type(filename) is not str
        or _ARTIFACT_FILENAME.fullmatch(filename) is None
        or Path(filename).name != filename
        or type(digest) is not str
        or not digest.startswith("sha256:")
        or _CONTENT_ADDRESS.fullmatch(digest) is None
        or type(size) is not int
        or size <= 0
        or type(version) is not str
    ):
        raise Music3RuntimeError(f"{role} runtime artifact identity is invalid")
    if role == "python":
        match = re.fullmatch(r"3\.(1[0-2])\.([0-9]+)", version)
        expected_abi = None if match is None else f"cp3{match.group(1)}"
        if (
            value.get("implementation") != "cpython"
            or type(value.get("abi")) is not str
            or value.get("abi") != expected_abi
        ):
            raise Music3RuntimeError("Python runtime version and ABI are inconsistent")
    elif role == "cuda":
        if (
            re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version) is None
            or not version.startswith("13.")
            or value.get("architecture") != "linux-x86_64"
        ):
            raise Music3RuntimeError("CUDA runtime identity is invalid")
    else:
        raise AssertionError("unsupported runtime artifact role")
    return dict(value)


def _required_stage_document(
    plan: Music3ProvisionPlan,
    *,
    generation_id: str,
    runtime_executable_sha256: str,
    runtime_source_tree_sha256: str,
    dependency_lock_sha256: str,
    environment_tree_sha256: str,
    ucx_info_sha256: str,
    ucx_build_record_sha256: str,
    ucx_probe_sha256: str,
    model_snapshot_sha256: str,
    python_runtime: object,
    cuda_runtime: object,
) -> dict[str, object]:
    if _GENERATION_ID.fullmatch(generation_id) is None:
        raise Music3RuntimeError("generation ID is invalid")
    return {
        "schema": STAGE_SCHEMA,
        "generation_id": generation_id,
        "plan_sha256": plan.sha256,
        "model_id": MUSIC3_MODEL_ID,
        "model_revision": PINNED_MODEL_REVISION,
        "model_snapshot_sha256": _content_address(
            model_snapshot_sha256,
            label="model snapshot digest",
        ),
        "runtime_source_revision": plan.runtime_source_revision,
        "runtime_source_tree_sha256": _content_address(
            runtime_source_tree_sha256,
            label="runtime source tree digest",
        ),
        "runtime_executable": "env/bin/sgl-omni",
        "runtime_executable_sha256": _content_address(
            runtime_executable_sha256,
            label="runtime executable digest",
        ),
        "dependency_lock": "env/requirements.lock",
        "dependency_lock_sha256": _content_address(
            dependency_lock_sha256,
            label="runtime dependency lock digest",
        ),
        "environment_tree_sha256": _content_address(
            environment_tree_sha256,
            label="installed environment tree digest",
        ),
        "ucx_version": plan.ucx_version,
        "ucx_source_revision": plan.ucx_source_revision,
        "ucx_source_tarball_sha256": PINNED_UCX_TARBALL_SHA256,
        "ucx_source_tarball_size": PINNED_UCX_TARBALL_SIZE,
        "ucx_configure_flags": list(PINNED_UCX_CONFIGURE_FLAGS),
        "ucx_cuda_support": True,
        "ucx_info_executable": "env/bin/ucx_info",
        "ucx_info_sha256": _content_address(
            ucx_info_sha256,
            label="ucx_info digest",
        ),
        "ucx_build_record": "provenance/ucx-build.json",
        "ucx_build_record_sha256": _content_address(
            ucx_build_record_sha256,
            label="UCX build record digest",
        ),
        "ucx_probe_sha256": _content_address(
            ucx_probe_sha256,
            label="UCX probe digest",
        ),
        "source_directory": "source",
        "model_directory": "model",
        "environment_directory": "env",
        "offline_only": True,
        "generation_lock": GENERATION_LOCK_NAME,
        "python_runtime": _runtime_artifact_record(python_runtime, role="python"),
        "python_runtime_record": PYTHON_RUNTIME_RECORD,
        "cuda_runtime": _runtime_artifact_record(cuda_runtime, role="cuda"),
        "cuda_runtime_record": CUDA_RUNTIME_RECORD,
    }


def build_music3_stage_manifest(
    plan: Music3ProvisionPlan,
    *,
    generation_id: str,
    runtime_executable_sha256: str,
    runtime_source_tree_sha256: str,
    dependency_lock_sha256: str,
    environment_tree_sha256: str,
    ucx_info_sha256: str,
    ucx_build_record_sha256: str,
    ucx_probe_sha256: str,
    model_snapshot_sha256: str,
    python_runtime: object,
    cuda_runtime: object,
) -> dict[str, object]:
    """Return the exact marker a separate, offline provisioner must stage."""

    return _required_stage_document(
        plan,
        generation_id=generation_id,
        runtime_executable_sha256=runtime_executable_sha256,
        runtime_source_tree_sha256=runtime_source_tree_sha256,
        dependency_lock_sha256=dependency_lock_sha256,
        environment_tree_sha256=environment_tree_sha256,
        ucx_info_sha256=ucx_info_sha256,
        ucx_build_record_sha256=ucx_build_record_sha256,
        ucx_probe_sha256=ucx_probe_sha256,
        model_snapshot_sha256=model_snapshot_sha256,
        python_runtime=python_runtime,
        cuda_runtime=cuda_runtime,
    )


def _probe_ucx(executable: Path) -> bytes:
    environment_root = executable.parents[1]
    outputs: list[bytes] = []
    for argument in ("-v", "-d"):
        try:
            completed = subprocess.run(
                [str(executable), argument],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
                shell=False,
                timeout=30,
                env={
                    "HOME": "/nonexistent",
                    "PATH": f"{environment_root / 'bin'}:/usr/bin:/bin",
                    "LD_LIBRARY_PATH": str(environment_root / "lib"),
                    "NO_PROXY": "*",
                    "no_proxy": "*",
                },
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise Music3RuntimeSecurityError("UCX proof command failed") from error
        outputs.append(completed.stdout)
    return b"\n--- ucx_info -d ---\n".join(outputs)


def _validate_stage(
    plan: Music3ProvisionPlan,
    stage_root: Path,
    *,
    expected_stage_manifest_sha256: object,
    ucx_probe: Callable[[Path], bytes] | None = None,
) -> tuple[dict[str, object], str]:
    if stage_root.parent != plan.layout.generations or not stage_root.is_absolute():
        raise Music3RuntimeSecurityError(
            "generation must be one final-path child of the runtime generation root"
        )
    expected_device = plan.layout.pinokio_root.stat().st_dev
    _assert_runtime_path(plan.layout, stage_root, allow_missing=False)
    _safe_tree(stage_root, expected_device=expected_device)
    document = _read_json(stage_root / STAGE_MANIFEST_NAME)
    reviewed_manifest_sha256 = _content_address(
        expected_stage_manifest_sha256,
        label="independently reviewed stage manifest digest",
    )
    actual_manifest_sha256 = _mapping_sha256(document)
    if not _constant_time_equal(reviewed_manifest_sha256, actual_manifest_sha256):
        raise Music3RuntimeSecurityError(
            "stage marker is not the independently reviewed artifact manifest"
        )
    expected_keys = {
        "schema",
        "generation_id",
        "plan_sha256",
        "model_id",
        "model_revision",
        "model_snapshot_sha256",
        "runtime_source_revision",
        "runtime_source_tree_sha256",
        "runtime_executable",
        "runtime_executable_sha256",
        "dependency_lock",
        "dependency_lock_sha256",
        "environment_tree_sha256",
        "ucx_version",
        "ucx_source_revision",
        "ucx_source_tarball_sha256",
        "ucx_source_tarball_size",
        "ucx_configure_flags",
        "ucx_cuda_support",
        "ucx_info_executable",
        "ucx_info_sha256",
        "ucx_build_record",
        "ucx_build_record_sha256",
        "ucx_probe_sha256",
        "source_directory",
        "model_directory",
        "environment_directory",
        "offline_only",
        "generation_lock",
        "python_runtime",
        "python_runtime_record",
        "cuda_runtime",
        "cuda_runtime_record",
    }
    if set(document) != expected_keys:
        raise Music3RuntimeSecurityError("stage marker fields are not exact")
    expected = _required_stage_document(
        plan,
        generation_id=document.get("generation_id", ""),
        runtime_executable_sha256=document.get("runtime_executable_sha256", ""),
        runtime_source_tree_sha256=document.get("runtime_source_tree_sha256", ""),
        dependency_lock_sha256=document.get("dependency_lock_sha256", ""),
        environment_tree_sha256=document.get("environment_tree_sha256", ""),
        ucx_info_sha256=document.get("ucx_info_sha256", ""),
        ucx_build_record_sha256=document.get("ucx_build_record_sha256", ""),
        ucx_probe_sha256=document.get("ucx_probe_sha256", ""),
        model_snapshot_sha256=document.get("model_snapshot_sha256", ""),
        python_runtime=document.get("python_runtime"),
        cuda_runtime=document.get("cuda_runtime"),
    )
    if document != expected:
        raise Music3RuntimeSecurityError("stage marker does not match the reviewed plan")
    if document["generation_id"] != stage_root.name:
        raise Music3RuntimeSecurityError("generation ID does not match its final directory")
    executable = stage_root / "env" / "bin" / "sgl-omni"
    ucx_info = stage_root / "env" / "bin" / "ucx_info"
    dependency_lock = stage_root / "env" / "requirements.lock"
    python_runtime_record = stage_root / PYTHON_RUNTIME_RECORD
    cuda_runtime_record = stage_root / CUDA_RUNTIME_RECORD
    ucx_build_record = stage_root / "provenance" / "ucx-build.json"
    for directory in (stage_root / "source", stage_root / "model", stage_root / "env"):
        _existing_path_is_safe(directory, directory=True)
    for binary, digest_key in (
        (executable, "runtime_executable_sha256"),
        (ucx_info, "ucx_info_sha256"),
    ):
        if not os.access(binary, os.X_OK):
            raise Music3RuntimeSecurityError("runtime executable is not executable")
        if _regular_file_sha256(binary) != document[digest_key]:
            raise Music3RuntimeSecurityError("runtime executable digest does not match")
    if _regular_file_sha256(dependency_lock) != document["dependency_lock_sha256"]:
        raise Music3RuntimeSecurityError("runtime dependency lock digest does not match")
    if _regular_file_sha256(ucx_build_record) != document["ucx_build_record_sha256"]:
        raise Music3RuntimeSecurityError("UCX build record digest does not match")
    for path, expected_record in (
        (python_runtime_record, document["python_runtime"]),
        (cuda_runtime_record, document["cuda_runtime"]),
    ):
        _existing_path_is_safe(path, directory=False)
        if stat.S_IMODE(os.lstat(path).st_mode) != 0o600:
            raise Music3RuntimeSecurityError("runtime artifact record mode is not exact")
        if _read_json(path) != expected_record:
            raise Music3RuntimeSecurityError("runtime artifact record does not match")
    try:
        raw_lock_lines = [
            line.strip()
            for line in dependency_lock.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except (OSError, UnicodeError) as error:
        raise Music3RuntimeSecurityError("runtime dependency lock is unreadable") from error
    if len(raw_lock_lines) != len(set(raw_lock_lines)):
        raise Music3RuntimeSecurityError("runtime dependency lock contains duplicate pins")
    lock_lines = set(raw_lock_lines)
    if not REQUIRED_RUNTIME_LOCK_LINES.issubset(lock_lines):
        raise Music3RuntimeSecurityError("runtime dependency lock misses reviewed exact pins")
    if not lock_lines or any(_EXACT_REQUIREMENT.fullmatch(line) is None for line in lock_lines):
        raise Music3RuntimeSecurityError("every runtime dependency must be one exact pin")
    normalized_names = [
        re.sub(
            r"[-_.]+",
            "-",
            line.partition("==")[0].partition("[")[0].casefold(),
        )
        for line in raw_lock_lines
    ]
    if len(normalized_names) != len(set(normalized_names)):
        raise Music3RuntimeSecurityError("runtime dependency lock contains conflicting pins")
    expected_build_record = {
        "schema": "maestro.music3.ucx-build.v1",
        "ucx_version": PINNED_UCX_VERSION,
        "ucx_source_revision": PINNED_UCX_SOURCE_REVISION,
        "ucx_source_tarball_sha256": PINNED_UCX_TARBALL_SHA256,
        "ucx_source_tarball_size": PINNED_UCX_TARBALL_SIZE,
        "ucx_configure_flags": list(PINNED_UCX_CONFIGURE_FLAGS),
        "ucx_info_sha256": document["ucx_info_sha256"],
    }
    if _read_json(ucx_build_record) != expected_build_record:
        raise Music3RuntimeSecurityError("UCX build record is not exact")
    source = stage_root / "source"
    model = stage_root / "model"
    if (
        not (source / "pyproject.toml").is_file()
        or not (source / "sglang_omni").is_dir()
        or not (source / ".git" / "HEAD").is_file()
    ):
        raise Music3RuntimeSecurityError("SGLang-Omni source tree is incomplete")
    if (
        not (model / "config.json").is_file()
        or not (model / ".maestro-hf-revision").is_file()
    ):
        raise Music3RuntimeSecurityError("Music 3 model snapshot is incomplete")
    try:
        source_head = (source / ".git" / "HEAD").read_text(encoding="ascii").strip()
        model_revision = (model / ".maestro-hf-revision").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise Music3RuntimeSecurityError("source revision evidence is unreadable") from error
    if source_head != PINNED_SGLANG_SOURCE_REVISION.removeprefix("git:"):
        raise Music3RuntimeSecurityError("SGLang-Omni checkout is not at the reviewed commit")
    if model_revision != MUSIC3_HF_REVISION:
        raise Music3RuntimeSecurityError("Music 3 snapshot is not at the reviewed revision")
    if music3_tree_sha256(source, expected_device=expected_device) != document["runtime_source_tree_sha256"]:
        raise Music3RuntimeSecurityError("SGLang-Omni source tree digest does not match")
    if music3_tree_sha256(model, expected_device=expected_device) != document["model_snapshot_sha256"]:
        raise Music3RuntimeSecurityError("Music 3 model snapshot digest does not match")
    if music3_tree_sha256(stage_root / "env", expected_device=expected_device) != document["environment_tree_sha256"]:
        raise Music3RuntimeSecurityError("installed runtime environment digest does not match")
    probe_output = (ucx_probe or _probe_ucx)(ucx_info)
    if type(probe_output) is not bytes or len(probe_output) > MAX_JSON_BYTES:
        raise Music3RuntimeSecurityError("UCX probe evidence is invalid")
    probe_text = probe_output.decode("utf-8", errors="strict").casefold()
    if PINNED_UCX_VERSION not in probe_text or not {
        "cuda_copy",
        "cuda_ipc",
    }.issubset(set(re.findall(r"[a-z0-9_]+", probe_text))):
        raise Music3RuntimeSecurityError("UCX version or CUDA transports are not proven")
    if "sha256:" + hashlib.sha256(probe_output).hexdigest() != document["ucx_probe_sha256"]:
        raise Music3RuntimeSecurityError("UCX probe digest does not match")
    return document, actual_manifest_sha256


def music3_publication_token(
    plan: Music3ProvisionPlan,
    stage_root: str | os.PathLike[str],
    *,
    expected_stage_manifest_sha256: object,
) -> str:
    stage = Path(stage_root)
    with _generation_lock(plan.layout, stage, exclusive=False):
        filesystem_capability = _filesystem_capability_evidence(plan.layout)
        _document, stage_sha256 = _validate_stage(
            plan,
            stage,
            expected_stage_manifest_sha256=expected_stage_manifest_sha256,
        )
    return _mapping_sha256({
        "action": "publish",
        "plan_sha256": plan.sha256,
        "stage_manifest_sha256": stage_sha256,
        "filesystem_capability_sha256": _mapping_sha256(filesystem_capability),
        "stage": str(stage),
    })


def publish_music3_stage(
    plan: Music3ProvisionPlan,
    stage_root: str | os.PathLike[str],
    *,
    apply_token: object,
    expected_stage_manifest_sha256: object,
) -> dict[str, object]:
    """Atomically publish one exact stage while preserving one last-good."""

    stage = Path(stage_root)
    expected_token = music3_publication_token(
        plan,
        stage,
        expected_stage_manifest_sha256=expected_stage_manifest_sha256,
    )
    if type(apply_token) is not str or not _constant_time_equal(apply_token, expected_token):
        raise Music3RuntimeConflict("publication token is missing or stale")
    layout = plan.layout
    previous_marker: dict[str, object] | None = None
    rollback_required = False
    marker: dict[str, object] | None = None
    @contextmanager
    def publication_scope():
        with _lifecycle_lock(layout) as release_lifecycle_lock:
            try:
                with _generation_lock(
                    layout,
                    stage,
                    exclusive=True,
                ) as verify_generation_lock:
                    yield verify_generation_lock
                release_lifecycle_lock()
            except Exception as error:
                if rollback_required:
                    try:
                        if previous_marker is None:
                            layout.current_marker.unlink(missing_ok=True)
                        else:
                            _atomic_json(layout.current_marker, previous_marker)
                        _sync_directory(layout.state)
                    except Exception:  # noqa: BLE001 - preserve evidence
                        raise Music3RuntimeConflict(
                            "publication failed and rollback is incomplete"
                        ) from error
                    raise Music3RuntimeConflict(
                        "publication failed; last-good was restored"
                    ) from error
                raise

    with publication_scope() as verify_generation_lock:
        filesystem_capability = _filesystem_capability_evidence(layout)
        document, stage_sha256 = _validate_stage(
            plan,
            stage,
            expected_stage_manifest_sha256=expected_stage_manifest_sha256,
        )
        fresh_token = _mapping_sha256({
            "action": "publish",
            "plan_sha256": plan.sha256,
            "stage_manifest_sha256": stage_sha256,
            "filesystem_capability_sha256": _mapping_sha256(filesystem_capability),
            "stage": str(stage),
        })
        if not _constant_time_equal(apply_token, fresh_token):
            raise Music3RuntimeConflict("publication token became stale")
        if _path_present(layout.process_marker):
            raise Music3RuntimeConflict(
                "runtime ownership must be stopped and reconciled before publication"
            )
        if _path_present(layout.current_marker):
            existing_plan, previous_marker = _load_current_plan(layout.pinokio_root)
            if existing_plan.sha256 != plan.sha256:
                raise Music3RuntimeConflict("current runtime belongs to another plan")
            referenced_descriptors = [previous_marker["current"]]
            if previous_marker.get("previous") is not None:
                referenced_descriptors.append(previous_marker["previous"])
            if any(
                _generation_path(layout, descriptor) == stage
                for descriptor in referenced_descriptors
            ):
                raise Music3RuntimeConflict(
                    "generation is already current or retained as last-good"
                )
            verify_music3_runtime(layout.pinokio_root)
        if previous_marker is not None and previous_marker.get("previous") is not None:
            raise Music3RuntimeConflict(
                "last-good runtime must be reconciled before another publication"
            )
        current_descriptor = {
            "path": str(stage),
            "stage_manifest_sha256": stage_sha256,
            "generation_id": document["generation_id"],
        }
        previous_descriptor = (
            previous_marker.get("current") if previous_marker is not None else None
        )
        _sync_generation_tree(
            stage,
            expected_device=layout.pinokio_root.stat().st_dev,
        )
        synced_document, synced_stage_sha256 = _validate_stage(
            plan,
            stage,
            expected_stage_manifest_sha256=expected_stage_manifest_sha256,
        )
        if synced_document != document or synced_stage_sha256 != stage_sha256:
            raise Music3RuntimeSecurityError(
                "generation changed while its publication lock was held"
            )
        verify_generation_lock()
        marker = {
            "schema": RUNTIME_SCHEMA,
            "plan": plan.to_mapping(),
            "plan_sha256": plan.sha256,
            "filesystem_capability": filesystem_capability,
            "current": current_descriptor,
            "previous": previous_descriptor,
        }
        rollback_required = True
        _atomic_json(layout.current_marker, marker)
        verify_generation_lock()
    if marker is None:
        raise AssertionError("publication completed without a runtime marker")
    return marker


def _generation_path(
    layout: Music3RuntimeLayout,
    descriptor: object,
) -> Path:
    if type(descriptor) is not dict or set(descriptor) != {
        "path",
        "stage_manifest_sha256",
        "generation_id",
    }:
        raise Music3RuntimeSecurityError("runtime generation descriptor is invalid")
    path_value = descriptor.get("path")
    generation_id = descriptor.get("generation_id")
    if (
        type(path_value) is not str
        or type(generation_id) is not str
        or _GENERATION_ID.fullmatch(generation_id) is None
    ):
        raise Music3RuntimeSecurityError("runtime generation identity is invalid")
    path = Path(path_value)
    if path.parent != layout.generations or path.name != generation_id:
        raise Music3RuntimeSecurityError("runtime generation path is not final and exact")
    _assert_runtime_path(layout, path, allow_missing=False)
    return path


def _load_current_plan(
    pinokio_root: str | os.PathLike[str],
) -> tuple[Music3ProvisionPlan, dict[str, object]]:
    layout = resolve_music3_runtime_layout(pinokio_root)
    _assert_runtime_path(layout, layout.current_marker, allow_missing=False)
    marker = _read_json(layout.current_marker)
    if set(marker) != {
        "schema",
        "plan",
        "plan_sha256",
        "filesystem_capability",
        "current",
        "previous",
    } or marker.get("schema") != RUNTIME_SCHEMA:
        raise Music3RuntimeSecurityError("current runtime marker fields are not exact")
    raw_plan = marker.get("plan")
    if type(raw_plan) is not dict:
        raise Music3RuntimeSecurityError("current runtime plan is invalid")
    paths = raw_plan.get("paths")
    if type(paths) is not dict or paths.get("pinokio_root") != str(layout.pinokio_root):
        raise Music3RuntimeSecurityError("current runtime plan root does not match")
    plan = _build_plan(
        layout.pinokio_root,
        runtime_source_revision=raw_plan.get("runtime_source_revision"),
        ucx_version=raw_plan.get("ucx_version"),
        ucx_source_revision=raw_plan.get("ucx_source_revision"),
        check_free_space=False,
    )
    if raw_plan != plan.to_mapping() or marker.get("plan_sha256") != plan.sha256:
        raise Music3RuntimeSecurityError("current runtime plan digest does not match")
    filesystem_capability = marker.get("filesystem_capability")
    if type(filesystem_capability) is not dict:
        raise Music3RuntimeSecurityError("runtime filesystem capability marker is invalid")
    _generation_path(layout, marker.get("current"))
    if marker.get("previous") is not None:
        previous = _generation_path(layout, marker.get("previous"))
        if previous == _generation_path(layout, marker.get("current")):
            raise Music3RuntimeSecurityError("current and previous generations cannot match")
    return plan, marker


def verify_music3_runtime(
    pinokio_root: str | os.PathLike[str],
) -> dict[str, object]:
    plan, current = _load_current_plan(pinokio_root)
    if current["filesystem_capability"] != _filesystem_capability_evidence(plan.layout):
        raise Music3RuntimeSecurityError("runtime filesystem capability changed")
    current_descriptor = current["current"]
    active = _generation_path(plan.layout, current_descriptor)
    with _generation_lock(plan.layout, active, exclusive=False):
        document, stage_sha256 = _validate_stage(
            plan,
            active,
            expected_stage_manifest_sha256=current_descriptor.get(
                "stage_manifest_sha256"
            ),
        )
    if (
        current_descriptor.get("stage_manifest_sha256") != stage_sha256
        or current_descriptor.get("generation_id") != document.get("generation_id")
    ):
        raise Music3RuntimeSecurityError("active runtime no longer matches its marker")
    previous_generation_id = None
    if current["previous"] is not None:
        previous_descriptor = current["previous"]
        previous_path = _generation_path(plan.layout, previous_descriptor)
        with _generation_lock(plan.layout, previous_path, exclusive=False):
            previous_document, previous_sha256 = _validate_stage(
                plan,
                previous_path,
                expected_stage_manifest_sha256=previous_descriptor.get(
                    "stage_manifest_sha256"
                ),
            )
        if (
            previous_descriptor.get("stage_manifest_sha256") != previous_sha256
            or previous_descriptor.get("generation_id")
            != previous_document.get("generation_id")
        ):
            raise Music3RuntimeSecurityError("last-good runtime no longer matches its marker")
        previous_generation_id = previous_document["generation_id"]
    return {
        "verified": True,
        "generation_id": document["generation_id"],
        "plan_sha256": plan.sha256,
        "stage_manifest_sha256": stage_sha256,
        "previous_generation_id": previous_generation_id,
        "runtime_source_revision": plan.runtime_source_revision,
        "model_revision": PINNED_MODEL_REVISION,
        "ucx_version": plan.ucx_version,
        "ucx_source_revision": plan.ucx_source_revision,
        "python_runtime": document["python_runtime"],
        "cuda_runtime": document["cuda_runtime"],
        "effective_paths": {
            "generation": str(active),
            "environment": str(active / "env"),
            "model": str(active / "model"),
            "source": str(active / "source"),
            "home": str(plan.layout.home),
            "hf_home": str(plan.layout.cache / "huggingface"),
            "hf_hub_cache": str(plan.layout.cache / "huggingface" / "hub"),
            "uv_cache": str(plan.layout.cache / "uv"),
            "torch_home": str(plan.layout.cache / "torch"),
            "xdg_cache": str(plan.layout.cache / "xdg"),
            "temporary": str(plan.layout.temporary),
        },
    }


def _port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise Music3RuntimeError("runtime port must be an explicit dynamic port")
    return value


def build_music3_start_command(
    pinokio_root: str | os.PathLike[str],
    *,
    port: object,
) -> tuple[str, ...]:
    plan, current = _load_current_plan(pinokio_root)
    verify_music3_runtime(pinokio_root)
    active = _generation_path(plan.layout, current["current"])
    selected_port = _port(port)
    return (
        str(active / "env" / "bin" / "sgl-omni"),
        "serve",
        "--model-path",
        str(active / "model"),
        "--host",
        "127.0.0.1",
        "--port",
        str(selected_port),
    )


def music3_runtime_environment(
    pinokio_root: str | os.PathLike[str],
) -> dict[str, str]:
    plan, current = _load_current_plan(pinokio_root)
    layout = plan.layout
    active = _generation_path(layout, current["current"])
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "CUDA_DEVICE_ORDER",
            "CUDA_VISIBLE_DEVICES",
            "NVIDIA_VISIBLE_DEVICES",
        }
    }
    environment.update({
        "PATH": f"{active / 'env' / 'bin'}:/usr/bin:/bin",
        "LD_LIBRARY_PATH": str(active / "env" / "lib"),
        "HOME": str(layout.home),
        "HF_HOME": str(layout.cache / "huggingface"),
        "HF_HUB_CACHE": str(layout.cache / "huggingface" / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(layout.cache / "huggingface" / "hub"),
        "TRANSFORMERS_CACHE": str(layout.cache / "huggingface" / "transformers"),
        "UV_CACHE_DIR": str(layout.cache / "uv"),
        "TORCH_HOME": str(layout.cache / "torch"),
        "XDG_CACHE_HOME": str(layout.cache / "xdg"),
        "TMPDIR": str(layout.temporary),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(layout.cache / "pycache"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "NO_PROXY": "127.0.0.1",
        "no_proxy": "127.0.0.1",
        "NCCL_SOCKET_IFNAME": "lo",
    })
    for path in (
        layout.home,
        layout.cache / "huggingface" / "hub",
        layout.cache / "huggingface" / "transformers",
        layout.cache / "uv",
        layout.cache / "torch",
        layout.cache / "xdg",
        layout.cache / "pycache",
        layout.temporary,
    ):
        if path != layout.root and layout.root not in path.parents:
            raise Music3RuntimeSecurityError("effective cache path escaped runtime root")
    return environment


def _command_sha256(command: Sequence[str]) -> str:
    return _mapping_sha256({"argv": list(command)})


def _proc_identity(pid: int) -> tuple[int, int, str]:
    if os.name != "posix" or not Path("/proc").is_dir():
        raise Music3RuntimeSecurityError("owned runtime processes require Linux procfs")
    if type(pid) is not int or pid <= 1:
        raise Music3RuntimeSecurityError("runtime PID is invalid")
    process_root = Path("/proc") / str(pid)
    info = process_root.stat()
    if info.st_uid != os.geteuid():
        raise Music3RuntimeSecurityError("runtime process has a foreign owner")
    raw_stat = (process_root / "stat").read_text(encoding="ascii")
    close = raw_stat.rfind(")")
    fields = raw_stat[close + 2 :].split()
    if close < 1 or len(fields) < 20:
        raise Music3RuntimeSecurityError("runtime process identity is malformed")
    process_group = int(fields[2])
    started_at_ticks = int(fields[19])
    command_bytes = (process_root / "cmdline").read_bytes()
    if not command_bytes or len(command_bytes) > MAX_JSON_BYTES:
        raise Music3RuntimeSecurityError("runtime process command is unavailable")
    command = [item.decode("utf-8", errors="strict") for item in command_bytes.split(b"\0") if item]
    return process_group, started_at_ticks, _command_sha256(command)


def _prepare_runtime_directories(layout: Music3RuntimeLayout) -> None:
    for path in (
        layout.state,
        layout.home,
        layout.cache / "huggingface" / "hub",
        layout.cache / "huggingface" / "transformers",
        layout.cache / "uv",
        layout.cache / "torch",
        layout.cache / "xdg",
        layout.cache / "pycache",
        layout.temporary,
    ):
        _ensure_private_runtime_directory(layout, path)


def start_music3_runtime(
    pinokio_root: str | os.PathLike[str],
    *,
    port: object,
    popen_factory: Callable[..., _Process] = subprocess.Popen,
    identity_reader: Callable[[int], tuple[int, int, str]] = _proc_identity,
) -> tuple[_Process, dict[str, object]]:
    """Start one isolated process group and durably bind its exact identity."""

    if os.name != "posix":
        raise Music3RuntimeError("Music 3 runtime is currently Linux-only")
    plan, current_marker = _load_current_plan(pinokio_root)
    with _lifecycle_lock(plan.layout):
        if _path_present(plan.layout.process_marker):
            raise Music3RuntimeConflict("runtime process ownership evidence requires review")
        selected_port = _port(port)
        command = build_music3_start_command(pinokio_root, port=selected_port)
        environment = music3_runtime_environment(pinokio_root)
        _prepare_runtime_directories(plan.layout)
        active = _generation_path(plan.layout, current_marker["current"])
        supervisor_python = active / "env" / "bin" / "python"
        _existing_path_is_safe(supervisor_python, directory=False)
        if not os.access(supervisor_python, os.X_OK):
            raise Music3RuntimeSecurityError("runtime supervisor interpreter is not executable")
        supervisor_command = (
            str(supervisor_python),
            "-I",
            "-S",
            "-c",
            _SUPERVISOR_CODE,
            *command,
        )
        process = popen_factory(
            list(supervisor_command),
            cwd=str(active / "source"),
            env=environment,
            stdin=subprocess.PIPE,
            bufsize=0,
            shell=False,
            start_new_session=True,
        )
        provisional_marker: dict[str, object] | None = None
        try:
            pid = int(process.pid)
            process_group, started_at_ticks, observed_command_sha256 = identity_reader(pid)
            launch_command_sha256 = _command_sha256(supervisor_command)
            if process_group != pid or observed_command_sha256 != launch_command_sha256:
                raise Music3RuntimeSecurityError("spawned process group identity does not match")
            provisional_marker = {
                "schema": PROCESS_SCHEMA,
                "pid": pid,
                "process_group_id": process_group,
                "started_at_ticks": started_at_ticks,
                "launch_command_sha256": launch_command_sha256,
                "observed_command_sha256": observed_command_sha256,
                "plan_sha256": plan.sha256,
                "generation_id": current_marker["current"]["generation_id"],
                "base_url": f"http://127.0.0.1:{selected_port}",
            }
            current = verify_music3_runtime(pinokio_root)
            if current["generation_id"] != provisional_marker["generation_id"]:
                raise Music3RuntimeConflict("runtime generation changed during startup")
            marker = provisional_marker
            _atomic_json(plan.layout.process_marker, marker)
            input_stream = getattr(process, "stdin", None)
            if input_stream is None:
                raise Music3RuntimeSecurityError("runtime supervisor control pipe is unavailable")
            if input_stream.write(b"go\n") != 3:
                raise Music3RuntimeSecurityError("runtime supervisor release was incomplete")
            return process, marker
        except Exception:
            # Until the exact `go` token is written, the supervisor cannot create
            # descendants. Every failure in this block is therefore recoverable by
            # killing the exact Popen leader even when identity acquisition failed.
            try:
                process.kill()
                process.wait(timeout=10.0)
            except Exception as cleanup_error:  # noqa: BLE001 - best-effort gated cleanup
                _ = cleanup_error
            if _path_present(plan.layout.process_marker):
                try:
                    written = _read_json(plan.layout.process_marker)
                    if provisional_marker is not None and written == provisional_marker:
                        plan.layout.process_marker.unlink()
                        _sync_directory(plan.layout.state)
                except Music3RuntimeError:
                    pass
            raise


def _validate_process_marker(
    plan: Music3ProvisionPlan,
    marker: Mapping[str, object],
    *,
    identity_reader: Callable[[int], tuple[int, int, str]],
    expected_generation_id: object,
) -> bool:
    if set(marker) != {
        "schema",
        "pid",
        "process_group_id",
        "started_at_ticks",
        "launch_command_sha256",
        "observed_command_sha256",
        "plan_sha256",
        "generation_id",
        "base_url",
    } or marker.get("schema") != PROCESS_SCHEMA:
        raise Music3RuntimeSecurityError("process ownership marker fields are not exact")
    pid = marker.get("pid")
    if type(pid) is not int or pid <= 1 or marker.get("process_group_id") != pid:
        raise Music3RuntimeSecurityError("process ownership marker identity is invalid")
    if marker.get("plan_sha256") != plan.sha256:
        raise Music3RuntimeSecurityError("process marker is bound to another runtime plan")
    if marker.get("launch_command_sha256") != marker.get("observed_command_sha256"):
        raise Music3RuntimeSecurityError("process marker command binding is inconsistent")
    if marker.get("generation_id") != expected_generation_id:
        raise Music3RuntimeSecurityError("process marker is bound to another runtime generation")
    try:
        process_group, started_at_ticks, command_sha256 = identity_reader(pid)
    except (FileNotFoundError, ProcessLookupError):
        return False
    if (
        process_group != pid
        or marker.get("started_at_ticks") != started_at_ticks
        or marker.get("observed_command_sha256") != command_sha256
    ):
        raise Music3RuntimeSecurityError("live process does not match ownership evidence")
    return True


def music3_runtime_status(
    pinokio_root: str | os.PathLike[str],
    *,
    identity_reader: Callable[[int], tuple[int, int, str]] = _proc_identity,
) -> dict[str, object]:
    plan, current = _load_current_plan(pinokio_root)
    verified = verify_music3_runtime(pinokio_root)
    if not _path_present(plan.layout.process_marker):
        return {**verified, "state": "stopped", "base_url": None}
    _assert_runtime_path(plan.layout, plan.layout.process_marker, allow_missing=False)
    marker = _read_json(plan.layout.process_marker)
    running = _validate_process_marker(
        plan,
        marker,
        identity_reader=identity_reader,
        expected_generation_id=current["current"]["generation_id"],
    )
    return {
        **verified,
        "state": "running" if running else "stale",
        "base_url": marker.get("base_url") if running else None,
        "process_marker_sha256": _mapping_sha256(marker),
        "current_marker_sha256": _mapping_sha256(current),
    }


def _signal_proven_owned_group(
    marker: Mapping[str, object],
    signal_number: int,
    *,
    identity_reader: Callable[[int], tuple[int, int, str]],
) -> None:
    """Pin the root with pidfd/SIGSTOP before signalling its process group."""

    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise Music3RuntimeSecurityError("safe process-group signalling is unavailable")
    pid = int(marker["pid"])
    descriptor = os.pidfd_open(pid, 0)
    stopped = False
    try:
        process_group, started_at_ticks, command_sha256 = identity_reader(pid)
        if (
            process_group != pid
            or marker.get("started_at_ticks") != started_at_ticks
            or marker.get("observed_command_sha256") != command_sha256
        ):
            raise Music3RuntimeSecurityError("process identity changed before signalling")
        signal.pidfd_send_signal(descriptor, signal.SIGSTOP)
        stopped = True
        process_group, started_at_ticks, command_sha256 = identity_reader(pid)
        if (
            process_group != pid
            or marker.get("started_at_ticks") != started_at_ticks
            or marker.get("observed_command_sha256") != command_sha256
        ):
            raise Music3RuntimeSecurityError("process identity changed while pinned")
        os.killpg(pid, signal_number)
    finally:
        if stopped and signal_number != signal.SIGKILL:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGCONT)
            except OSError:
                pass
        os.close(descriptor)


def stop_owned_music3_runtime(
    pinokio_root: str | os.PathLike[str],
    *,
    identity_reader: Callable[[int], tuple[int, int, str]] = _proc_identity,
    owned_group_signaler: Callable[..., None] = _signal_proven_owned_group,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    grace_seconds: float = 10.0,
) -> dict[str, object]:
    """Unload only the process group proven by the exact ownership marker."""

    plan, current = _load_current_plan(pinokio_root)
    with _lifecycle_lock(plan.layout):
        _assert_runtime_path(plan.layout, plan.layout.process_marker, allow_missing=False)
        marker = _read_json(plan.layout.process_marker)

        def live_now() -> bool:
            return _validate_process_marker(
                plan,
                marker,
                identity_reader=identity_reader,
                expected_generation_id=current["current"]["generation_id"],
            )

        if not live_now():
            raise Music3RuntimeConflict("owned runtime process is no longer live")
        owned_group_signaler(marker, signal.SIGTERM, identity_reader=identity_reader)
        deadline = monotonic() + max(0.1, min(float(grace_seconds), 60.0))
        while monotonic() < deadline:
            if not live_now():
                break
            sleeper(min(0.05, max(0.0, deadline - monotonic())))
        else:
            if live_now():
                owned_group_signaler(marker, signal.SIGKILL, identity_reader=identity_reader)
                final_deadline = monotonic() + max(0.1, min(float(grace_seconds), 60.0))
                while monotonic() < final_deadline and live_now():
                    sleeper(0.05)
        if live_now():
            raise Music3RuntimeConflict("owned runtime process did not stop")
        plan.layout.process_marker.unlink()
        _sync_directory(plan.layout.state)
        return {"stopped": True, "generation_id": marker["generation_id"]}


def retire_stopped_music3_process_marker(
    pinokio_root: str | os.PathLike[str],
    *,
    identity_reader: Callable[[int], tuple[int, int, str]] = _proc_identity,
) -> bool:
    plan, current = _load_current_plan(pinokio_root)
    with _lifecycle_lock(plan.layout):
        if not _path_present(plan.layout.process_marker):
            return False
        _assert_runtime_path(plan.layout, plan.layout.process_marker, allow_missing=False)
        marker = _read_json(plan.layout.process_marker)
        if _validate_process_marker(
            plan,
            marker,
            identity_reader=identity_reader,
            expected_generation_id=current["current"]["generation_id"],
        ):
            raise Music3RuntimeConflict("live process ownership evidence cannot be retired")
        plan.layout.process_marker.unlink()
        _sync_directory(plan.layout.state)
        return True


def build_music3_reset_plan(
    pinokio_root: str | os.PathLike[str],
) -> dict[str, object]:
    plan, current = _load_current_plan(pinokio_root)
    status = music3_runtime_status(pinokio_root)
    if status["state"] != "stopped":
        raise Music3RuntimeConflict("runtime must be cleanly stopped before reset")
    descriptors = [current["current"]]
    if current["previous"] is not None:
        descriptors.append(current["previous"])
    targets = []
    for descriptor in descriptors:
        generation = _generation_path(plan.layout, descriptor)
        targets.append({
            "source": str(generation),
            "destination": f"generations/{generation.name}",
        })
    if _path_present(plan.layout.staging):
        _assert_runtime_path(plan.layout, plan.layout.staging, allow_missing=False)
        targets.append({
            "source": str(plan.layout.staging),
            "destination": "staging",
        })
    reset = {
        "schema": RESET_SCHEMA,
        "plan_sha256": plan.sha256,
        "current_marker_sha256": _mapping_sha256(current),
        "targets": targets,
        "action": "move-to-reset-quarantine",
    }
    return {**reset, "confirmation_token": _mapping_sha256(reset)}


def apply_music3_reset_plan(
    pinokio_root: str | os.PathLike[str],
    *,
    confirmation_token: object,
) -> dict[str, object]:
    layout = resolve_music3_runtime_layout(pinokio_root)
    with _lifecycle_lock(layout):
        reset = build_music3_reset_plan(pinokio_root)
        expected = reset["confirmation_token"]
        if (
            type(confirmation_token) is not str
            or type(expected) is not str
            or not _constant_time_equal(confirmation_token, expected)
        ):
            raise Music3RuntimeConflict("reset confirmation token is missing or stale")
        suffix = expected.removeprefix("sha256:")[:16]
        quarantine = layout.reset_quarantine / suffix
        _assert_runtime_path(layout, quarantine, allow_missing=True)
        if quarantine.exists():
            raise Music3RuntimeConflict("reset quarantine destination already exists")
        _ensure_private_runtime_directory(layout, quarantine)
        moved: list[str] = []
        try:
            for raw_target in reset["targets"]:
                if type(raw_target) is not dict or set(raw_target) != {
                    "source",
                    "destination",
                }:
                    raise Music3RuntimeSecurityError("reset target is invalid")
                target = Path(raw_target["source"])
                relative_destination = Path(raw_target["destination"])
                if (
                    relative_destination.is_absolute()
                    or ".." in relative_destination.parts
                    or len(relative_destination.parts) not in {1, 2}
                ):
                    raise Music3RuntimeSecurityError("reset destination is invalid")
                expected_destination = (
                    Path("generations") / target.name
                    if target.parent == layout.generations
                    else Path("staging")
                    if target == layout.staging
                    else None
                )
                if relative_destination != expected_destination:
                    raise Music3RuntimeSecurityError("reset target namespace is invalid")
                _safe_tree(
                    target,
                    expected_device=layout.pinokio_root.stat().st_dev,
                )
                destination = quarantine / relative_destination
                _ensure_private_runtime_directory(layout, destination.parent)
                if _path_present(destination):
                    raise Music3RuntimeConflict("reset destination already exists")
                os.replace(target, destination)
                _sync_directory(destination.parent)
                moved.append(str(destination))
            for marker in (layout.current_marker, layout.process_marker):
                if _path_present(marker):
                    _assert_runtime_path(layout, marker, allow_missing=False)
                    _existing_path_is_safe(marker, directory=False)
                    marker.unlink()
            _sync_directory(layout.generations)
            _sync_directory(layout.state)
            _sync_directory(quarantine)
            _sync_directory(layout.reset_quarantine)
            _sync_directory(layout.root)
        except Exception as error:
            raise Music3RuntimeConflict("reset stopped with recoverable quarantine evidence") from error
        return {
            "reset": True,
            "quarantine": str(quarantine),
            "moved": moved,
            "confirmation_token": expected,
        }
