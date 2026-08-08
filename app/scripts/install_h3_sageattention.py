#!/usr/bin/env python3
"""Build pinned official SageAttention2++ only inside its proven envelope."""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse


REVISION = "eb615cf6cf4d221338033340ee2de1c37fbdba4a"
VERSION = "2.2.0"
APP_ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = APP_ROOT / "services" / "sageattention_thu_ml"
MARKER = Path(sys.prefix) / ".maestro_h3_sage2.json"
CUDA_TOOLKIT_VERSION = "12.8.1"
CUDA_TOOLKIT = APP_ROOT / "tools" / f"cuda-{CUDA_TOOLKIT_VERSION}"
CUDA_CHANNEL = f"nvidia/label/cuda-{CUDA_TOOLKIT_VERSION}"
CUDA_DEPENDENCY_CHANNEL = "conda-forge"


def _version(value: object) -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _git_revision() -> str | None:
    if not (CHECKOUT / ".git").is_dir():
        return None
    try:
        return subprocess.check_output(
            ["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_source_clean() -> bool:
    try:
        status = subprocess.run(
            ["git", "-C", str(CHECKOUT), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return status.returncode == 0 and not status.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False


def _distribution_source(distribution=None) -> Path | None:
    try:
        distribution = distribution or importlib.metadata.distribution("sageattention")
        direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
        parsed = urlparse(str(direct_url.get("url") or ""))
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            return None
        return Path(unquote(parsed.path)).resolve()
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return None


def _distribution_digest(distribution=None) -> str | None:
    try:
        distribution = distribution or importlib.metadata.distribution("sageattention")
        files = sorted(distribution.files or (), key=lambda item: str(item))
        prefix = Path(sys.prefix).resolve()
        digest = hashlib.sha256()
        for relative in files:
            path = Path(distribution.locate_file(relative)).resolve()
            if not path.is_file() or not path.is_relative_to(prefix):
                return None
            digest.update(str(relative).encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest() if files else None
    except (OSError, importlib.metadata.PackageNotFoundError):
        return None


def _nvcc_version(cuda_home: Path) -> tuple[int, int]:
    try:
        output = subprocess.check_output(
            [str(cuda_home / "bin" / "nvcc"), "--version"],
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return (0, 0)
    release = re.search(r"release\s+(\d+)\.(\d+)", output)
    return (int(release.group(1)), int(release.group(2))) if release else (0, 0)


def _conda_executable() -> str | None:
    discovered = shutil.which("conda")
    if discovered:
        return discovered
    pinokio_home = APP_ROOT.parents[2]
    for candidate in (
        pinokio_home / "bin" / "miniconda" / "bin" / "conda",
        pinokio_home / "bin" / "miniforge" / "bin" / "conda",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _ensure_cuda_toolkit() -> Path | None:
    if _nvcc_version(CUDA_TOOLKIT) >= (12, 8):
        return CUDA_TOOLKIT
    conda = _conda_executable()
    if conda is None:
        print("[H3 Sage2] skipped: Pinokio Conda is unavailable for the pinned CUDA 12.8 toolkit")
        return None
    CUDA_TOOLKIT.parent.mkdir(parents=True, exist_ok=True)
    command = [
        conda, "create", "--yes", "--prefix", str(CUDA_TOOLKIT),
        "--override-channels", "--channel", CUDA_CHANNEL,
        "--channel", CUDA_DEPENDENCY_CHANNEL,
        f"cuda-toolkit={CUDA_TOOLKIT_VERSION}",
    ]
    print(f"[H3 Sage2] provisioning NVIDIA CUDA Toolkit {CUDA_TOOLKIT_VERSION} in an isolated app prefix")
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"[H3 Sage2] optional CUDA toolkit provisioning failed ({error})")
        return None
    if _nvcc_version(CUDA_TOOLKIT) < (12, 8):
        print("[H3 Sage2] pinned CUDA toolkit did not provide nvcc 12.8+")
        return None
    return CUDA_TOOLKIT


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version("sageattention")
    except importlib.metadata.PackageNotFoundError:
        return None


def _read_marker() -> dict[str, object]:
    try:
        value = json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_marker(value: dict[str, object]) -> None:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sage2-", suffix=".json", dir=MARKER.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, MARKER)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    if platform.system() != "Linux":
        print("[H3 Sage2] skipped: the pinned source build is Linux-only; dense SDPA remains available")
        return 0
    try:
        import torch
        from torch.utils.cpp_extension import CUDA_HOME
    except Exception as error:
        print(f"[H3 Sage2] skipped: PyTorch/CUDA build support is unavailable ({error})")
        return 0
    if not torch.cuda.is_available():
        print("[H3 Sage2] skipped: CUDA is unavailable; dense SDPA remains available")
        return 0
    capability = tuple(torch.cuda.get_device_capability(0))
    if capability != (12, 0):
        print(f"[H3 Sage2] skipped: detected SM{capability[0]}{capability[1]}, currently gated to SM120")
        return 0
    if _version(torch.version.cuda) < (12, 8):
        print(f"[H3 Sage2] skipped: PyTorch CUDA {torch.version.cuda} is below 12.8")
        return 0
    revision = _git_revision()
    if revision != REVISION:
        print(f"[H3 Sage2] skipped: pinned official checkout revision is missing or mismatched ({revision})")
        return 0
    if not _git_source_clean():
        print("[H3 Sage2] skipped: official SageAttention checkout has local source changes")
        return 0
    cuda_home = Path(CUDA_HOME) if CUDA_HOME is not None else Path("/")
    if _nvcc_version(cuda_home) < (12, 8):
        provisioned = _ensure_cuda_toolkit()
        if provisioned is None:
            print("[H3 Sage2] skipped: an nvcc CUDA 12.8+ toolkit is required to build official v2.2.0")
            return 0
        cuda_home = provisioned
    expected = {
        "revision": REVISION,
        "version": VERSION,
        "torch": str(torch.__version__),
        "torch_cuda": str(torch.version.cuda),
        "compute_capability": [12, 0],
    }
    marker = _read_marker()
    if (
        all(marker.get(key) == value for key, value in expected.items())
        and _installed_version() == VERSION
        and _distribution_source() == CHECKOUT.resolve()
        and marker.get("distribution_sha256") == _distribution_digest()
    ):
        print("[H3 Sage2] pinned official v2.2.0 SM120 build already verified")
        return 0

    environment = os.environ.copy()
    environment.update({
        "CUDA_HOME": str(cuda_home),
        "TORCH_CUDA_ARCH_LIST": "12.0",
        "EXT_PARALLEL": environment.get("EXT_PARALLEL", "4"),
        "MAX_JOBS": environment.get("MAX_JOBS", str(min(16, os.cpu_count() or 4))),
    })
    environment["PATH"] = str(cuda_home / "bin") + os.pathsep + environment.get("PATH", "")
    environment["LD_LIBRARY_PATH"] = (
        str(cuda_home / "lib") + os.pathsep + environment.get("LD_LIBRARY_PATH", "")
    )
    command = [
        sys.executable, "-m", "pip", "install", "--no-build-isolation",
        "--no-deps", "--force-reinstall", ".",
    ]
    print(f"[H3 Sage2] building official SageAttention v{VERSION} from pinned source {REVISION[:12]}")
    try:
        subprocess.run(command, cwd=CHECKOUT, env=environment, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        MARKER.unlink(missing_ok=True)
        print(f"[H3 Sage2] optional build failed ({error}); dense SDPA remains available")
        return 0
    if _installed_version() != VERSION:
        MARKER.unlink(missing_ok=True)
        print("[H3 Sage2] build did not install the expected package version; dense SDPA remains available")
        return 0
    distribution_digest = _distribution_digest()
    if _distribution_source() != CHECKOUT.resolve() or distribution_digest is None:
        MARKER.unlink(missing_ok=True)
        print("[H3 Sage2] installed package provenance could not be verified; dense SDPA remains available")
        return 0
    _write_marker({**expected, "distribution_sha256": distribution_digest})
    print("[H3 Sage2] pinned official v2.2.0 SM120 source build verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
