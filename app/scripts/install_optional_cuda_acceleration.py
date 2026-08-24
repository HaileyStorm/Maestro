"""Install optional Linux CUDA accelerators without blocking Maestro startup.

The CUDA runtime bundled in a PyTorch wheel is not an nvcc compiler. Linux
hosts commonly have a CUDA 12.8 toolkit on PATH even when Maestro correctly
uses PyTorch CUDA 13. Building SageAttention or FlashAttention in that state
fails with a CUDA-version mismatch. These tested prebuilt wheels avoid the
local compiler entirely. A transient download or optional-wheel failure is
reported, but intentionally does not invalidate the required H3 Sol runtime.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


# This is the same multi-architecture Linux wheel shipped by Pinokio's WanGP
# launcher. Pinning its source commit and digest prevents a moving binary from
# being substituted underneath an existing Maestro release.
SAGEATTENTION_WHEEL = (
    "https://raw.githubusercontent.com/pinokiofactory/wan/"
    "460f991ddd762ca5bc80e88e63b7a18773267d27/wheel/"
    "sageattention-2.2.0-cp311-cp311-linux_x86_64.whl"
    "#sha256=2ce936012a361e80a3ac4db61243a13c995b56e5073877f2ffd80dbbe68ca52a"
)
FLASHATTENTION_WHEEL = (
    "https://github.com/mjun0812/flash-attention-prebuild-wheels/releases/"
    "download/v0.7.16/"
    "flash_attn-2.8.3+cu130torch2.10-cp311-cp311-linux_x86_64.whl"
)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _resolve_marker(marker: str, *, app_root: Path) -> Path:
    """Resolve one app-relative readiness marker without escaping app_root."""
    relative = Path(marker)
    if not marker or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("marker must be a safe app-relative path")
    root = app_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("marker must stay inside the Maestro app directory") from exc
    return resolved


def _publish_marker(marker: Path, *, ready: bool) -> None:
    """Publish readiness only after every requested optional install succeeds."""
    if not ready:
        try:
            marker.unlink(missing_ok=True)
        except OSError as exc:
            print(
                "[Optional acceleration] The stale readiness marker could not be "
                f"removed ({type(exc).__name__}); normal Update will retry."
            )
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "Maestro optional CUDA acceleration wheels installed successfully.\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(
            "[Optional acceleration] Packages installed, but readiness could not be "
            f"recorded ({type(exc).__name__}); normal Update will retry."
        )
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass


def _installer_prefix() -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install"]
    return [sys.executable, "-m", "pip", "install"]


def install_optional_wheel(
    label: str,
    url: str,
    *,
    runner: Runner = subprocess.run,
) -> bool:
    """Install one wheel and convert failure into an explicit safe fallback."""
    command = [*_installer_prefix(), url, "--force-reinstall", "--no-deps"]
    try:
        result = runner(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"[Optional acceleration] {label} was skipped because the package "
            f"installer could not start ({type(exc).__name__})."
        )
        return False

    returncode = getattr(result, "returncode", 1)
    if returncode != 0:
        print(
            f"[Optional acceleration] {label} was not installed (installer exit "
            f"{returncode}). Maestro will continue with Sol/SDPA; use "
            "the normal Update action in Pinokio to retry later."
        )
        return False

    print(f"[Optional acceleration] {label} is ready.")
    return True


def main(
    argv: Sequence[str] | None = None,
    *,
    app_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--flash-only",
        action="store_true",
        help="Repair only the optional FlashAttention wheel.",
    )
    parser.add_argument(
        "--marker",
        required=True,
        help="App-relative readiness marker owned by this optional installer.",
    )
    args = parser.parse_args(argv)
    try:
        marker = _resolve_marker(args.marker, app_root=app_root or Path.cwd())
    except ValueError as exc:
        parser.error(str(exc))

    results: list[bool] = []
    if not args.flash_only:
        results.append(install_optional_wheel("SageAttention", SAGEATTENTION_WHEEL))
    results.append(install_optional_wheel("FlashAttention", FLASHATTENTION_WHEEL))
    _publish_marker(marker, ready=all(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
