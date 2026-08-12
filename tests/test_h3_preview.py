"""Model-free regressions for the native H3 preview adapter."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

import torch

_APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(_APP))

from services.h3_preview import (
    H3_PREVIEW_DECODER_IDENTITY,
    H3PreviewGeometryError,
    H3PreviewRequest,
    decode_h3_preview,
)


def _request(rows: torch.Tensor, **updates) -> H3PreviewRequest:
    values = {
        "enabled": True,
        "packed_rows": rows,
        "latent_frames": 2,
        "latent_height": 4,
        "latent_width": 6,
        "pixel_frames": 5,
        "pixel_height": 64,
        "pixel_width": 96,
    }
    values.update(updates)
    return H3PreviewRequest(**values)


def _rows(*, requires_grad: bool = False) -> torch.Tensor:
    return (
        torch.arange(12 * 96, dtype=torch.float32)
        .reshape(12, 96)
        .requires_grad_(requires_grad)
    )


class _Decoder:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.received = None

    def decode_h3_preview_rows(self, **kwargs) -> torch.Tensor:
        self.received = kwargs
        kwargs["packed_rows"].zero_()
        if self.failure is not None:
            raise self.failure
        return torch.zeros(1, 3, 5, 64, 96, dtype=torch.float16)


class TestH3PreviewAdapter(unittest.TestCase):
    def test_native_helper_unpatchifies_and_applies_official_normalization(self):
        from models.minimax_h3.minimax_h3_main import (
            VIDEO_LATENTS_MEAN,
            _decode_h3_video_rows,
        )
        from models.minimax_h3.packing import MINIMAX_H3_PIXEL_MEAN

        class VAE:
            received = None

            def decode(self, latents, *, return_dict):
                self.received = latents.detach().clone()
                self.return_dict = return_dict
                return (torch.zeros(1, 3, 5, 64, 96),)

        vae = VAE()
        video, normalized_latents = _decode_h3_video_rows(
            vae=vae,
            device=torch.device("cpu"),
            packed_rows=torch.zeros(12, 96),
            latent_frames=2,
            latent_height=4,
            latent_width=6,
            pixel_frames=5,
            pixel_height=64,
            pixel_width=96,
            channels=24,
            patch_size=(1, 2, 2),
        )

        self.assertEqual(tuple(normalized_latents.shape), (1, 24, 2, 4, 6))
        self.assertTrue(torch.count_nonzero(normalized_latents) == 0)
        self.assertFalse(vae.return_dict)
        self.assertEqual(tuple(vae.received.shape), (1, 24, 2, 4, 6))
        self.assertTrue(
            torch.equal(
                vae.received[:, :, 0, 0, 0],
                torch.tensor(VIDEO_LATENTS_MEAN).view(1, 24),
            )
        )
        expected_pixels = torch.tensor(MINIMAX_H3_PIXEL_MEAN).mul(2).sub(1)
        self.assertTrue(
            torch.allclose(video[0, :, 0, 0, 0], expected_pixels)
        )

    def test_valid_decode_is_detached_observational_and_geometry_exact(self):
        rows = _rows(requires_grad=True)
        before = rows.detach().clone()
        decoder = _Decoder()

        result = decode_h3_preview(_request(rows), decoder)

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.reason, "ready")
        self.assertEqual(result.decoder_identity, H3_PREVIEW_DECODER_IDENTITY)
        self.assertEqual(result.geometry.packed_rows, 12)
        self.assertEqual(result.geometry.packed_channels, 96)
        self.assertEqual(result.geometry.patch_size, (1, 2, 2))
        self.assertEqual(result.frames.dtype, torch.float16)
        self.assertFalse(result.frames.requires_grad)
        self.assertNotIn("tensor", repr(result))
        self.assertIsNot(decoder.received["packed_rows"], rows)
        self.assertFalse(decoder.received["packed_rows"].requires_grad)
        self.assertTrue(torch.equal(rows.detach(), before))

    def test_preview_cannot_change_input_or_production_final_bytes(self):
        rows = _rows()
        before = hashlib.sha256(rows.numpy().tobytes()).digest()
        decoder = _Decoder()

        result = decode_h3_preview(_request(rows), decoder)
        final_bytes = hashlib.sha256(rows.numpy().tobytes()).digest()

        self.assertEqual(result.status, "ready")
        self.assertEqual(final_bytes, before)

    def test_invalid_input_and_output_geometry_drop_safely(self):
        invalid_requests = (
            _request(_rows()[:-1]),
            _request(_rows(), latent_height=5),
            _request(_rows(), patch_size=(1, 0, 2)),
            _request(_rows(), channels=23),
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                result = decode_h3_preview(request, _Decoder())
                self.assertEqual(
                    (result.status, result.reason),
                    ("dropped", "invalid_geometry"),
                )
                self.assertIsNone(result.frames)

        decoder = _Decoder()
        decoder.decode_h3_preview_rows = lambda **_kwargs: torch.zeros(
            1, 3, 4, 64, 96
        )
        result = decode_h3_preview(_request(_rows()), decoder)
        self.assertEqual((result.status, result.reason), ("dropped", "invalid_geometry"))

    def test_real_native_helper_pixel_shape_mismatch_is_invalid_geometry(self):
        from models.minimax_h3.minimax_h3_main import _decode_h3_video_rows

        class WrongShapeVAE:
            def decode(self, _latents, *, return_dict):
                self.return_dict = return_dict
                return (torch.zeros(1, 3, 4, 64, 96),)

        class NativeHelperDecoder:
            def decode_h3_preview_rows(self, **kwargs):
                video, _latents = _decode_h3_video_rows(
                    vae=WrongShapeVAE(),
                    device=torch.device("cpu"),
                    **kwargs,
                )
                return video

        result = decode_h3_preview(_request(_rows()), NativeHelperDecoder())

        self.assertEqual(
            (result.status, result.reason),
            ("dropped", "invalid_geometry"),
        )

    def test_other_decoder_value_error_remains_decode_error(self):
        result = decode_h3_preview(
            _request(_rows()),
            _Decoder(failure=ValueError("not geometry")),
        )
        self.assertEqual(
            (result.status, result.reason),
            ("dropped", "decode_error"),
        )
        self.assertTrue(issubclass(H3PreviewGeometryError, ValueError))

    def test_unavailable_cancel_oom_and_decode_errors_are_preview_only_drops(self):
        cases = (
            (None, {}, "decoder_unavailable"),
            (_Decoder(), {"cancelled": True}, "cancelled"),
            (
                _Decoder(failure=torch.OutOfMemoryError("synthetic")),
                {},
                "out_of_memory",
            ),
            (_Decoder(failure=RuntimeError("synthetic")), {}, "decode_error"),
            (_Decoder(failure=InterruptedError("synthetic")), {}, "cancelled"),
        )
        for decoder, updates, reason in cases:
            with self.subTest(reason=reason):
                result = decode_h3_preview(_request(_rows(), **updates), decoder)
                self.assertEqual((result.status, result.reason), ("dropped", reason))
                self.assertIsNone(result.frames)

        class CloneOOMTensor(torch.Tensor):
            def clone(self, *args, **kwargs):
                raise torch.OutOfMemoryError("synthetic clone OOM")

        clone_oom_rows = _rows().as_subclass(CloneOOMTensor)
        result = decode_h3_preview(_request(clone_oom_rows), _Decoder())
        self.assertEqual(
            (result.status, result.reason),
            ("dropped", "out_of_memory"),
        )

    def test_opt_in_schema_and_mode_are_explicit(self):
        cases = (
            ({"enabled": False}, "not_requested"),
            ({"schema_version": 2}, "schema_unsupported"),
            ({"mode": "external"}, "mode_unsupported"),
        )
        for updates, reason in cases:
            with self.subTest(reason=reason):
                result = decode_h3_preview(_request(_rows(), **updates), _Decoder())
                self.assertEqual((result.status, result.reason), ("unsupported", reason))


if __name__ == "__main__":
    unittest.main()
