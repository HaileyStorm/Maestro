"""Exact synthetic CPU source frames, binding, resource bounds and cancellation."""
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services import h3_face_refine as face
from services import h3_face_refine_preview as preview
from services import h3_gallery_av_guide as av


def run(command):
    return subprocess.run(command, check=True, timeout=30, capture_output=True).stdout


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class FacePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.storage = tempfile.TemporaryDirectory(prefix="face-preview-test-")
        cls.root = Path(cls.storage.name)
        cls.source = cls.root / "source.mov"
        run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=96x64:r=24",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
             "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=6",
             "-filter_complex", "[2:a]adelay=125[a2]", "-map", "0:v", "-map", "1:a", "-map", "[a2]",
             "-frames:v", "124", "-c:v", "libx264", "-threads", "1", "-c:a", "pcm_s16le",
             "-metadata:s:a:0", "title=/private/source-not-for-labels", str(cls.source)])
        cls.lossless = cls.root / "source.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(cls.source), "-map", "0",
             "-c:v", "ffv1", "-threads", "1", "-pix_fmt", "bgr0", "-c:a", "copy", str(cls.lossless)])
        cls.pixels = np.frombuffer(run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(cls.source),
             "-map", "0:v:0", "-an", "-threads", "1", "-fps_mode", "passthrough",
             "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]), dtype=np.uint8).reshape(124, 64, 96, 3)

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.storage.cleanup)
        self.directory = Path(self.storage.name)

    def test_measured_facts_and_ordered_audio_do_not_echo_arbitrary_metadata(self):
        value = preview.read_face_source(str(self.source))
        self.assertEqual((value.width, value.height, value.frame_count, value.fps), (96, 64, 124, "24/1"))
        self.assertEqual([item[0] for item in value.audio_streams], [0, 1])
        self.assertTrue(all("48000 Hz" in item[1] for item in value.audio_streams))
        public = value.public_facts()
        self.assertNotIn("sha256", public)
        self.assertNotIn("size", public)
        self.assertNotIn("private/source", str(public))
        self.assertNotIn(str(self.root), str(public))
        self.assertEqual(value.sha256, face._file_digest(self.source))
        self.assertEqual(value.size, self.source.stat().st_size)

    def test_first_interior_and_last_frames_are_lossless_exact_decoded_ordinals(self):
        import numpy as np
        from PIL import Image
        for path in (self.source, self.lossless):
            binding = preview.read_face_source(str(path))
            for index in (0, 53, 123):
                with self.subTest(source=path.suffix, frame=index):
                    measured, png = preview.read_face_frame(str(path), index, expected_source=binding)
                    self.assertEqual(measured, binding)
                    self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                    image = Image.open(io.BytesIO(png))
                    self.assertEqual(image.size, (96, 64))
                    np.testing.assert_array_equal(np.asarray(image), self.pixels[index])

    def test_invalid_indices_and_foreign_bindings_never_fall_back(self):
        binding = preview.read_face_source(str(self.source))
        for index in (-1, 124, True, 1.0, "1", None):
            with self.subTest(frame=index), self.assertRaises(preview.FacePreviewError):
                preview.read_face_frame(str(self.source), index, expected_source=binding)
        with self.assertRaises(preview.FacePreviewError):
            preview.read_face_frame(str(self.lossless), 0, expected_source=binding)
        with self.assertRaises(preview.FacePreviewError):
            preview.read_face_frame(str(self.source), 0, expected_source={"sha256": binding.sha256})

    def test_changes_after_snapshot_are_rejected_before_return_and_snapshot_removed(self):
        path = self.directory / "changed.mov"
        shutil.copyfile(self.source, path)
        original = preview._frame
        snapshots = []
        def change(snapshot, *args):
            snapshots.append(snapshot.parent)
            png = original(snapshot, *args)
            with path.open("ab") as output:
                output.write(b"changed after snapshot")
            return png
        with patch.object(preview, "_frame", change), self.assertRaises(preview.FacePreviewError):
            preview.read_face_frame(str(path), 0)
        self.assertTrue(snapshots)
        self.assertTrue(all(not directory.exists() for directory in snapshots))

    def test_symlink_and_hardlink_sources_and_encoded_size_overflow_are_rejected(self):
        link = self.directory / "link.mov"
        link.symlink_to(self.source)
        with self.assertRaises(preview.FacePreviewError):
            preview.read_face_source(str(link))
        copy = self.directory / "copy.mov"
        shutil.copyfile(self.source, copy)
        hard = self.directory / "hard.mov"
        hard.hardlink_to(copy)
        with self.assertRaises(preview.FacePreviewError):
            preview.read_face_source(str(copy))
        with patch.object(av, "MAX_ENCODED_BYTES", 8), self.assertRaises(preview.FacePreviewError):
            preview.read_face_source(str(self.source))

    def test_wrong_clock_base_length_and_display_rotation_are_rejected(self):
        for name, rate, count in (("rate", 25, 124), ("length", 24, 125)):
            path = self.directory / (name + ".mov")
            run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"testsrc2=s=96x64:r={rate}",
                 "-frames:v", str(count), "-c:v", "libx264", "-threads", "1", str(path)])
            with self.subTest(name=name), self.assertRaises(preview.FacePreviewError):
                preview.read_face_source(str(path))
        path = self.directory / "rotated.mov"
        run(["ffmpeg", "-v", "error", "-nostdin", "-display_rotation:v:0", "90", "-i", str(self.source),
             "-map", "0", "-c", "copy", str(path)])
        with self.assertRaises(preview.FacePreviewError):
            preview.read_face_source(str(path))

    def test_cancellation_reaps_decoder_and_removes_private_snapshot(self):
        original = av._stream
        children, snapshots = [], []
        popen = subprocess.Popen
        def capture(command, *args, **kwargs):
            child = popen(command, *args, **kwargs)
            children.append(child)
            return child
        def stop_during_stream(command, limit, consume, cancel_check):
            snapshots.extend(Path(command[i + 1]).parent for i, item in enumerate(command[:-1]) if item == "-i")
            calls = 0
            def stopped():
                nonlocal calls
                calls += 1
                return calls > 2
            return original(command, limit, consume, stopped)
        with patch.object(av, "_stream", stop_during_stream), patch.object(subprocess, "Popen", capture):
            with self.assertRaises(preview.FacePreviewCancelled):
                preview.read_face_frame(str(self.source), 0)
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertTrue(all(not directory.exists() for directory in snapshots))

    def test_decode_overflow_and_early_eof_fail_without_preview(self):
        real = av._stream
        def truncated(command, limit, consume, cancel_check):
            if "rawvideo" in command:
                consume(bytes(9))
                return 9
            return real(command, limit, consume, cancel_check)
        with patch.object(av, "_stream", truncated), self.assertRaises(preview.FacePreviewError):
            preview.read_face_frame(str(self.source), 0)
        with patch.object(face, "MAX_RGB_BYTES", 8), patch.object(face, "_probe") as decode:
            with self.assertRaises(preview.FacePreviewError):
                preview.read_face_source(str(self.source))
            decode.assert_not_called()

    def test_aggregate_timeout_after_metadata_stops_remaining_work_and_removes_snapshot(self):
        real = preview._audio_streams
        snapshots = []
        def delayed(snapshot, cancel_check):
            result = real(snapshot, cancel_check)
            snapshots.append(snapshot.parent)
            time.sleep(2.1)
            return result
        with patch.object(preview, "PREVIEW_SECONDS", 2), patch.object(preview, "_audio_streams", delayed):
            with self.assertRaisesRegex(preview.FacePreviewError, "timed out"):
                preview.read_face_frame(str(self.source), 0)
        self.assertTrue(snapshots)
        self.assertTrue(all(not directory.exists() for directory in snapshots))

    def test_missing_path_and_broken_cancel_check_errors_are_redacted(self):
        for path, check in ((str(self.directory / "secret.mov"), None),
                            (str(self.source), lambda: (_ for _ in ()).throw(RuntimeError(str(self.root))))):
            with self.assertRaises(preview.FacePreviewError) as result:
                preview.read_face_source(path, cancel_check=check)
            self.assertNotIn(str(self.root), str(result.exception))


if __name__ == "__main__":
    unittest.main()
