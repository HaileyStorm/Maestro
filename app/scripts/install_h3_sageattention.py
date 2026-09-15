#!/usr/bin/env python3
"""Build pinned official SageAttention2++ only inside its proven envelope."""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path
import platform
import re
import shlex
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
CUDA13_TORCH = "2.10.0+cu130"
CUDA13_VERSION = "13.0"


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


def _expected_marker(torch_module) -> dict[str, object]:
    return {
        "revision": REVISION,
        "version": VERSION,
        "torch": str(torch_module.__version__),
        "torch_cuda": str(torch_module.version.cuda),
        "compute_capability": [12, 0],
    }


def _verified_install(expected: dict[str, object]) -> bool:
    """Return whether the installed package still matches its source marker."""
    marker = _read_marker()
    try:
        source = CHECKOUT.resolve()
    except OSError:
        return False
    distribution_digest = _distribution_digest()
    return (
        all(marker.get(key) == value for key, value in expected.items())
        and _installed_version() == VERSION
        and _distribution_source() == source
        and isinstance(distribution_digest, str)
        and bool(distribution_digest)
        and marker.get("distribution_sha256") == distribution_digest
    )


def _lightx2v_installer_module():
    """Load the read-only CUDA 13 identity helpers without provisioning state."""
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))
    try:
        from scripts import install_lightx2v_runtime
    except Exception as error:
        raise RuntimeError(
            "the LightX2V CUDA 13 identity helpers are unavailable; run Update first"
        ) from error
    return install_lightx2v_runtime


def _ordinary_directory(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise RuntimeError(f"verified CUDA 13 {label} is missing")
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"verified CUDA 13 {label} is missing or not an ordinary directory")
    return path.resolve()


def _ordinary_file(value: object, label: str, *, executable: bool = False) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise RuntimeError(f"verified CUDA 13 {label} is missing")
    path = Path(value)
    if path.is_symlink() or not path.is_file() or (executable and not os.access(path, os.X_OK)):
        suffix = " executable" if executable else ""
        raise RuntimeError(f"verified CUDA 13 {label}{suffix} is missing or not an ordinary file")
    return path.resolve()


def _validated_cuda13_inputs(runtime: dict[str, object], toolchain: dict[str, object], prefix: Path):
    if runtime.get("torch") != CUDA13_TORCH or runtime.get("torch_cuda") != CUDA13_VERSION:
        raise RuntimeError(
            "the selected runtime is not the required Torch 2.10.0+cu130 / CUDA 13.0 environment"
        )
    cuda_include = _ordinary_directory(runtime.get("cuda_include"), "header directory")
    cuda_header = _ordinary_file(cuda_include / "cuda.h", "cuda.h")
    cuda_lib = _ordinary_directory(runtime.get("cuda_lib"), "library directory")
    cuda_runtime = _ordinary_file(runtime.get("cuda_runtime"), "libcudart.so.13")
    if cuda_runtime.name != "libcudart.so.13" or cuda_runtime.parent != cuda_lib:
        raise RuntimeError(
            "verified CUDA 13 libcudart.so.13 must live directly in the selected runtime library directory"
        )

    toolchain_bin = _ordinary_directory(prefix / "bin", "compiler bin directory")
    toolchain_lib = _ordinary_directory(prefix / "lib", "compiler library directory")
    nvcc = _ordinary_file(toolchain.get("nvcc"), "nvcc", executable=True)
    ninja = _ordinary_file(toolchain.get("ninja"), "ninja", executable=True)
    cc = _ordinary_file(toolchain.get("cc"), "host C compiler", executable=True)
    cxx = _ordinary_file(toolchain.get("cxx"), "host C++ compiler", executable=True)
    for name, path in (("nvcc", nvcc), ("ninja", ninja), ("host C compiler", cc), ("host C++ compiler", cxx)):
        if not path.is_relative_to(prefix.resolve()):
            raise RuntimeError(f"verified CUDA 13 {name} escaped the managed compiler prefix")
    cccl_include = _ordinary_directory(toolchain.get("cccl_include"), "CCCL include directory")
    _ordinary_directory(cccl_include / "cuda" / "std", "CCCL cuda/std directory")
    if not toolchain_bin.is_relative_to(prefix.resolve()) or not toolchain_lib.is_relative_to(prefix.resolve()):
        raise RuntimeError("verified CUDA 13 compiler paths escaped the managed compiler prefix")
    return {
        "cuda_include": cuda_include,
        "cuda_header": cuda_header,
        "cuda_lib": cuda_lib,
        "cuda_runtime": cuda_runtime,
        "nvcc": nvcc,
        "ninja": ninja,
        "cc": cc,
        "cxx": cxx,
        "cccl_include": cccl_include,
        "toolchain_lib": toolchain_lib,
    }


