"""CPU-only checks that film grain never publishes an incomplete video."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
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
        def run_ffmpeg(args, **_kwargs):
            if args[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout="audio")
            Path(args[-1]).write_bytes(b"grained-with-audio")
            return SimpleNamespace(returncode=0)

        def encode(**kwargs):
            Path(kwargs["save_file"]).write_bytes(b"grained")

        with mock.patch.object(subprocess, "run", side_effect=run_ffmpeg):
            self._run(encode)
        self.assertEqual(self.video.read_bytes(), b"grained-with-audio")
        self._assert_no_intermediate_files()

    def test_audio_mux_failure_keeps_original_and_cleans_staging(self):
        def run_ffmpeg(args, **_kwargs):
            if args[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout="audio")
            Path(args[-1]).write_bytes(b"incomplete mux")
            return SimpleNamespace(returncode=1)

        def encode(**kwargs):
            Path(kwargs["save_file"]).write_bytes(b"grained")

        with mock.patch.object(subprocess, "run", side_effect=run_ffmpeg):
            with self.assertRaisesRegex(RuntimeError, "audio remux failed"):
                self._run(encode)
        self.assertEqual(self.video.read_bytes(), b"original")
        self._assert_no_intermediate_files()

    def test_cancel_after_remux_keeps_original_and_cleans_staging(self):
        cancelled = False
        mux_completed = False

        def run_ffmpeg(args, **_kwargs):
            nonlocal cancelled, mux_completed
            if args[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout="audio")
            Path(args[-1]).write_bytes(b"grained-with-audio")
            if ".muxed." in args[-1]:
                mux_completed = cancelled = True
            return SimpleNamespace(returncode=0)

        def encode(**kwargs):
            Path(kwargs["save_file"]).write_bytes(b"grained")

        with mock.patch.object(subprocess, "run", side_effect=run_ffmpeg):
            with self.assertRaises(InterruptedError):
                self._run(encode, cancel_check=lambda: cancelled)
        self.assertTrue(mux_completed)
        self.assertEqual(self.video.read_bytes(), b"original")
        self._assert_no_intermediate_files()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU ffmpeg/ffprobe required")
    def test_native_remux_preserves_every_audio_stream_and_exact_timeline(self):
        def native(args):
            return subprocess.run(args, capture_output=True, check=True, timeout=15).stdout

        native([
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-filter_threads", "1",
            "-filter_complex_threads", "1", "-f", "lavfi", "-i",
            "testsrc2=size=192x128:rate=24:duration=1", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=1", "-f", "lavfi", "-i",
            "sine=frequency=880:sample_rate=48000:duration=1",
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "aac", "-b:a", "64k",
            "-t", "1", str(self.video),
        ])
        original = self.video.parent / "protected-source.mp4"
        shutil.copyfile(self.video, original)
        original.chmod(0o400)
        self.addCleanup(original.chmod, 0o600)
        protected_bytes = original.read_bytes()

        def streams(path):
            return json.loads(native([
                "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path),
            ]))["streams"]

        def packets(path, stream):
            payload = json.loads(native([
                "ffprobe", "-v", "error", "-select_streams", stream,
                "-show_packets", "-show_data_hash", "sha256", "-of", "json", str(path),
            ]))
            fields = ("pts", "dts", "duration", "size", "flags", "data_hash", "side_data_list")
            return [{key: packet[key] for key in fields if key in packet}
                    for packet in payload["packets"]]

        def pcm(path, stream):
            return native([
                "ffmpeg", "-nostdin", "-v", "error", "-threads", "2", "-i", str(path),
                "-map", stream, "-vn", "-acodec", "pcm_f32le", "-f", "f32le", "-",
            ])

        def encode(**kwargs):
            # Isolate the real audio mux from frame processing with a valid video-only stage.
            native([
                "ffmpeg", "-nostdin", "-y", "-v", "error", "-threads", "2",
                "-i", str(original), "-map", "0:v:0", "-c:v", "copy", "-an",
                kwargs["save_file"],
            ])

        self._run(encode)
        source_streams = streams(original)
        result_streams = streams(self.video)
        source_audio = [stream for stream in source_streams if stream["codec_type"] == "audio"]
        result_audio = [stream for stream in result_streams if stream["codec_type"] == "audio"]
        self.assertEqual(len(source_audio), 2)
        self.assertEqual(len(result_audio), len(source_audio), "Every original audio track must survive")
        for index, (source, result) in enumerate(zip(source_audio, result_audio)):
            with self.subTest(audio_stream=index):
                fields = ("codec_name", "sample_rate", "time_base", "start_time", "duration")
                self.assertEqual({key: result[key] for key in fields}, {key: source[key] for key in fields})
                self.assertEqual(packets(self.video, f"a:{index}"), packets(original, f"a:{index}"))
                source_pcm = pcm(original, f"0:a:{index}")
                self.assertGreater(len(source_pcm), 0)
                self.assertEqual(pcm(self.video, f"0:a:{index}"), source_pcm)
        source_video = next(stream for stream in source_streams if stream["codec_type"] == "video")
        result_video = next(stream for stream in result_streams if stream["codec_type"] == "video")
        fields = ("width", "height", "avg_frame_rate", "time_base", "start_time", "duration", "nb_frames")
        self.assertEqual({key: result_video[key] for key in fields}, {key: source_video[key] for key in fields})
        self.assertEqual((result_video["width"], result_video["height"], result_video["nb_frames"],
                          result_video["avg_frame_rate"]), (192, 128, "24", "24/1"))
        self.assertEqual(packets(self.video, "v:0"), packets(original, "v:0"))
        native([
            "ffmpeg", "-nostdin", "-v", "error", "-threads", "2", "-i", str(self.video),
            "-map", "0", "-f", "null", "-",
        ])
        self.assertEqual(original.read_bytes(), protected_bytes)
        self.assertEqual(sorted(path.name for path in self.video.parent.iterdir()),
                         ["output.mp4", "protected-source.mp4"])


if __name__ == "__main__":
    unittest.main()
