#!/usr/bin/env python3
"""Build and transactionally install Maestro's pinned CUDA 13 LightX2V kernel.

This helper performs an ABI/package check only.  It never probes a CUDA device
or runs a kernel; the launcher owns any later coordinated GPU acceptance.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
import zipfile


VERSION = "0.0.2+torch2.10.0.cu130.maestro1"
KERNELS_REPOSITORY = "https://github.com/deepbeepmeep/kernels.git"
KERNELS_REVISION = "2808bfb073bd91e4fe3ef83712f600b8d642579b"
CUTLASS_REPOSITORY = "https://github.com/NVIDIA/cutlass.git"
CUTLASS_REVISION = "dcf215af68a2d08d305076c152a06f201728cd53"
CUDA_CHANNEL = "nvidia/label/cuda-13.0.2"
DEPENDENCY_CHANNEL = "conda-forge"
TOOLCHAIN_PACKAGES = (
    f"{CUDA_CHANNEL}::cuda-nvcc=13.0.88",
    f"{CUDA_CHANNEL}::cuda-nvcc_linux-64=13.0.88",
    f"{CUDA_CHANNEL}::cuda-cccl_linux-64=13.0.85",
    f"{DEPENDENCY_CHANNEL}::gcc_linux-64=14",
    f"{DEPENDENCY_CHANNEL}::gxx_linux-64=14",
    f"{DEPENDENCY_CHANNEL}::ninja=1.13.1",
)
REQUIRED_TOOLCHAIN_PACKAGES = {
    "cuda-nvcc": "13.0.88",
    "cuda-nvcc_linux-64": "13.0.88",
    "cuda-cccl_linux-64": "13.0.85",
    "gcc_linux-64": None,
    "gxx_linux-64": None,
    "sysroot_linux-64": None,
    "ninja": "1.13.1",
}
REQUIRED_COMPILER_MAJOR = 14

APP_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = APP_ROOT / ".lightx2v-runtime"
RECEIPT_NAME = ".maestro_lightx2v_runtime.json"
RUNTIME_LOCK_NAME = ".maestro_lightx2v_runtime.lock"
STATE_LOCK_NAME = "installer.lock"
RECEIPT_SCHEMA = 1
PATCHED_SOURCE_TREE_SHA256 = "29ea09b2ba5b7a89081e0e1c16af4dd33f75a046de53203898c645401e1fd4c0"
CUTLASS_TREE_SHA256 = "02267e9631e0f0b00bba7bd95c5f7241f61d7f0b2c5a741269ea08ee33988eee"

TRANSLATION_UNITS = (
    "csrc/gemm/mxfp4_quant_kernels_sm120.cu",
    "csrc/gemm/mxfp4_scaled_mm_kernels_sm120.cu",
    "csrc/gemm/mxfp6_mxfp8_scaled_mm_kernels_sm120.cu",
    "csrc/gemm/mxfp6_quant_kernels_sm120.cu",
    "csrc/gemm/mxfp8_quant_kernels_sm120.cu",
    "csrc/gemm/mxfp8_scaled_mm_kernels_sm120.cu",
    "csrc/gemm/nvfp4_quant_kernels_sm120.cu",
    "csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu",
    "csrc/common_extension.cc",
)
PACKAGE_SOURCES = (
    "python/lightx2v_kernel/__init__.py",
    "python/lightx2v_kernel/gemm.py",
    "python/lightx2v_kernel/utils.py",
    "python/lightx2v_kernel/version.py",
)
EXPECTED_OPERATORS = (
    "cutlass_scaled_nvfp4_mm_sm120",
    "scaled_nvfp4_quant_sm120",
    "scaled_mxfp4_quant_sm120",
    "scaled_mxfp8_quant_sm120",
    "scaled_mxfp6_quant_sm120",
    "cutlass_scaled_mxfp4_mm_sm120",
    "cutlass_scaled_mxfp6_mxfp8_mm_sm120",
    "cutlass_scaled_mxfp8_mm_sm120",
)
CUDA_FLAGS = (
    "-U__CUDA_NO_HALF_OPERATORS__",
    "-U__CUDA_NO_HALF_CONVERSIONS__",
    "-U__CUDA_NO_BFLOAT16_CONVERSIONS__",
    "-U__CUDA_NO_HALF2_OPERATORS__",
    "-O3",
    "-DNDEBUG",
    "-DCUTE_USE_PACKED_TUPLE=1",
    "-DCUTLASS_ENABLE_TENSOR_CORE_MMA=1",
    "-DCUTLASS_VERSIONS_GENERATED",
    "-DCUTLASS_TEST_LEVEL=0",
    "-DCUTLASS_TEST_ENABLE_CACHED_RESULTS=1",
    "-DCUTLASS_DEBUG_TRACE_LEVEL=0",
    "--expt-relaxed-constexpr",
    "--expt-extended-lambda",
    "--threads=2",
    "-gencode=arch=compute_120,code=sm_120",
    "-gencode=arch=compute_120a,code=sm_120a",
)
NINJA_RUNPATH_FLAG = (
    "-Wl,-rpath,'$$ORIGIN/../torch/lib:$$ORIGIN/../nvidia/cu13/lib'"
)

# Exact upstream bytes that Maestro intentionally changes before compiling.
PATCH_BASE_SHA256 = {
    "csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu":
        "519c841291e05df9be15699b9ff24acba74eb75366be7cc9b336122dd3dae2b2",
    "python/lightx2v_kernel/__init__.py":
        "a40883dc1a82d97543a8a8196ac2ace7e1b89ad1e771ae2c2cf7fcfd4ba82d80",
    "python/lightx2v_kernel/version.py":
        "b172e1ee0dca0b84021717191814e91b6b1c47b866981b0c8eae8ba91a6d9118",
}
UPSTREAM_CUBLAS_ERROR = (
    'throw std::runtime_error(std::string("cuBLAS error: ") + '
    "std::to_string(status));"
)
MAESTRO_CUBLAS_ERROR = (
    'throw std::runtime_error(std::string("cuBLAS call ") + #call + " at line " + '
    'std::to_string(__LINE__) + " returned status " + std::to_string(status));'
)
MAESTRO_INIT = (
    "from lightx2v_kernel import common_ops  # noqa: F401\n"
    "from lightx2v_kernel.version import __version__\n\n"
    "build_tree_kernel = None\n"
)
MAESTRO_VERSION = f"__version__ = '{VERSION}'\n"

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"Expected an ordinary directory: {root}")
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        directory = Path(current)
        for name in [*directories, *names]:
            path = directory / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise RuntimeError(f"Source/package tree contains an unsafe entry: {path}")
        files.extend(directory / name for name in names)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _tree_manifest(root: Path, *, ignore_cache: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in _regular_files(root):
        relative = path.relative_to(root)
        if ignore_cache and ("__pycache__" in relative.parts or path.suffix == ".pyc"):
            continue
        result[relative.as_posix()] = _sha256(path)
    return result


def _manifest_digest(manifest: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(manifest.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _run(
    command: Sequence[str], *, cwd: Path | None = None,
    env: dict[str, str] | None = None, timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    argv = list(command)
    process = subprocess.Popen(argv, cwd=cwd, env=env, text=True, start_new_session=True)
    try:
        return_code = process.wait(timeout=timeout)
    except BaseException:
        _terminate_process_group(process)
        raise
    completed = subprocess.CompletedProcess(argv, return_code)
    if return_code:
        _terminate_process_group(process)
        raise subprocess.CalledProcessError(return_code, argv)
    return completed


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Stop the full compiler/Conda process tree after timeout or interruption."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The direct Conda/Ninja process can exit before its compiler descendants.
    # Signal the group even when wait() says the group leader is already done.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait()


@contextmanager
def _exclusive_lock(path: Path, *, timeout: float = 300.0) -> Iterator[None]:
    """Bound concurrent toolchain work and mutation of a selected runtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise RuntimeError(f"LightX2V lock path is unsafe: {path}")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for another LightX2V installer: {path}")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _output(command: Sequence[str], *, cwd: Path, timeout: int = 60) -> str:
    return subprocess.check_output(list(command), cwd=cwd, text=True, timeout=timeout).strip()


