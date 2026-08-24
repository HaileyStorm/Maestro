"""Import-safety regression coverage for the Wan T5 encoder wrapper."""

from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))


class TestT5ImportSafety(unittest.TestCase):
    def test_hidden_cuda_import_does_not_initialize_cuda(self):
        script = """
import inspect
import torch
from models.wan.modules.t5 import T5EncoderModel

assert inspect.signature(T5EncoderModel).parameters["device"].default is None
assert not torch.cuda.is_initialized()
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=_ROOT,
            env={
                **os.environ,
                "CUDA_VISIBLE_DEVICES": "",
                "PYTHONPATH": str(_APP),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_default_cuda_device_is_resolved_in_constructor(self):
        from models.wan.modules import t5

        class EmptyModel:
            def eval(self):
                return self

            def requires_grad_(self, _enabled):
                return self

            def to(self, device):
                self.device = device
                return self

        model = EmptyModel()
        offload = types.SimpleNamespace(load_model_data=mock.Mock())
        fake_accelerate = types.SimpleNamespace(
            init_empty_weights=lambda: nullcontext(),
        )
        fake_mmgp = types.SimpleNamespace(offload=offload)

        with (
            mock.patch.object(t5.torch.cuda, "current_device", return_value=7) as current_device,
            mock.patch.object(t5, "umt5_xxl", return_value=model) as make_model,
            mock.patch.object(t5, "HuggingfaceTokenizer", return_value=object()),
            mock.patch.dict(
                sys.modules,
                {"accelerate": fake_accelerate, "mmgp": fake_mmgp},
            ),
        ):
            encoder = t5.T5EncoderModel(text_len=512)

        current_device.assert_called_once_with()
        make_model.assert_called_once_with(
            encoder_only=True,
            return_tokenizer=False,
            dtype=t5.torch.bfloat16,
            device=7,
        )
        self.assertEqual(encoder.device, 7)
        self.assertEqual(model.device, 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
