"""Build and install Maestro's pinned Python/Triton W4A8 runtime."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


REVISION = "b812819a97ac11d01f4a3a16ba47dd38de3b2519"


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def main() -> None:
    app_root = Path(__file__).resolve().parents[1]
    source = app_root / "services" / "comfy_kitchen_w4a8"
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True,
    ).strip()
    if actual != REVISION:
        raise RuntimeError(f"Unexpected comfy-kitchen W4A8 revision: {actual}")
    run(sys.executable, "setup.py", "bdist_wheel", "--no-cuda", cwd=source)
    wheel = source / "dist" / "comfy_kitchen-0.2.25-py3-none-any.whl"
    if not wheel.is_file():
        raise RuntimeError("Pinned comfy-kitchen W4A8 wheel was not created")
    run(
        sys.executable, "-m", "pip", "install", "--force-reinstall",
        "--no-deps", str(wheel), cwd=source,
    )
    import comfy_kitchen
    if not callable(getattr(comfy_kitchen, "w4a8_int8_linear", None)):
        raise RuntimeError("Installed comfy-kitchen lacks W4A8 support")
    run(sys.executable, str(app_root / "scripts" / "validate_h3_w4a8.py"), cwd=app_root)


if __name__ == "__main__":
    main()