def _cuda13_identities(torch_module):
    """Read and validate the shared CUDA 13 runtime and compiler identities."""
    installer = _lightx2v_installer_module()
    try:
        runtime = installer._runtime_identity(torch_module)
    except Exception as error:
        raise RuntimeError(
            "the selected Torch CUDA 13 runtime is unavailable; run Update before installing SageAttention "
            f"({error})"
        ) from error

    prefix = APP_ROOT / ".lightx2v-runtime" / "toolchain"
    if prefix.is_symlink() or not prefix.is_dir():
        raise RuntimeError(
            "the managed CUDA compiler prefix must remain exactly app/.lightx2v-runtime/toolchain; run Update first"
        )
    try:
        toolchain = installer._toolchain_identity(prefix)
    except Exception as error:
        raise RuntimeError(
            "the selected CUDA 13 compiler toolchain could not be inspected; run Update first"
        ) from error
    if toolchain is None:
        raise RuntimeError(
            "the selected CUDA 13 compiler toolchain is incomplete or mismatched; run Update first"
        )
    inputs = _validated_cuda13_inputs(runtime, toolchain, prefix)
    return runtime, toolchain, prefix, inputs


@contextmanager
def _private_cuda13_linker(runtime: dict[str, object]):
    """Expose only a temporary unversioned cudart linker name to Sage's setup."""
    temporary_root = "/var/tmp" if Path("/var/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix=".maestro-sage2-cuda13-", dir=temporary_root) as directory:
        alias = Path(directory) / "libcudart.so"
        try:
            alias.symlink_to(Path(runtime["cuda_runtime"]))
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise RuntimeError(
                "a private libcudart.so linker alias could not be created for the verified CUDA 13 runtime"
            ) from error
        yield Path(directory)


def _cuda13_environment(
    prefix: Path,
    inputs: dict[str, Path],
    linker_directory: Path,
) -> dict[str, str]:
    environment = os.environ.copy()
    include_paths = os.pathsep.join(
        str(path) for path in (inputs["cuda_include"], inputs["cccl_include"])
    )
    library_paths = os.pathsep.join(
        str(path) for path in (linker_directory, inputs["cuda_lib"], inputs["toolchain_lib"])
    )
    linker_flags = " ".join(
        f"-L{shlex.quote(str(path))}" for path in (linker_directory, inputs["cuda_lib"])
    )
    environment.update({
        "CC": str(inputs["cc"]),
        "CXX": str(inputs["cxx"]),
        "CUDAHOSTCXX": str(inputs["cxx"]),
        "CUDA_HOME": str(prefix),
        "CUDA_PATH": str(prefix),
        "CUDA_INCLUDE_PATH": str(inputs["cuda_include"]),
        "PYTHONNOUSERSITE": "1",
        "CPATH": include_paths,
        "CPLUS_INCLUDE_PATH": include_paths,
        "LIBRARY_PATH": library_paths,
        "LDFLAGS": linker_flags,
        "LD_LIBRARY_PATH": os.pathsep.join(
            str(path) for path in (inputs["cuda_lib"], inputs["toolchain_lib"])
        ),
        "TORCH_CUDA_ARCH_LIST": "12.0",
        "EXT_PARALLEL": environment.get("EXT_PARALLEL", "4"),
        "MAX_JOBS": environment.get("MAX_JOBS", str(min(16, os.cpu_count() or 4))),
    })
    environment["PATH"] = os.pathsep.join(
        path for path in (str(prefix / "bin"), environment.get("PATH", "")) if path
    )
    return environment


def main() -> int:
    if platform.system() != "Linux":
        print("[H3 Sage2] skipped: the pinned source build is Linux-only; dense SDPA remains available")
        return 0
    try:
        import torch
    except Exception as error:
        print(f"[H3 Sage2] skipped: PyTorch is unavailable ({error})")
        return 0
    if not torch.cuda.is_available():
        print("[H3 Sage2] skipped: CUDA is unavailable; dense SDPA remains available")
        return 0
    capability = tuple(torch.cuda.get_device_capability(0))
    if capability != (12, 0):
        print(f"[H3 Sage2] skipped: detected SM{capability[0]}{capability[1]}, currently gated to SM120")
        return 0
    torch_version = str(torch.__version__)
    torch_cuda = str(torch.version.cuda)
    if _version(torch_cuda) < (12, 8):
        print(f"[H3 Sage2] skipped: PyTorch CUDA {torch_cuda} is below 12.8")
        return 0
    revision = _git_revision()
    if revision != REVISION:
        print(f"[H3 Sage2] skipped: pinned official checkout revision is missing or mismatched ({revision})")
        return 0
    if not _git_source_clean():
        print("[H3 Sage2] skipped: official SageAttention checkout has local source changes")
        return 0

    expected = _expected_marker(torch)
    # A verified package is usable even when the shared CUDA 13 compiler
    # toolchain is unavailable. Keep this check before any compiler setup.
    if _verified_install(expected):
        print("[H3 Sage2] pinned official v2.2.0 SM120 build already verified")
        return 0

    build_context = nullcontext(None)
    runtime = None
    toolchain_prefix = None
    inputs = None
    build_executable = sys.executable
    if _version(torch_cuda) >= (13, 0):
        if torch_version != CUDA13_TORCH or torch_cuda != CUDA13_VERSION:
            print(
                "[H3 Sage2] skipped: CUDA 13 requires the selected Torch 2.10.0+cu130 / CUDA 13.0 runtime; "
                "run Update first"
            )
            return 0
        try:
            runtime, _toolchain, toolchain_prefix, inputs = _cuda13_identities(torch)
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError) as error:
            print(f"[H3 Sage2] skipped: CUDA 13 runtime/toolchain prerequisite is unavailable ({error})")
            return 0
        build_context = _private_cuda13_linker(runtime)
        build_executable = str(runtime.get("executable") or sys.executable)
    else:
        try:
            from torch.utils.cpp_extension import CUDA_HOME
        except Exception as error:
            print(f"[H3 Sage2] skipped: PyTorch/CUDA build support is unavailable ({error})")
            return 0
        cuda_home = Path(CUDA_HOME) if CUDA_HOME is not None else Path("/")
        if _nvcc_version(cuda_home) < (12, 8):
            provisioned = _ensure_cuda_toolkit()
            if provisioned is None:
                print("[H3 Sage2] skipped: an nvcc CUDA 12.8+ toolkit is required to build official v2.2.0")
                return 0
            cuda_home = provisioned
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
        build_executable = sys.executable

    try:
        with build_context as linker_directory:
            if linker_directory is not None:
                environment = _cuda13_environment(toolchain_prefix, inputs, linker_directory)
            command = [
                build_executable, "-m", "pip", "install", "--no-build-isolation",
                "--no-deps", "--force-reinstall", ".",
            ]
            print(f"[H3 Sage2] building official SageAttention v{VERSION} from pinned source {REVISION[:12]}")
            try:
                subprocess.run(command, cwd=CHECKOUT, env=environment, check=True)
            except (OSError, subprocess.CalledProcessError) as error:
                MARKER.unlink(missing_ok=True)
                print(f"[H3 Sage2] optional build failed ({error}); dense SDPA remains available")
                return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError) as error:
        print(f"[H3 Sage2] skipped: CUDA compiler/runtime preparation failed ({error})")
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
