"""Model-free tests for holding SERVER_PORT before WanGP import."""
from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "app"))

from services.server_port_hold import (  # noqa: E402
    ServerPortHoldError,
    acquire_configured_server_port,
)

_LAUNCH_PY = _ROOT / "app" / "launch.py"


class ServerPortHoldTests(unittest.TestCase):
    def tearDown(self) -> None:
        held = getattr(self, "_held", None)
        if held is not None:
            held.sock.close()

    def test_launch_py_holds_the_port_before_importing_torch(self) -> None:
        source = _LAUNCH_PY.read_text(encoding="utf-8")
        hold_at = source.find("acquire_configured_server_port")
        torch_at = source.find("import torch")
        self.assertGreater(hold_at, 0)
        self.assertGreater(torch_at, hold_at)
        self.assertIn('if __name__ == "__main__":', source[:torch_at])

    def test_hold_uses_a_plain_bind_and_blocks_a_second_listener(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        preferred = probe.getsockname()[1]
        probe.close()
        self._held = acquire_configured_server_port(
            environ={
                "SERVER_PORT": str(preferred),
                "PINOKIO_SHARE_LOCAL": "false",
            }
        )
        self.assertEqual(self._held.host, "127.0.0.1")
        self.assertEqual(self._held.sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
        rival = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(rival.close)
        with self.assertRaises(OSError):
            rival.bind((self._held.host, self._held.port))

    def test_strict_mode_refuses_to_relocate(self) -> None:
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(blocker.close)
        blocker.bind(("127.0.0.1", 0))
        preferred = blocker.getsockname()[1]
        with self.assertRaises(ServerPortHoldError) as raised:
            acquire_configured_server_port(
                environ={
                    "SERVER_PORT": str(preferred),
                    "MAESTRO_STRICT_SERVER_PORT": "true",
                    "PINOKIO_SHARE_LOCAL": "false",
                }
            )
        self.assertIn("refusing to move the stable-share backend", raised.exception.message)
        self.assertNotIn("http://", raised.exception.message)

    def test_non_strict_mode_relocates_to_the_next_free_port(self) -> None:
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(blocker.close)
        blocker.bind(("127.0.0.1", 0))
        preferred = blocker.getsockname()[1]
        self._held = acquire_configured_server_port(
            environ={
                "SERVER_PORT": str(preferred),
                "MAESTRO_STRICT_SERVER_PORT": "false",
                "PINOKIO_SHARE_LOCAL": "false",
            }
        )
        self.assertTrue(self._held.relocated)
        self.assertNotEqual(self._held.port, preferred)


if __name__ == "__main__":
    unittest.main()
