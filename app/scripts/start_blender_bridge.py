"""Start Maestro's hosted, loopback-only Blender MCP bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket


def _ready() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 9876), timeout=0.25):
            return True
    except OSError:
        return False


def main() -> int:
    if _ready():
        print("Blender bridge already ready at 127.0.0.1:9876", flush=True)
        return 0
    marker = Path(__file__).resolve().parents[1] / "tools" / "blender" / "runtime.json"
    if not marker.is_file():
        print("Blender runtime is not installed; continuing without the optional bridge", flush=True)
        return 0
    config = json.loads(marker.read_text(encoding="utf-8"))
    binary = Path(str(config["binary"])).resolve()
    user_home = Path(str(config["user_home"])).resolve()
    if not binary.is_file():
        raise RuntimeError("Portable Blender runtime marker points to a missing executable")
    env = os.environ.copy()
    if os.name == "nt":
        env.update({
            "USERPROFILE": str(user_home),
            "APPDATA": str(user_home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(user_home / "AppData" / "Local"),
        })
    else:
        env.update({
            "HOME": str(user_home),
            "XDG_CONFIG_HOME": str(user_home / ".config"),
            "XDG_CACHE_HOME": str(user_home / ".cache"),
        })
    argv = [
        str(binary), "--online-mode", "--background",
        "--command", "blender_mcp", "--host", "127.0.0.1", "--port", "9876",
    ]
    os.execvpe(str(binary), argv, env)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
