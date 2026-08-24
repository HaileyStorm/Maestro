"""CPU-hidden import regression coverage for the main WGP module."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"


class TestWgpCpuHiddenImport(unittest.TestCase):
    def test_import_does_not_initialize_hidden_cuda(self):
        script = """
import torch
import wgp

assert not torch.cuda.is_initialized()
assert (wgp.gpu_major, wgp.gpu_minor) == (0, 0)
assert wgp.bfloat16_supported is False
assert wgp.processing_device == "cuda"
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=_APP,
            env={
                **os.environ,
                "CUDA_VISIBLE_DEVICES": "",
                "PYTHONPATH": str(_APP),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