def _resolve_tool(name: str) -> str:
    value = shutil.which(name)
    if value is None:
        raise RuntimeError(
            f"LightX2V source installation requires Pinokio's {name} command on PATH. "
            "Run Update from Pinokio after its bundled tools are available."
        )
    return value


def _conda_executable() -> str:
    discovered = shutil.which("conda")
    if discovered:
        return discovered
    pinokio_home = APP_ROOT.parents[2]
    for candidate in (
        pinokio_home / "bin/miniforge/bin/conda",
        pinokio_home / "bin/miniconda/bin/conda",
        pinokio_home / "bin/miniforge/condabin/conda",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError(
        "LightX2V source installation requires Pinokio Conda on PATH or in its bundled tool directory."
    )


def _selected_python_is_isolated(
    prefix: Path, base_prefix: Path, *, environment: Mapping[str, str] | None = None,
) -> bool:
    """Accept virtualenvs or a natively identified active non-base Conda env."""
    prefix = prefix.resolve()
    if prefix != base_prefix.resolve():
        return True
    values = os.environ if environment is None else environment
    try:
        conda_prefix = Path(values["CONDA_PREFIX"]).resolve(strict=True)
        conda_executable = Path(values["CONDA_EXE"])
        conda_target = conda_executable.resolve(strict=True)
        if (
            conda_prefix != prefix
            or not conda_target.is_file()
            or not os.access(conda_target, os.X_OK)
        ):
            return False
        metadata = json.loads(subprocess.check_output(
            [str(conda_executable), "info", "--json"], text=True, timeout=30,
        ))
        if not isinstance(metadata, dict):
            return False
        active_prefix = Path(metadata["active_prefix"]).resolve(strict=True)
        root_prefix = Path(metadata["root_prefix"]).resolve(strict=True)
        history = prefix / "conda-meta/history"
        return (
            active_prefix == prefix
            and root_prefix != prefix
            and conda_target.is_relative_to(root_prefix)
            and history.is_file()
            and not history.is_symlink()
        )
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
        return False


def _runtime_identity(torch_module: Any | None = None) -> dict[str, Any]:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("The pinned LightX2V source package supports Linux x86_64 only.")
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("The pinned LightX2V source package requires the selected Python 3.11 runtime.")
    prefix = Path(sys.prefix).resolve()
    if not _selected_python_is_isolated(prefix, Path(sys.base_prefix)):
        raise RuntimeError(
            "LightX2V installation requires a selected Python virtual environment "
            "or an active non-base Conda environment."
        )
    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore[no-redef]
        except Exception as error:
            raise RuntimeError(f"PyTorch is unavailable in the selected runtime ({error}).") from error
    torch_version = str(getattr(torch_module, "__version__", ""))
    torch_cuda = str(getattr(getattr(torch_module, "version", None), "cuda", ""))
    if torch_version != "2.10.0+cu130" or torch_cuda != "13.0":
        raise RuntimeError(
            "LightX2V requires the selected Torch 2.10.0+cu130 runtime; "
            f"found Torch {torch_version or 'unknown'} with CUDA {torch_cuda or 'unknown'}."
        )
    site = Path(sysconfig.get_path("purelib")).resolve()
    if site.is_symlink() or not site.is_dir() or not site.is_relative_to(prefix):
        raise RuntimeError("The selected Python package directory must be an ordinary directory inside its venv.")
    cuda = site / "nvidia/cu13"
    include = cuda / "include"
    library = cuda / "lib"
    cuda_header = include / "cuda.h"
    cublas = library / "libcublas.so.13"
    cublas_lt = library / "libcublasLt.so.13"
    cuda_runtime = library / "libcudart.so.13"
    required = [cuda_header, cublas, cublas_lt, cuda_runtime]
    if any(path.is_symlink() or not path.is_file() for path in required):
        raise RuntimeError("The selected Torch runtime is missing complete ordinary CUDA 13 headers/libraries.")
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_abi": "cp311",
        "torch": torch_version,
        "torch_cuda": torch_cuda,
        "platform": "linux_x86_64",
        "prefix": str(prefix),
        "site": str(site),
        "cuda_include": str(include),
        "cuda_lib": str(library),
        "cublas": str(cublas),
        "cublas_lt": str(cublas_lt),
        "cuda_runtime": str(cuda_runtime),
        # Keep the selected venv entrypoint. Resolving its normal symlink would
        # silently replace it with UV/Conda's base interpreter for the worker.
        "executable": os.path.abspath(sys.executable),
    }


