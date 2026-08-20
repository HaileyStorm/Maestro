"""Continuum cached llama.cpp runtime probes.

Locks leftover 1.9.0 receipt / `_positive_llama_build` / `_llama_server_build`
helpers onto Continuum `_llama_server_probe` plus `_ensure_llama_server`.
Do not invent leftover receipts just to keep a build-zero install.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import llm_service


_LEFTOVER_HELPERS = (
    "_positive_llama_build",
    "_llama_release_build",
    "_llama_runtime_receipt_path",
    "_write_llama_runtime_receipt",
    "_read_llama_runtime_receipt",
    "_llama_server_build",
    "_WINDOWS_LLAMA_CUDA_FILES",
)


def _probe_output(text: str, returncode: int = 0):
    return SimpleNamespace(stdout=text, stderr="", returncode=returncode)


class TestLlamaBuildMetadata(unittest.TestCase):
    def test_continuum_has_no_leftover_receipt_helpers(self):
        for name in _LEFTOVER_HELPERS:
            with self.subTest(name=name):
                self.assertFalse(hasattr(llm_service, name))
        self.assertTrue(hasattr(llm_service, "_llama_server_probe"))
        self.assertTrue(hasattr(llm_service, "_ensure_llama_server"))
        self.assertGreaterEqual(llm_service.MIN_LLAMA_BUILD, 1)

    def test_probe_parses_decimal_version_build(self):
        with mock.patch(
            "subprocess.run",
            return_value=_probe_output("version: 10488 (012345678)\nbuilt with Clang"),
        ):
            build, runnable = llm_service._llama_server_probe("/tmp/llama-server")
        self.assertEqual(build, 10488)
        self.assertTrue(runnable)

    def test_prefixed_release_tag_is_unparseable_on_continuum(self):
        # Leftover `_llama_release_build("b10488")` was never restored.
        # Continuum only captures `version: <digits>`.
        with mock.patch(
            "subprocess.run",
            return_value=_probe_output("version: b10488"),
        ):
            build, runnable = llm_service._llama_server_probe("/tmp/llama-server")
        self.assertIsNone(build)
        self.assertTrue(runnable)

    def test_zero_build_is_numeric_not_unknown(self):
        # Leftover `_positive_llama_build` treated 0 as unknown. Continuum
        # returns the parsed 0 and lets `_ensure_llama_server` compare it.
        with mock.patch(
            "subprocess.run",
            return_value=_probe_output("version: 0 (unknown)"),
        ):
            build, runnable = llm_service._llama_server_probe("/tmp/llama-server")
        self.assertEqual(build, 0)
        self.assertTrue(runnable)


class TestLlamaRuntimeIdempotency(unittest.TestCase):
    @staticmethod
    def _write_complete_runtime(directory: str) -> str:
        executable = (
            "llama-server.exe" if sys.platform.startswith("win") else "llama-server"
        )
        path = os.path.join(directory, executable)
        with open(path, "wb") as handle:
            handle.write(b"installed")
        return path

    def test_unreadable_runnable_build_does_not_redownload(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_complete_runtime(directory)
            capabilities = {
                "build": None,
                "runnable": True,
                "backend": "cpu",
                "devices": [],
            }
            with (
                mock.patch.object(
                    llm_service,
                    "_llama_server_probe",
                    return_value=(None, True),
                ),
                mock.patch.object(
                    llm_service,
                    "_llama_server_capabilities",
                    return_value=capabilities,
                ),
                mock.patch(
                    "urllib.request.urlopen",
                    side_effect=AssertionError("network should not be used"),
                ),
            ):
                result = llm_service._ensure_llama_server(directory)

        self.assertEqual(result, capabilities)

    def test_zero_build_is_below_minimum_and_does_not_use_leftover_receipts(self):
        source_path = os.path.join(_APP, "services", "llm_service.py")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("_write_llama_runtime_receipt", source)
        self.assertNotIn("_positive_llama_build", source)
        self.assertIn(
            "if runnable and (build is None or build >= MIN_LLAMA_BUILD):",
            source,
        )
        self.assertLess(0, llm_service.MIN_LLAMA_BUILD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
