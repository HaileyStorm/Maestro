"""Add safe Maestro defaults to Pinokio's per-app ENVIRONMENT file."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


def ensure_default(path: Path, key: str, value: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    for raw_line in existing.splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and line.partition("=")[0].strip() == key:
            return False
    suffix = "" if not existing or existing.endswith("\n") else "\n"
    updated = f"{existing}{suffix}\n{key}={value}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="ENVIRONMENT")
    args = parser.parse_args()
    path = Path(args.file)
    ensure_default(path, "PINOKIO_SHARE_CLOUDFLARE", "true")
    ensure_default(path, "MAESTRO_ACCOUNTS_ENABLED", "false")
    ensure_default(path, "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED", "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
