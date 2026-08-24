"""Runtime capability checks for optional attention extensions."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import types
import unittest
from unittest import mock


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)


class TestFlashAttentionKernelProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from shared import attention
        except Exception as exc:
            raise unittest.SkipTest(
                f"shared attention dependencies unavailable: {exc}"
            ) from exc
        cls.attention = attention

    def setUp(self):
        self.saved_flash = self.attention.flash_attn
        self.saved_verdict = self.attention._flash_attn_kernels

    def tearDown(self):
        self.attention.flash_attn = self.saved_flash
        self.attention._flash_attn_kernels = self.saved_verdict

    def _probe_with(self, implementation):
        self.attention.flash_attn = types.SimpleNamespace(
            flash_attn_varlen_func=implementation,
        )
        self.attention._flash_attn_kernels = None
        sentinel = object()
        with (
            mock.patch.object(
                self.attention.torch.cuda,
                "is_available",
                return_value=True,
            ),
            mock.patch.object(
                self.attention.torch.cuda,
                "synchronize",
            ),
            mock.patch.object(
                self.attention.torch,
                "zeros",
                return_value=sentinel,
            ),
            mock.patch.object(
                self.attention.torch,
                "tensor",
                return_value=sentinel,
            ),
        ):
            return self.attention.flash_attn_kernels_available()

    def test_successful_kernel_probe_is_supported(self):
        implementation = mock.Mock(return_value=object())
        self.assertTrue(self._probe_with(implementation))
        implementation.assert_called_once()

    def test_failed_kernel_probe_is_rejected_and_cached(self):
        implementation = mock.Mock(
            side_effect=RuntimeError("no kernel image is available"),
        )
        self.assertFalse(self._probe_with(implementation))
        self.assertFalse(self.attention.flash_attn_kernels_available())
        implementation.assert_called_once()

    def test_missing_cuda_never_attempts_a_probe(self):
        implementation = mock.Mock()
        self.attention.flash_attn = types.SimpleNamespace(
            flash_attn_varlen_func=implementation,
        )
        self.attention._flash_attn_kernels = None
        with mock.patch.object(
            self.attention.torch.cuda,
            "is_available",
            return_value=False,
        ):
            self.assertFalse(self.attention.flash_attn_kernels_available())
        implementation.assert_not_called()


class TestCPUHiddenAttentionImport(unittest.TestCase):
    def test_hidden_cuda_import_reports_only_cpu_safe_attention(self):
        script = """
import json
import torch
from shared import attention

print(json.dumps({
    "cuda_available": torch.cuda.is_available(),
    "bfloat16_supported": attention.bfloat16_supported,
    "supported_modes": attention.get_supported_attention_modes(),
    "default_mode": attention.get_default_attention_mode(),
    "sol_status": attention.get_sol_attention_status(),
}))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=_ROOT,
            env={
                **os.environ,
                "CUDA_VISIBLE_DEVICES": "",
                "PYTHONPATH": _APP,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])

        self.assertFalse(payload["cuda_available"])
        self.assertFalse(payload["bfloat16_supported"])
        self.assertEqual(payload["supported_modes"], ["sdpa", "auto"])
        self.assertEqual(payload["default_mode"], "sdpa")
        self.assertFalse(payload["sol_status"]["supported"])
        self.assertIsNone(payload["sol_status"]["capability"])
        self.assertIn("visible CUDA GPU", payload["sol_status"]["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
