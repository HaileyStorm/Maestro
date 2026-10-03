"""Actual CPU central mux compatibility, atomic failure and WGP transfer checks."""

# Only reviewed repository AST is executed below.
# ruff: noqa: S102
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.h3_stream_video import H3VideoSink
from shared.utils.audio_video import (
    combine_and_concatenate_video_with_audio_tracks,
    write_wav_file,
)
from shared.utils.media_encoder import run_encoder


class H3StreamMuxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def encoded(self):
        sink = H3VideoSink(
            self.root,
            generated_frames=39,
            published_frames=38,
            height=32,
            width=32,
            codec_type="libx264_lossless",
            container="mp4",
            abort_check=lambda: False,
            timeout=30,
        )
        self.addCleanup(sink.cleanup)
        with sink:
            for count in (17, 17, 5):
                sink(torch.zeros(1, 3, count, 32, 32))
        return sink.receipt

    def mux(self, target, video, audio, **controls):
        combine_and_concatenate_video_with_audio_tracks(
            str(target),
            str(video),
            [],
            [str(audio)],
            0,
            32000,
            audio_codec_key="aac_128",
            output_audio_channels=2,
            **controls,
        )

    def decode(self, path, *options):
        return subprocess.check_output(
            ["ffmpeg", "-v", "error", "-threads", "1", "-i", str(path), *options, "-"],
            timeout=10,
        )

    def test_controlled_mux_preserves_actual_legacy_video_and_stereo_audio(self):
        receipt = self.encoded()
        video = self.root / "premux.mp4"
        receipt.transfer_to(video)
        samples = round(38 * 32000 / 24)
        clock = np.arange(samples, dtype=np.float32) / 32000
        audio = np.stack(
            (
                0.1 * np.sin(2 * np.pi * 220 * clock),
                0.1 * np.sin(2 * np.pi * 440 * clock),
            ),
            axis=1,
        )
        wave = self.root / "native.wav"
        write_wav_file(wave, audio, 32000)
        legacy, controlled = self.root / "legacy.mp4", self.root / "controlled.mp4"
        self.mux(legacy, video, wave)
        self.mux(controlled, video, wave, **receipt.mux_controls())
        probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-count_frames",
                    "-show_streams",
                    "-of",
                    "json",
                    str(controlled),
                ],
                timeout=10,
            )
        )["streams"]
        self.assertEqual(len(probe), 2)
        visual, sound = probe
        self.assertEqual(
            (
                visual["width"],
                visual["height"],
                visual["r_frame_rate"],
                int(visual["nb_read_frames"]),
            ),
            (32, 32, "24/1", 38),
        )
        self.assertEqual(
            (sound["codec_name"], sound["sample_rate"], sound["channels"]),
            ("aac", "32000", 2),
        )
        self.assertEqual(
            self.decode(
                controlled, "-map", "0:v", "-f", "rawvideo", "-pix_fmt", "rgb24"
            ),
            self.decode(video, "-map", "0:v", "-f", "rawvideo", "-pix_fmt", "rgb24"),
        )
        # AAC padding remains the established central policy. Compare the same
        # decoded legacy waveform, rather than asserting lossless source audio.
        for options in (
            ("-map", "0:v", "-f", "rawvideo", "-pix_fmt", "rgb24"),
            ("-map", "0:a", "-f", "f32le", "-acodec", "pcm_f32le"),
        ):
            self.assertEqual(
                self.decode(controlled, *options), self.decode(legacy, *options)
            )
        self.assertEqual(list(self.root.glob(".maestro-mux-*")), [])

    def test_failure_cancel_deadline_and_callback_error_preserve_prior_destination(
        self,
    ):
        target = self.root / "final.mp4"
        cases = ("failure", "cancel", "timeout", "callback")
        for case in cases:
            with self.subTest(case=case):
                target.write_bytes(b"prior accepted output")
                children = []
                original_popen = subprocess.Popen
                started = time.monotonic()

                def popen(
                    *args, original_popen=original_popen, children=children, **kwargs
                ):
                    child = original_popen(*args, **kwargs)
                    children.append(child)
                    return child

                def abort(started=started, case=case):
                    if time.monotonic() - started < 0.1:
                        return False
                    if case == "callback":
                        raise RuntimeError("callback failed")
                    return case == "cancel"

                def encoder(command, case=case, **controls):
                    if case == "failure":
                        Path(command[-1]).write_bytes(b"partial mux")
                        return 1
                    return run_encoder(
                        [sys.executable, "-c", "import time; time.sleep(10)"],
                        **controls,
                    )

                with (
                    patch(
                        "shared.utils.media_encoder.run_encoder", side_effect=encoder
                    ),
                    patch(
                        "shared.utils.media_encoder.subprocess.Popen", side_effect=popen
                    ),
                    self.assertRaises((RuntimeError, InterruptedError, TimeoutError)),
                ):
                    self.mux(
                        target,
                        self.root / "video.mp4",
                        self.root / "audio.wav",
                        abort_check=abort,
                        timeout=0.2 if case == "timeout" else 2,
                    )
                self.assertEqual(target.read_bytes(), b"prior accepted output")
                self.assertTrue(all(child.poll() is not None for child in children))
                self.assertFalse(
                    any(
                        t.name == "maestro-media-encoder-monitor"
                        for t in threading.enumerate()
                    )
                )
                self.assertEqual(list(self.root.glob(".maestro-mux-*")), [])

    def test_mux_temporary_creation_failure_preserves_prior_destination(self):
        target = self.root / "final.mp4"
        target.write_bytes(b"prior accepted output")
        with (
            patch(
                "shared.utils.audio_video.tempfile.mkstemp",
                side_effect=PermissionError("staging denied"),
            ),
            self.assertRaises(PermissionError),
        ):
            self.mux(
                target, self.root / "video.mp4", self.root / "audio.wav", timeout=2
            )
        self.assertEqual(target.read_bytes(), b"prior accepted output")
        self.assertEqual(list(self.root.glob(".maestro-mux-*")), [])

    def test_actual_wgp_transfer_registers_premux_before_waveform_write(self):
        receipt = self.encoded()
        tree = ast.parse((ROOT / "app/wgp.py").read_text())
        block = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and any(
                isinstance(x, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "h3_keep_premux"
                    for t in x.targets
                )
                for x in node.body
            )
        )
        # Execute the actual shared naming/transfer/registration prefix, stopping
        # immediately before the fallible audio write and mux try block.
        end = next(
            i
            for i, node in enumerate(block.body)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "output_new_audio_temp_filepath"
                for t in node.targets
            )
        )
        namespace = {
            "os": os,
            "output_dir": str(self.root),
            "durable_output_dir": str(self.root),
            "file_name": "segment.mp4",
            "container": "mp4",
            "durable_file_stem": "sealed-segment",
            "h3_encoded_video": receipt,
            "h3_audio_roles": None,
            "gen": {},
            "lock": threading.Lock(),
            "save_video": Mock(side_effect=AssertionError("must not reencode")),
        }
        module = ast.fix_missing_locations(
            ast.Module(body=block.body[: end + 1], type_ignores=[])
        )
        exec(compile(module, "wgp-stream-premux", "exec"), namespace)
        premux = str(self.root / "sealed-segment-premux-video.mp4")
        self.assertTrue(Path(premux).is_file())
        self.assertEqual(namespace["gen"]["artifact_list"], [premux])
        self.assertEqual(namespace["gen"]["artifact_roles"], {premux: "temporary"})
        self.assertEqual(namespace["save_path_tmp"], premux)
        self.assertEqual(namespace["h3_premux_paths"], [premux])
        namespace["save_video"].assert_not_called()
        self.assertEqual(list(self.root.glob(".h3-stream-*")), [])

    def test_actual_wgp_late_cancellation_removes_new_final_and_keeps_premux(self):
        tree = ast.parse((ROOT / "app/wgp.py").read_text())
        block = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and any(
                isinstance(x, ast.If)
                and ast.unparse(x.test) == "h3_encoded_video is not None"
                for x in node.body
            )
            and any(
                isinstance(x, ast.Call)
                and isinstance(x.func, ast.Name)
                and x.func.id == "enforce_h3_final_audio_safety"
                for x in ast.walk(node)
            )
        )
        final, premux = self.root / "new-final.mp4", self.root / "premux.mp4"
        premux.write_bytes(b"private recovery input")
        for failure in (InterruptedError("cancelled"), TimeoutError("deadline")):
            final.write_bytes(b"newly muxed unsealed output")
            receipt = Mock()
            receipt.mux_controls.side_effect = failure
            verify = Mock(return_value={"verified": True})
            namespace = {
                "h3_encoded_video": receipt,
                "h3_audio_safety_deferred_to_multiclip_final": False,
                "_retake_stitch_info": None,
                "video_path": str(final),
                "base_model_type": "minimax_h3",
                "send_cmd": None,
                "state": {},
                "enforce_h3_final_audio_safety": verify,
                "remove_failed_h3_final_output": lambda path: Path(path).unlink(),
                "PostDecodeStageError": type(
                    "PostDecodeStageError", (RuntimeError,), {}
                ),
            }
            module = ast.fix_missing_locations(
                ast.Module(body=[block], type_ignores=[])
            )
            with (
                self.subTest(failure=type(failure).__name__),
                self.assertRaises(type(failure)),
            ):
                exec(compile(module, "wgp-stream-finality", "exec"), namespace)
            verify.assert_called_once()
            self.assertFalse(final.exists())
            self.assertEqual(premux.read_bytes(), b"private recovery input")


if __name__ == "__main__":
    unittest.main()
