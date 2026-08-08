"""Verify and install Maestro's pinned portable Blender + official MCP extension."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile


def _contained(root: Path, member: str) -> bool:
    target = (root / member).resolve()
    return target == root or root in target.parents


def _extract(archive: Path, target: Path) -> None:
    if archive.name.endswith(".tar.xz"):
        with tarfile.open(archive, "r:xz") as handle:
            if any(not _contained(target, member.name) for member in handle.getmembers()):
                raise RuntimeError("Blender archive contains an unsafe path")
            handle.extractall(target)
        return
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as handle:
            if any(not _contained(target, member) for member in handle.namelist()):
                raise RuntimeError("Blender archive contains an unsafe path")
            handle.extractall(target)
        return
    raise RuntimeError(f"Unsupported Blender archive: {archive.name}")


def _runtime_environment(user_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    user_home.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        env["USERPROFILE"] = str(user_home)
        env["APPDATA"] = str(user_home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(user_home / "AppData" / "Local")
    else:
        env["HOME"] = str(user_home)
        env["XDG_CONFIG_HOME"] = str(user_home / ".config")
        env["XDG_CACHE_HOME"] = str(user_home / ".cache")
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--version", default="5.1.2")
    args = parser.parse_args()

    archive = Path(args.archive).resolve()
    checkout = Path(args.checkout).resolve()
    tools_root = Path(__file__).resolve().parents[1] / "tools" / "blender"
    runtime = tools_root / "runtime"
    marker = tools_root / "runtime.json"
    if not archive.is_file():
        raise FileNotFoundError(archive)
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != args.sha256.lower():
        raise RuntimeError("Portable Blender SHA-256 did not match the pinned release")
    if not (checkout / "addon" / "blender_mcp_addon" / "blender_manifest.toml").is_file():
        raise RuntimeError("Pinned Blender MCP checkout is incomplete")

    staging = tools_root / ".runtime-staging"
    for path in (staging, runtime):
        if path.exists():
            shutil.rmtree(path)
    staging.mkdir(parents=True, exist_ok=False)
    _extract(archive, staging)
    candidates = list(staging.rglob("blender.exe" if os.name == "nt" else "blender"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise RuntimeError("Portable Blender executable was not found after extraction")
    extracted_root = candidates[0].parent
    shutil.move(str(extracted_root), runtime)
    shutil.rmtree(staging, ignore_errors=True)
    blender = runtime / ("blender.exe" if os.name == "nt" else "blender")
    version = subprocess.run(
        [str(blender), "--version"], capture_output=True, text=True, timeout=60, check=True,
    ).stdout.splitlines()[0]
    if f"Blender {args.version}" not in version:
        raise RuntimeError(f"Unexpected portable Blender version: {version}")

    extension_dir = tools_root / "extension"
    extension_dir.mkdir(parents=True, exist_ok=True)
    env = _runtime_environment(tools_root / "home")
    subprocess.run(
        [
            str(blender), "--command", "extension", "build",
            "--source-dir=" + str(checkout / "addon" / "blender_mcp_addon"),
            "--output-dir=" + str(extension_dir),
        ],
        env=env, timeout=300, check=True,
    )
    extension_zips = sorted(extension_dir.glob("mcp-*.zip"))
    if not extension_zips:
        raise RuntimeError("Blender MCP extension build produced no archive")
    subprocess.run(
        [
            str(blender), "--online-mode", "--background", "--factory-startup",
            "--command", "extension", "install-file", str(extension_zips[-1]),
            "--repo", "user_default", "--enable",
        ],
        env=env, timeout=300, check=True,
    )
    marker.write_text(json.dumps({
        "version": args.version,
        "binary": str(blender),
        "user_home": str(tools_root / "home"),
        "archive_sha256": digest.hexdigest(),
        "mcp_revision": "03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4",
    }, indent=2), encoding="utf-8")
    print(f"Installed {version} with the pinned Blender MCP extension")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
