"""CPU-only checks that film grain never publishes an incomplete video."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Callable
import unittest
from unittest import mock


class _Frames:
    def permute(self, *_args):
        return self

    def to(self, _device):
        return self

    def cpu(self):
        return self

    def unsqueeze(self, _axis):
        return self


class _Reader:
    def __init__(self, _path):
        pass

    def __len__(self):
        return 2

    def get_avg_fps(self):
        return 24

    def get_batch(self, _indices):
        return _Frames()


class _Torch:
    class OutOfMemoryError(Exception):
        pass

    cuda = SimpleNamespace(is_available=lambda: False)

    @staticmethod
    def device(value):
        return value


class FilmGrainTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_apply_film_grain_to_file_impl"
        )
        namespace = {
            "Callable": Callable,
            "os": os,
            "time": __import__("time"),
            "wgp": SimpleNamespace(server_config={
                "video_output_codec": "libx264_8",
                "video_container": "mp4",
            }),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        cls.process = staticmethod(namespace[node.name])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.video = Path(self.temporary.name) / "output.mp4"
        self.video.write_bytes(b"original")

    def _run(self, save_video, *, cancel_check=None):
        return self.process(
            str(self.video), 0.2, 0.1,
            torch=_Torch,
            decord=SimpleNamespace(VideoReader=_Reader),
            add_film_grain=lambda frames, _intensity, _saturation: frames,
            save_video=save_video,
            cancel_check=cancel_check,
        )

    def _assert_no_intermediate_files(self):
        self.assertEqual([path.name for path in self.video.parent.iterdir()], ["output.mp4"])

    def test_no_audio_replaces_only_after_complete_encode(self):
        def encode(**kwargs):
            Path(kwargs["save_file"]).write_bytes(b"grained")

        with mock.patch.object(
            subprocess, "run",
            return_value=SimpleNamespace(returncode=0, stdout=""),
        ):
            self._run(encode)
        self.assertEqual(self.video.read_bytes(), b"grained")
        self._assert_no_intermediate_files()

    def test_cancel_after_encode_keeps_original_and_cleans_staging(self):
        cancelled = False

        def encode(**kwargs):
            nonlocal cancelled
            Path(kwargs["save_file"]).write_bytes(b"grained")
            cancelled = True

        with mock.patch.object(
            subprocess, "run",
            return_value=SimpleNamespace(returncode=0, stdout=""),
        ):
            with self.assertRaises(InterruptedError):
                self._run(encode, cancel_check=lambda: cancelled)
        self.assertEqual(self.video.read_bytes(), b"original")
        self._assert_no_intermediate_files()

    def test_audio_probe_failure_does_not_publish_silent_video(self):
        encode = mock.Mock()
        with mock.patch.object(
            subprocess, "run",
            return_value=SimpleNamespace(returncode=1, stdout=""),
        ):
            with self.assertRaisesRegex(RuntimeError, "audio probe failed"):
                self._run(encode)
        encode.assert_not_called()
        self.assertEqual(self.video.read_bytes(), b"original")
        self._assert_no_intermediate_files()

    def test_audio_remux_success_replaces_video_with_muxed_output(self):
        audio_path = None

        def run_ffmpeg(args, **_kwargs):
            nonlocal audio_path
            if args[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout="audio")
            if "-vn" in args:
                audio_path = Path(args[-1])
                audio_path.write_bytes(b"audio")
            else:
                Path(args[-1]).write_bytes(b"grained-with-audio")
            return SimpleNamespace(returncode=0)

        def encode(**kwargs):
            Path(kwargs["save_file"]).write_bytes(b"grained")

        with mock.patch.object(subprocess, "run", side_effect=run_ffmpeg):
            self._run(encode)
        self.assertEqual(self.video.read_bytes(), b"grained-with-audio")
        self.assertIsNotNone(audio_path)
        self.assertFalse(audio_path.exists())
        self._assert_no_intermediate_files()

    def test_audio_mux_failure_keeps_original_and_cleans_staging(self):
        audio_path = None

        def run_ffmpeg(args, **_kwargs):
            nonlocal audio_path
            if args[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout="audio")
            if "-vn" in args:
                audio_path = Path(args[-1])
                audio_path.write_bytes(b"audio")
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=1)

        def encode(**kwargs):
            Path(kwargs["save_file"]).write_bytes(b"grained")

        with mock.patch.object(subprocess, "run", side_effect=run_ffmpeg):
            with self.assertRaisesRegex(RuntimeError, "audio remux failed"):
                self._run(encode)
        self.assertEqual(self.video.read_bytes(), b"original")
        self.assertIsNotNone(audio_path)
        self.assertFalse(audio_path.exists())
        self._assert_no_intermediate_files()


if __name__ == "__main__":
    unittest.main()
