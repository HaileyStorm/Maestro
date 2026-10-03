"""CPU/native-recipe encoding checks; no learned VAE, GPU or publication."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from models.minimax_h3.packing import MINIMAX_H3_PIXEL_MEAN, MINIMAX_H3_PIXEL_STD
from services.h3_stream_video import H3VideoSink
from shared.utils.media_encoder import run_encoder
from test_h3_vae_temporal_sink import make_vae


class H3StreamVideoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.vae = make_vae()

    def sink(self, **overrides):
        settings = {
            "generated_frames": 39, "published_frames": 39, "height": 32, "width": 32,
            "codec_type": "libx264_lossless", "container": "mkv", "abort_check": lambda: False, "timeout": 30,
        }
        settings.update(overrides)
        sink = H3VideoSink(self.root, **settings)
        self.addCleanup(sink.cleanup)
        return sink

    def latents(self, length=12):
        return torch.linspace(-2, 2, length).reshape(1, 1, length, 1, 1).expand(1, 3, length, 32, 32).clone()

    def test_lossless_native_pixels_and_trim_across_three_publication_clocks(self):
        z = self.latents(142)
        raw = self.vae.decode(z, return_dict=False)[0]
        mean = torch.tensor(MINIMAX_H3_PIXEL_MEAN).view(1, 3, 1, 1, 1)
        std = torch.tensor(MINIMAX_H3_PIXEL_STD).view(1, 3, 1, 1, 1)
        eager = (raw.float() * std + mean).clamp(0, 1).mul(2).sub(1)
        eager = eager.clamp_(-1, 1).sub_(-1).mul_(127.5).to(torch.uint8)
        for published in (479, 480, 481):
            with self.subTest(published=published):
                sink = self.sink(generated_frames=481, published_frames=published)
                with sink:
                    count = self.vae.decode_to_sink(z, sink)
                receipt = sink.receipt
                self.assertEqual((count, receipt.generated_frames, receipt.published_frames), (481, 481, published))
                self.assertEqual((receipt.height, receipt.width), (32, 32))
                receipt.verify()
                target = self.root / f"synthetic-{published}.mkv"
                receipt.transfer_to(target)
                actual = subprocess.check_output(
                    ["ffmpeg", "-v", "error", "-threads", "2", "-i", str(target),
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=10,
                )
                expected = eager[0, :, :published].permute(1, 2, 3, 0).numpy().tobytes()
                self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest())
                self.assertEqual(len(actual), published * 32 * 32 * 3)
                with self.assertRaises(ValueError):
                    receipt.transfer_to(self.root / "reused.mkv")
                self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_receipt_cannot_be_fabricated_serialized_or_redirected(self):
        sink = self.sink()
        with sink:
            self.vae.decode_to_sink(self.latents(), sink)
        receipt = sink.receipt
        with self.assertRaises(ValueError):
            replace(receipt, published_frames=40).verify()
        for value in (receipt, sink):
            with self.assertRaises(TypeError):
                pickle.dumps(value)
        for path in (self.root.parent / "escaped.mkv", self.root / "wrong.mp4"):
            with self.assertRaises(ValueError):
                receipt.transfer_to(path)
        receipt.verify()

    def test_configured_probe_outside_path_is_used_by_actual_encoder_verification(self):
        configured = self.root / "configured-probe"
        configured.symlink_to(shutil.which("ffprobe"))
        sink = self.sink()
        with (
            patch.dict(os.environ, {"PATH": "", "FFPROBE_BINARY": str(configured)}),
            patch("services.h3_stream_video.run_encoder", wraps=run_encoder) as probe,
            sink,
        ):
            self.vae.decode_to_sink(self.latents(), sink)
        self.assertEqual(probe.call_args.args[0][0], str(configured))
        sink.receipt.verify()

    def test_mutated_verified_file_is_rejected_without_replacing_destination(self):
        target = self.root / "preserved.mkv"
        target.write_bytes(b"old output")
        sink = self.sink()
        with sink:
            self.vae.decode_to_sink(self.latents(), sink)
        receipt = sink.receipt
        with sink._path.open("r+b") as file:
            file.write(b"broken")
        with self.assertRaisesRegex(ValueError, "changed"):
            receipt.transfer_to(target)
        self.assertEqual(target.read_bytes(), b"old output")
        sink.cleanup()
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_producer_encoder_and_incomplete_tail_failures_discard_partial_video(self):
        for failure in ("producer", "encoder", "tail"):
            with self.subTest(failure=failure):
                sink = self.sink()
                with self.assertRaises((RuntimeError, ValueError)), sink:
                    if failure == "encoder":
                        with patch.object(sink._writer, "append_data", side_effect=RuntimeError("encoder failed")):
                            sink(torch.zeros(1, 3, 17, 32, 32))
                    else:
                        sink(torch.zeros(1, 3, 17, 32, 32))
                        if failure == "producer":
                            raise RuntimeError("VAE failed")
                with self.assertRaises(ValueError):
                    _ = sink.receipt
                self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_probe_refuses_changed_dimensions_clock_frame_count_or_audio(self):
        for field, value in (
            ("width", 64), ("r_frame_rate", "25/1"), ("nb_read_frames", "38"), ("audio", True),
        ):
            def probe(command, *, stdout, field=field, value=value, **kwargs):
                video = {"codec_type": "video", "width": 32, "height": 32, "r_frame_rate": "24/1", "avg_frame_rate": "24/1", "nb_read_frames": "39"}
                streams = [video]
                if field == "audio":
                    streams.append({"codec_type": "audio"})
                else:
                    video[field] = value
                stdout.write(json.dumps({"streams": streams}).encode())
                return 0

            with self.subTest(field=field), patch("services.h3_stream_video.run_encoder", side_effect=probe):
                sink = self.sink()
                with self.assertRaisesRegex(ValueError, "geometry or clock"), sink:
                    self.vae.decode_to_sink(self.latents(), sink)
                self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_cancel_and_deadline_after_first_chunk_stop_encoder_and_remove_staging(self):
        for mode in ("cancel", "deadline"):
            state = {"cancel": False}
            sink = self.sink(abort_check=lambda state=state: state["cancel"])
            error = InterruptedError if mode == "cancel" else TimeoutError
            with self.subTest(mode=mode), self.assertRaises(error), sink:
                sink(torch.zeros(1, 3, 17, 32, 32))
                encoder = sink._writer.encoder
                if mode == "cancel":
                    state["cancel"] = True
                else:
                    sink._deadline = time.monotonic() - 1
                sink(torch.zeros(1, 3, 22, 32, 32))
            self.assertEqual(list(self.root.glob(".h3-stream-*")), [])
            self.assertIsNotNone(encoder._process.poll())
            self.assertFalse(encoder._monitor_thread.is_alive())

    def test_encoder_finish_failure_reaps_child_and_keeps_old_destination(self):
        target = self.root / "preserved.mkv"
        target.write_bytes(b"old output")
        sink = self.sink()
        with self.assertRaisesRegex(RuntimeError, "finish failed"), sink:
            self.vae.decode_to_sink(self.latents(), sink)
            encoder = sink._writer.encoder
            with patch.object(encoder, "finish", side_effect=RuntimeError("finish failed")):
                sink._writer.__exit__(None, None, None)
        self.assertIsNotNone(encoder._process.poll())
        self.assertFalse(encoder._monitor_thread.is_alive())
        self.assertEqual(target.read_bytes(), b"old output")
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_cancellation_during_entry_cleans_temporary_before_context_exists(self):
        original_temporary = tempfile.TemporaryDirectory

        def temporary(*args, **kwargs):
            result = original_temporary(*args, **kwargs)
            state["cancel"] = True
            return result

        state = {"cancel": False}
        sink = self.sink(abort_check=lambda: state["cancel"])
        with (
            patch("services.h3_stream_video.tempfile.TemporaryDirectory", side_effect=temporary),
            self.assertRaises(InterruptedError),
        ):
            sink.__enter__()
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_invalid_geometry_full_tensor_and_symlink_root_are_rejected(self):
        for settings in (
            {"height": 18}, {"generated_frames": True}, {"generated_frames": 40},
            {"published_frames": 40}, {"published_frames": 1}, {"timeout": True},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.sink(**settings)
        for raw in (
            torch.zeros(1, 3, 39, 32, 32), torch.zeros(1, 3, 17, 64, 32),
            torch.zeros(2, 3, 17, 32, 32), torch.zeros(1, 3, 17, 32, 32, dtype=torch.uint8),
        ):
            sink = self.sink()
            with self.subTest(shape=raw.shape), self.assertRaises(ValueError), sink:
                sink(raw)
            self.assertEqual(list(self.root.glob(".h3-stream-*")), [])
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            H3VideoSink(link, generated_frames=39, published_frames=39, height=32, width=32,
                        codec_type=None, container="mkv", abort_check=lambda: False, timeout=30)


if __name__ == "__main__":
    unittest.main()