def _installer_recipe_sha256() -> str:
    return _sha256(Path(__file__).resolve())


def _receipt_context(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": VERSION,
        "kernels_revision": KERNELS_REVISION,
        "cutlass_revision": CUTLASS_REVISION,
        "patched_source_tree_sha256": PATCHED_SOURCE_TREE_SHA256,
        "cutlass_tree_sha256": CUTLASS_TREE_SHA256,
        "installer_recipe_sha256": _installer_recipe_sha256(),
        "python": identity["python"],
        "python_abi": identity["python_abi"],
        "torch": identity["torch"],
        "torch_cuda": identity["torch_cuda"],
        "platform": identity["platform"],
    }


def _distribution_roots(site: Path) -> list[Path]:
    roots: list[Path] = []
    package = site / "lightx2v_kernel"
    if package.exists() or package.is_symlink():
        roots.append(package)
    roots.extend(sorted(site.glob("lightx2v_kernel-*.dist-info"), key=lambda path: path.name))
    return roots


def _snapshot_distribution(site: Path, roots: Iterable[Path] | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for root in roots if roots is not None else _distribution_roots(site):
        for relative, digest in _tree_manifest(root, ignore_cache=True).items():
            result[f"{root.name}/{relative}"] = digest
    return result


def _digest_map(value: Any, *, allow_empty: bool = False) -> dict[str, str] | None:
    if not isinstance(value, dict) or (not value and not allow_empty):
        return None
    if any(
        not isinstance(name, str) or not name or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for name, digest in value.items()
    ):
        return None
    return value


def installation_matches_receipt(site: Path, receipt: dict[str, Any], identity: dict[str, Any]) -> bool:
    try:
        if receipt.get("schema_version") != RECEIPT_SCHEMA:
            return False
        if receipt.get("context") != _receipt_context(identity):
            return False
        roots_value = receipt.get("roots")
        installed = _digest_map(receipt.get("installed_files"))
        payload = _digest_map(receipt.get("payload"))
        if (
            not isinstance(roots_value, list)
            or any(not isinstance(name, str) for name in roots_value)
            or installed is None
            or payload is None
            or re.fullmatch(r"[0-9a-f]{64}", receipt.get("wheel_sha256", "")) is None
            or re.fullmatch(
                r"[0-9a-f]{64}", receipt.get("toolchain_packages_sha256", "")
            ) is None
        ):
            return False
        validation = receipt.get("validation")
        if not isinstance(validation, dict) or validation.get("evidence") != (
            "package-and-abi-only-no-gpu-execution"
        ):
            return False
        elf = validation.get("elf")
        abi = validation.get("abi")
        extension_digest = installed.get("lightx2v_kernel/common_ops.so")
        expected_mapped = {
            "extension": str((site / "lightx2v_kernel/common_ops.so").resolve()),
            "cublas_lt": str(Path(identity["cublas_lt"]).resolve()),
            "cuda_runtime": str(Path(identity["cuda_runtime"]).resolve()),
        }
        if (
            not isinstance(elf, dict)
            or elf.get("sha256") != extension_digest
            or not isinstance(abi, dict)
            or abi.get("version") != VERSION
            or abi.get("operators") != list(EXPECTED_OPERATORS)
            or abi.get("mapped") != expected_mapped
        ):
            return False
        installed_payload = {
            name: digest for name, digest in installed.items()
            if name.startswith("lightx2v_kernel/")
        }
        if payload != installed_payload:
            return False
        roots = _distribution_roots(site)
        if [path.name for path in roots] != roots_value:
            return False
        return _snapshot_distribution(site, roots) == installed
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_extract_tar(archive: Path, destination: Path, *, strip_prefix: str | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    with tarfile.open(archive, "r:") as bundle:
        for member in bundle.getmembers():
            source = Path(member.name)
            if source.is_absolute() or ".." in source.parts:
                raise RuntimeError("Pinned Git archive contains an unsafe path.")
            parts = source.parts
            if strip_prefix is not None:
                if not parts or parts[0] != strip_prefix:
                    raise RuntimeError("Pinned Git archive escaped its selected source subtree.")
                parts = parts[1:]
            if not parts:
                continue
            relative = Path(*parts)
            name = relative.as_posix()
            if name in seen:
                raise RuntimeError("Pinned Git archive contains a duplicate path.")
            seen.add(name)
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError("Pinned Git archive contains a link or special entry.")
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise RuntimeError("Pinned Git archive contains an unreadable file.")
            with target.open("xb") as handle:
                shutil.copyfileobj(extracted, handle)
    _regular_files(destination)


def _fetch_archive(
    *, git: str, repository: str, revision: str, checkout: Path,
    archive: Path, members: Sequence[str],
) -> None:
    checkout.mkdir(parents=True, exist_ok=False)
    _run([git, "init", "--quiet"], cwd=checkout)
    _run([git, "remote", "add", "origin", repository], cwd=checkout)
    _run(
        [git, "fetch", "--no-tags", "--depth", "1", "--filter=blob:none", "origin", revision],
        cwd=checkout,
    )
    actual = _output([git, "rev-parse", "FETCH_HEAD^{commit}"], cwd=checkout)
    if actual != revision:
        raise RuntimeError(f"Pinned source revision mismatch: expected {revision}, found {actual}.")
    _run(
        [git, "archive", "--format=tar", "--output", str(archive), revision, "--", *members],
        cwd=checkout,
    )
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size == 0:
        raise RuntimeError("Pinned Git source archive was not created.")


def _patch_source(source: Path) -> None:
    for relative, expected in PATCH_BASE_SHA256.items():
        path = source / relative
        if path.is_symlink() or not path.is_file() or _sha256(path) != expected:
            raise RuntimeError(f"Pinned LightX2V source bytes changed: {relative}")
    cublas = source / "csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu"
    text = cublas.read_text(encoding="utf-8")
    if text.count(UPSTREAM_CUBLAS_ERROR) != 1:
        raise RuntimeError("Pinned LightX2V cuBLAS error site changed.")
    cublas.write_text(text.replace(UPSTREAM_CUBLAS_ERROR, MAESTRO_CUBLAS_ERROR), encoding="utf-8")
    (source / "python/lightx2v_kernel/__init__.py").write_text(MAESTRO_INIT, encoding="utf-8")
    (source / "python/lightx2v_kernel/version.py").write_text(MAESTRO_VERSION, encoding="utf-8")
    manifest = _tree_manifest(source)
    if _manifest_digest(manifest) != PATCHED_SOURCE_TREE_SHA256:
        raise RuntimeError("Pinned LightX2V patched source manifest changed.")


def prepare_sources(attempt: Path, *, git: str) -> dict[str, Any]:
    downloads = attempt / "downloads"
    downloads.mkdir(parents=True, exist_ok=False)
    kernels_archive = downloads / "kernels.tar"
    cutlass_archive = downloads / "cutlass.tar"
    _fetch_archive(
        git=git, repository=KERNELS_REPOSITORY, revision=KERNELS_REVISION,
        checkout=downloads / "kernels.git", archive=kernels_archive,
        members=("lightx2v_kernel",),
    )
    _fetch_archive(
        git=git, repository=CUTLASS_REPOSITORY, revision=CUTLASS_REVISION,
        checkout=downloads / "cutlass.git", archive=cutlass_archive,
        members=("LICENSE.txt", "include", "tools/util/include"),
    )
    source = attempt / "source"
    cutlass = attempt / "cutlass"
    _safe_extract_tar(kernels_archive, source, strip_prefix="lightx2v_kernel")
    _safe_extract_tar(cutlass_archive, cutlass)
    _patch_source(source)
    cutlass_manifest = _tree_manifest(cutlass)
    if _manifest_digest(cutlass_manifest) != CUTLASS_TREE_SHA256:
        raise RuntimeError("Pinned CUTLASS source manifest changed.")
    result = {
        "kernels": {"repository": KERNELS_REPOSITORY, "revision": KERNELS_REVISION,
                    "tree_sha256": PATCHED_SOURCE_TREE_SHA256},
        "cutlass": {"repository": CUTLASS_REPOSITORY, "revision": CUTLASS_REVISION,
                   "tree_sha256": CUTLASS_TREE_SHA256},
        "translation_units": list(TRANSLATION_UNITS),
    }
    _atomic_json(attempt / "source-manifest.json", result)
    return result


def _toolchain_identity(prefix: Path) -> dict[str, Any] | None:
    try:
        resolved_prefix = prefix.resolve()

        def contained(path: Path | None, *, directory: bool = False) -> Path | None:
            if path is None:
                return None
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(resolved_prefix):
                return None
            if directory:
                return resolved if resolved.is_dir() else None
            return resolved if resolved.is_file() and os.access(resolved, os.X_OK) else None

        records: dict[str, dict[str, Any]] = {}
        for path in sorted((prefix / "conda-meta").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("name"), str):
                records[value["name"]] = value
        for name, expected_version in REQUIRED_TOOLCHAIN_PACKAGES.items():
            if name not in records or (
                expected_version is not None and str(records[name].get("version")) != expected_version
            ):
                return None
        for name in ("cuda-nvcc", "cuda-nvcc_linux-64", "cuda-cccl_linux-64"):
            if "/nvidia/label/cuda-13.0.2" not in str(records[name].get("channel", "")):
                return None
        nvcc = prefix / "bin/nvcc"
        ninja = prefix / "bin/ninja"
        compiler = next(iter(sorted((prefix / "bin").glob("*-gcc"))), None)
        cxx = next(iter(sorted((prefix / "bin").glob("*-g++"))), None)
        sysroot = next(iter(sorted(prefix.glob("*/sysroot"))), None)
        cccl_candidates = (
            prefix / "targets/x86_64-linux/include/cccl",
            prefix / "targets/x86_64-linux/include",
            prefix / "include/cccl",
            prefix / "include",
        )
        cccl_include = next((path for path in cccl_candidates if (path / "cuda/std").is_dir()), None)
        nvcc_target = contained(nvcc)
        ninja_target = contained(ninja)
        compiler_target = contained(compiler)
        cxx_target = contained(cxx)
        sysroot_target = contained(sysroot, directory=True)
        cccl_target = contained(cccl_include, directory=True)
        cccl_std_target = contained(
            cccl_include / "cuda/std" if cccl_include is not None else None,
            directory=True,
        )
        required_paths = [
            nvcc_target, ninja_target, compiler_target, cxx_target,
            sysroot_target, cccl_target, cccl_std_target,
        ]
        if any(path is None for path in required_paths):
            return None
        version = subprocess.check_output([str(nvcc), "--version"], text=True, timeout=20)
        if "release 13.0" not in version or "V13.0.88" not in version:
            return None
        compiler_major = int(
            subprocess.check_output([str(compiler), "-dumpversion"], text=True, timeout=20)
            .strip().split(".", 1)[0]
        )
        cxx_major = int(
            subprocess.check_output([str(cxx), "-dumpversion"], text=True, timeout=20)
            .strip().split(".", 1)[0]
        )
        if compiler_major != REQUIRED_COMPILER_MAJOR or cxx_major != REQUIRED_COMPILER_MAJOR:
            return None
        packages = [
            {key: record.get(key) for key in ("name", "version", "build", "channel", "sha256")}
            for record in sorted(records.values(), key=lambda value: str(value.get("name")))
        ]
        return {
            "nvcc": str(nvcc_target), "ninja": str(ninja_target),
            "cc": str(compiler_target), "cxx": str(cxx_target),
            "sysroot": str(sysroot_target), "cccl_include": str(cccl_target),
            "packages": packages,
            "packages_sha256": hashlib.sha256(
                json.dumps(packages, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ensure_toolchain(prefix: Path, *, conda: str) -> dict[str, Any]:
    expected_prefix = STATE_ROOT / "toolchain"
    if prefix.is_symlink() or prefix.absolute() != expected_prefix.absolute():
        raise RuntimeError("The managed CUDA compiler prefix must remain inside LightX2V's app-local state.")
    identity = _toolchain_identity(prefix)
    if identity is not None:
        return identity
    prefix.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, CONDA_PKGS_DIRS=str(STATE_ROOT / "conda-pkgs"))
    command = [
        conda, "create", "--yes", "--prefix", str(prefix), "--override-channels",
        "--channel", CUDA_CHANNEL, "--channel", DEPENDENCY_CHANNEL, *TOOLCHAIN_PACKAGES,
    ]
    print("[LightX2V] provisioning the pinned CUDA 13 compiler in an isolated app prefix")
    _run(command, env=environment, timeout=1800)
    identity = _toolchain_identity(prefix)
    if identity is None:
        raise RuntimeError("The isolated CUDA 13 toolchain is incomplete or does not match its pins.")
    return identity


def _verify_elf(extension: Path, *, readelf: str) -> dict[str, Any]:
    dynamic = subprocess.check_output([readelf, "-d", str(extension)], text=True, timeout=60)
    needed = sorted(
        match.group(1) for line in dynamic.splitlines()
        if "NEEDED" in line and (match := re.search(r"\[([^]]+)\]", line))
    )
    for library in ("libcublasLt.so.13", "libcudart.so.13"):
        if library not in needed:
            raise RuntimeError(f"LightX2V extension is missing CUDA 13 dependency {library}.")
    if any(name.startswith(("libcublas.so.12", "libcublasLt.so.12", "libcudart.so.12")) for name in needed):
        raise RuntimeError("LightX2V extension retained a CUDA 12 dependency.")
    runpath = "$ORIGIN/../torch/lib:$ORIGIN/../nvidia/cu13/lib"
    if runpath not in dynamic:
        raise RuntimeError("LightX2V extension is missing its selected-runtime CUDA 13 runpath.")
    return {"needed": needed, "runpath": runpath, "sha256": _sha256(extension)}


def _write_wheel(package: Path, source: Path, cutlass: Path, output: Path) -> dict[str, Any]:
    files: dict[str, bytes] = {}
    payload: dict[str, str] = {}
    for path in _regular_files(package):
        relative = path.relative_to(package).as_posix()
        archive_name = f"lightx2v_kernel/{relative}"
        data = path.read_bytes()
        files[archive_name] = data
        payload[archive_name] = hashlib.sha256(data).hexdigest()
    expected_payload = {Path(name).relative_to("python/lightx2v_kernel").as_posix() for name in PACKAGE_SOURCES}
    if set(path.removeprefix("lightx2v_kernel/") for path in payload) != expected_payload | {"common_ops.so"}:
        raise RuntimeError("LightX2V package payload is incomplete or contains an unexpected file.")
    distribution = f"lightx2v_kernel-{VERSION}.dist-info"
    files[f"{distribution}/METADATA"] = (
        "Metadata-Version: 2.1\nName: lightx2v-kernel\n"
        f"Version: {VERSION}\n"
        "Summary: LightX2V kernels for Maestro's selected CUDA 13 runtime\n"
        "Requires-Python: >=3.11,<3.12\nRequires-Dist: torch==2.10.0\n\n"
    ).encode("utf-8")
    files[f"{distribution}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: maestro-pinned-source\n"
        "Root-Is-Purelib: false\nTag: cp311-cp311-linux_x86_64\n"
    ).encode("utf-8")
    for path, name in ((source / "LICENSE", "LICENSE"), (cutlass / "LICENSE.txt", "CUTLASS-LICENSE.txt")):
        if path.is_symlink() or not path.is_file() or not path.read_bytes():
            raise RuntimeError("Pinned source license file is missing.")
        files[f"{distribution}/licenses/{name}"] = path.read_bytes()
    rows: list[tuple[str, str, str]] = []
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        rows.append((name, f"sha256={digest}", str(len(data))))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    writer.writerow((f"{distribution}/RECORD", "", ""))
    files[f"{distribution}/RECORD"] = stream.getvalue().encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / f"lightx2v_kernel-{VERSION}-cp311-cp311-linux_x86_64.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None or any(archive.read(name) != data for name, data in files.items()):
            raise RuntimeError("Deterministic LightX2V wheel verification failed.")
    return {"wheel": str(wheel), "wheel_sha256": _sha256(wheel), "payload": payload}


def _build_worker(plan_path: Path) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if os.path.abspath(sys.executable) != os.path.abspath(plan["runtime"]["executable"]):
        raise RuntimeError("The managed-toolchain build did not retain the selected Python interpreter.")
    if os.environ.get("MAX_JOBS") != "1" or os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise RuntimeError("The LightX2V build must use one Ninja worker with CUDA devices hidden.")
    source = Path(plan["source"])
    cutlass = Path(plan["cutlass"])
    if _manifest_digest(_tree_manifest(source)) != PATCHED_SOURCE_TREE_SHA256:
        raise RuntimeError("LightX2V source changed before compilation.")
    if _manifest_digest(_tree_manifest(cutlass)) != CUTLASS_TREE_SHA256:
        raise RuntimeError("CUTLASS source changed before compilation.")
    sources = [source / name for name in TRANSLATION_UNITS]
    if any(path.is_symlink() or not path.is_file() for path in sources) or len(sources) != 9:
        raise RuntimeError("The complete nine-unit LightX2V build input is unavailable.")

    import torch
    from torch.utils.cpp_extension import load
    if str(torch.__version__) != "2.10.0+cu130" or str(torch.version.cuda) != "13.0":
        raise RuntimeError("The build worker left the selected Torch 2.10 CUDA 13 runtime.")
    runtime = plan["runtime"]
    cuda_library = Path(runtime["cuda_lib"])
    runtime_libraries = [
        Path(runtime["cublas_lt"]), Path(runtime["cublas"]), Path(runtime["cuda_runtime"]),
    ]
    if any(
        path.is_symlink() or not path.is_file() or path.parent.resolve() != cuda_library.resolve()
        for path in runtime_libraries
    ):
        raise RuntimeError("The build plan lost its selected-runtime CUDA 13 libraries.")
    extension = load(
        name="common_ops", sources=[str(path) for path in sources],
        extra_include_paths=[str(source / "include"), str(source / "csrc"),
                             str(cutlass / "include"), str(cutlass / "tools/util/include"),
                             runtime["cuda_include"], plan["toolchain"]["cccl_include"]],
        extra_cflags=["-O3", "-DNDEBUG"], extra_cuda_cflags=list(CUDA_FLAGS),
        extra_ldflags=[
            f"-L{cuda_library}", *[str(path) for path in runtime_libraries], NINJA_RUNPATH_FLAG,
        ],
        build_directory=plan["build"], with_cuda=True, is_python_module=False, verbose=True,
    )
    extension_path = Path(extension)
    elf = _verify_elf(extension_path, readelf=plan["readelf"])
    package = Path(plan["package"])
    package.mkdir(parents=True, exist_ok=False)
    for relative in PACKAGE_SOURCES:
        shutil.copy2(source / relative, package / Path(relative).name)
    shutil.copy2(extension_path, package / "common_ops.so")
    wheel = _write_wheel(package, source, cutlass, Path(plan["wheel_dir"]))
    result = {**wheel, "elf": elf, "translation_units": list(TRANSLATION_UNITS),
              "cuda_flags": list(CUDA_FLAGS), "max_jobs": 1}
    _atomic_json(Path(plan["result"]), result)
    return 0


def build_package(
    attempt: Path, *, conda: str, toolchain_prefix: Path,
    toolchain: dict[str, Any], runtime: dict[str, Any], readelf: str,
) -> dict[str, Any]:
    build = attempt / "build"
    build.mkdir(parents=True, exist_ok=False)
    plan = {
        "source": str(attempt / "source"), "cutlass": str(attempt / "cutlass"),
        "build": str(build), "package": str(attempt / "package/lightx2v_kernel"),
        "wheel_dir": str(attempt / "wheel"), "result": str(attempt / "package-manifest.json"),
        "readelf": readelf, "runtime": runtime,
        "toolchain": {"cccl_include": toolchain["cccl_include"]},
    }
    plan_path = attempt / "build-plan.json"
    _atomic_json(plan_path, plan)
    environment = dict(os.environ)
    environment.update({
        "CC": toolchain["cc"], "CXX": toolchain["cxx"], "CUDAHOSTCXX": toolchain["cxx"],
        "CUDA_HOME": str(toolchain_prefix), "MAX_JOBS": "1", "CUDA_VISIBLE_DEVICES": "",
        "PYTHONNOUSERSITE": "1", "TORCH_EXTENSIONS_DIR": str(attempt / "torch-extensions"),
        "PATH": str(toolchain_prefix / "bin") + os.pathsep + environment.get("PATH", ""),
        "LIBRARY_PATH": runtime["cuda_lib"] + os.pathsep + str(toolchain_prefix / "lib"),
        "LD_LIBRARY_PATH": runtime["cuda_lib"] + os.pathsep + str(toolchain_prefix / "lib"),
    })
    command = [conda, "run", "--prefix", str(toolchain_prefix), "--no-capture-output",
               runtime["executable"], str(Path(__file__).resolve()), "--build-worker", str(plan_path)]
    _run(command, cwd=APP_ROOT, env=environment, timeout=3600)
    result = json.loads((attempt / "package-manifest.json").read_text(encoding="utf-8"))
    wheel = Path(result["wheel"])
    if not wheel.is_file() or _sha256(wheel) != result.get("wheel_sha256"):
        raise RuntimeError("LightX2V build did not produce its verified local wheel.")
    return result


def _validate_installed(
    *, site: Path, package_manifest: dict[str, Any], identity: dict[str, Any], readelf: str,
) -> dict[str, Any]:
    roots = _distribution_roots(site)
    expected_info = f"lightx2v_kernel-{VERSION}.dist-info"
    if [path.name for path in roots] != ["lightx2v_kernel", expected_info]:
        raise RuntimeError("Installed LightX2V metadata is missing, duplicated, or has the wrong version.")
    package = site / "lightx2v_kernel"
    observed = {
        f"lightx2v_kernel/{name}": digest
        for name, digest in _tree_manifest(package, ignore_cache=True).items()
    }
    if observed != package_manifest.get("payload"):
        raise RuntimeError("Installed LightX2V payload bytes do not match the verified local wheel.")
    elf = _verify_elf(package / "common_ops.so", readelf=readelf)
    expected_mapped = {
        "extension": str((package / "common_ops.so").resolve()),
        "cublas_lt": str(Path(identity["cublas_lt"]).resolve()),
        "cuda_runtime": str(Path(identity["cuda_runtime"]).resolve()),
    }
    check = (
        "import importlib.metadata as m,json,pathlib,torch;"
        f"assert m.version('lightx2v-kernel')=={VERSION!r};"
        "import lightx2v_kernel;"
        "p=pathlib.Path(lightx2v_kernel.__file__).resolve().parent;"
        f"assert p==pathlib.Path({str(package.resolve())!r});"
        f"ops={list(EXPECTED_OPERATORS)!r};ns=torch.ops.lightx2v_kernel;"
        "assert all(hasattr(ns,name) for name in ops);"
        "rows=[line.split(maxsplit=5) for line in pathlib.Path('/proc/self/maps').read_text().splitlines()];"
        "mapped={str(pathlib.Path(row[-1]).resolve()) for row in rows if len(row)==6 and row[-1].startswith('/')};"
        f"expected={expected_mapped!r};"
        "assert set(expected.values()).issubset(mapped);"
        "assert not any(pathlib.Path(name).name.startswith(('libcublas.so.12','libcublasLt.so.12','libcudart.so.12')) for name in mapped);"
        "print(json.dumps({'version':m.version('lightx2v-kernel'),'operators':ops,'mapped':expected},sort_keys=True))"
    )
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONNOUSERSITE="1")
    environment["LD_LIBRARY_PATH"] = os.pathsep.join((
        str(site / "torch/lib"), identity["cuda_lib"],
    ))
    completed = subprocess.run(
        [identity["executable"], "-c", check], env=environment, cwd=APP_ROOT,
        capture_output=True, text=True, check=True, timeout=120,
    )
    abi = json.loads(completed.stdout.strip().splitlines()[-1])
    if (
        not isinstance(abi, dict)
        or abi.get("version") != VERSION
        or abi.get("operators") != list(EXPECTED_OPERATORS)
        or abi.get("mapped") != expected_mapped
    ):
        raise RuntimeError("Fresh-process LightX2V ABI or mapped-library evidence is incomplete.")
    return {"elf": elf, "abi": abi, "evidence": "package-and-abi-only-no-gpu-execution"}


def _remove_distribution(site: Path) -> None:
    for path in _distribution_roots(site):
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError(f"Refusing to remove unsafe LightX2V rollback target: {path}")
    for path in _distribution_roots(site):
        shutil.rmtree(path)


def install_transaction(
    *, wheel: Path, uv: str, site: Path, attempt: Path, receipt_path: Path,
    identity: dict[str, Any], toolchain: dict[str, Any], package_manifest: dict[str, Any],
    readelf: str, validator: Callable[..., dict[str, Any]] = _validate_installed,
) -> dict[str, Any]:
    if receipt_path.is_symlink():
        raise RuntimeError("The LightX2V receipt path must not be a link.")
    roots = _distribution_roots(site)
    baseline = _snapshot_distribution(site, roots)
    backup = attempt / "rollback"
    backup.mkdir(parents=True, exist_ok=False)
    for path in roots:
        shutil.copytree(path, backup / path.name)
    previous_receipt = receipt_path.read_bytes() if receipt_path.is_file() else None
    if previous_receipt is not None:
        (backup / RECEIPT_NAME).write_bytes(previous_receipt)
    _atomic_json(attempt / "baseline.json", {"roots": [path.name for path in roots], "files": baseline})
    environment = dict(
        os.environ, UV_CACHE_DIR=str(STATE_ROOT / "uv-cache"), UV_NO_INDEX="1",
        UV_LINK_MODE="copy", UV_COMPILE_BYTECODE="0", CUDA_VISIBLE_DEVICES="",
        PYTHONNOUSERSITE="1",
    )
    try:
        _run([uv, "pip", "install", "--python", sys.executable, "--no-index", "--no-deps",
              "--force-reinstall", str(wheel)], cwd=APP_ROOT, env=environment, timeout=180)
        validation = validator(
            site=site, package_manifest=package_manifest, identity=identity, readelf=readelf,
        )
        installed_roots = _distribution_roots(site)
        installed_files = _snapshot_distribution(site, installed_roots)
        receipt = {
            "schema_version": RECEIPT_SCHEMA, "context": _receipt_context(identity),
            "roots": [path.name for path in installed_roots], "installed_files": installed_files,
            "wheel_sha256": package_manifest["wheel_sha256"],
            "payload": package_manifest["payload"],
            "toolchain_packages_sha256": toolchain["packages_sha256"],
            "validation": validation,
        }
        _atomic_json(receipt_path, receipt)
        return receipt
    except BaseException as install_error:
        try:
            _remove_distribution(site)
            for path in roots:
                shutil.copytree(backup / path.name, path)
            if previous_receipt is None:
                receipt_path.unlink(missing_ok=True)
            else:
                _atomic_bytes(receipt_path, previous_receipt)
            if _snapshot_distribution(site) != baseline:
                raise RuntimeError("LightX2V rollback bytes do not match the pre-install baseline.")
            if (
                (previous_receipt is None and receipt_path.exists())
                or (previous_receipt is not None and receipt_path.read_bytes() != previous_receipt)
            ):
                raise RuntimeError("LightX2V rollback receipt does not match the pre-install baseline.")
        except BaseException as rollback_error:
            raise RuntimeError(
                f"LightX2V installation failed and exact rollback also failed: {rollback_error}"
            ) from install_error
        raise


def _new_attempt(state: Path) -> Path:
    _validate_state_root(state)
    # Attempts can contain the only exact rollback after a failed restoration.
    # Retain every such audit/recovery directory for explicit operator cleanup.
    return Path(tempfile.mkdtemp(prefix="attempt-", dir=state))


def _validate_state_root(state: Path) -> None:
    if state.is_symlink():
        raise RuntimeError("LightX2V build state must remain inside the Maestro app directory.")
    state.mkdir(parents=True, exist_ok=True)
    if state.is_symlink() or not state.resolve().is_relative_to(APP_ROOT.resolve()):
        raise RuntimeError("LightX2V build state must remain inside the Maestro app directory.")


def main() -> int:
    runtime = _runtime_identity()
    _validate_state_root(STATE_ROOT)
    with _exclusive_lock(STATE_ROOT / STATE_LOCK_NAME):
        with _exclusive_lock(Path(runtime["prefix"]) / RUNTIME_LOCK_NAME):
            return _main_locked(runtime)


def _main_locked(runtime: dict[str, Any]) -> int:
    site = Path(runtime["site"])
    receipt_path = Path(runtime["prefix"]) / RECEIPT_NAME
    receipt = _read_receipt(receipt_path)
    readelf = _resolve_tool("readelf")
    if installation_matches_receipt(site, receipt, runtime):
        try:
            _validate_installed(
                site=site, package_manifest={"payload": receipt["payload"]},
                identity=runtime, readelf=readelf,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
            pass
        else:
            print("[LightX2V] pinned CUDA 13 package bytes and ABI already verified")
            return 0
    git = _resolve_tool("git")
    uv = _resolve_tool("uv")
    conda = _conda_executable()
    attempt = _new_attempt(STATE_ROOT)
    try:
        prepare_sources(attempt, git=git)
        toolchain_prefix = STATE_ROOT / "toolchain"
        toolchain = ensure_toolchain(toolchain_prefix, conda=conda)
        package = build_package(
            attempt, conda=conda, toolchain_prefix=toolchain_prefix,
            toolchain=toolchain, runtime=runtime, readelf=readelf,
        )
        wheel = Path(package["wheel"])
        install_transaction(
            wheel=wheel, uv=uv, site=site, attempt=attempt, receipt_path=receipt_path,
            identity=runtime, toolchain=toolchain, package_manifest=package, readelf=readelf,
        )
    except BaseException:
        print(f"[LightX2V] failed build/recovery artifacts retained at {attempt}", file=sys.stderr)
        raise
    print("[LightX2V] pinned CUDA 13 package installed; package/ABI checks passed (GPU math not tested)")
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-worker", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _raise_on_termination(_signum: int, _frame: Any) -> None:
    """Let catchable CLI termination unwind through package rollback."""
    raise TimeoutError("LightX2V installation was terminated before completion.")


if __name__ == "__main__":
    arguments = _parse_args()
    previous_handler = signal.signal(signal.SIGTERM, _raise_on_termination)
    try:
        raise SystemExit(_build_worker(arguments.build_worker) if arguments.build_worker else main())
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
